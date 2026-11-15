"""One ledger file, two owners (ENG-413 WP2b, QA ENG-432): the engine's copy of the usage ledger
(server/api/ledger.py) and TandemApp's conductor journal share the file as before.

The engine writes the request tables (`requests`, `meta`); the conductor's journal
(tandemapp/conductor/decisions.py, run in the engine's process in chat mode) writes
`conductor_events` and `conductor_texts`. WP2b moved the engine's side into engine code and changed
neither the schema nor who writes which table. TandemApp's own ledger module is the reference: it
is the code the engine ran before the move.

  1  the engine's schema is TandemApp's: the same version, columns, migrations and DDL (the
     conductor's tables included, which both create as before); a new file and a version-1 file
     opened by each copy end with the same tables, indexes, columns, rows and meta; the side
     shapes the Hermes check reads are the conductor's        test_the_schema_is_tandemapps
  2  each side writes its own tables: on one file, the engine's ledger inserts, amends and prunes
     only `requests` and `meta`, the conductor's journal only its two tables (every write seen by
     SQLite's authorizer); TandemApp's ledger in the engine's place writes the same tables
                                                                  test_each_side_writes_its_own_tables
  3  a replay of the same requests (clients told apart by their headers and body, refusals, an
     internal question, a relayed row, an amend) through each copy's record and ledger gives
     equal files: every row of `requests`, the meta but for the clock   test_rows_equal_on_a_replay

CPU, standard library only (sqlite3). Run: python tests/test_ledger_owners.py
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import pairapp  # noqa: E402,F401  (a pair test: TandemApp from TANDEMAPP_SRC, ENG-413 WP14)
sys.path.insert(1, pairapp.APP)  # TandemApp's packages: the reference copy
sys.path.insert(0, HERE)

from server.api import ledger as eng_ledger, usage as eng_usage  # noqa: E402
from tandemapp import ledger as app_ledger, usage as app_usage  # noqa: E402
from tandemapp.conductor import decisions, signals  # noqa: E402

REQUEST_TABLES = {"requests", "meta"}
JOURNAL_TABLES = {"conductor_events", "conductor_texts"}
WRITES = {sqlite3.SQLITE_INSERT: "insert", sqlite3.SQLITE_UPDATE: "update",
          sqlite3.SQLITE_DELETE: "delete"}


def _path(tag: str) -> str:
    return os.path.join(tempfile.mkdtemp(prefix=f"ledger-owners-{tag}-"), "usage", "ledger.sqlite3")


def _schema(path: str) -> list[tuple]:
    con = sqlite3.connect(path)
    try:
        return sorted(con.execute("SELECT type, name, tbl_name, sql FROM sqlite_master "
                                  "WHERE name NOT LIKE 'sqlite_%'").fetchall(), key=repr)
    finally:
        con.close()


def _table(path: str, table: str, order: str = "rowid") -> list[tuple]:
    con = sqlite3.connect(path)
    try:
        return con.execute(f"SELECT * FROM {table} ORDER BY {order}").fetchall()
    finally:
        con.close()


def _meta(path: str) -> dict:
    """The meta rows, the clock's values left out (created_at, a migration's time)."""
    return {k: (v if k != "created_at" and not k.startswith("migrated_") else "<t>")
            for k, v in _table(path, "meta", "key")}


def _quiet() -> types.SimpleNamespace:
    return types.SimpleNamespace(log=lambda msg: None)


# ----------------------------------------------------------------------------- 1. the schema

def _v1_file(path: str) -> None:
    """A version-1 ledger as the design note made it, with one router question row of before
    VIS-60 (relabelled `internal` by the migration to 6) and one client row."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(app_ledger.DDL)
    con.executemany("INSERT INTO meta(key, value) VALUES (?, ?)",
                    [("schema_version", "1"), ("created_at", "1700000000000")])
    cols = ("ts_ms", "request_id", "model", "client_id", "client_kind", "endpoint", "stream",
            "status", "finish_reason", "prompt_tokens", "completion_tokens", "max_tokens")
    con.executemany(f"INSERT INTO requests ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                    [(1700000000100, "chatcmpl-a", "qwen-raw", "anon", "other", "chat", 0, 200,
                      "stop", 40, 3, 24),
                     (1700000000200, "chatcmpl-b", "TandemLLM", "k:0123456789ab", "opencode",
                      "chat", 1, 200, "stop", 1200, 88, 4096)])
    con.commit()
    con.close()


