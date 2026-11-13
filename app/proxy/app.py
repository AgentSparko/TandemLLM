"""The standalone proxy: the dashboard and the OpenAI routes in front of any backend, no torch.

    python -m proxy --config-db ~/.tandemllm/config.sqlite3 --port 8080

serves on one port:

    POST /v1/chat/completions, /v1/completions, /v1/embeddings, /v1/responses
                                   relayed to the backend the model routes to (proxy/route.py)
    GET  /v1/models, /v1/models/{id}
                                   each fleet deployment once under its API id (SRV-731,
                                   proxy/deployments.py), then every backend's real models,
                                   `owned_by` its id
    GET  /dashboard/               the dashboard (dashboard/dist, served from disk)
    GET  /v1/dashboard/*           summary, usage, requests, system, live, logs, metrics, session
    *    /v1/config/*              the Backends and Setup screens (proxy/config_api.py)
    *    /v1/fleet/*               TandemApp's controller: the fleet registry, its nodes'
                                   figures and the saved layouts (proxy/fleet.py, SRV-723);
                                   each node's Setup Guide relayed (proxy/fleetsetup.py)
    GET  /v1/fleet/ledger/{summary,usage,requests}
                                   this controller's own ledger, as /v1/dashboard/* answers it
                                   (VIS-254: a page whose /v1/dashboard/* is an engine's reads
                                   the fleet's ledger here)
    POST /v1/fleet/gateway/chat/completions
                                   /v1/chat/completions through this gateway for a page whose
                                   /v1/* is an engine's (VIS-254, the Playground's targets;
                                   `X-Tandem-Pin: <node>` pins a deployment's member)
    GET  /metrics                  the proxy's families in the Prometheus text format
    GET  /health                   {"status": "ok"}; the full body for the admin or the machine
    *    /v1/conductor/*           the conductor's documents, journal and sessions (P4, with
                                   --conductor-config; the engine-only screens answer 404)
    GET  /v1/library[/...]         the package library for the model's scripts (P4)
    POST /v1/laya/ask              Laya, when the `decider` role names a Jev endpoint (P4)
    *    /s/<token>/v1/library..., /s/<token>/v1/laya...
                                   the same, attributed to the session the token names

The engine's own server mounts the same service with `--backends` (server/app.py); this entry
point is for everyone without a Spark. The dashboard's engine-only panels (caches, the
conductor, speculation, memory) answer 404 here and the views say so.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, parse_qsl, unquote, urlencode, urlsplit

from proxy import config as proxy_config
from proxy import config_api
from proxy import deployments
from proxy import fleet as fleet_mod
from proxy import hostcollect
from proxy import nodesource
from proxy import route as route_mod
from proxy.service import PROXY_PATHS, Service
from tandemapp import auth as auth_mod
from tandemapp import chat_forward
from tandemapp import dashboard_api
from tandemapp import live as live_mod
from tandemapp import parallel as parallel_mod
from tandemapp import logbuf
from tandemapp import metrics as metrics_mod
from tandemapp import static

STATE: dict = {}
GATEWAY = "/v1/fleet/gateway/chat/completions"
# SRV-733: on this server's /v1/models answers, so a page behind a reverse proxy knows whether its
# own /v1 (the data plane) reaches this controller or an engine (dashboard lib/fleet-state.ts)
GATEWAY_HEADER = ("X-Tandem-Gateway", "tandem-app")
# a /v1/fleet/* request body (not the gateway's chat body) is read only up to this: the body is
# read before the route's guard, so a bigger one is refused unread (an import is a few KiB)
FLEET_BODY_MAX = 4 * 1024 * 1024
# SRV-605: the request phase has a timeout, the answer has none. A client that connects and sends
# nothing, stops halfway through its request, or keeps a connection idle between requests gives
# the thread back after this many seconds (`--idle-timeout`, TANDEM_IDLE_TIMEOUT_S; 0 for none).
# Handler.handle_one_request arms it on the client's socket before each request line and
# send_response lifts it as the answer starts, so a stream (a relayed answer, the live view, the
# logs, a chatd SSE, the chat WebSocket; chat_forward lifts it before chatd's head) is never cut
# by it, however long its upstream is quiet or the client is slow to read.
IDLE_TIMEOUT_S = 60.0

# The body framing -- BodyError, the cap, the Content-Length spine, the chunked parser --
# is tandemapp/http_body.py, the same code the standalone server calls: the two servers
# carried it line for line and a fix had to land twice.
from tandemapp import http_body  # noqa: E402

BodyError = http_body.BodyError


def version() -> str:
    """VERSION in a checkout, the distribution's version when installed (proxy/_version.py)."""
    from proxy import _version
    return _version.release()


def _live_engine() -> dict:
    """The live stream's engine block for a server with no engine: the proxy's in-flight
    count."""
    svc: Service = STATE["svc"]
    return {"model": None, "version": STATE.get("version", ""),
            "draining": bool(STATE.get("draining")), "kv": {"length": 0, "max_len": 0},
            "memory": {"mem_available_gib": None, "rss_gib": None}, "store": None}


def _live_status() -> dict:
    svc: Service = STATE["svc"]
    n = svc.relay.inflight.total()
    return {"status": "draining" if STATE.get("draining") else ("busy" if n else "ok"),
            "running": n, "waiting": 0, "lane": 0,
            "uptime_s": round(time.time() - svc.started, 1),
            "last_request_at": None, "backends": svc.backends_view()}


def _hosts() -> list | None:
    """`system.hosts`: this machine first, then the backends' remote hosts. The fleet's sampler
    reads the same collector (every 2 s, so its 5-minute charts have a sample per point)."""
    svc: Service = STATE["svc"]
    args = STATE.get("args") or {}
    return hostcollect.hosts_for(
        STATE, svc.cfg.backends(),
        hostcollect.engine_entry(args.get("port"), "this proxy", backend="proxy"),
        paths=[os.path.dirname(svc.cfg.path)], fast_period=fleet_mod.SAMPLE_S - 0.25)


