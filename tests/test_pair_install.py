"""One command installs the engine and ends in pairing (ENG-448).

install.sh runs end to end into a HOME of its own, without sudo, from a release that is this
checkout's files as one commit of a repository of their own, with tests/test_fresh_install.py's and
tests/test_install_control.py's stand-ins (the GPU, nvcc, uv, the venv's Python, the weights'
download, curl to Hugging Face) and a pgrep that sees no other engine. The release's server/app.py
is a stub that answers /health, /v1/models, a streamed chat completion for the smoke test and the
real pairing routes (server/pairing over the sandbox's host keys and authorized_keys), so the
install's last step, bin/tandem pair, runs the real CLI against the real window. The engine binds
127.0.0.1 on a free port: nothing listens beyond loopback, nothing is advertised. The process table
is the test's own (ENGINE_PROC).

The test matrix (the Gherkin of ENG-448):

  1  Given a machine with nothing installed and no sudo, when the user runs the install command
     (--setup: no weights yet), then setup mode is running and the last lines print a 6-digit
     code, the SSH host key's fingerprint and the address; the window is open in the running
     process                                                 test_a_setup_install_ends_in_pairing
  2  the default install (weights, the engine, the smoke test) ends in the same three lines
                                                             test_a_plain_install_ends_in_pairing
  3  --no-pair: the install ends as before, no window, no code line, no lock; --no-start opens none
                                                             test_no_pair
  4  the address: with no --host a new install that pairs binds 0.0.0.0 and writes the HTTP
     token's lock before the start (the dry run says both); --host and --no-pair keep 127.0.0.1;
     a re-run keeps the address the config has                test_the_address_and_the_lock

Run: python tests/test_pair_install.py   (Linux only: install.sh stops anywhere else)
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import test_fresh_install as F  # noqa: E402
import test_install_control as I  # noqa: E402
from pair_client import host_etc  # noqa: E402
from ports import free_port  # noqa: E402

STUB = r'''
import json, os, signal, sys, time
repo = os.environ["REPO"]
sys.path.insert(0, repo)
sys.path.insert(0, os.path.join(repo, "tests"))
from http.server import ThreadingHTTPServer
a = sys.argv[1:]
host = a[a.index("--host") + 1] if "--host" in a else "127.0.0.1"
port = int(a[a.index("--port") + 1])
mode = "setup" if "--setup" in a else "engine"
proc = os.environ.get("ENGINE_PROC")
me = os.path.join(proc, str(os.getpid())) if proc else None
if me:
    os.makedirs(me, exist_ok=True)
    with open(os.path.join(me, "cmdline"), "wb") as fh:
        fh.write(b"".join(x.encode() + b"\0" for x in [sys.executable, "-u"] + sys.argv))
    os.symlink(sys.executable, os.path.join(me, "exe"))
def bye(*_):
    if me:
        import shutil
        shutil.rmtree(me, ignore_errors=True)
    os._exit(0)
signal.signal(signal.SIGTERM, bye)
from server.pairing.window import Service
from pair_client import _Handler
svc = Service(None, port=port, host=host, mode=mode, etc=os.environ["PAIR_TEST_ETC"],
              auth_keys=os.environ["PAIR_TEST_AUTH"])
class H(_Handler):
    state = {"pairing": svc}
    peer = None
    def _peer(self):
        return self.client_address[0]
    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/health":
            return self._json(200, {"status": "ok", "mode": mode})
        if p == "/v1/models":
            return self._json(200, {"object": "list", "data": [{"id": "stub"}]})
        return self._go("GET")
    def do_POST(self):
        if self.path.startswith("/v1/chat/completions"):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for piece in ("def fib", "(n):", " ..."):
                self.wfile.write(b"data: " + json.dumps({"choices": [{"delta": {"content": piece}}]}).encode() + b"\n\n")
            self.wfile.write(b'data: {"choices": [], "usage": {"completion_tokens": 3}}\n\ndata: [DONE]\n\n')
            return
        return self._go("POST")
print("stub engine:", mode, port, flush=True)
ThreadingHTTPServer((host, port), H).serve_forever()
'''

# a discrete GPU's answers (80 GiB, driver 580)
PLAIN_GPU = """#!/bin/bash
case "$*" in
  -L) echo "GPU 0: NVIDIA H100 (UUID: GPU-00000000-0000-0000-0000-000000000001)" ;;
  *query-gpu=name,driver_version,compute_cap,memory.total*) echo "NVIDIA H100, 580.95.05, 9.0, 81559" ;;
  *) exit 0 ;;
