"""SRV-728: discovery (SRV-727): mDNS advertise and browse, the consented LAN scan, ~/.ssh/config,
one row per box, what each device is, the Local Network permission, and the ssh line.

Nothing touches a network. The LAN is a fake socket: each query the browser sends goes to the
responders' own `handle` (the node's code, unchanged) and to scripted answers, and the clock moves
when the browser waits. Port 22 is a fake connect that records every call with the fake clock, so
"no packets before consent" and the scan's rate are read from it. Addresses are 192.0.2.x and
198.51.100.x; a global address is 11.0.0.x; every name is invented.

  1  the message codec and the responder: PTR, SRV, TXT and A to a legacy query from a LAN
     subnet, the address it has there; nothing to a WAN or a routed asker; never on loopback or a
     global address; announcements and a goodbye                    test_responder_answers_its_lan
  2  the browser against responders and an avahi-like host: services with their TXT and
     addresses; macOS's EHOSTUNREACH is LanDenied                    test_browser
  3  Gherkin "Scan asks first": no connect before consent, the question as data, a wrong
     subnet refused, then every connect inside the /24, at the rate, 300 ms each; stop; a new run
     asks again                                                      test_scan_asks_first
  4  the guard: the list and the consent need the admin token or a session (or this machine),
     with the login on or off                                        test_consent_is_a_write
  5  Gherkin "Classified results": a GX10 with nothing installed (its DMI from the SSH-once
     login) is "GB10 box, nothing installed", a NAS "Linux host, not a Spark", a TandemNode
     "Spark with TandemLLM x.y", another TandemApp's "Spark owned by another TandemApp", an
     Ubuntu 24.04 banner unknown until a login                       test_classified_results
  6  no duplicate rows: one box by mDNS, the scan and ~/.ssh/config is one row; by the host key
     (hashed known_hosts) too; a virtual node on the same address keeps its own row; the cursor
     gives changed rows and gone ones                                test_one_row_per_box
  7  Gherkin "Local Network denied": the API says so, the scan is refused, add by address works
                                                                     test_local_network_denied
  8  the classification table covers every GB10 vendor; the banner table
                                                                     test_classification_tables
  9  ~/.ssh/config resolved as ssh does (first value wins, wildcards, Include, Match skipped)
                                                                     test_ssh_config
 10  the ssh line: -p, -l, -i, -J, -o, ssh://, the route; refused and dropped options
                                                                     test_ssh_line
 11  the node's advertisement: its TXT (id, class, version, DMI, paired, owner, window, host
     key), no secret in it, a virtual node without the host key      test_node_advertisement

    python tests/test_discovery.py
"""

from __future__ import annotations

import base64
import errno
import hashlib
import hmac
import ipaddress
import json
import os
import socket
import struct
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_fleet as tf  # noqa: E402
from proxy import discovery, mdns, nodetrust  # noqa: E402
from proxy_util import Proxy, run  # noqa: E402

ME = "192.0.2.10"
LAN = [{"name": "lan0", "address": ME, "subnet": "192.0.2.0/24"}]
GX10_DMI = {"vendor": "ASUSTeK COMPUTER INC.", "product": "GX10", "version": None,
            "family": "DGX Spark"}
UBUNTU_2404 = "SSH-2.0-OpenSSH_9.6p1 Ubuntu-3ubuntu13.5"
NAS = "SSH-2.0-dropbear_2022.83"
OWN_FP = "ab" * 32
OTHER_FP = "cd" * 32


# ---------------------------------------------------------------------------- fakes

class Mono:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def sleep(self, d):
        self.t += max(0.0, d)


class FakeLan:
    """The browser's socket: a query reaches every responder and every scripted host; their
    answers wait for recvfrom; an empty queue moves the clock by the timeout."""

    def __init__(self, clock, responders=(), hosts=(), deny=None):
        self.clock, self.responders, self.hosts, self.deny = clock, list(responders), \
            list(hosts), deny
        self.sent, self.q, self.timeout = [], [], 1.0

    def __call__(self, iface):
        return self

    def settimeout(self, t):
        self.timeout = t

    def sendto(self, data, dest):
        self.sent.append(dest)
        if self.deny:
            raise OSError(self.deny, os.strerror(self.deny))
        for r in self.responders:
            for msg, to, iface in r.handle(data, (ME, 50000)):
                if to == (ME, 50000):
                    self.q.append((msg, (str(iface.ip), mdns.PORT)))
        for h in self.hosts:
            self.q += h(data)

    def recvfrom(self, n):
        if self.q:
            return self.q.pop(0)
        self.clock.t += self.timeout
        raise socket.timeout()

    def close(self):
        pass


def ssh_host(name: str, ip: str):
    """An avahi-like host that announces `_ssh._tcp` for its name."""
    def answer(data):
        m = mdns.unpack(data)
        if not any(mdns._same(q[0], "_ssh._tcp.local") for q in m.questions):
            return []
        inst = f"{name}._ssh._tcp.local"
        return [(mdns.pack(id_=m.id, flags=mdns.RESPONSE, answers=[
            mdns.Record("_ssh._tcp.local", mdns.PTR, inst, 10)], additional=[
            mdns.Record(inst, mdns.SRV, (0, 0, 22, f"{name}.local"), 10),
            mdns.Record(f"{name}.local", mdns.A, ip, 10)]), (ip, mdns.PORT))]
    return answer


