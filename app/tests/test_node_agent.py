"""SRV-726: TandemNode, the agent on each Spark (SRV-725), and the controller reading a node through it.

The machine is a fake: a directory standing for /etc, /proc and /sys (two ConnectX interfaces on
one physical port, one of them down; an RDMA device; an NVMe sensor; no fan), a host collector's
entry and an NVML that names its clock event reasons. Engines are fakes on free loopback ports;
the engine a box serves on :8000 is never addressed. Time is injected where the cadence is tested.

  1  identity and inventory: the machine's id, DMI, OS, driver, CUDA; disks, ConnectX ports with
     their firmware and RoCE state                                   test_identity_inventory
  2  the 1 s stream: whole seconds, no burst after a slow read, one sampler for every listener,
     none while nobody listens; over HTTP with an injected clock     test_metrics_cadence
  3  a field the board does not expose is absent: no fan, no GPU memory, no page cache without
     /proc/meminfo; throttle reasons by name                         test_absent_fields
  4  callers: no secret, a wrong secret, a secret file others can read; mTLS without a
     certificate, with one nobody pinned, with the pinned one       test_auth_refusal
  5  the bind rule: loopback only until paired, never 0.0.0.0, never a WAN address, never a name;
     the unit text (`tandem node check` and `unit` through install.sh's bin/tandem are the
     engine's: tests/test_engine_scripts.py)                         test_bind_rule
  6  engines: list, start, stop, restart a fake engine (foreground, and one that daemonizes with
     its own stop command), refused when not managed or busy; its routes relayed, its live stream
     passed on as it comes                                           test_engines_on_a_fake_engine
  7  logs: the agent's and the engine's by level, text and source; the stream gets a new line
                                                                     test_logs_filter
  8  link test against a fake peer: RTT, bandwidth per interface, MTU, RoCE, a port with one of
     its two interfaces down flagged half bandwidth                  test_linktest_fake_peer
  9  /node/setup/*: the engine's answer byte for byte, a POST's body as sent and no secret passed
     on, an event stream, the status responder while the engine is down; POST estimate relayed as
     every route (503 engine_down, no_engine: no estimate of the agent's own); weights with
     content ids and the weights store, a removal as the Setup Guide takes it
                                                                     test_setup_delegation
 10  the controller: a node read through its agent in /v1/fleet/* (nodes, metrics from the 1 s
     stream, memory, logs, deployments through the relay), this machine switching to its agent
     and back, a remote agent node, an unpaired one                  test_agent_node_in_fleet
 11  the machine's reboot and update (VIS-250): refused until node.json sets them up, a job that
     ends done or failed, busy; a virtual node simulates both and never runs a command, and has
     no engine of the machine's                                      test_machine_actions
 12  the controller's node actions through the agent: an engine's restart with its plan's
     confirm, refused for an engine TandemNode did not start; reboot refused when not set up and
     simulated on a virtual node; a LAN link's test                  test_actions_through_the_agent

    python tests/test_node_agent.py
"""

from __future__ import annotations

import datetime
import http.client
import json
import os
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_fleet as tf  # noqa: E402
from ports import free_port  # noqa: E402
from proxy import nodeagent, nodeclient, nodeengines, nodemaint, nodemetrics  # noqa: E402
from proxy import nodesource, nodetrust  # noqa: E402
from proxy_util import Proxy, run  # noqa: E402
from tandemapp import logbuf  # noqa: E402

GIB = 1024 ** 3


# ---------------------------------------------------------------------------- a fake machine

def machine(serial=tf.SERIAL) -> str:
    """test_fleet's fake root plus what the agent reads besides: cores of two kinds, thermal zones,
    an NVMe sensor, an RDMA device behind fab0, fab1 on the same physical port and down."""
    r = tf.fake_root(serial=serial, fabric_bytes=10_000)
    w = tf._write
    w(r, "proc/stat", "cpu  400 0 100 1500 0 0 0 0 0 0\n"
                      "cpu0 100 0 25 375 0 0 0 0 0 0\ncpu1 100 0 25 375 0 0 0 0 0 0\n"
                      "cpu2 100 0 25 375 0 0 0 0 0 0\ncpu3 100 0 25 375 0 0 0 0 0 0\n")
    for c, cap in ((0, 1024), (1, 1024), (2, 718), (3, 718)):
        w(r, f"sys/devices/system/cpu/cpu{c}/cpu_capacity", f"{cap}\n")
        w(r, f"sys/devices/system/cpu/cpu{c}/cpufreq/scaling_cur_freq", "2808000\n")
    for z, t in ((0, 40400), (1, 37900)):
        w(r, f"sys/class/thermal/thermal_zone{z}/type", "acpitz\n")
        w(r, f"sys/class/thermal/thermal_zone{z}/temp", f"{t}\n")
    w(r, "sys/class/hwmon/hwmon1/name", "nvme\n")
    w(r, "sys/class/hwmon/hwmon1/temp1_input", "41850\n")
    os.makedirs(os.path.join(r, "sys/class/nvme/nvme0"))
    os.symlink(os.path.join(r, "sys/class/nvme/nvme0"), os.path.join(r, "sys/class/hwmon/hwmon1/"
                                                                         "device"))
    w(r, "sys/block/nvme0n1/size", "7814037168\n")
    w(r, "sys/block/nvme0n1/device/model", "TEST NVME 4TB\n")
    w(r, "sys/block/nvme0n1/queue/rotational", "0\n")
    w(r, "sys/block/loop0/size", "8\n")
    w(r, "proc/uptime", "4911.25 90000.00\n")
    w(r, "proc/mounts", f"/dev/nvme0n1p2 {r} ext4 rw 0 0\ntmpfs /run tmpfs rw 0 0\n")
    w(r, "sys/class/net/fab0/phys_port_name", "p0\n")
    w(r, "sys/class/net/fab0/address", "aa:bb:cc:00:00:01\n")
    os.makedirs(os.path.join(r, "sys/class/net/fab0/device/infiniband/mlx5_0"))
    ib = "sys/class/infiniband/mlx5_0"
    w(r, f"{ib}/fw_ver", "28.45.1020\n")
    w(r, f"{ib}/board_id", "NVD0000000033\n")
    w(r, f"{ib}/ports/1/state", "4: ACTIVE\n")
    w(r, f"{ib}/ports/1/link_layer", "Ethernet\n")
    w(r, f"{ib}/ports/1/counters/port_rcv_data", "1000\n")
    w(r, f"{ib}/ports/1/counters/port_xmit_data", "0\n")
    for n, v in (("speed", "-1"), ("mtu", "9000"), ("operstate", "down"),
                 ("phys_port_name", "p0")):
        w(r, f"sys/class/net/fab1/{n}", v + "\n")
    os.makedirs(os.path.join(r, "sys/class/net/fab1/device"))
    os.symlink(os.path.join(r, "drivers/mlx5_core"), os.path.join(r, "sys/class/net/fab1/device/"
                                                                       "driver"))
    w(r, "sys/class/net/fab0/mtu", "9000\n")
    return r


class FakeNvml:
    """The NVML calls the agent adds: clock event reasons (SW power cap, HW thermal) and the
    video engines; no fan, as on a GB10."""

    def nvmlDeviceGetHandleByIndex(self, i):
        return i

    def nvmlDeviceGetCurrentClocksEventReasons(self, h):
        return 0x4 | 0x40

    def nvmlDeviceGetEncoderUtilization(self, h):
        return [3, 100000]

    def nvmlDeviceGetDecoderUtilization(self, h):
        return [0, 100000]


class FakeCollector:
    def __init__(self, h=None, nvml=True):
        self.h = h or tf.host()
        self.psutil = None
        self.nvml = FakeNvml() if nvml else None

    def sample(self, watch=None):
        return self.h

    def _nvml_handle(self):
        return self.nvml


class Clock:
    """A clock the sampler's wait moves on; a little real sleep so the threads interleave."""

    def __init__(self, t=tf.T0):
        self.t = t
        self.lock = threading.Lock()

    def __call__(self):
        return self.t

    def wait(self, d):
        time.sleep(0.02)
        with self.lock:
            self.t += max(0.0, d)
        return False


