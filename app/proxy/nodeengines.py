"""The engines on a TandemNode (SRV-725): the list, their health, start, stop and restart, and the
engine routes relayed.

    engines = Engines.load(path, tandem_dir)    engines.json, else the install's engine, else :8000
    engines.view()                              [{id, kind, port, managed, state, health, job}]
    engines.act(id, 'start' | 'stop' | 'restart', who)   a job (202), or EngineError 404/409
    engines.relay(id, path, query)              an allowed engine route: (status, headers, stream)
    engines.set_stage(id, doc, who)             one stage of a split model on this engine (below)
    engines.clear_stage(id, who)                the engine serves the whole model again

ENGINES.JSON (`<state dir>/engines.json`), a list under `engines`:

    {"engines": [{"id": "engine", "kind": "tandemllm", "port": 8000,
                  "start": ["bash", "/opt/tandem/bin/tandem", "start"],
                  "stop": ["bash", "/opt/tandem/bin/tandem", "stop"],
                  "restart": ["bash", "/opt/tandem/bin/tandem", "restart"],
                  "cwd": "/opt/tandem", "start_timeout_s": 1800}]}

HEALTH. `health` is the engine's /health in short. A TandemLLM engine's adds the engine API it
speaks (`engine_api`, `api_assumed`, `api_ok`, `api_why`; proxy/engineapi.py): one on another
major is `unhealthy`, whatever its status, and `api_why` names the major this TandemApp needs.

An engine without a `start` command is listed, its health and routes relayed, but never started,
stopped or restarted here (409 `not_managed`): an engine someone else runs stays theirs. Without
the file: the install's engine when `--tandem-dir` names one (`run/ops/serve.env`'s PORT, its
`bin/tandem` start, stop and restart), else one TandemLLM engine on :8000, not managed. A virtual
node (`--virtual`, a second agent on one machine) has no engine without the file: the machine's
own engine is the machine's node's, never restarted or relayed through a virtual one.

LIFECYCLE. One job per engine at a time (409 `busy`). `start` runs the start command in its own
session (its output in `<state dir>/logs/<id>.log`) and waits for `/health` to answer; a command
that exits non-zero first fails the job; one that stays (the engine itself in the foreground) is
kept and its process group is what `stop` ends when there is no stop command (SIGTERM, then SIGKILL
after the grace). `restart` runs its own command, else stop then start. Each action is a line in the
agent's log with the caller.

A STAGE (SRV-817). `set_stage` makes a managed engine one stage of a split model at its next
start: {stage "LO:HI", listen or peer "IP:PORT" (an IP literal, never every interface), key (the
link key, base64, at least 32 bytes), caches on|off, slots, window?, side_window?, snapshots_gb?,
cache_gb?, need_gib?}. The key is written to `<state dir>/stage/<id>.key` (mode 0600, its folder
0700) and never anywhere else: not to the log, a job, an answer or an env file. The engine is told
the key FILE's path only (TANDEM_LINK_KEY) with the stage's other keys (contracts/engine-api-v1/
node/control.json `stage`): in a marked block of the engine's `local_env` file (an install's
`run/local.env`, read after its template), else in the start command's environment. `clear_stage`
removes the block, the env and the key file. The view says `stage` without the key: {stage, role,
listen or peer, caches, slots, key: "set"}.

RELAY. GET only, these routes: /health, /v1/models, /v1/dashboard/live, /v1/dashboard/system,
/v1/dashboard/logs, /metrics, /v1/replica/identity (a Replicas member's identity, SRV-741). The agent calls the engine on loopback without proxy headers, where
the engine trusts its caller (server/auth.py); an event stream is passed on as it arrives.
"""

from __future__ import annotations

import base64
import binascii
import http.client
import ipaddress
import json
import os
import re
import signal
import subprocess
import threading
import time

from proxy import engineapi

RELAY = ("/health", "/v1/models", "/v1/dashboard/live", "/v1/dashboard/system",
         "/v1/dashboard/logs", "/metrics", "/v1/replica/identity")
