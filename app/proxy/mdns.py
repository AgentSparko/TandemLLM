"""Multicast DNS for discovery (SRV-727): a TandemNode says it is there, a controller asks who is.

    msg = pack(questions=[(name, PTR, IN)], answers=[...], additional=[...])
    m = unpack(data)                          id, flags, questions, records (every section)
    found = Browser().browse([SERVICE, "_ssh._tcp.local"], seconds=3.0)
    r = Responder(info, addrs).start()        the node's answers, on its LAN addresses only
    r.update() / r.stop()                     a new TXT announced / a goodbye

WHY OUR OWN. The standard library has no mDNS, and the two ways to get one are a dependency
(python-zeroconf, which pulls ifaddr and runs an asyncio loop in every agent) or the system's
daemon (avahi-publish on DGX OS, dns-sd on a Mac), whose command-line tools publish on every
interface the daemon has, with no way to keep one service off a WAN address. What discovery needs
is small: a browser that asks one question and reads the answers, and a responder for one service.
Both are here, about 300 lines, with the socket injected so the tests run without a network.

THE BROWSER asks from an ephemeral port (a "legacy unicast" query, RFC 6762 section 6.7): every
responder answers it by unicast to that port, so the controller never binds 5353 and never competes
with mDNSResponder or avahi for it. Three queries one second apart, then one more for the SRV, TXT
and A records of an instance whose first answer lacked them. On macOS the first query is what
shows the Local Network prompt; while the permission is off, sendto fails with EHOSTUNREACH and
the browse raises LanDenied.

THE RESPONDER binds 5353 beside the system's daemon (SO_REUSEADDR on Linux, SO_REUSEPORT too on a
Mac) and joins the group on each address the agent's bind rule allows (proxy/nodetrust.py: LAN,
link-local or fabric, never loopback, never a global address). It answers a query only when the
asker's address is on one of those subnets, with the address it has on that subnet; anything else
(a WAN neighbour, a routed network) gets nothing. Its names are its own: the instance is the
host name, the SRV target `tandemnode-<id>.local`, so it never answers for the host's
`<hostname>.local` and never collides with avahi's records. It announces at start and on every
change of its TXT, and says goodbye (TTL 0) when it stops. No probing: the names carry the node
id, so two nodes never share one.
"""

from __future__ import annotations

import errno
import ipaddress
import select
import socket
import struct
import sys
import threading
import time

GROUP, PORT = "224.0.0.251", 5353
A, PTR, TXT, AAAA, SRV, ANY = 1, 12, 16, 28, 33, 255
IN = 1
TOP = 0x8000                          # rrclass: cache-flush; qclass: unicast response wanted
RESPONSE = 0x8400                     # QR and AA
SERVICE = "_tandemnode._tcp.local"
META = "_services._dns-sd._udp.local"
TTL = 120
LEGACY_TTL = 10
QUERIES_S = (0.0, 1.0, 2.0)
DENIED = (errno.EHOSTUNREACH, errno.EPERM, errno.EACCES)


class LanDenied(OSError):
    """macOS keeps this process off the local network (System Settings, Local Network)."""


# ---------------------------------------------------------------------- the message

def _enc_name(name: str) -> bytes:
    out = b""
    for label in name.rstrip(".").split("."):
        b = label.encode("utf-8")
        if not 0 < len(b) < 64:
            raise ValueError(f"a DNS label is 1 to 63 bytes: {label!r}")
        out += bytes([len(b)]) + b
    return out + b"\0"


def _dec_name(buf: bytes, off: int) -> tuple[str, int]:
    labels, end, jumps = [], None, 0
    while True:
        if off >= len(buf):
            raise ValueError("a name runs past the message")
        n = buf[off]
        if n & 0xC0 == 0xC0:
            if off + 1 >= len(buf) or jumps > 32:
                raise ValueError("a bad compression pointer")
            end = off + 2 if end is None else end
            off, jumps = ((n & 0x3F) << 8) | buf[off + 1], jumps + 1
            continue
        if n == 0:
            return ".".join(labels), (off + 1 if end is None else end)
        if n > 63 or off + 1 + n > len(buf):
            raise ValueError("a label runs past the message")
        labels.append(buf[off + 1:off + 1 + n].decode("utf-8", "replace"))
        off += 1 + n


def txt_bytes(txt: dict) -> bytes:
    out = b""
    for k, v in txt.items():
        s = (k if v is None else f"{k}={v}").encode("utf-8")[:255]
        out += bytes([len(s)]) + s
    return out or b"\0"


def txt_dict(raw: bytes) -> dict:
    out, i = {}, 0
    while i < len(raw):
        n = raw[i]
        s = raw[i + 1:i + 1 + n].decode("utf-8", "replace")
        i += 1 + n
        if s:
            k, sep, v = s.partition("=")
            out.setdefault(k.lower(), v if sep else None)
    return out


