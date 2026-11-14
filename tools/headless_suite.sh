#!/usr/bin/env bash
# The engine's own tests in a tree without app/ (ENG-413 WP12, OPS-250): the files
# tools/headless_tests.py lists, each through tests/script_suite.py in a clean process, as the cpu
# job runs the whole suite. The CI job `headless` runs it in a copy of the checkout without app/;
# the box runs it the same way in an archive without app/.
#
#   bash tools/headless_suite.sh OUT     results in OUT: FILES, RESULT, logs/
#
# PY (default python3) and SUITE_JOBS (default 4) from the environment. Refuses a tree that has
# app/. Exit 0 when every listed file ran and passed (a GPU file may say skipped), else 1.
set -euo pipefail
[ $# -eq 1 ] || { echo "usage: headless_suite.sh OUT" >&2; exit 2; }
mkdir -p "$1"
OUT="$(cd "$1" && pwd)"
PY="${PY:-python3}"
JOBS="${SUITE_JOBS:-4}"
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [ -e app ]; then
    echo "headless_suite: app/ is here: run it in a tree without app/" >&2
    exit 2
fi
mkdir -p "$OUT/logs"
"$PY" tools/headless_tests.py > "$OUT/FILES"
: > "$OUT/RESULT"
xargs -P "$JOBS" -I{} sh -c \
    '"$0" tests/script_suite.py --all --timeout 900 --out "$1/logs" "$2" | tail -1 >> "$1/RESULT"' \
    "$PY" "$OUT" {} < "$OUT/FILES"
"$PY" tools/headless_tests.py --summary "$OUT/RESULT"
