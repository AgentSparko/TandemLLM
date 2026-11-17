"""The engine-only screens as the engine's own routes (ENG-413 WP5, ENG-433, QA ENG-434; engine API 1.5,
group 9): server/engine_routes.py, served at /v1/engine/* and handed to an extension in process.

  1  the handlers over a server's state, no engine: the decision models (off: status says so, the
     rest 404 with the engine's text; a registry: test, autoload, load allowed and refused, unload,
     a model it does not serve, an unknown route naming its base), the narrator (off with the right
     text whether its model is set or not, try's bounds, a role that is off, a load queued behind
     the decision models, load and unload with the keeper's switch), the shadow reader (off, ask's
     bounds, an answer, a refusal), the disk tier (off: its settings, clear 409 not_running), the
     residency plan (headless: the built-in documents, version 0, no journal; with an extension:
     its document and version, the journal in its directory)       test_the_handlers
  2  the access rule of each route, the bound handlers of in_process(state) (host.engine), and
     the paths each answers 404                                     test_the_route_table
  3  nothing of TandemApp: server/engine_routes.py and server/conductor/inproc.py import no
     tandemapp, proxy or chatd module, and a fresh process importing both loads none
                                                                    test_no_tandemapp
  4  a headless fake engine serves group 9 over HTTP: each GET 200 against its schema in
     contracts/engine-api-v1/engine/ with Cache-Control no-store, disk/clear 409 not_running, a
     narrator load 404 (no narrator), an unknown path 404; with an admin token set a proxied
     caller without it gets 401 for the disk and the admin token passes
                                                                    test_a_headless_engine_serves_them
  5  chat mode (QSE_EXT=tandemapp.engine_ext, a conductor directory): /v1/engine/* and the
     conductor's old paths answer the same body (the old one with its contract_version) for the
     residency plan, the disk, the decision models, the narrator and the shadow reader; disk/clear's
     refusal is the same bytes; the residency plan's version is the stored document's
                                                                    test_both_paths_answer_alike

CPU (the fake engine's server imports torch); without torch 4 and 5 print `skipped: no torch`.
Run: python tests/test_engine_routes.py
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import types
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import pairapp  # noqa: E402,F401  (a pair test: TandemApp from TANDEMAPP_SRC, ENG-413 WP14)
CONTRACT = os.path.join(ROOT, "contracts", "engine-api-v1")
ADMIN = "routes-" + "0" * 40
LAUNCH = ("import sys, runpy; sys.modules['triton'] = None; sys.modules['triton.language'] = None;"
          "sys.argv = ['server/app.py'] + sys.argv[1:]; runpy.run_path('server/app.py', "
          "run_name='__main__')")


def _refused(fn, *args):
    from server.ext import Refusal
    try:
        fn(*args)
    except Refusal as exc:
        return exc.status, exc.type, exc.message
    raise AssertionError(f"{fn.__name__}{args!r} did not refuse")


class Reg:
    """The decision models' registry as the screen calls it."""

    def __init__(self):
        self.models, self.order, self.state = {"a": 1, "b": 1}, ["a", "b"], {"a": "loaded", "b": "not_loaded"}
        self.refuse, self.busy = False, False

    def autoload(self, service_start=False, hold_restart=False):
        return ["a"] if service_start else []

    def load(self, name):
        return {"allowed": not self.refuse, "reason": "refused_memory" if self.refuse else None}

    def unload(self, name):
        return self.state[name] == "loaded"

    def loading(self):
        return self.busy


class NSlot:
    def __init__(self):
        self.state, self.loads = "not_loaded", []

    def load(self, service_start=False, hold_restart=False):
        self.loads.append((service_start, hold_restart))
        self.state = "loading"
        return {"allowed": True, "reason": None}

    def unload(self):
        if self.state != "loaded":
            return False
        self.state = "not_loaded"
        return True

    def status(self):
        return {"state": self.state}


class Narr:
    def __init__(self, mode="on"):
        self.slot, self.mode = NSlot(), mode

    def settings(self):
        return {"mode": self.mode, "budget_ms": 150}

    def status(self):
        return {"enabled": True, "why": None, "settings": self.settings(), "slot": self.slot.status()}

    def try_events(self, events, budget_ms=None, max_tokens=None):
        return [{"line": e.get("kind")} for e in events]


