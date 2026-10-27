"""Where a fleet node's figures come from (SRV-723): this machine, read in-process; a TandemNode
agent later (SRV-725).

    local = LocalNode(hosts=fn, draining=fn, version=fn)
    local.identity()                 {id, alias, hw, dmi, serial, address}: what the machine says
    local.sync(backends)             the engine backends on this machine, one EngineProbe each
    local.is_node()                  a box with an NVIDIA GPU or an engine of its own
    local.sample(now, watched)       {"node": {metric: value | None}, "backends": {id: [points]}}
    local.inventory()                the contract's inventory block, read again every 10 minutes
    local.memory()                   the unified memory split in GiB, or None
    local.logs(level, q, limit)      this box's log lines (tandem-app's, its engines'), oldest first
    local.status()                   (status, status text)

`Unreachable` stands for a node with no source yet (a remote Spark before its agent): status
`off`, no figure. The fleet (proxy/fleet.py) reads a node only through these methods, so the
agent's client (SRV-725, over the pinned mTLS of SEC-4) is one more class beside these two.

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

    def __init__(self, backend: dict, *, fetch=_fetch, key=None):
        self.backend = backend
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
        self._system_at = -SYSTEM_EVERY_S

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
        """A TandemLLM engine's `/v1/dashboard/system` memory block and limits, every 10 s."""
        if self.kind != "tandemllm":
            return None
        with self._lock:
            if now - self._system_at < SYSTEM_EVERY_S:
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
                 "source": "engine", "msg": str(e.get("msg") or "")}
                for e in lines or [] if isinstance(e, dict)]


# ====================================================================== this machine

class LocalNode:
    """The machine tandem-app runs on, as a fleet node."""

    endpoint = "local"

    def __init__(self, *, hosts, draining=lambda: False, version=lambda: "", logbuf=None,
                 fetch=_fetch, key=lambda bid: None, root: str = "/", run=None,
                 address=None, clock=time.time):
        self.hosts = hosts                   # () -> this machine's hostcollect entry, or None
        self.draining, self.version_of = draining, version
        self.logbuf = logbuf
        self.fetch, self.key = fetch, key
        self.root = root
        self.run = run or _run
        self._address = address
        self.clock = clock
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
                    p = EngineProbe(b, fetch=self.fetch, key=key)
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
        h = self.host() or {}
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
        node = {
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
        return {"node": node, "backends": {p.id: p.points(now, watched) for p in self.engines()}}

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

    def usable(self) -> float | None:
        """The machine's memory, GiB (MemTotal: on a GB10 the unified pool)."""
        total = _num(((self._last_host or self.host() or {}).get("memory") or {})
                     .get("total_bytes"))
        return None if total is None else round(total / GIB, 2)

    def memory(self, now: float) -> dict | None:
        """{weights, kv, cache, other, page, ceiling, usable} in GiB: the engines' own split
        (TandemLLM's `/v1/dashboard/system`), the rest of what the machine uses as `other`, the
        page cache apart; None without a memory reading."""
        h = self.host() or {}
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
                            "source": "gateway", "msg": e["msg"]})
        for p in self.engines():
            out += p.logs(level, q, limit)
        out = [x for x in out if x["t"] is not None and x["level"] in
               ("debug", "info", "warn", "error")]
        out.sort(key=lambda x: x["t"])
        return out[-limit:]


class Unreachable:
    """A node whose source does not exist yet (a remote Spark before its TandemNode agent,
    SRV-725): `off`, every figure None."""

    endpoint = None

    def __init__(self, why: str = "not reachable: no node agent yet"):
        self.why = why

    def status(self) -> tuple[str, str]:
        return "off", self.why

    def sample(self, now: float, watched: bool = True) -> dict:
        return {"node": {}, "backends": {}}

    def memory(self, now: float) -> None:
        return None

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
