"""The dashboard schemas an engine test validates against (ENG-413 WP9, WP14).

The engine's answers under /v1/dashboard (summary, usage, requests, system, logs, live, parallel,
the session, the error body and the log's gap marker) are checked against the engine's contract,
contracts/engine-api-v1/dashboard/ (tools/contract_check's default). A name the engine's folder
does not hold is TandemApp's page's own (tandem-eval): only a pair test asks for it, and it is read
from the TandemApp checkout TANDEMAPP_SRC names.

Standard library only.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools import contract_check  # noqa: E402

ENGINE = contract_check.ROOT


def _page() -> str | None:
    """TandemApp's dashboard-v1 in the checkout TANDEMAPP_SRC names, or None."""
    src = os.environ.get("TANDEMAPP_SRC")
    if src and os.path.isdir(os.path.join(src, "contracts", "dashboard-v1")):
        return os.path.join(src, "contracts", "dashboard-v1")
    return None


PAGE = _page()


def root_of(name: str) -> str:
    """The engine's folder when it holds `name`'s schema, else TandemApp's (PAGE)."""
    if os.path.isfile(os.path.join(ENGINE, f"{name}.schema.json")):
        return ENGINE
    if PAGE is None:
        raise RuntimeError(f"{name}.schema.json is TandemApp's page's: set TANDEMAPP_SRC")
    return PAGE


def check(value, name: str) -> list[str]:
    return contract_check.check(value, name, root_of(name))
