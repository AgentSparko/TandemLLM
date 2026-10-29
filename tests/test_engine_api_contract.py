"""The engine API contract v1, the engine's side (SRV-748): app/contracts/engine-api-v1 against the
engine's code. TandemApp's side is app/tests/test_engine_api_contract.py; the live engine is
tests/test_engine_api_live.py.

  1  every path of routes.json's routes and app_routes_via_engine is one the engine dispatches
     (server/app.py, server/setup_api.py, ops/engine_status.py for the status responder,
     inproc.ROUTES, and the two in-process app modules that own a prefix); the engine-only
     screens reach their inproc.ROUTES handler                    test_every_route_is_dispatched
  2  server/engine_api.py VERSION, routes.json's version, ops/engine_status.py's literal and
     TandemApp's proxy/engineapi.py SPEAKS are one version      test_one_version
  3  reverse-deps.json equals the inventory this file computes from the engine's tree (python
     tests/test_engine_api_contract.py --write writes it); each read AST cannot see names a file
     that holds its mark; README.md lists every engine->app import    test_reverse_deps
  4  the copies held equal until ENG-413: same_origin on a table of cases (server/setup_api.py,
     tandemapp/auth.py), TIER_OF (server/cache_peek.py, tandemapp/usage.py), the OFF_WHY texts
     (server/decider_api.py, server/conductor/shadow.py, tandemapp/conductor/routes.py)
                                                                  test_copies_hold_equal
  5  setup/: estimate-cases.json is models/estimate-cases.json byte for byte, limits.json's
     engine_max_slots is the registry's ENGINE_MAX_SLOTS          test_setup_fixtures
  6  node/: ops/serve.env carries PORT= and QSE_STATUS_PORT= as serve.env.example does and
     install.sh writes the status port as PORT + 9; the bin/tandem install.sh writes takes the
     control.json verbs and install.sh its flags; ops/fetch_weights.verify accepts a folder laid
     out as hub_manifest.example.json says and refuses a changed byte  test_node_fixtures
  7  the byte copies (test helpers, contract_check, the licenses, the setup fixture) are
     identical                                                    test_byte_copies

CPU only, no torch, standard library only. Run: python tests/test_engine_api_contract.py
"""

from __future__ import annotations

import ast
import functools
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
CONTRACT = os.path.join(ROOT, "app", "contracts", "engine-api-v1")
REVERSE = os.path.join(CONTRACT, "reverse-deps.json")
#: TandemApp's top-level packages, run in the engine's process until ENG-413 (server/__init__.py)
APP_PACKAGES = ("tandemapp", "proxy", "chatd")
#: the engine's tree the inventory reads; app/ is TandemApp's
ENGINE_DIRS = ("bench", "engine", "manual", "models", "ops", "server", "tests", "tools")
SKIP_DIRS = ("__pycache__", "node_modules")


def _routes() -> dict:
    with open(os.path.join(CONTRACT, "routes.json")) as f:
        return json.load(f)


# ---------------------------------------------------------------------------- the inventory

@functools.lru_cache(maxsize=None)
def _names(d: str) -> frozenset:
    try:
        return frozenset(os.listdir(d))
    except OSError:
        return frozenset()


def _exists(*parts: str) -> bool:
    """Whether ROOT/parts exists, each part in its exact case (a case-blind file system would read
    `monitor.Journal` as the module journal.py)."""
    d = ROOT
    for part in "/".join(parts).split("/"):
        if part not in _names(d):
            return False
        d = os.path.join(d, part)
    return True


def _app_module(name: str, path: str) -> str | None:
    """`name` as the reverse-deps entry when the engine file `path` imports it from TandemApp's
    tree: a package of APP_PACKAGES, a `tools.X` only app/tools has (the namespace `tools` spans
    both trees once app/ is on the path), or, in a test, a bare name only app/tests or app/tools
    has (a test that puts that directory on the path)."""
    top = name.split(".")[0]
    if top in APP_PACKAGES:
        return name
    if top == "tools" and name.count(".") == 1:
        mod = name.split(".")[1]
        if _exists("app", "tools", mod + ".py") and not _exists("tools", mod + ".py"):
            return name
    if "." not in name and path.startswith("tests/"):
        for d in ("tests", "tools"):
            if _exists("app", d, name + ".py") and not _exists(d, name + ".py"):
                return f"app/{d}/{name}.py"
    return None


