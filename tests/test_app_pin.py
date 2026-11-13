"""The vendored TandemApp tree equals the commit app.pin names (ENG-413, QA ENG-414).

Until WP14, TandemApp's repository is the source of the engine's vendored copy and nobody edits the
copy by hand: `bash tools/app_vendor.sh --from SRC COMMIT` replaces it whole and writes app.pin (the
folder, the commit, the commit's tree, the folder's tree, the excluded paths). Here the folder is
hashed as git hashes a tree, from the files on disk, so the test runs in a copy without .git (the box
harness's) as well as in a checkout. A file changed, added, removed or made executable under the
folder changes the hash; files git ignores there (its .gitignore files' patterns, and caches such as
__pycache__) do not count, as git would not commit them.

The test matrix, the Gherkin of the transition rule (plan section 6):

  1  the pin names a folder, a full commit id, the trees and the exclusions    test_the_pin_reads
  2  the folder on disk hashes to the pin's tree                                test_the_folder_is_the_pinned_tree
  3  an edit, a new file, a removed file and a mode change each change the
     hash; an ignored file does not (the hasher's controls)                     test_the_hasher_sees_every_edit
  4  no excluded path exists in the folder                                      test_no_excluded_path_is_vendored
  5  in a checkout: HEAD's folder is the pin's tree too                         test_head_matches_the_pin

Run: python tests/test_app_pin.py
"""
from __future__ import annotations

import fnmatch
import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIN = os.path.join(ROOT, "app.pin")
#: never part of a tree, whatever the ignore files say
ALWAYS_SKIP = {".git", "__pycache__", ".pytest_cache"}


def read_pin(path: str = PIN) -> dict:
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, _, value = line.partition(" ")
            out[key] = value.strip()
    out["exclude"] = out.get("exclude", "").split()
    return out


# ---------------------------------------------------------------------------- ignore rules

def _rules(path: str, base: str, outer: bool = False) -> list:
    """(base, pattern, anchored, dir_only) of one .gitignore; negations are not supported and
    make the test fail loudly, so a vendored tree that needs them is seen. `outer`: the checkout's
    own .gitignore, of which only the unanchored patterns reach into the folder."""
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return out
    for raw in lines:
        line = raw.rstrip()
        if not line or line.startswith("#"):
            continue
        if outer and (line.startswith("!") or "/" in line.rstrip("/")):
            continue
        assert not line.startswith("!"), f"{path}: a negated pattern ({line}) is not supported here"
        dir_only = line.endswith("/")
        line = line.rstrip("/")
        anchored = "/" in line
        out.append((base, line.lstrip("/"), anchored, dir_only))
    return out


def _ignored(rel: str, is_dir: bool, rules: list) -> bool:
    name = rel.rsplit("/", 1)[-1]
    if name in ALWAYS_SKIP or name.endswith(".pyc"):
        return True
    for base, pat, anchored, dir_only in rules:
        if dir_only and not is_dir:
            continue
        if base and not rel.startswith(base + "/"):
            continue
        sub = rel[len(base) + 1:] if base else rel
        if fnmatch.fnmatchcase(sub, pat) if anchored else fnmatch.fnmatchcase(name, pat):
            return True
    return False


# ---------------------------------------------------------------------------- git's tree hash

def _object(kind: str, data: bytes) -> bytes:
    return hashlib.sha1(kind.encode() + b" " + str(len(data)).encode() + b"\0" + data).digest()


def tree_id(top: str, extra_rules: list | None = None) -> str:
    """The id git gives the tree of the folder `top` as it is on disk, ignored files left out."""
    rules = list(extra_rules or [])

    def walk(d: str, rel: str, rules: list) -> bytes | None:
        if rel == "" or os.path.isfile(os.path.join(d, ".gitignore")):
            rules = rules + _rules(os.path.join(d, ".gitignore"), rel)
        entries = []
        for name in os.listdir(d):
            p = os.path.join(d, name)
            r = f"{rel}/{name}" if rel else name
            st = os.lstat(p)
            is_dir = stat.S_ISDIR(st.st_mode)
            if _ignored(r, is_dir, rules):
                continue
            if stat.S_ISLNK(st.st_mode):
                entries.append((name, b"120000", _object("blob", os.readlink(p).encode())))
            elif is_dir:
                sub = walk(p, r, rules)
                if sub is not None:                      # git keeps no empty folder
                    entries.append((name + "/", b"40000", sub))
            elif stat.S_ISREG(st.st_mode):
                with open(p, "rb") as f:
                    data = f.read()
                mode = b"100755" if st.st_mode & stat.S_IXUSR else b"100644"
                entries.append((name, mode, _object("blob", data)))
        if not entries:
            return None
        body = b"".join(mode + b" " + name.rstrip("/").encode() + b"\0" + sha
                        for name, mode, sha in sorted(entries, key=lambda e: e[0].encode()))
        return _object("tree", body)

    got = walk(top, "", rules)
    return (got or _object("tree", b"")).hex()


def _folder(pin: dict) -> str:
    d = pin["dir"]
    assert d and "/" not in d and not d.startswith("."), d
    return os.path.join(ROOT, d)


# ---------------------------------------------------------------------------- the tests

