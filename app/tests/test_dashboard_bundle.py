"""OPS-246/247: the dashboard's built page is a build output, not a file in git; CI builds it, and
every install path builds it, fetches a checked release bundle, or stops with what to do.

Fixtures only, no network and no Node: a fake `npm` (and `node`) on the PATH writes a small page as
`npm run build` would, an archive is served from a local file or a local HTTP server, and the build
backend runs against a stand-in for setuptools. One test builds with the real npm, and only when
TANDEM_BUNDLE_REAL_BUILD=1 says this machine may (Node, the network); it skips otherwise. It runs
as `TANDEM_BUNDLE_REAL_BUILD=1 python tests/test_dashboard_bundle.py`: the suite runner passes no
such variable on.

    python tests/test_dashboard_bundle.py
"""

from __future__ import annotations

import gzip
import hashlib
import http.server
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import types
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(ROOT)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tandemapp import bundle, static  # noqa: E402

TOOL = os.path.join(ROOT, "tools", "dashboard_bundle.py")
WF = os.path.join(REPO, ".github", "workflows")

# a stand-in for npm: `ci` makes node_modules, `run build [-- --outDir X --emptyOutDir]` writes a
# page whose asset names are content hashes, as Vite does; FAKE_NPM_SALT changes the content,
# FAKE_NPM_FAIL=ci|build fails that step, FAKE_NPM_LOG gets one line per call
FAKE_NPM = r'''
import hashlib, os, shutil, sys
args = sys.argv[1:]
log = os.environ.get("FAKE_NPM_LOG")
if log:
    with open(log, "a") as f:
        f.write(" ".join(args) + "\n")
step = "ci" if args[:1] == ["ci"] else "build" if args[:2] == ["run", "build"] else "?"
if os.environ.get("FAKE_NPM_FAIL") == step:
    print("npm ERR! the fake failed at", step, file=sys.stderr)
    sys.exit(3)
if step == "ci":
    os.makedirs("node_modules", exist_ok=True)
    open(os.path.join("node_modules", ".ok"), "w").close()
    sys.exit(0)
if step != "build":
    sys.exit(9)
out = args[args.index("--outDir") + 1] if "--outDir" in args else "dist"
if os.path.isdir(out):
    shutil.rmtree(out)
os.makedirs(os.path.join(out, "assets"))
salt = os.environ.get("FAKE_NPM_SALT", "")
js = ("console.log('page', %r);" % salt).encode()
css = b"body{color:#cfd6e4}"
hj, hc = hashlib.sha256(js).hexdigest()[:8], hashlib.sha256(css).hexdigest()[:8]
open(os.path.join(out, "assets", f"app-{hj}.js"), "wb").write(js)
open(os.path.join(out, "assets", f"index-{hc}.css"), "wb").write(css)
open(os.path.join(out, "favicon.svg"), "w").write("<svg/>")
open(os.path.join(out, "sw.js"), "w").write("const BUILD='%s%s';" % (hj, hc))
open(os.path.join(out, "index.html"), "w").write(
    '<!doctype html><title>TandemLLM</title><script type="module" src="./assets/app-%s.js">'
    '</script><link rel="stylesheet" href="./assets/index-%s.css">' % (hj, hc))
'''


def _tmp(prefix: str) -> str:
    return tempfile.mkdtemp(prefix=f"bundle-{prefix}-")


def _fake_bin(d: str) -> str:
    """A bin folder with the fake npm and a node that prints a version."""
    b = os.path.join(d, "bin")
    os.makedirs(b, exist_ok=True)
    for name, body in (("npm", FAKE_NPM), ("node", "import sys; print('v22.0.0-fixture')")):
        p = os.path.join(b, name)
        with open(p, "w") as f:
            f.write(f"#!{sys.executable}\n{body}")
        os.chmod(p, 0o755)
    return b


def _env(binpath: str | None, **kw) -> dict:
    """An environment whose PATH has the fake bin (or no npm at all) and the basic tools."""
    path = os.pathsep.join(x for x in [binpath, "/usr/bin", "/bin"] if x)
    env = {"PATH": path, "HOME": os.environ.get("HOME", "/tmp"), "LANG": "C.UTF-8"}
    env.update({k: v for k, v in kw.items() if v is not None})
    return env


def _app_tree(d: str) -> str:
    """A fresh TandemApp tree as a clone gives it: the bundle code, the tool, the dashboard's
    package files, and no dist/."""
    app = os.path.join(d, "app")
    os.makedirs(os.path.join(app, "tandemapp"))
    os.makedirs(os.path.join(app, "tools"))
    os.makedirs(os.path.join(app, "dashboard"))
    open(os.path.join(app, "tandemapp", "__init__.py"), "w").close()
    shutil.copy(os.path.join(ROOT, "tandemapp", "bundle.py"), os.path.join(app, "tandemapp"))
    shutil.copy(TOOL, os.path.join(app, "tools"))
    with open(os.path.join(app, "dashboard", "package.json"), "w") as f:
        f.write('{"name": "fixture", "private": true}\n')
    with open(os.path.join(app, "dashboard", "package-lock.json"), "w") as f:
        f.write('{"lockfileVersion": 3, "packages": {}}\n')
    return app


def _page(d: str, salt: str = "") -> str:
    """A built page with its manifest, through the fake npm."""
    app = _app_tree(d)
    bundle.build(os.path.join(app, "dashboard"), env=_env(_fake_bin(d), FAKE_NPM_SALT=salt),
                 log=lambda *_: None)
    return os.path.join(app, "dashboard", "dist")


def _tool(app: str, args: list[str], env: dict) -> tuple[int, str]:
    r = subprocess.run([sys.executable, "-I", os.path.join(app, "tools", "dashboard_bundle.py")]
                       + args, capture_output=True, text=True, env=env, timeout=120)
    return r.returncode, r.stdout + r.stderr


