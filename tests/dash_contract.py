"""The dashboard schemas an engine test validates against (ENG-413 WP9).

The engine's own answers (summary, usage, requests, system, logs, live, parallel) are checked
against the engine's contract, contracts/engine-api-v1/dashboard/ (tools/contract_check's default
since ENG-413 WP10). The page's own schemas (session, error, gap, tandem-eval) are TandemApp's
contracts/dashboard-v1 (PAGE: TANDEMAPP_SRC's, else the vendored app/'s until WP14), while those
routes are still served by the engine.

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


def _page() -> str:
    """TandemApp's dashboard-v1, the page's own schemas: TANDEMAPP_SRC's, else the vendored app/'s."""
    src = os.environ.get("TANDEMAPP_SRC")
    if src and os.path.isdir(os.path.join(src, "contracts", "dashboard-v1")):
        return os.path.join(src, "contracts", "dashboard-v1")
    return os.path.join(ROOT, "app", "contracts", "dashboard-v1")


PAGE = _page()


def root_of(name: str) -> str:
    """The engine's folder when it holds `name`'s schema, else TandemApp's (PAGE)."""
    if os.path.isfile(os.path.join(ENGINE, f"{name}.schema.json")):
        return ENGINE
    return PAGE


def check(value, name: str) -> list[str]:
    return contract_check.check(value, name, root_of(name))