def agent_on(root=None, *, engines=None, clock=None, h=None, addrs=None, state=None, buf=None,
             secret=None, address="192.0.2.40", nvml=True, virtual=None):
    root = root or machine()
    state = state or tempfile.mkdtemp(prefix="node-state-")
    if secret is not None:
        os.makedirs(state, mode=0o700, exist_ok=True)
        fd = os.open(os.path.join(state, "secret"), os.O_WRONLY | os.O_CREAT, 0o600)
        os.write(fd, (secret + "\n").encode())
        os.close(fd)
    col = FakeCollector(h, nvml=nvml)
    clock = clock or time.time
    local = nodesource.LocalNode(hosts=lambda: col.sample([]), root=root,
                                 run=lambda argv: tf.SMI if argv[0] == "nvidia-smi" else None,
                                 address=lambda: address, version=lambda: "0.15.0-node",
                                 clock=clock)
    logs = os.path.join(state, "logs")
    eng = nodeengines.Engines(engines if engines is not None else [], clock=clock, poll_s=0.05,
                              log=lambda m: (buf.append(m) if buf is not None else None))
    a = nodeagent.Agent(state, root=root, collector=col, local=local, engines=eng, clock=clock,
                        sample_wait=getattr(clock, "wait", None),
                        addrs=addrs or (lambda: {}), ping=lambda argv: True, logbuf=buf,
                        log=lambda m: (buf.append(m) if buf is not None else None),
                        virtual=virtual)
    a.engine_logs = logs
    return a


class Running:
    def __init__(self, agent, hosts=("127.0.0.1",)):
        self.agent = agent
        self.servers = nodeagent.listen(agent, list(hosts), 0)
        for s in self.servers:
            threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.05},
                             daemon=True).start()
        self.port = self.servers[0].server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.client = nodeclient.Client(self.url, secret=agent.secret)

    def close(self):
        self.agent.sampler.close()
        for s in self.servers:
            s.shutdown()
            s.server_close()
        # SRV-732: an engine this agent started (a fake engine in the foreground) ends with it
        for e in self.agent.engines.list:
            if e.proc is not None and e.proc.poll() is None:
                nodeengines._end_group(e.proc, 2.0)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def raw(port, method, path, headers=None, body=None, tls=None):
    c = http.client.HTTPSConnection("127.0.0.1", port, context=tls, timeout=5) if tls else \
        http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        return r.status, r.getheader("Content-Type"), r.read()
    finally:
        c.close()


def engine_doc(eid, port, **kw):
    return nodeengines.Engine(dict({"id": eid, "port": port}, **kw),
                              tempfile.mkdtemp(prefix="node-elogs-"))


# ---------------------------------------------------------------------------- a fake engine

class SetupEngine:
    """An engine's Setup Guide and dashboard routes as the agent relays them; it records what
    reached it."""

    def __init__(self, weights_dir=None):
        self.seen = []
        self.weights_dir = weights_dir
        self.status_port = None
        eng = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _send(self, code, doc, ctype="application/json", extra=()):
                raw_ = doc if isinstance(doc, bytes) else json.dumps(doc).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(raw_)))
                for k, v in extra:
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(raw_)

            def _sse(self, events, hold=0.0):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                for ev, data in events:
                    self.wfile.write(f"event: {ev}\ndata: {json.dumps(data)}\n\n".encode())
                    self.wfile.flush()
                    time.sleep(0.05)
                t0 = time.time()
                try:
                    while time.time() - t0 < hold:
                        time.sleep(0.05)
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                except OSError:
                    pass

            def do_GET(self):
                u = urllib.parse.urlsplit(self.path)
                q = dict(urllib.parse.parse_qsl(u.query))
                eng.seen.append(("GET", self.path, dict(self.headers)))
                if u.path == "/health":
                    return self._send(200, {"status": "ok", "version": "0.15.0-fake",
                                            "model": "TandemLLM", "uptime_s": 12.5,
                                            "status_port": eng.status_port,
                                            "weights": {"name": "pack-4.0"}})
                if u.path == "/v1/setup/device":
                    return self._send(200, b'{"mode": "serve",  "gpu": "GB10"}',
                                      extra=(("Cache-Control", "no-store"),))
                if u.path == "/v1/setup/jobs/j1":
                    return self._sse([("job", {"step": i}) for i in range(3)])
                if u.path == "/v1/setup/jobs/slow":
                    # a job between two steps: quiet for longer than any read timeout of 1 s
                    self._sse([("job", {"step": 0, "after": self.headers.get("Last-Event-ID")})])
                    time.sleep(1.6)
                    try:
                        self.wfile.write(b'event: job\ndata: {"step": 1}\n\n')
                        self.wfile.flush()
                    except OSError:
                        pass
                    return
                if u.path == "/v1/setup/weights":
                    return self._send(200, {"contract_version": "1.0", "active": "p-run",
                                            "free_gb": 900.0, "assets": [],
                                            "weights": [{"profile": "p-old", "gb": 16.9,
                                                         "dir": eng.weights_dir,
                                                         "active": False, "removable": True},
                                                        {"profile": "p-run", "gb": 23.3,
                                                         "dir": "/nowhere", "active": True,
                                                         "removable": False}]})
                if u.path == "/v1/dashboard/live":
                    if q.get("follow") == "0":
                        return self._send(200, {"contract_version": "1.1", "history": []})
                    return self._sse([("live", {"seq": 1}), ("live", {"seq": 2})], hold=0.3)
                if u.path == "/v1/dashboard/logs":
                    lines = [{"seq": 1, "ts": "2027-01-15T08:00:01.000Z", "level": "info",
                              "source": "req", "msg": "[req] done 120 tok"},
                             {"seq": 2, "ts": "2027-01-15T08:00:03.000Z", "level": "warning",
                              "source": "cache", "msg": "[cache] put declined"}]
                    rank = {"debug": 0, "info": 1, "warning": 2, "error": 3}
                    lines = [x for x in lines if rank[x["level"]] >= rank[q.get("level", "info")]
                             and q.get("grep", "") in x["msg"]]
                    if q.get("follow") == "0":
                        return self._send(200, {"lines": lines, "last_seq": 2})
                    later = {"seq": 3, "ts": "2027-01-15T08:00:05.000Z", "level": "error",
                             "source": "req", "msg": "[req] !! a later engine line"}
                    return self._sse([("log", later)], hold=1.0)
                return self._send(404, {"error": {"type": "not_found"}})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n)
                eng.seen.append(("POST", self.path, dict(self.headers), body))
                if self.path.startswith("/v1/setup/estimate"):
                    return self._send(200, {"got": json.loads(body)})
                if self.path == "/v1/setup/remove":
                    return self._send(200, {"removed": json.loads(body)["profiles"],
                                            "echo": json.loads(body)})
                if self.path == "/v1/setup/apply":
                    return self._send(202, {"contract_version": "1.0",
                                            "job": {"id": "j1", "state": "running"}})
                if self.path in ("/v1/setup/assistant/load", "/v1/setup/assistant/unload"):
                    return self._send(200, {"state": "loading" if self.path.endswith("load")
                                            and not self.path.endswith("unload") else
                                            "not_loaded"})
                if self.path == "/v1/setup/assistant/chat":
                    return self._sse([("message", {"type": "delta", "text": "Hi"}),
                                      ("message", {"type": "done"})])
                return self._send(404, {"error": {"type": "not_found"}})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def fake_engine_main(port: int) -> None:
    """`--fake-engine PORT`: an engine process in the foreground answering /health; it ends with
    the test that started it (tests/owned.py), whatever the test did."""
    import owned
    owned.watch()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            raw_ = json.dumps({"status": "ok", "version": "0.15.0-proc"}).encode()
            self.send_response(200 if self.path == "/health" else 404)
            self.send_header("Content-Length", str(len(raw_)))
            self.end_headers()
            self.wfile.write(raw_)
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()


