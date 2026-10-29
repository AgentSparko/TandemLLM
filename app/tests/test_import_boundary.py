"""TandemApp never imports or runs the engine (SRV-748): every .py under app/ (plugins/ included;
dashboard/node_modules, dashboard/dist and __pycache__ left out) and the dashboard's own reads.

  1  no import at any depth (lazy, in a try, under TYPE_CHECKING) whose top name is server,
     engine, models, bench or manual; no tools.X or tests.X that app/tools or app/tests does
     not hold; no literal importlib.import_module, __import__, runpy.run_module/run_path or
     importlib.util.spec_from_file_location naming a server./engine. module or a path under
     server/ or engine/                                          test_no_engine_import
  2  no argv list or command string given to subprocess.*, os.exec*, os.spawn*, os.system or
     asyncio's subprocesses with '-m' and a server.* or engine.* module, a path under server/ or
     engine/ (server/app.py among them), or a '-c' code that imports server or engine; a '-m'
     server.*/engine.* pair in any list or tuple counts too   test_no_engine_exec
  3  no torch, numpy or triton import at module level outside a try that catches ImportError
                                                                  test_no_bare_heavy_import
  4  torch, numpy and triton are imported only at the sites in HOOKS, each an engine-process hook
     until ENG-413; every site there still holds its import     test_heavy_imports_only_at_hooks
  5  the dashboard reads nothing outside app/: new URL(..., import.meta.url), fileURLToPath,
     join/resolve(__dirname, ...) and import/export ... from in mock, tests, e2e, scripts, src
     and the vite and playwright configs                     test_dashboard_reads_stay_in_app
  6  a child process (python -I -B, app/ the only path of the tree) imports every module of
     proxy, chatd and tandemapp with server, engine, torch, numpy, triton, models and bench
     blocked; none fails, and none binds, listens, forks or starts a process while it imports
                                                         test_every_module_imports_alone
  7  the checks above find a lazy 'from server import app', an argv ['python', '-m',
     'server.app'] and a '-c' 'import engine.cache' in a made-up tree and name each x.py:N
                                                                  test_the_checks_find_planted_hits
     and four reads out of a made-up dashboard        test_the_dashboard_check_finds_planted_reads

Each hit is one line: app/<file>:<line>: <source> -- TandemApp reaches the engine; use the engine
API (app/contracts/engine-api-v1). Checks 1 and 2 allow nothing.

Standard library only. Run: python tests/test_import_boundary.py
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, HERE)

WHY = "TandemApp reaches the engine; use the engine API (app/contracts/engine-api-v1)"
#: top-level names of the engine's tree
ENGINE_TOPS = ("server", "engine", "models", "bench", "manual")
HEAVY = ("torch", "numpy", "triton")
#: (file under app/, function or <module>) -> why; each an engine-process hook until ENG-413
HOOKS = {
    ("tandemapp/metrics.py", "_gpu_memory"): "engine-process hook until ENG-413: GPU gauge",
    ("tandemapp/metrics.py", "_gpu_reserved"): "engine-process hook until ENG-413: GPU gauge",
    ("tandemapp/metrics.py", "_unified_free"): "engine-process hook until ENG-413: GPU gauge",
    ("tandemapp/conductor/router.py", "_apply"): "engine-process hook until ENG-413: torch tensors",
    ("tandemapp/dashboard_api.py", "<module>"):
        "engine-process hook until ENG-413: numpy percentiles, sorted() without it",
}
SKIP = ("dashboard/node_modules", "dashboard/dist")
EXEC_MODULES = {
    "subprocess": {"run", "call", "check_call", "check_output", "Popen", "getoutput",
                   "getstatusoutput"},
    "os": {"system", "popen", "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp",
           "execvpe", "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp",
           "spawnvpe", "posix_spawn", "posix_spawnp"},
    "asyncio": {"create_subprocess_exec", "create_subprocess_shell"},
}
ENGINE_PATH = re.compile(r"(?:^|[\s/'\"=*])(?:server|engine)/")
ENGINE_M = re.compile(r"(?:^|\s)-m\s+(?:server|engine)(?:\.|\s|$)")


def _hit(rel: str, line: int, source: str) -> str:
    return f"app/{rel}:{line}: {source.strip()} -- {WHY}"


def py_files(app: str = APP) -> list[str]:
    """Every .py under app/, as paths relative to it."""
    out = []
    for base, dirs, names in os.walk(app):
        rel_base = os.path.relpath(base, app).replace(os.sep, "/")
        dirs[:] = sorted(d for d in dirs if d != "__pycache__" and
                         (d if rel_base == "." else f"{rel_base}/{d}") not in SKIP)
        out += [os.path.normpath(os.path.join(rel_base, n)).replace(os.sep, "/")
                for n in sorted(names) if n.endswith(".py")]
    return out


def _parse(app: str, rel: str):
    with open(os.path.join(app, rel), encoding="utf-8") as f:
        src = f.read()
    return src, ast.parse(src, filename=rel)


def _line(src: str, n: int) -> str:
    lines = src.splitlines()
    return lines[n - 1] if 0 < n <= len(lines) else ""


# ------------------------------------------------------------------------------- 1 imports

def _held(app: str, sub: str, name: str) -> bool:
    p = os.path.join(app, sub, name)
    return os.path.isfile(p + ".py") or os.path.isdir(p)


def _bad_module(app: str, name: str) -> bool:
    parts = name.split(".")
    if parts[0] in ENGINE_TOPS:
        return True
    return parts[0] in ("tools", "tests") and len(parts) > 1 and not _held(app, parts[0], parts[1])


def _str_path(node) -> str | None:
    """A path a call argument spells: a literal, an f-string or os.path.join/Path(...) of
    literals; '*' stands for a part that is not literal."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "*" for v in node.values)
    if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) in \
            ("join", "Path", "PurePath"):
        return "/".join(_str_path(a) or "*" for a in node.args)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return f"{_str_path(node.left) or '*'}/{_str_path(node.right) or '*'}"
    return None


