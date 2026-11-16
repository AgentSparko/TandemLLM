"""The reference host of engine-ext-v1 (ENG-429, QA ENG-430): ext.json's rules as code.

No model and no server. `load` is the discovery and the start, closed on any failure; `FakeHost.serve`
drives one request through an extension's hooks in ext.json's order, open on any failure of a
hook. The engine's host (server/ext.py, ENG-413 WP3a) is held to the same behaviours by its tests,
and an extension (TandemApp's tandemapp/engine_ext.py, WP4) is tested against this host before it
meets an engine. Standard library only; imports neither tree.

    ext = load("fake_ext", host)                 # or load(module, host)
    record = host.serve(ext, Scenario(...))      # record["trace"], ["errors"], ["acts"], ["raw"]
    host.call(ext, name, *args)                  # since 2.0: one of the extension's optional calls
    route(ext, method, path)                     # since 3.1: the handler the engine dispatches to

Since 2.0: CALLS, SUP_OPTIONAL and SUP_FIELDS hold the headless value of each optional call, each
optional supervisor method and each supervisor field (ext.json extension.calls,
supervisor.optional_methods, supervisor.fields).

Since 3.2: SET_CONSTRAINT at SP4 (ext.json supervisor.act.constraint). `constraint_of` is the
shape (a wrong one detaches the supervisor), `check_constraint` the refusals the engine makes
before its grammar compiles anything (kinds, scopes, limits, precedence, the vocabulary); the
compile itself is the engine's (`FakeHost(compile_regex=...)` stands in for it, Python's `re` by
default). `serve` records each act's outcome in `record["constraints"]`, hands the outcomes to the
supervisor's `constraint_outcomes` and answers a strict act's refusal as the engine does (400,
param SET_CONSTRAINT; the extension's `finish` is still asked).
"""

from __future__ import annotations

import importlib
import json
import os
import re
import types

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "ext.json"), encoding="utf-8") as _f:
    SPEC = json.load(_f)

EXT_API = SPEC["version"]
HOOKS = {h["name"]: h["args"] for h in SPEC["extension"]["hooks"]}
SUP = {m["name"]: m["args"] for m in SPEC["supervisor"]["methods"]}
POINT_OF = {m["name"]: m["point"] for m in SPEC["supervisor"]["methods"]}
ALLOWED = {sp: set(v["acts"]) for sp, v in SPEC["supervisor"]["safe_points"].items()}
#: paths the engine serves itself; an extension that claims one is refused at start
ENGINE_PREFIXES = ("/v1/chat/completions", "/v1/completions", "/v1/embeddings", "/v1/responses",
                   "/v1/models", "/health", "/healthz", "/v1/health", "/metrics", "/readyz",
                   "/v1/replica/", "/v1/tokenize", "/v1/cache/", "/v1/setup/", "/v1/engine/",
                   "/v1/systemone", "/v1/predict", "/_tandem/")
ENGINE_DASHBOARD = ("live", "system", "logs", "parallel", "metrics", "summary", "usage", "requests")


def _released(call, *a, **kw):
    return {k: v for k, v in call.items() if not k.startswith("_")}


