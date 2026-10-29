"""#108: the standalone proxy (`tandem-app`, its image) ships `proxy` and `tandemapp` without the
engine, so the knobs they read carry the engine's defaults in app/tandemapp/knobs.py.

  1  app/tandemapp/knobs.py's defaults are engine/settings.py's for every knob the conductor and the
     proxy read, and every such read is one of them          test_the_defaults_are_the_registry

The checks without the engine run on TandemApp's side: app/tests/test_knobs.py (the knobs read the
environment with these defaults, an unknown name raises KeyError) and
app/tests/test_import_boundary.py (every module of proxy, chatd and tandemapp imports with the
engine blocked, and none imports it).

CPU. Run: python tests/test_knobs_standalone.py
"""

from __future__ import annotations

import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(1, os.path.join(ROOT, "app"))  # SRV-747: TandemApp's packages, until ENG-413

DIRS = ("app/proxy", "app/tandemapp/conductor", "app/tandemapp/conductor/monitor")


def _files():
    found = {d: glob.glob(os.path.join(ROOT, d, "*.py")) for d in DIRS}
    assert all(found.values()), f"no module under {[d for d, fs in found.items() if not fs]}"
    return sorted(p for fs in found.values() for p in fs)


def test_the_defaults_are_the_registry():
    from engine.settings import KNOBS
    from tandemapp import knobs
    assert knobs.DEFAULTS == {k: KNOBS[k][0] for k in knobs.DEFAULTS}, \
        {k: (v, KNOBS[k][0]) for k, v in knobs.DEFAULTS.items() if KNOBS[k][0] != v}
    read = set()
    for p in _files():
        read |= set(re.findall(r'_S\.get\("([A-Z0-9_]+)"', open(p, encoding="utf-8").read()))
    assert read and read <= set(knobs.DEFAULTS), sorted(read - set(knobs.DEFAULTS))
    return f"{len(knobs.DEFAULTS)} knobs, {len(read)} read"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
