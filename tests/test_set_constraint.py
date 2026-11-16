"""SET_CONSTRAINT, engine-ext-v1 3.2 (ENG-458, QA ENG-459): an extension's constraint on the answer,
carried out by the engine's own grammar as the request field it is the same as.

The test matrix, the Gherkin of ENG-458 (a regex constraint; no act, no change; an invalid
constraint; a constraint beside response_format) and the contract's cases:

  1  3.2 holds 3.1: every key, list item and value of compat/3.1/ext.json is in ext.json (only the
     version and the summary line differ); SP4 adds SET_CONSTRAINT, the act adds `constraint`, the
     supervisor `constraint_outcomes`; the host speaks 3.2 and takes a 3.1 extension, not a 3.3 one
                                                                  test_3_2_holds_3_1
  2  the reference host (fake_host.py, fake_ext.py): a regex applied, its outcome to the
     supervisor; no act, no outcome; an invalid regex refused open (the order unchanged, the
     supervisor attached, counted, the other acts kept); strict: 400 SET_CONSTRAINT and finish;
     a wrong shape detaches; precedence (response_format, structured_outputs, tool_choice, the
     request's logit_bias, tool_choice none) gives way uncounted; one of a family; tool_args;
     the engine's switch off; a bias outside the vocabulary
                                                                  test_the_reference_host
  3  the engine's host (server/ext.py): constraint_of is fake_host's on every shape, a mapping or
     an object; a wrong shape detaches the supervisor and is counted under at_request; a right
     one is taken; a supervisor without constraint_outcomes stays attached
                                                                  test_the_engine_host_checks_the_shape
  4  server/compat.py act_constraint and fake_host.py check_constraint agree, applied or refused
     and counted or not, on a matrix of acts and requests (the engine's regex compile handed to
     both)                                                        test_the_engine_and_the_reference_agree
  5  the act is the field: the act's automaton (pattern, kind, typed parameters, branches,
     markers) is answer_constraint's of the request with `same_as`, for a regex, a choice, a
     schema, beside tools under auto, on a completion; tool_args one name is tool_choice that
     function, every name required; a logit bias is logit_bias's
                                                                  test_the_act_is_the_field
  6  no act, no change (G2 on the CPU model): the headless engine, an extension that answers no
     act and one whose only act is refused write the same tokens, plain and under response_format,
     both transports                                              test_no_act_no_change
  7  a regex, a choice, a schema and a logit bias from the act write the tokens the request field
     writes, both transports and a completion; the regex's answer is in its language
                                                                  test_the_act_writes_the_fields_tokens
  8  under speculation: the act's answer with a chain and a tree drafter (free drafts and the
     constrained answer's) is the drafter-less answer, drafts accepted
                                                                  test_the_act_is_exact_under_speculation
  9  an invalid constraint: the request runs unconstrained (the headless tokens), the outcome says
     why, counted as at_request; a constraint the vocabulary cannot write ends as the field's
     would, naming SET_CONSTRAINT; the routing row's extra holds the outcomes
                                                                  test_an_invalid_constraint_runs_unconstrained
 10  precedence: response_format with a regex act answers as response_format alone, the act gave
     way uncounted; strict: the 400 that names SET_CONSTRAINT, both transports
                                                                  test_precedence_and_strict
 11  tool_args forces a call to the named function; apply_request gets the other acts only, and
     is not asked for a list of SET_CONSTRAINT alone               test_tool_args_and_the_other_acts
 12  tools/set_constraint_check.py (the window's check on a served model): each pair's act is the
     same as its field and the two requests differ only there; the precedence and invalid pairs
     give way and are refused; its verdicts tell a log-probability apart
                                                                  test_the_window_tool

Run: python tests/test_set_constraint.py   (1-3 standard library; 4, 5, 12 numpy; 6-11 CPU torch)
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import sys
import types

for _k in ("NORM", "GDN", "HEAD", "ATTN", "GDNBLOCK", "GDNTREE", "GDNPRE"):
    os.environ.setdefault(f"QWEN38_FUSED_{_k}", "0")
os.environ.setdefault("QWEN38_TREE_CHAIN_DELEGATE", "0")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
DIR = os.path.join(ROOT, "contracts", "engine-ext-v1")

from server import ext as E  # noqa: E402


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"set_constraint_{name}", os.path.join(DIR, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _json(*parts):
    with open(os.path.join(DIR, *parts)) as f:
        return json.load(f)


PLAIN = ["plan", "prompt", "supervisor", "at_request", "extras start",
         "at_block", "extras block", "at_block", "extras block",
         "at_finish", "extras finish", "follow_ups", "finish", "done"]
TOOLS = [{"type": "function", "function": {"name": "pick", "parameters": {
             "type": "object", "properties": {"color": {"enum": ["red", "green"]}},
             "required": ["color"]}}},
         {"type": "function", "function": {"name": "stop", "parameters": {
             "type": "object", "properties": {}}}},
         {"type": "function", "function": {"name": "now"}}]
SCHEMA = {"type": "object", "properties": {"k": {"type": "string", "maxLength": 4},
                                           "n": {"type": "integer"}}, "required": ["k", "n"]}
MSG = {"messages": [{"role": "user", "content": "hello there"}]}


# ---------------------------------------------------------------------------- 1

def _holds(old, new, path: str, changed: set) -> list:
    """Where `new` lacks a part of `old` (a key, a list item, a value), outside `changed`."""
    if path in changed:
        return []
    if isinstance(old, dict):
        if not isinstance(new, dict):
            return [path]
        return [p for k, v in old.items()
                for p in ([f"{path}/{k}"] if k not in new else _holds(v, new[k], f"{path}/{k}", changed))]
    if isinstance(old, list):
        if not isinstance(new, list):
            return [path]
        return [f"{path}[{i}]" for i, x in enumerate(old) if x not in new]
    return [] if old == new else [path]


def test_3_2_holds_3_1():
    old, new = _json("compat", "3.1", "ext.json"), _json("ext.json")
    assert old["version"] == "3.1" and new["version"] == "3.2" == E.EXT_API, (old["version"], new["version"])
    lost = _holds(old, new, "", {"/version", "/about"})
    assert not lost, f"3.2 must hold every part of 3.1: {lost}"
    assert new["supervisor"]["safe_points"]["SP4"]["acts"] == \
        old["supervisor"]["safe_points"]["SP4"]["acts"] + ["SET_CONSTRAINT"]
    assert new["supervisor"]["act"]["fields"][-1] == "constraint" and "SET_CONSTRAINT" in new["supervisor"]["act"]["kinds"]
    assert [m["name"] for m in new["supervisor"]["optional_methods"]][-1] == "constraint_outcomes"
    assert "since_3_2" in new and "constraint" in new["failure"]
    c = new["supervisor"]["act"]["constraint"]
    assert set(c["same_as"]) == {"regex", "choice", "json_schema", "logit_bias", "tool_args"}
    assert list(c["fields"]) == ["kind", "value", "scope", "strict"]
    assert c["limits"]["bias_ids"] == 300 and c["limits"]["automaton_states"] == 20000
    assert _json("knobs.json")["version"] == "3.2"
    H = _load("fake_host")
    assert H.accepts("3.1") is None and E.accepts("3.1", E.EXT_API) is None, "a 3.1 extension starts"
    assert H.accepts("3.3") and E.accepts("3.3", E.EXT_API), "a newer minor does not"
    assert E.CONSTRAINT_KINDS == H.CONSTRAINT_KINDS and E.CONSTRAINT_SCOPES == H.CONSTRAINT_SCOPES
    assert c["fields"]["kind"] == " | ".join(E.CONSTRAINT_KINDS)
    return f"3.1's {len(json.dumps(old))} bytes held by 3.2; SP4, the act, the optional method added"


# ---------------------------------------------------------------------------- 2

def _serve(constraints=None, body=None, act=None, **host_kw):
    H, X = _load("fake_host"), _load("fake_ext")
    args = {"constraint": constraints or []}
    if act:
        args["act"] = act
    host = H.FakeHost(args=args, **host_kw)
    ext = H.load(X, host)
    rec = host.serve(ext, H.Scenario(body=dict(MSG, **(body or {}))))
    return rec, host, ext


def test_the_reference_host():
    # a regex: carried out, the order unchanged, the outcome to the supervisor
    rec, host, ext = _serve([{"kind": "regex", "value": "[0-9]{3}"}])
    assert rec["trace"] == PLAIN and rec["acts"] == [("SP4", "SET_CONSTRAINT")] and not rec["errors"], rec
    want = [{"kind": "regex", "scope": "answer", "strict": False, "applied": True, "why": None}]
    assert rec["constraints"] == want and ext.outcomes == [want], (rec["constraints"], ext.outcomes)
    # no act: no outcome, nothing asked
    rec, host, ext = _serve()
    assert rec["trace"] == PLAIN and rec["constraints"] == [] and ext.outcomes == [], rec
    assert "supervisor.constraint_outcomes" not in ext.calls
    # an invalid regex: open, counted, the supervisor stays, a note beside it is carried out
    rec, host, ext = _serve([{"kind": "regex", "value": "(a"}], act={"at_request": "PROMPT_TAIL_NOTE"})
    assert rec["status"] == 200 and rec["trace"] == PLAIN, rec
    assert rec["acts"] == [("SP4", "PROMPT_TAIL_NOTE"), ("SP4", "SET_CONSTRAINT")], rec["acts"]
    assert host.errors == {"at_request": 1} and not rec["constraints"][0]["applied"], (host.errors, rec)
    assert "does not compile" in rec["constraints"][0]["why"] and \
        any("SET_CONSTRAINT regex refused" in ln for ln in host.lines), host.lines
    # strict: the client's 400 naming SET_CONSTRAINT; the extension's finish is still asked
    rec, host, ext = _serve([{"kind": "regex", "value": "(a", "strict": True}])
    assert rec["status"] == 400 and rec["refusal"]["param"] == "SET_CONSTRAINT", rec
    assert rec["trace"] == ["plan", "prompt", "supervisor", "at_request", "finish"], rec["trace"]
    # a wrong shape: the failure rule's wrong shape, the supervisor detached, no act carried out
    for bad in ({"kind": "grammar", "value": "x"}, {"kind": "regex", "value": "x", "scope": "all"},
                {"kind": "regex", "value": "x", "strict": "yes"}):
        rec, host, ext = _serve([bad])
        assert rec["acts"] == [] and host.errors == {"at_request": 1} and "done" not in rec["trace"], (bad, rec)
        assert rec["trace"][-3:] == ["extras finish", "follow_ups", "finish"], rec["trace"]
    # precedence: the request's own constraint wins, uncounted
    for body, field in (({"response_format": {"type": "json_object"}}, "response_format"),
                        ({"structured_outputs": {"regex": "a"}}, "structured_outputs"),
                        ({"tools": TOOLS, "tool_choice": "required"}, "tool_choice")):
        rec, host, ext = _serve([{"kind": "choice", "value": ["x", "y"]}], body)
        o = rec["constraints"][0]
        assert not o["applied"] and field in o["why"] and not host.errors and rec["trace"] == PLAIN, (field, rec)
    rec, host, _ = _serve([{"kind": "logit_bias", "value": {"5": 3}}], {"logit_bias": {"6": 1}})
    assert not rec["constraints"][0]["applied"] and not host.errors, rec
    rec, host, _ = _serve([{"kind": "choice", "value": ["pick"], "scope": "tool_args"}],
                          {"tools": TOOLS, "tool_choice": "none"})
    assert not rec["constraints"][0]["applied"] and not host.errors, rec
    # one of a family; a grammar and a bias together
    rec, host, _ = _serve([{"kind": "regex", "value": "a"}, {"kind": "json_schema", "value": SCHEMA},
                           {"kind": "logit_bias", "value": {"5": -3}}])
    assert [o["applied"] for o in rec["constraints"]] == [True, False, True] and host.errors == {"at_request": 1}
    # tool_args: offered functions only, kind choice only
    rec, host, _ = _serve([{"kind": "choice", "value": ["pick"], "scope": "tool_args"}], {"tools": TOOLS})
    assert rec["constraints"][0]["applied"] and rec["constraints"][0]["scope"] == "tool_args", rec
    for c in ({"kind": "choice", "value": ["nope"], "scope": "tool_args"},
              {"kind": "regex", "value": "a", "scope": "tool_args"},
              {"kind": "logit_bias", "value": {"1": 1}, "scope": "answer"}):
        rec, host, _ = _serve([c], {"tools": TOOLS})
        assert not rec["constraints"][0]["applied"] and host.errors == {"at_request": 1}, (c, rec)
    # the engine's switch: the grammar kinds give way, a bias still applies
    rec, host, _ = _serve([{"kind": "regex", "value": "a"}, {"kind": "logit_bias", "value": {"1": 1}}],
                          structured=False)
    assert [o["applied"] for o in rec["constraints"]] == [False, True] and not host.errors, rec
    # a bias outside the vocabulary, over its bounds
    for v in ({"256": 1}, {"3": 101}, {"x": 1}):
        rec, host, _ = _serve([{"kind": "logit_bias", "value": v}], vocab=256)
        assert not rec["constraints"][0]["applied"] and host.errors == {"at_request": 1}, (v, rec)
    return "applied, absent, refused open, strict, wrong shapes, precedence x5, families, tool_args, " \
           "the switch, the vocabulary"


# ---------------------------------------------------------------------------- 3

SHAPES = [{"kind": "regex", "value": "a"}, {"kind": "regex", "value": "a", "scope": "answer", "strict": True},
          {"kind": "choice", "value": ["f"], "scope": "tool_args"}, {"kind": "logit_bias", "value": {}},
          {"kind": "json_schema", "value": None, "strict": False},
          None, {"kind": "grammar"}, {"value": "a"}, {"kind": "regex", "scope": "tools"},
          {"kind": "regex", "strict": 1}, {"kind": "regex", "strict": "true"}]


def test_the_engine_host_checks_the_shape():
    H = _load("fake_host")

    def verdict(fn, act):
        try:
            return fn(act)
        except ValueError:
            return "wrong shape"
    for c in SHAPES:
        act = {"kind": "SET_CONSTRAINT", "constraint": c}
        obj = types.SimpleNamespace(kind="SET_CONSTRAINT", constraint=types.SimpleNamespace(**c)
                                    if isinstance(c, dict) else None)
        assert verdict(E.constraint_of, act) == verdict(H.constraint_of, act) == \
            verdict(E.constraint_of, obj), c

    class Sup:
        def __init__(self, acts):
            self.acts_out = acts

        def at_request(self, event):
            return list(self.acts_out)

        def at_block(self, event, room):
            return None

    lines = []
    host = E.Host(mode="chat", engine_api="", state={}, lock=None, tokenizer=None, registry=None,
                  ledger_path=None, args={}, log=lines.append)
    loaded = E.Loaded(types.SimpleNamespace(NAME="t", VERSION="0", EXT_API="3.2"), types.SimpleNamespace(), host)
    note = {"kind": "PROMPT_TAIL_NOTE", "ids": [1], "visible": []}
    good = {"kind": "SET_CONSTRAINT", "constraint": {"kind": "regex", "value": "a"}}
    sup = E.Supervision(Sup([note, good]), E.Request(loaded, {}))
    assert sup.at_request({}) == [note, good] and not sup.detached
    assert sup.taken == [("SP4", "PROMPT_TAIL_NOTE"), ("SP4", "SET_CONSTRAINT")], sup.taken
    assert sup.constraint_outcomes([{"applied": True}]) is None and not sup.detached
    for bad in ({"kind": "SET_CONSTRAINT"}, {"kind": "SET_CONSTRAINT", "constraint": {"kind": "x"}}):
        sup = E.Supervision(Sup([note, bad]), E.Request(loaded, {}))
        assert sup.at_request({}) == [] and sup.detached, bad
        assert sup.at_block({}, 9) is None, "nothing more is asked"
    assert loaded.errors == {"at_request": 2}, loaded.errors
    assert sum("at_request failed, the supervisor is detached" in ln for ln in lines) == 2, lines
    # an act an older safe point does not allow is still the old failure
    sup = E.Supervision(Sup([]), E.Request(loaded, {}))
    sup._obj.at_block = lambda event, room: good
    assert sup.at_block({}, 9) is None and sup.detached
    return f"{len(SHAPES)} shapes alike (a mapping, an object, the reference); detach and count"


# ---------------------------------------------------------------------------- 4, 5

def _vocab():
    from engine import grammar as G
    # the CPU model's tokenizer (tests/test_compat.py Tok): ids 0..95 the printable ASCII, 96 the end
    return G.Vocab([bytes([32 + i]) for i in range(96)] + [None], 97)


def _engine_verdict(c, body, chat=True, structured=True, taken=()):
    from engine import grammar as G
    from server import compat
    full = {"kind": c.get("kind"), "value": c.get("value"), "scope": c.get("scope"),
            "strict": bool(c.get("strict"))}
    ac = compat.answer_constraint(body, chat) if structured else None
    own = None if ac is None else (compat.constraint_field(body) if ac.kind == "response_format"
                                   else "tool_choice")
    v = _vocab()
    try:
        k = compat.act_constraint(full, body, chat, 97, structured=structured, own=own, taken=taken,
                                  compile=lambda a: G.Grammar(a.pattern, v, a.parts))
    except compat.ActRefused as exc:
        return False, exc.counted, str(exc)
    return True, None, k


CASES = [
    ({"kind": "regex", "value": "[0-9]{3}-[a-z]{2}"}, {}),
    ({"kind": "regex", "value": "(a"}, {}),
    ({"kind": "regex", "value": "x" * 8193}, {}),
    ({"kind": "regex", "value": 5}, {}),
    ({"kind": "choice", "value": ["red", "green"]}, {}),
    ({"kind": "choice", "value": []}, {}),
    ({"kind": "choice", "value": ["a"] * 257}, {}),
    ({"kind": "choice", "value": ["a" * 1025]}, {}),
    ({"kind": "json_schema", "value": SCHEMA}, {}),
    ({"kind": "json_schema", "value": {"type": "object", "properties": {"d": {"type": "string", "description": "x" * 70000}}}}, {}),
    ({"kind": "json_schema", "value": "object"}, {}),
    ({"kind": "logit_bias", "value": {"5": 10, "6": -100}}, {}),
    ({"kind": "logit_bias", "value": {"97": 1}}, {}),
    ({"kind": "logit_bias", "value": {"5": 100.5}}, {}),
    ({"kind": "logit_bias", "value": {str(i): 1 for i in range(301)}}, {}),
    ({"kind": "logit_bias", "value": {"5": 1}, "scope": "answer"}, {}),
    ({"kind": "logit_bias", "value": {"5": 1}}, {"logit_bias": {"7": 2}}),
    ({"kind": "regex", "value": "a+"}, {"response_format": {"type": "json_object"}}),
    ({"kind": "regex", "value": "a+"}, {"structured_outputs": {"choice": ["x"]}}),
    ({"kind": "regex", "value": "a+"}, {"tools": TOOLS, "tool_choice": {"type": "function", "function": {"name": "now"}}}),
    ({"kind": "regex", "value": "a+"}, {"tools": TOOLS}),
    ({"kind": "regex", "value": "a+"}, {"tools": TOOLS, "tool_choice": "none"}),
    ({"kind": "choice", "value": ["pick"], "scope": "tool_args"}, {"tools": TOOLS}),
    ({"kind": "choice", "value": ["pick", "stop", "now"], "scope": "tool_args"}, {"tools": TOOLS}),
    ({"kind": "choice", "value": ["nope"], "scope": "tool_args"}, {"tools": TOOLS}),
    ({"kind": "choice", "value": ["pick"], "scope": "tool_args"}, {}),
    ({"kind": "choice", "value": ["pick"], "scope": "tool_args"}, {"tools": TOOLS, "tool_choice": "none"}),
    ({"kind": "json_schema", "value": SCHEMA, "scope": "tool_args"}, {"tools": TOOLS}),
]


def test_the_engine_and_the_reference_agree():
    from engine import grammar as G
    H = _load("fake_host")
    seen = {"applied": 0, "refused": 0, "gave way": 0}
    for c, extra in CASES:
        body = dict(MSG, **extra)
        full = {"kind": c["kind"], "value": c.get("value"), "scope": c.get("scope"), "strict": False}
        ref = H.check_constraint(full, body, 97, compile_regex=G.compile_regex)
        eng = _engine_verdict(c, body)
        want = (True, None) if ref is None else (False, ref[1])
        assert eng[:2] == want, (c["kind"], c.get("scope"), extra, eng, ref)
        seen["applied" if eng[0] else "refused" if eng[1] else "gave way"] += 1
    for structured, taken in ((False, ()), (True, ("grammar",)), (True, ("bias",))):
        for c, extra in CASES[:1] + CASES[11:12]:
            full = {"kind": c["kind"], "value": c["value"], "scope": None, "strict": False}
            ref = H.check_constraint(full, dict(MSG), 97, taken, structured)
            eng = _engine_verdict(c, dict(MSG), structured=structured, taken=taken)
            assert eng[:2] == ((True, None) if ref is None else (False, ref[1])), (structured, taken, eng, ref)
    return f"{len(CASES)} cases and the switch and families alike: {seen}"


def test_the_act_is_the_field():
    from server import compat
    v = _vocab()
    n = 0
    for chat in (True, False):
        for extra in ({}, {"tools": TOOLS}, {"tools": TOOLS, "parallel_tool_calls": False}):
            if not chat and extra:
                continue
            body = dict(MSG if chat else {"prompt": "hello there"}, **extra)
            for c in ({"kind": "regex", "value": "[0-9]{3}-[a-z]{2}"},
                      {"kind": "choice", "value": ["red", "green", "blue"]},
                      {"kind": "json_schema", "value": SCHEMA}):
                ok, _, k = _engine_verdict(c, body, chat=chat)
                assert ok, (c, k)
                field = compat.answer_constraint(dict(body, **k.same_as), chat)
                assert k.answer == field and k.grammar.pattern == field.pattern, (c, extra)
                assert k.grammar.side is None if field.parts is None else k.grammar.side is not None
                n += 1
    body = dict(MSG, tools=TOOLS)
    for names, tc in ((["pick"], {"type": "function", "function": {"name": "pick"}}),
                      (["pick", "stop", "now"], "required"), (["now", "pick", "stop"], "required")):
        ok, _, k = _engine_verdict({"kind": "choice", "value": names, "scope": "tool_args"}, body)
        field = compat.answer_constraint(dict(body, tool_choice=tc))
        if names[0] == "pick":
            assert ok and k.answer == field and k.same_as == {"tool_choice": tc}, (names, k)
        else:
            assert ok and k.answer.kind == "tool_choice" and k.answer.types == field.types
        n += 1
    ok, _, k = _engine_verdict({"kind": "choice", "value": ["stop", "pick"], "scope": "tool_args"}, body)
    assert ok and k.same_as == {} and "function=now" not in k.answer.pattern, k.answer.pattern
    ok, _, k = _engine_verdict({"kind": "logit_bias", "value": {"5": 10, "6": 0}}, dict(MSG))
    assert ok and k.bias == compat.logit_bias({"logit_bias": {"5": 10, "6": 0}}, 97) == {5: 10.0}
    del v
    return f"{n} automata equal the request field's; a subset of functions; the bias"


# ---------------------------------------------------------------------------- 6-11: the CPU model

def _cpu():
    import torch  # noqa: F401
    import test_compat as C
    from server import app
    return C, app


class _Drafter:
    """tests/test_compat.py Oracle for a chat prompt: a reference continuation with every third token
    wrong. Where the answer starts in the context is found at the first proposal (the context ends
    with the first tokens of the reference)."""

    def __init__(self, ref, width=5, tree=False):
        self.ref, self.n, self.width, self.tree = list(ref), None, width, tree
        self.asked = 0

    def reset(self):
        pass

    def observe(self, tokens):
        pass

    def _seq(self, ctx, count, corrupt=True):
        self.asked += 1
        if self.n is None:
            ctx_ = list(ctx)
            self.n = next((len(ctx_) - b for b in (1, 2, 3, 0) if ctx_[len(ctx_) - b:] == self.ref[:b]),
                          len(ctx_) - 1)
        i = len(ctx) - self.n
        out = []
        for j in range(min(self.width, max(1, count))):
            t = self.ref[i + j] if 0 <= i + j < len(self.ref) else 0
            out.append((t + 1) % 95 if corrupt and (i + j) % 3 == 2 else t)
        return out

    def propose(self, ctx, count):
        return self._seq(ctx, count)

    def propose_tree(self, ctx, count):
        from engine.tree import DraftTree
        a, b = self._seq(ctx, count), self._seq(ctx, count, corrupt=False)
        seqs = [(a, 0.9)] + ([(b[:3], 0.5)] if b[:3] != a[:3] else [])
        return DraftTree.from_sequences(ctx[-1], seqs)


def _ask(constraints=None, stream=False, act=None, drafter=None, tree=False, path="/v1/chat/completions",
         sup=None, ext=True, **extra):
    """One request on the CPU model; `ext`: fake_ext.py loaded (its args: the constraints, `act`),
    else the headless engine. (head, content, finish, names, the ext object, log lines, usage)."""
    C, app = _cpu()
    C.serve(drafter, tree=tree)
    lines, obj = [], None
    if ext:
        X = _load("fake_ext")
        args = {"constraint": list(constraints or [])}
        if act:
            args["act"] = act
        host = E.Host(mode="chat", engine_api="", state=app.STATE, lock=None, tokenizer=app.STATE["tok"],
                      registry=None, ledger_path=None, args=args, log=lines.append)
        obj = X.start(host)
        if sup is not None:
            obj.supervisor = lambda plan, event: sup(obj)
        app.EXT = E.Loaded(X, obj, host)
    body = dict(MSG) if path.endswith("chat/completions") else {"prompt": "hello there"}
    body.update(max_tokens=60, stream=stream)
    body.update(extra)
    out = io.StringIO()
    req = C.Req(path, body)
    try:
        import contextlib
        with contextlib.redirect_stdout(out):
            req.do_POST()
    finally:
        app.EXT = E.NULL
    h, _, r = req.wfile.getvalue().partition(b"\r\n\r\n")
    head, raw = h.decode(), r.decode()
    lines += out.getvalue().splitlines()
    if not head.startswith("HTTP/1.1 200"):
        return head, raw, None, [], obj, lines, None
    if stream:
        ev = C._events(raw)
        content = "".join((c.get("delta") or {}).get("content") or c.get("text") or ""
                          for e in ev for c in e["choices"])
        names = [d["function"]["name"] for e in ev for c in e["choices"]
                 for d in ((c.get("delta") or {}).get("tool_calls") or []) if d.get("function", {}).get("name")]
        finish = [c["finish_reason"] for e in ev for c in e["choices"] if c["finish_reason"]]
        usage = next((e["usage"] for e in ev if e.get("usage")), None)
        return head, content, finish[-1] if finish else None, names, obj, lines, usage
    j = json.loads(raw)
    c = j["choices"][0]
    if "message" in c:
        content = c["message"].get("content") or ""
        names = [t["function"]["name"] for t in c["message"].get("tool_calls") or []]
    else:
        content, names = c.get("text") or "", []
    return head, content, c["finish_reason"], names, obj, lines, j.get("usage")


def test_no_act_no_change():
    seen = {}
    for stream in (False, True):
        for extra in ({}, {"response_format": {"type": "json_schema", "json_schema": {"name": "s", "schema": SCHEMA}}}):
            bare = _ask(stream=stream, ext=False, **extra)
            quiet = _ask(stream=stream, **extra)
            refused = _ask([{"kind": "regex", "value": "(a"}], stream=stream, **extra)
            assert bare[0].startswith("HTTP/1.1 200"), bare[:2]
            for label, got in (("no act", quiet), ("a refused act", refused)):
                assert got[1:4] == bare[1:4], (label, stream, extra, got[1:4], bare[1:4])
            assert quiet[4].outcomes == [] and not refused[4].outcomes[0][0]["applied"]
            seen[f"{'stream' if stream else 'json'}{'/rf' if extra else ''}"] = (bare[2], len(bare[1]))
    return f"headless == no act == a refused act: {seen}"


ACTS = [({"kind": "regex", "value": r"[0-9]{3}-[a-z]{2}"}, {"structured_outputs": {"regex": r"[0-9]{3}-[a-z]{2}"}},
         lambda t: re.fullmatch(r"[0-9]{3}-[a-z]{2}", t)),
        ({"kind": "choice", "value": ["red", "green", "blue"]}, {"structured_outputs": {"choice": ["red", "green", "blue"]}},
         lambda t: t in ("red", "green", "blue")),
        ({"kind": "json_schema", "value": SCHEMA}, {"structured_outputs": {"json": SCHEMA}},
         lambda t: set(json.loads(t)) >= {"k", "n"}),
        ({"kind": "logit_bias", "value": {"33": 40, "0": -50}}, {"logit_bias": {"33": 40, "0": -50}},
         lambda t: True)]


def test_the_act_writes_the_fields_tokens():
    seen = {}
    for c, field, ok in ACTS:
        for stream in (False, True):
            got = _ask([c], stream=stream)
            want = _ask(stream=stream, ext=False, **field)
            assert got[0].startswith("HTTP/1.1 200") and got[1:4] == want[1:4], (c["kind"], stream, got[:4], want[:4])
            assert got[4].outcomes[0][0]["applied"], got[4].outcomes
            if got[2] == "stop":
                assert ok(got[1]), (c["kind"], got[1])
            seen[f"{c['kind']}/{'stream' if stream else 'json'}"] = got[2]
        if c["kind"] != "logit_bias":
            assert any(f"SET_CONSTRAINT {c['kind']}" in ln and "carried out as structured_outputs" in ln
                       for ln in got[5]), got[5]
    # a completion
    c, field, ok = ACTS[0]
    got = _ask([c], path="/v1/completions")
    want = _ask(ext=False, path="/v1/completions", **field)
    assert got[0].startswith("HTTP/1.1 200") and got[1:3] == want[1:3], (got[:3], want[:3])
    seen["regex/completion"] = got[2]
    return f"the act's tokens are the field's: {seen}"


def _drafted(lines) -> int:
    """Tokens the request's blocks committed beyond one a block: drafts accepted (its `[req]` line)."""
    for ln in lines:
        m = re.search(r" blocks=(\d+) committed=(\d+)", ln) if ln.startswith("[req]") else None
        if m:
            return int(m.group(2)) - int(m.group(1))
    return -1


