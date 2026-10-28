"""SRV-734: the OpenAI data plane behind TandemApp's controller (SRV-733): the five route families
answered as the engine answers them, the engine's keys, long thinking, each request counted once,
and the reverse proxy's locations with their rollback.

The engine is the fake backend as a `tandemllm` backend (tests/fake_backend.py: its board of the
requests in flight, its model list with `decision_models`, its cards and aliases); the controller is
the standalone proxy in this process (tests/proxy_util.py). A request sent to the fake directly is
what a client of the engine gets today; the same request through the controller must match it.

  1  Gherkin "Existing client keeps working" / AC1: chat (whole and streamed), completions,
     embeddings, Responses, /v1/models and /v1/models/{id} (a listed id, the raw model's alias, a
     decision model, an unknown id) answer through the controller with the engine's status and
     body; a model id no list names goes to the engine as it did (TANDEM_DEFAULT_BACKEND); a body
     that is not JSON is the engine's 400, with its row          test_the_five_routes_as_the_engine
  2  the engine gets the client's headers as sent (agent, referer, the conductor's and memory's
     headers, X-Real-IP, the key) and never one only a proxy may set; whose memory: the key's
     label, else `owner`; the answer's headers come back; the cost comment is the engine's
                                                                    test_headers_as_the_engine_sees
  3  AC2: the engine's keys (TANDEM_API_USERS_FROM) open the controller, live as the engine's file
     changes; a wrong key is the 401 the engine writes; no users there: open as the engine is; a
     file that cannot be read keeps the last answer, closed before any        test_the_engines_keys
  4  Gherkin "Long thinking is not cut" / AC3: quiet longer than the first-byte and idle timeouts,
     whole and streamed, while the engine's board holds the request: 200; the same quiet on an
     engine that dropped it ends with the timeout                    test_long_thinking_is_not_cut
  5  Gherkin "Counted once" / AC4: 10 requests through the controller are 10 rows of its ledger,
     each with its node and deployment and the engine's own row id (`upstream_request`); Usage of
     the controller's ledger counts 10                                     test_counted_once
  6  AC5 + Gherkin "Rollback" / AC6: docs/proxy.md's locations send exactly the five route families
     to the controller and every engine-only route to the engine (nginx's `=` and `^~` matching);
     the rollback is written down                                  test_locations_and_rollback

    python tests/test_data_plane.py
"""

from __future__ import annotations

import http.client
import json
import os
import re
import sqlite3
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_deployments as td  # noqa: E402
import test_fleet as tf  # noqa: E402
from proxy import config as config_mod  # noqa: E402
from proxy_util import Proxy, fake, run  # noqa: E402

KEY = "tdm-engine-key-0123456789"
DECIDERS = [{"id": "laya-english", "object": "decision_model", "created": 1,
             "owned_by": "convaiinnovations", "family": "laya"}]
CARD = {"profile": "chat", "description": "the engine with its conductor",
        "tandem": {"backend": "engine", "kind": "engine", "context_length": 262144,
                   "capabilities": {"thinking": True, "tools": True, "vision": True}}}
OUTSIDE = {"X-Real-IP": "192.0.2.7", "X-Forwarded-For": "192.0.2.7"}   # through nginx


def engine(**kw):
    """The fake as our engine: one model with the engine's card, its decision models, its alias."""
    srv, port = fake(models=["TandemLLM"], cache=False, **kw)
    srv.fake.cards["TandemLLM"] = CARD
    srv.fake.top = {"decision_models": DECIDERS}
    srv.fake.aliases = {"qwen3.8-flash-next": "TandemLLM"}
    srv.fake.board = True
    return srv, port


def env_for(port, **more):
    # no fleet of this machine: a test never reads (or claims) the TandemNode agent a box runs
    return {"TANDEM_BACKENDS": "engine", "TANDEM_BACKEND_ENGINE_KIND": "tandemllm",
            "TANDEM_BACKEND_ENGINE_URL": f"http://127.0.0.1:{port}/v1",
            "TANDEM_DEFAULT_BACKEND": "engine", "TANDEM_NODE_AGENT": "off",
            "TANDEM_LOCAL_NODE": "off", **more}


