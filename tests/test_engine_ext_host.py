"""The engine's engine-ext-v1 host (ENG-413 WP3a; ENG-429, QA ENG-430): server/ext.py, the extension
chat mode loads (TandemApp's tandemapp.engine_ext since WP4, vendored in app/) and what server/app.py
serves through them.

The test matrix (the Gherkin of ENG-429's host part):

  1  server/ext.py says what contracts/engine-ext-v1/ext.json says: its version, the module
     attributes, every hook and supervisor method with its arguments, the safe points' acts, the
     request fields; the engine's paths are fake_host.py's; it imports the standard library only;
     3.1: shadow_question, the engine's objects in state_keys, and the route rule (a preflight
     goes only to a route that names OPTIONS, as fake_host.py's `route`)
                                                                  test_the_host_is_the_interface
  2  fake_ext.py through the real loader (QSE_EXT by name) and the real Request asks the same hooks
     in the same order as the reference host, for a plain request, a tool call, CONTINUE_HIDDEN,
     REGENERATE_CALL, GIVE_UP, an SP4 note and a plan of None   test_the_order_is_the_reference_hosts
  3  start is closed: chat mode without QSE_EXT, a module that does not import, no EXT_API,
     another major (1.x), a newer minor, start raising, a missing hook, a route on an engine path,
     3.0: the session monitor without the module's before_load and every 2.x extension (another
     major) each refuse with the reference host's one line; engine mode without QSE_EXT is headless
                                                                  test_start_is_closed
  4  a request is open: each hook and supervisor method that raises, a wrong shape and an act out
     of its safe point leave the request served (raw from a failed plan), counted by hook in
     qse_ext_errors_total; the next request asks again; a Refusal from plan is the answer
                                                                  test_a_request_is_open
  5  the engine refuses to start with one '[ext] REFUSING:' line, never serving: chat mode without
     QSE_EXT, a module that does not import, another major (1.3), a missing hook, a QSE_EXT_ARGS that is
     not an object (each before the weights load); start raising, a claimed engine path,
     TandemApp's extension without a conductor directory (after the load, before the first request)
                                                                  test_the_engine_refuses_to_start
  6  a headless engine: /health extensions [], 404 no_ui on the page, 404 not_on_engine on the
     conductor's, library, memory, Laya (no decision models), session, config and collect paths,
     the raw model alone, no qse_ext_errors_total; chat completions served
                                                                  test_a_headless_engine
  7  an extension on a fake engine: /health lists it; a health hook that raises leaves /health 200
     without its part and counts qse_ext_errors_total{hook="health"}; its models replace the raw
     one (the raw name a hidden alias); its route's (status, headers, body) is the answer; a route
     that raises is 500 extension_error, counted; stop is asked at the drain, open
                                                                  test_an_extension_on_a_fake_engine
  8  chat mode through TandemApp's extension: /health mode chat and the extension listed (its
     release, engine-ext-v1 2.0), TandemLLM, the conductor's routes and the page served by it as
     (status, headers, body)                                   test_chat_mode_through_the_adapter

1-4 need no torch; 5-8 start server/app.py --fake-engine (CPU torch). Run: python tests/test_engine_ext_host.py
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import types
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import pairapp  # noqa: E402,F401  (a pair test: TandemApp from TANDEMAPP_SRC, ENG-413 WP14)
DIR = os.path.join(ROOT, "contracts", "engine-ext-v1")

from server import ext as E  # noqa: E402


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"engine_ext_ref_{name}", os.path.join(DIR, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _spec() -> dict:
    with open(os.path.join(DIR, "ext.json")) as f:
        return json.load(f)


def _fake_ext():
    """fake_ext.py found by name, as QSE_EXT=fake_ext finds it with the folder on the path."""
    if DIR not in sys.path:
        sys.path.insert(0, DIR)
    return E.discover("fake_ext", "chat")


def _host(args=None, mode="chat") -> E.Host:
    lines = []
    h = E.Host(mode=mode, engine_api="1.4", state={}, lock=None, tokenizer=None, registry=None,
               ledger_path=None, args=dict(args or {}), log=lines.append)
    h.lines = lines
    return h


# ---------------------------------------------------------------------------- 1

def test_the_host_is_the_interface():
    s = _spec()
    assert E.EXT_API == s["version"], (E.EXT_API, s["version"])
    assert tuple(s["discovery"]["module"]) == E.MODULE_ATTRS
    assert {h["name"]: h["args"] for h in s["extension"]["hooks"]} == E.HOOKS
    assert tuple(s["extension"]["attributes"]) == E.ATTRS
    assert {m["name"]: (m["args"], m["point"]) for m in s["supervisor"]["methods"]} == \
        {k: (v[0], v[1]) for k, v in E.SUPERVISOR.items()}
    assert {k: tuple(v["acts"]) for k, v in s["supervisor"]["safe_points"].items()} == E.SAFE_POINTS
    assert tuple(s["request"]["fields"]) == E.REQUEST_FIELDS
    assert all(hasattr(_host(), a) for a in s["host"]), [a for a in s["host"] if not hasattr(_host(), a)]
    H = _load("fake_host")
    assert (E.ENGINE_PREFIXES, E.ENGINE_DASHBOARD) == (H.ENGINE_PREFIXES, H.ENGINE_DASHBOARD)
    for prefix in ("/v1/conductor/", "/v1/library", "/dashboard", "/v1/dashboard/session", "/v1/models",
                   "/v1/dashboard/live", "/v1/", "/v1/engine/disk"):
        assert E.claims_engine_path(prefix) == H._claims_engine_path(prefix), prefix
    for a, b in (("1.0", "1.1"), ("1.1", "1.1"), ("1.2", "1.1"), ("2.0", "1.1"), ("x", "1.1")):
        assert E.accepts(a, b) == H.accepts(a, b), (a, b)
    _the_2_0_surface(s, H)
    with open(os.path.join(ROOT, "server", "ext.py")) as f:
        tree = ast.parse(f.read())
    std = {"__future__", "importlib", "json", "os", "re", "threading"}
    top = [a.name for n in tree.body if isinstance(n, ast.Import) for a in n.names] + \
        [n.module for n in tree.body if isinstance(n, ast.ImportFrom)]
    assert set(top) <= std, top
    inner = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} - set(top)
    assert inner == {"server.api"}, inner                       # the counter, inside a function
    return f"engine-ext-v1 {E.EXT_API}: {len(E.HOOKS)} hooks, {len(E.SUPERVISOR)} methods, the reference's paths"


def _params(fn) -> list:
    return [p.name for p in inspect.signature(fn).parameters.values()
            if p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)]


#: arguments for each optional call and supervisor method, to compare the headless values
_ARGS = {"header": "on", "n": 2, "messages": [{"role": "user"}], "routing": None, "info": {},
         "prompt": [1], "in_think": True, "prompt_ids": [1, 2], "choice": None, "rows": None,
         "why": "no_room", "ui": None, "chat": True, "n_choices": 1, "tool_names": (), "ids": [4, 5],
         "decode": str, "taken": ["t"], "body": {}, "session": "s", "supervisor": None,
         "tokenizer": None, "text": "x", "acts": [], "budget": 64, "task": "client", "asker": None,
         "answer": "a", "items": [{"x": 1}], "events": None, "call": {"id": 1, "_held": 2},
         "inner": "y", "renamed": {}, "verdict": "v", "name": "f", "slot": None, "fields": {},
         "tokenizer_sha": None, "registry": None, "doc": {}, "outcomes": []}


def _the_2_0_surface(s: dict, H) -> None:
    """engine-ext-v1 2.0: the optional calls, the supervisor's optional methods and fields and the
    kept interim are server/ext.py's and the reference host's, name for name, argument for
    argument and headless value for headless value."""
    calls = {c["name"]: c for c in s["extension"]["calls"]}
    assert set(calls) == set(E.CALLS) == set(H.CALLS), (sorted(set(calls) ^ set(E.CALLS)),
                                                         sorted(set(calls) ^ set(H.CALLS)))
    for name, c in calls.items():
        assert _params(E.CALLS[name]) == c["args"] == _params(H.CALLS[name]), name
        args = [_ARGS[a] for a in c["args"]]
        assert E.NULL.ask(name, *args) == H.CALLS[name](*args), name
    opt = {m["name"]: m for m in s["supervisor"]["optional_methods"]}
    assert set(opt) == set(E.SUP_OPTIONAL) == set(H.SUP_OPTIONAL), sorted(set(opt) ^ set(E.SUP_OPTIONAL))
    assert not set(opt) & set(E.SUPERVISOR)
    for name, m in opt.items():
        assert _params(E.SUP_OPTIONAL[name]) == m["args"] == _params(H.SUP_OPTIONAL[name]), name
        args = [_ARGS[a] for a in m["args"]]
        assert E.SUP_OPTIONAL[name](*args) == H.SUP_OPTIONAL[name](*args), name
    assert set(s["supervisor"]["fields"]) == set(E.SUP_FIELDS) == set(H.SUP_FIELDS)
    plain = E.Supervision(types.SimpleNamespace())
    for name in E.SUP_FIELDS:
        a, b = getattr(plain, name), H.SUP_FIELDS[name]()
        if isinstance(b, types.SimpleNamespace):
            assert all(getattr(a, k) == v for k, v in vars(b).items()), name
        else:
            assert a == b, (name, a, b)
    # a supervisor without an optional method answers its headless value and stays attached
    assert plain.apply_request([1], True, []) == ([1], True) and not plain.detached
    kept = " ".join(k["what"] for k in s["kept_interim"])
    # 3.0 (ENG-413 WP5): nothing interim of the 2.0 list is left; only the --backends hand-over,
    # until WP14; the writer and the reads outside a request are calls
    assert not hasattr(E, "INTERIM") and "writer" not in kept and "--backends" in kept, kept
    assert all(k["why"] and k["until"] == "ENG-413 WP14" for k in s["kept_interim"])
    assert set(E.CALLS_3_0) == {c["name"] for c in s["extension"]["calls"] if c.get("since") == "3.0"}
    # 3.1 (ENG-413 WP5b): the router's shadow question; the engine's objects the extension uses
    assert set(E.CALLS_3_1) == {c["name"] for c in s["extension"]["calls"] if c.get("since") == "3.1"} \
        == {"shadow_question"}
    assert E.NULL.ask("shadow_question", None, [1, 2]) is None
    assert {"escapes", "session_tokens"} <= set(s["state_keys"]) and "since_3_1" in s
    # the route rule: a preflight goes only to a route that names OPTIONS (engine and reference host)
    every, pre = (lambda r: "every"), (lambda r: "pre")
    obj = types.SimpleNamespace(routes=[{"method": "*", "prefix": "/v1/conductor/", "handler": every},
                                        {"method": "GET", "prefix": "/v1/laya", "handler": every},
                                        {"method": "OPTIONS", "prefix": "/v1/laya", "handler": pre}])
    got = E.Loaded(types.SimpleNamespace(NAME="t", VERSION="0"), obj, None)
    for method, path, want in (("GET", "/v1/conductor/x", every), ("OPTIONS", "/v1/conductor/x", None),
                               ("OPTIONS", "/v1/laya/ask", pre), ("options", "/v1/laya", pre),
                               ("GET", "/v1/laya/kit/a.py", every), ("POST", "/v1/laya/ask", None)):
        assert got.route(method, path) is want and H.route(obj, method, path) is want, (method, path)
    assert set(E.DOCUMENTS) == set(s["extension"]["documents"]) and set(E.WRITER) == set(s["memory_writer"]["methods"])
    assert set(E.MODULE_OPTIONAL) == set(s["discovery"]["module_optional"])
    assert {"shipped_guard", "engine"} <= set(s["host"]) and "conductor" in s["state_keys"]
    assert {"ext_dir", "identity_name", "own_eta", "own_idle", "NoRoom"} <= set(s["host"])
    assert "routing" in s["request"]["plan_fields"] and s["request"]["routing_fields"]
    assert set(s["extras"]["lines_fields"]) >= {"ui", "doc", "channel", "inline", "share"}
    assert "interim" not in json.dumps({k: v for k, v in s.items()
                                        if k not in ("since_1_2", "kept_interim", "changed_from_1", "changed_from_2",
                                                     "since_3_0")})


# ---------------------------------------------------------------------------- 2

def _play(ext, sc) -> dict:
    """One request through the engine's Request, in the reference host's order (fake_host.serve)."""
    request = {"body": dict(sc.body), "headers": {}, "client_kind": "test", "request_id": "r1",
               "chat": True, "user": None, "count_tokens": lambda: len(sc.prompt)}
    r = ext.request(request)
    try:
        plan = r.begin()
    except E.Refusal as x:
        return {"status": x.status, "refusal": {"type": x.type, "message": x.message}, "acts": [],
                "raw": False}
    if plan is None:
        return {"status": 200, "raw": True, "acts": []}
    ids = r.prompt(list(sc.prompt))
    ev = {k: None for k in _spec()["supervisor"]["events"]["request"]}
    ev.update(request_id="r1", messages=sc.body.get("messages", []), tools=sc.body.get("tools", []))
    r.supervise(ev)
    r.ask("at_request", ev)
    r.extras("start", {})
    blocks, calls, rounds = sc.blocks, sc.calls, 0
    while True:
        for b in range(blocks):
            r.ask("at_block", {"reasoning_tokens": b, "inside": True}, 100)
            r.extras("block", {"n": b})
        gave_up = False
        for i in range(calls):
            act = r.ask("at_tool_call", {"name": "f", "arguments": "{}", "index": i})
            if act and act["kind"] == "REGENERATE_CALL" and rounds < 3:
                rounds += 1
                act = r.ask("at_tool_call", {"name": "f", "arguments": "{}", "index": i})
            gave_up = gave_up or bool(act and act["kind"] == "GIVE_UP")
        if gave_up:
            break
        act = r.ask("at_finish", {"kind": "stop", "answer": ""}, 100)
        if act and act["kind"] == "CONTINUE_HIDDEN" and rounds < 3:
            rounds, blocks, calls = rounds + 1, 1, 0
            continue
        break
    r.extras("finish", {})
    r.follow_ups({"finish": "stop"})
    r.finish({"request_id": "r1"})
    r.done("stop", {})
    return {"status": 200, "raw": r.raw, "acts": r.acts, "prompt": ids}


def _both(args, sc_kw) -> tuple:
    """(the reference host's, the engine's): the extension's own call log, the acts, the errors."""
    H, X = _load("fake_host"), _load("fake_ext")
    host = H.FakeHost(args=dict(args))
    ref_ext = H.load(X, host)
    rec = host.serve(ref_ext, H.Scenario(**sc_kw))
    ours = E.start(_fake_ext(), _host(args))
    got = _play(ours, H.Scenario(**sc_kw))
    ref = {"calls": ref_ext.calls, "acts": rec["acts"], "errors": host.errors, "raw": rec["raw"],
           "status": rec["status"]}
    eng = {"calls": ours.obj.calls, "acts": got["acts"], "errors": ours.errors, "raw": got["raw"],
           "status": got["status"]}
    return ref, eng


