"""What a pairing window and the advertisement say about this host: its SSH host key and its
fingerprint, the SSH port and user, and its addresses. No root is needed: sshd's public host keys
(`*.pub`) and its config are readable by every user. Standard library only.

THE HOST KEY is the one an OpenSSH client gets first, as `ssh-keyscan` reports it: the client's
default order prefers ssh-ed25519, then ecdsa-sha2-nistp256/384/521, then RSA. The keys are sshd's
`HostKey` lines (sshd_config and the files its `Include` lines name) with `.pub`, else the
default files /etc/ssh/ssh_host_{ed25519,ecdsa,rsa}_key.pub.

THE SSH PORT is QSE_SSH_PORT when set, else sshd_config's first `Port`, else 22.
"""

from __future__ import annotations

import base64
import binascii
import glob
import hashlib
import os
import pwd
import re
import socket
import struct
import sys

ETC_SSH = "/etc/ssh"
#: the OpenSSH client's preference among plain host key types
ORDER = ("ssh-ed25519", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521",
         "rsa-sha2-512", "ssh-rsa")
TYPE_NAME = {"ssh-ed25519": "ED25519", "ecdsa-sha2-nistp256": "ECDSA", "ecdsa-sha2-nistp384": "ECDSA",
             "ecdsa-sha2-nistp521": "ECDSA", "ssh-rsa": "RSA"}
B64 = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")


def fingerprint(blob: bytes) -> str:
    """OpenSSH's SHA256 fingerprint of a public key blob (what `ssh-keygen -l` prints)."""
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


class NoHostKey(Exception):
    pass


def parse_pub(text: str) -> tuple[str, bytes] | None:
    """(type, blob) of an OpenSSH public key line, checked: the blob names the same type."""
    parts = text.strip().split()
    if len(parts) < 2 or not B64.match(parts[1]):
        return None
    try:
        blob = base64.b64decode(parts[1], validate=True)
    except (binascii.Error, ValueError):
        return None
    if len(blob) < 4:
        return None
    n = struct.unpack(">I", blob[:4])[0]
    if blob[4:4 + n].decode("ascii", "replace") != parts[0]:
        return None
    return parts[0], blob


def ed25519_key(text: str) -> tuple[str, bytes] | None:
    """(type, blob) of an ssh-ed25519 public key line whose blob is exactly the type and 32 bytes."""
    got = parse_pub(text)
    if not got or got[0] != "ssh-ed25519":
        return None
    blob = got[1]
    if len(blob) != 4 + 11 + 4 + 32 or struct.unpack(">I", blob[15:19])[0] != 32:
        return None
    return got


def _config_lines(path: str, seen: set) -> list[tuple[str, str]]:
    """(keyword, value) of sshd_config and the files its Include lines name, in order."""
    real = os.path.realpath(path)
    if real in seen:
        return []
    seen.add(real)
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return []
    out = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        parts = ln.split(None, 1)
        kw, val = parts[0].lower(), (parts[1].strip() if len(parts) > 1 else "")
        if kw == "match":
            break                               # a Match block's settings are per connection
        if kw == "include":
            for pat in val.split():
                pat = pat if os.path.isabs(pat) else os.path.join(os.path.dirname(path), pat)
                for f in sorted(glob.glob(pat)):
                    out += _config_lines(f, seen)
            continue
        out.append((kw, val))
    return out


def host_keys(etc: str = ETC_SSH) -> list[dict]:
    """sshd's public host keys, the client's preferred one first: {type, line, fingerprint, path}."""
    conf = _config_lines(os.path.join(etc, "sshd_config"), set())
    paths = [v + ".pub" for k, v in conf if k == "hostkey" and v]
    if not paths:
        paths = [os.path.join(etc, f"ssh_host_{t}_key.pub") for t in ("ed25519", "ecdsa", "rsa")]
    keys, seen = [], set()
    for p in paths:
        try:
            with open(p, encoding="ascii", errors="replace") as fh:
                got = parse_pub(fh.read())
        except OSError:
            continue
        if not got or got[1] in seen:
            continue
        seen.add(got[1])
        t, blob = got
        keys.append({"type": t, "line": f"{t} {base64.b64encode(blob).decode()}", "fingerprint": fingerprint(blob),
                     "path": p})
    keys.sort(key=lambda k: ORDER.index(k["type"]) if k["type"] in ORDER else len(ORDER))
    return keys


def preferred(etc: str = ETC_SSH) -> dict:
    keys = host_keys(etc)
    if not keys:
        raise NoHostKey(f"no SSH host key readable under {etc} (is the SSH server installed? "
                        "sudo apt install openssh-server)")
    return keys[0]


def ssh_port(env=None, etc: str = ETC_SSH) -> int:
    env = os.environ if env is None else env
    v = (env.get("QSE_SSH_PORT") or "").strip()
    if v.isdigit() and 0 < int(v) < 65536:
        return int(v)
    for k, val in _config_lines(os.path.join(etc, "sshd_config"), set()):
        if k == "port" and val.isdigit() and 0 < int(val) < 65536:
            return int(val)
    return 22


def ssh_user() -> str:
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except KeyError:                      # no passwd entry (a bare container)
        return os.environ.get("USER") or os.environ.get("LOGNAME") or str(os.getuid())


# ---------------------------------------------------------------------------------------- addresses
_SIOCGIFADDR = {"linux": 0x8915, "darwin": 0xC0206921}


def _usable(ip: str) -> bool:
    return not ip.startswith(("127.", "169.254.", "0."))


def ipv4_addresses() -> list[str]:
    """This host's IPv4 addresses that another machine may reach (no loopback, no link-local)."""
    out: list[str] = []
    req = _SIOCGIFADDR.get(sys.platform)
    if req is not None and hasattr(socket, "if_nameindex"):
        import fcntl
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            for _i, name in socket.if_nameindex():
                try:
                    raw = fcntl.ioctl(s.fileno(), req, struct.pack("256s", name.encode()[:15]))
                except OSError:
                    continue
                ip = socket.inet_ntoa(raw[20:24])
                if _usable(ip) and ip not in out:
                    out.append(ip)
        finally:
            s.close()
    if not out:
        # the address a datagram towards the LAN would leave from (nothing is sent)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.0.2.1", 9))
            ip = s.getsockname()[0]
            if _usable(ip):
                out.append(ip)
        except OSError:
            pass
        finally:
            s.close()
    return out


def bound_addresses(host: str) -> list[str]:
    """The addresses a server bound to `host` answers on for other machines ([] for loopback)."""
    h = (host or "").strip()
    if h in ("", "0.0.0.0", "::", "*"):
        return ipv4_addresses()
    if h.startswith("127.") or h in ("::1", "localhost"):
        return []
    return [h]


def hostname() -> str:
    return socket.gethostname().split(".")[0] or "engine"
