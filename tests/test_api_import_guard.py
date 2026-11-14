"""G5 for ENG-413 WP2a, WP2b and WP2c (QA ENG-432): the engine's wire format, its observability and
the small helpers its setup, probe and disk cache read are the engine's own code.

server/api holds the engine's copies of the modules it took from TandemApp: toolcall, toolformats,
callrepair, refusal, thinking, argloop (WP2a); metrics, usage, ledger, live, parallel, activity,
logbuf, dashboard_api, http_body, auth and compaction (WP2b; compaction because it runs with the
conductor off, tests/test_compaction_trace.py). WP2c copied names out of TandemApp's modules: the
JSON-schema validator (validate), the residency and roles seeds (seeds), the secret refusal
(redact), the BM25 index (textindex), the repository check (repository) and chatd's address
(chat_url). Only TandemApp's own tree (app/, the extension side) imports TandemApp's copies; the
engine does not.

  1  no engine file outside app/ imports one of those modules from TandemApp, by import statement,
     by importlib name or in a code string; no engine script or config names one, except the
     files ALLOWED says why                                    test_no_engine_file_imports_them
  2  server/api imports only the standard library and server/api itself; torch (metrics' GPU
     gauges) and numpy (dashboard_api's order statistics) only inside a function or a try, as
     OPTIONAL says                                                test_api_stands_alone
  3  the importers the plan names (server/app.py, server/compat.py, server/endalign.py,
     server/setup/wren.py, tools/argloop_fp.py; WP2b: server/setup_api.py, server/probe.py) take
     each module from server/api                                 test_the_importers_use_the_engine_copy
  4  in a fresh process every server/api module, server/compat.py and server/endalign.py load no
     TandemApp module; compat's Refusal is server/api's; the forms register; the default effort
     caps are the seed's; the registry holds the engine's families
                                                                  test_a_process_loads_no_app_module
  5  thinking.LEVELS (the effort caps without a conductor) is the conductor's seed, thinking and
     budget per level (until the extension hands its levels in, ENG-413 WP3)
                                                                  test_the_seed_levels_are_the_conductors
  6  each WP2b copy is TandemApp's module as forked: the same code once the imports are named
     alike, docstrings aside; the ledger's copy adds only the side-shape helpers it took from the
     conductor's signals, compaction's only its check and the shapes it reads, metrics' GPU gauges
     read the allocator's nested dict without the engine's hook (FORK_CHANGED). A change to either
     copy fails here until it lands in both
                                                                  test_the_copies_are_the_fork
  7  WP2c: no engine file imports a copied name from TandemApp (`from M import name`); no engine
     code outside tests/ reads one through TandemApp's module (`M.name`, the conductor's
     `schema.seed("residency"|"roles")`); the importers the plan names take each from server/api
                                                                  test_the_helpers_are_the_engines
  8  each WP2c copy holds exactly the names it took, each statement TandemApp's (docstrings
     aside), plus what WP2C_OWN names (seeds.seed: the kinds of schema.seed; WP5: DECIDERS)
                                                                  test_the_helper_copies_are_the_fork

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

#: TandemApp's module -> the engine's copy (ENG-413 WP2a, then WP2b)
WP2A = ("toolcall", "toolformats", "callrepair", "refusal", "thinking", "argloop")
WP2B = ("metrics", "usage", "ledger", "live", "parallel", "activity", "logbuf", "dashboard_api",
        "http_body", "auth", "compaction")
MOVED = {f"tandemapp.{m}": f"server.api.{m}" for m in WP2A + WP2B}
#: the engine's tree (app/ is TandemApp's)
ENGINE_DIRS = ("bench", "engine", "manual", "models", "ops", "server", "tests", "tools")
SKIP_DIRS = ("__pycache__", "node_modules")
TEXT_SUFFIXES = (".sh", ".yaml", ".yml", ".env", ".toml", ".cfg", ".txt")
#: files that may name a moved module of TandemApp's in text, and why
ALLOWED = {
    "tests/test_api_import_guard.py": "this guard names what it looks for",
    "tools/gate/map.yaml": "a vendored change to TandemApp's copies still takes the server's gate: "
                           "its conductor runs them in the engine's process in chat mode",
    "tests/test_ledger_owners.py": "holds the engine's ledger against TandemApp's: one file, the "
                                   "same schema, each table written by the same side",
    "tools/ledger_synth.py": "TandemApp's tool (plan 3.7: it moves to TandemApp, WP10); it reads "
                             "TandemApp's ledger, the one its controller writes",
    "tools/ledger_migrate_check.py": "TandemApp's tool (plan 3.7: it moves to TandemApp, WP10)",
    "tools/memory_measure.py": "TandemApp's tool (plan 3.7: it moves to TandemApp, WP10); it measures "
                               "the conductor's notes",
    "tools/app_split_check.sh": "follows the history of TandemApp's own tandemapp/ledger.py in the "
                                "split; it reads no module",
}
NAMES = re.compile(r"\btandemapp[./](" + "|".join(WP2A + WP2B) + r")\b")
#: the files the plan switches (section 3.1, WP2a), with the modules each must take from server/api
IMPORTERS = {"server/app.py": {"toolformats", "callrepair", "toolcall", "thinking", "argloop"},
             "server/compat.py": {"refusal", "toolformats"},
             "server/endalign.py": {"toolcall"},
             "server/setup/wren.py": {"toolcall"},
             "tools/argloop_fp.py": {"argloop"},
             "server/setup_api.py": {"dashboard_api"},
             "server/probe.py": {"ledger"}}
IMPORTERS["server/app.py"] |= set(WP2B)
#: ENG-413 WP2c: engine module -> (TandemApp's module, the names copied out of it, in its order)
WP2C = {"validate": ("tandemapp.conductor.schema", ("ID_RE", "_TYPES", "validate")),
        "seeds": ("tandemapp.conductor.schema", ("TOKENIZER_NONE", "_role", "ROLES", "RESIDENCY")),
        "redact": ("tandemapp.conductor.redact", ("PATTERNS", "KINDS", "redact", "merge")),
        "textindex": ("tandemapp.conductor.libsearch", ("K1", "B", "_WORD", "STOP", "words", "TextIndex")),
        "repository": ("tandemapp.conductor.transcripts", ("in_repository",)),
        "chat_url": ("tandemapp.chat_forward", ("target",))}
#: tests that read TandemApp's copied names on purpose, and why
HELD = {"tests/test_api_helpers.py": "holds each WP2c copy's answers to TandemApp's"}
#: what a WP2c copy has of its own, and why
#: (ENG-413 WP5: DECIDERS, schema.DECIDERS less its api, the extension's part; tests/test_api_helpers.py)
WP2C_OWN = {"seeds": {"seed", "DECIDERS"}}       # schema.seed for "residency" and "roles", a fresh copy each
#: the conductor's seed kinds the engine reads from server/api/seeds.py (the others stay its own)
SEED_KINDS = ("residency", "roles", "deciders")
#: the files WP2c switches (plan section 3.1, WP2c), with the server/api modules each takes
IMPORTERS_2C = {"server/diskcache.py": {"repository"}, "server/probe.py": {"repository"},
                "server/mode.py": {"chat_url"}, "server/decider_api.py": {"seeds"},
                # ENG-413 WP5: the role memory and Wren's planner read the documents through
                # server/conductor/residency.py `documents`, which takes the seeds
                "server/conductor/residency.py": {"seeds"},
                "server/setup/registry.py": {"validate"}, "server/setup/community.py": {"validate"},
                "server/setup/knowledge.py": {"textindex"}, "server/setup/apply.py": {"redact"},
                "tests/test_parallel_card.py": {"validate"}}
#: third-party imports server/api may make, only inside a function or a try (an optional speedup or
#: a gauge read where the engine runs)
OPTIONAL = {"server/api/metrics.py": {"torch"}, "server/api/dashboard_api.py": {"numpy"}}


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
        "__future__", "ast", "bisect", "collections", "contextlib", "copy", "dataclasses", "datetime",
        "hashlib", "hmac", "importlib", "json", "math", "os", "queue", "re", "secrets", "select",
        "shlex", "shutil", "socket", "sqlite3", "subprocess", "sys", "threading", "time",
        "traceback", "urllib", "uuid", "zlib", "zoneinfo"}
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
            guarded = set()          # imports inside a function or a try: optional ones may be there
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Try)):
                    guarded |= {id(c) for c in ast.walk(node) if c is not node}
            optional = OPTIONAL.get(rel.replace(os.sep, "/"), set())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level:
                    bad.append(f"{rel}:{node.lineno} relative import")
                    continue
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                    [node.module] if isinstance(node, ast.ImportFrom) else []
                for nm in names:
                    if nm.split(".")[0] in optional and id(node) in guarded:
                        continue
                    if nm.split(".")[0] not in std and not (nm == "server.api" or
                                                             nm.startswith("server.api.")):
                        bad.append(f"{rel}:{node.lineno} imports {nm}")
    assert n >= 19, n
    assert not bad, bad
    return f"{n} files, standard library and server.api only ({len(OPTIONAL)} optional imports)"


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
from server.api import (activity, auth, compaction, dashboard_api, http_body, ledger, live, logbuf,
                        metrics, parallel, usage)
from server import compat, endalign
assert compat.Refusal is refusal.Refusal
toolformats.get(None)
print("forms", ",".join(sorted(toolformats.FORMATS)))
print("caps", sorted(thinking.caps().items()))
a = '<tool_call>\\n<function=f>\\n<parameter=x>\\n1\\n</parameter>\\n</function>\\n</tool_call>'
print("same", endalign.same_calls(a, a))
print("families", len(metrics.REGISTRY._metrics))
print("hermes", ledger.client_of({"User-Agent": "OpenAI/Python 2.24.0"}, {"messages": [
    {"role": "system", "content": ledger.SIDE_SHAPES[2]["match"]}]})[1])
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
    with open(os.path.join(ROOT, "contracts", "engine-api-v1", "metrics-families.json")) as f:
        assert int(out["families"]) == len(__import__("json").load(f)["families"]), out
    assert out["hermes"] == "hermes", out
    return "server.api, compat and endalign load no TandemApp module; 3 forms; seed caps; " \
           f"{out['families']} families; the Hermes title call read without the conductor"


def test_the_seed_levels_are_the_conductors():
    from server.api import thinking
    from tandemapp.conductor import schema      # the extension's seed, until it hands levels in
    theirs = {k: {"thinking": v.get("thinking"), "budget": v.get("budget")}
              for k, v in schema.ROUTER["effort"]["levels"].items()}
    assert thinking.LEVELS == theirs, (thinking.LEVELS, theirs)
    assert thinking.caps() == thinking.caps(schema.ROUTER["effort"]["levels"])
    return f"{len(theirs)} levels"


#: what the WP2b fork changed in each copy beyond the import lines (ENG-413 WP2b): the ledger took
#: the conductor's side-shape helpers in, compaction the check and its shapes, metrics a GPU gauge
#: reading; nothing else changed but docstrings, comments and FORK_CHANGED
FORK_ADDED = {"ledger": {"SIDE_SHAPES", "_text", "side_shape"},
              "compaction": {"COMPACTION_MIN_CHARS", "COMPACTION_SHAPES", "BARE_COMPACTION_CLIENTS",
                             "_is_tool_response", "compaction_shape", "is_compaction"},
              # the GPU gauges' reading without the engine's hook: the nested dict, never torch's
              # flattening readers, which server/ may not call (tests/test_alloc_stats_cycles.py)
              "metrics": {"_allocator",
                          # ENG-413 WP3a: the extension host's counter, an engine family
                          "ext_errors_total"}}
#: functions the fork changed on purpose, each in both copies: (module, name) -> why
FORK_CHANGED = {("metrics", "_gpu_memory"): "reads _allocator without the hook",
                ("metrics", "_gpu_reserved"): "reads _allocator without the hook",
                # ENG-413 WP5: own_work reads the memory writer's numbers through the extension
                # (engine-ext-v1 3.0 memory_writer), not the conductor's object in the state
                ("metrics", "install"): "own_work asks the extension's memory_writer"}


def _fork_view(path: str, mod: str, engine: bool) -> list[str]:
    """The module's top-level statements as code, docstrings dropped, imports of a moved module
    named as server/api's, `signals.` dropped (the ledger's helpers are its own in the engine) and
    the helpers the engine's copy added left out."""
    with open(path) as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) and \
                isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            body[0] = ast.Pass()
    out = []
    for node in tree.body:
        if engine and getattr(node, "name", None) in FORK_ADDED.get(mod, ()):
            continue
        if (mod, getattr(node, "name", None)) in FORK_CHANGED:
            out.append(f"def {node.name}: changed by the fork")
            continue
        if engine and isinstance(node, ast.Assign) and any(
                getattr(t, "id", None) in FORK_ADDED.get(mod, ()) for t in node.targets):
            continue
        if not engine and isinstance(node, ast.ImportFrom) and node.module == "tandemapp.conductor":
            continue                      # the ledger's `from tandemapp.conductor import signals`
        code = ast.unparse(node)
        code = re.sub(r"\btandemapp(\.| import )(" + "|".join(WP2B) + r")\b",
                      lambda m: ("server.api" + m.group(1) + m.group(2)), code)
        code = code.replace("signals.", "")
        out.append(code)
    return out


