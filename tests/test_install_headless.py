"""The engine's installer and launchers without app/ (ENG-413 WP7, OPS-272, QA OPS-273).

Since engine API 1.7 no launcher or installer of the engine reads TandemApp's vendored tree: the
engine runs no chatd and serves no page, and the chat is TandemApp's, installed from its wheel
(`install.sh --with-chat --app-wheel FILE`) with its TandemNode running chatd. These tests run the
real install.sh end to end into a HOME of their own, from a release that is this checkout's files as
one commit of a repository of their own, with tests/test_fresh_install.py's stand-ins for the GPU,
nvcc and g++, a real venv (the python of this run behind install.sh's 3.11 and GPU checks), a uv that
installs a wheel into that venv offline and installs nothing else, and a systemctl that records its
calls. No network, no GPU, nothing of the box: the engine is install_control's stub on a free port,
TandemNode's port and chatd's URL are free ports, and the box's engine, chatd and agent are never
addressed. TandemApp's wheel is built here from its source (app/ until ENG-413 WP14, else
TANDEM_APP_SRC) with a stand-in page.

The pre-WP7 install is made by that release's own install.sh (tests/fixtures/pre_wp7: install.sh,
ops/start.sh, ops/watchdog.sh and ops/serve.env of the engine commit before WP7), whose launchers
ran chatd from the checkout's app/ops/chatd.sh, here a stand-in that writes its calls down.

The test matrix, the Gherkin of OPS-272:

  1  an engine-only install from a release with no app/: no QSE_CHATD written (off), QSE_MODE
     engine, no TandemApp, no TandemNode unit, no page in the last message; its bin/tandem start,
     status and stop and its watchdog run the stub engine with no app/ anywhere in the install;
     tandem chat and tandem node say TandemNode is TandemApp's       test_engine_only_needs_no_app
  2  --with-chat --app-wheel FILE --app-wheel-sha256 HEX: the wheel in the venv with the engine's
     pins as constraints, its sha256 kept, QSE_CHATD=node and QSE_MODE=chat written, TandemNode's
     unit (--chat on --tandem-dir DIR --port P) written and enabled; tandem node unit and tandem
     chat status run the venv's tandem-node; refusals: --app-wheel alone, a wrong sha256, a file that
     is not TandemApp's wheel, --with-chat with no wheel and none installed, --with-chat with
     --runtime-only, another TandemNode's unit              test_with_chat_installs_tandemapp
  3  updates: a re-run keeps node, chat and the TandemApp it has (no wheel installed again); a new
     wheel replaces it and says a running chatd keeps its code until tandem chat restart; a dry run
     changes nothing; --engine-only writes off              test_updates
  4  --uninstall: a dry run lists chatd's stop through this install's TandemNode, its unit's
     disable and removal before the engine's stop; the real one removes the unit and DIR; another
     TandemNode's unit is left alone and said so          test_uninstall
  5  a pre-WP7 install in chat mode keeps working until it is migrated: its own launchers start
     chatd from src/app/ops/chatd.sh; a re-run of this install.sh stops before it changes anything
     (run/, bin/ and src byte for byte), a dry run says so too, --runtime-only rewrites none of its
     launchers; --with-chat --app-wheel migrates it (node, TandemNode's unit, its chatd not stopped);
     --engine-only migrates another one and stops the chatd its launchers ran
                                                         test_a_pre_wp7_install_keeps_working
  6  the engine's argv from ops/start.sh is byte for byte the pre-WP7 start.sh's, in engine and in
     chat mode, with its environment's models (the WP7 gate)
                                                         test_start_argv_unchanged

Run: python tests/test_install_headless.py   (1 to 5 need Linux: install.sh stops anywhere else)
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import test_fresh_install as F  # noqa: E402
import test_install_control as I  # noqa: E402
from ports import free_port  # noqa: E402

PRE = os.path.join(ROOT, "tests", "fixtures", "pre_wp7")
#: the fixture's files: the engine's at the commit before WP7 (sha256, first 16 hex digits)
PRE_FILES = {"install.sh": "a2fc547893c26287", "serve.env": "7f495d2ad1197169",
             "start.sh": "faad8f48e26b2fe1", "watchdog.sh": "cdb25f866250cf88"}

# uv: --version, and `uv pip install ... X.whl` installs the wheel into --python's venv from the
# file alone (the engine's lock installs nothing here); every call is in $HOME/uv.log
UV = r"""#!/bin/bash
[ "$1" = --version ] && { echo "uv 0.12.12"; exit 0; }
echo "uv $*" >> "$HOME/uv.log"
if [ "$1" = pip ] && [ "$2" = install ]; then
    py=""; whl=""; prev=""
    for a in "$@"; do [ "$prev" = --python ] && py="$a"; case "$a" in *.whl) whl="$a" ;; esac; prev="$a"; done
    if [ -n "$whl" ]; then
        exec "$py" -m pip install --disable-pip-version-check --no-index --no-deps --force-reinstall -q "$whl"
    fi
