"""6.3b (OPS-234, VIS-E19): App settings on the controller (proxy/appsettings.py, proxy/adminpw.py,
proxy/app.py): the admin password, the guard of every fleet write, the export and the import, the
permissions per platform.

  1  the password file: scrypt (or PBKDF2) with a salt, mode 0600, a wrong one refused, a broken
     file a password nobody knows, a removal keeps a fresh salt, the cap over every address
                                                                           test_password_file
  2  set, change, remove through the API: the first one from this machine only, then `current`
     always (403 wrong, 429 too many); every session before a change ends, this browser gets a
     new cookie (HttpOnly, SameSite=Strict, Secure behind https); writes from the LAN need the
     session; reads keep their rule; the password never in an answer or a log
                                                                           test_password_api
  3  the admin token beside it: its bearer and sign-in keep working, its sessions keep working
     while no password is set, end when one is, and do not come back when it is removed
                                                                           test_token_compatibility
  4  sign-in brute force: five a minute per address, thirty a minute from anywhere for the
     password (a forged X-Real-IP spreads nothing), the token never held by the latter
                                                                           test_rate_limits
  5  the guard of every write: another site or origin 403 (trusted-local included), a Host that
     is not loopback is not this machine (DNS rebinding), a body over 4 MiB 413 unread
                                                                           test_write_guard
  6  the export: nodes, deployments, rules, layouts, settings; no trust, key, route or secret;
     the pin as its SHA-256 only; a download                                test_export
  7  the import: the plan item by item, 428 without the confirm, 409 spent or the registry
     changed, applied: a node added unpaired, an alias changed, a rule changed, a deployment
     added stopped, conflicts skipped; replace removes what is safe; the controller's own node
     and identity untouched; every answer validates                          test_import
  8  the permissions: macOS Local Network denied under launchd with its fallback, granted;
     Linux: not applicable; a key file too open; the service as systemd and launchd say it
                                                                           test_permissions
  9  Take the fleet signs in with a password set in App settings (no bearer for it)
                                                                           test_take_signs_in

    python tests/test_app_settings.py
"""

from __future__ import annotations

import base64
import json
import os
import stat
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_fleet as tf  # noqa: E402
from proxy import adminpw, appsettings, ctlmove  # noqa: E402
from proxy import app as proxy_app  # noqa: E402
from proxy_util import Proxy, run  # noqa: E402
from server import auth as auth_mod  # noqa: E402

TOKEN = "app-admin-token-0123456789abcdef01234"
PW = "correct horse battery"
PW2 = "another long passphrase"
LAN = {"X-Real-IP": "192.0.2.77"}
ok = tf.ok


def _cookie(headers: dict) -> str | None:
    c = headers.get("Set-Cookie") or ""
    k, _, v = c.split(";", 1)[0].partition("=")
    return v if k == auth_mod.COOKIE else None


def _with(cookie: str | None, h: dict | None = None) -> dict:
    return {**(h or {}), **({"Cookie": f"{auth_mod.COOKIE}={cookie}"} if cookie else {})}


def _req(px, method, path, body=None, headers=None):
    st, h, out = px.request(method, path, body, headers=headers)
    return st, h, (json.loads(out) if out else None)


def _fleet(px):
    return tf.fleet_on(px, tf.local_node(tf.fake_root()))


# ---------------------------------------------------------------------------- 1

def test_password_file():
    d = tempfile.mkdtemp(prefix="pw-")
    path = os.path.join(d, "admin.json")
    pw = adminpw.Password(path)
    assert not pw.is_set and pw.material() is None and not pw.verify(PW)
    for bad in ("short", "x" * 1025, "tab\there and long", "            ", None):
        assert adminpw.check_new(bad), bad
    pw.set(PW)
    assert pw.is_set and pw.set_at
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    doc = json.load(open(path))
    assert PW not in open(path).read()
    assert doc["kdf"] in ("scrypt", "pbkdf2-sha256") and len(base64.b64decode(doc["salt"])) == 16
    assert pw.verify(PW) and not pw.verify(PW2) and not pw.verify("")
    m1 = pw.material()
    # another process (the command) changes the file: read again
    other = adminpw.Password(path)
    other.set(PW2)
    assert pw.verify(PW2) and not pw.verify(PW) and pw.material() != m1
    # a removal keeps a fresh salt: no password, and a key of its own
    m2 = pw.material()
    assert pw.remove() and not pw.is_set and not pw.verify(PW2)
    assert pw.material() not in (None, m1, m2)
    assert not pw.remove()
    # a broken file: a password nobody knows, never "no password"
    with open(path, "w") as f:
        f.write("{not json")
    assert pw.is_set and not pw.verify(PW2) and pw.material()
    # the cap over every address
    pw.set(PW)
    for _ in range(adminpw.GLOBAL_FAILS):
        assert not pw.verify("wrong guess number")
    assert pw.held() and not pw.verify(PW), "held: even the right one waits"
    clock = [1000.0]
    pw2 = adminpw.Password(path, clock=lambda: clock[0])
    for _ in range(adminpw.GLOBAL_FAILS):
        pw2.verify("wrong guess number")
    assert pw2.held()
    clock[0] += adminpw.GLOBAL_WINDOW_S + 1
    assert not pw2.held() and pw2.verify(PW)
    # the command, without the current one
    assert adminpw.main(["status", "--state-dir", d]) == 0
    assert adminpw.main(["remove", "--state-dir", d]) == 0 and not adminpw.Password(path).is_set


