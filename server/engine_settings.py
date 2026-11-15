"""`GET` and `PATCH /v1/engine/settings`: the settings a running engine changes without a restart
(engine API 1.9, group 9; ENG-456).

The keys are the managed overlay's (server/remote/overlay.py, engine-ctl-v1) whose restart class is
`hot`: overlay.HOT. A key is hot only when the server itself applies a new value live; every other
serve.env knob is read once, at the start, into the engine's arguments and objects, and changes
through `tandem remote settings apply` and a restart. Today one key is hot: QSE_ALLOW_FROM, the
allow-from list of server/api/access.py.

    GET    the hot keys: the value the server runs with now (`live`), the value the configuration
           gives (`value`, `source`: the layer that sets it), type; the overlay's versions
    PATCH  {"set": {KEY: VALUE}, "unset": [KEY], "plan": false, "base": N}: a new overlay version
           with these changes, made current at once, and each value applied live. The checks are
           `tandem remote settings plan`'s (the key's type, the value's characters, serve.env must
           load with it); a key that is not hot is refused (409 restart_needed: apply it over SSH,
           which restarts). "plan": true answers the plan and writes nothing; "base" refuses a
           change when the overlay's current version is another one (409 base_moved). A new
           allow-from list that would shut out the caller's own address is refused (409
           locks_out). While a lifecycle job with an overlay change waits or acts, 409 busy.

Every PATCH is a line of run/remote/journal.jsonl, as an SSH call is (`via` http, `from` the peer,
the changes with secret-looking values cut). Access: GET follows the dashboard's rule; PATCH needs a
trusted-local caller, the engine's HTTP token or the admin token (with neither file nor token, only
trusted-local), and a JSON body that is not cross-site (server/api/auth.py same_origin_json).
"""

from __future__ import annotations

import time

from server.api import access, auth as auth_mod
from server.remote import grammar, journal, lifecycle, overlay
from server.remote.grammar import Refusal
from server.remote.paths import Tree

USAGE_CODES = ("bad_command", "unknown_option", "bad_value", "unknown_key", "missing_argument",
               "duplicate_option")


def _apply_live(state, key: str, value: str) -> None:
    """One hot key's new value into the running server."""
    if key == access.ALLOW_ENV:
        gate = state.get("gate")
        if gate is not None:
            gate.set_allow(value)


def _live(state, key: str):
    if key == access.ALLOW_ENV:
        gate = state.get("gate")
        return gate.allow_text if gate is not None else None
    return None


def get(state, tree: Tree) -> dict:
    hot = list(overlay.HOT)
    body = overlay.settings(tree, hot) if hot else {"keys": [], "overlay": overlay.manifest_brief(tree),
                                                     "hook": overlay.hook_present(tree)}
    keys = []
    for r in body["keys"]:
        keys.append({"key": r["key"], "live": _live(state, r["key"]), "value": r["value"], "set": r["set"],
                     "source": r["source"], "type": r["type"]})
    return {"hot": hot, "keys": keys, "overlay": body["overlay"], "hook": body["hook"]}


def _error(handler, status: int, code: str, message: str) -> None:
    handler._json(status, {"error": {"type": code, "message": message}},
                  extra_headers=(("Cache-Control", "no-store"),))


def _status(ref: Refusal) -> int:
    return 400 if ref.code in USAGE_CODES else 409


