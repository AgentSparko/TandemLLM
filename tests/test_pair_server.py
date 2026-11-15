"""Pairing in the engine process (SEC-10, ENG-448; engine API 1.9): the routes /v1/engine/pair* through
server/app.py's own handlers and its gate, serving and in setup mode.

The server runs in process on a real socket (tests/test_app_loop.py's CPU engine, as
tests/test_engine_access.py runs it); a handler subclass sets the peer address each case wants, so a
"network" caller is a TCP peer that is not loopback. The tree, the host keys and authorized_keys are
a sandbox's. Nothing of the box is read or written, and nothing listens beyond loopback.

The test matrix:

  1  a new install's lock (run/http-token's `*` line): a network caller gets 401 on inference and
     on the pairing routes while no window is open; `tandem pair`'s open from this host with the
     window key opens one; then the network caller pairs without the token (the gate lets the
     pairing routes through), its token opens /v1/models, the lock line is gone; once paired the
     pairing routes are 401 again for a caller without a token      test_the_gate_and_the_lock
  2  the allow-from list still applies while a window is open: an address outside it gets 403
     forbidden_address on the pairing routes                   test_allow_from_still_applies
  3  setup mode: GET /v1/engine/pair and the whole exchange through SetupHandler; any other route
     stays 503 setup_mode                                      test_setup_mode_pairs
  4  a process with no pairing answers 404 no_pairing; pair_http.start makes the Service (its window
     key 600) and prints nothing for an engine on loopback; QSE_ADVERTISE=off on a LAN address says
     so in one line                                            test_the_start

CPU (the in-process server imports torch). Run: python tests/test_pair_server.py
"""

from __future__ import annotations

import contextlib
import http.client
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from test_app_loop import serve  # noqa: E402  (first: the CPU environment)
from test_usage import UTok  # noqa: E402

from http.server import ThreadingHTTPServer  # noqa: E402

import remote_box as R  # noqa: E402
from pair_client import Client, PairFailed, ed25519_line, host_etc  # noqa: E402
from server import app  # noqa: E402
from server.api import access, auth, dashboard_api  # noqa: E402
from server.pairing import http as pair_http  # noqa: E402
from server.pairing.window import Service  # noqa: E402
from server.remote.paths import Tree  # noqa: E402

NET = "192.0.2.7"
APP = ed25519_line("server test key")


class Peer(app.Handler):
    peer = "127.0.0.1"

    def setup(self):
        super().setup()
        self.client_address = (type(self).peer, self.client_address[1])


class SetupPeer(app.SetupHandler):
    peer = "127.0.0.1"

    def setup(self):
        super().setup()
        self.client_address = (type(self).peer, self.client_address[1])


class Box:
    def __init__(self, handler=Peer, allow: str = "", lock: bool = True, setup: bool = False):
        pair_http.LIMIT = pair_http.Limit()
        serve()
        self.b = R.make(install=True)
        self.tmp = tempfile.mkdtemp(prefix="pair-srv-")
        self.tree = Tree(self.b["root"])
        if lock:
            access.lock(self.tree.http_token)
        self.etc = host_etc(self.tmp)
        self.auth_keys = os.path.join(self.tmp, "home", ".ssh", "authorized_keys")
        os.makedirs(os.path.dirname(os.path.dirname(self.auth_keys)))
        self.svc = Service(self.tree, port=8123, host="0.0.0.0", mode="setup" if setup else "engine", etc=self.etc,
                           auth_keys=self.auth_keys, env={})
        app.STATE.update(tok=UTok(), auth=auth.Auth(None, None, login=False),
                         gpu_sampler=dashboard_api.GpuSampler(cmd="/nonexistent"), args={"x": 1},
                         started=int(time.time()), tree_root=self.b["root"],
                         gate=access.Gate(self.tree.http_token, allow), pairing=self.svc)
        if setup:
            app.STATE.update(setup_mode=True, version="test", model="setup")
        self.handler = handler
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def call(self, method: str, path: str, peer: str = NET, headers: dict | None = None, body=None):
        self.handler.peer = peer
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        data = json.dumps(body).encode() if body is not None else None
        h = dict(headers or {})
        if data is not None:
            h["Content-Type"] = "application/json"
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        raw = r.read()
        c.close()
        try:
            return r.status, json.loads(raw or b"{}")
        except ValueError:
            return r.status, raw.decode("utf-8", "replace")

    def open(self) -> dict:
        st, doc = self.call("POST", "/v1/engine/pair/open", peer="127.0.0.1",
                            headers={pair_http.KEY_HEADER: self.svc.local_key}, body={})
        assert st == 200, doc
        return doc

    def client(self, code: str) -> Client:
        self.handler.peer = NET
        return Client(self.port, code, APP, "tandemapp-1")

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        app.STATE.pop("pairing", None)
        R.cleanup(self.b)
        shutil.rmtree(self.tmp, ignore_errors=True)


