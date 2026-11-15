"""The engine host on the LAN: a minimal mDNS/DNS-SD responder for `_tandem._tcp` (RFC 6762,
RFC 6763), answered by the engine process in setup mode and while serving. Standard library only.

WHAT IT ANSWERS, for one service instance, and nothing else:

    _services._dns-sd._udp.local  PTR  _tandem._tcp.local
    _tandem._tcp.local            PTR  <name>._tandem._tcp.local
    <name>._tandem._tcp.local     SRV  0 0 <port> <name>-tandem.local
    <name>._tandem._tcp.local     TXT  name=<host name> version=<release> port=<port> fp=SHA256:...
    <name>-tandem.local           A    each address the engine listens on for other machines

`fp` is the SSH host key's fingerprint, the one `tandem pair` prints, so TandemApp can show it
beside the engine it found. Nothing else goes out: no code, no token, no key, no path. The target
host name is the responder's own (`<name>-tandem.local`), never the host's `<name>.local`, so an
avahi or a systemd-resolved on the same host keeps its records and sees no conflict.

HOW. One UDP socket on 0.0.0.0:5353 with SO_REUSEADDR and SO_REUSEPORT (what avahi sets too: the
kernel then hands each multicast datagram to every socket on the port), joined to 224.0.0.251 on
each address. At start it announces twice, a second apart; at stop it sends the goodbye (TTL 0).
A query is answered by multicast, or by unicast when it asks for one (the QU bit) or comes from a
port other than 5353 (a legacy resolver: its id and question echoed, TTLs at most 10 s). An answer
the asker lists as known (at least half its TTL left) is not sent again. No probing: the instance is
the host's name, which another engine host on the same LAN does not share.

When the port cannot be shared (another program holds 5353 without SO_REUSEADDR) the service is
registered through avahi-publish-service when it is installed, and is off otherwise: the start says
which in one line. `QSE_ADVERTISE=off` switches it off; an engine bound to loopback does not
advertise (nothing outside the host could reach it). IPv4 only.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import threading
import time

GROUP = "224.0.0.251"
PORT = 5353
SERVICE = "_tandem._tcp.local"
META = "_services._dns-sd._udp.local"
A, PTR, TXT, SRV, ANY = 1, 12, 16, 33, 255
IN, FLUSH, QU = 1, 0x8000, 0x8000
TTL_HOST = 120
TTL_OTHER = 4500
LEGACY_TTL = 10
OFF = ("off", "0", "no", "false")


# ---------------------------------------------------------------------------------------- the wire
def encode_name(name: str) -> bytes:
    out = b""
    for label in name.rstrip(".").split("."):
        b = label.encode("utf-8")[:63]
        out += bytes([len(b)]) + b
    return out + b"\0"


def read_name(buf: bytes, off: int) -> tuple[str, int]:
    """A (possibly compressed) name at `off`: (name, the offset after it)."""
    labels, end, jumps = [], None, 0
    while True:
        if off >= len(buf):
            raise ValueError("a name runs past the packet")
        n = buf[off]
        if n & 0xC0 == 0xC0:
            if off + 1 >= len(buf) or jumps > 32:
                raise ValueError("a bad name pointer")
            if end is None:
                end = off + 2
            off = ((n & 0x3F) << 8) | buf[off + 1]
            jumps += 1
            continue
        if n == 0:
            off += 1
            break
        labels.append(buf[off + 1:off + 1 + n].decode("utf-8", "replace"))
        off += 1 + n
    return ".".join(labels), (end if end is not None else off)


def parse(packet: bytes) -> dict:
    """{id, flags, questions: [(name, type, unicast)], answers: [(name, type, ttl, rdata)]};
    ValueError for a packet that does not read."""
    if len(packet) < 12:
        raise ValueError("short packet")
    pid, flags, qd, an, ns, ar = struct.unpack(">6H", packet[:12])
    off, qs, ans = 12, [], []
    for _ in range(qd):
        name, off = read_name(packet, off)
        if off + 4 > len(packet):
            raise ValueError("short question")
        qt, qc = struct.unpack(">HH", packet[off:off + 4])
        off += 4
        qs.append((name, qt, bool(qc & QU)))
    for _ in range(an + ns + ar):
        name, off = read_name(packet, off)
        if off + 10 > len(packet):
            raise ValueError("short record")
        rt, _rc, ttl, rl = struct.unpack(">HHIH", packet[off:off + 10])
        off += 10
        rstart = off
        rdata = packet[off:off + rl]
        if len(rdata) != rl:
            raise ValueError("short rdata")
        off += rl
        ans.append((name, rt, ttl, rdata, rstart))
    return {"id": pid, "flags": flags, "questions": qs, "answers": ans, "raw": packet}


def rdata_value(packet: bytes, rtype: int, rstart: int, rdata: bytes):
    """A record's data, decoded: a name (PTR), (prio, weight, port, target) (SRV), [strings] (TXT),
    an address (A), else the bytes."""
    if rtype == PTR:
        return read_name(packet, rstart)[0]
    if rtype == SRV:
        prio, weight, port = struct.unpack(">HHH", rdata[:6])
        return prio, weight, port, read_name(packet, rstart + 6)[0]
    if rtype == TXT:
        out, i = [], 0
        while i < len(rdata):
            n = rdata[i]
            out.append(rdata[i + 1:i + 1 + n].decode("utf-8", "replace"))
            i += 1 + n
        return out
    if rtype == A and len(rdata) == 4:
        return socket.inet_ntoa(rdata)
    return rdata


def _norm(v):
    """A decoded rdata for comparison: names in lower case, lists as tuples."""
    if isinstance(v, str):
        return v.lower()
    if isinstance(v, (list, tuple)):
        return tuple(x.lower() if isinstance(x, str) and i == len(v) - 1 and not isinstance(v, list) else x
                     for i, x in enumerate(v))
    return v


def _record(name: str, rtype: int, ttl: int, rdata: bytes, unique: bool) -> bytes:
    return encode_name(name) + struct.pack(">HHIH", rtype, IN | (FLUSH if unique else 0), ttl, len(rdata)) + rdata


def _txt(pairs: list[tuple[str, str]]) -> bytes:
    out = b""
    for k, v in pairs:
        b = f"{k}={v}".encode("utf-8")[:255]
        out += bytes([len(b)]) + b
    return out


def label(text: str) -> str:
    """A DNS label from a host name: letters, digits and hyphens, at most 40 characters."""
    s = re.sub(r"[^A-Za-z0-9-]+", "-", text).strip("-")[:40]
    return s or "engine"


# ---------------------------------------------------------------------------------------- the records
class Advert:
    """What is advertised: the instance, its target, its port, its TXT and its addresses."""

    def __init__(self, name: str, port: int, version: str, fingerprint: str, addresses: list[str]):
        self.name = label(name)
        self.instance = f"{self.name}.{SERVICE}"
        self.target = f"{self.name}-tandem.local"
        self.port = int(port)
        self.txt = [("name", name[:63]), ("version", (version or "unknown")[:32]), ("port", str(self.port)),
                    ("fp", fingerprint)]
        self.addresses = [a for a in addresses if a]

    def rrs(self) -> dict:
        """kind -> [(name, type, ttl, rdata, unique)]"""
        return {
            "meta": [(META, PTR, TTL_OTHER, encode_name(SERVICE), False)],
            "ptr": [(SERVICE, PTR, TTL_OTHER, encode_name(self.instance), False)],
            "srv": [(self.instance, SRV, TTL_HOST, struct.pack(">HHH", 0, 0, self.port) + encode_name(self.target),
                     True)],
            "txt": [(self.instance, TXT, TTL_OTHER, _txt(self.txt), True)],
            "a": [(self.target, A, TTL_HOST, socket.inet_aton(ip), True) for ip in self.addresses],
        }


def _message(pid: int, questions: list[bytes], answers: list, additional: list, legacy: bool, ttl0: bool = False) -> bytes:
    def enc(rr):
        name, rtype, ttl, rdata, unique = rr
        if ttl0:
            ttl = 0
        elif legacy:
            ttl = min(ttl, LEGACY_TTL)
        return _record(name, rtype, ttl, rdata, unique and not legacy)
    body = b"".join(questions) + b"".join(enc(r) for r in answers) + b"".join(enc(r) for r in additional)
    return struct.pack(">6H", pid, 0x8400, len(questions), len(answers), 0, len(additional)) + body


class Responder:
    """The answers of one advert. `answer` is pure: a packet in, [(packet, destination)] out."""

    def __init__(self, advert: Advert):
        self.advert = advert

    def announcement(self, goodbye: bool = False) -> bytes:
        r = self.advert.rrs()
        return _message(0, [], r["ptr"] + r["srv"] + r["txt"] + r["a"], [], False, ttl0=goodbye)

    def _known(self, q: dict) -> set:
        """(name, type, rdata) the asker lists as known with at least half the TTL left."""
        out = set()
        ours = {(n.lower(), t): x for n, t, x, _d, _u in sum(self.advert.rrs().values(), [])}
        for name, rtype, ttl, rdata, rstart in q["answers"]:
            full = ours.get((name.lower(), rtype))
            if full is not None and ttl >= full // 2:
                out.add((name.lower(), rtype, _norm(rdata_value(q["raw"], rtype, rstart, rdata))))
        return out

    def answer(self, packet: bytes, src: tuple) -> list[tuple[bytes, tuple]]:
        try:
            q = parse(packet)
        except (ValueError, struct.error, IndexError):
            return []
        if q["flags"] & 0x8000 or not q["questions"]:
            return []                                     # a response, or no question
        r = self.advert.rrs()
        known = self._known(q) if q["answers"] else set()
        legacy = src[1] != PORT
        answers, additional, asked, unicast = [], [], [], legacy
        for name, qt, qu in q["questions"]:
            n = name.lower()
            hit = []
            if n == META.lower() and qt in (PTR, ANY):
                hit = r["meta"]
            elif n == SERVICE.lower() and qt in (PTR, ANY):
                hit = r["ptr"]
                additional += r["srv"] + r["txt"] + r["a"]
            elif n == self.advert.instance.lower() and qt in (SRV, TXT, ANY):
                hit = [x for x in r["srv"] + r["txt"] if qt == ANY or x[1] == qt]
                additional += r["a"]
            elif n == self.advert.target.lower() and qt in (A, ANY):
                hit = r["a"]
            if hit:
                asked.append(encode_name(name) + struct.pack(">HH", qt, IN))
                unicast = unicast or qu
            for rr in hit:
                if self._is_known(rr, known):
                    continue
                if rr not in answers:
                    answers.append(rr)
        if not answers:
            return []
        additional = [x for i, x in enumerate(additional) if x not in answers and x not in additional[:i]]
        if legacy:
            return [(_message(q["id"], asked, answers, additional, True), src)]
        dest = (src[0], PORT) if unicast else (GROUP, PORT)
        return [(_message(0, [], answers, additional, False), dest)]

    def _is_known(self, rr, known: set) -> bool:
        if not known:
            return False
        name, rtype, _ttl, rdata, _u = rr
        pkt = _record(name, rtype, 1, rdata, False)
        start = len(encode_name(name)) + 10
        return (name.lower(), rtype, _norm(rdata_value(pkt, rtype, start, rdata))) in known


# ---------------------------------------------------------------------------------------- the socket
def bind_socket(port: int = PORT, addresses: list[str] | None = None, bind: str = "",
                join: bool = True) -> socket.socket:
    """The responder's UDP socket: shared with other responders (SO_REUSEADDR, SO_REUSEPORT), joined
    to the mDNS group on each address. OSError when the port cannot be shared."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        s.bind((bind, port))
        if join:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
            joined = 0
            for ip in (addresses or []) or ["0.0.0.0"]:
                try:
                    s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                                 socket.inet_aton(GROUP) + socket.inet_aton(ip))
                    joined += 1
                except OSError:
                    continue
            if not joined:
                raise OSError("the mDNS group could not be joined on any address")
    except OSError:
        s.close()
        raise
    return s