# ---------------------------------------------------------------------------- the tests

def test_identity_inventory():
    with Running(agent_on(addrs=lambda: {"fab0": ["10.77.0.1/24"], "lan0": ["192.0.2.40/24"]})) \
            as r:
        st, ident = r.client.json("GET", "/node/identity")
        assert st == 200, ident
        want = nodesource.LocalNode(hosts=lambda: None, root=r.agent.root).identity()["id"]
        assert ident["id"] == want and tf.SERIAL not in ident["id"]
        assert (ident["serial"], ident["hw"], ident["dmi"], ident["address"]) == \
            (tf.SERIAL, "NVIDIA DGX Spark", "Test Vendor TX10", "192.0.2.40"), ident
        assert ident["os"] == "DGX OS 7.5.0 (Ubuntu 24.04 LTS)" and ident["driver"] == "580.1.2"
        assert ident["cuda"] == "13.0" and ident["tandemllm"]
        assert ident["agent"]["listen"] == [{"address": "127.0.0.1", "port": r.port,
                                             "mode": "secret"}]
        st, inv = r.client.json("GET", "/node/inventory")
        assert st == 200
        assert inv["fleet"]["cx7"] == "2 ConnectX ports · 1 up · FW 28.45.1020", inv["fleet"]
        assert inv["lan"] == {"iface": "lan0", "speed": "10 GbE", "mtu": 1500, "state": "up"}
        assert [d["name"] for d in inv["disks"]] == ["nvme0n1"]           # loop devices left out
        assert inv["disks"][0] == {"name": "nvme0n1", "nvme": True, "model": "TEST NVME 4TB",
                                   "size_gb": 4000.8, "rotational": False}
        nics = {n["iface"]: n for n in inv["nics"]}
        assert nics["fab0"]["firmware"] == "28.45.1020" and nics["fab0"]["port"] == "p0"
        assert nics["fab0"]["roce"] == {"device": "mlx5_0", "port": "1", "state": "active",
                                        "link_layer": "Ethernet"}
        assert nics["fab0"]["addrs"] == ["10.77.0.1/24"] and nics["fab0"]["kind"] == "fabric"
        assert nics["fab1"]["state"] == "down" and "roce" not in nics["fab1"]
        assert nics["lan0"]["kind"] == "lan" and "firmware" not in nics["lan0"]
        assert inv["cpu"] == {"count": os.cpu_count(), "performance": 2, "efficiency": 2}
        assert inv["memory"]["unified"] is True


def test_metrics_cadence():
    # the sampler alone: whole seconds, a slow read skips ahead, one thread for two listeners
    clock = Clock(tf.T0 + 0.3)
    reads = []

    def read(t):
        reads.append(t)
        if len(reads) == 3:
            clock.t += 2.5                     # this read took 2.5 s
        return {"t": t}
    s = nodemetrics.Sampler(read, clock=clock, wait=clock.wait)
    a, b = s.subscribe(), s.subscribe()
    got = []
    while len(got) < 6:
        got += [x["t"] for x in a.take(1.0)]
    s.unsubscribe(a)
    assert [x["t"] for x in b.take(0.1)][:6] == got[:6]
    assert got[:3] == [tf.T0 + 1, tf.T0 + 2, tf.T0 + 3], got
    assert got[3] == tf.T0 + 6, got             # no burst for the seconds the slow read took
    assert all(x == int(x) for x in got)
    assert all(y - x == 1 for x, y in zip(got[3:], got[4:6]))
    s.unsubscribe(b)
    n = len(reads)
    time.sleep(0.2)
    assert len(reads) <= n + 1 and not s.subs   # nobody listens: the sampler stops
    # over HTTP, the agent's own clock
    clock = Clock(tf.T0 + 0.5)
    with Running(agent_on(clock=clock)) as r:
        seen = []
        for ev, data in r.client.events("/node/metrics"):
            assert ev == "sample"
            seen.append(data)
            if len(seen) == 4:
                break
        ts = [x["t"] for x in seen]
        assert ts == [ts[0] + i for i in range(4)] and ts[0] == int(ts[0]), ts
        for x in seen:
            assert set(x) >= {"t", "node", "gpus", "memory", "cpu", "disks", "net", "board"}, x
            assert x["node"]["gpu"] == 50.0 and x["node"]["power"] == 60.0
            assert {"weights", "kv", "cache", "other", "page", "total", "available",
                    "used"} <= set(x["memory"])
        # the fabric counters moved: fab0's rate, its RDMA port's (4-byte words)
        tf.set_fabric(r.agent.root, 10_000 + 125_000_000)
        tf._write(r.agent.root, "sys/class/infiniband/mlx5_0/ports/1/counters/port_rcv_data",
                  str(1000 + 31_250_000) + "\n")
        for ev, data in r.client.events("/node/metrics"):
            fab = {f["iface"]: f for f in data["net"]["fabric"]}
            if fab["fab0"].get("rx_gbps"):
                break
        assert fab["fab0"]["rx_gbps"] == 1.0 and fab["fab0"]["rdma"]["rx_gbps"] == 1.0, fab
        assert fab["fab0"]["mtu"] == 9000 and fab["fab1"]["state"] == "down"
        assert "rx_gbps" not in fab["fab1"]                     # down, never counted: absent
        st, once = r.client.json("GET", "/node/metrics?once=1")
        assert st == 200 and once["t"] == int(once["t"])
        time.sleep(0.2)
        assert not r.agent.sampler.subs                         # the streams closed: none left


def test_absent_fields():
    root = machine()
    with Running(agent_on(root)) as r:
        s = r.agent._sample(tf.T0)
        s = r.agent._sample(tf.T0 + 1)
        assert "fans" not in s["board"], s["board"]             # no fan sensor: no fan field
        assert s["board"]["zones"] == [{"zone": "thermal_zone0", "type": "acpitz",
                                        "temp_c": 40.4},
                                       {"zone": "thermal_zone1", "type": "acpitz",
                                        "temp_c": 37.9}]
        assert s["disks"]["nvme"] == [{"name": "nvme0", "temp_c": 41.85}]
        g = s["gpus"][0]
        assert g["throttle"] == ["sw_power_cap", "hw_thermal"] and g["throttled"] is True
        assert g["enc"] == 3.0
        # the driver answers no reason: a reading (throttled false), not an absence
        r.agent.collector.nvml.nvmlDeviceGetCurrentClocksEventReasons = lambda h: 0x1
        g = r.agent._sample(tf.T0 + 1.5)["gpus"][0]
        assert g["throttled"] is False and g["throttle"] == ["gpu_idle"]
        r.agent.collector.nvml.nvmlDeviceGetCurrentClocksEventReasons = lambda h: 0
        g = r.agent._sample(tf.T0 + 1.7)["gpus"][0]
        assert g["throttled"] is False and "throttle" not in g
        assert "power_cap_w" not in g and "mem_mhz" not in g    # GB10: no figure, no field
        assert "memclk" not in s["node"] and "rtt" not in s["node"]
        cores = s["cpu"]["cores"]
        assert [c["kind"] for c in cores] == ["performance"] * 2 + ["efficiency"] * 2
        assert cores[0]["mhz"] == 2808 and "util" not in cores[0]   # one /proc/stat: no rate
        assert s["memory"]["unified"] is True and s["memory"]["page"] == 4.0
        assert s["board"]["uptime_s"] == 4911.2
        assert [f["path"] for f in s["disks"]["fs"]] == [root]
        # no /proc/meminfo: no page cache figure, never a 0
        os.remove(os.path.join(root, "proc/meminfo"))
        assert "page" not in r.agent._sample(tf.T0 + 2)["memory"]
    # a board with a fan sensor and an NVML without the reasons
    root = machine()
    tf._write(root, "sys/class/hwmon/hwmon3/name", "pwmfan\n")
    tf._write(root, "sys/class/hwmon/hwmon3/fan1_input", "2150\n")
    with Running(agent_on(root, nvml=False)) as r:
        s = r.agent._sample(tf.T0)
        assert s["board"]["fans"] == [{"name": "pwmfan/fan1", "rpm": 2150}]
        assert not {"throttle", "throttled", "enc"} & set(s["gpus"][0])
    # a GB10's cores: capacities differ inside each cluster; the core design decides, else the
    # midpoint of the capacities
    root = machine()
    caps = (718, 718, 997, 1024, 731, 1017)
    for c, cap in enumerate(caps):
        tf._write(root, f"sys/devices/system/cpu/cpu{c}/cpu_capacity", f"{cap}\n")
    kinds = ["efficiency", "efficiency", "performance", "performance", "efficiency",
             "performance"]
    r = nodemetrics.Reader(None, None, root=root)
    assert [r._capacities()[c] for c in range(6)] == kinds
    tf._write(root, "proc/cpuinfo", "".join(
        f"processor\t: {c}\nCPU part\t: {'0xd85' if k == 'performance' else '0xd87'}\n\n"
        for c, k in enumerate(kinds)))
    r = nodemetrics.Reader(None, None, root=root)
    assert [r._capacities()[c] for c in range(6)] == kinds
    # the prune rule itself
    assert nodemetrics.prune({"a": None, "b": {"c": None}, "d": [None, {"e": None}, 0],
                              "f": False}) == {"d": [0], "f": False}


