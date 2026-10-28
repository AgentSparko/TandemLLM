"""The link test between two TandemNode agents (SRV-725): RTT, bandwidth per logical interface,
MTU, RoCE state, and a port whose cable reaches only one of its logical interfaces.

    info = interfaces(reader, addrs)          this node's interfaces (GET /node/linktest/info)
    result = run(client, info, ...)           this node to the peer `client` speaks to

WHAT IS MEASURED, AND HOW.
- RTT: `rtt_n` round trips of GET /node/linktest/echo over one kept-alive connection to the
  peer's agent address; min, median, p90 in ms. An HTTP round trip: a floor of the agent's own
  work on top of the wire.
- Bandwidth: per local IPv4 interface that has a peer address in the same subnet, `streams` TCP
  connections from that address to the peer's agent there (the agent's port) each POST
  `bytes / streams` to /node/linktest/sink; Gb/s over the slowest stream's wall time. Through the
  agent (TLS when paired), so it is a floor of what the link carries, never a claim of RDMA speed.
  The same over the agent's own address is `channel`.
- MTU: this interface's and the peer's; with `ping` (Linux) a do-not-fragment probe of that size
  says whether the path carries it.
- RoCE: the RDMA port behind a ConnectX interface, its state and link layer (sysfs).
- Half bandwidth: the ConnectX interfaces grouped by physical port (`phys_port_name`, else the
  `np<N>` suffix). A DGX Spark shows each QSFP port as two logical interfaces, one per PCIe half;
  the port's full rate needs both. A port with some but not all of its interfaces carrying the
  link (up, an address the peer shares, bandwidth measured) is flagged `half_bandwidth`.
A field without a source is absent: no interface address, no ping, no RDMA device.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import statistics
import subprocess
import threading
import time

CHUNK = 1 << 20
DEFAULT_BYTES = 256 << 20
MAX_BYTES = 8 << 30
DEFAULT_STREAMS = 4
DEFAULT_RTT_N = 20


def ip_addrs(run=None) -> dict:
    """{interface: ["10.0.0.1/24", ...]} of IPv4 addresses (`ip -j -4 addr`); {} without `ip`."""
    try:
        r = (run or _run)(["ip", "-j", "-4", "addr", "show"])
        doc = json.loads(r) if r else []
    except ValueError:
        return {}
    out = {}
    for d in doc if isinstance(doc, list) else []:
        for a in d.get("addr_info") or []:
            if a.get("family") == "inet" and a.get("local"):
                out.setdefault(d.get("ifname"), []).append(f"{a['local']}/{a.get('prefixlen', 32)}")
    return out


def _run(argv: list[str]) -> str | None:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def port_of(reader, iface: str) -> str:
    name = reader._read("sys", "class", "net", iface, "phys_port_name")
    if name:
        return name
    m = re.search(r"n(p\d+)$", iface)
    return m.group(1) if m else iface


def interfaces(reader, addrs: dict) -> list[dict]:
    """This node's physical interfaces (a device behind them) and ConnectX ports, with their
    IPv4 addresses, MTU, speed, state; a ConnectX one also its physical port and RoCE state."""
    fabric = reader.local.fabric_ports()
    out = []
    for n in reader._ls("sys", "class", "net"):
        if n not in fabric and not os.path.exists(reader._p("sys", "class", "net", n, "device")):
            continue
        sp = reader._int("sys", "class", "net", n, "speed")
        row = {"iface": n, "kind": "fabric" if n in fabric else "lan",
               "addrs": addrs.get(n) or [], "mtu": reader._int("sys", "class", "net", n, "mtu"),
               "speed_mbps": sp if sp is not None and sp > 0 else None,
               "state": reader._read("sys", "class", "net", n, "operstate")}
        if n in fabric:
            row["port"] = port_of(reader, n)
            rp = reader.rdma_port(n)
            if rp is not None:
                ib = ("sys", "class", "infiniband", rp[0], "ports", rp[1])
                st = reader._read(*ib, "state")
                row["roce"] = {"device": rp[0], "port": rp[1],
                               "state": st.split(":")[-1].strip().lower() if st else None,
                               "link_layer": reader._read(*ib, "link_layer")}
        out.append(row)
    return out


def _match(local: list[str], peer: list[str]) -> tuple[str, str] | None:
    """(local address, peer address) of the first pair in one subnet."""
    for a in local:
        net = ipaddress.ip_interface(a).network
        for b in peer:
            pb = ipaddress.ip_interface(b)
            if pb.ip in net and pb.ip != ipaddress.ip_interface(a).ip:
                return str(ipaddress.ip_interface(a).ip), str(pb.ip)
            if pb.ip in net and pb.ip.is_loopback:
                return str(ipaddress.ip_interface(a).ip), str(pb.ip)
    return None


def rtt(client, n: int) -> dict | None:
    conn = client.connection()
    got = []
    try:
        conn.connect()
        conn.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        for _ in range(n):
            t0 = time.perf_counter()
            conn.request("GET", "/node/linktest/echo", headers=client.headers())
            r = conn.getresponse()
            r.read()
            if r.status != 200:
                return None
            got.append((time.perf_counter() - t0) * 1000.0)
    except OSError:
        return None
    finally:
        conn.close()
    got.sort()
    return {"n": len(got), "min_ms": round(got[0], 3), "median_ms": round(statistics.median(got), 3),
            "p90_ms": round(got[min(len(got) - 1, int(len(got) * 0.9))], 3)}


def bandwidth(client, host: str | None, source: str | None, nbytes: int, streams: int) -> dict:
    """{gbps, bytes, streams, s} or {error}."""
    per = max(CHUNK, nbytes // streams)
    buf = memoryview(b"\0" * CHUNK)
    res: list = [None] * streams

    def one(i):
        try:
            conn = client.connection(host=host, source=source, timeout=60.0)
            t0 = time.perf_counter()
            conn.putrequest("POST", "/node/linktest/sink")
            for k, v in client.headers().items():
                conn.putheader(k, v)
            conn.putheader("Content-Type", "application/octet-stream")
            conn.putheader("Content-Length", str(per))
            conn.endheaders()
            left = per
            while left:
                n = min(left, CHUNK)
                conn.send(buf[:n])
                left -= n
            r = conn.getresponse()
            r.read()
            res[i] = (r.status, time.perf_counter() - t0)
            conn.close()
        except OSError as exc:
            res[i] = (0, str(exc) or type(exc).__name__)
    ts = [threading.Thread(target=one, args=(i,)) for i in range(streams)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    bad = [r for r in res if r is None or r[0] != 200]
    if bad:
        why = bad[0][1] if bad[0] and bad[0][0] == 0 else f"HTTP {bad[0][0] if bad[0] else '?'}"
        return {"error": f"the peer's agent does not take the test on "
                         f"{host or client.host}:{client.port} ({why})"}
    s = max(r[1] for r in res)
    return {"gbps": round(per * streams * 8 / s / 1e9, 3), "bytes": per * streams,
            "streams": streams, "s": round(s, 3)}


def path_mtu(iface: str, peer: str, mtu: int, run=None) -> bool | None:
    """Whether a do-not-fragment ping of `mtu` bytes reaches `peer` out of `iface`; None without
    ping or off Linux."""
    if mtu is None or mtu <= 28 or (run is None and not os.path.isdir("/proc/sys/net")):
        return None
    r = (run or _run_rc)(["ping", "-M", "do", "-s", str(mtu - 28), "-c", "1", "-W", "1",
                          "-I", iface, peer])
    return r


def _run_rc(argv) -> bool | None:
    try:
        return subprocess.run(argv, capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return None


def run(client, local: list[dict], *, nbytes: int = DEFAULT_BYTES,
        streams: int = DEFAULT_STREAMS, rtt_n: int = DEFAULT_RTT_N, ping=None) -> dict:
    """This node to the peer behind `client` (its agent's address): see the module's text."""
    try:
        st, peer = client.json("GET", "/node/linktest/info")
    except OSError as exc:
        st, peer = f"{type(exc).__name__}", None
    if st != 200 or not isinstance(peer, dict):
        return {"peer": client.url, "error": f"the peer's agent did not answer its link info "
                                             f"({st if isinstance(st, str) else f'HTTP {st}'})"}
    out = {"peer": client.url, "peer_id": peer.get("id"), "rtt": rtt(client, rtt_n),
           "channel": dict(bandwidth(client, None, None, nbytes, streams),
                           address=client.host),
           "interfaces": [], "ports": [], "half_bandwidth": False, "notes": []}
    carrying: dict = {}
    for li in local:
        row = {k: li.get(k) for k in ("iface", "kind", "mtu", "speed_mbps", "state", "port",
                                       "roce") if li.get(k) is not None}
        pair = None
        for pi in peer.get("interfaces") or []:
            pair = _match(li.get("addrs") or [], pi.get("addrs") or [])
            if pair is not None:
                row["peer_iface"] = pi["iface"]
                row["peer_mtu"] = pi.get("mtu")
                break
        same = {ipaddress.ip_interface(a).ip for a in li.get("addrs") or []} & \
            {ipaddress.ip_interface(b).ip for pi in peer.get("interfaces") or []
             for b in pi.get("addrs") or []}
        if pair is None:
            row["result"] = "no IPv4 address" if not li.get("addrs") else \
                "the peer has this address: it is this node" if same else \
                "no peer address in this interface's subnet"
        elif li.get("state") not in ("up", "unknown"):
            row["result"] = f"the interface is {li.get('state') or 'down'}"
            pair = None
        if pair is not None:
            row["local_addr"], row["peer_addr"] = pair
            if row.get("peer_mtu") is not None and li.get("mtu") is not None:
                row["mtu_match"] = row["peer_mtu"] == li["mtu"]
            pm = path_mtu(li["iface"], pair[1], li.get("mtu"), ping)
            if pm is not None:
                row["path_mtu_ok"] = pm
            bw = bandwidth(client, pair[1], pair[0], nbytes, streams)
            row.update(bw)
            row["result"] = "measured" if "gbps" in bw else "the peer did not take the test"
        if li.get("kind") == "fabric":
            carrying.setdefault(li.get("port") or li["iface"], []).append(
                (li["iface"], "gbps" in row))
        out["interfaces"].append(row)
    for port, members in sorted(carrying.items()):
        n = sum(1 for _, ok in members if ok)
        half = len(members) > 1 and 0 < n < len(members)
        out["ports"].append({"port": port, "interfaces": [m for m, _ in members],
                             "carrying": n, "total": len(members), "half_bandwidth": half})
        if half:
            out["half_bandwidth"] = True
            out["notes"].append(f"port {port}: {n} of {len(members)} logical interfaces carry "
                                f"the link, so it runs at about half its rate; the other needs "
                                f"its link up and an address in the peer's subnet")
    if not carrying:
        out["notes"].append("no ConnectX interface on this node: no fabric to test")
    out["notes"].append("bandwidth is TCP through the agents (a floor of the link), not RDMA")
    return out
