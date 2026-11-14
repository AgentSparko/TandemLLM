"""TandemApp's engine extension tandemapp/engine_ext.py (ENG-413 WP4, SRV-821, QA SRV-822) against the
engine's interface engine-ext-v1 at the version PIN names, through the reference host
(contracts/engine-ext-v1/fake_host.py). No engine and no model: the host is the reference's.

  1  the module is an extension of the pinned interface: EXT_API is ext.json's version and the
     reference host takes it (a 1.x or a 2.x host does not: 3.0), NAME tandemapp, VERSION TandemApp's
     release, every hook, every optional call (extension.calls, 3.0's included) and the module's
     optional functions with exactly ext.json's arguments, no interim `writer` and nothing kept
     interim of it, the knobs knobs.json's                           test_the_module_fits_the_interface
  2  start is closed: with neither a conductor directory nor the monitor's policy the reference
     host refuses it with one line; nothing is started              test_start_needs_work
  3  the session monitor alone: the reference host asks the hooks in ext.json's order (a plain
     request, a tool call, a raw request: nothing after plan), no error; the plan has no routing,
     the prompt comes back as built; the monitor's journal counts through metrics.event
                                                                  test_the_monitor_alone
  4  chat mode with a conductor directory: the start stores the shipped documents, the journal and
     the Conductor (the log lines in their order), /v1/models lists TandemLLM, health says
     conductor; a raw request's plan has routing; the optional calls answer (server_mode,
     served_name, strip_states, side_first_max); attach builds the object without a start
                                                                  test_chat_mode
  5  the routes answer (status, headers, body): /v1/conductor/settings 200 JSON with no-store,
     the engine's refusal from allowed() passes as it is (401 with its header), /v1/memory
     without a session address 403 with its journal row, the page and the library answer; a
     route that writes nothing is an error the host counts           test_the_routes_answer
  6  3.0 (ENG-413 WP5): the conductor's engine screens answer over host.engine: the handler's body
     plus contract_version, a Refusal as the conductor's own error (no contract_version), the base
     its messages name is the conductor's path; with no engine (the controller) the status screens
     say ENGINE_ONLY and the rest 404; the release tree's speed-guard report reaches the Conductor
                                                                  test_the_engine_screens
  7  3.0: the calls outside a request: document, library_text, shadow_wanted, monitor_mode,
     role_errors, deciders_api, narrator over a slot (its status), deciders (the classify backends
     registered), memory_writer (the writer with gate(conv) asking the conductor's router)
                                                                  test_the_calls_outside_a_request
  8  3.0, the module before the start: read_document reads the stored document (version 0, the
     built-in one, in a new directory); before_load checks the session monitor's policy and gives
     its line, nothing without --conductor shadow|on, and a policy that does not load stops the
     start with the line the engine printed before                  test_the_module_before_the_start

Standard library only (the reference host is loaded by path). Run: python tests/test_engine_ext.py
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import os
import sys
import tempfile
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from proxy._version import release  # noqa: E402
from proxy_util import run  # noqa: E402
from tandemapp import engine_ext, knobs  # noqa: E402

DIR = os.path.join(ROOT, "contracts", "engine-ext-v1")


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"engine_ext_ref_{name}", os.path.join(DIR, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


H = _load("fake_host")
with open(os.path.join(DIR, "ext.json")) as _f:
    SPEC = json.load(_f)


class Tok:
    """A tokenizer as the monitor and the router call it: one id per character."""

    def __call__(self, text, add_special_tokens=False):
        return types.SimpleNamespace(input_ids=[ord(c) for c in text])

    def encode(self, text, add_special_tokens=False):
        return [ord(c) for c in text]

    def decode(self, ids, skip_special_tokens=False):
        return "".join(chr(i) for i in ids)


def _params(fn) -> list:
    return [p.name for p in inspect.signature(fn).parameters.values()
            if p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)]


def _monitor_host(**kw):
    from tandemapp.conductor.monitor import policy
    host = H.FakeHost(args={"conductor": "on", "fake_engine": True, "conductor_watch_s": 3600.0,
                            **kw.pop("args", {})},
                      state={"conductor_policy": policy.load_default(), "tok": Tok()}, **kw)
    host.ledger_path = None
    return host


def _chat_host():
    from tandemapp.session_tokens import SessionTokens
    root = tempfile.mkdtemp(prefix="engine-ext-")
    state = {"tok": Tok(), "model": "qwen38-raw", "started": time.time(), "port": 0, "engine": None,
             "session_tokens": SessionTokens(os.path.join(root, "session-tokens.json"))}
    host = H.FakeHost(args={"conductor_defaults": "shipped"}, ext_dir=root, state=state)
    host.ledger_path = None
    host.identity_name = lambda: "qwen38-raw"
    return host


# ---------------------------------------------------------------------------- 1, 2

def test_the_module_fits_the_interface():
    X = engine_ext
    assert X.EXT_API == SPEC["version"] and H.accepts(X.EXT_API) is None, (X.EXT_API, SPEC["version"])
    assert H.accepts(X.EXT_API, "1.2") is not None and X.EXT_API == "3.0"
    assert H.accepts(X.EXT_API, "2.1") is not None                    # another major: a 2.x host refuses it
    assert H.accepts("2.0", X.EXT_API) is not None                    # and this host a 2.x extension
    for name, args in (("read_document", ["ext_dir", "name"]), ("before_load", ["args"])):
        assert name in SPEC["discovery"]["module_optional"] and _params(getattr(X, name)) == args, name
    assert X.NAME == "tandemapp" and X.VERSION == (release() or "0"), (X.NAME, X.VERSION)
    for attr in SPEC["discovery"]["module"]:
        assert hasattr(X, attr), attr
    obj = X.attach(H.FakeHost())
    for h in SPEC["extension"]["hooks"]:
        assert _params(getattr(obj, h["name"])) == h["args"], (h["name"], _params(getattr(obj, h["name"])))
    for c in SPEC["extension"]["calls"]:
        assert _params(getattr(obj, c["name"])) == c["args"], c["name"]
    kept = " ".join(k["what"] for k in SPEC["kept_interim"])
    assert "writer" not in kept and not hasattr(obj, "writer") and _params(obj.memory_writer) == []
    for m in SPEC["supervisor"]["methods"]:
        assert callable(getattr(X.Watch, m["name"], None)), m["name"]
    with open(os.path.join(DIR, "knobs.json")) as f:
        doc = json.load(f)
    assert sorted(obj.knobs) == sorted(doc["prefix"] + k for k in doc["knobs"]) and doc["knobs"] == knobs.DEFAULTS
    for attr in SPEC["extension"]["attributes"]:
        assert isinstance(getattr(obj, attr), list), attr
    return f"engine-ext-v1 {X.EXT_API}: {len(SPEC['extension']['hooks'])} hooks, " \
           f"{len(SPEC['extension']['calls'])} calls, 2 module functions, VERSION {X.VERSION}"


def test_start_needs_work():
    host = H.FakeHost()
    try:
        H.load(engine_ext, host)
    except H.Refused as exc:
        line = str(exc)
    else:
        raise AssertionError("started with nothing to run")
    assert line.startswith("[ext] REFUSING: tandemapp.engine_ext.start failed: RuntimeError(") \
        and "conductor directory" in line, line
    assert "conductor" not in host.state and "monitor" not in host.state and "journal" not in host.state
    return line[:90]


# ---------------------------------------------------------------------------- 3

PLAIN = ["plan", "prompt", "supervisor", "at_request", "extras start", "at_block", "extras block",
         "at_block", "extras block", "at_finish", "extras finish", "follow_ups", "finish", "done"]


def test_the_monitor_alone():
    host = _monitor_host()
    ext = H.load(engine_ext, host)
    try:
        assert host.state["monitor"] is not None and host.state.get("conductor") is None
        assert any(ln.startswith("[conductor] journal: in memory") for ln in host.lines), host.lines
        rec = host.serve(ext, H.Scenario(blocks=2))
        assert rec["trace"] == PLAIN and not rec["errors"] and rec["prompt"] == [1, 2, 3], rec
        rec = host.serve(ext, H.Scenario(blocks=1, calls=1))
        assert rec["trace"][:4] == PLAIN[:4] and "at_tool_call" in rec["trace"] \
            and rec["trace"][-1] == "done" and not rec["errors"], rec
        plan = ext.plan({"body": {"messages": []}, "headers": {"X-Tandem": "raw"}, "client_kind": "t",
                         "request_id": "r", "chat": True})
        assert plan.routing is None and not plan.active and plan.server_mode == "raw"
        assert ext.supervisor(plan, {"prompt_ids": [1]}) is None        # raw: nothing watches
        assert ext.prompt(plan, [5, 6], {"in_think": True}) == {"ids": [5, 6], "in_think": True,
                                                                 "client_ids": None}
        assert ext.server_mode(None, 2) == "shadow" and ext.server_mode("raw", 1) == "raw"
        assert ext.memory_writer() == (None, None) and host.errors == {}, host.errors
        assert ext.monitor_mode() == "on" and ext.document("residency") is None
    finally:
        host.state["monitor"].stop_watch()
    return f"{len(PLAIN)} steps in order, a call, a raw plan; {len(host.metrics.events)} journal rows counted"


# ---------------------------------------------------------------------------- 4, 5

def test_chat_mode():
    host = _chat_host()
    ext = H.load(engine_ext, host)
    lines = host.lines
    assert any(ln.startswith("[conductor] shipped default stored:") for ln in lines), lines
    order = [next(i for i, ln in enumerate(lines) if ln.startswith(p))
             for p in ("[conductor] journal:", "[conductor] on:")]
    assert order == sorted(order), lines
    cond = host.state["conductor"]
    assert cond is not None and host.state["journal"] is not None and "escapes" in host.state
    assert [m["id"] for m in ext.models] == ["TandemLLM"], ext.models
    assert ext.health() == {"conductor": True}
    plan = ext.plan({"body": {"model": "TandemLLM", "messages": [{"role": "user", "content": "hi"}]},
                     "headers": {"X-Tandem": "raw"}, "client_kind": "test", "request_id": "r1",
                     "chat": True, "user": None, "count_tokens": lambda: 3})
    assert plan.routing is not None and plan.server_mode == "raw"
    assert ext.served_name() == "TandemLLM" and isinstance(ext.side_first_max(), int)
    msgs = [{"role": "user", "content": "hi"}]
    got, n = ext.strip_states(msgs)
    assert n == 0 and got == msgs
    assert ext.server_mode("on", 1) == "raw"                 # no monitor: raw
    again = engine_ext.attach(H.FakeHost(state=host.state))
    assert again.cond is cond and again.models == ext.models
    return f"{len(lines)} start lines in order, TandemLLM, a raw plan with routing, attach"


class Route:
    """A route's request as the engine hands it (engine-ext-v1 2.0)."""

    def __init__(self, method, path, query="", body=None, refuse=None, session_token=None):
        self.method, self.path, self.query, self.body = method, path, query, body
        self.headers, self.peer, self.auth = {}, "127.0.0.1", None
        self.session_token, self.session = session_token, None
        self._refuse = refuse

    def allowed(self, route):
        return self._refuse


