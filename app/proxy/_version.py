"""The release's version: the repository's VERSION file in a checkout, the installed
distribution's metadata in a wheel.

VERSION holds the release name as the project writes it (`0.4.1-test`, `0.4.0`); `PEP440` is the
same name as a Python package version (`0.3.29.dev0`, `0.4.0`), which `pyproject.toml` reads. A
`-test` build is a development release; `-rcN` a release candidate.
"""

from __future__ import annotations

import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def release() -> str:
    """The release name as VERSION writes it, '' when neither source answers."""
    if os.path.exists(os.path.join(_ROOT, "pyproject.toml")):      # a checkout
        try:
            with open(os.path.join(_ROOT, "VERSION")) as f:
                return f.read().strip()
        except OSError:
            pass
    try:
        from importlib.metadata import PackageNotFoundError, version
        try:
            return version("tandemllm")
        except PackageNotFoundError:
            return ""
    except ImportError:                                               # pragma: no cover
        return ""


def pep440(name: str) -> str:
    """The PEP 440 form of a release name; a local label (`0.15.0+test`, a build of the release
    made here, OPS-234's update check) stays as it is."""
    m = re.fullmatch(r"(\d+(?:\.\d+)*)(?:-(test|dev|rc\d*|a\d*|b\d*))?(\+[a-z0-9]+(?:\.[a-z0-9]+)*)?",
                     name.strip(), re.I)
    if not m:
        raise ValueError(f"VERSION '{name}' is not N.N.N with an optional -test, -rcN, -aN, -bN "
                         f"and +label")
    base, tag, local = m.group(1), m.group(2), (m.group(3) or "").lower()
    if not tag:
        return base + local
    if tag in ("test", "dev"):
        return base + ".dev0" + local
    n = re.sub(r"\D", "", tag) or "0"
    return base + tag[0:2 if tag.startswith("rc") else 1] + n + local


def _pep440_now() -> str:
    name = release() or "0.0.0"
    try:
        return pep440(name)
    except ValueError:
        return name                     # the installed metadata is PEP 440 already


PEP440 = _pep440_now()
