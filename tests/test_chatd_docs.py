"""The CHANGELOG's 0.11.2 entry for chatd's lifecycle and the Chat tab's down state (OPS-157, QA OPS-158;
GitHub issue #3).

  5  the CHANGELOG's 0.11.2 entry: the section, the upgrade line, the credit, no aiohttp, no manual
     chatd restart                                                           test_the_patch_entry

Checks 1 to 4 (the Chat tab's docs and manual chapter, chatd's paths) moved to TandemApp's
tests/test_chatd_docs.py with the pages they read (ENG-413 WP11); the released entry stays here.

Run: python tests/test_chatd_docs.py
"""

from __future__ import annotations

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*p: str) -> str:
    return open(os.path.join(ROOT, *p)).read()


def test_the_patch_entry():
    log = _read("CHANGELOG.md")
    head = re.search(r"\n## (0\.11\.2[^\n]*)\n(.*?)\n## ", log, re.S)
    assert head, "no 0.11.2 entry on top"
    entry = head.group(2)
    assert log.index("## 0.11.2") < log.index("## 0.11.1")
    assert "### Chat tab and install" in entry
    assert "Upgrade: run the installer again; chatd then starts with the engine" in entry
    assert "`tandem chat restart`" in entry and "Reported by Aron (issue #3)." in entry
    assert "aiohttp" not in entry.lower()
    assert "chatd.sh stop" not in entry


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  {name:50s} ok")
            n += 1
    print(f"{n} passed")
