"""A unit supervisor must hold on death, not on silence.

ops/engine-exec.sh stays a Type=simple unit's main process while the engine runs, so the unit's
cgroup owns it. The first version left the unit on the first `curl -m 10 /health` timeout; a
long disk-tier save or a GPU sync stalls every Python thread past the timeout while the kernel
still accepts connections, so a keep-alive client finishes its request while the fresh probe
hears nothing -- and the exit took a serving engine down with the unit (2026-11-21, four
bounces of a live engine in one morning). The hold now needs QSE_HOLD_FAILS misses in a row AND
a dead pid AND no listener on the port before it lets the unit go.

The engine here is a stub: start.sh writes the pid of a `sleep` into logs/engine.pid, and curl
and ss on a stubbin PATH answer from control files, so the test drives the three signals
(probe, pid, port) one at a time.

The test matrix:

  1  the script parses (bash -n) and install.sh does not copy it -- a box's unit is its own
     file, like qwen38-engine.service          test_the_script_parses, test_install_does_not_copy_it
  2  a silent-but-alive engine is held: misses far past the single-probe line, pid alive and
     the port claimed, and the supervisor still runs      test_a_silent_engine_is_held
  3  a probe that answers again resets the count: miss 5, recover, miss 5, die -- the exit
     line reports 6 misses, not 11                        test_a_recovered_probe_resets_the_count
  4  a dead pid alone is not death while the port stays claimed (the engine may respawn
     itself; the comment in engine-exec.sh)               test_a_dead_pid_with_a_claimed_port_holds
  5  the engine really gone -- dead pid, no listener, misses in a row -- and the unit lets go
     within seconds                                       test_a_dead_engine_lets_go

Run: python tests/test_engine_exec_hold.py   (Linux only: bash, and a stub curl/ss on PATH)
"""
from __future__ import annotations

import os
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "ops", "engine-exec.sh")
LINUX = sys.platform.startswith("linux")


def _skip() -> bool:
    if not LINUX:
        print("  (skipped: Linux only)")
        return True
    for tool in ("bash", "ss", "curl"):
        if not shutil.which(tool):
            print(f"  (skipped: no {tool})")
            return True
    return False


def _port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _fixture() -> dict:
    """A fake install: DIR/run/ops with the script, a stub start.sh and serve.env, DIR/src/logs
    for the pidfile, and a stubbin dir whose curl/ss read control files."""
    tmp = tempfile.mkdtemp(prefix="engine-exec-test-")
    ops = os.path.join(tmp, "run", "ops")
    src = os.path.join(tmp, "src")
    stubbin = os.path.join(tmp, "stubbin")
    ctl = os.path.join(tmp, "ctl")
    for d in (ops, os.path.join(src, "logs"), stubbin, ctl):
        os.makedirs(d)

    port = _port()
    shutil.copy(SCRIPT, os.path.join(ops, "engine-exec.sh"))

    with open(os.path.join(ops, "serve.env"), "w") as fh:
        fh.write(f"PORT={port}\nREPO='{src}'\n")

    # the stub engine: start.sh drops the pid of a `sleep` where the real one drops the server's
    with open(os.path.join(ops, "start.sh"), "w") as fh:
        fh.write("#!/bin/bash\n"
                 "mkdir -p \"$REPO/logs\"\n"
                 "nohup sleep 300 </dev/null >/dev/null 2>&1 &\n"
                 "echo $! > \"$REPO/logs/engine.pid\"\n"
                 "echo \"[stub start] engine pid $(cat \"$REPO/logs/engine.pid\")\"\n")

    # curl answers from ctl/curl: 'ok' -> a body and 0; anything else -> a refused exit
    with open(os.path.join(stubbin, "curl"), "w") as fh:
        fh.write(f"#!/bin/bash\n"
                 f"M=$(cat '{ctl}/curl' 2>/dev/null || echo ok)\n"
                 f"[ \"$M\" = ok ] && {{ echo '{{\"status\":\"ok\"}}'; exit 0; }}\n"
                 f"exit 7\n")

    # ss answers from ctl/ss: 'listen' -> a listener on the port; anything else -> none
    with open(os.path.join(stubbin, "ss"), "w") as fh:
        fh.write("#!/bin/bash\n"
                 f"M=$(cat '{ctl}/ss' 2>/dev/null || echo listen)\n"
                 f"[ \"$M\" = listen ] && echo 'LISTEN 0 511 0.0.0.0:{port} 0.0.0.0:*'\n"
                 f"exit 0\n")

    for n in ("curl", "ss", "start.sh"):
        p = os.path.join(stubbin if n != "start.sh" else ops, n)
        os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    with open(os.path.join(ctl, "curl"), "w") as fh:
        fh.write("fail")
    with open(os.path.join(ctl, "ss"), "w") as fh:
        fh.write("listen")

    env = dict(os.environ)
    env["PATH"] = stubbin + os.pathsep + env["PATH"]
    env["QSE_HOLD_EVERY_S"] = "0.05"      # ~20 misses a second; the assertions stay generous
    env["QSE_HOLD_FAILS"] = "6"
    return {"tmp": tmp, "ops": ops, "src": src, "ctl": ctl, "env": env, "port": port}