def node(ip: str, nid: str, *, ver="0.15.0", paired=False, owner=None, hk=None, virtual=None,
         name="lab-node", window=None, hw="ASUS Ascent GX10"):
    """A TandemNode's responder on a fake machine at `ip`."""
    txt = {"v": "1", "id": nid, "class": "spark", "ver": ver, "hw": hw,
           "paired": "1" if paired else "0", "listen": "loopback"}
    if owner:
        txt["owner"] = owner[:16]
    if window:
        txt["window"] = str(window)
    if hk:
        txt["hk"] = hk
    if virtual:
        txt["virtual"] = virtual
    info = {"instance": name + (f"-{virtual}" if virtual else ""), "port": 7700, "txt": txt,
            "target": "tandemnode-" + nid[2:14]}
    r = mdns.Responder(lambda: info, lambda: [f"{ip}/24", "127.0.0.1/8"], clock=lambda: 0)
    r.ifaces = mdns.allowed(r.addrs())
    return r


class Conn:
    def __init__(self, banner):
        self.data = b"" if banner is None else banner.encode() + b"\r\n"

    def settimeout(self, t):
        pass

    def recv(self, n):
        out, self.data = self.data[:n], self.data[n:]
        return out

    def close(self):
        pass


class Hosts:
    """Port 22 (and the agent's ports) of the fake LAN: {ip: banner} answer, the rest refuse."""

    def __init__(self, clock, banners=None, agents=(), deny=None):
        self.clock, self.banners, self.agents, self.deny = clock, banners or {}, set(agents), deny
        self.calls = []

    def __call__(self, ip, port, timeout):
        self.calls.append((ip, port, timeout, self.clock()))
        if self.deny:
            raise OSError(self.deny, os.strerror(self.deny))
        if port == 22 and ip in self.banners:
            return Conn(self.banners[ip])
        if (ip, port) in self.agents:
            return Conn(None)
        raise ConnectionRefusedError(errno.ECONNREFUSED, "refused")


class Cfg:
    def __init__(self, nodes=(), path=None):
        self._nodes, self.path, self.audits = list(nodes), path, []

    def nodes(self):
        return self._nodes

    def log_audit(self, who, action, obj, oid, detail=None):
        self.audits.append((who, action, obj, oid))


class FleetStub:
    def __init__(self, nodes=()):
        self.cfg = Cfg(nodes)
        self.logs = []

    def log(self, m):
        self.logs.append(m)


def disc(fleet=None, *, lan=None, hosts=None, home=None, platform="linux", mono=None):
    mono = mono or Mono()
    lan = lan or FakeLan(mono)
    return discovery.Discovery(
        fleet or FleetStub(), browser=mdns.Browser(sock=lan, clock=mono, platform=platform),
        connect=hosts or Hosts(mono), clock=lambda: 1_800_000_000.0, mono=mono,
        sleep=mono.sleep, home=home or tempfile.mkdtemp(prefix="disc-home-"),
        interfaces=lambda: [dict(i) for i in LAN], platform=platform, spawn=lambda fn: fn(),
        own_fp=OWN_FP)


def ok(obj, name):
    return tf.ok(obj, name)


def rows_by_ip(snap) -> dict:
    return {a: r for r in snap["rows"] for a in r["addresses"]}


def home_with(config: str | None = None, known: str | None = None) -> str:
    h = tempfile.mkdtemp(prefix="disc-home-")
    os.makedirs(os.path.join(h, ".ssh"))
    if config is not None:
        with open(os.path.join(h, ".ssh", "config"), "w") as f:
            f.write(config)
    if known is not None:
        with open(os.path.join(h, ".ssh", "known_hosts"), "w") as f:
            f.write(known)
    return h


def key_line(seed: bytes) -> tuple[str, str]:
    """(an ed25519 public key blob in base64, its SHA256 fingerprint), from a fixture seed."""
    t = b"ssh-ed25519"
    raw = len(t).to_bytes(4, "big") + t + (32).to_bytes(4, "big") + \
        hashlib.sha256(seed).digest()
    b64 = base64.b64encode(raw).decode()
    return b64, "SHA256:" + base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")


def hashed(name: str, salt: bytes) -> str:
    mac = hmac.new(salt, name.encode(), hashlib.sha1).digest()
    return f"|1|{base64.b64encode(salt).decode()}|{base64.b64encode(mac).decode()}"


# ---------------------------------------------------------------------------- the tests

