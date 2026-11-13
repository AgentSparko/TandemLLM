#!/usr/bin/env python3
"""ENG-413's baselines B1, B2 and B3: capture them from a test engine, normalise, compare (ENG-414).

    eng413_baseline.py capture --port P --set b1|b2|b2f|b3 --out DIR [--name NAME] [--model M]
    eng413_baseline.py journal --ledger FILE --out DIR --name NAME     (after the engine stopped)
    eng413_baseline.py compare BASE_DIR CAND_DIR [--allow PATH ...] [--additive] [--json OUT]
    eng413_baseline.py normalize FILE                                   (what compare compares)

The plan's gates compare a refactored engine with these records: G2 engine mode with B1, G3 chat mode
with B2 (and the monitor fixtures, B2f), G4 the API's bytes with B3. A capture talks HTTP to an engine
on 127.0.0.1 only and refuses port 8000, the box's engine: a window kit starts a test engine of the
tree under test on a test port and points this at it. Standard library only; it imports nothing of
the engine or of TandemApp, so the same file captures from any tree before and after the refactor.

What a record holds. Every request is written as it was sent and as it came back (the raw answer,
every SSE line of a stream), with what compare gates on beside it:

  b1   engine mode (no conductor; the served raw model name, read from /v1/models). Greedy and
       seeded raw completions with `logprobs`; chat completions with thinking off, thinking on, with
       tools (streamed and not), a tool result's turn and a seeded sampled one, each with `logprobs`.
       `cache_prompt: false` on all of them: every request reads its prompt whole.
  b2   chat mode (the conductor on, model `TandemLLM`): an opencode-shaped session of three turns, each
       built from the answers before it (tool calls answered with fixed results), a Hermes-shaped
       request, `X-Tandem: raw`, and opencode's title request. Streamed, with `logprobs`.
  b2f  the fake engine (`server/app.py --fake-engine`) with the session monitor on: the prompts that
       make it act -- D1 (a reasoning loop closed), E1 (an empty turn continued, cases A and B),
       GIVE_UP (empty after two tries), C3 (a repeated call: the tail note, then the regenerate), C4
       (a call the parser cannot read, written again) -- once in chat mode (`TandemLLM`, the
       conductor's directory on) and once with the monitor alone (the raw name, no directory: C4
       acts only there). The journal rows say at which token each act fired (`tokens_at_cut`,
       `position`).
  b3   the API's reads: /health, /v1/models, /metrics, /v1/replica/identity, /readyz and
       /v1/dashboard/{live,system,logs,parallel,metrics,summary,usage,requests}.
  journal  the ledger file's conductor_events, conductor_texts and requests rows (read only, after
       the engine has stopped, so the journal's queue is flushed).

Each request's record carries `gate`: the parts G2/G3 hold equal -- the status, the answer's text,
reasoning, tool calls (names and arguments) and finish reason, the usage's token counts, the token
of every logprobs entry with its bytes and log-probability (exact floats), the ids of the answer's
text (`/v1/tokenize`) and the prompt's ids (`/v1/tokenize` of the same messages and tools). `info`:
the stream's chunks after normalisation; a difference there is reported, never a failure.

The normaliser (compare runs it on the raw records, so it can be changed after a capture):
  N1  keys whose value is a moment, a duration, a rate, a process, a port or a size of memory become "<v>":
      the names in VOLATILE_KEYS and every key ending in one of VOLATILE_SUFFIXES
  N2  the subtrees under SHAPE_KEYS (host, GPU and memory figures, samples, series, timings, the
      live view's activity) keep their keys only, every value "<v>"
  N3  identifiers become ordinals in order of first sight within one record ("#1", "#2", ...):
      the keys in ORDINAL_KEYS and the values that look like a completion, call or session id
      (ORDINAL_VALUE), so two references to one id stay equal and two different ids stay different
  N4  /metrics becomes its family list: name, type and each family's label names, no value
  N5  the live view, the log and the requests in flight (SHAPE_ROUTES) keep their shape only: the
      keys of the answer and of a list's first entry, and whether a list is empty
  N6  the journal's requests rows lose the engine's statistics (REQUEST_STATS: blocks, drafts,
      slot, lane, cache figures): how a request was served, not what it answered
  N7  of conductor_events, the router's and the monitor's rows are gated (JOURNAL_GATED, in order,
      with their hidden texts); the other kinds (config migrations, memory writers, outcomes) run
      on timers and threads of their own and are info
  N8  the run's own folders (capture's `--strip`, kept in the record) become "<root>" in every
      string: the ledger's path, the tree copy's paths in the flags and the weights list
compare holds the normalised records equal, path by path. `--allow GLOB` (`*` and `?`, the rest
literal) accepts a difference at a path that matches (`b3-engine-post./health.body.engine_api`);
`--additive` accepts paths only the candidate has (an additive contract version). Exit 0 when every
gated part is equal.
"""

from __future__ import annotations

import argparse
import http.client as hc
import json
import os
import re
import sqlite3
import sys
import time

LIVE_PORT = 8000
HOST = "127.0.0.1"
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FORMAT = 1

