"""SRV-742: the checks of a Replicas deployment (SRV-741, proxy/canary.py, proxy/deployments.py):
each member's identity through its TandemNode agent, the output canary, mixed windows and the
cache routes on the member that holds the conversation.

Two virtual nodes, each an agent on a loopback port with a fake engine in this process
(tests/fake_backend.py) that says engine API 1.1 and answers `GET /v1/replica/identity` with a
block the test sets. The engine a box serves on :8000 is never addressed.

  1  identity: a member whose block differs in one field is `mismatch` and names the field, once
     for each kind of field (the release, a digest, the family and layer count, a decode
     setting, the tokenizer); `max_len` alone is no mismatch; a mismatched member takes no
     request; one window: no prompt is counted                        test_identity_fields
  2  an engine that says no engine API (1.0) is never asked: "identity not available", and the
     light check (the release) still finds a mismatch                  test_identity_not_available
  3  503 identity_pending is asked again until the block comes; a block that never comes ends
     the read, bounded                                                 test_identity_pending
  4  the canary: same tokens pass; another token is a mismatch until a round passes; a near tie
     the reference does not flip on is a mismatch, one it flips on (an A/A control of two more
     runs) the warning near_tie; its requests are greedy, thinking off, 32 tokens, marked
     internal, and none is a ledger row; a member with a request in flight defers the round; a
     member state change makes it due                                  test_canary
  5  mixed windows: a request past the home member's max_len goes to one that fits, else 400
     context_length_exceeded with the largest window                   test_windows
  6  POST /v1/cache/peek, /prewarm and /release with a conversation go to its sticky member and
     leave the conversation's home where it was; without a model: 400  test_cache_routes

    python tests/test_replica_checks.py
"""

from __future__ import annotations

import copy
import json
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_deployments as td  # noqa: E402
import test_fleet as tf  # noqa: E402
from proxy import canary as canary_mod  # noqa: E402
from proxy_util import run  # noqa: E402
from tandemapp import ledger as ledger_mod  # noqa: E402

BLOCK = {"identity": 1, "served_model": "fake-r", "base_model": "/models/fake", "version": "0.15.4",
         "code": "c0de0000c0de0000", "family": "fake", "layers": 48, "profile": "exl3-3.05",
         "weights": "a1a1a1a1a1a1a1a1", "drafters": "d2d2d2d2d2d2d2d2", "rows": "b3b3b3b3b3b3b3b3",
         "tokenizer": "e4e4e4e4e4e4e4e4",
         "decode": {"tree": True, "sampled_tree": None, "reasoning_format": "deepseek",
                    "thinking_default": "on", "temperature": 0.0, "top_p": 1.0, "top_k": 0,
                    "parallel": 1, "kv_fp8": False},
         "max_len": 32768, "stage": None}


def _fleet(gold_api="1.1", r1_api="1.1", block=BLOCK):
    nodes = {"Gold": td.Node("RCK-G-0001", "g", ["fake-r"], kind="tandemllm"),
             "R-1": td.Node("RCK-1-0001", "r1", ["fake-r"], kind="tandemllm")}
    for n, api in ((nodes["Gold"], gold_api), (nodes["R-1"], r1_api)):
        n.srv.fake.health = {"engine_api": api} if api else {}
        n.srv.fake.identity = copy.deepcopy(block)
    fx = td.Fleet(nodes)
    fx.record({"model": "fake-r", "type": "replicas", "members": [fx.ids["Gold"], fx.ids["R-1"]],
               "api_id": "qwen-r"})
    return fx


def _checked(fx, reread=False):
    """The deployment once each member's identity is read."""
    deps = fx.f.deps
    if reread:
        with deps.identities._lock:
            deps.identities._d.clear()
    deps._ctx = None
    td._dep(fx, "qwen-r")
    deps.identities.settle()
    deps._ctx = None
    return td._dep(fx, "qwen-r")


def _members(d):
    return {m["alias"]: m for m in d["member_states"]}


# ---------------------------------------------------------------------------- 1. identity

