"""Conversation affinity for a Replicas deployment (SRV-467, SRV-731): which member serves a
conversation's turn (proxy/deployments.py places it).

Each engine keeps a conversation's caches (resident rows, park, spill, disk tier) on its own box, so
every turn of a conversation should land on the member that served the turn before. Nothing here is
load-bearing for the answer: a turn on another member is a cache miss, never a wrong answer.

THE KEY is the engine's own (server/app.py: `conversation_id`, then `_session_header`): the body's
`conversation_id`, `session_id` or `user`, its `metadata.conversation_id` or `session_id`, the
`X-Conversation-Id` header, then `x-session-id`, then Open WebUI's `X-OpenWebUI-Chat-Id` as
`c:<id>`; at most 128 characters. tests/test_proxy_affinity.py pins the order against the engine's
source. Under `root` (the default) a worker's key `chat-7-task-2` is its chat's `chat-7`, so a chat
and its workers share the member that holds the chat's prefix (chatd/render.py); `own` keeps them
apart.

THE CHOICE. A key the sticky table holds goes to its member while that member is routable. A miss
goes by rendezvous hash over the routable members: each member scores sha256(member, key) and the
highest wins, so keys spread evenly, a member that leaves moves only its own keys and gets them back
when it returns, and no tie-break sends every miss to one member. A request without a key goes to
the member with the fewest requests in flight.

THE TABLE is a hint that is corrected: the member that served a turn (a fallback included) is
written; an entry whose member answered with no cache tier (the conversation was evicted there) is
dropped, so the hash decides the next turn. Entries live 30 minutes past their last turn, at most
STICKY_MAX of them (the oldest go first).
"""

from __future__ import annotations

import collections
import hashlib
import re
import threading
import time

KEY_MAX = 128
STICKY_TTL_S = 1800.0
STICKY_MAX = 20000
_TASK = re.compile(r"-task-\d+$")


def _hdr(headers, name: str):
    try:
        v = headers.get(name)
    except Exception:                                              # noqa: BLE001
        return None
    return v if isinstance(v, str) and v else None


def conversation_key(body, headers) -> str | None:
    """The conversation's key, in the engine's order; None when the request names none."""
    if isinstance(body, dict):
        for k in ("conversation_id", "session_id", "user"):
            v = body.get(k)
            if isinstance(v, str) and v:
                return v[:KEY_MAX]
        meta = body.get("metadata")
        if isinstance(meta, dict):
            v = meta.get("conversation_id") or meta.get("session_id")
            if isinstance(v, str) and v:
                return v[:KEY_MAX]
    if headers is None:
        return None
    v = _hdr(headers, "X-Conversation-Id")
    if v:
        return v[:KEY_MAX]
    v = _hdr(headers, "x-session-id") or _hdr(headers, "X-Session-Id")
    if not v:
        w = _hdr(headers, "X-OpenWebUI-Chat-Id")
        v = "c:" + w if w else None
    return v[:KEY_MAX] if v else None


def policy_key(key: str | None, affinity: str = "root") -> str | None:
    """The key a placement uses: under `root` a worker's key is its chat's."""
    if key is None or affinity == "own":
        return key
    return _TASK.sub("", key) or key


def score(member: str, key: str) -> int:
    return int.from_bytes(hashlib.sha256(f"{member}\0{key}".encode()).digest()[:8], "big")


def ranked(key: str, members) -> list[str]:
    """The members in rendezvous order for `key`: the first is the key's home."""
    return sorted(members, key=lambda m: (score(m, key), m), reverse=True)


def rendezvous(key: str, members) -> str:
    return ranked(key, members)[0]


class Sticky:
    """`{key: member}` with each entry's last turn; thread safe."""

    def __init__(self, ttl: float = STICKY_TTL_S, cap: int = STICKY_MAX, clock=time.time):
        self.ttl, self.cap, self.clock = ttl, cap, clock
        self._d: collections.OrderedDict = collections.OrderedDict()   # key -> [member, at, turns]
        self._lock = threading.Lock()

    def get(self, key: str) -> str | None:
        with self._lock:
            e = self._d.get(key)
            if e is None:
                return None
            if self.clock() - e[1] > self.ttl:
                del self._d[key]
                return None
            return e[0]

    def turns(self, key: str) -> int:
        with self._lock:
            e = self._d.get(key)
            return e[2] if e is not None else 0

    def put(self, key: str, member: str) -> None:
        now = self.clock()
        with self._lock:
            e = self._d.pop(key, None)
            turns = e[2] + 1 if e is not None and e[0] == member else 1
            self._d[key] = [member, now, turns]
            while len(self._d) > self.cap:
                self._d.popitem(last=False)

    def drop(self, key: str, member: str | None = None) -> bool:
        with self._lock:
            e = self._d.get(key)
            if e is None or (member is not None and e[0] != member):
                return False
            del self._d[key]
            return True

    def counts(self) -> dict[str, int]:
        """Live entries per member."""
        now = self.clock()
        out: dict[str, int] = {}
        with self._lock:
            for m, at, _ in self._d.values():
                if now - at <= self.ttl:
                    out[m] = out.get(m, 0) + 1
        return out

    def __len__(self) -> int:
        return sum(self.counts().values())
