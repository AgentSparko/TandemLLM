"""tools/memgate.py, the MemAvailable gate of two engines on one box (OPS-197, QA OPS-198), on a
meminfo file of the test's own and a clock of its own (QSE_MEMGATE_FAKE_CLOCK=1):

  1  one reading after a load passes at the floor and fails under it
  2  watching passes, every 0.2 s for the run's length, every sample written
  3  a breach ends the watch non-zero, the load driver signalled, the minimum and its time logged
  4  the watch ends with the process it watches and on SIGTERM; no mode is a usage error

Run: python3 tests/test_memgate.py
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(ROOT, "tools", "memgate.py")


def _meminfo(d: str, gib: float) -> str:
    p = os.path.join(d, "meminfo")
    with open(p, "w") as f:
        f.write(f"MemTotal:       127543104 kB\nMemFree:          1000000 kB\n"
                f"MemAvailable:   {int(gib * 1024 * 1024)} kB\n")
    return p


def _gate(*args, **env) -> subprocess.CompletedProcess:
    e = dict(os.environ, QSE_MEMGATE_FAKE_CLOCK="1")
    e.update(env)
    return subprocess.run([sys.executable, GATE, *args], capture_output=True, text=True, env=e,
                          timeout=30)


def _summary(r) -> dict:
    return json.loads(r.stdout.strip().splitlines()[-1])


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _sleeper() -> int:
    """A detached process to be signalled (a child would linger as a zombie and look alive)."""
    return int(subprocess.run(["bash", "-c", "sleep 300 >/dev/null 2>&1 & echo $!"],
                              capture_output=True, text=True).stdout)


def test_memgate_after_load_passes_at_the_floor_and_fails_under_it():
    d = tempfile.mkdtemp(prefix="memgate-")
    r = _gate("--meminfo", _meminfo(d, 15.5), "--after-load-min-gib", "15")
    assert r.returncode == 0 and "PASS" in r.stdout, r.stdout + r.stderr
    s = _summary(r)
    assert s["mode"] == "after_load" and abs(s["min_gib"] - 15.5) < 0.01 and not s["breach"], s
    r = _gate("--meminfo", _meminfo(d, 15.0), "--after-load-min-gib", "15")
    assert r.returncode == 0, r.stdout
    r = _gate("--meminfo", _meminfo(d, 14.0), "--after-load-min-gib", "15")
    assert r.returncode == 1 and "FAIL" in r.stdout and _summary(r)["breach"], r.stdout


def test_memgate_watch_passes_and_writes_every_sample():
    d = tempfile.mkdtemp(prefix="memgate-")
    csv = os.path.join(d, "s.csv")
    r = _gate("--meminfo", _meminfo(d, 12.0), "--floor-gib", "10", "--seconds", "2", "--csv", csv)
    assert r.returncode == 0, r.stdout + r.stderr
    s = _summary(r)
    assert s["samples"] == 11 and not s["breach"] and s["floor_gib"] == 10 and s["min_at"], s
    rows = open(csv).read().splitlines()
    assert rows[0] == "unix_s,iso,mem_available_gib" and len(rows) == 12, rows
    t = [float(x.split(",")[0]) for x in rows[1:]]
    assert all(abs((b - a) - 0.2) < 1e-6 for a, b in zip(t, t[1:])), t
    assert "minimum 12.00 GiB at" in r.stdout and "PASS" in r.stdout


def test_memgate_breach_stops_the_load_driver_and_logs_the_minimum():
    d = tempfile.mkdtemp(prefix="memgate-")
    driver = _sleeper()
    stop = os.path.join(d, "stop")
    try:
        r = _gate("--meminfo", _meminfo(d, 9.5), "--floor-gib", "10", "--stop-pid", str(driver),
                  "--stop-file", stop)
        assert r.returncode == 1, r.stdout + r.stderr
        assert "BREACH at" in r.stdout and f"SIGTERM to the load driver {driver}" in r.stdout
        s = _summary(r)
        assert s["breach"] and abs(s["min_gib"] - 9.5) < 0.01 and s["min_at"], s
        assert open(stop).read().strip() == s["min_at"]
        for _ in range(50):
            if not _alive(driver):
                break
            time.sleep(0.1)
        assert not _alive(driver)
    finally:
        if _alive(driver):
            os.kill(driver, signal.SIGKILL)


def test_memgate_ends_with_the_process_it_watches_and_on_sigterm():
    d = tempfile.mkdtemp(prefix="memgate-")
    r = _gate("--meminfo", _meminfo(d, 20), "--floor-gib", "10", "--while-pid", "999999")
    assert r.returncode == 0 and _summary(r)["samples"] == 1, r.stdout
    p = subprocess.Popen([sys.executable, GATE, "--meminfo", _meminfo(d, 20), "--floor-gib", "10",
                          "--every", "0.05"], stdout=subprocess.PIPE, text=True)
    time.sleep(0.5)
    p.send_signal(signal.SIGTERM)
    out = p.communicate(timeout=10)[0]
    assert p.returncode == 0 and json.loads(out.strip().splitlines()[-1])["samples"] >= 2, out
    r = _gate("--meminfo", _meminfo(d, 20))
    assert r.returncode == 2


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  {name:66s} ok")
            passed += 1
    print(f"{passed} passed")
