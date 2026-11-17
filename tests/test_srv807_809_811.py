"""Three small engine fixes of 2026-11-17 (SRV-807, SRV-809, SRV-811).

  1  SRV-807: the first "over the ceiling" line prints at any uptime: its stamp starts far in the
     past, so an event at a monotonic clock reading below OVER_CEILING_LOG_S prints; the 300 s
     period between two lines holds                              test_the_first_over_ceiling_line_prints_at_any_uptime
  2  SRV-809: /v1/dashboard/parallel's engine_slots counts a slot the round runner decodes (phase
     runner) as decoding, and adds the runner's rounds and joint rounds (groups of more than one
     slot) to the barrier's                                        test_the_parallel_brief_counts_the_round_runner
  3  SRV-811: a tree with no .git names its build from .git-sha, else .deployed-commit (a hex
     commit, first line); anything else is not a commit             test_the_build_comes_from_the_deploy_file

    python tests/test_srv807_809_811.py
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import types
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import test_forward_tree as T  # noqa: E402,F401  (the CPU environment before the engine imports)

from server import app  # noqa: E402
from server import round_runner  # noqa: E402

EV = {"kind": "over_ceiling", "over": 2 ** 30, "shed": ["store"], "graphs_reserved": 2 ** 31,
      "graphs_free": 0, "exempt": 0, "default_free": 0, "allocated": 2 ** 32, "reserved": 2 ** 33}


def test_the_first_over_ceiling_line_prints_at_any_uptime():
    assert app._OVER_CEILING_LOGGED[0] <= -app.OVER_CEILING_LOG_S, app._OVER_CEILING_LOGGED
    real, saved = app.time.monotonic, list(app._OVER_CEILING_LOGGED)
    clock = [5.0]
    app.time.monotonic = lambda: clock[0]          # a box up five seconds
    try:
        out = []
        for t in (5.0, 6.0, 5.0 + app.OVER_CEILING_LOG_S):
            clock[0] = t
            buf = io.StringIO()
            with redirect_stdout(buf):
                app._memory_event(dict(EV))
            out.append(buf.getvalue().startswith("[memory] over the ceiling by 1.00 GiB"))
        assert out == [True, False, True], out
    finally:
        app.time.monotonic = real
        app._OVER_CEILING_LOGGED[:] = saved
    return "printed at 5 s of uptime, quiet 1 s later, printed again 300 s later"


class _Slot:
    def __init__(self, i, phase):
        self.index, self.phase, self.max_len, self.busy, self.owner = i, phase, 1024, phase != "idle", f"r{i}"


def test_the_parallel_brief_counts_the_round_runner():
    slots = [_Slot(0, "runner"), _Slot(1, "runner"), _Slot(2, "decode"), _Slot(3, "idle")]
    meet = types.SimpleNamespace(report=lambda: {"rounds": 10, "joint_rounds": 4, "joint_hist": {2: 3, 3: 1},
                                                 "timeouts": 0})
    pool = types.SimpleNamespace(n=4, slots=slots, meeting=meet, admission=None, running=lambda: 3)
    runner = types.SimpleNamespace(stats={"rounds": 100, "joint": 100, "joint_hist": {1: 40, 2: 50, 3: 10}})
    saved_pool, saved_runner = dict.get(app.STATE, "pool"), round_runner.RUNNER
    dict.__setitem__(app.STATE, "pool", pool)
    round_runner.RUNNER = runner
    try:
        b = app._par_brief()
    finally:
        dict.__setitem__(app.STATE, "pool", saved_pool)
        round_runner.RUNNER = saved_runner
    assert b["decoding"] == 3, b                       # two in the runner, one on the barrier path
    assert b["rounds"] == 110 and b["joint_rounds"] == 4 + 60, b
    assert b["joint_hist"] == {2: 53, 3: 11}, b["joint_hist"]
    assert b["runner"] == {"rounds": 100, "joint_rounds": 60}, b["runner"]
    # no runner: the barrier's alone, as before
    dict.__setitem__(app.STATE, "pool", types.SimpleNamespace(n=1, slots=[_Slot(0, "decode")], meeting=meet,
                                                               admission=None, running=lambda: 1))
    round_runner.RUNNER = None
    try:
        b = app._par_brief()
    finally:
        dict.__setitem__(app.STATE, "pool", saved_pool)
        round_runner.RUNNER = saved_runner
    assert b["decoding"] == 1 and b["rounds"] == 10 and b["joint_rounds"] == 4 and b["runner"] is None, b
    return "runner slots decoding, its rounds and joint rounds added"


def test_the_build_comes_from_the_deploy_file():
    d = tempfile.mkdtemp(prefix="srv811-")
    with open(os.path.join(d, "VERSION"), "w") as f:
        f.write("0.15.4\n")
    assert app.identity(d)["git_sha"] == "", "no .git and no file: empty, as before"
    with open(os.path.join(d, ".deployed-commit"), "w") as f:
        f.write("f5ada3b778962d1a041277629ef7055c27110a71\ndeployed 2026-11-17\n")
    assert app.identity(d)["git_sha"] == "f5ada3b77896"
    with open(os.path.join(d, ".git-sha"), "w") as f:
        f.write("2f3ca1edc5d3\n")
    assert app.identity(d)["git_sha"] == "2f3ca1edc5d3", ".git-sha wins"
    with open(os.path.join(d, ".git-sha"), "w") as f:
        f.write("not a commit\x1b[31m\n")
    assert app.identity(d)["git_sha"] == "f5ada3b77896", "a .git-sha that is no commit is skipped"
    return "from .deployed-commit; .git-sha first; a bad file ignored"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
