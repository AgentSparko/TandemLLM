"""The engine host on the LAN (ENG-450): server/pairing/mdns.py's `_tandem._tcp` responder.

The answers are checked as bytes (the responder's `answer` is pure), on a real UDP socket over
loopback (unicast: the box harness may give no multicast route), and with a second socket holding
the port. Nothing is sent to the LAN: every socket here is bound to 127.0.0.1 on a free port, and
the group is never joined.

The test matrix (the Gherkin of ENG-450):

  1  Given an engine in setup mode or serving, when a browser of _tandem._tcp asks (PTR, from port
     5353), then it answers with the instance's PTR, SRV (the HTTP port, the responder's own target
     name), TXT name, version, port and fp, and A records of the addresses it listens on, by
     multicast; and the answer holds no code, token or key (only the TXT's four keys, fp the
     host key's fingerprint)                                test_a_browse_is_answered
  2  the other questions: the service enumeration, SRV/TXT/ANY of the instance, A of the target; a
     legacy resolver (another source port) gets a unicast answer with its id and question and TTLs
     of at most 10 s and no cache-flush bit; the QU bit gets a unicast answer to 5353; a known
     answer is not sent again; a response, a question for another name and a packet that does not
     read get nothing; the goodbye carries TTL 0             test_the_answers
  3  a real socket: the advertiser on 127.0.0.1 announces at start, answers a query from another
     socket, and says goodbye at stop                         test_on_a_socket
  4  a second responder holding the port with SO_REUSEADDR (and SO_REUSEPORT), as avahi does: the
     engine's binds beside it and both get the query; a holder that does not share: bind_socket
     refuses, the start falls back to avahi-publish-service with the same TXT (argv), or is off
     with one line when there is none                         test_a_second_responder
  5  the off switch: QSE_ADVERTISE=off (and 0, no, false) starts nothing; an engine on loopback
     starts nothing and says nothing; no SSH host key: off with one line; QSE_ADVERTISE is a
     documented managed key (on|off) that changes no start  test_the_off_switch

Not exercised here: multicast on a real network (the group join and a browser on another machine);
the responder's addresses come from the interfaces, which are faked in 1 and 2.

Run: python tests/test_pair_mdns.py
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import struct
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from pair_hostkeys import host_etc  # noqa: E402
from ports import free_port  # noqa: E402
from server.pairing import mdns as M  # noqa: E402

FP = "SHA256:" + "Q" * 43


def _advert(addrs=("192.0.2.10", "198.51.100.3")) -> M.Advert:
    return M.Advert("Engine.Host_1", 8000, "0.15.4", FP, list(addrs))


def _query(questions, pid: int = 0, answers: bytes = b"", an: int = 0) -> bytes:
    body = b"".join(M.encode_name(n) + struct.pack(">HH", t, 1 | (M.QU if qu else 0)) for n, t, qu in questions)
    return struct.pack(">6H", pid, 0, len(questions), an, 0, 0) + body + answers


def _records(pkt: bytes) -> list[tuple]:
    d = M.parse(pkt)
    out = []
    for name, t, ttl, rd, rs in d["answers"]:
        cls = struct.unpack(">H", pkt[rs - 8:rs - 6])[0]
        out.append((name, t, ttl, M.rdata_value(pkt, t, rs, rd), bool(cls & M.FLUSH)))
    return out


def test_a_browse_is_answered():
    adv = _advert()
    out = M.Responder(adv).answer(_query([(M.SERVICE, M.PTR, False)]), ("192.0.2.20", 5353))
    assert len(out) == 1 and out[0][1] == (M.GROUP, M.PORT), out
    recs = _records(out[0][0])
    inst = "Engine-Host-1._tandem._tcp.local"
    assert recs[0] == (M.SERVICE, M.PTR, 4500, inst, False), recs
    assert (inst, M.SRV, 120, (0, 0, 8000, "Engine-Host-1-tandem.local"), True) in recs, recs
    txt = next(r for r in recs if r[1] == M.TXT)
    assert txt[3] == ["name=Engine.Host_1", "version=0.15.4", "port=8000", f"fp={FP}"], txt
    assert [r[3] for r in recs if r[1] == M.A] == ["192.0.2.10", "198.51.100.3"]
    assert all(r[4] for r in recs if r[1] in (M.SRV, M.TXT, M.A))
    # no secret: only the four TXT keys, nothing that looks like a code, a token or a key
    assert {kv.split("=", 1)[0] for kv in txt[3]} == {"name", "version", "port", "fp"}
    raw = out[0][0].decode("latin-1")
    assert not re.search(r"(?<![0-9])[0-9]{6}(?![0-9])", raw.replace("8000", "")), "a 6-digit run in the answer"
    assert "ssh-ed25519" not in raw and "AAAAC3" not in raw and "sha256:" not in raw.lower().replace("sha256:qq", "")
    return f"{len(recs)} records, TXT {', '.join(k.split('=')[0] for k in txt[3])}"


def test_the_answers():
    adv = _advert(["192.0.2.10"])
    r = M.Responder(adv)
    src = ("192.0.2.20", 5353)
    out = r.answer(_query([(M.META, M.PTR, False)]), src)
    assert _records(out[0][0]) == [(M.META, M.PTR, 4500, M.SERVICE, False)]
    for t, want in ((M.SRV, {M.SRV}), (M.TXT, {M.TXT}), (M.ANY, {M.SRV, M.TXT})):
        recs = _records(r.answer(_query([(adv.instance, t, False)]), src)[0][0])
        d = M.parse(r.answer(_query([(adv.instance, t, False)]), src)[0][0])
        assert {x[1] for x in recs[:len(want)]} == want and recs[-1][1] == M.A, (t, recs)
        assert d["id"] == 0 and d["flags"] == 0x8400
    assert _records(r.answer(_query([(adv.target.upper(), M.A, False)]), src)[0][0]) == \
        [(adv.target, M.A, 120, "192.0.2.10", True)]
    # a legacy resolver: unicast back, its id and question, TTL <= 10, no cache-flush bit
    (pkt, dest), = r.answer(_query([(M.SERVICE, M.PTR, False)], pid=4242), ("192.0.2.21", 40000))
    d = M.parse(pkt)
    assert dest == ("192.0.2.21", 40000) and d["id"] == 4242 and d["questions"] == [(M.SERVICE, M.PTR, False)]
    assert all(x[2] <= 10 and not x[4] for x in _records(pkt)), _records(pkt)
    # QU: unicast to 5353
    (pkt, dest), = r.answer(_query([(M.SERVICE, M.PTR, True)]), src)
    assert dest == ("192.0.2.20", 5353)
    # known-answer suppression: the PTR is known with more than half its TTL
    known = M._record(M.SERVICE, M.PTR, 4000, M.encode_name(adv.instance), False)
    assert r.answer(_query([(M.SERVICE, M.PTR, False)], answers=known, an=1), src) == []
    stale = M._record(M.SERVICE, M.PTR, 100, M.encode_name(adv.instance), False)
    assert r.answer(_query([(M.SERVICE, M.PTR, False)], answers=stale, an=1), src)
    # nothing for a response, another name, garbage, an empty question list
    resp = bytearray(_query([(M.SERVICE, M.PTR, False)]))
    resp[2] = 0x84
    for pkt in (bytes(resp), _query([("_http._tcp.local", M.PTR, False)]), b"\x00" * 5, b"\xff" * 40,
                _query([])):
        assert r.answer(pkt, src) == [], pkt[:20]
    # a compressed name in a query reads
    q = struct.pack(">6H", 0, 0, 2, 0, 0, 0) + M.encode_name(M.SERVICE) + struct.pack(">HH", M.PTR, 1) + \
        b"\xc0\x0c" + struct.pack(">HH", M.PTR, 1)
    assert M.parse(q)["questions"][1][0] == M.SERVICE
    # the goodbye
    assert all(x[2] == 0 for x in _records(r.announcement(goodbye=True)))
    assert {x[1] for x in _records(r.announcement())} == {M.PTR, M.SRV, M.TXT, M.A}
    return "enumeration, SRV/TXT/ANY, A, legacy, QU, known answers, junk, goodbye"


def _sock(port: int = 0, reuse: bool = True) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if reuse:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    s.bind(("127.0.0.1", port))
    s.settimeout(3)
    return s


def test_on_a_socket():
    port = free_port()
    watcher = _sock()
    sock = M.bind_socket(port, bind="127.0.0.1", join=False)
    adv = M.Advertiser(_advert(["192.0.2.10"]), sock, multicast=False, group=watcher.getsockname(),
                       announce_gap_s=0.2).start()
    try:
        first, _ = watcher.recvfrom(9000)
        second, _ = watcher.recvfrom(9000)
        assert _records(first) == _records(second) and {r[1] for r in _records(first)} == {M.PTR, M.SRV, M.TXT, M.A}
        asker = _sock()
        asker.sendto(_query([(M.SERVICE, M.PTR, False)], pid=7), ("127.0.0.1", port))
        pkt, frm = asker.recvfrom(9000)
        assert frm == ("127.0.0.1", port) and M.parse(pkt)["id"] == 7, frm
        assert any(r[1] == M.SRV and r[3][2] == 8000 for r in _records(pkt))
        asker.close()
    finally:
        adv.stop()
    bye, _ = watcher.recvfrom(9000)
    assert all(r[2] == 0 for r in _records(bye))
    watcher.close()
    return f"announced twice, answered a legacy query on 127.0.0.1:{port}, goodbye TTL 0"


def test_a_second_responder():
    port = free_port()
    other = _sock(port)                                        # avahi's socket: shared
    sock = M.bind_socket(port, bind="127.0.0.1", join=False)
    asker = _sock()
    try:
        assert sock.getsockname()[1] == port
        sock.settimeout(3)
        # the multicast case is the kernel's (each datagram to every socket); on unicast one of them
        # takes it: the engine's must at least bind and answer what reaches it
        adv = M.Advertiser(_advert(["192.0.2.10"]), sock, multicast=False, group=asker.getsockname(),
                           announce_gap_s=0.1).start()
        _ann, _ = asker.recvfrom(9000)
        adv.stop()
    finally:
        other.close()
        asker.close()
    # a holder that does not share: refused; the start falls back to avahi-publish-service, or is off
    port = free_port()
    holder = _sock(port, reuse=False)
    tmp = tempfile.mkdtemp(prefix="pair-mdns-")
    try:
        try:
            M.bind_socket(port, bind="127.0.0.1", join=False)
            raise AssertionError("bound beside a holder that does not share")
        except OSError:
            pass
        etc = host_etc(tmp)
        log = os.path.join(tmp, "argv")
        fake = os.path.join(tmp, "avahi-publish-service")
        with open(fake, "w") as fh:
            fh.write(f'#!/bin/sh\nprintf "%s\\n" "$@" > {log}\nexec sleep 30\n')
        os.chmod(fake, 0o755)
        real_bind = M.bind_socket
        M.bind_socket = lambda p, addrs=None, bind="", join=True: real_bind(p, addrs, "127.0.0.1", False)
        try:
            adv, why = M.start_from_env(port=8000, host="192.0.2.10", env={}, etc=etc, mdns_port=port,
                                        which=lambda n: fake)
            assert isinstance(adv, M.AvahiPublish) and "avahi-publish-service" in why, why
            for _ in range(50):
                if os.path.exists(log) and os.path.getsize(log):
                    break
                time.sleep(0.1)
            argv = open(log).read().split("\n")
            assert argv[:4] == ["-s", M.label(_host()), "_tandem._tcp", "8000"], argv
            assert [a.split("=")[0] for a in argv[4:8]] == ["name", "version", "port", "fp"], argv
            adv.stop()
            assert adv.proc.poll() is not None
            adv, why = M.start_from_env(port=8000, host="192.0.2.10", env={}, etc=etc, mdns_port=port,
                                        which=lambda n: None)
            assert adv is None and why.startswith(f"off: UDP {port} is held") and "\n" not in why, why
        finally:
            M.bind_socket = real_bind
    finally:
        holder.close()
        shutil.rmtree(tmp, ignore_errors=True)
    return "shared with a SO_REUSEADDR holder; a sole holder: avahi-publish-service, else off"


def _host() -> str:
    from server.pairing import hostkey
    return hostkey.hostname()


def test_the_off_switch():
    tmp = tempfile.mkdtemp(prefix="pair-mdns-")
    try:
        etc = host_etc(tmp)
        for v in ("off", "OFF", "0", "no", "false"):
            adv, why = M.start_from_env(port=8000, host="0.0.0.0", env={"QSE_ADVERTISE": v}, etc=etc)
            assert adv is None and why == "off (QSE_ADVERTISE=off)", (v, why)
        adv, why = M.start_from_env(port=8000, host="127.0.0.1", env={}, etc=etc)
        assert adv is None and why is None
        adv, why = M.start_from_env(port=8000, host="192.0.2.10", env={}, etc=os.path.join(tmp, "none"))
        assert adv is None and why.startswith("off: no SSH host key"), why
        from server.remote import overlay
        from server.remote.paths import Tree
        spec = overlay.schema(Tree(ROOT))
        assert spec["QSE_ADVERTISE"]["managed"] and spec["QSE_ADVERTISE"]["values"] == ["on", "off"], \
            spec["QSE_ADVERTISE"]
        assert spec["QSE_ADVERTISE"]["documented"] == "on" and spec["QSE_ADVERTISE"]["default"] is None
        assert spec["QSE_SSH_PORT"]["managed"] and spec["QSE_SSH_PORT"]["type"] == "integer"
        with open(os.path.join(ROOT, "ops", "serve.env")) as fh:
            env = fh.read()
        for k in ("QSE_ADVERTISE", "QSE_SSH_PORT"):
            assert re.search(rf"^#{k}=", env, re.M) and not re.search(rf"^\s*(export\s+)?{k}=", env, re.M), k
        return "off values, loopback silent, no host key off; documented on|off, assigned nowhere"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
