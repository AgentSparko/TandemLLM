"""The dashboard's files at `/dashboard/`, served by the engine itself.

`dashboard/dist/` holds the built page -- one `index.html`, hashed JS and CSS, and `bundle.json`,
the bundle's manifest. It is a build output, not a file in git (OPS-246): `tools/dashboard_bundle.py`
builds it, or fetches a released one against its pinned sha256, on every install path. The shell
holds no data: every number in it comes from `/v1/dashboard/*`, which needs the admin token or the
session, so the files themselves are public.

    GET /dashboard            301 to /dashboard/
    GET /dashboard/           index.html, Cache-Control: no-cache
    GET /dashboard/assets/*   the file; immutable for a hashed name
    GET /dashboard/<route>    index.html again (the app routes on the client)

Fixed content types, `X-Content-Type-Options: nosniff`, the CSP of the dashboard design
(`default-src 'self'; connect-src 'self'; img-src 'self' data:`; with QSE_STATUS_PORT the status
responder's port on any host joins connect-src, OPS-175), and no path outside the
directory: `..`, an absolute path or a symlink out of it is a 404. With no built page (no
`index.html`), a one-paragraph placeholder says how to build or fetch it. Every answer under
/dashboard/ names the bundle it comes from in `X-Tandem-Bundle` (the first 16 hex digits of its
content hash, tandemapp/bundle.py; `none` for the placeholder); `bundle_info()` has the rest.
"""

from __future__ import annotations

import os
import re

TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".json": "application/json", ".map": "application/json", ".svg": "image/svg+xml",
         ".png": "image/png", ".jpg": "image/jpeg", ".ico": "image/x-icon",
         ".webp": "image/webp", ".woff2": "font/woff2", ".woff": "font/woff",
         ".txt": "text/plain; charset=utf-8", ".webmanifest": "application/manifest+json"}
CSP = "default-src 'self'; connect-src 'self'; img-src 'self' data:"
# `index.html` may carry small inline scripts (the dashboard sets its theme before the first paint);
# each is allowed by its own sha256, never by 'unsafe-inline'. Styles: Lit writes `style=`
# attributes from templates, which a style-src without 'unsafe-inline' would silently drop.
STYLE_SRC = "style-src 'self' 'unsafe-inline'"
_INLINE = re.compile(rb"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S | re.I)
# a Vite asset name: `index-3f9a1c0e.js`, `app-Bx7_kQ2d.css`
HASHED = re.compile(r"-[A-Za-z0-9_-]{8,}\.[a-z0-9]+$")

PLACEHOLDER = b"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Engine dashboard</title>
</head><body style="font-family: system-ui, sans-serif; background: #0b0f17; color: #cfd6e4;
padding: 2rem; line-height: 1.5"><h1 style="font-size: 1.2rem">The dashboard is not built on this
server</h1><p>The page is a build output, not a file in git. In TandemApp's folder (<code>app/</code>
of the repository), build it with Node 20.19 or newer: <code>python3 tools/dashboard_bundle.py
build</code>, or fetch a released bundle against its published sha256: <code>python3
tools/dashboard_bundle.py fetch --url &lt;bundle&gt; --sha256 &lt;sha256&gt;</code>. Then reload
this page; no restart is needed. The API it reads is already here:
<code>/v1/dashboard/summary</code>, with the admin token.</p></body></html>
"""


def default_dir() -> str:
    """`dashboard/dist` in a checkout; `proxy/dashboard` beside this package in an installed
    wheel (the tandemapp wheel puts the built page there). A checkout with no built page and an
    installed one: the installed one."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    repo = os.path.join(root, "dashboard", "dist")
    installed = os.path.join(root, "proxy", "dashboard")
    if not os.path.isfile(os.path.join(repo, "index.html")) and \
            os.path.isfile(os.path.join(installed, "index.html")):
        return installed
    return repo


