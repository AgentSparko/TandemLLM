"""The loop escape's engine side (SRV-475; ENG-413 WP5b): the checkpoint's own sampling and the
sessions' escape counts.

`card_sampling` reads the served checkpoint's generation_config.json at the load: every sampled
request that names no filter runs with it (server/sampling_defaults.py), with or without an
extension, and /health `sampling.card` shows it. `Escapes` holds the sessions' escapes; the engine
makes it at its start and /health `sampling.loop_escape` reports it on every engine (the counts
stay 0 headless). The extension's `loop_escape` call steps it (engine-ext-v1 3.1 `state_keys`
`escapes`); the rule a step follows, the signal and the sampled request's seed are the
extension's. Copies of TandemApp's tandemapp/conductor/loopescape.py `card_sampling` and `Escapes`,
held to it by tests/test_api_import_guard.py.
"""

from __future__ import annotations

import collections
import json
import os
import threading


def card_sampling(model_dir: str | None) -> dict | None:
    """The checkpoint's own sampling (generation_config.json): {temperature, top_p, top_k,
    source}, or None when the file is missing, says `do_sample: false` or has no temperature."""
    if not model_dir:
        return None
    path = os.path.join(model_dir, "generation_config.json")
    try:
        with open(path) as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(cfg, dict) or cfg.get("do_sample") is False:
        return None
    try:
        t = float(cfg["temperature"])
        p = float(cfg.get("top_p", 1.0))
        k = int(cfg.get("top_k", 0) or 0)
    except (KeyError, TypeError, ValueError):
        return None
    if not 0.0 < t <= 2.0 or not 0.0 < p <= 1.0 or k < 0:
        return None
    return {"temperature": t, "top_p": p, "top_k": k, "source": path}


class Escapes:
    """Per session, the escape in progress: the turn it started and how many requests it has
    sampled. Bounded LRU; a session that left it is forgotten."""

    def __init__(self, sessions: int = 1024):
        self.max = int(sessions)
        self._d: collections.OrderedDict = collections.OrderedDict()
        self._lock = threading.Lock()
        self.counts = {"started": 0, "sampled": 0, "back": 0}

    def step(self, key: str, sig: dict | None, turn: int) -> dict | None:
        """This request's escape record: `state` on (with `since_turn`, `turn`, `nth`, the n-th
        sampled request of the streak, from 0) while `sig` holds, `back` (with `since_turn` and
        `back_at_turn`) on the first request after it; None otherwise."""
        with self._lock:
            cur = self._d.get(key)
            if sig is None:
                if cur is None:
                    return None
                del self._d[key]
                self.counts["back"] += 1
                return {"state": "back", "why": cur["why"], "name": cur["name"],
                        "since_turn": cur["since"], "back_at_turn": turn,
                        "sampled": cur["n"]}
            if cur is None:
                cur = {"since": turn, "n": 0, "why": sig["why"], "name": sig["name"]}
                self.counts["started"] += 1
            else:
                cur["why"], cur["name"] = sig["why"], sig["name"]
            self._d[key] = cur
            self._d.move_to_end(key)
            while len(self._d) > self.max:
                self._d.popitem(last=False)
            out = {"state": "on", "why": sig["why"], "name": sig["name"],
                   "count": sig["count"], "since_turn": cur["since"], "turn": turn,
                   "nth": cur["n"]}
            cur["n"] += 1
            self.counts["sampled"] += 1
            return out

    def report(self) -> dict:
        with self._lock:
            return {"sessions": len(self._d), **self.counts}
