"""TandemNode's one-second sample of its machine (SRV-725, the concept's metrics list).

    reader = Reader(local, collector)        local: a nodesource.LocalNode over the collector
    reader.sample(now)                       one sample, every field the board does not expose absent
    sampler = Sampler(reader.sample)         one thread while someone listens, one sample a second
    sub = sampler.subscribe(); sub.take(t)   each listener's own queue; sampler.unsubscribe(sub)

A sample, each block and field present only when its source answers (never 0 or a guess for a
sensor the board lacks; a GB10 has no fan reading and no GPU memory of its own):

    t          the second it stands for (Unix seconds, on the whole second)
    node       the fleet's keys, as LocalNode computes them for the controller (gpu %, power W,
               temp °C, sm/memclk MHz, mem GiB, cpu %, disk MB/s, lan MB/s, fabric Gb/s)
    gpus       per device: util %, SM and memory clocks, power and its cap, temperature, the clock
               event (throttle) reasons by name and `throttled`, encoder and decoder use %
    memory     GiB: total, available (MemAvailable), used, swap; the split of what is used into
               weights, KV, caches, other processes, the page cache; the engines' ceiling
    cpu        use %, load 1/5/15, the hottest zone °C, per core: use %, MHz, performance or
               efficiency (cpu_capacity: the biggest cores are `performance`)
    disks      per block device read/write MB/s and busy; fs: free and total GB per mount
               (ext4, xfs, btrfs, vfat, zfs); nvme: temperature °C per controller
    net        lan: rx/tx MB/s, speed, state per interface; fabric: the ConnectX interfaces, each
               rx/tx Gb/s, speed, state, MTU, and its RDMA port's rx/tx Gb/s and state
    board      thermal zones °C by type, fans RPM (hwmon fan*_input), uptime s

Cost: one host collector read (psutil and NVML, or /proc and nvidia-smi), a few dozen small /sys
and /proc files, and the engines' memory split once a minute (a TandemLLM engine takes 0.1 to 0.6 s
to answer it). The mount list is read again every 60 s. The sampler runs only while a stream is
open.
"""

from __future__ import annotations

import collections
import math
import os
import threading
import time

from proxy import nodesource

GIB = 1024 ** 3
MOUNTS_EVERY_S = 60.0
FS_TYPES = ("ext4", "ext3", "xfs", "btrfs", "vfat", "zfs", "f2fs", "apfs")
# NVML's clock event reasons (nvmlClocksEventReason*), by bit
THROTTLE = ((0x1, "gpu_idle"), (0x2, "applications_clocks"), (0x4, "sw_power_cap"),
            (0x8, "hw_slowdown"), (0x10, "sync_boost"), (0x20, "sw_thermal"),
            (0x40, "hw_thermal"), (0x80, "hw_power_brake"), (0x100, "display_clocks"))
SUB_QUEUE = 120


def prune(x):
    """`x` with every None, and every dict or list that is empty or left empty by that, taken
    out: what a board does not expose is absent, never null, [] or 0."""
    if isinstance(x, dict):
        out = {}
        for k, v in x.items():
            v = prune(v)
            if v is not None and v != {} and v != []:
                out[k] = v
        return out
    if isinstance(x, list):
        return [y for y in (prune(v) for v in x) if y is not None and y != {} and y != []]
    return x


def _r(v, nd=2):
    return None if v is None else round(v, nd)


