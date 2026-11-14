"""chatd and the engine's launchers (OPS-155, QA OPS-156; ENG-413 WP6 SRV-823; WP7 OPS-272, QA OPS-273).

Since engine API 1.7 (ENG-413 WP7, node/control.json `chat`) the engine runs no chatd: ops/start.sh,
ops/stop.sh, ops/watchdog.sh and the bin/tandem install.sh writes have no launcher of it, in any mode.
QSE_CHATD is node (TandemApp's TandemNode runs chatd; `tandem chat ...` is `tandem-node chat ...`) or
off (nobody does); serve.env reads the older `on` as node and an unset value as off. TandemNode's side
(the watcher with OPS-155's rule, the hand verbs) is TandemApp's tests/test_node_chat.py; the
installs themselves are tests/test_install_headless.py.

ops/start.sh, ops/stop.sh and ops/watchdog.sh run for real in tests/test_ops_scripts.py's sandbox (fake
curl, a fake process table, a fake engine). A stand-in chatd.sh sits where the launchers used to find
one (app/ops and ops/): any call to it is in the markers, and there must be none. bin/tandem is the one
install.sh writes: its `write_control` is taken from install.sh and run against stand-in start.sh,
stop.sh and tandem-node. No process of the box is seen or signalled.

The test matrix (OPS-155's cases, as WP7 leaves them; WP6's node cases; WP7's):

  1  start.sh calls no chatd launcher after /health, nor on a start that finds the engine healthy,
     with a launcher at app/ops and at ops/; it says who runs chatd in one line
                                                         test_start_calls_no_chatd
  2  setup mode, the setup marker and a hold's restart: no chatd call
                                                         test_setup_mode_and_a_hold_call_no_chatd
  3  the line per QSE_CHATD: unset and off (nobody here), on (one notice: read as node), node with
     and without a TandemNode answering on TANDEM_NODE_PORT; the release's template in chat and
     engine mode resolves the same                       test_the_chatd_line_per_value
  4  stop.sh never touches chatd                         test_stop_sh_leaves_chatd_alone
  5  the watchdog never calls chatd, never logs it, never restarts the engine for it, for every
     QSE_CHATD value                                     test_the_watchdog_leaves_chatd_alone
  6  bin/tandem start, restart, stop and status call no chatd launcher; status's chat line per value;
     tandem chat ... and tandem node ... run tandem-node (TANDEM_NODE_BIN, DIR/venv/bin/tandem-node,
     PATH, in that order) with --tandem-dir DIR, and --chat on when this install put TandemApp here
     (state/tandemapp-wheel.json); without one both exit 2 and say where it comes from
                                                         test_bin_tandem_and_tandem_node
  7  the systemd units: tandemllm.service runs tandem start and stop and no chatd; --uninstall stops
     chatd only through a TandemNode this install wrote (its unit names DIR), then removes that unit
                                                         test_the_units_and_the_uninstall
  8  the install's last message, the setup message and the update note name TandemNode, never a page
     on the engine port or a chatd launcher               test_the_install_says_what_it_did
  9  no launcher or installer names app/: install.sh, the bin/tandem it writes, ops/*.sh, ops/serve.env's
     code and ops/executables.txt (G5 on shell text)      test_no_launcher_names_app

Run: python tests/test_chatd_lifecycle.py
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))

import test_ops_scripts as T  # noqa: E402
from ports import free_port  # noqa: E402

# a chatd.sh where the launchers used to find one: every call goes to the markers
FAKE_CHATD = """#!/bin/bash
echo "chatd.sh $1" >> "$MARKERS"
case "$1" in status) exit 1 ;; esac
"""

ENGINE_ENV = ("SERVED_MODEL=t\nMAX_LEN=64\nDEFAULT_MAX_TOKENS=8\nREASONING_FORMAT=tags\n"
              "REASONING_EFFORT=medium\nLEN_FIXED=0\nLEN_LATCH=1\nBUDGET=16\nCORPUS=x\n"
              "CKPT8=x\nCKPT16=x\nNV=x\nHEAD=x\nCACHE_GB=0\nREQUEST_TIMEOUT=1\n"
              "MAX_QUEUE=1\nQUEUE_TIMEOUT=1\nFUSE_PROJ=0\nQWEN38_DF2_TREE_MODE=paths\n"
              "QWEN38_TREE_ALIAS_STATE=0\n")
#: where a checkout from before engine API 1.7 kept chatd's launcher
LAUNCHERS = (("app", "ops", "chatd.sh"), ("ops", "chatd.sh"))


def _write(path: str, text: str, mode: int = 0o755) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)
    os.chmod(path, mode)


def _chat_box(scripts: list[str]) -> str:
    """tests/test_ops_scripts.py's box with a stand-in chatd.sh where each old layout kept one."""
    repo = T._box(scripts)
    for where in LAUNCHERS:
        _write(os.path.join(repo, *where), FAKE_CHATD)
    if not shutil.which("setsid"):                 # macOS has none; start.sh launches with it
        _write(os.path.join(repo, "bin", "setsid"), '#!/bin/bash\nexec "$@"\n')
    return repo


