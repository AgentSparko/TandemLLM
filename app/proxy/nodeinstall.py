"""Install over SSH (VIS-246, the Add Spark wizard's Install step): TandemNode and the engine at the
fleet's version on a box with nothing installed, then its pairing; progress per step and a log.

    pkg = Package(state_dir)              the wheel this controller installs (built once per code)
    job = Install(trust, target, secret, version=..., options=..., on_change=fn)
    job.run()                             the steps in order, in the caller's thread (a job's own)
    job.view()                            {state, version, steps: [{id, label, state, detail}], log}

THE PACKAGE. Nothing is published yet, so a node gets what this controller runs: one wheel of the
tandemapp distribution (proxy/_version.py DIST), `tandemapp-<version>-py3-none-any.whl`, built
here from this controller's own copy (its checkout, read as pyproject.toml lists the packages and
their data, or the installed distribution's own files) with the standard library's zipfile, no
setuptools and no network. It carries TandemNode (`tandem-node`) and the controller, the packages
a `pip install` of app/ gives, and no engine code. Its only dependency, cryptography, comes from
the node's own package index when pip installs the wheel there. TANDEM_NODE_WHEEL names a wheel
file to send instead (a release once there is one). The wheel is cached by version and content
hash in `<config dir>/packages/`.

THE STEPS, each one SSH login with the credentials the wizard holds for this install (the password
in memory only, proxy/addspark.py), each line it prints kept in the log:

    check     the box again (proxy/preflight.py SCRIPT): another TandemLLM there refuses the install
    upload    the wheel to <prefix>/pkg, its sha256 checked there
    agent     python3 -m venv <prefix>/venv and pip install the wheel; the version it reports must
              be the fleet's
    engine    the engine's runtime: the engine's install.sh (TANDEM_ENGINE_INSTALL_SH), run there
              with --runtime-only at the engine ref (Package.engine_ref; the engine's source and its
              Python environment with the CUDA 13.0 torch; no weights, no corpus, no config: Serve a
              model, step 6, brings those). Without TANDEM_ENGINE_INSTALL_SH the step fails and says
              so. Off with `engine: false`
    unit      `tandem-node unit` written to ~/.config/systemd/user/<unit>.service, enabled and
              started; the agent must answer on its loopback within 30 s
    pair      SSH once's adopt (proxy/fleettrust.py) with the installed tandem-node: the node is
              paired and its certificate pinned

THE ENGINE. TandemApp carries no engine: the controller's operator names the engine's installer,
TANDEM_ENGINE_INSTALL_SH (a path on this machine; the fleet update sends the same file), and the
release it installs is `engine_ref()`: TANDEM_ENGINE_REF (a tag), else `v` and the VERSION file
beside the installer, else `v` and this controller's own release. The installer goes to the box as
the login's input, never on a command line; the ref is a checked tag name, quoted there.

The prefix is ~/.local/share/tandemllm, the agent's state ~/.tandemllm/node, its port 7700 and the
unit `tandemnode`, unless `options` say otherwise (a virtual node, OPS-197: its own of each). The
engine weights are never installed here. A step that fails stops the job with its last lines; a
new Install starts again from `check`, and each step is safe to repeat.
"""

from __future__ import annotations

import base64
import csv
import fnmatch
import glob
import hashlib
import io
import os
import re
import shlex
import threading
import time
import zipfile

from proxy import _version, preflight, sshonce

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULTS = {"prefix": "~/.local/share/tandemllm", "state_dir": "~/.tandemllm/node", "port": 7700,
            "unit": "tandemnode", "virtual": None, "engine": True}
STEPS = (("check", "Check the box"), ("upload", "Upload TandemLLM {v}"),
         ("agent", "Install TandemNode {v}"), ("engine", "Install the engine's runtime {ref}"),
         ("unit", "Start the agent ({unit}.service)"), ("pair", "Pair and pin its certificate"))
