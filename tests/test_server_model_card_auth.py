"""The engine's single model card answers as its list route does (ENG-413 WP14).

server/app.py once gated /v1/models on the users' keys of `--backends` while /v1/models/{id}
answered anyone, the 404-vs-200 difference naming the models. `--backends` left the engine: both
answer with no key, through a proxy too, and a name the engine does not serve is 404. The users'
keys are TandemApp's controller's (its tests/test_proxy_model_card_auth.py).

Run: python tests/test_server_model_card_auth.py   (CPU torch, as tests/test_fake_engine.py)
"""

from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_fake_engine import _port, _start  # noqa: E402
from test_proxy_engine import _req, _wait  # noqa: E402

MODEL = "qwen38-spark-engine"
FAR = {"X-Forwarded-For": "192.0.2.9", "Authorization": "Bearer nope"}


def test_without_backends_unchanged():
    d = tempfile.mkdtemp(prefix="srvcard-")
    port = _port()
    proc = _start(port, os.path.join(d, "ledger.sqlite3"), home=d, env_extra={"QSE_TEST": "1"})
    try:
        _wait(port, proc)
        time.sleep(0.2)
        assert _req(port, f"/v1/models/{MODEL}", headers=FAR)[0] == 200
        assert _req(port, "/v1/models/nope", headers=FAR)[0] == 404
    finally:
        proc.terminate()
        proc.wait(30)


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
