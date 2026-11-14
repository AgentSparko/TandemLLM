"""OPS-250 / OPS-251 (ENG-413 WP7): the wheel carries chatd's launcher, so a TandemNode installed
from the wheel runs chatd. Before it, the wheel had chatd/, proxy/ and tandemapp/ but no
ops/chatd.sh, and `tandem-node chat start` there answered 409 `no_launcher`.

The installed layout is built here as a wheel lays it out (site-packages with chatd/, proxy/,
tandemapp/ and chatd/ops/chatd.sh; a venv's bin/python): no build, no network. The real wheel
is built and installed by tools/package_check.sh, which checks the launcher is in it, and by the
engine's tests/test_install_headless.py (install.sh --with-chat --app-wheel). The engine is a fake
on a free port, chatd runs on a free port with its own data folder; the box's engine, chatd and
agent are never addressed.

  1  pyproject.toml packages ops/chatd.sh as chatd/ops/chatd.sh (package chatd.ops, the ops
     folder, chatd.sh alone), the sdist carries it, package_check.sh checks the wheel for it
                                                               test_the_wheel_carries_the_launcher
  2  the agent's default launcher: ops/chatd.sh in a checkout, chatd/ops/chatd.sh in an installed
     layout, the checkout's path with neither (a start then answers 409 no_launcher)
                                                               test_the_default_launcher
  3  a real chatd from the installed layout: chatd.sh puts site-packages on the path, picks the
     venv's python, logs under ~/.tandemllm/logs; TandemNode's start, view and stop work with the
     default launcher                                          test_a_wheel_layout_runs_chatd

CPU only, standard library only. Run: python tests/test_wheel_chatd.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import tomllib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_node_chat as tnc  # noqa: E402
from ports import free_port  # noqa: E402
from proxy import nodechat  # noqa: E402
from proxy_util import run  # noqa: E402


def test_the_wheel_carries_the_launcher():
    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
        st = tomllib.load(f)["tool"]["setuptools"]
    assert "chatd.ops" in st["packages"] and st["package-dir"]["chatd.ops"] == "ops"
    assert st["package-data"]["chatd.ops"] == ["chatd.sh"]
    # setuptools takes the folder's modules too: ops/ has none, so the package is chatd.sh alone
    assert not [f for f in os.listdir(os.path.join(ROOT, "ops")) if f.endswith(".py")]
    assert os.path.isfile(os.path.join(ROOT, "ops", "chatd.sh"))
    with open(os.path.join(ROOT, "MANIFEST.in")) as f:
        assert "include ops/chatd.sh" in f.read().splitlines()
    with open(os.path.join(ROOT, "tools", "package_check.sh")) as f:
        assert 'grep -q "chatd/ops/chatd.sh" "$OUT/wheel-files.txt"' in f.read()
    return "chatd.ops = ops/chatd.sh, in the sdist, checked in the built wheel"


def _site(d: str) -> tuple[str, str]:
    """A venv's site-packages as the wheel lays it out, and the venv's python."""
    venv = os.path.join(d, "venv")
    site = os.path.join(venv, "lib", "python3", "site-packages")
    skip = shutil.ignore_patterns("__pycache__", "*.pyc")
    for pkg in ("chatd", "proxy", "tandemapp"):
        shutil.copytree(os.path.join(ROOT, pkg), os.path.join(site, pkg), ignore=skip)
    os.makedirs(os.path.join(site, "chatd", "ops"))
    shutil.copy2(os.path.join(ROOT, "ops", "chatd.sh"), os.path.join(site, "chatd", "ops", "chatd.sh"))
    os.makedirs(os.path.join(venv, "bin"))
    py = os.path.join(venv, "bin", "python")
    os.symlink(sys.executable, py)
    return site, py


def test_the_default_launcher():
    assert nodechat.default_launcher() == os.path.join(ROOT, "ops", "chatd.sh")
    d = tempfile.mkdtemp(prefix="wheel-chatd-")
    try:
        site, _ = _site(d)
        assert nodechat.default_launcher(site) == os.path.join(site, "chatd", "ops", "chatd.sh")
        empty = os.path.join(d, "empty")
        os.makedirs(empty)
        assert nodechat.default_launcher(empty) == os.path.join(empty, "ops", "chatd.sh")
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return "checkout, installed layout, neither"


def _environ(pid: int) -> list[bytes]:
    p = f"/proc/{pid}/environ"
    return open(p, "rb").read().split(b"\0") if os.path.exists(p) else []


def test_a_wheel_layout_runs_chatd():
    eng = tnc.FakeEngine(api="1.7")
    cport = free_port()
    assert cport not in (8016, 8000, 7700) and eng.port not in (8016, 8000, 7700)
    d = tempfile.mkdtemp(prefix="wheel-chatd-")
    try:
        site, py = _site(d)
        launcher = nodechat.default_launcher(site)
        home = os.path.join(d, "home")
        os.makedirs(home)
        # by hand, nothing of an engine around: the wheel's own venv and its home's logs
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": home,
               "QSE_CHAT_URL": f"http://127.0.0.1:{cport}", "QSE_CHAT_DATA": os.path.join(d, "data"),
               "QSE_CHAT_ENGINE": f"http://127.0.0.1:{eng.port}"}

        def sh(verb):
            return subprocess.run(["bash", launcher, verb], capture_output=True, text=True, env=env,
                                  timeout=90, cwd=d, stdin=subprocess.DEVNULL)
        r = sh("start")
        assert r.returncode == 0 and f"[chatd] started with {py} " in r.stdout, r.stdout + r.stderr
        r = sh("status")
        assert r.returncode == 0 and "[chatd] running: pid " in r.stdout, r.stdout + r.stderr
        assert f"tree {os.path.realpath(site)}" in r.stdout or "tree" not in r.stdout, r.stdout
        assert (tnc._chat_health(cport) or {}).get("service") == "chatd"
        pid = int(r.stdout.split("pid ")[1].split(",")[0])
        env_of = _environ(pid)
        if env_of:
            assert f"PYTHONPATH={site}".encode() in env_of, "site-packages on the path"
        log = os.path.join(home, ".tandemllm", "logs", "chatd.log")
        assert os.path.isfile(log) and os.path.getsize(log) > 0, "chatd's log under ~/.tandemllm"
        r = sh("stop")
        assert r.returncode == 0 and "[chatd] stopped" in r.stdout, r.stdout + r.stderr
        assert tnc._chat_health(cport) is None
        # through TandemNode with its default launcher (what a wheel install's agent uses)
        b = tnc.box(eng.port, chat_port=cport)
        try:
            chat = tnc.chat_on(b, eng.port, launcher=launcher)
            assert chat.launcher == launcher
            rc, lines = chat.run_action("start")
            assert rc == 0, lines
            v = chat.view()
            assert v["state"] == "running" and v["pid"] and v["launcher"] == launcher, v
            assert (tnc._chat_health(cport) or {}).get("service") == "chatd"
            rc, lines = chat.run_action("stop")
            assert rc == 0 and chat.view()["state"] == "stopped", lines
        finally:
            shutil.rmtree(b, ignore_errors=True)
    finally:
        eng.close()
        shutil.rmtree(d, ignore_errors=True)
    return "by hand and through TandemNode, from site-packages"


if __name__ == "__main__":
    run(globals())
