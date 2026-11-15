"""The managed overlay: the key schema, the layers, the versions and the plan (engine-ctl-v1).

THE SCHEMA is derived, in this one place, from the release's ops/serve.env: every key it assigns or
documents as `#KEY=value`, with the default it gives, a type read off that default, the restart a
change needs and whether the key feeds the disk tier's fingerprint. A key is `managed` (the overlay
may set it) unless it is the install's (paths of the interpreter and the tree, the address), the
weight profile's (weights, drafters, corpus, the model's own flags), one serve.env computes itself,
one that may hold a secret, or QSE_BACKENDS (the engine takes only off). No other key exists here.

THE FINGERPRINT. The disk tier's and the spill's rows are keyed by the engine's code and
`fingerprint_extra` (server/diskcache.py): every QWEN38_* variable of the engine's environment but the
prefixes `_NOT_ROWS` names, the variables `_ROW_FILES` names, and the arguments that name weight and
drafter files, the drafter, the prefix chunk and the narrow arm, which ops/start.sh builds from NV,
HEAD, CKPT8, CKPT16, QSE_ENGINE_FLAGS and NARROW_ARM, and FUSE_PROJ, which it exports as
QWEN38_FUSE_PROJ. The prefixes are read from server/diskcache.py's source, so they cannot drift;
tests/test_remote_overlay.py holds the argument list to start.sh. Setting a QWEN38_* key that was
unset changes the fingerprint too, even to the engine's default. This file changes nothing the
fingerprint reads.

THE LAYERS, in serve.env's order: default (ops/serve.env), install (the keys install.sh wrote into
run/ops/serve.env), profile (ops/profiles/<name>.env and the shared file it sources), setup
(run/setup.env), local (an install's run/local.env), managed (run/managed.env), and mode (the mode
block's own values: engine mode turns the conductor's keys off, an empty QSE_MODE is inferred).
A value is what bash makes of serve.env, sourced as ops/start.sh sources it; its source is the last
layer that assigns it.

THE VERSIONS. run/managed.d/<n>.env holds version n, manifest.json the list, `current` (0: no
overlay) and `good` (the version the engine last reached /health with). run/managed.env is a copy of
the current one. Nothing here edits ops/serve.env or a profile.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time

from server.remote.grammar import EXIT_REFUSED, Refusal
from server.remote.journal import SECRET_NAME
from server.remote.paths import Tree

MODE_MARK = "# OPS-260: the mode (QSE_MODE above), applied last"
HOOK = "run/managed.env"
ASSIGN = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
DOC = re.compile(r"^#(\s?)([A-Z][A-Z0-9_]*)=(\S*)")
VALUE = re.compile(r"^[A-Za-z0-9._,:=@%+/-]*$")
EXPANSIONS = ("${HOME}", "${REPO}")
MAX_VALUE = 512

#: the install's keys (install.sh writes them): never the overlay's
MACHINE = ("HOME_DIR", "REPO", "PY", "PYTHONPATH", "HOST", "PORT")
#: the weight profile's: the weights, the drafters, the corpus, the model's own flags
WEIGHTS = ("NV", "HEAD", "CKPT8", "CKPT16", "CORPUS", "QSE_ENGINE_FLAGS", "QSE_PAGE_CACHE_DROP",
           "QSE_PAGE_CACHE_KEEP")
#: what serve.env computes from other keys
DERIVED = ("QSE_PROFILE_NAME", "QSE_PACK_VISION", "QSE_SETUP_ENV_FILE", "QSE_VISION_ASKED",
           "QSE_CHATD_ASKED")
#: the engine relays to no other backend: only off, which is the default
FIXED = ("QSE_BACKENDS",)
#: values the mode block itself checks
ENUMS = {"QSE_MODE": ("engine", "chat"), "QSE_CHATD": ("node", "off"), "CONDUCTOR": ("raw", "shadow", "on")}
TYPES = {"QSE_EXT": "module", "QSE_ALLOW_FROM": "cidrs"}
#: a change of these keys needs no engine restart: the server applies a new value live (engine API
#: 1.9's PATCH /v1/engine/settings, server/engine_settings.py). Every other key is read at the start
HOT: tuple = ("QSE_ALLOW_FROM",)
#: ops/start.sh's sources of fingerprint_extra's arguments and of QWEN38_FUSE_PROJ
START_ARGS = {"NV": "--nvfp4", "HEAD": "--fp8-head", "CKPT8": "--dflash2-ckpt", "CKPT16": "--dflash2-ckpt16",
              "QSE_ENGINE_FLAGS": "--model, --drafter, --prefix-chunk", "NARROW_ARM": "--narrow-arm",
              "FUSE_PROJ": "QWEN38_FUSE_PROJ"}
#: the roots a path value may name besides the install: the engine's state directory
STATE_DIRS = (".qwen38-spark-engine",)
LAYERS = ("default", "install", "profile", "setup", "local", "managed", "mode")


# ---------------------------------------------------------------------------------------- schema
def _fingerprint_rules(root: str) -> tuple[tuple, tuple]:
    """(`_NOT_ROWS`, `_ROW_FILES`) of server/diskcache.py, read from its source, not imported."""
    with open(os.path.join(root, "server", "diskcache.py"), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    names: dict = {}

    def value(node):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return value(node.left) + value(node.right)
        if isinstance(node, ast.Name):
            return names[node.id]
        return tuple(ast.literal_eval(node))

    for n in tree.body:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name) and \
                n.targets[0].id in ("_NOT_ARITHMETIC", "_NOT_ROWS", "_ROW_FILES"):
            names[n.targets[0].id] = value(n.value)
    return names["_NOT_ROWS"], names["_ROW_FILES"]


def feeds_fingerprint(key: str, not_rows: tuple, row_files: tuple) -> bool:
    return (key.startswith("QWEN38_") and not key.startswith(not_rows)) or key in row_files or key in START_ARGS


def _unquote(v: str) -> str:
    v = v.split(" #")[0].strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        return v[1:-1]
    return v


def _type_of(key: str, default: str | None) -> str:
    if key in TYPES:
        return TYPES[key]
    if key in ENUMS:
        return "enum"
    d = default or ""
    if re.fullmatch(r"-?[0-9]+", d):
        return "integer"
    if re.fullmatch(r"-?[0-9]+\.[0-9]+", d):
        return "number"
    if "/" in d and d.startswith(("${HOME}", "${REPO}", "/")):
        return "path"
    return "string"


def schema(tree: Tree) -> dict:
    """KEY -> {default, documented, type, managed, why, restart, fingerprint, mode_block}."""
    with open(tree.template, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    not_rows, row_files = _fingerprint_rules(tree.root)
    out: dict = {}
    in_mode = False
    spaced: dict = {}
    for ln in lines:
        if ln.startswith(MODE_MARK):
            in_mode = True
        m = ASSIGN.match(ln)
        if m and m.group(1).isupper():
            k = m.group(1)
            e = out.setdefault(k, {"default": None, "documented": None, "mode_block": in_mode})
            if e["default"] is None and not in_mode:
                e["default"] = _unquote(m.group(2))
            if not in_mode:
                e["mode_block"] = False
            continue
        d = DOC.match(ln)
        if d:
            if d.group(1):
                spaced.setdefault(d.group(2), _unquote(d.group(3)))
            else:
                e = out.setdefault(d.group(2), {"default": None, "documented": None, "mode_block": False})
                e["documented"] = e["documented"] or _unquote(d.group(3))
    for k, v in spaced.items():
        out.setdefault(k, {"default": None, "documented": v.rstrip(":"), "mode_block": False})
    for k, e in out.items():
        why = None
        if k in MACHINE:
            why = "the install's"
        elif k in WEIGHTS:
            why = "the weight profile's"
        elif k in DERIVED:
            why = "computed by serve.env"
        elif k in FIXED:
            why = "only off: the engine relays to no other backend"
        elif SECRET_NAME.search(k):
            why = "may hold a secret"
        e.update(type=_type_of(k, e["default"] if e["default"] is not None else e["documented"]),
                 managed=why is None, why=why, restart="hot" if k in HOT else "engine",
                 fingerprint=feeds_fingerprint(k, not_rows, row_files))
        if k in ENUMS:
            e["values"] = list(ENUMS[k])
    return out


# ---------------------------------------------------------------------------------------- values
def check_value(tree: Tree, key: str, value: str, spec: dict) -> None:
    """A Refusal unless `value` may go into the overlay for `key`."""
    if not spec["managed"]:
        raise Refusal("key_not_managed", f"{key} is not a managed key ({spec['why']})", EXIT_REFUSED)
    if len(value) > MAX_VALUE:
        raise Refusal("bad_value", f"{key}: longer than {MAX_VALUE} characters")
    bare = value
    for x in EXPANSIONS:
        bare = bare.replace(x, "")
    if not VALUE.match(bare):
        raise Refusal("bad_value", f"{key}: a value is letters, digits and . _ , : = @ % + / - only "
                                   f"(and ${{HOME}} or ${{REPO}})")
    t = spec["type"]
    if t == "enum" and value not in spec["values"]:
        raise Refusal("bad_value", f"{key} takes {' or '.join(spec['values'])}")
    if t == "integer" and not re.fullmatch(r"-?[0-9]{1,12}", value):
        raise Refusal("bad_value", f"{key} takes a whole number")
    if t == "number" and not re.fullmatch(r"-?[0-9]{1,12}(\.[0-9]{1,12})?", value):
        raise Refusal("bad_value", f"{key} takes a number")
    if t == "module" and value and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*", value):
        raise Refusal("bad_value", f"{key} takes a dotted module name")
    if t == "cidrs":
        from server.api.access import parse_allow
        try:
            parse_allow(value)
        except ValueError as exc:
            raise Refusal("bad_value", f"{key}: {exc} (addresses and CIDRs, comma-separated)") from None
        return
    if t == "path" or value.startswith(("/", "~", "$")) or "${" in value:
        for part in value.split(":") if t == "path" else [value]:
            check_path(tree, key, part)


def _expand(tree: Tree, v: str) -> str:
    return v.replace("${HOME}", os.path.expanduser("~")).replace("${REPO}", tree.root)


def _real(p: str) -> str:
    """The real path of `p`, or of its longest part that exists with the rest added."""
    head, tail = p, ""
    while head and not os.path.exists(head):
        head, last = os.path.split(head)
        tail = os.path.join(last, tail) if tail else last
        if head in ("", os.sep) and not os.path.exists(head or os.sep):
            break
    return os.path.normpath(os.path.join(os.path.realpath(head or os.sep), tail))


def check_path(tree: Tree, key: str, raw: str) -> None:
    """A path value lies under the install (or the checkout) or the engine's state directory."""
    if raw in ("", "off", "auto", "on"):
        return
    p = _expand(tree, raw)
    if not os.path.isabs(p) or "/../" in p + "/" or p.endswith("/.."):
        raise Refusal("path_outside", f"{key}: {raw!r} is not an absolute path inside the install",
                      EXIT_REFUSED)
    real = _real(p)
    home = os.path.realpath(os.path.expanduser("~"))
    roots = [os.path.realpath(tree.base)] + [os.path.join(home, d) for d in STATE_DIRS]
    if os.path.commonpath([real, os.path.join(home, ".ssh")]) == os.path.join(home, ".ssh") or \
            not any(os.path.commonpath([real, r]) == r for r in roots):
        raise Refusal("path_outside", f"{key}: {raw!r} is outside the install and the engine's state "
                                      f"directory", EXIT_REFUSED)