def test_responder_answers_its_lan():
    info = {"instance": "lab-node", "port": 7700, "target": "tandemnode-0123456789ab",
            "txt": {"v": "1", "id": "n-0123456789ab", "class": "spark", "paired": "0"}}
    r = mdns.Responder(lambda: info, lambda: ["127.0.0.1/8", "192.0.2.5/24", "11.0.0.5/24",
                                              "198.51.100.9/24", "0.0.0.0/0"], clock=lambda: 0)
    r.ifaces = mdns.allowed(r.addrs())
    assert [str(i) for i in r.ifaces] == ["192.0.2.5/24", "198.51.100.9/24"], r.ifaces
    q = mdns.pack(id_=77, questions=[(mdns.SERVICE, mdns.PTR, mdns.IN)])
    out = r.handle(q, ("192.0.2.77", 50123))
    assert len(out) == 1 and out[0][1] == ("192.0.2.77", 50123)
    m = mdns.unpack(out[0][0])
    assert m.id == 77 and m.response and m.questions == [(mdns.SERVICE, mdns.PTR, mdns.IN)]
    got = {rec.rtype: rec for rec in m.records}
    inst = "lab-node." + mdns.SERVICE
    assert got[mdns.PTR].data == inst and got[mdns.SRV].data == (0, 0, 7700,
                                                                 "tandemnode-0123456789ab.local")
    assert got[mdns.TXT].data == info["txt"] and got[mdns.A].data == "192.0.2.5"
    assert all(rec.ttl == mdns.LEGACY_TTL and not rec.flush for rec in m.records)
    # the other LAN gets the address it has there
    a = [x for x in mdns.unpack(r.handle(q, ("198.51.100.20", 5353))[0][0]).records
         if x.rtype == mdns.A]
    assert a[0].data == "198.51.100.9"
    # a WAN neighbour, a routed asker: nothing
    for src in (("11.0.0.9", 50000), ("203.0.113.4", 50000), ("127.0.0.1", 50000)):
        assert r.handle(q, src) == [], src
    # a multicast query: one answer to the group, then quiet for a second
    first = r.handle(mdns.pack(questions=[(mdns.SERVICE, mdns.PTR, mdns.IN)]),
                     ("192.0.2.77", 5353))
    assert first[0][1] == (mdns.GROUP, mdns.PORT)
    assert r.handle(mdns.pack(questions=[(mdns.SERVICE, mdns.PTR, mdns.IN)]),
                    ("192.0.2.78", 5353)) == []
    # SRV/TXT of the instance, its A, the service list; not the host's own name
    for qs, want in (([(inst, mdns.TXT, mdns.IN)], {mdns.TXT, mdns.A}),
                     ([("tandemnode-0123456789ab.local", mdns.A, mdns.IN)], {mdns.A}),
                     ([(mdns.META, mdns.PTR, mdns.IN)], {mdns.PTR}),
                     ([("lab-node.local", mdns.A, mdns.IN)], None)):
        out = r.handle(mdns.pack(id_=1, questions=qs), ("192.0.2.77", 40000))
        assert (out and {x.rtype for x in mdns.unpack(out[0][0]).records}) == (want or []), qs
    # an announcement carries the records with the cache-flush bit; a goodbye TTL 0
    ann = mdns.unpack(r.announcement(r.ifaces[0]))
    assert ann.response and {x.rtype for x in ann.records} == {mdns.PTR, mdns.SRV, mdns.TXT,
                                                               mdns.A}
    assert all(x.flush for x in ann.records if x.rtype != mdns.PTR)
    assert all(x.ttl == 0 for x in mdns.unpack(r.announcement(r.ifaces[0], ttl=0)).records)
    # garbage and responses are ignored
    assert r.handle(b"\x00\x01garbage", ("192.0.2.77", 5353)) == []
    assert r.handle(r.announcement(r.ifaces[0]), ("192.0.2.77", 5353)) == []
    # the codec: a compression pointer, a loop refused
    hdr = struct.pack(">HHHHHH", 0, mdns.RESPONSE, 0, 2, 0, 0)
    rec = struct.pack(">HHIH", mdns.A, mdns.IN, 10, 4)
    msg = hdr + mdns._enc_name("a.local") + rec + socket.inet_aton("192.0.2.1") + \
        b"\xc0\x0c" + rec + socket.inet_aton("192.0.2.2")
    got = mdns.unpack(msg).records
    assert [(x.name, x.data) for x in got] == [("a.local", "192.0.2.1"), ("a.local", "192.0.2.2")]
    try:
        mdns.unpack(hdr + b"\xc0\x0c")
        raise AssertionError("a pointer loop parsed")
    except ValueError:
        pass


def test_browser():
    mono = Mono()
    lan = FakeLan(mono, responders=[node("192.0.2.60", "n-aaaaaaaaaaa1"),
                                    node("192.0.2.62", "n-aaaaaaaaaaa2", paired=True,
                                         owner=OTHER_FP, name="other-box")],
                  hosts=[ssh_host("nas-box", "192.0.2.70")])
    told = []
    got = mdns.Browser(sock=lan, clock=mono).browse(list(discovery.TYPES), 3.0,
                                                    on_found=told.append)
    assert len(got) == 3 and len(told) == 3, got
    by = {g["instance"]: g for g in got}
    assert by["lab-node"]["txt"]["id"] == "n-aaaaaaaaaaa1" and by["lab-node"]["port"] == 7700
    assert by["lab-node"]["addresses"] == ["192.0.2.60"]
    assert by["other-box"]["txt"]["paired"] == "1"
    assert by["nas-box"]["type"] == "_ssh._tcp.local" and by["nas-box"]["host"] == "nas-box.local"
    # three queries, all to the mDNS group, none elsewhere
    assert set(lan.sent) == {(mdns.GROUP, mdns.PORT)} and len(lan.sent) >= 3
    # macOS with Local Network off: LanDenied; elsewhere the OSError as it is
    for platform, want in (("darwin", mdns.LanDenied), ("linux", OSError)):
        b = mdns.Browser(sock=FakeLan(Mono(), deny=errno.EHOSTUNREACH), platform=platform)
        try:
            b.browse([mdns.SERVICE], 3.0)
            raise AssertionError("no error")
        except OSError as exc:
            assert type(exc) is want, (platform, type(exc))


