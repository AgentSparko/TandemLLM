"""The idle rule a restart waits for: no request runs or waits on the engine.

The Setup Guide's apply job (server/setup/apply.py `_wait_idle`) and `tandem remote restart --when
idle` (server/remote/lifecycle.py) read the same three counts of the engine's full /health answer.
Standard library only.
"""

from __future__ import annotations

#: before anything stops, the engine's requests finish: at most this long, asked this often
IDLE_WAIT_S = 600
IDLE_POLL_S = 2.0


def requests_in(health: dict | None) -> int:
    """The requests that run or wait on the engine, from its /health answer (`inflight`)."""
    inf = (health or {}).get("inflight") or {}
    return int(inf.get("running", 0)) + int(inf.get("waiting", 0)) + int(inf.get("lane", 0))
