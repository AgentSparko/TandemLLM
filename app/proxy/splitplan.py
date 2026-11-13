"""The plan of a Split (SRV-817): one model cut across two nodes, from the engines' own estimates.

    ask, opts = check_ask(body["split"], slots)      the engine's part of the ask, and the planner's
    block, why = read_block(estimate)                a node's `split` block, or why it has none
    plan = pair(front, tail, ...)                    one ordered pair of nodes, cut by cut
    link = link_between(...)                         the interface pair the two stages talk over

A SPLIT is one model as two engine processes: the FRONT stage holds the first layers `[0, k)`, the
TAIL the rest `[k, layers)` and everything a client talks to. The engine says what each side of
each legal cut needs (engine API 1.2, `POST /v1/setup/estimate` with a `split` ask; the block is
contracts/engine-api-v1/setup/split.schema.json). Nothing here knows a model: every number is the
estimate's, or follows from it by the rule below.

THE RULE. Each node answers for itself: the front's need of a cut is the front node's own answer,
the tail's the tail node's. A side's need is the estimate's `gb` for that side (weights, the n-gram
table on the stage that reads it, the KV of every slot at its window, the recurrent sections, the
front's kept states, the engine's fixed parts) plus the SLOT MARGIN: the estimate's `slot_gb` leaves
out a side slot's graphs and row buffers, and the one measurement (a tail side slot: 0.87 GiB
allocated against 0.44 computed) is twice the figure, so each side slot is counted once more
(`SLOT_MARGIN` x `slot_gb` x side slots). A side fits when its need is within its node's budget
(the device's memory less the floor, from that node's own estimate); a node that keeps serving
something else must hold it in what is free now. Among the cuts where both sides fit, the plan takes
the one whose smaller headroom is largest; a tie goes to the smaller carry, then the smaller cut.
Every other cut is listed with why it lost.

THE LINK. The two stages talk over one TCP connection between explicit IP literals, never a
wildcard. The planner looks for an interface pair in one subnet (each agent's link-test info): a
fabric pair first, else a LAN pair with a warning, because the data link is neither signed nor
encrypted after its handshake. Two nodes on one machine (a test layout) use loopback. `link` in
the ask names the addresses instead: `{"listen": "IP[:PORT]", "peer": "IP[:PORT]"}` (the front
listens, the tail dials `peer`, which is `listen` unless a relay sits between).

Standard library only.
"""

from __future__ import annotations

import ipaddress
import math

#: the engine API a Split needs on both nodes
NEEDS_API = (1, 2)
#: each side slot counted once more than the estimate's `slot_gb` (see THE RULE)
SLOT_MARGIN = 1.0
#: the port the front listens on for the tail unless the ask names one
LINK_PORT = 7740
RULE = ("the cut whose smaller headroom is largest among the cuts where both sides fit; a tie goes "
        "to the smaller carry, then the smaller cut")
MARGIN_TEXT = ("each side slot is counted once more than the estimate's slot figure: its graphs "
               "and row buffers are not in the estimate (measured once: 0.87 GiB against 0.44)")
LAN_WARNING = ("no fabric link: the stages talk over the LAN pair {a} and {b}. The data link is "
               "neither signed nor encrypted after its handshake: use it on a private network only")
LOOP_WARNING = ("both stages run on one machine over loopback: a test layout; their needs add up "
                "on that machine ({gib:.1f} GiB)")
GIVEN_WARNING = ("the link addresses were given by hand. The data link is neither signed nor "
                 "encrypted after its handshake: use it on a private network only")
#: the ask's fields the engine reads (setup/split.schema.json `$defs.ask`)
ENGINE_ASK = ("cuts", "caches", "side_window", "table", "snapshots_gb", "front_cache_gb",
              "tail_disk_gb")
#: the planner's own
PLAN_ASK = ("cut", "link")


class AskError(ValueError):
    pass