class Record:
    """One resource record: `data` is an address (A), a name (PTR), a dict (TXT) or
    (priority, weight, port, target) (SRV); raw bytes for any other type."""

    def __init__(self, name: str, rtype: int, data, ttl: int = TTL, flush: bool = False):
        self.name, self.rtype, self.data, self.ttl, self.flush = name, rtype, data, ttl, flush

    def rdata(self) -> bytes:
        if self.rtype == A:
            return socket.inet_aton(self.data)
        if self.rtype == PTR:
            return _enc_name(self.data)
        if self.rtype == TXT:
            return txt_bytes(self.data)
        if self.rtype == SRV:
            pri, w, port, target = self.data
            return struct.pack(">HHH", pri, w, port) + _enc_name(target)
        return bytes(self.data)

    def pack(self) -> bytes:
        rd = self.rdata()
        return _enc_name(self.name) + struct.pack(">HHIH", self.rtype,
                                                  IN | (TOP if self.flush else 0), self.ttl,
                                                  len(rd)) + rd

    def __repr__(self) -> str:
        return f"Record({self.name!r}, {self.rtype}, {self.data!r}, ttl={self.ttl})"


class Message:
    def __init__(self, id_: int, flags: int, questions: list, records: list):
        self.id, self.flags, self.questions, self.records = id_, flags, questions, records

    @property
    def response(self) -> bool:
        return bool(self.flags & 0x8000)


def pack(*, id_: int = 0, flags: int = 0, questions=(), answers=(), additional=()) -> bytes:
    out = struct.pack(">HHHHHH", id_, flags, len(questions), len(answers), 0, len(additional))
    for name, qtype, qclass in questions:
        out += _enc_name(name) + struct.pack(">HH", qtype, qclass)
    for r in list(answers) + list(additional):
        out += r.pack()
    return out


def unpack(buf: bytes) -> Message:
    """A message; ValueError for one that does not parse."""
    if len(buf) < 12:
        raise ValueError("shorter than a DNS header")
    id_, flags, qd, an, ns, ar = struct.unpack(">HHHHHH", buf[:12])
    off, questions, records = 12, [], []
    for _ in range(qd):
        name, off = _dec_name(buf, off)
        if off + 4 > len(buf):
            raise ValueError("a question runs past the message")
        qtype, qclass = struct.unpack(">HH", buf[off:off + 4])
        questions.append((name, qtype, qclass))
        off += 4
    for _ in range(an + ns + ar):
        name, off = _dec_name(buf, off)
        if off + 10 > len(buf):
            raise ValueError("a record runs past the message")
        rtype, rclass, ttl, n = struct.unpack(">HHIH", buf[off:off + 10])
        off += 10
        if off + n > len(buf):
            raise ValueError("a record's data runs past the message")
        raw = buf[off:off + n]
        if rtype == A and n == 4:
            data = socket.inet_ntoa(raw)
        elif rtype == PTR:
            data = _dec_name(buf, off)[0]
        elif rtype == TXT:
            data = txt_dict(raw)
        elif rtype == SRV and n >= 7:
            data = struct.unpack(">HHH", raw[:6]) + (_dec_name(buf, off + 6)[0],)
        else:
            data = raw
        records.append(Record(name, rtype, data, ttl, bool(rclass & TOP)))
        off += n
    return Message(id_, flags, questions, records)


def _same(a: str, b: str) -> bool:
    return a.rstrip(".").lower() == b.rstrip(".").lower()


# ---------------------------------------------------------------------- the browser

def _browse_socket(iface: str | None) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
    if iface:
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(iface))
    s.bind(("0.0.0.0", 0))
    return s


