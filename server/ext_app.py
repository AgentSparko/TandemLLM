"""ENG-413 WP3a's interim adapter: the conductor that runs in the engine's process today, reached
through the engine-ext-v1 host (server/ext.py) as an extension. `QSE_EXT=server.ext_app` in chat mode.

It holds what server/app.py's start did for the conductor and the routes TandemApp's code answers in
the engine's process, unchanged in what they do:

    start(host)     the shipped documents, the conductor's journal (the `conductor_events` table in
                    the ledger's file), the Conductor and its router's wiring, the migrations; the
                    same lines in the log as before
    routes          /v1/conductor/* (its routes), /v1/library* (the package library), /v1/memory*
                    (the model's own notes), /dashboard* (the page, until it leaves the engine)
    models          `TandemLLM`, read from the conductor's settings at every /v1/models answer
    health()        its part of /health `loaded`

ENG-413 WP3b: the request path asks the conductor and its session monitor only through this adapter:
the hooks (plan, prompt, supervisor and the decode loop's safe points, extras, follow_ups, finish)
and the interim calls server/ext.py names (INTERIM, SUPERVISION_INTERIM), each the code that ran in
server/app.py before, moved here unchanged in what it does. The session monitor is started here too,
also without a conductor directory (`--conductor on|shadow` alone, MONITOR_ALONE). WP4 moves this
body into TandemApp (tandemapp/engine_ext.py, no engine import) and deletes this file; until then it
is the one engine file that imports TandemApp's conductor, and it reads what it still needs of the
engine beyond the interface from `host.interim`.
"""

from __future__ import annotations

import dataclasses
import json
import os
import time

EXT_API = "1.2"
NAME = "tandemapp"
#: ENG-413 WP3b: the session monitor runs behind this adapter with no conductor directory too
MONITOR_ALONE = True
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _version() -> str:
    """`interim+<vendored TandemApp commit>` (app.pin), the code this adapter wraps."""
    try:
        with open(os.path.join(_REPO, "app.pin")) as f:
            for line in f:
                if line.startswith("commit "):
                    return "interim+" + line.split()[1][:12]
    except OSError:
        pass
    return "interim"


VERSION = _version()


def _knobs() -> list:
    try:
        with open(os.path.join(_REPO, "contracts", "engine-ext-v1", "knobs.json")) as f:
            doc = json.load(f)
        return [doc["prefix"] + k for k in doc["knobs"]]
    except (OSError, ValueError, KeyError):
        return []


class XPlan:
    """The plan this adapter hands the engine (engine-ext-v1 `plan`): `active` and `body` as the
    interface says, and `cond`, the conductor's own plan (None with the monitor alone), which the
    engine's request path still reads until ENG-413 WP4. `server_mode` is the monitor's mode for the
    request before its profile lowers it (`Monitor.mode_for`); `ui` the state lines `extras` made.
    """

    __slots__ = ("cond", "request", "server_mode", "ui")
    body = None

    def __init__(self, cond, request: dict, server_mode: str):
        self.cond, self.request, self.server_mode = cond, request, server_mode
        self.ui = None

    @property
    def active(self) -> bool:
        return bool(self.cond is not None and self.cond.active)


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
    monitor its own; every other name (the interim ones the engine still asks, the state it reads
    and writes) is the Supervisor's."""

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
        """E2's questions asked in place (the engine's `asker`); the engine's NoRoom is the
        in-context stage's `no_room`."""
        return self._sup.settle_claim(asker)

    def __getattr__(self, name):
        return getattr(self._sup, name)

    def __setattr__(self, name, value):
        setattr(self._sup, name, value)


class Lines:
    """The state lines of one request (engine-ext-v1 `extras` at `start`, an item of kind `lines`):
    the conductor's States object (`ui`), its profile's `ui` section (`doc`), the channel and the
    progress settings, and the helpers the stream writes them with. Interim (ENG-413 WP3b): the
    engine still calls into it until WP4 gives the stream's lines a shape of their own."""

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


