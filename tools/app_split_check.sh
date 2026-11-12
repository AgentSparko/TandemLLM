#!/bin/bash
# The AC5 dry run (SRV-748): TandemApp's tree, app/, split out as its own repository, on two
# scratch clones of one branch. Nothing here touches the repository it reads: it never adds a
# remote, never pushes and never passes --force to git filter-repo, so filter-repo's own
# fresh-clone check refuses to run anywhere but in the scratch clones.
#
#   bash tools/app_split_check.sh [--repo DIR] [--branch NAME] [--tar FILE]
#
# - The path list ($W/paths.txt, outside both clones) is app/ plus every older name of every file
#   under app/ at the branch head, from one pass of `git log -M --diff-filter=R` with the rename
#   chains followed transitively. An older name that exists at the head outside app/ (a file or a
#   directory) would carry engine code into the split: it is dropped and printed.
# - $W/split runs that list with --path-rename app/: (the history the split keeps); $W/literal
#   runs the AC's literal `git filter-repo --path app/`.
# - Checks: both clones are plain fresh clones; the split's HEAD tree and the literal clone's
#   HEAD:app equal <branch>:app; `git log --follow` in the split reaches the commits before the
#   move (tandemapp/ledger.py to server/ledger.py, proxy/app.py to its first commit); a wheel
#   tandemapp-<app/VERSION>-py3-none-any.whl builds from both, with the same files.
# - --tar FILE receives the split's HEAD tree (git archive) for runs elsewhere.
#
# Needs git-filter-repo and $PY (default python3) with setuptools >= 77 (app/pyproject.toml); pip
# runs with --no-deps --no-build-isolation --no-cache-dir. Writes nothing outside a temp dir and
# FILE. Exit 0 only when every check holds.
set -u
REPO=""; BRANCH=tandemapp; TAR=""
PY=${PY:-python3}
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="$2"; shift 2 ;;
    --branch) BRANCH="$2"; shift 2 ;;
    --tar) TAR="$2"; shift 2 ;;
    *) echo "unknown argument $1"; exit 2 ;;
  esac
