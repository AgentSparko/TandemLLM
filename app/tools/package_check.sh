#!/bin/bash
# The installable package, checked as a stranger would use it: build the wheel, install it into a
# clean venv with no torch, import TandemApp from it with nothing else on the path (no server,
# engine, torch or numpy may load), run `tandem-app version` and `check`, start `tandem-app serve`
# from an env file with a fake backend and read /v1/models, the page and the System body, then
# start `tandem-node` from the wheel and read its identity as a controller does.
#
#   bash tools/package_check.sh [--extras host] [--out DIR]
#
# Needs Python 3.11 or newer as $PY (default python3) and the network for pip (cryptography,
# setuptools; psutil and nvidia-ml-py with --extras host). Writes nothing outside a temp dir and
# DIR (default results/package-check-<time>). Ports: the fake backend 19100-19299, serve
# 19300-19499, tandem-node 19500-19589. Exit 0 when every check passed.
#
# It never reaches this machine's own TandemNode (SRV-745). After the pip steps every command runs
# with HOME in the temp dir and TANDEM_NODE_AGENT=off, and the conductor's documents and the
# agent's state live in the temp dir. The real node's claim and trust store
# (~/.tandemllm/node/fleet.json and trust/*, under the home in the password database, as
# tests/realnode.py reads it) are hashed before the pip steps and again at the exit, compared here
# and never printed: a change fails the check, and so does a file there that cannot be read.
set -u
cd "$(dirname "$0")/.."
REPO=$PWD
PY=${PY:-python3}
EXTRAS=""; OUT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --extras) EXTRAS="[$2]"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    *) echo "unknown argument $1"; exit 2 ;;
  esac
done
OUT=${OUT:-results/package-check-$(date +%Y%m%d-%H%M)}
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
T=$(mktemp -d "${TMPDIR:-/tmp}/tandem-pkg-XXXXXX") || { echo "FAIL no temp dir"; exit 1; }
SRV=""; FAKE=""; NODE=""; REAL=""; REAL_DONE=""
fail() { echo "FAIL $*" | tee -a "$OUT/summary.txt"; exit 1; }

halt() {                          # a process this check started: TERM, then KILL after 10 s
  local i
  [ -n "$1" ] || return 0
  kill "$1" 2>/dev/null
  for i in $(seq 50); do
    kill -0 "$1" 2>/dev/null || return 0
    sleep 0.2
  done
  kill -KILL "$1" 2>/dev/null
}
stop() { halt "$SRV"; halt "$FAKE"; halt "$NODE"; SRV=""; FAKE=""; NODE=""; }

# {path: sha256} of the real node's fleet.json and trust/*; `same` compares it with the JSON on
# stdin and prints the changed names and the count, never a hash. A missing file or dir is
# absent; one that exists but cannot be read ends `snap` and `same` alike with its name (exit 1),
# so it is never left out of the comparison
REALNODE_PY='
import hashlib, json, os, pwd, sys
home = pwd.getpwuid(os.getuid()).pw_dir
node = os.path.join(home, ".tandemllm", "node")
def short(p):
    return "~" + p[len(home):] if p.startswith(home + os.sep) else p
def state():
    out = {}
    for root in sorted({node, os.path.realpath(node)}):
        names = [os.path.join(root, "fleet.json")]
        trust = os.path.join(root, "trust")
        try:
            names += [os.path.join(trust, n) for n in sorted(os.listdir(trust))]
        except (FileNotFoundError, NotADirectoryError):
            pass
        except OSError as exc:
            sys.exit(f"unreadable {short(trust)}: {exc.strerror or exc}")
        for p in names:
            try:
                with open(p, "rb") as f:
                    out[p] = hashlib.sha256(f.read()).hexdigest()
            except (FileNotFoundError, NotADirectoryError):
                pass
            except OSError as exc:
                sys.exit(f"unreadable {short(p)}: {exc.strerror or exc}")
    return out
if sys.argv[1] == "snap":
    json.dump(state(), sys.stdout)
    sys.exit(0)
was, now = json.load(sys.stdin), state()
bad = sorted(p for p in set(was) | set(now) if was.get(p) != now.get(p))
for p in bad:
    print("changed", short(p))
