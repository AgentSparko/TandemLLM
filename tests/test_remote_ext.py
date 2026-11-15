"""`tandem remote ext` (engine-ctl-v1 1.1; ENG-454): an extension's wheel into an install's venv, out
of it, and listed as the engine's extension host reads it.

A sandbox install (tests/remote_box.py, `install=True`) with a real venv of its own (python -m venv,
pip from ensurepip) and a one-line run/requirements.lock; the wheels are built here (a package with
an `engine_ext` module that has EXT_API, NAME, VERSION and start). Nothing is fetched: the wheels
have no dependencies. No engine runs; a job's runner meets a hold's pause file and changes nothing.

The test matrix (the Gherkin of ENG-454):

  1  refused with nothing written, each journaled with its code and the sha256 it checked: bytes
     whose sha256 is not --sha256 (hash_mismatch, exit 3), more than the cap (too_large), not a zip,
     a file name that is not the METADATA's distribution and version, a member outside the root,
     two .dist-info folders (not_a_wheel), a wheel with no engine_ext module and none named
     (no_extension), a checkout instead of an install (not_an_install); --sha256 and --name are
     required                                                    test_install_refusals
  2  install: the wheel in the venv through ops/venv-wheel.sh with the lock as constraints, the
     registry keeps distribution, module, wheel and sha256, the answer reads the module back as the
     host does (NAME, VERSION, EXT_API, accepted), restart_needed and no job; list shows it (source
     remote, not configured); with --when idle a restart job; a module the host refuses (EXT_API
     9.0) installs and lists accepted false with the host's reason; an entry point names the
     module; --module picks one; list finds an engine_ext module no `ext install` named (source
     venv)                                                      test_install_and_list
  3  remove: a distribution that is not installed is refused; the extension chat mode runs
     (run/local.env QSE_MODE=chat, QSE_EXT=its module) is refused ext_in_use, the venv unchanged;
     with --and-mode engine a mode job writes QSE_MODE=engine and an empty QSE_EXT into the overlay,
     and the distribution leaves the venv and the registry; another one goes with no job
                                                                 test_remove
  4  one code path: install.sh's --app-wheel step runs ops/venv-wheel.sh (no pip or uv install line
     of its own), its index defaults are install.sh's, and with uv it passes the lock as
     constraints and reinstalls the package, as install.sh did (a uv stand-in records the call)
                                                                 test_one_way_into_the_venv

Run: python tests/test_remote_ext.py
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import remote_box as R  # noqa: E402
from server.remote import ext as E  # noqa: E402
from server.remote.grammar import Refusal  # noqa: E402

EXT_MOD = 'EXT_API = "{api}"\nNAME = "{name}"\nVERSION = "{version}"\n\n\ndef start(host):\n    return None\n'


def wheel(dist: str = "tandemx", version: str = "1.0", api: str = "3.1", *, module: str | None = "engine_ext",
          entry: str | None = None, extra: dict | None = None, name: str | None = None) -> tuple[str, bytes]:
    """(file name, bytes) of a pure wheel: package `dist` with `module` (an extension), its dist-info."""
    files = {f"{dist}/__init__.py": ""}
    if module:
        files[f"{dist}/{module}.py"] = EXT_MOD.format(api=api, name=dist, version=version)
    files.update(extra or {})
    info = f"{dist}-{version}.dist-info"
    files[f"{info}/METADATA"] = f"Metadata-Version: 2.1\nName: {name or dist}\nVersion: {version}\n"
    files[f"{info}/WHEEL"] = "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    if entry:
        files[f"{info}/entry_points.txt"] = f"[{E.ENTRY_GROUP}]\nmain = {entry}\n"
    rec = []
    for f, body in files.items():
        d = base64.urlsafe_b64encode(hashlib.sha256(body.encode()).digest()).rstrip(b"=").decode()
        rec.append(f"{f},sha256={d},{len(body.encode())}")
    files[f"{info}/RECORD"] = "\n".join(rec + [f"{info}/RECORD,,"]) + "\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for f, body in files.items():
            zf.writestr(f, body)
    return f"{dist}-{version}-py3-none-any.whl", buf.getvalue()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _venv(b: dict) -> str:
    """A real venv for the sandbox install (pip from ensurepip) and a one-line lock."""
    venv = os.path.join(b["install"], "venv")
    shutil.rmtree(venv)
    subprocess.run([sys.executable, "-m", "venv", venv], check=True, capture_output=True, timeout=600)
    with open(os.path.join(b["install"], "run", "requirements.lock"), "w") as fh:
        fh.write("packaging==26.3\n")
    return os.path.join(venv, "bin", "python")


def _pip_env(b: dict, **kw) -> dict:
    return dict(VENV_WHEEL_TOOL="pip", **kw)


def _installed(py: str, dist: str) -> str | None:
    r = subprocess.run([py, "-c", "import sys; from importlib import metadata; print(metadata.version(sys.argv[1]))",
                        dist], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def test_install_refusals():
    b = R.make(install=True)
    try:
        with open(os.path.join(b["install"], "run", "requirements.lock"), "w") as fh:
            fh.write("packaging==26.3\n")
        name, data = wheel()
        n = 0
        # a wrong sha256: refused, the one it got journaled, nothing written
        rc, a = R.call(b, "ext", "install", "--sha256", "0" * 64, "--name", name, stdin=data)
        assert rc == 3 and a["error"]["code"] == "hash_mismatch" and a["error"]["sha256"] == _sha(data), a
        R.valid("error", a)
        row = R.journal(b)[-1]
        assert row["error"] == "hash_mismatch" and row["checked"] == {"sha256": _sha(data)}, row
        assert "0" * 64 in row["args"], row
        n += 1
        # not a wheel, in each way
        cases = [(b"not a zip", name), (wheel(name="other")[1], name), (data, "tandemx-2.0-py3-none-any.whl")]
        _, evil = wheel(extra={"../evil.py": "x"})
        cases.append((evil, name))
        twice = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(twice, "w") as dst:
            for i in src.infolist():
                dst.writestr(i, src.read(i))
            dst.writestr("other-1.0.dist-info/METADATA", "Name: other\nVersion: 1.0\n")
        cases.append((twice.getvalue(), name))
        for blob, fname in cases:
            rc, a = R.call(b, "ext", "install", "--sha256", _sha(blob), "--name", fname, stdin=blob)
            assert rc == 3 and a["error"]["code"] == "not_a_wheel", (fname, a)
            R.valid("error", a)
            n += 1
        nm, plain = wheel(module=None)
        rc, a = R.call(b, "ext", "install", "--sha256", _sha(plain), "--name", nm, stdin=plain)
        assert rc == 3 and a["error"]["code"] == "no_extension", a
        rc, a = R.call(b, "ext", "install", "--sha256", _sha(data), "--name", name, "--module", "tandemx.nope",
                       stdin=data)
        assert rc == 3 and a["error"]["code"] == "no_extension", a
        n += 2
        # nothing on stdin; the size cap (in process: the cap is 256 MiB)
        rc, a = R.call(b, "ext", "install", "--sha256", _sha(data), "--name", name)
        assert rc == 2 and a["error"]["code"] == "missing_argument", a
        old = E.WHEEL_MAX
        E.WHEEL_MAX = 64
        try:
            from server.remote.paths import Tree
            E.ext_install(Tree(b["root"]), _sha(data), name, None, stream=io.BytesIO(data))
            raise AssertionError("no refusal")
        except Refusal as ref:
            assert ref.code == "too_large", ref.code
        finally:
            E.WHEEL_MAX = old
        n += 2
        # required options
        for args in (["--name", name], ["--sha256", _sha(data)]):
            rc, a = R.call(b, "ext", "install", *args, stdin=data)
            assert rc == 2 and a["error"]["code"] == "missing_argument", a
        assert not os.path.exists(os.path.join(b["root"], "run", "remote", "extensions.json"))
        assert not os.listdir(os.path.join(b["root"], "run", "remote", "wheels")) \
            if os.path.isdir(os.path.join(b["root"], "run", "remote", "wheels")) else True
    finally:
        R.cleanup(b)
    c = R.make()
    try:
        rc, a = R.call(c, "ext", "install", "--sha256", _sha(data), "--name", name, stdin=data)
        assert rc == 3 and a["error"]["code"] == "not_an_install", a
        rc, a = R.call(c, "ext", "remove", "tandemx")
        assert rc == 3 and a["error"]["code"] == "not_an_install", a
    finally:
        R.cleanup(c)
    return f"{n + 2} refusals, nothing written"


def test_install_and_list():
    b = R.make(install=True)
    try:
        py = _venv(b)
        open(os.path.join(b["root"], ".watchdog.off"), "w").close()      # a job changes nothing
        name, data = wheel()
        rc, a = R.call(b, "ext", "install", "--sha256", _sha(data), "--name", name, stdin=data, **_pip_env(b))
        assert rc == 0, a
        assert a["installed"] == {"dist": "tandemx", "version": "1.0", "module": "tandemx.engine_ext", "wheel": name,
                                  "sha256": _sha(data)}, a
        assert a["extension"] == {"name": "tandemx", "version": "1.0", "ext_api": "3.1", "imports": True,
                                  "accepted": True, "why": None}, a
        assert a["restart_needed"] is True and a["configured"] is False and "job" not in a, a
        R.valid("ext-install", a)
        assert _installed(py, "tandemx") == "1.0"
        assert R.journal(b)[-1]["checked"] == {"sha256": _sha(data), "bytes": len(data)}
        reg = json.load(open(os.path.join(b["root"], "run", "remote", "extensions.json")))
        assert reg["tandemx"]["module"] == "tandemx.engine_ext" and reg["tandemx"]["sha256"] == _sha(data), reg
        assert not os.listdir(os.path.join(b["root"], "run", "remote", "wheels"))
        rc, lst = R.call(b, "ext", "list")
        assert rc == 0 and lst["mode"] == "engine" and lst["configured"] is None, lst
        R.valid("ext-list", lst)
        (row,) = lst["extensions"]
        assert row["module"] == "tandemx.engine_ext" and row["source"] == "remote" and row["accepted"] and \
            row["dist"] == "tandemx" and row["dist_version"] == "1.0" and not row["configured"], row
        # a module the host refuses still installs, and says why
        nm2, d2 = wheel("tandemy", "2.0", api="9.0")
        rc, a = R.call(b, "ext", "install", "--sha256", _sha(d2), "--name", nm2, "--when", "idle", stdin=d2,
                       **_pip_env(b))
        assert rc == 0 and a["extension"]["accepted"] is False and "9.0" in a["extension"]["why"], a
        assert a["job"]["kind"] == "restart" and a["job"]["when"]["mode"] == "idle", a
        R.valid("ext-install", a)
        job = R.wait_job(b)
        assert job["state"] == "failed" and "hold" in job["message"], job
        # an entry point names the module; --module picks one
        nm3, d3 = wheel("tandemz", "0.1", module="hooks", entry="tandemz.hooks")
        rc, a = R.call(b, "ext", "install", "--sha256", _sha(d3), "--name", nm3, stdin=d3, **_pip_env(b))
        assert rc == 0 and a["installed"]["module"] == "tandemz.hooks" and a["extension"]["accepted"], a
        nm4, d4 = wheel("tandemw", "0.2", extra={"tandemw/engine_ext2.py": EXT_MOD.format(api="3.0", name="w2",
                                                                                         version="0.2")})
        rc, a = R.call(b, "ext", "install", "--sha256", _sha(d4), "--name", nm4, "--module", "tandemw.engine_ext2",
                       stdin=d4, **_pip_env(b))
        assert rc == 0 and a["installed"]["module"] == "tandemw.engine_ext2" and a["extension"]["name"] == "w2", a
        # list: every extension in the venv, an engine_ext module that `ext install` did not name too
        rc, lst = R.call(b, "ext", "list")
        got = [(r["module"], r["source"]) for r in lst["extensions"]]
        assert got == [("tandemw.engine_ext", "venv"), ("tandemw.engine_ext2", "remote"), ("tandemx.engine_ext", "remote"),
                       ("tandemy.engine_ext", "remote"), ("tandemz.hooks", "remote")], got
    finally:
        R.cleanup(b)
    return "4 wheels installed, 5 extensions listed as the host reads them"


def test_remove():
    b = R.make(install=True)
    try:
        py = _venv(b)
        open(os.path.join(b["root"], ".watchdog.off"), "w").close()
        for dist in ("tandemx", "tandemy"):
            name, data = wheel(dist)
            rc, a = R.call(b, "ext", "install", "--sha256", _sha(data), "--name", name, stdin=data, **_pip_env(b))
            assert rc == 0, a
        rc, a = R.call(b, "ext", "remove", "nothere", **_pip_env(b))
        assert rc == 3 and a["error"]["code"] == "not_installed", a
        # chat mode runs tandemx's extension: refused, nothing changed
        with open(os.path.join(b["install"], "run", "local.env"), "w") as fh:
            fh.write("QSE_MODE=chat\nQSE_EXT=tandemx.engine_ext\n")
        rc, lst = R.call(b, "ext", "list")
        assert lst["configured"] == "tandemx.engine_ext" and \
            [r["configured"] for r in lst["extensions"]] == [True, False], lst
        rc, a = R.call(b, "ext", "remove", "tandemx", **_pip_env(b))
        assert rc == 3 and a["error"]["code"] == "ext_in_use" and a["error"]["module"] == "tandemx.engine_ext", a
        R.valid("error", a)
        assert _installed(py, "tandemx") == "1.0"
        # --and-mode engine: the overlay gets engine mode (a job), the distribution goes
        rc, a = R.call(b, "ext", "remove", "tandemx", "--and-mode", "engine", **_pip_env(b))
        assert rc == 0 and a["removed"]["in_use"] and a["removed"]["registry"], a
        assert a["job"]["kind"] == "mode" and a["job"]["when"]["mode"] == "idle", a
        assert a["job"]["overlay"]["values"] == {"QSE_MODE": "engine", "QSE_EXT": ""}, a
        R.valid("ext-remove", a)
        assert _installed(py, "tandemx") is None
        assert "tandemx" not in json.load(open(os.path.join(b["root"], "run", "remote", "extensions.json")))
        job = R.wait_job(b)
        assert job["state"] == "failed" and "hold" in job["message"], job
        # one that does not run: no job
        rc, a = R.call(b, "ext", "remove", "tandemy", **_pip_env(b))
        assert rc == 0 and "job" not in a and not a["removed"]["in_use"] and _installed(py, "tandemy") is None, a
        R.valid("ext-remove", a)
    finally:
        R.cleanup(b)
    return "refused in use, removed with engine mode, removed alone"


def test_one_way_into_the_venv():
    text = open(os.path.join(ROOT, "install.sh")).read()
    step = text[text.index("(--with-chat --app-wheel, ENG-413 WP7)"):text.index('if [ "$RUNTIME_ONLY" = 1 ]; then\n    printf')]
    assert re.search(r'bash "\$SRC/ops/venv-wheel\.sh" install "\$DIR" "\$APP_WHEEL" [a-z]+\n', step), step
    assert "pip install" not in step, "install.sh installs the wheel itself again"
    script = open(os.path.join(ROOT, "ops", "venv-wheel.sh")).read()
    for var in ("PYPI_INDEX", "TORCH_INDEX"):
        want = re.search(rf'^{var}="([^"]+)"', text, re.M).group(1)
        got = re.search(rf'^{var}="\$\{{{var}:-([^}}]+)\}}"', script, re.M).group(1)
        assert want == got, (var, want, got)
    # with uv: the lock as constraints and the package reinstalled, install.sh's own arguments
    d = tempfile.mkdtemp(prefix="vw-")
    try:
        os.makedirs(os.path.join(d, "venv", "bin"))
        os.makedirs(os.path.join(d, "run"))
        os.symlink(sys.executable, os.path.join(d, "venv", "bin", "python"))
        open(os.path.join(d, "run", "requirements.lock"), "w").write("packaging==26.3\n")
        uv = os.path.join(d, "uv")
        open(uv, "w").write('#!/bin/bash\necho "$*" >> "$(dirname "$0")/uv.log"\n')
        os.chmod(uv, 0o755)
        whl = os.path.join(d, "x-1.0-py3-none-any.whl")
        open(whl, "w").close()
        for args in (["install", d, whl, "x"], ["remove", d, "x"]):
            r = subprocess.run(["bash", os.path.join(ROOT, "ops", "venv-wheel.sh"), *args], capture_output=True,
                               text=True, env=dict(os.environ, VENV_WHEEL_TOOL="uv", UV=uv))
            assert r.returncode == 0, r.stderr
        calls = open(os.path.join(d, "uv.log")).read().splitlines()
        assert calls[0].startswith(f"pip install --python {d}/venv/bin/python --reinstall-package x "
                                   f"-c {d}/run/requirements.lock {whl} --index-url"), calls
        assert calls[1] == f"pip uninstall --python {d}/venv/bin/python x", calls
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return "install.sh and ext install share ops/venv-wheel.sh"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