def _selfsigned(cn: str, d: str) -> tuple[str, str]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    k = ec.generate_private_key(ec.SECP256R1())
    n = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = datetime.datetime.now(datetime.timezone.utc)
    c = (x509.CertificateBuilder().subject_name(n).issuer_name(n).public_key(k.public_key())
         .serial_number(x509.random_serial_number())
         .not_valid_before(now - datetime.timedelta(days=1))
         .not_valid_after(now + datetime.timedelta(days=1))
         .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
         .sign(k, hashes.SHA256()))
    cp, kp = os.path.join(d, f"{cn}.crt"), os.path.join(d, f"{cn}.key")
    with open(cp, "wb") as f:
        f.write(c.public_bytes(serialization.Encoding.PEM))
    fd = os.open(kp, os.O_WRONLY | os.O_CREAT, 0o600)
    os.write(fd, k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                 serialization.NoEncryption()))
    os.close(fd)
    return cp, kp


def paired_state() -> tuple[str, dict]:
    """A state dir whose trust store holds the node's certificate and a pinned controller's;
    the certificates: node, ctl (pinned), stranger (not)."""
    d = tempfile.mkdtemp(prefix="node-pki-")
    state = os.path.join(d, "state")
    trust = os.path.join(state, "trust")
    os.makedirs(os.path.join(trust, "peers"))
    os.chmod(state, 0o700)
    certs = {n: _selfsigned(n, d) for n in ("node", "ctl", "stranger")}
    os.replace(certs["node"][0], os.path.join(trust, "node.crt"))
    os.replace(certs["node"][1], os.path.join(trust, "node.key"))
    with open(certs["ctl"][0]) as f, open(os.path.join(trust, "peers", "ctl.pem"), "w") as g:
        g.write(f.read())
    certs["node"] = (os.path.join(trust, "node.crt"), os.path.join(trust, "node.key"))
    return state, certs


def test_auth_refusal():
    buf = []
    with Running(agent_on(buf=buf)) as r:
        for h in ({}, {"Authorization": "Bearer not-the-secret-0123456789"},
                  {"Authorization": "Basic " + r.agent.secret}):
            for path in ("/node/identity", "/node/metrics", "/node/engines", "/node/logs",
                         "/node/setup/device", "/node/linktest/echo"):
                st, _, body = raw(r.port, "GET", path, h)
                assert st == 401 and json.loads(body)["error"]["type"] == "unauthorized", path
        st, _, _ = raw(r.port, "POST", "/node/engines/x/start", {})
        assert st == 401
        assert any("refused GET /node/identity" in m and "no secret" in m for m in buf), buf
        assert any("a wrong secret" in m for m in buf)
        assert r.agent.secret not in "".join(buf)
        st, _, _ = raw(r.port, "GET", "/node/identity",
                       {"Authorization": f"Bearer {r.agent.secret}"})
        assert st == 200
        mode = os.stat(r.agent.secret_path).st_mode & 0o777
        assert mode == 0o600, oct(mode)
    # a secret file others can read is refused, at the agent's start and for the controller
    d = tempfile.mkdtemp(prefix="node-state-")
    p = os.path.join(d, "secret")
    with open(p, "w") as f:
        f.write("x" * 40 + "\n")
    os.chmod(p, 0o644)
    for fn in (lambda: nodetrust.load_secret(p), lambda: agent_on(state=d)):
        try:
            fn()
            raise AssertionError("a 0644 secret was taken")
        except nodetrust.TrustError as exc:
            assert "0644" in str(exc)
    # mTLS: no certificate, an unpinned one, the pinned one
    state, certs = paired_state()
    buf = []
    a = agent_on(state=state, buf=buf)
    assert nodetrust.listen_mode("127.0.0.1", a.store) == "mtls"
    with Running(a) as r:
        assert r.servers[0].mode == "mtls"
        pin = open(certs["node"][0]).read()
        for who in (None, "stranger"):
            ctx = nodetrust.client_context(*(certs[who] if who else (None, None)), pin)
            try:
                st, _, _ = raw(r.port, "GET", "/node/identity", tls=ctx)
            except (ssl.SSLError, ConnectionResetError, BrokenPipeError, OSError):
                st = None
            assert st is None, (who, st)
        # a plain HTTP request with the secret on the mTLS port: no answer either
        try:
            st, _, _ = raw(r.port, "GET", "/node/identity",
                           {"Authorization": f"Bearer {r.agent.secret}"})
        except (http.client.HTTPException, OSError):
            st = None
        assert st is None
        ok = nodetrust.client_context(*certs["ctl"], pin)
        st, _, body = raw(r.port, "GET", "/node/identity", tls=ok)
        assert st == 200 and json.loads(body)["agent"]["listen"][0]["mode"] == "mtls"
        # the controller's side: an AgentNode over the pinned mTLS
        an = nodesource.agent_for({"endpoint": f"https://127.0.0.1:{r.port}", "trust": {
            "kind": "mtls", "cert": certs["ctl"][0], "key": certs["ctl"][1], "pin": pin}})
        assert isinstance(an, nodesource.AgentNode) and an.identity()["id"] == \
            json.loads(body)["id"]
        # a node certificate other than the pinned one: the controller refuses it
        other = nodesource.agent_for({"endpoint": f"https://127.0.0.1:{r.port}", "trust": {
            "kind": "mtls", "cert": certs["ctl"][0], "key": certs["ctl"][1],
            "pin": open(certs["stranger"][0]).read()}})
        assert other.identity() is None and other.status()[0] == "off"
        other.close()
        an.close()
    assert any("refused TLS" in m for m in buf), buf


def test_bind_rule():
    empty = nodetrust.Store(tempfile.mkdtemp(prefix="node-trust-"))
    state, _ = paired_state()
    full = nodetrust.Store(os.path.join(state, "trust"))
    assert full.ready() and not empty.ready()
    for host, store, want in (("127.0.0.1", empty, "secret"), ("localhost", empty, "secret"),
                              ("::1", empty, "secret"), ("127.0.0.1", full, "mtls"),
                              ("192.168.1.20", full, "mtls"), ("10.77.0.1", full, "mtls"),
                              ("169.254.3.1", full, "mtls"), ("fd00::1", full, "mtls")):
        assert nodetrust.listen_mode(host, store) == want, (host, want)
    for host, store, why in (("0.0.0.0", full, "unspecified"), ("::", full, "unspecified"),
                             ("8.8.8.8", full, "WAN"), ("2001:4860::8888", full, "WAN"),
                             ("192.168.1.20", empty, "needs mTLS"),
                             ("10.77.0.1", empty, "needs mTLS"),
                             ("spark.local", full, "not an IP")):
        try:
            nodetrust.listen_mode(host, store)
            raise AssertionError(f"{host} was taken")
        except nodetrust.BindRefused as exc:
            assert why in str(exc), (host, str(exc))
    # the command refuses before anything listens
    for argv in (["check", "--host", "0.0.0.0"], ["serve", "--host", "192.168.1.20"],
                 ["--host", "8.8.8.8"]):
        try:
            nodeagent.main(argv + ["--state-dir", tempfile.mkdtemp(), "--port", "0"])
            raise AssertionError(argv)
        except SystemExit as exc:
            assert "not listening" in str(exc), exc
    # listen() too, for every address before any of them is bound
    a = agent_on()
    try:
        nodeagent.listen(a, ["127.0.0.1", "192.168.1.20"], 0)
        raise AssertionError("bound beyond loopback without mTLS")
    except nodetrust.BindRefused:
        assert a.listening == {}
    unit = nodeagent.unit_text(nodeagent.parser().parse_args(["--state-dir", "/x/node"]))
    assert "--host 127.0.0.1 --port 7700 --state-dir /x/node" in unit
    assert "Restart=on-failure" in unit and "WantedBy=default.target" in unit


