"""Who may read what: the OpenAI API stays open; the rest is open too until an admin password is set.

THE ADMIN PASSWORD (AU1, 2026-10-06, a design decision: no password
prompt anywhere in the UI until a password is set). The one switch is the admin
token, `QSE_ADMIN_TOKEN` (`TANDEM_ADMIN_TOKEN` for the standalone proxy), from secrets.env:

    not set (the default)   every route below answers anyone: the dashboard's reads, the conductor's
                            writes, the memory (notes, transcripts, the disk tier), the deciders,
                            the cache routes and the full /health. The dashboard asks for nothing.
                            Only /metrics keeps its own token when QSE_METRICS_TOKEN is set (the
                            scrapers'). The server is then meant for a trusted network only.
    set                     the table below, unchanged: the admin token (bearer) or a dashboard
                            session, or trusted-local.

`Auth.password_set` is that switch; nothing else turns the admin rule on.

your-host.example proxies every path to this server, and the reverse proxy reaches it through an ssh tunnel,
so a request from any device on the LAN arrives from 127.0.0.1 -- the source address cannot tell
the box's own tools from anyone else. So the mechanism is a bearer token, with one narrow
exemption, and a session cookie so the dashboard's EventSource needs no token in its URL:

    route                                   who
    POST /v1/chat/completions, /v1/completions, GET /v1/models      anyone (per-key API auth is)
    GET /health                             anyone gets {"status"}; the full body needs a token or trusted-local
    GET /v1/library, /v1/library/*          anyone, as the chat API (R1: the package library for the
                                            model's own scripts, rate-limited; server/library_api.py)
    GET /metrics                            the metrics token, the admin token/session, or trusted-local
    GET /v1/cache/stats                     the admin token/session, or trusted-local
    POST /v1/cache/peek, /v1/cache/prewarm  the same (`cache_read`; SRV-349: where a chat's rows
                                            are, and the NVMe read back while the engine is idle)
    POST /v1/cache/clear                    the admin BEARER token (never the cookie), or trusted-local
    POST /v1/cache/release                  the same (SRV-483: chatd asks for room for a sandbox)
    PUT/POST /v1/conductor/* (writes)       the admin token/session, or trusted-local; reads follow
                                            the dashboard rule below
    POST /v1/systemone, /v1/predict         the admin token/session, or trusted-local (decision
                                            models, SRV-131: LAN only, behind the bearer token)
    /v1/setup/* (GET and POST)              the admin token/session, or trusted-local (the setup
                                            guide: its reads too, since apply and reset follow
                                            them; server/setup_api.py)
    POST /v1/laya/ask, GET /v1/laya         anyone, as the chat API (L1: Laya for the model's own
                                            scripts, typed answers on a short text, rate-limited;
                                            server/laya_api.py); X1: POST /v1/laya/drive/end and
                                            GET /v1/laya/kit/<file> (the playtest kit) the same
    GET /dashboard, /dashboard/*            anyone: the static shell, no data in it
    POST /v1/fleet/pair*, DELETE /v1/fleet/nodes/{id}
                                            (`fleet_trust`, SEC-4: who controls a node) the admin
                                            token/session, or trusted-local; with NO password set,
                                            trusted-local only: pairing never opens to the network
                                            by default
    /v1/dashboard/*                         login off (the default): anyone; login on: the admin token or
                                            session only (POST /v1/dashboard/session is the login)

THE DASHBOARD LOGIN (`--dashboard-login on|off`, from `QSE_DASHBOARD_LOGIN`, off by default). The
dashboard only reads -- counts, timings, the log (no prompt or answer text unless --log-content),
the settings with their secrets redacted -- and it is meant for a local network, so by default its
read API answers without a token, even with no secrets.env, and the page opens without a sign-in
screen. `GET /v1/dashboard/session` then says `login: false`, so the page knows there is nothing
to sign in to. With the login on, everything below applies unchanged. Either way the login decides
only `/v1/dashboard/*` (which includes `/v1/dashboard/metrics`, the /metrics page for the
Performance view): /metrics itself, the cache routes and the full /health keep their own rules.

TRUSTED-LOCAL (`--trust-loopback`, on by default): the TCP peer is loopback AND the request has
neither `X-Forwarded-For` nor `X-Real-IP`. The reverse proxy (nginx) sets both on everything it proxies, so
tunnelled traffic never qualifies, and the watchdog, row3, gate.sh and soak on the box keep working
with no token.

NO PASSWORD, OPEN (AU1): with no admin token configured the admin routes answer everyone (until
2026-10-06 they answered 404, fail closed, and the dashboard asked for the token). Tokens come from `~/.qwen38-spark-engine/secrets.env` (made by
`ops/make-secrets.sh`, mode 600, sourced by `ops/start.sh`), are at least 32 characters, are
compared with `hmac.compare_digest`, and are never printed, logged or returned.

THE SESSION: `POST /v1/dashboard/session {"token"}` sets `qse_dash=<expiry>.<nonce>.<HMAC-SHA256>` (a
key derived from the admin token, so rotating the token ends every session; the nonce makes each
login its own session, so signing out ends that one only), HttpOnly, SameSite=Strict,
Path=/, 12 hours, Secure behind https. Five failed logins a minute from one address and the
sixth gets a 429: the address is `login_key`'s, not the raw `X-Real-IP` -- the header is the
client's to write, so it counts only from a loopback peer or from a proxy this box trusts
(`TANDEM_TRUSTED_PROXY`), and otherwise the peer address is used. The dashboard API only
reads, and `/v1/cache/clear` does not take the cookie. SameSite=Strict is NOT the whole CSRF
story for `/v1/setup/*`, which writes
(apply, reset) and takes the cookie: an open box, or loopback through a tunnel, needs no cookie at
all, and a text/plain POST from any page needs no preflight. So every /v1/setup POST must also be
declared JSON, carry no `Sec-Fetch-Site: cross-site`, and name this server's host in its `Origin`
when it sends one (server/setup_api.py same_origin_json).

THE CONTROLLER'S PASSWORD (tandem-app only; proxy/adminpw.py, set in App settings, 6.3b). A
second admin secret beside the token, a salted scrypt hash in `admin.json` beside the config
database. It counts for `password_set`, signs in where the token does (`sign_in`: the token first,
then the hash; the per-address limit above, plus the file's own cap over every address), and is
never a bearer: tools keep the token. While it is set the session key is made from its hash and the
token's key together, so setting, changing or removing it ends every session signed in before
(the page that changed it gets a new cookie); with no password the key is the token's alone, as
before, and the token's sessions keep working.
"""