CASES = [({}, {"blocks": 2}), ({}, {"blocks": 1, "calls": 1}),
         ({"act": {"at_finish": "CONTINUE_HIDDEN"}}, {"blocks": 1}),
         ({"act": {"at_tool_call": "REGENERATE_CALL"}}, {"blocks": 1, "calls": 1}),
         ({"act": {"at_tool_call": "GIVE_UP"}}, {"blocks": 1, "calls": 1}),
         ({"act": {"at_request": "PROMPT_TAIL_NOTE"}}, {}), ({"raw": True}, {})]


def test_the_order_is_the_reference_hosts():
    for args, sc in CASES:
        ref, eng = _both(args, sc)
        assert ref == eng, (args, sc, ref, eng)
    ref, _ = _both({}, {"blocks": 2})
    assert ref["calls"][:3] == ["plan", "prompt", "supervisor"] and ref["calls"][-1] == "supervisor.done", ref
    return f"{len(CASES)} scenarios ask fake_ext alike on both hosts"


# ---------------------------------------------------------------------------- 3

def test_start_is_closed():
    H, X = _load("fake_host"), _load("fake_ext")
    mod = _fake_ext()

    def variant(**attrs):
        m = types.ModuleType("variant_ext")
        for k in ("EXT_API", "NAME", "VERSION", "start"):
            setattr(m, k, getattr(mod, k))
        for k, v in attrs.items():
            if v is None:
                delattr(m, k)
            else:
                setattr(m, k, v)
        return m

    def ours(module, args=None, mode="chat") -> str:
        try:
            m = E.discover(module, mode) if isinstance(module, str) else E.check_module(module)
            E.before_load(m, dict(args or {}))            # 3.0: server/app.py asks it before the load
            E.start(m, _host(args))
        except E.Refused as exc:
            line = str(exc)
            assert line.startswith("[ext] REFUSING: ") and "\n" not in line, line
            return line
        raise AssertionError(f"started: {module} {args}")

    def ref(module, args=None) -> str:
        try:
            H.load(module, H.FakeHost(args=dict(args or {})))
        except H.Refused as exc:
            return str(exc)
        raise AssertionError(f"the reference started: {module}")

    class NoHook:
        routes, models, knobs = [], [], []
    cases = [("", None), ("no_such_extension_module_x", None), (variant(EXT_API=None), None),
             (variant(EXT_API="1.3"), None), (variant(EXT_API="3.9"), None),
             (variant(EXT_API="one"), None), (mod, {"fail": "start"}),
             (mod, {"route": "/v1/chat/completions"}), (mod, {"route": "/v1/dashboard/live"}),
             (mod, {"route": "/v1/engine/disk"}), (variant(start=lambda host: NoHook()), None),
             # 3.0 (ENG-413 WP5): the monitor without before_load; every 2.x extension (the major)
             (mod, {"conductor": "on"}), (variant(EXT_API="2.0"), None), (variant(EXT_API="2.1"), None)]
    lines = []
    for module, args in cases:
        a = ours(module, args)
        b = ref(module if not (isinstance(module, types.ModuleType) and module is mod) else X, args)
        # the same reason; the reference names its own copy of the module
        assert a.replace("fake_ext", "M").replace("variant_ext", "M") == \
            b.replace("engine_ext_ref_fake_ext", "M").replace("variant_ext", "M"), (a, b)
        lines.append(a)
    assert "QSE_EXT is empty" in lines[0] and lines[3] == "[ext] REFUSING: the extension speaks engine-ext-v1 1.3; this engine speaks 3.2", lines
    assert lines[-2:] == [f"[ext] REFUSING: the extension speaks engine-ext-v1 {v}; this engine speaks 3.2"
                          for v in ("2.0", "2.1")], lines[-2:]
    assert E.discover("", "engine") is None and E.NULL.listing() == [] and E.NULL.route("GET", "/x") is None
    # an older minor starts; the extension's own paths are fine
    assert E.start(mod, _host({"route": "/v1/conductor/"})).listing() == \
        [{"name": "fake-ext", "version": "0", "ext_api": "3.2"}]
    # QSE_EXT_ARGS: a JSON object over the command line
    assert E.ext_args({"a": 1, "fail": None}, {"QSE_EXT_ARGS": '{"fail": "plan"}'}) == {"a": 1, "fail": "plan"}
    for bad in ("[1]", "{nope"):
        try:
            E.ext_args({}, {"QSE_EXT_ARGS": bad})
        except E.Refused as exc:
            assert str(exc).startswith("[ext] REFUSING: QSE_EXT_ARGS"), exc
        else:
            raise AssertionError(bad)
    return f"{len(cases)} start failures refused with the reference's line; headless; QSE_EXT_ARGS"