def _is_app_path(parts: list[str]) -> bool:
    return _exists("app", *parts)


def _scan(path: str, tree: ast.AST) -> tuple[set, set]:
    mods, reads = set(), set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                m = _app_module(a.name, path)
                if m:
                    mods.add(m)
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            for a in n.names:
                full = f"{n.module}.{a.name}"
                sub = full.split(".")
                if (n.module.split(".")[0] in APP_PACKAGES or n.module == "tools") and \
                        _app_module(full, path) and \
                        (_exists("app", *sub[:-1], sub[-1] + ".py") or _exists("app", *sub)):
                    mods.add(full)                              # a module
                elif n.module.split(".")[0] in APP_PACKAGES:
                    mods.add(f"{n.module}:{a.name}")             # a name in a module
                elif _app_module(n.module, path):
                    mods.add(_app_module(n.module, path))
        elif isinstance(n, ast.Call) and \
                getattr(n.func, "attr", getattr(n.func, "id", None)) == "import_module" and \
                n.args and isinstance(n.args[0], ast.Constant) and \
                isinstance(n.args[0].value, str) and _app_module(n.args[0].value, path):
            mods.add(n.args[0].value)
        # a path into app/: "app" then a part of the tree in one call, tuple or list
        seqs = [n.args] if isinstance(n, ast.Call) else \
            [n.elts] if isinstance(n, (ast.Tuple, ast.List)) else []
        for s in seqs:
            for i, a in enumerate(s):
                if not (isinstance(a, ast.Constant) and a.value == "app"):
                    continue
                if i + 1 == len(s):
                    reads.add("app/")
                elif isinstance(s[i + 1], ast.Constant) and isinstance(s[i + 1].value, str) and \
                        _is_app_path([s[i + 1].value]):
                    reads.add("app/" + s[i + 1].value)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and \
                n.value.startswith("app/") and not any(c.isspace() for c in n.value):
            part = n.value.split("/")[1]
            if not part:
                reads.add("app/")
            elif _is_app_path([part]):
                reads.add("app/" + part)
    return mods, reads


def inventory(root: str = ROOT) -> dict:
    """What the engine's Python reaches of TandemApp's tree, from the AST of every .py outside
    app/: `imports` and `reads` of the engine's code, `tests` for tests/."""
    out = {"imports": {}, "reads": {}, "tests": {}}
    files = []
    for top in ENGINE_DIRS:
        for base, dirs, names in os.walk(os.path.join(root, top)):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
            files += [os.path.join(base, x) for x in names if x.endswith(".py")]
    for full in sorted(files):
        rel = os.path.relpath(full, root).replace(os.sep, "/")
        try:
            with open(full, encoding="utf-8", errors="replace") as f:
                tree = ast.parse(f.read())
        except SyntaxError:
            continue
        mods, reads = _scan(rel, tree)
        if rel.startswith("tests/"):
            if mods or reads:
                out["tests"][rel] = {"imports": sorted(mods), "reads": sorted(reads)}
            continue
        if mods:
            out["imports"][rel] = sorted(mods)
        if reads:
            out["reads"][rel] = sorted(reads)
    return out


def write_reverse_deps() -> None:
    """reverse-deps.json's inventory from this tree; its hand-kept sections stay."""
    with open(REVERSE) as f:
        doc = json.load(f)
    doc.update(inventory())
    with open(REVERSE, "w") as f:
        json.dump(doc, f, indent=1)
        f.write("\n")


# ---------------------------------------------------------------------------- dispatch

def _constants(path: str) -> set:
    with open(os.path.join(ROOT, path)) as f:
        tree = ast.parse(f.read())
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            out.add(n.value)
    return out


def _sample(pattern: str) -> str:
    return re.sub(r"\{[^}]+\}", "x1", pattern).replace("*", "x1")


