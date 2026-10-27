"""TandemApp's controller: the fleet registry and its API, `/v1/fleet/*` (SRV-723).

    fleet = Fleet(svc, LocalNode(...))           one per tandem-app process (proxy/app.py)
    fleet.start() / fleet.stop()                 the sampler: every node's figures every 2 s
    fleet.handle(method, path, query, body, who) -> (status, JSON body)

The contract is docs/contract/fleet-v1 (its schemas, and the rules they cannot express); the
dashboard's mock (dashboard/mock/fleet.ts) answers the same routes the same way. The browser talks
to tandem-app only: every figure of a node is read here, server-side, and the routes keep the
dashboard's guard (proxy/app.py: reads the dashboard rule, writes `conductor_write`).

    GET   /v1/fleet/nodes, /v1/fleet/nodes/{id}     the nodes, with status and inventory
    PATCH /v1/fleet/nodes/{id}                      {alias?, colour?, icon?, tags?}
    GET   /v1/fleet/links, /v1/fleet/topology       the LAN and the internet as this host sees them
    GET   /v1/fleet/deployments                     each model an engine on a node serves: Single
    GET   /v1/fleet/metrics?source=&metric=&range=  per-node series from the sampler's rings
    GET   /v1/fleet/memory                          the unified memory split per node, GiB
    GET   /v1/fleet/logs?node=&level=&q=&limit=     the nodes' log lines, oldest first
    GET   /v1/fleet/events, /alerts, /rules         empty lists until SRV-729
    POST  /v1/fleet/alerts/{id}/ack                 404: no alert fires before SRV-729
    GET   /v1/fleet/layouts/{page}?device=          a page's saved window layout, or its default
    PUT   /v1/fleet/layouts/{page}?device=          saved with `saved_at` now: the last write wins

A FLEET OF ONE. The machine tandem-app runs on is a node when it is Spark-class (an NVIDIA GPU or an
engine of its own, proxy/nodesource.py): at the first start it is registered with its hostname as
the alias, an id from the machine (never its address) and the first free identity colour, and kept
from then on. A Mac in front of cloud APIs only is the controller's host and no node. A node record
carries `transport`, `endpoint`, `address` and `trust`: `endpoint` `local` reads this process; any
other is a remote node, `off` until its agent (SRV-725) and pairing (SEC-4) give it a source.

THE DEPLOYMENTS. Each model an engine backend on a node serves itself is a Single deployment on that
node; its id is the model's public id at this gateway, its status the backend's health.

METRICS. The sampler keeps each hardware figure of each node, and decode tok/s, requests and TTFT
per engine backend, in memory (`Ring`: 2 s slots for 10 minutes, 30 s slots for 7 days). A node's
tok/s and requests are the sum of its engines', its TTFT their mean; a deployment's are its
engine's. An answer's sample i is the mean of the samples in (t_i - step, t_i]; with none it is
null. History starts with the process (a TandemLLM engine's own five minutes before it): earlier,
and while a node gave nothing, the answer says null.
"""

from __future__ import annotations

import math
import socket
import sys
import threading
import time
from array import array
from urllib.parse import unquote

from proxy import config as config_mod
from proxy import hostcollect
from proxy import nodesource

CONTRACT = "1.0"
SAMPLE_S = 2.0
# a dashboard read the fleet this recently: the engines are read at every sample
WATCHED_S = 60.0
# fleet-v1's metric keys: unit and how nodes combine (the page aggregates, never the controller)
METRICS = {"tps": ("tok/s", "sum"), "req": ("req", "sum"), "ttft": ("ms", "mean"),
           "gpu": ("%", "mean"), "power": ("W", "sum"), "temp": ("°C", "maxmean"),
           "sm": ("MHz", "mean"), "memclk": ("MHz", "mean"), "mem": ("GiB", "sum"),
           "cpu": ("%", "mean"), "disk": ("MB/s", "sum"), "lan": ("MB/s", "sum"),
           "fabric": ("Gb/s", "sum"), "rtt": ("ms", "max")}
# the figures a deployment's scope counts per engine backend, not per node
DEPLOYMENT_METRICS = ("tps", "req", "ttft")
# span and sample count per range; the step is span / (count - 1)
RANGES = {"5m": (300, 151), "1h": (3600, 121), "6h": (21600, 145), "24h": (86400, 145),
          "7d": (604800, 169)}