def test_the_schema_is_tandemapps():
    names = ("PRODUCTION", "SCHEMA_VERSION", "MIN_RETENTION_DAYS", "QUEUE_MAX", "BACKUP_KEEP",
             "COLUMNS", "ADDED", "AMENDABLE", "CONDUCTOR_DDL", "CONDUCTOR_TABLES", "DDL", "KINDS",
             "INTERNAL_UA", "PARENT_HEADER", "_OLD_INTERNAL", "HERMES_TOOLS", "HERMES_TOOLS_MIN",
             "HERMES_IDENTITY")
    differ = [n for n in names if getattr(eng_ledger, n) != getattr(app_ledger, n)]
    assert not differ, differ
    # the journal creates its tables from TandemApp's text, the engine's ledger from its own: equal
    assert decisions.CONDUCTOR_DDL == eng_ledger.CONDUCTOR_DDL
    assert eng_ledger.SIDE_SHAPES == signals.SIDE_SHAPES
    new = {}
    for tag, mod in (("eng", eng_ledger), ("app", app_ledger)):
        p = _path(tag + "-new")
        led = mod.Ledger(p, log=_quiet().log).open()
        led.close()
        new[tag] = (_schema(p), _meta(p), led.schema_was)
    assert new["eng"] == new["app"], (new["eng"], new["app"])
    tables = {r[1] for r in new["eng"][0] if r[0] == "table"}
    assert tables == REQUEST_TABLES | JOURNAL_TABLES, tables
    old = {}
    for tag, mod in (("eng", eng_ledger), ("app", app_ledger)):
        p = _path(tag + "-v1")
        _v1_file(p)
        led = mod.Ledger(p, log=_quiet().log).open()
        led.close()
        old[tag] = (_schema(p), _meta(p), _table(p, "requests"), led.schema_was)
    assert old["eng"] == old["app"], "a version-1 file migrates differently"
    assert old["eng"][3] == 1 and old["eng"][1]["relabel_internal"] == "1", old["eng"][1]
    assert old["eng"][0] == new["eng"][0], "a migrated file and a new one differ in schema"
    return f"schema {eng_ledger.SCHEMA_VERSION}, {len(eng_ledger.COLUMNS)} columns, " \
           f"{len(new['eng'][0])} schema objects equal; a v1 file migrates alike"


# ----------------------------------------------------------------------------- 2. the writers

class _Watch:
    """A stand-in for a module's `sqlite3` whose connections report every row write: table and
    kind, through SQLite's authorizer, so a write in any statement shape is seen."""

    def __init__(self):
        self.writes: set[tuple[str, str]] = set()
        self.mod = types.SimpleNamespace(**{k: getattr(sqlite3, k) for k in dir(sqlite3)
                                            if not k.startswith("__")})
        self.mod.connect = self.connect

    def connect(self, *a, **kw):
        con = sqlite3.connect(*a, **kw)

        def auth(action, arg1, arg2, db, source):
            if action in WRITES and arg1 and not arg1.startswith("sqlite_"):
                self.writes.add((arg1, WRITES[action]))
            return sqlite3.SQLITE_OK
        con.set_authorizer(auth)
        return con