# ---------------------------------------------------------------------------- 4

def _counted(hook: str) -> float:
    from server.api import metrics
    return metrics.ext_errors_total.values.get((hook,), 0.0)


def test_a_request_is_open():
    H = _load("fake_host")
    mod = _fake_ext()
    for hook in ("plan", "prompt", "supervisor", "extras", "follow_ups", "finish",
                 "at_request", "at_block", "at_finish", "done"):
        before = _counted(hook)
        ext = E.start(mod, _host({"fail": hook}))
        rec = _play(ext, H.Scenario(blocks=2))
        assert rec["status"] == 200 and ext.errors.get(hook) and rec["raw"] == (hook == "plan"), (hook, rec)
        assert _counted(hook) > before, hook
        assert any(f"[ext] {hook} failed" in ln for ln in ext.host.lines), ext.host.lines
        if hook == "prompt":
            assert rec["prompt"] == [1, 2, 3], rec
        if hook in ("at_request", "at_block", "at_finish"):
            after = ext.obj.calls[ext.obj.calls.index(f"supervisor.{hook}") + 1:]
            assert not [c for c in after if c.startswith("supervisor.")], (hook, after)
            assert after[-3:] == ["extras", "follow_ups", "finish"], (hook, after)
        # the next request asks the extension again
        ext.obj.args.pop("fail")
        first = dict(ext.errors)
        again = _play(ext, H.Scenario(blocks=2))
        assert again["status"] == 200 and again["raw"] is False and ext.errors == first, (hook, ext.errors)
    for hook in ("prompt", "extras", "follow_ups", "health"):
        ext = E.start(mod, _host({"bad": hook}))
        rec = _play(ext, H.Scenario())
        if hook == "health":
            assert ext.health() == {}, hook
        assert ext.errors.get(hook) == {"prompt": 1, "extras": 4, "follow_ups": 1, "health": 1}[hook], \
            (hook, ext.errors)
    # an act its safe point does not allow: dropped, counted, the supervisor detached
    ext = E.start(mod, _host({"act": {"at_block": "CONTINUE_HIDDEN"}}))
    rec = _play(ext, H.Scenario(blocks=2))
    assert rec["acts"] == [] and ext.errors == {"at_block": 1} and rec["status"] == 200, (rec, ext.errors)
    # a Refusal from plan is the client's answer, not a failure
    ext = E.start(mod, _host({"refuse": True}))
    rec = _play(ext, H.Scenario())
    assert rec["status"] == 400 and rec["refusal"]["type"] == "invalid_request_error" and not ext.errors, rec
    # a raising health hook and stop are open too; with no extension nothing is asked
    ext = E.start(mod, _host({"fail": "health"}))
    assert ext.health() == {} and ext.errors == {"health": 1}
    ext = E.start(mod, _host({"fail": "stop"}))
    ext.stop()
    assert ext.errors == {"stop": 1}
    r = E.NULL.request({})
    assert r.begin() is None and r.prompt([1]) == [1] and r.ask("at_request", {}) == [] and r.extras("x", {}) == []
    return "10 failures served open and counted, 4 bad shapes, a misplaced act, a Refusal, the headless Request"


