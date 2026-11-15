"""A sandbox engine tree for the `tandem remote` tests (engine-ctl-v1): no real engine, no box files.

`make()` writes a temporary tree with the release's own ops/serve.env (its machine lines pointed at the
sandbox: REPO, PY, PORT on a free port, HOME inside it), the real ops scripts, server/diskcache.py and
the profiles, a process table of its own (ENGINE_PROC, tests/test_ops_scripts.py's rule: the box's
engine is never seen or signalled) and a stub engine at server/app.py: a tiny HTTP server that answers
/health, enters itself in the sandbox's process table and exits before it answers when it is started
with `--max-queue 13` (a start that fails). `env(box)` is the environment the scripts and the CLI run
with. `call(box, *args)` runs `tandem remote` in a process of its own and returns (exit, answer).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from ports import free_port  # noqa: E402

SCRIPTS = ("start.sh", "stop.sh", "engines.sh", "roles.sh", "engine_status.py", "watchdog.sh", "hold.sh")

STUB = r'''
import http.server, json, os, signal, sys, time
args = sys.argv[1:]
port = int(args[args.index("--port") + 1])
proc = os.environ.get("ENGINE_PROC")
me = os.path.join(proc, str(os.getpid())) if proc else None
if me:
    os.makedirs(me, exist_ok=True)
    with open(os.path.join(me, "cmdline"), "wb") as fh:
        fh.write(b"".join(a.encode() + b"\0" for a in [sys.executable, "-u"] + sys.argv))
    os.symlink(sys.executable, os.path.join(me, "exe"))
def bye(*_):
    if me:
        import shutil
        shutil.rmtree(me, ignore_errors=True)
    os._exit(0)
signal.signal(signal.SIGTERM, bye)
if "--max-queue" in args and args[args.index("--max-queue") + 1] == "13":
    time.sleep(0.5)
    print("stub: refusing to start with --max-queue 13", flush=True)
    bye()
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"status": "ok", "engine_api": "1.8", "version": "stub", "model": "stub",
                           "extensions": [], "inflight": json.load(open(os.environ["STUB_INFLIGHT"]))
                           if os.path.exists(os.environ.get("STUB_INFLIGHT", "")) else {}}).encode()
        self.send_response(200 if self.path == "/health" else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a):
        pass
time.sleep(0.3)
http.server.HTTPServer(("127.0.0.1", port), H).serve_forever()
'''


def _sub(text: str, key: str, value: str) -> str:
    out, n = re.subn(rf"(?m)^{key}=.*$", f"{key}={value}", text, count=1)
    assert n == 1, key
    return out


def serve_env(template: str, box: str, port: int) -> str:
    text = open(template).read()
    for k, v in (("REPO", box), ("PY", sys.executable), ("PYTHONPATH", box), ("HOST", "127.0.0.1"),
                 ("PORT", str(port)), ("DROP_PAGE_CACHE", "0")):
        text = _sub(text, k, v)
    return text


def make(template: str | None = None, start_sh: str | None = None, engine: str | None = None,
         port: int | None = None, install: bool = False) -> dict:
    """A sandbox tree; `template` and `start_sh` replace the release's ops/serve.env and ops/start.sh,
    `engine` the stub (a script's text). `install`: the tree is DIR/src of an install (DIR has the
    marker, run/ops the scripts and serve.env, bin/tandem the control script install.sh writes, the
    venv's python this one, src/server the engine's server package)."""
    base = tempfile.mkdtemp(prefix="remote-inst-") if install else None
    box = os.path.join(base, "src") if base else tempfile.mkdtemp(prefix="remote-box-")
    os.makedirs(box, exist_ok=True)
    port = port or free_port()
    home_dir = tempfile.mkdtemp(prefix="remote-home-")        # beside the tree, as a HOME is
    for d in ("ops", "server", "logs", "proc", "bin", "run"):
        os.makedirs(os.path.join(box, d), exist_ok=True)
    for s in SCRIPTS:
        shutil.copy(os.path.join(ROOT, "ops", s), os.path.join(box, "ops", s))
    if start_sh:
        shutil.copy(start_sh, os.path.join(box, "ops", "start.sh"))
    shutil.copytree(os.path.join(ROOT, "ops", "profiles"), os.path.join(box, "ops", "profiles"))
    shutil.copy(os.path.join(ROOT, "server", "diskcache.py"), os.path.join(box, "server", "diskcache.py"))
    with open(os.path.join(box, "ops", "serve.env"), "w") as fh:
        fh.write(serve_env(template or os.path.join(ROOT, "ops", "serve.env"), box, port))
    with open(os.path.join(box, "server", "app.py"), "w") as fh:
        fh.write(engine or STUB)
    with open(os.path.join(box, "VERSION"), "w") as fh:
        fh.write("0.0.0-test\n")
    if not shutil.which("setsid"):                        # macOS has none; start.sh launches with it
        with open(os.path.join(box, "bin", "setsid"), "w") as fh:
            fh.write('#!/bin/bash\nexec "$@"\n')
        os.chmod(os.path.join(box, "bin", "setsid"), 0o755)
    b = {"root": box, "port": port, "home": home_dir}
    if base:
        b["install"] = base
        open(os.path.join(base, ".tandemllm-install"), "w").close()
        shutil.copytree(os.path.join(box, "ops"), os.path.join(base, "run", "ops"), ignore=shutil.ignore_patterns("profiles"))
        shutil.copytree(os.path.join(ROOT, "server"), os.path.join(box, "server"), dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "app.py"))
        # install.sh's render puts run/local.env's line just before the mode block (its awk)
        envf = os.path.join(base, "run", "ops", "serve.env")
        text = open(envf).read()
        i = text.index("# OPS-260: the mode (QSE_MODE above), applied last")
        local = os.path.join(base, "run", "local.env")
        with open(envf, "w") as fh:
            fh.write(text[:i] + f"if [ -f '{local}' ]; then . '{local}'; fi\n\n" + text[i:])
        os.makedirs(os.path.join(base, "venv", "bin"))
        os.symlink(sys.executable, os.path.join(base, "venv", "bin", "python"))
        text = open(os.path.join(ROOT, "install.sh")).read()
        fn = text[text.index("write_control() {"):text.index("\nCTL\n}\n") + len("\nCTL\n}\n")]
        os.makedirs(os.path.join(base, "bin"))
        r = subprocess.run(["bash", "-c", fn + '\nDIR="$1"; write_control', "-", base], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        with open(os.path.join(base, "bin", "tandem"), "w") as fh:
            fh.write(r.stdout)
    return b


class home:
    """The sandbox's HOME for an in-process test (serve.env's ${HOME}, the state directory)."""

    def __init__(self, b: dict):
        self.b, self.old = b, None

    def __enter__(self):
        self.old = os.environ.get("HOME")
        self.b.setdefault("home_before", self.old)        # cleanup() puts it back
        os.environ["HOME"] = self.b["home"]
        return self

    def __exit__(self, *exc):
        os.environ["HOME"] = self.old


def env(b: dict, **extra) -> dict:
    e = {"PATH": os.path.join(b["root"], "bin") + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"),
         "HOME": b["home"], "LANG": "C.UTF-8",
         "ENGINE_PROC": os.path.join(b["root"], "proc"), "BOX_LOCK": os.path.join(b["root"], "box.flock"),
         "STUB_INFLIGHT": os.path.join(b["root"], "inflight.json"), "PYTHONPATH": ROOT}
    e.update({k: str(v) for k, v in extra.items()})
    return e


def call(b: dict, *args, ssh: str | None = None, timeout: float = 120, **extra) -> tuple[int, dict | list]:
    """`tandem remote` on the sandbox: the arguments by hand, or `ssh` as SSH_ORIGINAL_COMMAND."""
    e = env(b, **extra)
    if ssh is not None:
        e["SSH_ORIGINAL_COMMAND"] = ssh
        e.setdefault("SSH_CONNECTION", "192.0.2.7 50000 192.0.2.1 22")
    code = ("import sys; from server.remote import cli; from server.remote.paths import Tree; "
            "sys.exit(cli.run(sys.argv[2:], tree=Tree(sys.argv[1])))")
    r = subprocess.run([sys.executable, "-c", code, b["root"], *args], capture_output=True, text=True,
                       env=e, cwd=ROOT, timeout=timeout)
    lines = [json.loads(x) for x in r.stdout.splitlines() if x.strip()]
    assert lines, (r.returncode, r.stdout, r.stderr)
    return r.returncode, (lines[0] if len(lines) == 1 else lines)


def journal(b: dict) -> list[dict]:
    p = os.path.join(b["root"], "run", "remote", "journal.jsonl")
    return [json.loads(x) for x in open(p)] if os.path.exists(p) else []


def wait_job(b: dict, seconds: float = 120) -> dict:
    """The newest job once it has ended."""
    t_end = time.time() + seconds
    while True:
        rc, st = call(b, "status")
        job = st.get("job") or {}
        if job.get("state") in ("done", "failed", "cancelled", "lost"):
            return job
        if time.time() > t_end:
            raise AssertionError(f"the job did not end in {seconds} s: {job}")
        time.sleep(0.5)


def cleanup(b: dict) -> None:
    """Every stub engine of the sandbox stopped (its process table names them), the tree removed, a
    HOME that `home` changed put back."""
    if b.get("home_before"):
        os.environ["HOME"] = b["home_before"]
    proc = os.path.join(b["root"], "proc")
    for d in os.listdir(proc) if os.path.isdir(proc) else []:
        if d.isdigit():
            try:
                os.kill(int(d), 9)
            except OSError:
                pass
    subprocess.run(["pkill", "-f", b["root"] + "/"], capture_output=True)
    # and every process that runs in the tree (an engine stub's argv names server/app.py alone)
    for d in os.listdir("/proc") if os.path.isdir("/proc/self") else []:
        if d.isdigit():
            try:
                cwd = os.readlink(f"/proc/{d}/cwd")
            except OSError:
                continue
            if cwd == b["root"] or cwd.startswith(b["root"] + "/"):
                try:
                    os.kill(int(d), 9)
                except OSError:
                    pass
    shutil.rmtree(b["root"], ignore_errors=True)
    shutil.rmtree(b["home"], ignore_errors=True)
    if b.get("install"):
        shutil.rmtree(b["install"], ignore_errors=True)
