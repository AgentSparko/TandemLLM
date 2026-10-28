"""SEC-5: node trust (SEC-4): pairing by code and by SSH once to mTLS with pinned certificates,
ownership, take-over and release, on real TLS between real agents.

The nodes are TandemNode agents in this process on loopback ports, each with its own state dir and
a fake machine (test_node_agent's), so each has its own id; the controllers are tandem-app's fleet
over their own config databases. SSH runs a fake `ssh` and `ssh-keyscan` (no network): the fake
checks the host key file it is given, takes the password only through SSH_ASKPASS, and runs the
remote command with a fake home whose user unit points at a real agent.

  1  pair by code: both sides pin each other, the node is up through mTLS, loopback turns to mTLS,
     the node's CLI uses its own certificate, the audit on both sides     test_pair_by_code
  2  a wrong code, five of them, an expired code, the window closes; the controller's rate limit
                                                                          test_wrong_expired_spent
  3  someone in between with another certificate (relaying, or swapping the controller's): refused,
     nothing pinned, the code still good for the real controller         test_mitm_refused
  4  after pairing: a stranger's certificate, no certificate, plain HTTP with the secret: refused
                                                                          test_unpinned_refused
  5  release from the controller: both sides forget; a node that does not answer only with force
                                                                          test_release_both_sides
  6  a second controller: refused while owned (no window, `owned`); a take-over window on the node
     moves it, the old owner's stream is cut and its calls refused; `tandem node takeover`
                                                                          test_takeover
  7  SSH once on a fake ssh: host key new/same/changed, password through askpass, a key, owned and
     take-over, not installed, a wrong password; the password and key in no log, file or answer
                                                                          test_ssh_once
  8  the guard: pairing and release need the admin password or this machine; /v1/fleet/session
                                                                          test_guard_and_session
  9  the LAN bind: refused without the trust store, mTLS on it after pairing there
                                                                          test_lan_bind
 10  the one-peer migration: a peer backend with a host collector is an unpaired node, and pairing
     at its address keeps its alias and colour                           test_one_peer_migration
 11  the protocol alone: SPAKE2's group, a code's tries and expiry on a clock, the CLI's reset
                                                                          test_protocol_and_reset

    python tests/test_node_trust.py
"""

from __future__ import annotations

import contextlib
import glob
import io
import json
import os
import socket
import ssl
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_fleet as tf  # noqa: E402
import test_node_agent as tna  # noqa: E402
from ports import free_port  # noqa: E402
from proxy import config as config_mod  # noqa: E402
from proxy import fleet as fleet_mod  # noqa: E402
from proxy import nodeagent, nodeclient, nodesource, nodetrust, pairing, sshonce  # noqa: E402
from proxy_util import Proxy, run  # noqa: E402

ADMIN = "trust-admin-token-0123456789abcdef0123"
PASSWORD = "Tr0ub4dor-fixture-pw-8812"
KEY = ("-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAAFIXTUREfixturekeymaterial0001\n"
       "AAAAC3NzaC1lZDI1NTE5AAAAIFIXTUREfixturekeymaterial0002\n-----END OPENSSH PRIVATE KEY-----")


def _hostkey(seed: bytes) -> str:
    """An ed25519 public key line as ssh-keyscan prints it (the key bytes a fixture)."""
    import base64
    import struct
    t = b"ssh-ed25519"
    blob = struct.pack(">I", len(t)) + t + struct.pack(">I", 32) + seed.ljust(32, b"0")
    return "ssh-ed25519 " + base64.b64encode(blob).decode()


HOSTKEY = _hostkey(b"tandemnode-test-host-key")
OTHER_HOSTKEY = _hostkey(b"reinstalled-host-key")


# ---------------------------------------------------------------------------- nodes

class Node:
    """A TandemNode agent on a loopback port with its own state dir and machine."""

    def __init__(self, serial: str, hosts=("127.0.0.1",), clock=None, state=None):
        self.buf: list[str] = []
        self.agent = tna.agent_on(tna.machine(serial=serial), buf=self.buf, clock=clock,
                                  state=state)
        self.port = two_ports()
        nodeagent.listen(self.agent, list(hosts), self.port)
        for s in self.agent.servers:
            threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.05},
                             daemon=True).start()
        self.state = self.agent.state_dir

    def args(self):
        return nodeagent.parser().parse_args(["--state-dir", self.state, "--port",
                                              str(self.port)])

    def local(self) -> nodeclient.Client:
        c = nodeagent.local_client(self.args())
        assert c is not None, "the agent does not answer its own CLI"
        return c

    def window(self, takeover=False, address="127.0.0.1") -> dict:
        """A pairing window on the agent's port + 1, as by default."""
        st, doc = self.local().json("POST", "/node/pair", {"address": address,
                                                           "takeover": takeover})
        assert st == 200, (st, doc)
        return doc

    def close(self):
        self.agent.sampler.close()
        self.agent.close_window()
        for s in list(self.agent.servers):
            s.shutdown()
            s.server_close()


