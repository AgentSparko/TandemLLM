"""`tandem remote`: the engine's control verbs for a controller that reaches the box over SSH.

    command="/bin/bash DIR/bin/tandem remote --key-id ID",no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding ssh-ed25519 AAAA...

sshd runs that forced command for the key whatever the client asks; the client's command arrives in
SSH_ORIGINAL_COMMAND and is the only input. Run by hand, the command is this program's arguments
(`bin/tandem remote status`). Either way it is split without a shell (server/remote/grammar.py),
answered with one JSON document on stdout (JSON lines for `logs --follow`) and an exit status, and
journaled (server/remote/journal.py). A session that holds a terminal, a forwarded agent or X11
display, or asks for a shell, is refused before any verb runs.

The contract is contracts/engine-ctl-v1 (its README, verbs.json and a schema per answer).
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time

from server.remote import grammar, journal, keys, lifecycle, overlay
from server.remote.grammar import (CTL, CTL_VERSION, EXIT_BUSY, EXIT_INTERNAL, EXIT_NOT_OK, EXIT_OK,
                                   EXIT_REFUSED, Refusal)
from server.remote.paths import Tree

#: one line of a doctor check id -> what to do about a problem it found
DOCTOR_FIX = {
    "modes": "tandem doctor (without --check) restores the execute bits",
    "copies": "run the installer again (install.sh): it rewrites run/ops from src/ops",
    "control": "run the installer again: it writes bin/tandem",
    "config": "fix the named line of serve.env, run/setup.env, run/local.env or run/managed.env "
              "(tandem remote settings restore puts an older overlay back)",
    "secrets": "tandem doctor sets the mode to 600; move a stray secrets file where the engine reads it",
    "python": "run the installer again: it rebuilds the venv",
    "pids": "tandem doctor removes the stale pid file",
    "pause": "wait for the hold to end; an old pause file is removed by tandem doctor",
    "marker": "tandem doctor rewrites run/setup-pending with 127.0.0.1",
    "backends": "move the backends to the controller, then tandem doctor --retire-backends",
    "disk": "free disk space before the download",
}


def answer(call_name: str, body: dict) -> dict:
    return {"ctl": CTL, "version": CTL_VERSION, "verb": call_name, "ok": True, **body}


def error(call_name: str | None, ref: Refusal) -> dict:
    return {"ctl": CTL, "version": CTL_VERSION, "verb": call_name, "ok": False, "error": ref.body()}


def emit(doc: dict) -> None:
    sys.stdout.write(json.dumps(doc, sort_keys=True) + "\n")
    sys.stdout.flush()


# ------------------------------------------------------------------------------------- the session
def caller(env: dict) -> dict:
    """What is known of the caller: {via, from?}."""
    info = {"via": "ssh" if env.get("SSH_CONNECTION") or env.get("SSH_CLIENT") else "local"}
    if env.get("SSH_CONNECTION"):
        info["from"] = env["SSH_CONNECTION"].split()[0][:64]
    return info


def session(env: dict, argv: list[str], info: dict) -> list[str]:
    """The command's tokens (`info` gains the key id), or a Refusal."""
    key_id = None
    args = list(argv)
    if args[:1] == ["--key-id"]:
        if len(args) < 2 or not grammar.KEY_ID_RE.match(args[1]):
            raise Refusal("bad_command", "--key-id takes an id (letters, digits, . _ : -)", EXIT_REFUSED)
        key_id, args = args[1], args[2:]
        info["key"] = key_id
    if "SSH_ORIGINAL_COMMAND" in env:
        if args:
            raise Refusal("bad_command", "the forced command takes --key-id only", EXIT_REFUSED)
        for var, what in (("SSH_TTY", "a terminal"), ("SSH_AUTH_SOCK", "a forwarded agent"),
                          ("DISPLAY", "a forwarded X11 display")):
            if env.get(var):
                code = "pty_refused" if var == "SSH_TTY" else "forward_refused"
                raise Refusal(code, f"this session holds {what}: this key runs `tandem remote` verbs "
                                    f"only (the key's line needs no-pty and no forwarding)", EXIT_REFUSED)
        return grammar.split(env["SSH_ORIGINAL_COMMAND"])
    if info["via"] == "ssh" and key_id is not None:
        # a forced command with no client command: the client asked for a shell
        raise Refusal("no_command", "no command: this key runs `tandem remote` verbs only, never a shell",
                      EXIT_REFUSED)
    if not args:
        raise Refusal("no_command", "no command: tandem remote status | start | stop | restart | mode | "
                                    "settings | logs | doctor | unpair", EXIT_REFUSED)
    return grammar.split(" ".join(_quote(a) for a in args))