def _marks(repo: str) -> list[str]:
    p = os.path.join(repo, "markers")
    return open(p).read().splitlines() if os.path.exists(p) else []


def _chat_calls(repo: str) -> list[str]:
    return [m for m in _marks(repo) if m.startswith("chatd.sh")]


def _said(out: str) -> list[str]:
    return [ln for ln in out.splitlines() if "chatd" in ln and not ln.startswith("[start] mode: ")]


def _start(repo: str, *args, extra: str = "", **env) -> subprocess.CompletedProcess:
    """start.sh with a fake engine that answers /health once it runs."""
    engine = os.path.join(repo, "fake-engine.sh")
    _write(engine, f"#!/bin/bash\necho \"engine $*\" >> {repo}/markers\ntouch {repo}/up\nsleep 30\n")
    env_file = os.path.join(repo, "ops", "serve.env")
    body = open(env_file).read()
    with open(env_file, "w") as fh:
        fh.write(body.replace("PY=/bin/false", f"PY={engine}").replace("PORT=8000", "PORT=18997")
                 + ENGINE_ENV + extra)
    _write(os.path.join(repo, "bin", "curl"), f"#!/bin/bash\n[ -f {repo}/up ] || exit 22\nexit 0\n")
    env.setdefault("TANDEM_NODE_PORT", str(_quiet_port()))
    try:
        return subprocess.run(["bash", os.path.join(repo, "ops", "start.sh"), *args],
                              capture_output=True, text=True, env=T._env(repo, **env), timeout=120)
    finally:
        subprocess.run(["pkill", "-f", engine])


def _quiet_port() -> int:
    p = free_port()
    assert p != 7700                                 # never the box's own TandemNode
    return p


def test_start_calls_no_chatd():
    repo = _chat_box(["start.sh"])
    r = _start(repo)
    assert r.returncode == 0 and "healthy after" in r.stdout, r.stdout + r.stderr
    assert _chat_calls(repo) == [], _marks(repo)
    assert _said(r.stdout) == ["[start] chatd: off (QSE_CHATD=off: no chatd here; QSE_CHATD=node gives it "
                               "to TandemNode)"], r.stdout
    # a start that finds the engine healthy: the line again, no call
    os.remove(os.path.join(repo, "markers"))
    r = T._run(repo, "start.sh", FAKE_HEALTHY="1", TANDEM_NODE_PORT=str(_quiet_port()))
    assert r.returncode == 0 and "already healthy" in r.stdout, r.stdout + r.stderr
    assert _chat_calls(repo) == [] and len(_said(r.stdout)) == 1, (_marks(repo), r.stdout)
    assert "chatd.sh" not in open(os.path.join(ROOT, "ops", "start.sh")).read()


def test_setup_mode_and_a_hold_call_no_chatd():
    repo = _chat_box(["start.sh"])
    r = _start(repo, "--setup")
    assert r.returncode == 0 and "setup mode answers" in r.stdout, r.stdout + r.stderr
    assert "/dashboard" not in r.stdout and "TandemApp's Setup page" in r.stdout, r.stdout
    assert _chat_calls(repo) == [] and _said(r.stdout) == [], r.stdout
    os.makedirs(os.path.join(repo, "run"), exist_ok=True)
    open(os.path.join(repo, "run", "setup-pending"), "w").write("HOST=127.0.0.1\n")
    r = T._run(repo, "start.sh", FAKE_HEALTHY="1")
    assert r.returncode == 0 and _chat_calls(repo) == [] and _said(r.stdout) == [], (_marks(repo), r.stdout)
    repo = _chat_box(["start.sh"])
    r = _start(repo, HOLD_RESTART="1", extra="QSE_CHATD=on\n")
    assert r.returncode == 0 and _chat_calls(repo) == [], (_marks(repo), r.stdout + r.stderr)


