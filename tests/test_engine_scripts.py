"""The engine's install scripts, run for real in temporary trees (SRV-748): the engine's parts of
tests that lived beside TandemApp's, moved here as they were apart from paths. TandemApp's tests
keep their own assertions (app/tests/test_add_spark.py, test_node_agent.py, test_chatd_sh.py,
test_chatd_ops.py).

  1  install.sh --runtime-only --dry-run stops after the Python environment: no weights, corpus
     or config                                                 test_install_sh_runtime_only_dry_run
  2  `tandem node` in the bin/tandem install.sh writes: the venv's tandem-node (ENG-413 WP7) with
     this install's engine, TandemNode's bind rule (`check`, `unit`, a refused 0.0.0.0)
                                                               test_tandem_node_in_bin_tandem
  3  make-secrets.sh writes where the engine reads, also from the setup guide's apply job, whose
     environment has no QSE_SECRETS                  test_make_secrets_writes_where_the_engine_reads
  4  make-secrets.sh writes the forward key, and adds it to an older file once
                          test_make_secrets_writes_the_forward_key_and_adds_it_to_an_old_file

CPU only, standard library only. Run: python tests/test_engine_scripts.py
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _out(r: subprocess.CompletedProcess) -> str:
    return r.stdout + r.stderr


class Tree:
    """DIR as install.sh makes it, as far as make-secrets.sh and the engine's start read it:
    DIR/run/ops/serve.env (with QSE_SECRETS=DIR/state/secrets.env) and DIR/run/local.env, and a
    HOME of its own."""

    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="engine-scripts-")
        self.dir = os.path.join(self.tmp, "TandemLLM")
        self.src = os.path.join(self.dir, "src")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.src)
        os.makedirs(self.home)
        open(os.path.join(self.dir, ".tandemllm-install"), "w").close()
        self.secrets = os.path.join(self.dir, "state", "secrets.env")
        os.makedirs(os.path.join(self.dir, "run", "ops"))
        with open(os.path.join(self.dir, "run", "ops", "serve.env"), "w") as fh:
            fh.write(f"REPO='{self.src}'\nPY='{self.dir}/venv/bin/python'\nPORT=18000\n"
                     f"QSE_SECRETS='{self.secrets}'\n"
                     f"if [ -f '{self.dir}/run/local.env' ]; then . '{self.dir}/run/local.env'; fi\n")
        with open(os.path.join(self.dir, "run", "local.env"), "w") as fh:
            fh.write("QSE_CHAT_URL=http://127.0.0.1:9\n")

    def env(self, **extra) -> dict:
        """What a user's shell has: no serve.env variables, no venv active."""
        e = {k: v for k, v in os.environ.items()
             if k not in ("PY", "REPO", "CHATD_PY", "VIRTUAL_ENV", "QSE_SECRETS", "QSE_STATE_DIR",
                          "QSE_CHAT_URL", "QSE_CHAT_DATA", "QSE_CHAT_FORWARD_KEY", "QSE_CHATD_ENV",
                          "PORT")}
        e["HOME"] = self.home
        e.update({k: str(v) for k, v in extra.items() if v is not None})
        for k in [k for k, v in extra.items() if v is None]:
            e.pop(k, None)
        return e

    def close(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)


def _engine_env(t: Tree) -> dict:
    """start.sh's environment: serve.env and local.env, then the secrets file, all exported."""
    out = subprocess.run(["bash", "-c", 'set -a; . "$1"; S="${QSE_SECRETS:-}"; [ -f "$S" ] && . "$S"; '
                          'env -0', "-", os.path.join(t.dir, "run", "ops", "serve.env")],
                         capture_output=True, env={"HOME": t.home, "PATH": os.environ["PATH"]},
                         check=True).stdout.decode()
    return dict(kv.split("=", 1) for kv in out.split("\0") if "=" in kv)


# ------------------------------------------- from app/tests/test_add_spark.py (test 12, its half)

def test_install_sh_runtime_only_dry_run():
    d = tempfile.mkdtemp(prefix="add-rt-")
    r = subprocess.run(["bash", os.path.join(ROOT, "install.sh"), "--runtime-only", "--dry-run",
                        "--yes", "--dir", os.path.join(d, "engine")], capture_output=True,
                       text=True, timeout=300, env=dict(os.environ, HOME=d))
    out = r.stdout + r.stderr
    assert r.returncode == 0, out[-2000:]
    assert "[3/3]" in out and "[4/" not in out and "Weights" not in out, out[-2000:]
    assert "No weights, corpus or config yet" in out
    shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------ from app/tests/test_node_agent.py (test 5, its tail)