print(f"{len(now)} files")
sys.exit(1 if bad else 0)
'
realnode() { "$PY" -I -c "$REALNODE_PY" "$@"; }
real_check() {                    # once, after what this check started has stopped
  [ -n "$REAL" ] && [ -z "$REAL_DONE" ] || return 0
  REAL_DONE=1
  stop
  if printf '%s' "$REAL" | realnode same >"$T/real.txt" 2>&1; then
    echo "PASS the real TandemNode state unchanged ($(tail -n 1 "$T/real.txt"))" \
      | tee -a "$OUT/summary.txt"
    return 0
  fi
  tee -a "$OUT/summary.txt" <"$T/real.txt"
  echo "FAIL the real TandemNode state changed" | tee -a "$OUT/summary.txt"
  return 1
}
finish() {
  local rc=$?
  real_check || rc=1
  stop
  case "$T" in */tandem-pkg-??????) rm -rf "$T" ;; esac
  exit "$rc"
}
trap finish EXIT
REAL=$(realnode snap 2>"$T/real.txt") \
  || { REAL=""; tee -a "$OUT/summary.txt" <"$T/real.txt"; \
       fail "reading the real TandemNode state"; }

# the source as git would ship it (no node_modules, no results), so the wheel sees what CI sees
mkdir -p "$T/src"
if git -C "$REPO" rev-parse >/dev/null 2>&1; then
  git -C "$REPO" ls-files -z --cached --others --exclude-standard \
    | (cd "$REPO" && xargs -0 tar -cf - 2>/dev/null) | tar -xf - -C "$T/src"
else
  cp -R "$REPO/." "$T/src/"
fi
# the built page is a build output, not in git (OPS-246): the wheel takes the checkout's bundle,
# which must be intact (tools/dashboard_bundle.py build or fetch makes it)
if [ ! -f "$REPO/dashboard/dist/index.html" ]; then
  "$PY" -I "$REPO/tools/dashboard_bundle.py" verify "$REPO/dashboard/dist" >"$OUT/bundle.log" 2>&1
  fail "no built page in dashboard/dist: run 'python3 tools/dashboard_bundle.py build' (Node) or fetch a released bundle first"
fi
"$PY" -I "$REPO/tools/dashboard_bundle.py" verify "$REPO/dashboard/dist" >"$OUT/bundle.log" 2>&1 \
  || fail "dashboard/dist does not match its bundle.json (bundle.log)"
# git's list leaves the ignored dist/ out; a copy without git has it already
[ -e "$T/src/dashboard/dist" ] || cp -R "$REPO/dashboard/dist" "$T/src/dashboard/dist"
BUNDLE=$("$PY" -I -c 'import json, sys; print(json.load(open(sys.argv[1]))["sha256"])' \
  "$T/src/dashboard/dist/bundle.json") || fail "reading the bundle's manifest"
echo "PASS the page's bundle ${BUNDLE:0:16} verifies" | tee -a "$OUT/summary.txt"
$PY -m venv "$T/build" && "$T/build/bin/pip" -q install build >"$OUT/build.log" 2>&1 \
  || fail "the build venv"
"$T/build/bin/python" -m build --wheel "$T/src" -o "$T/wheel" >>"$OUT/build.log" 2>&1 \
  || fail "the wheel build (build.log)"
