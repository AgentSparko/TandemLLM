"""Where compaction runs (ENG-413 WP2b, QA ENG-432): a call trace of the engine with the conductor
off and on.

The plan's rule: compaction is engine code if it runs with the conductor off. This test traces it.
`server/app.py --fake-engine` runs in a process of its own under a profiler that records the first
entry of every function of the compaction code (TandemApp's tandemapp/compaction.py and the engine's
server/api/compaction.py), of the conductor's shape reader (signals.shape and its compaction
helpers) and of server/app.py's compaction path (`_compacting`, `_compaction_plan`, the preparer and
the memory writer's compaction hook). Each engine gets the same four chat requests: opencode's
compaction request (its agent's prompt and the history as one user message), the bare shape (one
long user message), a request marked by its client (`tandem.compaction`, chatd's word) and a plain
turn.

  1  engine mode (no conductor directory, `--conductor raw`): every request passes `_compacting`,
     the check that tells a compaction, and that check is the engine's own
     (server/api/compaction.py `is_compaction`); nothing of TandemApp's compaction or of the
     conductor's signals runs; the continuation (`_compaction_plan`, `split`) and the preparer's
     bookkeeping (`Prepared.seen`, `learn`, `take`, `put`) never run, so the preparer has no
     client to prepare for                                      test_engine_mode_runs_only_the_check
  2  chat mode (`--conductor on` with a directory, the control: the trace sees a path that runs):
     the conductor's router reads the shape (its own copy of the marker), and the continuation
     runs in the engine: `_compaction_plan`, `split`, `Prepared.learn`
                                                                  test_chat_mode_runs_the_continuation
  3  the engine's check answers as the conductor's shape reader (signals.shape's `compaction`,
     which `_compacting` read before WP2b) on every case below: the client's word either way,
     opencode's agent prompt, the bare shape from opencode and from another client, short, with
     tools (offered or `tool_choice: none`), a tool response, list content, a system message; and
     the shapes and their helper are the conductor's               test_the_check_is_the_conductors

1 and 2 need CPU torch (the fake engine's server imports it); without it they print `skipped: no
torch`. 3 is standard library only.
Run: python tests/test_compaction_trace.py
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
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

#: the profiler: the first entry of each watched function, one line each, written as it happens
TRACE_LAUNCH = r"""
import os, sys, threading, runpy
sys.modules['triton'] = None; sys.modules['triton.language'] = None
OUT, ROOT = os.environ['TRACE_OUT'], os.getcwd()
FILES = {'app/tandemapp/compaction.py', 'server/api/compaction.py'}
APP = {'_compacting', '_compaction_plan', 'prepare_once', '_prepare_loop', '_writer_after_compaction'}
SIG = {'shape', '_compaction_marker', 'compaction_shape'}
seen, lock, rels = set(), threading.Lock(), {}
def prof(frame, event, arg):
    if event != 'call':
        return
    code = frame.f_code
    rel = rels.get(code.co_filename)
    if rel is None:
        rel = rels[code.co_filename] = os.path.relpath(code.co_filename, ROOT).replace(os.sep, '/')
    name = getattr(code, 'co_qualname', code.co_name)
    if not (rel in FILES or (rel == 'server/app.py' and code.co_name in APP)
            or (rel == 'app/tandemapp/conductor/signals.py' and code.co_name in SIG)):
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

HISTORY = "\n".join(f"[User]: please look at file number {i} and tell me what it does\n"
                    f"[Assistant]: file {i} holds a small helper; nothing else changes" for i in range(12))


def _compaction_body(model: str) -> dict:
    from server.api import compaction as C
    user = (f"Here is the conversation so far:\n\n<conversation>\n{HISTORY}\n</conversation>\n\n"
            f"{C.OPENCODE_INSTRUCTIONS}")
    return {"model": model, "max_tokens": 8, "messages": [
        {"role": "system", "content": C.OPENCODE_SYSTEM}, {"role": "user", "content": user}]}


REQUESTS = (
    ("opencode-compaction", lambda m: _compaction_body(m)),
    ("bare-shape", lambda m: {"model": m, "max_tokens": 8, "messages": [
        {"role": "user", "content": "Summarize this history. " + "x" * 2400}]}),
    ("marked", lambda m: {"model": m, "max_tokens": 8, "tandem": {"compaction": True}, "messages": [
        {"role": "user", "content": "the history, as the chat service sends it"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "now the summary"}]}),
    ("plain", lambda m: {"model": m, "max_tokens": 8, "messages": [
        {"role": "system", "content": "You are a coding agent."},
        {"role": "user", "content": "hello"}]}),
)


