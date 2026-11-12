# Which processes are engines. Sourced by start.sh, stop.sh, hold.sh, watchdog.sh and gate.sh.
#
# They used to ask `pgrep -f "server/app.py ..."`, which matches any process whose command line
# merely CONTAINS the text: on 2026-09-23 at 22:47 a lock holder, `flock -o LOCK bash -c "<script
# text naming server/app.py>"`, was taken for the engine -- a hold's restore refused to restart
# :8000 because of it, and the next hold's stop.sh sent it SIGTERM, which freed the box lock while
# that hold's command ran. A flock, an ssh session's `bash -c`, a grep or an editor all match.
#
# An engine is a process whose EXECUTABLE is a Python interpreter (/proc/<pid>/exe) and whose
# argument vector runs the script server/app.py: `python [-u ...] server/app.py ...`, the path
# relative or ending in /server/app.py -- what start.sh, the systemd unit and row3 all launch.
# `python -c "..."` and `python tools/x.py --server server/app.py` are not engines. The port is the
# `--port` argument, app.py's default 8000 when there is none. No pid file: a service started by
# an older start.sh, by hand or by row3 is still found.
#
# ENGINE_PROC exists so tests/test_ops_scripts.py can hand the scripts a process table of its own
# (and never the box's real one, which holds the operator's engine).
ENGINE_PROC="${ENGINE_PROC:-/proc}"

# engine_pids [PORT] -- the pid of every engine, one a line; only those on PORT when given.
engine_pids() {
    local want="${1:-}" d exe arg i n port
    local -a argv
    for d in "$ENGINE_PROC"/[0-9]*; do
        argv=()
        # builtins only until the argument vector says "engine": this runs over every process
        while IFS= read -r -d '' arg; do argv+=("$arg"); done 2>/dev/null < "$d/cmdline"
        n=${#argv[@]}
        i=1
        while [ "$i" -lt "$n" ]; do
            case "${argv[$i]}" in
                -c|-m) i=$n ;;                       # a command or a module, not a script
                -*) i=$((i + 1)) ;;                  # an interpreter option (-u)
                *) break ;;
            esac
        done
        [ "$i" -lt "$n" ] || continue
        case "${argv[$i]}" in server/app.py|*/server/app.py) ;; *) continue ;; esac
        exe="$(readlink "$d/exe" 2>/dev/null)" || continue
        case "${exe##*/}" in python*) ;; *) continue ;; esac
        if [ -n "$want" ]; then
            port=8000
            while [ "$i" -lt "$n" ]; do
                case "${argv[$i]}" in
                    --port) port="${argv[$((i + 1))]:-}" ;;
                    --port=*) port="${argv[$i]#--port=}" ;;
                esac
                i=$((i + 1))
            done
            [ "$port" = "$want" ] || continue
        fi
        echo "${d##*/}"
    done
}

# Peers (OPS-197): a second engine on this box, run from an install directory of its own (a
# checkout at the same commit) by that tree's ops/start.sh with QSE_PEER=<name> in its environment.
# A peer is not the box's engine: start.sh starts the box's engine beside one and one beside it,
# stop.sh and the watchdog of the box's tree never act on one, and a hold refuses while one lives.
# A process whose environment cannot be read (another user's) is not a peer.

# peer_name PID -- the QSE_PEER an engine was started with, else nothing.
peer_name() {
    local kv
    while IFS= read -r -d '' kv; do
        case "$kv" in QSE_PEER=?*) echo "${kv#QSE_PEER=}"; return 0 ;; esac
    done 2>/dev/null < "$ENGINE_PROC/$1/environ"
    return 0
}

# main_pids [PORT] -- engine_pids less the peers: the box's engine and anything else (a row3 or a
# probe server), the engines the box's start.sh refuses to start beside.
main_pids() {
    local p
    for p in $(engine_pids "${1:-}"); do
        [ -n "$(peer_name "$p")" ] || echo "$p"
    done
}

# peer_pids [NAME] -- the peers' pids, only NAME's when given.
peer_pids() {
    local p n
    for p in $(engine_pids); do
        n="$(peer_name "$p")"
        [ -n "$n" ] || continue
        [ -z "${1:-}" ] || [ "$n" = "$1" ] || continue
        echo "$p"
    done
}

# this_peer -- the peer the tree above ops/ serves: QSE_PEER, else the name in its run/peer (a
# peer's start writes it), else nothing: the box's own tree. Callers set HERE to their ops/.
this_peer() {
    if [ -n "${QSE_PEER:-}" ]; then echo "$QSE_PEER"; return 0; fi
    [ -f "$HERE/../run/peer" ] && head -1 "$HERE/../run/peer" | tr -cd 'A-Za-z0-9_-'
    return 0
}

