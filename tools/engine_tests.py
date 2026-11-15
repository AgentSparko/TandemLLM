#!/usr/bin/env python3
"""The engine's own tests: the suite a checkout with no TandemApp runs (ENG-413 WP12, WP14; OPS-250).

    python3 tools/engine_tests.py                  the files, one a line
    python3 tools/engine_tests.py --pair           the pair tests instead (tools/pair_suite.sh)
    python3 tools/engine_tests.py --summary RESULT  the totals of a run's result lines; exit 1 on a
                                                   failed file, a missing one or none at all

The engine's files are the top-level tests/test_*.py of this tree, less .github/cpu-suite-skip.txt
and less the pair tests: the files tests/test-ledger.json lists under `pair_suite`, which run the
engine and TandemApp's extension, packages or test helpers together and need a TandemApp checkout
beside the engine (TANDEMAPP_SRC). tools/engine_suite.sh runs the engine's list, the CI job and the
box call it; tools/pair_suite.sh runs the pair list. Standard library only.
"""

from __future__ import annotations

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(ROOT, "tests", "test-ledger.json")
SKIP_FILE = os.path.join(ROOT, ".github", "cpu-suite-skip.txt")


def _top() -> list[str]:
    return sorted(f"tests/{n}" for n in os.listdir(os.path.join(ROOT, "tests"))
                  if re.fullmatch(r"test_[^/]+\.py", n))


def pair() -> list[str]:
    """The pair tests that are top-level test files (a GPU script of the list is not)."""
    with open(LEDGER, encoding="utf-8") as f:
        listed = set(json.load(f)["pair_suite"]["files"])
    return [rel for rel in _top() if rel in listed]


def files() -> list[str]:
    skip = set()
    if os.path.isfile(SKIP_FILE):
        with open(SKIP_FILE, encoding="utf-8") as f:
            skip = {ln.strip() for ln in f if ln.strip() and not ln.startswith("#")}
    paired = set(pair())
    return [rel for rel in _top() if rel not in skip and rel not in paired]


def summary(path: str, want: list[str]) -> int:
    """script_suite's total lines (`files=N ok=N fail=N skipped=N tests=N[ -- bad...][ -- skipped:
    ...]`, one a file) added up, as the cpu job does."""
    with open(path, encoding="utf-8") as f:
        lines = [ln.rstrip("\n") for ln in f if ln.strip()]
    total = {"files": 0, "ok": 0, "fail": 0, "skipped": 0, "tests": 0}
    bad, skipped = [], []
    for line in lines:
        for k, v in re.findall(r"(\w+)=(\d+)", line.split(" -- ")[0]):
            if k in total:
                total[k] += int(v)
        parts = line.split(" -- ")[1:]
        for p in parts:
            if p.startswith("skipped: "):
                skipped += p[len("skipped: "):].split()
            else:
                bad += p.split()
    print(" ".join(f"{k}={v}" for k, v in total.items()))
    for f in skipped:
        print("skipped", f)
    for f in bad:
        print(f"::error file={f}::{f} failed or ran no test")
    missing = len(want) - total["files"]
    if missing:
        print(f"::error::{len(want)} files listed, {total['files']} results")
    return 1 if bad or missing or total["files"] == 0 else 0


def main(argv: list[str]) -> int:
    which = pair if argv[:1] == ["--pair"] else files
    if argv[:1] == ["--pair"]:
        argv = argv[1:]
    if argv[:1] == ["--summary"] and len(argv) == 2:
        return summary(argv[1], which())
    if argv:
        print(__doc__)
        return 2
    for rel in which():
        print(rel)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