def _system() -> dict:
    svc: Service = STATE["svc"]
    led = STATE.get("ledger")
    li = led.info() if led is not None else None
    hosts = _hosts()
    mem = dashboard_api.meminfo()
    if hosts and mem["unified_total_bytes"] is None:          # no /proc here (macOS)
        mem["unified_total_bytes"] = hosts[0]["memory"]["total_bytes"]
        mem["unified_available_bytes"] = hosts[0]["memory"]["available_bytes"]
    gpu = hostcollect.gpu_block(STATE) if hosts else None
    return {
        "engine": {"version": STATE.get("version", ""), "git_sha": "", "code_sha256": "",
                   "started_at": dashboard_api.iso_utc_s(svc.started),
                   "uptime_s": round(time.time() - svc.started, 1), "pid": os.getpid(),
                   "model": None, "base_model": None, "max_len": None, "drafter": None,
                   "tree": False, "reasoning_format": None, "reasoning_effort": None,
                   "status": _live_status()["status"]},
        "flags": {"args": dashboard_api.redact_args(STATE.get("args") or {}),
                  "env": dashboard_api.redact_env(dict(os.environ))},
        "memory": dict(mem, gpu_allocated_bytes=None,
                       gpu_reserved_bytes=None, gpu_max_allocated_bytes=None),
        "gpu": gpu or STATE.setdefault("gpu", dashboard_api.GpuSampler()).sample(),
        "caches": None,
        "queue": {"running": svc.relay.inflight.total(), "waiting": 0, "max_queue": None,
                  "queue_timeout_s": None, "request_timeout_s": None},
        "inflight": {"served": 0, "refused": 0, "errors": 0, "timeouts": 0, "abandoned": 0},
        "ledger": {"enabled": li is not None,
                   "rows": (li or {}).get("rows") or 0, "bytes": (li or {}).get("bytes") or 0,
                   "oldest": dashboard_api.iso_utc((li or {}).get("oldest_ms")),
                   "queue": (li or {}).get("queue") or 0,
                   "dropped": (li or {}).get("dropped") or 0},
        "disk": {"state_dir_free_bytes": dashboard_api.disk_free(os.path.dirname(
            STATE["svc"].cfg.path))},
        "proxy": {"mode": "standalone", "backends": svc.backends_view()},
        **({"hosts": hosts} if hosts is not None else {}),
    }