def test_the_act_is_exact_under_speculation():
    C, app = _cpu()
    c = {"kind": "regex", "value": r'\{"k": "[a-z]{2,6}", "n": [0-9]{1,3}\}'}
    ref = _ask([c], max_tokens=60)
    free = _ask(ext=False)
    assert ref[2] == "stop" and re.fullmatch(c["value"], ref[1]), ref[1:3]
    enc = lambda t: [(ord(ch) - 32) % 96 for ch in t]           # noqa: E731
    runs, took, asked = {}, {}, {}
    for label, seq, tree in (("chain, free drafts", enc(free[1]), False),
                             ("chain, constrained drafts", enc(ref[1]) + [C.EOS], False),
                             ("tree, free drafts", enc(free[1]), True),
                             ("tree, constrained drafts", enc(ref[1]) + [C.EOS], True)):
        dr = _Drafter(seq, tree=tree)
        got = _ask([c], drafter=dr, tree=tree)
        runs[label] = got[1:3]
        took[label], asked[label] = _drafted(got[5]), dr.asked
    bad = {k: v for k, v in runs.items() if v != ref[1:3]}
    assert not bad, f"differs from the drafter-less run {ref[1:3]}: {bad}"
    assert took["chain, constrained drafts"] and took["tree, constrained drafts"], (took, asked)
    return f"{ref[1]!r}: 4 speculative runs == no drafter; drafts accepted {took}"