VOLATILE_KEYS = frozenset((
    "created", "ts", "ts_ms", "time", "at", "started", "started_at", "since", "until", "uptime",
    "uptime_s", "pid", "pids", "date", "now", "age", "age_s", "last", "last_ms", "boot", "boot_id",
    "elapsed", "took", "took_s", "eta", "eta_s", "system_fingerprint", "first_ms", "labelled_at",
    "next", "next_at", "expires", "expires_at", "mtime", "load", "loadavg", "rss", "vms", "free",
    "available", "used", "cached", "buffers", "temperature", "temp", "power", "watts", "util",
    "utilization", "clock", "fan", "rate", "rates", "health_cost", "seconds", "wall", "ms", "seed", "tps",
    "bytes", "oldest", "newest", "port", "ports", "host_port"))
VOLATILE_SUFFIXES = ("_ms", "_s", "_at", "_ts", "_tps", "_pct", "_ratio", "_hz", "tok_s", "_gib",
                     "_gb", "_mb", "_kb", "_bytes", "_ns", "_us", "_mib", "_kib", "_ago", "_time",
                     "_avg", "_mean", "_p50", "_p90", "_p95", "_p99", "_us_max", "_us_last", "_port")
SHAPE_KEYS = frozenset((
    "memory", "mem", "gpu", "gpus", "host", "hosts", "cpu", "disk", "disks", "net", "network",
    "page_cache", "allocator", "timings", "metrics", "samples", "series", "activity", "box",
    "health_cache", "kv_pool", "weights_cache", "processes", "system", "device", "devices"))
ORDINAL_KEYS = frozenset((
    "id", "request_id", "session_key", "client_id", "question_id", "hidden_text_id",
    "parent_request", "tool_call_id", "upstream_request", "conversation_id", "session"))
#: the reads whose values all move with the clock: compared by their shape (N5)
SHAPE_ROUTES = frozenset(("/v1/dashboard/live", "/v1/dashboard/logs", "/v1/dashboard/parallel"))
#: the journal's kinds compare holds equal (the router's decisions and the monitor's); the others
#: (config migrations, memory writers, outcomes, judges) run on timers and threads of their own (N7)
JOURNAL_GATED = frozenset(("route", "monitor"))
#: the requests table's engine statistics: how a request was served, not what it answered (N6)
REQUEST_STATS = frozenset(("blocks", "draft_tokens", "draft_accepted", "slot", "lane", "engine_cached",
                           "cached_tokens", "forwarded_tokens", "preemptions"))
ORDINAL_VALUE = re.compile(r"^(chatcmpl|cmpl|resp|call|req|ses|msg|evt|run|job|turn)[-_][0-9A-Za-z_-]{4,}$")


# ---------------------------------------------------------------------------- HTTP

def _guard(port: int) -> None:
    if int(port) == LIVE_PORT:
        raise SystemExit(f"eng413_baseline: port {LIVE_PORT} is the box's engine; captures run against a "
                         "test engine only")


def request(port: int, method: str, path: str, body=None, headers=None, timeout: float = 600.0,
            stream: bool = False) -> dict:
    """One request to 127.0.0.1:port: status, headers, the parsed body (or text), the SSE lines."""
    _guard(port)
    c = hc.HTTPConnection(HOST, int(port), timeout=timeout)
    t0 = time.monotonic()
    try:
        raw = None if body is None else json.dumps(body).encode()
        h = dict(headers or {})
        if raw is not None:
            h["Content-Type"] = "application/json"
        c.request(method, path, body=raw, headers=h)
        r = c.getresponse()
        out = {"status": r.status, "headers": {k.lower(): v for k, v in r.getheaders()}}
        parts, end = [], t0 + timeout          # a whole-answer deadline: a stream that never ends fails
        while True:
            chunk = r.read1(65536)
            if not chunk:
                break
            parts.append(chunk)
            if time.monotonic() > end:
                raise TimeoutError(f"{method} {path}: the answer did not end within {timeout:.0f} s")
        text = b"".join(parts).decode("utf-8", "replace")
        if stream and r.status == 200 and "text/event-stream" in out["headers"].get("content-type", ""):
            out["sse"] = [ln for ln in text.split("\n") if ln.strip()]
        else:
            try:
                out["body"] = json.loads(text)
            except ValueError:
                out["text"] = text
        out["s"] = round(time.monotonic() - t0, 3)
        return out
    finally:
        c.close()


# ---------------------------------------------------------------------------- the batteries

WEATHER = {"type": "function", "function": {
    "name": "get_weather", "description": "The weather forecast for a city.",
    "parameters": {"type": "object", "properties": {
        "city": {"type": "string", "description": "the city"},
        "days": {"type": "integer", "description": "days ahead, 1 to 7"}},
        "required": ["city"]}}}
BASH = {"type": "function", "function": {
    "name": "bash", "description": "Run one shell command in the project and return its output.",
    "parameters": {"type": "object", "properties": {"command": {"type": "string"}},
                   "required": ["command"]}}}
NO_THINK = {"chat_template_kwargs": {"enable_thinking": False}}
PROMPTS = [
    "The harbour office keeps a ledger of every boat that ties up at the north quay. Each line names",
    "def fibonacci(n):\n    \"\"\"Return the n-th Fibonacci number.\"\"\"\n",
    "The three primary colours of light are",
    "Translate into German: The train to Hamburg leaves at seven in the morning.\nGerman:",
    "A short history of the lighthouse at the end of the pier:\n\n",
    "Q: What is 17 times 23?\nA:",
]
SAMPLED = [("Write one sentence about a fox in the snow.", 11), ("List four uses for a paperclip:", 22),
           ("The best way to learn a language is", 33)]
