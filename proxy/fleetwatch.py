"""The fleet's alert rules and its event log's sources (SRV-729).

    watch = Watch(store, clock=, log=)
    watch.observe(now, readings)        each sample: the rules, and what changed (proxy/fleet.py)
    watch.rules() / watch.edit(id, body, who)
    watch.alerts() / watch.ack(id, who)

RULES. Seeded for a DGX Spark (GB10: unified memory, no fan reading, no power cap exposed), each
with a debounce (`for_s`: the condition holds that long before it fires) and hysteresis (it
resolves once the value is `clear` back past the threshold for `clear_s`):

    gpu-temp     GPU temperature >= 85 °C for 60 s           err   clears below 80 °C for 60 s
    throttle     a thermal or power-brake clock event, 30 s   warn  clears after 60 s without
    mem-avail    MemAvailable < 10 GiB for 30 s               warn  clears above 12 GiB for 30 s
    disk-free    a mount under 10 % free for 5 min            warn  clears above 12 % for 5 min
    node-down    no answer for 30 s                           err   clears after 10 s up
    engine-down  its health check fails for 60 s              err   clears after one up check
    host-sleep   the controller's machine may sleep (a Mac)   warn
    lease        a rented node's lease ends in < 24 h         warn  clears when it is extended
                 (VIS-246; a reminder, once per lease: the node's `lease_end` from Add Spark)

`PATCH /v1/fleet/rules/{id}` sets `enabled`, `notify`, `threshold`, `for_s` and `scope` (all, or
node:<id>); an edit is kept in the store and lies over the seeded rule. A rule turned off resolves
its alerts. A value the node does not give (an off node's temperature) holds an alert as it is.

AN ALERT has an id per rule and subject (`gpu-temp:<node>`, `engine-down:<backend>`), a title
with the value when it fired, an action that opens the page to look at, and `acked_at` once
acknowledged; it stays listed until it resolves. Firing, resolving and acknowledging are events.

NOTIFY. `app` is the alerts list itself. `system` is the page's to show (the browser's
notification, with its permission); the controller makes no outbound call. Another target is a
callable in `notifiers` (name -> fn(event, alert)), none by default.

EVENTS besides the alerts': a node up, off or draining (held 10 s), an engine backend down, up,
started, restarted (back within 2 minutes, or its token counter reset while up) or stopped by a
job on its node, a deployment's status, and what proxy/fleet.py and proxy/fleettrust.py add
(controller start and stop, pairing, release, take-over, an address change).
"""

from __future__ import annotations

import functools
import math
import threading
import time

NOTIFY = ("app", "system")
NODE_HOLD_S = 10.0
RESTART_S = 120.0
FOR_MAX_S = 86400
# NVML clock events that mean heat or power, not an idle GPU or an application clock
THROTTLE_REASONS = ("hw_slowdown", "sw_thermal", "hw_thermal", "hw_power_brake")

# id: (name, metric, op, threshold, unit, for_s, clear, clear_s, severity, notify, scope)
DEFAULTS = (
    ("gpu-temp", "GPU temperature", "temp", ">=", 85.0, "°C", 60, 5.0, 60, "err",
     ["app", "system"], "all"),
    ("throttle", "GPU clock throttled", "throttle", None, None, None, 30, None, 60, "warn",
     ["app"], "all"),
    ("mem-avail", "Memory headroom", "mem_avail", "<", 10.0, "GiB", 30, 2.0, 30, "warn",
     ["app", "system"], "all"),
    ("disk-free", "Disk almost full", "disk_free", "<", 10.0, "%", 300, 2.0, 300, "warn",
     ["app"], "all"),
    ("node-down", "Node reachable", "reachable", None, None, None, 30, None, 10, "err",
     ["app", "system"], "all"),
    ("engine-down", "Engine answers", "engine", None, None, None, 60, None, 0, "err",
     ["app", "system"], "all"),
    ("host-sleep", "Controller host may sleep", "host_sleep", None, None, None, 0, None, 0, "warn",
     ["app"], "host"),
    ("lease", "Lease ends", "lease_left", "<", 24.0, "h", 0, 0.0, 0, "warn", ["app", "system"],
     "all"),
)


class RuleError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _fmt(v: float) -> str:
    return f"{v:g}"


def _left(h: float) -> str:
    return f"{h:.0f} h" if h >= 1 else f"{max(1, round(h * 60))} min"


def _dur(s: float) -> str:
    return f"{s / 60:g} min" if s >= 120 and s % 60 == 0 else f"{s:g} s"