def test_the_pin_reads():
    pin = read_pin()
    for key in ("dir", "commit", "commit_tree", "tree"):
        assert key in pin, f"app.pin has no {key} line"
    for key in ("commit", "commit_tree", "tree"):
        v = pin[key]
        assert len(v) == 40 and all(c in "0123456789abcdef" for c in v), (key, v)
    assert os.path.isdir(_folder(pin)), pin["dir"]
    return f"{pin['dir']}/ is TandemApp {pin['commit'][:12]}, tree {pin['tree'][:12]}"


def test_the_folder_is_the_pinned_tree():
    pin = read_pin()
    got = tree_id(_folder(pin), _rules(os.path.join(ROOT, ".gitignore"), "", outer=True))
    assert got == pin["tree"], (
        f"{pin['dir']}/ hashes to {got}, app.pin says {pin['tree']}: {pin['dir']}/ was changed by "
        f"hand. Undo it, or vendor a TandemApp commit with tools/app_vendor.sh")
    return got


def test_the_hasher_sees_every_edit():
    """The hasher's controls on a small tree, against git's own ids where git is present."""
    d = tempfile.mkdtemp(prefix="app-pin-")
    try:
        os.makedirs(os.path.join(d, "pkg", "sub"))
        os.makedirs(os.path.join(d, "empty"))
        with open(os.path.join(d, ".gitignore"), "w") as f:
            f.write("# build output\n/dist/\nlogs/\n*.egg-info/\n")
        for rel, text in (("a.py", "print(1)\n"), ("pkg/b.txt", "b\n"), ("pkg/sub/c.sh", "echo c\n"),
                          ("pkg/sub/d", "")):
            with open(os.path.join(d, rel), "w") as f:
                f.write(text)
        os.chmod(os.path.join(d, "pkg", "sub", "c.sh"), 0o755)
        base = tree_id(d)
        if shutil.which("git"):
            env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
            subprocess.run(["git", "init", "-q", d], check=True, env=env)
            subprocess.run(["git", "-C", d, "add", "-A"], check=True, env=env)
            git_id = subprocess.run(["git", "-C", d, "write-tree"], check=True, env=env,
                                    capture_output=True, text=True).stdout.strip()
            assert base == git_id, (base, git_id)
        # ignored: a cache, a build folder, a log folder, a .pyc
        for rel in ("__pycache__/x.pyc", "dist/page.js", "pkg/logs/run.log", "pkg/y.pyc",
                    "z.egg-info/PKG-INFO"):
            os.makedirs(os.path.dirname(os.path.join(d, rel)), exist_ok=True)
            with open(os.path.join(d, rel), "w") as f:
                f.write("x")
        assert tree_id(d) == base, "an ignored file changed the hash"
        seen = set()
        for what, do, undo in (
                ("edit", lambda: open(os.path.join(d, "pkg", "b.txt"), "a").write("more\n"),
                 lambda: open(os.path.join(d, "pkg", "b.txt"), "w").write("b\n")),
                ("new file", lambda: open(os.path.join(d, "pkg", "new.py"), "w").write(""),
                 lambda: os.unlink(os.path.join(d, "pkg", "new.py"))),
                ("removed", lambda: os.rename(os.path.join(d, "a.py"), d + ".a"),
                 lambda: os.rename(d + ".a", os.path.join(d, "a.py"))),
                ("mode", lambda: os.chmod(os.path.join(d, "a.py"), 0o755),
                 lambda: os.chmod(os.path.join(d, "a.py"), 0o644)),
                ("anchored dist not at the top", lambda: os.makedirs(os.path.join(d, "pkg", "dist"))
                 or open(os.path.join(d, "pkg", "dist", "f"), "w").write("f"),
                 lambda: os.unlink(os.path.join(d, "pkg", "dist", "f"))
                 or os.rmdir(os.path.join(d, "pkg", "dist")))):
            do()
            changed = tree_id(d)
            undo()
            assert changed != base, f"{what}: the hash did not change"
            assert tree_id(d) == base, f"{what}: undone, the hash did not come back"
            seen.add(what)
        return f"{len(seen)} kinds of edit seen, ignored files not counted"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_no_excluded_path_is_vendored():
    pin = read_pin()
    top = _folder(pin)
    present = [e for e in pin["exclude"] if os.path.lexists(os.path.join(top, e.rstrip("/")))]
    assert not present, f"excluded paths inside {pin['dir']}/: {present}"
    return f"{len(pin['exclude'])} excluded paths absent"


def test_head_matches_the_pin():
    pin = read_pin()
    if not os.path.isdir(os.path.join(ROOT, ".git")) and not os.path.isfile(os.path.join(ROOT, ".git")):
        from scriptmode import Skip
        raise Skip("a copy without .git: the folder's hash above is the check")
    r = subprocess.run(["git", "-C", ROOT, "rev-parse", f"HEAD:{pin['dir']}"], capture_output=True,
                       text=True)
    if r.returncode != 0:
        from scriptmode import Skip
        raise Skip(f"git cannot read HEAD here: {r.stderr.strip()[:200]}")
    assert r.stdout.strip() == pin["tree"], (r.stdout.strip(), pin["tree"])
    return pin["tree"]


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from scriptmode import run
    run(globals())
