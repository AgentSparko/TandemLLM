"""`tandem-app service install | uninstall | status` (OPS-234): TandemApp's controller as a background
service of the user, a launchd agent on macOS and a systemd user unit on Linux.

    tandem-app service install [--name N] [--host 127.0.0.1] [--port 8080] [--state-dir ~/.tandemllm]
                               [--env-file F] [--open]
    tandem-app service uninstall [--name N]
    tandem-app service status [--name N] [--json]

    svc = Service(name, platform=..., home=..., run=...)       the same from Python (the agent's
    svc.install(Spec(...)) / svc.uninstall() / svc.status()    controller start, proxy/ctlmove.py)

WHAT IT WRITES. macOS: `~/Library/LaunchAgents/<label>.plist` (label `org.tandemllm.app`), loaded
into the user's GUI domain (`launchctl bootstrap gui/<uid>`), started at load and kept alive, its
output in `<state dir>/logs/tandem-app.log`. Linux: `~/.config/systemd/user/<unit>.service` (unit
`tandemapp`), enabled and started (`systemctl --user enable --now`), restarted on failure, its
output in the user's journal. Either runs `<this Python> -m proxy serve` with the address, the port
and the state dir's files (config.sqlite3, ledger.sqlite3, conductor/) given, and the env file when
one is named; a checkout runs from its own directory. The page is at http://<host>:<port>/ once the
controller answers /health, which install waits for.

UNINSTALL stops the service and removes the plist or the unit; nothing of it stays loaded. The
state dir (the fleet's registry, the ledger, the keys) is never removed here.

A HOST THAT SLEEPS. A Mac's controller stops while the Mac sleeps (the host-sleep rule says so,
proxy/fleetwatch.py); a Linux user unit stops at logout unless the user lingers (`loginctl
enable-linger`), which status reports.
"""

from __future__ import annotations

import json
import os
import shlex
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LABEL = "org.tandemllm.app"
UNIT = "tandemapp"
DEFAULT_PORT = 8080
DEFAULT_DIR = "~/.tandemllm"
HEALTH_WAIT_S = 30.0
UNLOAD_WAIT_S = 15.0
MAC_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


class ServiceError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind, self.message = kind, message


@dataclass
class Spec:
    """What the service runs."""
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    state_dir: str = DEFAULT_DIR
    env_file: str | None = None
    env: dict = field(default_factory=dict)      # put in the plist or the unit as they are
    python: str = sys.executable

    def argv(self, home: str) -> list[str]:
        d = _expand(self.state_dir, home)
        a = [self.python, "-m", "proxy", "serve", "--host", self.host, "--port", str(self.port),
             "--config-db", os.path.join(d, "config.sqlite3"),
             "--usage-ledger", os.path.join(d, "ledger.sqlite3"),
             "--conductor-config", os.path.join(d, "conductor")]
        if self.env_file:
            a += ["--env-file", _expand(self.env_file, home)]
        return a


def _expand(p: str, home: str) -> str:
    return os.path.join(home, p[2:]) if p.startswith("~/") else (home if p == "~" else p)


def _run(argv: list[str], timeout: float = 30.0) -> tuple[int, str, str]:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, "", f"{argv[0]}: {exc}"
    return r.returncode, r.stdout, r.stderr


