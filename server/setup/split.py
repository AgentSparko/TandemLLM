"""The estimate's `split` block (SRV-817): one model as two stage processes, cut by cut.

`POST /v1/setup/estimate` takes an optional `split` ask beside the selection:

    {"selection": {...}, "split": {"cuts": [12, 24], "caches": "on", "side_window": 16384,
                                   "table": "cold", "snapshots_gb": 2, "front_cache_gb": 16,
                                   "tail_disk_gb": 16}}

Every field is optional (`ASK` holds the defaults). The selection's `window`, `slots` and `floor_gb`
are the shape; a stage keeps its rows in place, so every window takes its memory at load whatever the
selection's `kv_pool` says. A body without `split` is answered as it was, byte for byte.

The facts come from the model's family, through its stage module (`engine/models/<family>/stages.py`
`split_facts(path)`), read from the pack's config and its files' headers: no weight is loaded. The pack
is the one the selection's weight profile names on this box (`device.profile_dirs`); nothing a client
sends becomes a path. This module names no family.

For each legal cut `k` (the ask's, else every one the family supports) each side's memory, in GiB:

    weights      the stage's own layers, what both stages read (the embedding, the head, the final
                 mixer), the tail's own (the draft layer, the final norm), the norm the front reads
                 of layer k                                                  pack_headers
    table        the n-gram table on the stage that holds a layer that reads it: its file when
                 warm (the page cache), its row cache when cold              pack_headers / registry
    kv           slot 0 at the window and every side slot at the side window, for the stage's
                 attention layers                                            config
    sections     the recurrent and conv states of the stage's layers, a slot  config
    draft_kv     the tail: the draft layer's own cache, a slot              config
    snapshots    the front with caches on: `snapshots_gb` a slot            ask
    engine, allocator and the per-window parts: the registry's figures for the WHOLE model in one
                 process, put on each stage (an upper bound; a stage's own was never measured)
                                                                             registry_whole_model

`slot_gb` is one side slot's cost on that stage (its KV at the side window, its sections, on the tail
its draft cache); the graphs and row buffers a slot captures are not in it (not known a stage).
Disk: each stage opens the pack's index and table file, so both boxes keep the whole pack; the front's
cache folder and the tail's disk tier are the ask's.

`fits` compares each side with this device preset's budget (total less the selection's floor), as if
both boxes were this preset; `best_cut` is the cut whose smaller headroom is the largest among those
where both sides fit. A planner with two different boxes takes `front.gb` and `tail.gb` and each box's
own budget. `measured` lists what a split of this family has been measured to cost on a GPU; every
other number here is computed.

A model whose family is not served as two stages, a pack that is not on this box or does not read, a
cut list with no legal cut, caches asked of a family that keeps no state split: `splittable` false
with `reason` (family, pack, cuts, caches, slots) and a `message`. Not an error.
"""

from __future__ import annotations

import os
import threading
from collections import OrderedDict

GIB = float(1 << 30)
VERSION = "1.0"
#: the ask's fields and their defaults (None: the selection's or the profile's)
ASK = {"cuts": None, "caches": "off", "side_window": None, "table": None, "snapshots_gb": None,
       "front_cache_gb": 0.0, "tail_disk_gb": 0.0}
REASONS = ("family", "pack", "cuts", "caches", "slots")

_FACTS: OrderedDict = OrderedDict()
_LOCK = threading.Lock()


def _r(x: float) -> float:
    from server.setup.estimate import _r as r
    return r(x)


def _bad(field: str, why: str):
    from server.setup.estimate import SelectionError
    return SelectionError(422, "invalid_split", f"split.{field}: {why}", [{"field": f"split.{field}", "reason": why}])


def _num(name: str, v, lo: float = 0.0, hi: float = 1 << 20) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= float(v) <= hi:
        raise _bad(name, f"a number from {lo:g} to {hi:g}")
    return float(v)


