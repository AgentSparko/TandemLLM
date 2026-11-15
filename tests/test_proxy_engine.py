"""The engine without the proxy (ENG-413 WP14: `--backends` left the engine), with the fake engine.

What must hold: without `--backends` nothing of a proxy runs (TANDEM_* is ignored, /v1/config and
/v1/responses answer 404, /v1/models lists the engine's model only); `--backends` with any value but
off stops the start with one line naming tandem-app, before anything loads. TandemApp's controller
serves other backends; its own tests hold the proxy.

Run: python tests/test_proxy_engine.py   (CPU torch, as tests/test_fake_engine.py)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_fake_engine import ADMIN, _port, _start  # noqa: E402


def _req(port, path, body=None, method=None, headers=None):
    data = None if body is None else json.dumps(body).encode()
    h = {"Content-Type": "application/json", "Authorization": f"Bearer {ADMIN}"}
    h.update(headers or {})
    r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, headers=h,
                               method=method or ("POST" if body is not None else "GET"))
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def _wait(port, proc, s=60):
    t0 = time.time()
    while time.time() - t0 < s:
        try:
            if _req(port, "/health")[0] == 200:
                return
        except OSError:
            time.sleep(0.3)
        if proc.poll() is not None:
            raise AssertionError(proc.stdout.read())
    raise AssertionError("the server did not start")


def test_without_backends_the_proxy_is_not_loaded():
    d = tempfile.mkdtemp(prefix="pxeng0-")
    port = _port()
    proc = _start(port, os.path.join(d, "ledger.sqlite3"), home=d,
                  env_extra={"QSE_TEST": "1", "TANDEM_BACKENDS": "f1"})
    try:
        _wait(port, proc)
        st, _, body = _req(port, "/v1/config")
        assert st == 404 and json.loads(body)["error"]["type"] == "not_on_engine", body
        ids = [m["id"] for m in json.loads(_req(port, "/v1/models")[2])["data"]]
        assert ids == ["qwen38-spark-engine"], ids
        st, _, _ = _req(port, "/v1/responses", {"model": "x", "input": "y"})
        assert st == 404
    finally:
        proc.terminate()
        out = proc.communicate(timeout=30)[0]
    assert "[proxy]" not in out, "TANDEM_* is ignored without --backends"


def test_backends_stops_the_start():
    d = tempfile.mkdtemp(prefix="pxeng1-")
    port = _port()
    proc = _start(port, os.path.join(d, "ledger.sqlite3"), home=d,
                  extra=("--backends", os.path.join(d, "config.sqlite3")), env_extra={"QSE_TEST": "1"})
    out = proc.communicate(timeout=120)[0]
    assert proc.returncode != 0, out
    assert "--backends is not served by this engine: tandem-app serves other backends" in out, out
    assert not os.path.exists(os.path.join(d, "config.sqlite3")), "no config database opened"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
