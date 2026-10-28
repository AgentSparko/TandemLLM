"""A client of a TandemNode agent (SRV-725): the controller's AgentNode and a node's link test.

    c = Client("http://127.0.0.1:7700", secret=lambda: "...")          loopback, the secret
    c = Client("https://10.0.0.2:7700", ssl_context=ctx)                 mTLS (proxy/nodetrust.py)
    c.json("GET", "/node/identity")                -> (status, parsed body or None)
    for ev, data in c.events("/node/metrics"): ...  an event stream, (event name, parsed data)
    conn = c.connection(host="10.77.0.2", source="10.77.0.1")   a raw connection (the link test)
    pipe(r, conn, write, gone)                     a streamed answer passed on as it arrives

The address is the one given, never resolved anew; a TLS peer is checked against the pinned
certificates in its context, not by name.
"""

from __future__ import annotations

import http.client
import json
import socket
import threading
import urllib.parse

TIMEOUT_S = 5.0


class Client:
    def __init__(self, url: str, *, secret=None, ssl_context=None, timeout: float = TIMEOUT_S):
        u = urllib.parse.urlsplit(url)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ValueError(f"{url}: an agent URL is http(s)://address:port")
        self.url, self.scheme, self.host = url.rstrip("/"), u.scheme, u.hostname
        self.port = u.port or 7700
        self.secret = secret if callable(secret) or secret is None else (lambda s=secret: s)
        self.ctx = ssl_context
        self.timeout = timeout

    def headers(self) -> dict:
        h = {"Accept": "application/json"}
        if self.secret is not None:
            h["Authorization"] = f"Bearer {self.secret()}"
        return h

    def connection(self, host: str | None = None, source: str | None = None,
                   timeout: float | None = None) -> http.client.HTTPConnection:
        kw = {"timeout": timeout or self.timeout}
        if source:
            kw["source_address"] = (source, 0)
        if self.scheme == "https":
            return http.client.HTTPSConnection(host or self.host, self.port, context=self.ctx,
                                               **kw)
        return http.client.HTTPConnection(host or self.host, self.port, **kw)

    def raw(self, method: str, path: str, body: bytes | None = None,
            timeout: float | None = None) -> tuple[int, bytes]:
        conn = self.connection(timeout=timeout)
        try:
            h = self.headers()
            if body is not None:
                h["Content-Type"] = "application/json"
            conn.request(method, path, body=body, headers=h)
            r = conn.getresponse()
            return r.status, r.read()
        finally:
            conn.close()

    def json(self, method: str, path: str, body=None, timeout: float | None = None):
        st, data = self.raw(method, path, None if body is None else json.dumps(body).encode(),
                            timeout)
        try:
            return st, json.loads(data) if data else None
        except ValueError:
            return st, None

    def events(self, path: str, timeout: float | None = None, on_open=None):
        """(event, data) of a server-sent event stream until it ends; data parsed as JSON when it
        is. `on_open(conn)` gets the connection, so another thread can close it."""
        conn = self.connection(timeout=timeout)
        try:
            h = dict(self.headers(), Accept="text/event-stream")
            conn.request("GET", path, headers=h)
            r = conn.getresponse()
            if r.status != 200:
                raise OSError(f"{path}: HTTP {r.status}")
            if on_open is not None:
                on_open(conn)
            ev, data = "message", []
            while True:
                line = r.readline()
                if not line:
                    return
                line = line.decode("utf-8", "replace").rstrip("\r\n")
                if not line:
                    if data:
                        text = "\n".join(data)
                        try:
                            yield ev, json.loads(text)
                        except ValueError:
                            yield ev, text
                    ev, data = "message", []
                elif line.startswith(":"):
                    continue
                elif line.startswith("event:"):
                    ev = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].lstrip())
        finally:
            conn.close()


def pipe(r, conn, write, gone, every: float = 1.0) -> None:
    """An upstream answer's bytes to `write` as they arrive, until it ends, a write fails or
    `gone()` (asked every `every` s) says the reader left; then the upstream is closed. A read
    has no timeout here: a quiet stream (a job between two steps, a model thinking) stays open.
    (A socket timeout would end it: after one, a file read from that socket only fails.)"""
    done = threading.Event()
    # the response's own socket: http.client lets go of it on the connection once the answer
    # says it closes, so a caller keeps it as r.sock before getresponse
    sock = getattr(r, "sock", None) or conn.sock

    def watch():
        while not done.wait(every):
            if gone():
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except (OSError, AttributeError):
                    pass
                return
    if sock is not None:
        sock.settimeout(None)
    threading.Thread(target=watch, daemon=True).start()
    try:
        while True:
            piece = r.read1(65536)
            if not piece:
                return
            write(piece)
    finally:
        done.set()
        conn.close()
