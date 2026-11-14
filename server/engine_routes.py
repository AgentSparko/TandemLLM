"""The engine-only screens as the engine's own routes (ENG-413 WP5, ENG-433; engine API 1.5, group 9).

The conductor's screens that read or load the engine's own models and memory were TandemApp's
`/v1/conductor/*` routes over handlers the engine put into its state (server/conductor/inproc.py
ROUTES until WP5). The engine serves them now, under `/v1/engine/*`, and hands the same handlers to
an extension in process (`host.engine`, engine-ext-v1 2.1), so the conductor's old paths answer the
same bodies while they last (they leave with ENG-413 WP14):

    GET  /v1/engine/residency/plan             the residency planner's view (OPS-41, OPS-47)
    GET  /v1/engine/disk                       the disk tier under the caches (C1)
    POST /v1/engine/disk/clear                 every entry of the disk tier gone
    GET  /v1/engine/deciders/status            the decision models (ENG-179)
    POST /v1/engine/deciders/test              one question to them
    POST /v1/engine/deciders/autoload          the on_start ones, through the planner
    POST /v1/engine/deciders/models/{name}/load|unload
    GET  /v1/engine/narrator/status            the narrator (P1, N2)
    POST /v1/engine/narrator/try|load|unload
    GET  /v1/engine/shadow/status              the shadow reader (SR1)
    POST /v1/engine/shadow/ask

contracts/engine-api-v1/README.md (group 9) and engine/*.schema.json describe the bodies. An answer
is the body as the screen showed it (no `contract_version`: that is the conductor contract's field,
which its own paths keep adding); an error is `{"error": {"type", "message"}}`. Reads follow the
dashboard's rule and writes, and the disk tier's numbers, `conductor_write` (server/api/auth.py), as
the conductor's paths did. Nothing here imports TandemApp: the documents come from the extension
(`document`, engine-ext-v1 2.1) or the engine's seeds, the narrator is the object the extension built
over the engine's slot.

    ROUTES[name](state, ...)        a handler: (status, body), or raises ext.Refusal
    in_process(state)               host.engine: the handlers bound to the server's state
    serve(handler, state, method, path, body)   one /v1/engine/* request on the HTTP handler
"""

from __future__ import annotations

import functools

from server.ext import Refusal

PREFIX = "/v1/engine/"
#: the narrator's try box (the screen's bounds, the engine's since WP5)
TRY_MAX_EVENTS = 64
TRY_MAX_BUDGET_MS = 10_000
TRY_MAX_TOKENS = 64
#: what a server with no narrator says (the engine's own words since WP5; until then TandemApp's, which
#: named an example model: a shared module names none, tests/test_adapter_guards.py)
NARRATOR_OFF_WHY = ("this engine serves no narrator: QWEN38_NARRATOR_MODEL is empty (set it in "
                    "ops/serve.env to the narrator's model, and restart)")
#: a narrator model is set but nothing built the narrator over its slot: it writes an extension's
#: state lines, and no extension that writes them runs here
NARRATOR_NO_EXT_WHY = ("this engine serves no narrator: QWEN38_NARRATOR_MODEL is set, but no extension "
                       "(QSE_EXT) that writes narrated lines built one over its slot")
DECIDER_ROUTES = ("status", "models", "autoload", "test")


def _not_found(message: str):
    return Refusal(404, "not_found", message)


# ---------------------------------------------------------------------- the handlers
def residency_plan(state):
    """OPS-41: the planner's view -- the limits, what fits now, the engine's own split, the
    journal's last refusal -- for the System view."""
    from server import memlimits, memsplit
    from server.conductor import residency as res_mod
    got = res_mod.documents(state)
    v, doc, roles = got["version"], got["residency"], got["roles"]
    jr = res_mod.journal_of(state)
    try:
        split = memsplit.split(state)
    except Exception:                                          # noqa: BLE001
        split = None
    # OPS-47: the planner counts the engine's growth to its declared peak, and the panel
    # shows every memory limit with what it holds now
    peak, now = memlimits.engine_gib(state)
    try:
        limits = memlimits.view(state)
    except Exception:                                          # noqa: BLE001
        limits = None
    return 200, {"version": v, "doc": doc,
                 "plan": res_mod.plan(res_mod._mem_gib(), doc, roles, engine_peak_gib=peak,
                                      engine_now_gib=now),
                 "engine": split, "limits": limits,
                 "last_refusal": jr.last_refusal() if jr is not None else None,
                 "journal": jr.tail(20) if jr is not None else []}


def disk(state):
    """The Memory screen's Disk cache tab (C1)."""
    from server import diskcache
    return 200, diskcache.view(state)


def disk_clear(state):
    from server import diskcache
    got = diskcache.clear(state)
    if not got.get("cleared"):
        raise Refusal(409, "not_running", got["why"])
    return 200, {**got, "disk": diskcache.view(state)}


