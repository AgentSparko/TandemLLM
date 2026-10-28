"""SRV-730: the fleet store, the event log and the alert rules on TandemApp's controller (SRV-729).

Every answer is held to docs/contract/fleet-v1 (tools/contract_check.py). The machine and its
engine are test_fleet.py's fakes; time is injected and nothing sleeps.

  1  the tiers: 5m from 2 s slots, 1h/6h/24h from 30 s, 7d from 30 s for its newest 26 h and from
     the 10-minute slots before; every sample against the raw samples; the 7d answer fast
                                                            test_tiers_and_ranges
  2  history, TTFT included, survives a clean restart; a crash loses what was not flushed and
     says so                                                test_restart_keeps_history
  3  retention: the tiers and the events pruned, at least 7 days         test_retention_prunes
  4  TTFT per slot is the median of the requests, not a mean           test_ttft_p50
  5  events: controller start and stop, a node off, draining and up again, a new address,
     paired, taken over and released, an engine down, back, restarted, a deployment's status
                                                            test_events_from_each_source
  6  rules: GPU temperature through the sampler with debounce and hysteresis (the Gherkin's
     thermal alert), the other rules on readings                test_temperature_rule,
                                                                test_rules_on_readings
  7  an acknowledgement is kept across a restart; a new firing is a new alert
                                                                test_ack_persists
  8  rule edits: the shape, refused bodies, kept across a restart, and who may write
                                                                test_rule_edits_and_auth
  9  logs: info and up by default, the HTTP access lines tagged and left out unless asked
                                                                test_logs_default_and_access

    python tests/test_fleet_store.py
"""

from __future__ import annotations

import bisect
import math
import os
import sqlite3
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from proxy_util import Proxy, run  # noqa: E402
from proxy import app as proxy_app  # noqa: E402
from proxy import fleet as fleet_mod  # noqa: E402
from proxy import fleetstore, fleetwatch, nodetrust  # noqa: E402
from test_fleet import (ADMIN, Clock, Engine, FakeBuf, HOSTNAME, T0, engine_env,  # noqa: E402
                        fake_root, fleet_on, get, host, local_node, ok, send)

DAY = 86400


def store_at(d, clock, **kw):
    return fleetstore.Store(os.path.join(d, "fleet.sqlite3"), clock=clock, **kw).open()


def ticks(f, clock, eng=None, n=1, step=2.0, tokens=0):
    """`n` samples `step` apart while a dashboard reads the fleet (the engine read at each)."""
    for _ in range(n):
        clock.t += step
        if eng is not None:
            eng.step(clock.t, tokens)
        f._read_at = clock.t
        f.tick()


def metric(px, q):
    m = ok(get(px, "/v1/fleet/metrics?" + q)[1], "metrics")
    return m, m["series"][0]["values"]


def events(px, q=""):
    return ok(get(px, "/v1/fleet/events" + (f"?{q}" if q else ""))[1], "events")["events"]


# ---------------------------------------------------------------------------- 1 the tiers

def block(t):
    """A power figure that is constant over each 10-minute slot ((b-1)·600, b·600]."""
    return 100.0 + 10.0 * (math.ceil(t / 600) % 7)