class Reader:
    """The extra figures beside LocalNode's, read from `root` (/proc, /sys) and NVML."""

    def __init__(self, local: nodesource.LocalNode, collector, *, root: str = "/",
                 clock=time.time):
        self.local, self.collector = local, collector
        self.root = root
        self.clock = clock
        self._prev: dict = {}                       # counters of the previous sample
        self._mounts: list | None = None
        self._mounts_at = -math.inf
        self._caps: dict | None = None

    def _p(self, *parts) -> str:
        return os.path.join(self.root, *parts)

    def _read(self, *parts) -> str | None:
        try:
            with open(self._p(*parts)) as f:
                return f.read().strip()
        except OSError:
            return None

    def _int(self, *parts) -> int | None:
        v = self._read(*parts)
        try:
            return int(v) if v is not None else None
        except ValueError:
            return None

    def _ls(self, *parts) -> list[str]:
        try:
            return sorted(os.listdir(self._p(*parts)))
        except OSError:
            return []

    # ------------------------------------------------------------------ the sample
    def sample(self, now: float) -> dict:
        h = self.local.host() or {}
        prev, cur = self._prev, {"t": now}
        dt = now - prev["t"] if prev.get("t") is not None and now > prev["t"] else None
        mem = h.get("memory") or {}
        cpu = h.get("cpu") or {}
        split = None
        try:
            split = self.local.memory(now, h)
        except Exception:                                            # noqa: BLE001
            split = None
        g = lambda b: None if b is None else round(b / GIB, 2)       # noqa: E731
        memory = {"total": g(mem.get("total_bytes")), "available": g(mem.get("available_bytes")),
                  "used": g(mem.get("used_bytes")), "swap_total": g(mem.get("swap_total_bytes")),
                  "swap_used": g(mem.get("swap_used_bytes")), "unified": mem.get("unified")}
        if split is not None:
            memory.update({k: split[k] for k in ("weights", "kv", "cache", "other", "ceiling")})
            if self._read("proc", "meminfo") is not None:          # no page cache figure else
                memory["page"] = split["page"]
        fabric_if = self.local.fabric_ports()
        out = {
            "t": now,
            "node": self.local.figures(now, h),
            "gpus": self._gpus(h),
            "memory": memory,
            "cpu": {"util": _r(None if cpu.get("utilization") is None
                               else cpu["utilization"] * 100.0),
                    "load": [x for x in cpu.get("load") or [] if x is not None] or None,
                    "temp_c": cpu.get("temperature_c"), "cores": self._cores(cur, prev, dt)},
            "disks": {"devices": [{"name": d["name"], "nvme": d.get("nvme"),
                                   "read_mbps": _r(None if d.get("read_bps") is None
                                                   else d["read_bps"] / 1e6, 3),
                                   "write_mbps": _r(None if d.get("write_bps") is None
                                                    else d["write_bps"] / 1e6, 3),
                                   "busy": d.get("busy")} for d in h.get("disks") or []],
                      "fs": self._fs(now), "nvme": self._nvme_temps()},
            "net": {"lan": self._lan(h, fabric_if),
                    "fabric": self._fabric(fabric_if, cur, prev, dt)},
            "board": {"zones": self._zones(), "fans": self._fans(), "uptime_s": self._uptime()},
        }
        self._prev = cur
        return prune(out)

    # ------------------------------------------------------------------ GPU
    def _gpus(self, h: dict) -> list[dict]:
        n = self.collector._nvml_handle() if self.collector is not None else None
        out = []
        for gp in h.get("gpus") or []:
            row = {"index": gp.get("index"), "name": gp.get("name"),
                   "util": _r(None if gp.get("utilization") is None
                              else gp["utilization"] * 100.0),
                   "sm_mhz": gp.get("sm_clock_mhz"), "mem_mhz": gp.get("mem_clock_mhz"),
                   "power_w": gp.get("power_w"), "power_cap_w": gp.get("power_limit_w"),
                   "temp_c": gp.get("temperature_c")}
            if n is not None:
                row.update(self._nvml_extra(n, gp.get("index") or 0))
            out.append(row)
        return out

    @staticmethod
    def _nvml_extra(n, i: int) -> dict:
        out = {}
        try:
            h = n.nvmlDeviceGetHandleByIndex(i)
        except Exception:                                            # noqa: BLE001
            return out
        for fn in ("nvmlDeviceGetCurrentClocksEventReasons",
                   "nvmlDeviceGetCurrentClocksThrottleReasons"):
            try:
                bits = int(getattr(n, fn)(h))
            except Exception:                                        # noqa: BLE001
                continue
            # `throttled` says the driver answered (none active is a reading, not an absence)
            out["throttled"] = bool(bits & ~0x1)          # an idle GPU's clock is no throttle
            out["throttle"] = [name for bit, name in THROTTLE if bits & bit]
            break
        for key, fn in (("enc", "nvmlDeviceGetEncoderUtilization"),
                        ("dec", "nvmlDeviceGetDecoderUtilization")):
            try:
                out[key] = float(getattr(n, fn)(h)[0])
            except Exception:                                        # noqa: BLE001
                pass
        return out

    # ------------------------------------------------------------------ CPU
    def _capacities(self) -> dict:
        """{cpu: 'performance' | 'efficiency'}, {} when every core is alike or the kernel does not
        say. By core design (/proc/cpuinfo's `CPU part`) when there are two, the one with the
        larger cpu_capacity the performance cores; else by cpu_capacity, split halfway between the
        smallest and the largest (a GB10's X925 cores read 997 to 1024, its A725 cores 718 and
        731, so the largest value alone is not the cluster)."""
        if self._caps is None:
            caps = {}
            for n in self._ls("sys", "devices", "system", "cpu"):
                if n.startswith("cpu") and n[3:].isdigit():
                    v = self._int("sys", "devices", "system", "cpu", n, "cpu_capacity")
                    if v is not None:
                        caps[int(n[3:])] = v
            parts, cur = {}, None
            for line in (self._read("proc", "cpuinfo") or "").splitlines():
                k, _, v = line.partition(":")
                if k.strip() == "processor" and v.strip().isdigit():
                    cur = int(v)
                elif k.strip() == "CPU part" and cur is not None:
                    parts[cur] = v.strip()
            self._caps = {}
            if caps and len(set(parts.values())) == 2 and set(parts) >= set(caps):
                mean = {p: sum(caps[c] for c in caps if parts[c] == p) /
                        max(1, sum(1 for c in caps if parts[c] == p)) for p in set(parts.values())}
                big = max(mean, key=mean.get)
                self._caps = {c: "performance" if parts[c] == big else "efficiency" for c in caps}
            elif len(set(caps.values())) > 1:
                mid = (min(caps.values()) + max(caps.values())) / 2
                self._caps = {c: "performance" if v > mid else "efficiency"
                              for c, v in caps.items()}
        return self._caps

    def _cores(self, cur: dict, prev: dict, dt) -> list[dict]:
        text = self._read("proc", "stat")
        if not text:
            return []
        kinds = self._capacities()
        ticks = {}
        for line in text.splitlines():
            p = line.split()
            if not p or not p[0].startswith("cpu") or p[0] == "cpu":
                continue
            try:
                vals = [int(x) for x in p[1:9]]
            except ValueError:
                continue
            ticks[int(p[0][3:])] = (sum(vals), vals[3] + vals[4])
        cur["cores"] = ticks
        old = prev.get("cores") or {}
        out = []
        for c, (tot, idle) in sorted(ticks.items()):
            util = None
            if c in old and tot > old[c][0]:
                util = round(max(0.0, min(1.0, 1 - (idle - old[c][1]) / (tot - old[c][0])))
                             * 100.0, 1)
            khz = self._int("sys", "devices", "system", "cpu", f"cpu{c}", "cpufreq",
                            "scaling_cur_freq")
            out.append({"cpu": c, "util": util, "mhz": None if khz is None else khz // 1000,
                        "kind": kinds.get(c)})
        return out

    # ------------------------------------------------------------------ disks
    def _fs(self, now: float) -> list[dict]:
        if self._mounts is None or now - self._mounts_at >= MOUNTS_EVERY_S:
            seen, mounts = set(), []
            for line in (self._read("proc", "mounts") or "").splitlines():
                p = line.split()
                if len(p) >= 3 and p[2] in FS_TYPES and p[0] not in seen:
                    seen.add(p[0])
                    mounts.append(p[1].replace("\\040", " "))
            self._mounts, self._mounts_at = mounts, now
        out = []
        for m in self._mounts:
            try:
                st = os.statvfs(m)
            except OSError:
                continue
            out.append({"path": m, "total_gb": round(st.f_blocks * st.f_frsize / 1e9, 1),
                        "free_gb": round(st.f_bavail * st.f_frsize / 1e9, 1)})
        return out

    def _hwmons(self):
        for d in self._ls("sys", "class", "hwmon"):
            yield d, self._read("sys", "class", "hwmon", d, "name")

    def _nvme_temps(self) -> list[dict]:
        out = []
        for d, name in self._hwmons():
            if name != "nvme":
                continue
            v = self._int("sys", "class", "hwmon", d, "temp1_input")
            dev = os.path.basename(os.path.realpath(self._p("sys", "class", "hwmon", d,
                                                            "device")))
            if v is not None:
                out.append({"name": dev if dev.startswith("nvme") else d, "temp_c": v / 1000.0})
        return out

    # ------------------------------------------------------------------ network
    def _lan(self, h: dict, fabric_if: dict) -> list[dict]:
        out = []
        for r in h.get("net") or []:
            n = r.get("name")
            if n in fabric_if:
                continue
            sp = self._int("sys", "class", "net", n, "speed")
            out.append({"iface": n,
                        "rx_mbps": _r(None if r.get("rx_bps") is None else r["rx_bps"] / 1e6, 3),
                        "tx_mbps": _r(None if r.get("tx_bps") is None else r["tx_bps"] / 1e6, 3),
                        "speed_mbps": sp if sp is not None and sp > 0 else None,
                        "state": self._read("sys", "class", "net", n, "operstate")})
        return out

    def rdma_port(self, iface: str) -> tuple[str, str] | None:
        """(RDMA device, port) behind a network interface, None without one."""
        devs = self._ls("sys", "class", "net", iface, "device", "infiniband")
        if not devs:
            return None
        ports = self._ls("sys", "class", "infiniband", devs[0], "ports")
        return (devs[0], ports[0]) if ports else None

    def _fabric(self, ports: dict, cur: dict, prev: dict, dt) -> list[dict]:
        out = []
        cur["fabric"] = {}
        old = prev.get("fabric") or {}
        for n in sorted(ports):
            net = ("sys", "class", "net", n)
            c = {"rx": self._int(*net, "statistics", "rx_bytes"),
                 "tx": self._int(*net, "statistics", "tx_bytes")}
            row = {"iface": n, "state": self._read(*net, "operstate"),
                   "speed_mbps": None, "mtu": self._int(*net, "mtu")}
            sp = self._int(*net, "speed")
            row["speed_mbps"] = sp if sp is not None and sp > 0 else None
            rp = self.rdma_port(n)
            if rp is not None:
                ib = ("sys", "class", "infiniband", rp[0], "ports", rp[1])
                # port_*_data counts 4-byte words
                c["rrx"] = self._int(*ib, "counters", "port_rcv_data")
                c["rtx"] = self._int(*ib, "counters", "port_xmit_data")
                state = self._read(*ib, "state")
                row["rdma"] = {"device": rp[0], "port": rp[1],
                               "state": state.split(":")[-1].strip().lower() if state else None,
                               "link_layer": self._read(*ib, "link_layer")}
            cur["fabric"][n] = c
            o = old.get(n) or {}

            def rate(k, scale):
                if dt is None or c.get(k) is None or o.get(k) is None or c[k] < o[k]:
                    return None
                return round((c[k] - o[k]) * scale * 8 / dt / 1e9, 4)
            row["rx_gbps"], row["tx_gbps"] = rate("rx", 1), rate("tx", 1)
            if "rdma" in row:
                row["rdma"]["rx_gbps"], row["rdma"]["tx_gbps"] = rate("rrx", 4), rate("rtx", 4)
            out.append(row)
        return out

    # ------------------------------------------------------------------ board
    def _zones(self) -> list[dict]:
        out = []
        for z in self._ls("sys", "class", "thermal"):
            if not z.startswith("thermal_zone"):
                continue
            v = self._int("sys", "class", "thermal", z, "temp")
            if v is not None and -40_000 < v < 150_000:
                out.append({"zone": z, "type": self._read("sys", "class", "thermal", z, "type"),
                            "temp_c": v / 1000.0})
        return out

    def _fans(self) -> list[dict]:
        out = []
        for d, name in self._hwmons():
            for f in self._ls("sys", "class", "hwmon", d):
                if f.startswith("fan") and f.endswith("_input"):
                    v = self._int("sys", "class", "hwmon", d, f)
                    if v is not None:
                        out.append({"name": f"{name or d}/{f[:-6]}", "rpm": v})
        return out

    def _uptime(self) -> float | None:
        v = self._read("proc", "uptime")
        try:
            return round(float(v.split()[0]), 1) if v else None
        except ValueError:
            return None