#: (name, prompt, with tools, streamed, thinking): the fake engine's words that make the monitor act
FAKE_FIXTURES = [
    ("e1_case_a", "use the tool FAKE_EMPTY_THINK", True, False, True),
    ("e1_case_b", "use the tool FAKE_EMPTY_ANSWER", True, True, True),
    ("give_up", "use the tool FAKE_EMPTY_ANSWER FAKE_EMPTY_ALWAYS", True, True, True),
    ("c4_regenerate", "call it FAKE_BAD_CALL", True, True, False),
    ("c4_regenerate_json", "call it FAKE_BAD_CALL", True, False, False),
    ("c4_twice_bad", "call it FAKE_BAD_ALWAYS", True, True, False),
    ("d1_loop", "FAKE_LOOP", False, True, True),
    ("two_calls", "use the tool FAKE_TWO_CALLS", True, True, True),
    ("plain", "Name three rivers.", False, False, True),
]
OPENCODE_UA = "opencode/1.18.33 ai-sdk/provider-utils/3.0.20 runtime/bun/1.3.5"
HERMES_UA = "OpenAI/Python 2.8.1"
SYSTEM = ("You are opencode, an interactive CLI tool that helps users with software engineering tasks. "
          "Use the tools available to you. Working directory: /home/user/proj")
TOOL_RESULTS = {"bash": "main.py\nutil.py\ntests/test_util.py\nREADME.md",
                "glob": "main.py\nutil.py\ntests/test_util.py",
                "grep": "main.py:12:if __name__ == \"__main__\":",
                "read": "1\timport sys\n2\tfrom util import run\n3\n4\tdef main():\n5\t    run(sys.argv[1:])\n"}


def _opencode_tools() -> list:
    with open(os.path.join(HERE, "tests", "fixtures", "opencode_tools.json")) as f:
        return json.load(f)["tools"]


def served_name(port: int) -> str:
    r = request(port, "GET", "/v1/models", timeout=30)
    data = (r.get("body") or {}).get("data") or []
    if r["status"] != 200 or not data:
        raise SystemExit(f"eng413_baseline: /v1/models answered {r['status']}")
    return data[0]["id"]


def _sse_chunks(lines: list) -> list:
    out = []
    for ln in lines or []:
        if ln.startswith("data: ") and ln[6:].strip() != "[DONE]":
            try:
                out.append(json.loads(ln[6:]))
            except ValueError:
                out.append({"unparsed": ln[6:200]})
    return out


def wire(rec: dict) -> dict:
    """What a client holds of one answer: text, reasoning, calls, finish, usage, logprobs entries."""
    w = {"text": "", "content": "", "reasoning": "", "calls": {}, "finish": None, "usage": None,
         "logprobs": {}, "error": None}

    def add_lp(key: str, entries) -> None:
        for e in entries or []:
            if isinstance(e, dict):
                w["logprobs"].setdefault(key, []).append(
                    {"t": e.get("token"), "b": e.get("bytes"), "lp": e.get("logprob")})

    def legacy_lp(lp) -> None:
        if isinstance(lp, dict) and isinstance(lp.get("tokens"), list):
            for t, v in zip(lp["tokens"], lp.get("token_logprobs") or [None] * len(lp["tokens"])):
                w["logprobs"].setdefault("text", []).append({"t": t, "lp": v})
        elif isinstance(lp, dict):
            for k, v in lp.items():
                if isinstance(v, list):
                    add_lp(k, v)

    if "sse" in rec:
        for ch in _sse_chunks(rec["sse"]):
            if ch.get("error"):
                w["error"] = ch["error"]
            if ch.get("usage"):
                w["usage"] = ch["usage"]
            for c in ch.get("choices") or []:
                d = c.get("delta") or {}
                w["content"] += d.get("content") or ""
                w["reasoning"] += d.get("reasoning_content") or d.get("reasoning") or ""
                w["text"] += c.get("text") or ""
                for tc in d.get("tool_calls") or []:
                    cur = w["calls"].setdefault(str(tc.get("index", 0)), {"name": "", "arguments": ""})
                    fn = tc.get("function") or {}
                    cur["name"] += fn.get("name") or ""
                    cur["arguments"] += fn.get("arguments") or ""
                if c.get("finish_reason"):
                    w["finish"] = c["finish_reason"]
                legacy_lp(c.get("logprobs"))
        return w
    body = rec.get("body")
    if not isinstance(body, dict):
        w["error"] = rec.get("text", "")[:500]
        return w
    w["usage"] = body.get("usage")
    w["error"] = body.get("error")
    for c in body.get("choices") or []:
        msg = c.get("message") or {}
        w["text"] += c.get("text") or ""
        w["content"] += msg.get("content") or ""
        w["reasoning"] += msg.get("reasoning_content") or msg.get("reasoning") or ""
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function") or {}
            w["calls"][str(i)] = {"name": fn.get("name") or "", "arguments": fn.get("arguments") or ""}
        w["finish"] = c.get("finish_reason")
        legacy_lp(c.get("logprobs"))
    return w


def _ids(port: int, payload: dict) -> list | None:
    r = request(port, "POST", "/v1/tokenize", dict(payload, ids=True), timeout=120)
    body = r.get("body")
    return body.get("ids") if r["status"] == 200 and isinstance(body, dict) else None