def test_scan_asks_first():
    mono = Mono()
    hosts = Hosts(mono, {"192.0.2.63": UBUNTU_2404, "192.0.2.70": NAS,
                         "192.0.2.80": None})
    lan = FakeLan(mono, responders=[node("192.0.2.60", "n-aaaaaaaaaaa1")])
    fs = FleetStub()
    d = disc(fs, lan=lan, hosts=hosts, mono=mono)
    snap = ok(d.handle("POST", ["discovery"], {}, {}, "admin"), "discovery")
    # Given a run with its mDNS browse, When nobody said yes, Then no connect went out and the
    # question is data
    assert hosts.calls == [], hosts.calls
    assert set(lan.sent) == {(mdns.GROUP, mdns.PORT)}
    sc = snap["scan"]
    assert sc["state"] == "ask" and sc["question"] == "Scan 192.0.2.0/24 for SSH?" and \
        sc["subnet"] == "192.0.2.0/24" and sc["interface"] == "lan0", sc
    assert sc["timeout_ms"] == 300 and sc["rate_per_s"] == discovery.RATE_PER_S
    assert [r["kind"] for r in snap["rows"]] == ["spark"]
    # a consent for another subnet, a body with more, none: refused, still nothing sent
    for bad in ({"subnet": "198.51.100.0/24"}, {"subnet": "192.0.2.0/24", "all": True}, None,
                {"subnet": "192.0.0.0/16"}):
        try:
            d.handle("POST", ["discovery", "scan"], {}, bad, "admin")
            raise AssertionError(f"accepted {bad}")
        except discovery.DiscoveryError as exc:
            assert exc.status == 400, (bad, exc.message)
    assert hosts.calls == []
    # yes to the question: every address of the /24 once, inside it, port 22, 300 ms, paced
    snap = ok(d.handle("POST", ["discovery", "scan"], {}, {"subnet": "192.0.2.0/24"}, "admin"),
              "discovery")
    net = ipaddress.ip_network("192.0.2.0/24")
    assert len(hosts.calls) == 254 and len({c[0] for c in hosts.calls}) == 254
    assert all(ipaddress.ip_address(c[0]) in net and c[1] == 22 and c[2] == 0.3
               for c in hosts.calls)
    gaps = [b[3] - a[3] for a, b in zip(hosts.calls, hosts.calls[1:])]
    assert min(gaps) >= 1 / discovery.RATE_PER_S - 1e-9, min(gaps)
    took = hosts.calls[-1][3] - hosts.calls[0][3]
    assert 253 / discovery.RATE_PER_S - 0.01 <= took <= 253 / discovery.RATE_PER_S + 0.01
    sc = snap["scan"]
    assert (sc["state"], sc["total"], sc["done"], sc["open"], sc["by"]) == \
        ("done", 254, 254, 3, "admin"), sc
    assert fs.cfg.audits == [("admin", "scan", "discovery", "192.0.2.0/24")]
    assert any("said yes to a scan of 192.0.2.0/24" in m for m in fs.logs)
    ips = rows_by_ip(snap)
    assert {"192.0.2.60", "192.0.2.63", "192.0.2.70", "192.0.2.80"} <= set(ips)
    assert ips["192.0.2.80"]["kind"] == "unknown" and ips["192.0.2.80"]["ssh"]["banner"] is None
    assert ips["192.0.2.80"]["detail"] == "port 22 is open but sent no SSH banner"
    # a scan running cannot be started twice; DELETE stops it
    d.scan["state"] = "running"
    try:
        d.handle("POST", ["discovery", "scan"], {}, {"subnet": "192.0.2.0/24"}, "admin")
        raise AssertionError("two scans")
    except discovery.DiscoveryError as exc:
        assert (exc.status, exc.kind) == (409, "busy")
    d.handle("DELETE", ["discovery", "scan"], {}, None, "admin")
    assert d._scan_stop.is_set()
    # a new run asks again, and drops the old scan's rows
    n = len(hosts.calls)
    snap = d.handle("POST", ["discovery"], {}, {"interface": "lan0"}, "admin")
    assert snap["scan"]["state"] == "ask" and len(hosts.calls) == n
    assert "192.0.2.70" not in rows_by_ip(snap)
    try:
        d.handle("POST", ["discovery"], {}, {"interface": "wlan9"}, "admin")
        raise AssertionError("an unknown interface")
    except discovery.DiscoveryError as exc:
        assert exc.status == 400 and "lan0" in exc.message


def test_consent_is_a_write():
    lan_ip = {"X-Real-IP": "192.0.2.77"}
    bearer = {"Authorization": f"Bearer {tf.ADMIN}"}
    for login in ("on", "off"):
        with Proxy(env={"TANDEM_ADMIN_TOKEN": tf.ADMIN}, login=login) as px:
            f = tf.fleet_on(px, tf.local_node(tf.fake_root()))
            mono = Mono()
            hosts = Hosts(mono, {"192.0.2.70": NAS})
            f._discovery = disc(f, hosts=hosts, mono=mono)
            st, err = tf.get(px, "/v1/fleet/discovery", lan_ip)
            assert st == 401 and ok(err, "error")["error"]["type"] == "unauthorized", login
            st, err = tf.send(px, "POST", "/v1/fleet/discovery/scan",
                              {"subnet": "192.0.2.0/24"}, lan_ip)
            assert st == 401 and hosts.calls == [], login
            st, snap = tf.get(px, "/v1/fleet/discovery", {**lan_ip, **bearer})
            assert st == 200 and ok(snap, "discovery")["scan"]["state"] == "ask"
            st, snap = tf.send(px, "POST", "/v1/fleet/discovery/scan",
                               {"subnet": "192.0.2.0/24"}, {**lan_ip, **bearer})
            assert st == 200 and len(hosts.calls) == 254, (st, snap)
            st, got = tf.send(px, "POST", "/v1/fleet/discovery/target",
                              {"line": "ssh -p 2201 spark@198.51.100.7"}, {**lan_ip, **bearer})
            assert st == 200 and ok(got, "ssh-target")["target"]["port"] == 2201
            st, err = tf.send(px, "POST", "/v1/fleet/discovery/nope", {}, bearer)
            assert st == 404 and ok(err, "error")
            st, err = tf.get(px, "/v1/fleet/discovery?since=x", bearer)
            assert st == 400 and ok(err, "error")


