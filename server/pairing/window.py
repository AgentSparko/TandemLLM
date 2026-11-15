"""The pairing window (engine-pair-v1): one code, its limits, and the exchange that installs
TandemApp's SSH key on this host.

    svc = Service(tree, port=8000, host="0.0.0.0", mode="setup")
    svc.open()                      `tandem pair`: a new window, its code (shown on this host only)
    svc.step1(body, peer)           {pA, public_key, key_id, name?} -> {session, pB, cB, host_key, ...}
    svc.step2(body, peer)           {session, cA} -> {sealed}: the key installed, the token issued
    svc.abandon(body, peer)         a run TandemApp gave up (its check of cB failed)

THE LIMITS (TandemApp's proxy/pairing.py): a 6-digit code, 5 minutes, 5 runs, single use. Each
step 1 spends a try; a run whose confirmation fails is a failed try; with no try left the code is
spent. The first run that confirms closes the window. A run holds its session for 60 s.

WHAT A RUN DOES once TandemApp's confirmation checks: TandemApp's ed25519 key goes into the
installing user's ~/.ssh/authorized_keys with `tandem remote` as its forced command
(server/remote/keys.py `install`), a new HTTP token is issued for its key id (server/api/access.py
`issue`: only the hash is kept), and the answer (SSH user and port, the host keys, the HTTP port and
token) goes back sealed with the run's key (server/pairing/spake2.py). Nothing is installed before
the confirmation, and nothing at all from a wrong code.

THE JOURNAL. Every step is a line of run/remote/journal.jsonl (`via` http or local): the event, the
peer's address, the key id and the key's fingerprint, the error code. Never the code, the token, a
key's bytes or a protocol message.

`tandem pair` reaches `open` over loopback with the window key, run/remote/pair-local.key (600,
new at every start): only the user who installed the engine (and root) can read it.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import threading
import time

from server.api import access
from server.pairing import hostkey, spake2
from server.remote import journal, keys
from server.remote.grammar import KEY_ID_RE
from server.remote.paths import Tree

PAIR = "engine-pair-v1"
CODE_LEN = 6
TTL_S = 300.0
TRIES = 5
SESSION_S = 60.0
NAME_MAX = 80


class PairError(Exception):
    """A step that did not happen: `kind` is the contract's error type, `status` its HTTP status."""

    def __init__(self, kind: str, message: str, status: int = 400, **extra):
        super().__init__(message)
        self.kind, self.message, self.status, self.extra = kind, message, status, extra

    def body(self) -> dict:
        return {"error": {"type": self.kind, "message": self.message, **self.extra}}


def new_code(rng=secrets, n: int = CODE_LEN) -> str:
    return f"{rng.randbelow(10 ** n):0{n}d}"


def _hex_or_none(v) -> str:
    return v if isinstance(v, str) else ""