def test_engines_on_a_fake_engine():
    me = os.path.abspath(__file__)
    fg, dm, un = free_port(), free_port(), free_port()
    pidfile = os.path.join(tempfile.mkdtemp(), "engine.pid")
    for p in (fg, dm, un):
        assert p != 8000
    engines = [engine_doc("fg", fg, start=[sys.executable, me, "--fake-engine", str(fg)],
                          start_timeout_s=20),
               engine_doc("dm", dm, start=[sys.executable, me, "--fake-daemon", str(dm), pidfile],
                          stop=[sys.executable, me, "--fake-stop", pidfile], start_timeout_s=20),
               engine_doc("theirs", un)]
    buf = []
    with Running(agent_on(engines=engines, buf=buf)) as r:
        c = r.client

        def state(eid):
            return c.json("GET", f"/node/engines/{eid}")[1]

        def wait_job(eid):
            for _ in range(400):
                s = state(eid)
                if s["job"] and s["job"]["state"] != "running":
                    return s
                time.sleep(0.05)
            raise AssertionError(state(eid))
        st, doc = c.json("GET", "/node/engines")
        assert st == 200 and [e["id"] for e in doc["engines"]] == ["fg", "dm", "theirs"]
        assert [(e["managed"], e["state"]) for e in doc["engines"]] == \
            [(True, "down"), (True, "down"), (False, "down")]
        # not managed: never started, stopped or restarted here
        for act in ("start", "stop", "restart"):
            st, err = c.json("POST", f"/node/engines/theirs/{act}")
            assert st == 409 and err["error"]["type"] == "not_managed", err
        assert c.json("POST", "/node/engines/nope/start")[0] == 404
        # the foreground engine: start, busy while it starts, up, restart, stop
        st, job = c.json("POST", "/node/engines/fg/start")
        assert st == 202 and job["state"] == "running" and job["who"] == "secret"
        st, err = c.json("POST", "/node/engines/fg/stop")
        assert st == 409 and err["error"]["type"] == "busy"
        s = wait_job("fg")
        assert s["job"]["state"] == "done" and s["state"] == "up" and s["pid"], s
        assert s["health"]["version"] == "0.15.0-proc"
        first = s["pid"]
        assert c.json("POST", "/node/engines/fg/restart")[0] == 202
        s = wait_job("fg")
        assert s["state"] == "up" and s["pid"] and s["pid"] != first, s
        assert c.json("POST", "/node/engines/fg/stop")[0] == 202
        s = wait_job("fg")
        assert s["job"]["message"] == "down" and s["state"] == "down" and s["pid"] is None, s
        # the daemonizing one: its start command returns, its stop command ends it
        assert c.json("POST", "/node/engines/dm/start")[0] == 202
        s = wait_job("dm")
        assert s["state"] == "up" and s["pid"] is None, s       # not ours: the command exited
        assert c.json("POST", "/node/engines/dm/stop")[0] == 202
        s = wait_job("dm")
        assert s["job"]["state"] == "done" and s["state"] == "down", s
        assert any("start fg (port" in m and "by secret" in m for m in buf), buf
        # a start command that fails
        r.agent.engines.by_id["fg"].start_cmd = [sys.executable, "-c", "raise SystemExit(3)"]
        c.json("POST", "/node/engines/fg/start")
        s = wait_job("fg")
        assert s["job"]["state"] == "failed" and "exited 3" in s["job"]["message"], s
    # the relay: an allowed route as the engine answers, the live stream as it comes, the rest 404
    eng = SetupEngine()
    with Running(agent_on(engines=[engine_doc("engine", eng.port)])) as r:
        st, ctype, body = raw(r.port, "GET", "/node/engines/engine/health",
                              {"Authorization": f"Bearer {r.agent.secret}"})
        assert st == 200 and json.loads(body)["version"] == "0.15.0-fake"
        assert "Authorization" not in eng.seen[-1][2]               # the secret stays here
        got = list(r.client.events("/node/engines/engine/v1/dashboard/live"))
        assert got == [("live", {"seq": 1}), ("live", {"seq": 2})], got
        st, _, _ = raw(r.port, "GET", "/node/engines/engine/v1/setup/device",
                       {"Authorization": f"Bearer {r.agent.secret}"})
        assert st == 404
        st, doc = r.client.json("GET", "/node/engines")
        e = doc["engines"][0]
        assert (e["state"], e["managed"], e["health"]["weights"]) == ("up", False, "pack-4.0")
    eng.close()


def test_logs_filter():
    eng = SetupEngine()
    buf = logbuf.LogBuffer()
    for line in ("[node] TandemNode on 127.0.0.1:7700", "[node] refused GET /node/x: no secret",
                 "[req] !! failed in the agent"):
        buf.append(line)
    with Running(agent_on(engines=[engine_doc("engine", eng.port)], buf=buf)) as r:
        c = r.client
        st, doc = c.json("GET", "/node/logs?follow=0&level=debug")
        assert st == 200
        srcs = {(x["source"], x["msg"]) for x in doc["lines"]}
        assert ("engine", "[req] done 120 tok") in srcs and ("agent", "[node] TandemNode on "
                                                             "127.0.0.1:7700") in srcs
        assert [x["t"] for x in doc["lines"]] == sorted(x["t"] for x in doc["lines"])
        st, doc = c.json("GET", "/node/logs?follow=0&level=warn")
        assert {x["msg"] for x in doc["lines"]} == {"[cache] put declined",
                                                    "[req] !! failed in the agent"}
        assert all(x["level"] in ("warn", "error") for x in doc["lines"])
        st, doc = c.json("GET", "/node/logs?follow=0&q=refused")
        assert [x["msg"] for x in doc["lines"]] == ["[node] refused GET /node/x: no secret"]
        st, doc = c.json("GET", "/node/logs?follow=0&source=engine&level=debug")
        assert {x["source"] for x in doc["lines"]} == {"engine"}
        st, doc = c.json("GET", "/node/logs?follow=0&source=agent&level=debug")
        assert {x["source"] for x in doc["lines"]} == {"agent"}
        for bad in ("level=loud", "source=nope", "backlog=9999"):
            assert c.json("GET", f"/node/logs?follow=0&{bad}")[0] == 400, bad
        # the stream: the backlog, then a new line of the agent's and the engine's
        got = []

        def later():
            time.sleep(0.3)
            buf.append("[node] a later agent line")
        threading.Thread(target=later, daemon=True).start()
        for ev, data in c.events("/node/logs?level=info&backlog=10", timeout=5):
            assert ev == "log"
            got.append((data["source"], data["msg"]))
            if ("agent", "[node] a later agent line") in got and \
                    ("engine", "[req] !! a later engine line") in got:
                break
        assert ("engine", "[req] done 120 tok") in got                 # the backlog came first
    eng.close()