class Browser:
    """One browse: `sock(iface)` makes the socket (a fake in the tests), `clock` is monotonic."""

    def __init__(self, *, sock=None, clock=time.monotonic, platform: str = sys.platform):
        self.sock = sock or _browse_socket
        self.clock = clock
        self.platform = platform
        self.sent = 0

    def _send(self, s, data: bytes) -> None:
        try:
            s.sendto(data, (GROUP, PORT))
            self.sent += 1
        except OSError as exc:
            if self.platform == "darwin" and exc.errno in DENIED:
                raise LanDenied(exc.errno, "macOS keeps this app off the local network")
            raise

    def browse(self, types: list[str], seconds: float = 3.0, iface: str | None = None,
               on_found=None) -> list[dict]:
        """Every instance of `types` that answered: {type, instance, host, port, txt, addresses,
        src}. `on_found(service)` is called as each one is complete. LanDenied (macOS), OSError
        (no network) before anything is found."""
        s = self.sock(iface)
        ptr: dict[str, str] = {}                 # instance -> its type
        srv: dict[str, tuple] = {}
        txt: dict[str, dict] = {}
        addr: dict[str, set] = {}
        src: dict[str, str] = {}
        told: set = set()
        asked_more = False
        try:
            start = self.clock()
            sends = list(QUERIES_S)
            while True:
                now = self.clock() - start
                while sends and sends[0] <= now:
                    sends.pop(0)
                    self._send(s, pack(questions=[(t, PTR, IN) for t in types]))
                if now >= seconds:
                    break
                if not sends and not asked_more:
                    # one more question for what the first answers left out
                    asked_more = True
                    qs = [(i, SRV, IN) for i in ptr if i not in srv] + \
                         [(i, TXT, IN) for i in ptr if i not in txt] + \
                         [(t[3], A, IN) for t in srv.values() if t[3].lower() not in addr]
                    if qs:
                        self._send(s, pack(questions=qs[:32]))
                wait = min([seconds - now] + [x - now for x in sends])
                s.settimeout(max(0.01, wait))
                try:
                    data, (ip, _) = s.recvfrom(9000)
                except (socket.timeout, BlockingIOError):
                    continue
                try:
                    m = unpack(data)
                except ValueError:
                    continue
                if not m.response:
                    continue
                for r in m.records:
                    if r.rtype == PTR and any(_same(r.name, t) for t in types) and r.ttl:
                        ptr.setdefault(r.data.lower(), next(t for t in types if _same(r.name, t)))
                        src.setdefault(r.data.lower(), ip)
                    elif r.rtype == SRV:
                        srv[r.name.lower()] = r.data
                    elif r.rtype == TXT:
                        txt[r.name.lower()] = r.data
                    elif r.rtype == A:
                        addr.setdefault(r.name.lower().rstrip("."), set()).add(r.data)
                for inst in list(ptr):
                    if inst in told or inst not in srv:
                        continue
                    host = srv[inst][3].lower().rstrip(".")
                    if host not in addr and self.clock() - start < seconds - 0.5:
                        continue                 # its address may still come
                    told.add(inst)
                    if on_found:
                        on_found(self._service(inst, ptr, srv, txt, addr, src))
        finally:
            s.close()
        return [self._service(i, ptr, srv, txt, addr, src) for i in sorted(ptr)]

    @staticmethod
    def _service(inst, ptr, srv, txt, addr, src) -> dict:
        t = ptr[inst]
        pri_w_port_target = srv.get(inst)
        host = pri_w_port_target[3].rstrip(".") if pri_w_port_target else None
        ips = sorted(addr.get((host or "").lower(), set())) or [src[inst]]
        label = inst[:-len(t) - 1] if inst.endswith("." + t.lower()) else inst
        return {"type": t, "instance": label, "host": host,
                "port": pri_w_port_target[2] if pri_w_port_target else None,
                "txt": txt.get(inst, {}), "addresses": ips, "src": src[inst]}


# ---------------------------------------------------------------------- the responder

def allowed(addrs: list[str]) -> list[ipaddress.IPv4Interface]:
    """The IPv4 interface addresses a TandemNode may advertise on: what its bind rule allows to
    listen on (LAN, link-local, fabric), never loopback, the unspecified or a global address."""
    out = []
    for a in addrs:
        try:
            i = ipaddress.IPv4Interface(a)
        except ValueError:
            continue
        ip = i.ip
        if ip.is_loopback or ip.is_unspecified or ip.is_global or ip.is_multicast:
            continue
        out.append(i)
    return out


def _responder_socket() -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if sys.platform == "darwin" and hasattr(socket, "SO_REUSEPORT"):
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    s.bind(("0.0.0.0", PORT))
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
    return s


