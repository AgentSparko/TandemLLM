"""What a stage keeps of its own half of a model's state, under the keys the coordinator names
(ENG-423, engine/methods/stages.py): the front stage's side of the caches.

The tail's caches (engine/cache.py) keep the tail's sections and cache rows as tensors and put them
back themselves. The front is told only a key: "keep what you hold now", "be the state of this key
again". This store is how it answers, over the same interface the caches read a model through
(`sections()`: the recurrent state by name; `views()`: the position-indexed buffers and their row
axis). It names no model.

A kept state is its sections, copied when it is kept, and its rows. The rows are kept as segments:
a state's own segment is the rows written since the state it grew from (its parent), so a
conversation of many kept states holds each row once. A segment stays where it is, in the live
buffers, until the running sequence is about to write over it; then it is copied out first
(`write`). A restore walks the state's chain of segments from row 0: each is in place, or is
copied back, or the restore is a miss and nothing changed. A miss costs the coordinator a rebuild
of the front, never an answer.

`budget_bytes` bounds the copies (sections and copied-out rows). The least recently used state gives
up its sections first: it can no longer be restored to, but while its segment is in place it stays
a link of the chain, at no cost, so a long conversation's later states still restore. A segment
that was copied out goes next, and then every state above it is a miss. With a directory, a state the coordinator asks
to `keep` is also written there with every segment of its chain, one file a state, named by the
key: token ids are not in it, only the stage's own tensors. A file is written to a temporary name,
synced and renamed, carries a checksum and its layout, and is read back only into buffers of that
layout. A read needs every file of the chain, or it is a miss.
"""

from __future__ import annotations

import json
import os
import struct
import zlib
from collections import OrderedDict

import torch

MAGIC = b"TLSEG001"
_HDR = struct.Struct("<8sI")


class _Kept:
    __slots__ = ("key", "parent", "start", "length", "sections", "rows", "on_disk", "disk_sections", "nbytes")

    def __init__(self, key, parent, start, length, sections, rows=None, on_disk=False):
        self.key, self.parent, self.start, self.length = key, parent, start, length
        #: `sections` None: a link of a chain (its rows count, it cannot be restored to)
        self.sections, self.rows, self.on_disk = sections, rows, on_disk
        self.disk_sections = on_disk and sections is not None      # its file holds its sections too
        self.nbytes = 0


def _nb(ts) -> int:
    return sum(t.numel() * t.element_size() for t in ts)


def _raw(t: torch.Tensor) -> bytes:
    t = t.detach().contiguous().cpu()
    return t.reshape(-1).view(torch.uint8).numpy().tobytes() if t.numel() else b""


