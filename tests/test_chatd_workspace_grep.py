"""Grep tells the truth: a file path is searchable, and size is not a mute button.

Reproduced live on a 256K conversation: the model grepped `^#{1,2} ` at the manual's .md —
the tool answered "no matches" while the file's very first line is `## TandemLLM`.

Run: python tests/test_chatd_workspace_grep.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chatd.workspace import Workspace

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f" FAIL {name}")


def make_ws():
    d = tempfile.mkdtemp(prefix="grep-test-")
    os.makedirs(d + "/uploads", exist_ok=True)
    with open(d + "/uploads/notes.md", "w") as f:
        f.write("# Notes\nsome prose\n## Section A\n")
    # a file past the old MAX_READ cliff (2 MB): the needle sits at the end
    with open(d + "/uploads/big.txt", "w") as f:
        while os.path.getsize(d + "/uploads/big.txt") < 2_200_000:
            f.write("filler " + "x" * 60 + "\n")
        f.write("NEEDLE-PAST-CLIFF\n")
    return Workspace(d)


ws = make_ws()

h = ws.grep("^## ", "/workspace/uploads/notes.md")
check("a FILE path is searchable (was: PathError -> 'no matches')",
      len(h) == 1 and h[0].endswith("## Section A"))

h = ws.grep("^# ", "/workspace/uploads/notes.md")
check("anchor ^ works per-line on a single-file grep",
      len(h) == 1 and "Notes" in h[0])

h = ws.grep("NEEDLE-PAST-CLIFF", "/workspace")
check("a 2.2 MB file is scanned, not silently skipped", len(h) == 1)

h = ws.grep("NEEDLE-PAST-CLIFF", "/workspace/uploads/big.txt")
check("the big file answers to its own path", len(h) == 1)

h = ws.grep("Section", "/workspace")
check("directory walk still greps siblings too", len(h) == 1)

h = ws.grep("no-such-thing-xyz", "/workspace")
check("a genuine no-match still says so", h == [])

print(f"\n{PASS}/{PASS + FAIL} passed")
sys.exit(0 if FAIL == 0 else 1)
