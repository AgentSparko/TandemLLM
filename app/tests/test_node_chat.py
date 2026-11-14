"""SRV-823 / SRV-824 (ENG-413 WP6): TandemNode owns chatd on an engine box (proxy/nodechat.py).

The engine is a fake on a free loopback port that answers /health with the mode and engine API a
test sets; the box's own engine, chatd and agent are never addressed. Test 1 runs TandemApp's real
ops/chatd.sh and a real chatd on a free port with a data folder of its own; the others run a
stand-in launcher that keeps its state in a file and writes every call to the markers. Time is
injected where the hour counts. The engine's side of the switch (ops/start.sh, ops/watchdog.sh and
bin/tandem with QSE_CHATD=node) is the engine's tests/test_chatd_lifecycle.py.

The test matrix, the Gherkin of SRV-823 (the rewrite of the engine's tests/test_engine_only.py case
3 and tests/test_chatd_lifecycle.py's watchdog cases against TandemNode):

  1  hand verbs on a real chatd under a test agent: start, status, restart, a crash (SIGKILL)
     started again by the watcher, stop that holds the watcher until a start; busy; chatd runs
     with the engine's environment and the forward key, never the admin token; its log is the
     node's; nothing is left running                               test_hand_verbs_on_a_real_chatd
  2  the watchdog's rule: nothing while the engine does not answer 200; a dead chatd started, a
     running one clears the count, 3 starts an hour then one "giving up" line, the next hour
     starts again; the log lines are the watchdog's            test_the_watchdog_rule
  3  setup mode (the marker, /health mode setup) and a hold's pause file: nothing; a pause file
     older than 2,400 s is stale                                  test_setup_mode_and_the_pause_file
  4  engine mode starts none; the engine seen coming up in engine mode stops a running chatd
     once; the first look after the agent starts stops nothing   test_engine_mode
  5  one owner: the engine's QSE_CHATD on (or unset) and node on an engine before 1.6 start
     nothing and say why once an hour; node on 1.6, off, and a box without an engine env start
     it                                                           test_one_owner
  6  an engine start this agent ran wakes the watcher: chatd starts at once, outside the count
                                                                  test_the_engine_start_kicks
  7  not this node's (an engine box without TandemNode's chat): every verb 409 not_owned, the
     watcher does nothing, the view names the engine as the owner
                                                                  test_not_owned
  8  the launcher's call: the engine's env file, then the secrets it names, chat.json's url and
     data over the engine's, the engine tree's run/chatd.env, the node's log; a start in a scope
     of its own under systemd only; chat.json's defaults and its refusals
                                                                  test_the_launcher_call
  9  `tandem-node chat`: status, start, stop and logs without an agent and through one; `unit
     --chat on`                                                   test_the_cli

CPU only, standard library only. Run: python tests/test_node_chat.py
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_node_agent as tna  # noqa: E402
from ports import free_port  # noqa: E402
from proxy import nodechat, nodeengines  # noqa: E402
from proxy_util import run  # noqa: E402

T0 = 1_800_000_000.0                      # a fixed clock: 2027-01-15


class FakeEngine:
    """/health as a test says: code, mode and engine API."""

    def __init__(self, mode="chat", api="1.6", code=200):
        self.code, self.doc = code, {"status": "ok", "mode": mode, "engine_api": api}
        me = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                raw = json.dumps(me.doc).encode()
                self.send_response(me.code if self.path == "/health" else 404)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        self.port = free_port()
        assert self.port not in (8000, 8016, 7700)
        self.srv = ThreadingHTTPServer(("127.0.0.1", self.port), H)
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05},
                         daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


# the stand-in launcher: its state in $BOX/up, a start fails while $BOX/fail exists; every call
# in $BOX/marks; a start writes the environment it got (names and the marked values) to $BOX/env
STANDIN = r"""#!/bin/bash
echo "chatd.sh $1" >> "$BOX/marks"
case "$1" in
  start) env | grep -E '^(SANDBOX_MARK|QSE_CHAT_FORWARD_KEY|QSE_ADMIN_TOKEN|QSE_CHAT_LOG|QSE_CHATD_ENV|QSE_CHAT_URL|QSE_CHAT_DATA|CHATD_PY|PORT)=' | sort > "$BOX/env"
         [ -f "$BOX/fail" ] && { echo "[chatd] FAILED: port 8016 is taken"; exit 1; }
         touch "$BOX/up"; echo "[chatd] started" ;;
  stop) rm -f "$BOX/up"; echo "[chatd] stopped" ;;
  restart) touch "$BOX/up"; echo "[chatd] stopped"; echo "[chatd] started" ;;
  status) if [ -f "$BOX/up" ]; then echo "[chatd] running: pid 4242, http://127.0.0.1:18016, chatd 0.1.0"
            echo "[chatd] python /usr/bin/python3 (3.12.3), data $BOX/data, tree $BOX"; exit 0; fi
          echo "[chatd] not running (http://127.0.0.1:18016, data $BOX/data)"; exit 1 ;;