def run_one(port: int, name: str, endpoint: str, body: dict, headers=None) -> dict:
    """One request with its gate: the answer, the ids of its text, the ids of its prompt."""
    stream = bool(body.get("stream"))
    rec = {"name": name, "endpoint": endpoint, "request": body, "request_headers": headers or {}}
    rec.update(request(port, "POST", endpoint, body, headers=headers, stream=stream))
    w = wire(rec)
    out_text = w["text"] or w["content"]
    gate = {"status": rec["status"], "wire": {k: w[k] for k in ("text", "content", "reasoning", "calls",
                                                                 "finish", "error")},
            "usage": {k: v for k, v in (w["usage"] or {}).items() if k.endswith("_tokens")},
            "logprobs": w["logprobs"]}
    if rec["status"] == 200:
        gate["text_ids"] = _ids(port, {"text": out_text}) if out_text else []
        if w["reasoning"]:
            gate["reasoning_ids"] = _ids(port, {"text": w["reasoning"]})
        if endpoint == "/v1/completions":
            gate["prompt_ids"] = _ids(port, {"text": body["prompt"]})
        else:
            p = {k: body[k] for k in ("model", "messages", "tools", "chat_template_kwargs") if k in body}
            gate["prompt_ids"] = _ids(port, p)
    rec["gate"] = gate
    rec["wire"] = w
    return rec


def battery_b1(port: int, model: str, max_tokens: int) -> list:
    out = []
    for i, p in enumerate(PROMPTS):
        out.append(run_one(port, f"complete_greedy_{i}", "/v1/completions", {
            "model": model, "prompt": p, "max_tokens": 256 if i == 4 else max_tokens, "temperature": 0,
            "logprobs": 1, "cache_prompt": False}))
    for p, seed in SAMPLED:
        out.append(run_one(port, f"complete_seed_{seed}", "/v1/completions", {
            "model": model, "prompt": p, "max_tokens": max_tokens, "temperature": 0.8, "top_p": 0.95,
            "seed": seed, "logprobs": 1, "cache_prompt": False}))
    ask = [{"role": "user", "content": "Name three rivers in Europe, one line each, nothing else."}]
    out.append(run_one(port, "chat_nothink", "/v1/chat/completions", dict(
        NO_THINK, model=model, messages=ask, max_tokens=128, temperature=0, logprobs=True,
        cache_prompt=False)))
    out.append(run_one(port, "chat_think", "/v1/chat/completions", {
        "model": model, "messages": [{"role": "user", "content": "Is 391 a prime number? Answer briefly."}],
        "max_tokens": 384, "temperature": 0, "logprobs": True, "cache_prompt": False}))
    weather = [{"role": "user", "content": "What is the weather in Berlin for the next three days? Use the tool."}]
    out.append(run_one(port, "chat_tools", "/v1/chat/completions", dict(
        NO_THINK, model=model, messages=weather, tools=[WEATHER, BASH], max_tokens=256, temperature=0,
        logprobs=True, cache_prompt=False)))
    out.append(run_one(port, "chat_tools_think_stream", "/v1/chat/completions", {
        "model": model, "messages": weather, "tools": [WEATHER, BASH], "max_tokens": 512, "temperature": 0,
        "logprobs": True, "cache_prompt": False, "stream": True, "stream_options": {"include_usage": True}}))
    turn = weather + [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call_w1", "type": "function", "function": {
            "name": "get_weather", "arguments": json.dumps({"city": "Berlin", "days": 3})}}]},
        {"role": "tool", "tool_call_id": "call_w1",
         "content": json.dumps({"city": "Berlin", "days": [{"high": 7, "low": 1, "sky": "rain"},
                                                           {"high": 9, "low": 3, "sky": "clouds"},
                                                           {"high": 11, "low": 4, "sky": "sun"}]})}]
    out.append(run_one(port, "chat_tool_result", "/v1/chat/completions", dict(
        NO_THINK, model=model, messages=turn, tools=[WEATHER, BASH], max_tokens=192, temperature=0,
        logprobs=True, cache_prompt=False)))
    out.append(run_one(port, "chat_seed_7", "/v1/chat/completions", dict(
        NO_THINK, model=model, messages=[{"role": "user", "content": "Invent a name for a small cafe."}],
        max_tokens=96, temperature=0.7, top_p=0.95, seed=7, logprobs=True, cache_prompt=False)))
    return out


def _assistant_turn(w: dict) -> tuple:
    """The assistant message a client sends back, and the tool messages answering its calls."""
    calls = [w["calls"][k] for k in sorted(w["calls"], key=int)]
    msg = {"role": "assistant", "content": w["content"]}
    if w["reasoning"]:
        msg["reasoning_content"] = w["reasoning"]
    tools = []
    if calls:
        msg["tool_calls"] = [{"id": f"call_t{i}", "type": "function", "function": c}
                             for i, c in enumerate(calls)]
        tools = [{"role": "tool", "tool_call_id": f"call_t{i}",
                  "content": TOOL_RESULTS.get(c["name"], "done")} for i, c in enumerate(calls)]
    return msg, tools


