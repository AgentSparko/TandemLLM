"""The reference host of engine-ext-v1 1.0 (ENG-429, QA ENG-430): ext.json's rules as code.

No model and no server. `load` is the discovery and the start, closed on any failure; `FakeHost.serve`
drives one request through an extension's hooks in ext.json's order, open on any failure of a
hook. The engine's host (server/ext.py, ENG-413 WP3a) is held to the same behaviours by its tests,
and an extension (TandemApp's tandemapp/engine_ext.py, WP4) is tested against this host before it
meets an engine. Standard library only; imports neither tree.

    ext = load("fake_ext", host)                 # or load(module, host)
    record = host.serve(ext, Scenario(...))      # record["trace"], ["errors"], ["acts"], ["raw"]
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


class Refused(Exception):
    """The engine does not start: one line, '[ext] REFUSING: <why>'."""

    def __init__(self, why: str):
        super().__init__(f"[ext] REFUSING: {why}")
        self.why = why


class Refusal(Exception):
    """What `plan` raises to answer the client with an error (ext.json failure.refusal)."""

    def __init__(self, status: int, type_: str, message: str):
        super().__init__(message)
        self.status, self.type, self.message = status, type_, message


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


class FakeHost:
    """ext.json's host attributes, a log of every hook call and the failure counters."""

    def __init__(self, mode: str = "chat", args: dict | None = None):
        self.ext_api = EXT_API
        self.engine_api = "1.3"
        self.mode = mode
        self.state: dict = {}
        self.lock = None
        self.tokenizer = types.SimpleNamespace(encode=lambda s: [ord(c) for c in s],
                                               decode=lambda ids: "".join(chr(i) for i in ids))
        self.metrics = types.SimpleNamespace(families=[], add=lambda fam: self.metrics.families.append(fam))
        self.ledger_path = ":memory:"
        self.args = dict(args or {})
        self.engine = {k: (lambda *a, **kw: {}) for k in
                       ("residency_plan", "disk", "disk_clear", "deciders", "narrator", "shadow")}
        self.Refusal = Refusal
        self.lines: list[str] = []
        self.errors: dict[str, int] = {}

    def log(self, line: str) -> None:
        self.lines.append(line)

    # ------------------------------------------------------------------------- one request

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
        rec["acts"] += [(point, a["kind"]) for a in acts]
        return out

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
        ids = self._call(rec, ext, "prompt", plan, list(sc.prompt), fallback=list(sc.prompt),
                         check=lambda v: isinstance(v, list) and all(isinstance(i, int) for i in v))
        rec["prompt"] = ids
        ev = {k: None for k in SPEC["supervisor"]["events"]["request"]}
        ev.update(request_id="r1", messages=sc.body.get("messages", []), tools=sc.body.get("tools", []))
        sup = self._call(rec, ext, "supervisor", plan, ev)
        self._ask(rec, sup, "at_request", ev)
        self._call(rec, ext, "extras", plan, "start", {}, fallback=[], check=lambda v: isinstance(v, list))
        blocks, rounds = sc.blocks, 0
        while True:
            for b in range(blocks):
                self._ask(rec, sup, "at_block", {"reasoning_tokens": b, "inside": True}, 100)
                self._call(rec, ext, "extras", plan, "block", {"n": b}, fallback=[],
                           check=lambda v: isinstance(v, list))
            gave_up = False
            for i in range(sc.calls):
                act = self._ask(rec, sup, "at_tool_call", {"name": "f", "arguments": "{}", "index": i})
                if act and act["kind"] == "REGENERATE_CALL" and rounds < 3:
                    rounds += 1
                    act = self._ask(rec, sup, "at_tool_call", {"name": "f", "arguments": "{}", "index": i})
                gave_up = gave_up or bool(act and act["kind"] == "GIVE_UP")
            if gave_up:
                break
            act = self._ask(rec, sup, "at_finish", {"kind": "stop", "answer": ""}, 100)
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
