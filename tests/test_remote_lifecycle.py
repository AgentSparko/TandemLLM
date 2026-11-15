"""The lifecycle jobs of `tandem remote` (engine-ctl-v1; ENG-452): start, stop, restart, settings.

The real ops/start.sh, ops/stop.sh and ops/serve.env run in a sandbox tree against a stub engine
(tests/remote_box.py: its own process table, a free port, no box file); the waits run on a fake clock.

The test matrix:

  1  start answers at once with a job; the job starts the engine and status says healthy; logs reads
     the engine's and the launcher's log, --follow streams JSON lines; a new version applied with its
     token reaches the engine's command line; a version whose start fails (the stub refuses
     --max-queue 13) is rolled back: the previous version current again, the engine started again and
     healthy, the job failed with `restored`; restore of an older version; stop leaves the marker
     and status says stopped on purpose; a plan of another base version does not apply
                                                                    test_jobs_against_the_stub_engine
  2  one job acts at a time: a call while one acts answers busy (exit 4); a new call replaces a job
     that waits; a runner that is gone reads lost                  test_busy_and_replace
  3  --when idle waits for the engine's in-flight requests (/health `inflight`, the Setup Guide's
     rule) and acts once there are none; past its deadline the job fails with nothing changed;
     at= waits for the time first; a cancelled waiting job stops  test_when_idle_at_and_deadline
  4  ops/watchdog.sh leaves an engine down while run/remote/stopped exists, restarts it without the
     marker, and removes the marker once the engine answers       test_the_watchdog_honours_a_stop

Run: python tests/test_remote_lifecycle.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import remote_box as R  # noqa: E402
from server.remote import cli, lifecycle as L, overlay as O  # noqa: E402
from server.remote.grammar import Call  # noqa: E402
from server.remote.paths import Tree  # noqa: E402
from server.setup import apply as setup_apply, idle  # noqa: E402


def _engine_argv(b: dict) -> list[str]:
    proc = os.path.join(b["root"], "proc")
    pids = [d for d in os.listdir(proc) if d.isdigit()]
    assert len(pids) == 1, pids
    return open(os.path.join(proc, pids[0], "cmdline"), "rb").read().decode().split("\0")[:-1]


def _apply(b: dict, *changes: str, when: str = "now") -> dict:
    rc, p = R.call(b, "settings", "plan", *changes)
    assert rc == 0, p
    extra = ["--confirm-fingerprint", p["plan"]["confirm_fingerprint"]] if "confirm_fingerprint" in p["plan"] else []
    rc, a = R.call(b, "settings", "apply", *changes, "--confirm", p["plan"]["confirm"], *extra, "--when", when)
    assert rc == 0 and a["job"]["kind"] == "settings", a
    return R.wait_job(b, 180)


def test_jobs_against_the_stub_engine():
    b = R.make()
    try:
        rc, a = R.call(b, "start")
        assert rc == 0 and a["job"]["kind"] == "start" and a["job"]["state"] == "waiting", a
        job = R.wait_job(b)
        assert job["state"] == "done" and job["message"] == "healthy", job
        rc, st = R.call(b, "status")
        assert st["engine"]["state"] == "healthy" and st["engine"]["pid"] and st["overlay"]["good"] == 0, st
        assert st["version"] == "0.0.0-test" and st["mode"] == "engine", st
        rc, lg = R.call(b, "logs", "--which", "launcher", "--lines", "50")
        assert any("[start] healthy" in x for x in lg["lines"]), lg
        rc, lg = R.call(b, "logs")
        assert lg["which"] == "engine" and lg["file"] == "logs/engine.log", lg
        # follow: the last lines, then new ones, one JSON document a line
        p = subprocess.Popen([sys.executable, "-c", "import sys; from server.remote import cli; "
                              "from server.remote.paths import Tree; sys.exit(cli.run(sys.argv[2:], tree=Tree(sys.argv[1])))",
                              b["root"], "logs", "--follow", "--lines", "1", "--which", "launcher"],
                             cwd=ROOT, env=R.env(b), stdout=subprocess.PIPE, text=True)
        try:
            first = json.loads(p.stdout.readline())
            with open(os.path.join(b["root"], "logs", "launcher.log"), "a") as fh:
                fh.write("a new line\n")
            nxt = json.loads(p.stdout.readline())
            assert first["seq"] == 1 and nxt == {"ctl": "engine-ctl-v1", "verb": "logs", "which": "launcher",
                                                 "seq": 2, "line": "a new line"}, (first, nxt)
        finally:
            p.terminate()
            p.wait(10)
        # a version that starts
        job = _apply(b, "MAX_QUEUE=5")
        assert job["state"] == "done" and job["overlay"]["to"] == 1, job
        argv = _engine_argv(b)
        assert argv[argv.index("--max-queue") + 1] == "5", argv
        m = O.manifest(Tree(b["root"]))
        assert (m["current"], m["good"]) == (1, 1), m
        # a version whose start fails: version 1 back, started again
        job = _apply(b, "MAX_QUEUE=13")
        assert job["state"] == "failed" and job["restored"] == {"version": 1, "healthy": True}, job
        assert "version 1 is back and the engine answers" in job["message"], job
        m = O.manifest(Tree(b["root"]))
        assert (m["current"], m["good"], len(m["versions"])) == (1, 1, 2), m
        argv = _engine_argv(b)
        assert argv[argv.index("--max-queue") + 1] == "5", argv
        rc, st = R.call(b, "status")
        assert st["engine"]["state"] == "healthy", st
        # restore of version 0 (no overlay): a new version with its keys
        rc, p = R.call(b, "settings", "plan", "--unset", "MAX_QUEUE")
        rc, r0 = R.call(b, "settings", "restore", "0")
        assert rc == 3 and r0["error"]["code"] == "confirm_required", r0
        rc, r0 = R.call(b, "settings", "restore", "0", "--confirm", r0["error"]["plan"]["confirm"], "--when", "now")
        assert rc == 0 and r0["job"]["kind"] == "restore", r0
        job = R.wait_job(b, 180)
        assert job["state"] == "done", job
        argv = _engine_argv(b)
        assert argv[argv.index("--max-queue") + 1] == "8", argv
        assert O.values_of(Tree(b["root"]), 3) == {}
        # a plan's token is bound to its base version
        rc, a = R.call(b, "settings", "apply", "MAX_QUEUE=6", "--confirm", O.token(1, {"MAX_QUEUE": "6"}))
        assert rc == 3 and a["error"]["code"] == "confirm_mismatch", a
        # stop: the marker, and status says so
        rc, a = R.call(b, "stop")
        job = R.wait_job(b)
        assert job["state"] == "done" and os.path.exists(os.path.join(b["root"], "run", "remote", "stopped")), job
        rc, st = R.call(b, "status")
        assert st["engine"]["state"] == "down" and st["engine"]["stopped_on_purpose"] is True, st
        rc, a = R.call(b, "start")
        assert R.wait_job(b)["state"] == "done" and not os.path.exists(os.path.join(b["root"], "run", "remote", "stopped"))
        rows = R.journal(b)
        assert sum(1 for r in rows if r.get("job")) >= 6 and all("ms" in r for r in rows), rows
    finally:
        R.cleanup(b)
    return "start, apply, failed start rolled back, restore, stop"


def test_busy_and_replace():
    b = R.make()
    try:
        t = Tree(b["root"])
        at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))
        rc, a = R.call(b, "restart", "--when", f"at={at}")
        assert rc == 0 and a["job"]["when"]["mode"] == "at", a
        first = a["job"]["id"]
        time.sleep(1.0)
        rc, a2 = R.call(b, "restart", "--when", f"at={at}")
        assert rc == 0, a2
        assert L.read_job(t, first)["state"] == "cancelled", L.read_job(t, first)
        # its runner sees the cancel and ends
        t_end = time.time() + 60
        while L._runner_alive(t, first) and time.time() < t_end:
            time.sleep(0.5)
        assert not L._runner_alive(t, first)
        # an acting job: busy
        j = L.read_job(t, a2["job"]["id"])
        with L._lock(t):
            j.update(state="acting", phase="stopping")
            L.write_job(t, j)
        rc, a3 = R.call(b, "start")
        assert rc == 4 and a3["error"]["code"] == "busy" and a3["error"]["job"]["id"] == j["id"], a3
        # a runner that is gone: lost
        subprocess.run(["pkill", "-f", os.path.join(t.jobs, j["id"])], capture_output=True)
        t_end = time.time() + 30
        while L._runner_alive(t, j["id"]) and time.time() < t_end:
            time.sleep(0.2)
        j["created_s"] -= 60
        L.write_job(t, j)
        rc, st = R.call(b, "status")
        assert st["job"]["state"] == "lost", st
    finally:
        R.cleanup(b)
    return "busy, replaced, lost"


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _runner(b, when, healths, kind="restart"):
    t = Tree(b["root"])
    job = L.create(t, kind, when, 30 if when["mode"] != "now" else None, spawn=False)
    seen = []
    it = iter(healths)

    def http(url, timeout=5.0):
        try:
            h = next(it)
        except StopIteration:
            h = {"inflight": {}}
        seen.append(h)
        return 200, h
    calls = []

    def cmd(argv, timeout, env=None):
        calls.append(os.path.basename(argv[1]) if len(argv) > 1 else argv[0])
        return 0
    c = Clock()
    r = L.Runner(t, job["id"], cmd=cmd, http=http, clock=c, sleep=c.sleep, port=1)
    return t, r, calls, seen, c


def test_when_idle_at_and_deadline():
    assert setup_apply.IDLE_WAIT_S == idle.IDLE_WAIT_S and setup_apply.requests_in is idle.requests_in
    assert idle.requests_in({"inflight": {"running": 1, "waiting": 2, "lane": 1}}) == 4
    assert idle.requests_in({}) == 0 and idle.requests_in(None) == 0
    b = R.make()
    os.environ["BOX_LOCK"] = os.path.join(b["root"], "box.flock")     # never the box's own lock
    try:
        busy = {"inflight": {"running": 1}}
        t, r, calls, seen, c = _runner(b, {"mode": "idle"}, [busy, busy, busy, {"inflight": {}}])
        assert r._main() == 0 and calls == ["stop.sh", "start.sh"], calls
        assert len(seen) >= 4 and r.job["state"] == "done", r.job
        # the deadline: nothing changed
        t, r, calls, seen, c = _runner(b, {"mode": "idle"}, [busy] * 1000)
        assert r._main() == 1 and calls == [], calls
        j = L.read_job(t, r.jid)
        assert j["state"] == "failed" and "after the deadline (30 s); nothing changed" in j["message"], j
        # at=: the time first, then idle
        at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(1_000_000 + 600))
        t, r, calls, seen, c = _runner(b, {"mode": "at", "at": at}, [])
        assert r._main() == 0 and c.t >= 1_000_600 and calls == ["stop.sh", "start.sh"], (c.t, calls)
        # cancelled while it waits
        t, r, calls, seen, c = _runner(b, {"mode": "idle"}, [busy] * 1000)
        j = L.read_job(t, r.jid)
        j["state"] = "cancelled"
        L.write_job(t, j)
        assert r._main() == 1 and calls == [] and L.read_job(t, r.jid)["state"] == "cancelled"
        # a start of an engine that answers already
        t, r, calls, seen, c = _runner(b, {"mode": "now"}, [], kind="start")
        assert r._main() == 0 and calls == [] and r.job["message"] == "already healthy", r.job
    finally:
        R.cleanup(b)
    return "idle, deadline, at, cancel"


def test_the_watchdog_honours_a_stop():
    import test_ops_scripts as T
    repo = T._box(["watchdog.sh"])
    marker = os.path.join(repo, "run", "remote", "stopped")
    os.makedirs(os.path.dirname(marker))
    open(marker, "w").close()
    for _ in range(5):
        r = T._run(repo, "watchdog.sh", FAKE_HTTP_CODE="000")
        assert r.returncode == 0, r.stderr
    marks = os.path.join(repo, "markers")
    assert not os.path.exists(marks), open(marks).read()
    T._run(repo, "watchdog.sh", FAKE_HTTP_CODE="200", FAKE_HEALTHY="1")
    assert not os.path.exists(marker)
    for _ in range(3):
        T._run(repo, "watchdog.sh", FAKE_HTTP_CODE="000")
    assert "start.sh" in open(marks).read() and "stop.sh" in open(marks).read()
    return "down left alone, marker gone on 200, restart after"


# the CLI's own Call objects are used by the contract test too
assert Call and cli

if __name__ == "__main__":
    from scriptmode import run
    run(globals())
