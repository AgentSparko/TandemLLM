"""OPS-235: moving TandemApp's controller (OPS-234, proxy/ctlmove.py): the export and its import,
the sealed export of a pull, a move to a Spark on fakes and the move back.

The nodes are TandemNode agents in this process on loopback ports, paired by code; the target is
a virtual node whose controller start (POST /node/controller) writes a real user unit that a fake
systemctl runs as a real `python -m proxy serve` process: the new controller is another process,
as on a Spark. The controllers here are tandem-app in this process (the Proxy helper).

  1  the export: backends, routes, users, rules, nodes and layouts; no secrets table, no sessions,
     no jobs, no moved_to; stored keys decrypted for the import; the import lands in an empty
     controller only, its nodes' trust naming its own certificate, the keys under its own master
     key                                                                   test_export_import
  2  sealing: only the holder of the certificate's key opens it               test_seal
  3  the plan: the target's words, every node's how, the password the new controller needs, a
     rented or unpaired target refused, the confirm single use, the sleep warning on a Mac
                                                                           test_plan_and_refusals
     the routes that move a fleet or update it need the admin password from another machine
  4  Gherkin "Move controller to a Spark": a controller with 3 nodes moves to Gold (virtual):
     the new controller runs with the fleet imported, every node trusts it and refuses the old
     one, the API URL is Gold's, the old controller says moved_to (its page follows); every
     answer validates; before it, a controller that never comes up fails the move at its start
     with nothing handed over and nothing left on the Spark. Then Gherkin's way back: a fresh controller takes the fleet from Gold
     with its password, the nodes trust it and refuse Gold, Gold says moved_to it
                                                                           test_move_and_back

    python tests/test_controller_move.py
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_fleet as tf  # noqa: E402
import test_node_agent as tna  # noqa: E402
import test_node_trust as tnt  # noqa: E402
from ports import free_port  # noqa: E402
from proxy import appservice, ctlmove, nodetrust  # noqa: E402
from proxy import config as config_mod  # noqa: E402
from proxy_util import Proxy, run  # noqa: E402

ADMIN = "move-admin-token-0123456789abcdef0123456"
KEY = "sk-fixture-ABCDEFGH1234"


def _until(fn, s=30.0, step=0.1):
    end = time.time() + s
    while time.time() < end:
        if fn():
            return True
        time.sleep(step)
    return False


# ---------------------------------------------------------------------------- 1, 2

def _cfg(d: str) -> config_mod.ConfigDB:
    return config_mod.ConfigDB(os.path.join(d, "config.sqlite3"), env={},
                               use_keychain=False).open()


def test_export_import():
    a = _cfg(tempfile.mkdtemp(prefix="mv-a-"))
    a.create_backend({"id": "ds", "kind": "deepseek", "label": "DeepSeek", "key": KEY})
    a.create_backend({"id": "eng", "kind": "tandemllm", "url": "http://192.0.2.10:8000/v1"})
    a.set_rules([{"pattern": "qwen*", "backend": "eng"}])
    a.add_node({"id": "n-gold", "alias": "Gold", "endpoint": "https://192.0.2.10:7700",
                "address": "192.0.2.10", "trust": {"kind": "mtls", "cert": "/a/ctl.crt",
                                                   "key": "/a/ctl.key", "pin": "PEM\n"}})
    a.add_node({"id": "n-here", "alias": "Here", "endpoint": "local", "trust": {"kind": "local"}})
    a.set_layout({"page": "overview", "device": "desk", "saved_at": 1.0, "rail": {}})
    a.set_setting("theme", "dark")
    a.set_setting("controller.moved_to", {"api_url": "x"})
    a.put_add_session({"id": "a-1", "state": "active"})
    a.put_job({"id": "u-1", "kind": "update", "state": "done"})
    b = ctlmove.export(a)
    assert b["kind"] == "tandemapp-export" and b["keys"] == {"ds": KEY}
    assert KEY not in json.dumps({k: v for k, v in b.items() if k != "keys"})
    d = tempfile.mkdtemp(prefix="mv-b-")
    c = _cfg(d)
    got = ctlmove.import_bundle(c, b, "/b/ctl.crt", "/b/ctl.key")
    assert got == {"nodes": 1, "backends": 2, "keys": 1}, got
    assert {x["id"] for x in c.backends()} == {"ds", "eng"}
    assert c.key_for("ds") == KEY                     # stored again under c's own master key
    assert [r["pattern"] for r in c.rules()] == ["qwen*"]
    n = c.nodes()
    assert [x["id"] for x in n] == ["n-gold"], "this machine's node stays with this machine"
    assert n[0]["trust"]["cert"] == "/b/ctl.crt" and n[0]["trust"]["pin"] == "PEM\n"
    assert c.setting("theme") == "dark" and c.setting("controller.moved_to") is None
    assert c.layout("overview", "desk") is not None
    assert c.add_session() is None and c.job("update") is None
    # an import lands only in an empty controller
    try:
        ctlmove.import_bundle(c, b, "/b/ctl.crt", "/b/ctl.key")
        raise AssertionError("imported twice")
    except ctlmove.MoveError as exc:
        assert exc.kind == "not_empty" and exc.status == 409
    # at the start: import.json is read once and removed
    d2 = tempfile.mkdtemp(prefix="mv-c-")
    with open(os.path.join(d2, "import.json"), "w") as f:
        json.dump(b, f)
    c2 = _cfg(d2)
    assert ctlmove.import_at_start(c2, d2, log=lambda m: None)["nodes"] == 1
    assert not os.path.exists(os.path.join(d2, "import.json"))
    assert os.path.isfile(os.path.join(d2, "controller", "controller.crt"))
    assert c2.nodes()[0]["trust"]["cert"] == os.path.join(d2, "controller", "controller.crt")
    assert ctlmove.import_at_start(c2, d2) is None


def test_seal():
    d = tempfile.mkdtemp(prefix="mv-seal-")
    cert, key = os.path.join(d, "c.crt"), os.path.join(d, "c.key")
    nodetrust.make_identity(cert, key, "TandemApp test")
    doc = {"kind": "tandemapp-export", "keys": {"ds": KEY}}
    s = ctlmove.seal(doc, open(cert).read())
    assert KEY not in json.dumps(s) and s["alg"].startswith("ECDH-P256")
    assert ctlmove.unseal(s, key) == doc
    other = os.path.join(d, "o.crt"), os.path.join(d, "o.key")
    nodetrust.make_identity(*other, "another")
    try:
        ctlmove.unseal(s, other[1])
        raise AssertionError("opened with another key")
    except ctlmove.MoveError as exc:
        assert exc.kind == "bad_request"


# ---------------------------------------------------------------------------- the fakes

class VNode(tnt.Node):
    """test_node_trust's node, as a virtual node when `virtual` is given."""

    def __init__(self, serial, virtual=None):
        self.buf: list[str] = []
        self.agent = tna.agent_on(tna.machine(serial=serial), buf=self.buf, virtual=virtual,
                                  address="127.0.0.1")
        self.port = tnt.two_ports()
        from proxy import nodeagent
        nodeagent.listen(self.agent, ["127.0.0.1"], self.port)
        for s in self.agent.servers:
            threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.05},
                             daemon=True).start()
        self.state = self.agent.state_dir


