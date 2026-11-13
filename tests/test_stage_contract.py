"""What the app reads of a stage and what both stages must agree on (SRV-817, QA SRV-818): server/stage.py and
engine/methods/link.py on the fake engine's stage, no model, no GPU.

The test matrix:

  1  `/health`'s stage block has every field of its documented shape on both stages, before a link and
     with one: `shape`, `role`, `lo`, `hi`, `range`, `layers`, `cut`, `identity`, `options`, `link`
     (`state`, `epoch`, `reason`, `transport`, `bytes`, `peer`); each side's `link.peer.identity` is the
     other's `identity`; a server that is not a stage adds nothing to `/health`; both stages' answers
     validate against the engine API contract's `health.schema.json` (1.2)
                                                         test_the_health_stage_block_has_its_shape
  2  the identity block names `slots` and `caches` only when they are not the defaults, so a default
     stage's block is the one it was; the link compares them: another caches setting refuses it with
     `caches`, other slots with `slots`, absent fields are the defaults, both sides give the same reason
                                                         test_the_handshake_compares_slots_and_caches
  3  two stages that differ in caches or slots: the tail refuses to start with the reason, the front's
     `/health` names it in `stage.link.reason` and its log says it; equal ones link up
                                                         test_a_mismatch_is_refused_at_the_link
  4  the front's start check: MemAvailable under its kept states and the floor refuses with the figures;
     a cache folder whose filesystem cannot hold `--stage-cache-gb` refuses; enough of both passes and
     says what it checked; a tail and a front without caches are not checked
                                                         test_the_front_checks_the_box_at_start
  5  a front with a disk tier is refused (the tier is the tail's), with the caches on too
                                                         test_a_front_with_a_disk_tier_is_refused

Run: python tests/test_stage_contract.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from engine.methods import link as L  # noqa: E402
from server import stage  # noqa: E402
from test_stage_server import KEY, _args, _free_port, _http, _keyfile, _refused, _tmp  # noqa: E402

SHAPE = {"shape", "role", "lo", "hi", "range", "layers", "cut", "identity", "options", "link"}
#: the engine API contract (contracts/engine-api-v1): the stage block is part of its /health schema
CONTRACT = os.path.join(ROOT, "contracts", "engine-api-v1")


def _contract_health(doc: dict) -> list:
    from tools import contract_check
    return contract_check.validate(doc, contract_check.load("health", CONTRACT), root=CONTRACT)
LINK = {"state", "epoch", "reason", "transport", "bytes", "peer"}


def _meminfo(gib: float) -> str:
    p = os.path.join(_tmp(), "meminfo")
    with open(p, "w") as f:
        f.write(f"MemTotal:       127535744 kB\nMemAvailable:   {int(gib * (1 << 20))} kB\n")
    return p


class _Mem:
    """stage.MEMINFO at a test's file for a block."""

    def __init__(self, gib: float):
        self.path = _meminfo(gib)

    def __enter__(self):
        self.saved, stage.MEMINFO = stage.MEMINFO, self.path

    def __exit__(self, *exc):
        stage.MEMINFO = self.saved


def _front(key: str, *more, k: int = 2):
    """A front stage serving in this process: (its Stage, its HTTP port, its link port, its log lines)."""
    link_port, port = _free_port(), _free_port()
    a = _args("--stage", f"0:{k}", "--link-listen", f"127.0.0.1:{link_port}", "--link-key-file", key,
              "--port", str(port), *more)
    state: dict = {}
    lines: list = []
    with _Mem(500):
        stage.prepare(a, state, log=lines.append)
    st = state["stage"]
    st.log = lines.append
    threading.Thread(target=stage.serve_front, args=(a, state, lines.append), daemon=True).start()
    end = time.monotonic() + 10
    while time.monotonic() < end:
        try:
            if _http(port, "/health")[0] == 200:
                break
        except OSError:
            time.sleep(0.05)
    return st, port, link_port, lines


def _tail(key: str, to: int, *more, k: int = 2):
    state: dict = {}
    a = _args("--stage", f"{k}:4", "--link-peer", f"127.0.0.1:{to}", "--link-key-file", key, *more)
    stage.prepare(a, state, log=lambda line: None)
    st = state["stage"]
    st.log = lambda line: None
    return state, st


