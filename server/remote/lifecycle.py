"""The lifecycle jobs of `tandem remote`: start, stop, restart and the restarts of a settings change.

A call writes a job (run/remote/jobs/<id>/job.json) and starts its runner, a process of its own
session (`python -m server.remote.lifecycle run <job folder>`), so the call answers at once and an
SSH drop or the engine's restart does not take the job along; `status` reads the job back.

One job acts at a time. A job waits first (`--when`): `now` not at all, `idle` until no request
runs or waits on the engine (server/setup/idle.py, the Setup Guide's rule) for at most its
deadline, `at=<time>` until that time and then as idle. A job that runs out of deadline fails with
nothing changed. A new lifecycle call cancels a job that still waits; while one acts, a call is
refused `busy`.

Acting: the box lock (BOX_LOCK, the holds' and the watchdog's) is taken, a hold's fresh pause file
refuses; run/remote/stopped tells ops/watchdog.sh the silence is on purpose; the engine stops
(ops/stop.sh); a settings job makes its overlay version current; the marker goes and the engine
starts (an install's bin/tandem start, else ops/start.sh) and must answer /health. An engine that
exits before it answers fails the start at once. A start that does not reach /health puts the
overlay version back that the engine last ran with, stops what is left and starts again; the job
fails and says so. A stop leaves run/remote/stopped in place until the next start. An `upgrade` job
(engine-ctl-v1 1.1) acts through server/remote/upgrade.py: the previous tree kept, the stop, the
target's install.sh --update, the start, and a rollback to the previous tree when one fails.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from server.remote import overlay, upgrade
from server.remote.journal import utc
from server.remote.paths import ROOT as CODE, Tree
from server.setup.idle import IDLE_POLL_S, IDLE_WAIT_S, requests_in

FINAL = ("done", "failed", "cancelled", "lost")
KINDS = ("start", "stop", "restart", "settings", "mode", "restore", "upgrade")
STOP_GRACE_S = 60
STOP_TIMEOUT_S = 300
START_TIMEOUT_S = 2400
HEALTH_POLL_S = 1.0
BOX_LOCK_WAIT_S = 120
PAUSE_MAX_S = 2400
KEEP_JOBS = 50
JOB_ID = re.compile(r"^[0-9]{8}T[0-9]{6}-[0-9a-f]{6}$")


class Busy(Exception):
    def __init__(self, job: dict):
        super().__init__("another lifecycle job is acting")
        self.job = job


# ------------------------------------------------------------------------------------- the jobs
def _job_path(tree: Tree, jid: str) -> str:
    return os.path.join(tree.jobs, jid, "job.json")


def read_job(tree: Tree, jid: str) -> dict | None:
    try:
        with open(_job_path(tree, jid), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def write_job(tree: Tree, job: dict) -> None:
    overlay._write_atomic(_job_path(tree, job["id"]), json.dumps(job, indent=1, sort_keys=True) + "\n")


def _ids(tree: Tree) -> list[str]:
    try:
        return sorted((d for d in os.listdir(tree.jobs) if JOB_ID.match(d)), reverse=True)
    except OSError:
        return []


def _runner_alive(tree: Tree, jid: str) -> bool:
    """The runner holds run.lock for its life: a lock this process can take has no runner."""
    p = os.path.join(tree.jobs, jid, "run.lock")
    try:
        fd = os.open(p, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        os.close(fd)


def latest(tree: Tree) -> dict | None:
    """The newest job, a runner that died marked lost (a job younger than 30 s may still be starting)."""
    for jid in _ids(tree):
        job = read_job(tree, jid)
        if job is None:
            continue
        if job["state"] not in FINAL and time.time() - job.get("created_s", 0) > 30 and \
                not _runner_alive(tree, jid):
            job.update(state="lost", ended_at=utc(), message="the job's runner is gone (a reboot or a kill)")
            write_job(tree, job)
        return job
    return None


def brief(job: dict | None) -> dict | None:
    if not job:
        return None
    keys = ("id", "kind", "state", "phase", "when", "created_at", "ended_at", "message", "overlay",
            "restored", "waiting_for", "upgrade", "rolled_back")
    return {k: job.get(k) for k in keys if job.get(k) is not None}


class _Lock:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        self.fh = open(path, "a")

    def __enter__(self):
        fcntl.flock(self.fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.fh, fcntl.LOCK_UN)
        self.fh.close()


def _lock(tree: Tree) -> _Lock:
    return _Lock(os.path.join(tree.remote, "lifecycle.lock"))


def create(tree: Tree, kind: str, when: dict | None = None, deadline: int | None = None,
           target: dict | None = None, note: str | None = None, spawn=True, extra: dict | None = None) -> dict:
    """A new job, its runner started; cancels a job that still waits, Busy while one acts. `extra`
    goes into the job as it is (an upgrade's record)."""
    assert kind in KINDS
    when = dict(when or {"mode": "now"})
    if when["mode"] != "now":
        when["deadline_s"] = deadline or IDLE_WAIT_S
    with _lock(tree):
        cur = latest(tree)
        if cur and cur["state"] not in FINAL:
            if cur["state"] != "waiting":
                raise Busy(brief(cur))
            cur.update(state="cancelled", ended_at=utc(), message=f"replaced by a {kind} call")
            write_job(tree, cur)
        now = time.time()
        jid = time.strftime("%Y%m%dT%H%M%S", time.gmtime(now)) + "-" + secrets.token_hex(3)
        job = {"id": jid, "kind": kind, "state": "waiting", "phase": "waiting", "when": when,
               "created_at": utc(now), "created_s": now, "ended_at": None, "message": None, "log": []}
        if target is not None:
            m = overlay.manifest(tree)
            job["overlay"] = {"from": m["current"], "values": target, "note": note or kind}
        job.update(extra or {})
        os.makedirs(os.path.join(tree.jobs, jid), mode=0o700, exist_ok=True)
        write_job(tree, job)
        _prune(tree)
    if spawn:
        _spawn(tree, jid)
    return job


def _prune(tree: Tree) -> None:
    import shutil
    for jid in _ids(tree)[KEEP_JOBS:]:
        d = os.path.join(tree.jobs, jid)
        if os.path.basename(os.path.dirname(d)) == "jobs" and JOB_ID.match(jid):
            shutil.rmtree(d, ignore_errors=True)


def _spawn(tree: Tree, jid: str) -> int:
    d = os.path.join(tree.jobs, jid)
    env = {k: v for k, v in os.environ.items()
           if k in ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "BOX_LOCK", "USER", "LOGNAME", "ENGINE_PROC",
                    "STATUS_PY", "TANDEM_SKIP_MEMCHECK")}
    env["PYTHONPATH"] = CODE
    with open(os.path.join(d, "runner.out"), "ab") as out:
        p = subprocess.Popen([sys.executable, "-s", "-m", "server.remote.lifecycle", "run", d], cwd=tree.root,
                             env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                             start_new_session=True, close_fds=True)
    return p.pid