done
die() { echo "FAIL $*"; exit 1; }
[ -n "$REPO" ] || REPO=$(git -C "$(dirname "$0")" rev-parse --show-toplevel) || die "no repository"
REPO=$(cd "$REPO" && pwd -P) || die "no directory $REPO"
if [ -n "$TAR" ]; then
  case "$TAR" in /*) ;; *) TAR="$PWD/$TAR" ;; esac
fi
git filter-repo --version >/dev/null 2>&1 || die "git filter-repo is not installed"
SRC_HEAD=$(git -C "$REPO" rev-parse --verify -q "refs/heads/$BRANCH^{commit}") \
  || die "no branch $BRANCH in $REPO"
SRC_APP=$(git -C "$REPO" rev-parse --verify -q "$SRC_HEAD:app") || die "$BRANCH has no app/"
VER=$(git -C "$REPO" show "$SRC_HEAD:app/VERSION" | tr -d '[:space:]')
[ -n "$VER" ] || die "$BRANCH has no app/VERSION"

# the scratch dir: removed at the exit, and only when its name has mktemp's shape
TBASE=${TMPDIR:-/tmp}
W=$(mktemp -d "$TBASE/app-split-XXXXXX") || die "no temp dir"
cleanup() {
  case "$W" in
    "$TBASE/app-split-"??????) rm -rf -- "$W" ;;
    *) echo "kept $W (unexpected name)" ;;
  esac
}
trap cleanup EXIT
trap 'exit 130' INT TERM

FAILS=0
ok() { echo "ok   $*"; }
bad() { echo "FAIL $*"; FAILS=$((FAILS + 1)); }

echo "repo $REPO, branch $BRANCH at $SRC_HEAD, app/ tree $SRC_APP, app VERSION $VER"

# the path list: app/ and the older names of its files, minus those the head holds outside app/
git -C "$REPO" log -M -l0 --name-status --diff-filter=R -z --format= "$SRC_HEAD" > "$W/renames.z" \
  || die "git log of the renames"
git -C "$REPO" ls-tree -r -z --name-only "$SRC_HEAD" > "$W/head.z" || die "git ls-tree"
"$PY" -I - "$W/renames.z" "$W/head.z" "$W/paths.txt" "$W/dropped.txt" <<'EOF' || die "path list"
import sys
renames, head, out, dropped = sys.argv[1:5]
f = open(renames, "rb").read().split(b"\0")
older_of, i = {}, 0
while i < len(f):
    status = f[i].strip(b"\n")
    if not status:
        i += 1
        continue
    if not status.startswith(b"R") or i + 2 >= len(f):
        sys.exit("unexpected git log line %r" % status)
    older_of.setdefault(f[i + 2], set()).add(f[i + 1])
    i += 3
files = {p for p in open(head, "rb").read().split(b"\0") if p}
dirs = {p[:k] for p in files for k in range(len(p)) if p[k:k + 1] == b"/"}
app = sorted(p for p in files if p.startswith(b"app/"))
seen, todo = set(app), list(app)
while todo:                                   # the rename chains, transitively
    for old in older_of.get(todo.pop(), ()):
        if old not in seen:
            seen.add(old)
            todo.append(old)
older = sorted(p for p in seen - set(app) if not p.startswith(b"app/"))
drop = [p for p in older if p in files or p in dirs]
keep = [p for p in older if p not in files and p not in dirs]
for p in keep:
    if b"\n" in p or b"==>" in p:
        sys.exit("a name filter-repo cannot take: %r" % p)
with open(out, "wb") as fh:
    fh.write(b"literal:app/\n" + b"".join(b"literal:" + p + b"\n" for p in keep))
with open(dropped, "wb") as fh:
    fh.write(b"".join(p + b"\n" for p in drop))
print("path list: app/ (%d files) and %d older names; %d older names dropped"
      % (len(app), len(keep), len(drop)))
EOF
while IFS= read -r name; do
  echo "dropped $name (exists at the head outside app/)"
done < "$W/dropped.txt"

# two fresh clones, each its own repository with one work tree; no remote is ever added
for c in split literal; do
  git clone -q --no-local --single-branch --branch "$BRANCH" "$REPO" "$W/$c" || die "clone $c"
  gd=$(git -C "$W/$c" rev-parse --absolute-git-dir) || die "clone $c: no git dir"
  [ "$(cd "$gd" && pwd -P)" = "$(cd "$W/$c" && pwd -P)/.git" ] || die "clone $c: git dir is $gd"
  [ "$(git -C "$W/$c" worktree list | wc -l | tr -d ' ')" = 1 ] || die "clone $c: more than one work tree"
  [ "$(git -C "$W/$c" rev-parse HEAD)" = "$SRC_HEAD" ] || die "clone $c: HEAD is not $SRC_HEAD"
  echo "clone $c: $(git -C "$W/$c" rev-list --count HEAD) commits"
done

# the two filter-repo runs, without --force
git -C "$W/split" filter-repo --paths-from-file "$W/paths.txt" --path-rename app/: \
  > "$W/split.log" 2>&1 || { tail -n 20 "$W/split.log"; die "filter-repo in the split clone"; }
git -C "$W/literal" filter-repo --path app/ \
  > "$W/literal.log" 2>&1 || { tail -n 20 "$W/literal.log"; die "filter-repo in the literal clone"; }
for c in split literal; do
  echo "filtered $c: $(git -C "$W/$c" rev-list --count HEAD) commits, remotes: [$(git -C "$W/$c" remote | tr '\n' ' ')]"
done

# the trees
t=$(git -C "$W/split" rev-parse "HEAD^{tree}")
[ "$t" = "$SRC_APP" ] && ok "split HEAD tree = $BRANCH:app ($t)" || bad "split HEAD tree $t != $BRANCH:app $SRC_APP"
t=$(git -C "$W/literal" rev-parse "HEAD:app")
[ "$t" = "$SRC_APP" ] && ok "literal HEAD:app = $BRANCH:app ($t)" || bad "literal HEAD:app $t != $BRANCH:app $SRC_APP"

# the history: --follow reaches the old name and the first commit that touched it
follow() {  # follow NEW OLD
  local want got n
  want=$(git -C "$REPO" log --reverse --format='%at %ae %s' "$SRC_HEAD" -- "$2" | sed -n 1p)
  git -C "$W/split" log --follow --name-only --format='@@ %at %ae %s' -- "$1" > "$W/follow.txt"
  n=$(grep -c '^@@ ' "$W/follow.txt")
  got=$(grep -c -Fx "$2" "$W/follow.txt")
  if [ -n "$want" ] && [ "$got" -gt 0 ] && grep -q -Fx "@@ $want" "$W/follow.txt"; then
    ok "--follow $1: $n commits, $got name $2, back to its first commit ($(git -C "$REPO" log --reverse --format='%h %ad' --date=short "$SRC_HEAD" -- "$2" | sed -n 1p))"
  else
    bad "--follow $1: $n commits, $got name $2, first commit of $2 reached: no"
  fi
}
follow tandemapp/ledger.py server/ledger.py
follow proxy/app.py proxy/app.py

# the wheels
whl="tandemapp-$VER-py3-none-any.whl"
wheel() {  # wheel LABEL DIR
  mkdir -p "$W/wheel-$1"
  if (cd "$2" && PIP_DISABLE_PIP_VERSION_CHECK=1 "$PY" -m pip wheel -q --no-deps --no-build-isolation \
        --no-cache-dir -w "$W/wheel-$1" .) > "$W/wheel-$1.log" 2>&1 && [ -f "$W/wheel-$1/$whl" ]; then
    ok "wheel from $1: $whl"
  else
    tail -n 20 "$W/wheel-$1.log"; bad "wheel from $1: no $whl ($(ls "$W/wheel-$1" | tr '\n' ' '))"
  fi
}
wheel split "$W/split"
wheel literal "$W/literal/app"
if [ -f "$W/wheel-split/$whl" ] && [ -f "$W/wheel-literal/$whl" ]; then
  if "$PY" -I -c 'import sys, zipfile
a, b = (sorted(zipfile.ZipFile(p).namelist()) for p in sys.argv[1:3])
print(len(a)); sys.exit(a != b)' "$W/wheel-split/$whl" "$W/wheel-literal/$whl" > "$W/wheel-n.txt"; then
    ok "both wheels hold the same $(cat "$W/wheel-n.txt") files"
  else
    bad "the two wheels hold different files"
  fi
fi

if [ -n "$TAR" ]; then
  git -C "$W/split" archive --format=tar HEAD > "$TAR" && ok "split HEAD tree written to $TAR" \
    || bad "git archive to $TAR"
fi

if [ "$FAILS" = 0 ]; then echo "PASS every check"; exit 0; fi
echo "FAIL $FAILS checks"; exit 1
