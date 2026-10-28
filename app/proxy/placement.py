"""The placement planner (SRV-731, concept C6): which nodes fit a model, and which deployment types
may serve it there.

    planner = Planner(fleet)                     one per Fleet (fleet.placement)
    POST /v1/fleet/placement {model, pack, ctx, slots?, nodes?}   (proxy/fleet.py)

THE INPUTS. `model` and `pack` are a Setup Guide model and one of its weight profiles (models/,
server/setup/registry.py), `ctx` the context window a lane holds, `slots` the parallel lanes (1).
`nodes` (the node ids, in pipeline order for a Split) narrows the plan to those; without it every
node of the fleet is planned.

EACH NODE is planned from its own memory estimate, the Setup Guide's formula
(server/setup/estimate.py) with that node's device and MemAvailable: `POST /node/setup/estimate`
through its TandemNode agent, which relays it to the engine's Setup Guide, or computes the same
formula itself when no engine there serves one (`source` engine or agent). This machine read
without an agent is estimated in-process (`process`). A node that serves nothing has the engine's
whole budget (device memory less the floor): it fits when the estimate does. A node that serves
something keeps it: the new engine's own parts (the estimate's total less the OS) and the floor
must fit in what is free now, else `needs 98 GiB, Gold has 61 free`. An off node, one without an
estimate (an older agent, a model its registry lacks), says why and fits nothing.

THE TYPES:

    single     one node that fits
    replicas   two or more nodes that fit and run one release; a member reached through a tunnel
               beside one on the LAN adds the WAN warning
    role       one node that fits, beside the deployment it helps
    split      2 or 4 nodes in the order given, each joined to the next by a fabric link (both
               ends' ConnectX interfaces up in one subnet, from their agents' link-test info), one
               release, and each stage's share fitting: the weights and the KV divided by the
               stages, every other part whole on each. Planned and recorded only: a Split runs
               once ENG-307 builds the pipeline split (`built` false). Without `nodes` the first
               fabric-joined pair that fits is offered.

A type that may not serve says why in `reason` (`no fabric link between Gold and R-1`,
`needs two nodes that fit: only Gold fits`, `R-1 runs 0.15.1, Gold 0.15.0`).
"""

from __future__ import annotations

import threading
import time

from proxy import linktest, nodesource

CONTRACT = "1.0"
FABRIC_TTL_S = 60.0
ESTIMATE_TIMEOUT_S = 20.0


class PlanError(Exception):
    def __init__(self, status: int, kind: str, message: str):
        super().__init__(message)
        self.status, self.kind, self.message = status, kind, message


def estimate_here(body) -> tuple[int, dict]:
    """The Setup Guide's estimate of a selection on this machine (its device, its MemAvailable):
    (status, answer). A node agent's fallback when no engine serves the Setup Guide, and the
    controller's for its own machine read without an agent."""
    sel = body.get("selection", body) if isinstance(body, dict) else body
    try:
        from server.setup import device as device_mod
        from server.setup import estimate as estimate_mod
        from server.setup import registry
    except ImportError as exc:
        return 503, {"error": {"type": "no_setup", "message": f"no Setup Guide models here: "
                                                              f"{exc}"}}
    mid = sel.get("model") if isinstance(sel, dict) else None
    try:
        m = device_mod.windowed(registry.get(mid)) if isinstance(mid, str) else None
    except Exception as exc:                                         # noqa: BLE001
        return 503, {"error": {"type": "no_setup", "message": f"the models' registry: "
                                                              f"{type(exc).__name__}: {exc}"}}
    if m is None:
        return 400, {"error": {"type": "unknown_model", "message": f"no model {mid!r} in models/"}}
    try:
        s = estimate_mod.check(m, sel)
    except estimate_mod.SelectionError as exc:
        return exc.status, exc.body()
    mem = device_mod.meminfo()
    out = estimate_mod.estimate(m, s, available_gb=round(mem["MemAvailable"], 2)
                                if "MemAvailable" in mem else None)
    out["source"] = "agent"
    return 200, out


def _part(est: dict, pid: str) -> float:
    return sum(p["gb"] for p in est.get("parts") or [] if p.get("id") == pid)


