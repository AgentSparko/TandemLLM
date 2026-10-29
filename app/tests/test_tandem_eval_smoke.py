"""SRV-747: tools/tandem_eval.py's client_smoke suite (the protocol set) runs the engine's
tools/client_smoke.py, which TandemApp's tree does not carry; `--smoke-script PATH` names it.

  1  no --smoke-script: the run's record has an error that names the option and the suite, and
     nothing runs
  2  a path that is no file: the error says so, and nothing runs
  3  a relative path is the caller's: from another directory than TandemApp's, the script it
     names runs on each arm (from TandemApp's tree, with the eval key and the raw arm's
     --x-tandem raw), and its results are the run's gates; an absolute path the same
                                                            test_the_smoke_script_is_the_callers

Run: python tests/test_tandem_eval_smoke.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools import tandem_eval as te  # noqa: E402

# a stand-in for the engine's client_smoke.py: it notes how it ran and passes its two checks
STUB = r'''import json, os, sys
a = sys.argv[1:]
with open(os.environ["STUB_SEEN"], "a") as f:
    f.write(json.dumps({"argv": a, "cwd": os.getcwd(), "key": os.environ.get("QSE_API_KEY")}) + "\n")
with open(a[a.index("--json") + 1], "w") as f:
    json.dump({"results": [{"check": "models", "pass": True}, {"check": "stream", "pass": True}]}, f)
'''

SUITE = {"id": "protocol", "title": "the protocol set", "kind": "client_smoke", "timeout_min": 1,
         "args": ["--only", "models,stream"]}


def _night(tmp: str, script: str, seen: str) -> te.Night:
    a = argparse.Namespace(night="2026-10-01", out=os.path.join(tmp, "out"),
                           work=os.path.join(tmp, "work"), smoke_script=script,
                           smoke_python=[sys.executable], base="http://127.0.0.1:9/v1",
                           clear_store=True, profile="overnight", driver="watch")
    env = {"PATH": os.environ.get("PATH", ""), "TANDEM_EVAL_KEY": "k-test", "STUB_SEEN": seen}
    return te.Night(a, [SUITE], None, env, {})


def _item(arm: str, tag: str) -> dict:
    return {"suite": "protocol", "arm": arm, "rep": 1, "tag": tag}


def test_the_smoke_script_is_the_callers():
    here = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        checkout = os.path.join(tmp, "checkout")
        os.makedirs(os.path.join(checkout, "tools"))
        stub = os.path.join(checkout, "tools", "client_smoke.py")
        with open(stub, "w") as f:
            f.write(STUB)
        seen = os.path.join(tmp, "seen.jsonl")
        # 1, 2: no script named, or none there: the record says why, and nothing runs
        rec = _night(tmp, "", seen).run(_item("tandem", "p-none"))
        assert rec["error"] and "--smoke-script" in rec["error"] and "protocol" in rec["error"], rec
        rec = _night(tmp, os.path.join(tmp, "nope.py"), seen).run(_item("tandem", "p-nope"))
        assert rec["error"] and "no such file" in rec["error"], rec
        assert not os.path.exists(seen)
        # 3: a relative path, from the checkout's directory: that script runs, on both arms
        os.chdir(checkout)
        try:
            n = _night(tmp, os.path.join("tools", "client_smoke.py"), seen)
            recs = [n.run(_item(arm, "p-" + arm)) for arm in ("raw", "tandem")]
        finally:
            os.chdir(here)
        for rec in recs:
            assert rec["error"] is None and rec["exit_code"] == 0 and not rec["timed_out"], rec
            assert rec["passed"] and rec["gates"]["total"] == 2 and rec["gates"]["passed"] == 2, rec
            assert [g["id"] for g in rec["gates"]["by_gate"]] == ["models", "stream"], rec
        runs = [json.loads(x) for x in open(seen)]
        assert len(runs) == 2, runs
        assert {os.path.realpath(r["cwd"]) for r in runs} == {os.path.realpath(te.ROOT)}, runs
        assert all(r["key"] == "k-test" for r in runs), runs
        assert all(r["argv"][:4] == ["--base", "http://127.0.0.1:9/v1", "--json",
                                     os.path.join(n.work, "p-" + arm + ".json")]
                   for r, arm in zip(runs, ("raw", "tandem"))), runs
        assert runs[0]["argv"][-4:] == ["--only", "models,stream", "--x-tandem", "raw"], runs
        assert runs[1]["argv"][-2:] == ["--only", "models,stream"], runs
        # an absolute path the same, from any directory
        rec = _night(tmp, stub, seen).run(_item("raw", "p-abs"))
        assert rec["error"] is None and rec["passed"], rec
        assert len(open(seen).read().splitlines()) == 3


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
