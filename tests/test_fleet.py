"""SRV-724: TandemApp's controller, the fleet registry and `/v1/fleet/*` (SRV-723).

Every answer is held to docs/contract/fleet-v1 with tools/contract_check.py, the schemas the
dashboard's mock is held to. The machine is a fake: a directory standing for /etc, /proc and /sys,
a host collector's entry, and a TandemLLM engine on a loopback port with its live snapshot, model
list, system snapshot and log. Time is injected; nothing sleeps.

  1  a Spark is node #1 at the first start: its hostname as the alias, colour 1, an id from its
     serial; the same id at the next start, a new address logged, no second node
                                                         test_one_box_upgrade_registers_once
  2  a machine without a GPU or an engine of its own is no node    test_a_mac_in_front_of_clouds
  3  alias, colour, icon and tags: PATCH, refused values, kept     test_alias_and_colour
  4  every route answers the contract, the engine's own model only, the host and this
     machine's sample read in-process                              test_every_route_validates
  5  the sampler: slots, ranges, nulls before history, a deployment's own figures
                                                                   test_ring_and_ranges
  6  memory split and the engine's log beside tandem-app's        test_memory_and_logs
  7  layouts: the default, a save that comes back, refused bodies, kept across a restart
                                                                   test_layouts_round_trip
  8  the guard: reads and layouts as the dashboard, writes as the configuration
                                                                         test_auth_guard
  9  tandem-app starts, answers the fleet API and stops on SIGTERM       test_start_stop
 10  drain (VIS-250): what is in flight finishes, a new request goes to the route's fallback or
     gets 503 node_draining with Retry-After, draining then drained, kept across a restart,
     undrain                                                         test_drain_and_the_gateway
 11  the node actions' plans: what goes and what stays, blocked with the reason, the confirm a
     destructive one needs (missing, spent, another action's), forget, a lease's release 501
     for a rented node and 409 for an owned one, a link with no wire  test_node_action_plans

    python tests/test_fleet.py
"""

from __future__ import annotations

import json
import math
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_backend  # noqa: E402
from proxy_util import Proxy, run  # noqa: E402
from ports import free_port  # noqa: E402
from proxy import app as proxy_app  # noqa: E402
from proxy import config as config_mod  # noqa: E402
from proxy import fleet as fleet_mod  # noqa: E402
from proxy import nodesource  # noqa: E402
from tools import contract_check  # noqa: E402

CONTRACT = os.path.join(ROOT, "docs", "contract", "fleet-v1")
ADMIN = "fleet-admin-token-0123456789abcdef0123"
SERIAL = "TEST-SERIAL-0001"
HOSTNAME = socket.gethostname().split(".")[0]
T0 = 1_800_000_001.0                       # an odd second: mid-slot, as the sampler ticks
GIB = 1024 ** 3


def ok(obj, name):
    errs = contract_check.validate(obj, contract_check.load(name, CONTRACT), root=CONTRACT)
    assert not errs, (name, errs[:5])
    return obj


# ---------------------------------------------------------------------------- a fake machine