def test_identity_fields():
    fx = _fleet()
    try:
        d = _checked(fx)
        ms = _members(d)
        assert d["status"] == "serving", d
        for a in ("Gold", "R-1"):
            c = ms[a]["check"]
            assert (c["identity"], c["window"], c["field"]) == ("read", 32768, None), ms[a]
        assert ms["R-1"]["check"]["identity_text"] == "same identity as Gold", ms
        # the record lists its engines' model once, under its API id: an engine's own card
        # (owned_by the engine, not the backend) is covered too
        st, _, raw = fx.px.get("/v1/models")
        ids = [m["id"] for m in json.loads(raw)["data"]]
        assert st == 200 and ids == ["qwen-r"], ids
        # one window on both: the gateway counts no prompt
        fx.nodes["Gold"].srv.fake.tokenize = True
        assert fx.chat("qwen-r", conv="w-1", extra={"max_tokens": 50})[0] == 200
        assert fx.nodes["Gold"].srv.fake.counted == 0
        cases = (("version", "0.15.5", "version"), ("code", "ffff0000ffff0000", "code"),
                 ("weights", "0000000000000000", "weights"), ("family", "other", "family"),
                 ("layers", 64, "layers"), ("tokenizer", "9999999999999999", "tokenizer"),
                 ("decode", dict(BLOCK["decode"], temperature=0.6), "decode.temperature"),
                 ("drafters", None, "drafters"), ("rows", "7777777777777777", "rows"))
        for key, value, field in cases:
            fx.nodes["R-1"].srv.fake.identity = dict(copy.deepcopy(BLOCK), **{key: value})
            d = _checked(fx, reread=True)
            m = _members(d)["R-1"]
            assert m["state"] == "mismatch" and not m["routable"], (key, m)
            assert m["check"]["identity"] == "mismatch" and m["check"]["field"] == field, (key, m)
            assert f"in {field}:" in m["text"] and "Gold" in m["text"], (key, m)
            assert d["status"] == "degraded", d
            homes = {fx.chat("qwen-r", conv=f"{key}-{i}")[1]["X-Tandem-Node"] for i in range(6)}
            assert homes == {"Gold"}, (key, homes)
        # max_len alone is no mismatch: both serve, each with its window
        fx.nodes["R-1"].srv.fake.identity = dict(copy.deepcopy(BLOCK), max_len=8192)
        d = _checked(fx, reread=True)
        ms = _members(d)
        assert [ms[a]["state"] for a in ("Gold", "R-1")] == ["serving", "serving"], ms
        assert (ms["Gold"]["check"]["window"], ms["R-1"]["check"]["window"]) == (32768, 8192)
        assert d["status"] == "serving", d
        # the differ rule itself: a field the controller does not know still counts
        assert canary_mod.differs(BLOCK, dict(BLOCK, new_field=1))[0] == "new_field"
        assert canary_mod.differs(BLOCK, dict(BLOCK, max_len=1)) is None
        tf.ok({"contract_version": "1.0", **d}, "deployment")
    finally:
        fx.close()


def test_identity_not_available():
    fx = _fleet(r1_api=None)
    try:
        d = _checked(fx)
        ms = _members(d)
        r1 = ms["R-1"]
        assert r1["check"]["identity"] == "not_available", r1
        assert "identity not available" in r1["check"]["identity_text"] and "1.0" in \
            r1["check"]["identity_text"], r1
        assert fx.nodes["R-1"].srv.fake.identity_reads == 0        # never asked
        assert r1["state"] == "serving" and ms["Gold"]["check"]["identity"] == "read", ms
        # the light check stays: another release is a mismatch
        src = fx.f.source(fx.px.svc.cfg.node(fx.ids["R-1"]))
        src.version = lambda: "0.16.0"
        fx.f.deps._ctx = None
        r1 = _members(td._dep(fx, "qwen-r"))["R-1"]
        assert r1["state"] == "mismatch" and "Replicas members run one release" in r1["text"], r1
        assert r1["check"]["identity"] == "not_available", r1
    finally:
        fx.close()


