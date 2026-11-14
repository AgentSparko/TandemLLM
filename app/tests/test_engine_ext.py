"""TandemApp's engine extension tandemapp/engine_ext.py (ENG-413 WP4, SRV-821, QA SRV-822) against the
engine's interface engine-ext-v1 at the version PIN names, through the reference host
(contracts/engine-ext-v1/fake_host.py). No engine and no model: the host is the reference's.

  1  the module is an extension of the pinned interface: EXT_API is ext.json's version and the
     reference host takes it (a 1.2 host does not), NAME tandemapp, VERSION TandemApp's release,
     every hook and every optional call (extension.calls) with exactly ext.json's arguments,
     `writer` (kept_interim) beside them, the knobs knobs.json's     test_the_module_fits_the_interface
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
    assert H.accepts(X.EXT_API, "1.2") is not None
    assert X.NAME == "tandemapp" and X.VERSION == (release() or "0"), (X.NAME, X.VERSION)
    for attr in SPEC["discovery"]["module"]:
        assert hasattr(X, attr), attr
    obj = X.attach(H.FakeHost())
    for h in SPEC["extension"]["hooks"]:
        assert _params(getattr(obj, h["name"])) == h["args"], (h["name"], _params(getattr(obj, h["name"])))
    for c in SPEC["extension"]["calls"]:
        assert _params(getattr(obj, c["name"])) == c["args"], c["name"]
    kept = " ".join(k["what"] for k in SPEC["kept_interim"])
    assert "writer()" in kept and _params(obj.writer) == []
    for m in SPEC["supervisor"]["methods"]:
        assert callable(getattr(X.Watch, m["name"], None)), m["name"]
    with open(os.path.join(DIR, "knobs.json")) as f:
        doc = json.load(f)
    assert sorted(obj.knobs) == sorted(doc["prefix"] + k for k in doc["knobs"]) and doc["knobs"] == knobs.DEFAULTS
    for attr in SPEC["extension"]["attributes"]:
        assert isinstance(getattr(obj, attr), list), attr
    return f"engine-ext-v1 {X.EXT_API}: {len(SPEC['extension']['hooks'])} hooks, " \
           f"{len(SPEC['extension']['calls'])} calls and writer, VERSION {X.VERSION}"


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
        assert ext.writer() == (None, None) and host.errors == {}, host.errors
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
    """A route's request as the engine hands it (engine-ext-v1 1.3)."""

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


if __name__ == "__main__":
    run(globals())