def test_the_chatd_line_per_value():
    quiet = _quiet_port()
    node_quiet = ("[start] chatd: TandemNode's (QSE_CHATD=node), and no TandemNode answers on "
                  f"127.0.0.1:{quiet}: the engine serves without the Chat tab (install.sh --with-chat "
                  "--app-wheel FILE installs TandemNode)")
    notice = ("[start] chatd: QSE_CHATD=on is read as node: this engine has no chatd launcher (engine API "
              "1.7), TandemNode runs chatd")
    off = "[start] chatd: off (QSE_CHATD=off: no chatd here; QSE_CHATD=node gives it to TandemNode)"
    for value, want in (("off", [off]), ("node", [node_quiet]), ("on", [notice, node_quiet])):
        repo = _chat_box(["start.sh"])
        r = _start(repo, extra=f"QSE_CHATD={value}\n", TANDEM_NODE_PORT=str(quiet))
        assert r.returncode == 0 and _chat_calls(repo) == [], (value, _marks(repo), r.stdout + r.stderr)
        assert _said(r.stdout) == want, (value, r.stdout)
    # a TandemNode listening: it runs chatd
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(4)
    try:
        port = s.getsockname()[1]
        repo = _chat_box(["start.sh"])
        r = _start(repo, extra="QSE_CHATD=node\n", TANDEM_NODE_PORT=str(port))
        assert _said(r.stdout) == ["[start] chatd: TandemNode's (QSE_CHATD=node): it runs chatd "
                                   "(tandem-node chat status)"], r.stdout
    finally:
        s.close()
    # the release's template: what each value resolves to, in chat and in engine mode; start.sh's
    # lines from it (on: the notice once, then node's line); engine mode keeps node
    import test_engine_only as EO
    for mode in ("chat", "engine"):
        for value, resolved in ((None, "off"), ("off", "off"), ("on", "node" if mode == "chat" else "off"),
                                ("node", "node"), ("bogus", "off")):
            extra = {} if value is None else {"QSE_CHATD": value}
            r, marks, _ = EO._start(mode, chatd_running=True, TANDEM_NODE_PORT=str(quiet), **extra)
            assert r.returncode == 0, (mode, value, r.stdout + r.stderr)
            assert not [m for m in marks if m.startswith("chatd.sh")], (mode, value, marks)
            said = _said(r.stdout)
            if resolved == "node":
                assert said[-1] == node_quiet and (said[0] == notice) == (value == "on"), (mode, value, said)
            elif mode == "engine":
                assert said == ["[start] chatd: off (engine only; QSE_MODE=chat turns the chat on)"], said
            else:
                assert said == [off], (mode, value, said)
            if value == "bogus" and mode == "chat":             # engine mode turns it off first
                assert "[serve.env] QSE_CHATD=bogus is neither node nor off: off" in r.stderr, r.stderr


def test_stop_sh_leaves_chatd_alone():
    repo = _chat_box(["stop.sh"])
    pid = T._sleeper()
    T._proc(repo, pid, T._engine_argv(8000))
    try:
        r = T._run(repo, "stop.sh", "5")
        assert r.returncode == 0 and "stopped" in r.stdout, r.stdout + r.stderr
        assert _chat_calls(repo) == []
        assert "chatd" not in open(os.path.join(ROOT, "ops", "stop.sh")).read()
    finally:
        if T._alive(pid):
            os.kill(pid, 9)


def test_the_watchdog_leaves_chatd_alone():
    for value in (None, "on", "off", "node"):
        repo = _chat_box(["watchdog.sh"])
        if value:
            with open(os.path.join(repo, "ops", "serve.env"), "a") as fh:
                fh.write(f"QSE_CHATD={value}\n")
        for code in ("200", "200", "000"):
            assert T._run(repo, "watchdog.sh", FAKE_HTTP_CODE=code).returncode == 0
        assert _chat_calls(repo) == [], (value, _marks(repo))
        assert "chatd" not in T._wd_log(repo), (value, T._wd_log(repo))
        assert not [m for m in _marks(repo) if m.startswith(("stop.sh", "start.sh"))], (value, _marks(repo))
    text = open(os.path.join(ROOT, "ops", "watchdog.sh")).read()
    assert "chatd.sh" not in text and "chatd_check" not in text


