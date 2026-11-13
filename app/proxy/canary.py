"""The checks of a Replicas deployment (SRV-741): each member's identity, and an output canary.

    ids = Identities(log)                  one per Deployments (deps.identities)
    ids.read(member, serving)              {state, block, why} from what was read; a read starts
                                           in the background when due (never on a request's path)
    differs(ref, got)                      (field, ref's value, got's value) of the first identity
                                           field that differs (`max_len` left out), or None
    can = Canary(deps)                     one per Deployments (deps.canary)
    can.observe(did, members)              a member state changed: the canary is due now
    can.tick(now)                          the fleet's sampler: a due canary runs on a thread
    can.run(did)                           one canary round of a deployment, here and now
    can.verdict(did, backend)              {verdict, text, at} of a member, or None

IDENTITY. A member's engine says who it is at `GET /v1/replica/identity` (engine API 1.1,
server/identity.py), read through the node's TandemNode agent (`/node/engines/{id}/...`). The
first answer after the engine's start can be 503 `identity_pending` while the engine computes the
block: the read asks again every PENDING_POLL_S for at most PENDING_WAIT_S. A member is read when
it turns `serving`, when its engine restarted (its uptime went back), and every IDENTITY_TTL_S;
until the first block is in, the light check (release and weights name) is all there is. An engine
that says no engine API, or 1.0, has no such route: it is never asked, and its member shows
"identity not available" with the light check kept. The controller compares blocks; it reads no
field's meaning, so a new family needs nothing here.

THE CANARY. CANARY_PROMPTS, greedy (temperature 0), thinking off, CANARY_TOKENS tokens, with the
two most likely tokens' logprobs, to each serving member; the first serving member is the
reference. Due after any member state change and every CANARY_EVERY_S. The requests go to each
member's engine directly with the User-Agent `tandem-controller/canary` and `X-Tandem: raw`:
they never pass the gateway, so this controller's ledger has no row of them, and an engine's own
ledger files them as `internal` (tandemapp/ledger.py INTERNAL_UA), out of every total.

    same tokens                   pass
    another token at k            mismatch: the member takes no request until a later canary
                                  passes
    a near tie at k               each answer ranks the other's token within TIE_NATS (0.1) of its
                                  own: the prompt runs twice more on the reference (an A/A
                                  control). The reference writing the member's token at k after
                                  the same prefix in either run is a flip on one engine: the
                                  warning `near_tie`, the member keeps serving. No flip: mismatch.

BUSY RULE. A canary request goes to a member only while it has nothing in flight (the gateway's
own requests on it and its engine's count), checked before each of the prompts. A member that is
busy defers the whole round by CANARY_RETRY_S. So a user's request that arrives meanwhile waits at
most for one canary prompt (CANARY_TOKENS tokens of a short prompt), on an engine with one slot;
with more slots it waits for nothing. A round that cannot run keeps the last verdict.

Standard library only; nothing of the engine is imported.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

from proxy import nodesource

IDENTITY_PATH = "/v1/replica/identity"
CHAT_PATH = "/v1/chat/completions"
#: the engine API minor that added the identity route
IDENTITY_SINCE = (1, 1)
IDENTITY_TTL_S = 60.0
IDENTITY_TIMEOUT_S = 10.0
PENDING_WAIT_S = 120.0
PENDING_POLL_S = 1.0
CANARY_EVERY_S = 600.0
CANARY_RETRY_S = 30.0
CANARY_TIMEOUT_S = 60.0
CANARY_UA = "tandem-controller/canary"
CANARY_TOKENS = 32
CANARY_PROMPTS = (
    "Count from one to twenty in English words, separated by commas.",
    "List the planets of the solar system in order from the sun, one per line.",
    "Write the opening two sentences of a short story about a lighthouse keeper.")
#: two answers that part at a token each ranks within this many nats of its own choice
TIE_NATS = 0.1
#: the identity's fields named first when they differ; any other field after them, by name
ORDER = ("served_model", "version", "code", "family", "layers", "profile", "weights", "drafters",
         "rows", "tokenizer", "decode", "stage")
#: the one field two members may differ in: it limits which requests a member takes
WINDOW = "max_len"


def short(v) -> str:
    s = v if isinstance(v, str) else json.dumps(v, sort_keys=True)
    return s if len(s) <= 40 else s[:37] + "..."


def api_version(e: dict | None) -> tuple[int, int] | None:
    """The engine API (major, minor) an agent's engine view says, (1, 0) when it says nothing,
    None for an engine that is not TandemLLM's or a value not in the MAJOR.MINOR form."""
    h = (e or {}).get("health") if isinstance(e, dict) else None
    if not isinstance(h, dict) or e.get("kind", "tandemllm") != "tandemllm":
        return None
    got = h.get("engine_api")
    if got is None or h.get("api_assumed"):
        return (1, 0)
    try:
        a, b = str(got).split(".")
        return int(a), int(b)
    except ValueError:
        return None


