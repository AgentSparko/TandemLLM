#!/usr/bin/env python3
"""The engine's own tests that run in a tree without app/ (ENG-413 WP12, OPS-250).

    python3 tools/headless_tests.py                  the files, one a line
    python3 tools/headless_tests.py --summary RESULT  the totals of a run's result lines; exit 1 on a
                                                     failed file, a missing one or none at all

The files are the top-level tests/test_*.py of this tree whose home in tests/test-ledger.json is
`engine` (the engine's own code; WP9) or that came after the ledger's base (an engine test file),
less .github/cpu-suite-skip.txt and less NEEDS_APP: engine tests that still read the vendored app/
until WP14 removes it (each says why). A split or pair file is not here: its pair ids need
TandemApp (WP13 builds the pair suite). tools/headless_suite.sh runs the list in a
tree without app/; the CI job of that name and the box both call it. Standard library only.
"""

from __future__ import annotations

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(ROOT, "tests", "test-ledger.json")
SKIP_FILE = os.path.join(ROOT, ".github", "cpu-suite-skip.txt")
#: engine tests that read the vendored app/ until WP14 (that package flips or drops each read)
NEEDS_APP = {
    "tests/test_app_hook.py": "the no-app guard: while VENDORED_APP is True it asserts app/ and app.pin "
                              "are here (WP14 flips it)",
    "tests/test_app_pin.py": "hashes app/ against app.pin (WP14 removes both)",
    "tests/test_engine_api_contract.py": "reverse-deps.json is the inventory of the engine's reach into "
                                         "app/, and the vendored SPEAKS is checked (WP14 empties both)",
}


def files() -> list[str]:
    with open(LEDGER, encoding="utf-8") as f:
        led = json.load(f)
    skip = set()
    if os.path.isfile(SKIP_FILE):
        with open(SKIP_FILE, encoding="utf-8") as f:
            skip = {ln.strip() for ln in f if ln.strip() and not ln.startswith("#")}
    on_disk = sorted(f"tests/{n}" for n in os.listdir(os.path.join(ROOT, "tests"))
                     if re.fullmatch(r"test_[^/]+\.py", n))
    return [rel for rel in on_disk
            if led["engine"].get(rel, {"home": "engine"})["home"] == "engine"
            and rel not in skip and rel not in NEEDS_APP]


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
    if argv[:1] == ["--summary"] and len(argv) == 2:
        return summary(argv[1], files())
    if argv:
        print(__doc__)
        return 2
    for rel in files():
        print(rel)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