#: since 2.0: the extension's optional calls (ext.json extension.calls), each with the value a
#: headless engine uses
CALLS = {
    "server_mode": lambda header, n: "raw",
    "served_name": lambda: None,
    "side_first_max": lambda: 0,
    "strip_states": lambda messages: (messages, 0),
    "loop_escape": lambda routing, info: None,
    "code_class": lambda routing: False,
    "served": lambda routing, prompt, in_think: None,
    "in_context": lambda routing, prompt_ids, choice, rows: None,
    "settle_context": lambda routing, why: None,
    "ask_in_place": lambda routing, prompt_ids, choice: None,
    "route_lines": lambda routing, ui: None,
    "memory_filter": lambda routing, chat, n_choices, ui, tool_names: None,
    "memory_spans": lambda ids, decode, taken: ([], 0),
    "answer_mask": lambda routing, chat, body: None,
    "replayed": lambda session, ids: ids,
    "turn_ended": lambda supervisor: None,
    "writer_ban_ids": lambda tokenizer: {},
    "writer_foreign_call": lambda text: None,
    "probe_stage": lambda routing: None,
    # since 3.0: what the engine reads of the extension outside a request
    "document": lambda name: None,
    "library_text": lambda name: None,
    "shadow_wanted": lambda: False,
    "monitor_mode": lambda: None,
    "narrator": lambda slot: None,
    "role_errors": lambda fields, name, tokenizer_sha: [],
    "deciders": lambda registry: None,
    "deciders_api": lambda doc: None,
    "memory_writer": lambda: (None, None),
    # since 3.1: the router's question for the shadow reader
    "shadow_question": lambda routing, prompt_ids: None,
}
#: since 2.0: the supervisor's optional methods (ext.json supervisor.optional_methods), headless
SUP_OPTIONAL = {
    "apply_request": lambda prompt, in_think, acts: (prompt, in_think),
    "route_budget": lambda budget, task="route": False,
    "settle_claim": lambda asker: None,
    "without_notes": lambda answer: (answer, 0, 0),
    "as_sent": lambda items, events=None: items,
    "release_call": _released,
    "release_block": lambda inner: None,
    "note_repairs": lambda renamed: None,
    "argloop_stopped": lambda verdict, name: None,
    # since 3.2
    "constraint_outcomes": lambda outcomes: None,
}
#: since 2.0: the supervisor's fields (ext.json supervisor.fields), the value a supervisor that keeps
#: one answers
SUP_FIELDS = {
    "final_answer": lambda: None, "holds_calls": lambda: False, "arms": lambda: None,
    "watches_blocks": lambda: True, "mode": lambda: "on",
    "policy": lambda: types.SimpleNamespace(version=0),
    "acts": lambda: 0, "hidden_tokens": lambda: 0, "acted": lambda: False, "gave_up": lambda: None,
    "cuts": list, "checked": lambda: True,
    "req": lambda: types.SimpleNamespace(request_id="", tools_offered=False, n=1, max_tokens=0),
    "memory_notes": lambda: None, "lift_ids": lambda: None, "on_event": lambda: None,
    "held_events": lambda: None, "resume_ms": list, "forwarding": lambda: None,
    "forwarded_at": lambda: None, "can_ask": lambda: False,
}


#: since 3.2: SET_CONSTRAINT (ext.json supervisor.act.constraint)
CONSTRAINT = SPEC["supervisor"]["act"]["constraint"]
CONSTRAINT_KINDS = ("regex", "choice", "json_schema", "logit_bias")
CONSTRAINT_SCOPES = ("answer", "tool_args")
LIMITS = CONSTRAINT["limits"]


def _field(obj, name):
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def constraint_of(act) -> dict:
    """A SET_CONSTRAINT act's constraint as {kind, value, scope, strict}; ValueError on a wrong
    shape (failure.constraint: the supervisor is detached)."""
    c = _field(act, "constraint")
    if c is None:
        raise ValueError("SET_CONSTRAINT without its constraint")
    kind, scope, strict = _field(c, "kind"), _field(c, "scope"), _field(c, "strict")
    if kind not in CONSTRAINT_KINDS:
        raise ValueError(f"SET_CONSTRAINT kind {kind!r}")
    if scope is not None and scope not in CONSTRAINT_SCOPES:
        raise ValueError(f"SET_CONSTRAINT scope {scope!r}")
    if strict is not None and not isinstance(strict, bool):
        raise ValueError("SET_CONSTRAINT strict is not a bool")
    return {"kind": kind, "value": _field(c, "value"), "scope": scope, "strict": bool(strict)}


def _offered(body: dict) -> list:
    return [t["function"]["name"] for t in body.get("tools") or []
            if isinstance(t, dict) and isinstance(t.get("function"), dict)
            and isinstance(t["function"].get("name"), str)]


def _own(body: dict) -> str | None:
    """The request's own constraint's field (precedence), or None."""
    rf = body.get("response_format")
    if isinstance(rf, dict) and rf.get("type") in ("json_object", "json_schema"):
        return "response_format"
    if body.get("structured_outputs") is not None:
        return "structured_outputs"
    tc = body.get("tool_choice")
    if body.get("tools") and (tc == "required" or isinstance(tc, dict)):
        return "tool_choice"
    return None


