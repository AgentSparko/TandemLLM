"""The bit-equal guard of the managed overlay (engine-ctl-v1; ENG-452): with an empty overlay,
ops/start.sh starts the engine with the command line and the environment it had before the overlay
existed, byte for byte (the gate of ENG-413 WP7's test_start_argv_unchanged, on this change).

tests/fixtures/pre_ctl holds ops/serve.env and ops/start.sh of the engine commit before the overlay
(their sha256 below). Each case runs the old pair and this tree's pair in a sandbox tree each
(tests/remote_box.py) on the same port, against an engine stub that writes its argv and its whole
environment, NUL-separated, and answers /health. The sandbox's path is the only text replaced.

The test matrix:

  1  engine mode (QSE_MODE empty, inferred), with no overlay file, an empty one and one holding only
     a comment; chat mode; a weight profile (flashnext-3.05); a pack that serves text only: argv and
     environment equal to the old start's                     test_an_empty_overlay_starts_the_same_engine
  2  the negative control: an overlay with MAX_QUEUE=3 changes both (`--max-queue 3`, MAX_QUEUE=3),
     so the comparison sees a difference                       test_a_managed_key_reaches_the_engine

Run: python tests/test_remote_start_equal.py
"""

from __future__ import annotations

import hashlib
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import remote_box as R  # noqa: E402
from ports import free_port  # noqa: E402

PRE = os.path.join(HERE, "fixtures", "pre_ctl")
#: the fixture's files, the engine's at the commit before the overlay (sha256, first 16 hex digits)
PRE_FILES = {"serve.env": "827cf981eeef365d", "start.sh": "69685b4c9ec4573e"}

ENGINE = r'''
import http.server, json, os, sys
out = os.path.join(os.environ["REPO"], "seen")
with open(out + ".argv", "wb") as fh:
    fh.write(b"\0".join(a.encode() for a in sys.argv))
with open(out + ".env", "wb") as fh:
    fh.write(b"\0".join(f"{k}={v}".encode() for k, v in sorted(os.environ.items())))
port = int(sys.argv[sys.argv.index("--port") + 1])
me = os.path.join(os.environ["ENGINE_PROC"], str(os.getpid()))     # so the sandbox's cleanup stops it
os.makedirs(me, exist_ok=True)
open(os.path.join(me, "cmdline"), "wb").write(b"".join(a.encode() + b"\0" for a in [sys.executable, "-u"] + sys.argv))
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"{}")
    def log_message(self, *a):
        pass
http.server.HTTPServer(("127.0.0.1", port), H).serve_forever()
'''


def _sha(p: str) -> str:
    return hashlib.sha256(open(p, "rb").read()).hexdigest()[:16]


def _run(old: bool, port: int, mode: str | None, profile: str | None, overlay: str | None) -> tuple[bytes, bytes]:
    b = R.make(template=os.path.join(PRE, "serve.env") if old else None,
               start_sh=os.path.join(PRE, "start.sh") if old else None, engine=ENGINE, port=port)
    try:
        envf = os.path.join(b["root"], "ops", "serve.env")
        text = open(envf).read()
        if mode:
            text = R._sub(text, "QSE_MODE", mode)
        open(envf, "w").write(text)
        if profile:
            open(os.path.join(b["root"], "ops", "active-profile"), "w").write(profile + "\n")
            if profile == "textonly":
                open(os.path.join(b["root"], "ops", "profiles", "textonly.env"), "w").write("QSE_PACK_VISION=off\n")
        if overlay is not None:
            open(os.path.join(b["root"], "run", "managed.env"), "w").write(overlay)
        os.makedirs(os.path.join(b["home"], ".qwen38-spark-engine", "conductor"), exist_ok=True)
        import subprocess
        r = subprocess.run(["bash", os.path.join(b["root"], "ops", "start.sh")], capture_output=True, text=True,
                           env=R.env(b), timeout=120)
        assert r.returncode == 0, r.stdout + r.stderr
        t_end = time.time() + 10
        while not os.path.exists(os.path.join(b["root"], "seen.env")) and time.time() < t_end:
            time.sleep(0.1)
        argv = open(os.path.join(b["root"], "seen.argv"), "rb").read()
        env = open(os.path.join(b["root"], "seen.env"), "rb").read()
        for path, name in ((b["root"], b"<tree>"), (b["home"], b"<home>")):
            argv, env = argv.replace(path.encode(), name), env.replace(path.encode(), name)
        return argv, env
    finally:
        R.cleanup(b)


CASES = [("engine, no overlay file", None, None, None),
         ("engine, an empty overlay", None, None, ""),
         ("engine, a comment only", None, None, "# nothing set\n"),
         ("chat", "chat", None, ""),
         ("a weight profile", None, "flashnext-3.05", ""),
         ("a text-only pack", None, "textonly", "")]


def test_an_empty_overlay_starts_the_same_engine():
    for f, want in PRE_FILES.items():
        assert _sha(os.path.join(PRE, f)) == want, f
    for name, mode, profile, overlay in CASES:
        port = free_port()
        pre = _run(True, port, mode, profile, overlay)
        now = _run(False, port, mode, profile, overlay)
        assert pre[0] == now[0], (name, pre[0], now[0])
        a, b = (dict(x.split(b"=", 1) for x in e.split(b"\0")) for e in (pre[1], now[1]))
        assert a == b, (name, {k: (a.get(k), b.get(k)) for k in set(a) | set(b) if a.get(k) != b.get(k)})
        assert pre[1] == now[1], name
        assert b"--port" in now[0] and b"QSE_MANAGED" not in now[1], name
    return f"{len(CASES)} cases byte-equal"


def test_a_managed_key_reaches_the_engine():
    port = free_port()
    pre = _run(True, port, None, None, "MAX_QUEUE='3'\n")
    now = _run(False, port, None, None, "MAX_QUEUE='3'\n")
    assert b"--max-queue\x003\x00" in now[0] and b"--max-queue\x008\x00" in pre[0], (pre[0], now[0])
    assert b"\x00MAX_QUEUE=3\x00" in now[1] and b"\x00MAX_QUEUE=8\x00" in pre[1]
    return "the overlay is read (and the old tree did not)"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
