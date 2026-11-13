"""Deployments behind one API (SRV-731): what the fleet serves, each deployment one record, and
the gateway's route of a request to the node that serves it.

    deps = Deployments(fleet)                    one per Fleet (fleet.deps)
    deps.all()                                   every deployment: the records, then the derived
                                                 Singles of what no record covers (below)
    deps.place(model, body, headers)             the gateway's route of a request (proxy/route.py
                                                 Route with its deployment and the members in
                                                 order), or None for a model no record names
                                                 (the table's route instead while an engine
                                                 speaks another engine API major)
    deps.listing(created)                        /v1/models: each deployment once
    deps.locate(backend id, upstream model)      (node id, alias, deployment) for the ledger row
    deps.handle(method, parts, q, body, who, now)   /v1/fleet/deployments[/...]

ROUTES (contracts/fleet-v1):

    GET    /v1/fleet/deployments                       every deployment with its members' states
    POST   /v1/fleet/deployments                       a record: {model, type, members, pack?, name?,
                                                       id?, api_id?, ctx?, stages?, role?, role_of?,
                                                       affinity?, state?}
    GET    /v1/fleet/deployments/{id}
    PATCH  /v1/fleet/deployments/{id}                  {name?, api_id?, ctx?, pack?, type?,
                                                       members?, stages?, role?, role_of?,
                                                       affinity?}
    DELETE /v1/fleet/deployments/{id}                  the record goes; its engines keep running
                                                       and show as derived Singles again
    GET    /v1/fleet/deployments/{id}/actions/{action}[?to=&from=&engine=]   what start, stop or
                                                       move would do, whether it may, the confirm
    POST   /v1/fleet/deployments/{id}/actions/{action}  {confirm?, to?, from?, engine?}: the job

TYPES. `single`: one member. `replicas`: two or more members that serve one model at one release,
each conversation on one member (proxy/affinity.py, SRV-467: the engine's own conversation key, a
sticky table corrected by the member that served the turn, a rendezvous hash for a new key, the
fewest requests in flight without a key). `role`: one member, a helper (`role`: what it does) of
the deployment `role_of` names (SRV-465). `split`: two or four members in pipeline order with a
stage each ({node, layers, gib}); modelled, planned (proxy/placement.py) and recorded, never
started: its start answers 501 `not_built` and the gateway 501 until ENG-307 builds the pipeline
split.

A MEMBER is {node, engine, backend, model}: the node, the engine on it (its TandemNode agent's
engine id), the backend through which this gateway reaches that engine (proxy/config.py backends,
made for the engine when it has none: `http://<node address>:<port>/v1`) and the id that backend
serves the model under. A member's state: `serving` (its backend answers and, after a start, a
move or a rejoin, its warm-up answered one token), `starting`, `down`, `draining` (its node is
drained), `leaving` (a move takes it out: it takes no new request), `mismatch` (a Replicas member
on another release, or with other weights, than the first serving member: G15 from SRV-467, on
what the controller reads; with engine API 1.1 its identity block differs in a field other than
`max_len`, or the output canary gave other tokens: SRV-741, proxy/canary.py; it takes no request),
`off` (its node does not answer), `gone` (its node
left the fleet). Only `serving` takes requests. A member whose engine speaks another engine API
major (its agent's engine view, proxy/engineapi.py) is `down` with the agent's reason, which names
the major this TandemApp needs; the gateway sends such an engine no request, not as a fallback and
not for a model no record names (a derived Single's or a routing rule's): 503 `no_member`.

THE STATUS of a deployment: `serving` when every member serves, `degraded` when some do, `starting`
while its start runs or a member starts, `down` when none serves, `stopped` when the record says
so (and a Split: not built). The record's `state` is what should be (serving or stopped); the
status is what is.

DERIVED SINGLES. A model an engine on a node serves that no record covers (a backend and the id it
serves) is a Single derived from what that engine serves: not a record, `record` false, its id the
model's public id here (`<backend id>/<id>` when a record or another engine has the same one,
as /v1/models has it). So a box whose engine nobody manages (the one on :8000) shows as a Single
without a record, and a record made over it later covers it. Fleet updates, the version rule and
drains read Replicas from the records only; two derived Singles of one model id stay two.

THE GATEWAY. `place` routes a request for a record's `api_id` (or its id): the members in order
(a Replicas' conversation home first, then the other serving members as fallbacks, then the
backend's own fallbacks); a drained, leaving, mismatched or down member is never first. The relay
(proxy/relay.py) tries them on a failure before any byte, as it does a route's fallbacks, and puts
`X-Tandem-Node` (the alias) and `X-Tandem-Deployment` (the id) on the answer. Members whose
engines say different windows (`max_len`, SRV-741): a request whose prompt (counted by the first
member's engine, `POST /v1/tokenize`) plus `max_tokens` exceeds a member's window goes to one that
fits, else 400 `context_length_exceeded` with the largest window. `POST /v1/cache/peek`,
`/prewarm` and `/release` for a deployment's model go to the member that holds the conversation
(`cache_route`) and leave its home as it is. A constrained request
(SRV-467's G20: `structured_outputs`, a `response_format` other than text, a grammar or guided
field, a forced `tool_choice`) moves on only when the first member never took it. A stopped
deployment answers 503 `deployment_stopped`, a Split 501 `not_built`.

JOBS (`fleet_jobs`, kind `deploy`; one per deployment at a time, 409 `busy`):

    start   every member's engine started through its agent (one TandemNode did not start: started
            only when it is up already, else refused), its backend's health up, then the warm-up:
            one token from the model (WARM_WAIT_S). A Split: 501 not_built. An engine on another
            engine API major fails the start at the agent's first such answer (start_failed).
    stop    the record stopped first (the gateway takes no new request for it), the requests in
            flight finished (DRAIN_WAIT_S), every member's engine stopped through its agent. A
            member TandemNode does not manage blocks the stop (the plan says so).
    move    {from, to}: the engine on `to` started and warmed, `to` joins the members, `from` turns
            `leaving` (new requests and conversations go to `to`), its requests in flight finish
            (DRAIN_WAIT_S), its engine stops (one TandemNode does not manage is left running, the job
            says so), `from` leaves the record. The API id never changes.

A controller that restarts during a job marks it failed (`interrupted`) at its next start.

TIMEOUTS: START_WAIT_S for an engine's start (the agent's own `start_timeout_s` applies on its
side), WARM_WAIT_S for the health and the one-token warm-up after it, DRAIN_WAIT_S for the
requests in flight on a stopping or leaving member. A fleet update's drain and rejoin
(proxy/fleetupdate.py) use their own: DRAIN_WAIT_S 600 s and REJOIN_WAIT_S 300 s, the rejoin's warm-up
inside it.
"""

from __future__ import annotations

import json
import math
import re
import secrets
import threading
import time
import urllib.error
import urllib.request

from proxy import affinity, canary as canary_mod, nodesource

CONTRACT = "1.0"
TYPES = ("single", "replicas", "split", "role")
STATES = ("serving", "stopped")
ACTIONS = ("start", "stop", "move")
DESTRUCTIVE = ("stop", "move")
AFFINITY = ("root", "own")
CONFIRM_S = 300.0
START_WAIT_S = 1800.0
WARM_WAIT_S = 300.0
DRAIN_WAIT_S = 600.0
POLL_S = 1.0
CTX_TTL_S = 1.0
WARM_TIMEOUT_S = 60.0
WARM_UA = "tandem-controller/warm"
LOG_MAX = 200
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
FIELDS = ("id", "name", "model", "pack", "type", "members", "stages", "api_id", "ctx", "role",
          "role_of", "affinity", "state")
PATCHABLE = ("name", "api_id", "ctx", "pack", "type", "members", "stages", "role", "role_of",
             "affinity")
# request fields that constrain the output (SRV-467's G20)
CONSTRAINED = ("structured_outputs", "guided_json", "guided_regex", "guided_choice",
               "guided_grammar", "grammar", "json_schema")
SELF = "self"                                   # SRV-467's member for this machine's engine
# VIS-254: a request pinned to one member (the Playground's A/B of a deployment's members)
PIN_HEADER = "X-Tandem-Pin"
# SRV-741: the engine routes the gateway calls on a member itself
TOKENIZE_PATH = "/v1/tokenize"
TOKENIZE_TIMEOUT_S = 5.0
CACHE_PATHS = ("/v1/cache/peek", "/v1/cache/prewarm", "/v1/cache/release")
CACHE_TIMEOUT_S = 30.0


class DeployError(Exception):
    """An error in the contract's shape: `status`, `kind` (the error type) and the words."""

    def __init__(self, status: int, kind: str, message: str):
        super().__init__(message)
        self.status, self.kind, self.message = status, kind, message


