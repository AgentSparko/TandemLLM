"""`tandem remote upgrade`: the install's tree at another release (engine-ctl-v1 1.1; ENG-454).

    upgrade plan --ref REF                     the install's origin at REF (fetched first)
    upgrade plan --sha256 HEX                  an archive of the tree on stdin (git archive, tar or
                                               tar.gz, the tree at its root), checked and kept
    upgrade apply (--ref REF | --sha256 HEX) --confirm T [--when now|idle|at=...] [--deadline S]

Only an install has an upgrade: DIR/src is a git checkout that install.sh made, DIR/venv its
Python and DIR/run/ops its config. A checkout is refused (`not_an_install`).

THE PLAN changes nothing that runs. `--ref` fetches the origin (`git fetch`, as install.sh does)
and resolves REF as install.sh does (a tag, a branch of the origin, a commit). An archive is read
from stdin (at most ARCHIVE_MAX), its sha256 checked against --sha256 and its members checked: no
absolute path, no `..`, no macOS `._` file, no link that leaves the tree, regular files, folders
and links only, at most UNPACKED_MAX in all; it is kept as run/remote/upgrades/<sha256>.tar for
the apply. Either way the target must have `tandem remote` (server/remote/cli.py), so this key
still reaches the engine afterwards, and an installer that takes --update and --no-fetch. The
plan names both trees (commit, VERSION, engine API, this contract's version, the venv's pins),
whether the venv is synced to new pins, what is kept, the steps, the rollback, and a confirm token
bound to the current commit and the target.

THE APPLY checks the token against the same plan (an archive from its kept file, a ref without a
new fetch) and starts a lifecycle job of kind `upgrade` (server/remote/lifecycle.py), `--when idle`
by default. The job, once it acts:
  1. the tree must still be at the plan's commit; the previous tree is kept: the commit under
     refs/tandem/previous, and a copy of run/ops, bin/tandem and its install.sh in
     run/remote/upgrades/<job>/;
  2. stops the engine (run/remote/stopped: the watchdog leaves it down);
  3. an archive becomes a commit of DIR/src's repository (refs/tandem/upgrade/<sha256 prefix>),
     its parent the current one; nothing of the work tree changes yet;
  4. runs the TARGET's install.sh: --dir DIR --ref COMMIT --no-fetch --update --yes. That is
     install.sh's own update path: the checkout at COMMIT, the venv synced to its pins, run/ops and
     bin/tandem written again from the new release with this install's keys as they are; no
     weights, no corpus, no start;
  5. starts the engine and waits for /health.
A failed step 3 or 4 or a start that does not reach /health rolls back: the engine stopped, DIR/src
checked out at the previous commit, the venv synced back with the previous install.sh's --update
when the pins changed, run/ops and bin/tandem put back byte for byte, the engine started again; the
job fails and says so (`rolled_back`). The overlay (run/managed.env) is not touched. Standard
library only, and everything it needs is imported before the tree changes under it.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import time

from server.remote import overlay
from server.remote.grammar import CTL_VERSION, EXIT_REFUSED, Refusal
from server.remote.paths import Tree

ARCHIVE_MAX = 512 << 20
UNPACKED_MAX = 2 << 30
#: what the target tree must hold
NEEDS = ("VERSION", "install.sh", "ops/serve.env", "server/engine_api.py", "server/remote/cli.py",
         "server/remote/grammar.py")
FETCH_TIMEOUT_S = 300
INSTALL_TIMEOUT_S = 3600
KEEP_ARCHIVES = 2
KEEP_JOBS = 3
GIT_ID = {"GIT_AUTHOR_NAME": "tandem remote", "GIT_AUTHOR_EMAIL": "remote@localhost",
          "GIT_COMMITTER_NAME": "tandem remote", "GIT_COMMITTER_EMAIL": "remote@localhost"}


class UpgradeError(Exception):
    pass


# ------------------------------------------------------------------------------------- git
def git(tree: Tree, *args: str, env: dict | None = None, timeout: float = 120, cwd: str | None = None,
        check: bool = True) -> str:
    e = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "USER")}
    e.update(GIT_TERMINAL_PROMPT="0", **(env or {}))
    try:
        r = subprocess.run(["git", *args] if cwd else ["git", "-C", tree.root, *args], cwd=cwd, env=e,
                           capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        raise UpgradeError(f"git {args[0]}: {exc}") from None
    if check and r.returncode != 0:
        err = r.stderr.decode("utf-8", "replace").strip().splitlines()
        raise UpgradeError(f"git {args[0]} exited {r.returncode}: {(err or [''])[-1][:300]}")
    return r.stdout.decode("utf-8", "replace").strip()


def head(tree: Tree) -> str:
    return git(tree, "rev-parse", "HEAD")


def resolve_ref(tree: Tree, ref: str) -> str | None:
    """REF as install.sh resolves it: a tag, a branch of the origin, then anything git takes."""
    for cand in (f"refs/tags/{ref}", f"refs/remotes/origin/{ref}", ref):
        out = git(tree, "rev-parse", "-q", "--verify", f"{cand}^{{commit}}", check=False)
        if re.fullmatch(r"[0-9a-f]{40}", out or ""):
            return out
    return None


# ------------------------------------------------------------------------------------- the trees
def lock_sha(install_sh: str) -> str | None:
    """install.sh's LOCK as its venv marker (DIR/venv/.tandem-lock) names it: sha256 of LOCK and a
    newline, 16 hex digits."""
    m = re.search(r"^LOCK=\"\$\(cat <<'EOF'\n(.*?)\nEOF\n\)\"", install_sh, re.S | re.M)
    if not m:
        return None
    return hashlib.sha256((m.group(1) + "\n").encode()).hexdigest()[:16]


def describe(files: dict) -> dict:
    """{version, engine_api, ctl, lock, update} of a tree from the texts of NEEDS."""
    def lit(text: str, name: str):
        m = re.search(rf'^{name} = "([^"]+)"', text or "", re.M)
        return m.group(1) if m else None
    inst = files.get("install.sh") or ""
    return {"version": (files.get("VERSION") or "").strip() or None,
            "engine_api": lit(files.get("server/engine_api.py"), "VERSION"),
            "ctl": lit(files.get("server/remote/grammar.py"), "CTL_VERSION"),
            "lock": lock_sha(inst),
            "update": "--update)" in inst and "--no-fetch)" in inst,
            "remote": "server/remote/cli.py" in files,
            "hook": overlay.HOOK in (files.get("ops/serve.env") or "")}


def files_of_commit(tree: Tree, commit: str) -> dict:
    out = {}
    for f in NEEDS:
        if git(tree, "cat-file", "-t", f"{commit}:{f}", check=False) == "blob":
            out[f] = git(tree, "show", f"{commit}:{f}", timeout=60)
    return out


def files_of_tree(root: str) -> dict:
    out = {}
    for f in NEEDS:
        try:
            with open(os.path.join(root, f), encoding="utf-8", errors="replace") as fh:
                out[f] = fh.read()
        except OSError:
            pass
    return out


def venv_lock(tree: Tree) -> str | None:
    try:
        with open(os.path.join(tree.install, "venv", ".tandem-lock")) as fh:
            return fh.read().strip() or None
    except (OSError, TypeError):
        return None


# ------------------------------------------------------------------------------------- the archive
def members(data: bytes) -> list[tarfile.TarInfo]:
    """The archive's members, checked (Refusal bad_archive), the tree at its root."""
    def bad(why: str):
        return Refusal("bad_archive", f"the archive: {why}; nothing was written", EXIT_REFUSED)
    try:
        tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:*")
        ms = tf.getmembers()
    except (tarfile.TarError, EOFError, OSError, ValueError):
        raise bad("not a tar or tar.gz archive") from None
    total, names = 0, set()
    for m in ms:
        name = m.name[2:] if m.name.startswith("./") else m.name
        parts = [p for p in name.split("/") if p not in ("", ".")]
        if m.name.startswith("/") or ".." in parts:
            raise bad(f"a member outside the tree ({m.name[:80]!r})")
        if parts and parts[-1].startswith("._"):
            raise bad(f"a macOS metadata file ({m.name[:80]!r}): make it with git archive, or COPYFILE_DISABLE=1")
        if m.type == tarfile.XGLTYPE or m.name == "pax_global_header":
            continue
        if not (m.isfile() or m.isdir() or m.issym()):
            raise bad(f"{m.name[:80]!r} is neither a file, a folder nor a link")
        if m.issym():
            target = os.path.normpath(os.path.join(os.path.dirname("/".join(parts)), m.linkname))
            if m.linkname.startswith("/") or target.startswith(".."):
                raise bad(f"the link {m.name[:80]!r} leaves the tree")
        total += max(0, m.size)
        if total > UNPACKED_MAX:
            raise bad(f"more than {UNPACKED_MAX >> 30} GiB unpacked")
        names.add("/".join(parts))
    missing = [f for f in NEEDS if f != "server/remote/cli.py" and f not in names]
    if missing:
        raise bad(f"no {', '.join(missing)} at its root (the tree's own files, git archive without --prefix)")
    return [m for m in ms if not (m.type == tarfile.XGLTYPE or m.name == "pax_global_header")]