# ---------------------------------------------------------------------------- 5 to 8: a fake engine

#: test extensions written into a folder on PYTHONPATH (fake_ext.py's folder is there too)
MODULES = {
    "ext_major1.py": "from fake_ext import *  # noqa\nfrom fake_ext import start  # noqa\nEXT_API = '1.3'\n",
    "ext_major2.py": "from fake_ext import *  # noqa\nfrom fake_ext import start  # noqa\nEXT_API = '2.0'\n",
    "ext_nohook.py": ("EXT_API = '3.0'\nNAME = 'nohook'\nVERSION = '0'\n\n\nclass _X:\n"
                      "    routes, models, knobs = [], [], []\n\n    def health(self):\n        return {}\n\n\n"
                      "def start(host):\n    return _X()\n"),
    "ext_boom.py": ("import fake_ext\n\nEXT_API = '3.0'\nNAME = 'boom'\nVERSION = '1'\n\n\n"
                    "class Boom(fake_ext.FakeExt):\n    def __init__(self, host):\n        super().__init__(host)\n"
                    "        self.routes = [{'method': 'GET', 'prefix': '/v1/boom', 'handler': self.boom},\n"
                    "                       {'method': 'GET', 'prefix': '/v1/fine', 'handler': lambda r: (201, "
                    "{'X-Ext': 'fine'}, {'path': r.path, 'query': r.query})}]\n\n"
                    "    def boom(self, request):\n        raise RuntimeError('the route failed on purpose')\n\n\n"
                    "def start(host):\n    return Boom(host)\n"),
}
RAW = "qwen38-spark-engine"