class Refused(Exception):
    """A request the gateway answers itself (a stopped deployment, a Split): an OpenAI error."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message

    def body(self) -> dict:
        return {"error": {"message": self.message, "type": "server_busy" if self.status == 503
                          else "invalid_request_error", "code": self.code}}


def _header(headers, name: str) -> str:
    try:
        return ((headers.get(name) if headers is not None else None) or "").strip()
    except Exception:                                              # noqa: BLE001
        return ""


def constrained(body) -> bool:
    """A request whose output is constrained: never replayed on another member once sent."""
    if not isinstance(body, dict):
        return False
    if any(body.get(k) is not None for k in CONSTRAINED):
        return True
    rf = body.get("response_format")
    if isinstance(rf, dict) and rf.get("type") not in (None, "text"):
        return True
    tc = body.get("tool_choice")
    return tc == "required" or isinstance(tc, dict)


def release(v: str | None) -> str | None:
    """A version's release (a local build label is the same release), None for none."""
    if not v:
        return None
    from proxy import fleetwatch
    return fleetwatch._release(v)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", (text or "").lower()).strip("-.")[:64]


class Deployments:
    def __init__(self, fleet, clock=None):
        self.fleet = fleet
        self.cfg, self.svc = fleet.cfg, fleet.svc
        self.clock = clock or fleet.clock
        self.sleep = time.sleep
        self._lock = threading.Lock()
        self._ctx: tuple[float, dict] | None = None
        self._sticky: dict[str, affinity.Sticky] = {}
        self._warm: dict[tuple, tuple[bool, str, float]] = {}  # (backend, model) -> verdict
        self._tokens: dict[str, tuple[str, str, float]] = {}
        self._running: set[str] = set()
        # (t, config generation, by backend, by (backend, model)): rebuilt every 5 s and at once
        # when the config changes (a backend made for a new member)
        self._index: tuple[float, int, dict, dict] | None = None
        self._interrupted = False
        # SRV-741: the members' identity blocks and the output canary (proxy/canary.py)
        self.identities = canary_mod.Identities(log=fleet.log)
        self.canary = canary_mod.Canary(self)

    # ------------------------------------------------------------------ what the fleet has
    def _context(self, max_age: float = 0.0) -> dict:
        """Every node with its source, status, engine probes (backend ids) and its agent's
        engines; cached `max_age` seconds (the gateway reads it per request)."""
        now = time.monotonic()
        got = self._ctx
        if got is not None and max_age and now - got[0] < max_age:
            return got[1]
        f = self.fleet
        f.ensure_local()
        nodes = {}
        for row, src in f.sources():
            agent = {}
            if isinstance(src, nodesource.AgentNode):
                agent = {e.get("id"): e for e in src.agent_engines()}
            st, text = src.status()
            nodes[row["id"]] = {"row": row, "src": src, "status": st, "text": text,
                                "probes": src.engines() if hasattr(src, "engines") else [],
                                "agent": agent}
        ctx = {"nodes": nodes, "by_backend": {p.id: nid for nid, n in nodes.items()
                                              for p in n["probes"]},
               "local": f._local_id}
        self._ctx = (now, ctx)
        return ctx

    def records(self) -> list[dict]:
        return self.cfg.deployments()

    def record(self, did: str) -> dict:
        rec = self.cfg.deployment(did)
        if rec is None:
            raise DeployError(404, "not_found", f"no deployment {did}")
        return rec

    # ------------------------------------------------------------------ members and status
    def _member(self, ctx: dict, m: dict, rec: dict) -> dict:
        """A record's member resolved against the fleet now: its node, engine, backend."""
        nid, bid = m.get("node"), m.get("backend")
        if bid == SELF:                          # a SRV-467 set's own engine: this machine's
            nid = ctx["local"]
            n = ctx["nodes"].get(nid) if nid else None
            bid = n["probes"][0].id if n and n["probes"] else None
        if nid is None and bid:
            nid = ctx["by_backend"].get(bid)
        n = ctx["nodes"].get(nid) if nid else None
        probe = next((p for p in n["probes"] if p.id == bid), None) if n else None
        eid = m.get("engine") or getattr(probe, "via", None)
        e = n["agent"].get(eid) if n and eid else None
        return {"node": nid, "alias": n["row"]["alias"] if n else (nid or "?"), "engine": eid,
                "backend": bid, "model": m.get("model") or rec["model"],
                "managed": bool(e and e.get("managed")), "_n": n, "_e": e}

    def _state(self, mv: dict, rec: dict) -> tuple[str, str]:
        n, e, bid = mv["_n"], mv["_e"], mv["backend"]
        if n is None:
            return "gone", "no longer in the fleet"
        if n["status"] == "off":
            return "off", n["text"]
        if mv["node"] in (rec.get("options") or {}).get("leaving", []):
            return "leaving", "moving away: no new requests"
        if n["row"]["drain"]:
            return "draining", f"{n['row']['alias']} is drained"
        if not bid or self.svc.table.backend_for(bid) is None:
            return "down", "no backend reaches its engine"
        why = _api_why(e)
        if why is not None:
            return "down", why
        h = self.svc.health.view(bid)
        w = self._warm.get((bid, mv["model"]))
        if h["state"] == "up" and w is not None:
            # this controller's own warm-up said it: newer than the agent's engine list
            return ("serving", "serving") if w[0] else ("starting", f"warming up: {w[1]}")
        job = (e or {}).get("job") or {}
        if job.get("state") == "running" and job.get("action") in ("start", "restart"):
            return "starting", f"its engine {mv['engine']} {job['action']}s"
        if h["state"] == "up":
            return "serving", "serving"
        if h["state"] == "down":
            return "down", f"down · {h['last_error'] or 'no answer'}"
        return "starting", "waiting for the first health check"

    def members(self, rec: dict, ctx: dict | None = None, canary: bool = True) -> list[dict]:
        """A record's members with their state and whether they take requests; a Replicas'
        identity check, then (unless `canary` is False: the canary's own round) its canary's
        verdicts."""
        ctx = ctx or self._context()
        out = []
        for m in rec["members"]:
            mv = self._member(ctx, m, rec)
            mv["state"], mv["text"] = self._state(mv, rec)
            mv["api_why"] = _api_why(mv["_e"])
            out.append(mv)
        if rec["type"] == "replicas":
            self._identity(out)
            self._full_identity(out)
            self.canary.observe(rec["id"], out)
            if canary:
                self._canary_verdicts(rec, out)
        for mv in out:
            mv["routable"] = mv["state"] == "serving"
        return out

    def _full_identity(self, ms: list[dict]) -> None:
        """SRV-741: each serving member's identity block against the first serving member's:
        every field but `max_len`; a member that differs is a mismatch that names the field. An
        engine without the route keeps the light check (`_identity`) alone."""
        for m in ms:
            got = self.identities.read(m, m["state"] == "serving")
            m["check"] = {"identity": got["state"], "identity_text": got["why"] or "read",
                          "field": None, "window": None, "canary": None, "canary_text": None,
                          "canary_at": None}
            m["_block"] = got["block"]
            w = (got["block"] or {}).get(canary_mod.WINDOW)
            m["check"]["window"] = w if isinstance(w, int) and not isinstance(w, bool) else None
        ref = next((m for m in ms if m["state"] == "serving"), None)
        if ref is None or ref["_block"] is None:
            return
        for m in ms:
            if m is ref or m["state"] != "serving" or m["_block"] is None:
                continue
            d = canary_mod.differs(ref["_block"], m["_block"])
            if d is None:
                m["check"]["identity_text"] = f"same identity as {ref['alias']}"
                continue
            field, a, b = d
            m["check"].update(identity="mismatch", field=field,
                              identity_text=f"{field}: {canary_mod.short(b)}, {ref['alias']} "
                                            f"{canary_mod.short(a)}")
            m["state"], m["text"] = "mismatch", (f"its identity differs from {ref['alias']}'s in "
                                                 f"{field}: {canary_mod.short(b)} vs "
                                                 f"{canary_mod.short(a)}")

    def _canary_verdicts(self, rec: dict, ms: list[dict]) -> None:
        """The canary's last verdict on each serving member: a `mismatch` takes it out of
        service until a later round passes; a `near_tie` is a warning only."""
        for m in ms:
            v = self.canary.verdict(rec["id"], m["backend"]) if m["backend"] else None
            if v is None:
                continue
            m["check"].update(canary=v["verdict"], canary_text=v["text"], canary_at=v["at"])
            if v["verdict"] == "mismatch" and m["state"] == "serving":
                m["state"], m["text"] = "mismatch", f"canary: {v['text']}"

    @staticmethod
    def _identity(ms: list[dict]) -> None:
        """G15 on what the controller reads: a serving Replicas member on another release, or
        with other weights, than the first serving one is a mismatch."""
        ref = next((m for m in ms if m["state"] == "serving"), None)
        if ref is None:
            return
        rv = release(ref["_n"]["src"].version())
        rw = ((ref["_e"] or {}).get("health") or {}).get("weights")
        for m in ms:
            if m is ref or m["state"] != "serving":
                continue
            v = release(m["_n"]["src"].version())
            w = ((m["_e"] or {}).get("health") or {}).get("weights")
            if rv and v and v != rv:
                m["state"], m["text"] = "mismatch", (f"runs {v}, {ref['alias']} {rv}: Replicas "
                                                     f"members run one release")
            elif rw and w and w != rw:
                m["state"], m["text"] = "mismatch", (f"serves the weights {w}, {ref['alias']} "
                                                     f"{rw}")

    def view(self, rec: dict, ctx: dict | None = None) -> dict:
        ms = self.members(rec, ctx)
        o = rec.get("options") or {}
        job = self.cfg.job("deploy", o.get("job")) if o.get("job") else None
        status, text = self._status(rec, ms, job)
        out = {"id": rec["id"], "name": rec["name"], "model": rec["model"], "pack": rec["pack"],
               "type": rec["type"], "members": [m["node"] or "?" for m in ms],
               "api_id": o.get("api_id") or rec["id"], "ctx": int(o.get("ctx") or 0),
               "status": status, "status_text": text, "record": True, "state": rec["state"],
               "managed": all(m["managed"] for m in ms) if ms else False,
               "member_states": [self._member_view(m) for m in ms],
               "job": _job_short(job)}
        if rec["type"] == "split":
            out["stages"] = rec["stages"]
        if rec["type"] == "role":
            if o.get("role_of"):
                out["role_of"] = o["role_of"]
            if o.get("role"):
                out["role"] = o["role"]
        if rec["type"] == "replicas":
            out["affinity"] = o.get("affinity") or "root"
            warn = [w for w in [self._wan_warning(ms)] if w]
            warn += [f"{m['alias']}: {m['check']['canary_text']}" for m in ms
                     if (m.get("check") or {}).get("canary") == "near_tie"]
            if warn:
                out["warn"] = " · ".join(warn)
        return out

    @staticmethod
    def _member_view(m: dict) -> dict:
        out = {"node": m["node"] or "?", "alias": m["alias"], "engine": m["engine"],
               "backend": m["backend"], "model": m["model"], "managed": m["managed"],
               "state": m["state"], "text": m["text"], "routable": m["routable"]}
        if m.get("check") is not None:
            out["check"] = dict(m["check"])
        return out

    @staticmethod
    def _wan_warning(ms: list[dict]) -> str | None:
        """Replicas over the LAN and a remote node: a failover crosses the internet."""
        far = [m["alias"] for m in ms if m["_n"] and m["_n"]["row"]["transport"] == "tunnel"]
        near = [m["alias"] for m in ms if m["_n"] and m["_n"]["row"]["transport"] != "tunnel"]
        if far and near:
            return (f"{', '.join(near)} on the LAN, {', '.join(far)} remote: each conversation "
                    f"stays on one member; a failover moves it across the internet")
        return None

    def _status(self, rec: dict, ms: list[dict], job: dict | None) -> tuple[str, str]:
        if rec["type"] == "split":
            return "stopped", ("Split: planned and recorded; it runs once the pipeline split "
                               "(ENG-307) is built")
        if rec["state"] == "stopped":
            up = [m["alias"] for m in ms if m["state"] == "serving"]
            return "stopped", "stopped" + (f" · its engine still answers on {', '.join(up)}"
                                           if up else "")
        if job is not None and job["state"] == "running" and job["action"] == "start":
            return "starting", f"start: {_last(job)}"
        live = [m for m in ms if m["state"] not in ("leaving",)]
        serving = [m for m in live if m["state"] == "serving"]
        if live and len(serving) == len(live):
            return "serving", "serving" if len(live) == 1 else \
                f"serving · {len(live)} of {len(live)} members"
        bad = next((m for m in live if m["state"] != "serving"), None)
        why = f"{bad['alias']}: {bad['text']}" if bad else "no member"
        if serving:
            return "degraded", f"{len(serving)} of {len(live)} members serving · {why}"
        if any(m["state"] == "starting" for m in live):
            return "starting", why
        return "down", why

    def all(self) -> list[dict]:
        """Every deployment: the records, then the derived Singles."""
        ctx = self._context()
        recs = self.records()
        out = [self.view(r, ctx) for r in recs]
        covered = set()
        for r in recs:
            for m in self.members(r, ctx):
                if m["backend"]:
                    covered.add((m["backend"], m["model"]))
        out += self._derived(ctx, covered, {d["api_id"] for d in out} | {d["id"] for d in out})
        self._reindex(ctx, out)
        return out

    def _derived(self, ctx: dict, covered: set, taken: set) -> list[dict]:
        """What an engine on a node serves that no record covers: a Single each."""
        now = self.clock()
        snap = self.svc.table.snapshot()["by_backend"]
        items = []
        for nid, n in ctx["nodes"].items():
            for p in n["probes"]:
                rows = snap.get(p.id) or {}
                h = self.svc.health.view(p.id)
                cards = p.cards(now) if p.kind == "tandemllm" else None
                if cards is not None:
                    got = [(c, rows.get(c["id"])) for c in cards]
                else:
                    got = [({"id": m, "name": m, "model": m, "pack": "",
                             "ctx": r["context_length"], "loaded": True}, r)
                           for m, r in sorted(rows.items())]
                e = n["agent"].get(getattr(p, "via", None))
                why = _api_why(e)
                for card, row in got:
                    if row is None or (p.id, row["model"]) in covered:
                        continue
                    if why is not None:
                        st, txt = "down", why
                    elif h["state"] == "up":
                        st, txt = ("serving", "serving") if card["loaded"] else \
                            ("starting", "loading the model")
                    elif h["state"] == "down":
                        st, txt = "down", f"down · {h['last_error'] or 'no answer'}"
                    else:
                        st, txt = "starting", "waiting for the first health check"
                    if n["row"]["drain"] and st == "serving":
                        txt = f"serving · {n['row']['alias']} is drained"
                    items.append((nid, n, p, e, card, row, st, txt))
        count: dict[str, int] = {}
        for it in items:
            pub = it[5]["public_id"] or it[5]["model"]
            count[pub] = count.get(pub, 0) + 1
        out = []
        for nid, n, p, e, card, row, st, txt in items:
            pub = row["public_id"] or row["model"]
            api = pub if count[pub] == 1 and pub not in taken else f"{p.id}/{pub}"
            out.append({"id": api, "name": card["name"], "model": card["model"],
                        "pack": card["pack"], "type": "single", "members": [nid],
                        "api_id": api, "ctx": int(card["ctx"] or row["context_length"] or 0),
                        "status": st, "status_text": txt, "record": False,
                        "state": "serving", "managed": bool(e and e.get("managed")),
                        "member_states": [{"node": nid, "alias": n["row"]["alias"],
                                           "engine": getattr(p, "via", None), "backend": p.id,
                                           "model": row["model"],
                                           "managed": bool(e and e.get("managed")),
                                           "state": "draining" if n["row"]["drain"] else st,
                                           "text": txt, "routable": st == "serving"
                                           and not n["row"]["drain"]}],
                        "job": None})
        return out

    def _reindex(self, ctx: dict, deps: list[dict]) -> None:
        by_backend = {bid: (nid, ctx["nodes"][nid]["row"]["alias"])
                      for bid, nid in ctx["by_backend"].items()}
        by_model = {}
        for d in deps:
            for m in d["member_states"]:
                if m["backend"]:
                    by_model[(m["backend"], m["model"])] = d["id"]
        self._index = (time.monotonic(), self.cfg.generation, by_backend, by_model)

    # ------------------------------------------------------------------ the gateway
    def locate(self, bid: str, model: str | None) -> tuple | None:
        """(node id, alias, deployment id or None) of a backend on a node; None for one on no
        node (a cloud API)."""
        idx = self._index
        if idx is None or time.monotonic() - idx[0] > 5.0 or idx[1] != self.cfg.generation:
            try:
                self.all()
            except Exception:                                        # noqa: BLE001
                return None
            idx = self._index
        got = idx[2].get(bid)
        if got is None:
            return None
        return got[0], got[1], idx[3].get((bid, model))

    def _by_api(self) -> dict:
        out = {}
        for r in self.records():
            out.setdefault(r["id"], r)
            api = (r.get("options") or {}).get("api_id")
            if api:
                out[api] = r
        return out

    def place(self, model, body, headers, windows: bool = True):
        """The route of a request for a record's model; for any other model the table's route
        without the engines on another engine API major, or None (the table routes it). A
        Replicas' members with mixed windows: one that fits the request first (`_fitting`)."""
        if not isinstance(model, str) or not model:
            return None
        rec = self._by_api().get(model)
        if rec is None:
            return self._table_route(model)
        if rec["type"] == "split":
            raise Refused(501, "not_built", f"{rec['name']} runs Split: the pipeline split "
                                            f"(ENG-307) is not built, so it serves no request yet")
        if rec["state"] == "stopped":
            raise Refused(503, "deployment_stopped", f"{rec['name']} is stopped: start it to "
                                                     f"serve requests")
        ctx = self._context(CTX_TTL_S)
        ms = [m for m in self.members(rec, ctx) if m["state"] != "leaving"]
        if not ms:
            raise Refused(503, "no_member", f"{rec['name']} has no member that takes requests")
        pin = _header(headers, PIN_HEADER)
        if pin:
            # one member, by its node's id or alias: no fallback to another member, and the
            # conversation's home stays where it was
            m = next((x for x in ms if pin in (x["node"], x["alias"])), None)
            if m is None:
                raise Refused(409, "not_a_member", f"{pin} is not a member of {rec['name']}")
            if not m["routable"]:
                raise Refused(503, "member_not_serving", f"{m['alias']} does not take "
                                                         f"{rec['name']}'s requests now: "
                                                         f"{m['text']}")
            ms = [m]
        ok = [m for m in ms if m["routable"]]
        key = None
        if rec["type"] == "replicas" and ok and not pin:
            key = affinity.policy_key(affinity.conversation_key(body, headers),
                                      (rec.get("options") or {}).get("affinity") or "root")
            by = {m["backend"]: m for m in ok}
            if key is not None:
                home = self._sticky_of(rec["id"]).get(key)
                ranked = affinity.ranked(key, list(by))
                if home in by:
                    ranked = [home] + [b for b in ranked if b != home]
                first = [by[b] for b in ranked]
            else:
                first = sorted(ok, key=lambda m: (self.svc.relay.inflight.count(m["backend"]),
                                                  m["backend"]))
            cands = first + [m for m in ms if not m["routable"]]
        else:
            cands = ok + [m for m in ms if not m["routable"]]
        # a member on another engine API major takes no request, not even as a last fallback
        wrong = [m for m in cands if m.get("api_why")]
        cands = [m for m in cands if not m.get("api_why")]
        if wrong and not cands:
            raise Refused(503, "no_member", f"{rec['name']} has no member that takes requests: "
                                            f"{wrong[0]['alias']}: {wrong[0]['api_why']}")
        s = self.svc.table.snapshot()
        cands = [m for m in cands if m["backend"] in s["backends"]]
        if not cands:
            raise Refused(503, "no_member", f"{rec['name']}: no backend reaches its members' "
                                            f"engines")
        if rec["type"] == "replicas" and windows and not pin:
            cands = self._fitting(rec, cands, body)
        m0 = cands[0]
        row = self.svc.table._row(s, m0["backend"], m0["model"])
        r = self.svc.table._route(s, m0["backend"], row, model)
        r.model = m0["model"]
        mine = [(m["backend"], m["model"]) for m in cands[1:]]
        bad = self._wrong_api(ctx)
        r.fallbacks = [] if pin else mine + [fb for fb in r.fallbacks
                                             if fb not in mine and fb[0] != m0["backend"]
                                             and fb[0] not in bad]
        r.deployment = rec["id"]
        r.no_replay = constrained(body)
        if key is not None:
            r.on_served = lambda got, did=rec["id"], k=key: self._served(did, k, got)
        return r

    @staticmethod
    def _window(m: dict) -> int | None:
        return (m.get("check") or {}).get("window")

    def _fitting(self, rec: dict, cands: list[dict], body) -> list[dict]:
        """SRV-741, mixed windows: when the routable members' engines say more than one
        `max_len`, the request's prompt (counted by the first candidate's engine) plus its
        `max_tokens` decides: the members whose window holds it, in their order, else a 400
        that names the largest window. One window (or none read): the order as it is, and the
        engine's own 400 for a request past it."""
        known = {self._window(m) for m in cands if m["routable"]} - {None}
        if len(known) < 2 or not isinstance(body, dict):
            return cands
        need = self._need(cands[0], body)
        if need is None:
            return cands
        fit = [m for m in cands if (self._window(m) or 0) >= need]
        if fit:
            return fit
        raise Refused(400, "context_length_exceeded",
                      f"the prompt and max_tokens need {need} tokens; the largest window of "
                      f"{rec['name']} is {max(known)} tokens")

    def _need(self, m: dict, body: dict) -> int | None:
        """The prompt's tokens by the member's engine (`POST /v1/tokenize`) plus `max_tokens`;
        None when the engine does not count it."""
        b = self.cfg.backend(m["backend"])
        if b is None:
            return None
        st, doc = self._call(b, TOKENIZE_PATH, dict(body, model=m["model"], stream=False),
                             TOKENIZE_TIMEOUT_S)
        n = (doc or {}).get("tokens") if st == 200 and isinstance(doc, dict) else None
        if not isinstance(n, int) or isinstance(n, bool):
            return None
        mt = body.get("max_tokens", body.get("max_completion_tokens"))
        return n + (mt if isinstance(mt, int) and not isinstance(mt, bool) and mt > 0 else 0)

    def _call(self, b: dict, path: str, body: dict, timeout: float) -> tuple[int, dict | None]:
        """One JSON POST to an engine route on a backend: (status, answer); 502 and the reason
        when it does not answer."""
        h = {"Content-Type": "application/json", "User-Agent": WARM_UA}
        try:
            key = self.cfg.key_for(b["id"])
        except Exception:                                            # noqa: BLE001
            key = None
        if key:
            h["Authorization"] = f"Bearer {key}"
        req = urllib.request.Request(canary_mod.engine_url(b["url"], path),
                                     data=json.dumps(body).encode(), headers=h, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                st, raw = r.status, r.read()
        except urllib.error.HTTPError as exc:
            st, raw = exc.code, exc.read()
        except Exception as exc:                                     # noqa: BLE001
            return 502, {"error": {"type": "upstream_error",
                                   "message": f"{type(exc).__name__}: {exc}"[:200]}}
        try:
            doc = json.loads(raw or b"null")
        except ValueError:
            doc = None
        return st, doc if isinstance(doc, dict) else None

    def cache_route(self, path: str, body, headers) -> tuple[int, dict, tuple]:
        """SRV-741: `POST /v1/cache/peek`, `/prewarm` and `/release` for a deployment's model on
        the member that holds the conversation (its `session`, else the conversation key the
        gateway reads): the sticky member, as the conversation's next turn would go; the
        conversation's home is not moved. (status, answer, headers)."""
        if path not in CACHE_PATHS:
            raise Refused(404, "not_found", f"no route {path}")
        if not isinstance(body, dict):
            raise Refused(400, "bad_request", "the body is a JSON object")
        model = body.get("model")
        if not isinstance(model, str) or not model:
            raise Refused(400, "model_required", "model names the deployment whose member "
                                                 "holds the conversation")
        q = dict(body)
        sess = body.get("session")
        if isinstance(sess, str) and sess and not affinity.conversation_key(body, None):
            q["conversation_id"] = sess
        r = self.place(model, q, headers, windows=False)
        if r is None:
            r = self.svc.table.resolve(model)
        b = self.cfg.backend(r.backend)
        if b is None:
            raise Refused(503, "no_member", f"{model}: no backend reaches its engine")
        st, doc = self._call(b, path, dict(body, model=r.model), CACHE_TIMEOUT_S)
        hdrs = []
        got = self.locate(r.backend, r.model)
        if got is not None:
            hdrs.append(("X-Tandem-Node", got[1]))
        if getattr(r, "deployment", None):
            hdrs.append(("X-Tandem-Deployment", r.deployment))
        if doc is None:
            st, doc = 502, {"error": {"type": "upstream_error",
                                      "message": f"the engine answered HTTP {st} without JSON"}}
        return st, doc, tuple(hdrs)

    def _table_route(self, model: str):
        """The table's route for a model no record names, when an engine of the fleet speaks
        another engine API major: refused when it lands on such an engine (a derived Single's
        member reads `down` there), such an engine dropped from its fallbacks. None otherwise, and
        always for a proxy without nodes: a plain proxy's requests never read a machine."""
        if not self.cfg.nodes():
            return None
        bad = self._wrong_api(self._context(CTX_TTL_S))
        if not bad:
            return None
        r = self.svc.table.resolve(model)            # route.NotFound as the table answers it
        if r.backend in bad:
            alias, why = bad[r.backend]
            raise Refused(503, "no_member", f"{model} has no member that takes requests: "
                                            f"{alias}: {why}")
        r.fallbacks = [fb for fb in r.fallbacks if fb[0] not in bad]
        return r

    @staticmethod
    def _wrong_api(ctx: dict) -> dict:
        """{backend id: (node alias, reason)} of every engine whose agent says it speaks another
        engine API major."""
        out = {}
        for n in ctx["nodes"].values():
            for p in n["probes"]:
                why = _api_why(n["agent"].get(getattr(p, "via", None)))
                if why is not None:
                    out[p.id] = (n["row"]["alias"], why)
        return out

    def _sticky_of(self, did: str) -> affinity.Sticky:
        with self._lock:
            st = self._sticky.get(did)
            if st is None:
                st = self._sticky[did] = affinity.Sticky(clock=self.clock)
            return st

    def _served(self, did: str, key: str, rec) -> None:
        """The member that answered a turn keeps the conversation (a fallback included)."""
        if rec.backend and rec.status is not None and rec.status < 400:
            self._sticky_of(did).put(key, rec.backend)

    def listing(self, created: int) -> list[dict]:
        """/v1/models: each record that may serve once under its API id (a stopped one and a
        Split are not listed), then the table's models that no record covers; a table id a record
        took is listed in its prefixed form."""
        ctx = self._context(CTX_TTL_S)
        out, covered, ids = [], set(), set()
        for rec in self.records():
            ms = self.members(rec, ctx)
            for m in ms:
                if m["backend"]:
                    covered.add((m["backend"], m["model"]))
            if rec["type"] == "split" or rec["state"] == "stopped":
                continue
            o = rec.get("options") or {}
            api = o.get("api_id") or rec["id"]
            ids.add(api)
            out.append({"id": api, "object": "model", "created": created, "owned_by": "fleet",
                        "tandem": {"deployment": rec["id"], "type": rec["type"],
                                   "name": rec["name"], "model": rec["model"],
                                   "pack": rec["pack"], "context_length": o.get("ctx") or None,
                                   "nodes": [m["alias"] for m in ms if m["state"] != "leaving"],
                                   "cloud": False}})
        for m in self.svc.table.listing(created):
            t = m.get("tandem") or {}
            if (m["owned_by"], t.get("upstream_id")) in covered:
                continue
            if m["id"] in ids:
                m = dict(m, id=f"{m['owned_by']}/{m['id']}")
            out.append(m)
        return out

    def card(self, name: str, created: int) -> dict | None:
        return next((m for m in self.listing(created) if m["id"] == name), None)

    # ------------------------------------------------------------------ records
    def _engine_for(self, ctx: dict, nid: str, eid: str | None, who: str) -> dict:
        """{node, engine, backend} of the engine `eid` (or the node's only one) on node `nid`,
        a backend made for it when it has none."""
        n = ctx["nodes"].get(nid)
        if n is None:
            raise DeployError(404, "not_found", f"no node {nid}")
        a = n["row"]["alias"]
        probes = {getattr(p, "via", None) or p.id: p for p in n["probes"]}
        engines = list(n["agent"]) or list(probes)
        if eid is None:
            if len(engines) != 1:
                raise DeployError(409, "no_engine" if not engines else "ambiguous_engine",
                                  f"{a} has no engine: its TandemNode agent lists none" if not
                                  engines else f"{a} has {len(engines)} engines: name one "
                                               f"({', '.join(map(str, engines))})")
            eid = engines[0]
        if eid not in engines:
            raise DeployError(404, "not_found", f"{a} has no engine {eid}")
        p = probes.get(eid)
        if p is not None:
            return {"node": nid, "engine": eid if n["agent"] else None, "backend": p.id}
        e = n["agent"][eid]
        addr = n["row"]["address"] or n["src"].address()
        host = f"[{addr}]" if ":" in addr else addr
        bid = slug(f"{a}-{eid}").replace(".", "-")[:32].strip("-") or "engine"
        while self.cfg.backend(bid) is not None:
            bid = (bid[:28] + "-" + secrets.token_hex(2)).strip("-")
        kind = e.get("kind") if e.get("kind") in ("tandemllm", "vllm", "sglang") else "generic"
        self.cfg.create_backend({"id": bid, "kind": kind, "label": f"{a} · {eid}",
                                 "url": f"http://{host}:{e['port']}/v1"}, who=who)
        self.fleet.log(f"[deploy] the backend {bid} reaches {eid} on {a} (:{e['port']})")
        self._ctx = None
        return {"node": nid, "engine": eid, "backend": bid}

    def _served_id(self, bid: str, model: str) -> str:
        """The id a backend serves `model` under: the model when it lists it, its one model when
        it lists one, else the model."""
        rows = self.svc.table.snapshot()["by_backend"].get(bid) or {}
        if model in rows:
            return model
        for r in rows.values():
            if r["public_id"] == model:
                return r["model"]
        return next(iter(rows)) if len(rows) == 1 else model

    def check(self, b: dict, who: str, old: dict | None = None) -> dict:
        """A record from a POST body (or `old` merged with a PATCH body), checked: DeployError
        400 for a malformed field, 404 for a node or deployment the fleet does not have, 409
        for a member without an engine, an id taken, Replicas on two releases."""
        if not isinstance(b, dict):
            raise DeployError(400, "bad_request", "the body is a deployment object")
        allowed = PATCHABLE if old is not None else FIELDS
        extra = [k for k in b if k not in allowed]
        if extra:
            raise DeployError(400, "bad_request", f"deployment: unknown field {extra[0]!r}")
        o = dict(old.get("options") or {}) if old else {}
        cur = {"id": old["id"], "name": old["name"], "model": old["model"], "pack": old["pack"],
               "type": old["type"], "members": old["members"], "stages": old["stages"],
               "api_id": o.get("api_id"), "ctx": o.get("ctx"), "role": o.get("role"),
               "role_of": o.get("role_of"), "affinity": o.get("affinity"),
               "state": old["state"]} if old else {}
        d = dict(cur, **b)
        for k in ("model", "type"):
            if not d.get(k):
                raise DeployError(400, "bad_request", f"deployment: {k} is required")
        if not isinstance(d["model"], str) or len(d["model"]) > 200:
            raise DeployError(400, "bad_request", "model is a string of at most 200 characters")
        t = d["type"]
        if t not in TYPES:
            raise DeployError(400, "bad_request", f"type is one of {', '.join(TYPES)}")
        for k, cap in (("name", 120), ("pack", 80), ("role", 80)):
            if d.get(k) is not None and (not isinstance(d[k], str) or len(d[k]) > cap):
                raise DeployError(400, "bad_request", f"{k} is a string of at most {cap} "
                                                      f"characters")
        api = d.get("api_id") or d["model"]
        if not isinstance(api, str) or not 0 < len(api) <= 200 or any(c.isspace() for c in api):
            raise DeployError(400, "bad_request", "api_id is 1 to 200 characters, no spaces")
        did = d.get("id") or slug(api)
        if not isinstance(did, str) or not ID_RE.match(did):
            raise DeployError(400, "bad_request", "id is 1 to 64 of a-z, 0-9, '.', '_', '-'")
        ctx_tokens = d.get("ctx")
        if ctx_tokens is not None and (not isinstance(ctx_tokens, int) or
                                       isinstance(ctx_tokens, bool) or ctx_tokens < 0):
            raise DeployError(400, "bad_request", "ctx is a token count")
        if d.get("affinity") is not None and d["affinity"] not in AFFINITY:
            raise DeployError(400, "bad_request", "affinity is root or own")
        if d.get("state", "serving") not in STATES:
            raise DeployError(400, "bad_request", "state is serving or stopped")
        others = [r for r in self.records() if not old or r["id"] != old["id"]]
        if old is None and any(r["id"] == did for r in others):
            raise DeployError(409, "conflict", f"a deployment {did} exists already")
        for r in others:
            if api in (r["id"], (r.get("options") or {}).get("api_id")):
                raise DeployError(409, "conflict", f"{r['name']} answers to {api} already")
        if d.get("role_of") is not None:
            if t != "role":
                raise DeployError(400, "bad_request", "role_of is a Role deployment's")
            if not any(r["id"] == d["role_of"] for r in others):
                raise DeployError(404, "not_found", f"no deployment {d['role_of']} for the role")
        raw = d.get("members")
        if not isinstance(raw, list) or not raw:
            raise DeployError(400, "bad_request", "members is a list of nodes")
        n = len(raw)
        if t in ("single", "role") and n != 1:
            raise DeployError(400, "bad_request", f"a {t.title()} deployment has one member")
        if t == "replicas" and n < 2:
            raise DeployError(400, "bad_request", "a Replicas deployment has two or more members")
        if t == "split" and n not in (2, 4):
            raise DeployError(400, "bad_request", "a Split runs over 2 or 4 nodes")
        ctx = self._context()
        members = []
        for i, m in enumerate(raw):
            if isinstance(m, str):
                m = {"node": m}
            if not isinstance(m, dict) or not isinstance(m.get("node") or m.get("backend"), str):
                raise DeployError(400, "bad_request", f"members[{i}] is a node id or {{node, "
                                                      f"engine?}}")
            if set(m) - {"node", "engine", "backend", "model"}:
                raise DeployError(400, "bad_request", f"members[{i}]: node, engine, backend, "
                                                      f"model")
            prev = next((x for x in (old or {}).get("members") or []
                         if x.get("node") == m.get("node") and
                         (m.get("engine") is None or x.get("engine") == m.get("engine"))), None)
            if prev is not None and prev.get("backend"):
                members.append(dict(prev))
                continue
            if m.get("backend") and not m.get("node"):
                members.append({"node": ctx["by_backend"].get(m["backend"]), "engine": None,
                                "backend": m["backend"], "model": m.get("model")})
                continue
            if t == "split":
                if m["node"] not in ctx["nodes"]:
                    raise DeployError(404, "not_found", f"no node {m['node']}")
                members.append({"node": m["node"], "engine": m.get("engine"), "backend": None,
                                "model": None})
                continue
            got = self._engine_for(ctx, m["node"], m.get("engine"), who)
            got["model"] = m.get("model") or self._served_id(got["backend"], d["model"])
            members.append(got)
        keys = [(m["node"], m.get("engine"), m.get("backend")) for m in members]
        if len(set(keys)) != len(keys):
            raise DeployError(400, "bad_request", "each member once")
        if t == "split":
            nodes = [m["node"] for m in members]
            if len(set(nodes)) != n:
                raise DeployError(400, "bad_request", "a Split's stages run on different nodes")
            st = d.get("stages") or [{"node": x, "layers": f"stage {i + 1} of {n}", "gib": 0.0}
                                     for i, x in enumerate(nodes)]
            if not isinstance(st, list) or [s.get("node") if isinstance(s, dict) else None
                                            for s in st] != nodes:
                raise DeployError(400, "bad_request", "stages: one {node, layers, gib} per member, "
                                                      "in the members' order")
            for s in st:
                if set(s) != {"node", "layers", "gib"} or not isinstance(s["layers"], str) or \
                        not isinstance(s["gib"], (int, float)) or isinstance(s["gib"], bool):
                    raise DeployError(400, "bad_request", "a stage is {node, layers, gib}")
            d["stages"] = st
        elif d.get("stages"):
            raise DeployError(400, "bad_request", "stages are a Split's")
        if t == "replicas":
            rels = {}
            for m in members:
                nn = ctx["nodes"].get(m["node"])
                v = release(nn["src"].version()) if nn and nn["status"] != "off" else None
                if v:
                    rels.setdefault(v, []).append(nn["row"]["alias"])
            if len(rels) > 1:
                words = ", ".join(f"{', '.join(a)} {v}" for v, a in sorted(rels.items()))
                raise DeployError(409, "version_mismatch", f"Replicas members run one release: "
                                                           f"{words}")
        if ctx_tokens is None and t != "split":
            rows = self.svc.table.snapshot()["by_backend"].get(members[0]["backend"]) or {}
            row = rows.get(members[0]["model"])
            ctx_tokens = (row or {}).get("context_length") or 0
        o.update({"api_id": api, "ctx": ctx_tokens or 0})
        for k in ("role", "role_of", "affinity"):
            if d.get(k) is not None:
                o[k] = d[k]
        if t != "replicas":
            o.pop("affinity", None)
        if t != "role":
            o.pop("role", None)
            o.pop("role_of", None)
        return {"id": old["id"] if old else did, "name": d.get("name") or d["model"],
                "model": d["model"], "pack": d.get("pack") or "", "type": t, "members": members,
                "stages": d["stages"] if t == "split" else [], "options": o,
                "state": "stopped" if t == "split" else d.get("state") or "serving"}

    def create(self, body, who: str, now: float) -> dict:
        doc = self.check(body, who)
        rec = self.cfg.put_deployment(doc, who=who)
        self._event(rec, f"{rec['name']} ({rec['type'].title()}) recorded on "
                         f"{self._aliases(rec)}", "a deployment record")
        return {"contract_version": CONTRACT, **self.view(rec)}

    def patch(self, did: str, body, who: str, now: float) -> dict:
        old = self.record(did)
        if did in self._running:
            raise DeployError(409, "busy", f"a job runs on {old['name']}")
        if not isinstance(body, dict) or not body:
            raise DeployError(400, "bad_request", "the body is an object with what changes")
        doc = self.check(body, who, old)
        doc["options"] = dict(old.get("options") or {}, **doc["options"])
        rec = self.cfg.put_deployment(doc, who=who)
        if "members" in body:
            self._event(rec, f"{rec['name']} now on {self._aliases(rec)}",
                        f"{rec['type'].title()} · {len(rec['members'])} "
                        f"member{'s' if len(rec['members']) != 1 else ''}")
        return {"contract_version": CONTRACT, **self.view(rec)}

    def delete(self, did: str, who: str) -> dict:
        rec = self.record(did)
        if did in self._running:
            raise DeployError(409, "busy", f"a job runs on {rec['name']}")
        self.cfg.delete_deployment(did, who=who)
        with self._lock:
            self._sticky.pop(did, None)
        self._event(rec, f"{rec['name']}'s record removed", "its engines keep running; what they "
                                                            "serve shows as Singles")
        return {"contract_version": CONTRACT, "deleted": did}

    def _aliases(self, rec: dict) -> str:
        names = {n["id"]: n["alias"] for n in self.cfg.nodes()}
        return " + ".join(names.get(m.get("node"), m.get("node") or m.get("backend") or "?")
                          for m in rec["members"])

    def _event(self, rec: dict, text: str, detail: str) -> None:
        nodes = {m.get("node") for m in rec["members"]}
        node = next(iter(nodes)) if len(nodes) == 1 and None not in nodes else "all"
        self.fleet.store.add_event("deploy", node, text, detail, deployment=rec["id"])

    # ------------------------------------------------------------------ plans and jobs
    def plan(self, did: str, action: str, now: float, q: dict) -> dict:
        return self._plan(did, action, now, q)[0]

    def _plan(self, did: str, action: str, now: float, q: dict) -> tuple[dict, str]:
        rec = self.record(did)
        if action not in ACTIONS:
            raise DeployError(404, "not_found", f"no action {action} (one of "
                                                f"{', '.join(ACTIONS)})")
        p = {"contract_version": CONTRACT, "deployment": did, "action": action, "title": "",
             "allowed": True, "blocked": None, "destructive": action in DESTRUCTIVE,
             "impact": [], "keeps": [], "confirm": None, "expires_at": None, "job": None,
             "to": None, "from": None}
        o = rec.get("options") or {}
        if o.get("job"):
            p["job"] = _job_short(self.cfg.job("deploy", o["job"]))
        kind = "conflict"
        ms = self.members(rec)
        name = rec["name"]
        if did in self._running:
            p.update(allowed=False, blocked=f"a job runs on {name}: wait for it")
            kind = "busy"
        if action == "start":
            p["title"] = f"Start {name}"
            if rec["type"] == "split":
                p.update(allowed=False, blocked="a Split runs once the pipeline split (ENG-307) "
                                                "is built: its record and plan are kept until then")
                kind = "not_built"
            for m in ms:
                if m["state"] == "serving":
                    p["impact"].append(f"{m['alias']}: serves already; warm-up only")
                elif m["managed"]:
                    p["impact"].append(f"{m['alias']}: TandemNode starts {m['engine']}, then one "
                                       f"token warms it up")
                elif rec["type"] != "split":
                    p.update(allowed=False, blocked=f"{m['alias']}: {m['engine'] or 'its engine'}"
                                                    f" is not managed by TandemNode and does not "
                                                    f"answer: start it where it runs")
                    kind = "not_managed"
        elif action == "stop":
            p["title"] = f"Stop {name}"
            if rec["state"] == "stopped" and not any(m["state"] == "serving" for m in ms):
                p.update(allowed=False, blocked=f"{name} is stopped already")
            p["impact"].append(f"{o.get('api_id') or did} takes no new request: the gateway "
                               f"answers 503 deployment_stopped")
            n = sum(self._inflight(m) for m in ms)
            p["impact"].append(f"{n} request{'s' if n != 1 else ''} in flight finish first "
                               f"(at most {DRAIN_WAIT_S:.0f} s)" if n else "nothing is in flight")
            for m in ms:
                if m["managed"]:
                    p["impact"].append(f"{m['alias']}: TandemNode stops {m['engine']}")
                elif m["state"] in ("serving", "starting", "draining") and rec["type"] != "split":
                    p.update(allowed=False, blocked=f"{m['alias']}: {m['engine'] or 'its engine'}"
                                                    f" is not managed by TandemNode: stop it where "
                                                    f"it runs")
                    kind = "not_managed"
            p["keeps"].append("the record, its members and the weights on every node")
        else:
            p["title"] = f"Move {name}"
            kind = self._plan_move(rec, ms, p, q) or kind
        if p["allowed"] and p["destructive"]:
            token = secrets.token_urlsafe(18)
            with self._lock:
                self._tokens = {k: v for k, v in self._tokens.items() if v[2] > now}
                self._tokens[token] = (did, action, now + CONFIRM_S)
            p["confirm"], p["expires_at"] = token, round(now + CONFIRM_S, 3)
        return p, kind

    def _plan_move(self, rec: dict, ms: list[dict], p: dict, q: dict) -> str | None:
        name = rec["name"]
        if rec["type"] == "split":
            p.update(allowed=False, blocked="a Split moves with ENG-307")
            return "not_built"
        src, dst = q.get("from"), q.get("to")
        if src is None and len(ms) == 1:
            src = ms[0]["node"]
        p["from"], p["to"] = src, dst
        if not dst or not src:
            p.update(allowed=False, blocked="name the node it moves from (from) and to (to)")
            return "bad_request"
        if not any(m["node"] == src for m in ms):
            p.update(allowed=False, blocked=f"{src} is no member of {name}")
            return "bad_request"
        if any(m["node"] == dst for m in ms):
            p.update(allowed=False, blocked=f"{dst} is a member of {name} already")
            return "conflict"
        ctx = self._context()
        n = ctx["nodes"].get(dst)
        if n is None:
            p.update(allowed=False, blocked=f"no node {dst}")
            return "not_found"
        a = n["row"]["alias"]
        if n["status"] == "off" or n["row"]["drain"]:
            p.update(allowed=False, blocked=f"{a} is {'off' if n['status'] == 'off' else 'drained'}"
                                            f": it takes nothing new")
            return "conflict"
        engines = list(n["agent"]) or [getattr(x, "via", None) or x.id for x in n["probes"]]
        eid = q.get("engine") or (engines[0] if len(engines) == 1 else None)
        if eid is None or eid not in engines:
            p.update(allowed=False, blocked=f"{a} has no engine to serve it" if not engines else
                     f"{a} has {len(engines)} engines: name one (engine)")
            return "no_engine"
        e = n["agent"].get(eid) or {}
        if not e.get("managed") and e.get("state") != "up":
            p.update(allowed=False, blocked=f"{a}: {eid} is not managed by TandemNode and does "
                                            f"not answer")
            return "not_managed"
        old = next(m for m in ms if m["node"] == src)
        p["impact"] += [f"{a}: {eid} starts and warms up; then it takes {name}'s new requests "
                        f"and conversations",
                        f"{old['alias']}: what is in flight finishes, then "
                        + (f"TandemNode stops {old['engine']}" if old["managed"] else
                           f"{old['engine'] or 'its engine'} keeps running (not managed by "
                           f"TandemNode)"),
                        f"{(rec.get('options') or {}).get('api_id') or rec['id']} stays the "
                        f"API id"]
        p["engine"] = eid
        return None

    def _inflight(self, m: dict) -> int:
        """The requests running on a member: the gateway's own, or its engine's count."""
        bid = m.get("backend")
        if not bid:
            return 0
        gw = self.svc.relay.inflight.count(bid)
        k = math.ceil(self.clock() / 2)
        eng = 0.0
        with self.fleet._lock:
            r = self.fleet.rings.get(("backend", bid, "req"))
            got = r and (r.slot(2, k) or r.slot(2, k - 1) or r.slot(2, k - 2))
            if got and got[1]:
                eng = got[2]
        return max(gw, int(round(eng)))

    def act(self, did: str, action: str, body, who: str, now: float) -> dict:
        body = body if isinstance(body, dict) else {}
        q = {k: body[k] for k in ("to", "from", "engine") if body.get(k)}
        p, kind = self._plan(did, action, now, q)
        if not p["allowed"]:
            raise DeployError(501 if kind == "not_built" else 400 if kind == "bad_request" else
                              404 if kind == "not_found" else 409, kind, p["blocked"])
        if p["destructive"]:
            self._spend(did, action, body.get("confirm"), now)
        rec = self.record(did)
        job = {"id": f"deploy-{did}-{int(now * 1000)}", "kind": "deploy", "deployment": did,
               "action": action, "state": "running", "by": who, "started_at": round(now, 3),
               "ended_at": None, "steps": [], "log": [], "error": None, "from": p["from"],
               "to": p["to"]}
        with self._lock:
            if did in self._running:
                raise DeployError(409, "busy", f"a job runs on {rec['name']}")
            self._running.add(did)
        self.cfg.put_job(job)
        rec["options"] = dict(rec.get("options") or {}, job=job["id"])
        self.cfg.put_deployment(rec, who=who)
        threading.Thread(target=self._run, args=(job, dict(q, engine=p.get("engine"),
                                                           **{"from": p["from"], "to": p["to"]})),
                         name=f"deploy-{action}", daemon=True).start()
        return {"contract_version": CONTRACT, "deployment": did, "action": action,
                "state": "running", "job": job}

    def _spend(self, did: str, action: str, token, now: float) -> None:
        if not token:
            raise DeployError(428, "confirm_required", f"{action} needs the confirm of its plan "
                                                       f"(GET .../actions/{action})")
        with self._lock:
            got = self._tokens.pop(token, None)
        if got is None or got[0] != did or got[1] != action or got[2] < now:
            raise DeployError(409, "confirm_invalid", "this confirm is spent, stale or another "
                                                      "action's: read the plan again")

    def job_view(self, jid: str) -> dict:
        j = self.cfg.job("deploy", jid)
        if j is None or j.get("kind") != "deploy":
            raise DeployError(404, "not_found", f"no deployment job {jid}")
        return {"contract_version": CONTRACT, "deployment": j["deployment"],
                "action": j["action"], "state": j["state"], "job": j}

    def interrupted(self) -> None:
        """A job that ran when the controller stopped: failed, `interrupted`."""
        for rec in self.records():
            jid = (rec.get("options") or {}).get("job")
            j = self.cfg.job("deploy", jid) if jid else None
            if j is not None and j["state"] == "running" and rec["id"] not in self._running:
                j.update(state="failed", ended_at=round(self.clock(), 3),
                         error={"type": "interrupted", "message": "the controller stopped "
                                                                  "during the job"})
                self.cfg.put_job(j)

    # ------------------------------------------------------------------ the jobs' work
    def _say(self, job: dict, text: str) -> None:
        job["log"] = (job["log"] + [{"t": round(self.clock(), 3), "text": text}])[-LOG_MAX:]
        self.cfg.put_job(job)
        self.fleet.log(f"[deploy] {job['deployment']}: {text}")

    def _step(self, job: dict, node: str, alias: str, phase: str, text: str) -> None:
        s = next((x for x in job["steps"] if x["node"] == node), None)
        if s is None:
            s = {"node": node, "alias": alias, "phase": phase, "text": text}
            job["steps"].append(s)
        s.update(phase=phase, text=text)
        self._say(job, f"{alias}: {text}")

    def _run(self, job: dict, q: dict) -> None:
        did = job["deployment"]
        try:
            rec = self.record(did)
            getattr(self, "_job_" + job["action"])(job, rec, q)
            job.update(state="done", ended_at=round(self.clock(), 3))
            self._say(job, "done")
        except Exception as exc:                                     # noqa: BLE001
            err = exc if isinstance(exc, DeployError) else None
            job.update(state="failed", ended_at=round(self.clock(), 3),
                       error={"type": err.kind if err else "error",
                              "message": err.message if err else f"{type(exc).__name__}: {exc}"})
            self._say(job, f"failed: {job['error']['message']}")
            try:
                self._event(self.record(did), f"{job['action'].title()} of "
                                              f"{self.record(did)['name']} failed",
                            job["error"]["message"])
            except DeployError:
                pass
        finally:
            with self._lock:
                self._running.discard(did)
            self._ctx = None

    def _job_start(self, job: dict, rec: dict, q: dict) -> None:
        rec = dict(rec, state="serving")
        self.cfg.put_deployment(rec, who=job["by"])
        threads = []
        for m in self.members(rec):
            t = threading.Thread(target=self._bring_up, args=(job, rec, m), daemon=True)
            t.start()
            threads.append((m, t))
        for _m, t in threads:
            t.join()
        bad = [s for s in job["steps"] if s["phase"] == "failed"]
        if bad:
            raise DeployError(502, "start_failed", "; ".join(f"{s['alias']}: {s['text']}"
                                                             for s in bad))
        self._event(rec, f"{rec['name']} started on {self._aliases(rec)}",
                    f"{rec['type'].title()} · warm")

    def _bring_up(self, job: dict, rec: dict, m: dict) -> None:
        try:
            self._up(job, rec, m)
        except DeployError as exc:
            self._step(job, m["node"], m["alias"], "failed", exc.message)

    def _up(self, job: dict, rec: dict, m: dict) -> None:
        """One member up: its engine started (when TandemNode manages it and it is down), its
        backend healthy, one token from the model."""
        a, nid = m["alias"], m["node"]
        if self.svc.health.view(m["backend"])["state"] != "up" and not self._check(m["backend"]):
            if not m["managed"]:
                raise DeployError(409, "not_managed", f"{m['engine'] or 'its engine'} is not "
                                                      f"managed by TandemNode and does not answer")
            src = m["_n"]["src"]
            self._step(job, nid, a, "start", f"TandemNode starts {m['engine']}")
            st, doc = src.post(f"/node/engines/{m['engine']}/start", None)
            if st not in (202, 409):
                raise DeployError(502, "start_failed", (doc.get("error") or {}).get("message")
                                  or f"its agent answered HTTP {st}")
            end = self.clock() + START_WAIT_S
            while True:
                stj, e = src.client.json("GET", f"/node/engines/{m['engine']}", timeout=10.0)
                j = (e or {}).get("job") or {}
                why = _api_why(e) if stj == 200 else None
                if why is not None:
                    # another engine API major: it will not serve here, so no need to wait; the
                    # member's status reads the agent's engine view again
                    src.forget_engines()
                    raise DeployError(502, "start_failed", why)
                if stj == 200 and e.get("state") == "up" and j.get("state") != "running":
                    break
                if j.get("state") == "failed":
                    raise DeployError(502, "start_failed", j.get("message") or "its start failed")
                if self.clock() >= end:
                    raise DeployError(504, "start_timeout", f"{m['engine']} is not up after "
                                                            f"{START_WAIT_S:.0f} s")
                self.sleep(POLL_S)
        why = self._agent_api_why(m)
        if why is not None:
            m["_n"]["src"].forget_engines()
            raise DeployError(502, "start_failed", why)
        self._step(job, nid, a, "warm", "its engine answers; warming up with one token")
        self._warm_until(m["backend"], m["model"], WARM_WAIT_S)
        if isinstance(m["_n"]["src"], nodesource.AgentNode):
            m["_n"]["src"].forget_engines()
        self._step(job, nid, a, "serving", "warm: serves")

    @staticmethod
    def _agent_api_why(m: dict) -> str | None:
        """The agent's reason, read now, when the member's engine speaks another engine API major;
        None otherwise, and for a node without an agent."""
        src = (m.get("_n") or {}).get("src")
        if not isinstance(src, nodesource.AgentNode) or not m.get("engine"):
            return None
        try:
            st, e = src.client.json("GET", f"/node/engines/{m['engine']}", timeout=10.0)
        except Exception:                                            # noqa: BLE001
            return None
        return _api_why(e) if st == 200 else None

    def _check(self, bid: str) -> bool:
        b = self.cfg.backend(bid)
        if b is None:
            return False
        try:
            self.svc.health.check(b)
        except Exception:                                            # noqa: BLE001
            return False
        return self.svc.health.view(bid)["state"] == "up"

    def _warm_until(self, bid: str, model: str, wait: float) -> None:
        end = self.clock() + wait
        self._warm[(bid, model)] = (False, "not yet", self.clock())
        while True:
            if self.svc.health.view(bid)["state"] == "up" or self._check(bid):
                ok, why = self.warm(bid, model)
                self._warm[(bid, model)] = (ok, why, self.clock())
                if ok:
                    self._ctx = None
                    return
            else:
                why = self.svc.health.view(bid)["last_error"] or "no answer"
            if self.clock() >= end:
                raise DeployError(504, "warm_timeout", f"no token after {wait:.0f} s: {why}")
            self.sleep(POLL_S)

    def warm(self, bid: str, model: str, timeout: float = WARM_TIMEOUT_S) -> tuple[bool, str]:
        """One token from `model` on the backend: (answered, why not). The engine loaded and
        warm, not only healthy: a fleet update's rejoin and a deployment's start wait for it."""
        b = self.cfg.backend(bid)
        if b is None:
            return False, "no backend"
        body = json.dumps({"model": model, "max_tokens": 1, "stream": False, "temperature": 0,
                           "messages": [{"role": "user", "content": "Say OK."}]}).encode()
        h = {"Content-Type": "application/json", "User-Agent": WARM_UA, "X-Tandem": "raw"}
        try:
            key = self.cfg.key_for(bid)
        except Exception:                                            # noqa: BLE001
            key = None
        if key:
            h["Authorization"] = f"Bearer {key}"
        req = urllib.request.Request(b["url"].rstrip("/") + "/chat/completions", data=body,
                                     headers=h, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                doc = json.loads(r.read() or b"null")
        except urllib.error.HTTPError as exc:
            return False, f"HTTP {exc.code}"
        except Exception as exc:                                     # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}"[:160]
        ch = (doc or {}).get("choices") if isinstance(doc, dict) else None
        if not ch:
            return False, "no choice in the answer"
        u = doc.get("usage") or {}
        if u.get("completion_tokens") == 0:
            return False, "no token in the answer"
        return True, "one token"

    def _drain(self, job: dict, rec: dict, ms: list[dict]) -> None:
        end = self.clock() + DRAIN_WAIT_S
        while True:
            left = sum(self._inflight(m) for m in ms)
            if not left:
                return
            if self.clock() >= end:
                raise DeployError(504, "drain_timeout", f"{left} requests still in flight after "
                                                        f"{DRAIN_WAIT_S:.0f} s")
            self.sleep(POLL_S)

    def _down(self, job: dict, m: dict) -> None:
        a, nid = m["alias"], m["node"]
        if not m["managed"]:
            self._step(job, nid, a, "kept", f"{m['engine'] or 'its engine'} keeps running: not "
                                            f"managed by TandemNode")
            return
        src = m["_n"]["src"]
        self._step(job, nid, a, "stop", f"TandemNode stops {m['engine']}")
        st, doc = src.post(f"/node/engines/{m['engine']}/stop", None)
        if st not in (202, 409):
            raise DeployError(502, "stop_failed", (doc.get("error") or {}).get("message")
                              or f"its agent answered HTTP {st}")
        end = self.clock() + START_WAIT_S
        while True:
            stj, e = src.client.json("GET", f"/node/engines/{m['engine']}", timeout=10.0)
            j = (e or {}).get("job") or {}
            if stj == 200 and e.get("state") == "down" and j.get("state") != "running":
                break
            if j.get("state") == "failed" and j.get("action") == "stop":
                raise DeployError(502, "stop_failed", j.get("message") or "its stop failed")
            if self.clock() >= end:
                raise DeployError(504, "stop_timeout", f"{m['engine']} still answers")
            self.sleep(POLL_S)
        self._warm.pop((m["backend"], m["model"]), None)
        self._check(m["backend"])
        src.forget_engines()
        self._step(job, nid, a, "stopped", "stopped")

    def _job_stop(self, job: dict, rec: dict, q: dict) -> None:
        rec = dict(rec, state="stopped")
        self.cfg.put_deployment(rec, who=job["by"])
        ms = self.members(rec)
        self._say(job, "no new requests; those in flight finish")
        self._drain(job, rec, ms)
        for m in ms:
            if m["node"] is not None:
                self._down(job, m)
        self._event(rec, f"{rec['name']} stopped", f"on {self._aliases(rec)}")

    def _job_move(self, job: dict, rec: dict, q: dict) -> None:
        ctx = self._context()
        got = self._engine_for(ctx, q["to"], q.get("engine"), job["by"])
        got["model"] = self._served_id(got["backend"], rec["model"])
        new = self._member(self._context(), got, rec)
        self._up(job, rec, new)
        o = dict(rec.get("options") or {})
        o["leaving"] = [q["from"]]
        rec = dict(rec, members=rec["members"] + [got], options=o)
        self.cfg.put_deployment(rec, who=job["by"])
        self._say(job, f"{new['alias']} takes new requests; {self._alias(q['from'])} finishes "
                       f"what it runs")
        old = [m for m in self.members(rec) if m["node"] == q["from"]]
        self._drain(job, rec, old)
        for m in old:
            self._down(job, m)
        o = dict(rec["options"])
        o.pop("leaving", None)
        rec = dict(rec, members=[m for m in rec["members"] if m.get("node") != q["from"]],
                   options=o)
        self.cfg.put_deployment(rec, who=job["by"])
        with self._lock:
            st = self._sticky.get(rec["id"])
        if st is not None:
            for m in old:
                for k in [k for k, v in list(st._d.items()) if v[0] == m["backend"]]:
                    st.drop(k)
        self._event(rec, f"{rec['name']} moved {self._alias(q['from'])} → {new['alias']}",
                    "the API id stayed; its conversations continue on the new node")

    def _alias(self, nid: str) -> str:
        row = self.cfg.node(nid)
        return row["alias"] if row else nid

    # ------------------------------------------------------------------ for the fleet
    def backends_of(self, did: str) -> list[tuple[str, str]]:
        """[(node id, backend id)] of a deployment's members (records and derived)."""
        for d in self.all():
            if d["id"] == did:
                return [(m["node"], m["backend"]) for m in d["member_states"] if m["backend"]]
        raise DeployError(404, "not_found", f"no deployment {did}")

    def warm_node(self, nid: str) -> list[tuple[str, bool, str]]:
        """A fleet update's rejoin: one token from each record member on node `nid`
        [(deployment, answered, why)]."""
        out = []
        for rec in self.records():
            if rec["type"] == "split" or rec["state"] == "stopped":
                continue
            for m in self.members(rec):
                if m["node"] == nid and m["backend"]:
                    ok, why = self.warm(m["backend"], m["model"])
                    self._warm[(m["backend"], m["model"])] = (ok, why, self.clock())
                    out.append((rec["name"], ok, why))
        return out

    # ------------------------------------------------------------------ the routes
    def handle(self, method: str, parts: list[str], q: dict, body, who: str, now: float):
        p = parts[1:]
        if not p:
            if method == "GET":
                return {"contract_version": CONTRACT, "deployments": self.all()}
            if method == "POST":
                return self.create(body, who, now)
        elif p[0] == "jobs" and len(p) == 2 and method == "GET":
            return self.job_view(p[1])
        elif len(p) == 1:
            if method == "GET":
                rec = self.cfg.deployment(p[0])
                got = self.view(rec) if rec is not None else \
                    next((d for d in self.all() if d["id"] == p[0]), None)
                if got is None:
                    raise DeployError(404, "not_found", f"no deployment {p[0]}")
                return {"contract_version": CONTRACT, **got}
            if method == "PATCH":
                return self.patch(p[0], body, who, now)
            if method == "DELETE":
                return self.delete(p[0], who)
        elif len(p) == 3 and p[1] == "actions":
            if method == "GET":
                return self.plan(p[0], p[2], now, q)
            if method == "POST":
                return self.act(p[0], p[2], body, who, now)
        raise DeployError(404, "not_found", f"no route {method} /v1/fleet/{'/'.join(parts)}")


def _job_short(j: dict | None) -> dict | None:
    if not j:
        return None
    return {"id": j["id"], "action": j["action"], "state": j["state"],
            "started_at": j["started_at"], "ended_at": j["ended_at"], "text": _last(j),
            "error": j.get("error")}


def _last(j: dict) -> str:
    return j["log"][-1]["text"] if j.get("log") else j.get("action") or ""


def _api_why(e: dict | None) -> str | None:
    """An agent's engine view (GET /node/engines/{id}): its reason when the engine speaks another
    engine API major than this TandemApp (health.api_ok false, proxy/engineapi.py), else None."""
    h = (e or {}).get("health") if isinstance(e, dict) else None
    if not isinstance(h, dict) or h.get("api_ok") is not False:
        return None
    return h.get("api_why") or "its engine speaks another engine API"
