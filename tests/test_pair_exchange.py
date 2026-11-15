"""Pairing by code (SEC-10; engine-pair-v1): the window, its limits and the exchange, end to end over
HTTP with a client written from the contract (tests/pair_client.py, TandemApp's half).

server/pairing/window.py's Service runs on a sandbox install tree (tests/remote_box.py), with host
keys, an authorized_keys and a clock of the test's own; server/pairing/http.py's routes serve it on
a loopback socket, the peer address set as each case wants (a network peer is not trusted-local).
Nothing of the box is read or written.

The test matrix (the Gherkin of SEC-10):

  1  Given an open window, when TandemApp completes the exchange with the right code, then its key
     is in authorized_keys with the forced command (A1's line, --key-id, 0600 in a 0700 ~/.ssh,
     nothing else in the file), its token's hash in run/http-token on its key id's line (the
     token opens the gate), the answer opens with the run's key and holds the SSH user and port,
     the host keys, the HTTP port; and the window is closed: a second run gets 410 (single use);
     every answer validates against the contract           test_the_right_code_pairs
  2  Given five wrong codes (TandemApp's check of cB fails and it abandons; or a guesser sends cA
     anyway: 403 wrong_code), then the code is spent (410, the right code too) and nothing was
     installed: no authorized_keys, no token file          test_five_wrong_codes_spend_it
  3  expiry: past 5 minutes the right code gets 410 and nothing is installed; a run's session
     lasts 60 s                                             test_the_window_expires
  4  a host key swapped on the way (and TandemApp's key swapped on the way): the confirmation fails,
     TandemApp abandons, nothing is installed               test_a_swapped_key_fails
  5  the journal: open, each try, abandoned, confirm, paired, expired and closed lines with the
     peer, the key id and its fingerprint; never the code, the token, a key's bytes or a protocol
     message                                                test_the_journal_holds_no_secret
  6  open and close: 403 local_only from the network, 403 window_key without or with a wrong key,
     200 on this host with it; a closed window answers 404 no_window; a new window closes the old
     one (its code no longer pairs)                        test_open_and_close
  7  open refuses a checkout (no bin/tandem), no host key, an unsafe ~/.ssh; step 2 refuses a key
     that already has a line of its own (409 key_present: the file unchanged, the window still
     open, the run spent), and a line that fails another way (500 install_failed, the window
     still open)                                            test_the_refusals
  8  the rate limit: 20 requests a minute from one address, then 429 with Retry-After; another
     address still passes; this host is not limited        test_the_rate_limit
  9  `tandem pair`: the code, the fingerprint and the address as the last three lines; --status
     and --close; no engine: exit 1 and one line            test_tandem_pair

Run: python tests/test_pair_exchange.py
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import stat
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import remote_box as R  # noqa: E402
from pair_client import Client, PairFailed, PairServer, blob_of, ed25519_line, host_etc  # noqa: E402
from server.api import access, auth  # noqa: E402
from server.pairing import cli as pair_cli, hostkey, http as pair_http, spake2 as S  # noqa: E402
from server.pairing.window import PairError, Service  # noqa: E402
from server.remote import journal as J, keys  # noqa: E402
from server.remote.paths import Tree  # noqa: E402
from tools import contract_check  # noqa: E402

CONTRACT = os.path.join(ROOT, "contracts", "engine-pair-v1")
NET = "192.0.2.7"
APP = ed25519_line("test TandemApp key")


def valid(name: str, doc) -> None:
    errs = contract_check.validate(doc, contract_check.load(name, CONTRACT), root=CONTRACT)
    assert not errs, (name, errs, doc)


class Box:
    """An install tree, an etc/ssh, a HOME with no ~/.ssh yet, a clock, a Service and its server."""

    def __init__(self, install: bool = True, kinds=("ed25519", "ecdsa"), sshd: str | None = "Port 2222\n"):
        pair_http.LIMIT = pair_http.Limit()
        self.b = R.make(install=install)
        self.tmp = tempfile.mkdtemp(prefix="pair-ex-")
        self.etc = host_etc(self.tmp, kinds, sshd)
        self.auth = os.path.join(self.tmp, "home", ".ssh", "authorized_keys")
        os.makedirs(os.path.dirname(os.path.dirname(self.auth)))
        self.now = [1_000_000.0]
        self.tree = Tree(self.b["root"])
        self.svc = Service(self.tree, port=8123, host="0.0.0.0", mode="setup", etc=self.etc, auth_keys=self.auth,
                           env={}, clock=lambda: self.now[0])
        self.srv = PairServer(self.svc, peer=NET)

    def client(self, code: str, key_id: str = "tandemapp-1", key: str = APP, **kw) -> Client:
        return Client(self.srv.port, code, key, key_id, **kw)

    def journal(self) -> list[dict]:
        return [r for r in J.read(self.tree.journal, 500) if str(r.get("verb", "")).startswith("pair ")]

    def close(self) -> None:
        self.srv.close()
        R.cleanup(self.b)
        shutil.rmtree(self.tmp, ignore_errors=True)


def _wrong(code: str) -> str:
    return f"{(int(code) + 1) % 10 ** 6:06d}"


def _nothing_installed(box: Box) -> None:
    assert not os.path.exists(box.auth), open(box.auth).read()
    assert not os.path.exists(box.tree.http_token)


# ---------------------------------------------------------------------------------------- 1
def test_the_right_code_pairs():
    box = Box()
    try:
        opened = box.svc.open()
        valid("open", {"pair": "engine-pair-v1", **opened})
        assert re.fullmatch(r"[0-9]{6}", opened["code"]) and opened["tries"] == 5 and opened["expires_in"] == 300
        assert opened["fingerprint"] == hostkey.preferred(box.etc)["fingerprint"] and opened["ssh_port"] == 2222
        st, doc = box.srv.get()
        assert st == 200 and doc["open"] and doc["tries_left"] == 5 and "code" not in json.dumps(doc), doc
        valid("status", doc)
        got = box.client(opened["code"], expect_fp=opened["fingerprint"]).pair()
        valid("step1", got["step1"])
        valid("step2", got["step2"])
        plain = got["plain"]
        valid("answer", plain)
        control = box.tree.control
        line = keys.key_line(control, "tandemapp-1", "ssh-ed25519", APP.split()[1])
        assert line.startswith(f'command="/bin/bash {control} remote --key-id tandemapp-1",no-pty,'
                               "no-port-forwarding,no-agent-forwarding,no-X11-forwarding ssh-ed25519 "), line
        assert plain["authorized_keys"] == {"added": True, "line": line}
        with open(box.auth) as fh:
            assert fh.read() == line + "\n"
        assert stat.S_IMODE(os.stat(box.auth).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(os.path.dirname(box.auth)).st_mode) == 0o700
        assert plain["ssh_port"] == 2222 and plain["ssh_user"] == hostkey.ssh_user() and plain["http_port"] == 8123
        assert plain["host_key"] == hostkey.preferred(box.etc)["line"] and plain["mode"] == "setup"
        assert plain["host_keys"] == [k["line"] for k in hostkey.host_keys(box.etc)] and len(plain["host_keys"]) == 2
        # the token: its hash alone, on the key id's line; it opens the gate
        with open(box.tree.http_token) as fh:
            text = fh.read()
        assert text == f"sha256:{access.token_hash(plain['http_token'])} tandemapp-1\n", text
        assert plain["http_token"] not in text
        assert stat.S_IMODE(os.stat(box.tree.http_token).st_mode) == 0o600
        gate = access.Gate(box.tree.http_token)
        a = auth.Auth(None, None, login=False)
        assert gate.check(NET, {"Authorization": f"Bearer {plain['http_token']}"}, "GET", "/v1/models", a) is None
        assert gate.check(NET, {}, "GET", "/v1/models", a)[0] == 401
        # the window is closed: single use
        assert box.svc.status()["state"] == "paired" and not box.svc.lan_open()
        try:
            box.client(opened["code"], key_id="tandemapp-2", key=ed25519_line("another")).pair()
            raise AssertionError("a second run paired")
        except PairFailed as exc:
            assert (exc.kind, exc.status) == ("code_spent", 410), exc
            valid("error", exc.body)
        with open(box.auth) as fh:
            assert fh.read() == line + "\n"
        return f"paired: line, token, sealed answer; second run 410"
    finally:
        box.close()


# ---------------------------------------------------------------------------------------- 2
def test_five_wrong_codes_spend_it():
    out = []
    for guesser in (False, True):
        box = Box()
        try:
            code = box.svc.open()["code"]
            for i in range(5):
                try:
                    box.client(_wrong(code), key_id=f"g{i}").pair(skip_check=guesser)
                    raise AssertionError("a wrong code paired")
                except PairFailed as exc:
                    if guesser:
                        assert (exc.kind, exc.status) == ("wrong_code", 403), exc
                        valid("error", exc.body)
                    else:
                        assert exc.kind == "confirmation", exc
            st = box.svc.status()
            assert (st["state"], st["tries_left"], st["open"]) == ("spent", 0, False), st
            try:
                box.client(code).pair()
                raise AssertionError("the right code paired after five wrong ones")
            except PairFailed as exc:
                assert (exc.kind, exc.status) == ("code_spent", 410), exc
                assert "5 wrong codes" in exc.body["error"]["message"], exc.body
            _nothing_installed(box)
            rows = [r["verb"] for r in box.journal()]
            assert rows.count("pair try") == 6 and rows.count("pair abandoned" if not guesser else "pair confirm") == 5
            out.append("guesser 403x5" if guesser else "abandoned x5")
        finally:
            box.close()
    return ", ".join(out) + ": spent, the right code 410, nothing installed"


# ---------------------------------------------------------------------------------------- 3
def test_the_window_expires():
    box = Box()
    try:
        code = box.svc.open()["code"]
        box.now[0] += 299
        assert box.svc.lan_open()
        box.now[0] += 2
        try:
            box.client(code).pair()
            raise AssertionError("an expired code paired")
        except PairFailed as exc:
            assert (exc.kind, exc.status) == ("code_spent", 410) and "expired" in exc.body["error"]["message"], exc
        assert not box.svc.lan_open() and box.svc.status()["state"] == "expired"
        _nothing_installed(box)
        assert any(r["verb"] == "pair expired" for r in box.journal())
        # a run's session lasts 60 s
        code = box.svc.open()["code"]
        c = box.client(code)
        w, x = S.w_of(code), 12345
        pa = S.message_a(x, w)
        st, a1, _ = c.post("1", {"pA": format(pa, "x"), "public_key": APP, "key_id": "tandemapp-1"})
        assert st == 200
        box.now[0] += 61
        st, a2, _ = c.post("2", {"session": a1["session"], "cA": "0" * 64})
        assert (st, a2["error"]["type"]) == (400, "bad_request"), a2
        _nothing_installed(box)
        return "410 past 300 s; a session past 60 s 400"
    finally:
        box.close()


# ---------------------------------------------------------------------------------------- 4
def test_a_swapped_key_fails():
    box = Box()
    try:
        code = box.svc.open()["code"]
        other = ed25519_line("an attacker's host key")

        def swap_host(a1):
            a1["host_key"] = other
            a1["fingerprint"] = hostkey.fingerprint(blob_of(other))       # consistent, so only cB can tell
            return a1
        try:
            box.client(code).pair(tamper=swap_host)
            raise AssertionError("a swapped host key paired")
        except PairFailed as exc:
            assert exc.kind == "confirmation", exc
        _nothing_installed(box)
        # TandemApp's key swapped on the way: the engine binds the attacker's key, the client its own
        attacker = ed25519_line("an attacker's key")

        class Swapped(Client):
            def post(self, rest, body):
                if rest == "1":
                    body = dict(body, public_key=attacker)
                return super().post(rest, body)
        try:
            Swapped(box.srv.port, code, APP, "tandemapp-1").pair()
            raise AssertionError("a swapped TandemApp key paired")
        except PairFailed as exc:
            assert exc.kind == "confirmation", exc
        _nothing_installed(box)
        assert box.svc.status()["tries_left"] == 3
        # the right run still pairs afterwards
        assert box.client(code).pair()["plain"]["key_id"] == "tandemapp-1"
        return "host key swap and key swap: confirmation fails, nothing installed; the right run pairs"
    finally:
        box.close()


# ---------------------------------------------------------------------------------------- 5
def test_the_journal_holds_no_secret():
    box = Box()
    secrets_seen = []
    try:
        code = box.svc.open()["code"]
        try:
            box.client(_wrong(code), key_id="wrong-1").pair()
        except PairFailed:
            pass
        got = box.client(code, x=987654321).pair()
        secrets_seen += [code, got["plain"]["http_token"], APP.split()[1], got["step1"]["pB"][:40],
                         got["step1"]["cB"], got["step1"]["session"], got["step2"]["sealed"]["box"][:40],
                         format(S.message_a(987654321, S.w_of(code)), "x")[:40]]
        box.svc.open()
        box.now[0] += 400
        box.svc.status()
        box.svc.open()
        box.svc.close()
        with open(box.tree.journal) as fh:
            text = fh.read()
        for s in secrets_seen:
            assert s not in text, f"the journal holds a secret ({s[:6]}...)"
        rows = box.journal()
        verbs = [r["verb"] for r in rows]
        for v in ("pair open", "pair try", "pair abandoned", "pair paired", "pair expired", "pair closed"):
            assert v in verbs, (v, verbs)
        paired = next(r for r in rows if r["verb"] == "pair paired")
        assert paired["key"] == "tandemapp-1" and paired["from"] == NET and paired["via"] == "http"
        assert paired["fingerprint"] == hostkey.fingerprint(blob_of(APP)) and paired["exit"] == 0
        assert next(r for r in rows if r["verb"] == "pair open")["via"] == "local"
        assert all(set(r) <= {"at", "verb", "args", "exit", "ms", "via", "from", "key", "fingerprint", "error",
                              "tries_left", "state", "reason", "added", "name"} for r in rows), rows
        return f"{len(rows)} lines, {len(secrets_seen)} secrets absent"
    finally:
        box.close()


# ---------------------------------------------------------------------------------------- 6
def test_open_and_close():
    box = Box()
    try:
        key = open(box.svc.local_key_file).read().strip()
        assert stat.S_IMODE(os.stat(box.svc.local_key_file).st_mode) == 0o600 and len(key) == 64
        c = Client(box.srv.port, "000000", APP, "x")
        st, doc, _ = c.post("open", {})
        assert (st, doc["error"]["type"]) == (403, "local_only"), doc
        box.srv.set_peer("127.0.0.1")
        for hdr in ({}, {pair_http.KEY_HEADER: "0" * 64}, {pair_http.KEY_HEADER: "é"}):
            c.headers = hdr
            st, doc, _ = c.post("open", {})
            assert (st, doc["error"]["type"]) == (403, "window_key"), (hdr, doc)
        c.headers = {pair_http.KEY_HEADER: key, "X-Forwarded-For": "203.0.113.4"}
        st, doc, _ = c.post("open", {})
        assert (st, doc["error"]["type"]) == (403, "local_only"), doc          # relayed: not this host
        c.headers = {pair_http.KEY_HEADER: key}
        st, first, h = c.post("open", {})
        assert st == 200 and h.get("Cache-Control") == "no-store", first
        valid("open", first)
        st, second, _ = c.post("open", {})
        assert st == 200 and box.svc.status()["state"] == "open"
        rows = box.journal()
        assert any(r["verb"] == "pair closed" and r.get("reason") == "replaced" for r in rows), rows
        box.srv.set_peer(NET)
        if first["code"] != second["code"]:
            try:
                box.client(first["code"]).pair()
                raise AssertionError("the replaced window's code paired")
            except PairFailed as exc:
                assert exc.kind == "confirmation", exc
        box.srv.set_peer("127.0.0.1")
        st, doc, _ = c.post("close", {})
        assert st == 200 and doc == {"pair": "engine-pair-v1", "closed": True}, doc
        valid("close", doc)
        box.srv.set_peer(NET)
        try:
            box.client(second["code"]).pair()
            raise AssertionError("a closed window paired")
        except PairFailed as exc:
            assert (exc.kind, exc.status) == ("no_window", 404), exc
        st, doc = box.srv.get()
        assert st == 200 and doc["open"] is False and doc["state"] == "closed", doc
        _nothing_installed(box)
        return "403 local_only / window_key; open, replace, close; 404 no_window"
    finally:
        box.close()


# ---------------------------------------------------------------------------------------- 7
def test_the_refusals():
    out = []
    checkout = Box(install=False)
    try:
        try:
            checkout.svc.open()
            raise AssertionError("a checkout opened a window")
        except PairError as exc:
            assert (exc.kind, exc.status) == ("no_install", 409), exc
        out.append("no_install")
    finally:
        checkout.close()
    box = Box()
    try:
        empty = os.path.join(box.tmp, "empty-etc")
        os.makedirs(empty)
        box.svc.etc = empty
        try:
            box.svc.open()
            raise AssertionError("no host key opened a window")
        except PairError as exc:
            assert exc.kind == "no_host_key", exc
        box.svc.etc = box.etc
        out.append("no_host_key")
        ssh = os.path.dirname(box.auth)
        elsewhere = os.path.join(box.tmp, "elsewhere")
        os.makedirs(elsewhere)
        os.symlink(elsewhere, ssh)
        try:
            box.svc.open()
            raise AssertionError("a linked ~/.ssh opened a window")
        except PairError as exc:
            assert exc.kind == "ssh_dir_link", exc
        os.unlink(ssh)
        out.append("ssh_dir_link")
        # a key with a line of its own already (an operator's login line): 409, nothing changed
        os.makedirs(ssh, mode=0o700)
        os.chmod(ssh, 0o700)
        before = f"# mine\nssh-ed25519 {APP.split()[1]} me@laptop\n"
        with open(box.auth, "w") as fh:
            fh.write(before)
        os.chmod(box.auth, 0o600)
        code = box.svc.open()["code"]
        try:
            box.client(code).pair()
            raise AssertionError("a key with its own line paired")
        except PairFailed as exc:
            assert (exc.kind, exc.status) == ("key_present", 409), exc
            valid("error", exc.body)
        with open(box.auth) as fh:
            assert fh.read() == before
        st = box.svc.status()
        assert st["state"] == "open" and st["tries_left"] == 4, st
        assert not os.path.exists(box.tree.http_token)
        out.append("key_present (file unchanged, window open, 4 tries left)")
        # an install that fails another way: 500 install_failed, the window still open (not stuck)
        real = keys.install

        def broken(*a, **k):
            raise OSError(5, "I/O error")
        keys.install = broken
        try:
            box.client(code, key_id="tandemapp-9", key=ed25519_line("third")).pair()
            raise AssertionError("a failed install paired")
        except PairFailed as exc:
            assert (exc.kind, exc.status) == ("install_failed", 500), exc
            valid("error", exc.body)
        finally:
            keys.install = real
        st = box.svc.status()
        assert st["state"] == "open" and st["tries_left"] == 3, st
        out.append("install_failed (window open, 3 tries left)")
        return ", ".join(out)
    finally:
        box.close()


# ---------------------------------------------------------------------------------------- 8
def test_the_rate_limit():
    t = [0.0]
    lim = pair_http.Limit(clock=lambda: t[0])
    assert all(lim.take("192.0.2.1") == 0 for _ in range(20))
    wait = lim.take("192.0.2.1")
    assert 1 <= wait <= 60, wait
    assert lim.take("192.0.2.2") == 0
    t[0] = 61
    assert lim.take("192.0.2.1") == 0
    total = pair_http.Limit(per_peer=100, total=3, clock=lambda: t[0])
    assert [total.take(f"10.0.0.{i}") == 0 for i in range(4)] == [True, True, True, False]
    box = Box()
    try:
        for _ in range(20):
            assert box.srv.get()[0] == 200
        c = Client(box.srv.port, "000000", APP, "x")
        st, doc, h = c.post("1", {})
        assert st == 429 and doc["error"]["type"] == "rate_limited" and int(h["Retry-After"]) >= 1, (st, doc)
        box.srv.set_peer("192.0.2.8")
        assert box.srv.get()[0] == 200
        box.srv.set_peer("127.0.0.1")
        assert all(box.srv.get()[0] == 200 for _ in range(30))
        return "20 a minute an address, then 429 Retry-After; another address and this host pass"
    finally:
        box.close()


# ---------------------------------------------------------------------------------------- 9
def test_tandem_pair():
    box = Box()
    try:
        box.srv.set_peer("127.0.0.1")
        out = io.StringIO()
        assert pair_cli.main(["--port", str(box.srv.port)], tree=box.tree, out=out) == 0
        lines = out.getvalue().splitlines()
        code = re.fullmatch(r"  Code:\s+([0-9]{6})", lines[-3])
        assert code, lines
        assert lines[-2].startswith("  Fingerprint:   " + hostkey.preferred(box.etc)["fingerprint"]), lines
        assert re.match(r"  Address:\s+\S+:8123", lines[-1]) or "this machine only" in lines[-1], lines
        assert "5 tries, single use" in lines[0], lines
        out = io.StringIO()
        assert pair_cli.main(["--port", str(box.srv.port), "--status"], tree=box.tree, out=out) == 0
        assert out.getvalue().startswith("[pair] window: open, 300 s and 5 tries left"), out.getvalue()
        assert code.group(1) not in out.getvalue()
        out = io.StringIO()
        assert pair_cli.main(["--port", str(box.srv.port), "--json"], tree=box.tree, out=out) == 0
        valid("open", json.loads(out.getvalue()))
        out = io.StringIO()
        assert pair_cli.main(["--port", str(box.srv.port), "--close"], tree=box.tree, out=out) == 0
        assert out.getvalue().strip() == "[pair] the window is closed", out.getvalue()
        # an engine bound to loopback says so
        box.svc.host = "127.0.0.1"
        out = io.StringIO()
        assert pair_cli.main(["--port", str(box.srv.port)], tree=box.tree, out=out) == 0
        assert out.getvalue().splitlines()[-1] == "  Address:       127.0.0.1:8123   (this machine only)"
        assert "Set HOST=0.0.0.0 in run/local.env" in out.getvalue()
        # nothing answers: exit 1, one line
        port = box.srv.port
        box.srv.close()
        out = io.StringIO()
        assert pair_cli.main(["--port", str(port)], tree=box.tree, out=out) == 1
        assert out.getvalue().startswith("[pair] the engine does not answer on 127.0.0.1:") and \
            len(out.getvalue().splitlines()) == 1, out.getvalue()
        box.srv = PairServer(box.svc)            # for close()
        return "code, fingerprint, address last; --status, --json, --close; loopback note; no engine exit 1"
    finally:
        box.close()


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