# ---------------------------------------------------------------------------- 2

def test_password_api():
    with Proxy() as px:
        _fleet(px)
        nid = tf.get(px, "/v1/fleet/nodes")[1]["nodes"][0]["id"]
        st, _, v = _req(px, "GET", "/v1/fleet/app/password")
        assert st == 200 and not ok(v, "app-password")["set"] and v["this_machine"]
        # the first one: never from the LAN (no password: pairing's rule, this machine only)
        st, _, err = _req(px, "PUT", "/v1/fleet/app/password", {"password": PW}, LAN)
        assert st == 401 and "this machine" in ok(err, "error")["error"]["message"]
        st, _, err = _req(px, "PUT", "/v1/fleet/app/password", {"password": "short"})
        assert st == 400 and "12 to 1024" in err["error"]["message"]
        st, _, err = _req(px, "PUT", "/v1/fleet/app/password", {"password": PW, "x": 1})
        assert st == 400
        st, h, v = _req(px, "PUT", "/v1/fleet/app/password", {"password": PW})
        assert st == 200 and ok(v, "app-password")["set"] and v["password"] and not v["token"]
        c1 = _cookie(h)
        sc = h["Set-Cookie"]
        assert c1 and "HttpOnly" in sc and "SameSite=Strict" in sc and "Path=/" in sc
        # the session: LAN writes need it, reads keep the rule (login off: open)
        assert tf.get(px, "/v1/fleet/nodes", LAN)[0] == 200
        assert _req(px, "PATCH", f"/v1/fleet/nodes/{nid}", {"alias": "Desk"}, LAN)[0] == 401
        assert _req(px, "PATCH", f"/v1/fleet/nodes/{nid}", {"alias": "Desk"},
                    _with(c1, LAN))[0] == 200
        s = tf.get(px, "/v1/fleet/session", _with(c1, LAN))[1]
        assert ok(s, "session")["password_set"] and s["authenticated"]
        # sign in from another browser with it
        st, h, _ = _req(px, "POST", "/v1/fleet/session", {"token": PW}, LAN)
        assert st == 204
        c2 = _cookie(h)
        assert _req(px, "POST", "/v1/fleet/session", {"token": PW2}, LAN)[0] == 401
        # a change needs `current`, even from this machine
        st, _, err = _req(px, "PUT", "/v1/fleet/app/password", {"password": PW2})
        assert st == 403 and ok(err, "error")["error"]["type"] == "wrong_password"
        st, _, err = _req(px, "PUT", "/v1/fleet/app/password",
                          {"current": "not it at all", "password": PW2}, _with(c1, LAN))
        assert st == 403
        # the second browser changes it behind https: its new cookie is Secure; every other ends
        st, h, v = _req(px, "PUT", "/v1/fleet/app/password", {"current": PW, "password": PW2},
                        _with(c2, {**LAN, "X-Forwarded-Proto": "https"}))
        assert st == 200 and "Secure" in h["Set-Cookie"]
        c3 = _cookie(h)
        for c in (c1, c2):
            assert _req(px, "PATCH", f"/v1/fleet/nodes/{nid}", {"alias": "Desk 2"},
                        _with(c, LAN))[0] == 401
        assert _req(px, "PATCH", f"/v1/fleet/nodes/{nid}", {"alias": "Desk 2"},
                    _with(c3, LAN))[0] == 200
        # a LAN page without a session cannot change it
        assert _req(px, "PUT", "/v1/fleet/app/password", {"current": PW2, "password": PW},
                    LAN)[0] == 401
        # the page's view carries the password's state
        app = tf.get(px, "/v1/fleet/app", LAN)[1]
        assert ok(app, "app")["password"]["set"] and app["password"]["set_at"]
        # remove: with no token, the controller is open again and the cookie is cleared
        st, _, err = _req(px, "DELETE", "/v1/fleet/app/password", {"current": PW}, _with(c3, LAN))
        assert st == 403
        st, h, v = _req(px, "DELETE", "/v1/fleet/app/password", {"current": PW2}, _with(c3, LAN))
        assert st == 200 and not ok(v, "app-password")["set"] and "Max-Age=0" in h["Set-Cookie"]
        assert _req(px, "PATCH", f"/v1/fleet/nodes/{nid}", {"alias": "Desk"}, LAN)[0] == 200
        assert _req(px, "DELETE", "/v1/fleet/app/password", {"current": PW2})[0] == 409
        # never in an answer, a log or the audit
        text = "\n".join(px.logs) + json.dumps(px.svc.cfg.audit(200))
        assert PW not in text and PW2 not in text
        assert any("admin password set" in x for x in px.logs)


