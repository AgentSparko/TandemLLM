"""Who runs the last engine-side TandemApp code (ENG-413 WP5b, QA ENG-432): a call trace of the engine
headless and in chat mode.

The rule (WP2b's, the compaction's): code that runs with the conductor off and decides what the
engine does is engine code; the rest is the extension's (a call or a route). `server/app.py
--fake-engine` runs in a process of its own under a profiler that records the first entry of every
function of the four pieces in both trees (TandemApp's tandemapp/conductor/loopescape.py,
tandemapp/session_tokens.py, tandemapp/laya_api.py, tandemapp/conductor/incontext.py; the engine's
server/api copies), of server/sampling_defaults.py `resolve` and of server/app.py's session address,
loop escape and report functions. Each engine gets the same requests: /health, a sampled chat
completion that names no filter, /v1/cache/stats, a session address of the library and one of a
path that has none, Laya's card and its preflight. The checkpoint's card is a test directory
(QSE_FAKE_CARD_DIR: temperature 1.0, top_p 0.95, top_k 20).

  1  engine mode as ops/serve.env starts it (no conductor, no extension, no decision model, no
     shadow reader): the card's sampling is read at the load and decides the sampled request's
     filters (/health `unnamed_filters` source card), the loop escape's counts are made at the
     start and reported by /health, every path goes through the session address strip and the
     registry answers /v1/cache/stats, a session address of the library names a session and is
     404 not_on_engine, one of another path 404 no route; all of it the engine's own
     (server/api/loopescape.py, server/api/session_tokens.py) and nothing of TandemApp's four
     modules runs; Laya is 404 not_on_engine, its preflight 501; the shadow's question form never
     runs (no reader)                                          test_engine_mode_runs_the_engines_own
  2  chat mode (TandemApp's extension, a conductor directory, the fake decision model): Laya is the
     extension's route (Ext._laya, TandemApp's laya_api) over the engine's decision models: the
     card 200, the preflight 204 without a Content-Type; the strip, the registry and the escapes
     are still the engine's, and TandemApp's own copies of them are not made
                                                                  test_chat_mode_serves_laya_through_the_extension
  3  a headless engine started by hand with decision models (no extension): Laya is 404
     not_on_engine (the engine served it itself before WP5b), its preflight 501; /v1/systemone,
     the engine's own route over the same models, still answers
                                                                  test_a_headless_engine_with_decision_models

1-3 need CPU torch (the fake engine's server imports it); without it they print `skipped: no torch`.
Run: python tests/test_owner_trace.py
"""

from __future__ import annotations

import functools
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import pairapp  # noqa: E402,F401  (a pair test: TandemApp from TANDEMAPP_SRC, ENG-413 WP14)
#: the profiler: the first entry of each watched function, one line each, written as it happens
TRACE_LAUNCH = r"""
import os, sys, threading, runpy
sys.modules['triton'] = None; sys.modules['triton.language'] = None
OUT, ROOT = os.environ['TRACE_OUT'], os.getcwd()
FILES = {'app/tandemapp/conductor/loopescape.py', 'app/tandemapp/session_tokens.py',
         'app/tandemapp/laya_api.py', 'app/tandemapp/conductor/incontext.py',
         'server/api/loopescape.py', 'server/api/session_tokens.py', 'server/api/incontext.py'}
APP = {'_strip_session', 'loop_escapes', 'card_sampling', '_health_sampling', 'cache_stats',
       '_shadow_begin'}
EXT = {'_laya', 'shadow_question', 'router_question'}
seen, lock, rels = set(), threading.Lock(), {}
def prof(frame, event, arg):
    if event != 'call':
        return
    code = frame.f_code
    rel = rels.get(code.co_filename)
    if rel is None:
        rel = rels[code.co_filename] = os.path.relpath(code.co_filename, ROOT).replace(os.sep, '/')
    name = getattr(code, 'co_qualname', code.co_name)
    if not code.co_flags & 0x2 or code.co_name.startswith('<'):
        return                       # a module's or a class's body, a comprehension: not a call
    if not (rel in FILES or (rel == 'server/app.py' and code.co_name in APP)
            or (rel == 'server/sampling_defaults.py' and code.co_name == 'resolve')
            or (rel == 'app/tandemapp/engine_ext.py' and code.co_name in EXT)):
        return
    key = rel + ':' + name
    with lock:
        if key in seen:
            return
        seen.add(key)
        with open(OUT, 'a') as f:
            f.write(key + '\n')
threading.setprofile(prof)
sys.setprofile(prof)
sys.argv = ['server/app.py'] + sys.argv[1:]
runpy.run_path('server/app.py', run_name='__main__')
"""

ADMIN = "trace-" + "0" * 40
#: a session token of the right shape that no session holds
TOKEN = "nWJkUiDZ59TX"
CARD = {"temperature": 1.0, "top_p": 0.95, "top_k": 20, "do_sample": True}
#: the modules each mode's TandemApp side may not run (the engine's copies run instead)
APP_FOUR = ("app/tandemapp/conductor/loopescape.py:", "app/tandemapp/session_tokens.py:",
            "app/tandemapp/laya_api.py:", "app/tandemapp/conductor/incontext.py:")


def _call(port: int, method: str, path: str, body=None, token=None, raw=False):
    h = {"Authorization": f"Bearer {token}"} if token else {}
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            payload, status, headers = r.read(), r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        payload, status, headers = e.read(), e.code, dict(e.headers)
    if raw:
        return status, headers, payload
    try:
        return status, headers, json.loads(payload or b"{}")
    except ValueError:
        return status, headers, {"_text": payload[:200].decode("utf-8", "replace")}


def _requests(port: int, model: str) -> dict:
    out = {}
    st, _, h = _call(port, "GET", "/health", token=ADMIN)
    out["health"] = (st, (h.get("sampling") or {}))
    st, _, b = _call(port, "POST", "/v1/chat/completions", {
        "model": model, "max_tokens": 4, "temperature": 0.7, "seed": 7,
        "messages": [{"role": "user", "content": "hello"}]})
    out["sampled"] = (st, (b.get("choices") or [{}])[0].get("finish_reason"))
    st, _, b = _call(port, "GET", "/v1/cache/stats", token=ADMIN)
    out["cache_stats"] = (st, b.get("session_tokens"))
    st, _, b = _call(port, "GET", f"/s/{TOKEN}/v1/library", token=ADMIN)
    out["session_library"] = (st, (b.get("error") or {}).get("type"))
    st, _, b = _call(port, "GET", f"/s/{TOKEN}/v1/models", token=ADMIN)
    out["session_other"] = (st, (b.get("error") or {}).get("type"), (b.get("error") or {}).get("message"))
    st, _, b = _call(port, "GET", "/v1/laya")
    out["laya"] = (st, (b.get("error") or {}).get("type") if st != 200 else "card")
    st, hd, raw = _call(port, "OPTIONS", "/v1/laya/ask", raw=True)
    out["laya_preflight"] = (st, hd.get("Content-Type"), hd.get("Access-Control-Allow-Origin"), len(raw))
    st, _, h = _call(port, "GET", "/health", token=ADMIN)
    out["health_after"] = (st, (h.get("sampling") or {}).get("loop_escape"))
    return out


@functools.lru_cache(maxsize=None)
def trace(mode: str) -> tuple:
    """(the functions entered, the answers) of one fresh fake engine in `mode` (engine | chat |
    deciders: headless with decision models)."""
    try:
        import torch  # noqa: F401
    except ImportError:
        from scriptmode import Skip
        raise Skip("no torch here: the fake engine needs CPU torch")
    from ports import free_port
    tmp = tempfile.mkdtemp(prefix=f"owner-trace-{mode}-")
    try:
        out = os.path.join(tmp, "trace.txt")
        open(out, "w").close()
        card = os.path.join(tmp, "card")
        os.makedirs(card)
        with open(os.path.join(card, "generation_config.json"), "w") as f:
            json.dump(CARD, f)
        config = os.path.join(tmp, "conductor")
        chat = mode == "chat"
        if chat:
            os.makedirs(config)
        port = free_port()
        assert port not in (8000, 8009), port
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": tmp, "PYTHONPATH": os.pathsep.join(p for p in (ROOT, os.environ.get("PYTHONPATH")) if p),
               "CUDA_VISIBLE_DEVICES": "", "TZ": "Europe/Berlin", "TRACE_OUT": out, "TMPDIR": tmp,
               "QSE_ADMIN_TOKEN": ADMIN, "QSE_FAKE_CARD_DIR": card,
               "BOX_LOCK": os.path.join(tmp, "no-box.flock")}
        if mode in ("chat", "deciders"):
            env.update(QWEN38_DECIDER_MODELS="other-decider", FAKE_MEM_AVAILABLE_GIB="64")
        if chat:
            env["QSE_EXT"] = "tandemapp.engine_ext"           # ENG-413 WP4: chat mode's extension
        log = open(os.path.join(tmp, "engine.log"), "w")
        p = subprocess.Popen(
            [sys.executable, "-c", TRACE_LAUNCH, "--fake-engine", "--host", "127.0.0.1",
             "--port", str(port), "--served-model", "qwen38-spark-engine",
             "--usage-ledger", os.path.join(tmp, "ledger.sqlite3"), "--fake-tps", "4000",
             "--conductor", "on" if chat else "raw",
             "--conductor-config", config if chat else "off",
             "--conductor-defaults", "shipped", "--disk-cache", "off", "--session-spill", "off"],
            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            end = time.time() + 180
            while True:
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2).read()
                    break
                except OSError:
                    if p.poll() is not None or time.time() > end:
                        log.flush()
                        with open(os.path.join(tmp, "engine.log")) as f:
                            raise AssertionError(f"the fake engine did not start: {f.read()[-3000:]}")
                    time.sleep(0.3)
            answers = _requests(port, "TandemLLM" if chat else "qwen38-spark-engine")
            if mode == "deciders":
                st, _, b = _call(port, "POST", "/v1/systemone", {"model": "other-decider"}, token=ADMIN)
                answers["systemone"] = (st, sorted(b)[:3])
        finally:
            p.terminate()
            try:
                p.wait(30)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(10)
            log.close()
        with open(out) as f:
            entered = tuple(sorted({ln.strip() for ln in f if ln.strip()}))
        with open(os.path.join(tmp, "engine.log")) as f:
            answers["_log"] = f.read()[-3000:]
        return entered, answers
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _show(entered: tuple, answers: dict) -> str:
    shown = {k: v for k, v in answers.items() if k != "_log"}
    return "\n  " + "\n  ".join(entered) + "\n" + json.dumps(shown, indent=1, default=str)


def test_engine_mode_runs_the_engines_own():
    entered, a = trace("engine")
    s, why = set(entered), _show(entered, a)
    want = {"server/api/loopescape.py:card_sampling", "server/api/loopescape.py:Escapes.__init__",
            "server/api/loopescape.py:Escapes.report", "server/api/session_tokens.py:strip",
            "server/api/session_tokens.py:SessionTokens.__init__",
            "server/api/session_tokens.py:SessionTokens.report",
            "server/api/session_tokens.py:SessionTokens.key_for",
            "server/app.py:Handler._strip_session", "server/app.py:loop_escapes",
            "server/app.py:card_sampling",
            "server/sampling_defaults.py:resolve"}
    missing = sorted(want - s)
    assert not missing, f"not the engine's own with the conductor off: {missing}" + why
    app_side = sorted(k for k in s if k.startswith(APP_FOUR))
    assert not app_side, f"TandemApp's code ran with the conductor off: {app_side}" + why
    assert not any(k.startswith("server/api/incontext.py:") for k in s), "no reader, no question" + why
    st, sampling = a["health"]
    card = sampling.get("card") or {}
    assert st == 200 and {k: card.get(k) for k in ("temperature", "top_p", "top_k")} == \
        {"temperature": 1.0, "top_p": 0.95, "top_k": 20}, why
    assert sampling["unnamed_filters"] == {"top_p": 0.95, "top_k": 20, "min_p": 0.0, "source": "card"}, why
    assert sampling["loop_escape"] == {"sessions": 0, "started": 0, "sampled": 0, "back": 0}, why
    assert a["sampled"][0] == 200, why
    assert a["cache_stats"] == (200, {"sessions": 0, "issued": 0, "calls": 0, "unknown": 0,
                                      "expired": 0}), why
    assert a["session_library"] == (404, "not_on_engine"), why
    assert a["session_other"] == (404, "not_found", "no route /v1/models under a session address"), why
    assert a["laya"] == (404, "not_on_engine") and a["laya_preflight"][0] == 501, why
    assert a["health_after"] == (200, {"sessions": 0, "started": 0, "sampled": 0, "back": 0}), why
    return f"{len(entered)} functions entered, each the engine's: " + ", ".join(entered)


def test_chat_mode_serves_laya_through_the_extension():
    entered, a = trace("chat")
    s, why = set(entered), _show(entered, a)
    want = {"app/tandemapp/engine_ext.py:Ext._laya", "app/tandemapp/laya_api.py:handle_get",
            "app/tandemapp/laya_api.py:handle_options", "server/api/session_tokens.py:strip",
            "server/api/session_tokens.py:SessionTokens.__init__", "server/api/loopescape.py:Escapes.__init__",
            "server/api/loopescape.py:card_sampling"}
    missing = sorted(want - s)
    assert not missing, f"not entered in chat mode: {missing}" + why
    theirs = sorted(k for k in s if k in ("app/tandemapp/session_tokens.py:strip",
                                          "app/tandemapp/session_tokens.py:SessionTokens.__init__",
                                          "app/tandemapp/conductor/loopescape.py:Escapes.__init__",
                                          "app/tandemapp/conductor/loopescape.py:card_sampling"))
    assert not theirs, f"TandemApp made its own copies of the engine's objects: {theirs}" + why
    assert a["laya"] == (200, "card"), why
    st, ctype, origin, n = a["laya_preflight"]
    assert (st, ctype, origin, n) == (204, None, "*", 0), why
    assert a["session_library"][0] == 200 and a["cache_stats"][0] == 200, why
    assert a["health"][1]["loop_escape"] == {"sessions": 0, "started": 0, "sampled": 0, "back": 0}, why
    return f"Laya 200 and its preflight 204 through the extension; {len(entered)} functions entered"


def test_a_headless_engine_with_decision_models():
    entered, a = trace("deciders")
    s, why = set(entered), _show(entered, a)
    assert a["laya"] == (404, "not_on_engine") and a["laya_preflight"][0] == 501, why
    assert not any(k.startswith("app/tandemapp/laya_api.py:") for k in s), why
    assert a["systemone"][0] != 404, why
    return f"Laya 404 not_on_engine without an extension; /v1/systemone {a['systemone'][0]}"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
