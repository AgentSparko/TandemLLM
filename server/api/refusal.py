"""A request a server will not serve as asked: a 400 that names the field.

The engine's OpenAI layer (server/compat.py) and its thinking read (server/api/thinking.py) raise
the same class, so the engine's handler answers both alike (ENG-413: the engine's own copy).
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
