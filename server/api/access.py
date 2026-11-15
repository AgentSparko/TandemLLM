"""Who may reach the engine over the network (engine API 1.9; ENG-456): the HTTP token and the
allow-from list, checked before any route.

THE TOKEN. `tandem remote token rotate` (server/remote, engine-ctl-v1 1.1) makes a new random token,
answers it on its SSH channel and nowhere else, and writes only its sha256 to the tree's
run/http-token (mode 600, one line `sha256:<64 hex>`). The engine reads the file again when it
changes (a stat at most once a RECHECK_S), so a rotation counts within a second and needs no
restart; the token itself is on no disk of the engine box, in no log and in no journal.

    no run/http-token        nothing changes: every caller passes as before (the default)
    run/http-token           a caller that is not trusted-local needs `Authorization: Bearer
                             <token>` on every route, inference included; the admin token (or
                             the dashboard session) passes too, since it opens more. Else 401
                             `unauthorized` with WWW-Authenticate. A file that does not read as
                             a token's hash locks the network out (fail closed) until it does

TRUSTED-LOCAL is server/api/auth.py's rule: a loopback peer without X-Forwarded-For or X-Real-IP
(with --trust-loopback, the default). The controller on the same host, the status responder, the
watchdog, ops/start.sh's checks and the box's test harness call that way and need no token. A
reverse proxy on the box sets the forwarding headers, so what it relays needs the token.

OPEN PATHS, without the token: GET /health, /healthz, /v1/health and /readyz (the status, the
liveness a probe or a controller needs to tell "down" from "not allowed"; a caller without the
token gets `{status}` only, the answer a caller through a proxy always got, and with it the full
body follows the admin rule of server/api/auth.py as before) and /metrics/up (one number). An OPTIONS preflight carries no
credentials by its own rule, so it passes the token too. The allow-from list applies to all of
them.

THE ALLOW-FROM LIST (`QSE_ALLOW_FROM`, a settings key of the managed overlay, hot): addresses and
CIDRs, comma-separated. Empty: every address (with the token when there is one). Set: a peer that
is not loopback and in none of them gets 403 `forbidden_address`, whatever it sends. A loopback
peer is this host and always passes it (the token rule still applies to what it relays). The
engine reads it from its environment at the start (a bad value stops the start with one line),
and PATCH /v1/engine/settings changes it live.

Standard library only.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import re
import secrets
import tempfile
import threading
import time

from server.api.auth import LOOPBACK

ALLOW_ENV = "QSE_ALLOW_FROM"
#: how often the token file is looked at again (a stat), at most
RECHECK_S = 1.0
#: paths a caller passes without the token (the allow-from list still applies)
OPEN = ("/health", "/healthz", "/v1/health", "/readyz", "/metrics/up")
TOKEN_LINE = re.compile(r"^sha256:([0-9a-f]{64})$")
TOKEN_BYTES = 32


def parse_allow(text: str | None) -> list:
    """The networks of an allow-from value ([] for an empty one); ValueError names a bad item."""
    out = []
    for item in (text or "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            out.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            raise ValueError(f"{item!r} is not an address or a CIDR") from None
    return out


def _address(peer: str):
    try:
        ip = ipaddress.ip_address((peer or "").split("%")[0])
    except ValueError:
        return None
    if ip.version == 6 and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def allowed(peer: str, networks: list) -> bool:
    """`peer` passes the allow-from list: loopback always, an empty list every address."""
    if not networks or peer in LOOPBACK:
        return True
    ip = _address(peer)
    if ip is None:
        return False
    if ip.is_loopback:
        return True
    return any(ip.version == n.version and ip in n for n in networks)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def rotate(path: str) -> tuple[str, bool]:
    """A new token: its hash written to `path` (600, atomically), the token returned once.
    (the token, whether one was there before)."""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    before = os.path.exists(path)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".http-token-", dir=os.path.dirname(path))
    with os.fdopen(fd, "w") as fh:
        fh.write(f"sha256:{token_hash(token)}\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return token, before


class Gate:
    """The token file and the allow-from list of one server. `check` answers each request."""

    def __init__(self, token_file: str | None, allow: str = "", *, clock=time.monotonic):
        self.token_file = token_file
        self.clock = clock
        self._lock = threading.Lock()
        self._seen = None                  # the token file's (inode, size, mtime_ns), or "none"
        self._hash: str | None = None      # its sha256; "" for a file that does not read as one
        self._next = 0.0
        self.allow_text = ""
        self.networks: list = []
        self.set_allow(allow)

    @classmethod
    def from_env(cls, root: str, env=None) -> "Gate":
        """The server's gate: run/http-token under the tree `root`, QSE_ALLOW_FROM from the
        environment. A bad QSE_ALLOW_FROM stops the start (SystemExit, one line)."""
        env = os.environ if env is None else env
        try:
            return cls(os.path.join(root, "run", "http-token"), env.get(ALLOW_ENV, ""))
        except ValueError as exc:
            raise SystemExit(f"[access] {ALLOW_ENV}: {exc}; fix it in the overlay (tandem remote settings) "
                             f"or run/local.env") from None

    def set_allow(self, text: str | None) -> None:
        """A new allow-from list, live (ValueError: nothing changed)."""
        nets = parse_allow(text)
        with self._lock:
            self.allow_text = ",".join(x.strip() for x in (text or "").split(",") if x.strip())
            self.networks = nets

    def required(self) -> str | None:
        """The token's sha256 while a token file exists ("" for one that does not read), else None."""
        now = self.clock()
        with self._lock:
            if now < self._next:
                return self._hash
            self._next = now + RECHECK_S
            try:
                st = os.stat(self.token_file) if self.token_file else None
            except OSError:
                st = None
            seen = "none" if st is None else (st.st_ino, st.st_size, st.st_mtime_ns)
            if seen != self._seen:
                self._seen = seen
                if st is None:
                    self._hash = None
                else:
                    try:
                        with open(self.token_file, encoding="ascii", errors="replace") as fh:
                            m = TOKEN_LINE.match(fh.read().strip())
                        self._hash = m.group(1) if m else ""
                    except OSError:
                        self._hash = ""
            return self._hash

    def active(self) -> bool:
        """A token file or an allow-from list is in force (the start says so in one line)."""
        return self.required() is not None or bool(self.networks)

    def describe(self) -> str:
        h = self.required()
        tok = "off (no run/http-token)" if h is None else ("on" if h else "on, but the file does not read: "
                                                           "only trusted-local callers pass")
        return f"HTTP token: {tok}, allow-from: {self.allow_text or 'every address'}"

    def token_ok(self, headers, auth) -> bool:
        """The request carries the engine's HTTP token (a token file exists and reads)."""
        want = self.required()
        bearer = auth.bearer(headers)
        return bool(want) and bool(bearer) and hmac.compare_digest(token_hash(bearer), want)

    def check(self, peer: str, headers, method: str, path: str, auth):
        """None: pass. "open": pass, but the caller holds no token where one is needed (an open
        path answers it the short way). Else (status, type, message): the answer instead."""
        if not allowed(peer, self.networks):
            return 403, "forbidden_address", ("this engine does not take requests from this address "
                                              "(its allow-from list, QSE_ALLOW_FROM)")
        if auth.trusted_local(peer, headers):
            return None
        if self.required() is None:
            return None
        if self.token_ok(headers, auth) or auth.admin_any(headers):
            return None
        if method == "OPTIONS" or (method in ("GET", "HEAD") and path.rstrip("/") in OPEN):
            return "open"
        return 401, "unauthorized", ("this engine needs its bearer token from this address "
                                     "(Authorization: Bearer <token>; tandem remote token rotate makes one)")
