"""SRV-818: a Split in the app (SRV-817): the planner's cut and link, the node agent's stage and
key, the deployment's start order, checks, routing, degraded state, recovery and stop.

The engines are fake (tests/fake_backend.py): the estimate's `split` block is the contract's own
example (contracts/engine-api-v1/setup/split.example.json, Flash-Next 2.05 from a box, cuts 12 and
24); a stage is a fake engine process its node's agent starts with the stage keys in its
environment, the two joined by a real TCP link with a keyed handshake. No model, no GPU; the engine
a box serves on :8000 is never addressed.

  1  the rule: the cut whose smaller headroom is largest among the cuts where both sides fit, each
     side against its own node's budget, with the slot margin; two unequal nodes pick another
     cut; no cut fits says which side lacks how much; a block that says not splittable is the
     engine's sentence; one asked cut                                       test_plan_rule
  2  the link: a fabric pair first, a LAN pair with the warning (not signed, not encrypted), no
     pair refused, addresses by hand as IP literals only (no name, no wildcard, loopback only on
     one machine)                                                           test_link_rule
  3  the agent: PUT /node/engines/{id}/stage writes the key to a 0600 file and only its path
     into the env block or the start's environment; the view, the log and every error are
     without the key; DELETE removes all; a stage survives an agent restart; an engine's 503
     keeps its Retry-After through the agent's relay                        test_agent_stage
  4  the planner over HTTP: `split` sent beside the selection to engines on API 1.2 only; an
     engine on 1.1 and a model that is not splittable are plan-level answers with the reason;
     a model one node holds is not split unless asked                        test_placement_api
  5  the deployment: the plan recorded, the front started first and then the tail, both checked
     and linked, requests on the tail only, the front never listed and never asked; the key in
     no answer, log, job, event, database or error; a link that drops: degraded, 503 stage_down
     with Retry-After, and serving again by itself; a drain of either node takes both; a stage
     that stops: partial, Start brings it back with the same key; a rejoin after an update;
     Stop takes the tail first and removes the keys                          test_split_runs
  6  a half-started Split: the tail does not come up, the start fails, the front is stopped,
     the keys are gone, the record is stopped; a Split without a plan does not start (409
     no_plan); a move is refused (409 partial_split)                         test_half_started

    python tests/test_split.py
"""

from __future__ import annotations

import base64
import copy
import json
import os
import stat
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_backend  # noqa: E402
import test_deployments as td  # noqa: E402
import test_fleet as tf  # noqa: E402
import test_node_agent as tna  # noqa: E402
from ports import free_port  # noqa: E402
from proxy import nodeengines, splitplan, splitrun  # noqa: E402
from proxy_util import run  # noqa: E402

FAKE = os.path.join(ROOT, "tests", "fake_backend.py")
EXAMPLE = os.path.join(ROOT, "contracts", "engine-api-v1", "setup", "split.example.json")
with open(EXAMPLE) as _f:
    BLOCK = json.load(_f)
EST = dict(td.EST)
SEL = {"model": "fake-m", "profile": "flashnext-2.05", "window": 40960, "slots": 8}


def node(alias, block=None, budget=None, **kw):
    b = copy.deepcopy(block or BLOCK)
    return dict({"node": alias.lower(), "alias": alias, "block": b,
                 "budget_gib": b["budget_gb"] if budget is None else budget,
                 "disk_free_gib": None}, **kw)


# ---------------------------------------------------------------------------- 1. the rule

