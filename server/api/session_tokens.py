"""Session addresses (SRV-765; ENG-413 WP5b): `/s/<token>` in front of a path, and the registry
of the sessions' tokens.

The runtime notes give the model its session's address (`http://host:8000/s/<token>`), so a call
from the model's own script (the library, Laya, its memory) names the session it belongs to. The
engine strips the prefix from every request before routing (`strip`), answers a session address of
any other route 404, and hands the route the token and the session the registry names. It keeps the
registry (`SessionTokens`, a 0600 file in the extension's directory, `/v1/cache/stats`
`session_tokens`); the extension's router issues the tokens through it (engine-ext-v1 3.1
`state_keys` `session_tokens`). Copies of TandemApp's tandemapp/session_tokens.py names, held to it
by tests/test_api_import_guard.py; `with_token` and `Aliases` (the router's) stay TandemApp's.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from collections import OrderedDict


TTL_S = 48 * 3600.0
MAX_SESSIONS = 4096
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8,32}$")
PREFIX = "/s/"


class SessionTokens:
    def __init__(self, path: str | None = None, clock=time.time):
        self.path, self.clock = path, clock
        self.lock = threading.Lock()
        self.by_key: OrderedDict[str, dict] = OrderedDict()
        self.by_token: dict[str, str] = {}
        self.stats = {"issued": 0, "calls": 0, "unknown": 0, "expired": 0}
        self._dirty_at = 0.0
        if path:
            try:
                with open(path) as f:
                    for key, e in json.load(f).get("sessions", {}).items():
                        if TOKEN_RE.match(e.get("token", "")):
                            self.by_key[key] = {"token": e["token"], "used": float(e["used"]),
                                                "calls": dict(e.get("calls") or {})}
                            self.by_token[e["token"]] = key
            except (OSError, ValueError, AttributeError):
                pass
            self._sweep()

    # -------------------------------------------------------------- the registry
    def _sweep(self) -> None:
        now = self.clock()
        for key in [k for k, e in self.by_key.items() if now - e["used"] > TTL_S]:
            self.by_token.pop(self.by_key.pop(key)["token"], None)
            self.stats["expired"] += 1
        while len(self.by_key) > MAX_SESSIONS:
            key, e = self.by_key.popitem(last=False)
            self.by_token.pop(e["token"], None)

    def _save(self, force: bool = False) -> None:
        if not self.path:
            return
        now = self.clock()
        if not force and now - self._dirty_at < 30.0:
            return
        self._dirty_at = now
        tmp = self.path + ".tmp"
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump({"sessions": dict(self.by_key)}, f)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def token_for(self, key: str) -> str:
        """The session's token, issued at its first request."""
        with self.lock:
            e = self.by_key.get(key)
            new = e is None
            if new:
                tok = secrets.token_urlsafe(9)
                while tok in self.by_token:
                    tok = secrets.token_urlsafe(9)
                e = self.by_key[key] = {"token": tok, "used": self.clock(), "calls": {}}
                self.by_token[tok] = key
                self.stats["issued"] += 1
                self._sweep()
            e["used"] = self.clock()
            self.by_key.move_to_end(key)
            self._save(force=new)
            return e["token"]

    def key_for(self, token: str | None) -> str | None:
        with self.lock:
            key = self.by_token.get(token or "")
            if key is not None and self.clock() - self.by_key[key]["used"] > TTL_S:
                return None
            return key

    def note(self, key: str | None, kind: str) -> None:
        """One out-of-band call of the session (`library`, `laya`)."""
        with self.lock:
            if key is None or key not in self.by_key:
                self.stats["unknown"] += 1
                return
            e = self.by_key[key]
            e["calls"][kind] = e["calls"].get(kind, 0) + 1
            e["used"] = self.clock()
            self.stats["calls"] += 1
            self._save()

    def calls(self, key: str) -> dict:
        with self.lock:
            e = self.by_key.get(key)
            return dict(e["calls"]) if e else {}

    def report(self) -> dict:
        with self.lock:
            return {"sessions": len(self.by_key), **self.stats}

    def close(self) -> None:
        with self.lock:
            self._save(force=True)


def strip(path: str) -> tuple[str, str | None]:
    """`/s/<token>/rest` -> (`/rest`, token); any other path as it is, None."""
    if not path.startswith(PREFIX):
        return path, None
    rest = path[len(PREFIX):]
    tok, sep, tail = rest.partition("/")
    if not TOKEN_RE.match(tok):
        return path, None
    return "/" + tail if sep else "/", tok