def _dispatched(path: str, consts: set) -> bool:
    s = _sample(path)
    for c in consts:
        if not c.startswith("/") or len(c) < 2:
            continue
        if c == s or c.rstrip("/") == s.rstrip("/") or (c.endswith("/") and s.startswith(c)):
            return True
    return False


def _setup_parts_known(path: str, consts: set) -> list[str]:
    """The literal parts after /v1/setup/ that the dispatcher names nowhere."""
    rest = path[len("/v1/setup/"):]
    return [p for p in rest.split("/") if p and not p.startswith("{") and p not in consts]


def test_every_route_is_dispatched():
    doc = _routes()
    engine = _constants("server/app.py")
    setup = _constants("server/setup_api.py")
    status = _constants("ops/engine_status.py")
    # app modules that own a prefix and run in the engine's process until ENG-413
    inproc_app = _constants("app/tandemapp/chat_forward.py") | \
        _constants("app/tandemapp/session_tokens.py")
    misses = []
    for r in doc["routes"]:
        for p in [r["path"]] + r["aliases"]:
            if r.get("port") == "QSE_STATUS_PORT":
                ok = _dispatched(p, status) and (not p.startswith("/v1/setup/") or
                                                 not _setup_parts_known(p, status | setup))
            else:
                ok = _dispatched(p, engine)
                if ok and p.startswith("/v1/setup/"):
                    ok = not _setup_parts_known(p, setup)
            if not ok:
                misses.append(f"{r['method']} {p} (group {r['group']})")
    for r in doc["app_routes_via_engine"]:
        p = r["path"].replace("{token}", "x1")
        ok = _dispatched(p, engine | inproc_app)
        if not ok and p.startswith("/s/"):
            ok = "/s/" in inproc_app
        if not ok and p.endswith("*"):
            ok = _dispatched(p.rstrip("*").rstrip("/"), engine | inproc_app)
        if not ok:
            misses.append(f"app route {r['path']}")
    from server.conductor import inproc
    for s in doc["engine_screens_until_eng413"]:
        if not _dispatched(s["path"], engine):
            misses.append(f"screen {s['path']}")
        m = re.match(r"inproc\.ROUTES\[(\w+)\]", s["handler"])
        if m and m.group(1) not in inproc.ROUTES:
            misses.append(f"screen {s['path']}: no inproc.ROUTES[{m.group(1)}]")
    assert not misses, misses
    n = sum(1 + len(r["aliases"]) for r in doc["routes"])
    return f"{n} paths of {len(doc['routes'])} routes, {len(doc['app_routes_via_engine'])} app " \
           f"routes, {len(doc['engine_screens_until_eng413'])} screens"


def _literal(path: str, name: str):
    with open(os.path.join(ROOT, path)) as f:
        tree = ast.parse(f.read())
    for n in tree.body:
        if isinstance(n, ast.Assign) and any(getattr(t, "id", None) == name for t in n.targets):
            return ast.literal_eval(n.value)
    raise AssertionError(f"{path} has no {name}")


def test_one_version():
    from server import engine_api
    got = {"server/engine_api.py VERSION": engine_api.VERSION,
           "routes.json version": _routes()["version"],
           "ops/engine_status.py ENGINE_API": _literal("ops/engine_status.py", "ENGINE_API"),
           "app/proxy/engineapi.py SPEAKS": _literal("app/proxy/engineapi.py", "SPEAKS")}
    assert len(set(got.values())) == 1, got
    assert re.fullmatch(r"\d+\.\d+", engine_api.VERSION), engine_api.VERSION
    with open(os.path.join(ROOT, "server", "app.py")) as f:
        src = f.read()
    assert src.count('"engine_api": engine_api.VERSION') == 2       # the full and setup answers
    return f"engine API {engine_api.VERSION} in 4 places"