def test_plan_rule():
    # the contract's example: 8 slots, caches on; cut 12 front 40.74 (slot 0.14), tail 41.80
    # (slot 0.44); cut 24 front 49.80 (0.27), tail 32.74 (0.31); budget 106.63 a node
    p = splitplan.pair(node("Gold"), node("R-1"), 8)
    assert p["allowed"] and p["cut"] == 12, p
    f, t = p["front"], p["tail"]
    assert (f["layers"], t["layers"]) == ("0:12", "12:48"), (f, t)
    # each side: the estimate's figure plus one more slot figure a side slot (7 of them)
    assert (f["estimate_gib"], f["margin_gib"], f["need_gib"]) == (40.74, 0.98, 41.72), f
    assert (t["estimate_gib"], t["margin_gib"], t["need_gib"]) == (41.8, 3.08, 44.88), t
    assert f["parts"][-1] == {"id": "slot_margin", "gib": 0.98, "basis": "margin"}, f["parts"]
    assert {x["basis"] for x in f["parts"][:-1]} <= {"pack_headers", "setting", "config", "ask",
                                                      "registry_whole_model"}, f["parts"]
    assert abs(sum(x["gib"] for x in t["parts"]) - t["need_gib"]) < 0.02, t
    assert f["table"] and not t["table"] and p["table_on"] == "front", p
    assert (f["headroom_gib"], t["headroom_gib"], p["headroom_gib"]) == (64.91, 61.75, 61.75)
    assert f["disk"] == {"pack_gib": 58.36, "cache_gib": 16.0, "free_gib": None}, f["disk"]
    assert t["disk"]["cache_gib"] == 16.0, t["disk"]
    assert p["computed"] is True and len(p["measured"]) == 4 and p["rule"] == splitplan.RULE
    assert p["margin"] == {"slot_factor": 1.0, "text": splitplan.MARGIN_TEXT}, p["margin"]
    by = {c["cut"]: c for c in p["candidates"]}
    assert by[12]["chosen"] and by[12]["why"] is None and by[12]["fits"], by
    assert by[24]["fits"] and not by[24]["chosen"], by
    assert by[24]["why"] == "smaller headroom (54.94 GiB) than cut 12 (61.75)", by[24]
    assert (by[24]["front_gib"], by[24]["tail_gib"]) == (51.69, 34.91), by[24]
    assert "cut 12 leaves the larger of the smaller headrooms: 61.75 GiB" in p["why"], p["why"]
    # two unequal nodes: each side against its own node. A tail node with 40 GiB cannot hold
    # cut 12's tail (44.88) and holds cut 24's (34.91)
    p = splitplan.pair(node("Gold"), node("R-1", budget=40.0), 8)
    assert p["allowed"] and p["cut"] == 24, p
    by = {c["cut"]: c for c in p["candidates"]}
    assert by[12]["why"] == "the tail needs 44.9 GiB, R-1 has 40.0: 4.9 short", by[12]
    assert (p["tail"]["budget_gib"], p["tail"]["headroom_gib"]) == (40.0, 5.09), p["tail"]
    # the other way round the front is the small one: no cut fits, and the plan says who lacks
    p = splitplan.pair(node("Gold", budget=40.0), node("R-1"), 8)
    assert not p["allowed"] and p["cut"] is None, p
    assert p["reason"] == ("no cut fits: at the closest one (cut 12) the front needs 41.7 GiB, "
                           "Gold has 40.0: 1.7 short"), p["reason"]
    assert [c["fits"] for c in p["candidates"]] == [False, False], p["candidates"]
    # one asked cut; one that does not fit; one the engine did not compute
    p = splitplan.pair(node("Gold"), node("R-1"), 8, cut=24)
    assert p["cut"] == 24 and p["why"] == "asked for: cut 24", p
    assert {c["cut"]: c["why"] for c in p["candidates"]}[12] == "asked for cut 24", p
    p = splitplan.pair(node("Gold"), node("R-1", budget=40.0), 8, cut=12)
    assert not p["allowed"] and p["reason"].startswith("cut 12: the tail needs 44.9 GiB"), p
    p = splitplan.pair(node("Gold"), node("R-1"), 8, cut=13)
    assert not p["allowed"] and "cut 13 is not one the engine computed" in p["reason"], p
    # one slot: no side slot, no margin; caches off the same
    one = copy.deepcopy(BLOCK)
    p = splitplan.pair(node("Gold", block=one), node("R-1", block=one), 1)
    assert p["front"]["margin_gib"] == 0 and p["front"]["need_gib"] == 40.74, p["front"]
    assert all(x["id"] != "slot_margin" for x in p["front"]["parts"]), p["front"]
    # a cache folder the front's disk cannot hold
    p = splitplan.pair(node("Gold", disk_free_gib=8.0), node("R-1"), 8)
    assert not p["allowed"] and p["reason"].endswith("the front's cache folder needs 16 GB, "
                                                     "Gold has 8 free"), p["reason"]
    # two nodes that do not describe one model
    other = copy.deepcopy(BLOCK)
    other["layers"] = 40
    p = splitplan.pair(node("Gold"), node("R-1", block=other), 8)
    assert not p["allowed"] and "do not describe one model" in p["reason"], p
    # the block as the planner reads it: not splittable is the engine's sentence
    assert splitplan.read_block({"split": BLOCK}) == (BLOCK, None)
    no = {"version": "1.0", "splittable": False, "reason": "family",
          "message": "This model's engine is not served as two stages."}
    assert splitplan.read_block({"split": no}) == (None, no["message"])
    assert splitplan.read_block({}) == (None, "its engine's estimate has no split block")
    bad = copy.deepcopy(BLOCK)
    bad["candidates"][0]["front"]["gb"] = "big"
    assert splitplan.read_block({"split": bad})[1].endswith("does not read (a candidate)")
    # the engine API a Split needs
    assert [splitplan.api_ok(v) for v in ("1.2", "1.3", "1.10", "1.1", "1.0", "2.2", None, "x")] \
        == [True, True, True, False, False, False, False, False]
    # the ask: the engine's fields pass, caches default on, the planner's own are checked
    assert splitplan.check_ask(True, 8) == ({"caches": "on"}, {"cut": None, "link": None})
    ask, opts = splitplan.check_ask({"cuts": [12, 24], "caches": "off", "cut": 12}, 1)
    assert ask == {"cuts": [12, 24], "caches": "off"} and opts["cut"] == 12, (ask, opts)
    for raw in ({"nope": 1}, {"cut": 0}, {"cut": True}, "yes"):
        try:
            splitplan.check_ask(raw, 1)
        except splitplan.AskError:
            continue
        raise AssertionError(raw)
    # what each node's agent is told: no key in it
    p = splitplan.pair(node("Gold"), node("R-1"), 8)
    p["link"] = {"listen": "10.77.0.1:7740", "peer": "10.77.0.1:7740"}
    assert splitplan.stage_env(p, "front") == {
        "stage": "0:12", "caches": "on", "slots": 8, "need_gib": 41.72, "window": 40960,
        "side_window": 16384, "listen": "10.77.0.1:7740", "snapshots_gb": 2.0, "cache_gb": 16.0}
    assert splitplan.stage_env(p, "tail") == {
        "stage": "12:48", "caches": "on", "slots": 8, "need_gib": 44.88, "window": 40960,
        "side_window": 16384, "peer": "10.77.0.1:7740"}


# ---------------------------------------------------------------------------- 2. the link

def test_link_rule():
    fab = lambda ip: {"iface": "fab0", "kind": "fabric", "addrs": [f"{ip}/24"], "state": "up"}
    lan = lambda ip: {"iface": "lan0", "kind": "lan", "addrs": [f"{ip}/24"], "state": "up"}
    g = {"alias": "Gold", "machine": "A", "interfaces": [lan("192.0.2.10"), fab("10.77.0.1")]}
    r = {"alias": "R-1", "machine": "B", "interfaces": [lan("192.0.2.11"), fab("10.77.0.2")]}
    got = splitplan.link_between(g, r)
    assert got == {"kind": "fabric", "listen": "10.77.0.1:7740", "peer": "10.77.0.1:7740",
                   "front_iface": "fab0", "tail_iface": "fab0", "warn": None}, got
    # no fabric pair: the LAN pair, with the warning
    r2 = dict(r, interfaces=[lan("192.0.2.11")])
    got = splitplan.link_between(g, r2)
    assert (got["kind"], got["listen"], got["front_iface"]) == ("lan", "192.0.2.10:7740", "lan0")
    assert got["warn"] == ("no fabric link: the stages talk over the LAN pair Gold 192.0.2.10 "
                           "and R-1 192.0.2.11. The data link is neither signed nor encrypted "
                           "after its handshake: use it on a private network only"), got["warn"]
    # a fabric port on one side and a LAN port on the other in one subnet is a LAN pair
    r3 = dict(r, interfaces=[lan("10.77.0.9")])
    assert splitplan.link_between(g, r3)["kind"] == "lan"
    # an interface that is down, or another subnet: no pair
    r4 = dict(r, interfaces=[dict(fab("10.77.0.2"), state="down"), lan("198.51.100.5")])
    got = splitplan.link_between(g, r4)
    assert got == {"reason": "no interface pair in one subnet between Gold and R-1: join them "
                             "with the fabric cable, or put both on one LAN (allowed with a "
                             "warning)"}, got
    # two nodes on one machine: loopback
    got = splitplan.link_between(dict(g, machine="A"), dict(r4, machine="A"))
    assert (got["kind"], got["listen"], got["warn"]) == ("loopback", "127.0.0.1:7740", "loopback")
    # addresses by hand: IP literals, never a name, never every interface
    got = splitplan.link_between(g, r, given={"listen": "10.77.0.1:9000"})
    assert (got["kind"], got["listen"], got["peer"], got["warn"]) == \
        ("fabric", "10.77.0.1:9000", "10.77.0.1:9000", None), got
    got = splitplan.link_between(g, r, given={"listen": "192.0.2.10", "peer": "192.0.2.10:7801"})
    assert (got["kind"], got["listen"], got["peer"]) == ("lan", "192.0.2.10:7740",
                                                         "192.0.2.10:7801"), got
    assert "neither signed nor encrypted" in got["warn"], got
    got = splitplan.link_between(g, r, given={"listen": "203.0.113.7:7740"})
    assert got["kind"] == "given" and got["warn"] == splitplan.GIVEN_WARNING, got
    for given, word in (({"listen": "0.0.0.0:7740"}, "every interface; name one IP"),
                        ({"listen": "[::]:7740"}, "every interface; name one IP"),
                        ({"listen": "gold.lan:7740"}, "takes an IP literal, not 'gold.lan'"),
                        ({"listen": "10.77.0.1:0"}, "the port is a number from 1 to 65535"),
                        ({"listen": "10.77.0.1:7740", "peer": "r1:1"}, "takes an IP literal"),
                        ({"peer": "10.77.0.1:7740"}, "link is {listen, peer?}"),
                        ({"listen": "127.0.0.1:7740"}, "is loopback: it reaches Gold or R-1 "
                                                       "alone, never both")):
        got = splitplan.link_between(g, r, given=given)
        assert word in got.get("reason", ""), (given, got)
    got = splitplan.link_between(dict(g, machine="A"), dict(r, machine="A"),
                                 given={"listen": "127.0.0.1:8104", "peer": "127.0.0.1:8114"})
    assert (got["kind"], got["listen"], got["peer"]) == ("loopback", "127.0.0.1:8104",
                                                         "127.0.0.1:8114"), got
    assert splitplan.address("[fd00::1]:7740", "x") == ("fd00::1", 7740)
    assert splitplan.join("fd00::1", 7740) == "[fd00::1]:7740"


