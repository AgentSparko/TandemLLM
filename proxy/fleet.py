"""TandemApp's controller: the fleet registry and its API, `/v1/fleet/*` (SRV-723).

    fleet = Fleet(svc, LocalNode(...))           one per tandem-app process (proxy/app.py)
    fleet.start() / fleet.stop()                 the sampler: every node's figures every 2 s
    fleet.handle(method, path, query, body, who) -> (status, JSON body)

The contract is docs/contract/fleet-v1 (its schemas, and the rules they cannot express); the
dashboard's mock (dashboard/mock/fleet.ts) answers the same routes the same way. The browser talks
to tandem-app only: every figure of a node is read here, server-side, and the routes keep the
dashboard's guard (proxy/app.py: reads the dashboard rule, writes `conductor_write`).

    GET   /v1/fleet/nodes, /v1/fleet/nodes/{id}     the nodes, with status and inventory
    GET   /v1/fleet/nodes/{id}/sample               the node's newest one-second sample, every
                                                    block its agent reads (VIS-248)
    GET   /v1/fleet/host                            the machine the controller runs on and the
                                                    controller's own process (VIS-248)
    PATCH /v1/fleet/nodes/{id}                      {alias?, colour?, icon?, tags?}
    GET   /v1/fleet/links, /v1/fleet/topology       the LAN and the internet as this host sees them
    *     /v1/fleet/deployments[/{id}[/actions/{action}]]   the deployments: records (Single,
                                                    Replicas, Split, Role) and the Singles derived
                                                    from what no record covers; start, stop and
                                                    move through the agents
                                                    (proxy/deployments.py, SRV-731)
    POST  /v1/fleet/placement                       {model, pack, ctx, ...}: each node's fit from
                                                    its own Setup Guide estimate and the types
                                                    allowed, or why not (proxy/placement.py)
    *     /v1/fleet/weights[/...]                   the packs on every node, a copy node to node
                                                    verified by content id, a removal with its
                                                    confirm (proxy/fleetweights.py)
    *     /v1/fleet/nodes/{id}/setup/<route>       the node's Setup Guide through its agent,
                                                    streams as they come; a write with the confirm
                                                    of GET /v1/fleet/nodes/{id}/setup-confirm/
                                                    <route> (proxy/fleetsetup.py, VIS-252; the
                                                    relay itself is proxy/app.py's)
    GET   /v1/fleet/metrics?source=&metric=&range=  per-node series from the sampler's rings
    GET   /v1/fleet/memory                          the unified memory split per node, GiB
    GET   /v1/fleet/logs?node=&level=&q=&limit=     the nodes' log lines, oldest first
    GET   /v1/fleet/events?since=&before=&node=&type=&limit=   the event log, newest first
    GET   /v1/fleet/alerts                          the alerts firing now, acknowledged or not
    POST  /v1/fleet/alerts/{id}/ack                 acknowledged (404 for one that does not fire)
    GET   /v1/fleet/rules                           the alert rules
    PATCH /v1/fleet/rules/{id}                      {enabled?, notify?, threshold?, for_s?, scope?}
    GET   /v1/fleet/layouts/{page}?device=          a page's saved window layout, or its default
    PUT   /v1/fleet/layouts/{page}?device=          saved with `saved_at` now: the last write wins
    GET   /v1/fleet/trust                           this controller's certificate and the audit
    POST  /v1/fleet/pair                            {address, code, port?}: pairing by code (SEC-4)
    POST  /v1/fleet/pair/ssh/hostkey                {host, port?}: the host key, new/same/changed
    POST  /v1/fleet/pair/ssh                        {host, user, port?, password?|key?, host_key?,
                                                    takeover?}: SSH once
    DELETE /v1/fleet/nodes/{id}[?force=1]           release: the node forgets this controller,
                                                    then the controller forgets the node
    GET   /v1/fleet/nodes/{id}/actions/{action}     what drain, undrain, restart-engine, reboot,
                                                    update, forget or release-lease would do,
                                                    and a confirm for the destructive ones
    POST  /v1/fleet/nodes/{id}/actions/{action}     {confirm?, engine?}: done, or a job started
                                                    (proxy/fleetops.py, VIS-250)
    POST  /v1/fleet/links/{id}/test                 a LAN link's round trip and bandwidth, kept
    GET   /v1/fleet/discovery[?since=]              what the Add Spark wizard finds, each device
                                                    classified; POST a run, /scan the consented
                                                    LAN scan, /address one device, /target an ssh
                                                    line (proxy/discovery.py, SRV-727)
    *     /v1/fleet/add[/{id}[/...]]                the Add Spark wizard's session: its targets,
                                                    connect, check, install, link and names,
                                                    resumable (proxy/addspark.py, VIS-246)
    *     /v1/fleet/update[/plan|/stop]             the fleet update: its plan (the order, the
                                                    Splits refused), the job per node with its
                                                    log, resumed after a restart
                                                    (proxy/fleetupdate.py, OPS-234)
    *     /v1/fleet/controller[/move|/handover|/take]   where the controller runs and moving it
                                                    to a Spark or back (proxy/ctlmove.py, OPS-234)
    *     /v1/fleet/app[/permissions/local-network|/export|/import[/apply]]   App settings: the
                                                    machine's permissions, the service, the
                                                    configuration as a file and its import
                                                    (proxy/appsettings.py); /v1/fleet/app/password
                                                    is proxy/app.py's (it sets the cookie)

A FLEET OF ONE. The machine tandem-app runs on is a node when it is Spark-class (an NVIDIA GPU or an
engine of its own, proxy/nodesource.py): at the first start it is registered with its hostname as
the alias, an id from the machine (never its address) and the first free identity colour, and kept
from then on. A Mac in front of cloud APIs only is the controller's host and no node. A node record
carries `transport`, `endpoint`, `address` and `trust`: `endpoint` `local` is this machine; an
`http(s)://` endpoint is a node's TandemNode agent (SRV-725), read with the trust the record names
(nodesource.agent_for: the loopback secret, or pinned mTLS from pairing, SEC-4); a record without
either is `off`.

THIS MACHINE THROUGH ITS AGENT. When a TandemNode agent runs on this machine (its loopback address
and secret file, `agent`), the machine is read through it: the same stream, relay and code as a
remote node's, so one path feeds every node's figures. While it does not answer, the machine is read
in-process (LocalNode) as before, and the log says each switch.

PAIRED NODES (SEC-4, proxy/fleettrust.py). Pairing by code or by SSH once gives a node record its
`trust` (the controller's certificate and the node's pinned one) and its `https://` endpoint; a
node's `pairing` says how (local, paired by code or ssh, or unpaired) and the pinned certificate's
fingerprint. An engine backend of kind tandemllm on another machine with a host collector (the
one-peer setup before TandemApp) is a node record at its address, unpaired until pairing finds it.
Pairing, release and take-over are routes of their own (the `fleet_trust` rule of server/auth.py:
the admin password, or this machine; with no password set, this machine only).

THE DEPLOYMENTS (proxy/deployments.py, SRV-731) are records: a type, members (node, engine,
backend), a pack and options; the gateway routes a record's API id to its members, and /v1/models
lists each deployment once. A model an engine on a node serves that no record covers is a Single
derived from what that engine serves (the box's unmanaged engine on :8000 among them); 5.3's
interim rule (one model id on two nodes = Replicas) is gone: Replicas are records.

A CONTROLLER THAT MOVED (proxy/ctlmove.py) says where to (`moved_to`) and reads no node any more,
this machine's included. TANDEM_LOCAL_NODE=off (a controller a virtual node started) never makes
this machine a node.

METRICS. The sampler keeps each hardware figure of each node, and decode tok/s, requests and TTFT
per engine backend, in the fleet store (proxy/fleetstore.py: 2 s slots for 15 minutes, 30 s for
26 hours, 10 minutes for the retention, in `fleet.sqlite3` beside the config database). A node's
tok/s and requests are the sum of its engines'; a deployment's are its engine's. An answer's
sample i is the mean of the samples in (t_i - step, t_i], TTFT their median (p50) over every
request the node's engines ended then; with none it is null. Each sample is read from the
coarsest tier whose whole slots make its window and that still holds it (a 7d sample beyond the
30 s tier's 26 hours: the six 10-minute slots that end in its hour). History starts with the
store; while the controller was stopped, and while a node gave nothing, the answer says null.

EVENTS AND ALERTS (proxy/fleetwatch.py). Each sample runs the alert rules over every node's
figures and logs what changed: a node up or off, an engine down, up or restarted, a deployment's
status, and here the controller's start and stop and a new address; pairing, release and
take-over in proxy/fleettrust.py.

NODE ACTIONS (proxy/fleetops.py). A drained node takes no new request through this gateway (the
relay asks `svc.drain_of`); restart, reboot and update go to the node's agent; forget is the
release above; each destructive one runs only with the confirm of the plan it was read from.
A virtual node on this machine (a second agent, OPS-197) serves the engine backends whose port
its agent lists, never this machine's own.

TUNNELS (proxy/sshtunnel.py). A node with `transport` tunnel (a rented or remote one, paired over
SSH with `tunnel: true`) carries its SSH route in its trust; its source is its agent at the local
end of the tunnel, opened when the node is read and kept open, so its endpoint's port is the
tunnel's. THIS MACHINE'S NODE tells its agent that it is the fleet's own (PUT /node/fleet): the
agent then advertises itself over mDNS as owned by this controller, so another TandemApp on the
LAN lists it as "Spark owned by another TandemApp", never as one to pair.
"""

