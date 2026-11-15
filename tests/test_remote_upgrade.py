"""`tandem remote upgrade` (engine-ctl-v1 1.1; ENG-454): plan, confirm, the job, the rollback.

A sandbox install (tests/remote_box.py, `install=True`) whose DIR/src is a git repository: its first
commit is the sandbox tree with a stand-in install.sh (STANDIN: it takes --update and --no-fetch,
checks out --ref and copies run/ops again, as install.sh's update path does, with no venv, weights
or GPU; its LOCK names the venv's pins), a stub engine as server/app.py and the real ops scripts.
Targets are commits of a clone of it (`rel`), given as `git archive --format=tar.gz` on stdin or
as a tag of `rel`, the sandbox's origin. The engine is the stub; its starts go through the
install's bin/tandem start, as the job's are.

The test matrix (the Gherkin of ENG-454):

  1  plan refusals, nothing kept: an archive whose sha256 is not --sha256 (hash_mismatch, the one it
     got journaled), not a tar, a member outside the tree, a macOS `._` file, no install.sh at its
     root (bad_archive); a target without tandem remote or whose install.sh has no --update
     (target_unsupported); the tree it is at (no_change); local changes in src (local_changes); a
     checkout (not_an_install); an unknown ref                     test_plan_refusals
  2  a plan of an archive: both trees' version, engine API and contract version, the venv kept (the
     same pins), the steps and the rollback, a confirm token; the archive kept as
     run/remote/upgrades/<sha256>.tar (600); the journal has the sha256 it checked. apply with
     another token is refused, an archive never planned too (not_planned); apply with the token
     answers a job of kind upgrade; the job ends done: src at a new commit whose parent is the
     previous one, refs/tandem/previous, VERSION the target's, the installer called once with
     --update --no-fetch --ref COMMIT, the engine healthy           test_archive_upgrade
  3  rollback: a target whose engine does not start, and one whose installer fails: the job fails
     with rolled_back (the previous commit, healthy), src back at the previous commit, run/ops and
     bin/tandem byte for byte as before, the engine healthy on the previous tree; a target with
     other pins: the previous install.sh runs again to sync the venv back
                                                                    test_rollback
  4  a ref: plan --ref TAG fetches the origin and resolves it as install.sh does; apply without a new
     fetch; the job ends done at that commit                         test_ref_upgrade

Run: python tests/test_remote_upgrade.py
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import remote_box as R  # noqa: E402
from server.remote import upgrade as U  # noqa: E402

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "advice.detachedHead=false"]

STANDIN = r'''#!/usr/bin/env bash
# A stand-in for install.sh's update path (tests/test_remote_upgrade.py).
set -euo pipefail
LOCK="$(cat <<'EOF'
pinned==@PIN@
EOF
)"
DIR=""; REF=""
while [ $# -gt 0 ]; do
    case "$1" in
        --dir) DIR="$2"; shift 2 ;;
        --ref) REF="$2"; shift 2 ;;
        --update) shift ;;
        --no-fetch) shift ;;
        --yes) shift ;;
        *) echo "stand-in: unknown option $1" >&2; exit 2 ;;
    esac
done
echo "@PIN@ $REF" >> "$DIR/installer.calls"
git -C "$DIR/src" -c advice.detachedHead=false checkout --quiet --detach "$REF"
if [ -f "$DIR/src/FAIL_INSTALL" ]; then echo "stand-in: failing on purpose" >&2; exit 3; fi
printf '# updated to %s\n' "$(cat "$DIR/src/VERSION")" >> "$DIR/run/ops/serve.env"
printf '# updated\n' >> "$DIR/bin/tandem"
'''

#: bin/tandem start's memory check reads the GPU: off here (the stub needs none)
SKIP = {"TANDEM_SKIP_MEMCHECK": "1"}

DEAD = 'import sys\nprint("stub: this release does not start", flush=True)\nsys.exit(3)\n'


def _git(cwd: str, *args: str) -> str:
    r = subprocess.run(GIT + list(args), cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stderr)
    return r.stdout.strip()


def _box() -> dict:
    """An install whose src is a repository at its first commit, and `rel`, a clone of it."""
    b = R.make(install=True)
    src = b["root"]
    with open(os.path.join(src, "install.sh"), "w") as fh:
        fh.write(STANDIN.replace("@PIN@", "A"))
    with open(os.path.join(src, ".gitignore"), "w") as fh:
        fh.write("/run/\n/logs/\n/proc/\n/seen*\n/inflight.json\n__pycache__/\n/box.flock\n")
    _git(src, "init", "-q")
    _git(src, "add", "-A")
    _git(src, "commit", "-q", "-m", "base")
    b["base"] = _git(src, "rev-parse", "HEAD")
    with open(os.path.join(b["install"], "venv", ".tandem-lock"), "w") as fh:
        fh.write(U.lock_sha(STANDIN.replace("@PIN@", "A")) + "\n")
    b["rel"] = tempfile.mkdtemp(prefix="remote-rel-")
    _git(b["rel"], "clone", "-q", src, ".")
    open(os.path.join(b["root"], ".git", "info", "exclude"), "a").close()
    return b


def _release(b: dict, version: str, *, pin: str = "A", dead: bool = False, fail: bool = False,
             change=None) -> tuple[str, bytes]:
    """A commit of `rel` (from the base): its VERSION, its installer's pins, an engine that does not
    start, an installer that fails; (the commit, its git archive as tar.gz)."""
    rel = b["rel"]
    _git(rel, "checkout", "-q", "--detach", b["base"])
    with open(os.path.join(rel, "VERSION"), "w") as fh:
        fh.write(version + "\n")
    with open(os.path.join(rel, "install.sh"), "w") as fh:
        fh.write(STANDIN.replace("@PIN@", pin))
    if dead:
        with open(os.path.join(rel, "server", "app.py"), "w") as fh:
            fh.write(DEAD)
    if fail:
        open(os.path.join(rel, "FAIL_INSTALL"), "w").close()
    if change:
        change(rel)
    _git(rel, "add", "-A")
    _git(rel, "commit", "-q", "-m", version)
    commit = _git(rel, "rev-parse", "HEAD")
    tgz = subprocess.run(["git", "archive", "--format=tar.gz", commit], cwd=rel, capture_output=True, check=True).stdout
    return commit, tgz


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _cleanup(b: dict) -> None:
    shutil.rmtree(b.get("rel") or "/nonexistent", ignore_errors=True)
    R.cleanup(b)


def _tar(files: dict) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, body in files.items():
            data = body.encode()
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def _start(b: dict) -> None:
    rc, a = R.call(b, "start", **SKIP)
    assert rc == 0, a
    job = R.wait_job(b, 180)
    assert job["state"] == "done", job


def test_plan_refusals():
    b = _box()
    try:
        _, tgz = _release(b, "0.0.2-test")
        n = 0
        rc, a = R.call(b, "upgrade", "plan", "--sha256", "1" * 64, stdin=tgz)
        assert rc == 3 and a["error"]["code"] == "hash_mismatch" and a["error"]["sha256"] == _sha(tgz), a
        assert R.journal(b)[-1]["checked"] == {"sha256": _sha(tgz)}
        R.valid("error", a)
        n += 1
        good = {f: "x" for f in U.NEEDS}
        for blob in (b"not a tar", _tar(dict(good, **{"../up.txt": "x"})), _tar(dict(good, **{"ops/._serve.env": "x"})),
                     _tar({k: v for k, v in good.items() if k != "install.sh"})):
            rc, a = R.call(b, "upgrade", "plan", "--sha256", _sha(blob), stdin=blob)
            assert rc == 3 and a["error"]["code"] == "bad_archive", a
            n += 1
        for change in (lambda rel: os.unlink(os.path.join(rel, "server", "remote", "cli.py")),
                       lambda rel: open(os.path.join(rel, "install.sh"), "w").write("#!/bin/bash\nexit 0\n")):
            _, blob = _release(b, "0.0.3-test", change=change)
            rc, a = R.call(b, "upgrade", "plan", "--sha256", _sha(blob), stdin=blob)
            assert rc == 3 and a["error"]["code"] == "target_unsupported", a
            n += 1
        assert not os.path.isdir(os.path.join(b["root"], "run", "remote", "upgrades")) or \
            not os.listdir(os.path.join(b["root"], "run", "remote", "upgrades"))
        # the tree it is at; an unknown ref; local changes
        _git(b["root"], "remote", "add", "origin", b["rel"])
        _git(b["rel"], "tag", "same", b["base"])
        rc, a = R.call(b, "upgrade", "plan", "--ref", "same")
        assert rc == 3 and a["error"]["code"] == "no_change", a
        rc, a = R.call(b, "upgrade", "plan", "--ref", "nothere")
        assert rc == 3 and a["error"]["code"] == "unknown_ref", a
        with open(os.path.join(b["root"], "VERSION"), "a") as fh:
            fh.write("changed\n")
        rc, a = R.call(b, "upgrade", "plan", "--sha256", _sha(tgz), stdin=tgz)
        assert rc == 3 and a["error"]["code"] == "local_changes", a
        n += 3
    finally:
        _cleanup(b)
    c = R.make()
    try:
        rc, a = R.call(c, "upgrade", "plan", "--ref", "v1")
        assert rc == 3 and a["error"]["code"] == "not_an_install", a
    finally:
        R.cleanup(c)
    return f"{n + 1} refusals"


def test_archive_upgrade():
    b = _box()
    try:
        _start(b)
        _, tgz = _release(b, "0.0.2-test")
        rc, p = R.call(b, "upgrade", "plan", "--sha256", _sha(tgz), stdin=tgz)
        assert rc == 0, p
        R.valid("upgrade-plan", p)
        plan = p["plan"]
        assert plan["from"]["commit"] == b["base"] and plan["from"]["version"] == "0.0.0-test", plan
        assert plan["to"]["source"] == "archive" and plan["to"]["version"] == "0.0.2-test" and \
            plan["to"]["sha256"] == _sha(tgz) and plan["to"]["ctl"] == "1.1", plan
        assert plan["venv"] == "kept" and plan["from"]["lock"] == plan["to"]["lock"] == plan["from"]["venv_lock"], plan
        assert p["checked"] == {"sha256": _sha(tgz), "bytes": len(tgz)} and R.journal(b)[-1]["checked"] == p["checked"]
        kept = os.path.join(b["root"], "run", "remote", "upgrades", f"{_sha(tgz)}.tar")
        assert os.stat(kept).st_mode & 0o777 == 0o600
        rc, a = R.call(b, "upgrade", "apply", "--sha256", _sha(tgz), "--confirm", "0" * 16)
        assert rc == 3 and a["error"]["code"] == "confirm_mismatch", a
        R.valid("error", a)
        rc, a = R.call(b, "upgrade", "apply", "--sha256", "2" * 64, "--confirm", plan["confirm"])
        assert rc == 3 and a["error"]["code"] == "not_planned", a
        rc, a = R.call(b, "upgrade", "apply", "--sha256", _sha(tgz), "--confirm", plan["confirm"], "--when", "now",
                       **SKIP)
        assert rc == 0 and a["job"]["kind"] == "upgrade" and a["job"]["upgrade"]["sha256"] == _sha(tgz), a
        R.valid("job", a)
        job = R.wait_job(b, 300)
        assert job["state"] == "done", job
        new = _git(b["root"], "rev-parse", "HEAD")
        assert new != b["base"] and _git(b["root"], "rev-parse", "HEAD^") == b["base"]
        assert job["upgrade"]["to_commit"] == new and _git(b["root"], "rev-parse", "refs/tandem/previous") == b["base"]
        assert open(os.path.join(b["root"], "VERSION")).read().strip() == "0.0.2-test"
        calls = open(os.path.join(b["install"], "installer.calls")).read().splitlines()
        assert calls == [f"A {new}"], calls
        rc, st = R.call(b, "status")
        assert st["engine"]["state"] == "healthy" and st["engine_version"] == "0.0.2-test", st
        R.valid("status", st)
    finally:
        _cleanup(b)
    return "planned, confirmed, upgraded, healthy"


def _snapshot(b: dict) -> dict:
    out = {}
    for d in (os.path.join(b["install"], "run", "ops"),):
        for f in sorted(os.listdir(d)):
            out[f] = open(os.path.join(d, f), "rb").read()
    out["bin/tandem"] = open(os.path.join(b["install"], "bin", "tandem"), "rb").read()
    return out


def test_rollback():
    n = 0
    for kind in ("dead", "fail", "pins"):
        b = _box()
        try:
            _start(b)
            before = _snapshot(b)
            _, tgz = _release(b, f"0.0.9-{kind}", dead=kind in ("dead", "pins"), fail=kind == "fail",
                              pin="B" if kind == "pins" else "A")
            rc, p = R.call(b, "upgrade", "plan", "--sha256", _sha(tgz), stdin=tgz)
            assert rc == 0 and p["plan"]["venv"] == ("synced" if kind == "pins" else "kept"), p
            rc, a = R.call(b, "upgrade", "apply", "--sha256", _sha(tgz), "--confirm", p["plan"]["confirm"],
                           "--when", "now", **SKIP)
            assert rc == 0, a
            job = R.wait_job(b, 300)
            assert job["state"] == "failed" and job["rolled_back"] == {"commit": b["base"], "healthy": True}, (kind, job)
            assert _git(b["root"], "rev-parse", "HEAD") == b["base"], kind
            assert _snapshot(b) == before, kind
            rc, st = R.call(b, "status")
            assert st["engine"]["state"] == "healthy" and st["engine_version"] == "0.0.0-test", (kind, st)
            R.valid("status", st)
            calls = open(os.path.join(b["install"], "installer.calls")).read().split()
            want = 2 if kind != "pins" else 4                     # the target's call, then the previous one's
            assert len(calls) == want and calls[0] == ("B" if kind == "pins" else "A"), (kind, calls)
            if kind == "pins":
                assert calls[2:] == ["A", b["base"]], calls
            n += 1
        finally:
            _cleanup(b)
    return f"{n} rollbacks to a healthy previous tree"


def test_ref_upgrade():
    b = _box()
    try:
        _start(b)
        commit, _ = _release(b, "0.1.0-test")
        _git(b["rel"], "tag", "v0.1.0-test", commit)
        _git(b["root"], "remote", "add", "origin", b["rel"])
        rc, p = R.call(b, "upgrade", "plan", "--ref", "v0.1.0-test")
        assert rc == 0 and p["plan"]["to"] == {"source": "ref", "ref": "v0.1.0-test", "commit": commit,
                                               "version": "0.1.0-test", "engine_api": p["plan"]["to"]["engine_api"],
                                               "ctl": "1.1", "lock": p["plan"]["from"]["lock"]}, p
        rc, a = R.call(b, "upgrade", "apply", "--ref", "v0.1.0-test", "--confirm", p["plan"]["confirm"],
                       "--when", "now", **SKIP)
        assert rc == 0 and a["job"]["upgrade"]["commit"] == commit, a
        job = R.wait_job(b, 300)
        assert job["state"] == "done" and _git(b["root"], "rev-parse", "HEAD") == commit, job
    finally:
        _cleanup(b)
    return "a tag of the origin, upgraded"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