# ---------------------------------------------------------------------------- 3. the agent

KEY = bytes(range(7, 55))                        # 48 bytes a test knows
KEY64 = base64.b64encode(KEY).decode()
KEY_FORMS = (KEY, KEY64.encode(), KEY.hex().encode(), base64.urlsafe_b64encode(KEY))


def leaks(blob) -> bool:
    raw = blob if isinstance(blob, bytes) else str(blob).encode("utf-8", "replace")
    return any(k in raw for k in KEY_FORMS)


def test_agent_stage():
    buf = []
    port = free_port()
    d = tempfile.mkdtemp(prefix="split-env-")
    local_env = os.path.join(d, "run", "local.env")
    os.makedirs(os.path.dirname(local_env))
    with open(local_env, "w") as f:
        f.write("QSE_MODE=engine\n# mine\n")
    eng = tna.engine_doc("e", port, kind="tandemllm", local_env=local_env,
                         start=[sys.executable, FAKE, "--port", str(port), "--models", "m"])
    plain = tna.engine_doc("p", free_port(), kind="tandemllm",
                           start=[sys.executable, FAKE, "--port", "1", "--models", "m"])
    other = tna.engine_doc("u", free_port(), kind="tandemllm")           # not managed
    agent = tna.agent_on(tna.machine("SPL-A-0001"), engines=[eng, plain, other], buf=buf)
    with tna.Running(agent) as r:
        c = r.client
        body = {"stage": "0:12", "listen": "10.77.0.1:7740", "key": KEY64, "caches": "on",
                "slots": 8, "window": 40960, "side_window": 16384, "snapshots_gb": 2,
                "cache_gb": 16, "need_gib": 41.72}
        st, view = c.json("PUT", "/node/engines/e/stage", body)
        assert st == 200, view
        assert view["stage"] == {"stage": "0:12", "role": "front", "listen": "10.77.0.1:7740",
                                 "caches": "on", "slots": 8, "key": "set"}, view["stage"]
        kpath = os.path.join(eng.stage_dir, "e.key")
        with open(kpath, "rb") as f:
            assert f.read() == KEY
        assert stat.S_IMODE(os.stat(kpath).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(eng.stage_dir).st_mode) == 0o700
        with open(local_env) as f:
            text = f.read()
        # the engine's own lines stay; the block has the key FILE's path, never the key
        assert text.startswith("QSE_MODE=engine\n# mine\n" + nodeengines.STAGE_BEGIN), text
        assert text.rstrip().endswith(nodeengines.STAGE_END), text
        env = dict(x.split("=", 1) for x in text.splitlines() if "=" in x and x[0] != "#")
        cdir = os.path.join(eng.stage_dir, "e-cache")
        assert env == {"QSE_MODE": "engine", "TANDEM_STAGE": "0:12", "TANDEM_LINK_KEY": kpath,
                       "TANDEM_STAGE_CACHES": "on", "QSE_PARALLEL": "8",
                       "TANDEM_LINK_LISTEN": "10.77.0.1:7740", "MAX_LEN": "40960",
                       "QSE_SIDE_MAX_LEN": "16384", "QSE_DISK_CACHE": "off",
                       "TANDEM_STAGE_SNAPSHOTS_GB": "2", "TANDEM_STAGE_CACHE_DIR": cdir,
                       "TANDEM_STAGE_CACHE_GB": "16", "TANDEM_MIN_FREE_GIB": "42"}, env
        assert os.path.isdir(cdir) and eng.stage_env == {}, eng.stage_env
        # set again: the block is replaced, not added to
        st, _ = c.json("PUT", "/node/engines/e/stage", dict(body, slots=4))
        with open(local_env) as f:
            text = f.read()
        assert st == 200 and text.count(nodeengines.STAGE_BEGIN) == 1 and \
            "QSE_PARALLEL=4" in text and "QSE_PARALLEL=8" not in text, text
        # an engine without an env file of its own: the keys go into its start's environment
        tail = {"stage": "12:48", "peer": "10.77.0.1:7740", "key": KEY64}
        st, view = c.json("PUT", "/node/engines/p/stage", tail)
        assert st == 200 and view["stage"]["role"] == "tail" and view["stage"]["slots"] == 1
        assert plain.stage_env["TANDEM_LINK_PEER"] == "10.77.0.1:7740", plain.stage_env
        assert plain.stage_env["TANDEM_LINK_KEY"] == os.path.join(plain.stage_dir, "p.key")
        assert "QSE_DISK_CACHE" not in plain.stage_env and \
            "TANDEM_STAGE_SNAPSHOTS_GB" not in plain.stage_env, plain.stage_env
        # the views, the engines list, the agent's log: no key anywhere
        seen = [c.raw("GET", "/node/engines")[1], c.raw("GET", "/node/engines/e")[1],
                c.raw("GET", "/node/engines/p")[1], "\n".join(map(str, buf)), text,
                json.dumps(plain.stage_env)]
        assert not any(leaks(x) for x in seen), "the key leaked"
        assert any("stage 0:12 (front) set on e" in str(x) for x in buf), buf
        # what the agent refuses, in words without the key
        for bad, word in ((dict(body, listen="0.0.0.0:7740"), "every interface; name one IP"),
                          (dict(body, listen="gold:7740"), "takes an IP literal and a port"),
                          (dict(body, peer="10.77.0.2:1"), "one of listen and peer"),
                          (dict(body, stage="12:12"), "LO:HI with LO < HI"),
                          (dict(body, key=base64.b64encode(b"short").decode()), "at least 32"),
                          (dict(body, key=KEY64 + "!"), "at least 32"),
                          (dict(body, caches="off"), "without caches serves one slot"),
                          (dict(tail, snapshots_gb=2), "the front stage's"),
                          (dict(body, extra=1), "unknown field 'extra'")):
            st, err = c.json("PUT", "/node/engines/e/stage", bad)
            assert st == 400 and word in err["error"]["message"], (bad, err)
            assert not leaks(json.dumps(err)), err
        st, err = c.json("PUT", "/node/engines/u/stage", body)
        assert st == 409 and err["error"]["type"] == "not_managed", err
        st, err = c.json("PUT", "/node/engines/nope/stage", body)
        assert st == 404, err
        # a stage set before the agent started is still set (its env file and key file)
        again = tna.engine_doc("p", plain.port, kind="tandemllm", start=["true"])
        again.stage_dir = plain.stage_dir
        nodeengines._load_stage(again)
        assert again.stage == {"stage": "12:48", "role": "tail", "caches": "off", "slots": 1,
                               "key": "set", "peer": "10.77.0.1:7740"}, again.stage
        assert again.stage_env == plain.stage_env
        # clear: the block, the env and the key file are gone; the engine's own lines stay
        st, view = c.json("DELETE", "/node/engines/e/stage")
        assert st == 200 and view["stage"] is None, view
        with open(local_env) as f:
            assert f.read() == "QSE_MODE=engine\n# mine\n"
        assert not os.path.exists(kpath) and not os.path.exists(kpath[:-4] + ".env")
        st, view = c.json("DELETE", "/node/engines/e/stage")
        assert st == 200 and view["stage"] is None, view
        st, view = c.json("DELETE", "/node/engines/p/stage")
        assert st == 200 and plain.stage_env == {} and not os.path.exists(
            os.path.join(plain.stage_dir, "p.key")), view
    # the agent's relay keeps an engine's Retry-After (a tail whose link is down says 5 s)
    srv, eport = fake_backend.start(models=["m"])
    srv.fake.identity, srv.fake.identity_pending = {"version": 1}, 1
    agent = tna.agent_on(tna.machine("SPL-A-0002"),
                         engines=[tna.engine_doc("e", eport, kind="tandemllm")])
    try:
        with tna.Running(agent) as r:
            conn = r.client.connection()
            conn.request("GET", "/node/engines/e/v1/replica/identity", headers=r.client.headers())
            resp = conn.getresponse()
            resp.read()
            assert (resp.status, resp.getheader("Retry-After")) == (503, "1"), resp.getheaders()
            conn.close()
    finally:
        srv.shutdown()
        srv.server_close()


