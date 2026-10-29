"""VIS-247: the Add Spark wizard's controller side (VIS-246): its session, preflight, the install
over SSH, the tunnel to a rented node and the lease reminder; the 5.1 follow-ups (the route of
/pair/ssh, the controller's own node advertised as owned, mDNS off container bridges).

Every answer is held to contracts/fleet-v1. The boxes are fakes: an `ssh` that runs each
command in a temporary HOME on this machine (after checking the host key and the password, the key
or the key file, as OpenSSH would), a `python3` whose venv gets a `pip` that unpacks the wheel the
controller built (so the TandemNode that runs is the wheel's own code), and a `systemctl` that
starts a unit's ExecStart as a process. The agents are real (loopback, mTLS once paired). The
preflight script's facts are canned where a test needs a given box; one test runs it for real.
Addresses are 192.0.2.x and 198.51.100.x; every name is invented.

  1  the session: none, a new one, busy, Start over, resume from another device (GET), the steps,
     abandoned; every answer validates                               test_session_lifecycle
  2  Gherkin "LAN Spark with TandemLLM 0.15.0 + pairing code": Check passes through the agent,
     Install is skipped, Name applies, the node is in the fleet          test_lan_spark_by_code
  3  Gherkin "Bare box over SSH user + password": Check over SSH, the wheel built here, the
     agent installed from it, the unit started, paired; the password in no file, row, log or
     answer, and forgotten after the install                       test_bare_box_over_ssh_install
  4  Gherkin "Rented": the ssh line + a key, a lease end: paired through the tunnel, the lease on
     the record, the reminder 24 h before it ends fires as an alert; the kept key 0600, gone with
     the node                                                            test_rented_through_tunnel
  5  Gherkin "Resume": closed at Install, reopened (a new controller process): the Install step
     with its progress; a controller stopped mid-install says so      test_resume_at_install
  6  version mismatch: another TandemNode release is a red row with its fix and Install refuses
     it; every red row names its fix                          test_version_mismatch_and_fixes
  7  Link only with two or more new LAN nodes; skipped with why otherwise; configure 501
                                                                         test_link_needs_two
  8  the guard: the session reads and writes as discovery, connect/check/install as pairing
                                                                              test_guard
  9  /pair/ssh takes the route: -J jump hosts, -i the key file; host key scan through a jump
                                                                        test_pair_ssh_route
 9b  an SSH login to the controller's own machine is refused before the adopt: its agent's
     trust store untouched                                          test_own_machine_refused
 10  the agent: /node/preflight, PUT /node/fleet (secret only) → mDNS says owned; mDNS skips
     docker/br-/veth/virbr                                      test_agent_preflight_and_claim
 11  the tunnel: its argv, the local port kept, the back-off of a process that exits
                                                                        test_tunnel_process
 12  the preflight script runs on this box and parses; install.sh --runtime-only --dry-run
     stops after the Python environment                     test_script_and_runtime_only
 13  the wheel this controller builds: TandemApp's packages and the built page, no engine
     code, the console scripts, cryptography; cached by content          test_the_node_wheel
 14  the engine step (SRV-747): TandemApp 1.0.0 with a stub installer (TANDEM_ENGINE_INSTALL_SH)
     whose VERSION says 0.16.0 runs it on the box with --ref v0.16.0; TANDEM_ENGINE_REF
     overrides that; without an installer the step fails and names the variable; a ref that is no
     tag name is refused before any login          test_the_engine_step_takes_the_installer_and_ref
 15  the version row reads TandemNode only (SRV-747): the preflight script in a real shell, HOME a
     box with ~/TandemLLM/src/VERSION 0.14.2 and no TandemNode, says nothing installed; a TandemNode
     in the install prefix is read; a red row's fix names TandemNode, the fleet's version and the
     engine ref                                               test_preflight_reads_tandemnode_only

    python tests/test_add_spark.py
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import types
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_fleet as tf  # noqa: E402
import test_node_trust as tnt  # noqa: E402
from proxy import _version, addspark, discovery, fleetwatch, nodeagent, nodeinstall  # noqa: E402
from proxy import preflight, sshonce, sshtunnel  # noqa: E402
from proxy_util import Proxy, run  # noqa: E402

VERSION = _version.release()
PASSWORD = "Gx10-fixture-pw-5521"
KEY = tnt.KEY
HOSTKEY = tnt.HOSTKEY
BOX = "192.0.2.63"
RENTED = "198.51.100.7"
FACTS = {"system": "Linux", "arch": "aarch64", "kernel": "6.11.0-1015-nvidia",
         "os": "Ubuntu 24.04.3 LTS", "os_id": "ubuntu 24.04", "dgx": "NVIDIA DGX Spark",
         "dmi": "ASUSTeK COMPUTER INC.|GX10|DGX Spark", "driver_smi": "580.95.05",
         "gpu": "NVIDIA GB10", "cuda": "13.0", "mem_kb": str(128 * 1024 * 1024),
         "disk_kb": str(3000 * 1024 * 1024), "disk_mount": "/", "tandemllm": "",
         "cx7": "enp1s0f0np0:4: ACTIVE:200,enP2p1s0f1np1:1: DOWN:?", "sudo": "password",
         "ntp": "yes", "ntp_on": "yes", "linger": "yes", "python": "3.12", "venv": "yes",
         "systemd": "yes", "user": "spark"}


def ok(obj, name):
    return tf.ok(obj, name)


def session_ok(s):
    ok(s, "add-session")
    assert PASSWORD not in json.dumps(s) and "PRIVATE KEY" not in json.dumps(s)
    return s


# ---------------------------------------------------------------------------- the fake box

FAKE_SSH = r'''#!@PY@
import json, os, subprocess, sys, time
st = json.load(open(os.environ["FAKE_BOX"]))
args = sys.argv[1:]
with open(st["log"], "a") as f:
    f.write(json.dumps({"argv": args}) + "\n")
opts, i, port, user, ident, jump = {}, 0, "22", None, None, None
while i < len(args):
    a = args[i]
    if a == "-o":
        k, _, v = args[i + 1].partition("="); opts[k] = v; i += 2; continue
    if a in ("-p", "-l", "-i", "-J"):
        v = args[i + 1]
        if a == "-p": port = v
        elif a == "-l": user = v
        elif a == "-i": ident = v
        else: jump = v
        i += 2; continue
    if a in ("-T", "-N"):
        i += 1; continue
    break
host, command = args[i], args[i + 1]
name = host if port == "22" else "[%s]:%s" % (host, port)
if opts.get("StrictHostKeyChecking") == "accept-new":
    open(opts["UserKnownHostsFile"], "a").write("%s %s\n" % (name, st["hostkey"]))
    sys.stderr.write("Permission denied (none).\n"); sys.exit(255)
kh = open(opts["UserKnownHostsFile"]).read().split()
if kh[0] != name or " ".join(kh[1:3]) != st["hostkey"]:
    sys.stderr.write("Host key verification failed.\n"); sys.exit(255)
if opts.get("PubkeyAuthentication") == "no":
    got = subprocess.run([os.environ["SSH_ASKPASS"]], capture_output=True, text=True).stdout
    good = got.rstrip("\n") == st["password"]
elif ident:
    good = open(ident).read().strip() == st["key"].strip()
else:
    good = st.get("agent", False)
if not good:
    sys.stderr.write("%s@%s: Permission denied (publickey,password).\n" % (user, host))
    sys.exit(255)
if "TANDEM-PF %s" in command and st.get("facts") is not None:
    facts = dict(st["facts"], now=str(int(time.time())))
    for k, v in facts.items():
        print("TANDEM-PF %s=%s" % (k, v))
    sys.exit(0)
env = {k: v for k, v in os.environ.items()
       if k not in ("TANDEM_SSH_SECRET", "SSH_ASKPASS", "SSH_ASKPASS_REQUIRE", "PYTHONPATH")}
env.update(HOME=st["home"], PATH=st["path"], TANDEM_NODE_MDNS="off", FAKE_BOX=os.environ["FAKE_BOX"],
           SSH_CONNECTION="127.0.0.1 50000 127.0.0.1 " + port, TANDEM_MACHINE_ID_FILE=st["machine"])
sys.exit(subprocess.run(["sh", "-c", command], env=env, cwd=st["home"]).returncode)
'''

FAKE_KEYSCAN = r'''#!@PY@
import json, os, sys
st = json.load(open(os.environ["FAKE_BOX"]))
port, host = sys.argv[sys.argv.index("-p") + 1], sys.argv[-1]
print(("%s" if port == "22" else "[%s]:" + port) % host, st["hostkey"])
'''

# the box's python3: a real venv (no pip), with a pip that installs a wheel as pip does: its files
# into site-packages, its console scripts into bin, its dependency (cryptography) from this
# interpreter's own site
FAKE_PY = r'''#!@PY@
import os, subprocess, sys
if sys.argv[1:3] == ["-m", "venv"]:
    d = sys.argv[3]
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", d], check=True)
    site = subprocess.run([os.path.join(d, "bin", "python"), "-c",
                           "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                          capture_output=True, text=True, check=True).stdout.strip()
    os.makedirs(os.path.join(site, "pip"), exist_ok=True)
    open(os.path.join(site, "pip", "__init__.py"), "w").write("")
    open(os.path.join(site, "pip", "__main__.py"), "w").write(open(os.environ["FAKE_PIP"]).read())
    import cryptography
    open(os.path.join(site, "deps.pth"), "w").write(os.path.dirname(cryptography.__path__[0]) + "\n")
    sys.exit(0)
os.execv(sys.executable, [sys.executable] + sys.argv[1:])
'''

FAKE_PIP = r'''import configparser, os, sys, sysconfig, zipfile
args = [a for a in sys.argv[1:] if not a.startswith("-")]
assert args[0] == "install", args
wheel = args[-1]
site = sysconfig.get_paths()["purelib"]
with zipfile.ZipFile(wheel) as z:
    z.extractall(site)
    ep = next(n for n in z.namelist() if n.endswith(".dist-info/entry_points.txt"))
    cp = configparser.ConfigParser()
    cp.read_string(z.read(ep).decode())
for name, target in cp["console_scripts"].items():
    mod, fn = target.split(":")
    p = os.path.join(os.path.dirname(sys.executable), name)
    open(p, "w").write("#!%s\nimport sys\nfrom %s import %s\nsys.exit(%s())\n"
                       % (sys.executable, mod, fn, fn))
    os.chmod(p, 0o755)
print("Successfully installed " + os.path.basename(wheel).split("-py3")[0])
'''

FAKE_SYSTEMCTL = r'''#!@PY@
import json, os, re, signal, subprocess, sys
args = [a for a in sys.argv[1:] if a not in ("--user", "--no-pager", "--now")]
home = os.environ["HOME"]
units = os.path.join(home, ".config", "systemd", "user")
pids = os.path.join(home, ".units")
os.makedirs(pids, exist_ok=True)
def pid_of(u):
    try:
        return int(open(os.path.join(pids, u + ".pid")).read())
    except (OSError, ValueError):
        return None
if args[0] == "daemon-reload":
    sys.exit(0)
if args[0] == "enable":
    u = args[1]
    text = open(os.path.join(units, u)).read()
    cmd = re.search(r"^ExecStart=(.*)$", text, re.M).group(1).replace("%h", home)
    log = open(os.path.join(pids, u + ".log"), "a")
    p = subprocess.Popen(["sh", "-c", "exec " + cmd], stdout=log, stderr=log,
                         start_new_session=True, env=dict(os.environ, TANDEM_NODE_MDNS="off"))
    open(os.path.join(pids, u + ".pid"), "w").write(str(p.pid))
    print("Created symlink default.target.wants/%s" % u)
    sys.exit(0)
if args[0] in ("stop", "disable"):
    p = pid_of(args[1])
    if p:
        os.kill(p, signal.SIGTERM)
    sys.exit(0)
if args[0] == "status":
    print("%s: %s" % (args[1], "active (running)" if pid_of(args[1]) else "inactive"))
    sys.exit(0)
sys.exit(0)
'''


class Box:
    """A box the controller reaches over the fake ssh: its own HOME, PATH and state."""

    def __init__(self, facts: dict | None = None, password: str = PASSWORD, key: str = KEY):
        self.dir = tempfile.mkdtemp(prefix="add-box-")
        self.bin = os.path.join(self.dir, "bin")            # the controller's ssh
        self.rbin = os.path.join(self.dir, "rbin")          # the box's own commands
        self.home = os.path.join(self.dir, "home")
        for d in (self.bin, self.rbin, self.home):
            os.makedirs(d)
        for where, name, text in ((self.bin, "ssh", FAKE_SSH), (self.bin, "ssh-keyscan",
                                                                 FAKE_KEYSCAN),
                                  (self.rbin, "python3", FAKE_PY),
                                  (self.rbin, "systemctl", FAKE_SYSTEMCTL)):
            p = os.path.join(where, name)
            with open(p, "w") as f:
                f.write(text.replace("@PY@", sys.executable))
            os.chmod(p, 0o755)
        self.pip = os.path.join(self.dir, "fake_pip.py")
        with open(self.pip, "w") as f:
            f.write(FAKE_PIP)
        self.log = os.path.join(self.dir, "argv.log")
        self.state = os.path.join(self.dir, "box.json")
        machine = os.path.join(self.dir, "machine-id")
        with open(machine, "w") as f:
            f.write("fedcba9876543210fedcba9876543210\n")
        self.st = {"hostkey": HOSTKEY, "password": password, "key": key, "home": self.home,
                   "path": f"{self.rbin}:/usr/bin:/bin", "log": self.log, "machine": machine,
                   "facts": dict(FACTS) if facts is None else facts}
        self.save()
        os.environ["FAKE_BOX"] = self.state
        os.environ["FAKE_PIP"] = self.pip

    def save(self):
        with open(self.state, "w") as f:
            json.dump(self.st, f)

    def ssh(self, known_hosts: str) -> sshonce.Ssh:
        return sshonce.Ssh(known_hosts, ssh=os.path.join(self.bin, "ssh"),
                           keyscan=os.path.join(self.bin, "ssh-keyscan"))

    def argv(self) -> list[list[str]]:
        try:
            with open(self.log) as f:
                return [json.loads(x)["argv"] for x in f]
        except FileNotFoundError:
            return []

    def stop(self):
        for p in glob.glob(os.path.join(self.home, ".units", "*.pid")):
            try:
                os.killpg(int(open(p).read()), signal.SIGTERM)
            except (OSError, ValueError):
                pass


class Rows:
    """Discovery's list as the wizard reads it (Fleet.discover().snapshot())."""

    def __init__(self, rows):
        self.rows = rows
        self.learned = []

    def snapshot(self, since=None):
        return {"rows": self.rows}

    def learn(self, host, port=22, **facts):
        self.learned.append((host, facts))
        if facts.get("installed") is False and facts.get("dmi"):
            for r in self.rows:
                if host in r["addresses"]:
                    r.update(kind="gb10", title="GB10 box, nothing installed",
                             detail="ASUS Ascent GX10 · TandemNode is not installed")


