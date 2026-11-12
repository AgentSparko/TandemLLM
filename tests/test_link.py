"""engine/methods/link.py, the link between two stage processes (ENG-308), on loopback sockets:

  1  a typed frame arrives with its dims and dtype; the link counts frames and bytes
  2  a frame over the cap, or whose dims do not give its length, is refused before any payload is
     allocated or read
  3  a receive ends at its deadline, with nothing or with half a frame
  4  the handshake passes with one key and equal identities, in both directions, and counts epochs
     a frame sent before the key is proved is capped at 64 KiB
  5  another key, or other code, weights, family, layers or layer ranges, refuses the link on both
     sides with the reason; the key is in no message
  6  a listener binds one explicit address, never a wildcard or a name
  7  the heartbeat pings an idle link and takes it down when the peer stops answering
  8  through tools/linkrelay.py: a blackhole ends a receive at its deadline, a cut closes the link

No torch. Run: python3 tests/test_link.py
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import tracemalloc

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from engine.methods import link as L  # noqa: E402

KEY = b"k" * 40
IDENT = {"identity": 1, "code": "c0de", "weights": "w8", "family": "toy", "layers": 4}


def ident(role: str, lo: int, hi: int, **over) -> dict:
    return {**IDENT, "stage": {"role": role, "lo": lo, "hi": hi}, **over}


def pair(max_frame: int = L.MAX_FRAME) -> tuple:
    """Two connected links on loopback: (dialer's, listener's)."""
    lst = L.Listener("127.0.0.1:0", max_frame)
    try:
        a = L.dial(f"127.0.0.1:{lst.port}", max_frame=max_frame)
        b = lst.accept(5)
    finally:
        lst.close()
    return a, b


def raw_pair() -> tuple:
    """(a plain socket, the link at its other end)."""
    lst = L.Listener("127.0.0.1:0")
    try:
        s = socket.create_connection(("127.0.0.1", lst.port), timeout=5)
        return s, lst.accept(5)
    finally:
        lst.close()


def raises(kind, fn):
    try:
        fn()
    except kind as e:
        return e
    raise AssertionError(f"no {kind.__name__}")


def shake(a_ident, b_ident, a_key=KEY, b_key=KEY, a_last=0, b_last=0) -> tuple:
    """Both sides' handshake results (an identity block or the exception), and the links."""
    a, b = pair()
    out = {}

    def side(name, link, key, idn, dialer, last):
        try:
            out[name] = L.handshake(link, key, idn, dialer=dialer, last_epoch=last, deadline=5)
        except L.LinkError as e:
            out[name] = e

    t = threading.Thread(target=side, args=("b", b, b_key, b_ident, False, b_last))
    t.start()
    side("a", a, a_key, a_ident, True, a_last)
    t.join(10)
    return out["a"], out["b"], a, b


def test_a_typed_frame_arrives_with_dims_and_dtype():
    a, b = pair()
    payload = bytes(range(48))
    a.send(L.CARRY, payload, dtype=L.DTYPE_CODES["float32"], dims=(3, 4), seq=7)
    fr = b.recv(2)
    assert (fr.kind, fr.seq, fr.dims, L.DTYPES[fr.dtype][0]) == (L.CARRY, 7, (3, 4), "float32")
    assert bytes(fr.payload) == payload
    assert a.stats.tx_bytes == b.stats.rx_bytes == L.HEADER_BYTES + 48 == 88
    assert a.stats.tx_payload[L.CARRY] == b.stats.rx_payload[L.CARRY] == 48
    assert a.stats.tx_frames == b.stats.rx_frames == 1
    # the caller's own buffer is filled when it has the payload's size
    mine = bytearray(48)
    a.send(L.CARRY, payload, dtype=L.DTYPE_CODES["bfloat16"], dims=(2, 12))
    fr = b.recv(2, into=mine)
    assert fr.payload is mine and bytes(mine) == payload
    # more than 64 KiB goes out as two writes and arrives whole
    big = os.urandom(300_000)
    a.send(L.CARRY, big, dtype=L.DTYPE_CODES["uint8"], dims=(300_000,))
    assert bytes(b.recv(5).payload) == big
    a.close(), b.close()