def _num(v) -> bool:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    try:
        return math.isfinite(v)
    except OverflowError:
        return False


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def api_ok(version) -> bool:
    """An engine API version that has the split block and the stage block: 1.2 or later of 1.x."""
    if not isinstance(version, str):
        return False
    a, _, b = version.partition(".")
    if not (a.isascii() and a.isdigit() and b.isascii() and b.isdigit()):
        return False
    return (int(a), int(b)) >= NEEDS_API and int(a) == NEEDS_API[0]


# ---------------------------------------------------------------------------- addresses

def address(text, what: str, default_port: int | None = LINK_PORT) -> tuple[str, int]:
    """(ip, port) of `IP[:PORT]` or `[IPv6]:PORT`: an IP literal, never a name, never every
    interface. AskError says which rule it broke."""
    if not isinstance(text, str) or not text.strip():
        raise AskError(f"{what} is an IP literal and a port (IP:PORT)")
    s = text.strip()
    host, port = s, None
    if s.startswith("["):
        end = s.find("]")
        if end < 0:
            raise AskError(f"{what}: {s!r} is not an address")
        host, rest = s[1:end], s[end + 1:]
        if rest:
            if not rest.startswith(":"):
                raise AskError(f"{what}: {s!r} is not an address")
            port = rest[1:]
    elif s.count(":") == 1:
        host, port = s.split(":")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        raise AskError(f"{what} takes an IP literal, not {host!r}: an address is named, never "
                       f"resolved") from None
    if ip.is_unspecified:
        raise AskError(f"{what}: {host} is every interface; name one IP")
    if port is None:
        if default_port is None:
            raise AskError(f"{what} needs a port (IP:PORT)")
        n = default_port
    else:
        if not (port.isascii() and port.isdigit()) or not 0 < int(port) < 65536:
            raise AskError(f"{what}: the port is a number from 1 to 65535")
        n = int(port)
    return str(ip), n


def join(ip: str, port: int) -> str:
    return f"[{ip}]:{port}" if ":" in ip else f"{ip}:{port}"


def _same_subnet(local: list[str], peer: list[str]) -> tuple[str, str] | None:
    """(local address, peer address) of the first pair in one subnet, never the same address."""
    for a in local:
        try:
            ia = ipaddress.ip_interface(a)
        except ValueError:
            continue
        for b in peer:
            try:
                ib = ipaddress.ip_interface(b)
            except ValueError:
                continue
            if ib.ip in ia.network and ib.ip != ia.ip and ia.version == ib.version:
                return str(ia.ip), str(ib.ip)
    return None


def _kind_of(ip: str, ifs: list[dict]) -> tuple[str | None, str | None]:
    """(fabric | lan | None, interface) of an address among a node's interfaces."""
    for i in ifs:
        for a in i.get("addrs") or []:
            try:
                if str(ipaddress.ip_interface(a).ip) == ip:
                    return ("fabric" if i.get("kind") == "fabric" else "lan"), i.get("iface")
            except ValueError:
                continue
    return None, None


