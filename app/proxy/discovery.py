"""Discovery (SRV-727): what the Add Spark wizard's Find step lists, and what each device is.

    d = Discovery(fleet)                         one per controller (Fleet.discover())
    d.handle(method, parts, query, body, who)    the routes under /v1/fleet/discovery
    d.learn(host, port, dmi=..., owned=...)      what a pairing or an SSH login found out
    classify_dmi(vendor, product, version, family) -> (label, spark) | None
    parse_ssh_line("ssh -p 2201 user@host -J jump") -> the target, its route, warnings

    GET    /v1/fleet/discovery[?since=<cursor>]  the list and each source's state; with `since`
                                                 only the rows changed after that cursor (poll it)
    POST   /v1/fleet/discovery {interface?}      a new run: mDNS browse (3 s), ~/.ssh/config, and
                                                 the scan's question for the interface's /24
    POST   /v1/fleet/discovery/scan {subnet}     the consent: the subnet exactly as asked
    DELETE /v1/fleet/discovery/scan              stop the scan
    POST   /v1/fleet/discovery/address {address, port?}   add by address: one device checked
    POST   /v1/fleet/discovery/target {line}     a pasted ssh line as a target with its route

THE SOURCES. mDNS (proxy/mdns.py): `_tandemnode._tcp` for nodes with TandemNode, whose TXT says the
node id, its class, TandemLLM's version, the DMI product, whether it is paired and to whom, and a
pairing window while one is open; `_ssh._tcp` and `_workstation._tcp` for host names. The scan: a
TCP connect to port 22 of each address of one /24 (300 ms), then the banner the server sends first
(SSH servers speak first), nothing else: never a login, never a byte sent. ~/.ssh/config: its Host
entries resolved as ssh resolves them (the first value wins, wildcards supply defaults, Include
followed), never connected to. Add by address: the same banner read of one address the user typed,
and a connect to the agent's ports. The SSH login of pairing (proxy/fleettrust.py) adds the DMI
product it read on the node.

CONSENT. Nothing goes to port 22 of any address until a write (the admin token, a session, or this
machine) answers the question the list carries, "Scan 192.0.2.0/24 for SSH?", with that subnet. The
scan stays inside it (each address is checked against it before its connect), runs at most
RATE_PER_S connects a second and INFLIGHT at once, and is audited with who said yes. A new run asks
again.

ONE ROW PER BOX. Every finding is a sighting with its keys (the node id, the SSH host key, each
address, the mDNS host name); sightings that share a key are one row. Two different node ids are
never one row, however many keys they share (a virtual node shares its machine's address). Host
keys come without packets: the TXT's `hk`, and the user's and this controller's known_hosts
(hashed entries too) for each address and name a sighting has.

WHAT IT IS. A row's kind: `spark` (TandemNode answered over mDNS: "Spark with TandemLLM x.y"),
`gb10` (a GB10 box by its DMI product, nothing installed), `host` (not a Spark: by its DMI product,
or its SSH banner says an OS other than DGX OS's Ubuntu 24.04 or later), `owned` (a Spark another
TandemApp owns, by its TXT or a pairing refused 409) or `unknown`. The GB10 boxes are in GB10 below,
one table for every vendor. Without a login the banner can only rule a Spark out: an Ubuntu 24.04
banner is a candidate, and the row says to sign in once.

macOS. The first browse shows the Local Network prompt; with the permission off sendto fails and
`local_network.state` is `denied` with the setting's place in words. The scan is refused then (409
lan_denied); ~/.ssh/config, add by address, the ssh line and pairing by address keep working (a
LAN address may still be blocked for this process: its pairing error says so).
"""

from __future__ import annotations

import base64
import fnmatch
import glob
import hashlib
import hmac
import ipaddress
import os
import re
import shlex
import socket
import sys
import threading
import time

from proxy import linktest, mdns

CONTRACT = "1.0"
BROWSE_S = 3.0
TYPES = (mdns.SERVICE, "_ssh._tcp.local", "_workstation._tcp.local")
SCAN_PORT = 22
TIMEOUT_S = 0.3
BANNER_S = 1.0
RATE_PER_S = 50
INFLIGHT = 16
AGENT_PORTS = (7700, 7701)
SETTINGS = "System Settings → Privacy & Security → Local Network"
# interfaces that are no LAN to scan: container and VM bridges, VPN tunnels, Apple's peer links
VIRTUAL = ("docker", "br-", "veth", "virbr", "cni", "flannel", "lxc", "tailscale", "utun",
           "awdl", "llw", "bridge", "zt")

# The GB10 boxes (every one is Spark-class): the vendor's name, the model, how the DMI says it
# (sys_vendor contains one of `vendor`, and product_name, product_version or product_family one of
# `model`) and where that match comes from. Any box whose product_family is "DGX Spark" is
# Spark-class too, named by its own DMI: the reference design sets it, an OEM not listed here
# included.
GB10 = (
    ("NVIDIA", "DGX Spark", ("nvidia",), ("dgx spark",),
     "NVIDIA's reference box; product family DGX Spark"),
    ("ASUS", "Ascent GX10", ("asustek", "asus"), ("gx10",),
     "read on a GX10: sys_vendor ASUSTeK COMPUTER INC., product_name GX10, family DGX Spark"),
    ("Dell", "Pro Max with GB10", ("dell",), ("gb10",),
     "the vendor's model name; not yet read from a box"),
    ("Lenovo", "ThinkStation PGX", ("lenovo",), ("pgx",),
     "the vendor's model name, in product_version (Lenovo's product_name is the machine "
     "type); not yet read from a box"),
    ("MSI", "EdgeXpert", ("micro-star", "msi"), ("edgexpert", "ms-c931"),
     "the vendor's model name and board MS-C931; not yet read from a box"),
    ("Gigabyte", "AI TOP ATOM", ("gigabyte", "giga computing"), ("atom",),
     "the vendor's model name; not yet read from a box"),
    ("Acer", "Veriton GN100", ("acer",), ("gn100",),
     "the vendor's model name; not yet read from a box"),
    ("HP", "ZGX Nano G1n", ("hp", "hewlett"), ("zgx",),
     "the vendor's model name; not yet read from a box"),
)
# a host name that suggests a Spark (a hint in the row's words, never its kind)
NAME_HINT = re.compile(r"spark|gx10|gb10|dgx|pgx|atom|gn100|zgx", re.I)
# OpenSSH's version on an Ubuntu release (the banner's "Ubuntu-..." says the distribution)
UBUNTU = {(8, 2): "20.04", (8, 9): "22.04", (9, 3): "23.10", (9, 6): "24.04", (9, 7): "24.10",
          (9, 9): "25.04", (10, 0): "25.10"}