def resolve(root: str, rel: str) -> tuple[str | None, bool]:
    """`(file, fallback)`: the file under `root` for the URL path below /dashboard/, or index.html
    for a client-side route (`fallback`), or `(None, False)` for anything that leaves the root or
    does not exist."""
    from urllib.parse import unquote
    rel = unquote(rel.split("?", 1)[0].split("#", 1)[0])
    if "\x00" in rel or "\\" in rel:
        return None, False
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return None, False
    real_root = os.path.realpath(root)
    path = os.path.realpath(os.path.join(real_root, *parts)) if parts else real_root
    if path != real_root and not path.startswith(real_root + os.sep):
        return None, False
    if os.path.isdir(path):
        path = os.path.join(path, "index.html")
    if os.path.isfile(path):
        return path, False
    last = parts[-1] if parts else ""
    if "." not in last:                                  # a client route, not a missing asset
        index = os.path.join(real_root, "index.html")
        if os.path.isfile(index):
            return index, True
    return None, False


def serve(handler, raw_path: str, root: str | None = None) -> None:
    """Answer one GET under /dashboard on a `BaseHTTPRequestHandler`."""
    root = root or default_dir()
    path = raw_path.split("?", 1)[0]
    if path == "/dashboard":
        return _send(handler, 301, b"", "text/plain", extra=(("Location", "/dashboard/"),))
    if not os.path.isfile(os.path.join(root, "index.html")):
        return _send(handler, 200, PLACEHOLDER, TYPES[".html"], cache="no-cache",
                     csp=csp_for(PLACEHOLDER), extra=(("X-Tandem-Bundle", "none"),))
    tag = (("X-Tandem-Bundle", bundle_tag(root)),)
    file, _ = resolve(root, path[len("/dashboard/"):])
    if file is None:
        return _send(handler, 404, b"not found\n", TYPES[".txt"], cache="no-cache", extra=tag)
    ext = os.path.splitext(file)[1].lower()
    ctype = TYPES.get(ext, "application/octet-stream")
    if ext == ".html":
        cache = "no-cache"
    elif HASHED.search(os.path.basename(file)):
        cache = "public, max-age=31536000, immutable"
    else:
        cache = "no-cache"
    with open(file, "rb") as f:
        raw = f.read()
    return _send(handler, 200, raw, ctype, cache=cache,
                 csp=csp_for(raw) if ext == ".html" else CSP, extra=tag)


def bundle_info(root: str | None = None) -> dict:
    """Which bundle this server serves: {"present", "sha256", "verified", "source", "commit"}
    (tandemapp/bundle.py), computed once per change of the folder."""
    from tandemapp import bundle
    return bundle.info(root or default_dir())


def bundle_tag(root: str | None = None) -> str:
    """The `X-Tandem-Bundle` value: 16 hex digits of the content hash, `modified` appended when the
    files differ from the manifest, `none` with no page."""
    i = bundle_info(root)
    if not i["present"]:
        return "none"
    return i["sha256"][:16] + ("" if i["verified"] else " modified")


def csp_for(html: bytes) -> str:
    """The CSP for one HTML page: the design's policy, its inline scripts by hash, styles inline."""
    import base64
    import hashlib
    hashes = [f"'sha256-{base64.b64encode(hashlib.sha256(m).digest()).decode()}'"
              for m in _INLINE.findall(html)]
    return f"{policy()}; script-src 'self'{''.join(' ' + h for h in hashes)}; {STYLE_SRC}"


def policy() -> str:
    """The design's policy; with a status responder (QSE_STATUS_PORT, OPS-175) the page may also ask
    it, on any host at that port, for the restart while this server is away (VIS-209)."""
    port = os.environ.get("QSE_STATUS_PORT") or ""
    if not port.isdigit():
        return CSP
    return CSP.replace("connect-src 'self'", f"connect-src 'self' http://*:{port}")


def _send(handler, code: int, raw: bytes, ctype: str, cache: str | None = None,
          extra: tuple = (), csp: str = CSP) -> None:
    try:
        handler.send_response(code)
        handler.send_header("Content-Type", ctype)
        handler.send_header("Content-Length", str(len(raw)))
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.send_header("Content-Security-Policy", csp)
        if cache:
            handler.send_header("Cache-Control", cache)
        for k, v in extra:
            handler.send_header(k, v)
        handler.end_headers()
        handler.wfile.write(raw)
    except (BrokenPipeError, ConnectionResetError):
        handler.close_connection = True
