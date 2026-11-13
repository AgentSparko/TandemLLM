"""The link between two stage processes of one model (ENG-307): frames over one TCP connection.

A model split across two engine processes (engine/methods/stages.py) exchanges two kinds of message
a round: a small step header one way and the rows' activations at the cut the other way. This module
is the transport and knows neither: it moves frames, and a frame says what it holds.

    frame = header (40 bytes, network order) + payload
    header: magic "TL", version, kind, dtype, ndim, reserved, epoch, seq, payload bytes, 4 dims

The payload's length is checked against the link's cap and, for a typed frame, against
dims x item size before a byte of it is allocated or read. Every receive has a deadline. A link that
misses one, or reads a frame it cannot accept, is down for good: the caller opens a new connection,
which gets a new epoch (the handshake counts it), and nothing sent under an older epoch is used.

The handshake, before any other frame:

    dialer   -> HELLO  nonce
    listener -> HELLO  nonce
    dialer   -> AUTH   HMAC-SHA256(key, "dial"   | both nonces)
    listener -> AUTH   HMAC-SHA256(key, "listen" | both nonces)     only after the dialer's holds
    both     -> IDENT  the engine's identity block with its stage, and a MAC over it
    both     -> OK, or REFUSE with the reason

Both identity blocks must name the same code, weights, family and layer count, the two stages' layer
ranges must be complementary: [0, k) and [k, layers), and both must keep state alike (`stage.caches`,
"off" when absent) with the same slots (`stage.slots`, 1 when absent). Anything else refuses the link; there is
no second transport to fall back to. The key comes from the caller's configuration (`read_key`) and
is never printed, logged or put in an error. The handshake proves both ends hold the key; frames after
it are neither encrypted nor signed, so the link belongs on a private segment.

Addresses are IP literals. A listener binds the address it is given and refuses a wildcard.
The coordinator's side pings an idle link once a second (`start_heartbeat`); the other side answers
from its blocking receive, so an idle process sleeps in the kernel.

Standard library only: a payload is bytes here, the stage protocol turns tensors into them.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import socket
import struct
import threading
import time

MAGIC = b"TL"
VERSION = 1
HEADER = struct.Struct("!2sBBBBHIIQ4I")
HEADER_BYTES = HEADER.size                      # 40
MAX_DIMS = 4
#: the default cap of one frame's payload; a prefill chunk's rows are the largest frame a link carries
MAX_FRAME = 256 << 20

# frame kinds
HELLO, AUTH, IDENT, OK, REFUSE, STEP, CARRY, REPLY, ERROR, PING, PONG, BYE = range(1, 13)
KINDS = {HELLO: "hello", AUTH: "auth", IDENT: "ident", OK: "ok", REFUSE: "refuse", STEP: "step",
         CARRY: "carry", REPLY: "reply", ERROR: "error", PING: "ping", PONG: "pong", BYE: "bye"}

# payload types: code -> (name, bytes an item); 0 is untyped bytes (no dims)
RAW = 0
DTYPES = {0: ("bytes", 1), 1: ("bfloat16", 2), 2: ("float16", 2), 3: ("float32", 4), 4: ("float64", 8),
          5: ("int16", 2), 6: ("int32", 4), 7: ("int64", 8), 8: ("uint8", 1), 9: ("bool", 1)}
DTYPE_CODES = {name: code for code, (name, _) in DTYPES.items()}

HEARTBEAT_S = 1.0
HANDSHAKE_S = 5.0
#: the cap of a frame before the peer has proved it holds the key
HANDSHAKE_FRAME = 1 << 16
KEY_ENV = "QWEN38_LINK_KEY"
KEY_MIN = 32
NONCE = 32
_TAG = b"tandem-link/1|"
_SMALL = 1 << 16


class LinkError(Exception):
    """A link failure; `code` is one of timeout, closed, frame, refused."""

    code = "link"


class LinkTimeout(LinkError):
    code = "timeout"


class LinkClosed(LinkError):
    code = "closed"


class LinkProtocol(LinkError):
    code = "frame"


class LinkRefused(LinkError):
    """The handshake refused the peer; `reason` is auth, version, code, weights, family, layers, stage, caches
    or slots."""

    code = "refused"

    def __init__(self, reason: str):
        super().__init__(f"link refused: {reason}")
        self.reason = reason


class Frame:
    __slots__ = ("kind", "epoch", "seq", "dtype", "dims", "payload")

    def __init__(self, kind, epoch, seq, dtype, dims, payload):
        self.kind, self.epoch, self.seq = kind, epoch, seq
        self.dtype, self.dims, self.payload = dtype, dims, payload

    def json(self) -> dict:
        try:
            out = json.loads(bytes(self.payload).decode())
        except (ValueError, UnicodeDecodeError) as e:
            raise LinkProtocol(f"a {KINDS.get(self.kind)} frame without JSON") from e
        if not isinstance(out, dict):
            raise LinkProtocol(f"a {KINDS.get(self.kind)} frame whose JSON is no object")
        return out

    def __repr__(self) -> str:
        return (f"Frame({KINDS.get(self.kind, self.kind)}, epoch {self.epoch}, seq {self.seq}, "
                f"{DTYPES[self.dtype][0]}{list(self.dims)}, {len(self.payload)} bytes)")


def pack_header(kind: int, epoch: int, seq: int, dtype: int, dims: tuple, nbytes: int) -> bytes:
    if kind not in KINDS or dtype not in DTYPES or len(dims) > MAX_DIMS:
        raise LinkProtocol(f"cannot frame kind {kind}, dtype {dtype}, {len(dims)} dims")
    if dtype != RAW or dims:
        want = DTYPES[dtype][1]
        for d in dims:
            want *= d
        if dtype == RAW or want != nbytes:
            raise LinkProtocol(f"payload of {nbytes} bytes is not {DTYPES[dtype][0]}{list(dims)}")
    d4 = tuple(dims) + (0,) * (MAX_DIMS - len(dims))
    return HEADER.pack(MAGIC, VERSION, kind, dtype, len(dims), 0, epoch, seq, nbytes, *d4)


def parse_header(raw: bytes, max_frame: int = MAX_FRAME) -> tuple:
    """(kind, epoch, seq, dtype, dims, payload bytes) of a header, or LinkProtocol: every check a
    receiver makes before it allocates the payload."""
    if len(raw) != HEADER_BYTES:
        raise LinkProtocol(f"a header of {len(raw)} bytes")
    magic, ver, kind, dtype, ndim, _res, epoch, seq, nbytes, *d4 = HEADER.unpack(raw)
    if magic != MAGIC:
        raise LinkProtocol("not a link frame")
    if ver != VERSION:
        raise LinkProtocol(f"link version {ver}, this side speaks {VERSION}")
    if kind not in KINDS or dtype not in DTYPES or ndim > MAX_DIMS:
        raise LinkProtocol(f"unknown kind {kind}, dtype {dtype} or {ndim} dims")
    if nbytes > max_frame:
        raise LinkProtocol(f"a frame of {nbytes} bytes exceeds the cap of {max_frame}")
    dims = tuple(d4[:ndim])
    if dtype == RAW:
        if ndim:
            raise LinkProtocol("an untyped frame with dims")
    else:
        want = DTYPES[dtype][1]
        for d in dims:
            want *= d
        if want != nbytes:
            raise LinkProtocol(f"payload of {nbytes} bytes is not {DTYPES[dtype][0]}{list(dims)}")
    return kind, epoch, seq, dtype, dims, nbytes


def parse_addr(text: str, wildcard: bool = False) -> tuple[str, int]:
    """"HOST:PORT" with an IP literal as the host (never a name); a wildcard host only on request."""
    host, sep, port = str(text).rpartition(":")
    host = host.strip("[]")
    if not sep:
        raise ValueError(f"{text!r} is not HOST:PORT")
    ip = ipaddress.ip_address(host)
    if ip.is_unspecified and not wildcard:
        raise ValueError(f"{text!r} names every interface; a link binds one explicit address")
    p = int(port)
    if not 0 <= p <= 65535:
        raise ValueError(f"{text!r}: no such port")
    return host, p


def interface_of(host: str) -> str:
    """The interface that holds the address `host` (IPv4, Linux), else "unknown"."""
    try:
        import fcntl
        want = socket.inet_aton(host)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            for _idx, name in socket.if_nameindex():
                try:
                    got = fcntl.ioctl(s.fileno(), 0x8915, struct.pack("256s", name.encode()[:15]))
                except OSError:
                    continue
                if got[20:24] == want:
                    return name
    except (ImportError, OSError, AttributeError):
        pass
    return "unknown"


def read_key(env, name: str = KEY_ENV) -> bytes:
    """The link's shared key from the caller's configuration (a mapping, e.g. the parsed env file):
    at least 32 characters. The value appears in no message."""
    value = env.get(name) if env is not None else None
    if not value:
        raise ValueError(f"{name} is not set: a link needs the key both stages share")
    raw = value if isinstance(value, bytes) else str(value).strip().encode()
    if len(raw) < KEY_MIN:
        raise ValueError(f"{name} is shorter than {KEY_MIN} characters")
    return raw


class Stats:
    """The link's own counters: frames and bytes each way, and payload bytes by frame kind."""

    def __init__(self):
        self.tx_frames = self.rx_frames = self.tx_bytes = self.rx_bytes = 0
        self.tx_payload: dict = {}
        self.rx_payload: dict = {}

    def as_dict(self) -> dict:
        return {"tx_frames": self.tx_frames, "rx_frames": self.rx_frames, "tx_bytes": self.tx_bytes,
                "rx_bytes": self.rx_bytes,
                "tx_payload": {KINDS[k]: v for k, v in self.tx_payload.items()},
                "rx_payload": {KINDS[k]: v for k, v in self.rx_payload.items()}}