def _health(host: str, port: int, timeout: float = 2.0) -> bool:
    h = "127.0.0.1" if host in ("0.0.0.0", "", "::") else host
    h = f"[{h}]" if ":" in h else h
    try:
        with urllib.request.urlopen(f"http://{h}:{port}/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:                                                # noqa: BLE001
        return False


def port_busy(host: str, port: int) -> bool:
    h = "127.0.0.1" if host in ("0.0.0.0", "", "::") else host
    try:
        with socket.create_connection((h, port), timeout=1.0):
            return True
    except OSError:
        return False


def page_url(spec: Spec) -> str:
    h = "127.0.0.1" if spec.host in ("0.0.0.0", "", "::") else spec.host
    return f"http://{'[' + h + ']' if ':' in h else h}:{spec.port}/"


class Service:
    """One named service of this user. `run(argv) -> (rc, out, err)`, `health(host, port)` and
    `sleep` are the system's, a test's fakes otherwise."""

    def __init__(self, name: str | None = None, *, platform: str = sys.platform,
                 home: str | None = None, uid: int | None = None, run=_run, health=_health,
                 sleep=time.sleep, wait_s: float = HEALTH_WAIT_S):
        self.mac = platform == "darwin"
        if not self.mac and not platform.startswith("linux"):
            raise ServiceError("unsupported", f"a background service on {platform} is not built: "
                                              f"run `tandem-app serve` in a terminal")
        self.name = name or (LABEL if self.mac else UNIT)
        if not self.name or not all(c.isalnum() or c in "._-@" for c in self.name) or \
                self.name.startswith(("-", ".")):
            raise ServiceError("bad_request", f"{self.name!r}: a service name is letters, digits "
                                              f"and . _ - @")
        self.home = home or os.path.expanduser("~")
        self.uid = os.getuid() if uid is None else uid
        self.run, self.health, self.sleep, self.wait_s = run, health, sleep, wait_s

    # ------------------------------------------------------------------ where
    @property
    def path(self) -> str:
        if self.mac:
            return os.path.join(self.home, "Library", "LaunchAgents", f"{self.name}.plist")
        return os.path.join(self.home, ".config", "systemd", "user", f"{self.name}.service")

    @property
    def unit(self) -> str:
        return f"{self.name}.service"

    @property
    def domain(self) -> str:
        return f"gui/{self.uid}"

    # ------------------------------------------------------------------ the text
    def text(self, spec: Spec) -> str:
        argv = spec.argv(self.home)
        cwd = ROOT if os.path.isfile(os.path.join(ROOT, "pyproject.toml")) else self.home
        d = _expand(spec.state_dir, self.home)
        if self.mac:
            env = {"PATH": MAC_PATH, "PYTHONUNBUFFERED": "1", **spec.env}
            log = os.path.join(d, "logs", "tandem-app.log")
            args = "".join(f"\n    <string>{escape(a)}</string>" for a in argv)
            envs = "".join(f"\n    <key>{escape(k)}</key><string>{escape(v)}</string>"
                           for k, v in env.items())
            return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{escape(self.name)}</string>
  <key>ProgramArguments</key>
  <array>{args}
  </array>
  <key>WorkingDirectory</key><string>{escape(cwd)}</string>
  <key>EnvironmentVariables</key>
  <dict>{envs}
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>{escape(log)}</string>
  <key>StandardErrorPath</key><string>{escape(log)}</string>
</dict>
</plist>
"""
        env = {"PYTHONUNBUFFERED": "1", **spec.env}
        lines = "".join(f"Environment={shlex.quote(f'{k}={v}')}\n" for k, v in env.items())
        return f"""[Unit]
Description=TandemApp controller (tandem-app) on {spec.host}:{spec.port}
After=network-online.target

[Service]
ExecStart={' '.join(shlex.quote(a) for a in argv)}
WorkingDirectory={cwd}
{lines}Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""

    # ------------------------------------------------------------------ install
    def install(self, spec: Spec, say=lambda m: None) -> dict:
        """Written, loaded and answering /health; the status. A port another process holds is
        refused before anything is written."""
        was = self.status(spec.host, spec.port)
        if port_busy(spec.host, spec.port) and not was["running"]:
            raise ServiceError("port_in_use", f"port {spec.port} is in use by another program: "
                                              f"pick another (--port)")
        d = _expand(spec.state_dir, self.home)
        os.makedirs(d, mode=0o700, exist_ok=True)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        if self.mac:
            os.makedirs(os.path.join(d, "logs"), mode=0o700, exist_ok=True)
            if was["loaded"]:
                self.run(["launchctl", "bootout", f"{self.domain}/{self.name}"])
        with open(self.path + ".part", "w") as f:
            f.write(self.text(spec))
        os.replace(self.path + ".part", self.path)
        say(f"wrote {self.path}")
        if self.mac:
            rc, out, err = self.run(["launchctl", "bootstrap", self.domain, self.path])
            if rc != 0:
                raise ServiceError("load_failed", f"launchctl bootstrap: {(err or out).strip()}")
        else:
            for argv in (["systemctl", "--user", "daemon-reload"],
                         ["systemctl", "--user", "enable", self.unit],
                         ["systemctl", "--user", "restart", self.unit]):
                rc, out, err = self.run(argv)
                if rc != 0:
                    raise ServiceError("load_failed", f"{' '.join(argv)}: {(err or out).strip()}")
        end = time.monotonic() + self.wait_s
        while not self.health(spec.host, spec.port):
            if time.monotonic() >= end:
                raise ServiceError("not_answering", f"{self.name} is loaded but answers no "
                                                    f"/health on port {spec.port} after "
                                                    f"{self.wait_s:.0f} s: {self.log_hint(spec)}")
            self.sleep(0.5)
        st = self.status(spec.host, spec.port)
        st["url"] = page_url(spec)
        return st

    def log_hint(self, spec: Spec) -> str:
        if self.mac:
            return f"see {os.path.join(_expand(spec.state_dir, self.home), 'logs', 'tandem-app.log')}"
        return f"journalctl --user -u {self.unit} -n 50"

    # ------------------------------------------------------------------ uninstall
    def uninstall(self, say=lambda m: None) -> dict:
        """Stopped, unloaded and its file removed; what is left is said (nothing, when it went)."""
        if self.mac:
            self.run(["launchctl", "bootout", f"{self.domain}/{self.name}"])
        else:
            self.run(["systemctl", "--user", "disable", "--now", self.unit])
        removed = False
        try:
            os.remove(self.path)
            removed = True
            say(f"removed {self.path}")
        except FileNotFoundError:
            pass
        if not self.mac:
            self.run(["systemctl", "--user", "daemon-reload"])
            self.run(["systemctl", "--user", "reset-failed", self.unit])
        st = self.status()
        # launchd boots a service out after its process ends: wait for it, at most UNLOAD_WAIT_S
        end = time.monotonic() + UNLOAD_WAIT_S
        while self.mac and st["loaded"] and time.monotonic() < end:
            self.sleep(0.25)
            st = self.status()
        st["removed"] = removed
        return st

    # ------------------------------------------------------------------ status
    def status(self, host: str | None = None, port: int | None = None) -> dict:
        """{name, kind, path, installed, loaded, running, pid, answers, linger}: `answers` only
        when the address is known (the installed file's own, else the arguments)."""
        out = {"name": self.name, "kind": "launchd" if self.mac else "systemd", "path": self.path,
               "installed": os.path.isfile(self.path), "loaded": False, "running": False,
               "pid": None, "answers": None, "linger": None}
        if self.mac:
            rc, text, _ = self.run(["launchctl", "print", f"{self.domain}/{self.name}"])
            if rc == 0:
                out["loaded"] = True
                seen = set()
                for line in text.splitlines():
                    # the service's own keys are one tab in; nested blocks have their own `state`
                    if not line.startswith("\t") or line.startswith("\t\t"):
                        continue
                    k, _, v = line.strip().partition(" = ")
                    if k in seen:
                        continue
                    seen.add(k)
                    if k == "state":
                        out["running"] = v.strip() == "running"
                    elif k == "pid" and v.strip().isdigit():
                        out["pid"] = int(v.strip())
        else:
            rc, text, _ = self.run(["systemctl", "--user", "show", self.unit, "-p", "LoadState",
                                    "-p", "ActiveState", "-p", "MainPID"])
            got = dict(x.split("=", 1) for x in text.splitlines() if "=" in x)
            out["loaded"] = got.get("LoadState") == "loaded"
            out["running"] = got.get("ActiveState") in ("active", "activating", "reloading")
            pid = got.get("MainPID", "0")
            out["pid"] = int(pid) if pid.isdigit() and pid != "0" else None
            user = os.environ.get("USER") or ""
            if user:
                rc, text, _ = self.run(["loginctl", "show-user", user, "-p", "Linger"])
                if rc == 0 and "=" in text:
                    out["linger"] = text.strip().split("=", 1)[1] == "yes"
        if host is None and out["installed"]:
            host, port = self._address()
        if host is not None and port is not None:
            out["answers"] = bool(out["running"] and self.health(host, port))
            out["url"] = page_url(Spec(host=host, port=port))
        return out

    def _address(self) -> tuple[str | None, int | None]:
        """The --host and --port of the installed file."""
        try:
            with open(self.path) as f:
                text = f.read()
        except OSError:
            return None, None
        if self.mac:
            import re
            argv = re.findall(r"<string>([^<]*)</string>", text)
        else:
            line = next((x for x in text.splitlines() if x.startswith("ExecStart=")), "")
            argv = shlex.split(line[len("ExecStart="):])
        try:
            return argv[argv.index("--host") + 1], int(argv[argv.index("--port") + 1])
        except (ValueError, IndexError):
            return None, None


# ---------------------------------------------------------------------- the command

def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="tandem-app service",
                                 description="TandemApp's controller as a background service")
    ap.add_argument("action", choices=("install", "uninstall", "status"))
    ap.add_argument("--name", default=None, help=f"the launchd label (default {LABEL}) or the "
                                                 f"systemd user unit (default {UNIT})")
    ap.add_argument("--host", default="127.0.0.1",
                    help="the address it listens on (default 127.0.0.1: this machine only; a "
                         "controller others reach needs TANDEM_ADMIN_TOKEN in its env file)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--state-dir", default=DEFAULT_DIR,
                    help="config.sqlite3, ledger.sqlite3, conductor/ and the logs (default "
                         "~/.tandemllm)")
    ap.add_argument("--env-file", default=None, help="KEY=VALUE lines read at each start")
    ap.add_argument("--open", action="store_true", help="install: open the page in the browser")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    say = (lambda m: None) if a.json else (lambda m: print(f"[service] {m}", flush=True))
    try:
        svc = Service(a.name)
        if a.action == "install":
            spec = Spec(host=a.host, port=a.port, state_dir=a.state_dir, env_file=a.env_file)
            if a.host not in ("127.0.0.1", "::1", "localhost") and not a.env_file:
                say("WARN listening beyond this machine: set TANDEM_ADMIN_TOKEN in an env file "
                    "(--env-file), or anyone on the network changes the fleet")
            st = svc.install(spec, say)
            if a.json:
                print(json.dumps(st))
                return 0
            say(f"{st['name']} runs ({st['kind']}, pid {st['pid']}): open {st['url']}")
            if st.get("linger") is False:
                say("WARN this user does not linger: the controller stops at logout "
                    "(loginctl enable-linger)")
            if svc.mac:
                say("WARN a Mac that sleeps stops the controller and the API: keep it awake, or "
                    "run the controller on a Spark (App settings)")
            if a.open:
                svc.run(["open" if svc.mac else "xdg-open", st["url"]])
            return 0
        if a.action == "uninstall":
            st = svc.uninstall(say)
            if a.json:
                print(json.dumps(st))
            elif st["loaded"] or st["running"]:
                say(f"{st['name']} is still loaded: {st}")
            else:
                say(f"{st['name']} is gone: not loaded, no file; the state dir stays")
            return 1 if st["loaded"] or st["running"] else 0
        st = svc.status()
        if a.json:
            print(json.dumps(st))
        else:
            say(f"{st['name']} ({st['kind']}): " + ("not installed" if not st["installed"] else
                f"installed, {'running pid ' + str(st['pid']) if st['running'] else 'not running'}"
                f"{', answers ' + st['url'] if st.get('answers') else ''}"))
        return 0 if st["installed"] and st["running"] else 3
    except ServiceError as exc:
        print(f"[service] {exc.kind}: {exc.message}", file=sys.stderr, flush=True)
        return 2
