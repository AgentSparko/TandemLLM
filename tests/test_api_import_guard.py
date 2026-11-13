"""G5 for ENG-413 WP2a (QA ENG-432): the engine's wire format is the engine's own code.

server/api holds the engine's copies of the modules it took from TandemApp: toolcall, toolformats,
callrepair, refusal, thinking, argloop. After WP2a only TandemApp's own tree (app/, the extension
side) imports TandemApp's copies; the engine does not.

  1  no engine file outside app/ imports one of those modules from TandemApp, by import statement,
     by importlib name or in a code string; no engine script or config names one, except the
     files ALLOWED says why                                    test_no_engine_file_imports_them
  2  server/api imports only the standard library and server/api itself    test_api_stands_alone
  3  the importers the plan names (server/app.py, server/compat.py, server/endalign.py,
     server/setup/wren.py, tools/argloop_fp.py) take each module from server/api
                                                                  test_the_importers_use_the_engine_copy
  4  in a fresh process the engine copy, server/compat.py and server/endalign.py load no TandemApp
     module; compat's Refusal is server/api's; the forms register; the default effort caps are
     the seed's                                                 test_a_process_loads_no_app_module
  5  thinking.LEVELS (the effort caps without a conductor) is the conductor's seed, thinking and
     budget per level (until the extension hands its levels in, ENG-413 WP3)
                                                                  test_the_seed_levels_are_the_conductors

CPU, standard library only. Run: python tests/test_api_import_guard.py
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

#: TandemApp's module -> the engine's copy (ENG-413 WP2a)
MOVED = {"tandemapp.toolcall": "server.api.toolcall",
         "tandemapp.toolformats": "server.api.toolformats",
         "tandemapp.callrepair": "server.api.callrepair",
         "tandemapp.refusal": "server.api.refusal",
         "tandemapp.thinking": "server.api.thinking",
         "tandemapp.argloop": "server.api.argloop"}
#: the engine's tree (app/ is TandemApp's)
ENGINE_DIRS = ("bench", "engine", "manual", "models", "ops", "server", "tests", "tools")
SKIP_DIRS = ("__pycache__", "node_modules")
TEXT_SUFFIXES = (".sh", ".yaml", ".yml", ".env", ".toml", ".cfg", ".txt")
#: files that may name a moved module of TandemApp's in text, and why
ALLOWED = {
    "tests/test_api_import_guard.py": "this guard names what it looks for",
    "tools/gate/map.yaml": "a vendored change to TandemApp's copies still takes the server's gate: "
                           "its conductor runs them in the engine's process in chat mode",
}
NAMES = re.compile(r"\btandemapp[./](toolcall|toolformats|callrepair|refusal|thinking|argloop)\b")
#: the files the plan switches (section 3.1, WP2a), with the modules each must take from server/api
IMPORTERS = {"server/app.py": {"toolformats", "callrepair", "toolcall", "thinking", "argloop"},
             "server/compat.py": {"refusal", "toolformats"},
             "server/endalign.py": {"toolcall"},
             "server/setup/wren.py": {"toolcall"},
             "tools/argloop_fp.py": {"argloop"}}


def _files(suffixes: tuple) -> list[str]:
    out = []
    for top in ENGINE_DIRS:
        for base, dirs, names in os.walk(os.path.join(ROOT, top)):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
            out += [os.path.relpath(os.path.join(base, n), ROOT).replace(os.sep, "/")
                    for n in sorted(names) if n.endswith(suffixes)]
    return sorted(out)


def _moved(name: str) -> str | None:
    """The moved module `name` is (or is inside), else None."""
    for m in MOVED:
        if name == m or name.startswith(m + "."):
            return m
    return None


def imports_of(tree: ast.AST) -> list[tuple[int, str]]:
    """(line, module) of every import in `tree`: statements (each `from X import y` also as
    X.y, since y may be a module), importlib calls with a literal name, and the imports in a code
    string (`python -c`, a child's source)."""
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out += [(n.lineno, a.name) for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            out.append((n.lineno, n.module))
            out += [(n.lineno, f"{n.module}.{a.name}") for a in n.names]
        elif isinstance(n, ast.Call) and \
                getattr(n.func, "attr", getattr(n.func, "id", None)) in ("import_module", "__import__") and \
                n.args and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str):
            out.append((n.lineno, n.args[0].value))
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and "import" in n.value:
            for m in re.finditer(r"(?m)^\s*(?:from\s+([\w.]+)\s+import\s+([\w, ]+)|import\s+([\w.]+))",
                                 n.value):
                if m.group(3):
                    out.append((n.lineno, m.group(3)))
                else:
                    out.append((n.lineno, m.group(1)))
                    out += [(n.lineno, f"{m.group(1)}.{x.strip()}") for x in m.group(2).split(",")
                            if x.strip()]
    return out


def test_no_engine_file_imports_them():
    bad = []
    py = _files((".py",))
    for rel in py:
        with open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace") as f:
            src = f.read()
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for line, name in imports_of(tree):
            m = _moved(name)
            if m and rel not in ALLOWED:
                bad.append(f"{rel}:{line} imports {name} (the engine's copy is {MOVED[m]})")
    text = _files(TEXT_SUFFIXES)
    for rel in text:
        with open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace") as f:
            for i, ln in enumerate(f, 1):
                if NAMES.search(ln) and rel not in ALLOWED:
                    bad.append(f"{rel}:{i} names {NAMES.search(ln).group(0)}")
    assert not bad, bad
    gone = [f for f in ALLOWED if not os.path.exists(os.path.join(ROOT, f))]
    assert not gone, f"ALLOWED names files that do not exist: {gone}"
    return f"{len(py)} Python files and {len(text)} scripts and configs: no engine import of " \
           f"{len(MOVED)} moved modules"


def test_api_stands_alone():
    std = set(getattr(sys, "stdlib_module_names", ())) or {
        "__future__", "ast", "bisect", "dataclasses", "importlib", "json", "os", "re", "shlex",
        "threading", "time", "uuid", "zlib"}
    std.add("__future__")
    api = os.path.join(ROOT, "server", "api")
    bad, n = [], 0
    for base, dirs, names in os.walk(api):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for x in sorted(names):
            if not x.endswith(".py"):
                continue
            n += 1
            rel = os.path.relpath(os.path.join(base, x), ROOT)
            with open(os.path.join(base, x)) as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level:
                    bad.append(f"{rel}:{node.lineno} relative import")
                    continue
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                    [node.module] if isinstance(node, ast.ImportFrom) else []
                for nm in names:
                    if nm.split(".")[0] not in std and not (nm == "server.api" or
                                                             nm.startswith("server.api.")):
                        bad.append(f"{rel}:{node.lineno} imports {nm}")
    assert n >= 9, n
    assert not bad, bad
    return f"{n} files, standard library and server.api only"


def test_the_importers_use_the_engine_copy():
    bad = []
    for rel, want in IMPORTERS.items():
        with open(os.path.join(ROOT, rel)) as f:
            got = imports_of(ast.parse(f.read()))
        took = {name.split(".")[2] for _, name in got
                if name.startswith("server.api.") and name.split(".")[2] in want}
        if took != want:
            bad.append(f"{rel}: takes {sorted(took)} from server/api, wants {sorted(want)}")
    assert not bad, bad
    return f"{len(IMPORTERS)} importers"


CHILD = """
import sys
sys.path.insert(0, sys.argv[1])
from server.api import argloop, callrepair, refusal, thinking, toolcall, toolformats
from server import compat, endalign
assert compat.Refusal is refusal.Refusal
toolformats.get(None)
print("forms", ",".join(sorted(toolformats.FORMATS)))
print("caps", sorted(thinking.caps().items()))
a = '<tool_call>\\n<function=f>\\n<parameter=x>\\n1\\n</parameter>\\n</function>\\n</tool_call>'
print("same", endalign.same_calls(a, a))
print("app", sorted(m for m in sys.modules if m.split(".")[0] in ("tandemapp", "proxy", "chatd")))
"""


def test_a_process_loads_no_app_module():
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    with tempfile.TemporaryDirectory(prefix="api-guard-") as cwd:
        p = subprocess.run([sys.executable, "-I", "-B", "-c", CHILD, ROOT], capture_output=True,
                           text=True, timeout=120, env=env, cwd=cwd)
    assert p.returncode == 0, (p.stdout[-2000:], p.stderr[-2000:])
    out = dict(ln.split(" ", 1) for ln in p.stdout.splitlines())
    assert out["app"] == "[]", out["app"]
    assert out["forms"] == "argkey,pythonic,xml", out["forms"]
    assert out["caps"] == str(sorted({"low": 1024, "medium": 8192, "high": 16384,
                                      "xhigh": 0}.items())), out["caps"]
    assert out["same"] == "True", out
    return "server.api, compat and endalign load no TandemApp module; 3 forms; seed caps"


def test_the_seed_levels_are_the_conductors():
    from server.api import thinking
    from tandemapp.conductor import schema      # the extension's seed, until it hands levels in
    theirs = {k: {"thinking": v.get("thinking"), "budget": v.get("budget")}
              for k, v in schema.ROUTER["effort"]["levels"].items()}
    assert thinking.LEVELS == theirs, (thinking.LEVELS, theirs)
    assert thinking.caps() == thinking.caps(schema.ROUTER["effort"]["levels"])
    return f"{len(theirs)} levels"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
