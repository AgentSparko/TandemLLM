"""The dashboard schemas an engine test validates against (ENG-413 WP9).

The engine's own answers (summary, usage, requests, system, logs, live, parallel) are checked
against the engine's contract, contracts/engine-api-v1/dashboard/. The page's own schemas (session,
error, gap, tandem-eval) are TandemApp's contracts/dashboard-v1, read through tools/contract_check's
default root while those routes are still served by the engine.

Standard library only.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools import contract_check  # noqa: E402

ENGINE = os.path.join(ROOT, "contracts", "engine-api-v1", "dashboard")


def root_of(name: str) -> str:
    """The engine's folder when it holds `name`'s schema, else TandemApp's (contract_check.ROOT)."""
    if os.path.isfile(os.path.join(ENGINE, f"{name}.schema.json")):
        return ENGINE
    return contract_check.ROOT


def check(value, name: str) -> list[str]:
    return contract_check.check(value, name, root_of(name))