def test_a_frame_over_the_cap_is_refused_before_allocation():
    s, link = raw_pair()
    link.max_frame = 1 << 20
    s.sendall(L.HEADER.pack(L.MAGIC, L.VERSION, L.CARRY, L.RAW, 0, 0, 0, 0, 1 << 40, 0, 0, 0, 0))
    tracemalloc.start()
    t0 = time.monotonic()
    e = raises(L.LinkProtocol, lambda: link.recv(5))
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert "exceeds the cap" in str(e) and time.monotonic() - t0 < 3 and peak < 1 << 20, (e, peak)
    assert link.state == "down" and link.why == "frame"
    raises(L.LinkClosed, lambda: link.recv(1))
    s.close()
    # a sender refuses its own oversized frame and stays up
    a, b = pair(max_frame=1024)
    raises(L.LinkProtocol, lambda: a.send(L.CARRY, bytes(2048)))
    assert a.state == "up"
    a.close(), b.close()


def test_a_header_that_lies_is_refused():
    f32 = L.DTYPE_CODES["float32"]
    bad = {
        "dims": L.HEADER.pack(L.MAGIC, L.VERSION, L.CARRY, f32, 2, 0, 0, 0, 20, 2, 3, 0, 0),
        "magic": L.HEADER.pack(b"XX", L.VERSION, L.CARRY, f32, 2, 0, 0, 0, 24, 2, 3, 0, 0),
        "version": L.HEADER.pack(L.MAGIC, 9, L.CARRY, f32, 2, 0, 0, 0, 24, 2, 3, 0, 0),
        "kind": L.HEADER.pack(L.MAGIC, L.VERSION, 99, f32, 2, 0, 0, 0, 24, 2, 3, 0, 0),
        "dtype": L.HEADER.pack(L.MAGIC, L.VERSION, L.CARRY, 77, 2, 0, 0, 0, 24, 2, 3, 0, 0),
        "ndim": L.HEADER.pack(L.MAGIC, L.VERSION, L.CARRY, f32, 5, 0, 0, 0, 24, 2, 3, 0, 0),
        "untyped dims": L.HEADER.pack(L.MAGIC, L.VERSION, L.CARRY, L.RAW, 1, 0, 0, 0, 24, 24, 0, 0, 0),
    }
    for name, head in bad.items():
        raises(L.LinkProtocol, lambda: L.parse_header(head))
        s, link = raw_pair()
        s.sendall(head + bytes(24))
        raises(L.LinkProtocol, lambda: link.recv(2))
        assert link.state == "down", name
        s.close()
    good = L.HEADER.pack(L.MAGIC, L.VERSION, L.CARRY, f32, 2, 0, 5, 9, 24, 2, 3, 0, 0)
    assert L.parse_header(good) == (L.CARRY, 5, 9, f32, (2, 3), 24)
    raises(L.LinkProtocol, lambda: L.pack_header(L.CARRY, 0, 0, f32, (2, 3), 20))


def test_a_receive_ends_at_its_deadline():
    for sent in (b"", L.pack_header(L.CARRY, 0, 0, L.RAW, (), 100) + b"half"):
        s, link = raw_pair()
        if sent:
            s.sendall(sent)
        t0 = time.monotonic()
        raises(L.LinkTimeout, lambda: link.recv(0.3))
        took = time.monotonic() - t0
        assert 0.25 <= took < 3.0, took                     # the upper bound leaves room for a loaded box
        assert link.state == "down" and link.why == "timeout"
        s.close()


def test_the_handshake_passes_and_counts_epochs():
    ra, rb, a, b = shake(ident("tail", 2, 4), ident("front", 0, 2))
    assert isinstance(ra, dict) and isinstance(rb, dict), (ra, rb)
    assert ra["stage"] == {"role": "front", "lo": 0, "hi": 2} and rb["stage"]["role"] == "tail"
    assert a.epoch == b.epoch == 1 and a.peer_ident == ra
    a.send(L.PING)
    assert b.recv(2).epoch == 1
    a.close(), b.close()
    # the front may be the one that dials; the epoch is one more than either side has seen
    ra, rb, a, b = shake(ident("front", 0, 3), ident("tail", 3, 4), a_last=3, b_last=5)
    assert isinstance(ra, dict) and a.epoch == b.epoch == 6
    ra, rb, a2, b2 = shake(ident("front", 0, 3), ident("tail", 3, 4), a_last=6, b_last=0)
    assert a2.epoch == b2.epoch == 7
    for x in (a, b, a2, b2):
        x.close()


