"""The engine starts and serves with no TandemApp present (ENG-413 WP13; QA ENG-414).

server/app.py takes TandemApp's three modules it still runs until WP14 (setup mode's page
tandemapp.static, the /v1/chat-app forward tandemapp.chat_forward, the System view's hosts
proxy.hostcollect) at their first use, from the vendored tree while it is here or from the
extension's package when QSE_EXT names TandemApp's (`_app_part`). Without either the engine is
headless and answers as engine API 1.4 says. Each test runs in a copy of the engine's tree with no
TandemApp folder and with TandemApp's packages blocked in the child, so an installed TandemApp
cannot stand in for one.

  1  the server imports: no finder, no TandemApp module loaded, every part None, the setup registry
     holds every model (the backends' docs link into TandemApp is TandemApp's own); with QSE_EXT
     naming TandemApp's extension the parts are asked for (and missing here); in this tree, when
     it carries the vendored folder, the parts are the vendored modules
                                                   test_the_server_imports_without_tandemapp
  2  a fake engine starts in that copy: /health mode engine and extensions []; the page 404 no_ui;
     /v1/chat-app and every path under it 404 not_on_engine (GET and POST); the conductor's,
     library, memory, Laya, config and collect paths 404 not_on_engine; the System view without
     hosts; the dashboard's session read and a chat served; no traceback in the log
                                                   test_a_fake_engine_without_tandemapp
  3  setup mode in that copy: the page 404 no_ui, /health mode setup; `--backends` refused at start
     with one line naming tandem-app               test_setup_mode_and_backends_without_tandemapp

1 needs only the engine's imports; 2 and 3 start server/app.py --fake-engine (CPU torch).
Run: python tests/test_headless_start.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

#: what the copy carries: the engine's code and what its start reads
KEEP = ("server", "engine", "models", "ops", "contracts", "tools", "docs", "VERSION")
#: TandemApp's packages, blocked in every child (None in sys.modules: an import of one fails)
BLOCK = ("tandemapp", "proxy", "chatd")
RAW = "qwen38-spark-engine"
ADMIN = "headless-admin-token-0123456789abcdef"
PRELUDE = ("import sys; [sys.modules.__setitem__(m, None) for m in %r];"
           "sys.modules['triton'] = None; sys.modules['triton.language'] = None;" % (BLOCK,))
LAUNCH = PRELUDE + ("sys.argv = ['server/app.py'] + sys.argv[1:]; import runpy;"
                    "runpy.run_path('server/app.py', run_name='__main__')")


def _torch() -> bool:
    try:
        import torch  # noqa: F401
        return True
    except ImportError:
        return False


def _copy() -> str:
    """The engine's tree without the vendored folder, in a fresh temp dir."""
    d = os.path.join(tempfile.mkdtemp(prefix="headless-"), "engine")
    os.makedirs(d)
    for name in KEEP:
        src = os.path.join(ROOT, name)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(d, name), symlinks=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        elif os.path.isfile(src):
            shutil.copy2(src, os.path.join(d, name))
    return d


def _env(tree: str, **extra) -> dict:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.path.dirname(tree),
           "PYTHONPATH": tree, "CUDA_VISIBLE_DEVICES": "", "QSE_ADMIN_TOKEN": ADMIN,
           "LANG": "C.UTF-8"}
    env.update(extra)
    return env


PROBE = PRELUDE + r"""
import json, os
import server
from server import app as A
before = sorted(m for m in sys.modules if m.split('.')[0] in %r and sys.modules[m] is not None)
parts = {n: A._app_part(n) for n in A._APP_PART_MODULES}
from server.setup import registry as R
models, errors = R.load_all()
print(json.dumps({
    "models": sorted(models), "registry_errors": [str(e)[:200] for e in errors],
    "vendored": server.APP_VENDORED,
    "finder": any(getattr(f, "tandem_app_finder", False) for f in sys.meta_path),
    "here": A._app_parts_here(),
    "parts": {n: (getattr(m, "__file__", None) if m is not None else None) for n, m in parts.items()},
    "loaded": before,
}))
""" % (BLOCK,)


