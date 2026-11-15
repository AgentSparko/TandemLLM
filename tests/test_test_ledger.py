"""The test ledger (ENG-413 WP9, QA ENG-414): tests/test-ledger.json names every test id the engine
had at its base and every one TandemApp had at its base, and where each runs now. No old id may be
lost on the way: each has a home, and that home has it.

  1  the ledger's counts are its own lists: ids per home, files, the pair suite's files, the
     dropped ids (each with its proof)                         test_the_counts_are_the_lists
  2  every engine id is where the ledger says: in the engine's file (engine, pair; `pair:path`
     for an id that moved into another pair test) or in TandemApp's tests (tandemapp), no id
     without a home, no home without the id
                                                               test_no_engine_id_is_lost
  3  every TandemApp id of its base is still in TandemApp's tests      test_no_tandemapp_id_is_lost
  4  a dropped id names the code that is gone, and it is gone          test_a_dropped_id_has_its_proof

TandemApp's tests are read from the checkout TANDEMAPP_SRC names (ENG-413 WP14: the vendored app/
is gone); without one, 2 and 3 check the engine's side only and say so.

CPU, standard library only. Run: python tests/test_test_ledger.py
"""

from __future__ import annotations

import ast
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LEDGER = os.path.join(HERE, "test-ledger.json")
HOMES = ("engine", "pair", "tandemapp", "dropped")


def _ledger() -> dict:
    with open(LEDGER) as f:
        return json.load(f)


def _app_root() -> str | None:
    """TandemApp's tree: the checkout TANDEMAPP_SRC names, or None."""
    d = os.environ.get("TANDEMAPP_SRC", "")
    if d and os.path.isdir(os.path.join(d, "tests")) and os.path.isdir(os.path.join(d, "tandemapp")):
        return d
    return None


_IDS: dict = {}


def _ids(path: str) -> set | None:
    """The test ids of one file: its module-level `test*` functions and `Class.test*` methods."""
    if path not in _IDS:
        if not os.path.isfile(path):
            _IDS[path] = None
        else:
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read())
            out = set()
            for n in tree.body:
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test"):
                    out.add(n.name)
                elif isinstance(n, ast.ClassDef):
                    out |= {f"{n.name}.{m.name}" for m in n.body
                            if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and m.name.startswith("test")}
            _IDS[path] = out
    return _IDS[path]


def _dests(rel: str, entry: dict) -> dict:
    """{id: [destination, ...]} of one engine file's entry; a destination is engine, pair,
    dropped, or tandemapp[:path]"""
    if isinstance(entry["ids"], dict):
        return {i: list(d) for i, d in entry["ids"].items()}
    home = entry["home"]
    if home == "tandemapp":
        return {i: [f"tandemapp:{entry.get('to', rel)}"] for i in entry["ids"]}
    return {i: [home] for i in entry["ids"]}


def test_the_counts_are_the_lists():
    led = _ledger()
    assert led["version"] == 1 and all(re.fullmatch(r"[0-9a-f]{40}", c) for c in led["base"].values()), led["base"]
    counts = dict.fromkeys(HOMES, 0)
    ids = 0
    pair = set()
    for rel, entry in led["engine"].items():
        assert entry["home"] in HOMES + ("split",), (rel, entry["home"])
        d = _dests(rel, entry)
        ids += len(d)
        for i, dests in d.items():
            assert dests, f"{rel}::{i} has no home"
            for x in dests:
                counts[x.split(":")[0]] += 1
                if x.split(":")[0] == "pair":
                    pair.add(x.partition(":")[2] or rel)    # pair:path, an id moved to that pair test
        if entry["home"] == "pair":
            pair.add(rel)                       # a pair file with no test ids (a GPU script)
    s = led["summary"]
    assert s["engine_ids"] == ids and s["engine_files"] == len(led["engine"]), (s, ids)
    assert s["to"] == counts, (s["to"], counts)
    assert sorted(pair) == led["pair_suite"]["files"], sorted(set(pair) ^ set(led["pair_suite"]["files"]))
    app = led["tandemapp"]["files"]
    assert s["tandemapp_ids"] == sum(len(v) for v in app.values()) and s["tandemapp_files"] == len(app)
    assert counts["dropped"] == sum(len(v["ids"]) for v in led["dropped"].values())
    return f"{ids} engine ids: " + ", ".join(f"{k} {v}" for k, v in counts.items()) + \
        f"; {len(pair)} pair files; {s['tandemapp_ids']} TandemApp ids"


def test_no_engine_id_is_lost():
    led = _ledger()
    app = _app_root()
    lost, checked, unread = [], 0, 0
    for rel, entry in led["engine"].items():
        for i, dests in _dests(rel, entry).items():
            for x in dests:
                home, _, to = x.partition(":")
                if home in ("engine", "pair"):
                    where = to or rel
                    have = _ids(os.path.join(ROOT, where))
                elif home == "tandemapp":
                    if app is None:
                        unread += 1
                        continue
                    where = to or entry.get("to", rel)
                    have = _ids(os.path.join(app, where))
                else:
                    continue
                checked += 1
                if have is None or i not in have:
                    lost.append(f"{rel}::{i} -> {home} {where}")
    assert not lost, f"{len(lost)} ids not where the ledger says: {lost[:10]}"
    for f in led["pair_suite"]["files"]:
        assert os.path.isfile(os.path.join(ROOT, f)), f"pair file gone: {f}"
    if unread:
        print(f"note: no TandemApp tree (no TANDEMAPP_SRC): {unread} TandemApp homes not read")
    return f"{checked} homes checked, {unread} not read"


def test_no_tandemapp_id_is_lost():
    led = _ledger()
    app = _app_root()
    if app is None:
        print("note: no TandemApp tree (no TANDEMAPP_SRC): TandemApp's ids not read")
        return "TandemApp not here"
    lost = []
    for rel, ids in led["tandemapp"]["files"].items():
        have = _ids(os.path.join(app, rel)) or set()
        lost += [f"{rel}::{i}" for i in ids if i not in have]
    assert not lost, f"{len(lost)} TandemApp ids gone: {lost[:10]}"
    return f"{sum(len(v) for v in led['tandemapp']['files'].values())} TandemApp ids in {app[len(ROOT) + 1:] or app}"


def test_a_dropped_id_has_its_proof():
    led = _ledger()
    for key, d in led["dropped"].items():
        assert d.get("ids") and d.get("why") and d.get("gone"), key
        for p in d["gone"]:
            assert not os.path.exists(os.path.join(ROOT, p)), f"{key}: {p} is still here"
        entry = led["engine"].get(key, {})
        assert entry.get("home") == "dropped" or any(
            "dropped" in v for v in (entry.get("ids") or {}).values() if isinstance(v, list)), key
    return f"{len(led['dropped'])} files with dropped ids"


if __name__ == "__main__":
    sys.path.insert(0, HERE)
    from scriptmode import run
    run(globals())
