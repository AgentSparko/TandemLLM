"""The knobs the conductor and the proxy read (#108): the environment (prefix QWEN38_) with the
defaults below.

TandemApp never imports the engine (SRV-747). The engine's registry (engine/settings.py) reads the
same live environment with the same prefix, so in an engine that runs the conductor in its process
a name reads the same here as there: tests/test_knobs_standalone.py holds these defaults equal to
the registry's, and app/tests/test_knobs.py reads them without the engine. The shadow reader's and
the narrator's slot knobs (SHADOW_* but the deferred questions' SHADOW_ASK and SHADOW_ASK_IDLE_S,
NARRATOR_DEVICE, NARRATOR_DIR) are the engine's own, read from engine/settings.py.
"""

from __future__ import annotations

import os

#: name -> default, as engine/settings.py KNOBS has them: the conductor's and the proxy's reads
DEFAULTS: dict[str, str] = {
    "CLIENT_CWD": "note", "CONDUCTOR_FIT": "model", "FAIL_OPEN": "general", "MEMORY_ROUTES": "auto",
    "MODEL_NAME": "", "NARRATOR_MODEL": "", "NOTE_ANCHOR": "on", "NOTE_DEDUP": "parts",
    "NOTES_HEAD": "off", "NOTES_HEAD_ORDER": "memory", "SHADOW_ASK": "idle", "SHADOW_ASK_IDLE_S": "5",
    "PACKAGE_HOLD": "pick", "LIBRARY_HOLD": "message", "SESSION_GENERATIONS": "on",
    "STATUS_CLIENTS": "open-webui", "TANDEM_SCRATCH": "", "TANDEM_URL": "", "TOOL_FIT": "on",
}

_MISSING = object()


class StandaloneSettings:
    """`EngineSettings.get` over the names above, for a process without the engine."""

    prefix = "QWEN38_"

    def get(self, name: str, default=_MISSING):
        if default is _MISSING:
            if name not in DEFAULTS:
                raise KeyError(f"{self.prefix}{name} is not a knob the conductor or the proxy reads (tandemapp/knobs.py)")
            default = DEFAULTS[name]
        return os.environ.get(self.prefix + name, default)

    def is_set(self, name: str) -> bool:
        return (self.prefix + name) in os.environ


SETTINGS = StandaloneSettings()
