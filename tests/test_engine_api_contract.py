"""The engine API contract v1, the engine's side (SRV-748; 1.3: ENG-427, QA ENG-428):
contracts/engine-api-v1, which the engine owns, against the engine's code. TandemApp's side is its
tests/test_engine_api_contract.py against its pinned copy; the live engine is
tests/test_engine_api_live.py; the tool-call fixtures are tests/test_contract_toolcalls.py.

  1  every path of routes.json's routes and app_routes_via_engine is one the engine dispatches
     (server/app.py, server/engine_routes.py, server/api/session_tokens.py, server/setup_api.py,
     ops/engine_status.py for the status responder); group 9 has an engine_routes handler each;
     no planned route is dispatched yet                          test_every_route_is_dispatched
  2  server/engine_api.py VERSION, routes.json's version and ops/engine_status.py's literal are one
     version, and both /health answers carry it                  test_one_version
  3  (retired with reverse-deps.json at ENG-413 WP14: the no-app guard is tests/test_app_hook.py)
  4  the values are the contract's (1.3; TandemApp holds its copies to the same files):
     server/setup_api.py and server/api/auth.py same_origin and same_origin_json answer
     auth.json's cases, server/cache_peek.py and server/api/usage.py TIER_OF are cache.json's;
     1.5: no off_why copy; the engine's own off texts     test_values_are_the_contracts
  5  setup/: estimate-cases.json is models/estimate-cases.json byte for byte, limits.json's
     engine_max_slots is the registry's ENGINE_MAX_SLOTS          test_setup_fixtures
  6  node/: ops/serve.env carries PORT= and QSE_STATUS_PORT= as serve.env.example does and
     install.sh writes the status port as PORT + 9; the bin/tandem install.sh writes takes the
     control.json verbs and install.sh its flags; ops/fetch_weights.verify accepts a folder laid
     out as hub_manifest.example.json says and refuses a changed byte; control.json's stage keys
     (1.2) are the ones ops/start.sh and ops/engines.sh read, and its status line is bin/tandem's
                                                                  test_node_fixtures
  7  (the copies both trees keep equal are checked by the pair test tests/test_pair_copies.py
     since ENG-413 WP14)
  8  every schema and example here validates (dashboard/, decision/, health, setup); a reference
     leaves the folder only to TandemApp's conductor-v1 and fleet-v1, and only from the lists that
     name TandemApp's routes; routes.json's after_eng413 marks are `extension` but the sign-in's
     and the leaves mark is well formed                                          test_schemas_and_references
  9  metrics-families.json names every family of the engine's /metrics registry with its type and
     labels, the conductor's as owner ext; metrics.md has a row for each and no other
                                                                  test_metrics_families
 10  1.3 is additive (G4): every route of compat/1.2/routes.json is here with its method and
     aliases; the 1.2 examples of compat/1.2/ (a 1.0, a 1.1 and a 1.2 engine's /health, both
     stages, the split request and block) validate against the 1.3 schemas, and the 1.3
     examples equal them but for engine_api                     test_additive_1_3
 11  1.4 is additive (ENG-413 WP3a): every 1.3 route is here; what 1.3 planned and the extension
     host serves left `planned` (`answers`: 404 no_ui and not_on_engine; /health extensions in
     the schema, optional); the 1.3 examples still validate and the 1.4 ones (compat/1.4/) equal
     them but for engine_api and extensions; qse_ext_errors_total is an engine family
                                                                  test_additive_1_4
 12  1.5 is additive (ENG-413 WP5): every 1.4 route is here with its aliases; group 9 is the
     engine's screens, each GET live, each with a schema in engine/ or a doc, each replacing a
     screen whose old path is marked to leave with WP14; nothing of group 9 is planned any more;
     the 1.4 examples validate and the 1.5 ones (compat/1.5/) equal them but for engine_api and
     ext_api                                                      test_additive_1_5
 13  1.6 is additive (ENG-413 WP6): every 1.5 route is here unchanged; the 1.5 examples validate
     and the 1.6 ones equal them but for engine_api; node/control.json `chat` names QSE_CHATD's
     values, and ops/start.sh, ops/watchdog.sh, serve.env's mode block and the bin/tandem
     install.sh writes each handle node (the behaviour: tests/test_chatd_lifecycle.py)
                                                                  test_additive_1_6
 14  1.7 is additive (ENG-413 WP7): every 1.6 route is here unchanged; the 1.6 examples validate
     and the 1.7 ones equal them but for engine_api; node/control.json `chat` says the engine runs
     no chatd (node or off, on read as node, default off) and names the install's flags; the
     launchers have no chatd launcher and serve.env reads on as node, unset as off (the behaviour:
     tests/test_chatd_lifecycle.py, tests/test_install_headless.py)
                                                                  test_additive_1_7
 15  1.8 (ENG-413 WP14): every 1.7 route is here unchanged; the 1.7 examples validate and the 1.8
     ones equal them but for engine_api; the Chat tab forward 1.7 planned is an answer, the
     sign-in stays planned; the other leaving app routes and the screens' old paths are out;
     dashboard/ has session, error and gap; server/app.py imports no TandemApp module and has no
     --dashboard-dir or --host-collector flag                    test_additive_1_8

CPU only, no torch, standard library only. Run: python tests/test_engine_api_contract.py
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
CONTRACT = os.path.join(ROOT, "contracts", "engine-api-v1")


def _routes() -> dict:
    with open(os.path.join(CONTRACT, "routes.json")) as f:
        return json.load(f)


# ---------------------------------------------------------------------------- dispatch

def _constants(path: str) -> set:
    with open(os.path.join(ROOT, path)) as f:
        tree = ast.parse(f.read())
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            out.add(n.value)
    return out


def _sample(pattern: str) -> str:
    return re.sub(r"\{[^}]+\}", "x1", pattern).replace("*", "x1")


def _dispatched(path: str, consts: set) -> bool:
    s = _sample(path)
    for c in consts:
        if not c.startswith("/") or len(c) < 2:
            continue
        if c == s or c.rstrip("/") == s.rstrip("/") or (c.endswith("/") and s.startswith(c)):
            return True
    return False


def _setup_parts_known(path: str, consts: set) -> list[str]:
    """The literal parts after /v1/setup/ that the dispatcher names nowhere."""
    rest = path[len("/v1/setup/"):]
    return [p for p in rest.split("/") if p and not p.startswith("{") and p not in consts]


def test_every_route_is_dispatched():
    doc = _routes()
    engine = _constants("server/app.py") | _constants("server/engine_routes.py")
    setup = _constants("server/setup_api.py")
    status = _constants("ops/engine_status.py")
    tokens = _constants("server/api/session_tokens.py")      # the engine strips /s/<token>
    misses = []
    for r in doc["routes"]:
        if r.get("relay_only"):                    # 1.8: only ever relayed, answered 404 here
            continue
        for p in [r["path"]] + r["aliases"]:
            if r.get("port") == "QSE_STATUS_PORT":
                ok = _dispatched(p, status) and (not p.startswith("/v1/setup/") or
                                                 not _setup_parts_known(p, status | setup))
            else:
                ok = _dispatched(p, engine)
                if ok and p.startswith("/v1/setup/"):
                    ok = not _setup_parts_known(p, setup)
            if not ok:
                misses.append(f"{r['method']} {p} (group {r['group']})")
    for r in doc["app_routes_via_engine"]:
        p = r["path"].replace("{token}", "x1")
        ok = _dispatched(p, engine | tokens)
        if not ok and p.startswith("/s/"):
            ok = "/s/" in tokens
        if not ok and p.endswith("*"):
            ok = _dispatched(p.rstrip("*").rstrip("/"), engine | tokens)
        if not ok:
            misses.append(f"app route {r['path']}")
    from server import engine_routes
    # 1.5: group 9 is the engine's own (server/engine_routes.py's table)
    for r in doc["routes"]:
        if r["group"] == 9:
            parts = r["path"][len(engine_routes.PREFIX):].replace("{name}", "a").split("/")
            if engine_routes._route(r["method"], parts) is None:
                misses.append(f"group 9 {r['method']} {r['path']}: not in engine_routes")
    # a planned route moves into `routes` in the minor of the engine that serves it
    early = [p["path"] for p in doc["planned"] if p["kind"] == "route" and _dispatched(p["path"], engine)]
    assert not early, f"served but still planned (move them into routes): {early}"
    assert not misses, misses
    n = sum(1 + len(r["aliases"]) for r in doc["routes"])
    return f"{n} paths of {len(doc['routes'])} routes, {len(doc['app_routes_via_engine'])} " \
           f"extension routes"


def _literal(path: str, name: str):
    with open(os.path.join(ROOT, path)) as f:
        tree = ast.parse(f.read())
    for n in tree.body:
        if isinstance(n, ast.Assign) and any(getattr(t, "id", None) == name for t in n.targets):
            return ast.literal_eval(n.value)
    raise AssertionError(f"{path} has no {name}")


def _mm(v: str) -> tuple[int, int]:
    assert re.fullmatch(r"\d+\.\d+", v), v
    return tuple(int(x) for x in v.split("."))


def test_one_version():
    from server import engine_api
    got = {"server/engine_api.py VERSION": engine_api.VERSION,
           "routes.json version": _routes()["version"],
           "ops/engine_status.py ENGINE_API": _literal("ops/engine_status.py", "ENGINE_API")}
    assert len(set(got.values())) == 1, got
    _mm(engine_api.VERSION)
    with open(os.path.join(ROOT, "server", "app.py")) as f:
        src = f.read()
    assert src.count('"engine_api": engine_api.VERSION') == 2       # the full and setup answers
    return f"engine API {engine_api.VERSION} in 3 places"


def _json(*parts: str):
    with open(os.path.join(CONTRACT, *parts)) as f:
        return json.load(f)


def test_values_are_the_contracts():
    from server import cache_peek, decider_api, setup_api
    from server.api import auth as api_auth, usage as api_usage
    from server.conductor import shadow
    cases = _json("auth.json")["same_origin"]
    assert len(cases) >= 10, len(cases)

    def answer(x):
        return None if x is None else list(x)
    for c in cases:
        for mod in (setup_api, api_auth):           # ENG-413 WP2b: and the engine's auth copy
            assert answer(mod.same_origin(c["headers"])) == c["same_origin"], (mod.__name__, c)
            assert answer(mod.same_origin_json(c["headers"])) == c["same_origin_json"], (mod.__name__, c)
    assert {c["headers"].get("Origin") is not None for c in cases} == {True, False}
    assert cache_peek.TIER_OF == _json("cache.json")["tier_of"]
    assert api_usage.TIER_OF == _json("cache.json")["tier_of"]
    # 1.5 (ENG-413 WP5): the engine answers its screens itself; no copy of its off texts was kept
    assert not any("off_why" in s for s in _json("compat", "1.7", "routes.json")["engine_screens_until_eng413"])
    assert decider_api.OFF_WHY.startswith("this engine serves no decision model")
    assert shadow.OFF_WHY.startswith("this engine serves no shadow reader")
    return f"same_origin on {len(cases)} cases (2 copies), TIER_OF (2 copies), no OFF_WHY copy"


def test_setup_fixtures():
    with open(os.path.join(ROOT, "models", "estimate-cases.json"), "rb") as f:
        mine = f.read()
    with open(os.path.join(CONTRACT, "setup", "estimate-cases.json"), "rb") as f:
        theirs = f.read()
    assert mine == theirs, "setup/estimate-cases.json is not models/estimate-cases.json"
    with open(os.path.join(CONTRACT, "setup", "limits.json")) as f:
        limits = json.load(f)
    reg = _literal("server/setup/registry.py", "ENGINE_MAX_SLOTS")
    assert limits["engine_max_slots"] == reg, (limits, reg)
    return f"estimate cases {len(mine)} bytes, engine_max_slots {reg}"


def _env_lines(text: str) -> dict:
    return {m.group(1): m.group(2) for m in re.finditer(r"(?m)^([A-Z_]+)=(.*)$", text)}


def test_node_fixtures():
    with open(os.path.join(CONTRACT, "node", "serve.env.example")) as f:
        example = _env_lines(f.read())
    assert set(example) == {"PORT", "QSE_STATUS_PORT"}, example
    assert all(v.isdigit() for v in example.values()), example
    with open(os.path.join(ROOT, "ops", "serve.env")) as f:
        tmpl = _env_lines(f.read())
    assert tmpl["PORT"] == example["PORT"], tmpl["PORT"]
    assert "QSE_STATUS_PORT" in tmpl                     # empty: install.sh sets it
    with open(os.path.join(ROOT, "install.sh")) as f:
        inst = f.read()
    assert re.search(r'(?m)^setv PORT "\$PORT"$', inst)
    assert re.search(r'(?m)^\[ "\$PORT" -le 65526 \] && setv QSE_STATUS_PORT "\$\(\(PORT \+ 9\)\)"$',
                     inst)
    assert int(example["QSE_STATUS_PORT"]) == int(example["PORT"]) + 9
    # bin/tandem's verbs, as install.sh writes it
    with open(os.path.join(CONTRACT, "node", "control.json")) as f:
        control = json.load(f)
    fn = inst[inst.index("write_control() {"):inst.index("\nCTL\n}\n") + len("\nCTL\n}\n")]
    d = tempfile.mkdtemp(prefix="engine-api-ctl-")
    script = subprocess.run(["bash", "-c", fn + '\nDIR="$1"; write_control', "-", d],
                            capture_output=True, text=True, check=True).stdout
    body = script[script.rindex('case "${1:-}" in'):]
    arms = set(re.findall(r"(?m)^    ([a-z|]+)\)", body))
    verbs = {v for a in arms for v in a.split("|")}
    assert set(control["verbs"]) <= verbs, (control["verbs"], sorted(verbs))
    flags = set(re.findall(r"(?m)^        (--[a-z-]+)(?:\|-[a-z])?\)", inst))
    assert set(control["install"]["flags"]) <= flags, (control["install"]["flags"], flags)
    os.rmdir(d)
    # SRV-817 (1.2): a stage's run/local.env keys and bin/tandem status's stage line
    with open(os.path.join(ROOT, "ops", "engines.sh")) as f, open(os.path.join(ROOT, "ops", "start.sh")) as g:
        ops = f.read() + g.read()
    missing = [k for k in control["stage"]["keys"] if "${" + k not in ops]
    assert not missing, missing
    assert '"[tandem] stage: the %s, layers %s, link %s (epoch %s)%s"' in script, "bin/tandem status's stage line"
    assert control["stage"]["status_line"].startswith("[tandem] stage: the <role>, layers <LO:HI>, link ")
    # a folder laid out as the hub manifest example says: each file `size` zero bytes
    sys.path.insert(0, os.path.join(ROOT, "ops"))
    import fetch_weights
    with open(os.path.join(CONTRACT, "node", "hub_manifest.example.json")) as f:
        man = json.load(f)
    assert set(man) == {"repo", "revision", "commit", "files"}, set(man)
    with tempfile.TemporaryDirectory(prefix="engine-api-pack-") as pack:
        for name, row in man["files"].items():
            with open(os.path.join(pack, name), "wb") as f:
                f.write(b"\0" * row["size"])
            assert row["sha256"] in (None, hashlib.sha256(b"\0" * row["size"]).hexdigest()), name
        with open(os.path.join(pack, fetch_weights.MANIFEST), "w") as f:
            json.dump(man, f, indent=1)
        assert fetch_weights.verify(pack) == []
        hashed = next(n for n, r in sorted(man["files"].items()) if r["sha256"])
        with open(os.path.join(pack, hashed), "r+b") as f:
            f.write(b"\1")
        assert fetch_weights.verify(pack) != []
    return f"PORT {example['PORT']}, status {example['QSE_STATUS_PORT']}, verbs " \
           f"{'/'.join(control['verbs'])}, {len(control['install']['flags'])} flags, " \
           f"{len(man['files'])} files verified"


# ---------------------------------------------------------------------------- 1.3

#: where a reference may leave this folder: TandemApp's contracts, from the lists that name its routes
APP_CONTRACTS = ("../conductor-v1/", "../fleet-v1")
APP_LISTS = ("app_routes_via_engine", "app_families", "planned")


def _refs(node, out: list) -> list:
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "$ref" and isinstance(v, str):
                out.append(v)
            else:
                _refs(v, out)
    elif isinstance(node, list):
        for v in node:
            _refs(v, out)
    return out


def test_schemas_and_references():
    from tools import contract_check
    doc = _routes()
    bad, n = [], 0
    # every schema's references stay in this folder and resolve
    for base, _dirs, names in os.walk(CONTRACT):
        for name in sorted(names):
            if not name.endswith(".schema.json"):
                continue
            rel = os.path.relpath(os.path.join(base, name), CONTRACT)
            with open(os.path.join(CONTRACT, rel)) as f:
                schema = json.load(f)
            for ref in _refs(schema, []):
                file, _, _frag = ref.partition("#")
                if file and not os.path.isfile(os.path.join(os.path.dirname(os.path.join(CONTRACT, rel)), file)):
                    bad.append(f"{rel}: $ref {ref} leaves the folder or is missing")
            # its example, where one sits beside it
            ex = os.path.join(base, name.replace(".schema.json", ".example.json"))
            if os.path.isfile(ex):
                with open(ex) as f:
                    errs = contract_check.validate(json.load(f), schema, root=base)
                bad += [f"{os.path.relpath(ex, CONTRACT)}: {e}" for e in errs[:3]]
                n += 1
    # the routes' shapes: inside the folder; TandemApp's only from the lists of its routes
    for r in doc["routes"]:
        if r.get("schema") and (r["schema"].startswith("..") or
                                not os.path.isfile(os.path.join(CONTRACT, r["schema"]))):
            bad.append(f"{r['method']} {r['path']}: schema {r['schema']}")
    for key in APP_LISTS:
        for e in doc[key]:
            for f in ("schema", "contract"):
                v = e.get(f)
                if v and v.startswith("..") and not v.startswith(APP_CONTRACTS):
                    bad.append(f"{key} {e.get('path') or e.get('what')}: {f} {v}")
    # the marks: since 1.8 every app route left is an extension's, but the sign-in, which leaves
    after = {e["path"]: e["after_eng413"] for e in doc["app_routes_via_engine"]}
    assert {p for p, v in after.items() if v == "leaves"} == {"/v1/dashboard/session"}, after
    assert set(after.values()) == {"leaves", "extension"}, after
    assert all(e.get("why") for e in doc["app_routes_via_engine"])
    leaving = [r["path"] for r in doc["routes"] if r.get("leaves")]
    assert leaving == ["/v1/dashboard/metrics"], leaving
    assert all(r["leaves"].startswith("2.0:") for r in doc["routes"] if r.get("leaves"))
    assert not bad, bad
    return f"{n} examples valid, references inside the folder, {len(after)} extension routes, " \
           f"{len(doc['planned'])} planned"


def test_metrics_families():
    from server.api import metrics as M        # ENG-413 WP2b: the engine's own registry
    fam = {f["name"]: f for f in _json("metrics-families.json")["families"]}
    reg = {m.name: m for m in M.REGISTRY._metrics}
    assert set(fam) == set(reg), (sorted(set(fam) - set(reg)), sorted(set(reg) - set(fam)))
    for name, m in reg.items():
        f = fam[name]
        assert (f["type"], f["labels"]) == (m.kind, list(m.labelnames)), (name, f, m.kind)
        assert f["owner"] == ("ext" if name.startswith("qse_conductor_") else "engine"), f
    with open(os.path.join(CONTRACT, "metrics.md")) as fh:
        text = fh.read()
    rows = set()
    for line in text.splitlines():
        if line.startswith("| `qse_"):
            rows |= set(re.findall(r"`(qse_[a-z0-9_]+)`", line.split("|")[1]))
    assert rows == set(fam), (sorted(rows - set(fam)), sorted(set(fam) - rows))
    ext = sorted(n for n, f in fam.items() if f["owner"] == "ext")
    return f"{len(fam)} families ({len(ext)} ext), each with a row in metrics.md"


def test_additive_1_3():
    from tools import contract_check
    old = _json("compat", "1.2", "routes.json")
    assert old["version"] == "1.2"
    now = {(r["method"], r["path"], r.get("port")): r for r in _routes()["routes"]}
    gone = []
    for r in old["routes"]:
        cur = now.get((r["method"], r["path"], r.get("port")))
        if cur is None or set(cur["aliases"]) < set(r["aliases"]) or cur["live"] != r["live"]:
            gone.append(f"{r['method']} {r['path']}")
    assert not gone, f"1.3 dropped or narrowed 1.2 routes: {gone}"
    assert {s["path"] for s in old["engine_screens_until_eng413"]} == \
        {s["path"] for s in _json("compat", "1.3", "routes.json")["engine_screens_until_eng413"]}
    health = contract_check.load("health", CONTRACT)

    def ok(d, schema):
        return not contract_check.validate(d, schema, root=CONTRACT)
    ex = _json("compat", "1.2", "health.example.json")
    for said in ("1.0", "1.1", "1.2"):
        assert ok(dict(ex, engine_api=said), health), said
    assert ok({k: v for k, v in ex.items() if k != "engine_api"}, health)      # a 0.15.x engine
    front, tail = _json("compat", "1.2", "health.stage-front.example.json"), \
        _json("compat", "1.2", "health.stage-tail.example.json")
    assert ok(front, health) and ok(tail, health)
    req, block = _json("compat", "1.2", "split-request.example.json"), _json("compat", "1.2", "split.example.json")
    assert ok(req, {"$ref": "setup/split.schema.json#/$defs/request"})
    assert ok(block, {"$ref": "setup/split.schema.json"})
    # the 1.3 examples are the 1.2 ones but for the version they say
    for name in ("health.example.json", "health.stage-tail.example.json", "health.stage-front.example.json"):
        a, b = _json("compat", "1.3", name), _json("compat", "1.2", name)
        assert {k: v for k, v in a.items() if k != "engine_api"} == \
            {k: v for k, v in b.items() if k != "engine_api"}, name
        assert a.get("engine_api") in (None, "1.3"), name
    for name in ("split.example.json", "split-request.example.json"):
        assert _json("setup", name) == _json("compat", "1.2", name), name
    return f"{len(old['routes'])} 1.2 routes kept, 1.0/1.1/1.2 health and both stages valid, split valid"


def test_additive_1_4():
    """ENG-413 WP3a: 1.4 adds /health `extensions` and the answers of a path nothing serves
    (`answers`, out of `planned`); every 1.3 route stays, the 1.3 examples still validate."""
    from tools import contract_check
    old = _json("compat", "1.3", "routes.json")
    assert old["version"] == "1.3"
    doc = _json("compat", "1.4", "routes.json")              # 1.4 as it was (1.5 is test_additive_1_5)
    assert doc["version"] == "1.4"
    now = {(r["method"], r["path"], r.get("port")): r for r in doc["routes"]}
    gone = [f"{r['method']} {r['path']}" for r in old["routes"]
            if (r["method"], r["path"], r.get("port")) not in now or
            set(now[(r["method"], r["path"], r.get("port"))]["aliases"]) < set(r["aliases"]) or
            now[(r["method"], r["path"], r.get("port"))]["live"] != r["live"]]
    assert not gone, f"1.4 dropped or narrowed 1.3 routes: {gone}"
    for key in ("engine_screens_until_eng413", "app_routes_via_engine", "app_families", "shared_prefixes"):
        assert doc[key] == old[key], key
    # what 1.3 planned and 1.4 serves: out of `planned`, into `answers` and the health schema
    served = {"404 no_ui", "404 not_on_engine"}
    assert {a["what"] for a in doc["answers"]} == served and all(a["since"] == "1.4" for a in doc["answers"])
    was = {p["what"]: p for p in old["planned"] if p["kind"] in ("field", "answer")}
    assert set(was) == served | {"/health extensions"}, sorted(was)
    for a in doc["answers"]:
        assert set(a["paths"]) - set(was[a["what"]]["paths"]) <= {
            "/v1/library", "/v1/library/*", "/v1/memory", "/v1/laya", "/v1/laya/*",
            "/s/{token}/v1/library*", "/s/{token}/v1/memory*", "/s/{token}/v1/laya*"}, a["what"]
    left = [p for p in doc["planned"] if p["kind"] != "route"]
    assert [p["paths"] for p in left] == [["/v1/chat-app", "/v1/chat-app/*", "/v1/dashboard/session"]], left
    assert [p for p in doc["planned"] if p["kind"] == "route"] == \
        [p for p in old["planned"] if p["kind"] == "route"]
    health = contract_check.load("health", CONTRACT)
    assert "extensions" in health["properties"] and "extensions" not in health.get("required", [])

    def ok(d):
        return not contract_check.validate(d, health, root=CONTRACT)
    for name in ("health.example.json", "health.stage-tail.example.json", "health.stage-front.example.json"):
        a, b = _json("compat", "1.4", name), _json("compat", "1.3", name)
        assert ok(b), name                                     # a 1.3 engine's answer
        assert {k: v for k, v in a.items() if k not in ("engine_api", "extensions")} == \
            {k: v for k, v in b.items() if k != "engine_api"}, name
        assert a.get("engine_api") in (None, "1.4"), name
    ex = _json("compat", "1.4", "health.example.json")
    assert ok(ex) and ok(dict(ex, extensions=[])) and ok(dict(ex, engine_api="1.3"))
    assert not ok(dict(ex, extensions=[{"name": "x"}]))        # name, version and ext_api, all three
    fam = {f["name"]: f for f in _json("metrics-families.json")["families"]}
    assert fam["qse_ext_errors_total"] == {"name": "qse_ext_errors_total", "type": "counter",
                                           "labels": ["hook"], "owner": "engine"}
    return f"{len(old['routes'])} 1.3 routes kept, 1.3 health examples valid, {len(doc['answers'])} answers " \
           f"out of planned, /health extensions in the schema"



def test_additive_1_5():
    """ENG-413 WP5: 1.5 adds group 9, the engine-only screens as /v1/engine/*; every 1.4 route stays."""
    from tools import contract_check
    old = _json("compat", "1.4", "routes.json")
    doc = _json("compat", "1.5", "routes.json")               # 1.5 as it was (1.6 is test_additive_1_6)
    assert (old["version"], doc["version"]) == ("1.4", "1.5")
    now = {(r["method"], r["path"], r.get("port")): r for r in doc["routes"]}
    gone = [f"{r['method']} {r['path']}" for r in old["routes"]
            if (r["method"], r["path"], r.get("port")) not in now or
            set(now[(r["method"], r["path"], r.get("port"))]["aliases"]) < set(r["aliases"])]
    assert not gone, f"1.5 dropped or narrowed 1.4 routes: {gone}"
    g9 = [r for r in doc["routes"] if r["group"] == 9]
    assert len(g9) == 14 and all(r["path"].startswith("/v1/engine/") and r["since"] == "1.5" for r in g9)
    assert all(r["live"] == (r["method"] == "GET") for r in g9)
    assert all(r.get("schema", "").startswith("engine/") or r.get("doc") for r in g9)
    screens = {s["path"] for s in doc["engine_screens_until_eng413"]}
    assert {r["replaces"] for r in g9} == screens == {s["path"] for s in old["engine_screens_until_eng413"]}
    assert not [p for p in doc["planned"] if p.get("lands_with") == "ENG-413 WP5"]
    assert [p for p in doc["planned"]] == [p for p in old["planned"] if p["kind"] != "route"]
    for key in ("app_routes_via_engine", "app_families", "shared_prefixes", "answers"):
        assert doc[key] == old[key], key
    health = contract_check.load("health", CONTRACT)

    def ok(d):
        return not contract_check.validate(d, health, root=CONTRACT)
    for name in ("health.example.json", "health.stage-tail.example.json", "health.stage-front.example.json"):
        a, b = _json("compat", "1.5", name), _json("compat", "1.4", name)
        assert ok(b), name                                     # a 1.4 engine's answer
        strip = lambda d: {k: v for k, v in d.items() if k not in ("engine_api", "extensions")}  # noqa: E731
        assert strip(a) == strip(b), name
        assert a.get("engine_api") in (None, "1.5"), name
    ex = _json("compat", "1.5", "health.example.json")
    assert [x["ext_api"] for x in ex["extensions"]] == ["3.1"], ex["extensions"]
    return f"{len(old['routes'])} 1.4 routes kept, {len(g9)} group 9 routes replacing {len(screens)} screens"


def test_additive_1_6():
    """ENG-413 WP6: 1.6 adds node/control.json's `chat` (QSE_CHATD=node: TandemNode owns chatd);
    no route or answer changes."""
    from tools import contract_check
    old, doc = _json("compat", "1.5", "routes.json"), _json("compat", "1.6", "routes.json")   # 1.6 as it was
    assert (old["version"], doc["version"]) == ("1.5", "1.6")
    assert {k: v for k, v in doc.items() if k != "version"} == \
        {k: v for k, v in old.items() if k != "version"}, "1.6 changes no route"
    health = contract_check.load("health", CONTRACT)
    for name in ("health.example.json", "health.stage-tail.example.json", "health.stage-front.example.json"):
        a, b = _json("compat", "1.6", name), _json("compat", "1.5", name)
        assert not contract_check.validate(b, health, root=CONTRACT), name    # a 1.5 engine's answer
        assert {k: v for k, v in a.items() if k != "engine_api"} == \
            {k: v for k, v in b.items() if k != "engine_api"}, name
        assert a.get("engine_api") in (None, "1.6"), name
    chat = _json("node", "control.json")["chat"]
    assert (chat["key"], chat["values"], chat["node"]) == ("QSE_CHATD", ["on", "off", "node"], "node")
    assert chat["node_verbs"] == ["start", "stop", "restart", "status", "logs"]
    assert chat["node_cli"] == "tandem-node chat" and chat["watch"]["starts_per_hour"] == 3
    with open(os.path.join(ROOT, "ops", "start.sh")) as f:
        start = f.read()
    with open(os.path.join(ROOT, "ops", "serve.env")) as f:
        env = f.read()
    with open(os.path.join(ROOT, "install.sh")) as f:
        inst = f.read()
    ctl = inst[inst.index("write_control() {"):inst.index("\nCTL\n}\n")]
    assert chat["start_line"] in start                       # node's line, as 1.6 wrote it
    assert '[ "${QSE_CHATD:-}" = node ] || QSE_CHATD=off' in env
    assert "TANDEM_NODE_BIN" in ctl and 'exec "$NODE" chat' in ctl
    return "routes unchanged, 1.5 examples valid, control.json chat and the launchers' node"


def test_additive_1_7():
    """ENG-413 WP7: 1.7: the engine runs no chatd (node or off; on is node, unset is off) and
    install.sh --with-chat --app-wheel installs TandemApp; no route or answer changes."""
    from tools import contract_check
    old, doc = _json("compat", "1.6", "routes.json"), _json("compat", "1.7", "routes.json")   # 1.7 as it was
    assert (old["version"], doc["version"]) == ("1.6", "1.7")
    assert {k: v for k, v in doc.items() if k != "version"} == \
        {k: v for k, v in old.items() if k != "version"}, "1.7 changes no route"
    health = contract_check.load("health", CONTRACT)
    for name in ("health.example.json", "health.stage-tail.example.json", "health.stage-front.example.json"):
        a, b = _json("compat", "1.7", name), _json("compat", "1.6", name)
        assert not contract_check.validate(b, health, root=CONTRACT), name    # a 1.6 engine's answer
        assert {k: v for k, v in a.items() if k != "engine_api"} == \
            {k: v for k, v in b.items() if k != "engine_api"}, name
        assert a.get("engine_api") in (None, "1.7"), name
    chat = _json("node", "control.json")["chat"]
    assert (chat["values"], chat["default"], chat["aliases"]) == (["on", "off", "node"], "off", {"on": "node"})
    assert chat["engine_launcher"].startswith("none since 1.7")
    assert chat["install"]["flags"] == ["--with-chat", "--app-wheel"]
    assert "--app-wheel-sha256" in chat["install"]["about"] and "state/tandemapp-wheel.json" in chat["install"]["about"]
    texts = {}
    for rel in ("ops/start.sh", "ops/watchdog.sh", "ops/serve.env", "install.sh"):
        with open(os.path.join(ROOT, rel)) as f:
            texts[rel] = f.read()
    for rel in ("ops/start.sh", "ops/watchdog.sh"):
        assert "chatd.sh" not in texts[rel], rel
    env = texts["ops/serve.env"]
    assert "\nQSE_CHATD_ASKED=${QSE_CHATD:-}\n" in env and "    on) QSE_CHATD=node ;;" in env
    assert '    "") QSE_CHATD=off ;;' in env and env.index("QSE_CHATD_ASKED=") > env.index("# OPS-260: the mode (QSE_MODE")
    inst = texts["install.sh"]
    for flag in chat["install"]["flags"] + ["--app-wheel-sha256"]:
        assert f"        {flag}) " in inst, flag
    assert 'APP_STATE="$DIR/state/tandemapp-wheel.json"' in inst
    assert '"$NODE_BIN" unit --chat on --tandem-dir "$DIR" --port "$NODE_PORT"' in inst
    assert "^QSE_CHATD_ASKED=" in inst                      # chat.update: the pre-1.7 install's mark
    return "routes unchanged, 1.6 examples valid, chat: node or off, the install's flags"


def test_additive_1_8():
    """ENG-413 WP14: 1.8: the page, its sign-in, the Chat tab forward and --backends left the
    engine; every 1.7 route stays, the 1.7 examples validate, what 1.7 planned is an answer."""
    from tools import contract_check
    old, doc = _json("compat", "1.7", "routes.json"), _routes()
    assert (old["version"], doc["version"]) == ("1.7", "1.8")
    # no route goes; the two the engine never answered itself say so (`relay_only`)
    relay = [r["path"] for r in doc["routes"] if r.get("relay_only")]
    assert relay == ["/v1/embeddings", "/v1/responses"], relay
    assert [{k: v for k, v in r.items() if k != "relay_only"} for r in doc["routes"]] == old["routes"]
    # what left the engine is the controller's own family now (never the engine's)
    assert doc["app_families"][:len(old["app_families"])] == old["app_families"]
    health = contract_check.load("health", CONTRACT)
    for name in ("health.example.json", "health.stage-tail.example.json", "health.stage-front.example.json"):
        a, b = _json(name), _json("compat", "1.7", name)
        assert not contract_check.validate(b, health, root=CONTRACT), name    # a 1.7 engine's answer
        assert {k: v for k, v in a.items() if k != "engine_api"} == \
            {k: v for k, v in b.items() if k != "engine_api"}, name
        assert a.get("engine_api") in (None, "1.8"), name
    # the Chat tab forward that 1.7 planned is an answer; the sign-in stays planned
    was = [p for p in old["planned"] if p["kind"] == "answer"]
    assert [p["paths"] for p in was] == [["/v1/chat-app", "/v1/chat-app/*", "/v1/dashboard/session"]]
    now = [a for a in doc["answers"] if a.get("since") == "1.8"]
    assert [(a["what"], a["paths"]) for a in now] == [("404 not_on_engine", ["/v1/chat-app", "/v1/chat-app/*"])]
    assert [(p["what"], p["paths"]) for p in doc["planned"]] == [("404 not_on_engine", ["/v1/dashboard/session"])]
    assert {a["what"] for a in doc["answers"]} == {"404 no_ui", "404 not_on_engine"}
    # the leaving app routes and the screens' old paths are out but the sign-in; the extension's stay
    left = [e["path"] for e in old["app_routes_via_engine"] if e["after_eng413"] == "leaves"
            and e["path"] != "/v1/dashboard/session"]
    assert sorted(left) == sorted(["/v1/config", "/v1/config/*", "/v1/collect/*",
                                   "/v1/chat-app", "/v1/chat-app/*", "/dashboard/*"]), left
    assert [e["path"] for e in doc["app_routes_via_engine"]] == \
        [e["path"] for e in old["app_routes_via_engine"] if e["path"] not in left]
    assert [e["path"] for e in doc["app_families"][len(old["app_families"]):]] == left
    assert "engine_screens_until_eng413" not in doc and doc["shared_prefixes"] == old["shared_prefixes"]
    # the shapes the engine serves under /v1/dashboard are its own
    for name in ("session", "error", "gap"):
        assert os.path.isfile(os.path.join(CONTRACT, "dashboard", f"{name}.schema.json")), name
    replaced = {r["replaces"] for r in doc["routes"] if r.get("replaces")}
    assert replaced == {s["path"] for s in old["engine_screens_until_eng413"]}
    paths = {p for a in doc["answers"] for p in a["paths"]}
    assert set(left) - {"/dashboard/*"} <= paths and "/dashboard/*" in paths, sorted(set(left) - paths)
    # the server: no TandemApp module, the page and the rest answered, the page's flags gone
    with open(os.path.join(ROOT, "server", "app.py")) as f:
        src = f.read()
    tree = ast.parse(src)
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module} | \
        {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not {m for m in mods if m.split(".")[0] in ("tandemapp", "proxy", "chatd")}, mods
    for gone in ("--dashboard-dir", "--host-collector", "_app_part", "dashboard_dir"):
        assert gone not in src, gone
    return f"{len(old['routes'])} 1.7 routes kept, 1.7 examples valid, {len(left)} app routes " \
           f"left, {len(replaced)} old screen paths out, the sign-in planned"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