def direct(port, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    data = None if body is None else (body if isinstance(body, bytes)
                                      else json.dumps(body).encode())
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    out = r.read()
    c.close()
    return r.status, dict(r.getheaders()), out


def shape(raw: bytes, stream: bool):
    """The answer without what differs per request (ids, times): JSON, or a stream's events."""
    def clean(o):
        if isinstance(o, dict):
            return {k: clean(v) for k, v in o.items() if k not in ("id", "created",
                                                                   "created_at")}
        if isinstance(o, list):
            return [clean(v) for v in o]
        return o
    if not stream:
        return clean(json.loads(raw))
    out = []
    for ev in raw.decode().split("\n\n"):
        data = "\n".join(x[5:].lstrip() for x in ev.splitlines() if x.startswith("data:"))
        if data:
            out.append(data if data == "[DONE]" else clean(json.loads(data)))
    return out


def _closed(srv):
    srv.shutdown()
    srv.server_close()


# ---------------------------------------------------------------------------- 1. the five routes

def test_the_five_routes_as_the_engine():
    srv, port = engine()
    try:
        with Proxy(env=env_for(port)) as px:
            px.sync()
            msgs = [{"role": "user", "content": "hello there"}]
            cases = [("/v1/chat/completions", {"model": "TandemLLM", "messages": msgs}),
                     ("/v1/chat/completions", {"model": "TandemLLM", "messages": msgs,
                                               "stream": True}),
                     ("/v1/chat/completions", {"model": "TandemLLM", "messages": msgs,
                                               "stream": True,
                                               "stream_options": {"include_usage": True}}),
                     ("/v1/completions", {"model": "TandemLLM", "prompt": "hello there"}),
                     ("/v1/completions", {"model": "TandemLLM", "prompt": "hi", "stream": True}),
                     ("/v1/embeddings", {"model": "TandemLLM", "input": "hello there"}),
                     ("/v1/responses", {"model": "TandemLLM", "input": "hello there"}),
                     # an id the engine answers with its own model and no list names (the ledger
                     # has clients sending the raw model's name)
                     ("/v1/chat/completions", {"model": "qwen3.8-flash-next", "messages": msgs})]
            for path, body in cases:
                s1, h1, a = direct(port, "POST", path, body)
                s2, h2, b = px.post(path, body)
                stream = bool(body.get("stream"))
                assert (s1, shape(a, stream)) == (s2, shape(b, stream)), (path, body, a, b)
                assert h2.get("Content-Type") == h1.get("Content-Type"), (path, h1, h2)
            # /v1/models: the engine's cards and its decision models, the controller's header
            s1, _, a = direct(port, "GET", "/v1/models")
            s2, h2, b = px.get("/v1/models")
            assert (s1, json.loads(a)) == (s2, json.loads(b)), (a, b)
            assert h2.get("X-Tandem-Gateway") == "tandem-app", h2
            for name in ("TandemLLM", "qwen3.8-flash-next", "nothing-here"):
                s1, _, a = direct(port, "GET", f"/v1/models/{name}")
                s2, _, b = px.get(f"/v1/models/{name}")
                assert (s1, json.loads(a)) == (s2, json.loads(b)), (name, a, b)
            # a body that is not JSON: the engine's 400 (server/app.py do_POST), and its row
            n0 = len(px.rows())
            try:
                json.loads(b"{")
            except ValueError as exc:
                want = {"error": {"message": f"bad json: {exc}", "type": "invalid_request_error"}}
            st, _, raw = px.post("/v1/chat/completions", b"{")
            assert (st, json.loads(raw)) == (400, want), raw
            rows = px.rows()
            assert len(rows) == n0 + 1 and rows[-1]["status"] == 400, rows[-1]
        # without a default backend an unknown id is OpenAI's 404, as before SRV-733
        e = env_for(port)
        e.pop("TANDEM_DEFAULT_BACKEND")
        with Proxy(env=e) as px:
            px.sync()
            st, _, raw = px.post("/v1/chat/completions", {"model": "qwen3.8-flash-next",
                                                          "messages": [{"role": "user",
                                                                        "content": "x"}]})
            assert st == 404 and json.loads(raw)["error"]["code"] == "model_not_found", raw
    finally:
        _closed(srv)


# ---------------------------------------------------------------------------- 2. the headers

def test_headers_as_the_engine_sees():
    srv, port = engine()
    f = srv.fake
    try:
        f.script = lambda body, headers: {"content": "ok", "headers": {
            "X-Tandem": "on", "X-Tandem-Wait": "memory", "X-Tandem-Backend": "inner"}}
        sent = {"User-Agent": "opencode/1.18.32 ai-sdk/provider-utils/4.0.23",
                "Referer": "https://box.example/app/", "X-QSE-Client": "dashboard",
                "X-Tandem-Memory": "off", "X-Tandem-Profile": "coding",
                "X-OpenWebUI-Chat-Id": "c-1", "X-Tandem-Cost": "1",
                "Authorization": "Bearer sk-client-0001", **OUTSIDE,
                # only a proxy may set these: a client's never reach the engine
                "X-Tandem-User": "mallory", "X-Tandem-Relay-Id": "forged",
                "X-Tandem-Pin": "nowhere", "Cookie": "qse_dash=1.2.3"}
        body = {"model": "TandemLLM", "messages": [{"role": "user", "content": "hi"}]}
        with Proxy(env=env_for(port)) as px:
            px.sync()
            st, h, _ = px.post("/v1/chat/completions", body, headers=sent)
            got = {k.lower(): v for k, v in f.last["headers"].items()}
            assert st == 200, h
            for k in ("User-Agent", "Referer", "X-QSE-Client", "X-Tandem-Memory",
                      "X-Tandem-Profile", "X-OpenWebUI-Chat-Id", "X-Tandem-Cost",
                      "Authorization", "X-Real-IP", "X-Forwarded-For"):
                assert got.get(k.lower()) == sent[k], (k, got)
            assert got["x-tandem-user"] == "owner" and got["x-tandem-relay-id"] != "forged", got
            assert "cookie" not in got and "x-tandem-pin" not in got, got
            # the answer's own headers come back; the controller's facts are its own
            assert h.get("X-Tandem") == "on" and h.get("X-Tandem-Wait") == "memory", h
            assert h.get("X-Tandem-Backend") == "engine", h
            # streamed with X-Tandem-Cost: the engine prices what it relays; no second comment
            st, _, raw = px.post("/v1/chat/completions", dict(body, stream=True),
                                 headers={"X-Tandem-Cost": "1"})
            assert st == 200 and b"tandem-cost" not in raw, raw[-200:]
        # a key's label is whose memory; the backend's own key replaces the client's
        with Proxy(env=env_for(port, TANDEM_API_KEYS=f"laptop:{KEY}",
                               TANDEM_BACKEND_ENGINE_KEY="sk-backend-000000001")) as px:
            px.sync()
            st, _, _ = px.post("/v1/chat/completions", body,
                               headers={"Authorization": f"Bearer {KEY}", **OUTSIDE})
            got = {k.lower(): v for k, v in f.last["headers"].items()}
            assert st == 200 and got["x-tandem-user"] == "laptop", got
            assert got["authorization"] == "Bearer sk-backend-000000001", got
    finally:
        _closed(srv)


# ---------------------------------------------------------------------------- 3. the keys

def test_the_engines_keys():
    srv, port = engine()
    d = tempfile.mkdtemp(prefix="engine-cfg-")
    path = os.path.join(d, "config.sqlite3")
    eng = config_mod.ConfigDB(path, env={}, use_keychain=False).open()
    body = {"model": "TandemLLM", "messages": [{"role": "user", "content": "hi"}]}
    try:
        with Proxy(env=env_for(port, TANDEM_API_USERS_FROM=path)) as px:
            px.sync()
            # the engine has no user: open, as the engine is (a wrong key passes there too)
            assert px.post("/v1/chat/completions", body, headers=OUTSIDE)[0] == 200
            assert px.post("/v1/chat/completions", body,
                           headers={"Authorization": "Bearer nope", **OUTSIDE})[0] == 200
            user, key = eng.create_user({"label": "laptop"})
            # from now on, live: the engine's key opens, a wrong or no key is the engine's 401
            st, _, _ = px.post("/v1/chat/completions", body,
                               headers={"Authorization": f"Bearer {key}", **OUTSIDE})
            assert st == 200
            for hdr in ({}, {"Authorization": "Bearer tdm-wrong-wrong-wrong"}):
                st, h, raw = px.post("/v1/chat/completions", body, headers={**hdr, **OUTSIDE})
                assert st == 401 and raw == (
                    b'{"error": {"message": "a proxy API key is needed (Authorization: Bearer '
                    b'<key>); the admin makes one under Backends, Users", "type": '
                    b'"invalid_request_error", "code": "invalid_api_key"}}'), raw
                assert h.get("WWW-Authenticate") == 'Bearer realm="TandemLLM"', h
            st, h, _ = px.get("/v1/models", headers=OUTSIDE)
            assert st == 401 and h.get("X-Tandem-Gateway") == "tandem-app", h
            # the box's own tools stay open (trusted-local), as on the engine
            assert px.post("/v1/chat/completions", body)[0] == 200
            rows = px.rows()
            assert [r["user"] for r in rows if r["status"] == 200][-2:] == ["laptop", None], rows
            assert key not in json.dumps(rows)
            # a user turned off on the engine (another one still on): refused here at once; with
            # none left on, open again, as the engine is
            other, _ = eng.create_user({"label": "ci"})
            eng.update_user(user["id"], {"enabled": False})
            assert px.post("/v1/chat/completions", body,
                           headers={"Authorization": f"Bearer {key}", **OUTSIDE})[0] == 401
            eng.update_user(other["id"], {"enabled": False})
            assert px.post("/v1/chat/completions", body, headers=OUTSIDE)[0] == 200
            eng.update_user(user["id"], {"enabled": True})
            assert px.post("/v1/chat/completions", body, headers=OUTSIDE)[0] == 401
            # a file that cannot be read: the last answer stands (keys needed)
            px.svc.cfg.users_from = os.path.join(d, "gone", "config.sqlite3")
            assert px.post("/v1/chat/completions", body, headers=OUTSIDE)[0] == 401
        # ... and closed before any answer
        with Proxy(env=env_for(port, TANDEM_API_USERS_FROM=os.path.join(d, "none.sqlite3"))) \
                as px:
            px.sync()
            assert px.post("/v1/chat/completions", body, headers=OUTSIDE)[0] == 401
            for f in px.files():
                with open(f, "rb") as fh:
                    assert key.encode() not in fh.read(), f
    finally:
        eng.close()
        _closed(srv)


# ---------------------------------------------------------------------------- 4. long thinking

def test_long_thinking_is_not_cut():
    srv, port = engine()
    f = srv.fake
    quiet = 3.5                         # three and a half times the 1 s timeouts
    try:
        with Proxy(env=env_for(port, TANDEM_BACKEND_ENGINE_TIMEOUT_FIRST_BYTE="1",
                               TANDEM_BACKEND_ENGINE_TIMEOUT_IDLE="1")) as px:
            px.sync()
            body = {"model": "TandemLLM", "messages": [{"role": "user", "content": "think"}]}
            # whole: nothing at all until the answer; streamed: the head, then a quiet gap
            f.script = lambda b, h: {"content": "after a long think", "quiet": quiet}
            t0 = time.time()
            st, _, raw = px.post("/v1/chat/completions", body)
            assert st == 200 and json.loads(raw)["choices"][0]["message"]["content"] == \
                "after a long think" and time.time() - t0 >= quiet, raw
            f.script = lambda b, h: {"content": "after a long think", "ttft": quiet}
            st, _, raw = px.post("/v1/chat/completions", dict(body, stream=True))
            assert st == 200 and raw.rstrip().endswith(b"data: [DONE]"), raw[-200:]
            assert b"timeout" not in raw, raw
            # an engine that no longer holds the request (or has no board): cut as before
            f.board = False
            f.script = lambda b, h: {"content": "late", "quiet": quiet}
            st, _, raw = px.post("/v1/chat/completions", body)
            assert st == 504 and b"timeout_first_byte" in raw, raw
            f.script = lambda b, h: {"content": "late", "ttft": quiet}
            st, _, raw = px.post("/v1/chat/completions", dict(body, stream=True))
            assert st == 200 and b"timeout_idle" in raw, raw[-300:]
            rows = px.rows()
            assert [r["finish_reason"] for r in rows[-4:]] == ["stop", "stop", "timeout",
                                                               "timeout"], rows[-4:]
    finally:
        _closed(srv)


# ---------------------------------------------------------------------------- 5. counted once

def test_counted_once():
    fx = td.Fleet({"Box": td.Node("ONCE-B-0001", "box", ["TandemLLM"])})
    try:
        box = fx.ids["Box"]
        eport = fx.nodes["Box"].eport
        fx.nodes["Box"].srv.fake.board = True
        # the box's engine, as the controller on a Spark reaches its :8000 (no record covers it)
        fx.px.svc.cfg.create_backend({"id": "engine", "kind": "tandemllm",
                                      "url": f"http://127.0.0.1:{eport}/v1"})
        fx.f.deps.all()
        fx.sync()
        ids = []
        for i in range(10):
            st, h, raw = fx.chat("TandemLLM", text=f"turn {i}", stream=bool(i % 2))
            assert st == 200 and h.get("X-Tandem-Node") == "Box", h
            assert h.get("X-Tandem-Deployment") == "TandemLLM", h
            if not i % 2:
                ids.append(json.loads(raw)["id"])
        rows = fx.px.rows()
        assert len(rows) == 10, rows
        assert {(r["node"], r["deployment"]) for r in rows} == {(box, "TandemLLM")}, rows
        # each row names the engine's own row of the request: the id of its answer
        assert all(r["upstream_request"] for r in rows), rows
        assert [rows[i]["upstream_request"] for i in range(0, 10, 2)] == ids, (ids, rows)
        # Usage of the controller's ledger (the page's default when the data plane runs here)
        st, out = tf.get(fx.px, "/v1/fleet/ledger/usage?bucket=day")
        assert st == 200 and out["totals"]["requests"] == 10, out["totals"]
        st, out = tf.get(fx.px, "/v1/fleet/ledger/requests?limit=50")
        assert st == 200 and len(out["requests"]) == 10 and \
            all(r["upstream_request"] for r in out["requests"]), out["requests"][:1]
        td.dash_ok(out, "requests")
    finally:
        fx.close()


# ---------------------------------------------------------------------------- 6. nginx

FIVE = ("/v1/chat/completions", "/v1/completions", "/v1/embeddings", "/v1/responses",
        "/v1/models", "/v1/models/TandemLLM", "/v1/models/a%2Fb")
ENGINE_ONLY = ("/v1/conductor/profiles", "/v1/setup/models", "/v1/dashboard/summary",
               "/v1/dashboard/live", "/v1/cache/stats", "/v1/cache/clear", "/v1/chat/runs",
               "/v1/chat/sessions/x", "/v1/memory/notes", "/v1/library", "/v1/laya/ask",
               "/v1/systemone", "/v1/predict", "/v1/tokenize", "/v1/config/backends",
               "/v1/models-cache", "/v1/completions-old", "/v1/chat/completions/x",
               "/v1/responses/r1", "/health", "/metrics", "/events")


def _locations(doc: str) -> list[tuple[str, str]]:
    """(modifier, path) of each `location` in the data plane's nginx block of docs/proxy.md."""
    m = re.search(r"### Through a reverse proxy\n(.*?)\n### ", doc, re.S)
    assert m, "docs/proxy.md has no data plane section"
    return re.findall(r"location\s+(=|\^~)\s+(\S+)\s*\{", m.group(1))


def _goes_to_controller(locs, path: str) -> bool:
    for mod, loc in locs:
        if (mod == "=" and path == loc) or (mod == "^~" and path.startswith(loc)):
            return True
    return False


def test_locations_and_rollback():
    with open(os.path.join(ROOT, "docs", "proxy.md")) as fh:
        doc = fh.read()
    locs = _locations(doc)
    assert locs, "no locations in the nginx block"
    for p in FIVE:
        assert _goes_to_controller(locs, p), p
    for p in ENGINE_ONLY:
        assert not _goes_to_controller(locs, p), p
    m = re.search(r"### Through a reverse proxy\n(.*?)\n## ", doc, re.S)
    section = m.group(0)
    for words in ("Rollback", "nginx -t", "reload", "proxy_read_timeout", "proxy_buffering off"):
        assert words in section, words


if __name__ == "__main__":
    run(globals())
