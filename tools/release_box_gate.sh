#!/usr/bin/env bash
# The release gate's box half (OPS-223). tools/release.sh copies a git archive of the candidate to the box
# and starts this there, from the archive's root, under nohup; it can also run by hand:
#
#   bash tools/release_box_gate.sh --version X.Y.Z [--py PYTHON] [--pythonpath P] [--jobs N]
#        [--smoke-base URL] [--skip suite,setup,smoke,loose] [--out DIR]
#
#   suite  every tests/test_*.py on the CPU through tests/script_suite.py, one clean process a file
#          (env -i, CUDA_VISIBLE_DEVICES empty), N files at a time, 900 s a file; a file that runs no
#          test fails (SRV-661), one that says `skipped:` is listed apart; then every
#          app/tests/test_*.py through app/tests/script_suite.py --all, one file a call, N at a time
#          (env -i; the runner sets PYTHONPATH to app/), into OUT/app-suite. A failing file in
#          either pass fails the step
#   setup  bash tools/release_gate_setup.sh --py PYTHON (OPS-177): a scratch install through the Setup
#          Guide beside the live engine, on spare ports
#   smoke  tools/client_smoke.py against --smoke-base on the conductor path, then the raw path
#          (--x-tandem raw), after /health there names X.Y.Z (the candidate must be deployed). The API
#          key, when the server needs one, is read from .smoke-key in this tree (tools/release.sh writes
#          it, mode 600, and this script removes it) and passed in the environment (QSE_API_KEY)
#   loose  tools/loose_ends_live.py against --smoke-base without /v1, after smoke: the live checks of
#          the 2026-10-05 loose ends (a streamed call cut by the token limit reaches the client once;
#          a streamed turn behind a running one gets `: waiting` comments and its answer, never a
#          429), written to OUT/loose.json. As for smoke, /health there must name X.Y.Z, and the API
#          key goes in the environment (QSE_API_KEY)
#
# Writes OUT/SUMMARY (a line a step) and OUT/EXIT (0 when every step run passed) last, which
# tools/release.sh polls for. Every process it starts ends before it exits.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY=python3; PYP=""; JOBS=6; BASE=http://127.0.0.1:8000/v1; SKIP=""; VERSION=""; OUT=""
while [ $# -gt 0 ]; do
    case "$1" in
        --py) PY="$2"; shift 2 ;;
        --pythonpath) PYP="$2"; shift 2 ;;
        --jobs) JOBS="$2"; shift 2 ;;
        --smoke-base) BASE="$2"; shift 2 ;;
        --skip) SKIP="$2"; shift 2 ;;
        --version) VERSION="$2"; shift 2 ;;
        --out) OUT="$2"; shift 2 ;;
        -h|--help) sed -n '2,/^set -u$/p' "$0" | sed '$d'; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done
[ -n "$VERSION" ] || VERSION="$(cat "$ROOT/VERSION")"
OUT="${OUT:-$ROOT/results/release-gate/$(date -u +%Y%m%dT%H%M%SZ)}"
mkdir -p "$OUT"
: > "$OUT/SUMMARY"
rm -f "$OUT/EXIT"
KEY=""
if [ -f "$ROOT/.smoke-key" ]; then KEY="$(cat "$ROOT/.smoke-key")"; rm -f "$ROOT/.smoke-key"; fi
FAILS=0
line() { echo "$*" | tee -a "$OUT/SUMMARY"; }
skipped() { case ",$SKIP," in *",$1,"*) return 0 ;; *) return 1 ;; esac; }
cd "$ROOT"

if skipped suite; then
    line "suite  skipped"
