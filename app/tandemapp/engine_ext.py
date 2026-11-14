"""TandemApp's engine extension (ENG-413 WP4, SRV-821): the conductor and its session monitor inside a
TandemLLM engine's process, through the engine's in-process interface engine-ext-v1
(contracts/engine-ext-v1, pinned by contracts/engine-api-v1/PIN). The engine loads it by name in
chat mode: `QSE_EXT=tandemapp.engine_ext`.

    start(host)     the shipped documents, the conductor's journal (the `conductor_events` table in
                    the ledger's file), the Conductor and its router's wiring, the migrations, the
                    session monitor (also alone, with `--conductor shadow|on` and no directory)
    routes          /v1/conductor/* (its routes), /v1/library* (the package library), /v1/memory*
                    (the model's own notes), /dashboard* (the page, until it leaves the engine);
                    each answers (status, headers, body)
    models          `TandemLLM`, read from the conductor's settings at every /v1/models answer
    health()        its part of /health `loaded`
    hooks           plan, prompt, supervisor (the decode loop's safe points), extras, follow_ups,
                    finish, stop
    calls           the request path's optional calls (engine-ext-v1 2.0 `extension.calls`), and
                    since 2.1 (ENG-413 WP5) what the engine reads of it outside a request: its
                    documents, the library's texts, the shadow switch, the monitor's mode, the
                    narrator over the engine's slot, the role contract, the decision models'
                    client and the deciders screen's api part, and the memory writer
    read_document(ext_dir, name), before_load(args)
                    2.1: the module's own reads before the start (the residency limits, the
                    session monitor's policy)
    attach(host)    tests: the extension object around a conductor or monitor a test built, no start

The engine-only screens are the engine's (`/v1/engine/*`); the conductor's old paths answer over
the engine's handlers in process (`host.engine`), set on the Conductor as `cond.engine`.

The conductor's own code is unchanged: this module wraps it. It is the engine's interim adapter
(the engine's server/ext_app.py of ENG-413 WP3a/WP3b) moved here, with what that adapter read of the
engine beyond the interface now read from the host's 2.0 fields. It imports nothing of the engine;
the engine's objects come in through `host` (tests/test_import_boundary.py holds that).
"""

from __future__ import annotations

import dataclasses
import io
import json
import time

EXT_API = "2.1"
NAME = "tandemapp"
#: the session monitor runs behind this extension with no conductor directory too
MONITOR_ALONE = True


def _version() -> str:
    try:
        from proxy._version import release
        return release() or "0"
    except Exception:                                              # noqa: BLE001
        return "0"


VERSION = _version()


#: engine-ext-v1 2.1: what before_load read before the weights load (the session monitor's policy)
_EARLY: dict = {"policy": None}


def read_document(ext_dir, name):
    """engine-ext-v1 2.1 `read_document`: the stored document `name` in the conductor's directory,
    before the extension starts: [version, doc] (version 0: the built-in one)."""
    from tandemapp.conductor.store import Store
    v, doc = Store(ext_dir, readonly=True).get(name)
    return [v, doc]


def before_load(args):
    """engine-ext-v1 2.1 `before_load`: the session monitor's policy for `--conductor shadow|on`,
    validated before the weights load (a file that does not validate stops the start); its line
    for the engine's log, or None when the monitor is not asked for."""
    _EARLY["policy"] = None
    if args.get("conductor", "raw") not in ("shadow", "on"):
        return None
    from tandemapp.conductor import monitor as conductor
    mode = conductor.parse_mode(args.get("conductor", "raw"))
    if mode in (None, "raw"):
        return None
    try:
        pol = conductor.load(args.get("conductor_policy", "") or None)
    except conductor.PolicyError as exc:
        raise SystemExit(f"[conductor] the policy does not load: {exc}")
    _EARLY["policy"] = pol
    return (f"policy {pol.path} version {pol.version}: "
            + ", ".join(f"{t.id}={t.mode}" for t in pol.tasks.values()))


def _policy(state):
    """The session monitor's policy: before_load's, or one a test put into the state."""
    return _EARLY["policy"] if _EARLY["policy"] is not None else (state or {}).get("conductor_policy")