def test_an_invalid_constraint_runs_unconstrained():
    C, app = _cpu()
    bare = _ask(ext=False)
    for c in ({"kind": "regex", "value": "(a"}, {"kind": "logit_bias", "value": {"97": 5}},
              {"kind": "json_schema", "value": {"type": "x"}}):
        got = _ask([c])
        assert got[1:4] == bare[1:4], (c, got[1:4])
        o = got[4].outcomes[0][0]
        assert not o["applied"] and o["why"], o
        assert any("at_request failed" in ln and "SET_CONSTRAINT" in ln for ln in got[5]), got[5]
    # counted in qse_ext_errors_total{hook="at_request"} (the extension's own counter too)
    X = _load("fake_ext")
    host = E.Host(mode="chat", engine_api="", state={}, lock=None, tokenizer=None, registry=None,
                  ledger_path=None, args={}, log=lambda line: None)
    loaded = E.Loaded(X, X.start(host), host)
    C.serve()
    plan = types.SimpleNamespace(row={"extra": {}})
    outs = []
    sup = types.SimpleNamespace(constraint_outcomes=outs.append)
    acts = [{"kind": "SET_CONSTRAINT", "constraint": {"kind": "regex", "value": "(a"}},
            {"kind": "SET_CONSTRAINT", "constraint": {"kind": "regex", "value": "b+"}},
            {"kind": "SET_CONSTRAINT", "constraint": {"kind": "choice", "value": ["x"]}}]
    with __import__("contextlib").redirect_stdout(io.StringIO()):
        g, b, strict = app._set_constraints(acts, dict(MSG), True, None, True,
                                            types.SimpleNamespace(ext=loaded), plan, sup, "r1")
    assert g is not None and g.answer.pattern == "b+" and b is None and strict is None
    assert [o["applied"] for o in plan.row["extra"]["set_constraint"]] == [False, True, False]
    assert outs == [plan.row["extra"]["set_constraint"]] and loaded.errors == {"at_request": 2}, loaded.errors
    # a constraint the vocabulary cannot write: the field's ending, naming SET_CONSTRAINT
    head, raw, *_ = _ask([{"kind": "regex", "value": "é+"}])
    assert head.startswith("HTTP/1.1 400") and json.loads(raw)["error"]["param"] == "SET_CONSTRAINT" \
        and "no legal token" in raw, (head, raw[:300])
    head, raw, *_ = _ask(ext=False, structured_outputs={"regex": "é+"})
    assert json.loads(raw)["error"]["param"] == "structured_outputs", raw[:300]
    head, text, finish, *_ = _ask([{"kind": "regex", "value": "é+"}], stream=True)
    assert finish == "error", (head, finish)
    return "3 invalid constraints: the headless tokens, the outcome, counted; the row's extra; no legal token"