def test_tiers_and_ranges():
    for now in (T0 - (T0 % 3600) + 1, T0 - (T0 % 3600) + 901):    # on the hour, a quarter past
        clock = Clock(now)
        with Proxy() as px:
            f = fleet_on(px, local_node(fake_root(), clock=clock), clock=clock)
            lid = f.ensure_local()
            times, vals_raw = [], []
            ring = f.store.ring(("node", lid, "power"))
            t = now - 8 * DAY
            while t <= now:
                times.append(t)
                vals_raw.append(block(t))
                ring.add(t, block(t))
                t += 2 if now - t <= 1200 else 30

            def raw_mean(lo, hi):
                a, b = bisect.bisect_right(times, lo), bisect.bisect_right(times, hi)
                return round(sum(vals_raw[a:b]) / (b - a), 3) if b > a else None
            for rng, (span, count) in fleet_mod.RANGES.items():
                m, vals = metric(px, f"metric=power&range={rng}")
                step, t0, _ = fleet_mod.window(rng, now)
                assert (m["step_s"], m["t0"], len(vals)) == (step, t0, count)
                for i, v in enumerate(vals):
                    ti = t0 + i * step
                    if step == 2 or (ti % 30 == 0 and ti - step >= now - 26 * 3600):
                        lo, hi = ti - step, ti                 # whole slots of a fine tier
                    else:                                       # the 10-min slots ending in it
                        hi = math.floor(ti / 600) * 600
                        lo = hi - step
                    assert v == raw_mean(lo, hi), (rng, i, ti, v, raw_mean(lo, hi))
                assert None not in vals, rng
            # the Gherkin's long range: the week answers fast
            t1 = time.perf_counter()
            get(px, "/v1/fleet/metrics?metric=power&range=7d")
            took = time.perf_counter() - t1
            assert took < 0.3, f"7d answered in {took * 1000:.0f} ms"
            # nothing before the history began
            f.store.rings.clear()
            ring = f.store.ring(("node", lid, "power"))
            ring.add(now - 3 * 3600, 50.0)
            _, day = metric(px, "metric=power&range=24h")
            assert [v for v in day if v is not None] == [50.0]


# ---------------------------------------------------------------------------- 2 restart

def test_restart_keeps_history():
    d = tempfile.mkdtemp(prefix="fleet-store-")
    clock = Clock()
    eng = Engine(clock)
    try:
        with Proxy(env=engine_env(eng)) as px:
            px.sync()
            node = local_node(fake_root(), clock=clock)
            f = fleet_on(px, node, clock=clock, store=store_at(d, clock))
            get(px, "/v1/fleet/nodes")                       # a dashboard reads: every tick
            for i in range(150):
                if i == 20:
                    eng.ended += [(clock.t + 0.5, ms) for ms in (210.0, 230.0, 990.0)]
                ticks(f, clock, eng, tokens=100)
            clock.t += 1
            before = {(m, r): metric(px, f"metric={m}&range={r}")[1]
                      for m in ("tps", "ttft", "mem", "power") for r in ("5m", "1h", "24h")}
            assert [v for v in before[("ttft", "1h")] if v is not None] == [230.0]
            assert before[("tps", "5m")][-1] == 50.0
            f.stop()                                         # a clean stop writes the 2 s tier
            st2 = store_at(d, clock)
            assert not st2.crashed and st2.last_stop == clock.t
            f2 = fleet_on(px, node, clock=clock, store=st2)
            for k, vals in before.items():
                assert metric(px, f"metric={k[0]}&range={k[1]}")[1] == vals, k
            f2.start()
            f2.stop()
            # a crash: what the last flush wrote survives, the rest is gone, the start says so
            st3 = store_at(d, clock)
            f3 = fleet_on(px, node, clock=clock, store=st3)
            ev = events(px, "type=controller")
            assert [e["text"].split(" (")[0] for e in ev] == [
                "TandemApp's controller stopped", "TandemApp's controller started"]
            assert ev[1]["node"] == "host" and "stopped for" in ev[1]["detail"]
            ticks(f3, clock, eng, n=200, tokens=100)        # 400 s: a flush at 300 s ...
            flushed = clock.t
            f3.store.flush(flushed)                          # ... and one now
            ticks(f3, clock, eng, n=30, tokens=100)          # 60 s more, never written
            clock.t += 1
            m, now_1h = metric(px, "metric=tps&range=1h")
            st4 = store_at(d, clock)                         # st3 never closed
            assert st4.crashed
            f4 = fleet_on(px, node, clock=clock, store=st4)
            after = metric(px, "metric=tps&range=1h")[1]
            for i, v in enumerate(after):
                ti = m["t0"] + i * 30
                if ti <= flushed:
                    assert v == now_1h[i], (i, v, now_1h[i])
                elif ti - 30 >= flushed:
                    assert v is None and now_1h[i] is not None, (i, v)
            assert metric(px, "metric=tps&range=5m")[1][-1] is None
            f4.start()
            assert "did not stop cleanly" in events(px, "type=controller")[0]["detail"]
            f4.stop()
    finally:
        eng.close()


# ---------------------------------------------------------------------------- 3 retention