LINUX_MARKS = ("debian", "raspbian", "dropbear", "fedora", "alpine", "suse", "arch", "gentoo",
               "openwrt", "synology", "qnap")
KINDS = ("spark", "gb10", "host", "owned", "unknown")
LEARNED = ("login", "pair")


class DiscoveryError(Exception):
    def __init__(self, status: int, kind: str, message: str):
        super().__init__(message)
        self.status, self.kind, self.message = status, kind, message


# ---------------------------------------------------------------------- what a device is

def classify_dmi(vendor: str | None, product: str | None, version: str | None = None,
                 family: str | None = None) -> tuple[str, bool] | None:
    """(the box's name, Spark-class) from its DMI; None when nothing was read."""
    v = (vendor or "").strip()
    fields = " | ".join(x for x in (product, version, family) if x).lower()
    if not v and not fields:
        return None
    for name, model, vend, models, _ in GB10:
        if any(x in v.lower() for x in vend) and any(m in fields for m in models):
            return f"{name} {model}" if name not in model else model, True
    raw = " ".join(x for x in (v, product) if x).strip() or "unknown"
    if "dgx spark" in (family or "").lower():
        return raw, True
    return raw, False


def banner_os(banner: str | None) -> tuple[str, str]:
    """(verdict, the OS in words) from an SSH banner: `candidate` (Ubuntu 24.04 or later, as DGX
    OS is), `not_spark`, or `unknown` (no banner, or one that says nothing)."""
    if not banner or not banner.startswith("SSH-"):
        return "unknown", "no SSH banner"
    b = banner.strip()
    low = b.lower()
    m = re.search(r"openssh_(\d+)\.(\d+)", low)
    ver = (int(m.group(1)), int(m.group(2))) if m else None
    if "windows" in low:
        return "not_spark", "Windows"
    if "ubuntu" in low:
        rel = UBUNTU.get(ver) if ver else None
        name = f"Ubuntu {rel}" if rel else "Ubuntu"
        if ver is not None and ver >= (9, 6):
            return "candidate", name
        return "not_spark", name
    for mark in LINUX_MARKS:
        if mark in low:
            return "not_spark", "Linux (dropbear)" if mark == "dropbear" else \
                f"Linux ({mark.capitalize()})"
    if ver == (9, 6) and re.search(r"openssh_9\.6p1\s*$", low):
        # DGX OS with `DebianBanner no` says no distribution
        return "candidate", "OpenSSH 9.6p1, as on Ubuntu 24.04"
    return "not_spark", f"OpenSSH {ver[0]}.{ver[1]}" if ver else b[8:40] or "SSH"


# ---------------------------------------------------------------------- ~/.ssh

def _cfg_lines(path: str, home: str, depth: int = 0):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"(\S+?)\s*(?:=\s*|\s+)(.*)$", line)
        if not m:
            continue
        key, val = m.group(1).lower(), m.group(2).strip()
        if key == "include" and depth < 8:
            for pat in shlex.split(val):
                pat = os.path.expanduser(pat)
                if not os.path.isabs(pat):
                    pat = os.path.join(home, ".ssh", pat)
                for p in sorted(glob.glob(pat)):
                    yield from _cfg_lines(p, home, depth + 1)
            continue
        yield key, val


def ssh_config(home: str) -> list[dict]:
    """The concrete Host entries of `home`/.ssh/config, each as ssh would resolve it: {alias,
    hostname, user, port, identity_file, proxy_jump}. Wildcard patterns name no host; a Match block
    is skipped (it depends on the moment)."""
    blocks, cur = [], (["*"], {})               # options before the first Host apply to all
    blocks.append(cur)
    for key, val in _cfg_lines(os.path.join(home, ".ssh", "config"), home):
        if key == "host":
            cur = (shlex.split(val) or ["*"], {})
            blocks.append(cur)
            continue
        if key == "match":
            cur = ([], {})                       # matches nothing here
            blocks.append(cur)
            continue
        if key in ("hostname", "user", "port", "identityfile", "proxyjump"):
            cur[1].setdefault(key, val.strip('"'))
    aliases = []
    for pats, _ in blocks:
        for p in pats:
            if p.startswith("!") or any(c in p for c in "*?") or p in aliases:
                continue
            aliases.append(p)
    out = []
    for alias in aliases:
        got: dict = {}
        for pats, opts in blocks:
            pos = [p for p in pats if not p.startswith("!")]
            neg = [p[1:] for p in pats if p.startswith("!")]
            if any(fnmatch.fnmatch(alias, p) for p in pos) and \
                    not any(fnmatch.fnmatch(alias, p) for p in neg):
                for k, v in opts.items():
                    got.setdefault(k, v)
        hostname = got.get("hostname", alias).replace("%h", alias)
        try:
            port = int(got.get("port", 22))
        except ValueError:
            port = 22
        jump = got.get("proxyjump")
        out.append({"alias": alias, "hostname": hostname, "user": got.get("user"),
                    "port": port, "identity_file": got.get("identityfile"),
                    "proxy_jump": None if jump in (None, "none") else jump})
    return out


def known_hosts(paths: list[str]) -> list[tuple[list[str], str, str]]:
    """(patterns, key type, base64 key) of each line of the known_hosts files."""
    out = []
    for p in paths:
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) < 3 or parts[0].startswith(("#", "@")):
                        continue
                    out.append((parts[0].split(","), parts[1], parts[2]))
        except OSError:
            continue
    return out


def _hk_match(pattern: str, name: str) -> bool:
    if pattern.startswith("|1|"):
        try:
            salt, digest = pattern[3:].split("|", 1)
            mac = hmac.new(base64.b64decode(salt), name.encode(), hashlib.sha1).digest()
            return hmac.compare_digest(mac, base64.b64decode(digest))
        except (ValueError, TypeError):
            return False
    return pattern.lower() == name.lower()


