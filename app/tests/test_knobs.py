"""#108: the knobs the conductor and the proxy read (tandemapp/knobs.py), without the engine. Each
check runs in a child process (python -I, app/ the only path of the tree, server, engine and
torch blocked, no QWEN38_* variable but the one it sets).

  1  knobs.SETTINGS is a StandaloneSettings                 test_the_settings_are_standalone
  2  QWEN38_TOOL_FIT=off reads 'off'; NOTE_DEDUP, unset, reads its default 'parts'
                                                             test_the_environment_and_the_defaults
  3  a name that is not a knob raises KeyError               test_an_unknown_knob_raises

The engine's side, these defaults against its registry, is the repository's
tests/test_knobs_standalone.py; every module importing without the engine is
tests/test_import_boundary.py.

Standard library only. Run: python tests/test_knobs.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, HERE)

CHILD = r'''
import os, sys
APP = sys.argv[1]
class Block:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in ("server", "engine", "torch"):
            raise ModuleNotFoundError(f"blocked: {name}", name=name)
        return None
sys.meta_path.insert(0, Block())
sys.path = [APP] + [p for p in sys.path if p and not os.path.realpath(p).startswith(
    os.path.realpath(os.path.dirname(APP)) + os.sep)]
from tandemapp import knobs
print(type(knobs.SETTINGS).__name__)
print(knobs.SETTINGS.get("TOOL_FIT"), knobs.SETTINGS.get("NOTE_DEDUP"))
try:
    knobs.SETTINGS.get("NOT_A_KNOB")
except KeyError as e:
    print("KeyError", e)
else:
    print("no error")
print(sorted(k for k in sys.modules if k.split(".")[0] in ("server", "engine", "torch")))
'''


def _child() -> list[str]:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH" and not k.startswith("QWEN38_")}
    env["QWEN38_TOOL_FIT"] = "off"
    with tempfile.TemporaryDirectory(prefix="tandemapp-knobs-") as cwd:
        p = subprocess.run([sys.executable, "-I", "-c", CHILD, APP], capture_output=True,
                           text=True, timeout=120, env=env, cwd=cwd)
    lines = p.stdout.splitlines()
    assert p.returncode == 0 and len(lines) == 4 and lines[3] == "[]", \
        (p.returncode, p.stdout[-2000:], p.stderr[-2000:])
    return lines


def test_the_settings_are_standalone():
    assert _child()[0] == "StandaloneSettings"


def test_the_environment_and_the_defaults():
    assert _child()[1] == "off parts"


def test_an_unknown_knob_raises():
    line = _child()[2]
    assert line.startswith("KeyError") and "QWEN38_NOT_A_KNOB" in line, line


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