def test_the_copies_are_the_fork():
    diffs = []
    for m in WP2B:
        theirs = _fork_view(os.path.join(ROOT, "app", "tandemapp", m + ".py"), m, False)
        ours = _fork_view(os.path.join(ROOT, "server", "api", m + ".py"), m, True)
        if theirs != ours:
            import difflib
            d = list(difflib.unified_diff(theirs, ours, "app/tandemapp/" + m, "server/api/" + m,
                                          lineterm="", n=0))
            diffs.append("\n".join(d[:12]))
    assert not diffs, "the copies parted; land the change in both or record the fork here:\n" + \
        "\n".join(diffs)
    return f"{len(WP2B)} copies are TandemApp's modules as forked"


def _copied() -> dict:
    """TandemApp's module -> the names copied out of it."""
    out: dict = {}
    for mod, names in WP2C.values():
        out.setdefault(mod, set()).update(names)
    return out


def _bound_modules(tree: ast.AST) -> dict:
    """local name -> TandemApp module, for every `import M as x`, `from P import leaf` of a WP2c
    source module in `tree`."""
    srcs = set(_copied())
    out = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name in srcs:
                    out[a.asname or a.name] = a.name
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            for a in n.names:
                if f"{n.module}.{a.name}" in srcs:
                    out[a.asname or a.name] = f"{n.module}.{a.name}"
    return out


