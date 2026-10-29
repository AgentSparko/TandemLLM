"""OPS-235: TandemApp alone on macOS and Linux (OPS-234): `tandem-app service install | uninstall |
status` on a temporary HOME with fake launchctl and systemctl, the `app` extra, and the wheel of a
local build label (the fleet update's package).

  1  macOS: the plist (a valid property list: the controller's argv, its state dir's files, kept
     alive, its log), bootstrapped into gui/<uid>, answering /health, status running with its pid;
     uninstall boots it out and removes the plist: nothing loaded, no file   test_launchd
  2  Linux: the user unit (ExecStart, the env, restart on failure), daemon-reload, enable and
     restart; status from systemctl show, linger; uninstall disables, removes, reloads, resets:
     nothing left                                                           test_systemd
  3  refusals: a port another program holds, a service that never answers /health, a bad name,
     an unsupported system; reinstalling a running one boots the old one out first
                                                                            test_refusals
  4  the command: `tandem-app service status` of a service that is not there exits 3 and says
     so, on this machine's real launchctl or systemctl, without touching any service
                                                                            test_command_status
  5  pyproject's `app` extra: no engine, no torch; `pip install './app[app]'` gives tandem-app
                                                                            test_app_extra
  6  `tandem-app package --version 0.15.0+test`: the wheel's name and METADATA carry the label,
     wheel_version reads it, pep440 keeps it, same_release says it is 0.15.0's
                                                                            test_local_label_wheel

    python tests/test_app_service.py
"""

from __future__ import annotations

import json
import os
import plistlib
import shlex
import socket
import subprocess
import sys
import tempfile
import tomllib
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from proxy import _version, appservice, nodeinstall, preflight  # noqa: E402
from proxy_util import run  # noqa: E402


class FakeSys:
    """launchctl / systemctl / loginctl as far as the service module asks them."""

    def __init__(self, mac: bool):
        self.mac = mac
        self.calls: list[list[str]] = []
        self.loaded: dict[str, int] = {}          # label or unit -> pid
        self.leaving: dict[str, int] = {}         # booted out, still listed this many prints
        self.next_pid = 4100

    def __call__(self, argv, timeout=30.0):
        self.calls.append(list(argv))
        if argv[0] == "launchctl":
            if argv[1] == "bootstrap":
                label = plistlib.load(open(argv[3], "rb"))["Label"]
                self.leaving.pop(label, None)
                self.next_pid += 1
                self.loaded[label] = self.next_pid
                return 0, "", ""
            if argv[1] == "bootout":
                # as launchd does it: the service stays listed until its process ended
                label = argv[2].split("/", 2)[2]
                if label not in self.loaded:
                    return 3, "", "Boot-out failed: 3: No such process\n"
                self.leaving[label] = 2
                return 0, "", ""
            if argv[1] == "print":
                label = argv[2].split("/", 2)[2]
                if label in self.leaving:
                    self.leaving[label] -= 1
                    if self.leaving[label] < 0:
                        del self.leaving[label]
                        self.loaded.pop(label, None)
                if label not in self.loaded:
                    return 113, "", f"Could not find service \"{label}\"\n"
                # as launchctl prints it: nested blocks have a `state` of their own
                return 0, (f"gui/501/{label} = {{\n\tactive count = 1\n\tstate = running\n"
                           f"\tpid = {self.loaded[label]}\n\tendpoints = {{\n\t\t\"x\" = {{\n"
                           f"\t\t\tstate = active\n\t\t}}\n\t}}\n\tstate = waiting\n}}\n"), ""
        if argv[0] == "systemctl":
            verb = argv[2]
            if verb == "daemon-reload":
                return 0, "", ""
            if verb == "enable":
                return 0, "", ""
            if verb == "restart":
                self.next_pid += 1
                self.loaded[argv[3]] = self.next_pid
                return 0, "", ""
            if verb == "disable":
                self.loaded.pop(argv[4], None)
                return 0, "", ""
            if verb == "reset-failed":
                return 0, "", ""
            if verb == "show":
                unit = argv[3]
                pid = self.loaded.get(unit)
                return 0, (f"LoadState={'loaded' if pid else 'not-found'}\n"
                           f"ActiveState={'active' if pid else 'inactive'}\nMainPID={pid or 0}\n"), ""
        if argv[0] == "loginctl":
            return 0, "Linger=no\n", ""
        return 127, "", f"{argv[0]}: not faked"