# ---------------------------------------------------------------------------- 3

def test_token_compatibility():
    with Proxy(env={"TANDEM_ADMIN_TOKEN": TOKEN}) as px:
        _fleet(px)
        nid = tf.get(px, "/v1/fleet/nodes")[1]["nodes"][0]["id"]
        bearer = {"Authorization": f"Bearer {TOKEN}"}
        st, h, _ = _req(px, "POST", "/v1/fleet/session", {"token": TOKEN}, LAN)
        assert st == 204
        old = _cookie(h)
        assert _req(px, "PATCH", f"/v1/fleet/nodes/{nid}", {"alias": "A"}, _with(old, LAN))[0] == 200
        v = tf.get(px, "/v1/fleet/app/password", LAN)[1]
        assert v["set"] and v["token"] and not v["password"]
        # set a password with the token as `current` (from the LAN, signed in)
        st, h, v = _req(px, "PUT", "/v1/fleet/app/password", {"current": TOKEN, "password": PW},
                        _with(old, LAN))
        assert st == 200 and v["token"] and v["password"]
        new = _cookie(h)
        assert _req(px, "PATCH", f"/v1/fleet/nodes/{nid}", {"alias": "B"}, _with(old, LAN))[0] == 401
        assert _req(px, "PATCH", f"/v1/fleet/nodes/{nid}", {"alias": "B"}, _with(new, LAN))[0] == 200
        # the token: still a bearer, still a password at sign-in; the password never a bearer
        assert _req(px, "PATCH", f"/v1/fleet/nodes/{nid}", {"alias": "C"}, {**LAN, **bearer})[0] == 200
        assert _req(px, "PATCH", f"/v1/fleet/nodes/{nid}", {"alias": "C"},
                    {**LAN, "Authorization": f"Bearer {PW}"})[0] == 401
        assert _req(px, "POST", "/v1/fleet/session", {"token": TOKEN}, LAN)[0] == 204
        assert _req(px, "POST", "/v1/dashboard/session", {"token": PW}, LAN)[0] in (204, 404)
        # removed: the token stays; neither the sessions of before nor those with it come back
        st, h, v = _req(px, "DELETE", "/v1/fleet/app/password", {"current": PW}, _with(new, LAN))
        assert st == 200 and v["set"] and v["token"] and not v["password"]
        latest = _cookie(h)
        for c in (old, new):
            assert _req(px, "PATCH", f"/v1/fleet/nodes/{nid}", {"alias": "D"}, _with(c, LAN))[0] == 401
        assert _req(px, "PATCH", f"/v1/fleet/nodes/{nid}", {"alias": "D"}, _with(latest, LAN))[0] == 200
        assert _req(px, "DELETE", "/v1/fleet/app/password", {"current": TOKEN},
                    _with(latest, LAN))[0] == 409
        assert TOKEN not in "\n".join(px.logs)
    # a controller that never had a password: its token sessions are the same as before
    a = auth_mod.Auth(TOKEN, password=adminpw.Password(os.path.join(tempfile.mkdtemp(), "a.json")))
    b = auth_mod.Auth(TOKEN)
    value, _ = b.make_cookie()
    assert a.session({"Cookie": f"{auth_mod.COOKIE}={value}"}) is not None


# ---------------------------------------------------------------------------- 4