HEALTH_TIMEOUT_S = 2.0
STOP_GRACE_S = 30.0
COMMAND_TIMEOUT_S = 600.0
KINDS = ("tandemllm", "vllm", "sglang", "generic")
STAGE_KEY_MIN = 32
STAGE_BEGIN = "# >>> TandemNode stage (written by TandemNode: do not edit) >>>"
STAGE_END = "# <<< TandemNode stage <<<"
STAGE_FIELDS = ("stage", "listen", "peer", "key", "caches", "slots", "window", "side_window",
                "snapshots_gb", "cache_gb", "need_gib")


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
    def __init__(self, doc: dict, logs_dir: str, stage_dir: str | None = None):
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
        # SRV-817: a stage's files (the link key, its env) in the state dir; the engine's own
        # env file when it has one (an install's run/local.env)
        self.stage_dir = stage_dir or os.path.join(logs_dir, "stage")
        self.local_env = os.path.expanduser(doc["local_env"]) if doc.get("local_env") else None
        self.stage_env: dict[str, str] = {}
        self.stage: dict | None = None                  # what the view says of it (no key)
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
        self.on_done = None             # (engine, job) after each job: the chat watcher's kick
        for e in engines:
            _load_stage(e)

    @classmethod
    def load(cls, state_dir: str, tandem_dir: str | None = None, virtual: str | None = None,
             **kw) -> "Engines":
        logs = os.path.join(state_dir, "logs")
        path = os.path.join(state_dir, "engines.json")
        if os.path.exists(path):
            with open(path) as f:
                doc = json.load(f)
            items = doc.get("engines") if isinstance(doc, dict) else None
            if not isinstance(items, list):
                raise ValueError(f"{path}: {{\"engines\": [...]}}")
            engines = [Engine(d, logs, os.path.join(state_dir, "stage")) for d in items]
            if len({e.id for e in engines}) != len(engines):
                raise ValueError(f"{path}: two engines with one id")
        elif virtual:
            engines = []
        else:
            engines = [Engine(default_engine(tandem_dir), logs,
                              os.path.join(state_dir, "stage"))]
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
        # SRV-747: the engine API it speaks (proxy/engineapi.py); None for another kind of engine
        api = engineapi.read(doc, e.kind) or {}
        out = {"http": st, "status": doc.get("status"), "version": doc.get("version"),
               "model": doc.get("model"), "mode": doc.get("mode"),
               "weights": w.get("name"), "uptime_s": doc.get("uptime_s"),
               "engine_api": api.get("engine_api"), "api_assumed": api.get("assumed"),
               "api_ok": api.get("ok"), "api_why": api.get("why")}
        if isinstance(doc.get("stage"), dict):
            out["stage"] = stage_short(doc["stage"])     # SRV-817: a stage of a split model
        return out

    def view_one(self, e: Engine) -> dict:
        h = self.health(e)
        job = e.job
        if job is not None and job["state"] == "running":
            state = {"start": "starting", "stop": "stopping", "restart": "restarting"}[
                job["action"]]
        elif h is None:
            state = "down"
        elif h["http"] == 200 and h["api_ok"] is not False and (
                h["status"] in ("ok", "busy") or (h["status"] == "degraded" and h.get("stage"))):
            state = "up"                    # a tail whose link is down still runs: `degraded`
        else:
            state = "unhealthy"
        pid = e.proc.pid if e.proc is not None and e.proc.poll() is None else None
        return {"id": e.id, "kind": e.kind, "port": e.port, "managed": e.managed,
                "state": state, "health": h, "pid": pid, "job": dict(job) if job else None,
                "stage": dict(e.stage) if e.stage else None}

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
        if self.on_done is not None:
            try:
                self.on_done(e, dict(job))
            except Exception as exc:                                 # noqa: BLE001
                self.log(f"[node] after {job['action']} {e.id}: {type(exc).__name__}: {exc}")

    def _popen(self, e: Engine, argv: list[str]) -> subprocess.Popen:
        os.makedirs(os.path.dirname(e.log_path), exist_ok=True)
        out = open(e.log_path, "ab")
        try:
            return subprocess.Popen(argv, cwd=e.cwd, env={**os.environ, **e.env, **e.stage_env},
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
                                                 f"its output: {e.log_path}{_last_line(e)}")
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

    # ------------------------------------------------------------------ a stage (SRV-817)
    def set_stage(self, eid: str, doc, who: str) -> dict:
        """This engine is one stage of a split model at its next start. The key goes to its
        file and nowhere else; the answer is the engine's view."""
        e = self.get(eid)
        if not e.managed:
            raise EngineError(409, "not_managed", f"{eid} is not managed by this node: a stage "
                                                  f"is started by its node's agent")
        cfg, key = check_stage(doc)
        with e.lock:
            if e.job is not None and e.job["state"] == "running":
                raise EngineError(409, "busy", f"{eid}: a {e.job['action']} runs")
            os.makedirs(e.stage_dir, mode=0o700, exist_ok=True)
            os.chmod(e.stage_dir, 0o700)
            kpath = os.path.join(e.stage_dir, f"{e.id}.key")
            _write_secret(kpath, key)
            env = {"TANDEM_STAGE": cfg["stage"], "TANDEM_LINK_KEY": kpath,
                   "TANDEM_STAGE_CACHES": cfg["caches"], "QSE_PARALLEL": str(cfg["slots"])}
            env["TANDEM_LINK_LISTEN" if "listen" in cfg else "TANDEM_LINK_PEER"] = \
                cfg.get("listen") or cfg["peer"]
            if cfg.get("window"):
                env["MAX_LEN"] = str(cfg["window"])
            if cfg.get("side_window"):
                env["QSE_SIDE_MAX_LEN"] = str(cfg["side_window"])
            if cfg["role"] == "front":
                env["QSE_DISK_CACHE"] = "off"
                if cfg["caches"] == "on":
                    if cfg.get("snapshots_gb") is not None:
                        env["TANDEM_STAGE_SNAPSHOTS_GB"] = f"{cfg['snapshots_gb']:g}"
                    if cfg.get("cache_gb"):
                        cdir = os.path.join(e.stage_dir, f"{e.id}-cache")
                        os.makedirs(cdir, mode=0o700, exist_ok=True)
                        env["TANDEM_STAGE_CACHE_DIR"] = cdir
                        env["TANDEM_STAGE_CACHE_GB"] = f"{cfg['cache_gb']:g}"
            if cfg.get("need_gib"):
                env["TANDEM_MIN_FREE_GIB"] = str(int(-(-cfg["need_gib"] // 1)))
            text = "".join(f"{k}={v}\n" for k, v in env.items())
            _write_secret(os.path.join(e.stage_dir, f"{e.id}.env"), text.encode())
            if e.local_env:
                _put_block(e.local_env, text)
                e.stage_env = {}
            else:
                e.stage_env = env
            e.stage = {k: cfg[k] for k in ("stage", "role", "listen", "peer", "caches", "slots")
                       if k in cfg}
            e.stage["key"] = "set"
        self.log(f"[node] stage {cfg['stage']} ({cfg['role']}) set on {eid} by {who}")
        return self.view_one(e)

    def clear_stage(self, eid: str, who: str) -> dict:
        """The engine serves the whole model at its next start: the stage's block, env and key
        file are gone. Nothing set: nothing done."""
        e = self.get(eid)
        with e.lock:
            had = e.stage is not None
            for name in (f"{e.id}.key", f"{e.id}.env"):
                try:
                    os.unlink(os.path.join(e.stage_dir, name))
                except OSError:
                    pass
            if e.local_env:
                _put_block(e.local_env, None)
            e.stage_env, e.stage = {}, None
        if had:
            self.log(f"[node] stage cleared on {eid} by {who}")
        return self.view_one(e)

    # ------------------------------------------------------------------ relay
    def relay(self, eid: str, path: str, query: str, timeout: float = 30.0):
        """(status, content type, connection, response) for an allowed GET; the caller reads
        the response and closes the connection."""
        e = self.get(eid)
        if path not in RELAY:
            raise EngineError(404, "not_found", f"{path} is not relayed (one of "
                                                f"{', '.join(RELAY)})")
        return open_get(e.port, path + (f"?{query}" if query else ""), timeout)


def stage_short(b: dict) -> dict:
    """An engine's /health `stage` block in short: what a deployment reads of it."""
    link = b.get("link") if isinstance(b.get("link"), dict) else {}
    opt = b.get("options") if isinstance(b.get("options"), dict) else {}
    peer = link.get("peer") if isinstance(link.get("peer"), dict) else None
    return {"role": b.get("role"), "lo": b.get("lo"), "hi": b.get("hi"),
            "layers": b.get("layers"), "cut": b.get("cut"), "identity": b.get("identity"),
            "options": {k: opt.get(k) for k in ("slots", "caches") if k in opt},
            "link": {"state": link.get("state"), "epoch": link.get("epoch"),
                     "reason": link.get("reason"), "transport": link.get("transport"),
                     "peer": None if peer is None else
                     {k: peer.get(k) for k in ("role", "lo", "hi", "identity")}}}


def _literal(text, what: str) -> str:
    """`IP:PORT` or `[IPv6]:PORT` with an IP literal that is not every interface."""
    m = re.fullmatch(r"\[([0-9A-Fa-f:.]+)\]:([0-9]{1,5})|([0-9.]+):([0-9]{1,5})", text) \
        if isinstance(text, str) else None
    if m is None:
        raise ValueError(f"{what} takes an IP literal and a port (IP:PORT, [IPv6]:PORT)")
    host, port = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError(f"{what} takes an IP literal, not {host!r}") from None
    if ip.is_unspecified:
        raise ValueError(f"{what}: {host} is every interface; name one IP")
    if not 0 < int(port) < 65536:
        raise ValueError(f"{what}: the port is a number from 1 to 65535")
    return text


def check_stage(doc) -> tuple[dict, bytes]:
    """(the stage's settings, the key's bytes) of a PUT body; ValueError says what is wrong,
    never with a byte of the key in it."""
    if not isinstance(doc, dict):
        raise ValueError("the body is a stage object")
    extra = [k for k in doc if k not in STAGE_FIELDS]
    if extra:
        raise ValueError(f"stage: unknown field {extra[0]!r}")
    m = re.fullmatch(r"([0-9]{1,5}):([0-9]{1,5})", doc.get("stage") or "") \
        if isinstance(doc.get("stage"), str) else None
    if m is None or int(m.group(1)) >= int(m.group(2)):
        raise ValueError("stage takes LO:HI with LO < HI (0:k the front, k:layers the tail)")
    cfg = {"stage": f"{int(m.group(1))}:{int(m.group(2))}",
           "role": "front" if int(m.group(1)) == 0 else "tail"}
    if ("listen" in doc) == ("peer" in doc):
        raise ValueError("a stage takes one of listen and peer (IP:PORT), not both")
    k = "listen" if "listen" in doc else "peer"
    cfg[k] = _literal(doc[k], k)
    try:
        key = base64.b64decode(doc.get("key") or "", validate=True) \
            if isinstance(doc.get("key"), str) else b""
    except (binascii.Error, ValueError):
        key = b""
    if len(key) < STAGE_KEY_MIN:
        raise ValueError(f"key is the link key in base64, at least {STAGE_KEY_MIN} bytes")
    cfg["caches"] = doc.get("caches", "off")
    if cfg["caches"] not in ("on", "off"):
        raise ValueError("caches is on or off")
    slots = doc.get("slots", 1)
    if not isinstance(slots, int) or isinstance(slots, bool) or not 1 <= slots <= 64:
        raise ValueError("slots is a whole number from 1 to 64")
    if cfg["caches"] == "off" and slots != 1:
        raise ValueError("a stage without caches serves one slot")
    cfg["slots"] = slots
    for name in ("window", "side_window"):
        v = doc.get(name)
        if v is not None:
            if not isinstance(v, int) or isinstance(v, bool) or v < 1:
                raise ValueError(f"{name} is a token count")
            cfg[name] = v
    for name in ("snapshots_gb", "cache_gb", "need_gib"):
        v = doc.get(name)
        if v is not None:
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v < 1 << 20:
                raise ValueError(f"{name} takes GiB (a number)")
            cfg[name] = float(v)
    if cfg["role"] == "tail" and any(k in cfg for k in ("snapshots_gb", "cache_gb")):
        raise ValueError("snapshots_gb and cache_gb are the front stage's")
    return cfg, key


def _write_secret(path: str, data: bytes) -> None:
    """A file only its owner reads (0600), replaced whole."""
    fd = os.open(path + ".part", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, data)
    finally:
        os.close(fd)
    os.replace(path + ".part", path)


def _put_block(path: str, text: str | None) -> None:
    """The marked stage block of an env file: replaced by `text`, or taken out (None). The
    rest of the file stays as it is; a file that is not there is made (0600) only to add."""
    try:
        with open(path, encoding="utf-8") as f:
            old = f.read()
    except OSError:
        old = None
    keep, skip = [], False
    for line in (old or "").splitlines():
        if line.strip() == STAGE_BEGIN:
            skip = True
        elif line.strip() == STAGE_END:
            skip = False
        elif not skip:
            keep.append(line)
    body = "\n".join(keep).rstrip("\n")
    if text is not None:
        body = (body + "\n" if body else "") + f"{STAGE_BEGIN}\n{text}{STAGE_END}"
    elif old is None:
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    mode = 0o600
    try:
        mode = os.stat(path).st_mode & 0o777
    except OSError:
        pass
    fd = os.open(path + ".part", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(body + "\n" if body else "")
    os.replace(path + ".part", path)


def _load_stage(e: "Engine") -> None:
    """A stage set before this agent started: its env file and key file are still there."""
    try:
        with open(os.path.join(e.stage_dir, f"{e.id}.env"), encoding="utf-8") as f:
            env = dict(line.rstrip("\n").split("=", 1) for line in f if "=" in line)
    except (OSError, ValueError):
        return
    st = env.get("TANDEM_STAGE") or ""
    if not os.path.isfile(env.get("TANDEM_LINK_KEY") or "") or ":" not in st:
        return
    e.stage_env = {} if e.local_env else env
    e.stage = {"stage": st, "role": "front" if st.startswith("0:") else "tail",
               "caches": env.get("TANDEM_STAGE_CACHES", "off"),
               "slots": int(env["QSE_PARALLEL"]) if env.get("QSE_PARALLEL", "").isdigit() else 1,
               "key": "set"}
    for k, name in (("TANDEM_LINK_LISTEN", "listen"), ("TANDEM_LINK_PEER", "peer")):
        if env.get(k):
            e.stage[name] = env[k]


def _last_line(e: "Engine") -> str:
    """`; its last line: ...` of an engine's start output, for a start that failed (a stage's
    refusal is one line there)."""
    try:
        with open(e.log_path, "rb") as f:
            f.seek(max(0, os.path.getsize(e.log_path) - 4096))
            lines = [x.strip() for x in f.read().decode("utf-8", "replace").splitlines()
                     if x.strip()]
    except OSError:
        return ""
    refusal = next((x for x in reversed(lines) if "REFUSING" in x), None)
    line = refusal or (lines[-1] if lines else "")
    return f"; its last line: {line[:240]}" if line else ""


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
                    "restart": ["bash", ctl, "restart"],
                    "local_env": os.path.join(os.path.expanduser(tandem_dir), "run",
                                              "local.env")}
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