def link_between(front: dict, tail: dict, given=None, port: int = LINK_PORT) -> dict:
    """The link of a pair: {kind, listen, peer, front_iface, tail_iface, warn}, or {reason}.
    `front` and `tail`: {alias, interfaces (the agent's link-test info, up and with an address),
    machine (what tells two nodes of one machine apart from two machines)}."""
    fa, ta = front["alias"], tail["alias"]
    fi = [i for i in front.get("interfaces") or [] if i.get("addrs") and i.get("state") in
          ("up", None)]
    ti = [i for i in tail.get("interfaces") or [] if i.get("addrs") and i.get("state") in
          ("up", None)]
    one_machine = bool(front.get("machine")) and front.get("machine") == tail.get("machine")
    if given is not None:
        if not isinstance(given, dict) or set(given) - {"listen", "peer"} or "listen" not in given:
            return {"reason": "link is {listen, peer?}: the address the front listens on and the "
                              "one the tail dials"}
        try:
            lip, lport = address(given["listen"], "link.listen", port)
            pip, pport = address(given.get("peer") or join(lip, lport), "link.peer", lport)
        except AskError as exc:
            return {"reason": str(exc)}
        loop = ipaddress.ip_address(lip).is_loopback or ipaddress.ip_address(pip).is_loopback
        if loop and not one_machine:
            return {"reason": f"{lip if ipaddress.ip_address(lip).is_loopback else pip} is "
                              f"loopback: it reaches {fa} or {ta} alone, never both"}
        kind, iface = _kind_of(lip, fi)
        if loop:
            kind = "loopback"
        out = {"kind": kind or "given", "listen": join(lip, lport), "peer": join(pip, pport),
               "front_iface": iface, "tail_iface": None, "warn": None}
        if kind == "loopback":
            out["warn"] = "loopback"
        elif kind != "fabric":
            out["warn"] = GIVEN_WARNING if kind is None else \
                LAN_WARNING.format(a=f"{fa} {lip}", b=f"{ta} {pip}")
        return out
    if one_machine:
        return {"kind": "loopback", "listen": join("127.0.0.1", port),
                "peer": join("127.0.0.1", port), "front_iface": "lo", "tail_iface": "lo",
                "warn": "loopback"}
    for want in ("fabric", "lan"):
        for x in fi:
            for y in ti:
                both = x.get("kind") == "fabric" and y.get("kind") == "fabric"
                if (want == "fabric") != both:
                    continue
                got = _same_subnet(x["addrs"], y["addrs"])
                if got is None:
                    continue
                out = {"kind": want, "listen": join(got[0], port), "peer": join(got[0], port),
                       "front_iface": x.get("iface"), "tail_iface": y.get("iface"), "warn": None}
                if want == "lan":
                    out["warn"] = LAN_WARNING.format(a=f"{fa} {got[0]}", b=f"{ta} {got[1]}")
                return out
    return {"reason": f"no interface pair in one subnet between {fa} and {ta}: join them with "
                      f"the fabric cable, or put both on one LAN (allowed with a warning)"}


# ---------------------------------------------------------------------------- the ask

def check_ask(raw, slots: int) -> tuple[dict, dict]:
    """(the engine's ask, the planner's own options) of a placement body's `split`: true (every
    default) or an object. AskError for a field of the planner's that is malformed; the engine
    checks its own (422 invalid_split, passed on in its words)."""
    if raw is True or raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise AskError("split is true or an object")
    extra = [k for k in raw if k not in ENGINE_ASK + PLAN_ASK]
    if extra:
        raise AskError(f"split: unknown field {extra[0]!r}")
    ask = {k: raw[k] for k in ENGINE_ASK if raw.get(k) is not None}
    ask.setdefault("caches", "on")
    opts = {"cut": None, "link": raw.get("link")}
    if raw.get("cut") is not None:
        if not _int(raw["cut"]) or raw["cut"] < 1:
            raise AskError("split.cut is a layer boundary (a whole number of at least 1)")
        opts["cut"] = raw["cut"]
    return ask, opts


# ---------------------------------------------------------------------------- the block

def read_block(doc) -> tuple[dict | None, str | None]:
    """(the split block of an estimate answer, None) when the planner can read it; (None, why)
    for an answer without one, one that says not splittable, or one whose fields do not read."""
    b = doc.get("split") if isinstance(doc, dict) else None
    if not isinstance(b, dict):
        return None, "its engine's estimate has no split block"
    if b.get("splittable") is not True:
        msg = b.get("message")
        return None, msg if isinstance(msg, str) and msg else \
            f"not splittable ({b.get('reason') or 'no reason given'})"
    if not _int(b.get("layers")) or b["layers"] < 2 or not _num(b.get("budget_gb")) or \
            not _num(b.get("floor_gb")) or not isinstance(b.get("candidates"), list):
        return None, "its engine's split block does not read (layers, budget_gb, candidates)"
    for c in b["candidates"]:
        ok = isinstance(c, dict) and _int(c.get("cut")) and 0 < c["cut"] < b["layers"]
        for side in ("front", "tail"):
            s = c.get(side) if ok else None
            ok = ok and isinstance(s, dict) and _num(s.get("gb")) and _num(s.get("slot_gb")) \
                and isinstance(s.get("parts"), list) and all(
                    isinstance(p, dict) and isinstance(p.get("id"), str) and _num(p.get("gb"))
                    for p in s["parts"])
        if not ok:
            return None, "its engine's split block does not read (a candidate)"
    return b, None


