"""The estimate's `split` block (SRV-817, QA SRV-818): server/setup/split.py and each family's `split_facts`,
on a CPU, from small packs with the real packs' structure (tests/fn_host.py, tests/test_lfm2_moe_pack.py). No
weight is loaded by the code under test.

The test matrix:

  1  a family's facts are its stage loader's: at every cut, the front's and the tail's weights from
     `split_facts` equal `check_pack(path, cut)`'s bytes (Flash-Next and LFM2 MoE)
                                                         test_the_facts_are_the_stage_loaders
  2  POST /v1/setup/estimate without `split` answers the body it answered before, byte for byte (with
     `"split": null` too); with it, the same body and a `split` block
                                                         test_a_body_without_the_ask_is_the_old_body
  3  the block for Flash-Next: every legal cut, each side's weights, KV, sections, the draft layer's
     cache on the tail, the n-gram table on the side that holds its layer (warm: its file, cold: its row
     cache), the front's kept states with caches on, the carry a row, the disk of each side, a side
     slot's cost; which parts are computed from what; `best_cut` the largest smaller headroom
                                                         test_the_flashnext_block
  4  LFM2 MoE: no table, no draft layer, one slot; caches asked of it answer `caches`
                                                         test_the_lfm2_block
  5  not splittable, never an error: a family with no stage module, a pack that is not on the box, a
     cut list with no legal cut, more slots than the family serves split
                                                         test_not_splittable_answers
  6  the ask is checked: an unknown field, caches other than on/off, a side window over the window, a
     cut that is not a number: 422 invalid_split          test_the_ask_is_checked
  -  every block of 3, 4 and 5 and every ask of 6 the code takes validate against the engine API
     contract's setup/split.schema.json (1.2); the asks it refuses do not

Run: python tests/test_split_estimate.py
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from server.setup import estimate as estimate_mod  # noqa: E402
from server.setup import registry  # noqa: E402
from server.setup import split as split_mod  # noqa: E402

GIB = float(1 << 30)
#: the engine API contract (contracts/engine-api-v1): the split ask and block (1.2)
CONTRACT = os.path.join(ROOT, "contracts", "engine-api-v1")
FN = "qwen3.8-flash-next"


def _contract(doc, ref: str = "setup/split.schema.json") -> list:
    from tools import contract_check
    return contract_check.validate(doc, {"$ref": ref}, root=CONTRACT)


def _valid(b: dict) -> dict:
    assert not _contract(b), _contract(b)[:5]
    return b
LFM = "lfm2.5-24b-a2b"


def _fn_pack() -> str:
    import fn_host
    return fn_host.host_pack()


_LFM: list = []


def _lfm_pack() -> str:
    if not _LFM:
        import test_lfm2_moe_pack as P
        d = tempfile.mkdtemp(prefix="split-lfm-")
        P._exl3_pack(d)
        _LFM.append(d)
    return _LFM[0]


def _model(mid: str) -> dict:
    from server.setup import device as device_mod
    return device_mod.windowed(registry.get(mid))


def _sel(mid: str, **over) -> dict:
    m = _model(mid)
    return m, estimate_mod.check(m, {"model": mid, **over})


def _refused(fn, *needles):
    try:
        fn()
    except estimate_mod.SelectionError as e:
        assert e.status == 422 and e.code == "invalid_split", (e.status, e.code)
        for n in needles:
            assert n in str(e), (n, str(e))
        return str(e)
    raise AssertionError(f"not refused (wanted {needles})")


# ------------------------------------------------------------------ 1
def test_the_facts_are_the_stage_loaders():
    from engine.models.flashnext import stages as FS
    from engine.models.lfm2_moe import stages as LS
    n = 0
    for mod, path in ((FS, _fn_pack()), (LS, _lfm_pack())):
        f = mod.split_facts(path)
        assert f["cuts"] == list(range(1, f["layers"])) and len(f["layer_bytes"]) == f["layers"]
        for k in f["cuts"]:
            want = mod.check_pack(path, k)["stages"]
            front = f["shared_bytes"] + sum(f["layer_bytes"][:k]) + f["front_extra_bytes"][k]
            tail = f["shared_bytes"] + f["tail_only_bytes"] + sum(f["layer_bytes"][k:])
            assert (front, tail) == (want["front"]["bytes"], want["tail"]["bytes"]), (mod.__name__, k, front, tail, want)
            if "carry_bytes_per_row" in mod.check_pack(path, k):
                assert f["carry_bytes_per_row"][k] == mod.check_pack(path, k)["carry_bytes_per_row"]
            n += 1
    return f"{n} cuts of two families: the facts' weights are check_pack's"


# ------------------------------------------------------------------ 2
def _post(body: dict, mem: dict) -> tuple[int, bytes]:
    from server import app
    from tandemapp import auth
    from server.setup import device as device_mod
    import time
    app.STATE.clear()
    app.STATE.update(auth=auth.Auth(None, None), started=time.time() - 60, version="t")
    device_mod.meminfo = lambda: dict(mem)
    raw = json.dumps(body).encode()

    class Req(app.Handler):
        def __init__(self):
            self.rfile, self.wfile = io.BytesIO(raw), io.BytesIO()
            self.headers = {"Content-Length": str(len(raw)), "Content-Type": "application/json"}
            self.path, self.command, self.request_version = "/v1/setup/estimate", "POST", "HTTP/1.1"
            self.requestline, self.client_address = "POST /v1/setup/estimate", ("127.0.0.1", 0)
            self.close_connection = True

    r = Req()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        r.do_POST()
    head, _, out = r.wfile.getvalue().partition(b"\r\n\r\n")
    return int(head.decode().split(" ", 2)[1]), out


def test_a_body_without_the_ask_is_the_old_body():
    from server import app
    from server.setup import device as device_mod
    real = device_mod.meminfo
    mem = {"MemAvailable": 61.25}
    sel = {"model": FN, "profile": "flashnext-2.05", "window": 8192, "slots": 2}
    m = _model(FN)
    old = json.dumps(estimate_mod.estimate(m, estimate_mod.check(m, sel), available_gb=61.25)).encode()
    old_path, split_mod.pack_dir = split_mod.pack_dir, lambda m, s: _fn_pack()
    try:
        for body in ({"selection": sel}, sel, {"selection": sel, "split": None}):
            code, raw = _post(body, mem)
            assert code == 200 and raw == old, (body, raw[:200], old[:200])
        code, raw = _post({"selection": sel, "split": {"cuts": [2]}}, mem)
        got = json.loads(raw)
        assert code == 200 and "split" in got and got["split"]["splittable"] is True, got.get("split")
        assert {k: v for k, v in got.items() if k != "split"} == json.loads(old)
        # the ask in a bare selection: the same block
        code, raw2 = _post({**sel, "split": {"cuts": [2]}}, mem)
        assert code == 200 and json.loads(raw2)["split"] == got["split"]
        # a bare selection with "split": null is a selection with an unknown field, as before
        code, raw3 = _post({**sel, "split": None}, mem)
        assert code == 422 and json.loads(raw3)["error"]["code"] == "unknown_field", raw3
    finally:
        split_mod.pack_dir = old_path
        device_mod.meminfo = real
        app.STATE.clear()
    return f"{len(old)} bytes the same without the ask; with it, the same body and a split block"


# ------------------------------------------------------------------ 3
def test_the_flashnext_block():
    from engine.models.flashnext import stages as FS
    path = _fn_pack()
    f = FS.split_facts(path)
    m, sel = _sel(FN, profile="flashnext-2.05", window=8192, slots=2)
    b = _valid(split_mod.block(m, sel, {"caches": "on", "side_window": 4096, "table": "warm", "snapshots_gb": 1.5,
                                        "front_cache_gb": 8, "tail_disk_gb": 16}, path=path))
    assert b["splittable"] is True and b["version"] == "1.0" and b["computed"] is True, b
    assert b["legal_cuts"] == f["cuts"] and [c["cut"] for c in b["candidates"]] == f["cuts"]
    assert b["shape"] == {"window": 8192, "slots": 2, "side_window": 4096, "caches": "on", "table": "warm",
                          "snapshots_gb": 1.5, "front_cache_gb": 8.0, "tail_disk_gb": 16.0, "kv_pool": False}, b["shape"]
    assert b["measured"] and all("what" in x and "date" in x for x in b["measured"])
    attn = set(f["attention_layers"])
    kvl = f["kv_layer"]
    ple = f["table"]["layers"]
    for c in b["candidates"]:
        k = c["cut"]
        assert c["carry_bytes_per_row"] == f["carry_bytes_per_row"][k]
        front, tail = c["front"], c["tail"]
        parts = {s: {p["id"]: p for p in side["parts"]} for s, side in (("front", front), ("tail", tail))}
        w = (f["shared_bytes"] + sum(f["layer_bytes"][:k]) + f["front_extra_bytes"][k]) / GIB
        assert parts["front"]["weights"]["gb"] == split_mod._r(w) and parts["front"]["weights"]["basis"] == "pack_headers"
        na = sum(1 for li in range(k) if li in attn)
        kv = na * (kvl["per_token"] * 8192 + kvl["const"]) + na * (kvl["per_token"] * 4096 + kvl["const"])
        assert parts["front"]["kv"]["gb"] == split_mod._r(kv / GIB) and front["attention_layers"] == na
        assert parts["front"]["snapshots"] == {"id": "snapshots", "gb": 3.0, "basis": "ask"}
        assert "snapshots" not in parts["tail"] and "draft_kv" in parts["tail"] and "draft_kv" not in parts["front"]
        # the table: on the side that holds a layer reading it, its file when warm
        on_front = any(li < k for li in ple)
        assert c["table_on"] == ("front" if on_front else "tail") and ("table" in parts["front"]) == on_front
        side = parts["front" if on_front else "tail"]["table"]
        assert side["gb"] == split_mod._r(f["table"]["bytes"] / GIB) and side["policy"] == "warm"
        for s in ("front", "tail"):
            assert {p["basis"] for p in c[s]["parts"]} <= {"pack_headers", "config", "ask", "registry_whole_model"}, c[s]
            # the side's total and headroom from its parts (each part rounded on its own: a cent a part)
            assert abs(c[s]["gb"] - sum(p["gb"] for p in c[s]["parts"])) <= 0.01 * len(c[s]["parts"]), c[s]
            assert abs(c[s]["headroom_gb"] - (b["budget_gb"] - c[s]["gb"])) <= 0.011, c[s]
            assert c[s]["fits"] == (c[s]["headroom_gb"] >= 0)
            assert c[s]["disk"]["pack_gb"] == split_mod._r(f["pack_bytes"] / GIB)
        assert front["disk"]["cache_dir_gb"] == 8.0 and tail["disk"]["disk_tier_gb"] == 16.0
        slot = (na * (kvl["per_token"] * 4096 + kvl["const"]) + sum(f["section_bytes"][:k])) / GIB
        assert front["slot_gb"] == split_mod._r(slot)
    fitting = [c for c in b["candidates"] if c["fits"]]
    assert b["best_cut"] == max(fitting, key=lambda c: (c["headroom_gb"], -c["cut"]))["cut"]
    # cold: the row cache of the profile in place of the file; the cuts asked only, a cut not legal said
    cold = _valid(split_mod.block(m, sel, {"table": "cold", "cuts": [1, 3, 99]}, path=path))
    assert [c["cut"] for c in cold["candidates"]] == [1, 3] and any("[99]" in n for n in cold["notes"])
    t = [p for c in cold["candidates"] for s in ("front", "tail") for p in c[s]["parts"] if p["id"] == "table"]
    # 2.05 reads its table warm: asked cold, the row cache is the engine's setting (2 GiB by default)
    assert t and all(p["gb"] == 2.0 and p["basis"] == "setting" for p in t), t
    m3, sel3 = _sel(FN, profile="flashnext-3.05", window=8192)
    t = [p for c in split_mod.block(m3, sel3, {"cuts": [3]}, path=path)["candidates"] for s in ("front", "tail")
         for p in c[s]["parts"] if p["id"] == "table"]
    assert t and all(p["gb"] == 2.0 and p["basis"] == "registry" and p["policy"] == "cold" for p in t), t
    # without caches: one slot whatever the selection says, no kept states, no folders
    off = split_mod.block(m, sel, {}, path=path)
    assert off["shape"]["slots"] == 1 and off["shape"]["snapshots_gb"] == 0.0 and any("one slot" in n for n in off["notes"])
    assert all("snapshots" not in {p["id"] for p in c["front"]["parts"]} for c in off["candidates"])
    # a box too small: no best cut, and the note names the closest
    tiny = copy.deepcopy(m)
    tiny["devices"][0]["total_gib"] = 15.0
    _, tsel = tiny, estimate_mod.check(tiny, {"model": FN, "profile": "flashnext-2.05", "window": 8192, "floor_gb": 10})
    small = _valid(split_mod.block(tiny, tsel, {}, path=path))
    assert small["best_cut"] is None and any("No cut fits" in n for n in small["notes"]), small["notes"]
    return f"{len(b['candidates'])} cuts, best {b['best_cut']}; warm and cold, caches on and off"


# ------------------------------------------------------------------ 4
def test_the_lfm2_block():
    path = _lfm_pack()
    m, sel = _sel(LFM, window=8192, slots=1)
    b = _valid(split_mod.block(m, sel, {}, path=path))
    assert b["splittable"] is True and b["table"] is None and b["shape"]["table"] is None, b
    for c in b["candidates"]:
        assert c["table_on"] is None and "draft_kv" not in {p["id"] for p in c["tail"]["parts"]}
    no = _valid(split_mod.block(m, sel, {"caches": "on"}, path=path))
    assert no["splittable"] is False and no["reason"] == "caches" and "keeps no state split" in no["message"], no
    return f"{len(b['candidates'])} cuts; caches asked: {no['reason']}"


# ------------------------------------------------------------------ 5
def test_not_splittable_answers():
    m, sel = _sel("qwen3.8-27b")
    b = _valid(split_mod.block(m, sel, {}))
    assert b["splittable"] is False and b["reason"] == "family" and "not served as two stages" in b["message"], b
    m, sel = _sel(FN, profile="flashnext-2.05", window=8192)
    old_path, split_mod.pack_dir = split_mod.pack_dir, lambda m, s: None
    try:
        b = _valid(split_mod.block(m, sel, {}))
        assert b["splittable"] is False and b["reason"] == "pack" and "not on this box" in b["message"], b
    finally:
        split_mod.pack_dir = old_path
    d = tempfile.mkdtemp(prefix="split-bad-")
    with open(os.path.join(d, "config.json"), "w") as fh:
        fh.write("{}")
    b = split_mod.block(m, sel, {}, path=d)
    assert b["splittable"] is False and b["reason"] == "pack" and "does not read" in b["message"], b
    b = _valid(split_mod.block(m, sel, {"cuts": [0, 999]}, path=_fn_pack()))
    assert b["splittable"] is False and b["reason"] == "cuts" and b["legal_cuts"], b
    from engine.models.flashnext import stages as FS
    facts = dict(json.loads(json.dumps(FS.split_facts(_fn_pack()))), slots_max=1)
    m, sel8 = _sel(FN, profile="flashnext-2.05", window=8192, slots=8)
    b = _valid(split_mod.block(m, sel8, {"caches": "on"}, facts=facts))
    assert b["splittable"] is False and b["reason"] == "slots", b
    # facts read back from JSON (keys as text) give the same block as the family's own
    one = split_mod.block(m, sel, {"cuts": [2]}, facts=dict(facts, slots_max=8))
    two = split_mod.block(m, sel, {"cuts": [2]}, path=_fn_pack())
    assert one == two
    assert set(split_mod.REASONS) == {"family", "pack", "cuts", "caches", "slots"}
    return "family, pack (missing, unreadable), cuts, slots: splittable false with the reason"


# ------------------------------------------------------------------ 6
def test_the_ask_is_checked():
    m, sel = _sel(FN, profile="flashnext-2.05", window=8192)
    _refused(lambda: split_mod.check_ask({"cut": 3}, sel), "split.cut", "unknown")
    _refused(lambda: split_mod.check_ask({"caches": "yes"}, sel), "split.caches", "on or off")
    _refused(lambda: split_mod.check_ask({"side_window": 16384}, sel), "split.side_window")
    _refused(lambda: split_mod.check_ask({"cuts": ["12"]}, sel), "split.cuts")
    _refused(lambda: split_mod.check_ask({"cuts": []}, sel), "split.cuts")
    _refused(lambda: split_mod.check_ask({"table": "hot"}, sel), "split.table")
    _refused(lambda: split_mod.check_ask({"snapshots_gb": -1}, sel), "split.snapshots_gb")
    _refused(lambda: split_mod.check_ask("all", sel), "an object")
    assert split_mod.check_ask(True, sel)["side_window"] == 8192
    # the contract's ask: what the code takes validates, what it refuses for a field it does not know does not
    ask = "setup/split.schema.json#/$defs/ask"
    for good in (True, {}, {"cuts": [12, 24], "caches": "on", "side_window": 4096, "table": "cold",
                            "snapshots_gb": 2, "front_cache_gb": 16, "tail_disk_gb": 16}):
        split_mod.check_ask(good, sel)
        assert not _contract(good, ask), (good, _contract(good, ask))
    from tools import contract_check
    props = contract_check.load("setup/split.schema.json", CONTRACT)["$defs"]["ask"]["properties"]
    assert set(props) == set(split_mod.ASK), (sorted(props), sorted(split_mod.ASK))
    for bad in ({"cut": 3}, {"caches": "yes"}, {"table": "hot"}, "all"):
        assert _contract(bad, ask), bad
    assert split_mod.check_ask({"cuts": [24, 12, 12]}, sel)["cuts"] == [12, 24]
    return "unknown field, caches, side window, cuts, table, a negative size: 422 invalid_split"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