class Window:
    """One code: `state` is open, paired, expired, spent (the tries are used up) or closed."""

    def __init__(self, host_key: dict, *, code: str | None = None, ttl_s: float = TTL_S, tries: int = TRIES,
                 clock=time.time, rng=secrets):
        self.rng = rng
        self.code = code or new_code(rng)
        self.host_key = host_key
        self.clock = clock
        self.opened = clock()
        self.expires_at = self.opened + ttl_s
        self.tries = tries
        self.tries_left = tries
        self.state = "open"
        self.failed = 0
        self._sessions: dict[str, dict] = {}

    def _expire(self) -> bool:
        """True when the window expired just now."""
        if self.state == "open" and self.clock() >= self.expires_at:
            self.state = "expired"
            self._sessions.clear()
            return True
        return False

    def view(self) -> dict:
        """What anybody may see: no code."""
        self._expire()
        return {"state": self.state, "expires_in": max(0, round(self.expires_at - self.clock())),
                "tries_left": self.tries_left, "fingerprint": self.host_key["fingerprint"]}

    def usable(self) -> None:
        self._expire()
        if self.state == "expired":
            raise PairError("code_spent", "the code has expired: run `tandem pair` on the engine host "
                                          "for a new one", 410)
        if self.state == "spent":
            raise PairError("code_spent", f"{self.tries} wrong codes: this code is spent; run `tandem pair` "
                                          "on the engine host for a new one", 410)
        if self.state != "open":
            raise PairError("code_spent", "this code was used; run `tandem pair` on the engine host for a "
                                          "new one", 410)

    def _spend_if_out(self) -> None:
        if self.tries_left <= 0 and self.state == "open":
            self.state = "spent"
            self._sessions.clear()

    def step1(self, key_fp: str, key_id: str, pa: int, extra: dict) -> dict:
        """One try spent; the engine's message, its confirmation and its host key."""
        self.usable()
        if self.tries_left <= 0:
            self._spend_if_out()
            self.usable()
        self.tries_left -= 1
        now = self.clock()
        self._sessions = {k: v for k, v in self._sessions.items() if v["until"] > now}
        w = spake2.w_of(self.code)
        y = 1 + self.rng.randbelow(spake2.Q - 1)
        pb = spake2.message_b(y, w)
        k = spake2.shared_b(pa, y, w)
        kca, kcb, tt = spake2.keys(spake2.identity_a(key_fp, key_id), spake2.identity_b(self.host_key["fingerprint"]),
                                   pa, pb, k, w)
        sid = self.rng.token_hex(16)
        self._sessions[sid] = {"until": now + SESSION_S, "kca": kca, "tt": tt, **extra}
        return {"session": sid, "pB": format(pb, "x"), "cB": spake2.mac(kcb, tt),
                "host_key": self.host_key["line"], "fingerprint": self.host_key["fingerprint"],
                "tries_left": self.tries_left, "expires_in": max(0, round(self.expires_at - now))}

    def step2(self, sid: str, ca: str) -> dict:
        """The run of `sid` once its confirmation checks (the window is then `confirming` until
        `done` or `undo`); a wrong one is a failed try."""
        s = self._sessions.pop(sid, None)
        if s is None or s["until"] <= self.clock():
            self.usable()
            raise PairError("bad_request", "no such pairing session (one try, 60 s): start again", 400)
        self.usable()
        if not (ca.isascii() and spake2.hmac.compare_digest(ca, spake2.mac(s["kca"], s["tt"]))):
            self.failed += 1
            self._spend_if_out()
            raise PairError("wrong_code", "the code does not match" +
                            (f": {self.tries_left} tries left" if self.state == "open"
                             else f"; {self.tries} wrong codes spent it"), 403)
        self.state = "confirming"
        self._sessions.clear()
        return s

    def done(self) -> None:
        self.state = "paired"

    def undo(self) -> None:
        """The confirmed run could not install: the window stays open with the try spent."""
        self.state = "open"
        self._spend_if_out()

    def abandon(self, sid: str) -> bool:
        if self._sessions.pop(sid, None) is None:
            return False
        self.failed += 1
        self._spend_if_out()
        return True

    def close(self) -> None:
        if self.state == "open":
            self.state = "closed"
            self._sessions.clear()


