"""SRV-745/746: no test reaches this machine's own TandemNode (tests/realnode.py).

  1  the guarded dir and port are the agent's defaults (~/.tandemllm/node of this account, 7700)
                                                                    test_the_real_dir_and_port
  2  the hook's decision: an open, rename or removal under the real state dir and a connection to
     loopback:7700 are refused, naming the path or address; anything else passes
                                                                    test_what_is_refused
  3  an agent pointed at a guarded state dir fails before its first write, names the path, and
     check() fails the test; the dir was never made; the next check is clean
                                                                    test_a_write_fails_first
  4  a connection to the real agent's port is refused before it is made, and check() fails the
     test                                                           test_a_connect_fails_first
  5  a claim or trust file that changed under the test (a child process's write) fails check()
                                                                    test_a_changed_claim_fails
  6  a test controller (the Proxy helper) has no node agent unless the test gives its own
                                                                    test_a_test_controller_has_no_agent
  7  TANDEM_TEST_REAL_NODE=1 leaves the hook off; without it the hook is on
                                                                    test_the_opt_in
1-5 skip under TANDEM_TEST_REAL_NODE=1 (the guard is off).

    python tests/test_real_node_guard.py
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import realnode  # noqa: E402
import test_node_agent as tna  # noqa: E402
from proxy import app as proxy_app, nodeagent  # noqa: E402
from proxy_util import Proxy  # noqa: E402


@contextlib.contextmanager
def guarded():
    """A temp dir the hook treats as the real state dir, forgotten again after the test."""
    d = tempfile.mkdtemp(prefix="realnode-")
    realnode.ROOTS.append(d)
    try:
        yield d
    finally:
        realnode.ROOTS.remove(d)
        for p in [p for p in realnode._seen if p.startswith(d + os.sep)]:
            del realnode._seen[p]
        shutil.rmtree(d, ignore_errors=True)


def guard_on() -> None:
    if os.environ.get(realnode.OPT_IN) == "1":
        raise unittest.SkipTest(f"{realnode.OPT_IN}=1: the guard is off")
    assert realnode._on, "the guard is not on in a test process"


def refused(fn) -> str:
    try:
        fn()
    except realnode.RealNode as exc:
        return str(exc)
    raise AssertionError("the guard let it through")


def check_fails() -> str:
    try:
        realnode.check()
    except AssertionError as exc:
        return str(exc)
    raise AssertionError("check() passed")


def test_the_real_dir_and_port():
    guard_on()
    assert nodeagent.DEFAULT_DIR.replace("~", realnode.HOME, 1) in realnode.ROOTS, realnode.ROOTS
    assert realnode.PORT == nodeagent.DEFAULT_PORT


def test_what_is_refused():
    guard_on()
    real = realnode.ROOTS[0]
    for event, args in (("open", (os.path.join(real, "fleet.json"), "w", 0)),
                        ("open", (os.path.join(real, "secret"), "r", 0)),
                        ("os.rename", ("/tmp/x", os.path.join(real, "trust", "node.key"), -1, -1)),
                        ("os.mkdir", (real, 0o700, -1)),
                        ("shutil.rmtree", (real, None))):
        w = realnode.why(event, args)
        assert w is not None and real in w, (event, w)
    for host, port in (("127.0.0.1", 7700), ("::1", 7700), ("localhost", 7700), ("0.0.0.0", 7700)):
        w = realnode.why("socket.connect", (None, (host, port)))
        assert w is not None and f"{host}:7700" in w, w
    for event, args in (("open", (os.path.join(real + "-b1", "fleet.json"), "w", 0)),
                        ("open", (3, "w", 0)),
                        ("os.mkdir", (tempfile.gettempdir(), 0o700, -1)),
                        ("socket.connect", (None, ("192.0.2.40", 7700))),
                        ("socket.connect", (None, ("127.0.0.1", 7701))),
                        ("socket.connect", (None, "/tmp/sock")),
                        ("os.listdir", (real,))):
        assert realnode.why(event, args) is None, (event, args)


def test_a_write_fails_first():
    guard_on()
    with guarded() as d:
        state = os.path.join(d, "node")
        msg = refused(lambda: tna.agent_on(state=state))
        assert f"os.mkdir {state}" in msg and realnode.OPT_IN in msg, msg
        assert not os.path.exists(state)
        assert state in check_fails()
        realnode.check()


def test_a_connect_fails_first():
    guard_on()
    s = socket.socket()
    try:
        msg = refused(lambda: s.connect(("127.0.0.1", realnode.PORT)))
    finally:
        s.close()
    assert "127.0.0.1:7700" in msg, msg
    assert "127.0.0.1:7700" in check_fails()
    realnode.check()


def test_a_changed_claim_fails():
    guard_on()
    with guarded() as d:
        realnode.check()
        claim = os.path.join(d, "fleet.json")
        with realnode.allowed(), open(claim, "w") as f:
            json.dump({"fingerprint": "0" * 64, "name": "a test controller"}, f)
        assert f"{claim} changed" in check_fails()
        realnode.check()
        with realnode.allowed():
            os.makedirs(os.path.join(d, "trust"))
            with open(os.path.join(d, "trust", "peer-x.crt"), "w") as f:
                f.write("pinned\n")
        assert os.path.join(d, "trust", "peer-x.crt") in check_fails()


def test_a_test_controller_has_no_agent():
    with Proxy():
        assert proxy_app.STATE["fleet"].agent is None
    with Proxy(env={"TANDEM_NODE_AGENT": "http://127.0.0.1:1",
                    "TANDEM_NODE_SECRET": os.path.join(tempfile.mkdtemp(), "secret")}):
        assert proxy_app.STATE["fleet"].agent.endpoint == "http://127.0.0.1:1"


def test_the_opt_in():
    code = "import realnode; realnode.guard(); print(realnode._on)"
    env = {k: v for k, v in os.environ.items() if k != realnode.OPT_IN}
    for flag, want in ((None, "True"), ("1", "False")):
        e = dict(env, **({realnode.OPT_IN: flag} if flag else {}))
        r = subprocess.run([sys.executable, "-c", code], cwd=HERE, env=e, capture_output=True,
                           text=True, timeout=60)
        assert r.returncode == 0 and r.stdout.strip() == want, (flag, r.stdout, r.stderr)


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
