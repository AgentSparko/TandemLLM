#!/usr/bin/env bash
# A wheel into an install's venv, or a package out of it, with the engine's pins held (ENG-454).
#
#   bash ops/venv-wheel.sh install DIR WHEEL PACKAGE   the wheel, its dependencies resolved with
#                                                      DIR/run/requirements.lock as constraints, so
#                                                      no pinned package moves; PACKAGE is reinstalled
#   bash ops/venv-wheel.sh remove DIR PACKAGE          the package alone; its dependencies stay
#
# The one way a wheel goes into the engine's venv: install.sh --with-chat --app-wheel (TandemApp's
# wheel) and `tandem remote ext install` (an extension's) both run it. The tool is install.sh's:
# VENV_WHEEL_TOOL=pip or uv when the caller says (install.sh does, with UV), else uv when it is on
# PATH or in DIR/uv, else the venv's pip. PYPI_INDEX and TORCH_INDEX are install.sh's; it passes its
# own, and tests/test_remote_ext.py holds these defaults equal to them.
set -euo pipefail

usage() { echo "usage: venv-wheel.sh install DIR WHEEL PACKAGE | remove DIR PACKAGE" >&2; exit 2; }
[ $# -ge 3 ] || usage
ACT="$1"; DIR="$2"
PY="$DIR/venv/bin/python"
LOCKF="$DIR/run/requirements.lock"
PYPI_INDEX="${PYPI_INDEX:-https://pypi.org/simple}"
TORCH_INDEX="${TORCH_INDEX:-https://download.pytorch.org/whl/cu130}"
[ -x "$PY" ] || { echo "[venv-wheel] no venv at $DIR/venv" >&2; exit 1; }

TOOL="${VENV_WHEEL_TOOL:-}"
UV="${UV:-}"
if [ -z "$TOOL" ]; then
    for c in "$(command -v uv 2>/dev/null || true)" "$DIR/uv/uv" "$DIR/uv/bin/uv"; do
        if [ -n "$c" ] && [ -x "$c" ]; then TOOL=uv; UV="$c"; break; fi
    done
    [ -n "$TOOL" ] || TOOL=pip
fi
if [ "$TOOL" = uv ] && [ -z "$UV" ]; then UV="$(command -v uv 2>/dev/null || true)"; fi
[ "$TOOL" = pip ] || [ -n "$UV" ] || { echo "[venv-wheel] VENV_WHEEL_TOOL=uv but no uv" >&2; exit 1; }

case "$ACT" in
    install)
        [ $# -eq 4 ] || usage
        WHEEL="$3"; PKG="$4"
        [ -f "$WHEEL" ] || { echo "[venv-wheel] no file $WHEEL" >&2; exit 1; }
        [ -f "$LOCKF" ] || { echo "[venv-wheel] no $LOCKF: run the installer first" >&2; exit 1; }
        if [ "$TOOL" = pip ]; then
            "$PY" -m pip install --disable-pip-version-check --force-reinstall -c "$LOCKF" \
                "$WHEEL" --index-url "$PYPI_INDEX" --extra-index-url "$TORCH_INDEX"
        else
            "$UV" pip install --python "$PY" --reinstall-package "$PKG" -c "$LOCKF" \
                "$WHEEL" --index-url "$PYPI_INDEX" --extra-index-url "$TORCH_INDEX" --index-strategy unsafe-best-match
        fi ;;
    remove)
        [ $# -eq 3 ] || usage
        PKG="$3"
        if [ "$TOOL" = pip ]; then
            "$PY" -m pip uninstall --disable-pip-version-check -y "$PKG"
        else
            "$UV" pip uninstall --python "$PY" "$PKG"
        fi ;;
    *) usage ;;
esac