class Ext:
    """An extension as server/ext.py asks it: its documents."""

    def __init__(self, docs):
        self.docs = docs

    def ask(self, name, *args):
        if name == "document":
            return self.docs.get(args[0])
        return None


# ---------------------------------------------------------------------------- 1, 2, 3

def test_the_handlers():
    from server import decider_api, engine_routes as R
    from server.api import seeds
    from server.conductor import shadow as shadow_mod
    # the decision models
    st = {}
    code, body = R.deciders(st, "GET", ["status"], {})
    assert code == 200 and body["enabled"] is False and body["why"] == decider_api.OFF_WHY, body
    assert _refused(R.deciders, st, "POST", ["test"], {}) == (404, "not_found", decider_api.OFF_WHY)
    reg = st["deciders"] = Reg()
    assert R.deciders(st, "POST", ["autoload"], {"service_start": True}) == (202, {"queued": ["a"]})
    assert R.deciders(st, "POST", ["models", "b", "load"], {})[0] == 202
    reg.refuse = True
    code, body = R.deciders(st, "POST", ["models", "b", "load"], {})
    assert code == 409 and body["error"]["type"] == "refused_memory" and body["decision"]["allowed"] is False
    assert R.deciders(st, "POST", ["models", "a", "unload"], {}) == (200, {"name": "a", "state": "loaded"})
    assert _refused(R.deciders, st, "POST", ["models", "b", "unload"], {})[:2] == (409, "not_loaded")
    assert _refused(R.deciders, st, "POST", ["models", "z", "load"], {})[0] == 404
    assert _refused(R.deciders, st, "POST", ["models", "a", "x"], {}, "/v1/conductor/deciders") == \
        (404, "not_found", "no route POST /v1/conductor/deciders/models/a/x")
    # the narrator
    st = {}
    old = os.environ.pop("QWEN38_NARRATOR_MODEL", None)
    try:
        assert R.narrator(st, "GET", "status", {}) == (200, {"enabled": False, "why": R.NARRATOR_OFF_WHY})
        os.environ["QWEN38_NARRATOR_MODEL"] = "qwen3.5-0.8b"
        assert R.narrator(st, "GET", "status", {})[1]["why"] == R.NARRATOR_NO_EXT_WHY
        assert _refused(R.narrator, st, "POST", "load", {})[:2] == (404, "not_found")
    finally:
        os.environ.pop("QWEN38_NARRATOR_MODEL", None)
        if old is not None:
            os.environ["QWEN38_NARRATOR_MODEL"] = old
    n = st["narrator"] = Narr()
    assert R.narrator(st, "GET", "status", {})[1]["enabled"] is True
    assert R.narrator(st, "POST", "try", {"events": [{"kind": "route"}]})[1]["lines"] == [{"line": "route"}]
    for bad in ({"events": []}, {"events": [{}] * 65}, {"events": [{}], "budget_ms": 0},
                {"events": [{}], "max_tokens": 65}, {"events": [{}], "budget_ms": True}):
        assert _refused(R.narrator, st, "POST", "try", bad)[:2] == (400, "bad_request"), bad
    assert _refused(R.narrator, st, "POST", "load", {"service_start": "yes"})[:2] == (400, "bad_request")
    n.mode = "off"
    code, body = R.narrator(st, "POST", "load", {})
    assert code == 409 and body["error"]["type"] == "role_off" and not st.get("narrator_keep")
    n.mode = "on"
    st["deciders"] = Reg()
    st["deciders"].busy = True
    assert R.narrator(st, "POST", "load", {}) == (202, {"state": "not_loaded", "queued": True})
    assert st["narrator_keep"] is True and n.slot.loads == []
    st["deciders"].busy = False
    code, body = R.narrator(st, "POST", "load", {"service_start": True})
    assert code == 202 and n.slot.loads == [(True, False)]
    assert _refused(R.narrator, st, "POST", "unload", {})[:2] == (409, "not_loaded")
    n.slot.state = "loaded"
    assert R.narrator(st, "POST", "unload", {}) == (200, {"state": "not_loaded"}) and st["narrator_keep"] is False
    assert _refused(R.narrator, st, "POST", "nope", {}, "/v1/conductor/narrator")[2] == \
        "no route POST /v1/conductor/narrator/nope"
    # the shadow reader
    st = {}
    assert R.shadow(st, "GET", "status", {}) == (200, {"enabled": False, "why": shadow_mod.OFF_WHY})
    assert _refused(R.shadow, st, "POST", "ask", {})[2] == shadow_mod.OFF_WHY
    asked = []
    st["shadow"] = types.SimpleNamespace(
        slot=types.SimpleNamespace(), status=lambda: {"enabled": True},
        ask=lambda key, q, ch, budget_ms: asked.append(key) or ({"why": "unknown_session"} if key == "x"
                                                                 else {"label": "a", "confidence": 1.0}))
    good = {"session": "s", "question": "which one?", "choices": {"a": "A", "b": "B"}}
    assert R.shadow(st, "POST", "ask", good) == (200, {"label": "a", "confidence": 1.0})
    code, body = R.shadow(st, "POST", "ask", dict(good, session="x"))
    assert code == 409 and body["error"]["type"] == "unknown_session"
    for bad in ({**good, "session": ""}, {**good, "question": "?"}, {**good, "choices": {"a": "A"}},
                {**good, "budget_ms": 0}):
        assert _refused(R.shadow, st, "POST", "ask", bad)[:2] == (400, "bad_request"), bad
    assert asked == ["s", "x"]
    # the disk tier, off
    st = {"args": {"disk_cache": "off"}}
    code, body = R.disk(st)
    assert code == 200 and body["running"] is False and body["settings_version"] is None
    assert _refused(R.disk_clear, st) == (409, "not_running", "the disk tier is not running on this server")
    st["ext"] = Ext({"settings": (4, {"memory": {"disk_cache": {"mode": "off", "budget_gb": 9}}})})
    assert R.disk(st)[1]["settings_version"] == 4
    # the residency plan: headless, then with the extension's documents and directory
    st = {}
    code, body = R.residency_plan(st)
    assert code == 200 and body["version"] == 0 and body["doc"] == seeds.seed("residency"), body
    assert body["journal"] == [] and body["last_refusal"] is None and body["plan"]["rows"]
    root = tempfile.mkdtemp(prefix="engine-routes-")
    try:
        doc = dict(seeds.seed("residency"), floor_gib=11.0)
        st = {"ext": Ext({"residency": (7, doc), "roles": (2, seeds.seed("roles"))}), "ext_root": root}
        from server.conductor import residency
        residency.Journal(root).write({"kind": "check", "role": "decider", "allowed": False,
                                       "reason": "refused_memory"})
        code, body = R.residency_plan(st)
        assert body["version"] == 7 and body["doc"]["floor_gib"] == 11.0 and body["plan"]["floor_gib"] == 11.0
        assert len(body["journal"]) == 1 and body["last_refusal"]["reason"] == "refused_memory"
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return "deciders, narrator, shadow, disk and the residency plan answer as the screens did"


