"""Each node's Setup Guide through TandemApp's controller (VIS-252): the browser asks the controller,
the controller asks the node's TandemNode agent, the agent its engine. The browser never calls a node.

    relay = SetupRelay(fleet)                    one per Fleet (fleet.setup)
    *    /v1/fleet/nodes/{id}/setup/<route>      the node's Setup Guide: its engine's
                                                 /v1/setup/<route>, as the agent relays it
                                                 (/node/setup/<route>; POST estimate is the agent's
                                                 own formula when no engine serves the guide); an
                                                 event stream (a job's log, Wren's answer) as it
                                                 comes. The controller's own machine read without
                                                 its agent: its engine's /v1/setup/<route>
    GET  /v1/fleet/nodes/{id}/setup-confirm/<route>[?model=&profile=|?profiles=|?level=]
                                                 what a write does on that node, whether it may,
                                                 and its confirm (proxy/fleet.py routes it)

THE WRITES change what the node serves or holds: `apply` (the engine restarts on a new choice),
`remove` (weights off its disk), `reset` (a reset level), `jobs/{id}/retry` (a failed job again: a
restart) and `assistant/load` (Wren takes memory on the node). Each needs the confirm of its plan
as `?confirm=`: single use, CONFIRM_S, for that node, that route and what the plan was read for
(apply: the selection's model and profile; remove: the profiles; reset: the level), so the body
sent is the one the plan named. Without it 428 `confirm_required`; spent, stale, another node's or
another body's 409 `confirm_invalid`. A dry run (`dry_run: true`) is a read. Every other POST
(estimate, plan, community/sync, assistant/unload, assistant/chat) and every GET passes as it is.

RIGHTS. Every route here is the `setup` rule of server/auth.py, the rule the engine's own
/v1/setup has (proxy/app.py); a POST must be a same-origin JSON request (server/setup_api.py
same_origin_json), checked here because the agent passes no Origin on. A write that the node took
(below 400) is an `engine` event naming the node and who sent it.
"""

from __future__ import annotations

import http.client
import json
import secrets
import threading
import urllib.parse

from proxy import nodesource

CONTRACT = "1.0"
CONFIRM_S = 300.0
RELAY_TIMEOUT_S = 120.0
WRITES = ("apply", "remove", "reset", "retry", "assistant/load")


def write_of(method: str, sub: list[str], body) -> str | None:
    """The write a request is (one of WRITES), else None: a read, a dry run, a free POST."""
    if method != "POST":
        return None
    w = "retry" if len(sub) == 3 and sub[0] == "jobs" and sub[2] == "retry" else "/".join(sub)
    if w not in WRITES:
        return None
    if isinstance(body, dict) and body.get("dry_run") is True:
        return None
    return w


def bound(write: str, src: dict) -> str:
    """What a confirm is for besides the node and the route: from the plan's query, or the body."""
    if write == "apply":
        sel = src.get("selection") if isinstance(src.get("selection"), dict) else src
        return f"{sel.get('model') or ''}/{sel.get('profile') or ''}"
    if write == "remove":
        p = src.get("profiles")
        p = p.split(",") if isinstance(p, str) else p if isinstance(p, list) else []
        return ",".join(sorted(str(x) for x in p if x))
    if write == "reset":
        return str(src.get("level") or "")
    return ""


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


