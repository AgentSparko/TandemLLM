"""The engine API contract v1, TandemApp's side (SRV-748; 1.3: ENG-427, QA ENG-428): the engine's
contracts/engine-api-v1, copied here at the commit PIN names (tests/test_contract_pin.py), against
TandemApp's callers and its own copies. The engine's side is the engine's
tests/test_engine_api_contract.py.

  1  routes.json is well formed: its version is proxy.engineapi.SPEAKS, every route has a method,
     a group, a shape that exists, and callers or a named consumer; the lists are disjoint
                                                                  test_routes_json
  2  every engine path literal is classified: in the Python callers (nodeengines, nodesource,
     nodeagent, nodeweights, placement, fleetsetup, health, service, relay, deployments, canary,
     tandemapp/parallel, conductor/classify, chatd engine/agent/service/skills/tools,
     tools/tandem_eval) and in every string or template literal of dashboard/src that starts
     with /v1/, /health, /metrics, /readyz, /_tandem, /s/ or /dashboard/ (`${...}` is {param},
     the query is dropped, a base matches as a prefix, all of it when text follows a {param}),
     each falls in exactly one of routes, engine_screens_until_eng413, app_routes_via_engine
     and app_families, or the test names file:line; planted unknown paths are caught
                                                                  test_every_literal_is_classified
  3  every route is used: a file of its used_by holds a literal for it, or a relay prefix
     covers it; nodeengines.RELAY is in the routes               test_every_route_is_used
  4  the version: a missing field reads 1.0 (assumed, ok), 1.0 to 1.5 are ok, 2.0
     is not and names 1.x, a value not in the plain MAJOR.MINOR form is not ok and its reason
     quotes it, another kind is not read; nodeengines.health against fake engines that say
     nothing, 1.0, 1.1, 1.2, 1.3, 1.4, 2.0 and garbage; the health example validates
                                                                  test_versions
  5  1.2 is additive: the health answers of a 1.0 and a 1.1 engine (no stage block) still
     validate, and so do the two stages' examples with the stage block and the tail's degraded;
     a stage block missing a field or with another role does not; the split examples (a request
     and a block the engine computed) validate, a not-splittable block per reason validates, an
     unknown ask field or reason does not; control.json's stage keys    test_additive_1_2
  6  the node fixtures: nodeengines.default_engine reads node/serve.env.example's port and builds
     node/control.json's verbs; nodeweights reads a folder laid out as
     node/hub_manifest.example.json says (files_of, the sha256 per file, the content id)
                                                                  test_node_fixtures
  7  1.3, TandemApp's copies of the engine's values: tandemapp/auth.py same_origin and
     same_origin_json answer auth.json's cases, tandemapp/usage.py TIER_OF is cache.json's,
     tandemapp/conductor/routes.py keeps no OFF_WHY copy (1.5: the engine answers its screens), the registry of
     tandemapp/metrics.py names metrics-families.json's families with their types and labels
     (less the engine host's own counter, 1.4's qse_ext_errors_total)
                                                                  test_values_1_3
  8  1.3 is additive and the moved shapes are the same: the 1.2 examples (compat/1.2/) validate,
     every 1.2 route is still here, contracts/dashboard-v1 keeps byte copies of the engine's
     dashboard/ files and conductor-v1 of decision/systemone.schema.json, the app routes carry
     their after-ENG-413 mark and no TandemApp file calls a planned route
                                                                  test_additive_1_3
  9  1.4 (ENG-413 WP3a, pinned for WP4) is additive: the 1.3 examples (compat/1.3/) validate,
     every 1.3 route is still here, 1.4's health example (compat/1.4/) says 1.4 and its
     `extensions` names TandemApp's extension, and the headless answers name no_ui and
     not_on_engine                                                test_additive_1_4
 10  1.5 (ENG-413 WP5) is additive: the 1.4 examples (compat/1.4/) validate, every 1.4 route is
     still here, group 9 is the engine's screens (each replaces a /v1/conductor/* screen, which
     is marked to leave), the dashboard reads them at /v1/engine/* and no longer at the
     conductor's paths, and the health example names TandemApp's extension on PIN's ext_api
                                                                  test_additive_1_5

Standard library only; the fake engines are threads on loopback ports.

    python tests/test_engine_api_contract.py
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from proxy import _version, engineapi, nodeengines, nodeweights  # noqa: E402
from proxy_util import run  # noqa: E402
from tools import contract_check  # noqa: E402

CONTRACT = os.path.join(ROOT, "contracts", "engine-api-v1")
UI = os.path.join(ROOT, "dashboard", "src")
CLIENTS = ("proxy/nodeengines.py", "proxy/nodesource.py", "proxy/nodeagent.py",
           "proxy/nodeweights.py", "proxy/placement.py", "proxy/fleetsetup.py", "proxy/health.py",
           "proxy/service.py", "proxy/relay.py", "proxy/deployments.py", "proxy/canary.py",
           "tandemapp/parallel.py",
           "tandemapp/conductor/classify.py", "chatd/engine.py", "chatd/agent.py",
           "chatd/service.py", "chatd/skills.py", "chatd/tools.py", "tools/tandem_eval.py")
LISTS = ("routes", "engine_screens_until_eng413", "app_routes_via_engine", "app_families")
#: where an engine path starts in a literal: its beginning, or right after a `${...}`
LEAD = re.compile(r"(?:^|(?<=\}))(/v1/|/health|/metrics|/readyz|/_tandem|/s/|/dashboard/)")
#: the API root alone (a base URL's `/v1`) is no route
ROOTS = ("/v1", "/v1/")
SAMPLE = "p1x"


def _routes() -> dict:
    with open(os.path.join(CONTRACT, "routes.json")) as f:
        return json.load(f)


# ---------------------------------------------------------------------------- literals

def ts_literals(src: str) -> list[tuple[int, str]]:
    """(line, text) of every string and template literal of TypeScript source, comments and
    regular expressions skipped; a template's `${...}` is `{param}` and the literals inside it are
    reported too."""
    out: list[tuple[int, str]] = []
    n = len(src)

    def string(i: int, q: str) -> tuple[int, str]:
        j, buf = i + 1, []
        while j < n and src[j] != q and src[j] != "\n":
            if src[j] == "\\":
                buf.append(src[j:j + 2])
                j += 2
                continue
            buf.append(src[j])
            j += 1
        return j + 1, "".join(buf)

    def template(i: int, line: int) -> tuple[int, str, int]:
        j, buf = i + 1, []
        while j < n and src[j] != "`":
            c = src[j]
            if c == "\\":
                buf.append(src[j:j + 2])
                j += 2
                continue
            if c == "\n":
                line += 1
            if c == "$" and src.startswith("${", j):
                depth, k = 1, j + 2
                while k < n and depth:
                    ch = src[k]
                    if ch in "'\"":
                        k, t = string(k, ch)
                        out.append((line, t))
                        continue
                    if ch == "`":
                        start = line
                        k, t, line = template(k, line)
                        out.append((start, t))
                        continue
                    if ch == "\n":
                        line += 1
                    depth += {"{": 1, "}": -1}.get(ch, 0)
                    k += 1
                buf.append("{param}")
                j = k
                continue
            buf.append(c)
            j += 1
        return j + 1, "".join(buf), line

    i, line, last, word = 0, 1, "", ""
    while i < n:
        c = src[i]
        if c == "\n":
            line += 1
            i += 1
        elif c in " \t\r":
            i += 1
        elif src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            line += src.count("\n", i, j)
            i = j
        elif c in "'\"":
            i, t = string(i, c)
            out.append((line, t))
            last, word = "a", ""
        elif c == "`":
            start = line
            i, t, line = template(i, line)
            out.append((start, t))
            last, word = "a", ""
        elif c == "/" and (last in "" or last in "(,=:[!&|?{};+-*%<>~^" or
                           word in ("return", "typeof", "case", "in", "of", "void", "throw")):
            j, cls = i + 1, False                         # a regular expression literal
            while j < n and src[j] != "\n":
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == "/" and not cls:
                    break
                cls = True if src[j] == "[" else False if src[j] == "]" else cls
                j += 1
            j += 1
            while j < n and src[j].isalnum():
                j += 1
            i, last, word = j, "a", ""
        elif c.isalnum() or c in "_$":
            j = i
            while j < n and (src[j].isalnum() or src[j] in "_$"):
                j += 1
            word, last, i = src[i:j], "a", j
        else:
            last, word = c, ""
            i += 1
    return out


def py_literals(src: str) -> list[tuple[int, str]]:
    """(line, text) of every string constant and f-string of Python source but docstrings; an
    f-string's `{...}` is `{param}`."""
    tree = ast.parse(src)
    docs, parts, out = set(), set(), []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.body and isinstance(node.body[0], ast.Expr) and \
                isinstance(node.body[0].value, ast.Constant):
            docs.add(id(node.body[0].value))
        if isinstance(node, ast.JoinedStr):
            text = ""
            for v in node.values:
                if isinstance(v, ast.Constant):
                    parts.add(id(v))
                    text += str(v.value)
                else:
                    text += "{param}"
            out.append((node.lineno, text))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and \
                id(node) not in docs and id(node) not in parts:
            out.append((node.lineno, node.value))
    return out


