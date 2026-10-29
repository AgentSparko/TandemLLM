"""The Add Spark wizard's state on the controller (VIS-246, concept §6): one session per add, kept in
the config database so that it resumes after a reload, a restart, or on another device.

    w = Wizard(fleet)                                one per controller (Fleet.wizard())
    w.handle(method, parts, query, body, who)        the routes under /v1/fleet/add

    GET    /v1/fleet/add                              {fleet_version, session}: the active one, or
                                                      null (resume reads this)
    POST   /v1/fleet/add {replace?}                   a new session (409 busy while one is
                                                      active, unless replace: Start over)
    GET    /v1/fleet/add/{id}                         a session, ended ones too
    PATCH  /v1/fleet/add/{id} {step?, names?}         where the user is, and the Name step's
                                                      drafts; step `done` applies the names
    DELETE /v1/fleet/add/{id}                         abandoned: its secrets dropped
    POST   /v1/fleet/add/{id}/targets {row} | {ssh, rented?, key?}
                                                      a device picked in Find: a discovery row, or
                                                      a rented/remote node from its ssh line
    DELETE /v1/fleet/add/{id}/targets/{tid}
    POST   /v1/fleet/add/{id}/targets/{tid}/hostkey   the host key the box offers, new/same/changed
    POST   /v1/fleet/add/{id}/targets/{tid}/connect {method: code|ssh|takeover, code? | user,
              password? | key? | key_file? | agent?, host_key?}
                                                      pairing (proxy/fleettrust.py); a bare box
                                                      answers `bare` and goes to Install
    POST   /v1/fleet/add/{id}/check {secrets?}        preflight per target (proxy/preflight.py):
                                                      through the agent when paired, else over
                                                      SSH once
    POST   /v1/fleet/add/{id}/install {secrets?, options?}
                                                      the install jobs (proxy/nodeinstall.py),
                                                      one thread per bare box; poll the session
    POST   /v1/fleet/add/{id}/link {action: test|skip|configure}
                                                      the Link step (two or more new LAN nodes)

THE STEPS: find, connect, check, install, link, name, done. `step` is where the user is; the page
gates Next (lib/addspark.ts) and the controller refuses what may not happen: an install before a
check passed, another TandemLLM version, a finish with a node not paired.

SECRETS. A password or a pasted key is never written: not in the session's JSON, the database,
the log or an answer. The Connect step's SSH login uses it once; a box with nothing installed
needs it again for Check and Install, so it is held in this process's memory only (the vault
below) for SECRET_TTL_S, until the install takes it, the session ends, or the controller stops,
whichever is first; then the page asks for it again, saying why. A key file is named by its path
on the controller's machine and read by ssh there; a key pasted for a rented node is kept as a
0600 file beside the controller's certificate while the node is in the fleet, because its tunnel
needs it (proxy/fleettrust.py), and removed with the node.

RENTED. A target from an ssh line (`/discovery/target`) with `rented: {lease_end, cost_per_hour?}`
is paired through a tunnel (proxy/sshtunnel.py); once paired its lease and cost are on its node
record, and the lease rule (proxy/fleetwatch.py, 24 h before the end by default) reminds.
"""

from __future__ import annotations

import os
import secrets as _secrets
import threading
import time

from proxy import _version, nodeinstall, preflight

CONTRACT = "1.0"
STEPS = ("find", "connect", "check", "install", "link", "name", "done")
SECRET_TTL_S = 1800.0
TARGETS_MAX = 8
ICONS = ("desk", "cloud", "rack")


class AddError(Exception):
    def __init__(self, status: int, kind: str, message: str):
        super().__init__(message)
        self.status, self.kind, self.message = status, kind, message


def _err(exc) -> dict:
    return {"type": getattr(exc, "kind", "error"), "message": getattr(exc, "message", str(exc))}


