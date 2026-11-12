"""`GET /v1/replica/identity`: what two engines compare before they serve one model as replicas.

The block (version 1) names what decides an engine's answer to a prompt: the code, the model's
family and layer count, the weight profile, the weights and the drafters by content, the rows'
fingerprint as the disk tier keys them (server/diskcache.py), the tokenizer by the ids of a fixed
text, and the decode settings. Two engines whose blocks agree in every field but `max_len` give the
same tokens for the same request; `max_len` only limits which requests one of them takes.

    {"identity": 1, "served_model", "base_model", "version", "code", "family", "layers", "profile",
     "weights", "drafters", "rows", "tokenizer",
     "decode": {"tree", "sampled_tree", "reasoning_format", "thinking_default", "temperature",
                "top_p", "top_k", "parallel", "kv_fp8"},
     "max_len", "stage"}

The fixed part (code, family, layers, profile, weights, drafters, rows, tokenizer, kv_fp8) reads
files, so it is computed once, on a thread of its own that the first call starts after the load;
until it is ready the route answers 503 `identity_pending` with Retry-After. It takes no lock the
decode loop takes. The rest is read from the server's state on every call. A field with no source
in this server is null; a part that fails to compute says `error: <type>`, which never equals
another engine's digest.

This module imports nothing from the server's handler or from a proxy: the caller passes the state
and the two functions it reads the served name and the weight profile with.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION = 1

#: the tokenizer's fingerprint text: words, digits, accents, a reasoning tag and a call marker
PROBE = ("The 27 replicas agree: café, naïve, 12.5%, <think> x </think> "
         "<tool_call>{\"name\": \"f\"}</tool_call>")

#: the code digest's folders and file kinds: source only. Not ops/ (each box's own env files),
#: not caches or build outputs.
CODE_DIRS = ("engine", "server", "tools")
CODE_EXTS = (".py", ".c", ".cc", ".cpp", ".cu", ".cuh", ".h", ".hpp", ".json", ".yaml")
SKIP_DIRS = ("__pycache__", "build", "dist", "node_modules")

_START = threading.Lock()


def digest(value) -> str:
    """A short, stable hash of a JSON value: sha256 of its canonical form, 16 hex digits."""
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def code_digest(root: str = ROOT) -> str:
    """The engine's code by content: each source file under CODE_DIRS, by relative path and bytes."""
    h = hashlib.sha256()
    for d in CODE_DIRS:
        for dirpath, dirs, files in os.walk(os.path.join(root, d)):
            dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not x.startswith(".")
                             and not x.endswith(".egg-info"))
            for f in sorted(files):
                if not f.endswith(CODE_EXTS):
                    continue
                path = os.path.join(dirpath, f)
                h.update(os.path.relpath(path, root).replace(os.sep, "/").encode() + b"\0")
                with open(path, "rb") as fh:
                    h.update(fh.read() + b"\0")
    return h.hexdigest()[:16]


def _err(exc: BaseException) -> str:
    return f"error: {type(exc).__name__}"


def _model_config(model) -> dict | None:
    path = os.path.join(str(model), "config.json") if model else ""
    if not path or not os.path.isfile(path):
        return None
    with open(path) as f:
        return json.load(f)


def _family(args: dict) -> str:
    """The registry entry the loader chose for `--model` (as server/app.py `_arch_of` does)."""
    from engine.models import architecture, default_arch
    raw = _model_config(args.get("model"))
    return (architecture(raw) if raw is not None else default_arch()).name


def _layers(state: dict, args: dict):
    """The model's hidden layer count: the engine's config, else the checkpoint's."""
    n = getattr(getattr(state.get("engine"), "cfg", None), "num_hidden_layers", None)
    if n is None:
        raw = _model_config(args.get("model")) or {}
        n = (raw.get("text_config") or {}).get("num_hidden_layers", raw.get("num_hidden_layers"))
    return int(n) if isinstance(n, int) else None


def _tokenizer(state: dict) -> str:
    tok = state["tok"]
    try:
        size = len(tok)
    except TypeError:
        size = getattr(getattr(state.get("engine"), "cfg", None), "vocab_size", None)
    for attempt in range(3):
        try:
            ids = [int(x) for x in tok(PROBE, add_special_tokens=False).input_ids]
            return digest([size, ids])
        except Exception:                                          # noqa: BLE001
            if attempt == 2:
                raise
            time.sleep(0.05)
    raise AssertionError("unreachable")