esac
"""
_BOX: dict = {}


def _linux() -> bool:
    return platform.system() == "Linux"


def _release() -> tuple[str, str, str, str]:
    """(release repo, its commit, the stand-ins' dir, the temp dir): this checkout with the stub app."""
    if "rel" in _BOX:
        return _BOX["rel"], _BOX["ref"], _BOX["stubs"], _BOX["tmp"]
    tmp = tempfile.mkdtemp(prefix="pair-install-")
    rel, stubs = os.path.join(tmp, "release"), os.path.join(tmp, "stubs")
    skip = shutil.ignore_patterns("node_modules", "__pycache__", "*.pdf")
    shutil.copytree(ROOT, rel, symlinks=True, ignore=lambda d, names: set(skip(d, names)) | (
        {".git", "run", "logs", "train"} & set(names) if os.path.samefile(d, ROOT) else set()))
    with open(os.path.join(rel, "server", "app.py"), "w") as fh:
        fh.write(STUB)
    git = ["git", "-C", rel, "-c", "user.name=t", "-c", "user.email=t@example.com"]
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "release"]):
        subprocess.run(git + cmd, check=True, capture_output=True)
    ref = subprocess.run(git + ["rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    for p, text in dict(F._STUBS, **{"bin/pgrep": "#!/bin/bash\nexit 1\n"}).items():
        F._write(os.path.join(stubs, p), text.encode())
        os.chmod(os.path.join(stubs, p), 0o755)
    _BOX.update(rel=rel, ref=ref, stubs=stubs, tmp=tmp)
    return rel, ref, stubs, tmp


def _cleanup() -> None:
    tmp = _BOX.pop("tmp", None)
    _BOX.clear()
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)


def teardown_module(module=None) -> None:
    _cleanup()


def install(name: str, *extra: str, setup: bool = True, host: str | None = "127.0.0.1",
            port: int | None = None) -> dict:
    rel, ref, stubs, tmp = _release()
    home = os.path.join(tmp, name)
    d = os.path.join(home, "TandemLLM")
    py = F._PY if setup else I._PLAIN_PY
    F._write(os.path.join(d, "venv/bin/python"), py.format(py=sys.executable).encode())
    os.chmod(os.path.join(d, "venv/bin/python"), 0o755)
    # no status responder: it outlives the engine's stop on purpose, and a test leaves no process
    F._write(os.path.join(d, "run", "local.env"), b"QSE_STATUS_PORT=\n")
    for f in ("config.json", "tokenizer.json"):
        F._write(os.path.join(home, ".qwen38-spark-engine/models/qwen3.5-2b", f), b"{}")
    mine = os.path.join(home, "stubs")
    F._write(os.path.join(mine, "curl"), I._CURL.format(curl=shutil.which("curl")).encode())
    os.chmod(os.path.join(mine, "curl"), 0o755)
    if not setup:
        # a board that is not a Spark: the plain install's memory check asks 16 GiB, not a Spark's 64
        F._write(os.path.join(mine, "nvidia-smi"), PLAIN_GPU.encode())
        os.chmod(os.path.join(mine, "nvidia-smi"), 0o755)
    proc = os.path.join(home, "proc")
    os.makedirs(proc, exist_ok=True)
    etc = host_etc(home)
    env = {"HOME": home, "PATH": mine + ":" + os.path.join(stubs, "bin") + ":" + os.environ.get("PATH", "/usr/bin:/bin"),
           "CUDA_HOME": os.path.join(stubs, "cuda"), "LANG": "C.UTF-8", "ENGINE_PROC": proc,
           "TANDEM_SKIP_MEMCHECK": "1", "PAIR_TEST_ETC": etc,
           "PAIR_TEST_AUTH": os.path.join(home, ".ssh", "authorized_keys")}
    port = port or free_port()
    args = ["bash", os.path.join(ROOT, "install.sh"), "--yes", "--no-corpus", "--dir", d, "--repo", rel, "--ref", ref,
            "--port", str(port), *(("--host", host) if host else ()), *extra]
    if setup:
        args.append("--setup")
    r = subprocess.run(args, env=env, capture_output=True, text=True, timeout=900, stdin=subprocess.DEVNULL)
    return {"home": home, "dir": d, "env": env, "port": port, "out": r.stdout, "err": r.stderr, "rc": r.returncode,
            "etc": etc, "args": args}


def _plain(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _stop(box: dict) -> None:
    """The stub engine down, by bin/tandem stop and then by its pid; the process table empty."""
    subprocess.run(["bash", os.path.join(box["dir"], "bin", "tandem"), "stop", "5"], env=box["env"],
                   capture_output=True, timeout=120, stdin=subprocess.DEVNULL)
    pidf = os.path.join(box["dir"], "src", "logs", "engine.pid")
    try:
        pid = int(open(pidf).read().strip())
        os.kill(pid, signal.SIGTERM)
        time.sleep(0.5)
    except (OSError, ValueError):
        pass
    for p in os.listdir(box["env"]["ENGINE_PROC"]):
        try:
            os.kill(int(p), signal.SIGKILL)
        except (OSError, ValueError):
            pass


def _ends_in_pairing(box: dict, mode: str) -> str:
    out = _plain(box["out"])
    assert box["rc"] == 0, out[-3000:] + box["err"][-3000:]
    lines = out.rstrip("\n").splitlines()
    code = re.fullmatch(r"  Code:\s+([0-9]{6})", lines[-3])
    assert code, lines[-8:]
    from server.pairing import hostkey
    fp = hostkey.preferred(box["etc"])["fingerprint"]
    assert lines[-2].startswith(f"  Fingerprint:   {fp}   ("), lines[-2]
    assert lines[-1] == f"  Address:       127.0.0.1:{box['port']}   (this machine only)", lines[-1]
    assert "[pair] a pairing window is open for 5 minutes (5 tries, single use)." in out
    # the window is open in the running process, with that code (the code is in no file)
    r = subprocess.run(["bash", os.path.join(box["dir"], "bin", "tandem"), "pair", "--status"], env=box["env"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and r.stdout.startswith("[pair] window: open"), r.stdout + r.stderr
    import urllib.request
    with urllib.request.urlopen(f"http://127.0.0.1:{box['port']}/health", timeout=5) as h:
        assert f'"mode": "{mode}"' in h.read().decode()
    # the code is on no disk: not in the journal, the run/ files or the engine's log
    for d in (os.path.join(box["dir"], "run"), os.path.join(box["dir"], "src", "run"),
              os.path.join(box["dir"], "src", "logs")):
        for root, _dirs, files in os.walk(d):
            for f in files:
                p = os.path.join(root, f)
                if os.path.isfile(p) and not os.path.islink(p):
                    assert code.group(1) not in open(p, "rb").read().decode("latin-1"), f"the code is in {p}"
    return code.group(1)


def test_a_setup_install_ends_in_pairing():
    if not _linux():
        print("    (skipped: install.sh runs on Linux only -- run it on the box)")
        return "skipped off Linux"
    box = install("setup")
    try:
        _ends_in_pairing(box, "setup")
        assert not os.path.exists(os.path.join(box["dir"], "src", "run", "http-token"))   # --host 127.0.0.1
        return "setup mode running; code, fingerprint, address last"
    finally:
        _stop(box)


def test_a_plain_install_ends_in_pairing():
    if not _linux():
        print("    (skipped: install.sh runs on Linux only -- run it on the box)")
        return "skipped off Linux"
    avail = int(next(ln.split()[1] for ln in open("/proc/meminfo") if ln.startswith("MemAvailable:"))) >> 20
    if avail < 17:
        print(f"    (skipped: {avail} GiB available; the plain install's check asks 16)")
        return f"skipped: {avail} GiB available"
    box = install("plain", setup=False)
    try:
        _ends_in_pairing(box, "engine")
        out = _plain(box["out"])
        assert "[smoke] 3 tokens" in out and out.index("Done.") < out.index("Pair with TandemApp"), out[-2000:]
        return "engine and smoke, then code, fingerprint, address last"
    finally:
        _stop(box)


def test_no_pair():
    if not _linux():
        print("    (skipped: install.sh runs on Linux only -- run it on the box)")
        return "skipped off Linux"
    box = install("nopair", "--no-pair")
    try:
        out = _plain(box["out"])
        assert box["rc"] == 0, out[-3000:] + box["err"][-3000:]
        assert "Code:" not in out and "Pair with TandemApp" not in out and "[pair]" not in out, out[-1500:]
        assert out.rstrip().splitlines()[-1].startswith("Engine only, the default") or "Setup mode" in out
        assert not os.path.exists(os.path.join(box["dir"], "src", "run", "http-token"))
        r = subprocess.run(["bash", os.path.join(box["dir"], "bin", "tandem"), "pair", "--status"], env=box["env"],
                           capture_output=True, text=True, timeout=60)
        assert r.stdout.startswith("[pair] window: none"), r.stdout + r.stderr
    finally:
        _stop(box)
    box = install("nostart", "--no-start", host=None)
    out = _plain(box["out"])
    assert box["rc"] == 0 and "Pair with TandemApp" not in out and "Code:" not in out, out[-1500:]
    assert "HOST='127.0.0.1'" in open(os.path.join(box["dir"], "run", "ops", "serve.env")).read()
    return "--no-pair: no window, no lock; --no-start: none, 127.0.0.1"


def test_the_address_and_the_lock():
    if not _linux():
        print("    (skipped: install.sh runs on Linux only -- run it on the box)")
        return "skipped off Linux"
    dry = install("dry", "--dry-run", host=None)
    out = _plain(dry["out"])
    assert dry["rc"] == 0, out[-2000:] + dry["err"][-2000:]
    assert "--setup --host 0.0.0.0" in out and "run/http-token: the lock line" in out, out[-2500:]
    assert "bin/tandem pair   (a pairing window for TandemApp" in out
    for extra, host in ((("--no-pair",), None), ((), "127.0.0.1")):
        d = install("dry-" + ("nopair" if extra else "host"), "--dry-run", *extra, host=host)
        o = _plain(d["out"])
        assert d["rc"] == 0 and "--setup --host 127.0.0.1" in o and "the lock line" not in o, o[-2000:]
    # a re-run keeps the address its config has: a config with 0.0.0.0, re-run with no --host
    box = install("rerun", "--no-start", host="0.0.0.0")
    assert box["rc"] == 0, box["out"][-2000:]
    again = subprocess.run([a for a in box["args"] if a not in ("--host", "0.0.0.0", "--no-start")] + ["--dry-run"],
                           env=box["env"], capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
    o = _plain(again.stdout)
    assert again.returncode == 0 and "--setup --host 0.0.0.0" in o and "the lock line" not in o, o[-2000:]
    return "new: 0.0.0.0 and the lock; --host / --no-pair: 127.0.0.1; a re-run keeps 0.0.0.0, no lock"


if __name__ == "__main__":
    from scriptmode import run
    try:
        run(globals())
    finally:
        _cleanup()
