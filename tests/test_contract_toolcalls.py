"""The engine's tool-call parser against the contract's fixtures (engine API 1.3, ENG-427, QA ENG-428).

contracts/engine-api-v1/toolcalls/ holds cases in each form a model writes its calls in (xml,
argkey, pythonic) and the argument renames and tool mappings (repair.json); check.py there runs a
case and says what "passes" means. TandemApp runs the same files against its copy of the parser
(its tests/test_contract_toolcalls.py), so the two copies answer the same text the same way. Until
ENG-413 WP2a the engine's parser is TandemApp's modules through server/__init__.py's finder; WP2a
switches API below to the engine's own copy and nothing else here changes.

  1  every form registered for a family has a fixture file, and every file names a form
                                                                  test_every_form_has_fixtures
  2  every case of every file answers its want: the content, each call's name and its arguments'
     exact JSON text, the renames, and the same read as a stream in pieces of several sizes
                                                                  test_the_parser_passes_the_fixtures
  3  the runner sees a difference: a changed want fails, and so does a parser that drops a call
                                                                  test_the_runner_catches_a_change

CPU, no torch. Run: python tests/test_contract_toolcalls.py; --write fills the wants from this
parser (only when the parser's answer is meant to change; the diff is the review).
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(1, os.path.join(ROOT, "app"))  # SRV-747: TandemApp's packages, until ENG-413
sys.path.insert(0, HERE)

DIR = os.path.join(ROOT, "contracts", "engine-api-v1", "toolcalls")


def _check():
    spec = importlib.util.spec_from_file_location("contract_toolcalls_check", os.path.join(DIR, "check.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def api() -> dict:
    """The engine's parser (ENG-413 WP2a: server/api's copy)."""
    from tandemapp import callrepair, toolformats
    from tandemapp.toolcall import ToolCallBuffer, parse_tool_calls, schema_types
    return {"parse_tool_calls": parse_tool_calls, "get_format": toolformats.get,
            "ToolCallBuffer": ToolCallBuffer, "repair_calls": callrepair.repair_calls,
            "schemas_of": callrepair.schemas_of, "schema_types": schema_types}


def test_every_form_has_fixtures():
    from engine.models import REGISTRY
    from tandemapp import toolformats
    check = _check()
    toolformats.get(None)
    forms = set(toolformats.FORMATS)
    files = {}
    for name in check.FILES:
        files[name] = check.load(os.path.join(DIR, name))["form"]
    assert forms <= set(files.values()), (forms, files)
    assert set(files.values()) <= forms, files
    for name, a in REGISTRY.items():
        assert a.spec.tool_format in forms, (name, a.spec.tool_format)
    assert sorted(f for f in os.listdir(DIR) if f.endswith(".json")) == sorted(check.FILES)
    return f"{len(forms)} forms, {len(files)} files"


def test_the_parser_passes_the_fixtures():
    check = _check()
    bad, n, streamed = [], 0, 0
    for name in check.FILES:
        path = os.path.join(DIR, name)
        bad += check.run(path, api())
        doc = check.load(path)
        n += len(doc["cases"])
        streamed += sum(1 for c in doc["cases"] if c.get("stream"))
        assert all(c.get("want") for c in doc["cases"]), f"{name}: a case without want"
    assert not bad, bad
    return f"{n} cases ({streamed} also streamed) in {len(check.FILES)} files"


def test_the_runner_catches_a_change():
    check = _check()
    import tempfile
    path = os.path.join(DIR, "xml.json")
    doc = check.load(path)
    with tempfile.TemporaryDirectory(prefix="toolcalls-") as d:
        bent = copy.deepcopy(doc)
        bent["cases"][0]["want"]["calls"][0]["arguments"] = json.dumps({"path": "elsewhere"})
        p = os.path.join(d, "xml.json")
        with open(p, "w") as f:
            json.dump(bent, f)
        got = check.run(p, api())
        assert len(got) == 1 and bent["cases"][0]["id"] in got[0], got
    real = api()

    def drop_last(text, names=None, eos=False, types=None):
        content, calls = real["parse_tool_calls"](text, names=names, eos=eos, types=types)
        return content, calls[:-1]
    lossy = dict(real, parse_tool_calls=drop_last)
    got = check.run(path, lossy)
    assert got, "a parser that drops a call passed"
    return f"a changed want and a lossy parser fail ({len(got)} cases)"


if __name__ == "__main__":
    if sys.argv[1:] == ["--write"]:
        check = _check()
        for name in check.FILES:
            print(f"{name}: {check.write(os.path.join(DIR, name), api())} cases")
        sys.exit(0)
    from scriptmode import run
    run(globals())
