"""`tandem remote`'s grammar, its refusals and its journal (engine-ctl-v1; ENG-452, SEC-12).

The test matrix:

  1  every verb of the grammar parses with its options in both forms (`--when idle`, `--when=idle`),
     its arguments checked: when (now, idle, at= in the window), deadline bounds, lines, which,
     KEY=VALUE and --unset, a version, engine|chat, --ext                test_the_verbs_parse
  2  every refusal of the grammar has its code and the usage exit: unknown verb (the planned ones
     too), subcommand, option, an extra argument, a missing one, a bad value, an option twice, a
     control character, a space inside a token, an unbalanced quote, too many arguments
                                                                         test_every_grammar_refusal
  3  the session: a forced command takes --key-id only; a terminal, a forwarded agent, an X11 display
     or no command (a shell) are refused before a verb runs, each journaled with its code
                                                                         test_the_session_refusals
  4  no shell is reachable: `status; touch F`, `$(touch F)`, backticks, `|`, `&&`, a newline and a
     redirection are refused or answered as status alone, and F is never created
                                                                         test_no_shell_is_reachable
  5  the journal: one line a call, refused ones too, UTC time, verb, arguments, exit, duration, the
     key id and the address; a secret-looking value cut out; rotation past the cap keeps KEEP files
                                                                         test_the_journal

CPU, no engine. Run: python tests/test_remote_grammar.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import remote_box as R  # noqa: E402
from server.remote import grammar as G  # noqa: E402
from server.remote import journal as J  # noqa: E402

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)


def _p(line: str):
    return G.parse(G.split(line), now=NOW)


def _refused(line: str, code: str, exit_code: int = G.EXIT_USAGE):
    try:
        _p(line)
    except G.Refusal as r:
        assert (r.code, r.exit_code) == (code, exit_code), (line, r.code, r.exit_code, str(r))
        return str(r)
    raise AssertionError(f"{line!r} was not refused")


def test_the_verbs_parse():
    assert _p("status").name == "status" and _p("doctor").name == "doctor" and _p("unpair").name == "unpair"
    for line in ("restart --when idle", "restart --when=idle"):
        c = _p(line)
        assert c.opts["--when"] == {"mode": "idle"}, c
    c = _p("restart --when at=2026-11-16T03:00:00Z --deadline 900")
    assert c.opts == {"--when": {"mode": "at", "at": "2026-11-16T03:00:00Z"}, "--deadline": 900}, c.opts
    c = _p("restart --when at=2026-11-15T14:00:00+01:00")
    assert c.opts["--when"]["at"] == "2026-11-15T13:00:00Z", c.opts
    c = _p("logs --follow --lines 20 --which launcher")
    assert c.opts == {"--follow": True, "--lines": 20, "--which": "launcher"}, c.opts
    c = _p("settings plan MAX_QUEUE=4 PATTERN_STOP=256:32:8 --unset QSE_PARALLEL")
    assert c.sets == [("MAX_QUEUE", "4"), ("PATTERN_STOP", "256:32:8")] and c.unsets == ["QSE_PARALLEL"], c
    c = _p("settings plan 'REASONING_EFFORT=low'")
    assert c.sets == [("REASONING_EFFORT", "low")]
    c = _p("settings apply MAX_QUEUE=4 --confirm 0123456789abcdef --confirm-fingerprint fedcba9876543210 "
           "--when now")
    assert c.opts["--confirm"] == "0123456789abcdef" and c.opts["--when"] == {"mode": "now"}, c.opts
    assert _p("settings restore 3 --confirm 0123456789abcdef").opts["version"] == 3
    assert _p("settings get MAX_QUEUE QSE_MODE").pos == ["MAX_QUEUE", "QSE_MODE"]
    assert _p("settings versions").name == "settings versions"
    c = _p("mode set chat --ext pkg.ext_mod --when idle")
    assert c.opts["mode"] == "chat" and c.opts["--ext"] == "pkg.ext_mod", c.opts
    assert _p("mode get").name == "mode get"
    return "13 shapes"


def test_every_grammar_refusal():
    for v in G.PLANNED:
        assert "not in this engine yet" in _refused(f"{v} list", "unknown_verb")
    _refused("bash", "unknown_verb")
    _refused("settings", "unknown_subcommand")
    _refused("settings delete", "unknown_subcommand")
    _refused("mode", "unknown_subcommand")
    _refused("status --verbose", "unknown_option")
    _refused("logs --path /etc/passwd", "unknown_option")
    _refused("start now", "extra_argument")
    _refused("status /etc/passwd", "extra_argument")
    _refused("restart --when", "missing_argument")
    _refused("settings plan", "missing_argument")
    _refused("settings restore", "missing_argument")
    _refused("mode set", "missing_argument")
    _refused("restart --when soon", "bad_value")
    _refused("restart --when at=2026-11-15T03:00:00Z", "bad_value")         # passed
    _refused("restart --when at=2026-12-15T03:00:00Z", "bad_value")         # beyond 7 days
    _refused("restart --when at=2026-11-15T13:00:00", "bad_value")          # no zone
    _refused("restart --when now --deadline 60", "bad_value")
    _refused("restart --when idle --deadline 5", "bad_value")
    _refused("restart --when idle --deadline 100000", "bad_value")
    _refused("logs --lines 0", "bad_value")
    _refused("logs --lines 99999", "bad_value")
    _refused("logs --which /etc/passwd", "bad_value")
    _refused("logs --follow=yes", "bad_value")
    _refused("settings plan max_queue=4", "bad_value")
    _refused("settings plan MAX_QUEUE", "bad_value")
    _refused("settings plan --unset lower", "bad_value")
    _refused("settings apply MAX_QUEUE=4 --confirm xyz", "bad_value")
    _refused("settings restore x", "bad_value")
    _refused("mode set debug", "bad_value")
    _refused("mode set chat --ext 'a;b'", "bad_value")
    _refused("restart --when now --when idle", "duplicate_option")
    _refused("settings plan MAX_QUEUE=4 MAX_QUEUE=5", "duplicate_option")
    _refused("settings plan MAX_QUEUE=4 --unset MAX_QUEUE", "duplicate_option")
    for line, code in (("status\x07", "bad_command"), ("status 'a b'", "bad_command"),
                       ("status 'open", "bad_command"), (" ".join(["status"] * 70), "bad_command"),
                       ("status é", "bad_command"), ("x" * 9000, "bad_command")):
        try:
            G.split(line)
            raise AssertionError(line)
        except G.Refusal as r:
            assert r.code == code and r.exit_code == G.EXIT_USAGE, (line, r.code)
    try:
        G.split("   ")
        raise AssertionError("empty")
    except G.Refusal as r:
        assert (r.code, r.exit_code) == ("no_command", G.EXIT_REFUSED)
    return "39 refusals"


def test_the_session_refusals():
    b = R.make()
    try:
        cases = [
            ({"ssh": "status", "SSH_TTY": "/dev/pts/3"}, "pty_refused"),
            ({"ssh": "status", "SSH_AUTH_SOCK": "/tmp/agent.1"}, "forward_refused"),
            ({"ssh": "status", "DISPLAY": "localhost:10.0"}, "forward_refused"),
            ({"ssh": ""}, "no_command"),
            ({"ssh": "   "}, "no_command"),
        ]
        for kw, code in cases:
            rc, ans = R.call(b, "--key-id", "ctl-1", **kw)
            assert rc == G.EXIT_REFUSED and ans["error"]["code"] == code and ans["ok"] is False, (kw, ans)
        # a forced command takes --key-id only: anything else from the key's line is refused
        rc, ans = R.call(b, "--key-id", "ctl-1", "status", ssh="status")
        assert rc == G.EXIT_REFUSED and ans["error"]["code"] == "bad_command", ans
        # a shell asked of a forced command (sshd sets no SSH_ORIGINAL_COMMAND)
        rc, ans = R.call(b, "--key-id", "ctl-1", SSH_CONNECTION="192.0.2.7 1 192.0.2.1 22")
        assert rc == G.EXIT_REFUSED and ans["error"]["code"] == "no_command", ans
        rc, ans = R.call(b, "--key-id", "c t")
        assert rc == G.EXIT_REFUSED and ans["error"]["code"] == "bad_command", ans
        rc, ans = R.call(b, ssh="status", SSH_TTY="")
        assert rc == 0 and ans["ok"] and ans["verb"] == "status", ans
        rows = R.journal(b)
        assert [r.get("error") for r in rows] == ["pty_refused", "forward_refused", "forward_refused",
                                                 "no_command", "no_command", "bad_command", "no_command",
                                                 "bad_command", None], rows
        assert all(r["via"] == "ssh" for r in rows[:5]) and rows[0]["key"] == "ctl-1", rows
        assert rows[0]["from"] == "192.0.2.7" and rows[-1]["verb"] == "status" and rows[-1]["exit"] == 0, rows
    finally:
        R.cleanup(b)
    return "10 sessions"


def test_no_shell_is_reachable():
    b = R.make()
    try:
        flag = os.path.join(b["root"], "pwned")
        for line in (f"status; touch {flag}", f"status && touch {flag}", f"status | touch {flag}",
                     f"$(touch {flag})", f"`touch {flag}`", f"status > {flag}", f"status\ntouch {flag}",
                     f"touch {flag}", f"sh -c 'touch {flag}'", f"settings plan MAX_QUEUE=$(touch {flag})",
                     f"settings plan QSE_DISK_CACHE={flag}`id`"):
            rc, ans = R.call(b, ssh=line)
            assert rc in (G.EXIT_USAGE, G.EXIT_REFUSED) and ans["ok"] is False, (line, ans)
            assert not os.path.exists(flag), line
        rc, ans = R.call(b, ssh="'status'")
        assert rc == 0 and ans["verb"] == "status", ans
        assert len(R.journal(b)) == 12
    finally:
        R.cleanup(b)
    return "11 injections refused, nothing ran"


def test_the_journal():
    toks = ["plan", "QSE_ADMIN_TOKEN=abc", "HF_TOKEN=hf_x", "MAX_QUEUE=4", "--password", "pw", "--confirm",
            "0123456789abcdef", "PATTERN_STOP=" + "9" * 300]
    red = J.redact_args(toks)
    assert red[1] == "QSE_ADMIN_TOKEN=[redacted]" and red[2] == "HF_TOKEN=[redacted]", red
    assert red[3] == "MAX_QUEUE=4" and red[5] == "[redacted]" and red[7] == "0123456789abcdef", red
    assert red[8].endswith("...") and len(red[8]) == J.MAX_ARG + 3, red
    d = tempfile.mkdtemp(prefix="remote-journal-")
    p = os.path.join(d, "run", "remote", "journal.jsonl")
    cap = J.CAP_BYTES
    try:
        J.CAP_BYTES = 2000
        for i in range(200):
            J.write(p, {"at": J.utc(), "verb": "status", "args": [], "exit": 0, "ms": i})
        files = sorted(os.listdir(os.path.dirname(p)))
        assert files == ["journal.jsonl", "journal.jsonl.1", "journal.jsonl.2", "journal.jsonl.3",
                         "journal.jsonl.lock"], files
        assert all(os.path.getsize(os.path.join(os.path.dirname(p), f)) <= 2000 for f in files)
        assert J.read(p)[-1]["ms"] == 199 and oct(os.stat(p).st_mode & 0o777) == "0o600"
    finally:
        J.CAP_BYTES = cap
    b = R.make()
    try:
        R.call(b, "settings", "plan", "QSE_ADMIN_TOKEN=s3cret-value")
        R.call(b, "status")
        rows = R.journal(b)
        assert rows[0]["verb"] == "settings plan" and rows[0]["args"] == ["QSE_ADMIN_TOKEN=[redacted]"], rows
        assert rows[0]["exit"] == G.EXIT_USAGE and rows[0]["error"] == "unknown_key", rows
        assert "s3cret" not in open(os.path.join(b["root"], "run", "remote", "journal.jsonl")).read()
        t = datetime.strptime(rows[1]["at"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        assert abs(datetime.now(timezone.utc) - t) < timedelta(minutes=5), rows[1]
        assert set(rows[1]) >= {"at", "verb", "args", "exit", "ms", "via"} and rows[1]["via"] == "local", rows
        assert isinstance(rows[1]["ms"], int)
    finally:
        R.cleanup(b)
    return "redaction, rotation, rows"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