class Rule:
    def __init__(self, rid, name, metric, op, threshold, unit, for_s, clear, clear_s, severity,
                 notify, scope):
        self.id, self.name, self.metric = rid, name, metric
        self.op, self.threshold, self.unit = op, threshold, unit
        self.for_s, self.clear, self.clear_s = for_s, clear, clear_s
        self.severity, self.notify, self.scope = severity, list(notify), scope
        self.enabled = True

    def condition(self) -> str:
        t = None if self.threshold is None else f"{_fmt(self.threshold)} {self.unit}"
        hold = f" for {_dur(self.for_s)}" if self.for_s else ""
        return {"temp": f"≥ {t}{hold}",
                "throttle": f"thermal or power-brake clock event{hold}",
                "mem_avail": f"MemAvailable < {t}{hold}",
                "disk_free": f"free < {t} on a mount{hold}",
                "reachable": f"no answer{hold}",
                "engine": f"health check fails{hold}",
                "host_sleep": "sleep allowed",
                "lease_left": f"lease ends in < {t}"}[self.metric]

    def view(self) -> dict:
        return {"id": self.id, "name": self.name, "metric": self.metric,
                "condition": self.condition(), "scope": self.scope, "notify": list(self.notify),
                "enabled": self.enabled, "threshold": self.threshold, "unit": self.unit,
                "for_s": self.for_s, "severity": self.severity}

    def applies(self, node: str) -> bool:
        return self.scope in ("all", "host") or self.scope == f"node:{node}"

    def holds(self, v, firing: bool) -> bool:
        """Whether the condition holds: past the threshold to fire, past `clear` the other way
        round to resolve (hysteresis)."""
        if self.op is None:
            return bool(v)
        if self.op == ">=":
            return v >= (self.threshold - self.clear if firing else self.threshold)
        return v < (self.threshold + self.clear if firing else self.threshold)


def _host_said(v, resolved=False):
    if resolved:
        return "the controller's machine may not sleep any more"
    return ("The controller runs on a machine that can sleep",
            "If it sleeps, the API and every metric stop. Keep it awake or move the controller "
            "to a Spark.", {"label": "App settings", "route": "#/app"})


def default_rules() -> dict:
    return {d[0]: Rule(*d) for d in DEFAULTS}


def _locked(fn):
    @functools.wraps(fn)
    def inner(self, *a, **kw):
        with self._lock:
            return fn(self, *a, **kw)
    return inner