def test_classified_results():
    import test_node_trust as tn
    mono = Mono()
    hosts = Hosts(mono, {"192.0.2.40": UBUNTU_2404, "192.0.2.63": UBUNTU_2404, "192.0.2.70": NAS,
                         "192.0.2.71": "SSH-2.0-OpenSSH_8.2p1 Ubuntu-4ubuntu0.11",
                         "192.0.2.72": "SSH-2.0-OpenSSH_for_Windows_9.5"})
    lan = FakeLan(mono, responders=[
        node("192.0.2.60", "n-aaaaaaaaaaa1", window=7701),
        node("192.0.2.62", "n-aaaaaaaaaaa2", paired=True, owner=OTHER_FP, name="other-box"),
        node("192.0.2.64", "n-aaaaaaaaaaa4", paired=True, owner=OWN_FP, name="mine")])
    with Proxy() as px:
        f = tf.fleet_on(px, tf.local_node(tf.fake_root()))
        f._discovery = d = disc(f, lan=lan, hosts=hosts, mono=mono)
        d.handle("POST", ["discovery"], {}, None, "admin")
        snap = ok(d.handle("POST", ["discovery", "scan"], {}, {"subnet": "192.0.2.0/24"},
                           "admin"), "discovery")
        ips = rows_by_ip(snap)
        spark, owned, mine = ips["192.0.2.60"], ips["192.0.2.62"], ips["192.0.2.64"]
        assert (spark["kind"], spark["title"]) == ("spark", "Spark with TandemLLM 0.15.0")
        assert spark["detail"] == "ASUS Ascent GX10 · TandemLLM 0.15.0 · pairing code showing " \
                                  "on the node" and spark["connect"] == ["code", "ssh"]
        assert spark["node"]["window"] == 7701 and spark["hw"]["source"] == "mdns"
        assert (owned["kind"], owned["title"]) == ("owned", "Spark owned by another TandemApp")
        assert owned["node"]["owner"] == "other" and owned["connect"] == ["takeover"]
        assert mine["kind"] == "spark" and "paired with this TandemApp" in mine["detail"]
        # the controller's own machine, found by the scan only: in the fleet, whatever its banner
        here = ips["192.0.2.40"]
        assert (here["kind"], here["fleet"], here["detail"], here["connect"]) == \
            ("spark", f.ensure_local(), "Spark · in this fleet", []), here
        # And a NAS shows "Linux host, not a Spark"; an older Ubuntu and Windows are no Spark
        nas = ips["192.0.2.70"]
        assert (nas["kind"], nas["title"]) == ("host", "Linux host, not a Spark"), nas
        assert nas["connect"] == [] and nas["ssh"]["os"] == "Linux (dropbear)"
        assert ips["192.0.2.71"]["title"] == "Linux host, not a Spark"
        assert "Ubuntu 20.04" in ips["192.0.2.71"]["detail"]
        assert ips["192.0.2.72"]["title"] == "Windows host, not a Spark"
        # Given a GX10 with nothing installed: before a login its banner only allows a Spark
        gx = ips["192.0.2.63"]
        assert gx["kind"] == "unknown" and "Ubuntu 24.04, as DGX OS" in gx["detail"]
        assert gx["connect"] == ["ssh"]
        # When the SSH-once login runs there, Then its DMI says "GB10 box, nothing installed"
        dmi_dir = tempfile.mkdtemp(prefix="disc-dmi-")
        for k, v in (("sys_vendor", "ASUSTeK COMPUTER INC."), ("product_name", "GX10"),
                     ("product_version", ""), ("product_family", "DGX Spark")):
            with open(os.path.join(dmi_dir, k), "w") as fh:
                fh.write(v + "\n")

        class NoNode:
            port, state = 7700, tempfile.mkdtemp(prefix="disc-none-")
        fake = tn.FakeSsh(NoNode)
        fake.unit(None)
        fake.st["env"] = {"TANDEM_DMI_DIR": dmi_dir}
        t = f.trust()
        t.ssh = fake.ssh(t.ssh.known_hosts)
        st, err = tf.send(px, "POST", "/v1/fleet/pair/ssh", {
            "host": "192.0.2.63", "user": "spark", "password": tn.PASSWORD})
        assert st == 409 and ok(err, "error")["error"]["type"] == "not_installed", err
        assert "(ASUS Ascent GX10, a GB10)" in err["error"]["message"], err
        snap2 = ok(tf.get(px, f"/v1/fleet/discovery?since={snap['cursor']}")[1], "discovery")
        assert not snap2["full"] and [r["addresses"] for r in snap2["rows"]] == [["192.0.2.63"]]
        gx = snap2["rows"][0]
        assert (gx["kind"], gx["title"]) == ("gb10", "GB10 box, nothing installed"), gx
        assert gx["detail"] == "ASUS Ascent GX10 · TandemNode is not installed"
        assert gx["hw"] == {"label": "ASUS Ascent GX10", "spark": True, "source": "dmi"}
        assert gx["sources"] == ["scan", "login"] and gx["ssh"]["host_key"].startswith("SHA256:")
        assert any(e.startswith("DMI over SSH: ASUSTeK") for e in gx["evidence"])
        # a NAS logged into the same way (its own host key) stays a host by its DMI
        fake.st["hostkey"] = tn.OTHER_HOSTKEY
        fake.save()
        for k, v in (("sys_vendor", "Synology Inc."), ("product_name", "DS920+"),
                     ("product_family", "")):
            with open(os.path.join(dmi_dir, k), "w") as fh:
                fh.write(v + "\n")
        st, err = tf.send(px, "POST", "/v1/fleet/pair/ssh", {
            "host": "192.0.2.70", "user": "admin", "password": tn.PASSWORD})
        assert st == 409 and "not a Spark" in err["error"]["message"]
        nas = rows_by_ip(d.snapshot())["192.0.2.70"]
        assert nas["kind"] == "host" and nas["hw"]["label"] == "Synology Inc. DS920+"
        # a pairing by code refused as owned marks the row
        d.learn("192.0.2.66", owned=True)
        assert rows_by_ip(d.snapshot())["192.0.2.66"]["kind"] == "owned"


