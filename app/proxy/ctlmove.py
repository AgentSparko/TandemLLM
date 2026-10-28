"""Where TandemApp's controller runs, and moving it (OPS-234, concept A9): This Mac or a Spark.

    c = Mover(fleet)                                  one per Fleet (fleet.mover())
    GET  /v1/fleet/controller                         this controller: its host, API URL, where it
                                                      may move to, the move job, `moved_to`
    GET  /v1/fleet/controller/move?to=<node>&port=    the plan of a move to a Spark, its confirm
    POST /v1/fleet/controller/move {to, confirm, port?, password?, name?, state_dir?}
                                                      the move job (push: this controller to a
                                                      Spark's TandemNode)
    POST /v1/fleet/controller/handover {certificate, name, api_url}
                                                      the giver's half of a pull: the export,
                                                      sealed to the taker's certificate
    POST /v1/fleet/controller/handover/{id}/commit    the giver re-pins its nodes to the taker
    POST /v1/fleet/controller/take {url, password}    the taker's half: a fleet taken from the
                                                      controller at `url` (This Mac takes it back)

THE EXPORT (`kind: tandemapp-export`): the config database without the secrets, the wizard's
sessions and the jobs (backends, models, routes, the proxy's own keys as hashes, the settings,
the nodes with their pins, the layouts), and the backends' stored API keys decrypted, for the new
controller to store under its own master key. The metric history (fleet.sqlite3) stays where it
is. A node reached through an SSH tunnel stays too: its key is a file on this machine (rented
nodes, step 7); the plan says so. An import lands only in a controller with no fleet of its own,
and every node's trust then names that controller's certificate.

A MOVE TO A SPARK (push). The Spark's TandemNode starts a controller there (POST /node/controller,
a systemd user unit with its own state dir, the export imported at its first start: proxy/app.py),
which makes its own certificate. Then each paired node gets that certificate as its owner from the
current one (PUT /node/owner): the old controller is refused from then on, the new one accepted.
The Spark itself is released here and read by the new controller over its loopback (a virtual
node stays a paired node: the machine under it is not the fleet's). The new controller is asked
for the nodes until each is up, the old one's refusal is checked, and this controller says
`moved_to`: its page follows to the new URL, the API URL printed. Each step's progress and log are
in the job (`fleet_jobs`, kind move); a controller stopped during a move finds it failed at its
next start, with the words to finish it.

A MOVE BACK (pull, This Mac). The page of TandemApp on the Mac takes the fleet from the controller
at a URL with its admin password: the giver seals its export to the taker's certificate (ECDH
P-256, HKDF-SHA256, AES-256-GCM: the keys never cross the LAN in clear), the taker imports it, the
giver hands its nodes over (its own Spark's agent too, with the address to listen on) and says
`moved_to` the taker's URL.

A MAC THAT SLEEPS. The host-sleep rule (proxy/fleetwatch.py) fires while a Mac runs the
controller; the plan and this route say it too.
"""

from __future__ import annotations

import base64
import json
import os
import secrets as _secrets
import sqlite3
import tempfile
import threading
import time
import urllib.error
import urllib.request

from proxy import _version, nodesource, nodetrust

CONTRACT = "1.0"
KIND = "tandemapp-export"
CONFIRM_S = 300.0
HANDOVER_S = 300.0
VERIFY_S = 45.0
DEFAULT_PORT = 8080
# the admin password a controller starts with (server/auth.py MIN_TOKEN)
MIN_PASSWORD = 32
# the tables an export carries (the rest: secrets, sessions, jobs, audit, meta stay)
TABLES = ("backends", "models", "model_features", "routing_rules", "users", "settings", "nodes",
          "layouts")
SKIP_SETTINGS = ("controller.", "fleet.")
STEPS = {"push": (("check", "Check {to}"), ("export", "Export the fleet"),
                  ("start", "Start the controller on {to}"),
                  ("repin", "Hand the nodes to the new controller"),
                  ("verify", "The nodes answer the new controller"),
                  ("done", "This controller points to {url}")),
         "pull": (("check", "Reach the controller at {from}"), ("export", "Its export, sealed"),
                  ("import", "Import here"), ("repin", "It hands its nodes here"),
                  ("verify", "The nodes answer this controller"))}


class MoveError(Exception):
    def __init__(self, status: int, kind: str, message: str):
        super().__init__(message)
        self.status, self.kind, self.message = status, kind, message


# ---------------------------------------------------------------------- the export

