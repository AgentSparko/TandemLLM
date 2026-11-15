"""TandemApp's half of the pairing exchange (engine-pair-v1), written from the contract, and a small
HTTP server that serves server/pairing/http.py's routes over a Service: what the pairing tests run
against. Nothing of the box is read or written: the trees, host keys and authorized_keys are a
test's own.

    srv = PairServer(svc)                 the routes on 127.0.0.1:<free port>, peers as a test names them
    c = Client(srv.port, code, key_line, key_id)
    plain = c.pair()                      the sealed answer, opened; PairFailed otherwise
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from server.api import auth  # noqa: E402
from pair_hostkeys import ed25519_line, host_etc  # noqa: E402,F401  (the tests import them from here too)
from server.pairing import hostkey, http as pair_http, spake2 as S  # noqa: E402


class PairFailed(Exception):
    def __init__(self, kind: str, status: int = 0, body=None):
        super().__init__(f"{kind} ({status})")
        self.kind, self.status, self.body = kind, status, body


def blob_of(line: str) -> bytes:
    return base64.b64decode(line.split()[1])


class Client:
    """TandemApp's side. `tamper` may change step 1's answer before it is checked (a man in the
    middle), `skip_check` sends cA without checking cB (a guesser that does not care)."""

    def __init__(self, port: int, code: str, key_line: str, key_id: str, *, peer_headers: dict | None = None,
                 expect_fp: str | None = None, x: int | None = None):
        self.port, self.code, self.key_line, self.key_id = port, code, key_line, key_id
        self.headers = dict(peer_headers or {})
        self.expect_fp = expect_fp
        self.x = x

    def post(self, rest: str, body: dict) -> tuple[int, dict, dict]:
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        c.request("POST", f"{pair_http.PREFIX}/{rest}", body=json.dumps(body).encode(),
                  headers={"Content-Type": "application/json", **self.headers})
        r = c.getresponse()
        raw = r.read()
        c.close()
        try:
            doc = json.loads(raw or b"{}")
        except ValueError:
            doc = {"raw": raw.decode("utf-8", "replace")}
        return r.status, doc, dict(r.getheaders())

    def pair(self, tamper=None, skip_check: bool = False) -> dict:
        w = S.w_of(self.code)
        x = self.x or 1 + secrets.randbelow(S.Q - 1)
        pa = S.message_a(x, w)
        st, a1, _h = self.post("1", {"pA": format(pa, "x"), "public_key": self.key_line, "key_id": self.key_id,
                                     "name": "test"})
        if st != 200:
            raise PairFailed(a1.get("error", {}).get("type", "http"), st, a1)
        if tamper:
            a1 = tamper(dict(a1))
        host_blob = blob_of(a1["host_key"])
        fp = hostkey.fingerprint(host_blob)
        if fp != a1["fingerprint"] or (self.expect_fp and fp != self.expect_fp):
            self.post("abandon", {"session": a1["session"]})
            raise PairFailed("fingerprint", 0, a1)
        pb = S.elem(a1["pB"])
        k = S.shared_a(pb, x, w)
        kca, kcb, tt = S.keys(S.identity_a(hostkey.fingerprint(blob_of(self.key_line)), self.key_id), S.identity_b(fp),
                              pa, pb, k, w)
        if not skip_check and not S.hmac.compare_digest(a1["cB"], S.mac(kcb, tt)):
            self.post("abandon", {"session": a1["session"]})
            raise PairFailed("confirmation", 0, a1)
        st, a2, _h = self.post("2", {"session": a1["session"], "cA": S.mac(kca, tt)})
        if st != 200:
            raise PairFailed(a2.get("error", {}).get("type", "http"), st, a2)
        plain = json.loads(S.open_seal(S.ke_of(tt), a2["sealed"]))
        if plain["host_key"] != a1["host_key"]:
            raise PairFailed("host_key_changed", 0, plain)
        return {"step1": a1, "step2": a2, "plain": plain}


class _Handler(BaseHTTPRequestHandler):
    """server/app.py's Handler as far as the pairing routes use it: _json, _peer, _auth_policy."""

    peer = "127.0.0.1"
    state: dict = {}

    def log_message(self, *a):
        pass

    def _peer(self) -> str:
        return type(self).peer

    def _auth_policy(self):
        return auth.Auth(None, None, login=False)

    def _json(self, code: int, payload: dict, extra_headers: tuple = ()) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in extra_headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _go(self, method: str) -> None:
        path = self.path.split("?")[0]
        body = None
        if method == "POST":
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._json(400, {"error": {"type": "invalid_request_error", "message": "bad json"}})
        if pair_http.split(path) is None:
            return self._json(404, {"error": {"type": "not_found", "message": path}})
        return pair_http.serve(self, type(self).state, method, path, body)

    def do_GET(self):
        self._go("GET")

    def do_POST(self):
        self._go("POST")


class PairServer:
    """The pairing routes over `svc` on a free loopback port; `peer` is the address the routes see."""

    def __init__(self, svc, peer: str = "127.0.0.1"):
        self.handler = type("H", (_Handler,), {"peer": peer, "state": {"pairing": svc}})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), self.handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def set_peer(self, peer: str) -> None:
        self.handler.peer = peer

    def get(self, rest: str = "", headers: dict | None = None) -> tuple[int, dict]:
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        c.request("GET", pair_http.PREFIX + (f"/{rest}" if rest else ""), headers=headers or {})
        r = c.getresponse()
        doc = json.loads(r.read() or b"{}")
        c.close()
        return r.status, doc

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


class FixedRng:
    """secrets' calls the window makes, answered from a script (the fixtures' y, session, nonce)."""

    def __init__(self, below=(), hexes=(), raw=()):
        self.below, self.hexes, self.raw = list(below), list(hexes), list(raw)

    def randbelow(self, n):
        return self.below.pop(0) if self.below else secrets.randbelow(n)

    def token_hex(self, n):
        return self.hexes.pop(0) if self.hexes else secrets.token_hex(n)

    def token_bytes(self, n):
        return self.raw.pop(0) if self.raw else secrets.token_bytes(n)
