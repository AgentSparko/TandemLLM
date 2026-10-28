"""SSH tunnels to rented and remote nodes (VIS-246, concept A5): the controller reaches a node that
has no LAN in common with it through `ssh -L` to the node's own loopback, never on a WAN address.

    t = Tunnels(known_hosts="<controller dir>/known_hosts")     one per controller (Fleet.tunnels)
    port = t.ensure(route)          the local port of the route's tunnel, opened (or reopened) now
    t.view(route)                   {state: up|opening|down, port, pid, since, restarts, error}
    t.close(route) / t.close_all()

A ROUTE is what pairing stored in the node's trust (proxy/fleettrust.py): {host, port, user, jumps,
identity_file, remote_port}, the node's SSH login and the agent's port on its loopback. Its tunnel
is one system `ssh` process (the user's OpenSSH, as SSH once uses it):

    ssh -N -T -p PORT -l USER [-J jump,...] [-i KEY -o IdentitiesOnly=yes] -o BatchMode=yes
        -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3
        -o StrictHostKeyChecking=yes -o UserKnownHostsFile=<the controller's known_hosts>
        -L 127.0.0.1:LOCAL:127.0.0.1:REMOTE HOST

The node's host key is the one pairing confirmed (the controller's known_hosts); a changed key
fails the tunnel, never replaces the key. BatchMode: no password, ever: a tunnel lives as long as
the node is in the fleet, so it logs in with the key file the user gave (or the user's agent). The
local port is chosen once per route and kept while the process lives; a process that exits is
started again after 1, 2, 4 ... up to 60 s, and the node reads `off` meanwhile (its agent does not
answer). `ensure` waits up to OPEN_S for the local port to accept.

NOT LIVE YET. Step 7's rental is the first real remote node: until then the tunnel is held to its
argv and its restarts on fakes (tests/test_add_spark.py), and once against a loopback sshd on the
Spark (a throwaway one with its own host key and port).
"""

from __future__ import annotations

import hashlib
import json
import socket
import subprocess
import threading
import time

from proxy import sshonce

OPEN_S = 10.0
BACKOFF_MAX_S = 60.0


def route_key(route: dict) -> str:
    """One tunnel per route: the node's login and the agent's port."""
    keep = {k: route.get(k) for k in ("host", "port", "user", "jumps", "identity_file",
                                      "remote_port")}
    return hashlib.sha256(json.dumps(keep, sort_keys=True).encode()).hexdigest()[:16]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def accepts(port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


class Tunnels:
    def __init__(self, known_hosts: str, *, ssh: str = "ssh", spawn=None, probe=None,
                 pick=None, clock=time.monotonic, sleep=time.sleep, log=None):
        self.known_hosts = known_hosts
        self.ssh = ssh
        self.spawn = spawn or (lambda argv: subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            start_new_session=True))
        self.probe = probe or accepts
        self.pick = pick or free_port
        self.clock, self.sleep = clock, sleep
        self.log = log or (lambda m: print(m, flush=True))
        self._lock = threading.RLock()
        self._t: dict[str, dict] = {}

    def argv(self, route: dict, local: int) -> list[str]:
        a = [self.ssh, "-N", "-T", "-p", str(route.get("port") or 22)]
        if route.get("user"):
            a += ["-l", route["user"]]
        if route.get("jumps"):
            a += ["-J", ",".join(sshonce.jump_spec(j) for j in route["jumps"])]
        if route.get("identity_file"):
            a += ["-i", route["identity_file"], "-o", "IdentitiesOnly=yes"]
        a += ["-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
              "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
              "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={self.known_hosts}",
              "-o", "GlobalKnownHostsFile=/dev/null", "-o", "ControlPath=none",
              "-o", "LogLevel=ERROR",
              "-L", f"127.0.0.1:{local}:127.0.0.1:{int(route['remote_port'])}", route["host"]]
        return a

    def _start(self, k: str, t: dict) -> None:
        t["proc"] = self.spawn(self.argv(t["route"], t["port"]))
        t["started"] = self.clock()
        t["state"] = "opening"

    def ensure(self, route: dict) -> int:
        """The local port of the route's tunnel; its process started (again) when it is not
        running and its back-off has passed. Waits until the port accepts, OPEN_S at most."""
        k = route_key(route)
        with self._lock:
            t = self._t.get(k)
            if t is None:
                t = self._t[k] = {"route": dict(route), "port": self.pick(), "proc": None,
                                  "restarts": 0, "since": None, "error": None, "next": 0.0,
                                  "state": "down", "started": None}
            p = t["proc"]
            if p is not None and p.poll() is not None:
                t["error"] = self._why(p)
                t["proc"] = None
                t["restarts"] += 1
                t["state"] = "down"
                wait = min(BACKOFF_MAX_S, 2 ** min(t["restarts"] - 1, 6))
                t["next"] = self.clock() + wait
                self.log(f"[fleet] tunnel to {route['host']}:{route.get('port') or 22} ended "
                         f"({t['error'] or 'exit ' + str(p.returncode)}); again in {wait} s")
            if t["proc"] is None and self.clock() >= t["next"]:
                self._start(k, t)
                self.log(f"[fleet] tunnel to {route['host']}:{route.get('port') or 22} "
                         f"opening on 127.0.0.1:{t['port']}")
            port, opening = t["port"], t["state"] == "opening"
        if opening:
            end = self.clock() + OPEN_S
            while self.clock() < end:
                with self._lock:
                    p = t["proc"]
                    if p is None or p.poll() is not None:
                        break
                if self.probe(port):
                    with self._lock:
                        t["state"], t["since"], t["error"] = "up", time.time(), None
                    break
                self.sleep(0.1)
        return port

    @staticmethod
    def _why(p) -> str | None:
        try:
            err = (p.stderr.read() if p.stderr else b"") or b""
        except (OSError, ValueError):
            return None
        lines = [x for x in err.decode("utf-8", "replace").strip().splitlines() if x.strip()]
        return lines[-1][:300] if lines else None

    def view(self, route: dict) -> dict:
        with self._lock:
            t = self._t.get(route_key(route))
            if t is None:
                return {"state": "down", "port": None, "pid": None, "since": None,
                        "restarts": 0, "error": None}
            return {"state": t["state"], "port": t["port"],
                    "pid": getattr(t["proc"], "pid", None), "since": t["since"],
                    "restarts": t["restarts"], "error": t["error"]}

    def close(self, route: dict) -> None:
        with self._lock:
            t = self._t.pop(route_key(route), None)
        if t is not None and t["proc"] is not None:
            _stop(t["proc"])

    def close_all(self) -> None:
        with self._lock:
            ts, self._t = list(self._t.values()), {}
        for t in ts:
            if t["proc"] is not None:
                _stop(t["proc"])


def _stop(p) -> None:
    try:
        p.terminate()
        p.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        try:
            p.kill()
        except OSError:
            pass
