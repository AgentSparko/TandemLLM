"""Backends as fleet members (SRV-749): a server reached by URL, with no TandemNode on its host.

    mem = Members(fleet)                   one per Fleet (fleet.members)
    mem.sources()                          [(row, source)] of every backend member
    mem.views(now)                         /v1/fleet/nodes `backends`: each as a node-shaped view
    mem.view(member id, now)               one (GET /v1/fleet/nodes/{id}), FleetError 404
    mem.sample_view(member id)             its `sample`: none, and why
    mem.note(rec)                          the gateway accounted a request (its first-token time)

WHO IS A MEMBER. A backend of the config database that sits on no node: a cloud API, or an
OpenAI-compatible server on a machine without TandemNode. A backend whose URL is on a node's
address is that node's engine (proxy/fleet.py `engine_backends`), as before: that address is the
host link, and the node's hardware figures are the backend's. This machine's own local backends
are its node's when it is one. A member's id is `backend.<backend id>` (a backend id holds no
dot, a node id never starts so), its alias the backend's label.

WHAT A MEMBER GIVES. Only what the OpenAI-compatible surface gives: its model list (health,
proxy/health.py), and what this gateway measured of the requests it relayed: the requests in
flight and the time to the first token. A server whose own metrics page the app reads
(proxy/engines.py) gives its decode rate and running requests as on a node. Everything a
TandemNode agent reads (GPU, memory, power, disks, logs, the engine's identity, start and stop)
is not available for a member, and each answer says so instead of failing: `sample` is null with
a note, a hardware series is all null, an action answers 409 `not_a_node`.

A member's `status`: `up` (its model list answers), `alert` with "key rejected" (the server
answers and refuses the key: it is reachable, the key is wrong), `off` (down, disabled, or not
checked yet).
"""

from __future__ import annotations

import collections
import json
import threading
from urllib.parse import urlsplit

from proxy import engineapi, engines, nodesource

PREFIX = "backend."
COLOURS = range(1, 9)
NO_FIGURES = ("no host figures: no TandemNode runs on this backend's host. Add the machine as "
              "a Spark to read its GPU, memory and power")
NOT_A_NODE = ("is a backend without TandemNode: only its model list and what this gateway "
              "relays are known. Add its machine as a Spark for this")
TTFT_KEEP = 512
API_EVERY_S = 30.0


def member_id(bid: str) -> str:
    return PREFIX + bid


def backend_of(mid) -> str | None:
    return mid[len(PREFIX):] if isinstance(mid, str) and mid.startswith(PREFIX) else None


def _hostport(url: str) -> tuple[str | None, int | None]:
    try:
        u = urlsplit(url)
        return u.hostname, u.port or (443 if u.scheme == "https" else 80)
    except ValueError:
        return None, None


def what(b: dict) -> str:
    """A member's kind in words (the `hw` of its view)."""
    if b["cloud"]:
        return "cloud API"
    return "TandemLLM engine by URL" if b["kind"] == "tandemllm" else "OpenAI-compatible server"


