"""OPS-235: the fleet update (OPS-234, proxy/fleetupdate.py), the agent's own update
(proxy/nodeupdate.py) and the mixed-version rule (proxy/fleetwatch.py).

The nodes are TandemNode agents in this process on loopback ports, each with an engine the fake
backend plays; a Replicas deployment record over both serves the model id (SRV-731: its members
are the gateway's route, a drained one is never first, a rejoin waits for one token). The agent's pip, its Python and its restart are fakes that install the wheel's version
and come back on it (the real ones run in test 7, on a real venv, where one is there).

  1  the order: idle first, Replicas members one at a time, Single after, a Split's members
     together and last; a Split with a member that cannot be updated refused, a selection that
     takes one member of a Split blocked; off nodes skipped; the current ones named
                                                                            test_order
  2  Gherkin "Rolling update": Replicas on two nodes, the fleet updates: one member drains,
     updates and rejoins at a time, a client's requests all answer 200 meanwhile; every answer
     validates; events; the plan's confirm is single use (428, 409)    test_rolling_update
  3  resume: the controller stops after the first node; a new one resumes the job and ends it
                                                                            test_resume
  4  Split: the plan names one step with both members; ?nodes= with one member blocks, its POST
     is 409 partial_split; a member that cannot be updated refuses the Split
                                                                            test_split
  5  the mixed-version rule: two releases for 60 s fire one alert for the fleet with its
     action; a local build label is its release's; held while an update runs; resolves
                                                                            test_version_mismatch
  6  the agent's update routes: the package's name and sha256 checked, no package 409, outside
     a venv 409 not_managed, the job's steps (the engine simulated on a virtual node), the
     restart, update.json settled by the version the agent runs after it; peers refused
                                                                            test_agent_update
  8  two nodes at one address on another machine (virtual nodes behind a controller elsewhere):
     each serves the engines its own agent lists, so Replicas are two members, not two each
                                                                            test_shared_address
  7  a real agent from a venv updated to a wheel with a local label: pip installs it without the
     package index, the agent re-executes itself and answers on the new version, its process id
     kept (skipped without a venv, cryptography or the index for the first install)
                                                                            test_real_venv_update
  9  the engine's installer and release are the controller's inputs (SRV-747): TandemApp 1.0.0 with
     an installer whose VERSION says 0.16.0 sends ref v0.16.0, and a node with an install.sh install
     runs that installer at v0.16.0; TANDEM_ENGINE_REF overrides the ref, and a node that gets no
     installer skips its engine step and says why          test_the_engine_installer_and_its_ref

    python tests/test_fleet_update.py
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import quote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_backend  # noqa: E402
import test_fleet as tf  # noqa: E402
import test_node_agent as tna  # noqa: E402
from ports import free_port  # noqa: E402
from proxy import _version, fleetstore, fleetupdate, fleetwatch  # noqa: E402
from proxy import nodeinstall, nodeupdate  # noqa: E402
from proxy_util import Proxy, run  # noqa: E402

TARGET = "0.15.0+test"
FROM = "0.15.0"


def _until(fn, s=30.0, step=0.05):
    end = time.time() + s
    while time.time() < end:
        if fn():
            return True
        time.sleep(step)
    return False


# ---------------------------------------------------------------------------- 1. the order

def _n(i, alias, version=FROM, status="up", updatable=True, why=None):
    return {"id": i, "alias": alias, "version": version, "status": status,
            "updatable": updatable, "why": why}


def test_order():
    nodes = [_n("g", "Gold"), _n("r3", "R-3"), _n("r1", "R-1"), _n("r2", "R-2"),
             _n("i", "Idle"), _n("x", "Off", status="off", updatable=False, why="off"),
             _n("c", "Current", version=TARGET)]
    deps = [{"id": "qwen", "name": "Qwen3.8-27B", "type": "replicas", "members": ["g", "r3"],
             "status": "serving"},
            {"id": "wren", "name": "Wren", "type": "single", "members": ["g"],
             "status": "serving"},
            {"id": "ds", "name": "DeepSeek", "type": "split", "members": ["r1", "r2"],
             "status": "serving"}]
    got = fleetupdate.order(nodes, deps, TARGET)
    assert got["blocked"] is None and got["refused"] == []
    assert [(s["kind"], s["nodes"]) for s in got["steps"]] == [
        ("idle", ["i"]), ("replica", ["r3"]), ("single", ["g"]), ("split", ["r1", "r2"])]
    assert [s["n"] for s in got["steps"]] == [1, 2, 3, 4]
    assert got["steps"][0]["drain"] is False and all(s["drain"] for s in got["steps"][1:])
    assert got["steps"][1]["downtime"] is None
    assert got["steps"][2]["downtime"].startswith("Wren not served while Gold updates")
    assert got["steps"][3]["downtime"] == "DeepSeek is down while its members update together"
    assert got["skipped"] == [{"node": "x", "why": "off"}]
    assert got["current"] == ["Current"]
    # one Replicas member already down: the other is a Single for this update
    nodes2 = [dict(n, status="off", updatable=False, why="off") if n["id"] == "r3" else n
              for n in nodes]
    got = fleetupdate.order(nodes2, deps, TARGET)
    g = next(s for s in got["steps"] if s["nodes"] == ["g"])
    assert g["kind"] == "single" and "Qwen3.8-27B" in g["downtime"]
    # a Split member that cannot be updated: the Split stays, refused with why
    nodes3 = [dict(n, updatable=False, why="its agent is older") if n["id"] == "r2" else n
              for n in nodes]
    got = fleetupdate.order(nodes3, deps, TARGET)
    assert not any("r1" in s["nodes"] or "r2" in s["nodes"] for s in got["steps"])
    assert got["refused"][0]["deployment"] == "ds" and "never updates partly" in \
        got["refused"][0]["why"] and "R-2" in got["refused"][0]["why"]
    # a selection with one Split member: blocked
    got = fleetupdate.order(nodes, deps, TARGET, only=["r1", "g"])
    assert got["blocked"] and "R-2 must be in the update too" in got["blocked"]
    assert not any("r1" in s["nodes"] for s in got["steps"]), "a blocked Split's member has no step"
    got = fleetupdate.order(nodes, deps, TARGET, only=["r1", "r2"])
    assert got["blocked"] is None and [s["nodes"] for s in got["steps"]] == [["r1", "r2"]]
    assert fleetupdate.order(nodes, deps, TARGET, only=["nope"])["blocked"].startswith("no node")
    # every node on the target: nothing to do
    same = [dict(n, version=TARGET) for n in nodes]
    assert fleetupdate.order(same, deps, TARGET)["steps"] == []
    assert fleetupdate.needs("0.15.0-test", "0.15.0.dev0") is False
    assert fleetupdate.needs("0.15.0", "0.15.0+test") is True


# ---------------------------------------------------------------------------- the fakes

class FakeNode:
    """An agent on a loopback port with an engine the fake backend plays, its pip, Python and
    restart faked: an update installs the wheel's version and the agent comes back on it."""

    def __init__(self, serial: str, port: int, virtual=None):
        self.agent = tna.agent_on(tna.machine(serial), engines=[tna.engine_doc("e", port)],
                                  address="127.0.0.1", virtual=virtual)
        self.tandem_dir = None                   # an install.sh install's engine (--tandem-dir)
        self.engine_runs: list[list[str]] = []   # the engine installer's runs, as argv
        self.dir = tempfile.mkdtemp(prefix="upd-node-")
        self.ver = os.path.join(self.dir, "version")
        with open(self.ver, "w") as f:
            f.write(FROM + "\n")
        self.py = os.path.join(self.dir, "python")
        with open(self.py, "w") as f:
            f.write(f"#!/bin/sh\ncat '{self.ver}'\n")
        os.chmod(self.py, 0o755)
        self.running = FROM                      # the code this agent runs: new after a restart
        self.agent.code_release = lambda: self.running
        self.pips: list[list[str]] = []
        self.restarts = 0
        self.agent.updater = self.updater()
        self.run = tna.Running(self.agent)
        self.port = self.run.port

    def updater(self):
        return nodeupdate.Updater(self.agent.state_dir, virtual=self.agent.virtual,
                                  tandem_dir=self.tandem_dir, log=lambda m: None, python=self.py,
                                  prefix=self.dir, base_prefix="/", restart=self.restart,
                                  run=self.pip, sleep=lambda s: None,
                                  release=self.agent.code_release)

    def pip(self, argv):
        if argv[:1] == ["bash"]:                 # the engine's installer, --runtime-only
            self.engine_runs.append(list(argv))
            return 0, "[stub] the engine's runtime"
        self.pips.append(list(argv))
        wheel = argv[-1]
        v = nodeinstall.wheel_version(wheel)
        if self.agent.updater.job and self.agent.updater.job.get("fail"):
            return 1, "ERROR: no"
        with open(self.ver, "w") as f:
            f.write(v + "\n")
        return 0, f"Successfully installed tandemapp-{v}"

    def restart(self):
        """The agent's exec: a new process reads update.json and runs the new version."""
        self.running = open(self.ver).read().strip()
        self.agent.updater = self.updater()
        self.restarts += 1

    def close(self):
        self.run.close()