def battery_b2(port: int, max_tokens: int) -> list:
    tools = _opencode_tools()
    oc = {"User-Agent": OPENCODE_UA, "x-session-id": "ses_eng413b2"}
    base = {"model": "TandemLLM", "stream": True, "stream_options": {"include_usage": True},
            "tools": tools, "max_tokens": max_tokens, "temperature": 0, "logprobs": True,
            "reasoning_effort": "medium", "reasoning_format": "reasoning_content"}
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": "List the Python files in this project and tell me which one is the "
                                        "entry point."}]
    out = []
    for turn, follow in enumerate(("Add a --verbose flag to main.py's command line. Show the change only.",
                                   None)):
        rec = run_one(port, f"opencode_turn{turn + 1}", "/v1/chat/completions", dict(base, messages=list(msgs)),
                      headers=oc)
        out.append(rec)
        msg, results = _assistant_turn(rec["wire"])
        msgs += [msg] + results
        if not results and follow:
            msgs.append({"role": "user", "content": follow})
    out.append(run_one(port, "opencode_turn3", "/v1/chat/completions", dict(base, messages=list(msgs)),
                       headers=oc))
    out.append(run_one(port, "hermes", "/v1/chat/completions", {
        "model": "TandemLLM", "stream": True, "max_tokens": max_tokens, "temperature": 0, "logprobs": True,
        "tools": tools, "messages": [{"role": "system", "content": "You are Hermes Agent, built by Nous "
                                                                   "Research. You are Hermes."},
                                     {"role": "user", "content": "What does the command `ls -la` show?"}]},
        headers={"User-Agent": HERMES_UA}))
    out.append(run_one(port, "raw_header", "/v1/chat/completions", {
        "model": "TandemLLM", "stream": True, "max_tokens": 200, "temperature": 0, "logprobs": True,
        "reasoning_format": "reasoning_content",
        "messages": [{"role": "user", "content": "Write a haiku about a compiler."}]},
        headers={"User-Agent": OPENCODE_UA, "X-Tandem": "raw"}))
    out.append(run_one(port, "opencode_title", "/v1/chat/completions", {
        "model": "TandemLLM", "stream": True, "stream_options": {"include_usage": True}, "temperature": 0.5,
        "max_tokens": 32000, "reasoning_effort": "low", "logprobs": True,
        "messages": [{"role": "system", "content": "You are a title generator. You output ONLY a thread title."},
                     {"role": "user", "content": "Generate a title for this conversation:\n"},
                     {"role": "user", "content": "List the Python files in this project."}]},
        headers={"User-Agent": OPENCODE_UA, "x-session-id": "ses_eng413b2"}))
    return out


def battery_b2f(port: int, model: str) -> list:
    """The monitor fixtures on the fake engine, under `model`: `TandemLLM` in chat mode (the conductor's
    directory on), the raw name with the monitor alone (`--conductor on`, no directory)."""
    tools = _opencode_tools()
    out = []
    for model in (model,):
        for name, text, with_tools, stream, think in FAKE_FIXTURES:
            body = {"model": model, "stream": stream, "max_tokens": 8000 if "LOOP" in text else 400,
                    "reasoning_format": "tags", "logprobs": True,
                    "messages": [{"role": "system", "content": "You are opencode."},
                                 {"role": "user", "content": text}]}
            if with_tools:
                body["tools"] = tools
            if not think:
                body.update(NO_THINK)
            out.append(run_one(port, f"{'tandemllm' if model == 'TandemLLM' else 'raw'}_{name}",
                               "/v1/chat/completions", body,
                               headers={"User-Agent": OPENCODE_UA, "x-session-id": f"ses_{name}"}))
        hist = []
        args = {"command": "python3 pagodacheck.py"}
        call = {"name": "bash", "arguments": json.dumps(args)}
        for i in range(3):
            hist += [{"role": "assistant", "content": "", "tool_calls": [
                {"id": f"call_{i}", "type": "function", "function": call}]},
                     {"role": "tool", "tool_call_id": f"call_{i}", "content": "RESULT: FAIL"}]
        out.append(run_one(port, f"{'tandemllm' if model == 'TandemLLM' else 'raw'}_c3_repeat",
                           "/v1/chat/completions", {
                               "model": model, "stream": True, "tools": tools, "max_tokens": 400,
                               "chat_template_kwargs": {"enable_thinking": False},
                               "messages": [{"role": "user", "content": "FAKE_CALL_JSON " + json.dumps(
                                   {"name": "bash", "arguments": args})}] + hist},
                           headers={"User-Agent": OPENCODE_UA, "x-session-id": "ses_c3"}))
    return out


#: the reads, each one JSON answer (`follow=0`: a snapshot of the live view and the log, not their streams)
B3_GETS = ("/health", "/v1/models", "/metrics", "/v1/replica/identity", "/readyz", "/v1/dashboard/live?follow=0",
           "/v1/dashboard/system", "/v1/dashboard/logs?follow=0", "/v1/dashboard/parallel",
           "/v1/dashboard/metrics", "/v1/dashboard/summary", "/v1/dashboard/usage", "/v1/dashboard/requests")


def battery_b3(port: int) -> dict:
    out = {}
    for path in B3_GETS:
        r = request(port, "GET", path, timeout=60)
        end = time.monotonic() + 120
        while path == "/v1/replica/identity" and r["status"] == 503 and time.monotonic() < end:
            time.sleep(1.0)                   # the identity is computed after the start: wait for it
            r = request(port, "GET", path, timeout=60)
        r.pop("s", None)
        out[path] = r
    return out