# ------------------------------------------------------------------ 1
def test_the_health_stage_block_has_its_shape():
    key = _keyfile(_tmp())
    front, port, link_port, _ = _front(key)
    code, _, body = _http(port, "/health")
    f = body["stage"]
    assert code == 200 and SHAPE <= set(f) and LINK <= set(f["link"]), f
    assert (f["shape"], f["role"], f["range"], f["cut"], f["layers"]) == (1, "front", "0:2", 2, 4), f
    assert f["link"]["state"] == "down" and f["link"]["peer"] is None and f["link"]["reason"] == "no peer yet", f
    assert f["link"]["transport"] is None and f["link"]["bytes"] is None and len(f["identity"]) == 16, f
    assert not _contract_health(body), _contract_health(body)
    state, tail = _tail(key, link_port)
    tick = tail.fake_tail()
    tick([5, 6, 7])
    t = stage.health(state, "ok")["stage"]
    assert SHAPE <= set(t) and LINK <= set(t["link"]), t
    assert (t["shape"], t["role"], t["range"], t["cut"]) == (1, "tail", "2:4", 2) and t["link"]["reason"] is None, t
    _, _, body = _http(port, "/health")
    f = body["stage"]
    assert f["link"]["state"] == "up" and f["link"]["reason"] is None and f["link"]["transport"] == "tcp", f
    assert t["link"]["peer"] == {"role": "front", "lo": 0, "hi": 2, "identity": f["identity"]}, (t, f)
    assert f["link"]["peer"] == {"role": "tail", "lo": 2, "hi": 4, "identity": t["identity"]}, (t, f)
    assert t["identity"] == stage.identity_digest(tail._ident) != f["identity"]
    full = {"status": "ok", **stage.health(state, "ok")}
    assert not _contract_health(body) and not _contract_health(full), (_contract_health(body), _contract_health(full))
    # a server that is not a stage: /health gains no key
    assert stage.health({}, "ok") == {} and stage.health({}, "draining") == {}
    assert KEY not in json.dumps(t) + json.dumps(f)
    return "both stages: every field, the peer's identity the other's own; no stage, no block"


# ------------------------------------------------------------------ 2
def _ident(role, lo, hi, **st):
    return {"identity": 1, "code": "c", "weights": "w", "family": "f", "layers": 4,
            "stage": {"role": role, "lo": lo, "hi": hi, **st}}


def test_the_handshake_compares_slots_and_caches():
    key = _keyfile(_tmp())
    base = ("--link-key-file", key)
    plain = stage.prepare
    st: dict = {}
    plain(_args("--stage", "2:4", "--link-peer", "127.0.0.1:9100", *base), st, log=lambda line: None)
    assert st["stage"].identity(stage.FakeStage(2, 4))["stage"] == {"role": "tail", "lo": 2, "hi": 4}
    st = {}
    plain(_args("--stage", "2:4", "--link-peer", "127.0.0.1:9100", "--stage-caches", "on", "--parallel", "3",
                *base), st, log=lambda line: None)
    assert st["stage"].identity(stage.FakeStage(2, 4))["stage"] == {"role": "tail", "lo": 2, "hi": 4, "slots": 3,
                                                                    "caches": "on"}
    m = L.mismatch
    front, tail = _ident("front", 0, 2), _ident("tail", 2, 4)
    assert m(front, tail) is None and m(_ident("front", 0, 2, slots=1, caches="off"), tail) is None
    for theirs, reason in ((_ident("front", 0, 2, caches="on"), "caches"),
                           (_ident("front", 0, 2, caches="on", slots=2), "caches"),
                           (_ident("front", 0, 2, slots=2), "slots"),
                           (_ident("front", 0, 2, slots="x"), "stage")):
        assert m(theirs, tail) == m(tail, theirs) == reason, (theirs, reason)
    assert m(_ident("front", 0, 2, caches="on", slots=4), _ident("tail", 2, 4, caches="on", slots=4)) is None
    # over a real handshake: both ends refuse with the same reason
    lis = L.Listener("127.0.0.1:0")
    got = {}

    def accept():
        try:
            got["front"] = L.handshake(lis.accept(5), b"k" * 40,
                                       _ident("front", 0, 2, caches="on", slots=2), dialer=False)
        except L.LinkRefused as e:
            got["front"] = e.reason

    th = threading.Thread(target=accept)
    th.start()
    try:
        L.handshake(L.dial(f"127.0.0.1:{lis.port}"), b"k" * 40, _ident("tail", 2, 4, caches="on", slots=3), dialer=True)
        got["tail"] = None
    except L.LinkRefused as e:
        got["tail"] = e.reason
    th.join(10)
    lis.close()
    assert got == {"front": "slots", "tail": "slots"}, got
    return "default blocks unchanged; caches and slots compared, the same reason on both ends"


