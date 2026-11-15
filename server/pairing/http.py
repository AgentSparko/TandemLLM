"""The pairing routes on the engine's port (engine API 1.9, group 9; engine-pair-v1), in setup mode
and while serving, and the start and stop of pairing and the advertisement in the engine process.

    GET  /v1/engine/pair            the window's state: open, expires_in, tries_left, fingerprint
    POST /v1/engine/pair/1          step 1 of the exchange
    POST /v1/engine/pair/2          step 2: the key installed, the answer sealed
    POST /v1/engine/pair/abandon    a run TandemApp gave up
    POST /v1/engine/pair/open       `tandem pair`: trusted-local with the window key only
    POST /v1/engine/pair/close      the same

ACCESS. The first four take a caller from the network without the HTTP token while a window is open
(server/app.py's gate asks `lan_open`); with no window open they follow the token rule like every
route. The allow-from list applies to all of them. A caller that is not trusted-local gets at most
RATE_PEER requests a minute from its address, and all of them together RATE_ALL, then 429 with
Retry-After. `open` and `close` answer only a trusted-local caller (server/api/auth.py) that sends
the window key (`X-Tandem-Pair-Key`, run/remote/pair-local.key), else 403.
"""

from __future__ import annotations

import collections
import os
import threading
import time

from server.pairing.window import PAIR, PairError, Service

PREFIX = "/v1/engine/pair"
KEY_HEADER = "X-Tandem-Pair-Key"
RATE_PEER = 20
RATE_ALL = 120
RATE_WINDOW_S = 60.0
NO_STORE = (("Cache-Control", "no-store"),)
LAN = {("GET", ""): "status", ("POST", "1"): "step1", ("POST", "2"): "step2", ("POST", "abandon"): "abandon"}
LOCAL = {("POST", "open"): "open", ("POST", "close"): "close"}


class Limit:
    """At most `per_peer` requests a window from one address and `total` from all of them."""

    def __init__(self, per_peer: int = RATE_PEER, total: int = RATE_ALL, window_s: float = RATE_WINDOW_S,
                 clock=time.monotonic):
        self.per_peer, self.total, self.window_s, self.clock = per_peer, total, window_s, clock
        self._peers: dict[str, collections.deque] = {}
        self._all: collections.deque = collections.deque()
        self._lock = threading.Lock()

    def take(self, peer: str) -> float:
        """0 when the request may go; else the seconds until it may."""
        now = self.clock()
        with self._lock:
            for q in [self._all] + list(self._peers.values()):
                while q and q[0] <= now - self.window_s:
                    q.popleft()
            self._peers = {k: v for k, v in self._peers.items() if v}
            q = self._peers.setdefault(peer, collections.deque())
            if len(q) >= self.per_peer or len(self._all) >= self.total:
                first = min(x for x in (q[0] if q else None, self._all[0] if self._all else None) if x is not None)
                return max(1.0, first + self.window_s - now)
            q.append(now)
            self._all.append(now)
            return 0.0


LIMIT = Limit()


def split(path: str) -> str | None:
    """The part after /v1/engine/pair ("" for the window itself), None for another path."""
    p = path.rstrip("/")
    if p == PREFIX:
        return ""
    if p.startswith(PREFIX + "/") and "/" not in p[len(PREFIX) + 1:]:
        return p[len(PREFIX) + 1:]
    return None


def route(method: str, rest: str) -> str | None:
    return LAN.get((method, rest)) or LOCAL.get((method, rest))


def lan_open(state, method: str, path: str) -> bool:
    """A network caller without the token passes the gate: a pairing route while a window is open."""
    rest = split(path.split("?")[0])
    svc = state.get("pairing") if state is not None else None
    return rest is not None and (method, rest) in LAN and svc is not None and svc.lan_open()


def serve(handler, state, method: str, path: str, body) -> None:
    """One /v1/engine/pair* request on the engine's HTTP handler."""
    rest = split(path)
    name = route(method, rest) if rest is not None else None
    if name is None:
        return handler._json(404, {"error": {"type": "not_found", "message": f"no route {method} {path}"}})
    svc: Service | None = state.get("pairing")
    if svc is None:
        return handler._json(404, {"error": {"type": "no_pairing", "message": "this engine process serves no "
                                             "pairing"}}, extra_headers=NO_STORE)
    peer = handler._peer()
    local = handler._auth_policy().trusted_local(peer, handler.headers)
    if not local:
        wait = LIMIT.take(peer)
        if wait:
            return handler._json(429, {"error": {"type": "rate_limited", "message": "too many pairing requests "
                                                 "from this address; wait a minute"}},
                                 extra_headers=NO_STORE + (("Retry-After", str(int(wait + 0.999))),))
    if name in ("open", "close"):
        if not local:
            return handler._json(403, {"error": {"type": "local_only", "message": "a pairing window is opened "
                                                 "on the engine host: tandem pair"}}, extra_headers=NO_STORE)
        if not svc.local_ok(handler.headers.get(KEY_HEADER)):
            return handler._json(403, {"error": {"type": "window_key", "message": "the window key is missing or "
                                                 "wrong (run/remote/pair-local.key; tandem pair sends it)"}},
                                 extra_headers=NO_STORE)
    try:
        if name == "status":
            out = svc.status()
        elif name == "open":
            ttl = (body or {}).get("ttl_s") if isinstance(body, dict) else None
            out = {"pair": PAIR, **svc.open(**({"ttl_s": float(ttl)} if isinstance(ttl, (int, float))
                                                and 30 <= ttl <= 300 else {}))}
        elif name == "close":
            out = {"pair": PAIR, **svc.close()}
        else:
            out = getattr(svc, name)(body, peer)
    except PairError as exc:
        return handler._json(exc.status, exc.body(), extra_headers=NO_STORE)
    return handler._json(200, out, extra_headers=NO_STORE)


# ------------------------------------------------------------------------------ the engine process
def start(state, port: int, host: str, mode: str, root: str | None = None, env=None) -> None:
    """Pairing and the advertisement for this process (server/app.py, setup mode and serving).
    Neither stops the start: a failure is one line. An engine on loopback prints nothing here, so
    its log is the one it had before pairing existed."""
    from server.pairing import mdns
    from server.remote.paths import Tree
    env = os.environ if env is None else env
    try:
        state["pairing"] = Service(Tree(root), port=port, host=host, mode=mode, env=env)
    except Exception as exc:                                      # noqa: BLE001
        state["pairing"] = None
        print(f"[pair] pairing is off in this process ({type(exc).__name__}: {exc})", flush=True)
    adv, why = mdns.start_from_env(port=port, host=host, root=root, env=env)
    state["advertiser"] = adv
    if why:
        print(f"[pair] advertisement: {why}", flush=True)


def stop(state) -> None:
    adv = state.get("advertiser") if state is not None else None
    if adv is not None:
        state["advertiser"] = None
        try:
            adv.stop()
        except Exception:                                         # noqa: BLE001
            pass