def _service(mac: bool, home: str, sysf: FakeSys, health=lambda h, p: True, **kw):
    return appservice.Service(kw.pop("name", None), platform="darwin" if mac else "linux",
                              home=home, uid=501, run=sysf, health=health,
                              sleep=lambda s: None, **kw)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_launchd():
    home = tempfile.mkdtemp(prefix="svc-home-")
    sysf = FakeSys(mac=True)
    svc = _service(True, home, sysf)
    port = _free_port()
    assert svc.status()["installed"] is False and svc.status()["loaded"] is False
    st = svc.install(appservice.Spec(port=port, state_dir="~/.tandemllm-t", python="/opt/py/bin/python3"))
    path = os.path.join(home, "Library", "LaunchAgents", "org.tandemllm.app.plist")
    assert st["path"] == path and os.path.isfile(path)
    with open(path, "rb") as f:
        pl = plistlib.load(f)
    d = os.path.join(home, ".tandemllm-t")
    assert pl["Label"] == "org.tandemllm.app" and pl["RunAtLoad"] is True and pl["KeepAlive"] is True
    assert pl["ProgramArguments"] == ["/opt/py/bin/python3", "-m", "proxy", "serve", "--host",
                                      "127.0.0.1", "--port", str(port), "--config-db",
                                      os.path.join(d, "config.sqlite3"), "--usage-ledger",
                                      os.path.join(d, "ledger.sqlite3"), "--conductor-config",
                                      os.path.join(d, "conductor")]
    assert pl["StandardOutPath"] == os.path.join(d, "logs", "tandem-app.log")
    assert pl["EnvironmentVariables"]["PYTHONUNBUFFERED"] == "1"
    assert pl["WorkingDirectory"] == ROOT              # a checkout runs from its own directory
    assert os.stat(d).st_mode & 0o077 == 0
    assert ["launchctl", "bootstrap", "gui/501", path] in sysf.calls
    assert st["running"] and st["loaded"] and st["pid"] == sysf.loaded["org.tandemllm.app"]
    assert st["url"] == f"http://127.0.0.1:{port}/" and st["kind"] == "launchd"
    again = svc.status()
    assert again["answers"] is True and again["url"] == f"http://127.0.0.1:{port}/"
    gone = svc.uninstall()
    assert ["launchctl", "bootout", "gui/501/org.tandemllm.app"] in sysf.calls
    assert gone["removed"] and not os.path.exists(path)
    assert not gone["loaded"] and not gone["running"] and not gone["installed"]
    assert not sysf.loaded
    assert os.path.isdir(d), "the state dir stays"
    # an env file and a name of its own (the live check's throwaway service)
    svc2 = _service(True, home, sysf, name="org.tandemllm.app-check")
    svc2.install(appservice.Spec(port=port, state_dir="/tmp/x", env_file="~/app.env",
                                 env={"TANDEM_NODE_AGENT": "off"}))
    pl = plistlib.load(open(svc2.path, "rb"))
    assert pl["ProgramArguments"][-2:] == ["--env-file", os.path.join(home, "app.env")]
    assert pl["EnvironmentVariables"]["TANDEM_NODE_AGENT"] == "off"
    svc2.uninstall()
    assert not os.path.exists(svc2.path) and not sysf.loaded


def test_systemd():
    home = tempfile.mkdtemp(prefix="svc-home-")
    sysf = FakeSys(mac=False)
    svc = _service(False, home, sysf, name="tandemapp-check")
    port = _free_port()
    st = svc.install(appservice.Spec(host="0.0.0.0", port=port, state_dir="~/ctl",
                                     env_file="~/ctl/app.env", python="/v/bin/python",
                                     env={"TANDEM_LOCAL_NODE": "off"}))
    path = os.path.join(home, ".config", "systemd", "user", "tandemapp-check.service")
    text = open(path).read()
    line = next(x for x in text.splitlines() if x.startswith("ExecStart="))
    argv = shlex.split(line[len("ExecStart="):])
    d = os.path.join(home, "ctl")
    assert argv == ["/v/bin/python", "-m", "proxy", "serve", "--host", "0.0.0.0", "--port",
                    str(port), "--config-db", os.path.join(d, "config.sqlite3"),
                    "--usage-ledger", os.path.join(d, "ledger.sqlite3"), "--conductor-config",
                    os.path.join(d, "conductor"), "--env-file", os.path.join(d, "app.env")]
    assert "Environment=TANDEM_LOCAL_NODE=off" in text and "Restart=on-failure" in text
    assert "WantedBy=default.target" in text
    assert [c[2] for c in sysf.calls if c[0] == "systemctl" and c[2] in
            ("daemon-reload", "enable", "restart")] == ["daemon-reload", "enable", "restart"]
    assert st["running"] and st["pid"] and st["linger"] is False and st["kind"] == "systemd"
    assert st["url"] == f"http://127.0.0.1:{port}/"
    gone = svc.uninstall()
    assert ["systemctl", "--user", "disable", "--now", "tandemapp-check.service"] in sysf.calls
    assert ["systemctl", "--user", "reset-failed", "tandemapp-check.service"] in sysf.calls
    assert gone["removed"] and not os.path.exists(path)
    assert not gone["loaded"] and not gone["running"] and not sysf.loaded


