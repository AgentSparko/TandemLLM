"""engine-ctl-v1, the contract of `tandem remote` (ENG-452): the CLI's answers validate, verbs.json is
the grammar, and the control code stands alone.

The test matrix:

  1  every verb's answer on a sandbox tree validates against its schema (contracts/engine-ctl-v1):
     status, mode get, settings get, plan and versions, logs, a line of logs --follow, doctor, the job
     of start, restart, mode set, settings apply and restore, unpair, ext list, token rotate (1.1;
     ext install and remove: tests/test_remote_ext.py, upgrade: tests/test_remote_upgrade.py); and
     every refusal against error.schema.json                    test_the_answers_validate
  2  verbs.json lists exactly the grammar's verbs, subcommands, options and arguments, every one with
     an answer schema that exists; the exit statuses are grammar's; every error code the code raises
     is listed                                                  test_verbs_json_is_the_grammar
  3  server/remote imports only the standard library and the engine's own server modules; no file
     of this contract, the code, its tests or its doc names the app tree or imports its modules (G5)
                                                                test_the_control_code_stands_alone
  4  bin/tandem remote, as install.sh writes it, runs the CLI in an install before the config loads:
     by hand, and as a forced command (--key-id, SSH_ORIGINAL_COMMAND); status answers on a config
     that does not load; the install's run/ops/serve.env and run/local.env are the ones read
                                                                test_bin_tandem_remote

Run: python tests/test_remote_contract.py
"""

from __future__ import annotations

import ast
import glob
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import remote_box as R  # noqa: E402
from server.api.validate import validate  # noqa: E402
from server.remote import grammar as G  # noqa: E402

CONTRACT = os.path.join(ROOT, "contracts", "engine-ctl-v1")


def _schema(name: str) -> dict:
    with open(os.path.join(CONTRACT, f"{name}.schema.json")) as fh:
        return json.load(fh)


def _ok(name: str, doc) -> None:
    errs = validate(doc, _schema(name))
    assert not errs, (name, errs, doc)


def test_the_answers_validate():
    b = R.make()
    try:
        # a hold's pause file: every job fails before it changes anything, no engine starts
        open(os.path.join(b["root"], ".watchdog.off"), "w").close()
        n = 0
        for args, schema in ((["status"], "status"), (["mode", "get"], "mode-get"),
                             (["settings", "get"], "settings-get"), (["settings", "get", "MAX_QUEUE"], "settings-get"),
                             (["settings", "plan", "MAX_QUEUE=4", "QWEN38_DEEP=16"], "settings-plan"),
                             (["settings", "versions"], "settings-versions"), (["logs"], "logs"),
                             (["logs", "--which", "launcher", "--lines", "3"], "logs"),
                             (["ext", "list"], "ext-list"), (["token", "rotate"], "token-rotate")):
            rc, ans = R.call(b, *args)
            assert rc == 0, ans
            _ok(schema, ans)
            n += 1
        rc, ans = R.call(b, "doctor")
        assert rc in (0, 1) and ans["ok"] == (rc == 0), ans
        _ok("doctor", ans)
        for args in (["start"], ["stop"], ["restart", "--when", "idle", "--deadline", "60"], ["mode", "set", "chat"],
                     ["settings", "apply", "MAX_QUEUE=4", "--confirm", None]):
            if None in args:
                _, p = R.call(b, "settings", "plan", "MAX_QUEUE=4")
                args = args[:-1] + [p["plan"]["confirm"]]
            rc, ans = R.call(b, *args)
            assert rc == 0, ans
            _ok("job", ans)
            job = R.wait_job(b)
            assert job["state"] == "failed" and "hold" in job["message"], job
            _ok("status", R.call(b, "status")[1])
            n += 1
        os.unlink(os.path.join(b["root"], ".watchdog.off"))
        # unpair, with a line of this tree's forced command
        os.makedirs(os.path.join(b["home"], ".ssh"))
        control = os.path.join(b["root"], "bin", "tandem")
        open(control, "w").close()
        with open(os.path.join(b["home"], ".ssh", "authorized_keys"), "w") as fh:
            fh.write(f'command="/bin/bash {control} remote --key-id k1",no-pty ssh-ed25519 AAAAC3Nz k1\n')
        rc, ans = R.call(b, "--key-id", "k1", ssh="unpair")
        assert rc == 0, ans
        _ok("unpair", ans)
        # refusals
        for kw, args in (({}, ["bogus"]), ({}, ["settings", "plan", "PORT=1"]), ({}, ["settings", "apply", "MAX_QUEUE=4"]),
                         ({"ssh": "status", "SSH_TTY": "/dev/pts/1"}, []), ({"ssh": "status; id"}, []),
                         ({}, ["settings", "restore", "7", "--confirm", "0123456789abcdef"])):
            rc, ans = R.call(b, *args, **kw)
            assert rc in (2, 3) and not ans["ok"], ans
            _ok("error", ans)
            n += 1
        # a line of logs --follow
        import subprocess
        with open(os.path.join(b["root"], "logs", "launcher.log"), "a") as fh:
            fh.write("x\n")
        p = subprocess.Popen([sys.executable, "-c", "import sys; from server.remote import cli; "
                              "from server.remote.paths import Tree; sys.exit(cli.run(sys.argv[2:], tree=Tree(sys.argv[1])))",
                              b["root"], "logs", "--follow", "--which", "launcher", "--lines", "1"],
                             cwd=ROOT, env=R.env(b), stdout=subprocess.PIPE, text=True)
        try:
            _ok("logs-line", json.loads(p.stdout.readline()))
        finally:
            p.terminate()
            p.wait(10)
    finally:
        R.cleanup(b)
    return f"{n + 3} answers valid"