def row(rid, address, kind="unknown", connect=("ssh",), **kw):
    r = {"id": rid, "kind": kind, "title": kw.pop("title", "Unknown device"),
         "detail": kw.pop("detail", ""), "name": kw.pop("name", None), "addresses": [address],
         "sources": ["scan"], "node": kw.pop("node", None), "hw": kw.pop("hw", None),
         "ssh": {"port": 22, "banner": None, "os": None, "host_key": None},
         "ssh_config": kw.pop("ssh_config", None), "fleet": None, "connect": list(connect),
         "evidence": [], "seen_at": 0, "seq": 0}
    r.update(kw)
    return r


def wizard_on(px, rows=None, box: Box | None = None):
    f = tf.fleet_on(px, tf.local_node(tf.fake_root()))
    f._discovery = Rows(rows or [])
    t = f.trust()
    if box is not None:
        t.ssh = box.ssh(t.ssh.known_hosts)
    w = f.wizard()
    w.fleet_version = lambda: VERSION
    return f, w


def call(px, method, path, body=None):
    st, out = tf.send(px, method, path, body) if body is not None or method != "GET" \
        else tf.get(px, path)
    return st, out


def until(fn, s=60.0):
    end = time.monotonic() + s
    while time.monotonic() < end:
        got = fn()
        if got:
            return got
        time.sleep(0.2)
    return None


