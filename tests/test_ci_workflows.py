"""The engine's CI without TandemApp (ENG-413 WP12, WP14; OPS-250; QA OPS-251), read from the files.

TandemApp's suite and its page build left with OPS-250 (TandemApp's own .github/workflows); this
repository's CI runs the engine, in its checkout, with no TandemApp anywhere. The workflows are read
here; their commands are run by the box and by the tests below on made-up trees.

  1  tests.yml has the cpu job only, on the triggers it had; no job builds the page, checks out
     TandemApp or runs its tests; dashboard-dist.yml is gone       test_the_ci_runs_the_engine_only
  2  the cpu job installs the engine's packages and runs tools/engine_suite.sh with PY and
     SUITE_JOBS; logs kept on a failure; every action pinned to a full version
                                                                  test_the_cpu_job
  3  no workflow, the labeler or CODEOWNERS names a path into app/   test_no_ci_file_names_app_paths
  4  tools/engine_tests.py lists every top-level test file but cpu-suite-skip.txt's and the pair
     suite's (tests/test-ledger.json); --pair lists the pair suite's top-level files; its summary
     passes a clean run and fails a failed file, a missing result or none
                                                                  test_the_engine_list_and_summary
  5  tools/engine_suite.sh runs each listed file through the tree's script_suite with no
     TANDEMAPP_SRC and exits 0, or 1 when one fails; tools/pair_suite.sh refuses without a TandemApp
     checkout or with one pinned to other contracts, and runs the pair list with one that is
                                                                  test_the_suite_scripts

Standard library and PyYAML. Run: python tests/test_ci_workflows.py
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from tools import engine_tests as H  # noqa: E402

GH = os.path.join(ROOT, ".github")
WF = os.path.join(GH, "workflows")


def _yaml(path: str) -> dict:
    import yaml
    with open(path) as f:
        return yaml.load(f, Loader=yaml.BaseLoader)         # `on` stays the string "on"


def test_the_ci_runs_the_engine_only():
    wf = _yaml(os.path.join(WF, "tests.yml"))
    assert set(wf["jobs"]) == {"cpu"}, sorted(wf["jobs"])
    assert wf["on"] == {"pull_request": "", "push": {"branches": ["main"]}}, wf["on"]
    assert wf["permissions"] == {"contents": "read"}
    assert not os.path.exists(os.path.join(WF, "dashboard-dist.yml"))
    with open(os.path.join(WF, "tests.yml")) as f:
        text = f.read()
    for gone in (r"\bapp/tests", "dashboard-dist", "download-artifact", "npm ", "TandemApp.git",
                 "TANDEMAPP_SRC", "headless"):
        assert not re.search(gone, text), gone
    return "cpu only"


def test_the_cpu_job():
    wf = _yaml(os.path.join(WF, "tests.yml"))
    steps = wf["jobs"]["cpu"]["steps"]
    install = [s for s in steps if "pip install" in s.get("run", "")]
    assert len(install) == 1 and "torch==" in install[0]["run"]
    run = next(s for s in steps if "engine_suite.sh" in s.get("run", ""))
    assert run["run"] == "bash tools/engine_suite.sh results/suite", run["run"]
    assert set(run["env"]) == {"PY", "SUITE_JOBS"}
    assert steps.index(install[0]) < steps.index(run)
    keep = steps[-1]
    assert keep["if"] == "failure()" and keep["uses"].startswith("actions/upload-artifact@")
    assert keep["with"]["path"] == "results/suite"
    for s in steps:
        if "uses" in s:
            assert re.fullmatch(r"[\w-]+/[\w-]+@v\d+\.\d+\.\d+", s["uses"]), s["uses"]
    return f"{len(steps)} steps: install, the engine suite, the logs"


def test_no_ci_file_names_app_paths():
    files = [os.path.join(WF, n) for n in sorted(os.listdir(WF))]
    files += [os.path.join(GH, "labeler.yml"), os.path.join(GH, "CODEOWNERS"),
              os.path.join(GH, "pull_request_template.md")]
    bad = []
    for p in files:
        with open(p) as f:
            for i, line in enumerate(f, 1):
                if re.search(r"(?<![\w/.-])app/|/dashboard/", line):
                    bad.append(f"{os.path.relpath(p, ROOT)}:{i}: {line.strip()}")
    assert not bad, bad
    return f"{len(files)} files"


def test_the_engine_list_and_summary():
    got, pair = H.files(), H.pair()
    with open(os.path.join(ROOT, "tests", "test-ledger.json")) as f:
        listed = set(json.load(f)["pair_suite"]["files"])
    top = sorted(f"tests/{n}" for n in os.listdir(os.path.join(ROOT, "tests"))
                 if re.fullmatch(r"test_[^/]+\.py", n))
    assert got and len(got) == len(set(got)) and got == sorted(got)
    assert pair == [r for r in top if r in listed] and pair
    assert not set(pair) & set(got) and sorted(set(got) | set(pair)) == top
    assert "tests/test_app_hook.py" in got and "tests/test_pair_copies.py" in pair
    with tempfile.TemporaryDirectory(prefix="engine-sum-") as d:
        res = os.path.join(d, "RESULT")

        def run(lines, want):
            with open(res, "w") as f:
                f.write("".join(x + "\n" for x in lines))
            out = io.StringIO()
            with redirect_stdout(out):
                rc = H.summary(res, want)
            return rc, out.getvalue()

        ok = "files=1 ok=1 fail=0 skipped=0 tests=3"
        rc, out = run([ok, "files=1 ok=0 fail=0 skipped=1 tests=0 -- skipped: tests/test_k.py"], ["a", "b"])
        assert rc == 0 and out.splitlines()[0] == "files=2 ok=1 fail=0 skipped=1 tests=3", out
        assert "skipped tests/test_k.py" in out
        rc, out = run([ok, "files=1 ok=0 fail=1 skipped=0 tests=2 -- tests/test_b.py"], ["a", "b"])
        assert rc == 1 and "::error file=tests/test_b.py::" in out, out
        rc, out = run([ok], ["a", "b"])
        assert rc == 1 and "2 files listed, 1 results" in out, out
        rc, out = run([], [])
        assert rc == 1
    return f"{len(got)} engine files, {len(pair)} pair files"


#: a stand-in for tests/script_suite.py: one total line a file; a file whose text says FAIL fails
FAKE_SUITE = r'''
import os, sys
f = sys.argv[-1]
bad = "FAIL" in open(f).read() or ("NEEDS_APP" in open(f).read() and not os.environ.get("TANDEMAPP_SRC"))
if "NO_APP" in open(f).read() and os.environ.get("TANDEMAPP_SRC"):
    bad = True
print(f"files=1 ok={0 if bad else 1} fail={1 if bad else 0} skipped=0 tests=1" + (f" -- {f}" if bad else ""))
'''


def _tree(d: str, fail: bool) -> str:
    t = os.path.join(d, "tree")
    for sub in ("tools", "tests", "contracts/engine-api-v1", "contracts/engine-ext-v1"):
        os.makedirs(os.path.join(t, sub))
    for n in ("engine_suite.sh", "engine_tests.py", "pair_suite.sh"):
        shutil.copy(os.path.join(ROOT, "tools", n), os.path.join(t, "tools", n))
    with open(os.path.join(t, "tests", "script_suite.py"), "w") as f:
        f.write(FAKE_SUITE)
    with open(os.path.join(t, "tests", "test-ledger.json"), "w") as f:
        json.dump({"pair_suite": {"files": ["tests/test_p.py"]}}, f)
    for n, body in (("test_a.py", "NO_APP"), ("test_b.py", "FAIL" if fail else "ok"), ("test_p.py", "NEEDS_APP")):
        with open(os.path.join(t, "tests", n), "w") as f:
            f.write(body + "\n")
    for folder in ("engine-api-v1", "engine-ext-v1"):
        with open(os.path.join(t, "contracts", folder, "routes.json"), "w") as f:
            f.write("{}\n")
    return t


def _app(d: str, t: str, same: bool = True) -> str:
    """A made-up TandemApp checkout whose contract copy is the tree's (or not), with its PIN."""
    a = os.path.join(d, "app-checkout")
    os.makedirs(os.path.join(a, "tandemapp"))
    open(os.path.join(a, "tandemapp", "__init__.py"), "w").close()
    shutil.copytree(os.path.join(t, "contracts"), os.path.join(a, "contracts"))
    with open(os.path.join(a, "contracts", "engine-api-v1", "PIN"), "w") as f:
        f.write("engine_commit 0\n")
    if not same:
        with open(os.path.join(a, "contracts", "engine-ext-v1", "routes.json"), "w") as f:
            f.write("{\"x\": 1}\n")
    return a