LOG_MAX = 400
UNIT_WAIT_S = 30
#: the engine's installer and the release it installs come from the controller's environment
INSTALL_SH_ENV, ENGINE_REF_ENV = "TANDEM_ENGINE_INSTALL_SH", "TANDEM_ENGINE_REF"
#: an engine ref as install.sh hands it to git: a tag name, a letter or digit first
REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


class InstallError(Exception):
    def __init__(self, kind: str, message: str, status: int = 409):
        super().__init__(message)
        self.kind, self.message, self.status = kind, message, status


def same_version(a: str | None, b: str | None) -> bool:
    """One release (proxy/preflight.py same_release): -test and .dev0, a local label aside."""
    return preflight.same_release(a, b)


def tag_ok(ref: str) -> bool:
    """A tag name install.sh can take for --ref: letters, digits, '.', '_' and '-', a letter or
    digit first (never an option to git), at most 64 characters, no '..'."""
    return bool(REF.fullmatch(ref or "")) and ".." not in ref


# ---------------------------------------------------------------------- the package

def _toml(path: str) -> dict:
    import tomllib
    with open(path, "rb") as f:
        return tomllib.load(f)


def _record_hash(data: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")


def checkout_files(root: str) -> tuple[dict, list[tuple[str, str]]]:
    """(project table, [(path in the wheel, file)]) of a checkout, as pyproject.toml says it."""
    doc = _toml(os.path.join(root, "pyproject.toml"))
    st = doc["tool"]["setuptools"]
    dirs = st.get("package-dir") or {}
    data = st.get("package-data") or {}
    out = []
    for pkg in st["packages"]:
        src = os.path.join(root, dirs.get(pkg) or pkg.replace(".", os.sep))
        dest = pkg.replace(".", "/")
        pats = data.get(pkg, [])
        for name in sorted(os.listdir(src)):
            p = os.path.join(src, name)
            if not os.path.isfile(p):
                continue
            if name.endswith(".py") or any(fnmatch.fnmatch(name, x) for x in pats
                                           if "/" not in x):
                out.append((f"{dest}/{name}", p))
        for x in pats:
            if "/" not in x:
                continue
            for p in sorted(glob.glob(os.path.join(src, x), recursive=True)):
                if os.path.isfile(p) and "__pycache__" not in p:
                    out.append((f"{dest}/{os.path.relpath(p, src).replace(os.sep, '/')}", p))
    seen, uniq = set(), []
    for w, p in out:
        if w not in seen:
            seen.add(w)
            uniq.append((w, p))
    return doc["project"], uniq


def installed_files() -> tuple[dict, list[tuple[str, str]]]:
    """The same of the installed distribution (no checkout): its own RECORD's files."""
    from importlib import metadata
    dist = metadata.distribution(_version.DIST)
    proj = {"name": _version.DIST, "dependencies": dist.requires or [],
            "scripts": {e.name: e.value for e in dist.entry_points
                        if e.group == "console_scripts"},
            "description": dist.metadata.get("Summary") or ""}
    out = []
    for f in dist.files or []:
        s = str(f)
        if ".dist-info/" in s or s.startswith("..") or "__pycache__" in s:
            continue
        out.append((s, str(f.locate())))
    return proj, out


def build_wheel(out_dir: str, version: str, root: str = ROOT) -> str:
    """The wheel of this controller's copy in `out_dir`; its path. Cached by content."""
    if os.path.isfile(os.path.join(root, "pyproject.toml")):
        proj, files = checkout_files(root)
        scripts = proj.get("scripts") or {}
    else:
        proj, files = installed_files()
        scripts = proj.get("scripts") or {}
    v = _version.pep440(version)
    h = hashlib.sha256()
    for w, p in files:
        h.update(w.encode())
        with open(p, "rb") as f:
            h.update(hashlib.sha256(f.read()).digest())
    tag = h.hexdigest()[:12]
    os.makedirs(out_dir, mode=0o700, exist_ok=True)
    name = f"{_version.DIST}-{v}-py3-none-any.whl"
    path = os.path.join(out_dir, tag, name)
    if os.path.isfile(path):
        return path
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    di = f"{_version.DIST}-{v}.dist-info"
    meta = [f"Metadata-Version: 2.1", f"Name: {_version.DIST}", f"Version: {v}",
            f"Summary: {proj.get('description') or 'TandemApp'}", "Requires-Python: >=3.11"]
    meta += [f"Requires-Dist: {d}" for d in proj.get("dependencies") or []]
    extra = {
        f"{di}/METADATA": ("\n".join(meta) + "\n").encode(),
        f"{di}/WHEEL": b"Wheel-Version: 1.0\nGenerator: tandem-app (proxy/nodeinstall.py)\n"
                       b"Root-Is-Purelib: true\nTag: py3-none-any\n",
        f"{di}/entry_points.txt": ("[console_scripts]\n" + "".join(
            f"{k} = {val}\n" for k, val in scripts.items())).encode(),
    }
    rec = io.StringIO()
    wr = csv.writer(rec, lineterminator="\n")
    tmp = path + ".part"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for w, p in files:
            with open(p, "rb") as f:
                data = f.read()
            z.writestr(w, data)
            wr.writerow([w, _record_hash(data), len(data)])
        for w, data in extra.items():
            z.writestr(w, data)
            wr.writerow([w, _record_hash(data), len(data)])
        wr.writerow([f"{di}/RECORD", "", ""])
        z.writestr(f"{di}/RECORD", rec.getvalue())
    os.replace(tmp, path)
    return path


class Package:
    """The wheel this controller sends (TANDEM_NODE_WHEEL, else built from its own copy), and the
    engine's installer and release that go with it (TANDEM_ENGINE_INSTALL_SH, engine_ref)."""

    def __init__(self, cache_dir: str, *, version: str | None = None, root: str = ROOT,
                 env=os.environ):
        self.cache_dir, self.root = cache_dir, root
        self.version = version or _version.release()
        self.env = env
        self._lock = threading.Lock()

    def wheel(self) -> str:
        given = self.env.get("TANDEM_NODE_WHEEL")
        if given:
            if not os.path.isfile(given):
                raise InstallError("no_package", f"TANDEM_NODE_WHEEL {given}: no such file", 500)
            return given
        with self._lock:
            try:
                return build_wheel(self.cache_dir, self.version, self.root)
            except (OSError, KeyError, ValueError, ImportError) as exc:
                raise InstallError("no_package", f"this controller cannot build its wheel "
                                                 f"({type(exc).__name__}: {exc}); set "
                                                 f"TANDEM_NODE_WHEEL to a {_version.DIST} wheel",
                                   500)

    def target(self) -> str:
        """The version the wheel carries, as a node reports it once installed (PEP 440, a local
        label kept): the update's target (proxy/fleetupdate.py)."""
        given = self.env.get("TANDEM_NODE_WHEEL")
        if given:
            v = wheel_version(given)
            if v:
                return v
        return _version.pep440(self.version) if self.version else ""

    def install_sh_path(self) -> str | None:
        """The engine's installer this controller sends (Add Spark's engine step, the fleet
        update): the file TANDEM_ENGINE_INSTALL_SH names, None while that is unset. TandemApp's
        own copy has none."""
        p = (self.env.get(INSTALL_SH_ENV) or "").strip()
        return os.path.expanduser(p) if p else None

    def install_sh(self) -> bytes | None:
        """That file's bytes; None while TANDEM_ENGINE_INSTALL_SH is unset. A file it names that
        cannot be read is an InstallError that names both."""
        p = self.install_sh_path()
        if p is None:
            return None
        try:
            with open(p, "rb") as f:
                return f.read()
        except OSError as exc:
            raise InstallError("no_installer", f"{INSTALL_SH_ENV} {p}: "
                                               f"{exc.strerror or type(exc).__name__}", 500)

    def engine_ref(self) -> str:
        """The engine release install.sh installs (--ref): TANDEM_ENGINE_REF; else `v` and the
        VERSION file beside TANDEM_ENGINE_INSTALL_SH, so that the ref names the installer sent;
        else `v` and this controller's release. A local label (`+build`) is its release's tag.
        A value that makes no tag name (tag_ok) is an InstallError naming where it came from."""
        given = (self.env.get(ENGINE_REF_ENV) or "").strip()
        if given:
            if not tag_ok(given):
                raise InstallError("bad_engine_ref",
                                   f"{ENGINE_REF_ENV} {given[:80]!r} is not a tag name: letters, "
                                   f"digits, '.', '_' and '-', a letter or digit first, at most "
                                   f"64, no '..'", 500)
            return given
        sh = self.install_sh_path()
        if sh is not None:
            vf = os.path.join(os.path.dirname(os.path.abspath(sh)), "VERSION")
            try:
                with open(vf) as f:
                    v = f.read().strip()
            except FileNotFoundError:
                v = None                         # an installer alone: this controller's release
            except OSError as exc:
                raise InstallError("bad_engine_ref", f"{vf}: {exc.strerror or type(exc).__name__}",
                                   500)
            if v is not None:
                ref = "v" + v.split("+", 1)[0]
                if not tag_ok(ref):
                    raise InstallError("bad_engine_ref", f"{vf} says {v[:80]!r}: no release name "
                                                         f"for install.sh's --ref", 500)
                return ref
        ref = "v" + (self.version or "").strip().split("+", 1)[0]
        if ref == "v" or not tag_ok(ref):
            raise InstallError("bad_engine_ref", f"no engine ref: this controller's release is "
                                                 f"{self.version!r}; set {ENGINE_REF_ENV}", 500)
        return ref

    def engine_ref_or_none(self) -> str | None:
        """engine_ref(), None when it refuses its inputs: the step that runs install.sh says why."""
        try:
            return self.engine_ref()
        except InstallError:
            return None


# ---------------------------------------------------------------------- one install

def _q(s: str) -> str:
    """A path for the remote shell: `~/` stays the remote home."""
    if s.startswith("~/"):
        return '"$HOME"/' + shlex.quote(s[2:])
    return shlex.quote(s)


class Install:
    """One node's install. `target`: {host, port, user, jumps, key_file, tunnel, host_key};
    `secret`: {"password": ...} or {"key": ...} or {} (key file or agent), dropped at the end."""

    def __init__(self, trust, target: dict, secret: dict, *, package: Package, version: str,
                 options: dict | None = None, on_change=None, who: str = "gui",
                 clock=time.time):
        self.trust, self.t = trust, dict(target)
        self._secret = dict(secret or {})
        self.pkg, self.version = package, version
        self.o = dict(DEFAULTS, **{k: v for k, v in (options or {}).items() if v is not None})
        self.on_change = on_change or (lambda view: None)
        self.who, self.clock = who, clock
        self.state = "queued"
        self.error: dict | None = None
        self.node: str | None = None
        self.started_at = self.ended_at = None
        ref = package.engine_ref_or_none() if self.o["engine"] else None
        self.steps = [{"id": sid, "label": label.format(v=version, unit=self.o["unit"],
                                                        ref=ref or "").strip(),
                       "state": "skip" if sid == "engine" and not self.o["engine"] else "todo",
                       "detail": None, "started_at": None, "ended_at": None}
                      for sid, label in STEPS]
        self.log: list[dict] = []
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ what it shows
    def view(self) -> dict:
        with self._lock:
            return {"state": self.state, "version": self.version, "node": self.node,
                    "error": self.error, "started_at": self.started_at,
                    "ended_at": self.ended_at, "options": {k: self.o[k] for k in
                                                           ("prefix", "port", "unit", "engine")},
                    "steps": [dict(s) for s in self.steps], "log": list(self.log[-LOG_MAX:])}

    def _say(self, step: str, text: str, level: str = "info") -> None:
        secrets = [v for v in self._secret.values() if v]
        for line in (text or "").splitlines():
            line = sshonce.scrub(line.rstrip(), secrets)
            if line and not line.startswith(("TANDEM-PF ", "TANDEMNODE-ADOPTED ")):
                with self._lock:
                    self.log.append({"t": round(self.clock(), 3), "step": step,
                                     "level": level, "text": line[:400]})

    def _step(self, sid: str, state: str, detail: str | None = None) -> None:
        with self._lock:
            s = next(x for x in self.steps if x["id"] == sid)
            s["state"] = state
            if detail is not None:
                s["detail"] = detail
            if state == "running":
                s["started_at"] = round(self.clock(), 3)
            elif state in ("done", "failed"):
                s["ended_at"] = round(self.clock(), 3)
        self.on_change(self.view())

    # ------------------------------------------------------------------ the logins
    def _ssh(self, step: str, command: str, stdin: bytes = b"") -> tuple[int, str, str]:
        t = self.t
        hk, b64 = self.trust.ssh.host_key(t["host"], t["port"], t.get("jumps") or None)
        if hk["known"] == "changed" or (t.get("host_key") and hk["fingerprint"] != t["host_key"]):
            raise InstallError("host_key_changed", f"the host key of {t['host']} is "
                                                   f"{hk['fingerprint']} now, not the confirmed "
                                                   f"{t.get('host_key') or hk['stored']}; "
                                                   f"refused")
        rc, out, err = self.trust.ssh.run(
            t["host"], t["port"], t["user"], command, stdin, kind=hk["type"], b64=b64,
            password=self._secret.get("password"), key=self._secret.get("key"),
            key_file=t.get("key_file"), jumps=t.get("jumps") or None)
        self._say(step, out)
        self._say(step, err, "warn" if rc else "info")
        return rc, out, err

    def _fail(self, sid: str, kind: str, message: str) -> None:
        self._step(sid, "failed", message)
        raise InstallError(kind, message)

    # ------------------------------------------------------------------ the steps
    def run(self) -> dict:
        with self._lock:
            self.state, self.started_at = "running", round(self.clock(), 3)
        self.on_change(self.view())
        try:
            for sid, _ in STEPS:
                if next(s for s in self.steps if s["id"] == sid)["state"] == "skip":
                    continue
                self._step(sid, "running")
                detail = getattr(self, "_" + sid)()
                self._step(sid, "done", detail)
            with self._lock:
                self.state = "done"
        except InstallError as exc:
            with self._lock:
                self.state, self.error = "failed", {"type": exc.kind, "message": exc.message}
                for s in self.steps:
                    if s["state"] == "running":
                        s["state"], s["detail"] = "failed", exc.message
        except sshonce.SshError as exc:
            with self._lock:
                self.state, self.error = "failed", {"type": exc.kind, "message": exc.message}
                for s in self.steps:
                    if s["state"] == "running":
                        s["state"], s["detail"] = "failed", exc.message
        finally:
            self._secret = {}                   # the password or key: forgotten here
            with self._lock:
                self.ended_at = round(self.clock(), 3)
            self.on_change(self.view())
        return self.view()

    def _check(self) -> str:
        env = f"TANDEM_PF_PREFIX={_q(self.o['prefix'])}; export TANDEM_PF_PREFIX\n"
        rc, out, _ = self._ssh("check", env + preflight.SCRIPT)
        f = preflight.parse(out)
        if not f:
            self._fail("check", "ssh_failed", "the box did not run the check")
        have = (f.get("tandemllm") or "").strip()
        if have and not same_version(have, self.version):
            self._fail("check", "version_mismatch", f"{self.t['host']} has TandemLLM {have}; the "
                                                    f"fleet runs {self.version}: update it there "
                                                    f"first. Install refuses another version")
        rows = preflight.checks(f, self.version, self.clock())
        bad = [r for r in rows if r["state"] == "fail" and r["id"] != "tandemllm"]
        if bad:
            self._fail("check", "preflight_failed", "; ".join(f"{r['label']}: {r['value']}"
                                                              for r in bad))
        return f"{f.get('dgx') or f.get('os') or 'Linux'} · TandemLLM " + \
            (have or "not installed")

    def _upload(self) -> str:
        path = self.pkg.wheel()
        with open(path, "rb") as f:
            data = f.read()
        sha = hashlib.sha256(data).hexdigest()
        name = os.path.basename(path)
        pkg = f"{_q(self.o['prefix'])}/pkg"
        cmd = (f"set -eu; mkdir -p {pkg}; cat > {pkg}/{shlex.quote(name)}.part; "
               f"got=$(sha256sum {pkg}/{shlex.quote(name)}.part | cut -d' ' -f1); "
               f"[ \"$got\" = {sha} ] || {{ echo \"sha256 $got, not {sha}\" >&2; exit 70; }}; "
               f"mv {pkg}/{shlex.quote(name)}.part {pkg}/{shlex.quote(name)}; "
               f"echo \"uploaded {name} ({len(data)} bytes, sha256 {sha[:16]})\"")
        rc, _, err = self._ssh("upload", cmd, data)
        if rc != 0:
            self._fail("upload", "ssh_failed", _last(err) or f"exit status {rc}")
        self._wheel = name
        return f"{name} · {len(data) / 1e6:.1f} MB · sha256 {sha[:16]}"

    def _agent(self) -> str:
        p = _q(self.o["prefix"])
        cmd = (f"set -eu; [ -x {p}/venv/bin/python ] || python3 -m venv {p}/venv; "
               f"{p}/venv/bin/python -m pip install --disable-pip-version-check --no-input "
               f"--upgrade {p}/pkg/{shlex.quote(self._wheel)} 2>&1 | tail -n 20; "
               f"echo \"TANDEM-VERSION $({p}/venv/bin/python -c 'from proxy import _version; "
               f"print(_version.release())')\"")
        rc, out, err = self._ssh("agent", cmd)
        m = re.search(r"^TANDEM-VERSION (\S+)", out, re.M)
        if rc != 0 or not m:
            self._fail("agent", "install_failed", _last(err) or _last(out) or
                       f"exit status {rc}")
        if not same_version(m.group(1), self.version):
            self._fail("agent", "version_mismatch", f"the installed TandemNode says "
                                                    f"{m.group(1)}, not {self.version}")
        return f"TandemNode {m.group(1)} in {self.o['prefix']}/venv"

    def _engine(self) -> str:
        script = self.pkg.install_sh()
        if script is None:
            self._fail("engine", "no_installer", f"this controller has no engine installer to "
                                                 f"send: set {INSTALL_SH_ENV} to the engine's "
                                                 f"install.sh (its VERSION beside it names the "
                                                 f"release), or install without the engine")
        ref = self.pkg.engine_ref()
        p = _q(self.o["prefix"])
        cmd = (f"set -eu; mkdir -p {p}; cat > {p}/install.sh; bash {p}/install.sh --dir "
               f"{p}/engine --ref {shlex.quote(ref)} --runtime-only --yes 2>&1 | "
               f"tail -n 60; exit ${{PIPESTATUS[0]:-0}}")
        rc, out, err = self._ssh("engine", "bash -c " + shlex.quote(cmd), script)
        if rc != 0:
            self._fail("engine", "install_failed", _last(out) or _last(err) or
                       f"exit status {rc}")
        return f"the engine's runtime {ref} in {self.o['prefix']}/engine"

    def _agent_args(self) -> str:
        o = self.o
        a = f"--port {int(o['port'])} --state-dir {_q(o['state_dir'])}"
        if o.get("virtual"):
            a += f" --virtual {shlex.quote(o['virtual'])}"
        return a

    def _unit(self) -> str:
        o = self.o
        p = _q(o["prefix"])
        unit = shlex.quote(o["unit"] + ".service")
        tdir = f" --tandem-dir {_q(o['prefix'] + '/engine')}" if o["engine"] else ""
        wait = (f"{p}/venv/bin/python -c \"import sys; from proxy import nodeagent; "
                f"a = nodeagent.parser().parse_args(sys.argv[1:]); a.state_dir = "
                f"__import__('os').path.expanduser(a.state_dir); "
                f"sys.exit(0 if nodeagent.local_client(a, 2.0) else 1)\" {self._agent_args()}")
        cmd = (f"set -eu; d=\"$HOME/.config/systemd/user\"; mkdir -p \"$d\"; "
               f"{p}/venv/bin/tandem-node unit --host 127.0.0.1 {self._agent_args()}{tdir} > "
               f"\"$d\"/{unit}; systemctl --user daemon-reload; "
               f"systemctl --user enable --now {unit} 2>&1; i=0; "
               f"until {wait}; do i=$((i+1)); [ $i -ge {UNIT_WAIT_S} ] && "
               f"{{ systemctl --user status {unit} --no-pager 2>&1 | tail -n 15; exit 71; }}; "
               f"sleep 1; done; echo \"{o['unit']}.service is up on 127.0.0.1:{int(o['port'])}\"")
        rc, out, err = self._ssh("unit", cmd)
        if rc != 0:
            self._fail("unit", "install_failed", _last(out) or _last(err) or
                       f"exit status {rc}")
        return f"{o['unit']}.service enabled · agent on 127.0.0.1:{int(o['port'])}"

    def _pair(self) -> str:
        from proxy import fleettrust
        p = _q(self.o["prefix"])
        adopt = ("addr=$(printf '%s' \"${SSH_CONNECTION:-}\" | awk '{print $3}'); "
                 f"exec {p}/venv/bin/tandem-node adopt {self._agent_args()} "
                 "--address \"$addr\" ")
        t = self.t
        body = {"host": t["host"], "port": t["port"], "user": t["user"],
                **({"jumps": t["jumps"]} if t.get("jumps") else {}),
                **({"key_file": t["key_file"]} if t.get("key_file") else {}),
                **({"tunnel": True} if t.get("tunnel") else {}),
                **({"host_key": t["host_key"]} if t.get("host_key") else {}),
                **self._secret}
        try:
            row = self.trust.pair_ssh(body, self.who, command=adopt)
        except fleettrust.TrustError as exc:
            self._fail("pair", exc.kind, exc.message)
        with self._lock:
            self.node = row["id"]
        return f"paired as {row['alias']} ({row['id']}) · certificate pinned"


def wheel_version(path: str) -> str | None:
    """The version in a wheel's file name (`tandemapp-0.15.0+test-py3-none-any.whl`); None for
    another distribution's."""
    parts = os.path.basename(path).split("-")
    return parts[1] if len(parts) >= 5 and parts[0] == _version.DIST and \
        path.endswith(".whl") else None


def package_main(argv: list[str]) -> int:
    """`tandem-app package`: the wheel this controller sends, built here; its path on stdout."""
    import argparse
    ap = argparse.ArgumentParser(prog="tandem-app package",
                                 description=f"the {_version.DIST} wheel a node is installed or "
                                             "updated from")
    ap.add_argument("--version", default=None,
                    help="the release name (default this copy's VERSION); a local label such as "
                         "0.15.0+test marks a build of the release")
    ap.add_argument("--out", default="dist-node", help="the directory (default ./dist-node)")
    a = ap.parse_args(argv)
    v = a.version or _version.release()
    try:
        path = build_wheel(os.path.abspath(a.out), v)
    except (OSError, KeyError, ValueError, ImportError) as exc:
        print(f"[package] cannot build: {type(exc).__name__}: {exc}", flush=True)
        return 1
    with open(path, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    print(f"{path}\n[package] {_version.DIST} {wheel_version(path)}, sha256 {sha}", flush=True)
    return 0


def _last(text: str) -> str:
    lines = [x for x in (text or "").strip().splitlines()
             if x.strip() and not x.startswith(("TANDEM-PF ", "TANDEMNODE-DMI "))]
    return lines[-1][:300] if lines else ""