# peer_layer -- start.sh's settings for peer QSE_PEER over serve.env's: the tree above ops/ as
# REPO (its logs, run/ and server/), QSE_PEER_PORT (required, never serve.env's PORT), QSE_PEER_HOST
# (an address, default 127.0.0.1), the engine alone (no conductor, roles, decision models, chatd,
# status file or responder, usage ledger or backends), QSE_PEER_DISK_CACHE (default off; never
# the box's), QSE_PEER_ENGINE_FLAGS in place of QSE_ENGINE_FLAGS. QSE_PEER_NEED_GB (required,
# no default) and QSE_PEER_FLOOR_GB (default 15) are the memory check's.
peer_layer() {
    local n="$QSE_PEER" box_disk="${QSE_DISK_CACHE:-off}"
    case "$n" in [A-Za-z0-9]*) ;; *) n="" ;; esac
    case "$n" in *[!A-Za-z0-9_-]*) n="" ;; esac
    if [ -z "$n" ] || [ "${#n}" -gt 32 ]; then
        echo "[start] QSE_PEER takes a name of letters, digits, - and _ (up to 32)"; return 2
    fi
    case "${QSE_PEER_PORT:-}" in
        ""|*[!0-9]*) echo "[start] peer $n: QSE_PEER_PORT must name its port"; return 2 ;;
    esac
    if [ "$QSE_PEER_PORT" = "$PORT" ]; then
        echo "[start] peer $n: :$PORT is the box's engine's port"; return 2
    fi
    case "${QSE_PEER_NEED_GB:-}" in
        ""|*[!0-9]*) echo "[start] peer $n: QSE_PEER_NEED_GB is required (GiB its load takes)"; return 2 ;;
    esac
    case "${QSE_PEER_FLOOR_GB:-15}" in
        ""|*[!0-9]*) echo "[start] peer $n: QSE_PEER_FLOOR_GB takes whole GiB"; return 2 ;;
    esac
    HOST="${QSE_PEER_HOST:-127.0.0.1}"
    case "$HOST" in
        *[!0-9.:a-fA-F]*|"") echo "[start] peer $n: QSE_PEER_HOST takes an address, not '$HOST'"; return 2 ;;
    esac
    QSE_DISK_CACHE="${QSE_PEER_DISK_CACHE:-off}"
    if [ "$QSE_DISK_CACHE" != off ] && [ "$QSE_DISK_CACHE" = "$box_disk" ]; then
        echo "[start] peer $n: QSE_PEER_DISK_CACHE is the box's disk tier; give the peer its own"; return 2
    fi
    REPO="$(cd "$HERE/.." && pwd)"
    PORT="$QSE_PEER_PORT"
    QSE_MODE=engine CONDUCTOR=raw QSE_CONDUCTOR_CONFIG=off QSE_CHATD=off QSE_STATUS_PORT=
    QSE_USAGE_LEDGER=off QSE_BACKENDS=off ROLES_AUTOLOAD= DROP_PAGE_CACHE=0
    QWEN38_DECIDER_MODELS= QWEN38_NARRATOR_MODEL= QWEN38_SHADOW_MODEL=
    [ -z "${QSE_PEER_ENGINE_FLAGS:-}" ] || QSE_ENGINE_FLAGS="$QSE_PEER_ENGINE_FLAGS"
    export QSE_PEER
    echo "[start] peer $n: tree $REPO, $HOST:$PORT"
}

# peer_check SETUP -- start.sh's refusals for peer QSE_PEER (after peer_layer): setup mode, a tree
# that serves another peer, an engine on its port, a live peer of the same name, and MemAvailable
# under QSE_PEER_NEED_GB + QSE_PEER_FLOOR_GB (tools/memgate.py; QSE_MEMINFO another file, a
# test's). Passing, it marks the tree as the peer's (run/peer).
peer_check() {
    local n="$QSE_PEER" mine pids gate rc
    if [ "$1" = "1" ] || [ -f "$REPO/run/setup-pending" ]; then
        echo "[start] peer $n: REFUSING in setup mode (a peer serves a configured engine)"; return 1
    fi
    mine="$( [ -f "$REPO/run/peer" ] && head -1 "$REPO/run/peer")"
    if [ -n "$mine" ] && [ "$mine" != "$n" ]; then
        echo "[start] peer $n: REFUSING: this tree serves peer $mine (run/peer)"; return 1
    fi
    pids="$(engine_pids "$PORT")"
    if [ -n "$pids" ]; then
        echo "[start] peer $n: REFUSING: an engine holds :$PORT (pid $(echo $pids))"; return 1
    fi
    pids="$(peer_pids "$n")"
    if [ -n "$pids" ]; then
        echo "[start] peer $n: REFUSING: peer $n is alive already (pid $(echo $pids))"; return 1
    fi
    gate="$("${MEMGATE_PY:-python3}" "$REPO/tools/memgate.py" --meminfo "${QSE_MEMINFO:-/proc/meminfo}" \
        --after-load-min-gib "$((QSE_PEER_NEED_GB + ${QSE_PEER_FLOOR_GB:-15}))" 2>&1)"; rc=$?
    echo "$gate" | head -1
    if [ "$rc" != 0 ]; then
        echo "[start] peer $n: REFUSING: MemAvailable does not cover the need ($QSE_PEER_NEED_GB GiB)" \
             "plus the floor (${QSE_PEER_FLOOR_GB:-15} GiB)"; return 1
    fi
    mkdir -p "$REPO/run" && echo "$n" > "$REPO/run/peer"
}