def _run(script: str, t: str, out: str, **env_extra) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "TANDEMAPP_SRC")}
    env.update(PY=sys.executable, SUITE_JOBS="2", **env_extra)
    return subprocess.run(["bash", os.path.join(t, "tools", script), out],
                          capture_output=True, text=True, timeout=120, env=env, cwd=os.path.dirname(t))


def test_the_suite_scripts():
    with tempfile.TemporaryDirectory(prefix="engine-sh-") as d:
        t = _tree(d, fail=False)
        a = _app(d, t)
        # the engine's suite: no TandemApp, even when the caller has one set
        p = _run("engine_suite.sh", t, "out1", TANDEMAPP_SRC=a)       # relative to the caller's folder
        assert p.returncode == 0, (p.stdout, p.stderr)
        out = os.path.join(d, "out1")
        with open(os.path.join(out, "FILES")) as f:
            assert f.read().split() == ["tests/test_a.py", "tests/test_b.py"]
        assert p.stdout.splitlines()[-1] == "files=2 ok=2 fail=0 skipped=0 tests=2", p.stdout
        # the pair suite: refused without TandemApp, run with it
        p = _run("pair_suite.sh", t, os.path.join(d, "outp0"))
        assert p.returncode == 2 and "TANDEMAPP_SRC must name" in p.stderr, (p.returncode, p.stderr)
        p = _run("pair_suite.sh", t, os.path.join(d, "outp1"), TANDEMAPP_SRC=a)
        assert p.returncode == 0, (p.stdout, p.stderr)
        assert "is pinned to this engine's contracts" in p.stdout
        with open(os.path.join(d, "outp1", "FILES")) as f:
            assert f.read().split() == ["tests/test_p.py"]
    with tempfile.TemporaryDirectory(prefix="engine-sh-") as d:
        t = _tree(d, fail=True)
        p = _run("engine_suite.sh", t, os.path.join(d, "out"))
        assert p.returncode == 1 and "::error file=tests/test_b.py::" in p.stdout, (p.stdout, p.stderr)
        a = _app(d, t, same=False)
        p = _run("pair_suite.sh", t, os.path.join(d, "outp"), TANDEMAPP_SRC=a)
        assert p.returncode == 2 and "engine-ext-v1/routes.json: differs" in p.stderr, p.stderr
    return "the engine suite with no TandemApp; the pair suite only with TandemApp at this contract"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