def test_rate_limits():
    with Proxy(env={"TANDEM_ADMIN_TOKEN": TOKEN}) as px:
        _fleet(px)
        assert _req(px, "PUT", "/v1/fleet/app/password", {"current": TOKEN, "password": PW})[0] == 200
        one = {"X-Real-IP": "192.0.2.10"}
        for _ in range(auth_mod.LOGIN_FAILS):
            assert _req(px, "POST", "/v1/fleet/session", {"token": "wrong guess xx"}, one)[0] == 401
        st, h, _ = _req(px, "POST", "/v1/fleet/session", {"token": PW}, one)
        assert st == 429 and h.get("Retry-After") == "60"
        # a forged address per guess: the cap over every address holds the password
        a = proxy_app.STATE["auth"]
        n = 0
        while not a.password.held():
            ip = {"X-Real-IP": f"198.51.100.{n % 250}", "X-Forwarded-For": f"203.0.113.{n // 250}"}
            st = _req(px, "POST", "/v1/fleet/session", {"token": f"guess {n:06d} xx"}, ip)[0]
            assert st == 401, st
            n += 1
        assert n <= adminpw.GLOBAL_FAILS
        assert _req(px, "POST", "/v1/fleet/session", {"token": PW}, {"X-Real-IP": "192.0.2.99"})[0] == 429
        # the token still signs in
        assert _req(px, "POST", "/v1/fleet/session", {"token": TOKEN}, {"X-Real-IP": "192.0.2.98"})[0] == 204


# ---------------------------------------------------------------------------- 5

def test_write_guard():
    with Proxy() as px:
        _fleet(px)
        nid = tf.get(px, "/v1/fleet/nodes")[1]["nodes"][0]["id"]
        host = f"127.0.0.1:{px.port}"
        for h in ({"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"},
                  {"Origin": "null"}):
            st, _, err = _req(px, "PATCH", f"/v1/fleet/nodes/{nid}", {"alias": "X"}, h)
            assert st == 403 and ok(err, "error")["error"]["type"] in ("cross_origin", "cross_site"), h
            assert _req(px, "PUT", "/v1/fleet/app/password", {"password": PW}, h)[0] == 403
        # the configuration's and the conductor's writes: the same guard (an open controller on
        # this machine answers a page from anywhere otherwise)
        st, _, err = _req(px, "POST", "/v1/config/backends", {"id": "evil", "kind": "openai",
                                                              "url": "https://evil.example/v1"},
                          {"Origin": "https://evil.example"})
        assert st == 403 and px.svc.cfg.backend("evil") is None
        assert _req(px, "PUT", "/v1/conductor/x", {}, {"Sec-Fetch-Site": "cross-site"})[0] == 403
        # JSON nested past the parser's depth: 400, not a dropped connection
        st, _, err = _req(px, "POST", "/v1/fleet/app/import", b"[" * 100000 + b"]" * 100000)
        assert st == 400, st
        # a form's text/plain POST of JSON: refused on the App routes
        st, _, err = _req(px, "POST", "/v1/fleet/app/import", {"config": {}},
                          {"Content-Type": "text/plain"})
        assert st == 415
        # the page itself: same origin
        assert _req(px, "PATCH", f"/v1/fleet/nodes/{nid}", {"alias": "Y"},
                    {"Origin": f"http://{host}", "Sec-Fetch-Site": "same-origin"})[0] == 200
        # DNS rebinding: loopback peer, a name that is not this machine's loopback
        assert _req(px, "PUT", "/v1/fleet/app/password", {"password": PW},
                    {"Host": "rebind.example:80"})[0] == 401
        assert _req(px, "PUT", "/v1/fleet/app/password", {"password": PW},
                    {"Host": f"localhost:{px.port}"})[0] == 200
        assert auth_mod.loopback_host("127.0.0.1:8080") and auth_mod.loopback_host("[::1]:80")
        assert not auth_mod.loopback_host("127.0.0.1.evil.example")
        assert not auth_mod.loopback_host("evil.example")
        # oversized: 413 before the guard reads it
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", px.port, timeout=10)
        c.putrequest("POST", "/v1/fleet/app/import")
        c.putheader("Content-Type", "application/json")
        c.putheader("Content-Length", str(proxy_app.FLEET_BODY_MAX + 1))
        c.endheaders()
        r = c.getresponse()
        assert r.status == 413 and json.loads(r.read())["error"]["type"] == "too_large"
        c.close()


# ---------------------------------------------------------------------------- 6, 7

