"""The engines on a TandemNode (SRV-725): the list, their health, start, stop and restart, and the
engine routes relayed.

    engines = Engines.load(path, tandem_dir)    engines.json, else the install's engine, else :8000
    engines.view()                              [{id, kind, port, managed, state, health, job}]
    engines.act(id, 'start' | 'stop' | 'restart', who)   a job (202), or EngineError 404/409
    engines.relay(id, path, query)              an allowed engine route: (status, headers, stream)

ENGINES.JSON (`<state dir>/engines.json`), a list under `engines`:

    {"engines": [{"id": "engine", "kind": "tandemllm", "port": 8000,
                  "start": ["bash", "/opt/tandem/bin/tandem", "start"],
                  "stop": ["bash", "/opt/tandem/bin/tandem", "stop"],
                  "restart": ["bash", "/opt/tandem/bin/tandem", "restart"],
                  "cwd": "/opt/tandem", "start_timeout_s": 1800}]}

An engine without a `start` command is listed, its health and routes relayed, but never started,
stopped or restarted here (409 `not_managed`): an engine someone else runs stays theirs. Without
the file: the install's engine when `--tandem-dir` names one (`run/ops/serve.env`'s PORT, its
`bin/tandem` start, stop and restart), else one TandemLLM engine on :8000, not managed.

LIFECYCLE. One job per engine at a time (409 `busy`). `start` runs the start command in its own
session (its output in `<state dir>/logs/<id>.log`) and waits for `/health` to answer; a command
that exits non-zero first fails the job; one that stays (the engine itself in the foreground) is
kept and its process group is what `stop` ends when there is no stop command (SIGTERM, then SIGKILL
after the grace). `restart` runs its own command, else stop then start. Each action is a line in the
agent's log with the caller.

RELAY. GET only, these routes: /health, /v1/models, /v1/dashboard/live, /v1/dashboard/system,
/v1/dashboard/logs, /metrics. The agent calls the engine on loopback without proxy headers, where
the engine trusts its caller (server/auth.py); an event stream is passed on as it arrives.
"""

from __future__ import annotations

import http.client
import json
import os
import signal
import subprocess
import threading
import time

RELAY = ("/health", "/v1/models", "/v1/dashboard/live", "/v1/dashboard/system",
         "/v1/dashboard/logs", "/metrics")
HEALTH_TIMEOUT_S = 2.0
STOP_GRACE_S = 30.0
COMMAND_TIMEOUT_S = 600.0
KINDS = ("tandemllm", "vllm", "sglang", "generic")


class EngineError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message

    def body(self) -> dict:
        return {"error": {"type": self.code, "message": self.message}}


def _argv(v, what: str):
    if v is None:
        return None
    if not isinstance(v, list) or not v or not all(isinstance(x, str) and x for x in v):
        raise ValueError(f"{what}: a command is a list of strings")
    return list(v)


class Engine:
    def __init__(self, doc: dict, logs_dir: str):
        eid = doc.get("id")
        if not isinstance(eid, str) or not eid or "/" in eid or len(eid) > 64:
            raise ValueError(f"an engine id is 1 to 64 characters without '/': {eid!r}")
        port = doc.get("port")
        if not isinstance(port, int) or isinstance(port, bool) or not 0 < port < 65536:
            raise ValueError(f"{eid}: port is a whole number from 1 to 65535")
        self.id, self.port = eid, port
        self.kind = doc.get("kind") or "tandemllm"
        if self.kind not in KINDS:
            raise ValueError(f"{eid}: kind is one of {', '.join(KINDS)}")
        self.start_cmd = _argv(doc.get("start"), f"{eid}.start")
        self.stop_cmd = _argv(doc.get("stop"), f"{eid}.stop")
        self.restart_cmd = _argv(doc.get("restart"), f"{eid}.restart")
        self.cwd = os.path.expanduser(doc["cwd"]) if doc.get("cwd") else None
        self.env = {str(k): str(v) for k, v in (doc.get("env") or {}).items()}
        self.start_timeout = float(doc.get("start_timeout_s") or 1800)
        self.log_path = os.path.join(logs_dir, f"{eid}.log")
        self.proc: subprocess.Popen | None = None       # the process this agent started
        self.job: dict | None = None
        self.status_port: int | None = None             # OPS-175's responder, from /health
        self.lock = threading.Lock()

    @property
    def managed(self) -> bool:
        return self.start_cmd is not None

    def url(self, path: str = "") -> str:
        return f"http://127.0.0.1:{self.port}{path}"