def test_precedence_and_strict():
    rf = {"response_format": {"type": "json_schema", "json_schema": {"name": "s", "schema": SCHEMA}}}
    for stream in (False, True):
        got = _ask([{"kind": "regex", "value": "[0-9]+"}], stream=stream, **rf)
        want = _ask(stream=stream, ext=False, **rf)
        assert got[1:4] == want[1:4], (stream, got[1:4], want[1:4])
        o = got[4].outcomes[0][0]
        assert not o["applied"] and "response_format" in o["why"], o
        assert not any("at_request failed" in ln for ln in got[5]) and \
            any("gave way" in ln for ln in got[5]), got[5]
    for c, extra in (({"kind": "regex", "value": "(a", "strict": True}, {}),
                     ({"kind": "regex", "value": "[0-9]+", "strict": True}, rf),
                     ({"kind": "logit_bias", "value": {"5": 1}, "strict": True}, {"logit_bias": {"6": 1}})):
        for stream in (False, True):
            head, raw, *_ = _ask([c], stream=stream, **extra)
            assert head.startswith("HTTP/1.1 400"), (c, stream, head, raw[:200])
            err = json.loads(raw)["error"]
            assert err["param"] == "SET_CONSTRAINT" and err["type"] == "invalid_request_error", err
    return "response_format wins (both transports, uncounted); 3 strict refusals are 400s on both"


