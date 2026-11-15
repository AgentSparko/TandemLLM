"""`tandem remote unpair` (engine-ctl-v1; ENG-452, SEC-12): only the line of this call's key goes.

The test matrix:

  1  with --key-id from the forced command: the one line whose forced command runs this install's
     `tandem remote --key-id ID` goes; every other line, comments and blank lines included, stays
     byte for byte; the file keeps its mode                       test_unpair_by_key_id
  2  with sshd's SSH_USER_AUTH (ExposeAuthInfo): the line with that public key and this install's
     forced command goes; an operator's own line with the same key and no forced command stays
                                                                  test_unpair_by_user_auth
  3  refusals, nothing removed: neither the id nor SSH_USER_AUTH, an id no line carries, a line of
     another install, two lines for one key, a key id whose line holds another key, no file
                                                                  test_unpair_refusals
  4  the options parser: quoted commas and spaces, escaped quotes, from= before command=, a line
     without options, a comment                                   test_the_line_parser

Run: python tests/test_remote_unpair.py
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import remote_box as R  # noqa: E402
from server.remote import keys as K  # noqa: E402
from server.remote.grammar import Refusal  # noqa: E402

KEY_A = "AAAAC3NzaC1lZDI1NTE5AAAAIAaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
KEY_B = "AAAAC3NzaC1lZDI1NTE5AAAAIBbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
KEY_C = "AAAAC3NzaC1lZDI1NTE5AAAAICccccccccccccccccccccccccccccccccccccccccc"
OPTS = "no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding"


def _line(control: str, key: str, kid: str | None, comment: str, extra: str = "") -> str:
    cmd = f"/bin/bash {control} remote" + (f" --key-id {kid}" if kid else "")
    return f'{extra}command="{cmd}",{OPTS} ssh-ed25519 {key} {comment}'


def _setup(lines: list[str]) -> tuple[str, str]:
    d = tempfile.mkdtemp(prefix="unpair-")
    os.makedirs(os.path.join(d, "bin"))
    control = os.path.join(d, "bin", "tandem")
    open(control, "w").close()
    ak = os.path.join(d, "authorized_keys")
    with open(ak, "w") as fh:
        fh.write("\n".join(lines(control) if callable(lines) else lines) + "\n")
    os.chmod(ak, 0o600)
    return control, ak


def _auth(d: str, key: str) -> str:
    p = os.path.join(d, "user-auth")
    with open(p, "w") as fh:
        fh.write(f"publickey ssh-ed25519 {key}\n")
    return p


def test_unpair_by_key_id():
    control, ak = _setup(lambda c: ["# keys", "", f"ssh-ed25519 {KEY_C} operator@laptop",
                                    _line(c, KEY_A, "ctl-1", "ctl-1", 'from="192.0.2.0/24",'),
                                    _line(c, KEY_B, "ctl-2", "ctl-2")])
    before = open(ak).read().split("\n")
    out = K.unpair(control, "ctl-1", None, ak)
    assert out == {"removed": {"type": "ssh-ed25519", "comment": "ctl-1", "key_id": "ctl-1"}, "left": 2}, out
    after = open(ak).read().split("\n")
    assert after == before[:3] + before[4:], (before, after)
    assert stat.S_IMODE(os.stat(ak).st_mode) == 0o600
    return "one line gone, the rest byte for byte"


def test_unpair_by_user_auth():
    control, ak = _setup(lambda c: [f"ssh-ed25519 {KEY_A} operator-same-key",
                                    _line(c, KEY_A, None, "ctl"), _line(c, KEY_B, None, "other")])
    out = K.unpair(control, None, _auth(os.path.dirname(ak), KEY_A), ak)
    assert out["left"] == 2 and out["removed"]["comment"] == "ctl", out
    text = open(ak).read()
    assert f"ssh-ed25519 {KEY_A} operator-same-key" in text and "ctl\n" not in text and "other" in text, text
    # through the CLI: sshd's file, a forced command's session
    b = R.make()
    try:
        home = b["home"]
        os.makedirs(os.path.join(home, ".ssh"))
        control = os.path.join(b["root"], "bin", "tandem")
        open(control, "w").close()
        with open(os.path.join(home, ".ssh", "authorized_keys"), "w") as fh:
            fh.write(_line(control, KEY_A, "ctl-9", "ctl-9") + "\n" + _line(control, KEY_B, "ctl-8", "ctl-8") + "\n")
        rc, ans = R.call(b, "--key-id", "ctl-9", ssh="unpair")
        assert rc == 0 and ans["removed"]["key_id"] == "ctl-9" and ans["left"] == 1, ans
        assert "ctl-9" not in open(os.path.join(home, ".ssh", "authorized_keys")).read()
        rc, ans = R.call(b, "--key-id", "ctl-9", ssh="unpair")
        assert rc == 3 and ans["error"]["code"] == "key_unknown", ans
    finally:
        R.cleanup(b)
    return "by key, the operator's line kept; through the CLI"


def _refused(fn, code):
    try:
        fn()
    except Refusal as r:
        assert r.code == code, (code, r.code)
        return
    raise AssertionError(code)


def test_unpair_refusals():
    control, ak = _setup(lambda c: [_line(c, KEY_A, "ctl-1", "a"), _line(c, KEY_A, None, "a2"),
                                    _line("/other/bin/tandem", KEY_B, "ctl-2", "b"),
                                    _line(c, KEY_C, "dup", "c1"), _line(c, KEY_C, "dup", "c2")])
    before = open(ak).read()
    _refused(lambda: K.unpair(control, None, None, ak), "key_unknown")
    _refused(lambda: K.unpair(control, "ctl-9", None, ak), "key_unknown")
    _refused(lambda: K.unpair(control, "ctl-2", None, ak), "key_unknown")
    _refused(lambda: K.unpair(control, "dup", None, ak), "key_ambiguous")
    _refused(lambda: K.unpair(control, None, _auth(os.path.dirname(ak), KEY_A), ak), "key_ambiguous")
    _refused(lambda: K.unpair(control, "ctl-1", _auth(os.path.dirname(ak), KEY_B), ak), "key_unknown")
    _refused(lambda: K.unpair(control, "ctl-1", None, ak + ".none"), "key_unknown")
    assert open(ak).read() == before
    return "7 refusals, file unchanged"


def test_the_line_parser():
    p = K.split_line('from="a,b c",command="/bin/bash /x/bin/tandem remote --key-id k",no-pty ssh-ed25519 '
                     + KEY_A + " the comment")
    assert p == ('from="a,b c",command="/bin/bash /x/bin/tandem remote --key-id k",no-pty', "ssh-ed25519",
                 KEY_A, "the comment"), p
    assert K.forced_command(p[0]) == "/bin/bash /x/bin/tandem remote --key-id k"
    q = K.split_line('command="echo \\"x y\\"" ssh-rsa AAAAB3 c')
    assert q[1:3] == ("ssh-rsa", "AAAAB3") and K.forced_command(q[0]) == 'echo "x y"', q
    assert K.split_line(f"ssh-ed25519 {KEY_A}") == ("", "ssh-ed25519", KEY_A, "")
    assert K.split_line("# a comment") is None and K.split_line("") is None
    assert K.split_line("garbage line here") is None
    assert K._ours("/bin/bash /x/bin/tandem remote --key-id k", "/x/bin/tandem")
    assert not K._ours("/x/bin/tandem start", "/x/bin/tandem") and not K._ours(None, "/x/bin/tandem")
    return "options, quotes, comments"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