def _probe(tree: str, env_extra: dict | None = None, block: bool = True) -> dict:
    code = PROBE if block else PROBE.replace(PRELUDE, "import sys;", 1)
    r = subprocess.run([sys.executable, "-c", code], cwd=tree, env=_env(tree, **(env_extra or {})),
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-3000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_the_server_imports_without_tandemapp():
    if not _torch():
        return "skipped: no torch"
    tree = _copy()
    try:
        assert not os.path.exists(os.path.join(tree, "app"))
        got = _probe(tree)
        assert got["vendored"] is False and got["finder"] is False and got["here"] is False, got
        assert got["loaded"] == [] and set(got["parts"].values()) == {None}, got
        # the setup registry: every model of models/, the backends' TandemApp docs link taken as TandemApp's
        want = sorted(d for d in os.listdir(os.path.join(tree, "models"))
                      if os.path.isfile(os.path.join(tree, "models", d, "model.yaml")))
        assert want and len(got["models"]) == len(want) and got["registry_errors"] == [], (want, got)
        # QSE_EXT names TandemApp's extension: the parts are asked for, and none is installed here
        got = _probe(tree, {"QSE_EXT": "tandemapp.engine_ext"})
        assert got["here"] is True and set(got["parts"].values()) == {None}, got
        # another extension's module does not make TandemApp's parts this process's
        got = _probe(tree, {"QSE_EXT": "fake_ext"})
        assert got["here"] is False and set(got["parts"].values()) == {None}, got
    finally:
        shutil.rmtree(os.path.dirname(tree), ignore_errors=True)
    import server
    if not server.APP_VENDORED:
        return "headless: no finder, nothing of TandemApp loaded, every part None (this tree has no vendored folder)"
    # this tree carries the vendored folder: the parts are its modules, as before WP13
    got = _probe(ROOT, block=False)
    assert got["vendored"] is True and got["finder"] is True and got["here"] is True, got
    for n, f in got["parts"].items():
        assert f and os.path.dirname(os.path.dirname(f)) == os.path.join(ROOT, "app"), (n, f)
    return "headless: no finder, nothing of TandemApp loaded, every part None; vendored: the vendored modules"


def _start(tree: str, *args: str, env_extra: dict | None = None) -> subprocess.Popen:
    ledger = os.path.join(os.path.dirname(tree), "ledger.sqlite3")
    cmd = [sys.executable, "-c", LAUNCH, "--fake-engine", "--port", "0", "--served-model", RAW,
           "--usage-ledger", ledger, "--fake-tps", "2000", *args]
    return subprocess.Popen(cmd, cwd=tree, env=_env(tree, **(env_extra or {})), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)


def _up(p) -> tuple[int, list]:
    seen, end, port = [], time.time() + 180, None
    while port is None and time.time() < end:
        line = p.stdout.readline()
        if not line:
            raise AssertionError("the server ended before it listened:\n" + "".join(seen[-40:]))
        seen.append(line)
        m = re.search(r"\[server\] listening on http://[^:]+:(\d+)", line)
        if m:
            port = int(m.group(1))
    while time.time() < end:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2)
            return port, seen
        except urllib.error.HTTPError:
            return port, seen
        except OSError:
            time.sleep(0.2)
    raise AssertionError(f"port {port} did not answer")


def _stop(p, seen=()) -> str:
    p.send_signal(signal.SIGTERM)
    try:
        out, _ = p.communicate(timeout=60)
    except subprocess.TimeoutExpired:
        p.kill()
        out, _ = p.communicate(timeout=10)
    return "".join(seen) + out