def _imports(tree) -> list[tuple[int, str]]:
    """(line, module) for every import of an engine module in `tree`, at any depth."""
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out += [(n.lineno, a.name) for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            out.append((n.lineno, n.module))
            if n.module in ("tools", "tests"):
                out += [(n.lineno, f"{n.module}.{a.name}") for a in n.names]
        elif isinstance(n, ast.Call) and n.args:
            fn = getattr(n.func, "attr", getattr(n.func, "id", ""))
            arg = n.args[0] if fn != "spec_from_file_location" or len(n.args) < 2 else n.args[1]
            if fn in ("import_module", "__import__", "run_module") and \
                    isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str):
                out.append((n.lineno, n.args[0].value))
            elif fn in ("run_path", "spec_from_file_location"):
                path = _str_path(arg)
                if path is not None and ENGINE_PATH.search(path):
                    out.append((n.lineno, "server" if "server/" in path else "engine"))
    return out


def import_hits(app: str = APP) -> list[str]:
    hits = []
    for rel in py_files(app):
        src, tree = _parse(app, rel)
        hits += [_hit(rel, ln, _line(src, ln)) for ln, mod in _imports(tree)
                 if _bad_module(app, mod)]
    return hits


# ------------------------------------------------------------------------------ 2 execution

def _exec_names(tree) -> set:
    """Names bound to exec functions by `from subprocess import run` and the like."""
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module in EXEC_MODULES:
            out |= {a.asname or a.name for a in n.names if a.name in EXEC_MODULES[n.module]}
    return out


def _is_exec(call: ast.Call, names: set) -> bool:
    f = call.func
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
        return f.attr in EXEC_MODULES.get(f.value.id, ())
    return isinstance(f, ast.Name) and f.id in names


def _module_strings(tree) -> dict:
    """NAME -> text for module-level `NAME = "..."`."""
    out = {}
    for n in tree.body:
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant) and \
                isinstance(n.value.value, str):
            out.update({t.id: n.value.value for t in n.targets if isinstance(t, ast.Name)})
    return out


def _code_imports_engine(code: str) -> bool:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return bool(re.search(r"\b(?:import|from)\s+(?:server|engine)\b", code))
    return any(m.split(".")[0] in ("server", "engine") for _, m in _imports(tree))