class Watch:
    """The sampler's thread observes; the routes read, acknowledge and edit (one lock)."""

    def __init__(self, store, *, clock, log, notifiers: dict | None = None):
        self.store, self.clock, self.log = store, clock, log
        self._lock = threading.RLock()
        self.notifiers = dict(notifiers or {})
        self._rules = default_rules()
        for rid, doc in store.rule_edits().items():
            if rid in self._rules:
                self._apply(self._rules[rid], doc)
        self.firing = {a["id"]: a for a in store.open_alerts()}
        self.state: dict[str, dict] = {}         # alert id -> {"on": t, "off": t}
        self.nodes: dict[str, dict] = {}         # node id -> {"status", "since", "said"}
        self.engines: dict[str, dict] = {}       # backend id -> {"state", "since", "restarts"}
        self.deps: dict[str, str] | None = None  # deployment id -> status

    # ------------------------------------------------------------------ rules
    @_locked
    def rules(self) -> list[dict]:
        return [r.view() for r in self._rules.values()]

    @staticmethod
    def _apply(r: Rule, doc: dict) -> None:
        for k in ("enabled", "notify", "threshold", "for_s", "scope"):
            if k in doc:
                setattr(r, k, list(doc[k]) if k == "notify" else doc[k])

    @_locked
    def edit(self, rid: str, body, who: str, nodes: list[str]) -> dict:
        r = self._rules.get(rid)
        if r is None:
            raise RuleError(404, f"no rule {rid}")
        if not isinstance(body, dict) or not body:
            raise RuleError(400, "the body is an object with enabled, notify, threshold, for_s "
                                 "or scope")
        extra = [k for k in body if k not in ("enabled", "notify", "threshold", "for_s", "scope")]
        if extra:
            raise RuleError(400, f"rule: unknown field {extra[0]!r}")
        if "enabled" in body and not isinstance(body["enabled"], bool):
            raise RuleError(400, "enabled is true or false")
        if "notify" in body and (not isinstance(body["notify"], list) or
                                 any(x not in NOTIFY for x in body["notify"])):
            raise RuleError(400, "notify is a list of app and system")
        if "threshold" in body:
            if r.threshold is None:
                raise RuleError(400, f"{rid} has no threshold")
            if not _num(body["threshold"]):
                raise RuleError(400, "threshold is a number")
        if "for_s" in body and (not _num(body["for_s"]) or not 0 <= body["for_s"] <= FOR_MAX_S):
            raise RuleError(400, f"for_s is a number of seconds from 0 to {FOR_MAX_S}")
        if "scope" in body:
            s = body["scope"]
            if r.scope == "host":
                raise RuleError(400, f"{rid} is about the controller's machine; its scope stays")
            if s != "all" and not (isinstance(s, str) and s.startswith("node:")
                                   and s[5:] in nodes):
                raise RuleError(400, "scope is all or node:<id> of a node in the fleet")
        doc = dict(self.store.rule_edits().get(rid) or {})
        doc.update({k: (list(dict.fromkeys(v)) if k == "notify" else v)
                    for k, v in body.items()})
        self.store.set_rule_edit(rid, doc, self.clock(), who)
        self._apply(r, doc)
        self.log(f"[fleet] rule {rid} changed by {who}: {r.condition()}, "
                 f"{'on' if r.enabled else 'off'}, scope {r.scope}")
        return r.view()

    # ------------------------------------------------------------------ alerts
    @staticmethod
    def public(a: dict) -> dict:
        return {"id": a["id"], "severity": a["severity"], "node": a["node"], "title": a["title"],
                "since": a["since"], "rule": a["rule_text"], "detail": a["detail"],
                "action": a["action"], "acked": a["acked_at"] is not None,
                "acked_at": a["acked_at"]}

    @_locked
    def alerts(self) -> list[dict]:
        return [self.public(a) for a in sorted(self.firing.values(), key=lambda a: -a["since"])]

    @_locked
    def ack(self, aid: str, who: str) -> dict | None:
        a = self.firing.get(aid)
        if a is None:
            return None
        if a["acked_at"] is None:
            now = round(self.clock(), 3)
            self.store.ack_alert(a["seq"], now, who)
            a["acked_at"], a["acked_by"] = now, who
            self.store.add_event("alert", a["node"], f"Acknowledged: {a['title']}",
                                 f"by {who}", at=now)
        return self.public(a)

    def _fire(self, now: float, r: Rule, aid: str, node: str, title: str, detail: str,
              action: dict | None) -> None:
        a = self.store.add_alert({"id": aid, "rule": r.id, "node": node, "severity": r.severity,
                                  "title": title, "rule_text": f"{r.name} {r.condition()}",
                                  "detail": detail, "action": action, "since": round(now, 3)})
        self.firing[aid] = a
        ev = self.store.add_event("alert", node, title, detail, severity=r.severity, at=now)
        self.log(f"[fleet] alert {aid}: {title}")
        for name in r.notify:
            fn = self.notifiers.get(name)
            if fn is not None:
                try:
                    fn(ev, self.public(a))
                except Exception as exc:                             # noqa: BLE001
                    self.log(f"[fleet] notify {name} failed: {type(exc).__name__}: {exc}")

    def _resolve(self, now: float, aid: str, why: str) -> None:
        a = self.firing.pop(aid, None)
        self.state.pop(aid, None)
        if a is None:
            return
        self.store.resolve_alert(a["seq"], round(now, 3))
        self.store.add_event("alert", a["node"], f"Resolved: {a['title']}", why,
                             severity="resolved", at=now)
        self.log(f"[fleet] alert {aid} resolved: {why}")

    def _check(self, now: float, r: Rule, aid: str, node: str, v, said) -> None:
        """One subject of one rule: `v` the value (None: unknown, the state holds); `said()` gives
        (title, detail, action) when it fires, and the words of its resolution."""
        if v is None:
            return
        firing = aid in self.firing
        st = self.state.setdefault(aid, {"on": None, "off": None})
        if not firing:
            if not r.holds(v, False):
                st["on"] = None
                return
            st["on"] = now if st["on"] is None else st["on"]
            if now - st["on"] >= r.for_s:
                title, detail, action = said(v)
                self._fire(now, r, aid, node, title, detail, action)
                st.update(on=None, off=None)
            return
        if r.holds(v, True):
            st["off"] = None
            return
        st["off"] = now if st["off"] is None else st["off"]
        if now - st["off"] >= r.clear_s:
            self._resolve(now, aid, said(v, resolved=True))

    # ------------------------------------------------------------------ each sample
    @_locked
    def observe(self, now: float, readings: dict) -> None:
        """`readings`: {"nodes": [{id, alias, status, status_text, temp, throttle, mem_avail,
        disk, engines: [{id, label, state, error, restarts, job}]}], "host": {"may_sleep"},
        "deployments": [{id, name, status, status_text, members}] or None (not read this time)}.
        """
        nodes = readings["nodes"]
        self._node_events(now, nodes)
        self._engine_events(now, nodes)
        if readings.get("deployments") is not None:
            self._deployment_events(now, readings["deployments"])
        self._evaluate(now, nodes, readings.get("host") or {})

    def _evaluate(self, now: float, nodes: list[dict], host: dict) -> None:
        seen = set()
        for r in self._rules.values():
            if not r.enabled:
                continue
            if r.metric == "host_sleep":
                aid = "host-sleep:host"
                seen.add(aid)
                self._check(now, r, aid, "host", bool(host.get("may_sleep")), _host_said)
                continue
            for n in nodes:
                if not r.applies(n["id"]):
                    continue
                for aid, v, said in self._subjects(r, n):
                    seen.add(aid)
                    self._check(now, r, aid, n["id"], v, said)
        for aid in [a for a in self.firing if a not in seen]:
            rule = self._rules.get(self.firing[aid]["rule"])
            gone = any(n["id"] == self.firing[aid]["node"] for n in nodes)
            self._resolve(now, aid, "its rule is off" if rule is None or not rule.enabled
                          else "out of the rule's scope" if gone else "its subject is gone")

    def _subjects(self, r: Rule, n: dict):
        """(alert id, value, said) per subject of rule `r` on node `n`."""
        a, nid = n["alias"], n["id"]
        hw = {"label": f"Open {a} in Hardware", "route": f"#/hardware?scope=node:{nid}"}
        spark = {"label": f"Open {a}", "route": f"#/sparks/{nid}"}
        if r.metric == "temp":
            yield f"{r.id}:{nid}", n.get("temp"), lambda v, resolved=False: (
                f"GPU at {v:.0f} °C" if resolved else
                (f"{a} GPU {v:.0f} °C", f"At {_fmt(r.threshold)} °C or more for "
                 f"{_dur(r.for_s)}. A GB10 slows its clocks when it stays this hot.", hw))
        elif r.metric == "throttle":
            got = n.get("throttle")
            hot = None if got is None else [x for x in got if x in THROTTLE_REASONS]
            yield f"{r.id}:{nid}", hot, lambda v, resolved=False: (
                "no thermal or power-brake clock event" if resolved else
                (f"{a} GPU throttled: {', '.join(v).replace('_', ' ')}",
                 "The driver reports a clock event for heat or power; decode runs slower while "
                 "it lasts.", hw))
        elif r.metric == "mem_avail":
            yield f"{r.id}:{nid}", n.get("mem_avail"), lambda v, resolved=False: (
                f"{v:.1f} GiB available" if resolved else
                (f"{a} memory low: {v:.1f} GiB available",
                 "Unified memory: the GPU and the system share it. Below this the engine's "
                 "memory gate refuses work and the kernel may kill a process.", hw))
        elif r.metric == "disk_free":
            d = n.get("disk")
            yield f"{r.id}:{nid}", None if d is None else d[0], lambda v, resolved=False: (
                f"{v:.0f} % free" if resolved else
                (f"{a} disk {d[1]} {v:.0f} % free", "Weights and logs need room; free some or "
                 "remove weights the node no longer serves.", spark))
        elif r.metric == "reachable":
            yield f"{r.id}:{nid}", n["status"] == "off", lambda v, resolved=False: (
                f"{a} answers again" if resolved else
                (f"{a} not reachable", n.get("status_text") or "its agent does not answer",
                 spark))
        elif r.metric == "lease_left":
            end = n.get("lease_end")
            yield f"{r.id}:{nid}", n.get("lease_left"), lambda v, resolved=False: (
                f"{a}'s lease runs {v:.0f} h more" if resolved else
                (f"{a}'s lease ended" if v <= 0 else f"{a}'s lease ends in {_left(v)}",
                 f"On {time.strftime('%Y-%m-%d %H:%M', time.localtime(end))}. Extend it with the "
                 f"provider, or release {a} before: Release wipes its weights, caches, keys and "
                 f"TandemNode." if end else "Extend it with the provider, or release it.", spark))
        elif r.metric == "engine":
            for e in n.get("engines") or []:
                st = e["state"]
                v = None if st not in ("up", "down") else st == "down"
                yield f"{r.id}:{e['id']}", v, lambda v, resolved=False, e=e: (
                    f"{e['label']} answers again" if resolved else
                    (f"{e['label']} on {a} is down", e.get("error") or "no answer",
                     {"label": "Open Models", "route": "#/models"}))

    # ------------------------------------------------------------------ what changed
    def _node_events(self, now: float, nodes: list[dict]) -> None:
        for n in nodes:
            st = self.nodes.get(n["id"])
            if st is None:
                # the first look: an up node needs no event; an off one is said once it held
                self.nodes[n["id"]] = {"status": n["status"], "since": now,
                                       "said": "up" if n["status"] == "up" else None}
                continue
            if n["status"] != st["status"]:
                st.update(status=n["status"], since=now)
            if st["status"] != st["said"] and now - st["since"] >= NODE_HOLD_S:
                prev, st["said"] = st["said"], st["status"]
                s = st["status"]
                if s == "up":
                    text = f"{n['alias']} is up" + (" again" if prev else "")
                    sev = None
                elif s == "draining":
                    text, sev = f"{n['alias']} is draining", None
                else:
                    text, sev = f"{n['alias']} is {s}", "err" if s == "off" else "warn"
                self.store.add_event("node", n["id"], text, n.get("status_text") or "",
                                     severity=sev, at=st["since"])
        for nid in [k for k in self.nodes if k not in {n["id"] for n in nodes}]:
            del self.nodes[nid]

    def _engine_events(self, now: float, nodes: list[dict]) -> None:
        here = set()
        for n in nodes:
            for e in n.get("engines") or []:
                here.add(e["id"])
                st = self.engines.get(e["id"])
                if st is None:
                    self.engines[e["id"]] = {"state": e["state"], "since": now,
                                             "restarts": e.get("restarts") or 0}
                    if e["state"] == "down":
                        self._engine_event(now, n, e, "down", 0.0)
                    continue
                if e["state"] != st["state"] and e["state"] in ("up", "down"):
                    # the first health check after the start says what was so: no event for up
                    if st["state"] in ("up", "down") or e["state"] == "down":
                        self._engine_event(now, n, e, e["state"], now - st["since"],
                                           was=st["state"])
                    st.update(state=e["state"], since=now)
                elif (e.get("restarts") or 0) > st["restarts"] and e["state"] == "up":
                    self._engine_event(now, n, e, "restarted", 0.0)
                st["restarts"] = e.get("restarts") or 0
        for bid in [k for k in self.engines if k not in here]:
            del self.engines[bid]

    def _engine_event(self, now, n, e, what, held, was=None) -> None:
        job = e.get("job") or {}
        asked = job.get("action") if job.get("action") in ("start", "stop", "restart") and \
            now - (job.get("started_at") or -math.inf) <= RESTART_S else None
        by = f" by {job.get('who')}" if asked and job.get("who") else ""
        label, a = e["label"], n["alias"]
        if what == "down":
            if asked in ("stop", "restart"):
                text, sev, detail = f"{label} on {a} stopped", None, f"{asked} asked{by}"
            else:
                text, sev = f"{label} on {a} went down", "err"
                detail = (e.get("error") or "no answer") + "; no stop was asked through TandemApp"
        elif what == "restarted":
            text, sev, detail = (f"{label} on {a} restarted", None,
                                 "its token counter started again from 0")
        elif was == "down" and held <= RESTART_S:
            text, sev, detail = (f"{label} on {a} restarted", None,
                                 f"down for {held:.0f} s" + (f"; {asked} asked{by}" if asked
                                                             else ""))
        else:
            text, sev, detail = (f"{label} on {a} started", None,
                                 f"{asked} asked{by}" if asked else "it answers its health check")
        self.store.add_event("engine", n["id"], text, detail, severity=sev, at=now)

    def _deployment_events(self, now: float, deps: list[dict]) -> None:
        cur = {d["id"]: d for d in deps}
        if self.deps is None:
            self.deps = {k: d["status"] for k, d in cur.items()}
            return
        for k, d in cur.items():
            prev = self.deps.get(k)
            if prev == d["status"]:
                continue
            node = d["members"][0] if len(d["members"]) == 1 else "all"
            on = d.get("on") or ""
            if prev is None:
                text = f"{d['name']} appeared{on}: {d['status']}"
            else:
                text = f"{d['name']} is {d['status']}{on}"
            sev = "err" if d["status"] == "down" else None
            self.store.add_event("deploy", node, text, d.get("status_text") or "",
                                 severity=sev, deployment=k, at=now)
        for k in [k for k in self.deps if k not in cur]:
            self.store.add_event("deploy", "all", f"{k} is no longer served", "", deployment=k,
                                 at=now)
        self.deps = {k: d["status"] for k, d in cur.items()}