# ---------------------------------------------------------------------------------------- files
def _assigned(path: str | None) -> dict:
    """KEY -> the raw value of its last assignment in `path` ({} when there is no file)."""
    if not path or not os.path.isfile(path):
        return {}
    out = {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            m = ASSIGN.match(ln.rstrip("\n"))
            if m:
                out[m.group(1)] = _unquote(m.group(2))
    return out


def _install_keys(serve_env: str) -> list[str]:
    """The keys install.sh lists in the header of the run/ops/serve.env it wrote."""
    try:
        with open(serve_env, encoding="utf-8", errors="replace") as fh:
            head = [next(fh, "") for _ in range(6)]
    except OSError:
        return []
    for i, ln in enumerate(head):
        if "these keys are this machine's" in ln and i + 1 < len(head):
            return head[i + 1].lstrip("#").split()
    return []


def hook_present(tree: Tree) -> bool:
    """The serve.env in use reads the managed overlay (one rendered from an older release does not)."""
    try:
        with open(tree.serve_env, encoding="utf-8") as fh:
            return HOOK in fh.read()
    except OSError:
        return False


def resolve(tree: Tree, keys, overlay: str | None = None, timeout: float = 15.0) -> tuple[dict, str | None]:
    """KEY -> value as ops/start.sh sees serve.env (sourced under set -u, its keys exported), for the set
    ones of `keys`;
    `overlay` replaces run/managed.env (QSE_MANAGED_ENV). The second item is bash's last error line."""
    keys = list(keys)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.path.expanduser("~"),
           "ENVF": tree.serve_env}
    if overlay is not None:
        env["QSE_MANAGED_ENV"] = overlay
    script = ('set -u; set -a; . "$ENVF" >/dev/null || exit 3; set +a; for k in "$@"; do '
              'if [ -n "${!k+x}" ]; then printf "%s\\0%s\\0" "$k" "${!k}"; fi; done')
    if not os.path.isfile(tree.serve_env):
        return {}, f"{tree.serve_env} is missing"
    try:
        r = subprocess.run(["bash", "-c", script, "-", *keys], capture_output=True, timeout=timeout, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        return {}, str(exc)
    parts = r.stdout.decode("utf-8", "replace").split("\0")
    out = {parts[i]: parts[i + 1] for i in range(0, len(parts) - 1, 2)}
    err = None
    if r.returncode != 0:
        err = (r.stderr.decode("utf-8", "replace").strip().splitlines() or [f"exit {r.returncode}"])[-1]
    return out, err


def _profile_files(tree: Tree, name: str | None) -> list[str]:
    if not name:
        return []
    if name.startswith("community/"):
        p = os.path.join(tree.run, name + ".env")
        return [p] if os.path.isfile(p) else []
    p = os.path.join(tree.root, "ops", "profiles", name + ".env")
    if not os.path.isfile(p):
        return []
    out = []
    with open(p, encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            m = re.match(r'^\s*\.\s+"\$\{REPO\}/ops/profiles/(_[A-Za-z0-9_-]+\.env)"', ln)
            if m:
                out.append(os.path.join(tree.root, "ops", "profiles", m.group(1)))
    return out + [p]


def layers(tree: Tree, profile: str | None) -> list[tuple[str, dict]]:
    """[(layer, {KEY: raw value})] in serve.env's order (mode last, empty here)."""
    template = {}
    with open(tree.template, encoding="utf-8") as fh:
        for ln in fh:
            if ln.startswith(MODE_MARK):
                break
            m = ASSIGN.match(ln.rstrip("\n"))
            if m:
                template[m.group(1)] = _unquote(m.group(2))
    installed = _assigned(tree.serve_env) if tree.install else {}
    prof: dict = {}
    for f in _profile_files(tree, profile):
        prof.update(_assigned(f))
    return [("default", template),
            ("install", {k: installed.get(k, "") for k in _install_keys(tree.serve_env)}),
            ("profile", prof), ("setup", _assigned(tree.setup_env)), ("local", _assigned(tree.local_env)),
            ("managed", _assigned(tree.managed))]


def settings(tree: Tree, only: list[str] | None = None) -> dict:
    """The `settings get` answer's body."""
    sch = schema(tree)
    unknown = [k for k in only or [] if k not in sch]
    if unknown:
        raise Refusal("unknown_key", f"not a settings key: {', '.join(unknown)}")
    keys = only or sorted(sch)
    eff, err = resolve(tree, set(keys) | {"QSE_PROFILE_NAME", "QSE_MODE"})
    lay = layers(tree, eff.get("QSE_PROFILE_NAME"))
    mode = eff.get("QSE_MODE")
    forced = _engine_forced(tree)
    rows = []
    for k in keys:
        spec = sch[k]
        src = "default"
        last = None
        for name, vals in lay:
            if k in vals:
                src, last = name, vals[k]
        if spec["mode_block"] and src == "default":
            src = "mode"
        elif k == "QSE_MODE" and not last:
            src = "mode"
        elif mode == "engine" and k in forced:
            src = "mode"
        row = {"key": k, "set": k in eff, "value": eff.get(k), "source": src, "type": spec["type"],
               "managed": spec["managed"], "restart": spec["restart"], "fingerprint": spec["fingerprint"]}
        if spec["default"] is not None:
            row["default"] = spec["default"]
        if spec.get("values"):
            row["values"] = spec["values"]
        if spec["why"]:
            row["why"] = spec["why"]
        rows.append(row)
    return {"keys": rows, "overlay": manifest_brief(tree), "hook": hook_present(tree),
            **({"config_error": err} if err else {})}


def _engine_forced(tree: Tree) -> set:
    """The keys engine mode sets whatever the layers say: the mode block's `else` branch."""
    with open(tree.template, encoding="utf-8") as fh:
        text = fh.read()
    block = text[text.index(MODE_MARK):]
    m = re.search(r'^if \[ "\$\{QSE_MODE\}" = chat \]; then\n.*?^else\n(.*?)^fi\n', block, re.S | re.M)
    if not m:
        return set()
    out = set()
    for ln in m.group(1).splitlines():
        a = ASSIGN.match(ln)
        if a and ln.startswith("    ") and not ln.startswith("        "):
            out.add(a.group(1))
    return out - {"QSE_MODE"}


# ---------------------------------------------------------------------------------------- versions
def _manifest_path(tree: Tree) -> str:
    return os.path.join(tree.managed_dir, "manifest.json")


def manifest(tree: Tree) -> dict:
    try:
        with open(_manifest_path(tree), encoding="utf-8") as fh:
            m = json.load(fh)
        if isinstance(m, dict) and isinstance(m.get("versions"), list):
            return m
    except (OSError, ValueError):
        pass
    return {"current": 0, "good": None, "versions": []}


def manifest_brief(tree: Tree) -> dict:
    m = manifest(tree)
    return {"current": m["current"], "good": m.get("good"), "versions": len(m["versions"]),
            "file": os.path.exists(tree.managed)}


def _write_atomic(path: str, data: str, mode: int = 0o600) -> None:
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(path))
    with os.fdopen(fd, "w") as fh:
        fh.write(data)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def save_manifest(tree: Tree, m: dict) -> None:
    _write_atomic(_manifest_path(tree), json.dumps(m, indent=1, sort_keys=True) + "\n")


def values_of(tree: Tree, n: int) -> dict:
    """The keys version `n` sets (0: none)."""
    if n == 0:
        return {}
    for v in manifest(tree)["versions"]:
        if v["n"] == n:
            return dict(v["set"])
    raise Refusal("unknown_version", f"there is no overlay version {n}", EXIT_REFUSED)


def render(values: dict, n: int, note: str) -> str:
    lines = [f"# The managed overlay, version {n} ({note}), written by tandem remote on "
             f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}.",
             "# Generated: ops/serve.env reads it after run/local.env; change it with tandem remote settings."]
    for k in sorted(values):
        v = values[k]
        lines.append(f'{k}="{v}"' if "${" in v else f"{k}='{v}'")
    return "\n".join(lines) + "\n"


