"""Whether a directory lies inside a git work tree (ENG-413 WP2c).

The disk cache's tier (server/diskcache.py) and the probe's snapshots (server/probe.py) refuse a
directory inside a repository: what they write is never committed by accident. A copy of
TandemApp's tandemapp/conductor/transcripts.py `in_repository`.
"""

from __future__ import annotations

import os

def in_repository(path: str) -> str | None:
    """The git work tree `path` is inside, if any."""
    p = os.path.realpath(path)
    while True:
        if os.path.exists(os.path.join(p, ".git")):
            return p
        up = os.path.dirname(p)
        if up == p:
            return None
        p = up