# ---------------------------------------------------------------------------- the fleet

class SNode:
    """A virtual node whose agent manages one fake TandemLLM engine process: a whole model
    until a stage is set on it. `estimate`: the file its Setup Guide's estimate comes from."""

    def __init__(self, serial, name, estimate, addrs=None, env=None, engine_api="1.2"):
        self.eport = free_port()
        self.buf = []
        self.engine = tna.engine_doc(
            "e", self.eport, kind="tandemllm", start_timeout_s=20, env=env or {},
            start=[sys.executable, FAKE, "--port", str(self.eport), "--models", "fake-m",
                   "--engine-api", engine_api, "--estimate", estimate])
        self.agent = tna.agent_on(tna.machine(serial), engines=[self.engine],
                                  address="127.0.0.1", virtual=name, addrs=addrs, buf=self.buf)
        self.run = tna.Running(self.agent)
        self.url = self.run.url
        self.srv = None

    def act(self, action):
        st, _ = self.run.client.json("POST", f"/node/engines/e/{action}")
        assert st in (202, 409), st
        want = "down" if action == "stop" else "up"
        got = td._until(lambda: (self.view().get("job") or {}).get("state") != "running"
                        and self.view()["state"] == want, 20)
        assert got, self.view()

    def view(self):
        return self.run.client.json("GET", "/node/engines/e")[1]

    def key(self):
        try:
            with open(os.path.join(self.engine.stage_dir, "e.key"), "rb") as f:
                return f.read()
        except OSError:
            return None

    def fake(self, method, path, body=None):
        """(status, body) of a request straight to this node's fake engine."""
        st, _, raw = tna.raw(self.eport, method, path, {"Content-Type": "application/json"},
                             None if body is None else json.dumps(body).encode())
        return st, raw

    def close(self):
        self.run.close()


