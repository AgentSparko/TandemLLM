"""The engine-ext-v1 host (ENG-413 WP3a, ENG-429): how code that is not the engine's runs inside it.

contracts/engine-ext-v1 is the interface (ext.json, README.md); this module is the engine's side of
it. Standard library only, plus the engine's metrics registry for one counter.

    module = discover(os.environ.get("QSE_EXT", ""), mode)   before the weights load; None: headless
    ext = start(module, Host(...))                          once the served model is loaded
    ext.listing()            /health `extensions`: [{name, version, ext_api}], [] when headless
    ext.health()             the extension's part of /health `loaded` ({} when it fails)
    ext.models()             its /v1/models entries; ext.card(name, raw) one of them, or None
    ext.route(method, path)  the handler of a path it serves, or None
    ext.request(request)     one request's hooks (Request), open on any failure
    ext.interim(name, ...)   one of the interim calls (INTERIM), open; the fallback when headless
    ext.stop()               the engine stops

ENG-413 WP3b: the request path asks these hooks and nothing of the conductor. `Request` asks them in
ext.json's order; `Supervision` is the supervisor as the decode loop asks it at SP1-SP4, each act
checked against its method and every failure detaching it for the rest of the request. The acts
are the engine's (the kinds below), carried out by the engine. INTERIM names what the request path
still asks the interim adapter (server/ext_app.py) outside the contract: each with the value a
headless engine uses; ENG-413 WP4 turns them into hooks or moves them out.

The failure rule (ext.json `failure`): closed at start, open per request. `discover` and `start`
raise Refused, a SystemExit whose text is the one line `[ext] REFUSING: <why>`: a module that does
not import, no EXT_API or one this host does not take, a module attribute or hook missing, start
raising, a route on a path the engine serves, or chat mode asked with QSE_EXT empty. The engine
never falls back to headless when an extension was asked for. Once it runs, a hook that raises or
answers the wrong shape is logged, counted in `qse_ext_errors_total{hook}` and the rest of that
request goes on as a headless engine would serve it; a `Refusal` raised by `plan` is the client's
answer, not a failure. NULL is the headless engine: no routes, no models, no hooks asked.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import threading

#: the engine-ext-v1 version this host speaks (contracts/engine-ext-v1/ext.json `version`)
EXT_API = "1.2"
#: the module attributes discovery reads (ext.json discovery.module)
MODULE_ATTRS = ("EXT_API", "NAME", "VERSION", "start")
#: the extension's hooks and their arguments (ext.json extension.hooks)
HOOKS = {"health": [], "plan": ["request"], "prompt": ["plan", "ids", "info"],
         "supervisor": ["plan", "event"], "extras": ["plan", "point", "info"],
         "follow_ups": ["plan", "outcome"], "finish": ["plan", "row"], "stop": []}
#: the extension's attributes, lists (ext.json extension.attributes)
ATTRS = ("routes", "models", "knobs")
#: the supervisor's methods, their arguments and safe points (ext.json supervisor.methods)
SUPERVISOR = {"at_request": (["event"], "SP4"), "at_block": (["event", "room"], "SP1"),
              "at_tool_call": (["event"], "SP3"), "check_answer": (["items", "final"], "SP3"),
              "at_finish": (["event", "room"], "SP2"), "done": (["outcome", "detail"], "end")}
#: the acts each safe point allows (ext.json supervisor.safe_points)
SAFE_POINTS = {"SP1": ("CLOSE_REASONING", "GIVE_UP"), "SP2": ("CONTINUE_HIDDEN", "GIVE_UP", "REGENERATE_CALL"),
               "SP3": ("REPAIR_CALL", "REGENERATE_CALL", "GIVE_UP"),
               "SP4": ("PROMPT_TAIL_NOTE", "SET_THINK_BUDGET")}
#: the request fields `plan` gets (ext.json request.fields)
REQUEST_FIELDS = ("body", "headers", "client_kind", "request_id", "chat", "user", "count_tokens")
#: paths the engine serves itself; an extension that claims one is refused at start (the same list
#: as contracts/engine-ext-v1/fake_host.py, held equal by tests/test_engine_ext_host.py)
ENGINE_PREFIXES = ("/v1/chat/completions", "/v1/completions", "/v1/embeddings", "/v1/responses",
                   "/v1/models", "/health", "/healthz", "/v1/health", "/metrics", "/readyz",
                   "/v1/replica/", "/v1/tokenize", "/v1/cache/", "/v1/setup/", "/v1/engine/",
                   "/v1/systemone", "/v1/predict", "/_tandem/")
ENGINE_DASHBOARD = ("live", "system", "logs", "parallel", "metrics", "summary", "usage", "requests")


#: the engine's own exceptions that pass through an extension's code (the engine's callbacks raise
#: them inside a hook): the client left. Not a failure of the extension: they go on to the engine
PASS_THROUGH = (ConnectionError,)


class Refused(SystemExit):
    """The engine does not start: one line, `[ext] REFUSING: <why>` (SystemExit prints it, exit 1)."""

    def __init__(self, why: str):
        super().__init__(f"[ext] REFUSING: {why}")
        self.why = why


class NoRoom(ValueError):
    """A question asked in place has no room left in the window (the in-context stage's `no_room`)."""

    def __init__(self, why: str = "no_room"):
        super().__init__(why)
        self.why = why


class Refusal(Exception):
    """What `plan` raises to answer the client with an error (ext.json failure.refusal)."""

    def __init__(self, status: int, type_: str, message: str, param: str | None = None):
        super().__init__(message)
        self.status, self.type, self.message = int(status), str(type_), str(message)
        self.param = param                       # since 1.2: the request field it names, or None

    def body(self) -> dict:
        err = {"message": self.message, "type": self.type}
        if self.param is not None:
            err["param"] = self.param
        return {"error": err}


def _version(text) -> tuple[int, int] | None:
    if not isinstance(text, str) or not re.fullmatch(r"[0-9]+\.[0-9]+", text):
        return None
    major, minor = text.split(".")
    return int(major), int(minor)


def accepts(ext_api, host_api: str = EXT_API) -> str | None:
    """None when a host on `host_api` takes an extension written against `ext_api`, else why not."""
    want, have = _version(ext_api), _version(host_api)
    if want is None:
        return f"EXT_API {ext_api!r} is not MAJOR.MINOR"
    if want[0] != have[0]:
        return f"the extension speaks engine-ext-v1 {ext_api}; this engine speaks {host_api}"
    if want[1] > have[1]:
        return f"the extension needs engine-ext-v1 {ext_api}; this engine speaks {host_api}"
    return None


def claims_engine_path(prefix: str) -> bool:
    """Whether a route prefix reaches a path the engine serves itself."""
    if prefix.startswith("/v1/dashboard/"):
        return prefix[len("/v1/dashboard/"):].split("/")[0] in ENGINE_DASHBOARD
    return any(prefix.startswith(p) or p.startswith(prefix.rstrip("*")) for p in ENGINE_PREFIXES)


def ext_args(args: dict, env=os.environ) -> dict:
    """`host.args`: the parsed command line, and QSE_EXT_ARGS (a JSON object) over it."""
    out = dict(args or {})
    raw = (env.get("QSE_EXT_ARGS") or "").strip()
    if raw:
        try:
            extra = json.loads(raw)
        except ValueError as exc:
            raise Refused(f"QSE_EXT_ARGS is not JSON: {exc}") from None
        if not isinstance(extra, dict):
            raise Refused("QSE_EXT_ARGS is not a JSON object")
        out.update(extra)
    return out


def discover(name: str | None, mode: str):
    """The extension's module for QSE_EXT=`name` in `mode` (engine or chat), or None for a headless
    engine. Closed: Refused before the weights load."""
    name = (name or "").strip()
    if not name:
        if mode == "chat":
            raise Refused("chat mode needs an extension (QSE_EXT is empty)")
        return None
    try:
        module = importlib.import_module(name)
    except BaseException as exc:                                   # noqa: BLE001
        raise Refused(f"QSE_EXT={name} does not import: {exc!r}") from None
    return check_module(module)


def check_module(module):
    """The module's attributes and its EXT_API, closed: the module, or Refused."""
    for attr in MODULE_ATTRS:
        if not hasattr(module, attr):
            raise Refused(f"{module.__name__} has no {attr}")
    why = accepts(module.EXT_API)
    if why:
        raise Refused(why)
    return module


class Host:
    """What the engine hands an extension (ext.json `host`). `interim`: what ENG-413 WP3a's
    interim adapter (server/ext_app.py) still reads of the engine beyond the interface; WP4 ends it."""

    def __init__(self, *, mode: str, engine_api: str, state, lock, tokenizer, registry,
                 ledger_path, args: dict, engine: dict | None = None, log=print, interim=None):
        self.ext_api = EXT_API
        self.engine_api = engine_api
        self.mode = mode
        self.state = state
        self.lock = lock
        self.tokenizer = tokenizer
        self.metrics = _Families(registry)
        self.ledger_path = ledger_path
        self.args = args
        self.engine = dict(engine or {})
        self._log = log
        self.Refusal = Refusal
        self.interim = interim

    def log(self, line: str) -> None:
        self._log(line)


class _Families:
    """The registry handle: `add(family)` for the families metrics-families.json lists as `ext`."""

    def __init__(self, registry):
        self._registry = registry

    def add(self, family):
        return self._registry.add(family) if self._registry is not None else family


def _count(hook: str) -> None:
    try:
        from server.api import metrics
        metrics.ext_errors_total.inc(hook=hook)
    except Exception:                                              # noqa: BLE001
        pass


def start(module, host: Host) -> "Loaded":
    """`module.start(host)`, then the extension's hooks, attributes and routes. Closed: Refused."""
    name = getattr(module, "__name__", str(module))
    try:
        obj = module.start(host)
    except BaseException as exc:                                   # noqa: BLE001
        raise Refused(f"{name}.start failed: {exc!r}") from None
    missing = [h for h in HOOKS if not callable(getattr(obj, h, None))]
    if missing:
        raise Refused(f"{name} lacks the hooks {missing}")
    for attr in ATTRS:
        if not isinstance(getattr(obj, attr, None), list):
            raise Refused(f"{name}'s {attr} is not a list")
    for r in obj.routes:
        prefix = r.get("prefix") if isinstance(r, dict) else None
        if not isinstance(prefix, str) or not prefix.startswith("/") or not callable(r.get("handler")):
            raise Refused(f"{name} has a route that is not {{method, prefix, handler}}: {r!r}")
        if claims_engine_path(prefix):
            raise Refused(f"{name} claims the engine's path {prefix}")
    return Loaded(module, obj, host)


class Null:
    """A headless engine: no extension, nothing asked."""

    active = False
    name = None

    def listing(self) -> list:
        return []

    def health(self) -> dict:
        return {}

    def models(self) -> list:
        return []

    def card(self, name: str, raw_model: str) -> dict | None:
        return None

    def route(self, method: str, path: str):
        return None

    def request(self, request: dict) -> "Request":
        return Request(None, request)

    def interim(self, name: str, *args, **kw):
        """INTERIM's headless value: nothing is asked."""
        return INTERIM[name](*args, **kw)

    def stop(self) -> None:
        return None


NULL = Null()


def _matches(prefix: str, path: str) -> bool:
    if prefix.endswith("/"):
        return path.startswith(prefix) or path == prefix.rstrip("/")
    return path == prefix or path.startswith(prefix + "/")


class Loaded(Null):
    """An extension that started: its hooks asked through `call`, open on every failure."""

    active = True

    def __init__(self, module, obj, host: Host):
        self.module, self.obj, self.host = module, obj, host
        self.name = str(getattr(module, "NAME", ""))
        self.version = str(getattr(module, "VERSION", ""))
        self.ext_api = str(getattr(module, "EXT_API", ""))
        self.errors: dict[str, int] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ the failure rule
    def failed(self, hook: str, exc: BaseException, what: str = "the request goes on without it") -> None:
        with self._lock:
            self.errors[hook] = self.errors.get(hook, 0) + 1
        _count(hook)
        try:
            self.host.log(f"[ext] {hook} failed, {what}: {exc!r}")
        except Exception:                                          # noqa: BLE001
            pass

    def call(self, hook: str, *args, fallback=None, check=None):
        """One hook, open: a raise or a wrong shape is logged, counted and `fallback` comes back.
        A Refusal (only `plan` may raise one) passes to the caller."""
        try:
            out = getattr(self.obj, hook)(*args)
            if check is not None and not check(out):
                raise TypeError(f"{hook} returned {type(out).__name__}")
            return out
        except Refusal:
            if hook == "plan":
                raise
            self.failed(hook, TypeError(f"{hook} raised a Refusal"))
            return fallback
        except PASS_THROUGH:
            raise
        except Exception as exc:                                   # noqa: BLE001
            self.failed(hook, exc)
            return fallback

    # ------------------------------------------------------------------ what the engine serves
    def listing(self) -> list:
        return [{"name": self.name, "version": self.version, "ext_api": self.ext_api}]

    def health(self) -> dict:
        return self.call("health", fallback={}, check=lambda v: isinstance(v, dict))

    def models(self) -> list:
        """Read at every /v1/models answer, so an extension's list may follow its own documents."""
        try:
            out = self.obj.models
            if not isinstance(out, list) or not all(isinstance(m, dict) and m.get("id") for m in out):
                raise TypeError(f"models is {type(out).__name__}")
            return out
        except Exception as exc:                                   # noqa: BLE001
            self.failed("models", exc)
            return []

    def card(self, name: str, raw_model: str) -> dict | None:
        """`/v1/models/{name}`: the entry of that id, or for the engine's own model name (a hidden
        alias once an extension lists models) the first entry; None for any other name."""
        models = self.models()
        low = str(name or "").lower()
        for m in models:
            if str(m.get("id", "")).lower() == low:
                return m
        if models and low and low == str(raw_model or "").lower():
            return models[0]
        return None

    def route(self, method: str, path: str):
        for r in self.obj.routes:
            m = str(r.get("method") or "*").upper()
            if (m == "*" or m == method.upper()) and _matches(r["prefix"], path):
                return r["handler"]
        return None

    def serve(self, handler, request):
        """A handler of the extension's own route: its (status, headers, body), or ExtRouteError
        when it raised (500 extension_error: no headless answer exists for a path only it serves)."""
        try:
            return handler(request)
        except Exception as exc:                                   # noqa: BLE001
            self.failed("route", exc, "answered 500 extension_error")
            raise ExtRouteError(f"{type(exc).__name__}: {exc}"[:300]) from None

    def request(self, request: dict) -> "Request":
        return Request(self, request)

    def speaks(self, major: int, minor: int) -> bool:
        """Whether the extension was written against engine-ext-v1 major.minor or later."""
        v = _version(self.ext_api)
        return v is not None and v[0] == major and v[1] >= minor

    def interim(self, name: str, *args, **kw):
        """One of INTERIM, asked of the extension object's `interim` (the interim adapter's): open,
        a raise is logged, counted as `interim.<name>` and the headless value comes back."""
        fn = getattr(getattr(self.obj, "interim", None), name, None)
        if fn is None:
            return INTERIM[name](*args, **kw)
        try:
            return fn(*args, **kw)
        except PASS_THROUGH:
            raise
        except Exception as exc:                                   # noqa: BLE001
            self.failed(f"interim.{name}", exc)
            return INTERIM[name](*args, **kw)

    def stop(self) -> None:
        self.call("stop")


class ExtRouteError(Exception):
    """An extension's route handler raised: the engine answers 500 extension_error."""


class RouteRequest:
    """What a route handler gets: `method`, `path` (no query), `query`, `headers`, `body` (the JSON
    read, or None), `peer`, `auth` (the engine's access rule). `handler` is the engine's HTTP handler:
    ENG-413 WP3a's interim adapter answers on it and returns None; an extension returns
    (status, headers, body) instead."""

    def __init__(self, handler, method: str, path: str, body=None, auth=None):
        self.handler = handler
        self.method = method
        self.path = path
        raw = getattr(handler, "path", "") or ""
        self.query = raw.split("?", 1)[1] if "?" in raw else ""
        self.headers = getattr(handler, "headers", None)
        self.body = body
        self.auth = auth
        try:
            self.peer = handler._peer()
        except Exception:                                          # noqa: BLE001
            self.peer = None


class Request:
    """One request's hooks in ext.json's order, open on any failure (ENG-413 WP3b asks them from
    the request path and the decode loop). With no extension (or a plan of None) nothing is asked
    and every step hands its input back: the headless engine."""

    def __init__(self, ext: Loaded | None, request: dict):
        self.ext = ext
        self.request = request
        self.plan = None
        self.sup: Supervision | None = None
        self.raw = ext is None

    @property
    def detached(self) -> bool:
        return bool(self.sup is not None and self.sup.detached)

    @property
    def acts(self) -> list:
        return list(self.sup.taken) if self.sup is not None else []

    def begin(self):
        """`plan(request)`: the plan, or None (served raw). A Refusal passes to the caller."""
        if self.ext is None:
            return None
        self.plan = self.ext.call("plan", self.request)
        self.raw = self.plan is None
        return self.plan

    def prompt(self, ids: list, in_think: bool | None = None, conv: str | None = None):
        """`prompt(plan, ids)`: the ids to prefill (a failure: the ids as built). With `in_think`
        (since 1.2, the prompt ends inside `<think>`): `(ids, in_think, client_ids)`, where an
        extension on 1.2 gets `info` {in_think, conv} and may answer `{ids, in_think, client_ids}`
        (client_ids: the prompt as the client sent it, without what the extension put in; None
        when it put nothing in)."""
        ids = list(ids)
        if self.raw:
            return ids if in_think is None else (ids, in_think, None)
        if in_think is None or not self.ext.speaks(1, 2):
            out = self.ext.call("prompt", self.plan, ids, fallback=ids, check=_ids_ok)
            return out if in_think is None else (out, in_think, None)
        out = self.ext.call("prompt", self.plan, ids, {"in_think": bool(in_think), "conv": conv},
                            fallback=ids,
                            check=lambda v: _ids_ok(v) or (isinstance(v, dict) and _ids_ok(v.get("ids"))
                                                          and (v.get("client_ids") is None
                                                               or _ids_ok(v["client_ids"]))))
        if isinstance(out, dict):
            return list(out["ids"]), bool(out.get("in_think", in_think)), out.get("client_ids")
        return out, in_think, None

    def supervise(self, event: dict):
        """`supervisor(plan, event)`: the request's Supervision (the decode loop asks it), or None."""
        if self.raw:
            return None
        obj = self.ext.call("supervisor", self.plan, event)
        self.sup = Supervision(obj, self) if obj is not None else None
        return self.sup

    def ask(self, method: str, *args):
        """A supervisor method at its safe point (Supervision); [] or None without one."""
        if self.sup is None:
            return [] if method == "at_request" else None
        return getattr(self.sup, method)(*args)

    def extras(self, point: str, info: dict) -> list:
        if self.raw:
            return []
        return self.ext.call("extras", self.plan, point, info, fallback=[],
                             check=lambda v: isinstance(v, list))

    def follow_ups(self, outcome) -> list:
        if self.raw or self.plan is None:
            return []
        return self.ext.call("follow_ups", self.plan, outcome, fallback=[],
                             check=lambda v: isinstance(v, list))

    def finish(self, row) -> None:
        if not self.raw and self.plan is not None:
            self.ext.call("finish", self.plan, row)

    def done(self, outcome, detail) -> None:
        if self.sup is not None:
            self.sup.done(outcome, detail)


def _ids_ok(v) -> bool:
    return isinstance(v, list) and all(isinstance(i, int) for i in v)


# ------------------------------------------------------------------------------- the acts

#: the act kinds (ext.json supervisor.act.kinds): defined by the engine, carried out by the engine
CLOSE_REASONING = "CLOSE_REASONING"
CONTINUE_HIDDEN = "CONTINUE_HIDDEN"
REPAIR_CALL = "REPAIR_CALL"
REGENERATE_CALL = "REGENERATE_CALL"
PROMPT_TAIL_NOTE = "PROMPT_TAIL_NOTE"
SET_THINK_BUDGET = "SET_THINK_BUDGET"
GIVE_UP = "GIVE_UP"
ACT_KINDS = (CLOSE_REASONING, CONTINUE_HIDDEN, REPAIR_CALL, REGENERATE_CALL, PROMPT_TAIL_NOTE,
             SET_THINK_BUDGET, GIVE_UP)


def act_kind(act):
    """An act's kind: a mapping's `kind`, or (since 1.2) an object's `kind` attribute."""
    if isinstance(act, dict):
        return act.get("kind")
    return getattr(act, "kind", None)


class Event(dict):
    """An event (ext.json supervisor.events): a mapping whose fields read as attributes too."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


class Supervision:
    """One request's supervisor as the decode loop asks it (ENG-413 WP3b). The safe points' methods
    are asked open: a raise, a wrong shape or an act the method does not allow detaches the
    supervisor for the rest of the request, counted under the method's name, and the loop goes on
    as on a headless engine (no act). Acts already carried out stay.

    Beyond the contract's methods the decode loop still asks the interim adapter's supervisor what
    SUPERVISION_INTERIM names (each open, with the value a headless engine has) and reads its state
    (`gave_up`, `holds_calls`, `cuts`, ...) as attributes; ENG-413 WP4 turns them into fields."""

    _OWN = ("_obj", "_req", "_detached", "_taken")

    def __init__(self, obj, request: Request | None = None):
        object.__setattr__(self, "_obj", obj)
        object.__setattr__(self, "_req", request)
        object.__setattr__(self, "_detached", False)
        object.__setattr__(self, "_taken", [])

    @property
    def detached(self) -> bool:
        """A safe point failed: nothing more is asked of this request's supervisor."""
        return self._detached

    @property
    def taken(self) -> list:
        """(safe point, kind) of every act the methods handed back, in order (the supervisor's
        own `acts`, a count, is its state the engine reads)."""
        return list(self._taken)

    def _failed(self, method: str, exc: BaseException):
        object.__setattr__(self, "_detached", True)
        ext = self._req.ext if self._req is not None else None
        if ext is not None:
            ext.failed(method, exc, "the supervisor is detached for the rest of the request")
        else:
            _count(method)
            print(f"[ext] {method} failed, the supervisor is detached for the rest of the request: "
                  f"{exc!r}", flush=True)

    def _ask(self, method: str, fallback, *args):
        if self.detached:
            return fallback
        fn = getattr(self._obj, method, None)
        if fn is None and method in OPTIONAL_METHODS:
            return fallback                    # an extension before the method's minor has none
        try:
            out = fn(*args)
        except PASS_THROUGH:
            raise
        except Exception as exc:                                   # noqa: BLE001
            self._failed(method, exc)
            return fallback
        if method == "done":
            return None
        if method == "at_request" and not isinstance(out, list):
            self._failed(method, TypeError(f"at_request returned {type(out).__name__}"))
            return fallback
        acts = out if method == "at_request" else ([out] if out is not None else [])
        point = SUPERVISOR[method][1]
        for a in acts:
            if act_kind(a) not in SAFE_POINTS.get(point, ()):
                self._failed(method, ValueError(f"act {act_kind(a)} at {point}"))
                return fallback
        self._taken.extend((point, act_kind(a)) for a in acts)
        return out

    # ------------------------------------------------------------------ the contract's methods
    def at_request(self, event):
        return self._ask("at_request", [], event)

    def at_block(self, event, room):
        return self._ask("at_block", None, event, room)

    def at_tool_call(self, event):
        return self._ask("at_tool_call", None, event)

    def check_answer(self, items, final=False):
        return self._ask("check_answer", None, items, final)

    def at_finish(self, event, room):
        return self._ask("at_finish", None, event, room)

    def done(self, outcome, detail):
        return self._ask("done", None, outcome, detail)

    # ------------------------------------------------------------------ interim (outside the contract)
    def interim(self, name: str, *args, **kw):
        """One of SUPERVISION_INTERIM, open: a raise detaches the supervisor (counted under its
        name) and the headless value comes back; detached, nothing is asked."""
        fallback = SUPERVISION_INTERIM[name](*args, **kw)
        if self.detached:
            return fallback
        try:
            return getattr(self._obj, name)(*args, **kw)
        except PASS_THROUGH:
            raise
        except Exception as exc:                                   # noqa: BLE001
            self._failed(name, exc)
            return fallback

    def __getattr__(self, name):
        if name in SUPERVISION_INTERIM:
            return lambda *a, **kw: self.interim(name, *a, **kw)
        return getattr(self._obj, name)

    def __setattr__(self, name, value):
        if name in self._OWN:
            object.__setattr__(self, name, value)
        else:
            setattr(self._obj, name, value)


#: supervisor methods a minor added: an extension written before it has none, which is no failure
OPTIONAL_METHODS = ("check_answer",)


def _released(call, *a, **kw):
    return {k: v for k, v in call.items() if not k.startswith("_")}


#: what the decode loop asks the interim adapter's supervisor beyond the contract (ENG-413 WP3b),
#: each with the value a headless engine has: SP4's splice and the request's reasoning cap, SP2's
#: claim check asked in place and the memory notes taken out of an answer, SP3's release of held
#: calls and the argument loop's stop
SUPERVISION_INTERIM = {
    "apply_request": lambda prompt, in_think, acts: (prompt, in_think),
    "route_budget": lambda budget, task="route": False,
    "settle_claim": lambda asker: None,
    "without_notes": lambda answer: (answer, 0, 0),
    "as_sent": lambda items, events=None: items,
    "release_call": _released,
    "release_block": lambda inner: None,
    "note_repairs": lambda renamed: None,
    "argloop_stopped": lambda verdict, name: None,
}


def supervising(sup):
    """The decode loop's supervisor: a Supervision as it is, any other object (a test's) wrapped."""
    if sup is None or isinstance(sup, Supervision):
        return sup
    return Supervision(sup)


#: what the request path asks the interim adapter (server/ext_app.py) beyond the contract (ENG-413
#: WP3b), each with the value a headless engine has; `Loaded.interim(name, ...)` asks it open
INTERIM = {
    "server_mode": lambda header, n: "raw",
    "served_name": lambda: None,
    "side_first_max": lambda: 0,
    "strip_states": lambda messages: (messages, 0),
    "loop_escape": lambda plan, info: None,
    "code_class": lambda plan: False,
    "served": lambda plan, prompt, in_think: None,
    "in_context": lambda plan, prompt_ids, choice, rows: None,
    "settle_context": lambda plan, why: None,
    "ask_in_place": lambda plan, prompt_ids, choice: None,
    "route_lines": lambda plan, ui: None,
    "memory_filter": lambda plan, chat, n_choices, ui, tool_names: None,
    "memory_spans": lambda ids, decode, taken: ([], 0),
    "answer_mask": lambda plan, chat, body: None,
    "replayed": lambda session, ids: ids,
    "turn_ended": lambda sup: None,
    "writer": lambda: (None, None),
    "writer_ban_ids": lambda tok: {},
    "writer_foreign_call": lambda text: None,
    "probe_stage": lambda plan: None,
}