def read_journal(ledger: str) -> dict:
    """The ledger file's rows, read only: conductor_events, conductor_texts, requests."""
    uri = "file:" + os.path.abspath(ledger) + "?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    out = {}
    try:
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        for table, order in (("conductor_events", "rowid"), ("conductor_texts", "first_ms, rowid"),
                             ("requests", "rowid"), ("meta", "rowid")):
            if table in names:
                out[table] = [dict(r) for r in db.execute(f"SELECT * FROM {table} ORDER BY {order}")]
    finally:
        db.close()
    for row in out.get("conductor_events", []):
        for k, v in list(row.items()):
            if isinstance(v, str) and v[:1] in "[{":
                try:
                    row[k] = json.loads(v)
                except ValueError:
                    pass
    return out


# ---------------------------------------------------------------------------- the normaliser

def _volatile(key: str) -> bool:
    k = key.lower()
    return k in VOLATILE_KEYS or k.endswith(VOLATILE_SUFFIXES)


def _shape(x):
    if isinstance(x, dict):
        return {k: _shape(v) for k, v in sorted(x.items())}
    if isinstance(x, list):
        return [_shape(x[0])] if x else []
    return "<v>"


class Ordinals:
    def __init__(self):
        self.seen: dict = {}

    def __call__(self, v):
        key = json.dumps(v, sort_keys=True)
        if key not in self.seen:
            self.seen[key] = f"#{len(self.seen) + 1}"
        return self.seen[key]


def normalize(x, ords: Ordinals | None = None, key: str = ""):
    """N1-N3 on one JSON value (a new value; the input is not changed)."""
    ords = ords if ords is not None else Ordinals()
    if isinstance(x, dict):
        out = {}
        for k, v in sorted(x.items()):
            if k in SHAPE_KEYS:
                out[k] = _shape(v)
            elif _volatile(k):
                out[k] = "<v>"
            elif k in ORDINAL_KEYS and v is not None and not isinstance(v, (dict, list)):
                out[k] = ords(v)
            else:
                out[k] = normalize(v, ords, k)
        return out
    if isinstance(x, list):
        return [normalize(v, ords, key) for v in x]
    if isinstance(x, str) and ORDINAL_VALUE.match(x):
        return ords(x)
    return x


_TYPE = re.compile(r"^# TYPE (\S+) (\S+)")
_SAMPLE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{([^}]*)\})?\s")


def metric_families(text: str) -> list:
    """N4: [name, type, sorted label names] of every family in a Prometheus text answer."""
    fams, labels = {}, {}
    for ln in (text or "").splitlines():
        m = _TYPE.match(ln)
        if m:
            fams[m.group(1)] = m.group(2)
            continue
        m = _SAMPLE.match(ln)
        if m and not ln.startswith("#"):
            name = m.group(1)
            fam = next((f for f in (name, re.sub(r"_(bucket|sum|count|total|created)$", "", name))
                        if f in fams), name)
            keys = sorted(re.findall(r'([a-zA-Z_][a-zA-Z0-9_]*)="', m.group(3) or ""))
            labels.setdefault(fam, set()).update(k for k in keys if k != "le")
    return [[n, fams[n], sorted(labels.get(n, ()))] for n in sorted(fams)]


def _strip(x, prefixes: list):
    """N8: the run's own folders (`--strip`) inside any string become "<root>"."""
    if isinstance(x, dict):
        return {k: _strip(v, prefixes) for k, v in x.items()}
    if isinstance(x, list):
        return [_strip(v, prefixes) for v in x]
    if isinstance(x, str):
        for p in prefixes:
            x = x.replace(p, "<root>")
    return x


def normalize_record(doc: dict) -> dict:
    """The normalised form of one record file, as compare holds it."""
    prefixes = sorted((p.rstrip("/") for p in doc.get("strip") or [] if p and len(p) > 1), key=len, reverse=True)
    if prefixes:
        doc = _strip(doc, prefixes)
    kind = doc.get("set")
    if kind in ("b1", "b2", "b2f"):
        out = {"set": kind, "requests": {}}
        for rec in doc["requests"]:
            ords = Ordinals()
            gate = normalize(rec["gate"], ords)
            chunks = _sse_chunks(rec.get("sse")) if "sse" in rec else [rec.get("body")]
            out["requests"][rec["name"]] = {"gate": gate, "info": {"chunks": normalize(chunks, Ordinals())}}
        return out
    if kind == "b3":
        out = {"set": kind, "routes": {}}
        for path, r in sorted(doc["routes"].items()):
            item = {"status": r.get("status"), "content_type": (r.get("headers") or {}).get("content-type")}
            if path in ("/metrics", "/v1/dashboard/metrics") and "text" in r:
                item["families"] = metric_families(r.get("text") or "")
            elif path == "/v1/dashboard/metrics" and isinstance(r.get("body"), str):
                item["families"] = metric_families(r["body"])
            elif path.split("?")[0] in SHAPE_ROUTES:
                body = r.get("body")
                item["shape"] = _shape(body)
                item["empty_lists"] = sorted(k for k, v in (body.items() if isinstance(body, dict) else ())
                                             if isinstance(v, list) and not v)
            else:
                item["body"] = normalize(r.get("body", r.get("text")))
            out["routes"][path] = item
        return {"set": kind, "gate": out["routes"], "info": {}}
    if kind == "journal":
        ords = Ordinals()
        gate, info = {"route_monitor": [], "requests": []}, {"other_events": []}
        texts = {t["id"]: t["text"] for t in doc["rows"].get("conductor_texts", [])}
        for table in ("conductor_events", "requests"):
            for row in doc["rows"].get(table, []):
                r = {k: ("<v>" if table == "requests" and k in REQUEST_STATS else v) for k, v in row.items()}
                if r.get("hidden_text_id") in texts:
                    r["hidden_text"] = texts[r["hidden_text_id"]]
                r = normalize(r, ords)
                if table == "requests":
                    gate["requests"].append(r)
                elif row.get("kind") in JOURNAL_GATED:
                    gate["route_monitor"].append(r)
                else:
                    info["other_events"].append(r)
        meta = {r.get("key"): r.get("value") for r in doc["rows"].get("meta", []) if isinstance(r, dict)}
        gate["schema"] = {k: v for k, v in meta.items() if "version" in str(k)}
        return {"set": kind, "gate": gate, "info": info}
    raise SystemExit(f"eng413_baseline: unknown record set {kind!r}")