def two_ports() -> int:
    """A free port whose next one is free too (the agent's and its pairing window's)."""
    while True:
        p = free_port()
        if free_port() == p + 1:
            return p


def second_fleet(px, d=None) -> fleet_mod.Fleet:
    """Another controller in this process: its own config database (and so its own
    certificate), the same fake machine as the first's."""
    d = d or tempfile.mkdtemp(prefix="trust-ctl-")
    cfg = config_mod.ConfigDB(os.path.join(d, "config.sqlite3"), env={}, use_keychain=False).open()
    f = fleet_mod.Fleet(px.svc, tf.local_node(tf.fake_root()), log=lambda m: None)
    f.cfg = cfg
    return f


def pair(px, w: dict, code=None, address="127.0.0.1"):
    return tf.send(px, "POST", "/v1/fleet/pair", {"address": address,
                                                  "code": code or w["code"], "port": w["port"]})


def wait(cond, s=5.0):
    end = time.monotonic() + s
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def tls_refused(port, ctx) -> bool:
    try:
        st, _, _ = tna.raw(port, "GET", "/node/identity", tls=ctx)
    except (ssl.SSLError, ConnectionResetError, BrokenPipeError, OSError):
        return True
    return st in (401, 403)


def lan_ip() -> str | None:
    """This machine's address on its default route, when it is a private one."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))
        ip = s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()
    p = nodetrust.parse_ip(ip)
    return ip if p.is_private and not p.is_loopback else None


# ---------------------------------------------------------------------------- the tests

def test_pair_by_code():
    n = Node("TRUST-A-0001")
    try:
        with Proxy() as px:
            f = tf.fleet_on(px, tf.local_node(tf.fake_root()))
            w = n.window()
            assert w["state"] == "open" and len(w["code"]) == 6 and w["tries_left"] == 5
            assert w["fingerprint"] == n.agent.store.own_fingerprint()
            st, node = pair(px, w)
            assert st == 200, node
            tf.ok(node, "node")
            p = node["pairing"]
            assert p["state"] == "paired" and p["via"] == "code"
            assert p["fingerprint"] == n.agent.store.own_fingerprint()
            # both sides pin each other
            ctl = f.trust()
            owner = n.agent.store.owner()
            assert owner["fingerprint"] == ctl.fingerprint() and owner["via"] == "code"
            row = f.cfg.node(node["id"])
            assert row["trust"]["pin"].strip() == n.agent.store.own_pem().strip()
            assert row["endpoint"] == f"https://127.0.0.1:{n.port}"
            # the node is read through its mTLS; it shows up with its own id
            assert node["id"] == n.agent.node_id() and node["status"] == "up", node
            nodes = tf.ok(tf.get(px, "/v1/fleet/nodes")[1], "nodes")["nodes"]
            assert {x["pairing"]["state"] for x in nodes} == {"local", "paired"}
            tf.ok(tf.get(px, "/v1/fleet/topology")[1], "topology")
            # loopback is mTLS now: the old secret gets nothing, the node's CLI its own cert
            assert n.agent.listening["127.0.0.1"]["mode"] == "mtls"
            try:
                st, _, _ = tna.raw(n.port, "GET", "/node/identity",
                                   {"Authorization": f"Bearer {n.agent.secret}"})
            except (OSError, ValueError):
                st = None
            assert st is None
            c = n.local()
            assert c.scheme == "https"
            st, doc = c.json("GET", "/node/pair")
            assert st == 200 and doc["window"]["state"] == "paired" and "code" in doc["window"]
            # the code was single use: the window is gone, the node says it is ours already
            st, err = pair(px, w)
            assert st == 409 and tf.ok(err, "error")["error"]["type"] == "conflict", err
            assert "already" in err["error"]["message"]
            # the audit, both sides; no code in any log
            trust = tf.ok(tf.get(px, "/v1/fleet/trust")[1], "trust")
            assert trust["controller"]["fingerprint"] == ctl.fingerprint()
            assert trust["audit"][0]["action"] == "pair" and trust["audit"][0]["node"] == node["id"]
            assert [r["action"] for r in n.agent.store.audit_rows()] == ["pair"]
            assert not any(w["code"] in m for m in n.buf + px.logs)
            # the controller's key file is private
            assert os.stat(ctl.key).st_mode & 0o077 == 0
    finally:
        n.close()


def test_wrong_expired_spent():
    n = Node("TRUST-B-0001")
    try:
        with Proxy() as px:
            tf.fleet_on(px, tf.local_node(tf.fake_root()))
            w = n.window()
            wrong = "%06d" % ((int(w["code"]) + 1) % 10 ** 6)
            for left in (4, 3, 2, 1):
                st, err = pair(px, w, wrong)
                assert st == 403 and tf.ok(err, "error")["error"]["type"] == "wrong_code", err
                st, doc = n.local().json("GET", "/node/pair")
                assert doc["window"]["tries_left"] == left
            st, err = pair(px, w, wrong)
            assert st == 403
            # five wrong codes: spent, the window closes, the right code is too late
            assert wait(lambda: n.local().json("GET", "/node/pair")[1]["window"]["state"]
                        == "spent")
            assert wait(lambda: n.agent.pair_server is None)
            st, err = pair(px, w)
            assert st in (410, 502) and tf.ok(err, "error")["error"]["type"] in (
                "code_spent", "unreachable"), err
            assert n.agent.store.owner() is None
            assert any(r["action"] == "window_spent" for r in n.agent.store.audit_rows())
            # the controller's own limit: ten attempts a minute
            f2 = second_fleet(px)
            seen = []
            for _ in range(11):
                seen.append(f2.handle("POST", "/v1/fleet/pair", {}, {
                    "address": "127.0.0.1", "code": "123456", "port": free_port()})[0])
            assert seen[:10] == [502] * 10 and seen[10] == 429, seen
            tf.ok(f2.handle("POST", "/v1/fleet/pair", {}, {"address": "x", "code": "1"})[1],
                  "error")
        # an expired code, on the node's clock
        t = [1_900_000_000.0]
        e = Node("TRUST-B-0002", clock=lambda: t[0])
        try:
            w = e.window()
            t[0] += pairing.TTL_S + 1
            try:
                pairing.pair("127.0.0.1", w["port"], w["code"], tf_ctl_pem())
                raise AssertionError("an expired code paired")
            except pairing.PairError as exc:
                assert exc.kind in ("code_spent", "unreachable") and exc.status in (410, 502)
            assert wait(lambda: e.agent.window.view()["state"] == "expired")
            assert e.agent.store.owner() is None
        finally:
            e.close()
    finally:
        n.close()


def tf_ctl_pem() -> str:
    d = tempfile.mkdtemp(prefix="trust-pem-")
    return nodetrust.make_identity(os.path.join(d, "c.crt"), os.path.join(d, "c.key"), "ctl")


class Mitm:
    """A TLS listener with its own certificate between the controller and the node's window:
    it relays each request (or swaps the controller's certificate for its own) and the answer."""

    def __init__(self, target_port: int, swap: bool):
        d = tempfile.mkdtemp(prefix="trust-mitm-")
        self.pem = nodetrust.make_identity(os.path.join(d, "m.crt"), os.path.join(d, "m.key"),
                                           "mitm")
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(os.path.join(d, "m.crt"), os.path.join(d, "m.key"))
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if swap and self.path == "/node/pair/1":
                    doc = json.loads(body)
                    doc["certificate"] = outer.pem
                    body = json.dumps(doc).encode()
                up = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                up.check_hostname, up.verify_mode = False, ssl.CERT_NONE
                import http.client
                c = http.client.HTTPSConnection("127.0.0.1", target_port, context=up, timeout=5)
                c.request("POST", self.path, body=body,
                          headers={"Content-Type": "application/json"})
                r = c.getresponse()
                data = r.read()
                self.send_response(r.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        from http.server import ThreadingHTTPServer
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.socket = ctx.wrap_socket(self.srv.socket, server_side=True)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def test_mitm_refused():
    n = Node("TRUST-C-0001")
    try:
        with Proxy() as px:
            f = tf.fleet_on(px, tf.local_node(tf.fake_root()))
            w = n.window()
            for swap in (False, True):
                m = Mitm(w["port"], swap)
                try:
                    st, err = pair(px, dict(w, port=m.port))
                finally:
                    m.close()
                assert st == 403 and tf.ok(err, "error")["error"]["type"] == "wrong_code", err
                assert n.agent.store.owner() is None
                assert [x["pairing"]["state"] for x in
                        tf.get(px, "/v1/fleet/nodes")[1]["nodes"]] == ["local"]
            # two tries spent; the real controller still pairs with the same code
            st, doc = n.local().json("GET", "/node/pair")
            assert doc["window"]["tries_left"] == 3 and doc["window"]["state"] == "open"
            st, node = pair(px, w)
            assert st == 200 and n.agent.store.owner()["fingerprint"] == f.trust().fingerprint()
    finally:
        n.close()


def test_unpinned_refused():
    n = Node("TRUST-D-0001")
    try:
        with Proxy() as px:
            f = tf.fleet_on(px, tf.local_node(tf.fake_root()))
            st, node = pair(px, n.window())
            assert st == 200
            assert wait(lambda: n.agent.listening["127.0.0.1"]["mode"] == "mtls")
            pin = n.agent.store.own_pem()
            d = tempfile.mkdtemp(prefix="trust-x-")
            nodetrust.make_identity(os.path.join(d, "s.crt"), os.path.join(d, "s.key"), "x")
            stranger = nodetrust.client_context(os.path.join(d, "s.crt"),
                                                os.path.join(d, "s.key"), pin)
            assert tls_refused(n.port, stranger)
            assert tls_refused(n.port, nodetrust.client_context(None, None, pin))
            ok = nodetrust.client_context(f.trust().cert, f.trust().key, pin)
            st, _, body = tna.raw(n.port, "GET", "/node/identity", tls=ok)
            assert st == 200 and json.loads(body)["trust"]["owner"]["fingerprint"] == \
                f.trust().fingerprint()
            assert any("refused TLS" in m for m in n.buf)
            # a pinned peer node reaches its identity and the link test only
            st, _, _ = tna.raw(n.port, "PUT", "/node/peers/n-peer1", tls=ok,
                               body=json.dumps({"certificate": open(
                                   os.path.join(d, "s.crt")).read()}))
            assert st == 200
            assert wait(lambda: not tls_refused(n.port, stranger))
            st, _, _ = tna.raw(n.port, "GET", "/node/engines", tls=stranger)
            assert st == 403
            st, _, _ = tna.raw(n.port, "DELETE", "/node/pair", tls=stranger)
            assert st == 403 and n.agent.store.owner() is not None
            st, _, _ = tna.raw(n.port, "DELETE", "/node/peers/n-peer1", tls=ok)
            assert st == 200 and wait(lambda: tls_refused(n.port, stranger))
    finally:
        n.close()


def test_release_both_sides():
    n = Node("TRUST-E-0001")
    m = Node("TRUST-E-0002")
    try:
        with Proxy() as px:
            f = tf.fleet_on(px, tf.local_node(tf.fake_root()))
            st, node = pair(px, n.window())
            st2, gone = pair(px, m.window())
            assert st == 200 and st2 == 200
            st, rel = tf.send(px, "DELETE", f"/v1/fleet/nodes/{node['id']}", None)
            assert st == 200 and tf.ok(rel, "release")["node"] == "unpinned", rel
            assert n.agent.store.owner() is None and f.cfg.node(node["id"]) is None
            assert wait(lambda: n.agent.listening["127.0.0.1"]["mode"] == "secret")
            assert n.local().scheme == "http"
            assert [r["action"] for r in n.agent.store.audit_rows()] == ["pair", "release"]
            assert tf.get(px, "/v1/fleet/trust")[1]["audit"][0]["action"] == "release"
            assert tf.get(px, f"/v1/fleet/nodes/{node['id']}")[0] == 404
            # a node that does not answer: refused, then forgotten here with force
            m.close()
            st, err = tf.send(px, "DELETE", f"/v1/fleet/nodes/{gone['id']}", None)
            assert st == 502 and tf.ok(err, "error")["error"]["type"] == "unreachable"
            st, rel = tf.send(px, "DELETE", f"/v1/fleet/nodes/{gone['id']}?force=1", None)
            assert st == 200 and rel["node"] == "unreachable"
            assert tf.get(px, "/v1/fleet/trust")[1]["audit"][0]["action"] == "forget"
            # the controller's own machine has nothing to release
            local = next(x for x in tf.get(px, "/v1/fleet/nodes")[1]["nodes"])
            st, err = tf.send(px, "DELETE", f"/v1/fleet/nodes/{local['id']}", None)
            assert st == 409 and err["error"]["type"] == "conflict"
    finally:
        n.close()


def test_takeover():
    n = Node("TRUST-F-0001")
    try:
        with Proxy() as px:
            fa = tf.fleet_on(px, tf.local_node(tf.fake_root()))
            st, node = pair(px, n.window())
            assert st == 200
            fb = second_fleet(px)
            assert fb.trust().fingerprint() != fa.trust().fingerprint()
            # owned: the node opens no window without take-over, B is told so
            st, err = n.local().json("POST", "/node/pair", {"address": "127.0.0.1"})
            assert st == 409 and err["error"]["type"] == "owned"
            st, err = fb.handle("POST", "/v1/fleet/pair", {}, {
                "address": "127.0.0.1", "code": "123456", "port": n.port + 1})
            assert st == 409 and tf.ok(err, "error")["error"]["type"] == "owned", err
            assert "another TandemApp" in err["error"]["message"]
            # A keeps a metrics stream open
            pin = n.agent.store.own_pem()
            a_ctx = nodetrust.client_context(fa.trust().cert, fa.trust().key, pin)
            a_client = nodeclient.Client(f"https://127.0.0.1:{n.port}", ssl_context=a_ctx)
            ended = threading.Event()

            def stream():
                try:
                    for _ in a_client.events("/node/metrics", timeout=30):
                        pass
                except (OSError, ssl.SSLError):
                    pass
                ended.set()
            threading.Thread(target=stream, daemon=True).start()
            time.sleep(0.5)
            assert not ended.is_set()
            # the take-over is confirmed on the node: `tandem node takeover`
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                try:
                    nodeagent.main(["takeover", "--json", "--state-dir", n.state, "--port",
                                    str(n.port), "--address", "127.0.0.1"])
                except SystemExit as exc:
                    assert exc.code == 0, exc
            w = json.loads(out.getvalue())
            assert w["takeover"] is True
            st, got = fb.handle("POST", "/v1/fleet/pair", {}, {
                "address": "127.0.0.1", "code": w["code"], "port": w["port"]})
            assert st == 200, got
            tf.ok(got, "node")
            owner = n.agent.store.owner()
            assert owner["fingerprint"] == fb.trust().fingerprint()
            # A's pin is gone, its stream cut, its calls refused, its status says why
            assert wait(ended.is_set)
            assert tls_refused(n.port, a_ctx)
            src = nodesource.agent_for(fa.cfg.node(node["id"]))
            try:
                status, text = src.status()
                assert status == "off" and "refuses this controller's certificate" in text, text
            finally:
                src.close()
            rows = n.agent.store.audit_rows()
            assert rows[-1]["action"] == "takeover" and \
                rows[-1]["from"] == fa.trust().fingerprint()
            assert fb.trust().view()["audit"][0]["action"] == "takeover"
            assert any("pin of" in m and "removed" in m for m in n.buf)
            # A's release now reaches a node that no longer knows it: forgotten here
            st, rel = tf.send(px, "DELETE", f"/v1/fleet/nodes/{node['id']}", None)
            assert st == 200 and rel["node"] == "refused", rel
            assert n.agent.store.owner()["fingerprint"] == fb.trust().fingerprint()
    finally:
        n.close()


# ---------------------------------------------------------------------------- SSH once

FAKE_SSH = r'''#!@PY@
import json, os, subprocess, sys
st = json.load(open(os.environ["FAKE_SSH_STATE"]))
args = sys.argv[1:]
with open(st["log"], "a") as f:
    f.write(json.dumps({"argv": args, "env_secret": "TANDEM_SSH_SECRET" in os.environ}) + "\n")
opts, i, port, user, ident = {}, 0, "22", None, None
while i < len(args):
    a = args[i]
    if a == "-o":
        k, _, v = args[i + 1].partition("="); opts[k] = v; i += 2; continue
    if a in ("-p", "-l", "-i"):
        port, user, ident = (args[i + 1], user, ident) if a == "-p" else \
            (port, args[i + 1], ident) if a == "-l" else (port, user, args[i + 1])
        i += 2; continue
    if a == "-T":
        i += 1; continue
    break
host, command = args[i], args[i + 1]
name = host if port == "22" else "[%s]:%s" % (host, port)
kh = open(opts["UserKnownHostsFile"]).read().split()
if kh[0] != name or " ".join(kh[1:3]) != st["hostkey"]:
    sys.stderr.write("Host key verification failed.\n"); sys.exit(255)
if opts.get("PubkeyAuthentication") == "no":
    got = subprocess.run([os.environ["SSH_ASKPASS"]], capture_output=True, text=True).stdout
    ok = got.rstrip("\n") == st["password"]
elif ident:
    ok = open(ident).read().strip() == st["key"].strip()
else:
    ok = st.get("agent", False)
if not ok:
    sys.stderr.write("%s@%s: Permission denied (publickey,password).\n" % (user, host))
    sys.exit(255)
env = {k: v for k, v in os.environ.items()
       if k not in ("TANDEM_SSH_SECRET", "SSH_ASKPASS", "SSH_ASKPASS_REQUIRE")}
env.update(HOME=st["home"], PATH=st["path"], PYTHONPATH=st["root"],
           SSH_CONNECTION="127.0.0.1 50000 127.0.0.1 " + port)
env.update(st.get("env") or {})
sys.exit(subprocess.run(["sh", "-c", command], env=env, cwd=st["home"]).returncode)
'''

FAKE_KEYSCAN = r'''#!@PY@
import json, os, sys
st = json.load(open(os.environ["FAKE_SSH_STATE"]))
port, host = sys.argv[sys.argv.index("-p") + 1], sys.argv[-1]
print(("%s" if port == "22" else "[%s]:" + port) % host, st["hostkey"])
'''


class FakeSsh:
    def __init__(self, node: Node):
        self.dir = tempfile.mkdtemp(prefix="trust-ssh-")
        self.bin = os.path.join(self.dir, "bin")
        self.home = os.path.join(self.dir, "home")
        os.makedirs(self.bin)
        os.makedirs(os.path.join(self.home, ".config", "systemd", "user"))
        for name, text in (("ssh", FAKE_SSH), ("ssh-keyscan", FAKE_KEYSCAN)):
            p = os.path.join(self.bin, name)
            with open(p, "w") as f:
                f.write(text.replace("@PY@", sys.executable))
            os.chmod(p, 0o755)
        self.state = os.path.join(self.dir, "state.json")
        self.log = os.path.join(self.dir, "argv.log")
        self.st = {"hostkey": HOSTKEY, "password": PASSWORD, "key": KEY, "home": self.home,
                   "path": "/usr/bin:/bin", "root": ROOT, "log": self.log}
        self.unit(node)
        os.environ["FAKE_SSH_STATE"] = self.state

    def save(self):
        with open(self.state, "w") as f:
            json.dump(self.st, f)

    def unit(self, node: Node | None):
        p = os.path.join(self.home, ".config", "systemd", "user", "tandemnode.service")
        if node is None:
            if os.path.exists(p):
                os.remove(p)
            return
        with open(p, "w") as f:
            f.write(f"[Service]\nExecStart={sys.executable} -m proxy.nodeagent serve --host "
                    f"127.0.0.1 --port {node.port} --state-dir {node.state}\n")

    def ssh(self, known_hosts: str) -> sshonce.Ssh:
        self.save()
        return sshonce.Ssh(known_hosts, ssh=os.path.join(self.bin, "ssh"),
                           keyscan=os.path.join(self.bin, "ssh-keyscan"))


def _all_text(paths) -> str:
    out = []
    for root in paths:
        for p in glob.glob(os.path.join(root, "**", "*"), recursive=True):
            if os.path.isfile(p):
                try:
                    with open(p, "rb") as f:
                        out.append(f.read().decode("utf-8", "replace"))
                except OSError:
                    pass
    return "\n".join(out)


def test_ssh_once():
    n = Node("TRUST-G-0001")
    fake = FakeSsh(n)
    tmp_before = set(glob.glob(os.path.join(tempfile.gettempdir(), "tandem-ssh-*")))
    answers = []
    try:
        with Proxy() as px:
            f = tf.fleet_on(px, tf.local_node(tf.fake_root()))
            t = f.trust()
            t.ssh = fake.ssh(t.ssh.known_hosts)
            # the host key, before the first login: new
            st, hk = tf.send(px, "POST", "/v1/fleet/pair/ssh/hostkey", {"host": "127.0.0.1"})
            assert st == 200 and tf.ok(hk, "hostkey")["known"] == "new", hk
            assert hk["fingerprint"] == sshonce.fingerprint(HOSTKEY.split()[1])
            body = {"host": "127.0.0.1", "user": "spark", "password": "wrong-" + PASSWORD}
            st, err = tf.send(px, "POST", "/v1/fleet/pair/ssh", body)
            answers.append(err)
            assert st == 502 and tf.ok(err, "error")["error"]["type"] == "ssh_failed", err
            assert "Permission denied" in err["error"]["message"]
            assert n.agent.store.owner() is None
            # a confirmed fingerprint that is not the host's: refused before any login
            st, err = tf.send(px, "POST", "/v1/fleet/pair/ssh", dict(
                body, password=PASSWORD, host_key="SHA256:somethingelse"))
            answers.append(err)
            assert st == 409 and err["error"]["type"] == "host_key_changed"
            # the password once, through askpass: paired
            st, node = tf.send(px, "POST", "/v1/fleet/pair/ssh", dict(
                body, password=PASSWORD, host_key=hk["fingerprint"]))
            answers.append(node)
            assert st == 200, node
            tf.ok(node, "node")
            assert node["pairing"]["via"] == "ssh" and node["pairing"]["host_key"] == \
                hk["fingerprint"]
            assert node["id"] == n.agent.node_id() and node["status"] == "up", node
            owner = n.agent.store.owner()
            assert owner["fingerprint"] == t.fingerprint() and owner["via"] == "ssh"
            assert n.agent.store.audit_rows()[-1]["via"] == "ssh"
            st, hk2 = tf.send(px, "POST", "/v1/fleet/pair/ssh/hostkey", {"host": "127.0.0.1"})
            assert hk2["known"] == "same"
            # another controller over SSH: owned, unless it takes over (SSH proves access)
            fb = second_fleet(px)
            fb.trust().ssh = fake.ssh(fb.trust().ssh.known_hosts)
            kb = dict(body, password=None, key=KEY)
            kb.pop("password")
            st, err = fb.handle("POST", "/v1/fleet/pair/ssh", {}, dict(kb))
            answers.append(err)
            assert st == 409 and err["error"]["type"] == "owned", err
            st, got = fb.handle("POST", "/v1/fleet/pair/ssh", {}, dict(kb, takeover=True))
            answers.append(got)
            assert st == 200, got
            assert n.agent.store.owner()["fingerprint"] == fb.trust().fingerprint()
            assert n.agent.store.audit_rows()[-1]["action"] == "takeover"
            # the host key changes: refused, never replaced
            fake.st["hostkey"] = OTHER_HOSTKEY
            fake.save()
            st, hk3 = tf.send(px, "POST", "/v1/fleet/pair/ssh/hostkey", {"host": "127.0.0.1"})
            assert hk3["known"] == "changed" and hk3["stored"] == hk["fingerprint"]
            st, err = tf.send(px, "POST", "/v1/fleet/pair/ssh", dict(body, password=PASSWORD,
                                                                     takeover=True))
            answers.append(err)
            assert st == 409 and err["error"]["type"] == "host_key_changed", err
            assert n.agent.store.owner()["fingerprint"] == fb.trust().fingerprint()
            # nothing installed there
            fake.st["hostkey"] = HOSTKEY
            fake.unit(None)
            fake.save()
            st, err = fb.handle("POST", "/v1/fleet/pair/ssh", {}, dict(kb, takeover=True))
            answers.append(err)
            assert st == 409 and err["error"]["type"] == "not_installed", err
            # bad input never reaches ssh
            for bad in ({"host": "-oProxyCommand=x", "user": "a"}, {"host": "h", "user": "a b"},
                        {"host": "h", "user": "a", "password": "p", "key": "k"},
                        {"host": "h", "user": "a", "shell": "x"}):
                st, err = tf.send(px, "POST", "/v1/fleet/pair/ssh", bad)
                assert st == 400 and tf.ok(err, "error"), bad
            # the password and the key: in no answer, log, file, argv or left-over directory
            assert len(open(fake.log).readlines()) == 5         # the logins ssh made
            haystack = "\n".join([json.dumps(answers), "\n".join(px.logs), "\n".join(n.buf),
                                  open(fake.log).read(),
                                  _all_text([px.dir, n.state, os.path.dirname(fb.cfg.path)])])
            for secret in [PASSWORD] + [ln for ln in KEY.splitlines() if "fixture" in ln]:
                assert secret not in haystack, secret[:12]
            assert set(glob.glob(os.path.join(tempfile.gettempdir(), "tandem-ssh-*"))) <= \
                tmp_before
            assert os.stat(t.ssh.known_hosts).st_mode & 0o077 == 0
    finally:
        n.close()


def test_guard_and_session():
    lan = {"X-Real-IP": "192.0.2.77"}
    body = {"address": "x"}
    with Proxy() as px:                                   # no password: this machine only
        tf.fleet_on(px, tf.local_node(tf.fake_root()))
        st, err = tf.send(px, "POST", "/v1/fleet/pair", body, lan)
        assert st == 401 and "TANDEM_ADMIN_TOKEN" in tf.ok(err, "error")["error"]["message"]
        for method, path in (("POST", "/v1/fleet/pair/ssh"), ("POST", "/v1/fleet/pair/ssh/hostkey"),
                             ("DELETE", "/v1/fleet/nodes/n-x")):
            assert tf.send(px, method, path, {}, lan)[0] == 401, path
        assert tf.send(px, "POST", "/v1/fleet/pair", body)[0] == 400       # passes the guard
        st, s = tf.get(px, "/v1/fleet/session", lan)
        assert st == 200 and tf.ok(s, "session") == {
            "contract_version": "1.0", "password_set": False, "authenticated": True,
            "expires_at": None}
        assert tf.send(px, "POST", "/v1/fleet/session", {"token": "x"}, lan)[0] == 404
        assert tf.get(px, "/v1/fleet/trust", lan)[0] == 200
    with Proxy(env={"TANDEM_ADMIN_TOKEN": ADMIN}, login="off") as px:
        tf.fleet_on(px, tf.local_node(tf.fake_root()))
        assert tf.get(px, "/v1/fleet/nodes", lan)[0] == 200                 # reads stay open
        assert tf.send(px, "POST", "/v1/fleet/pair", body, lan)[0] == 401
        assert tf.send(px, "POST", "/v1/fleet/pair", body,
                       {**lan, "Authorization": f"Bearer {ADMIN}"})[0] == 400
        st, s = tf.get(px, "/v1/fleet/session", lan)
        assert s["password_set"] is True and s["authenticated"] is False
        assert tf.send(px, "POST", "/v1/fleet/session", {"token": "wrong"}, lan)[0] == 401
        st, hdrs, _ = px.request("POST", "/v1/fleet/session", {"token": ADMIN}, headers=lan)
        assert st == 204
        cookie = dict(hdrs)["Set-Cookie"].split(";")[0]
        signed = {**lan, "Cookie": cookie}
        st, s = tf.get(px, "/v1/fleet/session", signed)
        assert tf.ok(s, "session")["authenticated"] is True and s["expires_at"]
        assert tf.send(px, "POST", "/v1/fleet/pair", body, signed)[0] == 400
        st, _, _ = px.request("DELETE", "/v1/fleet/session", None, headers=signed)
        assert st == 204 and tf.send(px, "POST", "/v1/fleet/pair", body, signed)[0] == 401
        assert ADMIN not in "\n".join(px.logs)


def test_lan_bind():
    ip = lan_ip()
    empty = Node("TRUST-H-0001")
    try:
        if ip is None:
            print("    (no private LAN address on this machine: the refusal only)")
        a = empty.agent
        try:
            nodeagent.listen(a, ["127.0.0.1", ip or "192.168.1.20"], free_port())
            raise AssertionError("listened beyond loopback without the trust store")
        except nodetrust.BindRefused as exc:
            assert "needs mTLS" in str(exc)
        if ip is None:
            return
        with Proxy() as px:
            f = tf.fleet_on(px, tf.local_node(tf.fake_root()))
            st, node = pair(px, empty.window(address=ip), address=ip)
            assert st == 200, node
            assert wait(lambda: a.listening.get(ip, {}).get("mode") == "mtls"), a.listening
            assert a.store.listen_addrs() == [ip]
            assert node["address"] == ip
            pin = a.store.own_pem()
            ok = nodetrust.client_context(f.trust().cert, f.trust().key, pin)
            c = nodeclient.Client(f"https://{ip}:{empty.port}", ssl_context=ok)
            assert c.json("GET", "/node/identity")[0] == 200
            stranger = nodetrust.client_context(None, None, pin)
            try:
                nodeclient.Client(f"https://{ip}:{empty.port}", ssl_context=stranger).json(
                    "GET", "/node/identity")
                raise AssertionError("an unpaired client got an answer on the LAN")
            except (ssl.SSLError, OSError):
                pass
            import http.client
            try:
                hc = http.client.HTTPConnection(ip, empty.port, timeout=3)
                hc.request("GET", "/node/identity")
                st = hc.getresponse().status
            except (OSError, http.client.HTTPException):
                st = None
            assert st is None, "plain HTTP answered on the LAN"
            # released: the LAN address closes
            tf.send(px, "DELETE", f"/v1/fleet/nodes/{node['id']}", None)
            assert wait(lambda: ip not in a.listening)
    finally:
        empty.close()


def test_one_peer_migration():
    env = {"TANDEM_BACKENDS": "peer", "TANDEM_BACKEND_PEER_KIND": "tandemllm",
           "TANDEM_BACKEND_PEER_URL": "http://127.0.0.1:1/v1", "TANDEM_BACKEND_PEER_LABEL": "Gold",
           "TANDEM_BACKEND_PEER_HOST": "collector",
           "TANDEM_BACKEND_PEER_HOST_URL": "http://127.0.0.1:2"}
    n = Node("TRUST-I-0001")
    try:
        with Proxy(env=env) as px:
            f = tf.fleet_on(px, tf.local_node(tf.fake_root()))
            f.locality.is_local = lambda url: False          # the peer is another machine
            nodes = tf.ok(tf.get(px, "/v1/fleet/nodes")[1], "nodes")["nodes"]
            peer = next(x for x in nodes if x["alias"] == "Gold")
            assert peer["status"] == "off" and "not paired" in peer["status_text"]
            assert peer["pairing"]["state"] == "unpaired" and peer["address"] == "127.0.0.1"
            assert len(tf.get(px, "/v1/fleet/nodes")[1]["nodes"]) == 2      # once
            tf.send(px, "PATCH", f"/v1/fleet/nodes/{peer['id']}", {"colour": 5})
            st, node = pair(px, n.window())
            assert st == 200 and node["id"] == n.agent.node_id() != peer["id"]
            assert node["alias"] == "Gold" and node["colour"] == 5
            assert tf.get(px, f"/v1/fleet/nodes/{peer['id']}")[0] == 404
            assert len(tf.get(px, "/v1/fleet/nodes")[1]["nodes"]) == 2
    finally:
        n.close()


def test_protocol_and_reset():
    assert pow(pairing.G, pairing.Q, pairing.P) == 1
    for e in (pairing.M, pairing.N):
        assert 1 < e < pairing.P - 1 and pow(e, pairing.Q, pairing.P) == 1
    for bad in ("1", format(pairing.P - 1, "x"), "zz", "0" * 600):
        try:
            pairing._elem(bad)
            raise AssertionError(bad)
        except pairing.PairError:
            pass
    codes = {pairing.new_code() for _ in range(200)}
    assert all(len(c) == 6 and c.isdigit() for c in codes) and len(codes) > 150
    # tries and expiry on an injected clock
    t = [100.0]
    pem = tf_ctl_pem()
    w = pairing.Window(pem, code="482913", clock=lambda: t[0])
    assert w.view()["state"] == "open"
    t[0] += pairing.TTL_S
    assert w.view()["state"] == "expired"
    # `tandem node reset`: owner and pins gone, the agent back on the secret; --keys a new key
    n = Node("TRUST-J-0001")
    try:
        with Proxy() as px:
            tf.fleet_on(px, tf.local_node(tf.fake_root()))
            assert pair(px, n.window())[0] == 200
            assert wait(lambda: n.agent.listening["127.0.0.1"]["mode"] == "mtls")
            fp = n.agent.store.own_fingerprint()
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                try:
                    nodeagent.main(["reset", "--keys", "--state-dir", n.state, "--port",
                                    str(n.port)])
                except SystemExit as exc:
                    assert exc.code == 0
            assert "forgotten" in out.getvalue()
            assert n.agent.store.owner() is None and n.agent.store.peers() == []
            assert n.agent.store.own_fingerprint() != fp
            assert wait(lambda: n.agent.listening["127.0.0.1"]["mode"] == "secret")
            assert n.agent.store.audit_rows()[-1]["action"] == "reset"
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                try:
                    nodeagent.main(["status", "--state-dir", n.state, "--port", str(n.port)])
                except SystemExit:
                    pass
            assert "owner:      none" in out.getvalue()
    finally:
        n.close()


if __name__ == "__main__":
    run(globals())