WHL=$(ls "$T"/wheel/*.whl)
echo "wheel $(basename "$WHL") $(du -k "$WHL" | cut -f1) KB" | tee "$OUT/summary.txt"
unzip -l "$WHL" > "$OUT/wheel-files.txt"
if grep -Eq "tests/|node_modules|\.safetensors|\.pt$|/src/.*\.ts$" "$OUT/wheel-files.txt"; then
  fail "the wheel holds tests, node_modules, weights or dashboard sources"
fi
grep -q "proxy/dashboard/index.html" "$OUT/wheel-files.txt" || fail "no built page in the wheel"
grep -q "proxy/dashboard/bundle.json" "$OUT/wheel-files.txt" || fail "no bundle manifest in the wheel"
# chatd's launcher, which TandemNode runs from a wheel install (OPS-250)
grep -q "chatd/ops/chatd.sh" "$OUT/wheel-files.txt" || fail "no chatd launcher (chatd/ops/chatd.sh) in the wheel"
# every file of the built page, as proxy/dashboard/<path> (sw.js was missing until SRV-747)
"$PY" -I - "$WHL" "$T/src/dashboard/dist" >"$OUT/dist.log" 2>&1 <<'EOF' \
  || fail "dashboard/dist files missing from the wheel (dist.log)"
import os, sys, zipfile
names = set(zipfile.ZipFile(sys.argv[1]).namelist())
root = sys.argv[2]
files = sorted(os.path.relpath(os.path.join(d, f), root).replace(os.sep, "/")
               for d, _, fs in os.walk(root) for f in fs)
missing = [f for f in files if "proxy/dashboard/" + f not in names]
print(len(files), "files in dashboard/dist,", len(missing), "missing:", missing[:20])
sys.exit(1 if missing or not files else 0)
EOF
echo "PASS the built page in the wheel ($(cut -d' ' -f1 "$OUT/dist.log") files)" \
  | tee -a "$OUT/summary.txt"

$PY -m venv "$T/venv" || fail "the clean venv"
"$T/venv/bin/pip" -q install "$WHL$EXTRAS" >"$OUT/install.log" 2>&1 || fail "pip install (install.log)"
"$T/venv/bin/pip" list --format=freeze > "$OUT/pip-freeze.txt"
if grep -Eiq "^(torch|numpy|transformers)==" "$OUT/pip-freeze.txt"; then
  fail "the install pulled torch, numpy or transformers"
fi
echo "PASS install without torch ($(wc -l < "$OUT/pip-freeze.txt") packages)" | tee -a "$OUT/summary.txt"

# from here on: a home of its own and no TandemNode agent, so nothing reads or claims this
# machine's node (its loopback secret is under the real home)
mkdir -p "$T/home"
export HOME="$T/home" TANDEM_NODE_AGENT=off

# a fake backend from the checkout's tests (not part of the wheel)
FPORT=$((19100 + $$ % 200)); PORT=$((19300 + $$ % 200)); NPORT=$((19500 + $$ % 90))
$PY "$REPO/tests/fake_backend.py" --port $FPORT --models fake-a >"$OUT/fake.log" 2>&1 &
FAKE=$!
cat > "$T/app.env" <<EOF
TANDEM_BACKENDS=fake
TANDEM_BACKEND_FAKE_KIND=generic
TANDEM_BACKEND_FAKE_URL=http://127.0.0.1:$FPORT/v1
TANDEM_SECRETS_KEY=a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s=
EOF
cd "$T"                                     # nowhere near the checkout: the wheel alone
# TandemApp's entry points and heaviest modules, the venv alone on the path: no engine loads, and
# there is no `server` to find
"$T/venv/bin/python" -I - >"$OUT/imports.log" 2>&1 <<'EOF' \
  || fail "the installed app reaches the engine (imports.log)"
import importlib, importlib.util, sys
for name in ("proxy.__main__", "proxy.app", "proxy.nodeagent", "chatd", "tandemapp.conductor.routes"):
    importlib.import_module(name)
bad = sorted(m for m in sys.modules if m.split(".")[0] in ("server", "engine", "torch", "numpy"))
spec = importlib.util.find_spec("server")
print(len(sys.modules), "modules; server, engine, torch or numpy:", bad, "; find_spec(server):", spec)
sys.exit(1 if bad or spec is not None else 0)
EOF
echo "PASS imports: proxy.__main__, proxy.app, proxy.nodeagent, chatd, tandemapp.conductor.routes;" \
  "no server, engine, torch or numpy" | tee -a "$OUT/summary.txt"
APP="$T/venv/bin/tandem-app"
VER=$(basename "$WHL" | cut -d- -f2)      # the wheel's version: VERSION's, as PEP 440 writes it
WANT=$("$T/venv/bin/python" -I -c 'import sys; from proxy import _version
print(_version.pep440(open(sys.argv[1]).read()))' "$T/src/VERSION") || fail "reading VERSION"
[ "$VER" = "$WANT" ] || fail "the wheel says $VER, VERSION $WANT"
V=$("$APP" version) || fail "tandem-app version"
echo "$V" | tee -a "$OUT/summary.txt"
case "$V" in "tandemapp $VER "*) ;; *) fail "tandem-app version does not say tandemapp $VER" ;; esac
OLD="$T/venv/bin/tandem-dash"               # the old name (OPS-238): one line on stderr, the same answer
"$OLD" version 2>"$OUT/alias.err" | cmp -s - <("$APP" version) \
  || fail "the old name answered differently"
[ "$(wc -l < "$OUT/alias.err")" -eq 1 ] && grep -q "is now tandem-app" "$OUT/alias.err" \
  || fail "the old name did not say its new one (alias.err)"
echo "PASS the old name" | tee -a "$OUT/summary.txt"
"$APP" check --env-file "$T/app.env" --config-db "$T/data/config.sqlite3" \
  --conductor-config "$T/conductor" >"$OUT/check.log" 2>&1 || fail "tandem-app check (check.log)"
grep -q "^page .*proxy/dashboard bundle ${BUNDLE:0:16} " "$OUT/check.log" \
  || fail "check did not find the packaged page with its bundle"
[ -e "$T/data/config.sqlite3" ] && fail "check wrote the config database"
echo "PASS check" | tee -a "$OUT/summary.txt"
QSE_TEST=1 "$APP" serve --env-file "$T/app.env" --config-db "$T/data/config.sqlite3" \
  --usage-ledger "$T/data/ledger.sqlite3" --conductor-config "$T/conductor" --port $PORT \
  --no-keychain >"$OUT/serve.log" 2>&1 &
SRV=$!
"$T/venv/bin/python" - "$PORT" "$OUT" "$BUNDLE" <<'EOF' || fail "serve (serve.log, $OUT/serve-check.log)"
import json, sys, time, urllib.request
port, out, bundle = sys.argv[1], sys.argv[2], sys.argv[3]
base = f"http://127.0.0.1:{port}"
log = open(f"{out}/serve-check.log", "w")
def get(path, raw=False):
    with urllib.request.urlopen(base + path, timeout=10) as r:
        body = r.read()
        return (r.status, body) if raw else json.loads(body)
for _ in range(100):
    try:
        get("/health"); break
    except OSError:
        time.sleep(0.2)
time.sleep(1.5)
ids = [m["id"] for m in get("/v1/models")["data"]]
print("models", ids, file=log)
assert ids == ["fake-a"], ids
st, page = get("/dashboard/", raw=True)
assert st == 200 and b"<script" in page and b"not built" not in page, page[:200]
with urllib.request.urlopen(base + "/dashboard/", timeout=10) as r:      # OPS-246
    tag = r.headers.get("X-Tandem-Bundle")
health = get("/health")
print("bundle", tag, health.get("dashboard"), file=log)
assert tag == bundle[:16], (tag, bundle)
assert health["dashboard"]["sha256"] == bundle and health["dashboard"]["verified"], health
sysd = get("/v1/dashboard/system")
print("version", sysd["engine"]["version"], "hosts", [h["source"] for h in sysd.get("hosts", [])],
      file=log)
assert sysd["engine"]["version"], "the version from the package metadata"
assert sysd.get("hosts"), "the host collector answered"
r = urllib.request.Request(base + "/v1/chat/completions", data=json.dumps(
    {"model": "fake-a", "messages": [{"role": "user", "content": "hi"}]}).encode(),
    headers={"Content-Type": "application/json"})
with urllib.request.urlopen(r, timeout=30) as resp:
    assert resp.status == 200
print("ok", file=log)
EOF
echo "PASS serve: /v1/models, /dashboard/ with its bundle, system with hosts, one request" \
  | tee -a "$OUT/summary.txt"
# TandemNode from the wheel: a virtual node with its own state dir and port, on loopback, no mDNS;
# its identity read as a controller reads it (proxy/nodesource.py AgentNode, the loopback secret)
"$T/venv/bin/tandem-node" serve --host 127.0.0.1 --port $NPORT --state-dir "$T/node" \
  --virtual pkgcheck --mdns off >"$OUT/node.log" 2>&1 &
NODE=$!
"$T/venv/bin/python" -I - "$NPORT" "$T/node/secret" "$VER" >"$OUT/node-check.log" 2>&1 <<'EOF' \
  || fail "tandem-node (node.log, node-check.log)"
import sys, time
from proxy import nodesource, nodetrust
port, secret, want = sys.argv[1], sys.argv[2], sys.argv[3]
node = nodesource.AgentNode(f"http://127.0.0.1:{port}",
                            secret=lambda: nodetrust.load_secret(secret))
ident = None
for _ in range(150):
    ident = node.identity()
    if ident:
        break
    time.sleep(0.2)
node.close()
assert ident, "no identity within 30 s"
print("version", ident.get("tandemllm"), "virtual", ident.get("virtual"))
assert ident.get("tandemllm") == want, (ident.get("tandemllm"), want)
assert ident.get("virtual") == "pkgcheck", ident.get("virtual")
EOF
halt "$NODE"; NODE=""
echo "PASS tandem-node: its identity through AgentNode, version $VER; stopped" \
  | tee -a "$OUT/summary.txt"
"$T/venv/bin/pip" list --format=freeze | grep -Eiq "^torch==" && fail "torch appeared"
real_check || exit 1
echo "PASS all ($OUT)" | tee -a "$OUT/summary.txt"