def text_of(paths) -> str:
    return tnt._all_text(paths)


# ---------------------------------------------------------------------------- the tests

def test_session_lifecycle():
    with Proxy() as px:
        f, w = wizard_on(px)
        st, got = call(px, "GET", "/v1/fleet/add")
        assert st == 200 and ok(got, "add")["session"] is None and got["fleet_version"] == VERSION
        st, s = call(px, "POST", "/v1/fleet/add", {})
        assert st == 200 and session_ok(s)["step"] == "find" and s["state"] == "active"
        sid = s["id"]
        st, err = call(px, "POST", "/v1/fleet/add", {})
        assert st == 409 and ok(err, "error")["error"]["type"] == "busy"
        # another device resumes it: the same session, where the user left it
        st, s2 = call(px, "PATCH", f"/v1/fleet/add/{sid}", {"step": "connect"})
        assert st == 200 and session_ok(s2)["step"] == "connect"
        st, got = call(px, "GET", "/v1/fleet/add")
        assert got["session"]["id"] == sid and got["session"]["step"] == "connect"
        session_ok(got["session"])
        # kept in the config database: a new controller process reads it
        w2 = addspark.Wizard(f)
        assert w2.view(w2.cfg.add_session())["step"] == "connect"
        for bad in ({"step": "later"}, {"names": []}, {}, {"step": "find", "x": 1}):
            st, err = call(px, "PATCH", f"/v1/fleet/add/{sid}", bad)
            assert st == 400 and ok(err, "error"), bad
        st, err = call(px, "PATCH", f"/v1/fleet/add/{sid}", {"step": "done"})
        assert st == 409 and "no device" in err["error"]["message"]
        # Start over: the open one is abandoned, a new one starts at Find
        st, s3 = call(px, "POST", "/v1/fleet/add", {"replace": True})
        assert st == 200 and s3["id"] != sid and s3["step"] == "find"
        st, old = call(px, "GET", f"/v1/fleet/add/{sid}")
        assert st == 200 and session_ok(old)["state"] == "abandoned"
        st, err = call(px, "PATCH", f"/v1/fleet/add/{sid}", {"step": "check"})
        assert st == 409
        st, gone = call(px, "DELETE", f"/v1/fleet/add/{s3['id']}")
        assert st == 200 and gone["state"] == "abandoned"
        assert call(px, "GET", "/v1/fleet/add")[1]["session"] is None
        assert call(px, "GET", "/v1/fleet/add/a-nothing")[0] == 404


def test_lan_spark_by_code():
    n = tnt.Node("ADD-CODE-0001")
    try:
        with Proxy() as px:
            win = n.window()
            r = row("d-60", "127.0.0.1", "spark", ("code", "ssh"), title="Spark with TandemLLM "
                    f"{VERSION}", name="spark-lab",
                    node={"id": n.agent.node_id(), "class": "spark", "version": VERSION,
                          "hw": "NVIDIA DGX Spark", "paired": False, "owner": None,
                          "listen": "loopback", "window": win["port"], "port": n.port,
                          "virtual": False})
            f, w = wizard_on(px, [r])
            n.agent.preflight = lambda: {"facts": dict(FACTS, tandemllm=VERSION,
                                                       now=str(int(time.time()))), "at": 0}
            s = call(px, "POST", "/v1/fleet/add", {})[1]
            sid = s["id"]
            st, s = call(px, "POST", f"/v1/fleet/add/{sid}/targets", {"row": "d-60"})
            assert st == 200 and session_ok(s)["targets"][0]["connect"] == ["code", "ssh"]
            tid = s["targets"][0]["id"]
            assert call(px, "POST", f"/v1/fleet/add/{sid}/targets", {"row": "d-60"})[0] == 409
            st, err = call(px, "POST", f"/v1/fleet/add/{sid}/targets/{tid}/connect",
                           {"method": "code", "code": "000000"})
            assert st == 403 and err["error"]["type"] == "wrong_code", err
            assert call(px, "GET", f"/v1/fleet/add/{sid}")[1]["targets"][0]["error"]["type"] == \
                "wrong_code"
            st, s = call(px, "POST", f"/v1/fleet/add/{sid}/targets/{tid}/connect",
                         {"method": "code", "code": win["code"]})
            assert st == 200, s
            t = session_ok(s)["targets"][0]
            assert t["state"] == "paired" and t["via"] == "code" and t["node"] == n.agent.node_id()
            # Check passes, through the node's agent; Install is skipped
            st, s = call(px, "POST", f"/v1/fleet/add/{sid}/check", {})
            t = session_ok(s)["targets"][0]
            assert t["preflight"]["via"] == "agent"
            assert t["preflight"]["verdict"] == {"fails": 0, "warns": 0, "install": "skip",
                                                 "why": t["preflight"]["verdict"]["why"]}, t
            assert t["install"]["state"] == "skip"
            st, s = call(px, "POST", f"/v1/fleet/add/{sid}/install", {})
            assert st == 200 and s["targets"][0]["install"]["state"] == "skip"
            # one LAN node: Link says why it is skipped
            st, s = call(px, "PATCH", f"/v1/fleet/add/{sid}", {"step": "link"})
            assert s["link"]["state"] == "not_needed" and "only new Spark" in s["link"]["why"]
            # Name, then Done: the node is in the fleet with its name
            st, s = call(px, "PATCH", f"/v1/fleet/add/{sid}", {
                "step": "name", "names": {tid: {"alias": "Spark 5", "colour": 5, "icon": "rack",
                                                "tags": ["desk"]}}})
            assert st == 200 and s["targets"][0]["name"]["alias"] == "Spark 5"
            st, err = call(px, "PATCH", f"/v1/fleet/add/{sid}", {
                "names": {tid: {"alias": f.cfg.nodes()[0]["alias"]}}, "step": "done"})
            assert st == 400 and "called" in err["error"]["message"], err
            st, s = call(px, "PATCH", f"/v1/fleet/add/{sid}", {
                "names": {tid: {"alias": "Spark 5"}}, "step": "done"})
            assert st == 200 and session_ok(s)["state"] == "done" and s["step"] == "done"
            st, node = tf.get(px, f"/v1/fleet/nodes/{n.agent.node_id()}")
            assert st == 200 and ok(node, "node")["alias"] == "Spark 5" and \
                node["colour"] == 5 and node["icon"] == "rack" and node["status"] == "up"
            assert call(px, "GET", "/v1/fleet/add")[1]["session"] is None
    finally:
        n.close()