def reason_of(block) -> str | None:
    """The machine-readable reason of a block that says not splittable, None otherwise."""
    if isinstance(block, dict) and block.get("splittable") is False:
        r = block.get("reason")
        return r if isinstance(r, str) else "unknown"
    return None


def _side(c: dict, side: str, block: dict, node: dict, slots: int, caches: bool) -> dict:
    s = c[side]
    side_slots = max(0, slots - 1) if caches else 0
    margin = round(SLOT_MARGIN * s["slot_gb"] * side_slots, 2)
    need = round(s["gb"] + margin, 2)
    budget = node["budget_gib"]
    parts = [{"id": p["id"], "gib": p["gb"], "basis": p.get("basis") or "computed"}
             for p in s["parts"]]
    if margin:
        parts.append({"id": "slot_margin", "gib": margin, "basis": "margin"})
    lo, hi = (0, c["cut"]) if side == "front" else (c["cut"], block["layers"])
    disk = s.get("disk") if isinstance(s.get("disk"), dict) else {}
    return {"node": node["node"], "alias": node["alias"], "role": side, "layers": f"{lo}:{hi}",
            "need_gib": need, "estimate_gib": s["gb"], "margin_gib": margin,
            "budget_gib": budget, "headroom_gib": round(budget - need, 2),
            "fits": need <= budget, "slot_gib": s["slot_gb"], "table": bool(s.get("table")),
            "parts": parts,
            "disk": {"pack_gib": disk.get("pack_gb"),
                     "cache_gib": disk.get("cache_dir_gb" if side == "front" else "disk_tier_gb"),
                     "free_gib": node.get("disk_free_gib")}}


def _why_not(f: dict, t: dict) -> str | None:
    for s in (f, t):
        if not s["fits"]:
            return (f"the {s['role']} needs {s['need_gib']:.1f} GiB, {s['alias']} has "
                    f"{s['budget_gib']:.1f}: {s['need_gib'] - s['budget_gib']:.1f} short")
        d = s["disk"]
        if d["free_gib"] is not None and d["cache_gib"] and d["cache_gib"] > d["free_gib"]:
            return (f"the {s['role']}'s cache folder needs {d['cache_gib']:.0f} GB, "
                    f"{s['alias']} has {d['free_gib']:.0f} free")
    return None


