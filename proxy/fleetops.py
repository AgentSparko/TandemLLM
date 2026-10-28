"""The node actions of TandemApp's controller (VIS-250): drain, restart an engine, reboot, update,
forget, release a lease and test a link, each with what it would do said before it is done.

    ops = Ops(fleet)                              one per Fleet (fleet.ops)
    ops.plan(id, action, now, engine)             GET  /v1/fleet/nodes/{id}/actions/{action}
    ops.act(id, action, body, who, now)           POST /v1/fleet/nodes/{id}/actions/{action}
    ops.test_link(link id, who, now)              POST /v1/fleet/links/{id}/test
    ops.drain_of(backend id)                      the gateway's question (proxy/relay.py)

ACTIONS: `drain`, `undrain`, `restart-engine` (body or query `engine`), `reboot`, `update`,
`forget`, `release-lease`. A plan names what the action stops (the deployments on the node and
where their new requests go), what it removes and what it keeps, and whether it may run and why
not. The destructive ones (all but drain and undrain) carry a `confirm` token, single use, good
for CONFIRM_S: their POST without one is refused 428 `confirm_required`, with a spent, stale or
another action's 409 `confirm_invalid`. So whoever runs one has read its plan first.

DRAIN (concept B5). A drained node takes no new request through this gateway: the relay skips a
route's backend on it and tries the route's fallbacks, else answers 503 `node_draining` with
Retry-After DRAIN_RETRY_S (proxy/relay.py). What is in flight finishes. In flight is the larger
of the gateway's own requests on the node's engines and the engines' own count of the requests
they run (a client that calls an engine's port directly is counted by the engine, never turned
away). The node shows `draining` until nothing is in flight, then drained (`drain.state`); an off
node stays off. The drain is kept in the node's record, so it outlives a restart of the
controller; Undrain ends it. Until Replicas (one engine per deployment) "elsewhere" is a route's
fallback; without one a new request waits for Retry-After.

RESTART, REBOOT, UPDATE go to the node's TandemNode agent: an engine's restart
(`/node/engines/{id}/restart`, refused 409 `not_managed` for an engine TandemNode did not start),
the machine's reboot and update (`/node/maintenance/*`, proxy/nodemaint.py: set up by the node's
owner, else 409; simulated on a virtual node). A node read without its agent (this machine
in-process, a record not paired) has none of them.

FORGET is SEC-4's release (`DELETE /v1/fleet/nodes/{id}`, proxy/fleettrust.py): both pins, the
record and its history here go; TandemNode, its engines and the weights stay on the node.
RELEASE-LEASE is for a rented node (`lease_end` set): weights and caches wiped, keys removed and
TandemNode uninstalled before the lease ends. It comes with rental support: 409 `not_rented` for
an owned node, 501 `not_built` for a rented one until then.

LINK TEST of a LAN link to a node's agent, from the controller's machine: round trips of
`/node/linktest/echo` and a bandwidth floor (`/node/linktest/sink`, TCP through the agent's
mTLS), kept per link in the fleet store and said as a `link` event. The controller's own node has
no wire to test; a virtual node on the controller's machine is timed over this machine's own
network stack, and the test says so.
"""

from __future__ import annotations

import math
import secrets
import threading

from proxy import linktest, nodesource
from proxy.relay import DRAIN_RETRY_S

CONTRACT = "1.0"
CONFIRM_S = 300.0
DESTRUCTIVE = ("restart-engine", "reboot", "update", "forget", "release-lease")
ACTIONS = ("drain", "undrain") + DESTRUCTIVE
LINK_BYTES = 64 << 20
LINK_STREAMS = 4
LINK_RTT_N = 10


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


