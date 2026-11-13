"""A Split deployment at work (SRV-817): one model as two stage engines on two nodes.

    split = Split(deps)                  one per Deployments (deps.split)
    split.check(raw, members, ctx)       a record's plan (the planner's `split`), checked
    split.members(rec, ctx)              the two stages now: process, link, the pair's checks
    split.status(rec, ms, job)           (status, words, phase)
    split.gate(rec, ms)                  the member a request goes to (the tail), or Refused
    split.start(job, rec)                the start job: the key, the front, then the tail
    split.stop(job, rec)                 the stop job: the tail, then the front, the key gone
    split.rejoin(did, say)               a fleet update's step: both stages up again, in order

THE TWO STAGES. The record's first member runs the FRONT (layers `[0, cut)`), its second the TAIL
(`[cut, layers)`). The tail is the engine clients talk to: it has the backend, the gateway routes to
it alone, and `/v1/models` lists the deployment once. The front serves no OpenAI route: it has no
backend in the record, a backend that reaches its engine is never listed and never routed to
(`fronts`). Each stage's state is read through its node's agent (`GET /node/engines/{id}`: the
engine's `/health` `stage` block in short, proxy/nodeengines.py `stage_short`), cached STAGE_TTL_S.

A MEMBER'S STATE: `serving` (its stage runs, the link is up and the pair's checks pass), `waiting`
(its stage runs, the link is down), `starting`, `down` (no stage process), `mismatch` (the engine
there is not the planned stage, or the pair's checks fail), `draining`, `off`, `gone`. Only the
tail of a pair that serves takes requests.

THE PAIR'S CHECKS, on both `/health` stage blocks: the planned ranges (`0:cut`, `cut:layers`),
the ranges meet, the same link epoch, each side's peer identity equals the other's own identity
(the handshake compared code, weights and family), equal slots and caches.

THE PHASES of a Split: `stopped`; `starting-front` and `starting-tail` (a start job); `linked`
(serving); `degraded` (both stages run, the link is down or a check fails: requests get 503
`stage_down` with Retry-After RETRY_S; the engines link again by themselves and the deployment
serves again without any action); `partial` (one stage runs, the other does not: Start brings up
what is missing, Stop ends what runs); `stopping`; `down`.

THE KEY. A start makes a link key (KEY_BYTES random bytes) and hands it to both node agents over
the node channel (`PUT /node/engines/{id}/stage`); each agent keeps it in a 0600 file and tells its
engine the file's path. The key is held in one local variable here: it is never stored in the
config database, a job, a log line, an event or an answer. A stop removes both key files.

A START THAT FAILS stops what it started (the tail first), removes the keys and leaves the record
stopped; when a stage could not be stopped the error is `partial_split` and names it.
"""

from __future__ import annotations

import base64
import json
import secrets
import threading
import time

from proxy import nodesource, splitplan

FRONT_WAIT_S = 600.0
TAIL_WAIT_S = 600.0
LINK_WAIT_S = 150.0
STOP_WAIT_S = 180.0
STAGE_TTL_S = 2.0
RETRY_S = 5
KEY_BYTES = 48
PLAN_MAX = 96 * 1024
ROLES = ("front", "tail")


def _runs(m: dict) -> bool:
    """The member's engine runs a stage now (linked or not, the planned one or another)."""
    return bool((m.get("stage") or {}).get("runs"))


def _planned(m: dict) -> bool:
    """The member's engine runs the stage the plan gives it."""
    return _runs(m) and m["state"] in ("serving", "waiting", "draining")


def _err(status: int, kind: str, message: str):
    from proxy.deployments import DeployError
    return DeployError(status, kind, message)


