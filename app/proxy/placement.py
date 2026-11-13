"""The placement planner (SRV-731, concept C6): which nodes fit a model, and which deployment types
may serve it there.

    planner = Planner(fleet)                     one per Fleet (fleet.placement)
    POST /v1/fleet/placement {model, pack, ctx, slots?, nodes?, split?}   (proxy/fleet.py)

THE INPUTS. `model` and `pack` are a Setup Guide model and one of its weight profiles (the
engine's `GET /v1/setup/models` and `/v1/setup/models/{id}`), `ctx` the context window a lane
holds, `slots` the parallel lanes (1). `nodes` (the node ids, in pipeline order for a Split)
narrows the plan to those; without it every node of the fleet is planned.

EACH NODE is planned from its own memory estimate, the engine's Setup Guide formula with that
node's device and MemAvailable, read over HTTP only: `POST /node/setup/estimate` through its
TandemNode agent, which relays it to its engine's `POST /v1/setup/estimate`; this machine read
without an agent asks its own engine's `POST /v1/setup/estimate` (the fleet's local `tandemllm`
backend, proxy/fleetsetup.py local_engine). `source` is engine (or what an older agent that
computed the estimate itself says); neither the planner nor TandemNode computes one. A node that
serves nothing has the engine's whole budget (device memory less the floor): it fits when the
estimate does. A node that serves something keeps it: the new engine's own parts (the estimate's
total less the OS) and the floor must fit in what is free now, else
`needs 98 GiB, Gold has 61 free`. An off node, one without an estimate (no engine there serves
the Setup Guide, its engine does not answer, a model its registry lacks, an answer the plan cannot
read), says why and fits nothing.

THE TYPES:

    single     one node that fits
    replicas   two or more nodes that fit and run one release; a member reached through a tunnel
               beside one on the LAN adds the WAN warning
    role       one node that fits, beside the deployment it helps
    split      two nodes, the front stage's first: one model cut between two layers (SRV-817,
               proxy/splitplan.py). Planned when the body asks (`split`: true or the ask) or when
               no node fits the model alone. Each node's engine (engine API 1.2) answers its own
               estimate with the `split` block; the plan compares each side of each legal cut with
               its node's budget, takes the cut whose smaller headroom is largest and says every
               number, which are computed and which measured, and why each other cut lost. The
               nodes need an interface pair in one subnet: a fabric pair first, a LAN pair with a
               warning (`warn`), explicit IP literals always. Without `nodes` every ordered pair
               is planned and the best one offered. `built` is true: a Split starts and serves.

A type that may not serve says why in `reason` (`no interface pair in one subnet between Gold
and R-1`,
`needs two nodes that fit: only Gold fits`, `R-1 runs 0.15.1, Gold 0.15.0`).
"""

from __future__ import annotations

import http.client
import json
import math
import threading
import time

from proxy import fleetsetup, nodesource, splitplan

CONTRACT = "1.0"
FABRIC_TTL_S = 60.0
ESTIMATE_TIMEOUT_S = 20.0


class PlanError(Exception):
    def __init__(self, status: int, kind: str, message: str):
        super().__init__(message)
        self.status, self.kind, self.message = status, kind, message


def _part(est: dict, pid: str) -> float:
    return sum(p["gb"] for p in est.get("parts") or [] if p.get("id") == pid)