def differs(ref: dict, got: dict):
    """(field, ref's value, got's value) of the first field of two identity blocks that differs,
    a dict field named to its key (`decode.temperature`); `max_len` never counts. None when equal."""
    keys = [k for k in ORDER if k in ref or k in got]
    keys += sorted(k for k in set(ref) | set(got) if k not in ORDER and k != WINDOW)
    for k in keys:
        a, b = ref.get(k), got.get(k)
        if a == b:
            continue
        if isinstance(a, dict) and isinstance(b, dict):
            for sub in sorted(set(a) | set(b)):
                if a.get(sub) != b.get(sub):
                    return f"{k}.{sub}", a.get(sub), b.get(sub)
        return k, a, b
    return None


class Identities:
    """Each member's identity block, read through its agent off the request path."""

    def __init__(self, log=lambda m: None, clock=time.monotonic):
        self.log, self.clock = log, clock
        self.sleep = time.sleep
        self._lock = threading.Lock()
        self._d: dict[tuple, dict] = {}
        self._threads: list[threading.Thread] = []

    def read(self, mv: dict, serving: bool) -> dict:
        """{state, block, why}: `state` is `read` (a block), `pending` (none yet), `failed` (the
        last read failed; `why`) or `not_available` (the engine has no route; `why`)."""
        e, n = mv.get("_e"), mv.get("_n")
        src = (n or {}).get("src")
        if not isinstance(src, nodesource.AgentNode) or not mv.get("engine"):
            return {"state": "not_available", "block": None,
                    "why": "identity not available: no TandemNode agent reads this engine"}
        ver = api_version(e)
        if ver is None or ver < IDENTITY_SINCE:
            said = "no engine API" if ver is None else f"engine API {ver[0]}.{ver[1]}"
            return {"state": "not_available", "block": None,
                    "why": f"identity not available: its engine says {said} (the route is 1.1)"}
        key = (mv["node"], mv["engine"])
        h = (e or {}).get("health") or {}
        up = h.get("uptime_s")
        with self._lock:
            ent = self._d.setdefault(key, {"block": None, "why": None, "at": None, "busy": False,
                                           "serving": False, "started": None})
            now = self.clock()
            started = now - up if isinstance(up, (int, float)) else None
            restarted = started is not None and ent["started"] is not None and \
                abs(started - ent["started"]) > 5.0
            if started is not None:
                ent["started"] = started
            due = serving and not ent["busy"] and (
                not ent["serving"] or restarted or ent["at"] is None or
                now - ent["at"] >= IDENTITY_TTL_S)
            ent["serving"] = serving
            if restarted:
                ent["block"] = None
            if due:
                ent["busy"] = True
                t = threading.Thread(target=self._fetch, args=(key, src, mv["engine"]),
                                     name="replica-identity", daemon=True)
                self._threads = [x for x in self._threads if x.is_alive()] + [t]
                t.start()
            out = dict(ent)
        if out["block"] is not None:
            return {"state": "read", "block": out["block"], "why": None}
        if out["why"]:
            return {"state": "failed", "block": None, "why": out["why"]}
        return {"state": "pending", "block": None, "why": "identity being read"}

    def settle(self, timeout: float = 30.0) -> None:
        """Wait for the reads that run (tests)."""
        end = time.monotonic() + timeout
        for t in list(self._threads):
            t.join(max(0.0, end - time.monotonic()))

    def _fetch(self, key: tuple, src, eid: str) -> None:
        block, why = None, None
        try:
            block, why = self.fetch(src, eid)
        except Exception as exc:                                     # noqa: BLE001
            why = f"{type(exc).__name__}: {exc}"[:160]
        with self._lock:
            ent = self._d[key]
            if block is not None and block != ent["block"]:
                self.log(f"[replica] {key[0]} {eid}: identity read")
            ent.update(block=block, why=why, at=self.clock(), busy=False)

    def fetch(self, src, eid: str) -> tuple[dict | None, str | None]:
        """(block, None) or (None, why): the route through the agent; 503 `identity_pending`
        asked again every PENDING_POLL_S, at most PENDING_WAIT_S."""
        end = self.clock() + PENDING_WAIT_S
        path = f"/node/engines/{eid}{IDENTITY_PATH}"
        while True:
            st, doc = src.client.json("GET", path, timeout=IDENTITY_TIMEOUT_S)
            if st == 200 and isinstance(doc, dict) and "identity" in doc:
                return doc, None
            err = (doc or {}).get("error") if isinstance(doc, dict) else None
            kind = (err or {}).get("type") or (err or {}).get("code")
            if st == 503 and kind == "identity_pending":
                if self.clock() >= end:
                    return None, f"identity still pending after {PENDING_WAIT_S:.0f} s"
                self.sleep(PENDING_POLL_S)
                continue
            if st == 404:
                return None, "identity not read: the route is not relayed or not served (404)"
            return None, f"identity not read: HTTP {st}" + \
                (f" {(err or {}).get('message')}" if (err or {}).get("message") else "")