def test_the_gate_and_the_lock():
    box = Box()
    try:
        st, doc = box.call("GET", "/v1/models")
        assert st == 401, (st, doc)
        for method, path, body in (("GET", "/v1/engine/pair", None), ("POST", "/v1/engine/pair/1", {})):
            st, doc = box.call(method, path, body=body)
            assert st == 401 and doc["error"]["type"] == "unauthorized", (path, st, doc)
        st, doc = box.call("POST", "/v1/engine/pair/open", headers={pair_http.KEY_HEADER: box.svc.local_key}, body={})
        assert st == 401, (st, doc)                              # from the network: the gate first
        st, doc = box.call("POST", "/v1/engine/pair/open", peer="127.0.0.1", body={})
        assert (st, doc["error"]["type"]) == (403, "window_key"), doc
        code = box.open()["code"]
        st, doc = box.call("GET", "/v1/engine/pair")
        assert st == 200 and doc["open"] is True, doc
        st, doc = box.call("GET", "/v1/models")
        assert st == 401, "an open window opened more than the pairing routes"
        got = box.client(code).pair()
        token = got["plain"]["http_token"]
        time.sleep(1.1)                                          # the gate reads the file again within a second
        st, doc = box.call("GET", "/v1/models", headers={"Authorization": f"Bearer {token}"})
        assert st == 200, (st, doc)
        lines = access.read_lines(box.tree.http_token)
        assert [o for _h, o in lines] == ["tandemapp-1"], lines
        assert open(box.auth_keys).read() == got["plain"]["authorized_keys"]["line"] + "\n"
        for method, path, body in (("GET", "/v1/engine/pair", None), ("POST", "/v1/engine/pair/1", {})):
            st, doc = box.call(method, path, body=body)
            assert st == 401, (path, st, doc)
        st, doc = box.call("GET", "/v1/engine/pair", headers={"Authorization": f"Bearer {token}"})
        assert st == 200 and doc["state"] == "paired", doc
        return "401 until a window opens, the exchange through the gate, the token opens the engine, 401 after"
    finally:
        box.close()


def test_allow_from_still_applies():
    box = Box(allow="198.51.100.0/24")
    try:
        code = box.open()["code"]
        st, doc = box.call("GET", "/v1/engine/pair")
        assert (st, doc["error"]["type"]) == (403, "forbidden_address"), doc
        try:
            box.client(code).pair()
            raise AssertionError("paired from outside the allow-from list")
        except PairFailed as exc:
            assert exc.status == 403, exc
        st, doc = box.call("GET", "/v1/engine/pair", peer="198.51.100.9")
        assert st == 200 and doc["open"], doc
        assert not os.path.exists(box.auth_keys)
        return "403 forbidden_address outside the list, 200 inside"
    finally:
        box.close()


def test_setup_mode_pairs():
    box = Box(handler=SetupPeer, setup=True)
    try:
        code = box.open()["code"]
        st, doc = box.call("GET", "/v1/engine/pair")
        assert st == 200 and doc["open"], doc
        plain = box.client(code).pair()["plain"]
        assert plain["mode"] == "setup" and plain["key_id"] == "tandemapp-1"
        time.sleep(1.1)                                          # the gate reads the file again within a second
        st, doc = box.call("GET", "/v1/engine/disk", headers={"Authorization": f"Bearer {plain['http_token']}"})
        assert st == 503 and doc["error"]["type"] == "setup_mode", (st, doc)
        return "the exchange in setup mode; other routes 503 setup_mode"
    finally:
        box.close()


def test_the_start():
    box = Box(lock=False)
    try:
        app.STATE["pairing"] = None
        st, doc = box.call("GET", "/v1/engine/pair", peer="127.0.0.1")
        assert (st, doc["error"]["type"]) == (404, "no_pairing"), doc
        state: dict = {}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            pair_http.start(state, 8123, "127.0.0.1", "engine", root=box.b["root"], env={})
        assert out.getvalue() == "", out.getvalue()
        assert isinstance(state["pairing"], Service) and state["advertiser"] is None
        key = state["pairing"].local_key_file
        assert stat.S_IMODE(os.stat(key).st_mode) == 0o600
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            pair_http.start(state, 8123, "192.0.2.10", "engine", root=box.b["root"], env={"QSE_ADVERTISE": "off"})
        assert out.getvalue() == "[pair] advertisement: off (QSE_ADVERTISE=off)\n", out.getvalue()
        pair_http.stop(state)
        return "404 no_pairing; loopback start silent, window key 600; off in one line"
    finally:
        box.close()


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