class Responder:
    """`info()` -> {instance, port, txt, target} of the one service; `addrs()` -> the machine's
    IPv4 addresses with their prefix ("192.0.2.5/24"); `sock()` the bound socket."""

    REFRESH_S = 60.0

    def __init__(self, info, addrs, *, sock=None, clock=time.monotonic, log=None,
                 service: str = SERVICE):
        self.info, self.addrs = info, addrs
        self.sock_factory = sock or _responder_socket
        self.clock = clock
        self.log = log or (lambda m: None)
        self.service = service
        self.ifaces: list[ipaddress.IPv4Interface] = []
        self.s = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._last: dict = {}                    # (question, iface) -> when answered by multicast

    # ------------------------------------------------------------------ the records
    def names(self) -> tuple[str, str]:
        i = self.info()
        return f"{i['instance']}.{self.service}", i["target"] + ".local"

    def records(self, iface: ipaddress.IPv4Interface, ttl: int = TTL) -> list[Record]:
        i = self.info()
        inst, target = self.names()
        return [Record(self.service, PTR, inst, ttl),
                Record(inst, SRV, (0, 0, i["port"], target), ttl, flush=True),
                Record(inst, TXT, i["txt"], ttl, flush=True),
                Record(target, A, str(iface.ip), ttl, flush=True)]

    def iface_for(self, ip: str) -> ipaddress.IPv4Interface | None:
        try:
            a = ipaddress.IPv4Address(ip)
        except ValueError:
            return None
        return next((i for i in self.ifaces if a in i.network), None)

    def handle(self, data: bytes, src: tuple[str, int]) -> list[tuple[bytes, tuple, object]]:
        """The replies to one packet: [(message, destination, the interface to send on)]."""
        try:
            m = unpack(data)
        except ValueError:
            return []
        iface = self.iface_for(src[0])
        if m.response or iface is None:
            return []
        inst, target = self.names()
        legacy = src[1] != PORT
        ttl = LEGACY_TTL if legacy else TTL
        recs = self.records(iface, ttl)
        ptr, srv, txt, a = recs
        answers, extra, asked = [], [], []
        for name, qtype, qclass in m.questions:
            hit = []
            if _same(name, self.service) and qtype in (PTR, ANY):
                hit, more = [ptr], [srv, txt, a]
            elif _same(name, META) and qtype in (PTR, ANY):
                hit, more = [Record(META, PTR, self.service, ttl)], []
            elif _same(name, inst) and qtype in (SRV, TXT, ANY):
                hit = [r for r in (srv, txt) if qtype in (r.rtype, ANY)]
                more = [a]
            elif _same(name, target) and qtype in (A, ANY):
                hit, more = [a], []
            if hit:
                answers += [r for r in hit if r not in answers]
                extra += [r for r in more if r not in extra]
                asked.append((name, qtype, qclass))
        if not answers:
            return []
        extra = [r for r in extra if r not in answers]
        if legacy:
            for r in answers + extra:
                r.flush = False
            return [(pack(id_=m.id, flags=RESPONSE, questions=asked, answers=answers,
                          additional=extra), src, iface)]
        msg = pack(flags=RESPONSE, answers=answers, additional=extra)
        if any(q[2] & TOP for q in asked):
            return [(msg, src, iface)]
        key = (tuple(asked), str(iface))
        now = self.clock()
        if now - self._last.get(key, -1e9) < 1.0:
            return []                            # one multicast answer a second (RFC 6762 6)
        self._last[key] = now
        return [(msg, (GROUP, PORT), iface)]

    def announcement(self, iface, ttl: int = TTL) -> bytes:
        return pack(flags=RESPONSE, answers=self.records(iface, ttl))

    # ------------------------------------------------------------------ the socket
    def _send(self, data: bytes, dest, iface) -> None:
        with self._lock:
            try:
                if dest[0] == GROUP:
                    self.s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                                      socket.inet_aton(str(iface.ip)))
                self.s.sendto(data, dest)
            except OSError as exc:
                self.log(f"[node] mDNS: no answer sent on {iface.ip}: {exc}")

    def _join(self) -> None:
        now = allowed(self.addrs() or [])
        for i in now:
            if i in self.ifaces:
                continue
            try:
                self.s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                                  socket.inet_aton(GROUP) + socket.inet_aton(str(i.ip)))
            except OSError as exc:
                if exc.errno != errno.EADDRINUSE:
                    self.log(f"[node] mDNS: cannot join the group on {i.ip}: {exc}")
                    continue
            self.log(f"[node] mDNS: advertising {self.service} on {i.ip}")
        self.ifaces = now

    def announce(self, ttl: int = TTL) -> None:
        for i in self.ifaces:
            self._send(self.announcement(i, ttl), (GROUP, PORT), i)

    def start(self) -> "Responder":
        self.s = self.sock_factory()
        self._join()
        self._thread = threading.Thread(target=self._run, name="mdns", daemon=True)
        self._thread.start()
        return self

    def update(self) -> None:
        """The TXT changed (paired, a window): announced twice, a second apart."""
        if self.s is None:
            return
        self.announce()
        threading.Timer(1.0, self.announce).start()

    def stop(self) -> None:
        self._stop.set()
        if self.s is not None:
            self.announce(ttl=0)
            if self._thread is not None:
                self._thread.join(timeout=3)
            self.s.close()
            self.s = None

    def _run(self) -> None:
        self.announce()
        again, refresh = self.clock() + 1.0, self.clock() + self.REFRESH_S
        while not self._stop.is_set():
            try:
                r, _, _ = select.select([self.s], [], [], 0.5)
            except (OSError, ValueError):
                return
            now = self.clock()
            if again is not None and now >= again:
                self.announce()
                again = None
            if now >= refresh:
                refresh = now + self.REFRESH_S
                self._join()
            if not r:
                continue
            try:
                data, src = self.s.recvfrom(9000)
            except OSError:
                continue
            for msg, dest, iface in self.handle(data, src):
                self._send(msg, dest, iface)