def _replicas_env(pa: int, pb: int) -> dict:
    return {"TANDEM_BACKENDS": "va,vb",
            "TANDEM_BACKEND_VA_KIND": "generic", "TANDEM_BACKEND_VA_URL": f"http://127.0.0.1:{pa}/v1",
            "TANDEM_BACKEND_VA_LABEL": "A", "TANDEM_BACKEND_VA_MODELS": "fake-27b",
            "TANDEM_BACKEND_VA_MODELS_MODE": "list", "TANDEM_BACKEND_VA_FALLBACK": "vb",
            "TANDEM_BACKEND_VB_KIND": "generic", "TANDEM_BACKEND_VB_URL": f"http://127.0.0.1:{pb}/v1",
            "TANDEM_BACKEND_VB_LABEL": "B", "TANDEM_BACKEND_VB_MODELS": "fake-27b",
            "TANDEM_BACKEND_VB_MODELS_MODE": "list", "TANDEM_BACKEND_VB_FALLBACK": "va",
            "TANDEM_ROUTE_1": "fake-27b=va"}


class Fleet2:
    """A controller (Proxy) with two nodes serving fake-27b as Replicas, the wheel TARGET."""

    def __init__(self):
        self.sa, self.pa = fake_backend.start(models=["fake-27b"])
        self.sb, self.pb = fake_backend.start(models=["fake-27b"])
        self.a = FakeNode("UPD-A-0001", self.pa)
        self.b = FakeNode("UPD-B-0001", self.pb, virtual="vb")
        self.px = Proxy(env=_replicas_env(self.pa, self.pb)).__enter__()
        self.px.sync()
        self.f = tf.fleet_on(self.px, tf.local_node(tf.fake_root(), h=tf.host(gpu=False)))
        cfg = self.px.svc.cfg
        for n, nid, alias in ((self.a, "", "Gold"), (self.b, "", "R-3")):
            cfg.add_node({"id": n.agent.node_id(), "alias": alias,
                          "endpoint": f"http://127.0.0.1:{n.port}", "address": "127.0.0.1",
                          "trust": {"kind": "loopback-secret", "file": n.agent.secret_path}})
        d = tempfile.mkdtemp(prefix="upd-wheel-")
        self.wheel = os.path.join(d, f"tandemapp-{TARGET}-py3-none-any.whl")
        with open(self.wheel, "wb") as f:
            f.write(b"PK fake wheel " * 64)
        self.up = self.f.updater()
        self.up._package = nodeinstall.Package(d, env={"TANDEM_NODE_WHEEL": self.wheel})
        self.up.sleep = lambda s: time.sleep(0.05)
        self.ids = {"a": self.a.agent.node_id(), "b": self.b.agent.node_id()}
        # SRV-731: Replicas are a record, never two engines that serve one id
        st, d = tf.send(self.px, "POST", "/v1/fleet/deployments",
                        {"model": "fake-27b", "type": "replicas",
                         "members": [self.ids["a"], self.ids["b"]]})
        assert st == 200 and d["type"] == "replicas", d

    def close(self):
        for src in [s for _, s in self.f.agents.values()]:
            src.close()
        self.px.__exit__(None, None, None)
        self.a.close()
        self.b.close()
        self.sa.shutdown()
        self.sb.shutdown()


