"""Per-session transcripts (SRV-137, QA SRV-138) on the fake engine: the conductor that the
engine's server runs in its process until ENG-413, over HTTP. TandemApp's
app/tests/test_transcripts.py holds the matrix, the unit cases and the golden files; these two
cases start server/app.py, so they stay with the engine.

  3 tools-only scope, 5 secrets are never stored   test_3_5_on_the_fake_engine_scope_and_secrets
      (a chat without tools writes counters, a request with tools a block; a grep of memory/
      finds none of the values and the journal holds the counts, never a value)
  6 a write failure does not fail the request      test_6_on_the_fake_engine_a_read_only_memory_
      directory_fails_nothing (the memory directory read-only)
All text here is synthetic.

Run: python tests/test_transcripts_e2e.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.append(os.path.join(pairapp.APP, "tests"))  # SRV-747: TandemApp's test helpers, until ENG-413

import pairapp  # noqa: E402,F401  (a pair test: TandemApp from TANDEMAPP_SRC, ENG-413 WP14)
from test_transcripts import SYSTEM, _secrets  # noqa: E402


# ------------------------------------------------------------------------- the fake engine
def _fake(port, cdir):
    from tests.test_conductor_e2e import _server, _wait
    proc, ledger = _server(port, cdir)
    _wait(port, proc)
    return proc, ledger


def _chat(port, messages, tools=True, session="ses_e2e"):
    from tests.test_conductor_e2e import TOOLS, _call
    body = {"model": "TandemLLM", "messages": messages, "max_tokens": 300}
    if tools:
        body["tools"] = TOOLS
    return _call(port, "POST", "/v1/chat/completions", body, headers={"x-session-id": session})


def _memory_rows(ledger):
    conn = sqlite3.connect(ledger)
    rows = conn.execute("SELECT task, decision, session_key, detail FROM conductor_events WHERE "
                        "kind = 'memory'").fetchall()
    conn.close()
    return [(t, d, s, json.loads(x) if x else None) for t, d, s, x in rows]


def test_3_5_on_the_fake_engine_scope_and_secrets():
    from tests.test_conductor_e2e import _port
    root = tempfile.mkdtemp(prefix="qse-mem-")
    cdir = os.path.join(root, "conductor")
    port = _port()
    proc, ledger = _fake(port, cdir)
    shapes = _secrets(11)
    try:
        st, _, r = _chat(port, [{"role": "user", "content": "hello, just chatting"}], tools=False,
                         session="ses_chat")
        assert st == 200, r
        msgs = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": "Read the config and run the tool."},
                {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function",
                 "function": {"name": "bash", "arguments": "{\"command\": \"cat .env\"}"}}]},
                {"role": "tool", "tool_call_id": "c1",
                 "content": shapes[2]["text"] + "\n" + shapes[9]["text"] + "\n" + shapes[0]["text"]}]
        st, _, r = _chat(port, msgs)
        assert st == 200 and r["choices"][0]["message"].get("tool_calls"), r
        mem = os.path.join(cdir, "memory", "sessions")
        deadline = time.time() + 10
        while time.time() < deadline and len(_memory_rows(ledger)) < 1:
            time.sleep(0.2)
        months = os.listdir(mem)
        chat = os.path.join(mem, months[0], "c-ses_chat")
        work = os.path.join(mem, months[0], "c-ses_e2e")
        assert not os.path.exists(os.path.join(chat, "transcript.md"))
        assert json.load(open(os.path.join(chat, "meta.json")))["counts"]["untranscribed"] == 1
        text = open(os.path.join(work, "transcript.md")).read()
        assert text.startswith("## turn 1 · ") and "### tool result (bash, " in text
        assert "### tool call (bash)" in text and "### runtime" in text
        assert re.search(r"^redactions: 3 \(api_key 1, bearer 1, private_key 1\)$", text, re.M), text
        blob = b"".join(open(os.path.join(dp, f), "rb").read()
                        for dp, _, fs in os.walk(os.path.join(cdir, "memory")) for f in fs)
        for s in (shapes[2], shapes[9], shapes[0]):
            for line in s["secret"].splitlines():
                assert line.encode() not in blob, s["kind"]
        rows = [r for r in _memory_rows(ledger) if r[1] == "block"]
        assert rows and rows[0][2] == "c:ses_e2e" and rows[0][3]["redactions"] == 3
        conn = sqlite3.connect(ledger)
        dump = "\n".join(str(x) for x in conn.execute("SELECT * FROM conductor_events"))
        conn.close()
        assert shapes[2]["secret"] not in dump and shapes[9]["secret"] not in dump
        # the raw path never writes a transcript
        from tests.test_conductor_e2e import TOOLS, _call
        st, _, _ = _call(port, "POST", "/v1/chat/completions",
                         {"model": "TandemLLM", "messages": msgs, "tools": TOOLS,
                          "max_tokens": 50}, headers={"x-session-id": "ses_raw", "X-Tandem": "raw"})
        assert st == 200
        time.sleep(0.5)
        assert not os.path.exists(os.path.join(mem, months[0], "c-ses_raw"))
        # nothing of the memory left the conductor's directory
        for dp, _, fs in os.walk(root):
            for f in fs:
                if f in ("transcript.md", "meta.json"):
                    assert os.path.realpath(dp).startswith(os.path.realpath(cdir)), dp
    finally:
        proc.kill()
        shutil.rmtree(root, ignore_errors=True)


def test_6_on_the_fake_engine_a_read_only_memory_directory_fails_nothing():
    from tests.test_conductor_e2e import _port, _stream
    root = tempfile.mkdtemp(prefix="qse-mem-")
    cdir = os.path.join(root, "conductor")
    os.makedirs(os.path.join(cdir, "memory"), mode=0o500)
    port = _port()
    proc, ledger = _fake(port, cdir)
    try:
        text, hdrs, usage = _stream(port, [{"role": "user", "content": "run the tool please"}],
                                    headers={"x-session-id": "ses_ro"})
        assert usage and usage["completion_tokens"] > 0 and text, (text, usage)
        deadline = time.time() + 10
        while time.time() < deadline and not _memory_rows(ledger):
            time.sleep(0.2)
        rows = _memory_rows(ledger)
        assert rows and rows[0][:2] == ("transcript", "write_failed"), rows
        assert not os.listdir(os.path.join(cdir, "memory"))
    finally:
        os.chmod(os.path.join(cdir, "memory"), 0o700)
        proc.kill()
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  {name:64s} ok")
            passed += 1
    print(f"{passed} passed")