def path_of(text: str) -> str | None:
    """The engine path a literal holds, normalized: from where it starts to the first character
    no path has, the query dropped, a `{param}` glued to the end of a segment (a query built in
    code) dropped; None when it holds none or only the API root."""
    m = LEAD.search(text)
    if m is None:
        return None
    p = re.match(r"[A-Za-z0-9_./{}%:-]*", text[m.start():]).group(0)
    while re.search(r"[^/]\{param\}$", p):
        p = p[:-len("{param}")]
    return None if p in ROOTS else p


def literals_of(rel: str) -> list[tuple[int, str]]:
    """(line, path) of the engine paths in a file under TandemApp's tree."""
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        src = f.read()
    raw = py_literals(src) if rel.endswith(".py") else ts_literals(src)
    return [(ln, p) for ln, t in raw for p in [path_of(t)] if p]


def ui_files() -> list[str]:
    out = []
    for base, dirs, files in os.walk(UI):
        dirs.sort()
        out += [os.path.relpath(os.path.join(base, f), ROOT).replace(os.sep, "/")
                for f in sorted(files) if f.endswith(".ts")]
    return out


# ---------------------------------------------------------------------------- classification

def _rx(pattern: str) -> re.Pattern:
    out = ""
    for part in re.split(r"(\{[^}]+\}|\*)", pattern):
        out += "[^/]+" if part.startswith("{") else ".*" if part == "*" else re.escape(part)
    return re.compile(out)