DEVICES = ("desk", "tablet", "phone")
ICONS = ("desk", "cloud", "rack")
COLOURS = range(1, 9)
LEVELS = ("debug", "info", "warn", "error")
WINDOW_KINDS = ("terminal", "files", "editor", "diff", "markdown", "plan", "preview", "image",
                "pdf", "video", "audio", "binary", "logs", "chart", "inspector", "chat",
                "playground", "context")
DOCKS = ("right", "bottom", "float", "min")
ALIAS_MAX, TAGS_MAX, TAG_MAX, WINDOWS_MAX, PAGE_MAX = 40, 16, 32, 64, 80


class FleetError(Exception):
    """A 400 or 404 in the contract's error shape."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message

    def body(self) -> dict:
        return {"error": {"type": "not_found" if self.status == 404 else "bad_request",
                          "message": self.message}}


def window(rng: str, now: float) -> tuple[float, float, int]:
    """(step, t0, count) of a range ending at `now`: the end on a 30 s grid, or on the step when
    it is finer (the mock's `window`)."""
    span, count = RANGES[rng]
    step = span / (count - 1)
    grid = min(step, 30.0)
    end = math.floor(now / grid) * grid
    return step, end - span, count


class Ring:
    """One series in memory: per slot the sum, count and max of its samples, in two tiers (2 s
    slots for 10 minutes, 30 s slots for 7 days and two hours). Slot k of a tier holds the
    samples in ((k-1)·res, k·res]."""

    TIERS = ((2, 300), (30, 604800 // 30 + 240))

    def __init__(self):
        self.tiers = []
        for res, n in self.TIERS:
            self.tiers.append((res, n, array("d", bytes(8 * n)), array("d", bytes(8 * n)),
                               array("d", bytes(8 * n)), array("q", [-1]) * n))

    def add(self, t: float, v: float) -> None:
        for res, n, s, c, mx, ks in self.tiers:
            k = math.ceil(t / res)
            i = k % n
            if ks[i] != k:
                ks[i], s[i], c[i], mx[i] = k, 0.0, 0.0, -math.inf
            s[i] += v
            c[i] += 1
            mx[i] = max(mx[i], v)

    def _tier(self, res: int):
        return next(t for t in self.tiers if t[0] == res)

    def mean(self, res: int, k_lo: int, k_hi: int) -> float | None:
        _, n, s, c, _, ks = self._tier(res)
        tot = cnt = 0.0
        for k in range(max(k_lo, k_hi - n + 1), k_hi + 1):
            i = k % n
            if ks[i] == k:
                tot += s[i]
                cnt += c[i]
        return tot / cnt if cnt else None

    def peak(self, now: float, span: float) -> float | None:
        """The largest sample of the last `span` seconds (30 s slots)."""
        _, n, _, _, mx, ks = self._tier(30)
        k_hi = math.ceil(now / 30)
        got = [mx[k % n] for k in range(k_hi - min(n, int(span // 30)) + 1, k_hi + 1)
               if ks[k % n] == k]
        return max(got) if got else None


def default_layout(page: str, device: str) -> dict:
    """What a page answers before its first save (the mock's `defaultLayout`)."""
    wide = device == "desk"
    return {"page": page, "device": device, "rail": {"open": wide, "w": 248},
            "ctx": {"open": wide, "w": 340}, "windows": [],
            "docks": {"right": {"f": 0.38, "act": None}, "bottom": {"f": 0.34, "act": None}},
            "saved_at": None}


def _isnum(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _keys(d: dict, allowed: tuple, required: tuple, what: str) -> None:
    extra = [k for k in d if k not in allowed]
    if extra:
        raise FleetError(400, f"{what}: unknown field {extra[0]!r}")
    missing = [k for k in required if k not in d]
    if missing:
        raise FleetError(400, f"{what}: {missing[0]} is required")


def check_layout(b, page: str, device: str) -> dict:
    """The body of a layout PUT, held to layout.schema.json (field for field: a saved layout
    answers every later GET, so it must validate); FleetError 400 naming the first fault."""
    if not isinstance(b, dict):
        raise FleetError(400, "the body is a layout object")
    _keys(b, ("page", "device", "rail", "ctx", "windows", "docks", "saved_at"),
          ("page", "device", "rail", "ctx", "windows", "docks"), "layout")
    if b["page"] != page or b["device"] != device:
        raise FleetError(400, f"the body is the layout of {page} on {device}")
    for side in ("rail", "ctx"):
        s = b[side]
        if not isinstance(s, dict):
            raise FleetError(400, f"{side} is {{open, w}}")
        _keys(s, ("open", "w"), ("open", "w"), side)
        if not isinstance(s["open"], bool) or not _isnum(s["w"]):
            raise FleetError(400, f"{side}: open is true or false, w a number")
    d = b["docks"]
    if not isinstance(d, dict):
        raise FleetError(400, "docks is {right, bottom}")
    _keys(d, ("right", "bottom"), ("right", "bottom"), "docks")
    for side in ("right", "bottom"):
        x = d[side]
        if not isinstance(x, dict):
            raise FleetError(400, f"docks.{side} is {{f, act}}")
        _keys(x, ("f", "act"), ("f", "act"), f"docks.{side}")
        if not _isnum(x["f"]) or not (x["act"] is None or isinstance(x["act"], str)):
            raise FleetError(400, f"docks.{side}: f is a number, act a window id or null")
    ws = b["windows"]
    if not isinstance(ws, list) or len(ws) > WINDOWS_MAX:
        raise FleetError(400, f"windows is a list of at most {WINDOWS_MAX}")
    for w in ws:
        if not isinstance(w, dict):
            raise FleetError(400, "a window is an object")
        _keys(w, ("id", "kind", "ref", "at", "prev", "x", "y", "w", "h", "z"),
              ("id", "kind", "ref", "at", "x", "y", "w", "h", "z"), "window")
        if not isinstance(w["id"], str):
            raise FleetError(400, "a window's id is a string")
        if w["kind"] not in WINDOW_KINDS:
            raise FleetError(400, f"unknown window kind {w['kind']!r}")
        if not isinstance(w["ref"], dict):
            raise FleetError(400, f"window {w['id']}: ref is an object")
        if w["at"] not in DOCKS:
            raise FleetError(400, f"window {w['id']}: at is right, bottom, float or min")
        if "prev" in w and w["prev"] not in DOCKS[:3]:
            raise FleetError(400, f"window {w['id']}: prev is right, bottom or float")
        if not all(_isnum(w[k]) for k in ("x", "y", "w", "h", "z")):
            raise FleetError(400, f"window {w['id']}: x, y, w, h and z are numbers")
    if "saved_at" in b and not (b["saved_at"] is None or _isnum(b["saved_at"])):
        raise FleetError(400, "saved_at is a Unix time or null")
    return b


class Fleet:
    def __init__(self, svc, local, *, port: int | None = None, bind: str = "127.0.0.1",
                 clock=time.time, log=None, locality=None):
        self.svc, self.cfg = svc, svc.cfg
        self.local = local
        self.port, self.bind = port, bind
        self.clock = clock
        self.log = log or (lambda msg: print(msg, flush=True))
        self.locality = locality or hostcollect._Locality(clock=clock)
        self.rings: dict[tuple, Ring] = {}
        self._lock = threading.Lock()
        self._reg = threading.Lock()            # one registration, whichever thread asks first
        self._local_id: str | None = None
        self._read_at = -math.inf               # the last /v1/fleet request
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ the sampler
    def start(self) -> "Fleet":
        self._thread = threading.Thread(target=self._run, name="fleet-sampler", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            # mid-slot: a tick never falls on a slot's edge, so each 2 s slot gets one sample
            now = self.clock()
            due = (math.floor(now / SAMPLE_S) + 0.5) * SAMPLE_S
            if due <= now:
                due += SAMPLE_S
            if self._stop.wait(due - now):
                return
            try:
                self.tick()
            except Exception as exc:                                 # noqa: BLE001
                self.log(f"[fleet] sample failed: {type(exc).__name__}: {exc}")

    def tick(self, now: float | None = None) -> None:
        """One sample of every node with a source into the rings."""
        now = self.clock() if now is None else now
        lid = self.ensure_local()
        if lid is None:
            return
        got = self.local.sample(now, watched=now - self._read_at < WATCHED_S)
        self.local.inventory()                   # read here, so a request finds it ready
        with self._lock:
            for k, v in got["node"].items():
                if v is not None:
                    self._ring(("node", lid, k)).add(now, v)
            for bid, points in got["backends"].items():
                for t, vals in points:
                    for k, v in vals.items():
                        if v is not None:
                            self._ring(("backend", bid, k)).add(t, v)

    def _ring(self, key: tuple) -> Ring:
        r = self.rings.get(key)
        if r is None:
            r = self.rings[key] = Ring()
        return r

    # ------------------------------------------------------------------ the registry
    def engine_backends(self) -> list[dict]:
        """The engine backends on this machine (the URL is), enabled."""
        return [b for b in self.cfg.backends()
                if b["enabled"] and not b["cloud"] and b["kind"] in nodesource.ENGINE_KINDS
                and self.locality.is_local(b["url"])]

    def ensure_local(self) -> str | None:
        """This machine's node id; registered here the first time it is a node. Its address,
        hardware and serial follow the machine; a new address is said in the log."""
        self.local.sync(self.engine_backends())
        with self._reg:
            return self._ensure_local()

    def _ensure_local(self) -> str | None:
        ident = self.local.identity()
        row = self.cfg.node(ident["id"])
        if row is None:
            if not self.local.is_node():
                return None
            taken = {n["alias"].lower() for n in self.cfg.nodes()}
            alias, i = ident["alias"], 2
            while alias.lower() in taken:
                alias, i = f"{ident['alias']} {i}", i + 1
            row = self.cfg.add_node({"id": ident["id"], "alias": alias, "hw": ident["hw"],
                                     "dmi": ident["dmi"], "serial": ident["serial"],
                                     "transport": "lan", "endpoint": "local",
                                     "address": ident["address"], "trust": {"kind": "local"}})
            self.log(f"[fleet] this machine is node {row['alias']} ({row['id']}), "
                     f"colour {row['colour']}")
        else:
            fields = {k: ident[k] for k in ("hw", "dmi", "serial", "address")
                      if ident[k] != row[k]}
            if row["endpoint"] != "local":
                fields.update(endpoint="local", trust={"kind": "local"})
            if fields:
                if "address" in fields and row["address"]:
                    self.log(f"[fleet] {row['alias']} moved from {row['address']} to "
                             f"{fields['address']}")
                self.cfg.update_node(row["id"], fields, who="controller")
        self._local_id = row["id"]
        return row["id"]

    def source(self, row: dict):
        if row["endpoint"] == "local" and row["id"] == self._local_id:
            return self.local
        return nodesource.Unreachable()

    def node_view(self, row: dict, now: float) -> dict:
        src = self.source(row)
        status, text = src.status()
        local = src is self.local
        inv = self.local.inventory() if local else \
            {k: None for k in ("serial", "os", "kernel", "driver", "cuda", "cx7", "disk", "nics")}
        if not local:
            inv["serial"] = row["serial"]
        lease = row["lease_end_ms"]
        return {"id": row["id"], "alias": row["alias"], "colour": row["colour"],
                "icon": row["icon"], "tags": row["tags"], "class": row["class"],
                "hw": row["hw"] or "unknown", "dmi": row["dmi"] or "unknown",
                "owned": lease is None, "transport": row["transport"],
                "address": row["address"] or "", "version": self.local.version() if local
                else "", "status": status, "status_text": text, "since": row["added_at"] / 1000,
                "lease_end": None if lease is None else lease / 1000,
                "cost_per_hour": row["cost_per_hour"], "inventory": inv}

    def nodes(self, now: float) -> list[dict]:
        self.ensure_local()
        return [self.node_view(r, now) for r in self.cfg.nodes()]

    def _node_row(self, nid: str) -> dict:
        self.ensure_local()
        row = self.cfg.node(nid)
        if row is None:
            raise FleetError(404, f"no node {nid}")
        return row

    def patch_node(self, nid: str, body, who: str, now: float) -> dict:
        row = self._node_row(nid)
        if not isinstance(body, dict) or not body:
            raise FleetError(400, "the body is an object with alias, colour, icon or tags")
        _keys(body, ("alias", "colour", "icon", "tags"), (), "node")
        others = [n for n in self.cfg.nodes() if n["id"] != nid]
        f = {}
        if "alias" in body:
            a = body["alias"].strip() if isinstance(body["alias"], str) else ""
            if not a or len(a) > ALIAS_MAX or any(ord(c) < 32 for c in a):
                raise FleetError(400, f"alias is 1 to {ALIAS_MAX} characters")
            if any(n["alias"].lower() == a.lower() for n in others):
                raise FleetError(400, f"another node is called {a}")
            f["alias"] = a
        if "colour" in body:
            c = body["colour"]
            if not isinstance(c, int) or isinstance(c, bool) or c not in COLOURS:
                raise FleetError(400, "colour is a whole number from 1 to 8")
            held = next((n for n in others if n["colour"] == c), None)
            if held is not None:
                raise FleetError(400, f"colour {c} is {held['alias']}'s")
            f["colour"] = c
        if "icon" in body:
            if body["icon"] not in ICONS:
                raise FleetError(400, "icon is desk, cloud or rack")
            f["icon"] = body["icon"]
        if "tags" in body:
            t = body["tags"]
            if not isinstance(t, list) or len(t) > TAGS_MAX or not all(
                    isinstance(x, str) and 0 < len(x.strip()) <= TAG_MAX for x in t):
                raise FleetError(400, f"tags is a list of at most {TAGS_MAX} words of 1 to "
                                      f"{TAG_MAX} characters")
            f["tags"] = list(dict.fromkeys(x.strip() for x in t))
        return self.node_view(self.cfg.update_node(row["id"], f, who=who), now)

    # ------------------------------------------------------------------ what runs where
    def deployments(self) -> list[dict]:
        lid = self.ensure_local()
        if lid is None:
            return []
        now = self.clock()
        snap = self.svc.table.snapshot()["by_backend"]
        out = []
        for p in self.local.engines():
            rows = snap.get(p.id) or {}
            h = self.svc.health.view(p.id)
            cards = p.cards(now) if p.kind == "tandemllm" else None
            if cards is not None:
                items = [(c, rows.get(c["id"])) for c in cards]
            else:
                items = [({"id": m, "name": m, "model": m, "pack": "", "ctx": r["context_length"],
                           "loaded": True}, r) for m, r in sorted(rows.items())]
            for card, row in items:
                if row is None:                  # not served at this gateway (off, or gone)
                    continue
                if h["state"] == "up":
                    st, txt = ("serving", "serving") if card["loaded"] else \
                        ("starting", "loading the model")
                elif h["state"] == "down":
                    st, txt = "down", f"down · {h['last_error'] or 'no answer'}"
                else:
                    st, txt = "starting", "waiting for the first health check"
                pub = row["public_id"] or row["model"]
                out.append({"id": pub, "name": card["name"], "model": card["model"],
                            "pack": card["pack"], "type": "single", "members": [lid],
                            "api_id": pub, "ctx": int(card["ctx"] or row["context_length"] or 0),
                            "status": st, "status_text": txt})
        return out

    def _deployment_backends(self) -> dict:
        """{deployment id: (backend id, member node)}."""
        lid = self._local_id
        out = {}
        snap = self.svc.table.snapshot()["by_backend"]
        for d in self.deployments():
            for p in self.local.engines():
                if any((r["public_id"] or r["model"]) == d["id"]
                       for r in (snap.get(p.id) or {}).values()):
                    out[d["id"]] = (p.id, lid)
        return out

    def links_infra(self) -> tuple[list[dict], list[dict]]:
        """(infra, links): the LAN (the default route's interface) and, with a cloud backend, the
        internet. The controller runs on node #1 here, so the node is the host."""
        lid = self.ensure_local()
        lan = self.local.lan()
        infra, links = [], []
        at = lid
        if lid is None:
            infra.append({"id": "host", "kind": "host", "label": self.host_name()})
            at = "host"
        if lan is not None:
            infra.append({"id": "lan", "kind": "lan", "label": "LAN"})
            links.append({"id": f"lan-{at}", "a": "lan", "b": at, "kind": "lan",
                          "speed": lan["speed"], "rtt_ms": None, "mtu": lan["mtu"],
                          "state": lan["state"], "note": f"{lan['iface']}; the controller runs "
                                                         f"on {'this node' if lid else 'it'}"})
            at = "lan"
        cloud = [b for b in self.cfg.backends() if b["enabled"] and b["cloud"]]
        if cloud:
            up = [b for b in cloud if self.svc.health.view(b["id"])["state"] == "up"]
            infra.append({"id": "inet", "kind": "internet", "label": "Internet"})
            links.append({"id": f"{at}-inet", "a": at, "b": "inet", "kind": "wan",
                          "speed": "uplink", "rtt_ms": None, "mtu": None,
                          "state": "up" if len(up) == len(cloud) else
                          ("degraded" if up else "down"),
                          "note": "cloud backends: " + ", ".join(b["label"] or b["id"]
                                                                for b in cloud)})
        return infra, links

    def host_name(self) -> str:
        row = self.cfg.node(self._local_id) if self._local_id else None
        return row["alias"] if row else (socket.gethostname().split(".")[0] or "this host")

    def topology(self, now: float) -> dict:
        nodes = self.nodes(now)
        infra, links = self.links_infra()
        addr = self.local.address() if self.bind in ("0.0.0.0", "::", "") else self.bind
        return {"contract_version": CONTRACT,
                "host": {"name": self.host_name(), "address": addr,
                         "api_url": f"http://{addr}:{self.port}" if self.port else f"http://{addr}",
                         "may_sleep": sys.platform == "darwin"},
                "infra": infra, "nodes": nodes, "links": links,
                "groups": [{"deployment": d["id"], "type": d["type"], "members": d["members"]}
                           for d in self.deployments()]}

    # ------------------------------------------------------------------ figures
    def metrics(self, q: dict, now: float) -> dict:
        metric = q.get("metric") or ""
        rng = q.get("range") or "6h"
        source = q.get("source") or "all"
        if metric not in METRICS:
            raise FleetError(400, f'unknown metric "{metric}" (one of {", ".join(METRICS)})')
        if rng not in RANGES:
            raise FleetError(400, f'unknown range "{rng}" (one of {", ".join(RANGES)})')
        kind, _, sid = source.partition(":")
        if source != "all" and (kind not in ("node", "deployment") or not sid):
            raise FleetError(400, f'source is all, node:<id> or deployment:<id>, not "{source}"')
        lid = self.ensure_local()
        ids = [n["id"] for n in self.cfg.nodes()]
        engine = metric in DEPLOYMENT_METRICS

        def of(node):                            # the rings of a node's figure
            if not engine:
                return [("node", node, metric)]
            bids = [p.id for p in self.local.engines()] if node == lid else []
            return [("backend", b, metric) for b in bids]
        if kind == "node":
            if sid not in ids:
                raise FleetError(404, f"no node {sid}")
            keys = [(sid, of(sid))]
        elif kind == "deployment":
            deps = self._deployment_backends()
            if sid not in deps:
                raise FleetError(404, f"no deployment {sid}")
            bid, member = deps[sid]
            keys = [(member, [("backend", bid, metric)] if engine else of(member))]
        else:
            keys = [(i, of(i)) for i in ids]
        step, t0, count = window(rng, now)
        res = 2 if step < 30 else 30
        per = int(round(step / res))
        series = []
        with self._lock:
            for node, rkeys in keys:
                rings = [self.rings[k] for k in rkeys if k in self.rings]
                vals = []
                for i in range(count):
                    k_hi = int(round((t0 + i * step) / res))
                    got = [v for v in (r.mean(res, k_hi - per + 1, k_hi) for r in rings)
                           if v is not None]
                    v = None if not got else (sum(got) / len(got) if metric == "ttft"
                                              else sum(got))
                    vals.append(None if v is None else round(v, 3))
                series.append({"node": node, "values": vals})
        cap = self.local.usable() if metric == "mem" and self._local_id else None
        unit, agg = METRICS[metric]
        return {"contract_version": CONTRACT, "source": source, "metric": metric, "unit": unit,
                "agg": agg, "range": rng, "step_s": step, "t0": t0, "cap": cap,
                "series": series}

    def memory(self, now: float) -> dict:
        lid = self.ensure_local()
        out = []
        m = self.local.memory(now) if lid else None
        if m is not None:
            with self._lock:
                r = self.rings.get(("node", lid, "mem"))
                peak = r.peak(now, 86400) if r is not None else None
            used = m["weights"] + m["kv"] + m["cache"] + m["other"]
            out.append({"node": lid, "weights": m["weights"], "kv": m["kv"], "cache": m["cache"],
                        "other": m["other"], "page": m["page"], "ceiling": m["ceiling"],
                        "peak": round(max(peak or 0.0, used), 2), "usable": m["usable"]})
        return {"contract_version": CONTRACT, "unit": "GiB", "nodes": out}

    def logs(self, q: dict) -> dict:
        node = q.get("node") or "all"
        level = q.get("level")
        try:
            limit = int(q.get("limit") or 200)
        except ValueError:
            limit = 0
        lid = self.ensure_local()
        if node != "all" and self.cfg.node(node) is None:
            raise FleetError(404, f"no node {node}")
        if level is not None and level not in LEVELS:
            raise FleetError(400, f'level is debug, info, warn or error, not "{level}"')
        if not 1 <= limit <= 1000:
            raise FleetError(400, "limit is a whole number from 1 to 1000")
        lines = []
        if lid is not None and node in ("all", lid):
            lines = [dict(x, node=lid) for x in self.local.logs(level, q.get("q") or None, limit)]
        return {"contract_version": CONTRACT, "node": node, "lines": lines}

    # ------------------------------------------------------------------ the routes
    def handle(self, method: str, path: str, q: dict, body, who: str = "gui") -> tuple[int, dict]:
        """One /v1/fleet request, after the caller's guard: (status, body)."""
        parts = [unquote(p) for p in path[len("/v1/fleet"):].split("/") if p]
        now = self._read_at = self.clock()
        try:
            return 200, self._route(method, parts, q, body, who, now)
        except FleetError as exc:
            return exc.status, exc.body()
        except config_mod.ConfigError as exc:
            return (404 if exc.status == 404 else 400), FleetError(exc.status,
                                                                   exc.message).body()

    def _route(self, method, parts, q, body, who, now):
        v = CONTRACT
        if method == "GET" and len(parts) == 1:
            name = parts[0]
            if name == "nodes":
                return {"contract_version": v, "nodes": self.nodes(now)}
            if name == "links":
                return {"contract_version": v, "links": self.links_infra()[1]}
            if name == "topology":
                return self.topology(now)
            if name == "deployments":
                return {"contract_version": v, "deployments": self.deployments()}
            if name == "metrics":
                return self.metrics(q, now)
            if name == "memory":
                return self.memory(now)
            if name == "logs":
                return self.logs(q)
            if name == "events":
                s = q.get("since")
                try:
                    if s is not None:
                        float(s)
                except ValueError:
                    raise FleetError(400, f'since is a Unix time in seconds, not "{s}"')
                return {"contract_version": v, "events": []}
            if name == "alerts":
                return {"contract_version": v, "alerts": []}
            if name == "rules":
                return {"contract_version": v, "rules": []}
        if len(parts) == 2 and parts[0] == "nodes":
            if method == "GET":
                return self.node_view(self._node_row(parts[1]), now)
            if method == "PATCH":
                return self.patch_node(parts[1], body, who, now)
        if method == "POST" and len(parts) == 3 and parts[0] == "alerts" and parts[2] == "ack":
            raise FleetError(404, f"no active alert {parts[1]}")
        if len(parts) == 2 and parts[0] == "layouts" and method in ("GET", "PUT"):
            page, device = parts[1], q.get("device") or "desk"
            if device not in DEVICES:
                raise FleetError(400, f'device is desk, tablet or phone, not "{device}"')
            if len(page) > PAGE_MAX or any(ord(c) < 32 for c in page):
                raise FleetError(400, f"a page is 1 to {PAGE_MAX} characters")
            if method == "GET":
                return self.cfg.layout(page, device) or default_layout(page, device)
            doc = dict(check_layout(body, page, device), saved_at=round(now, 3))
            self.cfg.set_layout(doc)
            return doc
        raise FleetError(404, f"no route {method} /v1/fleet/{'/'.join(parts)}")