class Advertiser:
    """The responder on a socket, in a thread of its own: announce, answer, say goodbye."""

    def __init__(self, advert: Advert, sock: socket.socket, *, multicast: bool = True,
                 group: tuple = (GROUP, PORT), announce_gap_s: float = 1.0):
        self.responder = Responder(advert)
        self.sock = sock
        self.multicast = multicast
        self.group = group
        self.gap = announce_gap_s
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="mdns", daemon=True)
        self.sent = 0

    def _send(self, data: bytes, dest: tuple) -> None:
        if dest[0] == GROUP and self.multicast and self.responder.advert.addresses:
            for ip in self.responder.advert.addresses:
                try:
                    self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
                    self.sock.sendto(data, dest)
                    self.sent += 1
                except OSError:
                    continue
            return
        try:
            self.sock.sendto(data, dest if dest[0] != GROUP else self.group)
            self.sent += 1
        except OSError:
            pass

    def start(self) -> "Advertiser":
        self.thread.start()
        return self

    def _run(self) -> None:
        self.sock.settimeout(0.25)
        next_announce, announced = time.monotonic(), 0
        while not self._stop.is_set():
            if announced < 2 and time.monotonic() >= next_announce:
                self._send(self.responder.announcement(), self.group)
                announced += 1
                next_announce = time.monotonic() + self.gap
            try:
                data, src = self.sock.recvfrom(9000)
            except socket.timeout:
                continue
            except OSError:
                if self._stop.is_set():
                    break
                time.sleep(0.25)
                continue
            for out, dest in self.responder.answer(data, src):
                self._send(out, dest)

    def stop(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        self.thread.join(timeout=2.0)
        self._send(self.responder.announcement(goodbye=True), self.group)
        try:
            self.sock.close()
        except OSError:
            pass


class AvahiPublish:
    """The fallback: avahi-publish-service registers the service with the host's avahi-daemon; it
    ends with the engine process (PR_SET_PDEATHSIG) or at stop."""

    def __init__(self, exe: str, advert: Advert):
        self.argv = [exe, "-s", advert.name, "_tandem._tcp", str(advert.port)] + [f"{k}={v}" for k, v in advert.txt]

        def _child():
            os.setsid()
            if sys.platform.startswith("linux"):
                try:
                    import ctypes
                    ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)
                except (OSError, AttributeError):
                    pass
        self.proc = subprocess.Popen(self.argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, preexec_fn=_child)

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def start_from_env(*, port: int, host: str, root: str | None = None, env=None, etc: str | None = None,
                   mdns_port: int = PORT, which=shutil.which):
    """(the advertiser or None, one line on what it does, None for an engine on loopback, which
    advertises nothing and says nothing) for the engine process."""
    from server.pairing import hostkey
    env = os.environ if env is None else env
    if (env.get("QSE_ADVERTISE") or "on").strip().lower() in OFF:
        return None, "off (QSE_ADVERTISE=off)"
    addrs = hostkey.bound_addresses(host)
    if not addrs:
        return None, None              # loopback only: nothing to advertise, nothing to say
    try:
        fp = hostkey.preferred(etc or hostkey.ETC_SSH)["fingerprint"]
    except hostkey.NoHostKey as exc:
        return None, f"off: {exc}"
    version = ""
    try:
        with open(os.path.join(root or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                               "VERSION")) as fh:
            version = fh.read().strip()
    except OSError:
        pass
    advert = Advert(hostkey.hostname(), port, version, fp, addrs)
    try:
        sock = bind_socket(mdns_port, addrs)
    except OSError as exc:
        exe = which("avahi-publish-service")
        if exe:
            try:
                return AvahiPublish(exe, advert), (f"_tandem._tcp as {advert.name} through avahi-publish-service "
                                                   f"(UDP {mdns_port} is not shared: {exc})")
            except OSError as exc2:
                return None, f"off: UDP {mdns_port} is not shared ({exc}) and avahi-publish-service failed ({exc2})"
        return None, (f"off: UDP {mdns_port} is held by a program that does not share it ({exc}) and "
                      "avahi-publish-service is not installed")
    adv = Advertiser(advert, sock).start()
    return adv, f"_tandem._tcp as {advert.name} on {', '.join(addrs)} port {port} (TXT name, version, port, fp)"