def _seed(px, f):
    """A fleet of the box, an unpaired node and a paired one (a pin, a key path), a deployment
    record (stopped), a rule edit, a layout, an app setting."""
    cfg = px.svc.cfg
    lid = f.ensure_local()
    cfg.add_node({"id": "n-gold", "alias": "Gold", "endpoint": "https://192.0.2.10:7700",
                  "address": "192.0.2.10", "colour": 2,
                  "trust": {"kind": "mtls", "cert": "/secret/ctl.crt", "key": "/secret/ctl.key",
                            "pin": "-----BEGIN CERTIFICATE-----PEM", "fingerprint": "ab" * 32,
                            "host_key": "ssh-ed25519 AAAA", "tunnel": None}})
    cfg.add_node({"id": "n-loose", "alias": "Loose", "endpoint": "https://192.0.2.11:7700",
                  "address": "192.0.2.11", "colour": 3})
    cfg.put_deployment({"id": "wren", "name": "Wren", "model": "wren-1", "pack": "", "type":
                        "single", "members": [{"node": "n-gold", "engine": None, "backend": None,
                                               "model": "wren-1"}], "stages": [],
                        "options": {"api_id": "wren-1", "ctx": 0}, "state": "stopped"})
    f.watch.edit("gpu-temp", {"threshold": 80.0}, "test", [lid, "n-gold"])
    lay = dict(tf.fleet_mod.default_layout("overview", "desk"), saved_at=None)
    cfg.set_layout(dict(tf.fleet_mod.check_layout(lay, "overview", "desk"), saved_at=1.0))
    cfg.set_setting("app.theme", "dark")
    cfg.set_setting("collect_token", "secret-collect-token-123")
    return lid


def test_export():
    with Proxy() as px:
        f = _fleet(px)
        lid = _seed(px, f)
        st, h, doc = _req(px, "GET", "/v1/fleet/app/export")
        assert st == 200 and "attachment" in h["Content-Disposition"]
        ok(doc, "fleet-config")
        assert doc["secrets"] == "excluded" and doc["excluded"]
        text = json.dumps(doc)
        for secret in ("/secret/ctl.key", "/secret/ctl.crt", "BEGIN CERTIFICATE", "ssh-ed25519",
                       "secret-collect-token-123", '"trust"', '"endpoint"'):
            assert secret not in text, secret
        by = {n["id"]: n for n in doc["nodes"]}
        assert by["n-gold"]["paired"] and by["n-gold"]["pin_sha256"] == "ab" * 32
        assert not by["n-loose"]["paired"] and by["n-loose"]["pin_sha256"] is None
        assert by[lid]["controller_host"] and not by["n-gold"]["controller_host"]
        assert [d["id"] for d in doc["deployments"]] == ["wren"]
        assert next(r for r in doc["rules"] if r["id"] == "gpu-temp")["threshold"] == 80.0
        assert doc["layouts"][0]["page"] == "overview" and doc["settings"] == {"app.theme": "dark"}
        assert doc["from"]["fingerprint"] and doc["from"]["node"] == lid
        # write rights: the LAN needs the session once a password is set
    with Proxy(env={"TANDEM_ADMIN_TOKEN": TOKEN}) as px:
        _fleet(px)
        assert _req(px, "GET", "/v1/fleet/app/export", None, LAN)[0] == 401
        assert tf.get(px, "/v1/fleet/app", LAN)[0] == 200


