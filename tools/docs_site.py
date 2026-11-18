#!/usr/bin/env python3
"""The documentation website: docs/ as a MkDocs (Material) site, one per repository, on GitHub
Pages behind the custom domain in DOMAIN, versioned with mike (/latest/, /X.Y.Z/, /dev/ from main).

    python3 tools/docs_site.py stage OUT [--ref REF]    docs/ into OUT/docs with mkdocs.yml beside it
    python3 tools/docs_site.py check                    stage into a temporary folder and check it
                                                        (no MkDocs needed; tests/test_docs_site.py)

Then `mkdocs build --strict -f OUT/mkdocs.yml` builds the site, and .github/workflows/docs.yml
deploys it with mike. The page order is docs/README.md's: each `## Section` with its table of
pages is a group of the navigation, README.md is the home page. A link that leaves docs/
(`../CHANGELOG.md`, `../contracts/...`) becomes a link to the file on GitHub at REF (the release
tag, or main). Standard library only.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DOCS = os.path.join(ROOT, "docs")

REPO = "TandemLLM/TandemEngine"
NAME = "TandemEngine"
DOMAIN = "tandemengine.0xbakeer.com"
TAGLINE = "An inference engine for one GPU box: fast, exact, measured."

LINK = re.compile(r"(\]\()([^)\s]+)(\))")
ROW = re.compile(r"^\|\s*\[([^\]]+)\]\(([^)]+)\)\s*\|")


def _version() -> str:
    try:
        with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return "dev"


def _outside(page_rel: str, target: str, ref: str) -> str | None:
    """A link of docs/<page_rel> to a file outside docs/: its GitHub URL at `ref`, else None."""
    if re.match(r"[a-z]+:", target) or target.startswith(("#", "/", "<")):
        return None
    path, _, frag = target.partition("#")
    full = os.path.normpath(os.path.join(os.path.dirname(os.path.join("docs", page_rel)), path))
    if full == "docs" or full.startswith("docs" + os.sep):
        return None
    kind = "tree" if os.path.isdir(os.path.join(ROOT, full)) else "blob"
    return f"https://github.com/{REPO}/{kind}/{ref}/{full.replace(os.sep, '/')}" + (f"#{frag}" if frag else "")


def rewrite(page_rel: str, text: str, ref: str) -> str:
    out, code = [], False
    for line in text.split("\n"):
        if line.startswith("```"):
            code = not code
        if not code:
            line = LINK.sub(lambda m: m.group(1) + (_outside(page_rel, m.group(2), ref) or m.group(2)) + m.group(3), line)
        out.append(line)
    return "\n".join(out)


def _title(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("# "):
                return line[2:].strip()
    return os.path.basename(path)[:-3]


def nav(ref: str) -> list:
    """docs/README.md's sections as MkDocs navigation."""
    with open(os.path.join(DOCS, "README.md"), encoding="utf-8") as fh:
        lines = fh.read().split("\n")
    out: list = [{"Home": "README.md"}]
    group, items = None, []
    for line in lines:
        if line.startswith("## "):
            if group and items:
                out.append({group: items})
            group, items = line[3:].strip(), []
            continue
        m = ROW.match(line)
        if m and group:
            target = m.group(2)
            ext = _outside("README.md", target, ref)
            if ext:
                items.append({m.group(1).removesuffix(".md"): ext})
            else:
                items.append({_title(os.path.join(DOCS, target)): target})
    if group and items:
        out.append({group: items})
    return out


def _yaml(v, indent: int = 0) -> str:
    """Enough YAML for mkdocs.yml: dicts, lists, strings, numbers and booleans."""
    pad = "  " * indent
    if isinstance(v, dict):
        lines = []
        for k, x in v.items():
            key = k if re.fullmatch(r"[A-Za-z_][\w.-]*", k) else _scalar(k)
            if isinstance(x, (dict, list)) and x:
                lines.append(f"{pad}{key}:\n{_yaml(x, indent + 1)}")
            else:
                lines.append(f"{pad}{key}: {_scalar(x)}")
        return "\n".join(lines)
    if isinstance(v, list):
        lines = []
        for x in v:
            if isinstance(x, (dict, list)):
                body = _yaml(x, indent + 1).lstrip()
                lines.append(f"{pad}- {body}")
            else:
                lines.append(f"{pad}- {_scalar(x)}")
        return "\n".join(lines)
    return pad + _scalar(v)