def deciders(state, method: str, rest: list, body: dict, base: str = PREFIX + "deciders"):
    """The Deciders screen's routes over the registry in `state["deciders"]` (None: the engine
    serves no decision model, and only `status` answers, saying so)."""
    from server import decider_api
    reg = state.get("deciders")
    if rest == ["status"] and method == "GET":
        return 200, decider_api.status(reg, state)
    if reg is None:
        raise _not_found(decider_api.OFF_WHY)
    if rest == ["test"] and method == "POST":
        status, out, _ = decider_api.ask(reg, body, source="test")
        if status != 200:
            raise Refusal(status, out["error"]["type"], out["error"]["message"])
        return 200, out
    if rest == ["autoload"] and method == "POST":
        names = reg.autoload(service_start=bool(body.get("service_start")),
                             hold_restart=bool(body.get("hold_restart")))
        return 202, {"queued": names}
    if len(rest) == 3 and rest[0] == "models" and rest[2] in ("load", "unload") and \
            method == "POST":
        name = rest[1]
        if name not in reg.models:
            raise _not_found(f"this engine does not serve {name!r}; it serves "
                             f"{', '.join(reg.order)}")
        if rest[2] == "load":
            d = reg.load(name)
            if not d.get("allowed"):
                return 409, {"name": name, "state": reg.state[name], "decision": d,
                             "error": {"type": d.get("reason") or "refused",
                                       "message": f"the residency planner refused the load: "
                                                  f"{d.get('reason')}"}}
            return 202, {"name": name, "state": reg.state[name], "decision": d}
        if not reg.unload(name):
            raise Refusal(409, "not_loaded", f"{name} is {reg.state[name]}, not loaded")
        return 200, {"name": name, "state": reg.state[name]}
    raise _not_found(f"no route {method} {base}/{'/'.join(rest)}")


def narrator_off_why(state) -> str:
    from engine.settings import SETTINGS as _S
    return NARRATOR_NO_EXT_WHY if (_S.get("NARRATOR_MODEL") or "").strip() else NARRATOR_OFF_WHY


def _narrator_status(state, n) -> dict:
    if n is None:
        return {"enabled": False, "why": narrator_off_why(state)}
    return n.status()


def narrator(state, method: str, what: str, body: dict, base: str = PREFIX + "narrator"):
    """The narrator's routes over `state["narrator"]` (None: no narrator, and only `status`
    answers, saying so)."""
    n = state.get("narrator")
    if what == "status" and method == "GET":
        return 200, _narrator_status(state, n)
    if n is None:
        raise _not_found(narrator_off_why(state))
    if what == "try" and method == "POST":
        events = body.get("events")
        if not isinstance(events, list) or not events or len(events) > TRY_MAX_EVENTS \
                or not all(isinstance(e, dict) for e in events):
            raise Refusal(400, "bad_request", f"events is a list of 1 to {TRY_MAX_EVENTS} objects")
        budget, cap = body.get("budget_ms"), body.get("max_tokens")
        for k, v, top in (("budget_ms", budget, TRY_MAX_BUDGET_MS),
                          ("max_tokens", cap, TRY_MAX_TOKENS)):
            if v is not None and (not isinstance(v, int) or isinstance(v, bool)
                                  or not 1 <= v <= top):
                raise Refusal(400, "bad_request", f"{k} is an integer from 1 to {top}")
        lines = n.try_events(events, budget_ms=budget, max_tokens=cap)
        return 200, {"lines": lines, "slot": n.slot.status()}
    if what in ("load", "unload") and method == "POST":
        if what == "load":
            # v3: ops/start.sh asks at the start (`service_start`); from then on the server's
            # keeper loads it again whenever it is not loaded (app.narrator_once)
            ss, hr = body.get("service_start"), body.get("hold_restart")
            for k, v in (("service_start", ss), ("hold_restart", hr)):
                if v is not None and not isinstance(v, bool):
                    raise Refusal(400, "bad_request", f"{k} is a boolean")
            if n.settings()["mode"] == "off":
                # 0.11.3: the role is off by default; ops/start.sh asks whenever the model is set
                print("[narrator] not loaded: the narrator role is off (roles document, "
                      "narrator.mode)", flush=True)
                return 409, {"state": n.slot.state, "error": {
                    "type": "role_off", "message": "the narrator role is off (the roles "
                                                   "document's narrator.mode); nothing loads"}}
            state["narrator_keep"] = True
            reg = state.get("deciders")
            if n.slot.state not in ("loaded", "loading") and reg is not None and reg.loading():
                # one load at a time: the keeper loads it once the decision models are in
                return 202, {"state": n.slot.state, "queued": True}
            d = n.slot.load(service_start=bool(ss), hold_restart=bool(hr))
            if not d.get("allowed"):
                return 409, {"state": n.slot.state, "decision": d,
                             "error": {"type": d.get("reason") or "refused",
                                       "message": f"the residency planner refused the load: "
                                                  f"{d.get('reason')}"}}
            return 202, {"state": n.slot.state, "decision": d}
        state["narrator_keep"] = False        # unloaded by hand: the keeper leaves it
        if not n.slot.unload():
            raise Refusal(409, "not_loaded", f"the narrator is {n.slot.state}, not loaded")
        return 200, {"state": n.slot.state}
    raise _not_found(f"no route {method} {base}/{what}")