def check_constraint(c: dict, body: dict, vocab: int, taken=(), structured: bool = True,
                     compile_regex=None):
    """None when the engine carries the constraint out, else (why, counted): `counted` false when
    it gave way (precedence, the engine's switch). The reference of server/compat.py
    `act_constraint`, standard library only: the engine's grammar may still refuse a regex or a
    schema it cannot compile (counted)."""
    kind, value, scope = c["kind"], c["value"], c["scope"]
    family = "bias" if kind == "logit_bias" else "grammar"
    if family in taken:
        return "one of a family a request", True
    if kind == "logit_bias":
        if scope is not None:
            return "logit_bias takes no scope", True
        if not isinstance(value, dict) or len(value) > LIMITS["bias_ids"]:
            return "logit_bias maps at most bias_ids token ids to numbers", True
        if body.get("logit_bias"):
            return "the request has its own logit_bias, which wins", False
        for k, v in value.items():
            try:
                tid = int(k)
            except (TypeError, ValueError):
                return f"logit_bias key {k!r} is not a token id", True
            if not 0 <= tid < vocab:
                return f"logit_bias token id {tid} is outside the vocabulary", True
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not -100 <= v <= 100:
                return f"logit_bias value for {tid} must be in [-100, 100]", True
        return None
    if not structured:
        return "structured outputs are off on this engine", False
    if _own(body) is not None:
        return f"the request has its own constraint ({_own(body)}), which wins", False
    if scope == "tool_args":
        if kind != "choice":
            return "scope tool_args takes kind choice", True
        if not body.get("tools"):
            return "scope tool_args needs a chat request that offers tools", True
        if body.get("tool_choice") == "none":
            return "the request's tool_choice is none, which wins", False
        offered = _offered(body)
        if not isinstance(value, list) or not value or not all(isinstance(x, str) and x in offered
                                                                for x in value):
            return "scope tool_args takes a list of the offered functions", True
        return None
    if kind == "regex":
        if not isinstance(value, str) or len(value) > LIMITS["regex_chars"]:
            return "regex is a string of at most regex_chars characters", True
        try:
            (compile_regex or re.compile)(value)
        except Exception as exc:                                   # noqa: BLE001
            return f"the regex does not compile: {exc}", True
    elif kind == "choice":
        if not isinstance(value, list) or not value or not all(isinstance(x, str) for x in value):
            return "choice is a non-empty list of strings", True
        if len(value) > LIMITS["choices"] or max(map(len, value)) > LIMITS["choice_chars"]:
            return "choice holds at most choices strings of at most choice_chars characters", True
    elif kind == "json_schema":
        if not isinstance(value, dict) or len(json.dumps(value).encode()) > LIMITS["schema_bytes"]:
            return "json_schema is an object of at most schema_bytes bytes as JSON", True
    return None


class Refused(Exception):
    """The engine does not start: one line, '[ext] REFUSING: <why>'."""

    def __init__(self, why: str):
        super().__init__(f"[ext] REFUSING: {why}")
        self.why = why


class Refusal(Exception):
    """What `plan` raises to answer the client with an error (ext.json failure.refusal)."""

    def __init__(self, status: int, type_: str, message: str, param: str | None = None):
        super().__init__(message)
        self.status, self.type, self.message = status, type_, message
        self.param = param                       # since 1.2


class NoRoom(ValueError):
    """Since 2.0: what the engine's callbacks raise when a question asked in place has no room."""

    def __init__(self, why: str = "no_room"):
        super().__init__(why)
        self.why = why


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


def _claims_engine_path(prefix: str) -> bool:
    if prefix.startswith("/v1/dashboard/"):
        return prefix[len("/v1/dashboard/"):].split("/")[0] in ENGINE_DASHBOARD
    return any(prefix.startswith(p) or p.startswith(prefix.rstrip("*")) for p in ENGINE_PREFIXES)


def _matches(prefix: str, path: str) -> bool:
    if prefix.endswith("/"):
        return path.startswith(prefix) or path == prefix.rstrip("/")
    return path == prefix or path.startswith(prefix + "/")


def route(ext, method: str, path: str):
    """The handler of the extension's route for `method` and `path` (ext.json
    extension.attributes.routes), or None. Since 3.1 a preflight (OPTIONS) goes only to a route
    that names OPTIONS, never to a `*` one."""
    want = method.upper()
    for r in ext.routes:
        m = str(r.get("method") or "*").upper()
        if (m == want or (m == "*" and want != "OPTIONS")) and _matches(r.get("prefix", ""), path):
            return r["handler"]
    return None