# ------------------------------------------------------------------------------------- the runner
def _http(url: str, timeout: float = 5.0) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except ValueError:
            return exc.code, {}
    except (OSError, ValueError):
        return 0, {}


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class Runner:
    """One job's steps. `cmd(argv, timeout) -> rc`, `http(url) -> (code, body)`, `clock` and
    `sleep` are injectable (tests/test_remote_lifecycle.py)."""

    def __init__(self, tree: Tree, jid: str, *, cmd=None, http=_http, clock=time.time, sleep=time.sleep,
                 port: int | None = None):
        self.tree, self.jid = tree, jid
        self.real = cmd is None
        self.cmd = cmd or self._run
        self.http, self.clock, self.sleep = http, clock, sleep
        self.job = read_job(tree, jid)
        self.port = port

    # -- bookkeeping
    def save(self, **kv) -> None:
        self.job.update(kv)
        write_job(self.tree, self.job)

    def log(self, text: str) -> None:
        line = f"{utc(self.clock())} [{self.jid}] {text}"
        self.job["log"] = (self.job.get("log") or [])[-19:] + [line]
        write_job(self.tree, self.job)
        try:
            os.makedirs(self.tree.logs, exist_ok=True)
            with open(self.tree.launcher_log, "a") as fh:
                fh.write(line + "\n")
        except OSError:
            pass

    def cancelled(self) -> bool:
        j = read_job(self.tree, self.jid) or {}
        return j.get("state") == "cancelled"

    def _run(self, argv: list[str], timeout: float, env: dict | None = None) -> int:
        """`argv` in its own session, its output to logs/launcher.log; 124 after the timeout."""
        os.makedirs(self.tree.logs, exist_ok=True)
        with open(self.tree.launcher_log, "ab") as out:
            p = subprocess.Popen(argv, cwd=self.tree.root, stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                                 start_new_session=True, close_fds=True, env=env)
            return self._wait(p, timeout)

    def _wait(self, p, timeout: float) -> int:
        try:
            return p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._kill(p)
            return 124

    @staticmethod
    def _kill(p) -> None:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            p.kill()
        p.wait()

    def health(self) -> tuple[int, dict]:
        return self.http(f"http://127.0.0.1:{self.port}/health")

    # -- the steps
    def wait(self) -> bool:
        """The job's --when. False: cancelled, or the deadline passed (the job is failed then)."""
        when = self.job["when"]
        if when["mode"] == "now":
            return True
        if when["mode"] == "at":
            at = datetime.fromisoformat(when["at"].replace("Z", "+00:00")).timestamp()
            self.save(waiting_for="the time")
            while self.clock() < at:
                if self.cancelled():
                    return False
                self.sleep(min(30.0, max(0.05, at - self.clock())))
        t_end = self.clock() + when.get("deadline_s", IDLE_WAIT_S)
        shown = None
        while True:
            if self.cancelled():
                return False
            code, h = self.health()
            n = requests_in(h) if code == 200 else 0
            if n == 0:
                return True
            if n != shown:
                shown = n
                self.save(waiting_for=f"{n} request{'s' if n != 1 else ''} to finish")
            if self.clock() > t_end:
                self.fail(f"{n} request{'s' if n != 1 else ''} still ran after the deadline "
                          f"({when.get('deadline_s', IDLE_WAIT_S)} s); nothing changed")
                return False
            self.sleep(IDLE_POLL_S)

    def fail(self, message: str, **kv) -> None:
        self.log("failed: " + message)
        self.save(state="failed", phase="failed", ended_at=utc(self.clock()), message=message, **kv)

    def _box_lock(self):
        path = os.environ.get("BOX_LOCK") or os.path.expanduser("~/.qwen38-box.flock")
        if not os.path.exists(path):
            return None
        fh = open(path)
        t_end = self.clock() + BOX_LOCK_WAIT_S
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fh
            except OSError:
                if self.clock() > t_end:
                    fh.close()
                    raise RuntimeError(f"the box lock {path} is held (a hold runs): nothing changed")
                self.sleep(2.0)

    def _paused(self) -> bool:
        p = os.path.join(self.tree.root, ".watchdog.off")
        return os.path.exists(p) and time.time() - os.stat(p).st_mtime < PAUSE_MAX_S

    def _mark(self, on: bool, reason: str = "") -> None:
        if on:
            os.makedirs(self.tree.remote, mode=0o700, exist_ok=True)
            with open(self.tree.stopped, "w") as fh:
                json.dump({"job": self.jid, "kind": self.job["kind"], "at": utc(), "reason": reason}, fh)
        else:
            try:
                os.unlink(self.tree.stopped)
            except FileNotFoundError:
                pass

    def stop(self) -> bool:
        self.save(phase="stopping")
        self.log("stop: ops/stop.sh")
        rc = self.cmd(["bash", os.path.join(self.tree.ops, "stop.sh"), str(STOP_GRACE_S)], STOP_TIMEOUT_S,
                      dict(os.environ, STATUS_REASON=f"tandem remote {self.job['kind']}"))
        if rc != 0:
            self.log(f"stop.sh exit {rc}")
        return rc == 0

    def start(self) -> bool:
        """The engine starts and answers /health; an engine that exits on the way fails at once."""
        self.save(phase="starting")
        self._mark(False)
        argv = ["bash", self.tree.control, "start"] if self.tree.control and os.path.isfile(self.tree.control) \
            else ["bash", os.path.join(self.tree.ops, "start.sh")]
        self.log(f"start: {' '.join(os.path.relpath(a, self.tree.base) if a.startswith('/') else a for a in argv)}")
        before = self._pid_now()
        env = dict(os.environ, START_REASON=f"tandem remote {self.job['kind']}")
        if not self.real:                                          # a test's command
            rc = self.cmd(argv, START_TIMEOUT_S, env)
            return rc == 0 and self.health()[0] == 200
        os.makedirs(self.tree.logs, exist_ok=True)
        with open(self.tree.launcher_log, "ab") as out:
            p = subprocess.Popen(argv, cwd=self.tree.root, stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                                 start_new_session=True, close_fds=True, env=env)
            t_end = time.monotonic() + START_TIMEOUT_S
            while True:
                rc = p.poll()
                if self.health()[0] == 200:
                    if rc is None:
                        self._wait(p, 600)                           # its after-health steps
                    return True
                pid = self._new_pid(before)
                if pid and not _alive(pid):
                    self.log(f"the engine (pid {pid}) exited before /health")
                    if p.poll() is None:
                        self._kill(p)
                    return False
                if rc is not None and not (pid and _alive(pid)):
                    self.log(f"the start exited {rc} and no engine runs")
                    return False
                if time.monotonic() > t_end:
                    self.log(f"no /health after {START_TIMEOUT_S} s")
                    if p.poll() is None:
                        self._kill(p)
                    return False
                time.sleep(HEALTH_POLL_S)

    def _pid_now(self) -> int | None:
        try:
            with open(self.tree.pid_file) as fh:
                return int(fh.read().strip() or 0) or None
        except (OSError, ValueError):
            return None

    def _new_pid(self, before: int | None) -> int | None:
        """The engine this start launched: the pid file's pid once it is not the one from before."""
        pid = self._pid_now()
        return pid if pid != before else None

    def main(self) -> int:
        lk = open(os.path.join(self.tree.jobs, self.jid, "run.lock"), "a")
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            return self._main()
        except Exception as exc:                                    # noqa: BLE001
            self.fail(f"the job stopped: {type(exc).__name__}: {exc}")
            return 1

    def _main(self) -> int:
        if self.port is None:
            env, _ = overlay.resolve(self.tree, ["PORT"])
            self.port = int(env.get("PORT") or 8000)
        if not self.wait():
            if self.job["state"] != "failed":
                self.job = read_job(self.tree, self.jid)
            return 1
        with _lock(self.tree):
            if self.cancelled():
                return 1
            self.save(state="acting", phase="acting", waiting_for=None)
        if self._paused():
            self.fail("a hold owns the board (.watchdog.off): nothing changed")
            return 1
        box = self._box_lock()
        try:
            return self.act()
        finally:
            if box:
                box.close()

    def act(self) -> int:
        kind = self.job["kind"]
        if kind == "upgrade":
            rc = upgrade.act(self)
            if rc == 0:
                overlay.mark_good(self.tree)
            return rc
        if kind == "stop":
            self._mark(True, "tandem remote stop")
            if not self.stop():
                self.fail("ops/stop.sh failed (logs/launcher.log)")
                return 1
            return self.done("stopped; ops/watchdog.sh leaves it down until the next start")
        if kind == "start" and self.health()[0] == 200:
            self._mark(False)
            overlay.mark_good(self.tree)
            return self.done("already healthy")
        target = self.job.get("overlay")
        before = overlay.manifest(self.tree)
        if target and before["current"] != target["from"]:
            self.fail(f"the overlay changed since the plan (version {before['current']}, the plan's "
                      f"{target['from']}): nothing changed")
            return 1
        if kind != "start":
            self._mark(True, f"tandem remote {kind}")
            if not self.stop():
                self._mark(False)
                self.fail("ops/stop.sh failed (logs/launcher.log): nothing changed")
                return 1
        if target:
            n = overlay.add_version(self.tree, target["values"], target["note"])
            overlay.make_current(self.tree, n)
            self.job["overlay"]["to"] = n
            self.log(f"overlay: version {n} is current (was {target['from']})")
        if self.start():
            overlay.mark_good(self.tree)
            return self.done("healthy")
        # back to the version the engine last ran with
        m = overlay.manifest(self.tree)
        back = target["from"] if target else m.get("good")
        if back is None or back == m["current"]:
            self.fail("the engine did not reach /health (logs/engine.log, logs/launcher.log)")
            return 1
        self.save(phase="restoring")
        self.log(f"restore: overlay version {back} (the engine's last good one), start again")
        overlay.make_current(self.tree, back)
        self.stop()
        ok = self.start()
        if ok:
            overlay.mark_good(self.tree)
        self.fail(f"the engine did not reach /health with overlay version {m['current']}; version {back} "
                  f"is back and the engine {'answers' if ok else 'did not start with it either'}",
                  restored={"version": back, "healthy": ok})
        return 1

    def done(self, message: str) -> int:
        self.log(message)
        self.save(state="done", phase="done", ended_at=utc(self.clock()), message=message)
        return 0


def main(argv: list[str] | None = None) -> int:
    a = list(sys.argv[1:] if argv is None else argv)
    if len(a) == 2 and a[0] == "run":
        d = os.path.realpath(a[1])
        jobs = os.path.dirname(d)
        remote = os.path.dirname(jobs)
        if os.path.basename(jobs) == "jobs" and os.path.basename(remote) == "remote" and \
                os.path.basename(os.path.dirname(remote)) == "run":
            tree = Tree(os.path.dirname(os.path.dirname(remote)))
            return Runner(tree, os.path.basename(d)).main()
    print("usage: python -m server.remote.lifecycle run <tree>/run/remote/jobs/<id>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