fi
exit 0
"""
SYSTEMCTL = '#!/bin/bash\necho "systemctl $*" >> "$HOME/systemctl.log"\nexit 0\n'
# chatd's launcher where a checkout from before WP7 had it: its calls in $HOME/chatd.calls, its
# state in $HOME/chatd.up
OLD_CHATD = r"""#!/bin/bash
echo "chatd.sh $*" >> "$HOME/chatd.calls"
case "$1" in
  start) touch "$HOME/chatd.up"; echo "[chatd] started" ;;
  stop) rm -f "$HOME/chatd.up"; echo "[chatd] stopped" ;;
  restart) touch "$HOME/chatd.up" ;;
  status) [ -f "$HOME/chatd.up" ] && { echo "[chatd] running: pid 4242"; exit 0; }; echo "[chatd] not running"; exit 1 ;;
esac
"""
_ST: dict = {}


def _linux() -> bool:
    return platform.system() == "Linux"


def _cleanup() -> None:
    d = _ST.pop("tmp", None)
    _ST.clear()
    if d:
        shutil.rmtree(d, ignore_errors=True)


def teardown_module(module=None) -> None:
    _cleanup()


def _tmp() -> str:
    if "tmp" not in _ST:
        _ST["tmp"] = tempfile.mkdtemp(prefix="headless-install-")
        atexit.register(_cleanup)
    return _ST["tmp"]


def _write(path: str, text: str, mode: int = 0o644) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)
    os.chmod(path, mode)
    return path


def _sha(path: str) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def release(kind: str) -> tuple[str, str]:
    """This checkout as one commit of a repository of its own: `headless` without app/, `pre` with
    app/ and the pre-WP7 launchers and template (its chatd.sh a stand-in). (path, commit)"""
    key = "rel-" + kind
    if key in _ST:
        return _ST[key]
    rel = os.path.join(_tmp(), key)
    top = {".git", "run", "logs", "train"} | ({"app"} if kind == "headless" else set())
    skip = shutil.ignore_patterns("node_modules", "__pycache__", "*.pdf")
    shutil.copytree(ROOT, rel, symlinks=True, ignore=lambda d, names: set(skip(d, names)) | (
        top & set(names) if os.path.samefile(d, ROOT) else set()))
    if kind == "pre":
        for name, where in (("start.sh", "ops"), ("watchdog.sh", "ops"), ("serve.env", "ops")):
            shutil.copy(os.path.join(PRE, name), os.path.join(rel, where, name))
            if name.endswith(".sh"):                 # the release's modes (tandem doctor restores them)
                os.chmod(os.path.join(rel, where, name), 0o755)
        _write(os.path.join(rel, "app", "ops", "chatd.sh"), OLD_CHATD, 0o755)
        # the old installer builds the dashboard's page when this is there (Node, the network): not here
        os.remove(os.path.join(rel, "app", "tools", "dashboard_bundle.py"))
    git = ["git", "-C", rel, "-c", "user.name=t", "-c", "user.email=t@example.com"]
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "release"]):
        subprocess.run(git + cmd, check=True, capture_output=True)
    ref = subprocess.run(git + ["rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    _ST[key] = (rel, ref)
    return rel, ref


def wheel() -> str | None:
    """TandemApp's wheel, built once from its source with a stand-in page; None without a source."""
    if "wheel" in _ST:
        return _ST["wheel"]
    src = os.environ.get("TANDEM_APP_SRC") or os.path.join(ROOT, "app")
    if not os.path.isfile(os.path.join(src, "pyproject.toml")):
        _ST["wheel"] = None
        return None
    copy = os.path.join(_tmp(), "appsrc")
    shutil.copytree(src, copy, ignore=shutil.ignore_patterns("node_modules", "__pycache__", ".git"))
    dist = os.path.join(copy, "dashboard", "dist")
    if not os.path.isfile(os.path.join(dist, "index.html")):
        shutil.rmtree(dist, ignore_errors=True)
        _write(os.path.join(dist, "index.html"), "<!doctype html><title>stand-in</title>\n")
        _write(os.path.join(dist, "assets", "app.js"), "console.log(1)\n")
    out = os.path.join(_tmp(), "wheel")
    r = subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "--no-index",
                        "--disable-pip-version-check", "-q", "-w", out, copy], capture_output=True, text=True,
                       timeout=600, cwd=_tmp())
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    (name,) = [n for n in os.listdir(out) if n.endswith(".whl")]
    assert re.fullmatch(r"tandemapp-[^-]+-py3-none-any\.whl", name), name
    import zipfile
    names = set(zipfile.ZipFile(os.path.join(out, name)).namelist())
    assert "chatd/ops/chatd.sh" in names, "the wheel carries chatd's launcher (OPS-250)"
    _ST["wheel"] = os.path.join(out, name)
    return _ST["wheel"]


