"""The model list and the route of a request: which backend, under which upstream id.

`/v1/models` lists the real models with their backend. A model one backend serves is listed under
its own id (`deepseek-chat`, `Qwen/Qwen3.5-4B`); when two backends serve one id, each is listed as
`<backend id>/<model id>` and the bare id is not listed. The prefixed form is always accepted. A
model may get a public id of its own in the GUI; the list and the routes use it. In the engine's
own server, `TandemLLM` (and its hidden raw alias) stay the engine's, whatever a backend serves.

The route of a request, in this order:

    1. an explicit `<backend id>/` prefix whose rest that backend serves
    2. the first enabled routing rule whose glob matches the id (the id goes upstream unchanged
       unless the rule's backend lists it under another public id)
    3. the one backend that serves the id
    4. the default backend (TANDEM_DEFAULT_BACKEND, SRV-733), the id as asked: a TandemLLM engine
       answers any id with its own model, so a proxy in front of one answers what it did
    5. otherwise OpenAI's 404 `model_not_found` (two backends and no rule: the message names the
       prefixed forms)

A model of a TandemLLM backend is listed with the engine's own card (its profile, description,
`tandem` block and capabilities, as the engine's /v1/models has it, SRV-733) under the id this
proxy lists it by; the proxy's card while no list of the engine's was read yet.

A route carries its fallbacks: the model's own list, else its backend's. A fallback entry is a
backend id (the same model id there) or `<backend>/<model>`. The relay tries them on a connect
error, a 429, 502 or 503 before any byte went to the client.
"""

from __future__ import annotations

import threading

from proxy import config as config_mod
from proxy.adapters import KINDS, Generic


def capabilities(cls, m: dict) -> dict | None:
    """What a client may ask of a backend's model, from its provider's model list (0.11.3):
    images when its input kinds name `image`, the provider's effort levels, its default and the
    word that turns thinking off. None when the list said neither (vLLM, an older discovery)."""
    mods, eff = m.get("input_modalities"), m.get("effort")
    if mods is None and eff is None:
        return None
    return {"thinking": eff is not None, "effort_levels": list((eff or {}).get("levels") or []),
            "effort_default": (eff or {}).get("default"),
            "effort_off": getattr(cls, "effort_off", None) if eff else None,
            "tools": True, "vision": "image" in (mods or []), "structured": False}


def default_max_output(cls, m: dict) -> int | None:
    """A model's output limit without the operator's (VIS-227): the provider's model list, else
    the adapter's table. The Backends row shows it where the operator's field is empty."""
    return m.get("max_output") or cls.max_output.get(m["model"])


class Route:
    __slots__ = ("backend", "model", "public", "fallbacks", "price", "rule", "deployment",
                 "no_replay", "on_served")

    def __init__(self, backend: str, model: str, public: str, fallbacks=(), price=None,
                 rule=None):
        self.backend = backend          # backend id
        self.model = model              # the id sent upstream
        self.public = public            # the id the client asked for
        self.fallbacks = list(fallbacks)    # [(backend id, upstream model id)]
        self.price = price
        self.rule = rule
        # SRV-731 (proxy/deployments.py): the deployment a request goes to, whether a member that
        # took its body may hand it on (a constrained request: never), the turn's callback
        self.deployment = None
        self.no_replay = False
        self.on_served = None

    def candidates(self) -> list[tuple[str, str]]:
        return [(self.backend, self.model)] + self.fallbacks


class NotFound(Exception):
    def __init__(self, model: str, message: str):
        super().__init__(message)
        self.model, self.message = model, message

    def body(self) -> dict:
        return {"error": {"message": self.message, "type": "invalid_request_error",
                          "param": "model", "code": "model_not_found"}}


