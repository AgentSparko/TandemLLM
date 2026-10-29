"""OPS-238: the standalone dashboard and proxy is `tandem-app` (TandemApp); `tandem-dash` stays as
an alias until 0.17.0.

  1  the alias prints one line on stderr, then answers as `tandem-app` does: the same stdout,
     the same stderr after that line, the same exit code       test_the_old_name_runs_the_new_one
  2  no file names `tandem-dash` outside the changelogs, the alias and its migration note
                                                                test_no_stale_name

    python tests/test_app_rename.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LINE = "tandem-dash is now tandem-app; the old name is removed in 0.17.0\n"
# what a console script runs: `sys.exit(<entry point>())` with the arguments in sys.argv
SCRIPT = "import sys; from proxy.__main__ import {fn}; sys.exit({fn}())"

# the files that may still say `tandem-dash`: the release history, the alias and its tests, and in
# docs/proxy.md the migration note (its lines name the old name on purpose)
ALLOWED = {
    "CHANGELOG.md": None,
    "manual/chapters/changelog.tex": None,
    "pyproject.toml": re.compile(r'^tandem-dash = "proxy\.__main__:main_old_name"$'),
    "proxy/__main__.py": None,
    "tests/test_app_rename.py": None,
    "tests/test_package.py": re.compile(r'\["tandem-dash"\]'),
    "tools/package_check.sh": re.compile(r"# the old name"),
    "docs/proxy.md": re.compile(r"old name|-v tandem-dash:/"),
}


def _run(fn: str, *argv: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("TANDEM_")}
    return subprocess.run([sys.executable, "-c", SCRIPT.format(fn=fn), *argv], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=60)


def test_the_old_name_runs_the_new_one():
    for argv in (["--help"], ["version"], ["check", "--help"], ["backends", "nosuch"]):
        new, old = _run("main", *argv), _run("main_old_name", *argv)
        assert LINE not in new.stderr, (argv, new.stderr)
        assert old.stderr.count(LINE) == 1 and old.stderr.startswith(LINE), (argv, old.stderr)
        assert old.stderr[len(LINE):] == new.stderr, argv
        assert (old.stdout, old.returncode) == (new.stdout, new.returncode), argv
    assert new.returncode == 2 and "tandem-app backends" in new.stderr, new.stderr
    assert "tandem-app check" in _run("main", "check", "--help").stdout
    assert _run("main", "version").stdout.startswith("tandemapp ")


def _hits() -> list[tuple[str, int, str]]:
    out = subprocess.run(["git", "-C", ROOT, "grep", "-n", "-I", "tandem-dash"],
                         capture_output=True, text=True)
    if out.returncode in (0, 1):
        return [(f, int(n), text) for f, n, text in
                (line.split(":", 2) for line in out.stdout.splitlines())]
    hits = []                         # a copy without .git (the fresh-install tests' release)
    for d, subs, names in os.walk(ROOT):
        # git grep skips the ignored build output (build/, dist/, *.egg-info) of pip install ./app
        subs[:] = [s for s in subs if s not in (".git", "node_modules", "__pycache__")
                   and not s.endswith(".egg-info") and not (d == ROOT and s in ("build", "dist"))]
        for name in names:
            path = os.path.join(d, name)
            try:
                with open(path, encoding="utf-8") as f:
                    lines = f.read().splitlines()
            except (UnicodeDecodeError, OSError):
                continue
            rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
            hits += [(rel, n, t) for n, t in enumerate(lines, 1) if "tandem-dash" in t]
    return hits


def test_no_stale_name():
    stale = []
    for f, n, text in _hits():
        if f not in ALLOWED:
            stale.append(f"{f}:{n}: {text.strip()[:120]}")
        elif ALLOWED[f] is not None and not ALLOWED[f].search(text):
            stale.append(f"{f}:{n} (outside its allowed lines): {text.strip()[:120]}")
    assert not stale, "tandem-dash is tandem-app (OPS-238):\n" + "\n".join(stale)


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
