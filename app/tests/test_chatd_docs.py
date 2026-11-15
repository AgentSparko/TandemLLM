"""The docs for chatd's lifecycle and the Chat tab's down state (OPS-157, QA OPS-158; GitHub issue #3),
taken from the engine's tests with the pages they read (ENG-413 WP11). Since engine API 1.7 TandemNode
owns chatd on an engine box, and the pages say so.

  1  docs/chat.md: TandemNode owns chatd, the engine runs none, the watcher's rule (3 starts an
     hour, never an engine restart), tandem-node chat start|stop|restart|status|logs, bin/tandem
     chat runs them, QSE_CHATD                                               test_the_normal_path
  2  docs/chat.md names the down states and the command for each, and that the mock loads only for
     ?chat=mock                                                              test_the_down_state
  3  no doc names a qwen38-spark-engine path for chatd; the launcher is ops/chatd.sh (chatd/ops in a
     wheel)                                                                  test_paths_match_the_installer
  4  the manual's chapter says the same                                      test_the_manual_follows

Run: python tests/test_chatd_docs.py
"""

from __future__ import annotations

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*p: str) -> str:
    return open(os.path.join(ROOT, *p)).read()


def _section(text: str, start: str) -> str:
    i = text.index(start)
    j = text.find("\n## ", i + len(start))
    return text[i:j if j > 0 else None]


def test_the_normal_path():
    s = " ".join(_section(_read("docs", "chat.md"), "## Starting and stopping").split())
    assert "TandemNode, TandemApp's agent there, owns it" in s
    assert "the engine has no chatd launcher" in s and "`QSE_CHATD=node`" in s
    assert "at most 3 times an hour" in s and "never restarts the engine for it" in s
    assert "It starts none in setup mode" in s
    assert "tandem-node chat start | stop | restart" in s and "tandem-node chat status" in s
    assert "tandem-node chat logs" in s
    assert "`~/TandemLLM/bin/tandem chat start | stop | restart | status | logs` runs `tandem-node chat`" in s


def test_the_down_state():
    s = _section(_read("docs", "chat.md"), "## When the Chat tab says the chat service is not running")
    for state, cmd in (("The chat service is not running.", "tandem chat start"),
                       ("does not accept this server's key.", "tandem chat restart && tandem restart"),
                       ("has no Chat tab server.", "run the installer again"),
                       ("Cannot reach the engine server.", "tandem status")):
        row = next(ln for ln in s.splitlines() if state in ln)
        assert cmd in row, row
    assert "`?chat=mock`" in s and "never falls back to an in-browser mock" in s


def test_paths_match_the_installer():
    for p in (("docs", "chat.md"), ("manual", "chapters", "chat.tex"), ("ops", "chatd.service")):
        text = _read(*p)
        for ln in text.splitlines():
            if "chatd" in ln:
                assert "qwen38-spark-engine" not in ln, (p, ln)
    chat = " ".join(_read("docs", "chat.md").split())
    assert "The launcher is TandemApp's `ops/chatd.sh`" in chat and "`chatd/ops/chatd.sh` inside an installed wheel" in chat
    assert "bash app/ops/chatd.sh start             #" not in chat
    assert os.path.isfile(os.path.join(ROOT, "ops", "chatd.sh"))


def test_the_manual_follows():
    tex = " ".join(_read("manual", "chapters", "chat.tex").split())
    assert r"On an engine box TandemNode owns chatd (SRV-823)" in tex
    assert "starts a chatd that died at most 3 times an hour, and never restarts the engine for it" in tex
    assert "tandem-node chat start | stop | restart | logs" in tex and "QSE_CHATD=off" in tex
    assert r"\code{~/TandemLLM/bin/tandem chat} runs the same commands through TandemNode" in tex


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  {name:50s} ok")
            n += 1
    print(f"{n} passed")
