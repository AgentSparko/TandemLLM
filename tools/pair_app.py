"""Where a pair tool finds TandemApp (ENG-413 WP10, WP14).

A pair tool measures or replays the engine and TandemApp's conductor together (tests/test_api_import_guard.py
PAIR_TOOLS lists them and says why each needs both). It calls `use()` before it imports TandemApp;
no other engine tool imports TandemApp. `use()` puts TandemApp's tree on sys.path, in this order:

  1  TANDEMAPP_SRC, a TandemApp checkout (its tandemapp/ package at the top)
  2  nothing, when a `tandemapp` package is installed (the TandemApp wheel)

and returns the folder it put there ("" for an installed package). Without any of them it raises
SystemExit with that list, before the tool does anything.
"""

from __future__ import annotations

import importlib.util
import os
import sys


def _is_tree(d: str) -> bool:
    return bool(d) and os.path.isfile(os.path.join(d, "tandemapp", "__init__.py"))


def source() -> str | None:
    """TandemApp's folder as `use()` would take it, "" for an installed package, None for none."""
    d = os.environ.get("TANDEMAPP_SRC", "")
    if _is_tree(d):
        return os.path.abspath(d)
    return "" if importlib.util.find_spec("tandemapp") is not None else None


def use() -> str:
    """TandemApp's tree on sys.path (after the engine's root); its folder, or "" when installed."""
    src = source()
    if src is None:
        raise SystemExit("this pair tool needs TandemApp: set TANDEMAPP_SRC to a TandemApp checkout, "
                         "or install the TandemApp wheel")
    if src and src not in sys.path:
        sys.path.insert(1, src)
    return src