class Planner:
    def __init__(self, fleet):
        self.fleet = fleet
        self._fabric: dict[str, tuple[float, list]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ one node
    def _estimate(self, row: dict, src, sel: dict) -> tuple[dict | None, str | None, str]:
        """(estimate, why not, source) of a selection on one node."""
        if isinstance(src, nodesource.AgentNode):
            st, doc = src.post("/node/setup/estimate", {"selection": sel},
                               timeout=ESTIMATE_TIMEOUT_S)
            if st == 200 and isinstance(doc, dict) and "total_gb" in doc:
                return doc, None, doc.get("source") or "engine"
            err = (doc or {}).get("error") or {}
            return None, err.get("message") or f"its agent answered HTTP {st}", "none"
        if isinstance(src, nodesource.LocalNode):
            st, doc = estimate_here({"selection": sel})
            if st == 200:
                return doc, None, "process"
            return None, (doc.get("error") or {}).get("message") or f"HTTP {st}", "none"
        return None, src.status()[1], "none"

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

    # ------------------------------------------------------------------ the fabric
    def _interfaces(self, nid: str, src) -> list:
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
                    ifs = [i for i in doc.get("interfaces") or [] if i.get("kind") == "fabric"
                           and i.get("state") == "up" and i.get("addrs")]
            except Exception:                                        # noqa: BLE001
                ifs = []
        with self._lock:
            self._fabric[nid] = (now, ifs)
        return ifs

    def fabric_between(self, a: tuple, b: tuple) -> bool:
        """A fabric link joins two nodes: a ConnectX interface up on each, in one subnet."""
        ia, ib = self._interfaces(*a), self._interfaces(*b)
        for x in ia:
            for y in ib:
                if linktest._match(x["addrs"], y["addrs"]):
                    return True
        return False

    # ------------------------------------------------------------------ the plan
    def plan(self, body, now: float) -> dict:
        if not isinstance(body, dict):
            raise PlanError(400, "bad_request", "the body is {model, pack, ctx, slots?, nodes?}")
        extra = [k for k in body if k not in ("model", "pack", "ctx", "slots", "nodes")]
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
                 self._single(nodes, "role"), self._split(nodes, srcs, want is not None)]
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

    def _stage_fits(self, n: dict, k: int) -> tuple[bool, float | None]:
        """One stage of `k` on node `n`: (fits, the stage's GiB)."""
        est = n.get("_est")
        if est is None:
            return False, None
        weights, kv = _part(est, "weights"), _part(est, "kv")
        need = n["need_gib"] - weights - kv + (weights + kv) / k
        if n["serving"] and n["free_gib"] is not None:
            return need + est["floor_gb"] <= n["free_gib"], round(need, 2)
        return need + _part(est, "os") <= est["budget_gb"], round(need, 2)

    def _split(self, nodes: list[dict], srcs: dict, given: bool) -> dict:
        out = {"type": "split", "allowed": False, "built": False, "nodes": [], "reason": None,
               "warn": "planned and recorded only: a Split runs once the pipeline split "
                       "(ENG-307) is built", "stages": []}
        if given:
            sets = [nodes]
        else:
            sets = [[a, b] for i, a in enumerate(nodes) for b in nodes[i + 1:]]
        reason = None
        for s in sets:
            why = self._split_set(s, srcs)
            if why is None:
                k = len(s)
                out.update(allowed=True, nodes=[n["node"] for n in s],
                           stages=[{"node": n["node"], "layers": f"stage {i + 1} of {k}",
                                    "gib": self._stage_fits(n, k)[1] or 0.0}
                                   for i, n in enumerate(s)])
                return out
            reason = reason or why
        out["reason"] = reason or "a Split runs over 2 or 4 nodes: the fleet has fewer"
        return out

    def _split_set(self, s: list[dict], srcs: dict) -> str | None:
        if len(s) not in (2, 4):
            return f"a Split runs over 2 or 4 nodes, not {len(s)}"
        for a, b in zip(s, s[1:]):
            if not self.fabric_between((a["node"], srcs[a["node"]][1]),
                                       (b["node"], srcs[b["node"]][1])):
                return f"no fabric link between {a['alias']} and {b['alias']}"
        from proxy.deployments import release
        rels = {release(n["version"]) for n in s}
        if len(rels) > 1:
            return (", ".join(f"{n['alias']} runs {release(n['version']) or '?'}" for n in s)
                    + ": a Split's stages run one release")
        for n in s:
            if n["status"] == "off" or n.get("_est") is None:
                return n["reason"] or f"{n['alias']} has no estimate"
            ok, gib = self._stage_fits(n, len(s))
            if not ok:
                return (f"a stage needs {gib:.0f} GiB, {n['alias']} has "
                        f"{max(0.0, (n['free_gib'] or 0) - n['_est']['floor_gb']):.0f} free"
                        if n["serving"] else
                        f"a stage needs {gib:.0f} GiB, more than {n['alias']}'s budget")
        return None

