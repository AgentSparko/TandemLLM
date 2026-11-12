"""The MemAvailable gate of a two-engine run on one unified-memory box (OPS-197, guard G12).

A freeze is how this board fails when two engines, their page cache and a prefill's peak outgrow it,
so a run with two engines on one box passes only while MemAvailable stays above a floor, read from
/proc/meminfo every 0.2 s. Two checks:

    python3 tools/memgate.py --after-load-min-gib 15
        one reading after both loads: exit 0 at or above it, 1 under it

    python3 tools/memgate.py --floor-gib 10 [--seconds N | --while-pid PID] \\
            [--stop-pid PID | --stop-file PATH] [--csv samples.csv]
        sample until SIGTERM/SIGINT, N seconds, or PID's exit; the first reading under the floor
        ends it with exit 1, SIGTERM to --stop-pid (the load driver) and/or the time written to
        --stop-file, and the breach logged

Both end with one JSON line: {"min_gib", "min_at", "samples", "breach", "floor_gib", "mode"}, the
minimum with its wall-clock time (ISO, UTC). --every sets the period (0.2 s); --meminfo another file
(a test's). QSE_MEMGATE_FAKE_CLOCK=1 advances a clock of its own instead of sleeping (tests).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import signal
import sys
import time

GIB = 1 << 30


def mem_available(path: str = "/proc/meminfo") -> float:
    """MemAvailable in GiB."""
    with open(path) as f:
        for line in f:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024 / GIB
    raise ValueError(f"{path} has no MemAvailable line")


class Clock:
    def __init__(self, fake: bool):
        self.fake = fake
        self.t = time.time()

    def now(self) -> float:
        return self.t if self.fake else time.time()

    def sleep(self, s: float) -> None:
        if self.fake:
            self.t += s
        else:
            time.sleep(s)


def _iso(t: float) -> str:
    return _dt.datetime.fromtimestamp(t, _dt.timezone.utc).isoformat(timespec="milliseconds")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--meminfo", default="/proc/meminfo")
    ap.add_argument("--every", type=float, default=0.2, help="seconds between readings")
    ap.add_argument("--after-load-min-gib", type=float, default=None,
                    help="one reading: pass at or above this many GiB")
    ap.add_argument("--floor-gib", type=float, default=None,
                    help="watch: the first reading under it is a breach")
    ap.add_argument("--seconds", type=float, default=None, help="watch this long, then pass")
    ap.add_argument("--while-pid", type=int, default=None, help="watch while this process lives")
    ap.add_argument("--stop-pid", type=int, default=None,
                    help="on a breach, SIGTERM this process (the load driver)")
    ap.add_argument("--stop-file", default=None, help="on a breach, write the time to this file")
    ap.add_argument("--csv", default=None, help="write every reading: unix time, ISO time, GiB")
    a = ap.parse_args(argv)
    if (a.after_load_min_gib is None) == (a.floor_gib is None):
        ap.error("give one of --after-load-min-gib or --floor-gib")
    clock = Clock(os.environ.get("QSE_MEMGATE_FAKE_CLOCK") == "1")
    rows: list[tuple[float, float]] = []

    def read() -> float:
        g = mem_available(a.meminfo)
        rows.append((clock.now(), g))
        return g

    def summary(mode: str, breach: bool, floor: float) -> dict:
        t, g = min(rows, key=lambda r: r[1]) if rows else (None, None)
        return {"mode": mode, "min_gib": round(g, 3) if g is not None else None,
                "min_at": _iso(t) if t is not None else None, "samples": len(rows),
                "breach": breach, "floor_gib": floor}

    def write_csv() -> None:
        if a.csv:
            with open(a.csv, "w") as f:
                f.write("unix_s,iso,mem_available_gib\n")
                for t, g in rows:
                    f.write(f"{t:.3f},{_iso(t)},{g:.3f}\n")

    if a.after_load_min_gib is not None:
        g = read()
        ok = g >= a.after_load_min_gib
        print(f"[memgate] after load: MemAvailable {g:.2f} GiB, need >= {a.after_load_min_gib:g}: "
              f"{'PASS' if ok else 'FAIL'}", flush=True)
        write_csv()
        print(json.dumps(summary("after_load", not ok, a.after_load_min_gib)), flush=True)
        return 0 if ok else 1

    stop = {"sig": None}

    def on_signal(sig, _frame):
        stop["sig"] = sig

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    t0 = clock.now()
    breach = False
    print(f"[memgate] watching MemAvailable every {a.every:g} s, floor {a.floor_gib:g} GiB",
          flush=True)
    while stop["sig"] is None:
        g = read()
        if g < a.floor_gib:
            breach = True
            print(f"[memgate] BREACH at {_iso(rows[-1][0])}: MemAvailable {g:.2f} GiB under "
                  f"{a.floor_gib:g}", flush=True)
            if a.stop_pid:
                try:
                    os.kill(a.stop_pid, signal.SIGTERM)
                    print(f"[memgate] SIGTERM to the load driver {a.stop_pid}", flush=True)
                except OSError as exc:
                    print(f"[memgate] the load driver {a.stop_pid}: {exc}", flush=True)
            if a.stop_file:
                with open(a.stop_file, "w") as f:
                    f.write(_iso(rows[-1][0]) + "\n")
            break
        if a.seconds is not None and clock.now() - t0 >= a.seconds:
            break
        if a.while_pid is not None and not _alive(a.while_pid):
            break
        clock.sleep(a.every)
    t, g = min(rows, key=lambda r: r[1])
    print(f"[memgate] minimum {g:.2f} GiB at {_iso(t)} over {len(rows)} readings: "
          f"{'FAIL' if breach else 'PASS'}", flush=True)
    write_csv()
    print(json.dumps(summary("watch", breach, a.floor_gib)), flush=True)
    return 1 if breach else 0


if __name__ == "__main__":
    sys.exit(main())