def _text(pattern: str) -> str:
    """A pattern's literal text up to its first wildcard, a {param} as SAMPLE."""
    return re.sub(r"\{[^}]+\}", SAMPLE, pattern).split("*")[0]


def entries(doc: dict) -> list[tuple[str, str, list[str]]]:
    """(list, pattern, except patterns) of every path the four lists name."""
    out = []
    for key in LISTS:
        for e in doc[key]:
            for p in [e["path"]] + list(e.get("aliases") or []):
                out.append((key, p, list(e.get("except") or [])))
    return out


def classify(path: str, ents) -> tuple[set, bool]:
    """The lists a literal's path falls in, and whether it matched as a prefix (a base). A base
    with text after a {param} counts only where all of it, each {param} one segment, starts a
    pattern's own text."""
    sample = path.replace("{param}", SAMPLE)
    exact = {k for k, p, exc in ents if _rx(p).fullmatch(sample) and
             not any(_rx(x).fullmatch(sample) for x in exc)}
    if exact:
        return exact, False
    base, _, rest = path.partition("{param}")
    if rest.strip("/"):
        rx = re.compile("[^/]+".join(re.escape(x) for x in path.split("{param}")))
        return {k for k, p, _exc in ents
                if rx.match(re.sub(r"\{[^}]+\}", SAMPLE, p).replace("*", SAMPLE))}, True
    return {k for k, p, _exc in ents if _text(p).startswith(base) or
            (p.endswith("*") and base.startswith(_text(p)))}, True


def test_routes_json():
    doc = _routes()
    assert doc["version"] == engineapi.SPEAKS, (doc["version"], engineapi.SPEAKS)
    assert set(LISTS) <= set(doc), set(doc)
    bad = []
    for r in doc["routes"]:
        name = f"{r.get('method')} {r.get('path')}"
        if r.get("method") not in ("GET", "POST") or not str(r.get("path", "")).startswith("/"):
            bad.append(f"{name}: method or path")
        if r.get("group") not in range(1, 10):            # 1.5: group 9, the engine's screens
            bad.append(f"{name}: group")
        if not isinstance(r.get("live"), bool) or (r["live"] and r["method"] != "GET"):
            bad.append(f"{name}: live")
        if not r.get("used_by") and not r.get("consumer"):
            bad.append(f"{name}: no caller and no consumer")
        if r.get("schema") and not os.path.isfile(os.path.join(CONTRACT, r["schema"])):
            bad.append(f"{name}: no {r['schema']}")
        if not r.get("schema") and not r.get("doc"):
            bad.append(f"{name}: no shape")
        for u in r.get("used_by") or []:
            if not u.startswith("relay:") and not os.path.isfile(os.path.join(ROOT, u)):
                bad.append(f"{name}: no {u}")
    for e in doc["engine_screens_until_eng413"]:
        if e.get("schema") and not os.path.isfile(os.path.join(CONTRACT, e["schema"])):
            bad.append(f"screen {e['path']}: no {e['schema']}")
    for key in ("app_routes_via_engine", "app_families"):
        for e in doc[key]:
            c = e.get("contract")
            if c and not os.path.exists(os.path.join(CONTRACT, c)):
                bad.append(f"{key} {e['path']}: no {c}")
    assert not bad, bad
    # the lists are disjoint: no path of one falls in another
    ents = entries(doc)
    for key, p, _exc in ents:
        got, _ = classify(p.replace("*", SAMPLE), ents)
        assert got == {key}, (key, p, got)
    for base, keys in doc.get("shared_prefixes", {}).items():
        got, prefix = classify(base, ents)
        assert prefix and got == set(keys) and len(keys) > 1, (base, got, keys)
    return f"{len(doc['routes'])} routes, {len(ents)} paths in 4 lists"