# ---------------------------------------------------------------------------- compare

def diffs(a, b, path: str = "") -> list:
    """[(path, kind, a, b)] where the two normalised values differ; kind: changed, removed, added."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            p = f"{path}.{k}" if path else str(k)
            if k not in b:
                out.append((p, "removed", a[k], None))
            elif k not in a:
                out.append((p, "added", None, b[k]))
            else:
                out += diffs(a[k], b[k], p)
        return out
    if isinstance(a, list) and isinstance(b, list):
        out = []
        for i in range(max(len(a), len(b))):
            p = f"{path}[{i}]"
            if i >= len(b):
                out.append((p, "removed", a[i], None))
            elif i >= len(a):
                out.append((p, "added", None, b[i]))
            else:
                out += diffs(a[i], b[i], p)
        return out
    if a != b or type(a) is not type(b):
        return [(path, "changed", a, b)]
    return []


def first_token_diff(a: dict, b: dict) -> str:
    """Where two requests' tokens part: the first differing logprobs entry or text id."""
    words = []
    for key in sorted(set((a.get("logprobs") or {})) | set((b.get("logprobs") or {}))):
        x, y = (a.get("logprobs") or {}).get(key) or [], (b.get("logprobs") or {}).get(key) or []
        i = next((i for i, (p, q) in enumerate(zip(x, y)) if p != q), None)
        if i is None and len(x) != len(y):
            i = min(len(x), len(y))
        if i is not None:
            words.append(f"logprobs.{key} token {i} of {len(x)}/{len(y)}: "
                         f"{(x[i] if i < len(x) else None)!r} vs {(y[i] if i < len(y) else None)!r}")
    for key in ("text_ids", "reasoning_ids", "prompt_ids"):
        x, y = a.get(key) or [], b.get(key) or []
        if x != y:
            i = next((i for i, (p, q) in enumerate(zip(x, y)) if p != q), min(len(x), len(y)))
            words.append(f"{key} index {i} of {len(x)}/{len(y)}")
    return "; ".join(words)


def compare_dirs(base: str, cand: str, allow=(), additive: bool = False) -> dict:
    files = sorted(f for f in os.listdir(base) if f.endswith(".json") and _is_record(os.path.join(base, f)))
    report = {"base": base, "cand": cand, "files": {}, "gate_fail": 0, "info_diff": 0}
    for f in files:
        p = os.path.join(cand, f)
        if not os.path.isfile(p):
            report["files"][f] = {"verdict": "MISSING"}
            report["gate_fail"] += 1
            continue
        with open(os.path.join(base, f)) as fa, open(p) as fb:
            a, b = normalize_record(json.load(fa)), normalize_record(json.load(fb))
        stem = f[:-5]
        entry = {"verdict": "SAME", "gate": [], "info": [], "allowed": []}
        if a["set"] in ("b1", "b2", "b2f"):
            for name in sorted(set(a["requests"]) | set(b["requests"])):
                ra, rb = a["requests"].get(name), b["requests"].get(name)
                if ra is None or rb is None:
                    entry["gate"].append(f"{name}: only in {'cand' if ra is None else 'base'}")
                    continue
                d = _filter(diffs(ra["gate"], rb["gate"], f"{stem}.{name}.gate"), allow, additive, entry)
                if d:
                    entry["gate"].append(f"{name}: {len(d)} paths differ, first {d[0][0]}; "
                                         f"{first_token_diff(ra['gate'], rb['gate']) or 'tokens equal'}")
                di = diffs(ra["info"], rb["info"], f"{stem}.{name}.info")
                if di:
                    entry["info"].append(f"{name}: the stream's chunks differ ({len(di)} paths, first {di[0][0]})")
        else:
            d = _filter(diffs(a["gate"], b["gate"], stem), allow, additive, entry)
            entry["gate"] += [f"{p} {k}: {json.dumps(x)[:120]} -> {json.dumps(y)[:120]}" for p, k, x, y in d]
            di = diffs(a["info"], b["info"], stem + ".info")
            entry["info"] += [f"{p} {k}: {json.dumps(x)[:80]} -> {json.dumps(y)[:80]}" for p, k, x, y in di]
        if entry["gate"]:
            entry["verdict"] = "DIFF"
            report["gate_fail"] += 1
        report["info_diff"] += len(entry["info"])
        report["files"][f] = entry
    for f in sorted(os.listdir(cand)):
        if f.endswith(".json") and f not in report["files"] and _is_record(os.path.join(cand, f)):
            report["files"][f] = {"verdict": "ONLY IN CAND"}
    return report