class Service:
    """The pairing windows of one engine process (setup mode or serving), one at a time."""

    def __init__(self, tree: Tree | None = None, *, port: int, host: str = "127.0.0.1", mode: str = "engine",
                 etc: str = hostkey.ETC_SSH, auth_keys: str | None = None, env=None, clock=time.time,
                 rng=secrets, uid: int | None = None, write_key: bool = True):
        self.tree = tree or Tree()
        self.port, self.host, self.mode = int(port), host, mode
        self.etc, self.auth_keys, self.uid = etc, auth_keys, uid
        self.env = os.environ if env is None else env
        self.clock, self.rng = clock, rng
        self.window: Window | None = None
        self._lock = threading.Lock()
        self.local_key = None
        if write_key:
            self.local_key = self._write_local_key()

    # ------------------------------------------------------------------------------ the window key
    @property
    def local_key_file(self) -> str:
        return os.path.join(self.tree.remote, "pair-local.key")

    def _write_local_key(self) -> str | None:
        key = self.rng.token_hex(32)
        try:
            os.makedirs(self.tree.remote, mode=0o700, exist_ok=True)
            tmp = self.local_key_file + ".tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(key + "\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.local_key_file)
        except OSError as exc:
            print(f"[pair] the window key was not written ({exc.strerror}): tandem pair cannot open a window",
                  flush=True)
            return None
        return key

    def local_ok(self, given: str | None) -> bool:
        return bool(self.local_key) and isinstance(given, str) and given.isascii() and \
            spake2.hmac.compare_digest(given.strip(), self.local_key)

    # ------------------------------------------------------------------------------ the journal
    def _journal(self, event: str, peer: str | None, *, via: str = "http", exit_code: int = 0, **fields) -> None:
        row = {"at": journal.utc(), "verb": f"pair {event}", "args": [], "exit": exit_code, "ms": 0, "via": via}
        if peer:
            row["from"] = str(peer)[:64]
        for k, v in fields.items():
            if v is not None:
                row[k] = v
        journal.write(self.tree.journal, row)

    def _expired_now(self, peer=None) -> None:
        if self.window is not None and self.window._expire():
            self._journal("expired", peer, exit_code=3, error="expired")

    # ------------------------------------------------------------------------------ the local side
    def open(self, ttl_s: float = TTL_S) -> dict:
        """A new window (an open one is closed first): its code and what the user is shown."""
        control = self.tree.control
        if not self.tree.install or not control:
            raise PairError("no_install", "pairing needs an install (install.sh): this checkout has no "
                                          "bin/tandem for the key's forced command", 409)
        try:
            hk = hostkey.preferred(self.etc)
        except hostkey.NoHostKey as exc:
            raise PairError("no_host_key", str(exc), 409) from None
        try:
            keys.check_place(self.auth_keys or keys.default_path(), self.uid)
        except keys.Unsafe as exc:
            raise PairError(exc.code, exc.message, 409) from None
        with self._lock:
            if self.window is not None and self.window.state == "open":
                self.window.close()
                self._journal("closed", None, via="local", reason="replaced")
            self.window = Window(hk, ttl_s=ttl_s, clock=self.clock, rng=self.rng)
            w = self.window
        self._journal("open", None, via="local", fingerprint=hk["fingerprint"])
        addrs = hostkey.bound_addresses(self.host)
        return {"code": w.code, "expires_in": int(ttl_s), "tries": w.tries,
                "fingerprint": hk["fingerprint"], "host_key_type": hostkey.TYPE_NAME.get(hk["type"], hk["type"]),
                "addresses": addrs, "port": self.port, "host": self.host, "mode": self.mode,
                "ssh_user": hostkey.ssh_user(), "ssh_port": hostkey.ssh_port(self.env, self.etc),
                "lan": bool(addrs)}

    def close(self) -> dict:
        with self._lock:
            was = self.window.state if self.window else None
            if self.window is not None:
                self.window.close()
        if was == "open":
            self._journal("closed", None, via="local", reason="closed")
        return {"closed": was == "open"}

    def status(self) -> dict:
        with self._lock:
            self._expired_now()
            if self.window is None:
                return {"pair": PAIR, "open": False, "state": "none"}
            v = self.window.view()
        return {"pair": PAIR, "open": v["state"] == "open", **v}

    def lan_open(self) -> bool:
        """A window is open: the pairing routes take callers without the HTTP token."""
        with self._lock:
            self._expired_now()
            return self.window is not None and self.window.state == "open"

    # ------------------------------------------------------------------------------ the exchange
    def _window(self) -> Window:
        if self.window is None or self.window.state == "closed":
            raise PairError("no_window", "no pairing window is open: run `tandem pair` on the engine host", 404)
        return self.window

    def step1(self, body, peer: str | None = None) -> dict:
        if not isinstance(body, dict):
            raise PairError("bad_request", "the body is {pA, public_key, key_id, name?}")
        key_id = body.get("key_id")
        if not isinstance(key_id, str) or not KEY_ID_RE.match(key_id):
            raise PairError("bad_request", "key_id: 1 to 64 of letters, digits and . _ : - (a letter or digit first)")
        got = hostkey.ed25519_key(body.get("public_key") or "") if isinstance(body.get("public_key"), str) else None
        if got is None:
            raise PairError("bad_request", "public_key: one ssh-ed25519 public key (`ssh-ed25519 AAAA...`)")
        key_fp = hostkey.fingerprint(got[1])
        try:
            pa = spake2.elem(body.get("pA"))
        except spake2.BadElement:
            raise PairError("bad_request", "pA: not a group element") from None
        name = body.get("name")
        name = "".join(c for c in name if c.isprintable())[:NAME_MAX] if isinstance(name, str) else ""
        with self._lock:
            self._expired_now(peer)
            w = self._window()
            try:
                out = w.step1(key_fp, key_id, pa, {"key_id": key_id, "key_fp": key_fp,
                                                     "key_b64": base64.b64encode(got[1]).decode(), "name": name,
                                                     "peer": peer})
            except PairError as exc:
                self._journal("try", peer, exit_code=3, key=key_id, fingerprint=key_fp, error=exc.kind)
                raise
            self._journal("try", peer, key=key_id, fingerprint=key_fp, tries_left=w.tries_left,
                          **({"name": name} if name else {}))
        return {"pair": PAIR, **out}

    def abandon(self, body, peer: str | None = None) -> dict:
        sid = _hex_or_none((body or {}).get("session") if isinstance(body, dict) else None)
        with self._lock:
            w = self._window()
            gone = w.abandon(sid)
            if gone:
                self._journal("abandoned", peer, exit_code=3, error="confirmation", tries_left=w.tries_left,
                              **({"state": w.state} if w.state != "open" else {}))
        return {"pair": PAIR, "abandoned": gone, "tries_left": w.tries_left, "state": w.state}

    def step2(self, body, peer: str | None = None) -> dict:
        if not isinstance(body, dict) or not isinstance(body.get("session"), str) or \
                not isinstance(body.get("cA"), str):
            raise PairError("bad_request", "the body is {session, cA}")
        with self._lock:
            self._expired_now(peer)
            w = self._window()
            try:
                s = w.step2(body["session"], body["cA"])
            except PairError as exc:
                self._journal("confirm", peer, exit_code=3, error=exc.kind, tries_left=w.tries_left,
                              **({"state": w.state} if w.state != "open" else {}))
                raise
            try:
                got = keys.install(self.tree.control, s["key_id"], "ssh-ed25519", s["key_b64"],
                                   path=self.auth_keys, uid=self.uid)
            except keys.Unsafe as exc:
                w.undo()
                self._journal("confirm", peer, exit_code=3, key=s["key_id"], fingerprint=s["key_fp"],
                              error=exc.code)
                raise PairError(exc.code, exc.message, 409) from None
            try:
                token = access.issue(self.tree.http_token, s["key_id"])
            except OSError as exc:
                w.undo()
                self._journal("confirm", peer, exit_code=3, key=s["key_id"], fingerprint=s["key_fp"],
                              error="token_write")
                raise PairError("token_write", f"the HTTP token was not written ({exc.strerror})", 500) from None
            w.done()
            answer = self._answer(s, got, token)
            sealed = spake2.seal(spake2.ke_of(s["tt"]), answer, self.rng.token_bytes(spake2.NONCE_BYTES))
            self._journal("paired", peer, key=s["key_id"], fingerprint=s["key_fp"],
                          added=got["added"], **({"name": s["name"]} if s["name"] else {}))
        return {"pair": PAIR, "sealed": sealed}

    def _answer(self, s: dict, got: dict, token: str) -> bytes:
        try:
            with open(os.path.join(self.tree.root, "VERSION")) as fh:
                version = fh.read().strip()
        except OSError:
            version = None
        doc = {"pair": PAIR, "key_id": s["key_id"], "ssh_user": hostkey.ssh_user(),
               "ssh_port": hostkey.ssh_port(self.env, self.etc), "host_key": self.window.host_key["line"],
               "host_keys": [k["line"] for k in hostkey.host_keys(self.etc)], "http_port": self.port,
               "http_token": token, "engine_version": version, "mode": self.mode,
               "authorized_keys": {"added": got["added"], "line": got["line"]}}
        return json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()
