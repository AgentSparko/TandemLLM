"""The fleet update (OPS-234, concept A12): one release on every node, TandemNode and the engine's
runtime, without taking a model down that more than one node serves.

    up = Updater(fleet)                               one per Fleet (fleet.updater())
    GET  /v1/fleet/update                             {target, job}: the last update and its
                                                      progress per node, with its log
    GET  /v1/fleet/update/plan[?nodes=a,b]            the order, what each step stops, what is
                                                      refused and why; a confirm (single use)
    POST /v1/fleet/update {confirm, nodes?}           the job (428 without the confirm, 409 with
                                                      a spent one or a plan that is blocked)
    POST /v1/fleet/update/stop                        stop after the step that runs

THE TARGET is the release of the package this controller installs (proxy/nodeinstall.py: its own
copy, or TANDEM_NODE_WHEEL), local label included: a node needs the update when the TandemNode it
runs reports another version. The mixed-version alert (proxy/fleetwatch.py) compares releases
only; the update compares the whole version, so a build of the release (`0.15.0+fix`) rolls too.
The engine's runtime goes with it as Add Spark installs it (proxy/nodeinstall.py THE ENGINE): the
installer TANDEM_ENGINE_INSTALL_SH names, at `engine_ref()` (TANDEM_ENGINE_REF, else the
installer's VERSION, and only without both this controller's release). Without that variable a
node's agent skips its engine step and says so.

THE ORDER (`order`, the same rules as the dashboard's lib/fleet-update.ts):

    idle      a node that serves nothing: updated first, without a drain
    replica   a node whose every deployment another member serves too (Replicas): drained, updated,
              rejoined, one node at a time, so the model stays served by the others
    single    a node with a deployment no other node serves: drained (new requests wait, or go to
              its route's fallback), updated, rejoined; the plan names the downtime
    split     the members of a Split, together in one step: a Split runs one release on every
              member, so it never updates partly. A Split with a member that cannot be updated
              (off, no agent) is refused and stays as it is; a selection of nodes that takes one
              member of a Split and not another blocks the plan

One node at a time otherwise; a Replicas member waits until another member serves before it
drains (REPLICA_WAIT_S at most, then its step fails and the job stops: the model would go down).

EACH NODE: drain (the node's drain, proxy/fleetops.py: the gateway sends it no new request, and a
Replicas member no new conversation; its conversations go to another member at their next turn;
one a person set stays after the update), the requests in flight finished (DRAIN_WAIT_S), the
package sent to the agent and its update job (proxy/nodeupdate.py: pip into its environment, the
engine's runtime, the agent restarted on the new code), the agent's version checked, then the
rejoin: its engines healthy AND each deployment it serves loaded and warm (one token from the
model, proxy/deployments.py `warm_node`), only then is the drain ended (REJOIN_WAIT_S for both).
The job is kept in `fleet_jobs` at every step; a controller that restarts during it resumes it
where it was: each phase checks first whether it is done already (a node that runs the target
skips the update, a drained node the wait).

DEPLOYMENTS are the records (proxy/deployments.py, SRV-731): Replicas and Split are what a record
says; a node with an engine nobody manages and no record (the box's own engine) serves a derived
Single, so it updates as `single` with its downtime named.

TIMEOUTS: DRAIN_WAIT_S 600 s for the requests in flight on a drained node, UPDATE_WAIT_S 1800 s
for the agent's update, REJOIN_WAIT_S 300 s for the engines up and the warm-up, REPLICA_WAIT_S
300 s for another Replicas member to serve before one drains. Past any of them the step fails
and the job stops (the node stays drained).
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets as _secrets
import threading
import time
from urllib.parse import quote

from proxy import _version, nodeinstall, nodesource
from proxy.nodeupdate import norm

CONTRACT = "1.0"
CONFIRM_S = 300.0
DRAIN_WAIT_S = 600.0
UPDATE_WAIT_S = 1800.0
REJOIN_WAIT_S = 300.0
REPLICA_WAIT_S = 300.0
POLL_S = 1.0
KINDS = ("idle", "replica", "single", "split")
LOG_MAX = 400


class UpdateError(Exception):
    def __init__(self, status: int, kind: str, message: str):
        super().__init__(message)
        self.status, self.kind, self.message = status, kind, message


def needs(version: str | None, target: str) -> bool:
    return bool(target) and norm(version) != norm(target)


def order(nodes: list[dict], deployments: list[dict], target: str,
          only: list[str] | None = None) -> dict:
    """The update's steps. `nodes`: [{id, alias, version, status, updatable, why}] (updatable: an
    agent that updates itself; `why` when not); `deployments`: [{id, name, type, members,
    status}]. {steps, skipped, refused, blocked, current}."""
    by = {n["id"]: n for n in nodes}
    want = {n["id"] for n in nodes if needs(n.get("version"), target)}
    if only is not None:
        unknown = [x for x in only if x not in by]
        if unknown:
            return {"steps": [], "skipped": [], "refused": [], "current": [],
                    "blocked": f"no node {unknown[0]} in this fleet"}
    picked = set(only) if only is not None else set(by)
    skipped, refused, blocked = [], [], None
    current = sorted((by[i]["alias"] for i in by if i not in want), key=str.lower)
    serving = {d["id"]: [m for m in d["members"] if m in by and by[m]["status"] in
                         ("up", "alert")] for d in deployments}
    splits = [d for d in deployments if d["type"] == "split"]
    in_split: dict[str, dict] = {}
    for d in splits:
        need = [m for m in d["members"] if m in want]
        if not need:
            continue
        sel = [m for m in need if m in picked]
        if sel and len(sel) < len(need):
            left = [by[m]["alias"] for m in need if m not in picked]
            blocked = (f"{d['name']} runs Split over {', '.join(by[m]['alias'] for m in d['members'] if m in by)}"
                       f": its members update together, so {', '.join(left)} must be in the update too")
            for m in d["members"]:
                in_split[m] = None
            continue
        if not sel:
            continue
        bad = [m for m in d["members"] if m not in by or not by[m].get("updatable")]
        if bad:
            refused.append({"deployment": d["id"], "name": d["name"],
                            "nodes": [m for m in d["members"]],
                            "why": f"{d['name']} runs Split: {', '.join(by[m]['alias'] if m in by else m for m in bad)} "
                                   f"cannot be updated now ({'; '.join(by[m].get('why') or 'off' for m in bad if m in by) or 'gone'}), "
                                   f"and a Split never updates partly: it stays on its version"})
            for m in d["members"]:
                in_split[m] = None                     # refused with its Split
            continue
        for m in d["members"]:
            in_split[m] = d
    steps = []
    done = set()
    for d in splits:
        members = [m for m in d["members"] if in_split.get(m) is d and m in want]
        if not members or any(m in done for m in members):
            continue
        done.update(members)
        other = [x for x in deployments if x is not d and any(m in x["members"] for m in members)]
        steps.append({"kind": "split", "nodes": members, "drain": True,
                      "deployments": [d["id"]] + [x["id"] for x in other],
                      "downtime": f"{d['name']} is down while its members update together"})
    for nid in sorted(want, key=lambda i: by[i]["alias"].lower()):
        if nid in done or nid in in_split:
            continue
        n = by[nid]
        if nid not in picked:
            continue
        if not n.get("updatable"):
            skipped.append({"node": nid, "why": n.get("why") or "it cannot be updated now"})
            continue
        deps = [d for d in deployments if nid in d["members"]]
        alone = [d for d in deps if not [m for m in serving.get(d["id"], []) if m != nid]]
        kind = "idle" if not deps else "single" if alone else "replica"
        steps.append({"kind": kind, "nodes": [nid], "drain": kind != "idle",
                      "deployments": [d["id"] for d in deps],
                      "downtime": None if kind != "single" else
                      f"{', '.join(d['name'] for d in alone)} not served while {n['alias']} "
                      f"updates, unless its route has a fallback"})
    rank = {k: i for i, k in enumerate(KINDS)}
    steps.sort(key=lambda s: (rank[s["kind"]], by[s["nodes"][0]]["alias"].lower()))
    for i, s in enumerate(steps):
        s["n"] = i + 1
    return {"steps": steps, "skipped": skipped, "refused": refused, "blocked": blocked,
            "current": current}


class Updater:
    def __init__(self, fleet, *, clock=time.time, spawn=None, sleep=time.sleep, package=None):
        self.fleet = fleet
        self.clock, self.sleep = clock, sleep
        self.spawn = spawn or (lambda fn: threading.Thread(target=fn, name="fleet-update",
                                                           daemon=True).start())
        self._package = package
        self._tokens: dict[str, tuple] = {}
        self._lock = threading.RLock()
        self._running = False
        self.resumed: str | None = None

    @property
    def cfg(self):
        return self.fleet.cfg

    def package(self):
        if self._package is None:
            self._package = self.fleet.wizard().package()
        return self._package

    def target(self) -> str:
        return self.package().target()

    # ------------------------------------------------------------------ resume
    def resume(self) -> None:
        """A job the controller was running when it stopped: run on from where it was."""
        job = self.cfg.job("update")
        if job and job["state"] == "running" and not self._running:
            self.resumed = job["id"]
            self._say(job, "the controller restarted: the update resumes where it was")
            self.cfg.put_job(job)
            self._start(job)

    def running(self) -> bool:
        job = self.cfg.job("update")
        return bool(job and job["state"] == "running")

    # ------------------------------------------------------------------ the nodes
    def _nodes(self) -> list[dict]:
        out = []
        for row, src in self.fleet.sources():
            st = src.status()[0]
            agent = isinstance(src, nodesource.AgentNode)
            v = src.agent_version() if agent else (src.version() or None)
            why = None
            if not agent:
                why = ("read without its TandemNode agent: update it on the machine"
                       if row["endpoint"] == "local" else src.status()[1])
            elif st == "off":
                why = f"does not answer: {src.status()[1]}"
            else:
                u = self._agent_update(src)
                if u is None:
                    why = "its TandemNode is older and has no update route: update it once by hand"
                elif not u.get("managed"):
                    why = u.get("why") or "its agent does not update itself here"
            out.append({"id": row["id"], "alias": row["alias"], "version": v,
                        "status": "draining" if row["drain"] and st != "off" else st,
                        "updatable": why is None, "why": why,
                        "virtual": bool((src.identity() or {}).get("virtual")) if agent else False,
                        "drained_before": bool(row["drain"]) and (row["drain"] or {}).get("by")
                        != "fleet update"})
        return out

    @staticmethod
    def _agent_update(src) -> dict | None:
        try:
            st, doc = src.client.json("GET", "/node/update", timeout=5.0)
        except Exception:                                            # noqa: BLE001
            return None
        return doc if st == 200 and isinstance(doc, dict) else None

    # ------------------------------------------------------------------ the plan
    def plan(self, q: dict, now: float) -> dict:
        only = [x for x in (q.get("nodes") or "").split(",") if x] or None
        return self._plan(only, now)[0]

    def _plan(self, only, now: float, mint: bool = True) -> tuple[dict, list[dict]]:
        target = self.target()
        nodes = self._nodes()
        deps = self.fleet.deployments()
        got = order(nodes, deps, target, only)
        by = {n["id"]: n for n in nodes}
        names = {d["id"]: d["name"] for d in deps}
        for s in got["steps"]:
            a = ", ".join(by[x]["alias"] for x in s["nodes"])
            served = [names[d] for d in s["deployments"] if d in names]
            s["words"] = {"idle": f"{a}: serves nothing; updated without a drain",
                          "replica": f"{a}: drain, update, rejoin; {', '.join(served)} stays "
                                     f"served by the other member{'s' if len(served) > 1 else ''}",
                          "single": f"{a}: drain, update, rejoin",
                          "split": f"{a} together: drain, update, rejoin"}[s["kind"]]
        running = self.running()
        blocked = got["blocked"] or ("an update runs" if running else None) or \
            (None if got["steps"] else f"every node runs {target} already" if not got["refused"]
             and not got["skipped"] else "no node can be updated now")
        p = {"contract_version": CONTRACT, "target": target,
             "controller": _version.release() or None, "allowed": blocked is None,
             "blocked": blocked, "steps": got["steps"], "skipped": got["skipped"],
             "refused": got["refused"], "current": got["current"],
             "nodes": [{k: n[k] for k in ("id", "alias", "version", "status", "updatable", "why")}
                       for n in nodes], "selected": only, "confirm": None, "expires_at": None}
        if p["allowed"] and mint:
            token = _secrets.token_urlsafe(18)
            with self._lock:
                self._tokens = {k: v for k, v in self._tokens.items() if v[2] > now}
                self._tokens[token] = (target, ",".join(only or []), now + CONFIRM_S)
            p["confirm"], p["expires_at"] = token, round(now + CONFIRM_S, 3)
        return p, nodes

    # ------------------------------------------------------------------ start, stop
    def start(self, body, who: str, now: float) -> dict:
        if body is None or not isinstance(body, dict) or set(body) - {"confirm", "nodes"}:
            raise UpdateError(400, "bad_request", "the body is {confirm, nodes?}")
        only = body.get("nodes")
        if only is not None and (not isinstance(only, list) or
                                 not all(isinstance(x, str) for x in only)):
            raise UpdateError(400, "bad_request", "nodes is a list of node ids")
        p, nodes = self._plan(only or None, now, mint=False)
        if not p["allowed"]:
            raise UpdateError(409, "partial_split" if "Split" in (p["blocked"] or "") else
                              "conflict", p["blocked"])
        tok = body.get("confirm")
        if not tok:
            raise UpdateError(428, "confirm_required", "the fleet update needs its plan's "
                                                       "confirm: GET /v1/fleet/update/plan first")
        with self._lock:
            got = self._tokens.pop(tok, None) if isinstance(tok, str) else None
        if got is None or got[:2] != (p["target"], ",".join(only or [])) or got[2] <= now:
            raise UpdateError(409, "confirm_invalid", "that confirm is spent, too old or another "
                                                      "plan's: read the plan again")
        by = {n["id"]: n for n in nodes}
        job = {"id": "u-" + _secrets.token_hex(6), "kind": "update", "state": "running",
               "target": p["target"], "by": who, "started_at": round(now, 3), "ended_at": None,
               "stop": False, "error": None, "log": [], "refused": p["refused"],
               "skipped": p["skipped"],
               "steps": [dict(s, state="todo") for s in p["steps"]],
               "nodes": {nid: {"alias": by[nid]["alias"], "from": by[nid]["version"],
                               "to": p["target"], "phase": "todo", "detail": None,
                               "started_at": None, "ended_at": None,
                               "drained_before": by[nid]["drained_before"]}
                         for s in p["steps"] for nid in s["nodes"]}}
        self._say(job, f"update to {p['target']} started by {who}: "
                       + "; ".join(s["words"] for s in p["steps"]))
        self.cfg.put_job(job)
        self.fleet.store.add_event("update", "all", f"Fleet update to {p['target']} started",
                                   f"{len(p['steps'])} steps · by {who}")
        self._start(job)
        return self.view(now)

    def stop(self, who: str, now: float) -> dict:
        job = self.cfg.job("update")
        if not job or job["state"] != "running":
            raise UpdateError(409, "conflict", "no update runs")
        job["stop"] = True
        self._say(job, f"stop asked by {who}: the update stops after the step that runs")
        self.cfg.put_job(job)
        return self.view(now)

    def view(self, now: float) -> dict:
        return {"contract_version": CONTRACT, "target": self.target(),
                "job": self.cfg.job("update")}

    def _start(self, job: dict) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
        self.spawn(lambda: self._run(job["id"]))

    # ------------------------------------------------------------------ the job
    def _say(self, job: dict, text: str) -> None:
        job["log"].append({"t": round(self.clock(), 3), "text": text[:300]})
        del job["log"][:-LOG_MAX]
        self.fleet.log(f"[fleet] update: {text}")

    def _node(self, job, nid, phase, detail=None) -> None:
        n = job["nodes"][nid]
        n["phase"] = phase
        if detail is not None:
            n["detail"] = detail
        if phase in ("drain", "update") and n["started_at"] is None:
            n["started_at"] = round(self.clock(), 3)
        if phase in ("done", "failed", "skipped"):
            n["ended_at"] = round(self.clock(), 3)
        self.cfg.put_job(job)

    def _run(self, jid: str) -> None:
        job = self.cfg.job("update", jid)
        try:
            for s in job["steps"]:
                if s["state"] in ("done", "failed"):
                    continue
                if job.get("stop"):
                    job.update(state="stopped", ended_at=round(self.clock(), 3))
                    self._say(job, "stopped before step "
                                   f"{s['n']}: {', '.join(job['nodes'][x]['alias'] for x in s['nodes'])} "
                                   f"stay on their version")
                    self.cfg.put_job(job)
                    self.fleet.store.add_event("update", "all", "Fleet update stopped",
                                               f"before step {s['n']}")
                    return
                s["state"] = "running"
                self.cfg.put_job(job)
                try:
                    self._step(job, s)
                    s["state"] = "done"
                except UpdateError as exc:
                    s["state"] = "failed"
                    for nid in s["nodes"]:
                        if job["nodes"][nid]["phase"] != "done":
                            self._node(job, nid, "failed", exc.message)
                    job.update(state="failed", ended_at=round(self.clock(), 3),
                               error={"type": exc.kind, "message": exc.message})
                    self._say(job, f"step {s['n']} failed: {exc.message}; the update stops "
                                   f"here")
                    self.cfg.put_job(job)
                    self.fleet.store.add_event("update", s["nodes"][0], "Fleet update failed",
                                               exc.message, severity="err")
                    return
                self.cfg.put_job(job)
            job.update(state="done", ended_at=round(self.clock(), 3))
            self._say(job, f"every step done: the fleet runs {job['target']}")
            self.cfg.put_job(job)
            self.fleet.store.add_event("update", "all", f"Fleet updated to {job['target']}",
                                       f"{len(job['nodes'])} nodes")
        except Exception as exc:                                     # noqa: BLE001
            job.update(state="failed", ended_at=round(self.clock(), 3),
                       error={"type": "error", "message": f"{type(exc).__name__}: {exc}"})
            self._say(job, f"failed: {type(exc).__name__}: {exc}")
            self.cfg.put_job(job)
        finally:
            with self._lock:
                self._running = False

    def _src(self, nid: str):
        row = self.cfg.node(nid)
        if row is None:
            raise UpdateError(409, "gone", f"node {nid} left the fleet during the update")
        return row, self.fleet.source(row)

    def _step(self, job: dict, s: dict) -> None:
        ids = [x for x in s["nodes"] if job["nodes"][x]["phase"] != "done"]
        if not ids:
            return                              # done before a restart of the controller
        if s["kind"] == "replica":
            for d in s["deployments"]:
                self._wait_other(job, ids[0], d)
        if s["drain"]:
            for nid in ids:
                self._drain(job, nid)
            for nid in ids:
                self._drained(job, nid)
        for nid in ids:
            self._send(job, nid)
        for nid in ids:
            self._updated(job, nid)
        for nid in ids:
            self._rejoin(job, nid, s["drain"])

    def _wait_other(self, job: dict, nid: str, dep: str) -> None:
        """A Replicas member drains only while another member serves."""
        end = self.clock() + REPLICA_WAIT_S
        said = False
        while True:
            d = next((x for x in self.fleet.deployments() if x["id"] == dep), None)
            if d is None or len(d["members"]) < 2:
                return
            others = [m for m in d["members"] if m != nid]
            ok = []
            for m in others:
                row = self.cfg.node(m)
                if row is None or row["drain"]:
                    continue
                if self.fleet.source(row).status()[0] in ("up", "alert"):
                    ok.append(m)
            if ok:
                return
            if self.clock() >= end:
                raise UpdateError(409, "replica_down", f"no other member of {d['name']} serves: "
                                                       f"draining {job['nodes'][nid]['alias']} "
                                                       f"would stop it")
            if not said:
                said = True
                self._say(job, f"{job['nodes'][nid]['alias']} waits: no other member of "
                               f"{d['name']} serves yet")
            self.sleep(POLL_S)

    def _drain(self, job: dict, nid: str) -> None:
        n = job["nodes"][nid]
        row, src = self._src(nid)
        if n["phase"] not in ("todo", "drain"):
            return
        self._node(job, nid, "drain", "no new requests; those in flight finish")
        if not row["drain"]:
            now = round(self.clock(), 3)
            self.cfg.update_node(nid, {"drain": {"since": now, "by": "fleet update"}},
                                 who="fleet update")
            self.fleet.store.add_event("update", nid, f"{n['alias']} drains for the update",
                                       f"to {job['target']}")
            self.fleet.ops.refresh()
        self._say(job, f"{n['alias']}: draining")

    def _drained(self, job: dict, nid: str) -> None:
        n = job["nodes"][nid]
        if n["phase"] != "drain":
            return
        end = self.clock() + DRAIN_WAIT_S
        while True:
            row, src = self._src(nid)
            left = self.fleet.ops.inflight(src)
            if left == 0:
                break
            if self.clock() >= end:
                raise UpdateError(504, "drain_timeout", f"{n['alias']} still has {left} requests "
                                                        f"in flight after {DRAIN_WAIT_S:.0f} s")
            self.sleep(POLL_S)
        self._node(job, nid, "drained", "nothing in flight")

    def _send(self, job: dict, nid: str) -> None:
        n = job["nodes"][nid]
        if n["phase"] not in ("todo", "drained", "update"):
            return
        row, src = self._src(nid)
        if not isinstance(src, nodesource.AgentNode):
            raise UpdateError(409, "no_agent", f"{n['alias']} is read without its agent")
        if not needs(src.agent_version(fresh=True), job["target"]):
            self._node(job, nid, "update", f"runs {job['target']} already")
            return
        u = self._agent_update(src) or {}
        aj = u.get("job") or {}
        if aj.get("state") == "running" and norm(aj.get("version")) == norm(job["target"]):
            self._node(job, nid, "update", "its agent's update runs")
            return
        pkg = self.package()
        # the engine's installer and the release it installs (TANDEM_ENGINE_INSTALL_SH,
        # Package.engine_ref), read before anything is sent; given no installer, the node's agent
        # skips its engine step
        try:
            ref, script = pkg.engine_ref(), pkg.install_sh()
        except nodeinstall.InstallError as exc:
            raise UpdateError(exc.status, exc.kind, exc.message)
        path = pkg.wheel()
        with open(path, "rb") as f:
            data = f.read()
        sha = hashlib.sha256(data).hexdigest()
        name = os.path.basename(path)
        self._node(job, nid, "update", f"sending {name} ({len(data) / 1e6:.1f} MB)")
        st, raw = src.client.raw("PUT", f"/node/update/package?name={quote(name)}&sha256={sha}",
                                 data, timeout=300.0)
        if st != 200:
            raise UpdateError(502, "send_failed", f"{n['alias']}'s agent took no package: HTTP "
                                                  f"{st} {raw[:200]!r}")
        body = {"name": name, "sha256": sha, "version": job["target"], "ref": ref}
        if script is not None:
            body["install_sh"] = base64.b64encode(script).decode()
        st, doc = src.post("/node/update", body, timeout=30.0)
        if st != 202:
            raise UpdateError(502, (doc.get("error") or {}).get("type") or "refused",
                              f"{n['alias']}: {(doc.get('error') or {}).get('message') or f'HTTP {st}'}")
        self._node(job, nid, "update", "installing on the node")
        self._say(job, f"{n['alias']}: installing {job['target']}")

    def _updated(self, job: dict, nid: str) -> None:
        n = job["nodes"][nid]
        if n["phase"] != "update":
            return
        end = self.clock() + UPDATE_WAIT_S
        seen = None
        while True:
            row, src = self._src(nid)
            v = src.agent_version(fresh=True) if isinstance(src, nodesource.AgentNode) else None
            u = self._agent_update(src) if isinstance(src, nodesource.AgentNode) else None
            aj = (u or {}).get("job") or {}
            # the agent's own job ends once the restarted agent runs the version
            if v is not None and not needs(v, job["target"]) and (
                    aj.get("state") != "running" or norm(aj.get("version")) != norm(job["target"])):
                break
            if aj.get("state") == "failed" and norm(aj.get("version")) == norm(job["target"]):
                raise UpdateError(502, "install_failed", f"{n['alias']}: {aj.get('message')}")
            step = next((x for x in aj.get("steps") or [] if x["state"] == "running"), None)
            if step and step["id"] != seen:
                seen = step["id"]
                self._node(job, nid, "update", step["label"])
            if self.clock() >= end:
                raise UpdateError(504, "update_timeout", f"{n['alias']} does not run "
                                                         f"{job['target']} after "
                                                         f"{UPDATE_WAIT_S:.0f} s")
            self.sleep(POLL_S)
        self._node(job, nid, "rejoin", f"runs {v}; rejoining")
        self._say(job, f"{n['alias']}: runs {v}")
        self.fleet.store.add_event("update", nid, f"{n['alias']} updated to {v}",
                                   f"from {n['from'] or 'an unknown version'}")

    def _rejoin(self, job: dict, nid: str, drained: bool) -> None:
        n = job["nodes"][nid]
        if n["phase"] != "rejoin":
            return
        end = self.clock() + REJOIN_WAIT_S
        said = None
        while True:
            row, src = self._src(nid)
            probes = src.engines() if hasattr(src, "engines") else []
            states = [self.fleet.svc.health.view(p.id)["state"] for p in probes]
            cold = []
            if src.status()[0] != "off" and all(s == "up" for s in states):
                # healthy is not enough: each deployment it serves answers one token (warm)
                cold = [f"{d}: {why}" for d, ok, why in self.fleet.deps.warm_node(nid) if not ok]
                if not cold:
                    break
                if cold[0] != said:
                    said = cold[0]
                    self._node(job, nid, "rejoin", f"up; warming up ({cold[0]})")
            if self.clock() >= end:
                raise UpdateError(504, "rejoin_timeout", f"{n['alias']} is not "
                                                         f"{'warm' if cold else 'up'} "
                                                         f"{REJOIN_WAIT_S:.0f} s after the update"
                                                         + (f": {cold[0]}" if cold else ""))
            for p in probes:
                b = self.fleet.cfg.backend(p.id)
                if b is not None:
                    try:
                        self.fleet.svc.health.check(b)
                    except Exception:                                # noqa: BLE001
                        pass
            self.sleep(POLL_S)
        if drained and not n["drained_before"] and (row["drain"] or {}).get("by") == "fleet update":
            self.cfg.update_node(nid, {"drain": None}, who="fleet update")
            self.fleet.ops.refresh()
            self.fleet.store.add_event("update", nid, f"{n['alias']} takes requests again",
                                       f"rejoined on {job['target']}")
        self._node(job, nid, "done", f"runs {job['target']}; "
                                     f"{'kept drained, as before' if n['drained_before'] else 'serves again'}")
        self._say(job, f"{n['alias']}: rejoined")

    # ------------------------------------------------------------------ the routes
    def handle(self, method: str, parts: list[str], q: dict, body, who: str, now: float):
        p = parts[1:]
        if method == "GET" and not p:
            return self.view(now)
        if method == "GET" and p == ["plan"]:
            return self.plan(q, now)
        if method == "POST" and not p:
            return self.start(body, who, now)
        if method == "POST" and p == ["stop"]:
            return self.stop(who, now)
        raise UpdateError(404, "not_found", f"no route {method} /v1/fleet/{'/'.join(parts)}")