# ---------------------------------------------------------------------------- the canary

def tokens_of(choice: dict) -> list | None:
    """A choice's logprobs as [(token, logprob, {alternative: logprob})], None without them."""
    rows = ((choice or {}).get("logprobs") or {}).get("content")
    if not isinstance(rows, list) or not rows:
        return None
    out = []
    for r in rows:
        if not isinstance(r, dict):
            return None
        alts = {a.get("token"): a.get("logprob") for a in r.get("top_logprobs") or []
                if isinstance(a, dict)}
        out.append((r.get("token"), r.get("logprob"), alts))
    return out


def compare(a: dict, b: dict) -> tuple[str, int | None, str]:
    """('same' | 'tie' | 'differ', the token where they part, words) of two answers: token by
    token when both carry logprobs, else by text."""
    ta, tb = a.get("tokens"), b.get("tokens")
    if ta is None or tb is None:
        if a["text"] == b["text"]:
            return "same", None, ""
        return "differ", None, f"{short(a['text'])} vs {short(b['text'])}"
    for k, (x, y) in enumerate(zip(ta, tb)):
        if x[0] == y[0]:
            continue
        ga = x[1] - x[2][y[0]] if y[0] in x[2] and x[1] is not None else None
        gb = y[1] - y[2][x[0]] if x[0] in y[2] and y[1] is not None else None
        words = f"token {k}: {x[0]!r} vs {y[0]!r}"
        if ga is not None and gb is not None and max(abs(ga), abs(gb)) <= TIE_NATS:
            return "tie", k, f"{words} ({abs(ga):.3f} and {abs(gb):.3f} nats)"
        return "differ", k, words
    if len(ta) != len(tb):
        return "differ", min(len(ta), len(tb)), f"{len(ta)} vs {len(tb)} tokens"
    return "same", None, ""


