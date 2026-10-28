"""The weights across the fleet (SRV-731, concept C9): which pack is on which node, a copy from one
node to another verified by content id, a removal with its confirm.

    w = Weights(fleet)                                   one per Fleet (fleet.weights)
    GET    /v1/fleet/weights                             the matrix: every pack and the nodes that
                                                         hold it, each node's store and free space,
                                                         the recent copies
    POST   /v1/fleet/weights/copy {from, to, profile}    the copy's job
    GET    /v1/fleet/weights/copy/{id}                   its progress: bytes, files, rate, the
                                                         content ids and the verdict
    POST   /v1/fleet/weights/copy/{id}/stop              stopped before its next chunk; nothing
                                                         left on the receiver
    GET    /v1/fleet/weights/{node}/{profile}            what removing that pack there does, and
                                                         its confirm (single use, five minutes)
    DELETE /v1/fleet/weights/{node}/{profile}?confirm=   removed (428 without the confirm)

A node's packs are its TandemNode agent's (`GET /node/weights`: the Setup Guide's and the weights
store's, proxy/nodeweights.py); a node read without its agent has none listed and says why.

THE COPY (one per receiving node at a time):

    1  the source's manifest of the pack; the receiver opens a receive (its free space checked, the
       pack not there already) and answers a single-use token
    2  the bytes go `direct` when both nodes are paired over mTLS on the LAN: the controller pins
       each node's certificate on the other (the pins a link test between them uses too) and the
       source's agent pushes every file to the receiver's agent at the address it is paired on
       (the LAN; the fabric's address once an agent listens there, with the cabled pair of step
       7). Otherwise `relay`: the controller
       streams each file from the source's agent to the receiver's (the controller's own machine,
       read on loopback, and a tunnelled node)
    3  each file is hashed by the sender and by the receiver (proxy/nodeweights.py push), and must
       match the source's manifest where it has a sha256
    4  the receiver commits (the folder moved into place) and answers the pack's content id; the
       copy is `verified` when it is the source's content id (its `_hub_manifest.json` came along),
       or, for a pack that had none, the id of the hashes the sender read: the receiver computed
       its own from the bytes it wrote

Progress is the sender's bytes over the elapsed time. A failed or stopped copy aborts the receive.
Each copy and removal is a `weights` event. The job is kept in `fleet_jobs` (kind `weights`); one
that ran when the controller stopped is failed (`interrupted`) at its next start.

A REMOVAL is refused while a deployment that is not stopped serves that pack on that node (its
record's pack, or what the node's engine reports it loaded).
"""

from __future__ import annotations

import secrets
import threading
import time
import urllib.parse

from proxy import nodesource, nodeweights

CONTRACT = "1.0"
CONFIRM_S = 300.0
POLL_S = 0.5
GIB = float(1 << 30)
JOBS_SHOWN = 10


class WeightsError(Exception):
    def __init__(self, status: int, kind: str, message: str):
        super().__init__(message)
        self.status, self.kind, self.message = status, kind, message


class _Stream:
    """A file from the source's agent as a stream to read (the relay's opener)."""

    def __init__(self, conn, r):
        self.conn, self.r = conn, r

    def read(self, n: int) -> bytes:
        return self.r.read(n)

    def close(self) -> None:
        self.conn.close()


