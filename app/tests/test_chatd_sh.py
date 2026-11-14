"""ops/chatd.sh and ops/chatd.service follow the install (OPS-153, QA OPS-154; GitHub issue #3).

`ops/chatd.sh` runs for real against a temporary tree laid out as install.sh lays one out
(DIR/src with TandemApp's tree in DIR/src/app: the `chatd` package and the script, beside the
engine's server/app.py; DIR/venv/bin/python, DIR/run/ops/serve.env, DIR/state/secrets.env, the
.tandemllm-install marker). The interpreters are stubs that write their argument vector to a file
and then exec this Python, so a test can tell which one ran chatd. Every chatd here runs on a free
port with a data folder of its own; none is left running.

The test matrix, the Gherkin of OPS-153:

  1  the install's venv is used, and the start line names it        test_the_installs_venv_is_used
     (by hand in an installed tree: serve.env's PY; with no serve.env: ../venv)
                                                                     test_the_venv_beside_the_checkout
  2  CHATD_PY wins over serve.env's PY and the venv                  test_chatd_py_wins
  3  no venv: python3, said so with its version, a warning below 3.11
                                                                     test_no_venv_falls_back_to_python3
  4  a start that fails exits 1, prints the log's last lines, leaves no pid file
                                                                     test_a_start_that_fails_is_a_failure
  5  both sides read one secrets file: the engine's forward of GET /v1/chat-app/capabilities answers
     200 with the key in QSE_SECRETS, with the key only in chatd's forward.key, and with no key
     anywhere; with the admin password in the file too, chatd's environment gets the forward key
     and never the admin token                                       test_both_sides_read_one_secrets_file
  6  make-secrets.sh writes where the engine reads: the engine's (tests/test_engine_scripts.py)
  7  a reused pid is not a running chatd: start replaces the pid file, stop signals nothing
                                                                     test_a_reused_pid_is_not_a_running_chatd
  8  the port follows QSE_CHAT_URL                                   test_the_port_follows_qse_chat_url
  9  ops/chatd.service runs from the installer's layout (src/app)    test_the_unit_file_matches_the_installer_layout
  10 run/chatd.env, logs/ and the venv are the install's, the repository checkout's root (SRV-747:
     TandemApp in app/ beside the engine) or TandemApp's own root; chatd runs in TandemApp's tree
                                                                     test_run_logs_and_the_venv_follow_the_tree

  11 stop --this-tree leaves another tree's chatd running and stops this tree's (from the engine's
     tests/test_engine_only.py, which the engine dropped with its launcher at ENG-413 WP7; Linux)
                                                                     test_stop_this_tree_only
  12 the script parses (from the engine's tests/test_ops_scripts.py, ENG-413 WP7)
                                                                     test_the_script_parses

Edge cases: a port another program holds fails visibly and names the port, status and restart
(test_a_taken_port_status_and_restart).

CPU only, standard library only. Run: python tests/test_chatd_sh.py
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from chatd_util import run_all  # noqa: E402

from tandemapp import chat_forward  # noqa: E402

# an interpreter that says who it is, then runs this Python
STUB = """#!/bin/bash
[ "$1" = -c ] || echo "{name} $*" >> "{log}"
exec "{py}" "$@"
"""


def _free_port() -> int:
    from ports import free_port
    return free_port()                 # OPS-231: no two tests get one port


def _stub(path: str, name: str, log: str, body: str | None = None) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(body if body is not None else STUB.format(name=name, log=log, py=sys.executable))
    os.chmod(path, 0o755)
    return path


class Tree:
    """DIR as install.sh makes it, with this checkout's chatd package and ops/chatd.sh in DIR/src/app
    and a stand-in DIR/src/server/app.py (layout "install"). Without an install around it: layout
    "checkout", the repository (TandemApp in repo/app beside the engine's server/app.py), and "app",
    TandemApp's own tree; their TOP is that root, and the venv goes there."""

    def __init__(self, venv: bool = True, serve_env: bool = True, layout: str = "install"):
        self.tmp = tempfile.mkdtemp(prefix="chatdsh-")
        self.dir = os.path.join(self.tmp, "TandemLLM")
        self.home = os.path.join(self.tmp, "home")
        #: the checkout's root (serve.env's REPO), which is chatd.sh's TOP (run/, logs/), and
        #: TandemApp's root in it (the chatd package, ops/chatd.sh)
        self.src = self.top = {"install": os.path.join(self.dir, "src"), "checkout": os.path.join(self.tmp, "repo"),
                               "app": os.path.join(self.tmp, "tandemapp")}[layout]
        self.app = self.top if layout == "app" else os.path.join(self.top, "app")
        os.makedirs(os.path.join(self.app, "ops"))
        os.makedirs(self.home)
        if layout != "app":
            os.makedirs(os.path.join(self.top, "server"))
            open(os.path.join(self.top, "server", "app.py"), "w").close()
        shutil.copytree(os.path.join(ROOT, "chatd"), os.path.join(self.app, "chatd"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy(os.path.join(ROOT, "ops", "chatd.sh"), os.path.join(self.app, "ops"))
        if layout == "install":
            os.makedirs(self.dir, exist_ok=True)
            open(os.path.join(self.dir, ".tandemllm-install"), "w").close()
        self.argv = os.path.join(self.tmp, "argv.log")
        self.port = _free_port()
        self.data = os.path.join(self.home, ".tandemllm", "chat")
        self.secrets = os.path.join(self.dir, "state", "secrets.env")
        #: the venv chatd.sh finds when nothing names an interpreter: DIR/venv, else TOP/venv
        self.venv = os.path.join(self.dir if layout == "install" else self.top, "venv", "bin", "python")
        if venv:
            _stub(self.venv, "venv", self.argv)
        if serve_env:
            os.makedirs(os.path.join(self.dir, "run", "ops"))
            with open(os.path.join(self.dir, "run", "ops", "serve.env"), "w") as fh:
                fh.write(f"REPO='{self.src}'\nPY='{self.dir}/venv/bin/python'\nPORT=18000\n"
                         f"QSE_SECRETS='{self.secrets}'\n"
                         f"if [ -f '{self.dir}/run/local.env' ]; then . '{self.dir}/run/local.env'; fi\n")
            with open(os.path.join(self.dir, "run", "local.env"), "w") as fh:
                fh.write(f"QSE_CHAT_URL=http://127.0.0.1:{self.port}\n")

    def env(self, **extra) -> dict:
        """What a user's shell has: no serve.env variables, no venv active."""
        e = {k: v for k, v in os.environ.items()
             if k not in ("PY", "REPO", "CHATD_PY", "VIRTUAL_ENV", "QSE_SECRETS", "QSE_STATE_DIR",
                          "QSE_CHAT_URL", "QSE_CHAT_DATA", "QSE_CHAT_FORWARD_KEY", "QSE_CHATD_ENV",
                          "PORT")}
        e["HOME"] = self.home
        if not os.path.exists(os.path.join(self.dir, "run", "local.env")):
            e["QSE_CHAT_URL"] = f"http://127.0.0.1:{self.port}"
        e.update({k: str(v) for k, v in extra.items() if v is not None})
        for k in [k for k, v in extra.items() if v is None]:
            e.pop(k, None)
        return e

    def sh(self, *args, **env) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", os.path.join(self.app, "ops", "chatd.sh"), *args],
                              capture_output=True, text=True, env=self.env(**env), timeout=60,
                              stdin=subprocess.DEVNULL)

    def pid(self) -> int | None:
        try:
            return int(open(os.path.join(self.data, "chatd.pid")).read())
        except (OSError, ValueError):
            return None

    def ran(self) -> str:
        return open(self.argv).read() if os.path.exists(self.argv) else ""

    def health(self, port: int | None = None) -> dict | None:
        try:
            c = http.client.HTTPConnection("127.0.0.1", port or self.port, timeout=2)
            c.request("GET", "/health")
            return json.loads(c.getresponse().read())
        except (OSError, ValueError):
            return None

    def close(self) -> None:
        pid = self.pid()
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)


def _out(r: subprocess.CompletedProcess) -> str:
    return r.stdout + r.stderr


def test_the_installs_venv_is_used():
    t = Tree()
    try:
        r = t.sh("start")
        assert r.returncode == 0, _out(r)
        venv = os.path.join(t.dir, "venv", "bin", "python")
        assert f"[chatd] started with {venv}" in r.stdout, r.stdout
        assert "venv -u -m chatd" in t.ran(), t.ran()
        assert t.health()["service"] == "chatd"
        st = t.sh("status")
        # the running process's interpreter (the stub has exec'd this Python by now)
        assert st.returncode == 0 and f"python {sys.executable} (" in st.stdout, _out(st)
        assert f"data {t.data}" in st.stdout and f"http://127.0.0.1:{t.port}" in st.stdout, st.stdout
        # start is idempotent
        again = t.sh("start")
        assert again.returncode == 0 and "already running" in again.stdout, _out(again)
        assert t.ran().count("-m chatd") == 1, t.ran()
        stop = t.sh("stop")
        assert stop.returncode == 0 and "stopped" in stop.stdout and t.pid() is None, _out(stop)
        assert t.sh("status").returncode == 1
    finally:
        t.close()


def test_the_venv_beside_the_checkout():
    t = Tree(serve_env=False)
    try:
        r = t.sh("start")
        assert r.returncode == 0, _out(r)
        assert f"started with {os.path.join(t.dir, 'venv', 'bin', 'python')}" in r.stdout, r.stdout
        assert "venv -u -m chatd" in t.ran()
    finally:
        t.close()


def test_chatd_py_wins():
    t = Tree()
    try:
        own = _stub(os.path.join(t.tmp, "own", "python"), "own", t.argv)
        r = t.sh("start", CHATD_PY=own)
        assert r.returncode == 0 and f"started with {own}" in r.stdout, _out(r)
        assert "own -u -m chatd" in t.ran() and "venv" not in t.ran(), t.ran()
        t.sh("stop")
        # serve.env's PY, exported by start.sh, comes before the venv beside the checkout
        other = _stub(os.path.join(t.tmp, "serve", "python"), "serve", t.argv)
        r = t.sh("start", PY=other, REPO=t.src, QSE_CHAT_URL=f"http://127.0.0.1:{t.port}")
        assert r.returncode == 0 and f"started with {other}" in r.stdout, _out(r)
        # a CHATD_PY that does not run is an error, not a silent fall-through
        t.sh("stop")
        r = t.sh("start", CHATD_PY=os.path.join(t.tmp, "nope"))
        assert r.returncode == 1 and "does not run" in r.stdout, _out(r)
    finally:
        t.close()


def test_no_venv_falls_back_to_python3():
    t = Tree(venv=False, serve_env=False)
    try:
        bindir = os.path.join(t.tmp, "bin")
        _stub(os.path.join(bindir, "python3"), "system", t.argv)
        path = bindir + os.pathsep + os.environ["PATH"]
        r = t.sh("start", PATH=path)
        assert r.returncode == 0, _out(r)
        ver = "%d.%d.%d" % sys.version_info[:3]
        assert f"no venv found: using the system python3 ({bindir}/python3, {ver})" in r.stdout, r.stdout
        assert "system -u -m chatd" in t.ran(), t.ran()
        t.sh("stop")
        # an old system Python: the warning (the stub answers the version probe with 3.9.6)
        _stub(os.path.join(bindir, "python3"), "old", t.argv, body=(
            '#!/bin/bash\ncase "$2" in *version_info*) echo 3.9.6; exit 0;; esac\n'
            f'echo "old $*" >> "{t.argv}"\nexec "{sys.executable}" "$@"\n'))
        r = t.sh("start", PATH=path)
        assert r.returncode == 0 and "WARNING: Python 3.9.6 is older than 3.11" in r.stdout, _out(r)
        # a broken venv is passed over with a note
        t.sh("stop")
        broken = os.path.join(t.dir, "venv", "bin", "python")
        os.makedirs(os.path.dirname(broken))
        with open(broken, "w") as fh:
            fh.write("not a program\n")
        os.chmod(broken, 0o755)
        r = t.sh("start", PATH=path)
        assert r.returncode == 0 and f"{broken} does not run; trying the next" in r.stdout, _out(r)
    finally:
        t.close()


def test_a_start_that_fails_is_a_failure():
    t = Tree()
    try:
        bad = _stub(os.path.join(t.tmp, "bad", "python"), "bad", t.argv, body=(
            f'#!/bin/bash\n[ "$1" = -c ] && exec "{sys.executable}" "$@"\n'
            'echo "ModuleNotFoundError: No module named chatd (a stand-in)" >&2\nexit 1\n'))
        r = t.sh("start", CHATD_PY=bad)
        assert r.returncode == 1, _out(r)
        assert "FAILED: chatd exited at start" in r.stdout, r.stdout
        assert "last lines of" in r.stdout and "No module named chatd (a stand-in)" in r.stdout, r.stdout
        assert not os.path.exists(os.path.join(t.data, "chatd.pid"))
    finally:
        t.close()


def _forward_capabilities(engine_env: dict) -> tuple[int, dict]:
    """GET /v1/chat-app/capabilities through server/chat_forward.py with the environment the
    engine server has (what start.sh exports)."""
    keep = {k: os.environ.get(k) for k in ("QSE_CHAT_FORWARD_KEY", "QSE_CHAT_URL", "QSE_CHAT_DATA",
                                           "HOME")}
    for k in keep:
        os.environ.pop(k, None)
        if engine_env.get(k):
            os.environ[k] = engine_env[k]

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_GET(self):
            chat_forward.route(self, "GET", None)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
        c.request("GET", "/v1/chat-app/capabilities")
        r = c.getresponse()
        return r.status, json.loads(r.read())
    finally:
        srv.shutdown()
        srv.server_close()
        for k, v in keep.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v


def _engine_env(t: Tree) -> dict:
    """start.sh's environment: serve.env and local.env, then the secrets file, all exported."""
    out = subprocess.run(["bash", "-c", 'set -a; . "$1"; S="${QSE_SECRETS:-}"; [ -f "$S" ] && . "$S"; '
                          'env -0', "-", os.path.join(t.dir, "run", "ops", "serve.env")],
                         capture_output=True, env={"HOME": t.home, "PATH": os.environ["PATH"]},
                         check=True).stdout.decode()
    return dict(kv.split("=", 1) for kv in out.split("\0") if "=" in kv)


def test_both_sides_read_one_secrets_file():
    key = "f" * 64
    # 1. the key in QSE_SECRETS (written with CRLF and quotes: the engine's bash sees the value)
    t = Tree()
    try:
        os.makedirs(os.path.dirname(t.secrets))
        with open(t.secrets, "w") as fh:
            fh.write(f'QSE_METRICS_TOKEN={"m" * 64}\r\nQSE_CHAT_FORWARD_KEY="{key}"\r\n')
        r = t.sh("start")
        assert r.returncode == 0, _out(r)
        assert key not in r.stdout + r.stderr
        env = _engine_env(t)
        assert env["QSE_CHAT_FORWARD_KEY"].strip() == key
        st, obj = _forward_capabilities(env)
        assert st == 200 and "session" in obj, (st, obj)
        assert not os.path.exists(os.path.join(t.data, "forward.key"))
        assert not os.path.exists(os.path.join(t.home, ".qwen38-spark-engine"))
    finally:
        t.close()
    # 2. no secrets file, the key only in chatd's own forward.key
    t = Tree()
    try:
        os.makedirs(t.data)
        with open(os.path.join(t.data, "forward.key"), "w") as fh:
            fh.write(key + "\n")
        assert t.sh("start").returncode == 0
        st, obj = _forward_capabilities(_engine_env(t))
        assert st == 200, (st, obj)
    finally:
        t.close()
    # 3. no key anywhere: chatd makes forward.key and the engine finds it
    t = Tree()
    try:
        assert t.sh("start").returncode == 0
        assert os.path.exists(os.path.join(t.data, "forward.key"))
        st, obj = _forward_capabilities(_engine_env(t))
        assert st == 200, (st, obj)
        # and with chatd stopped, the engine's answer is the named 502 the Chat tab shows
        t.sh("stop")
        st, obj = _forward_capabilities(_engine_env(t))
        assert st == 502 and obj["error"]["type"] == "chat_unavailable", (st, obj)
    finally:
        t.close()
    # 4. the file holds the admin password too (as make-secrets.sh --admin writes it): the engine
    #    reads it, chatd's environment does not
    t = Tree()
    try:
        os.makedirs(os.path.dirname(t.secrets))
        with open(t.secrets, "w") as fh:
            fh.write(f"QSE_ADMIN_TOKEN={'a' * 64}\nQSE_METRICS_TOKEN={'m' * 64}\n"
                     f"QSE_CHAT_FORWARD_KEY={key}\n")
        env = _engine_env(t)
        assert env["QSE_ADMIN_TOKEN"] == "a" * 64 and env["QSE_CHAT_FORWARD_KEY"] == key
        # chatd gets the forward key and never the admin or metrics token
        assert t.sh("start").returncode == 0
        pid = t.pid()
        if os.path.exists(f"/proc/{pid}/environ"):
            environ = open(f"/proc/{pid}/environ", "rb").read().split(b"\0")
            names = {e.split(b"=", 1)[0] for e in environ}
            assert b"QSE_CHAT_FORWARD_KEY" in names and b"QSE_ADMIN_TOKEN" not in names, names
        st, _ = _forward_capabilities(env)
        assert st == 200
    finally:
        t.close()


def test_a_reused_pid_is_not_a_running_chatd():
    t = Tree()
    sleeper = subprocess.Popen(["sleep", "300"])
    try:
        os.makedirs(t.data)
        pidf = os.path.join(t.data, "chatd.pid")
        with open(pidf, "w") as fh:
            fh.write(f"{sleeper.pid}\n")
        assert t.sh("status").returncode == 1
        stop = t.sh("stop")
        assert stop.returncode == 0 and "stale pid file" in stop.stdout, _out(stop)
        assert sleeper.poll() is None, "stop signalled a process that is not chatd"
        with open(pidf, "w") as fh:
            fh.write(f"{sleeper.pid}\n")
        r = t.sh("start")
        assert r.returncode == 0 and "already running" not in r.stdout, _out(r)
        assert t.pid() not in (None, sleeper.pid) and t.health()["service"] == "chatd"
        assert sleeper.poll() is None
    finally:
        sleeper.kill()
        sleeper.wait()
        t.close()


def test_the_port_follows_qse_chat_url():
    t = Tree(serve_env=False)
    try:
        port = _free_port()
        r = t.sh("start", QSE_CHAT_URL=f"http://127.0.0.1:{port}")
        assert r.returncode == 0, _out(r)
        assert f"http://127.0.0.1:{port}" in r.stdout and t.health(port)["service"] == "chatd"
        assert f"--port {port}" in t.ran(), t.ran()
        st = t.sh("status", QSE_CHAT_URL=f"http://127.0.0.1:{port}")
        assert st.returncode == 0 and '"service": "chatd"' in st.stdout, _out(st)
    finally:
        t.close()


def test_a_taken_port_status_and_restart():
    t = Tree()
    holder = socket.socket()
    holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    holder.bind(("127.0.0.1", t.port))
    holder.listen(1)
    stop = threading.Event()

    def answer():          # an HTTP server that is not chatd
        holder.settimeout(0.2)
        while not stop.is_set():
            try:
                c, _ = holder.accept()
            except OSError:
                continue
            c.recv(4096)
            c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nhi")
            c.close()
    th = threading.Thread(target=answer, daemon=True)
    th.start()
    try:
        r = t.sh("start")
        assert r.returncode == 1 and f"port {t.port} is taken by another program" in r.stdout, _out(r)
        assert t.pid() is None
    finally:
        stop.set()
        th.join()
        holder.close()
    try:
        assert t.sh("start").returncode == 0
        first = t.pid()
        r = t.sh("restart")
        assert r.returncode == 0 and "stopped" in r.stdout and "started with" in r.stdout, _out(r)
        assert t.pid() not in (None, first)
    finally:
        t.close()


def test_the_unit_file_matches_the_installer_layout():
    unit = open(os.path.join(ROOT, "ops", "chatd.service")).read()
    assert "qwen38-spark-engine" not in unit and "/usr/bin/python3" not in unit, unit
    lines = dict(ln.split("=", 1) for ln in unit.splitlines() if "=" in ln and not ln.startswith("#"))
    # `-m chatd` from TandemApp's tree, src/app (SRV-747); the log where chatd.sh writes it (TOP/logs)
    assert lines["WorkingDirectory"] == "%h/TandemLLM/src/app"
    assert lines["Environment"] == "PYTHONPATH=%h/TandemLLM/src/app"
    assert lines["ExecStart"].startswith("%h/TandemLLM/venv/bin/python -u -m chatd ")
    assert lines["EnvironmentFile"] == "-%h/TandemLLM/state/secrets.env"
    assert lines["StandardOutput"] == "append:%h/TandemLLM/src/logs/chatd.log"
    assert lines["StandardError"] == "append:%h/TandemLLM/src/logs/chatd.log"
    assert "cp ~/TandemLLM/src/app/ops/chatd.service ~/.config/systemd/user/" in unit


def test_run_logs_and_the_venv_follow_the_tree():
    for layout in ("install", "checkout", "app"):
        t = Tree(serve_env=False, layout=layout)
        try:
            # run/chatd.env of the install, else of TOP; its data folder shows that it was read
            run = os.path.join(t.dir if layout == "install" else t.top, "run")
            t.data = os.path.join(t.tmp, "data-" + layout)          # close() finds the pid file there
            os.makedirs(run, exist_ok=True)
            with open(os.path.join(run, "chatd.env"), "w") as fh:
                fh.write(f"QSE_CHAT_DATA={t.data}\n")
            r = t.sh("start")
            assert r.returncode == 0 and f"[chatd] started with {t.venv} (" in r.stdout, (layout, _out(r))
            st = t.sh("status")
            assert st.returncode == 0 and f"data {t.data}" in st.stdout, (layout, _out(st))
            if os.path.isdir("/proc"):                                # its cwd: TandemApp's tree
                assert f", tree {os.path.realpath(t.app)}" in st.stdout, (layout, st.stdout)
            assert os.path.isfile(os.path.join(t.top, "logs", "chatd.log")), layout
            if t.app != t.top:                                        # nothing written into app/
                assert not os.path.exists(os.path.join(t.app, "logs")), layout
                assert not os.path.exists(os.path.join(t.app, "run")), layout
            stop = t.sh("stop")
            assert stop.returncode == 0 and t.pid() is None, (layout, _out(stop))
        finally:
            t.close()



def test_stop_this_tree_only():
    if not os.path.isdir("/proc/self"):
        print("    (skipped: the tree check reads /proc -- run it on the box)")
        return "skipped off Linux"
    d = tempfile.mkdtemp(prefix="chatd-this-tree-")
    data, other = os.path.join(d, "data"), os.path.join(d, "other")
    os.makedirs(data)
    os.makedirs(other)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": d, "REPO": d, "QSE_CHAT_DATA": data,
           "QSE_CHATD_ENV": os.path.join(d, "none.env"), "QSE_CHAT_URL": "http://127.0.0.1:9"}
    procs = []
    try:
        for cwd, kept in ((other, True), (ROOT, False)):
            p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "-m", "chatd"], cwd=cwd)
            procs.append(p)
            with open(os.path.join(data, "chatd.pid"), "w") as fh:
                fh.write(f"{p.pid}\n")
            r = subprocess.run(["bash", os.path.join(ROOT, "ops", "chatd.sh"), "stop", "--this-tree"], env=env,
                               capture_output=True, text=True, timeout=60)
            assert r.returncode == 0, r.stdout + r.stderr
            if kept:
                assert p.poll() is None and "another tree's chatd" in r.stdout, r.stdout
            else:
                assert p.wait(timeout=30) is not None and "[chatd] stopped" in r.stdout, r.stdout
    finally:
        for p in procs:
            p.kill()
        shutil.rmtree(d, ignore_errors=True)
    return "another tree's chatd stays; this tree's stops"


def test_the_script_parses():
    r = subprocess.run(["bash", "-n", os.path.join(ROOT, "ops", "chatd.sh")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


if __name__ == "__main__":
    run_all(globals())