def _bad_argv(elts: list, consts: dict, paths: bool) -> bool:
    texts = []
    for e in elts:
        if isinstance(e, ast.Name) and e.id in consts:
            texts.append(consts[e.id])
        else:
            literal = e.value if isinstance(e, ast.Constant) and isinstance(e.value, str) else None
            texts.append(_str_path(e) if paths else literal)
    for i, t in enumerate(texts):
        nxt = texts[i + 1] if i + 1 < len(texts) else None
        if t == "-m" and nxt and nxt.split(".")[0] in ("server", "engine"):
            return True
        if t == "-c" and nxt and _code_imports_engine(nxt):
            return True
        if paths and t and t != "-c" and (i == 0 or texts[i - 1] != "-c") and \
                (ENGINE_PATH.search(t) or t.startswith(("server/", "engine/"))):
            return True
    return False


def _bad_command(text: str) -> bool:
    return bool(ENGINE_M.search(text) or ENGINE_PATH.search(text) or
                text.startswith(("server/", "engine/")) or
                re.search(r"-c\s+['\"][^'\"]*\b(?:import|from)\s+(?:server|engine)\b", text))


def exec_hits(app: str = APP) -> list[str]:
    hits = set()
    for rel in py_files(app):
        src, tree = _parse(app, rel)
        names, consts = _exec_names(tree), _module_strings(tree)
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and _is_exec(n, names):
                for a in list(n.args) + [k.value for k in n.keywords if k.arg == "args"]:
                    if isinstance(a, (ast.List, ast.Tuple)):
                        bad = _bad_argv(a.elts, consts, paths=True)
                    else:
                        text = consts.get(a.id) if isinstance(a, ast.Name) else _str_path(a)
                        bad = text is not None and _bad_command(text)
                    if bad:
                        hits.add((rel, n.lineno))
            elif isinstance(n, (ast.List, ast.Tuple)) and _bad_argv(n.elts, consts, paths=False):
                hits.add((rel, n.lineno))
    out = []
    for rel, ln in sorted(hits):
        src, _ = _parse(app, rel)
        out.append(_hit(rel, ln, _line(src, ln)))
    return out


# ------------------------------------------------------------------- 3 and 4 heavy imports

def _catches_import_error(t: ast.Try) -> bool:
    for h in t.handlers:
        types = h.type.elts if isinstance(h.type, ast.Tuple) else [h.type]
        if any(isinstance(x, ast.Name) and x.id in ("ImportError", "ModuleNotFoundError")
               for x in types):
            return True
    return False


def heavy_imports(app: str = APP) -> list[tuple[str, int, str, bool, str]]:
    """(file, line, function or <module>, guarded, source line) for every torch, numpy or triton
    import under app/; `guarded` is a try that catches ImportError around it."""
    out = []
    for rel in py_files(app):
        src, tree = _parse(app, rel)

        def walk(node, func, guarded):
            for child in ast.iter_child_nodes(node):
                f, g = func, guarded
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                    f = getattr(child, "name", "<lambda>")
                if isinstance(node, ast.Try) and child in node.body and _catches_import_error(node):
                    g = True
                if isinstance(child, (ast.Import, ast.ImportFrom)):
                    mods = [a.name for a in child.names] if isinstance(child, ast.Import) else \
                        [child.module or ""] if child.level == 0 else []
                    if any(m.split(".")[0] in HEAVY for m in mods):
                        out.append((rel, child.lineno, f, g, _line(src, child.lineno)))
                walk(child, f, g)
        walk(tree, "<module>", False)
    return out


# ---------------------------------------------------------------------- 5 dashboard reads

DASH_DIRS = ("mock", "tests", "e2e", "scripts", "src")
DASH_EXT = (".ts", ".tsx", ".mts", ".cts", ".js", ".mjs", ".cjs")
LIT = r"""(?:'[^'\n]*'|"[^"\n]*"|`[^`]*`)"""
URL_LIT = re.compile(r"new\s+URL\(\s*(" + LIT + r")\s*,\s*import\.meta\.url")
URL_VAR = re.compile(r"new\s+URL\(\s*[A-Za-z_$][\w$]*\s*,\s*import\.meta\.url")
DIRNAME = re.compile(r"\b(?:join|resolve)\(\s*__dirname((?:\s*,\s*" + LIT + r")*)")
FROM = re.compile(r"""(?:\bfrom|\bimport|\bimport\s*\()\s*(['"])(\.\.?/[^'"\n]*)\1""")
REL_LIT = re.compile(r"""(['"`])(\.\.?/[^'"`$\n]*)""")


