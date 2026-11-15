#!/usr/bin/env bash
# The pair suite (ENG-413 WP13, WP14): the engine's pair tests, which run the engine and TandemApp's
# extension, packages or test helpers together (tools/engine_tests.py --pair, from
# tests/test-ledger.json `pair_suite`), each through tests/script_suite.py in a clean process, four at
# a time, with TandemApp from the checkout TANDEMAPP_SRC names. The engine's own suite needs none of
# this (tools/engine_suite.sh); TandemApp's tools/pair_suite.sh runs this one from its side.
#
#   TANDEMAPP_SRC=<TandemApp checkout> bash tools/pair_suite.sh OUT     results in OUT: FILES, RESULT, logs/
#
# Before it runs anything it checks that the TandemApp checkout is pinned to this engine: its copy of
# contracts/engine-api-v1 and contracts/engine-ext-v1 (its PIN left out) equals this tree's, file for
# file and byte for byte. PY (default python3) and SUITE_JOBS (default 4) from the environment.
# Exit 0 when every listed file ran and passed (a GPU file may say skipped), 1 on a failed file, 2
# when TandemApp is missing or pinned to other contracts.
set -euo pipefail
[ $# -eq 1 ] || { echo "usage: TANDEMAPP_SRC=DIR pair_suite.sh OUT" >&2; exit 2; }
PY="${PY:-python3}"
JOBS="${SUITE_JOBS:-4}"
APP="${TANDEMAPP_SRC:-}"
if [ -z "$APP" ] || [ ! -f "$APP/tandemapp/__init__.py" ]; then
    echo "pair_suite: TANDEMAPP_SRC must name a TandemApp checkout (its tandemapp/ at the top)" >&2
    exit 2
fi
APP="$(cd "$APP" && pwd)"
mkdir -p "$1"
OUT="$(cd "$1" && pwd)"
cd "$(dirname "${BASH_SOURCE[0]}")/.."
"$PY" - "$PWD" "$APP" <<'PY' || exit 2
import os, sys
engine, app = sys.argv[1:]
bad = []
for folder in ("engine-api-v1", "engine-ext-v1"):
    def files(root):
        base = os.path.join(root, "contracts", folder)
        out = {}
        for d, dirs, names in os.walk(base):
            dirs[:] = [x for x in dirs if x not in ("__pycache__", ".pytest_cache")]
            for n in names:
                rel = os.path.relpath(os.path.join(d, n), base)
                if rel != "PIN" and not n.endswith(".pyc"):
                    out[rel] = os.path.join(d, n)
        return out
    a, b = files(engine), files(app)
    bad += [f"{folder}/{r}: only in the engine" for r in sorted(set(a) - set(b))]
    bad += [f"{folder}/{r}: only in TandemApp's copy" for r in sorted(set(b) - set(a))]
    for r in sorted(set(a) & set(b)):
        with open(a[r], "rb") as f1, open(b[r], "rb") as f2:
            if f1.read() != f2.read():
                bad.append(f"{folder}/{r}: differs")
for line in bad[:20]:
    print("pair_suite:", line, file=sys.stderr)
if bad:
    print(f"pair_suite: TandemApp at {app} is not pinned to this engine's contracts "
          f"({len(bad)} differences)", file=sys.stderr)
    sys.exit(1)
print(f"pair_suite: TandemApp at {app} is pinned to this engine's contracts")
PY
mkdir -p "$OUT/logs"
"$PY" tools/engine_tests.py --pair > "$OUT/FILES"
: > "$OUT/RESULT"
export TANDEMAPP_SRC="$APP" SUITE_PYTHONPATH="$APP"
xargs -P "$JOBS" -I{} sh -c \
    '"$0" tests/script_suite.py --all --timeout 900 --out "$1/logs" "$2" | tail -1 >> "$1/RESULT"' \
    "$PY" "$OUT" {} < "$OUT/FILES"
"$PY" tools/engine_tests.py --pair --summary "$OUT/RESULT"
