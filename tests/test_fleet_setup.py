"""VIS-253 (tests for VIS-252): each node's Setup Guide through the controller,
`/v1/fleet/nodes/{id}/setup/*` and its writes' plans `/v1/fleet/nodes/{id}/setup-confirm/*`
(proxy/fleetsetup.py, proxy/app.py).

The nodes are TandemNode agents in this process on loopback ports (virtual nodes), each with an
engine whose Setup Guide routes a fake answers (tests/test_node_agent.py SetupEngine). The engine a
box serves on :8000 is never addressed.

  1  reads: each node's own engine answers, bytes and type as they are; an unknown node 404, a
     node without a Setup Guide engine 503 as its agent says, an agent that is gone 502 or 503;
     the controller's machine read without its agent asks its own engine        test_reads
  2  a stream passes as it comes, through both hops, across a quiet gap longer than a second
     (the agent's old 1 s read timeout cut it), with Last-Event-ID; a reader that leaves ends the
     upstream                                                                    test_streams
  3  writes: 428 without the confirm (the engine never reached), the plan names the node, its
     deployments and where their requests go; a confirm for another choice, a spent or another
     node's is 409; the right one passes once and is an event; a dry run, estimate, unload and
     chat need none; a removal of a pack in use is blocked                       test_writes
  4  rights and origin: the `setup` rule (401 from the LAN with a password set), a POST from
     another origin or not JSON refused as the engine refuses it; plans and errors validate
                                                                                 test_rights

    python tests/test_fleet_setup.py
"""

from __future__ import annotations

import http.client
import json
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_fleet as tf  # noqa: E402
import test_node_agent as tna  # noqa: E402
from proxy_util import Proxy, run  # noqa: E402

ADMIN = tf.ADMIN
JSON = {"Content-Type": "application/json"}


class Node:
    """A virtual node: its agent on a loopback port, its engine's Setup Guide a fake."""

    def __init__(self, serial: str, name: str, engine=True):
        self.eng = tna.SetupEngine() if engine else None
        docs = [tna.engine_doc("engine", self.eng.port)] if engine else []
        self.agent = tna.agent_on(tna.machine(serial), engines=docs, address="127.0.0.1",
                                  virtual=name)
        self.run = tna.Running(self.agent)
        self.url = self.run.url

    def close(self):
        self.run.close()
        if self.eng is not None:
            self.eng.close()


class Fleet:
    def __init__(self, nodes: dict, env=None, login="off"):
        self.nodes = nodes
        self.px = Proxy(env=env, login=login).__enter__()
        self.f = tf.fleet_on(self.px, tf.local_node(tf.fake_root(), h=tf.host(gpu=False)))
        self.ids = {}
        for alias, n in nodes.items():
            nid = n.agent.node_id()
            self.px.svc.cfg.add_node({"id": nid, "alias": alias, "endpoint": n.url,
                                      "address": "127.0.0.1",
                                      "trust": {"kind": "loopback-secret",
                                                "file": n.agent.secret_path}})
            self.ids[alias] = nid

    def req(self, method, path, body=None, headers=None):
        h = dict(JSON) if body is not None else {}
        h.update(headers or {})
        st, hh, raw = self.px.request(method, path, body, headers=h)
        return st, hh, raw

    def close(self):
        for src in [s for _, s in self.f.agents.values()]:
            src.close()
        self.px.__exit__(None, None, None)
        for n in self.nodes.values():
            n.close()


def _j(raw: bytes):
    return json.loads(raw)


# ---------------------------------------------------------------------------- 1. reads

