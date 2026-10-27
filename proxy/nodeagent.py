"""`tandem-node` (`tandem node`): TandemNode, the agent on every Spark that TandemApp's controller
talks to (SRV-725). Port 7700.

    tandem-node [serve] [--host 127.0.0.1] [--port 7700] [--state-dir ~/.tandemllm/node]
                [--tandem-dir DIR]                    the agent (systemd user unit: `tandem-node unit`)
    tandem-node check [...]                           how it would listen, and why; starts nothing
    tandem-node unit [...]                            a systemd user unit for these options, on stdout

ROUTES (every one behind the caller check below; JSON unless an event stream):

    GET    /node/identity              node id, hostname, DMI product and serial, OS, kernel, driver,
                                       CUDA, TandemLLM version, code sha, how the agent listens
    GET    /node/inventory             the fleet's inventory line, and disks, NICs (ConnectX ports with
                                       their firmware), memory, CPU cores, GPUs
    GET    /node/metrics               events `sample` once a second (proxy/nodemetrics.py); ?once=1
                                       the newest as JSON
    GET    /node/engines               the engines (proxy/nodeengines.py): state, health, job
    GET    /node/engines/{id}
    POST   /node/engines/{id}/start | stop | restart     202 and the job; 409 not managed or busy
    GET    /node/engines/{id}/<route>  the engine's /health, /v1/models, /v1/dashboard/live (its live
                                       stream), /v1/dashboard/system, /v1/dashboard/logs, /metrics
    GET    /node/weights               the engine's Setup Guide weights, each with a content id
    DELETE /node/weights/{profile}?confirm=1 | ?dry_run=1   the Setup Guide's removal of one profile
    POST   /node/weights/receive       501: a copy from another node comes with pairing (SEC-4)
    GET    /node/logs                  ?level=&q=&source=all|agent|<engine>&backlog=; events `log`;
                                       ?follow=0 the lines as JSON
    GET    /node/linktest/info         this node's interfaces (proxy/linktest.py)
    GET    /node/linktest/echo         a round trip
    POST   /node/linktest/sink         a body read and dropped: the bandwidth test's far end
    POST   /node/linktest              {peer, bytes?, streams?, rtt_n?}: this node to the peer's agent
    *      /node/setup/<route>         the engine's /v1/setup/<route> (the Setup Guide) as it answers;
                                       its status responder (OPS-175) while it restarts

CALLERS. An address is listened on as proxy/nodetrust.py's bind rule says: loopback in plain HTTP
behind the loopback secret (`<state dir>/secret`, 0600, made at the first start) until the trust
store (`<state dir>/trust`) is filled, then mTLS; any other address only in mTLS; never 0.0.0.0 or a
WAN address. A call without the secret, or a TLS client without a certificate the store vouches
for, gets no answer from any route. Every change (an engine action, a removal, a setup POST, a link
test) is a log line with the caller (`secret` or the certificate's fingerprint).

ENGINES. The engine's own routes are called on loopback without proxy headers, where it trusts its
caller; the caller's secret is never passed on. The Setup Guide routes run in the engine, so the
node's Setup Guide is the engine's, unchanged; the first TandemLLM engine in the list serves them.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import http.client
import queue
import select
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if __package__ in (None, ""):                                    # pragma: no cover
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proxy import hostcollect, linktest, nodeclient, nodeengines, nodemetrics  # noqa: E402
from proxy import nodesource, nodetrust  # noqa: E402

DEFAULT_PORT = 7700
DEFAULT_DIR = "~/.tandemllm/node"
MAX_JSON = 1 << 20
PING_S = 5.0
LEVELS = ("debug", "info", "warn", "error")
TO_BUF = {"warn": "warning"}
FROM_BUF = {"warning": "warn"}
INVENTORY_EVERY_S = 600.0
# the engines' memory split in the stream: the engine takes 0.1 to 0.6 s to answer it
SYSTEM_EVERY_S = 60.0
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _code_sha() -> str | None:
    if not os.path.isdir(os.path.join(ROOT, ".git")):
        return None
    try:
        r = subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"], capture_output=True,
                           text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return (r.stdout.strip() or None) if r.returncode == 0 else None


def content_id(path: str) -> tuple[str | None, str | None]:
    """(sha256:<hex>, basis) of a weights folder from the file hashes it already records, never
    from reading the weights: `_hub_manifest.json` (ops/fetch_weights.py), else the Hugging Face
    download metadata (`.cache/huggingface/download/*.metadata`, a `--local-dir` download), else a
    Hugging Face cache snapshot's links into its blobs (named by their hash). (None, None) when
    none of these is there."""
    import hashlib
    rows = None
    basis = None
    try:
        with open(os.path.join(path, "_hub_manifest.json")) as f:
            files = json.load(f).get("files") or {}
        rows = [f"{n}\t{v.get('size')}\t{v.get('sha256') or ''}" for n, v in files.items()
                if isinstance(v, dict)]
        basis = "hub_manifest"
    except (OSError, ValueError, AttributeError):
        pass
    if rows is None:
        meta = os.path.join(path, ".cache", "huggingface", "download")
        got = []
        for d, _dirs, fs in os.walk(meta):
            for f in fs:
                if not f.endswith(".metadata"):
                    continue
                try:
                    with open(os.path.join(d, f)) as fh:
                        lines = fh.read().splitlines()
                except OSError:
                    continue
                if len(lines) >= 2:
                    got.append(f"{os.path.relpath(os.path.join(d, f[:-9]), meta)}\t{lines[1]}")
        if got:
            rows, basis = got, "hf_metadata"
    if rows is None and "/snapshots/" in os.path.realpath(path) + "/":
        got = []
        for d, _dirs, fs in os.walk(path):
            for f in fs:
                p = os.path.join(d, f)
                if os.path.islink(p):
                    got.append(f"{os.path.relpath(p, path)}\t{os.path.basename(os.readlink(p))}")
        if got:
            rows, basis = got, "hf_blobs"
    if not rows:
        return None, None
    return "sha256:" + hashlib.sha256("\n".join(sorted(rows)).encode()).hexdigest(), basis


class Agent:
    """Everything the routes read: this machine (LocalNode over its own host collector), the 1 s
    sampler, the engines, the trust store and the secret."""

    def __init__(self, state_dir: str, *, tandem_dir: str | None = None, root: str = "/",
                 collector=None, local=None, engines=None, clock=time.time, sample_wait=None,
                 addrs=None, ping=None, logbuf=None, log=None):
        self.state_dir = os.path.expanduser(state_dir)
        os.makedirs(self.state_dir, mode=0o700, exist_ok=True)
        self.log = log or (lambda m: print(m, flush=True))
        self.clock = clock
        self.root = root
        self.store = nodetrust.Store(os.path.join(self.state_dir, "trust"))
        self.secret_path = os.path.join(self.state_dir, "secret")
        self.secret = nodetrust.load_secret(self.secret_path, create=True)
        self.engines = engines or nodeengines.Engines.load(self.state_dir, tandem_dir,
                                                           log=self.log, clock=clock)
        self.collector = collector or hostcollect.HostCollector(fast_period=0.5,
                                                                paths=[self.state_dir])
        from proxy import _version
        self.local = local or nodesource.LocalNode(
            hosts=lambda: self.collector.sample([]), version=_version.release, root=root,
            clock=clock, system_every=SYSTEM_EVERY_S)
        self.local.sync([{"id": e.id, "kind": e.kind, "url": e.url("/v1")}
                         for e in self.engines.list])
        self.reader = nodemetrics.Reader(self.local, self.collector, root=root, clock=clock)
        self._read_lock = threading.Lock()
        self.sampler = nodemetrics.Sampler(self._sample, clock=clock, wait=sample_wait,
                                           log=self.log)
        self.addrs = addrs or linktest.ip_addrs
        self.ping = ping
        self.logbuf = logbuf
        self.started = clock()
        self.listening: dict = {}                # address -> mode
        self.code_sha = _code_sha()
        self._inv: dict | None = None
        self._inv_at = -INVENTORY_EVERY_S

    def _sample(self, t: float) -> dict:
        with self._read_lock:
            return self.reader.sample(t)

    # ------------------------------------------------------------------ who and what
    def identity(self) -> dict:
        ident = self.local.identity()
        inv = self.local.inventory()
        from proxy import _version
        return nodemetrics.prune({
            "id": ident["id"], "hostname": ident["alias"], "serial": ident["serial"],
            "hw": ident["hw"], "dmi": ident["dmi"], "address": ident["address"],
            "os": inv.get("os"), "kernel": inv.get("kernel"),
            "driver": inv.get("driver"), "cuda": inv.get("cuda"),
            "tandemllm": _version.release() or None, "code_sha": self.code_sha,
            "agent": {"listen": [{"address": a, "port": m["port"], "mode": m["mode"]}
                                 for a, m in self.listening.items()],
                      "started_at": self.started, "pid": os.getpid()}})

    def inventory(self) -> dict:
        now = self.clock()
        if self._inv is not None and now - self._inv_at < INVENTORY_EVERY_S:
            return self._inv
        r = self.reader
        disks = []
        for d in r._ls("sys", "block"):
            if d.startswith(("loop", "ram", "zram", "dm-", "sr")):
                continue
            sectors = r._int("sys", "block", d, "size")
            disks.append({"name": d, "nvme": d.startswith("nvme"),
                          "model": r._read("sys", "block", d, "device", "model"),
                          "size_gb": None if sectors is None else round(sectors * 512 / 1e9, 1),
                          "rotational": r._int("sys", "block", d, "queue", "rotational") == 1})
        nics = []
        for i in linktest.interfaces(r, self.addrs()):
            drv = os.path.realpath(r._p("sys", "class", "net", i["iface"], "device", "driver"))
            row = dict(i, driver=os.path.basename(drv) if os.path.isdir(drv) else None,
                       mac=r._read("sys", "class", "net", i["iface"], "address"))
            rp = r.rdma_port(i["iface"])
            if rp is not None:
                row["firmware"] = r._read("sys", "class", "infiniband", rp[0], "fw_ver")
                row["board_id"] = r._read("sys", "class", "infiniband", rp[0], "board_id")
            nics.append(row)
        caps = r._capacities()
        h = self.local._last_host or self.local.host() or {}
        mem = h.get("memory") or {}
        g = lambda b: None if b is None else round(b / nodesource.GIB, 2)   # noqa: E731
        inv = nodemetrics.prune({
            "fleet": self.local.inventory(), "lan": self.local.lan(), "disks": disks,
            "nics": nics,
            "memory": {"total_gib": g(mem.get("total_bytes")),
                       "swap_total_gib": g(mem.get("swap_total_bytes")),
                       "unified": mem.get("unified")},
            "cpu": {"count": os.cpu_count(),
                    "performance": sum(1 for v in caps.values() if v == "performance") or None,
                    "efficiency": sum(1 for v in caps.values() if v == "efficiency") or None},
            "gpus": [{"index": x.get("index"), "name": x.get("name"), "uuid": x.get("uuid"),
                      "memory_unified": x.get("memory_unified")} for x in h.get("gpus") or []]})
        self._inv, self._inv_at = inv, now
        return inv

    def setup_engine(self) -> nodeengines.Engine | None:
        return next((e for e in self.engines.list if e.kind == "tandemllm"), None)

    # ------------------------------------------------------------------ logs
    def log_lines(self, level: str, q: str | None, source: str, limit: int) -> list[dict]:
        out = []
        if source in ("all", "agent") and self.logbuf is not None:
            for e in self.logbuf.lines(level=TO_BUF.get(level, level), grep=q, limit=limit):
                out.append(_line(e, "agent"))
        for p in self.local.engines():
            if source not in ("all", p.id):
                continue
            for e in p.logs(level, q, limit):
                out.append(dict(e, source=p.id))
        out = [x for x in out if x.get("t") is not None]
        out.sort(key=lambda x: x["t"])
        return out[-limit:]


def _line(e: dict, source: str) -> dict:
    return {"t": nodesource._epoch(e.get("ts")), "level": FROM_BUF.get(e.get("level"),
                                                                      e.get("level")),
            "source": source, "msg": str(e.get("msg") or "")}


# ====================================================================== the HTTP side

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "TandemNode"
    server: "Server"

    def log_message(self, fmt, *args):
        pass

    # ------------------------------------------------------------------ plumbing
    @property
    def agent(self) -> Agent:
        return self.server.agent

    def _send(self, code: int, body: bytes, ctype: str = "application/json",
              extra: tuple = ()) -> None:
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in extra:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
            self.close_connection = True

    def _json(self, code: int, doc, extra: tuple = ()) -> None:
        self._send(code, json.dumps(doc).encode(), extra=extra)

    def _err(self, code: int, kind: str, message: str) -> None:
        self._json(code, {"error": {"type": kind, "message": message}})

    def _who(self) -> str:
        if self.server.mode == "mtls":
            try:
                der = self.connection.getpeercert(binary_form=True)
            except (AttributeError, ValueError):
                der = None
            return "cert:" + nodetrust.fingerprint(der)[:16] if der else "cert:?"
        return "secret"

    def _allowed(self) -> bool:
        if self.server.mode == "mtls":
            return True                      # the handshake checked the certificate
        got = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
        if got and hmac.compare_digest(got.encode(), self.agent.secret.encode()):
            return True
        self.agent.log(f"[node] refused {self.command} {self.path.split('?')[0]} from "
                       f"{self.client_address[0]}: {'a wrong' if got else 'no'} secret")
        self._err(401, "unauthorized", "this agent answers its loopback secret or a paired "
                                       "certificate only")
        self.close_connection = True
        return False

    def _body(self, cap: int = MAX_JSON):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > cap:
            raise ValueError(f"a body of at most {cap} bytes")
        raw = self.rfile.read(n) if n else b""
        return raw

    def _audit(self, what: str, status: int) -> None:
        self.agent.log(f"[node] {self._who()} {what} -> {status}")

    def _gone(self) -> bool:
        if isinstance(self.connection, ssl.SSLSocket):
            return False                     # a closed TLS reader shows at the next write
        try:
            ready, _, _ = select.select([self.connection], [], [], 0)
            return bool(ready) and not self.connection.recv(1, socket.MSG_PEEK)
        except (OSError, ValueError):
            return True

    def _stream_head(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    # ------------------------------------------------------------------ verbs
    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_DELETE(self):
        self._route("DELETE")

    def _route(self, method: str) -> None:
        if not self._allowed():
            return
        u = urllib.parse.urlsplit(self.path)
        path = u.path.rstrip("/")
        q = {k: v[-1] for k, v in urllib.parse.parse_qs(u.query).items()}
        parts = [urllib.parse.unquote(p) for p in path.split("/") if p]
        try:
            if parts[:1] != ["node"] or len(parts) < 2:
                return self._err(404, "not_found", f"no route {method} {path}")
            head = parts[1]
            if method == "GET" and parts == ["node", "identity"]:
                return self._json(200, self.agent.identity())
            if method == "GET" and parts == ["node", "inventory"]:
                return self._json(200, self.agent.inventory())
            if method == "GET" and parts == ["node", "metrics"]:
                return self._metrics(q)
            if head == "engines":
                return self._engines(method, parts[2:], u.query)
            if head == "weights":
                return self._weights(method, parts[2:], q)
            if method == "GET" and parts == ["node", "logs"]:
                return self._logs(q)
            if head == "linktest":
                return self._linktest(method, parts[2:], q)
            if head == "setup":
                return self._setup(method, "/".join(parts[2:]), u.query)
            return self._err(404, "not_found", f"no route {method} {path}")
        except ValueError as exc:
            return self._err(400, "bad_request", str(exc))
        except nodeengines.EngineError as exc:
            return self._json(exc.status, exc.body())

    # ------------------------------------------------------------------ metrics
    def _metrics(self, q: dict) -> None:
        s = self.agent.sampler
        if q.get("once") in ("1", "true"):
            return self._json(200, s.latest or self.agent._sample(self.agent.clock()))
        sub = s.subscribe()
        try:
            self._stream_head()
            last = time.monotonic()
            while True:
                items = sub.take(PING_S)
                if items:
                    for it in items:
                        self.wfile.write(b"event: sample\ndata: " + json.dumps(it).encode()
                                         + b"\n\n")
                    self.wfile.flush()
                    last = time.monotonic()
                elif time.monotonic() - last >= PING_S:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    last = time.monotonic()
                if self._gone() or sub.closed:
                    break
        except (BrokenPipeError, ConnectionResetError, ssl.SSLError, OSError):
            pass
        finally:
            s.unsubscribe(sub)

    # ------------------------------------------------------------------ engines
    def _engines(self, method: str, rest: list[str], query: str) -> None:
        eng = self.agent.engines
        if method == "GET" and not rest:
            return self._json(200, {"engines": eng.view()})
        if not rest:
            return self._err(405, "method_not_allowed", "GET /node/engines")
        e = eng.get(rest[0])
        if method == "GET" and len(rest) == 1:
            return self._json(200, eng.view_one(e))
        if method == "POST" and len(rest) == 2:
            job = eng.act(e.id, rest[1], self._who())
            self._audit(f"{rest[1]} engine {e.id}", 202)
            return self._json(202, job)
        if method == "GET":
            return self._relay_get(e, "/" + "/".join(rest[1:]), query)
        return self._err(404, "not_found", f"no route {method} /node/engines/{'/'.join(rest)}")

    def _relay_get(self, e, path: str, query: str) -> None:
        try:
            st, ctype, conn, r = self.agent.engines.relay(e.id, path, query)
        except OSError as exc:
            return self._err(503, "engine_down", f"{e.id} does not answer on :{e.port} "
                                                 f"({type(exc).__name__})")
        self._pass(st, ctype, conn, r)

    def _pass(self, st: int, ctype: str, conn, r, extra: tuple = ()) -> None:
        """An upstream answer passed on: whole with its length, or as it arrives (a stream)."""
        try:
            if "text/event-stream" not in ctype:
                return self._send(st, r.read(), ctype, extra)
            self.send_response(st)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            for k, v in extra:
                self.send_header(k, v)
            self.end_headers()
            self.close_connection = True
            r.sock.settimeout(1.0)
            while True:
                try:
                    piece = r.read1(65536)
                except socket.timeout:
                    if self._gone():
                        break
                    continue
                if not piece:
                    break
                self.wfile.write(piece)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ssl.SSLError, OSError):
            self.close_connection = True
        finally:
            conn.close()

    # ------------------------------------------------------------------ setup and weights
    def _setup_call(self, method: str, target: str, body: bytes | None):
        """The setup engine's answer to `target` (its status responder's while it restarts):
        (status, content type, connection, response), or an error already sent (None)."""
        e = self.agent.setup_engine()
        if e is None:
            self._err(503, "no_engine", "no TandemLLM engine on this node serves the Setup Guide")
            return None
        h = {"Accept": self.headers.get("Accept") or "application/json"}
        for k in ("Content-Type", "Last-Event-ID"):
            if self.headers.get(k):
                h[k] = self.headers[k]
        for port in (e.port, e.status_port):
            if port is None:
                continue
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
            try:
                conn.request(method, target, body=body, headers=h)
                sock = conn.sock
                r = conn.getresponse()
                r.sock = sock
                return r.status, r.getheader("Content-Type") or "application/json", conn, r
            except OSError:
                conn.close()
                continue
        self._err(503, "engine_down", f"the engine on :{e.port} does not answer")
        return None

    def _setup(self, method: str, sub: str, query: str) -> None:
        body = self._body() if method == "POST" else None
        got = self._setup_call(method, f"/v1/setup/{sub}" + (f"?{query}" if query else ""), body)
        if got is None:
            return
        st, ctype, conn, r = got
        if method != "GET":
            self._audit(f"{method} setup/{sub}", st)
        extra = tuple((k, r.getheader(k)) for k in ("Retry-After",) if r.getheader(k))
        self._pass(st, ctype, conn, r, extra)

    def _weights(self, method: str, rest: list[str], q: dict) -> None:
        if method == "POST" and rest == ["receive"]:
            return self._err(501, "needs_pairing", "a copy from another node is received over "
                                                   "the pinned mTLS of pairing (SEC-4); not "
                                                   "before")
        if method == "GET" and not rest:
            got = self._setup_call("GET", "/v1/setup/weights", None)
            if got is None:
                return
            st, _, conn, r = got
            try:
                doc = json.loads(r.read() or b"null")
            except ValueError:
                doc = None
            finally:
                conn.close()
            if st != 200 or not isinstance(doc, dict):
                return self._json(st if st != 200 else 502, doc if isinstance(doc, dict) else
                                  {"error": {"type": "bad_gateway",
                                             "message": "the engine's weights answer"}})
            for w in doc.get("weights") or []:
                cid, basis = content_id(w.get("dir") or "")
                w["content_id"], w["content_basis"] = cid, basis
            return self._json(200, doc)
        if method == "DELETE" and len(rest) == 1:
            dry = q.get("dry_run") in ("1", "true")
            body = {"profiles": [rest[0]], "dry_run": dry}
            if not dry:
                body["confirm"] = q.get("confirm") in ("1", "true")
            got = self._setup_call("POST", "/v1/setup/remove", json.dumps(body).encode())
            if got is None:
                return
            st, ctype, conn, r = got
            self._audit(f"remove weights {rest[0]}{' (dry run)' if dry else ''}", st)
            return self._pass(st, ctype, conn, r)
        return self._err(404, "not_found", f"no route {method} /node/weights/{'/'.join(rest)}")

    # ------------------------------------------------------------------ logs
    def _logs(self, q: dict) -> None:
        level = q.get("level") or "info"
        if level not in LEVELS:
            raise ValueError(f"level is {', '.join(LEVELS)}")
        grep = q.get("q") or None
        if grep is not None and len(grep) > 128:
            raise ValueError("q is at most 128 characters")
        source = q.get("source") or "all"
        names = {p.id for p in self.agent.local.engines()}
        if source not in ("all", "agent") and source not in names:
            raise ValueError(f"source is all, agent or an engine ({', '.join(sorted(names))})")
        try:
            limit = int(q.get("backlog") or q.get("limit") or 200)
        except ValueError:
            limit = -1
        if not 0 <= limit <= 5000:
            raise ValueError("backlog is 0 to 5000")
        lines = self.agent.log_lines(level, grep, source, limit) if limit else []
        if q.get("follow", "1") in ("0", "false", "no"):
            return self._json(200, {"lines": lines})
        out: queue.Queue = queue.Queue(maxsize=2000)
        stop = threading.Event()
        conns: list = []
        sub = None
        buf = self.agent.logbuf
        if source in ("all", "agent") and buf is not None:
            sub = buf.subscribe(TO_BUF.get(level, level), grep)
            if sub is None:
                return self._err(429, "too_many", "too many log streams at once")
        for e in self.agent.engines.list:
            if e.kind == "tandemllm" and source in ("all", e.id):
                threading.Thread(target=self._follow_engine, daemon=True,
                                 args=(e, level, grep, out, stop, conns)).start()
        try:
            self._stream_head()
            for x in lines:
                self.wfile.write(b"event: log\ndata: " + json.dumps(x).encode() + b"\n\n")
            self.wfile.flush()
            last = time.monotonic()
            while not self._gone():
                got = []
                if sub is not None:
                    items, _ = sub.take(0.25)
                    got += [_line(e, "agent") for e in items]
                try:
                    while True:
                        got.append(out.get(timeout=0 if got or sub is not None else 0.25))
                except queue.Empty:
                    pass
                for x in got:
                    self.wfile.write(b"event: log\ndata: " + json.dumps(x).encode() + b"\n\n")
                if got:
                    self.wfile.flush()
                    last = time.monotonic()
                elif time.monotonic() - last >= PING_S:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    last = time.monotonic()
        except (BrokenPipeError, ConnectionResetError, ssl.SSLError, OSError):
            pass
        finally:
            stop.set()
            for c in conns:
                try:
                    c.close()
                except OSError:
                    pass
            if sub is not None:
                buf.unsubscribe(sub)

    @staticmethod
    def _follow_engine(e, level, grep, out, stop, conns) -> None:
        qs = {"follow": "1", "backlog": "0", "level": TO_BUF.get(level, level)}
        if grep:
            qs["grep"] = grep
        try:
            st, _, conn, r = nodeengines.open_get(e.port, "/v1/dashboard/logs?"
                                                  + urllib.parse.urlencode(qs), 3600)
        except OSError:
            return
        conns.append(conn)
        try:
            data = []
            while not stop.is_set():
                line = r.readline()
                if not line:
                    return
                line = line.decode("utf-8", "replace").rstrip("\r\n")
                if line.startswith("data:"):
                    data.append(line[5:].lstrip())
                elif not line and data:
                    try:
                        ent = json.loads("\n".join(data))
                    except ValueError:
                        ent = None
                    data = []
                    if isinstance(ent, dict) and "seq" in ent:
                        try:
                            out.put_nowait(_line(ent, e.id))
                        except queue.Full:
                            pass
        except (OSError, ValueError):
            return
        finally:
            conn.close()

    # ------------------------------------------------------------------ linktest
    def _linktest(self, method: str, rest: list[str], q: dict) -> None:
        a = self.agent
        if method == "GET" and rest == ["echo"]:
            return self._json(200, {"t": a.clock()})
        if method == "GET" and rest == ["info"]:
            return self._json(200, {"id": a.local.identity()["id"],
                                    "interfaces": linktest.interfaces(a.reader, a.addrs())})
        if method == "POST" and rest == ["sink"]:
            n = int(self.headers.get("Content-Length") or 0)
            if not 0 <= n <= linktest.MAX_BYTES:
                raise ValueError(f"a sink body is at most {linktest.MAX_BYTES} bytes")
            t0, left = time.perf_counter(), n
            while left:
                got = self.rfile.read(min(left, linktest.CHUNK))
                if not got:
                    break
                left -= len(got)
            return self._json(200, {"bytes": n - left, "s": round(time.perf_counter() - t0, 4)})
        if method == "POST" and not rest:
            doc = json.loads(self._body() or b"{}")
            if not isinstance(doc, dict) or not isinstance(doc.get("peer"), str):
                raise ValueError("the body is {peer: the peer agent's URL, bytes?, streams?, "
                                 "rtt_n?}")
            nbytes = int(doc.get("bytes") or linktest.DEFAULT_BYTES)
            streams = int(doc.get("streams") or linktest.DEFAULT_STREAMS)
            rtt_n = int(doc.get("rtt_n") or linktest.DEFAULT_RTT_N)
            if not (linktest.CHUNK <= nbytes <= linktest.MAX_BYTES and 1 <= streams <= 16
                    and 1 <= rtt_n <= 1000):
                raise ValueError("bytes 1 MiB to 8 GiB, streams 1 to 16, rtt_n 1 to 1000")
            client = self._peer_client(doc["peer"])
            res = linktest.run(client, linktest.interfaces(a.reader, a.addrs()), nbytes=nbytes,
                               streams=streams, rtt_n=rtt_n, ping=a.ping)
            self._audit(f"linktest to {doc['peer']}", 200)
            return self._json(200, res)
        return self._err(404, "not_found", f"no route {method} /node/linktest/{'/'.join(rest)}")

    def _peer_client(self, url: str) -> nodeclient.Client:
        u = urllib.parse.urlsplit(url)
        if u.scheme == "https":
            try:
                ctx = nodetrust.store_client_context(self.agent.store)
            except nodetrust.TrustError as exc:
                raise ValueError(f"an https peer needs this node's paired certificate: {exc}")
            return nodeclient.Client(url, ssl_context=ctx, timeout=30)
        try:
            loop = nodetrust.parse_ip(u.hostname or "").is_loopback
        except nodetrust.BindRefused:
            loop = False
        if u.scheme != "http" or not loop:
            raise ValueError("a peer is https://<address>:<port> (mTLS), or http:// on loopback "
                             "(an agent on this box, the same secret)")
        return nodeclient.Client(url, secret=self.agent.secret, timeout=30)


class Server(ThreadingHTTPServer):
    """One listening address. With TLS the handshake runs on the request's own thread, so a slow or
    refused client never holds the accept loop."""

    daemon_threads = True
    request_queue_size = 64
    allow_reuse_address = True

    def __init__(self, addr, agent: Agent, mode: str, ctx: ssl.SSLContext | None):
        self.agent, self.mode, self.ctx = agent, mode, ctx
        if ":" in addr[0]:
            self.address_family = socket.AF_INET6
        super().__init__(addr, Handler)

    def finish_request(self, request, client_address):
        try:                         # a head and its body are two writes: no 40 ms Nagle stall
            request.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        if self.ctx is None:
            return super().finish_request(request, client_address)
        try:
            request.settimeout(10.0)
            tls = self.ctx.wrap_socket(request, server_side=True)
            tls.settimeout(None)
        except (ssl.SSLError, OSError) as exc:
            reason = getattr(exc, "reason", None) or type(exc).__name__
            self.agent.log(f"[node] refused TLS from {client_address[0]}: {reason}")
            return
        try:
            Handler(tls, client_address, self)
        finally:
            try:
                tls.close()
            except OSError:
                pass


def listen(agent: Agent, hosts: list[str], port: int) -> list[Server]:
    """A server per address, each in the mode the bind rule gives it (BindRefused otherwise,
    before anything listens)."""
    modes = [(h, nodetrust.listen_mode(h, agent.store)) for h in hosts]
    servers = []
    for h, mode in modes:
        ip = str(nodetrust.parse_ip(h))
        ctx = nodetrust.server_context(agent.store) if mode == "mtls" else None
        srv = Server((ip, port), agent, mode, ctx)
        agent.listening[ip] = {"mode": mode, "port": srv.server_address[1]}
        servers.append(srv)
    return servers


# ====================================================================== the command

def parser() -> argparse.ArgumentParser:
    env = os.environ
    ap = argparse.ArgumentParser(prog="tandem-node", description=__doc__.split("\n")[0])
    ap.add_argument("--host", action="append", default=None,
                    help="an address to listen on (repeatable): loopback until pairing, any other "
                         "only with mTLS; never 0.0.0.0 or a WAN address (TANDEM_NODE_HOST, "
                         "default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=int(env.get("TANDEM_NODE_PORT", DEFAULT_PORT)))
    ap.add_argument("--state-dir", default=env.get("TANDEM_NODE_DIR", DEFAULT_DIR),
                    help="the secret, the trust store, engines.json, the engines' logs")
    ap.add_argument("--tandem-dir", default=env.get("TANDEM_DIR") or None,
                    help="an install.sh install: its engine is the default engine")
    return ap


def _hosts(a) -> list[str]:
    return a.host or [x.strip() for x in os.environ.get("TANDEM_NODE_HOST",
                                                        "127.0.0.1").split(",") if x.strip()]


def unit_text(a) -> str:
    exe = os.path.join(os.path.dirname(sys.executable), "tandem-node")
    cmd = exe if os.path.exists(exe) else f"{sys.executable} -m proxy.nodeagent"
    args = "".join(f" --host {h}" for h in _hosts(a)) + f" --port {a.port} --state-dir " \
        f"{a.state_dir}" + (f" --tandem-dir {a.tandem_dir}" if a.tandem_dir else "")
    return f"""[Unit]