class Weights:
    def __init__(self, fleet):
        self.fleet = fleet
        self.cfg = fleet.cfg
        self.clock = fleet.clock
        self.sleep = time.sleep
        self._tokens: dict[str, tuple[str, str, float]] = {}
        self._lock = threading.Lock()
        self._stops: dict[str, threading.Event] = {}
        self._live: dict[str, dict] = {}            # running jobs, written to the store each step

    # ------------------------------------------------------------------ the matrix
    def _sources(self) -> dict:
        self.fleet.ensure_local()
        return {row["id"]: (row, src) for row, src in self.fleet.sources()}

    def _packs(self, src) -> tuple[list[dict], dict | None, str | None]:
        """(packs, store, why none) of one node."""
        if not isinstance(src, nodesource.AgentNode):
            return [], None, "read without its TandemNode agent: its weights are not listed"
        try:
            st, doc = src.client.json("GET", "/node/weights", timeout=30.0)
        except Exception as exc:                                     # noqa: BLE001
            return [], None, f"its agent does not answer: {type(exc).__name__}"
        if st != 200 or not isinstance(doc, dict):
            return [], None, f"its agent answered HTTP {st}"
        return [w for w in doc.get("weights") or [] if isinstance(w, dict)], doc.get("store"), \
            None

    def matrix(self) -> dict:
        nodes, packs = [], {}
        copying = {}
        for j in self._jobs():
            if j["state"] == "running":
                copying[(j["to"], j["profile"])] = j["id"]
        for nid, (row, src) in self._sources().items():
            got, store, why = self._packs(src)
            nodes.append({"node": nid, "alias": row["alias"], "status": src.status()[0],
                          "store": (store or {}).get("dir"),
                          "free_gib": (store or {}).get("free_gib"), "note": why})
            for w in got:
                name = w.get("profile") or w.get("name")
                if not name:
                    continue
                p = packs.setdefault(name, {"profile": name, "model": w.get("model"),
                                            "nodes": {}})
                p["model"] = p["model"] or w.get("model")
                p["nodes"][nid] = {"node": nid, "present": True, "source": w.get("source") or "setup",
                                   "dir": w.get("dir"), "bytes": w.get("bytes"),
                                   "gib": round((w.get("bytes") or 0) / GIB, 2)
                                   if w.get("bytes") is not None else w.get("gib"),
                                   "content_id": w.get("content_id"), "copy": None}
        for (to, name), jid in copying.items():
            p = packs.setdefault(name, {"profile": name, "model": None, "nodes": {}})
            p["nodes"].setdefault(to, {"node": to, "present": False, "source": "store",
                                       "dir": None,
                                       "bytes": None, "gib": None, "content_id": None,
                                       "copy": None})["copy"] = jid
        for p in packs.values():
            ids = {x["content_id"] for x in p["nodes"].values() if x["content_id"]}
            p["same"] = len(ids) <= 1
            p["nodes"] = list(p["nodes"].values())
        return {"contract_version": CONTRACT, "nodes": nodes,
                "packs": [packs[k] for k in sorted(packs)], "copies": self._jobs()[:JOBS_SHOWN]}

    def _jobs(self) -> list[dict]:
        out = []
        with self.cfg._lock:
            rows = self.cfg._conn.execute(
                "SELECT doc FROM fleet_jobs WHERE kind = 'weights' ORDER BY created_at DESC, "
                "rowid DESC LIMIT ?", (JOBS_SHOWN,)).fetchall()
        import json
        for (doc,) in rows:
            j = json.loads(doc)
            out.append(self._live.get(j["id"], j))
        return out

    # ------------------------------------------------------------------ a copy
    def copy(self, body, who: str, now: float) -> dict:
        if not isinstance(body, dict) or set(body) - {"from", "to", "profile"} or \
                not all(isinstance(body.get(k), str) for k in ("from", "to", "profile")):
            raise WeightsError(400, "bad_request", "the body is {from, to, profile}")
        try:
            nodeweights.check_name(body["profile"])
        except nodeweights.WeightsError as exc:
            raise WeightsError(exc.status, exc.kind, exc.message)
        srcs = self._sources()
        for k in ("from", "to"):
            if body[k] not in srcs:
                raise WeightsError(404, "not_found", f"no node {body[k]}")
        if body["from"] == body["to"]:
            raise WeightsError(400, "bad_request", "a copy goes to another node")
        (frow, fsrc), (trow, tsrc) = srcs[body["from"]], srcs[body["to"]]
        for row, src in ((frow, fsrc), (trow, tsrc)):
            if not isinstance(src, nodesource.AgentNode):
                raise WeightsError(409, "no_agent", f"{row['alias']} is read without its "
                                                    f"TandemNode agent: a copy goes agent to agent")
            if src.status()[0] == "off":
                raise WeightsError(409, "unreachable", f"{row['alias']} does not answer")
        for j in self._jobs():
            if j["state"] == "running" and j["to"] == body["to"]:
                raise WeightsError(409, "busy", f"a copy to {trow['alias']} runs: {j['profile']}")
        via, why = self._via(frow, trow)
        job = {"id": f"weights-{int(now * 1000)}-{secrets.token_hex(3)}", "kind": "weights",
               "state": "running", "from": frow["id"], "to": trow["id"],
               "from_alias": frow["alias"], "to_alias": trow["alias"],
               "profile": body["profile"], "via": via, "via_why": why, "by": who,
               "started_at": round(now, 3), "ended_at": None, "bytes_total": None,
               "bytes_done": 0, "files_total": None, "files_done": 0, "rate_gbps": None,
               "content_id": {"source": None, "expected": None, "copy": None},
               "verified": False, "log": [], "error": None}
        stop = threading.Event()
        with self._lock:
            self._stops[job["id"]] = stop
            self._live[job["id"]] = job
        self.cfg.put_job(job)
        threading.Thread(target=self._run, args=(job, fsrc, tsrc, frow, trow, stop),
                         name="weights-copy", daemon=True).start()
        return {"contract_version": CONTRACT, "job": job}

    @staticmethod
    def _via(frow: dict, trow: dict) -> tuple[str, str]:
        """`direct` (source agent to receiver agent) when both are paired over mTLS on the LAN,
        else `relay` through the controller, with why."""
        for row in (frow, trow):
            t = row["trust"] or {}
            if row["transport"] == "tunnel":
                return "relay", f"{row['alias']} is reached through an SSH tunnel"
            if t.get("kind") != "mtls" or not (row["endpoint"] or "").startswith("https://"):
                return "relay", f"{row['alias']} listens on loopback only (the controller's own " \
                                f"machine)"
        return "direct", "both nodes are paired over mTLS on the LAN"

    def _say(self, job: dict, text: str) -> None:
        job["log"] = (job["log"] + [{"t": round(self.clock(), 3), "text": text}])[-100:]
        self.cfg.put_job(job)
        self.fleet.log(f"[weights] {job['id']}: {text}")

    def _run(self, job, fsrc, tsrc, frow, trow, stop) -> None:
        copy = job["id"]
        opened = False
        try:
            name = job["profile"]
            st, man = fsrc.client.json("GET", f"/node/weights/{urllib.parse.quote(name)}/manifest",
                                       timeout=600.0)
            if st != 200 or not isinstance(man, dict):
                raise WeightsError(404 if st == 404 else 502, "not_found" if st == 404 else
                                   "refused", f"{frow['alias']}: " + (((man or {}).get("error")
                                                                       or {}).get("message")
                                                                      or f"HTTP {st}"))
            files = man["files"]
            job.update(bytes_total=man["bytes"], files_total=len(files))
            job["content_id"]["source"] = man.get("content_id")
            self._say(job, f"{len(files)} files, {man['bytes'] / GIB:.2f} GiB, {job['via']}: "
                           f"{job['via_why']}")
            st, rcv = tsrc.post("/node/weights/receive", {"copy": copy, "profile": name,
                                                          "files": files, "from": frow["id"]},
                                timeout=60.0)
            if st != 200:
                err = (rcv.get("error") or {})
                raise WeightsError(st if st >= 400 else 502, err.get("type") or "refused",
                                   f"{trow['alias']}: {err.get('message') or f'HTTP {st}'}")
            opened = True
            t0 = time.monotonic()
            if job["via"] == "direct":
                hashes = self._direct(job, fsrc, tsrc, frow, trow, files, rcv["token"], stop, t0)
            else:
                hashes = self._relay(job, fsrc, tsrc, files, rcv["token"], stop, t0)
            st, done = tsrc.post(f"/node/weights/receive/{copy}/commit", None, timeout=120.0)
            if st != 200:
                err = (done.get("error") or {})
                raise WeightsError(502, err.get("type") or "commit_failed",
                                   f"{trow['alias']}: {err.get('message') or f'HTTP {st}'}")
            opened = False
            src_id = man.get("content_id") if man.get("content_basis") == "hub_manifest" else \
                None
            expected = src_id or nodeweights.content_id_of({r: tuple(v) for r, v in
                                                            hashes.items()})
            job["content_id"].update(expected=expected, copy=done.get("content_id"))
            job["verified"] = done.get("content_id") == expected
            dt = max(1e-6, time.monotonic() - t0)
            job["rate_gbps"] = round(job["bytes_done"] * 8 / dt / 1e9, 3)
            if not job["verified"]:
                raise WeightsError(502, "content_mismatch", f"{trow['alias']}'s copy has the "
                                                            f"content id {done.get('content_id')}"
                                                            f", not {expected}")
            job.update(state="done", ended_at=round(self.clock(), 3))
            self._say(job, f"verified: {expected}")
            self.fleet.store.add_event(
                "weights", trow["id"], f"Weights copied {frow['alias']} → {trow['alias']}: {name}",
                f"{job['bytes_done'] / GIB:.2f} GiB in {dt:.1f} s ({job['rate_gbps']} Gb/s, "
                f"{job['via']}); content id {expected[:23]}… verified")
        except Exception as exc:                                     # noqa: BLE001
            err = exc if isinstance(exc, (WeightsError, nodeweights.WeightsError)) else None
            job.update(state="stopped" if stop.is_set() else "failed",
                       ended_at=round(self.clock(), 3),
                       error={"type": err.kind if err else "error",
                              "message": err.message if err else f"{type(exc).__name__}: {exc}"})
            if opened:
                try:
                    tsrc.client.json("DELETE", f"/node/weights/receive/{copy}", timeout=30.0)
                except Exception:                                    # noqa: BLE001
                    pass
            self._say(job, f"{job['state']}: {job['error']['message']}")
            self.fleet.store.add_event(
                "weights", trow["id"], f"Weights copy {frow['alias']} → {trow['alias']} "
                                       f"{job['state']}: {job['profile']}",
                job["error"]["message"], severity="err" if job["state"] == "failed" else None)
        finally:
            with self._lock:
                self._stops.pop(copy, None)
                self._live.pop(copy, None)
            self.cfg.put_job(job)

    def _relay(self, job, fsrc, tsrc, files, token, stop, t0) -> dict:
        prof = urllib.parse.quote(job["profile"])

        def opener(rel):
            conn = fsrc.client.connection(timeout=300.0)
            conn.request("GET", f"/node/weights/{prof}/files/{urllib.parse.quote(rel)}",
                         headers=fsrc.client.headers())
            r = conn.getresponse()
            if r.status != 200:
                conn.close()
                raise WeightsError(502, "refused", f"the source gave HTTP {r.status} for {rel}")
            return _Stream(conn, r)
        sent = {"hashes": {}, "bytes_done": 0, "files_done": 0}
        ticker = self._ticker(job, sent, t0, stop)
        try:
            nodeweights.push(files, opener, tsrc.client, job["id"], token, sent, stop)
        finally:
            ticker.set()
        job.update(bytes_done=sent["bytes_done"], files_done=sent["files_done"])
        return sent["hashes"]

    def _ticker(self, job, sent, t0, stop) -> threading.Event:
        """The relay's progress written to the job every POLL_S."""
        done = threading.Event()

        def run():
            while not done.wait(POLL_S):
                job.update(bytes_done=sent["bytes_done"], files_done=sent["files_done"],
                           rate_gbps=round(sent["bytes_done"] * 8 /
                                           max(1e-6, time.monotonic() - t0) / 1e9, 3))
        threading.Thread(target=run, daemon=True).start()
        return done

    def _direct(self, job, fsrc, tsrc, frow, trow, files, token, stop, t0) -> dict:
        """The source's agent pushes to the receiver's: each pinned on the other first."""
        for at, src, other in ((trow, tsrc, frow), (frow, fsrc, trow)):
            st, doc = src.client.json("PUT", f"/node/peers/{other['id']}",
                                      {"certificate": (other["trust"] or {}).get("pin")},
                                      timeout=30.0)
            if st != 200:
                raise WeightsError(502, "pin_failed", f"{at['alias']} did not pin "
                                                      f"{other['alias']}: HTTP {st}")
        self.sleep(0.3)                          # the agents reload their trust (0.1 s)
        to = trow["endpoint"]           # its paired LAN address: the address its agent listens on
        st, doc = fsrc.post("/node/weights/send", {"copy": job["id"], "profile": job["profile"],
                                                   "to": to, "token": token}, timeout=60.0)
        if st != 202:
            err = (doc.get("error") or {})
            raise WeightsError(502, err.get("type") or "refused",
                               f"{frow['alias']}: {err.get('message') or f'HTTP {st}'}")
        self._say(job, f"{frow['alias']} sends to {to}")
        while True:
            if stop.is_set():
                fsrc.client.json("DELETE", f"/node/weights/send/{job['id']}", timeout=10.0)
            st, s = fsrc.client.json("GET", f"/node/weights/send/{job['id']}", timeout=30.0)
            if st == 200 and isinstance(s, dict):
                job.update(bytes_done=s["bytes_done"], files_done=s["files_done"],
                           rate_gbps=round(s["bytes_done"] * 8 /
                                           max(1e-6, time.monotonic() - t0) / 1e9, 3))
                if s["state"] == "done":
                    return s["hashes"]
                if s["state"] != "running":
                    e = s.get("error") or {}
                    raise WeightsError(502, e.get("type") or "send_failed",
                                       f"{frow['alias']}: {e.get('message') or s['state']}")
            self.sleep(POLL_S)

    def view(self, jid: str) -> dict:
        j = self._live.get(jid) or self.cfg.job("weights", jid)
        if j is None or j.get("kind") != "weights":
            raise WeightsError(404, "not_found", f"no weights copy {jid}")
        return {"contract_version": CONTRACT, "job": j}

    def stop(self, jid: str) -> dict:
        self.view(jid)
        with self._lock:
            ev = self._stops.get(jid)
        if ev is None:
            raise WeightsError(409, "conflict", "this copy is not running")
        ev.set()
        return self.view(jid)

    def interrupted(self) -> None:
        for j in self._jobs():
            if j["state"] == "running" and j["id"] not in self._live:
                j.update(state="failed", ended_at=round(self.clock(), 3),
                         error={"type": "interrupted",
                                "message": "the controller stopped during the copy"})
                self.cfg.put_job(j)

    # ------------------------------------------------------------------ a removal
    def _in_use(self, nid: str, name: str) -> list[str]:
        out = []
        for d in self.fleet.deployments():
            if d["status"] == "stopped" or nid not in d["members"]:
                continue
            if d.get("pack") == name:
                out.append(d["name"])
        row = self.cfg.node(nid)
        src = self.fleet.source(row) if row else None
        if isinstance(src, nodesource.AgentNode):
            for e in src.agent_engines():
                if ((e.get("health") or {}).get("weights")) == name and e.get("state") == "up":
                    out.append(f"its engine {e.get('id')}")
        return out

    def plan_delete(self, nid: str, name: str, now: float) -> dict:
        srcs = self._sources()
        if nid not in srcs:
            raise WeightsError(404, "not_found", f"no node {nid}")
        row, src = srcs[nid]
        packs, _, why = self._packs(src)
        w = next((x for x in packs if (x.get("profile") or x.get("name")) == name), None)
        p = {"contract_version": CONTRACT, "node": nid, "profile": name,
             "title": f"Delete {name} on {row['alias']}", "allowed": True, "blocked": None,
             "impact": [], "keeps": [], "bytes": None, "confirm": None, "expires_at": None}
        if w is None:
            p.update(allowed=False, blocked=why or f"{row['alias']} has no {name}")
        else:
            p["bytes"] = w.get("bytes")
            used = self._in_use(nid, name)
            if used:
                p.update(allowed=False, blocked=f"{', '.join(used)} serves {name} on "
                                                f"{row['alias']}: stop it first")
            gib = f"{(w.get('bytes') or 0) / GIB:.2f} GiB" if w.get("bytes") is not None else \
                "its space"
            p["impact"].append(f"{name} is removed from {row['alias']} ({w.get('dir')}): {gib} "
                               f"free again")
            others = [r["alias"] for k, (r, s) in srcs.items() if k != nid]
            p["keeps"].append(f"{name} on every other node" + (f" ({', '.join(others)})"
                                                               if others else ""))
        if p["allowed"]:
            token = secrets.token_urlsafe(18)
            with self._lock:
                self._tokens = {k: v for k, v in self._tokens.items() if v[2] > now}
                self._tokens[token] = (nid, name, now + CONFIRM_S)
            p["confirm"], p["expires_at"] = token, round(now + CONFIRM_S, 3)
        return p

    def delete(self, nid: str, name: str, token, who: str, now: float) -> dict:
        p = self.plan_delete(nid, name, now)
        if not p["allowed"]:
            raise WeightsError(409, "in_use" if "stop it first" in (p["blocked"] or "") else
                               "not_found", p["blocked"])
        if not token:
            raise WeightsError(428, "confirm_required", "a removal needs the confirm of its plan "
                                                        f"(GET /v1/fleet/weights/{nid}/{name})")
        with self._lock:
            self._tokens.pop(p["confirm"], None)
            got = self._tokens.pop(token, None)
        if got is None or got[:2] != (nid, name) or got[2] < now:
            raise WeightsError(409, "confirm_invalid", "this confirm is spent, stale or another "
                                                       "pack's: read the plan again")
        row, src = self._sources()[nid]
        st, doc = src.client.json("DELETE", f"/node/weights/{urllib.parse.quote(name)}"
                                            f"?confirm=1", timeout=120.0)
        if st != 200:
            err = (doc or {}).get("error") or {}
            raise WeightsError(st if st >= 400 else 502, err.get("type") or "refused",
                               err.get("message") or f"HTTP {st}")
        self.fleet.store.add_event("weights", nid, f"{name} deleted on {row['alias']}",
                                   f"{(p['bytes'] or 0) / GIB:.2f} GiB freed · by {who}")
        return {"contract_version": CONTRACT, "node": nid, "profile": name, "deleted": True,
                "bytes": p["bytes"]}

    # ------------------------------------------------------------------ the routes
    def handle(self, method: str, parts: list[str], q: dict, body, who: str, now: float):
        p = parts[1:]
        if method == "GET" and not p:
            return self.matrix()
        if p[:1] == ["copy"]:
            if method == "POST" and len(p) == 1:
                return self.copy(body, who, now)
            if method == "GET" and len(p) == 2:
                return self.view(p[1])
            if method == "POST" and len(p) == 3 and p[2] == "stop":
                return self.stop(p[1])
        elif len(p) == 2:
            if method == "GET":
                return self.plan_delete(p[0], p[1], now)
            if method == "DELETE":
                return self.delete(p[0], p[1], q.get("confirm"), who, now)
        raise WeightsError(404, "not_found", f"no route {method} /v1/fleet/{'/'.join(parts)}")