def files_of_archive(data: bytes, ms: list[tarfile.TarInfo]) -> dict:
    tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:*")
    out = {}
    for m in ms:
        name = m.name[2:] if m.name.startswith("./") else m.name
        if name in NEEDS and m.isfile():
            out[name] = tf.extractfile(m).read().decode("utf-8", "replace")
    return out


def staged(tree: Tree, sha: str) -> str:
    return os.path.join(tree.upgrades, f"{sha}.tar")


def stage(tree: Tree, data: bytes, sha: str) -> str:
    os.makedirs(tree.upgrades, mode=0o700, exist_ok=True)
    path = staged(tree, sha)
    fd = os.open(path + ".part", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.replace(path + ".part", path)
    olds = sorted((f for f in os.listdir(tree.upgrades) if re.fullmatch(r"[0-9a-f]{64}\.tar", f)),
                  key=lambda f: os.stat(os.path.join(tree.upgrades, f)).st_mtime, reverse=True)
    for f in olds[KEEP_ARCHIVES:]:
        try:
            os.unlink(os.path.join(tree.upgrades, f))
        except OSError:
            pass
    return path


def unpack(data: bytes, ms: list[tarfile.TarInfo], dest: str) -> None:
    """The checked members under `dest`: files with their execute bit, folders, links."""
    tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:*")
    for m in ms:
        name = m.name[2:] if m.name.startswith("./") else m.name
        p = os.path.join(dest, *[x for x in name.split("/") if x not in ("", ".")])
        if m.isdir():
            os.makedirs(p, exist_ok=True)
        elif m.issym():
            os.makedirs(os.path.dirname(p), exist_ok=True)
            os.symlink(m.linkname, p)
        else:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as fh:
                shutil.copyfileobj(tf.extractfile(m), fh)
            os.chmod(p, 0o755 if m.mode & 0o111 else 0o644)


# ------------------------------------------------------------------------------------- the plan
def _need(tree: Tree) -> None:
    if not tree.install:
        raise Refusal("not_an_install", "this engine runs from a checkout, not an install: an upgrade is "
                                        "install.sh's (re-run it, or git in the checkout)", EXIT_REFUSED)
    if not os.path.exists(os.path.join(tree.root, ".git")):
        raise Refusal("not_an_install", f"{os.path.relpath(tree.root, tree.base)} is not a git checkout: "
                                        f"install.sh makes one", EXIT_REFUSED)


def token(head_commit: str, target: str) -> str:
    blob = json.dumps(["upgrade", head_commit, target], separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def plan(tree: Tree, opts: dict, data: bytes | None = None, *, fetch: bool = True) -> tuple[dict, dict | None]:
    """(the plan, what was checked: {sha256, bytes} for an archive). `data` is the archive (plan); an
    apply passes none and the kept file is read."""
    _need(tree)
    try:
        cur = head(tree)
        dirty = git(tree, "status", "--porcelain", "--untracked-files=no")
    except UpgradeError as exc:
        raise Refusal("not_an_install", str(exc), EXIT_REFUSED) from None
    if dirty:
        raise Refusal("local_changes", f"{os.path.relpath(tree.root, tree.base)} has local changes: install.sh "
                                       f"refuses to update it", EXIT_REFUSED)
    now = describe(files_of_tree(tree.root))
    checked = None
    if "--ref" in opts:
        ref = opts["--ref"]
        if fetch:
            try:
                git(tree, "fetch", "--quiet", "--tags", "--force", "--prune", "origin", timeout=FETCH_TIMEOUT_S)
            except UpgradeError as exc:
                raise Refusal("fetch_failed", f"{exc}: give the tree as an archive instead (--sha256, on stdin)",
                              EXIT_REFUSED) from None
        commit = resolve_ref(tree, ref)
        if commit is None:
            raise Refusal("unknown_ref", f"no tag, branch or commit {ref!r} in the origin", EXIT_REFUSED)
        to = {"source": "ref", "ref": ref, "commit": commit, **describe(files_of_commit(tree, commit))}
        target = commit
    else:
        sha = opts["--sha256"]
        if data is None:
            try:
                with open(staged(tree, sha), "rb") as fh:
                    data = fh.read()
            except OSError:
                raise Refusal("not_planned", f"no archive {sha[:16]}... is kept: plan it first (upgrade plan "
                                             f"--sha256 HEX with the archive on stdin)", EXIT_REFUSED) from None
        got = hashlib.sha256(data).hexdigest()
        if got != sha:
            raise Refusal("hash_mismatch", f"the {len(data)} bytes have sha256 {got}, not {sha}: nothing was "
                                           f"written", EXIT_REFUSED, sha256=got)
        ms = members(data)
        checked = {"sha256": got, "bytes": len(data)}
        to = {"source": "archive", "sha256": got, "bytes": len(data), **describe(files_of_archive(data, ms))}
        target = got
    if not to["remote"]:
        raise Refusal("target_unsupported", "the target has no tandem remote (server/remote/cli.py): this key "
                                            "could not reach the engine after it", EXIT_REFUSED)
    if not to["update"]:
        raise Refusal("target_unsupported", "the target's install.sh takes no --update and --no-fetch", EXIT_REFUSED)
    if to.get("commit") == cur:
        raise Refusal("no_change", f"the tree is at {cur[:12]} already", EXIT_REFUSED)
    if to["source"] == "archive" and data is not None:
        stage(tree, data, target)                     # kept for the apply, once every check passed
    have = venv_lock(tree)
    synced = to["lock"] is None or to["lock"] != have
    warnings = []
    a, b = now["engine_api"], to["engine_api"]
    va, vb = (tuple(int(x) for x in v.split(".")) if v and re.fullmatch(r"[0-9]+\.[0-9]+", v) else None
              for v in (a, b))
    if va and vb and va[0] != vb[0]:
        warnings.append(f"engine API {a} -> {b}: another major; a caller on {va[0]}.x refuses this engine")
    elif va and vb and vb < va:
        warnings.append(f"engine API {a} -> {b}: an older minor; what {a} added is gone")
    if synced:
        warnings.append("the venv is synced to the target's pins: the installer downloads what it lacks, and a "
                        "rollback syncs it back")
    if not to["hook"]:
        warnings.append("the target's ops/serve.env does not read run/managed.env: the overlay is ignored there")
    body = {
        "from": {"commit": cur, **{k: now[k] for k in ("version", "engine_api", "ctl", "lock")}, "venv_lock": have},
        "to": {k: v for k, v in to.items() if k not in ("update", "remote", "hook")},
        "venv": "synced" if synced else "kept",
        "keeps": ["the weights and the corpus", "run/local.env, run/setup.env and the overlay (run/managed.env)",
                  "this install's keys of run/ops/serve.env (address, port, weights, mode, chatd's owner)",
                  "secrets, logs, the engine's data"],
        "steps": ["wait (--when, default idle)", "keep the previous tree: refs/tandem/previous and a copy of "
                  "run/ops, bin/tandem and install.sh", "stop the engine"]
                 + (["commit the archive into src's repository"] if to["source"] == "archive" else [])
                 + ["run the target's install.sh --update --no-fetch --ref COMMIT", "start the engine, wait for "
                    "/health"],
        "rollback": "a failed install or start: the previous commit checked out, the venv synced back when the "
                    "pins changed, run/ops and bin/tandem put back, the engine started again; the job fails",
        "warnings": warnings,
        "confirm": token(cur, target)}
    return body, checked


def apply(tree: Tree, opts: dict) -> tuple[dict, dict]:
    """(the plan, the job's `upgrade` record) once the token matches; a Refusal otherwise."""
    body, checked = plan(tree, opts, fetch=False)
    if opts.get("--confirm") != body["confirm"]:
        raise Refusal("confirm_mismatch", "the token is not this plan's: plan again", EXIT_REFUSED, upgrade=body)
    to = body["to"]
    rec = {"source": to["source"], "ref": to.get("ref"), "commit": to.get("commit"), "sha256": to.get("sha256"),
           "version": to.get("version"), "from_commit": body["from"]["commit"],
           "from_version": body["from"]["version"], "venv": body["venv"]}
    return body, {k: v for k, v in rec.items() if v is not None}


# ------------------------------------------------------------------------------------- the job
def _keep_previous(tree: Tree, keep: str, prev: str) -> None:
    os.makedirs(keep, mode=0o700, exist_ok=True)
    ops = os.path.join(tree.install, "run", "ops")
    if os.path.isdir(ops):
        shutil.copytree(ops, os.path.join(keep, "ops"), symlinks=True, dirs_exist_ok=True)
    ctl = os.path.join(tree.install, "bin", "tandem")
    if os.path.isfile(ctl):
        shutil.copy2(ctl, os.path.join(keep, "bin-tandem"))
    with open(os.path.join(keep, "install.prev.sh"), "w") as fh:
        fh.write(git(tree, "show", f"{prev}:install.sh"))
    with open(os.path.join(keep, "previous.json"), "w") as fh:
        json.dump({"commit": prev, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, fh)
    git(tree, "update-ref", "refs/tandem/previous", prev)
    jobs = sorted(d for d in os.listdir(tree.upgrades) if os.path.isdir(os.path.join(tree.upgrades, d)))
    for d in jobs[:-KEEP_JOBS]:
        shutil.rmtree(os.path.join(tree.upgrades, d), ignore_errors=True)


def _put_back(tree: Tree, keep: str) -> None:
    src = os.path.join(keep, "ops")
    if os.path.isdir(src):
        shutil.copytree(src, os.path.join(tree.install, "run", "ops"), symlinks=True, dirs_exist_ok=True)
    if os.path.isfile(os.path.join(keep, "bin-tandem")):
        shutil.copy2(os.path.join(keep, "bin-tandem"), os.path.join(tree.install, "bin", "tandem"))


def commit_archive(tree: Tree, sha: str, keep: str) -> str:
    """The kept archive as a commit of DIR/src's repository (parent: HEAD); the work tree unchanged."""
    try:
        with open(staged(tree, sha), "rb") as fh:
            data = fh.read()
    except OSError:
        raise UpgradeError(f"the archive {sha[:16]}... is no longer kept") from None
    if hashlib.sha256(data).hexdigest() != sha:
        raise UpgradeError("the kept archive's sha256 changed")
    try:
        ms = members(data)
    except Refusal as ref:
        raise UpgradeError(str(ref)) from None
    work = os.path.join(keep, "tree")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    unpack(data, ms, work)
    gitdir = git(tree, "rev-parse", "--absolute-git-dir")
    env = dict(GIT_ID, GIT_DIR=gitdir, GIT_WORK_TREE=work, GIT_INDEX_FILE=os.path.join(keep, "index"))
    try:
        git(tree, "add", "-A", "-f", ".", env=env, cwd=work, timeout=600)
        tree_id = git(tree, "write-tree", env=env, cwd=work)
        commit = git(tree, "commit-tree", tree_id, "-p", "HEAD", "-m",
                     f"tandem remote upgrade: an archive, sha256 {sha}", env=env, cwd=work)
        git(tree, "update-ref", f"refs/tandem/upgrade/{sha[:16]}", commit)
    finally:
        shutil.rmtree(work, ignore_errors=True)
        try:
            os.unlink(os.path.join(keep, "index"))
        except OSError:
            pass
    return commit


def act(r) -> int:
    """The `upgrade` job's acting part (lifecycle.Runner `r`, the box lock held)."""
    tree, job = r.tree, r.job
    up = dict(job["upgrade"])
    prev = head(tree)
    if prev != up["from_commit"]:
        r.fail(f"the tree is at {prev[:12]}, not at the plan's {up['from_commit'][:12]}: nothing changed")
        return 1
    keep = os.path.join(tree.upgrades, job["id"])
    _keep_previous(tree, keep, prev)
    r.log(f"upgrade: the previous tree is {prev[:12]} (refs/tandem/previous; run/ops and bin/tandem in "
          f"{os.path.relpath(keep, tree.base)})")
    r._mark(True, "tandem remote upgrade")
    if not r.stop():
        r._mark(False)
        r.fail("ops/stop.sh failed (logs/launcher.log): nothing changed")
        return 1
    try:
        target = up.get("commit") or commit_archive(tree, up["sha256"], keep)
    except UpgradeError as exc:
        return rollback(r, keep, prev, f"the archive did not become a commit: {exc}", installed=False)
    up["to_commit"] = target
    r.save(upgrade=up, phase="installing")
    installer = os.path.join(keep, "install.sh")
    with open(installer, "w") as fh:
        fh.write(git(tree, "show", f"{target}:install.sh"))
    r.log(f"upgrade: install.sh --update --no-fetch --ref {target[:12]} (the target's installer)")
    rc = r.cmd(["bash", installer, "--dir", tree.install, "--ref", target, "--no-fetch", "--update", "--yes"],
               INSTALL_TIMEOUT_S, dict(os.environ))
    if rc != 0:
        return rollback(r, keep, prev, f"the installer exited {rc} (logs/launcher.log)", installed=True)
    if r.start():
        return r.done(f"healthy on {up.get('version') or '?'} ({target[:12]}); the previous tree is {prev[:12]}")
    return rollback(r, keep, prev, "the engine did not reach /health on the new tree", installed=True)


def rollback(r, keep: str, prev: str, why: str, *, installed: bool) -> int:
    tree = r.tree
    r.save(phase="rolling back")
    r.log(f"rollback: {why}; back to {prev[:12]}")
    r.stop()
    notes = []
    try:
        if head(tree) != prev:
            git(tree, "-c", "advice.detachedHead=false", "checkout", "--quiet", "--detach", prev, timeout=300)
    except UpgradeError as exc:
        notes.append(f"the checkout of {prev[:12]} failed ({exc})")
    if installed and r.job["upgrade"].get("venv") == "synced":
        rc = r.cmd(["bash", os.path.join(keep, "install.prev.sh"), "--dir", tree.install, "--ref", prev,
                    "--no-fetch", "--update", "--yes"], INSTALL_TIMEOUT_S, dict(os.environ))
        if rc != 0:
            notes.append(f"the previous install.sh --update exited {rc}")
    try:
        _put_back(tree, keep)
    except OSError as exc:
        notes.append(f"run/ops and bin/tandem were not put back ({exc})")
    ok = r.start()
    extra = f" ({'; '.join(notes)})" if notes else ""
    r.fail(f"{why}; the previous tree ({prev[:12]}) is back{extra} and the engine "
           f"{'answers' if ok else 'did not start with it either'}", rolled_back={"commit": prev, "healthy": ok})
    return 1
