"""TandemApp's build backend: setuptools, plus the dashboard's built page (OPS-246).

The page (dashboard/dist) is not in git. A wheel or an sdist carries it, so before setuptools runs:

  - an intact bundle in dashboard/dist (its bundle.json matches the files) is used as it is; a page
    built by a plain `npm run build` gets its manifest recorded first;
  - with no page and TANDEM_DASHBOARD_URL plus TANDEM_DASHBOARD_SHA256 set, the released bundle is
    fetched and checked against that sha256 (tools/dashboard_bundle.py fetch);
  - otherwise the build stops with what to do (build it with Node, or fetch it), never a traceback.

An editable install (`pip install -e`) is held to the same rule, since pip shows nothing of a
successful build. With TANDEM_DASHBOARD_LATER=1 it installs without the page: it makes the empty
folders setuptools maps (dashboard/dist and dist/assets), and the server shows its "not built" page
until the page is built in place.

The backend never runs npm itself: a build needs the network and Node, which an install from a
release does not have.
"""

from __future__ import annotations

import os
import sys

from setuptools import build_meta as _meta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_NOTED: list = []                      # the editable note is printed once a process


class PageMissing(SystemExit):
    """The build stops here with the message (exit status 1, no traceback)."""


def _bundle():
    sys.path.insert(0, ROOT)
    try:
        from tandemapp import bundle
    finally:
        sys.path.remove(ROOT)
    return bundle


def need_page(kind: str, root: str | None = None, env: dict | None = None) -> dict:
    """The page's manifest for a wheel or an sdist; PageMissing with the actionable text."""
    root = root or ROOT
    bundle = _bundle()
    env = os.environ if env is None else env
    dist = os.path.join(root, "dashboard", "dist")
    if os.path.isfile(os.path.join(dist, "index.html")):
        v = bundle.verify(dist)
        if v["ok"]:
            return bundle.read_manifest(dist) or {}
        if bundle.read_manifest(dist) is None:
            return bundle.write_manifest(dist, "record")
        raise PageMissing(f"\ntandemapp {kind}: dashboard/dist does not match its bundle.json ("
                          + "; ".join(v["problems"]) + "). Build it again:\n  python3 "
                          + os.path.join(root, "tools", "dashboard_bundle.py") + " build\n")
    url, sha = env.get("TANDEM_DASHBOARD_URL"), env.get("TANDEM_DASHBOARD_SHA256")
    if url:
        try:
            return bundle.fetch(url, sha or "", dist)
        except bundle.BundleError as exc:
            raise PageMissing(f"\ntandemapp {kind}: the dashboard's bundle could not be fetched: "
                              f"{exc}\n") from None
    raise PageMissing(f"\ntandemapp {kind}: no built page.\n{bundle.message(dist)}\n")


# every hook checks first: setuptools' first hook (get_requires_*) already reads the package
# folders, and a missing dashboard/dist would end there with its own error
def get_requires_for_build_wheel(config_settings=None):
    need_page("wheel")
    return _meta.get_requires_for_build_wheel(config_settings)


def get_requires_for_build_sdist(config_settings=None):
    need_page("sdist")
    return _meta.get_requires_for_build_sdist(config_settings)


def get_requires_for_build_editable(config_settings=None):
    editable_page()
    return _meta.get_requires_for_build_editable(config_settings)


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    need_page("wheel")
    return _meta.build_wheel(wheel_directory, config_settings, metadata_directory)


def build_sdist(sdist_directory, config_settings=None):
    need_page("sdist")
    return _meta.build_sdist(sdist_directory, config_settings)


def prepare_metadata_for_build_wheel(metadata_directory, config_settings=None):
    need_page("wheel")
    return _meta.prepare_metadata_for_build_wheel(metadata_directory, config_settings)


def editable_page(root: str | None = None, err=None, env: dict | None = None) -> bool:
    """For `pip install -e`: True with a page (or a fetched one); with TANDEM_DASHBOARD_LATER=1
    False, the note on stderr and the empty folders; else PageMissing, as for a wheel."""
    root = root or ROOT
    err = err or sys.stderr
    env = os.environ if env is None else env
    dist = os.path.join(root, "dashboard", "dist")
    if env.get("TANDEM_DASHBOARD_LATER") != "1" or \
            os.path.isfile(os.path.join(dist, "index.html")):
        try:
            need_page("editable install", root=root, env=env)
            return True
        except PageMissing as exc:
            raise PageMissing(f"{exc.code}(Or install now and build the page later: "
                              "TANDEM_DASHBOARD_LATER=1 pip install -e ...)\n") from None
    os.makedirs(os.path.join(dist, "assets"), exist_ok=True)
    if _NOTED:
        return False
    _NOTED.append(dist)
    print("\ntandemapp (editable): no built page yet; /dashboard/ shows a placeholder until it is "
          "built.\n" + _bundle().message(dist) + "\n", file=err)
    return False


def prepare_metadata_for_build_editable(metadata_directory, config_settings=None):
    editable_page()
    return _meta.prepare_metadata_for_build_editable(metadata_directory, config_settings)


def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    editable_page()
    return _meta.build_editable(wheel_directory, config_settings, metadata_directory)
