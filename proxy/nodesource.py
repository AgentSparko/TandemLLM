"""Where a fleet node's figures come from (SRV-723): this machine, read in-process; a TandemNode
agent (SRV-725).

    local = LocalNode(hosts=fn, draining=fn, version=fn)
    local.identity()                 {id, alias, hw, dmi, serial, address}: what the machine says
    local.sync(backends)             the engine backends on this machine, one EngineProbe each
    local.is_node()                  a box with an NVIDIA GPU or an engine of its own
    local.sample(now, watched)       {"node": {metric: value | None}, "backends": {id: [points]}}
    local.inventory()                the contract's inventory block, read again every 10 minutes
    local.memory()                   the unified memory split in GiB, or None
    local.logs(level, q, limit)      this box's log lines (tandem-app's, its engines'), oldest first
    local.status()                   (status, status text)
    local.checks()                   the alert rules' inputs: MemAvailable, the fullest mount,
                                     the GPU's clock events (proxy/fleetwatch.py)

`AgentNode` is a node read through its TandemNode agent (proxy/nodeagent.py) with the same
methods: its one-second stream feeds the fleet's rings, sample by sample at each sample's time.
`Unreachable` stands for a node with no source yet: status `off`, no figure. The fleet
(proxy/fleet.py) reads a node only through these methods.

THIS MACHINE. The id is a hash of the DMI serial (/etc/dgx-release, or /sys/class/dmi when it is
readable), else of /etc/machine-id, else of the macOS platform UUID: the same across restarts, an
address change and a new config file, never the address. The hardware figures come from
proxy/hostcollect.py, the System view's collector, so the two never disagree; the fabric from the
ConnectX ports' own counters (an interface under /sys/class/net whose driver is mlx5_core, up).
A figure no source gives is None, never 0.

ENGINES. Each engine backend on this machine (`tandemllm`, `vllm`, `sglang`, `generic`) gets an
`EngineProbe`. A TandemLLM engine: its live snapshot (`/v1/dashboard/live?follow=0`, the
Performance view's source, a millisecond to answer), whose history holds its last 300
one-a-second samples: decode tok/s from the rise of the token count between two samples, requests
running and waiting, each request's TTFT at its end. Read every 2 s while a dashboard reads the
fleet, every 30 s otherwise (each read is a line in a verbose engine's log); the first read brings
the last five minutes. Not its /metrics page: its generation counter rises only when a request
ends, and the page takes half a second to render. vLLM and SGLang: their /metrics pages at each
sample, through proxy/engines.py's tables. Besides, a TandemLLM engine's model cards every 30 s
(the models it serves itself, apart from those it relays to a cloud), its `/v1/dashboard/system`
memory split every 10 s and its log on demand. The engine trusts a request from loopback without
proxy headers (server/auth.py), where these come from.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import os
import platform
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.parse

from proxy import engines

GIB = 1024 ** 3
ENGINE_KINDS = ("tandemllm", "vllm", "sglang", "generic")
INVENTORY_EVERY_S = 600.0
CARDS_EVERY_S = 30.0
SYSTEM_EVERY_S = 10.0
IDLE_EVERY_S = 30.0
TIMEOUT_S = 2.0

# the parts of the engine's memory split (server/dashboard_api.py) under the contract's names
WEIGHT_PARTS = ("weights", "mtp_weights", "vision", "deciders")
KV_PARTS = ("kv_pool", "drafter_kv", "gdn_state")
CACHE_PARTS = ("resident_prefix",)
LEVEL_IN = {"warning": "warn"}
LEVEL_OUT = {"warn": "warning", None: "debug"}
# a log's HTTP access lines (server/logbuf.py: source `http`) are tagged `access` in the fleet's
# log, so a page can leave them out
ACCESS = "access"


def _source(e: dict, own: str) -> str:
    return ACCESS if e.get("source") in ("http", ACCESS) else own


def _checks(avail_gib, fs, throttle) -> dict:
    """{mem_avail: GiB, disk: (free %, path) of the fullest mount, throttle: [clock events]};
    None for what the machine does not say."""
    disk = None
    for path, total, free in fs:
        if total and free is not None:
            pct = 100.0 * free / total
            if disk is None or pct < disk[0]:
                disk = (round(pct, 2), path)
    return {"mem_avail": avail_gib, "disk": disk, "throttle": throttle}


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _epoch(ts) -> float | None:
    try:
        return _dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _size(n: float) -> str:
    return f"{n / 1e12:.2f} TB" if n >= 1e12 else f"{n / 1e9:.0f} GB"


def _speed(mbps: int | None) -> str:
    if not mbps or mbps <= 0:
        return "speed unknown"
    return f"{mbps / 1000:g} GbE" if mbps >= 1000 else f"{mbps} Mb/s"


def _fetch(url: str, key: str | None = None, timeout: float = TIMEOUT_S) -> tuple[int, bytes]:
    return engines._fetch(url, key, timeout)


# ====================================================================== an engine on this machine

class EngineProbe:
    """One engine backend on this machine. Thread safe (one lock)."""

    def __init__(self, backend: dict, *, fetch=_fetch, key=None,
                 system_every: float = SYSTEM_EVERY_S):
        self.backend = backend
        self.system_every = system_every
        self.id, self.kind = backend["id"], backend["kind"]
        self.root = engines.root_of(backend["url"])
        self.fetch, self.key = fetch, key
        self._lock = threading.Lock()
        self._last: tuple | None = None          # the newest sample read: (t, tokens or page)
        self._seen: float | None = None          # when the last live snapshot was taken
        self._idle_due = -math.inf
        self.version: str | None = None
        self._cards: list[dict] | None = None
        self._cards_at = -CARDS_EVERY_S
        self._system: dict | None = None
        self._system_at = -math.inf
        self.restarts = 0                        # token counter resets seen (a restart)

    def _get(self, path: str):
        status, body = self.fetch(self.root + path, self.key, TIMEOUT_S)
        return body if status == 200 else None

    def points(self, now: float, watched: bool = True) -> list[tuple[float, dict]]:
        """[(t, {tps, req, ttft})]: the engine's figures since the last read, each at its own
        time (None where it gives none). A TandemLLM engine: its one-a-second samples, read at
        every call while a dashboard reads the fleet and every 30 s otherwise (its history holds
        the last 300, so nothing is missed, and its log gets a line per read, not per second);
        the first read brings its last five minutes. vLLM, SGLang: their /metrics now."""
        if self.kind == "tandemllm":
            with self._lock:
                if not watched and now < self._idle_due:
                    return []
                self._idle_due = now + IDLE_EVERY_S
            return self._live_points(now)
        p = self._metrics_point(now)
        return [(now, p)] if any(v is not None for v in p.values()) else []

    def _metrics_point(self, now: float) -> dict:
        out = {"tps": None, "req": None, "ttft": None}
        if self.kind not in engines.TABLES:
            return out
        try:
            body = self._get("/metrics")
        except Exception:                                            # noqa: BLE001
            body = None
        with self._lock:
            if body is None:
                self._last = None
                return out
            page = engines.map_page(self.kind, body.decode("utf-8", "replace"))
            out["req"] = engines.value(page, "requests_running")
            prev, self._last = self._last, (now, page)
        if prev is not None and now > prev[0]:
            a = engines.value(prev[1], "generation_tokens_total")
            b = engines.value(page, "generation_tokens_total")
            if a is not None and b is not None and b >= a:       # a restart resets the counter
                out["tps"] = round((b - a) / (now - prev[0]), 3)
            elif a is not None and b is not None:
                self.restarts += 1
            h = engines.hist_delta(engines.histogram(prev[1], "ttft_seconds"),
                                   engines.histogram(page, "ttft_seconds"))
            if h and h["count"] > 0:
                q = engines.quantile(h, 0.5)
                out["ttft"] = None if q is None else round(q * 1000.0, 1)
        return out

    def _live_points(self, now: float) -> list[tuple[float, dict]]:
        try:
            body = self._get("/v1/dashboard/live?follow=0")
            doc = json.loads(body) if body is not None else None
        except Exception:                                            # noqa: BLE001
            doc = None
        with self._lock:
            if not isinstance(doc, dict):
                self._last = None
                return []
            self.version = (doc.get("engine") or {}).get("version") or self.version
            at = _epoch(doc.get("generated_at")) or now
            rows = [x for x in (doc.get("history") or []) + [doc.get("sample")]
                    if isinstance(x, dict) and _num(x.get("t")) is not None
                    and _num(x.get("tokens")) is not None]
            last, seen = self._last, self._seen
            out = []
            for x in sorted({x["t"]: x for x in rows}.values(), key=lambda x: x["t"]):
                t, n = x["t"], x["tokens"]
                if last is not None and t <= last[0]:
                    continue
                tps = round((n - last[1]) / (t - last[0]), 3) if last is not None and \
                    n >= last[1] else None                       # a restart counts from 0 again
                if last is not None and n < last[1]:
                    self.restarts += 1
                out.append((t, {"tps": tps, "req": (_num(x.get("running")) or 0)
                                + (_num(x.get("waiting")) or 0), "ttft": None}))
                last = (t, n)
            if not out and last is not None and \
                    not _num((doc.get("counts") or {}).get("in_flight")):
                out.append((at, {"tps": 0.0, "req": 0.0, "ttft": None}))   # its sampler rests
            for r in doc.get("recent") or []:
                if not isinstance(r, dict) or _num(r.get("ttft_ms")) is None or \
                        _num(r.get("ended_ms_ago")) is None:
                    continue
                end = at - r["ended_ms_ago"] / 1000.0
                if seen is None or end > seen:
                    out.append((end, {"tps": None, "req": None, "ttft": float(r["ttft_ms"])}))
            self._last, self._seen = last, at
        return out

    def cards(self, now: float) -> list[dict] | None:
        """The engine's own models from its model list: [{id, name, model, pack, ctx, loaded}];
        None before the first answer. A TandemLLM engine's card says what it serves itself
        (`tandem.kind` engine) and what it relays (a cloud model): only the first count."""
        with self._lock:
            if now - self._cards_at < CARDS_EVERY_S:
                return self._cards
            self._cards_at = now
        try:
            body = self._get("/v1/models")
            data = json.loads(body).get("data") if body is not None else None
        except Exception:                                            # noqa: BLE001
            data = None
        if not isinstance(data, list):
            return self._cards
        out = []
        for m in data:
            if not isinstance(m, dict) or not isinstance(m.get("id"), str):
                continue
            t = m.get("tandem") if isinstance(m.get("tandem"), dict) else None
            if t is not None and (t.get("cloud") or t.get("kind") not in (None, "engine")):
                continue
            t = t or {}
            out.append({"id": m["id"], "name": t.get("label") or m["id"],
                        "model": t.get("model") or m["id"], "pack": t.get("pack") or "",
                        "ctx": t.get("context_length") or m.get("context_length")
                        or m.get("max_model_len"), "loaded": t.get("loaded", True)})
        with self._lock:
            self._cards = out
        return out

    def system(self, now: float) -> dict | None:
        """A TandemLLM engine's `/v1/dashboard/system` memory block and limits, every 10 s (the
        engine takes 0.1 to 0.6 s to answer it; an always-on reader asks less often)."""
        if self.kind != "tandemllm":
            return None
        with self._lock:
            if now - self._system_at < self.system_every:
                return self._system
            self._system_at = now
        try:
            body = self._get("/v1/dashboard/system")
            doc = json.loads(body) if body is not None else None
        except Exception:                                            # noqa: BLE001
            doc = None
        with self._lock:
            self._system = doc if isinstance(doc, dict) else None
            return self._system

    def logs(self, level: str | None, q: str | None, limit: int) -> list[dict]:
        """A TandemLLM engine's log lines (`GET /v1/dashboard/logs?follow=0`), source `engine`."""
        if self.kind != "tandemllm":
            return []
        qs = {"follow": "0", "backlog": str(limit), "level": LEVEL_OUT.get(level, level)}
        if q:
            qs["grep"] = q
        try:
            body = self._get("/v1/dashboard/logs?" + urllib.parse.urlencode(qs))
            lines = json.loads(body).get("lines") if body is not None else None
        except Exception:                                            # noqa: BLE001
            lines = None
        return [{"t": _epoch(e.get("ts")), "level": LEVEL_IN.get(e.get("level"), e.get("level")),
                 "source": _source(e, "engine"), "msg": str(e.get("msg") or "")}
                for e in lines or [] if isinstance(e, dict)]


