"""server/stage.py with --stage-caches (ENG-424): the flags, what stays refused, the key's seed, the
kept state in /health, and the disk tier as a tail stage uses it. No model: the fake engine's stage.

  -  off by default: more than one slot and a disk tier are refused as before; on: both are taken,
     and the options say so; the session spill is turned off on a stage, and said
  -  both stages put the same seed in every key; another cut, code or weights give another
  -  /health carries the kept state's counters only with the caches on
  -  the disk tier: after the tier took a state's sections for a file the engine is asked to keep
     the front's half; a restore the front cannot follow gives the anchor up and answers a miss;
     any other error is the tier's own; another server's tier is not wrapped

Run: python3 tests/test_stage_caches.py
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from engine.methods import stages as S  # noqa: E402
from server import stage  # noqa: E402


def _args(*argv) -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fake-engine", action="store_true")
    ap.add_argument("--model", default=None)
    ap.add_argument("--parallel", type=int, default=1)
    ap.add_argument("--kv-pool", default="off")
    ap.add_argument("--disk-cache", default="off")
    ap.add_argument("--session-spill", default="on")
    ap.add_argument("--conductor", default="raw")
    ap.add_argument("--backends", default="off")
    ap.add_argument("--setup", action="store_true")
    ap.add_argument("--max-len", type=int, default=8192)
    stage.add_flags(ap)
    return ap.parse_args(["--fake-engine", *argv])


def _key(d: str) -> str:
    p = os.path.join(d, "link.key")
    with open(os.open(p, os.O_WRONLY | os.O_CREAT, 0o600), "w") as f:
        f.write("k" * 64 + "\n")
    return p


def _refused(fn, *needles) -> str:
    try:
        fn()
    except SystemExit as e:
        for n in needles:
            assert n in str(e), (n, str(e))
        return str(e)
    raise AssertionError(f"not refused (wanted {needles})")


def _meminfo(gib: float) -> str:
    """A /proc/meminfo with `gib` GiB available: a front's memory check reads this file, not the
    machine's, so a CPU run on a shared box does not depend on what its live engine leaves free
    (ENG-413 WP9)."""
    p = os.path.join(tempfile.mkdtemp(prefix="stage-caches-"), "meminfo")
    with open(p, "w") as f:
        f.write(f"MemTotal:       127535744 kB\nMemAvailable:   {int(gib * (1 << 20))} kB\n")
    return p


def _prepared(*argv, lines=None):
    state: dict = {}
    a = _args(*argv)
    saved, stage.MEMINFO = stage.MEMINFO, _meminfo(64)
    try:
        stage.prepare(a, state, log=(lines.append if lines is not None else (lambda line: None)))
    finally:
        stage.MEMINFO = saved
    return state["stage"], a


def test_caches_and_slots_are_taken_only_with_the_flag():
    d = tempfile.mkdtemp()
    base = ("--stage", "2:4", "--link-peer", "127.0.0.1:9100", "--link-key-file", _key(d))
    a = _args()
    assert (a.stage_caches, a.stage_snapshots_gb, a.stage_cache_dir, a.stage_cache_gb) == ("off", 2.0, "", 32.0)
    st, _ = _prepared(*base)
    assert st.options == {"slots": 1}
    _refused(lambda: _prepared(*base, "--parallel", "2"), "one slot", "--stage-caches on")
    _refused(lambda: _prepared(*base, "--disk-cache", "/tmp/x"), "--disk-cache off", "--stage-caches on")
    lines: list = []
    st, a = _prepared(*base, "--stage-caches", "on", "--parallel", "8", "--disk-cache", "/tmp/x", lines=lines)
    assert st.options == {"slots": 8, "caches": "on"} and a.session_spill == "off"
    assert any("session spill is not served split" in x for x in lines), lines
    # still refused with the caches on
    for extra, why in ((("--kv-pool", "on"), "--kv-pool off"), (("--conductor", "on"), "--conductor raw"),
                       (("--setup",), "setup mode")):
        _refused(lambda extra=extra: _prepared(*base, "--stage-caches", "on", *extra), why)
    return "off: one slot, no disk tier; on: 8 slots and a disk tier, the session spill off and said"


def test_both_stages_put_the_same_seed_in_every_key():
    d = tempfile.mkdtemp()
    key = _key(d)
    front, _ = _prepared("--stage", "0:2", "--link-listen", "127.0.0.1:9100", "--link-key-file", key)
    tail, _ = _prepared("--stage", "2:4", "--link-peer", "127.0.0.1:9100", "--link-key-file", key)
    other, _ = _prepared("--stage", "3:4", "--link-peer", "127.0.0.1:9100", "--link-key-file", key)
    assert front.seed() == tail.seed() and len(front.seed()) == 32 and other.seed() != tail.seed()
    tail._base["weights"] = "other"
    assert tail.seed() != front.seed()
    assert S.root_key(front.seed()) != S.root_key(other.seed()) != S.root_key()
    return "front and tail of one cut: one seed; another cut or other weights: another"


def test_health_carries_the_kept_state_only_with_the_caches_on():
    d = tempfile.mkdtemp()
    base = ("--stage", "2:4", "--link-peer", "127.0.0.1:9100", "--link-key-file", _key(d))
    for on in (False, True):
        st, _ = _prepared(*base, *(("--stage-caches", "on") if on else ()))
        st.coordinator = S.Coordinator(stage.FakeStage(2, 4))
        h = st.health()["stage"]
        assert ("state" in h) == on, h
        if on:
            assert h["state"]["marks"] == 0 and h["state"]["rebuilds"] == 0 and "trees" in h["state"]
    front, _ = _prepared("--stage", "0:2", "--link-listen", "127.0.0.1:9100", "--link-key-file", _key(d),
                         "--stage-caches", "on")
    front.follower = S.Follower(stage.FakeStage(0, 2))
    assert front.health()["stage"]["state"]["snapshots"] == 0 and front.health()["stage"]["state"]["slots"] == {}
    return "the tail's counters and the front's, under stage.state, with --stage-caches on only"


class _Tier:
    """A disk tier's two verbs as engine/cache.py calls them, and what they were asked."""

    def __init__(self, fail=None):
        self.calls, self.failed, self.fail, self.budget = [], [], fail, 3

    def capture(self, job, eng, L):
        self.calls.append(("capture", L))

    def restore(self, eng, drafter, hit):
        self.calls.append(("restore", hit.L))
        if self.fail is not None:
            raise self.fail
        return True

    def _fail(self, why, hit, bad):
        self.failed.append((why, bad))

    def report(self):
        return {"entries": 0}


