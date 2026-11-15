"""The managed overlay of `tandem remote settings` and `mode` (engine-ctl-v1; ENG-452).

The test matrix:

  1  the schema is ops/serve.env's: every key it assigns or documents, no other; the install's, the
     weight profile's, the computed ones, QSE_BACKENDS and a secret-looking name are not managed;
     types come from the defaults; QSE_ALLOW_FROM alone is hot (1.9)     test_the_schema_is_serve_envs
  2  the fingerprint flag is server/diskcache.py's rule: its `_NOT_ROWS` prefixes and `_ROW_FILES`
     read from its source, and the arguments ops/start.sh builds from NV, HEAD, CKPT8, CKPT16,
     QSE_ENGINE_FLAGS, NARROW_ARM and FUSE_PROJ (each found in start.sh)  test_the_fingerprint_flag
  3  settings get: the value serve.env resolves and its source per layer (default, profile, setup,
     local, managed, mode)                                               test_settings_get_sources
  4  a plan writes nothing, diffs the effective values, names the restart, and a fingerprint key
     asks a second token; tokens bind to the base version; refusals: an unmanaged key, a value with
     a shell character, a path outside the install or into ~/.ssh, an unset of a key the overlay
     lacks, no change, an older serve.env without the hook               test_the_plan
  5  versions: a version's file, the manifest, make_current and restore, the file mode, render's
     quoting read back by bash                                           test_versions_round_trip
  6  the overlay is read after run/local.env and before the mode block; a pack that serves text only
     keeps VISION off whatever the overlay says                          test_the_overlay_order
  7  mode set writes QSE_MODE and QSE_EXT both, never an empty mode; engine writes an empty QSE_EXT,
     chat serve.env's own default or --ext; apply refuses without the token
                                                                         test_mode_set_writes_both_keys

CPU, no engine (the jobs run in tests/test_remote_lifecycle.py). Run: python tests/test_remote_overlay.py
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import remote_box as R  # noqa: E402
from server.remote import overlay as O  # noqa: E402
from server.remote.grammar import Refusal  # noqa: E402
from server.remote.paths import Tree  # noqa: E402

TEMPLATE = os.path.join(ROOT, "ops", "serve.env")


def _refusal(fn, code):
    try:
        fn()
    except Refusal as r:
        assert r.code == code, (code, r.code, str(r))
        return r
    raise AssertionError(f"no refusal, wanted {code}")


def test_the_schema_is_serve_envs():
    sch = O.schema(Tree(ROOT))
    text = open(TEMPLATE).read()
    for k in sch:
        assert re.search(rf"(?m)^\s*(?:#\s?)?{k}=", text), k
    assigned = set(re.findall(r"(?m)^\s*([A-Z][A-Z0-9_]*)=", text))
    assert assigned <= set(sch), assigned - set(sch)
    unmanaged = {k for k, e in sch.items() if not e["managed"]}
    assert unmanaged == set(O.MACHINE + O.WEIGHTS + O.DERIVED + O.FIXED), unmanaged
    for k in ("MAX_QUEUE", "QSE_PARALLEL", "QSE_MODE", "QSE_EXT", "VISION", "QWEN38_DEEP", "REP_PENALTY",
              "DEFAULT_MAX_TOKENS", "QSE_ROLES_DYNAMIC"):
        assert sch[k]["managed"], k
    assert sch["MAX_QUEUE"]["type"] == "integer" and sch["RESIDENT_GB"]["type"] == "number"
    assert sch["QSE_DISK_CACHE"]["type"] == "path" and sch["QSE_MODE"]["values"] == ["engine", "chat"]
    assert sch["QSE_EXT"]["mode_block"] and not sch["MAX_QUEUE"]["mode_block"]
    assert sch["REP_PENALTY"]["default"] is None and sch["REP_PENALTY"]["documented"] == "1.05"
    # 1.9 (ENG-456): the hot keys are the server's live ones, every other key needs a restart
    assert {k for k, e in sch.items() if e["restart"] == "hot"} == set(O.HOT) == {"QSE_ALLOW_FROM"}
    assert sch["QSE_ALLOW_FROM"]["type"] == "cidrs" and sch["QSE_ALLOW_FROM"]["managed"]
    assert "tandem" + "app" not in json.dumps({k: {x: y for x, y in e.items() if x not in ("default", "documented")}
                                          for k, e in sch.items()}).lower()
    return f"{len(sch)} keys, {len(sch) - len(unmanaged)} managed"


def test_the_fingerprint_flag():
    src = open(os.path.join(ROOT, "server", "diskcache.py")).read()
    tree = ast.parse(src)
    names = {n.targets[0].id for n in tree.body if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)}
    assert {"_NOT_ARITHMETIC", "_NOT_ROWS", "_ROW_FILES", "_ARG_FILES"} <= names
    not_rows, row_files = O._fingerprint_rules(ROOT)
    assert "QWEN38_DECIDER" in not_rows and "QWEN38_SHADOW" in not_rows and row_files == ("QSE_NH_GGUF",)
    assert "os.environ.items()" in src and 'k.startswith("QWEN38_") and not k.startswith(_NOT_ROWS)' in src
    # the arguments fingerprint_extra reads come from these keys in start.sh
    start = open(os.path.join(ROOT, "ops", "start.sh")).read()
    for key, flag in (("NV", "--nvfp4"), ("HEAD", "--fp8-head"), ("CKPT8", "--dflash2-ckpt"),
                      ("CKPT16", "--dflash2-ckpt16")):
        assert f'{flag} "${key}"' in start, key
    assert 'QWEN38_FUSE_PROJ="$FUSE_PROJ"' in start and "--narrow-arm off" in start and "NARROW_ARM" in start
    assert "MODEL_HEAD=($QSE_ENGINE_FLAGS" in start
    arg_files = ast.literal_eval(re.search(r"_ARG_FILES = (\(.*?\))", src).group(1))
    assert set(arg_files) == {"model", "nvfp4", "fp8_head", "head", "dflash2_ckpt", "dflash2_ckpt16"}
    sch = O.schema(Tree(ROOT))
    fp = {k for k, e in sch.items() if e["fingerprint"]}
    assert {"QWEN38_DEEP", "QWEN38_TREE_NODES", "FUSE_PROJ", "NARROW_ARM", "NV", "QWEN38_FN_TABLE"} <= fp
    assert not fp & {"QWEN38_DRAFT_TEMP", "QWEN38_DECIDER_MODELS", "QWEN38_NARRATOR_MODEL", "QWEN38_SHADOW_GB",
                     "QWEN38_PROBE_DIR", "QWEN38_CONDUCTOR_FIT", "MAX_QUEUE", "QSE_PARALLEL", "QSE_MODE"}
    return f"{len(fp)} fingerprint keys"


def test_settings_get_sources():
    b = R.make()
    try:
        R.home(b).__enter__()                   # cleanup() restores HOME
        t = Tree(b["root"])
        with open(os.path.join(b["root"], "ops", "active-profile"), "w") as fh:
            fh.write("flashnext-3.05\n")
        with open(t.setup_env, "w") as fh:
            fh.write("QSE_PARALLEL='4'\n")
        os.makedirs(t.managed_dir)
        n = O.add_version(t, {"MAX_QUEUE": "5"}, "test")
        O.make_current(t, n)
        rows = {r["key"]: r for r in O.settings(t)["keys"]}
        assert rows["MAX_QUEUE"]["value"] == "5" and rows["MAX_QUEUE"]["source"] == "managed", rows["MAX_QUEUE"]
        assert rows["QSE_PARALLEL"]["value"] == "4" and rows["QSE_PARALLEL"]["source"] == "setup"
        assert rows["MEM_CEILING_GIB"]["source"] == "profile" and rows["MEM_CEILING_GIB"]["value"] == "66"
        assert rows["QUEUE_TIMEOUT"]["source"] == "default" and rows["QUEUE_TIMEOUT"]["value"] == "240"
        assert rows["QSE_MODE"]["source"] == "mode" and rows["QSE_MODE"]["value"] == "engine"
        assert rows["CONDUCTOR"]["source"] == "mode" and rows["CONDUCTOR"]["value"] == "raw"
        assert rows["REP_PENALTY"]["set"] is False and rows["REP_PENALTY"]["value"] is None
        assert rows["PORT"]["managed"] is False and rows["PORT"]["why"] == "the install's"
        assert O.settings(t, ["MAX_QUEUE"])["keys"][0]["key"] == "MAX_QUEUE"
        _refusal(lambda: O.settings(t, ["NOPE"]), "unknown_key")
    finally:
        R.cleanup(b)
    return "6 layers"


def test_the_plan():
    b = R.make()
    try:
        R.home(b).__enter__()                   # cleanup() restores HOME
        t = Tree(b["root"])
        p = O.plan(t, {"MAX_QUEUE": "4"})
        assert not os.path.exists(t.managed) and not os.path.exists(t.managed_dir)
        assert p["effect"] == [{"key": "MAX_QUEUE", "from": "8", "to": "4", "was_set": True, "is_set": True,
                                "restart": "engine", "fingerprint": False}], p
        assert p["restart_needed"] and p["fingerprint_keys"] == [] and "confirm_fingerprint" not in p
        assert p["confirm"] == O.token(0, {"MAX_QUEUE": "4"}) and p["confirm"] != O.token(1, {"MAX_QUEUE": "4"})
        # a QWEN38_* key that was unset is a fingerprint change too, even at the engine's default
        p = O.plan(t, {"QWEN38_FN_LOOKUP": "1"})
        assert p["fingerprint_keys"] == ["QWEN38_FN_LOOKUP"] and p["effect"][0]["was_set"] is False, p
        assert p["confirm_fingerprint"] != p["confirm"] and "fingerprint" in p["warnings"][0]
        O.check_confirm(p, p["confirm"], p["confirm_fingerprint"])
        _refusal(lambda: O.check_confirm(p, p["confirm"], None), "confirm_required")
        _refusal(lambda: O.check_confirm(p, "0" * 16, p["confirm_fingerprint"]), "confirm_mismatch")
        # engine mode decides CONDUCTOR: the overlay's value has no effect, and the plan says so
        p = O.plan(t, {"CONDUCTOR": "shadow"})
        assert p["shadowed"] == ["CONDUCTOR"] and not p["restart_needed"], p
        sch = O.schema(t)
        for k, v, code in (("PORT", "9", "key_not_managed"), ("QSE_BACKENDS", "/x.db", "key_not_managed"),
                           ("NV", "/x", "key_not_managed"), ("MAX_QUEUE", "4;id", "bad_value"),
                           ("MAX_QUEUE", "four", "bad_value"), ("PATTERN_STOP", "$(id)", "bad_value"),
                           ("PATTERN_STOP", "a b", "bad_value"), ("QSE_MODE", "debug", "bad_value"),
                           ("RESIDENT_GB", "1e9", "bad_value"), ("QSE_DISK_CACHE", "/etc/x", "path_outside"),
                           ("QSE_DISK_CACHE", "${HOME}/.ssh/cache", "path_outside"),
                           ("QSE_DISK_CACHE", "${HOME}/elsewhere", "path_outside"),
                           ("QSE_DISK_CACHE", "${REPO}/../x", "path_outside"),
                           ("QSE_USAGE_LEDGER", "relative/x.db", "path_outside"),
                           ("PATTERN_STOP", "/etc/passwd", "path_outside")):
            _refusal(lambda: O.check_value(t, k, v, sch[k]), code)
        for k, v in (("QSE_DISK_CACHE", "off"), ("QSE_DISK_CACHE", "${HOME}/.qwen38-spark-engine/cache/d2"),
                     ("QSE_DISK_CACHE", "${REPO}/cache"), ("QWEN38_SHADOW_DISK", "auto"), ("RESIDENT_GB", "2.5"),
                     ("PATTERN_STOP", "256:32:8"), ("QWEN38_TREE_MS", "8:77.9,16:78.9")):
            O.check_value(t, k, v, sch[k])
        # a link out of the install is followed
        os.symlink("/etc", os.path.join(b["root"], "out"))
        _refusal(lambda: O.check_value(t, "QSE_DISK_CACHE", "${REPO}/out/x", sch["QSE_DISK_CACHE"]), "path_outside")
        _refusal(lambda: O.target_of(t, [], ["MAX_QUEUE"], {}), "not_in_overlay")
        _refusal(lambda: O.target_of(t, [("NOT_A_KEY", "1")], [], {}), "unknown_key")
        _refusal(lambda: O.plan(t, {}), "no_change")
        # a serve.env rendered before the overlay existed does not read it
        envf = os.path.join(b["root"], "ops", "serve.env")
        text = open(envf).read()
        start = text.index("# The managed overlay (engine-ctl-v1")
        end = text.index("# Empty: the directory decides")
        with open(envf, "w") as fh:
            fh.write(text[:start] + text[end:])
        _refusal(lambda: O.plan(t, {"MAX_QUEUE": "4"}), "hook_missing")
    finally:
        R.cleanup(b)
    return "plan, tokens, 15 value refusals"


def test_versions_round_trip():
    b = R.make()
    try:
        R.home(b).__enter__()                   # cleanup() restores HOME
        t = Tree(b["root"])
        n1 = O.add_version(t, {"MAX_QUEUE": "4", "QSE_DISK_CACHE": "${HOME}/.qwen38-spark-engine/c"}, "one")
        assert O.manifest(t)["current"] == 0 and not os.path.exists(t.managed)
        O.make_current(t, n1)
        body = open(t.managed).read()
        assert "MAX_QUEUE='4'" in body and 'QSE_DISK_CACHE="${HOME}/.qwen38-spark-engine/c"' in body, body
        assert oct(os.stat(t.managed).st_mode & 0o777) == "0o600"
        env, err = O.resolve(t, ["MAX_QUEUE", "QSE_DISK_CACHE"])
        assert err is None and env == {"MAX_QUEUE": "4",
                                       "QSE_DISK_CACHE": os.path.join(b["home"], ".qwen38-spark-engine", "c")}, env
        n2 = O.add_version(t, {"MAX_QUEUE": "6"}, "two")
        O.make_current(t, n2)
        O.mark_good(t)
        v = O.versions(t)
        assert (v["current"], v["good"], [x["n"] for x in v["versions"]]) == (2, 2, [1, 2]), v
        import hashlib
        assert v["versions"][1]["sha256"] == hashlib.sha256(open(os.path.join(t.managed_dir, "2.env"), "rb").read()).hexdigest()
        assert O.values_of(t, 1) == {"MAX_QUEUE": "4", "QSE_DISK_CACHE": "${HOME}/.qwen38-spark-engine/c"}
        O.make_current(t, 0)
        assert not os.path.exists(t.managed) and O.resolve(t, ["MAX_QUEUE"])[0]["MAX_QUEUE"] == "8"
        _refusal(lambda: O.values_of(t, 9), "unknown_version")
    finally:
        R.cleanup(b)
    return "2 versions, restore to 0"


def _installed(b: dict) -> str:
    """The sandbox's serve.env with an install's run/local.env line where install.sh puts it (its awk:
    just before the mode block's first line)."""
    envf = os.path.join(b["root"], "ops", "serve.env")
    text = open(envf).read()
    local = os.path.join(b["root"], "local.env")
    i = text.index(O.MODE_MARK)
    with open(envf, "w") as fh:
        fh.write(text[:i] + f"if [ -f '{local}' ]; then . '{local}'; fi\n\n" + text[i:])
    install = open(os.path.join(ROOT, "install.sh")).read()
    assert "/^# OPS-260: the mode \\(QSE_MODE above\\), applied last/ && !lp" in install
    return local


def test_the_overlay_order():
    b = R.make()
    try:
        R.home(b).__enter__()                   # cleanup() restores HOME
        t = Tree(b["root"])
        local = _installed(b)
        with open(local, "w") as fh:
            fh.write("MAX_QUEUE=3\nQUEUE_TIMEOUT=7\n")
        n = O.add_version(t, {"MAX_QUEUE": "5", "QSE_MODE": "engine", "CONDUCTOR": "on"}, "order")
        O.make_current(t, n)
        env, _ = O.resolve(t, ["MAX_QUEUE", "QUEUE_TIMEOUT", "CONDUCTOR", "VISION", "QSE_VISION_ASKED"])
        assert env["MAX_QUEUE"] == "5" and env["QUEUE_TIMEOUT"] == "7", env      # managed wins over local
        assert env["CONDUCTOR"] == "raw", env                                    # the mode block decides last
        # SPD-167: a text-only pack keeps the tower off
        prof = os.path.join(b["root"], "ops", "profiles", "textonly.env")
        with open(prof, "w") as fh:
            fh.write("QSE_PACK_VISION=off\n")
        with open(os.path.join(b["root"], "ops", "active-profile"), "w") as fh:
            fh.write("textonly\n")
        n = O.add_version(t, {"VISION": "on"}, "vision")
        O.make_current(t, n)
        env, _ = O.resolve(t, ["VISION", "QSE_VISION_ASKED"])
        assert env == {"VISION": "off", "QSE_VISION_ASKED": "on"}, env
        O.make_current(t, 0)
        env, _ = O.resolve(t, ["VISION", "QSE_VISION_ASKED"])
        assert env == {"VISION": "off", "QSE_VISION_ASKED": "on"}, env
    finally:
        R.cleanup(b)
    return "local < managed < mode; VISION"


def test_mode_set_writes_both_keys():
    b = R.make()
    try:
        # a hold's fresh pause file: each job fails before it changes anything, no engine starts
        open(os.path.join(b["root"], ".watchdog.off"), "w").close()
        rc, ans = R.call(b, "mode", "set", "chat")
        assert rc == 0 and ans["ok"], ans
        assert ans["job"]["overlay"]["values"] == {"QSE_MODE": "chat", "QSE_EXT": _chat_default(b)}, ans
        assert ans["job"]["when"] == {"mode": "idle", "deadline_s": 600}, ans
        job = R.wait_job(b)
        assert job["state"] == "failed" and "a hold owns the board" in job["message"], job
        assert O.manifest(Tree(b["root"]))["versions"] == []
        rc, ans = R.call(b, "mode", "set", "engine", "--when", "now")
        assert ans["job"]["overlay"]["values"] == {"QSE_MODE": "engine", "QSE_EXT": ""}, ans
        R.wait_job(b)
        rc, ans = R.call(b, "mode", "set", "chat", "--ext", "pkg.ext")
        assert ans["job"]["overlay"]["values"] == {"QSE_MODE": "chat", "QSE_EXT": "pkg.ext"}, ans
        R.wait_job(b)
        rc, ans = R.call(b, "mode", "set", "engine", "--ext", "pkg.ext")
        assert rc == 2 and ans["error"]["code"] == "bad_value", ans
        rc, ans = R.call(b, "mode", "get")
        assert ans["mode"] == "engine" and ans["inferred"] is True, ans
        rc, ans = R.call(b, "settings", "apply", "MAX_QUEUE=4")
        assert rc == 3 and ans["error"]["code"] == "confirm_required" and ans["error"]["plan"]["confirm"], ans
        rc, ans = R.call(b, "settings", "restore", "0")
        assert rc == 3 and ans["error"]["code"] == "no_change", ans
        assert not os.listdir(os.path.join(b["root"], "proc")), "no engine was started"
    finally:
        R.cleanup(b)
    return "chat, engine, --ext"


def _chat_default(b: dict) -> str:
    """serve.env's own chat-mode extension, read from the sandbox's file."""
    probe = os.path.join(b["root"], "probe.env")
    with open(probe, "w") as fh:
        fh.write("QSE_MODE=chat\n")
    r = subprocess.run(["bash", "-c", 'set -a; . "$1" >/dev/null 2>&1; printf %s "$QSE_EXT"', "-",
                        os.path.join(b["root"], "ops", "serve.env")], capture_output=True, text=True,
                       env=R.env(b, QSE_MANAGED_ENV=probe))
    assert r.stdout, r
    return r.stdout


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