def host_key_of(entries, names: list[str], port: int = 22) -> str | None:
    """The SHA256 fingerprint of the key known_hosts holds for any of `names`, ed25519 first."""
    want = [n if port == 22 else f"[{n}]:{port}" for n in names if n]
    best = None
    for pats, kind, b64 in entries:
        if any(_hk_match(p, n) for p in pats for n in want):
            if best is None or (kind == "ssh-ed25519" and best[0] != "ssh-ed25519"):
                best = (kind, b64)
    if best is None:
        return None
    try:
        raw = base64.b64decode(best[1], validate=True)
    except ValueError:
        return None
    return "SHA256:" + base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")


# ---------------------------------------------------------------------- the ssh line

HOST_BAD = set("@/\\'\"`$;|&<>") | set(" \t\n")
USER_OK = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
NO_ARG = set("46AaCfGgKkMNnqsTtVvXxYy")
WITH_ARG = set("BbcDEeFIiJLlmOoPpQRSWw")
REFUSED_O = ("proxycommand", "localcommand", "permitlocalcommand", "knownhostscommand",
             "remotecommand", "controlmaster", "controlpath", "sendenv", "setenv")


def _host_ok(h: str) -> bool:
    return bool(h) and len(h) <= 253 and not h.startswith("-") and not (set(h) & HOST_BAD)


def _hop(spec: str, what: str) -> dict:
    """[user@]host[:port] of a jump host (an IPv6 address in brackets)."""
    user, _, rest = spec.rpartition("@")
    m = re.match(r"^\[([0-9a-fA-F:.]+)\](?::(\d+))?$", rest) or \
        re.match(r"^([^:\[\]]+)(?::(\d+))?$", rest)
    if not m or not _host_ok(m.group(1)):
        raise ValueError(f"{what} {spec!r} is not [user@]host[:port]")
    if user and not USER_OK.match(user):
        raise ValueError(f"{what}: {user!r} is not a login name")
    port = int(m.group(2)) if m.group(2) else 22
    if not 0 < port < 65536:
        raise ValueError(f"{what}: port {port} is not 1 to 65535")
    return {"user": user or None, "host": m.group(1), "port": port}


def parse_ssh_line(line: str) -> dict:
    """A pasted ssh command line as {user, host, port, identity_file, jumps, route, remote,
    warnings}. `-p`, `-l`, `-i`, `-J` and `-o Port/User/IdentityFile/ProxyJump` are read, the first
    value of each winning as in ssh (an option before the user in user@host too); an option
    that runs a command (ProxyCommand and the like) is refused; forwards, a remote command and the
    other options are dropped with a warning. ValueError says what is wrong."""
    if not isinstance(line, str) or not line.strip() or len(line) > 2000:
        raise ValueError("the line is an ssh command, up to 2000 characters")
    try:
        argv = shlex.split(line.strip())
    except ValueError as exc:
        raise ValueError(f"the line does not parse: {exc}")
    if argv and os.path.basename(argv[0]) == "ssh":
        argv = argv[1:]
    port = user = key = jump = None
    warnings, dest, rest = [], None, []
    i = 0
    while i < len(argv):
        a = argv[i]
        if dest is not None:
            rest.append(a)
            i += 1
            continue
        if a.startswith("-") and len(a) > 1:
            j = 1
            while j < len(a):
                c = a[j]
                if c in NO_ARG:
                    j += 1
                    continue
                if c not in WITH_ARG:
                    raise ValueError(f"-{c} is not an ssh option")
                val = a[j + 1:] if j + 1 < len(a) else None
                if val is None:
                    i += 1
                    if i >= len(argv):
                        raise ValueError(f"-{c} needs a value")
                    val = argv[i]
                if c == "p":
                    port = port or val
                elif c == "l":
                    user = user or val
                elif c == "i":
                    key = key or val
                elif c == "J":
                    jump = jump or val
                elif c == "o":
                    k, sep, v = val.partition("=")
                    if not sep:
                        k, _, v = val.partition(" ")
                    k, v = k.strip().lower(), v.strip()
                    if k in REFUSED_O:
                        raise ValueError(f"-o {k}: an option that runs a command or changes "
                                         f"how connections are shared is not accepted (use -J "
                                         f"for a jump host)")
                    if k == "port":
                        port = port or v
                    elif k == "user":
                        user = user or v
                    elif k == "identityfile":
                        key = key or v
                    elif k == "proxyjump":
                        jump = jump or v
                    else:
                        warnings.append(f"-o {k} is not used")
                else:
                    warnings.append(f"-{c} {val} is not used")
                break
            i += 1
            continue
        dest = a
        i += 1
    if dest is None:
        raise ValueError("no host: the line is ssh [options] [user@]host")
    m = re.match(r"^ssh://(?:([^@/]+)@)?(\[[0-9a-fA-F:.]+\]|[^:/]+)(?::(\d+))?/?$", dest)
    if m:
        user = user or m.group(1)
        host = m.group(2).strip("[]")
        port = port or m.group(3)
    else:
        u, _, host = dest.rpartition("@")
        user = user or u or None
    if not _host_ok(host):
        raise ValueError(f"{host!r} is not a host name or address")
    if user is not None and not USER_OK.match(user):
        raise ValueError(f"{user!r} is not a login name")
    try:
        port = int(port) if port is not None else 22
    except ValueError:
        raise ValueError(f"port {port!r} is not a number")
    if not 0 < port < 65536:
        raise ValueError(f"port {port} is not 1 to 65535")
    jumps = [] if jump in (None, "none") else \
        [_hop(s.strip(), "jump host") for s in jump.split(",") if s.strip()]
    if rest:
        warnings.append("the command after the host is not used")
    try:
        ip = ipaddress.ip_address(host)
        remote = ip.is_global
    except ValueError:
        remote = not host.lower().endswith(".local") and "." in host
    route = [{"kind": "jump", **h} for h in jumps] + \
        [{"kind": "node", "user": user, "host": host, "port": port}]
    return {"user": user, "host": host, "port": port, "identity_file": key, "jumps": jumps,
            "route": route, "remote": bool(remote or jumps), "warnings": warnings}


# ---------------------------------------------------------------------- the LAN

def _ifconfig(text: str) -> dict:
    out, name = {}, None
    for line in (text or "").splitlines():
        m = re.match(r"^(\S+?):\s+flags=", line)
        if m:
            name = m.group(1)
            continue
        m = re.match(r"^\s+inet\s+(\d+\.\d+\.\d+\.\d+)\s+netmask\s+(0x[0-9a-fA-F]+|[\d.]+)", line)
        if m and name:
            mask = m.group(2)
            bits = bin(int(mask, 16)).count("1") if mask.startswith("0x") else \
                ipaddress.IPv4Network(f"0.0.0.0/{mask}").prefixlen
            out.setdefault(name, []).append(f"{m.group(1)}/{bits}")
    return out