# ------------------------------------------------------------------ 3
def test_a_mismatch_is_refused_at_the_link():
    key = _keyfile(_tmp())
    saved, stage.ATTACH_S = stage.ATTACH_S, 3.0
    try:
        for front_more, tail_more, reason in (
                (("--stage-caches", "on", "--parallel", "2"), (), "caches"),
                (("--stage-caches", "on", "--parallel", "2"), ("--stage-caches", "on", "--parallel", "3"), "slots")):
            front, port, link_port, lines = _front(key, *front_more)
            _, tail = _tail(key, link_port, *tail_more)
            _refused(lambda: tail.fake_tail(), f"refused the link ({reason})")
            end = time.monotonic() + 5
            while time.monotonic() < end:
                f = _http(port, "/health")[2]["stage"]
                if f["link"]["reason"] == f"refused ({reason})":
                    break
                time.sleep(0.05)
            assert f["link"]["state"] == "down" and f["link"]["reason"] == f"refused ({reason})", f
            assert any(f"link: refused ({reason})" in x for x in lines), lines
        front, port, link_port, _ = _front(key, "--stage-caches", "on", "--parallel", "2")
        _, tail = _tail(key, link_port, "--stage-caches", "on", "--parallel", "2")
        tail.fake_tail()([1, 2])
        f = _http(port, "/health")[2]["stage"]
        assert f["link"]["state"] == "up" and f["link"]["reason"] is None, f
    finally:
        stage.ATTACH_S = saved
    return "caches and slots: refused, the front's /health and log name it; equal ones link up"


# ------------------------------------------------------------------ 4
def test_the_front_checks_the_box_at_start():
    key = _keyfile(_tmp())
    ok = ("--stage", "0:2", "--link-listen", "127.0.0.1:9100", "--link-key-file", key)
    on = ok + ("--stage-caches", "on", "--parallel", "4", "--stage-snapshots-gb", "3")
    with _Mem(20.0):                      # 4 x 3 GiB of kept states and the 15 GiB floor: 27 GiB
        text = _refused(lambda: stage.prepare(_args(*on), {}, log=lambda line: None),
                        "needs 27.00 GiB of MemAvailable", "--stage-snapshots-gb 3 x 4 slot(s) = 12.00 GiB",
                        "floor 15 GiB", "there are 20.00 GiB")
        assert text.startswith("[stage] REFUSING: the front needs"), text
        # a tail, and a front without caches, are not checked
        st: dict = {}
        stage.prepare(_args("--stage", "2:4", "--link-peer", "127.0.0.1:9100", "--link-key-file", key,
                            "--stage-caches", "on", "--parallel", "4", "--stage-snapshots-gb", "3"), st,
                      log=lambda line: None)
        st = {}
        stage.prepare(_args(*ok), st, log=lambda line: None)
        assert st["stage"].role == "front"
    with _Mem(40.0):
        lines: list = []
        st = {}
        stage.prepare(_args(*on), st, log=lines.append)
        assert st["stage"].role == "front"
        assert any("front check:" in x and "kept states 12.00 GiB" in x and "MemAvailable 40.00 GiB" in x
                   for x in lines), lines
        d = os.path.join(_tmp(), "front-cache")
        _refused(lambda: stage.prepare(_args(*on, "--stage-cache-dir", d, "--stage-cache-gb", "1000000"), {},
                                       log=lambda line: None),
                 f"--stage-cache-dir {d}: --stage-cache-gb 1e+06 asked")
        lines = []
        stage.prepare(_args(*on, "--stage-cache-dir", d, "--stage-cache-gb", "0.001"), {}, log=lines.append)
        assert any("its folder holds 0.001 GiB" in x for x in lines), lines
    saved, stage.MEMINFO = stage.MEMINFO, os.path.join(_tmp(), "no-meminfo")
    try:
        lines = []
        stage.prepare(_args(*on), {}, log=lines.append)
        assert any("no MemAvailable on this box" in x for x in lines), lines
    finally:
        stage.MEMINFO = saved
    return "memory short: refused with its figures; a folder too small: refused; enough: passes and says so"


# ------------------------------------------------------------------ 5
def test_a_front_with_a_disk_tier_is_refused():
    key = _keyfile(_tmp())
    ok = ("--stage", "0:2", "--link-listen", "127.0.0.1:9100", "--link-key-file", key)
    with _Mem(500):
        _refused(lambda: stage.prepare(_args(*ok, "--stage-caches", "on", "--disk-cache", "/tmp/x"), {},
                                       log=lambda line: None),
                 "the disk tier is the tail's", "--disk-cache off on the front")
        _refused(lambda: stage.prepare(_args(*ok, "--disk-cache", "/tmp/x"), {}, log=lambda line: None),
                 "--disk-cache off")
        st: dict = {}
        stage.prepare(_args("--stage", "2:4", "--link-peer", "127.0.0.1:9100", "--link-key-file", key,
                            "--stage-caches", "on", "--disk-cache", "/tmp/x"), st, log=lambda line: None)
        assert st["stage"].role == "tail"
    return "a front with --disk-cache: refused with the caches on and off; the tail takes it"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