def _client(px, stop: threading.Event, out: dict) -> None:
    while not stop.is_set():
        st, _, _ = px.post("/v1/chat/completions", {"model": "fake-27b", "stream": False,
                                                    "messages": [{"role": "user",
                                                                  "content": "hi"}]})
        out[st] = out.get(st, 0) + 1
        time.sleep(0.01)


def _job_end(px, s=60.0) -> dict:
    got = {}

    def done():
        got["v"] = tf.get(px, "/v1/fleet/update")[1]
        return got["v"]["job"] and got["v"]["job"]["state"] != "running"
    assert _until(done, s), got.get("v")
    return got["v"]


# ---------------------------------------------------------------------------- 2. rolling

def test_rolling_update():
    fx = Fleet2()
    try:
        px = fx.px
        deps = tf.ok(tf.get(px, "/v1/fleet/deployments")[1], "deployments")["deployments"]
        d = next(x for x in deps if x["id"] == "fake-27b")
        assert d["type"] == "replicas" and sorted(d["members"]) == sorted(fx.ids.values()), deps
        st, v = tf.get(px, "/v1/fleet/update")
        assert st == 200 and tf.ok(v, "update")["job"] is None and v["target"] == TARGET
        p = tf.ok(tf.get(px, "/v1/fleet/update/plan")[1], "update-plan")
        assert p["allowed"] and p["target"] == TARGET and p["confirm"], p
        assert [(s["kind"], s["nodes"]) for s in p["steps"]] == [
            ("replica", [fx.ids["a"]]), ("replica", [fx.ids["b"]])], p["steps"]
        assert "fake-27b stays served by the other member" in p["steps"][0]["words"]
        # the confirm: none 428, a made-up one 409; the plan's own works once
        st, err = tf.send(px, "POST", "/v1/fleet/update", {})
        assert st == 428 and tf.ok(err, "error")["error"]["type"] == "confirm_required"
        st, err = tf.send(px, "POST", "/v1/fleet/update", {"confirm": "nope"})
        assert st == 409 and err["error"]["type"] == "confirm_invalid"
        stop, counts = threading.Event(), {}
        t = threading.Thread(target=_client, args=(px, stop, counts), daemon=True)
        t.start()
        time.sleep(0.3)
        seen_drained: list[set] = []

        def watch():
            while not stop.is_set():
                rows = {r["id"]: r for r in px.svc.cfg.nodes()}
                seen_drained.append({i for i, r in rows.items() if r["drain"]})
                time.sleep(0.02)
        w = threading.Thread(target=watch, daemon=True)
        w.start()
        st, res = tf.send(px, "POST", "/v1/fleet/update", {"confirm": p["confirm"]})
        assert st == 200 and tf.ok(res, "update")["job"]["state"] == "running", res
        tf.ok(res["job"], "update-job")
        st, err = tf.send(px, "POST", "/v1/fleet/update", {"confirm": p["confirm"]})
        assert st == 409, err                    # running now, and the confirm is spent
        v = _job_end(px)
        time.sleep(0.3)
        stop.set()
        t.join(5)
        w.join(5)
        job = tf.ok(v["job"], "update-job")
        assert job["state"] == "done", job
        assert all(n["phase"] == "done" and n["to"] == TARGET for n in job["nodes"].values())
        # one member drained at a time, never both; each was drained once
        assert not any(len(x) > 1 for x in seen_drained), "both members drained at once"
        assert any(fx.ids["a"] in x for x in seen_drained) and \
            any(fx.ids["b"] in x for x in seen_drained)
        # the model stayed served: every request answered 200
        assert set(counts) == {200} and counts[200] > 20, counts
        # the agents run the target; no drain is left; the controller sees the version
        assert fx.a.restarts == 1 and fx.b.restarts == 1
        assert fx.a.agent.updater.job["state"] == "done"
        assert fx.b.agent.updater.job["steps"][1]["detail"].startswith("simulated")
        nodes = tf.ok(tf.get(px, "/v1/fleet/nodes")[1], "nodes")["nodes"]
        assert {n["version"] for n in nodes} == {TARGET} and all(n["drain"] is None
                                                                 for n in nodes), nodes
        texts = [e["text"] for e in tf.get(px, "/v1/fleet/events?type=update")[1]["events"]]
        assert texts[0] == f"Fleet updated to {TARGET}" and \
            f"Fleet update to {TARGET} started" in texts, texts
        assert "Gold updated to 0.15.0+test" in texts and "R-3 updated to 0.15.0+test" in texts
        # nothing left to do: the plan says so
        p = tf.get(px, "/v1/fleet/update/plan")[1]
        assert not p["allowed"] and p["blocked"] == f"every node runs {TARGET} already", p
        print(f"    rolling update: {counts[200]} requests, all 200")
    finally:
        fx.close()


