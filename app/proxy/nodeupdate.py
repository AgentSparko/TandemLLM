"""A TandemNode's own update (OPS-234, the fleet update): the wheel its controller sends, installed
into the agent's own environment, the engine's runtime brought to the same release, and the agent
restarted into the new code.

    up = Updater(state_dir, virtual=..., tandem_dir=..., log=...)
    up.receive(name, sha256, data)        PUT  /node/update/package?name=&sha256=   the wheel
    up.start(body, who)                   POST /node/update {name, sha256, version, install_sh?}
    up.view()                             GET  /node/update  {managed, why, version, job}

THE PACKAGE is the controller's wheel (proxy/nodeinstall.py: built from its own copy, or
TANDEM_NODE_WHEEL), kept in `<state dir>/pkg/` once its sha256 matches. Nothing is fetched by the
node itself; pip takes the dependencies the wheel names from the node's package index only when
one is missing.

THE JOB, one at a time (409 `busy`), its steps in `<state dir>/update.json` so that it outlives the
restart at its end:

    install   `<python> -m pip install --upgrade <wheel>` into the environment the agent runs
              from (a virtual environment of its own, as the Add Spark install makes it); the
              version it then reports must be the job's
    engine    the engine's runtime at the same release: the controller's install.sh, run with
              --runtime-only over the install the agent names (`--tandem-dir`); none named: there
              is no engine install here to update (an engines.json engine is its owner's).
              SIMULATED on a virtual node (OPS-197): the runtime under it is the machine's
    restart   the agent replaces itself with the new code (exec, same process id, so systemd and
              launchd keep it); the job is done when the agent that comes up runs the version

An agent outside a virtual environment of its own (a checkout, a system Python) is not updated
here: 409 `not_managed`, with the words to update it by its installer.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time

PKG_MAX = 256 << 20
SIMULATED_S = 2.0
STEPS = (("install", "Install TandemNode {v}"), ("engine", "Engine runtime {v}"),
         ("restart", "Restart the agent on {v}"))
LOG_MAX = 200
COMMAND_TIMEOUT_S = 1800.0


class UpdateError(Exception):
    def __init__(self, status: int, kind: str, message: str):
        super().__init__(message)
        self.status, self.kind, self.message = status, kind, message

    def body(self) -> dict:
        return {"error": {"type": self.kind, "message": self.message}}


def norm(v: str | None) -> str:
    """A version as pip writes it: `0.15.0-test` and `0.15.0.dev0` one, a local label kept."""
    from proxy import _version
    v = (v or "").strip()
    try:
        return _version.pep440(v)
    except ValueError:
        return v.lower()


def installed_version(python: str) -> str | None:
    """The version of the distribution (proxy/_version.py DIST) the environment of `python` has
    installed, None without one."""
    from proxy import _version
    try:
        r = subprocess.run([python, "-c", "from importlib.metadata import version; "
                                          f"print(version({_version.DIST!r}))"],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() or None if r.returncode == 0 else None


class Updater:
    def __init__(self, state_dir: str, *, virtual: str | None = None,
                 tandem_dir: str | None = None, log=lambda m: print(m, flush=True),
                 clock=time.time, sleep=time.sleep, python: str = sys.executable,
                 prefix: str = sys.prefix, base_prefix: str = sys.base_prefix,
                 restart=None, run=None, release=None):
        from proxy import _version
        self.release = release or _version.release
        self.state_dir = state_dir
        self.pkg_dir = os.path.join(state_dir, "pkg")
        self.path = os.path.join(state_dir, "update.json")
        self.virtual, self.tandem_dir = virtual, tandem_dir
        self.log, self.clock, self.sleep = log, clock, sleep
        self.python, self.prefix, self.base_prefix = python, prefix, base_prefix
        self.restart = restart or exec_self
        self.run = run or self._command
        self._lock = threading.Lock()
        self.job: dict | None = self._load()

    # ------------------------------------------------------------------ the record
    def _load(self) -> dict | None:
        """The last job; one that was restarting the agent is settled here, by the version this
        agent runs now."""
        try:
            with open(self.path) as f:
                job = json.load(f)
        except (OSError, ValueError):
            return None
        if not isinstance(job, dict):
            return None
        if job.get("state") == "running":
            restart = next((s for s in job.get("steps") or [] if s["id"] == "restart"), None)
            now = installed_version(self.python) if restart and restart["state"] == "running" \
                else None
            if now is not None and norm(now) == norm(job.get("version")):
                restart.update(state="done", detail=f"the agent runs {now}",
                               ended_at=round(self.clock(), 3))
                job.update(state="done", ended_at=round(self.clock(), 3))
                self.log(f"[node] update to {job['version']}: done, the agent runs {now}")
            else:
                for s in job.get("steps") or []:
                    if s["state"] == "running":
                        s.update(state="failed", detail="the agent stopped during this step")
                job.update(state="failed", ended_at=round(self.clock(), 3),
                           message=f"the agent stopped during the update; it runs "
                                   f"{now or 'an unknown version'}: update again")
            self._save(job)
        return job

    def _save(self, job: dict) -> None:
        os.makedirs(self.state_dir, mode=0o700, exist_ok=True)
        fd = os.open(self.path + ".part", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(job, f)
        os.replace(self.path + ".part", self.path)

    # ------------------------------------------------------------------ what it can do
    def can(self) -> tuple[bool, str | None]:
        if self.prefix == self.base_prefix:
            return False, ("TandemNode runs outside a virtual environment of its own (from a "
                           "checkout or the system's Python): update it with its installer "
                           "(install.sh, or pip in its environment)")
        if not os.access(self.prefix, os.W_OK):
            return False, f"the agent's environment {self.prefix} is not writable by its user"
        return True, None

    def view(self) -> dict:
        ok, why = self.can()
        return {"managed": ok, "why": why, "simulated_engine": bool(self.virtual),
                "version": self.release() or None,
                "job": None if self.job is None else json.loads(json.dumps(self.job))}

    # ------------------------------------------------------------------ the package
    def receive(self, name: str, sha256: str, data: bytes) -> dict:
        from proxy import _version
        if not re.fullmatch(rf"{re.escape(_version.DIST)}-[A-Za-z0-9.+_]+-py3-none-any\.whl",
                            name or ""):
            raise UpdateError(400, "bad_request", "name is the wheel's file name "
                                                  f"({_version.DIST}-<version>-py3-none-any.whl)")
        if not re.fullmatch(r"[0-9a-f]{64}", sha256 or ""):
            raise UpdateError(400, "bad_request", "sha256 is the wheel's hex digest")
        got = hashlib.sha256(data).hexdigest()
        if got != sha256:
            raise UpdateError(400, "bad_request", f"the wheel's sha256 is {got}, not {sha256}")
        os.makedirs(self.pkg_dir, mode=0o700, exist_ok=True)
        path = os.path.join(self.pkg_dir, name)
        with open(path + ".part", "wb") as f:
            f.write(data)
        os.replace(path + ".part", path)
        return {"name": name, "sha256": sha256, "size": len(data)}

    # ------------------------------------------------------------------ the job
    def start(self, body, who: str) -> dict:
        if not isinstance(body, dict) or set(body) - {"name", "sha256", "version", "ref",
                                                             "install_sh"}:
            raise UpdateError(400, "bad_request", "the body is {name, sha256, version, ref?, "
                                                  "install_sh?}")
        ok, why = self.can()
        if not ok:
            raise UpdateError(409, "not_managed", why)
        name, version = body.get("name"), body.get("version")
        path = os.path.join(self.pkg_dir, str(name or ""))
        if not isinstance(name, str) or "/" in name or not os.path.isfile(path):
            raise UpdateError(409, "no_package", f"no package {name}: send it first (PUT "
                                                 f"/node/update/package)")
        with open(path, "rb") as f:
            if hashlib.sha256(f.read()).hexdigest() != body.get("sha256"):
                raise UpdateError(409, "no_package", f"{name} on this node is not the one with "
                                                     f"that sha256: send it again")
        if not isinstance(version, str) or not version:
            raise UpdateError(400, "bad_request", "version is the wheel's version")
        script = body.get("install_sh")
        if script is not None:
            try:
                script = base64.b64decode(script, validate=True)
            except (ValueError, TypeError):
                raise UpdateError(400, "bad_request", "install_sh is base64")
        with self._lock:
            if self.job is not None and self.job["state"] == "running":
                raise UpdateError(409, "busy", f"an update to {self.job['version']} runs")
            now = round(self.clock(), 3)
            self.job = {"id": f"update-{int(now * 1000)}", "state": "running",
                        "version": version, "from": self.release() or None, "who": who,
                        "ref": body.get("ref") if isinstance(body.get("ref"), str) else None,
                        "simulated": bool(self.virtual), "started_at": now, "ended_at": None,
                        "message": None, "log": [],
                        "steps": [{"id": sid, "label": label.format(v=version), "state": "todo",
                                   "detail": None, "ended_at": None} for sid, label in STEPS]}
            self._save(self.job)
            job = self.job
        self.log(f"[node] update to {version} by {who}"
                 f"{' (the engine runtime simulated: a virtual node)' if self.virtual else ''}")
        threading.Thread(target=self._go, args=(job, path, script), name="node-update",
                         daemon=True).start()
        return json.loads(json.dumps(job))

    def _step(self, job: dict, sid: str, state: str, detail: str | None = None) -> None:
        with self._lock:
            s = next(x for x in job["steps"] if x["id"] == sid)
            s["state"] = state
            if detail is not None:
                s["detail"] = detail
            if state in ("done", "failed", "skip"):
                s["ended_at"] = round(self.clock(), 3)
            self._save(job)

    def _say(self, job: dict, text: str) -> None:
        for line in (text or "").splitlines():
            if line.strip():
                job["log"].append(line.rstrip()[:300])
        del job["log"][:-LOG_MAX]

    def _go(self, job: dict, wheel: str, script: bytes | None) -> None:
        from proxy import _version
        sid = "install"
        try:
            self._step(job, sid, "running")
            rc, out = self.run([self.python, "-m", "pip", "install", "--disable-pip-version-check",
                                "--no-input", "--upgrade", wheel])
            self._say(job, out)
            now = installed_version(self.python)
            if rc != 0:
                raise UpdateError(500, "install_failed", f"pip exited {rc}: "
                                                         f"{(out.strip().splitlines() or [''])[-1][:200]}")
            if norm(now) != norm(job["version"]):
                raise UpdateError(500, "version_mismatch", f"the environment has {now}, not "
                                                           f"{job['version']}")
            self._step(job, sid, "done", f"{_version.DIST} {now} in {self.prefix}")
            sid = "engine"
            self._step(job, sid, "running")
            if self.virtual:
                self.sleep(SIMULATED_S)
                self._step(job, sid, "done", "simulated: a virtual node; the runtime under it is "
                                             "the machine's")
            elif self.tandem_dir and script:
                d = os.path.expanduser(self.tandem_dir)
                sh = os.path.join(self.state_dir, "install.sh")
                with open(sh, "wb") as f:
                    f.write(script)
                ref = job.get("ref") or "v" + job["version"].split("+", 1)[0]
                rc, out = self.run(["bash", sh, "--dir", d, "--ref", ref, "--runtime-only",
                                    "--yes"])
                self._say(job, out)
                if rc != 0:
                    raise UpdateError(500, "install_failed", f"install.sh exited {rc}: "
                                                             f"{(out.strip().splitlines() or [''])[-1][:200]}")
                self._step(job, sid, "done", f"the engine's runtime at {ref} in {d}")
            else:
                self._step(job, sid, "skip", "no install.sh install named here (--tandem-dir): "
                                             "an engine from engines.json is its owner's to update")
            sid = "restart"
            self._step(job, sid, "running", "the agent restarts on the new code")
            self.log(f"[node] update to {job['version']}: restarting the agent")
            threading.Timer(0.5, self.restart).start()
        except UpdateError as exc:
            self._fail(job, sid, exc.message)
        except Exception as exc:                                     # noqa: BLE001
            self._fail(job, sid, f"{type(exc).__name__}: {exc}")

    def _fail(self, job: dict, sid: str, message: str) -> None:
        with self._lock:
            for s in job["steps"]:
                if s["id"] == sid:
                    s.update(state="failed", detail=message, ended_at=round(self.clock(), 3))
            job.update(state="failed", message=message, ended_at=round(self.clock(), 3))
            self._save(job)
        self.log(f"[node] update to {job['version']} failed: {message}")

    @staticmethod
    def _command(argv: list[str]) -> tuple[int, str]:
        try:
            r = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                               timeout=COMMAND_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return -1, f"{argv[0]}: no end after {COMMAND_TIMEOUT_S:.0f} s"
        except OSError as exc:
            return 127, f"{argv[0]}: {exc}"
        return r.returncode, (r.stdout or "") + (r.stderr or "")


def exec_self() -> None:
    """This process replaced by the same command on the new code (its listening sockets close on
    exec; the process id stays, so its service manager keeps it)."""
    argv = list(getattr(sys, "orig_argv", None) or [sys.executable] + sys.argv)
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(sys.executable, [sys.executable] + argv[1:])
