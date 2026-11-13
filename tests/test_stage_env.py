"""A stage's serve.env keys (SRV-817, QA SRV-818): ops/start.sh with ops/engines.sh `stage_layer`, run for real
against a fake engine in a sandbox (tests/test_ops_scripts.py's box: fake curl and process table, no real engine),
and bin/tandem's status line as install.sh writes it (tests/test_chatd_lifecycle.py's box).

The test matrix:

  1  without TANDEM_STAGE the engine's command line has no stage flag (the box's engine as before)
                                                         test_no_stage_key_no_stage_flag
  2  a front: TANDEM_STAGE, TANDEM_LINK_LISTEN and TANDEM_LINK_KEY become --stage, --link-listen,
     --link-key-file and --stage-caches off; the engine alone (--conductor raw); --disk-cache off stays
     the last flag; with caches on the front's three keys become their flags; a tail dials with
     TANDEM_LINK_PEER; the key file's content is printed nowhere
                                                         test_a_front_and_a_tail_get_their_flags
  3  each bad combination is refused in one line before an engine starts: a link key without a stage, a
     range that is not LO:HI, a stage without a link address or with both, a wildcard or a name for
     an address, the key itself in place of its path, a key file that is not there, a front with a
     disk tier, the front's keys on a tail or without caches, more than one slot without caches,
     caches neither on nor off, a stage in setup mode     test_bad_combinations_are_refused_in_one_line
  4  a stage that exits before /health ends start.sh's wait at once with the log's tail
                                                         test_a_stage_that_exits_ends_the_wait
  5  bin/tandem status names the stage, its layers and its link from /health
                                                         test_tandem_status_names_the_stage

Run: python tests/test_stage_env.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import test_ops_scripts as T  # noqa: E402

KEY_TEXT = "stage-link-key-" + "q8" * 20


def _key() -> str:
    d = tempfile.mkdtemp(prefix="stage-env-")
    p = os.path.join(d, "link.key")
    with open(os.open(p, os.O_WRONLY | os.O_CREAT, 0o600), "w") as f:
        f.write(KEY_TEXT + "\n")
    return p


def _argv(marks: str) -> list[str]:
    return next(line for line in marks.splitlines() if line.startswith("engine ")).split()


def _start(extra: str, **kw):
    return T._start_with_fake_engine("0", extra, **kw)


def test_no_stage_key_no_stage_flag():
    r, marks = _start("")
    argv = _argv(marks)
    assert r.returncode == 0 and "[start] stage:" not in r.stdout, r.stdout + r.stderr
    for flag in ("--stage", "--link-listen", "--link-peer", "--link-key-file", "--stage-caches"):
        assert flag not in argv, (flag, argv)
    assert argv[-2:] == ["--disk-cache", "off"], argv
    return "no TANDEM_STAGE: no stage flag"


def test_a_front_and_a_tail_get_their_flags():
    key = _key()
    r, marks = _start(f"TANDEM_STAGE=0:12\nTANDEM_LINK_LISTEN=10.0.0.1:9100\nTANDEM_LINK_KEY={key}\nCONDUCTOR=on\n")
    assert r.returncode == 0, r.stdout + r.stderr
    argv = _argv(marks)
    i = argv.index("--stage")
    assert argv[i:i + 8] == ["--stage", "0:12", "--link-listen", "10.0.0.1:9100", "--link-key-file", key,
                             "--stage-caches", "off"], argv
    assert argv[-2:] == ["--disk-cache", "off"] and argv[argv.index("--conductor") + 1] == "raw", argv
    assert "[start] stage: the front, layers 0:12, link-listen 10.0.0.1:9100, caches off, slots 1" in r.stdout
    assert "mode: engine" in r.stdout and KEY_TEXT not in r.stdout + r.stderr + marks
    d = tempfile.mkdtemp(prefix="stage-env-dir-")
    r, marks = _start(f"TANDEM_STAGE=0:12\nTANDEM_LINK_LISTEN=10.0.0.1:9100\nTANDEM_LINK_KEY={key}\n"
                      f"TANDEM_STAGE_CACHES=on\nQSE_PARALLEL=8\nTANDEM_STAGE_SNAPSHOTS_GB=2.5\n"
                      f"TANDEM_STAGE_CACHE_DIR={d}\nTANDEM_STAGE_CACHE_GB=16\n")
    assert r.returncode == 0, r.stdout + r.stderr
    argv = _argv(marks)
    i = argv.index("--stage-caches")
    assert argv[i:i + 8] == ["--stage-caches", "on", "--stage-snapshots-gb", "2.5", "--stage-cache-dir", d,
                             "--stage-cache-gb", "16"], argv
    assert "caches on, slots 8" in r.stdout and argv[-2:] == ["--disk-cache", "off"]
    r, marks = _start(f"TANDEM_STAGE=12:48\nTANDEM_LINK_PEER=[fd00::2]:9100\nTANDEM_LINK_KEY={key}\n"
                      "TANDEM_STAGE_CACHES=on\nQSE_DISK_CACHE=/srv/tail-disk\n")
    assert r.returncode == 0, r.stdout + r.stderr
    argv = _argv(marks)
    i = argv.index("--stage")
    assert argv[i:i + 4] == ["--stage", "12:48", "--link-peer", "[fd00::2]:9100"], argv
    assert argv[-2:] == ["--disk-cache", "/srv/tail-disk"], argv
    return "front, front with caches, tail: their flags; --disk-cache last; the key in no output"


def test_bad_combinations_are_refused_in_one_line():
    key = _key()
    link = f"TANDEM_LINK_LISTEN=10.0.0.1:9100\nTANDEM_LINK_KEY={key}\n"
    cases = (
        (f"TANDEM_LINK_KEY={key}\n", "TANDEM_LINK_KEY needs TANDEM_STAGE"),
        ("TANDEM_STAGE_CACHES=on\n", "TANDEM_STAGE_CACHES needs TANDEM_STAGE"),
        ("TANDEM_STAGE=12:12\n" + link, "LO:HI with LO < HI"),
        ("TANDEM_STAGE=a:b\n" + link, "LO:HI with LO < HI"),
        (f"TANDEM_STAGE=12:48\nTANDEM_LINK_KEY={key}\n", "the tail stage needs TANDEM_LINK_LISTEN or TANDEM_LINK_PEER"),
        ("TANDEM_STAGE=0:12\nTANDEM_LINK_PEER=10.0.0.2:9100\n" + link, "one of TANDEM_LINK_LISTEN and TANDEM_LINK_PEER, not both"),
        (f"TANDEM_STAGE=0:12\nTANDEM_LINK_LISTEN=0.0.0.0:9100\nTANDEM_LINK_KEY={key}\n", "every interface"),
        (f"TANDEM_STAGE=12:48\nTANDEM_LINK_PEER=front.example:9100\nTANDEM_LINK_KEY={key}\n", "an IP literal"),
        (f"TANDEM_STAGE=0:12\nTANDEM_LINK_LISTEN=10.0.0.1:9100\nTANDEM_LINK_KEY={KEY_TEXT}\n", "never the key itself"),
        ("TANDEM_STAGE=0:12\nTANDEM_LINK_LISTEN=10.0.0.1:9100\nTANDEM_LINK_KEY=/nonexistent/link.key\n", "names no file"),
        ("TANDEM_STAGE=0:12\n" + link.replace(f"TANDEM_LINK_KEY={key}\n", ""), "needs TANDEM_LINK_KEY"),
        ("TANDEM_STAGE=0:12\nQSE_DISK_CACHE=/srv/disk\n" + link, "a front stage takes no disk tier: QSE_DISK_CACHE=off"),
        ("TANDEM_STAGE=12:48\nTANDEM_STAGE_CACHES=on\nTANDEM_STAGE_CACHE_DIR=/srv/f\n" + link,
         "TANDEM_STAGE_CACHE_DIR is the front stage's"),
        ("TANDEM_STAGE=0:12\nTANDEM_STAGE_SNAPSHOTS_GB=2\n" + link, "TANDEM_STAGE_SNAPSHOTS_GB needs TANDEM_STAGE_CACHES=on"),
        ("TANDEM_STAGE=0:12\nTANDEM_STAGE_CACHES=on\nTANDEM_STAGE_CACHE_GB=lots\n" + link, "TANDEM_STAGE_CACHE_GB takes GiB"),
        ("TANDEM_STAGE=0:12\nQSE_PARALLEL=8\n" + link, "a stage without caches serves one slot: QSE_PARALLEL=1"),
        ("TANDEM_STAGE=0:12\nTANDEM_STAGE_CACHES=yes\n" + link, "TANDEM_STAGE_CACHES is on or off"),
    )
    for extra, why in cases:
        r, marks = _start(extra)
        lines = [ln for ln in r.stdout.splitlines() if "REFUSING" in ln]
        assert r.returncode == 2 and len(lines) == 1 and why in lines[0], (extra, why, r.stdout + r.stderr)
        assert "engine " not in marks and KEY_TEXT not in r.stdout + r.stderr, (extra, marks)

    def setup_marker(repo):
        os.makedirs(os.path.join(repo, "run"), exist_ok=True)
        with open(os.path.join(repo, "run", "setup-pending"), "w") as fh:
            fh.write("HOST=127.0.0.1\n")
    r, marks = _start("TANDEM_STAGE=0:12\n" + link, prepare=setup_marker)
    assert r.returncode == 2 and "TANDEM_STAGE serves a configured engine, not setup mode" in r.stdout, r.stdout
    return f"{len(cases) + 1} combinations, each refused in one line before any engine"


def test_a_stage_that_exits_ends_the_wait():
    key = _key()
    repo = T._box(["start.sh"])
    engine = os.path.join(repo, "fake-engine.sh")
    with open(engine, "w") as fh:
        fh.write("#!/bin/bash\necho '[stage] REFUSING to start: the other stage refused the link (slots)'\nexit 1\n")
    os.chmod(engine, 0o755)
    env_file = os.path.join(repo, "ops", "serve.env")
    body = open(env_file).read().replace("PY=/bin/false", f"PY={engine}").replace("PORT=8000", "PORT=18997")
    with open(env_file, "w") as fh:
        fh.write(body + "SERVED_MODEL=t\nMAX_LEN=64\nDEFAULT_MAX_TOKENS=8\nREASONING_FORMAT=tags\n"
                 "REASONING_EFFORT=medium\nCORPUS=x\nCACHE_GB=0\nREQUEST_TIMEOUT=1\nMAX_QUEUE=1\nQUEUE_TIMEOUT=1\n"
                 "FUSE_PROJ=0\nQWEN38_DF2_TREE_MODE=paths\nQWEN38_TREE_ALIAS_STATE=0\n"
                 'QSE_ENGINE_FLAGS="--model /p --drafter mtp"\n'
                 f"TANDEM_STAGE=12:48\nTANDEM_LINK_PEER=10.0.0.1:9100\nTANDEM_LINK_KEY={key}\n")
    t0 = time.monotonic()
    r = subprocess.run(["bash", os.path.join(repo, "ops", "start.sh")], capture_output=True, text=True,
                       env=T._env(repo), timeout=120)
    took = time.monotonic() - t0
    assert r.returncode == 1 and "the tail stage exited before /health" in r.stdout, r.stdout + r.stderr
    assert "refused the link (slots)" in r.stdout and took < 30, (took, r.stdout)
    return f"the tail's exit ended the wait after {took:.1f} s with the log's tail"


def test_tandem_status_names_the_stage():
    import test_chatd_lifecycle as C
    d, marks, env = C._tandem()
    health = {"status": "ok", "stage": {"shape": 1, "role": "front", "range": "0:12", "link": {
        "state": "down", "epoch": 2, "reason": "refused (slots)"}}}
    C._write(os.path.join(d, "fakebin", "curl"),
             f"#!/bin/bash\n[ -f {d}/up ] || exit 7\ncase \"$*\" in *health*) echo '{json.dumps(health)}' ;; "
             "*) echo '{}' ;; esac\n")
    C._write(os.path.join(d, "run", "local.env"), f"TANDEM_STAGE=0:12\nPY={sys.executable}\n", 0o644)
    open(os.path.join(d, "up"), "w").close()
    r = C._ctl(d, env, "status")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[tandem] stage: the front, layers 0:12, link down (epoch 2), refused (slots)" in r.stdout, r.stdout
    os.remove(os.path.join(d, "run", "local.env"))
    C._write(os.path.join(d, "run", "local.env"), f"PY={sys.executable}\n", 0o644)
    r = C._ctl(d, env, "status")
    assert "[tandem] stage:" not in r.stdout, r.stdout
    return "status: the stage's role, layers and link; none without TANDEM_STAGE"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
