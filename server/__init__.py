"""The engine's HTTP server (server/app.py) and the modules it runs.

SRV-747: until ENG-413 (the headless engine) the engine runs TandemApp's conductor, ledger and
dashboard API in its own process and imports them from the app's tree. This puts app/ on the path
once, for every entry point (server/app.py, `python -m server.setup.*`, the tools and the tests);
app/contracts/engine-api-v1/README.md lists each such import. TandemApp never imports the engine.
"""

import os
import sys

_APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if os.path.isdir(_APP) and _APP not in sys.path:
    sys.path.append(_APP)