class Seams:
    """The interim calls (server/ext.py INTERIM): what server/app.py's request path ran of the
    conductor's before ENG-413 WP3b, each moved here unchanged. `plan` is the conductor's own plan."""

    def __init__(self, ext: "Interim"):
        self.ext = ext
        self.state = ext.state

    @property
    def cond(self):
        return self.state.get("conductor")

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

    def code_class(self, plan):
        from tandemapp.conductor import schema
        return plan.row.get("class_id") in schema.CODE_CLASSES

    def loop_escape(self, plan, info):
        """SRV-475: the profile's `sampling` section and the loop escape's step for this session:
        {agentic_default, state, sampling(fallback)}. The engine builds the sampler."""
        from tandemapp.conductor import loopescape
        prof = loopescape.profile_sampling(plan.res.doc)
        msgs = info["body"].get("messages") or []
        st = None
        if prof["loop_escape"] == "on":
            mon = self.state.get("monitor")
            pol = self.cond.monitor_policy(plan, mon.policy if mon else None) \
                if mon is not None else None
            c3 = pol.tasks.get("C3") if pol is not None else None
            repeats = int(c3["repeats"]) if c3 is not None else loopescape.REPEATS
            wait_s = float(c3.get("wait_s", 0.0) or 0.0) if c3 is not None \
                else loopescape.WAIT_S
            key = plan.session.key if plan.session is not None else info["session"]
            sess = mon.sessions.get(key) if mon is not None else None
            sig = loopescape.signal(msgs, loopescape.waits_now(sess, msgs, time.time()),
                                    repeats=repeats, wait_s=wait_s)
            st = self.state.setdefault("escapes", loopescape.Escapes()).step(
                key, sig, loopescape.turn_of(msgs))
        return {"agentic_default": prof["agentic_default"], "state": st,
                "sampling": lambda fallback: loopescape.sampling_of(self.state.get("card_sampling"),
                                                                    fallback)}

    def served(self, plan, prompt, in_think):
        return plan.served(prompt, self.state["tok"], in_think)

    def in_context(self, plan, prompt_ids, choice, rows):
        return self.cond.decide_in_context(plan, prompt_ids, choice, rows)

    def settle_context(self, plan, why):
        return self.cond.router.settle_context(plan, why)

    def ask_in_place(self, plan, prompt_ids, choice):
        return self.cond.router.ask_in_place(plan, prompt_ids, self.state["tok"], choice)

    def route_lines(self, plan, ui):
        ui.route(plan.row, plan.injected, self.cond.store.peek("packages")[1].get("packages"),
                 plan.compaction)

    def memory_filter(self, plan, chat, n_choices, ui, tool_names):
        """The stream's memory filter (app/tandemapp/memblock.py) for a plan whose memory is not off:
        one choice of a chat request; None otherwise (the stream is the release's)."""
        takes = self.ext._memory_takes(plan, chat, n_choices, tool_names)
        if takes is None:
            return None
        return self.cond.memory.filter(plan.memory, ui, calls=takes)

    def memory_spans(self, ids, decode, taken):
        from tandemapp import memblock
        return memblock.token_spans(ids, decode, taken)

    def answer_mask(self, plan, chat, body):
        """SRV-625: the secret echo mask (app/tandemapp/conductor/redact.py AnswerMask) for a chat
        request whose plan's profile has `secret_mask` on or shadow and whose conversation's tool
        results hold a secret span; None otherwise."""
        if not chat or plan is None or not plan.active:
            return None
        from tandemapp.conductor import redact, schema
        mode = schema.profile_secret_mask(getattr(plan.res, "doc", None) or {})
        if mode == "off":
            return None
        spans = redact.tool_spans(body.get("messages"))
        if not spans:
            return None
        return redact.AnswerMask(spans, replace=mode == "on")

    def replayed(self, session, ids):
        return self.cond.router.replayed_for("c:" + session[:120], ids, self.state["tok"])

    def turn_ended(self, sup):
        mon = self.state.get("monitor")
        if mon is not None:
            w = getattr(sup, "_obj", sup)
            mon.turn_ended(w._sup if isinstance(w, Watch) else w)   # the watch's idle time starts here

    def writer(self):
        """(the conductor's memory, the writer's settings), or (None, None) without one."""
        cond = self.cond
        if cond is None or getattr(cond, "memory", None) is None:
            return None, None
        from tandemapp.conductor import memwriter
        return cond.memory, memwriter.settings_of(cond.memory.cfg())

    def writer_ban_ids(self, tok):
        from tandemapp.conductor import memwriter
        return memwriter.ban_ids(tok)

    def writer_foreign_call(self, text):
        from tandemapp.conductor import memwriter
        return memwriter.foreign_call(text)

    def probe_stage(self, plan):
        from tandemapp.conductor import schema
        return schema.probe_stage(plan.router_cfg)