def test_retention_prunes():
    d = tempfile.mkdtemp(prefix="fleet-store-")
    clock = Clock()
    st = store_at(d, clock, retention_days=7)
    ring = st.ring(("node", "n-1", "temp"))
    for age in (10 * DAY, 6 * DAY, 2 * DAY, 3600, 60):
        ring.add(T0 - age, 50.0)
    st.add_event("node", "n-1", "old", at=T0 - 8 * DAY)
    st.add_event("node", "n-1", "new", at=T0 - DAY)
    st.flush(T0)
    db = sqlite3.connect(os.path.join(d, "fleet.sqlite3"))

    def rows(tier):
        return db.execute("SELECT count(*) FROM slots WHERE tier = ?", (tier,)).fetchone()[0]
    assert (rows(2), rows(30), rows(600)) == (0, 2, 4), (rows(30), rows(600))
    assert [e["text"] for e in st.events()] == ["new"]
    st.close()
    assert rows(2) == 1                                   # the clean stop: the newest minutes
    st2 = store_at(d, Clock(T0 + 2 * DAY))                # two days on: the 30 s tier is gone
    r = st2.rings[("node", "n-1", "temp")]
    assert r.live(2, 0) == [] and r.live(30, 0) == [] and len(r.live(600, 0)) == 4
    st2.close()
    for bad in (6, 0):
        try:
            fleetstore.Store(":memory:", retention_days=bad)
        except ValueError:
            continue
        raise AssertionError(f"retention {bad} accepted")
    import argparse
    args = argparse.Namespace(fleet_store=os.path.join(d, "x.sqlite3"), fleet_retention_days=3)
    try:
        proxy_app.fleet_store(args, None)
    except SystemExit as exc:
        assert "at least 7" in str(exc)
    else:
        raise AssertionError("--fleet-retention-days 3 accepted")
    # beside the config database by default
    with Proxy() as px:
        args = argparse.Namespace(fleet_store="beside", fleet_retention_days=30)
        st3 = proxy_app.fleet_store(args, px.svc.cfg)
        assert os.path.realpath(st3.path) == os.path.realpath(os.path.join(px.dir,
                                                                          "fleet.sqlite3"))
        assert oct(os.stat(st3.path).st_mode & 0o777) == "0o600"
        st3.close()


# ---------------------------------------------------------------------------- 4 TTFT p50

def test_ttft_p50():
    clock = Clock()
    eng = Engine(clock)
    try:
        with Proxy(env=engine_env(eng)) as px:
            px.sync()
            f = fleet_on(px, local_node(fake_root(), clock=clock), clock=clock)
            get(px, "/v1/fleet/nodes")
            ticks(f, clock, eng, tokens=10)
            t = clock.t
            # five requests end in one 2 s slot: the mean would say 1,863 ms
            eng.ended += [(t + 0.5, ms) for ms in (100.0, 105.0, 110.0, 4000.0, 5000.0)]
            ticks(f, clock, eng, n=20, tokens=10)
            clock.t += 1
            for rng in ("5m", "1h", "24h", "7d"):
                assert [v for v in metric(px, f"metric=ttft&range={rng}")[1]
                        if v is not None] == [110.0], rng
            # four more in the next minute: the 1h sample of each 30 s slot is its own median
            eng.ended += [(clock.t + 1, 300.0), (clock.t + 1, 500.0)]
            ticks(f, clock, eng, n=20, tokens=10)
            clock.t += 1
            hour = [v for v in metric(px, "metric=ttft&range=1h")[1] if v is not None]
            assert hour == [110.0, 400.0], hour
            week = [v for v in metric(px, "metric=ttft&range=7d")[1] if v is not None]
            assert week == [300.0], week                  # 100 105 110 300 500 4000 5000
        # past 64 distinct values a slot keeps buckets 2^(1/16) apart: the median within 2.2 %
        r = fleetstore.Ring(hist=True)
        vals = [50.0 + 7.3 * i for i in range(301)]
        for v in vals:
            r.add(T0, v)
        k = math.ceil(T0 / 30)
        got = fleetstore.p50(r.hist(30, k, k, {}))
        exact = sorted(vals)[150]
        assert abs(got - exact) / exact < 0.022 and len(r.hists[30][k]) < 100, (got, exact)
    finally:
        eng.close()