def _write(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(text)


def fake_root(serial=SERIAL, fabric_bytes=0) -> str:
    r = tempfile.mkdtemp(prefix="fleet-root-")
    _write(r, "etc/dgx-release", 'DGX_PRETTY_NAME="NVIDIA DGX Spark"\nDGX_OTA_VERSION="7.5.0"\n'
                                 + (f'DGX_SERIAL_NUMBER="{serial}"\n' if serial else ""))
    _write(r, "etc/os-release", 'PRETTY_NAME="Ubuntu 24.04 LTS"\n')
    _write(r, "etc/machine-id", "0123456789abcdef0123456789abcdef\n")
    _write(r, "sys/class/dmi/id/sys_vendor", "Test Vendor\n")
    _write(r, "sys/class/dmi/id/product_name", "TX10\n")
    _write(r, "proc/net/route", "Iface\tDestination\tGateway\nlan0\t00000000\t0101A8C0\n")
    _write(r, "proc/meminfo", "MemTotal: 127535336 kB\nBuffers: 1048576 kB\nCached: 3145728 kB\n")
    _write(r, "proc/driver/nvidia/version",
           "NVRM version: NVIDIA UNIX Open Kernel Module for aarch64  580.1.2  Release Build\n")
    for n, speed, state in (("lan0", "10000", "up"), ("fab0", "200000", "up")):
        _write(r, f"sys/class/net/{n}/speed", speed + "\n")
        _write(r, f"sys/class/net/{n}/mtu", "1500\n")
        _write(r, f"sys/class/net/{n}/operstate", state + "\n")
        os.makedirs(os.path.join(r, f"sys/class/net/{n}/device"), exist_ok=True)
    os.makedirs(os.path.join(r, "drivers/mlx5_core"))
    os.symlink(os.path.join(r, "drivers/mlx5_core"), os.path.join(r, "sys/class/net/fab0/device/"
                                                                       "driver"))
    set_fabric(r, fabric_bytes)
    return r


def set_fabric(root, n):
    _write(root, "sys/class/net/fab0/statistics/rx_bytes", f"{n}\n")
    _write(root, "sys/class/net/fab0/statistics/tx_bytes", "0\n")


def host(gpu=True, used_gib=40.0, util=0.25):
    total = 121.6 * GIB
    return {"memory": {"total_bytes": int(total), "available_bytes": int(total - used_gib * GIB),
                       "used_bytes": int(used_gib * GIB), "unified": True},
            "cpu": {"count": 20, "utilization": util},
            "gpus": [{"name": "NVIDIA GB10", "utilization": 0.5, "power_w": 60.0,
                      "temperature_c": 55, "sm_clock_mhz": 2400, "mem_clock_mhz": None}] if gpu
            else [],
            "disks": [{"name": "nvme0n1", "nvme": True, "read_bps": 2e6, "write_bps": 1e6}],
            "net": [{"name": "lan0", "rx_bps": 3e5, "tx_bps": 2e5},
                    {"name": "fab0", "rx_bps": 9e9, "tx_bps": 0.0}],
            "fs": [{"path": "/", "total_bytes": 4_000_000_000_000,
                    "free_bytes": 2_710_000_000_000}]}


SMI = "| NVIDIA-SMI 580.1.2   Driver Version: 580.1.2   CUDA Version: 13.0 |\n"


def local_node(root, h=None, address="192.0.2.40", logbuf=None, **kw):
    state = {"host": h if h is not None else host(), "address": address}
    n = nodesource.LocalNode(hosts=lambda: state["host"], root=root,
                             run=lambda argv: SMI if argv[0] == "nvidia-smi" else None,
                             address=lambda: state["address"], logbuf=logbuf,
                             version=lambda: "0.15.0-app", **kw)
    n.state = state
    return n


class FakeBuf:
    def __init__(self, lines):
        self._lines = lines

    def lines(self, *, level="info", after=None, grep=None, limit=500):
        rank = {"debug": 0, "info": 1, "warning": 2, "error": 3}
        return [e for e in self._lines if rank[e["level"]] >= rank[level]
                and (not grep or grep in e["msg"])][-limit:]


# ---------------------------------------------------------------------------- a fake engine

class Engine:
    """A TandemLLM engine on loopback: what tandem-app reads from it, on the test's clock."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self.gen = 0                       # tokens decoded so far, the live sample's count
        self.running = 0
        self.samples = []                  # its sampler's one-a-second samples (here: per step)
        self.ended = []                    # (end time, ttft ms) of the requests so far
        self.loaded = True
        self.live_reads = 0
        eng = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                p = self.path.split("?")[0]
                q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
                doc = {"/v1/models": eng.models, "/v1/dashboard/system": eng.system,
                       "/v1/dashboard/live": eng.live,
                       "/v1/dashboard/logs": lambda: eng.logs(q)}.get(p)
                if doc is None:
                    return self._send(b"{}", "application/json", 404)
                return self._send(json.dumps(doc()).encode(), "application/json")

            def _send(self, raw, ctype, code=200):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/v1"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()

    def step(self, t, tokens):
        """One sample of its sampler at t: `tokens` more decoded since the last."""
        self.gen += tokens
        self.samples.append({"t": t, "decode_tps": None, "running": self.running,
                             "waiting": 0, "tokens": self.gen})

    def live(self):
        self.live_reads += 1
        now = self.clock()
        at = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)) + f".{int(now * 1000) % 1000:03d}Z"
        hist = self.samples[-300:]
        return {"contract_version": "1.1", "generated_at": at,
                "engine": {"version": "0.15.0-test", "model": "TandemLLM"},
                "counts": {"in_flight": self.running},
                "sample": hist[-1] if hist else None, "history": hist,
                "recent": [{"ttft_ms": ms, "ended_ms_ago": (now - end) * 1000.0}
                           for end, ms in self.ended if end <= now]}

    def models(self):
        return {"object": "list", "data": [
            {"id": "TandemLLM", "object": "model", "owned_by": "tandemllm",
             "tandem": {"backend": "engine", "kind": "engine", "label": "Test Model",
                        "model": "test-model", "pack": "pack-4.0", "context_length": 131072,
                        "loaded": self.loaded}},
            {"id": "cloud-x", "object": "model", "owned_by": "cloudy",
             "tandem": {"backend": "cloudy", "kind": "deepseek", "cloud": True}}]}

    def system(self):
        return {"memory": {"split": {"parts": {"weights": 40 * GIB, "mtp_weights": 1 * GIB,
                                               "kv_pool": 8 * GIB, "gdn_state": 0.5 * GIB,
                                               "resident_prefix": 2 * GIB},
                                     "pinned_cache": 0, "drafter_caches": 0},
                           "limits": {"rows": [{"key": "MEM_CEILING_GIB", "running": 66.0}]}}}

    def logs(self, q):
        rank = {"debug": 0, "info": 1, "warning": 2, "error": 3}
        lines = [e for e in self.log_lines() if rank[e["level"]] >= rank[q.get("level", "info")]
                 and q.get("grep", "") in e["msg"]]
        return {"contract_version": "1.0", "last_seq": 2,
                "lines": lines[-int(q.get("backlog", 500)):]}

    def log_lines(self):
        return [
            {"seq": 1, "ts": "2027-01-15T08:00:01.000Z", "level": "info", "source": "req",
             "msg": "[req] done 120 tok", "request_id": None},
            {"seq": 2, "ts": "2027-01-15T08:00:03.000Z", "level": "warning", "source": "cache",
             "msg": "[cache] put declined", "request_id": None}]


def engine_env(eng: Engine) -> dict:
    return {"TANDEM_BACKENDS": "eng", "TANDEM_BACKEND_ENG_KIND": "tandemllm",
            "TANDEM_BACKEND_ENG_URL": eng.url, "TANDEM_BACKEND_ENG_LABEL": "Engine"}


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


def fleet_on(px: Proxy, node, clock=None, cfg=None, store=None) -> fleet_mod.Fleet:
    """The proxy's fleet with a fake machine (proxy_app.build made one with the real one), over
    `cfg` when a test keeps its own file, and `store` (else one in memory). The controller's
    machine never sleeps here, whatever runs the test."""
    f = fleet_mod.Fleet(px.svc, node, port=px.port, clock=clock or time.time, log=px.logs.append,
                        store=store)
    f.cfg = cfg or px.svc.cfg
    f.may_sleep = False
    proxy_app.STATE["fleet"] = f
    return f


def get(px, path, headers=None):
    st, _, body = px.get(path, headers=headers)
    return st, json.loads(body)


def send(px, method, path, body, headers=None):
    st, _, out = px.request(method, path, body, headers=headers)
    return st, json.loads(out)


# ---------------------------------------------------------------------------- the tests

def test_one_box_upgrade_registers_once():
    root = fake_root()
    d = tempfile.mkdtemp(prefix="fleet-db-")
    path = os.path.join(d, "config.sqlite3")
    with Proxy() as px:
        cfg = config_mod.ConfigDB(path, env={}, use_keychain=False).open()
        f = fleet_on(px, local_node(root), cfg=cfg)
        nodes = ok(get(px, "/v1/fleet/nodes")[1], "nodes")["nodes"]
        assert len(nodes) == 1, nodes
        n = nodes[0]
        assert (n["alias"], n["colour"], n["status"], n["address"]) == \
            (HOSTNAME, 1, "up", "192.0.2.40"), n
        assert n["id"].startswith("n-") and SERIAL not in n["id"]
        assert n["hw"] == "NVIDIA DGX Spark" and n["dmi"] == "Test Vendor TX10"
        assert n["inventory"] == {"serial": SERIAL, "os": "DGX OS 7.5.0 (Ubuntu 24.04 LTS)",
                                  "kernel": n["inventory"]["kernel"], "driver": "580.1.2",
                                  "cuda": "13.0", "cx7": "1 ConnectX port · 1 up",
                                  "disk": "NVMe 4.00 TB · 2.71 TB free",
                                  "nics": "fab0 (200 GbE, up) · lan0 (10 GbE, up)"}, n
        assert any("this machine is node" in m for m in px.logs)
        # the next start: the same node, nothing added; a new address is said, the id stays
        f2 = fleet_on(px, local_node(root, address="192.0.2.52"), cfg=cfg)
        again = get(px, "/v1/fleet/nodes")[1]["nodes"]
        assert [x["id"] for x in again] == [n["id"]] and again[0]["address"] == "192.0.2.52"
        assert any("moved from 192.0.2.40 to 192.0.2.52" in m for m in px.logs), px.logs
        assert f2.ensure_local() == f.ensure_local() == n["id"]
        # the id follows the serial, not the database: a fresh file gives the same id
        cfg2 = config_mod.ConfigDB(os.path.join(d, "b.sqlite3"), env={},
                                   use_keychain=False).open()
        fleet_on(px, local_node(root), cfg=cfg2)
        assert get(px, "/v1/fleet/nodes")[1]["nodes"][0]["id"] == n["id"]
        # without a serial the machine id stands in, still not the address
        no_serial = local_node(fake_root(serial=None))
        assert no_serial.identity()["id"] != n["id"] and no_serial.identity()["serial"] is None
        cfg.close()
        cfg2.close()


def test_a_mac_in_front_of_clouds():
    with Proxy(env={"TANDEM_BACKENDS": "cloud", "TANDEM_BACKEND_CLOUD_KIND": "openai",
                    "TANDEM_BACKEND_CLOUD_KEY": "sk-fixture-ABCDEFGH1234"}) as px:
        fleet_on(px, local_node(fake_root(), h=host(gpu=False)))
        assert ok(get(px, "/v1/fleet/nodes")[1], "nodes")["nodes"] == []
        assert ok(get(px, "/v1/fleet/deployments")[1], "deployments")["deployments"] == []
        topo = ok(get(px, "/v1/fleet/topology")[1], "topology")
        assert [i["kind"] for i in topo["infra"]] == ["host", "lan", "internet"], topo
        assert ok(get(px, "/v1/fleet/memory")[1], "memory")["nodes"] == []
        m = ok(get(px, "/v1/fleet/metrics?metric=tps&range=5m")[1], "metrics")
        assert m["series"] == []


def test_alias_and_colour():
    with Proxy() as px:
        fleet_on(px, local_node(fake_root()))
        nid = get(px, "/v1/fleet/nodes")[1]["nodes"][0]["id"]
        st, n = send(px, "PATCH", f"/v1/fleet/nodes/{nid}",
                     {"alias": "  Desk  ", "colour": 3, "icon": "rack", "tags": ["desk", "desk"]})
        assert st == 200, n
        ok({"contract_version": "1.0", "nodes": [n]}, "nodes")
        ok(n, "node")
        assert (n["alias"], n["colour"], n["icon"], n["tags"]) == ("Desk", 3, "rack", ["desk"])
        assert get(px, f"/v1/fleet/nodes/{nid}") == (200, n)
        # a second node (a record before its agent) to collide with
        px.svc.cfg.add_node({"id": "n-remote", "alias": "R-1", "endpoint": "agent",
                             "address": "192.0.2.41:7700", "serial": "TEST-SERIAL-0002"})
        nodes = ok(get(px, "/v1/fleet/nodes")[1], "nodes")["nodes"]
        r1 = nodes[1]
        assert (r1["colour"], r1["status"], r1["version"]) == (1, "off", ""), r1
        assert r1["inventory"]["serial"] == "TEST-SERIAL-0002" and r1["inventory"]["os"] is None
        for body in ({"alias": "r-1"}, {"colour": 1}, {"colour": 9}, {"colour": True},
                     {"alias": ""}, {"alias": "x" * 41}, {"icon": "tower"}, {"tags": "desk"},
                     {"tags": ["x" * 33]}, {"owner": "me"}, {}, ["alias"]):
            st, err = send(px, "PATCH", f"/v1/fleet/nodes/{nid}", body)
            assert st == 400, (body, st, err)
            ok(err, "error")
        assert send(px, "PATCH", "/v1/fleet/nodes/n-nope", {"alias": "x"})[0] == 404
        st, err = get(px, "/v1/fleet/nodes/n-nope")
        assert st == 404 and ok(err, "error")
        st, _, raw = px.request("PATCH", f"/v1/fleet/nodes/{nid}", b"{nope")
        assert st == 400 and ok(json.loads(raw), "error")
        assert get(px, f"/v1/fleet/nodes/{nid}")[1]["alias"] == "Desk"
        assert px.svc.cfg.audit(1)[0]["object"] == "node"


def test_every_route_validates():
    clock = Clock()
    eng = Engine(clock)
    try:
        with Proxy(env=engine_env(eng)) as px:
            px.sync()
            node = local_node(fake_root(), clock=clock)
            f = fleet_on(px, node, clock=clock)
            for i in range(40):
                clock.t = T0 + 2 * i
                eng.step(clock.t, 100)
                f.tick()
            nodes = ok(get(px, "/v1/fleet/nodes")[1], "nodes")["nodes"]
            lid = nodes[0]["id"]
            assert nodes[0]["version"] == "0.15.0-test"
            deps = ok(get(px, "/v1/fleet/deployments")[1], "deployments")["deployments"]
            assert deps == [{"id": "TandemLLM", "name": "Test Model", "model": "test-model",
                             "pack": "pack-4.0", "type": "single", "members": [lid],
                             "api_id": "TandemLLM", "ctx": 131072, "status": "serving",
                             "status_text": "serving"}], deps
            links = ok(get(px, "/v1/fleet/links")[1], "links")["links"]
            assert links == [{"id": f"lan-{lid}", "a": "lan", "b": lid, "kind": "lan",
                              "speed": "10 GbE", "rtt_ms": None, "mtu": 1500, "state": "up",
                              "note": "lan0; the controller runs on this node"}], links
            topo = ok(get(px, "/v1/fleet/topology")[1], "topology")
            assert topo["groups"] == [{"deployment": "TandemLLM", "type": "single",
                                       "members": [lid]}]
            assert topo["host"]["name"] == HOSTNAME and topo["infra"] == \
                [{"id": "lan", "kind": "lan", "label": "LAN"}]
            ok(get(px, "/v1/fleet/memory")[1], "memory")
            # the host: this machine is the node; the controller's own process
            h = ok(get(px, "/v1/fleet/host")[1], "host")
            assert (h["name"], h["node"], h["may_sleep"]) == (HOSTNAME, lid, False), h
            assert h["cpu"] == {"util": 25.0, "cores": 20} and h["memory"]["total_gib"] == 121.6
            assert h["controller"]["threads"] >= 1 and h["controller"]["uptime_s"] >= 0
            # this machine read in-process: the fleet's keys, its GPU and memory, said so
            sm = ok(get(px, f"/v1/fleet/nodes/{lid}/sample")[1], "sample")
            assert sm["via"] == "process" and sm["note"].startswith("read in-process"), sm
            assert sm["sample"]["node"]["temp"] == 55.0 and sm["sample"]["node"]["power"] == 60.0
            assert sm["sample"]["gpus"][0]["temp_c"] == 55 and "mem_mhz" not in \
                sm["sample"]["gpus"][0] and "power_cap_w" not in sm["sample"]["gpus"][0]
            assert sm["sample"]["memory"]["total"] == 121.6
            for name in ("events", "alerts"):
                assert ok(get(px, f"/v1/fleet/{name}")[1], name)[name] == []
            assert [r["id"] for r in ok(get(px, "/v1/fleet/rules")[1], "rules")["rules"]] == \
                ["gpu-temp", "throttle", "mem-avail", "disk-free", "node-down", "engine-down",
                 "host-sleep"]
            assert ok(get(px, "/v1/fleet/events?since=1800000000")[1], "events")["events"] == []
            ok(get(px, "/v1/fleet/logs?node=all")[1], "logs")
            ok(get(px, f"/v1/fleet/logs?node={lid}&level=warn&limit=20")[1], "logs")
            for metric in fleet_mod.METRICS:
                for rng in fleet_mod.RANGES:
                    st, m = get(px, f"/v1/fleet/metrics?source=all&metric={metric}&range={rng}")
                    assert st == 200, m
                    ok(m, "metrics")
                    assert len(m["series"][0]["values"]) == fleet_mod.RANGES[rng][1]
            for source in (f"node:{lid}", "deployment:TandemLLM"):
                for metric in ("tps", "temp", "fabric"):
                    ok(get(px, f"/v1/fleet/metrics?source={source}&metric={metric}"
                               f"&range=1h")[1], "metrics")
            for path, status in (("/v1/fleet/metrics?metric=watts", 400),
                                 ("/v1/fleet/metrics?metric=tps&range=3h", 400),
                                 ("/v1/fleet/metrics?metric=tps&source=r3", 400),
                                 ("/v1/fleet/metrics?metric=tps&source=node:r9", 404),
                                 ("/v1/fleet/metrics?metric=tps&source=deployment:nope", 404),
                                 ("/v1/fleet/events?since=yesterday", 400),
                                 ("/v1/fleet/layouts/overview?device=watch", 400),
                                 ("/v1/fleet/logs?node=r9", 404),
                                 ("/v1/fleet/logs?node=all&level=loud", 400),
                                 ("/v1/fleet/logs?node=all&limit=0", 400),
                                 ("/v1/fleet/nodes/r9/sample", 404),
                                 ("/v1/fleet/sparks", 404)):
                st, err = get(px, path)
                assert st == status, (path, st, err)
                ok(err, "error")
            st, err = send(px, "POST", "/v1/fleet/alerts/nope/ack", None)
            assert st == 404 and ok(err, "error")
            # the engine loads, then goes away
            eng.loaded = False
            node.probes["eng"]._cards_at = -1e9
            assert get(px, "/v1/fleet/deployments")[1]["deployments"][0]["status"] == "starting"
            px.svc.health.state["eng"] = {"state": "down", "last_error": "unreachable"}
            d = ok(get(px, "/v1/fleet/deployments")[1], "deployments")["deployments"][0]
            assert (d["status"], d["status_text"]) == ("down", "down · unreachable")
            # every old route still answers
            assert px.get("/v1/models")[0] == 200
            assert px.get("/v1/dashboard/system")[0] == 200
            assert px.get("/v1/config")[0] == 200
    finally:
        eng.close()


def test_ring_and_ranges():
    clock = Clock()
    eng = Engine(clock)
    root = fake_root()
    try:
        with Proxy(env=engine_env(eng)) as px:
            px.sync()
            node = local_node(root, clock=clock)
            f = fleet_on(px, node, clock=clock)
            # 60 ticks, one per 2 s slot: 100 tokens a tick, one request ends before the 10th;
            # a dashboard reads the fleet meanwhile, so the engine is read at every tick
            for i in range(60):
                clock.t = T0 + 2 * i
                if i % 10 == 0:
                    get(px, "/v1/fleet/nodes")
                if i == 10:
                    eng.ended.append((T0 + 19.5, 300.0))
                set_fabric(root, i * 250_000_000)            # 1 Gb/s
                eng.step(clock.t, 100)
                f.tick()
            assert eng.live_reads == 60
            lid = f.ensure_local()

            def series(q):
                m = ok(get(px, "/v1/fleet/metrics?" + q)[1], "metrics")
                return m, m["series"][0]["values"]
            m, tps = series("metric=tps&range=5m")
            end = T0 + 2 * 59
            assert m["step_s"] == 2 and m["t0"] == (end - 1) - 300 and m["agg"] == "sum"
            # sample i is at t0 + 2i and holds the slot (t_i - 2, t_i]: the newest tick's slot
            # is not over yet, so the last sample is the tick before; a rate needs two ticks
            assert tps[-1] == 50.0 and tps[-58] == 50.0 and tps[-59] is None
            assert all(v is None for v in tps[:-59]), "nothing before the process started"
            _, ttft = series("metric=ttft&range=5m")
            assert [v for v in ttft if v is not None] == [300.0]   # counted once
            _, fab = series("metric=fabric&range=5m")
            assert fab[-1] == 1.0 and fab[-58] == 1.0 and fab[-59] is None
            _, mem = series("metric=mem&range=5m")
            assert mem[-1] == 40.0
            m, gpu = series("metric=gpu&range=1h")
            assert m["step_s"] == 30 and m["agg"] == "mean" and gpu[-1] == 50.0
            assert all(v is None for v in gpu[:-5])
            m, cpu = series("metric=temp&range=7d")
            assert m["step_s"] == 3600 and m["agg"] == "maxmean" and cpu[-1] == 55.0
            assert m["cap"] is None and series("metric=mem&range=6h")[0]["cap"] == 121.6
            # the means of a 30 s slot: 15 ticks of 0.25 and 0.75 CPU in one slot give 50 %
            r = fleet_mod.Ring()
            r.add(31.0, 0.25 * 100)
            r.add(59.0, 0.75 * 100)
            assert r.mean(30, 2, 2) == 50.0 and r.mean(2, 16, 16) == 25.0
            assert r.mean(2, 17, 29) is None and r.peak(60.0, 3600) == 75.0
            # a deployment's tok/s are its engine's; its hardware the member's
            _, dtps = series("source=deployment:TandemLLM&metric=tps&range=5m")
            assert dtps == tps
            m, dtemp = series("source=deployment:TandemLLM&metric=temp&range=5m")
            assert m["series"][0]["node"] == lid and dtemp[-1] == 55.0
            # a counter reset (the engine restarted) is no sample, never a negative rate
            eng.gen = -100
            clock.t = end + 2
            eng.step(clock.t, 100)
            f.tick()
            clock.t += 2                                      # that tick's slot is over
            assert series("metric=tps&range=5m")[1][-1] is None
            # an idle engine's sampler rests: no new sample and nothing in flight is 0 tok/s;
            # with a request in flight it is no sample
            p = node.probes["eng"]
            assert [v["tps"] for _, v in p.points(clock.t)] == [0.0]
            eng.running = 1
            assert p.points(clock.t + 2) == []
    finally:
        eng.close()


def test_engine_history_and_cadence():
    """The first read brings the engine's last five minutes; unread by any dashboard the engine
    is asked every 30 s, read every 2 s."""
    clock = Clock()
    eng = Engine(clock)
    try:
        with Proxy(env=engine_env(eng)) as px:
            px.sync()
            for k in range(151):                              # five minutes up to the start
                eng.step(T0 - 300 + 2 * k, 60)
            f = fleet_on(px, local_node(fake_root(), clock=clock), clock=clock)
            f.tick()
            assert eng.live_reads == 1
            clock.t += 1                                      # the slot of the tick is over
            tps = get(px, "/v1/fleet/metrics?metric=tps&range=5m")[1]["series"][0]["values"]
            assert tps == [None] + [30.0] * 150, tps[:4]   # its oldest has no rate before it
            eng.live_reads = 0
            f._read_at = -math.inf                            # nobody reads the fleet
            for i in range(1, 16):
                clock.t = T0 + 2 * i
                eng.step(clock.t, 60)
                f.tick()
            assert eng.live_reads == 1, "every 30 s"          # at T0 + 30
            get(px, "/v1/fleet/nodes")                       # a dashboard reads
            for i in range(16, 20):
                clock.t = T0 + 2 * i
                eng.step(clock.t, 60)
                f.tick()
            assert eng.live_reads == 5
            clock.t += 1
            tps = get(px, "/v1/fleet/metrics?metric=tps&range=5m")[1]["series"][0]["values"]
            assert tps[-20:] == [30.0] * 20, "the 30 s between reads came with the next one"
    finally:
        eng.close()


def test_memory_and_logs():
    eng = Engine()
    try:
        with Proxy(env=engine_env(eng)) as px:
            px.sync()
            buf = FakeBuf([{"seq": 1, "ts": "2027-01-15T08:00:02.000Z", "level": "info",
                            "source": "proxy", "msg": "[proxy] backend eng is up"},
                           {"seq": 2, "ts": "2027-01-15T08:00:04.000Z", "level": "error",
                            "source": "traceback", "msg": "Traceback: boom"}])
            node = local_node(fake_root(), h=host(used_gib=60.0), logbuf=buf)
            f = fleet_on(px, node)
            lid = f.ensure_local()
            mem = ok(get(px, "/v1/fleet/memory")[1], "memory")["nodes"]
            assert mem == [{"node": lid, "weights": 41.0, "kv": 8.5, "cache": 2.0, "other": 8.5,
                            "page": 4.0, "ceiling": 66.0, "peak": 60.0, "usable": 121.6}], mem
            lines = ok(get(px, "/v1/fleet/logs?node=all")[1], "logs")["lines"]
            assert [(x["source"], x["level"], x["msg"]) for x in lines] == [
                ("engine", "info", "[req] done 120 tok"),
                ("gateway", "info", "[proxy] backend eng is up"),
                ("engine", "warn", "[cache] put declined"),
                ("gateway", "error", "Traceback: boom")], lines
            assert all(x["node"] == lid for x in lines)
            assert [x["msg"] for x in get(px, "/v1/fleet/logs?node=all&level=error")[1]
                    ["lines"]] == ["Traceback: boom"]
            assert len(get(px, f"/v1/fleet/logs?node={lid}&limit=2")[1]["lines"]) == 2
    finally:
        eng.close()


def test_layouts_round_trip():
    d = tempfile.mkdtemp(prefix="fleet-db-")
    path = os.path.join(d, "config.sqlite3")
    clock = Clock()
    with Proxy() as px:
        cfg = config_mod.ConfigDB(path, env={}, use_keychain=False).open()
        fleet_on(px, local_node(fake_root()), clock=clock, cfg=cfg)
        st, dflt = get(px, "/v1/fleet/layouts/overview?device=phone")
        assert st == 200 and ok(dflt, "layout")["saved_at"] is None and not dflt["rail"]["open"]
        windows = [{"id": f"w{i}", "kind": k, "ref": {"node": "n-1", "n": i},
                    "at": ("right", "bottom", "float", "min")[i % 4],
                    **({"prev": "float"} if i % 4 == 3 else {}),
                    "x": 60, "y": 40, "w": 560, "h": 360, "z": i}
                   for i, k in enumerate(fleet_mod.WINDOW_KINDS)]
        body = {"page": "overview", "device": "desk", "rail": {"open": True, "w": 248},
                "ctx": {"open": False, "w": 340}, "windows": windows,
                "docks": {"right": {"f": 0.4, "act": "w0"}, "bottom": {"f": 0.3, "act": None}},
                "saved_at": T0 - 999}
        clock.t = T0 + 2
        st, put = send(px, "PUT", "/v1/fleet/layouts/overview?device=desk", body)
        assert st == 200 and ok(put, "layout") == dict(body, saved_at=T0 + 2), put
        assert get(px, "/v1/fleet/layouts/overview?device=desk") == (200, put)
        assert get(px, "/v1/fleet/layouts/overview?device=tablet")[1]["saved_at"] is None
        # a page name with a slash, encoded
        page = {**body, "page": "fleet/sparks", "saved_at": None}
        assert send(px, "PUT", "/v1/fleet/layouts/fleet%2Fsparks?device=desk", page)[0] == 200
        # the last write wins
        clock.t = T0 + 5
        later = dict(body, windows=[])
        assert send(px, "PUT", "/v1/fleet/layouts/overview?device=desk", later)[1]["windows"] \
            == []
        w0 = windows[0]
        for wrong in (None, [], dict(body, page="usage"), dict(body, device="phone"),
                      dict(body, extra=1), dict(body, rail={"open": "yes", "w": 1}),
                      dict(body, docks={"right": {"f": 0.4, "act": None}}),
                      dict(body, windows=[dict(w0, kind="clock")]),
                      dict(body, windows=[dict(w0, at="left")]),
                      dict(body, windows=[dict(w0, prev="min")]),
                      dict(body, windows=[dict(w0, ref="n-1")]),
                      dict(body, windows=[dict(w0, x="60")]),
                      dict(body, windows=[w0] * 65),
                      dict(body, saved_at="today")):
            st, err = send(px, "PUT", "/v1/fleet/layouts/overview?device=desk", wrong)
            assert st == 400, (wrong, err)
            ok(err, "error")
        cfg.close()
        # kept across a restart
        cfg2 = config_mod.ConfigDB(path, env={}, use_keychain=False).open()
        fleet_on(px, local_node(fake_root()), clock=clock, cfg=cfg2)
        back = get(px, "/v1/fleet/layouts/overview?device=desk")[1]
        assert back["windows"] == [] and back["saved_at"] == T0 + 5
        assert get(px, "/v1/fleet/layouts/fleet%2Fsparks?device=desk")[1]["page"] == \
            "fleet/sparks"
        cfg2.close()


def test_auth_guard():
    lan = {"X-Real-IP": "192.0.2.77"}
    bearer = {"Authorization": f"Bearer {ADMIN}"}
    layout = dict(fleet_mod.default_layout("overview", "desk"), saved_at=None)
    with Proxy(env={"TANDEM_ADMIN_TOKEN": ADMIN}, login="on") as px:
        fleet_on(px, local_node(fake_root()))
        nid = get(px, "/v1/fleet/nodes", bearer)[1]["nodes"][0]["id"]
        for path in ("/v1/fleet/nodes", "/v1/fleet/metrics?metric=tps",
                     "/v1/fleet/layouts/overview"):
            st, err = get(px, path, lan)
            assert st == 401 and ok(err, "error")["error"]["type"] == "unauthorized", path
            assert get(px, path, {**lan, **bearer})[0] == 200
        for method, path, body in (("PUT", "/v1/fleet/layouts/overview", layout),
                                   ("PATCH", f"/v1/fleet/nodes/{nid}", {"alias": "Desk"}),
                                   ("POST", "/v1/fleet/alerts/a/ack", None)):
            st, err = send(px, method, path, body, lan)
            assert st == 401 and ok(err, "error"), (method, path)
        assert send(px, "PUT", "/v1/fleet/layouts/overview", layout, {**lan, **bearer})[0] == 200
        # a tool on this machine writes as it writes the configuration
        assert send(px, "PATCH", f"/v1/fleet/nodes/{nid}", {"alias": "Desk"})[0] == 200
    with Proxy(env={"TANDEM_ADMIN_TOKEN": ADMIN}, login="off") as px:
        fleet_on(px, local_node(fake_root()))
        assert get(px, "/v1/fleet/nodes", lan)[0] == 200          # the login off: reads open
        # a page's window layout follows the reads (SEC-4): where windows sit, no fleet state
        assert send(px, "PUT", "/v1/fleet/layouts/overview", layout, lan)[0] == 200
        assert send(px, "PATCH", f"/v1/fleet/nodes/{nid}", {"alias": "Desk"}, lan)[0] == 401
    with Proxy() as px:                                             # no password: all open
        fleet_on(px, local_node(fake_root()))
        assert send(px, "PUT", "/v1/fleet/layouts/overview", layout, lan)[0] == 200


def _chat(px, text, stream=True, model="fake-a", out=None):
    st, h, body = px.post("/v1/chat/completions", {"model": model, "stream": stream,
                                                   "messages": [{"role": "user",
                                                                 "content": text}]})
    if out is not None:
        out.append((st, body))
    return st, h, body


def _until(fn, s=10.0):
    t = time.time() + s
    while time.time() < t:
        if fn():
            return True
        time.sleep(0.02)
    return False


def test_drain_and_the_gateway():
    srv, port = fake_backend.start(models=["fake-a"])
    env = {"TANDEM_BACKENDS": "eng", "TANDEM_BACKEND_ENG_KIND": "generic",
           "TANDEM_BACKEND_ENG_URL": f"http://127.0.0.1:{port}/v1",
           "TANDEM_BACKEND_ENG_LABEL": "Engine"}
    try:
        with Proxy(env=env) as px:
            px.sync()
            root = fake_root()
            f = fleet_on(px, local_node(root))
            nid = get(px, "/v1/fleet/nodes")[1]["nodes"][0]["id"]
            n = ok(get(px, f"/v1/fleet/nodes/{nid}")[1], "node")
            assert (n["status"], n["drain"]) == ("up", None), n
            # a request in flight: two seconds before its first token
            first = []
            t = threading.Thread(target=_chat, args=(px, "FAKE_SLOW_TTFT 2"),
                                 kwargs={"out": first})
            t.start()
            assert _until(lambda: px.svc.relay.inflight.count("eng") == 1)
            plan = ok(get(px, f"/v1/fleet/nodes/{nid}/actions/drain")[1], "action-plan")
            assert plan["allowed"] and not plan["destructive"] and plan["confirm"] is None
            assert f"1 request in flight on {HOSTNAME} finish" in plan["impact"], plan
            assert any(x.startswith("fake-a: new requests wait, the gateway answers 503 with "
                                    "Retry-After 30 s") for x in plan["impact"]), plan
            st, res = send(px, "POST", f"/v1/fleet/nodes/{nid}/actions/drain", {})
            assert st == 200 and ok(res, "action-result")["state"] == "done", res
            n = ok(get(px, f"/v1/fleet/nodes/{nid}")[1], "node")
            assert (n["status"], n["status_text"]) == ("draining", "draining · 1 in flight"), n
            assert n["drain"]["state"] == "draining" and n["drain"]["inflight"] == 1
            assert n["drain"]["by"] == "local"                  # a caller on this machine
            # a new request: no fallback, so it waits
            st, h, body = _chat(px, "hello", stream=False)
            err = json.loads(body)["error"]
            assert st == 503 and h.get("Retry-After") == "30", (st, h)
            assert err["code"] == "node_draining" and HOSTNAME in err["message"], err
            # the one in flight runs to its end
            t.join(15)
            assert first[0][0] == 200 and b"[DONE]" in first[0][1], first
            f.tick()
            n = ok(get(px, f"/v1/fleet/nodes/{nid}")[1], "node")
            assert (n["status_text"], n["drain"]["state"], n["drain"]["inflight"]) == \
                ("drained · nothing in flight", "drained", 0), n
            texts = [e["text"] for e in get(px, "/v1/fleet/events?type=node")[1]["events"]]
            assert texts[:2] == [f"{HOSTNAME} is drained", f"{HOSTNAME} drains: no new "
                                                           f"requests"], texts
            p2 = get(px, f"/v1/fleet/nodes/{nid}/actions/drain")[1]
            assert not p2["allowed"] and "drained already" in p2["blocked"]
            assert send(px, "POST", f"/v1/fleet/nodes/{nid}/actions/drain", {})[0] == 409
            # the drain is the record's: a new controller over the file holds it at once
            fleet_on(px, local_node(root))
            assert _chat(px, "again", stream=False)[0] == 503
            assert get(px, f"/v1/fleet/nodes/{nid}")[1]["status"] == "draining"
            st, res = send(px, "POST", f"/v1/fleet/nodes/{nid}/actions/undrain", None)
            assert st == 200 and ok(res, "action-result")["text"] == \
                f"{HOSTNAME} takes new requests again"
            assert _chat(px, "back", stream=False)[0] == 200
            n = ok(get(px, f"/v1/fleet/nodes/{nid}")[1], "node")
            assert (n["status"], n["drain"]) == ("up", None)
            assert not get(px, f"/v1/fleet/nodes/{nid}/actions/undrain")[1]["allowed"]
        # with a fallback on no node (a cloud API): the new requests go there
        srv2, port2 = fake_backend.start(models=["fake-c"])
        env2 = dict(env, TANDEM_BACKENDS="eng,cloud", TANDEM_BACKEND_ENG_FALLBACK="cloud/fake-c",
                    TANDEM_BACKEND_CLOUD_KIND="openai", TANDEM_BACKEND_CLOUD_LABEL="Cloud",
                    TANDEM_BACKEND_CLOUD_URL=f"http://127.0.0.1:{port2}/v1",
                    TANDEM_BACKEND_CLOUD_KEY="sk-fixture-ABCDEFGH1234")
        try:
            with Proxy(env=env2) as px:
                px.sync()
                fleet_on(px, local_node(fake_root()))
                nid = get(px, "/v1/fleet/nodes")[1]["nodes"][0]["id"]
                plan = get(px, f"/v1/fleet/nodes/{nid}/actions/drain")[1]
                assert "fake-a: new requests go to Cloud, its route's fallback" in \
                    plan["impact"], plan
                assert send(px, "POST", f"/v1/fleet/nodes/{nid}/actions/drain", {})[0] == 200
                st, _, body = _chat(px, "elsewhere", stream=False)
                assert st == 200, body
                row = px.rows()[-1]
                assert (row["backend"], row["fallback_from"]) == ("cloud", "eng"), row
        finally:
            srv2.shutdown()
    finally:
        srv.shutdown()


def test_node_action_plans():
    with Proxy() as px:
        f = fleet_on(px, local_node(fake_root()))
        nid = get(px, "/v1/fleet/nodes")[1]["nodes"][0]["id"]
        cfg = px.svc.cfg
        # this machine read in-process: no agent, so no restart, reboot or update; never forgotten
        for action in ("restart-engine", "reboot", "update"):
            p = ok(get(px, f"/v1/fleet/nodes/{nid}/actions/{action}")[1], "action-plan")
            assert not p["allowed"] and "without its TandemNode agent" in p["blocked"], p
            assert p["confirm"] is None and p["destructive"]
            st, err = send(px, "POST", f"/v1/fleet/nodes/{nid}/actions/{action}", {})
            assert st == 409 and ok(err, "error")["error"]["type"] == "no_agent", err
        p = ok(get(px, f"/v1/fleet/nodes/{nid}/actions/forget")[1], "action-plan")
        assert not p["allowed"] and "the machine this controller runs on" in p["blocked"]
        st, err = send(px, "POST", f"/v1/fleet/nodes/{nid}/actions/release-lease", {})
        assert st == 409 and err["error"]["type"] == "not_rented", err
        # a rented record: what its release would remove; not built yet
        cfg.add_node({"id": "n-r2", "alias": "R-2", "endpoint": "", "address": "203.0.113.21",
                      "lease_end_ms": int((time.time() + 86400) * 1000), "cost_per_hour": 2.4})
        p = ok(get(px, "/v1/fleet/nodes/n-r2/actions/release-lease")[1], "action-plan")
        assert not p["allowed"] and "rental support" in p["blocked"]
        assert p["removes"] == ["the weights and caches on R-2, wiped",
                                "the node certificate, this controller's pin and the app's SSH "
                                "key on R-2", "TandemNode, uninstalled from R-2",
                                "R-2's record and its history here"], p["removes"]
        st, err = send(px, "POST", "/v1/fleet/nodes/n-r2/actions/release-lease", {})
        assert st == 501 and ok(err, "error")["error"]["type"] == "not_built"
        # forget: what goes and what stays, and only with its plan's confirm
        p = ok(get(px, "/v1/fleet/nodes/n-r2/actions/forget")[1], "action-plan")
        assert p["allowed"] and p["destructive"] and p["confirm"] and p["expires_at"]
        assert p["removes"][0] == "the record of R-2: it was never paired", p
        assert "TandemNode stays installed and running on R-2, without an owner" in p["keeps"]
        assert "weights and caches stay on R-2's disk" in p["keeps"]
        st, err = send(px, "POST", "/v1/fleet/nodes/n-r2/actions/forget", {})
        assert st == 428 and ok(err, "error")["error"]["type"] == "confirm_required"
        other = get(px, f"/v1/fleet/nodes/{nid}/actions/drain")[1]
        assert other["confirm"] is None                          # drain needs none
        for bad in ("nope", 7, get(px, "/v1/fleet/nodes/n-r2/actions/forget")[1]["confirm"] +
                    "x"):
            st, err = send(px, "POST", "/v1/fleet/nodes/n-r2/actions/forget", {"confirm": bad})
            assert st == 409 and err["error"]["type"] == "confirm_invalid", (bad, err)
        # another action's token is refused, and a token is good once
        cfg.add_node({"id": "n-x", "alias": "X", "endpoint": "", "address": "192.0.2.77"})
        theirs = get(px, "/v1/fleet/nodes/n-x/actions/forget")[1]["confirm"]
        st, err = send(px, "POST", "/v1/fleet/nodes/n-r2/actions/forget", {"confirm": theirs})
        assert st == 409 and err["error"]["type"] == "confirm_invalid"
        st, res = send(px, "POST", "/v1/fleet/nodes/n-r2/actions/forget",
                       {"confirm": p["confirm"]})
        assert st == 200 and ok(res, "action-result")["text"] == \
            "R-2 forgotten; its side: not_paired", res
        assert cfg.node("n-r2") is None and get(px, "/v1/fleet/nodes/n-r2")[0] == 404
        assert send(px, "POST", "/v1/fleet/nodes/n-r2/actions/forget",
                    {"confirm": p["confirm"]})[0] == 404
        # refused bodies and names
        for body in ([], {"confirm": "a", "force": True}):
            assert send(px, "POST", f"/v1/fleet/nodes/{nid}/actions/drain", body)[0] == 400
        st, err = get(px, f"/v1/fleet/nodes/{nid}/actions/explode")
        assert st == 404 and ok(err, "error")
        assert get(px, "/v1/fleet/nodes/n-nope/actions/drain")[0] == 404
        # the link to the node the controller runs on: no wire to test
        st, err = send(px, "POST", f"/v1/fleet/links/lan-{nid}/test", None)
        assert st == 409 and ok(err, "error")["error"]["type"] == "no_wire", err
        assert send(px, "POST", "/v1/fleet/links/nope/test", None)[0] == 404
        assert f.ops.drain_of("eng") is None


def test_start_stop():
    port = free_port()
    d = tempfile.mkdtemp(prefix="fleet-app-")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("TANDEM_", "QSE_"))}
    env.update(QSE_TEST="1", TANDEM_SECRETS_KEY="")
    p = subprocess.Popen([sys.executable, "-m", "proxy", "serve", "--port", str(port),
                          "--config-db", os.path.join(d, "config.sqlite3"),
                          "--usage-ledger", "off", "--conductor-config", "off",
                          "--no-keychain"], cwd=ROOT, env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True)
    try:
        body = None
        for _ in range(100):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/fleet/nodes",
                                            timeout=2) as r:
                    body = json.loads(r.read())
                break
            except OSError:
                if p.poll() is not None:
                    break
                time.sleep(0.1)
        assert body is not None, p.stdout.read() if p.poll() is not None else "no answer"
        ok(body, "nodes")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/fleet/layouts/chat",
                                    timeout=2) as r:
            ok(json.loads(r.read()), "layout")
        p.send_signal(signal.SIGTERM)
        assert p.wait(timeout=20) == 0
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()


if __name__ == "__main__":
    run(globals())