def test_identity_pending():
    fx = _fleet()
    old = (canary_mod.PENDING_WAIT_S, canary_mod.PENDING_POLL_S)
    try:
        canary_mod.PENDING_POLL_S = 0.05
        fx.nodes["R-1"].srv.fake.identity_pending = 3
        d = _checked(fx, reread=True)
        r1 = _members(d)["R-1"]
        assert r1["check"]["identity"] == "read" and r1["state"] == "serving", r1
        assert fx.nodes["R-1"].srv.fake.identity_reads == 4, fx.nodes["R-1"].srv.fake.identity_reads
        # never ready: the read ends after PENDING_WAIT_S and says so; the light check holds
        canary_mod.PENDING_WAIT_S = 0.3
        fx.nodes["R-1"].srv.fake.identity_pending = 10 ** 6
        d = _checked(fx, reread=True)
        r1 = _members(d)["R-1"]
        assert r1["check"]["identity"] == "failed", r1
        assert "still pending after" in r1["check"]["identity_text"], r1
        assert r1["state"] == "serving", r1
    finally:
        canary_mod.PENDING_WAIT_S, canary_mod.PENDING_POLL_S = old
        fx.close()


# ---------------------------------------------------------------------------- 4. the canary

def _row(tok, other=None, gap=3.0, lp=-0.5):
    return {"token": tok, "logprob": lp,
            "top_logprobs": [{"token": tok, "logprob": lp},
                             {"token": other or "zz", "logprob": lp - gap}]}


def _base(i):
    return [f"p{i}t{j}" for j in range(5)]


class Answers:
    """A fake engine's canary answers: prompt i's tokens, with a change at token 2 of prompt 0
    (`at2`: a list of (token, other, gap) for its 1st, 2nd, 3rd ... call)."""

    def __init__(self):
        self.at2 = None
        self.calls = []
        self.lock = threading.Lock()

    def __call__(self, body, headers):
        if headers.get("User-Agent") != canary_mod.CANARY_UA:
            return None
        text = body["messages"][-1]["content"]
        i = canary_mod.CANARY_PROMPTS.index(text)
        with self.lock:
            self.calls.append((i, headers, body))
            n = sum(1 for c in self.calls if c[0] == i)
        toks = _base(i)
        rows = [_row(t) for t in toks]
        if i == 0 and self.at2:
            tok, other, gap = self.at2[min(n, len(self.at2)) - 1]
            toks[2] = tok
            rows[2] = _row(tok, other, gap)
        return {"content": "".join(toks), "logprobs": rows}