def check_ask(ask, sel: dict) -> dict:
    """The `split` ask complete and checked against the selection; SelectionError (422 invalid_split)."""
    if ask is True:
        ask = {}
    if not isinstance(ask, dict):
        raise _bad("", "an object (or true for the defaults)")
    extra = sorted(k for k in ask if k not in ASK)
    if extra:
        raise _bad(extra[0], f"unknown; the split ask has {', '.join(ASK)}")
    out = dict(ASK, **ask)
    if out["cuts"] is not None:
        if not isinstance(out["cuts"], list) or not out["cuts"] or len(out["cuts"]) > 4096 or any(
                isinstance(k, bool) or not isinstance(k, int) for k in out["cuts"]):
            raise _bad("cuts", "a list of whole layer numbers")
        out["cuts"] = sorted(set(out["cuts"]))
    if out["caches"] not in ("on", "off"):
        raise _bad("caches", "on or off")
    sw = out["side_window"]
    if sw is None:
        sw = sel["window"]
    if isinstance(sw, bool) or not isinstance(sw, int) or not 1 <= sw <= sel["window"]:
        raise _bad("side_window", f"a whole number of tokens from 1 to the window ({sel['window']})")
    out["side_window"] = sw
    if out["table"] not in (None, "warm", "cold"):
        raise _bad("table", "warm or cold")
    if out["snapshots_gb"] is not None:
        out["snapshots_gb"] = _num("snapshots_gb", out["snapshots_gb"])
    out["front_cache_gb"] = _num("front_cache_gb", out["front_cache_gb"])
    out["tail_disk_gb"] = _num("tail_disk_gb", out["tail_disk_gb"])
    return out


def family_module(engine: str):
    """The stage module of the registry's `engine`, or (None, why)."""
    from engine.methods import stages as S
    from engine.models import REGISTRY
    arch = REGISTRY.get(engine)
    if arch is None or not arch.package:
        return None, f"the {engine} engine is not served as two stages"
    try:
        mod = S.family_stages(arch)
    except ModuleNotFoundError as e:
        if e.name and e.name.endswith(".stages"):
            return None, f"the {engine} engine is not served as two stages"
        raise
    if not hasattr(mod, "split_facts") or not hasattr(mod, "open_stage"):
        return None, f"the {engine} engine is not served as two stages"
    return mod, None


def facts_of(mod, path: str) -> dict:
    """`mod.split_facts(path)`, kept while the pack's config and index stay as they are."""
    stamp = []
    for f in ("config.json", "model.safetensors.index.json", "model.safetensors"):
        try:
            st = os.stat(os.path.join(path, f))
            stamp.append((f, st.st_mtime_ns, st.st_size))
        except OSError:
            pass
    key = (mod.__name__, os.path.realpath(path), tuple(stamp))
    with _LOCK:
        got = _FACTS.get(key)
        if got is not None:
            _FACTS.move_to_end(key)
            return got
    got = mod.split_facts(path)
    with _LOCK:
        _FACTS[key] = got
        while len(_FACTS) > 8:
            _FACTS.popitem(last=False)
    return got


def pack_dir(m: dict, sel: dict) -> str | None:
    """The folder the selection's weight profile names on this box, when its weights are there."""
    from server.setup import device as device_mod
    d = device_mod.profile_dirs([sel["profile"]]).get(sel["profile"])
    return d["dir"] if device_mod.weights_present(d) else None


def _at(d: dict, k: int):
    """A per-cut fact by its cut (a dict read back from JSON has the keys as text)."""
    return d[k] if k in d else d[str(k)]


def no(reason: str, message: str, **more) -> dict:
    assert reason in REASONS
    return {"version": VERSION, "splittable": False, "reason": reason, "message": message, **more}


