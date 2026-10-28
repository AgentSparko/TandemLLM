"""TandemApp's fleet store (SRV-729): the metric history, the event log, the alerts and the rule
edits, in one SQLite file beside the config database (`fleet.sqlite3`, WAL, mode 0600).

    store = Store(path, retention_days=30, flush_s=300).open()   ":memory:" keeps nothing
    store.ring(key)                 a series' Ring, made at its first sample
    store.flush(now)                the slots changed since the last flush, one transaction
    store.close()                   all of it, the 2 s tier too, and the clean stop's time

METRICS. A series (`("node", id, metric)` or `("backend", id, metric)`) is a `Ring`: per slot the
sum, count and largest of its samples, in three tiers. Slot k of a tier holds the samples in
((k-1)·res, k·res].

    2 s       15 minutes    the 5m range; in memory, written at a clean stop, read at the start
    30 s      26 hours      1h, 6h, 24h, and the newest day of 7d
    10 min    the retention 7d beyond the 30 s tier (`--fleet-retention-days`, at least 7)

TTFT keeps a histogram per slot as well, {ms: count}: each value as it came (to 0.1 ms) while the
slot holds up to 64 distinct ones, then in buckets 2^(1/16) apart (a bucket's value within 2.2 %
of every TTFT in it). A range's p50 merges the slots and engines it covers and takes the median,
never a mean of means.

WRITES. A slot that changed is written at the next flush (every `flush_s`, 5 minutes by default,
one transaction, `INSERT OR REPLACE` of the whole slot), so a sample that arrives late, an
agent's backlog or an engine's five minutes, lands in its own slot. The primary key puts the tier
and the slot first: each flush appends at the end of the table, and the hourly prune deletes from
its start. A crash loses at most one flush interval of the 30 s and 10 min tiers and the 2 s tier;
a clean stop loses nothing. Events, alerts, rule edits and each link's last test (VIS-250) are
written when they happen.
"""

from __future__ import annotations

import json
import math
import os
import secrets
import sqlite3
import threading
import time
from array import array

TIER_KEEP = {2: 900, 30: 26 * 3600}          # the 600 s tier keeps the retention
FLUSH_S = 300.0
PRUNE_S = 3600.0
RETENTION_DAYS = 30
MIN_RETENTION_DAYS = 7
HIST_STEPS = 16                              # histogram buckets per doubling
HIST_EXACT = 64                              # distinct values a slot keeps as they came

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS series (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, sid TEXT NOT NULL,
  metric TEXT NOT NULL, UNIQUE (kind, sid, metric));