def _call(port: int, method: str, path: str, body=None):
    raw = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=raw, method=method,
                                 headers={"Authorization": f"Bearer {ADMIN}",
                                          **({"Content-Type": "application/json"} if raw else {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            code, data = r.status, r.read()
    except urllib.error.HTTPError as e:
        code, data = e.code, e.read()
    try:
        return code, json.loads(data)
    except ValueError:
        return code, data.decode("utf-8", "replace")


def _err(body) -> str | None:
    return ((body or {}).get("error") or {}).get("type") if isinstance(body, dict) else None


def test_a_fake_engine_without_tandemapp():
    if not _torch():
        return "skipped: no torch"
    tree = _copy()
    p = _start(tree)
    seen: list = []
    try:
        port, seen = _up(p)
        st, h = _call(port, "GET", "/health")
        assert st == 200 and h["status"] == "ok" and h["mode"] == "engine" and h["extensions"] == [], h
        st, m = _call(port, "GET", "/v1/models")
        assert st == 200 and [x["id"] for x in m["data"]] == [RAW], m
        for path in ("/dashboard", "/dashboard/", "/dashboard/index.html"):
            st, b = _call(port, "GET", path)
            assert st == 404 and _err(b) == "no_ui" and "TandemApp" in b["error"]["message"], (path, st, b)
        chat = [("GET", "/v1/chat-app"), ("GET", "/v1/chat-app/"), ("GET", "/v1/chat-app/capabilities"),
                ("GET", "/v1/chat-app/session"), ("POST", "/v1/chat-app/session"),
                ("POST", "/v1/chat-app/runs"), ("DELETE", "/v1/chat-app/session"),
                ("PUT", "/v1/chat-app/x"), ("PATCH", "/v1/chat-app/x")]
        for method, path in chat:
            st, b = _call(port, method, path, {} if method in ("POST", "PUT", "PATCH") else None)
            assert st == 404 and _err(b) == "not_on_engine" and "TandemApp" in b["error"]["message"], \
                (method, path, st, b)
            assert b["error"]["code"] == "not_on_engine", b
        rest = [("GET", "/v1/conductor/settings"), ("GET", "/v1/library"), ("GET", "/v1/memory"),
                ("GET", "/v1/laya"), ("GET", "/s/nWJkUiDZ59TX/v1/library"), ("GET", "/v1/config"),
                ("POST", "/v1/collect/batch")]
        for method, path in rest:
            st, b = _call(port, method, path, {} if method == "POST" else None)
            assert st == 404 and _err(b) == "not_on_engine", (method, path, st, b)
        # a path beside the prefix is not the forward's
        st, b = _call(port, "GET", "/v1/chat-apps")
        assert st == 404 and _err(b) != "not_on_engine", b
        st, s = _call(port, "GET", "/v1/dashboard/system")
        assert st == 200 and "hosts" not in s and "gpu" in s and s["proxy"]["mode"] == "off", sorted(s)
        st, s = _call(port, "GET", "/v1/dashboard/session")
        assert st == 200 and s["authenticated"] is True, s
        st, b = _call(port, "POST", "/v1/chat/completions",
                      {"model": RAW, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 8})
        assert st == 200 and b["choices"][0]["message"], b
    finally:
        out = _stop(p, seen)
        shutil.rmtree(os.path.dirname(tree), ignore_errors=True)
    assert "[ext] none: a headless engine" in out, out[-2000:]
    assert "Traceback" not in out, out[-3000:]
    return (f"extensions [], 3 page paths no_ui, {len(chat)} chat-app requests and {len(rest)} other paths "
            f"not_on_engine, no hosts, a chat served")


def test_setup_mode_and_backends_without_tandemapp():
    if not _torch():
        return "skipped: no torch"
    tree = _copy()
    try:
        from ports import free_port               # setup mode prints no listening line
        port = free_port()
        p = subprocess.Popen([sys.executable, "-c", LAUNCH, "--setup", "--host", "127.0.0.1", "--port",
                              str(port)], cwd=tree, env=_env(tree), stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True)
        got, end = [], time.time() + 180
        try:
            while p.poll() is None and time.time() < end:
                try:
                    got.append(_call(port, "GET", "/health"))
                    break
                except OSError:
                    time.sleep(0.3)
            for path in ("/dashboard", "/dashboard/") if got else ():
                got.append(_call(port, "GET", path))
        finally:
            out = _stop(p)
        assert len(got) == 3, out[-3000:]
        st, h = got[0]
        assert st == 200 and h["mode"] == "setup", h
        for st, b in got[1:]:
            assert st == 404 and _err(b) == "no_ui", (st, b)
        assert "Traceback" not in out, out[-3000:]
        p = _start(tree, "--backends", os.path.join(os.path.dirname(tree), "backends.sqlite3"))
        out, _ = p.communicate(timeout=300)
        assert p.returncode != 0 and "[server] listening" not in out, out[-3000:]
        lines = [ln for ln in out.splitlines() if ln.startswith("[proxy] --backends")]
        assert len(lines) == 1 and "tandem-app" in lines[0], out[-3000:]
    finally:
        shutil.rmtree(os.path.dirname(tree), ignore_errors=True)
    return "setup mode: the page 404 no_ui; --backends refused with one line"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