# ------------------------------------------------------------------ bin/tandem (install.sh's write_control)
def control_script(d: str) -> str:
    text = open(os.path.join(ROOT, "install.sh")).read()
    fn = text[text.index("write_control() {"):text.index("\nCTL\n}\n") + len("\nCTL\n}\n")]
    return subprocess.run(["bash", "-c", fn + '\nDIR="$1"; write_control', "-", d], capture_output=True,
                          text=True, check=True).stdout


def _tandem() -> tuple[str, str, dict]:
    """A TANDEM_DIR with bin/tandem as install.sh writes it, stand-in scripts beside it, and a
    stand-in chatd.sh where a checkout from before 1.7 kept one (src/app/ops, src/ops)."""
    d = tempfile.mkdtemp(prefix="tandem-ctl-")
    src, run = os.path.join(d, "src"), os.path.join(d, "run")
    marks = os.path.join(d, "markers")
    for name in ("start.sh", "stop.sh"):
        _write(os.path.join(run, "ops", name),
               f'#!/bin/bash\necho "{name} $*" >> "$MARKERS"\n'
               + (f"touch {d}/up\n" if name == "start.sh" else f"rm -f {d}/up\n"))
    for where in LAUNCHERS:
        _write(os.path.join(src, *where), FAKE_CHATD)
    _write(os.path.join(d, "py"), '#!/bin/bash\necho "py $*" >> "$MARKERS"\n')
    _write(os.path.join(d, "fakebin", "curl"), f"#!/bin/bash\n[ -f {d}/up ] || exit 7\necho '{{}}'\n")
    _write(os.path.join(run, "ops", "serve.env"),
           f"REPO={src}\nPY={d}/py\nPORT=18995\nNV=a,b\nHEAD=h\nCKPT8=c\nCKPT16=c\nQWEN38_MODEL=m\n"
           f"SERVED_MODEL=t\nif [ -f {run}/local.env ]; then . {run}/local.env; fi\n", 0o644)
    _write(os.path.join(d, "bin", "tandem"), control_script(d))
    env = dict(os.environ, MARKERS=marks, TANDEM_SKIP_MEMCHECK="1",
               PATH=os.path.join(d, "fakebin") + os.pathsep + "/usr/bin:/bin")
    env.pop("TANDEM_NODE_BIN", None)
    return d, marks, env


def _ctl(d: str, env: dict, *args) -> subprocess.CompletedProcess:
    return subprocess.run([os.path.join(d, "bin", "tandem"), *args], capture_output=True, text=True,
                          env=env, timeout=60, stdin=subprocess.DEVNULL)


def _lines(path: str) -> list[str]:
    if not os.path.exists(path):
        return []
    out = open(path).read().splitlines()
    open(path, "w").close()
    return [ln for ln in out if not ln.startswith("py ")]


FAKE_NODE = '#!/bin/bash\necho "{name} $*" >> "$MARKERS"\necho "[chat] chatd: this node\'s"\n'