def test_import():
    with Proxy() as px:
        f = _fleet(px)
        lid = _seed(px, f)
        doc = _req(px, "GET", "/v1/fleet/app/export")[2]
        local_before = px.svc.cfg.node(lid)
        fp_before = f.trust().fingerprint()
        # the file another controller would give: one node renamed, one new, one behind a
        # tunnel, an alias clash, a rule changed, a deployment new and one that clashes
        cfg2 = json.loads(json.dumps(doc))
        cfg2["from"] = {"name": "TandemApp on other", "fingerprint": "cd" * 32, "node": "n-other"}
        nodes = {n["id"]: n for n in cfg2["nodes"]}
        nodes["n-gold"]["alias"] = "Golden"
        nodes[lid]["alias"] = "Box"
        cfg2["nodes"] += [
            {"id": "n-new", "alias": "Newbie", "colour": 2, "icon": "rack", "tags": ["lab"],
             "class": "spark", "hw": None, "address": "192.0.2.20", "transport": "lan",
             "lease_end_ms": None, "cost_per_hour": None, "controller_host": True,
             "paired": True, "pin_sha256": "ef" * 32},
            {"id": "n-tun", "alias": "Rented", "colour": 4, "icon": "cloud", "tags": [],
             "class": "spark", "hw": None, "address": "198.51.100.5", "transport": "tunnel",
             "lease_end_ms": None, "cost_per_hour": 2.5, "controller_host": False,
             "paired": True, "pin_sha256": None},
            {"id": "n-clash", "alias": "loose", "colour": 5, "icon": "desk", "tags": [],
             "class": "spark", "hw": None, "address": "192.0.2.21", "transport": "lan",
             "lease_end_ms": None, "cost_per_hour": None, "controller_host": False,
             "paired": False, "pin_sha256": None}]
        next(r for r in cfg2["rules"] if r["id"] == "mem-avail")["enabled"] = False
        cfg2["deployments"].append({"id": "big", "state": "serving", "name": "Big", "model": "big-1",
                                    "pack": "", "type": "single",
                                    "members": [{"node": "n-new", "engine": None, "backend": None,
                                                 "model": "big-1"}],
                                    "stages": [], "options": {"api_id": "big-1", "ctx": 0}})
        cfg2["deployments"].append({"id": "spacey", "state": "stopped", "name": "Spacey",
                                    "model": "s", "pack": "", "type": "single",
                                    "members": [{"node": "n-new"}], "stages": [],
                                    "options": {"api_id": "has space", "run": "rm -rf"}})
        cfg2["deployments"].append({"id": "ghost", "state": "stopped", "name": "Ghost",
                                    "model": "g", "pack": "", "type": "single",
                                    "members": [{"node": "n-nowhere"}], "stages": [],
                                    "options": {}})
        # 400s: not a file, a move's export, bad mode
        assert _req(px, "POST", "/v1/fleet/app/import", {"config": {"kind": "x"}})[0] == 400
        st, _, err = _req(px, "POST", "/v1/fleet/app/import",
                          {"config": {"kind": ctlmove.KIND, "version": 1}})
        assert st == 400 and "Take the fleet" in err["error"]["message"]
        assert _req(px, "POST", "/v1/fleet/app/import", {"config": cfg2, "mode": "all"})[0] == 400
        st, _, p = _req(px, "POST", "/v1/fleet/app/import", {"config": cfg2})
        assert st == 200, p
        ok(p, "import-plan")
        assert p["allowed"] and p["confirm"] and not p["from"]["same_controller"]
        ops = {(i["kind"], i["id"]): i for i in p["items"]}
        assert ops[("node", "n-gold")]["op"] == "change" and ops[("node", "n-gold")]["fields"] == ["alias"]
        assert ops[("node", "n-gold")]["detail"] == "alias Gold → Golden"
        assert ops[("node", lid)]["op"] == "change" and "own machine" in ops[("node", lid)]["detail"]
        add = ops[("node", "n-new")]
        assert add["op"] == "add" and "unpaired" in add["detail"] and "colour 2" in add["detail"]
        assert ops[("node", "n-tun")]["op"] == "conflict" and "tunnel" in ops[("node", "n-tun")]["detail"]
        assert ops[("node", "n-clash")]["op"] == "conflict"
        assert ops[("node", "n-loose")]["op"] == "keep"
        assert ops[("rule", "mem-avail")]["op"] == "change"
        assert ops[("rule", "mem-avail")]["detail"] == "enabled on → off"
        assert ops[("deployment", "big")]["op"] == "add" and "stopped" in ops[("deployment", "big")]["detail"]
        assert ops[("deployment", "ghost")]["op"] == "conflict"
        assert ops[("deployment", "spacey")]["op"] == "conflict" and \
            "options" in ops[("deployment", "spacey")]["detail"]
        bad_layout = dict(cfg2, layouts=[{"page": "x\ny", "device": "desk", "doc": {}}])
        assert _req(px, "POST", "/v1/fleet/app/import", {"config": bad_layout})[0] == 400
        assert ops[("deployment", "wren")]["op"] == "keep"
        assert "certificate" in p["identity"]
        # 428 without the confirm, 409 another file's
        st, _, err = _req(px, "POST", "/v1/fleet/app/import/apply", {"config": cfg2})
        assert st == 428 and err["error"]["type"] == "confirm_required"
        st, _, err = _req(px, "POST", "/v1/fleet/app/import/apply",
                          {"config": doc, "confirm": p["confirm"]})
        assert st == 409 and err["error"]["type"] == "confirm_invalid"
        # spent by that try? no: a confirm is spent only by its own file
        st, _, p = _req(px, "POST", "/v1/fleet/app/import", {"config": cfg2})
        # the registry changes after the plan: 409
        px.svc.cfg.update_node("n-loose", {"alias": "Looser"})
        st, _, err = _req(px, "POST", "/v1/fleet/app/import/apply",
                          {"config": cfg2, "confirm": p["confirm"]})
        assert st == 409 and "changed" in err["error"]["message"]
        st, _, p = _req(px, "POST", "/v1/fleet/app/import", {"config": cfg2})
        st, _, res = _req(px, "POST", "/v1/fleet/app/import/apply",
                          {"config": cfg2, "confirm": p["confirm"]})
        assert st == 200, res
        ok(res, "import-result")
        # added: Newbie and Big; changed: Gold, the box, Loose (back from Looser), mem-avail
        assert res["applied"] == {"add": 2, "change": 4, "remove": 0}, res["applied"]
        assert res["skipped"] == 4
        # spent
        assert _req(px, "POST", "/v1/fleet/app/import/apply",
                    {"config": cfg2, "confirm": p["confirm"]})[0] == 409
        cfg = px.svc.cfg
        new = cfg.node("n-new")
        assert new["trust"] == {} and new["endpoint"] == "https://192.0.2.20:7700"
        assert new["alias"] == "Newbie" and new["colour"] != 2 and new["tags"] == ["lab"]
        assert tf.get(px, "/v1/fleet/nodes/n-new")[1]["pairing"]["state"] == "unpaired"
        assert cfg.node("n-gold")["alias"] == "Golden"
        assert cfg.node("n-gold")["trust"]["key"] == "/secret/ctl.key", "the pairing stays"
        loc = cfg.node(lid)
        assert loc["alias"] == "Box"
        assert {k: loc[k] for k in ("endpoint", "address", "trust")} == \
            {k: local_before[k] for k in ("endpoint", "address", "trust")}
        assert f.trust().fingerprint() == fp_before
        assert cfg.deployment("big")["state"] == "stopped"
        assert not next(r for r in f.watch.rules() if r["id"] == "mem-avail")["enabled"]
        ev = tf.get(px, "/v1/fleet/events?type=controller")[1]["events"]
        assert any("Fleet configuration imported" in e["text"] for e in ev)
        # the same file again: nothing to change
        st, _, p = _req(px, "POST", "/v1/fleet/app/import", {"config": cfg2})
        assert not p["allowed"] and "nothing to change" in p["blocked"] and p["confirm"] is None
        # replace with the first export: removes what is safe, keeps the rest
        st, _, p = _req(px, "POST", "/v1/fleet/app/import", {"config": doc, "mode": "replace"})
        ops = {(i["kind"], i["id"]): i for i in p["items"]}
        assert ops[("node", "n-new")]["op"] == "remove"
        assert ops[("deployment", "big")]["op"] == "remove"
        assert ops[("rule", "mem-avail")]["op"] == "change"
        assert ops[("node", "n-gold")]["op"] == "change"
        st, _, res = _req(px, "POST", "/v1/fleet/app/import/apply",
                          {"config": doc, "mode": "replace", "confirm": p["confirm"]})
        assert st == 200 and cfg.node("n-new") is None and cfg.deployment("big") is None
        assert cfg.node(lid)["alias"] == local_before["alias"]
        # a serving deployment is never replaced or removed; a paired node never removed
        cfg.put_deployment(dict(cfg.deployment("wren"), state="serving"))
        bare = dict(doc, nodes=[n for n in doc["nodes"] if n["id"] == lid], deployments=[])
        st, _, p = _req(px, "POST", "/v1/fleet/app/import", {"config": bare, "mode": "replace"})
        ops = {(i["kind"], i["id"]): i for i in p["items"]}
        assert ops[("deployment", "wren")]["op"] == "conflict"
        assert ops[("node", "n-gold")]["op"] == "keep" and "release" in ops[("node", "n-gold")]["detail"]
        assert ops[("node", "n-loose")]["op"] == "remove"
    # pairing's rights for the apply: from the LAN with no password, refused
    with Proxy() as px:
        f = _fleet(px)
        _seed(px, f)
        doc = _req(px, "GET", "/v1/fleet/app/export")[2]
        doc["nodes"][0]["alias"] = "Renamed"
        p = _req(px, "POST", "/v1/fleet/app/import", {"config": doc}, LAN)[2]
        assert p["confirm"]
        assert _req(px, "POST", "/v1/fleet/app/import/apply",
                    {"config": doc, "confirm": p["confirm"]}, LAN)[0] == 401