esac
"""


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


def _write(path: str, text: str, mode: int = 0o644) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)
    os.chmod(path, mode)
    return path


def box(engine_port: int, chatd: str = "node", extra: str = "", chat_port: int | None = None) -> str:
    """An engine checkout's files as chatd needs them: ops/serve.env (with its local.env),
    secrets.env, run/chatd.env; the stand-in launcher."""
    d = tempfile.mkdtemp(prefix="node-chat-")
    repo = os.path.join(d, "repo")
    _write(os.path.join(d, "secrets.env"),
           "QSE_ADMIN_TOKEN=admin-not-for-chatd\nQSE_CHAT_FORWARD_KEY=" + "f" * 40 + "\n", 0o600)
    lines = [f"REPO={repo}", f"PORT={engine_port}", f"QSE_SECRETS={d}/secrets.env",
             "SANDBOX_MARK=from-serve-env", f"PY={sys.executable}"]
    if chatd is not None:
        lines.append(f"QSE_CHATD={chatd}")
    if chat_port:
        lines += [f"QSE_CHAT_URL=http://127.0.0.1:{chat_port}", f"QSE_CHAT_DATA={d}/data"]
    lines.append(f'if [ -f "{repo}/run/local.env" ]; then . "{repo}/run/local.env"; fi')
    _write(os.path.join(repo, "ops", "serve.env"), "\n".join(lines) + "\n" + extra)
    _write(os.path.join(d, "chatd.sh"), STANDIN, 0o755)
    os.makedirs(os.path.join(d, "data"))
    return d


def chat_on(d: str, engine_port: int, *, cfg: dict | None = None, clock=None, log=None,
            env=None, launcher: str | None = None, engines=None) -> nodechat.Chat:
    state = os.path.join(d, "node")
    os.makedirs(state, exist_ok=True)
    doc = {"own": True, "serve_env": os.path.join(d, "repo", "ops", "serve.env"),
           "launcher": launcher or os.path.join(d, "chatd.sh"), "scope": "off"}
    doc.update(cfg or {})
    _write(os.path.join(state, "chat.json"), json.dumps({k: v for k, v in doc.items() if v is not None}))
    if engines is None:
        engines = nodeengines.Engines([nodeengines.Engine({"id": "engine", "port": engine_port},
                                                          os.path.join(state, "logs"))], poll_s=0.05)
    base = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": d, "BOX": d}
    return nodechat.Chat.load(state, None, engines, log=log if log is not None else (lambda m: None),
                              clock=clock or Clock(), env=dict(base, **(env or {})))


def marks(d: str) -> list[str]:
    p = os.path.join(d, "marks")
    return open(p).read().split("\n")[:-1] if os.path.exists(p) else []


def starts(d: str) -> int:
    return sum(1 for m in marks(d) if m == "chatd.sh start")


def _until(cond, seconds: float = 20.0, what: str = ""):
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        got = cond()
        if got:
            return got
        time.sleep(0.05)
    raise AssertionError(f"not within {seconds} s: {what}")


# ---------------------------------------------------------------------------- 1 a real chatd

def _chat_health(port: int) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
            return json.load(r)
    except OSError:
        return None


def test_hand_verbs_on_a_real_chatd():
    eng = FakeEngine()
    cport = free_port()
    assert cport not in (8016, 8000, 7700)
    d = box(eng.port, chat_port=cport)
    buf: list[str] = []
    chat = chat_on(d, eng.port, launcher=os.path.join(ROOT, "ops", "chatd.sh"), log=buf.append,
                   clock=time.time)
    agent = tna.agent_on(engines=[], buf=buf)
    agent.chat = chat
    pids: list[int] = []
    try:
        with tna.Running(agent) as r:
            c = r.client

            def view():
                st, doc = c.json("GET", "/node/chat")
                assert st == 200, doc
                return doc

            def done(job):
                return _until(lambda: (lambda v: v if (v["job"] or {}).get("id") == job["id"]
                                       and v["job"]["state"] != "running" else None)(view()),
                              60, f"job {job}")
            v = view()
            assert (v["own"], v["owner"], v["state"], v["url"]) == \
                (True, "node", "stopped", f"http://127.0.0.1:{cport}"), v
            assert v["hands_over"] is True and v["engine_chatd"] == "node", v
            st, job = c.json("POST", "/node/chat/start")
            assert st == 202 and job["who"] == "secret", job
            v = done(job)
            assert v["job"]["state"] == "done" and v["state"] == "running" and v["pid"], v
            assert v["data"] == os.path.join(d, "data") and (_chat_health(cport) or {}).get("service") == "chatd"
            pids.append(v["pid"])
            # the engine's environment and the forward key; never the admin token (Linux: /proc)
            if os.path.isdir(f"/proc/{v['pid']}"):
                env = open(f"/proc/{v['pid']}/environ", "rb").read().split(b"\0")
                assert b"SANDBOX_MARK=from-serve-env" in env, "serve.env reached chatd"
                assert b"QSE_CHAT_FORWARD_KEY=" + b"f" * 40 in env
                assert not [e for e in env if e.startswith(b"QSE_ADMIN_TOKEN=")], "the admin token"
            nlog = os.path.join(d, "node", "logs", "chatd.log")
            assert os.path.isfile(nlog) and os.path.getsize(nlog) > 0, "chatd's log is the node's"
            # busy: one action at a time
            chat._run.acquire()
            try:
                st, held = c.json("POST", "/node/chat/restart")
                assert st == 202
                st, err = c.json("POST", "/node/chat/stop")
                assert st == 409 and err["error"]["type"] == "busy", err
            finally:
                chat._run.release()
            v = done(held)
            assert v["state"] == "running" and v["pid"] and v["pid"] != pids[-1], v
            pids.append(v["pid"])
            # a crash: the watcher starts it again (1/3)
            os.kill(v["pid"], signal.SIGKILL)
            _until(lambda: _chat_health(cport) is None, 10, "the killed chatd gone")
            assert chat.tick() == "started", buf[-3:]
            assert any("chatd was not running; started it (1/3 this hour)" in m for m in buf), buf
            v = view()
            assert v["state"] == "running" and v["pid"] not in pids, v
            pids.append(v["pid"])
            assert chat.tick() == "running" and view()["watch"]["starts"] == 0
            # a hand stop holds the watcher until a hand start
            st, job = c.json("POST", "/node/chat/stop")
            v = done(job)
            assert v["state"] == "stopped" and v["watch"]["stopped_by_hand"], v
            assert chat.tick() == "stopped_by_hand" and _chat_health(cport) is None
            v = done(c.json("POST", "/node/chat/start")[1])
            assert v["state"] == "running" and not v["watch"]["stopped_by_hand"], v
            pids.append(v["pid"])
            assert chat.tick() == "running"
            v = done(c.json("POST", "/node/chat/stop")[1])
            assert v["state"] == "stopped"
            assert c.json("POST", "/node/chat/nope")[0] == 404
            assert any(m.startswith("[node] chat start by secret") for m in buf), buf
    finally:
        for p in pids:
            try:
                os.kill(p, signal.SIGKILL)
            except ProcessLookupError:
                pass
        eng.close()
    assert _chat_health(cport) is None
    shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------- 2 the rule

def test_the_watchdog_rule():
    eng = FakeEngine()
    d = box(eng.port)
    clock, buf = Clock(), []
    chat = chat_on(d, eng.port, clock=clock, log=buf.append)
    try:
        # an engine that does not answer 200 is the engine's business
        eng.code = 503
        assert chat.tick() == "engine_down" and marks(d) == []
        eng.code = 200
        assert chat.tick() == "started" and marks(d) == ["chatd.sh status", "chatd.sh start"]
        assert buf[-1] == "[node] chatd was not running; started it (1/3 this hour)", buf
        assert chat.tick() == "running" and chat._watch().get("hour") is None
        # a chatd that does not stay up: 3 starts this hour, then one line
        open(os.path.join(d, "fail"), "w").close()
        os.remove(os.path.join(d, "up"))
        got = [chat.tick() for _ in range(5)]
        assert got == ["failed", "failed", "failed", "gave_up", "gave_up"], got
        assert starts(d) == 1 + 3, marks(d)
        log = "\n".join(buf)
        assert log.count("did not start (") == 3 and log.count("giving up until the next hour") == 1, log
        assert "[node] chatd was not running and did not start (3/3 this hour); log: " in log
        assert chat.view()["watch"]["gave_up"] is True
        # the next hour counts again
        clock.t += 3600
        assert chat.tick() == "failed" and starts(d) == 5
        assert buf[-1].startswith("[node] chatd was not running and did not start (1/3 this hour)"), buf
        os.remove(os.path.join(d, "fail"))
        assert chat.tick() == "started" and buf[-1].endswith("(2/3 this hour)")
        assert chat.tick() == "running" and chat.view()["watch"]["starts"] == 0
        # the engine down again: nothing
        eng.close()
        eng = None
        n = len(marks(d))
        assert chat.tick() == "engine_down" and len(marks(d)) == n
    finally:
        if eng is not None:
            eng.close()
    shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------- 3 setup, pause

def test_setup_mode_and_the_pause_file():
    eng = FakeEngine()
    d = box(eng.port)
    clock = Clock()
    chat = chat_on(d, eng.port, clock=clock)
    try:
        marker = _write(os.path.join(d, "repo", "run", "setup-pending"), "HOST=127.0.0.1\n")
        assert chat.tick() == "setup"
        os.remove(marker)
        eng.doc["mode"] = "setup"
        assert chat.tick() == "setup"
        eng.doc["mode"] = "chat"
        assert marks(d) == []
        pause = _write(os.path.join(d, "repo", ".watchdog.off"), "hold\n")
        os.utime(pause, (clock.t - 10, clock.t - 10))
        assert chat.tick() == "paused" and marks(d) == []
        os.utime(pause, (clock.t - 2401, clock.t - 2401))             # older than the longest hold
        assert chat.tick() == "started"
    finally:
        eng.close()
    shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------- 4 engine mode

def test_engine_mode():
    eng = FakeEngine(mode="engine")
    d = box(eng.port)
    buf: list[str] = []
    chat = chat_on(d, eng.port, log=buf.append)
    try:
        assert chat.tick() == "engine_only" and marks(d) == []          # starts none
        open(os.path.join(d, "up"), "w").close()                        # a chatd runs
        # the first look after the agent starts is no switch: nothing stopped
        chat = chat_on(d, eng.port, log=buf.append)
        assert chat.tick() == "engine_only" and os.path.exists(os.path.join(d, "up"))
        # the engine stops and comes back in engine mode: the running chatd stops, once
        eng.code = 502
        assert chat.tick() == "engine_down"
        eng.code = 200
        assert chat.tick() == "stopped_engine_only" and not os.path.exists(os.path.join(d, "up"))
        assert buf[-1] == "[node] chat: the engine runs engine only (QSE_MODE=engine): chatd stopped", buf
        assert chat.tick() == "engine_only" and marks(d)[-1] == "chatd.sh stop"
        # a switch from chat mode while it runs: stopped too
        eng.doc["mode"] = "chat"
        assert chat.tick() == "started"
        eng.doc["mode"] = "engine"
        assert chat.tick() == "stopped_engine_only"
        assert not chat.view()["watch"]["stopped_by_hand"]            # the watcher's, not a hand's
    finally:
        eng.close()
    shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------- 5 one owner

def test_one_owner():
    eng = FakeEngine()
    try:
        for chatd, api, want in (("on", "1.6", "two_owners"), (None, "1.6", "two_owners"),
                                 ("node", "1.5", "two_owners"), ("node", None, "two_owners"),
                                 ("node", "1.6", "started"), ("node", "1.7", "started"),
                                 ("off", "1.0", "started")):
            eng.doc["engine_api"] = api
            d = box(eng.port, chatd=chatd)
            buf: list[str] = []
            chat = chat_on(d, eng.port, log=buf.append)
            got = [chat.tick(), chat.tick()]
            if want == "two_owners":
                assert got == [want, want] and starts(d) == 0, (chatd, api, got, marks(d))
                said = [m for m in buf if "not started here" in m]
                assert len(said) == 1, (chatd, api, buf)                  # once an hour
                if chatd == "node":
                    assert "does not know QSE_CHATD=node" in said[0] and "1.6" in said[0], said
                else:
                    assert "QSE_CHATD=on" in said[0] and "QSE_CHATD=node" in said[0], said
                v = chat.view()
                assert (v["owner"], v["hands_over"]) == ("both", False) and v["why"], v
                # a hand verb still acts
                assert chat.run_action("start")[0] == 0 and starts(d) == 1
            else:
                assert got == ["started", "running"], (chatd, api, got)
                assert chat.view()["owner"] == "node"
            shutil.rmtree(d, ignore_errors=True)
        # a box with no engine env file (TandemApp alone beside an engine): this interpreter
        d = box(eng.port)
        chat = chat_on(d, eng.port, cfg={"serve_env": None})
        assert chat.serve_env is None and chat.python == sys.executable
        assert chat.tick() == "started"
        env = open(os.path.join(d, "env")).read()
        assert f"CHATD_PY={sys.executable}" in env and "SANDBOX_MARK" not in env, env
        shutil.rmtree(d, ignore_errors=True)
    finally:
        eng.close()


# ---------------------------------------------------------------------------- 6 the kick

def test_the_engine_start_kicks():
    port = free_port()
    d = box(port, chatd="off")                    # the fake engine says no engine API: off hands over
    me = os.path.join(ROOT, "tests", "test_node_agent.py")
    buf: list[str] = []
    # the agent's own wiring: its engines' job end kicks whatever chat it has
    agent = tna.agent_on(engines=[tna.engine_doc(
        "engine", port, start=[sys.executable, me, "--fake-engine", str(port)], start_timeout_s=20)],
        buf=buf)
    chat = chat_on(d, port, engines=agent.engines, log=buf.append, clock=time.time)
    chat.every_s = 3600.0                         # the minute never comes: only the kick wakes it
    agent.chat = chat
    engines = agent.engines
    try:
        with tna.Running(agent) as r:
            chat.start_watcher()
            _until(lambda: chat._seen is not None, 10, "the first look")
            assert marks(d) == [], marks(d)                             # the engine was down
            st, job = r.client.json("POST", "/node/engines/engine/start")
            assert st == 202
            _until(lambda: "chatd.sh start" in marks(d), 30, f"chatd started after the engine {buf}")
            assert any("[node] chat: the engine answers; chatd started" in m for m in buf), buf
            w = chat.view()["watch"]
            assert w["starts"] == 0 and w["hour"] is None, w          # outside the count
            chat.close()
            for e in engines.list:
                if e.proc is not None and e.proc.poll() is None:
                    nodeengines._end_group(e.proc, 2.0)
    finally:
        chat.close()
    shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------- 7 not owned

def test_not_owned():
    eng = FakeEngine()
    d = box(eng.port, chatd="on")
    buf: list[str] = []
    chat = chat_on(d, eng.port, cfg={"own": False}, log=buf.append)
    agent = tna.agent_on(engines=[], buf=buf)
    agent.chat = chat
    try:
        assert chat.tick() == "not_owned" and marks(d) == []
        chat.start_watcher()
        assert chat._thread is None                                    # no watcher at all
        with tna.Running(agent) as r:
            for act in nodechat.ACTIONS:
                st, err = r.client.json("POST", f"/node/chat/{act}")
                assert st == 409 and err["error"]["type"] == "not_owned", err
                assert "QSE_CHATD=node" in err["error"]["message"]
            st, v = r.client.json("GET", "/node/chat")
            assert st == 200 and (v["own"], v["owner"], v["state"]) == (False, "engine", "stopped"), v
            assert v["hands_over"] is None and v["engine_chatd"] is None, v
        assert [m for m in marks(d) if m != "chatd.sh status"] == [], marks(d)
        # the agent's own default: not owned without --chat on or chat.json
        a = nodechat.Chat.load(tempfile.mkdtemp(prefix="node-chat-none-"))
        assert a.own is False and a.launcher == os.path.join(ROOT, "ops", "chatd.sh")
    finally:
        eng.close()
    shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------- 8 the call

def test_the_launcher_call():
    eng = FakeEngine()
    d = box(eng.port, extra="QSE_CHAT_URL=http://127.0.0.1:1\n")
    try:
        chat = chat_on(d, eng.port, cfg={"url": "http://127.0.0.1:18016", "data": os.path.join(d, "data")})
        assert chat.repo == os.path.join(d, "repo"), chat.repo         # above serve.env's ops/
        assert chat.chat_env == os.path.join(d, "repo", "run", "chatd.env")
        assert chat.python is None                                     # chatd.sh's order: PY first
        assert chat.run_action("start")[0] == 0
        env = dict(ln.split("=", 1) for ln in open(os.path.join(d, "env")).read().splitlines())
        assert env["SANDBOX_MARK"] == "from-serve-env" and env["PORT"] == str(eng.port), env
        assert env["QSE_CHAT_FORWARD_KEY"] == "f" * 40               # the secrets serve.env names
        assert env["QSE_ADMIN_TOKEN"]                                  # chatd.sh holds it back
        assert env["QSE_CHAT_URL"] == "http://127.0.0.1:18016", env    # chat.json over serve.env
        assert env["QSE_CHAT_DATA"] == os.path.join(d, "data")
        assert env["QSE_CHAT_LOG"] == os.path.join(d, "node", "logs", "chatd.log")
        assert env["QSE_CHATD_ENV"] == os.path.join(d, "repo", "run", "chatd.env")
        assert "CHATD_PY" not in env
        # a scope of its own under systemd only, for a start or restart
        sd = {"INVOCATION_ID": "x", "XDG_RUNTIME_DIR": "/run/user/1000"}
        found = {"which": lambda n: f"/usr/bin/{n}"}
        for env_, which, scope, want in ((sd, found["which"], "auto", True),
                                         (sd, lambda n: None, "auto", False),
                                         ({"XDG_RUNTIME_DIR": "/run/user/1000"}, found["which"], "auto", False),
                                         (sd, found["which"], "off", False)):
            c = chat_on(d, eng.port, cfg={"scope": scope}, env=env_)
            c.which = which
            for verb in ("start", "restart", "stop", "status"):
                argv = c.argv(verb)
                scoped = argv[:4] == ["systemd-run", "--user", "--scope", "--quiet"]
                assert scoped == (want and verb in ("start", "restart")), (env_, scope, verb, argv)
                assert argv[-2:] == [c.launcher, verb]
                if scoped:
                    assert argv[5].startswith("--unit=tandem-chatd-") and argv[6] == "--", argv
        # an install's layout (--tandem-dir) and chat.json's refusals
        td = tempfile.mkdtemp(prefix="node-chat-td-")
        c = nodechat.Chat.load(os.path.join(td, "node"), td)
        assert (c.serve_env, c.repo, c.chat_env) == (os.path.join(td, "run", "ops", "serve.env"),
                                                     os.path.join(td, "src"),
                                                     os.path.join(td, "run", "chatd.env"))
        _write(os.path.join(td, "node", "chat.json"), json.dumps({"own": True, "port": 1}))
        try:
            nodechat.Chat.load(os.path.join(td, "node"), td)
            raise AssertionError("an unknown key")
        except ValueError as exc:
            assert "unknown keys port" in str(exc)
        _write(os.path.join(td, "node", "chat.json"), json.dumps({"scope": "maybe"}))
        try:
            nodechat.Chat.load(os.path.join(td, "node"), td)
            raise AssertionError("a bad scope")
        except ValueError as exc:
            assert "scope" in str(exc)
        # no launcher: refused plainly
        c = chat_on(d, eng.port, cfg={"launcher": os.path.join(d, "none.sh")})
        try:
            c.run_action("start")
            raise AssertionError("no launcher")
        except nodechat.ChatError as exc:
            assert exc.code == "no_launcher" and exc.status == 409
        assert c.view()["state"] == "stopped"
        said: list[str] = []
        c.log = said.append
        assert c.tick() == "no_launcher" and c.tick() == "no_launcher"
        assert len(said) == 1 and "chatd launcher" in said[0] and "once an hour" in said[0], said
        assert nodechat.api_at_least("1.6") and nodechat.api_at_least("1.10") and \
            nodechat.api_at_least("2.0") and not nodechat.api_at_least("1.5") and \
            not nodechat.api_at_least(None) and not nodechat.api_at_least("x")
        shutil.rmtree(td, ignore_errors=True)
    finally:
        eng.close()
    shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------- 9 the CLI

def _cli(state: str, *args, env=None, port=None) -> subprocess.CompletedProcess:
    argv = [sys.executable, "-m", "proxy.nodeagent", *args, "--state-dir", state,
            "--port", str(port or free_port())]
    return subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=120,
                          env=dict(env or os.environ, PYTHONPATH=ROOT))


def test_the_cli():
    eng = FakeEngine()
    d = box(eng.port)
    state = os.path.join(d, "node")
    chat_on(d, eng.port)                                  # writes chat.json
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": d, "BOX": d}
    try:
        r = _cli(state, "chat", "status", env=env)
        assert r.returncode == 1 and "[chat] chatd: this node's" in r.stdout, r.stdout + r.stderr
        assert "[chatd] not running" in r.stdout and "does not answer" in r.stdout, r.stdout
        r = _cli(state, "chat", "start", env=env)
        assert r.returncode == 0 and "acting here" in r.stdout and "start: done" in r.stdout, r.stdout + r.stderr
        r = _cli(state, "chat", "status", "--json", env=env)
        doc = json.loads(r.stdout)
        assert r.returncode == 0 and doc["state"] == "running" and doc["pid"] == 4242, doc
        _write(os.path.join(state, "logs", "chatd.log"), "one\ntwo\nthree\n")
        r = _cli(state, "chat", "logs", "2", "--no-follow", env=env)
        assert r.returncode == 0 and r.stdout == "two\nthree\n", r.stdout + r.stderr
        r = _cli(state, "chat", "stop", env=env)
        assert r.returncode == 0 and "stop: done" in r.stdout
        # not this node's: refused, said so
        _write(os.path.join(state, "chat.json"), json.dumps({"launcher": os.path.join(d, "chatd.sh")}))
        r = _cli(state, "chat", "start", env=env)
        assert r.returncode == 4 and "not this node's" in r.stderr, r.stdout + r.stderr
        r = _cli(state, "chat", "status", env=env)
        assert "[chat] chatd: the engine's" in r.stdout, r.stdout
        r = _cli(state, "unit", "--chat", "on", env=env)
        assert r.returncode == 0 and " --chat on" in r.stdout and "serve " in r.stdout, r.stdout
        assert " --chat " not in _cli(state, "unit", env=env).stdout
        # through a running agent: its view, its job
        chat = chat_on(d, eng.port)
        agent = tna.agent_on(engines=[], state=state)
        agent.chat = chat
        with tna.Running(agent) as run_:
            r = _cli(state, "chat", "start", env=env, port=run_.port)
            assert r.returncode == 0 and "acting here" not in r.stdout and "start: done" in r.stdout, \
                r.stdout + r.stderr
            r = _cli(state, "chat", "status", "--json", env=env, port=run_.port)
            doc = json.loads(r.stdout)
            assert "agent" not in doc and doc["state"] == "running" and doc["job"]["action"] == "start", doc
            r = _cli(state, "chat", "stop", env=env, port=run_.port)
            assert r.returncode == 0 and "stop: done" in r.stdout
    finally:
        eng.close()
    shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    run(globals())