def block(m: dict, sel: dict, ask, *, path: str | None = None, facts: dict | None = None) -> dict:
    """The `split` block of a checked selection `sel` of model `m` for the ask `ask` (`check_ask`). `path` /
    `facts`: given (a test's), else the profile's pack on this box and its family's facts."""
    from server.setup import registry
    ask = check_ask(ask, sel)
    if facts is None:
        mod, why = family_module(m["engine"])
        if mod is None:
            return no("family", why)
        path = path or pack_dir(m, sel)
        if not path:
            return no("pack", f"the weights of {sel['profile']} are not on this box: the split is computed from "
                              "their headers")
        try:
            facts = facts_of(mod, path)
        except (OSError, ValueError, KeyError) as e:
            return no("pack", f"the pack of {sel['profile']} does not read: {type(e).__name__}: {e}"[:300])
    if ask["caches"] == "on" and not facts["caches"]:
        return no("caches", f"the {m['engine']} engine keeps no state split: caches off, one slot")
    slots = sel["slots"] if ask["caches"] == "on" else 1
    if slots > int(facts["slots_max"]):
        return no("slots", f"a split of this model serves at most {facts['slots_max']} slots, not {slots}")
    legal = [int(k) for k in facts["cuts"]]
    cuts = legal if ask["cuts"] is None else [k for k in ask["cuts"] if k in legal]
    not_legal = [] if ask["cuts"] is None else [k for k in ask["cuts"] if k not in legal]
    if not cuts:
        return no("cuts", f"none of the cuts {not_legal} is legal; this model's are 1 to {facts['layers'] - 1}"
                  if legal else "this pack has no legal cut", legal_cuts=legal)
    prof = registry.profile(m, sel["profile"])
    dev = next(d for d in m["devices"] if d["id"] == sel["device"])
    budget = dev["total_gib"] - sel["floor_gb"]
    window, sw = sel["window"], ask["side_window"]
    snaps = ask["snapshots_gb"] if ask["snapshots_gb"] is not None else float(facts["snapshots_gb"])
    table = facts.get("table")
    policy = ask["table"] or ((prof.get("table") or {}).get("policy") or "cold")
    table_mem = 0.0
    table_basis = "pack_headers"
    if table:
        if policy == "warm":
            table_mem = table["bytes"] / GIB
        else:
            table_mem, table_basis = float((prof.get("table") or {}).get("cache_gib", 2.0)), "registry"
    fixed = [(p["id"], p["gib"]) for p in m["memory"]["parts"]]
    for p in m["memory"].get("per_window_parts") or []:
        gb = p["bytes_per_token"] * window / GIB
        if p.get("max_gib") is not None:
            gb = min(gb, p["max_gib"])
        fixed.append((p["id"], gb))
    kvl = facts["kv_layer"]

    def kv(layers: int, tokens: int) -> float:
        return layers * (kvl["per_token"] * tokens + kvl["const"]) / GIB

    attn = set(facts["attention_layers"])
    lb, sec = facts["layer_bytes"], facts["section_bytes"]
    out_cands = []
    for k in cuts:
        sides = {}
        for role, lo, hi in (("front", 0, k), ("tail", k, facts["layers"])):
            n_attn = sum(1 for li in range(lo, hi) if li in attn)
            w = (facts["shared_bytes"] + sum(lb[lo:hi])
                 + (_at(facts["front_extra_bytes"], k) if role == "front" else facts["tail_only_bytes"])) / GIB
            holds_table = bool(table) and any(lo <= li < hi for li in table["layers"])
            sections = sum(sec[lo:hi]) / GIB
            draft = facts["draft_attention_layers"] if role == "tail" else 0
            slot = kv(n_attn, sw) + sections + kv(draft, sw)
            parts = [{"id": "weights", "gb": w, "basis": "pack_headers"}]
            if holds_table:
                parts.append({"id": "table", "gb": table_mem, "basis": table_basis, "policy": policy})
            parts += [{"id": "kv", "gb": kv(n_attn, window) + (slots - 1) * kv(n_attn, sw), "basis": "config",
                       "attention_layers": n_attn},
                      {"id": "sections", "gb": slots * sections, "basis": "config"}]
            if draft:
                parts.append({"id": "draft_kv", "gb": kv(draft, window) + (slots - 1) * kv(draft, sw),
                              "basis": "config"})
            if role == "front" and ask["caches"] == "on":
                parts.append({"id": "snapshots", "gb": snaps * slots, "basis": "ask"})
            parts += [{"id": pid, "gb": gb, "basis": "registry_whole_model"} for pid, gb in fixed]
            total = sum(p["gb"] for p in parts)
            disk = {"pack_gb": _r(facts["pack_bytes"] / GIB)}
            if role == "front":
                disk["cache_dir_gb"] = _r(ask["front_cache_gb"]) if ask["caches"] == "on" else 0.0
            else:
                disk["disk_tier_gb"] = _r(ask["tail_disk_gb"]) if ask["caches"] == "on" else 0.0
            sides[role] = {"layers": [lo, hi], "attention_layers": n_attn, "gb": _r(total),
                           "headroom_gb": _r(budget - total), "fits": _r(total) <= _r(budget),
                           "slot_gb": _r(slot), "table": holds_table,
                           "parts": [dict(p, gb=_r(p["gb"])) for p in parts], "disk": disk}
        out_cands.append({"cut": k, "carry_bytes_per_row": int(_at(facts["carry_bytes_per_row"], k)),
                          "table_on": ("front" if sides["front"]["table"] else "tail" if sides["tail"]["table"]
                                       else None),
                          "front": sides["front"], "tail": sides["tail"],
                          "fits": sides["front"]["fits"] and sides["tail"]["fits"],
                          "headroom_gb": min(sides["front"]["headroom_gb"], sides["tail"]["headroom_gb"])})
    fitting = [c for c in out_cands if c["fits"]]
    best = max(fitting, key=lambda c: (c["headroom_gb"], -c["cut"]))["cut"] if fitting else None
    notes = ["A stage keeps its rows in place (no KV pool): every window takes its memory at load."]
    if ask["caches"] == "off" and sel["slots"] > 1:
        notes.append(f"Without caches a split serves one slot: computed for 1, not {sel['slots']}.")
    if not_legal:
        notes.append(f"Not legal cuts, left out: {not_legal}.")
    if best is None:
        short = max(out_cands, key=lambda c: c["headroom_gb"])
        side = "front" if short["front"]["headroom_gb"] < short["tail"]["headroom_gb"] else "tail"
        notes.append(f"No cut fits two boxes of this preset; the closest is {short['cut']}, where the {side} "
                     f"lacks {_r(-short['headroom_gb']):.2f} GiB.")
    notes.append("The engine, allocator and per-window parts are the whole model's figures in one process, put on "
                 "each stage; a stage's own were never measured. A side slot's graphs and row buffers are not in "
                 "slot_gb.")
    return {
        "version": VERSION, "splittable": True, "reason": None, "message": None,
        "family": m["engine"], "layers": facts["layers"], "legal_cuts": legal,
        "shape": {"window": window, "slots": slots, "side_window": sw, "caches": ask["caches"],
                  "table": policy if table else None, "snapshots_gb": snaps if ask["caches"] == "on" else 0.0,
                  "front_cache_gb": ask["front_cache_gb"] if ask["caches"] == "on" else 0.0,
                  "tail_disk_gb": ask["tail_disk_gb"] if ask["caches"] == "on" else 0.0, "kv_pool": False},
        "budget_gb": _r(budget), "device_gb": _r(dev["total_gib"]), "floor_gb": _r(sel["floor_gb"]),
        "layer_gb": [_r(b / GIB) for b in lb], "shared_gb": _r(facts["shared_bytes"] / GIB),
        "tail_only_gb": _r(facts["tail_only_bytes"] / GIB),
        "kv_bytes_per_token_layer": [kvl["per_token"] if li in attn else 0 for li in range(facts["layers"])],
        "section_bytes_layer": list(sec),
        "table": ({"gb": _r(table["bytes"] / GIB), "layers": list(table["layers"]), "policy": policy}
                  if table else None),
        "candidates": out_cands, "best_cut": best,
        "computed": True, "measured": list(facts.get("measured") or []),
        "notes": notes,
    }