def _pair_bare(px, sid, tid, host_key_check=True):
    st, s = call(px, "POST", f"/v1/fleet/add/{sid}/targets/{tid}/hostkey", {})
    assert st == 200, s
    hk = session_ok(s)["targets"][-1]["host_key"]
    assert hk["known"] == "new" and hk["fingerprint"] == sshonce.fingerprint(HOSTKEY.split()[1])
    if host_key_check:
        st, err = call(px, "POST", f"/v1/fleet/add/{sid}/targets/{tid}/connect",
                       {"method": "ssh", "user": "spark", "password": PASSWORD})
        assert st == 409 and err["error"]["type"] == "host_key_unconfirmed"
    return hk


def test_bare_box_over_ssh_install():
    box = Box()
    try:
        with Proxy() as px:
            f, w = wizard_on(px, [row("d-63", BOX, name="gx10-7f3a")], box)
            sid = call(px, "POST", "/v1/fleet/add", {})[1]["id"]
            tid = call(px, "POST", f"/v1/fleet/add/{sid}/targets", {"row": "d-63"})[1][
                "targets"][0]["id"]
            hk = _pair_bare(px, sid, tid)
            st, err = call(px, "POST", f"/v1/fleet/add/{sid}/targets/{tid}/connect", {
                "method": "ssh", "user": "spark", "password": "wrong", "host_key":
                hk["fingerprint"]})
            assert st == 502 and err["error"]["type"] == "ssh_failed"
            st, s = call(px, "POST", f"/v1/fleet/add/{sid}/targets/{tid}/connect", {
                "method": "ssh", "user": "spark", "password": PASSWORD,
                "host_key": hk["fingerprint"]})
            assert st == 200, s
            t = session_ok(s)["targets"][0]
            # nothing installed: bare, the row a GB10, the password held in memory only
            assert t["state"] == "bare" and t["secret"]["held"] and t["ssh"]["auth"] == "password"
            assert t["kind"] == "gb10" and t["title"] == "GB10 box, nothing installed", t
            st, s = call(px, "POST", f"/v1/fleet/add/{sid}/check", {})
            t = session_ok(s)["targets"][0]
            assert t["preflight"]["via"] == "ssh" and t["preflight"]["verdict"]["install"] == \
                "needed" and t["preflight"]["verdict"]["fails"] == 0, t["preflight"]
            assert {r["id"] for r in t["preflight"]["rows"]} >= {
                "os", "kernel", "driver", "cuda", "disk", "memory", "tandemllm", "cx7", "sudo",
                "clock", "linger", "python"}
            # the install: no engine runtime here (its own test below), a virtual node's ports
            port = tnt.two_ports()
            st, s = call(px, "POST", f"/v1/fleet/add/{sid}/install", {"options": {tid: {
                "engine": False, "port": port, "virtual": "b1", "unit": "tandemnode-b1",
                "state_dir": "~/.tandemllm/node-b1"}}})
            assert st == 200, s
            assert not session_ok(s)["targets"][0]["secret"]["held"]   # the job has it now
            done = until(lambda: (lambda x: x if x["targets"][0]["install"]["state"] in (
                "done", "failed") else None)(call(px, "GET", f"/v1/fleet/add/{sid}")[1]), 90)
            assert done, "the install did not end"
            inst = session_ok(done)["targets"][0]["install"]
            assert inst["state"] == "done", json.dumps(inst, indent=1)[:3000]
            assert [x["state"] for x in inst["steps"]] == ["done", "done", "done", "skip",
                                                           "done", "done"], inst["steps"]
            assert any("uploaded tandemapp-" in x["text"] for x in inst["log"])
            t = done["targets"][0]
            assert t["state"] == "paired" and t["node"] and not t["secret"]["held"]
            node = ok(tf.get(px, f"/v1/fleet/nodes/{t['node']}")[1], "node")
            assert node["pairing"]["via"] == "ssh" and node["status"] == "up", node
            # the wheel the box runs is the one this controller built: its TandemNode answers
            assert node["version"] == VERSION or nodeinstall.same_version(node["version"],
                                                                          VERSION)
            whl = glob.glob(os.path.join(box.home, ".local", "share", "tandemllm", "pkg",
                                         "*.whl"))
            assert len(whl) == 1 and zipfile.ZipFile(whl[0]).getinfo("proxy/nodeagent.py")
            unit = open(os.path.join(box.home, ".config", "systemd", "user",
                                     "tandemnode-b1.service")).read()
            assert f"--port {port}" in unit and "--virtual b1" in unit
            # the password: forgotten (the vault empty), in no file, row, log or answer
            assert not w.vault
            assert call(px, "POST", f"/v1/fleet/add/{sid}/check", {})[0] == 200
            haystack = "\n".join([json.dumps(done), "\n".join(px.logs),
                                  text_of([px.dir, box.home, os.path.dirname(f.cfg.path)]),
                                  open(box.log).read()])
            assert PASSWORD not in haystack
            # Done with a default name: the session ends
            st, s = call(px, "PATCH", f"/v1/fleet/add/{sid}", {"step": "done"})
            assert st == 200 and s["state"] == "done"
            tf.send(px, "DELETE", f"/v1/fleet/nodes/{t['node']}", None)
    finally:
        box.stop()


def test_rented_through_tunnel():
    box = Box()
    try:
        with Proxy() as px:
            f, w = wizard_on(px, [], box)
            port = tnt.two_ports()
            spawned = []

            class Proc:
                pid, returncode, stderr = 4242, None, None

                def poll(self):
                    return None

                def terminate(self):
                    spawned.append("stopped")

                def wait(self, timeout=None):
                    return 0
            f._tunnels = sshtunnel.Tunnels(f.trust().ssh.known_hosts,
                                           spawn=lambda argv: (spawned.append(argv), Proc())[1],
                                           pick=lambda: port, probe=lambda p: True)
            sid = call(px, "POST", "/v1/fleet/add", {})[1]["id"]
            end = time.time() + 30 * 3600
            # the Rented form's line, read as /discovery/target reads it
            tgt = {"target": discovery.parse_ssh_line(f"ssh -p 2201 spark@{RENTED}")}
            st, s = call(px, "POST", f"/v1/fleet/add/{sid}/targets", {
                "ssh": tgt["target"], "rented": {"lease_end": end, "cost_per_hour": 2.4},
                "key": KEY})
            assert st == 200, s
            t = session_ok(s)["targets"][0]
            assert t["kind"] == "rented" and t["transport"] == "tunnel" and t["secret"]["held"]
            for bad in ({"ssh": "ssh x", "rented": {"lease_end": 1}},
                        {"ssh": "ssh -o ProxyCommand=nc x", "rented": {"lease_end": end}}):
                assert call(px, "POST", f"/v1/fleet/add/{sid}/targets", bad)[0] == 400, bad
            hk = _pair_bare(px, sid, t["id"], host_key_check=False)
            st, s = call(px, "POST", f"/v1/fleet/add/{sid}/targets/{t['id']}/connect", {
                "method": "ssh", "user": "spark", "key": KEY, "host_key": hk["fingerprint"]})
            assert st == 200 and s["targets"][0]["state"] == "bare", s
            call(px, "POST", f"/v1/fleet/add/{sid}/check", {})
            st, s = call(px, "POST", f"/v1/fleet/add/{sid}/install", {"options": {t["id"]: {
                "engine": False, "port": port, "virtual": "r1", "unit": "tandemnode-r1",
                "state_dir": "~/.tandemllm/node-r1"}}})
            assert st == 200, s
            done = until(lambda: (lambda x: x if x["targets"][0]["install"]["state"] in (
                "done", "failed") else None)(call(px, "GET", f"/v1/fleet/add/{sid}")[1]), 90)
            inst = done["targets"][0]["install"]
            assert inst["state"] == "done", json.dumps(inst, indent=1)[:3000]
            nid = done["targets"][0]["node"]
            row_ = f.cfg.node(nid)
            # reached through the tunnel to the node's loopback, never a WAN address
            assert row_["transport"] == "tunnel" and row_["trust"]["tunnel"]["host"] == RENTED
            assert row_["trust"]["tunnel"]["remote_port"] == port
            argv = spawned[0]
            assert argv[argv.index("-L") + 1] == f"127.0.0.1:{port}:127.0.0.1:{port}"
            assert "BatchMode=yes" in argv and argv[-1] == RENTED and "-p" in argv
            node = ok(tf.get(px, f"/v1/fleet/nodes/{nid}")[1], "node")
            assert node["transport"] == "tunnel" and node["status"] == "up"
            assert node["owned"] is False and "rented" in node["tags"] and node["icon"] == "cloud"
            assert abs(node["lease_end"] - end) < 1 and node["cost_per_hour"] == 2.4
            kept = row_["trust"]["tunnel"]["identity_file"]
            assert os.stat(kept).st_mode & 0o077 == 0 and open(kept).read().strip() == KEY.strip()
            # the reminder: 24 h before the lease ends, said in the session, fired by the rule
            rem = done["reminders"]
            assert rem and abs(rem[0]["at"] - (end - 24 * 3600)) < 1 and rem[0]["hours"] == 24
            rules = {r["id"]: r for r in tf.get(px, "/v1/fleet/rules")[1]["rules"]}
            assert rules["lease"]["condition"] == "lease ends in < 24 h"
            f.watch.observe(time.time(), f.readings(time.time()))
            assert not [a for a in f.watch.alerts() if a["id"] == f"lease:{nid}"]
            later = end - 23 * 3600
            f.watch.observe(later, f.readings(later))
            al = next(a for a in f.watch.alerts() if a["id"] == f"lease:{nid}")
            assert "lease ends in 23 h" in al["title"] and al["action"]["route"] == \
                f"#/sparks/{nid}"
            ev = tf.get(px, "/v1/fleet/events?type=alert")[1]["events"]
            assert any("lease ends" in e["text"] for e in ev)
            # released: the tunnel closed, the kept key removed
            assert tf.send(px, "DELETE", f"/v1/fleet/nodes/{nid}", None)[0] == 200
            assert "stopped" in spawned and not os.path.exists(kept)
            assert KEY.splitlines()[1] not in text_of([px.dir])
    finally:
        box.stop()


