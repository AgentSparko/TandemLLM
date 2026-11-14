"""The engine's CI without TandemApp (ENG-413 WP12, OPS-250; QA OPS-251), read from the files.

TandemApp's suite and its page build left with OPS-250 (TandemApp's own .github/workflows); this
repository's CI runs the engine. The workflows are read here; their commands are run by the box
(the headless job's script on an archive without app/) and by the tests below on made-up trees.

  1  tests.yml has the cpu and the headless job only, on the triggers it had; no job builds the
     page or runs app/tests; dashboard-dist.yml is gone   test_the_ci_runs_the_engine_only
  2  the headless job installs what the cpu job installs, copies the checkout without app/
     (git archive, app/ excluded, then checked absent) and runs tools/headless_suite.sh there with
     PY and SUITE_JOBS; logs kept on a failure; every action pinned to a full version
                                                                  test_the_headless_job
  3  no workflow, the labeler or CODEOWNERS names a path into app/ (the headless job's exclusion
     aside)                                                      test_no_ci_file_names_app_paths
  4  tools/headless_tests.py lists the top-level test files whose ledger home is engine (or that
     came after the ledger's base), less NEEDS_APP (each an engine file here, with a reason);
     no pair, split or TandemApp file; its summary passes a clean run and fails a failed file, a
     missing result or none                                      test_the_headless_list_and_summary
  5  tools/headless_suite.sh refuses a tree with app/; in a made-up tree it runs each listed file
     through the tree's script_suite and exits 0, or 1 when one fails   test_the_headless_script

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

from tools import headless_tests as H  # noqa: E402

GH = os.path.join(ROOT, ".github")
#: the vendored folder the headless script refuses
VENDORED = "app"
WF = os.path.join(GH, "workflows")


def _yaml(path: str) -> dict:
    import yaml
    with open(path) as f:
        return yaml.load(f, Loader=yaml.BaseLoader)         # `on` stays the string "on"


def test_the_ci_runs_the_engine_only():
    wf = _yaml(os.path.join(WF, "tests.yml"))
    assert set(wf["jobs"]) == {"cpu", "headless"}, sorted(wf["jobs"])
    assert wf["on"] == {"pull_request": "", "push": {"branches": ["main"]}}, wf["on"]
    assert wf["permissions"] == {"contents": "read"}
    assert not os.path.exists(os.path.join(WF, "dashboard-dist.yml"))
    with open(os.path.join(WF, "tests.yml")) as f:
        text = f.read()
    for gone in (r"\bapp/tests", "dashboard-dist", "download-artifact", "npm "):
        assert not re.search(gone, text), gone
    return "cpu and headless"


def test_the_headless_job():
    wf = _yaml(os.path.join(WF, "tests.yml"))
    cpu, job = wf["jobs"]["cpu"], wf["jobs"]["headless"]
    install = [s for s in cpu["steps"] if "pip install" in s.get("run", "")]
    assert len(install) == 1 and install[0] in job["steps"], "the same packages as the cpu job"
    steps = job["steps"]
    copy = next(s for s in steps if "git archive" in s.get("run", ""))
    assert "git archive HEAD -- . ':(exclude)app' | tar -x -C \"$RUNNER_TEMP/headless\"" in copy["run"]
    assert 'test ! -e "$RUNNER_TEMP/headless/app"' in copy["run"]
    run = next(s for s in steps if "headless_suite.sh" in s.get("run", ""))
    assert run["run"].startswith('bash "$RUNNER_TEMP/headless/tools/headless_suite.sh" ')
    assert set(run["env"]) == {"PY", "SUITE_JOBS"}
    assert steps.index(install[0]) < steps.index(copy) < steps.index(run)
    keep = steps[-1]
    assert keep["if"] == "failure()" and keep["uses"].startswith("actions/upload-artifact@")
    assert keep["with"]["path"] == "results/headless"
    assert run["run"].endswith('"$GITHUB_WORKSPACE/results/headless"')
    for s in steps + cpu["steps"]:
        if "uses" in s:
            assert re.fullmatch(r"[\w-]+/[\w-]+@v\d+\.\d+\.\d+", s["uses"]), s["uses"]
    return f"{len(steps)} steps: install, copy without app/, the suite, the logs"


def test_no_ci_file_names_app_paths():
    files = [os.path.join(WF, n) for n in sorted(os.listdir(WF))]
    files += [os.path.join(GH, "labeler.yml"), os.path.join(GH, "CODEOWNERS"),
              os.path.join(GH, "pull_request_template.md")]
    bad = []
    for p in files:
        with open(p) as f:
            for i, line in enumerate(f, 1):
                left = line.replace("':(exclude)app'", "").replace("headless/app", "") \
                           .replace("without app/", "")
                if re.search(r"(?<![\w/.-])app/|/dashboard/", left):
                    bad.append(f"{os.path.relpath(p, ROOT)}:{i}: {line.strip()}")
    assert not bad, bad
    return f"{len(files)} files"


def test_the_headless_list_and_summary():
    got = H.files()
    with open(os.path.join(ROOT, "tests", "test-ledger.json")) as f:
        led = json.load(f)["engine"]
    assert got and len(got) == len(set(got)) and got == sorted(got)
    for rel in got:
        assert os.path.isfile(os.path.join(ROOT, rel)), rel
        assert led.get(rel, {"home": "engine"})["home"] == "engine", rel
    for rel, why in H.NEEDS_APP.items():
        assert led[rel]["home"] == "engine" and os.path.isfile(os.path.join(ROOT, rel)) and why, rel
        assert rel not in got
    pair = [r for r, e in led.items() if e["home"] != "engine"]
    assert not set(pair) & set(got)
    with tempfile.TemporaryDirectory(prefix="headless-sum-") as d:
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
    return f"{len(got)} files, {len(H.NEEDS_APP)} wait for WP14"


#: a stand-in for tests/script_suite.py: one total line a file; a file whose text says FAIL fails
FAKE_SUITE = r'''
import sys
f = sys.argv[-1]
bad = "FAIL" in open(f).read()
print(f"files=1 ok={0 if bad else 1} fail={1 if bad else 0} skipped=0 tests=1" + (f" -- {f}" if bad else ""))
'''


def _tree(d: str, fail: bool) -> str:
    t = os.path.join(d, "tree")
    os.makedirs(os.path.join(t, "tools"))
    os.makedirs(os.path.join(t, "tests"))
    for n in ("headless_suite.sh", "headless_tests.py"):
        shutil.copy(os.path.join(ROOT, "tools", n), os.path.join(t, "tools", n))
    with open(os.path.join(t, "tests", "script_suite.py"), "w") as f:
        f.write(FAKE_SUITE)
    led = {"engine": {"tests/test_a.py": {"home": "engine", "ids": []},
                      "tests/test_p.py": {"home": "pair", "ids": []}}}
    with open(os.path.join(t, "tests", "test-ledger.json"), "w") as f:
        json.dump(led, f)
    for n, body in (("test_a.py", "ok"), ("test_b.py", "FAIL" if fail else "ok"), ("test_p.py", "FAIL")):
        with open(os.path.join(t, "tests", n), "w") as f:
            f.write(body + "\n")
    return t


def _suite(t: str, out: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update(PY=sys.executable, SUITE_JOBS="2")
    return subprocess.run(["bash", os.path.join(t, "tools", "headless_suite.sh"), out],
                          capture_output=True, text=True, timeout=120, env=env, cwd=os.path.dirname(t))


def test_the_headless_script():
    with tempfile.TemporaryDirectory(prefix="headless-sh-") as d:
        t = _tree(d, fail=False)
        os.makedirs(os.path.join(t, VENDORED))
        p = _suite(t, os.path.join(d, "out0"))
        assert p.returncode == 2 and "app/ is here" in p.stderr, (p.returncode, p.stderr)
        os.rmdir(os.path.join(t, VENDORED))
        p = _suite(t, "out1")                        # relative to the caller's folder
        assert p.returncode == 0, (p.stdout, p.stderr)
        out = os.path.join(d, "out1")
        with open(os.path.join(out, "FILES")) as f:
            assert f.read().split() == ["tests/test_a.py", "tests/test_b.py"]
        with open(os.path.join(out, "RESULT")) as f:
            assert len(f.read().splitlines()) == 2
        assert p.stdout.splitlines()[-1] == "files=2 ok=2 fail=0 skipped=0 tests=2", p.stdout
    with tempfile.TemporaryDirectory(prefix="headless-sh-") as d:
        t = _tree(d, fail=True)
        p = _suite(t, os.path.join(d, "out"))
        assert p.returncode == 1 and "::error file=tests/test_b.py::" in p.stdout, (p.stdout, p.stderr)
    return "refuses app/; runs the list; fails on a failed file"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
