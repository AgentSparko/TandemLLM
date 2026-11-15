"""The HTTP token, the allow-from list and the hot settings route (engine API 1.9; ENG-456).

server/api/access.py is the gate every request passes before its route; server/engine_settings.py
is GET and PATCH /v1/engine/settings. The server runs in process on a real socket (tests/test_app_loop.py's
CPU engine, tests/test_auth.py's setup); a handler subclass sets the peer address each test wants,
so a "remote" caller is a TCP peer that is not loopback. The token file and the settings tree are a
sandbox's (tests/remote_box.py); nothing of the box is read or written.

The test matrix (the Gherkin of ENG-456):

  1  the parts, no server: an allow-from value's items (addresses, CIDRs, v4 and v6, a bad one
     named), loopback and v4-mapped peers; token rotate writes only the sha256 (mode 600) and
     answers a new token of 32+ characters each time; a bad QSE_ALLOW_FROM stops the start with one
     line; a token file that does not read locks the network out           test_the_parts
  2  no token file and no list: every caller passes as before (a remote chat completion, /v1/models,
     the status-only /health), and the start prints no access line            test_off_by_default
  3  with a token: a remote caller without it gets 401 with WWW-Authenticate on inference, the
     models and the reads, its body unread and the connection closed; with it, 200 and the same
     answer as with no gate (the tokens of the completion equal); a wrong token 401; the admin token
     passes; trusted-local (loopback, no proxy headers) needs none, loopback with X-Forwarded-For
     does; /health, /metrics/up and /readyz answer without it (the status only); with it the full
     /health follows the admin rule (a password set: the admin token's); a rotation counts within
     a second, the old token then 401                                         test_the_token
  4  the allow-from list: a peer outside it gets 403 on every route, /health and a right token
     included; a peer inside with the token and loopback pass (no password set: the token opens the
     full /health); a v4-mapped v6 peer is read as v4
                                                                              test_allow_from
  5  the hot route: GET lists QSE_ALLOW_FROM with its live and configured value; PATCH from this
     host sets it (a new overlay version, current at once, applied live, journaled via http with
     the peer), unset puts every address back; refusals: a key that is not hot (409
     restart_needed), a bad value (400), a list that shuts the caller out (409 locks_out), another
     base (409 base_moved), a job with an overlay change waiting (409 busy), a remote caller with
     no token (401), a cross-site or non-JSON write (403, 415); plan answers and writes nothing
                                                                              test_the_hot_route

CPU (the in-process server imports torch). Run: python tests/test_engine_access.py
"""

from __future__ import annotations

import contextlib
import http.client
import io
import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from test_app_loop import serve  # noqa: E402  (first: the CPU environment)
from test_usage import UTok, _ids, _script  # noqa: E402

from http.server import ThreadingHTTPServer  # noqa: E402

import remote_box as R  # noqa: E402
from server import app  # noqa: E402
from server.api import access, auth, dashboard_api  # noqa: E402
from server.remote import journal as J, lifecycle as L, overlay as O  # noqa: E402
from server.remote.paths import Tree  # noqa: E402

ADMIN = "adm-" + "a1b2c3d4" * 6
REMOTE = "192.0.2.7"
PROXY = {"X-Forwarded-For": "203.0.113.44"}
CHAT = {"messages": [{"role": "user", "content": "q"}], "max_tokens": 8}


class Peer(app.Handler):
    """The engine's handler, its TCP peer the address the test names."""

    peer = "127.0.0.1"

    def setup(self):
        super().setup()
        self.client_address = (Peer.peer, self.client_address[1])