# ---------------------------------------------------------------------------- 8

def test_permissions():
    svc_launchd = {"manager": "launchd"}
    files = [{"what": "the controller's certificate key", "path": "~/x/controller.key",
              "mode": "0600", "state": "ok"}]
    rows = {r["id"]: r for r in appsettings.permissions(
        platform="darwin", lan="denied", service=svc_launchd, key_source="keychain", files=files,
        system_rules=2)}
    ln = rows["local_network"]
    assert ln["state"] == "lan_denied" and ln["applies"] and "launchd" in ln["detail"]
    assert "Spark" in ln["fallback"] and ln["action"]["kind"] == "check"
    assert rows["keys"]["state"] == "file" and "Keychain" in rows["keys"]["detail"]
    assert rows["notifications"]["state"] == "in_browser" and "2 alert rules" in rows["notifications"]["detail"]
    assert rows["node_sudo"]["state"] == "per_action" and "never stored" in rows["node_sudo"]["detail"]
    ok_mac = {r["id"]: r for r in appsettings.permissions(
        platform="darwin", lan="granted", service={"manager": "none"}, key_source="file",
        files=files, system_rules=0)}
    assert ok_mac["local_network"]["state"] == "granted" and ok_mac["local_network"]["fallback"] is None
    assert appsettings.permissions(platform="darwin", lan=None, service={}, key_source=None,
                                   files=[], system_rules=0)[0]["state"] == "unknown"
    asked = appsettings.permissions(platform="darwin", lan="not_asked", service={},
                                    key_source=None, files=[], system_rules=0)[0]
    assert asked["state"] == "not_asked" and asked["fallback"] is None, "a way around once denied"
    lin = {r["id"]: r for r in appsettings.permissions(
        platform="linux", lan="not_needed", service={}, key_source="file", files=files,
        system_rules=1)}
    assert lin["local_network"]["state"] == "not_applicable" and not lin["local_network"]["applies"]
    assert lin["keys"]["state"] == "not_applicable"
    loose = [dict(files[0], mode="0644", state="too_open")]
    k = {r["id"]: r for r in appsettings.permissions(
        platform="linux", lan="not_needed", service={}, key_source="file", files=loose,
        system_rules=1)}["keys"]
    assert k["state"] == "too_open" and k["fallback"].startswith("chmod 600")
    d = tempfile.mkdtemp()
    p = os.path.join(d, "k")
    open(p, "w").close()
    os.chmod(p, 0o644)
    assert appsettings.file_state(p, "k")["state"] == "too_open"
    os.chmod(p, 0o600)
    assert appsettings.file_state(p, "k")["state"] == "ok"
    assert appsettings.file_state(p + "x", "k")["state"] == "missing"
    # the service: systemd from /proc/self/cgroup, launchd from XPC_SERVICE_NAME
    calls = []

    def run_(argv):
        calls.append(argv)
        if argv[0] == "systemctl":
            return 0, "UnitFileState=enabled\nRestart=on-failure\n", ""
        if argv[0] == "loginctl":
            return 0, "Linger=yes\n", ""
        return 0, "\tstate = running\n\tkeepalive = true\n", ""
    cg = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/tandemapp-ctl.service\n"
    s = appsettings.service_view(platform="linux", env={"USER": "u"}, run=run_,
                                 cgroup=lambda: cg)
    assert (s["manager"], s["unit"], s["starts_at_boot"], s["restarts"], s["linger"]) == \
        ("systemd", "tandemapp-ctl.service", True, True, True)
    s = appsettings.service_view(platform="linux", env={}, run=run_,
                                 cgroup=lambda: "0::/user.slice/user-1000.slice/session-4.scope\n")
    assert s["manager"] == "none"
    s = appsettings.service_view(platform="darwin", env={"XPC_SERVICE_NAME": "org.tandemllm.app"},
                                 run=run_)
    assert s["manager"] == "launchd" and s["unit"] == "org.tandemllm.app" and s["restarts"]
    s = appsettings.service_view(platform="darwin", env={"XPC_SERVICE_NAME": "0"}, run=run_)
    assert s["manager"] == "none"
    # the route: the view validates; on Linux the check answers not applicable
    with Proxy() as px:
        f = _fleet(px)
        f._app = appsettings.AppSettings(f, platform="linux", env={}, run=run_,
                                         cgroup=lambda: cg)
        v = ok(tf.get(px, "/v1/fleet/app")[1], "app")
        assert v["platform"] == "linux" and v["service"]["unit"] == "tandemapp-ctl.service"
        st, _, v = _req(px, "POST", "/v1/fleet/app/permissions/local-network", {})
        assert st == 200 and ok(v, "app")["permissions"][0]["state"] == "not_applicable"


# ---------------------------------------------------------------------------- 9

def test_take_signs_in():
    seen = []

    def call(url, method, path, body=None, token=None, timeout=20.0, cookie=None):
        seen.append((path, token, cookie))
        if cookie != "c-1":
            return 401, {"error": {"type": "unauthorized", "message": "no"}}
        return 502, {"error": {"type": "unreachable", "message": "stop here"}}

    class F:
        cfg = None
    m = ctlmove.Mover.__new__(ctlmove.Mover)
    m.call, m.sign_in = call, (lambda url, pw: "c-1" if pw == PW else None)
    steps = []
    m._step = lambda job, sid, state, detail=None: steps.append((sid, state))
    m._fail = lambda job, sid, kind, msg: steps.append((sid, "failed", msg))
    m.fleet = F()
    m._pull({"id": "j"}, "http://192.0.2.40:8080", PW)
    assert seen[0] == ("/v1/fleet/controller", PW, None)
    assert seen[1] == ("/v1/fleet/controller", None, "c-1")
    assert steps[-1][:2] == ("check", "failed")


if __name__ == "__main__":
    run(globals())