def test_every_literal_is_classified():
    doc = _routes()
    ents = entries(doc)
    shared = doc.get("shared_prefixes", {})
    files = list(CLIENTS) + ui_files()
    bad, seen = [], 0
    for rel in files:
        for ln, p in literals_of(rel):
            seen += 1
            got, prefix = classify(p, ents)
            if len(got) == 1 or (prefix and p in shared and got == set(shared[p])):
                continue
            bad.append(f"{rel}:{ln} {p} -> {sorted(got) or 'unclassified'}")
    assert not bad, f"{len(bad)} literals: " + "; ".join(bad[:20])
    assert seen > 100, seen
    # the scan's own check: unknown paths, text after a {param} included, are caught
    for p in ("/v1/bogus/route", "/v1/setup/jobs/{param}/bogus", "/v1/models/{param}/nope/deep",
              "/v1/setup/models/{param}/steps/{param}/more"):
        assert classify(p, ents)[0] == set(), (p, classify(p, ents))
    for p, key in (("/v1/setup/jobs/{param}/retry", "routes"),
                   ("/v1/setup/models/{param}/", "routes"),
                   ("/s/{param}/x", "app_routes_via_engine")):
        assert classify(p, ents)[0] == {key}, (p, classify(p, ents))
    out = f"{seen} literals in {len(files)} files, 0 unclassified"
    print(f"[engine-api] {out}", flush=True)
    return out


def _uses(rel: str, route: dict, ents) -> bool:
    """Whether a file holds a literal for the route: its path or an alias, or a base (a literal
    that names no route itself) the route's path starts with."""
    paths = [route["path"]] + list(route["aliases"])
    for _ln, lit in literals_of(rel):
        sample = lit.replace("{param}", SAMPLE)
        if any(_rx(p).fullmatch(sample) for p in paths):
            return True
        _got, base = classify(lit, ents)
        if base and any(_text(p).startswith(lit.split("{param}")[0]) for p in paths):
            return True
    return False


def test_every_route_is_used():
    doc = _routes()
    ents = entries(doc)
    #: the generic relays: TandemNode and the fleet's setup pass /v1/setup/<sub> on as it is
    relayed = {p for rel in ("proxy/fleetsetup.py", "proxy/nodeagent.py")
               for _ln, p in literals_of(rel) if p.endswith("/{param}")}
    unused = []
    for r in doc["routes"]:
        ok = False
        for u in r["used_by"]:
            if u.startswith("relay:"):
                pre = u[len("relay:"):]
                ok = ok or (r["path"].startswith(pre) and pre + "{param}" in relayed)
            else:
                ok = ok or _uses(u, r, ents)
        if r["used_by"] and not ok:
            unused.append(f"{r['method']} {r['path']}: none of {r['used_by']}")
        if not r["used_by"] and r.get("port") != "QSE_STATUS_PORT":
            # a route no TandemApp file calls names its consumer; then no caller exists here
            hits = [rel for rel in list(CLIENTS) + ui_files()
                    if any(_rx(r["path"]).fullmatch(lit.replace("{param}", SAMPLE))
                           for _ln, lit in literals_of(rel))]
            if hits:
                unused.append(f"{r['method']} {r['path']}: called by {hits}, used_by empty")
    assert not unused, unused
    gets = {p for r in doc["routes"] if r["method"] == "GET" for p in [r["path"]] + r["aliases"]}
    assert set(nodeengines.RELAY) <= gets, set(nodeengines.RELAY) - gets
    return f"{len(doc['routes'])} routes used; RELAY {len(nodeengines.RELAY)} in routes"


# ---------------------------------------------------------------------------- the version