class FakeSystemd:
    """systemctl --user for the units the agent writes: restart runs the unit's ExecStart as a
    real process (its WorkingDirectory and Environment), disable --now ends it."""

    def __init__(self, home: str):
        self.home = home
        self.broken = False                      # restart starts nothing: a unit that never answers
        self.procs: dict[str, subprocess.Popen] = {}
        self.logs: dict[str, str] = {}

    def __call__(self, argv, timeout=30.0):
        if argv[0] != "systemctl":
            return 0, "", ""
        verb = argv[2]
        if verb == "restart":
            unit = argv[3]
            self._stop(unit)
            if self.broken:
                return 0, "", ""
            text = open(os.path.join(self.home, ".config", "systemd", "user", unit)).read()
            cmd = shlex.split(next(x for x in text.splitlines()
                                   if x.startswith("ExecStart="))[len("ExecStart="):])
            cwd = next(x for x in text.splitlines() if x.startswith("WorkingDirectory="))[17:]
            env = {k: v for k, v in os.environ.items() if not k.startswith("TANDEM_")}
            for x in text.splitlines():
                if x.startswith("Environment="):
                    k, _, v = shlex.split(x[len("Environment="):])[0].partition("=")
                    env[k] = v
            env["QSE_TEST"] = "1"
            log = os.path.join(self.home, unit + ".log")
            self.logs[unit] = log
            self.procs[unit] = subprocess.Popen(cmd, cwd=cwd, env=env,
                                                stdout=open(log, "w"), stderr=subprocess.STDOUT,
                                                start_new_session=True)
            return 0, "", ""
        if verb == "disable":
            self._stop(argv[4])
            return 0, "", ""
        if verb == "show":
            p = self.procs.get(argv[3])
            up = p is not None and p.poll() is None
            return 0, (f"LoadState={'loaded' if p else 'not-found'}\nActiveState="
                       f"{'active' if up else 'inactive'}\nMainPID={p.pid if up else 0}\n"), ""
        return 0, "", ""

    def _stop(self, unit):
        p = self.procs.pop(unit, None)
        if p is not None and p.poll() is None:
            os.killpg(p.pid, signal.SIGTERM)
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)

    def close(self):
        for u in list(self.procs):
            self._stop(u)