def dashboard() -> "dashboard_api.DashboardAPI":
    api = STATE.get("dashboard_api")
    if api is None:
        api = STATE["dashboard_api"] = dashboard_api.DashboardAPI(
            STATE.get("ledger"), live=_live_status, system=_system,
            backends=lambda: STATE["svc"].cfg.backends())
    return api


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "TandemLLM-proxy"

    def log_message(self, fmt, *args):
        if STATE.get("verbose"):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def handle_one_request(self):
        self.connection.settimeout(self.timeout)          # the request phase (IDLE_TIMEOUT_S)
        self._body_done = False
        super().handle_one_request()
        # a chunked body the handler never read stayed on the wire and the next request on
        # the keep-alive connection was parsed from the middle of it (the size line as a
        # request line). A body this handler never touched at all closes the connection,
        # the same decision the standalone server made.
        if (not self.close_connection and not self._body_done
                and "chunked" in (self.headers.get("Transfer-Encoding") or "").lower()):
            self.close_connection = True
        # RFC 9112 section 6.1: a request with BOTH Transfer-Encoding: chunked and
        # Content-Length is answered and then the connection is closed -- the two
        # framings disagree about where the body ends. The rule is the engine server's
        # (tandemapp/http_body.py), so both servers answer such a request the same way.
        if not self.close_connection and http_body.conflicting_framing(self.headers):
            self.close_connection = True

    def send_response(self, code, message=None):
        self.connection.settimeout(None)                  # the answer: no timeout
        super().send_response(code, message)

    # ----------------------------------------------------------------- helpers
    def _json(self, code: int, payload, extra_headers: tuple = ()) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode()
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            for k, v in extra_headers:
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def _raw_json(self, code: int, raw: bytes, extra_headers: tuple = ()) -> None:
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            for k, v in extra_headers:
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def _send_empty(self, code: int, headers: tuple = ()) -> None:
        try:
            self.send_response(code)
            for k, v in headers:
                self.send_header(k, v)
            self.send_header("Content-Length", "0")
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    _body_done = False               # the request in hand has had its body read to the end

    def _read_raw(self) -> bytes:
        """The request's body, consumed whole or not at all -- tandemapp/http_body.py owns
        the framing, the same module the standalone server calls. An answer written with
        the body still unread is a desync one request away: every path out of here has
        read to the end of the framing, and BodyError (or handle_one_request) closes the
        connection."""
        try:
            raw = http_body.read_body(self.rfile, self.headers)
        except BodyError:
            self.close_connection = True     # the promised bytes, if they ever come,
            raise                            # would be read as the next request
        self._body_done = True
        return raw

    def _body_error(self, exc) -> None:
        """A framing/body refusal: 400 or 413 with the error's own message. The connection
        closes afterwards: the unread body would be the next request."""
        self._json(exc.code, {"error": {"type": exc.type, "message": exc.message}})

    def _fleet_too_big(self, path: str) -> bool:
        """A /v1/fleet/* body over FLEET_BODY_MAX: 413, unread, and the connection closed."""
        if not path.startswith("/v1/fleet/") or path == GATEWAY:
            return False
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if 0 <= n <= FLEET_BODY_MAX:
            return False
        self.close_connection = True
        self._json(413, {"error": {"type": "too_large", "message": f"a fleet request's body is "
                                   f"at most {FLEET_BODY_MAX // (1024 * 1024)} MiB"}})
        return True

    def _peer(self) -> str:
        return (self.client_address or ("",))[0]

    def _allowed(self, route: str) -> bool:
        verdict = STATE["auth"].decide(route, self._peer(), self.headers)
        if verdict == "ok":
            return True
        path = self.path.split("?")[0]
        if verdict == "absent":
            self._json(404, {"error": {"type": "not_found", "message": f"no route {path}"}})
        else:
            self._json(401, {"error": {"type": "unauthorized",
                                       "message": "a bearer token or the dashboard session is "
                                                  "needed"}},
                       extra_headers=(("WWW-Authenticate", 'Bearer realm="TandemLLM"'),))
        return False

    def _cross_site(self) -> bool:
        """A write from another site or origin (tandemapp/auth.py same_origin): 403, True. The
        configuration's and the conductor's writes take no cookie on an open controller or from
        this machine, and a form's POST needs no preflight (6.3b)."""
        bad = auth_mod.same_origin(self.headers)
        if bad is None:
            return False
        self._json(bad[0], {"error": {"type": bad[1], "code": bad[1], "message":
                                      "a request from another site or origin cannot change "
                                      "this server"}})
        return True

    def _reader_gone(self) -> bool:
        from proxy.relay import client_gone
        return client_gone(self)

    # ----------------------------------------------------------------- routes
    def do_GET(self):
        if self._strip_session():
            return
        if chat_forward.route(self, "GET", STATE["auth"]):     # the Chat tab's chatd (SRV-343)
            return
        raw = self.path.split("?")[0]
        if raw == "/dashboard" or raw.startswith("/dashboard/"):
            return static.serve(self, self.path, STATE.get("dashboard_dir"))
        path = raw.rstrip("/") or "/"
        if path == "/":
            return self._send_empty(302, (("Location", "/dashboard/"),))
        if path.startswith("/v1/dashboard/"):
            return self._dashboard_get(path)
        if path == "/v1/config" or path.startswith("/v1/config/"):
            return config_api.handle(self, STATE["svc"], "GET", path, None)
        if path.startswith("/v1/fleet/"):
            return self._fleet("GET", path, None)
        if path in ("/health", "/healthz", "/v1/health"):
            code = 503 if STATE.get("draining") else 200
            out = {"status": "draining" if STATE.get("draining") else "ok"}
            if STATE["auth"].decide("health_full", self._peer(), self.headers) == "ok":
                out["backends"] = STATE["svc"].backends_view()
                out["version"] = STATE.get("version")
                # OPS-246: the page's bundle this server serves (its content hash and manifest)
                out["dashboard"] = static.bundle_info(STATE.get("dashboard_dir"))
            return self._json(code, out)
        if path == "/metrics":
            if not self._allowed("metrics"):
                return
            return self._metrics()
        if path == "/v1/models":
            ok, _ = STATE["svc"].authorize(self, (GATEWAY_HEADER,))
            if not ok:
                return
            # SRV-733: beside `data`, what a TandemLLM engine's own list holds (decision models)
            return self._json(200, {"object": "list",
                                    "data": STATE["svc"].models(int(STATE["svc"].started)),
                                    **STATE["svc"].model_extras()},
                              extra_headers=(GATEWAY_HEADER,))
        if path.startswith("/v1/models/"):
            # the list above asks for a key; a single card was answering without one,
            # so a configured deployment leaked model ids and card metadata to strangers
            ok, _ = STATE["svc"].authorize(self, (GATEWAY_HEADER,))
            if not ok:
                return
            name = unquote(path[len("/v1/models/"):])
            card = STATE["svc"].card(name, int(STATE["svc"].started))
            if card is None:
                # SRV-733: an id the default engine answers that no list names (its raw model's
                # alias, a decision model): the engine's own answer
                got = STATE["svc"].default_card(name)
                if got is not None and got[0] in (200, 404):
                    return self._raw_json(got[0], got[1], (GATEWAY_HEADER,))
                return self._json(404, {"error": {"message": f"no model {name!r}",
                                                  "type": "invalid_request_error",
                                                  "param": "model", "code": "model_not_found"}},
                                  extra_headers=(GATEWAY_HEADER,))
            return self._json(200, card, extra_headers=(GATEWAY_HEADER,))
        if path.startswith("/v1/conductor/"):
            return self._conductor("GET")
        if (path == "/v1/library" or path.startswith("/v1/library/")) and \
                STATE.get("conductor") is not None:
            from tandemapp import library_api
            return library_api.handle_get(self, STATE["conductor"])
        if path in ("/v1/laya", "/v1/laya/") and STATE.get("laya") is not None:
            from tandemapp import laya_api
            return laya_api.handle_get(self, STATE["laya"], STATE)
        return self._json(404, {"error": {"message": f"no route {path}", "type": "not_found"}})

    def _conductor(self, method: str, body=None):
        cond = STATE.get("conductor")
        if cond is None:
            return self._json(404, {"error": {"type": "not_found",
                                              "message": "the conductor is off on this proxy "
                                                         "(--conductor-config off)"}})
        from tandemapp.conductor import routes as conductor_routes
        return conductor_routes.handle(self, cond, method, body, STATE["auth"])

    def _strip_session(self) -> bool:
        """`/s/<token>/v1/library...` and `/s/<token>/v1/laya...` (server/session_tokens.py):
        the prefix off, the session on the handler; True when answered here (a 404)."""
        from tandemapp import session_tokens as st_mod
        self._sess = self._stok = None
        path, tok = st_mod.strip(self.path)
        if tok is None:
            return False
        p = path.split("?")[0].rstrip("/")
        if not (p in ("/v1/library", "/v1/laya") or p.startswith("/v1/library/")
                or p.startswith("/v1/laya/")):
            self._json(404, {"error": {"message": f"no route {p} under a session address",
                                       "type": "not_found"}})
            return True
        self.path, self._stok = path, tok
        reg = STATE.get("session_tokens")
        self._sess = reg.key_for(tok) if reg is not None else None
        return False

    def do_POST(self):
        if self._strip_session():
            return
        if chat_forward.route(self, "POST", STATE["auth"]):
            return
        path = self.path.split("?")[0].rstrip("/")
        t_arrival = time.perf_counter()
        if self._fleet_too_big(path):
            return
        try:
            raw = self._read_raw()
        except BodyError as exc:
            return self._body_error(exc)
        if path.startswith("/v1/collect/"):          # P6: a plugin's batch, its own token
            from proxy import collect_api
            return collect_api.handle(self, STATE["svc"], path, raw)
        if path == GATEWAY:
            return self._gateway(raw, t_arrival)
        if path.startswith("/v1/fleet/"):
            return self._fleet("POST", path, raw)
        try:
            body = json.loads(raw or b"{}")
        except (ValueError, RecursionError) as exc:
            if path in PROXY_PATHS:
                # a row as the engine writes one for a body it cannot read (SRV-733: counted once
                # when the data plane comes here)
                STATE["svc"].account_refusal(self, path, None, 400)
            return self._json(400, {"error": {"message": f"bad json: {exc}",
                                              "type": "invalid_request_error"}})
        if path == "/v1/dashboard/session":
            return self._session("POST", body)
        if (path == "/v1/config" or path.startswith(("/v1/config/", "/v1/conductor/"))) and \
                self._cross_site():
            return
        if path == "/v1/config" or path.startswith("/v1/config/"):
            return config_api.handle(self, STATE["svc"], "POST", path, body)
        if path in PROXY_PATHS:
            return self._complete(path, raw, body, t_arrival)
        if path in deployments.CACHE_PATHS and STATE["svc"].cache_route is not None:
            return self._cache(path, body)
        if path.startswith("/v1/conductor/"):
            return self._conductor("POST", body)
        if path == "/v1/laya/ask" and STATE.get("laya") is not None:
            from tandemapp import laya_api
            return laya_api.handle_post(self, STATE["laya"], STATE, body)
        return self._json(404, {"error": {"message": f"no route {path}", "type": "not_found"}})

    def _complete(self, path, raw, body, t_arrival):
        svc: Service = STATE["svc"]
        ok, user = svc.authorize(self)
        if not ok:
            svc.account_refusal(self, path, body, 401, finish="refused",
                                error_type="invalid_api_key")
            return
        if not isinstance(body, dict):
            return self._json(400, {"error": {"message": "the body must be a JSON object",
                                              "type": "invalid_request_error"}})
        from proxy import deployments
        try:
            route = svc.resolve(body, self.headers)
        except route_mod.NotFound as exc:
            svc.account_refusal(self, path, body, 404, user=user, error_type="model_not_found")
            return self._json(404, exc.body())
        except deployments.Refused as exc:         # SRV-731: a stopped deployment, a Split
            svc.account_refusal(self, path, body, exc.status, user=user, finish="refused",
                                error_type=exc.code)
            return self._json(exc.status, exc.body(), extra_headers=exc.headers)
        svc.serve(self, path, raw, body, route, user, t_arrival=t_arrival)

    def _cache(self, path, body):
        """SRV-741: a cache route on the member of a deployment that holds the conversation,
        under the rule of the completions (the proxy's API keys when users exist)."""
        svc: Service = STATE["svc"]
        ok, _user = svc.authorize(self)
        if not ok:
            return
        try:
            st, doc, hdrs = svc.cache_route(path, body, self.headers)
        except route_mod.NotFound as exc:
            return self._json(404, exc.body())
        except deployments.Refused as exc:
            return self._json(exc.status, exc.body(), extra_headers=exc.headers)
        return self._json(st, doc, extra_headers=hdrs)

    def _gateway(self, raw: bytes, t_arrival: float) -> None:
        """`POST /v1/fleet/gateway/chat/completions` (VIS-254): the page's own JSON POST, then the
        completion as /v1/chat/completions takes it (the proxy's API keys when users exist)."""
        bad = auth_mod.same_origin_json(self.headers)
        if bad:
            words = {"json_only": "the gateway takes a JSON body (Content-Type: application/json)",
                     "cross_site": "a request from another site cannot use the gateway",
                     "cross_origin": "a request from another origin cannot use the gateway"}
            return self._json(bad[0], {"error": {"type": "invalid_request_error", "code": bad[1],
                                                 "message": words.get(bad[1], bad[2])}})
        try:
            body = json.loads(raw or b"{}")
        except (ValueError, RecursionError) as exc:
            return self._json(400, {"error": {"message": f"bad json: {exc}",
                                              "type": "invalid_request_error"}})
        return self._complete("/v1/chat/completions", raw, body, t_arrival)

    def _fleet_ledger(self, name: str) -> None:
        """`GET /v1/fleet/ledger/{summary,usage,requests}` (VIS-254): this controller's ledger
        under the dashboard's rule, the same answers as /v1/dashboard/*."""
        if not self._allowed("dashboard"):
            return
        api = dashboard()
        fn = {"summary": api.summary, "usage": api.usage, "requests": api.requests}.get(name)
        if fn is None:
            return self._json(404, {"error": {"type": "not_found",
                                              "message": f"no route /v1/fleet/ledger/{name}"}})
        q = {k: v[-1] for k, v in parse_qs(urlsplit(self.path).query).items()}
        try:
            return self._json(200, fn(q), extra_headers=(("Cache-Control", "no-store"),))
        except dashboard_api.ApiError as exc:
            return self._json(exc.status, exc.body())

    def _write(self, method: str):
        if chat_forward.route(self, method, STATE["auth"]):
            return
        path = self.path.split("?")[0].rstrip("/")
        if self._fleet_too_big(path):
            return
        try:
            raw = self._read_raw()
        except BodyError as exc:
            return self._body_error(exc)
        if path.startswith("/v1/fleet/"):
            return self._fleet(method, path, raw)
        try:
            body = json.loads(raw) if raw else None
        except (ValueError, RecursionError) as exc:
            return self._json(400, {"error": {"message": f"bad json: {exc}",
                                              "type": "invalid_request_error"}})
        if method == "DELETE" and path == "/v1/dashboard/session":
            return self._session("DELETE")
        if (path == "/v1/config" or path.startswith(("/v1/config/", "/v1/conductor/"))) and \
                self._cross_site():
            return
        if path == "/v1/config" or path.startswith("/v1/config/"):
            return config_api.handle(self, STATE["svc"], method, path, body)
        if path.startswith("/v1/conductor/"):
            return self._conductor(method, body)
        return self._json(404, {"error": {"message": f"no route {path}", "type": "not_found"}})

    def do_PUT(self):
        return self._write("PUT")

    def do_PATCH(self):
        return self._write("PATCH")

    def do_DELETE(self):
        return self._write("DELETE")

    def _fleet(self, method: str, path: str, raw: bytes | None) -> None:
        """`/v1/fleet/*` (proxy/fleet.py): reads (an action's plan too) and a page's window
        layout under the dashboard's rule, writes (a node's alias or colour, an acknowledgement,
        a drain, a restart, a link test; discovery and the Add Spark session, read too) under
        the configuration's, pairing, release, forget, a lease's release and the wizard's
        connect, check and install under `fleet_trust` (server/auth.py), and the controller's
        own sign-in at /v1/fleet/session."""
        sub = path.rstrip("/").split("/")[3:]
        if method != "GET":
            # a write from another site or origin is refused before anything reads it: the
            # session cookie is SameSite=Strict, but trusted-local and an open controller need no
            # cookie, and a form's POST needs no preflight (6.3b)
            bad = auth_mod.same_origin(self.headers)
            if bad is not None:
                return self._json(bad[0], {"error": {"type": bad[1], "code": bad[1], "message": (
                    "a request from another site cannot change the fleet" if bad[1] ==
                    "cross_site" else "a request from another origin cannot change the fleet")}})
        if method == "GET" and len(sub) == 2 and sub[0] == "ledger":
            return self._fleet_ledger(sub[1])
        if len(sub) >= 4 and sub[0] == "nodes" and sub[2] == "setup":
            return self._fleet_setup(method, unquote(sub[1]), [unquote(x) for x in sub[3:]], raw)
        body = None
        if raw:
            try:
                body = json.loads(raw)
            except (ValueError, RecursionError) as exc:
                return self._json(400, {"error": {"type": "bad_request",
                                                  "message": f"bad json: {exc}"}})
        if path.rstrip("/") == "/v1/fleet/session":
            return self._session(method, body, fleet=True)
        parts = path.rstrip("/").split("/")[3:]
        if parts == ["app", "password"]:
            return self._app_password(method, body)
        if parts[:1] == ["app"] and method == "POST":
            bad = auth_mod.same_origin_json(self.headers)
            if bad is not None:
                return self._json(bad[0], {"error": {"type": bad[1], "message": bad[2]}})
        if parts[:1] == ["app"]:
            # 6.3b: the page's view follows the reads; the export (the whole registry, a backup)
            # and a plan need write rights, an import that writes pairing's
            route = ("dashboard" if method == "GET" and parts == ["app"] else
                     "fleet_trust" if parts == ["app", "import", "apply"] else "conductor_write")
        elif len(parts) >= 4 and parts[0] == "nodes" and parts[2] == "setup-confirm":
            # VIS-252: a Setup Guide write's plan, under the rule of the engine's own /v1/setup
            route = "setup"
        elif parts[:1] == ["discovery"]:
            # SRV-727: the LAN scan's consent is a write, and the list (~/.ssh/config's hosts,
            # what answers on the LAN) is read with a write's rights too
            route = "conductor_write"
        elif parts[:1] == ["add"]:
            # VIS-246: the wizard's session is read as discovery is; connecting, checking over
            # SSH and installing log in to a machine and pair it: pairing's rights
            route = "fleet_trust" if method == "POST" and (
                parts[-1:] in (["connect"], ["check"], ["install"])) else "conductor_write"
        elif method == "GET" or (method == "PUT" and parts[:1] == ["layouts"]) or \
                (method == "POST" and parts == ["placement"]):
            # SRV-731: the placement planner is a read (each node's memory estimate) in a POST
            route = "dashboard"
        elif parts[:1] in (["update"], ["controller"]):
            # OPS-234: the fleet update installs software on every node, a move hands every node
            # to another controller: pairing's rights
            route = "fleet_trust"
        elif (method == "POST" and parts[:1] == ["pair"]) or (method == "DELETE"
                                                              and parts[:1] == ["nodes"]) or \
                (method == "POST" and len(parts) == 4 and parts[2] == "actions"
                 and parts[3] in ("forget", "release-lease")):
            route = "fleet_trust"
        else:
            route = "conductor_write"
        a = STATE["auth"]
        if route == "fleet_trust" and not a.password_set and \
                a.decide(route, self._peer(), self.headers) != "ok":
            return self._json(401, {"error": {
                "type": "unauthorized", "message": "pairing and release change who controls a "
                "node: set an admin password (App settings, or TANDEM_ADMIN_TOKEN) to do it from another "
                "machine, or ask from this one"}})
        if not self._allowed(route):
            return
        q = {k: v[-1] for k, v in parse_qs(urlsplit(self.path).query).items()}
        code, out = STATE["fleet"].handle(method, path, q, body,
                                          who=config_api._who(self, STATE["svc"]))
        if code == 200 and parts in (["app"], ["app", "permissions", "local-network"]):
            out["password"] = self._password_view()
        extra = (("Cache-Control", "no-store"),)
        if code == 200 and parts == ["app", "export"]:
            extra += (("Content-Disposition", 'attachment; filename="tandemapp-fleet-config.json"'),)
        return self._json(code, out, extra_headers=extra)

    # ----------------------------------------------------------------- the admin password (6.3b)
    def _password_view(self) -> dict:
        from proxy import adminpw
        a = STATE["auth"]
        return {"set": a.password_set, "password": a.has_password, "token": bool(a.admin),
                "set_at": a.password.set_at if a.has_password else None,
                "min_length": adminpw.MIN_LEN, "max_length": adminpw.MAX_LEN,
                "this_machine": a.trusted_local(self._peer(), self.headers)}

    def _app_password(self, method: str, body) -> None:
        """`/v1/fleet/app/password` (proxy/adminpw.py): GET its state under the reads' rule; PUT
        {current?, password} sets or changes it, DELETE {current} removes it, under pairing's rule
        (no password yet: from this machine only), each with the current password or the admin
        token whenever one is set, as a JSON request of this origin. Setting, changing or
        removing it ends every other session; this browser gets a new cookie."""
        from proxy import adminpw
        a = STATE["auth"]
        nostore = (("Cache-Control", "no-store"),)
        if method == "GET":
            if not self._allowed("dashboard"):
                return
            return self._json(200, {"contract_version": fleet_mod.CONTRACT,
                                    **self._password_view()}, extra_headers=nostore)
        if method not in ("PUT", "DELETE"):
            return self._json(405, {"error": {"type": "method_not_allowed", "message":
                                              "the password takes GET, PUT and DELETE"}})
        bad = auth_mod.same_origin_json(self.headers)
        if bad is not None:
            return self._json(bad[0], {"error": {"type": bad[1], "message": bad[2]}})
        if not a.password_set and not a.trusted_local(self._peer(), self.headers):
            return self._json(401, {"error": {"type": "unauthorized", "message":
                                              "no admin password is set yet: set the first one "
                                              "from this machine (its own browser), or run "
                                              "`tandem-app password set` on it"}})
        if not self._allowed("fleet_trust"):
            return
        want = {"current", "password"} if method == "PUT" else {"current"}
        if not isinstance(body, dict) or set(body) - want or \
                (method == "PUT" and "password" not in body):
            return self._json(400, {"error": {"type": "bad_request", "message": (
                "the body is {current?, password}" if method == "PUT" else
                "the body is {current}")}})
        who = self.headers.get("X-Real-IP") or self._peer()
        if a.password_set:
            v = a.sign_in(body.get("current"), who)
            if v == "held":
                return self._json(429, {"error": {"type": "too_many", "message":
                                                  "too many wrong passwords; wait a minute"}},
                                  extra_headers=(("Retry-After", "60"),))
            if v != "ok":
                return self._json(403, {"error": {"type": "wrong_password", "message":
                                                  "the current password (or the admin token) "
                                                  "is not right"}})
        actor = config_api._who(self, STATE["svc"])
        if method == "PUT":
            had = a.has_password
            try:
                a.password.set(body["password"])
            except adminpw.PasswordError as exc:
                return self._json(exc.status, {"error": {"type": exc.kind,
                                                         "message": exc.message}})
            what = "changed" if had else "set"
        else:
            if not a.has_password:
                return self._json(409, {"error": {"type": "conflict", "message": (
                    "no password is set here: the admin token stays, change it in the env "
                    "file" if a.admin else "no admin password is set")}})
            a.password.remove()
            what = "removed"
        STATE["svc"].cfg.log_audit(actor, "password", "controller", None,
                                   detail={"text": f"admin password {what}"})
        STATE["svc"].log(f"[fleet] admin password {what} ({actor}); every session before it "
                         f"ends")
        secure = "; Secure" if (self.headers.get("X-Forwarded-Proto") or "") == "https" else ""
        if a.password_set:
            value, _ = a.make_cookie()
            cookie = (f"{auth_mod.COOKIE}={value}; HttpOnly; SameSite=Strict; Path=/; "
                      f"Max-Age={auth_mod.SESSION_S}{secure}")
        else:
            cookie = f"{auth_mod.COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0{secure}"
        return self._json(200, {"contract_version": fleet_mod.CONTRACT, **self._password_view()},
                          extra_headers=nostore + (("Set-Cookie", cookie),))

    def _fleet_setup(self, method: str, nid: str, sub: list[str], raw: bytes | None) -> None:
        """`/v1/fleet/nodes/{id}/setup/<route>` (proxy/fleetsetup.py, VIS-252): the node's Setup
        Guide through its agent, under the rule of the engine's own /v1/setup; a POST is a
        same-origin JSON request, a write carries its plan's confirm (?confirm=, never passed on).
        A stream (a job's log, Wren's answer) is passed on as it arrives."""
        if not self._allowed("setup"):
            return
        if method not in ("GET", "POST"):
            return self._json(405, {"error": {"type": "method_not_allowed",
                                              "code": "method_not_allowed",
                                              "message": "the Setup Guide takes GET and POST"}})
        if method == "POST":
            bad = auth_mod.same_origin_json(self.headers)
            if bad is not None:
                return self._json(bad[0], {"error": {"type": bad[1], "code": bad[1],
                                                     "message": bad[2]}})
        pairs = parse_qsl(urlsplit(self.path).query, keep_blank_values=True)
        confirm = next((v for k, v in pairs if k == "confirm"), None)
        query = urlencode([(k, v) for k, v in pairs if k != "confirm"])
        headers = {"Accept": self.headers.get("Accept") or "application/json",
                   "Content-Type": self.headers.get("Content-Type"),
                   "Last-Event-ID": self.headers.get("Last-Event-ID")}
        try:
            st, ctype, extra, conn, r = STATE["fleet"].setup.open(
                nid, method, sub, query, raw, headers, confirm,
                config_api._who(self, STATE["svc"]))
        except fleet_mod.FleetError as exc:
            return self._json(exc.status, exc.body(), extra_headers=(("Cache-Control",
                                                                      "no-store"),))
        from proxy import nodeclient
        from proxy.relay import client_gone
        try:
            if "text/event-stream" not in ctype:
                data = r.read()
                conn.close()
                self.send_response(st)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                for k, v in extra:
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            self.send_response(st)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            for k, v in extra:
                self.send_header(k, v)
            self.end_headers()
            self.close_connection = True

            def write(piece: bytes) -> None:
                self.wfile.write(piece)
                self.wfile.flush()
            nodeclient.pipe(r, conn, write, lambda: client_gone(self))
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.close_connection = True
        finally:
            conn.close()

    def _metrics(self) -> None:
        raw = STATE["registry"].render().encode()
        try:
            self.send_response(200)
            self.send_header("Content-Type", metrics_mod.CONTENT_TYPE)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    # ----------------------------------------------------------------- the dashboard's API
    def _dashboard_get(self, path: str) -> None:
        if path == "/v1/dashboard/session":
            return self._session("GET")
        if not self._allowed("dashboard"):
            return
        if path == "/v1/dashboard/metrics":
            return self._metrics()
        if path == "/v1/dashboard/logs/export":
            # the whole ring as one text file (SRV-803). The proxy has no commit of its own and
            # no --log-content flag, so the header says "unknown" for both.
            return logbuf.serve_export(self, logbuf.BUFFER, version=STATE.get("version"),
                                       started=getattr(STATE.get("svc"), "started", None),
                                       log_content=None)
        q = {k: v[-1] for k, v in parse_qs(urlsplit(self.path).query).items()}
        try:
            if path == "/v1/dashboard/live":
                return self._live(q)
            if path == "/v1/dashboard/parallel":
                return self._parallel(q)
            if path == "/v1/dashboard/logs":
                return self._logs(q)
            api = dashboard()
            fn = {"/v1/dashboard/summary": api.summary, "/v1/dashboard/usage": api.usage,
                  "/v1/dashboard/requests": api.requests,
                  "/v1/dashboard/system": lambda _q: api.system()}.get(path)
            if fn is None:
                return self._json(404, {"error": {"type": "not_found",
                                                  "message": f"no route {path}"}})
            return self._json(200, fn(q), extra_headers=(("Cache-Control", "no-store"),))
        except dashboard_api.ApiError as exc:
            return self._json(exc.status, exc.body())

    def _session(self, method: str, body=None, fleet: bool = False) -> None:
        a = STATE["auth"]
        if fleet:
            return self._fleet_session(method, body)
        # AU1: no admin password set, or the login off -- nothing to sign in to. GET says so
        # (`login: false`, `password_set`), POST and DELETE do not exist.
        if not a.login or not a.password_set:
            if method == "GET":
                return self._json(200, {"contract_version": dashboard_api.SESSION_CONTRACT,
                                        "authenticated": True, "login": False,
                                        "password_set": a.password_set, "expires_at": None},
                                  extra_headers=(("Cache-Control", "no-store"),))
            return self._json(404, {"error": {"type": "not_found",
                                              "message": "the dashboard login is off" if
                                              a.password_set else "no admin password is set"}})
        secure = "; Secure" if (self.headers.get("X-Forwarded-Proto") or "") == "https" else ""
        if method == "GET":
            exp = a.session(self.headers)
            if exp is None and not a.admin_bearer(self.headers):
                return self._allowed("dashboard")
            exp = exp or int(time.time()) + auth_mod.SESSION_S
            return self._json(200, {"contract_version": dashboard_api.SESSION_CONTRACT,
                                    "authenticated": True, "login": True, "password_set": True,
                                    "expires_at": dashboard_api.iso_utc_s(exp)})
        if method == "DELETE":
            a.revoke(self.headers)
            return self._send_empty(204, (("Set-Cookie", f"{auth_mod.COOKIE}=; HttpOnly; "
                                                         f"SameSite=Strict; Path=/; Max-Age=0"
                                                         f"{secure}"),))
        who = a.login_key(self._peer(), self.headers)
        # the one rule, in server/auth.py: X-Real-IP counts from loopback (an nginx on this
        # host) or from a peer named by TANDEM_TRUSTED_PROXY; every other peer is keyed as
        # itself, so a browser that reached us directly cannot pick its own bucket.
        token = (body or {}).get("token") if isinstance(body, dict) else None
        v = a.sign_in(token, who)
        if v == "held":
            return self._json(429, {"error": {"type": "too_many",
                                              "message": "too many failed logins; wait a minute"}},
                              extra_headers=(("Retry-After", "60"),))
        if v != "ok":
            return self._json(401, {"error": {"type": "unauthorized", "message": "wrong token"}},
                              extra_headers=(("WWW-Authenticate", 'Bearer realm="TandemLLM"'),))
        value, _ = a.make_cookie()
        return self._send_empty(204, (("Set-Cookie", f"{auth_mod.COOKIE}={value}; HttpOnly; "
                                                     f"SameSite=Strict; Path=/; "
                                                     f"Max-Age={auth_mod.SESSION_S}{secure}"),))

    def _fleet_session(self, method: str, body) -> None:
        """`/v1/fleet/session`: the controller's sign-in for its writes (pairing, release, a
        node's alias), whatever the dashboard login says: with an admin password set the writes
        need it, and a page behind a reverse proxy that sends /v1/fleet/* here and the rest to an
        engine signs in to the controller here. GET says whether a password is set and whether
        this browser is signed in; POST {token} signs in (the same cookie and limits as
        /v1/dashboard/session); DELETE signs out. No password set: GET only."""
        a = STATE["auth"]
        nostore = (("Cache-Control", "no-store"),)
        if method == "GET":
            exp = a.session(self.headers) if a.password_set else None
            ok = not a.password_set or exp is not None or a.admin_bearer(self.headers)
            return self._json(200, {"contract_version": fleet_mod.CONTRACT,
                                    "password_set": a.password_set, "authenticated": ok,
                                    "expires_at": dashboard_api.iso_utc_s(exp) if exp else None},
                              extra_headers=nostore)
        if not a.password_set:
            return self._json(404, {"error": {"type": "not_found",
                                              "message": "no admin password is set"}})
        secure = "; Secure" if (self.headers.get("X-Forwarded-Proto") or "") == "https" else ""
        if method == "DELETE":
            a.revoke(self.headers)
            return self._send_empty(204, (("Set-Cookie", f"{auth_mod.COOKIE}=; HttpOnly; "
                                                         f"SameSite=Strict; Path=/; Max-Age=0"
                                                         f"{secure}"),))
        if method != "POST":
            return self._json(404, {"error": {"type": "not_found",
                                              "message": f"no route {method} /v1/fleet/session"}})
        who = self.headers.get("X-Real-IP") or self._peer()
        token = body.get("token") if isinstance(body, dict) else None
        v = a.sign_in(token, who)
        if v == "held":
            return self._json(429, {"error": {"type": "too_many",
                                              "message": "too many failed logins; wait a minute"}},
                              extra_headers=(("Retry-After", "60"),))
        if v != "ok":
            return self._json(401, {"error": {"type": "unauthorized", "message": "wrong token"}})
        value, _ = a.make_cookie()
        return self._send_empty(204, (("Set-Cookie", f"{auth_mod.COOKIE}={value}; HttpOnly; "
                                                     f"SameSite=Strict; Path=/; "
                                                     f"Max-Age={auth_mod.SESSION_S}{secure}"),))

    def _sse_head(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def _parallel(self, q: dict) -> None:
        """`GET /v1/dashboard/parallel[?since=<t>]`: every request in flight on every backend
        (server/parallel.py)."""
        board = STATE.get("parallel")
        if board is None:
            raise dashboard_api.ApiError(404, "not_found", "the parallel view is off")
        try:
            since = float(q["since"]) if q.get("since") else None
        except ValueError:
            raise dashboard_api.ApiError(400, "bad_request", "since: a number (unix seconds)")
        data = parallel_mod.encode(board.snapshot(since))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _live(self, q: dict) -> None:
        reg = STATE["live"]
        if q.get("follow", "1") in ("0", "false", "no"):
            return self._json(200, reg.snapshot(), extra_headers=(("Cache-Control", "no-store"),))
        if not reg.subscribe():
            raise dashboard_api.ApiError(429, "too_many",
                                         f"at most {live_mod.MAX_STREAMS} live streams at once")
        try:
            self._sse_head()
            sent, data = reg.first_event()
            self.wfile.write(data)
            self.wfile.flush()
            pinged = time.monotonic()
            while not STATE.get("draining"):
                reg.wait_event(sent, 0.25)
                ev = reg.event()
                if ev is not None and ev[0] > sent:
                    sent = ev[0]
                    self.wfile.write(ev[1])
                    self.wfile.flush()
                    pinged = time.monotonic()
                elif self._reader_gone():
                    break
                elif time.monotonic() - pinged > 15.0:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    pinged = time.monotonic()
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.close_connection = True
        finally:
            reg.unsubscribe()

    def _logs(self, q: dict) -> None:
        buf = logbuf.BUFFER
        level = q.get("level") or "info"
        if level not in logbuf.LEVELS:
            raise dashboard_api.ApiError(400, "bad_request",
                                         f"level must be one of {', '.join(logbuf.LEVELS)}")
        grep = q.get("grep") or None
        try:
            backlog = int(q.get("backlog") or 500)
            lei = self.headers.get("Last-Event-ID")
            since = q.get("since")
            after = int(lei) if lei else (int(since) if since and since.isdigit() else None)
        except ValueError:
            raise dashboard_api.ApiError(400, "bad_request", "backlog and Last-Event-ID are "
                                                             "integers")
        if q.get("follow", "1") in ("0", "false", "no"):
            return self._json(200, {"contract_version": dashboard_api.LOGS_CONTRACT,
                                    "lines": buf.lines(level=level, after=after, grep=grep,
                                                       limit=backlog), "last_seq": buf.seq},
                              extra_headers=(("Cache-Control", "no-store"),))
        sub = buf.subscribe(level, grep, self._reader_gone)
        if sub is None:
            raise dashboard_api.ApiError(429, "too_many",
                                         f"at most {logbuf.MAX_SUBSCRIBERS} log streams at once")
        try:
            self._sse_head()
            last = after or 0

            def send(entries):
                nonlocal last
                for e in entries:
                    if e["seq"] > last:
                        self.wfile.write(f"event: log\nid: {e['seq']}\ndata: "
                                         f"{json.dumps(e, ensure_ascii=False)}\n\n".encode())
                        last = e["seq"]

            send(buf.lines(level=level, after=after, grep=grep, limit=backlog))
            self.wfile.flush()
            quiet = time.monotonic()
            while not STATE.get("draining"):
                items, dropped = sub.take(1.0)
                if sub.closed or self._reader_gone():
                    break
                if dropped:
                    self.wfile.write(f"event: gap\ndata: {json.dumps({'dropped': dropped})}\n\n"
                                     .encode())
                if items:
                    send(items)
                elif not dropped:
                    if time.monotonic() - quiet < 15.0:
                        continue
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
                quiet = time.monotonic()
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.close_connection = True
        finally:
            buf.unsubscribe(sub)


class Server(ThreadingHTTPServer):
    """The standalone server: a thread per request; a peer that resets a keep-alive connection
    between requests is not an error worth a traceback."""

    daemon_threads = True
    # clients that connect in the same moment all get in (the night pass's bench: 64 at once);
    # the default backlog of 5 reset some of them on Linux
    request_queue_size = 128

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def build(args, env=None) -> Service:
    """The service of the standalone server from its arguments (tests call this too)."""
    from proxy import config as config_mod
    from tandemapp import ledger as ledger_mod
    env = os.environ if env is None else env
    Handler.timeout = getattr(args, "idle_timeout", IDLE_TIMEOUT_S) or None
    cfg = config_mod.ConfigDB(args.config_db, env=env,
                              use_keychain=not getattr(args, "no_keychain", False)).open()
    cfg.apply_env()
    from proxy import ctlmove
    try:
        # a move put the fleet's export here (proxy/ctlmove.py): imported once, before anything
        # reads the registry
        ctlmove.import_at_start(cfg, os.path.dirname(cfg.path),
                                log=lambda m: print(m, flush=True))
    except ctlmove.MoveError as exc:
        print(f"[fleet] the fleet's export was not imported: {exc.message}", flush=True)
    led = None
    if args.usage_ledger and args.usage_ledger != "off":
        ledger_mod.check_config(args.usage_ledger, args.usage_retention_days,
                                test=env.get("QSE_TEST") == "1")
        led = ledger_mod.Ledger(args.usage_ledger, retention_days=args.usage_retention_days).open()
    login = (args.dashboard_login == "on")
    admin = env.get("TANDEM_ADMIN_TOKEN") or env.get("QSE_ADMIN_TOKEN") or None
    from proxy import adminpw
    a = auth_mod.Auth(admin, env.get("QSE_METRICS_TOKEN") or None, login=login,
                      trust_loopback=args.trust_loopback == "on", local_host_only=True,
                      trusted_proxies=env.get("TANDEM_TRUSTED_PROXY", "").split(","),
                      password=adminpw.Password(os.path.join(os.path.dirname(cfg.path),
                                                             "admin.json")))
    registry = metrics_mod.Registry()
    live = live_mod.LiveRegistry(blocks=lambda: None, last_prefill=lambda: None,
                                 engine=_live_engine, hz=live_mod.DEFAULT_HZ, activity=False,
                                 queue_timeout=lambda: 0.0)
    STATE.update(version=version(), ledger=led, auth=a, registry=registry, live=live,
                 dashboard_dir=args.dashboard_dir, verbose=args.verbose,
                 args={k: v for k, v in vars(args).items()})
    cond, base = conductor_of(args, led, env)
    svc = Service(cfg, ledger=led, live=live, auth=a, registry=registry,
                  engine_version=STATE["version"], log=lambda m: print(m, flush=True),
                  conductor=cond, monitor_base=base)
    STATE["svc"] = svc
    STATE["parallel"] = parallel_mod.ParallelBoard(live, cost=svc.live_cost,
                                                   peers=svc.peer_backends)
    STATE["fleet"] = fleet_mod.Fleet(svc, nodesource.LocalNode(
        hosts=lambda: (_hosts() or [None])[0], draining=lambda: bool(STATE.get("draining")),
        version=lambda: STATE.get("version", ""), logbuf=logbuf.BUFFER,
        key=lambda bid: svc.cfg.key_for(bid)),
        port=getattr(args, "port", None), bind=getattr(args, "host", "127.0.0.1"),
        log=lambda m: print(m, flush=True), agent=node_agent(env),
        store=fleet_store(args, cfg))
    STATE["fleet"].local_node = env.get("TANDEM_LOCAL_NODE", "on") != "off"
    # the sampler: it sleeps while nothing is in flight (the live stream and the parallel view's
    # once-a-second sample; before 0.3.32 the standalone proxy never started it)
    live.start()
    STATE.pop("dashboard_api", None)
    return svc


def fleet_store(args, cfg):
    """The fleet's store (proxy/fleetstore.py): `--fleet-store` beside (fleet.sqlite3 next to the
    config database), a path, or off (memory only)."""
    from proxy import fleetstore
    where = getattr(args, "fleet_store", "off") or "off"
    if where == "beside":
        where = os.path.join(os.path.dirname(cfg.path), "fleet.sqlite3")
    days = getattr(args, "fleet_retention_days", fleetstore.RETENTION_DAYS)
    if days < fleetstore.MIN_RETENTION_DAYS:
        raise SystemExit(f"--fleet-retention-days is at least {fleetstore.MIN_RETENTION_DAYS}")
    return fleetstore.Store(":memory:" if where == "off" else where, retention_days=days,
                            log=lambda m: print(m, flush=True)).open()


def node_agent(env):
    """This machine's TandemNode agent (SRV-725): TANDEM_NODE_AGENT (default
    http://127.0.0.1:7700; `off` for none) with the loopback secret TANDEM_NODE_SECRET (default
    ~/.tandemllm/node/secret). The fleet reads this machine through it while it answers."""
    url = env.get("TANDEM_NODE_AGENT", "http://127.0.0.1:7700")
    if url == "off":
        return None
    from proxy import nodetrust
    path = os.path.expanduser(env.get("TANDEM_NODE_SECRET", "~/.tandemllm/node/secret"))
    return nodesource.AgentNode(url, secret=lambda: nodetrust.load_secret(path),
                                draining=lambda: bool(STATE.get("draining")),
                                logbuf=logbuf.BUFFER)


def conductor_of(args, led, env) -> tuple:
    """The standalone proxy's conductor (P4): its documents under `--conductor-config`, its
    journal in the usage ledger's file (as the engine's), the session tokens beside the
    documents, the monitor's policy file (`ops/supervisor.yaml`, when PyYAML is there), and
    Laya when the `decider` role names a Jev endpoint. (None, None) with `off`."""
    root = getattr(args, "conductor_config", "off") or "off"
    STATE.update(conductor=None, session_tokens=None, laya=None)
    if root == "off":
        return None, None
    from tandemapp import session_tokens as st_mod
    from tandemapp.conductor import shipped
    from tandemapp.conductor.decisions import Journal
    from tandemapp.conductor.hook import Conductor
    root = os.path.realpath(os.path.expanduser(root))
    os.makedirs(root, mode=0o700, exist_ok=True)
    if getattr(args, "conductor_defaults", "shipped") == "shipped":
        for line in shipped.install_at(root):
            print(f"[conductor] shipped default stored: {line}", flush=True)
    journal = Journal(led.path if led is not None else None)
    cond = Conductor(root, state={"port": getattr(args, "port", None), "model": "TandemLLM"},
                     journal=journal)
    tokens = st_mod.SessionTokens(os.path.join(root, "session-tokens.json"))
    cond.router.session_tokens = tokens
    from tandemapp.conductor import migrations
    migrations.run(cond.store, cond.journal)
    base = None
    try:
        from tandemapp.conductor.monitor import policy as policy_mod
        base = policy_mod.load_default()
    except Exception as exc:                                       # noqa: BLE001
        print(f"[conductor] the monitor's policy file did not load ({type(exc).__name__}): "
              f"E1, the tool checks and the loop guards stay off", flush=True)
    STATE.update(conductor=cond, session_tokens=tokens, laya=laya_of(cond))
    return cond, base


def laya_of(cond):
    """Laya for `/v1/laya/ask` on the standalone proxy: the `decider` role's Jev endpoint over
    HTTP (another engine's decision models, arbiter), as the engine's own registry answers."""
    from concurrent.futures import Future
    from tandemapp.conductor.classify import JevHttp
    _, roles = cond.store.peek("roles")
    r = roles.get("decider") or {}
    ep = str(r.get("endpoint") or "")
    if r.get("mode", "off") == "off" or not ep.startswith(("http://", "https://")):
        return None
    d = JevHttp(ep, r.get("model") or "auto", int(r.get("timeout_ms") or 400))

    class Registry:
        device = None

        def ask(self, state, questions, model="auto", window="idle", deadline_ms=400,
                source=None):
            f: Future = Future()
            try:
                d.timeout_ms = max(d.timeout_ms, int(deadline_ms))
                f.set_result(d.ask(state, questions))
            except Exception as exc:                               # noqa: BLE001
                f.set_exception(exc)
            return f

        def limits(self, model="auto"):
            return d.limits()

        def ready(self):
            return bool(d.health().get("up"))

        def loaded(self):
            return [d.model]

    return Registry()


def serve(args) -> None:
    logbuf.install()
    svc = build(args)
    svc.start()
    STATE["fleet"].start()
    srv = Server((args.host, args.port), Handler)
    led = STATE.get("ledger")
    print(f"[proxy] {version()} on {args.host}:{args.port}, config {svc.cfg.path} "
          f"(schema {svc.cfg.schema_was or 'new'} -> {proxy_config.SCHEMA_VERSION}), {len(svc.cfg.backends())} backends, "
          f"ledger {'off' if led is None else led.path}, {STATE['auth'].describe()}",
          flush=True)
    if svc.cfg.empty():
        print(f"[proxy] no backend yet: open http://{args.host}:{args.port}/dashboard/ for the "
              f"Setup screen, or set TANDEM_BACKENDS", flush=True)
    import signal

    def drain(signum, frame):
        STATE["draining"] = True
        threading.Thread(target=srv.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, drain)
    try:
        srv.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        STATE["fleet"].stop()
        svc.stop()
        if led is not None:
            led.close()
        svc.cfg.close()
