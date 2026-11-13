"""The engine's HTTP server (server/app.py) and the modules it runs.

SRV-747: until ENG-413 (the headless engine) the engine runs TandemApp's conductor, ledger and
dashboard API in its own process and imports them from the app's tree. A finder serves only the
packages tandemapp, proxy and chatd from app/, once, for every entry point (server/app.py,
`python -m server.setup.*`, the tools and the tests); nothing else under app/ becomes importable
this way (app/tools and app/tests stay out of the engine's `tools` and `tests`).
contracts/engine-api-v1/README.md lists each such import. ENG-413 removes the finder.
TandemApp never imports the engine (app/tests/test_import_boundary.py).
"""

import importlib.machinery
import os
import sys

_APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
#: TandemApp's packages the engine imports until ENG-413
APP_PACKAGES = ("tandemapp", "proxy", "chatd")


class AppFinder:
    """A meta-path finder for the top-level packages in APP_PACKAGES, found in app/ only; their
    submodules come from each package's own path."""

    tandem_app_finder = True

    def __init__(self, app: str):
        self.app = app

    def find_spec(self, name, path=None, target=None):
        if path is None and name in APP_PACKAGES:
            return importlib.machinery.PathFinder.find_spec(name, [self.app])
        return None


if os.path.isdir(_APP) and not any(getattr(f, "tandem_app_finder", False) for f in sys.meta_path):
    sys.meta_path.insert(0, AppFinder(_APP))