def test_linktest_fake_peer():
    secret = "linktest-shared-secret-0123456789abcdef"
    a = agent_on(machine(), secret=secret, addrs=lambda: {"fab0": ["127.0.0.1/8"],
                                                          "lan0": ["192.0.2.40/24"]})
    b = agent_on(machine("PEER-SERIAL-2"), secret=secret,
                 addrs=lambda: {"fab0": ["127.0.0.1/8"], "fab1": ["10.9.9.2/24"],
                                "lan0": ["198.51.100.7/24"]})
    with Running(a) as ra, Running(b) as rb:
        st, res = ra.client.json("POST", "/node/linktest", {"peer": rb.url, "bytes": 4 << 20,
                                                            "streams": 2, "rtt_n": 5},
                                 timeout=60)
        assert st == 200, res
        assert res["peer_id"] == b.local.identity()["id"] != a.local.identity()["id"]
        assert res["rtt"]["n"] == 5 and 0 < res["rtt"]["min_ms"] <= res["rtt"]["median_ms"] \
            <= res["rtt"]["p90_ms"]
        assert res["rtt"]["median_ms"] < 20, res["rtt"]     # no 40 ms Nagle stall on keep-alive
        assert res["channel"]["gbps"] > 0 and res["channel"]["bytes"] == 4 << 20
        rows = {x["iface"]: x for x in res["interfaces"]}
        f0 = rows["fab0"]
        assert f0["result"] == "measured" and f0["gbps"] > 0 and f0["streams"] == 2, f0
        assert (f0["mtu"], f0["peer_mtu"], f0["mtu_match"], f0["path_mtu_ok"]) == \
            (9000, 9000, True, True)
        assert f0["roce"]["state"] == "active" and f0["port"] == "p0"
        assert rows["fab1"]["result"] == "no IPv4 address" and "gbps" not in rows["fab1"]
        assert rows["lan0"]["result"] == "no peer address in this interface's subnet"
        assert res["ports"] == [{"port": "p0", "interfaces": ["fab0", "fab1"], "carrying": 1,
                                 "total": 2, "half_bandwidth": True}]
        assert res["half_bandwidth"] is True and any("half its rate" in n for n in res["notes"])
        # to itself: its own addresses are said to be this node's
        st, res = ra.client.json("POST", "/node/linktest", {"peer": ra.url, "bytes": 1 << 20,
                                                            "streams": 1, "rtt_n": 2})
        assert {x["iface"]: x["result"] for x in res["interfaces"]}["lan0"] == \
            "the peer has this address: it is this node", res["interfaces"]
        # both interfaces of the port carrying: no flag
        a.addrs = lambda: {"fab0": ["127.0.0.1/8"], "fab1": ["127.0.0.1/8"]}
        tf._write(a.root, "sys/class/net/fab1/operstate", "up\n")
        st, res = ra.client.json("POST", "/node/linktest", {"peer": rb.url, "bytes": 2 << 20,
                                                            "streams": 1, "rtt_n": 2},
                                 timeout=60)
        assert res["ports"][0]["carrying"] == 2 and res["half_bandwidth"] is False, res["ports"]
        # a peer off this box over plain HTTP is refused; a peer that does not answer is said
        st, err = ra.client.json("POST", "/node/linktest", {"peer": "http://10.0.0.9:7700"})
        assert st == 400 and "mTLS" in err["error"]["message"]
        st, res = ra.client.json("POST", "/node/linktest",
                                 {"peer": f"http://127.0.0.1:{free_port()}", "rtt_n": 1})
        assert st == 200 and "error" in res, res
        st, err = ra.client.json("POST", "/node/linktest", {"peer": rb.url, "streams": 99})
        assert st == 400


def test_setup_delegation():
    wdir = tempfile.mkdtemp(prefix="node-weights-")
    with open(os.path.join(wdir, "_hub_manifest.json"), "w") as f:
        json.dump({"files": {"model-00001.safetensors": {"size": 10, "sha256": "ab" * 32},
                             "config.json": {"size": 2, "sha256": None}}}, f)
    eng = SetupEngine(weights_dir=wdir)
    with Running(agent_on(engines=[engine_doc("engine", eng.port)])) as r:
        auth = {"Authorization": f"Bearer {r.agent.secret}"}
        # a GET: the engine's status, type and bytes as they are
        direct = raw(eng.port, "GET", "/v1/setup/device")
        via = raw(r.port, "GET", "/node/setup/device", auth)
        assert via == direct and via[2] == b'{"mode": "serve",  "gpu": "GB10"}', via
        # a POST: the body and its type as sent, no secret and no origin passed on
        body = json.dumps({"selection": {"model": "m", "profile": "p"}}).encode()
        st, _, out = raw(r.port, "POST", "/node/setup/estimate?x=1",
                         dict(auth, **{"Content-Type": "application/json",
                                       "Origin": "http://evil.example"}), body)
        assert st == 200 and json.loads(out) == {"got": json.loads(body)}
        m, path, headers, got = eng.seen[-1]
        assert (m, path, got) == ("POST", "/v1/setup/estimate?x=1", body)
        assert headers.get("Content-Type") == "application/json"
        assert "Authorization" not in headers and "Origin" not in headers
        # an event stream as it comes
        evs = list(r.client.events("/node/setup/jobs/j1"))
        assert evs == [("job", {"step": 0}), ("job", {"step": 1}), ("job", {"step": 2})]
        # weights: the engine's list with content ids; a removal as the Setup Guide takes it
        st, w = r.client.json("GET", "/node/weights")
        assert st == 200 and w["active"] == "p-run"
        old = next(x for x in w["weights"] if x["profile"] == "p-old")
        assert old["content_basis"] == "hub_manifest" and old["content_id"].startswith("sha256:")
        assert len(old["content_id"]) == 7 + 64
        assert next(x for x in w["weights"] if x["profile"] == "p-run")["content_id"] is None
        st, out = r.client.json("DELETE", "/node/weights/p-old?confirm=1")
        assert st == 200 and out["echo"] == {"profiles": ["p-old"], "dry_run": False,
                                             "confirm": True}
        st, out = r.client.json("DELETE", "/node/weights/p-old?dry_run=1")
        assert out["echo"] == {"profiles": ["p-old"], "dry_run": True}
        # SRV-731: the store beside the Setup Guide's weights; a receive names its copy
        assert w["store"]["dir"] == r.agent.weights.root and w["store"]["free_gib"] > 0, w
        st, err = r.client.json("POST", "/node/weights/receive")
        assert st == 400 and err["error"]["type"] == "bad_request", err
        # the engine restarts: its status responder answers the Setup Guide meanwhile
        status = SetupEngine()
        e = r.agent.engines.by_id["engine"]
        e.status_port = status.port
        eng.close()
        st, _, body = raw(r.port, "GET", "/node/setup/device", auth)
        assert st == 200 and status.seen[-1][1] == "/v1/setup/device"
        status.close()
        st, _, body = raw(r.port, "GET", "/node/setup/device", auth)
        assert st == 503 and json.loads(body)["error"]["type"] == "engine_down"
        # POST estimate is relayed as every route is: this agent computes no estimate itself
        st, _, body = raw(r.port, "POST", "/node/setup/estimate",
                          dict(auth, **{"Content-Type": "application/json"}),
                          json.dumps({"selection": {"model": "m"}}).encode())
        assert st == 503 and json.loads(body)["error"]["type"] == "engine_down", body
    with Running(agent_on(engines=[])) as r:
        assert r.client.json("GET", "/node/setup/device")[0] == 503
        st, err = r.client.json("POST", "/node/setup/estimate", {"selection": {"model": "m"}})
        assert st == 503 and err["error"]["type"] == "no_engine", err
    # content ids from a --local-dir download's metadata, and a cache snapshot's blob links
    d = tempfile.mkdtemp()
    tf._write(d, ".cache/huggingface/download/model.safetensors.metadata",
              "0123abcd\n" + "cd" * 32 + "\n1700000000.0\n")
    assert nodeagent.content_id(d)[1] == "hf_metadata"
    snap = os.path.join(tempfile.mkdtemp(), "models--x--y", "snapshots", "abc")
    os.makedirs(snap)
    os.symlink("../../blobs/" + "ef" * 32, os.path.join(snap, "model.safetensors"))
    cid, basis = nodeagent.content_id(snap)
    assert basis == "hf_blobs" and cid == nodeagent.content_id(snap)[0]
    assert nodeagent.content_id(tempfile.mkdtemp()) == (None, None)


