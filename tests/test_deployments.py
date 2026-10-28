"""SRV-732: deployments behind one API (SRV-731): the records and the derived Singles, the gateway's
route per deployment with its headers, Replicas affinity, failover and drain, the placement
planner, the Split refusal, the jobs through the agents, the ledger's node and deployment, the
weights copy verified by content id, and SRV-467's replica sets moved into records.

The nodes are TandemNode agents in this process on loopback ports (virtual nodes at one address),
each with an engine the fake backend plays: in this process (an engine TandemNode does not
manage), or a process the agent starts (a managed one, tests/fake_backend.py, which ends with this
test: tests/owned.py). The engine a box serves on :8000 is never addressed.

  1  Gherkin "Two Singles on two nodes": /v1/models lists both once, each request lands on its
     node with X-Tandem-Node and X-Tandem-Deployment; an engine no record covers is a derived
     Single (the box's unmanaged engine); every answer validates          test_two_singles
  2  Gherkin "Ledger filter node=R-3": the rows carry node and deployment; usage and requests
     filtered by node and deployment count only theirs                   test_ledger_filter
  3  Replicas: a conversation stays on its member, keys spread, a member that dies hands its
     conversation to the other before any byte (failover), a drained member takes no new request
     and no new conversation while what it runs finishes, a constrained request is never
     replayed, a member on another release is a mismatch                  test_replicas
  4  Gherkin "Split refused without fabric": "no fabric link between Gold and R-1"; with a fabric
     link it is allowed but not built; its record is stopped, its start and its requests 501;
     a node that serves something: "needs 98 GiB, Gold has 61 free"; the agent's own estimate
                                                                          test_placement_and_split
  5  jobs through the agents: start (engine started, health, one token), move (the API id stays,
     the old engine stopped), stop (confirm, 503 deployment_stopped)      test_start_move_stop
  6  weights: a pack copied node to node through the controller, verified by content id; a
     changed byte fails it and leaves nothing; a pack without a manifest gets one; the matrix;
     a removal needs its confirm and is refused while a deployment serves the pack
                                                                          test_weights_copy
  7  the source agent pushes to the receiver agent itself (direct)        test_weights_direct
  8  a fleet update's rejoin waits for one token, not the health alone    test_rejoin_waits_warm
  9  SRV-467's replica sets (its routing_rules columns) become Replicas records once; its env
     form says how                                                        test_srv467_migration
 10  a fake engine ends with its test, whatever the test did             test_owner_watch

    python tests/test_deployments.py
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_backend  # noqa: E402
import owned  # noqa: E402
import test_fleet as tf  # noqa: E402
import test_node_agent as tna  # noqa: E402
from ports import free_port  # noqa: E402
from proxy import affinity, config as config_mod, deployments, fleetupdate  # noqa: E402
from proxy_util import Proxy, run  # noqa: E402
from tools import contract_check  # noqa: E402

ME = os.path.abspath(__file__)
FAKE = os.path.join(ROOT, "tests", "fake_backend.py")
DASH = os.path.join(ROOT, "docs", "contract", "dashboard-v1")


def dash_ok(obj, name):
    errs = contract_check.validate(obj, contract_check.load(name, DASH), root=DASH)
    assert not errs, (name, errs[:5])
    return obj


class Node:
    """A virtual node: its agent on a loopback port, one engine `e` (a fake backend in this
    process serving `models`, or a managed process on `port`)."""

    def __init__(self, serial: str, name: str, models=None, managed_port=None, addrs=None,
                 secret=None):
        self.srv = None
        if managed_port is None:
            self.srv, self.eport = fake_backend.start(models=models)
            doc = tna.engine_doc("e", self.eport, kind="generic")
        else:
            self.eport = managed_port
            doc = tna.engine_doc("e", managed_port, kind="generic", start_timeout_s=20,
                                 start=[sys.executable, FAKE, "--port", str(managed_port),
                                        "--models", ",".join(models)])
        self.agent = tna.agent_on(tna.machine(serial), engines=[doc], address="127.0.0.1",
                                  virtual=name, addrs=addrs, secret=secret)
        self.run = tna.Running(self.agent)
        self.url = self.run.url

    def close(self):
        self.run.close()
        if self.srv is not None:
            self.srv.shutdown()
            self.srv.server_close()


class Fleet:
    """A controller (Proxy, its ledger on) with the nodes added as records over their agents."""

    def __init__(self, nodes: dict, env=None):
        self.nodes = nodes                       # alias -> Node
        self.px = Proxy(env=env).__enter__()
        self.f = tf.fleet_on(self.px, tf.local_node(tf.fake_root(), h=tf.host(gpu=False)))
        self.ids = {}
        for alias, n in nodes.items():
            nid = n.agent.node_id()
            self.px.svc.cfg.add_node({"id": nid, "alias": alias, "endpoint": n.url,
                                      "address": "127.0.0.1",
                                      "trust": {"kind": "loopback-secret",
                                                "file": n.agent.secret_path}})
            self.ids[alias] = nid

    def sync(self):
        self.f.deps._ctx = None
        self.px.sync()

    def record(self, body, status=200):
        st, d = tf.send(self.px, "POST", "/v1/fleet/deployments", body)
        assert st == status, (st, d)
        self.sync()
        return d

    def chat(self, model, text="hi", conv=None, extra=None, stream=False):
        body = {"model": model, "stream": stream,
                "messages": [{"role": "user", "content": text}], **(extra or {})}
        if conv:
            body["conversation_id"] = conv
        st, h, raw = self.px.post("/v1/chat/completions", body)
        return st, h, raw

    def close(self):
        for src in [s for _, s in self.f.agents.values()]:
            src.close()
        self.px.__exit__(None, None, None)
        for n in self.nodes.values():
            n.close()


def _until(fn, s=20.0, every=0.05):
    end = time.time() + s
    while time.time() < end:
        got = fn()
        if got:
            return got
        time.sleep(every)
    return fn()


def _dep(fx, did):
    return tf.ok(tf.get(fx.px, f"/v1/fleet/deployments/{did}")[1], "deployment")


# ---------------------------------------------------------------------------- 1. two Singles

def test_two_singles():
    fx = Fleet({"Gold": Node("DEP-G-0001", "g", ["fake-a"]),
                "R-1": Node("DEP-1-0001", "r1", ["fake-b"]),
                "R-3": Node("DEP-3-0001", "r3", ["fake-c"])})
    try:
        g, r1, r3 = fx.ids["Gold"], fx.ids["R-1"], fx.ids["R-3"]
        a = fx.record({"model": "fake-a", "type": "single", "members": [g], "pack": "pack-a"})
        b = fx.record({"model": "fake-b", "type": "single", "members": [{"node": r1,
                                                                          "engine": "e"}]})
        assert tf.ok(a, "deployment")["record"] and a["state"] == "serving", a
        assert [m["backend"] for m in a["member_states"]] and b["member_states"][0]["engine"] == "e"
        # R-3's engine has no record: once its backend exists it is a derived Single
        fx.px.svc.cfg.create_backend({"id": "r3-e", "kind": "generic",
                                      "url": f"http://127.0.0.1:{fx.nodes['R-3'].eport}/v1"})
        fx.sync()
        deps = tf.ok(tf.get(fx.px, "/v1/fleet/deployments")[1], "deployments")["deployments"]
        by = {d["id"]: d for d in deps}
        assert set(by) == {"fake-a", "fake-b", "fake-c"}, by
        assert (by["fake-a"]["status"], by["fake-a"]["members"]) == ("serving", [g]), by
        assert by["fake-b"]["members"] == [r1] and by["fake-b"]["record"]
        assert not by["fake-c"]["record"] and by["fake-c"]["type"] == "single"
        assert by["fake-c"]["members"] == [r3] and not by["fake-c"]["managed"]
        # /v1/models: every deployment once, the records under their API id
        st, _, raw = fx.px.get("/v1/models")
        ids = [m["id"] for m in json.loads(raw)["data"]]
        assert st == 200 and sorted(ids) == ["fake-a", "fake-b", "fake-c"], ids
        card = next(m for m in json.loads(raw)["data"] if m["id"] == "fake-a")
        assert card["tandem"]["deployment"] == "fake-a" and card["tandem"]["nodes"] == ["Gold"]
        st, _, raw = fx.px.get("/v1/models/fake-b")
        assert st == 200 and json.loads(raw)["tandem"]["type"] == "single"
        # each request lands on its node, and says so
        for model, alias, srv in (("fake-a", "Gold", "Gold"), ("fake-b", "R-1", "R-1"),
                                  ("fake-c", "R-3", "R-3")):
            n0 = len(fx.nodes[srv].srv.fake.bodies)
            st, h, raw = fx.chat(model)
            assert st == 200, raw
            assert (h.get("X-Tandem-Node"), h.get("X-Tandem-Deployment")) == (alias, model), h
            assert len(fx.nodes[srv].srv.fake.bodies) == n0 + 1
        st, h, raw = fx.chat("fake-a", stream=True)
        assert st == 200 and h.get("X-Tandem-Node") == "Gold" and b"[DONE]" in raw
        rows = fx.px.rows()
        assert [(r["node"], r["deployment"]) for r in rows] == [
            (g, "fake-a"), (r1, "fake-b"), (r3, "fake-c"), (g, "fake-a")], rows
        # an api id that two records would share is refused; a bad body says what is wrong
        fx.record({"model": "fake-a", "type": "single", "members": [r1], "id": "other"}, 409)
        st, err = tf.send(fx.px, "POST", "/v1/fleet/deployments",
                          {"model": "x", "type": "replicas", "members": [g]})
        assert st == 400 and tf.ok(err, "error")["error"]["message"].startswith(
            "a Replicas deployment has two"), err
        st, err = tf.send(fx.px, "POST", "/v1/fleet/deployments",
                          {"model": "x", "type": "single", "members": ["n-nope"]})
        assert st == 404, err
        # PATCH a name, DELETE the record: its engine keeps serving as a derived Single
        st, d = tf.send(fx.px, "PATCH", "/v1/fleet/deployments/fake-b", {"name": "B model"})
        assert st == 200 and tf.ok(d, "deployment")["name"] == "B model"
        st, d = tf.send(fx.px, "DELETE", "/v1/fleet/deployments/fake-b", None)
        assert st == 200 and tf.ok(d, "deployment-deleted")["deleted"] == "fake-b"
        fx.sync()
        d = next(x for x in tf.get(fx.px, "/v1/fleet/deployments")[1]["deployments"]
                 if x["id"] == "fake-b")
        assert not d["record"] and d["members"] == [r1], d
        texts = [e["text"] for e in tf.get(fx.px, "/v1/fleet/events?type=deploy")[1]["events"]]
        assert "fake-a (Single) recorded on Gold" in texts and \
            "B model's record removed" in texts, texts
        # topology groups and per-deployment metrics follow the records
        topo = tf.ok(tf.get(fx.px, "/v1/fleet/topology")[1], "topology")
        assert {"deployment": "fake-a", "type": "single", "members": [g]} in topo["groups"]
        m = tf.ok(tf.get(fx.px, "/v1/fleet/metrics?source=deployment:fake-a&metric=tps"
                                "&range=5m")[1], "metrics")
        assert [s["node"] for s in m["series"]] == [g], m
    finally:
        fx.close()


# ---------------------------------------------------------------------------- 2. the ledger

def test_ledger_filter():
    fx = Fleet({"Gold": Node("LED-G-0001", "g", ["fake-a"]),
                "R-3": Node("LED-3-0001", "r3", ["fake-c"])})
    try:
        g, r3 = fx.ids["Gold"], fx.ids["R-3"]
        fx.record({"model": "fake-a", "type": "single", "members": [g]})
        fx.record({"model": "fake-c", "type": "single", "members": [r3], "id": "c-dep"})
        for model, n in (("fake-a", 3), ("fake-c", 2)):
            for _ in range(n):
                assert fx.chat(model)[0] == 200
        rows = fx.px.rows()
        assert sorted((r["node"], r["deployment"]) for r in rows) == sorted(
            [(g, "fake-a")] * 3 + [(r3, "c-dep")] * 2), rows
        api = tf.proxy_app.dashboard()
        api._memo.clear()
        q = {"bucket": "day", "node": r3}
        u = dash_ok(api.usage(q), "usage")
        assert u["totals"]["requests"] == 2 and u["filters"]["node"] == r3, u["totals"]
        assert dash_ok(api.usage({"bucket": "day"}), "usage")["totals"]["requests"] == 5
        u = dash_ok(api.usage({"bucket": "hour", "deployment": "fake-a"}), "usage")
        assert u["totals"]["requests"] == 3
        dims = api.usage({"bucket": "day"})["dimensions"]
        assert sorted(dims["nodes"]) == sorted([g, r3]) and \
            sorted(dims["deployments"]) == ["c-dep", "fake-a"], dims
        req = dash_ok(api.requests({"node": r3}), "requests")["requests"]
        assert len(req) == 2 and {(x["node"], x["deployment"]) for x in req} == {(r3, "c-dep")}
        # through the route, as the dashboard asks
        st, _, raw = fx.px.get(f"/v1/dashboard/usage?bucket=day&node={urllib.parse.quote(r3)}")
        assert st == 200 and json.loads(raw)["totals"]["requests"] == 2
    finally:
        fx.close()
    # a schema-10 ledger gains the two columns and their indexes in place, its rows kept
    from server import ledger as ledger_mod
    path = os.path.join(tempfile.mkdtemp(prefix="led10-"), "ledger.sqlite3")
    c = sqlite3.connect(path)
    c.executescript(ledger_mod.DDL)
    for name, kind in ledger_mod.ADDED[:-2]:
        c.execute(f"ALTER TABLE requests ADD COLUMN {name} {kind}")
    c.execute("INSERT INTO meta(key, value) VALUES ('schema_version', '10')")
    c.execute("INSERT INTO requests(ts_ms, request_id, model, client_id, client_kind, endpoint, "
              "stream, status) VALUES (1, 'r1', 'm', 'anon', 'curl', 'chat', 0, 200)")
    c.commit()
    assert ledger_mod.migrate(c) == 10
    cols = [r[1] for r in c.execute("PRAGMA table_info(requests)")]
    assert cols[-2:] == ["node", "deployment"] and ledger_mod.SCHEMA_VERSION == 11, cols
    idx = {r[1] for r in c.execute("PRAGMA index_list(requests)")}
    assert {"requests_node_ts", "requests_deployment_ts"} <= idx, idx
    assert c.execute("SELECT request_id, node FROM requests").fetchall() == [("r1", None)]
    assert c.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == ("11",)
    c.close()


# ---------------------------------------------------------------------------- 3. Replicas

def _conv_home(fx, did, home_backend, start=0):
    """A conversation key whose rendezvous home is `home_backend`."""
    rec = fx.px.svc.cfg.deployment(did)
    bids = [m["backend"] for m in rec["members"]]
    for i in range(start, start + 500):
        k = f"chat-{i}"
        if affinity.rendezvous(k, bids) == home_backend:
            return k
    raise AssertionError("no key")


def test_replicas():
    fx = Fleet({"Gold": Node("REP-G-0001", "g", ["fake-r"]),
                "R-1": Node("REP-1-0001", "r1", ["fake-r"])})
    try:
        g, r1 = fx.ids["Gold"], fx.ids["R-1"]
        d = fx.record({"model": "fake-r", "type": "replicas", "members": [g, r1],
                       "api_id": "qwen-r"})
        assert d["type"] == "replicas" and d["affinity"] == "root", d
        d = _dep(fx, "qwen-r")
        assert d["status"] == "serving" and d["status_text"] == "serving · 2 of 2 members", d
        rec = fx.px.svc.cfg.deployment("qwen-r")
        bg, br = (m["backend"] for m in rec["members"])
        # each conversation stays on one member, keys spread over both
        seen = {}
        for i in range(24):
            k = f"chat-{i}"
            st, h, _ = fx.chat("qwen-r", conv=k)
            assert st == 200 and h["X-Tandem-Deployment"] == "qwen-r"
            seen[k] = h["X-Tandem-Node"]
            st, h, _ = fx.chat("qwen-r", conv=k)
            assert h["X-Tandem-Node"] == seen[k], (k, h)
        assert set(seen.values()) == {"Gold", "R-1"}, seen
        # a worker's key (-task-N) goes with its chat (affinity root)
        st, h, _ = fx.chat("qwen-r", conv="chat-3-task-2")
        assert h["X-Tandem-Node"] == seen["chat-3"]
        # failover: Gold's engine dies; its conversation is served by R-1 before any byte
        k = _conv_home(fx, "qwen-r", bg)
        assert fx.chat("qwen-r", conv=k)[1]["X-Tandem-Node"] == "Gold"
        port = fx.nodes["Gold"].eport
        fx.nodes["Gold"].srv.shutdown()
        fx.nodes["Gold"].srv.server_close()
        fx.px.svc.relay.drop_pool(bg)        # a dead engine's kept connections die with it
        st, h, _ = fx.chat("qwen-r", conv=k)
        assert st == 200 and h["X-Tandem-Node"] == "R-1", h
        row = fx.px.rows()[-1]
        assert (row["node"], row["fallback_from"]) == (r1, bg), row
        fx.sync()
        d = _dep(fx, "qwen-r")
        assert d["status"] == "degraded" and d["status_text"].startswith(
            "1 of 2 members serving · Gold: down"), d
        assert [m["routable"] for m in d["member_states"]] == [False, True]
        st, h, _ = fx.chat("qwen-r", conv=k)              # the member that served keeps it
        assert h["X-Tandem-Node"] == "R-1"
        # Gold back: its own conversations come home only by the hash; k stays on R-1
        fx.nodes["Gold"].srv, _ = fake_backend.start(port=port, models=["fake-r"])
        fx.sync()
        assert _dep(fx, "qwen-r")["status"] == "serving"
        assert fx.chat("qwen-r", conv=k)[1]["X-Tandem-Node"] == "R-1"
        # drain R-1 with a request in flight there: it finishes; new conversations and R-1's
        # own go to Gold
        kr = _conv_home(fx, "qwen-r", br, 100)
        slow = []
        t = threading.Thread(target=lambda: slow.append(fx.chat(
            "qwen-r", "FAKE_SLOW_TTFT 2", conv=kr)))
        t.start()
        assert _until(lambda: fx.px.svc.relay.inflight.count(br) == 1, 5)
        plan = tf.ok(tf.get(fx.px, f"/v1/fleet/nodes/{r1}/actions/drain")[1], "action-plan")
        assert "fake-r: new requests go to Gold, another member" in plan["impact"], plan
        assert tf.send(fx.px, "POST", f"/v1/fleet/nodes/{r1}/actions/drain", {})[0] == 200
        fx.f.deps._ctx = None
        d = _dep(fx, "qwen-r")
        assert [m["state"] for m in d["member_states"]] == ["serving", "draining"], d
        for i in range(200, 210):
            st, h, _ = fx.chat("qwen-r", conv=f"new-{i}")
            assert st == 200 and h["X-Tandem-Node"] == "Gold", h
        assert fx.chat("qwen-r", conv=kr)[1]["X-Tandem-Node"] == "Gold"
        t.join(15)
        assert slow[0][0] == 200 and slow[0][1]["X-Tandem-Node"] == "R-1", slow
        fx.f.tick()
        n = tf.get(fx.px, f"/v1/fleet/nodes/{r1}")[1]
        assert n["drain"]["state"] == "drained", n
        assert tf.send(fx.px, "POST", f"/v1/fleet/nodes/{r1}/actions/undrain", None)[0] == 200
        # a constrained request is never replayed on the other member after one took it
        fx.f.deps._ctx = None
        kg = _conv_home(fx, "qwen-r", bg, 300)
        n_r1 = len(fx.nodes["R-1"].srv.fake.bodies)
        st, h, _ = fx.chat("qwen-r", "FAKE_503", conv=kg,
                           extra={"response_format": {"type": "json_object"}})
        assert st == 503 and len(fx.nodes["R-1"].srv.fake.bodies) == n_r1, st
        st, h, _ = fx.chat("qwen-r", "FAKE_503", conv=kg)
        assert len(fx.nodes["R-1"].srv.fake.bodies) > n_r1      # unconstrained: handed on
        # a member on another release takes no request; a new Replicas over two releases 409
        src = fx.f.source(fx.px.svc.cfg.node(r1))
        src.version = lambda: "0.16.0"
        fx.f.deps._ctx = None
        d = _dep(fx, "qwen-r")
        assert d["member_states"][1]["state"] == "mismatch" and \
            "Replicas members run one release" in d["member_states"][1]["text"], d
        assert all(fx.chat("qwen-r", conv=f"v-{i}")[1]["X-Tandem-Node"] == "Gold"
                   for i in range(6))
        st, err = tf.send(fx.px, "POST", "/v1/fleet/deployments",
                          {"model": "fake-r", "type": "replicas", "members": [g, r1],
                           "id": "two"})
        assert st == 409 and err["error"]["type"] == "version_mismatch", err
        # the version rule names the Replicas whose members differ
        from proxy import fleetwatch
        assert fleetwatch._split_groups([{"name": "Qwen", "type": "replicas",
                                          "members": [g, r1]}],
                                        [{"id": g, "version": "0.15.0", "status": "up"},
                                         {"id": r1, "version": "0.16.0", "status": "up"}]) == \
            "Qwen (Replicas)"
    finally:
        fx.close()


# ---------------------------------------------------------------------------- 4. placement

EST = {"total_gb": 108.0, "budget_gb": 106.63, "floor_gb": 15.0, "available_gb": 76.0,
       "fits": True, "parts": [{"id": "os", "label": "OS", "gb": 10.0},
                               {"id": "weights", "label": "Weights", "gb": 60.0},
                               {"id": "kv", "label": "KV", "gb": 20.0},
                               {"id": "engine", "label": "Engine", "gb": 18.0}]}


def test_placement_and_split():
    addrs = {"Gold": {"fab0": ["10.77.0.1/24"]}, "R-1": {"fab0": ["10.77.0.2/24"]}}
    fx = Fleet({"Gold": Node("PL-G-0001", "g", ["fake-a"]),
                "R-1": Node("PL-1-0001", "r1", ["fake-b"]),
                "R-2": Node("PL-2-0001", "r2", ["fake-c"])})
    try:
        g, r1, r2 = fx.ids["Gold"], fx.ids["R-1"], fx.ids["R-2"]
        body = {"model": "qwen3.8-27b", "pack": "exl3-4.0", "ctx": 32768, "nodes": [g, r1]}
        # the agent's own estimate (no engine here serves the Setup Guide): the same formula
        st, est = fx.nodes["Gold"].run.client.json(
            "POST", "/node/setup/estimate", {"selection": {"model": "qwen3.8-27b",
                                                           "profile": "exl3-4.0",
                                                           "window": 32768}})
        assert st == 200 and est["source"] == "agent" and est["total_gb"] > 20, est
        st, p = tf.send(fx.px, "POST", "/v1/fleet/placement", body)
        assert st == 200, p
        tf.ok(p, "placement")
        assert [n["source"] for n in p["nodes"]] == ["agent", "agent"], p["nodes"]
        split = next(t for t in p["types"] if t["type"] == "split")
        assert not split["allowed"] and not split["built"], split
        assert split["reason"] == "no fabric link between Gold and R-1", split
        # Gherkin: a Split record is kept, refused at start and at the gateway
        d = fx.record({"model": "ds", "type": "split", "members": [g, r1],
                       "stages": [{"node": g, "layers": "0-30", "gib": 78.2},
                                  {"node": r1, "layers": "31-60", "gib": 76.5}]})
        assert d["status"] == "stopped" and d["stages"][0]["layers"] == "0-30", d
        plan = tf.ok(tf.get(fx.px, "/v1/fleet/deployments/ds/actions/start")[1],
                     "deployment-plan")
        assert not plan["allowed"] and "ENG-307" in plan["blocked"], plan
        st, err = tf.send(fx.px, "POST", "/v1/fleet/deployments/ds/actions/start", {})
        assert st == 501 and tf.ok(err, "error")["error"]["type"] == "not_built", err
        st, _, raw = fx.chat("ds")
        assert st == 501 and json.loads(raw)["error"]["code"] == "not_built", raw
        st, _, raw = fx.px.get("/v1/models")
        assert "ds" not in [m["id"] for m in json.loads(raw)["data"]]
        fx.record({"model": "ds3", "type": "split", "members": [g, r1, r2]}, 400)
        # with a fabric link between them: allowed, not built
        for alias, a in addrs.items():
            fx.nodes[alias].agent.addrs = (lambda a=a: a)
        fx.f.placement._fabric.clear()
        p = tf.send(fx.px, "POST", "/v1/fleet/placement", body)[1]
        split = next(t for t in p["types"] if t["type"] == "split")
        assert split["allowed"] and not split["built"] and split["nodes"] == [g, r1], split
        assert [s["node"] for s in split["stages"]] == [g, r1] and "ENG-307" in split["warn"]
        # without nodes: every node planned; the pair with the fabric is offered
        p = tf.send(fx.px, "POST", "/v1/fleet/placement",
                    {k: v for k, v in body.items() if k != "nodes"})[1]
        assert len(p["nodes"]) == 3
        split = next(t for t in p["types"] if t["type"] == "split")
        assert split["allowed"] and split["nodes"] == [g, r1], split
        rep = next(t for t in p["types"] if t["type"] == "replicas")
        assert rep["allowed"] and len(rep["nodes"]) == 3, rep
        # a node that serves something keeps it: the new engine must fit in what is free
        fx.record({"model": "fake-a", "type": "single", "members": [g]})
        fx.f.placement._estimate = lambda row, src, sel: (dict(EST), None, "engine")
        p = tf.ok(tf.send(fx.px, "POST", "/v1/fleet/placement", dict(body, nodes=[g, r2]))[1],
                  "placement")
        ng, nr = p["nodes"]
        assert (ng["fits"], ng["reason"]) == (False, "needs 98 GiB, Gold has 61 free"), ng
        assert nr["fits"] and nr["need_gib"] == 98.0 and not nr["serving"], nr
        single = next(t for t in p["types"] if t["type"] == "single")
        assert single["allowed"] and single["nodes"] == [r2], single
        rep = next(t for t in p["types"] if t["type"] == "replicas")
        assert not rep["allowed"] and rep["reason"] == "needs two nodes that fit: only R-2 fits"
        st, err = tf.send(fx.px, "POST", "/v1/fleet/placement", dict(body, ctx=0))
        assert st == 400, err
        st, err = tf.send(fx.px, "POST", "/v1/fleet/placement", dict(body, nodes=["n-x"]))
        assert st == 404, err
    finally:
        fx.close()


# ---------------------------------------------------------------------------- 5. jobs

def _job_done(fx, did, s=40.0):
    def done():
        d = _dep(fx, did)
        return d if d["job"] and d["job"]["state"] != "running" else None
    d = _until(done, s, 0.1)
    assert d and d["job"]["state"] == "done", d
    return d


def test_start_move_stop():
    pg, pr = free_port(), free_port()
    assert 8000 not in (pg, pr)
    fx = Fleet({"Gold": Node("JOB-G-0001", "g", ["fake-m"], managed_port=pg),
                "R-1": Node("JOB-1-0001", "r1", ["fake-m"], managed_port=pr)})
    try:
        g, r1 = fx.ids["Gold"], fx.ids["R-1"]
        d = fx.record({"model": "fake-m", "type": "single", "members": [g]})
        assert d["managed"] and d["status"] in ("down", "starting"), d
        assert fx.chat("fake-m")[0] in (502, 504)
        plan = tf.ok(tf.get(fx.px, "/v1/fleet/deployments/fake-m/actions/start")[1],
                     "deployment-plan")
        assert plan["allowed"] and not plan["destructive"] and plan["confirm"] is None, plan
        assert plan["impact"] == ["Gold: TandemNode starts e, then one token warms it up"]
        st, res = tf.send(fx.px, "POST", "/v1/fleet/deployments/fake-m/actions/start", {})
        assert st == 200 and tf.ok(res, "deployment-job")["state"] == "running", res
        d = _job_done(fx, "fake-m")
        fx.sync()
        d = _dep(fx, "fake-m")
        assert d["status"] == "serving", d
        st, h, _ = fx.chat("fake-m")
        assert st == 200 and h["X-Tandem-Node"] == "Gold"
        st, j = tf.get(fx.px, f"/v1/fleet/deployments/jobs/{d['job']['id']}")
        job = tf.ok(j, "deployment-job")["job"]
        assert [s["phase"] for s in job["steps"]] == ["serving"], job
        assert any("warming up with one token" in x["text"] for x in job["log"]), job["log"]
        # move Gold -> R-1: the plan's confirm, the API id stays, Gold's engine stops
        st, err = tf.send(fx.px, "POST", "/v1/fleet/deployments/fake-m/actions/move",
                          {"to": r1})
        assert st == 428 and err["error"]["type"] == "confirm_required", err
        plan = tf.ok(tf.get(fx.px, f"/v1/fleet/deployments/fake-m/actions/move?to={r1}")[1],
                     "deployment-plan")
        assert plan["allowed"] and plan["from"] == g and plan["confirm"], plan
        assert "fake-m stays the API id" in plan["impact"], plan
        st, res = tf.send(fx.px, "POST", "/v1/fleet/deployments/fake-m/actions/move",
                          {"to": r1, "confirm": plan["confirm"]})
        assert st == 200, res
        _job_done(fx, "fake-m", 60.0)
        fx.sync()
        d = _dep(fx, "fake-m")
        assert d["members"] == [r1] and d["status"] == "serving" and d["api_id"] == "fake-m", d
        st, h, _ = fx.chat("fake-m")
        assert st == 200 and h["X-Tandem-Node"] == "R-1", h
        assert _until(lambda: fx.nodes["Gold"].run.client.json(
            "GET", "/node/engines/e")[1]["state"] == "down", 10)
        texts = [e["text"] for e in tf.get(fx.px, "/v1/fleet/events?type=deploy")[1]["events"]]
        assert "fake-m moved Gold → R-1" in texts, texts
        # stop: confirm; the record stops first; then the engine
        plan = tf.get(fx.px, "/v1/fleet/deployments/fake-m/actions/stop")[1]
        assert plan["allowed"] and plan["destructive"], plan
        st, res = tf.send(fx.px, "POST", "/v1/fleet/deployments/fake-m/actions/stop",
                          {"confirm": "made-up"})
        assert st == 409 and res["error"]["type"] == "confirm_invalid"
        plan = tf.get(fx.px, "/v1/fleet/deployments/fake-m/actions/stop")[1]
        assert tf.send(fx.px, "POST", "/v1/fleet/deployments/fake-m/actions/stop",
                       {"confirm": plan["confirm"]})[0] == 200
        _job_done(fx, "fake-m")
        d = _dep(fx, "fake-m")
        assert d["status"] == "stopped" and d["state"] == "stopped", d
        st, _, raw = fx.chat("fake-m")
        assert st == 503 and json.loads(raw)["error"]["code"] == "deployment_stopped"
        assert fx.nodes["R-1"].run.client.json("GET", "/node/engines/e")[1]["state"] == "down"
        # a deployment whose engine TandemNode does not manage: its start is refused when down
        fx.px.svc.cfg.create_backend({"id": "theirs", "kind": "generic",
                                      "url": f"http://127.0.0.1:{free_port()}/v1"})
        d = fx.record({"model": "x", "type": "single", "members": [{"backend": "theirs"}],
                       "id": "theirs-x"})
        plan = tf.get(fx.px, "/v1/fleet/deployments/theirs-x/actions/start")[1]
        assert not plan["allowed"], plan
    finally:
        fx.close()


# ---------------------------------------------------------------------------- 6. weights

def _pack(root: str, name: str, files: dict, manifest: bool | dict = True) -> str:
    d = os.path.join(root, name)
    rows = {}
    for rel, data in files.items():
        p = os.path.join(d, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
        rows[rel] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    if manifest:
        if isinstance(manifest, dict):
            rows.update(manifest)
        rows["config.json"]["sha256"] = None       # a small file the hub does not hash
        with open(os.path.join(d, "_hub_manifest.json"), "w") as f:
            json.dump({"repo": "test/tiny", "revision": None, "commit": "c0ffee",
                       "files": rows}, f)
    return d


def _copy_done(fx, jid, s=30.0):
    def done():
        j = tf.get(fx.px, f"/v1/fleet/weights/copy/{jid}")[1]["job"]
        return j if j["state"] != "running" else None
    return _until(done, s, 0.1)


def test_weights_copy():
    fx = Fleet({"Gold": Node("W-G-0001", "g", ["fake-a"]),
                "R-1": Node("W-1-0001", "r1", ["fake-b"])})
    try:
        g, r1 = fx.ids["Gold"], fx.ids["R-1"]
        src, dst = fx.nodes["Gold"].agent.weights.root, fx.nodes["R-1"].agent.weights.root
        big = os.urandom(3 * 1024 * 1024 + 17)
        _pack(src, "tiny-4.0", {"model-00001.safetensors": big, "config.json": b'{"x": 1}',
                                "sub/tok.json": b"[1, 2, 3]"})
        m = tf.ok(tf.get(fx.px, "/v1/fleet/weights")[1], "weights")
        tiny = next(p for p in m["packs"] if p["profile"] == "tiny-4.0")
        assert [x["node"] for x in tiny["nodes"]] == [g], tiny
        assert {n["node"]: n["store"] for n in m["nodes"]}[r1] == dst
        st, res = tf.send(fx.px, "POST", "/v1/fleet/weights/copy",
                          {"from": g, "to": r1, "profile": "tiny-4.0"})
        assert st == 200 and tf.ok(res, "weights-job")["job"]["via"] == "relay", res
        j = tf.ok({"contract_version": "1.0", "job": _copy_done(fx, res["job"]["id"])},
                  "weights-job")["job"]
        assert j["state"] == "done" and j["verified"], j
        assert j["content_id"]["copy"] == j["content_id"]["source"] == j["content_id"]["expected"]
        assert j["bytes_done"] == j["bytes_total"] == len(big) + 8 + 9 + os.path.getsize(
            os.path.join(src, "tiny-4.0", "_hub_manifest.json")), j
        with open(os.path.join(dst, "tiny-4.0", "model-00001.safetensors"), "rb") as f:
            assert f.read() == big
        tiny = next(p for p in tf.get(fx.px, "/v1/fleet/weights")[1]["packs"]
                    if p["profile"] == "tiny-4.0")
        assert sorted(x["node"] for x in tiny["nodes"]) == sorted([g, r1]) and tiny["same"]
        ev = tf.get(fx.px, "/v1/fleet/events?type=weights")[1]["events"]
        assert ev[0]["text"] == "Weights copied Gold → R-1: tiny-4.0" and \
            "verified" in ev[0]["detail"], ev[0]
        # again: the pack is there already
        st, res = tf.send(fx.px, "POST", "/v1/fleet/weights/copy",
                          {"from": g, "to": r1, "profile": "tiny-4.0"})
        j = _copy_done(fx, res["job"]["id"])
        assert j["state"] == "failed" and j["error"]["type"] == "exists", j
        # a file that is not its manifest's: failed, nothing left on the receiver
        _pack(src, "bad-1", {"a.bin": b"A" * 4096, "config.json": b"{}"},
              manifest={"a.bin": {"size": 4096, "sha256": "0" * 64}})
        st, res = tf.send(fx.px, "POST", "/v1/fleet/weights/copy",
                          {"from": g, "to": r1, "profile": "bad-1"})
        j = _copy_done(fx, res["job"]["id"])
        assert j["state"] == "failed" and j["error"]["type"] == "hash_mismatch", j
        assert sorted(os.listdir(dst)) == ["tiny-4.0"], os.listdir(dst)
        # a pack without a manifest: the receiver writes one from the hashes both ends agreed on
        _pack(src, "raw-1", {"w.bin": os.urandom(70_000), "config.json": b"{}"}, manifest=False)
        st, res = tf.send(fx.px, "POST", "/v1/fleet/weights/copy",
                          {"from": g, "to": r1, "profile": "raw-1"})
        j = _copy_done(fx, res["job"]["id"])
        assert j["state"] == "done" and j["verified"] and j["content_id"]["source"] is None, j
        assert os.path.exists(os.path.join(dst, "raw-1", "_hub_manifest.json"))
        # a removal: its plan's confirm; refused while a deployment serves the pack there
        fx.record({"model": "fake-a", "type": "single", "members": [g], "pack": "tiny-4.0"})
        p = tf.ok(tf.get(fx.px, f"/v1/fleet/weights/{g}/tiny-4.0")[1], "weights-plan")
        assert not p["allowed"] and p["blocked"] == "fake-a serves tiny-4.0 on Gold: stop it " \
                                                    "first", p
        p = tf.ok(tf.get(fx.px, f"/v1/fleet/weights/{r1}/tiny-4.0")[1], "weights-plan")
        assert p["allowed"] and p["confirm"] and p["bytes"] > 0, p
        st, err = tf.send(fx.px, "DELETE", f"/v1/fleet/weights/{r1}/tiny-4.0", None)
        assert st == 428 and err["error"]["type"] == "confirm_required", err
        st, res = tf.send(fx.px, "DELETE", f"/v1/fleet/weights/{r1}/tiny-4.0?confirm="
                                           f"{p['confirm']}", None)
        assert st == 200 and tf.ok(res, "weights-deleted")["deleted"], res
        assert not os.path.exists(os.path.join(dst, "tiny-4.0"))
        st, err = tf.send(fx.px, "DELETE", f"/v1/fleet/weights/{r1}/tiny-4.0?confirm="
                                           f"{p['confirm']}", None)
        assert st == 409, err
        st, err = tf.send(fx.px, "POST", "/v1/fleet/weights/copy",
                          {"from": g, "to": g, "profile": "tiny-4.0"})
        assert st == 400, err
        st, err = tf.send(fx.px, "POST", "/v1/fleet/weights/copy",
                          {"from": g, "to": r1, "profile": "../etc"})
        assert st == 400, err
    finally:
        fx.close()


def test_weights_direct():
    """The source's agent pushes to the receiver's (two agents of one owner: a shared secret here,
    pinned mTLS between paired nodes)."""
    secret = "shared-secret-" + "0" * 30
    a, b = Node("WD-A-0001", "a", ["x"], secret=secret), Node("WD-B-0001", "b", ["y"],
                                                              secret=secret)
    try:
        _pack(a.agent.weights.root, "pk-1", {"w.bin": os.urandom(200_000), "config.json": b"{}"})
        st, man = a.run.client.json("GET", "/node/weights/pk-1/manifest")
        assert st == 200 and man["content_basis"] == "hub_manifest", man
        st, rcv = b.run.client.json("POST", "/node/weights/receive",
                                    {"copy": "c1", "profile": "pk-1", "files": man["files"]})
        assert st == 200 and rcv["token"], rcv
        st, job = a.run.client.json("POST", "/node/weights/send",
                                    {"copy": "c1", "profile": "pk-1", "to": b.url,
                                     "token": rcv["token"]})
        assert st == 202, job
        got = _until(lambda: (lambda d: d if d["state"] != "running" else None)(
            a.run.client.json("GET", "/node/weights/send/c1")[1]))
        assert got["state"] == "done" and got["files_done"] == 3, got
        st, done = b.run.client.json("POST", "/node/weights/receive/c1/commit")
        assert st == 200 and done["content_id"] == man["content_id"], (done, man)
        # a receive's token: another value is refused
        st, rcv = b.run.client.json("POST", "/node/weights/receive",
                                    {"copy": "c2", "profile": "pk-2", "files": man["files"]})
        assert b.agent.weights.receive("c2").token == rcv["token"]
        try:
            b.agent.weights.receive("c2", "wrong")
            raise AssertionError("a wrong token was taken")
        except Exception as exc:                                     # noqa: BLE001
            assert getattr(exc, "status", None) == 403
        assert b.run.client.json("DELETE", "/node/weights/receive/c2")[0] == 200
        assert not [x for x in os.listdir(b.agent.weights.root) if x.startswith(".incoming")]
    finally:
        a.close()
        b.close()


# ---------------------------------------------------------------------------- 8. rejoin

def test_rejoin_waits_warm():
    fx = Fleet({"Gold": Node("RJ-G-0001", "g", ["fake-a"])})
    try:
        g = fx.ids["Gold"]
        fx.record({"model": "fake-a", "type": "single", "members": [g]})
        up = fx.f.updater()
        up.sleep = lambda s: time.sleep(0.01)
        answers = [(False, "HTTP 503"), (False, "HTTP 503"), (True, "one token")]
        tried = []

        def warm(bid, model, timeout=60.0):
            tried.append((bid, model))
            return answers.pop(0) if answers else (True, "one token")
        fx.f.deps.warm = warm
        job = {"id": "upd-test", "kind": "update", "state": "running", "target": "x",
               "log": [], "nodes": {g: {"alias": "Gold", "phase": "rejoin", "detail": None,
                                        "drained_before": False, "started_at": None,
                                        "ended_at": None, "from": None}}}
        up._rejoin(job, g, False)
        assert job["nodes"][g]["phase"] == "done" and len(tried) == 3, (job, tried)
        assert tried[0][1] == "fake-a"
        # never warm: the step fails with the reason, the node stays drained
        fleetupdate.REJOIN_WAIT_S, was = 0.3, fleetupdate.REJOIN_WAIT_S
        try:
            fx.f.deps.warm = lambda bid, model, timeout=60.0: (False, "HTTP 503")
            job["nodes"][g]["phase"] = "rejoin"
            try:
                up._rejoin(job, g, False)
                raise AssertionError("a cold engine rejoined")
            except fleetupdate.UpdateError as exc:
                assert exc.kind == "rejoin_timeout" and "not warm" in exc.message and \
                    "HTTP 503" in exc.message, exc.message
        finally:
            fleetupdate.REJOIN_WAIT_S = was
        # a deployment's own warm-up: one token through the real backend
        rec = fx.px.svc.cfg.deployment("fake-a")
        ok, why = deployments.Deployments.warm(fx.f.deps, rec["members"][0]["backend"], "fake-a")
        assert ok and why == "one token", why
    finally:
        fx.close()


# ---------------------------------------------------------------------------- 9. SRV-467

def test_srv467_migration():
    d = tempfile.mkdtemp(prefix="srv467-")
    path = os.path.join(d, "config.sqlite3")
    c = sqlite3.connect(path)
    c.executescript(config_mod.DDL)
    c.execute("ALTER TABLE routing_rules ADD COLUMN backends TEXT")
    c.execute("ALTER TABLE routing_rules ADD COLUMN replica TEXT")
    c.execute("INSERT INTO routing_rules(position, pattern, backend_id, enabled, source, "
              "backends, replica) VALUES (1, 'TandemLLM', 'self', 1, 'gui', ?, ?)",
              (json.dumps(["self", "sparkb"]), json.dumps({"affinity": "own",
                                                           "windows": "mixed"})))
    c.execute("INSERT INTO routing_rules(position, pattern, backend_id, enabled, source) "
              "VALUES (2, 'gpt-*', 'openai', 1, 'gui')")
    c.commit()
    c.close()
    cfg = config_mod.ConfigDB(path, env={}, use_keychain=False).open()
    try:
        recs = cfg.deployments()
        assert len(recs) == 1, recs
        r = recs[0]
        assert (r["id"], r["type"], r["model"]) == ("tandemllm", "replicas", "TandemLLM"), r
        assert r["members"] == [{"backend": "self"}, {"backend": "sparkb"}], r
        assert r["options"]["affinity"] == "own" and r["options"]["windows"] == "mixed"
        assert r["options"]["api_id"] == "TandemLLM" and "SRV-467" in r["options"]["from"]
        rules = {x["pattern"]: x["enabled"] for x in cfg.rules()}
        assert rules == {"TandemLLM": False, "gpt-*": True}, rules
    finally:
        cfg.close()
    cfg = config_mod.ConfigDB(path, env={}, use_keychain=False).open()   # once only
    assert len(cfg.deployments()) == 1
    cfg.close()
    try:
        config_mod.parse_env({"TANDEM_ROUTE_1": "TandemLLM=self+sparkb"})
        raise AssertionError("a replica set in the environment was taken")
    except config_mod.ConfigError as exc:
        assert "Replicas deployment since SRV-731" in exc.message, exc.message


# ---------------------------------------------------------------------------- 10. orphans

def test_owner_watch():
    owner = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    port = free_port()
    env = dict(os.environ, **{owned.ENV: str(owner.pid)})
    p = subprocess.Popen([sys.executable, FAKE, "--port", str(port), "--models", "m"], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    try:
        assert _until(lambda: tna.raw(port, "GET", "/health")[0] == 200
                      if _port_open(port) else False, 10), "the fake did not start"
        owner.kill()
        owner.wait()
        p.wait(timeout=5)                         # it ends on its own once its owner is gone
    finally:
        if p.poll() is None:
            p.kill()
        if owner.poll() is None:
            owner.kill()


def _port_open(port: int) -> bool:
    import socket
    s = socket.socket()
    s.settimeout(0.2)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


if __name__ == "__main__":
    run(globals())