def _quote(a: str) -> str:
    import shlex
    return shlex.quote(a)


# ------------------------------------------------------------------------------------- the verbs
def _pid(tree: Tree) -> int | None:
    try:
        with open(tree.pid_file) as fh:
            pid = int(fh.read().strip() or 0)
        return pid if pid > 0 and lifecycle._alive(pid) else None
    except (OSError, ValueError):
        return None


def v_status(tree: Tree, call) -> tuple[dict, int]:
    from server import engine_api
    env, err = overlay.resolve(tree, ["PORT", "QSE_MODE", "QSE_EXT", "QSE_PROFILE_NAME", "SERVED_MODEL"])
    port = int(env.get("PORT") or 8000)
    code, h = lifecycle._http(f"http://127.0.0.1:{port}/health", timeout=3.0)
    pid = _pid(tree)
    if code == 200:
        state = "healthy"
    elif code == 503:
        state = "draining"
    elif code == 500 and h.get("status") == "failed":
        state = "failed"
    elif pid:
        state = "loading"
    else:
        state = "down"
    try:
        with open(os.path.join(tree.root, "VERSION")) as fh:
            version = fh.read().strip()
    except OSError:
        version = None
    status_file = {}
    try:
        with open(os.path.join(tree.logs, "engine-status.json")) as fh:
            d = json.load(fh)
        status_file = {k: d.get(k) for k in ("state", "since", "reason") if d.get(k) is not None}
    except (OSError, ValueError):
        pass
    exts = h.get("extensions") if code == 200 else None
    body = {
        "engine": {"state": state, "pid": pid, "port": port, "health_code": code or None,
                   "engine_api": h.get("engine_api") if code == 200 else None,
                   "version": h.get("version") if code == 200 else None,
                   "model": h.get("model") if code == 200 else None,
                   "running_mode": (("chat" if exts else "engine") if isinstance(exts, list) else None),
                   "stopped_on_purpose": os.path.exists(tree.stopped)},
        "mode": env.get("QSE_MODE"),
        "profile": env.get("QSE_PROFILE_NAME") or None,
        "version": version,
        "engine_api": engine_api.VERSION,
        "install": tree.install is not None,
        "overlay": overlay.manifest_brief(tree),
        "hook": overlay.hook_present(tree),
        "job": lifecycle.brief(lifecycle.latest(tree)),
    }
    if status_file:
        body["status_file"] = status_file
    if err:
        body["config_error"] = err
    return body, EXIT_OK


def _job_answer(job: dict) -> tuple[dict, int]:
    return {"job": lifecycle.brief(job), "follow": "tandem remote status"}, EXIT_OK


def _create(tree: Tree, kind: str, call, target: dict | None = None, note: str | None = None):
    try:
        return _job_answer(lifecycle.create(tree, kind, call.opts.get("--when"), call.opts.get("--deadline"),
                                            target=target, note=note))
    except lifecycle.Busy as b:
        raise Refusal("busy", "another lifecycle job is acting; tandem remote status shows it", EXIT_BUSY,
                      job=b.job) from None


def v_start(tree, call):
    return _create(tree, "start", call)


def v_stop(tree, call):
    return _create(tree, "stop", call)


def v_restart(tree, call):
    if "--deadline" in call.opts and "--when" not in call.opts:
        raise Refusal("bad_value", "--deadline is the wait for idle: it goes with --when idle or at=")
    return _create(tree, "restart", call)


def _apply(tree: Tree, call, kind: str, new: dict, note: str, need_confirm: bool = True):
    p = overlay.plan(tree, new)
    if need_confirm or "confirm_fingerprint" in p:
        overlay.check_confirm(p, call.opts.get("--confirm") or (None if need_confirm else p["confirm"]),
                              call.opts.get("--confirm-fingerprint"))
    if "--when" not in call.opts:
        call.opts["--when"] = {"mode": "idle"}
    body, rc = _create(tree, kind, call, target=new, note=note)
    body["plan"] = p
    return body, rc


