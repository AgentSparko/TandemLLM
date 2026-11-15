"""`tandem remote ext`: the engine-ext-v1 extensions in an install's venv (engine-ctl-v1 1.1; ENG-454).

    ext list                                     what is installed, read as the engine reads it
    ext install --sha256 HEX --name FILE.whl [--module M] [--when ...]   the wheel comes on stdin
    ext remove DIST [--and-mode engine] [--when ...]

INSTALL takes the wheel's bytes on stdin, at most WHEEL_MAX. They are refused, with nothing
written, when their sha256 is not --sha256, when they are not a wheel (a zip with one
`*.dist-info/` whose METADATA names the file's distribution and version, a WHEEL file and a
RECORD, and no member outside the wheel's root) or when the wheel holds no extension module: the
one --module names, else the one its entry point group `tandemllm.engine_ext` names, else its one
`<package>/engine_ext` module. The wheel then goes into the install's venv through
ops/venv-wheel.sh, the way install.sh --with-chat --app-wheel installs the chat's wheel: its
dependencies resolved with run/requirements.lock as constraints. The module is read back as the
engine's extension host reads it at load (server/ext.py check_module, in the venv's Python), and
run/remote/extensions.json keeps the distribution, its module, the wheel and its sha256.

REMOVE uninstalls the distribution. When its module is the extension the configuration runs (chat
mode with that QSE_EXT), the call is refused unless --and-mode engine is given: then the overlay
gets QSE_MODE=engine and an empty QSE_EXT, as `mode set engine` writes them, and a restart job
(default --when idle) makes them current.

Neither changes the running engine: a process keeps the code it imported. The answer says a restart
is needed; --when schedules one through the lifecycle jobs (server/remote/lifecycle.py).

LIST finds the extensions in the venv by the same rule as a wheel's (a distribution's entry point in
`tandemllm.engine_ext`, else its `<package>/engine_ext` module), however they were installed (ext
install, install.sh --with-chat --app-wheel, pip), adds the configured QSE_EXT, and reads each
module in the venv's Python: its NAME, VERSION and EXT_API, whether this engine's host takes it, its
distribution, and the wheel and sha256 run/remote/extensions.json keeps for one `ext install` put
there. Standard library only.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
import zipfile

from server.remote import overlay
from server.remote.grammar import EXIT_NOT_OK, EXIT_REFUSED, Refusal
from server.remote.paths import Tree

#: the largest wheel `ext install` reads from stdin
WHEEL_MAX = 256 << 20
#: the entry point group a wheel may name its extension module in
ENTRY_GROUP = "tandemllm.engine_ext"
INSTALL_TIMEOUT_S = 1800
PROBE_TIMEOUT_S = 120
PROBE = r'''
import importlib, json, sys
from importlib import metadata
from server import ext as host
modules, group = json.loads(sys.argv[1]), sys.argv[2]
found = {}
if group:
    for d in metadata.distributions():
        eps = [e for e in (getattr(d, "entry_points", None) or []) if e.group == group]
        if eps:
            found.setdefault(eps[0].value.split(":")[0].strip(), d.metadata["Name"])
            continue
        for f in d.files or []:
            parts = str(f).split("/")
            if parts[1:] == ["engine_ext.py"] or parts[1:] == ["engine_ext", "__init__.py"]:
                found.setdefault(parts[0] + ".engine_ext", d.metadata["Name"])
out = []
try:
    dists = metadata.packages_distributions()
except AttributeError:                       # Python before 3.10
    dists = {}
    for d in metadata.distributions():
        for f in d.files or []:
            top = str(f).split("/")[0]
            dists.setdefault(top[:-3] if top.endswith(".py") else top, []).append(d.metadata["Name"])
for mod in sorted(set(modules) | set(found)):
    row = {"module": mod, "imports": False, "accepted": False, "found": mod in found}
    try:
        m = importlib.import_module(mod)
    except BaseException as exc:
        row["why"] = f"does not import: {exc!r}"[:300]
        out.append(row)
        continue
    row["imports"] = True
    for k in ("NAME", "VERSION", "EXT_API"):
        v = getattr(m, k, None)
        row[k.lower() if k != "EXT_API" else "ext_api"] = None if v is None else str(v)
    try:
        host.check_module(m)
        row["accepted"] = True
    except BaseException as exc:
        row["why"] = str(getattr(exc, "why", exc))[:300]
    top = (getattr(m, "__name__", mod) or mod).split(".")[0]
    names = [found[mod]] if mod in found else (dists.get(top) or [])
    row["dist"] = names[0] if names else None
    try:
        row["dist_version"] = metadata.version(names[0]) if names else None
    except Exception:
        row["dist_version"] = None
    out.append(row)
print(json.dumps(out))
'''


def norm(name: str) -> str:
    """A distribution's normalized name (PEP 503)."""
    return re.sub(r"[-_.]+", "-", name).lower()


