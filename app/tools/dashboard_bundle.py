#!/usr/bin/env python3
"""The dashboard's built page (dashboard/dist), which is not in git (OPS-246): build it, check it,
pack it for a release, fetch a released one, or put it where a web server serves it.

    python3 tools/dashboard_bundle.py build [--out DIR] [--commit SHA] [--prune-modules]
    python3 tools/dashboard_bundle.py repro                 a second build, compared by hash
    python3 tools/dashboard_bundle.py verify [DIR] [--expect SHA256]
    python3 tools/dashboard_bundle.py record [DIR] [--source NAME]
    python3 tools/dashboard_bundle.py pack [DIR] --out FILE.tar.gz      (+ FILE.tar.gz.sha256)
    python3 tools/dashboard_bundle.py fetch --url URL --sha256 SHA256 [--dest DIR]
    python3 tools/dashboard_bundle.py ensure [--url URL --sha256 SHA256]
    python3 tools/dashboard_bundle.py publish --to DEST [--url URL --sha256 SHA256]
    python3 tools/dashboard_bundle.py info [DIR]

build runs `npm ci` (the lockfile, exactly) and `npm run build` (type check, then Vite) in
dashboard/ and writes dist/bundle.json: the bundle's content hash, each file's sha256, the commit,
the lockfile's sha256 and the Node version. verify recomputes the hash and compares it with the
manifest (and with --expect). ensure keeps an intact bundle, else builds when npm is on the PATH,
else fetches when a URL and its sha256 are given (or TANDEM_DASHBOARD_URL and
TANDEM_DASHBOARD_SHA256), else exits 2 with what to do. publish runs ensure, then copies the
checked bundle to DEST: a local folder (replaced as a whole) or `host:/path` through rsync.

Exit 0 done, 1 a failed build, check or fetch, 2 no way to get the page (or a usage error).
Standard library only; Python 3.11 or newer.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

from tandemapp import bundle  # noqa: E402

DASH = os.path.join(APP, "dashboard")
DIST = os.path.join(DASH, "dist")


def _say(text: str) -> None:
    print(text, flush=True)


def cmd_build(a) -> int:
    doc = bundle.build(DASH, out_dir=a.out, commit=a.commit)
    _say(f"bundle {doc['sha256']} ({doc['files']} files, {doc['bytes']} bytes) in "
         f"{a.out or DIST}")
    if a.prune_modules:
        nm = os.path.join(DASH, "node_modules")
        if os.path.isdir(nm) and not os.path.islink(nm):
            shutil.rmtree(nm)
            _say("removed dashboard/node_modules")
    return 0


def cmd_repro(a) -> int:
    first = bundle.verify(DIST)
    if not first["ok"]:
        _say("FAIL the bundle in dist/ does not verify: " + "; ".join(first["problems"]))
        return 1
    tmp = tempfile.mkdtemp(prefix="bundle-repro-")
    try:
        out = os.path.join(tmp, "dist")
        doc = bundle.build(DASH, out_dir=out, install=False)
        same = doc["sha256"] == first["sha256"]
        _say(f"first  {first['sha256']}\nsecond {doc['sha256']}")
        if not same:
            mine = (bundle.read_manifest(DIST) or {}).get("file_sha256") or {}
            other = doc.get("file_sha256") or {}
            for p in sorted(set(mine) | set(other)):
                if mine.get(p) != other.get(p):
                    _say(f"  differs: {p}")
        _say("PASS two builds, one hash" if same else "FAIL two builds gave two hashes")
        return 0 if same else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def cmd_verify(a) -> int:
    v = bundle.verify(a.dir, expect=a.expect)
    _say(json.dumps(v, indent=1) if a.json else
         f"{'PASS' if v['ok'] else 'FAIL'} bundle {v['sha256']} in {a.dir}"
         + "".join(f"\n  {p}" for p in v["problems"]))
    return 0 if v["ok"] else 1


def cmd_record(a) -> int:
    doc = bundle.write_manifest(a.dir, a.source, commit=a.commit,
                                lockfile=os.path.join(DASH, "package-lock.json"))
    _say(f"recorded bundle {doc['sha256']} ({doc['files']} files) in {a.dir}")
    return 0


def cmd_pack(a) -> int:
    digest = bundle.pack(a.dir, a.out)
    doc = bundle.read_manifest(a.dir) or {}
    _say(f"{a.out}\n  archive sha256 {digest}\n  bundle  sha256 {doc.get('sha256')}")
    return 0


def cmd_fetch(a) -> int:
    doc = bundle.fetch(a.url, a.sha256, a.dest)
    _say(f"fetched bundle {doc.get('sha256')} ({doc.get('files')} files) into {a.dest}")
    return 0


def cmd_ensure(a) -> int:
    doc = bundle.ensure(APP, url=a.url, sha256=a.sha256)
    _say(f"bundle {doc.get('sha256')} ({doc.get('source')})")
    return 0


def cmd_publish(a) -> int:
    doc = bundle.ensure(APP, url=a.url, sha256=a.sha256)
    v = bundle.verify(DIST)
    if not v["ok"]:
        _say("FAIL the bundle does not verify: " + "; ".join(v["problems"]))
        return 1
    dest = a.to
    remote = ":" in dest.split("/", 1)[0] and not os.path.isabs(dest)
    if remote:
        rsync = shutil.which("rsync")
        if not rsync:
            _say("FAIL rsync is not on the PATH (needed for a host:/path target)")
            return 1
        cmd = [rsync, "-a", "--delete", "--checksum", DIST + "/", dest.rstrip("/") + "/"]
        _say("$ " + " ".join(cmd[1:]))
        r = subprocess.run(cmd)
        if r.returncode != 0:
            _say(f"FAIL rsync exit {r.returncode}")
            return 1
    else:
        dest = os.path.abspath(dest)
        parent = os.path.dirname(dest)
        os.makedirs(parent, exist_ok=True)
        stage = tempfile.mkdtemp(prefix=".bundle-pub-", dir=parent)
        os.rmdir(stage)
        shutil.copytree(DIST, stage, symlinks=False)
        got = bundle.verify(stage)
        if not got["ok"]:
            shutil.rmtree(stage, ignore_errors=True)
            _say("FAIL the copy does not verify: " + "; ".join(got["problems"]))
            return 1
        bundle._swap(stage, dest)
    _say(f"published bundle {doc.get('sha256') or v['sha256']} to {dest}")
    return 0


def cmd_info(a) -> int:
    _say(json.dumps(bundle.info(a.dir), indent=1))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="dashboard_bundle.py",
                                 description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("build", help="npm ci + npm run build + the manifest")
    p.add_argument("--out", default=None, help="build into this folder instead of dist/")
    p.add_argument("--commit", default=None, help="the commit to record (default: git's HEAD)")
    p.add_argument("--prune-modules", action="store_true",
                   help="remove dashboard/node_modules after the build")
    p.set_defaults(fn=cmd_build)
    p = sub.add_parser("repro", help="build again into a temp folder and compare the hash")
    p.set_defaults(fn=cmd_repro)
    for name, fn in (("verify", cmd_verify), ("record", cmd_record), ("info", cmd_info)):
        p = sub.add_parser(name)
        p.add_argument("dir", nargs="?", default=DIST)
        if name == "verify":
            p.add_argument("--expect", default=None, help="the bundle sha256 it must have")
            p.add_argument("--json", action="store_true")
        if name == "record":
            p.add_argument("--source", default="record")
            p.add_argument("--commit", default=None)
        p.set_defaults(fn=fn)
    p = sub.add_parser("pack", help="a reproducible .tar.gz of the bundle, with its .sha256")
    p.add_argument("dir", nargs="?", default=DIST)
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_pack)
    p = sub.add_parser("fetch", help="a released bundle, checked against its pinned sha256")
    p.add_argument("--url", required=True)
    p.add_argument("--sha256", required=True)
    p.add_argument("--dest", default=DIST)
    p.set_defaults(fn=cmd_fetch)
    for name, fn in (("ensure", cmd_ensure), ("publish", cmd_publish)):
        p = sub.add_parser(name)
        p.add_argument("--url", default=None)
        p.add_argument("--sha256", default=None)
        if name == "publish":
            p.add_argument("--to", required=True, help="a folder, or host:/path for rsync")
        p.set_defaults(fn=fn)
    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except bundle.BundleError as exc:
        print(f"FAIL {exc}", file=sys.stderr, flush=True)
        return 2 if isinstance(exc, bundle.NoBundle) else 1


if __name__ == "__main__":
    sys.exit(main())
