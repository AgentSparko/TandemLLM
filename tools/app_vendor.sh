#!/usr/bin/env bash
# Vendor TandemApp into app/ (ENG-413): until WP14 removes app/, TandemApp's repository is the source
# and app/ is a copy of one of its commits, recorded in app.pin. Nobody edits app/ by hand; this
# tool replaces it whole and commits that as one commit, `ENG-413: vendor TandemApp <sha>`.
#
#   bash tools/app_vendor.sh --from SRC COMMIT            vendor COMMIT of the checkout SRC, commit
#   bash tools/app_vendor.sh --from SRC COMMIT --dry-run  print what would change; nothing is written
#   bash tools/app_vendor.sh --check                      app/ in HEAD and on disk against app.pin
#
# SRC is any clone of TandemApp holding COMMIT (read only: this tool runs only ls-tree and cat-file
# there). The copy is the commit's tree less the paths in EXCLUDE: the files that exist only because
# TandemApp is a repository of its own (its README, its CI, its docs index and licence). Inside the
# engine's tree they would sit under app/ where nothing reads them, and the engine's own README, CI
# and docs licence cover that place. app.pin records the commit, the commit's tree and the tree of
# app/ (that tree less EXCLUDE), so tests/test_app_pin.py can hash app/ in a copy without .git.
#
# Refuses: a working tree that is not clean (untracked files included), a COMMIT SRC does not have,
# a submodule in COMMIT, and any staged change outside app/ and app.pin. Exit 0 also when app/ and
# app.pin already are COMMIT's (nothing to commit).
set -euo pipefail
export LC_ALL=C

EXCLUDE=(".github/" "README.md" "docs/README.md" "docs/LICENSE")
DIR=app
PIN=app.pin

die() { printf 'app_vendor: %s\n' "$*" >&2; exit 1; }
usage() { sed -n '2,23p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 2; }

ROOT="$(git -C "$(dirname "${BASH_SOURCE[0]}")/.." rev-parse --show-toplevel)" \
    || die "not inside a git checkout"
src='' rev='' dry=0 check=0
while [ $# -gt 0 ]; do
    case "$1" in
        --from) [ $# -ge 2 ] || usage; src="$2"; shift 2 ;;
        --dry-run) dry=1; shift ;;
        --check) check=1; shift ;;
        -h|--help) usage ;;
        -*) die "unknown option $1" ;;
        *) [ -z "$rev" ] || die "one COMMIT only"; rev="$1"; shift ;;
    esac
done

pin_get() { sed -n "s/^$1 //p" "$ROOT/$PIN" 2>/dev/null | head -1; }

if [ "$check" = 1 ]; then
    [ -f "$ROOT/$PIN" ] || die "no $PIN"
    want="$(pin_get tree)"
    head_tree="$(git -C "$ROOT" rev-parse "HEAD:$DIR")"
    [ "$head_tree" = "$want" ] || die "HEAD:$DIR is $head_tree, $PIN says $want"
    if [ -n "$(git -C "$ROOT" status --porcelain --untracked-files=all -- "$DIR" "$PIN")" ]; then
        die "$DIR/ or $PIN differs from HEAD on disk"
    fi
    echo "app_vendor: $DIR/ is TandemApp $(pin_get commit) (tree $want)"
    exit 0
fi

[ -n "$src" ] && [ -n "$rev" ] || usage
[ -d "$src" ] || die "--from: $src is not a directory"
sha="$(git -C "$src" rev-parse --verify -q "$rev^{commit}")" || die "$src has no commit $rev"
src_tree="$(git -C "$src" rev-parse "$sha^{tree}")"
if [ "$dry" = 0 ] && [ -n "$(git -C "$ROOT" status --porcelain --untracked-files=all)" ]; then
    die "the working tree of $ROOT is not clean (commit or stash first)"
fi

tmp="$(mktemp -d "${TMPDIR:-/tmp}/app-vendor.XXXXXX")"
trap 'rm -f "$tmp"/idx "$tmp"/idx.lock "$tmp"/src.ls "$tmp"/want.ls "$tmp"/head.ls "$tmp"/got.ls \
      "$tmp"/excluded.ls; rmdir "$tmp" 2>/dev/null || true' EXIT

excluded() {   # whether the path $1 (relative to the source root) is in EXCLUDE
    local e
    for e in "${EXCLUDE[@]}"; do
        case "$e" in
            */) [ "${1#"$e"}" != "$1" ] && return 0 ;;
            *) [ "$1" = "$e" ] && return 0 ;;
        esac
    done
    return 1
}

