"""Where chatd listens: QSE_CHAT_URL's host and port (ENG-413 WP2c).

`/health`'s `loaded.chatd` says whether that port takes a connection (server/mode.py). A copy of
TandemApp's tandemapp/chat_forward.py `target`; the forward itself (`/v1/chat-app*`) is TandemApp's.
"""

from __future__ import annotations

import os
import urllib.parse

def target(env=None) -> tuple[str, int]:
    env = os.environ if env is None else env
    u = urllib.parse.urlsplit(env.get("QSE_CHAT_URL") or "http://127.0.0.1:8016")
    return u.hostname or "127.0.0.1", u.port or 8016