from __future__ import annotations

import json
import math
import os
import socket
import sys
import threading
import time
from urllib.parse import unquote, urlsplit

from proxy import _version
from proxy import config as config_mod
from proxy import fleetops
from proxy import fleetstore
from proxy import fleetwatch
from proxy import hostcollect
from proxy import nodemetrics
from proxy import nodesource

CONTRACT = "1.0"
GIB = 1024 ** 3
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
# span and sample count per range; the step is span / (count - 1). 30d (hourly, the 600 s tier
# that keeps the retention) is for the energy of a month on Usage (VIS-254)
RANGES = {"5m": (300, 151), "1h": (3600, 121), "6h": (21600, 145), "24h": (86400, 145),
          "7d": (604800, 169), "30d": (2592000, 721)}
DEVICES = ("desk", "tablet", "phone")
ICONS = ("desk", "cloud", "rack")
COLOURS = range(1, 9)
LEVELS = ("debug", "info", "warn", "error")
EVENT_TYPES = ("alert", "failover", "deploy", "link", "weights", "node", "update", "engine",
               "controller")
EVENTS_MAX = 1000
LOG_DEEP = 5000                             # the most a node agent and an engine give at once
DEPLOYMENTS_EVERY_S = 10.0
WINDOW_KINDS = ("terminal", "files", "editor", "diff", "markdown", "plan", "preview", "image",
                "pdf", "video", "audio", "binary", "logs", "chart", "inspector", "chat",
                "playground", "context")
DOCKS = ("right", "bottom", "float", "min")
ALIAS_MAX, TAGS_MAX, TAG_MAX, WINDOWS_MAX, PAGE_MAX = 40, 16, 32, 64, 80


class FleetError(Exception):
    """An error in the contract's shape: 400 bad_request, 404 not_found, or `kind`."""

    def __init__(self, status: int, message: str, kind: str | None = None):
        super().__init__(message)
        self.status, self.message = status, message
        self.kind = kind or ("not_found" if status == 404 else "bad_request")

    def body(self) -> dict:
        return {"error": {"type": self.kind, "message": self.message}}