# the source listing (mode type sha<TAB>path, one line a file), split into kept and excluded
git -C "$src" ls-tree -r --full-tree "$sha" > "$tmp/src.ls"
: > "$tmp/want.ls"; : > "$tmp/excluded.ls"
while IFS=$'\t' read -r meta path; do
    case "$meta" in *" commit "*) die "$sha has a submodule at $path" ;; esac
    case "$path" in \"*) die "$sha has a path git quotes ($path); not supported" ;; esac
    if excluded "$path"; then printf '%s\t%s\n' "$meta" "$path" >> "$tmp/excluded.ls"
    else printf '%s\t%s\n' "$meta" "$path" >> "$tmp/want.ls"; fi
done < "$tmp/src.ls"
git -C "$ROOT" ls-tree -r --full-tree "HEAD:$DIR" > "$tmp/head.ls"

if [ "$dry" = 1 ]; then
    echo "app_vendor: TandemApp $sha (tree $src_tree) into $DIR/ (dry run, nothing written)"
    awk -F'\t' 'NR == FNR { split($1, m, " "); h[$2] = m[1] " " m[3]; next }
                { split($1, m, " "); w = m[1] " " m[3]; seen[$2] = 1
                  if (!($2 in h)) print "  A " $2; else if (h[$2] != w) print "  M " $2 }
                END { for (p in h) if (!(p in seen)) print "  D " p }' \
        "$tmp/head.ls" "$tmp/want.ls" | sort -k2
    while IFS=$'\t' read -r _ path; do echo "  excluded $path"; done < "$tmp/excluded.ls"
    echo "app_vendor: $(wc -l < "$tmp/want.ls" | tr -d ' ') files kept, $(wc -l < "$tmp/excluded.ls" | tr -d ' ') excluded"
    exit 0
fi

# the blobs into this repository's object store, each checked to hash to the source's id
while IFS=$'\t' read -r meta path; do
    id="${meta##* }"
    if ! git -C "$ROOT" cat-file -e "$id" 2>/dev/null; then
        got="$(git -C "$src" cat-file blob "$id" | git -C "$ROOT" hash-object -w --stdin)"
        [ "$got" = "$id" ] || die "the blob of $path hashed to $got, not $id"
    fi
done < "$tmp/want.ls"
# the tree of app/ as a tree object, and its listing checked line for line against the source's
GIT_INDEX_FILE="$tmp/idx" git -C "$ROOT" update-index --add --index-info < "$tmp/want.ls"
tree="$(GIT_INDEX_FILE="$tmp/idx" git -C "$ROOT" write-tree)"
git -C "$ROOT" ls-tree -r --full-tree "$tree" > "$tmp/got.ls"
cmp -s "$tmp/got.ls" "$tmp/want.ls" || die "the tree $tree does not list as $sha less EXCLUDE"
if [ ! -s "$tmp/excluded.ls" ] && [ "$tree" != "$src_tree" ]; then
    die "nothing excluded, yet tree $tree is not $src_tree"
fi

# app/ replaced: removed files through git rm, the rest from the tree into the index and the disk
awk -F'\t' 'NR == FNR { w[$2] = 1; next } !($2 in w) { print $2 }' "$tmp/want.ls" "$tmp/head.ls" \
    | while IFS= read -r p; do git -C "$ROOT" rm -q -- "$DIR/$p"; done
awk -F'\t' -v d="$DIR/" '{ print $1 "\t" d $2 }' "$tmp/want.ls" \
    | git -C "$ROOT" update-index --add --index-info
git -C "$ROOT" checkout -- "$DIR"
{
    echo "# TandemApp vendored into $DIR/ by tools/app_vendor.sh (ENG-413); never edited by hand"
    echo "dir $DIR"
    echo "commit $sha"
    echo "commit_tree $src_tree"
    echo "tree $tree"
    echo "exclude ${EXCLUDE[*]}"
} > "$ROOT/$PIN"
git -C "$ROOT" add -- "$PIN"

# the checks before the commit: only app/ and app.pin staged, app/ is the tree, the disk is the index
bad="$(git -C "$ROOT" diff --cached --name-only | grep -v -e "^$DIR/" -e "^$PIN\$" || true)"
[ -z "$bad" ] || die "staged outside $DIR/ and $PIN: $bad"
[ "$(git -C "$ROOT" write-tree --prefix="$DIR/")" = "$tree" ] || die "the index's $DIR/ is not $tree"
git -C "$ROOT" diff --quiet || die "the disk differs from the index after the checkout"
if git -C "$ROOT" diff --cached --quiet; then
    echo "app_vendor: $DIR/ and $PIN are TandemApp ${sha:0:7} already; nothing to commit"
    exit 0
fi
git -C "$ROOT" commit -q -m "ENG-413: vendor TandemApp ${sha:0:7}"
echo "app_vendor: $(git -C "$ROOT" rev-parse --short HEAD) vendors TandemApp $sha (tree $tree," \
     "$(wc -l < "$tmp/excluded.ls" | tr -d ' ') paths excluded)"