def flipped(runs: list[dict], got: dict, k: int) -> bool:
    """Whether one of the reference's runs wrote `got`'s token k after the same tokens."""
    want = [t[0] for t in (got.get("tokens") or [])[:k + 1]]
    for r in runs:
        have = [t[0] for t in (r.get("tokens") or [])[:k + 1]]
        if len(want) == k + 1 and have == want:
            return True
    return False


class Busy(Exception):
    pass


class Canary:
    def __init__(self, deps, clock=None):
        self.deps = deps
        self.clock = clock or deps.clock
        self._lock = threading.Lock()
        self._v: dict[tuple, dict] = {}         # (deployment, backend) -> verdict
        self._due: dict[str, float] = {}
        self._sig: dict[str, tuple] = {}
        self._note: dict[str, str] = {}         # a round that did not run, and why
        self._running: set[str] = set()

    # ------------------------------------------------------------------ when
    def observe(self, did: str, ms: list[dict]) -> None:
        sig = tuple((m["backend"], m["state"]) for m in ms)
        with self._lock:
            if self._sig.get(did) != sig:
                self._sig[did] = sig
                self._due[did] = 0.0

    def tick(self, now: float | None = None) -> None:
        now = self.clock() if now is None else now
        for rec in self.deps.records():
            did = rec["id"]
            if rec["type"] != "replicas" or rec["state"] == "stopped":
                continue
            with self._lock:
                if did in self._running or self._due.get(did, 0.0) > now:
                    continue
                self._running.add(did)
            threading.Thread(target=self._bg, args=(did,), name="replica-canary",
                             daemon=True).start()

    def _bg(self, did: str) -> None:
        try:
            self.run(did, claimed=True)
        except Exception as exc:                                     # noqa: BLE001
            self.deps.fleet.log(f"[replica] {did}: canary failed: {type(exc).__name__}: {exc}")
            with self._lock:
                self._due[did] = self.clock() + CANARY_RETRY_S

    def verdict(self, did: str, bid: str) -> dict | None:
        with self._lock:
            v = self._v.get((did, bid))
            return dict(v) if v else None

    def note(self, did: str) -> str | None:
        with self._lock:
            return self._note.get(did)

    # ------------------------------------------------------------------ one round
    def run(self, did: str, claimed: bool = False) -> dict:
        """One round: {ran, note, verdicts {backend: verdict}}."""
        if not claimed:
            with self._lock:
                if did in self._running:
                    return {"ran": False, "note": "a round runs already", "verdicts": {}}
                self._running.add(did)
        try:
            return self._round(did)
        finally:
            with self._lock:
                self._running.discard(did)

    def _later(self, did: str, why: str | None, every: float) -> dict:
        with self._lock:
            self._due[did] = self.clock() + every
            if why is None:
                self._note.pop(did, None)
            else:
                self._note[did] = why
        if why:
            self.deps.fleet.log(f"[replica] {did}: canary waits: {why}")
        return {"ran": why is None, "note": why, "verdicts": {}}

    def _round(self, did: str) -> dict:
        rec = self.deps.cfg.deployment(did)
        if rec is None or rec["type"] != "replicas" or rec["state"] == "stopped":
            return {"ran": False, "note": "not a serving Replicas", "verdicts": {}}
        ms = [m for m in self.deps.members(rec, canary=False) if m["state"] == "serving"]
        if len(ms) < 2:
            return self._later(did, None, CANARY_EVERY_S)
        ref = ms[0]
        try:
            base = self._ask_all(ref)
            out = {}
            for m in ms[1:]:
                got = self._ask_all(m)
                out[m["backend"]] = self._judge(ref, base, m, got)
        except Busy as exc:
            return self._later(did, str(exc), CANARY_RETRY_S)
        except _Failed as exc:
            return self._later(did, str(exc), CANARY_RETRY_S)
        now = round(self.clock(), 3)
        with self._lock:
            self._v[(did, ref["backend"])] = {"verdict": "pass", "text": "the reference",
                                              "at": now}
            for bid, (verdict, text) in out.items():
                old = self._v.get((did, bid))
                self._v[(did, bid)] = {"verdict": verdict, "text": text, "at": now}
                if old is None or old["verdict"] != verdict or verdict != "pass":
                    self.deps.fleet.log(f"[replica] {did} {bid}: canary {verdict}: {text}")
        self._later(did, None, CANARY_EVERY_S)
        return {"ran": True, "note": None,
                "verdicts": {b: v for b, (v, _t) in out.items()} | {ref["backend"]: "pass"}}

    def _judge(self, ref: dict, base: list, m: dict, got: list) -> tuple[str, str]:
        ties = []
        for i, (a, b) in enumerate(zip(base, got)):
            kind, k, words = compare(a, b)
            if kind == "same":
                continue
            if kind == "differ":
                return "mismatch", (f"canary {i + 1} differs from {ref['alias']} at {words}")
            runs = [self._ask(ref, CANARY_PROMPTS[i]) for _ in range(2)]
            if not flipped([a] + runs, b, k):
                return "mismatch", (f"canary {i + 1}: a near tie at {words}, but "
                                    f"{ref['alias']} writes its own token in two more runs")
            ties.append(f"canary {i + 1} at {words}; {ref['alias']} flips there itself")
        if ties:
            return "near_tie", "near tie: " + "; ".join(ties)
        return "pass", f"same tokens as {ref['alias']}"

    def _ask_all(self, m: dict) -> list[dict]:
        return [self._ask(m, p) for p in CANARY_PROMPTS]

    def _ask(self, m: dict, prompt: str) -> dict:
        """One canary prompt on a member that has nothing in flight: {text, tokens}."""
        n = self.deps._inflight(m)
        if n:
            raise Busy(f"{m['alias']} has {n} request{'s' if n != 1 else ''} in flight")
        b = self.deps.cfg.backend(m["backend"])
        if b is None:
            raise _Failed(f"{m['alias']}: no backend")
        body = {"model": m["model"], "messages": [{"role": "user", "content": prompt}],
                "max_tokens": CANARY_TOKENS, "temperature": 0, "top_p": 1, "stream": False,
                "chat_template_kwargs": {"enable_thinking": False},
                "logprobs": True, "top_logprobs": 2}
        h = {"Content-Type": "application/json", "User-Agent": CANARY_UA, "X-Tandem": "raw"}
        try:
            key = self.deps.cfg.key_for(m["backend"])
        except Exception:                                            # noqa: BLE001
            key = None
        if key:
            h["Authorization"] = f"Bearer {key}"
        req = urllib.request.Request(engine_url(b["url"], CHAT_PATH),
                                     data=json.dumps(body).encode(), headers=h, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=CANARY_TIMEOUT_S) as r:
                doc = json.loads(r.read() or b"null")
            ch = doc["choices"][0]
            return {"text": str((ch.get("message") or {}).get("content") or ""),
                    "tokens": tokens_of(ch)}
        except urllib.error.HTTPError as exc:
            raise _Failed(f"{m['alias']} answered HTTP {exc.code}") from None
        except (ValueError, KeyError, IndexError, TypeError):
            raise _Failed(f"{m['alias']}: the answer is not a chat completion") from None
        except Exception as exc:                                     # noqa: BLE001
            raise _Failed(f"{m['alias']}: {type(exc).__name__}: {exc}"[:160]) from None


class _Failed(Exception):
    pass


def engine_url(backend_url: str, path: str) -> str:
    """An engine route on a backend whose URL is the engine's `/v1` base."""
    base = backend_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return base + path