def export(cfg, *, skip_nodes=(), extra_nodes=(), keys=True) -> dict:
    """The fleet's export (THE EXPORT above). `skip_nodes`: ids that stay; `extra_nodes`: rows put
    in as they are (this machine's node as the new controller reaches it)."""
    d = tempfile.mkdtemp(prefix="tandem-export-")
    try:
        path = os.path.join(d, "export.sqlite3")
        with cfg._lock:
            dst = sqlite3.connect(path)
            cfg._conn.backup(dst)
            dst.close()
        c = sqlite3.connect(path)
        for t in ("backend_secrets", "add_sessions", "fleet_jobs", "audit"):
            if _has_table(c, t):
                c.execute(f"DELETE FROM {t}")
        for p in SKIP_SETTINGS:
            c.execute("DELETE FROM settings WHERE key LIKE ?", (p + "%",))
        c.execute("DELETE FROM nodes WHERE endpoint = 'local'")
        for nid in skip_nodes:
            c.execute("DELETE FROM nodes WHERE id = ?", (nid,))
        cols = [r[1] for r in c.execute("PRAGMA table_info(nodes)")]
        for row in extra_nodes:
            doc = {k: (json.dumps(v) if k in ("tags", "trust", "drain") and v is not None else v)
                   for k, v in row.items() if k in cols}
            c.execute(f"INSERT OR REPLACE INTO nodes({', '.join(doc)}) VALUES "
                      f"({', '.join('?' * len(doc))})", list(doc.values()))
        c.commit()
        c.execute("VACUUM")
        c.close()
        with open(path, "rb") as f:
            db = f.read()
    finally:
        for p in os.listdir(d):
            os.remove(os.path.join(d, p))
        os.rmdir(d)
    got = {}
    if keys:
        for b in cfg.backends():
            if b["id"] in cfg.env_keys:
                continue                          # the environment's: it stays with that env
            try:
                k = cfg.key_for(b["id"])
            except Exception:                                        # noqa: BLE001
                k = None
            if k:
                got[b["id"]] = k
    return {"kind": KIND, "version": 1, "release": _version.release() or None,
            "at": round(time.time(), 3), "db": base64.b64encode(db).decode(), "keys": got}


def _has_table(c, name: str) -> bool:
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                     (name,)).fetchone() is not None


def import_bundle(cfg, bundle: dict, cert: str, key: str, who: str = "controller") -> dict:
    """The export into `cfg`, which has no fleet of its own; each paired node's trust names this
    controller's certificate (`cert`, `key`). {nodes, backends, keys} counted."""
    if not isinstance(bundle, dict) or bundle.get("kind") != KIND:
        raise MoveError(400, "bad_request", "not a TandemApp export")
    if any(n["endpoint"] != "local" for n in cfg.nodes()):
        raise MoveError(409, "not_empty", "this controller has a fleet of its own: an import "
                                          "lands only on an empty one")
    d = tempfile.mkdtemp(prefix="tandem-import-")
    path = os.path.join(d, "import.sqlite3")
    try:
        with open(path, "wb") as f:
            f.write(base64.b64decode(bundle["db"]))
        n = {}
        with cfg._lock:
            c = cfg._conn
            c.execute("ATTACH DATABASE ? AS src", (path,))
            try:
                for t in TABLES:
                    have = {r[1] for r in c.execute(f"PRAGMA main.table_info({t})")}
                    got = [r[1] for r in c.execute(f"PRAGMA src.table_info({t})")]
                    cols = [x for x in got if x in have]
                    if not cols:
                        continue
                    if t == "users":
                        c.execute("DELETE FROM main.users")
                    c.execute(f"INSERT OR REPLACE INTO main.{t}({', '.join(cols)}) SELECT "
                              f"{', '.join(cols)} FROM src.{t}")
                    n[t] = c.execute(f"SELECT COUNT(*) FROM src.{t}").fetchone()[0]
                c.commit()
            finally:
                c.execute("DETACH DATABASE src")
            cfg._bump()
    finally:
        os.remove(path)
        os.rmdir(d)
    for row in cfg.nodes():
        t = row["trust"] or {}
        if t.get("kind") == "mtls":
            cfg.update_node(row["id"], {"trust": dict(t, cert=cert, key=key)}, who=who)
    for bid, k in (bundle.get("keys") or {}).items():
        if cfg.backend(bid) is not None:
            with cfg._lock:
                cfg._store_key(bid, k)
                cfg._conn.commit()
    cfg._bump()
    cfg.log_audit(who, "import", "fleet", None, detail={
        "text": f"imported {n.get('nodes', 0)} nodes, {n.get('backends', 0)} backends from "
                f"{(bundle.get('from') or {}).get('name') or 'another controller'}"})
    return {"nodes": n.get("nodes", 0), "backends": n.get("backends", 0),
            "keys": len(bundle.get("keys") or {})}


def import_at_start(cfg, state_dir: str, log=print) -> dict | None:
    """`import.json` in the state dir (a move put it there): imported once, then removed."""
    path = os.path.join(state_dir, "import.json")
    if not os.path.isfile(path):
        return None
    with open(path) as f:
        bundle = json.load(f)
    root = os.path.join(state_dir, "controller")
    cert, key = os.path.join(root, "controller.crt"), os.path.join(root, "controller.key")
    if not (os.path.isfile(cert) and os.path.isfile(key)):
        os.makedirs(root, mode=0o700, exist_ok=True)
        import socket
        nodetrust.make_identity(cert, key, f"TandemApp on {socket.gethostname().split('.')[0]}")
    got = import_bundle(cfg, bundle, cert, key)
    os.remove(path)
    log(f"[fleet] imported the fleet's export: {got['nodes']} nodes, {got['backends']} backends, "
        f"{got['keys']} stored keys")
    return got


# ---------------------------------------------------------------------- sealing (pull)