# ---------------------------------------------------------------------------- 3. the plan

def test_plan_and_refusals():
    n1, n2 = VNode("MV-P-0001"), VNode("MV-P-0002", virtual="gold")
    try:
        with Proxy() as px:
            f = tf.fleet_on(px, tf.local_node(tf.fake_root(), h=tf.host(gpu=False)))
            f.may_sleep = True
            for n in (n1, n2):
                st, node = tnt.pair(px, n.window())
                assert st == 200, node
            gold = n2.agent.node_id()
            v = tf.ok(tf.get(px, "/v1/fleet/controller")[1], "controller")
            assert v["host"]["kind"] == "mac" and v["host"]["node"] is None
            assert v["sleep_warning"] and "sleep" in v["sleep_warning"] and v["moved_to"] is None
            assert {t["node"] for t in v["targets"]} == {n1.agent.node_id(), gold}
            assert all(t["allowed"] for t in v["targets"]), v["targets"]
            assert next(t for t in v["targets"] if t["node"] == gold)["virtual"] is True
            p = tf.ok(tf.get(px, f"/v1/fleet/controller/move?to={gold}&port=8299")[1],
                      "move-plan")
            assert p["allowed"] and p["confirm"] and p["api_url"] == "http://127.0.0.1:8299"
            assert p["needs_password"] is True, "no admin password here: the move asks"
            how = {x["id"]: x["how"] for x in p["nodes"]}
            assert how == {n1.agent.node_id(): "repin", gold: "repin"}, how
            assert any("API moves to http://127.0.0.1:8299/v1" in x for x in p["impact"]), p
            assert any("give it an admin password" in x for x in p["impact"])
            # 428 without a confirm, 409 password_needed, 409 a spent confirm
            st, err = tf.send(px, "POST", "/v1/fleet/controller/move", {"to": gold,
                                                                        "port": 8299})
            assert st == 428 and tf.ok(err, "error")["error"]["type"] == "confirm_required"
            st, err = tf.send(px, "POST", "/v1/fleet/controller/move",
                              {"to": gold, "port": 8299, "confirm": p["confirm"]})
            assert st == 409 and tf.ok(err, "error")["error"]["type"] == "password_needed", err
            st, err = tf.send(px, "POST", "/v1/fleet/controller/move",
                              {"to": gold, "port": 8299, "confirm": p["confirm"],
                               "password": ADMIN})
            assert st == 409 and err["error"]["type"] == "confirm_invalid", err
            # a rented node is no controller host; nor a node that is not paired
            px.svc.cfg.update_node(n1.agent.node_id(), {"lease_end_ms": int(time.time() * 1e3)
                                                        + 86_400_000})
            p = tf.ok(tf.get(px, f"/v1/fleet/controller/move?to={n1.agent.node_id()}")[1],
                      "move-plan")
            assert not p["allowed"] and "rented" in p["blocked"] and p["confirm"] is None
            px.svc.cfg.add_node({"id": "n-unpaired", "alias": "Bare", "endpoint": "",
                                 "address": "192.0.2.77", "trust": {}})
            p = tf.get(px, "/v1/fleet/controller/move?to=n-unpaired")[1]
            assert not p["allowed"] and "not paired" in p["blocked"], p
            assert tf.get(px, "/v1/fleet/controller/move?to=nope")[0] == 404
            # a move, a handover, a take and the update are pairing's rights: from another
            # machine without the admin password, refused
            far = {"X-Real-IP": "192.0.2.5"}
            for path in ("/v1/fleet/controller/move", "/v1/fleet/controller/handover",
                         "/v1/fleet/controller/take", "/v1/fleet/update"):
                st, err = tf.send(px, "POST", path, {}, headers=far)
                assert st == 401, (path, st, err)
            assert tf.get(px, "/v1/fleet/controller", headers=far)[0] == 200
            assert tf.get(px, f"/v1/fleet/controller/move?to={gold}&port=80")[0] == 400
            for src in [s for _, s in f.agents.values()]:
                src.close()
    finally:
        n1.close()
        n2.close()