def add_version(tree: Tree, values: dict, note: str) -> int:
    """A new version holding `values` (not made current)."""
    m = manifest(tree)
    n = max([v["n"] for v in m["versions"]] + [0]) + 1
    body = render(values, n, note)
    _write_atomic(os.path.join(tree.managed_dir, f"{n}.env"), body)
    m["versions"].append({"n": n, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "note": note,
                          "set": dict(sorted(values.items())),
                          "sha256": hashlib.sha256(body.encode()).hexdigest()})
    save_manifest(tree, m)
    return n


def make_current(tree: Tree, n: int) -> None:
    """run/managed.env becomes version n's file (0: removed)."""
    m = manifest(tree)
    if n == 0:
        try:
            os.unlink(tree.managed)
        except FileNotFoundError:
            pass
    else:
        with open(os.path.join(tree.managed_dir, f"{n}.env"), encoding="utf-8") as fh:
            _write_atomic(tree.managed, fh.read())
    m["current"] = n
    save_manifest(tree, m)


def mark_good(tree: Tree) -> None:
    m = manifest(tree)
    m["good"] = m["current"]
    save_manifest(tree, m)


def versions(tree: Tree) -> dict:
    m = manifest(tree)
    return {"current": m["current"], "good": m.get("good"),
            "versions": [{"n": v["n"], "at": v["at"], "note": v["note"], "set": v["set"],
                          "sha256": v["sha256"]} for v in m["versions"]]}