def _stubs() -> str:
    if "stubs" in _ST:
        return _ST["stubs"]
    d = os.path.join(_tmp(), "stubs")
    for p, text in F._STUBS.items():
        _write(os.path.join(d, p), text, 0o755)
    _write(os.path.join(d, "bin", "uv"), UV, 0o755)
    _write(os.path.join(d, "bin", "systemctl"), SYSTEMCTL, 0o755)
    _write(os.path.join(d, "bin", "curl"), I._CURL.format(curl=shutil.which("curl")), 0o755)
    _ST["stubs"] = d
    return d


def _venv(d: str) -> None:
    """DIR/venv: a real venv (pip from ensurepip, offline) whose bin/python is install_control's
    stand-in in front of the venv's own python3."""
    venv = os.path.join(d, "venv")
    if os.path.isdir(venv):
        return
    subprocess.run([sys.executable, "-m", "venv", venv], check=True, capture_output=True, timeout=300)
    os.remove(os.path.join(venv, "bin", "python"))
    _write(os.path.join(venv, "bin", "python"), I._PLAIN_PY.format(py=os.path.join(venv, "bin", "python3")), 0o755)


def install(name: str, *extra: str, setup: bool = False, kind: str = "headless", script: str | None = None,
            port: int | None = None, check: bool = True) -> dict:
    """install.sh (this checkout's, or `script`) --no-start into HOME tmp/<name>, from release `kind`."""
    rel, ref = release(kind)
    stubs = _stubs()
    home = os.path.join(_tmp(), name)
    d = os.path.join(home, "TandemLLM")
    os.makedirs(home, exist_ok=True)
    _venv(d)
    for f in ("config.json", "tokenizer.json"):         # Wren's model, so nothing is downloaded
        _write(os.path.join(home, ".qwen38-spark-engine/models/qwen3.5-2b", f), "{}")
    port = port or I.free_port()
    node_port, chat_port = free_port(), free_port()
    assert {port, node_port, chat_port}.isdisjoint({7700, 8000, 8016, 8091})
    env = {"HOME": home, "PATH": os.path.join(stubs, "bin") + ":" + os.environ.get("PATH", "/usr/bin:/bin"),
           "CUDA_HOME": os.path.join(stubs, "cuda"), "LANG": "C.UTF-8", "TANDEM_NODE_PORT": str(node_port)}
    args = ["bash", script or os.path.join(ROOT, "install.sh"), "--no-start", "--yes", "--no-corpus", "--dir", d,
            "--repo", rel, "--ref", ref, "--port", str(port), "--host", "127.0.0.1", *extra]
    if setup:
        args.append("--setup")
    box = {"home": home, "dir": d, "env": env, "port": port, "args": args, "node_port": node_port,
           "chat_port": chat_port, "rel": rel}
    r = run(box)
    box.update(out=r.stdout, err=r.stderr, rc=r.returncode)
    if check:
        assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    if os.path.isdir(os.path.join(d, "run")):
        # no status responder (it outlives a stop on purpose) and chatd's URL a free port: nothing
        # here may reach the box's own chatd
        with open(os.path.join(d, "run", "local.env"), "a") as fh:
            fh.write(f"QSE_STATUS_PORT=\nQSE_CHAT_URL=http://127.0.0.1:{chat_port}\n")
    return box


def run(box: dict, *extra: str, drop: tuple = ()) -> subprocess.CompletedProcess:
    """install.sh again with the box's arguments (less `drop` and its value) and `extra`."""
    args, skip = [], 0
    for a in box["args"]:
        if skip:
            skip -= 1
            continue
        if a in drop:
            skip = 1 if a in ("--app-wheel", "--app-wheel-sha256") else 0
            continue
        args.append(a)
    return subprocess.run(args + list(extra), env=box["env"], capture_output=True, text=True, timeout=900,
                          stdin=subprocess.DEVNULL)


def resolved(box: dict, *keys: str) -> dict:
    serve = os.path.join(box["dir"], "run", "ops", "serve.env")
    script = 'set -a; . "$1" >/dev/null 2>&1; ' + "; ".join(f'echo "{k}=${{{k}-<unset>}}"' for k in keys)
    out = subprocess.run(["bash", "-c", script, "-", serve], env=box["env"], capture_output=True, text=True,
                         timeout=60).stdout
    return dict(ln.split("=", 1) for ln in out.splitlines())


