"""The engine's side of the conductor that runs in its process (SRV-747): the narrator's slot and the
engine-only screens of `/v1/conductor/*`.

TandemApp's conductor (app/tandemapp/conductor/routes.py) answers every `/v1/conductor/*` route; the
ones that read or load the engine's own models and memory it hands to the handlers in `ROUTES`,
which server/app.py puts in its state under `routes.ENGINE_ROUTES`. tandem-app has none of them
and answers those screens 404 (the deciders' and the shadow reader's `status` say off). The same
state names the speed-guard report this release tree ships (`SHIPPED_GUARD`, under
`routes.SHIPPED_GUARD_KEY`), which the Compare screen lists; tandem-app lists none.

    start_narrator(state, gate)       the narrator for this server, or None (QWEN38_NARRATOR_MODEL)
    ROUTES["residency_plan"](cond)    GET /v1/conductor/residency/plan
    ROUTES["disk"](cond, method, rest)            GET /v1/conductor/memory/disk, POST .../clear
    ROUTES["deciders"](cond, method, rest, body)  /v1/conductor/deciders/*
    SHIPPED_GUARD                     <repo>/release/speed-guard.json (VIS-68)

The engine imports TandemApp's conductor here until ENG-413 (headless engine) moves the conductor
out of the engine's process (contracts/engine-api-v1/README.md); since WP3a the host hands these
handlers to the extension as `host.engine` (server/ext.py), and WP5 serves them as /v1/engine/*.
"""

from __future__ import annotations

import os

from engine.settings import SETTINGS as _S
from tandemapp.conductor.routes import ApiError

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
#: VIS-68: the speed-guard report a release tree ships (tools/speed_guard.py ship)
SHIPPED_GUARD = os.path.join(_REPO, "release", "speed-guard.json")


def start_narrator(state: dict, gate, *, fake: bool = False, log=print):
    """The narrator for this server, or None when `QWEN38_NARRATOR_MODEL` is empty. Nothing
    loads here: the model loads when a profile's first narrated line asks for it."""
    from tandemapp.conductor import narrator as narr_mod
    name = narr_mod.configured()
    if not name:
        return None
    from engine import generative
    from engine.generative.slot import Slot
    from server import decider_api
    from server.conductor import residency
    from tandemapp.conductor import schema
    # N2: a Qwen3.5 model compares its tokenizer with the served checkpoint's, to read Qwen's ids
    engine_dir = getattr(getattr(state.get("engine"), "cfg", None), "path", None)
    model = generative.model(name, _S.get("NARRATOR_DIR") or None, allow_fake=fake,
                             engine_dir=engine_dir if isinstance(engine_dir, str) else None)
    device = None
    if not fake:
        from engine.decision.device import Device
        device = Device(_S.get("NARRATOR_DEVICE"))

    def journal(rec):
        cond = state.get("conductor")
        if cond is not None:
            residency.Journal(cond.root).write(rec)

    slot = Slot(model, role="narrator", gate=gate,
                planner=decider_api.planner(state, fake=fake, role="narrator"), device=device,
                log=lambda s: log(s, flush=True),
                contract=lambda f, n: schema.role_errors(f, n, state.get("tokenizer_sha")),
                journal=journal, mem_available=lambda: decider_api._mem_available_gib(fake))
    log(f"[narrator] {name} on {device or 'no device (fake)'}: loads through the residency "
        f"planner when ops/start.sh asks (v3: at the start, then kept loaded) or a profile's "
        f"first line asks", flush=True)
    return narr_mod.Narrator(slot, role=lambda: narr_mod.role_doc(state),
                             labels=lambda: narr_mod.class_labels(state))


def residency_plan(cond):
    """OPS-41: the planner's view -- the limits, what fits now, the engine's own split, the
    journal's last refusal -- for the System view."""
    from server import memsplit
    from server.conductor import residency as res_mod
    st = cond.store
    v, doc = st.get("residency")
    _, roles = st.get("roles")
    jr = res_mod.Journal(cond.root)
    try:
        split = memsplit.split(cond.state)
    except Exception:                                          # noqa: BLE001
        split = None
    # OPS-47: the planner counts the engine's growth to its declared peak, and the panel
    # shows every memory limit with what it holds now
    from server import memlimits
    peak, now = memlimits.engine_gib(cond.state)
    try:
        limits = memlimits.view(cond.state)
    except Exception:                                          # noqa: BLE001
        limits = None
    return 200, {"version": v, "doc": doc,
                 "plan": res_mod.plan(res_mod._mem_gib(), doc, roles, engine_peak_gib=peak,
                                      engine_now_gib=now),
                 "engine": split, "limits": limits, "last_refusal": jr.last_refusal(),
                 "journal": jr.tail(20)}, None


def disk(cond, method: str, rest: list[str]):
    """The Memory screen's Disk cache tab (C1)."""
    from server import diskcache
    if rest == ["disk"] and method == "GET":
        return 200, diskcache.view(cond.state), None
    if rest == ["disk", "clear"] and method == "POST":
        got = diskcache.clear(cond.state)
        if not got.get("cleared"):
            raise ApiError(409, "not_running", got["why"])
        return 200, {**got, "disk": diskcache.view(cond.state)}, None
    raise ApiError(404, "not_found", f"no route {method} /v1/conductor/memory/{'/'.join(rest)}")


def deciders(cond, method: str, rest: list[str], body: dict):
    """The Deciders screen's routes over the registry in `STATE["deciders"]` (None: the engine
    serves no decision model, and only `status` answers, saying so)."""
    from server import decider_api
    reg = cond.state.get("deciders")
    if rest == ["status"] and method == "GET":
        return 200, decider_api.status(reg, cond.state), None
    if reg is None:
        raise ApiError(404, "not_found", decider_api.OFF_WHY)
    if rest == ["test"] and method == "POST":
        status, out, _ = decider_api.ask(reg, body, source="test")
        if status != 200:
            raise ApiError(status, out["error"]["type"], out["error"]["message"])
        return 200, out, None
    if rest == ["autoload"] and method == "POST":
        names = reg.autoload(service_start=bool(body.get("service_start")),
                             hold_restart=bool(body.get("hold_restart")))
        return 202, {"queued": names}, None
    if len(rest) == 3 and rest[0] == "models" and rest[2] in ("load", "unload") and \
            method == "POST":
        name = rest[1]
        if name not in reg.models:
            raise ApiError(404, "not_found", f"this engine does not serve {name!r}; it serves "
                                             f"{', '.join(reg.order)}")
        if rest[2] == "load":
            d = reg.load(name)
            if not d.get("allowed"):
                return 409, {"name": name, "state": reg.state[name], "decision": d,
                             "error": {"type": d.get("reason") or "refused",
                                       "message": f"the residency planner refused the load: "
                                                  f"{d.get('reason')}"}}, None
            return 202, {"name": name, "state": reg.state[name], "decision": d}, None
        if not reg.unload(name):
            raise ApiError(409, "not_loaded", f"{name} is {reg.state[name]}, not loaded")
        return 200, {"name": name, "state": reg.state[name]}, None
    raise ApiError(404, "not_found", f"no route {method} /v1/conductor/deciders/{'/'.join(rest)}")


ROUTES = {"residency_plan": residency_plan, "disk": disk, "deciders": deciders}