class _Writer:
    """engine-ext-v1 2.1 `memory_writer`: the conductor's memory writer as the engine asks it; `gate`
    asks the conductor's own router."""

    def __init__(self, cond):
        self._w, self._router = cond.memory.writer, cond.router

    @property
    def stats(self):
        return self._w.stats

    def state(self, conv):
        return self._w.state(conv)

    def due(self, conv, at, idle_s, settings):
        return self._w.due(conv, at, idle_s, settings)

    def mark(self, conv, trigger):
        return self._w.mark(conv, trigger)

    def gate(self, conv):
        return self._w.gate(self._router, conv)

    def instructions(self, conv, settings):
        return self._w.instructions(conv, settings)

    def apply(self, conv, text, settings, trigger, at, info):
        return self._w.apply(conv, text, settings, trigger, at, info)

    def skipped(self, conv, trigger, why, at, info=None):
        return self._w.skipped(conv, trigger, why, at, info)


def _screens(host) -> dict:
    """The engine's screens (`host.engine`, engine-ext-v1 2.1) as the conductor's routes call them:
    the host's Refusal becomes the conductor's ApiError, so an error is answered as before."""
    from tandemapp.conductor.routes import ApiError
    refusal = getattr(host, "Refusal", None)

    def wrap(fn):
        def call(*args, **kw):
            try:
                return fn(*args, **kw)
            except Exception as exc:                                   # noqa: BLE001
                if refusal is not None and isinstance(exc, refusal):
                    raise ApiError(exc.status, exc.type, exc.message) from None
                raise
        return call
    return {name: wrap(fn) for name, fn in (getattr(host, "engine", None) or {}).items()}


def _knobs() -> list:
    from tandemapp import knobs
    return [knobs.SETTINGS.prefix + k for k in knobs.DEFAULTS]


class XPlan:
    """The plan this extension hands the engine (engine-ext-v1 `plan`): `active` and `body` as the
    interface says, and `routing` (2.0), the conductor's own plan (None with the monitor alone),
    which the engine reads by the fields `request.routing_fields` names. `server_mode` is the
    monitor's mode for the request before its profile lowers it (`Monitor.mode_for`); `ui` the state
    lines `extras` made."""

    __slots__ = ("routing", "request", "server_mode", "ui")
    body = None

    def __init__(self, routing, request: dict, server_mode: str):
        self.routing, self.request, self.server_mode = routing, request, server_mode
        self.ui = None

    @property
    def active(self) -> bool:
        return bool(self.routing is not None and self.routing.active)


def _fields(cls) -> set:
    return {f.name for f in dataclasses.fields(cls)}


def _event(cls, ev):
    """An event the engine hands over (a mapping) as the monitor's own event class."""
    if isinstance(ev, dict):
        names = _fields(cls)
        return cls(**{k: v for k, v in ev.items() if k in names})
    return ev


class Watch:
    """The supervisor the decode loop asks (engine-ext-v1 `supervisor`): the session monitor's
    Supervisor for this request. The interface's methods take the engine's events and hand the
    monitor its own; the optional methods and the fields (2.0 `supervisor.optional_methods`,
    `supervisor.fields`) are the Supervisor's own."""

    def __init__(self, sup, req_event, req_dict):
        object.__setattr__(self, "_sup", sup)
        object.__setattr__(self, "_req_event", req_event)
        object.__setattr__(self, "_req_dict", req_dict)

    def at_request(self, ev):
        from tandemapp.conductor import monitor as conductor
        ev = self._req_event if ev is self._req_dict else _event(conductor.RequestEvent, ev)
        return self._sup.at_request(ev)

    def at_block(self, ev, room):
        from tandemapp.conductor import monitor as conductor
        return self._sup.at_block(_event(conductor.BlockEvent, ev), room)

    def at_finish(self, ev, room):
        from tandemapp.conductor import monitor as conductor
        return self._sup.at_finish(_event(conductor.FinishEvent, ev), room)

    def at_tool_call(self, ev):
        from tandemapp.conductor import monitor as conductor
        return self._sup.at_tool_call(_event(conductor.ToolCallEvent, ev))

    def check_answer(self, items, final=False):
        return self._sup.check_answer(items, final=final)

    def done(self, outcome, detail):
        """After the response: its calls into the session state, and, when the monitor decided
        anything, the outcome into the journal (tool_call, text, empty or gave_up)."""
        sup, now = self._sup, time.time()
        for c in detail.get("calls") or ():
            sup.session.record_call(c["function"]["name"], c["function"]["arguments"], now)
        if not sup.events:
            sup.finish("")                     # the checks that found nothing are counted
            return
        sup.finish(outcome, finish=detail.get("finish"), resume_ms=sup.resume_ms)

    def without_notes(self, answer):
        """SP2's answer without the memory notes the client never gets: (answer, notes, calls)."""
        from tandemapp import memblock
        return memblock.without_notes(answer, calls=bool(self._sup.memory_notes))

    def settle_claim(self, asker):
        """E2's questions asked in place (the engine's `asker`); the host's NoRoom is the
        in-context stage's `no_room`."""
        return self._sup.settle_claim(asker)

    def __getattr__(self, name):
        return getattr(self._sup, name)

    def __setattr__(self, name, value):
        setattr(self._sup, name, value)


