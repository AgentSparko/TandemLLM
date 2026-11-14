"""The built-in residency and roles documents (ENG-413 WP2c), and the deciders one's models (WP5).

What a server without a conductor plans its small models' memory with: the roles (their kind,
window and contract) and the residency document (floor, margin, the order the roles load in and
each one's claim). With a conductor its stored documents count; without one these, version 0.
A copy of the conductor's seeds (TandemApp's tandemapp/conductor/schema.py `ROLES`, `RESIDENCY`,
`_role`, `TOKENIZER_NONE`; `seed()` answers its `schema.seed()` for these two kinds); the
decision models' planner (server/decider_api.py), the role memory (server/rolemem.py) and Wren's
planner (server/setup/wren.py) read them here.
"""

from __future__ import annotations

import copy

TOKENIZER_NONE = "none"


def _role(endpoint, model, contract, timeout_ms, mode, kind, window, preemptible=True,
          writes="nothing") -> dict:
    return {"endpoint": endpoint, "model": model, "contract": contract, "timeout_ms": timeout_ms,
            "memory_claim_gib": 0.0, "mode": mode, "kind": kind, "window": window,
            "preemptible": preemptible, "latency_budget_ms": timeout_ms, "fallback": "PASS",
            "tokenizer_sha256": TOKENIZER_NONE, "writes": writes, "sharing": ["text"]}


# `self` for a `jev` role: the decision models this engine serves (engine/decision, asked in
# process, SRV-131); the arbiter sidecar on :8010 is gone (OPS-45 re-scoped, 2026-10-01). Its
# memory claim is the residency document's. The classifier and the judge are Qwen itself through
# this server's port: their requests take the engine lock like any other, so their window is any.
ROLES: dict = {
    "decider": _role("self", "auto", "jev", 400, "on", "decider", "idle"),
    "classifier": _role("self", "qwen38-spark-engine", "openai-chat", 8000, "on", "reasoner",
                        "any", preemptible=False),
    "librarian": _role("", "", "rerank", 800, "off", "librarian", "any"),
    "reader": _role("", "", "openai-chat", 30000, "off", "clerk", "idle"),
    "judge": _role("self", "qwen38-spark-engine", "openai-chat", 30000, "off", "reasoner",
                   "any", preemptible=False),
    # P1: a small model inside the engine writes the state lines (server/conductor/narrator.py),
    # since N2 (2026-10-03) Qwen3.5-0.8B summing up each thought; a profile's `ui.narrator`
    # switches it on, this role's mode and budget (one slice between two rounds) say how.
    # 0.11.3 (SRV-534): off by default; it does not load, the keeper leaves it and the residency
    # plan leaves out its claim; `on` here and a profile's `ui.narrator` bring it back
    "narrator": _role("self", "qwen3.5-0.8b", "generate", 150, "off", "generative",
                      "between_rounds", writes="state_lines"),
    # L1 (2026-10-03): the test judge's Laya (server/conductor/judge.py), a role of its own, so
    # the judge answers whatever the router's decider stage does (routing is Qwen's since router
    # v7, and the J1 judge asked through the `decider` role); a profile's `outcomes.judge`
    # switches the questions, this role's mode says whether Laya answers them
    "tester": _role("self", "auto", "jev", 100, "on", "decider", "idle"),
}


# OPS-41: what may load beside the engine. A role loads when MemAvailable minus its claim stays at
# or above floor + margin (15 + 6 GiB: the 2026-09-11 wedge and the decider design note), one at a
# time in `order`, never during a hold. A claim is the MemAvailable the role took beside the
# engine, not what nvidia-smi shows; a role that grows (arbiter's batcher at 64 rows) claims its
# peak. The decider's 8.2 GiB was the arbiter sidecar's (31.0 -> 22.8 GiB, 2026-10-01); Laya inside
# the engine claims 3.0 (OPS-69, the reference box's residency v2 since 2026-10-06). With 8.2 a new
# box refused Laya on Super VL at 3 slots: 43.75 GiB available less the engine's 17.46 GiB growth
# to its peak leaves 26.29, less 8.2 is 18.09 under 21; less 3.0 is 23.29 (2026-10-17)
RESIDENCY: dict = {
    "floor_gib": 15.0, "margin_gib": 6.0, "settle_s": 5,
    "order": ["decider", "narrator", "librarian", "reader"],
    "claims": {
        "decider": {"gib": 3.0, "basis": "measured",
                    "note": "Laya's three checkpoints in the engine's process, measured beside a "
                            "side slot (OPS-69, 2026-10-06); the arbiter sidecar took 8.2"},
        "narrator": {"gib": 6.0, "basis": "declared",
                     "note": "the narrator's model in BF16: Qwen3.5-0.8B (2.5 GiB declared) or "
                             "2B (5.5), its 4,096-row cache, the step's workspace and graph, the "
                             "pool's slack (MiniCPM5-2B, P1, 6); loads only when a profile with "
                             "ui.narrator on or thoughts asks for a line"},
        "reader": {"gib": 12.0, "basis": "peak",
                   "note": "a Nemotron Nano 9B in FP8, declared peak 10 to 12 GB; not resident "
                           "until RND-21 earns it"},
    },
}


# ENG-413 WP5: the decision models' load policy and each question's model, which a server without
# the extension's `deciders` document loads by (schema.py `DECIDERS` without its `api`, the
# extension's part: `deciders_api`)
DECIDERS: dict = {
    "models": [{"name": "laya-english", "load": "on_start"},
               {"name": "laya-typed-decisions", "load": "on_demand"},
               {"name": "laya-multilingual", "load": "on_demand"}],
    "questions": {"classify": "auto", "packages": "auto", "judge": "auto", "laya_api": "auto"},
}


def seed(kind: str) -> dict | None:
    """The built-in version 0 of the `residency`, `roles` or (since ENG-413 WP5) `deciders`
    document, a fresh copy; None else."""
    src = {"roles": ROLES, "residency": RESIDENCY, "deciders": DECIDERS}.get(kind)
    return copy.deepcopy(src) if src is not None else None