def estimate_file(split=BLOCK, **est):
    fd, path = tempfile.mkstemp(prefix="split-est-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump({"estimate": dict(EST, **est), "split": split}, f)
    return path


def split_type(p):
    return next(t for t in p["types"] if t["type"] == "split")


def plan_body(g, r1, **split):
    return {"model": "fake-m", "pack": "flashnext-2.05", "ctx": 40960, "slots": 8,
            "nodes": [g, r1], "split": split or True}


def test_placement_api():
    addrs = {"Gold": {"fab0": ["10.77.0.1/24"]}, "R-1": {"fab0": ["10.77.0.2/24"]}}
    not_split = {"version": "1.0", "splittable": False, "reason": "family",
                 "message": "This model's engine is not served as two stages."}
    fx = td.Fleet({"Gold": td.Node("SPA-G-0001", "g", ["fake-m"], kind="tandemllm"),
                   "R-1": td.Node("SPA-1-0001", "r1", ["fake-m"], kind="tandemllm"),
                   "R-2": td.Node("SPA-2-0001", "r2", ["fake-m"], kind="tandemllm")})
    try:
        g, r1, r2 = fx.ids["Gold"], fx.ids["R-1"], fx.ids["R-2"]
        for n in fx.nodes.values():
            n.srv.fake.estimate, n.srv.fake.split = dict(EST), copy.deepcopy(BLOCK)
            n.srv.fake.health = {"engine_api": "1.2"}
        for src in [s for _, s in fx.f.sources()]:
            if hasattr(src, "forget_engines"):
                src.forget_engines()
        # no interface pair in one subnet: refused, in words
        st, p = tf.send(fx.px, "POST", "/v1/fleet/placement", plan_body(g, r1))
        assert st == 200, p
        sp = split_type(tf.ok(p, "placement"))
        assert not sp["allowed"] and sp["built"] and "split" not in sp, sp
        assert sp["reason"].startswith("no interface pair in one subnet between Gold and R-1")
        # the engines were asked beside the selection, with the ask
        last = fx.nodes["R-1"].srv.fake.last
        assert last["body"] == {"selection": SEL, "split": {"caches": "on"}}, last["body"]
        # a fabric pair: allowed, the cut and both sides' numbers stated
        for alias, a in addrs.items():
            fx.nodes[alias].agent.addrs = (lambda a=a: a)
        fx.f.placement._fabric.clear()
        st, p = tf.send(fx.px, "POST", "/v1/fleet/placement",
                        plan_body(g, r1, caches="on", tail_disk_gb=16))
        sp = split_type(tf.ok(p, "placement"))
        assert sp["allowed"] and sp["built"] and sp["nodes"] == [g, r1] and sp["warn"] is None
        assert sp["stages"] == [{"node": g, "layers": "0:12", "gib": 41.72},
                                {"node": r1, "layers": "12:48", "gib": 44.88}], sp["stages"]
        plan = sp["split"]
        assert (plan["cut"], plan["layers"], plan["window"], plan["slots"]) == (12, 48, 40960, 8)
        assert plan["link"] == {"kind": "fabric", "listen": "10.77.0.1:7740",
                                "peer": "10.77.0.1:7740", "front_iface": "fab0",
                                "tail_iface": "fab0", "warn": None}, plan["link"]
        assert (plan["front"]["alias"], plan["tail"]["alias"]) == ("Gold", "R-1"), plan
        assert fx.nodes["Gold"].srv.fake.last["body"]["split"] == {"caches": "on",
                                                                   "tail_disk_gb": 16}
        # without nodes: every ordered pair planned, the fabric pair offered; R-2 has no pair
        st, p = tf.send(fx.px, "POST", "/v1/fleet/placement",
                        {k: v for k, v in plan_body(g, r1).items() if k != "nodes"})
        sp = split_type(tf.ok(p, "placement"))
        assert sp["allowed"] and set(sp["nodes"]) == {g, r1}, sp
        # a model one node holds is not split unless asked
        body = {k: v for k, v in plan_body(g, r1).items() if k != "split"}
        sp = split_type(tf.send(fx.px, "POST", "/v1/fleet/placement", body)[1])
        assert not sp["allowed"] and sp["reason"] == ("Gold holds the model alone: a Split is "
                                                      "planned when asked for (split)"), sp
        # ... and is planned unasked when no node holds it
        for n in fx.nodes.values():
            n.srv.fake.estimate = dict(EST, fits=False, total_gb=140.0)
        sp = split_type(tf.send(fx.px, "POST", "/v1/fleet/placement", body)[1])
        assert sp["allowed"] and sp["split"]["cut"] == 12, sp
        for n in fx.nodes.values():
            n.srv.fake.estimate = dict(EST)
        # a LAN pair: allowed with the warning
        fx.nodes["R-1"].agent.addrs = (lambda: {"lan0": ["10.77.0.2/24"]})
        fx.f.placement._fabric.clear()
        sp = split_type(tf.ok(tf.send(fx.px, "POST", "/v1/fleet/placement",
                                      plan_body(g, r1))[1], "placement"))
        assert sp["allowed"] and sp["split"]["link"]["kind"] == "lan", sp
        assert sp["warn"] == sp["split"]["link"]["warn"] and \
            "neither signed nor encrypted after its handshake" in sp["warn"], sp
        fx.nodes["R-1"].agent.addrs = (lambda: addrs["R-1"])
        fx.f.placement._fabric.clear()
        # the engine's refusal of an ask is the plan's reason
        st, p = tf.send(fx.px, "POST", "/v1/fleet/placement", plan_body(g, r1, cut=0))
        assert st == 400 and "split.cut" in p["error"]["message"], p
        st, p = tf.send(fx.px, "POST", "/v1/fleet/placement", plan_body(g, r1, nope=1))
        assert st == 400 and "unknown field 'nope'" in p["error"]["message"], p
        # a model that is not splittable: a plan-level answer with the engine's sentence
        fx.nodes["R-1"].srv.fake.split = not_split
        sp = split_type(tf.ok(tf.send(fx.px, "POST", "/v1/fleet/placement",
                                      plan_body(g, r1))[1], "placement"))
        assert not sp["allowed"] and sp["reason"] == f"R-1: {not_split['message']}", sp
        fx.nodes["R-1"].srv.fake.split = copy.deepcopy(BLOCK)
        # an engine on API 1.1 is not asked for a split at all
        fx.nodes["R-1"].srv.fake.health = {"engine_api": "1.1"}
        fx.nodes["R-1"].srv.fake.last = None
        for src in [s for _, s in fx.f.sources()]:
            if hasattr(src, "forget_engines"):
                src.forget_engines()
        sp = split_type(tf.send(fx.px, "POST", "/v1/fleet/placement", plan_body(g, r1))[1])
        assert not sp["allowed"] and sp["reason"] == ("R-1 its engine speaks engine API 1.1: a "
                                                      "Split needs 1.2 or later"), sp
        last = fx.nodes["R-1"].srv.fake.last
        assert last is None or "split" not in last["body"], last
        # three nodes named: a Split runs over two
        sp = split_type(tf.send(fx.px, "POST", "/v1/fleet/placement",
                                dict(plan_body(g, r1), nodes=[g, r1, r2]))[1])
        assert not sp["allowed"] and "runs over two nodes" in sp["reason"], sp
    finally:
        fx.close()


def _db_text(px) -> str:
    cfg = px.svc.cfg
    with cfg._lock:
        return "\n".join(cfg._conn.iterdump())


def _chat(fx, model, **kw):
    st, h, raw = fx.chat(model, **kw)
    return st, {k.lower(): v for k, v in (h.items() if hasattr(h, "items") else h)}, \
        json.loads(raw or b"null")


def _wait_status(fx, did, status, s=20.0):
    d = td._until(lambda: (lambda v: v if v["status"] == status else None)(td._dep(fx, did)), s,
                  0.1)
    assert d and d["status"] == status, td._dep(fx, did)
    return d


def _job(fx, did, s=60.0):
    def done():
        d = td._dep(fx, did)
        return d if d["job"] and d["job"]["state"] != "running" else None
    d = td._until(done, s, 0.1)
    assert d, td._dep(fx, did)
    return d


def _act(fx, did, action, confirm=False):
    body = {}
    if confirm:
        plan = tf.ok(tf.get(fx.px, f"/v1/fleet/deployments/{did}/actions/{action}")[1],
                     "deployment-plan")
        assert plan["allowed"], plan
        body = {"confirm": plan["confirm"]}
    st, got = tf.send(fx.px, "POST", f"/v1/fleet/deployments/{did}/actions/{action}", body)
    assert st == 200, got
    d = _job(fx, did)
    st, j = tf.get(fx.px, f"/v1/fleet/deployments/jobs/{got['job']['id']}")
    return d, tf.ok(j, "deployment-job")["job"]


def _order(job, first, then):
    texts = [x["text"] for x in job["log"]]
    a = next(i for i, t in enumerate(texts) if first in t)
    b = next(i for i, t in enumerate(texts) if then in t)
    assert a < b, texts


def test_split_runs():
    est = estimate_file()
    plink = free_port()
    # two nodes of one machine (one serial): the link is loopback, its addresses by hand
    fx = td.Fleet({"Gold": SNode("SPL-M-0001", "g", est), "R-1": SNode("SPL-M-0001", "r1", est)})
    gold, tail = fx.nodes["Gold"], fx.nodes["R-1"]
    try:
        g, r1 = fx.ids["Gold"], fx.ids["R-1"]
        for n in fx.nodes.values():
            n.act("start")                         # whole-model engines: they answer the guide
        fx.sync()
        link = {"listen": f"127.0.0.1:{plink}"}
        st, p = tf.send(fx.px, "POST", "/v1/fleet/placement",
                        plan_body(g, r1, link=link, front_cache_gb=16))
        assert st == 200, p
        sp = split_type(tf.ok(p, "placement"))
        assert sp["allowed"] and sp["split"]["cut"] == 12, sp
        assert sp["split"]["link"]["kind"] == "loopback" and \
            sp["warn"].startswith("both stages run on one machine over loopback"), sp
        assert json.loads(gold.fake("GET", "/fake/last")[1])["body"]["split"] == \
            {"caches": "on", "front_cache_gb": 16}
        # the record: the plan kept, stopped until started; the front has no backend
        d = fx.record({"model": "fake-m", "pack": "flashnext-2.05", "type": "split",
                       "name": "Split M", "api_id": "split-m", "members": [g, r1],
                       "stages": sp["stages"], "split": sp["split"]})
        tf.ok(d, "deployment")
        did = d["id"]
        assert (d["status"], d["state"], d["ctx"], d["managed"]) == ("stopped", "stopped",
                                                                     40960, True), d
        f_ms, t_ms = d["member_states"]
        assert (f_ms["backend"], f_ms["engine"], f_ms["routable"]) == (None, "e", False), f_ms
        assert t_ms["backend"] and t_ms["engine"] == "e", t_ms
        assert d["split"]["cut"] == 12 and d["split"]["phase"] == "stopped" and \
            d["split"]["built"] is False and d["split"]["link"]["state"] == "unknown", d["split"]
        # a backend that reaches the front's engine, as if it had served a Single before
        fx.px.svc.cfg.create_backend({"id": "gold-e", "kind": "tandemllm",
                                      "url": f"http://127.0.0.1:{gold.eport}/v1"})
        fx.sync()
        st, _, body = _chat(fx, "split-m")
        assert st == 503 and body["error"]["code"] == "deployment_stopped", body
        # the start's plan says the order and the key
        plan = tf.ok(tf.get(fx.px, f"/v1/fleet/deployments/{did}/actions/start")[1],
                     "deployment-plan")
        assert plan["allowed"], plan
        assert "Gold: TandemNode starts the front stage (layers 0:12, 41.7 GiB)" in \
            plan["impact"][0] and "the engine that runs there now stops first" in \
            plan["impact"][0], plan["impact"]
        assert "R-1: TandemNode starts the tail stage (layers 12:48, 44.9 GiB), after the " \
               "front answers" in plan["impact"][1], plan["impact"]
        assert "a new link key goes to both nodes' agents" in plan["impact"][2], plan["impact"]
        d, job = _act(fx, did, "start")
        assert job["state"] == "done" and job["phase"] == "linked", job
        _order(job, "TandemNode starts the front stage", "TandemNode starts the tail stage")
        assert (d["status"], d["state"]) == ("serving", "serving"), d
        tf.ok(d, "deployment")
        sv = d["split"]
        assert (sv["phase"], sv["built"], sv["link"]["state"], sv["link"]["epoch"],
                sv["link"]["transport"]) == ("linked", True, "up", 1, "tcp"), sv
        f_ms, t_ms = d["member_states"]
        assert (f_ms["state"], f_ms["routable"], f_ms["stage"]["role"], f_ms["stage"]["layers"],
                f_ms["stage"]["link"]) == ("serving", False, "front", "0:12", "up"), f_ms
        assert (t_ms["state"], t_ms["routable"], t_ms["stage"]["role"], t_ms["stage"]["layers"],
                t_ms["stage"]["epoch"]) == ("serving", True, "tail", "12:48", 1), t_ms
        assert "the front on Gold, the tail on R-1, link up (epoch 1)" in d["status_text"], d
        # both stages as the engine says them: the planned ranges, linked to each other
        fh = json.loads(gold.fake("GET", "/health")[1])["stage"]
        th = json.loads(tail.fake("GET", "/health")[1])["stage"]
        assert (fh["role"], fh["range"], th["role"], th["range"]) == ("front", "0:12", "tail",
                                                                      "12:48"), (fh, th)
        assert fh["link"]["peer"]["identity"] == th["identity"] and \
            th["link"]["peer"]["identity"] == fh["identity"], (fh, th)
        assert fh["options"]["slots"] == th["options"]["slots"] == 8
        # one key on both nodes, in 0600 files; each engine got the file's path
        key = gold.key()
        assert key and key == tail.key() and len(key) >= 32, (key, tail.key())
        for n in (gold, tail):
            kp = os.path.join(n.engine.stage_dir, "e.key")
            assert stat.S_IMODE(os.stat(kp).st_mode) == 0o600
            assert n.engine.stage_env["TANDEM_LINK_KEY"] == kp, n.engine.stage_env
        assert gold.engine.stage_env["TANDEM_LINK_LISTEN"] == f"127.0.0.1:{plink}"
        assert tail.engine.stage_env["TANDEM_LINK_PEER"] == f"127.0.0.1:{plink}"
        # requests go to the tail only; /v1/models lists the Split once and nothing of the front
        st, h, body = _chat(fx, "split-m")
        assert st == 200 and h["x-tandem-node"] == "R-1" and h["x-tandem-deployment"] == did, h
        ids = [m["id"] for m in json.loads(fx.px.get("/v1/models")[2])["data"]]
        assert ids.count("split-m") == 1 and not any("gold-e" in i for i in ids), ids
        deps = tf.ok(tf.get(fx.px, "/v1/fleet/deployments")[1], "deployments")["deployments"]
        assert [x["id"] for x in deps] == [did], [x["id"] for x in deps]
        st, _, body = _chat(fx, "gold-e/fake-m")
        assert st in (404, 503), (st, body)
        stats = json.loads(gold.fake("GET", "/fake/stats")[1])
        assert stats["front_refused_posts"] == 0 and stats["requests"] == 0, stats
        # a Split is no Replicas: no identity round, no canary on either stage
        assert "check" not in f_ms and "check" not in t_ms, (f_ms, t_ms)
        assert fx.f.deps.canary.verdict(did, t_ms["backend"]) is None

        def nowhere():
            key_now = gold.key()
            forms = (key_now, base64.b64encode(key_now), key_now.hex().encode(),
                     base64.urlsafe_b64encode(key_now))
            seen = [fx.px.get("/v1/fleet/deployments")[2], fx.px.get("/v1/models")[2],
                    fx.px.get(f"/v1/fleet/deployments/{did}")[2],
                    fx.px.get(f"/v1/fleet/deployments/jobs/{job['id']}")[2],
                    fx.px.get("/v1/fleet/events")[2], fx.px.get("/health")[2],
                    gold.fake("GET", "/health")[1], tail.fake("GET", "/health")[1],
                    gold.run.client.raw("GET", "/node/engines")[1],
                    tail.run.client.raw("GET", "/node/engines")[1],
                    "\n".join(map(str, fx.px.logs)), "\n".join(map(str, gold.buf + tail.buf)),
                    _db_text(fx.px), json.dumps(gold.engine.stage_env)]
            for n in (gold, tail):
                for path in (n.engine.log_path,
                             os.path.join(n.engine.stage_dir, "e.env")):
                    with open(path, "rb") as f:
                        seen.append(f.read())
            for blob in seen:
                raw = blob if isinstance(blob, bytes) else str(blob).encode("utf-8", "replace")
                assert not any(k in raw for k in forms), "the link key leaked"
        nowhere()
        # the link drops: degraded, requests 503 with Retry-After, nothing to do
        jid = d["job"]["id"]
        assert tail.fake("POST", "/fake/link", {"cut": True})[0] == 200
        d = _wait_status(fx, did, "degraded")
        tf.ok(d, "deployment")
        assert d["split"]["phase"] == "degraded" and d["split"]["link"]["state"] == "down", d
        assert "the link between Gold and R-1 is down" in d["status_text"] and \
            "Retry-After 5 s" in d["status_text"] and "nothing to do" in d["status_text"], d
        assert [m["state"] for m in d["member_states"]] == ["waiting", "waiting"], d
        assert not any(m["routable"] for m in d["member_states"]), d
        st, h, body = _chat(fx, "split-m")
        assert (st, body["error"]["code"], h.get("retry-after")) == (503, "stage_down", "5"), \
            (st, h, body)
        assert "the link between Gold and R-1 is down" in body["error"]["message"], body
        # the link heals: served again without any action, under the next epoch
        assert tail.fake("POST", "/fake/link", {"cut": False})[0] == 200
        d = _wait_status(fx, did, "serving")
        assert d["split"]["link"]["epoch"] == 2 and d["job"]["id"] == jid, d
        assert _chat(fx, "split-m")[0] == 200
        # a drain of either node takes both: no request while the front's node is drained
        fx.px.svc.cfg.update_node(g, {"drain": {"since": 1.0, "by": "test"}}, who="test")
        fx.f.ops.refresh()
        fx.sync()
        st, h, body = _chat(fx, "split-m")
        assert (st, body["error"]["code"], h.get("retry-after")) == (503, "node_draining", "30")
        d = td._dep(fx, did)
        assert d["status"] == "degraded" and "Gold is drained" in d["status_text"], d
        dplan = tf.get(fx.px, f"/v1/fleet/nodes/{r1}/actions/drain")[1]
        assert any("Split M is a Split over R-1 and Gold" in x for x in dplan["impact"]), dplan
        fx.px.svc.cfg.update_node(g, {"drain": None}, who="test")
        fx.f.ops.refresh()
        fx.sync()
        assert _wait_status(fx, did, "serving") and _chat(fx, "split-m")[0] == 200
        # the front stops by itself: partial, in words; Start brings it back with the same key
        gold.act("stop")
        d = _wait_status(fx, did, "down")
        assert d["split"]["phase"] == "partial", d["split"]
        assert d["status_text"].startswith("only the tail runs (on R-1); the front on Gold "
                                           "does not") and "Start brings it back" in \
            d["status_text"], d
        st, h, body = _chat(fx, "split-m")
        assert (st, body["error"]["code"], h.get("retry-after")) == (503, "stage_down", "5")
        plan = tf.get(fx.px, f"/v1/fleet/deployments/{did}/actions/start")[1]
        assert plan["allowed"] and "R-1: the tail stage runs already" in plan["impact"], plan
        d, job2 = _act(fx, did, "start")
        assert job2["state"] == "done" and d["status"] == "serving", (job2, d)
        assert not any("starts the tail stage" in x["text"] for x in job2["log"]), job2["log"]
        assert gold.key() == key == tail.key()
        assert _chat(fx, "split-m")[0] == 200
        # a fleet update restarted both nodes' engines: the rejoin starts them in order again
        tail.act("stop")
        gold.act("stop")
        said = []
        fx.f.deps.split.rejoin(did, said.append)
        d = td._dep(fx, did)
        assert d["status"] == "serving" and gold.key() == key == tail.key(), d
        a = next(i for i, t in enumerate(said) if "starts the front stage" in t)
        b = next(i for i, t in enumerate(said) if "starts the tail stage" in t)
        assert a < b and _chat(fx, "split-m")[0] == 200, said
        nowhere()
        # a Split that runs is not deleted: its stages and its key would stay behind
        st, err = tf.send(fx.px, "DELETE", f"/v1/fleet/deployments/{did}", None)
        assert st == 409 and "stop it first" in err["error"]["message"], err
        # stop: the tail first, both gone, the key removed from both nodes
        plan = tf.ok(tf.get(fx.px, f"/v1/fleet/deployments/{did}/actions/stop")[1],
                     "deployment-plan")
        assert plan["impact"][-3:] == ["R-1: TandemNode stops the tail stage first",
                                       "Gold: TandemNode stops the front stage",
                                       "the link key is removed from both nodes"], plan
        d, job3 = _act(fx, did, "stop", confirm=True)
        assert job3["state"] == "done", job3
        _order(job3, "R-1: TandemNode stops the tail stage", "Gold: TandemNode stops the front")
        assert (d["status"], d["split"]["phase"], d["split"]["built"]) == ("stopped", "stopped",
                                                                           False), d
        for n in (gold, tail):
            v = n.view()
            assert v["state"] == "down" and v["stage"] is None and n.key() is None, v
            assert n.engine.stage_env == {} and not os.path.exists(
                os.path.join(n.engine.stage_dir, "e.env"))
        st, _, body = _chat(fx, "split-m")
        assert st == 503 and body["error"]["code"] == "deployment_stopped", body
        blobs = [fx.px.get(f"/v1/fleet/deployments/jobs/{j['id']}")[2] for j in (job2, job3)]
        assert not any(key in b or base64.b64encode(key) in b for b in blobs)
        st, err = tf.send(fx.px, "POST", f"/v1/fleet/deployments/{did}/actions/stop", {})
        assert st == 409 and "stopped already" in err["error"]["message"], err
        st, gone = tf.send(fx.px, "DELETE", f"/v1/fleet/deployments/{did}", None)
        assert st == 200 and gone["deleted"] == did, gone
    finally:
        fx.close()
        os.unlink(est)


def test_half_started():
    est = estimate_file()
    plink, dead = free_port(), free_port()
    fx = td.Fleet({"Gold": SNode("SPL-H-0001", "g", est),
                   "R-1": SNode("SPL-H-0001", "r1", est, env={"FAKE_ATTACH_S": "1.5"})})
    gold, tail = fx.nodes["Gold"], fx.nodes["R-1"]
    try:
        g, r1 = fx.ids["Gold"], fx.ids["R-1"]
        for n in fx.nodes.values():
            n.act("start")
        fx.sync()
        # the tail is told an address nothing listens on: it never links
        link = {"listen": f"127.0.0.1:{plink}", "peer": f"127.0.0.1:{dead}"}
        sp = split_type(tf.send(fx.px, "POST", "/v1/fleet/placement",
                                plan_body(g, r1, link=link))[1])
        assert sp["allowed"], sp
        d = fx.record({"model": "fake-m", "type": "split", "api_id": "half", "members": [g, r1],
                       "split": sp["split"]})
        did = d["id"]
        assert d["stages"] == sp["stages"], d["stages"]       # from the plan when not given
        d, job = _act(fx, did, "start")
        assert job["state"] == "failed" and job["error"]["type"] == "start_failed", job
        msg = job["error"]["message"]
        assert "the tail stage on R-1 did not start" in msg and "no front stage at " \
            f"127.0.0.1:{dead}" in msg and msg.endswith("both stages are stopped and the link "
                                                        "key is removed"), msg
        _order(job, "TandemNode starts the front stage", "TandemNode starts the tail stage")
        # a defined state: nothing runs, no key, the record stopped
        assert (d["status"], d["state"], d["split"]["phase"]) == ("stopped", "stopped",
                                                                  "stopped"), d
        for n in (gold, tail):
            v = n.view()
            assert v["state"] == "down" and v["stage"] is None and n.key() is None, v
        assert not td._port_open(plink)
        # one stage left running by hand: partial, and Stop ends it
        st, _ = gold.run.client.json("PUT", "/node/engines/e/stage",
                                     {"stage": "0:12", "listen": f"127.0.0.1:{plink}",
                                      "key": KEY64, "caches": "on", "slots": 8})
        assert st == 200
        gold.act("start")
        fx.f.deps.split.forget()
        d = td._dep(fx, did)
        assert (d["status"], d["split"]["phase"]) == ("stopped", "partial"), d
        assert d["status_text"] == "stopped · the front on Gold still runs: Stop ends it", d
        d, job = _act(fx, did, "stop", confirm=True)
        assert job["state"] == "done" and gold.view()["state"] == "down" and \
            gold.key() is None, (job, gold.view())
        # a move is refused: the two stages move together
        st, err = tf.send(fx.px, "POST", f"/v1/fleet/deployments/{did}/actions/move",
                          {"from": g, "to": r1})
        assert st == 409 and tf.ok(err, "error")["error"]["type"] == "partial_split", err
        # other members need a new plan
        st, err = tf.send(fx.px, "PATCH", f"/v1/fleet/deployments/{did}", {"members": [r1, g]})
        assert st == 400 and "split.front.node is the front's member" in \
            err["error"]["message"], err
        # a Split recorded without a plan is kept and does not start
        d = fx.record({"model": "fake-m", "type": "split", "api_id": "bare", "members": [g, r1]})
        assert d["status"] == "stopped" and "split" not in d and \
            "without a plan" in d["status_text"], d
        st, err = tf.send(fx.px, "POST", f"/v1/fleet/deployments/{d['id']}/actions/start", {})
        assert st == 409 and tf.ok(err, "error")["error"]["type"] == "no_plan", err
        ids = [m["id"] for m in json.loads(fx.px.get("/v1/models")[2])["data"]]
        assert "bare" not in ids and "half" not in ids, ids
        # a plan that names another node than the member, or a wildcard, is refused
        for change, word in (({"front": dict(sp["split"]["front"], node=r1)},
                              "split.front.node is the front's member"),
                             ({"link": dict(sp["split"]["link"], listen="0.0.0.0:1")},
                              "every interface; name one IP"),
                             ({"cut": 13}, "split is the plan of")):
            st, err = tf.send(fx.px, "POST", "/v1/fleet/deployments",
                              {"model": "fake-m", "type": "split", "api_id": "x",
                               "members": [g, r1], "split": dict(sp["split"], **change)})
            assert st == 400 and word in err["error"]["message"], (change, err)
        assert splitrun.KEY_BYTES >= 32
    finally:
        fx.close()
        os.unlink(est)


if __name__ == "__main__":
    run(globals())