class FakeHost:
    """ext.json's host attributes, a log of every hook call and the failure counters."""

    def __init__(self, mode: str = "chat", args: dict | None = None, ext_dir: str | None = None,
                 state: dict | None = None, vocab: int = 256, structured: bool = True,
                 compile_regex=None):
        self.ext_api = EXT_API
        self.engine_api = "1.5"
        self.mode = mode
        self.state: dict = state if state is not None else {}
        self.lock = None
        self.tokenizer = types.SimpleNamespace(encode=lambda s: [ord(c) for c in s],
                                               decode=lambda ids: "".join(chr(i) for i in ids))
        self.metrics = types.SimpleNamespace(families=[], events=[],
                                             add=lambda fam: self.metrics.families.append(fam),
                                             event=lambda row: self.metrics.events.append(row))
        self.ledger_path = ":memory:"
        self.args = dict(args or {})
        # since 3.0: the /v1/engine/* handlers, each answering (status, body)
        self.engine = {k: (lambda *a, **kw: (200, {})) for k in
                       ("residency_plan", "disk", "disk_clear", "deciders", "narrator", "shadow")}
        self.Refusal = Refusal
        # since 2.0
        self.ext_dir = ext_dir
        self.identity_name = lambda: None
        self.own_eta = None
        self.own_idle = lambda: None
        self.NoRoom = NoRoom
        self.shipped_guard = None                     # since 3.0
        self.lines: list[str] = []
        self.errors: dict[str, int] = {}
        # since 3.2: what SET_CONSTRAINT is checked against (the served vocabulary's size, the
        # engine's --no-structured-outputs, the engine's regex compile)
        self.vocab, self.structured, self.compile_regex = vocab, structured, compile_regex

    def log(self, line: str) -> None:
        self.lines.append(line)

    # ------------------------------------------------------------------------- one request

    def call(self, ext, name: str, *args, **kw):
        """Since 2.0: one of the extension's optional calls (CALLS), open: a missing method or one
        that raises answers the headless value; a raise is logged and counted under its name."""
        fn = getattr(ext, name, None)
        if fn is None:
            return CALLS[name](*args, **kw)
        try:
            return fn(*args, **kw)
        except Exception as exc:                                   # noqa: BLE001
            self.errors[name] = self.errors.get(name, 0) + 1
            self.log(f"[ext] {name} failed, the request goes on without it: {exc!r}")
            return CALLS[name](*args, **kw)

    def _call(self, rec: dict, ext, name: str, *args, fallback=None, check=None):
        """One hook, open on failure: the error is logged and counted, `fallback` comes back."""
        if rec["detached"].get(name):
            return fallback
        rec["trace"].append(f"extras {args[1]}" if name == "extras" else name)
        try:
            out = getattr(ext, name)(*args)
            if check is not None and not check(out):
                raise TypeError(f"{name} returned {type(out).__name__}")
            return out
        except Refusal:
            raise
        except Exception as exc:                                   # noqa: BLE001
            self.errors[name] = self.errors.get(name, 0) + 1
            rec["errors"].append(f"{name}: {exc!r}")
            self.log(f"[ext] {name} failed, the request goes on without it: {exc!r}")
            return fallback

    def _ask(self, rec: dict, sup, name: str, *args):
        """A supervisor method at its safe point; an error or a misplaced act detaches it."""
        if sup is None or rec["detached"].get("supervisor"):
            return [] if name == "at_request" else None
        rec["trace"].append(name)
        try:
            out = getattr(sup, name)(*args)
        except Exception as exc:                                   # noqa: BLE001
            return self._sup_failed(rec, name, exc)
        if name == "at_request" and not isinstance(out, list):
            return self._sup_failed(rec, name, TypeError(f"at_request returned {type(out).__name__}"))
        acts = out if name == "at_request" else ([out] if out is not None else [])
        point = POINT_OF[name]
        for a in acts:
            kind = a.get("kind") if isinstance(a, dict) else None
            if point in ALLOWED and kind not in ALLOWED[point]:
                return self._sup_failed(rec, name, ValueError(f"act {kind} at {point}"))
            if kind == "SET_CONSTRAINT":
                try:
                    constraint_of(a)                       # since 3.2: the shape
                except ValueError as exc:
                    return self._sup_failed(rec, name, exc)
        rec["acts"] += [(point, a["kind"]) for a in acts]
        return out

    def constrain(self, rec: dict, sup, acts: list, body: dict):
        """Since 3.2: SP4's SET_CONSTRAINT acts, in order: each outcome into rec["constraints"]
        and to the supervisor's constraint_outcomes (open); a refusal counted as an error of
        at_request when the constraint is wrong in itself. Answers a strict act's refusal, or None."""
        taken, outcomes, strict = set(), [], None
        for a in acts:
            if a.get("kind") != "SET_CONSTRAINT":
                continue
            c = constraint_of(a)
            got = check_constraint(c, body, self.vocab, taken, self.structured, self.compile_regex)
            if got is None:
                taken.add("bias" if c["kind"] == "logit_bias" else "grammar")
            else:
                why, counted = got
                if counted:
                    self.errors["at_request"] = self.errors.get("at_request", 0) + 1
                    rec["errors"].append(f"at_request: SET_CONSTRAINT {why}")
                self.log(f"[ext] SET_CONSTRAINT {c['kind']} refused, the request runs without it: {why}")
                if c["strict"] and strict is None:
                    strict = why
            outcomes.append({"kind": c["kind"], "scope": None if c["kind"] == "logit_bias"
                             else c["scope"] or "answer", "strict": c["strict"],
                             "applied": got is None, "why": None if got is None else got[0]})
        rec["constraints"] = outcomes
        fn = getattr(sup, "constraint_outcomes", None)
        if outcomes and callable(fn):
            try:
                fn(outcomes)
            except Exception as exc:                               # noqa: BLE001
                self._sup_failed(rec, "constraint_outcomes", exc)
        return strict

    def _sup_failed(self, rec: dict, name: str, exc: Exception):
        self.errors[name] = self.errors.get(name, 0) + 1
        rec["errors"].append(f"{name}: {exc!r}")
        rec["detached"]["supervisor"] = True
        self.log(f"[ext] supervisor.{name} failed, the request goes on without it: {exc!r}")
        return [] if name == "at_request" else None

    def serve(self, ext, sc: "Scenario") -> dict:
        """One request, as the engine's request path asks the hooks (ext.json order.request)."""
        rec = {"trace": [], "errors": [], "acts": [], "raw": False, "detached": {}, "status": 200}
        request = {"body": dict(sc.body), "headers": {}, "client_kind": "test", "request_id": "r1",
                   "chat": True, "user": None, "count_tokens": lambda: len(sc.prompt)}
        try:
            plan = self._call(rec, ext, "plan", request)
        except Refusal as r:
            rec["status"] = r.status
            rec["refusal"] = {"type": r.type, "message": r.message}
            return rec
        if plan is None:
            rec["raw"] = True                     # the headless path: no hook asked again
            return rec
        def ids_ok(v):
            return isinstance(v, list) and all(isinstance(i, int) for i in v)
        # info, and the answer may be a mapping (2.0: every extension; 1.x had a two-argument form)
        ids = self._call(rec, ext, "prompt", plan, list(sc.prompt), {"in_think": False, "conv": None},
                         fallback=list(sc.prompt),
                         check=lambda v: ids_ok(v) or (isinstance(v, dict) and ids_ok(v.get("ids"))))
        ids = list(ids["ids"]) if isinstance(ids, dict) else ids
        rec["prompt"] = ids
        ev = {k: None for k in SPEC["supervisor"]["events"]["request"]}
        ev.update(request_id="r1", messages=sc.body.get("messages", []), tools=sc.body.get("tools", []),
                  n=1, stream=False, prompt_tokens=len(ids), in_think=False, prompt_ids=list(ids),
                  chat=True, tool_names=[t.get("function", {}).get("name") for t in sc.body.get("tools", [])],
                  thinking_off=False, model=sc.body.get("model"), tool_choice="auto", max_tokens=64,
                  client="test", constrained=False, profile="", cls="", tool_format="")
        sup = self._call(rec, ext, "supervisor", plan, ev)
        acts = self._ask(rec, sup, "at_request", ev) or []
        strict = self.constrain(rec, sup, acts, sc.body)
        if strict is not None:
            # a strict act's refusal is the client's 400; the engine still asks finish
            rec["status"] = 400
            rec["refusal"] = {"type": "invalid_request_error", "param": "SET_CONSTRAINT",
                              "message": strict}
            self._call(rec, ext, "finish", plan, {"request_id": "r1"})
            return rec
        self._call(rec, ext, "extras", plan, "start", {}, fallback=[], check=lambda v: isinstance(v, list))
        blocks, rounds = sc.blocks, 0
        while True:
            for b in range(blocks):
                self._ask(rec, sup, "at_block", _event("block", reasoning_tokens=b, inside=True,
                                                       output_tokens=b, pos=len(ids) + b), 100)
                self._call(rec, ext, "extras", plan, "block", {"n": b}, fallback=[],
                           check=lambda v: isinstance(v, list))
            gave_up = False
            for i in range(sc.calls):
                call = _event("tool_call", name="f", arguments="{}", index=i, block="", cut=False,
                              stopped=False, final=False)
                act = self._ask(rec, sup, "at_tool_call", call)
                if act and act["kind"] == "REGENERATE_CALL" and rounds < 3:
                    rounds += 1
                    act = self._ask(rec, sup, "at_tool_call", call)
                gave_up = gave_up or bool(act and act["kind"] == "GIVE_UP")
            if gave_up:
                break
            act = self._ask(rec, sup, "at_finish", _event("finish", kind="stop", inside=False, answer="",
                                                          reasoning_tokens=blocks, output_tokens=blocks,
                                                          pos=len(ids) + blocks, calls="", notes=0,
                                                          note_calls=0), 100)
            if act and act["kind"] == "CONTINUE_HIDDEN" and rounds < 3:
                rounds, blocks = rounds + 1, 1
                sc = Scenario(body=sc.body, prompt=sc.prompt, blocks=1, calls=0)
                continue
            break
        self._call(rec, ext, "extras", plan, "finish", {}, fallback=[], check=lambda v: isinstance(v, list))
        self._call(rec, ext, "follow_ups", plan, {"finish": "stop"}, fallback=[],
                   check=lambda v: isinstance(v, list))
        self._call(rec, ext, "finish", plan, {"request_id": "r1"})
        if sup is not None and not rec["detached"].get("supervisor"):
            rec["trace"].append("done")
            try:
                sup.done("stop", {})
            except Exception as exc:                               # noqa: BLE001
                self._sup_failed(rec, "done", exc)
        return rec