def test_bin_tandem_and_tandem_node():
    d, marks, env = _tandem()
    # start, restart, stop, status: the engine's scripts only, whatever QSE_CHATD says
    for value in (None, "on", "off", "node"):
        if value:
            _write(os.path.join(d, "run", "local.env"), f"QSE_CHATD={value}\n", 0o644)
        _lines(marks)
        assert _ctl(d, env, "start").returncode == 0
        assert _ctl(d, env, "start").returncode == 0                 # healthy already
        assert _ctl(d, env, "restart").returncode == 0
        assert _ctl(d, env, "stop", "90").returncode == 0
        got = _lines(marks)
        assert got == ["start.sh ", "stop.sh 60", "start.sh ", "stop.sh 90"], (value, got)
    # status's chat line: off, engine only, and TandemNode's (none here, then one)
    _write(os.path.join(d, "run", "local.env"), "QSE_CHATD=off\n", 0o644)
    r = _ctl(d, env, "status")
    assert r.returncode == 1 and r.stdout.splitlines()[-1] == \
        "[tandem] chat: off (QSE_CHATD=off: no chatd here; QSE_CHATD=node gives it to TandemNode)", r.stdout
    _write(os.path.join(d, "run", "local.env"), "QSE_CHATD=off\nQSE_MODE=engine\n", 0o644)
    r = _ctl(d, env, "status")
    assert r.stdout.splitlines()[-1] == "[tandem] chat: off (engine only; install.sh --with-chat --app-wheel " \
        "FILE adds the chat)", r.stdout
    _write(os.path.join(d, "run", "local.env"), "QSE_CHATD=node\n", 0o644)
    r = _ctl(d, env, "status")
    assert r.stdout.splitlines()[-1] == "[tandem] chat: TandemNode's (QSE_CHATD=node); no tandem-node here " \
        "(TANDEM_NODE_BIN)", r.stdout
    # tandem chat and tandem node without a tandem-node: exit 2, where it comes from
    for args in (("chat", "start"), ("node", "unit")):
        r = _ctl(d, env, *args)
        assert r.returncode == 2 and "TandemNode is TandemApp's" in r.stderr and \
            "install.sh --with-chat --app-wheel FILE" in r.stderr, (args, r.stderr)
    # the venv's tandem-node (what --with-chat installs), then one on PATH, then TANDEM_NODE_BIN first
    venv = os.path.join(d, "venv", "bin", "tandem-node")
    path = os.path.join(d, "fakebin", "tandem-node")
    pinned = os.path.join(d, "pinned", "tandem-node")
    for where, name in ((path, "path-node"), (venv, "venv-node"), (pinned, "pinned-node")):
        _write(where, FAKE_NODE.format(name=name))
    e = dict(env, TANDEM_NODE_BIN=pinned)
    _lines(marks)
    for args in (("chat",), ("chat", "start"), ("chat", "logs", "5"), ("node", "check", "--state-dir", "x")):
        assert _ctl(d, e, *args).returncode == 0, args
    assert _lines(marks) == [f"pinned-node chat --tandem-dir {d}", f"pinned-node chat start --tandem-dir {d}",
                             f"pinned-node chat logs 5 --tandem-dir {d}",
                             f"pinned-node check --state-dir x --tandem-dir {d}"]
    r = _ctl(d, env, "status")
    assert r.stdout.splitlines()[-1] == "[chat] chatd: this node's", r.stdout
    assert _lines(marks) == [f"venv-node chat status --tandem-dir {d}"]
    os.remove(venv)
    assert _ctl(d, env, "chat", "restart").returncode == 0
    assert _lines(marks) == [f"path-node chat restart --tandem-dir {d}"]
    assert not [m for m in open(marks).read().splitlines() if m.startswith("chatd.sh")]
    # an install that put TandemApp here (state/tandemapp-wheel.json): its TandemNode owns chatd
    _write(os.path.join(d, "state", "tandemapp-wheel.json"), "{}\n", 0o644)
    _write(venv, FAKE_NODE.format(name="venv-node"))
    _lines(marks)
    assert _ctl(d, env, "chat", "stop").returncode == 0 and _ctl(d, env, "node", "unit").returncode == 0
    _ctl(d, env, "status")
    assert _lines(marks) == [f"venv-node chat stop --tandem-dir {d} --chat on",
                             f"venv-node unit --tandem-dir {d} --chat on",
                             f"venv-node chat status --tandem-dir {d} --chat on"]
    r = _ctl(d, env, "help")
    assert r.returncode == 2 and "chat start|stop|restart|status|logs" in r.stderr, r.stderr


def test_the_units_and_the_uninstall():
    text = open(os.path.join(ROOT, "install.sh")).read()
    unit = text[text.index('cat > "$UNIT" <<EOF'):]
    unit = unit[:unit.index("\nEOF\n")]
    assert "ExecStart=/bin/bash $DIR/bin/tandem start" in unit and "ExecStop=/bin/bash $DIR/bin/tandem stop 120" in unit
    assert "TandemNode runs it (tandemnode.service" in text
    un = text[text.index('if [ "$UNINSTALL" = 1 ]; then'):text.index("printf '%sTandemLLM installer%s")]
    assert "chatd.sh" not in un and '"$DIR/bin/tandem" stop 120' in un
    assert 'if node_unit_ours; then' in un and '"$NODE_BIN" chat stop --tandem-dir "$DIR" --chat on' in un
    assert un.index('"$NODE_BIN" chat stop') < un.index("disable --now tandemnode.service") < \
        un.index('"$DIR/bin/tandem" stop 120')
    # ours: the unit names this DIR (a prefix of another DIR is not ours)
    fn = text[text.index("\nnode_unit_ours() {"):]
    fn = fn[:fn.index("}\n") + 2]
    with tempfile.TemporaryDirectory() as h:
        unit_path = os.path.join(h, "tandemnode.service")
        for body, want in ((f"ExecStart=/x/tandem-node serve --port 7700 --state-dir s --tandem-dir {h}/T --chat on\n", 0),
                           (f"ExecStart=/x/tandem-node serve --tandem-dir {h}/T2 --chat on\n", 1),
                           ("ExecStart=/x/tandem-node serve --chat on\n", 1)):
            with open(unit_path, "w") as fh:
                fh.write(body)
            r = subprocess.run(["bash", "-c", fn + "node_unit_ours"], capture_output=True, text=True,
                               env=dict(os.environ, NODE_UNIT=unit_path, DIR=f"{h}/T"))
            assert r.returncode == want, (body, r.returncode, r.stderr)