class TcpLink:
    """One connected link. `lock` is held for a request and its answer, so a heartbeat never lands
    inside one; `epoch` is set by the handshake."""

    transport = "tcp"

    def __init__(self, sock: socket.socket, max_frame: int = MAX_FRAME):
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock = sock
        self.max_frame = int(max_frame)
        self.stats = Stats()
        self.lock = threading.RLock()
        self.epoch = 0
        self.state = "up"
        self.why = None
        self.peer_ident = None
        self.on_down = None
        self._hb = None
        self._hb_stop = threading.Event()
        try:
            self.local, self.peer = sock.getsockname()[:2], sock.getpeername()[:2]
        except OSError:
            self.local = self.peer = ("?", 0)

    def describe(self) -> str:
        """The start line: transport, bind, peer and interface."""
        return (f"link: transport {self.transport}, bind {self.local[0]}:{self.local[1]}, "
                f"peer {self.peer[0]}:{self.peer[1]}, interface {interface_of(self.local[0])}, "
                f"epoch {self.epoch}")

    # ------------------------------------------------------------------ state
    def down(self, why: str) -> None:
        """Mark the link down and close it; the first reason is kept."""
        first = self.state == "up"
        if first:
            self.state, self.why = "down", why
        self._hb_stop.set()
        try:
            self.sock.close()
        except OSError:
            pass
        if first and self.on_down is not None:
            self.on_down(why)

    def close(self) -> None:
        """Say goodbye when the link is still up, then close."""
        if self.state == "up":
            try:
                self.send(BYE, deadline=0.2)
            except LinkError:
                pass
        self.down("closed here")

    # ------------------------------------------------------------------ frames
    def _fail(self, exc: LinkError):
        self.down(exc.code)
        return exc

    def send(self, kind: int, payload=b"", dtype: int = RAW, dims: tuple = (), seq: int = 0,
             deadline: float = HANDSHAKE_S) -> None:
        if self.state != "up":
            raise LinkClosed(f"the link is down ({self.why})")
        mv = memoryview(payload).cast("B") if not isinstance(payload, (bytes, bytearray)) else payload
        n = len(mv)
        if n > self.max_frame:
            raise LinkProtocol(f"a frame of {n} bytes exceeds the cap of {self.max_frame}")
        head = pack_header(kind, self.epoch, seq, dtype, tuple(dims), n)
        try:
            self.sock.settimeout(max(deadline, 0.001))
            if n <= _SMALL:
                self.sock.sendall(head + bytes(mv))
            else:
                self.sock.sendall(head)
                self.sock.sendall(mv)
        except socket.timeout as e:
            raise self._fail(LinkTimeout(f"send of a {KINDS[kind]} frame passed {deadline:g} s")) from e
        except (OSError, ValueError) as e:
            raise self._fail(LinkClosed(f"send failed: {type(e).__name__}")) from e
        st = self.stats
        st.tx_frames += 1
        st.tx_bytes += HEADER_BYTES + n
        st.tx_payload[kind] = st.tx_payload.get(kind, 0) + n

    def _read(self, view: memoryview, end: float, what: str) -> None:
        got = 0
        while got < len(view):
            left = end - time.monotonic()
            if left <= 0:
                raise self._fail(LinkTimeout(f"no {what} inside the deadline"))
            try:
                self.sock.settimeout(left)
                n = self.sock.recv_into(view[got:])
            except socket.timeout as e:
                raise self._fail(LinkTimeout(f"no {what} inside the deadline")) from e
            except (OSError, ValueError) as e:
                raise self._fail(LinkClosed(f"receive failed: {type(e).__name__}")) from e
            if n == 0:
                raise self._fail(LinkClosed("the peer closed the link"))
            got += n

    def recv(self, deadline: float, into=None) -> Frame:
        """The next frame, or LinkTimeout once `deadline` seconds passed. `into`: a writable buffer
        the payload is read into when it has the payload's size (host staging the caller owns)."""
        if self.state != "up":
            raise LinkClosed(f"the link is down ({self.why})")
        end = time.monotonic() + deadline
        head = bytearray(HEADER_BYTES)
        self._read(memoryview(head), end, "frame")
        try:
            kind, epoch, seq, dtype, dims, n = parse_header(bytes(head), self.max_frame)
        except LinkProtocol as e:
            raise self._fail(e)
        if into is not None and memoryview(into).nbytes == n:
            buf = into
        else:
            buf = bytearray(n)
        if n:
            self._read(memoryview(buf).cast("B"), end, "payload")
        st = self.stats
        st.rx_frames += 1
        st.rx_bytes += HEADER_BYTES + n
        st.rx_payload[kind] = st.rx_payload.get(kind, 0) + n
        return Frame(kind, epoch, seq, dtype, dims, buf)

    # ------------------------------------------------------------------ heartbeat
    def ping(self, deadline: float = HEARTBEAT_S) -> None:
        """One ping and its pong, under the request lock."""
        with self.lock:
            self.send(PING, deadline=deadline)
            fr = self.recv(deadline)
            if fr.kind != PONG:
                raise self._fail(LinkProtocol(f"a {KINDS[fr.kind]} frame answered a ping"))

    def start_heartbeat(self, interval: float = HEARTBEAT_S, deadline: float = 2 * HEARTBEAT_S) -> None:
        """Ping the peer every `interval` seconds while no request holds the link. A missed pong
        takes the link down (`on_down` is called with the reason)."""
        if self._hb is not None:
            return

        def beat():
            while not self._hb_stop.wait(interval):
                if not self.lock.acquire(blocking=False):
                    continue                    # a request is in flight: its own deadline holds
                try:
                    if self.state != "up":
                        return
                    self.ping(deadline)
                except LinkError:
                    return
                finally:
                    self.lock.release()

        self._hb = threading.Thread(target=beat, name="link-heartbeat", daemon=True)
        self._hb.start()