def _lit(text: str) -> str:
    """A literal's text up to its first ${...}."""
    return text[1:-1].split("${")[0]


def dashboard_files(app: str = APP) -> list[str]:
    dash = os.path.join(app, "dashboard")
    out = []
    for d in DASH_DIRS:
        for base, dirs, names in os.walk(os.path.join(dash, d)):
            dirs[:] = sorted(x for x in dirs if x not in ("node_modules", "dist"))
            out += [os.path.join(base, n) for n in sorted(names) if n.endswith(DASH_EXT)]
    out += [os.path.join(dash, n) for n in sorted(os.listdir(dash) if os.path.isdir(dash) else [])
            if re.fullmatch(r"(vite|playwright[\w.-]*|pw-[\w.-]+)\.config\.[mc]?[jt]s", n)]
    return out


def dashboard_reads(app: str = APP) -> tuple[int, list[str]]:
    """(reads seen, hits): each relative path the dashboard resolves, against app/."""
    seen, hits = 0, []
    top = os.path.realpath(app)
    for path in dashboard_files(app):
        with open(path, encoding="utf-8") as f:
            src = f.read()
        found = []                                           # (offset, relative path)
        found += [(m.start(), _lit(m.group(1))) for m in URL_LIT.finditer(src)]
        for m in DIRNAME.finditer(src):
            parts = [_lit(x) for x in re.findall(LIT, m.group(1))]
            found.append((m.start(), "/".join(parts) or "."))
        found += [(m.start(), m.group(2)) for m in FROM.finditer(src)]
        if URL_VAR.search(src):                              # a helper: new URL(p, import.meta.url)
            found += [(m.start(), m.group(2)) for m in REL_LIT.finditer(src)]
        rel = os.path.relpath(path, app).replace(os.sep, "/")
        for off, p in found:
            seen += 1
            target = os.path.realpath(os.path.join(os.path.dirname(path), p))
            if target != top and not target.startswith(top + os.sep):
                ln = src.count("\n", 0, off) + 1
                hits.append(_hit(rel, ln, _line(src, ln)))
    return seen, sorted(set(hits))


# --------------------------------------------------------------------- 6 imports, alone

CHILD = r'''
import importlib, os, socket, subprocess, sys
APP = sys.argv[1]
BLOCK = ("server", "engine", "torch", "numpy", "triton", "models", "bench")
class Block:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCK:
            raise ModuleNotFoundError(f"blocked: {name}", name=name)
        return None
sys.meta_path.insert(0, Block())
sys.path = [APP] + [p for p in sys.path if p and not os.path.realpath(p).startswith(
    os.path.realpath(os.path.dirname(APP)) + os.sep)]
started = []
def refuse(what):
    def f(*a, **k):
        started.append(what)
        raise OSError(f"{what} refused while importing")
    return f
socket.socket.bind = refuse("bind")
socket.socket.listen = refuse("listen")
subprocess.Popen.__init__ = refuse("subprocess")
for name in ("fork", "forkpty", "system", "execv", "execve", "execvp", "execvpe", "posix_spawn",
             "posix_spawnp", "spawnv", "spawnve"):
    if hasattr(os, name):
        setattr(os, name, refuse(name))
mods = []
for top in ("proxy", "chatd", "tandemapp"):
    for base, dirs, names in os.walk(os.path.join(APP, top)):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for n in sorted(names):
            if n.endswith(".py") and n != "__main__.py":
                rel = os.path.relpath(os.path.join(base, n), APP)[:-3].replace(os.sep, ".")
                mods.append(rel[:-9] if rel.endswith(".__init__") else rel)
bad = []
for m in mods:
    try:
        importlib.import_module(m)
    except BaseException as e:
        bad.append(f"{m}: {type(e).__name__}: {str(e)[:200]}")
loaded = sorted(k for k in sys.modules if k.split(".")[0] in BLOCK)
print(len(mods), "modules")
for line in bad + [f"started {w}" for w in started] + [f"loaded {k}" for k in loaded]:
    print(line)
'''