def written(box: dict, key: str) -> list[str]:
    """The lines `KEY=` install.sh wrote into run/ops/serve.env."""
    text = open(os.path.join(box["dir"], "run", "ops", "serve.env")).read()
    return [ln for ln in text.splitlines() if ln.startswith(key + "=")]


def _plain(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _log(box: dict, name: str) -> list[str]:
    p = os.path.join(box["home"], name)
    return open(p).read().splitlines() if os.path.exists(p) else []


def _tree_bytes(*roots: str) -> dict:
    out = {}
    for root in roots:
        for base, _, names in os.walk(root):
            for n in names:
                p = os.path.join(base, n)
                if not os.path.islink(p):
                    out[p] = _sha(p)
    return out


def _skip(what: str) -> str | None:
    if not _linux():
        print(f"    (skipped: install.sh runs on Linux only -- run it on the box: {what})")
        return "skipped off Linux"
    return None


def _restore(box: dict) -> None:
    """The checkout's own server/app.py back (an update refuses a checkout with local changes)."""
    subprocess.run(["git", "-C", os.path.join(box["dir"], "src"), "checkout", "-q", "--", "server/app.py"],
                   check=True)


def _stub_env(box: dict) -> dict:
    """The stub engine in the install's checkout (install_control's), a process table of its own."""
    with open(os.path.join(box["dir"], "src", "server", "app.py"), "w") as fh:
        fh.write(I.STUB_APP)
    proc = os.path.join(box["home"], "proc")
    os.makedirs(proc, exist_ok=True)
    return dict(box["env"], ENGINE_PROC=proc, TANDEM_SKIP_MEMCHECK="1")


# ---------------------------------------------------------------------------------------- 1
def test_engine_only_needs_no_app():
    if _skip("engine only"):
        return "skipped off Linux"
    box = install("eo")
    d, src = box["dir"], os.path.join(box["dir"], "src")
    assert not os.path.exists(os.path.join(src, "app")) and not os.path.exists(os.path.join(box["rel"], "app"))
    assert written(box, "QSE_CHATD") == [] and written(box, "TANDEM_NODE_PORT") == []
    got = resolved(box, "QSE_MODE", "QSE_CHATD", "QSE_EXT")
    assert got == {"QSE_MODE": "engine", "QSE_CHATD": "off", "QSE_EXT": ""}, got
    assert not os.path.exists(os.path.join(d, "venv", "bin", "tandem-node"))
    assert not os.path.exists(os.path.join(d, "state", "tandemapp-wheel.json"))
    assert not os.path.exists(os.path.join(box["home"], ".config/systemd/user/tandemnode.service"))
    out = _plain(box["out"])
    assert "/dashboard" not in out and "Mode:      engine only (the default)" in out, out[-2500:]
    assert f"Health:    http://127.0.0.1:{box['port']}/health" in out and "--with-chat --app-wheel FILE" in out
    assert not [ln for ln in _log(box, "uv.log") if ".whl" in ln] and _log(box, "systemctl.log") == []
    # the launchers of this install, on the stub engine; nothing named app/ exists anywhere here
    env = _stub_env(box)
    pid = None
    try:
        r = I.tandem(box, env, "start")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "[start] chatd: off (engine only; QSE_MODE=chat turns the chat on)" in r.stdout, r.stdout
        pid = I.adopt(box, env)
        r = I.tandem(box, env, "status")
        assert r.returncode == 0 and f"[tandem] healthy on :{box['port']}" in r.stdout, r.stdout + r.stderr
        assert r.stdout.splitlines()[-1] == "[tandem] chat: off (engine only; install.sh --with-chat " \
            "--app-wheel FILE adds the chat)", r.stdout
        w = subprocess.run(["bash", os.path.join(d, "run", "ops", "watchdog.sh")], env=env, capture_output=True,
                           text=True, timeout=60)
        assert w.returncode == 0, w.stdout + w.stderr
        wd = os.path.join(src, "logs", "watchdog.log")
        assert "chatd" not in (open(wd).read() if os.path.exists(wd) else ""), "the watchdog said chatd"
        for args in (("chat", "status"), ("node", "check")):
            r = I.tandem(box, env, *args)
            assert r.returncode == 2 and "TandemNode is TandemApp's" in r.stderr, (args, r.stdout + r.stderr)
        r = I.tandem(box, env, "stop", "20")
        assert r.returncode == 0 and "[stop] stopped" in r.stdout, r.stdout + r.stderr
    finally:
        if pid is not None and not I._gone(pid):
            os.kill(pid, signal.SIGKILL)
    found = [os.path.join(b, n) for b, dirs, names in os.walk(d) for n in dirs + names if n == "app"
             and os.path.isdir(os.path.join(b, n)) and "/venv/" not in b + "/"]
    assert not found, found
    return "no app/, no QSE_CHATD (off), start/status/watchdog/stop on the stub, chat and node refused"


# ---------------------------------------------------------------------------------------- 2
def test_with_chat_installs_tandemapp():
    if _skip("with chat"):
        return "skipped off Linux"
    w = wheel()
    if w is None:
        print("    (skipped: no TandemApp source: app/ is gone; set TANDEM_APP_SRC)")
        return "skipped: no TandemApp source"
    sha = _sha(w)
    box = install("chat", "--with-chat", "--app-wheel", w, "--app-wheel-sha256", sha.upper())
    d = box["dir"]
    node = os.path.join(d, "venv", "bin", "tandem-node")
    assert os.access(node, os.X_OK), "tandem-node in the venv"
    state = json.load(open(os.path.join(d, "state", "tandemapp-wheel.json")))
    assert state["wheel"] == os.path.basename(w) and state["sha256"] == sha, state
    assert state["version"] == os.path.basename(w).split("-")[1]
    calls = [ln for ln in _log(box, "uv.log") if ".whl" in ln]
    assert len(calls) == 1 and f"-c {d}/run/requirements.lock" in calls[0] and "--reinstall-package tandemapp" \
        in calls[0], calls
    assert written(box, "QSE_CHATD") == ["QSE_CHATD=node"] and \
        written(box, "TANDEM_NODE_PORT") == [f"TANDEM_NODE_PORT={box['node_port']}"]
    assert resolved(box, "QSE_MODE", "QSE_CHATD") == {"QSE_MODE": "chat", "QSE_CHATD": "node"}
    unit = open(os.path.join(box["home"], ".config/systemd/user/tandemnode.service")).read()
    assert f"ExecStart={node} serve --host 127.0.0.1 --port {box['node_port']} " in unit, unit
    assert f" --tandem-dir {d} " in unit and unit.split("ExecStart=")[1].split("\n")[0].endswith(" --chat on"), unit
    assert _log(box, "systemctl.log") == ["systemctl --user daemon-reload", "systemctl --user enable tandemnode.service"]
    out = _plain(box["out"])
    assert f"Chat:      TandemNode runs chatd ({d}/bin/tandem chat status; its unit" in out, out[-2500:]
    assert "/dashboard" not in out and f"sha256 {sha}" in out
    # bin/tandem node and chat run the venv's tandem-node, owning chatd as its unit does
    env = dict(box["env"])
    r = I.tandem(box, env, "node", "unit")
    assert r.returncode == 0 and f"--tandem-dir {d}" in r.stdout and "--chat on" in r.stdout, r.stdout + r.stderr
    r = I.tandem(box, env, "chat", "status", "--json")
    assert r.returncode in (0, 1), r.stdout + r.stderr
    view = json.loads(r.stdout)
    assert view["own"] is True and view["engine_chatd"] == "node" and view["state"] == "stopped", view
    assert view["launcher"].endswith("/site-packages/chatd/ops/chatd.sh"), view["launcher"]
    # refusals: nothing installed or written by any of them
    before = _tree_bytes(os.path.join(d, "run"), os.path.join(d, "bin"), os.path.join(d, "state"))
    other = os.path.join(_tmp(), "not-a-wheel.whl")
    _write(other, "x")
    for extra, said in ((("--app-wheel", w), "--app-wheel goes with --with-chat"),
                        (("--with-chat", "--app-wheel", w, "--app-wheel-sha256", "0" * 64), f"has sha256 {sha}"),
                        (("--with-chat", "--app-wheel", other), "--app-wheel takes TandemApp's wheel"),
                        (("--with-chat", "--app-wheel", w, "--runtime-only"), "--runtime-only writes none")):
        r = run(box, *extra, drop=("--with-chat", "--app-wheel", "--app-wheel-sha256"))
        assert r.returncode != 0 and said in r.stderr, (extra, r.stdout[-1500:] + r.stderr[-1500:])
    assert _tree_bytes(os.path.join(d, "run"), os.path.join(d, "bin"), os.path.join(d, "state")) == before
    eo = install("chat-nowheel")
    r = run(eo, "--with-chat")
    assert r.returncode != 0 and "--with-chat installs TandemApp's chat: add --app-wheel FILE" in r.stderr, \
        r.stdout[-1500:] + r.stderr[-1500:]
    # another TandemNode's unit is never overwritten
    foreign = install("chat-foreign")
    _write(os.path.join(foreign["home"], ".config/systemd/user/tandemnode.service"),
           "[Service]\nExecStart=/opt/tandem-node serve --port 7700 --chat on\n")
    r = run(foreign, "--with-chat", "--app-wheel", w)
    assert r.returncode != 0 and "is another TandemNode's, not this install's" in r.stderr, r.stderr[-1500:]
    assert "/opt/tandem-node" in open(os.path.join(foreign["home"], ".config/systemd/user/tandemnode.service")).read()
    assert not os.path.exists(os.path.join(foreign["dir"], "venv", "bin", "tandem-node")), "stopped before the wheel"
    _ST["chat-box"] = box
    return f"{os.path.basename(w)} in the venv, node + unit --chat on, six refusals"


# ---------------------------------------------------------------------------------------- 3
def test_updates():
    if _skip("updates"):
        return "skipped off Linux"
    w = wheel()
    if w is None:
        print("    (skipped: no TandemApp source)")
        return "skipped: no TandemApp source"
    box = install("upd", "--with-chat", "--app-wheel", w)
    d = box["dir"]
    n_whl = lambda: len([ln for ln in _log(box, "uv.log") if ".whl" in ln])  # noqa: E731
    assert n_whl() == 1
    # a re-run with no flag: node, chat and the TandemApp it has; no wheel installed again
    r = run(box, drop=("--with-chat", "--app-wheel"))
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert n_whl() == 1 and written(box, "QSE_CHATD") == ["QSE_CHATD=node"]
    assert resolved(box, "QSE_MODE")["QSE_MODE"] == "chat" and "A chatd that was running keeps" not in r.stdout
    assert _log(box, "systemctl.log")[-1] == "systemctl --user enable tandemnode.service", "the unit kept"
    # --with-chat alone keeps it too; a new wheel replaces it and says what a running chatd does
    r = run(box, drop=("--app-wheel",))
    assert r.returncode == 0 and n_whl() == 1, r.stderr[-2000:]
    assert "TandemApp: the one this install has" in _plain(r.stdout), r.stdout[-2000:]
    r = run(box)
    assert r.returncode == 0 and n_whl() == 2, r.stderr[-2000:]
    assert f"A chatd that was running keeps the code it started with until: {d}/bin/tandem chat restart" in r.stdout
    # a dry run changes nothing
    before = _tree_bytes(os.path.join(d, "run"), os.path.join(d, "bin"), os.path.join(d, "state"))
    r = run(box, "--dry-run")
    assert r.returncode == 0 and "DRY RUN" in r.stdout, r.stderr[-2000:]
    assert n_whl() == 2 and _tree_bytes(os.path.join(d, "run"), os.path.join(d, "bin"),
                                        os.path.join(d, "state")) == before
    # --engine-only: off, engine; TandemApp stays installed
    r = run(box, "--engine-only", drop=("--with-chat", "--app-wheel"))
    assert r.returncode == 0, r.stderr[-2000:]
    assert written(box, "QSE_CHATD") == ["QSE_CHATD=off"]
    assert resolved(box, "QSE_MODE", "QSE_CHATD") == {"QSE_MODE": "engine", "QSE_CHATD": "off"}
    return "keep, keep, replace with the note, dry run, engine-only"


# ---------------------------------------------------------------------------------------- 4
def test_uninstall():
    if _skip("uninstall"):
        return "skipped off Linux"
    w = wheel()
    if w is None:
        print("    (skipped: no TandemApp source)")
        return "skipped: no TandemApp source"
    box = install("un", "--with-chat", "--app-wheel", w)
    d, unit = box["dir"], os.path.join(box["home"], ".config/systemd/user/tandemnode.service")
    base = ["bash", os.path.join(ROOT, "install.sh"), "--dir", d, "--uninstall", "--yes"]
    env = dict(box["env"], ENGINE_PROC=os.path.join(box["home"], "proc"))
    os.makedirs(env["ENGINE_PROC"], exist_ok=True)
    r = subprocess.run(base + ["--dry-run"], env=env, capture_output=True, text=True, timeout=300,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stdout + r.stderr
    lines = [ln.strip() for ln in r.stdout.splitlines() if "[dry-run]" in ln]
    order = [next(i for i, ln in enumerate(lines) if want in ln) for want in
             ("tandem-node chat stop --tandem-dir", "systemctl --user disable --now tandemnode.service",
              f"rm -f {unit}", "bin/tandem stop 120", f"rm -rf {d}")]
    assert order == sorted(order), lines
    assert f"chat stop --tandem-dir {d} --chat on" in lines[order[0]], lines
    assert os.path.isfile(unit) and os.path.isdir(d)
    r = subprocess.run(base, env=env, capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not os.path.exists(unit) and not os.path.exists(d)
    assert "systemctl --user disable --now tandemnode.service" in _log(box, "systemctl.log")
    # an engine-only install beside another TandemNode: its unit is left, and said so
    eo = install("un-eo")
    foreign = _write(os.path.join(eo["home"], ".config/systemd/user/tandemnode.service"),
                     "[Service]\nExecStart=/opt/tandem-node serve --chat on\n")
    r = subprocess.run(["bash", os.path.join(ROOT, "install.sh"), "--dir", eo["dir"], "--uninstall", "--yes"],
                       env=dict(eo["env"], ENGINE_PROC=os.path.join(eo["home"], "proc")), capture_output=True,
                       text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert r.returncode == 0 and "is not this install's: TandemNode and its chatd are left running" in r.stdout, \
        r.stdout + r.stderr
    assert os.path.isfile(foreign) and not os.path.exists(eo["dir"])
    assert _log(eo, "systemctl.log") == []
    return "dry run in order, unit and DIR gone; another TandemNode left"


# ---------------------------------------------------------------------------------------- 5
def _old_cycle(box: dict, env: dict) -> list[str]:
    """The pre-WP7 install's own bin/tandem start then stop on the stub engine: its chatd calls."""
    calls = os.path.join(box["home"], "chatd.calls")
    if os.path.exists(calls):
        os.remove(calls)
    pid = None
    try:
        r = I.tandem(box, env, "start")
        assert r.returncode == 0, r.stdout + r.stderr
        pid = I.adopt(box, env)
        r = I.tandem(box, env, "stop", "20")
        assert r.returncode == 0, r.stdout + r.stderr
    finally:
        if pid is not None and not I._gone(pid):
            os.kill(pid, signal.SIGKILL)
    return open(calls).read().splitlines() if os.path.exists(calls) else []


def test_a_pre_wp7_install_keeps_working():
    for name, want in PRE_FILES.items():
        assert _sha(os.path.join(PRE, name))[:16] == want, name
    if _skip("pre-WP7"):
        return "skipped off Linux"
    w = wheel()
    if w is None:
        print("    (skipped: no TandemApp source)")
        return "skipped: no TandemApp source"
    # the pre-WP7 install: its own install.sh, chat mode, chatd from src/app/ops/chatd.sh
    box = install("pre", "--with-chat", kind="pre", script=os.path.join(PRE, "install.sh"))
    d = box["dir"]
    assert resolved(box, "QSE_MODE", "QSE_CHATD") == {"QSE_MODE": "chat", "QSE_CHATD": "<unset>"}
    env = _stub_env(box)
    old = ["chatd.sh status", "chatd.sh start", "chatd.sh status", "chatd.sh stop"]
    assert _old_cycle(box, env) == old
    # a re-run of this install.sh stops before it changes anything, the dry run too
    head = subprocess.run(["git", "-C", os.path.join(d, "src"), "rev-parse", "HEAD"], capture_output=True,
                          text=True).stdout
    keep = (os.path.join(d, "run"), os.path.join(d, "bin"), os.path.join(d, "state"))
    before = _tree_bytes(*keep)
    hl, hl_ref = release("headless")
    new = [a for a in box["args"] if a != "--with-chat"]
    new[1] = os.path.join(ROOT, "install.sh")
    new[new.index("--repo") + 1], new[new.index("--ref") + 1] = hl, hl_ref
    box_new = dict(box, args=new)
    r = run(box_new)
    assert r.returncode != 0 and "is an install from before engine API 1.7 in chat mode" in r.stderr and \
        "Nothing was changed; the install keeps working as it is" in r.stderr, r.stdout[-2000:] + r.stderr[-2000:]
    same = [a for a in box["args"] if a != "--with-chat"]
    same[1] = os.path.join(ROOT, "install.sh")
    r = run(dict(box, args=same), "--dry-run")
    assert r.returncode == 0 and "(dry-run, would stop here)" in r.stderr and \
        "is an install from before engine API 1.7" in r.stderr, r.stderr[-2000:]
    assert _tree_bytes(*keep) == before
    assert subprocess.run(["git", "-C", os.path.join(d, "src"), "rev-parse", "HEAD"], capture_output=True,
                          text=True).stdout == head
    assert _old_cycle(box, env) == old, "still working"
    # --runtime-only touches no launcher of it (its source moves, its run/ and bin/ stay)
    pre_rel, pre_ref = release("pre")
    rt = [a for a in box["args"] if a not in ("--with-chat", "--no-corpus")]
    rt[1] = os.path.join(ROOT, "install.sh")
    _restore(box)                                  # the stub engine out of the checkout first
    r = subprocess.run(rt + ["--runtime-only"], env=box["env"], capture_output=True, text=True, timeout=900,
                       stdin=subprocess.DEVNULL)
    st = subprocess.run(["git", "-C", os.path.join(d, "src"), "status", "--porcelain", "--untracked-files=no"],
                        capture_output=True, text=True).stdout
    assert r.returncode == 0, st + r.stdout[-2000:] + r.stderr[-2000:]
    assert _tree_bytes(os.path.join(d, "run", "ops"), os.path.join(d, "bin")) == \
        {k: v for k, v in before.items() if k.startswith((os.path.join(d, "run", "ops"), os.path.join(d, "bin")))}
    env = _stub_env(box)
    assert _old_cycle(box, env) == old
    # migrated with TandemApp's wheel: node, TandemNode's unit; the running chatd is not stopped
    _restore(box)
    open(os.path.join(box["home"], "chatd.up"), "w").close()
    os.remove(os.path.join(box["home"], "chatd.calls"))
    r = run(box_new, "--with-chat", "--app-wheel", w)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    assert not os.path.exists(os.path.join(box["home"], "chatd.calls")), "the install called chatd.sh"
    assert written(box, "QSE_CHATD") == ["QSE_CHATD=node"]
    assert resolved(box, "QSE_MODE", "QSE_CHATD", "QSE_CHATD_ASKED") == \
        {"QSE_MODE": "chat", "QSE_CHATD": "node", "QSE_CHATD_ASKED": "node"}
    assert f"--tandem-dir {d} " in open(os.path.join(box["home"], ".config/systemd/user/tandemnode.service")).read()
    assert "A chatd that was running keeps the code it started with until" in r.stdout
    assert not os.path.exists(os.path.join(d, "src", "app"))
    # another pre-WP7 install, migrated with --engine-only: the chatd its launchers ran is stopped
    eo = install("pre-eo", "--with-chat", kind="pre", script=os.path.join(PRE, "install.sh"))
    data = os.path.join(eo["home"], ".tandemllm", "chat")
    os.makedirs(data, exist_ok=True)
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)", "-m", "chatd"],
                         start_new_session=True)
    try:
        _write(os.path.join(data, "chatd.pid"), f"{p.pid}\n")
        new = [a for a in eo["args"] if a != "--with-chat"]
        new[1] = os.path.join(ROOT, "install.sh")
        new[new.index("--repo") + 1], new[new.index("--ref") + 1] = hl, hl_ref
        r = run(dict(eo, args=new), "--engine-only")
        assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
        assert f"stopped the chatd the old launchers ran (pid {p.pid})" in r.stdout, r.stdout[-3000:]
        assert p.wait(timeout=60) is not None and not os.path.exists(os.path.join(data, "chatd.pid"))
        assert written(eo, "QSE_CHATD") == ["QSE_CHATD=off"] and resolved(eo, "QSE_MODE")["QSE_MODE"] == "engine"
    finally:
        if p.poll() is None:
            p.kill()
    return "old launchers run chatd; refused byte for byte; runtime-only keeps them; both migrations"


# ---------------------------------------------------------------------------------------- 6
def test_start_argv_unchanged():
    """The WP7 gate: start.sh launches the engine with the same argv and environment as before."""
    import test_engine_only as EO
    import test_ops_scripts as T
    got = {}
    real = T.OPS
    pre_ops = os.path.join(_tmp(), "pre-ops")
    os.makedirs(pre_ops, exist_ok=True)
    shutil.copy(os.path.join(PRE, "start.sh"), os.path.join(pre_ops, "start.sh"))
    for name in ("engines.sh", "roles.sh"):
        shutil.copy(os.path.join(real, name), os.path.join(pre_ops, name))
    try:
        for which, ops in (("pre", pre_ops), ("now", real)):
            T.OPS = ops
            for mode in ("engine", "chat"):
                r, marks, cdir = EO._start(mode, TANDEM_NODE_PORT=str(free_port()), QSE_CHATD="off")
                assert r.returncode == 0, (which, mode, r.stdout + r.stderr)
                argv = next(m for m in marks if m.startswith("engine "))
                envl = next(m for m in marks if m.startswith("env "))
                got[(which, mode)] = (argv.replace(cdir, "<conductor>").replace(os.path.dirname(cdir), "<repo>"),
                                      envl)
    finally:
        T.OPS = real
    for mode in ("engine", "chat"):
        assert got[("pre", mode)] == got[("now", mode)], (mode, got[("pre", mode)], got[("now", mode)])
    return "engine and chat: argv and environment equal to the pre-WP7 start.sh's"


if __name__ == "__main__":
    try:
        passed = 0
        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and callable(fn):
                t0 = time.time()
                fn()
                print(f"  {name:50s} ok ({time.time() - t0:.1f} s)")
                passed += 1
        print(f"{passed} passed")
    finally:
        _cleanup()