def patch(state, tree: Tree, body: dict, peer: str) -> tuple[int, dict]:
    """(status, answer) of one PATCH; the journal line is the caller's."""
    if not isinstance(body, dict) or set(body) - {"set", "unset", "plan", "base"}:
        raise Refusal("bad_value", "the body is {set: {KEY: VALUE}, unset: [KEY], plan, base}")
    sets, unsets = body.get("set") or {}, body.get("unset") or []
    if not isinstance(sets, dict) or not isinstance(unsets, list) or \
            not all(isinstance(k, str) and isinstance(v, str) for k, v in sets.items()) or \
            not all(isinstance(k, str) for k in unsets):
        raise Refusal("bad_value", "set maps keys to strings, unset lists keys")
    if not sets and not unsets:
        raise Refusal("missing_argument", "nothing to change: set or unset a key")
    for k in list(sets) + unsets:
        if not grammar.KEY_RE.match(k):
            raise Refusal("bad_value", f"{k[:40]!r} is not a settings key")
    if set(sets) & set(unsets):
        raise Refusal("duplicate_option", "a key is both set and unset")
    sch = overlay.schema(tree)
    for k in list(sets) + unsets:
        if k not in sch:
            raise Refusal("unknown_key", f"not a settings key: {k}")
        if sch[k]["restart"] != "hot":
            raise Refusal("restart_needed", f"{k} is read at the start: change it with tandem remote settings "
                                            f"apply, which restarts the engine (hot keys: "
                                            f"{', '.join(overlay.HOT) or 'none'})")
    with lifecycle._lock(tree):
        m = overlay.manifest(tree)
        if body.get("base") is not None and body["base"] != m["current"]:
            raise Refusal("base_moved", f"the overlay is at version {m['current']}, not {body['base']}")
        cur = lifecycle.latest(tree)
        if cur and cur["state"] not in lifecycle.FINAL and cur.get("overlay"):
            raise Refusal("busy", "a lifecycle job with an overlay change waits or acts: tandem remote status "
                                  "shows it")
        base = overlay.values_of(tree, m["current"])
        new = overlay.target_of(tree, sorted(sets.items()), unsets, base)
        p = overlay.plan(tree, new)
        live = {e["key"]: (e["to"] if e["is_set"] else "") for e in p["effect"] if e["key"] in overlay.HOT}
        if access.ALLOW_ENV in live:
            nets = access.parse_allow(live[access.ALLOW_ENV])
            if not access.allowed(peer, nets):
                raise Refusal("locks_out", f"{access.ALLOW_ENV}={live[access.ALLOW_ENV]} would shut out this "
                                           f"caller's own address ({peer})")
        if body.get("plan"):
            return 200, {"plan": p, "applied": False}
        n = overlay.add_version(tree, new, "settings patch (hot)")
        overlay.make_current(tree, n)
        overlay.mark_good(tree)
    for k, v in live.items():
        _apply_live(state, k, v)
    return 200, {"plan": p, "applied": True, "version": n, "live": {k: _live(state, k) for k in overlay.HOT}}


def serve(handler, state, method: str, body) -> None:
    """One /v1/engine/settings request on the engine's HTTP handler."""
    tree = Tree(state.get("tree_root"))           # the server's own tree; a test names a sandbox
    a = handler._auth_policy()
    if method == "GET":
        if not handler._allowed("dashboard"):
            return None
        try:
            return handler._json(200, get(state, tree), extra_headers=(("Cache-Control", "no-store"),))
        except Refusal as ref:
            return _error(handler, _status(ref), ref.code, str(ref))
    peer = handler._peer()
    gate = state.get("gate")
    if not (a.trusted_local(peer, handler.headers) or a.admin_any(handler.headers)
            or (gate is not None and gate.token_ok(handler.headers, a))):
        return handler._json(401, {"error": {"type": "unauthorized",
                                             "message": "a change of the engine's settings needs this host, "
                                                        "the engine's HTTP token or the admin token"}},
                             extra_headers=(("WWW-Authenticate", 'Bearer realm="TandemLLM"'),))
    bad = auth_mod.same_origin_json(handler.headers)
    if bad:
        return _error(handler, bad[0], bad[1], bad[2])
    t0 = time.monotonic()
    row = {"at": journal.utc(), "verb": "settings patch", "via": "http", "from": peer[:64]}
    if isinstance(body, dict):
        row["args"] = journal.redact_args([f"{k}={v}" for k, v in sorted((body.get("set") or {}).items())
                                           if isinstance(k, str) and isinstance(v, str)]
                                          + [f"--unset={k}" for k in (body.get("unset") or [])
                                             if isinstance(k, str)] + (["--plan"] if body.get("plan") else []))
    rc = grammar.EXIT_OK
    try:
        status, out = patch(state, tree, body, peer)
        if out.get("applied"):
            row["version"] = out["version"]
        return handler._json(status, out, extra_headers=(("Cache-Control", "no-store"),))
    except Refusal as ref:
        rc = ref.exit_code
        row["error"] = ref.code
        return _error(handler, _status(ref), ref.code, str(ref))
    except Exception as exc:                                       # noqa: BLE001
        rc = grammar.EXIT_INTERNAL
        row["error"] = "internal"
        return _error(handler, 500, "internal", f"{type(exc).__name__}: {str(exc)[:300]}")
    finally:
        row["exit"] = rc
        row["ms"] = int((time.monotonic() - t0) * 1000)
        journal.write(tree.journal, row)