def test_resume_at_install():
    box = Box()
    try:
        with Proxy() as px:
            f, w = wizard_on(px, [row("d-63", BOX)], box)
            sid = call(px, "POST", "/v1/fleet/add", {})[1]["id"]
            tid = call(px, "POST", f"/v1/fleet/add/{sid}/targets", {"row": "d-63"})[1][
                "targets"][0]["id"]
            hk = _pair_bare(px, sid, tid, host_key_check=False)
            call(px, "POST", f"/v1/fleet/add/{sid}/targets/{tid}/connect", {
                "method": "ssh", "user": "spark", "password": PASSWORD,
                "host_key": hk["fingerprint"]})
            call(px, "POST", f"/v1/fleet/add/{sid}/check", {})
            # an install whose upload hangs: the page closes at Install
            gate = {"go": False}
            real = nodeinstall.Install._upload

            def slow(self):
                while not gate["go"]:
                    time.sleep(0.05)
                return real(self)
            nodeinstall.Install._upload = slow
            try:
                call(px, "PATCH", f"/v1/fleet/add/{sid}", {"step": "install"})
                port = tnt.two_ports()
                call(px, "POST", f"/v1/fleet/add/{sid}/install", {"options": {tid: {
                    "engine": False, "port": port, "virtual": "c1", "unit": "tandemnode-c1",
                    "state_dir": "~/.tandemllm/node-c1"}}})
                assert until(lambda: call(px, "GET", "/v1/fleet/add")[1]["session"]["targets"][
                    0]["install"]["steps"][1]["state"] == "running", 20)
                # reopened (another device): the Install step, with its progress
                s = call(px, "GET", "/v1/fleet/add")[1]["session"]
                assert s["step"] == "install"
                steps = s["targets"][0]["install"]["steps"]
                assert [x["state"] for x in steps[:2]] == ["done", "running"]
                assert call(px, "DELETE", f"/v1/fleet/add/{sid}")[0] == 409   # it is running
                # a controller that restarts now: the install is said to be interrupted
                w2 = addspark.Wizard(f)
                got = w2.view(w2.cfg.add_session())["targets"][0]["install"]
                assert got["state"] == "failed" and got["error"]["type"] == "interrupted"
                session_ok(w2.view(w2.cfg.add_session()))
            finally:
                gate["go"] = True
                nodeinstall.Install._upload = real
            assert until(lambda: call(px, "GET", f"/v1/fleet/add/{sid}")[1]["targets"][0][
                "install"]["state"] in ("done", "failed"), 90)
            nid = call(px, "GET", f"/v1/fleet/add/{sid}")[1]["targets"][0]["node"]
            if nid:
                tf.send(px, "DELETE", f"/v1/fleet/nodes/{nid}", None)
    finally:
        box.stop()


def test_version_mismatch_and_fixes():
    now = time.time()
    # another TandemNode release on the box: a red row naming the fix, Install refuses
    rows = preflight.checks(dict(FACTS, tandemllm="0.14.2", now=str(int(now))), "0.15.0", now)
    tv = next(r for r in rows if r["id"] == "tandemllm")
    assert tv["state"] == "fail" and "0.14.2" in tv["value"] and "0.15.0" in tv["fix"]
    assert preflight.verdict(rows, False)["install"] == "refused"
    assert preflight.verdict(preflight.checks(dict(FACTS, tandemllm="0.15.0"), "0.15.0", now),
                             True)["install"] == "skip"
    assert nodeinstall.same_version("0.15.1-test", "0.15.1.dev0")
    assert nodeinstall.same_version("0.15.0+flashnext-3.05", "0.15.0")      # a local build label
    assert next(r for r in preflight.checks(dict(FACTS, tandemllm="0.15.0+flashnext-3.05"),
                                            "0.15.0", now) if r["id"] == "tandemllm")["state"] \
        == "ok"
    assert not nodeinstall.same_version("0.15.0", "0.14.2")
    # every red row names its fix
    bad = dict(FACTS, system="Linux", driver_smi="", driver="", cuda="12.4",
               disk_kb=str(10 * 1024 * 1024), ntp="no", ntp_on="no", python="3.10",
               tandemllm="0.14.2", systemd="no", now=str(int(now + 90)))
    red = [r for r in preflight.checks(bad, "0.15.0", now) if r["state"] == "fail"]
    assert {r["id"] for r in red} >= {"driver", "cuda", "disk", "clock", "python", "tandemllm",
                                      "systemd"}, red
    assert all(r["fix"] and len(r["fix"]) > 20 for r in red), red
    warn = [r for r in preflight.checks(dict(FACTS, cx7="", sudo="none", linger="no",
                                             disk_kb=str(50 * 1024 * 1024)), "0.15.0", now)
            if r["state"] == "warn"]
    assert {r["id"] for r in warn} == {"cx7", "sudo", "linger", "disk"} and \
        all(r["fix"] for r in warn)
    # through the routes: the check says it, Install answers 409 version_mismatch
    box = Box(facts=dict(FACTS, tandemllm="0.14.2"))
    try:
        with Proxy() as px:
            f, w = wizard_on(px, [row("d-63", BOX)], box)
            sid = call(px, "POST", "/v1/fleet/add", {})[1]["id"]
            tid = call(px, "POST", f"/v1/fleet/add/{sid}/targets", {"row": "d-63"})[1][
                "targets"][0]["id"]
            hk = _pair_bare(px, sid, tid, host_key_check=False)
            call(px, "POST", f"/v1/fleet/add/{sid}/targets/{tid}/connect", {
                "method": "ssh", "user": "spark", "password": PASSWORD,
                "host_key": hk["fingerprint"]})
            s = call(px, "POST", f"/v1/fleet/add/{sid}/check", {})[1]
            assert s["targets"][0]["preflight"]["verdict"]["install"] == "refused"
            st, err = call(px, "POST", f"/v1/fleet/add/{sid}/install", {})
            assert st == 409 and ok(err, "error")["error"]["type"] == "version_mismatch"
            # the held password expires: Check asks for it again, saying why
            w.vault[(sid, tid)]["until"] = time.time() - 1
            st, err = call(px, "POST", f"/v1/fleet/add/{sid}/check", {})
            assert st == 409 and err["error"]["type"] == "secret_needed" and \
                "never stored" in err["error"]["message"]
            st, s = call(px, "POST", f"/v1/fleet/add/{sid}/check",
                         {"secrets": {tid: {"password": PASSWORD}}})
            assert st == 200 and s["targets"][0]["secret"]["held"]
    finally:
        box.stop()


