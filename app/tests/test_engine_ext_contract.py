"""The engine extension interface engine-ext-v1, TandemApp's side (ENG-429, QA ENG-430).

contracts/engine-ext-v1 (the engine's, pinned by contracts/engine-api-v1/PIN) is how TandemApp's
conductor runs inside an engine process in chat mode. Until TandemApp ships its extension
(tandemapp/engine_ext.py, ENG-413 WP4, tested against fake_host.py), this side holds what it
already owns: the knobs the extension reads, and that the reference host and the smallest
extension run in this tree.

  1  knobs.json is tandemapp/knobs.py DEFAULTS, name for name and default for default
                                                                  test_the_knobs
  2  the reference host loads the smallest extension and asks its hooks in ext.json's order; a
     hook that raises leaves the request served; a start that fails is refused with one line
                                                                  test_the_reference_host_runs_here
  3  the folder's Python imports nothing of TandemApp or the engine
                                                                  test_no_tree_imported

Standard library only. Run: python tests/test_engine_ext_contract.py
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from proxy_util import run  # noqa: E402
from tandemapp import knobs  # noqa: E402

DIR = os.path.join(ROOT, "contracts", "engine-ext-v1")


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"engine_ext_{name}", os.path.join(DIR, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_knobs():
    with open(os.path.join(DIR, "knobs.json")) as f:
        doc = json.load(f)
    assert doc["prefix"] == knobs.SETTINGS.prefix, doc["prefix"]
    assert doc["knobs"] == knobs.DEFAULTS, (sorted(set(doc["knobs"]) ^ set(knobs.DEFAULTS)),
                                            {k for k in doc["knobs"] if doc["knobs"][k] != knobs.DEFAULTS.get(k)})
    return f"{len(doc['knobs'])} knobs, defaults equal"


def test_the_reference_host_runs_here():
    H, X = _load("fake_host"), _load("fake_ext")
    host = H.FakeHost()
    rec = host.serve(H.load(X, host), H.Scenario(blocks=1, calls=1))
    assert rec["trace"][:4] == ["plan", "prompt", "supervisor", "at_request"] and \
        rec["trace"][-3:] == ["follow_ups", "finish", "done"] and not rec["errors"], rec
    host = H.FakeHost(args={"fail": "prompt"})
    rec = host.serve(H.load(X, host), H.Scenario())
    assert rec["status"] == 200 and host.errors == {"prompt": 1} and rec["trace"][-1] == "done", rec
    try:
        H.load(X, H.FakeHost(args={"fail": "start"}))
    except H.Refused as exc:
        assert str(exc).startswith("[ext] REFUSING: "), exc
    else:
        raise AssertionError("a failed start was not refused")
    return f"{len(rec['trace'])} hooks asked in order; open per request, closed at start"


def test_no_tree_imported():
    bad = []
    for name in sorted(os.listdir(DIR)):
        if name.endswith(".py"):
            with open(os.path.join(DIR, name)) as f:
                tree = ast.parse(f.read())
            for n in ast.walk(tree):
                mods = [a.name for a in n.names] if isinstance(n, ast.Import) else \
                    [n.module or ""] if isinstance(n, ast.ImportFrom) else []
                bad += [f"{name}: {m}" for m in mods
                        if m.split(".")[0] in ("tandemapp", "proxy", "chatd", "server", "engine")]
    assert not bad, bad
    return "fake_host.py and fake_ext.py import neither tree"


if __name__ == "__main__":
    run(globals())
