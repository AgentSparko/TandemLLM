"""tandem doctor (OPS-171, QA OPS-172): server/setup/doctor.py on temporary trees laid out as a checkout
and as an install, never the box's own.

The test matrix, the Gherkin of OPS-171:

  1  a lost execute bit is repaired, in the checkout and in run/ops, and logs/doctor.log says so;
     --check reports it, changes nothing and exits 1           test_a_lost_execute_bit
  2  a missing run/ops copy is copied from src/ops (755); one that differs is reported; bin/tandem's
     mode is repaired                                          test_the_run_ops_copies
  3  what doctor does not touch: a secrets file where the engine does not read it is reported and
     stays; one readable by others gets 600                    test_secrets
  4  stale pid files, an old hold pause and a setup marker with an address start.sh refuses are
     repaired; a live pid of ours, a fresh pause and a good marker are left
                                                              test_pids_pause_and_marker
  5  a config that does not source and an interpreter that does not run are named; the disk check
     names the GB needed and free                              test_config_python_and_disk
  6  before an apply: the job's check step ran doctor, repaired the mode and lists it
                                                              test_before_an_apply
  7  the release's lists: ops/executables.txt is git's 100755 list, doctor's copy list install.sh's
                                                              test_the_lists_agree
  8  bin/tandem doctor on an installed box (Linux)             test_tandem_doctor_on_an_install
  9  ENG-413 WP14b, QSE_BACKENDS (the relay the engine had until engine API 1.8): empty or off is ok;
     a config database in serve.env is a problem that names the file, the import and the repair,
     and neither --check nor the default repair changes it; --retire-backends comments each line
     that sets it (serve.env, run/setup.env, an install's run/local.env) under a dated note, keeps
     the old line, the rest of the file and its mode, and never touches the database; a value set
     by a file the doctor does not edit is reported and left; --check --retire-backends is a usage
     error                                                     test_qse_backends

Run: python tests/test_doctor.py
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from server.setup import doctor  # noqa: E402

# a HOME of its own: the box's own secrets file and chatd pid file are never read here
os.environ["HOME"] = os.path.realpath(tempfile.mkdtemp(prefix="doctor-home-"))


def _w(p: str, text: str, mode: int = 0o644) -> str:
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as fh:
        fh.write(text)
    os.chmod(p, mode)
    return p


def _checkout(serve_env: str | None = None) -> str:
    """A checkout: ops/ with a manifest, two scripts and a serve.env whose PY is this interpreter."""
    r = os.path.realpath(tempfile.mkdtemp(prefix="doctor-co-"))
    _w(os.path.join(r, "ops", "executables.txt"), "# list\nops/start.sh\nops/stop.sh\n")
    _w(os.path.join(r, "ops", "start.sh"), "#!/bin/bash\necho start\n", 0o755)
    _w(os.path.join(r, "ops", "stop.sh"), "#!/bin/bash\necho stop\n", 0o755)
    _w(os.path.join(r, "ops", "serve.env"), serve_env if serve_env is not None else
       f"REPO={r}\nPY={sys.executable}\nQSE_CHATD=off\nQSE_SECRETS={r}/state/secrets.env\n")
    return r


def _mode(p: str) -> int:
    return stat.S_IMODE(os.stat(p).st_mode)


def _states(rep: dict) -> dict:
    out: dict = {}
    for c in rep["checks"]:
        out.setdefault(c["id"], []).append(c["state"])
    return out


def test_a_lost_execute_bit():
    r = _checkout()
    os.chmod(os.path.join(r, "ops", "start.sh"), 0o644)
    # --check: reported, unchanged, exit 1
    out = subprocess.run([sys.executable, "-m", "server.setup.doctor", "--check", "--root", r], cwd=ROOT,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 1 and "ops/start.sh lost its execute bit (mode 644)" in out.stdout, out.stdout
    assert _mode(os.path.join(r, "ops", "start.sh")) == 0o644
    rep = doctor.run(r)
    assert rep["ok"] and rep["repaired"] == ["ops/start.sh: execute bit restored (was 644)"], rep
    assert _mode(os.path.join(r, "ops", "start.sh")) == 0o755
    log = open(os.path.join(r, "logs", "doctor.log")).read()
    assert "repaired: ops/start.sh: execute bit restored" in log and "1 repaired, 0 to look at" in log, log
    # a file readable by the owner only gets x for the owner only
    os.chmod(os.path.join(r, "ops", "stop.sh"), 0o600)
    doctor.run(r)
    assert _mode(os.path.join(r, "ops", "stop.sh")) == 0o700
    # nothing to do: ok, nothing repaired
    rep = doctor.run(r)
    assert rep["ok"] and not rep["repaired"] and _states(rep)["modes"] == ["ok"], rep


def _install() -> tuple[str, str]:
    """An install: DIR/.tandemllm-install, DIR/src (the checkout, with every script install.sh
    copies), DIR/run/ops (the copies and serve.env), DIR/bin/tandem."""
    d = os.path.realpath(tempfile.mkdtemp(prefix="doctor-inst-"))
    src = os.path.join(d, "src")
    _w(os.path.join(d, ".tandemllm-install"), "")
    _w(os.path.join(src, "ops", "executables.txt"), "ops/start.sh\n")
    for s in doctor.COPIES:
        _w(os.path.join(src, "ops", s), f"# {s}\n", 0o755)
        _w(os.path.join(d, "run", "ops", s), f"# {s}\n", 0o755)
    _w(os.path.join(d, "run", "ops", "serve.env"), f"REPO={src}\nPY={sys.executable}\nQSE_CHATD=off\n"
                                                    f"QSE_SECRETS={d}/state/secrets.env\n")
    _w(os.path.join(d, "bin", "tandem"), "#!/bin/bash\n", 0o755)
    return d, src


def test_the_run_ops_copies():
    d, src = _install()
    os.unlink(os.path.join(d, "run", "ops", "roles.sh"))
    os.chmod(os.path.join(d, "run", "ops", "start.sh"), 0o644)
    os.chmod(os.path.join(d, "bin", "tandem"), 0o644)
    _w(os.path.join(d, "run", "ops", "hold.sh"), "# an older hold.sh\n", 0o755)
    rep = doctor.run(src)
    assert "run/ops/roles.sh was missing: copied from src/ops" in rep["repaired"], rep
    assert _mode(os.path.join(d, "run", "ops", "roles.sh")) == 0o755
    assert open(os.path.join(d, "run", "ops", "roles.sh")).read() == "# roles.sh\n"
    assert "run/ops/start.sh: execute bit restored (was 644)" in rep["repaired"], rep
    assert "bin/tandem: execute bit restored (was 644)" in rep["repaired"], rep
    assert rep["problems"] == ["run/ops/hold.sh differs from src/ops/hold.sh: run the installer again"], rep
    assert open(os.path.join(d, "run", "ops", "hold.sh")).read() == "# an older hold.sh\n"      # not replaced


def test_secrets():
    d, src = _install()
    home = os.path.realpath(tempfile.mkdtemp(prefix="doctor-home-"))
    old = os.environ["HOME"]
    os.environ["HOME"] = home
    try:
        # the engine reads DIR/state/secrets.env; a file at the old default is reported, not moved
        stray = _w(os.path.join(home, ".qwen38-spark-engine", "secrets.env"), "QSE_METRICS_TOKEN=x\n", 0o600)
        rep = doctor.run(src)
        assert any("which is missing; a secrets file sits at " + stray in p for p in rep["problems"]), rep
        assert os.path.exists(stray) and not os.path.exists(os.path.join(d, "state", "secrets.env"))
        # the right file, readable by others: 600
        right = _w(os.path.join(d, "state", "secrets.env"), "QSE_METRICS_TOKEN=x\n", 0o644)
        rep = doctor.run(src)
        assert f"{right}: mode 644 -> 600" in rep["repaired"] and _mode(right) == 0o600, rep
    finally:
        os.environ["HOME"] = old


def test_pids_pause_and_marker():
    r = _checkout()
    data = tempfile.mkdtemp(prefix="doctor-chat-")
    os.environ["QSE_CHAT_DATA"] = data
    try:
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        _w(os.path.join(r, "logs", "engine.pid"), f"{dead.pid}\n")
        _w(os.path.join(data, "chatd.pid"), f"{os.getpid()}\n")          # alive, but not chatd
        _w(os.path.join(r, ".watchdog.off"), "")
        t = time.time() - 3 * 3600
        os.utime(os.path.join(r, ".watchdog.off"), (t, t))
        _w(os.path.join(r, "run", "setup-pending"), "HOST=0.0.0.0; reboot\n")
        rep = doctor.run(r)
        st = _states(rep)
        assert st["pids"] == ["repaired", "repaired"] and st["pause"] == ["repaired"], rep
        assert st["marker"] == ["repaired"], rep
        assert not os.path.exists(os.path.join(r, "logs", "engine.pid"))
        assert not os.path.exists(os.path.join(data, "chatd.pid"))
        assert not os.path.exists(os.path.join(r, ".watchdog.off"))
        assert open(os.path.join(r, "run", "setup-pending")).read() == "HOST=127.0.0.1\n"
        # left alone: a live pid of ours (a chatd), a fresh pause (a hold runs), a good marker
        live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "chatd"])
        try:
            _w(os.path.join(data, "chatd.pid"), f"{live.pid}\n")
            _w(os.path.join(r, ".watchdog.off"), "")
            rep = doctor.run(r)
            assert os.path.exists(os.path.join(data, "chatd.pid")) and os.path.exists(os.path.join(r, ".watchdog.off"))
            assert "a hold owns the board now (.watchdog.off): starts wait for it" in rep["problems"], rep
            assert _states(rep)["marker"] == ["ok"]
        finally:
            live.kill()
    finally:
        os.environ.pop("QSE_CHAT_DATA", None)


def test_config_python_and_disk():
    r = _checkout(serve_env="REPO=/x\nPY=/nowhere/python\nQSE_CHATD=off\nX=$UNSET_IN_TEST\n")
    rep = doctor.run(r)
    assert any(p.startswith("serve.env does not load:") and "UNSET_IN_TEST" in p for p in rep["problems"]), rep
    r = _checkout(serve_env="REPO=/x\nPY=/nowhere/python\nQSE_CHATD=on\n")
    rep = doctor.run(r)
    assert "the engine's interpreter: /nowhere/python does not exist" in rep["problems"], rep
    assert "chatd's interpreter: /nowhere/python does not exist" in rep["problems"], rep
    # ENG-413 WP6: TandemNode's chatd (QSE_CHATD=node) is not the engine's to check
    rep = doctor.run(_checkout(serve_env="REPO=/x\nPY=/nowhere/python\nQSE_CHATD=node\n"))
    assert not [p for p in rep["problems"] if p.startswith("chatd's interpreter")], rep
    rep = doctor.run(_checkout(), need_gb=1e9)
    assert any(p.startswith("the download needs 1e+09 GB and 60 GB left after it") for p in rep["problems"]), rep


def test_before_an_apply():
    import test_setup_apply as T
    from server.setup import apply
    with T.Box() as box:
        _w(os.path.join(box.root, "ops", "executables.txt"), "ops/start.sh\n")
        os.chmod(os.path.join(box.root, "ops", "start.sh"), 0o644)
        jid = T._job(T._sel())
        assert apply.Runner(jid, cmd=T.Recorder(), http=T._smoke_ok(), ops=box.ops).main() == 0
        j = apply.read_job(jid)
        assert "ops/start.sh: execute bit restored (was 644)" in j["doctor"]["repaired"], j.get("doctor")
        assert _mode(os.path.join(box.root, "ops", "start.sh")) == 0o755
        logs = [e["event"]["line"] for e in apply.events(jid) if e["event"]["type"] == "log"]
        assert "[doctor] repaired: ops/start.sh: execute bit restored (was 644)" in logs, logs
        assert not os.path.exists(os.path.join(box.root, "logs", "doctor.log"))     # the job keeps it


def test_the_lists_agree():
    listed = [ln.strip() for ln in open(os.path.join(ROOT, "ops", "executables.txt"))
              if ln.strip() and not ln.startswith("#")]
    # ENG-413 WP7: the engine's own files; TandemApp's vendored app/ keeps its modes and leaves at WP14
    assert not [f for f in listed if f.startswith("app/")], listed
    git = subprocess.run(["git", "-C", ROOT, "ls-files", "-s"], capture_output=True, text=True)
    if git.returncode == 0 and git.stdout:
        modes = [ln.split(None, 3)[3] for ln in git.stdout.splitlines() if ln.startswith("100755")]
        modes = [f for f in modes if not f.startswith("app/")]
        assert sorted(listed) == sorted(modes), set(listed) ^ set(modes)
    else:
        print("    (no git here: the manifest against the files' own modes)")
        assert all(os.access(os.path.join(ROOT, f), os.X_OK) for f in listed if os.path.exists(os.path.join(ROOT, f)))
    text = open(os.path.join(ROOT, "install.sh")).read()
    m = re.search(r'for s in ([^;]*); do install -m 755 "\$SRC/ops/\$s"', text)
    assert tuple(m.group(1).replace("\\", " ").split()) == doctor.COPIES


def test_tandem_doctor_on_an_install():
    if platform.system() != "Linux":
        print("    (install.sh runs on Linux only -- run it on the box)")
        return
    import test_install_control as I
    box = I.install("doctor", True, I.free_port())
    ops = os.path.join(box["dir"], "run", "ops")
    os.chmod(os.path.join(ops, "start.sh"), 0o644)
    os.chmod(os.path.join(box["dir"], "src", "ops", "use-profile.sh"), 0o644)
    r = subprocess.run(["bash", os.path.join(box["dir"], "bin", "tandem"), "doctor"], env=box["env"],
                       capture_output=True, text=True, timeout=120)
    out = r.stdout + r.stderr
    assert "repaired: run/ops/start.sh: execute bit restored (was 644)" in out, out
    assert "repaired: ops/use-profile.sh: execute bit restored (was 644)" in out, out
    assert _mode(os.path.join(ops, "start.sh")) == 0o755
    r = subprocess.run(["bash", os.path.join(box["dir"], "bin", "tandem"), "doctor", "--check", "--json"],
                       env=box["env"], capture_output=True, text=True, timeout=120)
    body = json.loads(r.stdout)
    assert body["repaired"] == [] and not any("execute bit" in p for p in body["problems"]), body


def _env_with_overlays(r: str, extra: str = "", local: str | None = None) -> str:
    """A serve.env that reads run/setup.env (and an install's local.env) as the release's does."""
    text = (f"REPO={r}\nPY={sys.executable}\nQSE_CHATD=off\nQSE_SECRETS={r}/state/secrets.env\n"
            f"QSE_BACKENDS=\n{extra}")
    if local:
        text += f'if [ -f {local} ]; then . {local}; fi\n'
    return text + 'if [ -f "$REPO/run/setup.env" ]; then . "$REPO/run/setup.env"; fi\n'


def test_qse_backends():
    home = os.environ["HOME"]
    db = _w(os.path.join(home, ".qwen38-spark-engine", "backends", "config.sqlite3"), "not a database\n", 0o600)
    st_db = os.stat(db)
    # off: empty (the release's line), and off in the overlay
    r = _checkout()
    _w(os.path.join(r, "ops", "serve.env"), _env_with_overlays(r))
    rep = doctor.run(r)
    assert _states(rep)["backends"] == ["ok"] and rep["ok"], rep
    _w(os.path.join(r, "run", "setup.env"), "QSE_BACKENDS='off'\n")
    assert _states(doctor.run(r))["backends"] == ["ok"]
    # a config database in serve.env: reported, with what to do; --check and the default repair keep it
    env_text = _env_with_overlays(r, 'QSE_BACKENDS="${HOME}/.qwen38-spark-engine/backends/config.sqlite3"  # relay\n')
    envf = _w(os.path.join(r, "ops", "serve.env"), env_text, 0o640)
    _w(os.path.join(r, "run", "setup.env"), "QSE_PARALLEL='4'\n")
    out = subprocess.run([sys.executable, "-m", "server.setup.doctor", "--check", "--root", r], cwd=ROOT,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 1, out.stdout
    line = next(ln for ln in out.stdout.splitlines() if "QSE_BACKENDS=" in ln)
    assert line.startswith(f"[doctor] problem: QSE_BACKENDS={db}: this engine relays to no other backend"), line
    for want in (f"tandem-app import-backends --from {db}", "tandem doctor --retire-backends",
                 "docs/operations.md#other-backends-moved-to-tandemapp", "(set in ops/serve.env)"):
        assert want in line, (want, line)
    rep = doctor.run(r)
    assert any(p.startswith(f"QSE_BACKENDS={db}:") for p in rep["problems"]), rep
    assert open(envf).read() == env_text
    # --check --retire-backends: a usage error, nothing changed
    out = subprocess.run([sys.executable, "-m", "server.setup.doctor", "--check", "--retire-backends", "--root", r],
                         cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert out.returncode == 2 and open(envf).read() == env_text, (out.returncode, out.stderr)
    # --retire-backends: the line becomes a comment under a dated note, the rest stays, the mode too
    out = subprocess.run([sys.executable, "-m", "server.setup.doctor", "--retire-backends", "--root", r],
                         cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stdout
    assert f"[doctor] repaired: ops/serve.env: QSE_BACKENDS={db} commented out (1 line, dated)" in out.stdout, out.stdout
    after = open(envf).read().split("\n")
    before = env_text.split("\n")
    i = next(k for k, ln in enumerate(before) if ln.startswith('QSE_BACKENDS="'))
    assert after[:i] + after[i + 2:] == before[:i] + before[i + 1:], after
    assert re.match(r"# \d{4}-\d{2}-\d{2}, tandem doctor --retire-backends \(ENG-413\): ", after[i]), after[i]
    assert after[i + 1] == "# " + before[i], after[i + 1]
    assert _mode(envf) == 0o640
    assert _states(doctor.run(r))["backends"] == ["ok"]
    # an install: run/setup.env and run/local.env set it too; both are repaired, serve.env's empty line stays
    d, src = _install()
    local = os.path.join(d, "run", "local.env")
    _w(os.path.join(d, "run", "ops", "serve.env"), _env_with_overlays(src, local=local))
    _w(local, "QSE_MODE=engine\nexport QSE_BACKENDS=/srv/backends.sqlite3\n")
    _w(os.path.join(src, "run", "setup.env"), f"QSE_BACKENDS='{db}'\n")
    rep = doctor.run(src)
    names = next(p for p in rep["problems"] if p.startswith("QSE_BACKENDS="))
    assert names.endswith("(set in src/run/setup.env, run/local.env)"), names
    rep = doctor.run(src, retire_backends=True)
    assert sorted(t.split(":")[0] for t in rep["repaired"]) == ["run/local.env", "src/run/setup.env"], rep
    assert rep["ok"], rep
    assert open(os.path.join(d, "run", "ops", "serve.env")).read().count("\nQSE_BACKENDS=\n") == 1
    assert "# export QSE_BACKENDS=/srv/backends.sqlite3" in open(local).read()
    # a file the doctor does not edit: reported and left
    r2 = _checkout()
    other = _w(os.path.join(r2, "ops", "site.env"), f"QSE_BACKENDS={db}\n")
    _w(os.path.join(r2, "ops", "serve.env"), _env_with_overlays(r2, f". {other}\n"))
    rep = doctor.run(r2, retire_backends=True)
    assert any("the doctor does not edit" in p for p in rep["problems"]) and not rep["repaired"], rep
    assert open(other).read() == f"QSE_BACKENDS={db}\n"
    # the database itself: never opened for writing, moved or removed
    st = os.stat(db)
    assert (st.st_size, st.st_mtime_ns, st.st_ino) == (st_db.st_size, st_db.st_mtime_ns, st_db.st_ino)


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            t0 = time.time()
            fn()
            print(f"  {name:45s} ok ({time.time() - t0:.1f} s)")
            passed += 1
    print(f"{passed} passed")