Description=TandemNode agent (tandem-node) on {', '.join(_hosts(a))}:{a.port}
After=network-online.target

[Service]
ExecStart={cmd} serve{args}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""


def main(argv=None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv.pop(0) if argv and argv[0] in ("serve", "check", "unit") else "serve"
    a = parser().parse_args(argv)
    a.state_dir = os.path.expanduser(a.state_dir)
    if cmd == "unit":
        sys.stdout.write(unit_text(a))
        return
    store = nodetrust.Store(os.path.join(a.state_dir, "trust"))
    try:
        modes = [(h, nodetrust.listen_mode(h, store)) for h in _hosts(a)]
    except nodetrust.BindRefused as exc:
        raise SystemExit(f"[node] not listening: {exc}")
    if cmd == "check":
        for h, m in modes:
            print(f"{h}:{a.port} {m}" + (" (plain HTTP, the loopback secret)" if m == "secret"
                                         else " (mTLS, the trust store)"))
        return
    from server import logbuf
    buf = logbuf.install()
    try:
        agent = Agent(a.state_dir, tandem_dir=a.tandem_dir, logbuf=buf)
        servers = listen(agent, _hosts(a), a.port)
    except (nodetrust.TrustError, nodetrust.BindRefused, ValueError, OSError) as exc:
        raise SystemExit(f"[node] not starting: {exc}")
    ident = agent.identity()
    print(f"[node] TandemNode {ident.get('tandemllm', '')} node {ident['id']} on "
          + ", ".join(f"{ip}:{m['port']} ({m['mode']})" for ip, m in agent.listening.items())
          + "; engines: " + ", ".join(f"{e.id} :{e.port}{' managed' if e.managed else ''}"
                                       for e in agent.engines.list)
          + f"; psutil {'yes' if agent.collector.psutil else 'no'}, nvml "
          f"{'yes' if agent.collector._nvml_handle() else 'no'}", flush=True)
    for s in servers[1:]:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    import signal

    def stop(signum, frame):
        agent.sampler.close()
        for s in servers:
            threading.Thread(target=s.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    try:
        servers[0].serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        agent.sampler.close()
        for s in servers:
            s.server_close()
        print("[node] stopped", flush=True)


if __name__ == "__main__":
    main()