def fixed_part(state: dict, profile, root: str = ROOT) -> dict:
    """What does not change while the process runs; reads files, so never on a request's path."""
    from server import diskcache
    args = dict(state.get("args") or {})
    given = dict(state.get("identity_given") or {})
    w = profile() or {}
    out: dict = {}

    def part(name, fn):
        if name in given:
            out[name] = given[name]
            return
        try:
            out[name] = fn()
        except Exception as exc:                                   # noqa: BLE001
            out[name] = _err(exc)

    extra: dict = {}

    def rows():
        nonlocal extra
        extra = diskcache.fingerprint_extra(argparse.Namespace(**args))
        return digest(extra)

    def weights():
        v = w.get("weights")
        if isinstance(v, str) and v.startswith(("/", "~")):
            return digest([diskcache.content_id(x) for x in v.split(",")])
        return digest(extra.get("files"))

    def drafters():
        paths = [diskcache.content_id(p) for p in (w.get("drafters"), w.get("draft_head"))
                 if isinstance(p, str) and p.startswith(("/", "~"))]
        return digest([paths, args.get("drafter")])

    def kv_fp8():
        kv = w.get("kv_cache")
        return None if kv is None else kv == "e4m3"

    part("code", lambda: code_digest(root))
    part("family", lambda: _family(args))
    part("layers", lambda: _layers(state, args))
    part("profile", lambda: w.get("name") or w.get("format"))
    part("rows", rows)
    part("weights", weights)
    part("drafters", drafters)
    part("tokenizer", lambda: _tokenizer(state))
    part("kv_fp8", kv_fp8)
    return out


def _compute(state: dict, profile, root: str) -> None:
    try:
        state["identity_fixed"] = fixed_part(state, profile, root)
    except Exception as exc:                                       # noqa: BLE001
        state["identity_fixed"] = {"error": _err(exc)}


def start(state: dict, profile, root: str = ROOT) -> None:
    """Start the fixed part's thread once; a no-op once it runs or is done."""
    with _START:
        if state.get("identity_fixed") is not None or state.get("identity_thread") is not None:
            return
        t = threading.Thread(target=_compute, args=(state, profile, root),
                             name="replica-identity", daemon=True)
        state["identity_thread"] = t
        t.start()


def block(state: dict, fixed: dict, served) -> dict:
    """The version 1 block from the fixed part and the state as it is now."""
    args = state.get("args") or {}
    out = {"identity": VERSION, "served_model": served(), "base_model": state.get("model"),
           "version": state.get("version", ""),
           **{k: fixed.get(k) for k in ("code", "family", "layers", "profile", "weights",
                                         "drafters", "rows", "tokenizer")},
           "decode": {"tree": bool(state.get("tree")), "sampled_tree": state.get("sampled_tree"),
                      "reasoning_format": state.get("reasoning_format"),
                      "thinking_default": state.get("thinking_default", "on"),
                      "temperature": state.get("temperature", 0.0),
                      "top_p": state.get("top_p", 1.0), "top_k": state.get("top_k", 0),
                      "parallel": int(args.get("parallel") or 1),
                      "kv_fp8": fixed.get("kv_fp8")},
           "max_len": state.get("max_len"), "stage": None}
    out.update(state.get("identity_override") or {})
    return out


def answer(state: dict, served, profile, root: str = ROOT) -> tuple[int, dict, tuple]:
    """The route's (status, body, headers): 200 and the block, or 503 `identity_pending` while the
    fixed part is computed (the first call starts it), or 500 `internal_error` when it failed."""
    fixed = state.get("identity_fixed")
    if fixed is None:
        start(state, profile, root)
        return 503, {"error": {"type": "identity_pending", "code": "identity_pending",
                               "message": "the engine's identity is being computed; retry"}}, \
            (("Retry-After", "1"),)
    if "error" in fixed:
        return 500, {"error": {"type": "internal_error",
                               "message": f"the engine's identity failed: {fixed['error']}"}}, ()
    return 200, block(state, fixed, served), ()
