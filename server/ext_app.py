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

The request path (the router's plan, the session monitor in the decode loop, the stream's extras,
the follow-ups, the journal rows) still reads the conductor from the server's state, as before; ENG-413
WP3b asks it through the hooks below instead, which until then only name what each maps to. WP4 moves
this body into TandemApp (tandemapp/engine_ext.py, no engine import) and deletes this file; until then
it is the one engine file besides the request path's that imports TandemApp's conductor, and it reads
what it still needs of the engine beyond the interface from `host.interim`.
"""

from __future__ import annotations

import json
import os
import time

EXT_API = "1.1"
NAME = "tandemapp"
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
        """The router's plan (WP3b asks it here; until then server/app.py calls the conductor)."""
        cond = self.cond
        if cond is None:
            return None
        return cond.plan(request["body"], request["headers"], client_kind=request["client_kind"],
                         request_id=request["request_id"], chat=request["chat"],
                         count_tokens=request.get("count_tokens"), user=request.get("user"))

    def prompt(self, plan, ids):
        return list(ids)

    def supervisor(self, plan, event):
        return None

    def extras(self, plan, point, info):
        return []

    def follow_ups(self, plan, outcome):
        return []

    def finish(self, plan, row):
        cond = self.cond
        if cond is not None:
            cond.finish(plan, row)

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
    def boot(self) -> None:
        """What server/app.py's start did for the conductor (until ENG-413 WP3a), in its order."""
        i, state, log = self.host.interim, self.state, self.host.log
        root = i.root
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
    if i is None or getattr(i, "root", None) is None:
        raise RuntimeError("the interim adapter runs the engine's conductor: it needs a conductor "
                           "directory (--conductor-config, chat mode)")
    ext = Interim(host)
    ext.boot()
    return ext


def attach(state: dict, *, log=print):
    """Tests: this adapter as the loaded extension around a conductor the test built itself
    (`state["conductor"]`), without its start (no shipped documents, journal or migrations)."""
    import sys
    from server import ext
    host = ext.Host(mode="chat", engine_api="", state=state, lock=None, tokenizer=state.get("tok"),
                    registry=None, ledger_path=None, args={}, log=log)
    return ext.Loaded(sys.modules[__name__], Interim(host), host)