CREATE TABLE IF NOT EXISTS slots (tier INTEGER NOT NULL, k INTEGER NOT NULL,
  series INTEGER NOT NULL, sum REAL NOT NULL, n INTEGER NOT NULL, max REAL NOT NULL, hist TEXT,
  PRIMARY KEY (tier, k, series)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, at REAL NOT NULL, type TEXT NOT NULL,
  severity TEXT, node TEXT NOT NULL, deployment TEXT, text TEXT NOT NULL, detail TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS events_at ON events (at);
CREATE TABLE IF NOT EXISTS alerts (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL,
  rule TEXT NOT NULL, node TEXT NOT NULL, severity TEXT NOT NULL, title TEXT NOT NULL,
  rule_text TEXT NOT NULL, detail TEXT NOT NULL, action TEXT, since REAL NOT NULL,
  acked_at REAL, acked_by TEXT, resolved_at REAL);
CREATE INDEX IF NOT EXISTS alerts_open ON alerts (resolved_at);
CREATE TABLE IF NOT EXISTS rule_edits (id TEXT PRIMARY KEY, doc TEXT NOT NULL, at REAL NOT NULL,
  who TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS link_tests (link TEXT PRIMARY KEY, doc TEXT NOT NULL, at REAL NOT NULL);
"""


def bucket(v: float) -> float:
    """The value standing for `v` in a coarse histogram (a bucket's value maps to itself)."""
    return round(2.0 ** (round(HIST_STEPS * math.log2(v)) / HIST_STEPS), 1) if v > 0 else 0.0


def hist_add(h: dict, v: float, n: int = 1) -> None:
    v = round(v, 1)
    h[v] = h.get(v, 0) + n
    if len(h) > HIST_EXACT:
        coarse: dict = {}
        for x, c in h.items():
            b = bucket(x)
            coarse[b] = coarse.get(b, 0) + c
        h.clear()
        h.update(coarse)


def p50(hist: dict) -> float | None:
    """The median of a histogram {value: count}; with an even count the mean of the two middle
    values."""
    n = sum(hist.values())
    if not n:
        return None
    lo_i, hi_i = (n - 1) // 2, n // 2
    seen, lo = 0, None
    for v in sorted(hist):
        seen += hist[v]
        if lo is None and seen > lo_i:
            lo = v
        if seen > hi_i:
            return (lo + v) / 2
    return lo


class Ring:
    """One series in memory: three tiers of slots (2 s, 30 s, 10 min), each the sum, count and
    largest of its samples; with `hist`, a TTFT histogram per slot too. A slot that changed is
    marked for the next flush (the 30 s and 10 min tiers)."""

    def __init__(self, keep_600_s: float = RETENTION_DAYS * 86400, hist: bool = False):
        self.tiers = []
        for res, keep in ((2, TIER_KEEP[2]), (30, TIER_KEEP[30]), (600, keep_600_s)):
            n = int(keep // res) + max(30, int(keep // res) // 25)
            self.tiers.append((res, n, array("d", bytes(8 * n)), array("d", bytes(8 * n)),
                               array("d", bytes(8 * n)), array("q", [-1]) * n))
        self.hists = {res: {} for res, *_ in self.tiers} if hist else None
        self.dirty: set = set()

    def _tier(self, res: int):
        return next(t for t in self.tiers if t[0] == res)

    def add(self, t: float, v: float) -> None:
        for res, n, s, c, mx, ks in self.tiers:
            k = math.ceil(t / res)
            i = k % n
            if ks[i] > k:                    # older than the tier's reach
                continue
            if ks[i] != k:
                if self.hists is not None:
                    self.hists[res].pop(ks[i], None)
                ks[i], s[i], c[i], mx[i] = k, 0.0, 0.0, -math.inf
            s[i] += v
            c[i] += 1
            mx[i] = max(mx[i], v)
            if self.hists is not None:
                hist_add(self.hists[res].setdefault(k, {}), v)
            if res != 2:
                self.dirty.add((res, k))

    def load(self, res: int, k: int, total: float, cnt: int, top: float,
             hist: dict | None) -> None:
        _, n, s, c, mx, ks = self._tier(res)
        i = k % n
        if ks[i] > k:
            return
        ks[i], s[i], c[i], mx[i] = k, total, cnt, top
        if self.hists is not None and hist:
            self.hists[res][k] = dict(hist)

    def slot(self, res: int, k: int):
        """(sum, count, max, hist) of slot k, or None."""
        _, n, s, c, mx, ks = self._tier(res)
        i = k % n
        if ks[i] != k:
            return None
        return s[i], c[i], mx[i], (self.hists[res].get(k) if self.hists is not None else None)

    def live(self, res: int, k_lo: int):
        """The slots of a tier from k_lo on: [(k, sum, count, max, hist)]."""
        _, n, s, c, mx, ks = self._tier(res)
        return [(ks[i], s[i], c[i], mx[i],
                 self.hists[res].get(ks[i]) if self.hists is not None else None)
                for i in range(n) if ks[i] >= k_lo]

    def mean(self, res: int, k_lo: int, k_hi: int) -> float | None:
        _, n, s, c, _, ks = self._tier(res)
        tot = cnt = 0.0
        for k in range(max(k_lo, k_hi - n + 1), k_hi + 1):
            i = k % n
            if ks[i] == k:
                tot += s[i]
                cnt += c[i]
        return tot / cnt if cnt else None

    def hist(self, res: int, k_lo: int, k_hi: int, into: dict) -> dict:
        """The histogram of slots k_lo..k_hi added into `into`."""
        if self.hists is not None:
            h = self.hists[res]
            for k in range(k_lo, k_hi + 1):
                for v, c in (h.get(k) or {}).items():
                    into[v] = into.get(v, 0) + c
        return into

    def peak(self, now: float, span: float) -> float | None:
        """The largest sample of the last `span` seconds (30 s slots)."""
        _, n, _, _, mx, ks = self._tier(30)
        k_hi = math.ceil(now / 30)
        got = [mx[k % n] for k in range(k_hi - min(n, int(span // 30)) + 1, k_hi + 1)
               if ks[k % n] == k]
        return max(got) if got else None


class Store:
    """The fleet's SQLite file: rings loaded at the start, flushed in batches; the event log,
    the alerts and the rule edits written when they happen. `lock` guards the rings (the fleet
    takes it around every sample and every read)."""

    def __init__(self, path: str = ":memory:", *, retention_days: int = RETENTION_DAYS,
                 flush_s: float = FLUSH_S, clock=time.time, log=None):
        if retention_days < MIN_RETENTION_DAYS:
            raise ValueError(f"the fleet's retention is at least {MIN_RETENTION_DAYS} days")
        self.path = path if path == ":memory:" else os.path.expanduser(path)
        self.retention_s = retention_days * 86400.0
        self.flush_s = flush_s
        self.clock = clock
        self.log = log or (lambda msg: print(msg, flush=True))
        self.rings: dict[tuple, Ring] = {}
        self.lock = threading.RLock()
        self._db = threading.Lock()
        self._ids: dict[tuple, int] = {}
        self._conn: sqlite3.Connection | None = None
        self._flush_due = 0.0
        self._prune_due = 0.0
        self.last_stop: float | None = None      # the previous run's clean stop
        self.crashed = False                     # the previous run ended without one
        self.loaded_from: float | None = None    # the oldest slot read at the start

    # ------------------------------------------------------------------ the file
    def open(self) -> "Store":
        mem = self.path == ":memory:"
        if not mem:
            os.makedirs(os.path.dirname(self.path) or ".", mode=0o700, exist_ok=True)
        conn = sqlite3.connect(self.path, check_same_thread=False, timeout=5.0)
        if not mem:
            os.chmod(self.path, 0o600)
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(SCHEMA)
        self._conn = conn
        meta = dict(conn.execute("SELECT key, value FROM meta"))
        self.last_stop = float(meta["stopped_at"]) if meta.get("stopped_at") else None
        self.crashed = meta.get("running") == "1"
        with conn:
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('running', '1')")
        self._load(self.clock())
        self._flush_due = self.clock() + self.flush_s
        return self

    def _load(self, now: float) -> None:
        names = {r[0]: (r[1], r[2], r[3]) for r in
                 self._conn.execute("SELECT id, kind, sid, metric FROM series")}
        self._ids = {v: k for k, v in names.items()}
        lows = {2: math.ceil((now - TIER_KEEP[2]) / 2), 30: math.ceil((now - TIER_KEEP[30]) / 30),
                600: math.ceil((now - self.retention_s) / 600)}
        first = None
        for res, lo in lows.items():
            for k, sid, total, cnt, top, hist in self._conn.execute(
                    "SELECT k, series, sum, n, max, hist FROM slots WHERE tier = ? AND k >= ?",
                    (res, lo)):
                key = names.get(sid)
                if key is None:
                    continue
                h = {float(v): c for v, c in json.loads(hist).items()} if hist else None
                self.ring(key).load(res, k, total, cnt, top, h)
                first = (k - 1) * res if first is None else min(first, (k - 1) * res)
        self.loaded_from = first

    def ring(self, key: tuple) -> Ring:
        r = self.rings.get(key)
        if r is None:
            r = self.rings[key] = Ring(self.retention_s, hist=key[-1] == "ttft")
        return r

    def drop(self, keys: list[tuple]) -> None:
        """Series gone with their node: out of memory and out of the file."""
        with self.lock:
            for k in keys:
                self.rings.pop(k, None)
        ids = [self._ids.pop(k) for k in keys if k in self._ids]
        if ids and self._conn is not None:
            with self._db, self._conn:
                for sid in ids:
                    self._conn.execute("DELETE FROM slots WHERE series = ?", (sid,))
                    self._conn.execute("DELETE FROM series WHERE id = ?", (sid,))

    def _sid(self, key: tuple) -> int:
        sid = self._ids.get(key)
        if sid is None:
            self._conn.execute("INSERT OR IGNORE INTO series (kind, sid, metric) VALUES (?, ?, ?)",
                               key)
            sid = self._conn.execute("SELECT id FROM series WHERE kind = ? AND sid = ? AND "
                                     "metric = ?", key).fetchone()[0]
            self._ids[key] = sid
        return sid

    def due(self, now: float) -> bool:
        return now >= self._flush_due

    def flush(self, now: float | None = None, final: bool = False) -> int:
        """Write the slots that changed (with `final`, the 2 s tier's last 15 minutes too); prune
        once an hour. The number of slots written."""
        now = self.clock() if now is None else now
        self._flush_due = now + self.flush_s
        if self._conn is None:
            return 0
        rows = []
        with self.lock:
            for key, r in self.rings.items():
                for res, k in sorted(r.dirty):
                    got = r.slot(res, k)
                    if got is not None:
                        rows.append((key, res, k, got))
                r.dirty.clear()
                if final:
                    for k, total, cnt, top, h in r.live(2, math.ceil((now - TIER_KEEP[2]) / 2)):
                        rows.append((key, 2, k, (total, cnt, top, h)))
        try:
            with self._db, self._conn:
                self._conn.executemany(
                    "INSERT OR REPLACE INTO slots VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [(res, k, self._sid(key), total, int(cnt), top,
                      json.dumps(h, separators=(",", ":")) if h else None)
                     for key, res, k, (total, cnt, top, h) in sorted(rows, key=lambda x: x[1:3])])
                if now >= self._prune_due:
                    self._prune(now)
        except sqlite3.Error as exc:
            self.log(f"[fleet] the store did not write {len(rows)} slots: {exc}")
            return 0
        return len(rows)

    def _prune(self, now: float) -> None:
        self._prune_due = now + PRUNE_S
        c = self._conn
        c.execute("DELETE FROM slots WHERE tier = 2 AND k < ?", (math.ceil((now - TIER_KEEP[2])
                                                                          / 2),))
        c.execute("DELETE FROM slots WHERE tier = 30 AND k < ?",
                  (math.ceil((now - TIER_KEEP[30]) / 30),))
        c.execute("DELETE FROM slots WHERE tier = 600 AND k < ?",
                  (math.ceil((now - self.retention_s) / 600),))
        c.execute("DELETE FROM events WHERE at < ?", (now - self.retention_s,))
        c.execute("DELETE FROM alerts WHERE resolved_at IS NOT NULL AND resolved_at < ?",
                  (now - self.retention_s,))

    def close(self) -> None:
        if self._conn is None:
            return
        now = self.clock()
        self._prune_due = math.inf
        self.flush(now, final=True)
        with self._db, self._conn:
            self._conn.execute("INSERT OR REPLACE INTO meta VALUES ('stopped_at', ?)", (str(now),))
            self._conn.execute("INSERT OR REPLACE INTO meta VALUES ('running', '0')")
        with self._db:
            self._conn.close()
            self._conn = None

    def size(self) -> int:
        """The file's bytes with its WAL."""
        if self.path == ":memory:":
            return 0
        return sum(os.path.getsize(p) for p in (self.path, self.path + "-wal")
                   if os.path.exists(p))

    # ------------------------------------------------------------------ events
    def add_event(self, type_: str, node: str, text: str, detail: str = "", *,
                  severity: str | None = None, deployment: str | None = None,
                  at: float | None = None) -> dict:
        ev = {"id": "e-" + secrets.token_hex(6), "at": round(self.clock() if at is None else at,
                                                                3),
              "type": type_, "severity": severity, "node": node, "deployment": deployment,
              "text": text, "detail": detail}
        if self._conn is not None:
            with self._db, self._conn:
                self._conn.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                   tuple(ev.values()))
        return ev

    def events(self, *, since: float | None = None, before: float | None = None,
               node: str | None = None, types: list[str] | None = None,
               limit: int = 200) -> list[dict]:
        """Newest first."""
        where, args = [], []
        if since is not None:
            where.append("at > ?")
            args.append(since)
        if before is not None:
            where.append("at < ?")
            args.append(before)
        if node is not None:
            where.append("node = ?")
            args.append(node)
        if types:
            where.append(f"type IN ({', '.join('?' * len(types))})")
            args += types
        sql = ("SELECT id, at, type, severity, node, deployment, text, detail FROM events"
               + (" WHERE " + " AND ".join(where) if where else "")
               + " ORDER BY at DESC, rowid DESC LIMIT ?")
        with self._db:
            rows = self._conn.execute(sql, (*args, limit)).fetchall()
        keys = ("id", "at", "type", "severity", "node", "deployment", "text", "detail")
        return [dict(zip(keys, r)) for r in rows]

    # ------------------------------------------------------------------ alerts
    ALERT_COLS = ("seq", "id", "rule", "node", "severity", "title", "rule_text", "detail",
                  "action", "since", "acked_at", "acked_by", "resolved_at")

    def open_alerts(self) -> list[dict]:
        with self._db:
            rows = self._conn.execute(f"SELECT {', '.join(self.ALERT_COLS)} FROM alerts WHERE "
                                      f"resolved_at IS NULL ORDER BY since DESC").fetchall()
        out = []
        for r in rows:
            a = dict(zip(self.ALERT_COLS, r))
            a["action"] = json.loads(a["action"]) if a["action"] else None
            out.append(a)
        return out

    def add_alert(self, a: dict) -> dict:
        a = dict(a, acked_at=None, acked_by=None, resolved_at=None)
        with self._db, self._conn:
            cur = self._conn.execute(
                "INSERT INTO alerts (id, rule, node, severity, title, rule_text, detail, action, "
                "since) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (a["id"], a["rule"], a["node"], a["severity"], a["title"], a["rule_text"],
                 a["detail"], json.dumps(a["action"]) if a["action"] else None, a["since"]))
            a["seq"] = cur.lastrowid
        return a

    def ack_alert(self, seq: int, at: float, who: str) -> None:
        with self._db, self._conn:
            self._conn.execute("UPDATE alerts SET acked_at = ?, acked_by = ? WHERE seq = ? AND "
                               "acked_at IS NULL", (at, who, seq))

    def resolve_alert(self, seq: int, at: float) -> None:
        with self._db, self._conn:
            self._conn.execute("UPDATE alerts SET resolved_at = ? WHERE seq = ?", (at, seq))

    # ------------------------------------------------------------------ rule edits
    def rule_edits(self) -> dict:
        with self._db:
            return {r[0]: json.loads(r[1]) for r in
                    self._conn.execute("SELECT id, doc FROM rule_edits")}

    def set_rule_edit(self, rid: str, doc: dict, at: float, who: str) -> None:
        with self._db, self._conn:
            self._conn.execute("INSERT OR REPLACE INTO rule_edits VALUES (?, ?, ?, ?)",
                               (rid, json.dumps(doc, sort_keys=True), at, who))

    def delete_rule_edit(self, rid: str) -> None:
        with self._db, self._conn:
            self._conn.execute("DELETE FROM rule_edits WHERE id = ?", (rid,))

    # ------------------------------------------------------------------ link tests
    def link_tests(self) -> dict:
        """{link id: its last test} (proxy/fleetops.py)."""
        with self._db:
            return {r[0]: json.loads(r[1]) for r in
                    self._conn.execute("SELECT link, doc FROM link_tests")}

    def set_link_test(self, link: str, doc: dict, at: float) -> None:
        with self._db, self._conn:
            self._conn.execute("INSERT OR REPLACE INTO link_tests VALUES (?, ?, ?)",
                               (link, json.dumps(doc, sort_keys=True), at))
