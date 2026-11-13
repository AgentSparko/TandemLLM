"""TandemApp's tool-call parser against the engine contract's fixtures (ENG-427, QA ENG-428).

contracts/engine-api-v1/toolcalls/ (the engine's, pinned by PIN) holds cases in each form a model
writes its calls in and the argument renames; its check.py says what passing means. The engine
runs the same files against its parser, so both read a model's text the same way.

  1  every form tandemapp/toolformats registers has a fixture file, and each file names one
                                                                  test_every_form_has_fixtures
  2  every case answers its want: the content, each call's name and its arguments' exact JSON
     text, the renames, and the same read as a stream in pieces   test_the_parser_passes_the_fixtures

Standard library only. Run: python tests/test_contract_toolcalls.py
"""

from __future__ import annotations

import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from proxy_util import run  # noqa: E402
from tandemapp import callrepair, toolformats  # noqa: E402
from tandemapp.toolcall import ToolCallBuffer, parse_tool_calls, schema_types  # noqa: E402

DIR = os.path.join(ROOT, "contracts", "engine-api-v1", "toolcalls")
API = {"parse_tool_calls": parse_tool_calls, "get_format": toolformats.get,
       "ToolCallBuffer": ToolCallBuffer, "repair_calls": callrepair.repair_calls,
       "schemas_of": callrepair.schemas_of, "schema_types": schema_types}


def _check():
    spec = importlib.util.spec_from_file_location("contract_toolcalls_check", os.path.join(DIR, "check.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_form_has_fixtures():
    check = _check()
    toolformats.get(None)
    files = {name: check.load(os.path.join(DIR, name))["form"] for name in check.FILES}
    assert set(files.values()) == set(toolformats.FORMATS), (files, sorted(toolformats.FORMATS))
    return f"{len(toolformats.FORMATS)} forms, {len(files)} files"


def test_the_parser_passes_the_fixtures():
    check = _check()
    bad, n = [], 0
    for name in check.FILES:
        bad += check.run(os.path.join(DIR, name), API)
        n += len(check.load(os.path.join(DIR, name))["cases"])
    assert not bad, bad
    return f"{n} cases in {len(check.FILES)} files"


if __name__ == "__main__":
    run(globals())
