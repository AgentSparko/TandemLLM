"""A TandemNode's machine actions (VIS-250): reboot and update, as the node's owner set them up.

    maint = Maintenance.load(state_dir, virtual=None)   node.json's commands
    maint.view()                   {virtual, reboot: {managed, why}, update: {...}, job}
    maint.act('reboot' | 'update', who)   a job (202), or EngineError 409 not_managed / busy

NODE.JSON (`<state dir>/node.json`), either or both:

    {"reboot": ["sudo", "-n", "/usr/sbin/reboot"],
     "update": ["bash", "/opt/tandem/bin/tandem-update"]}

Neither is set by default. A reboot needs a right on the machine (a sudoers rule for the agent's
user, a polkit rule) that the agent never takes by itself, and an update is the install's own:
without a command the action is refused, 409 `not_managed`, with the words to set it up.

A VIRTUAL NODE (`--virtual NAME`, a second agent on one machine, OPS-197) runs neither command,
whatever node.json says: its reboot and its update are simulated, a job that runs `SIMULATED_S`
and says so, so a test fleet on one box never reboots or updates the box.

JOBS. One at a time (409 `busy`). A command runs in its own session with its output in
`<state dir>/logs/<action>.log`; its job is done when it exits 0. A reboot's command rarely
returns: its job runs until the agent goes down with the machine. Each action is a line in the
agent's log with the caller.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time

from proxy.nodeengines import EngineError, _argv

ACTIONS = ("reboot", "update")
SIMULATED_S = 6.0
COMMAND_TIMEOUT_S = 3600.0
HOW = {"reboot": "add a reboot command to node.json in the agent's state directory (it needs a "
                 "right to reboot: a sudoers or polkit rule for the agent's user)",
       "update": "add an update command to node.json in the agent's state directory"}


class Maintenance:
    def __init__(self, commands: dict, logs_dir: str, *, virtual: str | None = None,
                 log=lambda m: print(m, flush=True), clock=time.time,
                 sleep=time.sleep):
        self.commands = commands
        self.logs_dir = logs_dir
        self.virtual = virtual
        self.log, self.clock, self.sleep = log, clock, sleep
        self.job: dict | None = None
        self._lock = threading.Lock()

    @classmethod
    def load(cls, state_dir: str, virtual: str | None = None, **kw) -> "Maintenance":
        path = os.path.join(state_dir, "node.json")
        cmds = {}
        if os.path.exists(path):
            with open(path) as f:
                doc = json.load(f)
            if not isinstance(doc, dict):
                raise ValueError(f"{path}: {{\"reboot\": [...], \"update\": [...]}}")
            for a in ACTIONS:
                if doc.get(a) is not None:
                    cmds[a] = _argv(doc[a], f"{path}: {a}")
        return cls(cmds, os.path.join(state_dir, "logs"), virtual=virtual, **kw)

    def can(self, action: str) -> tuple[bool, str | None]:
        """(may it run, why not)."""
        if self.virtual:
            return True, None
        if action not in self.commands:
            return False, f"{action} is not set up on this node: {HOW[action]}"
        return True, None

    def view(self) -> dict:
        out = {"virtual": bool(self.virtual), "job": None if self.job is None else dict(self.job)}
        for a in ACTIONS:
            ok, why = self.can(a)
            out[a] = {"managed": ok, "simulated": bool(self.virtual), "why": why}
        return out

    def act(self, action: str, who: str) -> dict:
        if action not in ACTIONS:
            raise EngineError(404, "not_found", f"no action {action}")
        ok, why = self.can(action)
        if not ok:
            raise EngineError(409, "not_managed", why)
        with self._lock:
            if self.job is not None and self.job["state"] == "running":
                raise EngineError(409, "busy", f"a {self.job['action']} runs")
            self.job = {"id": f"{action}-{int(self.clock() * 1000)}", "action": action,
                        "state": "running", "who": who, "simulated": bool(self.virtual),
                        "started_at": self.clock(), "ended_at": None, "rc": None,
                        "message": None}
            job = self.job
        self.log(f"[node] {action}{' (simulated: a virtual node)' if self.virtual else ''} "
                 f"by {who}")
        threading.Thread(target=self._run, args=(job,), name=f"node-{action}",
                         daemon=True).start()
        return dict(job)

    def _run(self, job: dict) -> None:
        a = job["action"]
        try:
            if self.virtual:
                self.sleep(SIMULATED_S)
                job.update(state="done", message=f"simulated: a virtual node; the host was not "
                                                 f"{'rebooted' if a == 'reboot' else 'updated'}")
            else:
                rc = self._command(self.commands[a], a)
                job.update(rc=rc, state="done" if rc == 0 else "failed",
                           message=None if rc == 0 else f"the {a} command exited {rc}; its "
                                                        f"output: {self._log_path(a)}")
        except Exception as exc:                                     # noqa: BLE001
            job.update(state="failed", message=f"{type(exc).__name__}: {exc}")
        job["ended_at"] = self.clock()
        self.log(f"[node] {a}: {job['state']}{' · ' + job['message'] if job['message'] else ''}")

    def _log_path(self, action: str) -> str:
        return os.path.join(self.logs_dir, f"{action}.log")

    def _command(self, argv: list[str], action: str) -> int:
        os.makedirs(self.logs_dir, exist_ok=True)
        with open(self._log_path(action), "ab") as out:
            p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out,
                                 stderr=subprocess.STDOUT, start_new_session=True)
        try:
            return p.wait(timeout=COMMAND_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            p.kill()
            return -1
