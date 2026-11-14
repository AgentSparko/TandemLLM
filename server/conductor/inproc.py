"""The engine's side of the conductor that runs in its process (SRV-747): the narrator's slot and the
speed-guard report a release tree ships.

    start_narrator(state, gate)       the narrator for this server, or None (QWEN38_NARRATOR_MODEL)
    SHIPPED_GUARD                     <repo>/release/speed-guard.json (VIS-68)

ENG-413 WP5: the engine-only screens these handlers served through the conductor's
`/v1/conductor/*` are the engine's `/v1/engine/*` routes (server/engine_routes.py), handed to an
extension in process as `host.engine`, and the speed-guard report is `host.shipped_guard`
(engine-ext-v1 3.0); nothing goes through the server's state any more. The engine builds the
narrator's slot (its model, device, the residency planner, the journal) and the extension the
narrator over it (`narrator(slot)`, engine-ext-v1 3.0): the lines it writes are the extension's.
Nothing here imports TandemApp.
"""

from __future__ import annotations

import os

from engine.settings import SETTINGS as _S

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
#: VIS-68: the speed-guard report a release tree ships (tools/speed_guard.py ship)
SHIPPED_GUARD = os.path.join(_REPO, "release", "speed-guard.json")


def configured() -> str:
    """The narrator's model name (QWEN38_NARRATOR_MODEL), "" for none."""
    return (_S.get("NARRATOR_MODEL") or "").strip()


def start_narrator(state: dict, gate, *, fake: bool = False, log=print):
    """The narrator for this server, or None when `QWEN38_NARRATOR_MODEL` is empty or no extension
    builds one over the slot. Nothing loads here: the model loads when a profile's first narrated
    line asks for it."""
    name = configured()
    if not name:
        return None
    from engine import generative
    from engine.generative.slot import Slot
    from server import decider_api
    from server import ext as ext_mod
    from server.conductor import residency
    # N2: a Qwen3.5 model compares its tokenizer with the served checkpoint's, to read Qwen's ids
    engine_dir = getattr(getattr(state.get("engine"), "cfg", None), "path", None)
    model = generative.model(name, _S.get("NARRATOR_DIR") or None, allow_fake=fake,
                             engine_dir=engine_dir if isinstance(engine_dir, str) else None)
    device = None
    if not fake:
        from engine.decision.device import Device
        device = Device(_S.get("NARRATOR_DEVICE"))

    def journal(rec):
        jr = residency.journal_of(state)
        if jr is not None:
            jr.write(rec)

    slot = Slot(model, role="narrator", gate=gate,
                planner=decider_api.planner(state, fake=fake, role="narrator"), device=device,
                log=lambda s: log(s, flush=True),
                contract=decider_api._contract(state),
                journal=journal, mem_available=lambda: decider_api._mem_available_gib(fake))
    narr = ext_mod.of(state).ask("narrator", slot)
    if narr is None:
        log(f"[narrator] {name}: no extension writes narrated lines here; the narrator is off",
            flush=True)
        return None
    log(f"[narrator] {name} on {device or 'no device (fake)'}: loads through the residency "
        f"planner when ops/start.sh asks (v3: at the start, then kept loaded) or a profile's "
        f"first line asks", flush=True)
    return narr