class Split:
    def __init__(self, deps):
        self.d = deps
        self._lock = threading.Lock()
        self._views: dict[tuple, tuple[float, dict | None]] = {}

    # ------------------------------------------------------------------ the record's plan
    def check(self, raw, nodes: list[str]) -> dict:
        """A record's plan as the planner gave it (placement's `split`), checked for what a
        start reads: the cut, the layers, both sides' ranges and nodes, the link's addresses."""
        bad = _err(400, "bad_request", "split is the plan of POST /v1/fleet/placement's Split "
                                       "(cut, layers, front, tail, link, shape)")
        if not isinstance(raw, dict):
            raise bad
        try:
            if len(json.dumps(raw)) > PLAN_MAX:
                raise bad
        except (TypeError, ValueError):
            raise bad from None
        cut, layers = raw.get("cut"), raw.get("layers")
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in (cut, layers)) or \
                not 0 < cut < layers:
            raise bad
        want = {"front": f"0:{cut}", "tail": f"{cut}:{layers}"}
        for i, role in enumerate(ROLES):
            s = raw.get(role)
            if not isinstance(s, dict) or s.get("layers") != want[role] or \
                    isinstance(s.get("need_gib"), bool) or \
                    not isinstance(s.get("need_gib"), (int, float)):
                raise bad
            if s.get("node") != nodes[i]:
                raise _err(400, "bad_request", f"split.{role}.node is the {role}'s member: the "
                                               f"members are the front's node, then the tail's")
        link = raw.get("link")
        if not isinstance(link, dict) or not isinstance(raw.get("shape"), dict):
            raise bad
        try:
            lip, lport = splitplan.address(link.get("listen"), "split.link.listen", None)
            pip, pport = splitplan.address(link.get("peer") or link.get("listen"),
                                           "split.link.peer", None)
        except splitplan.AskError as exc:
            raise _err(400, "bad_request", str(exc)) from None
        plan = dict(raw)
        plan["link"] = dict(link, listen=splitplan.join(lip, lport),
                            peer=splitplan.join(pip, pport))
        plan["built"] = False
        return plan

    @staticmethod
    def plan_of(rec: dict) -> dict | None:
        p = (rec.get("options") or {}).get("split")
        return p if isinstance(p, dict) and p.get("cut") else None

    # ------------------------------------------------------------------ what the stages say
    def read(self, m: dict, fresh: bool = False) -> dict | None:
        """The agent's view of a member's engine (its state, job, `health.stage`, the stage set
        on it); None when its agent does not answer."""
        n = m.get("_n")
        src = n["src"] if n else None
        if not isinstance(src, nodesource.AgentNode) or not m.get("engine"):
            return None
        key = (m["node"], m["engine"])
        now = time.monotonic()
        with self._lock:
            got = self._views.get(key)
            if got is not None and not fresh and now - got[0] < STAGE_TTL_S:
                return got[1]
            try:
                st, doc = src.client.json("GET", f"/node/engines/{m['engine']}", timeout=5.0)
            except Exception:                                        # noqa: BLE001
                st, doc = 0, None
            view = doc if st == 200 and isinstance(doc, dict) else None
            self._views[key] = (time.monotonic(), view)
            return view

    def forget(self) -> None:
        with self._lock:
            self._views.clear()

    @staticmethod
    def _block(view: dict | None) -> dict | None:
        b = ((view or {}).get("health") or {}).get("stage")
        return b if isinstance(b, dict) else None

    def members(self, rec: dict, ctx: dict, fresh: bool = False) -> list[dict]:
        d = self.d
        plan = self.plan_of(rec) or {}
        out = []
        for i, m in enumerate(rec["members"][:2]):
            role = ROLES[i]
            mv = d._member(ctx, m, rec)
            mv["role"], mv["api_why"], mv["check"] = role, None, None
            mv["stage"] = {"role": role, "layers": (plan.get(role) or {}).get("layers"),
                           "need_gib": (plan.get(role) or {}).get("need_gib"),
                           "budget_gib": (plan.get(role) or {}).get("budget_gib"),
                           "runs": False, "link": None, "epoch": None, "reason": None,
                           "transport": None}
            mv["state"], mv["text"] = self._state(mv, role, plan, fresh)
            out.append(mv)
        if len(out) == 2:
            self._pair(out[0], out[1], plan)
        for mv in out:
            mv["routable"] = False
            # linked and checked, whatever its node's drain says (a start and a rejoin read it)
            mv["linked"] = mv["state"] == "serving"
            if mv["linked"] and mv["_n"]["row"]["drain"]:
                mv["state"], mv["text"] = "draining", f"{mv['_n']['row']['alias']} is drained"
        if len(out) == 2 and out[0]["state"] == "serving" and out[1]["state"] == "serving":
            out[1]["routable"] = bool(out[1]["backend"])
        return out

    def _state(self, mv: dict, role: str, plan: dict, fresh: bool) -> tuple[str, str]:
        n = mv["_n"]
        if n is None:
            return "gone", "no longer in the fleet"
        if n["status"] == "off":
            return "off", n["text"]
        view = self.read(mv, fresh)
        if view is None:
            return "down", "its node's agent does not answer for its engine"
        mv["managed"] = bool(view.get("managed"))
        job = view.get("job") or {}
        if job.get("state") == "running" and job.get("action") in ("start", "restart"):
            return "starting", f"its engine {mv['engine']} {job['action']}s"
        b = self._block(view)
        if b is None:
            if view.get("health") is None:
                return "down", f"the {role} stage does not run"
            return "mismatch", (f"its engine serves a whole model, not the {role} stage: Start "
                                f"sets the stage")
        st = mv["stage"]
        link = b.get("link") or {}
        st.update(runs=True, link=link.get("state"), epoch=link.get("epoch"),
                  reason=link.get("reason"), transport=link.get("transport"),
                  identity=b.get("identity"))
        mv["_b"] = b
        want = (plan.get(role) or {}).get("layers")
        got = f"{b.get('lo')}:{b.get('hi')}"
        if b.get("role") != role or (want and got != want):
            return "mismatch", (f"its engine runs the {b.get('role')} stage {got}, the plan says "
                                f"the {role} {want}")
        if link.get("state") != "up":
            return "waiting", f"its stage runs; the link is down ({link.get('reason') or '?'})"
        return "serving", f"linked (epoch {link.get('epoch')})"

    @staticmethod
    def _pair(f: dict, t: dict, plan: dict) -> None:
        """The pair's checks on both stage blocks; a failure turns both into a mismatch."""
        fb, tb = f.get("_b"), t.get("_b")
        if fb is None or tb is None or f["state"] != "serving" or t["state"] != "serving":
            return
        fl, tl = fb.get("link") or {}, tb.get("link") or {}
        if fl.get("epoch") != tl.get("epoch"):
            # a link that just came back: the two reads are of two moments
            for m in (f, t):
                m["state"], m["text"] = "waiting", (f"the link is coming back (epochs "
                                                    f"{fl.get('epoch')} and {tl.get('epoch')})")
            return
        why = None
        if fb.get("hi") != tb.get("lo") or tb.get("hi") != tb.get("layers"):
            why = (f"the ranges do not meet: {fb.get('lo')}:{fb.get('hi')} and "
                   f"{tb.get('lo')}:{tb.get('hi')} of {tb.get('layers')} layers")
        elif (fl.get("peer") or {}).get("identity") != tb.get("identity") or \
                (tl.get("peer") or {}).get("identity") != fb.get("identity"):
            why = "the stages are linked to other stages than each other (identities differ)"
        else:
            fo, to = fb.get("options") or {}, tb.get("options") or {}
            for k in ("slots", "caches"):
                if fo.get(k) != to.get(k):
                    why = f"{k} differ: the front {fo.get(k)}, the tail {to.get(k)}"
                    break
        if why is not None:
            for m in (f, t):
                m["state"], m["text"] = "mismatch", why

    def status(self, rec: dict, ms: list[dict], job: dict | None) -> tuple[str, str, str]:
        """(status, words, phase) of a Split."""
        if self.plan_of(rec) is None:
            return "stopped", ("Split: recorded without a plan. Plan it again on Serve a model "
                               "to start it"), "stopped"
        if len(ms) < 2:
            return "down", "a Split has two members", "down"
        f, t = ms
        runs = [m for m in ms if _runs(m)]
        where = {m["role"]: m["alias"] for m in ms}
        if job is not None and job["state"] == "running":
            if job["action"] == "start":
                return "starting", f"start: {_last(job)}", job.get("phase") or "starting-front"
            if job["action"] == "stop":
                return "stopped", f"stopping: {_last(job)}", "stopping"
        if rec["state"] == "stopped":
            if not runs:
                return "stopped", "stopped", "stopped"
            names = " and ".join(f"the {m['role']} on {m['alias']}" for m in runs)
            return "stopped", f"stopped · {names} still run{'s' if len(runs) == 1 else ''}: " \
                              f"Stop ends {'it' if len(runs) == 1 else 'them'}", "partial"
        bad = next((m for m in ms if m["state"] in ("off", "gone")), None)
        if bad is not None:
            return "down", f"{bad['alias']}: {bad['text']}", "down"
        if any(m["state"] == "starting" for m in ms):
            s = next(m for m in ms if m["state"] == "starting")
            return "starting", f"{s['alias']}: {s['text']}", f"starting-{s['role']}"
        if f["state"] == "serving" and t["state"] == "serving":
            ep = t["stage"].get("epoch")
            return "serving", (f"serving · the front on {where['front']}, the tail on "
                               f"{where['tail']}, link up (epoch {ep})"), "linked"
        if len(runs) == 2:
            drained = next((m for m in ms if m["state"] == "draining"), None)
            if drained is not None:
                return "degraded", (f"{drained['alias']} is drained: a Split takes no request "
                                    f"while either of its nodes is drained"), "degraded"
            mis = next((m for m in ms if m["state"] == "mismatch"), None)
            if mis is not None:
                return "degraded", (f"the stages do not fit each other: {mis['text']}. Stop "
                                    f"and start the Split"), "degraded"
            why = t["stage"].get("reason") or f["stage"].get("reason") or "no reason given"
            return "degraded", (f"the link between {where['front']} and {where['tail']} is "
                                f"down ({why}): requests get 503 with Retry-After {RETRY_S} s. "
                                f"The stages link again by themselves; nothing to do"), "degraded"
        if len(runs) == 1:
            up = runs[0]
            other = f if up is t else t
            return "down", (f"only the {up['role']} runs (on {up['alias']}); the "
                            f"{other['role']} on {other['alias']} does not ({other['text']}). "
                            f"Start brings it back, Stop ends the {up['role']}"), "partial"
        return "down", "neither stage runs: Start starts the front, then the tail", "down"

    def view(self, rec: dict, ms: list[dict], phase: str) -> dict | None:
        """The record's plan with what is live: the phase and the link's state."""
        plan = self.plan_of(rec)
        if plan is None:
            return None
        out = {k: v for k, v in plan.items()}
        st = next((m["stage"] for m in reversed(ms) if m.get("stage", {}).get("runs")), {})
        out["phase"] = phase
        out["link"] = dict(plan["link"], state=st.get("link") or "unknown",
                           epoch=st.get("epoch"), reason=st.get("reason"),
                           transport=st.get("transport"))
        return out

    # ------------------------------------------------------------------ the gateway
    def gate(self, rec: dict, ms: list[dict]) -> list[dict]:
        """The members a request may go to: the tail of a pair that serves, alone. Else Refused:
        503 with Retry-After, in words that say what is down."""
        from proxy.deployments import Refused
        from proxy.relay import DRAIN_RETRY_S
        name = rec["name"]
        if self.plan_of(rec) is None or len(ms) < 2:
            raise Refused(503, "deployment_stopped", f"{name} is a Split without a plan: it "
                                                     f"serves no request")
        f, t = ms
        if t["routable"]:
            return [t]
        drained = next((m for m in ms if m["state"] == "draining"), None)
        if drained is not None:
            raise Refused(503, "node_draining", f"{drained['alias']} is drained for maintenance: "
                                                f"{name} takes no new requests",
                          headers=(("Retry-After", str(DRAIN_RETRY_S)),))
        _status, text, phase = self.status(rec, ms, None)
        code = "stage_down" if phase in ("degraded", "partial", "down") else "deployment_starting"
        raise Refused(503, code, f"{name}: {text}", headers=(("Retry-After", str(RETRY_S)),))

    def fronts(self, ctx: dict) -> dict:
        """{backend id: (node alias, why)} of every backend that reaches a Split's front stage:
        never listed, never routed to."""
        out = {}
        for rec in self.d.records():
            if rec["type"] != "split" or not rec["members"]:
                continue
            m = rec["members"][0]
            n = ctx["nodes"].get(m.get("node"))
            if n is None:
                continue
            for p in n["probes"]:
                if getattr(p, "via", None) == m.get("engine") and m.get("engine"):
                    out[p.id] = (n["row"]["alias"], f"runs the front stage of {rec['name']}: "
                                                    f"it serves no request")
        return out

    # ------------------------------------------------------------------ plans of the actions
    def plan_start(self, rec: dict, ms: list[dict], p: dict) -> str | None:
        """The start's impact, or why it may not: the error kind when blocked."""
        plan = self.plan_of(rec)
        if plan is None:
            p.update(allowed=False, blocked="this Split has no plan: plan it again on Serve a "
                                            "model (the planner picks the cut and the link)")
            return "no_plan"
        if len(ms) < 2:
            p.update(allowed=False, blocked="a Split has two members")
            return "conflict"
        for m in ms:
            src = m["_n"]["src"] if m["_n"] else None
            if not isinstance(src, nodesource.AgentNode):
                p.update(allowed=False, blocked=f"{m['alias']} has no TandemNode agent that "
                                                f"answers: a stage is started by its node's agent")
                return "no_agent"
            if m["state"] in ("off", "gone"):
                p.update(allowed=False, blocked=f"{m['alias']}: {m['text']}")
                return "conflict"
            if not m["managed"]:
                p.update(allowed=False, blocked=f"{m['alias']}: {m['engine'] or 'its engine'} is "
                                                f"not managed by TandemNode: a stage is started "
                                                f"by its node's agent")
                return "not_managed"
            other = self._served_elsewhere(rec, m)
            if other is not None:
                p.update(allowed=False, blocked=f"{m['alias']}: its engine {m['engine']} serves "
                                                f"{other}: stop that deployment first")
                return "conflict"
        f, t = ms
        if f["linked"] and t["linked"]:
            p["impact"].append("both stages run and are linked: the tail is warmed up only")
            return None
        for m, wait in ((f, FRONT_WAIT_S), (t, TAIL_WAIT_S)):
            side = plan[m["role"]]
            if _planned(m):
                p["impact"].append(f"{m['alias']}: the {m['role']} stage runs already")
                continue
            p["impact"].append(
                f"{m['alias']}: TandemNode starts the {m['role']} stage (layers "
                f"{side['layers']}, {side['need_gib']:.1f} GiB)"
                + (", after the front answers" if m["role"] == "tail" else "")
                + f"; up to {wait:.0f} s"
                + ("; the engine that runs there now stops first" if m["state"] == "mismatch"
                   else ""))
        p["impact"].append("a new link key goes to both nodes' agents; requests go to the tail "
                           f"on {t['alias']}")
        if plan["link"].get("warn"):
            p["impact"].append(plan["link"]["warn"])
        return None

    def plan_stop(self, rec: dict, ms: list[dict], p: dict) -> str | None:
        runs = [m for m in ms if _runs(m) or m["state"] == "starting"]
        if rec["state"] == "stopped" and not runs:
            p.update(allowed=False, blocked=f"{rec['name']} is stopped already")
        for m in reversed(ms):
            if m["managed"]:
                p["impact"].append(f"{m['alias']}: TandemNode stops the {m['role']} stage"
                                   + (" first" if m["role"] == "tail" else ""))
            elif _runs(m):
                p.update(allowed=False, blocked=f"{m['alias']}: {m['engine'] or 'its engine'} is "
                                                f"not managed by TandemNode: stop it where it "
                                                f"runs")
                return "not_managed"
        p["impact"].append("the link key is removed from both nodes")
        return None

    def _served_elsewhere(self, rec: dict, m: dict) -> str | None:
        """The name of another record that serves from this member's engine, None for none."""
        for r in self.d.records():
            if r["id"] == rec["id"] or r["state"] == "stopped":
                continue
            for x in r["members"]:
                if x.get("node") == m["node"] and x.get("engine") == m["engine"] and m["engine"]:
                    return r["name"]
        return None

    # ------------------------------------------------------------------ the jobs
    def _note(self, job: dict, m: dict, phase: str, text: str) -> None:
        if "id" in job:
            self.d._step(job, m["node"], m["alias"], phase, text)
        else:
            job["say"](f"{m['alias']}: {text}")

    def _phase(self, job: dict, phase: str) -> None:
        job["phase"] = phase
        if "id" in job:
            self.d.cfg.put_job(job)

    def start(self, job: dict, rec: dict) -> None:
        d = self.d
        plan = self.plan_of(rec)
        if plan is None:
            raise _err(409, "no_plan", "this Split has no plan")
        rec = dict(rec, state="serving")
        d.cfg.put_deployment(rec, who=job["by"])
        try:
            self._bring_up(job, rec, plan)
        except Exception as exc:                                     # noqa: BLE001
            from proxy.deployments import DeployError
            left = self._clean(job, rec)
            msg = exc.message if isinstance(exc, DeployError) else f"{type(exc).__name__}: {exc}"
            if left:
                raise _err(409, "partial_split", f"{msg} · and {' and '.join(left)} could not "
                                                 f"be stopped: Stop ends it") from None
            raise _err(getattr(exc, "status", 502), getattr(exc, "kind", "start_failed"),
                       f"{msg} · both stages are stopped and the link key is removed") from None
        d._event(rec, f"{rec['name']} started on {d._aliases(rec)}",
                 f"Split · cut {plan['cut']} · linked")

    def _bring_up(self, job: dict, rec: dict, plan: dict, fresh_key: bool = True) -> None:
        d = self.d
        self.forget()
        d._ctx = None
        ms = self.members(rec, d._context(), fresh=True)
        if len(ms) < 2:
            raise _err(409, "conflict", "a Split has two members")
        f, t = ms
        for m in ms:
            src = m["_n"]["src"] if m["_n"] else None
            if not isinstance(src, nodesource.AgentNode) or not m["managed"]:
                raise _err(409, "not_managed", f"{m['alias']}: a stage is started by its node's "
                                               f"agent, on an engine it manages")
        up = {m["role"]: _planned(m) for m in ms}
        if not any(up.values()):
            if not fresh_key:
                # a fleet update restarted both: the keys are still on the nodes
                for m in ms:
                    if not ((self.read(m, True) or {}).get("stage") or {}).get("key"):
                        raise _err(409, "partial_split", f"{m['alias']} holds no link key: stop "
                                                         f"and start the Split")
            else:
                self._hand_key(job, plan, ms)
        else:
            for m in ms:
                cfg = (self.read(m, True) or {}).get("stage") or {}
                if cfg.get("key") != "set" or cfg.get("stage") != plan[m["role"]]["layers"]:
                    raise _err(409, "partial_split", f"the {next(r for r in up if up[r])} stage "
                                                     f"runs but {m['alias']} holds no link key "
                                                     f"for the plan: stop the Split, then start it")
        if not up["front"]:
            self._phase(job, "starting-front")
            self._engine_up(job, f, "front", plan, FRONT_WAIT_S)
        if not up["tail"]:
            self._phase(job, "starting-tail")
            self._engine_up(job, t, "tail", plan, TAIL_WAIT_S)
        self._linked(job, rec, plan)
        rec = self._tail_model(rec, t, job.get("by") or "fleet update")
        t = self.members(rec, d._context(), fresh=True)[1]
        self._note(job, t, "warm", "linked; warming up with one token")
        from proxy.deployments import WARM_WAIT_S
        d._warm_until(t["backend"], t["model"], WARM_WAIT_S)
        self._built(rec, True, job.get("by") or "fleet update")
        self._phase(job, "linked")
        self._note(job, f, "serving", f"the front stage, layers {plan['front']['layers']}: "
                                      f"linked")
        self._note(job, t, "serving", f"the tail stage, layers {plan['tail']['layers']}: "
                                      f"linked and warm; it takes the requests")

    def _hand_key(self, job: dict, plan: dict, ms: list[dict]) -> None:
        """A new link key to both node agents; here it lives in this one variable."""
        key = base64.b64encode(secrets.token_bytes(KEY_BYTES)).decode()
        try:
            for m in ms:
                body = dict(splitplan.stage_env(plan, m["role"]), key=key)
                st, doc = self._put(m, body)
                if st != 200:
                    raise _err(502, "stage_failed",
                               f"{m['alias']}: its agent did not take the stage: "
                               f"{((doc or {}).get('error') or {}).get('message') or f'HTTP {st}'}")
                self._note(job, m, "start", f"the {m['role']} stage set (layers "
                                            f"{plan[m['role']]['layers']}); the link key is on "
                                            f"its node")
        finally:
            del key

    @staticmethod
    def _put(m: dict, body: dict) -> tuple[int, dict | None]:
        src = m["_n"]["src"]
        try:
            st, doc = src.client.json("PUT", f"/node/engines/{m['engine']}/stage", body,
                                      timeout=15.0)
        except Exception as exc:                                     # noqa: BLE001
            return 502, {"error": {"message": f"its agent does not answer "
                                              f"({type(exc).__name__})"}}
        return st, doc if isinstance(doc, dict) else None

    def _engine_up(self, job: dict, m: dict, role: str, plan: dict, wait: float) -> None:
        d = self.d
        src = m["_n"]["src"]
        view = self.read(m, True) or {}
        if view.get("health") is not None:
            # what answers there is not the planned stage: a whole model, or another stage
            self._note(job, m, "stop", f"its engine stops to start again as the {role} stage")
            self._engine_down(job, m)
        self._note(job, m, "start", f"TandemNode starts the {role} stage")
        st, doc = src.post(f"/node/engines/{m['engine']}/start", None)
        if st not in (202, 409):
            raise _err(502, "start_failed", f"{m['alias']}: "
                       f"{(doc.get('error') or {}).get('message') or f'its agent answered HTTP {st}'}")
        end = d.clock() + wait
        while True:
            view = self.read(m, True) or {}
            j = view.get("job") or {}
            if view.get("state") == "up" and j.get("state") != "running":
                break
            if j.get("state") == "failed" and j.get("action") == "start":
                raise _err(502, "start_failed", f"the {role} stage on {m['alias']} did not "
                                                f"start: {j.get('message') or 'its start failed'}")
            if d.clock() >= end:
                raise _err(504, "start_timeout", f"the {role} stage on {m['alias']} is not up "
                                                 f"after {wait:.0f} s")
            d.sleep(_poll())
        b = self._block(view)
        want = plan[role]["layers"]
        if b is None or b.get("role") != role or f"{b.get('lo')}:{b.get('hi')}" != want:
            got = "a whole model" if b is None else f"the {b.get('role')} {b.get('lo')}:{b.get('hi')}"
            raise _err(502, "stage_mismatch", f"{m['alias']} started {got}, not the {role} "
                                              f"stage {want}")
        src.forget_engines()

    def _linked(self, job: dict, rec: dict, plan: dict) -> None:
        """Both stages up: wait for the link, then the pair's checks."""
        d = self.d
        end = d.clock() + LINK_WAIT_S
        while True:
            d._ctx = None
            ms = self.members(rec, d._context(), fresh=True)
            f, t = ms
            if f["linked"] and t["linked"]:
                return
            bad = next((m for m in ms if m["state"] == "mismatch"), None)
            if bad is not None:
                raise _err(502, "identity_mismatch", f"the stages do not fit each other: "
                                                     f"{bad['text']}")
            gone = next((m for m in ms if not _runs(m)), None)
            if gone is not None:
                raise _err(502, "start_failed", f"the {gone['role']} stage on {gone['alias']} "
                                                f"stopped: {gone['text']}")
            if d.clock() >= end:
                raise _err(504, "link_timeout", f"the stages are up but not linked after "
                                                f"{LINK_WAIT_S:.0f} s: {t['text']}")
            d.sleep(_poll())

    def _tail_model(self, rec: dict, t: dict, who: str) -> dict:
        """The id the tail's backend serves the model under, read now that it answers."""
        d = self.d
        try:
            d.svc.health.kick(t["backend"])          # its model list read again now
        except Exception:                                            # noqa: BLE001
            pass
        d._check(t["backend"])
        served = d._served_id(t["backend"], rec["model"])
        if rec["members"][1].get("model") != served:
            members = [dict(rec["members"][0]), dict(rec["members"][1], model=served)]
            rec = dict(rec, members=members)
            d.cfg.put_deployment(rec, who=who)
        d._ctx = None
        return rec

    def _built(self, rec: dict, built: bool, who: str) -> None:
        cur = self.d.cfg.deployment(rec["id"])
        if cur is None:
            return
        o = dict(cur.get("options") or {})
        if isinstance(o.get("split"), dict) and o["split"].get("built") != built:
            o["split"] = dict(o["split"], built=built)
            self.d.cfg.put_deployment(dict(cur, options=o), who=who)

    def _clean(self, job: dict, rec: dict) -> list[str]:
        """What a failed start leaves is taken down: the tail, the front, both keys; the record
        stopped. The stages that still run after it, in words (none: [])."""
        d = self.d
        cur = d.cfg.deployment(rec["id"]) or rec
        d.cfg.put_deployment(dict(cur, state="stopped"), who=job.get("by") or "fleet update")
        self._built(rec, False, job.get("by") or "fleet update")
        d._ctx = None
        ms = self.members(rec, d._context(), fresh=True)
        for m in reversed(ms):
            try:
                if (_runs(m) or m["state"] == "starting") and m["managed"]:
                    self._engine_down(job, m)
            except Exception:                                        # noqa: BLE001
                pass
            self._clear(m)
        d._ctx = None
        left = [f"the {m['role']} on {m['alias']}"
                for m in self.members(rec, d._context(), fresh=True) if _runs(m)]
        return left

    def _engine_down(self, job: dict, m: dict) -> None:
        """One stage's engine stopped through its agent, and seen down."""
        d = self.d
        src = m["_n"]["src"]
        self._note(job, m, "stop", f"TandemNode stops the {m['role']} stage")
        st, doc = src.post(f"/node/engines/{m['engine']}/stop", None)
        if st not in (202, 409):
            raise _err(502, "stop_failed", f"{m['alias']}: "
                       f"{(doc.get('error') or {}).get('message') or f'its agent answered HTTP {st}'}")
        end = d.clock() + STOP_WAIT_S
        while True:
            view = self.read(m, True) or {}
            j = view.get("job") or {}
            if view.get("state") == "down" and j.get("state") != "running":
                break
            if j.get("state") == "failed" and j.get("action") == "stop":
                raise _err(502, "stop_failed", f"the {m['role']} stage on {m['alias']} did not "
                                               f"stop: {j.get('message') or 'its stop failed'}")
            if d.clock() >= end:
                raise _err(504, "stop_timeout", f"the {m['role']} stage on {m['alias']} still "
                                                f"answers after {STOP_WAIT_S:.0f} s")
            d.sleep(_poll())
        if m.get("backend"):
            d._warm.pop((m["backend"], m["model"]), None)
            d._check(m["backend"])
        src.forget_engines()
        self._note(job, m, "stopped", "stopped")

    @staticmethod
    def _clear(m: dict) -> None:
        n = m.get("_n")
        src = n["src"] if n else None
        if isinstance(src, nodesource.AgentNode) and m.get("engine"):
            try:
                src.client.json("DELETE", f"/node/engines/{m['engine']}/stage", timeout=15.0)
            except Exception:                                        # noqa: BLE001
                pass

    def stop(self, job: dict, rec: dict) -> None:
        d = self.d
        rec = dict(rec, state="stopped")
        d.cfg.put_deployment(rec, who=job["by"])
        self._phase(job, "stopping")
        self.forget()
        ms = self.members(rec, d._context(), fresh=True)
        d._say(job, "no new requests; those in flight finish")
        d._drain(job, rec, [m for m in ms if m["role"] == "tail"])
        for m in reversed(ms):                      # the tail first: it drains its disk writer
            if m["_n"] is not None and m["managed"] and (_runs(m) or m["state"] == "starting"):
                self._engine_down(job, m)
        for m in ms:
            self._clear(m)
        self._built(rec, False, job["by"])
        self.forget()
        d._event(rec, f"{rec['name']} stopped", f"on {d._aliases(rec)} · the tail first, the "
                                                f"link key removed")

    def rejoin(self, did: str, say) -> None:
        """A fleet update's step: after both nodes updated, the stages up again in order (the
        keys still on the nodes), linked and warm."""
        rec = self.d.cfg.deployment(did)
        plan = self.plan_of(rec) if rec is not None else None
        if rec is None or rec["type"] != "split" or rec["state"] == "stopped" or plan is None:
            return
        self._bring_up({"say": say, "by": "fleet update"}, rec, plan, fresh_key=False)


def _poll() -> float:
    from proxy.deployments import POLL_S
    return POLL_S


def _last(j: dict) -> str:
    return j["log"][-1]["text"] if j.get("log") else j.get("action") or ""