def test_refusals():
    home = tempfile.mkdtemp(prefix="svc-home-")
    sysf = FakeSys(mac=True)
    busy = socket.socket()
    busy.bind(("127.0.0.1", 0))
    busy.listen(1)
    port = busy.getsockname()[1]
    try:
        try:
            _service(True, home, sysf).install(appservice.Spec(port=port))
            raise AssertionError("a busy port was taken")
        except appservice.ServiceError as exc:
            assert exc.kind == "port_in_use" and str(port) in exc.message
        assert not os.path.exists(os.path.join(home, "Library", "LaunchAgents",
                                               "org.tandemllm.app.plist"))
    finally:
        busy.close()
    port = _free_port()
    never = _service(True, home, sysf, health=lambda h, p: False, wait_s=0.01)
    try:
        never.install(appservice.Spec(port=port, state_dir="~/s"))
        raise AssertionError("a silent service passed")
    except appservice.ServiceError as exc:
        assert exc.kind == "not_answering" and "tandem-app.log" in exc.message
    never.uninstall()
    for bad in ("-x", "a b", "a/b", ".hidden"):
        try:
            _service(True, home, sysf, name=bad)
            raise AssertionError(bad)
        except appservice.ServiceError as exc:
            assert exc.kind == "bad_request"
    try:
        appservice.Service(platform="win32", home=home)
        raise AssertionError("win32")
    except appservice.ServiceError as exc:
        assert exc.kind == "unsupported"
    # a reinstall of a loaded one boots the old one out first
    svc = _service(True, home, sysf)
    svc.install(appservice.Spec(port=port))
    first = sysf.loaded["org.tandemllm.app"]
    svc.install(appservice.Spec(port=port))
    i = max(i for i, c in enumerate(sysf.calls) if c[:2] == ["launchctl", "bootout"])
    assert sysf.calls[i + 1][:2] == ["launchctl", "bootstrap"]
    assert sysf.loaded["org.tandemllm.app"] != first
    svc.uninstall()


def test_command_status():
    home = tempfile.mkdtemp(prefix="svc-home-")
    env = {k: v for k, v in os.environ.items() if not k.startswith("TANDEM_")}
    env["HOME"] = home
    name = "org.tandemllm.app-absent-test" if sys.platform == "darwin" else \
        "tandemapp-absent-test"
    r = subprocess.run([sys.executable, "-m", "proxy", "service", "status", "--name", name,
                        "--json"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 3, (r.returncode, r.stdout, r.stderr)
    st = json.loads(r.stdout)
    assert st["name"] == name and st["installed"] is False and st["loaded"] is False
    r = subprocess.run([sys.executable, "-m", "proxy", "service", "status", "--name", name],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert "not installed" in r.stdout and r.returncode == 3
    r = subprocess.run([sys.executable, "-m", "proxy", "service", "--help"], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "install" in r.stdout and "--state-dir" in r.stdout


def test_app_extra():
    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
        p = tomllib.load(f)["project"]
    app = p["optional-dependencies"]["app"]
    names = {d.split(">")[0].split("=")[0].split("[")[0].strip().lower() for d in app}
    assert names == {"psutil"}, app
    assert not names & {"torch", "torchvision", "numpy", "transformers", "nvidia-ml-py"}
    assert p["scripts"]["tandem-app"] == "proxy.__main__:main"
    text = open(os.path.join(ROOT, "packaging", "homebrew", "tandemapp.rb")).read()
    assert 'depends_on "cryptography"' in text and "tandem-app service install" in text
    assert 'resource "torch' not in text
    # the formula installs app/ of the repository's tarball (its root once TandemApp ships alone)
    assert 'buildpath/"app/pyproject.toml"' in text and "venv.pip_install_and_link src" in text
    desc = next(x for x in text.splitlines() if x.strip().startswith("desc "))
    assert "DGX" not in desc and len(desc.split('"')[1]) <= 80, desc


def test_local_label_wheel():
    assert _version.pep440("0.15.0+test") == "0.15.0+test"
    assert _version.pep440("0.15.1-test+Fix.2") == "0.15.1.dev0+fix.2"
    assert preflight.same_release("0.15.0+test", "0.15.0")
    out = tempfile.mkdtemp(prefix="wheel-")
    r = subprocess.run([sys.executable, "-m", "proxy", "package", "--version", "0.15.0+test",
                        "--out", out], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    path = r.stdout.splitlines()[0]
    assert os.path.basename(path) == "tandemapp-0.15.0+test-py3-none-any.whl"
    assert r.stdout.splitlines()[1].startswith("[package] tandemapp 0.15.0+test, sha256 "), \
        r.stdout
    assert nodeinstall.wheel_version(path) == "0.15.0+test"
    assert nodeinstall.wheel_version("tandemllm-0.15.0+test-py3-none-any.whl") is None
    with zipfile.ZipFile(path) as z:
        meta = z.read("tandemapp-0.15.0+test.dist-info/METADATA").decode()
        names = z.namelist()
    assert "Name: tandemapp" in meta.splitlines() and "Version: 0.15.0+test" in meta
    assert "proxy/appservice.py" in names and "proxy/nodeupdate.py" in names
    assert "proxy/dashboard/index.html" in names and "tandemapp/__init__.py" in names
    assert not [n for n in names if n.startswith("server/")]
    pkg = nodeinstall.Package(out, env={"TANDEM_NODE_WHEEL": path})
    assert pkg.target() == "0.15.0+test" and pkg.wheel() == path
    assert nodeinstall.Package(out, version="0.15.0", env={}).target() == "0.15.0"


if __name__ == "__main__":
    run(globals())