def test_the_route_table():
    from server import engine_routes as R
    want = {("GET", "residency/plan"): ("residency_plan", "dashboard"),
            ("GET", "disk"): ("disk", "conductor_write"),
            ("POST", "disk/clear"): ("disk_clear", "conductor_write"),
            ("GET", "deciders/status"): ("deciders", "dashboard"),
            ("POST", "deciders/test"): ("deciders", "dashboard"),
            ("POST", "deciders/autoload"): ("deciders", "conductor_write"),
            ("POST", "deciders/models/a/load"): ("deciders", "conductor_write"),
            ("GET", "narrator/status"): ("narrator", "dashboard"),
            ("POST", "narrator/try"): ("narrator", "conductor_write"),
            ("POST", "narrator/load"): ("narrator", "conductor_write"),
            ("GET", "shadow/status"): ("shadow", "dashboard"),
            ("POST", "shadow/ask"): ("shadow", "conductor_write")}
    for (method, path), (name, rule) in want.items():
        got = R._route(method, path.split("/"))
        assert got is not None and (got[0], got[2]) == (name, rule), (method, path, got)
    for method, path in (("GET", "residency"), ("POST", "residency/plan"), ("POST", "disk"),
                         ("GET", "disk/clear"), ("GET", "deciders/other"), ("GET", "narrator"),
                         ("GET", "shadow/a/b"), ("GET", "nope"), ("GET", "")):
        assert R._route(method, [p for p in path.split("/") if p]) is None, (method, path)
    st = {"deciders": None}
    bound = R.in_process(st)
    assert set(bound) == {"residency_plan", "disk", "disk_clear", "deciders", "narrator", "shadow"}
    assert bound["deciders"]("GET", ["status"], {})[1]["enabled"] is False
    assert bound["deciders"]("GET", ["status"], {}, "/v1/conductor/deciders")[0] == 200
    with open(os.path.join(CONTRACT, "routes.json")) as f:
        routes = json.load(f)
    g9 = [r for r in routes["routes"] if r["group"] == 9]
    for r in g9:
        parts = r["path"][len(R.PREFIX):].replace("{name}", "a").split("/")
        assert R._route(r["method"], parts) is not None, r["path"]
    return f"{len(want)} routes with their rules, {len(g9)} of routes.json group 9 dispatched"