class Listener:
    """A listening socket on one explicit address."""

    def __init__(self, bind: str, max_frame: int = MAX_FRAME):
        host, port = parse_addr(bind)
        self.max_frame = max_frame
        fam = socket.AF_INET6 if ":" in host else socket.AF_INET
        self.sock = socket.socket(fam, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, port))
        self.sock.listen(1)
        self.host, self.port = self.sock.getsockname()[:2]

    def accept(self, deadline: float | None = None) -> TcpLink:
        """The next connection (blocks in the kernel; `deadline` None waits without end)."""
        self.sock.settimeout(deadline)
        try:
            conn, _ = self.sock.accept()
        except socket.timeout as e:
            raise LinkTimeout("no peer connected inside the deadline") from e
        except OSError as e:
            raise LinkClosed(f"accept failed: {type(e).__name__}") from e
        return TcpLink(conn, self.max_frame)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def dial(peer: str, bind: str | None = None, deadline: float = HANDSHAKE_S,
         max_frame: int = MAX_FRAME) -> TcpLink:
    """Connect to `peer` ("IP:PORT"), from the local address `bind` ("IP:PORT", port 0 for any)."""
    host, port = parse_addr(peer)
    src = parse_addr(bind) if bind else None
    try:
        sock = socket.create_connection((host, port), timeout=deadline, source_address=src)
    except socket.timeout as e:
        raise LinkTimeout(f"no connection to {host}:{port} inside {deadline:g} s") from e
    except OSError as e:
        raise LinkClosed(f"no connection to {host}:{port}: {type(e).__name__}") from e
    return TcpLink(sock, max_frame)