class BackendSource:
    """A member's source: the shape of a node's (proxy/nodesource.py), with one probe and no
    machine behind it."""

    endpoint = None

    def __init__(self, members, backend: dict):
        self.members = members
        self.backend = backend
        try:
            key = members.cfg.key_for(backend["id"])
        except Exception:                                            # noqa: BLE001
            key = None
        self.probe = nodesource.EngineProbe(backend, key=key)
        # whether the server's own page gives its rates (else the gateway's count is all)
        self.own = backend["kind"] == "tandemllm" or backend["kind"] in engines.TABLES
        self._api: dict | None = None
        self._api_at: float | None = None

    def api(self, now: float) -> dict | None:
        """A TandemLLM engine reached by URL: the engine API its /health says (read with the
        backend's key, every API_EVERY_S; proxy/engineapi.py). An answer without the field (the
        open answer, an engine before the field) is read as 1.0. None for any other kind and
        before the first answer."""
        if self.backend["kind"] != "tandemllm" or not self.backend["enabled"]:
            return None
        if self._api_at is None or now - self._api_at >= API_EVERY_S:
            self._api_at = now
            try:
                body = self.probe._get("/health")
                doc = json.loads(body) if body is not None else None
            except Exception:                                        # noqa: BLE001
                doc = None
            if isinstance(doc, dict):
                self._api = engineapi.read(doc, "tandemllm")
        return self._api

    def engine_view(self, now: float) -> dict:
        """What an agent's engine view says of the engine API check, for the deployments."""
        a = self.api(now)
        if a is None:
            return {}
        return {None: {"kind": "tandemllm", "managed": False,
                       "health": {"engine_api": a["engine_api"], "api_assumed": a["assumed"],
                                  "api_ok": a["ok"], "api_why": a["why"]}}}

    def status(self) -> tuple[str, str]:
        b = self.backend
        if not b["enabled"]:
            return "off", "disabled"
        h = self.members.svc.health.view(b["id"])
        if h["state"] == "up":
            a = self.api(self.members.fleet.clock())
            if a is not None and not a["ok"]:
                return "alert", a["why"]
            n = len(self.members.svc.table.snapshot()["by_backend"].get(b["id"]) or {})
            return "up", f"up · {n} model{'' if n == 1 else 's'}"
        if h.get("key_rejected"):
            return "alert", "key rejected: the server answers and refuses the API key"
        if h["state"] == "down":
            return "off", f"down · {h['last_error'] or 'no answer'}"
        return "off", "waiting for the first check"

    def version(self) -> str:
        return self.probe.version or ""

    def engines(self) -> list:
        return [self.probe] if self.backend["enabled"] else []

    def inventory(self) -> dict:
        return {k: None for k in ("serial", "os", "kernel", "driver", "cuda", "cx7", "disk",
                                  "nics")}

    def forget_engines(self) -> None:
        self._api_at = None                      # read its engine API again at the next look

    def lan(self) -> None:
        return None

    def usable(self) -> None:
        return None

    def memory(self, now: float) -> None:
        return None

    def checks(self) -> dict:
        return {"mem_avail": None, "disk": None, "throttle": None}

    def logs(self, level, q, limit) -> list[dict]:
        return []

    def sample(self, now: float, watched: bool = True) -> dict:
        """No machine figures; the backend's own page when the app reads one, and always what
        the gateway saw: its requests in flight and each relayed request's first-token time."""
        bid = self.backend["id"]
        if not self.backend["enabled"]:
            return {"node": {}, "backends": {}}
        pts = []
        if self.own:
            try:
                pts = list(self.probe.points(now, watched))
            except Exception:                                        # noqa: BLE001
                pts = []
        if not any(p.get("req") is not None for _, p in pts):
            pts.append((now, {"tps": None, "ttft": None,
                              "req": float(self.members.svc.relay.inflight.count(bid))}))
        pts += [(t, {"tps": None, "req": None, "ttft": v})
                for t, v in self.members.take_ttft(bid)]
        return {"node": {}, "backends": {bid: pts}}