def _torch() -> bool:
    try:
        import torch  # noqa: F401
        return True
    except ImportError:
        return False


def _mods() -> str:
    d = tempfile.mkdtemp(prefix="ext-mods-")
    for name, text in MODULES.items():
        with open(os.path.join(d, name), "w") as f:
            f.write(text)
    return d


def _engine(env_extra: dict, conductor: str | None = None, extra=()):
    from test_conductor_e2e import LAUNCH, _env
    tmp = tempfile.mkdtemp(prefix="ext-host-")
    env = dict(_env(), HOME=tmp, PYTHONPATH=os.pathsep.join(
        p for p in (ROOT, DIR, _mods(), os.environ.get("PYTHONPATH")) if p), **env_extra)
    args = [sys.executable, "-c", LAUNCH, "--fake-engine", "--port", "0", "--served-model", RAW,
            "--usage-ledger", os.path.join(tmp, "ledger.sqlite3"), "--fake-tps", "2000", *extra]
    if conductor:
        args += ["--conductor-config", conductor, "--conductor-defaults", "shipped"]
    return subprocess.Popen(args, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True)


def _refused(env_extra: dict, conductor: str | None = None) -> tuple[str, str]:
    p = _engine(env_extra, conductor)
    out, _ = p.communicate(timeout=180)
    assert p.returncode != 0, out[-3000:]
    lines = [ln for ln in out.splitlines() if "[ext] REFUSING:" in ln]
    assert len(lines) == 1 and lines[0].startswith("[ext] REFUSING: "), out[-3000:]
    assert "[server] listening" not in out, out[-3000:]
    return lines[0], out


