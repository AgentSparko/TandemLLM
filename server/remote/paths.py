"""Where `tandem remote` finds things: the engine's tree, an install around it, and its own files.

An install is DIR/src (this tree) with DIR/.tandemllm-install, its scripts and serve.env in
DIR/run/ops, an operator's DIR/run/local.env and the control script DIR/bin/tandem
(server/setup/doctor.py `layout`); a checkout has ops/ beside the code. Either way the engine's
own state is the tree's run/, where the Setup Guide keeps run/setup.env and serve.env reads the
managed overlay:

    run/managed.env            the current version of the managed overlay (none: empty)
    run/managed.d/<n>.env      every version, and manifest.json (current, good, the list)
    run/remote/journal.jsonl   one line a call (server/remote/journal.py)
    run/remote/jobs/<id>/      a lifecycle job: job.json and runner.out
    run/remote/stopped         a stop on purpose: ops/watchdog.sh leaves the engine down
    run/remote/extensions.json the extensions `ext install` put into the venv (server/remote/ext.py)
    run/remote/upgrades/       a planned upgrade's archive, and each upgrade job's previous files
    run/http-token             the HTTP token's sha256 (`token rotate`; server/api/access.py)
    logs/launcher.log          what the jobs' ops/start.sh and ops/stop.sh printed

An install's own files beside the tree: DIR/venv (its Python) and DIR/run/requirements.lock (the
engine's pins, install.sh's).
"""

from __future__ import annotations

import os

from server.setup.doctor import layout

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class Tree:
    def __init__(self, root: str | None = None):
        lay = layout(root or ROOT)
        self.root = lay["root"]
        self.install = lay["install"]
        self.ops = lay["ops"]
        self.control = lay["control"]

    # the install's directory, else the checkout: path arguments must stay under it
    @property
    def base(self) -> str:
        return self.install or self.root

    @property
    def serve_env(self) -> str:
        return os.path.join(self.ops, "serve.env")

    @property
    def template(self) -> str:
        return os.path.join(self.root, "ops", "serve.env")

    @property
    def run(self) -> str:
        return os.path.join(self.root, "run")

    @property
    def local_env(self) -> str | None:
        return os.path.join(self.install, "run", "local.env") if self.install else None

    @property
    def setup_env(self) -> str:
        return os.path.join(self.run, "setup.env")

    @property
    def managed(self) -> str:
        return os.path.join(self.run, "managed.env")

    @property
    def managed_dir(self) -> str:
        return os.path.join(self.run, "managed.d")

    @property
    def remote(self) -> str:
        return os.path.join(self.run, "remote")

    @property
    def journal(self) -> str:
        return os.path.join(self.remote, "journal.jsonl")

    @property
    def jobs(self) -> str:
        return os.path.join(self.remote, "jobs")

    @property
    def stopped(self) -> str:
        return os.path.join(self.remote, "stopped")

    @property
    def logs(self) -> str:
        return os.path.join(self.root, "logs")

    @property
    def launcher_log(self) -> str:
        return os.path.join(self.logs, "launcher.log")

    @property
    def engine_log(self) -> str:
        return os.path.join(self.logs, "engine.log")

    @property
    def pid_file(self) -> str:
        return os.path.join(self.logs, "engine.pid")

    @property
    def extensions(self) -> str:
        return os.path.join(self.remote, "extensions.json")

    @property
    def upgrades(self) -> str:
        return os.path.join(self.remote, "upgrades")

    @property
    def http_token(self) -> str:
        return os.path.join(self.run, "http-token")

    # an install's venv and its pins (None in a checkout)
    @property
    def venv_python(self) -> str | None:
        return os.path.join(self.install, "venv", "bin", "python") if self.install else None

    @property
    def lock_file(self) -> str | None:
        return os.path.join(self.install, "run", "requirements.lock") if self.install else None

