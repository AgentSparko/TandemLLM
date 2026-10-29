"""Conversation affinity (SRV-467, app/proxy/affinity.py; a Replicas deployment's since SRV-731): the
key, the hash, the table, the policies, and the requests never replayed on another member.

The key order is pinned against the engine's own functions, read out of server/app.py (no torch:
the two functions are compiled from the source alone), over a matrix of bodies and headers.

Run: python tests/test_proxy_affinity.py
"""

from __future__ import annotations

import ast
import email.message
import itertools
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(1, os.path.join(ROOT, "app"))  # SRV-747: TandemApp's packages, until ENG-413
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(ROOT, "app", "tests"))  # SRV-747: TandemApp's test helpers, until ENG-413

from proxy import affinity  # noqa: E402
from proxy import deployments  # noqa: E402
from proxy_util import run  # noqa: E402

APP = os.path.join(ROOT, "server", "app.py")


def _engine_key():
    """The engine's key function: `conversation_id(body, headers) or _session_header(headers)`,
    both compiled from server/app.py as they stand."""
    src = open(APP).read()
    tree = ast.parse(src)
    ns: dict = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in ("conversation_id",
                                                               "_session_header"):
            exec(compile(ast.Module([node], []), APP, "exec"), ns)
    assert {"conversation_id", "_session_header"} <= set(ns), "the engine's key functions moved"
    # the order the engine combines them in, where it files a request and where the peek reads
    assert "rec.conv = conversation_id(body, self.headers) or _session_header(self.headers)" in src
    assert "(session or conversation_id(body, headers) or _session_header(headers) or None)" in src
    return lambda body, headers: ns["conversation_id"](body, headers) or \
        ns["_session_header"](headers)


def _headers(**kv) -> email.message.Message:
    m = email.message.Message()
    for k, v in kv.items():
        m[k.replace("_", "-")] = v
    return m


def test_the_key_is_the_engines_own():
    engine = _engine_key()
    bodies = [{}, {"conversation_id": "conv-1"}, {"session_id": "sess-1"}, {"user": "u-1"},
              {"metadata": {"conversation_id": "meta-c"}}, {"metadata": {"session_id": "meta-s"}},
              {"conversation_id": "", "user": "u-2"}, {"conversation_id": "x" * 300},
              {"metadata": "not a dict", "session_id": 7}]
    hsets = [{}, {"X-Conversation-Id": "hc"}, {"x-session-id": "chat-7"},
             {"X-OpenWebUI-Chat-Id": "owui-9"},
             {"X-Conversation-Id": "hc", "x-session-id": "chat-7", "X-OpenWebUI-Chat-Id": "o"},
             {"x-session-id": "chat-7-task-2", "X-OpenWebUI-Chat-Id": "o"},
             {"X-Session-Id": "S" * 200}]
    n = 0
    for body, hs in itertools.product(bodies, hsets):
        h = _headers(**{k.replace("-", "_"): v for k, v in hs.items()})
        assert affinity.conversation_key(body, h) == engine(body, h), (body, hs)
        n += 1
    assert n == len(bodies) * len(hsets)
    # the precedence the scenario names: three different ids, the engine's choice wins
    h = _headers(x_session_id="s", X_Conversation_Id="c", X_OpenWebUI_Chat_Id="o")
    assert affinity.conversation_key({}, h) == "c" == engine({}, h)
    assert affinity.conversation_key({}, _headers(X_OpenWebUI_Chat_Id="o")) == "c:o"
    assert affinity.conversation_key({}, None) is None


def test_root_keeps_a_chat_and_its_workers_together():
    assert affinity.policy_key("chat-7-task-2", "root") == "chat-7"
    assert affinity.policy_key("chat-7-task-12", "root") == "chat-7"
    assert affinity.policy_key("chat-7", "root") == "chat-7"
    assert affinity.policy_key("chat-7-task-2", "own") == "chat-7-task-2"
    assert affinity.policy_key(None, "root") is None
    for n in range(1, 30):
        assert affinity.rendezvous(affinity.policy_key(f"chat-x-task-{n}"), ["self", "b"]) == \
            affinity.rendezvous("chat-x", ["self", "b"])


def test_rendezvous_spreads_and_is_stable():
    members = ["self", "sparkb"]
    keys = [f"chat-{i}" for i in range(1000)]
    homes = {k: affinity.rendezvous(k, members) for k in keys}
    share = sum(1 for v in homes.values() if v == "self") / len(keys)
    assert 0.40 <= share <= 0.60, share                   # G19: each member 40-60 %
    # a third member takes only its own keys from the others; removing it gives them back
    three = {k: affinity.rendezvous(k, members + ["sparkc"]) for k in keys}
    moved = [k for k in keys if three[k] != homes[k]]
    assert all(three[k] == "sparkc" for k in moved)
    assert 0.25 <= len(moved) / len(keys) <= 0.42, len(moved)
    assert {k: affinity.rendezvous(k, members) for k in keys} == homes
    # order of the member list does not matter; ranked() starts with the home
    assert all(affinity.rendezvous(k, members[::-1]) == homes[k] for k in keys[:50])
    assert affinity.ranked("chat-1", members)[0] == homes["chat-1"]


def test_not_first_healthy():
    """A first-healthy router would send every miss to the first member: the spread test
    catches it (the mutation the QA ticket asks for)."""
    def first_healthy(key, members):
        return members[0]
    keys = [f"k{i}" for i in range(1000)]
    share = sum(1 for k in keys if first_healthy(k, ["self", "b"]) == "self") / len(keys)
    assert not 0.40 <= share <= 0.60


def test_the_sticky_table():
    now = [1000.0]
    t = affinity.Sticky(ttl=60.0, cap=3, clock=lambda: now[0])
    t.put("a", "self")
    assert t.get("a") == "self" and t.turns("a") == 1
    t.put("a", "self")
    assert t.turns("a") == 2
    t.put("a", "b")                                       # a fallback rewrites it
    assert t.get("a") == "b" and t.turns("a") == 1
    assert not t.drop("a", "self") and t.get("a") == "b"
    assert t.drop("a", "b") and t.get("a") is None
    t.put("x", "b")
    now[0] += 61.0
    assert t.get("x") is None                             # 30 min in production, 60 s here
    for k in "pqrs":
        t.put(k, "b")
    assert t.get("p") is None and t.get("s") == "b"        # the oldest go past the cap
    assert t.counts() == {"b": 3} and len(t) == 3


def test_constrained_requests():
    c = deployments.constrained
    assert c({"structured_outputs": {"json": {}}})
    assert c({"response_format": {"type": "json_schema", "json_schema": {}}})
    assert c({"response_format": {"type": "json_object"}})
    assert not c({"response_format": {"type": "text"}})
    assert c({"guided_regex": "a+"}) and c({"grammar": "root ::= x"})
    assert c({"tool_choice": "required"}) and c({"tool_choice": {"type": "function"}})
    assert not c({"tool_choice": "auto"}) and not c({"messages": []}) and not c(None)


if __name__ == "__main__":
    run(globals())