class Table:
    """A snapshot of the routable models, rebuilt when the config's generation moves."""

    def __init__(self, cfg: "config_mod.ConfigDB", reserved=()):
        self.cfg = cfg
        self.reserved = set(reserved)    # ids the engine itself answers (TandemLLM, its alias)
        # fn(backend id, upstream id) -> a TandemLLM engine's own card of the model, or None
        self.upstream_card = None
        self._gen = None
        self._lock = threading.Lock()
        self._snap = None

    def snapshot(self) -> dict:
        with self._lock:
            if self._snap is not None and self._gen == self.cfg.generation:
                return self._snap
            gen = self.cfg.generation
            backends = {b["id"]: b for b in self.cfg.backends() if b["enabled"]}
            served: dict[str, list] = {}       # public id -> [model row]
            by_backend: dict[str, dict] = {}   # backend -> {upstream id: row}
            for m in self.cfg.models():
                if m["backend"] not in backends or not m["enabled"] or not m["present"]:
                    continue
                pub = m["public_id"] or m["model"]
                served.setdefault(pub, []).append(m)
                by_backend.setdefault(m["backend"], {})[m["model"]] = m
            rules = [r for r in self.cfg.rules() if r["enabled"] and r["backend"] in backends]
            self._snap = {"backends": backends, "served": served, "by_backend": by_backend,
                          "rules": rules}
            self._gen = gen
            return self._snap

    # ----------------------------------------------------------------- the list
    def listing(self, created: int) -> list[dict]:
        return [card for _, _, card in self.listing_rows(created)]

    def listing_rows(self, created: int) -> list[tuple[str, str, dict]]:
        """(backend id, upstream model, card) of each model /v1/models lists. An engine's own
        card names the engine in owned_by, not the backend, so a caller that needs the backend
        reads it here (SRV-741: a record's coverage)."""
        s = self.snapshot()
        out = []
        for pub in sorted(s["served"]):
            rows = s["served"][pub]
            bare = len(rows) == 1 and pub not in self.reserved
            for m in rows:
                b = s["backends"][m["backend"]]
                cls = KINDS.get(b["kind"], Generic)
                card = {"backend": b["id"], "kind": b["kind"], "label": b["label"],
                        "upstream_id": m["model"],
                        # the provider's own words from its model list, else the adapter's table
                        "name": m.get("name") or cls.names.get(m["model"]), "cloud": cls.cloud,
                        "context_length": m["context_length"],
                        # the operator's limit (Backends), else the provider's or the table's
                        "max_output": m.get("max_output_set") or default_max_output(cls, m),
                        "features": m["features"]}
                caps = capabilities(cls, m)
                if caps is not None:
                    card["capabilities"] = caps
                lid = pub if bare else f"{b['id']}/{pub}"
                own = self.upstream_card(b["id"], m["model"]) \
                    if self.upstream_card is not None and getattr(cls, "cards", False) else None
                if own is not None:
                    out.append((b["id"], m["model"], dict(own, id=lid)))
                    continue
                out.append((b["id"], m["model"], {"id": lid, "object": "model",
                                                  "created": created, "owned_by": b["id"],
                                                  "tandem": card}))
        return out

    def card(self, name: str, created: int) -> dict | None:
        for m in self.listing(created):
            if m["id"] == name:
                return m
        try:
            r = self.resolve(name, default=False)
        except NotFound:
            return None
        return {"id": name, "object": "model", "created": created, "owned_by": r.backend}

    # ----------------------------------------------------------------- the route
    def _row(self, s, bid: str, upstream_or_public: str):
        rows = s["by_backend"].get(bid) or {}
        if upstream_or_public in rows:
            return rows[upstream_or_public]
        for m in rows.values():
            if m["public_id"] == upstream_or_public:
                return m
        return None

    def _route(self, s, bid: str, m, asked: str, rule=None) -> Route:
        b = s["backends"][bid]
        upstream = m["model"] if m is not None else asked
        fb_list = (m["fallback"] if m is not None and m["fallback"] is not None
                   else b["fallback"])
        fbs = []
        for ref in fb_list or []:
            fb_bid, slash, fb_model = ref.partition("/")
            if fb_bid not in s["backends"] or fb_bid == bid:
                continue
            if not slash:
                row = self._row(s, fb_bid, m["public_id"] or m["model"] if m else asked)
                fb_model = row["model"] if row else upstream
            fbs.append((fb_bid, fb_model))
        return Route(bid, upstream, asked, fbs, m["price"] if m is not None else None, rule)

    def resolve(self, model: str, default: bool = True) -> Route:
        if not isinstance(model, str) or not model:
            raise NotFound("", "the request names no model")
        s = self.snapshot()
        bid, slash, rest = model.partition("/")
        if slash and bid in s["backends"]:
            m = self._row(s, bid, rest)
            if m is not None:
                return self._route(s, bid, m, model)
        for r in s["rules"]:
            if config_mod.glob(r["pattern"], model):
                m = self._row(s, r["backend"], model)
                return self._route(s, r["backend"], m, model, rule=r["id"])
        rows = s["served"].get(model) or []
        if model in self.reserved:
            raise NotFound(model, f"the model '{model}' is the engine's own")
        if len(rows) == 1:
            return self._route(s, rows[0]["backend"], rows[0], model)
        if len(rows) > 1:
            names = ", ".join(sorted(f"{m['backend']}/{model}" for m in rows))
            raise NotFound(model, f"the model '{model}' is served by {len(rows)} backends; "
                                  f"ask for one of {names}, or add a routing rule")
        dflt = getattr(self.cfg, "default_backend", None) if default else None
        if dflt in s["backends"]:
            return self._route(s, dflt, None, model)
        raise NotFound(model, f"The model '{model}' does not exist or no backend serves it"
                       + self._now_serves(s, model))

    def _now_serves(self, s: dict, model: str) -> str:
        """SRV-749: for an id a backend listed once and lists no more (its model list changed),
        or `<backend>/<id>` of a backend that does not serve the id: what that backend serves
        now, so a client pinned to the old id reads the new ones in its 404."""
        bid, slash, rest = model.partition("/")
        if not (slash and bid in s["backends"]):
            bid = next((m["backend"] for m in self.cfg.models()
                        if m["model"] == model and not m["present"]
                        and m["backend"] in s["backends"]), None)
        if bid is None:
            return ""
        ids = sorted((m["public_id"] or m["model"]) for m in s["by_backend"].get(bid, {}).values())
        label = s["backends"][bid]["label"] or bid
        if not ids:
            return f": {label} serves no model now"
        more = f" and {len(ids) - 12} more" if len(ids) > 12 else ""
        return f": {label} serves now {', '.join(ids[:12])}{more}"

    def backend_for(self, bid: str) -> dict | None:
        return self.snapshot()["backends"].get(bid)