class Ops:
    def __init__(self, fleet):
        self.fleet = fleet
        self._tokens: dict[str, tuple[str, str, float]] = {}
        self._lock = threading.Lock()
        self._drained: dict[str, str] | None = None   # backend id -> its drained node's alias
        self._said: set[str] = set()             # drained nodes whose `drained` event is out

    # ------------------------------------------------------------------ the gateway's view
    def drain_of(self, bid: str) -> str | None:
        """The alias of the drained node `bid` runs on, None when it takes requests. The map is
        made at the first question (a drain kept across a restart holds from the first request),
        then at every sample and every drain."""
        if self._drained is None:
            with self._lock:
                if self._drained is None:
                    self.refresh()
        return self._drained.get(bid)

    def refresh(self) -> None:
        """The backends of the drained nodes for the gateway, and one `drained` event when a
        draining node has nothing left in flight."""
        f = self.fleet
        out = {}
        f.ensure_local()
        for row, src in f.sources():
            if not row["drain"]:
                self._said.discard(row["id"])
                continue
            for p in self._probes(src):
                out[p.id] = row["alias"]
            if row["id"] not in self._said and self.inflight(src) == 0:
                self._said.add(row["id"])
                f.store.add_event("node", row["id"], f"{row['alias']} is drained",
                                  "nothing in flight: it can be updated or rebooted")
        self._drained = out

    @staticmethod
    def _probes(src) -> list:
        return src.engines() if hasattr(src, "engines") else []

    def inflight(self, src) -> int:
        """The requests running on a node's engines now: the gateway's own, or the engines'
        count when it is higher (requests sent to an engine's port directly)."""
        f = self.fleet
        bids = [p.id for p in self._probes(src)]
        gateway = sum(f.svc.relay.inflight.count(b) for b in bids)
        engine = 0.0
        k = math.ceil(f.clock() / 2)
        with f._lock:
            for b in bids:
                r = f.rings.get(("backend", b, "req"))
                got = r and (r.slot(2, k) or r.slot(2, k - 1) or r.slot(2, k - 2))
                if got and got[1]:
                    engine += got[2]
        return max(gateway, int(round(engine)))

    def drain_view(self, row: dict, src) -> dict | None:
        d = row["drain"]
        if not d:
            return None
        n = self.inflight(src)
        return {"state": "draining" if n else "drained", "since": d.get("since"),
                "by": d.get("by"), "inflight": n}

    # ------------------------------------------------------------------ plans
    def plan(self, nid: str, action: str, now: float, engine: str | None = None) -> dict:
        return self._plan(nid, action, now, engine)[0]

    def _plan(self, nid: str, action: str, now: float, engine: str | None) -> tuple[dict, str]:
        """(the plan, the error kind of its `blocked`)."""
        from proxy.fleet import FleetError
        f = self.fleet
        row = f._node_row(nid)
        if action not in ACTIONS:
            raise FleetError(404, f"no action {action} (one of {', '.join(ACTIONS)})")
        src = f.source(row)
        p = {"contract_version": CONTRACT, "node": nid, "action": action, "title": "",
             "allowed": True, "blocked": None, "destructive": action in DESTRUCTIVE,
             "impact": [], "removes": [], "keeps": [], "engines": [], "job": None,
             "confirm": None, "expires_at": None}
        kind = getattr(self, "_plan_" + action.replace("-", "_"))(p, row, src, engine)
        if p["allowed"] and p["destructive"]:
            token = secrets.token_urlsafe(18)
            with self._lock:
                self._tokens = {k: v for k, v in self._tokens.items() if v[2] > now}
                self._tokens[token] = (nid, action, now + CONFIRM_S)
            p["confirm"], p["expires_at"] = token, round(now + CONFIRM_S, 3)
        return p, kind or "conflict"

    def _deployments(self, nid: str) -> list[dict]:
        return [d for d in self.fleet.deployments() if nid in d["members"]]

    def _elsewhere(self, d: dict, nid: str) -> str | None:
        """Where a deployment's new requests go while its node takes none: the label of the
        first fallback of its route on another node that takes requests, else None."""
        f = self.fleet
        try:
            route = f.svc.table.resolve(d["api_id"])
        except Exception:                                            # noqa: BLE001
            return None
        mine = {p.id for row, src in f.sources() if row["id"] == nid for p in self._probes(src)}
        for bid, _ in route.candidates()[1:]:
            if bid in mine or self.drain_of(bid) is not None:
                continue
            b = f.svc.table.backend_for(bid)
            if b is not None:
                return b.get("label") or bid
        return None

    def _stops(self, p: dict, row: dict, src, what: str) -> None:
        """The impact of an action that stops what the node serves."""
        a = row["alias"]
        deps = self._deployments(row["id"])
        p["impact"] += [f"{d['name']} stops on {a} {what}" for d in deps] or \
            [f"{a} serves nothing through this gateway now"]
        n = self.inflight(src)
        if n:
            p["impact"].append(f"{_plural(n, 'request')} in flight on {a} are cut off: drain it "
                               f"first to let them finish")

    def _agent(self, p: dict, row: dict, src) -> str | None:
        """An action that needs the node's agent: blocked without one (its error kind)."""
        if isinstance(src, nodesource.AgentNode):
            return None
        p["allowed"] = False
        p["blocked"] = (f"{row['alias']} is read without its TandemNode agent: start the agent "
                        f"(tandem node serve) for this action" if row["endpoint"] == "local"
                        else src.status()[1])
        return "no_agent"

    def _plan_drain(self, p, row, src, _):
        a = row["alias"]
        p["title"] = f"Drain {a}"
        if row["drain"]:
            p.update(allowed=False, blocked=f"{a} is drained already: Undrain ends it")
        n = self.inflight(src)
        p["impact"].append(f"{_plural(n, 'request')} in flight on {a} finish" if n else
                           f"nothing is in flight on {a}: it is drained at once")
        for d in self._deployments(row["id"]):
            to = self._elsewhere(d, row["id"])
            p["impact"].append(f"{d['name']}: new requests go to {to}, its route's fallback" if to
                               else f"{d['name']}: new requests wait, the gateway answers 503 "
                                    f"with Retry-After {DRAIN_RETRY_S} s until {a} takes them "
                                    f"again")
        p["keeps"] += [f"everything on {a} keeps running: only new requests through this gateway "
                       f"stop; a client that calls an engine's port directly is not turned away",
                       "the drain lasts until Undrain, across a restart of the controller"]

    def _plan_undrain(self, p, row, src, _):
        a = row["alias"]
        p["title"] = f"Undrain {a}"
        if not row["drain"]:
            p.update(allowed=False, blocked=f"{a} is not drained")
        p["impact"].append(f"new requests go to {a} again")

    def _plan_restart_engine(self, p, row, src, engine):
        a = row["alias"]
        p["title"] = f"Restart the engine on {a}"
        if self._agent(p, row, src):
            return "no_agent"
        engines = src.agent_engines()
        p["engines"] = [{"id": e.get("id"), "kind": e.get("kind"), "port": e.get("port"),
                         "managed": bool(e.get("managed")), "state": e.get("state"),
                         "job": e.get("job")} for e in engines if isinstance(e.get("id"), str)]
        if not p["engines"]:
            p.update(allowed=False, blocked=f"{a} runs no engine TandemNode knows of")
            return
        pick = next((e for e in p["engines"] if e["id"] == engine), None) if engine else \
            next((e for e in p["engines"] if e["managed"]), p["engines"][0])
        if pick is None:
            p.update(allowed=False, blocked=f"{a} has no engine {engine}")
            return
        p["job"] = pick["job"]
        if not pick["managed"]:
            p.update(allowed=False, blocked=f"this engine ({pick['id']}, port {pick['port']}) is "
                                            f"not managed by TandemNode: it was started outside "
                                            f"TandemNode, so TandemNode never stops or starts it")
            return "not_managed"
        p["engines"] = [pick] + [e for e in p["engines"] if e is not pick]
        self._stops(p, row, src, "until the engine answers its health check again")
        p["keeps"].append("the weights stay on the disk; a warm page cache makes the start short")

    def _machine(self, p, row, src, action: str) -> str | None:
        a = row["alias"]
        if self._agent(p, row, src):
            return "no_agent"
        m = src.maintenance()
        if m is None:
            p.update(allowed=False, blocked=f"{a}'s TandemNode agent has no {action} (an older "
                                            f"agent: update it first)")
            return "not_managed"
        p["job"] = m.get("job") if (m.get("job") or {}).get("action") == action else None
        st = m.get(action) or {}
        if not st.get("managed"):
            p.update(allowed=False, blocked=st.get("why") or f"{action} is not set up on {a}")
            return "not_managed"
        if st.get("simulated"):
            p["impact"].append(f"{a} is a virtual node: the {action} is simulated, the machine "
                               f"under it is not {'rebooted' if action == 'reboot' else 'updated'}")

    def _plan_reboot(self, p, row, src, _):
        a = row["alias"]
        p["title"] = f"Reboot {a}"
        kind = self._machine(p, row, src, "reboot")
        if p["allowed"]:
            self._stops(p, row, src, "until it is up again")
            p["impact"].append(f"{a} does not answer while it starts: a few minutes")
            p["keeps"].append(f"the record, the pairing and the drain: {a} rejoins by its id")
        return kind

    def _plan_update(self, p, row, src, _):
        a = row["alias"]
        p["title"] = f"Update {a}"
        kind = self._machine(p, row, src, "update")
        if p["allowed"]:
            v = src.version() or "an unknown version"
            p["impact"].append(f"{a} runs TandemLLM {v}; this controller runs "
                               f"{self.fleet.svc.engine_version or 'tandem-app'}")
            self._stops(p, row, src, "while its engine restarts with the update")
            p["keeps"].append("the weights, the record and the pairing")
        return kind

    def _plan_forget(self, p, row, src, _):
        a = row["alias"]
        p["title"] = f"Forget {a}"
        if row["endpoint"] == "local":
            p.update(allowed=False, blocked=f"{a} is the machine this controller runs on: it is "
                                            f"its own node and cannot be forgotten")
            return
        paired = (row["trust"] or {}).get("kind") == "mtls"
        p["removes"] += ([f"this controller's pin of {a}'s certificate, and {a}'s pin of this "
                          f"controller (it can be paired again: tandem node pair)"] if paired
                         else [f"the record of {a}: it was never paired"])
        p["removes"] += [f"{a}'s record here: alias, colour, icon, tags and the drain",
                         f"{a}'s metric history on this controller"]
        deps = ", ".join(d["name"] for d in self._deployments(row["id"]))
        p["keeps"] += [f"TandemNode stays installed and running on {a}, without an owner",
                       f"its engines keep running{': ' + deps if deps else ''}",
                       f"weights and caches stay on {a}'s disk",
                       f"the event log's lines about {a}"]
        if paired and src.status()[0] == "off":
            p["impact"].append(f"{a} does not answer: it is forgotten here only, and keeps its "
                               f"pin until `tandem node reset` on it")

    def _plan_release_lease(self, p, row, src, _):
        a = row["alias"]
        p["title"] = f"Release {a}"
        if row["lease_end_ms"] is None:
            p.update(allowed=False, blocked=f"{a} is not rented: Forget removes an owned Spark")
            return "not_rented"
        p.update(allowed=False, blocked="releasing a rented Spark comes with rental support: "
                                        "until then, release it with the provider")
        p["removes"] += [f"the weights and caches on {a}, wiped", f"the node certificate, this "
                         f"controller's pin and the app's SSH key on {a}",
                         f"TandemNode, uninstalled from {a}", f"{a}'s record and its history "
                                                              f"here"]
        p["keeps"].append(f"nothing of TandemApp stays on {a}; the lease ends with the provider")
        self._stops(p, row, src, "for good")
        return "not_built"

    # ------------------------------------------------------------------ actions
    def _spend(self, nid: str, action: str, token, now: float) -> None:
        from proxy.fleet import FleetError
        if not token:
            raise FleetError(428, f"{action} needs its plan's confirm: GET the plan "
                                  f"(/v1/fleet/nodes/{nid}/actions/{action}) and send its "
                                  f"`confirm`", "confirm_required")
        with self._lock:
            got = self._tokens.pop(token, None) if isinstance(token, str) else None
        if got is None or got[:2] != (nid, action) or got[2] <= now:
            raise FleetError(409, "that confirm is spent, too old or another action's: read the "
                                  "plan again", "confirm_invalid")

    def act(self, nid: str, action: str, body, who: str, now: float) -> dict:
        from proxy.fleet import FleetError
        f = self.fleet
        if body is None:
            body = {}
        if not isinstance(body, dict) or any(k not in ("confirm", "engine") for k in body):
            raise FleetError(400, "the body is {confirm?, engine?}")
        p, kind = self._plan(nid, action, now, body.get("engine"))
        if p["confirm"] is not None:                    # this call's own plan: never sent out
            with self._lock:
                self._tokens.pop(p["confirm"], None)
        if not p["allowed"]:
            raise FleetError(501 if kind == "not_built" else 409, p["blocked"], kind)
        if action in DESTRUCTIVE:
            self._spend(nid, action, body.get("confirm"), now)
        row = f._node_row(nid)
        a = row["alias"]
        out = {"contract_version": CONTRACT, "node": nid, "action": action, "state": "done",
               "text": "", "job": None, "at": round(now, 3)}
        if action == "drain":
            f.cfg.update_node(nid, {"drain": {"since": round(now, 3), "by": who}}, who=who)
            n = self.inflight(f.source(row))
            self._said.discard(nid)
            f.store.add_event("node", nid, f"{a} drains: no new requests",
                              f"{_plural(n, 'request')} in flight · by {who}")
            self.refresh()
            out["text"] = f"{a} takes no new requests; {_plural(n, 'request')} in flight finish"
        elif action == "undrain":
            f.cfg.update_node(nid, {"drain": None}, who=who)
            f.store.add_event("node", nid, f"{a} takes new requests again", f"by {who}")
            self.refresh()
            out["text"] = f"{a} takes new requests again"
        elif action == "forget":
            got = f._trusting(lambda t: t.release(nid, False, who))
            self.refresh()
            out["text"] = f"{a} forgotten; its side: {got['node']}"
        else:
            src = f.source(row)
            eid = p["engines"][0]["id"] if action == "restart-engine" else None
            path = f"/node/engines/{eid}/restart" if eid else f"/node/maintenance/{action}"
            st, doc = src.post(path)
            if st != 202:
                err = doc.get("error") or {}
                raise FleetError(st if st in (409, 404, 502) else 502,
                                 err.get("message") or f"{a}'s agent answered HTTP {st}",
                                 err.get("type") or "unreachable")
            what = f"the engine {eid} restarts" if eid else f"{action}s"
            f.store.add_event("update" if action == "update" else "engine"
                              if action == "restart-engine" else "node", nid,
                              f"{a}: {what}{' (simulated)' if doc.get('simulated') else ''}",
                              f"by {who}")
            out.update(state="running", job=doc,
                       text=f"{a} {what}" if action != "restart-engine" else f"on {a}, {what}")
        f.log(f"[fleet] {action} {a} ({nid}) by {who}")
        return out

    # ------------------------------------------------------------------ link tests
    def tests(self) -> dict:
        return self.fleet.store.link_tests()

    def test_link(self, lid: str, who: str, now: float) -> dict:
        from proxy.fleet import FleetError
        f = self.fleet
        _, links = f.links_infra()
        link = next((x for x in links if x["id"] == lid), None)
        if link is None:
            raise FleetError(404, f"no link {lid}")
        nid = next((x for x in (link["a"], link["b"]) if f.cfg.node(x)), None)
        row = f.cfg.node(nid) if nid else None
        if link["kind"] != "lan" or row is None:
            raise FleetError(409, "only a LAN link to a node's agent is tested from here: the "
                                  "internet link has no agent at its far end", "no_agent")
        src = f.source(row)
        if row["endpoint"] == "local":
            raise FleetError(409, f"the controller runs on {row['alias']}: there is no wire "
                                  f"between them to test", "no_wire")
        if not isinstance(src, nodesource.AgentNode) or src.status()[0] == "off":
            raise FleetError(409, f"{row['alias']} does not answer: "
                                  f"{src.status()[1]}", "unreachable")
        rtt = linktest.rtt(src.client, LINK_RTT_N)
        bw = linktest.bandwidth(src.client, None, None, LINK_BYTES, LINK_STREAMS)
        here = f.locality.is_local(src.endpoint)
        notes = ["bandwidth is TCP through the agent's mTLS: a floor of what the link carries"]
        if here:
            notes.insert(0, f"{row['alias']} runs on the controller's machine (a virtual node): "
                            f"these are this machine's own network stack, not a wire")
        test = {"gbps": bw.get("gbps"), "at": round(now, 3),
                "rtt_ms": None if rtt is None else rtt["median_ms"],
                "note": bw.get("error") or "; ".join(notes)}
        f.store.set_link_test(lid, test, now)
        res = f"{test['gbps']} Gb/s" if test["gbps"] is not None else "no bandwidth"
        f.store.add_event("link", nid, f"Link test controller ↔ {row['alias']}: {res}"
                          + (f", RTT {test['rtt_ms']} ms" if test["rtt_ms"] is not None else ""),
                          f"{test['note']} · by {who}")
        _, links = f.links_infra()
        return {"contract_version": CONTRACT,
                "link": next(x for x in links if x["id"] == lid),
                "result": {"rtt": rtt, "bandwidth": bw, "notes": notes}}
