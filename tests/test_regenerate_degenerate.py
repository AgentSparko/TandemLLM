"""A reasoning-only answer makes the next identical request sample (degenerate retries).

An agent client retries by re-sending the byte-identical conversation whose tail is a tool
result. `signature` cannot match it (a regenerate needs a user tail), so greedy replay returns
the identical reasoning-only continuation forever. The fix: a completion that wrote reasoning
and nothing usable is remembered by its retry signature, and the next request with that
signature samples like a regenerate. A good answer clears the mark; the header off switch
never matches; a client that names sampling decides as ever.

Run: python tests/test_regenerate_degenerate.py
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server import regenerate as rg  # noqa: E402

SYS = {"role": "system", "content": "You are helpful."}
U1 = {"role": "user", "content": "Read the file and tell me what changed."}
AS = {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "type": "function",
        "function": {"name": "read", "arguments": "{}"}}]}
TR = {"role": "tool", "tool_call_id": "1", "content": "the file contents"}
H = {"x-session-id": "sess-1"}


def _r(**kw):
    return rg.Regenerates(**kw)


def test_retry_signature():
    msgs = [SYS, U1, AS, TR]
    s = rg.retry_signature(msgs)
    assert s is not None and s == rg.retry_signature([dict(m) for m in msgs])
    assert rg.signature(msgs) is None                      # tool tail: not a classic regenerate
    assert rg.retry_signature([SYS, U1, AS]) is None       # assistant tail (Continue): not a retry
    assert rg.retry_signature([SYS]) is None               # no conversation
    assert rg.retry_signature(None) is None
    # reasoning fields are presentation: the same turn hashes the same
    assert rg.retry_signature([SYS, U1, dict(AS, reasoning_content="hmm"), TR]) == s
    print("ok: retry_signature")


def test_degenerate_completion():
    assert rg.degenerate_completion("stop", 0, 449, 451)          # thought, wrote nothing
    assert not rg.degenerate_completion("stop", 0, 449, 900)       # visible content after
    assert not rg.degenerate_completion("stop", 2, 449, 451)       # a tool call is an answer
    assert not rg.degenerate_completion("stop", 0, 0, 40)          # no thinking at all
    assert not rg.degenerate_completion("length", 0, 449, 451)     # cut, not degenerate
    assert not rg.degenerate_completion("tool_calls", 1, 449, 451)
    print("ok: degenerate_completion")


def test_degenerate_retry_samples():
    r = _r()
    body = {"messages": [SYS, U1, AS, TR]}
    d1 = r.decide(body, H)
    assert d1.how is None and not d1.sample and d1.rsig is not None
    r.degenerate(d1, "req-1")                                     # the degenerate completion
    d2 = r.decide(body, H)                                        # the agent's identical retry
    assert d2.how == "degenerate" and d2.sample and d2.of == "req-1"
    assert r.sampling()["temperature"] == 0.7 and "seed" in r.sampling()
    assert r.counts["degenerate"] == 1
    print("ok: degenerate retry samples")


def test_good_answer_clears_the_mark():
    r = _r()
    body = {"messages": [SYS, U1, AS, TR]}
    d1 = r.decide(body, H)
    r.degenerate(d1, "req-1")
    d2 = r.decide(body, H)
    assert d2.how == "degenerate"
    r.answered(d2, "req-2")                                       # the retry answered
    d3 = r.decide(body, H)                                        # a deliberate re-send
    assert d3.how is None and not d3.sample                       # greedy again, as asked
    print("ok: good answer clears the mark")


def test_off_header_and_client_params():
    r = _r()
    body = {"messages": [SYS, U1, AS, TR]}
    d1 = r.decide(body, H)
    r.degenerate(d1, "req-1")
    d2 = r.decide(body, {**H, rg.HEADER: "0"})
    assert d2.how is None and d2.rsig is None and not d2.sample   # off means off
    d3 = r.decide(dict(body, temperature=0.3), H)
    assert d3.how == "degenerate" and not d3.sample               # the client decides
    assert r.counts["client_params"] == 1
    print("ok: off header and client params")


def test_bounds_and_ttl():
    clock = [1000.0]
    r = _r(sessions=2, per=2, ttl_s=10.0, clock=lambda: clock[0])
    body = {"messages": [SYS, U1, AS, TR]}
    d1 = r.decide(body, H)
    r.degenerate(d1, "req-1")
    clock[0] += 11.0                                              # past the ttl
    assert r.decide(body, H).how is None
    clock[0] -= 11.0
    r.degenerate(r.decide(body, H), "req-2")
    for i in range(3):                                            # more sessions than the cap
        r.degenerate(r.decide({"messages": [SYS, dict(U1, content=f"q{i}"), AS, TR]},
                              {"x-session-id": f"s{i}"}), f"r{i}")
    assert len(r._d) <= 2
    print("ok: bounds and ttl")


def test_tool_tail_answered_keeps_retry_memory_of_user_tail():
    # a user-tail conversation keeps BOTH memories working: the classic regenerate still
    # samples after a good answer, and a degenerate one samples through the new path too
    r = _r()
    body = {"messages": [SYS, U1]}
    d1 = r.decide(body, H)
    r.answered(d1, "req-1")
    assert r.decide(body, H).how == "detected"                    # classic path untouched
    print("ok: classic regenerate path untouched")


def run():
    test_retry_signature()
    test_degenerate_completion()
    test_degenerate_retry_samples()
    test_good_answer_clears_the_mark()
    test_off_header_and_client_params()
    test_bounds_and_ttl()
    test_tool_tail_answered_keeps_retry_memory_of_user_tail()
    print("regenerate degenerate-retry: all ok")


if __name__ == "__main__":
    run()
    sys.exit(0)