# ====================================================================== the 1 s sampler

class Sub:
    """One listener's queue; a listener that falls behind loses its oldest samples."""

    def __init__(self):
        self.q: collections.deque = collections.deque(maxlen=SUB_QUEUE)
        self.cv = threading.Condition()
        self.closed = False

    def push(self, s: dict) -> None:
        with self.cv:
            self.q.append(s)
            self.cv.notify_all()

    def take(self, timeout: float) -> list[dict]:
        with self.cv:
            if not self.q and not self.closed:
                self.cv.wait(timeout)
            out = list(self.q)
            self.q.clear()
            return out


class Sampler:
    """`read(t)` once a second, on the whole second, while at least one listener is subscribed;
    a read that overruns its second skips to the next whole one (never a burst)."""

    def __init__(self, read, *, clock=time.time, wait=None, period: float = 1.0,
                 log=lambda m: print(m, flush=True)):
        self.read, self.clock, self.period, self.log = read, clock, period, log
        self._halt = threading.Event()
        self.wait = wait or self._halt.wait
        self._lock = threading.Lock()
        self.subs: list[Sub] = []
        self.latest: dict | None = None
        self.samples = 0
        self.cpu_s = 0.0                     # the reads' own CPU time, for the cost line
        self._thread: threading.Thread | None = None

    def subscribe(self) -> Sub:
        s = Sub()
        with self._lock:
            self.subs.append(s)
            if self._thread is None or not self._thread.is_alive():
                self._halt.clear()
                self._thread = threading.Thread(target=self._run, name="node-sampler",
                                                daemon=True)
                self._thread.start()
        return s

    def unsubscribe(self, s: Sub) -> None:
        with self._lock:
            if s in self.subs:
                self.subs.remove(s)
            s.closed = True
            if not self.subs:
                self._halt.set()

    def close(self) -> None:
        with self._lock:
            for s in self.subs:
                s.closed = True
            self.subs = []
            self._halt.set()

    def _run(self) -> None:
        while True:
            now = self.clock()
            due = math.floor(now / self.period) * self.period + self.period
            self.wait(due - now)
            with self._lock:
                if not self.subs:
                    self._thread = None
                    return
                subs = list(self.subs)
            c0 = time.process_time()
            try:
                s = self.read(due)
            except Exception as exc:                                 # noqa: BLE001
                self.log(f"[node] sample failed: {type(exc).__name__}: {exc}")
                continue
            self.cpu_s += time.process_time() - c0
            self.samples += 1
            self.latest = s
            for sub in subs:
                sub.push(s)
