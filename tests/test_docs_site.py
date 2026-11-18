"""The documentation website (tools/docs_site.py, .github/workflows/docs.yml), no network.

  1  the staged site: every link that leaves docs/ points at GitHub, every page is in a section of
     docs/README.md, every navigation entry is a page, the mermaid fence is configured
                                                                   test_the_site_stages_clean
  2  the workflow deploys with mike from the pinned requirements, builds strict first, and puts the
     custom domain on gh-pages                                     test_the_workflow
  3  links leave docs/ at the ref asked: a release tag, not main   test_links_at_the_tag

Run: python tests/test_docs_site.py
"""

from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import docs_site  # noqa: E402


def test_the_site_stages_clean():
    bad = docs_site.check()
    assert not bad, bad
    return "staged clean"


def test_the_workflow():
    with open(os.path.join(ROOT, ".github", "workflows", "docs.yml"), encoding="utf-8") as fh:
        wf = fh.read()
    assert "pip install -r tools/docs-requirements.txt" in wf and "mkdocs build --strict" in wf
    assert wf.index("mkdocs build --strict") < wf.index("mike deploy")
    assert '--update-aliases "${REF#v}" latest' in wf and "mike set-default" in wf and "push dev" in wf
    assert "CNAME" in wf
    with open(os.path.join(ROOT, "tools", "docs-requirements.txt"), encoding="utf-8") as fh:
        reqs = fh.read().split()
    assert all("==" in r for r in reqs) and {r.split("==")[0] for r in reqs} == {"mkdocs", "mkdocs-material", "mike"}, reqs
    return f"{docs_site.DOMAIN}, pinned {', '.join(reqs)}"


def test_links_at_the_tag():
    with tempfile.TemporaryDirectory() as d:
        docs_site.stage(d, "v9.9.9")
        text = open(os.path.join(d, "docs", "README.md"), encoding="utf-8").read()
        assert f"github.com/{docs_site.REPO}/blob/v9.9.9/" in text, text[:400]
        assert "../" not in "".join(l for l in text.splitlines() if "](" in l and "../" in l and "http" not in l)
    return "outside links at v9.9.9"


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                print(f"  {name:<40} ok   {fn() or ''}")
            except AssertionError as e:
                fails += 1
                print(f"  {name:<40} FAIL {e}")
    sys.exit(1 if fails else 0)
