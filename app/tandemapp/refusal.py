"""A request a server will not serve as asked: a 400 that names the field.

The engine's OpenAI layer (server/compat.py) and the thinking read the conductor's router shares
with it (tandemapp/thinking.py) raise the same class, so the engine's handler answers both alike.
It lived in server/compat.py; the engine imports it from here until ENG-413 (SRV-747).
"""

from __future__ import annotations


class Refusal(ValueError):
    """A request the engine will not serve as asked: a 400 that names the field."""

    def __init__(self, param: str, message: str):
        super().__init__(message)
        self.param = param

    def body(self) -> dict:
        return {"error": {"message": str(self), "type": "invalid_request_error",
                          "param": self.param}}