# ====================================================================== this machine

class LocalNode:
    """The machine tandem-app runs on, as a fleet node."""

    endpoint = "local"

    def __init__(self, *, hosts, draining=lambda: False, version=lambda: "", logbuf=None,
                 fetch=_fetch, key=lambda bid: None, root: str = "/", run=None,
                 address=None, clock=time.time, system_every: float = SYSTEM_EVERY_S):
        self.hosts = hosts                   # () -> this machine's hostcollect entry, or None
        self.draining, self.version_of = draining, version
        self.logbuf = logbuf
        self.fetch, self.key = fetch, key
        self.root = root
        self.run = run or _run
        self._address = address
        self.clock = clock
        self.system_every = system_every
        self.probes: dict[str, EngineProbe] = {}
        self._lock = threading.Lock()
        self._ident: dict | None = None
        self._inv: dict | None = None
        self._inv_at = -INVENTORY_EVERY_S
        self._fabric_prev: tuple[float, int] | None = None
        self._last_host: dict | None = None

    def _p(self, *parts: str) -> str:
        return os.path.join(self.root, *parts)

    def _read(self, *parts: str) -> str | None:
        try:
            with open(self._p(*parts)) as f:
                return f.read().strip()
        except OSError:
            return None

    def _kv(self, *parts: str) -> dict:
        out = {}
        for line in (self._read(*parts) or "").splitlines():
            k, sep, v = line.partition("=")
            if sep:
                out[k.strip()] = v.strip().strip('"')
        return out

    # ------------------------------------------------------------------ who it is
    def identity(self) -> dict:
        if self._ident is None:
            dgx = self._kv("etc", "dgx-release")
            serial = dgx.get("DGX_SERIAL_NUMBER") or self._read("sys", "class", "dmi", "id",
                                                                "product_serial") or None
            vendor = self._read("sys", "class", "dmi", "id", "sys_vendor")
            product = self._read("sys", "class", "dmi", "id", "product_name")
            if sys.platform == "darwin" and not product:
                product = (self.run(["sysctl", "-n", "hw.model"]) or "").strip() or None
                vendor = vendor or "Apple"
            machine = self._read("etc", "machine-id") or self._mac_uuid()
            seed = f"serial:{serial}" if serial else f"machine:{machine or socket.gethostname()}"
            name = socket.gethostname().split(".")[0] or "this box"
            self._ident = {"id": "n-" + hashlib.sha256(seed.encode()).hexdigest()[:12],
                           "alias": name, "serial": serial,
                           "hw": dgx.get("DGX_PRETTY_NAME") or product or platform.machine(),
                           "dmi": " ".join(x for x in (vendor, product) if x) or "unknown"}
        return dict(self._ident, address=self.address())

    def _mac_uuid(self) -> str | None:
        if sys.platform != "darwin":
            return None
        m = re.search(r'"IOPlatformUUID" = "([^"]+)"',
                      self.run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"]) or "")
        return m.group(1) if m else None

    def address(self) -> str:
        """This machine's address on the network: the source address of its default route
        (a UDP socket's connect sends nothing)."""
        if self._address is not None:
            return self._address()
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("192.0.2.1", 9))
                return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"

    # ------------------------------------------------------------------ what it runs
    def sync(self, backends: list[dict]) -> None:
        """The engine backends on this machine; a probe per backend, a new one when its URL or
        kind changed."""
        with self._lock:
            keep = {}
            for b in backends:
                p = self.probes.get(b["id"])
                if p is None or p.backend["url"] != b["url"] or p.kind != b["kind"]:
                    try:
                        key = self.key(b["id"])
                    except Exception:                                # noqa: BLE001
                        key = None
                    p = EngineProbe(b, fetch=self.fetch, key=key, system_every=self.system_every)
                keep[b["id"]] = p
            self.probes = keep

    def engines(self) -> list[EngineProbe]:
        with self._lock:
            return list(self.probes.values())

    def host(self) -> dict | None:
        try:
            h = self.hosts()
        except Exception:                                            # noqa: BLE001
            h = None
        if h is not None:
            self._last_host = h
        return h

    def is_node(self) -> bool:
        """A Spark-class box: an NVIDIA GPU, or an engine of its own. A Mac in front of cloud
        APIs only is the controller's host, not a node."""
        if self.engines():
            return True
        h = self.host()
        return bool(h and h.get("gpus"))

    def status(self) -> tuple[str, str]:
        if self.draining():
            return "draining", "draining: no new requests"
        return "up", "up"

    def version(self) -> str:
        for p in self.engines():
            if p.version:
                return p.version
        return self.version_of() or ""

    # ------------------------------------------------------------------ figures
    def sample(self, now: float, watched: bool = True) -> dict:
        """{"node": the hardware figures now, "backends": {id: [(t, {tps, req, ttft})]}}; the
        node's decode rate, requests and TTFT are its engines' (the fleet adds them up)."""
        return {"node": self.figures(now, self.host() or {}),
                "backends": {p.id: p.points(now, watched) for p in self.engines()}}

    def figures(self, now: float, h: dict) -> dict:
        """The fleet's hardware keys from one host collector entry (TandemNode's stream carries
        the same, proxy/nodemetrics.py)."""
        gpus = [g for g in h.get("gpus") or [] if isinstance(g, dict)]

        def col(k, scale=1.0):
            return [v * scale for v in (_num(g.get(k)) for g in gpus) if v is not None]

        def mean(xs):
            return round(sum(xs) / len(xs), 3) if xs else None
        mem = h.get("memory") or {}
        cpu = h.get("cpu") or {}
        fabric_if = self.fabric_ports()
        disks = [r for r in h.get("disks") or [] if r.get("read_bps") is not None
                 or r.get("write_bps") is not None]
        nets = [r for r in h.get("net") or [] if r.get("name") not in fabric_if
                and (r.get("rx_bps") is not None or r.get("tx_bps") is not None)]
        used = _num(mem.get("used_bytes"))
        util = _num(cpu.get("utilization"))
        return {
            "gpu": mean(col("utilization", 100.0)),
            "power": round(sum(col("power_w")), 2) if col("power_w") else None,
            "temp": max(col("temperature_c")) if col("temperature_c") else None,
            "sm": mean(col("sm_clock_mhz")),
            "memclk": mean(col("mem_clock_mhz")),
            "mem": None if used is None else round(used / GIB, 3),
            "cpu": None if util is None else round(util * 100.0, 2),
            "disk": round(sum((r.get("read_bps") or 0) + (r.get("write_bps") or 0)
                              for r in disks) / 1e6, 3) if disks else None,
            "lan": round(sum((r.get("rx_bps") or 0) + (r.get("tx_bps") or 0)
                             for r in nets) / 1e6, 3) if nets else None,
            "fabric": self._fabric_rate(now, fabric_if),
            "rtt": None,                     # the controller runs here: no link to time
        }

    def fabric_ports(self) -> dict:
        """{interface: up} of the ConnectX ports (driver mlx5_core)."""
        base = self._p("sys", "class", "net")
        out = {}
        try:
            names = sorted(os.listdir(base))
        except OSError:
            return out
        for n in names:
            try:
                drv = os.path.basename(os.readlink(os.path.join(base, n, "device", "driver")))
            except OSError:
                continue
            if drv.startswith("mlx5"):
                out[n] = self._read("sys", "class", "net", n, "operstate") == "up"
        return out

    def _fabric_rate(self, now: float, ports: dict) -> float | None:
        """Gb/s in and out over the fabric ports that are up; None with none up."""
        up = [n for n, ok in ports.items() if ok]
        total = 0
        for n in up:
            for f in ("rx_bytes", "tx_bytes"):
                v = self._read("sys", "class", "net", n, "statistics", f)
                total += int(v) if v and v.isdigit() else 0
        prev, self._fabric_prev = self._fabric_prev, (now, total) if up else None
        if not up or prev is None or now <= prev[0] or total < prev[1]:
            return None
        return round((total - prev[1]) * 8 / (now - prev[0]) / 1e9, 4)

    def lan(self) -> dict | None:
        """The interface of the default route: {iface, speed, mtu, state}, None without one."""
        iface = None
        for line in (self._read("proc", "net", "route") or "").splitlines()[1:]:
            p = line.split()
            if len(p) > 2 and p[1] == "00000000":
                iface = p[0]
                break
        if iface is None:
            return None
        sp = self._read("sys", "class", "net", iface, "speed")
        mtu = self._read("sys", "class", "net", iface, "mtu")
        state = self._read("sys", "class", "net", iface, "operstate")
        return {"iface": iface, "speed": _speed(int(sp) if sp and sp.lstrip("-").isdigit()
                                                else None),
                "mtu": int(mtu) if mtu and mtu.isdigit() else None,
                "state": "up" if state in ("up", "unknown") else "down"}

    def inventory(self) -> dict:
        now = self.clock()
        with self._lock:
            if self._inv is not None and now - self._inv_at < INVENTORY_EVERY_S:
                return self._inv
        dgx = self._kv("etc", "dgx-release")
        osr = self._kv("etc", "os-release")
        os_name = osr.get("PRETTY_NAME")
        dgx_ver = dgx.get("DGX_OTA_VERSION") or dgx.get("DGX_SWBUILD_VERSION")
        if dgx_ver:
            os_name = f"DGX OS {dgx_ver}" + (f" ({os_name})" if os_name else "")
        if sys.platform == "darwin" and not os_name:
            os_name = f"macOS {platform.mac_ver()[0]}"
        smi = self.run(["nvidia-smi"]) or ""
        drv = re.search(r"Kernel Module(?: for \S+)?\s+([\d.]+)",
                        self._read("proc", "driver", "nvidia", "version") or "")
        drv2 = re.search(r"Driver Version:\s*([\d.]+)", smi)
        cuda = re.search(r"CUDA Version:\s*([\d.]+)", smi)
        ports = self.fabric_ports()
        h = self._last_host or self.host() or {}
        root_fs = next((f for f in h.get("fs") or [] if f.get("path") == "/"), None)
        nvme = any(d.get("nvme") for d in h.get("disks") or [])
        inv = {"serial": self.identity()["serial"], "os": os_name or None,
               "kernel": platform.release() or None,
               "driver": (drv or drv2).group(1) if (drv or drv2) else None,
               "cuda": cuda.group(1) if cuda else None,
               "cx7": f"{len(ports)} ConnectX port{'s' if len(ports) != 1 else ''} · "
                      f"{sum(ports.values())} up" if ports else None,
               "disk": f"{'NVMe ' if nvme else ''}{_size(root_fs['total_bytes'])} · "
                       f"{_size(root_fs['free_bytes'])} free" if root_fs else None,
               "nics": self._nics()}
        with self._lock:
            self._inv, self._inv_at = inv, now
        return inv

    def _nics(self) -> str | None:
        """The physical interfaces (those with a device behind them), speed and state."""
        base = self._p("sys", "class", "net")
        try:
            names = sorted(os.listdir(base))
        except OSError:
            return None
        out = []
        for n in names:
            if not os.path.exists(os.path.join(base, n, "device")):
                continue
            state = self._read("sys", "class", "net", n, "operstate") or "unknown"
            sp = self._read("sys", "class", "net", n, "speed")
            speed = _speed(int(sp)) if state == "up" and sp and sp.lstrip("-").isdigit() else None
            out.append(f"{n} ({speed + ', ' if speed else ''}{state})")
        return " · ".join(out) or None

    def checks(self) -> dict:
        """The alert rules' inputs from the newest host reading (no clock events in-process)."""
        h = self._last_host or self.host() or {}
        avail = _num((h.get("memory") or {}).get("available_bytes"))
        return _checks(None if avail is None else round(avail / GIB, 2),
                       [(f.get("path"), _num(f.get("total_bytes")), _num(f.get("free_bytes")))
                        for f in h.get("fs") or [] if isinstance(f, dict)], None)

    def usable(self) -> float | None:
        """The machine's memory, GiB (MemTotal: on a GB10 the unified pool)."""
        total = _num(((self._last_host or self.host() or {}).get("memory") or {})
                     .get("total_bytes"))
        return None if total is None else round(total / GIB, 2)

    def memory(self, now: float, h: dict | None = None) -> dict | None:
        """{weights, kv, cache, other, page, ceiling, usable} in GiB: the engines' own split
        (TandemLLM's `/v1/dashboard/system`), the rest of what the machine uses as `other`, the
        page cache apart; None without a memory reading. `h`: a host entry already read."""
        h = h if h is not None else (self.host() or {})
        mem = h.get("memory") or {}
        total, used = _num(mem.get("total_bytes")), _num(mem.get("used_bytes"))
        if total is None or used is None:
            return None
        weights = kv = cache = 0.0
        ceiling = None
        for p in self.engines():
            doc = p.system(now) or {}
            sp = (doc.get("memory") or {}).get("split") or {}
            parts = sp.get("parts") or {}
            weights += sum(_num(parts.get(k)) or 0.0 for k in WEIGHT_PARTS)
            kv += sum(_num(parts.get(k)) or 0.0 for k in KV_PARTS)
            cache += sum(_num(parts.get(k)) or 0.0 for k in CACHE_PARTS) + \
                (_num(sp.get("pinned_cache")) or 0.0) + (_num(sp.get("drafter_caches")) or 0.0)
            for r in ((doc.get("memory") or {}).get("limits") or {}).get("rows") or []:
                if r.get("key") == "MEM_CEILING_GIB" and _num(r.get("running")) is not None:
                    ceiling = (ceiling or 0.0) + r["running"] * GIB
        page = 0.0
        for line in (self._read("proc", "meminfo") or "").splitlines():
            k, _, v = line.partition(":")
            if k in ("Cached", "Buffers") and v.split() and v.split()[0].isdigit():
                page += int(v.split()[0]) * 1024
        g = lambda b: round(b / GIB, 2)                              # noqa: E731
        return {"weights": g(weights), "kv": g(kv), "cache": g(cache),
                "other": g(max(0.0, used - weights - kv - cache)), "page": g(page),
                "ceiling": g(ceiling if ceiling is not None else total), "usable": g(total)}

    def logs(self, level: str | None, q: str | None, limit: int) -> list[dict]:
        """tandem-app's own log (source `gateway`) and its TandemLLM engines' (`engine`), merged
        by time, the last `limit`. `q` is a substring, case as written (the Dev log's filter)."""
        out = []
        if self.logbuf is not None:
            for e in self.logbuf.lines(level=LEVEL_OUT.get(level, level), grep=q or None,
                                       limit=limit):
                out.append({"t": _epoch(e.get("ts")), "level": LEVEL_IN.get(e["level"],
                                                                            e["level"]),
                            "source": _source(e, "gateway"), "msg": e["msg"]})
        for p in self.engines():
            out += p.logs(level, q, limit)
        out = [x for x in out if x["t"] is not None and x["level"] in
               ("debug", "info", "warn", "error")]
        out.sort(key=lambda x: x["t"])
        return out[-limit:]