def v_mode_get(tree, call):
    env, err = overlay.resolve(tree, ["QSE_MODE", "QSE_EXT"])
    rows = {r["key"]: r for r in overlay.settings(tree, ["QSE_MODE", "QSE_EXT"])["keys"]}
    return {"mode": env.get("QSE_MODE"), "ext": env.get("QSE_EXT") or None,
            "source": rows["QSE_MODE"]["source"], "inferred": rows["QSE_MODE"]["source"] == "mode",
            **({"config_error": err} if err else {})}, EXIT_OK


def v_mode_set(tree, call):
    mode = call.opts["mode"]
    base = overlay.values_of(tree, overlay.manifest(tree)["current"])
    new = dict(base, QSE_MODE=mode)
    if mode == "engine":
        if "--ext" in call.opts:
            raise Refusal("bad_value", "--ext goes with mode set chat")
        new["QSE_EXT"] = ""
    elif "--ext" in call.opts:
        new["QSE_EXT"] = call.opts["--ext"]
    else:
        # the extension chat mode runs when nothing names one: serve.env's own default, written down
        probe = {k: v for k, v in new.items() if k != "QSE_EXT"}
        import tempfile
        fd, cand = tempfile.mkstemp(prefix="managed-mode-", suffix=".env")
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(overlay.render(probe, 0, "mode"))
            env, _ = overlay.resolve(tree, ["QSE_EXT"], overlay=cand)
        finally:
            os.unlink(cand)
        if not env.get("QSE_EXT"):
            raise Refusal("bad_value", "serve.env names no extension for chat mode: give --ext NAME")
        new["QSE_EXT"] = env["QSE_EXT"]
    overlay.check_value(tree, "QSE_EXT", new["QSE_EXT"], overlay.schema(tree)["QSE_EXT"])
    return _apply(tree, call, "mode", new, f"mode {mode}", need_confirm=False)


def v_settings_get(tree, call):
    return overlay.settings(tree, call.pos or None), EXIT_OK


def v_settings_plan(tree, call):
    base = overlay.values_of(tree, overlay.manifest(tree)["current"])
    new = overlay.target_of(tree, call.sets, call.unsets, base)
    return {"plan": overlay.plan(tree, new)}, EXIT_OK


def v_settings_apply(tree, call):
    base = overlay.values_of(tree, overlay.manifest(tree)["current"])
    new = overlay.target_of(tree, call.sets, call.unsets, base)
    return _apply(tree, call, "settings", new, "settings apply")


def v_settings_versions(tree, call):
    return overlay.versions(tree), EXIT_OK


def v_settings_restore(tree, call):
    n = call.opts["version"]
    new = overlay.values_of(tree, n)
    if call.opts.get("--confirm") is None:
        p = overlay.plan(tree, new)
        raise Refusal("confirm_required", f"restore {n} needs --confirm with this plan's token",
                      EXIT_REFUSED, plan=p)
    return _apply(tree, call, "restore", new, f"restore of version {n}")


def v_doctor(tree, call):
    from server.setup import doctor
    body = doctor.run(tree.root, repair=False, log=False)
    findings = [{"code": f"doctor.{c['id']}", "state": c["state"], "text": c["text"],
                 "fix": DOCTOR_FIX.get(c["id"], "run tandem doctor")}
                for c in body["checks"] if c["state"] != "ok"]
    checks = sorted({c["id"] for c in body["checks"]})
    return {"healthy": body["ok"], "findings": findings, "checks": checks}, \
        EXIT_OK if body["ok"] else EXIT_NOT_OK


def v_unpair(tree, call, info, env):
    control = tree.control or os.path.join(tree.base, "bin", "tandem")
    return keys.unpair(control, info.get("key"), env.get("SSH_USER_AUTH")), EXIT_OK


def _log_file(tree: Tree, which: str) -> str:
    return tree.engine_log if which == "engine" else tree.launcher_log


def _clean(line: str) -> str:
    from server.api.redact import redact
    return redact(line.rstrip("\n")[:4000])[0]


