"""What pairing writes and reads on the engine host (SEC-10, ENG-448): authorized_keys, the SSH host
keys and port, and the HTTP token file with one token a paired key. No server; a sandbox's files only.

The test matrix:

  1  authorized_keys: a missing ~/.ssh is created 0700 and the file 0600; a line is appended after
     every other line byte for byte (a file without a final newline too, comments, CRLF, an
     operator's own keys and options); the same key and id again is not added twice; refused,
     with nothing changed: a key that has a line of its own, a key id another key holds, a file or
     ~/.ssh that is a symbolic link, a FIFO in its place, a file or ~/.ssh of another user, a file or
     ~/.ssh writable by group or others                      test_authorized_keys
  2  A1's unpair removes the paired line again (and nothing else), and with it the key's token line;
     `token rotate` over a key's channel replaces only that key's token, by hand the whole file
                                                             test_unpair_and_rotate_per_key
  3  the host keys: ed25519 first, then ECDSA, then RSA (the OpenSSH client's order); sshd_config's
     HostKey lines and Include files decide which; the fingerprints equal `ssh-keygen -lf`; the SSH
     port from QSE_SSH_PORT, else sshd_config's Port (a Match block's ignored), else 22; a public
     key whose blob names another type is refused            test_host_keys_and_port
  4  the token file: issue adds a key's line and drops the install's lock, keeps other keys' lines;
     every line's token passes the gate; lock writes only where no file is; drop leaves the lock
     when the last key goes (never an open network); a line that does not read locks the network
     out                                                     test_the_token_file

Run: python tests/test_pair_keys.py
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import remote_box as R  # noqa: E402
from pair_client import ed25519_line, host_etc  # noqa: E402
from server.api import access, auth  # noqa: E402
from server.pairing import hostkey, spake2 as S  # noqa: E402
from server.remote import keys  # noqa: E402
from server.remote.paths import Tree  # noqa: E402

CONTROL = "/home/u/TandemLLM/bin/tandem"
K1, K2 = ed25519_line("k1").split()[1], ed25519_line("k2").split()[1]


def _home() -> tuple[str, str]:
    tmp = tempfile.mkdtemp(prefix="pair-keys-")
    return tmp, os.path.join(tmp, ".ssh", "authorized_keys")


def _refused(code: str, fn) -> None:
    try:
        fn()
    except keys.Unsafe as exc:
        assert exc.code == code, (exc.code, code, exc.message)
        return
    raise AssertionError(f"not refused: {code}")


def test_authorized_keys():
    tmp, path = _home()
    try:
        got = keys.install(CONTROL, "ctl-1", "ssh-ed25519", K1, path=path)
        line1 = keys.key_line(CONTROL, "ctl-1", "ssh-ed25519", K1)
        assert got == {"added": True, "created": True, "line": line1}, got
        assert stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode) == 0o700
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        assert open(path).read() == line1 + "\n"
        # the same key and id: not added twice
        assert keys.install(CONTROL, "ctl-1", "ssh-ed25519", K1, path=path)["added"] is False
        assert open(path).read() == line1 + "\n"
        # every other line byte for byte, a file without a final newline
        other = (b"# the operator's\r\nfrom=\"203.0.113.0/24\",no-agent-forwarding ssh-rsa AAAAB3Nza me@x\n\n"
                 b'command="/bin/bash /other/bin/tandem remote --key-id ctl-2",no-pty ssh-ed25519 ' + K2.encode() +
                 b" else")
        with open(path, "wb") as fh:
            fh.write(other)
        got = keys.install(CONTROL, "ctl-2", "ssh-ed25519", K1, path=path)
        assert got["added"] and not got["created"]
        assert open(path, "rb").read() == other + b"\n" + keys.key_line(CONTROL, "ctl-2", "ssh-ed25519", K1).encode() + b"\n"
        # refusals, each with the file unchanged
        before = open(path, "rb").read()
        _refused("key_id_taken", lambda: keys.install(CONTROL, "ctl-2", "ssh-ed25519", ed25519_line("k3").split()[1],
                                                      path=path))
        _refused("key_present", lambda: keys.install(CONTROL, "ctl-3", "ssh-ed25519", K2, path=path))
        with open(path, "ab") as fh:
            fh.write(b"ssh-ed25519 " + ed25519_line("own").split()[1].encode() + b" me@laptop\n")
        before = open(path, "rb").read()
        _refused("key_present", lambda: keys.install(CONTROL, "ctl-9", "ssh-ed25519",
                                                     ed25519_line("own").split()[1], path=path))
        _refused("key_present", lambda: keys.install("/x/bin/tandem", "ctl-1", "ssh-ed25519", K1, path=path))
        assert open(path, "rb").read() == before
        me = os.getuid()
        # another user's (no chown without root: the uid the check compares with is another's)
        _refused("ssh_dir_owner", lambda: keys.install(CONTROL, "n1", "ssh-ed25519", K2, path=path, uid=me + 1))
        _refused("authorized_keys_owner", lambda: keys._check(os.stat(path), path, me + 1, "authorized_keys"))
        os.chmod(path, 0o620)
        _refused("authorized_keys_mode", lambda: keys.install(CONTROL, "n1", "ssh-ed25519", K2, path=path))
        os.chmod(path, 0o600)
        os.chmod(os.path.dirname(path), 0o770)
        _refused("ssh_dir_mode", lambda: keys.install(CONTROL, "n1", "ssh-ed25519", K2, path=path))
        os.chmod(os.path.dirname(path), 0o700)
        assert open(path, "rb").read() == before
        # a link in the file's place, a link for ~/.ssh, a FIFO
        real = os.path.join(tmp, "real-keys")
        shutil.move(path, real)
        os.symlink(real, path)
        _refused("authorized_keys_link", lambda: keys.install(CONTROL, "n1", "ssh-ed25519", K2, path=path))
        assert open(real, "rb").read() == before
        os.unlink(path)
        os.mkfifo(path)
        _refused("authorized_keys_type", lambda: keys.install(CONTROL, "n1", "ssh-ed25519", K2, path=path))
        os.unlink(path)
        tmp2, path2 = _home()
        elsewhere = os.path.join(tmp2, "elsewhere")
        os.makedirs(elsewhere, mode=0o700)
        os.symlink(elsewhere, os.path.dirname(path2))
        _refused("ssh_dir_link", lambda: keys.install(CONTROL, "n1", "ssh-ed25519", K2, path=path2))
        assert os.listdir(elsewhere) == []
        shutil.rmtree(tmp2)
        return "created 0700/0600, appended, idempotent; key_present, key_id_taken, owner, mode, link, fifo refused"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_unpair_and_rotate_per_key():
    b = R.make(install=True)
    try:
        tree = Tree(b["root"])
        control = tree.control
        path = os.path.join(b["home"], ".ssh", "authorized_keys")
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        os.chmod(os.path.dirname(path), 0o700)
        own = "ssh-ed25519 " + ed25519_line("own").split()[1] + " me@laptop\n"
        with open(path, "w") as fh:
            fh.write(own)
        os.chmod(path, 0o600)
        keys.install(control, "ctl-a", "ssh-ed25519", K1, path=path)
        keys.install(control, "ctl-b", "ssh-ed25519", K2, path=path)
        ta = access.issue(tree.http_token, "ctl-a")
        tb = access.issue(tree.http_token, "ctl-b")
        gate = access.Gate(tree.http_token)
        a = auth.Auth(None, None, login=False)

        def passes(tok):
            gate._next = 0
            return gate.check("192.0.2.9", {"Authorization": f"Bearer {tok}"}, "GET", "/v1/models", a) is None
        assert passes(ta) and passes(tb)
        # token rotate over ctl-a's channel: only ctl-a's token changes
        rc, doc = R.call(b, "--key-id", "ctl-a", ssh="token rotate")
        assert rc == 0 and doc["ok"], doc
        R.valid("token-rotate", doc)
        new_a = doc["token"]
        assert passes(new_a) and passes(tb) and not passes(ta), "a keyed rotate cut another key's token"
        # unpair over ctl-b's channel: its line and its token go, nothing else
        before = open(path).read()
        rc, doc = R.call(b, "--key-id", "ctl-b", ssh="unpair")
        assert rc == 0 and doc["ok"], doc
        R.valid("unpair", doc)
        after = open(path).read()
        assert after == before.replace(keys.key_line(control, "ctl-b", "ssh-ed25519", K2) + "\n", ""), after
        assert after.startswith(own)
        assert not passes(tb) and passes(new_a)
        # by hand, the whole file: this token alone
        tok, _ = access.rotate(tree.http_token)
        assert passes(tok) and not passes(new_a)
        return "keyed rotate keeps the other key's token; unpair takes the line and its token"
    finally:
        R.cleanup(b)


def test_host_keys_and_port():
    tmp = tempfile.mkdtemp(prefix="pair-host-")
    try:
        etc = host_etc(tmp, ("rsa", "ecdsa", "ed25519"))
        got = hostkey.host_keys(etc)
        assert [k["type"] for k in got][:1] == ["ssh-ed25519"], got
        types = [k["type"] for k in got]
        assert types == sorted(types, key=lambda t: hostkey.ORDER.index(t)), types
        if shutil.which("ssh-keygen"):
            for k in got:
                lf = subprocess.run(["ssh-keygen", "-lf", k["path"]], capture_output=True, text=True, check=True)
                assert lf.stdout.split()[1] == k["fingerprint"], (lf.stdout, k)
        # HostKey lines decide, through Include too
        inc = os.path.join(etc, "sshd_config.d")
        os.makedirs(inc)
        with open(os.path.join(inc, "10-keys.conf"), "w") as fh:
            fh.write(f"HostKey {etc}/ssh_host_rsa_key\n")
        with open(os.path.join(etc, "sshd_config"), "w") as fh:
            fh.write("# comment\nInclude /nonexistent/*.conf\nInclude sshd_config.d/*.conf\nPort 2200\nPort 2201\n"
                     "Match User x\n  Port 9\n")
        only = hostkey.host_keys(etc)
        assert [k["type"] for k in only] == ["ssh-rsa"], only
        assert hostkey.ssh_port({}, etc) == 2200
        assert hostkey.ssh_port({"QSE_SSH_PORT": "2022"}, etc) == 2022
        assert hostkey.ssh_port({"QSE_SSH_PORT": "x"}, etc) == 2200
        assert hostkey.ssh_port({}, os.path.join(tmp, "none")) == 22
        # a line whose blob names another type, and an ed25519 of the wrong size
        bad = os.path.join(tmp, "bad")
        os.makedirs(bad)
        line = ed25519_line("x")
        with open(os.path.join(bad, "ssh_host_ed25519_key.pub"), "w") as fh:
            fh.write("ssh-rsa " + line.split()[1] + "\n")
        assert hostkey.host_keys(bad) == []
        try:
            hostkey.preferred(bad)
            raise AssertionError("no key, no refusal")
        except hostkey.NoHostKey:
            pass
        import base64
        import struct
        short = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 31) + b"x" * 31
        assert hostkey.ed25519_key("ssh-ed25519 " + base64.b64encode(short).decode()) is None
        assert hostkey.ed25519_key(line)[1] == base64.b64decode(line.split()[1])
        assert hostkey.bound_addresses("127.0.0.1") == [] and hostkey.bound_addresses("192.0.2.5") == ["192.0.2.5"]
        assert all(not a.startswith("127.") for a in hostkey.bound_addresses("0.0.0.0"))
        return f"{len(got)} keys ordered, fingerprints as ssh-keygen -l, HostKey/Include/Port/Match read"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_the_token_file():
    tmp = tempfile.mkdtemp(prefix="pair-token-")
    try:
        path = os.path.join(tmp, "run", "http-token")
        assert access.lock(path) is True and access.lock(path) is False
        lines = access.read_lines(path)
        assert len(lines) == 1 and lines[0][1] == access.LOCK
        gate = access.Gate(path)
        a = auth.Auth(None, None, login=False)
        assert gate.check("192.0.2.9", {}, "GET", "/v1/models", a)[0] == 401
        assert gate.check("127.0.0.1", {}, "GET", "/v1/models", a) is None         # this host needs none
        t1 = access.issue(path, "ctl-1")
        t2 = access.issue(path, "ctl-2")
        got = access.read_lines(path)
        assert [o for _h, o in got] == ["ctl-1", "ctl-2"], got                       # the lock went
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        for t in (t1, t2):
            gate._next = 0
            assert gate.check("192.0.2.9", {"Authorization": f"Bearer {t}"}, "GET", "/v1/models", a) is None
        t1b = access.issue(path, "ctl-1")
        gate._next = 0
        assert gate.check("192.0.2.9", {"Authorization": f"Bearer {t1}"}, "GET", "/x", a)[0] == 401
        assert gate.check("192.0.2.9", {"Authorization": f"Bearer {t1b}"}, "GET", "/x", a) is None
        assert access.drop(path, "ctl-1") and access.drop(path, "ctl-2")
        left = access.read_lines(path)
        assert len(left) == 1 and left[0][1] == access.LOCK, left                   # never an open network
        assert not access.drop(path, "ctl-9")
        with open(path, "a") as fh:
            fh.write("not a token line\n")
        gate._next = 0
        assert gate.required() == "" and gate.check("192.0.2.9", {}, "GET", "/x", a)[0] == 401
        # a key id that is not one does not land in the file
        assert access.TOKEN_LINE.match("sha256:" + "0" * 64 + " bad id") is None
        assert hostkey.fingerprint(b"x").startswith("SHA256:")
        return "lock, issue per key, rotate one, drop to the lock, a bad line fails closed"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
