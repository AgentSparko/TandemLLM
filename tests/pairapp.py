"""Where a pair test finds TandemApp (ENG-413 WP14).

A pair test runs the engine and TandemApp's extension, packages or test helpers together; the test
ledger lists them (tests/test-ledger.json `pair_suite`), and they are not part of the engine's own
suite (tools/engine_tests.py). Each imports this module first. TandemApp comes from the checkout
TANDEMAPP_SRC names (its tandemapp/ package at the top), at the commit whose PIN names this engine's
contract (tools/pair_suite.sh checks that before it runs the list):

    APP   that checkout's folder; it is put at the end of sys.path and of PYTHONPATH, so the
          engine's own modules always come first (both trees have a tools/) and a child process
          the test starts imports TandemApp's packages too

Without it the file prints `skipped: <why>` and exits 0, which tests/script_suite.py lists as
skipped, never as passed. Standard library only.
"""

from __future__ import annotations

import os
import sys


def _tree(d: str) -> str | None:
    if d and os.path.isfile(os.path.join(d, "tandemapp", "__init__.py")):
        return os.path.abspath(d)
    return None


APP = _tree(os.environ.get("TANDEMAPP_SRC", ""))

if APP is None:
    print("skipped: a pair test needs TandemApp beside the engine: set TANDEMAPP_SRC to a TandemApp "
          "checkout (tools/pair_suite.sh)", flush=True)
    raise SystemExit(0)

if APP not in sys.path:
    sys.path.append(APP)
_pp = os.environ.get("PYTHONPATH", "")
if APP not in _pp.split(os.pathsep):
    os.environ["PYTHONPATH"] = os.pathsep.join(p for p in (_pp, APP) if p)