class Engines:
    def __init__(self, engines: list[Engine], *, log=lambda m: print(m, flush=True),
                 clock=time.time, poll_s: float = 1.0):
        self.list = engines
        self.by_id = {e.id: e for e in engines}
        self.log, self.clock, self.poll_s = log, clock, poll_s

    @classmethod
    def load(cls, state_dir: str, tandem_dir: str | None = None, **kw) -> "Engines":
        logs = os.path.join(state_dir, "logs")
        path = os.path.join(state_dir, "engines.json")
        if os.path.exists(path):
            with open(path) as f:
                doc = json.load(f)
            items = doc.get("engines") if isinstance(doc, dict) else None
            if not isinstance(items, list):
                raise ValueError(f"{path}: {{\"engines\": [...]}}")
            engines = [Engine(d, logs) for d in items]
            if len({e.id for e in engines}) != len(engines):
                raise ValueError(f"{path}: two engines with one id")
        else:
            engines = [Engine(default_engine(tandem_dir), logs)]
        return cls(engines, **kw)

    def get(self, eid: str) -> Engine:
        e = self.by_id.get(eid)
        if e is None:
            raise EngineError(404, "not_found", f"no engine {eid} on this node")
        return e

    # ------------------------------------------------------------------ what runs
    def health(self, e: Engine) -> dict | None:
        """The engine's /health in short, None when nothing answers on its port."""
        try:
            st, _, body = _get(e.port, "/health", HEALTH_TIMEOUT_S)
        except OSError:
            return None
        try:
            doc = json.loads(body)
        except ValueError:
            doc = {}
        if not isinstance(doc, dict):
            doc = {}
        if isinstance(doc.get("status_port"), int):
            e.status_port = doc["status_port"]
        w = doc.get("weights") if isinstance(doc.get("weights"), dict) else {}
        return {"http": st, "status": doc.get("status"), "version": doc.get("version"),
                "model": doc.get("model"), "mode": doc.get("mode"),
                "weights": w.get("name"), "uptime_s": doc.get("uptime_s")}

    def view_one(self, e: Engine) -> dict:
        h = self.health(e)
        job = e.job
        if job is not None and job["state"] == "running":
            state = {"start": "starting", "stop": "stopping", "restart": "restarting"}[
                job["action"]]
        elif h is None:
            state = "down"
        elif h["http"] == 200 and h["status"] in ("ok", "busy"):
            state = "up"
        else:
            state = "unhealthy"
        pid = e.proc.pid if e.proc is not None and e.proc.poll() is None else None
        return {"id": e.id, "kind": e.kind, "port": e.port, "managed": e.managed,
                "state": state, "health": h, "pid": pid, "job": dict(job) if job else None}

    def view(self) -> list[dict]:
        return [self.view_one(e) for e in self.list]

    # ------------------------------------------------------------------ lifecycle
    def act(self, eid: str, action: str, who: str) -> dict:
        e = self.get(eid)
        if action not in ("start", "stop", "restart"):
            raise EngineError(404, "not_found", f"no action {action}")
        if not e.managed:
            raise EngineError(409, "not_managed",
                              f"{eid} is not managed by this node: engines.json gives it no "
                              f"start command, so it is never started or stopped here")
        with e.lock:
            if e.job is not None and e.job["state"] == "running":
                raise EngineError(409, "busy", f"{eid}: a {e.job['action']} runs")
            e.job = {"id": f"{eid}-{int(self.clock() * 1000)}", "action": action,
                     "state": "running", "who": who, "started_at": self.clock(),
                     "ended_at": None, "rc": None, "message": None}
            job = e.job
        self.log(f"[node] {action} {eid} (port {e.port}) by {who}")
        threading.Thread(target=self._run_job, args=(e, job), name=f"engine-{action}",
                         daemon=True).start()
        return dict(job)

    def _run_job(self, e: Engine, job: dict) -> None:
        try:
            if job["action"] == "start":
                msg = self._start(e)
            elif job["action"] == "stop":
                msg = self._stop(e)
            elif e.restart_cmd is not None:
                rc = self._command(e, e.restart_cmd)
                if rc != 0:
                    raise EngineError(500, "failed", f"the restart command exited {rc}")
                msg = self._wait_health(e, None)
            else:
                self._stop(e)
                msg = self._start(e)
            job.update(state="done", message=msg)
        except EngineError as exc:
            job.update(state="failed", message=exc.message)
        except Exception as exc:                                     # noqa: BLE001
            job.update(state="failed", message=f"{type(exc).__name__}: {exc}")
        job["ended_at"] = self.clock()
        self.log(f"[node] {job['action']} {e.id}: {job['state']}"
                 f"{' · ' + job['message'] if job['message'] else ''}")

    def _popen(self, e: Engine, argv: list[str]) -> subprocess.Popen:
        os.makedirs(os.path.dirname(e.log_path), exist_ok=True)
        out = open(e.log_path, "ab")
        try:
            return subprocess.Popen(argv, cwd=e.cwd, env={**os.environ, **e.env},
                                    stdin=subprocess.DEVNULL, stdout=out,
                                    stderr=subprocess.STDOUT, start_new_session=True)
        finally:
            out.close()

    def _command(self, e: Engine, argv: list[str]) -> int:
        p = self._popen(e, argv)
        try:
            return p.wait(timeout=COMMAND_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            _end_group(p, 5.0)
            return -1

    def _up(self, e: Engine) -> bool:
        h = self.health(e)
        return h is not None and h["http"] == 200 and h["status"] in ("ok", "busy")

    def _start(self, e: Engine) -> str:
        if self._up(e):
            return "already up"
        p = self._popen(e, e.start_cmd)
        msg = self._wait_health(e, p)
        if p.poll() is None:
            e.proc = p                      # the engine itself, in the foreground: ours to stop
        return msg

    def _wait_health(self, e: Engine, p: subprocess.Popen | None) -> str:
        t0 = time.monotonic()
        while time.monotonic() - t0 < e.start_timeout:
            if self._up(e):
                return f"up after {time.monotonic() - t0:.1f} s"
            if p is not None and p.poll() not in (None, 0):
                raise EngineError(500, "failed", f"the start command exited {p.returncode}; "
                                                 f"its output: {e.log_path}")
            time.sleep(self.poll_s)
        raise EngineError(504, "timeout", f"no healthy /health after {e.start_timeout:g} s")

    def _stop(self, e: Engine) -> str:
        if e.stop_cmd is not None:
            rc = self._command(e, e.stop_cmd)
            if rc != 0:
                raise EngineError(500, "failed", f"the stop command exited {rc}")
        elif e.proc is not None and e.proc.poll() is None:
            _end_group(e.proc, STOP_GRACE_S)
        elif self.health(e) is not None:
            raise EngineError(409, "not_ours", f"{e.id} has no stop command and was not started "
                                               f"by this agent")
        else:
            return "already down"
        e.proc = None
        t0 = time.monotonic()
        while self.health(e) is not None and time.monotonic() - t0 < STOP_GRACE_S:
            time.sleep(self.poll_s)
        return "down" if self.health(e) is None else "the port still answers"

    # ------------------------------------------------------------------ relay
    def relay(self, eid: str, path: str, query: str, timeout: float = 30.0):
        """(status, content type, connection, response) for an allowed GET; the caller reads
        the response and closes the connection."""
        e = self.get(eid)
        if path not in RELAY:
            raise EngineError(404, "not_found", f"{path} is not relayed (one of "
                                                f"{', '.join(RELAY)})")
        return open_get(e.port, path + (f"?{query}" if query else ""), timeout)


def default_engine(tandem_dir: str | None) -> dict:
    """The install's engine (its serve.env's PORT and bin/tandem), else :8000 unmanaged."""
    if tandem_dir:
        env = os.path.join(os.path.expanduser(tandem_dir), "run", "ops", "serve.env")
        port = None
        try:
            with open(env) as f:
                for line in f:
                    if line.startswith("PORT="):
                        port = int(line.split("=", 1)[1].strip().strip('"\''))
        except (OSError, ValueError):
            port = None
        if port:
            ctl = os.path.join(os.path.expanduser(tandem_dir), "bin", "tandem")
            return {"id": "engine", "kind": "tandemllm", "port": port,
                    "start": ["bash", ctl, "start"], "stop": ["bash", ctl, "stop"],
                    "restart": ["bash", ctl, "restart"]}
    return {"id": "engine", "kind": "tandemllm", "port": 8000}


def open_get(port: int, target: str, timeout: float):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("GET", target, headers={"Accept": "application/json, text/event-stream, "
                                                       "text/plain"})
        sock = conn.sock
        r = conn.getresponse()
        r.sock = sock                       # http.client lets go of it on Connection: close
    except Exception:
        conn.close()
        raise
    return r.status, r.getheader("Content-Type") or "application/json", conn, r


def _get(port: int, target: str, timeout: float) -> tuple[int, str, bytes]:
    st, ctype, conn, r = open_get(port, target, timeout)
    try:
        return st, ctype, r.read(4 * 1024 * 1024)
    finally:
        conn.close()


def _end_group(p: subprocess.Popen, grace: float) -> None:
    """SIGTERM to the process's group, SIGKILL after `grace` seconds."""
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 5.0)):
        try:
            os.killpg(p.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            p.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue
