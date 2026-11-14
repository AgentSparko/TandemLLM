"""The request path through the engine-ext-v1 hooks (ENG-413 WP3b, QA ENG-430).

server/app.py asks the conductor and its session monitor only through the extension: the hooks
(plan, prompt, supervisor and the decode loop's safe points, extras, follow_ups, finish) and the
interim calls server/ext.py names. tests/test_ext_trace.py holds the tokens and the journal equal;
this file holds the seam itself.

  1  the import guard: server/app.py imports TandemApp only where ALLOWED says why: since WP5b only
     what WP14 removes (the --backends proxy, the page, the chat-app forward; the finder in
     server/__init__.py works by path) (WP5 took out the routes, the store and the monitor; WP5b
     Laya, the session addresses, the loop escape and the shadow's question), by statement or by
     name; no other file under server/ imports any of TandemApp
                                                              test_the_request_path_imports_no_conductor
  2  the optional calls (engine-ext-v1 2.0, 3.0): every name of CALLS and SUP_OPTIONAL is the
     extension's; a headless engine (NULL) answers each with its headless value and asks nothing;
     the act kinds are the monitor's; the own questions' User-Agent is TandemApp's
                                                              test_the_optional_calls_are_named
  3  a failing safe point in the decode loop (in process): SP1 (`_sp1`), SP2 (`_sp2`), SP3
     (`_sp3_calls`) and SP4 (`at_request`), each raising and each answering an act its method
     does not allow: no act, the supervisor detached (nothing more asked, `done` not asked),
     counted in qse_ext_errors_total{hook}; SP3's held calls go out as a headless engine sends them
                                                              test_a_failing_safe_point_detaches
  4  over the fake engine, chat mode, an extension around the adapter that fails one method:
     SP4 (C3's note), SP1 (D1's close), SP2 (E1's continue) and the hooks plan, prompt, extras,
     follow_ups and finish: every request 200, the act or row that method makes absent from the
     journal, the counter on /metrics for each request (the next request asks again)
                                                              test_a_failing_hook_serves_the_request_on
  5  `--conductor on` without a conductor directory needs the extension: QSE_EXT empty is refused
     before the weights load; a headless engine's /health still reports the loop escape's
     sessions; with the adapter the monitor alone runs, engine mode, the adapter listed
                                                              test_the_monitor_needs_the_extension

Run: python tests/test_ext_request_path.py   (3-5 need CPU torch)
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import types
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from server import ext as E  # noqa: E402

#: the extension chat mode loads (ops/serve.env)
EXT_NAME = "tandemapp.engine_ext"

LAUNCH = ("import sys, runpy; sys.modules['triton'] = None; sys.modules['triton.language'] = None;"
          "sys.argv = ['server/app.py'] + sys.argv[1:]; runpy.run_path('server/app.py', run_name='__main__')")
RAW = "qwen38-spark-engine"

#: (TandemApp module, the server/app.py function it is imported in; None: the module's top) -> why
#: ENG-413 WP5 took out the conductor's routes (the screens are /v1/engine/*), its store (the
#: fake-engine guard's directory is a value copy, the residency limits come through the module's
#: read_document) and its monitor (the policy is checked by the module's before_load)
ALLOWED = {
    ("tandemapp.static", None): "WP14: setup mode's page",
    ("tandemapp.chat_forward", None): "WP14: /v1/chat-app leaves the engine",
}
#: what WP5 took out of server/app.py: none of it may come back
GONE_WP5 = ("tandemapp.conductor.routes", "tandemapp.conductor.store", "tandemapp.conductor.monitor")
#: what WP5b took out of server/: Laya (the extension's route), the session addresses, the loop
#: escape and the shadow reader's question form (the engine's server/api copies), the router's
#: question (the extension's shadow_question); none of it may come back
GONE_WP5B = ("tandemapp.laya_api", "tandemapp.session_tokens", "tandemapp.conductor.loopescape",
             "tandemapp.conductor.incontext", "tandemapp.conductor.inject", "tandemapp.conductor.schema")
#: ENG-413 WP5b, the G5 list: the only engine files under server/ that may import TandemApp, each
#: with the modules WP14 removes with it
WP14 = {"server/app.py": ("proxy", "tandemapp.static", "tandemapp.chat_forward")}
#: --backends: the proxy in the engine's process, with the engine's conductor as its harness (it
#: leaves the engine with the controller, WP7/WP14); none of these functions is the request path
for _fn in ("start_proxy", "do_POST", "_proxied", "_config", "_hosts", "_system", "_cache_peek"):
    ALLOWED[("proxy", _fn)] = "--backends: the proxy in the engine's process (WP7/WP14)"


def _allowed(mod: str, fn) -> bool:
    return any((mod == m or mod.startswith(m + ".")) and fn == f for m, f in ALLOWED)


def _imports(path: str) -> set:
    """(module, enclosing function or None) of every import of a TandemApp module in `path`,
    statements and importlib/__import__ names alike."""
    with open(os.path.join(ROOT, path)) as f:
        tree = ast.parse(f.read())
    out = set()

    def visit(node, fn):
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn
            mods = []
            if isinstance(child, ast.Import):
                mods = [a.name for a in child.names]
            elif isinstance(child, ast.ImportFrom) and child.module:
                mods = [f"{child.module}.{a.name}" for a in child.names]
            elif isinstance(child, ast.Call) and child.args and isinstance(child.args[0], ast.Constant) \
                    and isinstance(child.args[0].value, str) and (
                        getattr(child.func, "attr", None) == "import_module"
                        or getattr(child.func, "id", None) == "__import__"):
                mods = [child.args[0].value]
            for m in mods:
                if m.split(".")[0] in ("tandemapp", "proxy", "chatd"):
                    out.add((m, fn))
            visit(child, name)

    visit(tree, None)
    return out


def test_the_request_path_imports_no_conductor():
    got = _imports("server/app.py")
    bad = sorted((x for x in got if not _allowed(*x)), key=str)
    got = sorted(got, key=str)
    assert not bad, f"server/app.py imports TandemApp outside ALLOWED: {bad}"
    back = sorted(m for m, _ in got if m.startswith(GONE_WP5))
    assert not back, f"WP5 took these out of server/app.py: {back}"
    back = sorted(m for m, _ in got if m.startswith(GONE_WP5B))
    assert not back, f"WP5b took these out of server/app.py: {back}"
    # the G5 list: no other file under server/ imports TandemApp; server/app.py only what WP14 removes
    seen = {}
    for base, dirs, names in os.walk(os.path.join(ROOT, "server")):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for n in sorted(names):
            if n.endswith(".py"):
                rel = os.path.relpath(os.path.join(base, n), ROOT).replace(os.sep, "/")
                mods = {m for m, _ in _imports(rel)}
                if mods:
                    seen[rel] = sorted(mods)
    assert set(seen) == set(WP14), seen
    for rel, mods in seen.items():
        out = [m for m in mods if not any(m == w or m.startswith(w + ".") for w in WP14[rel])]
        assert not out, (rel, out)
    for path in ("server/conductor/inproc.py", "server/conductor/residency.py", "server/decider_api.py",
                 "server/engine_routes.py", "server/memlimits.py", "server/diskcache.py",
                 "server/rolemem.py"):
        assert not _imports(path), (path, _imports(path))         # WP5: the engine's own
    import io
    import tokenize
    with open(os.path.join(ROOT, "server", "app.py")) as f:
        toks = tokenize.generate_tokens(io.StringIO(f.read()).readline)
        text = " ".join(t.string for t in toks if t.type not in (tokenize.COMMENT, tokenize.STRING))
    text = text.replace(" . ", ".")
    # nothing of the conductor's reached through a name the module once imported it as (code only)
    for alias in ("states_mod", "voice_mod", "narrator_mod", "loopescape_mod", "redact_mod",
                  "conductor_schema", "conductor_memory", "conductor_outcomes", "incontext_mod",
                  "memblock.", "CLASSIFY_UA", "COMPOSITE_NAME", "ConductorConfigError",
                  "SIDE_FIRST_MAX_TOKENS", "conductor.BlockEvent", "conductor.FinishEvent",
                  "conductor.RequestEvent", "conductor.Monitor"):
        assert alias not in text, alias
    for path in ("server/ext.py", "server/round_runner.py", "server/fake_engine.py"):
        assert not _imports(path), (path, _imports(path))
    # ENG-413 WP4: no engine file outside app/ is the adapter any more; TandemApp's extension (app/,
    # vendored) imports the conductor's request-path modules and nothing of the engine
    assert not os.path.exists(os.path.join(ROOT, "server", "ext_app.py"))
    ext = {m for m, _ in _imports("app/tandemapp/engine_ext.py")}
    for m in ("tandemapp.conductor.monitor", "tandemapp.conductor.states", "tandemapp.memblock.TOOL",
              "tandemapp.conductor.loopescape", "tandemapp.conductor.redact"):
        assert any(x == m or x.startswith(m + ".") or m.startswith(x) for x in ext), (m, sorted(ext))
    with open(os.path.join(ROOT, "app", "tandemapp", "engine_ext.py")) as f:
        tree = ast.parse(f.read())
    mods = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            mods.append(n.module or "")
    engine = sorted(m for m in mods if m.split(".")[0] in ("server", "engine", "models"))
    assert not engine, engine
    return f"server/app.py: {len(got)} TandemApp imports, each ALLOWED; the request path asks the extension"


def test_the_optional_calls_are_named():
    import importlib
    A = importlib.import_module(EXT_NAME)
    host = E.Host(mode="chat", engine_api="", state={}, lock=None, tokenizer=None, registry=None,
                  ledger_path=None, args={})
    obj = A.attach(host)
    names = set(E.CALLS)                     # 3.0: the writer is memory_writer, a call
    have = {n for n in names if callable(getattr(obj, n, None))}
    assert names == have and A.EXT_API == E.EXT_API, (sorted(names - have), A.EXT_API)
    watch = {n for n in vars(A.Watch) if not n.startswith("_")}
    sup_methods = set(E.SUPERVISOR)
    assert sup_methods <= watch, sorted(sup_methods - watch)
    for name in E.SUP_OPTIONAL:
        assert name in watch or hasattr(_monitor_supervisor(), name), name
    # headless: the documented values, nothing asked
    plan = types.SimpleNamespace(active=True)
    assert E.NULL.ask("server_mode", "on", 1) == "raw"
    assert E.NULL.ask("strip_states", [{"role": "user"}]) == ([{"role": "user"}], 0)
    assert E.NULL.ask("replayed", "s", [1, 2]) == [1, 2]
    assert E.NULL.ask("served", plan, None, False) is None
    assert E.NULL.ask("memory_writer") == (None, None) and E.NULL.ask("document", "router") is None
    assert E.NULL.ask("memory_spans", [1], None, []) == ([], 0)
    assert E.NULL.ask("code_class", plan) is False and E.NULL.ask("served_name") is None
    r = E.NULL.request({})
    assert r.begin() is None and r.prompt([3], True, conv="c") == ([3], True, None)
    assert r.supervise({}) is None and r.extras("start", {}) == [] and r.follow_ups({}) == []
    # the act kinds are the monitor's, the own questions' User-Agent is TandemApp's
    from tandemapp.conductor.monitor import policy
    from tandemapp.conductor.classify import USER_AGENT
    assert set(E.ACT_KINDS) <= set(policy.ACTIONS) and all(getattr(policy, k) == k for k in E.ACT_KINDS)
    with open(os.path.join(ROOT, "server", "app.py")) as f:
        m = re.search(r'^OWN_QUESTION_UA = "([^"]+)"$', f.read(), re.M)
    assert m and m.group(1) == USER_AGENT, (m and m.group(1), USER_AGENT)
    return f"{len(E.CALLS)} calls and {len(E.SUP_OPTIONAL)} supervisor " \
           f"ones, each the extension's, each headless"


def _monitor_supervisor():
    from tandemapp.conductor.monitor import executor
    return executor.Supervisor


# ---------------------------------------------------------------------------- 3: in process

def _torch() -> bool:
    try:
        import torch  # noqa: F401
        return True
    except ImportError:
        return False


def _count(hook: str) -> float:
    from server.api import metrics
    return metrics.ext_errors_total.values.get((hook,), 0.0)


class _Sup:
    """A supervisor whose method `fail` raises, or answers `act` (a kind) instead; every call kept."""

    def __init__(self, fail=None, act=None):
        self.fail, self.act, self.calls = fail, act, []
        self.watches_blocks, self.checked, self.gave_up, self.cuts = True, False, None, []
        self.req = types.SimpleNamespace(tools_offered=False, n=1, request_id="r1")
        self.memory_notes = None

    def _step(self, name):
        self.calls.append(name)
        if self.fail == name:
            if self.act is not None:
                return {"kind": self.act, "ids": [1], "visible": [], "detail": {}}
            raise RuntimeError(f"{name} failed on purpose")
        return None

    def at_request(self, ev):
        got = self._step("at_request")
        return [got] if got is not None else []

    def at_block(self, ev, room):
        return self._step("at_block")

    def at_finish(self, ev, room):
        return self._step("at_finish")

    def check_answer(self, items, final=False):
        return self._step("check_answer")

    def done(self, outcome, detail):
        self._step("done")

    def as_sent(self, items, events=None):
        self.calls.append("as_sent")
        return items

    def release_call(self, call):
        self.calls.append("release_call")
        return {k: v for k, v in call.items() if not k.startswith("_")}

    def release_block(self, inner):
        return None

    def note_repairs(self, renamed):
        self.calls.append("note_repairs")


def test_a_failing_safe_point_detaches():
    if not _torch():
        from scriptmode import Skip
        raise Skip("no torch here: server/app.py needs CPU torch")
    from server import app
    think = types.SimpleNamespace(inside=True, hit=False, n=5, reason=None, share=None,
                                  closes_in=lambda extra: False, end_id=None)
    answer = "<tool_call>\n<function=bash>\n<parameter=command>\nls\n</parameter>\n</function>\n</tool_call>"
    # SP3's release by a supervisor that found nothing: what a detached one must send as well
    def no_ids(out):                                  # a call's id is drawn per parse
        return out[0], [{k: v for k, v in c.items() if k != "id"} for c in out[1]]

    want = no_ids(app._sp3_calls(E.Supervision(_Sup()), answer, True, ["bash"], None, []))
    assert len(want[1]) == 1 and want[1][0]["function"]["name"] == "bash", want
    words = []
    for act in (None, "PROMPT_TAIL_NOTE"):            # raising, and an act its method does not allow
        for point, method, run in (
                ("SP1", "at_block", lambda s: app._sp1(s, think, 3, 100)),
                ("SP2", "at_finish", lambda s: app._sp2(s, think, [1, 2, 3], 1, 0, 100)),
                ("SP3", "check_answer", lambda s: app._sp3_calls(s, answer, True, ["bash"], None, [])),
                ("SP4", "at_request", lambda s: s.at_request({"request_id": "r1"}))):
            if act == "PROMPT_TAIL_NOTE" and method == "at_request":
                act_ = "GIVE_UP"
            else:
                act_ = act
            obj = _Sup(fail=method, act=act_)
            sup = E.Supervision(obj)
            before = _count(method)
            got = run(sup)
            if method == "check_answer":
                assert no_ids(got) == want, (act_, got, want)  # released as nothing was found
            elif method == "at_request":
                assert got == [], got
            else:
                assert got is None, (method, got)
            assert sup.detached and _count(method) == before + 1, (method, act_)
            asked = list(obj.calls)
            sup.done("text", {"calls": [], "finish": "stop"})          # the request's end
            assert sup.at_block({}, 1) is None and sup.at_finish({}, 1) is None
            assert obj.calls == asked, (method, obj.calls)             # nothing more asked
            words.append(f"{point}{'+' + act_ if act_ else ''}")
    # an act its method allows is carried out and recorded; nothing is detached
    obj = _Sup(fail="at_block", act="CLOSE_REASONING")
    sup = E.Supervision(obj)
    got = app._sp1(sup, think, 3, 100)
    assert got["kind"] == "CLOSE_REASONING" and not sup.detached and sup.taken == [("SP1", "CLOSE_REASONING")]
    return "detached and counted at " + ", ".join(words)


# ---------------------------------------------------------------------------- 4, 5: the fake engine

FAILING = '''"""A test extension: TandemApp's extension with one method failing (QSE_EXT_ARGS {"fail": name})."""
from tandemapp import engine_ext as A