def test_the_helpers_are_the_engines():
    copied, bad = _copied(), []
    py = _files((".py",))
    for rel in py:
        if rel in ALLOWED or rel in HELD:
            continue
        with open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace") as f:
            try:
                tree = ast.parse(f.read())
            except SyntaxError:
                continue
        for line, name in imports_of(tree):
            mod, _, leaf = name.rpartition(".")
            if leaf in copied.get(mod, ()):
                bad.append(f"{rel}:{line} imports {name}")
        if rel.startswith("tests/"):
            continue                      # TandemApp's own tests read its seeds and validator
        bound = _bound_modules(tree)
        for n in ast.walk(tree):
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in bound:
                mod = bound[n.value.id]
                if n.attr in copied[mod] and n.attr != "_role":
                    bad.append(f"{rel}:{n.lineno} reads {mod}.{n.attr}")
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "seed" and \
                    isinstance(n.func.value, ast.Name) and bound.get(n.func.value.id) == "tandemapp.conductor.schema" \
                    and n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value in SEED_KINDS:
                bad.append(f"{rel}:{n.lineno} reads the conductor's {n.args[0].value} seed")
    for rel, want in IMPORTERS_2C.items():
        with open(os.path.join(ROOT, rel)) as f:
            got = imports_of(ast.parse(f.read()))
        took = {name.split(".")[2] for _, name in got
                if name.startswith("server.api.") and name.split(".")[2] in want}
        if took != want:
            bad.append(f"{rel}: takes {sorted(took)} from server/api, wants {sorted(want)}")
    assert not bad, bad
    return f"{len(py)} Python files: no copied name taken from TandemApp; {len(IMPORTERS_2C)} importers"