def _write_through(mod_ledger, mod_usage, path: str, with_journal: bool):
    """The engine's side (a request row, an amend, the nightly prune) and, beside it on the same
    file, the conductor's journal (a route row with a note, a monitor row, an update, a fill, its
    prune). Returns the two watches."""
    eng_w, jr_w = _Watch(), _Watch()
    real_l, real_d = mod_ledger.sqlite3, decisions.sqlite3
    mod_ledger.sqlite3 = eng_w.mod
    decisions.sqlite3 = jr_w.mod
    try:
        led = mod_ledger.Ledger(path, log=_quiet().log).open()
        jr = decisions.Journal(path) if with_journal else None
        rec = mod_usage.RequestRecord("chatcmpl-own0000000000000000000000", "chat", True,
                                      t_arrival=10.0, ts=1_700_000_000.0)
        rec.model, rec.finish_reason, rec.prompt_tokens, rec.completion_tokens = "m", "stop", 9, 4
        rec.t_lock, rec.t_first, rec.t_last, rec.t_end = 10.1, 10.2, 10.4, 10.5
        led.submit(rec.row("0.0.0-test", "c0de"))
        led.flush()
        led.amend(rec.request_id, {"decode_tps": 12.5, "engine_finish": "stop"})
        led.flush()
        led.prune(now=time.time() + 10 * 400 * 86400)          # every row is old by then
        if jr is not None:
            row = jr.open_row({"ts_ms": 1_700_000_000_000, "request_id": rec.request_id,
                               "session_key": "s1", "kind": "route", "task": "route",
                               "class_id": "code_edit",
                               "injected": [{"text": "a note the model saw", "tokens": 5}]})
            jr.close_row(row)
            jr.monitor_sink({"ts": 1_700_000_000.5, "request_id": rec.request_id, "session": "s1",
                             "task": "E1", "mode": "act", "decision": "CONTINUE_HIDDEN",
                             "text": "continue", "hidden_tokens": 3})
            jr.update(row["id"], label="ok")
            jr.monitor_sink({"decision": "OUTCOME", "request_id": rec.request_id,
                             "outcome": "answered", "acts": 1})
            jr.flush()
            con = jr_w.connect(path)
            jr.prune(con, now=time.time() + 10 * 30 * 86400)
            con.commit()
            con.close()
            jr.close()
        led.close()
    finally:
        mod_ledger.sqlite3, decisions.sqlite3 = real_l, real_d
    return eng_w, jr_w


def test_each_side_writes_its_own_tables():
    eng_w, jr_w = _write_through(eng_ledger, eng_usage, _path("shared"), True)
    eng_tables = {t for t, _ in eng_w.writes}
    jr_tables = {t for t, _ in jr_w.writes}
    assert eng_tables == REQUEST_TABLES, eng_w.writes
    assert {("requests", k) for k in ("insert", "update", "delete")} <= eng_w.writes, eng_w.writes
    assert jr_tables == JOURNAL_TABLES, jr_w.writes
    assert not eng_tables & jr_tables
    # before the move: TandemApp's ledger in the engine's place writes exactly the same
    app_w, _ = _write_through(app_ledger, app_usage, _path("before"), False)
    assert app_w.writes == eng_w.writes, (app_w.writes, eng_w.writes)
    return f"engine {sorted(eng_w.writes)}; journal {sorted(jr_tables)}; the same as before"


# ----------------------------------------------------------------------------- 3. a replay

HEADERS = [
    {"User-Agent": "Python/3.12 aiohttp/3.11.2", "Authorization": "Bearer sk-owui-connection"},
    {"User-Agent": "opencode/1.18.32 ai-sdk/provider-utils/4.0.23 runtime/bun/1.3.14"},
    {"User-Agent": "OpenAI/Python 2.24.0", "Authorization": "Bearer sk-hermes"},
    {"User-Agent": "tandem-conductor/1", "X-Tandem-Parent": "chatcmpl-parent"},
    {"User-Agent": "curl/8.5.0"},
    {"Referer": "http://box.local/dashboard/#/usage"},
    {"X-QSE-Client": "chat-app"},
    {},
]
BODIES = [
    {"messages": [{"role": "user", "content": "hi"}]},
    {"messages": [{"role": "user", "content": "x"}], "tools": [
        {"type": "function", "function": {"name": "read"}}]},
    {"messages": [{"role": "system", "content": signals.SIDE_SHAPES[2]["match"] + " More."},
                  {"role": "user", "content": "a title"}]},
    {"messages": [{"role": "user", "content": "q"}], "max_tokens": 24},
    None,
    {"messages": [{"role": "user", "content": "page"}]},
    {"messages": [{"role": "user", "content": [{"type": "text", "text": "chat"}]}]},
    {"messages": [{"role": "system", "content": "You are Hermes Agent, built by Nous Research."}]},
]