def test_the_engine_refuses_to_start():
    if not _torch():
        return "skipped: no torch"
    cdir = tempfile.mkdtemp(prefix="ext-cond-")
    before = [({}, cdir, "chat mode needs an extension (QSE_EXT is empty)"),
              ({"QSE_EXT": "no_such_extension_module_x"}, None, "QSE_EXT=no_such_extension_module_x does not import"),
              ({"QSE_EXT": "ext_major1"}, None, "the extension speaks engine-ext-v1 1.3; this engine speaks 3.2"),
              # 3.0 (ENG-413 WP5): a 2.x extension is refused before the weights load, in chat mode too
              ({"QSE_EXT": "ext_major2"}, None, "the extension speaks engine-ext-v1 2.0; this engine speaks 3.2"),
              ({"QSE_EXT": "ext_major2"}, cdir, "the extension speaks engine-ext-v1 2.0; this engine speaks 3.2"),
              ({"QSE_EXT": "fake_ext", "QSE_EXT_ARGS": "[1]"}, None, "QSE_EXT_ARGS is not a JSON object")]
    after = [({"QSE_EXT": "ext_nohook"}, None, "ext_nohook lacks the hooks"),
             ({"QSE_EXT": "fake_ext", "QSE_EXT_ARGS": '{"fail": "start"}'}, None, "fake_ext.start failed"),
             ({"QSE_EXT": "fake_ext", "QSE_EXT_ARGS": '{"route": "/v1/models"}'}, None,
              "fake_ext claims the engine's path /v1/models"),
             ({"QSE_EXT": "tandemapp.engine_ext"}, None, "tandemapp.engine_ext runs the conductor")]
    for env, conductor, want in before + after:
        line, out = _refused(env, conductor)
        assert want in line, (want, line)
        loaded = "[server] FAKE ENGINE" in out
        assert loaded == ((env, conductor, want) in after), (want, "loaded" if loaded else "not loaded")
    return f"{len(before)} refused before the weights load, {len(after)} after it and before serving"


