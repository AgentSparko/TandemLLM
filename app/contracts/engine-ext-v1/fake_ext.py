"""The smallest engine-ext-v1 extension (ENG-429, QA ENG-430): every hook, no behaviour of its own.

Written against 1.0; its prompt takes 1.2's `info` (a default) and its supervisor has 1.2's
`check_answer`, so it fits the interface's signatures.

A host's tests load it (QSE_EXT=fake_ext with this folder on the path, or the module object) and
drive requests through it. `host.args` steers it, so one module covers the failure cases:

    fail: a hook or supervisor method name that raises (open per request; `start` is closed)
    bad:  a hook that returns a value of the wrong shape
    act:  {method: kind}: the supervisor acts once at that method (CONTINUE_HIDDEN at at_finish, ...)
    route: a prefix it claims (an engine path is refused at start)
    raw:  plan returns None (the request is served as a headless engine would)
    refuse: plan raises the host's Refusal

Standard library only; imports neither tree.
"""

from __future__ import annotations

EXT_API = "1.0"
NAME = "fake-ext"
VERSION = "0"


class _Supervisor:
    def __init__(self, ext):
        self.ext = ext
        self.acted: set = set()

    def _step(self, name: str):
        self.ext.calls.append(f"supervisor.{name}")
        if self.ext.args.get("fail") == name:
            raise RuntimeError(f"{name} failed on purpose")
        kind = (self.ext.args.get("act") or {}).get(name)
        if kind and name not in self.acted:
            self.acted.add(name)
            return {"kind": kind, "task": "T0", "text": None, "ids": [7], "visible": [],
                    "budget": None, "detail": {}}
        return None

    def at_request(self, event):
        act = self._step("at_request")
        return [act] if act else []

    def at_block(self, event, room):
        return self._step("at_block")

    def at_tool_call(self, event):
        return self._step("at_tool_call")

    def check_answer(self, items, final):
        return self._step("check_answer")

    def at_finish(self, event, room):
        return self._step("at_finish")

    def done(self, outcome, detail):
        self._step("done")


class FakeExt:
    def __init__(self, host):
        self.host = host
        self.args = dict(getattr(host, "args", {}) or {})
        self.calls: list[str] = []
        self.routes = [{"method": "GET", "prefix": self.args.get("route", "/v1/fake/"),
                        "handler": lambda request: (200, {}, b"{}")}]
        self.models = [{"id": "TandemLLM", "object": "model", "owned_by": NAME}]
        self.knobs = []

    def _hook(self, name: str, value):
        self.calls.append(name)
        if self.args.get("fail") == name:
            raise RuntimeError(f"{name} failed on purpose")
        if self.args.get("bad") == name:
            return object()
        return value

    def health(self):
        return self._hook("health", {"fake": True})

    def plan(self, request):
        if self.args.get("refuse"):
            from_host = getattr(self.host, "Refusal", None)
            if from_host is not None:
                raise from_host(400, "invalid_request_error", "refused on purpose")
        if self.args.get("raw"):
            return self._hook("plan", None)
        return self._hook("plan", {"active": True, "body": None})

    def prompt(self, plan, ids, info=None):
        return self._hook("prompt", list(ids))

    def supervisor(self, plan, event):
        return self._hook("supervisor", _Supervisor(self))

    def extras(self, plan, point, info):
        return self._hook("extras", [])

    def follow_ups(self, plan, outcome):
        return self._hook("follow_ups", [])

    def finish(self, plan, row):
        return self._hook("finish", None)

    def stop(self):
        self._hook("stop", None)


def start(host):
    if (getattr(host, "args", None) or {}).get("fail") == "start":
        raise RuntimeError("start failed on purpose")
    return FakeExt(host)