def shadow(state, method: str, what: str, body: dict, base: str = PREFIX + "shadow"):
    """The shadow reader's routes over `state["shadow"]` (SR1)."""
    from server.conductor import shadow as shadow_mod
    svc = state.get("shadow")
    if what == "status" and method == "GET":
        return 200, shadow_mod.status(state)
    if svc is None:
        raise _not_found(state.get("shadow_refused") or shadow_mod.OFF_WHY)
    if what == "ask" and method == "POST":
        key, question, choices = body.get("session"), body.get("question"), body.get("choices")
        if not isinstance(key, str) or not key:
            raise Refusal(400, "bad_request", "session is the router's session key")
        if not isinstance(question, str) or not 4 <= len(question) <= 4000:
            raise Refusal(400, "bad_request", "question is a string of 4 to 4,000 characters")
        if (not isinstance(choices, dict) or not 2 <= len(choices) <= 26
                or not all(isinstance(k, str) and isinstance(v, str) for k, v in choices.items())):
            raise Refusal(400, "bad_request", "choices is an object of 2 to 26 id: text pairs")
        budget = body.get("budget_ms", 5000)
        if not isinstance(budget, int) or isinstance(budget, bool) or not 1 <= budget <= 60000:
            raise Refusal(400, "bad_request", "budget_ms is an integer from 1 to 60000")
        gate = getattr(svc.slot, "gate", None)
        if gate is not None and not gate.acquire(timeout=30):
            raise Refusal(409, "busy", "the engine stayed busy for 30 s")
        try:
            out = svc.ask(key, question, choices, budget_ms=float(budget))
        finally:
            if gate is not None:
                gate.release()
        if out.get("why"):
            return 409, {"error": {"type": out["why"], "message": out.get("error") or out["why"]}}
        return 200, out
    raise _not_found(f"no route {method} {base}/{what}")


ROUTES = {"residency_plan": residency_plan, "disk": disk, "disk_clear": disk_clear,
          "deciders": deciders, "narrator": narrator, "shadow": shadow}


def in_process(state) -> dict:
    """`host.engine` (engine-ext-v1 2.1): each handler bound to the server's state. Called as
    residency_plan(), disk(), disk_clear(), deciders(method, rest, body[, base]),
    narrator(method, what, body[, base]), shadow(method, what, body[, base]); each answers
    (status, body) or raises the host's Refusal(status, type, message)."""
    return {name: functools.partial(fn, state) for name, fn in ROUTES.items()}


# ---------------------------------------------------------------------- HTTP
def _route(method: str, parts: list):
    """(handler name, its arguments, the access rule) for /v1/engine/<parts>, or None."""
    head, rest = (parts[0] if parts else ""), parts[1:]
    if head == "residency" and rest == ["plan"] and method == "GET":
        return "residency_plan", (), "dashboard"
    if head == "disk" and not rest and method == "GET":
        return "disk", (), "conductor_write"
    if head == "disk" and rest == ["clear"] and method == "POST":
        return "disk_clear", (), "conductor_write"
    if head == "deciders" and rest[:1] and rest[0] in DECIDER_ROUTES:
        read = method == "GET" or rest == ["test"]
        return "deciders", (method, rest), "dashboard" if read else "conductor_write"
    if head in ("narrator", "shadow") and len(rest) == 1:
        return head, (method, rest[0]), "dashboard" if method == "GET" else "conductor_write"
    return None


def serve(handler, state, method: str, path: str, body) -> None:
    """One `/v1/engine/*` request on the engine's HTTP handler (server/app.py's Handler): the
    access rule, then the handler's answer with `Cache-Control: no-store`."""
    parts = [p for p in path[len(PREFIX):].split("/") if p]
    got = _route(method, parts)
    if got is None:
        return handler._json(404, {"error": {"type": "not_found",
                                             "message": f"no route {method} {path}"}})
    name, args, rule = got
    if not handler._allowed(rule):
        return None
    body = body if isinstance(body, dict) else {}
    fn = ROUTES[name]
    try:
        if name in ("deciders", "narrator", "shadow"):
            status, out = fn(state, *args, body)
        else:
            status, out = fn(state)
    except Refusal as exc:
        return handler._json(exc.status, {"error": {"type": exc.type, "message": exc.message}},
                             extra_headers=(("Cache-Control", "no-store"),))
    return handler._json(status, out, extra_headers=(("Cache-Control", "no-store"),))
