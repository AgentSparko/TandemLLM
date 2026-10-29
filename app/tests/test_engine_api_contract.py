"""The engine API contract v1, TandemApp's side (SRV-748): contracts/engine-api-v1 against TandemApp's
callers. The engine's side is the repository's tests/test_engine_api_contract.py.

  1  routes.json is well formed: its version is proxy.engineapi.SPEAKS, every route has a method,
     a group, a shape that exists, and callers or a named consumer; the lists are disjoint
                                                                  test_routes_json
  2  every engine path literal is classified: in the Python callers (nodeengines, nodesource,
     nodeagent, nodeweights, placement, fleetsetup, health, service, relay, deployments,
     tandemapp/parallel, conductor/classify, chatd engine/agent/service/skills/tools,
     tools/tandem_eval) and in every string or template literal of dashboard/src that starts
     with /v1/, /health, /metrics, /readyz, /_tandem, /s/ or /dashboard/ (`${...}` is {param},
     the query is dropped, a base matches as a prefix, all of it when text follows a {param}),
     each falls in exactly one of routes, engine_screens_until_eng413, app_routes_via_engine
     and app_families, or the test names file:line; planted unknown paths are caught
                                                                  test_every_literal_is_classified
  3  every route is used: a file of its used_by holds a literal for it, or a relay prefix
     covers it; nodeengines.RELAY is in the routes               test_every_route_is_used
  4  the version: a missing field reads 1.0 (assumed, ok), 1.4 is ok, 2.0 is not and names 1.x,
     a value not in the plain MAJOR.MINOR form is not ok and its reason quotes it, another kind
     is not read; nodeengines.health against fake engines that say nothing, 1.4, 2.0 and
     garbage; the health example validates against its schema   test_versions
  5  the node fixtures: nodeengines.default_engine reads node/serve.env.example's port and builds
     node/control.json's verbs; nodeweights reads a folder laid out as
     node/hub_manifest.example.json says (files_of, the sha256 per file, the content id)
                                                                  test_node_fixtures

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
           "proxy/service.py", "proxy/relay.py", "proxy/deployments.py", "tandemapp/parallel.py",
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
        if r.get("group") not in range(1, 9):
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
        want = {"unsaid": ("up", "1.0", True, True, None), "v14": ("up", "1.4", False, True, None),
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
    return f"read: 1.0 assumed, 1.4 ok, 2.0 names 1.x; {len(want)} engines viewed"


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
        assert set(e) - {"id", "kind", "port"} == set(control["verbs"]), e
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


if __name__ == "__main__":
    run(globals())