# ---------------------------------------------------------------------------- 3. resume

class Crash(BaseException):
    """The controller's process ends here (no handler catches it)."""


def test_resume():
    fx = Fleet2()
    try:
        px = fx.px
        orig = fx.up._rejoin
        first = []

        def rejoin(job, nid, drained):
            orig(job, nid, drained)
            if not first:
                first.append(nid)
                raise Crash()
        fx.up._rejoin = rejoin
        hook = threading.excepthook
        threading.excepthook = lambda a: None if a.exc_type is Crash else hook(a)
        try:
            p = tf.get(px, "/v1/fleet/update/plan")[1]
            assert tf.send(px, "POST", "/v1/fleet/update", {"confirm": p["confirm"]})[0] == 200
            assert _until(lambda: first and not fx.up._running, 30)
        finally:
            threading.excepthook = hook
        job = px.svc.cfg.job("update")
        assert job["state"] == "running" and job["nodes"][first[0]]["phase"] == "done", job
        other = next(i for i in fx.ids.values() if i != first[0])
        assert job["nodes"][other]["phase"] == "todo"
        # a new controller over the same database: the job resumes and ends
        f2 = tf.fleet_on(px, tf.local_node(tf.fake_root(), h=tf.host(gpu=False)))
        up2 = f2.updater()
        up2._package = fx.up._package
        up2.sleep = lambda s: time.sleep(0.05)
        f2.start()
        try:
            v = _job_end(px)
        finally:
            f2.stop()
        assert up2.resumed == job["id"]
        assert v["job"]["state"] == "done" and all(n["phase"] == "done"
                                                   for n in v["job"]["nodes"].values()), v
        assert any("resumes where it was" in x["text"] for x in v["job"]["log"])
        assert fx.a.restarts + fx.b.restarts == 2, "each node updated once"
    finally:
        fx.close()


