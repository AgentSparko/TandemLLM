"""SSH once (SEC-4): the controller logs in to a node one time, with the user's password or key,
to pin its certificate there; the password or key lives in this call's memory only.

    s = Ssh(known_hosts="<controller dir>/known_hosts")
    s.host_key(host, port)                 {type, fingerprint, known: new|same|changed, stored}
    rc, out, err = s.run(host, port, user, command, stdin, password=..., key=..., hk=...)

THE TOOL. The system's OpenSSH (`ssh`, `ssh-keyscan`), no Python SSH library: the user's own ssh
config, agent and jump hosts keep working, and nothing new parses the protocol. A password reaches
ssh through SSH_ASKPASS: a script in a private temporary directory that prints an environment
variable set for that one child, which runs in its own session (no terminal), so ssh asks the
script and never a terminal. A pasted key is written to a 0600 file in the same directory for the
call. The directory is removed when the call returns; nothing of either is logged, and every line
ssh prints is scrubbed of both before anyone sees it.

HOST KEYS. Trust on first use, with a confirmation step: `host_key` scans the host's key
(`ssh-keyscan`) and says whether this controller has seen it (`same`), never (`new`) or a
different one (`changed`). The wizard shows the fingerprint (SHA256, as ssh prints it) and may send
it back as `host_key` to insist on it. A login takes exactly the remembered key (`known_hosts`
beside the config database, OpenSSH's format) or, the first time, the scanned one, with
StrictHostKeyChecking: a changed key is refused, never replaced.
"""

from __future__ import annotations

import base64
import hashlib
import os
import shutil
import subprocess
import tempfile
import threading

PREFER = ("ssh-ed25519", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521",
          "ssh-rsa")
ASKPASS = '#!/bin/sh\nprintf \'%s\\n\' "$TANDEM_SSH_SECRET"\n'
TIMEOUT_S = 120.0


class SshError(Exception):
    """`kind` is the contract's error type: host_key_changed, ssh_failed, unreachable."""

    def __init__(self, kind: str, message: str, status: int = 502):
        super().__init__(message)
        self.kind, self.message, self.status = kind, message, status


def fingerprint(b64: str) -> str:
    """OpenSSH's SHA256 fingerprint of a public key blob."""
    try:
        raw = base64.b64decode(b64, validate=True)
    except ValueError:
        raise SshError("unreachable", "the SSH server offered a host key that does not decode")
    return "SHA256:" + base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")


def entry(host: str, port: int) -> str:
    return host if port == 22 else f"[{host}]:{port}"


def scrub(text: str, secrets: list[str]) -> str:
    for s in secrets:
        if not s:
            continue
        for piece in [s] + [ln.strip() for ln in s.splitlines() if len(ln.strip()) >= 8]:
            text = text.replace(piece, "***")
    return text