def test_another_key_refuses_the_link():
    other = b"another-key-of-forty-characters-0123456789"
    for a_key, b_key in ((other, KEY), (KEY, other)):
        ra, rb, a, b = shake(ident("tail", 2, 4), ident("front", 0, 2), a_key=a_key, b_key=b_key)
        assert isinstance(ra, L.LinkRefused) and ra.reason == "auth", ra
        assert isinstance(rb, L.LinkError), rb
        assert a.state == b.state == "down"
        for text in (str(ra), repr(ra), str(rb), repr(rb), repr(a), a.describe()):
            assert KEY.decode() not in text and other.decode() not in text


def test_a_frame_before_the_key_is_proved_is_capped_small():
    s, link = raw_pair()
    out = []
    t = threading.Thread(target=lambda: out.append(raises(L.LinkProtocol, lambda: L.handshake(
        link, KEY, ident("front", 0, 2), dialer=False, deadline=3))))
    t.start()
    tracemalloc.start()
    s.sendall(L.HEADER.pack(L.MAGIC, L.VERSION, L.HELLO, L.RAW, 0, 0, 0, 0, 64 << 20, 0, 0, 0, 0))
    t.join(5)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert out and "exceeds the cap" in str(out[0]) and peak < 1 << 20, (out, peak)
    assert link.state == "down" and link.max_frame == L.HANDSHAKE_FRAME == 65536
    s.close()
    ra, rb, a, b = shake(ident("tail", 2, 4), ident("front", 0, 2))
    assert a.max_frame == b.max_frame == L.MAX_FRAME                 # the link's own cap after it
    a.close(), b.close()


def test_other_code_weights_family_or_layers_refuse_the_link():
    front, tail = ident("front", 0, 2), ident("tail", 2, 4)
    cases = {
        "code": ident("front", 0, 2, code="beef"),
        "weights": ident("front", 0, 2, weights="w9"),
        "family": ident("front", 0, 2, family="other"),
        "layers": {**ident("front", 0, 2), "layers": 6},
        "version": ident("front", 0, 2, identity=2),
        "stage": ident("front", 0, 3),                       # the ranges overlap
    }
    for reason, theirs in cases.items():
        ra, rb, a, b = shake(tail, theirs)
        assert isinstance(ra, L.LinkRefused) and isinstance(rb, L.LinkRefused), (reason, ra, rb)
        assert ra.reason == rb.reason == reason, (reason, ra.reason, rb.reason)
        assert a.state == b.state == "down"
    assert L.mismatch(tail, front) is None and L.mismatch(front, tail) is None
    table = {
        "a gap": (ident("front", 0, 1), tail),
        "two fronts": (front, ident("front", 0, 2)),
        "roles swapped": (ident("tail", 0, 2), ident("front", 2, 4)),
        "the tail ends early": (front, ident("tail", 2, 3)),
        "no stage": (front, IDENT),
        "an empty front": (ident("front", 0, 0), ident("tail", 0, 4)),
    }
    for name, (x, y) in table.items():
        assert L.mismatch(x, y) == L.mismatch(y, x) == "stage", name
    # a part an engine could not compute never equals another engine's
    assert L.mismatch(ident("front", 0, 2, code=None), ident("tail", 2, 4, code=None)) == "code"
    err = "error: OSError"
    assert L.mismatch(ident("front", 0, 2, weights=err), ident("tail", 2, 4, weights=err)) == "weights"