def _num(v) -> bool:
    """A finite JSON number: an int or a float, not a bool."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    try:
        return math.isfinite(v)
    except OverflowError:                           # an int past a float's range
        return False


def _unusable(doc) -> str | None:
    """Why an estimate answer cannot be planned from, None when it can: each field the plan reads
    as the engine's `POST /v1/setup/estimate` gives it (total_gb, budget_gb and floor_gb numbers,
    available_gb a number or null, fits a bool, parts a list of {id, label, gb})."""
    if not isinstance(doc, dict):
        return "is not a JSON object"
    for k in ("total_gb", "budget_gb", "floor_gb"):
        if not _num(doc.get(k)):
            return f"has no valid {k}"
    if doc.get("available_gb") is not None and not _num(doc["available_gb"]):
        return "has no valid available_gb"
    if not isinstance(doc.get("fits"), bool):
        return "has no valid fits"
    parts = doc.get("parts")
    if not isinstance(parts, list) or not all(
            isinstance(p, dict) and isinstance(p.get("id"), str)
            and isinstance(p.get("label"), str) and _num(p.get("gb")) for p in parts):
        return "has no valid parts"
    return None


def _message(doc) -> str | None:
    """An error answer's message, {"error": {"message": ...}}; None without one."""
    err = doc.get("error") if isinstance(doc, dict) else None
    msg = err.get("message") if isinstance(err, dict) else None
    return msg if isinstance(msg, str) and msg else None