def test_link_needs_two():
    nodes = [tnt.Node(f"ADD-LINK-000{i}") for i in (1, 2)]
    try:
        with Proxy() as px:
            rows = []
            for i, n in enumerate(nodes):
                win = n.window()
                n.code = win["code"]
                rows.append(row(f"d-{i}", "127.0.0.1", "spark", ("code",), name=f"s{i}",
                                node={"id": n.agent.node_id(), "class": "spark",
                                      "version": VERSION, "hw": "x", "paired": False,
                                      "owner": None, "listen": "loopback",
                                      "window": win["port"], "port": n.port, "virtual": True}))
            f, w = wizard_on(px, rows)
            sid = call(px, "POST", "/v1/fleet/add", {})[1]["id"]
            tids = []
            for i in range(2):
                st, s = call(px, "POST", f"/v1/fleet/add/{sid}/targets", {"row": f"d-{i}"})
                assert st == 200, s
                tids.append(s["targets"][-1]["id"])
            st, s = call(px, "POST", f"/v1/fleet/add/{sid}/targets/{tids[0]}/connect",
                         {"method": "code", "code": nodes[0].code})
            assert st == 200, s
            # one new node paired: no link to set up, and why
            st, s = call(px, "POST", f"/v1/fleet/add/{sid}/link", {"action": "test"})
            assert st == 200 and s["link"]["state"] == "not_needed" and s["link"]["why"]
            st, s = call(px, "POST", f"/v1/fleet/add/{sid}/targets/{tids[1]}/connect",
                         {"method": "code", "code": nodes[1].code})
            assert st == 200, s
            # two new LAN nodes: the link test runs on each; the fabric's sudo setup is not built
            st, s = call(px, "POST", f"/v1/fleet/add/{sid}/link", {"action": "configure"})
            assert st == 501 and s["error"]["type"] == "not_built"
            st, s = call(px, "POST", f"/v1/fleet/add/{sid}/link", {"action": "test"})
            assert st == 200 and session_ok(s)["link"]["state"] == "done", s["link"]
            res = s["link"]["results"]
            assert len(res) == 2 and all(r["test"] and r["test"]["rtt_ms"] is not None
                                         for r in res), res
            st, s = call(px, "POST", f"/v1/fleet/add/{sid}/link", {"action": "skip"})
            assert s["link"]["state"] == "skipped"
            for n in nodes:
                tf.send(px, "DELETE", f"/v1/fleet/nodes/{n.agent.node_id()}", None)
    finally:
        for n in nodes:
            n.close()


def test_guard():
    lan = {"X-Real-IP": "192.0.2.77"}
    with Proxy(env={"TANDEM_ADMIN_TOKEN": tf.ADMIN}, login="on") as px:
        f, w = wizard_on(px)
        st, _ = tf.get(px, "/v1/fleet/add", headers=lan)
        assert st == 401
        auth = dict(lan, Authorization=f"Bearer {tf.ADMIN}")
        st, s = tf.send(px, "POST", "/v1/fleet/add", {}, headers=auth)
        assert st == 200
        for path in ("check", "install"):
            assert tf.send(px, "POST", f"/v1/fleet/add/{s['id']}/{path}", {}, headers=lan)[0] \
                == 401
        assert tf.send(px, "POST", f"/v1/fleet/add/{s['id']}/check", {}, headers=auth)[0] == 200


def test_pair_ssh_route():
    box = Box()
    try:
        with Proxy() as px:
            f, w = wizard_on(px, [], box)
            kf = os.path.join(box.dir, "rent_ed25519")
            with open(kf, "w") as fh:
                fh.write(KEY)
            os.chmod(kf, 0o600)
            body = {"host": RENTED, "port": 2201, "user": "spark", "key_file": kf,
                    "jumps": [{"user": "hop", "host": "198.51.100.1", "port": 22}]}
            st, hk = tf.send(px, "POST", "/v1/fleet/pair/ssh/hostkey",
                             {"host": RENTED, "port": 2201, "jumps": body["jumps"]})
            assert st == 200 and ok(hk, "hostkey")["known"] == "new", hk
            scan = box.argv()[-1]
            assert scan[scan.index("-J") + 1] == "hop@198.51.100.1" and \
                "PreferredAuthentications=none" in scan
            ok(body, "pair-ssh")
            st, err = tf.send(px, "POST", "/v1/fleet/pair/ssh", body)
            assert st == 409 and err["error"]["type"] == "not_installed", err
            login = box.argv()[-1]
            assert login[login.index("-J") + 1] == "hop@198.51.100.1"
            assert login[login.index("-i") + 1] == kf      # the file itself, not a copy
            for bad in (dict(body, key_file="/nowhere/key"), dict(body, jumps=[{"host": "-x"}]),
                        dict(body, jumps="hop"), dict(body, key_file=kf, password="p"),
                        dict(body, tunnel=True, key_file=None, password="p")):
                bad = {k: v for k, v in bad.items() if v is not None}
                assert tf.send(px, "POST", "/v1/fleet/pair/ssh", bad)[0] == 400, bad
    finally:
        box.stop()


def test_own_machine_refused():
    """An SSH login to the controller's own machine (its TandemNode installed there) is refused
    before the adopt: nothing is pinned or written in that agent's trust store."""
    n = tnt.Node("ADD-OWN-0001")
    fake = tnt.FakeSsh(n)
    try:
        with Proxy() as px:
            f = tf.fleet_on(px, tf.local_node(tf.fake_root()))
            t = f.trust()
            t.ssh = fake.ssh(t.ssh.known_hosts)
            with open(fake.machine, "w") as fh:
                fh.write("ab12cd34ab12cd34ab12cd34ab12cd34\n")
            t.machine_id = "ab12cd34ab12cd34ab12cd34ab12cd34"
            trust_dir = os.path.join(n.state, "trust")
            before = sorted(os.listdir(trust_dir))
            st, hk = tf.send(px, "POST", "/v1/fleet/pair/ssh/hostkey", {"host": "127.0.0.1"})
            st, err = tf.send(px, "POST", "/v1/fleet/pair/ssh", {
                "host": "127.0.0.1", "user": "spark", "password": tnt.PASSWORD,
                "host_key": hk["fingerprint"]})
            assert st == 409 and ok(err, "error")["error"]["type"] == "conflict", err
            assert "own machine" in err["error"]["message"]
            assert n.agent.store.owner() is None and sorted(os.listdir(trust_dir)) == before
            # another machine id: the same login pairs
            t.machine_id = "00000000000000000000000000000000"
            st, node = tf.send(px, "POST", "/v1/fleet/pair/ssh", {
                "host": "127.0.0.1", "user": "spark", "password": tnt.PASSWORD,
                "host_key": hk["fingerprint"]})
            assert st == 200, node
    finally:
        n.close()


