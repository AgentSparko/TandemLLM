"""The Memory screen's routes on a running engine with TandemApp's extension (VIS-48, QA VIS-49):
the session list, one transcript, the keep toggle, the exports and the admin token on every memory
route, reads included, through the fake engine of tests/test_conductor_e2e.py.

The transcript writer's own cases (the list, paging at 200 blocks, a transcript read while it is
written, a session without meta.json, the keep toggle the prune honours) are TandemApp's
tests/test_memory_api.py since ENG-413 WP9; this file is the pair of both trees (WP13).
All text is synthetic.

Run: python tests/test_memory_api.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


# ------------------------------------------------------------------------- the fake engine
def test_the_memory_routes_on_the_fake_engine():
    from tests.test_conductor_e2e import PROXY, TOOLS, _call, _check, _port, _server, _wait
    root = tempfile.mkdtemp(prefix="qse-mem-")
    cdir = os.path.join(root, "conductor")
    port = _port()
    proc, _ = _server(port, cdir)
    try:
        _wait(port, proc)
        for i, s in enumerate(("ses_a", "ses_b")):
            st, _, _ = _call(port, "POST", "/v1/chat/completions",
                             {"model": "TandemLLM", "tools": TOOLS, "max_tokens": 200,
                              "messages": [{"role": "user", "content": f"run the tool {i}"}]},
                             headers={"x-session-id": s})
            assert st == 200
        deadline = time.time() + 10
        lst = None
        while time.time() < deadline:
            st, _, lst = _call(port, "GET", "/v1/conductor/memory/sessions")
            # a first block is on disk a moment before its meta.json: wait for both
            if st == 200 and sum(s["has_meta"] for s in lst["sessions"]) == 2:
                break
            time.sleep(0.2)
        _check(lst, "memory-sessions")
        assert {s["session"] for s in lst["sessions"]} == {"c:ses_a", "c:ses_b"}, lst
        assert lst["writer"]["blocks"] == 2 and lst["writer"]["settings"]["scope"] == "tools"
        st, _, one = _call(port, "GET", "/v1/conductor/memory/sessions/c:ses_a")
        assert st == 200, one
        _check(one, "memory-session")
        assert one["blocks"][0]["turn"] == 1 and "### tool call" in one["blocks"][0]["text"]
        st, _, k = _call(port, "PATCH", "/v1/conductor/memory/sessions/c:ses_a/keep",
                         {"keep": True})
        assert st == 200 and k["keep"] is True, k
        _check(k, "memory-keep")
        st, _, md = _call(port, "GET", "/v1/conductor/memory/sessions/c:ses_a/export?format=md",
                          raw=True)
        assert st == 200 and md.decode().startswith("## turn 1 · ")
        st, _, js = _call(port, "GET", "/v1/conductor/memory/sessions/c:ses_a/export?format=json")
        _check(js, "memory-session")
        st, _, ex = _call(port, "GET", "/v1/conductor/sessions/c:ses_a/export?format=json")
        assert st == 200 and ex["transcript"] == {
            "blocks": 1, "export": "/v1/conductor/memory/sessions/c:ses_a/export"}
        _check(ex, "export")
        assert _call(port, "GET", "/v1/conductor/memory/sessions/c:nobody")[0] == 404
        assert _call(port, "PATCH", "/v1/conductor/memory/sessions/c:ses_a/keep",
                     {"keep": "yes"})[0] == 400
        # the transcripts need the admin token from off the box, reads included; the session
        # list of the journal follows the dashboard's rule as before
        for path in ("/v1/conductor/memory/sessions", "/v1/conductor/memory/sessions/c:ses_a",
                     "/v1/conductor/memory/sessions/c:ses_a/export?format=md"):
            st, _, err = _call(port, "GET", path, headers=PROXY, token=None)
            assert st == 401, (path, st, err)
        assert _call(port, "PATCH", "/v1/conductor/memory/sessions/c:ses_a/keep", {"keep": False},
                     headers=PROXY, token=None)[0] == 401
        assert _call(port, "GET", "/v1/conductor/memory/sessions", headers=PROXY)[0] == 200
    finally:
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