def test_the_install_says_what_it_did():
    text = open(os.path.join(ROOT, "install.sh")).read()
    done = text[text.index("${B}Done.${N}"):]
    assert "Control:   $DIR/bin/tandem start | stop | restart | status | smoke | logs | doctor | chat | node" in done
    assert "/dashboard" not in done and "Health:    http://$API_HOST:$PORT/health" in done
    chat = text[text.index('if [ "$MODE_NOW" = chat ] && [ "$CHATD_NOW" = node ]; then'):text.index("${B}Done.${N}")]
    assert "Chat:      TandemNode runs chatd ($DIR/bin/tandem chat status; its unit $NODE_UNIT)." in chat
    assert "TandemApp's ops/sandbox/install.sh adds the rest" in chat and "$SBX" not in text
    assert "Mode:      engine only (the default): no conductor, user memory, small models, chatd or page." in chat
    assert "--with-chat --app-wheel FILE" in chat and "  $CHAT_LINE\n" in done
    setup = text[text.index("no model is loaded yet. Point the Setup Guide"):text.index('if [ "$START" != 1 ]; then\n    info "skipped (--no-start). Start it with')]
    assert "/dashboard" not in setup and "only the Setup API (/v1/setup/*)" in setup
    assert "TandemNode starts chatd once the setup is done (%s/bin/tandem chat status)" in setup
    assert "chat_note" in setup and done.rstrip().endswith("chat_note")
    assert "A chatd that was running keeps the code it started with until: %s/bin/tandem chat restart" in text


# the shell lines that may not reach TandemApp's vendored tree (ENG-413 WP7): a path into app/ or
# src/app, or a variable joined to app/
APP_PATH = re.compile(r"(^|[\s\"'=:(/])(app/(ops|tools|proxy|chatd|tandemapp|dashboard|docs|contracts)\b|"
                      r"\$\{?(REPO|SRC|TANDEM_DIR|app|HERE)\}?/app\b|src/app\b)")


def _code_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


def test_no_launcher_names_app():
    files = ["install.sh", "ops/start.sh", "ops/stop.sh", "ops/watchdog.sh", "ops/engines.sh",
             "ops/roles.sh", "ops/hold.sh", "ops/use-profile.sh", "ops/make-secrets.sh", "ops/serve.env"]
    bad = []
    for rel in files:
        for ln in _code_lines(open(os.path.join(ROOT, rel)).read()):
            if APP_PATH.search(ln):
                bad.append(f"{rel}: {ln.strip()}")
    with tempfile.TemporaryDirectory() as d:
        for ln in _code_lines(control_script(d)):
            if APP_PATH.search(ln) or "chatd.sh" in ln:
                bad.append(f"bin/tandem: {ln.strip()}")
    listed = [ln for ln in _code_lines(open(os.path.join(ROOT, "ops", "executables.txt")).read())]
    bad += [f"ops/executables.txt: {ln}" for ln in listed if ln.startswith("app/")]
    assert not bad, bad
    # the pattern itself catches what the launchers had before WP7
    for old in ('CHATD="$REPO/app/ops/chatd.sh"', 'if [ -f "$SRC/app/ops/chatd.sh" ]; then',
                'if [ -d "$app/app/proxy" ]; then app="$app/app"; fi', "app/ops/chatd.sh"):
        assert APP_PATH.search(old), old
    return f"{len(files) + 2} files, no app/ path in code"


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  {name:60s} ok")
            passed += 1
    print(f"{passed} passed")