# ---------------------------------------------------------------------- the handshake
def _mac(key: bytes, side: bytes, nonces: bytes, body: bytes = b"") -> bytes:
    return hmac.new(key, _TAG + side + b"|" + nonces + body, hashlib.sha256).digest()


def mismatch(mine: dict, theirs: dict) -> str | None:
    """Why two identity blocks cannot share a link, or None. The same answer on both sides."""
    for field in ("identity", "code", "weights", "family", "layers"):
        a, b = mine.get(field), theirs.get(field)
        bad = a is None or b is None or a != b
        if not bad and isinstance(a, str) and a.startswith("error"):
            bad = True
        if bad:
            return "version" if field == "identity" else field
    try:
        layers = int(mine["layers"])
        stages = sorted((int(s["lo"]), int(s["hi"]), str(s["role"]))
                        for s in (mine.get("stage"), theirs.get("stage")))
    except (TypeError, KeyError, ValueError):
        return "stage"
    (lo0, hi0, r0), (lo1, hi1, r1) = stages
    if not (lo0 == 0 and 0 < hi0 == lo1 < hi1 == layers and (r0, r1) == ("front", "tail")):
        return "stage"
    # what both stages must serve alike (SRV-817): a field left out is its default, one slot and no caches
    s0, s1 = mine["stage"], theirs["stage"]
    if str(s0.get("caches", "off")) != str(s1.get("caches", "off")):
        return "caches"
    try:
        if int(s0.get("slots", 1)) != int(s1.get("slots", 1)):
            return "slots"
    except (TypeError, ValueError):
        return "stage"
    return None


