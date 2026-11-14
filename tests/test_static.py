"""The dashboard's static files at /dashboard/ (serving half), on a CPU.

The redirect, the placeholder with no build, content types, the cache rules (index no-cache,
hashed assets immutable), the client-route fallback, the CSP, and no way out of the directory --
`..`, its URL-encoded form, a symlink. The shell is public: it holds no data.

Run: python tests/test_static.py
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(1, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))  # SRV-747: TandemApp's packages, until ENG-413
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_app_loop import Req, serve  # noqa: E402  (first: the CPU environment)

from server import app  # noqa: E402
from tandemapp import static  # noqa: E402
from server.api import auth  # noqa: E402

PROXY = {"X-Forwarded-For": "203.0.113.44", "X-Real-IP": "203.0.113.44"}


def _dist() -> str:
    root = tempfile.mkdtemp(prefix="qse-dist-")
    d = os.path.join(root, "dist")
    os.makedirs(os.path.join(d, "assets"))
    open(os.path.join(d, "index.html"), "w").write(
        "<!doctype html><title>dash</title><script>document.documentElement.dataset.theme="
        "localStorage.theme||'dark'</script><script type=\"module\" src=\"./assets/index-3f9a1c0e.js\">"
        "</script>")
    open(os.path.join(d, "assets", "index-3f9a1c0e.js"), "w").write("console.log(1)")
    open(os.path.join(d, "assets", "index-Bx7_kQ2d.css"), "w").write("body{}")
    open(os.path.join(d, "favicon.svg"), "w").write("<svg/>")
    open(os.path.join(root, "secret.txt"), "w").write("outside")
    os.symlink(os.path.join(root, "secret.txt"), os.path.join(d, "link.txt"))
    return d


def get(path, dist, headers=None, headless=False):
    serve()
    app.STATE.update(dashboard_dir=dist, auth=auth.Auth("adm-" + "1" * 40))
    # ENG-413 WP3a: the page is the extension's route (the interim adapter in chat mode); a headless
    # engine answers 404 no_ui
    app.EXT = app.ext_mod.NULL if headless else app.ext_mod.attach(app.STATE)
    req = Req(path, {})
    req.command, req.path = "GET", path
    req.headers.update(dict(PROXY, **(headers or {})))
    with contextlib.redirect_stdout(io.StringIO()):
        req.do_GET()
    head, _, body = req.wfile.getvalue().partition(b"\r\n\r\n")
    head = head.decode()
    return int(head.split(" ", 2)[1]), head, body


def test_the_redirect_and_the_placeholder():
    code, head, _ = get("/dashboard", "/nonexistent/dist")
    assert code == 301 and "Location: /dashboard/" in head, head
    code, head, body = get("/dashboard/", "/nonexistent/dist")
    assert code == 200 and "text/html" in head and b"not built" in body


def test_files_types_and_caching():
    d = _dist()
    code, head, body = get("/dashboard/", d)
    assert code == 200 and body.startswith(b"<!doctype html>") and "Cache-Control: no-cache" in head
    import base64
    import hashlib
    inline = b"document.documentElement.dataset.theme=localStorage.theme||'dark'"
    h = base64.b64encode(hashlib.sha256(inline).digest()).decode()
    csp = [l for l in head.split("\r\n") if l.startswith("Content-Security-Policy: ")][0]
    assert csp == ("Content-Security-Policy: default-src 'self'; connect-src 'self'; "
                   f"img-src 'self' data:; script-src 'self' 'sha256-{h}'; "
                   "style-src 'self' 'unsafe-inline'"), csp
    assert "unsafe-inline'" not in csp.split("script-src")[1].split(";")[0], "never for scripts"
    assert "X-Content-Type-Options: nosniff" in head
    code, head, body = get("/dashboard/assets/index-3f9a1c0e.js", d)
    assert code == 200 and "text/javascript" in head and "immutable" in head and body == \
        b"console.log(1)"
    code, head, _ = get("/dashboard/assets/index-Bx7_kQ2d.css?v=1", d)
    assert code == 200 and "text/css" in head and "immutable" in head
    code, head, _ = get("/dashboard/favicon.svg", d)
    assert code == 200 and "image/svg+xml" in head and "no-cache" in head
    code, head, body = get("/dashboard/usage/2026", d)
    assert code == 200 and b"<title>dash</title>" in body, "a client route gets the shell"
    code, _, _ = get("/dashboard/assets/missing-00000000.js", d)
    assert code == 404, "a missing asset is not the shell"


def test_nothing_outside_the_directory():
    d = _dist()
    for path in ("/dashboard/../secret.txt", "/dashboard/%2e%2e/secret.txt",
                 "/dashboard/assets/../../secret.txt", "/dashboard/link.txt",
                 "/dashboard/%2e%2e%2fsecret.txt", "/dashboard/..%5csecret.txt",
                 "/dashboard/a%00b.js"):
        code, _, body = get(path, d)
        assert code == 404 and b"outside" not in body, (path, code)


def test_the_shell_is_public_and_holds_no_data():
    d = _dist()
    code, _, _ = get("/dashboard/", d)                    # through the proxy, no token
    assert code == 200
    code, _, _ = get("/v1/dashboard/summary", d)          # the data is not
    assert code == 401


def test_a_headless_engine_has_no_page():
    """ENG-413 WP3a (engine API 1.4): without an extension that serves it, every page path is
    404 no_ui, naming TandemApp, through the proxy or not; the data reads stay the engine's."""
    import json
    d = _dist()
    for path in ("/dashboard", "/dashboard/", "/dashboard/assets/index-3f9a1c0e.js", "/dashboard/usage/2026"):
        for headers in ({}, {"Authorization": "Bearer adm-" + "1" * 40}):
            code, head, body = get(path, d, headers=headers, headless=True)
            err = json.loads(body)["error"]
            assert code == 404 and err["type"] == "no_ui" and "TandemApp" in err["message"], (path, code, body)
    code, _, _ = get("/v1/dashboard/summary", d, headless=True)
    assert code == 401
    return "4 page paths 404 no_ui; the reads keep their rule"


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  {name:64s} ok")
            passed += 1
    print(f"{passed} passed")