# ---------------------------------------------------------------------------- 4. Split

def test_split():
    fx = Fleet2()
    try:
        px = fx.px
        a, b = fx.ids["a"], fx.ids["b"]
        real = fx.f.deployments
        fx.f.deployments = lambda: [{"id": "ds", "name": "DeepSeek", "model": "ds",
                                     "pack": "", "type": "split", "members": [a, b],
                                     "api_id": "ds", "ctx": 0, "status": "serving",
                                     "status_text": "serving"}]
        p = tf.ok(tf.get(px, "/v1/fleet/update/plan")[1], "update-plan")
        assert [(s["kind"], sorted(s["nodes"])) for s in p["steps"]] == [("split", sorted([a, b]))]
        p = tf.ok(tf.get(px, f"/v1/fleet/update/plan?nodes={a}")[1], "update-plan")
        assert not p["allowed"] and "R-3 must be in the update too" in p["blocked"], p
        assert p["confirm"] is None
        full = tf.get(px, "/v1/fleet/update/plan")[1]
        st, err = tf.send(px, "POST", "/v1/fleet/update", {"confirm": full["confirm"],
                                                           "nodes": [a]})
        assert st == 409 and tf.ok(err, "error")["error"]["type"] == "partial_split", err
        # a member whose agent does not answer: the Split is refused and stays
        fx.b.close()
        for _, s in fx.f.agents.values():
            s._ident_at = -1e18
        p = tf.ok(tf.get(px, "/v1/fleet/update/plan")[1], "update-plan")
        assert p["steps"] == [] and p["refused"][0]["deployment"] == "ds", p
        assert not p["allowed"] and p["blocked"] == "no node can be updated now"
        fx.f.deployments = real
    finally:
        fx.close()


# ---------------------------------------------------------------------------- 5. the rule