# ---------------------------------------------------------------------------- 5 events

def test_events_from_each_source():
    clock = Clock()
    eng = Engine(clock)
    drain = {"on": False}
    try:
        with Proxy(env=engine_env(eng)) as px:
            px.sync()
            node = local_node(fake_root(), clock=clock, draining=lambda: drain["on"])
            f = fleet_on(px, node, clock=clock)
            lid = f.ensure_local()
            f.start()                                    # the controller's start
            f._stop.set()
            f._thread.join()
            f._thread = None
            for _ in range(3):
                ticks(f, clock, eng, tokens=50)
            assert [e["type"] for e in events(px)] == ["controller"]
            # draining, then up again: each held 10 s before it is said
            drain["on"] = True
            ticks(f, clock, eng, n=4, tokens=50)
            assert len(events(px, "type=node")) == 0
            ticks(f, clock, eng, n=2, tokens=50)
            drain["on"] = False
            ticks(f, clock, eng, n=6, tokens=50)
            assert [e["text"] for e in events(px, f"type=node&node={lid}")] == [
                f"{HOSTNAME} is up again", f"{HOSTNAME} is draining"]
            # a node record without an agent: off, said once it held
            px.svc.cfg.add_node({"id": "n-r1", "alias": "R-1", "endpoint": "",
                                 "address": "192.0.2.41", "trust": {}})
            ticks(f, clock, eng, n=6, tokens=50)
            off = events(px, "node=n-r1")
            assert [(e["text"], e["severity"]) for e in off] == [("R-1 is off", "err")]
            assert "not paired" in off[0]["detail"]
            # a new address of this machine
            node.state["address"] = "192.0.2.52"
            f.ensure_local()
            assert events(px, "type=node&limit=1")[0]["text"] == f"{HOSTNAME} has a new address"
            # the engine: down, back within two minutes (a restart), its counter reset
            px.svc.health.state["eng"] = {"state": "down", "last_error": "connection refused",
                                          "since": clock.t}
            ticks(f, clock, eng, n=6, tokens=50)
            px.svc.health.state["eng"] = {"state": "up", "since": clock.t}
            ticks(f, clock, eng, n=6, tokens=50)
            eng.gen = 0
            ticks(f, clock, eng, n=2, tokens=50)
            got = [(e["text"], e["severity"]) for e in events(px, "type=engine")]
            assert got == [("Engine on " + HOSTNAME + " restarted", None),
                           ("Engine on " + HOSTNAME + " restarted", None),
                           ("Engine on " + HOSTNAME + " went down", "err")], got
            assert events(px, "type=engine")[1]["detail"] == "down for 12 s"
            assert "no stop was asked" in events(px, "type=engine")[2]["detail"]
            # the deployment followed: down, then serving
            dep = [(e["text"], e["deployment"]) for e in events(px, "type=deploy")]
            assert dep == [(f"Test Model is serving on {HOSTNAME}", "TandemLLM"),
                           (f"Test Model is down on {HOSTNAME}", "TandemLLM")], dep
            # pairing, take-over and release (SEC-4's record and release, the network faked)
            t = f.trust()
            d = tempfile.mkdtemp(prefix="node-id-")
            pem = nodetrust.make_identity(os.path.join(d, "n.crt"), os.path.join(d, "n.key"),
                                          "spark-2")
            t._identity = lambda endpoint, trust: {"id": "n-s2", "hostname": "spark-2"}
            t._unpin_remote = lambda endpoint, trust: "unpinned"
            t._record(node_pem=pem, address="192.0.2.42", port=7700, node_id=None, via="code",
                      who="admin")
            t._record(node_pem=pem, address="192.0.2.42", port=7700, node_id=None, via="ssh",
                      who="admin", took={"name": "TandemApp on mac"})
            t.release("n-s2", False, "admin")
            assert [e["text"] for e in events(px, "node=n-s2")] == [
                "spark-2 released", "spark-2 taken over by ssh", "spark-2 paired by code"]
            # filters: type, node, paging, newest first
            every = events(px, "limit=1000")
            assert [e["at"] for e in every] == sorted((e["at"] for e in every), reverse=True)
            assert {e["type"] for e in every} == {"controller", "node", "engine", "deploy",
                                                  "alert"}           # R-1 off for 30 s
            assert events(px, "type=node,engine") == [e for e in every
                                                      if e["type"] in ("node", "engine")]
            assert events(px, f"limit=3&before={every[2]['at']}") == \
                [e for e in every if e["at"] < every[2]["at"]][:3]
            since = every[4]["at"]
            assert events(px, f"since={since}") == [e for e in every if e["at"] > since]
            for q in ("type=gossip", "since=yesterday", "before=x", "limit=0", "limit=1001"):
                st, err = get(px, f"/v1/fleet/events?{q}")
                assert st == 400 and ok(err, "error"), q
    finally:
        eng.close()