class FakeEngine:
    """/health as a TandemLLM engine (or another) answers it: `doc` as JSON, or `raw` bytes."""

    def __init__(self, doc=None, raw=None, status=200):
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                body = raw if raw is not None else json.dumps(doc).encode()
                self.send_response(status if self.path == "/health" else 404)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        assert self.port not in (8000, 8009)
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05},
                         daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def test_versions():
    why = f"this engine speaks engine API 2.0; TandemApp {_version.release()} needs 1.x"
    assert engineapi.read({"status": "ok"}, "tandemllm") == \
        {"engine_api": "1.0", "assumed": True, "ok": True, "why": None}
    assert engineapi.read({"status": "ok", "engine_api": "1.4"}, "tandemllm") == \
        {"engine_api": "1.4", "assumed": False, "ok": True, "why": None}
    # an engine one minor behind (1.0, no identity route) and one on this minor are both ok
    assert engineapi.ASSUMED == "1.0" and engineapi.SPEAKS != "1.0"
    for said in ("1.0", "1.1", "1.2", engineapi.SPEAKS):
        assert engineapi.read({"status": "ok", "engine_api": said}, "tandemllm") == \
            {"engine_api": said, "assumed": False, "ok": True, "why": None}, said
    assert engineapi.read({"engine_api": "2.0"}, "tandemllm") == \
        {"engine_api": "2.0", "assumed": False, "ok": False, "why": why}
    for kind in ("vllm", "sglang", "generic"):
        assert engineapi.read({"engine_api": "2.0"}, kind) is None
    assert engineapi.read({"engine_api": "one"}, "tandemllm")["ok"] is False
    # only the plain MAJOR.MINOR form, ASCII digits, a string; the reason quotes what was sent
    for got, said in (("1.0\n", "'1.0\\n', not MAJOR.MINOR"), ("", "'', not MAJOR.MINOR"),
                      (" 1.4", "' 1.4', not MAJOR.MINOR"),
                      ("1.\u0663", "'1.\u0663', not MAJOR.MINOR"),
                      (1.0, "1.0, not a MAJOR.MINOR string"),
                      (2, "2, not a MAJOR.MINOR string")):
        r = engineapi.read({"engine_api": got}, "tandemllm")
        assert (r["ok"], r["why"]) == (False, why.replace("speaks engine API 2.0",
                                                          f"sends engine_api {said}")), (got, r)
    # nodeengines.health and the view against engines on loopback
    fakes = {"unsaid": FakeEngine({"status": "ok", "version": "0.15.0"}),
             "v10": FakeEngine({"status": "ok", "engine_api": "1.0"}),
             "v11": FakeEngine({"status": "ok", "engine_api": "1.1"}),
             "v12": FakeEngine({"status": "ok", "engine_api": "1.2"}),
             "v13": FakeEngine({"status": "ok", "engine_api": "1.3"}),
             "v14": FakeEngine({"status": "ok", "engine_api": "1.4"}),
             "v20": FakeEngine({"status": "ok", "engine_api": "2.0"}),
             "garbage": FakeEngine(raw=b"<html>not json</html>"),
             "setup": FakeEngine({"status": "ok", "mode": "setup", "engine_api": "1.0"})}
    logs = tempfile.mkdtemp(prefix="engine-api-logs-")
    try:
        es = nodeengines.Engines([nodeengines.Engine({"id": k, "port": f.port}, logs)
                                  for k, f in fakes.items()] +
                                 [nodeengines.Engine({"id": "other", "port": fakes["v20"].port,
                                                      "kind": "generic"}, logs)])
        view = {v["id"]: v for v in es.view()}
        want = {"unsaid": ("up", "1.0", True, True, None), "v10": ("up", "1.0", False, True, None),
                "v11": ("up", "1.1", False, True, None), "v12": ("up", "1.2", False, True, None),
                "v13": ("up", "1.3", False, True, None),
                "v14": ("up", "1.4", False, True, None),
                "v20": ("unhealthy", "2.0", False, False, why),
                "garbage": ("unhealthy", "1.0", True, True, None),      # no status: not up
                "setup": ("up", "1.0", False, True, None),
                "other": ("up", None, None, None, None)}
        for k, (state, api, assumed, ok, reason) in want.items():
            h = view[k]["health"]
            got = (view[k]["state"], h["engine_api"], h["api_assumed"], h["api_ok"], h["api_why"])
            assert got == (state, api, assumed, ok, reason), (k, got)
        # the health example and the shapes the engines answer validate against the schema
        schema = contract_check.load("health", CONTRACT)
        with open(os.path.join(CONTRACT, "health.example.json")) as f:
            ex = json.load(f)
        assert not contract_check.validate(ex, schema, root=CONTRACT)
        assert engineapi.read(ex, "tandemllm")["ok"] and ex["engine_api"] == engineapi.SPEAKS
        for d in ({"status": "ok"}, {"status": "ok", "mode": "setup", "status_port": None,
                                     "assistant": "off", "engine_api": "1.0"},
                  {"status": "held", "engine_api": "1.0", "engine": {"state": "held"},
                   "error": {"type": "provider_unavailable"}}):
            assert not contract_check.validate(d, schema, root=CONTRACT), d
    finally:
        for f in fakes.values():
            f.close()
        shutil.rmtree(logs, ignore_errors=True)
    return f"read: 1.0 assumed, 1.0 to 1.4 ok, 2.0 names 1.x; {len(want)} engines viewed"


def _ex(name: str):
    with open(os.path.join(CONTRACT, name)) as f:
        return json.load(f)


