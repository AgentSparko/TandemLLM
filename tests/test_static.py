"""The engine has no page (ENG-413 WP14, engine API 1.8), on a CPU.

Every path under /dashboard answers 404 no_ui, naming TandemApp, through a proxy or not, with a
token or without, with an extension or headless; the data reads keep their rule. The page's own
files and their rules (the redirect, the placeholder, the cache headers, the CSP, no way out of the
directory) are TandemApp's tandemapp/static.py, tested in TandemApp's tests/test_static.py.

Run: python tests/test_static.py
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_app_loop import Req, serve  # noqa: E402  (first: the CPU environment)

from server import app  # noqa: E402
from server.api import auth  # noqa: E402

PROXY = {"X-Forwarded-For": "203.0.113.44", "X-Real-IP": "203.0.113.44"}


def get(path, headers=None, ext=None):
    serve()
    app.STATE.update(auth=auth.Auth("adm-" + "1" * 40))
    app.EXT = ext if ext is not None else app.ext_mod.NULL
    req = Req(path, {})
    req.command, req.path = "GET", path
    req.headers.update(dict(PROXY, **(headers or {})))
    with contextlib.redirect_stdout(io.StringIO()):
        req.do_GET()
    head, _, body = req.wfile.getvalue().partition(b"\r\n\r\n")
    head = head.decode()
    return int(head.split(" ", 2)[1]), head, body


class _PageExt:
    """An extension that registers a /dashboard route: the engine still answers it 404 no_ui."""

    def route(self, method, path):
        return (lambda *a, **k: None) if path.startswith("/dashboard") else None

    def __getattr__(self, name):
        return getattr(app.ext_mod.NULL, name)


def test_a_headless_engine_has_no_page():
    """Every page path is 404 no_ui, naming TandemApp, with or without a token, headless or with
    an extension that names the path; the data reads keep their rule."""
    paths = ("/dashboard", "/dashboard/", "/dashboard/assets/index-3f9a1c0e.js", "/dashboard/usage/2026")
    for ext in (None, _PageExt()):
        for path in paths:
            for headers in ({}, {"Authorization": "Bearer adm-" + "1" * 40}):
                code, _, body = get(path, headers=headers, ext=ext)
                err = json.loads(body)["error"]
                assert code == 404 and err["type"] == "no_ui" and "TandemApp" in err["message"], \
                    (path, code, body)
    code, _, _ = get("/v1/dashboard/summary")
    assert code == 401
    return f"{len(paths)} page paths 404 no_ui, headless and with an extension; the reads keep their rule"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