# ---------------------------------------------------------------------------- 6 rules

def set_temp(node, c):
    node.state["host"]["gpus"][0]["temperature_c"] = c


def test_temperature_rule():
    """The Gherkin's thermal alert: the GPU at 86 °C for 60 s opens "<node> GPU 86 °C" and logs
    an event; it holds above 80 °C and resolves after 60 s below."""
    clock = Clock()
    with Proxy() as px:
        node = local_node(fake_root(), h=host(), clock=clock)
        f = fleet_on(px, node, clock=clock)
        lid = f.ensure_local()

        def alerts():
            return ok(get(px, "/v1/fleet/alerts")[1], "alerts")["alerts"]
        set_temp(node, 86)
        ticks(f, clock, n=30)                             # held 58 s: not yet
        assert alerts() == []
        ticks(f, clock, n=1)
        a, = alerts()
        assert a == {"id": f"gpu-temp:{lid}", "severity": "err", "node": lid,
                     "title": f"{HOSTNAME} GPU 86 °C", "since": clock.t,
                     "rule": "GPU temperature ≥ 85 °C for 60 s", "detail": a["detail"],
                     "action": {"label": f"Open {HOSTNAME} in Hardware",
                                "route": f"#/hardware?scope=node:{lid}"},
                     "acked": False, "acked_at": None}, a
        fired = events(px, "type=alert")
        assert [(e["text"], e["severity"], e["node"]) for e in fired] == \
            [(f"{HOSTNAME} GPU 86 °C", "err", lid)]
        set_temp(node, 83)                                 # under the threshold, above 80
        ticks(f, clock, n=60)
        assert len(alerts()) == 1
        set_temp(node, 79)
        ticks(f, clock, n=20)                              # 40 s below, then back over 80
        set_temp(node, 82)
        ticks(f, clock, n=2)
        set_temp(node, 79)
        ticks(f, clock, n=30)
        assert len(alerts()) == 1, "the clear hold starts again"
        ticks(f, clock, n=1)
        assert alerts() == []
        res = events(px, "type=alert&limit=1")[0]
        assert (res["text"], res["severity"], res["detail"]) == \
            (f"Resolved: {HOSTNAME} GPU 86 °C", "resolved", "GPU at 79 °C")
        # a spike shorter than the debounce never fires
        set_temp(node, 95)
        ticks(f, clock, n=25)
        set_temp(node, 60)
        ticks(f, clock, n=40)
        assert alerts() == [] and len(events(px, "type=alert")) == 2


def readings(**kw):
    n = {"id": "n-1", "alias": "Desk", "status": "up", "status_text": "up", "temp": 50.0,
         "throttle": [], "mem_avail": 40.0, "disk": (40.0, "/"), "engines": [
             {"id": "eng", "label": "Engine", "state": "up", "error": None, "restarts": 0,
              "job": None}]}
    n.update(kw)
    return {"nodes": [n], "host": {"may_sleep": False}, "deployments": None}