# ------------------------------------------------------------------------------------- the wheel
def read_stdin(limit: int, stream=None) -> bytes:
    stream = stream if stream is not None else sys.stdin.buffer
    data = stream.read(limit + 1)
    if len(data) > limit:
        raise Refusal("too_large", f"more than {limit >> 20} MiB on stdin", EXIT_REFUSED)
    if not data:
        raise Refusal("missing_argument", "nothing on stdin: the file goes there")
    return data


def check_sha(data: bytes, want: str) -> str:
    got = hashlib.sha256(data).hexdigest()
    if got != want:
        raise Refusal("hash_mismatch", f"the {len(data)} bytes on stdin have sha256 {got}, not {want}: "
                                       f"nothing was written", EXIT_REFUSED, sha256=got)
    return got


def inspect_wheel(data: bytes, filename: str) -> dict:
    """{dist, version, modules, entry}: what the wheel is, or Refusal not_a_wheel."""
    def bad(why: str):
        return Refusal("not_a_wheel", f"{filename}: {why}", EXIT_REFUSED)
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        names = zf.namelist()
    except (zipfile.BadZipFile, ValueError, OSError):
        raise bad("not a zip archive") from None
    for n in names:
        if n.startswith(("/", "\\")) or ".." in n.split("/") or "\\" in n or ":" in n.split("/")[0]:
            raise bad(f"a member outside the wheel's root ({n[:80]!r})")
    infos = sorted({n.split("/")[0] for n in names if n.split("/")[0].endswith(".dist-info")})
    if len(infos) != 1:
        raise bad(f"{len(infos)} .dist-info folders (a wheel has one)")
    info = infos[0]
    for f in ("METADATA", "WHEEL", "RECORD"):
        if f"{info}/{f}" not in names:
            raise bad(f"no {info}/{f}")
    meta = zf.read(f"{info}/METADATA").decode("utf-8", "replace")
    fields = {}
    for ln in meta.split("\n\n")[0].splitlines():
        k, sep, v = ln.partition(":")
        if sep and k in ("Name", "Version") and k not in fields:
            fields[k] = v.strip()
    if "Name" not in fields or "Version" not in fields:
        raise bad("its METADATA names no distribution or version")
    fn_dist, fn_ver = filename.split("-")[:2]
    if norm(fn_dist) != norm(fields["Name"]) or fn_ver != fields["Version"].replace("-", "_"):
        raise bad(f"the file name says {fn_dist} {fn_ver}, its METADATA {fields['Name']} {fields['Version']}")
    modules = set()
    for n in names:
        m = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*(?:/[A-Za-z_][A-Za-z0-9_]*)*)(?:\.py|/__init__\.py)", n)
        if m:
            modules.add(m.group(1).replace("/", "."))
    entry = None
    if f"{info}/entry_points.txt" in names:
        group = None
        for ln in zf.read(f"{info}/entry_points.txt").decode("utf-8", "replace").splitlines():
            ln = ln.strip()
            if ln.startswith("[") and ln.endswith("]"):
                group = ln[1:-1].strip()
            elif group == ENTRY_GROUP and "=" in ln and entry is None:
                entry = ln.split("=", 1)[1].split(":")[0].strip()
    return {"dist": fields["Name"], "version": fields["Version"], "modules": sorted(modules), "entry": entry}


def extension_module(wheel: dict, asked: str | None) -> str:
    """The wheel's extension module: --module, else its entry point, else its one */engine_ext."""
    if asked:
        if asked not in wheel["modules"]:
            raise Refusal("no_extension", f"the wheel has no module {asked}", EXIT_REFUSED)
        return asked
    if wheel["entry"]:
        if wheel["entry"] not in wheel["modules"]:
            raise Refusal("no_extension", f"the wheel's entry point names {wheel['entry']}, which it does not "
                                          f"hold", EXIT_REFUSED)
        return wheel["entry"]
    cands = [m for m in wheel["modules"] if m.split(".")[-1] == "engine_ext"]
    if len(cands) != 1:
        raise Refusal("no_extension", f"the wheel holds {len(cands)} engine_ext modules and names none in "
                                      f"[{ENTRY_GROUP}]: give --module", EXIT_REFUSED)
    return cands[0]