def pair(front: dict, tail: dict, slots: int, cut: int | None = None) -> dict:
    """The plan of one ordered pair. `front` and `tail`: {node, alias, block (read_block's),
    budget_gib (what this node may give a stage), disk_free_gib}. {allowed, reason, cut, layers,
    front, tail, candidates, ...}: the chosen cut's two sides in full, every computed cut in
    short with why it lost."""
    fb, tb = front["block"], tail["block"]
    out = {"allowed": False, "reason": None, "cut": None, "layers": fb["layers"], "rule": RULE,
           "family": fb.get("family"), "front": None, "tail": None, "candidates": [],
           "shape": fb.get("shape") if isinstance(fb.get("shape"), dict) else {},
           "margin": {"slot_factor": SLOT_MARGIN, "text": MARGIN_TEXT},
           "computed": True,
           "measured": [m for m in fb.get("measured") or [] if isinstance(m, dict)],
           "notes": [n for n in fb.get("notes") or [] if isinstance(n, str)]}
    if fb["layers"] != tb["layers"] or fb.get("family") != tb.get("family"):
        out["reason"] = (f"{front['alias']} and {tail['alias']} do not describe one model: "
                         f"{fb.get('family')} with {fb['layers']} layers, {tb.get('family')} "
                         f"with {tb['layers']}")
        return out
    caches = (out["shape"].get("caches") == "on")
    tails = {c["cut"]: c for c in tb["candidates"]}
    rows = []
    for c in fb["candidates"]:
        k = c["cut"]
        if k not in tails:
            continue
        f = _side(c, "front", fb, front, slots, caches)
        t = _side(tails[k], "tail", tb, tail, slots, caches)
        why = _why_not(f, t)
        rows.append({"cut": k, "front": f, "tail": t, "fits": why is None, "why": why,
                     "headroom_gib": round(min(f["headroom_gib"], t["headroom_gib"]), 2),
                     "carry_bytes_per_row": c.get("carry_bytes_per_row"),
                     "table_on": c.get("table_on")})
    if not rows:
        out["reason"] = "the two nodes' estimates share no cut"
        return out
    fit = [r for r in rows if r["fits"]]
    best = None
    if cut is not None:
        best = next((r for r in rows if r["cut"] == cut), None)
        if best is None:
            out["reason"] = (f"cut {cut} is not one the engine computed (legal cuts: 1 to "
                             f"{fb['layers'] - 1})")
        elif not best["fits"]:
            out["reason"], best = f"cut {cut}: {best['why']}", None
    elif fit:
        best = min(fit, key=lambda r: (-r["headroom_gib"], r["carry_bytes_per_row"] or 0,
                                       r["cut"]))
    else:
        near = max(rows, key=lambda r: r["headroom_gib"])
        out["reason"] = f"no cut fits: at the closest one (cut {near['cut']}) {near['why']}"
    for r in rows:
        why = r["why"]
        if why is None and best is not None and r is not best:
            why = (f"asked for cut {best['cut']}" if cut is not None else
                   f"smaller headroom ({r['headroom_gib']:.2f} GiB) than cut {best['cut']} "
                   f"({best['headroom_gib']:.2f})" if r["headroom_gib"] < best["headroom_gib"]
                   else f"the same headroom as cut {best['cut']}: the tie rule")
        out["candidates"].append({"cut": r["cut"], "front_gib": r["front"]["need_gib"],
                                  "tail_gib": r["tail"]["need_gib"],
                                  "headroom_gib": r["headroom_gib"], "fits": r["fits"],
                                  "chosen": r is best, "why": None if r is best else why})
    if best is not None:
        out.update(allowed=True, cut=best["cut"], front=best["front"], tail=best["tail"],
                   headroom_gib=best["headroom_gib"],
                   carry_bytes_per_row=best["carry_bytes_per_row"], table_on=best["table_on"],
                   why=(f"asked for: cut {cut}" if cut is not None else
                        f"cut {best['cut']} leaves the larger of the smaller headrooms: "
                        f"{best['headroom_gib']:.2f} GiB ({best['front']['alias']} "
                        f"{best['front']['headroom_gib']:.2f}, {best['tail']['alias']} "
                        f"{best['tail']['headroom_gib']:.2f}); {len(fit)} of {len(rows)} cuts "
                        f"fit"))
    return out


def stage_env(plan: dict, role: str) -> dict:
    """What one stage's node agent is told (PUT /node/engines/{id}/stage), without the key: the
    stage's range, its side of the link, and the shape both stages share."""
    sh, link = plan.get("shape") or {}, plan["link"]
    caches = sh.get("caches") == "on"
    doc = {"stage": plan[role]["layers"], "caches": "on" if caches else "off",
           "slots": int(sh.get("slots") or 1) if caches else 1,
           "need_gib": plan[role]["need_gib"]}
    for k in ("window", "side_window"):
        if _int(sh.get(k)) and sh[k] > 0:
            doc[k] = sh[k]
    if role == "front":
        doc["listen"] = link["listen"]
        if caches:
            if _num(sh.get("snapshots_gb")):
                doc["snapshots_gb"] = sh["snapshots_gb"]
            if _num(sh.get("front_cache_gb")) and sh["front_cache_gb"] > 0:
                doc["cache_gb"] = sh["front_cache_gb"]
    else:
        doc["peer"] = link["peer"]
    return doc