def _named(tree: ast.Module) -> dict:
    """name -> the code of the top-level statement that binds it, docstrings dropped."""
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) and \
                isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            body[0] = ast.Pass()
    out = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            out[node.name] = ast.unparse(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                for x in ast.walk(t):
                    if isinstance(x, ast.Name):
                        out[x.id] = ast.unparse(node)
    return out


def test_the_helper_copies_are_the_fork():
    bad = []
    for eng, (mod, names) in WP2C.items():
        src = os.path.join(ROOT, "app", *mod.split(".")) + ".py"
        with open(src) as f:
            theirs = _named(ast.parse(f.read()))
        with open(os.path.join(ROOT, "server", "api", eng + ".py")) as f:
            tree = ast.parse(f.read())
        ours = _named(tree)
        extra = set(ours) - set(names) - WP2C_OWN.get(eng, set())
        if extra:
            bad.append(f"server/api/{eng}.py: names TandemApp's module does not give it: {sorted(extra)}")
        for n in names:
            if ours.get(n) != theirs.get(n):
                bad.append(f"server/api/{eng}.py: {n} is not {mod}'s")
        loose = [ast.unparse(x)[:60] for x in tree.body[1:] if not isinstance(
            x, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef, ast.Assign, ast.AnnAssign))]
        if loose:
            bad.append(f"server/api/{eng}.py: statements beside the copied names: {loose}")
    assert not bad, "the copies parted; land the change in both or record the fork here:\n" + "\n".join(bad)
    return f"{len(WP2C)} copies, {sum(len(n) for _, n in WP2C.values())} names, each TandemApp's"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