# ------------------------------------------------------------------------------------- the venv
def _need_install(tree: Tree) -> None:
    if not tree.install:
        raise Refusal("not_an_install", "this engine runs from a checkout, not an install: extensions go "
                                        "into an install's venv (install.sh)", EXIT_REFUSED)
    for p, what in ((tree.venv_python, "the venv's Python"), (tree.lock_file, "the engine's pins")):
        if not os.path.exists(p):
            raise Refusal("not_an_install", f"{what} ({os.path.relpath(p, tree.base)}) is missing: run the "
                                            f"installer again", EXIT_REFUSED)


def python_of(tree: Tree) -> str:
    """The Python the engine runs: the install's venv, else this one (a checkout)."""
    if tree.venv_python and os.path.exists(tree.venv_python):
        return tree.venv_python
    return sys.executable


def probe(tree: Tree, modules: list[str], discover: bool = False) -> list[dict]:
    """Each module as the engine's extension host reads it at load, in the engine's Python; with
    `discover`, every extension the venv holds too (`found`)."""
    if not modules and not discover:
        return []
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "LC_ALL", "TZ")}
    env["PYTHONPATH"] = tree.root
    try:
        r = subprocess.run([python_of(tree), "-s", "-c", PROBE, json.dumps(modules), ENTRY_GROUP if discover else ""],
                           cwd=tree.root, env=env,
                           capture_output=True, timeout=PROBE_TIMEOUT_S, stdin=subprocess.DEVNULL)
        rows = json.loads(r.stdout.decode("utf-8", "replace").strip().splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return [{"module": m, "imports": False, "accepted": False, "why": "the engine's Python did not answer"}
                for m in modules]
    return rows


def _script(tree: Tree, *args: str) -> tuple[int, str]:
    env = dict(os.environ)
    try:
        r = subprocess.run(["bash", os.path.join(tree.root, "ops", "venv-wheel.sh"), *args], cwd=tree.root,
                           env=env, capture_output=True, timeout=INSTALL_TIMEOUT_S, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return 124, f"no answer after {INSTALL_TIMEOUT_S} s"
    out = (r.stdout + r.stderr).decode("utf-8", "replace")
    try:
        os.makedirs(tree.logs, exist_ok=True)
        with open(tree.launcher_log, "a") as fh:
            fh.write(f"[remote ext] venv-wheel.sh {args[0]} {' '.join(os.path.basename(a) for a in args[2:])}: "
                     f"exit {r.returncode}\n" + out[-4000:] + ("\n" if out and not out.endswith("\n") else ""))
    except OSError:
        pass
    return r.returncode, out


# ------------------------------------------------------------------------------------- the registry
def registry(tree: Tree) -> dict:
    try:
        with open(tree.extensions, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_registry(tree: Tree, reg: dict) -> None:
    overlay._write_atomic(tree.extensions, json.dumps(reg, indent=1, sort_keys=True) + "\n")


def configured(tree: Tree) -> tuple[str | None, str | None]:
    """(QSE_MODE, the extension module chat mode runs: QSE_EXT, None in engine mode)."""
    env, _ = overlay.resolve(tree, ["QSE_MODE", "QSE_EXT"])
    mode = env.get("QSE_MODE") or None
    return mode, (env.get("QSE_EXT") or None) if mode == "chat" else None


# ------------------------------------------------------------------------------------- the verbs
def ext_list(tree: Tree) -> dict:
    mode, conf = configured(tree)
    reg = {e["module"]: (d, e) for d, e in registry(tree).items()}
    asked = sorted(set(reg) | ({conf} if conf else set()))
    out = []
    for p in probe(tree, asked, discover=True):
        mod = p["module"]
        dist, e = reg.get(mod, (None, {}))
        source = "remote" if mod in reg else ("venv" if p.get("found") else "config")
        out.append({"module": mod, "source": source, "dist": dist or p.get("dist"),
                    "dist_version": p.get("dist_version"), "name": p.get("name"), "version": p.get("version"),
                    "ext_api": p.get("ext_api"), "imports": bool(p.get("imports")),
                    "accepted": bool(p.get("accepted")), "why": p.get("why"), "configured": mod == conf,
                    "wheel": e.get("wheel"), "sha256": e.get("sha256"), "installed_at": e.get("installed_at")})
    from server import ext as host
    return {"extensions": out, "mode": mode, "configured": conf, "host_ext_api": host.EXT_API}


def ext_install(tree: Tree, sha: str, filename: str, module: str | None, stream=None) -> dict:
    _need_install(tree)
    data = read_stdin(WHEEL_MAX, stream)
    got = check_sha(data, sha)
    wheel = inspect_wheel(data, filename)
    mod = extension_module(wheel, module)
    stage = os.path.join(tree.remote, "wheels")
    os.makedirs(stage, mode=0o700, exist_ok=True)
    path = os.path.join(stage, filename)
    fd = os.open(path + ".part", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.replace(path + ".part", path)
    try:
        rc, out = _script(tree, "install", tree.install, path, wheel["dist"])
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    checked = {"sha256": got, "bytes": len(data)}
    if rc != 0:
        tail = [ln for ln in out.strip().splitlines() if ln.strip()][-6:]
        raise Refusal("install_failed", f"ops/venv-wheel.sh install exited {rc}: {' | '.join(tail)[-600:]}",
                      EXIT_NOT_OK, checked=checked)
    reg = registry(tree)
    for d in [d for d in reg if norm(d) == norm(wheel["dist"])]:
        del reg[d]
    reg[wheel["dist"]] = {"module": mod, "version": wheel["version"], "wheel": filename, "sha256": got,
                          "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _save_registry(tree, reg)
    seen = (probe(tree, [mod]) or [{}])[0]
    mode, conf = configured(tree)
    return {"installed": {"dist": wheel["dist"], "version": wheel["version"], "module": mod, "wheel": filename,
                          "sha256": got},
            "extension": {k: seen.get(k) for k in ("name", "version", "ext_api", "imports", "accepted", "why")},
            "configured": conf == mod, "restart_needed": True, "checked": checked}


def owner_of(tree: Tree, module: str) -> str | None:
    """The distribution that holds `module`: the registry's, else the venv's."""
    for dist, e in registry(tree).items():
        if e.get("module") == module:
            return dist
    seen = (probe(tree, [module]) or [{}])[0]
    return seen.get("dist")


def installed_version(tree: Tree, dist: str) -> str | None:
    """The version of `dist` in the engine's Python, None when it is not installed there."""
    code = "import sys; from importlib import metadata; print(metadata.version(sys.argv[1]))"
    try:
        r = subprocess.run([python_of(tree), "-s", "-c", code, dist], capture_output=True, timeout=PROBE_TIMEOUT_S,
                           stdin=subprocess.DEVNULL, env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")})
    except (OSError, subprocess.SubprocessError):
        return None
    out = r.stdout.decode("utf-8", "replace").strip()
    return out or None if r.returncode == 0 else None


def ext_remove(tree: Tree, dist: str, and_mode: str | None) -> dict:
    """What `ext remove DIST` would take out, checked; a Refusal when the extension runs here and
    --and-mode engine was not given. Nothing is changed."""
    _need_install(tree)
    version = installed_version(tree, dist)
    if version is None:
        raise Refusal("not_installed", f"{dist} is not installed in the engine's venv", EXIT_REFUSED)
    known = [e for d, e in registry(tree).items() if norm(d) == norm(dist)]
    _, conf = configured(tree)
    in_use = bool(conf) and norm(owner_of(tree, conf) or "") == norm(dist)
    if in_use and and_mode != "engine":
        raise Refusal("ext_in_use", f"{conf} is the extension chat mode runs here: remove it with "
                                    f"--and-mode engine (the overlay then sets engine mode, and a restart "
                                    f"makes it current)", EXIT_REFUSED, module=conf)
    module = known[0]["module"] if known else (conf if in_use else None)
    return {"dist": dist, "version": version, "module": module, "in_use": in_use}


def uninstall(tree: Tree, dist: str) -> dict:
    rc, out = _script(tree, "remove", tree.install, dist)
    if rc != 0:
        tail = [ln for ln in out.strip().splitlines() if ln.strip()][-6:]
        raise Refusal("remove_failed", f"ops/venv-wheel.sh remove exited {rc}: {' | '.join(tail)[-600:]}",
                      EXIT_NOT_OK)
    reg = registry(tree)
    gone = [d for d in reg if norm(d) == norm(dist)]
    for d in gone:
        del reg[d]
    if gone:
        _save_registry(tree, reg)
    return {"registry": bool(gone)}