def test_agent_node_in_fleet():
    clock = Clock()
    eng = tf.Engine(clock=clock)
    eng.step(tf.T0 - 1, 0)
    root = machine()
    abuf = logbuf.LogBuffer()
    abuf.append("[node] TandemNode up")
    agent = agent_on(root, engines=[engine_doc("engine", eng.port)], clock=clock, buf=abuf)
    peer = agent_on(machine("PEER-SERIAL-3"), clock=clock, address="192.0.2.99")
    with Running(agent) as ra, Running(peer) as rp, Proxy(env=tf.engine_env(eng)) as px:
        px.sync()
        local = tf.local_node(root, logbuf=tf.FakeBuf([{"seq": 1, "level": "info",
                                                        "ts": "2027-01-15T08:00:02.000Z",
                                                        "msg": "[proxy] a gateway line"}]))
        an = nodesource.AgentNode(ra.url, secret=agent.secret, clock=clock,
                                  logbuf=local.logbuf)
        f = tf.fleet_on(px, local, clock=clock)
        f.agent = an
        nodes = tf.ok(tf.get(px, "/v1/fleet/nodes")[1], "nodes")["nodes"]
        nid = nodes[0]["id"]
        assert nid == agent.local.identity()["id"] and f.here() is an
        assert any("read through its TandemNode agent" in m for m in px.logs), px.logs
        n = nodes[0]
        assert n["status"] == "up" and n["inventory"]["cx7"] == \
            "2 ConnectX ports · 1 up · FW 28.45.1020", n
        # the engine's samples through the agent's relay, the hardware from its stream
        for i in range(1, 6):
            eng.step(tf.T0 + i, 40)
        for _ in range(100):
            if len(an._buf) >= 3:
                break
            time.sleep(0.02)
        f.tick()
        assert eng.live_reads >= 1                  # the engine was read, through the relay
        m = tf.ok(tf.get(px, f"/v1/fleet/metrics?metric=gpu&range=5m&source=node:{nid}")[1],
                  "metrics")
        assert 50.0 in m["series"][0]["values"], m["series"]
        m = tf.ok(tf.get(px, "/v1/fleet/metrics?metric=tps&range=5m")[1], "metrics")
        assert any(v == 40.0 for v in m["series"][0]["values"]), m["series"]
        m = tf.ok(tf.get(px, "/v1/fleet/metrics?metric=mem&range=5m")[1], "metrics")
        assert m["cap"] == 121.6
        mem = tf.ok(tf.get(px, "/v1/fleet/memory")[1], "memory")["nodes"]
        assert mem[0]["node"] == nid and mem[0]["weights"] == 41.0 and mem[0]["kv"] == 8.5
        deps = tf.ok(tf.get(px, "/v1/fleet/deployments")[1], "deployments")["deployments"]
        assert [(d["id"], d["members"], d["pack"]) for d in deps] == \
            [("TandemLLM", [nid], "pack-4.0")], deps
        nodes = tf.get(px, "/v1/fleet/nodes")[1]["nodes"]
        assert nodes[0]["version"] == "0.15.0-test"
        # the newest sample as the agent sent it: every block the fake board exposes
        sm = tf.ok(tf.get(px, f"/v1/fleet/nodes/{nid}/sample")[1], "sample")
        assert sm["via"] == "agent" and sm["note"] is None and sm["age_s"] is not None, sm
        assert {"node", "gpus", "memory", "cpu"} <= set(sm["sample"]), sm["sample"].keys()
        assert sm["sample"]["cpu"]["cores"], sm["sample"]["cpu"]
        lines = tf.ok(tf.get(px, "/v1/fleet/logs?level=debug")[1], "logs")["lines"]
        assert {"agent", "engine", "gateway"} <= {x["source"] for x in lines}, lines
        # a remote node's agent (the loopback secret stands in for pairing's mTLS here)
        cfg = px.svc.cfg
        cfg.add_node({"id": "n-remote", "alias": "R-1", "endpoint": rp.url,
                      "address": "192.0.2.99", "trust": {"kind": "loopback-secret",
                                                         "file": peer.secret_path}})
        cfg.add_node({"id": "n-unpaired", "alias": "R-2", "endpoint": "https://10.0.0.9:7700",
                      "address": "10.0.0.9", "trust": {}})
        cfg.add_node({"id": "n-old", "alias": "R-3", "endpoint": "ssh", "address": "10.0.0.8"})
        nodes = {x["alias"]: x for x in tf.ok(tf.get(px, "/v1/fleet/nodes")[1], "nodes")["nodes"]}
        assert nodes["R-1"]["status"] == "up", nodes["R-1"]
        assert nodes["R-1"]["inventory"]["serial"] == "PEER-SERIAL-3"
        assert (nodes["R-2"]["status"], nodes["R-2"]["status_text"]) == \
            ("off", "not paired: no trust for its agent yet (SEC-4)")
        assert nodes["R-3"]["status"] == "off"
        sm = tf.ok(tf.get(px, "/v1/fleet/nodes/n-unpaired/sample")[1], "sample")
        assert (sm["via"], sm["sample"]) == ("none", None) and sm["note"].startswith("not paired")
        for _ in range(100):
            if f.agents["n-remote"][1]._buf:
                break
            time.sleep(0.02)
        f.tick()
        m = tf.ok(tf.get(px, "/v1/fleet/metrics?metric=power&range=5m&source=node:n-remote")[1],
                  "metrics")
        assert 60.0 in m["series"][0]["values"]
        links = tf.ok(tf.get(px, "/v1/fleet/links")[1], "links")["links"]
        rl = next(x for x in links if x["b"] == "n-remote")
        assert rl["a"] == "lan" and rl["rtt_ms"] is not None and rl["speed"] == "10 GbE"
        tf.ok(tf.get(px, "/v1/fleet/topology")[1], "topology")
        mem = tf.ok(tf.get(px, "/v1/fleet/memory")[1], "memory")["nodes"]
        assert {x["node"] for x in mem} == {nid, "n-remote"}
        deps = tf.get(px, "/v1/fleet/deployments")[1]["deployments"]
        assert len(deps) == 1                       # the remote agent has no engine
        # this machine's agent stops: read in-process again, and said so
        ra.close()
        an.close()
        clock.t += 10
        an._ident_at = -1e18
        an._halt.clear()
        assert f.here() is local
        assert any("read in-process" in m for m in px.logs), px.logs
        assert tf.get(px, "/v1/fleet/nodes")[1]["nodes"][0]["status"] == "up"
        for src in [a for _, a in f.agents.values()] + [an]:
            if hasattr(src, "close"):
                src.close()
    eng.close()


def _job(c, path="/node/maintenance"):
    for _ in range(400):
        st, doc = c.json("GET", path)
        job = doc.get("job")
        if job and job["state"] != "running":
            return doc
        time.sleep(0.02)
    raise AssertionError(doc)