# ---------------------------------------------------------------------------------------- the plan
def token(base: int, values: dict, kind: str = "plan") -> str:
    blob = json.dumps([kind, base, sorted(values.items())], separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def target_of(tree: Tree, sets: list, unsets: list, base_values: dict) -> dict:
    sch = schema(tree)
    new = dict(base_values)
    for k in unsets:
        if k not in sch:
            raise Refusal("unknown_key", f"not a settings key: {k}")
        if k not in new:
            raise Refusal("not_in_overlay", f"--unset {k}: the overlay does not set it", EXIT_REFUSED)
        del new[k]
    for k, v in sets:
        if k not in sch:
            raise Refusal("unknown_key", f"not a settings key: {k}")
        check_value(tree, k, v, sch[k])
        new[k] = v
    return new


def plan(tree: Tree, new: dict, *, base: int | None = None) -> dict:
    """What the overlay `new` would change, against the current version (nothing is written)."""
    if not hook_present(tree):
        raise Refusal("hook_missing", f"{tree.serve_env} does not read {HOOK}: run the installer again "
                                      f"so it is rendered from this release", EXIT_REFUSED)
    m = manifest(tree)
    base = m["current"] if base is None else base
    old = values_of(tree, base)
    if new == old:
        raise Refusal("no_change", "the overlay already holds exactly these values", EXIT_REFUSED)
    sch = schema(tree)
    keys = sorted(sch)
    before, err = resolve(tree, keys)
    if err:
        raise Refusal("config_error", f"serve.env does not load now: {err}", EXIT_REFUSED)
    fd, cand = tempfile.mkstemp(prefix="managed-plan-", suffix=".env")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(render(new, 0, "plan"))
        after, err = resolve(tree, keys, overlay=cand)
    finally:
        os.unlink(cand)
    if err:
        raise Refusal("config_error", f"serve.env does not load with these values: {err}", EXIT_REFUSED)
    overlay = [{"key": k, "from": old.get(k), "to": new.get(k)}
               for k in sorted(set(old) | set(new)) if old.get(k) != new.get(k)]
    effect = []
    for k in keys:
        a, b = (k in before, before.get(k)), (k in after, after.get(k))
        if a != b:
            effect.append({"key": k, "from": before.get(k), "to": after.get(k),
                           "was_set": a[0], "is_set": b[0], "restart": sch[k]["restart"],
                           "fingerprint": sch[k]["fingerprint"]})
    shadowed = [o["key"] for o in overlay if o["key"] not in {e["key"] for e in effect}]
    fp = [e["key"] for e in effect if e["fingerprint"]]
    out = {"base": base, "overlay": overlay, "effect": effect, "shadowed": shadowed,
           "restart_needed": any(e["restart"] == "engine" for e in effect),
           "fingerprint_keys": fp, "confirm": token(base, new)}
    warnings = []
    if fp:
        out["confirm_fingerprint"] = token(base, new, "fingerprint")
        warnings.append(f"{', '.join(fp)} feed{'s' if len(fp) == 1 else ''} the disk cache's fingerprint: "
                        f"after the restart every saved prefix and parked chat misses, and the disk tier "
                        f"fills again. Applying needs --confirm-fingerprint as well.")
    if shadowed:
        warnings.append(f"{', '.join(shadowed)}: the overlay's value has no effect (a later rule decides)")
    out["warnings"] = warnings
    return out


def check_confirm(p: dict, confirm: str | None, confirm_fp: str | None) -> None:
    if confirm != p["confirm"]:
        raise Refusal("confirm_required" if confirm is None else "confirm_mismatch",
                      "apply needs --confirm with the token of a plan of exactly these changes "
                      "on the current version", EXIT_REFUSED, plan=p)
    if "confirm_fingerprint" in p and confirm_fp != p["confirm_fingerprint"]:
        raise Refusal("confirm_required" if confirm_fp is None else "confirm_mismatch",
                      "these changes make the disk cache miss: apply needs --confirm-fingerprint "
                      "with the plan's second token", EXIT_REFUSED, plan=p)