def test_tandem_node_in_bin_tandem():
    # `tandem node` in the bin/tandem install.sh writes: this install's engine, the same rule
    d = tempfile.mkdtemp(prefix="node-install-")
    text = open(os.path.join(ROOT, "install.sh")).read()
    fn = text[text.index("write_control() {"):text.index("\nCTL\n}\n") + len("\nCTL\n}\n")]
    script = subprocess.run(["bash", "-c", fn + '\nDIR="$1"; write_control', "-", d],
                            capture_output=True, text=True, check=True).stdout
    os.makedirs(os.path.join(d, "bin"))
    os.makedirs(os.path.join(d, "venv", "bin"))
    os.symlink(sys.executable, os.path.join(d, "venv", "bin", "python"))
    os.symlink(ROOT, os.path.join(d, "src"))
    # ENG-413 WP7: bin/tandem runs the venv's tandem-node (install.sh --with-chat --app-wheel puts
    # it there); here TandemApp's agent from its source stands in for the wheel's entry point
    node = os.path.join(d, "venv", "bin", "tandem-node")
    with open(node, "w") as f:
        f.write(f'#!/bin/bash\nPYTHONPATH={os.path.join(ROOT, "app")} exec {sys.executable} -m proxy.nodeagent "$@"\n')
    os.chmod(node, 0o755)
    with open(os.path.join(d, "bin", "tandem"), "w") as f:
        f.write(script)
    ctl = ["bash", os.path.join(d, "bin", "tandem"), "node"]
    state = ["--state-dir", os.path.join(d, "node")]
    r = subprocess.run(ctl + ["check"] + state, capture_output=True, text=True, timeout=60)
    lines = r.stdout.strip().splitlines()
    assert r.returncode == 0 and lines[0] == \
        "127.0.0.1:7700 secret (plain HTTP, the loopback secret)", (r.stdout, r.stderr)
    assert len(lines) == 2 and lines[1].startswith("mDNS: "), lines     # SRV-727
    r = subprocess.run(ctl + ["unit"] + state, capture_output=True, text=True, timeout=60)
    assert f"--tandem-dir {d}" in r.stdout, r.stdout
    r = subprocess.run(ctl + ["check", "--host", "0.0.0.0"] + state, capture_output=True,
                       text=True, timeout=60)
    assert r.returncode != 0 and "unspecified" in r.stderr, (r.stdout, r.stderr)


# --------------------------------------------- from app/tests/test_chatd_sh.py (test 6, its half)

def test_make_secrets_writes_where_the_engine_reads():
    t = Tree()
    try:
        ops = os.path.join(t.dir, "run", "ops")
        shutil.copy(os.path.join(ROOT, "ops", "make-secrets.sh"), ops)
        # QSE_SECRETS set (what the engine's environment has)
        own = os.path.join(t.tmp, "own", "secrets.env")
        subprocess.run(["bash", os.path.join(ops, "make-secrets.sh")], check=True, capture_output=True,
                       env=t.env(QSE_SECRETS=own))
        assert "QSE_CHAT_FORWARD_KEY=" in open(own).read()
        # the setup guide's apply job: its environment keeps PATH, HOME and a few more, never
        # QSE_SECRETS (server/setup/apply.py Job.env); the file is still serve.env's
        apply_env = {"PATH": os.environ["PATH"], "HOME": t.home, "LANG": "C"}
        r = subprocess.run(["bash", os.path.join(ops, "make-secrets.sh"), "--admin"],
                           capture_output=True, text=True, env=apply_env)
        assert r.returncode == 0 and f"wrote {t.secrets}" in r.stdout, _out(r)
        text = open(t.secrets).read()
        assert "QSE_ADMIN_TOKEN=" in text and "QSE_CHAT_FORWARD_KEY=" in text
        assert not os.path.exists(os.path.join(t.home, ".qwen38-spark-engine")), \
            "make-secrets.sh wrote a file the installed engine never reads"
        assert oct(os.stat(t.secrets).st_mode & 0o777) == "0o600"
        # and the engine reads that file: the admin password reaches it
        env = _engine_env(t)
        assert len(env.get("QSE_ADMIN_TOKEN", "")) == 64
    finally:
        t.close()


# --------------------------------------------------- from app/tests/test_chatd_ops.py (test 1)

def test_make_secrets_writes_the_forward_key_and_adds_it_to_an_old_file():
    d = tempfile.mkdtemp()
    env = dict(os.environ, QSE_STATE_DIR=d)
    subprocess.run(["bash", os.path.join(ROOT, "ops", "make-secrets.sh")], env=env, check=True,
                   capture_output=True)
    text = open(os.path.join(d, "secrets.env")).read()
    key = [ln.split("=", 1)[1] for ln in text.splitlines() if ln.startswith("QSE_CHAT_FORWARD_KEY=")]
    assert len(key) == 1 and len(key[0]) == 64
    assert stat.S_IMODE(os.stat(os.path.join(d, "secrets.env")).st_mode) == 0o600
    # a file from before OPS-103 gets the line, once, and keeps what it had
    with open(os.path.join(d, "secrets.env"), "w") as fh:
        fh.write("QSE_METRICS_TOKEN=" + "m" * 64 + "\n")
    for _ in range(2):
        subprocess.run(["bash", os.path.join(ROOT, "ops", "make-secrets.sh")], env=env,
                       check=True, capture_output=True)
    text = open(os.path.join(d, "secrets.env")).read()
    assert text.startswith("QSE_METRICS_TOKEN=" + "m" * 64)
    assert text.count("QSE_CHAT_FORWARD_KEY=") == 1


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  {name:72s} ok")
            passed += 1
    print(f"{passed} passed")