def _filter(ds: list, allow, additive: bool, entry: dict) -> list:
    keep = []
    for d in ds:
        if any(_glob(g, d[0]) for g in allow) or (additive and d[1] == "added"):
            entry["allowed"].append(f"{d[0]} {d[1]}")
        else:
            keep.append(d)
    return keep


def _glob(pattern: str, path: str) -> bool:
    """`*` any run of characters, `?` one; everything else literal (paths hold `[0]`)."""
    rx = re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".")
    return re.fullmatch(rx, path) is not None


def _is_record(path: str) -> bool:
    try:
        with open(path) as f:
            head = f.read(200)
    except OSError:
        return False
    return '"eng413_baseline"' in head


# ---------------------------------------------------------------------------- the commands

def save(out_dir: str, name: str, doc: dict) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, name + ".json")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(dict({"eng413_baseline": FORMAT}, **doc), f, indent=1, sort_keys=False)
        f.write("\n")
    os.replace(tmp, path)
    return path


def cmd_capture(a) -> int:
    _guard(a.port)
    t0 = time.monotonic()
    model = a.model or served_name(a.port)
    doc = {"set": a.set, "port": a.port, "model": model, "strip": a.strip or [],
           "captured": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if a.set == "b1":
        doc["requests"] = battery_b1(a.port, model, a.max_tokens)
    elif a.set == "b2":
        doc["requests"] = battery_b2(a.port, a.max_tokens_chat)
    elif a.set == "b2f":
        doc["requests"] = battery_b2f(a.port, model)
    else:
        doc["routes"] = battery_b3(a.port)
    doc["seconds"] = round(time.monotonic() - t0, 1)
    path = save(a.out, a.name or a.set, doc)
    if "requests" in doc:
        bad = [r["name"] for r in doc["requests"] if r["status"] != 200 or r["wire"].get("error")]
        toks = sum(len(v) for r in doc["requests"] for v in r["gate"]["logprobs"].values())
        print(f"[baseline] {a.set}: {len(doc['requests'])} requests, {toks} logprobs entries, "
              f"failed {bad or 'none'}, {doc['seconds']} s -> {path}")
        return 1 if bad else 0
    codes = {p: r["status"] for p, r in doc["routes"].items()}
    print(f"[baseline] b3: {codes}, {doc['seconds']} s -> {path}")
    return 0


def cmd_journal(a) -> int:
    rows = read_journal(a.ledger)
    path = save(a.out, a.name, {"set": "journal", "rows": rows, "strip": a.strip or []})
    print(f"[baseline] journal: " + ", ".join(f"{k} {len(v)}" for k, v in rows.items()) + f" -> {path}")
    return 0


def cmd_compare(a) -> int:
    rep = compare_dirs(a.base, a.cand, allow=a.allow or (), additive=a.additive)
    for f, e in rep["files"].items():
        print(f"[compare] {e['verdict']:<12} {f}")
        for line in e.get("gate", [])[:20]:
            print(f"    gate  {line}")
        for line in e.get("info", [])[:10]:
            print(f"    info  {line}")
        if e.get("allowed"):
            print(f"    allowed {len(e['allowed'])}: {e['allowed'][:5]}")
    print(f"[compare] {len(rep['files'])} files, {rep['gate_fail']} with a gated difference, "
          f"{rep['info_diff']} info differences")
    if a.json:
        with open(a.json, "w") as f:
            json.dump(rep, f, indent=1)
    return 1 if rep["gate_fail"] else 0


def cmd_normalize(a) -> int:
    with open(a.file) as f:
        json.dump(normalize_record(json.load(f)), sys.stdout, indent=1, sort_keys=True)
    print()
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture")
    c.add_argument("--port", type=int, required=True)
    c.add_argument("--set", required=True, choices=("b1", "b2", "b2f", "b3"))
    c.add_argument("--out", required=True)
    c.add_argument("--name", default="", help="the record's file name (default: the set)")
    c.add_argument("--model", default="", help="b1/b2f: the raw model name (default: /v1/models' first)")
    c.add_argument("--max-tokens", type=int, default=64, help="b1: a raw completion's tokens")
    c.add_argument("--max-tokens-chat", type=int, default=1024, help="b2: a chat turn's tokens")
    c.add_argument("--strip", action="append", help="a folder of this run, \"<root>\" when compared (N8)")
    j = sub.add_parser("journal")
    j.add_argument("--ledger", required=True)
    j.add_argument("--out", required=True)
    j.add_argument("--name", required=True)
    j.add_argument("--strip", action="append", help="a folder of this run (N8)")
    m = sub.add_parser("compare")
    m.add_argument("base")
    m.add_argument("cand")
    m.add_argument("--allow", action="append", help="a path glob whose difference is accepted")
    m.add_argument("--additive", action="store_true", help="accept paths only the candidate has")
    m.add_argument("--json", default="")
    n = sub.add_parser("normalize")
    n.add_argument("file")
    a = ap.parse_args(argv)
    return {"capture": cmd_capture, "journal": cmd_journal, "compare": cmd_compare,
            "normalize": cmd_normalize}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