def test_no_tandemapp():
    bad = []
    for rel in ("server/engine_routes.py", "server/conductor/inproc.py"):
        with open(os.path.join(ROOT, rel)) as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            bad += [f"{rel}: {n}" for n in names if n.split(".")[0] in ("tandemapp", "proxy", "chatd")]
    assert not bad, bad
    code = ("import sys; sys.path.insert(0, sys.argv[1]); sys.modules['triton'] = None\n"
            "import server.engine_routes, server.conductor.inproc\n"
            "print(sorted(m for m in sys.modules if m.split('.')[0] in ('tandemapp', 'proxy', 'chatd')))")
    out = subprocess.run([sys.executable, "-c", code, ROOT], capture_output=True, text=True, timeout=120,
                         env={**os.environ, "PYTHONPATH": ""})
    assert out.returncode == 0 and out.stdout.strip().splitlines()[-1] == "[]", (out.stdout, out.stderr[-2000:])
    return "no TandemApp import, none loaded"


# ---------------------------------------------------------------------------- 4, 5

class Engine:
    """server/app.py --fake-engine on a free port, headless or in chat mode."""

    def __init__(self, chat: bool = False, admin: str | None = None):
        from ports import free_port
        self.port = free_port()
        assert self.port not in (8000, 8009), self.port
        self.tmp = tempfile.mkdtemp(prefix="engine-routes-")
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": home, "PYTHONPATH": os.pathsep.join(p for p in (ROOT, os.environ.get("PYTHONPATH")) if p),
               "CUDA_VISIBLE_DEVICES": "", "TMPDIR": self.tmp}
        if admin:
            env["QSE_ADMIN_TOKEN"] = admin
        args = ["--fake-engine", "--port", str(self.port), "--served-model", "tandem-routes",
                "--fake-tps", "400", "--usage-ledger", os.path.join(self.tmp, "ledger.sqlite3")]
        if chat:
            env["QSE_EXT"] = "tandemapp.engine_ext"
            args += ["--conductor-config", os.path.join(self.tmp, "conductor")]
        self.log = open(os.path.join(self.tmp, "engine.log"), "w")
        self.proc = subprocess.Popen([sys.executable, "-c", LAUNCH, *args], cwd=ROOT, env=env,
                                     stdout=self.log, stderr=subprocess.STDOUT)
        end = time.time() + 150
        while time.time() < end:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=2)
                return
            except OSError:
                if self.proc.poll() is not None:
                    break
                time.sleep(0.3)
        self.close()
        raise AssertionError("the fake engine did not come up")

    def call(self, method: str, path: str, body=None, token=None, headers=None):
        h = dict(headers or {})
        if token:
            h["Authorization"] = f"Bearer {token}"
        data = None
        if body is not None:
            data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method=method,
                                     headers=h)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), dict(exc.headers)

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        self.log.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


def _torch() -> bool:
    try:
        import torch  # noqa: F401
        return True
    except ImportError:
        return False