class Planner:
    def __init__(self, fleet):
        self.fleet = fleet
        self._fabric: dict[str, tuple[float, list]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ one node
    def _estimate(self, row: dict, src, sel: dict, split=None) -> tuple[dict | None, str | None,
                                                                          str]:
        """(estimate, why not, source) of a selection on one node; `split`: the ask sent beside
        the selection (an engine on API 1.2 or later only)."""
        if isinstance(src, nodesource.AgentNode):
            body = {"selection": sel} if split is None else {"selection": sel, "split": split}
            st, doc = src.post("/node/setup/estimate", body, timeout=ESTIMATE_TIMEOUT_S)
            if st != 200:
                return None, _message(doc) or f"its agent answered HTTP {st}", "none"
            bad = _unusable(doc)
            if bad is not None:
                return None, f"its agent's answer {bad}", "none"
            return doc, None, doc.get("source") or "engine"
        if isinstance(src, nodesource.LocalNode):
            return self._local_estimate(sel)
        return None, src.status()[1], "none"

    def _local_estimate(self, sel: dict) -> tuple[dict | None, str | None, str]:
        """This machine read without its agent: its own engine's Setup Guide estimate, over HTTP."""
        from proxy.fleet import FleetError
        try:
            host, port = fleetsetup.local_engine(self.fleet)
        except FleetError as exc:
            return None, exc.message, "none"
        except ValueError:                          # the URL's port: out of range, not a number
            return None, "this machine's engine URL has no valid port", "none"
        conn = http.client.HTTPConnection(host, port, timeout=ESTIMATE_TIMEOUT_S)
        try:
            conn.request("POST", "/v1/setup/estimate", body=json.dumps({"selection": sel}).encode(),
                         headers={"Content-Type": "application/json",
                                  "Accept": "application/json"})
            r = conn.getresponse()
            st, raw = r.status, r.read()
        except (OSError, http.client.HTTPException):
            return None, "the engine on this machine does not answer", "none"
        finally:
            conn.close()
        try:
            doc = json.loads(raw)
        except (ValueError, RecursionError):        # not JSON, or nested past the parser's depth
            doc = None
        if st != 200:
            return None, _message(doc) or f"its engine answered HTTP {st}", "none"
        bad = _unusable(doc)
        if bad is not None:
            return None, f"its engine's answer {bad}", "none"
        return doc, None, "engine"

    def _node(self, row: dict, src, sel: dict, busy: set) -> dict:
        a = row["alias"]
        st = src.status()[0]
        out = {"node": row["id"], "alias": a, "status": st, "version": src.version() or None,
               "transport": row["transport"], "fits": False, "need_gib": None,
               "free_gib": None, "budget_gib": None, "total_gib": None, "serving": row["id"]
               in busy, "source": "none", "reason": None, "parts": []}
        if st == "off":
            out["reason"] = f"{a} is off"
            return out
        est, why, source = self._estimate(row, src, sel)
        out["source"] = source
        if est is None:
            out["reason"] = f"{a} has no estimate: {why}"
            return out
        need = round(est["total_gb"] - _part(est, "os"), 2)
        free = est.get("available_gb")
        out.update(need_gib=need, free_gib=free, budget_gib=est["budget_gb"],
                   total_gib=est["total_gb"], parts=[{"id": p["id"], "label": p["label"],
                                                      "gib": p["gb"]} for p in est["parts"]],
                   _est=est)
        if row["drain"]:
            out["reason"] = f"{a} is drained: it takes nothing new"
        elif not est["fits"]:
            out["reason"] = (f"needs {est['total_gb']:.0f} GiB, {a}'s budget is "
                             f"{est['budget_gb']:.0f}")
        elif row["id"] in busy and free is not None and need + est["floor_gb"] > free:
            out["reason"] = f"needs {need:.0f} GiB, {a} has {max(0.0, free - est['floor_gb']):.0f} free"
        else:
            out["fits"] = True
        return out

    # ------------------------------------------------------------------ the interfaces
    def _interfaces(self, nid: str, src) -> list:
        """A node's interfaces that are up and have an address (its agent's link-test info)."""
        now = time.monotonic()
        with self._lock:
            got = self._fabric.get(nid)
        if got is not None and now - got[0] < FABRIC_TTL_S:
            return got[1]
        ifs = []
        if isinstance(src, nodesource.AgentNode):
            try:
                st, doc = src.client.json("GET", "/node/linktest/info", timeout=5.0)
                if st == 200 and isinstance(doc, dict):
                    ifs = [i for i in doc.get("interfaces") or [] if isinstance(i, dict)
                           and i.get("state") == "up" and i.get("addrs")]
            except Exception:                                        # noqa: BLE001
                ifs = []
        with self._lock:
            self._fabric[nid] = (now, ifs)
        return ifs

    @staticmethod
    def _machine(src) -> str | None:
        """What two nodes of one machine share: the machine's serial (a virtual node is a second
        agent on a machine); None when the agent does not say."""
        try:
            ident = src.identity() if hasattr(src, "identity") else None
        except Exception:                                            # noqa: BLE001
            ident = None
        return (ident or {}).get("serial") or None

    # ------------------------------------------------------------------ the plan
    def plan(self, body, now: float) -> dict:
        if not isinstance(body, dict):
            raise PlanError(400, "bad_request", "the body is {model, pack, ctx, slots?, nodes?, "
                                                "split?}")
        extra = [k for k in body if k not in ("model", "pack", "ctx", "slots", "nodes", "split")]
        if extra:
            raise PlanError(400, "bad_request", f"placement: unknown field {extra[0]!r}")
        model, pack, ctx = body.get("model"), body.get("pack"), body.get("ctx")
        slots = body.get("slots", 1)
        if not isinstance(model, str) or not model or not isinstance(pack, str) or not pack:
            raise PlanError(400, "bad_request", "model and pack are a Setup Guide model and one of "
                                                "its weight profiles")
        for k, v in (("ctx", ctx), ("slots", slots)):
            if not isinstance(v, int) or isinstance(v, bool) or v < 1:
                raise PlanError(400, "bad_request", f"{k} is a whole number of at least 1")
        asked = body.get("split") not in (None, False)
        try:
            ask, opts = splitplan.check_ask(body.get("split") if asked else None, slots)
        except splitplan.AskError as exc:
            raise PlanError(400, "bad_request", str(exc))
        f = self.fleet
        f.ensure_local()
        srcs = {row["id"]: (row, src) for row, src in f.sources()}
        want = body.get("nodes")
        if want is not None:
            if not isinstance(want, list) or not all(isinstance(x, str) for x in want) or \
                    len(set(want)) != len(want):
                raise PlanError(400, "bad_request", "nodes is a list of node ids, each once")
            gone = [x for x in want if x not in srcs]
            if gone:
                raise PlanError(404, "not_found", f"no node {gone[0]}")
        order = want if want is not None else list(srcs)
        busy = {m for d in f.deployments() if d["status"] in ("serving", "degraded", "starting")
                for m in d["members"]}
        sel = {"model": model, "profile": pack, "window": ctx, "slots": slots}
        nodes = [self._node(*srcs[n], sel, busy) for n in order]
        types = [self._single(nodes, "single"), self._replicas(nodes),
                 self._single(nodes, "role"),
                 self._split(nodes, srcs, want is not None, sel, ask, opts, asked, busy)]
        for n in nodes:
            n.pop("_est", None)
        return {"contract_version": CONTRACT, "model": model, "pack": pack, "ctx": ctx,
                "slots": slots, "nodes": nodes, "types": types}

    @staticmethod
    def _first_reason(nodes: list[dict]) -> str:
        return next((n["reason"] for n in nodes if n["reason"]), "no node in the fleet")

    def _single(self, nodes: list[dict], t: str) -> dict:
        fit = [n["node"] for n in nodes if n["fits"]]
        return {"type": t, "allowed": bool(fit), "built": True, "nodes": fit,
                "reason": None if fit else self._first_reason(nodes), "warn": None, "stages": []}

    def _replicas(self, nodes: list[dict]) -> dict:
        fit = [n for n in nodes if n["fits"]]
        out = {"type": "replicas", "allowed": False, "built": True, "nodes": [], "reason": None,
               "warn": None, "stages": []}
        if len(fit) < 2:
            out["reason"] = ("needs two nodes that fit: " + (f"only {fit[0]['alias']} fits"
                                                              if fit else "none fits")
                             + ("" if fit else f" ({self._first_reason(nodes)})"))
            return out
        from proxy.deployments import release
        rels: dict[str, list[str]] = {}
        for n in fit:
            rels.setdefault(release(n["version"]) or "?", []).append(n["alias"])
        if len(rels) > 1:
            out["reason"] = ", ".join(f"{', '.join(a)} {'run' if len(a) > 1 else 'runs'} {v}"
                                      for v, a in sorted(rels.items())) + \
                ": Replicas members run one release (update the fleet)"
            return out
        out.update(allowed=True, nodes=[n["node"] for n in fit])
        far = [n["alias"] for n in fit if n["transport"] == "tunnel"]
        near = [n["alias"] for n in fit if n["transport"] != "tunnel"]
        if far and near:
            out["warn"] = (f"{', '.join(near)} on the LAN, {', '.join(far)} remote: each "
                           f"conversation stays on one member; a failover moves it across the "
                           f"internet")
        return out

    # ------------------------------------------------------------------ the Split
    def _stage_engine(self, src) -> tuple[dict | None, str | None]:
        """(the engine a node would run a stage on, None) or (None, why not): its TandemLLM engine
        as its agent lists it, on engine API 1.2 or later."""
        if not isinstance(src, nodesource.AgentNode):
            return None, "has no TandemNode agent: a stage is started by its node's agent"
        e = next((x for x in src.agent_engines() if x.get("kind") == "tandemllm"), None)
        if e is None:
            return None, "has no TandemLLM engine"
        api = (e.get("health") or {}).get("engine_api")
        if (e.get("health") or {}).get("api_assumed") or not splitplan.api_ok(api):
            return None, (f"its engine speaks engine API {api or 'none it says'}: a Split needs "
                          f"{'.'.join(map(str, splitplan.NEEDS_API))} or later")
        return e, None

    def _split_node(self, n: dict, row: dict, src, sel: dict, ask: dict, busy: set) -> dict:
        """One node for a Split: its split block from its own engine, the budget a stage has
        there, its interfaces; `why` when it cannot hold a stage."""
        a = n["alias"]
        out = {"node": n["node"], "alias": a, "block": None, "why": None, "budget_gib": None,
               "disk_free_gib": None, "interfaces": [], "machine": None}
        if n["status"] == "off":
            out["why"] = f"{a} is off"
            return out
        if row["drain"]:
            out["why"] = f"{a} is drained: it takes nothing new"
            return out
        _e, why = self._stage_engine(src)
        if why is not None:
            out["why"] = f"{a} {why}"
            return out
        est, why, _source = self._estimate(row, src, sel, split=ask)
        if est is None:
            out["why"] = f"{a} has no estimate: {why}"
            return out
        block, why = splitplan.read_block(est)
        if block is None:
            out["why"] = f"{a}: {why}"
            return out
        budget = block["budget_gb"]
        free = est.get("available_gb")
        if n["node"] in busy and free is not None:
            # it keeps what it serves: a stage must fit in what is free now, above the floor
            budget = min(budget, max(0.0, round(free - block["floor_gb"], 2)))
        try:
            st, doc = src.client.json("GET", "/node/weights", timeout=5.0)
            store = doc.get("store") if st == 200 and isinstance(doc, dict) else None
            if isinstance(store, dict) and _num(store.get("free_gib")):
                out["disk_free_gib"] = store["free_gib"]
        except Exception:                                            # noqa: BLE001
            pass
        out.update(block=block, budget_gib=budget, machine=self._machine(src),
                   interfaces=self._interfaces(n["node"], src))
        return out

    def _split(self, nodes: list[dict], srcs: dict, given: bool, sel: dict, ask: dict,
               opts: dict, asked: bool, busy: set) -> dict:
        out = {"type": "split", "allowed": False, "built": True, "nodes": [], "reason": None,
               "warn": None, "stages": []}
        fit = [n["alias"] for n in nodes if n["fits"]]
        if not asked and fit:
            out["reason"] = (f"{fit[0]} holds the model alone: a Split is planned when asked "
                             f"for (split)")
            return out
        if given and len(nodes) != 2:
            out["reason"] = f"a Split runs over two nodes (the front stage's first), not " \
                            f"{len(nodes)}"
            return out
        if len(nodes) < 2:
            out["reason"] = "a Split runs over two nodes: the fleet has fewer"
            return out
        from proxy.deployments import release
        got = {n["node"]: self._split_node(n, *srcs[n["node"]], sel, ask, busy) for n in nodes}
        pairs = [(nodes[0], nodes[1])] if given else \
            [(a, b) for a in nodes for b in nodes if a is not b]
        plans, reason = [], None
        for a, b in pairs:
            fa, ta = got[a["node"]], got[b["node"]]
            why = fa["why"] or ta["why"]
            if why is None and release(a["version"]) != release(b["version"]):
                why = (f"{a['alias']} runs {release(a['version']) or '?'}, {b['alias']} "
                       f"{release(b['version']) or '?'}: a Split's stages run one release")
            if why is not None:
                reason = reason or why
                continue
            link = splitplan.link_between(fa, ta, given=opts["link"])
            if link.get("reason"):
                reason = reason or link["reason"]
                continue
            p = splitplan.pair(fa, ta, sel["slots"], cut=opts["cut"])
            if not p["allowed"]:
                reason = reason or p["reason"]
                continue
            if link["warn"] == "loopback":
                link["warn"] = splitplan.LOOP_WARNING.format(
                    gib=p["front"]["need_gib"] + p["tail"]["need_gib"])
            p["link"] = link
            plans.append(p)
        if not plans:
            out["reason"] = reason or "no pair of nodes can hold the two stages"
            return out
        best = max(plans, key=lambda p: (p["link"]["kind"] == "fabric", p["headroom_gib"]))
        best.pop("allowed", None)
        best.pop("reason", None)
        best["window"], best["slots"] = sel["window"], sel["slots"]
        out.update(allowed=True, nodes=[best["front"]["node"], best["tail"]["node"]],
                   warn=best["link"]["warn"], split=best,
                   stages=[{"node": best[r]["node"], "layers": best[r]["layers"],
                            "gib": best[r]["need_gib"]} for r in ("front", "tail")])
        return out