def test_version_mismatch():
    store = fleetstore.Store(":memory:").open()
    w = fleetwatch.Watch(store, clock=lambda: 0.0, log=lambda m: None)

    def mine():
        return [a for a in w.alerts() if a["id"].startswith("version-mismatch")]
    nodes = [{"id": "g", "alias": "Gold", "status": "up", "version": "0.15.0"},
             {"id": "r", "alias": "R-3", "status": "up", "version": "0.15.1"},
             {"id": "o", "alias": "Off", "status": "off", "version": None}]
    rd = {"nodes": nodes, "host": {}, "deployments": None, "target": "0.15.1"}
    w.observe(0.0, rd)
    w.observe(59.0, rd)
    assert mine() == []
    w.observe(61.0, rd)
    al = mine()
    assert [a["id"] for a in al] == ["version-mismatch:all"] and al[0]["node"] == "all"
    assert al[0]["title"] == "Mixed versions: 0.15.0 on Gold · 0.15.1 on R-3", al
    assert "This controller carries 0.15.1" in al[0]["detail"]
    assert al[0]["action"] == {"label": "Update fleet", "route": "#/sparks?update=1"}
    assert al[0]["rule"] == "One release on every node nodes run different releases for 60 s"
    # an update runs: held as it is
    w.observe(70.0, dict(rd, updating=True, nodes=[dict(nodes[0], version="0.15.1"), nodes[1]]))
    assert [a["id"] for a in mine()] == ["version-mismatch:all"]
    # one release again (a local label is its release's): resolved
    same = [dict(nodes[0], version="0.15.1+test"), nodes[1], nodes[2]]
    w.observe(80.0, dict(rd, nodes=same))
    assert mine() == []
    ev = [e["text"] for e in store.events(limit=20)]
    assert "Resolved: Mixed versions: 0.15.0 on Gold · 0.15.1 on R-3" in ev, ev
    # a build of the release is not mixed
    w.observe(100.0, dict(rd, nodes=same))
    w.observe(200.0, dict(rd, nodes=same))
    assert mine() == []
    assert any(r["id"] == "version-mismatch" for r in w.rules())


# ---------------------------------------------------------------------------- 6. the agent

def test_agent_update():
    n = FakeNode("UPD-AG-0001", free_port(), virtual="v9")
    try:
        c = n.run.client
        st, v = c.json("GET", "/node/update")
        assert st == 200 and v["managed"] and v["simulated_engine"] and v["job"] is None, v
        assert v["version"] == FROM
        data = b"wheel bytes " * 100
        sha = hashlib.sha256(data).hexdigest()
        name = f"tandemapp-{TARGET}-py3-none-any.whl"
        st, _ = c.raw("PUT", f"/node/update/package?name={quote(name)}&sha256={'0' * 64}", data)
        assert st == 400
        st, _ = c.raw("PUT", f"/node/update/package?name=../x.whl&sha256={sha}", data)
        assert st == 400
        old = f"tandemllm-{TARGET}-py3-none-any.whl"          # the distribution's old name
        st, raw = c.raw("PUT", f"/node/update/package?name={quote(old)}&sha256={sha}", data)
        assert st == 400 and b"tandemapp-<version>-py3-none-any.whl" in raw, raw
        st, doc = c.json("POST", "/node/update", {"name": name, "sha256": sha, "version": TARGET})
        assert st == 409 and doc["error"]["type"] == "no_package", doc
        st, raw = c.raw("PUT", f"/node/update/package?name={quote(name)}&sha256={sha}", data)
        assert st == 200 and json.loads(raw)["size"] == len(data)
        st, doc = c.json("POST", "/node/update", {"name": name, "sha256": "f" * 64,
                                                  "version": TARGET})
        assert st == 409 and doc["error"]["type"] == "no_package"
        st, doc = c.json("POST", "/node/update", {"name": name, "sha256": sha, "version": TARGET,
                                                  "extra": 1})
        assert st == 400
        st, job = c.json("POST", "/node/update", {"name": name, "sha256": sha, "version": TARGET})
        assert st == 202 and job["state"] == "running" and job["simulated"], job
        assert [s["id"] for s in job["steps"]] == ["install", "engine", "restart"]
        assert _until(lambda: n.restarts == 1, 10)
        st, v = c.json("GET", "/node/update")
        assert v["job"]["state"] == "done" and v["version"] == TARGET, v
        assert [s["state"] for s in v["job"]["steps"]] == ["done", "done", "done"], v
        assert v["job"]["steps"][1]["detail"].startswith("simulated: a virtual node")
        assert n.pips[0][1:5] == ["-m", "pip", "install", "--disable-pip-version-check"]
        assert n.pips[0][-1].endswith(name)
        assert c.json("GET", "/node/identity")[1]["tandemllm"] == TARGET
        # outside a venv: refused with the words to do it
        n.agent.updater = nodeupdate.Updater(n.agent.state_dir, python=n.py, prefix="/same",
                                             base_prefix="/same", log=lambda m: None)
        st, doc = c.json("POST", "/node/update", {"name": name, "sha256": sha, "version": TARGET})
        assert st == 409 and doc["error"]["type"] == "not_managed", doc
        assert "installer" in doc["error"]["message"]
        # a restart that never came back on the version: failed when the agent starts again
        with open(n.agent.updater.path, "w") as f:
            json.dump({"state": "running", "version": "9.9.9", "steps": [
                {"id": "install", "state": "done"}, {"id": "engine", "state": "done"},
                {"id": "restart", "state": "running"}]}, f)
        up = n.updater()
        assert up.job["state"] == "failed" and "it runs 0.15.0+test" in up.job["message"], up.job
    finally:
        n.close()