def seal(doc: dict, pem: str) -> dict:
    """`doc` for the holder of `pem`'s private key only (ECDH P-256 + HKDF-SHA256 + AES-GCM)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    pub = x509.load_pem_x509_certificate(pem.encode()).public_key()
    if not isinstance(pub, ec.EllipticCurvePublicKey):
        raise MoveError(400, "bad_request", "the certificate is not an EC key")
    eph = ec.generate_private_key(pub.curve)
    shared = eph.exchange(ec.ECDH(), pub)
    k = HKDF(hashes.SHA256(), 32, None, b"tandemapp-export-v1").derive(shared)
    nonce = _secrets.token_bytes(12)
    ct = AESGCM(k).encrypt(nonce, json.dumps(doc).encode(), b"tandemapp-export-v1")
    epk = eph.public_key().public_bytes(serialization.Encoding.X962,
                                        serialization.PublicFormat.UncompressedPoint)
    return {"alg": "ECDH-P256+HKDF-SHA256+A256GCM", "epk": base64.b64encode(epk).decode(),
            "nonce": base64.b64encode(nonce).decode(), "ct": base64.b64encode(ct).decode()}


def unseal(sealed: dict, key_path: str) -> dict:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    with open(key_path, "rb") as f:
        priv = serialization.load_pem_private_key(f.read(), None)
    try:
        epk = ec.EllipticCurvePublicKey.from_encoded_point(
            priv.curve, base64.b64decode(sealed["epk"]))
        shared = priv.exchange(ec.ECDH(), epk)
        k = HKDF(hashes.SHA256(), 32, None, b"tandemapp-export-v1").derive(shared)
        raw = AESGCM(k).decrypt(base64.b64decode(sealed["nonce"]),
                                base64.b64decode(sealed["ct"]), b"tandemapp-export-v1")
    except Exception as exc:                                         # noqa: BLE001
        raise MoveError(400, "bad_request", f"the export does not open with this controller's "
                                            f"key ({type(exc).__name__})")
    return json.loads(raw)


# ---------------------------------------------------------------------- another controller

def call(url: str, method: str, path: str, body=None, token: str | None = None,
         timeout: float = 20.0, cookie: str | None = None) -> tuple[int, dict]:
    """A JSON call to another TandemApp controller: (status, body); 502 when it does not answer.
    `token` goes as the bearer, `cookie` as its session (a sign-in with its password)."""
    data = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if cookie:
        headers["Cookie"] = f"qse_dash={cookie}"
    req = urllib.request.Request(url.rstrip("/") + path, data=data, method=method,
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except ValueError:
            return exc.code, {}
    except (OSError, ValueError) as exc:
        return 502, {"error": {"type": "unreachable", "message": f"{url} does not answer: "
                                                                 f"{type(exc).__name__}: {exc}"}}


def sign_in(url: str, password: str, timeout: float = 20.0) -> str | None:
    """A session at another controller with its admin password (one set in App settings is no
    bearer, server/auth.py): the cookie's value, or None."""
    req = urllib.request.Request(url.rstrip("/") + "/v1/fleet/session",
                                 data=json.dumps({"token": password}).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            got = r.headers.get_all("Set-Cookie") or []
    except (OSError, ValueError):
        return None
    for c in got:
        k, _, v = c.split(";", 1)[0].strip().partition("=")
        if k == "qse_dash" and v:
            return v
    return None


def _err(doc: dict, fallback: str) -> str:
    return ((doc or {}).get("error") or {}).get("message") or fallback


# ---------------------------------------------------------------------- the mover

class Mover:
    def __init__(self, fleet, *, clock=time.time, spawn=None, sleep=time.sleep, call=call,
                 sign_in=sign_in):
        self.fleet = fleet
        self.clock, self.sleep, self.call, self.sign_in = clock, sleep, call, sign_in
        self.spawn = spawn or (lambda fn: threading.Thread(target=fn, daemon=True).start())
        self._tokens: dict[str, tuple] = {}
        self._handovers: dict[str, dict] = {}
        self._lock = threading.RLock()
        self._recover()

    @property
    def cfg(self):
        return self.fleet.cfg

    def _recover(self) -> None:
        job = self.cfg.job("move")
        if job and job["state"] == "running":
            for s in job["steps"]:
                if s["state"] == "running":
                    s.update(state="failed", detail="the controller stopped during this step")
            job.update(state="failed", ended_at=round(self.clock(), 3),
                       error={"type": "interrupted", "message": "the controller stopped during "
                              "the move: read the plan again; a node handed over already "
                              "answers the new controller"})
            self.cfg.put_job(job)

    # ------------------------------------------------------------------ where it runs
    def api_url(self) -> str:
        return self.fleet.topology_host()["api_url"]

    def view(self, now: float) -> dict:
        f = self.fleet
        h = f.host_view(now)
        lid = h["node"]
        kind = "spark" if lid else ("mac" if f.may_sleep else "linux")
        targets = []
        for row, src in f.sources():
            if row["id"] == lid:
                continue
            why = self._not_a_host(row, src)
            targets.append({"node": row["id"], "alias": row["alias"], "allowed": why is None,
                            "why": why, "virtual": bool((src.identity() or {}).get("virtual"))
                            if isinstance(src, nodesource.AgentNode) else False})
        job = self.cfg.job("move")
        return {"contract_version": CONTRACT,
                "host": {"name": h["name"], "os": h["os"], "kind": kind, "node": lid,
                         "may_sleep": h["may_sleep"], "address": h["address"]},
                "api_url": self.api_url(), "moved_to": self.cfg.setting("controller.moved_to"),
                "sleep_warning": ("This Mac can sleep: while it sleeps the API is down and no "
                                  "metrics are kept. Keep it awake, or run the controller on a "
                                  "Spark.") if f.may_sleep else None,
                "targets": targets, "job": job}

    def _not_a_host(self, row: dict, src) -> str | None:
        if row["lease_end_ms"] is not None:
            return f"{row['alias']} is rented: its lease ends, the controller must outlive it"
        if row["transport"] == "tunnel":
            return f"{row['alias']} is reached through an SSH tunnel: not on the LAN"
        if not isinstance(src, nodesource.AgentNode) or (row["trust"] or {}).get("kind") != "mtls":
            return f"{row['alias']} is not paired with this controller"
        st, text = src.status()
        if st == "off":
            return f"{row['alias']} does not answer: {text}"
        return None

    # ------------------------------------------------------------------ the plan
    def plan(self, q: dict, now: float) -> dict:
        p, _ = self._plan(q.get("to") or "", q.get("port"), now)
        return p

    def _plan(self, to: str, port, now: float, mint: bool = True) -> tuple[dict, dict | None]:
        f = self.fleet
        row = self.cfg.node(to) if to else None
        if row is None:
            raise MoveError(404, "not_found", f"no node {to}")
        try:
            port = int(port) if port not in (None, "") else DEFAULT_PORT
        except ValueError:
            port = 0
        if not 1024 <= port < 65536:
            raise MoveError(400, "bad_request", "port is a whole number from 1024 to 65535")
        src = f.source(row)
        a = row["alias"]
        url = f"http://{row['address']}:{port}"
        p = {"contract_version": CONTRACT, "to": to, "title": f"Move the controller to {a}",
             "allowed": True, "blocked": None, "api_url": url, "port": port, "impact": [],
             "removes": [], "keeps": [], "nodes": [], "needs_password": False,
             "confirm": None, "expires_at": None}
        why = self._not_a_host(row, src)
        if why is None and self.cfg.setting("controller.moved_to"):
            why = "this controller moved already: open the new one"
        if why is None:
            job = self.cfg.job("move")
            if job and job["state"] == "running":
                why = "a move runs"
        lid = f._local_id
        virtual = bool((src.identity() or {}).get("virtual")) if isinstance(
            src, nodesource.AgentNode) else False
        for r, s in f.sources():
            if r["id"] == lid:
                p["nodes"].append({"id": r["id"], "alias": r["alias"], "how": "own",
                                   "why": "this machine's node: its agent is handed to the new "
                                          "controller with its LAN address"}
                                  if f.agent is not None and s is f.agent else
                                  {"id": r["id"], "alias": r["alias"], "how": "stays",
                                   "why": "read in-process here, without a TandemNode agent: "
                                          "pair it from the new controller"})
            elif r["transport"] == "tunnel":
                p["nodes"].append({"id": r["id"], "alias": r["alias"], "how": "stays",
                                   "why": "reached through an SSH tunnel with a key on this "
                                          "machine: release it here, add it from the new one"})
            elif (r["trust"] or {}).get("kind") != "mtls":
                p["nodes"].append({"id": r["id"], "alias": r["alias"], "how": "stays",
                                   "why": "not paired"})
            elif r["id"] == to and not virtual:
                p["nodes"].append({"id": r["id"], "alias": r["alias"], "how": "release",
                                   "why": "the new controller's own machine: read over its "
                                          "loopback"})
            else:
                off = s.status()[0] == "off"
                p["nodes"].append({"id": r["id"], "alias": r["alias"], "how": "repin",
                                   "why": "does not answer: pair it again from the new "
                                          "controller" if off else None})
        admin = getattr(f.svc.auth, "admin", None) if getattr(f.svc, "auth", None) else None
        p["needs_password"] = not admin
        moved = [x for x in p["nodes"] if x["how"] in ("repin", "release", "own")]
        p["impact"] += [f"{a} runs TandemApp's controller from now on (a systemd user unit, port "
                        f"{port}); this controller starts it there with this fleet's export",
                        f"{len(moved)} node{'' if len(moved) == 1 else 's'} trust the new "
                        f"controller and refuse this one",
                        f"the API moves to {url}/v1: point clients there",
                        "this page follows to the new address"]
        loop = [b["label"] or b["id"] for b in self.cfg.backends()
                if b["enabled"] and not b["cloud"] and f.locality.is_local(b["url"])
                and not f.locality.is_local(f"http://{row['address']}/")]
        if loop:
            p["impact"].append(f"backends on this machine's loopback answer no other machine: "
                               f"{', '.join(loop)}")
        p["impact"] += [f"{x['alias']}: {x['why']}" for x in p["nodes"]
                        if x["how"] == "stays" or (x["how"] == "repin" and x["why"])]
        p["keeps"] += ["backends, routes, the proxy's keys, alert rules, aliases, colours and "
                       "layouts go with the export; stored API keys too, under the new "
                       "controller's own master key",
                       "the metric history and the event log stay on this machine "
                       "(fleet.sqlite3)",
                       "this controller keeps running for its page; it no longer reads the "
                       "moved nodes"]
        if getattr(getattr(f.svc, "auth", None), "has_password", False):
            p["keeps"].append("this controller's admin password (App settings) stays here: the "
                              "new controller has the admin token it runs with")
        if p["needs_password"]:
            p["impact"].append(f"the new controller listens on the LAN: give it an admin "
                               f"password ({MIN_PASSWORD} or more characters)")
        if why:
            p.update(allowed=False, blocked=why)
        if p["allowed"] and mint:
            token = _secrets.token_urlsafe(18)
            with self._lock:
                self._tokens = {k: v for k, v in self._tokens.items() if v[2] > now}
                self._tokens[token] = (to, port, now + CONFIRM_S)
            p["confirm"], p["expires_at"] = token, round(now + CONFIRM_S, 3)
        return p, {"row": row, "src": src, "virtual": virtual, "admin": admin}

    # ------------------------------------------------------------------ push
    def move(self, body, who: str, now: float) -> dict:
        keys = {"to", "confirm", "port", "password", "name", "state_dir"}
        if not isinstance(body, dict) or set(body) - keys or not body.get("to"):
            raise MoveError(400, "bad_request", "the body is {to, confirm, port?, password?, "
                                                "name?, state_dir?}")
        port = body.get("port") or DEFAULT_PORT
        p, ctx = self._plan(body["to"], port, now, mint=False)
        if not p["allowed"]:
            raise MoveError(409, "conflict", p["blocked"])
        tok = body.get("confirm")
        if not tok:
            raise MoveError(428, "confirm_required", "a move needs its plan's confirm: GET "
                                                     "/v1/fleet/controller/move?to=… first")
        with self._lock:
            got = self._tokens.pop(tok, None) if isinstance(tok, str) else None
        if got is None or got[:2] != (body["to"], int(port)) or got[2] <= now:
            raise MoveError(409, "confirm_invalid", "that confirm is spent, too old or another "
                                                   "move's: read the plan again")
        password = body.get("password") or ctx["admin"]
        if not isinstance(password, str) or len(password) < MIN_PASSWORD or \
                any(c.isspace() for c in password):
            raise MoveError(409, "password_needed", f"the new controller listens on the LAN: give "
                                                    f"it an admin password ({MIN_PASSWORD} or more "
                                                    f"characters, no spaces)")
        for k in ("name", "state_dir"):
            v = body.get(k)
            if v is not None and (not isinstance(v, str) or not v or "\n" in v or
                                  v.startswith("-")):
                raise MoveError(400, "bad_request", f"{k} is a name or a path")
        a = ctx["row"]["alias"]
        job = self._new_job("push", who, to=body["to"], alias=a, api_url=p["api_url"],
                            port=int(port), nodes=p["nodes"])
        opts = {k: body[k] for k in ("name", "state_dir") if body.get(k)}
        self.spawn(lambda: self._push(job, ctx, password, opts))
        self.fleet.log(f"[fleet] controller move to {a} started by {who}")
        return job

    def _new_job(self, kind: str, who: str, **kw) -> dict:
        now = round(self.clock(), 3)
        words = {"to": kw.get("alias") or kw.get("url"), "url": kw.get("api_url") or "",
                 "from": kw.get("from") or ""}
        job = {"id": "m-" + _secrets.token_hex(6), "kind": "move", "direction": kind,
               "state": "running", "by": who, "started_at": now, "ended_at": None,
               "error": None, "log": [], "result": None,
               "steps": [{"id": sid, "label": label.format(**words), "state": "todo",
                          "detail": None} for sid, label in STEPS[kind]], **kw}
        self.cfg.put_job(job)
        return job

    def _step(self, job: dict, sid: str, state: str, detail: str | None = None) -> None:
        s = next(x for x in job["steps"] if x["id"] == sid)
        s["state"] = state
        if detail is not None:
            s["detail"] = detail
        self.cfg.put_job(job)

    def _say(self, job: dict, text: str) -> None:
        job["log"].append({"t": round(self.clock(), 3), "text": text[:300]})
        self.fleet.log(f"[fleet] move: {text}")

    def _fail(self, job: dict, sid: str, kind: str, message: str) -> None:
        self._step(job, sid, "failed", message)
        job.update(state="failed", ended_at=round(self.clock(), 3),
                   error={"type": kind, "message": message})
        self._say(job, f"failed at {sid}: {message}")
        self.cfg.put_job(job)

    def _push(self, job: dict, ctx: dict, password: str, opts: dict) -> None:
        f = self.fleet
        row, src = ctx["row"], ctx["src"]
        a = row["alias"]
        sid = "check"
        try:
            self._step(job, sid, "running")
            st, doc = src.client.json("GET", "/node/controller", timeout=10.0)
            if st == 404:
                return self._fail(job, sid, "not_built", f"{a}'s TandemNode is older and starts "
                                                         f"no controller: update it first")
            if st != 200:
                return self._fail(job, sid, "unreachable", f"{a}'s agent answered HTTP {st}")
            v = src.version()
            if v and _version.release() and not _same(v, _version.release()):
                return self._fail(job, sid, "version_mismatch", f"{a} runs TandemLLM {v}, this "
                                                                f"controller {_version.release()}"
                                                                f": update the fleet first")
            self._step(job, sid, "done", f"{a} answers; TandemNode {v or 'unknown'}")
            sid = "export"
            self._step(job, sid, "running")
            own = self._own_row() if any(x["how"] == "own" for x in job["nodes"]) else None
            bundle = export(self.cfg, skip_nodes=[x["id"] for x in job["nodes"]
                                                  if x["how"] == "stays"],
                            extra_nodes=[own] if own else [])
            bundle["from"] = {"name": f.trust().name, "fingerprint": f.trust().fingerprint(),
                              "api_url": self.api_url()}
            self._step(job, sid, "done", f"{len(base64.b64decode(bundle['db'])) / 1024:.0f} KiB, "
                                         f"{len(bundle['keys'])} stored keys")
            sid = "start"
            self._step(job, sid, "running")
            st, got = src.client.json("POST", "/node/controller", dict(
                bundle=bundle, port=job["port"], admin_token=password, **opts), timeout=90.0)
            bundle = None
            if st != 200 or not isinstance(got, dict) or not got.get("certificate"):
                return self._fail(job, sid, (got or {}).get("error", {}).get("type") or
                                  "start_failed", _err(got, f"{a}'s agent answered HTTP {st}"))
            pem = got["certificate"]
            name = f"TandemApp on {a}"
            self._step(job, sid, "done", f"{got['name']}.service on port {job['port']}, "
                                         f"certificate sha256 {got['fingerprint'][:16]}")
            sid = "repin"
            self._step(job, sid, "running")
            done = self._hand_over(job, pem, name, to=row["id"], virtual=ctx["virtual"],
                                   own_address=own and own["address"])
            self._step(job, sid, "done", f"{len(done)} node{'' if len(done) == 1 else 's'} "
                                         f"handed over")
            sid = "verify"
            self._step(job, sid, "running")
            up = self._verify(job, job["api_url"], password, done)
            self._step(job, sid, "done", f"{len(up)} of {len(done)} up at the new controller; "
                                         f"this one refused")
            sid = "done"
            self._moved(job, job["api_url"], name, got["fingerprint"], done)
            self._step(job, sid, "done", f"the API is {job['api_url']}/v1")
            job.update(state="done", ended_at=round(self.clock(), 3),
                       result={"api_url": job["api_url"], "nodes": job["nodes"]})
            self.cfg.put_job(job)
        except MoveError as exc:
            self._fail(job, sid, exc.kind, exc.message)
        except Exception as exc:                                     # noqa: BLE001
            self._fail(job, sid, "error", f"{type(exc).__name__}: {exc}")

    def _own_row(self) -> dict | None:
        """This machine's node as the new controller reaches it: its agent at its LAN address in
        mTLS, its own certificate pinned (the handover tells the agent to listen there)."""
        f = self.fleet
        lid = f._local_id
        row = self.cfg.node(lid) if lid else None
        a = f.agent
        if row is None or a is None or f.here() is not a:
            return None
        st, doc = a.client.json("GET", "/node/pair", timeout=5.0)
        if st != 200 or not (doc or {}).get("certificate"):
            raise MoveError(502, "unreachable", f"{row['alias']}'s own agent does not give its "
                                                f"certificate (an older agent: update it)")
        addr = row["address"] or f.local.address()
        port = a.client.port
        return dict(row, endpoint=f"https://{addr}:{port}", address=addr,
                    trust={"kind": "mtls", "pin": doc["certificate"].strip() + "\n",
                           "fingerprint": nodetrust.pem_fingerprint(doc["certificate"]),
                           "via": "handover", "paired_at": round(self.clock(), 3)})

    def _hand_over(self, job: dict, pem: str, name: str, *, to: str | None, virtual: bool,
                   own_address: str | None) -> list[str]:
        """Each node's owner pin to `pem` (the target released instead when the new controller
        runs on it and reads it over loopback); the ids handed over."""
        f = self.fleet
        done = []
        for x in job["nodes"]:
            if x["how"] == "stays":
                continue
            row = self.cfg.node(x["id"])
            if row is None:
                continue
            src = f.source(row)
            if x["how"] == "own":
                st, doc = f.agent.client.json("PUT", "/node/owner", {
                    "certificate": pem, "name": name, "address": own_address}, timeout=10.0)
            elif x["how"] == "release":
                st, doc = src.client.json("DELETE", "/node/pair", timeout=10.0)
            else:
                st, doc = (src.client.json("PUT", "/node/owner", {"certificate": pem,
                                                                  "name": name}, timeout=10.0)
                           if isinstance(src, nodesource.AgentNode) else (502, {}))
            if st == 200:
                x["state"] = "handed"
                done.append(x["id"])
                self._say(job, f"{x['alias']}: {'released to its own machine' if x['how'] == 'release' else 'trusts the new controller'}")
                f.store.add_event("node", x["id"], f"{x['alias']} moves to {name}",
                                  f"{'released here, read over its loopback there' if x['how'] == 'release' else 'its owner pin handed over'}")
            else:
                x["state"] = "failed"
                x["why"] = _err(doc, f"HTTP {st}") + ": pair it again from the new controller"
                self._say(job, f"{x['alias']}: not handed over ({x['why']})")
            self.cfg.put_job(job)
        return done

    def _verify(self, job: dict, url: str, token: str | None, ids: list[str]) -> list[str]:
        """The new controller lists each handed node up (VERIFY_S at most); this controller's own
        call to one of them is refused."""
        end = time.monotonic() + VERIFY_S
        up: set = set()
        while True:
            st, doc = self.call(url, "GET", "/v1/fleet/nodes", token=token, timeout=10.0)
            if st == 200:
                up = {n["id"] for n in doc.get("nodes") or [] if n["id"] in ids
                      and n["status"] in ("up", "draining", "alert")}
            if up >= set(ids) or time.monotonic() >= end:
                break
            self.sleep(1.0)
        for x in job["nodes"]:
            if x["id"] in ids:
                x["accepted"] = x["id"] in up
        refused = None
        for nid in ids:
            row = self.cfg.node(nid)
            if row is None or (row["trust"] or {}).get("kind") != "mtls":
                continue
            src = self.fleet.source(row)
            if isinstance(src, nodesource.AgentNode):
                try:
                    st, _ = src.client.json("GET", "/node/identity", timeout=5.0)
                    refused = st in (401, 403)
                except Exception:                                    # noqa: BLE001
                    refused = True
                break
        job["old_refused"] = refused
        self._say(job, f"new controller: {len(up)} of {len(ids)} up; this controller "
                       f"{'refused' if refused else 'still accepted' if refused is False else 'not checked'}")
        return sorted(up)

    def _moved(self, job: dict, url: str, name: str, fp: str | None, ids: list[str]) -> None:
        f = self.fleet
        self.cfg.set_setting("controller.moved_to", {"api_url": url, "name": name,
                                                     "fingerprint": fp,
                                                     "at": round(self.clock(), 3)},
                             who="controller")
        for nid in ids:
            if self.cfg.node(nid) is not None:
                f.forget(nid)
        f.store.add_event("controller", "host", f"The controller moved to {name}",
                          f"the API is {url}/v1; {len(ids)} nodes handed over")
        self._say(job, f"moved: the API is {url}/v1")

    # ------------------------------------------------------------------ pull: the giver
    def handover(self, body, who: str, now: float) -> dict:
        if not isinstance(body, dict) or set(body) - {"certificate", "name", "api_url"}:
            raise MoveError(400, "bad_request", "the body is {certificate, name, api_url}")
        pem, url = str(body.get("certificate") or ""), body.get("api_url")
        try:
            fp = nodetrust.pem_fingerprint(pem)
        except nodetrust.TrustError:
            raise MoveError(400, "bad_request", "certificate is the taker's PEM certificate")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise MoveError(400, "bad_request", "api_url is the taker's http(s) URL")
        if self.cfg.setting("controller.moved_to"):
            raise MoveError(409, "conflict", "this controller moved already")
        if fp == self.fleet.trust().fingerprint():
            raise MoveError(409, "conflict", "that is this controller's own certificate")
        own = self._own_row()
        nodes = []
        lid = self.fleet._local_id
        for r in self.cfg.nodes():
            how = "own" if r["id"] == lid and own else "stays" if r["transport"] == "tunnel" or \
                (r["trust"] or {}).get("kind") != "mtls" else "repin"
            if r["id"] == lid and not own:
                how = "stays"
            nodes.append({"id": r["id"], "alias": r["alias"], "how": how, "why": None})
        bundle = export(self.cfg, skip_nodes=[x["id"] for x in nodes if x["how"] == "stays"],
                        extra_nodes=[own] if own else [])
        bundle["from"] = {"name": self.fleet.trust().name,
                          "fingerprint": self.fleet.trust().fingerprint(),
                          "api_url": self.api_url()}
        hid = "h-" + _secrets.token_hex(6)
        with self._lock:
            self._handovers = {k: v for k, v in self._handovers.items() if v["until"] > now}
            self._handovers[hid] = {"pem": pem, "fp": fp, "name": str(body.get("name") or
                                                                     "TandemApp")[:80],
                                    "url": url, "nodes": nodes, "until": now + HANDOVER_S,
                                    "own_address": own and own["address"]}
        self.fleet.log(f"[fleet] handover {hid} to {url} prepared by {who}")
        return {"contract_version": CONTRACT, "id": hid, "sealed": seal(bundle, pem),
                "nodes": nodes, "expires_at": round(now + HANDOVER_S, 3)}

    def commit(self, hid: str, who: str, now: float) -> dict:
        with self._lock:
            h = self._handovers.pop(hid, None)
        if h is None or h["until"] <= now:
            raise MoveError(404, "not_found", f"no handover {hid} (spent or too old)")
        job = self._new_job("pull", who, url=h["url"], api_url=h["url"], nodes=h["nodes"],
                            alias=h["url"], **{"from": self.api_url()})
        job["direction"] = "give"
        done = self._hand_over(job, h["pem"], h["name"], to=None, virtual=False,
                               own_address=h["own_address"])
        self._moved(job, h["url"], h["name"], h["fp"], done)
        for s in job["steps"]:
            s["state"] = "done"
        job.update(state="done", ended_at=round(self.clock(), 3),
                   result={"api_url": h["url"], "nodes": job["nodes"]})
        self.cfg.put_job(job)
        return {"contract_version": CONTRACT, "id": hid, "nodes": job["nodes"],
                "moved_to": h["url"]}

    # ------------------------------------------------------------------ pull: the taker
    def take(self, body, who: str, now: float) -> dict:
        if not isinstance(body, dict) or set(body) - {"url", "password"} or \
                not isinstance(body.get("url"), str) or \
                not body["url"].startswith(("http://", "https://")):
            raise MoveError(400, "bad_request", "the body is {url, password}: the controller "
                                                "that runs the fleet now, and its admin password")
        if any(n["endpoint"] != "local" for n in self.cfg.nodes()):
            raise MoveError(409, "not_empty", "this controller has a fleet of its own: it takes "
                                              "another only while it has none")
        job = self._new_job("pull", who, url=body["url"], api_url=self.api_url(), nodes=[],
                            alias=body["url"], **{"from": body["url"]})
        self.spawn(lambda: self._pull(job, body["url"].rstrip("/"), body.get("password")))
        return job

    def _pull(self, job: dict, url: str, password: str | None) -> None:
        f = self.fleet
        sid = "check"
        try:
            self._step(job, sid, "running")
            auth = {"token": password}
            st, doc = self.call(url, "GET", "/v1/fleet/controller", **auth)
            if st == 401 and password:
                # a password set in its App settings signs in; the admin token is a bearer too
                c = self.sign_in(url, password)
                if c:
                    auth = {"cookie": c}
                    st, doc = self.call(url, "GET", "/v1/fleet/controller", **auth)
            if st != 200:
                return self._fail(job, sid, "unreachable", _err(doc, f"{url} answered HTTP {st}"))
            self._step(job, sid, "done", f"{doc['host']['name']} ({url})")
            sid = "export"
            self._step(job, sid, "running")
            tr = f.trust()
            st, got = self.call(url, "POST", "/v1/fleet/controller/handover",
                                {"certificate": tr.pem, "name": tr.name,
                                 "api_url": self.api_url()}, timeout=60.0, **auth)
            if st != 200:
                return self._fail(job, sid, (got.get("error") or {}).get("type") or
                                  "refused", _err(got, f"HTTP {st}"))
            job["nodes"] = got["nodes"]
            self._step(job, sid, "done", "sealed to this controller's certificate")
            sid = "import"
            self._step(job, sid, "running")
            bundle = unseal(got["sealed"], tr.key)
            n = import_bundle(self.cfg, bundle, tr.cert, tr.key, who=job["by"])
            bundle = None
            self._step(job, sid, "done", f"{n['nodes']} nodes, {n['backends']} backends, "
                                         f"{n['keys']} stored keys")
            sid = "repin"
            self._step(job, sid, "running")
            st, c = self.call(url, "POST", f"/v1/fleet/controller/handover/{got['id']}/commit",
                              {}, timeout=60.0, **auth)
            if st != 200:
                return self._fail(job, sid, "refused", _err(c, f"HTTP {st}"))
            job["nodes"] = c["nodes"]
            ids = [x["id"] for x in c["nodes"] if x.get("state") == "handed"]
            self._step(job, sid, "done", f"{len(ids)} nodes handed here")
            sid = "verify"
            self._step(job, sid, "running")
            end = time.monotonic() + VERIFY_S
            up: set = set()
            while time.monotonic() < end:
                up = {r["id"] for r, s in f.sources() if r["id"] in ids and s.status()[0] != "off"}
                if up >= set(ids):
                    break
                self.sleep(1.0)
            for x in job["nodes"]:
                x["accepted"] = x["id"] in up
            self._step(job, sid, "done", f"{len(up)} of {len(ids)} up here")
            f.store.add_event("controller", "host", f"This controller took the fleet from {url}",
                              f"{len(ids)} nodes handed here")
            job.update(state="done", ended_at=round(self.clock(), 3),
                       result={"api_url": self.api_url(), "nodes": job["nodes"]})
            self.cfg.put_job(job)
        except MoveError as exc:
            self._fail(job, sid, exc.kind, exc.message)
        except Exception as exc:                                     # noqa: BLE001
            self._fail(job, sid, "error", f"{type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------ the routes
    def handle(self, method: str, parts: list[str], q: dict, body, who: str, now: float):
        p = parts[1:]
        if method == "GET" and not p:
            return self.view(now)
        if p == ["move"]:
            if method == "GET":
                return self.plan(q, now)
            if method == "POST":
                return self.move(body, who, now)
        if p == ["handover"] and method == "POST":
            return self.handover(body, who, now)
        if len(p) == 3 and p[0] == "handover" and p[2] == "commit" and method == "POST":
            return self.commit(p[1], who, now)
        if p == ["take"] and method == "POST":
            return self.take(body, who, now)
        raise MoveError(404, "not_found", f"no route {method} /v1/fleet/{'/'.join(parts)}")


def _same(a: str, b: str) -> bool:
    from proxy import preflight
    return preflight.same_release(a, b)