class Server:
    def __init__(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Peer)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def call(self, method: str, path: str, *, peer: str = "127.0.0.1", headers: dict | None = None, body=None,
             raw: bytes | None = None):
        """(status, headers, body) of one request from `peer`."""
        Peer.peer = peer
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        h = dict(headers or {})
        if data is not None:
            h.setdefault("Content-Type", "application/json")
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        out = r.read()
        c.close()
        try:
            out = json.loads(out)
        except ValueError:
            out = out.decode("utf-8", "replace")
        return r.status, dict(r.getheaders()), out

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def bearer(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def _setup(box: dict, allow: str = "", admin: str | None = None) -> Server:
    serve()
    app.STATE.update(tok=UTok(), auth=auth.Auth(admin, None, login=False),
                     gpu_sampler=dashboard_api.GpuSampler(cmd="/nonexistent"), args={"x": 1},
                     started=int(time.time()), tree_root=box["root"],
                     gate=access.Gate(Tree(box["root"]).http_token, allow))
    return Server()


def _chat(s: Server, **kw):
    real = app.generate_stream
    app.generate_stream = _script(_ids("hi there"))
    try:
        return s.call("POST", "/v1/chat/completions", body=CHAT, **kw)
    finally:
        app.generate_stream = real


def test_the_parts():
    nets = access.parse_allow(" 192.0.2.0/24, 2001:db8::/32 ,10.1.2.3")
    assert [str(n) for n in nets] == ["192.0.2.0/24", "2001:db8::/32", "10.1.2.3/32"], nets
    for bad in ("192.0.2.0/33", "host.example", "1.2.3.4/x"):
        try:
            access.parse_allow(bad)
            raise AssertionError(bad)
        except ValueError as exc:
            assert repr(bad) in str(exc), exc
    assert access.allowed("192.0.2.9", nets) and access.allowed("::ffff:192.0.2.9", nets)
    assert access.allowed("2001:db8::1", nets) and not access.allowed("198.51.100.1", nets)
    assert access.allowed("127.0.0.1", nets) and access.allowed("::1", nets) and access.allowed("x", [])
    assert not access.allowed("not-an-address", nets)
    b = R.make()
    try:
        path = Tree(b["root"]).http_token
        t1, before = access.rotate(path)
        t2, again = access.rotate(path)
        assert (before, again) == (False, True) and t1 != t2 and len(t1) >= 32 and len(t2) >= 32
        text = open(path).read()
        assert text == f"sha256:{access.token_hash(t2)}\n" and t2 not in text and t1 not in text, text
        assert os.stat(path).st_mode & 0o777 == 0o600
        try:
            access.Gate.from_env(b["root"], {access.ALLOW_ENV: "10.0.0.0/8,nonsense"})
            raise AssertionError("started")
        except SystemExit as exc:
            assert str(exc).startswith("[access] QSE_ALLOW_FROM: 'nonsense'") and "\n" not in str(exc), exc
        # a token file that does not read: nobody but trusted-local passes
        open(path, "w").write("not a hash\n")
        g = access.Gate(path)
        a = auth.Auth(None, None)
        assert g.required() == "" and g.check(REMOTE, bearer(t2), "GET", "/v1/models", a)[0] == 401
        assert g.check("127.0.0.1", {}, "GET", "/v1/models", a) is None
        assert "does not read" in g.describe()
    finally:
        R.cleanup(b)
    return "lists, peers, rotation, a bad list, a bad file"


def test_off_by_default():
    b = R.make()
    s = _setup(b)
    try:
        assert not app.STATE["gate"].active()
        code, _, body = _chat(s, peer=REMOTE)
        msg = body["choices"][0]["message"]
        assert code == 200 and "hi there" in (msg.get("content") or "") + (msg.get("reasoning_content") or ""), body
        code, _, body = s.call("GET", "/v1/models", peer=REMOTE)
        assert code == 200 and body["data"], body
        code, _, body = s.call("GET", "/health", peer=REMOTE)
        assert code == 200 and "status" in body, body
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            g = access.Gate.from_env(b["root"], {})
        assert not g.active() and out.getvalue() == ""
    finally:
        s.close()
        R.cleanup(b)
    return "no file, no list: as before"


def test_the_token():
    b = R.make()
    s = _setup(b, admin=ADMIN)
    try:
        code, _, plain = _chat(s, peer=REMOTE)                       # the answer before the gate is on
        assert code == 200, plain
        token, _ = access.rotate(Tree(b["root"]).http_token)
        app.STATE["gate"]._next = 0
        for method, path in (("POST", "/v1/chat/completions"), ("GET", "/v1/models"),
                             ("GET", "/v1/dashboard/summary"), ("GET", "/v1/engine/settings")):
            code, head, body = s.call(method, path, peer=REMOTE, body=CHAT if method == "POST" else None)
            assert code == 401 and body["error"]["code"] == "unauthorized", (path, body)
            assert head.get("WWW-Authenticate") == 'Bearer realm="TandemLLM"' and head.get("Connection") == "close", head
        code, _, body = _chat(s, peer=REMOTE, headers=bearer(token))
        assert code == 200 and body["choices"] == plain["choices"] and body["usage"] == plain["usage"], (body, plain)
        code, _, _ = _chat(s, peer=REMOTE, headers=bearer(token[:-1] + ("A" if token[-1] != "A" else "B")))
        assert code == 401
        code, _, _ = s.call("GET", "/v1/models", peer=REMOTE, headers=bearer(ADMIN))
        assert code == 200, "the admin token opens more: it passes"
        # trusted-local: the box's own callers; a proxy's relays are not
        code, _, _ = _chat(s)
        assert code == 200
        code, _, body = s.call("GET", "/v1/models", headers=PROXY)
        assert code == 401, body
        # the open paths: the status only, the full body with the token
        code, _, body = s.call("GET", "/health", peer=REMOTE)
        assert code == 200 and body == {"status": "ok"}, body
        for path in ("/healthz", "/v1/health", "/metrics/up"):
            code, _, _ = s.call("GET", path, peer=REMOTE)
            assert code == 200, path
        code, _, body = s.call("GET", "/health", peer=REMOTE, headers=bearer(token))
        assert code == 200 and body == {"status": "ok"}, "with a password set, the full body is the admin's"
        code, _, body = s.call("GET", "/health", peer=REMOTE, headers=bearer(ADMIN))
        assert code == 200 and "inflight" in body, body
        # a rotation counts within a second; the old token is refused then
        new, before = access.rotate(Tree(b["root"]).http_token)
        assert before and new != token
        time.sleep(access.RECHECK_S + 0.1)
        code, _, _ = s.call("GET", "/v1/models", peer=REMOTE, headers=bearer(token))
        assert code == 401
        code, _, _ = s.call("GET", "/v1/models", peer=REMOTE, headers=bearer(new))
        assert code == 200
    finally:
        s.close()
        R.cleanup(b)
    return "401 without it, the same answer with it, trusted-local, open paths, rotation"


def test_allow_from():
    b = R.make()
    s = _setup(b, allow="192.0.2.0/24")
    try:
        token, _ = access.rotate(Tree(b["root"]).http_token)
        app.STATE["gate"]._next = 0
        for path in ("/health", "/v1/models"):
            code, _, body = s.call("GET", path, peer="198.51.100.9", headers=bearer(token))
            assert code == 403 and body["error"]["code"] == "forbidden_address", (path, body)
        code, _, _ = s.call("GET", "/v1/models", peer=REMOTE, headers=bearer(token))
        assert code == 200
        code, _, body = s.call("GET", "/health", peer=REMOTE, headers=bearer(token))
        assert code == 200 and "inflight" in body, "no password set: the token opens the full body"
        code, _, _ = s.call("GET", "/v1/models", peer="::ffff:192.0.2.8", headers=bearer(token))
        assert code == 200
        code, _, _ = s.call("GET", "/v1/models")
        assert code == 200, "loopback is this host"
        code, _, _ = s.call("GET", "/v1/models", peer=REMOTE)
        assert code == 401, "inside the list, the token is still needed"
    finally:
        s.close()
        R.cleanup(b)
    return "403 outside the list on every route, inside and loopback pass"


def test_the_hot_route():
    b = R.make()
    s = _setup(b, admin=ADMIN)
    tree = Tree(b["root"])
    try:
        code, _, body = s.call("GET", "/v1/engine/settings")
        assert code == 200 and body["hot"] == ["QSE_ALLOW_FROM"], body
        (row,) = body["keys"]
        assert row == {"key": "QSE_ALLOW_FROM", "live": "", "value": None, "set": False, "source": "default",
                       "type": "cidrs"}, row
        # refusals, nothing written
        cases = (({"set": {"MAX_QUEUE": "4"}}, 409, "restart_needed"),
                 ({"set": {"QSE_ALLOW_FROM": "10.0.0.0/33"}}, 400, "bad_value"),
                 ({"set": {"NOPE_KEY": "1"}}, 400, "unknown_key"),
                 ({"set": {}}, 400, "missing_argument"),
                 ({"set": {"QSE_ALLOW_FROM": "10.0.0.0/8"}, "base": 3}, 409, "base_moved"))
        for body_, status, kind in cases:
            code, _, out = s.call("PATCH", "/v1/engine/settings", body=body_)
            assert code == status and out["error"]["type"] == kind, (body_, out)
        code, _, out = s.call("PATCH", "/v1/engine/settings", peer=REMOTE, body={"set": {"QSE_ALLOW_FROM": "192.0.2.0/24"}})
        assert code == 401, out
        code, _, out = s.call("PATCH", "/v1/engine/settings", headers={"Sec-Fetch-Site": "cross-site"},
                              body={"set": {"QSE_ALLOW_FROM": "10.0.0.0/8"}})
        assert code == 403 and out["error"]["type"] == "cross_site", out
        code, _, out = s.call("PATCH", "/v1/engine/settings", headers={"Content-Type": "text/plain"},
                              raw=b'{"set": {"QSE_ALLOW_FROM": "10.0.0.0/8"}}')
        assert code == 415, out
        code, _, out = s.call("PATCH", "/v1/engine/settings", peer=REMOTE, headers=bearer(ADMIN),
                              body={"set": {"QSE_ALLOW_FROM": "10.0.0.0/8"}})
        assert code == 409 and out["error"]["type"] == "locks_out", out
        code, _, out = s.call("PATCH", "/v1/engine/settings", body={"set": {"QSE_ALLOW_FROM": "10.0.0.0/8"},
                                                                    "plan": True})
        assert code == 200 and out["applied"] is False and out["plan"]["restart_needed"] is False, out
        assert O.manifest(tree)["current"] == 0 and app.STATE["gate"].allow_text == ""
        # a job with an overlay change waits: busy
        L.create(tree, "settings", {"mode": "idle"}, target={"MAX_QUEUE": "4"}, spawn=False)
        code, _, out = s.call("PATCH", "/v1/engine/settings", body={"set": {"QSE_ALLOW_FROM": "10.0.0.0/8"}})
        assert code == 409 and out["error"]["type"] == "busy", out
        job = L.latest(tree)
        job.update(state="cancelled")
        L.write_job(tree, job)
        # applied: a version, current, live, journaled
        code, _, out = s.call("PATCH", "/v1/engine/settings", body={"set": {"QSE_ALLOW_FROM": "10.0.0.0/8,192.0.2.0/24"},
                                                                    "base": 0})
        assert code == 200 and out["applied"] and out["version"] == 1, out
        assert out["live"] == {"QSE_ALLOW_FROM": "10.0.0.0/8,192.0.2.0/24"}, out
        m = O.manifest(tree)
        assert m["current"] == 1 and m["versions"][0]["set"] == {"QSE_ALLOW_FROM": "10.0.0.0/8,192.0.2.0/24"}, m
        assert "QSE_ALLOW_FROM='10.0.0.0/8,192.0.2.0/24'" in open(tree.managed).read()
        row = J.read(tree.journal)[-1]
        assert row["verb"] == "settings patch" and row["via"] == "http" and row["from"] == "127.0.0.1" and \
            row["exit"] == 0 and row["args"] == ["QSE_ALLOW_FROM=10.0.0.0/8,192.0.2.0/24"], row
        code, _, _ = s.call("GET", "/v1/models", peer="198.51.100.9")
        assert code == 403, "the new list counts at once"
        code, _, body = s.call("GET", "/v1/engine/settings")
        assert body["keys"][0]["live"] == body["keys"][0]["value"] == "10.0.0.0/8,192.0.2.0/24" and \
            body["keys"][0]["source"] == "managed", body
        # unset: every address again
        code, _, out = s.call("PATCH", "/v1/engine/settings", body={"unset": ["QSE_ALLOW_FROM"]})
        assert code == 200 and out["version"] == 2 and out["live"] == {"QSE_ALLOW_FROM": ""}, out
        code, _, _ = s.call("GET", "/v1/models", peer="198.51.100.9")
        assert code == 200
    finally:
        s.close()
        R.cleanup(b)
    return "GET, PATCH applied live and journaled, 8 refusals, plan, unset"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