def test_reads():
    fx = Fleet({"Gold": Node("SET-G-0001", "g"), "R-1": Node("SET-1-0001", "r1"),
                "Bare": Node("SET-B-0001", "b", engine=False)})
    try:
        g, r1 = fx.ids["Gold"], fx.ids["R-1"]
        direct = tna.raw(fx.nodes["Gold"].eng.port, "GET", "/v1/setup/device")
        st, h, raw = fx.req("GET", f"/v1/fleet/nodes/{g}/setup/device")
        assert (st, h["Content-Type"], raw) == (direct[0], direct[1], direct[2]), (st, h, raw)
        assert h["Cache-Control"] == "no-store"
        # each node's own engine, the query passed on
        n1 = len(fx.nodes["R-1"].eng.seen)
        st, _, raw = fx.req("GET", f"/v1/fleet/nodes/{r1}/setup/weights?x=1")
        assert st == 200 and _j(raw)["active"] == "p-run"
        assert fx.nodes["R-1"].eng.seen[n1][1] == "/v1/setup/weights?x=1"
        assert all(p != "/v1/setup/weights?x=1" for _, p, *_ in fx.nodes["Gold"].eng.seen)
        # POST estimate (a read in a POST): the body as sent, no confirm needed
        sel = {"selection": {"model": "m", "profile": "p"}}
        st, _, raw = fx.req("POST", f"/v1/fleet/nodes/{g}/setup/estimate", sel)
        assert st == 200 and _j(raw) == {"got": sel}, raw
        # an unknown node, a node without a Setup Guide engine (its agent's own answer)
        st, _, raw = fx.req("GET", "/v1/fleet/nodes/nope/setup/device")
        assert st == 404 and tf.ok(_j(raw), "error")["error"]["type"] == "not_found"
        st, _, raw = fx.req("GET", f"/v1/fleet/nodes/{fx.ids['Bare']}/setup/device")
        assert st == 503 and _j(raw)["error"]["type"] == "no_engine", raw
        # an agent that is gone
        fx.nodes["R-1"].run.close()
        st, _, raw = fx.req("GET", f"/v1/fleet/nodes/{r1}/setup/device")
        assert st in (502, 503) and tf.ok(_j(raw), "error")["error"]["type"] == "unreachable"
    finally:
        fx.close()
    # the controller's machine read without its agent: its own engine's /v1/setup
    eng = tna.SetupEngine()
    with Proxy() as px:
        f = tf.fleet_on(px, tf.local_node(tf.fake_root()))
        px.svc.cfg.create_backend({"id": "box", "kind": "tandemllm",
                                   "url": f"http://127.0.0.1:{eng.port}/v1"})
        px.sync()
        nid = f.ensure_local()
        st, _, raw = px.get(f"/v1/fleet/nodes/{nid}/setup/device")
        assert st == 200 and _j(raw)["gpu"] == "GB10", raw
        assert eng.seen[-1][1] == "/v1/setup/device"
    eng.close()


# ---------------------------------------------------------------------------- 2. streams