class Interim:
    """The extension object: its attributes, its hooks, its routes' handlers."""

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
        self.interim = Seams(self)

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
        mode = self.interim.server_mode(request["headers"].get("X-Tandem"), n)
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
        c = plan.cond if plan is not None else None
        info = info or {}
        in_think = bool(info.get("in_think"))
        if c is None:
            return {"ids": list(ids), "in_think": in_think, "client_ids": None}
        import torch
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
        c, cond = plan.cond, self.cond
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
        if point != "start" or plan is None or plan.cond is None:
            return []
        got = self._lines(plan.cond, info)
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
        # v3 (`ui.narrator: live`, app/tandemapp/conductor/voice.py): the harness's lines in plain
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
        c = plan.cond if plan is not None else None
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
        c = plan.cond if plan is not None else None
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

    # ------------------------------------------------------------------ routes (interim: they answer
    # on the engine's HTTP handler, `request.handler`, and return None; WP4 returns the contract's
    # (status, headers, body))
    def _conductor(self, request):
        from tandemapp.conductor import routes as conductor_routes
        return conductor_routes.handle(request.handler, self.cond, request.method, request.body,
                                       request.auth)

    def _library(self, request):
        from tandemapp import library_api
        return library_api.handle_get(request.handler, self.cond)

    def _memory(self, request):
        from server import memory_api
        return memory_api.handle(request.handler, self.cond, request.method,
                                 request.body if request.method == "POST" else None)

    def _page(self, request):
        from tandemapp import static
        return static.serve(request.handler, request.handler.path, self.state.get("dashboard_dir"))

    # ------------------------------------------------------------------ start
    def start_monitor(self) -> None:
        """The session monitor (`--conductor shadow|on`, its policy validated before the weights
        load: STATE["conductor_policy"]), as server/app.py's start built it before ENG-413 WP3b."""
        i, state, args = self.host.interim, self.state, self.host.args
        pol = state.get("conductor_policy")
        if pol is None:
            return
        from tandemapp.conductor import monitor as conductor
        mode = conductor.parse_mode(args.get("conductor", "raw"), "raw")
        mon = state["monitor"] = conductor.Monitor(mode, pol, state["tok"])
        # the monitor's decisions are counted in Prometheus (SRV-115); its rows go into the
        # conductor's journal, opened with the ledger
        mon.journal.sinks.append(i.on_conductor_event)
        if args.get("fake_engine"):
            # a test run never pushes to a phone, whatever its policy says
            mon.notifier = lambda cfg, title, message: "suppressed: fake engine"
        if mode != "raw":
            mon.start_watch(float(args.get("conductor_watch_s", 30.0)))

    def boot(self) -> None:
        """What server/app.py's start did for the conductor (until ENG-413 WP3a) and its session
        monitor (until WP3b), in its order."""
        i, state, log = self.host.interim, self.state, self.host.log
        self.start_monitor()
        from tandemapp.conductor import loopescape
        state["escapes"] = loopescape.Escapes()        # SRV-475: the loop escape's sessions
        root = i.root
        if root is None:
            # the session monitor without the conductor (`--conductor shadow|on` alone): its
            # journal (SRV-115); with the ledger off it lives in memory only
            from tandemapp.conductor.decisions import Journal as CondJournal
            journal = state["journal"] = CondJournal(self.host.ledger_path, retention_days=30)
            state["monitor"].journal.sinks.append(journal.monitor_sink)
            log(f"[conductor] journal: {journal.path or 'in memory (no ledger)'}, 30 days")
            return
        if i.defaults == "shipped":
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
        journal = state["journal"] = CondJournal(self.host.ledger_path, retention_days=days)
        mon = state.get("monitor")
        if mon is not None:
            mon.journal.sinks.append(journal.monitor_sink)
        log(f"[conductor] journal: {journal.path or 'in memory (no ledger)'}, {days} days")
        from tandemapp.conductor.hook import Conductor
        from tandemapp.conductor import schema as cond_schema
        cond_schema.SERVED_NAME = i.identity_name()
        from tandemapp.conductor import modelfit as cond_fit
        _caps = getattr(state.get("engine"), "caps", None)
        log(cond_fit.install(getattr(_caps, "conductor", None)))
        cond = state["conductor"] = Conductor(
            root, state=state, journal=journal,
            monitor_seed=mon.policy.data if mon is not None else None)
        legacy = os.path.join(cond.root, "decisions.sqlite3")
        if journal.path and os.path.exists(legacy) and not os.path.exists(legacy + ".imported"):
            # the router's rows from before the journal moved into the ledger, copied once
            n = journal.import_legacy(legacy)
            with open(legacy + ".imported", "w") as f:
                f.write(f"{n} rows copied into {journal.path} on {time.ctime()}\n")
            log(f"[conductor] journal: {n} rows imported from {legacy}")
        cond.router.session_tokens = state["session_tokens"]
        # SPD-249: no Qwen question whose answer cannot come in time
        cond.router.own_eta = i.own_eta
        # VIS-119: the shadow questions after their turn wait for an idle server
        cond.router.own_idle = i.own_idle
        # stored documents a fix must reach (a shipped default reaches only a box without one)
        from tandemapp.conductor import migrations as cond_migrations
        cond_migrations.run(cond.store, cond.journal)
        st = cond.store.status()["documents"]
        log(f"[conductor] on: {cond.root} (router v{st['router']['version']}, "
            f"{len(cond.store.names())} profiles); model TandemLLM, {state['model']} a hidden "
            f"alias, raw by X-Tandem: raw")


def start(host) -> Interim:
    i = getattr(host, "interim", None)
    if i is None or (getattr(i, "root", None) is None
                     and (host.state or {}).get("conductor_policy") is None):
        raise RuntimeError("the interim adapter runs the engine's conductor or its session monitor: "
                           "it needs a conductor directory (--conductor-config, chat mode) or "
                           "--conductor shadow|on")
    ext = Interim(host)
    ext.boot()
    return ext


def attach(state: dict, *, log=print):
    """Tests: this adapter as the loaded extension around a conductor or a session monitor the test
    built itself (`state["conductor"]`, `state["monitor"]`, read at each request), without its start
    (no shipped documents, journal or migrations)."""
    import sys
    from server import ext
    host = ext.Host(mode="chat", engine_api="", state=state, lock=None, tokenizer=state.get("tok"),
                    registry=None, ledger_path=None, args={}, log=log)
    if "escapes" not in state:
        from tandemapp.conductor import loopescape
        state["escapes"] = loopescape.Escapes()
    return ext.Loaded(sys.modules[__name__], Interim(host), host)
