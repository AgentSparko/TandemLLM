#!/bin/bash
# A systemd unit's ExecStart for one engine: ops/start.sh backgrounds the engine and returns, so
# this script stays the unit's main process for as long as the engine runs and the unit's cgroup
# owns it. `systemctl stop` then drains the engine cleanly, and nothing can kill it by accident:
# on 2026-11-07 a restart asked mid-boot died with the agent session that asked for it, the
# SIGTERM going down the whole process tree.
#
# A reference, not an installed file: install.sh does not copy it, like ops/gate.sh and
# ops/qwen38-engine.service. A box's unit is the box's own file; copy this beside the rendered
# serve.env as DIR/run/ops/engine-exec.sh, point the unit's ExecStart at it through bash, and
# keep Type=simple with KillMode=control-group.
#
# Hold on death, not on silence (2026-11-21). A `curl -m N /health` loop that leaves on the first
# timeout treats a stalled engine as a dead one: a long disk-tier save or a GPU sync holds every
# Python thread past the timeout while the kernel still accepts connections, so a client on a
# keep-alive finishes its request while the fresh probe hears nothing. Leaving on one miss took
# a serving engine down with the unit four times in a morning. The engine is gone only when
# several probes in a row fail AND its pid is dead AND nothing listens on the port: a dead
# engine fails all three at once, a busy one fails none of them for long.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"          # DIR/run/ops, beside serve.env
RUN="$(dirname "$HERE")"
cd "$RUN" || exit 1
set -a; . "$HERE/serve.env"; set +a
PORT="${PORT:-8000}"
REPO="${REPO:-$RUN/../src}"

# A box that shares the accelerator can put its own guard beside this script as
# ops/engine-slot.sh (claim|release NAME; not part of the release): its exit 75 (EX_TEMPFAIL)
# is passed through, so the unit waits and retries instead of hot-looping. Whatever stops the
# engine later -- a stop, a crash, a reboot -- the slot goes back, so the other engine is never
# locked out by a claim nobody holds. Registered BEFORE start.sh: a start that fails must not
# leak the claim.
if [ -x "$HERE/engine-slot.sh" ]; then
    SLOT="${QSE_SLOT_NAME:-tandem-$PORT}"
    bash "$HERE/engine-slot.sh" claim "$SLOT" || exit $?
    release_slot() { bash "$HERE/engine-slot.sh" release "$SLOT" >/dev/null 2>&1 || true; }
    trap release_slot EXIT
fi

bash "$HERE/start.sh"

# start.sh writes the pid it spawned into $REPO/logs/engine.pid.
PIDFILE="$REPO/logs/engine.pid"
PID=""
for _ in $(seq 1 120); do
    if [ -s "$PIDFILE" ]; then
        PID=$(cat "$PIDFILE" 2>/dev/null || echo "")
        [ -n "$PID" ] && break
    fi
    sleep 1
done

if [ -z "$PID" ]; then
    # no pidfile: find the engine by what it owns -- the port. Never exit 1 here: this script's
    # first version did when the pidfile was missing, and KillMode=control-group killed the
    # engine it was written to protect. NEVER let this script exit while the engine is alive.
    echo "[unit] no pidfile at $PIDFILE; finding the engine by its port"
    for _ in $(seq 1 60); do
        PID=$(ss -ltnp 2>/dev/null | grep -m1 ":$PORT " | grep -oP 'pid=\K[0-9]+' | head -1)
        [ -n "$PID" ] && break
        sleep 2
    done
fi

# The engine is gone only when its pid is dead AND the port has no listener left: the port can
# outlive the pid by a moment, and a self-respawned engine keeps the port under a new pid, so
# neither check alone is proof.
engine_gone() {
    if [ -n "${PID:-}" ] && kill -0 "$PID" 2>/dev/null; then return 1; fi
    if ss -ltn 2>/dev/null | grep -q ":$PORT "; then return 1; fi
    return 0
}

hold_until_gone() {
    local fails=0
    while :; do
        if curl -sf -m 10 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
            fails=0
        else
            fails=$((fails+1))
            if [ "$fails" -ge "${QSE_HOLD_FAILS:-6}" ] && engine_gone; then
                echo "[unit] the engine stopped answering" \
                     "($fails failed probes; pid ${PID:-?} dead, :$PORT not listening)"
                exit 0
            fi
            echo "[unit] /health probe failed ($fails); the engine is still up, holding"
        fi
        sleep "${QSE_HOLD_EVERY_S:-5}"
    done
}

if [ -z "$PID" ]; then
    echo "[unit] the engine's pid is unknown; holding the unit on the port alone"
fi
echo "[unit] engine pid ${PID:-unknown}; holding the unit until it exits"

# the engine may respawn itself; keep following the port's owner while it lives
hold_until_gone
