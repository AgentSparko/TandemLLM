"""The controller's admin password (Q-2, concept A8): set, changed and removed in App settings,
kept as a salted hash beside the config database (OPS-234, VIS-E19 6.3b).

    pw = Password(path)                    `admin.json` in the state dir, read when it changes
    pw.is_set, pw.set_at
    pw.verify(text) -> bool                False for a wrong one, and while the cap below holds
    pw.held() -> str | None                why no check runs now (the cap, or checks in flight)
    pw.set(text) / pw.remove()
    pw.material() -> bytes | None          what the session key is made from (server/auth.py)

THE FILE. `admin.json`, mode 0600, written to a new file beside it and renamed over it, so a
crash leaves the old one or the new one (after a removal it stays with a fresh salt and no hash,
so the sessions a password ended never come back):

    {"v": 1, "kdf": "scrypt", "n": 16384, "r": 8, "p": 1, "salt": <b64 16 bytes>,
     "hash": <b64 32 bytes>, "set_at": <unix s>}

or `"kdf": "pbkdf2-sha256", "rounds": 600000` where hashlib has no scrypt (a Python built on
LibreSSL). The password itself is never written, logged or returned; the hash is compared with
`hmac.compare_digest`. A file that does not read is a password nobody knows (fail closed). The file is read again when its mtime or size changes, so
`tandem-app password set|remove` on the machine (the way back from a lost password) takes effect
without a restart.

THE ADMIN TOKEN BESIDE IT. `TANDEM_ADMIN_TOKEN` from the env file (32+ characters, server/auth.py)
stays what it was: the bearer for tools and a password at sign-in. This password is a second
secret for people: it signs in (POST /v1/fleet/session, /v1/dashboard/session) and it is the
`current` a change asks for; it is never a bearer, so no request pays for a hash.

LIMITS. A password is MIN_LEN to MAX_LEN characters, no control characters. A failed check counts
towards a cap over every address (GLOBAL_FAILS in GLOBAL_WINDOW_S): past it no password check runs
until the window passes, so a forged X-Real-IP cannot spread a guessing run over many "addresses"
(the per-address limit is server/auth.py's); the admin token is compared before any of this and is
never held by it. At most AT_ONCE checks run at once (scrypt takes 16 MiB each); the rest are
refused, not queued.
"""

from __future__ import annotations

import base64
import collections
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

MIN_LEN = 12
MAX_LEN = 1024
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}
PBKDF2_ROUNDS = 600_000
GLOBAL_FAILS = 30
GLOBAL_WINDOW_S = 60.0
AT_ONCE = 2


class PasswordError(Exception):
    def __init__(self, status: int, kind: str, message: str):
        super().__init__(message)
        self.status, self.kind, self.message = status, kind, message


def check_new(text) -> str | None:
    """Why `text` cannot be the password, or None."""
    if not isinstance(text, str):
        return "the password is a string"
    if not MIN_LEN <= len(text) <= MAX_LEN:
        return f"the password is {MIN_LEN} to {MAX_LEN} characters"
    if any(ord(c) < 32 or ord(c) == 127 for c in text):
        return "the password has no control characters"
    if not text.strip():
        return "the password is more than spaces"
    return None


def _derive(text: str, doc: dict) -> bytes:
    salt = base64.b64decode(doc["salt"])
    if doc["kdf"] == "scrypt":
        return hashlib.scrypt(text.encode(), salt=salt, n=doc["n"], r=doc["r"], p=doc["p"],
                              maxmem=64 * 1024 * 1024, dklen=32)
    if doc["kdf"] == "pbkdf2-sha256":
        return hashlib.pbkdf2_hmac("sha256", text.encode(), salt, doc["rounds"], dklen=32)
    raise ValueError(f"unknown kdf {doc['kdf']!r}")


def make_doc(text: str, now: float) -> dict:
    """The file's content for `text`: scrypt where hashlib has it, else PBKDF2."""
    salt = secrets.token_bytes(16)
    doc = {"v": 1, "salt": base64.b64encode(salt).decode(), "set_at": round(now, 3)}
    doc.update({"kdf": "scrypt", **SCRYPT} if hasattr(hashlib, "scrypt") else
               {"kdf": "pbkdf2-sha256", "rounds": PBKDF2_ROUNDS})
    doc["hash"] = base64.b64encode(_derive(text, doc)).decode()
    return doc