def test_one_row_per_box():
    b64, fp = key_line(b"box-61")
    b64b, fpb = key_line(b"box-60")
    salt = b"0123456789abcdefghij"
    known = (f"{hashed('192.0.2.61', salt)} ssh-ed25519 {b64}\n"
             f"lab-west.example ssh-ed25519 {b64}\n"
             f"@revoked * ssh-ed25519 {b64b}\n")
    config = ("Host lab-a\n  HostName 192.0.2.60\n  User spark\n\n"
              "Host lab-west\n  HostName lab-west.example\n  Port 22\n\n"
              "Host *\n  User fallback\n")
    mono = Mono()
    hosts = Hosts(mono, {"192.0.2.60": UBUNTU_2404, "192.0.2.61": UBUNTU_2404})
    lan = FakeLan(mono, responders=[node("192.0.2.60", "n-aaaaaaaaaaa1", hk=fpb),
                                    node("192.0.2.60", "n-bbbbbbbbbbb1", virtual="v1")],
                  hosts=[ssh_host("lab-node", "192.0.2.60")])
    fleet = FleetStub(nodes=[{"id": "n-aaaaaaaaaaa1", "address": "192.0.2.60"}])
    d = disc(fleet, lan=lan, hosts=hosts, home=home_with(config, known), mono=mono)
    first = d.handle("POST", ["discovery"], {}, None, "admin")
    snap = ok(d.handle("POST", ["discovery", "scan"], {}, {"subnet": "192.0.2.0/24"},
                       "admin"), "discovery")
    rows = snap["rows"]
    # 192.0.2.60: mDNS (TandemNode and _ssh._tcp), the scan and ~/.ssh/config: one row
    at60 = [r for r in rows if "192.0.2.60" in r["addresses"]]
    real = next(r for r in at60 if r["node"] and not r["node"]["virtual"])
    assert real["sources"] == ["mdns", "scan", "ssh_config"], real
    assert real["ssh_config"]["alias"] == "lab-a" and real["ssh_config"]["user"] == "spark"
    assert real["fleet"] == "n-aaaaaaaaaaa1" and real["connect"] == []
    assert "in this fleet" in real["detail"] and real["ssh"]["host_key"] == fpb
    # the virtual node on the same address keeps its own row
    virt = [r for r in at60 if r is not real]
    assert len(virt) == 1 and virt[0]["node"]["id"] == "n-bbbbbbbbbbb1" and \
        virt[0]["sources"] == ["mdns"] and virt[0]["fleet"] is None
    # 192.0.2.61 from the scan and lab-west from ~/.ssh/config: one row by the host key
    west = [r for r in rows if r["ssh_config"] and r["ssh_config"]["alias"] == "lab-west"]
    assert len(west) == 1 and west[0]["addresses"] == ["192.0.2.61"], west
    assert west[0]["ssh"]["host_key"] == fp and west[0]["sources"] == ["scan", "ssh_config"]
    assert len(rows) == 3, [(r["addresses"], r["sources"]) for r in rows]
    assert len({r["id"] for r in rows}) == 3
    # the cursor: the rows the scan changed, and the scan-only row 192.0.2.61 had before merging
    later = d.snapshot(first["cursor"])
    assert not later["full"] and {r["id"] for r in later["rows"]} <= {r["id"] for r in rows}
    assert d.snapshot(later["cursor"])["rows"] == []
    assert d.snapshot(-1)["full"]


def test_local_network_denied():
    mono = Mono()
    hosts = Hosts(mono, {"192.0.2.61": UBUNTU_2404}, agents={("192.0.2.61", 7701)})
    lan = FakeLan(mono, deny=errno.EHOSTUNREACH)
    d = disc(lan=lan, hosts=hosts, platform="darwin", mono=mono,
             home=home_with("Host lab-a\n  HostName 192.0.2.60\n"))
    before = ok(d.snapshot(), "discovery")
    assert before["local_network"]["state"] == "not_asked"
    # Given macOS with Local Network off, When Find runs, Then the API says so
    snap = ok(d.handle("POST", ["discovery"], {}, None, "admin"), "discovery")
    ln = snap["local_network"]
    assert ln["state"] == "denied" and "Local Network" in ln["settings"], ln
    assert "add by address still works" in ln["text"]
    assert snap["mdns"]["state"] == "denied" and snap["scan"]["state"] == "denied"
    assert snap["scan"]["question"] == "Scan 192.0.2.0/24 for SSH?"
    # ~/.ssh/config needs no network
    assert [r["ssh_config"]["alias"] for r in snap["rows"]] == ["lab-a"]
    try:
        d.handle("POST", ["discovery", "scan"], {}, {"subnet": "192.0.2.0/24"}, "admin")
        raise AssertionError("scanned while denied")
    except discovery.DiscoveryError as exc:
        assert (exc.status, exc.kind) == (409, "lan_denied")
    assert hosts.calls == []
    # And add by address works
    got = ok(d.handle("POST", ["discovery", "address"], {}, {"address": "192.0.2.61"}, "admin"),
             "discovered")
    row = got["row"]
    assert row["addresses"] == ["192.0.2.61"] and row["sources"] == ["address"]
    assert row["ssh"]["banner"] == UBUNTU_2404 and row["kind"] == "unknown"
    assert [c[1] for c in hosts.calls] == [22, 7700, 7701]
    for bad in ({"address": "spark.local"}, {"address": "192.0.2.61", "port": 0},
                {"address": "127.0.0.1"}, {"host": "192.0.2.61"}):
        try:
            d.handle("POST", ["discovery", "address"], {}, bad, "admin")
            raise AssertionError(f"accepted {bad}")
        except discovery.DiscoveryError as exc:
            assert exc.status == 400
    # the same on Linux: no permission at all
    assert disc().snapshot()["local_network"]["state"] == "not_needed"
    # the permission comes back: the next run says granted and asks the question again
    lan.deny = None
    snap = d.handle("POST", ["discovery"], {}, None, "admin")
    assert snap["local_network"]["state"] == "granted" and snap["scan"]["state"] == "ask"