def import_alone(app: str = APP) -> tuple[int, list[str]]:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH" and not k.startswith("QWEN38_")}
    with tempfile.TemporaryDirectory(prefix="tandemapp-imports-") as cwd:
        p = subprocess.run([sys.executable, "-I", "-B", "-c", CHILD, app], capture_output=True,
                           text=True, timeout=600, env=env, cwd=cwd)
    lines = p.stdout.splitlines()
    assert p.returncode == 0 and lines and lines[0].endswith(" modules"), \
        (p.returncode, p.stdout[-2000:], p.stderr[-2000:])
    return int(lines[0].split()[0]), lines[1:]


# ---------------------------------------------------------------------------------- tests

def test_no_engine_import():
    hits = import_hits()
    assert not hits, "\n" + "\n".join(hits)
    return f"{len(py_files())} files"


def test_no_engine_exec():
    hits = exec_hits()
    assert not hits, "\n" + "\n".join(hits)


def test_no_bare_heavy_import():
    hits = [_hit(rel, ln, src) for rel, ln, func, guarded, src in heavy_imports()
            if func == "<module>" and not guarded]
    assert not hits, "\n" + "\n".join(hits)


def test_heavy_imports_only_at_hooks():
    found = heavy_imports()
    hits = [_hit(rel, ln, src) for rel, ln, func, guarded, src in found if (rel, func) not in HOOKS]
    assert not hits, "\n" + "\n".join(hits)
    stale = sorted(set(HOOKS) - {(rel, func) for rel, _, func, _, _ in found})
    assert not stale, f"HOOKS names sites without a torch, numpy or triton import: {stale}"
    assert all(why.startswith("engine-process hook until ENG-413") for why in HOOKS.values())
    return f"{len(found)} imports at {len(HOOKS)} sites"


def test_dashboard_reads_stay_in_app():
    seen, hits = dashboard_reads()
    assert not hits, "\n" + "\n".join(hits)
    assert seen, "no dashboard read found: the patterns no longer match the sources"
    return f"{seen} reads in {len(dashboard_files())} files"


def test_every_module_imports_alone():
    n, bad = import_alone()
    assert n > 100 and not bad, (n, bad)
    return f"{n} modules"


PLANTED = {
    "x.py": ("import subprocess\nimport sys\n\n\ndef lazy():\n    from server import app\n"
             "    return app\n\n\ndef run():\n    subprocess.run(['python', '-m', 'server.app'])\n"
             "    subprocess.run([sys.executable, '-c', 'import engine.cache'])\n"),
    "ok.py": ("import subprocess\nimport sys\n\nHELP = 'python -m server.app runs the engine'\n\n\n"
              "def run():\n    subprocess.run([sys.executable, '-m', 'proxy', 'version'])\n"),
}


def test_the_checks_find_planted_hits():
    with tempfile.TemporaryDirectory() as d:
        app = os.path.join(d, "app")
        os.makedirs(app)
        for name, body in PLANTED.items():
            with open(os.path.join(app, name), "w") as f:
                f.write(body)
        got = import_hits(app) + exec_hits(app)
    want = ["app/x.py:6: from server import app", "app/x.py:11: subprocess.run(['python', '-m",
            "app/x.py:12: subprocess.run([sys.executable, '-c', 'import engine.cache'])"]
    assert len(got) == len(want) and all(g.startswith(w) and g.endswith(WHY)
                                         for g, w in zip(got, want)), got
    return f"{len(got)} planted hits named"


def test_the_dashboard_check_finds_planted_reads():
    with tempfile.TemporaryDirectory() as d:
        mock = os.path.join(d, "app", "dashboard", "mock")
        os.makedirs(mock)
        with open(os.path.join(mock, "x.ts"), "w") as f:
            f.write("import a from '../../tools/a';\nimport b from '../../../models/b';\n"
                    "const u = new URL('../../../server/c.json', import.meta.url);\n"
                    "const j = join(__dirname, '..', '..', '..', 'engine');\n"
                    "const here = (p: string) => new URL(p, import.meta.url);\n"
                    "const r = here('../../../docs');\n")
        seen, hits = dashboard_reads(os.path.join(d, "app"))
    assert seen >= 6 and [h.split(": ")[0] for h in hits] == \
        [f"app/dashboard/mock/x.ts:{n}" for n in (2, 3, 4, 6)], (seen, hits)
    return f"{len(hits)} of {seen} planted reads named"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