from __future__ import annotations

import collections
import hashlib
import hmac
import os
import secrets
import threading
import time

COOKIE = "qse_dash"
SESSION_S = int(os.environ.get("QSE_SESSION_S", 400 * 86400))  # a LAN-only dashboard: one login lasts 400 days (the cookie cap browsers keep)
MIN_TOKEN = 32
LOGIN_ENV = "QSE_DASHBOARD_LOGIN"
LOGIN_DEFAULT = "off"
LOGIN_FAILS = 5
LOGIN_WINDOW_S = 60.0
LOGIN_MAX_KEYS = 10_000               # a memory bound on `_fails`, not a ban reset
LOOPBACK = ("127.0.0.1", "::1", "::ffff:127.0.0.1")


def loopback_host(host: str) -> bool:
    """A Host header that names this machine's loopback: localhost, *.localhost, 127.x, ::1
    (with or without a port). An empty one (HTTP/1.0 tools) counts."""
    h = (host or "").strip().lower()
    if not h:
        return True
    if h.startswith("["):
        h = h[1:h.find("]")] if "]" in h else h
    elif h.count(":") == 1:
        h = h.split(":", 1)[0]
    if h == "localhost" or h.endswith(".localhost") or h == "::1":
        return True
    return h.startswith("127.") and all(p.isdigit() for p in h.split(".")) and h.count(".") == 3


def _eq(a: str | None, b: str | None) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(a.encode(), b.encode())