def _scalar(x) -> str:
    if isinstance(x, bool):
        return "true" if x else "false"
    if isinstance(x, (int, float)):
        return str(x)
    if x is None or x == [] or x == {}:
        return "[]" if x == [] else "{}" if x == {} else "null"
    s = str(x)
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def config(ref: str) -> str:
    cfg = {
        "site_name": NAME,
        "site_description": TAGLINE,
        "site_url": f"https://{DOMAIN}/",
        "repo_url": f"https://github.com/{REPO}",
        "repo_name": REPO,
        "edit_uri": "blob/main/docs/",
        "docs_dir": "docs",
        "use_directory_urls": True,
        "copyright": "The documentation is CC BY 4.0. The code has its own license.",
        "theme": {
            "name": "material",
            "features": ["navigation.sections", "navigation.top", "navigation.footer", "search.highlight",
                         "content.code.copy", "toc.follow"],
            "palette": [
                {"media": "(prefers-color-scheme: dark)", "scheme": "slate", "primary": "custom", "accent": "custom",
                 "toggle": {"icon": "material/weather-sunny", "name": "Light"}},
                {"media": "(prefers-color-scheme: light)", "scheme": "default", "primary": "custom", "accent": "custom",
                 "toggle": {"icon": "material/weather-night", "name": "Dark"}},
            ],
        },
        "extra_css": ["stylesheets/extra.css"],
        "extra": {"version": {"provider": "mike", "default": "latest"}},
        "markdown_extensions": [
            "tables", "admonition", "attr_list", "md_in_html", "footnotes",
            {"toc": {"permalink": True}},
            {"pymdownx.highlight": {"anchor_linenums": False}},
            "pymdownx.inlinehilite",
            {"pymdownx.superfences": {"custom_fences": [
                {"name": "mermaid", "class": "mermaid", "format": "!!python/name:pymdownx.superfences.fence_code_format"}]}},
        ],
        "plugins": ["search"],
        "validation": {"nav": {"omitted_files": "info", "not_found": "warn"}, "links": {"not_found": "warn", "anchors": "info"}},
        "nav": nav(ref),
    }
    text = _yaml(cfg) + "\n"
    # the one YAML tag MkDocs needs as written: a Python name, unquoted
    return text.replace('"!!python/name:pymdownx.superfences.fence_code_format"', "!!python/name:pymdownx.superfences.fence_code_format")


EXTRA_CSS = """/* the house colours: cobalt and signal orange (the product site's) */
:root {
  --md-primary-fg-color: #2a64c9;
  --md-primary-fg-color--light: #3987e5;
  --md-primary-fg-color--dark: #1d4a99;
  --md-accent-fg-color: #f2994a;
}
[data-md-color-scheme="slate"] {
  --md-default-bg-color: #0b0f14;
  --md-primary-fg-color: #0f1621;
  --md-typeset-a-color: #6aa6f0;
}
.md-typeset table:not([class]) {
  font-size: 0.72rem;
}
.md-typeset table:not([class]) code {
  word-break: break-word;
}
"""


def stage(out: str, ref: str) -> list[str]:
    """docs/ into out/docs with mkdocs.yml; the pages staged."""
    dst = os.path.join(out, "docs")
    if os.path.exists(dst):
        shutil.rmtree(dst)
    pages = []
    for dp, dn, fn in os.walk(DOCS):
        dn.sort()
        rel = os.path.relpath(dp, DOCS)
        os.makedirs(os.path.join(dst, rel), exist_ok=True)
        for f in sorted(fn):
            src = os.path.join(dp, f)
            r = os.path.normpath(os.path.join(rel, f))
            if f.endswith(".md"):
                with open(src, encoding="utf-8") as fh:
                    text = fh.read()
                with open(os.path.join(dst, r), "w", encoding="utf-8") as fh:
                    fh.write(rewrite(r, text, ref))
                pages.append(r)
            else:
                shutil.copy2(src, os.path.join(dst, r))
    os.makedirs(os.path.join(dst, "stylesheets"), exist_ok=True)
    with open(os.path.join(dst, "stylesheets", "extra.css"), "w", encoding="utf-8") as fh:
        fh.write(EXTRA_CSS)
    with open(os.path.join(out, "mkdocs.yml"), "w", encoding="utf-8") as fh:
        fh.write(config(ref))
    return pages


def check() -> list[str]:
    """What would break the site: a link that leaves docs/ unrewritten, a page missing from the
    navigation, a navigation entry with no page."""
    bad = []
    with tempfile.TemporaryDirectory() as d:
        pages = stage(d, "main")
        listed = set()

        def walk(n):
            for item in n:
                for _k, v in item.items():
                    if isinstance(v, list):
                        walk(v)
                    elif not v.startswith("https://"):
                        listed.add(v)
                        if not os.path.exists(os.path.join(d, "docs", v)):
                            bad.append(f"nav names {v}, which is no page")
        walk(nav("main"))
        for p in pages:
            if p not in listed and not p.startswith(("gallery" + os.sep,)) and p != "README.md":
                bad.append(f"{p} is in no section of docs/README.md")
            with open(os.path.join(d, "docs", p), encoding="utf-8") as fh:
                text = fh.read()
            for m in LINK.finditer(re.sub(r"```.*?```", "", text, flags=re.S)):
                t = m.group(2).partition("#")[0]
                if not t or re.match(r"[a-z]+:", t):
                    continue
                full = os.path.normpath(os.path.join(os.path.dirname(os.path.join("docs", p)), t))
                if not (full == "docs" or full.startswith("docs" + os.sep)):
                    bad.append(f"{p}: {m.group(2)} still leaves docs/")
        cfg = open(os.path.join(d, "mkdocs.yml"), encoding="utf-8").read()
        if "!!python/name:pymdownx.superfences.fence_code_format" not in cfg:
            bad.append("mkdocs.yml: the mermaid fence")
    return bad


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("stage")
    s.add_argument("out")
    s.add_argument("--ref", default="main", help="the tag or branch outside links point at")
    sub.add_parser("check")
    a = ap.parse_args(argv)
    if a.cmd == "stage":
        pages = stage(os.path.abspath(a.out), a.ref)
        print(f"staged {len(pages)} pages of {NAME} {_version()} into {a.out} (links at {a.ref})")
        return 0
    bad = check()
    for b in bad:
        print(b)
    print("ok" if not bad else f"{len(bad)} problems")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
