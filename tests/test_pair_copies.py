"""The copies the engine and TandemApp keep equal (ENG-413 WP14: a pair test, out of the retired
contracts/engine-api-v1/reverse-deps.json `copies`).

  1  the byte copies: the licenses, the setup fixture TandemApp pins, the engine's dashboard reads
     and TandemApp's dashboard-v1 copies of them, the decision models' shape    test_byte_copies
  2  the values: server/ext.py EXT_DEFAULT_DIR is the conductor's default directory (TandemApp's
     tandemapp/conductor/store.py DEFAULT_ROOT), which a fake engine may not use
                                                     test_the_values_the_engine_copied

TandemApp from TANDEMAPP_SRC (tests/pairapp.py). Standard library only.
Run: TANDEMAPP_SRC=... python tests/test_pair_copies.py
"""

from __future__ import annotations

import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import pairapp  # noqa: E402  a pair test: TandemApp from TANDEMAPP_SRC

APP = pairapp.APP
DASH = os.path.join("contracts", "engine-api-v1", "dashboard")
#: (the engine's file, TandemApp's), both paths from each tree's root
FILES = [("LICENSE", "LICENSE"), ("NOTICE", "NOTICE"), ("COMMERCIAL-LICENSE.md", "COMMERCIAL-LICENSE.md"),
         ("models/estimate-cases.json", "contracts/engine-api-v1/setup/estimate-cases.json"),
         ("contracts/engine-api-v1/decision/systemone.schema.json", "contracts/conductor-v1/systemone.schema.json")]


def _dash_pairs() -> list[tuple[str, str]]:
    names = sorted(n for n in os.listdir(os.path.join(ROOT, DASH)))
    return [(os.path.join(DASH, n), os.path.join("contracts", "dashboard-v1", n)) for n in names]


def test_byte_copies():
    pairs = FILES + _dash_pairs()
    diff = []
    for mine, theirs in pairs:
        with open(os.path.join(ROOT, mine), "rb") as f1, open(os.path.join(APP, theirs), "rb") as f2:
            if f1.read() != f2.read():
                diff.append(f"{mine} != TandemApp's {theirs}")
    assert not diff, diff
    return f"{len(pairs)} file pairs identical"


def _literal(path: str, name: str):
    with open(path) as f:
        tree = ast.parse(f.read())
    for n in tree.body:
        if isinstance(n, ast.Assign) and any(getattr(t, "id", None) == name for t in n.targets):
            return ast.literal_eval(n.value)
    raise AssertionError(f"{path} has no {name}")


def test_the_values_the_engine_copied():
    mine = _literal(os.path.join(ROOT, "server", "ext.py"), "EXT_DEFAULT_DIR")
    theirs = _literal(os.path.join(APP, "tandemapp", "conductor", "store.py"), "DEFAULT_ROOT")
    assert mine == theirs, (mine, theirs)
    return f"EXT_DEFAULT_DIR {mine}"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