def _get(port, path, token=True, raw=False):
    from test_conductor_e2e import ADMIN, _call
    return _call(port, "GET", path, token=ADMIN if token else None, raw=raw)


def _metric(port, name) -> dict:
    _, _, text = _get(port, "/metrics", raw=True)
    out = {}
    for ln in text.decode().splitlines():
        m = re.match(rf'{name}\{{hook="([a-z_]+)"\}} ([0-9.]+)$', ln)
        if m:
            out[m.group(1)] = float(m.group(2))
    return out


def _chat(port):
    from test_conductor_e2e import ADMIN, _call
    return _call(port, "POST", "/v1/chat/completions",
                 {"model": RAW, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 8},
                 token=ADMIN)


def _up(p) -> tuple[int, list]:
    """The port from the listening line, and the lines read until /health answers."""
    seen, end, port = [], time.time() + 120, None
    while port is None and time.time() < end:
        line = p.stdout.readline()
        if not line:
            raise AssertionError("the server ended before it listened:\n" + "".join(seen[-40:]))
        seen.append(line)
        m = re.search(r"\[server\] listening on http://[^:]+:(\d+)", line)
        if m:
            port = int(m.group(1))
    while time.time() < end:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2)
            return port, seen
        except OSError:
            time.sleep(0.2)
    raise AssertionError(f"port {port} did not answer")


def _stop(p, seen=()) -> str:
    p.send_signal(signal.SIGTERM)
    try:
        out, _ = p.communicate(timeout=60)
    except subprocess.TimeoutExpired:
        p.kill()
        out, _ = p.communicate(timeout=10)
    return "".join(seen) + out


def test_a_headless_engine():
    if not _torch():
        return "skipped: no torch"
    p = _engine({})
    seen: list = []
    try:
        port, seen = _up(p)
        st, _, h = _get(port, "/health")
        assert st == 200 and h["extensions"] == [] and h["mode"] == "engine", h
        st, _, m = _get(port, "/v1/models")
        assert [x["id"] for x in m["data"]] == [RAW], m
        for path in ("/dashboard", "/dashboard/", "/dashboard/assets/a.js"):
            st, _, b = _get(port, path, token=False)
            assert st == 404 and b["error"]["type"] == "no_ui" and "TandemApp" in b["error"]["message"], (path, b)
        from test_conductor_e2e import ADMIN, _call
        for method, path, body in (("GET", "/v1/conductor/settings", None), ("PUT", "/v1/conductor/settings", {}),
                                   ("GET", "/v1/library", None), ("GET", "/v1/library/x", None),
                                   ("GET", "/v1/memory", None), ("POST", "/v1/memory/notes", {}),
                                   ("GET", "/v1/laya", None), ("POST", "/v1/laya/ask", {}),
                                   ("GET", "/s/nWJkUiDZ59TX/v1/library", None),
                                   ("GET", "/v1/config", None), ("POST", "/v1/collect/batch", {}),
                                   ("DELETE", "/v1/conductor/memory/notes/x", None)):
            st, _, b = _call(port, method, path, body, token=ADMIN)
            assert st == 404 and b["error"]["type"] == "not_on_engine" and "TandemApp" in b["error"]["message"], \
                (method, path, st, b)
        st, b = _chat(port)[0], _chat(port)[2]
        assert st == 200 and b["choices"][0]["message"], b
        assert _metric(port, "qse_ext_errors_total") == {}
    finally:
        out = _stop(p, seen)
    assert "[ext] none: a headless engine" in out, out[-2000:]
    return "extensions [], 3 page paths no_ui, 12 paths not_on_engine, the raw model, a chat served"