def test_canary():
    fx = _fleet()
    try:
        deps = fx.f.deps
        ref, mem = Answers(), Answers()
        fx.nodes["Gold"].srv.fake.script = ref
        fx.nodes["R-1"].srv.fake.script = mem
        _checked(fx)
        before = len(fx.px.rows())
        # the same tokens: pass
        got = deps.canary.run("qwen-r")
        assert got["ran"] and set(got["verdicts"].values()) == {"pass"}, got
        assert len(ref.calls) == len(mem.calls) == 3
        for _i, h, b in ref.calls + mem.calls:
            assert h["User-Agent"] == "tandem-controller/canary" and h["X-Tandem"] == "raw", h
            assert (b["temperature"], b["max_tokens"], b["stream"]) == (0, 32, False), b
            assert b["chat_template_kwargs"] == {"enable_thinking": False}, b
            assert b["logprobs"] is True and b["top_logprobs"] == 2, b
            assert ledger_mod.client_of(h)[1] == "internal"
        assert len(fx.px.rows()) == before                      # not one ledger row
        m = _members(td._dep(fx, "qwen-r"))["R-1"]
        assert m["check"]["canary"] == "pass" and m["state"] == "serving", m
        # another token: mismatch, no request, until a round passes again
        mem.at2 = [("other", "p0t2", 2.0)]
        assert deps.canary.run("qwen-r")["verdicts"][_bid(fx, "R-1")] == "mismatch"
        fx.f.deps._ctx = None
        d = td._dep(fx, "qwen-r")
        m = _members(d)["R-1"]
        assert m["state"] == "mismatch" and not m["routable"], m
        assert m["text"].startswith("canary: canary 1 differs from Gold at token 2"), m
        assert {fx.chat("qwen-r", conv=f"c-{i}")[1]["X-Tandem-Node"] for i in range(6)} == {"Gold"}
        mem.at2 = None
        assert deps.canary.run("qwen-r")["verdicts"][_bid(fx, "R-1")] == "pass"
        assert _members(td._dep(fx, "qwen-r"))["R-1"]["state"] == "serving"
        # a near tie the reference does not flip on: the A/A control, then mismatch
        mem.at2 = [("y", "x", 0.03)]
        ref.at2 = [("x", "y", 0.04)]
        ref.calls.clear()
        got = deps.canary.run("qwen-r")
        assert got["verdicts"][_bid(fx, "R-1")] == "mismatch", got
        assert sum(1 for c in ref.calls if c[0] == 0) == 3     # one, then two A/A runs
        m = _members(td._dep(fx, "qwen-r"))["R-1"]
        assert m["state"] == "mismatch" and "near tie" in m["text"] and \
            "writes its own token" in m["text"], m
        # the reference flips on its own in the A/A control: near_tie, a warning only
        ref.calls.clear()
        ref.at2 = [("x", "y", 0.04), ("x", "y", 0.04), ("y", "x", 0.04)]
        got = deps.canary.run("qwen-r")
        assert got["verdicts"][_bid(fx, "R-1")] == "near_tie", got
        d = td._dep(fx, "qwen-r")
        m = _members(d)["R-1"]
        assert m["state"] == "serving" and m["routable"] and m["check"]["canary"] == "near_tie", m
        assert "near tie" in d["warn"] and "flips there itself" in d["warn"], d
        tf.ok({"contract_version": "1.0", **d}, "deployment")
        # a gap over TIE_NATS is no tie: mismatch without an A/A run
        ref.at2, mem.at2 = [("x", "y", 0.2)], [("y", "x", 0.2)]
        ref.calls.clear()
        assert deps.canary.run("qwen-r")["verdicts"][_bid(fx, "R-1")] == "mismatch"
        assert sum(1 for c in ref.calls if c[0] == 0) == 1
        ref.at2 = mem.at2 = None
        assert deps.canary.run("qwen-r")["verdicts"][_bid(fx, "R-1")] == "pass"
        # busy: a request in flight on a member defers the round, the verdict kept
        bg = _bid(fx, "Gold")
        k = td._conv_home(fx, "qwen-r", bg, 700)
        slow = []
        t = threading.Thread(target=lambda: slow.append(fx.chat("qwen-r", "FAKE_SLOW_TTFT 2",
                                                                conv=k)))
        t.start()
        assert td._until(lambda: fx.px.svc.relay.inflight.count(bg) == 1, 5)
        n = len(ref.calls) + len(mem.calls)
        got = deps.canary.run("qwen-r")
        assert not got["ran"] and got["note"] == "Gold has 1 request in flight", got
        assert len(ref.calls) + len(mem.calls) == n                 # nothing sent
        assert deps.canary._due["qwen-r"] > deps.clock()
        assert _members(td._dep(fx, "qwen-r"))["R-1"]["check"]["canary"] == "pass"
        t.join(15)
        assert slow[0][0] == 200
        # a member state change makes the canary due at once
        deps.canary._due["qwen-r"] = deps.clock() + 600
        assert tf.send(fx.px, "POST", f"/v1/fleet/nodes/{fx.ids['R-1']}/actions/drain", {})[0] \
            == 200
        fx.f.deps._ctx = None
        td._dep(fx, "qwen-r")
        assert deps.canary._due["qwen-r"] == 0.0
    finally:
        fx.close()


def _bid(fx, alias):
    rec = fx.px.svc.cfg.deployment("qwen-r")
    return next(m["backend"] for m in rec["members"] if m["node"] == fx.ids[alias])


