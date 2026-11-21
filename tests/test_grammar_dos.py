"""A request's regex is bounded: it cannot ask for a billion automaton states.

An audit found `structured_outputs: {"regex": ...}` accepted from any request: nested
repetitions compose — `((\\d{1000}){1000}){1000}` asks the NFA builder for ~10^9 states,
built on the handler's thread, and the box dies of OOM before the DFA's own cap (20,000
states) is ever asked. Two walls answer for it: the door caps the pattern's length (the
act path's own limit), and the builder stops at a total state budget.

  * nested repetitions die at the state budget: the refusal names the budget and arrives
    while the automaton is still small — measured, not assumed;
  * a request regex longer than the limit is a 400 that names the field;
  * what legitimate traffic sends still compiles: `json_object`, a realistic
    `json_schema`, the tool-call language for twenty tools.

Run: python tests/test_grammar_dos.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import grammar as G  # noqa: E402
from server import compat  # noqa: E402


def _compile_counting(pattern):
    """`compile_regex(pattern)`, counting the states the NFA builder created.
    Returns (dfa or None, GrammarError or None, states created)."""
    seen = [0]
    orig = G._NFA.state

    def counting(self):
        s = orig(self)
        seen[0] = s + 1
        return s

    G._NFA.state = counting
    try:
        try:
            return G.compile_regex(pattern), None, seen[0]
        except G.GrammarError as exc:
            return None, exc, seen[0]
    finally:
        G._NFA.state = orig


def test_nested_repetitions_die_at_the_state_budget():
    # (\\d{1000}){300} asks for 300k NFA states: the same composition as the audit's
    # ((\\d{1000}){1000}){1000}, at a size a test may build while it proves the wall.
    dfa, err, states = _compile_counting(r"(\d{1000}){300}")
    assert dfa is None and err is not None, "the builder took the request's pattern whole"
    assert "state budget" in str(err), f"refused for another reason: {err}"
    assert states <= G.NFA_STATE_BUDGET, f"the builder built {states} states past its budget"


def test_a_long_request_regex_is_refused_at_the_door():
    big = "a" * (compat.ACT_LIMITS["regex_chars"] + 1)
    try:
        got = compat.structured_pattern({"structured_outputs": {"regex": big}})
        raise AssertionError(f"a regex of {len(big)} characters passed as a pattern: {got!r}")
    except compat.Refusal as exc:
        assert exc.param == "structured_outputs", f"the 400 names {exc.param!r}: {exc}"
    # one at the limit is a pattern like any other
    ok = "a" * compat.ACT_LIMITS["regex_chars"]
    assert compat.structured_pattern({"structured_outputs": {"regex": ok}}) == ok


def test_what_legitimate_traffic_sends_still_compiles():
    G.compile_regex(G.json_object_regex(3))          # response_format: json_object
    schema = {"type": "object",
              "properties": {"title": {"type": "string", "maxLength": 100},
                             "author": {"type": "string", "maxLength": 100},
                             "year": {"type": "integer"},
                             "tags": {"type": "array", "maxItems": 4,
                                      "items": {"type": "string",
                                                "enum": ["sci-fi", "fantasy", "history"]}},
                             "summary": {"type": "string", "maxLength": 100},
                             "available": {"type": "boolean"}},
              "required": ["title", "author", "year", "summary"]}
    G.compile_regex(G.WS + G.schema_regex(schema) + G.WS)
    tools = [{"type": "function", "function": {
        "name": f"tool_{i}",
        "parameters": {"type": "object",
                       "properties": {"path": {"type": "string"},
                                      "content": {"type": "string", "maxLength": 4096},
                                      "mode": {"type": "string",
                                               "enum": ["a", "bb", "ccc", "dddd"]},
                                      "depth": {"type": "integer"}},
                       "required": ["path", "content"]}}} for i in range(20)]
    pat, _ = G.tool_call_regex(tools, [t["function"]["name"] for t in tools])
    G.compile_regex(pat)                              # the twenty-tool call language


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  {name:56s} ok")
            passed += 1
    print(f"{passed} passed")