def test_the_routes_answer():
    host = _chat_host()
    ext = H.load(engine_ext, host)
    st, hd, body = ext._conductor(Route("GET", "/v1/conductor/settings"))
    assert st == 200 and hd["Content-Type"] == "application/json" and hd["Cache-Control"] == "no-store"
    assert "Content-Length" not in hd and "default_profile" in body.decode(), (st, hd, body[:200])
    refusal = (401, {"WWW-Authenticate": 'Bearer realm="TandemLLM"'},
               {"error": {"type": "unauthorized", "message": "a bearer token or the dashboard session is needed"}})
    st, hd, body = ext._conductor(Route("PUT", "/v1/conductor/settings", body={}, refuse=refusal))
    assert (st, hd["WWW-Authenticate"], json.loads(body)) == (401, refusal[1]["WWW-Authenticate"], refusal[2])
    st, hd, body = ext._memory(Route("GET", "/v1/memory"))
    assert st == 403 and json.loads(body)["error"]["type"] == "forbidden", (st, body)
    st, hd, body = ext._page(Route("GET", "/dashboard/"))
    assert st in (200, 404) and body, (st, body[:120])
    st, hd, body = ext._library(Route("GET", "/v1/library"))
    assert st == 200 and isinstance(json.loads(body), dict), (st, body[:200])
    r = engine_ext._Reply(Route("GET", "/x"))
    try:
        r.answer()
    except RuntimeError:
        pass
    else:
        raise AssertionError("an empty answer passed")
    return "the conductor's settings, a refusal as the engine gives it, memory 403, the page, the library"