def test_an_extension_on_a_fake_engine():
    if not _torch():
        return "skipped: no torch"
    # fake_ext with a health hook that raises: open, counted
    p = _engine({"QSE_EXT": "fake_ext", "QSE_EXT_ARGS": '{"fail": "health"}'})
    seen: list = []
    try:
        port, seen = _up(p)
        st, _, h = _get(port, "/health")
        assert st == 200 and h["extensions"] == [{"name": "fake-ext", "version": "0", "ext_api": "3.2"}], h
        assert "fake" not in h["loaded"], h["loaded"]
        assert _metric(port, "qse_ext_errors_total").get("health", 0) >= 1
        st, _, m = _get(port, "/v1/models")
        assert [x["id"] for x in m["data"]] == ["TandemLLM"], m
        for name in ("TandemLLM", "tandemllm", RAW):
            st, _, card = _get(port, f"/v1/models/{name}")
            assert st == 200 and card["id"] == "TandemLLM", (name, st, card)
        assert _get(port, "/v1/models/nope")[0] == 404
        st, hd, body = _get(port, "/v1/fake/", raw=True)
        assert st == 200 and body == b"{}", (st, body)
        assert _get(port, "/dashboard/", token=False)[2]["error"]["type"] == "no_ui"
        assert _get(port, "/v1/conductor/settings")[2]["error"]["type"] == "not_on_engine"
        assert _chat(port)[0] == 200
    finally:
        out = _stop(p, seen)
    assert "[ext] on: fake-ext 0 (engine-ext-v1 3.2)" in out and "[ext] health failed" in out, out[-2000:]
    # a route that raises: 500 extension_error, counted; a route's own answer; stop at the drain, open
    p = _engine({"QSE_EXT": "ext_boom", "QSE_EXT_ARGS": '{"fail": "stop"}'})
    seen: list = []
    try:
        port, seen = _up(p)
        st, _, h = _get(port, "/health")
        assert h["loaded"].get("fake") is True and h["extensions"][0]["name"] == "boom", h
        st, _, b = _get(port, "/v1/boom")
        assert st == 500 and b["error"]["type"] == "extension_error" and "on purpose" in b["error"]["message"], b
        assert _metric(port, "qse_ext_errors_total") == {"route": 1.0}
        st, hd, b = _get(port, "/v1/fine?x=1")
        assert st == 201 and hd.get("X-Ext") == "fine" and b == {"path": "/v1/fine", "query": "x=1"}, (st, hd, b)
        assert _chat(port)[0] == 200
    finally:
        out = _stop(p, seen)
    assert "[ext] stop failed" in out and "[server] stopped." in out, out[-2000:]
    return "listed, health open and counted, its models with the raw alias, a route answered, " \
           "a raising route 500 extension_error, stop open"


def test_chat_mode_through_the_adapter():
    if not _torch():
        return "skipped: no torch"
    cdir = tempfile.mkdtemp(prefix="ext-cond-")
    p = _engine({"QSE_EXT": "tandemapp.engine_ext"}, cdir, extra=("--conductor", "on"))
    seen: list = []
    try:
        port, seen = _up(p)
        st, _, h = _get(port, "/health")
        assert st == 200 and h["mode"] == "chat" and h["loaded"]["conductor"] is True, h
        (x,) = h["extensions"]
        with open(os.path.join(pairapp.APP, "VERSION")) as f:
            release = f.read().strip()
        assert x == {"name": "tandemapp", "version": release, "ext_api": E.EXT_API}, x
        st, _, m = _get(port, "/v1/models")
        assert [y["id"] for y in m["data"]] == ["TandemLLM"], m
        assert _get(port, f"/v1/models/{RAW}")[2]["id"] == "TandemLLM"
        st, _, s = _get(port, "/v1/conductor/settings")
        assert st == 200 and "default_profile" in json.dumps(s), (st, str(s)[:300])
        st, hd, page = _get(port, "/dashboard/", token=False, raw=True)
        # ENG-413 WP14: no engine serves the page, with the extension too
        err = json.loads(page) if isinstance(page, (bytes, str)) else page
        assert st == 404 and err["error"]["type"] == "no_ui", (st, page)
        st, _, b = _get(port, "/v1/library")
        assert b.get("error", {}).get("type") != "not_on_engine", b
        assert _metric(port, "qse_ext_errors_total") == {}
    finally:
        out = _stop(p, seen)
    for want in ("[ext] QSE_EXT=tandemapp.engine_ext", "[ext] on: tandemapp", "[conductor] journal:",
                 "[conductor] on:"):
        assert want in out, (want, out[-3000:])
    assert out.index("[conductor] journal:") < out.index("[conductor] on:") < out.index("[server] listening")
    return "mode chat, the adapter listed, TandemLLM, the conductor's routes and the page through it"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
