"""The engine API version a TandemLLM engine speaks, read from its /health (SRV-747).

The contract is the engine's, copied into contracts/engine-api-v1 at the engine commit its PIN names
(README.md, routes.json; its version is routes.json's `version`, equal to SPEAKS). The engine's
full /health answer, its setup-mode /health and its status responder's /health carry `engine_api`
(MAJOR.MINOR). An engine that does not send it (every engine up to 0.15.x) speaks 1.0. A MINOR step
only adds, so any 1.x is fine here (a 1.0 engine too: it lacks only the routes a later minor
added); another major is not, and the reason names the major this TandemApp needs. Nor is a value outside the plain form (a string of
ASCII digits, a dot, ASCII digits, nothing around it); its reason quotes what the engine sent.

    read(doc, kind)   {engine_api, assumed, ok, why} for kind tandemllm; None for any other kind

Readers: proxy/nodeengines.py (an engine view says `unhealthy` with health.api_why) and
proxy/deployments.py (such a member is `down`, takes no request, and a start fails at once).
Standard library only; nothing of the engine is imported.
"""

from __future__ import annotations

import re

from proxy import _version

#: the engine API version this TandemApp speaks (routes.json `version`)
SPEAKS = "1.4"
#: what an engine without the field speaks
ASSUMED = "1.0"
_FORM = re.compile(r"([0-9]+)\.([0-9]+)")


def read(doc, kind: str = "tandemllm") -> dict | None:
    """The engine API of one /health answer: `engine_api` (what the engine says, or ASSUMED when it
    says nothing), `assumed`, `ok` (same major as SPEAKS) and `why` (None when ok). None for an
    engine of another kind (vLLM, SGLang, generic), which has no such field."""
    if kind != "tandemllm":
        return None
    got = doc.get("engine_api") if isinstance(doc, dict) else None
    if got is None:
        return {"engine_api": ASSUMED, "assumed": True, "ok": True, "why": None}
    text = got if isinstance(got, str) else repr(got)
    m = _FORM.fullmatch(got) if isinstance(got, str) else None
    want = SPEAKS.split(".")[0]
    if m is not None and int(m.group(1)) == int(want):
        return {"engine_api": text, "assumed": False, "ok": True, "why": None}
    app = _version.release() or "this release"
    said = f"speaks engine API {text}" if m is not None else \
        f"sends engine_api {text!r}, not MAJOR.MINOR" if isinstance(got, str) else \
        f"sends engine_api {text}, not a MAJOR.MINOR string"
    return {"engine_api": text, "assumed": False, "ok": False,
            "why": f"this engine {said}; TandemApp {app} needs {want}.x"}