class SetupRelay:
    def __init__(self, fleet):
        self.fleet = fleet
        self._tokens: dict[str, tuple[str, str, str, float]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ the plan
    def plan(self, nid: str, route: str, q: dict, now: float) -> dict:
        from proxy.fleet import FleetError
        f = self.fleet
        row = f._node_row(nid)
        sub = [x for x in route.split("/") if x]
        w = write_of("POST", sub, None)
        if w is None:
            raise FleetError(404, f"{route or '(none)'} is no Setup Guide write: one of apply, "
                                  f"remove, reset, jobs/<id>/retry, assistant/load")
        a = row["alias"]
        src = f.source(row)
        p = {"contract_version": CONTRACT, "node": nid, "route": "/".join(sub), "title": "",
             "allowed": True, "blocked": None, "impact": [], "keeps": [], "confirm": None,
             "expires_at": None}
        what = bound(w, q)
        st, text = src.status()
        if st == "off" or isinstance(src, nodesource.Unreachable):
            p.update(allowed=False, blocked=f"{a} does not answer: {text}")
        if w in ("apply", "retry"):
            if w == "apply":
                model, profile = what.split("/", 1)
                p["title"] = f"Apply the Setup Guide on {a}"
                p["impact"].append(f"{a}'s engine restarts serving {model or 'the chosen model'}"
                                   f"{' with ' + profile if profile else ''}: what it serves is "
                                   f"away while it loads, a minute or more")
            else:
                p["title"] = f"Run job {sub[1]} again on {a}"
                p["impact"].append(f"the failed job {sub[1]} runs again on {a} from its "
                                   f"selection: its engine restarts")
            self._stops(p, nid, a)
            p["keeps"] += [f"the weights on {a}'s disk; the Setup Guide's review lists every "
                           f"serve.env change", "each deployment's record: a member serves "
                                                "again once its engine is up and warm"]
        elif w == "reset":
            p["title"] = f"Reset level {what or '?'} on {a}"
            if what == "2":
                p["impact"].append(f"{a}'s engine restarts with the shipped configuration")
                self._stops(p, nid, a)
            else:
                p["impact"].append(f"what level {what or '?'} lists is removed from {a} (the "
                                   f"Setup Guide shows every path and its size first)")
            p["keeps"].append(f"TandemNode on {a}, its pairing and this controller's records")
        elif w == "remove":
            names = [x for x in what.split(",") if x]
            p["title"] = f"Remove weights on {a}"
            if not names:
                p.update(allowed=False, blocked="name the profiles to remove (?profiles=a,b)")
            for name in names:
                p["impact"].append(f"{name} is removed from {a}'s disk")
                used = f.weights._in_use(nid, name)
                if used:
                    p.update(allowed=False, blocked=f"{', '.join(used)} serves {name} on {a}: "
                                                    f"stop it first")
            p["keeps"].append(f"{'these packs' if len(names) > 1 else 'the pack'} on every "
                              f"other node, and every other pack on {a}")
        else:
            p["title"] = f"Wake Wren on {a}"
            p["impact"].append(f"Wren, the Setup Guide's helper, loads on {a}: it takes memory "
                               f"there while the guide is open (the engine refuses it when too "
                               f"little would stay free)")
            p["keeps"].append(f"what {a} serves keeps running")
        if p["allowed"]:
            token = secrets.token_urlsafe(18)
            with self._lock:
                self._tokens = {k: v for k, v in self._tokens.items() if v[3] > now}
                self._tokens[token] = (nid, w, what, now + CONFIRM_S)
            p["confirm"], p["expires_at"] = token, round(now + CONFIRM_S, 3)
        return p

    def _stops(self, p: dict, nid: str, a: str) -> None:
        """The deployments a restart of the node's engine takes away, and where their requests
        go meanwhile."""
        deps = [d for d in self.fleet.deployments() if nid in d["members"]
                and d["status"] != "stopped"]
        if not deps:
            p["impact"].append(f"{a} serves nothing through this gateway now")
        for d in deps:
            # a Split needs every stage: no other member takes its requests
            other = None if d["type"] == "split" else next(
                (m["alias"] for m in d.get("member_states") or []
                 if m["node"] != nid and m["routable"]), None)
            p["impact"].append(f"{d['name']}: new requests go to {other}, another member" if other
                               else f"{d['name']} does not answer until {a}'s engine is back")
        n = self.fleet.ops.inflight(self.fleet.source(self.fleet._node_row(nid)))
        if n:
            p["impact"].append(f"{_plural(n, 'request')} in flight on {a}: the engine refuses "
                               f"the restart while requests run (drain {a} first)")

    def spend(self, nid: str, write: str, token, body, now: float) -> None:
        from proxy.fleet import FleetError
        if not token:
            raise FleetError(428, f"{write} on a node needs its plan's confirm: GET "
                                  f"/v1/fleet/nodes/{nid}/setup-confirm/{write} and send its "
                                  f"`confirm` as ?confirm=", "confirm_required")
        with self._lock:
            got = self._tokens.pop(token, None) if isinstance(token, str) else None
        what = bound(write, body if isinstance(body, dict) else {})
        if got is None or got[:3] != (nid, write, what) or got[3] <= now:
            raise FleetError(409, "that confirm is spent, too old, another node's or for another "
                                  "choice: read the plan again", "confirm_invalid")

    # ------------------------------------------------------------------ the relay
    def open(self, nid: str, method: str, sub: list[str], query: str, raw: bytes | None,
             headers: dict, confirm, who: str):
        """The node's answer: (status, content type, extra headers, connection, response), the
        response unread. A write spends its confirm first."""
        from proxy.fleet import FleetError
        f = self.fleet
        row = f._node_row(nid)
        body = None
        if raw:
            try:
                body = json.loads(raw)
            except ValueError as exc:
                raise FleetError(400, f"bad json: {exc}")
        w = write_of(method, sub, body)
        if w is not None:
            self.spend(nid, w, confirm, body, f.clock())
        src = f.source(row)
        path = "/".join(urllib.parse.quote(x, safe="") for x in sub)
        if isinstance(src, nodesource.AgentNode):
            conn = src.client.connection(timeout=RELAY_TIMEOUT_S)
            target = f"/node/setup/{path}"
            h = src.client.headers()
        elif isinstance(src, nodesource.LocalNode):
            eng = next((b for b in f.engine_backends() if b["kind"] == "tandemllm"), None)
            if eng is None:
                raise FleetError(503, f"{row['alias']}: no TandemLLM engine on this machine serves "
                                      f"the Setup Guide", "no_engine")
            u = urllib.parse.urlsplit(eng["url"])
            conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=RELAY_TIMEOUT_S)
            target, h = f"/v1/setup/{path}", {}
        else:
            raise FleetError(503, f"{row['alias']} does not answer: {src.status()[1]}",
                             "unreachable")
        h = dict(h, **{k: v for k, v in headers.items() if v})
        try:
            conn.request(method, target + (f"?{query}" if query else ""), body=raw or None,
                         headers=h)
            sock = conn.sock
            r = conn.getresponse()
            r.sock = sock
        except OSError as exc:
            conn.close()
            raise FleetError(502, f"{row['alias']}'s agent does not answer: "
                                  f"{type(exc).__name__}: {exc}"[:200], "unreachable")
        if w is not None:
            a = row["alias"]
            what = bound(w, body or {})
            f.log(f"[fleet] {who} setup {w} on {a} -> {r.status}")
            if r.status < 400:
                f.store.add_event("engine", nid, f"Setup Guide on {a}: {w.replace('/', ' ')}"
                                                 f"{' ' + what if what else ''}",
                                  f"HTTP {r.status} · by {who}")
        extra = tuple((k, r.getheader(k)) for k in ("Retry-After",) if r.getheader(k))
        return r.status, r.getheader("Content-Type") or "application/json", extra, conn, r
