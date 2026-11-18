"""The docs are the whole documentation (the LaTeX manual was folded into them), no GPU.

  1  `tools/gen_reference.py --check` passes: every generated block under docs/ (the reference
     pages, the verify price tables of docs/speculative-decoding.md) is what the code makes now,
     every block has a page, and the HTTP reference names every route the engine's handlers
     test, every header the code reads or writes, and explains or lists every error type the
     code answers                                      test_the_reference_is_current
  2  every generated table is well formed: a caption heading, a header row, the separator, and each row
     with the header's number of cells (an unescaped | in a cell would add one)
                                                                            test_every_table_is_well_formed
  3  the ledger block ends with the journal's paragraph                     test_the_ledger_names_the_journal
  4  no page holds LaTeX or points at the manual                           test_no_latex_and_no_manual
  5  every relative link of every page reaches a file, and a #fragment a heading of that page
     (GitHub's anchors)                                                     test_every_link_resolves
  6  docs/README.md lists every page under docs/                            test_the_index_lists_every_page

Run: python tests/test_docs_reference.py
"""

from __future__ import annotations

import glob
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")
sys.path.insert(0, os.path.join(ROOT, "tools"))

BLOCK = re.compile(r"<!-- generated: ([a-z0-9-]+) by tools/gen_reference\.py; do not edit by hand -->\n(.*?)<!-- /generated -->", re.S)


def _pages() -> list:
    return sorted(glob.glob(os.path.join(DOCS, "**", "*.md"), recursive=True))


def _read(p: str) -> str:
    with open(p, encoding="utf-8") as fh:
        return fh.read()


def test_the_reference_is_current():
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "gen_reference.py"), "--check"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, "run python3 tools/gen_reference.py:\n" + r.stdout + r.stderr
    return "fresh, and the HTTP reference complete"


def _cells(row: str) -> int:
    body = row.strip()[1:-1]
    return len(re.split(r"(?<!\\)\|", body))


def test_every_table_is_well_formed():
    tables = 0
    for p in _pages():
        for name, body in BLOCK.findall(_read(p)):
            lines = body.strip("\n").split("\n")
            i = 0
            while i < len(lines):
                if lines[i].startswith("### ") and i + 3 < len(lines) and lines[i + 2].startswith("| "):
                    head, sep = lines[i + 2], lines[i + 3]
                    n = _cells(head)
                    assert sep == "|" + "-|" * n, (name, sep)
                    j = i + 4
                    while j < len(lines) and lines[j].startswith("| "):
                        assert _cells(lines[j]) == n, (name, lines[j][:120])
                        j += 1
                    assert j > i + 4, (name, "a table with no row")
                    tables += 1
                    i = j
                else:
                    i += 1
    assert tables >= 15, tables
    return f"{tables} tables"


def test_the_ledger_names_the_journal():
    import gen_reference
    text = gen_reference.gen_ledger(ROOT)
    assert text.rstrip("\n").splitlines()[-1].startswith("The conductor's journal shares the file: `conductor_events` has ")
    return "the journal's paragraph closes the block"


def test_no_latex_and_no_manual():
    bad = []
    latex = re.compile(r"\\(?:texttt|code|ref|label|emph|enquote|section|begin|end)\{|\[\[(?:REF|\?|MACRO)")
    for p in _pages() + [os.path.join(ROOT, "README.md")]:
        text = _read(p)
        for n, line in enumerate(text.splitlines(), 1):
            if latex.search(line) or re.search(r"\bmanual/|tandem(?:llm|app)-manual", line):
                bad.append(f"{os.path.relpath(p, ROOT)}:{n}: {line[:100]}")
    assert not os.path.exists(os.path.join(ROOT, "manual")), "manual/ is back"
    assert not bad, bad
    return f"{len(_pages())} pages"


def _anchors(text: str) -> set:
    """GitHub's heading anchors: lower case, punctuation but - and _ dropped, spaces to -."""
    out, seen = set(), {}
    in_code = False
    for line in text.splitlines():
        if line.startswith("```"):
            in_code = not in_code
            continue
        m = None if in_code else re.match(r"^#{1,6} (.*)$", line)
        if not m:
            continue
        a = re.sub(r"[^\w\- ]", "", m.group(1).strip().lower()).replace(" ", "-")
        k = seen.get(a, 0)
        seen[a] = k + 1
        out.add(a if k == 0 else f"{a}-{k}")
    return out


def test_every_link_resolves():
    bad, n = [], 0
    link = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)\)")
    for p in _pages():
        text = _read(p)
        stripped = re.sub(r"```.*?```", "", text, flags=re.S)
        for target in link.findall(stripped):
            if re.match(r"[a-z]+:", target) or target.startswith("<"):
                continue
            n += 1
            path, _, frag = target.partition("#")
            dest = os.path.normpath(os.path.join(os.path.dirname(p), path)) if path else p
            if not os.path.exists(dest):
                bad.append(f"{os.path.relpath(p, ROOT)}: {target} (no file)")
                continue
            if frag and dest.endswith(".md") and frag not in _anchors(_read(dest)):
                bad.append(f"{os.path.relpath(p, ROOT)}: {target} (no heading)")
    assert not bad, "\n".join(bad)
    return f"{n} links"


def test_the_index_lists_every_page():
    index = _read(os.path.join(DOCS, "README.md"))
    missing = [os.path.relpath(p, DOCS) for p in _pages()
               if os.path.basename(p) != "README.md" and os.path.relpath(p, DOCS) not in index]
    assert not missing, missing
    return "every page"


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                print(f"  {name:<44} ok   {fn() or ''}")
            except AssertionError as e:
                fails += 1
                print(f"  {name:<44} FAIL {e}")
    sys.exit(1 if fails else 0)
