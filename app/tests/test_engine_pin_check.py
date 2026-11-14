"""tools/engine_pin_check.py and the manual CI job that runs it (OPS-250, QA OPS-251).

The job fetches the engine at the commit contracts/engine-api-v1/PIN names and checks the copy
here against it. Here the engine is a made-up repository in a temp folder that holds this tree's
own copy of both folders, so nothing reaches the real engine.

  1  a PIN made from the engine's commit (its tree ids and versions) passes; --commit prints the
     PIN's commit                                                 test_a_matching_engine_passes
  2  each problem is named: a commit the engine does not hold, a tree id or a version that is not
     the PIN's, a file that differs, a file on one side only; a short commit id and a missing
     folder are refused                                          test_each_difference_is_named
  3  the workflow: by hand only, read-only, stops first without ENGINE_REPO_TOKEN, checks out
     TandemLLM/TandemEngine at the PIN's commit with that token (credentials not kept), then runs
     the tool on that checkout; every action pinned to a full version
                                                                  test_the_workflow_runs_the_check

Standard library and git. Run: python tests/test_engine_pin_check.py
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

from tools import engine_pin_check as T  # noqa: E402

WF = os.path.join(ROOT, ".github", "workflows", "contract-pin.yml")
ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
       "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def _git(repo: str, *args: str) -> str:
    p = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, env=ENV, timeout=60)
    assert p.returncode == 0, (args, p.stderr)
    return p.stdout.strip()


def _engine(d: str) -> str:
    """A made-up engine repository whose contracts/ holds this tree's copy (PIN left out)."""
    repo = os.path.join(d, "engine")
    os.makedirs(os.path.join(repo, "contracts"))
    _git(repo, "init", "-q")
    for folder in T.FOLDERS:
        shutil.copytree(os.path.join(T.CONTRACTS, folder), os.path.join(repo, "contracts", folder),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    os.remove(os.path.join(repo, "contracts", "engine-api-v1", "PIN"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "contracts")
    return repo


def _pin(repo: str, commit: str = "HEAD") -> dict:
    commit = _git(repo, "rev-parse", commit)
    pin = {"engine_commit": commit}
    for folder, (key, vfile) in T.FOLDERS.items():
        pin[folder] = _git(repo, "rev-parse", f"{commit}:contracts/{folder}")
        pin[key] = json.loads(_git(repo, "show", f"{commit}:contracts/{folder}/{vfile}"))["version"]
    return pin


def test_a_matching_engine_passes():
    with tempfile.TemporaryDirectory(prefix="pin-check-") as d:
        repo = _engine(d)
        assert T.check(repo, _pin(repo)) == []
    out = io.StringIO()
    with redirect_stdout(out):
        assert T.main(["--commit"]) == 0
    assert out.getvalue().strip() == T.read_pin()["engine_commit"]
    assert re.fullmatch(r"[0-9a-f]{40}", out.getvalue().strip())
    return "a made-up engine at the copy passes"


def test_each_difference_is_named():
    with tempfile.TemporaryDirectory(prefix="pin-check-") as d:
        repo = _engine(d)
        good = _pin(repo)
        api = os.path.join(repo, "contracts", "engine-api-v1")
        with open(os.path.join(api, "routes.json")) as f:
            routes = json.load(f)
        routes["version"] = "9.9"
        with open(os.path.join(api, "routes.json"), "w") as f:
            json.dump(routes, f)
        with open(os.path.join(api, "only-there.json"), "w") as f:
            f.write("{}\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "edit")
        moved = _pin(repo)
        # the PIN of the first commit pointed at the second: tree and version are not the PIN's
        got = T.check(repo, dict(good, engine_commit=moved["engine_commit"]))
        assert any(p.startswith("engine-api-v1: tree ") for p in got), got
        assert any("routes.json version '9.9'" in p for p in got), got
        assert "engine-api-v1/routes.json: differs from the engine's" in got, got
        assert "engine-api-v1/only-there.json: in the engine, not here" in got, got
        assert not any(p.startswith("engine-ext-v1") for p in got), got
        # a PIN of the second commit: ids and versions agree, the files here still differ
        got = T.check(repo, moved)
        assert sorted(got) == ["engine-api-v1/only-there.json: in the engine, not here",
                               "engine-api-v1/routes.json: differs from the engine's"], got
        assert T.check(repo, dict(good, engine_commit="0" * 40)) == \
            [f"the engine at {repo} does not hold commit {'0' * 40}"]
        assert T.check(repo, dict(good, engine_commit="abc123"))[0].startswith("PIN: engine_commit")
        with redirect_stdout(io.StringIO()):
            assert T.main(["--engine", os.path.join(d, "nowhere")]) == 2
    return "commit, tree, version, file and one-sided files named"


def _yaml(path: str) -> dict:
    import yaml
    with open(path) as f:
        return yaml.load(f, Loader=yaml.BaseLoader)         # `on` stays the string "on"


def test_the_workflow_runs_the_check():
    if not os.path.isfile(WF):
        from scriptmode import Skip
        raise Skip("no .github/workflows here (a vendored copy leaves it out)")
    wf = _yaml(WF)
    assert set(wf["on"]) == {"workflow_dispatch"}, wf["on"]
    assert wf["permissions"] == {"contents": "read"}
    steps = wf["jobs"]["pin"]["steps"]
    first = steps[0]
    assert first["env"]["ENGINE_REPO_TOKEN"] == "${{ secrets.ENGINE_REPO_TOKEN }}"
    assert '[ -z "$ENGINE_REPO_TOKEN" ]' in first["run"] and "exit 1" in first["run"]
    pin = next(s for s in steps if s.get("id") == "pin")
    assert "tools/engine_pin_check.py --commit" in pin["run"] and "$GITHUB_OUTPUT" in pin["run"]
    eng = next(s for s in steps if s.get("with", {}).get("repository") == "TandemLLM/TandemEngine")
    assert eng["uses"].startswith("actions/checkout@")
    w = eng["with"]
    assert w["ref"] == "${{ steps.pin.outputs.commit }}" and w["token"] == "${{ secrets.ENGINE_REPO_TOKEN }}"
    assert w["persist-credentials"] == "false"
    assert steps[-1]["run"].strip() == f"python3 tools/engine_pin_check.py --engine {w['path']}"
    assert steps.index(pin) < steps.index(eng) < len(steps) - 1
    for s in steps:
        if "uses" in s:
            assert re.fullmatch(r"[\w-]+/[\w-]+@v\d+\.\d+\.\d+", s["uses"]), s["uses"]
    with open(WF) as f:
        assert "secrets." not in f.read().replace("secrets.ENGINE_REPO_TOKEN", ""), "one secret only"
    return "manual, read-only, the PIN's commit with the token, then the check"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