def test_reverse_deps():
    with open(REVERSE) as f:
        doc = json.load(f)
    got = inventory()
    for key in ("imports", "reads", "tests"):
        assert doc[key] == got[key], (
            f"reverse-deps.json {key} differs from the tree "
            f"(python tests/test_engine_api_contract.py --write): "
            f"only in the file {sorted(set(doc[key]) - set(got[key]))[:5]}, only in the tree "
            f"{sorted(set(got[key]) - set(doc[key]))[:5]}, changed "
            f"{[k for k in doc[key] if k in got[key] and doc[key][k] != got[key][k]][:5]}")
    bad = []
    for e in doc["not_ast"]:
        for f in e["files"]:
            if not _exists(*f.split("/")):
                bad.append(f"{f}: missing")
                continue
            with open(os.path.join(ROOT, f), encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            bad += [f"{f}: no {m!r}" for m in e["marks"] if m not in text]
        bad += [f"{n}: missing" for n in e["names"] if not _exists(*n.rstrip("/").split("/"))]
    assert not bad, bad
    with open(os.path.join(CONTRACT, "README.md")) as f:
        readme = f.read()
    unlisted = []
    for path, mods in doc["imports"].items():
        if f"`{path}`" not in readme:
            unlisted.append(path)
        unlisted += [f"{path}: {m}" for m in {m.split(":")[0] for m in mods}
                     if f"`{m}`" not in readme]
    assert not unlisted, unlisted
    return f"{len(doc['imports'])} engine files import app code, {len(doc['reads'])} read it, " \
           f"{len(doc['tests'])} test files reach it, {len(doc['not_ast'])} reads outside the AST"


ORIGIN_CASES = [
    {},
    {"Content-Type": "application/json"},
    {"Content-Type": "application/json; charset=utf-8", "Origin": "http://box:8000",
     "Host": "box:8000"},
    {"Content-Type": "application/json", "Origin": "http://evil.example", "Host": "box:8000"},
    {"Content-Type": "application/json", "Origin": "null", "Host": "box:8000"},
    {"Content-Type": "application/json", "Origin": "https://box", "Host": "x",
     "X-Forwarded-Host": "box"},
    {"Content-Type": "text/plain", "Origin": "http://box:8000", "Host": "box:8000"},
    {"Content-Type": "application/json", "Sec-Fetch-Site": "cross-site"},
    {"Content-Type": "application/json", "Sec-Fetch-Site": "same-origin",
     "Origin": "http://BOX:8000", "Host": "box:8000"},
    {"Origin": "", "Host": "box"},
]


def test_copies_hold_equal():
    from server import cache_peek, decider_api, setup_api
    from server.conductor import shadow
    from tandemapp import auth, usage
    from tandemapp.conductor import routes
    for h in ORIGIN_CASES:
        assert setup_api.same_origin(h) == auth.same_origin(h), h
        assert setup_api.same_origin_json(h) == auth.same_origin_json(h), h
    assert cache_peek.TIER_OF == usage.TIER_OF
    assert decider_api.OFF_WHY == routes.DECIDERS_OFF_WHY
    assert shadow.OFF_WHY == routes.SHADOW_OFF_WHY
    return f"same_origin on {len(ORIGIN_CASES)} cases, TIER_OF, 2 OFF_WHY texts"


def test_setup_fixtures():
    with open(os.path.join(ROOT, "models", "estimate-cases.json"), "rb") as f:
        mine = f.read()
    with open(os.path.join(CONTRACT, "setup", "estimate-cases.json"), "rb") as f:
        theirs = f.read()
    assert mine == theirs, "setup/estimate-cases.json is not models/estimate-cases.json"
    with open(os.path.join(CONTRACT, "setup", "limits.json")) as f:
        limits = json.load(f)
    reg = _literal("server/setup/registry.py", "ENGINE_MAX_SLOTS")
    assert limits["engine_max_slots"] == reg, (limits, reg)
    return f"estimate cases {len(mine)} bytes, engine_max_slots {reg}"


def _env_lines(text: str) -> dict:
    return {m.group(1): m.group(2) for m in re.finditer(r"(?m)^([A-Z_]+)=(.*)$", text)}


def test_node_fixtures():
    with open(os.path.join(CONTRACT, "node", "serve.env.example")) as f:
        example = _env_lines(f.read())
    assert set(example) == {"PORT", "QSE_STATUS_PORT"}, example
    assert all(v.isdigit() for v in example.values()), example
    with open(os.path.join(ROOT, "ops", "serve.env")) as f:
        tmpl = _env_lines(f.read())
    assert tmpl["PORT"] == example["PORT"], tmpl["PORT"]
    assert "QSE_STATUS_PORT" in tmpl                     # empty: install.sh sets it
    with open(os.path.join(ROOT, "install.sh")) as f:
        inst = f.read()
    assert re.search(r'(?m)^setv PORT "\$PORT"$', inst)
    assert re.search(r'(?m)^\[ "\$PORT" -le 65526 \] && setv QSE_STATUS_PORT "\$\(\(PORT \+ 9\)\)"$',
                     inst)
    assert int(example["QSE_STATUS_PORT"]) == int(example["PORT"]) + 9
    # bin/tandem's verbs, as install.sh writes it
    with open(os.path.join(CONTRACT, "node", "control.json")) as f:
        control = json.load(f)
    fn = inst[inst.index("write_control() {"):inst.index("\nCTL\n}\n") + len("\nCTL\n}\n")]
    d = tempfile.mkdtemp(prefix="engine-api-ctl-")
    script = subprocess.run(["bash", "-c", fn + '\nDIR="$1"; write_control', "-", d],
                            capture_output=True, text=True, check=True).stdout
    body = script[script.rindex('case "${1:-}" in'):]
    arms = set(re.findall(r"(?m)^    ([a-z|]+)\)", body))
    verbs = {v for a in arms for v in a.split("|")}
    assert set(control["verbs"]) <= verbs, (control["verbs"], sorted(verbs))
    flags = set(re.findall(r"(?m)^        (--[a-z-]+)(?:\|-[a-z])?\)", inst))
    assert set(control["install"]["flags"]) <= flags, (control["install"]["flags"], flags)
    os.rmdir(d)
    # a folder laid out as the hub manifest example says: each file `size` zero bytes
    sys.path.insert(0, os.path.join(ROOT, "ops"))
    import fetch_weights
    with open(os.path.join(CONTRACT, "node", "hub_manifest.example.json")) as f:
        man = json.load(f)
    assert set(man) == {"repo", "revision", "commit", "files"}, set(man)
    with tempfile.TemporaryDirectory(prefix="engine-api-pack-") as pack:
        for name, row in man["files"].items():
            with open(os.path.join(pack, name), "wb") as f:
                f.write(b"\0" * row["size"])
            assert row["sha256"] in (None, hashlib.sha256(b"\0" * row["size"]).hexdigest()), name
        with open(os.path.join(pack, fetch_weights.MANIFEST), "w") as f:
            json.dump(man, f, indent=1)
        assert fetch_weights.verify(pack) == []
        hashed = next(n for n, r in sorted(man["files"].items()) if r["sha256"])
        with open(os.path.join(pack, hashed), "r+b") as f:
            f.write(b"\1")
        assert fetch_weights.verify(pack) != []
    return f"PORT {example['PORT']}, status {example['QSE_STATUS_PORT']}, verbs " \
           f"{'/'.join(control['verbs'])}, {len(control['install']['flags'])} flags, " \
           f"{len(man['files'])} files verified"


def test_byte_copies():
    with open(REVERSE) as f:
        doc = json.load(f)
    pairs = [(c["engine"], c["app"]) for c in doc["copies"] if c.get("kind") == "file"]
    assert pairs
    diff = []
    for a, b in pairs:
        with open(os.path.join(ROOT, a), "rb") as f1, open(os.path.join(ROOT, b), "rb") as f2:
            if f1.read() != f2.read():
                diff.append(f"{a} != {b}")
    assert not diff, diff
    return f"{len(pairs)} file pairs identical"


if __name__ == "__main__":
    if sys.argv[1:] == ["--write"]:
        write_reverse_deps()
        print(f"wrote {os.path.relpath(REVERSE, ROOT)}")
        sys.exit(0)
    from scriptmode import run
    run(globals())