EXT_API, NAME, VERSION = A.EXT_API, "failing", "0"
read_document, before_load = A.read_document, A.before_load     # engine-ext-v1 3.0's module functions
HOOKS = ("plan", "prompt", "extras", "follow_ups", "finish")


class _Sup:
    def __init__(self, watch, fail):
        object.__setattr__(self, "_w", watch)
        object.__setattr__(self, "_fail", fail)

    def __getattr__(self, name):
        if name == object.__getattribute__(self, "_fail"):
            def boom(*a, **kw):
                raise RuntimeError("failing on purpose: " + name)
            return boom
        return getattr(object.__getattribute__(self, "_w"), name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_w"), name, value)


def start(host):
    obj = A.start(host)
    fail = (host.args or {}).get("fail")
    if fail in HOOKS:
        def boom(*a, **kw):
            raise RuntimeError("failing on purpose: " + fail)
        setattr(obj, fail, boom)
    elif fail:
        real = obj.supervisor

        def supervisor(plan, event):
            w = real(plan, event)
            return None if w is None else _Sup(w, fail)
        obj.supervisor = supervisor
    return obj
'''


def _engine(tmp: str, env_extra: dict, conductor: str | None, extra=()):
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": tmp, "CUDA_VISIBLE_DEVICES": "",
           "PYTHONPATH": os.pathsep.join((ROOT, tmp)), "TZ": "Europe/Berlin", **env_extra}
    args = [sys.executable, "-c", LAUNCH, "--fake-engine", "--host", "127.0.0.1", "--port", "0",
            "--served-model", RAW, "--usage-ledger", os.path.join(tmp, "ledger.sqlite3"),
            "--fake-tps", "4000", "--conductor", "on", "--disk-cache", "off",
            "--session-spill", "off", *extra]
    if conductor:
        args += ["--conductor-config", conductor, "--conductor-defaults", "shipped"]
    return subprocess.Popen(args, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True)


def _port(p) -> int:
    end, seen = time.time() + 120, []
    while time.time() < end:
        line = p.stdout.readline()
        if not line:
            if p.poll() is not None:
                raise AssertionError("".join(seen)[-3000:])
            continue
        seen.append(line)
        m = re.search(r"\[server\] listening on http://[0-9.]+:(\d+)", line)
        if m:
            import threading
            threading.Thread(target=lambda: [seen.append(x) for x in p.stdout], daemon=True).start()
            p._seen = seen
            return int(m.group(1))
    p.kill()
    raise AssertionError("no listening line in 120 s: " + "".join(seen)[-3000:])


def _stop(p) -> str:
    p.terminate()
    try:
        p.wait(30)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait(10)
    time.sleep(0.2)
    return "".join(getattr(p, "_seen", []))


def _get(port: int, path: str):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=60) as r:
        return r.status, r.read().decode()


def _fixture(name: str, model: str):
    """(name, body, headers) of the fixture that makes `name`'s act."""
    from tools import eng413_baseline as B
    tools = B._opencode_tools()
    base = {"model": model, "reasoning_format": "tags", "logprobs": True}
    hdr = {"User-Agent": B.OPENCODE_UA, "x-session-id": f"ses_{name}"}
    sysm = {"role": "system", "content": "You are opencode."}
    if name == "d1":
        return dict(base, stream=True, max_tokens=8000,
                    messages=[sysm, {"role": "user", "content": "FAKE_LOOP"}]), hdr
    if name == "e1":
        return dict(base, stream=True, max_tokens=400, tools=tools,
                    messages=[sysm, {"role": "user", "content": "use the tool FAKE_EMPTY_ANSWER"}]), hdr
    args = {"command": "python3 pagodacheck.py"}
    call = {"name": "bash", "arguments": json.dumps(args)}
    hist = []
    for i in range(3):
        hist += [{"role": "assistant", "content": "", "tool_calls": [
            {"id": f"call_{i}", "type": "function", "function": call}]},
                 {"role": "tool", "tool_call_id": f"call_{i}", "content": "RESULT: FAIL"}]
    return dict(base, stream=True, max_tokens=400, tools=tools, **B.NO_THINK,
                messages=[{"role": "user", "content": "FAKE_CALL_JSON " + json.dumps(
                    {"name": "bash", "arguments": args})}] + hist), dict(hdr, **{"x-session-id": "ses_c3"})


def _post(port: int, body: dict, headers: dict) -> int:
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers=dict({"Content-Type": "application/json"}, **headers))
    with urllib.request.urlopen(req, timeout=300) as r:
        r.read()
        return r.status


def _rows(ledger: str) -> tuple[list, list]:
    from tools import eng413_baseline as B
    j = B.read_journal(ledger)
    return j.get("conductor_events", []), j.get("requests", [])


#: the method that fails -> (the fixture, the journal's sign of that method's work)
CASES = {
    "at_request": ("c3", lambda ev: any(r.get("task") == "C3" and r.get("decision") == "PROMPT_TAIL_NOTE"
                                        for r in ev)),
    "at_block": ("d1", lambda ev: any(r.get("task") == "D1" and r.get("decision") == "CLOSE_REASONING"
                                      for r in ev)),
    "at_finish": ("e1", lambda ev: any(r.get("task") == "E1" and r.get("mode") == "act" for r in ev)),
    "plan": ("e1", lambda ev: any(r.get("kind") == "route" for r in ev)),
    "finish": ("e1", lambda ev: any(r.get("kind") == "route" for r in ev)),
    "prompt": ("e1", None),
    "extras": ("e1", None),
    "follow_ups": ("e1", None),
}


def test_a_failing_hook_serves_the_request_on():
    if not _torch():
        from scriptmode import Skip
        raise Skip("no torch here: the fake engine needs CPU torch")
    words = []
    for fail, (fixture, sign) in CASES.items():
        tmp = tempfile.mkdtemp(prefix=f"ext-fail-{fail}-")
        try:
            with open(os.path.join(tmp, "ext_failing.py"), "w") as f:
                f.write(FAILING)
            cdir = os.path.join(tmp, "conductor")
            os.makedirs(cdir)
            body, hdr = _fixture(fixture, "TandemLLM")
            # the control: the adapter as it is makes the act or row
            p = _engine(tmp, {"QSE_EXT": "tandemapp.engine_ext"}, cdir)
            port = _port(p)
            assert _post(port, body, hdr) == 200
            _stop(p)
            ev, _ = _rows(os.path.join(tmp, "ledger.sqlite3"))
            if sign is not None:
                assert sign(ev), (fail, "the control made no act")
            os.remove(os.path.join(tmp, "ledger.sqlite3"))
            # the failing extension: served, counted once a request, the act or row absent
            p = _engine(tmp, {"QSE_EXT": "ext_failing", "QSE_EXT_ARGS": json.dumps({"fail": fail})}, cdir)
            port = _port(p)
            codes = [_post(port, body, dict(hdr, **{"x-session-id": f"ses_{fail}_{i}"})) for i in range(2)]
            _, metrics = _get(port, "/metrics")
            log = _stop(p)
            n = {m.group(1): float(m.group(2)) for m in re.finditer(
                r'^qse_ext_errors_total\{hook="([a-z_.]+)"\} ([0-9.]+)$', metrics, re.M)}
            assert codes == [200, 200], (fail, codes, log[-2000:])
            # every request that reaches the method counts it: the two, and with `finish` the router's
            # own questions to this engine too; the next request is asked again
            assert n.get(fail, 0) >= 2.0, (fail, n, log[-2000:])
            assert f"[ext] {fail} failed" in log, (fail, log[-2000:])
            ev, reqs = _rows(os.path.join(tmp, "ledger.sqlite3"))
            if sign is not None:
                assert not sign(ev), (fail, "the act was made after its method failed")
            own = [r for r in reqs if r.get("client_kind") != "internal"]   # not the router's questions
            assert len(own) == 2, (fail, [r.get("client_kind") for r in reqs])
            words.append(f"{fail}: 2 x 200, counted {n.get(fail):g}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return "; ".join(words)


def test_the_monitor_needs_the_extension():
    if not _torch():
        from scriptmode import Skip
        raise Skip("no torch here: the fake engine needs CPU torch")
    tmp = tempfile.mkdtemp(prefix="ext-monitor-")
    try:
        p = _engine(tmp, {"QSE_EXT": ""}, None)
        out, _ = p.communicate(timeout=180)
        lines = [ln for ln in out.splitlines() if "[ext] REFUSING:" in ln]
        assert p.returncode != 0 and len(lines) == 1, out[-3000:]
        assert "the session monitor (--conductor shadow|on) needs an extension" in lines[0], lines
        assert "[server] FAKE ENGINE" not in out and "[server] listening" not in out, out[-3000:]
        # a headless engine still reports the loop escape's sessions in /health, as before WP3b
        p = _engine(tmp, {"QSE_EXT": ""}, None, extra=("--conductor", "raw"))
        port = _port(p)
        _, h = _get(port, "/health")
        _stop(p)
        h = json.loads(h)
        le = (h.get("sampling") or {}).get("loop_escape")
        assert h["extensions"] == [] and isinstance(le, dict) and {"back", "sampled", "sessions"} <= set(le), h
        os.remove(os.path.join(tmp, "ledger.sqlite3"))
        p = _engine(tmp, {"QSE_EXT": "tandemapp.engine_ext"}, None)
        port = _port(p)
        _, h = _get(port, "/health")
        h = json.loads(h)
        body, hdr = _fixture("e1", RAW)
        assert _post(port, body, hdr) == 200
        log = _stop(p)
        assert h["mode"] == "engine" and [x["name"] for x in h["extensions"]] == ["tandemapp"], h
        assert "[conductor] journal: " in log and "[conductor] on:" not in log, log[-2000:]
        ev, _ = _rows(os.path.join(tmp, "ledger.sqlite3"))
        assert any(r.get("task") == "E1" and r.get("mode") == "act" for r in ev), "no act of the monitor alone"
        return "refused before the weights load without QSE_EXT; the monitor alone acts behind the adapter"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