def test_rules_on_readings():
    clock = Clock()
    st = fleetstore.Store(clock=clock).open()
    w = fleetwatch.Watch(st, clock=clock, log=lambda m: None)
    ids = lambda: sorted(a["id"] for a in w.alerts())             # noqa: E731

    def run_for(seconds, **kw):
        for _ in range(int(seconds // 2)):
            clock.t += 2
            w.observe(clock.t, readings(**kw))
    run_for(10)
    assert ids() == []
    # throttle: only heat and power count, after 30 s; clears after 60 s without
    run_for(60, throttle=["gpu_idle", "applications_clocks", "sw_power_cap"])
    assert ids() == []
    run_for(30, throttle=["sw_thermal"])                    # held 28 s
    assert ids() == []
    run_for(2, throttle=["sw_thermal", "hw_slowdown"])
    assert ids() == ["throttle:n-1"]
    assert w.alerts()[0]["title"] == "Desk GPU throttled: sw thermal, hw slowdown"
    run_for(60)                                             # 58 s without
    assert ids() == ["throttle:n-1"]
    run_for(2)
    assert ids() == []
    # MemAvailable: < 10 GiB for 30 s, clears above 12 GiB for 30 s
    run_for(32, mem_avail=9.5)
    assert ids() == ["mem-avail:n-1"]
    run_for(60, mem_avail=11.0)
    assert ids() == ["mem-avail:n-1"]
    run_for(30, mem_avail=12.5)
    assert ids() == ["mem-avail:n-1"]
    run_for(2, mem_avail=12.5)
    assert ids() == []
    # disk: the fullest mount under 10 % for 5 minutes
    run_for(300, disk=(8.0, "/data"))
    assert ids() == []
    run_for(2, disk=(8.0, "/data"))
    assert ids() == ["disk-free:n-1"] and "/data 8 % free" in w.alerts()[0]["title"]
    run_for(302, disk=(13.0, "/data"))
    assert ids() == []
    # node down 30 s, its unknown figures hold its other alerts; up again for 10 s clears
    run_for(32, mem_avail=5.0)
    run_for(32, status="off", status_text="the node agent does not answer", temp=None,
            mem_avail=None, throttle=None, disk=None)
    assert ids() == ["mem-avail:n-1", "node-down:n-1"]
    a = next(x for x in w.alerts() if x["id"] == "node-down:n-1")
    assert (a["title"], a["detail"], a["action"]["route"]) == \
        ("Desk not reachable", "the node agent does not answer", "#/sparks/n-1")
    run_for(12)
    assert ids() == ["mem-avail:n-1"]
    run_for(30)
    assert ids() == []
    # engine down 60 s, cleared by one up check
    eng_down = [{"id": "eng", "label": "Engine", "state": "down", "error": "refused",
                 "restarts": 0, "job": None}]
    run_for(62, engines=eng_down)
    assert ids() == ["engine-down:eng"] and w.alerts()[0]["title"] == "Engine on Desk is down"
    run_for(2)
    assert ids() == []
    # host sleep: at once, for the host
    clock.t += 2
    w.observe(clock.t, dict(readings(), host={"may_sleep": True}))
    assert ids() == ["host-sleep:host"] and w.alerts()[0]["node"] == "host"
    # a rule turned off resolves its alert; a scope on another node too
    w.edit("host-sleep", {"enabled": False}, "admin", ["n-1"])
    clock.t += 2
    w.observe(clock.t, readings())
    assert ids() == []
    run_for(32, mem_avail=5.0)
    assert ids() == ["mem-avail:n-1"]
    w.edit("mem-avail", {"scope": "node:n-2"}, "admin", ["n-1", "n-2"])
    run_for(2, mem_avail=5.0)
    assert ids() == []
    kinds = [(e["text"], e["severity"]) for e in st.events(types=["alert"], limit=1000)]
    assert ("Resolved: Desk memory low: 5.0 GiB available", "resolved") in kinds
    for e in st.events(limit=1000):
        ok({"contract_version": "1.0", "events": [e]}, "events")
    # a notify target besides the app plugs in; it is called once per firing
    got = []
    w.notifiers["system"] = lambda ev, alert: got.append((ev["text"], alert["id"]))
    run_for(62, temp=90.0)
    assert got == [("Desk GPU 90 °C", "gpu-temp:n-1")]


# ---------------------------------------------------------------------------- 7 ack

def test_ack_persists():
    d = tempfile.mkdtemp(prefix="fleet-store-")
    clock = Clock()
    with Proxy() as px:
        node = local_node(fake_root(), h=host(), clock=clock)
        f = fleet_on(px, node, clock=clock, store=store_at(d, clock))
        lid = f.ensure_local()
        set_temp(node, 90)
        ticks(f, clock, n=31)
        aid = f"gpu-temp:{lid}"
        clock.t += 5
        st, a = send(px, "POST", f"/v1/fleet/alerts/{aid}/ack", None)
        assert st == 200 and ok(a, "alert")["acked"] and a["acked_at"] == clock.t
        at = clock.t
        clock.t += 5
        assert send(px, "POST", f"/v1/fleet/alerts/{aid}/ack", None)[1]["acked_at"] == at
        ack = events(px, "type=alert&limit=1")[0]
        assert ack["text"] == f"Acknowledged: {HOSTNAME} GPU 90 °C" and ack["severity"] is None
        f.stop()
        f2 = fleet_on(px, node, clock=clock, store=store_at(d, clock))
        a2, = ok(get(px, "/v1/fleet/alerts")[1], "alerts")["alerts"]
        assert (a2["id"], a2["acked"], a2["acked_at"]) == (aid, True, at)
        # it resolves; firing again is a new alert, not acknowledged
        set_temp(node, 60)
        ticks(f2, clock, n=31)
        assert get(px, "/v1/fleet/alerts")[1]["alerts"] == []
        st, err = send(px, "POST", f"/v1/fleet/alerts/{aid}/ack", None)
        assert st == 404 and ok(err, "error")
        set_temp(node, 90)
        ticks(f2, clock, n=31)
        a3, = get(px, "/v1/fleet/alerts")[1]["alerts"]
        assert a3["id"] == aid and not a3["acked"] and a3["since"] > at


# ---------------------------------------------------------------------------- 8 rule edits

def test_rule_edits_and_auth():
    d = tempfile.mkdtemp(prefix="fleet-store-")
    clock = Clock()
    with Proxy() as px:
        f = fleet_on(px, local_node(fake_root(), clock=clock), clock=clock,
                     store=store_at(d, clock))
        lid = f.ensure_local()
        rules = ok(get(px, "/v1/fleet/rules")[1], "rules")["rules"]
        temp = next(r for r in rules if r["id"] == "gpu-temp")
        assert temp == {"id": "gpu-temp", "name": "GPU temperature", "metric": "temp",
                        "condition": "≥ 85 °C for 60 s", "scope": "all",
                        "notify": ["app", "system"], "enabled": True, "threshold": 85.0,
                        "unit": "°C", "for_s": 60, "severity": "err"}
        st, r = send(px, "PATCH", "/v1/fleet/rules/gpu-temp",
                     {"threshold": 40, "for_s": 10, "notify": ["app", "app"],
                      "scope": f"node:{lid}"})
        assert st == 200 and ok(r, "rule")["condition"] == "≥ 40 °C for 10 s", r
        assert (r["notify"], r["scope"]) == (["app"], f"node:{lid}")
        assert send(px, "PATCH", "/v1/fleet/rules/disk-free", {"enabled": False})[1][
            "enabled"] is False
        for rid, body, status in (("nope", {"enabled": True}, 404),
                                  ("gpu-temp", {}, 400), ("gpu-temp", [], 400),
                                  ("gpu-temp", {"metric": "power"}, 400),
                                  ("gpu-temp", {"enabled": "yes"}, 400),
                                  ("gpu-temp", {"notify": ["mail"]}, 400),
                                  ("gpu-temp", {"threshold": "40"}, 400),
                                  ("gpu-temp", {"threshold": True}, 400),
                                  ("gpu-temp", {"for_s": -1}, 400),
                                  ("gpu-temp", {"for_s": 86401}, 400),
                                  ("gpu-temp", {"scope": "node:n-nope"}, 400),
                                  ("node-down", {"threshold": 10}, 400),
                                  ("host-sleep", {"scope": "all"}, 400)):
            st, err = send(px, "PATCH", f"/v1/fleet/rules/{rid}", body)
            assert st == status and ok(err, "error"), (rid, body, st, err)
        f.stop()
        fleet_on(px, local_node(fake_root(), clock=clock), clock=clock,
                 store=store_at(d, clock))
        again = {r["id"]: r for r in get(px, "/v1/fleet/rules")[1]["rules"]}
        assert again["gpu-temp"] == r and again["disk-free"]["enabled"] is False
    lan = {"X-Real-IP": "192.0.2.77"}
    bearer = {"Authorization": f"Bearer {ADMIN}"}
    for login in ("on", "off"):
        with Proxy(env={"TANDEM_ADMIN_TOKEN": ADMIN}, login=login) as px:
            fleet_on(px, local_node(fake_root()))
            body = {"enabled": False}
            st, err = send(px, "PATCH", "/v1/fleet/rules/throttle", body, lan)
            assert st == 401 and ok(err, "error"), login
            assert send(px, "PATCH", "/v1/fleet/rules/throttle", body, {**lan, **bearer})[0] \
                == 200
            assert get(px, "/v1/fleet/rules", lan)[0] == (401 if login == "on" else 200)
            assert send(px, "POST", "/v1/fleet/alerts/x/ack", None, lan)[0] == 401
    with Proxy() as px:                                    # no password: open, as /v1/config
        fleet_on(px, local_node(fake_root()))
        assert send(px, "PATCH", "/v1/fleet/rules/throttle", {"enabled": False}, lan)[0] == 200


# ---------------------------------------------------------------------------- 9 logs

def test_logs_default_and_access():
    eng = Engine()
    eng.log_lines = lambda: [
        {"seq": 1, "ts": "2027-01-15T08:00:01.000Z", "level": "debug", "source": "http",
         "msg": '127.0.0.1 - "GET /v1/dashboard/live?follow=0 HTTP/1.1" 200 -'},
        {"seq": 2, "ts": "2027-01-15T08:00:02.000Z", "level": "debug", "source": "drafter",
         "msg": "[drafter] accepted 3 of 4"},
        {"seq": 3, "ts": "2027-01-15T08:00:03.000Z", "level": "info", "source": "req",
         "msg": "[req] done 120 tok"}] + [
        {"seq": 4 + i, "ts": f"2027-01-15T08:00:03.{5 + i}00Z", "level": "debug", "source": "http",
         "msg": '127.0.0.1 - "GET /health HTTP/1.1" 200 -'} for i in range(2)]
    try:
        with Proxy(env=engine_env(eng)) as px:
            px.sync()
            buf = FakeBuf([{"seq": 1, "ts": "2027-01-15T08:00:04.000Z", "level": "debug",
                            "source": "http", "msg": '192.0.2.9 - "GET /v1/fleet/nodes" 200'},
                           {"seq": 2, "ts": "2027-01-15T08:00:05.000Z", "level": "info",
                            "source": "proxy", "msg": "[proxy] backend eng is up"}])
            fleet_on(px, local_node(fake_root(), logbuf=buf))

            def lines(q):
                got = ok(get(px, "/v1/fleet/logs?node=all" + q)[1], "logs")["lines"]
                return [(x["source"], x["level"], x["msg"][:14]) for x in got]
            assert lines("") == [("engine", "info", "[req] done 120"),
                                 ("gateway", "info", "[proxy] backen")]
            assert lines("&level=debug") == [("engine", "debug", "[drafter] acce"),
                                             ("engine", "info", "[req] done 120"),
                                             ("gateway", "info", "[proxy] backen")]
            # the engine's newest two are access lines: the two before them still come
            assert lines("&level=debug&limit=2") == [("engine", "info", "[req] done 120"),
                                                     ("gateway", "info", "[proxy] backen")]
            assert lines("&level=debug&access=1") == [
                ("access", "debug", "127.0.0.1 - \"G"), ("engine", "debug", "[drafter] acce"),
                ("engine", "info", "[req] done 120"), ("access", "debug", "127.0.0.1 - \"G"),
                ("access", "debug", "127.0.0.1 - \"G"), ("access", "debug", "192.0.2.9 - \"G"),
                ("gateway", "info", "[proxy] backen")]
    finally:
        eng.close()


if __name__ == "__main__":
    run(globals())
