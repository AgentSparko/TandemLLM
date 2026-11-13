"""SRV-750: any OpenAI-compatible backend in the fleet, without TandemNode or our engine (SRV-749).

Every backend here is tests/fake_backend.py on a loopback port: no network, no real key. The
controller's own machine is a fake one and is no node (`local_node` off), so a backend on
loopback sits on no node, as a server on another machine does.

  1  Gherkin "Connect a server": a backend added by its URL alone (no id, no kind) is tested,
     saved and is a fleet member; its models are deployments, are in /v1/models and answer
     through the gateway, streamed and not, with X-Tandem-Node and a ledger row that names the
     member; the backend was asked for the OpenAI routes only          test_connect_a_server
  2  Gherkin "Wrong key": the test says the key was rejected, a verified add saves nothing
     until it is confirmed, the saved member reads "key rejected" (alert, not off) and another
     member keeps serving; a corrected key brings it up                test_wrong_key
  3  Gherkin "No host figures without TandemNode": the member's sample is null with a note that
     names TandemNode, a hardware series is all null, `all` leaves it out of hardware metrics,
     the engine metrics count what the gateway measured, and every node-only route answers 409
     not_a_node                                                        test_no_host_figures
  4  Gherkin "Usage counts backends": 5 requests to a backend and 5 to a Spark are 10 in the
     ledger, 5 per member, by the Usage filters                        test_usage_by_member
  5  the key: encrypted at rest in a 0600 file set, in no log line, ledger row, /health, config,
     fleet, export, audit or error answer; the export lists the backends and their keys as left
     out                                                               test_the_key_never_leaves
  6  Replicas across two backends only as a record: the same model id on two backends is two
     Singles; a record over them serves, its members read "identity not available", no canary
     request reaches them and none is a mismatch                       test_replicas_only_explicit
  7  a backend whose model list changes: its deployments follow, and a client pinned to the old
     id reads the new ids in its 404                                   test_model_list_changes
  8  a TandemLLM engine reached by URL: another engine API major is down with the reason and
     takes no request; 1.x and an engine that does not say serve       test_engine_by_url_api
  9  a fresh controller has no node and no backend member; one backend on a node's address is
     that node's engine, not a member                                  test_first_run_and_host_link
  10 a Split is never recorded on a backend member: 409 not_a_node   test_split_not_on_a_backend

    python tests/test_backend_members.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import sys
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_deployments as td  # noqa: E402
import test_fleet as tf  # noqa: E402
from proxy import canary as canary_mod  # noqa: E402
from proxy import members as members_mod  # noqa: E402
from proxy_util import FIXTURE_KEYS, Proxy, fake, run  # noqa: E402

GOOD = FIXTURE_KEYS[0]
WRONG = "sk-fixture-WRONGKEY00009999"
OPENAI_PATHS = {"/v1/models", "/v1/chat/completions", "/v1/completions", "/v1/embeddings",
                "/v1/responses"}


class Bed:
    """A controller on a fake machine that is no node, with a clock the test moves."""

    def __init__(self):
        self.t = [tf.T0]
        self.px = Proxy().__enter__()
        self.f = tf.fleet_on(self.px, tf.local_node(tf.fake_root(), h=tf.host(gpu=False)),
                             clock=lambda: self.t[0])
        self.f.local_node = False
        self.fakes = []
        self.said: list[str] = []            # every answer read, for the key grep

    def server(self, **kw):
        srv, port = fake(**kw)
        self.fakes.append(srv)
        return srv.fake, f"http://127.0.0.1:{port}"

    def call(self, method, path, body=None):
        st, _, raw = self.px.request(method, path, body)
        text = raw.decode("utf-8", "replace")
        self.said.append(text)
        return st, (json.loads(text) if text.strip() else None)

    def add(self, url, status=201, **kw):
        st, d = self.call("POST", "/v1/config/backends", {"url": url, "verify": True, **kw})
        assert st == status, (st, d)
        self.f.deps._ctx = None
        return d

    def members(self):
        st, d = self.call("GET", "/v1/fleet/nodes")
        assert st == 200, d
        tf.ok(d, "nodes")
        return d

    def member(self, bid):
        mid = members_mod.member_id(bid)
        return next(m for m in self.members()["backends"] if m["id"] == mid)

    def deployments(self):
        st, d = self.call("GET", "/v1/fleet/deployments")
        assert st == 200, d
        return tf.ok(d, "deployments")["deployments"]

    def chat(self, model, stream=False, text="hi"):
        st, h, raw = self.px.post("/v1/chat/completions", {
            "model": model, "stream": stream, "messages": [{"role": "user", "content": text}]})
        self.said.append(raw.decode("utf-8", "replace"))
        return st, h, raw

    def tick(self, n=3):
        for _ in range(n):
            self.t[0] += 2.0
            self.f.tick(self.t[0])

    def metric(self, source, metric):
        st, d = self.call("GET", f"/v1/fleet/metrics?source={urllib.parse.quote(source)}"
                                 f"&metric={metric}&range=5m")
        assert st == 200, d
        return tf.ok(d, "metrics")["series"]

    def close(self):
        self.px.__exit__(None, None, None)
        for s in self.fakes:
            s.shutdown()
            s.server_close()


def _no_key(bed: Bed, *keys):
    """No answer, log line or file the controller wrote holds one of `keys`."""
    blobs = [("an answer", t) for t in bed.said] + [("the log", "\n".join(bed.px.logs))]
    for path in bed.px.files():
        with open(path, "rb") as f:
            blobs.append((path, f.read().decode("latin-1")))
    for where, text in blobs:
        for k in keys:
            assert k not in text, f"a key is in {where}"


# ---------------------------------------------------------------------------- 1
def test_connect_a_server():
    bed = Bed()
    try:
        fk, url = bed.server(models=["tiny-a", "tiny-b"])
        assert bed.members() == {"contract_version": "1.0", "nodes": [], "backends": []}
        st, t = bed.call("POST", "/v1/config/test", {"url": url})
        assert st == 200 and t["ok"] and t["models"] == ["tiny-a", "tiny-b"] and \
            t["reason"] is None and "/v1/chat/completions" in t["routes"], t
        assert bed.call("GET", "/v1/config")[1]["backends"] == []     # a test saves nothing
        b = bed.add(url, label="Bench server")
        assert b["id"] == "bench-server" and b["kind"] == "generic" and b["test"]["state"] == "up"
        m = bed.member("bench-server")
        assert m["class"] == "backend" and m["status"] == "up" and m["alias"] == "Bench server"
        assert m["hw"] == "OpenAI-compatible server" and m["backend"]["models"] == [
            "tiny-a", "tiny-b"] and m["backend"]["figures"] == "gateway", m
        assert m["backend"]["key_set"] is False and m["backend"]["engine_api"] is None
        st, one = bed.call("GET", f"/v1/fleet/nodes/{m['id']}")
        assert st == 200 and tf.ok(one, "node")["id"] == m["id"]
        deps = {d["id"]: d for d in bed.deployments()}
        assert sorted(deps) == ["tiny-a", "tiny-b"], sorted(deps)
        for d in deps.values():
            ms = d["member_states"][0]
            assert d["type"] == "single" and d["record"] is False and d["status"] == "serving"
            assert d["members"] == [m["id"]] and ms["backend"] == "bench-server" and \
                ms["alias"] == "Bench server" and ms["managed"] is False and ms["routable"]
        st, _, raw = bed.px.get("/v1/models")
        assert st == 200 and {"tiny-a", "tiny-b"} <= {x["id"] for x in json.loads(raw)["data"]}
        st, h, raw = bed.chat("tiny-a")
        assert st == 200 and json.loads(raw)["choices"][0]["message"]["content"], raw[:200]
        assert h["X-Tandem-Node"] == "Bench server" and h["X-Tandem-Deployment"] == "tiny-a", h
        st, h, raw = bed.chat("tiny-b", stream=True)
        assert st == 200 and b"data: [DONE]" in raw and h["X-Tandem-Node"] == "Bench server"
        rows = bed.px.rows()
        assert [(r["node"], r["deployment"], r["backend"], r["status"]) for r in rows] == [
            (m["id"], "tiny-a", "bench-server", 200), (m["id"], "tiny-b", "bench-server", 200)]
        # what the fleet reads of it while it is watched: the placement plan, the sampler, the
        # deployments again. A plain backend is asked for the OpenAI routes and nothing else
        bed.call("POST", "/v1/fleet/placement", {"model": "tiny-a", "pack": "", "ctx": 4096})
        bed.tick()
        bed.deployments()
        assert set(fk.seen) <= OPENAI_PATHS, sorted(fk.seen)
        # a second server without a label takes its host as the id
        _, url2 = bed.server(models=["tiny-c"])
        assert bed.add(url2)["id"] == "127-0-0-1"
        assert len(bed.members()["backends"]) == 2
        cols = [x["colour"] for x in bed.members()["backends"]]
        assert len(set(cols)) == 2, cols
    finally:
        bed.close()


# ---------------------------------------------------------------------------- 2
def test_wrong_key():
    bed = Bed()
    try:
        fk, url = bed.server(models=["tiny-a"], key=GOOD)
        _, open_url = bed.server(models=["tiny-open"])
        bed.add(open_url, label="Open")
        st, t = bed.call("POST", "/v1/config/test", {"url": url, "key": WRONG})
        assert st == 200 and not t["ok"] and t["reason"] == "key_rejected" and \
            t["key_rejected"] is True and t["status"] == 401 and t["models"] == [], t
        # verified: nothing is saved
        st, d = bed.call("POST", "/v1/config/backends", {"url": url, "key": WRONG,
                                                         "label": "Keyed", "verify": True})
        assert st == 409 and d["error"]["type"] == "key_rejected" and \
            "nothing was saved" in d["error"]["message"] and \
            d["error"]["test"]["key_rejected"] is True, d
        assert [b["id"] for b in bed.call("GET", "/v1/config")[1]["backends"]] == ["open"]
        assert [m["alias"] for m in bed.members()["backends"]] == ["Open"]
        # a server that does not answer is refused the same way, with its own word
        st, d = bed.call("POST", "/v1/config/backends", {"url": "http://127.0.0.1:9",
                                                         "label": "Nobody", "verify": True})
        assert st == 409 and d["error"]["type"] == "unreachable", d
        # ... until the user confirms
        bed.add(url, key=WRONG, label="Keyed", confirm=True)
        m = bed.member("keyed")
        assert m["status"] == "alert" and m["status_text"].startswith("key rejected"), m
        assert m["backend"]["state"] == "key_rejected" and m["backend"]["key_set"] is True
        # the rest of the fleet is not touched: the other member is up and serves
        assert bed.member("open")["status"] == "up"
        assert bed.chat("tiny-open")[0] == 200
        assert [d["id"] for d in bed.deployments()] == ["tiny-open"]
        # the right key brings it up
        st, d = bed.call("PATCH", "/v1/config/backends/keyed", {"key": GOOD})
        assert st == 200, d
        bed.px.sync()
        bed.f.deps._ctx = None
        m = bed.member("keyed")
        assert m["status"] == "up" and m["backend"]["state"] == "up", m
        assert bed.chat("tiny-a")[0] == 200
        bed.px.rows()
        _no_key(bed, GOOD, WRONG)
    finally:
        bed.close()


# ---------------------------------------------------------------------------- 3
def test_no_host_figures():
    bed = Bed()
    try:
        _, url = bed.server(models=["tiny-a"])
        bed.add(url, label="Bench")
        mid = bed.member("bench")["id"]
        st, s = bed.call("GET", f"/v1/fleet/nodes/{mid}/sample")
        assert st == 200 and tf.ok(s, "sample")["sample"] is None and s["via"] == "none"
        assert "no host figures" in s["note"] and "TandemNode" in s["note"], s["note"]
        assert bed.chat("tiny-a", stream=True)[0] == 200
        bed.px.rows()
        bed.tick()
        for metric in ("gpu", "mem", "power", "temp", "cpu"):
            got = bed.metric(f"node:{mid}", metric)
            assert [x["node"] for x in got] == [mid] and set(got[0]["values"]) == {None}, metric
            assert bed.metric("all", metric) == [], metric
        req = bed.metric(f"node:{mid}", "req")[0]["values"]
        assert any(v is not None for v in req) and bed.metric("all", "req")[0]["node"] == mid
        assert any(v is not None for v in bed.metric(f"node:{mid}", "ttft")[0]["values"])
        assert set(bed.metric(f"node:{mid}", "tps")[0]["values"]) == {None}   # not known
        assert any(v is not None for v in bed.metric("deployment:tiny-a", "req")[0]["values"])
        st, mem = bed.call("GET", "/v1/fleet/memory")
        assert st == 200 and mem["nodes"] == []
        st, topo = bed.call("GET", "/v1/fleet/topology")
        assert st == 200 and tf.ok(topo, "topology")
        # what needs a machine says so, and never fails
        for method, path, body in (
                ("GET", f"/v1/fleet/nodes/{mid}/actions/drain", None),
                ("POST", f"/v1/fleet/nodes/{mid}/actions/drain", {}),
                ("PATCH", f"/v1/fleet/nodes/{mid}", {"alias": "x"}),
                ("DELETE", f"/v1/fleet/nodes/{mid}", None),
                ("GET", f"/v1/fleet/nodes/{mid}/setup-confirm/apply", None),
                ("GET", f"/v1/fleet/nodes/{mid}/setup/state", None)):
            st, d = bed.call(method, path, body)
            assert st == 409 and tf.ok(d, "error")["error"]["type"] == "not_a_node", (path, d)
            assert "TandemNode" in d["error"]["message"] and "Bench" in d["error"]["message"]
        st, d = bed.call("GET", "/v1/fleet/nodes/backend.nobody")
        assert st == 404, d
        st, d = bed.call("GET", "/v1/fleet/nodes/backend.nobody/sample")
        assert st == 404, d
    finally:
        bed.close()


# ---------------------------------------------------------------------------- 4
def test_usage_by_member():
    fx = td.Fleet({"Gold": td.Node("MEM-G-0001", "g", ["spark-a"])})
    try:
        fx.f.local_node = False
        srv, port = fake(models=["tiny-a"])
        st, d = tf.send(fx.px, "POST", "/v1/config/backends",
                        {"url": f"http://127.0.0.1:{port}", "label": "Bench", "verify": True})
        assert st == 201, d
        g, mid = fx.ids["Gold"], members_mod.member_id("bench")
        fx.record({"model": "spark-a", "type": "single", "members": [g]})
        st, d = tf.get(fx.px, "/v1/fleet/nodes")
        tf.ok(d, "nodes")
        assert [n["id"] for n in d["nodes"]] == [g] and [b["id"] for b in d["backends"]] == [mid]
        assert d["backends"][0]["colour"] != d["nodes"][0]["colour"]
        deps = {x["id"]: x["members"] for x in tf.get(fx.px, "/v1/fleet/deployments")[1][
            "deployments"]}
        assert deps == {"spark-a": [g], "tiny-a": [mid]}, deps
        for model in ("spark-a", "tiny-a"):
            for _ in range(5):
                assert fx.chat(model)[0] == 200
        rows = fx.px.rows()
        assert sorted(r["node"] for r in rows) == sorted([g] * 5 + [mid] * 5), rows
        api = tf.proxy_app.dashboard()
        api._memo.clear()
        assert td.dash_ok(api.usage({"bucket": "day"}), "usage")["totals"]["requests"] == 10
        for node in (g, mid):
            u = td.dash_ok(api.usage({"bucket": "day", "node": node}), "usage")
            assert u["totals"]["requests"] == 5 and u["filters"]["node"] == node, (node, u)
        assert sorted(api.usage({"bucket": "day"})["dimensions"]["nodes"]) == sorted([g, mid])
        q = urllib.parse.quote(mid)
        st, _, raw = fx.px.get(f"/v1/dashboard/usage?bucket=day&node={q}")
        assert st == 200 and json.loads(raw)["totals"]["requests"] == 5
        srv.shutdown()
        srv.server_close()
    finally:
        fx.close()


# ---------------------------------------------------------------------------- 5
def test_the_key_never_leaves():
    bed = Bed()
    try:
        fk, url = bed.server(models=["tiny-a"], key=GOOD)
        bed.add(url, key=GOOD, label="Keyed")
        assert bed.chat("tiny-a")[0] == 200
        fk.key = FIXTURE_KEYS[1]                 # the server now refuses the stored key
        st, _, raw = bed.chat("tiny-a")
        assert st >= 400, (st, raw[:200])
        bed.px.sync()
        bed.f.deps._ctx = None
        assert bed.member("keyed")["status"] == "alert"
        bed.deployments()
        for path in ("/health", "/v1/config", "/v1/config/audit", "/v1/config/backends/keyed",
                     "/v1/fleet/app/export", "/v1/fleet/events", "/v1/fleet/alerts",
                     f"/v1/fleet/nodes/{members_mod.member_id('keyed')}"):
            st, _ = bed.call("GET", path)
            assert st == 200, path
        st, ex = bed.call("GET", "/v1/fleet/app/export")
        tf.ok(ex, "fleet-config")
        assert ex["secrets"] == "excluded" and "the backends and their API keys" in ex["excluded"]
        assert "keyed" not in json.dumps(ex) and url not in json.dumps(ex)
        bed.px.rows()
        _no_key(bed, GOOD, FIXTURE_KEYS[1])
        # at rest: ciphertext in the config database, the master key and the database 0600
        db = os.path.join(bed.px.dir, "config.sqlite3")
        con = sqlite3.connect(db)
        (blob, last4), = con.execute("SELECT ciphertext, last4 FROM backend_secrets").fetchall()
        con.close()
        assert GOOD.encode() not in bytes(blob) and last4 == GOOD[-4:]
        for name in ("secrets.key", "config.sqlite3"):
            mode = stat.S_IMODE(os.stat(os.path.join(bed.px.dir, name)).st_mode)
            assert mode == 0o600, (name, oct(mode))
        assert bed.px.svc.cfg.key_for("keyed") == GOOD       # and it still decrypts
    finally:
        bed.close()


# ---------------------------------------------------------------------------- 6
def test_replicas_only_explicit():
    bed = Bed()
    try:
        fa, ua = bed.server(models=["twin"])
        fb, ub = bed.server(models=["twin"])
        bed.add(ua, label="Srv A")
        bed.add(ub, label="Srv B")
        deps = bed.deployments()
        assert sorted((d["id"], d["type"]) for d in deps) == [
            ("srv-a/twin", "single"), ("srv-b/twin", "single")], deps
        assert bed.chat("twin")[0] == 404          # the same id on two backends is no Replicas
        st, d = bed.call("POST", "/v1/fleet/deployments", {
            "model": "twin", "type": "replicas",
            "members": [{"backend": "srv-a"}, {"backend": "srv-b"}]})
        assert st == 200, d
        bed.f.deps._ctx = None
        a, b = members_mod.member_id("srv-a"), members_mod.member_id("srv-b")
        got = bed.f.deps.canary.run("twin")
        assert got["ran"] is False and "not available" in got["note"], got
        st, d = bed.call("GET", "/v1/fleet/deployments/twin")
        d = tf.ok(d, "deployment")
        assert d["type"] == "replicas" and d["status"] == "serving" and d["members"] == [a, b]
        for ms in d["member_states"]:
            assert ms["state"] == "serving" and ms["routable"], ms
            assert ms["check"]["identity"] == "not_available" and \
                "not available" in ms["check"]["identity_text"], ms
            assert ms["check"]["canary"] is None and "not available" in ms["check"]["canary_text"]
        for i in range(6):
            st, h, _ = bed.px.post("/v1/chat/completions", {
                "model": "twin", "conversation_id": f"c{i}",
                "messages": [{"role": "user", "content": f"hello {i}"}]})
            assert st == 200 and h["X-Tandem-Deployment"] == "twin", (st, h)
        rows = bed.px.rows()
        assert {r["deployment"] for r in rows if r["status"] == 200} == {"twin"}
        assert {r["node"] for r in rows if r["status"] == 200} <= {a, b}
        for fk in (fa, fb):                      # no canary request, no identity read
            assert all(h.get("User-Agent") != canary_mod.CANARY_UA for h, _ in fk.calls)
            assert set(fk.seen) <= OPENAI_PATHS, sorted(fk.seen)
        # a move onto a backend is refused in words
        st, p = bed.call("GET", f"/v1/fleet/deployments/twin/actions/move?from={a}&to={b}")
        assert st == 200 and p["allowed"] is False, p
    finally:
        bed.close()


def test_split_not_on_a_backend():
    bed = Bed()
    try:
        _fa, ua = bed.server(models=["twin"])
        _fb, ub = bed.server(models=["twin"])
        bed.add(ua, label="Srv A")
        bed.add(ub, label="Srv B")
        a, b = members_mod.member_id("srv-a"), members_mod.member_id("srv-b")
        for ms in ([a, b], [{"backend": "srv-a"}, {"backend": "srv-b"}]):
            st, d = bed.call("POST", "/v1/fleet/deployments",
                             {"model": "twin", "type": "split", "members": ms})
            assert st == 409, (st, d)
            e = tf.ok(d, "error")["error"]
            assert e["type"] == "not_a_node" and "Srv A" in e["message"], e
        assert all(x["type"] != "split" for x in bed.deployments())
    finally:
        bed.close()


# ---------------------------------------------------------------------------- 7
def test_model_list_changes():
    bed = Bed()
    try:
        fk, url = bed.server(models=["tiny-a"])
        bed.add(url, label="Bench")
        assert [d["id"] for d in bed.deployments()] == ["tiny-a"]
        fk.models[:] = ["tiny-b"]
        bed.px.svc.health.kick("bench")
        bed.px.sync()
        bed.f.deps._ctx = None
        assert [(d["id"], d["status"]) for d in bed.deployments()] == [("tiny-b", "serving")]
        assert bed.member("bench")["backend"]["models"] == ["tiny-b"]
        st, _, raw = bed.chat("tiny-a")
        err = json.loads(raw)["error"]
        assert st == 404 and err["code"] == "model_not_found" and \
            "Bench serves now tiny-b" in err["message"], err
        st, _, raw = bed.chat("bench/tiny-a")
        assert st == 404 and "serves now tiny-b" in json.loads(raw)["error"]["message"]
        st, _, raw = bed.chat("never-listed")
        assert st == 404 and "serves now" not in json.loads(raw)["error"]["message"]
        assert bed.chat("tiny-b")[0] == 200
    finally:
        bed.close()


# ---------------------------------------------------------------------------- 8
def test_engine_by_url_api():
    bed = Bed()
    try:
        old, u_old = bed.server(models=["eng-two"])
        old.health = {"engine_api": "2.0"}
        new, u_new = bed.server(models=["eng-one"])
        new.health = {"engine_api": "1.4"}
        _, u_plain = bed.server(models=["eng-plain"])
        for label, url in (("Two", u_old), ("One", u_new), ("Plain", u_plain)):
            bed.add(url, label=label, kind="tandemllm")
        deps = {d["id"]: d for d in bed.deployments()}
        assert deps["eng-one"]["status"] == "serving" and deps["eng-plain"]["status"] == "serving"
        two = deps["eng-two"]
        assert two["status"] == "down" and "engine API 2.0" in two["status_text"] and \
            "needs 1.x" in two["status_text"], two
        m = bed.member("two")
        assert m["hw"] == "TandemLLM engine by URL" and m["status"] == "alert" and \
            "engine API 2.0" in m["status_text"], m
        assert m["backend"]["engine_api"]["ok"] is False
        assert bed.member("one")["backend"]["engine_api"] == {
            "engine_api": "1.4", "assumed": False, "ok": True, "why": None}
        assert bed.member("plain")["backend"]["engine_api"]["assumed"] is True
        st, _, raw = bed.chat("eng-two")
        assert st == 503 and json.loads(raw)["error"]["code"] == "no_member", raw[:300]
        assert bed.chat("eng-one")[0] == 200 and bed.chat("eng-plain")[0] == 200
    finally:
        bed.close()


# ---------------------------------------------------------------------------- 9
def test_first_run_and_host_link():
    bed = Bed()
    try:
        d = bed.members()
        assert d["nodes"] == [] and d["backends"] == []
        assert bed.call("GET", "/v1/config")[1]["setup_needed"] is True
        assert bed.deployments() == []
    finally:
        bed.close()
    # a backend on a node's address (its engine's port) is that node's engine: the node gives
    # the hardware figures, and no member stands beside it
    fx = td.Fleet({"Gold": td.Node("MEM-H-0001", "h", ["spark-a"])})
    try:
        fx.f.local_node = False
        fx.record({"model": "spark-a", "type": "single", "members": [fx.ids["Gold"]]})
        assert len(fx.px.svc.cfg.backends()) == 1
        st, d = tf.get(fx.px, "/v1/fleet/nodes")
        assert [n["alias"] for n in d["nodes"]] == ["Gold"] and d["backends"] == [], d
        deps = tf.get(fx.px, "/v1/fleet/deployments")[1]["deployments"]
        assert [(x["id"], x["members"]) for x in deps] == [("spark-a", [fx.ids["Gold"]])], deps
    finally:
        fx.close()


if __name__ == "__main__":
    run(globals())