# ---------------------------------------------------------------------------- 8. one address

def test_shared_address():
    fx = Fleet2()
    try:
        # the nodes' machine is not the controller's: both at one address, as virtual nodes on a
        # Spark are to a controller on a Mac
        fx.f.locality.is_local = lambda url: False
        deps = tf.ok(tf.get(fx.px, "/v1/fleet/deployments")[1], "deployments")["deployments"]
        d = next(x for x in deps if x["id"] == "fake-27b")
        assert d["type"] == "replicas" and sorted(d["members"]) == sorted(fx.ids.values()), deps
        rows = {r["id"]: r for r in fx.px.svc.cfg.nodes()}
        got = {r["id"]: [b["id"] for b in fx.f.engine_backends(
            r["address"], fx.f._virtual_ports(r, fx.f.source(r)))] for r in rows.values()}
        assert got == {fx.ids["a"]: ["va"], fx.ids["b"]: ["vb"]}, got
        # alone at its address, a node takes every backend there, as before
        fx.px.svc.cfg.update_node(fx.ids["b"], {"address": "127.0.0.2"})
        row = fx.px.svc.cfg.node(fx.ids["a"])
        assert fx.f._virtual_ports(row, fx.f.source(row)) is None
    finally:
        fx.close()


# ---------------------------------------------------------------------------- 9. the engine

def _update_to_the_end(px) -> dict:
    p = tf.ok(tf.get(px, "/v1/fleet/update/plan")[1], "update-plan")
    assert p["allowed"], p
    st, res = tf.send(px, "POST", "/v1/fleet/update", {"confirm": p["confirm"]})
    assert st == 200, res
    job = tf.ok(_job_end(px)["job"], "update-job")
    assert job["state"] == "done", job
    return job


def test_the_engine_installer_and_its_ref():
    d = tempfile.mkdtemp(prefix="upd-engine-")
    sh = os.path.join(d, "install.sh")
    with open(sh, "wb") as f:
        f.write(b"#!/bin/bash\necho '[stub] the engine installer'\n")
    with open(os.path.join(d, "VERSION"), "w") as f:
        f.write("0.16.0\n")
    real = _version.release
    _version.release = lambda: "1.0.0"         # TandemApp 1.0.0 beside the engine 0.16.0
    fx = Fleet2()
    try:
        a, b = fx.a, fx.b
        a.tandem_dir = os.path.join(d, "engine")     # node a: an install.sh install
        a.agent.updater = a.updater()
        fx.up._package = nodeinstall.Package(d, env={"TANDEM_NODE_WHEEL": fx.wheel,
                                                     "TANDEM_ENGINE_INSTALL_SH": sh})
        assert fx.up._package.engine_ref() == "v0.16.0"
        _update_to_the_end(fx.px)
        ja, jb = a.agent.updater.job, b.agent.updater.job
        assert ja["ref"] == "v0.16.0" and jb["ref"] == "v0.16.0", (ja["ref"], jb["ref"])
        eng = ja["steps"][1]
        assert eng["label"] == "Engine runtime v0.16.0" and eng["state"] == "done", eng
        assert a.engine_runs == [["bash", os.path.join(a.agent.state_dir, "install.sh"), "--dir",
                                  a.tandem_dir, "--ref", "v0.16.0", "--runtime-only", "--yes"]], \
            a.engine_runs
        with open(os.path.join(a.agent.state_dir, "install.sh"), "rb") as f, open(sh, "rb") as g:
            assert f.read() == g.read(), "the node ran another installer"
        assert jb["steps"][1]["detail"].startswith("simulated"), jb["steps"][1]
        # TANDEM_ENGINE_REF overrides the installer's VERSION; with no installer sent, the node
        # with an install.sh install skips its engine step and says why
        target2 = "0.15.0+test2"
        wheel2 = os.path.join(os.path.dirname(fx.wheel), f"tandemapp-{target2}-py3-none-any.whl")
        with open(wheel2, "wb") as f:
            f.write(b"PK fake wheel two " * 64)
        fx.up._package = nodeinstall.Package(d, env={"TANDEM_NODE_WHEEL": wheel2,
                                                     "TANDEM_ENGINE_REF": "v0.16.1"})
        job = _update_to_the_end(fx.px)
        assert job["target"] == target2
        ja, jb = a.agent.updater.job, b.agent.updater.job
        assert ja["ref"] == "v0.16.1" and jb["ref"] == "v0.16.1", (ja["ref"], jb["ref"])
        assert ja["steps"][1]["state"] == "skip" and \
            ja["steps"][1]["detail"] == nodeupdate.NO_INSTALLER, ja["steps"][1]
        assert "TANDEM_ENGINE_INSTALL_SH" in nodeupdate.NO_INSTALLER
        assert len(a.engine_runs) == 1, a.engine_runs
    finally:
        _version.release = real
        fx.close()
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------- 7. a real venv