def v_logs(tree, call):
    which = call.opts.get("--which", "engine")
    n = call.opts.get("--lines", 100)
    path = _log_file(tree, which)
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - (n * 4096)))
            lines = fh.read().decode("utf-8", "replace").splitlines()[-n:]
    except OSError:
        lines = []
    return {"which": which, "file": os.path.relpath(path, tree.base), "lines": [_clean(x) for x in lines]}, EXIT_OK


def follow(tree: Tree, call, write=None, stop=None, poll: float = 0.5) -> int:
    """`logs --follow`: the last lines, then each new one, a JSON document a line, until the client
    goes (a broken pipe or SIGHUP) or `stop()`; a log replaced by a new start is read from its top."""
    write = write or emit
    which = call.opts.get("--which", "engine")
    path = _log_file(tree, which)
    body, _ = v_logs(tree, call)
    seq = 0
    for ln in body["lines"]:
        seq += 1
        write({"ctl": CTL, "verb": "logs", "which": which, "seq": seq, "line": ln})
    def ident():
        try:
            st = os.stat(path)
            return st.st_ino, st.st_dev
        except OSError:
            return None
    fh, cur = None, None
    pos = os.path.getsize(path) if os.path.exists(path) else 0
    buf = b""
    while not (stop and stop()):
        now = ident()
        if now != cur:
            if fh:
                fh.close()
            fh = open(path, "rb") if now else None
            if fh and cur is not None:
                pos = 0
            cur = now
            if fh:
                fh.seek(pos)
        if fh:
            chunk = fh.read(65536)
            if chunk:
                buf += chunk
                *done, buf = buf.split(b"\n")
                for ln in done:
                    seq += 1
                    write({"ctl": CTL, "verb": "logs", "which": which, "seq": seq,
                           "line": _clean(ln.decode("utf-8", "replace"))})
                continue
        time.sleep(poll)
    return EXIT_OK


HANDLERS = {
    "status": v_status, "start": v_start, "stop": v_stop, "restart": v_restart,
    "mode get": v_mode_get, "mode set": v_mode_set,
    "settings get": v_settings_get, "settings plan": v_settings_plan, "settings apply": v_settings_apply,
    "settings versions": v_settings_versions, "settings restore": v_settings_restore,
    "logs": v_logs, "doctor": v_doctor,
}


def run(argv: list[str], env: dict | None = None, tree: Tree | None = None) -> int:
    env = dict(os.environ if env is None else env)
    tree = tree or Tree()
    t0 = time.monotonic()
    row: dict = {"at": journal.utc()}
    name, rc, call = None, EXIT_INTERNAL, None
    info = caller(env)
    row.update(info)
    try:
        tokens = session(env, argv, info)
        row.update(info)
        row["verb"] = tokens[0][:40]
        row["args"] = journal.redact_args(tokens[1:])
        call = grammar.parse(tokens)
        name = call.name
        row["verb"] = name
        row["args"] = journal.redact_args(tokens[2 if call.sub else 1:])
        if name == "logs" and call.opts.get("--follow"):
            def _hup(*_):
                raise BrokenPipeError
            signal.signal(signal.SIGHUP, _hup)
            signal.signal(signal.SIGTERM, _hup)
            try:
                rc = follow(tree, call)
            except BrokenPipeError:
                rc = EXIT_OK
            return rc
        if name == "unpair":
            body, rc = v_unpair(tree, call, info, env)
        else:
            body, rc = HANDLERS[name](tree, call)
        if body.get("job"):
            row["job"] = body["job"]["id"]
        doc = answer(name, body)
        if rc == EXIT_NOT_OK:
            doc["ok"] = False
        emit(doc)
        return rc
    except Refusal as ref:
        rc = ref.exit_code
        row.update(info)
        row["error"] = ref.code
        try:
            emit(error(name, ref))
        except BrokenPipeError:
            pass
        return rc
    except BrokenPipeError:
        rc = EXIT_OK
        return rc
    except Exception as exc:                                          # noqa: BLE001
        rc = EXIT_INTERNAL
        row["error"] = "internal"
        try:
            emit(error(name, Refusal("internal", f"{type(exc).__name__}: {str(exc)[:300]}", EXIT_INTERNAL)))
        except BrokenPipeError:
            pass
        return rc
    finally:
        row["exit"] = rc
        row["ms"] = int((time.monotonic() - t0) * 1000)
        row.setdefault("verb", None)
        journal.write(tree.journal, row)


def main() -> int:
    return run(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