def test_a_listener_binds_one_explicit_address():
    for bad in ("0.0.0.0:0", "[::]:0", "localhost:9000", "9000", "127.0.0.1:70000"):
        raises(ValueError, lambda: L.Listener(bad))
    raises(ValueError, lambda: L.dial("0.0.0.0:9", deadline=0.2))
    raises(ValueError, lambda: L.dial("peer.example:9", deadline=0.2))
    lst = L.Listener("127.0.0.1:0")
    assert lst.host == "127.0.0.1" and lst.port > 0
    raises(L.LinkTimeout, lambda: lst.accept(0.1))
    a = L.dial(f"127.0.0.1:{lst.port}")
    b = lst.accept(2)
    line = b.describe()
    for part in ("transport tcp", f"bind 127.0.0.1:{lst.port}", f"peer 127.0.0.1:{a.local[1]}", "interface "):
        assert part in line, line
    opt = b.sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY)
    assert opt and a.sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY)
    lst.close(), a.close(), b.close()
    raises(L.LinkClosed, lambda: L.dial(f"127.0.0.1:{lst.port}", deadline=1))


def test_the_key_comes_from_configuration():
    secret = "s3cret-" + "x" * 40
    assert L.read_key({L.KEY_ENV: secret}) == secret.encode()
    assert L.read_key({"OTHER": secret}, name="OTHER") == secret.encode()
    for env in ({}, None, {L.KEY_ENV: ""}):
        e = raises(ValueError, lambda: L.read_key(env))
        assert L.KEY_ENV in str(e)
    short = "too-short-to-be-a-key"
    e = raises(ValueError, lambda: L.read_key({L.KEY_ENV: short}))
    assert short not in str(e)


def test_the_heartbeat_pings_and_takes_a_dead_link_down():
    a, b = pair()
    answering = threading.Event()
    answering.set()
    pings = []

    def follower():
        try:
            while True:
                fr = b.recv(5)
                if fr.kind == L.PING and answering.is_set():
                    pings.append(time.monotonic())
                    b.send(L.PONG)
        except L.LinkError:
            pass

    threading.Thread(target=follower, daemon=True).start()
    down = []
    a.on_down = down.append
    a.start_heartbeat(interval=0.05, deadline=0.3)
    time.sleep(0.6)
    assert len(pings) >= 2 and a.state == "up", len(pings)
    # a request holds the link: no ping lands inside it
    with a.lock:
        n = len(pings)
        time.sleep(0.25)
        assert len(pings) <= n + 1
    answering.clear()
    t0 = time.monotonic()
    while a.state == "up" and time.monotonic() - t0 < 5:
        time.sleep(0.01)
    took = time.monotonic() - t0
    assert a.state == "down" and down == ["timeout"] and took < 3.0, (a.state, down, took)
    print(f"    {len(pings)} pings answered; down {took:.2f} s after the peer stopped answering", flush=True)
    b.close()


def test_a_blackhole_and_a_cut_through_the_relay():
    from test_linkrelay import Relay
    lst = L.Listener("127.0.0.1:0")
    r = Relay(lst.port)
    try:
        def connect():
            got = {}
            t = threading.Thread(target=lambda: got.update(b=lst.accept(5)))
            t.start()
            a = L.dial(f"127.0.0.1:{r.port}")
            t.join(5)
            b = got["b"]
            tb = threading.Thread(target=L.handshake, args=(b, KEY, ident("front", 0, 2), False))
            tb.start()
            L.handshake(a, KEY, ident("tail", 2, 4), dialer=True)
            tb.join(5)
            return a, b

        a, b = connect()
        a.send(L.PING)
        assert b.recv(2).kind == L.PING
        r.cmd(cmd="blackhole")
        a.send(L.PING)
        t0 = time.monotonic()
        raises(L.LinkTimeout, lambda: b.recv(0.5))
        assert 0.45 <= time.monotonic() - t0 < 3.5
        r.cmd(cmd="heal")
        a.close(), b.close()

        a, b = connect()
        r.cmd(cmd="cut")
        t0 = time.monotonic()
        raises(L.LinkClosed, lambda: a.recv(5))
        raises(L.LinkClosed, lambda: b.recv(5))
        assert time.monotonic() - t0 < 4.0
        raises(L.LinkError, lambda: L.dial(f"127.0.0.1:{r.port}", deadline=1).recv(1))
        r.cmd(cmd="heal")
        a, b = connect()
        assert a.epoch == b.epoch == 1
        a.close(), b.close()
    finally:
        r.stop()
        lst.close()


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