def _codes_raised() -> set:
    out = set()
    for f in glob.glob(os.path.join(ROOT, "server", "remote", "*.py")):
        out |= set(re.findall(r'Refusal\(\s*"([a-z_]+)"', open(f).read()))
    return out


def test_verbs_json_is_the_grammar():
    with open(os.path.join(CONTRACT, "verbs.json")) as fh:
        doc = json.load(fh)
    assert (doc["ctl"], doc["version"]) == (G.CTL, G.CTL_VERSION)
    want = {}
    for v, subs in G.VERBS.items():
        for sub, (opts, pos) in subs.items():
            want[f"{v} {sub}" if sub else v] = (opts, pos)
    assert set(doc["verbs"]) == set(want), set(doc["verbs"]) ^ set(want)
    for name, (opts, pos) in want.items():
        e = doc["verbs"][name]
        assert e["options"] == opts and e["arguments"] == pos, name
        assert os.path.isfile(os.path.join(CONTRACT, e["answer"])), e["answer"]
    assert set(doc["planned"]) == set(G.PLANNED)
    assert {int(k) for k in doc["exit"]} == {G.EXIT_OK, G.EXIT_NOT_OK, G.EXIT_USAGE, G.EXIT_REFUSED, G.EXIT_BUSY,
                                              G.EXIT_INTERNAL}
    listed = {c for codes in doc["errors"].values() for c in codes}
    assert _codes_raised() <= listed, _codes_raised() - listed
    assert doc["line"]["max_chars"] == G.MAX_LINE and doc["line"]["max_tokens"] == G.MAX_TOKENS
    for f in os.listdir(CONTRACT):
        if f.endswith(".schema.json"):
            json.load(open(os.path.join(CONTRACT, f)))
    return f"{len(want)} verbs, {len(listed)} error codes"


def test_the_control_code_stands_alone():
    import sys as _s
    std = set(getattr(_s, "stdlib_module_names", ())) or {"os", "re", "sys", "json", "time"}
    for f in glob.glob(os.path.join(ROOT, "server", "remote", "*.py")):
        tree = ast.parse(open(f).read())
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            for m in mods:
                top = m.split(".")[0]
                assert top in std or top == "server" or top == "__future__", (f, m)
                assert not m.startswith(("server.app", "engine")), (f, m)
    texts = glob.glob(os.path.join(ROOT, "server", "remote", "*.py")) + glob.glob(os.path.join(CONTRACT, "*")) + \
        [os.path.join(ROOT, "docs", "remote-control.md"), os.path.join(ROOT, "server", "setup", "idle.py")] + \
        glob.glob(os.path.join(HERE, "test_remote_*.py")) + [os.path.join(HERE, "remote_box.py")]
    for f in texts:
        low = open(f).read().lower()
        app = "tandem" + "app"                           # spelt apart, so this file passes its own scan
        words = (app, "tandem" + "_app", "tandem" + "-app") + tuple(f"{a} {m}" for a in ("import", "from")
                                                                  for m in ("proxy", "chatd"))
        for word in words:
            assert word not in low, (f, word)
    return f"{len(texts)} files"


def test_bin_tandem_remote():
    import subprocess
    b = R.make(install=True)
    try:
        ctl = os.path.join(b["install"], "bin", "tandem")
        with open(os.path.join(b["install"], "run", "local.env"), "w") as fh:
            fh.write("QUEUE_TIMEOUT=17\n")

        def run(*args, **env):
            r = subprocess.run(["bash", ctl, "remote", *args], capture_output=True, text=True,
                               env=R.env(b, **env), timeout=60)
            return r.returncode, json.loads(r.stdout)
        rc, ans = run("status")
        assert rc == 0 and ans["install"] is True and ans["hook"] is True and ans["engine_version"] == "0.0.0-test", ans
        assert ans["version"] == G.CTL_VERSION, "the envelope's version is the contract's"
        _ok("status", ans)
        rc, ans = run("settings", "get", "QUEUE_TIMEOUT")
        assert ans["keys"][0]["value"] == "17" and ans["keys"][0]["source"] == "local", ans
        rc, ans = run("--key-id", "k1", SSH_ORIGINAL_COMMAND="settings versions",
                      SSH_CONNECTION="192.0.2.7 1 192.0.2.1 22")
        assert rc == 0 and ans["verb"] == "settings versions", ans
        rows = R.journal(b)
        assert rows[-1]["key"] == "k1" and rows[-1]["via"] == "ssh", rows
        # a config that does not load: status still answers, and says so
        with open(os.path.join(b["install"], "run", "local.env"), "a") as fh:
            fh.write("QUEUE_TIMEOUT=${NOT_SET_ANYWHERE}\n")
        rc, ans = run("status")
        assert rc == 0 and "config_error" in ans, ans
        rc, ans = run("bogus")
        assert rc == 2 and ans["error"]["code"] == "unknown_verb", ans
    finally:
        R.cleanup(b)
    return "by hand, forced, broken config"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