class Lines:
    """The state lines of one request (engine-ext-v1 2.0 `extras` at `start`, an item of kind
    `lines`, the fields `extras.lines_fields` names): the conductor's States object (`ui`), its
    profile's `ui` section (`doc`), the channel and the progress settings, and the helpers the
    engine's stream writes them with."""

    def __init__(self, ui, doc: dict, fmt: str):
        from tandemapp.conductor import states
        self.ui, self.doc = ui, doc
        self.channel = states.channel(doc, fmt)
        self.progress = states.progress(doc)
        self.MEMORY_KINDS = states.MEMORY_KINDS
        self.status_clients = states.status_clients()

    def status_bar(self):
        from tandemapp.conductor import states
        return states.StatusBar()

    def inline(self, line):
        from tandemapp.conductor import states
        return states.inline(line)

    def into_full(self, *args):
        from tandemapp.conductor import states
        return states.into_full(*args)

    def rate(self, tps):
        from tandemapp.conductor import states
        return states.rate(tps)

    def duration(self, s):
        from tandemapp.conductor import states
        return states.duration(s)

    def share(self):
        from tandemapp.conductor import narrator, voice
        return narrator.Share(float((self.doc.get("live") or {}).get("share", voice.DEFAULTS["share"])))


class _Reply:
    """A route's answer kept for the engine: the handler the conductor's routes, the library, the
    memory routes and the page write on (as on the controller's handler), turned into the
    interface's (status, headers, body). Its reads come from the route's request (2.0: `allowed`,
    `session_token`, `session`)."""

    def __init__(self, request):
        self._req = request
        query = getattr(request, "query", "") or ""
        self.path = request.path + ("?" + query if query else "")
        self.headers = request.headers
        self._stok = getattr(request, "session_token", None)
        self._sess = getattr(request, "session", None)
        self.status = None
        self.close_connection = False
        self._headers: dict = {}
        self.wfile = io.BytesIO()

    def _peer(self) -> str:
        return self._req.peer or ""

    def _allowed(self, route: str) -> bool:
        got = self._req.allowed(route)
        if got is None:
            return True
        status, headers, body = got
        self._json(status, body, extra_headers=tuple((headers or {}).items()))
        return False

    def _json(self, code: int, payload, extra_headers: tuple = ()) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        for k, v in extra_headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def send_response(self, code: int, message=None) -> None:
        self.status = int(code)

    def send_header(self, key: str, value) -> None:
        self._headers[str(key)] = str(value)

    def end_headers(self) -> None:
        return None

    def answer(self) -> tuple:
        if self.status is None:
            raise RuntimeError("the route wrote no answer")
        return (self.status, {k: v for k, v in self._headers.items() if k.lower() != "content-length"},
                self.wfile.getvalue())