class Ssh:
    def __init__(self, known_hosts: str, *, ssh: str = "ssh", keyscan: str = "ssh-keyscan",
                 timeout: float = TIMEOUT_S):
        self.known_hosts = known_hosts
        self.ssh, self.keyscan, self.timeout = ssh, keyscan, timeout
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ host keys
    def scan(self, host: str, port: int) -> dict[str, str]:
        """{key type: base64 blob} the host offers."""
        try:
            r = subprocess.run([self.keyscan, "-T", "5", "-p", str(port), host],
                               capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as exc:
            raise SshError("unreachable", f"ssh-keyscan {host}:{port}: {type(exc).__name__}")
        keys = {}
        for line in r.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 3 and not line.startswith("#"):
                keys.setdefault(parts[1], parts[2])
        if not keys:
            raise SshError("unreachable", f"no SSH server answers at {host}:{port}")
        return keys

    def stored(self, host: str, port: int) -> tuple[str, str] | None:
        name = entry(host, port)
        try:
            with open(self.known_hosts) as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 3 and parts[0] == name:
                        return parts[1], parts[2]
        except FileNotFoundError:
            pass
        return None

    def host_key(self, host: str, port: int) -> tuple[dict, str | None]:
        """What the host offers against what this controller remembers: ({type, fingerprint,
        known: new|same|changed, stored}, the blob a login may take, None when changed)."""
        offered = self.scan(host, port)
        pick = next(k for k in PREFER + tuple(offered) if k in offered)
        got = self.stored(host, port)
        if got is None:
            return {"type": pick, "fingerprint": fingerprint(offered[pick]), "known": "new",
                    "stored": None}, offered[pick]
        kind, b64 = got
        if offered.get(kind) == b64:
            return {"type": kind, "fingerprint": fingerprint(b64), "known": "same",
                    "stored": fingerprint(b64)}, b64
        now = kind if kind in offered else pick
        return {"type": now, "fingerprint": fingerprint(offered[now]), "known": "changed",
                "stored": fingerprint(b64)}, None

    def remember(self, host: str, port: int, kind: str, b64: str) -> None:
        with self._lock:
            if self.stored(host, port) is not None:
                return
            os.makedirs(os.path.dirname(self.known_hosts), mode=0o700, exist_ok=True)
            fd = os.open(self.known_hosts, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a") as f:
                f.write(f"{entry(host, port)} {kind} {b64}\n")

    # ------------------------------------------------------------------ one login
    def run(self, host: str, port: int, user: str, command: str, stdin: bytes, *,
            kind: str, b64: str, password: str | None = None,
            key: str | None = None) -> tuple[int, str, str]:
        """`command` on host as user, `stdin` fed to it: (exit status, stdout, stderr), both
        scrubbed of the password and the key. The host must present exactly `kind`/`b64`."""
        secrets = [password or "", key or ""]
        tmp = tempfile.mkdtemp(prefix="tandem-ssh-")
        try:
            kh = os.path.join(tmp, "known_hosts")
            with open(kh, "w") as f:
                f.write(f"{entry(host, port)} {kind} {b64}\n")
            algos = "rsa-sha2-512,rsa-sha2-256,ssh-rsa" if kind == "ssh-rsa" else kind
            argv = [self.ssh, "-T", "-p", str(port), "-l", user,
                    "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={kh}",
                    "-o", "GlobalKnownHostsFile=/dev/null", "-o", f"HostKeyAlgorithms={algos}",
                    "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=10",
                    "-o", "NumberOfPasswordPrompts=1", "-o", "ControlPath=none",
                    "-o", "LogLevel=ERROR"]
            env = {k: v for k, v in os.environ.items()
                   if k not in ("SSH_ASKPASS", "SSH_ASKPASS_REQUIRE", "TANDEM_SSH_SECRET")}
            if password is not None:
                ap = os.path.join(tmp, "askpass")
                fd = os.open(ap, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
                with os.fdopen(fd, "w") as f:
                    f.write(ASKPASS)
                argv += ["-o", "PreferredAuthentications=password,keyboard-interactive",
                         "-o", "PubkeyAuthentication=no"]
                env.update(SSH_ASKPASS=ap, SSH_ASKPASS_REQUIRE="force",
                           DISPLAY=env.get("DISPLAY") or ":0", TANDEM_SSH_SECRET=password)
            elif key is not None:
                kp = os.path.join(tmp, "key")
                fd = os.open(kp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as f:
                    f.write(key.strip() + "\n")
                argv += ["-i", kp, "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
                         "-o", "PreferredAuthentications=publickey"]
            else:
                argv += ["-o", "BatchMode=yes"]
            argv += [host, command]
            try:
                r = subprocess.run(argv, input=stdin, capture_output=True, env=env,
                                   timeout=self.timeout, start_new_session=True)
            except subprocess.TimeoutExpired:
                raise SshError("ssh_failed", f"ssh {user}@{host}:{port} took longer than "
                                             f"{self.timeout:.0f} s")
            except OSError as exc:
                raise SshError("ssh_failed", f"ssh could not start ({type(exc).__name__})")
            out = scrub(r.stdout.decode("utf-8", "replace"), secrets)
            err = scrub(r.stderr.decode("utf-8", "replace"), secrets)
            return r.returncode, out, err
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