class Wizard:
    def __init__(self, fleet, *, clock=time.time, spawn=None, package=None):
        self.fleet = fleet
        self.clock = clock
        self.spawn = spawn or (lambda fn: threading.Thread(target=fn, daemon=True).start())
        self._package = package
        self._lock = threading.RLock()
        self.vault: dict[tuple, dict] = {}         # (session, target) -> {secret, until}
        self.jobs: dict[tuple, nodeinstall.Install] = {}
        self._recover()

    @property
    def cfg(self):
        return self.fleet.cfg

    def package(self) -> nodeinstall.Package:
        if self._package is None:
            self._package = nodeinstall.Package(os.path.join(os.path.dirname(self.cfg.path),
                                                             "packages"))
        return self._package

    def _recover(self) -> None:
        """An install the controller was running when it stopped: failed, to be run again."""
        s = self.cfg.add_session()
        if s is None:
            return
        changed = False
        for t in s["targets"]:
            inst = t.get("install") or {}
            if inst.get("state") in ("queued", "running"):
                inst.update(state="failed", error={
                    "type": "interrupted", "message": "the controller stopped during the "
                    "install: Install again (each step is safe to repeat)"})
                changed = True
        if changed:
            self._save(s)

    # ------------------------------------------------------------------ the fleet's version
    def fleet_version(self) -> str:
        """The release every node runs (the most common one, without a local build label), else
        this controller's."""
        seen: dict[str, int] = {}
        for row, src in self.fleet.sources():
            try:
                v = src.version()
            except Exception:                                        # noqa: BLE001
                v = None
            if v:
                v = v.split("+", 1)[0]           # a recipe's local build label is the release
                seen[v] = seen.get(v, 0) + 1
        if seen:
            return max(seen.items(), key=lambda kv: kv[1])[0]
        return _version.release() or "0.0.0"

    # ------------------------------------------------------------------ the session
    def _save(self, s: dict) -> dict:
        s["updated_at"] = round(self.clock(), 3)
        self.cfg.put_add_session(s)
        return s

    def _get(self, sid: str) -> dict:
        s = self.cfg.add_session(sid)
        if s is None:
            raise AddError(404, "not_found", f"no Add Spark session {sid}")
        return s

    def _active(self, sid: str) -> dict:
        s = self._get(sid)
        if s["state"] != "active":
            raise AddError(409, "conflict", f"the session {sid} is {s['state']}: start a new one")
        return s

    def _target(self, s: dict, tid: str) -> dict:
        t = next((x for x in s["targets"] if x["id"] == tid), None)
        if t is None:
            raise AddError(404, "not_found", f"no target {tid} in this session")
        return t

    def view(self, s: dict | None) -> dict | None:
        """The session as the page sees it: each target's secret said as held or not (never the
        secret), the running installs' progress, the lease reminders."""
        if s is None:
            return None
        now = self.clock()
        out = dict(s)
        targets = []
        for t in s["targets"]:
            t = dict(t)
            v = self.vault.get((s["id"], t["id"]))
            t["secret"] = {"held": bool(v and v["until"] > now),
                           "until": round(v["until"], 3) if v and v["until"] > now else None}
            job = self.jobs.get((s["id"], t["id"]))
            if job is not None:
                t["install"] = job.view()
            targets.append(t)
        out["targets"] = targets
        out["reminders"] = self._reminders(s)
        out["contract_version"] = CONTRACT
        return out

    def _reminders(self, s: dict) -> list[dict]:
        rule = next((r for r in self.fleet.watch.rules() if r["id"] == "lease"), None)
        hours = (rule or {}).get("threshold") or 24.0
        out = []
        for t in s["targets"]:
            r = t.get("rented") or {}
            if r.get("lease_end") and t.get("node"):
                out.append({"node": t["node"], "target": t["id"], "lease_end": r["lease_end"],
                            "at": round(r["lease_end"] - hours * 3600, 3), "hours": hours,
                            "rule": "lease", "enabled": bool((rule or {}).get("enabled", True))})
        return out

    def new(self, body, who: str) -> dict:
        if body is not None and (not isinstance(body, dict) or set(body) - {"replace"}):
            raise AddError(400, "bad_request", "the body is {replace?}")
        with self._lock:
            cur = self.cfg.add_session()
            if cur is not None:
                if not (body or {}).get("replace"):
                    raise AddError(409, "busy", f"an Add Spark session is open ({cur['id']}, at "
                                                f"{cur['step']}): resume it, or start over with "
                                                f"replace: true")
                self._end(cur, "abandoned")
            now = round(self.clock(), 3)
            s = {"id": "a-" + _secrets.token_hex(6), "state": "active", "step": "find",
                 "created_at": now, "updated_at": now, "by": who,
                 "fleet_version": self.fleet_version(), "targets": [],
                 "link": {"state": "todo", "why": None, "results": []}, "done_at": None,
                 "seq": 0}
            self.fleet.log(f"[fleet] Add Spark session {s['id']} started by {who}")
            return self._save(s)

    def _end(self, s: dict, state: str) -> None:
        for k in [k for k in self.vault if k[0] == s["id"]]:
            self.vault.pop(k, None)
        s["state"] = state
        if state == "done":
            s["done_at"] = round(self.clock(), 3)
        self._save(s)

    # ------------------------------------------------------------------ targets
    def add_target(self, s: dict, body, who: str) -> dict:
        if not isinstance(body, dict) or not ({"row"} <= set(body) or {"ssh"} <= set(body)) or \
                set(body) - {"row", "ssh", "rented", "key"}:
            raise AddError(400, "bad_request", "the body is {row} (a discovery row) or {ssh, "
                                               "rented?, key?} (a target from an ssh line)")
        if len(s["targets"]) >= TARGETS_MAX:
            raise AddError(409, "conflict", f"{TARGETS_MAX} devices per add at most")
        s["seq"] += 1
        tid = f"t{s['seq']}"
        if "row" in body:
            t = self._from_row(tid, body["row"])
        else:
            t = self._from_ssh(tid, body["ssh"], body.get("rented"))
        # one box once: the same row, or the same login; a virtual node shares its machine's
        # address and is a row of its own
        if any((t["row"] and x["row"] == t["row"]) or (not t["row"] and not x["row"] and
               x["address"] == t["address"] and x["ssh"]["port"] == t["ssh"]["port"])
               for x in s["targets"]):
            raise AddError(409, "conflict", f"{t['label']} is picked already")
        if body.get("key") is not None:
            if not isinstance(body["key"], str) or not body["key"].strip() or \
                    len(body["key"]) > 16384:
                raise AddError(400, "bad_request", "key is the private key file's text")
            self._hold(s["id"], tid, {"key": body["key"]})
            t["ssh"]["auth"] = "key"
        s["targets"].append(t)
        return self._save(s)

    def _from_row(self, tid: str, rid) -> dict:
        from proxy import discovery
        d = self.fleet.discover()
        snap = d.snapshot()
        r = next((x for x in snap["rows"] if x["id"] == rid), None)
        if r is None:
            raise AddError(404, "not_found", f"no discovery row {rid}: find again")
        if r.get("fleet"):
            raise AddError(409, "conflict", f"{r.get('name') or r['addresses'][0]} is in this "
                                            f"fleet already")
        if not r["connect"]:
            raise AddError(409, "conflict", f"{r.get('name') or r['addresses'][0]}: {r['title']}"
                                            f"; nothing to add")
        cfg = r.get("ssh_config") or {}
        jumps = []
        if cfg.get("proxy_jump") and cfg["proxy_jump"] != "none":
            jumps = [discovery._hop(x.strip(), "jump host")
                     for x in cfg["proxy_jump"].split(",") if x.strip()]
        addr = cfg.get("hostname") or (r["addresses"] or [None])[0]
        if not addr:
            raise AddError(409, "conflict", "this row has no address to reach")
        remote = bool(jumps) or _remote(addr)
        node = r.get("node") or {}
        return {"id": tid, "row": rid, "label": r.get("name") or addr, "kind": r["kind"],
                "title": r["title"], "detail": r["detail"], "address": addr,
                "connect": list(r["connect"]), "hw": (r.get("hw") or {}).get("label"),
                "version": node.get("version"), "window": node.get("window"),
                "ssh": {"host": addr, "port": cfg.get("port") or (r.get("ssh") or {}).get("port")
                        or 22, "user": cfg.get("user"), "jumps": jumps,
                        "key_file": cfg.get("identity_file"), "auth": None},
                "rented": None, "transport": "tunnel" if remote else "lan",
                "state": "todo", "via": None, "host_key": None, "error": None, "node": None,
                "preflight": None, "install": None, "name": None}

    def _from_ssh(self, tid: str, ssh, rented) -> dict:
        from proxy import discovery
        if isinstance(ssh, str):
            try:
                ssh = discovery.parse_ssh_line(ssh)
            except ValueError as exc:
                raise AddError(400, "bad_request", str(exc))
        if not isinstance(ssh, dict) or not ssh.get("host"):
            raise AddError(400, "bad_request", "ssh is the ssh line, or its target as "
                                               "/discovery/target read it")
        try:
            line = f"ssh -p {int(ssh.get('port') or 22)} " + (
                f"-J {','.join(_hop_text(j) for j in ssh.get('jumps') or [])} "
                if ssh.get("jumps") else "") + (f"{ssh['user']}@" if ssh.get("user") else "") + \
                ssh["host"]
            got = discovery.parse_ssh_line(line)
        except (ValueError, TypeError, KeyError) as exc:
            raise AddError(400, "bad_request", f"the target does not read as an ssh line: {exc}")
        kf = ssh.get("identity_file") or ssh.get("key_file")
        if kf is not None and (not isinstance(kf, str) or "\n" in kf or kf.startswith("-")):
            raise AddError(400, "bad_request", "key_file is a path on the controller's machine")
        lease = None
        if rented is not None:
            if not isinstance(rented, dict) or set(rented) - {"lease_end", "cost_per_hour"}:
                raise AddError(400, "bad_request", "rented is {lease_end, cost_per_hour?}")
            le, cost = rented.get("lease_end"), rented.get("cost_per_hour")
            if not isinstance(le, (int, float)) or isinstance(le, bool) or le <= self.clock():
                raise AddError(400, "bad_request", "lease_end is a Unix time in the future")
            if cost is not None and (not isinstance(cost, (int, float)) or isinstance(cost, bool)
                                     or not 0 <= cost < 10000):
                raise AddError(400, "bad_request", "cost_per_hour is a number from 0")
            lease = {"lease_end": float(le), "cost_per_hour": None if cost is None
                     else float(cost)}
        return {"id": tid, "row": None, "label": got["host"], "kind": "rented" if lease else
                "remote", "title": "Rented Spark" if lease else "Remote host",
                "detail": f"{got['user'] + '@' if got['user'] else ''}{got['host']}:{got['port']}"
                          + (f" via {', '.join(_hop_text(j) for j in got['jumps'])}"
                             if got["jumps"] else ""),
                "address": got["host"], "connect": ["ssh"], "hw": None, "version": None,
                "window": None,
                "ssh": {"host": got["host"], "port": got["port"], "user": got["user"],
                        "jumps": got["jumps"], "key_file": kf, "auth": "key_file" if kf else None},
                "rented": lease, "transport": "tunnel" if (lease or got["remote"]) else "lan",
                "state": "todo", "via": None, "host_key": None, "error": None, "node": None,
                "preflight": None, "install": None, "name": None}

    def drop_target(self, s: dict, tid: str) -> dict:
        self._target(s, tid)
        if (s["id"], tid) in self.jobs and self.jobs[(s["id"], tid)].state == "running":
            raise AddError(409, "busy", "its install is running")
        self.vault.pop((s["id"], tid), None)
        s["targets"] = [x for x in s["targets"] if x["id"] != tid]
        return self._save(s)

    # ------------------------------------------------------------------ secrets
    def _hold(self, sid: str, tid: str, secret: dict) -> None:
        self.vault[(sid, tid)] = {"secret": dict(secret), "until": self.clock() + SECRET_TTL_S}

    def _secret(self, s: dict, t: dict, given: dict | None = None) -> dict:
        """The credentials a login to a bare box takes: given now, held, a key file, or the
        user's agent; 409 secret_needed when a password was used and is no longer held."""
        if given:
            return dict(given)
        v = self.vault.get((s["id"], t["id"]))
        if v and v["until"] > self.clock():
            return dict(v["secret"])
        self.vault.pop((s["id"], t["id"]), None)
        auth = (t.get("ssh") or {}).get("auth")
        if auth in ("password", "key"):
            raise AddError(409, "secret_needed", f"enter the {'password' if auth == 'password' else 'key'} "
                                                 f"for {t['ssh'].get('user')}@{t['address']} again: "
                                                 f"it is kept in memory only, for "
                                                 f"{SECRET_TTL_S / 60:.0f} minutes, and never "
                                                 f"stored")
        return {}

    @staticmethod
    def _given(body, tid: str) -> dict | None:
        got = ((body or {}).get("secrets") or {}).get(tid)
        if got is None:
            return None
        if not isinstance(got, dict) or set(got) - {"password", "key"} or len(got) != 1 or \
                not all(isinstance(v, str) and v for v in got.values()):
            raise AddError(400, "bad_request", "a secret is {password} or {key}")
        return got

    # ------------------------------------------------------------------ connect
    def host_key(self, s: dict, tid: str) -> dict:
        t = self._target(s, tid)
        ssh = t["ssh"]
        body = {"host": ssh["host"], "port": ssh["port"]}
        if ssh.get("jumps"):
            body["jumps"] = ssh["jumps"]
        hk = self.fleet._trusting(lambda tr: tr.host_key(body))
        t["host_key"] = {"type": hk["type"], "fingerprint": hk["fingerprint"],
                         "known": hk["known"], "stored": hk["stored"], "confirmed": False}
        return self._save(s)

    def connect(self, s: dict, tid: str, body, who: str) -> dict:
        t = self._target(s, tid)
        if not isinstance(body, dict) or body.get("method") not in ("code", "ssh", "takeover"):
            raise AddError(400, "bad_request", "the body is {method: code|ssh|takeover, ...}")
        m = body["method"]
        if m not in t["connect"] and not (m == "ssh" and "takeover" in t["connect"]):
            raise AddError(409, "conflict", f"{t['label']} cannot be connected by {m}")
        t["error"] = None
        try:
            if m == "code":
                if set(body) - {"method", "code"}:
                    raise AddError(400, "bad_request", "the body is {method: code, code}")
                b = {"address": t["address"], "code": str(body.get("code") or "")}
                if t.get("window"):
                    b["port"] = t["window"]
                row = self.fleet._trusting(lambda tr: tr.pair_code(b, who))
                self._paired(s, t, row, "code")
                return self._save(s)
            keys = {"method", "user", "password", "key", "key_file", "agent", "host_key"}
            if set(body) - keys:
                raise AddError(400, "bad_request", "the body is {method, user, password? | key? | "
                                                   "key_file? | agent?, host_key}")
            ssh = t["ssh"]
            user = body.get("user") or ssh.get("user")
            fp = body.get("host_key")
            if not fp or not t.get("host_key") or fp != t["host_key"]["fingerprint"]:
                raise AddError(409, "host_key_unconfirmed", "confirm the host key first (read it "
                                                            "with .../hostkey and compare it on "
                                                            "the node)")
            secret = {k: body[k] for k in ("password", "key") if body.get(k)}
            if not secret and not body.get("key_file") and not body.get("agent"):
                # none in the body: the one held for this target (a key given in Find)
                v = self.vault.get((s["id"], tid))
                if v and v["until"] > self.clock():
                    secret = dict(v["secret"])
            kf = body.get("key_file") or (ssh.get("key_file") if not secret and not
                                          body.get("agent") else None)
            auth = "password" if "password" in secret else "key" if "key" in secret else \
                "key_file" if kf else "agent"
            b = {"host": ssh["host"], "port": ssh["port"], "user": user, "host_key": fp,
                 **({"jumps": ssh["jumps"]} if ssh.get("jumps") else {}),
                 **({"key_file": kf} if kf else {}),
                 **({"tunnel": True} if t["transport"] == "tunnel" else {}),
                 **({"takeover": True} if m == "takeover" else {}), **secret}
            try:
                row = self.fleet._trusting(lambda tr: tr.pair_ssh(b, who))
            except Exception as exc:
                if getattr(exc, "kind", None) == "not_installed":
                    ssh.update(user=user, auth=auth, key_file=kf)
                    t["host_key"]["confirmed"] = True
                    # a bare box: Install needs this login again; held in memory only
                    if secret:
                        self._hold(s["id"], tid, secret)
                    t.update(state="bare", via=m, error=None)
                    self._refresh_row(t)
                    return self._save(s)
                raise
            ssh.update(user=user, auth=auth, key_file=kf)
            t["host_key"]["confirmed"] = True
            self._paired(s, t, row, m)
            return self._save(s)
        except Exception as exc:
            if hasattr(exc, "message"):
                t["error"] = _err(exc)
                if getattr(exc, "kind", None) == "owned" and "takeover" not in t["connect"]:
                    t["connect"].append("takeover")
                self._save(s)
            raise
        finally:
            body.pop("password", None)
            body.pop("key", None)

    def _refresh_row(self, t: dict) -> None:
        """What the SSH login taught discovery (a GB10 with nothing installed)."""
        if not t.get("row"):
            return
        try:
            r = next((x for x in self.fleet.discover().snapshot()["rows"]
                      if t["address"] in x["addresses"]), None)
        except Exception:                                            # noqa: BLE001
            r = None
        if r is not None:
            t.update(row=r["id"], kind=r["kind"], title=r["title"], detail=r["detail"],
                     hw=(r.get("hw") or {}).get("label") or t.get("hw"))

    def _paired(self, s: dict, t: dict, row: dict, via: str) -> None:
        t.update(state="paired", via=via, node=row["id"], error=None)
        self.vault.pop((s["id"], t["id"]), None)
        r = t.get("rented")
        if r:
            tags = list(dict.fromkeys((row.get("tags") or []) + ["rented"]))
            self.cfg.update_node(row["id"], {"lease_end_ms": int(r["lease_end"] * 1000),
                                             "cost_per_hour": r["cost_per_hour"], "tags": tags,
                                             "icon": "cloud"}, who="controller")
            self.fleet.store.add_event("node", row["id"], f"{row['alias']}: lease until "
                                       f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(r['lease_end']))}",
                                       "rented; the lease rule reminds before it ends")

    # ------------------------------------------------------------------ check
    def check(self, s: dict, body, who: str) -> dict:
        if body is not None and (not isinstance(body, dict) or set(body) - {"secrets",
                                                                            "targets"}):
            raise AddError(400, "bad_request", "the body is {secrets?, targets?}")
        only = (body or {}).get("targets")
        version = s["fleet_version"]
        ref = self.package().engine_ref_or_none()      # the engine release Install puts there
        for t in s["targets"]:
            if only and t["id"] not in only:
                continue
            if t["state"] not in ("paired", "bare"):
                continue
            try:
                facts, via = self._facts(s, t, self._given(body, t["id"]))
            except AddError as exc:
                t["preflight"] = {"at": round(self.clock(), 3), "via": None, "rows": [],
                                  "verdict": None, "error": _err(exc)}
                if exc.kind == "secret_needed":
                    self._save(s)
                    raise
                continue
            rows = preflight.checks(facts, version, self.clock(), install=t["state"] == "bare",
                                    engine_ref=ref)
            t["preflight"] = {"at": round(self.clock(), 3), "via": via, "rows": rows,
                              "verdict": preflight.verdict(rows, t["state"] == "paired"),
                              "error": None}
            if t["state"] == "paired" and t["preflight"]["verdict"]["install"] == "skip":
                t["install"] = {"state": "skip", "why": f"TandemLLM {version} runs there "
                                                        f"already"}
        return self._save(s)

    def _facts(self, s: dict, t: dict, given: dict | None) -> tuple[dict, str]:
        from proxy import nodesource
        if t["state"] == "paired":
            row = self.cfg.node(t["node"])
            src = self.fleet.source(row) if row else None
            if not isinstance(src, nodesource.AgentNode):
                raise AddError(409, "unreachable", f"{t['label']} is paired but its agent does "
                                                   f"not answer")
            try:
                st, doc = src.client.json("GET", "/node/preflight", timeout=20.0)
            except (OSError, ValueError) as exc:
                raise AddError(502, "unreachable", f"{t['label']}: {type(exc).__name__}")
            if st == 404:
                raise AddError(409, "not_built", f"{t['label']}'s TandemNode is older and has no "
                                                 f"preflight route: update it")
            if st != 200 or not isinstance(doc, dict):
                raise AddError(502, "unreachable", f"{t['label']}: HTTP {st}")
            return doc.get("facts") or {}, "agent"
        secret = self._secret(s, t, given)
        ssh = t["ssh"]
        tr = self.fleet.trust()
        from proxy import sshonce
        try:
            hk, b64 = tr.ssh.host_key(ssh["host"], ssh["port"], ssh.get("jumps") or None)
            confirmed = (t.get("host_key") or {}).get("fingerprint")
            if hk["known"] == "changed" or (confirmed and hk["fingerprint"] != confirmed):
                raise AddError(409, "host_key_changed", f"the host key of {t['address']} "
                                                        f"changed; refused")
            rc, out, err = tr.ssh.run(ssh["host"], ssh["port"], ssh["user"], preflight.SCRIPT,
                                      b"", kind=hk["type"], b64=b64,
                                      password=secret.get("password"), key=secret.get("key"),
                                      key_file=ssh.get("key_file"),
                                      jumps=ssh.get("jumps") or None)
        except sshonce.SshError as exc:
            raise AddError(exc.status, exc.kind, exc.message)
        facts = preflight.parse(out)
        if not facts:
            raise AddError(502, "ssh_failed", f"ssh to {t['address']} failed: "
                                              f"{(err.strip().splitlines() or ['no output'])[-1][:200]}")
        if given:
            self._hold(s["id"], t["id"], given)
        return facts, "ssh"

    # ------------------------------------------------------------------ install
    def install(self, s: dict, body, who: str) -> dict:
        if body is not None and (not isinstance(body, dict) or set(body) - {"secrets",
                                                                            "options"}):
            raise AddError(400, "bad_request", "the body is {secrets?, options?}")
        version = s["fleet_version"]
        pkg_version = _version.release()
        start = []
        for t in s["targets"]:
            pf = t.get("preflight") or {}
            v = pf.get("verdict")
            if t["state"] == "paired":
                if v and v["install"] == "refused":
                    raise AddError(409, "version_mismatch", f"{t['label']}: {v['why']}; Install "
                                                            f"refuses another version")
                t["install"] = {"state": "skip", "why": f"TandemLLM {version} runs there "
                                                        f"already"}
                continue
            if t["state"] != "bare":
                raise AddError(409, "conflict", f"connect {t['label']} first")
            if not v:
                raise AddError(409, "conflict", f"check {t['label']} first")
            if v["install"] == "refused":
                raise AddError(409, "version_mismatch", f"{t['label']}: {v['why']}; Install "
                                                        f"refuses another version")
            if v["fails"]:
                raise AddError(409, "preflight_failed", f"{t['label']}: fix the red checks, then "
                                                        f"check again")
            job = self.jobs.get((s["id"], t["id"]))
            if job is not None and job.state in ("queued", "running"):
                continue
            if (t.get("install") or {}).get("state") == "done":
                continue
            if not nodeinstall.same_version(pkg_version, version):
                raise AddError(409, "version_mismatch", f"this controller carries TandemLLM "
                                                        f"{pkg_version}; the fleet runs {version}: "
                                                        f"update the fleet first")
            start.append((t, self._secret(s, t, self._given(body, t["id"]))))
        for t, secret in start:
            opts = ((body or {}).get("options") or {}).get(t["id"]) or {}
            if not isinstance(opts, dict) or set(opts) - set(nodeinstall.DEFAULTS):
                raise AddError(400, "bad_request", f"options are {', '.join(nodeinstall.DEFAULTS)}")
            ssh = t["ssh"]
            target = {"host": ssh["host"], "port": ssh["port"], "user": ssh["user"],
                      "jumps": ssh.get("jumps") or [], "key_file": ssh.get("key_file"),
                      "tunnel": t["transport"] == "tunnel",
                      "host_key": (t.get("host_key") or {}).get("fingerprint")}
            key = (s["id"], t["id"])
            # the secret goes to the job; the vault forgets it now
            self.vault.pop(key, None)
            job = nodeinstall.Install(self.fleet.trust(), target, secret, package=self.package(),
                                      version=version, options=opts, who=who, clock=self.clock,
                                      on_change=lambda view, k=key: self._progress(k, view))
            self.jobs[key] = job
            t["install"] = job.view()
            self.spawn(lambda j=job: j.run())
            self.fleet.log(f"[fleet] Add Spark {s['id']}: installing TandemLLM {version} on "
                           f"{t['label']}")
        return self._save(s)

    def _progress(self, key: tuple, view: dict) -> None:
        """An install's step changed: kept in the session, so another device sees it."""
        with self._lock:
            s = self.cfg.add_session(key[0])
            if s is None:
                return
            t = next((x for x in s["targets"] if x["id"] == key[1]), None)
            if t is None:
                return
            t["install"] = view
            if view["state"] == "done" and view.get("node"):
                row = self.cfg.node(view["node"])
                if row is not None:
                    self._paired(s, t, row, "ssh")
            if view["state"] in ("done", "failed"):
                self.jobs.pop(key, None)
            self._save(s)

    # ------------------------------------------------------------------ link
    def new_nodes(self, s: dict) -> list[dict]:
        return [t for t in s["targets"] if t.get("node")]

    def link(self, s: dict, body, who: str) -> dict:
        if not isinstance(body, dict) or body.get("action") not in ("test", "skip", "configure"):
            raise AddError(400, "bad_request", "the body is {action: test|skip|configure}")
        lan = [t for t in self.new_nodes(s) if t["transport"] == "lan"]
        if len(lan) < 2:
            s["link"] = {"state": "not_needed", "why": _no_link(self.new_nodes(s)), "results": []}
            return self._save(s)
        if body["action"] == "skip":
            s["link"].update(state="skipped", why="skipped: run it later from a Spark's page")
            return self._save(s)
        if body["action"] == "configure":
            raise AddError(501, "not_built", "setting link addresses with sudo comes with the "
                                             "rented pair (step 7): set the fabric up by hand, "
                                             "then test it here")
        results = []
        for t in lan:
            try:
                got = self.fleet.ops.test_link(f"lan-{t['node']}", who, self.clock())
                results.append({"node": t["node"], "link": got["link"]["id"],
                                "test": got["link"].get("test"), "error": None})
            except Exception as exc:                                 # noqa: BLE001
                results.append({"node": t["node"], "link": f"lan-{t['node']}", "test": None,
                                "error": {"type": getattr(exc, "kind", None) or "error",
                                          "message": getattr(exc, "message", str(exc))}})
        s["link"] = {"state": "done", "why": None, "results": results}
        return self._save(s)

    # ------------------------------------------------------------------ name and done
    def patch(self, s: dict, body, who: str) -> dict:
        if not isinstance(body, dict) or not body or set(body) - {"step", "names"}:
            raise AddError(400, "bad_request", "the body is {step?, names?}")
        if "names" in body:
            names = body["names"]
            if not isinstance(names, dict):
                raise AddError(400, "bad_request", "names is {target id: {alias, colour, icon, "
                                                   "tags}}")
            for tid, n in names.items():
                t = self._target(s, tid)
                if not isinstance(n, dict) or set(n) - {"alias", "colour", "icon", "tags"}:
                    raise AddError(400, "bad_request", "a name is {alias?, colour?, icon?, tags?}")
                t["name"] = dict(t.get("name") or {}, **n)
        if "step" in body:
            step = body["step"]
            if step not in STEPS:
                raise AddError(400, "bad_request", f"step is one of {', '.join(STEPS)}")
            if step == "done":
                return self.finish(s, who)
            s["step"] = step
            if step == "link" and len([t for t in self.new_nodes(s)
                                       if t["transport"] == "lan"]) < 2:
                s["link"] = {"state": "not_needed", "why": _no_link(self.new_nodes(s)),
                             "results": []}
        return self._save(s)

    def finish(self, s: dict, who: str) -> dict:
        from proxy.fleet import FleetError
        todo = [t for t in s["targets"] if not t.get("node")]
        if not s["targets"] or todo:
            raise AddError(409, "conflict", "every device must be paired first: " +
                           ", ".join(t["label"] for t in todo) if todo else "no device picked")
        for t in s["targets"]:
            n = {k: v for k, v in (t.get("name") or {}).items() if v is not None}
            if not n:
                continue
            row = self.cfg.node(t["node"])
            if row is None:
                continue
            n = {k: v for k, v in n.items() if v != row.get(k)}
            if n:
                try:
                    self.fleet.patch_node(t["node"], n, who, self.clock())
                except FleetError as exc:
                    raise AddError(exc.status, "bad_request", f"{t['label']}: {exc.message}")
        s["step"] = "done"
        self._end(s, "done")
        self.fleet.log(f"[fleet] Add Spark session {s['id']} done: "
                       + ", ".join(t["node"] for t in s["targets"]))
        return s

    # ------------------------------------------------------------------ the routes
    def handle(self, method: str, parts: list[str], q: dict, body, who: str) -> dict:
        p = parts[1:]
        with self._lock:
            if not p:
                if method == "GET":
                    return {"contract_version": CONTRACT, "fleet_version": self.fleet_version(),
                            "session": self.view(self.cfg.add_session())}
                if method == "POST":
                    return self.view(self.new(body, who))
            elif len(p) == 1:
                if method == "GET":
                    return self.view(self._get(p[0]))
                if method == "PATCH":
                    return self.view(self.patch(self._active(p[0]), body, who))
                if method == "DELETE":
                    s = self._active(p[0])
                    if any(j.state == "running" for k, j in self.jobs.items() if k[0] == s["id"]):
                        raise AddError(409, "busy", "an install is running: wait for it")
                    self._end(s, "abandoned")
                    self.fleet.log(f"[fleet] Add Spark session {s['id']} abandoned by {who}")
                    return self.view(s)
            else:
                s = self._active(p[0])
                if p[1:] == ["targets"] and method == "POST":
                    return self.view(self.add_target(s, body, who))
                if len(p) == 3 and p[1] == "targets" and method == "DELETE":
                    return self.view(self.drop_target(s, p[2]))
                if len(p) == 4 and p[1] == "targets" and method == "POST":
                    if p[3] == "hostkey":
                        return self.view(self.host_key(s, p[2]))
                    if p[3] == "connect":
                        return self.view(self.connect(s, p[2], body, who))
                if p[1:] == ["check"] and method == "POST":
                    return self.view(self.check(s, body, who))
                if p[1:] == ["install"] and method == "POST":
                    return self.view(self.install(s, body, who))
                if p[1:] == ["link"] and method == "POST":
                    return self.view(self.link(s, body, who))
        raise AddError(404, "not_found", f"no route {method} /v1/fleet/{'/'.join(parts)}")


def _remote(addr: str) -> bool:
    import ipaddress
    try:
        return ipaddress.ip_address(addr).is_global
    except ValueError:
        return not addr.lower().endswith(".local") and "." in addr


def _hop_text(j: dict) -> str:
    from proxy import sshonce
    return sshonce.jump_spec(j)


def _no_link(new: list[dict]) -> str:
    if not new:
        return "no new Spark is paired yet"
    lan = [t for t in new if t["transport"] == "lan"]
    far = [t for t in new if t["transport"] != "lan"]
    if len(new) == 1:
        t = new[0]
        return (f"{t['label']} is the only new Spark: a link needs two or more on one fabric. "
                f"Test it later from its page" if t["transport"] == "lan" else
                f"{t['label']} is reached through a tunnel: no fabric reaches it from here")
    return (f"{', '.join(t['label'] for t in far)} {'is' if len(far) == 1 else 'are'} reached "
            f"through a tunnel: no fabric between {'it' if len(far) == 1 else 'them'} and the "
            f"LAN" + (f"; {lan[0]['label']} is the only one on the LAN" if lan else ""))