def window(rng: str, now: float) -> tuple[float, float, int]:
    """(step, t0, count) of a range ending at `now`: the end on a 30 s grid, or on the step when
    it is finer (the mock's `window`)."""
    span, count = RANGES[rng]
    step = span / (count - 1)
    grid = min(step, 30.0)
    end = math.floor(now / grid) * grid
    return step, end - span, count


Ring = fleetstore.Ring


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
                 clock=time.time, log=None, locality=None, agent=None, agent_for=None,
                 store=None):
        self.svc, self.cfg = svc, svc.cfg
        self.local = local
        self.agent = agent                      # this machine's TandemNode agent, or None
        self.agent_for = agent_for or nodesource.agent_for
        self.agents: dict = {}                  # node id -> (endpoint and trust, source)
        self._via_agent = False
        self.port, self.bind = port, bind
        self.clock = clock
        self.log = log or (lambda msg: print(msg, flush=True))
        self.locality = locality or hostcollect._Locality(clock=clock)
        self.store = store or fleetstore.Store(clock=clock, log=self.log).open()
        self.rings = self.store.rings
        self._lock = self.store.lock
        self.watch = fleetwatch.Watch(self.store, clock=clock, log=self.log)
        self._deps_at = -math.inf
        self.may_sleep = sys.platform == "darwin"   # the controller's machine (a Mac) may sleep
        self._reg = threading.Lock()            # one registration, whichever thread asks first
        self._local_id: str | None = None
        self._read_at = -math.inf               # the last /v1/fleet request
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._trust = None
        self._app = None
        self._migrated = False
        self.started = clock()
        self._proc_prev: tuple[float, float] | None = None
        self.ops = fleetops.Ops(self)
        svc.drain_of = self.ops.drain_of        # the gateway's question, per request
        from proxy import deployments, fleetweights, placement
        self.deps = deployments.Deployments(self)
        svc.place, svc.listing, svc.locate = self._place, self._listing, self._locate
        self.placement = placement.Planner(self)
        self.weights = fleetweights.Weights(self)
        from proxy import fleetsetup
        self.setup = fleetsetup.SetupRelay(self)
        self._discovery = None
        self._wizard = None
        self._tunnels = None
        self._claimed_by = None                 # the agent told it is this fleet's own node
        self.local_node = True                  # TANDEM_LOCAL_NODE=off: this machine is no node
        self._moved: tuple | None = None        # (config generation, moved_to)
        self._updater = None
        self._mover = None

    def trust(self):
        """The controller's side of node trust (proxy/fleettrust.py), its certificate made at
        the first call, beside the config database."""
        if self._trust is None:
            from proxy import fleettrust
            with self._reg:
                if self._trust is None:
                    self._trust = fleettrust.Trust(self, os.path.join(
                        os.path.dirname(self.cfg.path), "controller"), clock=self.clock)
        return self._trust

    def discover(self):
        """The Add Spark wizard's list (proxy/discovery.py), made at the first call."""
        if self._discovery is None:
            from proxy import discovery
            with self._reg:
                if self._discovery is None:
                    self._discovery = discovery.Discovery(self, clock=self.clock)
        return self._discovery

    def wizard(self):
        """The Add Spark wizard's sessions (proxy/addspark.py), made at the first call."""
        if self._wizard is None:
            from proxy import addspark
            with self._reg:
                if self._wizard is None:
                    self._wizard = addspark.Wizard(self, clock=self.clock)
        return self._wizard

    def updater(self):
        """The fleet update (proxy/fleetupdate.py), made at the first call."""
        if self._updater is None:
            from proxy import fleetupdate
            with self._reg:
                if self._updater is None:
                    self._updater = fleetupdate.Updater(self, clock=self.clock)
        return self._updater

    def app(self):
        """App settings: permissions, the service, the configuration file (proxy/appsettings.py),
        made at the first call."""
        if self._app is None:
            from proxy import appsettings
            with self._reg:
                if self._app is None:
                    self._app = appsettings.AppSettings(self, clock=self.clock)
        return self._app

    def mover(self):
        """The controller's host and its moves (proxy/ctlmove.py), made at the first call."""
        if self._mover is None:
            from proxy import ctlmove
            with self._reg:
                if self._mover is None:
                    self._mover = ctlmove.Mover(self, clock=self.clock)
        return self._mover

    def moved_to(self) -> dict | None:
        g = self.cfg.generation
        if self._moved is None or self._moved[0] != g:
            self._moved = (g, self.cfg.setting("controller.moved_to"))
        return self._moved[1]

    @property
    def tunnels(self):
        """The SSH tunnels to the nodes behind one (proxy/sshtunnel.py)."""
        if self._tunnels is None:
            from proxy import sshtunnel
            with self._reg:
                if self._tunnels is None:
                    self._tunnels = sshtunnel.Tunnels(self.trust().ssh.known_hosts, log=self.log)
        return self._tunnels

    def forget(self, nid: str) -> None:
        """A node gone from the registry: its source closed, its rings dropped."""
        got = self.agents.pop(nid, None)
        if got is not None and hasattr(got[1], "close"):
            got[1].close()
        self.cfg.delete_node(nid, who="controller")
        with self._lock:
            keys = [k for k in self.rings if k[0] == "node" and k[1] == nid]
        self.store.drop(keys)

    # ------------------------------------------------------------------ the sampler
    def start(self) -> "Fleet":
        st = self.store
        if st.crashed:
            detail = "the last run did not stop cleanly: its newest minutes of history are lost"
        elif st.last_stop is not None:
            detail = f"stopped for {self.clock() - st.last_stop:.0f} s before"
        else:
            detail = "the first start with this store"
        self.store.add_event("controller", "host", f"TandemApp's controller started "
                                                   f"({self.svc.engine_version or 'tandem-app'})",
                             detail)
        self._thread = threading.Thread(target=self._run, name="fleet-sampler", daemon=True)
        self._thread.start()
        try:
            self.deps.interrupted()
            self.weights.interrupted()
        except Exception as exc:                                     # noqa: BLE001
            self.log(f"[fleet] the deployment jobs were not read: {type(exc).__name__}: {exc}")
        if self.cfg.job("update") is not None:
            try:
                self.updater().resume()
            except Exception as exc:                                 # noqa: BLE001
                self.log(f"[fleet] the fleet update did not resume: {type(exc).__name__}: {exc}")
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
        for src in [a for _, a in self.agents.values()] + [self.agent]:
            if hasattr(src, "close"):
                src.close()
        if self._tunnels is not None:
            self._tunnels.close_all()
        if self._thread is not None:
            self.store.add_event("controller", "host", "TandemApp's controller stopped",
                                 "history written")
        self.store.close()

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
        """One sample of every node with a source into the rings: an agent's samples each at its
        own second, this process's reading at `now`."""
        now = self.clock() if now is None else now
        self.ensure_local()
        watched = now - self._read_at < WATCHED_S
        for row, src in self.sources():
            if not hasattr(src, "sample"):
                continue
            got = src.sample(now, watched=watched)
            src.inventory()                      # read here, so a request finds it ready
            points = got.get("node_points")
            if points is None:
                points = [(now, got["node"])]
            with self._lock:
                for t, vals in points:
                    for k, v in vals.items():
                        if v is not None:
                            self._ring(("node", row["id"], k)).add(t, v)
                for bid, pts in got["backends"].items():
                    for t, vals in pts:
                        for k, v in vals.items():
                            if v is not None:
                                self._ring(("backend", bid, k)).add(t, v)
        try:
            self.watch.observe(now, self.readings(now))
        except Exception as exc:                                     # noqa: BLE001
            self.log(f"[fleet] the alert rules did not run: {type(exc).__name__}: {exc}")
        self.ops.refresh()
        if self.store.due(now):
            self.store.flush(now)

    def _ring(self, key: tuple) -> Ring:
        return self.store.ring(key)

    def readings(self, now: float) -> dict:
        """What the alert rules and the event log look at, per node (fleetwatch.Watch.observe)."""
        labels = {b["id"]: b["label"] or b["id"] for b in self.cfg.backends()}
        nodes = []
        for row, src in self.sources():
            status, text = src.status()
            checks = src.checks() if hasattr(src, "checks") else {}
            with self._lock:
                r = self.rings.get(("node", row["id"], "temp"))
                temp = None
                if r is not None and status != "off":
                    k = math.ceil(now / 2)
                    got = r.slot(2, k) or r.slot(2, k - 1) or r.slot(2, k - 2)
                    temp = got[2] if got else None
            jobs = {}
            if isinstance(src, nodesource.AgentNode):
                jobs = {e.get("id"): e.get("job") for e in src.agent_engines()}
            engines = []
            for p in src.engines() if hasattr(src, "engines") else []:
                h = self.svc.health.view(p.id)
                engines.append({"id": p.id, "label": labels.get(p.id, p.id),
                                "state": h["state"], "error": h["last_error"],
                                "restarts": getattr(p, "restarts", 0),
                                "job": jobs.get(getattr(p, "via", None))})
            lease = row["lease_end_ms"]
            nodes.append({"id": row["id"], "alias": row["alias"], "status": status,
                          "status_text": text, "temp": temp, "engines": engines,
                          "version": None if status == "off" else (src.version() or None),
                          "lease_left": None if lease is None else (lease / 1000 - now) / 3600,
                          "lease_end": None if lease is None else lease / 1000, **checks})
        deps = None
        if now - self._deps_at >= DEPLOYMENTS_EVERY_S:
            self._deps_at = now
            alias = {r["id"]: r["alias"] for r in self.cfg.nodes()}
            deps = [dict(d, on=" on " + ", ".join(alias.get(m, m) for m in d["members"]))
                    for d in self.deployments()]
        updating = self._updater is not None and self._updater.running()
        return {"nodes": nodes, "host": {"may_sleep": self.may_sleep},
                "deployments": deps, "updating": updating,
                "target": _version.release() or None}

    # ------------------------------------------------------------------ the registry
    def engine_backends(self, address: str | None = None, ports=None) -> list[dict]:
        """The engine backends, enabled, on this machine (the URL is) less those a virtual
        node claims, or on the node at `address` (the URL's host is that address; with `ports`,
        a node on this machine, only on those ports)."""
        claimed = self._claimed() if address is None else {}
        out = []
        for b in self.cfg.backends():
            if not b["enabled"] or b["cloud"] or b["kind"] not in nodesource.ENGINE_KINDS:
                continue
            if address is None and self.locality.is_local(b["url"]) and b["id"] not in claimed:
                out.append(b)
            elif address is not None and _host_of(b["url"]) == address and (
                    ports is None or _port_of(b["url"]) in ports):
                out.append(b)
        return out

    def _virtual_ports(self, row: dict, src) -> set | None:
        """A node that shares its machine with another (a virtual node, OPS-197: on this machine,
        or beside another node at one address elsewhere): the ports of the engines its agent
        lists; None for a node alone on its machine."""
        addr = row["address"] or (src.address() if isinstance(src, nodesource.AgentNode) else "")
        if not isinstance(src, nodesource.AgentNode) or src is self.agent or \
                row["id"] == self._local_id or not addr:
            return None
        here = self.locality.is_local(f"http://[{addr}]/" if ":" in addr else f"http://{addr}/")
        shared = any(n["id"] != row["id"] and n["address"] == addr for n in self.cfg.nodes())
        if not here and not shared:
            return None
        return {e.get("port") for e in src.agent_engines()}

    def _claimed(self) -> dict:
        """{backend id: node id} of this machine's engine backends a virtual node serves."""
        out = {}
        for row in self.cfg.nodes():
            if row["endpoint"] in ("local", "") or row["id"] == self._local_id:
                continue
            src = self.source(row)
            ports = self._virtual_ports(row, src)
            if ports:
                for b in self.engine_backends(row["address"] or src.address(), ports):
                    out[b["id"]] = row["id"]
        return out

    def ensure_local(self) -> str | None:
        """This machine's node id; registered here the first time it is a node. Its address,
        hardware and serial follow the machine; a new address is said in the log."""
        backends = self.engine_backends()
        self.local.sync(backends)
        with self._reg:
            lid = self._ensure_local()
            if not self._migrated:
                self._migrated = True
                from proxy import fleettrust
                fleettrust.migrate_peers(self)
        if lid is not None:
            src = self.here()
            if src is not self.local:
                src.sync(backends)
        return lid

    def here(self):
        """This machine's source: its TandemNode agent while it answers (and is this machine),
        else this process."""
        a = self.agent
        use = False
        if a is not None and self._local_id is not None:
            ident = a.identity() if a.healthy() else None
            use = ident is not None and ident["id"] == self._local_id
        if use != self._via_agent:
            self._via_agent = use
            self.log(f"[fleet] this machine is read through its TandemNode agent at "
                     f"{a.endpoint}" if use else
                     "[fleet] this machine is read in-process: its TandemNode agent does not "
                     "answer")
        if use and self._claimed_by is None:
            self._claim(a)
        return a if use else self.local

    def _claim(self, a) -> None:
        """This machine's agent hears that it is this fleet's own node (its mDNS then says
        owned); an older agent without the route is left as it is."""
        try:
            fp = self.trust().fingerprint()
            st, _ = a.client.json("PUT", "/node/fleet", {"fingerprint": fp, "name":
                                                          self.trust().name}, timeout=3.0)
        except Exception as exc:                                     # noqa: BLE001
            self.log(f"[fleet] this machine's agent did not take the fleet's claim: "
                     f"{type(exc).__name__}")
            return
        self._claimed_by = fp if st in (200, 404) else None

    def sources(self) -> list[tuple[dict, object]]:
        """(row, source) of every node; engine backends synced for the agents' nodes."""
        out = []
        for row in self.cfg.nodes():
            src = self.source(row)
            if isinstance(src, nodesource.AgentNode) and src is not self.agent:
                src.sync(self.engine_backends(row["address"] or src.address(),
                                              self._virtual_ports(row, src)))
            out.append((row, src))
        return out

    def _ensure_local(self) -> str | None:
        if not self.local_node or self.moved_to():
            self._local_id = None
            return None
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
                    self.store.add_event("node", row["id"], f"{row['alias']} has a new address",
                                         f"{row['address']} → {fields['address']}")
                self.cfg.update_node(row["id"], fields, who="controller")
        self._local_id = row["id"]
        return row["id"]

    def source(self, row: dict):
        if row["endpoint"] == "local":
            return self.here() if row["id"] == self._local_id else nodesource.Unreachable()
        tun = (row["trust"] or {}).get("tunnel")
        if tun and row["transport"] == "tunnel":
            row = dict(row, endpoint=f"https://127.0.0.1:{self.tunnels.ensure(tun)}")
        ep = row["endpoint"] or ""
        if not ep.startswith(("http://", "https://")):
            return nodesource.Unreachable("not paired: run `tandem node pair` on it and enter "
                                          "the code in Add Spark" if not row["trust"] else
                                          "not reachable: no node agent yet")
        key = (ep, json.dumps(row["trust"], sort_keys=True))
        got = self.agents.get(row["id"])
        if got is None or got[0] != key:
            if got is not None and hasattr(got[1], "close"):
                got[1].close()
            got = self.agents[row["id"]] = (key, self.agent_for(row, clock=self.clock))
        return got[1]

    def node_view(self, row: dict, now: float) -> dict:
        src = self.source(row)
        status, text = src.status()
        inv = src.inventory()
        if inv.get("serial") is None:
            inv["serial"] = row["serial"]
        lease = row["lease_end_ms"]
        drain = self.ops.drain_view(row, src)
        if drain is not None and status != "off":
            status = "draining"
            text = f"draining · {drain['inflight']} in flight" if drain["state"] == "draining" \
                else "drained · nothing in flight"
        return {"id": row["id"], "alias": row["alias"], "colour": row["colour"],
                "icon": row["icon"], "tags": row["tags"], "class": row["class"],
                "hw": row["hw"] or "unknown", "dmi": row["dmi"] or "unknown",
                "owned": lease is None, "transport": row["transport"],
                "address": row["address"] or "", "version": src.version(),
                "status": status, "status_text": text, "since": row["added_at"] / 1000,
                "lease_end": None if lease is None else lease / 1000,
                "cost_per_hour": row["cost_per_hour"], "inventory": inv,
                "pairing": pairing_view(row), "drain": drain}

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
        """Every deployment as /v1/fleet/deployments has it (proxy/deployments.py)."""
        return self.deps.all()

    # the gateway's questions (proxy/service.py), asked only of a fleet with records or nodes:
    # a plain proxy's requests never read a machine
    def _place(self, model, body, headers):
        return self.deps.place(model, body, headers)

    def _listing(self, created: int) -> list[dict]:
        if not self.cfg.deployments():
            return self.svc.table.listing(created)
        return self.deps.listing(created)

    def _locate(self, bid: str, model: str | None):
        if not self.cfg.nodes():
            return None
        return self.deps.locate(bid, model)

    def links_infra(self) -> tuple[list[dict], list[dict]]:
        """(infra, links): the LAN (the default route's interface) and, with a cloud backend, the
        internet. The controller runs on node #1 here, so the node is the host. A node read
        through its agent elsewhere joins the LAN by its own interface, the agent's round trip
        as the link's RTT."""
        lid = self.ensure_local()
        lan = (self.here() if lid else self.local).lan()
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
            for row, src in self.sources():
                if row["id"] == lid or not isinstance(src, nodesource.AgentNode):
                    continue
                nl = src.lan()
                if nl is None:
                    continue
                links.append({"id": f"lan-{row['id']}", "a": "lan", "b": row["id"], "kind": "lan",
                              "speed": nl.get("speed") or "speed unknown", "rtt_ms": src.rtt,
                              "mtu": nl.get("mtu"), "state": nl.get("state") or "down",
                              "note": f"{nl.get('iface')}; through its TandemNode agent"})
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
        tests = self.ops.tests()
        for x in links:
            if x["id"] in tests:
                x["test"] = tests[x["id"]]
        return infra, links

    def host_name(self) -> str:
        row = self.cfg.node(self._local_id) if self._local_id else None
        return row["alias"] if row else (socket.gethostname().split(".")[0] or "this host")

    def topology_host(self) -> dict:
        """The controller's host as the topology shows it: its name, address and API URL."""
        addr = self.local.address() if self.bind in ("0.0.0.0", "::", "") else self.bind
        return {"name": self.host_name(), "address": addr,
                "api_url": f"http://{addr}:{self.port}" if self.port else f"http://{addr}",
                "may_sleep": self.may_sleep}

    def topology(self, now: float) -> dict:
        nodes = self.nodes(now)
        infra, links = self.links_infra()
        return {"contract_version": CONTRACT,
                "host": self.topology_host(),
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
        self.ensure_local()
        srcs = {row["id"]: src for row, src in self.sources()}
        ids = list(srcs)
        engine = metric in DEPLOYMENT_METRICS

        def of(node):                            # the rings of a node's figure
            if not engine:
                return [("node", node, metric)]
            src = srcs.get(node)
            bids = [p.id for p in src.engines()] if hasattr(src, "engines") else []
            return [("backend", b, metric) for b in bids]
        if kind == "node":
            if sid not in ids:
                raise FleetError(404, f"no node {sid}")
            keys = [(sid, of(sid))]
        elif kind == "deployment":
            from proxy import deployments
            try:
                members = self.deps.backends_of(sid)
            except deployments.DeployError as exc:
                raise FleetError(exc.status, exc.message, exc.kind)
            keys = [(member, [("backend", bid, metric)] if engine else of(member))
                    for member, bid in members if member in ids]
        else:
            keys = [(i, of(i)) for i in ids]
        step, t0, count = window(rng, now)
        slots = [self.slots(step, t0 + i * step, now) for i in range(count)]
        series = []
        with self._lock:
            for node, rkeys in keys:
                rings = [self.rings[k] for k in rkeys if k in self.rings]
                vals = []
                for res, k_lo, k_hi in slots:
                    if metric == "ttft":
                        h: dict = {}
                        for r in rings:
                            r.hist(res, k_lo, k_hi, h)
                        v = fleetstore.p50(h)
                    else:
                        got = [v for v in (r.mean(res, k_lo, k_hi) for r in rings)
                               if v is not None]
                        v = sum(got) if got else None
                    vals.append(None if v is None else round(v, 3))
                series.append({"node": node, "values": vals})
        caps = [srcs[n].usable() for n, _ in keys] if metric == "mem" else []
        cap = round(sum(c for c in caps if c is not None), 2) if any(
            c is not None for c in caps) else None
        unit, agg = METRICS[metric]
        return {"contract_version": CONTRACT, "source": source, "metric": metric, "unit": unit,
                "agg": agg, "range": rng, "step_s": step, "t0": t0, "cap": cap,
                "series": series}

    def slots(self, step: float, t: float, now: float) -> tuple[int, int, int]:
        """(tier, first slot, last slot) of the sample at `t`: the coarsest tier whose whole slots
        make (t - step, t] and that still holds it; else the coarsest that divides the step, its
        slots that end in the window."""
        fit = [res for res in (2, 30, 600) if res <= step and step % res == 0]
        whole = [res for res in fit if t % res == 0 and
                 t - step >= now - fleetstore.TIER_KEEP.get(res, self.store.retention_s)]
        res = max(whole) if whole else max(fit)
        k_hi = math.floor(t / res + 1e-9)
        return res, k_hi - int(round(step / res)) + 1, k_hi

    def memory(self, now: float) -> dict:
        self.ensure_local()
        out = []
        for row, src in self.sources():
            m = src.memory(now) if hasattr(src, "memory") else None
            if m is None:
                continue
            with self._lock:
                r = self.rings.get(("node", row["id"], "mem"))
                peak = r.peak(now, 86400) if r is not None else None
            used = m["weights"] + m["kv"] + m["cache"] + m["other"]
            out.append({"node": row["id"], "weights": m["weights"], "kv": m["kv"],
                        "cache": m["cache"], "other": m["other"], "page": m["page"],
                        "ceiling": m["ceiling"], "peak": round(max(peak or 0.0, used), 2),
                        "usable": m["usable"]})
        return {"contract_version": CONTRACT, "unit": "GiB", "nodes": out}

    def sample_view(self, nid: str, now: float) -> dict:
        """A node's newest one-second sample as its agent sent it (proxy/nodemetrics.py): every
        block the board exposes, the rest absent. This machine read in-process has the fleet's
        keys, its GPUs and memory only; a node without a source has none, and says why."""
        row = self._node_row(nid)
        src = self.source(row)
        out = {"contract_version": CONTRACT, "node": nid, "via": "none", "t": None,
               "age_s": None, "sample": None, "note": None}
        if isinstance(src, nodesource.AgentNode):
            s = src.latest()
            out["via"] = "agent"
            if s is None:
                out["note"] = src.status()[1] if src.status()[0] == "off" else \
                    "no sample from its agent yet"
                return out
            t = s.get("t")
            out.update(t=t, age_s=None if t is None else round(max(0.0, now - t), 1),
                       sample={k: v for k, v in s.items() if k != "t"})
            return out
        if isinstance(src, nodesource.LocalNode):
            h = src.host() or {}
            mem = h.get("memory") or {}
            gib = (lambda b: None if b is None else round(b / GIB, 2))  # noqa: E731
            gpus = [{"index": g.get("index", i), "name": g.get("name"),
                     "util": None if g.get("utilization") is None else
                     round(g["utilization"] * 100.0, 2), "sm_mhz": g.get("sm_clock_mhz"),
                     "mem_mhz": g.get("mem_clock_mhz"), "power_w": g.get("power_w"),
                     "power_cap_w": g.get("power_limit_w"), "temp_c": g.get("temperature_c")}
                    for i, g in enumerate(h.get("gpus") or []) if isinstance(g, dict)]
            memory = {"total": gib(mem.get("total_bytes")),
                      "available": gib(mem.get("available_bytes")),
                      "used": gib(mem.get("used_bytes")),
                      "swap_total": gib(mem.get("swap_total_bytes")),
                      "swap_used": gib(mem.get("swap_used_bytes")), "unified": mem.get("unified")}
            sample = {"node": self._figures_now(nid, now), "gpus": gpus, "memory": memory}
            out.update(via="process", t=now, age_s=0.0, sample=nodemetrics.prune(sample),
                       note="read in-process: per-core, disk, network and board figures come "
                            "through a TandemNode agent on the node")
            return out
        out["note"] = src.status()[1]
        return out

    def _figures_now(self, nid: str, now: float) -> dict:
        """The newest 2 s slot of each of a node's fleet keys."""
        out = {}
        k = math.ceil(now / 2)
        with self._lock:
            for key in nodesource.FLEET_KEYS:
                r = self.rings.get(("node", nid, key))
                got = r and (r.slot(2, k) or r.slot(2, k - 1) or r.slot(2, k - 2))
                if got and got[1]:
                    out[key] = round(got[0] / got[1], 3)
        return out

    def host_view(self, now: float) -> dict:
        """The machine the controller runs on (its host collector's entry), the controller's own
        process, whether the machine may sleep, and which node it is when it is one."""
        lid = self.ensure_local()
        h = self.local.host() or {}
        mem, cpu = h.get("memory") or {}, h.get("cpu") or {}
        gib = (lambda b: None if b is None else round(b / GIB, 2))  # noqa: E731
        util = cpu.get("utilization")
        try:
            os_name = self.local.inventory().get("os")
        except Exception:                                            # noqa: BLE001
            os_name = None
        addr = self.local.address() if self.bind in ("0.0.0.0", "::", "") else self.bind
        return {"contract_version": CONTRACT, "name": self.host_name(), "os": os_name,
                "address": addr, "node": lid, "may_sleep": self.may_sleep,
                "cpu": {"util": None if not isinstance(util, (int, float)) else
                        round(util * 100.0, 1), "cores": cpu.get("count")},
                "memory": {"used_gib": gib(mem.get("used_bytes")),
                           "total_gib": gib(mem.get("total_bytes"))},
                "controller": self._process(now)}

    def _process(self, now: float) -> dict:
        """The controller's process: resident memory, CPU % since the last read (since the start
        at the first), threads and uptime."""
        t = os.times()
        cpu_s = t.user + t.system
        prev, self._proc_prev = self._proc_prev, (now, cpu_s)
        base = prev if prev is not None and now > prev[0] else (self.started, 0.0)
        util = None if now <= base[0] else round(max(0.0, (cpu_s - base[1]) / (now - base[0]))
                                                 * 100.0, 1)
        rss = None
        try:
            import psutil  # type: ignore
            rss = psutil.Process().memory_info().rss
        except Exception:                                            # noqa: BLE001
            try:
                with open("/proc/self/statm") as f:
                    rss = int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
            except (OSError, ValueError, IndexError):
                rss = None
        return {"rss_mib": None if rss is None else round(rss / 2 ** 20, 1), "cpu": util,
                "threads": threading.active_count(),
                "uptime_s": round(max(0.0, now - self.started), 1)}

    def logs(self, q: dict) -> dict:
        node = q.get("node") or "all"
        level = q.get("level") or "info"
        access = q.get("access") in ("1", "true")
        try:
            limit = int(q.get("limit") or 200)
        except ValueError:
            limit = 0
        self.ensure_local()
        if node != "all" and self.cfg.node(node) is None:
            raise FleetError(404, f"no node {node}")
        if level not in LEVELS:
            raise FleetError(400, f'level is debug, info, warn or error, not "{level}"')
        if not 1 <= limit <= 1000:
            raise FleetError(400, "limit is a whole number from 1 to 1000")
        # at debug the access lines are most of a log: read deeper to leave `limit` without them
        deep = LOG_DEEP if level == "debug" and not access else limit
        lines = []
        for row, src in self.sources():
            if node in ("all", row["id"]) and hasattr(src, "logs"):
                got = [dict(x, node=row["id"]) for x in src.logs(level, q.get("q") or None, deep)
                       if access or x["source"] != "access"]
                lines += got[-limit:]
        lines.sort(key=lambda x: x["t"])
        return {"contract_version": CONTRACT, "node": node, "lines": lines[-limit:]}

    def events(self, q: dict) -> list[dict]:
        """The event log, newest first: after `since`, before `before`, of `node`, of the
        comma-separated `type`s, at most `limit` (200, up to 1000)."""
        t = {}
        for k in ("since", "before"):
            s = q.get(k)
            if s is not None:
                try:
                    t[k] = float(s)
                except ValueError:
                    t[k] = math.nan
                if not math.isfinite(t[k]):
                    raise FleetError(400, f'{k} is a Unix time in seconds, not "{s}"')
        types = [x for x in (q.get("type") or "").split(",") if x]
        bad = [x for x in types if x not in EVENT_TYPES]
        if bad:
            raise FleetError(400, f'type is one or more of {", ".join(EVENT_TYPES)}, not '
                                  f'"{bad[0]}"')
        try:
            limit = int(q.get("limit") or 200)
        except ValueError:
            limit = 0
        if not 1 <= limit <= EVENTS_MAX:
            raise FleetError(400, f"limit is a whole number from 1 to {EVENTS_MAX}")
        return self.store.events(since=t.get("since"), before=t.get("before"),
                                 node=q.get("node") or None, types=types, limit=limit)

    # ------------------------------------------------------------------ the routes
    def _trusting(self, fn):
        from proxy import fleettrust
        try:
            return fn(self.trust())
        except fleettrust.TrustError as exc:
            raise FleetError(exc.status, exc.message, exc.kind)

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
        if parts[:1] == ["discovery"]:
            from proxy import discovery
            try:
                return self.discover().handle(method, parts, q, body, who)
            except discovery.DiscoveryError as exc:
                raise FleetError(exc.status, exc.message, exc.kind)
        if parts[:1] == ["add"]:
            from proxy import addspark
            try:
                return self.wizard().handle(method, parts, q, body, who)
            except addspark.AddError as exc:
                raise FleetError(exc.status, exc.message, exc.kind)
        if parts[:1] == ["update"]:
            from proxy import fleetupdate
            try:
                return self.updater().handle(method, parts, q, body, who, now)
            except fleetupdate.UpdateError as exc:
                raise FleetError(exc.status, exc.message, exc.kind)
        if parts[:1] == ["controller"]:
            from proxy import ctlmove
            try:
                return self.mover().handle(method, parts, q, body, who, now)
            except ctlmove.MoveError as exc:
                raise FleetError(exc.status, exc.message, exc.kind)
        if parts[:1] == ["app"]:
            from proxy import appsettings
            try:
                return self.app().handle(method, parts, q, body, who, now)
            except appsettings.AppError as exc:
                raise FleetError(exc.status, exc.message, exc.kind)
        if parts[:1] == ["deployments"]:
            from proxy import deployments
            try:
                return self.deps.handle(method, parts, q, body, who, now)
            except deployments.DeployError as exc:
                raise FleetError(exc.status, exc.message, exc.kind)
        if parts == ["placement"] and method == "POST":
            from proxy import placement
            try:
                return self.placement.plan(body, now)
            except placement.PlanError as exc:
                raise FleetError(exc.status, exc.message, exc.kind)
        if parts[:1] == ["weights"]:
            from proxy import fleetweights
            try:
                return self.weights.handle(method, parts, q, body, who, now)
            except fleetweights.WeightsError as exc:
                raise FleetError(exc.status, exc.message, exc.kind)
        if method == "GET" and len(parts) == 1:
            name = parts[0]
            if name == "nodes":
                return {"contract_version": v, "nodes": self.nodes(now)}
            if name == "links":
                return {"contract_version": v, "links": self.links_infra()[1]}
            if name == "topology":
                return self.topology(now)
            if name == "metrics":
                return self.metrics(q, now)
            if name == "memory":
                return self.memory(now)
            if name == "host":
                return self.host_view(now)
            if name == "logs":
                return self.logs(q)
            if name == "events":
                return {"contract_version": v, "events": self.events(q)}
            if name == "alerts":
                return {"contract_version": v, "alerts": self.watch.alerts()}
            if name == "rules":
                return {"contract_version": v, "rules": self.watch.rules()}
        if method == "GET" and len(parts) == 3 and parts[0] == "nodes" and parts[2] == "sample":
            return self.sample_view(parts[1], now)
        if method == "GET" and len(parts) >= 4 and parts[0] == "nodes" and \
                parts[2] == "setup-confirm":
            return self.setup.plan(parts[1], "/".join(parts[3:]), q, now)
        if len(parts) == 4 and parts[0] == "nodes" and parts[2] == "actions":
            if method == "GET":
                return self.ops.plan(parts[1], parts[3], now, q.get("engine") or None)
            if method == "POST":
                self._node_row(parts[1])
                return self.ops.act(parts[1], parts[3], body, who, now)
        if method == "POST" and len(parts) == 3 and parts[0] == "links" and parts[2] == "test":
            return self.ops.test_link(parts[1], who, now)
        if len(parts) == 2 and parts[0] == "nodes":
            if method == "GET":
                return self.node_view(self._node_row(parts[1]), now)
            if method == "PATCH":
                return self.patch_node(parts[1], body, who, now)
            if method == "DELETE":
                self._node_row(parts[1])
                return self._trusting(lambda t: t.release(parts[1], q.get("force") in (
                    "1", "true"), who))
        if method == "GET" and parts == ["trust"]:
            return self.trust().view()
        if method == "POST" and parts == ["pair"]:
            return self.node_view(self._trusting(lambda t: t.pair_code(body, who)), now)
        if method == "POST" and parts == ["pair", "ssh", "hostkey"]:
            return self._trusting(lambda t: t.host_key(body))
        if method == "POST" and parts == ["pair", "ssh"]:
            return self.node_view(self._trusting(lambda t: t.pair_ssh(body, who)), now)
        if method == "POST" and len(parts) == 3 and parts[0] == "alerts" and parts[2] == "ack":
            got = self.watch.ack(parts[1], who)
            if got is None:
                raise FleetError(404, f"no active alert {parts[1]}")
            return got
        if method == "PATCH" and len(parts) == 2 and parts[0] == "rules":
            try:
                return self.watch.edit(parts[1], body, who, [n["id"] for n in self.cfg.nodes()])
            except fleetwatch.RuleError as exc:
                raise FleetError(exc.status, exc.message)
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


def pairing_view(row: dict) -> dict:
    """How the controller trusts a node: this machine (local), paired by code or SSH once (the
    pinned certificate's fingerprint), or not paired."""
    t = row["trust"] or {}
    local = row["endpoint"] == "local" or t.get("kind") == "loopback-secret"
    paired = not local and t.get("kind") == "mtls"
    return {"state": "local" if local else "paired" if paired else "unpaired",
            "via": t.get("via") if paired else None,
            "fingerprint": t.get("fingerprint") if paired else None,
            "paired_at": t.get("paired_at") if paired else None,
            "host_key": t.get("host_key") if paired else None}


def _host_of(url: str) -> str | None:
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


def _port_of(url: str) -> int | None:
    try:
        u = urlsplit(url)
        return u.port or (443 if u.scheme == "https" else 80)
    except ValueError:
        return None
