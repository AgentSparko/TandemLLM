"""The no-app guard (ENG-413 WP9, on since WP14; QA ENG-414): the engine's tree carries no TandemApp
and its code needs none. TandemApp depends on the engine's contract; the engine never on TandemApp.

  1  no app/ in the tree, no app.pin, no vendor tool; server/__init__.py installs no import finder
     (a child that imports server has none on sys.meta_path)       test_no_app_no_finder
  2  no engine module imports tandemapp, proxy or chatd: no import statement, no importlib call or
     __import__ with the name, no code string that imports one (bench, contracts, engine, manual,
     models, ops, server, tools), but the pair tools PAIR_TOOLS names, which find TandemApp
     through tools/pair_app.py (TANDEMAPP_SRC or an installed wheel). The extension seam
     (server/ext.py) imports the module QSE_EXT names, by name only    test_no_engine_module_imports_tandemapp
  3  no engine file names a path into app/ (Python, shell, YAML, JSON, text): the only `app/` left
     are engine/settings.py's knob owner labels and engine/disk_cache.py's owner prefix, which feed
     the frozen disk-cache fingerprint (byte for byte), the contract's compat/ snapshots of what
     earlier minors said, and NOT_PATHS (a name that is no path of this tree)
                                                                   test_no_engine_file_names_app
  4  no engine test reaches TandemApp: a test file outside the pair suite (tests/test-ledger.json
     `pair_suite`) imports none of its packages, names no app/ path and imports no pairapp
                                                                   test_no_engine_test_reaches_tandemapp

CPU, standard library only. Run: python tests/test_app_hook.py
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
#: TandemApp's top-level packages
APP_TOPS = ("tandemapp", "proxy", "chatd")
#: the engine's code, outside tests/
CODE_DIRS = ("bench", "contracts", "engine", "manual", "models", "ops", "server", "tools")
SKIP_DIRS = ("__pycache__", "node_modules", ".git")
#: the pair tools: each measures or replays the engine and TandemApp's conductor together and finds
#: TandemApp through tools/pair_app.py (tests/test_api_import_guard.py says why each needs both)
PAIR_TOOLS = ("tools/conductor_eval.py", "tools/narrator_reference.py", "tools/shadow_measure.py",
              "tools/shadow_router_eval.py", "tools/stall_replay.py", "tools/thought_narrator_measure.py",
              "tools/voice_replay.py", "tools/shadow_ab.py", "tools/stream_identity.py")
#: the `app/` text the fingerprint keeps byte for byte (plan rule 2.2): labels, not paths
FROZEN = ("engine/settings.py", "engine/disk_cache.py")
#: `app/` text that is no path of this tree: a file name inside a sample session a tool replays
NOT_PATHS = {"tools/narrator_reference.py": "app/main.py"}
TEXT_EXT = (".py", ".sh", ".yaml", ".yml", ".json", ".env", ".txt", ".toml", ".cfg", ".ini", ".tex")
APP_PATH = re.compile(r"(?<![\w.$/-])app/(?=[\w.*-])")
#: an import statement inside a code string (a line or a `;` part of one)
_TOPS = "(?:" + "|".join(APP_TOPS) + ")"
IMPORT_TEXT = re.compile(r"(?m)(?:^|;)\s*(?:from\s+" + _TOPS + r"(?:\.\w+)*\s+import\b|import\s+" + _TOPS + r"\b)")


def _walk(top: str):
    for base, dirs, names in os.walk(os.path.join(ROOT, top)):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for n in sorted(names):
            yield os.path.relpath(os.path.join(base, n), ROOT).replace(os.sep, "/")


def _imports(path: str) -> list[str]:
    """TandemApp names one Python file imports: statements, importlib/__import__ literals, and
    code strings that hold an import statement."""
    with open(os.path.join(ROOT, path), encoding="utf-8", errors="replace") as f:
        try:
            tree = ast.parse(f.read())
        except SyntaxError:
            return []
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out += [a.name for a in n.names if a.name.split(".")[0] in APP_TOPS]
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module and \
                n.module.split(".")[0] in APP_TOPS:
            out.append(n.module)
        elif isinstance(n, ast.Call) and getattr(n.func, "attr", getattr(n.func, "id", None)) in \
                ("import_module", "__import__") and n.args and isinstance(n.args[0], ast.Constant) \
                and isinstance(n.args[0].value, str) and n.args[0].value.split(".")[0] in APP_TOPS:
            out.append(n.args[0].value)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            out += [f"code string: {m.strip(' ;')}" for m in IMPORT_TEXT.findall(n.value)]
    return out


def _app_paths(path: str) -> list[str]:
    """The app/ paths one file names: `app/...` in its text and, in Python, `"app"` followed by a
    path part in one call, tuple or list."""
    with open(os.path.join(ROOT, path), encoding="utf-8", errors="replace") as f:
        text = f.read()
    hits = [m.group(0) + text[m.end():m.end() + 24].split()[0] if text[m.end():m.end() + 1].strip() else "app/"
            for m in APP_PATH.finditer(text)]
    if path.endswith(".py"):
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return hits
        for n in ast.walk(tree):
            seqs = [n.args] if isinstance(n, ast.Call) else \
                [n.elts] if isinstance(n, (ast.Tuple, ast.List)) else []
            for s in seqs:
                for i, a in enumerate(s):
                    if isinstance(a, ast.Constant) and a.value == "app" and i + 1 < len(s) and \
                            isinstance(s[i + 1], ast.Constant) and isinstance(s[i + 1].value, str):
                        hits.append(f'"app", "{s[i + 1].value}"')
    return hits


def _pair() -> set:
    with open(os.path.join(HERE, "test-ledger.json"), encoding="utf-8") as f:
        return set(json.load(f)["pair_suite"]["files"])


def test_no_app_no_finder():
    for p in ("app", "app.pin", "tools/app_vendor.sh", "tools/app_split_check.sh"):
        assert not os.path.exists(os.path.join(ROOT, p)), f"{p} after ENG-413 WP14"
    with open(os.path.join(ROOT, "server", "__init__.py")) as f:
        src = f.read()
    assert "meta_path" not in src and "Finder" not in src, "server/__init__.py installs a finder"
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "TANDEMAPP_SRC")}
    code = ("import sys; sys.path.insert(0, sys.argv[1]); before = list(sys.meta_path); import server; "
            "print(len(sys.meta_path) - len(before)); "
            "print(sorted(m for m in sys.modules if m.split('.')[0] in ('tandemapp', 'proxy', 'chatd')))")
    with tempfile.TemporaryDirectory(prefix="no-app-") as cwd:
        p = subprocess.run([sys.executable, "-I", "-B", "-c", code, ROOT], capture_output=True, text=True,
                           timeout=120, env=env, cwd=cwd)
    assert p.returncode == 0, p.stderr[-2000:]
    assert p.stdout.split() == ["0", "[]"], p.stdout
    return "no app/, no pin, no vendor tool, no finder"


def test_no_engine_module_imports_tandemapp():
    bad, n = [], 0
    for top in CODE_DIRS:
        for rel in _walk(top):
            if not rel.endswith(".py"):
                continue
            n += 1
            got = _imports(rel)
            if got and rel not in PAIR_TOOLS:
                bad.append(f"{rel}: {sorted(set(got))}")
    assert not bad, f"engine modules import TandemApp: {bad}"
    for rel in PAIR_TOOLS:
        with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
            text = f.read()
        assert "pair_app" in text, f"{rel} finds TandemApp other than through tools/pair_app.py"
    with open(os.path.join(ROOT, "tools", "pair_app.py")) as f:
        finder = f.read()
    assert '"app"' not in finder and "VENDORED" not in finder, "pair_app.py still reads the vendored app/"
    with open(os.path.join(ROOT, "server", "ext.py")) as f:
        ext = f.read()
    assert "import_module" in ext and "QSE_EXT" in ext                  # the seam: by name only
    return f"{n} engine modules, none imports TandemApp but the {len(PAIR_TOOLS)} pair tools"


def test_no_engine_file_names_app():
    bad, n = [], 0
    files = [rel for top in CODE_DIRS for rel in _walk(top)] + \
        ["install.sh"] + [rel for rel in _walk(".github")]
    for rel in files:
        if not rel.endswith(TEXT_EXT) and rel != "install.sh":
            continue
        if rel in FROZEN or rel.startswith("contracts/engine-api-v1/compat/"):
            continue
        n += 1
        got = [g for g in _app_paths(rel) if not g.startswith(NOT_PATHS.get(rel, "\0"))]
        if got:
            bad.append(f"{rel}: {got[:3]}")
    assert not bad, f"engine files name app/: {bad}"
    # the frozen labels are only labels: the knob owners and the cache's owner prefix
    with open(os.path.join(ROOT, "engine", "disk_cache.py")) as f:
        assert '_NOT_27B_KNOBS = ("server/", "engine/models/", "tools/fn_", "app/")' in f.read()
    return f"{n} engine files, no app/ path (the fingerprint's labels and compat/ aside)"


def test_no_engine_test_reaches_tandemapp():
    pair = _pair()
    bad, n = [], 0
    for rel in _walk("tests"):
        if not rel.endswith(".py") or rel in pair or rel in ("tests/test_app_hook.py", "tests/pairapp.py") \
                or rel.startswith("tests/fixtures/"):
            continue
        n += 1
        got = _imports(rel) + _app_paths(rel)
        with open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace") as f:
            if re.search(r"(?m)^\s*import pairapp\b", f.read()):
                got.append("import pairapp")
        if got:
            bad.append(f"{rel}: {got[:3]}")
    assert not bad, f"engine tests reach TandemApp (or belong in the pair suite): {bad}"
    return f"{n} engine test files, none reaches TandemApp; {len(pair)} pair tests apart"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