class Members:
    def __init__(self, fleet):
        self.fleet = fleet
        self.cfg, self.svc = fleet.cfg, fleet.svc
        self._lock = threading.Lock()
        self._src: dict[str, tuple[tuple, BackendSource]] = {}
        self._ttft: dict[str, collections.deque] = {}
        self._ids: frozenset = frozenset()
        self._gen = None

    # ------------------------------------------------------------------ who
    def _on_node(self) -> list[tuple[str | None, set | None]]:
        """(address, ports or None for the whole machine) of every node."""
        f = self.fleet
        out = []
        for row in self.cfg.nodes():
            src = f.source(row)
            addr = row["address"] or (src.address() if hasattr(src, "address") else None)
            out.append((addr, f._virtual_ports(row, src)))
        return out

    def backends(self) -> list[dict]:
        """The backends on no node, in the config database's order."""
        f = self.fleet
        held = self._on_node()
        local = f._local_id is not None
        out = []
        for b in self.cfg.backends():
            if not b["cloud"] and b["kind"] in nodesource.ENGINE_KINDS:
                if local and f.locality.is_local(b["url"]):
                    continue
                host, port = _hostport(b["url"])
                if any(host == a and (ports is None or port in ports) for a, ports in held):
                    continue
            out.append(b)
        self._ids = frozenset(b["id"] for b in out)
        return out

    def ids(self) -> frozenset:
        """The member backends' ids, read again when the configuration changed."""
        gen = self.cfg.generation
        if self._gen != gen:
            self.backends()
            self._gen = gen
        return self._ids

    def any(self) -> bool:
        return bool(self.ids())

    def sources(self) -> list[tuple[dict, BackendSource]]:
        """(row, source) of every member; a source is kept while its backend does not change."""
        bs = self.backends()
        taken = {n["colour"] for n in self.cfg.nodes()}
        out, keep = [], {}
        with self._lock:
            for i, b in enumerate(bs):
                key = (b["url"], b["kind"], b["enabled"], b["updated_at"])
                got = self._src.get(b["id"])
                if got is None or got[0] != key:
                    got = (key, BackendSource(self, b))
                got[1].backend = b
                keep[b["id"]] = got
                free = [c for c in COLOURS if c not in taken]
                colour = free[0] if free else (i % len(COLOURS)) + 1
                taken.add(colour)
                out.append((self._row(b, colour), got[1]))
            self._src = keep
        return out

    @staticmethod
    def _row(b: dict, colour: int) -> dict:
        host, port = _hostport(b["url"])
        return {"id": member_id(b["id"]), "alias": b["label"] or b["id"], "colour": colour,
                "icon": "cloud" if b["cloud"] else "rack", "tags": [], "class": "backend",
                "hw": what(b), "dmi": "", "serial": None, "transport": "lan", "endpoint": "",
                "address": f"{host}:{port}" if host else "", "trust": {}, "owner": None,
                "lease_end_ms": None, "cost_per_hour": None, "added_at": b["created_at"],
                "updated_at": b["updated_at"], "drain": None, "backend": b["id"]}

    # ------------------------------------------------------------------ the views
    def _view(self, row: dict, src: BackendSource) -> dict:
        b = src.backend
        status, text = src.status()
        h = self.svc.health.view(b["id"])
        state = "disabled" if not b["enabled"] else \
            "key_rejected" if h.get("key_rejected") else h["state"]
        try:
            key_set = bool(self.cfg.key_hint(b["id"]).get("key_set"))
        except Exception:                                            # noqa: BLE001
            key_set = False
        models = sorted(self.svc.table.snapshot()["by_backend"].get(b["id"]) or {})
        return {"id": row["id"], "alias": row["alias"], "colour": row["colour"],
                "icon": row["icon"], "tags": [], "class": "backend", "hw": row["hw"], "dmi": "",
                "owned": True, "transport": "lan", "address": row["address"],
                "version": src.version(), "status": status, "status_text": text,
                "since": row["added_at"] / 1000, "lease_end": None, "cost_per_hour": None,
                "inventory": src.inventory(),
                "pairing": {"state": "unpaired", "via": None, "fingerprint": None,
                            "paired_at": None, "host_key": None},
                "drain": None,
                "backend": {"id": b["id"], "kind": b["kind"], "cloud": b["cloud"],
                            "url": b["url"], "key_set": key_set, "state": state,
                            "error": h["last_error"], "latency_ms": h["latency_ms"],
                            "models": models,
                            "routes": list(self.svc.adapter(b).routes()),
                            "figures": "own" if src.own else "gateway",
                            "engine_api": src._api}}

    def views(self, now: float) -> list[dict]:
        return [self._view(row, src) for row, src in self.sources()]

    def find(self, mid: str):
        """(row, source) of a member id, or None."""
        if backend_of(mid) is None:
            return None
        return next(((r, s) for r, s in self.sources() if r["id"] == mid), None)

    def sample_view(self, mid: str, contract: str) -> dict | None:
        if self.find(mid) is None:
            return None
        return {"contract_version": contract, "node": mid, "via": "none", "t": None,
                "age_s": None, "sample": None, "note": NO_FIGURES}

    # ------------------------------------------------------------------ the gateway's figures
    def note(self, rec) -> None:
        """A relayed request ended: its first-token time counts for its member."""
        bid = getattr(rec, "backend", None)
        ms = getattr(rec, "ttft_ms", None)
        if ms is None or bid not in self.ids():
            return
        with self._lock:
            self._ttft.setdefault(bid, collections.deque(maxlen=TTFT_KEEP)).append(
                (self.fleet.clock(), round(float(ms), 1)))

    def take_ttft(self, bid: str) -> list[tuple[float, float]]:
        with self._lock:
            q = self._ttft.pop(bid, None)
        return list(q or ())
