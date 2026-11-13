"""TandemApp's copy of the engine's contracts is the one PIN names (ENG-427, QA ENG-428).

The engine owns contracts/engine-api-v1 and contracts/engine-ext-v1. TandemApp copies both folders
whole from one engine commit and writes contracts/engine-api-v1/PIN: the commit, each folder's
version and the git tree id the folder has in that commit. Nobody edits the copy here; a new engine
contract is a new copy and a new PIN. The folders are hashed from the files on disk as git hashes a
tree, so the test runs in a copy without .git too. Nothing here reaches the engine: a manual job
compares the PIN with the engine's own repository.

  1  PIN names a full commit id, both versions and both tree ids        test_the_pin_reads
  2  each folder on disk (PIN left out) hashes to its tree id            test_the_copy_is_the_pinned_tree
  3  the versions are the files' own: routes.json's version is engine_api and
     proxy.engineapi.SPEAKS, ext.json's version is ext_api              test_the_versions_agree
  4  the hasher sees an edit, a new file, a removed file and a mode change;
     a cache file does not count                                        test_the_hasher_sees_every_edit
  5  in a checkout: HEAD's folders are what is on disk                  test_head_matches_the_disk

Run: python tests/test_contract_pin.py
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from proxy import engineapi  # noqa: E402
from proxy_util import run  # noqa: E402

CONTRACTS = os.path.join(ROOT, "contracts")
PIN = os.path.join(CONTRACTS, "engine-api-v1", "PIN")
FOLDERS = ("engine-api-v1", "engine-ext-v1")
#: never part of a tree: git's own folder and Python's caches
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


def _object(kind: str, data: bytes) -> bytes:
    return hashlib.sha1(kind.encode() + b" " + str(len(data)).encode() + b"\0" + data).digest()


def tree_id(top: str, leave_out: tuple = ()) -> str:
    """The id git gives the folder `top` as it is on disk; `leave_out`: names at its top level that
    are not part of it (PIN)."""

    def walk(d: str, first: bool) -> bytes | None:
        entries = []
        for name in os.listdir(d):
            if name in SKIP or name.endswith(".pyc") or (first and name in leave_out):
                continue
            p = os.path.join(d, name)
            st = os.lstat(p)
            if stat.S_ISLNK(st.st_mode):
                entries.append((name, b"120000", _object("blob", os.readlink(p).encode())))
            elif stat.S_ISDIR(st.st_mode):
                sub = walk(p, False)
                if sub is not None:
                    entries.append((name + "/", b"40000", sub))
            elif stat.S_ISREG(st.st_mode):
                with open(p, "rb") as f:
                    blob = _object("blob", f.read())
                mode = b"100755" if st.st_mode & stat.S_IXUSR else b"100644"
                entries.append((name, mode, blob))
        if not entries:
            return None
        entries.sort(key=lambda e: e[0].encode())       # git sorts a folder as name + "/"
        data = b"".join(m + b" " + n.rstrip("/").encode() + b"\0" + h for n, m, h in entries)
        return _object("tree", data)

    got = walk(top, True)
    return got.hex() if got else ""


def test_the_pin_reads():
    pin = read_pin()
    assert re.fullmatch(r"[0-9a-f]{40}", pin.get("engine_commit", "")), pin
    for name in FOLDERS:
        assert re.fullmatch(r"[0-9a-f]{40}", pin.get(name, "")), (name, pin)
    for key in ("engine_api", "ext_api"):
        assert re.fullmatch(r"[0-9]+\.[0-9]+", pin.get(key, "")), (key, pin)
    return f"engine {pin['engine_commit'][:12]}, engine API {pin['engine_api']}, ext {pin['ext_api']}"


def test_the_copy_is_the_pinned_tree():
    pin = read_pin()
    bad = []
    for name in FOLDERS:
        got = tree_id(os.path.join(CONTRACTS, name), leave_out=("PIN",))
        if got != pin[name]:
            bad.append(f"contracts/{name} hashes to {got}, PIN says {pin[name]}: the copy was edited "
                       f"or is not the engine's at {pin['engine_commit'][:12]}; copy the folders "
                       f"again from that commit (or a newer one) and write PIN anew")
    assert not bad, bad
    return " ".join(f"{n} {pin[n][:12]}" for n in FOLDERS)


def test_the_versions_agree():
    pin = read_pin()
    with open(os.path.join(CONTRACTS, "engine-api-v1", "routes.json")) as f:
        api = json.load(f)["version"]
    with open(os.path.join(CONTRACTS, "engine-ext-v1", "ext.json")) as f:
        ext = json.load(f)["version"]
    assert pin["engine_api"] == api == engineapi.SPEAKS, (pin["engine_api"], api, engineapi.SPEAKS)
    assert pin["ext_api"] == ext, (pin["ext_api"], ext)
    return f"engine API {api} = SPEAKS, ext {ext}"


def test_the_hasher_sees_every_edit():
    src = os.path.join(CONTRACTS, "engine-ext-v1")
    with tempfile.TemporaryDirectory(prefix="contract-pin-") as d:
        top = os.path.join(d, "copy")
        shutil.copytree(src, top, ignore=shutil.ignore_patterns(*SKIP, "*.pyc"))
        base = tree_id(top)
        assert base == tree_id(src), "a copy hashes as the original"
        ext = os.path.join(top, "ext.json")
        with open(ext, "rb") as f:
            raw = f.read()
        with open(ext, "wb") as f:
            f.write(raw.replace(b'"1.0"', b'"1.1"', 1))
        assert tree_id(top) != base, "an edit"
        with open(ext, "wb") as f:
            f.write(raw)
        assert tree_id(top) == base
        open(os.path.join(top, "new.txt"), "w").close()
        assert tree_id(top) != base, "a new file"
        os.remove(os.path.join(top, "new.txt"))
        os.makedirs(os.path.join(top, "__pycache__"))
        open(os.path.join(top, "__pycache__", "x.cpython-312.pyc"), "w").close()
        assert tree_id(top) == base, "a cache file counts"
        os.chmod(ext, 0o755)
        assert tree_id(top) != base, "a mode change"
        os.chmod(ext, 0o644)
        os.remove(os.path.join(top, "knobs.json"))
        assert tree_id(top) != base, "a removed file"
        # PIN is left out only at the folder's top
        assert tree_id(src) == tree_id(src, leave_out=("PIN",))
    return "edit, new, removed and mode change seen; a cache file not"


def test_head_matches_the_disk():
    inside = subprocess.run(["git", "-C", ROOT, "rev-parse", "--show-toplevel"],
                            capture_output=True, text=True)
    if inside.returncode != 0 or os.path.realpath(inside.stdout.strip()) != os.path.realpath(ROOT):
        print("    (no checkout here: the disk is the copy under test)")
        return "no checkout"
    git = subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD:contracts/engine-ext-v1"],
                         capture_output=True, text=True)
    assert git.returncode == 0, f"HEAD has no contracts/engine-ext-v1: {git.stderr.strip()}"
    got = git.stdout.strip()
    assert got == tree_id(os.path.join(CONTRACTS, "engine-ext-v1")), got
    assert got == read_pin()["engine-ext-v1"], got
    return f"HEAD's engine-ext-v1 is {got[:12]}"


if __name__ == "__main__":
    run(globals())