def test_tool_args_and_the_other_acts():
    seen = {}
    for stream in (False, True):
        got = _ask([{"kind": "choice", "value": ["pick"], "scope": "tool_args"}], stream=stream,
                   max_tokens=120, tools=TOOLS[:2], logit_bias={"0": -50})
        assert got[2] == "tool_calls" and got[3] == ["pick"], (stream, got[1:4])
        seen["stream" if stream else "json"] = got[3]
    X = _load("fake_ext")
    asked = []

    class Splices(X._Supervisor):
        def apply_request(self, prompt, in_think, acts):
            asked.append([E.act_kind(a) for a in acts])
            return prompt, in_think
    c = {"kind": "regex", "value": "[0-9]{3}"}
    got = _ask([c], act={"at_request": "PROMPT_TAIL_NOTE"}, sup=Splices)
    assert asked == [["PROMPT_TAIL_NOTE"]] and re.fullmatch("[0-9]{3}", got[1]), (asked, got[1])
    asked.clear()
    got = _ask([c], sup=Splices)
    assert asked == [] and re.fullmatch("[0-9]{3}", got[1]), (asked, got[1])
    return f"tool_args calls {seen}; apply_request got the note alone, and nothing for a constraint alone"


# ---------------------------------------------------------------------------- 12

def test_the_window_tool():
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import set_constraint_check as T
    seen = []
    for name, endpoint, field, act in T.pairs({"5": -100}):
        chat = endpoint.endswith("chat/completions")
        (c,) = act[T.FIELD]
        rest = {k: v for k, v in act.items() if k != T.FIELD}
        ok, counted, k = _engine_verdict(c, rest, chat=chat)
        if name in ("precedence", "invalid"):
            assert rest == field and not ok and counted == (name == "invalid"), (name, ok, counted, k)
        else:
            own = {f: field[f] for f in ("structured_outputs", "logit_bias") if f in field}
            assert ok and k.same_as == own, (name, k)
            assert {f: v for f, v in field.items() if f not in own} == rest, name
        seen.append(name)
    (name, body, fn), = T.CHECKS
    ok, _, k = _engine_verdict(body[T.FIELD][0], {f: v for f, v in body.items() if f != T.FIELD})
    assert ok and k.answer.kind == "tool_choice" and fn in k.answer.pattern, k

    def rec(lp, finish="stop", calls=None):
        return {"gate": {"status": 200, "wire": {"text": "", "content": "a", "reasoning": "", "error": None,
                                                 "calls": calls or {}, "finish": finish},
                         "usage": {"completion_tokens": 1}, "logprobs": {"content": [{"t": "a", "lp": lp}]}}}
    doc = {"pairs": [{"name": "same", "field": rec(-0.5), "act": rec(-0.5)},
                     {"name": "differs", "field": rec(-0.5), "act": rec(-0.25)}],
           "checks": [{"name": "tool_args", "want": "get_weather",
                       "rec": rec(-1, "tool_calls", {"0": {"name": "get_weather", "arguments": "{}"}})}]}
    got = {n: ok for n, ok, _ in T.verdicts(doc)}
    assert got == {"same": True, "differs": False, "tool_args": True}, got
    return f"{len(seen)} pairs: each act the same as its field; verdicts hold a log-probability apart"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