# ---------------------------------------------------------------------------- 6, 7, 8 (3.0)

def test_the_engine_screens():
    from tandemapp.conductor import routes
    seen = []

    def plan():
        return 200, {"version": 3, "plan": {}}

    def disk_clear():
        raise H.Refusal(409, "not_running", "the disk tier is not running on this server")

    def deciders(method, rest, body, base="/v1/engine/deciders"):
        seen.append((method, rest, base))
        if rest == ["status"]:
            return 200, {"enabled": False, "why": "no models"}
        raise H.Refusal(404, "not_found", f"no route {method} {base}/{'/'.join(rest)}")

    host = _chat_host()
    host.engine = {"residency_plan": plan, "disk": lambda: (200, {"running": False}),
                   "disk_clear": disk_clear, "deciders": deciders,
                   "narrator": lambda m, w, b, base="": (200, {"enabled": False, "why": "none"}),
                   "shadow": lambda m, w, b, base="": (200, {"enabled": False, "why": "none"})}
    host.shipped_guard = "/nowhere/release/speed-guard.json"
    ext = H.load(engine_ext, host)
    cond = host.state["conductor"]
    assert cond.shipped_guard == host.shipped_guard and set(cond.engine) == set(host.engine)

    def get(method, path, body=None):
        st, _hd, raw = ext._conductor(Route(method, path, body=body))
        return st, json.loads(raw)
    assert get("GET", "/v1/conductor/residency/plan") == (200, {"version": 3, "plan": {},
                                                               "contract_version": "1.0"})
    assert get("GET", "/v1/conductor/memory/disk")[1]["running"] is False
    assert get("POST", "/v1/conductor/memory/disk/clear", {}) == (409, {"error": {
        "type": "not_running", "message": "the disk tier is not running on this server"}})
    assert get("GET", "/v1/conductor/deciders/status")[1]["why"] == "no models"
    st, out = get("POST", "/v1/conductor/deciders/models/x/nope", {})
    assert st == 404 and out == {"error": {"type": "not_found", "message":
                                           "no route POST /v1/conductor/deciders/models/x/nope"}}, out
    assert seen[-1] == ("POST", ["models", "x", "nope"], "/v1/conductor/deciders"), seen
    assert get("GET", "/v1/conductor/narrator/status")[1]["why"] == "none"
    assert get("GET", "/v1/conductor/shadow/status")[1]["why"] == "none"
    # the controller runs no engine: its own words, and no engine handler
    from tandemapp.conductor.hook import Conductor
    bare = Conductor(tempfile.mkdtemp(prefix="engine-ext-bare-"), journal=False)
    assert bare.engine == {} and bare.shipped_guard is None
    for what in ("deciders", "narrator", "shadow"):
        st, out, _ = routes._route(None, bare, "GET", [what, "status"], {}, {}, None)
        assert (st, out["enabled"], out["why"]) == (200, False, routes.ENGINE_ONLY), (what, out)
    for parts in (["residency", "plan"], ["deciders", "test"], ["shadow", "ask"]):
        try:
            routes._route(None, bare, "POST" if parts[-1] != "plan" else "GET", parts, {}, {}, None)
        except routes.ApiError as exc:
            assert (exc.status, str(exc)) == (404, routes.ENGINE_ONLY), parts
        else:
            raise AssertionError(parts)
    return "the engine's bodies with contract_version, its refusals as before, the controller's own words"