def test_classification_tables():
    dmi = [
        (("NVIDIA", "DGX Spark", None, "DGX Spark"), "NVIDIA DGX Spark"),
        (("ASUSTeK COMPUTER INC.", "GX10", None, "DGX Spark"), "ASUS Ascent GX10"),
        (("Dell Inc.", "Dell Pro Max with GB10 FCM1253", None, None), "Dell Pro Max with GB10"),
        (("LENOVO", "30KLCTO1WW", "ThinkStation PGX", None), "Lenovo ThinkStation PGX"),
        (("Micro-Star International Co., Ltd.", "MS-C931", None, None), "MSI EdgeXpert"),
        (("GIGABYTE", "AI TOP ATOM", None, None), "Gigabyte AI TOP ATOM"),
        (("Acer", "Veriton GN100", None, None), "Acer Veriton GN100"),
        (("HP", "HP ZGX Nano G1n AI Station", None, None), "HP ZGX Nano G1n"),
    ]
    assert len(dmi) == len(discovery.GB10)
    for (v, p, ver, fam), label in dmi:
        assert discovery.classify_dmi(v, p, ver, fam) == (label, True), (v, p)
    vendors = {row[0] for row in discovery.GB10}
    assert {"NVIDIA", "ASUS", "Dell", "Lenovo", "MSI", "Gigabyte"} <= vendors
    assert all(row[4] for row in discovery.GB10)                     # each with its source
    # an OEM not in the table: its product family says DGX Spark
    assert discovery.classify_dmi("Newco", "NC-1", None, "DGX Spark") == ("Newco NC-1", True)
    for v, p in (("Synology Inc.", "DS920+"), ("Dell Inc.", "PowerEdge R750"),
                 ("LENOVO", "ThinkPad X1"), ("Micro-Star International Co., Ltd.", "MS-7C02")):
        assert discovery.classify_dmi(v, p) == (f"{v} {p}", False), (v, p)
    assert discovery.classify_dmi(None, None) is None
    banners = [
        (UBUNTU_2404, ("candidate", "Ubuntu 24.04")),
        ("SSH-2.0-OpenSSH_10.0p1 Ubuntu-5ubuntu1", ("candidate", "Ubuntu 25.10")),
        ("SSH-2.0-OpenSSH_8.9p1 Ubuntu-3ubuntu0.10", ("not_spark", "Ubuntu 22.04")),
        ("SSH-2.0-OpenSSH_9.2p1 Debian-2+deb12u3", ("not_spark", "Linux (Debian)")),
        (NAS, ("not_spark", "Linux (dropbear)")),
        ("SSH-2.0-OpenSSH_for_Windows_9.5", ("not_spark", "Windows")),
        ("SSH-2.0-OpenSSH_9.6p1", ("candidate", "OpenSSH 9.6p1, as on Ubuntu 24.04")),
        ("SSH-2.0-OpenSSH_9.8", ("not_spark", "OpenSSH 9.8")),
        (None, ("unknown", "no SSH banner")),
        ("HTTP/1.1 400 Bad Request", ("unknown", "no SSH banner")),
    ]
    for b, want in banners:
        assert discovery.banner_os(b) == want, (b, discovery.banner_os(b))


def test_ssh_config():
    h = home_with(
        "User everyone\n"
        "Include conf.d/*\n"
        "Host lab-a lab-b\n  HostName %h.example\n  Port 2222\n"
        "Host lab-a\n  Port 22\n  IdentityFile ~/.ssh/lab_ed25519\n"
        "Match host lab-b\n  User matched\n"
        "Host rented\n  HostName 198.51.100.7\n  ProxyJump hop@198.51.100.1:2200\n"
        "Host !lab-b *\n  ServerAliveInterval 30\n  User star\n"
        "Host *.wild ?x\n  User never\n")
    os.makedirs(os.path.join(h, ".ssh", "conf.d"))
    with open(os.path.join(h, ".ssh", "conf.d", "extra"), "w") as fh:
        fh.write("Host included\n  HostName 192.0.2.90\n  User inc\n")
    got = {x["alias"]: x for x in discovery.ssh_config(h)}
    assert list(got) == ["included", "lab-a", "lab-b", "rented"], list(got)
    assert got["lab-a"] == {"alias": "lab-a", "hostname": "lab-a.example", "user": "everyone",
                            "port": 2222, "identity_file": "~/.ssh/lab_ed25519",
                            "proxy_jump": None}
    assert got["lab-b"]["port"] == 2222 and got["lab-b"]["user"] == "everyone"
    assert got["rented"]["proxy_jump"] == "hop@198.51.100.1:2200"
    assert got["included"]["hostname"] == "192.0.2.90"
    assert discovery.ssh_config(tempfile.mkdtemp()) == []