def handshake(link: TcpLink, key: bytes, ident: dict, dialer: bool, last_epoch: int = 0,
              deadline: float = HANDSHAKE_S) -> dict:
    """Authenticate the peer and compare identities. Returns the peer's identity block and sets
    `link.epoch` to one more than the highest epoch either side has used. Raises LinkRefused with
    the reason (both sides see it) or another LinkError; the link is down after any of them.
    Until it has passed, a frame is capped at `HANDSHAKE_FRAME` bytes."""
    cap, link.max_frame = link.max_frame, min(link.max_frame, HANDSHAKE_FRAME)
    try:
        peer = _handshake(link, key, ident, dialer, last_epoch, deadline)
    except LinkError as e:
        link.down(e.code)
        raise
    link.max_frame = cap
    return peer


def _handshake(link: TcpLink, key: bytes, ident: dict, dialer: bool, last_epoch: int, deadline: float) -> dict:
    end = time.monotonic() + deadline

    def left() -> float:
        return max(end - time.monotonic(), 0.001)

    def want(kind: int) -> Frame:
        fr = link.recv(left())
        if fr.kind == REFUSE:
            reason = str(fr.json().get("reason", "refused"))[:32]
            link.down("refused")
            raise LinkRefused(reason)
        if fr.kind != kind:
            link.down("frame")
            raise LinkProtocol(f"a {KINDS[fr.kind]} frame in the handshake, wanted {KINDS[kind]}")
        return fr

    def refuse(reason: str):
        try:
            link.send(REFUSE, json.dumps({"reason": reason}).encode(), deadline=left())
        except LinkError:
            pass
        link.down("refused")
        return LinkRefused(reason)

    me, peer = (b"dial", b"listen") if dialer else (b"listen", b"dial")
    mine = os.urandom(NONCE)
    if dialer:
        link.send(HELLO, mine, deadline=left())
    theirs = bytes(want(HELLO).payload)
    if len(theirs) != NONCE:
        raise refuse("auth")
    if not dialer:
        link.send(HELLO, mine, deadline=left())
    nonces = mine + theirs if dialer else theirs + mine
    if dialer:
        link.send(AUTH, _mac(key, me, nonces), deadline=left())
    got = bytes(want(AUTH).payload)
    if not hmac.compare_digest(got, _mac(key, peer, nonces)):
        raise refuse("auth")
    if not dialer:
        link.send(AUTH, _mac(key, me, nonces), deadline=left())

    body = json.dumps({**ident, "last_epoch": int(last_epoch)}, sort_keys=True).encode()
    link.send(IDENT, body + _mac(key, me + b"|ident", nonces, body), deadline=left())
    raw = bytes(want(IDENT).payload)
    pbody, pmac = raw[:-32], raw[-32:]
    if len(raw) <= 32 or not hmac.compare_digest(pmac, _mac(key, peer + b"|ident", nonces, pbody)):
        raise refuse("auth")
    try:
        pident = json.loads(pbody.decode())
        peer_epoch = int(pident.pop("last_epoch"))
    except (ValueError, KeyError, TypeError, AttributeError):
        raise refuse("version") from None
    reason = mismatch(ident, pident)
    if reason is not None:
        raise refuse(reason)
    link.send(OK, deadline=left())
    want(OK)
    link.epoch = max(int(last_epoch), peer_epoch) + 1
    link.peer_ident = pident
    return pident