def _stream(port, path, headers=None, keep=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", path, headers=dict({"Accept": "text/event-stream"}, **(headers or {})))
    r = c.getresponse()
    got, t0 = [], time.time()
    while True:
        line = r.readline()
        if not line:
            break
        if line.startswith(b"data:"):
            got.append((round(time.time() - t0, 1), json.loads(line[5:])))
            if keep is not None and len(got) >= keep:
                break
    c.close()
    return r.status, r.getheader("Content-Type"), got


def test_streams():
    fx = Fleet({"Gold": Node("SET-G-0002", "g")})
    try:
        g = fx.ids["Gold"]
        st, ctype, got = _stream(fx.px.port, f"/v1/fleet/nodes/{g}/setup/jobs/j1")
        assert st == 200 and ctype.startswith("text/event-stream")
        assert [d for _, d in got] == [{"step": 0}, {"step": 1}, {"step": 2}], got
        # quiet for 1.6 s between two events: both arrive, the second after the gap
        st, _, got = _stream(fx.px.port, f"/v1/fleet/nodes/{g}/setup/jobs/slow",
                             {"Last-Event-ID": "41"})
        assert [d for _, d in got] == [{"step": 0, "after": "41"}, {"step": 1}], got
        assert got[1][0] >= 1.4, got
        # the agent alone too (its relay was cut by a 1 s read timeout before)
        r = fx.nodes["Gold"].run
        evs = list(r.client.events("/node/setup/jobs/slow", timeout=10))
        assert [d["step"] for _, d in evs] == [0, 1], evs
        # Wren's answer, a POST stream
        c = http.client.HTTPConnection("127.0.0.1", fx.px.port, timeout=10)
        c.request("POST", f"/v1/fleet/nodes/{g}/setup/assistant/chat",
                  body=json.dumps({"messages": []}), headers=JSON)
        resp = c.getresponse()
        body = resp.read()
        c.close()
        assert resp.status == 200 and b'"done"' in body, body
        # a reader that leaves: the controller lets go of the upstream within a few seconds
        before = threading.active_count()
        st, _, got = _stream(fx.px.port, f"/v1/fleet/nodes/{g}/setup/jobs/slow", keep=1)
        assert [d["step"] for _, d in got] == [0]
        time.sleep(2.5)
        assert threading.active_count() <= before + 2, (before, threading.active_count())
    finally:
        fx.close()


# ---------------------------------------------------------------------------- 3. writes

def test_writes():
    fx = Fleet({"Gold": Node("SET-G-0003", "g"), "R-1": Node("SET-1-0003", "r1")})
    try:
        g, r1 = fx.ids["Gold"], fx.ids["R-1"]
        eng = fx.nodes["Gold"].eng
        apply = {"selection": {"model": "qwen3.8-27b", "profile": "exl3-4.0"}}
        n = len(eng.seen)
        st, _, raw = fx.req("POST", f"/v1/fleet/nodes/{g}/setup/apply", apply)
        assert st == 428 and tf.ok(_j(raw), "error")["error"]["type"] == "confirm_required"
        assert not [x for x in eng.seen[n:] if x[0] == "POST"]   # the engine never saw it
        # the plan: the restart, what Gold serves, the confirm
        st, _, raw = fx.req("GET", f"/v1/fleet/nodes/{g}/setup-confirm/apply"
                                   f"?model=qwen3.8-27b&profile=exl3-4.0")
        p = tf.ok(_j(raw), "setup-confirm")
        assert st == 200 and p["allowed"] and p["confirm"] and p["route"] == "apply", p
        assert p["title"] == "Apply the Setup Guide on Gold"
        assert any("restarts serving qwen3.8-27b with exl3-4.0" in x for x in p["impact"]), p
        # a confirm for another profile, another node, then spent
        other = {"selection": {"model": "qwen3.8-27b", "profile": "exl3-6.0"}}
        st, _, raw = fx.req("POST", f"/v1/fleet/nodes/{g}/setup/apply?confirm={p['confirm']}",
                            other)
        assert st == 409 and _j(raw)["error"]["type"] == "confirm_invalid"
        p = _j(fx.req("GET", f"/v1/fleet/nodes/{g}/setup-confirm/apply?model=qwen3.8-27b"
                             f"&profile=exl3-4.0")[2])
        st, _, raw = fx.req("POST", f"/v1/fleet/nodes/{r1}/setup/apply?confirm={p['confirm']}",
                            apply)
        assert st == 409, raw
        p = _j(fx.req("GET", f"/v1/fleet/nodes/{g}/setup-confirm/apply?model=qwen3.8-27b"
                             f"&profile=exl3-4.0")[2])
        st, _, raw = fx.req("POST", f"/v1/fleet/nodes/{g}/setup/apply?confirm={p['confirm']}",
                            apply)
        assert st == 202 and _j(raw)["job"]["id"] == "j1", raw
        m, path, headers, body = eng.seen[-1]
        assert (m, path, json.loads(body)) == ("POST", "/v1/setup/apply", apply)
        assert "confirm" not in path and "Authorization" not in headers
        st, _, raw = fx.req("POST", f"/v1/fleet/nodes/{g}/setup/apply?confirm={p['confirm']}",
                            apply)
        assert st == 409                                        # single use
        evs = tf.ok(_j(fx.req("GET", f"/v1/fleet/events?node={g}&type=engine")[2]), "events")
        assert evs["events"][0]["text"] == "Setup Guide on Gold: apply qwen3.8-27b/exl3-4.0"
        # reads in a POST and the assistant's own session need no confirm
        for route, body in (("apply", dict(apply, dry_run=True)), ("assistant/unload", {}),
                            ("estimate", apply)):
            st, _, raw = fx.req("POST", f"/v1/fleet/nodes/{g}/setup/{route}", body)
            assert st not in (428, 409), (route, st, raw)
        # waking Wren is a write: its plan says what it takes
        st, _, raw = fx.req("POST", f"/v1/fleet/nodes/{g}/setup/assistant/load", {})
        assert st == 428
        p = tf.ok(_j(fx.req("GET", f"/v1/fleet/nodes/{g}/setup-confirm/assistant/load")[2]),
                  "setup-confirm")
        assert p["title"] == "Wake Wren on Gold" and "memory" in p["impact"][0]
        st, _, raw = fx.req("POST", f"/v1/fleet/nodes/{g}/setup/assistant/load"
                                    f"?confirm={p['confirm']}", {})
        assert st == 200 and _j(raw)["state"] == "loading"
        # a removal: the pack the engine serves is blocked, another allowed and bound to it
        p = tf.ok(_j(fx.req("GET", f"/v1/fleet/nodes/{g}/setup-confirm/remove"
                                   f"?profiles=pack-4.0")[2]), "setup-confirm")
        assert not p["allowed"] and "serves pack-4.0 on Gold: stop it first" in p["blocked"], p
        assert p["confirm"] is None
        p = _j(fx.req("GET", f"/v1/fleet/nodes/{g}/setup-confirm/remove?profiles=p-old")[2])
        assert p["allowed"] and p["impact"] == ["p-old is removed from Gold's disk"], p
        st, _, raw = fx.req("POST", f"/v1/fleet/nodes/{g}/setup/remove?confirm={p['confirm']}",
                            {"profiles": ["p-run"], "confirm": True})
        assert st == 409                                        # the confirm named p-old
        p = _j(fx.req("GET", f"/v1/fleet/nodes/{g}/setup-confirm/remove?profiles=p-old")[2])
        st, _, raw = fx.req("POST", f"/v1/fleet/nodes/{g}/setup/remove?confirm={p['confirm']}",
                            {"profiles": ["p-old"], "confirm": True})
        assert st == 200 and _j(raw)["removed"] == ["p-old"], raw
        # no such write, and a node that does not answer has no plan to confirm
        st, _, raw = fx.req("GET", f"/v1/fleet/nodes/{g}/setup-confirm/estimate")
        assert st == 404 and tf.ok(_j(raw), "error")
        fx.px.svc.cfg.add_node({"id": "n-off", "alias": "Off", "endpoint": "", "address": ""})
        p = tf.ok(_j(fx.req("GET", "/v1/fleet/nodes/n-off/setup-confirm/reset?level=2")[2]),
                  "setup-confirm")
        assert not p["allowed"] and p["blocked"].startswith("Off does not answer"), p
        assert p["confirm"] is None and p["title"] == "Reset level 2 on Off"
        st, _, raw = fx.req("GET", "/v1/fleet/nodes/n-off/setup/device")
        assert st == 503 and tf.ok(_j(raw), "error")["error"]["type"] == "unreachable"
    finally:
        fx.close()


# ---------------------------------------------------------------------------- 4. rights

def test_rights():
    lan = {"X-Real-IP": "192.0.2.77"}
    bearer = {"Authorization": f"Bearer {ADMIN}"}
    fx = Fleet({"Gold": Node("SET-G-0004", "g")}, env={"TANDEM_ADMIN_TOKEN": ADMIN}, login="on")
    try:
        g = fx.ids["Gold"]
        for path in (f"/v1/fleet/nodes/{g}/setup/device",
                     f"/v1/fleet/nodes/{g}/setup-confirm/apply?model=m&profile=p"):
            st, _, raw = fx.req("GET", path, headers=lan)
            assert st == 401, (path, raw)
            assert fx.req("GET", path, headers={**lan, **bearer})[0] == 200, path
        n = len(fx.nodes["Gold"].eng.seen)
        st, _, raw = fx.req("POST", f"/v1/fleet/nodes/{g}/setup/estimate", {"selection": {}},
                            {"Origin": "http://evil.example", **bearer})
        assert st == 403 and _j(raw)["error"]["code"] == "cross_origin", raw
        st, _, raw = fx.req("POST", f"/v1/fleet/nodes/{g}/setup/estimate", b"{}",
                            {"Content-Type": "text/plain", **bearer})
        assert st == 415 and _j(raw)["error"]["type"] == "json_only", raw
        st, _, raw = fx.req("POST", f"/v1/fleet/nodes/{g}/setup/estimate", {"selection": {}},
                            {"Origin": f"http://127.0.0.1:{fx.px.port}",
                             "Host": f"127.0.0.1:{fx.px.port}", **bearer})
        assert st == 200, raw
        posts = [x for x in fx.nodes["Gold"].eng.seen[n:] if x[0] == "POST"]
        assert len(posts) == 1, posts                           # only the same-origin one
        st, _, raw = fx.req("DELETE", f"/v1/fleet/nodes/{g}/setup/device", headers=bearer)
        assert st == 405
    finally:
        fx.close()


if __name__ == "__main__":
    run(globals())