# ---------------------------------------------------------------------------- 5. windows

def test_windows():
    fx = _fleet()
    try:
        fx.nodes["Gold"].srv.fake.identity = dict(copy.deepcopy(BLOCK), max_len=100)
        fx.nodes["R-1"].srv.fake.identity = dict(copy.deepcopy(BLOCK), max_len=1000)
        for n in fx.nodes.values():
            n.srv.fake.tokenize = True
        d = _checked(fx)
        assert [m["state"] for m in d["member_states"]] == ["serving", "serving"], d
        k = td._conv_home(fx, "qwen-r", _bid(fx, "Gold"))
        text = " ".join(["word"] * 10)                 # 14 tokens with the message's 4
        st, h, _ = fx.chat("qwen-r", text, conv=k, extra={"max_tokens": 50})
        assert st == 200 and h["X-Tandem-Node"] == "Gold", (st, h)
        assert fx.nodes["Gold"].srv.fake.counted == 1   # counted by the home member's engine
        k2 = td._conv_home(fx, "qwen-r", _bid(fx, "Gold"), 100)
        st, h, _ = fx.chat("qwen-r", text, conv=k2, extra={"max_tokens": 200})
        assert st == 200 and h["X-Tandem-Node"] == "R-1", (st, h)
        st, _, raw = fx.chat("qwen-r", text, conv=k, extra={"max_tokens": 5000})
        err = json.loads(raw)["error"]
        assert st == 400 and err["code"] == "context_length_exceeded", (st, err)
        assert "need 5014 tokens" in err["message"] and "1000 tokens" in err["message"], err
    finally:
        fx.close()


# ---------------------------------------------------------------------------- 6. cache routes

def test_cache_routes():
    fx = _fleet()
    try:
        for n in fx.nodes.values():
            n.srv.fake.peek = {"tier": "resident", "cached": 12}
        _checked(fx)
        br = _bid(fx, "R-1")
        sticky = fx.f.deps._sticky_of("qwen-r")
        # a conversation whose hash home is Gold but whose turns R-1 served
        k = td._conv_home(fx, "qwen-r", _bid(fx, "Gold"), 900)
        sticky.put(k, br)
        msgs = [{"role": "user", "content": "hello there"}]
        st, h, raw = fx.px.post("/v1/cache/peek", {"model": "qwen-r", "messages": msgs,
                                                   "session": k})
        assert st == 200 and json.loads(raw)["tier"] == "resident", (st, raw)
        assert (h["X-Tandem-Node"], h["X-Tandem-Deployment"]) == ("R-1", "qwen-r"), h
        r1 = fx.nodes["R-1"].srv.fake
        assert r1.bodies[-1]["model"] == "fake-r" and r1.bodies[-1]["session"] == k
        st, h, _ = fx.px.post("/v1/cache/prewarm", {"model": "qwen-r", "messages": msgs,
                                                    "session": k})
        assert st == 200 and h["X-Tandem-Node"] == "R-1" and r1.prewarms == 1, h
        st, h, raw = fx.px.post("/v1/cache/release", {"model": "qwen-r", "bytes": 1024,
                                                      "conversation_id": k})
        assert st == 200 and h["X-Tandem-Node"] == "R-1" and r1.releases == 1, (st, raw)
        assert fx.nodes["Gold"].srv.fake.releases == 0 and fx.nodes["Gold"].srv.fake.prewarms == 0
        # a peek does not make or move a home
        k2 = td._conv_home(fx, "qwen-r", br, 950)
        st, h, _ = fx.px.post("/v1/cache/peek", {"model": "qwen-r", "messages": msgs,
                                                 "session": k2})
        assert st == 200 and h["X-Tandem-Node"] == "R-1" and sticky.get(k2) is None, h
        assert sticky.get(k) == br
        st, _, raw = fx.px.post("/v1/cache/peek", {"messages": msgs, "session": k})
        assert st == 400 and json.loads(raw)["error"]["code"] == "model_required", raw
    finally:
        fx.close()


if __name__ == "__main__":
    run(globals())