class Auth:
    def __init__(self, admin_token: str | None = None, metrics_token: str | None = None, *,
                 trust_loopback: bool = True, login: bool = True, clock=time.time,
                 trusted_proxies=(), password=None, local_host_only: bool = False):
        for name, tok in (("QSE_ADMIN_TOKEN", admin_token), ("QSE_METRICS_TOKEN", metrics_token)):
            if tok is not None and len(tok) < MIN_TOKEN:
                raise SystemExit(f"[auth] {name} is shorter than {MIN_TOKEN} characters; make a "
                                 f"new one with ops/make-secrets.sh --rotate")
        self.admin = admin_token or None
        self.metrics = metrics_token or None
        self.trust_loopback = bool(trust_loopback)
        # tandem-app (6.3b): trusted-local also needs a loopback Host, so a page whose name was
        # rebound to 127.0.0.1 (DNS rebinding) is not "this machine"
        self.local_host_only = bool(local_host_only)
        # the object on its own is the strict policy; the server's default (off) comes from
        # `--dashboard-login`, and from_env reads the same variable with the same default
        self.login = bool(login)
        self.clock = clock
        self._token_key = (hmac.new(self.admin.encode(), b"qse-dash-session-v1",
                                    hashlib.sha256).digest() if self.admin else None)
        self.trusted_proxies = frozenset(p.strip() for p in trusted_proxies if p and p.strip())
        # THE CONTROLLER'S PASSWORD (proxy/adminpw.py, set in App settings; the engine has none):
        # a second secret beside the token, for sign-in only
        self.password = password
        self._fails: dict = collections.defaultdict(collections.deque)
        self._revoked: dict[str, int] = {}          # logged-out cookie values, until they expire
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls, env=None, **kw) -> "Auth":
        env = os.environ if env is None else env
        kw.setdefault("login", env.get(LOGIN_ENV, LOGIN_DEFAULT) == "on")
        kw.setdefault("trusted_proxies",
                      (env.get("TANDEM_TRUSTED_PROXY") or "").split(","))
        return cls(env.get("QSE_ADMIN_TOKEN") or None, env.get("QSE_METRICS_TOKEN") or None, **kw)

    @property
    def password_set(self) -> bool:
        """THE switch (AU1): an admin password (the admin token, or the controller's password from
        App settings) is configured. False: every admin route is open and the dashboard asks for
        nothing."""
        return bool(self.admin) or self.has_password

    @property
    def has_password(self) -> bool:
        return self.password is not None and self.password.is_set

    @property
    def _key(self) -> bytes | None:
        """The session cookies' key. The token's alone while no password is set (the sessions of
        before keep working); with one, made from both, so a new password, or a new token, ends
        every session made before it."""
        m = self.password.material() if self.password is not None else None
        if m is None:
            return self._token_key
        return hmac.new(m, b"qse-dash-session-v2" + (self._token_key or b""),
                        hashlib.sha256).digest()

    def describe(self) -> str:
        """The startup line: whether auth is on, never a token."""
        if not self.password_set:
            return (f"admin password: not set (every admin route and the dashboard are open; keep "
                    f"this server on a trusted network), "
                    f"metrics token: {'set' if self.metrics else 'not set'}")
        if not self.login:
            dash = "off (its read API needs no token)"
        else:
            dash = "on"
        how = " and ".join(x for x, on in (("token", bool(self.admin)),
                                            ("password file", self.has_password)) if on)
        return (f"admin password: set ({how}), dashboard login: {dash}, "
                f"metrics token: {'set' if self.metrics else 'not set'}, "
                f"trusted loopback: {'on' if self.trust_loopback else 'off'}")

    # ----------------------------------------------------------------- who is asking
    @staticmethod
    def _header(headers, name: str) -> str:
        try:
            return headers.get(name) or ""
        except Exception:                                          # noqa: BLE001
            return ""

    def trusted_local(self, peer: str, headers) -> bool:
        if not self.trust_loopback or peer not in LOOPBACK:
            return False
        if self.local_host_only and not loopback_host(self._header(headers, "Host")):
            return False
        return not (self._header(headers, "X-Forwarded-For") or self._header(headers, "X-Real-IP"))

    def bearer(self, headers) -> str | None:
        auth = self._header(headers, "Authorization").strip()
        if auth[:7].lower() != "bearer ":
            return None
        return auth[7:].strip() or None

    def admin_bearer(self, headers) -> bool:
        return _eq(self.bearer(headers), self.admin)

    def metrics_bearer(self, headers) -> bool:
        return _eq(self.bearer(headers), self.metrics)

    # ----------------------------------------------------------------- the session cookie
    def make_cookie(self, now: float | None = None) -> tuple[str, int]:
        """`<expiry>.<nonce>.<mac>`. The nonce makes each login its own session: the value
        was the expiry second and its mac alone, so two logins in one second shared a cookie and a
        sign-out revoked both, or a login right after a sign-out was born revoked."""
        exp = int((self.clock() if now is None else now) + SESSION_S)
        nonce = secrets.token_hex(8)
        mac = hmac.new(self._key, f"{exp}.{nonce}".encode(), hashlib.sha256).hexdigest()
        return f"{exp}.{nonce}.{mac}", exp

    def cookie(self, headers) -> str | None:
        val = None
        for part in self._header(headers, "Cookie").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE:
                val = v
        return val or None

    def revoke(self, headers) -> None:
        """Logout: the presented session stops working here too, not only in the browser."""
        exp = self.session(headers)
        if exp is not None:
            now = self.clock()
            with self._lock:
                self._revoked = {v: e for v, e in self._revoked.items() if e > now}
                self._revoked[self.cookie(headers)] = exp

    def session(self, headers, now: float | None = None) -> int | None:
        """The session's expiry if the request carries a valid, unexpired one, else None."""
        if self._key is None:
            return None
        val = self.cookie(headers)
        if not val or val in self._revoked:
            return None
        parts = val.split(".")
        if len(parts) != 3 or not parts[0].isdigit():
            return None
        exp_s, nonce, mac = parts
        want = hmac.new(self._key, f"{exp_s}.{nonce}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(want, mac):
            return None
        exp = int(exp_s)
        return exp if exp > (self.clock() if now is None else now) else None

    def admin_any(self, headers) -> bool:
        return self.admin_bearer(headers) or self.session(headers) is not None

    # ----------------------------------------------------------------- signing in
    def sign_in(self, value, who: str) -> str:
        """`ok`, `wrong` or `held` (429: too many failures from `who`, or from anywhere for the
        password, or its checks all busy) for a sign-in or a change's `current`. The token is
        compared first, in constant time; then the password (proxy/adminpw.py). A failure counts
        for `who`."""
        if self.login_blocked(who):
            return "held"
        if isinstance(value, str) and _eq(value, self.admin):
            return "ok"
        if self.has_password:
            if self.password.held() or self.password.busy():
                return "held"
            if self.password.verify(value):
                return "ok"
        self.login_failed(who)
        return "wrong"

    # ----------------------------------------------------------------- login rate limit
    def login_blocked(self, who: str) -> bool:
        now = self.clock()
        with self._lock:
            q = self._fails[who]
            while q and now - q[0] > LOGIN_WINDOW_S:
                q.popleft()
            return len(q) >= LOGIN_FAILS

    def login_key(self, peer: str, headers) -> str:
        """The throttle key for one login attempt -- the one rule BOTH servers run.
        `X-Real-IP` is the client's to write, so it counts ONLY from a proxy this box
        trusts: loopback by default (the documented single-box shape -- an nginx on this
        box fronts the server and every real client shares the 127.0.0.1 peer; keying on
        the peer there lets one user's five wrong passwords lock out every user), plus
        TANDEM_TRUSTED_PROXY naming a proxy's own address for the LAN shape (docs/proxy.md).
        From any other peer the header is ignored and the peer itself is the key.
        Never both at once: a spoofable value must not merge with real IPs. A trusted
        proxy that sends no header leaves the attempt keyed on the peer."""
        if peer in self.trusted_proxies or peer in LOOPBACK:
            v = (headers.get("X-Real-IP") or "").strip() if headers is not None else ""
            if v:
                return v
        return peer

    def login_failed(self, who: str) -> None:
        now = self.clock()
        with self._lock:
            self._fails[who].append(now)
            if len(self._fails) <= LOGIN_MAX_KEYS:
                return
            # the old `self._fails.clear()` handed an attacker a free pass: a flood of
            # one-shot keys (one per rotating peer) wiped every ACTIVE ban at the moment
            # the table overflowed. Stale keys go first -- the window prunes them away --
            # then the OLDEST-SEEN keys, one bucket at a time; the cap is a memory bound,
            # never a reset of who is banned.
            for key, q in list(self._fails.items()):
                while q and now - q[0] > LOGIN_WINDOW_S:
                    q.popleft()
                if not q:
                    del self._fails[key]
            if len(self._fails) > LOGIN_MAX_KEYS:
                # one sweep per 10 % of the table, not one per login: at the cap every
                # further failed login would otherwise rescan and sort all 10k keys
                # under the lock (2 ms each, measured)
                banned = {k for k, q in self._fails.items() if len(q) >= LOGIN_FAILS}
                oldest = sorted(self._fails, key=lambda k: self._fails[k][0])
                target = LOGIN_MAX_KEYS - LOGIN_MAX_KEYS // 10
                excess = len(self._fails) - target
                drop = [k for k in oldest if k not in banned][:excess]   # spare the active
                seen = set(drop)   # a set: with most keys banned, scanning the list per
                drop += [k for k in oldest if k not in seen][:excess - len(drop)]  # key is
                for key in drop:                                         # millions of scans
                    del self._fails[key]

    # ----------------------------------------------------------------- the policy
    def decide(self, route: str, peer: str, headers) -> str:
        """`ok` or `unauthorized` (401) for one guarded route kind (`absent`, the 404 of the old
        fail-closed rule, is no longer returned; the callers still handle it):
        `metrics`, `cache_read`, `cache_clear`, `conductor_write`, `decider`, `dashboard`,
        `health_full`, `setup`, `fleet_trust`. With no admin password set (AU1) every kind is `ok`,
        but /metrics with a metrics token set still wants that token (or trusted-local), and
        `fleet_trust` wants trusted-local."""
        local = self.trusted_local(peer, headers)
        if not self.password_set:
            if route == "metrics" and self.metrics and not local:
                return "ok" if self.metrics_bearer(headers) else "unauthorized"
            if route == "fleet_trust" and not local:
                return "unauthorized"
            return "ok"
        if route == "dashboard":
            if not self.login:
                return "ok"
            return "ok" if self.admin_any(headers) else "unauthorized"
        if local:
            return "ok"
        if route == "metrics":
            return ("ok" if self.metrics_bearer(headers) or self.admin_any(headers)
                    else "unauthorized")
        if route == "health_full":
            return ("ok" if self.metrics_bearer(headers) or self.admin_any(headers)
                    else "unauthorized")
        if route in ("cache_read", "conductor_write", "decider", "setup", "fleet_trust"):
            return "ok" if self.admin_any(headers) else "unauthorized"
        if route == "cache_clear":
            return "ok" if self.admin_bearer(headers) else "unauthorized"
        return "unauthorized"


# ---------------------------------------------------------------------- a write from the page itself
# Copied from server/setup_api.py (SRV-747: TandemApp does not import the engine; the engine keeps
# its own for the Setup Guide's routes, and tests/test_engine_api_contract.py holds the two equal).

def same_origin_json(headers) -> tuple[int, str, str] | None:
    """None for a POST the page itself sent, else (status, code, message): the body must be
    declared JSON (a form or text/plain POST needs no CORS preflight), a browser's
    `Sec-Fetch-Site: cross-site` is refused, and an `Origin` must name this server's host (the
    `Host` the browser sent, which nginx passes on as $host, or `X-Forwarded-Host`). A tool
    without a browser sends no Origin and no Sec-Fetch-Site and passes on its JSON alone."""
    ctype = (headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if ctype != "application/json":
        return 415, "json_only", "the setup routes take a JSON body (Content-Type: application/json)"
    return same_origin(headers)


def same_origin(headers) -> tuple[int, str, str] | None:
    """same_origin_json without the body's type: for a write whose body may be empty (a DELETE)
    or that a tool sends without one (proxy/app.py, every /v1/fleet/* write)."""
    from urllib.parse import urlsplit
    if (headers.get("Sec-Fetch-Site") or "").strip().lower() == "cross-site":
        return 403, "cross_site", "a request from another site cannot change the setup"
    origin = headers.get("Origin")
    if origin is not None:
        o = urlsplit(origin.strip()) if origin.strip() != "null" else None
        hosts = {x.strip().lower() for x in (headers.get("Host"), headers.get("X-Forwarded-Host"))
                 if x}
        if o is None or not o.netloc or o.netloc.lower() not in hosts:
            return 403, "cross_origin", "a request from another origin cannot change the setup"
    return None
