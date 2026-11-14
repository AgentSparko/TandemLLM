"""The engine extension interface engine-ext-v1 1.0, the engine's side (ENG-429, QA ENG-430).

contracts/engine-ext-v1 is the in-process interface TandemApp's conductor runs through in chat mode:
ext.json (the hooks, the order, the failure rule), fake_host.py (the reference host), fake_ext.py
(the smallest extension), knobs.json. The engine's own host (server/ext.py, ENG-413 WP3a) is held
to the cases below once it exists; TandemApp's extension (WP4) runs against fake_host.py.

The test matrix, the Gherkin of ENG-429:

  1  ext.json is well formed: a MAJOR.MINOR version, every hook and supervisor method with its
     arguments, the order names only hooks and methods it defines, each safe point allows only
     act kinds it lists, the failure rule says closed at start and open per request
                                                                  test_the_interface_reads
  2  the fake extension has every hook and method with exactly ext.json's arguments, and the
     module attributes discovery names                            test_the_fake_extension_fits
  3  a plain request asks the hooks in ext.json's order; CONTINUE_HIDDEN at SP2 loops back to the
     blocks and asks at_finish again; REGENERATE_CALL asks at_tool_call again; a plan of None asks
     nothing more; prompt gets info and may answer a mapping       test_the_order
  4  start is closed: no module in chat mode, a module that does not import, a missing EXT_API,
     another major (a 1.x extension on this 2.0 host), a newer minor, start raising and a claimed engine path each refuse with one
     '[ext] REFUSING:' line; an engine without QSE_EXT runs headless; an older minor is taken
                                                                  test_start_is_closed
  5  a request is open: each hook that raises, one that returns the wrong shape, and an act the
     safe point does not allow, leave the request served (headless from there), counted by hook;
     the next request asks the extension again; a Refusal from plan is the client's answer
                                                                  test_a_request_is_open
  6  the folder's Python imports a few standard library modules only, and neither tree
                                                                  test_standard_library_only
  7  knobs.json names knobs of engine/settings.py KNOBS with the same defaults, each owned by an
     app/ label                                                   test_the_knobs

CPU, no torch. Run: python tests/test_engine_ext_contract.py
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import json
import os
import re
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
DIR = os.path.join(ROOT, "contracts", "engine-ext-v1")


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"engine_ext_{name}", os.path.join(DIR, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _spec() -> dict:
    with open(os.path.join(DIR, "ext.json")) as f:
        return json.load(f)


def test_the_interface_reads():
    s = _spec()
    assert s["name"] == "engine-ext-v1" and re.fullmatch(r"\d+\.\d+", s["version"]), s["version"]
    hooks = {h["name"]: h for h in s["extension"]["hooks"]}
    methods = {m["name"]: m for m in s["supervisor"]["methods"]}
    assert {"plan", "prompt", "supervisor", "extras", "follow_ups", "finish"} <= set(hooks)
    assert all(isinstance(h["args"], list) and h["returns"] and h["when"] for h in hooks.values())
    names = set(hooks) | set(methods)
    for step in s["order"]["request"]:
        for part in re.findall(r"[a-z_]+", step):
            assert part in names | {"start", "block", "finish"}, step
    kinds = set(s["supervisor"]["act"]["kinds"])
    assert {"CONTINUE_HIDDEN", "REGENERATE_CALL", "GIVE_UP"} <= kinds
    for sp, v in s["supervisor"]["safe_points"].items():
        assert set(v["acts"]) <= kinds, sp
        assert any(m["point"] == sp for m in methods.values()), sp
    assert set(s["order"]["act_loops"]) <= kinds
    assert s["failure"]["rule"] == "closed at start, open per request"
    assert "REFUSING" in s["failure"]["start"] and "headless" in s["failure"]["request"]
    with open(os.path.join(DIR, "knobs.json")) as f:
        assert json.load(f)["version"] == s["version"]
    return f"engine-ext-v1 {s['version']}: {len(hooks)} hooks, {len(methods)} supervisor methods, " \
           f"{len(kinds)} act kinds"


def test_the_fake_extension_fits():
    s, X = _spec(), _load("fake_ext")
    H = _load("fake_host")
    for attr in s["discovery"]["module"]:
        assert hasattr(X, attr), attr
    assert H.accepts(X.EXT_API) is None
    ext = X.start(H.FakeHost())
    for h in s["extension"]["hooks"]:
        params = list(inspect.signature(getattr(ext, h["name"])).parameters)
        assert params == h["args"], (h["name"], params)
    sup = ext.supervisor({"active": True}, {})
    for m in s["supervisor"]["methods"]:
        params = list(inspect.signature(getattr(sup, m["name"])).parameters)
        assert params == m["args"], (m["name"], params)
    for attr in s["extension"]["attributes"]:
        assert isinstance(getattr(ext, attr), list), attr
    host = H.FakeHost()
    for attr in s["host"]:
        assert hasattr(host, attr), attr
    return f"{len(s['extension']['hooks'])} hooks and {len(s['supervisor']['methods'])} methods match"


PLAIN = ["plan", "prompt", "supervisor", "at_request", "extras start",
         "at_block", "extras block", "at_block", "extras block",
         "at_finish", "extras finish", "follow_ups", "finish", "done"]


def test_the_order():
    H, X = _load("fake_host"), _load("fake_ext")
    host = H.FakeHost()
    rec = host.serve(H.load(X, host), H.Scenario(blocks=2))
    assert rec["trace"] == PLAIN and not rec["errors"] and not rec["raw"], rec
    # one tool call: SP3 between the blocks and the finish
    rec = host.serve(H.load(X, host), H.Scenario(blocks=1, calls=1))
    assert rec["trace"][6:9] == ["extras block", "at_tool_call", "at_finish"], rec["trace"]
    # CONTINUE_HIDDEN at SP2: the blocks again, then at_finish again
    host = H.FakeHost(args={"act": {"at_finish": "CONTINUE_HIDDEN"}})
    rec = host.serve(H.load(X, host), H.Scenario(blocks=1))
    t = rec["trace"]
    assert t.count("at_finish") == 2 and t[t.index("at_finish") + 1] == "at_block", t
    assert rec["acts"] == [("SP2", "CONTINUE_HIDDEN")], rec["acts"]
    # REGENERATE_CALL at SP3: the call is asked again
    host = H.FakeHost(args={"act": {"at_tool_call": "REGENERATE_CALL"}})
    rec = host.serve(H.load(X, host), H.Scenario(blocks=1, calls=1))
    assert rec["trace"].count("at_tool_call") == 2 and rec["acts"] == [("SP3", "REGENERATE_CALL")], rec
    # GIVE_UP at SP3: the turn ends, at_finish is not asked
    host = H.FakeHost(args={"act": {"at_tool_call": "GIVE_UP"}})
    rec = host.serve(H.load(X, host), H.Scenario(blocks=1, calls=1))
    assert "at_finish" not in rec["trace"] and rec["trace"][-1] == "done", rec["trace"]
    # SP4: a note at the request
    host = H.FakeHost(args={"act": {"at_request": "PROMPT_TAIL_NOTE"}})
    rec = host.serve(H.load(X, host), H.Scenario())
    assert rec["acts"] == [("SP4", "PROMPT_TAIL_NOTE")] and rec["trace"] == PLAIN, rec
    # a plan of None: the request is raw, nothing more is asked
    host = H.FakeHost(args={"raw": True})
    rec = host.serve(H.load(X, host), H.Scenario())
    assert rec["trace"] == ["plan"] and rec["raw"] and not rec["errors"], rec
    # prompt gets info and may answer a mapping (2.0: every extension)
    seen = []

    class Later:
        EXT_API, NAME, VERSION, __name__ = "2.0", "later", "0", "later"

        @staticmethod
        def start(host):
            ext = X.start(host)
            ext.prompt = lambda plan, ids, info=None: seen.append(info) or {
                "ids": list(ids) + [9], "in_think": False, "client_ids": None}
            return ext
    host = H.FakeHost()
    rec = host.serve(H.load(Later, host), H.Scenario())
    assert rec["prompt"] == [1, 2, 3, 9] and not rec["errors"] and seen == [{"in_think": False, "conv": None}], rec
    return f"plain {len(PLAIN)} steps; CONTINUE_HIDDEN, REGENERATE_CALL, GIVE_UP, SP4, raw; prompt with info"


def test_start_is_closed():
    H, X = _load("fake_host"), _load("fake_ext")

    def refused(module, **kw) -> str:
        try:
            H.load(module, H.FakeHost(**kw))
        except H.Refused as exc:
            line = str(exc)
            assert line.startswith("[ext] REFUSING: ") and "\n" not in line, line
            return line
        raise AssertionError(f"started: {module} {kw}")

    def variant(**attrs):
        m = types.ModuleType("variant_ext")
        for k in ("EXT_API", "NAME", "VERSION", "start"):
            setattr(m, k, getattr(X, k))
        for k, v in attrs.items():
            if v is None:
                delattr(m, k)
            else:
                setattr(m, k, v)
        return m
    lines = [refused(""),                                              # chat mode, no QSE_EXT
             refused("no_such_extension_module_x"),
             refused(variant(EXT_API=None)),
             refused(variant(EXT_API="1.3")),
             refused(variant(EXT_API="2.9")),
             refused(variant(EXT_API="one")),
             refused(X, args={"fail": "start"}),
             refused(X, args={"route": "/v1/chat/completions"}),
             refused(X, args={"route": "/v1/dashboard/live"}),
             refused(X, args={"route": "/v1/engine/disk"})]
    assert "QSE_EXT is empty" in lines[0] and "needs engine-ext-v1 2.9" in lines[4], lines
    # 2.0 (ENG-413 WP4): a 1.x extension is refused at start, one line, whatever its minor
    assert lines[3] == "[ext] REFUSING: the extension speaks engine-ext-v1 1.3; this engine speaks 2.0", lines[3]
    for old in ("1.0", "1.2", "1.9"):
        assert refused(variant(EXT_API=old)) == f"[ext] REFUSING: the extension speaks engine-ext-v1 {old}; " \
            f"this engine speaks 2.0", old

    class NoHook:
        routes, models, knobs = [], [], []
    assert "lacks the hooks" in refused(variant(start=lambda host: NoHook()))
    # headless: no extension asked for, outside chat mode
    assert H.load("", H.FakeHost(mode="engine")) is None
    # an older minor starts on this host; the extension's own routes are fine
    assert H.accepts("2.0", "2.4") is None and H.accepts("1.3", "2.0") is not None
    assert H.load(X, H.FakeHost(args={"route": "/v1/conductor/"})) is not None
    assert H.load(X, H.FakeHost(args={"route": "/v1/dashboard/session"})) is not None
    return f"{len(lines) + 1} start failures refused with one line; headless and older minors start"


def test_a_request_is_open():
    H, X = _load("fake_host"), _load("fake_ext")
    want_raw = {"plan"}
    for hook in ("plan", "prompt", "supervisor", "extras", "follow_ups", "finish",
                 "at_request", "at_block", "at_finish", "done"):
        host = H.FakeHost(args={"fail": hook})
        ext = H.load(X, host)
        rec = host.serve(ext, H.Scenario(blocks=2))
        assert rec["status"] == 200 and rec["errors"] and hook in rec["errors"][0], (hook, rec)
        assert host.errors.get(hook), (hook, host.errors)
        assert rec["raw"] == (hook in want_raw), (hook, rec)
        if hook in ("at_request", "at_block", "at_finish"):
            after = rec["trace"][rec["trace"].index(hook) + 1:]
            assert not {"at_block", "at_finish", "at_tool_call", "done"} & set(after), (hook, rec["trace"])
            assert after[-3:] == ["extras finish", "follow_ups", "finish"], (hook, after)
        if hook == "prompt":
            assert rec["prompt"] == [1, 2, 3], rec
        # the next request asks the extension again
        ext.args.pop("fail")
        again = host.serve(ext, H.Scenario(blocks=2))
        assert again["trace"] == PLAIN and not again["errors"], (hook, again)
    for hook in ("prompt", "extras", "follow_ups"):
        host = H.FakeHost(args={"bad": hook})
        rec = host.serve(H.load(X, host), H.Scenario())
        assert any(e.startswith(hook) for e in rec["errors"]), (hook, rec)
    # an act the safe point does not allow: dropped, counted, the supervisor detached
    host = H.FakeHost(args={"act": {"at_block": "CONTINUE_HIDDEN"}})
    rec = host.serve(H.load(X, host), H.Scenario(blocks=2))
    assert rec["acts"] == [] and host.errors.get("at_block") == 1 and rec["status"] == 200, rec
    # a Refusal from plan is an answer
    host = H.FakeHost(args={"refuse": True})
    rec = host.serve(H.load(X, host), H.Scenario())
    assert rec["status"] == 400 and rec["refusal"]["type"] == "invalid_request_error" and \
        not host.errors, rec
    return "10 hook failures and 3 bad shapes served open, a misplaced act dropped, a Refusal answered"


def test_standard_library_only():
    #: what the folder's Python may import: standard library modules, named (no version's list)
    std = {"__future__", "importlib", "inspect", "json", "os", "re", "types"}
    bad = []
    for d in (DIR, os.path.join(ROOT, "contracts", "engine-api-v1", "toolcalls")):
        for name in sorted(os.listdir(d)):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(d, name)) as f:
                tree = ast.parse(f.read())
            for n in ast.walk(tree):
                mods = [a.name for a in n.names] if isinstance(n, ast.Import) else \
                    [n.module] if isinstance(n, ast.ImportFrom) and n.level == 0 and n.module else []
                bad += [f"{name}: {m}" for m in mods if m.split(".")[0] not in std]
    assert not bad, bad
    return "fake_host.py, fake_ext.py and toolcalls/check.py import the standard library only"


def test_the_knobs():
    from engine import settings
    with open(os.path.join(DIR, "knobs.json")) as f:
        doc = json.load(f)
    assert doc["prefix"] == "QWEN38_", doc["prefix"]          # EngineSettings' default prefix
    bad = []
    for name, default in doc["knobs"].items():
        row = settings.KNOBS.get(name)
        if row is None:
            bad.append(f"{name}: not in KNOBS")
        elif row[0] != default:
            bad.append(f"{name}: KNOBS default {row[0]!r}, knobs.json {default!r}")
        elif not str(row[1]).startswith("app/"):
            bad.append(f"{name}: owner {row[1]}")
    assert not bad, bad
    assert list(doc["knobs"]) == sorted(doc["knobs"])
    return f"{len(doc['knobs'])} knobs in KNOBS with their defaults"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