def default_source() -> str | None:
    """The source address of the default route (a UDP socket's connect sends nothing)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            return s.getsockname()[0]
    except OSError:
        return None


def lan_interfaces(run=None, platform: str = sys.platform, source=default_source) -> list[dict]:
    """The LANs this machine could scan: {name, address, subnet}, the default route's first.
    `subnet` is the interface's network when it is a /24 or smaller, else the /24 around the
    address."""
    if platform == "darwin":
        addrs = _ifconfig((run or linktest._run)(["ifconfig"]) or "")
    else:
        addrs = linktest.ip_addrs(run)
    first = source() if source else None
    out = []
    for name, lst in sorted(addrs.items()):
        if name.startswith(VIRTUAL):
            continue
        for i in mdns.allowed(lst):
            if i.ip.is_link_local:
                continue
            net = i.network if i.network.prefixlen >= 24 else \
                ipaddress.IPv4Network(f"{i.ip}/24", strict=False)
            out.append({"name": name, "address": str(i.ip), "subnet": str(net)})
    out.sort(key=lambda r: r["address"] != first)
    return out


def _connect(ip: str, port: int, timeout: float):
    return socket.create_connection((ip, port), timeout=timeout)


def read_banner(s, wait: float = BANNER_S) -> str | None:
    """The first line an SSH server sends, up to 255 bytes; None when it sends none in time."""
    s.settimeout(wait)
    buf = b""
    try:
        while b"\n" not in buf and len(buf) < 255:
            got = s.recv(255 - len(buf))
            if not got:
                break
            buf += got
    except OSError:
        pass
    line = buf.split(b"\n", 1)[0].rstrip(b"\r").decode("ascii", "replace")
    return line or None


# ---------------------------------------------------------------------- the list

class Discovery:
    def __init__(self, fleet, *, browser=None, connect=None, clock=time.time,
                 mono=time.monotonic, sleep=time.sleep, home: str | None = None,
                 interfaces=None, platform: str = sys.platform, spawn=None, own_fp=None,
                 rate: float = RATE_PER_S, inflight: int = INFLIGHT):
        self.fleet = fleet
        self.browser = browser or mdns.Browser(platform=platform)
        self.connect = connect or _connect
        self.clock, self.mono, self.sleep = clock, mono, sleep
        self.home = home or os.path.expanduser("~")
        self.interfaces = interfaces or (lambda: lan_interfaces(platform=platform))
        self.platform = platform
        self.spawn = spawn or (lambda fn: threading.Thread(target=fn, daemon=True).start())
        self.own_fp = own_fp
        self.rate, self.inflight = rate, inflight
        self._lock = threading.RLock()
        self.seq = 0
        self.run = 0
        self.run_seq = 0
        self.sightings: list[dict] = []
        self.rows: dict[str, dict] = {}
        self.gone: dict[str, int] = {}
        self.iface: dict | None = None
        self._ifaces: list[dict] | None = None
        self.mdns = {"state": "idle", "started_at": None, "ended_at": None, "found": 0,
                     "note": None}
        self.ssh_cfg = {"state": "idle", "hosts": 0, "note": None}
        self.lan = "not_needed" if platform != "darwin" else "not_asked"
        self.scan = self._scan_idle()
        self._scan_stop = threading.Event()
        self.connects = 0                        # every connect this controller made: the tests

    # ------------------------------------------------------------------ helpers
    def log(self, msg: str) -> None:
        getattr(self.fleet, "log", print)(msg)

    def _own_fp(self) -> str | None:
        if self.own_fp is not None:
            return self.own_fp() if callable(self.own_fp) else self.own_fp
        try:
            return self.fleet.trust().fingerprint()
        except Exception:                                            # noqa: BLE001
            return None

    def _known(self):
        paths = [os.path.join(self.home, ".ssh", "known_hosts")]
        cfg = getattr(self.fleet, "cfg", None)
        if cfg is not None and getattr(cfg, "path", None):
            paths.append(os.path.join(os.path.dirname(cfg.path), "controller", "known_hosts"))
        return known_hosts(paths)

    def _scan_idle(self) -> dict:
        return {"state": "none", "subnet": None, "interface": None, "question": None,
                "total": 0, "done": 0, "open": 0, "started_at": None, "ended_at": None,
                "rate_per_s": self.rate, "timeout_ms": int(TIMEOUT_S * 1000), "by": None}

    def _ask(self) -> None:
        """The scan's question for the chosen interface (or the default route's)."""
        if self.scan["state"] == "running":
            return
        q = self._scan_idle()
        if self.lan == "denied":
            q["state"] = "denied"
        if self.iface is not None:
            q.update(subnet=self.iface["subnet"], interface=self.iface["name"],
                     question=f"Scan {self.iface['subnet']} for SSH?")
            if q["state"] != "denied":
                q["state"] = "ask"
        self.scan = q

    # ------------------------------------------------------------------ sightings
    def _add(self, s: dict) -> None:
        with self._lock:
            s.setdefault("at", self.clock())
            s.setdefault("run", self.run)
            self.sightings.append(s)
            self._rebuild()

    def _rebuild(self) -> None:
        self.seq += 1
        known = self._known()
        own = self._own_fp()
        fleet_nodes = []
        cfg = getattr(self.fleet, "cfg", None)
        if hasattr(self.fleet, "ensure_local"):
            self.fleet.ensure_local()            # this machine is a node from the first read
        if cfg is not None:
            fleet_nodes = [(n["id"], (n.get("address") or "").split(":")[0])
                           for n in cfg.nodes()]
        groups = merge(self.sightings, known)
        new = {}
        for g in groups:
            row = build_row(g, own, fleet_nodes)
            prev = self.rows.get(row["id"])
            body = {k: v for k, v in row.items() if k not in ("seq", "seen_at")}
            if prev is not None and {k: v for k, v in prev.items()
                                     if k not in ("seq", "seen_at")} == body:
                row["seq"] = prev["seq"]
            else:
                row["seq"] = self.seq
            new[row["id"]] = row
        for rid in self.rows:
            if rid not in new:
                self.gone[rid] = self.seq
        for rid in new:
            self.gone.pop(rid, None)
        self.rows = new

    def learn(self, host: str, port: int = 22, *, dmi: dict | None = None,
              installed: bool | None = None, owned: bool = False,
              host_key: str | None = None, node: str | None = None) -> None:
        """What a pairing found out about `host`: the DMI an SSH login read, TandemNode installed
        or not, owned by another TandemApp (409), its host key, the node id it paired as."""
        s = {"source": "login" if dmi is not None or installed is not None else "pair",
             "host": host, "port": port, "dmi": dmi, "installed": installed, "owned": owned,
             "hk": host_key, "node_id": node}
        try:
            ipaddress.IPv4Address(host)
            s["addresses"] = [host]
        except ValueError:
            s["addresses"], s["name"] = [], host
        self._add(s)

    # ------------------------------------------------------------------ the snapshot
    def snapshot(self, since=None) -> dict:
        with self._lock:
            if self._ifaces is None:
                self._choose(None)
            full = since is None or since < self.run_seq
            rows = sorted(self.rows.values(), key=_order)
            if not full:
                rows = [r for r in rows if r["seq"] > since]
            gone = [] if full else sorted(k for k, v in self.gone.items() if v > since)
            ln = self.lan
            return {"contract_version": CONTRACT, "run": self.run, "cursor": self.seq,
                    "full": full, "rows": rows, "gone": gone,
                    "mdns": dict(self.mdns, types=list(TYPES)),
                    "scan": dict(self.scan),
                    "ssh_config": dict(self.ssh_cfg),
                    "local_network": {"state": ln, "text": LAN_TEXT[ln],
                                      "settings": SETTINGS if self.platform == "darwin"
                                      else None},
                    "interfaces": [dict(i) for i in self._ifaces]}

    def _choose(self, name: str | None) -> None:
        self._ifaces = self.interfaces() or []
        if name is not None:
            got = next((i for i in self._ifaces if i["name"] == name), None)
            if got is None:
                raise DiscoveryError(400, "bad_request", f"no LAN interface {name!r} (one of "
                                     f"{', '.join(i['name'] for i in self._ifaces) or 'none'})")
            self.iface = got
        else:
            self.iface = self._ifaces[0] if self._ifaces else None
        self._ask()

    # ------------------------------------------------------------------ a run
    def start(self, body, who: str) -> dict:
        if body is None:
            body = {}
        if not isinstance(body, dict) or set(body) - {"interface"}:
            raise DiscoveryError(400, "bad_request", "the body is {interface?}")
        name = body.get("interface")
        if name is not None and not isinstance(name, str):
            raise DiscoveryError(400, "bad_request", "interface is an interface's name")
        with self._lock:
            self._scan_stop.set()
            self.run += 1
            self.seq += 1
            self.run_seq = self.seq
            self.sightings = [s for s in self.sightings if s["source"] in LEARNED]
            self.scan["state"] = "none"
            self._choose(name)
            self.mdns.update(state="browsing", started_at=self.clock(), ended_at=None, found=0,
                             note=None)
            self._read_ssh_config()
            self._rebuild()
            run = self.run
        self.spawn(lambda: self._browse(run))
        return self.snapshot()

    def _read_ssh_config(self) -> None:
        path = os.path.join(self.home, ".ssh", "config")
        if not os.path.isfile(path):
            self.ssh_cfg = {"state": "none", "hosts": 0, "note": "no ~/.ssh/config"}
            return
        try:
            hosts = ssh_config(self.home)
        except (OSError, ValueError) as exc:
            self.ssh_cfg = {"state": "failed", "hosts": 0, "note": str(exc)[:200]}
            return
        for h in hosts:
            s = {"source": "ssh_config", "config": h, "addresses": [], "name": None,
                 "at": self.clock(), "run": self.run}
            try:
                ipaddress.IPv4Address(h["hostname"])
                s["addresses"] = [h["hostname"]]
            except ValueError:
                s["name"] = h["hostname"]
            self.sightings.append(s)
        self.ssh_cfg = {"state": "read", "hosts": len(hosts), "note": None}

    def _browse(self, run: int) -> None:
        iface = self.iface["address"] if self.iface else None

        def found(svc):
            with self._lock:
                if self.run != run:
                    return
                self.mdns["found"] += 1
            self._add(mdns_sighting(svc, run))
        try:
            self.browser.browse(list(TYPES), BROWSE_S, iface=iface, on_found=found)
        except mdns.LanDenied:
            with self._lock:
                if self.run == run:
                    self.lan = "denied"
                    self.mdns.update(state="denied", ended_at=self.clock(),
                                     note="macOS keeps this app off the local network")
                    self._ask()
                    self.seq += 1
            self.log("[fleet] discovery: macOS denies this app the local network "
                     f"({SETTINGS})")
            return
        except OSError as exc:
            with self._lock:
                if self.run == run:
                    self.mdns.update(state="failed", ended_at=self.clock(),
                                     note=f"no mDNS: {exc}")
                    self.seq += 1
            return
        with self._lock:
            if self.run != run:
                return
            if self.lan in ("not_asked", "denied"):
                self.lan = "granted"
                self._ask()
            self.mdns.update(state="done", ended_at=self.clock())
            self.seq += 1

    def check_lan(self) -> str:
        """App settings' Local Network check (6.3b): one short mDNS browse (on macOS the first one
        shows the system's prompt), the state after it: `not_needed` off macOS, `granted`,
        `denied`, or `unknown` when this machine has no network to send on."""
        with self._lock:
            if self.lan == "not_needed":
                return self.lan
            if self._ifaces is None:
                self._choose(None)
            iface = self.iface["address"] if self.iface else None
        try:
            self.browser.browse([mdns.SERVICE], 1.0, iface=iface)
        except mdns.LanDenied:
            with self._lock:
                self.lan = "denied"
                self._ask()
                self.seq += 1
            return "denied"
        except OSError:
            return "unknown"
        with self._lock:
            self.lan = "granted"
            self._ask()
            self.seq += 1
        return "granted"

    # ------------------------------------------------------------------ the scan
    def consent(self, body, who: str) -> dict:
        if not isinstance(body, dict) or set(body) != {"subnet"} or \
                not isinstance(body.get("subnet"), str):
            raise DiscoveryError(400, "bad_request", "the body is {subnet}: the subnet the "
                                                     "question names")
        with self._lock:
            if self._ifaces is None:
                self._choose(None)
            st = self.scan
            if st["state"] == "running":
                raise DiscoveryError(409, "busy", f"a scan of {st['subnet']} is running")
            if self.lan == "denied":
                raise DiscoveryError(409, "lan_denied", "macOS keeps this app off the local "
                                     f"network: turn it on in {SETTINGS}, then find again")
            if st["subnet"] is None:
                raise DiscoveryError(409, "conflict", "this machine has no LAN to scan")
            if body["subnet"] != st["subnet"]:
                raise DiscoveryError(400, "bad_request", f"the consent names {body['subnet']}; "
                                     f"the question is about {st['subnet']}")
            net = ipaddress.IPv4Network(st["subnet"])
            hosts = [str(h) for h in net.hosts()]
            self.sightings = [s for s in self.sightings if s["source"] != "scan"]
            self._scan_stop = threading.Event()
            self.scan = dict(st, state="running", total=len(hosts), done=0, open=0,
                             started_at=self.clock(), ended_at=None, by=who)
            self._rebuild()
            run, stop = self.run, self._scan_stop
        cfg = getattr(self.fleet, "cfg", None)
        if cfg is not None and hasattr(cfg, "log_audit"):
            cfg.log_audit(who, "scan", "discovery", str(net),
                          detail={"text": f"LAN scan of {net} for SSH (port 22)"})
        self.log(f"[fleet] discovery: {who} said yes to a scan of {net} for SSH "
                 f"({self.rate:g}/s, {int(TIMEOUT_S * 1000)} ms)")
        self.spawn(lambda: self._scan(net, hosts, run, stop))
        return self.snapshot()

    def stop_scan(self, who: str) -> dict:
        with self._lock:
            if self.scan["state"] == "running":
                self._scan_stop.set()
                self.log(f"[fleet] discovery: {who} stopped the scan of {self.scan['subnet']}")
        return self.snapshot()

    def _scan(self, net, hosts: list[str], run: int, stop: threading.Event) -> None:
        gap = 1.0 / self.rate
        slots = threading.BoundedSemaphore(self.inflight)
        pending = threading.Semaphore(0)
        started = 0
        nxt = self.mono()
        for ip in hosts:
            if stop.is_set():
                break
            if ipaddress.IPv4Address(ip) not in net:      # never outside the consent
                continue
            now = self.mono()
            if nxt > now:
                self.sleep(nxt - now)
            nxt = max(nxt, now) + gap
            slots.acquire()
            started += 1

            def probe(ip=ip):
                try:
                    self._probe(ip, run)
                finally:
                    slots.release()
                    pending.release()
            self.spawn(probe)
        for _ in range(started):
            pending.acquire()
        with self._lock:
            if self.run != run or self.scan["state"] != "running":
                return
            self.scan.update(state="stopped" if stop.is_set() else "done",
                             ended_at=self.clock())
            self.seq += 1
            took = self.scan["ended_at"] - self.scan["started_at"]
            self.log(f"[fleet] discovery: the scan of {net} {self.scan['state']}: "
                     f"{self.scan['done']} addresses, {self.scan['open']} with SSH, {took:.1f} s")

    def _probe(self, ip: str, run: int) -> None:
        banner, opened = None, False
        try:
            with self._lock:
                self.connects += 1
            s = self.connect(ip, SCAN_PORT, TIMEOUT_S)
        except OSError:
            s = None
        if s is not None:
            opened = True
            try:
                banner = read_banner(s)
            finally:
                try:
                    s.close()
                except OSError:
                    pass
        with self._lock:
            if self.run != run:
                return
            self.scan["done"] += 1
            if opened:
                self.scan["open"] += 1
        if opened:
            self._add({"source": "scan", "addresses": [ip], "banner": banner,
                       "port": SCAN_PORT, "run": run})

    # ------------------------------------------------------------------ one address
    def by_address(self, body, who: str) -> dict:
        if not isinstance(body, dict) or set(body) - {"address", "port"} or "address" not in body:
            raise DiscoveryError(400, "bad_request", "the body is {address, port?}")
        try:
            ip = ipaddress.IPv4Address(str(body["address"]))
        except ValueError:
            raise DiscoveryError(400, "bad_request", "address is an IPv4 address, not "
                                                     f"\"{body['address']}\"")
        port = body.get("port", SCAN_PORT)
        if not isinstance(port, int) or isinstance(port, bool) or not 0 < port < 65536:
            raise DiscoveryError(400, "bad_request", "port is a whole number from 1 to 65535")
        if ip.is_unspecified or ip.is_multicast or ip.is_loopback:
            raise DiscoveryError(400, "bad_request", f"{ip} is no device's address")
        banner, ssh_open, blocked = None, False, False
        agent = {}
        for p in (port,) + AGENT_PORTS:
            try:
                with self._lock:
                    self.connects += 1
                s = self.connect(str(ip), p, TIMEOUT_S * 3)
            except OSError as exc:
                if self.platform == "darwin" and exc.errno in mdns.DENIED:
                    blocked = True
                if p != port:
                    agent[p] = False
                continue
            try:
                if p == port:
                    ssh_open = True
                    banner = read_banner(s)
                else:
                    agent[p] = True
            finally:
                try:
                    s.close()
                except OSError:
                    pass
        if blocked:
            with self._lock:
                self.lan = "denied"
                self._ask()
        self._add({"source": "address", "addresses": [str(ip)], "banner": banner,
                   "port": port, "ssh_open": ssh_open, "window": agent.get(7701, False),
                   "agent_lan": agent.get(7700, False), "blocked": blocked})
        with self._lock:
            row = next(r for r in self.rows.values() if str(ip) in r["addresses"])
        return {"contract_version": CONTRACT, "row": row}

    # ------------------------------------------------------------------ the routes
    def handle(self, method: str, parts: list[str], q: dict, body, who: str) -> dict:
        sub = parts[1:]
        if not sub and method == "GET":
            since = q.get("since")
            if since is not None:
                try:
                    since = int(since)
                except ValueError:
                    raise DiscoveryError(400, "bad_request", f"since is a cursor (a whole "
                                         f"number), not \"{since}\"")
            return self.snapshot(since)
        if not sub and method == "POST":
            return self.start(body, who)
        if sub == ["scan"] and method == "POST":
            return self.consent(body, who)
        if sub == ["scan"] and method == "DELETE":
            return self.stop_scan(who)
        if sub == ["address"] and method == "POST":
            return self.by_address(body, who)
        if sub == ["target"] and method == "POST":
            if not isinstance(body, dict) or set(body) != {"line"}:
                raise DiscoveryError(400, "bad_request", "the body is {line}: an ssh command")
            try:
                return {"contract_version": CONTRACT, "target": parse_ssh_line(body["line"])}
            except ValueError as exc:
                raise DiscoveryError(400, "bad_request", str(exc))
        raise DiscoveryError(404, "not_found", f"no route {method} /v1/fleet/"
                                               f"{'/'.join(parts)}")


LAN_TEXT = {
    "not_needed": "this machine asks no permission for the local network",
    "not_asked": "macOS asks once, at the first search: allow it to find Sparks",
    "granted": "allowed: this app can search the local network",
    "denied": "macOS keeps this app off the local network: Find lists nothing it would hear "
              f"there; add by address still works. Turn it on in {SETTINGS}",
}


def _order(r: dict) -> tuple:
    return (KINDS.index(r["kind"]), [int(x) for a in r["addresses"][:1] for x in a.split(".")]
            or [999], r["name"] or "", r["id"])


# ---------------------------------------------------------------------- sightings to rows

def mdns_sighting(svc: dict, run: int) -> dict:
    host = (svc.get("host") or "").lower()
    host = host[:-6] if host.endswith(".local") else host
    s = {"source": "mdns", "type": svc["type"], "addresses": list(svc["addresses"]),
         "name": None, "run": run}
    if svc["type"] == mdns.SERVICE:
        t = svc.get("txt") or {}
        s["name"] = svc.get("instance")
        s["node"] = {"id": t.get("id"), "class": t.get("class") or "spark",
                     "version": t.get("ver") or None, "hw": t.get("hw") or None,
                     "paired": t.get("paired") == "1", "owner": t.get("owner") or None,
                     "listen": t.get("listen") or "loopback",
                     "window": int(t["window"]) if (t.get("window") or "").isdigit() else None,
                     "port": svc.get("port"), "virtual": bool(t.get("virtual"))}
        s["hk"] = t.get("hk") or None
    else:
        s["name"] = host or svc.get("instance")
        if svc["type"].startswith("_ssh."):
            s["ssh_port"] = svc.get("port")
    return s


def _keys(s: dict, known) -> set:
    keys = set()
    nid = (s.get("node") or {}).get("id") or s.get("node_id")
    if nid:
        keys.add("id:" + nid)
    for a in s.get("addresses") or []:
        keys.add("ip:" + a)
    names = []
    if s.get("name") and s["source"] in ("mdns", "ssh_config", "login", "pair"):
        n = s["name"].lower()
        n = n[:-6] if n.endswith(".local") else n
        if s["source"] != "mdns" or s.get("type") != mdns.SERVICE:
            keys.add("name:" + n)
        names += [s["name"], n + ".local"]
    hk = s.get("hk")
    if not hk:
        cfg = s.get("config") or {}
        port = cfg.get("port") or s.get("port") or 22
        hk = host_key_of(known, names + list(s.get("addresses") or []) +
                         [cfg.get("alias"), cfg.get("hostname")], port)
        s["hk_known"] = hk
    if hk:
        keys.add("hk:" + hk)
    return keys


def merge(sightings: list[dict], known) -> list[list[dict]]:
    """Sightings that share a key, as groups; never two node ids in one group."""
    groups: list[dict] = []                      # {keys, ids, members}
    order = sorted(range(len(sightings)), key=lambda i: (
        0 if (sightings[i].get("node") or {}).get("id") and
        not sightings[i]["node"].get("virtual") else
        1 if (sightings[i].get("node") or {}).get("id") else 2, i))
    for i in order:
        s = sightings[i]
        keys = _keys(s, known)
        nid = (s.get("node") or {}).get("id") or s.get("node_id")
        g = None
        for cand in groups:
            if keys & cand["keys"] and not (nid and cand["ids"] and nid not in cand["ids"]):
                g = cand
                break
        if g is None:
            g = {"keys": set(), "ids": set(), "members": []}
            groups.append(g)
        g["keys"] |= keys
        if nid:
            g["ids"].add(nid)
        g["members"].append(s)
    # a later sighting may join two groups that were apart
    changed = True
    while changed:
        changed = False
        for a in range(len(groups)):
            for b in range(a + 1, len(groups)):
                ga, gb = groups[a], groups[b]
                if ga["keys"] & gb["keys"] and not (ga["ids"] and gb["ids"]
                                                    and ga["ids"] != gb["ids"]):
                    ga["keys"] |= gb["keys"]
                    ga["ids"] |= gb["ids"]
                    ga["members"] += gb["members"]
                    del groups[b]
                    changed = True
                    break
            if changed:
                break
    return [g["members"] for g in groups]


def build_row(members: list[dict], own_fp: str | None, fleet_nodes: list) -> dict:
    by = lambda src: [m for m in members if m["source"] == src]          # noqa: E731
    addrs = sorted({a for m in members for a in m.get("addresses") or []},
                   key=lambda a: [int(x) for x in a.split(".")])
    tn = next((m for m in by("mdns") if m.get("node")), None)
    node = dict(tn["node"]) if tn else None
    if node is not None:
        o = node.pop("owner")
        node["owner"] = None if not node["paired"] else \
            "this" if o and own_fp and own_fp.lower().startswith(o.lower()) else "other"
    cfgs = [m["config"] for m in by("ssh_config")]
    names = [m.get("name") for m in by("mdns") if m.get("name")] + \
        [c["alias"] for c in cfgs]
    name = names[0] if names else None
    banners = [m for m in by("scan") + by("address") if m.get("banner")]
    banner = banners[-1]["banner"] if banners else None
    hk = next((m.get("hk") or m.get("hk_known") for m in members
               if m.get("hk") or m.get("hk_known")), None)
    opened = by("scan") + [m for m in by("address") if m.get("ssh_open")]
    ssh_port = next((m.get("port") for m in opened), None) or \
        next((m.get("ssh_port") for m in by("mdns") if m.get("ssh_port")), None) or \
        (cfgs[0]["port"] if cfgs else None)
    seen_ssh = bool(opened or ssh_port or by("login"))
    verdict, os_name = banner_os(banner)
    logins = [m for m in by("login") if m.get("dmi")]
    dmi = logins[-1]["dmi"] if logins else None
    hw = None
    if dmi:
        got = classify_dmi(dmi.get("vendor"), dmi.get("product"), dmi.get("version"),
                           dmi.get("family"))
        if got:
            hw = {"label": got[0], "spark": got[1], "source": "dmi"}
    if hw is None and node and node.get("hw"):
        hw = {"label": node["hw"], "spark": node["class"] == "spark", "source": "mdns"}
    fleet = None
    ids = {m["node"]["id"] for m in by("mdns") if m.get("node") and m["node"].get("id")}
    ids |= {m["node_id"] for m in members if m.get("node_id")}
    for nid, addr in fleet_nodes:
        if nid in ids or (not ids and addr and addr in addrs):
            fleet = nid
            break
    owned = any(m.get("owned") for m in members) or (node is not None
                                                      and node["owner"] == "other")
    installed = next((m["installed"] for m in reversed(by("login"))
                      if m.get("installed") is not None), None)
    evidence = []
    model = (hw or {}).get("label") or "Spark"
    if node is not None:
        evidence.append(f"mDNS: TandemNode {node['id']}" + (
            f", TandemLLM {node['version']}" if node["version"] else "") +
            (f", {node['hw']}" if node["hw"] else "") +
            (", paired" if node["paired"] else ", not paired"))
    if dmi:
        evidence.append("DMI over SSH: " + " · ".join(
            v for v in (dmi.get("vendor"), dmi.get("product"), dmi.get("family")) if v))
    if banner:
        evidence.append(f"SSH banner: {banner[:80]} ({os_name})")
    for c in cfgs:
        evidence.append(f"~/.ssh/config: Host {c['alias']}" + (
            f" (via {c['proxy_jump']})" if c["proxy_jump"] else ""))
    if any(m.get("owned") for m in members):
        evidence.append("pairing: owned by another TandemApp")
    for m in by("mdns"):
        if not m.get("node"):
            evidence.append(f"mDNS: {m['type'].split('.')[0]} {m.get('name')}")
    if any(m.get("blocked") for m in by("address")):
        evidence.append(f"macOS blocked the connection: Local Network is off ({SETTINGS})")
    connect: list[str] = []
    if owned:
        kind = "owned"
        title = "Spark owned by another TandemApp"
        detail = f"{model}" + (f" · TandemLLM {node['version']}" if node and node["version"]
                               else "") + " · take it over on the node, or over SSH"
        connect = ["takeover"]
    elif node is not None and node["class"] == "spark":
        kind = "spark"
        ver = node["version"] or "?"
        title = f"Spark with TandemLLM {ver}"
        state = ("in this fleet" if fleet else "paired with this TandemApp"
                 if node["owner"] == "this" else "pairing code showing on the node"
                 if node["window"] else "not paired")
        detail = f"{model} · TandemLLM {ver} · {state}"
        connect = [] if fleet else ["code", "ssh"]
    elif node is not None:
        kind = "host"
        title = "Linux host, not a Spark"
        detail = f"TandemLLM {node['version'] or '?'} on {node['hw'] or 'another machine'}"
    elif fleet or (installed and hw is not None and hw["spark"]):
        # paired, or an SSH login found TandemNode there; its version comes with its TXT
        kind, title = "spark", "Spark with TandemLLM"
        detail = f"{model} · " + ("in this fleet" if fleet else "TandemNode installed")
        connect = [] if fleet else ["ssh"]
    elif hw is not None and hw["spark"]:
        kind = "gb10"
        title = "GB10 box, nothing installed"
        detail = f"{hw['label']} · " + ("TandemNode is not installed" if installed is False
                                        else "no TandemNode answered")
        connect = ["ssh"]
    elif hw is not None:
        kind, title = "host", "Linux host, not a Spark"
        detail = f"{hw['label']} (its DMI, read over SSH)"
    elif verdict == "not_spark":
        kind = "host"
        title = ("Windows host, not a Spark" if os_name == "Windows" else
                 "Linux host, not a Spark" if os_name.startswith(("Linux", "Ubuntu")) else
                 "Host, not a Spark")
        detail = f"{os_name} (its SSH banner): DGX OS is Ubuntu 24.04 or later"
    else:
        kind, title = "unknown", "Unknown device"
        if verdict == "candidate":
            detail = f"{os_name}, as DGX OS: maybe a Spark; sign in once over SSH to tell"
        elif cfgs:
            c = cfgs[0]
            detail = f"from ~/.ssh/config ({c['alias']})" + (
                f" via {c['proxy_jump']}" if c["proxy_jump"] else "") + ": not checked"
        elif by("scan") and not banner:
            detail = "port 22 is open but sent no SSH banner"
        elif any(m.get("blocked") for m in by("address")):
            detail = "macOS blocked the connection: turn on Local Network for this app"
        elif by("address") and not seen_ssh:
            detail = "no SSH server answers there"
        else:
            detail = "announces itself over mDNS; sign in once over SSH to tell"
        connect = ["ssh"] if seen_ssh or cfgs or by("mdns") else []
    if kind in ("unknown", "gb10") and name and NAME_HINT.search(name) and node is None \
            and hw is None:
        detail += " · its name suggests a Spark"
    key = sorted(_row_keys(members, node, hk, addrs, name))[0]
    return {
        "id": "d-" + hashlib.sha256(key.encode()).hexdigest()[:12],
        "kind": kind, "title": title, "detail": detail, "name": name, "addresses": addrs,
        "sources": sorted({m["source"] for m in members}, key=SOURCES.index),
        "node": node, "hw": hw,
        "ssh": None if not (seen_ssh or hk) else {
            "port": ssh_port or 22, "banner": banner,
            "os": os_name if banner else None, "host_key": hk},
        "ssh_config": cfgs[0] if cfgs else None,
        "fleet": fleet, "connect": connect, "evidence": evidence,
        "seen_at": round(max(m["at"] for m in members), 3), "seq": 0}


SOURCES = ("mdns", "scan", "ssh_config", "address", "login", "pair")


def _row_keys(members, node, hk, addrs, name) -> list[str]:
    """The row's id comes from its strongest key: the node id, the host key, an address, a name."""
    if node and node.get("id"):
        return ["0" + node["id"]]
    if hk:
        return ["1" + hk]
    if addrs:
        return ["2" + addrs[0]]
    return ["3" + (name or "?")]
