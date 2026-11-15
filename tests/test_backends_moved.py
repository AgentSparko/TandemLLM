"""A box that still sets QSE_BACKENDS (ENG-413 WP14b, QA ENG-414): since engine API 1.8 the engine
relays to no other backend, and no path may drop a configured backend without a word. The doctor's
half is tests/test_doctor.py 9, the Setup Guide's plan tests/test_setup_change_list.py 5, the
server's own refusal tests/test_proxy_engine.py and tests/test_headless_start.py 3.

The test matrix:

  1  ops/start.sh with QSE_BACKENDS set to a config database: one REFUSING line that names the
     value, the import into TandemApp's controller, the doctor's repair and the doc, exit 2, and no
     engine started; empty or off starts the engine with `--backends off` as before; setup mode
     still starts (it passes no --backends)          test_start_refuses_a_config_database
  2  an install's re-run whose run/local.env sets it (Linux: install.sh runs there only): it stops
     before anything changes (run/, bin/ and src's commit), with the same pieces; a dry run says it
     would stop; --retire-backends comments the line out under a dated note and goes on, the
     database left as it was; the next re-run needs no flag          test_an_install_rerun

Run: python tests/test_backends_moved.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import test_ops_scripts as T  # noqa: E402

DOC = "docs/operations.md#other-backends-moved-to-tandemapp"


def _pieces(line: str, value: str) -> None:
    for want in (f"QSE_BACKENDS={value}", "relays to no other backend",
                 f"tandem-app import-backends --from {value}", "--retire-backends", DOC):
        assert want in line, (want, line)


def test_start_refuses_a_config_database():
    db = "/srv/x/.qwen38-spark-engine/backends/config.sqlite3"
    r, marks = T._start_with_fake_engine("0", f'QSE_BACKENDS="{db}"\n')
    lines = [ln for ln in r.stdout.splitlines() if "REFUSING" in ln]
    assert r.returncode == 2 and len(lines) == 1, r.stdout + r.stderr
    assert lines[0].startswith("[start] REFUSING: ") and lines[0].endswith("Nothing was started"), lines[0]
    _pieces(lines[0], db)
    assert "engine " not in marks, marks
    for value in ("", "off"):
        r, marks = T._start_with_fake_engine("0", f"QSE_BACKENDS={value}\n")
        assert r.returncode == 0 and "REFUSING" not in r.stdout, r.stdout + r.stderr
        argv = next(ln for ln in marks.splitlines() if ln.startswith("engine ")).split()
        assert argv[argv.index("--backends") + 1] == "off", argv

    def setup_marker(repo):
        os.makedirs(os.path.join(repo, "run"), exist_ok=True)
        with open(os.path.join(repo, "run", "setup-pending"), "w") as fh:
            fh.write("HOST=127.0.0.1\n")
    r, marks = T._start_with_fake_engine("0", f"QSE_BACKENDS={db}\n", prepare=setup_marker)
    assert r.returncode == 0 and "REFUSING" not in r.stdout, r.stdout + r.stderr
    argv = next(ln for ln in marks.splitlines() if ln.startswith("engine ")).split()
    assert "--setup" in argv and "--backends" not in argv, argv
    return "refused in one line, nothing started; off and empty start; setup mode starts"


def _tree_bytes(*roots: str) -> dict:
    out = {}
    for root in roots:
        for d, _, names in os.walk(root):
            for n in names:
                p = os.path.join(d, n)
                if not os.path.islink(p):
                    with open(p, "rb") as fh:
                        out[p] = fh.read()
    return out


def test_an_install_rerun():
    import test_install_control as I
    box = I.install("backends-moved", False, I.free_port())
    if box is None:
        print("    (skipped: install.sh runs on Linux only -- run it on the box)")
        return "skipped off Linux"
    d, env = box["dir"], box["env"]
    db = os.path.join(box["home"], ".qwen38-spark-engine", "backends", "config.sqlite3")
    os.makedirs(os.path.dirname(db), exist_ok=True)
    with open(db, "w") as fh:
        fh.write("the relay's config database\n")
    st_db = os.stat(db)
    local = os.path.join(d, "run", "local.env")
    with open(local, "a") as fh:
        fh.write('QSE_BACKENDS="${HOME}/.qwen38-spark-engine/backends/config.sqlite3"\n')

    def rerun(*extra):
        return subprocess.run(box["args"] + list(extra), env=env, capture_output=True, text=True, timeout=600,
                              stdin=subprocess.DEVNULL)

    def head():
        return subprocess.run(["git", "-C", os.path.join(d, "src"), "rev-parse", "HEAD"], capture_output=True,
                              text=True).stdout

    keep = (os.path.join(d, "run"), os.path.join(d, "bin"))
    before, commit = _tree_bytes(*keep), head()
    r = rerun()
    line = next((ln for ln in r.stderr.splitlines() if "sets QSE_BACKENDS=" in ln), "")
    assert r.returncode != 0 and line, r.stdout[-2000:] + r.stderr[-2000:]
    _pieces(line, db)
    assert f"(in {local})" in line and "Nothing was changed; the install keeps working as it is" in line, line
    assert _tree_bytes(*keep) == before and head() == commit
    r = rerun("--dry-run")
    assert r.returncode == 0 and "(dry-run, would stop here)" in r.stderr and "sets QSE_BACKENDS=" in r.stderr, \
        r.stderr[-2000:]
    assert f"comment QSE_BACKENDS out in {local} (dated)" in r.stdout, r.stdout[-2000:]
    assert _tree_bytes(*keep) == before
    r = rerun("--retire-backends")
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    assert f"QSE_BACKENDS={db} commented out in {local}; the backends' database stays" in r.stdout, r.stdout[-2000:]
    text = open(local).read()
    assert re.search(r"^# \d{4}-\d{2}-\d{2}, install\.sh --retire-backends \(ENG-413\): .*The line was:$", text, re.M), text
    assert '\n# QSE_BACKENDS="${HOME}/.qwen38-spark-engine/backends/config.sqlite3"\n' in text, text
    got = subprocess.run(["bash", "-c", 'set -a; . "$1" >/dev/null 2>&1; printf "[%s]" "${QSE_BACKENDS-unset}"', "-",
                          os.path.join(d, "run", "ops", "serve.env")], env=env, capture_output=True, text=True).stdout
    assert got == "[]", got
    st = os.stat(db)
    assert (st.st_size, st.st_mtime_ns, st.st_ino) == (st_db.st_size, st_db.st_mtime_ns, st_db.st_ino)
    r = rerun()
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    return "stopped before any change, the dry run too; --retire-backends commented it out and went on"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
