#!/usr/bin/env bash
# The engine's own tests (ENG-413 WP12, WP14; OPS-250): the files tools/engine_tests.py lists, each
# through tests/script_suite.py in a clean process, four at a time. The CI job `cpu` runs it in the
# checkout; the box runs it the same way in an archive of one. No TandemApp is needed or read: a
# TANDEMAPP_SRC in the environment is ignored (tests/script_suite.py passes none to the files).
#
#   bash tools/engine_suite.sh OUT     results in OUT: FILES, RESULT, logs/
#
# PY (default python3) and SUITE_JOBS (default 4) from the environment. Exit 0 when every listed
# file ran and passed (a GPU file may say skipped), else 1.
set -euo pipefail
[ $# -eq 1 ] || { echo "usage: engine_suite.sh OUT" >&2; exit 2; }
mkdir -p "$1"
OUT="$(cd "$1" && pwd)"
PY="${PY:-python3}"
JOBS="${SUITE_JOBS:-4}"
cd "$(dirname "${BASH_SOURCE[0]}")/.."
mkdir -p "$OUT/logs"
"$PY" tools/engine_tests.py > "$OUT/FILES"
: > "$OUT/RESULT"
env -u TANDEMAPP_SRC xargs -P "$JOBS" -I{} sh -c \
    '"$0" tests/script_suite.py --all --timeout 900 --out "$1/logs" "$2" | tail -1 >> "$1/RESULT"' \
    "$PY" "$OUT" {} < "$OUT/FILES"
"$PY" tools/engine_tests.py --summary "$OUT/RESULT"