def test_agent_preflight_and_claim():
    n = tnt.Node("ADD-AGENT-0001")
    try:
        c = n.local()
        st, doc = c.json("GET", "/node/preflight")
        assert st == 200 and doc["facts"]["tandemllm"] == VERSION and "kernel" in doc["facts"]
        info = n.agent.mdns_info()
        assert info["txt"]["paired"] == "0" and "owner" not in info["txt"]
        fp = "ab" * 32
        st, got = c.json("PUT", "/node/fleet", {"fingerprint": fp, "name": "TandemApp on desk"})
        assert st == 200 and got["fingerprint"] == fp
        info = n.agent.mdns_info()
        assert info["txt"]["paired"] == "1" and info["txt"]["owner"] == fp[:16]
        assert c.json("PUT", "/node/fleet", {"fingerprint": "nothex"})[0] == 400
        # another controller's discovery lists it as owned, this one's as its own
        sight = discovery.mdns_sighting({"type": "_tandemnode._tcp.local", "instance": "x",
                                         "host": "x.local", "port": n.port, "txt": info["txt"],
                                         "addresses": ["192.0.2.40"], "src": "192.0.2.40"}, 1)
        sight["at"] = time.time()
        other = discovery.build_row([sight], "cd" * 32, [])
        assert other["kind"] == "owned" and other["title"] == "Spark owned by another TandemApp"
        mine = discovery.build_row([sight], fp, [])
        assert mine["node"]["owner"] == "this" and mine["kind"] != "owned", mine
        # only the loopback secret claims (the owner's certificate may not)
        assert os.stat(os.path.join(n.state, "fleet.json")).st_mode & 0o077 == 0
        # the controller makes the claim when it reads this machine through its agent
        with Proxy() as px:
            f = tf.fleet_on(px, tf.local_node(tf.fake_root(serial="ADD-AGENT-0001")))
            calls = []

            class Agent:
                class client:
                    @staticmethod
                    def json(method, path, body=None, timeout=None):
                        calls.append((method, path, body))
                        return 200, {}
            f._claim(Agent)
            assert calls[0][:2] == ("PUT", "/node/fleet") and \
                calls[0][2]["fingerprint"] == f.trust().fingerprint()
    finally:
        n.close()
    # mDNS answers on no container or VM bridge
    addrs = {"enP7s7": ["192.0.2.40/24"], "docker0": ["172.17.0.1/16"],
             "br-1a2b": ["172.18.0.1/16"], "veth9": ["169.254.3.1/16"],
             "virbr0": ["192.0.2.201/24"], "fab0": ["10.200.0.1/30"]}
    assert nodeagent.lan_addrs(addrs) == ["192.0.2.40/24", "10.200.0.1/30"]


def test_tunnel_process():
    clock = tf.Clock(100.0)
    procs = []

    class Proc:
        def __init__(self, argv):
            self.argv, self.returncode, self.pid = argv, None, 1000 + len(procs)
            self.stderr = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def wait(self, timeout=None):
            return self.returncode
    up = {"ok": True}
    t = sshtunnel.Tunnels("/x/known_hosts", spawn=lambda a: (procs.append(Proc(a)), procs[-1])[1],
                          pick=lambda: 45000 + len(procs), probe=lambda p: up["ok"],
                          clock=clock, sleep=lambda s: setattr(clock, "t", clock.t + s),
                          log=lambda m: None)
    route = {"host": RENTED, "port": 2201, "user": "spark", "identity_file": "/k/rent",
             "jumps": [{"user": "hop", "host": "198.51.100.1", "port": 2222}], "remote_port": 7700}
    p1 = t.ensure(route)
    a = procs[0].argv
    assert a[:6] == ["ssh", "-N", "-T", "-p", "2201", "-l"] and a[-1] == RENTED
    assert a[a.index("-J") + 1] == "hop@198.51.100.1:2222" and a[a.index("-i") + 1] == "/k/rent"
    assert a[a.index("-L") + 1] == f"127.0.0.1:{p1}:127.0.0.1:7700"
    assert "StrictHostKeyChecking=yes" in a and "UserKnownHostsFile=/x/known_hosts" in a
    assert t.view(route)["state"] == "up" and t.ensure(route) == p1 and len(procs) == 1
    # the process exits: started again after its back-off, on the same local port
    procs[0].returncode = 255
    assert t.ensure(route) == p1 and len(procs) == 1 and t.view(route)["restarts"] == 1
    clock.t += 1.5
    assert t.ensure(route) == p1 and len(procs) == 2
    procs[1].returncode = 255
    t.ensure(route)
    assert t.view(route)["restarts"] == 2
    clock.t += 1.5
    t.ensure(route)
    assert len(procs) == 2                    # 2 s now
    clock.t += 1
    t.ensure(route)
    assert len(procs) == 3
    t.close(route)
    assert procs[2].returncode == -15 and t.view(route)["state"] == "down"


class Login:
    """A box's SSH login as a local shell: HOME a temporary folder, the command and its input as
    ssh hands them over (no host key to confirm, no secret)."""

    def __init__(self, home: str):
        self.home, self.ran = home, []

    def host_key(self, host, port, jumps=None):
        return {"known": "known", "fingerprint": "SHA256:box", "type": "ssh-ed25519",
                "stored": "SHA256:box"}, ""

    def run(self, host, port, user, command, stdin, **kw):
        self.ran.append(command)
        r = subprocess.run(["sh", "-c", command], input=stdin, capture_output=True, timeout=60,
                           env={"HOME": self.home, "PATH": os.environ["PATH"]})
        return r.returncode, r.stdout.decode(), r.stderr.decode()