def test_additive_1_2():
    health = contract_check.load("health", CONTRACT)
    split = {"$ref": "setup/split.schema.json"}
    ask = {"$ref": "setup/split.schema.json#/$defs/ask"}

    def ok(doc, schema):
        return not contract_check.validate(doc, schema, root=CONTRACT)

    # a 1.0 and a 1.1 engine's full answers (no stage block, no degraded) still validate
    old = _ex("health.example.json")
    for said in ("1.0", "1.1"):
        assert ok(dict(old, engine_api=said), health), said
    assert ok({"status": "ok"}, health) and "stage" not in old
    # the two stages: the front's small server and the tail's full answer, the tail degraded
    front, tail = _ex("health.stage-front.example.json"), _ex("health.stage-tail.example.json")
    assert ok(front, health) and ok(tail, health)
    assert "engine_api" not in front and tail["engine_api"] == engineapi.SPEAKS
    assert tail["status"] == "degraded" and engineapi.read(tail, "tandemllm")["ok"]
    f, t = front["stage"], tail["stage"]
    assert (f["role"], t["role"], f["shape"], t["shape"]) == ("front", "tail", 1, 1)
    assert f["hi"] == t["lo"] == f["cut"] == t["cut"] and t["hi"] == t["layers"]
    assert f["link"]["peer"]["identity"] == t["identity"] and t["link"]["peer"]["identity"] == f["identity"]
    for key in ("shape", "role", "range", "identity", "options", "link"):
        bad = dict(tail, stage={k: v for k, v in t.items() if k != key})
        assert not ok(bad, health), key
    assert not ok(dict(tail, stage=dict(t, role="middle")), health)
    assert not ok(dict(tail, stage=dict(t, link={k: v for k, v in t["link"].items() if k != "epoch"})), health)
    assert not ok(dict(tail, status="sideways"), health)
    # the estimate's split: the request, the engine's block from a real pack, each not-splittable reason
    req, block = _ex("setup/split-request.example.json"), _ex("setup/split.example.json")
    assert ok(req, {"$ref": "setup/split.schema.json#/$defs/request"}) and ok(req["split"], ask)
    assert ok(True, ask) and ok({}, ask)
    assert not ok({"cut": 12}, ask) and not ok({"caches": "maybe"}, ask)
    assert ok(block, split) and block["splittable"] is True and block["computed"] is True
    assert block["best_cut"] in [c["cut"] for c in block["candidates"]] and block["measured"]
    for c in block["candidates"]:
        for s in ("front", "tail"):
            assert abs(c[s]["gb"] - sum(p["gb"] for p in c[s]["parts"])) <= 0.01 * len(c[s]["parts"]), c[s]
    for reason in ("family", "pack", "cuts", "caches", "slots"):
        assert ok({"version": "1.0", "splittable": False, "reason": reason, "message": "m"}, split), reason
    assert not ok({"version": "1.0", "splittable": False, "reason": "size", "message": "m"}, split)
    assert not ok({"version": "1.0", "splittable": True}, split)
    assert not ok(dict(block, computed=False), split)
    # node/control.json: the keys a stage's run/local.env takes
    with open(os.path.join(CONTRACT, "node", "control.json")) as fh:
        st = json.load(fh)["stage"]
    assert {"TANDEM_STAGE", "TANDEM_LINK_KEY", "TANDEM_STAGE_CACHES"} <= set(st["keys"])
    assert (st["start_order"], st["stop_order"]) == (["front", "tail"], ["tail", "front"])
    return f"1.0 and 1.1 answers valid, both stages, {len(block['candidates'])} cuts, 5 reasons"


# ---------------------------------------------------------------------------- the node fixtures

def test_node_fixtures():
    with open(os.path.join(CONTRACT, "node", "control.json")) as f:
        control = json.load(f)
    tandem = tempfile.mkdtemp(prefix="engine-api-tandem-")
    try:
        os.makedirs(os.path.join(tandem, "run", "ops"))
        shutil.copy(os.path.join(CONTRACT, "node", "serve.env.example"),
                    os.path.join(tandem, "run", "ops", "serve.env"))
        with open(os.path.join(CONTRACT, "node", "serve.env.example")) as f:
            port = int(re.search(r"(?m)^PORT=(\d+)$", f.read()).group(1))
        e = nodeengines.default_engine(tandem)
        ctl = os.path.join(tandem, "bin", "tandem")
        assert e["port"] == port and e["kind"] == "tandemllm", e
        assert {v: e.get(v) for v in control["verbs"]} == \
            {v: ["bash", ctl, v] for v in control["verbs"]}, e
        # SRV-817: a stage's keys go to the install's run/local.env (control.json `stage`)
        assert e["local_env"] == os.path.join(tandem, "run", "local.env"), e
        assert set(e) - {"id", "kind", "port", "local_env"} == set(control["verbs"]), e
        nodeengines.Engine(e, tandem)                      # a valid engine entry
    finally:
        shutil.rmtree(tandem, ignore_errors=True)
    with open(os.path.join(CONTRACT, "node", "hub_manifest.example.json")) as f:
        man = json.load(f)
    pack = tempfile.mkdtemp(prefix="engine-api-pack-")
    try:
        for name, row in man["files"].items():
            with open(os.path.join(pack, nodeweights.check_rel(name)), "wb") as f:
                f.write(b"\0" * row["size"])
        shutil.copy(os.path.join(CONTRACT, "node", "hub_manifest.example.json"),
                    os.path.join(pack, nodeweights.MANIFEST))
        sizes = nodeweights.files_of(pack)
        assert {k: v for k, v in sizes.items() if k != nodeweights.MANIFEST} == \
            {k: r["size"] for k, r in man["files"].items()}, sizes
        got = nodeweights.manifest(pack)["files"]
        for name, row in man["files"].items():
            assert got[name] == {"size": row["size"], "sha256": row["sha256"]}, (name, got[name])
        rows = {k: (v["size"], v["sha256"]) for k, v in got.items()}
        lines = sorted(f"{n}\t{s}\t{h or ''}" for n, (s, h) in rows.items())
        cid = "sha256:" + hashlib.sha256("\n".join(lines).encode()).hexdigest()
        assert nodeweights.content_id_of(rows) == cid
    finally:
        shutil.rmtree(pack, ignore_errors=True)
    return f"port {port}, verbs {'/'.join(control['verbs'])}, {len(man['files'])} files, {cid[:15]}"