def _event(event_type: str, **fields) -> dict:
    """An event with every field ext.json lists for its type (None where the scenario sets none)."""
    ev = {k: None for k in SPEC["supervisor"]["events"][event_type]}
    ev.update(fields)
    return ev


class Scenario:
    """A request the fake decode loop plays: its body, prompt ids, blocks and tool calls."""

    def __init__(self, body: dict | None = None, prompt=(1, 2, 3), blocks: int = 2, calls: int = 0):
        self.body = body or {"model": "TandemLLM", "messages": [{"role": "user", "content": "hi"}]}
        self.prompt = tuple(prompt)
        self.blocks = blocks
        self.calls = calls


def load(module, host: FakeHost):
    """The discovery and the start (ext.json discovery, failure.start): the extension object, or
    Refused. `module` is a module name (QSE_EXT), a module object, or '' for none."""
    if module in ("", None):
        if host.mode == "chat":
            raise Refused("chat mode needs an extension (QSE_EXT is empty)")
        return None
    if isinstance(module, str):
        try:
            module = importlib.import_module(module)
        except Exception as exc:                                   # noqa: BLE001
            raise Refused(f"QSE_EXT={module} does not import: {exc!r}") from None
    for attr in SPEC["discovery"]["module"]:
        if not hasattr(module, attr):
            raise Refused(f"{module.__name__} has no {attr}")
    why = accepts(module.EXT_API, host.ext_api)
    if why:
        raise Refused(why)
    # since 3.0: the module's own checks before the load; the session monitor needs them
    early = getattr(module, "before_load", None)
    if callable(early):
        try:
            line = early(dict(host.args))
        except SystemExit:
            raise
        except Exception as exc:                                   # noqa: BLE001
            raise Refused(f"{module.__name__}.before_load failed: {exc!r}") from None
        if isinstance(line, str):
            host.log(line)
    elif host.args.get("conductor") in ("shadow", "on"):
        raise Refused(f"the session monitor (--conductor shadow|on) needs engine-ext-v1 3.0's "
                      f"before_load; {module.__name__} (engine-ext-v1 {module.EXT_API}) has none")
    try:
        ext = module.start(host)
    except Exception as exc:                                       # noqa: BLE001
        raise Refused(f"{module.__name__}.start failed: {exc!r}") from None
    missing = [h for h in HOOKS if not callable(getattr(ext, h, None))]
    if missing:
        raise Refused(f"{module.__name__} lacks the hooks {missing}")
    for attr in SPEC["extension"]["attributes"]:
        if not isinstance(getattr(ext, attr, None), list):
            raise Refused(f"{module.__name__}'s {attr} is not a list")
    for r in ext.routes:
        if _claims_engine_path(r.get("prefix", "")):
            raise Refused(f"{module.__name__} claims the engine's path {r.get('prefix')}")
    return ext