def test_real_venv_update():
    import importlib.util
    if importlib.util.find_spec("cryptography") is None:
        print("    skipped: no cryptography here")
        return
    d = tempfile.mkdtemp(prefix="upd-venv-")
    venv = os.path.join(d, "venv")
    r = subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", venv],
                       capture_output=True, text=True, timeout=120)
    py = os.path.join(venv, "bin", "python")
    if r.returncode != 0 or subprocess.run([py, "-m", "pip", "--version"],
                                           capture_output=True).returncode != 0:
        print("    skipped: no venv with pip here")
        shutil.rmtree(d, ignore_errors=True)
        return
    env = {k: v for k, v in os.environ.items() if not k.startswith(("TANDEM_", "PYTHON"))}
    env.update(PIP_DISABLE_PIP_VERSION_CHECK="1")
    old = nodeinstall.build_wheel(os.path.join(d, "w"), FROM)
    new = nodeinstall.build_wheel(os.path.join(d, "w"), TARGET)
    # the first install takes the dependencies from the index, as Add Spark's does
    r = subprocess.run([py, "-m", "pip", "install", old], capture_output=True, text=True,
                       timeout=600, env=env)
    if r.returncode != 0:
        print(f"    skipped: the first install needs the package index: "
              f"{(r.stdout + r.stderr).strip().splitlines()[-1][:120]}")
        shutil.rmtree(d, ignore_errors=True)
        return
    env["PIP_NO_INDEX"] = "1"                  # the update itself fetches nothing
    state = os.path.join(d, "state")
    port = free_port()
    log = open(os.path.join(d, "agent.log"), "w")
    p = subprocess.Popen([os.path.join(venv, "bin", "tandem-node"), "serve", "--port", str(port),
                          "--state-dir", state, "--virtual", "upd", "--mdns", "off"],
                         cwd=d, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        from proxy import nodeclient, nodetrust
        assert _until(lambda: os.path.isfile(os.path.join(state, "secret")), 30)
        c = nodeclient.Client(f"http://127.0.0.1:{port}",
                              secret=nodetrust.load_secret(os.path.join(state, "secret")),
                              timeout=10)

        def ident():
            try:
                return c.json("GET", "/node/identity")[1] or {}
            except OSError:
                return {}
        assert _until(lambda: ident().get("tandemllm") == FROM, 30), ident()
        pid = ident()["agent"]["pid"]
        with open(new, "rb") as f:
            data = f.read()
        sha = hashlib.sha256(data).hexdigest()
        name = os.path.basename(new)
        st, _ = c.raw("PUT", f"/node/update/package?name={quote(name)}&sha256={sha}", data, timeout=60)
        assert st == 200
        st, job = c.json("POST", "/node/update", {"name": name, "sha256": sha, "version": TARGET})
        assert st == 202, job
        assert _until(lambda: ident().get("tandemllm") == TARGET, 120), ident()
        assert ident()["agent"]["pid"] == pid, "exec keeps the process id"
        def update():
            try:
                return c.json("GET", "/node/update")[1] or {}
            except OSError:
                return {}
        assert _until(lambda: (update().get("job") or {}).get("state") == "done", 30), \
            (update(), open(os.path.join(d, "agent.log")).read()[-2000:])
        v = update()
        assert v["version"] == TARGET and [s["state"] for s in v["job"]["steps"]] == \
            ["done", "done", "done"], v
        print(f"    real venv: {FROM} -> {TARGET}, pid {pid} kept")
    finally:
        p.terminate()
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()
        log.close()
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    run(globals())