def test_a_headless_engine_serves_them():
    if not _torch():
        return "skipped: no torch"
    from tools import contract_check
    e = Engine(admin=ADMIN)
    try:
        n = 0
        for path, schema in (("residency/plan", "residency-plan"), ("disk", "disk"),
                             ("deciders/status", "deciders-status"), ("narrator/status", "narrator-status"),
                             ("shadow/status", "shadow-status")):
            st, raw, hd = e.call("GET", "/v1/engine/" + path, token=ADMIN)
            assert st == 200 and hd.get("Cache-Control") == "no-store", (path, st, raw[:300])
            errs = contract_check.validate(json.loads(raw), contract_check.load(f"engine/{schema}", CONTRACT),
                                           root=os.path.join(CONTRACT, "engine"))
            assert not errs, (path, errs[:3])
            n += 1
        st, raw, _ = e.call("POST", "/v1/engine/disk/clear", {}, token=ADMIN)
        assert st == 409 and json.loads(raw) == {"error": {"type": "not_running", "message":
                                                           "the disk tier is not running on this server"}}
        st, raw, _ = e.call("POST", "/v1/engine/narrator/load", {}, token=ADMIN)
        assert st == 404 and json.loads(raw)["error"]["type"] == "not_found", raw
        st, raw, _ = e.call("GET", "/v1/engine/nope", token=ADMIN)
        assert st == 404 and "no route GET /v1/engine/nope" in json.loads(raw)["error"]["message"]
        proxied = {"X-Forwarded-For": "192.0.2.7"}
        assert e.call("GET", "/v1/engine/disk", headers=proxied)[0] == 401
        assert e.call("GET", "/v1/engine/disk", headers=proxied, token=ADMIN)[0] == 200
        st, raw, _ = e.call("GET", "/health")
        assert json.loads(raw)["engine_api"] == "1.9" and json.loads(raw)["extensions"] == []
    finally:
        e.close()
    return f"{n} reads valid and no-store, clear 409, 404s, 401 without the token behind a proxy"


def test_both_paths_answer_alike():
    if not _torch():
        return "skipped: no torch"
    e = Engine(chat=True)
    try:
        st, raw, _ = e.call("GET", "/health")
        assert [x["ext_api"] for x in json.loads(raw)["extensions"]] == ["3.2"], raw
        pairs = (("GET", "/v1/engine/residency/plan", "/v1/conductor/residency/plan"),
                 ("GET", "/v1/engine/disk", "/v1/conductor/memory/disk"),
                 ("GET", "/v1/engine/deciders/status", "/v1/conductor/deciders/status"),
                 ("GET", "/v1/engine/narrator/status", "/v1/conductor/narrator/status"),
                 ("GET", "/v1/engine/shadow/status", "/v1/conductor/shadow/status"))
        for method, new, old in pairs:
            a, b = e.call(method, new), e.call(method, old)
            assert a[0] == b[0] == 200, (new, a[:2], b[:2])
            got, was = json.loads(a[1]), json.loads(b[1])
            assert was.pop("contract_version") == "1.0" and "contract_version" not in got, new
            if new.endswith("residency/plan"):
                for d in (got, was):                         # MemAvailable moves between the two reads
                    d["plan"].pop("mem_available_gib", None)
                    d["plan"].pop("spendable_gib", None)
                    d["plan"].pop("rows", None)
                    d.pop("engine", None)
                    d.pop("limits", None)
            if new.endswith("disk"):
                got.pop("free_bytes", None), was.pop("free_bytes", None)
            assert got == was, (new, got, was)
        a = e.call("POST", "/v1/engine/disk/clear", {})
        b = e.call("POST", "/v1/conductor/memory/disk/clear", {})
        assert a[0] == b[0] == 409 and a[1] == b[1], (a[:2], b[:2])
        a = e.call("POST", "/v1/engine/deciders/models/x/load", {})
        b = e.call("POST", "/v1/conductor/deciders/models/x/load", {})
        assert a[0] == b[0] == 404 and a[1] == b[1], (a[:2], b[:2])      # no decision models: the same text
        st, raw, _ = e.call("GET", "/v1/engine/residency/plan")
        assert json.loads(raw)["version"] == 0                          # the stored one: none yet
    finally:
        e.close()
    return f"{len(pairs)} reads alike on both paths, the refusals the same bytes"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