# ====================================================================== a node's agent

FLEET_KEYS = ("gpu", "power", "temp", "sm", "memclk", "mem", "cpu", "disk", "lan", "fabric")
IDENTITY_EVERY_S = 60.0
ENGINES_EVERY_S = 30.0
STALE_S = 5.0


class AgentNode:
    """A node read through its TandemNode agent (SRV-725), at `url` with a client (proxy/
    nodeclient.py: the loopback secret, or the pinned mTLS of pairing).

    The metrics stream is opened at the first sample and kept open (reconnecting after 2 s, up to
    30 s between tries); every sample it brings goes into the fleet's rings at its own second. The
    identity is read every minute (its round trip is the node's `rtt`), the inventory every ten,
    the agent's engine list every 30 s. Each engine backend on the node is read through the
    agent's relay of the engine's own routes (`/node/engines/{id}/...`), so the controller needs no
    route to the engine's port; a backend whose port the agent does not list is read directly."""

    def __init__(self, url: str, *, client=None, secret=None, ssl_context=None, clock=time.time,
                 draining=lambda: False, logbuf=None, reconnect_s: float = 2.0):
        from proxy import nodeclient
        self.endpoint = url
        self.client = client or nodeclient.Client(url, secret=secret, ssl_context=ssl_context)
        self.clock, self.draining, self.logbuf = clock, draining, logbuf
        self.reconnect_s = reconnect_s
        self.probes: dict[str, EngineProbe] = {}
        self._lock = threading.Lock()
        self._buf: list = []
        self._latest: dict | None = None
        self._seen: float | None = None          # when the last sample arrived (the clock)
        self._error: str | None = None
        self._thread: threading.Thread | None = None
        self._halt = threading.Event()
        self._conn = None
        self._ident: dict | None = None
        self._ident_at = -math.inf
        self._ident_err: str | None = None
        self.rtt: float | None = None
        self._inv: dict | None = None
        self._inv_at = -INVENTORY_EVERY_S
        self._eng: list | None = None
        self._eng_at = -ENGINES_EVERY_S

    # ------------------------------------------------------------------ the stream
    def _ensure_stream(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._halt.clear()
            self._thread = threading.Thread(target=self._pump, name="agent-metrics", daemon=True)
            self._thread.start()

    def _pump(self) -> None:
        wait = self.reconnect_s
        while not self._halt.is_set():
            try:
                for ev, data in self.client.events("/node/metrics", timeout=15.0,
                                                   on_open=self._opened):
                    if ev == "sample" and isinstance(data, dict) and _num(data.get("t")):
                        with self._lock:
                            self._buf.append(data)
                            del self._buf[:-900]
                            self._latest, self._seen, self._error = data, self.clock(), None
                        wait = self.reconnect_s
                    if self._halt.is_set():
                        return
                err = "its metrics stream ended"
            except Exception as exc:                                 # noqa: BLE001
                err = f"{type(exc).__name__}: {exc}"[:160]
            with self._lock:
                self._error = err
            if self._halt.wait(wait):
                return
            wait = min(30.0, wait * 2)

    def latest(self) -> dict | None:
        """The newest one-second sample the stream brought, as the agent sent it; None before
        the first."""
        self._ensure_stream()
        with self._lock:
            return None if self._latest is None else dict(self._latest)

    def _opened(self, conn) -> None:
        self._conn = conn

    def close(self) -> None:
        self._halt.set()
        c = self._conn
        if c is not None:
            try:
                c.close()
            except Exception:                                        # noqa: BLE001
                pass

    # ------------------------------------------------------------------ who it is
    def _identity(self) -> dict | None:
        now = self.clock()
        if self._ident is not None and now - self._ident_at < IDENTITY_EVERY_S:
            return self._ident
        t0 = time.perf_counter()
        try:
            st, doc = self.client.json("GET", "/node/identity", timeout=TIMEOUT_S)
        except Exception as exc:                                     # noqa: BLE001
            st, doc = 0, None
            self._ident_err = f"{type(exc).__name__}: {exc}"[:160]
        self._ident_at = now
        if st == 200 and isinstance(doc, dict) and isinstance(doc.get("id"), str):
            self.rtt = round((time.perf_counter() - t0) * 1000.0, 2)
            self._ident, self._ident_err = doc, None
        elif st:
            self._ident_err = f"HTTP {st}"
        return self._ident

    def identity(self) -> dict | None:
        d = self._identity()
        if d is None:
            return None
        return {"id": d["id"], "alias": d.get("hostname") or d["id"], "serial": d.get("serial"),
                "hw": d.get("hw") or "unknown", "dmi": d.get("dmi") or "unknown",
                "address": d.get("address") or self.client.host}

    def alive(self) -> bool:
        """The agent answered its identity at the last try."""
        self._identity()
        return self._ident is not None and self._ident_err is None

    def healthy(self) -> bool:
        """A sample in the last 5 s; before the first, an identity answer and no stream error."""
        self._ensure_stream()
        with self._lock:
            fresh = self._seen is not None and self.clock() - self._seen <= STALE_S
            err = self._error
        return fresh or (err is None and self.alive())

    def address(self) -> str:
        return (self._ident or {}).get("address") or self.client.host

    def is_node(self) -> bool:
        return True

    def status(self) -> tuple[str, str]:
        self._ensure_stream()
        if self.draining():
            return "draining", "draining: no new requests"
        if self.healthy():
            return "up", "up"
        return "off", refusal(self._error or self._ident_err)

    def version(self) -> str:
        for p in self.engines():
            if p.version:
                return p.version
        return (self._identity() or {}).get("tandemllm") or ""

    # ------------------------------------------------------------------ what it runs
    def agent_engines(self) -> list[dict]:
        now = self.clock()
        if self._eng is None or now - self._eng_at >= ENGINES_EVERY_S:
            self._eng_at = now
            try:
                st, doc = self.client.json("GET", "/node/engines", timeout=TIMEOUT_S)
                if st == 200 and isinstance(doc, dict):
                    self._eng = [e for e in doc.get("engines") or [] if isinstance(e, dict)]
            except Exception:                                        # noqa: BLE001
                pass
        return self._eng or []

    def sync(self, backends: list[dict]) -> None:
        by_port = {e.get("port"): e.get("id") for e in self.agent_engines()}
        with self._lock:
            keep = {}
            for b in backends:
                try:
                    port = urllib.parse.urlsplit(b["url"]).port
                except ValueError:
                    port = None
                eid = by_port.get(port)
                p = self.probes.get(b["id"])
                if p is None or p.backend["url"] != b["url"] or p.kind != b["kind"] or \
                        getattr(p, "via", None) != eid:
                    p = EngineProbe(b, fetch=self._relay(eid) if eid else _fetch)
                    p.via = eid
                keep[b["id"]] = p
            self.probes = keep

    def _relay(self, eid: str):
        def fetch(url: str, key=None, timeout: float = TIMEOUT_S) -> tuple[int, bytes]:
            u = urllib.parse.urlsplit(url)
            path = u.path + (f"?{u.query}" if u.query else "")
            return self.client.raw("GET", f"/node/engines/{urllib.parse.quote(eid)}{path}",
                                   timeout=timeout)
        return fetch

    def engines(self) -> list[EngineProbe]:
        with self._lock:
            return list(self.probes.values())

    # ------------------------------------------------------------------ figures
    def sample(self, now: float, watched: bool = True) -> dict:
        """The node's samples since the last call, each at its own second (`node_points`), the
        newest as `node`; the engines' points as LocalNode gives them."""
        self._ensure_stream()
        with self._lock:
            got, self._buf = self._buf, []
        pts = [(s["t"], self._keys(s)) for s in got]
        return {"node": pts[-1][1] if pts else {}, "node_points": pts,
                "backends": {p.id: p.points(now, watched) for p in self.engines()}}

    def _keys(self, s: dict) -> dict:
        n = s.get("node") or {}
        out = {k: _num(n.get(k)) for k in FLEET_KEYS}
        out["rtt"] = self.rtt
        return out

    def _inventory(self) -> dict:
        now = self.clock()
        if self._inv is None or now - self._inv_at >= INVENTORY_EVERY_S:
            try:
                st, doc = self.client.json("GET", "/node/inventory", timeout=TIMEOUT_S * 3)
                if st == 200 and isinstance(doc, dict):
                    self._inv, self._inv_at = doc, now
            except Exception:                                        # noqa: BLE001
                pass
        return self._inv or {}

    def inventory(self) -> dict:
        f = self._inventory().get("fleet") or {}
        return {k: f.get(k) for k in ("serial", "os", "kernel", "driver", "cuda", "cx7", "disk",
                                      "nics")}

    def lan(self) -> dict | None:
        return self._inventory().get("lan")

    def usable(self) -> float | None:
        with self._lock:
            m = (self._latest or {}).get("memory") or {}
        return _num(m.get("total"))

    def checks(self) -> dict:
        """The alert rules' inputs from the newest sample of the stream (none while it is stale:
        an off node's last figures are no reading)."""
        with self._lock:
            s = self._latest if self._seen is not None and \
                self.clock() - self._seen <= STALE_S else None
        if s is None:
            return _checks(None, [], None)
        gpus = [g for g in s.get("gpus") or [] if isinstance(g, dict)]
        said = [g for g in gpus if "throttle" in g or "throttled" in g]
        return _checks(_num((s.get("memory") or {}).get("available")),
                       [(f.get("path"), _num(f.get("total_gb")), _num(f.get("free_gb")))
                        for f in (s.get("disks") or {}).get("fs") or [] if isinstance(f, dict)],
                       [x for g in said for x in g.get("throttle") or []] if said else None)

    def memory(self, now: float) -> dict | None:
        with self._lock:
            m = (self._latest or {}).get("memory") or {}
        if _num(m.get("total")) is None or _num(m.get("other")) is None:
            return None
        return {"weights": m.get("weights", 0.0), "kv": m.get("kv", 0.0),
                "cache": m.get("cache", 0.0), "other": m["other"], "page": m.get("page", 0.0),
                "ceiling": m.get("ceiling", m["total"]), "usable": m["total"]}

    def logs(self, level: str | None, q: str | None, limit: int) -> list[dict]:
        """The agent's lines (source `agent`) and its engines' (`engine`); on the controller's
        own box tandem-app's too (`gateway`), merged by time."""
        qs = {"follow": "0", "backlog": str(limit), "level": level or "debug"}
        if q:
            qs["q"] = q
        out = []
        try:
            st, doc = self.client.json("GET", "/node/logs?" + urllib.parse.urlencode(qs),
                                       timeout=TIMEOUT_S * 3)
        except Exception:                                            # noqa: BLE001
            st, doc = 0, None
        if st == 200 and isinstance(doc, dict):
            for e in doc.get("lines") or []:
                if isinstance(e, dict) and _num(e.get("t")) is not None:
                    out.append({"t": e["t"], "level": e.get("level"),
                                "source": e["source"] if e.get("source") in ("agent", ACCESS)
                                else "engine", "msg": str(e.get("msg") or "")})
        if self.logbuf is not None:
            for e in self.logbuf.lines(level=LEVEL_OUT.get(level, level), grep=q or None,
                                       limit=limit):
                out.append({"t": _epoch(e.get("ts")), "level": LEVEL_IN.get(e["level"],
                                                                            e["level"]),
                            "source": _source(e, "gateway"), "msg": e["msg"]})
        out = [x for x in out if x["t"] is not None and x["level"] in
               ("debug", "info", "warn", "error")]
        out.sort(key=lambda x: x["t"])
        return out[-limit:]


def agent_for(row: dict, clock=time.time):
    """The source of a node record whose endpoint is an agent: the trust it names, else
    Unreachable. `{"kind": "loopback-secret", "file": path}` for an agent on this machine's
    loopback; `{"kind": "mtls", "cert": path, "key": path, "pin": PEM}` (the controller's own
    certificate and the node's pinned one, written by pairing, SEC-4)."""
    from proxy import nodetrust
    t = row.get("trust") or {}
    ep = row["endpoint"]
    host = urllib.parse.urlsplit(ep).hostname or ""
    kind = t.get("kind")
    try:
        if kind == "loopback-secret" and ep.startswith("http://") and \
                nodetrust.parse_ip(host).is_loopback and t.get("file"):
            f = t["file"]
            return AgentNode(ep, secret=lambda: nodetrust.load_secret(f), clock=clock)
        if kind == "mtls" and ep.startswith("https://") and t.get("pin"):
            ctx = nodetrust.client_context(t.get("cert"), t.get("key"), t["pin"])
            return AgentNode(ep, ssl_context=ctx, clock=clock)
    except (nodetrust.BindRefused, nodetrust.TrustError, OSError, ValueError) as exc:
        return Unreachable(f"not reachable: its trust does not load ({exc})")
    return Unreachable("not paired: no trust for its agent yet (SEC-4)")


def refusal(err: str | None) -> str:
    """Why an agent does not answer, in words: a TLS refusal names its side (SEC-4)."""
    e = (err or "").upper()
    if any(x in e for x in ("ALERT_UNKNOWN_CA", "ALERT_BAD_CERTIFICATE", "CERTIFICATE_REQUIRED",
                            "ALERT_CERTIFICATE_UNKNOWN")):
        return ("the node refuses this controller's certificate: it was released or taken over "
                "on the node")
    if "CERTIFICATE_VERIFY_FAILED" in e:
        return ("the node presents a certificate other than the pinned one (reset on the node?): "
                "pair it again")
    return f"the node agent does not answer: {err}"


class Unreachable:
    """A node with no source: no agent endpoint yet, or one the controller has no trust for
    (SEC-4). `off`, every figure None."""

    endpoint = None

    def __init__(self, why: str = "not reachable: no node agent yet"):
        self.why = why

    def status(self) -> tuple[str, str]:
        return "off", self.why

    def version(self) -> str:
        return ""

    def inventory(self) -> dict:
        return {k: None for k in ("serial", "os", "kernel", "driver", "cuda", "cx7", "disk",
                                  "nics")}

    def lan(self) -> None:
        return None

    def usable(self) -> None:
        return None

    def sample(self, now: float, watched: bool = True) -> dict:
        return {"node": {}, "backends": {}}

    def memory(self, now: float) -> None:
        return None

    def checks(self) -> dict:
        return _checks(None, [], None)

    def logs(self, level, q, limit) -> list[dict]:
        return []

    def engines(self) -> list:
        return []


def _run(argv: list[str]) -> str | None:
    """A probe command's stdout, None when it is missing, fails or takes over 5 s."""
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None