def test_ssh_line():
    p = discovery.parse_ssh_line
    t = p("ssh -p 2201 spark@198.51.100.7")
    assert (t["user"], t["host"], t["port"], t["remote"], t["jumps"]) == \
        ("spark", "198.51.100.7", 2201, False, []), t
    assert t["route"] == [{"kind": "node", "user": "spark", "host": "198.51.100.7",
                           "port": 2201}]
    t = p("ssh -i ~/.ssh/rent_key -J hop@203.0.113.9:2200,[2001:db8::1]:22 -p2201 "
          "spark@node7.rent.example")
    assert t["identity_file"] == "~/.ssh/rent_key" and t["port"] == 2201 and t["remote"]
    assert t["jumps"] == [{"user": "hop", "host": "203.0.113.9", "port": 2200},
                          {"user": None, "host": "2001:db8::1", "port": 22}]
    assert [r["kind"] for r in t["route"]] == ["jump", "jump", "node"]
    t = p("ssh://spark@198.51.100.7:2201")
    assert (t["user"], t["host"], t["port"]) == ("spark", "198.51.100.7", 2201)
    t = p("-o Port=2202 -o 'User spark' -o ProxyJump=hop@198.51.100.1 -l other "
          "-L 8000:localhost:8000 -A 198.51.100.7 uptime")
    assert (t["user"], t["port"], t["jumps"][0]["host"]) == ("spark", 2202, "198.51.100.1")
    assert "-L 8000:localhost:8000 is not used" in t["warnings"]
    assert "the command after the host is not used" in t["warnings"]
    assert p("ssh spark@lab-a.local")["remote"] is False
    for bad in ("ssh -o ProxyCommand='nc %h %p' h", "ssh -o LocalCommand=x h", "ssh -p", "ssh",
                "ssh -p 70000 h", "ssh 'a b'@h", "ssh -Z h", "ssh h;rm", "ssh -J 'x y' h",
                "ssh -p two h", "", "ssh 'unclosed"):
        try:
            p(bad)
            raise AssertionError(f"parsed {bad!r}")
        except ValueError:
            pass


def test_node_advertisement():
    import test_node_agent as tna
    root = tna.machine(serial="DISC-NODE-0001")
    for k, v in (("sys_vendor", "ASUSTeK COMPUTER INC."), ("product_name", "GX10"),
                 ("product_family", "DGX Spark")):
        tf._write(root, f"sys/class/dmi/id/{k}", v + "\n")
    b64, fp = key_line(b"node-host-key")
    tf._write(root, "etc/ssh/ssh_host_ed25519_key.pub", f"ssh-ed25519 {b64} root@lab-node\n")
    a = tna.agent_on(root, addrs=lambda: {"lan0": ["192.0.2.5/24"], "lo": ["127.0.0.1/8"],
                                          "wan0": ["11.0.0.5/24"]})
    a.listening["127.0.0.1"] = {"mode": "secret", "port": 7700}
    info = a.mdns_info()
    txt = info["txt"]
    assert txt["id"] == a.node_id() and txt["class"] == "spark" and txt["paired"] == "0"
    assert txt["hw"] == "ASUS Ascent GX10" and txt["listen"] == "loopback" and txt["hk"] == fp
    assert txt["ver"] and "owner" not in txt and "window" not in txt and "virtual" not in txt
    assert info["target"] == "tandemnode-" + a.node_id()[2:14] and info["port"] == a.port
    # a pairing window: its port in the TXT, until it closes
    w = a.open_window({"address": "127.0.0.1", "port": 0}, "test")
    try:
        assert a.mdns_info()["txt"]["window"] == str(w["port"])
        code = w["code"]
    finally:
        a.close_window()
    assert "window" not in a.mdns_info()["txt"]
    # paired: the owner's fingerprint prefix, public; never a secret
    d = tempfile.mkdtemp(prefix="disc-ctl-")
    nodetrust.make_identity(os.path.join(d, "c.crt"), os.path.join(d, "c.key"), "TandemApp x")
    pem = open(os.path.join(d, "c.crt")).read()
    ctl_fp = a.store.set_owner(pem, {"name": "TandemApp x", "via": "code", "paired_at": 1.0})
    txt = a.mdns_info()["txt"]
    assert txt["paired"] == "1" and txt["owner"] == ctl_fp[:16]
    blob = json.dumps(a.mdns_info())
    key = open(a.store.key).read()
    for secret in [a.secret, code] + [ln for ln in key.splitlines() if "-----" not in ln]:
        assert secret not in blob
    # what it says on the LAN: only on the LAN address, never on loopback or the WAN one
    r = mdns.Responder(a.mdns_info, lambda: [x for v in a.addrs().values() for x in v],
                       clock=lambda: 0)
    r.ifaces = mdns.allowed(r.addrs())
    assert [str(i.ip) for i in r.ifaces] == ["192.0.2.5"]
    out = r.handle(mdns.pack(id_=3, questions=[(mdns.SERVICE, mdns.PTR, mdns.IN)]),
                   ("192.0.2.77", 50000))
    recs = {x.rtype: x.data for x in mdns.unpack(out[0][0]).records}
    assert recs[mdns.TXT]["id"] == a.node_id() and recs[mdns.A] == "192.0.2.5"
    assert r.handle(mdns.pack(questions=[(mdns.SERVICE, mdns.PTR, mdns.IN)]),
                    ("11.0.0.9", 50000)) == []
    # a virtual node: its own id and name, no host key (it shares its machine's)
    v = tna.agent_on(root, virtual="v1")
    vi = v.mdns_info()
    assert vi["txt"]["virtual"] == "v1" and "hk" not in vi["txt"]
    assert vi["txt"]["id"] != txt["id"] and vi["instance"].endswith("-v1")
    # --mdns off goes into the unit
    args = __import__("proxy.nodeagent", fromlist=["x"]).parser().parse_args(["--mdns", "off"])
    assert "--mdns off" in __import__("proxy.nodeagent", fromlist=["x"]).unit_text(args)
    for x in (a, v):
        x.sampler.close()


if __name__ == "__main__":
    run(globals())