def _post(port: int, body: dict, session: str) -> int:
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions",
                                 data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "opencode/1.18.34 ai-sdk/provider-utils/4.0.23",
                                          "x-session-id": session})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            r.read()
            return r.status
    except urllib.error.HTTPError as e:
        e.read()
        return e.code


@functools.lru_cache(maxsize=None)
def trace(mode: str) -> tuple:
    """(the functions entered, the statuses) of one fresh fake engine in `mode` (engine | chat)."""
    try:
        import torch  # noqa: F401
    except ImportError:
        from scriptmode import Skip
        raise Skip("no torch here: the fake engine needs CPU torch")
    from ports import free_port
    tmp = tempfile.mkdtemp(prefix=f"compaction-trace-{mode}-")
    try:
        out = os.path.join(tmp, "trace.txt")
        open(out, "w").close()
        config = os.path.join(tmp, "conductor")
        if mode == "chat":
            os.makedirs(config)
        port = free_port()
        assert port not in (8000, 8009), port
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": tmp, "PYTHONPATH": ROOT,
               "CUDA_VISIBLE_DEVICES": "", "TZ": "Europe/Berlin", "TRACE_OUT": out, "TMPDIR": tmp}
        if mode == "chat":
            env["QSE_EXT"] = "tandemapp.engine_ext"           # ENG-413 WP3a: chat mode's extension
        log = open(os.path.join(tmp, "engine.log"), "w")
        p = subprocess.Popen(
            [sys.executable, "-c", TRACE_LAUNCH, "--fake-engine", "--host", "127.0.0.1",
             "--port", str(port), "--served-model", "qwen38-spark-engine",
             "--usage-ledger", os.path.join(tmp, "ledger.sqlite3"), "--fake-tps", "4000",
             "--conductor", "on" if mode == "chat" else "raw",
             "--conductor-config", config if mode == "chat" else "off",
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
            model = "TandemLLM" if mode == "chat" else "qwen38-spark-engine"
            status = {name: _post(port, make(model), f"ses_trace_{name}") for name, make in REQUESTS}
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
        if set(status.values()) != {200}:
            with open(os.path.join(tmp, "engine.log")) as f:
                status["log"] = f.read()[-4000:]
        return entered, status
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _show(entered: tuple) -> str:
    return "\n  " + "\n  ".join(entered)


def test_engine_mode_runs_only_the_check():
    entered, status = trace("engine")
    assert set(status.values()) == {200}, json.dumps(status, indent=1)
    s = set(entered)
    assert "server/app.py:_compacting" in s, "the check never ran:" + _show(entered)
    assert "server/api/compaction.py:is_compaction" in s, "not the engine's check:" + _show(entered)
    app_side = sorted(k for k in s if k.startswith(("app/tandemapp/compaction.py:",
                                                    "app/tandemapp/conductor/signals.py:")))
    assert not app_side, f"TandemApp's code ran with the conductor off: {app_side}" + _show(entered)
    never = {"server/app.py:_compaction_plan", "server/api/compaction.py:split",
             "server/api/compaction.py:Prepared.seen", "server/api/compaction.py:Prepared.learn",
             "server/api/compaction.py:Prepared.take", "server/api/compaction.py:Prepared.put",
             "server/app.py:_writer_after_compaction"}
    ran = sorted(never & s)
    assert not ran, f"the continuation or the preparer ran with the conductor off: {ran}" + _show(entered)
    return f"{len(status)} requests 200; entered: {', '.join(entered)}"


def test_chat_mode_runs_the_continuation():
    entered, status = trace("chat")
    assert set(status.values()) == {200}, json.dumps(status, indent=1)
    s = set(entered)
    want = {"server/app.py:_compacting", "server/app.py:_compaction_plan",
            "server/api/compaction.py:split", "server/api/compaction.py:Prepared.learn",
            "app/tandemapp/conductor/signals.py:shape", "app/tandemapp/compaction.py:marker"}
    missing = sorted(want - s)
    assert not missing, f"not entered with the conductor on: {missing}" + _show(entered)
    return f"{len(status)} requests 200; entered: {', '.join(entered)}"


def _cases() -> list[tuple[str, dict, str | None]]:
    from server.api import compaction as C
    long = "x" * 2400
    oc = _compaction_body("m")
    sys_msg = {"role": "system", "content": "You are a coding agent."}
    return [
        ("opencode agent prompt", oc, "opencode"),
        ("agent prompt, any client", oc, None),
        ("agent prompt, rewrapped", dict(oc, messages=[dict(oc["messages"][0], content="  " + " \n ".join(
            C.OPENCODE_SYSTEM.split())), oc["messages"][1]]), "other"),
        ("agent prompt with tools", dict(oc, tools=[{"type": "function", "function": {"name": "f"}}]), "opencode"),
        ("agent prompt, three messages", dict(oc, messages=oc["messages"] + [{"role": "user", "content": "x"}]), "opencode"),
        ("bare, opencode", {"messages": [{"role": "user", "content": long}]}, "opencode"),
        ("bare, other client", {"messages": [{"role": "user", "content": long}]}, "open-webui"),
        ("bare, no client", {"messages": [{"role": "user", "content": long}]}, None),
        ("bare, short", {"messages": [{"role": "user", "content": "x" * 1999}]}, "opencode"),
        ("bare, exactly the minimum", {"messages": [{"role": "user", "content": "x" * 2000}]}, "opencode"),
        ("bare, list content", {"messages": [{"role": "user", "content": [
            {"type": "text", "text": long}, {"type": "image_url", "image_url": {"url": "u"}}]}]}, "opencode"),
        ("bare, tools offered", {"messages": [{"role": "user", "content": long}],
                                 "tools": [{"type": "function", "function": {"name": "f"}}]}, "opencode"),
        ("bare, tool_choice none", {"messages": [{"role": "user", "content": long}], "tool_choice": "none",
                                    "tools": [{"type": "function", "function": {"name": "f"}}]}, "opencode"),
        ("bare, a tool response", {"messages": [{"role": "user", "content":
                                                 "<tool_response>" + long + "</tool_response>"}]}, "opencode"),
        ("bare with a system message", {"messages": [sys_msg, {"role": "user", "content": long}]}, "opencode"),
        ("two user messages", {"messages": [{"role": "user", "content": long}] * 2}, "opencode"),
        ("an assistant message", {"messages": [{"role": "assistant", "content": long}]}, "opencode"),
        ("marked true", {"tandem": {"compaction": True}, "messages": [{"role": "user", "content": "q"}]}, None),
        ("marked false on the shape", dict(oc, tandem={"compaction": False}), "opencode"),
        ("marked, not a bool", {"tandem": {"compaction": "yes"}, "messages": [{"role": "user", "content": long}]},
         "opencode"),
        ("plain", {"messages": [sys_msg, {"role": "user", "content": "hello"}]}, "opencode"),
        ("no messages", {}, "opencode"),
        ("messages not dicts", {"messages": ["x", None, {"role": "user", "content": long}]}, "opencode"),
    ]


def test_the_check_is_the_conductors():
    import ast
    import server  # noqa: F401  (its finder serves the conductor's tandemapp from app/ until ENG-413)
    from server.api import compaction as C
    from tandemapp.conductor import signals
    bad = []
    cases = _cases()
    for name, body, kind in cases:
        ours, theirs = C.is_compaction(body, kind), bool(signals.shape(body, client_kind=kind).compaction)
        if ours != theirs:
            bad.append(f"{name}: engine {ours}, conductor {theirs}")
    assert not bad, bad
    assert (C.COMPACTION_MIN_CHARS, C.COMPACTION_SHAPES, C.BARE_COMPACTION_CLIENTS) == \
        (signals.COMPACTION_MIN_CHARS, signals.COMPACTION_SHAPES, signals.BARE_COMPACTION_CLIENTS)

    def src(path, fn):
        text = open(os.path.join(ROOT, path)).read()
        node = next(n for n in ast.parse(text).body if isinstance(n, ast.FunctionDef) and n.name == fn)
        return ast.dump(node)
    for fn in ("compaction_shape", "_is_tool_response"):
        assert src("server/api/compaction.py", fn) == src("app/tandemapp/conductor/signals.py", fn), fn
    yes = sum(C.is_compaction(b, k) for _, b, k in cases)
    return f"{len(cases)} cases answered alike ({yes} compactions); shapes and helpers equal"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