# ---------------------------------------------------------------------------- 4. the move

def test_move_and_back():
    nodes = [VNode("MV-M-0001"), VNode("MV-M-0002"), VNode("MV-M-0003", virtual="gold")]
    home = tempfile.mkdtemp(prefix="mv-home-")
    sysd = FakeSystemd(home)
    gold = nodes[2]
    gold.agent.service = lambda name: appservice.Service(name, platform="linux", home=home,
                                                         run=sysd, wait_s=3.0 if sysd.broken
                                                         else appservice.HEALTH_WAIT_S)
    port = free_port()
    state = os.path.join(home, "gold-ctl")
    ids = [n.agent.node_id() for n in nodes]
    try:
        with Proxy(env={"TANDEM_ADMIN_TOKEN": ADMIN}) as px:
            f = tf.fleet_on(px, tf.local_node(tf.fake_root(), h=tf.host(gpu=False)))
            px.svc.cfg.create_backend({"id": "ds", "kind": "deepseek", "label": "DeepSeek",
                                       "key": KEY})
            for n in nodes:
                st, node = tnt.pair(px, n.window())
                assert st == 200 and node["status"] == "up", node
            old = f.trust()

            def ended():
                v = tf.get(px, "/v1/fleet/controller")[1]
                return v["job"] and v["job"]["state"] != "running" and v
            # a controller that never comes up: the move stops at its start, nothing is handed
            # over, and nothing of it stays on the Spark, so the move can run again
            sysd.broken = True
            p = tf.get(px, f"/v1/fleet/controller/move?to={ids[2]}&port={port}")[1]
            st, job = tf.send(px, "POST", "/v1/fleet/controller/move",
                              {"to": ids[2], "port": port, "confirm": p["confirm"],
                               "name": "tandemapp-mvtest", "state_dir": state})
            assert st == 200
            assert _until(ended, 60)
            j = ended()["job"]
            assert j["state"] == "failed" and j["error"]["type"] == "not_answering", j
            assert [s["state"] for s in j["steps"]][:3] == ["done", "done", "failed"], j["steps"]
            assert not os.path.exists(state), "the failed controller's state dir stays"
            assert not os.path.exists(os.path.join(home, ".config", "systemd", "user",
                                                   "tandemapp-mvtest.service"))
            assert all(n.agent.store.owner()["fingerprint"] == old.fingerprint() for n in nodes)
            assert ended()["moved_to"] is None
            sysd.broken = False
            p = tf.ok(tf.get(px, f"/v1/fleet/controller/move?to={ids[2]}&port={port}")[1],
                      "move-plan")
            assert p["allowed"] and p["needs_password"] is False, p
            st, job = tf.send(px, "POST", "/v1/fleet/controller/move",
                              {"to": ids[2], "port": port, "confirm": p["confirm"],
                               "name": "tandemapp-mvtest", "state_dir": state})
            assert st == 200 and tf.ok(job, "move-job")["state"] == "running", job
            assert _until(lambda: ended() and ended()["job"]["id"] == job["id"], 90), tf.get(px, "/v1/fleet/controller")[1]["job"]
            v = tf.ok(ended(), "controller")
            job = tf.ok(v["job"], "move-job")
            assert job["state"] == "done", (job, open(sysd.logs.get("tandemapp-mvtest.service",
                                                                   os.devnull)).read()[-3000:])
            url = f"http://127.0.0.1:{port}"
            assert v["moved_to"]["api_url"] == url and job["result"]["api_url"] == url
            assert [s["state"] for s in job["steps"]] == ["done"] * 6, job["steps"]
            assert all(x.get("state") == "handed" and x.get("accepted") for x in job["nodes"]), \
                job["nodes"]
            assert job["old_refused"] is True
            # the old controller reads no node any more; its page has where it went
            assert tf.get(px, "/v1/fleet/nodes")[1]["nodes"] == []
            texts = [e["text"] for e in tf.get(px, "/v1/fleet/events?type=controller")[1]["events"]]
            assert "The controller moved to TandemApp on Gold" in texts[0] or \
                texts[0].startswith("The controller moved to"), texts
            # the new controller: the fleet imported, every node up through its certificate
            st, got = ctlmove.call(url, "GET", "/v1/fleet/nodes", token=ADMIN)
            assert st == 200 and sorted(n["id"] for n in tf.ok(got, "nodes")["nodes"]) == \
                sorted(ids), got
            assert all(n["status"] == "up" for n in got["nodes"]), got
            st, b = ctlmove.call(url, "GET", "/v1/fleet/controller", token=ADMIN)
            assert st == 200 and tf.ok(b, "controller")["host"]["node"] is None
            new_fp = nodetrust.pem_fingerprint(
                open(os.path.join(state, "controller", "controller.crt")).read())
            assert not os.path.exists(os.path.join(state, "import.json"))
            assert oct(os.stat(os.path.join(state, "app.env")).st_mode & 0o777) == "0o600"
            for n in nodes:
                assert n.agent.store.owner()["fingerprint"] == new_fp
                assert n.agent.store.owner()["via"] == "handover"
                ctx = nodetrust.client_context(old.cert, old.key, n.agent.store.own_pem())
                assert tnt.tls_refused(n.port, ctx), "the old controller is still accepted"
            # the API moved: the new controller has the backends and the stored key
            st, bk = ctlmove.call(url, "GET", "/v1/config/backends/ds", token=ADMIN)
            assert st == 200 and bk["key"]["key_set"] is True, bk
            for src in [s for _, s in f.agents.values()]:
                src.close()
        # back to a controller on this machine (This Mac): it takes the fleet with the password
        with Proxy() as px2:
            f2 = tf.fleet_on(px2, tf.local_node(tf.fake_root(), h=tf.host(gpu=False)))
            def taken():
                j = tf.get(px2, "/v1/fleet/controller")[1]["job"]
                return j and j["state"] != "running" and j
            st, job = tf.send(px2, "POST", "/v1/fleet/controller/take",
                              {"url": url, "password": ADMIN})
            assert st == 200 and tf.ok(job, "move-job")["direction"] == "pull"
            assert _until(lambda: taken() and taken()["id"] == job["id"], 60)
            j = tf.ok(taken(), "move-job")
            assert j["state"] == "done", j
            assert all(x.get("accepted") for x in j["nodes"] if x["how"] == "repin"), j["nodes"]
            nodes2 = tf.ok(tf.get(px2, "/v1/fleet/nodes")[1], "nodes")["nodes"]
            assert sorted(n["id"] for n in nodes2) == sorted(ids)
            assert all(n["status"] == "up" for n in nodes2), nodes2
            assert px2.svc.cfg.key_for("ds") == KEY
            st, b = ctlmove.call(url, "GET", "/v1/fleet/controller", token=ADMIN)
            assert b["moved_to"]["api_url"] == f"http://127.0.0.1:{px2.port}", b
            st, gone = ctlmove.call(url, "GET", "/v1/fleet/nodes", token=ADMIN)
            assert gone["nodes"] == [], gone
            mine = f2.trust().fingerprint()
            assert all(n.agent.store.owner()["fingerprint"] == mine for n in nodes)
            # a second take is refused: this controller has a fleet now
            st, err = tf.send(px2, "POST", "/v1/fleet/controller/take",
                              {"url": url, "password": ADMIN})
            assert st == 409 and tf.ok(err, "error")["error"]["type"] == "not_empty"
            for src in [s for _, s in f2.agents.values()]:
                src.close()
    finally:
        sysd.close()
        for n in nodes:
            n.close()


if __name__ == "__main__":
    run(globals())