class Ext:
    """The extension object: its attributes, its hooks, its optional calls, its routes."""

    def __init__(self, host):
        self.host = host
        self.state = host.state
        self.routes = [
            {"method": "*", "prefix": "/v1/conductor/", "handler": self._conductor},
            {"method": "GET", "prefix": "/v1/library", "handler": self._library},
            {"method": "GET", "prefix": "/v1/memory", "handler": self._memory},
            {"method": "POST", "prefix": "/v1/memory", "handler": self._memory},
            {"method": "GET", "prefix": "/dashboard", "handler": self._page},
        ]
        self.knobs = _knobs()

    @property
    def cond(self):
        return self.state.get("conductor")

    # ------------------------------------------------------------------ attributes and hooks
    @property
    def models(self) -> list:
        cond = self.cond
        return cond.models(self.state["model"], self.state["started"]) if cond is not None else []

    def health(self) -> dict:
        return {"conductor": self.cond is not None}

    def plan(self, request):
        """The router's plan; with the monitor alone a plan without one (the monitor still watches
        the request). None (served raw) with neither. A profile that does not resolve is the
        client's 400 naming X-Tandem-Profile."""
        cond, mon = self.cond, self.state.get("monitor")
        if cond is None and mon is None:
            return None
        try:
            n = int(request["body"].get("n") or 1)
        except (TypeError, ValueError):
            n = 1
        mode = self.server_mode(request["headers"].get("X-Tandem"), n)
        if cond is None:
            return XPlan(None, request, mode)
        from tandemapp.conductor.store import ConfigError
        try:
            got = cond.plan(request["body"], request["headers"], client_kind=request["client_kind"],
                            request_id=request["request_id"], chat=request["chat"],
                            count_tokens=request.get("count_tokens"), user=request.get("user"))
        except ConfigError as exc:
            raise self.host.Refusal(400, "invalid_request_error", str(exc),
                                    param="X-Tandem-Profile") from None
        return XPlan(got, request, mode)

    def prompt(self, plan, ids, info=None):
        """The router's notes into the prompt (`Plan.apply_prompt`), and the memory's notes marked
        as given: {ids, in_think, client_ids}."""
        c = plan.routing if plan is not None else None
        info = info or {}
        in_think = bool(info.get("in_think"))
        if c is None:
            return {"ids": list(ids), "in_think": in_think, "client_ids": None}
        import torch          # an engine-process hook: the router's prompt is a tensor
        t = torch.tensor(ids, dtype=torch.long)
        out, in_think = c.apply_prompt(t, self.state["tok"], in_think)
        got = {"ids": list(ids) if out is t else [int(x) for x in out.tolist()],
               "in_think": bool(in_think), "client_ids": c.clean_ids if c.changed else None}
        if getattr(c, "memory", None) is not None:
            from tandemapp.conductor import memory as conductor_memory
            conductor_memory.mark_injected(self.cond.memory, c)
            self.cond.memory.writer.seen(info.get("conv"), c.memory, c.memory.get("text"))
        return got

    def supervisor(self, plan, event):
        """The session monitor's supervisor for the request (SP4 on, before anything is
        prefilled), or None when it runs raw: the request's mode and policy under its profile, one
        session for the router and the monitor, the states' hook, the memory notes SP2 reads
        past, the router's acts and evidence, the client's thinking and the loop escape's note."""
        mon = self.state.get("monitor")
        if mon is None or plan is None:
            return None
        c, cond = plan.routing, self.cond
        mon_mode, mon_policy = plan.server_mode, None
        if c is not None:
            mon_mode = cond.monitor_mode(c, plan.server_mode)
            mon_policy = cond.monitor_policy(c, mon.policy if mon else None)
        if mon_mode not in ("shadow", "on"):
            return None
        from tandemapp.conductor import monitor as conductor
        req_ev = _event(conductor.RequestEvent, event)
        sup = mon.begin(mon_mode, req_ev, list(event["prompt_ids"]), event.get("conv"),
                        policy=mon_policy,
                        session_key=c.session.key if c is not None and c.active
                        and c.session is not None else None)
        if sup is None:
            return None
        if plan.ui is not None:
            sup.on_event = plan.ui.monitor
        # SP2 reads the answer as the client gets it: without the memory notes the filter takes
        # out (an answer of notes alone is an empty answer)
        sup.memory_notes = self._memory_takes(c, event.get("chat"), event.get("n", 1),
                                              event.get("tool_names") or ())
        if c is not None and c.acts:
            sup.adopt(c.acts)            # the router's outcome note (J1)
        if c is not None and c.outcome is not None and c.outcome["so"]["evaluate"] != "off":
            # J1 step 6, E-b: the claim check's note carries the turn's evidence
            from tandemapp.conductor import outcomes
            sup.evidence = (c.outcome["so"]["evaluate"], outcomes.evidence_text(c.outcome))
        sup.memo["client_thinking_off"] = bool(event.get("thinking_off"))
        if event.get("loop_escape"):
            sup.memo["loop_escape"] = event["loop_escape"]   # SRV-475: the escape note
        return Watch(sup, req_ev, event)

    def extras(self, plan, point, info):
        """`start`: the request's state lines (an item of kind `lines`, Lines), when its profile
        shows them; nothing at the other points (the lines go out through the Lines object)."""
        if point != "start" or plan is None or plan.routing is None:
            return []
        got = self._lines(plan.routing, info)
        if got is None:
            return []
        plan.ui = got.ui
        return [{"kind": "lines", "data": got}]

    def _lines(self, c, info):
        """The profile's `ui.states`: TandemLLM's steps as lines the client shows (one choice, a
        profile that acts, never a side request)."""
        ui_doc = c.res.doc.get("ui") or {}
        if not (c.active and c.res.mode == "on" and info["n_choices"] == 1
                and ui_doc.get("states") == "on" and c.side is None
                and not info["json_answer"]):             # SRV-169: a JSON answer's content is the JSON
            return None
        from tandemapp.conductor import narrator as narrator_mod
        from tandemapp.conductor import states as states_mod
        from tandemapp.conductor import voice as voice_mod
        # `ui.narrator`: the narrator writes each line inside its budget (P1), the template stays
        # the fallback
        narr = self.state.get("narrator")
        lines = (narr.for_request()
                 if narr is not None and ui_doc.get("narrator") == "on" else None)
        # v3 (`ui.narrator: live`, tandemapp/conductor/voice.py): the harness's lines in plain
        # words and the user's language, the live lines beside them
        vlang = (voice_mod.lang_of(info["messages"])
                 if ui_doc.get("narrator") == "live" else None)
        ui = states_mod.States(
            ui_doc.get("kinds"), narrator=lines, voice=vlang,
            labels=narrator_mod.class_labels(self.state) if vlang is not None else None)
        return Lines(ui, ui_doc, info["fmt"])

    def _memory_takes(self, c, chat, n_choices, tool_names=()):
        """What the memory filter takes out of this request's answer: None (no filter: the stream
        is the release's), False (the blocks), True (the blocks and the `tandem_memory` calls,
        unless the client offers a tool of that name: then the calls are its own)."""
        cond = self.cond
        if (cond is None or c is None or not c.active or not chat or n_choices != 1
                or not getattr(c, "memory", None)
                or c.memory.get("mode") not in ("on", "shadow")):
            return None
        from tandemapp import memblock
        return memblock.TOOL not in (tool_names or ())

    def follow_ups(self, plan, outcome):
        """The turn's end: a compaction's session goes to the memory writer (the engine runs its
        turn now, `after_compaction: turn`), a turn that gave up marks its session for it."""
        cond = self.cond
        c = plan.routing if plan is not None else None
        if cond is None or c is None:
            return []
        if outcome.get("compaction"):
            return [{"kind": "memory_writer", "what": "after_compaction", "body": None}]
        if outcome.get("gave_up") and getattr(cond, "memory", None) is not None:
            cond.memory.writer.mark(outcome.get("conv"), "give_up")
        return []

    def finish(self, plan, row):
        """The request's journal row and ledger fields, and a compaction's summary for the
        router's session."""
        cond = self.cond
        if cond is None:
            return
        c = plan.routing if plan is not None else None
        cond.finish(c, row)
        cids = getattr(c, "compaction_ids", None)
        if cids and c.session is not None:
            try:
                cond.router.compacted_into(c.session.key,
                                           self.state["tok"].decode(cids, skip_special_tokens=True))
            except Exception:                      # noqa: BLE001
                import traceback
                traceback.print_exc()

    def stop(self):
        return None

    # ------------------------------------------------------------------ the optional calls (2.0)
    def server_mode(self, header, n):
        """The conductor's mode for this request: the server's, lowered by `X-Tandem`, and never
        more than shadow for `n > 1`."""
        mon = self.state.get("monitor")
        return mon.mode_for(header, n) if mon is not None else "raw"

    def served_name(self):
        from tandemapp.conductor.router import COMPOSITE
        return COMPOSITE

    def side_first_max(self):
        from tandemapp.conductor.schema import SIDE_FIRST_MAX_TOKENS
        return SIDE_FIRST_MAX_TOKENS

    def strip_states(self, messages):
        from tandemapp.conductor import states
        return states.strip_messages(messages)

    def code_class(self, routing):
        from tandemapp.conductor import schema
        return routing.row.get("class_id") in schema.CODE_CLASSES

    def loop_escape(self, routing, info):
        """SRV-475: the profile's `sampling` section and the loop escape's step for this session:
        {agentic_default, state, sampling(fallback)}. The engine builds the sampler."""
        from tandemapp.conductor import loopescape
        prof = loopescape.profile_sampling(routing.res.doc)
        msgs = info["body"].get("messages") or []
        st = None
        if prof["loop_escape"] == "on":
            mon = self.state.get("monitor")
            pol = self.cond.monitor_policy(routing, mon.policy if mon else None) \
                if mon is not None else None
            c3 = pol.tasks.get("C3") if pol is not None else None
            repeats = int(c3["repeats"]) if c3 is not None else loopescape.REPEATS
            wait_s = float(c3.get("wait_s", 0.0) or 0.0) if c3 is not None \
                else loopescape.WAIT_S
            key = routing.session.key if routing.session is not None else info["session"]
            sess = mon.sessions.get(key) if mon is not None else None
            sig = loopescape.signal(msgs, loopescape.waits_now(sess, msgs, time.time()),
                                    repeats=repeats, wait_s=wait_s)
            st = self.state.setdefault("escapes", loopescape.Escapes()).step(
                key, sig, loopescape.turn_of(msgs))
        return {"agentic_default": prof["agentic_default"], "state": st,
                "sampling": lambda fallback: loopescape.sampling_of(self.state.get("card_sampling"),
                                                                    fallback)}

    def served(self, routing, prompt, in_think):
        return routing.served(prompt, self.state["tok"], in_think)

    def in_context(self, routing, prompt_ids, choice, rows):
        return self.cond.decide_in_context(routing, prompt_ids, choice, rows)

    def settle_context(self, routing, why):
        return self.cond.router.settle_context(routing, why)

    def ask_in_place(self, routing, prompt_ids, choice):
        return self.cond.router.ask_in_place(routing, prompt_ids, self.state["tok"], choice)

    def route_lines(self, routing, ui):
        ui.route(routing.row, routing.injected, self.cond.store.peek("packages")[1].get("packages"),
                 routing.compaction)

    def memory_filter(self, routing, chat, n_choices, ui, tool_names):
        """The stream's memory filter (tandemapp/memblock.py) for a plan whose memory is not off:
        one choice of a chat request; None otherwise (the stream is the release's)."""
        takes = self._memory_takes(routing, chat, n_choices, tool_names)
        if takes is None:
            return None
        return self.cond.memory.filter(routing.memory, ui, calls=takes)

    def memory_spans(self, ids, decode, taken):
        from tandemapp import memblock
        return memblock.token_spans(ids, decode, taken)

    def answer_mask(self, routing, chat, body):
        """SRV-625: the secret echo mask (tandemapp/conductor/redact.py AnswerMask) for a chat
        request whose plan's profile has `secret_mask` on or shadow and whose conversation's tool
        results hold a secret span; None otherwise."""
        if not chat or routing is None or not routing.active:
            return None
        from tandemapp.conductor import redact, schema
        mode = schema.profile_secret_mask(getattr(routing.res, "doc", None) or {})
        if mode == "off":
            return None
        spans = redact.tool_spans(body.get("messages"))
        if not spans:
            return None
        return redact.AnswerMask(spans, replace=mode == "on")

    def replayed(self, session, ids):
        return self.cond.router.replayed_for("c:" + session[:120], ids, self.state["tok"])

    def turn_ended(self, supervisor):
        mon = self.state.get("monitor")
        if mon is not None:
            w = getattr(supervisor, "_obj", supervisor)
            mon.turn_ended(w._sup if isinstance(w, Watch) else w)   # the watch's idle time starts here

    # ------------------------------------------------------------------ 2.1: outside a request
    def memory_writer(self):
        """(the memory writer, its settings), or (None, None) without one."""
        cond = self.cond
        if cond is None or getattr(cond, "memory", None) is None:
            return None, None
        from tandemapp.conductor import memwriter
        return _Writer(cond), memwriter.settings_of(cond.memory.cfg())

    def document(self, name):
        """One of the conductor's documents (engine-ext-v1 `extension.documents`): [version, doc]."""
        cond = self.cond
        if cond is None:
            return None
        v, doc = cond.store.peek(name)
        return [v, doc]

    def library_text(self, name):
        cond = self.cond
        if cond is None:
            return None
        text, _ = cond.library.text(cond.store.peek("packages")[1], name)
        return text

    def shadow_wanted(self):
        """Does some profile have `shadow.mode: on`?"""
        cond = self.cond
        if cond is None:
            return False
        for name in cond.store.names():
            try:
                _, doc = cond.store.peek("profiles", name)
            except Exception:                                      # noqa: BLE001
                continue
            if ((doc or {}).get("shadow") or {}).get("mode", "off") == "on":
                return True
        return False

    def monitor_mode(self):
        mon = self.state.get("monitor")
        return mon.mode if mon is not None else None

    def narrator(self, slot):
        """The narrator over the engine's slot (its roles document's `narrator`, the router's labels)."""
        from tandemapp.conductor import narrator as narr_mod
        state = self.state
        return narr_mod.Narrator(slot, role=lambda: narr_mod.role_doc(state),
                                 labels=lambda: narr_mod.class_labels(state))

    def role_errors(self, fields, name, tokenizer_sha):
        from tandemapp.conductor import schema
        return schema.role_errors(fields, name, tokenizer_sha)

    def deciders(self, registry):
        """The conductor's in-process client of the engine's decision models: the classify backends
        `self` and each family."""
        from tandemapp.conductor import classify

        def factory(model, timeout_ms):
            return classify.InProcess(registry, model, timeout_ms)

        classify.register_backend("self", factory)
        for fam in registry.families:
            classify.register_backend(fam, factory)

    def deciders_api(self, doc):
        """The Deciders screen's api part: the deciders document's api section and Laya's counts."""
        from tandemapp import laya_api
        from tandemapp.conductor import schema
        return {**schema.decider_api(doc), "stats": laya_api.stats()}

    def writer_ban_ids(self, tokenizer):
        from tandemapp.conductor import memwriter
        return memwriter.ban_ids(tokenizer)

    def writer_foreign_call(self, text):
        from tandemapp.conductor import memwriter
        return memwriter.foreign_call(text)

    def probe_stage(self, routing):
        from tandemapp.conductor import schema
        return schema.probe_stage(routing.router_cfg)

    # ------------------------------------------------------------------ routes: (status, headers, body)
    def _conductor(self, request):
        from tandemapp.conductor import routes as conductor_routes
        r = _Reply(request)
        conductor_routes.handle(r, self.cond, request.method, request.body, request.auth)
        return r.answer()

    def _library(self, request):
        from tandemapp import library_api
        r = _Reply(request)
        library_api.handle_get(r, self.cond)
        return r.answer()

    def _memory(self, request):
        from tandemapp import memory_api
        r = _Reply(request)
        memory_api.handle(r, self.cond, request.method,
                          request.body if request.method == "POST" else None)
        return r.answer()

    def _page(self, request):
        from tandemapp import static
        r = _Reply(request)
        static.serve(r, r.path, self.state.get("dashboard_dir"))
        return r.answer()

    # ------------------------------------------------------------------ start
    def start_monitor(self) -> None:
        """The session monitor (`--conductor shadow|on`, its policy validated before the weights
        load by before_load), as the engine's start built it before ENG-413 WP3b."""
        state, args = self.state, self.host.args
        pol = _policy(state)
        if pol is None:
            return
        from tandemapp.conductor import monitor as conductor
        mode = conductor.parse_mode(args.get("conductor", "raw"), "raw")
        mon = state["monitor"] = conductor.Monitor(mode, pol, state["tok"])
        # the monitor's decisions are counted in Prometheus (SRV-115); its rows go into the
        # conductor's journal, opened with the ledger
        mon.journal.sinks.append(self.host.metrics.event)
        if args.get("fake_engine"):
            # a test run never pushes to a phone, whatever its policy says
            mon.notifier = lambda cfg, title, message: "suppressed: fake engine"
        if mode != "raw":
            mon.start_watch(float(args.get("conductor_watch_s", 30.0)))

    def boot(self) -> None:
        """What the engine's start did for the conductor (until ENG-413 WP3a) and its session
        monitor (until WP3b), in its order."""
        host, state, log = self.host, self.state, self.host.log
        self.start_monitor()
        from tandemapp.conductor import loopescape
        state.setdefault("escapes", loopescape.Escapes())   # SRV-475: the engine made them at start
        root = host.ext_dir
        if root is None:
            # the session monitor without the conductor (`--conductor shadow|on` alone): its
            # journal (SRV-115); with the ledger off it lives in memory only
            from tandemapp.conductor.decisions import Journal as CondJournal
            journal = state["journal"] = CondJournal(host.ledger_path, retention_days=30)
            state["monitor"].journal.sinks.append(journal.monitor_sink)
            log(f"[conductor] journal: {journal.path or 'in memory (no ledger)'}, 30 days")
            return
        if host.args.get("conductor_defaults", "builtin") == "shipped":
            # The shipped documents a box has no version of yet, as version 1
            from tandemapp.conductor import shipped
            for line in shipped.install_at(root):
                log(f"[conductor] shipped default stored: {line}")
        # One journal for the router and the monitor (SRV-115): the table `conductor_events` in the
        # usage ledger's file (schema version 2), written from its own queue thread; with the ledger
        # off it lives in memory only
        from tandemapp.conductor.decisions import Journal as CondJournal
        from tandemapp.conductor.store import Store
        days = Store(root, readonly=True).get("settings")[1]["journal"]["retention_days"]
        journal = state["journal"] = CondJournal(host.ledger_path, retention_days=days)
        mon = state.get("monitor")
        if mon is not None:
            mon.journal.sinks.append(journal.monitor_sink)
        log(f"[conductor] journal: {journal.path or 'in memory (no ledger)'}, {days} days")
        from tandemapp.conductor.hook import Conductor
        from tandemapp.conductor import schema as cond_schema
        cond_schema.SERVED_NAME = host.identity_name()
        from tandemapp.conductor import modelfit as cond_fit
        _caps = getattr(state.get("engine"), "caps", None)
        log(cond_fit.install(getattr(_caps, "conductor", None)))
        cond = state["conductor"] = Conductor(
            root, state=state, journal=journal,
            monitor_seed=mon.policy.data if mon is not None else None)
        # engine-ext-v1 2.1: the engine's screens for the conductor's old paths, and the speed-guard
        # report the release tree ships (the Compare screen)
        cond.engine, cond.shipped_guard = _screens(host), getattr(host, "shipped_guard", None)
        import os
        legacy = os.path.join(cond.root, "decisions.sqlite3")
        if journal.path and os.path.exists(legacy) and not os.path.exists(legacy + ".imported"):
            # the router's rows from before the journal moved into the ledger, copied once
            n = journal.import_legacy(legacy)
            with open(legacy + ".imported", "w") as f:
                f.write(f"{n} rows copied into {journal.path} on {time.ctime()}\n")
            log(f"[conductor] journal: {n} rows imported from {legacy}")
        cond.router.session_tokens = state["session_tokens"]
        # SPD-249: no Qwen question whose answer cannot come in time
        cond.router.own_eta = host.own_eta
        # VIS-119: the shadow questions after their turn wait for an idle server
        cond.router.own_idle = host.own_idle
        # stored documents a fix must reach (a shipped default reaches only a box without one)
        from tandemapp.conductor import migrations as cond_migrations
        cond_migrations.run(cond.store, cond.journal)
        st = cond.store.status()["documents"]
        log(f"[conductor] on: {cond.root} (router v{st['router']['version']}, "
            f"{len(cond.store.names())} profiles); model TandemLLM, {state['model']} a hidden "
            f"alias, raw by X-Tandem: raw")


def start(host) -> Ext:
    """engine-ext-v1 `start(host)`: the conductor needs a directory (chat mode), the session
    monitor alone `--conductor shadow|on`; with neither there is nothing to run."""
    if getattr(host, "ext_dir", None) is None and _policy(host.state) is None:
        raise RuntimeError("tandemapp.engine_ext runs the conductor or its session monitor: it "
                           "needs a conductor directory (--conductor-config, chat mode) or "
                           "--conductor shadow|on")
    ext = Ext(host)
    ext.boot()
    return ext


def attach(host) -> Ext:
    """Tests: the extension object around a conductor or a session monitor the test built itself
    (`host.state["conductor"]`, `host.state["monitor"]`, read at each request), without its start
    (no shipped documents, journal or migrations)."""
    if "escapes" not in host.state:
        from tandemapp.conductor import loopescape
        host.state["escapes"] = loopescape.Escapes()
    cond = host.state.get("conductor")
    if cond is not None:
        cond.engine, cond.shipped_guard = _screens(host), getattr(host, "shipped_guard", None)
    return Ext(host)
