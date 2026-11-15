"""Trace replay of the conductor's acts and hooks on the fake engine (ENG-413 WP3b, QA ENG-430).

WP3b moves the request path's calls of the conductor (the router's plan, the prompt's notes, the
session monitor's acts inside the decode loop at SP1-SP4, the stream's state lines, the finish
rows) behind the engine-ext-v1 hooks. The acts are rare events at a token inside the decode loop,
so before any call site moved these fixtures recorded, on the code before the move (8e579447),
what each fixture request gets: the answer (content, reasoning, calls, finish, usage, every
logprobs entry and the ids of the output and the prompt, as tools/eng413_baseline.py gates them)
and the journal (the route and monitor rows with the token index of each act, and the request
rows). tests/fixtures/ext_trace.json holds that record; every later tree must give it again.

  1  chat mode (TandemLLM, the conductor's directory, QSE_EXT the tree's extension: TandemApp's
     tandemapp.engine_ext since ENG-413 WP4, the interim server.ext_app before): every fixture's
     answer and the journal equal the record                                  test_chat_mode_replays
  2  the monitor alone (the raw name, no directory): the same                 test_monitor_alone_replays
  3  the record holds each act the fixtures drive, at its safe point: SP1 CLOSE_REASONING (D1, and
     a client's budget: the SET_THINK_BUDGET the request's cap became), SP1 GIVE_UP (A1), SP2
     CONTINUE_HIDDEN and GIVE_UP (E1), SP3 REGENERATE_CALL (C3; C4 alone) and REPAIR_CALL (C4
     alone), SP4 PROMPT_TAIL_NOTE (C3 under the conductor, A1); the router's rows in chat mode
     only                                                                     test_the_record_drives_each_act

The fixtures: tools/eng413_baseline.py's B2f set (D1, E1 A and B, GIVE_UP, C4 three ways, two
calls, C3) and these: a call whose last parameter's closer never came (C4's repair, streamed and
not), a call to a tool not offered, a client's reasoning budget (the monitor's hidden close at
SP1), the argument loop and the session's next request (A1's give-up, then its note at SP4), a
request with `X-Tandem: raw` and one naming a profile that does not exist (the plan's refusal).

A mismatch prints the first paths that differ. Recording (only on the tree the record is of):
  python -c "import sys; sys.path[:0]=['tests','.']; import test_ext_trace as t; t.record()"
prints the record as `EXTTRACE` lines (zlib, base64) for tests/fixtures/ext_trace.json.

Needs CPU torch (the fake engine). Run: python tests/test_ext_trace.py
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import pairapp  # noqa: E402,F401  (a pair test: TandemApp from TANDEMAPP_SRC, ENG-413 WP14)
from tools import eng413_baseline as B  # noqa: E402

RECORD = os.path.join(HERE, "fixtures", "ext_trace.json")
LAUNCH = ("import sys, runpy; sys.modules['triton'] = None; sys.modules['triton.language'] = None;"
          "sys.argv = ['server/app.py'] + sys.argv[1:]; runpy.run_path('server/app.py', run_name='__main__')")
RAW_NAME = "qwen38-spark-engine"
#: the acts the record must hold (task:decision of a monitor row that acted), per mode. In chat mode
#: the shipped profile runs C4 in shadow: an unreadable call is not regenerated there (WP0's open
#: item 5); the monitor alone, on its own policy, repairs and regenerates it. C3's tail note acts only
#: under the conductor's profile
ACTS = {"chat": {"D1:CLOSE_REASONING", "client:CLOSE_REASONING", "E1:CONTINUE_HIDDEN", "E1:GIVE_UP",
                 "C3:PROMPT_TAIL_NOTE", "C3:REGENERATE_CALL", "A1:GIVE_UP", "A1:PROMPT_TAIL_NOTE"},
        "monitor": {"D1:CLOSE_REASONING", "client:CLOSE_REASONING", "E1:CONTINUE_HIDDEN", "E1:GIVE_UP",
                    "C3:REGENERATE_CALL", "C4:REGENERATE_CALL", "C4:REPAIR_CALL", "A1:GIVE_UP",
                    "A1:PROMPT_TAIL_NOTE"}}


def _monitor_ext() -> str:
    """The monitor alone runs behind the extension from ENG-413 WP3b on (the extension says it
    takes no conductor directory then: TandemApp's since WP4, WP3b's interim adapter before it); on
    a tree before WP3b, headless with `--conductor on`."""
    for name, path in (("tandemapp.engine_ext", os.path.join(pairapp.APP, "tandemapp", "engine_ext.py")),
                       ("server.ext_app", os.path.join(ROOT, "server", "ext_app.py"))):
        try:
            with open(path) as f:
                if "MONITOR_ALONE = True" in f.read():
                    return name
        except OSError:
            pass
    return ""


def _fake(port: int, ledger: str, config: str):
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.path.dirname(ledger),
           "PYTHONPATH": os.pathsep.join(p for p in (ROOT, os.environ.get("PYTHONPATH")) if p), "CUDA_VISIBLE_DEVICES": "", "TZ": "Europe/Berlin",
           "QSE_EXT": (_monitor_ext() or "server.ext_app") if config != "off" else _monitor_ext()}
    p = subprocess.Popen([sys.executable, "-c", LAUNCH, "--fake-engine", "--host", "127.0.0.1",
                          "--port", str(port), "--served-model", RAW_NAME, "--usage-ledger", ledger,
                          "--fake-tps", "4000", "--conductor", "on", "--conductor-config", config,
                          "--conductor-defaults", "shipped", "--disk-cache", "off",
                          "--session-spill", "off"],
                         cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    end = time.time() + 120
    while time.time() < end:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2)
            return p
        except OSError:
            if p.poll() is not None:
                raise AssertionError(p.stdout.read()[-3000:])
            time.sleep(0.3)
    p.kill()
    raise AssertionError("the fake engine did not answer /health in 120 s")


def _stop(p) -> str:
    p.terminate()
    try:
        out, _ = p.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        p.kill()
        out, _ = p.communicate(timeout=10)
    return out or ""


def _extra(port: int, model: str) -> list:
    """The fixtures beyond B2f (the module's docstring)."""
    tools = B._opencode_tools()
    pre = "tandemllm" if model == "TandemLLM" else "raw"

    def one(name, text, *, stream=True, think=False, with_tools=True, max_tokens=400, extra=None,
            headers=None, history=None, session=None):
        body = {"model": model, "stream": stream, "max_tokens": max_tokens,
                "reasoning_format": "tags", "logprobs": True,
                "messages": [{"role": "system", "content": "You are opencode."},
                             {"role": "user", "content": text}] + list(history or ())}
        if with_tools:
            body["tools"] = tools
        if not think:
            body.update(B.NO_THINK)
        body.update(extra or {})
        hdr = {"User-Agent": B.OPENCODE_UA, "x-session-id": f"ses_{session or name}"}
        hdr.update(headers or {})
        return B.run_one(port, f"{pre}_{name}", "/v1/chat/completions", body, headers=hdr)

    bash = json.dumps({"name": "bash", "arguments": {"command": "ls -la"}})
    out = [one("repair_drop_closer", "FAKE_DROP_CLOSER FAKE_CALL_JSON " + bash),
           one("repair_drop_closer_json", "FAKE_DROP_CLOSER FAKE_CALL_JSON " + bash, stream=False),
           one("unknown_tool", "FAKE_CALL_JSON " + json.dumps(
               {"name": "nosuch_tool", "arguments": {"x": 1}})),
           one("client_budget", "FAKE_LOOP", think=True, with_tools=False, max_tokens=8000,
               extra={"max_reasoning_tokens": 64}),
           one("argloop", "FAKE_ARG_LOOP write the game", max_tokens=60000),
           one("argloop_next", "FAKE_ARG_LOOP write the game", max_tokens=200, session="argloop",
               history=[{"role": "assistant", "content": "[TandemLLM stopped the `write` call]"},
                        {"role": "user", "content": "go on"}]),
           one("raw_header", "use the tool FAKE_EMPTY_ANSWER", think=True,
               headers={"X-Tandem": "raw"}),
           one("profile_refusal", "hello", with_tools=False,
               headers={"X-Tandem-Profile": "no-such-profile"})]
    return out


#: lists kept as their length and a hash whatever they hold (a logprobs channel's entries)
LONG_LISTS = ("content", "reasoning", "reasoning_content", "text", "refusal")


def _sha(x) -> str:
    return hashlib.sha256(json.dumps(x, sort_keys=True).encode()).hexdigest()[:16]


def _compact(x, key: str = ""):
    """The record's form: long texts, long id lists and the logprobs entries as their length and a
    hash (the journal's rows stay rows)."""
    if isinstance(x, dict):
        return {k: _compact(v, k) for k, v in sorted(x.items())}
    if isinstance(x, list):
        if len(x) > 32 and (key in LONG_LISTS or all(not isinstance(v, (dict, list)) for v in x)):
            return {"n": len(x), "sha": _sha(x)}
        return [_compact(v, key) for v in x]
    if isinstance(x, str) and len(x) > 300:
        return {"chars": len(x), "sha": _sha(x), "head": x[:120], "tail": x[-120:]}
    return x


def run_mode(mode: str) -> tuple[dict, str]:
    """One fresh fake engine in `mode` (chat or monitor), every fixture through it: the trace
    and the engine's log."""
    tmp = tempfile.mkdtemp(prefix=f"ext-trace-{mode}-")
    log = ""
    try:
        wd = os.path.join(tmp, "work")
        os.makedirs(wd)
        config = os.path.join(wd, "conductor") if mode == "chat" else "off"
        if mode == "chat":
            os.makedirs(config)
        ledger = os.path.join(wd, "ledger.sqlite3")
        from ports import free_port
        port = free_port()
        p = _fake(port, ledger, config)
        try:
            model = "TandemLLM" if mode == "chat" else RAW_NAME
            recs = B.battery_b2f(port, model) + _extra(port, model)
        finally:
            log = _stop(p)
        doc = B.normalize_record({"set": "b2f", "requests": recs, "strip": [tmp]})
        rows = B.read_journal(ledger)
        for r in rows.get("requests", []):
            r.pop("code_sha", None)            # the tree's own code hash: every change moves it
        jdoc = B.normalize_record({"set": "journal", "rows": rows, "strip": [tmp]})
        trace = {"requests": {n: _compact(r["gate"]) for n, r in doc["requests"].items()},
                 "order": [r["name"] for r in recs],
                 "journal": _compact(jdoc["gate"])}
        return trace, log
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def acts_of(trace: dict) -> set:
    rows = trace["journal"]["route_monitor"]
    rows = rows if isinstance(rows, list) else []
    return {f"{r.get('task')}:{r.get('decision')}" for r in rows
            if isinstance(r, dict) and r.get("kind") == "monitor" and r.get("mode") == "act"}


def _load() -> dict:
    with open(RECORD) as f:
        return json.load(f)


def _check(mode: str) -> str:
    try:
        import torch  # noqa: F401
    except ImportError:
        from scriptmode import Skip
        raise Skip("no torch here: the fake engine needs CPU torch")
    want = _load()["modes"][mode]
    got, log = run_mode(mode)
    d = B.diffs(want, got)
    if d:
        # the paths whose values moved first; an ordinal that only renumbered after them
        d.sort(key=lambda x: all(isinstance(v, str) and v.startswith("#") for v in x[2:]))
        lines = [f"  {p}: {k}: {json.dumps(a)[:160]} -> {json.dumps(b)[:160]}" for p, k, a, b in d[:25]]
        raise AssertionError(f"{mode}: {len(d)} paths differ from tests/fixtures/ext_trace.json:\n"
                             + "\n".join(lines) + "\n--- engine log tail:\n" + log[-2500:])
    return f"{mode}: {len(got['order'])} fixtures, {len(acts_of(got))} act kinds equal the record"


def test_chat_mode_replays():
    return _check("chat")


def test_monitor_alone_replays():
    return _check("monitor")


def test_the_record_drives_each_act():
    rec = _load()
    for mode, want in ACTS.items():
        got = acts_of(rec["modes"][mode])
        assert want <= got, (mode, sorted(want - got), sorted(got))
        routes = [r for r in rec["modes"][mode]["journal"]["route_monitor"] if r.get("kind") == "route"]
        assert (len(routes) > 0) == (mode == "chat"), (mode, len(routes))
    return "; ".join(f"{m}: {sorted(acts_of(rec['modes'][m]))}" for m in ACTS)


def record() -> None:
    """Print the record of this tree (two fresh engines a mode, which must agree) as EXTTRACE lines."""
    out = {"of": "ENG-413 WP3b trace replay, recorded on the tree before the move", "modes": {}}
    for mode in ("chat", "monitor"):
        a, _ = run_mode(mode)
        b, _ = run_mode(mode)
        d = B.diffs(a, b)
        if d:
            raise SystemExit(f"{mode}: two fresh engines differ: {d[:10]}")
        out["modes"][mode] = a
        kinds: dict = {}
        for r in a["journal"]["route_monitor"]:
            k = f"{r.get('kind')}/{r.get('sp')}/{r.get('task')}/{r.get('decision')}/{r.get('mode')}"
            kinds[k] = kinds.get(k, 0) + 1
        print(f"[ext-trace] {mode}: {len(a['order'])} fixtures, acts {sorted(acts_of(a))}; rows "
              + ", ".join(f"{k} x{n}" for k, n in sorted(kinds.items())), flush=True)
    raw = base64.b64encode(zlib.compress(json.dumps(out, indent=1, sort_keys=True).encode(), 9)).decode()
    for i in range(0, len(raw), 2000):
        print(f"EXTTRACE {raw[i:i + 2000]}", flush=True)
    print("EXTTRACE-END", flush=True)


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