class Slot:
    """A small-model slot as the engine hands it to narrator()."""

    state, loaded_at = "not_loaded", None
    model = types.SimpleNamespace(name="fake-narrator", jobs=False)

    def status(self):
        return {"state": self.state, "model": self.model.name}


def test_the_calls_outside_a_request():
    host = _chat_host()
    ext = H.load(engine_ext, host)
    cond = host.state["conductor"]
    v, doc = ext.document("residency")
    assert (v, doc) == tuple(cond.store.peek("residency")) and "claims" in doc
    assert ext.document("router")[1]["effort"]["levels"]
    wanted = any(((cond.store.peek("profiles", n)[1] or {}).get("shadow") or {}).get("mode") == "on"
                 for n in cond.store.names())
    assert ext.shadow_wanted() is wanted and ext.monitor_mode() is None      # the shipped profiles
    assert ext.role_errors({}, "narrator", None), "an empty role breaks the contract"
    api = ext.deciders_api(cond.store.peek("deciders")[1])
    assert {"mode", "stats"} <= set(api), api
    n = ext.narrator(Slot())
    st = n.status()
    assert st["enabled"] and st["slot"]["model"] == "fake-narrator" and st["settings"]["mode"] in ("on", "off")
    from tandemapp.conductor import classify
    reg = types.SimpleNamespace(families=["laya"])
    ext.deciders(reg)
    assert {"self", "laya"} <= set(classify.BACKENDS), sorted(classify.BACKENDS)
    w, cfg = ext.memory_writer()
    if cond.memory is not None:
        asked = []
        cond.memory.writer.gate = lambda router, conv: asked.append((router, conv)) or ("yes", "t")
        w2, _ = ext.memory_writer()
        assert w2.gate("c1") == ("yes", "t") and asked == [(cond.router, "c1")], asked
        assert isinstance(w2.stats, dict) and w2.state("nobody") is None and "mode" in cfg
    else:
        assert (w, cfg) == (None, None)
    return "documents, library, shadow, monitor, role contract, deciders api, narrator, classify, writer"


def test_the_module_before_the_start():
    root = tempfile.mkdtemp(prefix="engine-ext-early-")
    v, doc = engine_ext.read_document(root, "residency")
    from tandemapp.conductor import schema
    assert v == 0 and doc == schema.seed("residency")
    assert engine_ext.before_load({"conductor": "raw"}) is None and engine_ext._EARLY["policy"] is None
    line = engine_ext.before_load({"conductor": "on"})
    assert line.startswith("policy ") and " version " in line and engine_ext._EARLY["policy"] is not None
    bad = os.path.join(root, "bad.yaml")
    with open(bad, "w") as f:
        f.write("version: nope\n")
    try:
        engine_ext.before_load({"conductor": "on", "conductor_policy": bad})
    except SystemExit as exc:
        assert str(exc).startswith("[conductor] the policy does not load:"), exc
    else:
        raise AssertionError("a bad policy loaded")
    engine_ext.before_load({})
    return f"read_document v0, before_load: {line[:40]}..."


if __name__ == "__main__":
    run(globals())
