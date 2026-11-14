#!/usr/bin/env python3
"""Check TandemApp's copy of the engine's contracts against the engine's own repository (OPS-250).

    python3 tools/engine_pin_check.py --commit            print the engine commit PIN names
    python3 tools/engine_pin_check.py --engine PATH       check the copy against the engine at PATH

contracts/engine-api-v1/PIN names one engine commit, each folder's version and the git tree id the
folder has in that commit (tests/test_contract_pin.py holds the copy on disk to those ids without
reaching the engine). This tool reaches it: PATH is a clone of the engine's repository that holds
the pinned commit (the CI job checks it out at that commit; a local clone works the same). Checked:

  1  PATH has the commit
  2  each folder's tree id in that commit is the PIN's
  3  each folder's version in that commit (routes.json, ext.json) is the PIN's
  4  each file of the copy here (PIN left out) is the engine's at that commit, byte for byte, and
     no file is missing on either side

One line per problem and `engine pin: ok` or `engine pin: N problems`; exit 0 or 1, 2 on bad use.
Standard library and git only; nothing is written to PATH.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import tarfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACTS = os.path.join(ROOT, "contracts")
PIN = os.path.join(CONTRACTS, "engine-api-v1", "PIN")
#: folder -> (the PIN key of its version, the file that carries the version)
FOLDERS = {"engine-api-v1": ("engine_api", "routes.json"), "engine-ext-v1": ("ext_api", "ext.json")}
SKIP = {".git", "__pycache__", ".pytest_cache"}


def read_pin(path: str = PIN) -> dict:
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                key, _, value = line.partition(" ")
                out[key] = value.strip()
    return out


def _git(engine: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", engine, *args], capture_output=True, timeout=120)


def _local(folder: str) -> dict:
    """{path under the folder: bytes} of the copy here, PIN left out."""
    top = os.path.join(CONTRACTS, folder)
    out = {}
    for d, dirs, files in os.walk(top):
        dirs[:] = sorted(x for x in dirs if x not in SKIP)
        for name in files:
            if name.endswith(".pyc"):
                continue
            rel = os.path.relpath(os.path.join(d, name), top).replace(os.sep, "/")
            if rel == "PIN":
                continue
            with open(os.path.join(d, name), "rb") as f:
                out[rel] = f.read()
    return out


def _engine(engine: str, commit: str, folder: str) -> dict | None:
    """{path under the folder: bytes} of the engine's folder at `commit`, from `git archive`."""
    p = _git(engine, "archive", "--format=tar", commit, f"contracts/{folder}")
    if p.returncode != 0:
        return None
    out = {}
    with tarfile.open(fileobj=io.BytesIO(p.stdout)) as tar:
        for m in tar.getmembers():
            if m.isfile():
                out[m.name[len(f"contracts/{folder}/"):]] = tar.extractfile(m).read()
    return out


def check(engine: str, pin: dict | None = None) -> list[str]:
    pin = pin if pin is not None else read_pin()
    commit = pin.get("engine_commit", "")
    if len(commit) != 40:
        return [f"PIN: engine_commit is not a full commit id: {commit!r}"]
    if _git(engine, "cat-file", "-e", f"{commit}^{{commit}}").returncode != 0:
        return [f"the engine at {engine} does not hold commit {commit}"]
    probs = []
    for folder, (key, vfile) in FOLDERS.items():
        p = _git(engine, "rev-parse", f"{commit}:contracts/{folder}")
        got = p.stdout.decode().strip() if p.returncode == 0 else "(none)"
        if got != pin.get(folder):
            probs.append(f"{folder}: tree {got} at {commit[:12]}, PIN says {pin.get(folder)}")
        p = _git(engine, "show", f"{commit}:contracts/{folder}/{vfile}")
        try:
            version = json.loads(p.stdout)["version"] if p.returncode == 0 else None
        except (ValueError, KeyError, TypeError):
            version = None
        if version != pin.get(key):
            probs.append(f"{folder}: {vfile} version {version!r} at {commit[:12]}, PIN says {pin.get(key)!r}")
        theirs = _engine(engine, commit, folder)
        if theirs is None:
            probs.append(f"{folder}: not in the engine at {commit[:12]}")
            continue
        mine = _local(folder)
        for rel in sorted(set(mine) | set(theirs)):
            if rel not in theirs:
                probs.append(f"{folder}/{rel}: here, not in the engine")
            elif rel not in mine:
                probs.append(f"{folder}/{rel}: in the engine, not here")
            elif mine[rel] != theirs[rel]:
                probs.append(f"{folder}/{rel}: differs from the engine's")
    return probs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--commit", action="store_true", help="print the engine commit PIN names")
    g.add_argument("--engine", help="a clone of the engine's repository that holds that commit")
    a = ap.parse_args(argv)
    pin = read_pin()
    if a.commit:
        print(pin.get("engine_commit", ""))
        return 0
    if not os.path.isdir(a.engine):
        print(f"no such folder: {a.engine}")
        return 2
    probs = check(a.engine, pin)
    for p in probs:
        print(p)
    print("engine pin: ok" if not probs else f"engine pin: {len(probs)} problems")
    return 1 if probs else 0


if __name__ == "__main__":
    sys.exit(main())