def _replay(mod_ledger, mod_usage, path: str) -> None:
    led = mod_ledger.Ledger(path, log=_quiet().log).open()
    for i, (h, body) in enumerate(zip(HEADERS * 3, BODIES * 3)):
        rec = mod_usage.RequestRecord(f"chatcmpl-{i:024x}", "chat" if i % 3 else "completions",
                                      bool(i % 2), t_arrival=100.0 + i, ts=1_700_000_000.0 + i)
        rec.client_id, rec.client_kind = mod_ledger.client_of(h, body)
        rec.model = "TandemLLM" if i % 4 == 0 else "qwen-raw"
        if i % 7 == 3:                                   # refused before the lock
            rec.status, rec.finish_reason, rec.error_type = 503, "refused", "busy"
        else:
            rec.prompt_tokens, rec.cached_tokens = 100 + 13 * i, 7 * i
            rec.completion_tokens, rec.reasoning_tokens = 20 + i, i % 5
            rec.t_lock, rec.t_first = 100.0 + i + 0.05, 100.0 + i + 0.3
            rec.t_last, rec.t_end = 100.0 + i + 1.1, 100.0 + i + 1.2
            rec.blocks, rec.draft_n, rec.draft_accepted = 4 + i, 30 + i, 20 + i
            rec.finish_reason = ("stop", "length", "tool_calls")[i % 3]
            rec.tool_calls, rec.thinking = i % 2, bool(i % 3)
            rec.cache_source = ("none", "resident", "prefix", "disk")[i % 4]
            rec.max_tokens = 4096
        if h.get("X-Tandem-Parent"):
            rec.parent_request = h["X-Tandem-Parent"]
        rec.slot = i % 3 if i % 5 else None
        row = rec.row("0.15.4-test", "c0de")
        if i == 5:                                       # a relayed row (the proxy's columns)
            row.update(backend="cloud", backend_kind="openai", backend_model="m-up",
                       public_model="m", usage_source="backend", cost_micros=1234, retries=1,
                       upstream_request="resp-1", node="n1", deployment="d1")
        led.submit(row)
    led.flush()
    led.amend("chatcmpl-" + f"{5:024x}", {"decode_tps": 41.5, "preemptions": 2, "l2_join": "exact",
                                          "prompt": "not amendable"})
    led.flush()
    led.close()


def test_rows_equal_on_a_replay():
    a, b = _path("replay-eng"), _path("replay-app")
    _replay(eng_ledger, eng_usage, a)
    _replay(app_ledger, app_usage, b)
    ra, rb = _table(a, "requests"), _table(b, "requests")
    assert len(ra) == len(HEADERS) * 3, len(ra)
    differ = [i for i, (x, y) in enumerate(zip(ra, rb)) if x != y]
    assert ra == rb, f"rows differ at {differ[:5]}: {ra[differ[0]] if differ else None} != " \
                     f"{rb[differ[0]] if differ else None}"
    assert _meta(a) == _meta(b), (_meta(a), _meta(b))
    assert _schema(a) == _schema(b)
    kinds = sorted({r[5] for r in ra})                   # client_kind
    assert {"hermes", "internal", "open-webui", "opencode", "dashboard", "chat-app",
            "curl"} <= set(kinds), kinds
    return f"{len(ra)} rows equal, {len(eng_ledger.COLUMNS)} columns each; kinds {kinds}"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
