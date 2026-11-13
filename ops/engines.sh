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

# stage_layer -- SRV-817: start.sh's settings for one stage of a model split in two processes
# (docs/server.md "One model as two stage processes", docs/operations.md), from serve.env or run/local.env:
#   TANDEM_STAGE=LO:HI            0:k the front stage, k:layers the tail
#   TANDEM_LINK_LISTEN=IP:PORT    wait for the other stage here, or
#   TANDEM_LINK_PEER=IP:PORT      dial it there (one of the two, on each side)
#   TANDEM_LINK_KEY=PATH          the key FILE both stages share (mode 0600); never the key itself
#   TANDEM_STAGE_CACHES=on|off    both stages alike (off: one slot, nothing kept)
#   TANDEM_STAGE_SNAPSHOTS_GB=G   the front, caches on: memory for its kept states, a slot
#   TANDEM_STAGE_CACHE_DIR=PATH   the front, caches on: its half of what the tail's disk tier keeps
#   TANDEM_STAGE_CACHE_GB=G       the front: that folder's size
# Sets STAGE_ROLE (front or tail) and STAGE_FLAGS (the server's flags), and the engine alone (no conductor,
# roles, decision models or chatd) with the KV pool off, which a stage does not serve; or prints one line
# with the reason and returns 2: a link key without a stage, a stage without one of the link addresses or
# with both, a wildcard address, a key that is not a file's path, a front with a disk tier (QSE_DISK_CACHE
# is the tail's), the front's keys on a tail or without caches, more than one slot without caches. Without
# TANDEM_STAGE (and none of its keys) it sets nothing.
stage_layer() {
    STAGE_ROLE=""; STAGE_FLAGS=()
    local st="${TANDEM_STAGE:-}" lo hi k addr flag caches slots
    local front_keys="TANDEM_STAGE_SNAPSHOTS_GB TANDEM_STAGE_CACHE_DIR TANDEM_STAGE_CACHE_GB"
    if [ -z "$st" ]; then
        for k in TANDEM_LINK_LISTEN TANDEM_LINK_PEER TANDEM_LINK_KEY TANDEM_STAGE_CACHES $front_keys; do
            if [ -n "${!k:-}" ]; then echo "[start] REFUSING: $k needs TANDEM_STAGE (LO:HI)"; return 2; fi
        done
        return 0
    fi
    if [[ ! "$st" =~ ^[0-9]{1,4}:[0-9]{1,4}$ ]] || [ "$((10#${st%%:*}))" -ge "$((10#${st#*:}))" ]; then
        echo "[start] REFUSING: TANDEM_STAGE takes LO:HI with LO < HI (0:k the front, k:layers the tail)"; return 2
    fi
    lo=$((10#${st%%:*})); hi=$((10#${st#*:}))
    STAGE_ROLE=tail; [ "$lo" = 0 ] && STAGE_ROLE=front
    if [ -n "${TANDEM_LINK_LISTEN:-}" ] && [ -n "${TANDEM_LINK_PEER:-}" ]; then
        echo "[start] REFUSING: the $STAGE_ROLE stage takes one of TANDEM_LINK_LISTEN and TANDEM_LINK_PEER, not both"
        return 2
    fi
    if [ -n "${TANDEM_LINK_LISTEN:-}" ]; then addr="$TANDEM_LINK_LISTEN"; flag=--link-listen
    elif [ -n "${TANDEM_LINK_PEER:-}" ]; then addr="$TANDEM_LINK_PEER"; flag=--link-peer
    else
        echo "[start] REFUSING: the $STAGE_ROLE stage needs TANDEM_LINK_LISTEN or TANDEM_LINK_PEER (IP:PORT)"
        return 2
    fi
    if [[ ! "$addr" =~ ^(\[[0-9a-fA-F:]+\]|[0-9.]+):[0-9]{1,5}$ ]]; then
        echo "[start] REFUSING: ${flag#--} takes an IP literal and a port (IP:PORT, [IPv6]:PORT), not '$addr'"
        return 2
    fi
    case "${addr%:*}" in
        0.0.0.0|"[::]") echo "[start] REFUSING: ${flag#--} $addr is every interface; name one IP"; return 2 ;;
    esac
    case "${TANDEM_LINK_KEY:-}" in
        "") echo "[start] REFUSING: the $STAGE_ROLE stage needs TANDEM_LINK_KEY, the path of the key file (mode 0600)"
            return 2 ;;
        /*|~/*|./*|../*) ;;
        *) echo "[start] REFUSING: TANDEM_LINK_KEY names the key file's path, never the key itself"; return 2 ;;
    esac
    if [ ! -f "${TANDEM_LINK_KEY/#\~/$HOME}" ]; then
        echo "[start] REFUSING: TANDEM_LINK_KEY names no file (the key file both stages share, mode 0600)"; return 2
    fi
    caches="${TANDEM_STAGE_CACHES:-off}"
    case "$caches" in on|off) ;; *) echo "[start] REFUSING: TANDEM_STAGE_CACHES is on or off"; return 2 ;; esac
    if [ "$STAGE_ROLE" = front ] && [ "${QSE_DISK_CACHE:-off}" != off ]; then
        echo "[start] REFUSING: a front stage takes no disk tier: QSE_DISK_CACHE=off (the tail's; the front's half" \
             "goes to TANDEM_STAGE_CACHE_DIR)"
        return 2
    fi
    for k in $front_keys; do
        [ -n "${!k:-}" ] || continue
        if [ "$STAGE_ROLE" = tail ]; then echo "[start] REFUSING: $k is the front stage's"; return 2; fi
        if [ "$caches" != on ]; then echo "[start] REFUSING: $k needs TANDEM_STAGE_CACHES=on"; return 2; fi
    done
    for k in TANDEM_STAGE_SNAPSHOTS_GB TANDEM_STAGE_CACHE_GB; do
        if [ -n "${!k:-}" ] && [[ ! "${!k}" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
            echo "[start] REFUSING: $k takes GiB (a number)"; return 2
        fi
    done
    slots="${QSE_PARALLEL:-1}"
    if [ "$caches" = off ] && [ "${slots:-1}" != 1 ]; then
        echo "[start] REFUSING: a stage without caches serves one slot: QSE_PARALLEL=1 (side slots:" \
             "TANDEM_STAGE_CACHES=on on both stages)"
        return 2
    fi
    STAGE_FLAGS=(--stage "$st" "$flag" "$addr" --link-key-file "${TANDEM_LINK_KEY/#\~/$HOME}" --stage-caches "$caches")
    [ -z "${TANDEM_STAGE_SNAPSHOTS_GB:-}" ] || STAGE_FLAGS+=(--stage-snapshots-gb "$TANDEM_STAGE_SNAPSHOTS_GB")
    [ -z "${TANDEM_STAGE_CACHE_DIR:-}" ] || STAGE_FLAGS+=(--stage-cache-dir "${TANDEM_STAGE_CACHE_DIR/#\~/$HOME}")
    [ -z "${TANDEM_STAGE_CACHE_GB:-}" ] || STAGE_FLAGS+=(--stage-cache-gb "$TANDEM_STAGE_CACHE_GB")
    # what a stage does not serve, as the server would refuse it: the engine alone, rows in place
    QSE_MODE=engine CONDUCTOR=raw QSE_CONDUCTOR_CONFIG=off QSE_CHATD=off QSE_BACKENDS=off ROLES_AUTOLOAD=
    QWEN38_DECIDER_MODELS= QWEN38_NARRATOR_MODEL= QWEN38_SHADOW_MODEL=
    QSE_KV_POOL=off; export QSE_KV_POOL
    echo "[start] stage: the $STAGE_ROLE, layers $st, ${flag#--} $addr, caches $caches, slots $slots" \
         "(the engine alone, KV pool off)"
}