def test_the_engine_step_takes_the_installer_and_ref():
    d = tempfile.mkdtemp(prefix="add-engine-")
    stub = os.path.join(d, "engine", "install.sh")
    os.makedirs(os.path.dirname(stub))
    with open(stub, "w") as f:
        f.write('#!/bin/bash\nprintf "%s\\n" "$@" > "$HOME/install.argv"\necho "[stub] runtime"\n')
    with open(os.path.join(d, "engine", "VERSION"), "w") as f:
        f.write("0.16.0\n")
    home = os.path.join(d, "home")
    os.makedirs(home)
    seen = os.path.join(home, "install.argv")

    def job(env):
        login = Login(home)
        pkg = nodeinstall.Package(os.path.join(d, "pkg"), env=env)
        return nodeinstall.Install(types.SimpleNamespace(ssh=login), {"host": BOX, "port": 22,
                                                                       "user": "spark"}, {},
                                   package=pkg, version=pkg.version), login

    def engine_step(j):
        return next(x for x in j.view()["steps"] if x["id"] == "engine")

    real = _version.release
    _version.release = lambda: "1.0.0"          # TandemApp 1.0.0 beside the engine 0.16.0
    try:
        j, login = job({"TANDEM_ENGINE_INSTALL_SH": stub})
        assert j.version == "1.0.0" and engine_step(j)["label"] == \
            "Install the engine's runtime v0.16.0", engine_step(j)
        assert j._engine() == "the engine's runtime v0.16.0 in ~/.local/share/tandemllm/engine"
        prefix = os.path.join(home, ".local", "share", "tandemllm")
        argv = open(seen).read().splitlines()
        assert argv == ["--dir", os.path.join(prefix, "engine"), "--ref", "v0.16.0",
                        "--runtime-only", "--yes"], argv
        # the installer went over as the login's input: the box runs the stub's own bytes
        assert open(os.path.join(prefix, "install.sh")).read() == open(stub).read()
        assert stub not in login.ran[0] and "--ref v0.16.0" in login.ran[0]
        # TANDEM_ENGINE_REF wins over the installer's VERSION
        j, _ = job({"TANDEM_ENGINE_INSTALL_SH": stub, "TANDEM_ENGINE_REF": "v0.16.1-rc1"})
        j._engine()
        argv = open(seen).read().splitlines()
        assert argv[argv.index("--ref") + 1] == "v0.16.1-rc1", argv
        # no installer: the step fails, names the variable, and logs in nowhere
        j, login = job({})
        try:
            j._engine()
            raise AssertionError("an engine step ran without an installer")
        except nodeinstall.InstallError as exc:
            assert exc.kind == "no_installer" and "TANDEM_ENGINE_INSTALL_SH" in exc.message, exc
        st = engine_step(j)
        assert st["state"] == "failed" and "TANDEM_ENGINE_INSTALL_SH" in st["detail"], st
        assert not login.ran
        # a ref that is no tag name is refused before any login: nothing runs on the box
        os.remove(seen)
        for bad in ('v1; touch "$HOME/pwned"', "--upload-pack=x", "v1..2", "v" * 65, "v1 v2"):
            j, login = job({"TANDEM_ENGINE_INSTALL_SH": stub, "TANDEM_ENGINE_REF": bad})
            assert engine_step(j)["label"] == "Install the engine's runtime", bad
            try:
                j._engine()
                raise AssertionError(bad)
            except nodeinstall.InstallError as exc:
                assert exc.kind == "bad_engine_ref" and "TANDEM_ENGINE_REF" in exc.message, \
                    (bad, exc.message)
            assert not login.ran, bad
        assert not os.path.exists(os.path.join(home, "pwned")) and not os.path.exists(seen)
        # the same for a VERSION beside the installer; without one, TandemApp's own release
        with open(os.path.join(d, "engine", "VERSION"), "w") as f:
            f.write("0.16.0 $(id)\n")
        try:
            nodeinstall.Package(d, env={"TANDEM_ENGINE_INSTALL_SH": stub}).engine_ref()
            raise AssertionError("a VERSION that makes no tag was taken")
        except nodeinstall.InstallError as exc:
            assert exc.kind == "bad_engine_ref" and "VERSION" in exc.message, exc.message
        os.remove(os.path.join(d, "engine", "VERSION"))
        assert nodeinstall.Package(d, env={"TANDEM_ENGINE_INSTALL_SH": stub}).engine_ref() == \
            "v1.0.0"
        # an installer path that cannot be read is an error that names it
        gone = os.path.join(d, "gone", "install.sh")
        try:
            nodeinstall.Package(d, env={"TANDEM_ENGINE_INSTALL_SH": gone}).install_sh()
            raise AssertionError("a missing installer was taken")
        except nodeinstall.InstallError as exc:
            assert exc.kind == "no_installer" and gone in exc.message, exc.message
    finally:
        _version.release = real
        shutil.rmtree(d, ignore_errors=True)


def test_preflight_reads_tandemnode_only():
    d = tempfile.mkdtemp(prefix="add-pf-")
    try:
        os.makedirs(os.path.join(d, "TandemLLM", "src"))
        with open(os.path.join(d, "TandemLLM", "src", "VERSION"), "w") as f:
            f.write("0.14.2\n")                   # an engine checkout, as install.sh lays one out
        path = os.pathsep.join(p for p in os.environ["PATH"].split(os.pathsep)
                               if p and not os.path.exists(os.path.join(p, "tandem-node")))
        env = {"HOME": d, "PATH": path, "LANG": "C"}
        r = subprocess.run(["sh", "-c", preflight.SCRIPT], capture_output=True, text=True,
                           timeout=60, env=env)
        f = preflight.parse(r.stdout)
        assert f.get("home") == d and f["tandemllm"] == "", (f, r.stderr[-500:])
        tv = next(x for x in preflight.checks(f, "0.15.0", time.time()) if x["id"] == "tandemllm")
        assert tv["label"] == "TandemNode" and tv["state"] == "ok" and \
            tv["value"] == "not installed · Install puts 0.15.0", tv
        # a TandemNode in the install prefix's venv is what the row reads
        venv = os.path.join(d, "prefix", "venv", "bin")
        os.makedirs(venv)
        with open(os.path.join(venv, "python"), "w") as fh:
            fh.write("#!/bin/sh\necho 0.14.2\n")
        os.chmod(os.path.join(venv, "python"), 0o755)
        r = subprocess.run(["sh", "-c", preflight.SCRIPT], capture_output=True, text=True,
                           timeout=60, env=dict(env, TANDEM_PF_PREFIX=os.path.join(d, "prefix")))
        f = preflight.parse(r.stdout)
        assert f["tandemllm"] == "0.14.2", f
        rows = preflight.checks(f, "0.15.0", time.time(), engine_ref="v0.16.0")
        tv = next(x for x in rows if x["id"] == "tandemllm")
        assert tv["state"] == "fail" and tv["value"] == "0.14.2 · the fleet runs 0.15.0", tv
        assert "update TandemNode on this box to 0.15.0" in tv["fix"] and \
            "install.sh --ref v0.16.0" in tv["fix"] and "Install refuses 0.14.2" in tv["fix"], tv
        assert preflight.verdict(rows, False)["install"] == "refused"
        # without an engine ref the fix names no install.sh ref
        tv = next(x for x in preflight.checks(f, "0.15.0", time.time()) if x["id"] == "tandemllm")
        assert "--ref" not in tv["fix"], tv
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_script_and_runtime_only():
    r = subprocess.run(["sh", "-c", preflight.SCRIPT], capture_output=True, text=True, timeout=60)
    f = preflight.parse(r.stdout)
    assert {"now", "kernel", "arch", "sudo"} <= set(f), f
    rows = preflight.checks(f, VERSION, time.time())
    assert all(x["state"] in preflight.STATES for x in rows)
    d = tempfile.mkdtemp(prefix="add-rt-")
    r = subprocess.run(["bash", os.path.join(ROOT, "install.sh"), "--runtime-only", "--dry-run",
                        "--yes", "--dir", os.path.join(d, "engine")], capture_output=True,
                       text=True, timeout=300, env=dict(os.environ, HOME=d))
    out = r.stdout + r.stderr
    assert r.returncode == 0, out[-2000:]
    assert "[3/3]" in out and "[4/" not in out and "Weights" not in out, out[-2000:]
    assert "No weights, corpus or config yet" in out
    shutil.rmtree(d, ignore_errors=True)


def test_the_node_wheel():
    """The wheel this controller builds: what app/pyproject.toml lists (TandemApp's packages and
    the built page, no engine code), the console scripts, the metadata."""
    out_dir = tempfile.mkdtemp(prefix="add-whl-")
    try:
        p = nodeinstall.build_wheel(out_dir, VERSION)
        assert nodeinstall.build_wheel(out_dir, VERSION) == p      # cached by content
        v = _version.pep440(VERSION)
        assert os.path.basename(p) == f"tandemapp-{v}-py3-none-any.whl", p
        with zipfile.ZipFile(p) as z:
            names = z.namelist()
            assert "Name: tandemapp" in z.read(f"tandemapp-{v}.dist-info/METADATA").decode()
            assert {"proxy/nodeagent.py", "proxy/preflight.py", "chatd/__init__.py",
                    "tandemapp/__init__.py", "tandemapp/static.py", "proxy/dashboard/index.html",
                    "proxy/dashboard/sw.js"} <= set(names)
            assert not [n for n in names if n.split("/")[0] in ("server", "engine")], \
                "the node wheel carries no engine code"
            di = next(n for n in names if n.endswith(".dist-info/entry_points.txt"))
            assert "tandem-node = proxy.nodeagent:main" in z.read(di).decode()
            meta = z.read(di.replace("entry_points.txt", "METADATA")).decode()
            assert "Requires-Dist: cryptography" in meta
            assert not any("__pycache__" in n or n.endswith(".pyc") for n in names)
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


if __name__ == "__main__":
    run(globals())