def test_machine_actions():
    nodemaint.SIMULATED_S = 0.05
    # nothing set up: both refused with the words to set them up
    with Running(agent_on()) as r:
        st, doc = r.client.json("GET", "/node/maintenance")
        assert st == 200 and doc["virtual"] is False and doc["job"] is None, doc
        for a in ("reboot", "update"):
            assert doc[a]["managed"] is False and doc[a]["simulated"] is False
            assert doc[a]["why"].startswith(f"{a} is not set up on this node: add a"), doc[a]
            st, err = r.client.json("POST", f"/node/maintenance/{a}")
            assert st == 409 and err["error"]["type"] == "not_managed", err
        assert r.client.json("POST", "/node/maintenance/explode")[0] == 404
    # node.json's commands: a job, done or failed; one at a time
    state = tempfile.mkdtemp(prefix="node-state-")
    marker = os.path.join(state, "updated")
    with open(os.path.join(state, "node.json"), "w") as f:
        json.dump({"update": [sys.executable, "-c", f"import time; time.sleep(0.4); "
                                                    f"open({marker!r}, 'w').close()"],
                   "reboot": [sys.executable, "-c", "raise SystemExit(4)"]}, f)
    buf = []
    with Running(agent_on(state=state, buf=buf)) as r:
        st, job = r.client.json("POST", "/node/maintenance/update")
        assert st == 202 and job["state"] == "running" and job["who"] == "secret", job
        assert job["simulated"] is False
        st, err = r.client.json("POST", "/node/maintenance/reboot")
        assert st == 409 and err["error"]["type"] == "busy", err
        doc = _job(r.client)
        assert (doc["job"]["state"], doc["job"]["rc"]) == ("done", 0) and os.path.exists(marker)
        assert r.client.json("POST", "/node/maintenance/reboot")[0] == 202
        doc = _job(r.client)
        assert doc["job"]["state"] == "failed" and "exited 4" in doc["job"]["message"], doc
        assert any("update by secret" in m for m in buf), buf
    # a virtual node: simulated, whatever node.json says; and no engine of the machine's
    vstate = tempfile.mkdtemp(prefix="node-vstate-")
    touched = os.path.join(vstate, "rebooted")
    with open(os.path.join(vstate, "node.json"), "w") as f:
        json.dump({"reboot": ["touch", touched]}, f)
    with Running(agent_on(state=vstate, virtual="v1")) as r:
        st, doc = r.client.json("GET", "/node/maintenance")
        assert doc["virtual"] is True and doc["reboot"] == {"managed": True, "simulated": True,
                                                            "why": None}, doc
        assert doc["update"]["managed"] is True                # simulated, not set up
        st, job = r.client.json("POST", "/node/maintenance/reboot")
        assert st == 202 and job["simulated"] is True
        doc = _job(r.client)
        assert doc["job"]["state"] == "done" and doc["job"]["message"] == \
            "simulated: a virtual node; the host was not rebooted", doc
        assert not os.path.exists(touched)
    tandem = tempfile.mkdtemp(prefix="tandem-dir-")
    os.makedirs(os.path.join(tandem, "run", "ops"))
    with open(os.path.join(tandem, "run", "ops", "serve.env"), "w") as f:
        f.write("PORT=18123\n")
    assert [e.managed for e in nodeengines.Engines.load(tempfile.mkdtemp(), tandem).list] == \
        [True]
    assert nodeengines.Engines.load(tempfile.mkdtemp(), tandem, virtual="v1").list == []


def test_actions_through_the_agent():
    nodemaint.SIMULATED_S = 0.05
    me = os.path.abspath(__file__)
    fg, un = free_port(), free_port()
    assert 8000 not in (fg, un)
    agent = agent_on(engines=[engine_doc("fg", fg, start=[sys.executable, me, "--fake-engine",
                                                            str(fg)], start_timeout_s=20),
                              engine_doc("theirs", un)])
    virt = agent_on(machine("VIRT-SERIAL-1"), virtual="v1", address="127.0.0.1")
    with Running(agent) as ra, Running(virt) as rv, Proxy() as px:
        f = tf.fleet_on(px, tf.local_node(tf.fake_root()))
        cfg = px.svc.cfg
        cfg.add_node({"id": "n-remote", "alias": "R-1", "endpoint": ra.url,
                      "address": "192.0.2.99", "trust": {"kind": "loopback-secret",
                                                         "file": agent.secret_path}})
        cfg.add_node({"id": "n-virt", "alias": "V-1", "endpoint": rv.url,
                      "address": "127.0.0.1", "trust": {"kind": "loopback-secret",
                                                        "file": virt.secret_path}})
        p = tf.ok(tf.get(px, "/v1/fleet/nodes/n-remote/actions/restart-engine")[1],
                  "action-plan")
        assert p["allowed"] and p["confirm"] and [e["id"] for e in p["engines"]] == \
            ["fg", "theirs"], p
        assert p["engines"][0]["managed"] and not p["engines"][1]["managed"]
        assert "R-1 serves nothing through this gateway now" in p["impact"], p
        st, res = tf.send(px, "POST", "/v1/fleet/nodes/n-remote/actions/restart-engine",
                          {"confirm": p["confirm"]})
        assert st == 200 and tf.ok(res, "action-result")["state"] == "running", res
        assert res["job"]["action"] == "restart", res
        assert res["text"] == "on R-1, the engine fg restarts", res
        for _ in range(400):
            e = ra.client.json("GET", "/node/engines/fg")[1]
            if e["job"]["state"] != "running":
                break
            time.sleep(0.05)
        assert (e["job"]["state"], e["state"]) == ("done", "up"), e
        # the engine someone else runs: said so, never touched
        p = tf.get(px, "/v1/fleet/nodes/n-remote/actions/restart-engine?engine=theirs")[1]
        assert not p["allowed"] and p["confirm"] is None
        assert "is not managed by TandemNode" in p["blocked"], p
        st, err = tf.send(px, "POST", "/v1/fleet/nodes/n-remote/actions/restart-engine",
                          {"engine": "theirs"})
        assert st == 409 and tf.ok(err, "error")["error"]["type"] == "not_managed", err
        # a reboot the node's owner did not set up; a virtual node's, simulated
        p = tf.ok(tf.get(px, "/v1/fleet/nodes/n-remote/actions/reboot")[1], "action-plan")
        assert not p["allowed"] and p["blocked"].startswith("reboot is not set up"), p
        p = tf.ok(tf.get(px, "/v1/fleet/nodes/n-virt/actions/reboot")[1], "action-plan")
        assert p["allowed"] and "V-1 is a virtual node: the reboot is simulated, the machine " \
                                "under it is not rebooted" in p["impact"], p
        st, res = tf.send(px, "POST", "/v1/fleet/nodes/n-virt/actions/reboot",
                          {"confirm": p["confirm"]})
        assert st == 200 and res["job"]["simulated"] is True, res
        texts = [e["text"] for e in tf.get(px, "/v1/fleet/events?node=n-virt")[1]["events"]]
        assert "V-1: reboots (simulated)" in texts, texts
        _job(rv.client)
        p = tf.get(px, "/v1/fleet/nodes/n-virt/actions/reboot")[1]
        assert p["job"]["state"] == "done", p
        # a LAN link's test from the controller: kept on the link
        st, res = tf.send(px, "POST", "/v1/fleet/links/lan-n-remote/test", None)
        assert st == 200, res
        tf.ok(res, "link-test")
        t = res["link"]["test"]
        assert t["gbps"] > 0 and t["rtt_ms"] is not None and "a floor" in t["note"], t
        links = tf.ok(tf.get(px, "/v1/fleet/links")[1], "links")["links"]
        assert next(x for x in links if x["id"] == "lan-n-remote")["test"] == t
        st, res = tf.send(px, "POST", "/v1/fleet/links/lan-n-virt/test", None)
        assert st == 200 and "a virtual node" in res["link"]["test"]["note"], res
        for src in [a for _, a in f.agents.values()]:
            src.close()


# ---------------------------------------------------------------------------- the fake engine's
# processes (the lifecycle test starts this file as an engine)

def _daemon(port: int, pidfile: str) -> None:
    p = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--fake-engine", str(port)],
                         start_new_session=True, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    with open(pidfile, "w") as f:
        f.write(str(p.pid))


def _stop(pidfile: str) -> None:
    import signal
    with open(pidfile) as f:
        pid = int(f.read())
    os.kill(pid, signal.SIGTERM)
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--fake-engine"]:
        fake_engine_main(int(sys.argv[2]))
    elif sys.argv[1:2] == ["--fake-daemon"]:
        _daemon(int(sys.argv[2]), sys.argv[3])
    elif sys.argv[1:2] == ["--fake-stop"]:
        _stop(sys.argv[2])
    else:
        run(globals())