def _run(box: dict) -> tuple[subprocess.Popen, str]:
    log = os.path.join(box["tmp"], "unit.log")
    proc = subprocess.Popen(["bash", os.path.join(box["ops"], "engine-exec.sh")],
                            env=box["env"], stdout=open(log, "w"), stderr=subprocess.STDOUT)
    return proc, log


def _ctl(box: dict, name: str, value: str) -> None:
    with open(os.path.join(box["ctl"], name), "w") as fh:
        fh.write(value)


def _out(log: str) -> str:
    try:
        return open(log).read()
    except OSError:
        return ""


def _wait(cond, timeout: float, what: str) -> None:
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for: {what}")


def _engine_pid(box: dict) -> int:
    return int(open(os.path.join(box["src"], "logs", "engine.pid")).read())


def _misses(log: str) -> int:
    return max([int(m) for m in re.findall(r"probe failed \((\d+)\)", _out(log))] or [0])


def _stop(box: dict, proc: subprocess.Popen) -> None:
    try:
        os.kill(_engine_pid(box), signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass
    proc.kill()
    proc.wait(timeout=10)
    shutil.rmtree(box["tmp"], ignore_errors=True)


def test_the_script_parses():
    r = subprocess.run(["bash", "-n", SCRIPT], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_install_does_not_copy_it():
    # a box's unit is the box's own file, like ops/qwen38-engine.service: copying this beside a
    # rendered serve.env is the deploy step, not the installer's job
    text = open(os.path.join(ROOT, "install.sh")).read()
    m = re.search(r'for s in ([^;]*); do install -m 755 "\$SRC/ops/\$s"', text)
    assert "engine-exec.sh" not in m.group(1).split()


def test_a_silent_engine_is_held():
    if _skip():
        return
    box = _fixture()
    proc, log = _run(box)
    try:
        # probes fail from the start (a stalled engine) but the pid lives and the port is
        # claimed: far past the old single-miss line, the supervisor still runs
        _wait(lambda: _misses(log) >= 6, 15, "six failed probes")
        assert proc.poll() is None, _out(log)
        time.sleep(0.5)
        assert proc.poll() is None, _out(log)
        assert "stopped answering" not in _out(log), _out(log)
    finally:
        _stop(box, proc)


def test_a_recovered_probe_resets_the_count():
    if _skip():
        return
    box = _fixture()
    proc, log = _run(box)
    try:
        _wait(lambda: _misses(log) >= 5, 15, "five failed probes")
        _ctl(box, "curl", "ok")                    # the stall ends; the count must forget it
        time.sleep(0.4)
        _ctl(box, "curl", "fail")
        # a second "probe failed (1)" line exists only if the recovery restarted the count;
        # without the reset the second streak would keep numbering past the first's
        _wait(lambda: len(re.findall(r"probe failed \(1\)", _out(log))) >= 2,
              15, "the count restarting at 1")
        os.kill(_engine_pid(box), signal.SIGKILL)
        _ctl(box, "ss", "nolisten")
        _wait(lambda: proc.poll() == 0, 15, "the unit letting go")
        assert "the engine stopped answering" in _out(log), _out(log)
    finally:
        _stop(box, proc)


def test_a_dead_pid_with_a_claimed_port_holds():
    if _skip():
        return
    box = _fixture()
    proc, log = _run(box)
    try:
        _wait(lambda: _misses(log) >= 2, 15, "the hold settling in")
        os.kill(_engine_pid(box), signal.SIGKILL)   # pid dead, port still claimed (a respawn)
        _wait(lambda: _misses(log) >= 10, 15, "ten failed probes")
        assert proc.poll() is None, _out(log)
        assert "stopped answering" not in _out(log), _out(log)
    finally:
        _stop(box, proc)


def test_a_dead_engine_lets_go():
    if _skip():
        return
    box = _fixture()
    proc, log = _run(box)
    try:
        _wait(lambda: _misses(log) >= 2, 15, "the hold settling in")
        os.kill(_engine_pid(box), signal.SIGKILL)
        _ctl(box, "ss", "nolisten")                  # dead pid AND no listener: really gone
        _wait(lambda: proc.poll() == 0, 15, "the unit letting go")
        assert "the engine stopped answering" in _out(log), _out(log)
    finally:
        _stop(box, proc)


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  {name:60s} ok")
            passed += 1
    print(f"{passed} passed")