else
    mkdir -p "$OUT/suite"
    : > "$OUT/suite/RESULT"
    start=$(date +%s)
    export ROOT PY PYP OUT
    one() {
        local t="$1" n s r
        n="$(basename "$t" .py)"; s=$(date +%s)
        if env -i HOME="$HOME" PATH="$PATH" PYTHONPATH="$ROOT${PYP:+:$PYP}" CUDA_VISIBLE_DEVICES= \
                OMP_NUM_THREADS=2 TZ=Europe/Berlin timeout 900 nice -n 10 "$PY" -u tests/script_suite.py "$t" \
                > "$OUT/suite/$n.log" 2>&1; then
            r=ok; grep -qiE '^skipped' "$OUT/suite/$n.log" && r=skip
        else
            r=FAIL
        fi
        echo "$r $n $(( $(date +%s) - s ))" >> "$OUT/suite/RESULT"
    }
    export -f one
    ls tests/test_*.py | xargs -P "$JOBS" -I{} bash -c 'one "$1"' _ {}
    files=$(wc -l < "$OUT/suite/RESULT"); ok=$(grep -c '^ok ' "$OUT/suite/RESULT")
    skip=$(grep -c '^skip ' "$OUT/suite/RESULT"); bad=$(grep -c '^FAIL ' "$OUT/suite/RESULT")
    if [ "$bad" = 0 ] && [ "$files" -gt 0 ]; then st=PASS; else st=FAIL; FAILS=$((FAILS + 1)); fi
    line "suite  $st files=$files ok=$ok skipped=$skip failed=$bad $(( $(date +%s) - start )) s ($OUT/suite)"
    grep '^FAIL ' "$OUT/suite/RESULT" | sed 's/^/       /' | tee -a "$OUT/SUMMARY"
    grep '^skip ' "$OUT/suite/RESULT" | sed 's/^/       /' | tee -a "$OUT/SUMMARY"

    # SRV-748: TandemApp's tests through their own runner, each file in its own --all call
    start=$(date +%s)
    ls app/tests/test_*.py | env -i HOME="$HOME" PATH="$PATH" SUITE_PYTHONPATH="$PYP" OMP_NUM_THREADS=2 \
        xargs -P "$JOBS" -n 1 nice -n 10 "$PY" -u app/tests/script_suite.py --all --timeout 900 \
        --out "$OUT/app-suite" > "$OUT/app-suite.log" 2>&1
    arc=$?
    # each call ends with its own files= ok= fail= skipped= line: the sums are the pass's
    read -r files ok bad skip <<EOF
$(awk '/^files=/ { for (i = 1; i <= NF; i++) { split($i, kv, "="); t[kv[1]] += kv[2] } }
    END { printf "%d %d %d %d\n", t["files"], t["ok"], t["fail"], t["skipped"] }' "$OUT/app-suite.log")
EOF
    if [ "$arc" = 0 ] && [ "$bad" = 0 ] && [ "$files" -gt 0 ]; then st=PASS; else st=FAIL; FAILS=$((FAILS + 1)); fi
    line "suite  $st app/tests files=$files ok=$ok skipped=$skip failed=$bad $(( $(date +%s) - start )) s ($OUT/app-suite)"
    grep -E '^tests/test_[^ ]+\.py .* (FAIL|SKIPPED)' "$OUT/app-suite.log" | sed 's/^/       app\//' | tee -a "$OUT/SUMMARY"
    grep -F 'changed this machine' "$OUT/app-suite.log" | sed 's/^/       /' | tee -a "$OUT/SUMMARY"
fi

if skipped setup; then
    line "setup  skipped"
else
    start=$(date +%s)
    if bash tools/release_gate_setup.sh --py "$PY" > "$OUT/setup.log" 2>&1; then st=PASS; else st=FAIL; FAILS=$((FAILS + 1)); fi
    line "setup  $st $(( $(date +%s) - start )) s ($OUT/setup.log; $(grep -m1 -o 'report: .*' "$OUT/setup.log" || echo 'no report line'))"
fi

# the live steps (smoke, loose) run against the candidate only: /health at --smoke-base names it
root_url="${BASE%/v1}"
served=""
if ! skipped smoke || ! skipped loose; then
    if curl -fsS --max-time 20 -o "$OUT/health.json" "$root_url/health"; then
        served="$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("version", ""))' "$OUT/health.json" 2>/dev/null)"
    fi
fi
not_candidate() {
    line "$1  FAIL the server at $root_url runs '${served:-nothing}', not $VERSION: deploy the candidate first (or --skip $1)"
    FAILS=$((FAILS + 1))
}

if skipped smoke; then
    line "smoke  skipped"
elif [ "${served%%+*}" != "$VERSION" ]; then
    not_candidate smoke
else
    for path in conductor raw; do
        extra=""; [ "$path" = raw ] && extra="--x-tandem raw"
        # shellcheck disable=SC2086
        if QSE_API_KEY="$KEY" "$PY" -u tools/client_smoke.py --base "$BASE" $extra --json "$OUT/smoke-$path.json" \
                > "$OUT/smoke-$path.out" 2>&1; then st=PASS; else st=FAIL; FAILS=$((FAILS + 1)); fi
        line "smoke  $st $path: $(tail -1 "$OUT/smoke-$path.out") ($served)"
    done
fi

if skipped loose; then
    line "loose  skipped"
elif [ "${served%%+*}" != "$VERSION" ]; then
    not_candidate loose
else
    start=$(date +%s)
    if QSE_API_KEY="$KEY" "$PY" -u tools/loose_ends_live.py --base "$root_url" --out "$OUT/loose.json" \
            > "$OUT/loose.out" 2>&1; then st=PASS; else st=FAIL; FAILS=$((FAILS + 1)); fi
    line "loose  $st $(( $(date +%s) - start )) s ($OUT/loose.json; $served)"
fi

echo "$FAILS" > "$OUT/EXIT.tmp" && mv "$OUT/EXIT.tmp" "$OUT/EXIT"
exit $(( FAILS > 0 ? 1 : 0 ))
