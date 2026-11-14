"""The tools that came from the engine's tools/ (ENG-413 WP10, SRV-821/822): TandemApp's own now.

They read only TandemApp's code (the conductor, its notes, the ledger's journal, chatd's render, the
proxy's prices and fake backend), so they live here and run from this tree alone.

  1  each is in tools/, and in a child process with only this tree on the path and the engine's
     top names (server, engine, models, bench, manual) blocked it loads and answers --help with
     exit 0; conductor_contract (no --help) checks contracts/conductor-v1 against the code
                                                                  test_each_tool_runs_from_this_tree
  2  none names the engine's vendored app/ path or puts it on sys.path; the paths they default
     to (contracts/conductor-v1, proxy/prices.seed.json, tests/fake_backend.py) are this tree's
                                                                  test_no_tool_names_the_vendored_path

Standard library only. Run: python tests/test_moved_tools.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

#: the tool -> what it measures or checks (the engine's plan, section 3.7)
MOVED = {
    "class_core_eval": "the router's core classes on the engine, through the conductor's prompt",
    "class_fallback_eval": "the router's fallback rules over the class answers",
    "conductor_contract": "the conductor's config schemas and seeds in contracts/conductor-v1",
    "loop_words_fp": "the word-level reasoning-loop detector's false alarms",
    "memory_measure": "the memory notes' write and read cost on a test engine",
    "memory_recall_check": "memory recall on the chat path",
    "prefill_reuse_e2e": "a Chat-tab run's prefix reuse, rendered as chatd renders it",
    "parallel_load": "N agent sessions at once through the proxy",
}
BLOCK = ("server", "engine", "models", "bench", "manual")

CHILD = r"""
import importlib.abc, runpy, sys
ROOT, TOOL = sys.argv[1], sys.argv[2]
BLOCK = %r
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCK:
            raise ImportError("engine module: " + name)
        return None
sys.meta_path.insert(0, Block())
sys.argv = [TOOL] + sys.argv[3:]
runpy.run_path(TOOL, run_name="__main__")
""" % (BLOCK,)


def _run(tool: str, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "QSE_CONDUCTOR_CONFIG")}
    with tempfile.TemporaryDirectory(prefix="moved-tools-") as cwd:
        return subprocess.run([sys.executable, "-I", "-B", "-c", CHILD, ROOT,
                               os.path.join(ROOT, "tools", f"{tool}.py"), *args],
                              capture_output=True, text=True, timeout=120, env=env, cwd=cwd)


def test_each_tool_runs_from_this_tree():
    done = []
    for tool in MOVED:
        assert os.path.isfile(os.path.join(ROOT, "tools", f"{tool}.py")), f"tools/{tool}.py is missing"
        p = _run(tool) if tool == "conductor_contract" else _run(tool, "--help")
        assert p.returncode == 0, (tool, p.stdout[-1500:], p.stderr[-1500:])
        if tool != "conductor_contract":
            assert "usage:" in p.stdout, (tool, p.stdout[:500])
        done.append(tool)
    return f"{len(done)} tools ran here"


def test_no_tool_names_the_vendored_path():
    hits = []
    for tool in MOVED:
        with open(os.path.join(ROOT, "tools", f"{tool}.py"), encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                if re.search(r"""["']app["']|(?<![\w/.-])app/""", line):
                    hits.append(f"tools/{tool}.py:{n}: {line.strip()}")
    assert not hits, hits
    for rel in ("contracts/conductor-v1/seeds.json", "proxy/prices.seed.json", "tests/fake_backend.py"):
        assert os.path.isfile(os.path.join(ROOT, rel)), rel
    with open(os.path.join(ROOT, "tools", "parallel_load.py"), encoding="utf-8") as f:
        assert 'os.path.join(ROOT, "proxy", "prices.seed.json")' in f.read()
    return f"{len(MOVED)} tools name only this tree"


if __name__ == "__main__":
    sys.path.insert(0, HERE)
    from scriptmode import run
    run(globals())