# ---------------------------------------------------------------------------- 1.3

def test_values_1_3():
    from tandemapp import auth, metrics, usage
    from tandemapp.conductor import routes

    def answer(x):
        return None if x is None else list(x)
    cases = _ex("auth.json")["same_origin"]
    for c in cases:
        assert answer(auth.same_origin(c["headers"])) == c["same_origin"], c
        assert answer(auth.same_origin_json(c["headers"])) == c["same_origin_json"], c
    assert usage.TIER_OF == _ex("cache.json")["tier_of"]
    # 1.5 (ENG-413 WP5): the engine answers its screens itself, so no copy of its off texts is kept
    assert not hasattr(routes, "DECIDERS_OFF_WHY") and not hasattr(routes, "SHADOW_OFF_WHY")
    assert not any("off_why" in e for e in _routes()["engine_screens_until_eng413"])
    fams = {f["name"]: f for f in _ex("metrics-families.json")["families"]
            if f["name"] not in ENGINE_HOST_ONLY}
    reg = {m.name: m for m in metrics.REGISTRY._metrics}
    assert set(fams) == set(reg), (sorted(set(fams) - set(reg)), sorted(set(reg) - set(fams)))
    for name, m in reg.items():
        assert (fams[name]["type"], fams[name]["labels"]) == (m.kind, list(m.labelnames)), name
    ext = sorted(n for n, f in fams.items() if f["owner"] == "ext")
    assert ext and all(n.startswith("qse_conductor_") for n in ext), ext
    return f"same_origin on {len(cases)} cases, TIER_OF, no OFF_WHY copy, {len(fams)} families ({len(ext)} ext)"


#: families only an engine's extension host has (1.4: the host's failures of an extension's hooks);
#: TandemApp's registry is the controller's and the conductor's, not the host's
ENGINE_HOST_ONLY = ("qse_ext_errors_total",)


def test_additive_1_3():
    def ok(doc, schema):
        return not contract_check.validate(doc, schema, root=CONTRACT)
    health = contract_check.load("health", CONTRACT)
    old = _ex("compat/1.2/health.example.json")
    for said in ("1.0", "1.1", "1.2"):
        assert ok(dict(old, engine_api=said), health) and engineapi.read(dict(old, engine_api=said))["ok"]
    for name in ("health.stage-front.example.json", "health.stage-tail.example.json"):
        assert ok(_ex("compat/1.2/" + name), health), name
    assert ok(_ex("compat/1.2/split.example.json"), {"$ref": "setup/split.schema.json"})
    now = {(r["method"], r["path"], r.get("port")) for r in _routes()["routes"]}
    gone = [r["path"] for r in _ex("compat/1.2/routes.json")["routes"]
            if (r["method"], r["path"], r.get("port")) not in now]
    assert not gone, gone
    # the shapes the engine took over stay byte for byte where TandemApp's dashboard reads them
    dash, pairs = os.path.join(CONTRACT, "dashboard"), 0
    for name in sorted(os.listdir(dash)):
        with open(os.path.join(dash, name), "rb") as a, \
                open(os.path.join(ROOT, "contracts", "dashboard-v1", name), "rb") as b:
            assert a.read() == b.read(), f"contracts/dashboard-v1/{name} is not the engine's dashboard/{name}"
        pairs += 1
    with open(os.path.join(CONTRACT, "decision", "systemone.schema.json"), "rb") as a, \
            open(os.path.join(ROOT, "contracts", "conductor-v1", "systemone.schema.json"), "rb") as b:
        assert a.read() == b.read(), "conductor-v1/systemone.schema.json"
    # what leaves the engine and what an extension serves; no caller of a planned route yet
    doc = _routes()
    assert {e["after_eng413"] for e in doc["app_routes_via_engine"]} == {"leaves", "extension"}
    planned = [p["path"] for p in doc["planned"] if p["kind"] == "route"]
    callers = [f"{rel}:{ln} {lit}" for rel in list(CLIENTS) + ui_files()
               for ln, lit in literals_of(rel) if any(lit.startswith(p.rstrip("*")) for p in planned)]
    assert not callers, callers
    return f"1.0/1.1/1.2 answers valid, {len(now)} routes, {pairs} dashboard files equal, " \
           f"{len(planned)} planned routes uncalled"