def _tar(entries) -> bytes:
    """A .tar.gz of (TarInfo-kwargs, bytes or None) entries, for the unsafe-archive cases."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as t:
        for kw, data in entries:
            ti = tarfile.TarInfo(kw.pop("name"))
            for k, v in kw.items():
                setattr(ti, k, v)
            if data is not None:
                ti.size = len(data)
            t.addfile(ti, io.BytesIO(data) if data is not None else None)
    return gzip.compress(raw.getvalue())


def _sha(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


# ------------------------------------------------------------------ Scenario: dist is not tracked
def test_the_dist_is_not_tracked_and_is_ignored():
    """AC1: `git ls-files dashboard/dist` prints nothing and .gitignore lists the dist."""
    with open(os.path.join(ROOT, ".gitignore")) as f:
        rules = [ln.strip() for ln in f]
    assert "/dashboard/dist/" in rules, "app/.gitignore does not list dashboard/dist"
    r = subprocess.run(["git", "-C", ROOT, "rev-parse", "--is-inside-work-tree"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        # a copy made by `git archive` (a test run's tree) holds only tracked files: a dist here
        # came from git unless the bundle tool built it, which writes bundle.json (a dist from
        # git never had one)
        dist = os.path.join(ROOT, "dashboard", "dist")
        assert not os.path.exists(os.path.join(dist, "index.html")) or \
            bundle.read_manifest(dist) is not None, "the copy carries a dist from git"
        return
    out = subprocess.run(["git", "-C", ROOT, "ls-files", "dashboard/dist"], capture_output=True,
                         text=True, check=True).stdout
    assert out == "", f"tracked under dashboard/dist: {out.splitlines()[:3]}"
    r = subprocess.run(["git", "-C", ROOT, "check-ignore", "--no-index", "-q",
                        "dashboard/dist/index.html"])
    assert r.returncode == 0, "dashboard/dist/index.html is not ignored"


# ------------------------------------------------------------------ the bundle: hash and manifest
def test_the_bundle_hash_and_its_manifest():
    d = _tmp("hash")
    try:
        dist = _page(d)
        doc = bundle.read_manifest(dist)
        assert doc["schema"] == 1 and doc["source"] == "build" and doc["node"] == "v22.0.0-fixture"
        assert doc["lockfile_sha256"] == _sha(os.path.join(dist, "..", "package-lock.json"))
        assert set(doc["file_sha256"]) == {"index.html", "favicon.svg", "sw.js"} | {
            "assets/" + n for n in os.listdir(os.path.join(dist, "assets"))}
        assert "bundle.json" not in doc["file_sha256"], "the manifest is not part of the hash"
        # the hash: one line per file, path NUL sha256, sorted
        h = hashlib.sha256()
        for p in sorted(doc["file_sha256"]):
            h.update(p.encode() + b"\0" + doc["file_sha256"][p].encode() + b"\n")
        assert doc["sha256"] == h.hexdigest() and doc["files"] == 5
        v = bundle.verify(dist)
        assert v == {"ok": True, "sha256": doc["sha256"], "recorded": doc["sha256"], "problems": []}
        assert not bundle.verify(dist, expect="0" * 64)["ok"], "--expect is checked"
        # a symlink in the folder is not part of the bundle
        os.symlink("/etc/hostname", os.path.join(dist, "link.txt"))
        assert bundle.verify(dist)["ok"]
        # a changed file is named
        with open(os.path.join(dist, "favicon.svg"), "a") as f:
            f.write(" ")
        v = bundle.verify(dist)
        assert not v["ok"] and "favicon.svg" in v["problems"][0], v
        os.remove(os.path.join(dist, "index.html"))
        assert bundle.verify(dist)["problems"] == [f"no index.html in {dist}"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------------------------ Scenario: CI builds the UI
def test_the_build_runs_npm_ci_then_the_build_and_records():
    """The build pins the lockfile (`npm ci`, never `npm install`), then `npm run build` (type
    check and Vite), then writes the manifest with the commit it was given."""
    d = _tmp("build")
    try:
        app = _app_tree(d)
        log = os.path.join(d, "npm.log")
        doc = bundle.build(os.path.join(app, "dashboard"), commit="c" * 40,
                           env=_env(_fake_bin(d), FAKE_NPM_LOG=log), log=lambda *_: None)
        with open(log) as f:
            calls = f.read().splitlines()
        assert calls == ["ci --no-audit --no-fund", "run build"], calls
        assert doc["commit"] == "c" * 40
        assert bundle.verify(os.path.join(app, "dashboard", "dist"))["ok"]
        # into another folder: the build's own arguments after `--`
        out = os.path.join(d, "second")
        doc2 = bundle.build(os.path.join(app, "dashboard"), out_dir=out, install=False,
                            env=_env(_fake_bin(d), FAKE_NPM_LOG=log), log=lambda *_: None)
        with open(log) as f:
            assert f.read().splitlines()[2:] == [f"run build -- --outDir {out} --emptyOutDir"]
        assert doc2["sha256"] == doc["sha256"], "the same sources, the same bundle"
        # no lockfile: refused before npm runs
        os.remove(os.path.join(app, "dashboard", "package-lock.json"))
        try:
            bundle.build(os.path.join(app, "dashboard"), env=_env(_fake_bin(d)),
                         log=lambda *_: None)
            raise AssertionError("built without a lockfile")
        except bundle.BundleError as exc:
            assert "package-lock.json" in str(exc)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_a_failed_step_says_which_and_shows_its_tail():
    d = _tmp("fail")
    try:
        app = _app_tree(d)
        for step, word in (("ci", "ci --no-audit"), ("build", "run build")):
            try:
                bundle.build(os.path.join(app, "dashboard"), log=lambda *_: None,
                             env=_env(_fake_bin(d), FAKE_NPM_FAIL=step))
                raise AssertionError(f"{step} failed and the build went on")
            except bundle.NoBundle:
                raise AssertionError("a failed step is not a missing npm")
            except bundle.BundleError as exc:
                assert word in str(exc) and "exit 3" in str(exc) and "the fake failed" in str(exc)
        assert not os.path.exists(os.path.join(app, "dashboard", "dist", "bundle.json"))
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------------------------ AC5: same commit, same hashes
def test_two_builds_of_the_same_sources_give_one_hash():
    """`repro` builds again into a temp folder and compares; different content fails and names
    the files (the real two-build proof runs with the real npm, below and in CI)."""
    d = _tmp("repro")
    try:
        app = _app_tree(d)
        b = _fake_bin(d)
        env = _env(b)
        assert _tool(app, ["build"], env)[0] == 0
        code, out = _tool(app, ["repro"], env)
        assert code == 0 and "PASS two builds, one hash" in out, out
        first, second = re.findall(r"^(?:first|second) +([0-9a-f]{64})$", out, re.M)
        assert first == second
        code, out = _tool(app, ["repro"], _env(b, FAKE_NPM_SALT="other"))
        assert code == 1 and "FAIL two builds gave two hashes" in out, out
        assert re.search(r"differs: assets/app-[0-9a-f]{8}\.js", out), out
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------------------------ release archives
def test_the_archive_is_reproducible_and_round_trips():
    d = _tmp("pack")
    try:
        dist = _page(d)
        a1, a2 = os.path.join(d, "a1.tar.gz"), os.path.join(d, "a2.tar.gz")
        s1 = bundle.pack(dist, a1)
        for _, p in bundle.files(dist):                       # other mtimes, the same archive
            os.utime(p, (1, 1))
        s2 = bundle.pack(dist, a2)
        assert s1 == s2 == _sha(a1) == _sha(a2)
        with open(a1 + ".sha256") as f:
            assert f.read() == f"{s1}  a1.tar.gz\n"
        with tarfile.open(a1) as t:
            names = t.getnames()
            assert names == sorted(names) and "bundle.json" in names and "index.html" in names
            assert all(m.mtime == 0 and m.uid == 0 and m.mode == 0o644 for m in t.getmembers())
        dest = os.path.join(d, "out", "dist")
        doc = bundle.fetch(a1, s1, dest)
        assert doc["source"] == "fetch" and doc["built_as"] == "build" and \
            doc["archive_sha256"] == s1 and doc["sha256"] == bundle.read_manifest(dist)["sha256"]
        assert bundle.verify(dest)["ok"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_a_fetch_needs_the_pinned_sha256_and_leaves_the_old_page_on_a_mismatch():
    d = _tmp("fetch")
    try:
        dist = _page(d)
        arch = os.path.join(d, "b.tar.gz")
        good = bundle.pack(dist, arch)
        dest = os.path.join(d, "site", "dist")
        old = _page(os.path.join(d, "old"), salt="old")
        shutil.copytree(old, dest)
        before = bundle.verify(dest)["sha256"]
        for sha in ("", "abc", None):
            try:
                bundle.fetch(arch, sha, dest)
                raise AssertionError("fetched without a sha256")
            except bundle.NoBundle as exc:
                assert "sha256" in str(exc)
        try:
            bundle.fetch(arch, "f" * 64, dest)
            raise AssertionError("a wrong sha256 was accepted")
        except bundle.BundleError as exc:
            assert f"sha256 {good} is not the pinned {'f' * 64}" in str(exc)
        assert bundle.verify(dest)["sha256"] == before, "the old page stays on a mismatch"
        assert sorted(os.listdir(os.path.join(d, "site"))) == ["dist"], "no temp file left"
        # a file URL and the upper-case hex both work
        doc = bundle.fetch("file://" + arch, good.upper(), dest)
        assert doc["sha256"] == bundle.read_manifest(dist)["sha256"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


class _Files(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def test_a_fetch_over_http_from_a_local_server():
    d = _tmp("http")
    srv = None
    try:
        dist = _page(d)
        os.makedirs(os.path.join(d, "www"))
        sha = bundle.pack(dist, os.path.join(d, "www", "tandemapp-dashboard.tar.gz"))
        handler = type("H", (_Files,), {})
        srv = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), lambda *a: handler(*a, directory=os.path.join(d, "www")))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        dest = os.path.join(d, "dl", "dist")
        doc = bundle.fetch(base + "/tandemapp-dashboard.tar.gz", sha, dest)
        assert doc["fetched_from"].startswith(base) and bundle.verify(dest)["ok"]
        try:
            bundle.fetch(base + "/missing.tar.gz", sha, os.path.join(d, "dl2", "dist"))
            raise AssertionError("a 404 was accepted")
        except bundle.BundleError as exc:
            assert "HTTP 404" in str(exc)
        try:
            bundle.fetch("ftp://example.invalid/x.tar.gz", sha, os.path.join(d, "dl3", "dist"))
            raise AssertionError("an ftp URL was read")
        except bundle.BundleError as exc:
            assert "only http, https, file" in str(exc)
    finally:
        if srv:
            srv.shutdown()
            srv.server_close()
        shutil.rmtree(d, ignore_errors=True)


def test_an_unsafe_or_wrong_archive_is_refused():
    d = _tmp("unsafe")
    try:
        dist = _page(d)
        man = open(os.path.join(dist, "bundle.json"), "rb").read()
        idx = open(os.path.join(dist, "index.html"), "rb").read()
        cases = {
            "dotdot": [({"name": "../evil.js"}, b"x")],
            "absolute": [({"name": "/tmp/evil.js"}, b"x")],
            "symlink": [({"name": "index.html", "type": tarfile.SYMTYPE, "linkname": "/etc/passwd"},
                         None)],
            "hardlink": [({"name": "a.js", "type": tarfile.LNKTYPE, "linkname": "index.html"},
                          None)],
            "device": [({"name": "dev", "type": tarfile.CHRTYPE}, None)],
            "no manifest": [({"name": "index.html"}, idx)],
            "files differ": [({"name": "index.html"}, idx + b" "), ({"name": "bundle.json"}, man)],
            "not a tar": None,
        }
        for name, entries in cases.items():
            p = os.path.join(d, name.replace(" ", "_") + ".tar.gz")
            with open(p, "wb") as f:
                f.write(_tar(entries) if entries is not None else b"not gzip at all")
            dest = os.path.join(d, "x-" + name.replace(" ", "_"), "dist")
            try:
                bundle.fetch(p, _sha(p), dest)
                raise AssertionError(f"{name}: accepted")
            except bundle.BundleError as exc:
                assert not os.path.exists(dest), f"{name}: something was written"
                assert str(exc), name
            parent = os.path.dirname(dest)
            assert not os.path.isdir(parent) or os.listdir(parent) == [], f"{name}: left files"
        assert not os.path.exists(os.path.join(d, "evil.js"))
        # too large: refused while reading, not after
        old = bundle.MAX_BYTES
        bundle.MAX_BYTES = 64
        try:
            arch = os.path.join(d, "big.tar.gz")
            sha = bundle.pack(dist, arch)
            bundle.fetch(arch, sha, os.path.join(d, "big", "dist"))
            raise AssertionError("an oversized archive was accepted")
        except bundle.BundleError as exc:
            assert "larger than" in str(exc) or "more than" in str(exc)
        finally:
            bundle.MAX_BYTES = old
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------------------------ ensure: what every install does
def test_ensure_keeps_builds_fetches_or_says_what_to_do():
    d = _tmp("ensure")
    try:
        quiet = (lambda *_: None)
        # no page, no npm, no URL: the actionable text, never a traceback
        app = _app_tree(os.path.join(d, "a"))
        try:
            bundle.ensure(app, env=_env(None), log=quiet)
            raise AssertionError("no way to get the page, and no error")
        except bundle.NoBundle as exc:
            text = str(exc)
            assert "not built" in text and "dashboard_bundle.py build" in text and \
                "fetch --url" in text and "TANDEM_DASHBOARD_URL" in text, text
        assert not os.path.exists(os.path.join(app, "dashboard", "dist"))
        # npm on the PATH: it builds
        doc = bundle.ensure(app, env=_env(_fake_bin(d)), log=quiet)
        assert doc["source"] == "build"
        # an intact page: kept as it is (npm is not called)
        log = os.path.join(d, "npm.log")
        assert bundle.ensure(app, env=_env(_fake_bin(d), FAKE_NPM_LOG=log), log=quiet)["sha256"] \
            == doc["sha256"] and not os.path.exists(log)
        # a page from a plain `npm run build` (no manifest): recorded as it is
        os.remove(os.path.join(app, "dashboard", "dist", "bundle.json"))
        assert bundle.ensure(app, env=_env(None), log=quiet)["source"] == "record"
        # a changed page with npm: built again
        with open(os.path.join(app, "dashboard", "dist", "favicon.svg"), "a") as f:
            f.write("x")
        assert bundle.ensure(app, env=_env(_fake_bin(d)), log=quiet)["sha256"] == doc["sha256"]
        # no npm, a URL and its sha256 (the environment, as install.sh passes them): fetched
        arch = os.path.join(d, "rel.tar.gz")
        sha = bundle.pack(os.path.join(app, "dashboard", "dist"), arch)
        app2 = _app_tree(os.path.join(d, "b"))
        doc2 = bundle.ensure(app2, env=_env(None, TANDEM_DASHBOARD_URL=arch,
                                            TANDEM_DASHBOARD_SHA256=sha), log=quiet)
        assert doc2["source"] == "fetch" and doc2["sha256"] == doc["sha256"]
        # a URL without its sha256: refused
        app3 = _app_tree(os.path.join(d, "c"))
        try:
            bundle.ensure(app3, env=_env(None, TANDEM_DASHBOARD_URL=arch), log=quiet)
            raise AssertionError("a URL without a sha256 was used")
        except bundle.NoBundle as exc:
            assert "TANDEM_DASHBOARD_SHA256" in str(exc)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_the_tool_exit_codes_and_messages():
    d = _tmp("tool")
    try:
        app = _app_tree(d)
        code, out = _tool(app, ["ensure"], _env(None))
        assert code == 2 and "FAIL The dashboard's page is not built" in out and \
            "Traceback" not in out, out
        code, out = _tool(app, ["build"], _env(None))
        assert code == 2 and "npm is not on the PATH" in out and "Traceback" not in out, out
        code, out = _tool(app, ["verify"], _env(None))
        assert code == 1 and out.startswith("FAIL bundle None"), out
        code, out = _tool(app, ["build", "--prune-modules"], _env(_fake_bin(d)))
        assert code == 0 and re.search(r"^bundle [0-9a-f]{64} \(5 files", out, re.M), out
        assert not os.path.exists(os.path.join(app, "dashboard", "node_modules"))
        dist = os.path.join(app, "dashboard", "dist")
        sha = bundle.read_manifest(dist)["sha256"]
        assert _tool(app, ["verify", "--expect", sha], _env(None))[0] == 0
        assert _tool(app, ["verify", "--expect", "1" * 64], _env(None))[0] == 1
        code, out = _tool(app, ["info"], _env(None))
        assert code == 0 and json.loads(out)["sha256"] == sha
        code, out = _tool(app, ["fetch", "--url", "x.tar.gz", "--sha256", "nope"], _env(None))
        assert code == 2 and "64 hex digits" in out, out
        code, out = _tool(app, ["pack", "--out", os.path.join(d, "p.tar.gz")], _env(None))
        assert code == 0 and "archive sha256" in out and os.path.exists(os.path.join(d, "p.tar.gz"))
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------------------------ Scenario: local deploy works
class _Page(http.server.BaseHTTPRequestHandler):
    root = ""

    def do_GET(self):
        static.serve(self, self.path, self.root)

    def log_message(self, *a):
        pass


def _load_page(site: str) -> tuple[str, list[int]]:
    """Serve `site` as the server does (tandemapp/static.py) and load /dashboard/ with every asset
    its index.html names; (X-Tandem-Bundle, the statuses)."""
    handler = type("P", (_Page,), {"root": site})
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}/dashboard/"
        with urllib.request.urlopen(base, timeout=10) as r:
            html, tag, codes = r.read().decode(), r.headers["X-Tandem-Bundle"], [r.status]
        assets = re.findall(r'(?:src|href)="\./([^"]+)"', html)
        assert assets and any(a.endswith(".js") for a in assets), html[:300]
        for a in assets:
            with urllib.request.urlopen(base + a, timeout=10) as r:
                codes.append(r.status)
                assert r.headers["X-Tandem-Bundle"] == tag
        return tag, codes
    finally:
        srv.shutdown()
        srv.server_close()


def test_a_fresh_checkout_publishes_and_the_page_loads():
    """Scenario "Local deploy still works": a tree with no dist; `publish` builds it, copies it to
    the web folder (a whole new folder, swapped in) and the page and its assets load from there."""
    d = _tmp("publish")
    try:
        app = _app_tree(d)
        site = os.path.join(d, "www", "tandemapp")
        os.makedirs(site)
        with open(os.path.join(site, "stale.js"), "w") as f:
            f.write("old")
        code, out = _tool(app, ["publish", "--to", site], _env(_fake_bin(d)))
        assert code == 0 and "published bundle" in out, out
        assert not os.path.exists(os.path.join(site, "stale.js")), "the old folder is replaced"
        assert bundle.verify(site)["ok"]
        tag, codes = _load_page(site)
        assert tag == bundle.read_manifest(site)["sha256"][:16], tag
        assert set(codes) == {200}, codes
        assert sorted(os.listdir(os.path.join(d, "www"))) == ["tandemapp"], "no temp folder left"
        # a host:/path target goes through rsync with --delete (a stand-in rsync records it)
        rlog = os.path.join(d, "rsync.log")
        b = _fake_bin(d)
        with open(os.path.join(b, "rsync"), "w") as f:
            f.write(f"#!{sys.executable}\nimport sys\n"
                    f"open({rlog!r}, 'w').write(' '.join(sys.argv[1:]))\n")
        os.chmod(os.path.join(b, "rsync"), 0o755)
        code, out = _tool(app, ["publish", "--to", "web:/var/www/tandemapp"], _env(b))
        assert code == 0, out
        with open(rlog) as f:
            assert f.read() == ("-a --delete --checksum " + os.path.join(app, "dashboard", "dist")
                                + "/ web:/var/www/tandemapp/")
        # no npm and no URL: publish stops before it copies anything
        app2 = _app_tree(os.path.join(d, "two"))
        site2 = os.path.join(d, "www2")
        code, out = _tool(app2, ["publish", "--to", site2], _env(None))
        assert code == 2 and not os.path.exists(site2) and "not built" in out, out
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_real_build_publish_and_load():
    """The same with the real npm and Vite on this tree's dashboard, twice: the two bundles are
    the same. Only with TANDEM_BUNDLE_REAL_BUILD=1 (Node 20.19 or newer, the network)."""
    if os.environ.get("TANDEM_BUNDLE_REAL_BUILD") != "1":
        from scriptmode import Skip
        raise Skip("TANDEM_BUNDLE_REAL_BUILD is not 1 (needs Node and the network)")
    d = _tmp("real")
    try:
        app = os.path.join(d, "app")
        shutil.copytree(ROOT, app, ignore=shutil.ignore_patterns(
            "node_modules", "dist", "results", "__pycache__", "e2e-*", "screenshots"))
        env = dict(os.environ)
        code, out = _tool(app, ["build"], env)
        assert code == 0, out[-3000:]
        code, out = _tool(app, ["repro"], env)
        assert code == 0 and "PASS two builds, one hash" in out, out[-3000:]
        code, out = _tool(app, ["publish", "--to", os.path.join(d, "www")], env)
        assert code == 0, out[-2000:]
        tag, codes = _load_page(os.path.join(d, "www"))
        assert set(codes) == {200} and len(codes) > 5, codes
        print(f"    real bundle {bundle.read_manifest(os.path.join(d, 'www'))['sha256']} "
              f"({len(codes) - 1} assets loaded)")
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------------------------ the server names its bundle
def test_the_server_names_the_bundle_and_the_placeholder_says_how():
    d = _tmp("serve")
    try:
        dist = _page(d)
        sha = bundle.read_manifest(dist)["sha256"]
        tag, codes = _load_page(dist)
        assert tag == sha[:16] and set(codes) == {200}
        assert static.bundle_info(dist) == {"present": True, "sha256": sha, "verified": True,
                                            "source": "build", "commit": None}
        with open(os.path.join(dist, "favicon.svg"), "a") as f:
            f.write("changed")
        assert static.bundle_tag(dist).endswith(" modified")
        assert not static.bundle_info(dist)["verified"]
        # a folder with no index.html (the editable install's empty folders): the placeholder
        empty = os.path.join(d, "empty")
        os.makedirs(os.path.join(empty, "assets"))
        handler = type("P", (_Page,), {"root": empty})
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/dashboard/",
                                        timeout=10) as r:
                body = r.read()
                assert r.status == 200 and r.headers["X-Tandem-Bundle"] == "none"
        finally:
            srv.shutdown()
            srv.server_close()
        assert b"not built" in body and b"dashboard_bundle.py" in body and b"fetch" in body
        assert b"commit" not in body.lower(), "the placeholder no longer says to commit a build"
        assert static.bundle_info(empty)["present"] is False
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_health_and_check_name_the_bundle():
    from proxy_util import Proxy
    from proxy import app as proxy_app
    from test_package import _run
    d = _tmp("health")
    try:
        dist = _page(d)
        sha = bundle.read_manifest(dist)["sha256"]
        with Proxy() as px:
            proxy_app.STATE["dashboard_dir"] = dist
            st, _, body = px.get("/health")
            assert st == 200 and json.loads(body)["dashboard"] == {
                "present": True, "sha256": sha, "verified": True, "source": "build",
                "commit": None}, body
            st, head, _ = px.get("/dashboard/")
            assert st == 200 and head.get("X-Tandem-Bundle") == sha[:16], head
        env = {"HOME": d, "PATH": os.environ.get("PATH", ""), "TANDEM_NODE_AGENT": "off"}
        db = os.path.join(d, "conf", "config.sqlite3")
        code, out = _run(["check", "--config-db", db, "--dashboard-dir", dist], env)
        assert code == 0 and f"bundle {sha[:16]} (build)" in out, out
        code, out = _run(["check", "--config-db", db, "--dashboard-dir",
                          os.path.join(d, "nothing")], env)
        assert code == 0 and "MISSING: the page shows a placeholder" in out and \
            "dashboard_bundle.py build" in out, out
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------------------------ the build backend (wheel)
def _backend():
    """packaging/tandemapp_build.py with a stand-in setuptools.build_meta that records calls."""
    calls = []
    meta = types.ModuleType("setuptools.build_meta")
    for name in ("build_wheel", "build_sdist", "build_editable", "prepare_metadata_for_build_wheel",
                 "prepare_metadata_for_build_editable", "get_requires_for_build_wheel",
                 "get_requires_for_build_sdist", "get_requires_for_build_editable"):
        setattr(meta, name, (lambda n: lambda *a, **k: calls.append(n) or n)(name))
    st = types.ModuleType("setuptools")
    st.build_meta = meta
    saved = {k: sys.modules.get(k) for k in ("setuptools", "setuptools.build_meta")}
    sys.modules.update({"setuptools": st, "setuptools.build_meta": meta})
    try:
        spec = importlib.util.spec_from_file_location(
            "tandemapp_build_t", os.path.join(ROOT, "packaging", "tandemapp_build.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return mod, calls


def test_the_build_backend_wants_the_page_for_a_wheel():
    """AC4 (wheel): the wheel build takes an intact page, fetches a pinned one, or stops with
    what to do (SystemExit, no traceback) before setuptools runs."""
    with open(os.path.join(ROOT, "pyproject.toml")) as f:
        toml = f.read()
    assert re.search(r'^build-backend = "tandemapp_build"$', toml, re.M)
    assert re.search(r'^backend-path = \["packaging"\]$', toml, re.M)
    d = _tmp("backend")
    try:
        mod, calls = _backend()
        app = _app_tree(os.path.join(d, "a"))
        try:
            mod.need_page("wheel", root=app, env={})
            raise AssertionError("a wheel without the page")
        except SystemExit as exc:
            text = str(exc.code)
            assert "no built page" in text and "dashboard_bundle.py build" in text, text
        dist = _page(os.path.join(d, "b"))
        arch = os.path.join(d, "rel.tar.gz")
        sha = bundle.pack(dist, arch)
        doc = mod.need_page("wheel", root=app, env={"TANDEM_DASHBOARD_URL": arch,
                                                     "TANDEM_DASHBOARD_SHA256": sha})
        assert doc["source"] == "fetch"
        assert bundle.verify(os.path.join(app, "dashboard", "dist"))["ok"]
        assert mod.need_page("sdist", root=app, env={})["sha256"] == doc["sha256"], "intact: kept"
        with open(os.path.join(app, "dashboard", "dist", "sw.js"), "a") as f:
            f.write("x")
        try:
            mod.need_page("wheel", root=app, env={})
            raise AssertionError("a changed page went into a wheel")
        except SystemExit as exc:
            assert "does not match its bundle.json" in str(exc.code)
        app3 = _app_tree(os.path.join(d, "c"))
        try:
            mod.need_page("wheel", root=app3, env={"TANDEM_DASHBOARD_URL": arch,
                                                   "TANDEM_DASHBOARD_SHA256": "0" * 64})
            raise AssertionError("a wrong sha256 went into a wheel")
        except SystemExit as exc:
            assert "could not be fetched" in str(exc.code)
        # the hooks check before they hand over to setuptools: a tree with an intact page
        good = _app_tree(os.path.join(d, "good"))
        bundle.fetch(arch, sha, os.path.join(good, "dashboard", "dist"))
        mod.ROOT = good
        try:
            mod.get_requires_for_build_wheel()
            mod.get_requires_for_build_sdist()
            mod.build_wheel(d)
            mod.build_sdist(d)
            mod.prepare_metadata_for_build_wheel(d)
            mod.ROOT = app                                # the changed page: stopped first
            for hook in ("get_requires_for_build_wheel", "get_requires_for_build_sdist",
                         "build_wheel", "build_sdist", "prepare_metadata_for_build_wheel"):
                try:
                    getattr(mod, hook)(*([d] if hook.startswith(("build", "prepare")) else []))
                    raise AssertionError(f"{hook} went on with a changed page")
                except SystemExit:
                    pass
        finally:
            mod.ROOT = ROOT
        assert calls == ["get_requires_for_build_wheel", "get_requires_for_build_sdist",
                         "build_wheel", "build_sdist", "prepare_metadata_for_build_wheel"], calls
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_an_editable_install_wants_the_page_unless_told_later():
    d = _tmp("editable")
    try:
        mod, calls = _backend()
        app = _app_tree(d)
        try:
            mod.editable_page(root=app, env={})
            raise AssertionError("an editable install without the page and without LATER")
        except SystemExit as exc:
            text = str(exc.code)
            assert "no built page" in text and "TANDEM_DASHBOARD_LATER=1" in text, text
        assert not os.path.exists(os.path.join(app, "dashboard", "dist"))
        later = {"TANDEM_DASHBOARD_LATER": "1"}
        err = io.StringIO()
        assert mod.editable_page(root=app, err=err, env=later) is False
        assert os.path.isdir(os.path.join(app, "dashboard", "dist", "assets"))
        note = err.getvalue()
        assert "no built page yet" in note and "dashboard_bundle.py build" in note, note
        err2 = io.StringIO()
        mod.editable_page(root=app, err=err2, env=later)
        assert err2.getvalue() == "", "the note once a process"
        # the editable hooks go on to setuptools without a page when told later
        mod.ROOT = app
        old_env = os.environ.get("TANDEM_DASHBOARD_LATER")
        os.environ["TANDEM_DASHBOARD_LATER"] = "1"
        try:
            mod.get_requires_for_build_editable()
            mod.prepare_metadata_for_build_editable(d)
            mod.build_editable(d)
        finally:
            mod.ROOT = ROOT
            if old_env is None:
                os.environ.pop("TANDEM_DASHBOARD_LATER", None)
            else:
                os.environ["TANDEM_DASHBOARD_LATER"] = old_env
        assert calls == ["get_requires_for_build_editable", "prepare_metadata_for_build_editable",
                         "build_editable"], calls
        # the server then shows the placeholder for those empty folders
        assert static.bundle_tag(os.path.join(app, "dashboard", "dist")) == "none"
        _page(os.path.join(d, "built"))
        dist = os.path.join(d, "built", "app", "dashboard", "dist")
        assert mod.editable_page(root=os.path.dirname(os.path.dirname(dist)), env={}) is True
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_the_sdist_carries_the_page_and_the_backend():
    with open(os.path.join(ROOT, "MANIFEST.in")) as f:
        lines = f.read().splitlines()
    for want in ("include VERSION", "include packaging/tandemapp_build.py",
                 "include tools/dashboard_bundle.py", "graft dashboard/dist"):
        assert want in lines, want


# ------------------------------------------------------------------ other readers of the page
def test_the_node_wheel_builds_from_a_checkout_without_the_page():
    """The controller builds a TandemNode wheel from its own checkout (Add Spark); a checkout
    with no dashboard/dist gives a wheel without the page instead of a FileNotFoundError."""
    from proxy import nodeinstall
    d = _tmp("node")
    try:
        app = os.path.join(d, "app")
        shutil.copytree(ROOT, app, ignore=shutil.ignore_patterns(
            "dashboard", "tests", "node_modules", "results", "__pycache__", "docs", "bench"))
        os.makedirs(os.path.join(app, "docs"))
        shutil.copy(os.path.join(ROOT, "docs", "proxy.md"), os.path.join(app, "docs"))
        _, files = nodeinstall.checkout_files(app)
        names = {w for w, _ in files}
        assert "proxy/__init__.py" in names and "tandemapp/bundle.py" in names
        assert not [n for n in names if n.startswith("proxy/dashboard/")]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_the_chat_e2e_stack_refuses_without_the_page():
    d = _tmp("e2e")
    try:
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tests", "chat_e2e_stack.py"),
                            "--dashboard", os.path.join(d, "dist")], capture_output=True,
                           text=True, timeout=60, env=dict(os.environ, TANDEM_NODE_AGENT="off"))
        assert r.returncode == 2 and "no built page" in r.stderr and \
            "dashboard_bundle.py build" in r.stderr, (r.returncode, r.stderr[-800:])
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------------------------ CI, the budget, the packaging
def _yaml(path: str) -> dict:
    import yaml
    with open(path) as f:
        return yaml.load(f, Loader=yaml.BaseLoader)         # `on` stays the string "on"


def test_the_ci_workflow_builds_tests_checks_and_uploads():
    """Scenario "CI builds and checks the UI" (AC2), read from the workflow files: the build job
    runs npm ci and the build through the tool, vitest with the dist required, the size budget
    as a failing step, the two-build check, and uploads the page; the app job installs the wheel
    with that very page. The workflow file is read here; its commands are run by the build tests."""
    p = os.path.join(WF, "dashboard-dist.yml")
    if not os.path.isfile(p):
        from scriptmode import Skip
        raise Skip("no .github/workflows here (TandemApp's own tree)")
    wf = _yaml(p)
    assert "workflow_call" in wf["on"] and "workflow_dispatch" in wf["on"]
    job = wf["jobs"]["build"]
    steps = job["steps"]
    runs = [s.get("run", "") for s in steps]
    order = [next(i for i, r in enumerate(runs) if pat in r) for pat in (
        "dashboard_bundle.py build", "npm test", "npm run test:size", "dashboard_bundle.py repro",
        "dashboard_bundle.py pack")]
    assert order == sorted(order), f"the steps run out of order: {order}"
    assert not [s for s in steps if s.get("continue-on-error")], "every step is a gate"
    tests_step = steps[order[1]]
    assert tests_step.get("env", {}).get("DASHBOARD_DIST_REQUIRED") == "1"
    ups = {s["with"]["name"]: s for s in steps
           if s.get("uses", "").startswith("actions/upload-artifact@")}
    assert set(ups) == {"dashboard-dist", "dashboard-bundle"}, ups
    assert ups["dashboard-dist"]["with"]["path"] == "app/dashboard/dist"
    assert ups["dashboard-dist"]["with"]["if-no-files-found"] == "error"
    assert job["outputs"]["bundle"] and wf["on"]["workflow_call"]["outputs"]["bundle"]
    for s in steps:
        if "uses" in s:
            assert re.fullmatch(r"[\w-]+/[\w-]+@v\d+\.\d+\.\d+", s["uses"]), s["uses"]
    with open(p) as f:
        text = f.read()
    assert "npm install" not in text and "git status --porcelain -- dist" not in text, \
        "no install outside the lockfile, no committed-dist check"
    tests = _yaml(os.path.join(WF, "tests.yml"))
    assert tests["jobs"]["dashboard"]["uses"] == "./.github/workflows/dashboard-dist.yml"
    app = tests["jobs"]["app"]
    assert app["needs"] == "dashboard"
    dl = [s for s in app["steps"] if s.get("uses", "").startswith("actions/download-artifact@")]
    assert len(dl) == 1 and dl[0]["with"]["name"] == "dashboard-dist"
    install = next(s["run"] for s in app["steps"] if "pip install" in s.get("run", ""))
    assert install.index('app-src/dashboard/dist"') < install.index("pip install \"$RUNNER_TEMP"), \
        "the page is in the copy before the wheel is built"
    assert "--expect \"${{ needs.dashboard.outputs.bundle }}\"" in install


def test_the_size_budget_lives_in_one_place():
    """AC3: one value, BUDGET in scripts/gzip-size.mjs; the size check, the build test and CI read
    it. Over the budget fails: tests/size-gate.test.ts runs check-size.mjs on a fixture."""
    dash = os.path.join(ROOT, "dashboard")
    with open(os.path.join(dash, "scripts", "gzip-size.mjs")) as f:
        kb = re.search(r"^export const BUDGET = (\d+) \* 1024;$", f.read(), re.M).group(1)
    defs, literals = [], []
    paths = [os.path.join(dash, "scripts", n) for n in os.listdir(os.path.join(dash, "scripts"))]
    paths += [os.path.join(dash, "tests", n) for n in os.listdir(os.path.join(dash, "tests"))]
    paths += [os.path.join(dash, "vite.config.ts"), os.path.join(dash, "package.json")]
    if os.path.isdir(WF):
        paths += [os.path.join(WF, n) for n in os.listdir(WF)]
    for p in paths:
        if not os.path.isfile(p):
            continue
        with open(p) as f:
            for i, line in enumerate(f, 1):
                code = line.split("//")[0] if not p.endswith((".yml", ".sh")) else \
                    line.split("#")[0]
                if re.search(r"\bBUDGET\s*=", code):
                    defs.append(f"{os.path.relpath(p, ROOT)}:{i}")
                if re.search(rf"\b{kb}\s*(\*\s*1024\b|KB\b)|\b{int(kb) * 1024}\b", code):
                    literals.append(f"{os.path.relpath(p, ROOT)}:{i}")
    assert defs == ["dashboard/scripts/gzip-size.mjs:" + defs[0].split(":")[1]] and \
        len(defs) == 1, defs
    assert literals == defs, f"a budget number outside gzip-size.mjs: {literals}"
    with open(os.path.join(dash, "tests", "size-gate.test.ts")) as f:
        gate = f.read()
    assert "toBe(1)" in gate and "over budget" in gate and "BUDGET" in gate
    with open(os.path.join(dash, "package.json")) as f:
        assert json.load(f)["scripts"]["test:size"] == "node scripts/check-size.mjs"


def test_the_image_builds_the_page_in_a_node_stage():
    """AC4 (Dockerfile): a Node stage runs npm ci and the build; the wheel stage takes its dist,
    records and verifies it; the context ships the sources, not a dist or node_modules."""
    with open(os.path.join(ROOT, "packaging", "Dockerfile.app")) as f:
        lines = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    stages = [ln.split()[-1] for ln in lines if ln.startswith("FROM ")]
    assert stages[0] == "page" and re.match(r"FROM node:\d+\.\d+\.\d+-", lines[0]), lines[0]
    page = lines[:lines.index(next(ln for ln in lines if ln.startswith("FROM python")))]
    assert "COPY dashboard/package.json dashboard/package-lock.json ./" in page
    assert "RUN npm ci --no-audit --no-fund" in page and "RUN npm run build" in page
    assert page.index("RUN npm ci --no-audit --no-fund") < page.index("RUN npm run build")
    assert "COPY --from=page /src/dashboard/dist dashboard/dist" in lines
    assert not [ln for ln in lines if re.fullmatch(r"COPY dashboard/dist\b.*", ln)], \
        "the image no longer copies a dist from the checkout"
    run = next(ln for ln in lines if "dashboard_bundle.py record" in ln)
    assert lines.index("COPY --from=page /src/dashboard/dist dashboard/dist") < lines.index(run)
    with open(os.path.join(ROOT, "packaging", "Dockerfile.app.dockerignore")) as f:
        ign = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    for want in ("!dashboard/", "dashboard/node_modules", "dashboard/dist",
                 "!packaging/tandemapp_build.py", "!tools/dashboard_bundle.py"):
        assert want in ign, want


def test_homebrew_and_install_sh_get_the_page():
    """AC4 (the other install paths): the formula installs the release's bundle (Homebrew checks
    its sha256) or builds it with Node for head, and verifies it; install.sh runs ensure after
    the engine's checkout and goes on without the page, with the reason."""
    with open(os.path.join(ROOT, "packaging", "homebrew", "tandemapp.rb")) as f:
        rb = f.read()
    assert re.search(r'resource "dashboard" do\s+url "[^"]+tandemapp-dashboard-[^"]+\.tar\.gz"\s+'
                     r'sha256 "[0-9a-f]{64}"', rb), "no dashboard resource with a sha256"
    assert re.search(r'head do\s+url "[^"]+"[^\n]*\n(\s*#[^\n]*\n)*'
                     r'\s+depends_on "node" => :build', rb)
    assert 'src/"tools/dashboard_bundle.py", "build"' in rb
    assert 'src/"tools/dashboard_bundle.py", "verify"' in rb
    assert 'resources.reject { |r| r.name == "dashboard" }' in rb, \
        "the bundle is not a pip resource"
    inst = os.path.join(REPO, "install.sh")
    if not os.path.isfile(inst):
        return                                            # TandemApp's own tree has no install.sh
    with open(inst) as f:
        sh = f.read()
    i = sh.index('dashboard_bundle.py" ensure')
    assert sh.index('if [ "$RUNTIME_ONLY" = 1 ]; then\n    printf') < i < sh.index("# 4. Weights")
    block = sh[sh.rindex("\nif [", 0, i):sh.index("\nfi\n", i)]
    assert "warn " in block and "|| die" not in block and "fail " not in block, \
        "a missing page never stops the engine's install"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