class Password:
    def __init__(self, path: str, *, clock=time.time):
        self.path = path
        self.clock = clock
        self._doc: dict | None = None
        self._stamp = None
        self._fails: collections.deque = collections.deque()
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(AT_ONCE)
        self._load()

    # ------------------------------------------------------------------ the file
    def _load(self) -> dict | None:
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            self._doc, self._stamp = None, None
            return None
        stamp = (st.st_mtime_ns, st.st_size, st.st_ino)
        if stamp != self._stamp:
            try:
                with open(self.path) as f:
                    doc = json.load(f)
                if not isinstance(doc, dict) or doc.get("v") != 1 or not doc.get("salt") or \
                        not (doc.get("hash") or doc.get("removed")):
                    raise ValueError("not a password file")
            except (OSError, ValueError):
                # fail closed: a file there that does not read is a password nobody knows (the
                # command below sets a new one)
                doc = {"v": 1, "broken": True, "set_at": None}
            self._doc, self._stamp = doc, stamp
        return self._doc

    def _write(self, doc: dict) -> None:
        d = os.path.dirname(self.path) or "."
        os.makedirs(d, mode=0o700, exist_ok=True)
        tmp = os.path.join(d, f".admin.{secrets.token_hex(6)}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(doc, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.remove(tmp)
            except FileNotFoundError:
                pass
            raise
        with self._lock:
            self._stamp = None
            self._fails.clear()
        self._load()

    # ------------------------------------------------------------------ what it is
    def _live(self) -> dict | None:
        doc = self._load()
        return None if doc is None or doc.get("removed") else doc

    @property
    def is_set(self) -> bool:
        return self._live() is not None

    @property
    def set_at(self) -> float | None:
        doc = self._live()
        return doc.get("set_at") if doc else None

    def material(self) -> bytes | None:
        """The hash and its salt: the session key is made from them, so a new password (a new
        salt) ends every session made under the old one."""
        doc = self._load()
        if doc is None:
            return None
        if doc.get("broken"):
            return b"broken:" + repr(self._stamp).encode()
        return base64.b64decode(doc["salt"]) + base64.b64decode(doc.get("hash") or "")

    # ------------------------------------------------------------------ checks
    def _held_locked(self, now: float) -> str | None:
        while self._fails and now - self._fails[0] > GLOBAL_WINDOW_S:
            self._fails.popleft()
        if len(self._fails) >= GLOBAL_FAILS:
            return "too many wrong passwords from anywhere: wait a minute"
        return None

    def held(self) -> str | None:
        with self._lock:
            return self._held_locked(self.clock())

    def verify(self, text) -> bool:
        doc = self._live()
        if doc is None or doc.get("broken") or not isinstance(text, str) or not text or \
                len(text) > MAX_LEN:
            return False
        with self._lock:
            if self._held_locked(self.clock()):
                return False
        if not self._slots.acquire(blocking=False):
            return False
        try:
            got = _derive(text, doc)
        except (ValueError, KeyError, MemoryError):
            got = b""
        finally:
            self._slots.release()
        good = hmac.compare_digest(got, base64.b64decode(doc["hash"]))
        if not good:
            with self._lock:
                self._fails.append(self.clock())
        return good

    def busy(self) -> bool:
        """Every check slot is taken now (the caller answers 429, not a wrong password)."""
        if self._slots.acquire(blocking=False):
            self._slots.release()
            return False
        return True

    # ------------------------------------------------------------------ changes
    def set(self, text: str) -> dict:
        bad = check_new(text)
        if bad:
            raise PasswordError(400, "bad_request", bad)
        doc = make_doc(text, self.clock())
        self._write(doc)
        return doc

    def remove(self) -> bool:
        """No password from now on. The file stays with a new salt and no hash: the session key
        is still made from it, so the sessions the password's removal ends (and those it had
        ended) do not come back."""
        had = self.is_set
        self._write({"v": 1, "removed": True, "set_at": None,
                     "salt": base64.b64encode(secrets.token_bytes(16)).decode()})
        return had


# ---------------------------------------------------------------------- the command

def main(argv: list[str], state_dir: str | None = None) -> int:
    """`tandem-app password set|remove|status [--state-dir D]`: on the controller's machine, where
    the file is (the way back from a lost password; the page needs the current one)."""
    import argparse
    import getpass
    ap = argparse.ArgumentParser(prog="tandem-app password",
                                 description="The controller's admin password (App settings).")
    ap.add_argument("action", choices=("set", "remove", "status"))
    ap.add_argument("--state-dir", default=state_dir or "~/.tandemllm",
                    help="the directory of the controller's config.sqlite3 (default "
                         "~/.tandemllm)")
    a = ap.parse_args(argv)
    pw = Password(os.path.join(os.path.expanduser(a.state_dir), "admin.json"))
    if a.action == "status":
        print("admin password: " + ("set" if pw.is_set else "not set") + f" ({pw.path})")
        return 0
    if a.action == "remove":
        print("admin password removed" if pw.remove() else "no admin password was set")
        return 0
    one = getpass.getpass("New admin password: ")
    if one != getpass.getpass("Again: "):
        print("the two do not match; nothing changed")
        return 1
    try:
        pw.set(one)
    except PasswordError as exc:
        print(exc.message)
        return 1
    print(f"admin password set ({pw.path}, mode 0600); every session signed in before ends")
    return 0