def test_additive_1_4():
    def ok(doc, schema):
        return not contract_check.validate(doc, schema, root=CONTRACT)
    health = contract_check.load("health", CONTRACT)
    old = _ex("compat/1.3/health.example.json")
    for said in ("1.0", "1.1", "1.2", "1.3"):
        assert ok(dict(old, engine_api=said), health) and engineapi.read(dict(old, engine_api=said))["ok"]
    for name in ("health.stage-front.example.json", "health.stage-tail.example.json"):
        assert ok(_ex("compat/1.3/" + name), health), name
    now = {(r["method"], r["path"], r.get("port")) for r in _routes()["routes"]}
    gone = [r["path"] for r in _ex("compat/1.3/routes.json")["routes"]
            if (r["method"], r["path"], r.get("port")) not in now]
    assert not gone, gone
    ex = _ex("compat/1.4/health.example.json")
    assert ok(ex, health) and ex["engine_api"] == "1.4"
    (x,) = ex["extensions"]
    assert x["name"] == "tandemapp", x
    kinds = {a["what"].split()[-1] for a in _routes()["answers"]}
    assert {"no_ui", "not_on_engine"} <= kinds, kinds
    return f"1.0-1.3 answers valid, {len(now)} routes, 1.4's extensions names tandemapp {x['ext_api']}"


#: ENG-413 WP5: the screens the dashboard reads at the engine's /v1/engine/* since 1.5
OLD_SCREENS = ("/v1/conductor/residency/plan", "/v1/conductor/memory/disk",
               "/v1/conductor/deciders/")


def test_additive_1_5():
    def ok(doc, schema):
        return not contract_check.validate(doc, schema, root=CONTRACT)
    health = contract_check.load("health", CONTRACT)
    for name in ("health.example.json", "health.stage-front.example.json",
                 "health.stage-tail.example.json"):
        assert ok(_ex("compat/1.4/" + name), health), name
    old = _ex("compat/1.4/health.example.json")
    for said in ("1.0", "1.1", "1.2", "1.3", "1.4"):
        assert engineapi.read(dict(old, engine_api=said))["ok"], said
    doc = _routes()
    now = {(r["method"], r["path"], r.get("port")) for r in doc["routes"]}
    gone = [r["path"] for r in _ex("compat/1.4/routes.json")["routes"]
            if (r["method"], r["path"], r.get("port")) not in now]
    assert not gone, gone
    # group 9: each route replaces a screen the conductor served, and that screen leaves
    g9 = [r for r in doc["routes"] if r["group"] == 9]
    screens = {e["path"]: e for e in doc["engine_screens_until_eng413"]}
    assert len(g9) == 14 and all(r["path"].startswith("/v1/engine/") for r in g9), g9
    assert {r["replaces"] for r in g9} == set(screens), sorted({r["replaces"] for r in g9})
    assert all(e["leaves"].startswith("ENG-413 WP14") and e["after_eng413"].startswith("/v1/engine/")
               for e in screens.values())
    assert not [p for p in doc["planned"] if p.get("lands_with") == "ENG-413 WP5"]
    # the dashboard reads the engine's screens at the engine's paths only
    old_calls = [f"{rel}:{ln} {lit}" for rel in ui_files() for ln, lit in literals_of(rel)
                 if lit.startswith(OLD_SCREENS)]
    assert not old_calls, old_calls
    new_calls = {lit for rel in ui_files() for _ln, lit in literals_of(rel)
                 if lit.startswith("/v1/engine/")}
    assert len(new_calls) >= 6, sorted(new_calls)
    ex = _ex("health.example.json")
    assert ok(ex, health) and ex["engine_api"] == engineapi.SPEAKS == "1.5"
    with open(os.path.join(ROOT, "contracts", "engine-api-v1", "PIN")) as f:
        pin = dict(ln.split(None, 1) for ln in f.read().splitlines() if ln and not ln.startswith("#"))
    (x,) = ex["extensions"]
    assert x["name"] == "tandemapp" and x["ext_api"] == pin["ext_api"].strip(), (x, pin)
    return f"1.4 answers valid, {len(now)} routes, {len(g9)} in group 9, the dashboard reads " \
           f"{len(new_calls)} engine screen paths, extensions names tandemapp {x['ext_api']}"


if __name__ == "__main__":
    run(globals())