def test_the_disk_tier_as_a_tail_stage_uses_it():
    kept = []
    eng = types.SimpleNamespace(disk_keep=lambda: kept.append(1))
    job = types.SimpleNamespace(lines={16, 24})
    hit = types.SimpleNamespace(L=24, anchor={"key": (24, 77)})
    t = _Tier()
    d = stage.StageDisk(t)
    d.capture(job, eng, 8)                                   # a line the job writes no anchor at
    d.capture(None, eng, 16)                                 # nothing planned
    assert kept == [] and t.calls == [("capture", 8), ("capture", 16)]
    d.capture(job, eng, 24)
    assert kept == [1]
    d.capture(job, types.SimpleNamespace(), 16)              # an engine with no front to tell
    assert d.restore(eng, None, hit) is True and t.failed == []
    # the front does not hold its half: the anchor is given up, the answer is a miss
    t.fail = S.StageError("snapshot", "snapshot: the front stage does not hold its half of this state")
    assert d.restore(eng, None, hit) is False and t.failed == [("stage", ("anchor", (24, 77)))]
    # any other failure is the tier's own to report (engine/cache.py journals it)
    for exc in (S.StageDown("stage_down: timeout"), ValueError("layout")):
        t.fail = exc
        try:
            d.restore(eng, None, hit)
        except type(exc):
            pass
        else:
            raise AssertionError("swallowed")
    assert len(t.failed) == 1
    # everything else is the tier's own
    assert d.report() == {"entries": 0} and d.budget == 3
    d.budget = 5
    assert t.budget == 5
    # which servers get the wrapped tier
    tmp = tempfile.mkdtemp()
    base = ("--link-key-file", _key(tmp))
    tail_on, _ = _prepared("--stage", "2:4", "--link-peer", "127.0.0.1:9100", *base, "--stage-caches", "on")
    tail_off, _ = _prepared("--stage", "2:4", "--link-peer", "127.0.0.1:9100", *base)
    assert isinstance(stage.disk({"stage": tail_on}, t), stage.StageDisk)
    assert stage.disk({"stage": tail_off}, t) is t and stage.disk({}, t) is t and stage.disk({"stage": tail_on}, None) is None
    return "keep after a captured line only; a restore the front cannot follow: the anchor given up, a miss; other servers: the tier as it is"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