class SideStore:
    """The kept states of one sequence slot of a stage. `state`: the slot's cache state
    (`sections()`, `views()`). The caller settles the engine before `snapshot`, resumes it after a
    `restore` that hit, and calls `write` before any pass writes rows."""

    def __init__(self, state, budget_bytes: int, root: str | None = None, disk_bytes: int = 0,
                 name: str = "", log=None):
        self.state = state
        self.budget = int(budget_bytes)
        self.entries: OrderedDict = OrderedDict()
        self.place: dict = {}            # start row -> (end row, key): the segments in the live buffers
        self.top = 0                     # the highest row of a segment in place
        self.base = (None, 0)            # the kept state the running sequence grew from: (key, length)
        self.bytes = 0
        self.dir = os.path.join(root, name) if root else None
        self.disk_budget = int(disk_bytes)
        self.log = log
        self._pinned: set = set()
        self.stats = {"kept": 0, "hits": 0, "misses": 0, "copied_out": 0, "copied_back": 0, "evicted": 0,
                      "files_written": 0, "files_read": 0, "files_bad": 0}
        if self.dir:
            os.makedirs(self.dir, mode=0o700, exist_ok=True)
            for f in os.listdir(self.dir):
                if f.endswith(".tmp"):
                    os.unlink(os.path.join(self.dir, f))

    # ------------------------------------------------------------------ the running sequence
    def reset(self) -> None:
        """A new sequence from row 0 (what is kept stays)."""
        self.base = (None, 0)

    def _views(self) -> list:
        return [(t, ax) for _, t, ax in self.state.views()]

    def write(self, lo: int, hi: int) -> None:
        """The running sequence is about to write rows [lo, hi): a kept segment there is copied
        out first."""
        if lo >= self.top or hi <= lo:
            return
        for start in sorted(s for s, (end, _) in self.place.items() if s < hi and end > lo):
            end, key = self.place.pop(start)
            e = self.entries.get(key)
            if e is not None and e.rows is None:
                e.rows = tuple(t.narrow(ax, start, end - start).clone() for t, ax in self._views())
                grew = _nb(e.rows)
                e.nbytes += grew
                self.bytes += grew
                self.stats["copied_out"] += 1
        self.top = max((end for end, _ in self.place.values()), default=0)
        if not self._pinned:
            self._evict()

    # ------------------------------------------------------------------ keep, restore, drop
    def snapshot(self, key: bytes, length: int, keep: bool = False) -> None:
        key = bytes(key)
        e = self.entries.get(key)
        if e is not None and e.sections is None:
            # a link of a chain whose sections went: it is this state again, with its sections
            e.sections = tuple(t.clone() for t in self.state.sections().values())
            e.nbytes += _nb(e.sections)
            self.bytes += _nb(e.sections)
        if e is None:
            parent, start = self.base
            if length < start:
                raise ValueError(f"a state of {length} rows cannot grow from one of {start}")
            if parent is not None and parent not in self.entries:
                parent, start = None, 0          # its parent went: this state's segment is every row
                self.write(0, length)            # (what was in place below it is copied out, then it is)
            e = _Kept(key, parent, start, length, tuple(t.clone() for t in self.state.sections().values()))
            e.nbytes = _nb(e.sections)
            self.entries[key] = e
            self.bytes += e.nbytes
            if length > start:
                self.place[start] = (length, key)
                self.top = max(self.top, length)
            self.stats["kept"] += 1
        else:
            self.entries.move_to_end(key)
        self.base = (key, length)
        if keep and self.dir:
            self._persist(key)
        self._pinned = {key}
        try:
            self._evict()
        finally:
            self._pinned = set()

    def _chain(self, key: bytes) -> list | None:
        """The kept states from row 0 to `key`, or None when one is missing."""
        out, k = [], key
        while k is not None:
            e = self.entries.get(k) or self._load(k)
            if e is None or len(out) > 1 << 20:
                return None
            out.append(e)
            k = e.parent
        out.reverse()
        at = 0
        for e in out:
            if e.start != at or e.length < at:
                return None
            at = e.length
        return out

    def _in_place(self, e: _Kept) -> bool:
        return e.length == e.start or self.place.get(e.start) == (e.length, e.key)

    def restore(self, key: bytes, length: int) -> bool:
        """The live sections and rows [0, length) are the state kept under `key` again: True. False:
        it is not kept whole (a segment or a file is gone, another length, another layout), and
        nothing changed."""
        key = bytes(key)
        e = self.entries.get(key)
        if e is not None and e.sections is None and e.on_disk and e.disk_sections:
            self._forget(key)                    # its sections went from memory: its file has them
        chain = self._chain(key)
        live = list(self.state.sections().values())
        if (chain is None or chain[-1].length != length or chain[-1].sections is None
                or [(t.shape, t.dtype) for t in chain[-1].sections] != [(t.shape, t.dtype) for t in live]
                or any(e.rows is None and not self._in_place(e) for e in chain)):
            self.stats["misses"] += 1
            return False
        views = self._views()
        self._pinned = {e.key for e in chain}
        try:
            for e in chain:
                self.entries.move_to_end(e.key)
                if e.rows is None:
                    continue
                self.write(e.start, e.length)
                for (t, ax), x in zip(views, e.rows):
                    t.narrow(ax, e.start, e.length - e.start).copy_(x)
                freed = _nb(e.rows)
                e.rows = None
                e.nbytes -= freed
                self.bytes -= freed
                self.place[e.start] = (e.length, e.key)
                self.top = max(self.top, e.length)
                self.stats["copied_back"] += 1
            for t, x in zip(live, chain[-1].sections):
                t.copy_(x)
            self.base = (key, length)
            self.stats["hits"] += 1
            self._evict()
        finally:
            self._pinned = set()
        return True

    def drop(self, keys) -> None:
        for k in keys:
            self._forget(bytes(k))

    def _forget(self, key: bytes) -> None:
        e = self.entries.pop(key, None)
        if e is None:
            return
        self.bytes -= e.nbytes
        if self.place.get(e.start) == (e.length, key):
            del self.place[e.start]
            self.top = max((end for end, _ in self.place.values()), default=0)

    def _evict(self) -> None:
        """Under the budget again, the least recently used state first: its sections go (it stays a
        link of its chain while its segment is in place), then its rows when they were copied out."""
        for key in list(self.entries):
            if self.bytes <= self.budget:
                return
            e = self.entries[key]
            if key in self._pinned:
                continue
            if e.sections is not None:
                freed = _nb(e.sections)
                e.sections = None
                e.nbytes -= freed
                self.bytes -= freed
                self.stats["evicted"] += 1
            if (e.rows is not None and self.bytes > self.budget) or (e.rows is None and not self._in_place(e)):
                self._forget(key)

    def report(self) -> dict:
        out = {"states": len(self.entries), "links": sum(1 for e in list(self.entries.values()) if e.sections is None),
               "bytes": self.bytes, "budget": self.budget, "in_place": len(self.place), **self.stats}
        if self.dir:
            out["files"] = sum(1 for f in os.listdir(self.dir) if f.endswith(".seg"))
        return out

    # ------------------------------------------------------------------ the directory
    def _path(self, key: bytes) -> str:
        return os.path.join(self.dir, key.hex() + ".seg")

    def _persist(self, key: bytes) -> None:
        """`key` and every state of its chain not written yet, each to its own file."""
        chain = self._chain(key)
        if chain is None:
            return
        views = self._views()
        names = list(self.state.sections())
        for e in chain:
            if e.on_disk and (e.sections is None or e.disk_sections):
                continue
            if e.rows is not None:
                rows = e.rows
            elif self._in_place(e):
                rows = tuple(t.narrow(ax, e.start, e.length - e.start) for t, ax in views)
            else:
                return
            secs = e.sections or ()              # a link of the chain: its rows alone
            parts = [_raw(t) for t in secs] + [_raw(t) for t in rows]
            head = json.dumps({
                "key": e.key.hex(), "parent": e.parent.hex() if e.parent else None, "start": e.start,
                "length": e.length, "crc": zlib.crc32(b"".join(parts)),
                "sections": [[n, str(t.dtype), list(t.shape)] for n, t in zip(names, secs)],
                "rows": [[str(t.dtype), list(t.shape), ax] for t, (_, ax) in zip(rows, views)]}).encode()
            tmp = self._path(e.key) + ".tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, _HDR.pack(MAGIC, len(head)) + head)
                for p in parts:
                    view = memoryview(p)
                    while view:
                        view = view[os.write(fd, view):]
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(tmp, self._path(e.key))
            e.on_disk, e.disk_sections = True, bool(secs)
            self.stats["files_written"] += 1
        self._sweep(keep={e.key.hex() for e in chain})

    def _sweep(self, keep: set) -> None:
        """The directory under its budget: the files read or written longest ago go first."""
        if not self.disk_budget:
            return
        files = []
        for f in os.listdir(self.dir):
            if f.endswith(".seg"):
                st = os.stat(os.path.join(self.dir, f))
                files.append((st.st_mtime, st.st_size, f))
        total = sum(size for _, size, _ in files)
        for _, size, f in sorted(files):
            if total <= self.disk_budget:
                break
            if f[:-4] in keep:
                continue
            os.unlink(os.path.join(self.dir, f))
            total -= size
            e = self.entries.get(bytes.fromhex(f[:-4]))
            if e is not None:
                e.on_disk = False

    def _load(self, key: bytes) -> _Kept | None:
        """A state's file read into the store (its rows as a copy out of place), or None."""
        if not self.dir or not os.path.exists(self._path(key)):
            return None
        path = self._path(key)
        live = self.state.sections()
        views = self._views()
        try:
            with open(path, "rb") as f:
                magic, n = _HDR.unpack(f.read(_HDR.size))
                head = json.loads(f.read(n))
                body = f.read()
            if magic != MAGIC or head["key"] != key.hex() or zlib.crc32(body) != head["crc"]:
                raise ValueError("checksum")
            rows_n = head["length"] - head["start"]
            link = not head["sections"]              # a link of a chain: rows alone
            if not link and ([[nm, dt, sh] for nm, dt, sh in head["sections"]]
                             != [[nm, str(t.dtype), list(t.shape)] for nm, t in live.items()]):
                raise KeyError("layout")
            want = []
            for t, ax in views:
                shape = list(t.shape)
                shape[ax] = rows_n
                want.append([str(t.dtype), shape, ax])
            if head["rows"] != want:
                raise KeyError("layout")
            at, got = 0, []
            for t, shape in ([] if link else [(t, list(t.shape)) for t in live.values()]) + \
                    [(t, w[1]) for (t, _), w in zip(views, want)]:
                n = t.element_size()
                for d in shape:
                    n *= d
                buf = bytearray(body[at:at + n])
                at += n
                x = (torch.frombuffer(buf, dtype=torch.uint8).view(t.dtype).reshape(shape) if n
                     else torch.empty(shape, dtype=t.dtype))
                got.append(x.to(t.device).clone() if t.device.type != "cpu" else x.clone())
            if at != len(body):
                raise ValueError("size")
        except KeyError:
            return None                          # another layout's file: left for the stage that wrote it
        except Exception:                        # noqa: BLE001  (a file that cannot be read is no state)
            self.stats["files_bad"] += 1
            try:
                os.unlink(path)
            except OSError:
                pass
            return None
        ns = 0 if link else len(live)
        e = _Kept(key, bytes.fromhex(head["parent"]) if head["parent"] else None, int(head["start"]),
                  int(head["length"]), None if link else tuple(got[:ns]), tuple(got[ns:]), on_disk=True)
        e.nbytes = _nb(e.sections or ()) + _nb(e.rows)
        self.entries[key] = e
        self.bytes += e.nbytes
        self.stats["files_read"] += 1
        os.utime(path)
        return e
