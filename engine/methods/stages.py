"""One model as two stage processes (ENG-307): the protocol between them, written once.

The pass is cut between two layers. The front stage holds the embedding and layers [0, k), the tail
stage layers [k, layers), the head and the sampler. The tail is the coordinator, the engine a client
talks to; the front is a follower that acts only on the headers it is sent. A round:

    tail  -> front   the step header: the block's tokens and parents, the sequence length it starts
                     at, and the commit path of the block before it
    front -> tail    the carry: the block's rows as they leave layer k - 1, one typed frame

Nothing else crosses: no logits, no state. Each side keeps the cache rows and the recurrent state of
its own layers, and its own pending block until the commit path arrives with the next header.

A stage reads its model only through a `StageAdapter`, which the model's family provides
(`engine/models/<family>/stages.py`, found by `family_stages`). This module names no model.

Failure: every receive has a deadline (`CHUNK_S` for a prompt chunk, a chain of several rows;
`STEP_S` for every other request: a verify block, a block of one row, a reset, a sync, a probe). A miss
takes the link down; the request in flight ends with `StageDown` (code `stage_down`) and so does every
request after it, until a new link is attached. A new link has a new epoch, and both sides start it
from an empty state: nothing computed under an older epoch is used. A header whose length is not the
follower's is answered `desync`; the coordinator then starts both sides over and replays the
sequence's tokens before it repeats the step. A stale state costs a prefill, never an answer.
A front whose own pass fails (not a block it refuses) answers `stage` with the reason and ends the
link: the request ends with that reason, and the next link starts both sides empty.

State kept beyond the running sequence (ENG-423). Each side names the state it holds by a key: a
digest over the blocks that wrote it, in order (`chain_key`), computed on each side from what that
side ran. An adapter that is `exact` sends the key with every header, so a front whose rows are not
the tail's is found by content, not by length alone. Three more requests use the key:

    snapshot   the front keeps its committed state under its own key (the header's must equal it)
    restore    the front goes back to the state it keeps under a key, or answers a miss
    drop       the front forgets keys

The tail's caches hold its half of a state with the key inside it; a restore of that half sends
`restore` for the same key, so a section is never joined to another state's. A miss, a new epoch or
a desync is rebuilt on the front alone: the tail sends the blocks of its log again, each as the
kind of block it was (`quiet`: no carry comes back), from the nearest key the front still holds.
The tail's own rows are never run again, so the tokens after a rebuild are the ones before it.

A model with several slots keeps one sequence a slot on both sides (`StageAdapter.slot`); the
header names the slot. The blocks of one joint round cross as one request (`round`): every slot's
header in one frame, every slot's rows in one carry.

The step header, little-endian, then the arrays:

    op u8 | flags u8 | slot u16 | position class u16 | graph u16 | length u32 | rows u32 | commit u16
    tokens i32[rows] | parents i16[rows] (absent for a chain) | commit path i16[commit]
    key [16] (with the key flag) | keys [16 each] (a drop)

A round is the same head with the count of headers, then each header behind its u32 length.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import struct
import sys
import threading
from array import array
from collections import OrderedDict

from engine.methods import link as L

STEP_S = 5.0            # a verify block's carry
CHUNK_S = 60.0          # a prompt chunk's carry
IDLE_S = 5.0            # a follower without a frame for this long takes the link for dead
REPLAY_ROWS = 1024      # a replay's chunk

REBUILD_TRIES = 8       # the kept keys a rebuild asks the front for before it starts from row 0
MARKS = 8192            # the keys a coordinator keeps the log of

OP_STEP, OP_SYNC, OP_RESET, OP_PROBE = 1, 2, 3, 4
OP_SNAPSHOT, OP_RESTORE, OP_DROP, OP_ROUND = 5, 6, 7, 8
OPS = (OP_STEP, OP_SYNC, OP_RESET, OP_PROBE, OP_SNAPSHOT, OP_RESTORE, OP_DROP)
F_CHAIN, F_COMMIT, F_QUIET, F_KEY, F_KEEP = 1, 2, 4, 8, 16
KEY_BYTES = 16
_LEN = struct.Struct("<I")
HEADS = ("all", "last", "none")
_HEAD = struct.Struct("<BBHHHIIH")
MAX_TREE = 32767        # parents and commit paths are int16


class StageError(Exception):
    """A stage request that failed; `code` names why."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


class StageDown(StageError):
    """The other stage is not reachable: the link missed a deadline, closed or was never attached."""

    def __init__(self, message: str = ""):
        super().__init__("stage_down", message or "stage_down")


class Block:
    """The rows of one round: `tokens` in DFS pre-order with `parents` (-1 for the first row), or a
    chain (`parents` None: a prompt chunk, kept whole). `head` is the tail's: logits of "all" rows,
    the "last" row, or "none". `graph` is the family's own tag of how the block runs (0: its
    default); it crosses in the header."""

    __slots__ = ("tokens", "parents", "slot", "pos_class", "graph", "head")

    def __init__(self, tokens, parents=None, slot=0, pos_class=0, graph=0, head="all"):
        self.tokens = [int(t) for t in tokens]
        self.parents = None if parents is None else [int(p) for p in parents]
        self.slot, self.pos_class, self.graph, self.head = slot, pos_class, graph, head
        if not self.tokens:
            raise ValueError("a block has at least one row")
        if self.parents is not None:
            if len(self.parents) != len(self.tokens) or len(self.tokens) > MAX_TREE:
                raise ValueError(f"{len(self.tokens)} tokens with {len(self.parents)} parents")
            if self.parents[0] != -1 or any(not 0 <= p < i for i, p in enumerate(self.parents) if i):
                raise ValueError("parents are not a tree in DFS pre-order")
        if head not in HEADS:
            raise ValueError(f"head {head!r} is none of {HEADS}")

    @property
    def chain(self) -> bool:
        return self.parents is None

    @property
    def rows(self) -> int:
        return len(self.tokens)


def _ints(code: str, values) -> bytes:
    a = array(code, values)
    if sys.byteorder == "big":
        a.byteswap()
    return a.tobytes()


def _read_ints(code: str, raw: bytes) -> list:
    a = array(code)
    a.frombytes(raw)
    if sys.byteorder == "big":
        a.byteswap()
    return a.tolist()


def pack_step(op: int, length: int, block: Block | None = None, commit: list | None = None, *,
              key: bytes | None = None, quiet: bool = False, keep: bool = False, slot: int | None = None,
              keys=()) -> bytes:
    flags = ((F_COMMIT if commit is not None else 0) | (F_CHAIN if block is not None and block.chain else 0)
             | (F_QUIET if quiet else 0) | (F_KEY if key is not None else 0) | (F_KEEP if keep else 0))
    n = block.rows if block is not None else 0
    path = commit or []
    if slot is None:
        slot = block.slot if block else 0
    out = _HEAD.pack(op, flags, slot, block.pos_class if block else 0,
                     block.graph if block else 0, length, n, len(path))
    if block is not None:
        out += _ints("i", block.tokens)
        if not block.chain:
            out += _ints("h", block.parents)
    out += _ints("h", path)
    if key is not None:
        if len(key) != KEY_BYTES:
            raise ValueError("a key is 16 bytes")
        out += bytes(key)
    for k in keys:
        if len(k) != KEY_BYTES:
            raise ValueError("a key is 16 bytes")
        out += bytes(k)
    return out


class Step:
    """A parsed step header."""

    __slots__ = ("op", "flags", "slot", "length", "block", "commit", "key", "keys", "hit")

    @property
    def quiet(self) -> bool:
        return bool(self.flags & F_QUIET)

    @property
    def keep(self) -> bool:
        return bool(self.flags & F_KEEP)


def unpack(raw: bytes) -> Step:
    """A step header, or ValueError."""
    raw = bytes(raw)
    if len(raw) < _HEAD.size:
        raise ValueError("a step header too short")
    op, flags, slot, pos_class, graph, length, n, nc = _HEAD.unpack_from(raw)
    chain = bool(flags & F_CHAIN)
    want = (_HEAD.size + 4 * n + (0 if chain or not n else 2 * n) + 2 * nc
            + (KEY_BYTES if flags & F_KEY else 0))
    tail = len(raw) - want
    if (op not in OPS or (op == OP_STEP) != bool(n) or tail < 0
            or (tail and (op != OP_DROP or tail % KEY_BYTES))):
        raise ValueError("a step header that does not parse")
    at = _HEAD.size
    block = None
    if n:
        tokens = _read_ints("i", raw[at:at + 4 * n])
        at += 4 * n
        parents = None
        if not chain:
            parents = _read_ints("h", raw[at:at + 2 * n])
            at += 2 * n
        block = Block(tokens, parents, slot, pos_class, graph)
    path = _read_ints("h", raw[at:at + 2 * nc])
    at += 2 * nc
    if not flags & F_COMMIT:
        if nc:
            raise ValueError("a commit path without its flag")
        path = None
    st = Step()
    st.op, st.flags, st.slot, st.length, st.block, st.commit = op, flags, slot, length, block, path
    st.key = None
    if flags & F_KEY:
        st.key = raw[at:at + KEY_BYTES]
        at += KEY_BYTES
    st.keys = [raw[i:i + KEY_BYTES] for i in range(at, len(raw), KEY_BYTES)]
    if op in (OP_SNAPSHOT, OP_RESTORE) and st.key is None:
        raise ValueError("a snapshot or a restore names its key")
    return st


def unpack_step(raw: bytes) -> tuple:
    """(op, length, block or None, commit path or None) of a step header, or ValueError."""
    st = unpack(raw)
    return st.op, st.length, st.block, st.commit


def pack_round(headers: list) -> bytes:
    """The headers of one joint round as one request."""
    out = _HEAD.pack(OP_ROUND, 0, 0, 0, 0, 0, len(headers), 0)
    for h in headers:
        out += _LEN.pack(len(h)) + h
    return out


def unpack_round(raw: bytes) -> list:
    raw = bytes(raw)
    if len(raw) < _HEAD.size or raw[0] != OP_ROUND:
        raise ValueError("no round")
    n = _HEAD.unpack_from(raw)[6]
    at, out = _HEAD.size, []
    for _ in range(n):
        if at + _LEN.size > len(raw):
            raise ValueError("a round that does not parse")
        size = _LEN.unpack_from(raw, at)[0]
        at += _LEN.size
        if at + size > len(raw):
            raise ValueError("a round that does not parse")
        out.append(raw[at:at + size])
        at += size
    if at != len(raw) or not out:
        raise ValueError("a round that does not parse")
    return out


def root_key(seed: bytes = b"") -> bytes:
    """The key of an empty sequence. `seed`: what both stages agree on (the code and the weights)."""
    return hashlib.sha256(b"stage-log|" + bytes(seed)).digest()[:KEY_BYTES]


def chain_key(key: bytes, block: Block, path: list | None = None) -> bytes:
    """The key after `block` on a state of `key`: a chain block kept whole, or the rows `path` of a
    tree. It names what the rows depend on: every block before, its tokens, its shape and its kind."""
    h = hashlib.sha256()
    h.update(key)
    if path is None:
        h.update(b"c" + struct.pack("<HI", block.graph, block.rows))
        h.update(_ints("i", block.tokens))
    else:
        h.update(b"t" + struct.pack("<HIH", block.graph, block.rows, len(path)))
        h.update(_ints("i", block.tokens))
        h.update(_ints("h", block.parents))
        h.update(_ints("h", path))
    return h.digest()[:KEY_BYTES]


class StageAdapter:
    """What a stage reads of its model: the family's own code behind one interface.

    A family's `stages.py` subclasses it for the layers [lo, hi) of `layers`; lo == 0 is the front
    stage, hi == layers the tail, both the whole model in one process (the reference a split is
    held to). The stage keeps the state of its own layers only.

      family       the family's registry name; the handshake compares it, no code branches on it
      cuts         the boundaries k this family's stage code supports, of 1..layers-1
      carry_dtype, carry_dims   one row's carry: a link dtype name and the dims after the row axis
      length       rows committed so far
      position_class(length, rows)   the class the tail names in the header (0: none)
      reset()      a new sequence: every row and section dropped
      rows(block, carry)   run the block through the stage's layers. Front (carry None): from the
                   tokens, returns the carry. Tail: from the carry, returns the logits (fp32) the
                   block's head asks for. A chain is kept at once; a tree stays pending
      commit(path) keep the rows `path` of the pending block (row 0 first; [] keeps none);
                   ValueError when there is no such path
      state()      name -> tensor: the committed sections and cache rows [0, length), named with
                   their layer index, so the two stages' names are disjoint and their union is the
                   whole model's
      encode(t), decode(dtype, dims, buf)   a tensor as a typed buffer and back; a stage with
                   its own host staging overrides both
      staging(nbytes)   a writable host buffer of exactly `nbytes` the link reads a carry into
                   (`decode` is then given that buffer), or None for a buffer of the link's own

    State beyond the running sequence (ENG-423), for a family that keeps it:

      exact        True: a block run again on this stage gives the rows it gave, so the coordinator
                   sends the state's key with every header and rebuilds a front that lost its state
                   from its log of blocks (False: the sequence's tokens again as prompt chunks, on
                   both stages)
      seed         what both stages agree on, in every key (the server's: code, weights, cut)
      snapshot(key, keep)   the front keeps its committed state under `key` (`keep`: also in its
                   own directory); ValueError when it keeps none
      restore(key, length)  the front's state is the one kept under `key`, of `length` rows: True,
                   or False and nothing changed
      drop(keys)   forget them
      slot(i)      the adapter of slot i over the same weights (i > 0); ValueError when the stage
                   has no such slot
      round(jobs)  [(adapter, block, carry or None)] of one joint round's slots: each one's `rows`
      join(carries), split(carry, rows)   a round's carries as one frame, and back
    """

    family = ""
    layers = 0
    lo = 0
    hi = 0
    cuts: tuple = ()
    carry_dtype = "bfloat16"
    carry_dims: tuple = ()
    exact = False
    seed = b""

    @property
    def role(self) -> str:
        if self.lo == 0 and self.hi == self.layers:
            return "whole"
        return "front" if self.lo == 0 else "tail"

    @property
    def cut(self) -> int:
        return self.hi if self.lo == 0 else self.lo

    @property
    def carry_bytes_per_row(self) -> int:
        n = L.DTYPES[L.DTYPE_CODES[self.carry_dtype]][1]
        for d in self.carry_dims:
            n *= d
        return n

    @property
    def length(self) -> int:
        raise NotImplementedError

    def position_class(self, length: int, rows: int) -> int:
        return 0

    def reset(self) -> None:
        raise NotImplementedError

    def rows(self, block: Block, carry):
        raise NotImplementedError

    def commit(self, path: list) -> None:
        raise NotImplementedError

    def state(self) -> dict:
        raise NotImplementedError

    def staging(self, nbytes: int):
        return None

    def snapshot(self, key: bytes, keep: bool = False) -> None:
        raise ValueError("this stage keeps no snapshots")

    def restore(self, key: bytes, length: int) -> bool:
        return False

    def drop(self, keys) -> None:
        return None

    def slot(self, i: int) -> "StageAdapter":
        raise ValueError("this stage serves one slot")

    def round(self, jobs: list) -> list:
        return [ad.rows(block, carry) for ad, block, carry in jobs]

    def join(self, carries: list):
        if len(carries) == 1:
            return carries[0]
        if isinstance(carries[0], list):
            return [row for c in carries for row in c]
        import torch
        return torch.cat(list(carries))

    def split(self, carry, rows: list) -> list:
        out, at = [], 0
        for n in rows:
            out.append(carry[at:at + n])
            at += n
        return out

    def encode(self, t) -> tuple:
        """(dtype code, dims, buffer) of a tensor."""
        import torch
        t = t.detach().contiguous().cpu()
        name = str(t.dtype).replace("torch.", "")
        return L.DTYPE_CODES[name], tuple(t.shape), t.reshape(-1).view(torch.uint8).numpy()

    def decode(self, dtype: int, dims: tuple, buf):
        import torch
        kind = getattr(torch, L.DTYPES[dtype][0])
        if not len(buf):
            return torch.empty(dims, dtype=kind)
        return torch.frombuffer(buf, dtype=torch.uint8).view(kind).reshape(dims)

    def digest(self) -> str:
        """The committed state by content: names, types, shapes and bytes of `state()`."""
        h = hashlib.sha256()
        for name, t in sorted(self.state().items()):
            dtype, dims, buf = self.encode(t)
            h.update(f"{name}:{dtype}:{list(dims)};".encode())
            h.update(bytes(buf))
        return h.hexdigest()[:32]


def family_stages(arch):
    """The stage module of a registered family (an `engine.models` Arch): its package's `stages`,
    with `stage_adapter(model, lo, hi) -> StageAdapter` and, for a family a server can start as
    a stage, `open_stage(path, lo, hi, max_len, device, log) -> StageAdapter` (the stage's own
    weights read from the checkpoint at `path`)."""
    return importlib.import_module("engine." + arch.package.replace("/", ".") + ".stages")


def stage_identity(block: dict, adapter: StageAdapter) -> dict:
    """The engine's identity block with this stage in it: the handshake's payload."""
    if adapter.role == "whole":
        raise ValueError("a stage that holds every layer has no peer")
    if adapter.cut not in adapter.cuts:
        raise ValueError(f"the family does not support a cut at {adapter.cut} (it supports {list(adapter.cuts)})")
    if block.get("family") != adapter.family or block.get("layers") != adapter.layers:
        raise ValueError("the identity block is another model's than the stage's")
    return {**block, "stage": {"role": adapter.role, "lo": adapter.lo, "hi": adapter.hi}}


def open_link(key: bytes, ident: dict, listener=None, peer: str | None = None, bind: str | None = None,
              last_epoch: int = 0, accept_deadline: float | None = None, max_frame: int = L.MAX_FRAME):
    """A link with its handshake done: accepted on `listener`, or dialled to `peer`. Either stage
    may be the one that listens."""
    if listener is not None:
        link = listener.accept(accept_deadline)
    else:
        link = L.dial(peer, bind, max_frame=max_frame)
    L.handshake(link, key, ident, dialer=listener is None, last_epoch=last_epoch)
    return link


def _error(link, seq: int, code: str, **info) -> None:
    link.send(L.ERROR, json.dumps({"code": code, **info}).encode(), seq=seq)


class _Seat:
    """One slot of the front: its adapter, the key of its committed state, its pending tree."""

    __slots__ = ("adapter", "key", "tree")

    def __init__(self, adapter, key: bytes):
        self.adapter, self.key, self.tree = adapter, key, None


class _Refused(Exception):
    """A header the front answers with an error frame."""

    def __init__(self, code: str, **info):
        super().__init__(code)
        self.code, self.info = code, info


class Follower:
    """The front stage's loop: block on the link, act on a header, answer with the carry."""

    def __init__(self, adapter: StageAdapter, idle_deadline: float = IDLE_S):
        if adapter.role != "front":
            raise ValueError("the follower is the front stage")
        self.adapter = adapter
        self.idle_deadline = idle_deadline
        self.steps = 0
        self.seats: dict = {}
        self.stats = {"snapshots": 0, "restores": 0, "restore_misses": 0, "quiet_steps": 0, "rounds": 0}

    def _root(self) -> bytes:
        return root_key(getattr(self.adapter, "seed", b""))

    def _seat(self, slot: int) -> _Seat:
        seat = self.seats.get(slot)
        if seat is None:
            try:
                ad = self.adapter if slot == 0 else self.adapter.slot(slot)
            except ValueError as e:
                raise _Refused("block", message=str(e)[:200]) from None
            seat = self.seats[slot] = _Seat(ad, self._root())
        return seat

    def _empty(self) -> None:
        """Every slot's running sequence dropped (what a stage keeps under a key stays)."""
        self.seats.setdefault(0, _Seat(self.adapter, self._root()))
        for seat in self.seats.values():
            seat.adapter.reset()
            seat.key, seat.tree = self._root(), None

    def serve(self, link) -> str:
        """Serve one link (its handshake done) until it ends; returns why. The state starts empty
        and is dropped at the end: a link's rows never outlive its epoch."""
        self._empty()
        try:
            while True:
                fr = link.recv(self.idle_deadline)
                if fr.kind == L.PING:
                    link.send(L.PONG, seq=fr.seq)
                elif fr.kind in (L.BYE, L.ERROR):
                    link.down("peer closed")
                    return "bye" if fr.kind == L.BYE else "peer error"
                elif fr.kind != L.STEP:
                    _error(link, fr.seq, "frame")            # only a header moves this stage
                elif fr.epoch != link.epoch:
                    _error(link, fr.seq, "epoch", epoch=link.epoch)
                else:
                    try:
                        self._step(link, fr)
                    except L.LinkError:
                        raise
                    except Exception as e:                  # noqa: BLE001
                        # the stage's own failure (not a refused block): the tail is told why, and the
                        # epoch ends here, so nothing of a half-run block is used
                        why = f"{type(e).__name__}: {e}"[:300]
                        try:
                            _error(link, fr.seq, "stage", message=why)
                        except L.LinkError:
                            pass
                        link.down("stage error")
                        return f"stage error ({why})"
        except L.LinkError as e:
            return e.code
        finally:
            self._empty()

    def run(self, connect, log=None, links: int | None = None) -> None:
        """Serve link after link: `connect(last_epoch)` returns the next one, its handshake done
        (`open_link`), or raises a LinkError to be asked again. `links`: stop after that many."""
        epoch = 0
        while links is None or links > 0:
            try:
                link = connect(epoch)
            except L.LinkError as e:
                if log:
                    log(f"link: no peer ({e.code})")
                continue
            epoch = link.epoch
            if log:
                log(link.describe())
            why = self.serve(link)
            if log:
                log(f"link: epoch {epoch} ended ({why})")
            if links is not None:
                links -= 1

    def _step(self, link, fr) -> None:
        raw = bytes(fr.payload)
        try:
            if raw[:1] == bytes((OP_ROUND,)):
                return self._round(link, fr, [unpack(h) for h in unpack_round(raw)])
            st = unpack(raw)
        except ValueError:
            return _error(link, fr.seq, "header")
        try:
            seat = self._seat(st.slot)
            if self._settle(seat, st):
                return link.send(L.REPLY, json.dumps(self._answer(seat, st)).encode(), seq=fr.seq)
            carry = self._rows(seat, st)
        except _Refused as e:
            return _error(link, fr.seq, e.code, **e.info)
        if st.quiet:
            self.stats["quiet_steps"] += 1
            return link.send(L.REPLY, json.dumps({"length": seat.adapter.length, "key": seat.key.hex()}).encode(),
                             seq=fr.seq)
        dtype, dims, buf = seat.adapter.encode(carry)
        self.steps += 1
        link.send(L.CARRY, buf, dtype=dtype, dims=dims, seq=fr.seq, deadline=CHUNK_S)

    def _settle(self, seat: _Seat, st: Step) -> bool:
        """What a header asks before its block: a reset, a restore, a drop; the commit of the block
        before; the length and the key it names. True: the header has no block to run."""
        ad = seat.adapter
        if st.op == OP_RESET:
            ad.reset()
            seat.key, seat.tree = self._root(), None
            return True
        if st.op == OP_DROP:
            ad.drop(st.keys)
            return True
        if st.op == OP_RESTORE:
            try:
                hit = bool(ad.restore(st.key, st.length))
            except ValueError:
                hit = False
            if hit:
                seat.key, seat.tree = st.key, None
            st.hit = hit
            self.stats["restores" if hit else "restore_misses"] += 1
            return True
        try:
            if st.commit is not None:
                ad.commit(st.commit)
                if st.commit and seat.tree is not None:
                    seat.key = chain_key(seat.key, seat.tree, st.commit)
                seat.tree = None
        except ValueError:
            raise _Refused("desync", length=ad.length, slot=st.slot) from None
        if ad.length != st.length or (st.key is not None and st.key != seat.key):
            raise _Refused("desync", length=ad.length, slot=st.slot)
        if st.op == OP_SNAPSHOT:
            try:
                ad.snapshot(seat.key, keep=st.keep)
            except ValueError as e:
                raise _Refused("block", message=str(e)[:200]) from None
            self.stats["snapshots"] += 1
        return st.op != OP_STEP

    def _answer(self, seat: _Seat, st: Step) -> dict:
        ad = seat.adapter
        if st.op == OP_RESET:
            return {"length": 0}
        if st.op == OP_SYNC:
            return {"length": ad.length}
        if st.op == OP_PROBE:
            return {"length": ad.length, "digest": ad.digest()}
        if st.op == OP_RESTORE:
            return {"hit": st.hit, "length": ad.length}
        if st.op == OP_DROP:
            return {"dropped": len(st.keys)}
        return {"length": ad.length, "key": seat.key.hex()}

    def _rows(self, seat: _Seat, st: Step):
        try:
            carry = seat.adapter.rows(st.block, None)
        except ValueError as e:
            raise _Refused("block", message=str(e)[:200], slot=st.slot) from None
        self._ran(seat, st.block)
        return carry

    @staticmethod
    def _ran(seat: _Seat, block: Block) -> None:
        if block.chain:
            seat.key, seat.tree = chain_key(seat.key, block), None
        else:
            seat.tree = block

    def _round(self, link, fr, steps: list) -> None:
        """One joint round: every slot's commit and check first, then every slot's rows, one carry.
        A header that fails its check is answered with its place; the ones before it are settled."""
        if any(st.op != OP_STEP or st.block.chain or st.quiet for st in steps):
            return _error(link, fr.seq, "header")
        if len({st.slot for st in steps}) != len(steps):
            return _error(link, fr.seq, "header")
        seats = []
        for i, st in enumerate(steps):
            try:
                seat = self._seat(st.slot)
                self._settle(seat, st)
            except _Refused as e:
                return _error(link, fr.seq, e.code, **{**e.info, "index": i})
            seats.append(seat)
        try:
            carries = self.adapter.round([(seat.adapter, st.block, None) for seat, st in zip(seats, steps)])
        except ValueError as e:
            return _error(link, fr.seq, "block", message=str(e)[:200])
        for seat, st in zip(seats, steps):
            self._ran(seat, st.block)
        dtype, dims, buf = self.adapter.encode(self.adapter.join(carries))
        self.steps += len(steps)
        self.stats["rounds"] += 1
        link.send(L.CARRY, buf, dtype=dtype, dims=dims, seq=fr.seq, deadline=CHUNK_S)


class _Node:
    """One block of a sequence's log: what was run, the key and the length after it. The log is a
    chain of these back to the empty sequence; sequences that share a start share its nodes."""

    __slots__ = ("parent", "key", "length", "block", "path", "orphan")

    def __init__(self, parent, key: bytes, length: int, block=None, path=None, orphan: bool = False):
        self.parent, self.key, self.length = parent, key, length
        self.block, self.path, self.orphan = block, path, orphan


class Coordinator:
    """The tail stage's side: every request of the split model goes through here.

    One coordinator a slot. Slot 0's owns the link; the others (`side`) send through it."""

    def __init__(self, adapter: StageAdapter, step_deadline: float = STEP_S,
                 chunk_deadline: float = CHUNK_S, replay_rows: int = REPLAY_ROWS, slot: int = 0, main=None):
        if adapter.role != "tail":
            raise ValueError("the coordinator is the tail stage")
        self.adapter = adapter
        self.step_deadline, self.chunk_deadline, self.replay_rows = step_deadline, chunk_deadline, replay_rows
        self.slot, self.main = slot, main or self
        self.exact = bool(getattr(adapter, "exact", False))
        #: True: a new link leaves this stage's sequence as it is (its caches hold it) and the front is
        #: brought back to it at the next request; False: both sequences start empty
        self.keep = False
        self.replays = 0                 # sequences run again as prompt chunks on both stages
        self.rebuilds = 0                # the front brought back to this side's state from the log
        # what crossed the link, by kind of block: prompt chunks, decode steps, verify trees sent alone,
        # joint rounds and the blocks in them (`round_max`: the most slots one round carried)
        self.stats = {"chunks": 0, "steps": 0, "trees": 0, "rounds": 0, "round_blocks": 0, "round_max": 0,
                      "snapshots": 0, "restore_hits": 0, "restore_misses": 0, "quiet_steps": 0, "drops": 0}
        self._tokens: list = []          # the committed sequence, for a replay
        self._pending = None             # the commit path the front has not seen yet
        self._block = None               # the tree block a commit may keep rows of
        # the empty sequence: one key for every slot of a link (the seed is slot 0's adapter's)
        self._root = _Node(None, root_key(getattr(adapter, "seed", b"")), 0) if main is None else main._root
        self._node = self._root          # the log's end: the committed state's key and length
        #: key -> [the log's node, the slots whose front holds it, kept in the front's directory]; one
        #: table a link: a state marked on one slot can be put back on another (its front is rebuilt)
        self.marks: OrderedDict = OrderedDict() if main is None else main.marks
        if main is None:
            self.link = None
            self.epoch = 0
            self.why = "never attached"
            self._seq = 0
            self._lock = threading.RLock()
            self.sides: dict = {}
        else:
            self._lock = main._lock

    def side(self, adapter: StageAdapter, slot: int) -> "Coordinator":
        """The coordinator of another slot over this one's link."""
        if self.main is not self or slot <= 0:
            raise ValueError("a side slot is slot 1 or above of the coordinator that owns the link")
        co = self.sides[slot] = Coordinator(adapter, self.step_deadline, self.chunk_deadline, self.replay_rows,
                                            slot=slot, main=self)
        return co

    # ------------------------------------------------------------------ the link
    def attach(self, link) -> None:
        """Take a link whose handshake is done. Its epoch is new, so the front starts empty; so does
        this stage, unless it keeps its sequence (`keep`): then the front is brought back to it
        from its own kept states or the log when the next request finds it elsewhere."""
        with self._lock:
            if self.link is not None and self.link.state == "up":
                self.link.close()
            for co in [self] + list(self.sides.values()):
                if co.keep and co.exact:
                    co._pending = None       # the rebuild lands every commit of the log
                else:
                    co._clear()
            for m in self.marks.values():
                m[1], m[2] = set(), False        # a new front may hold none of them
            self.link, self.epoch, self.why = link, link.epoch, None
            link.on_down = self._on_down
            link.start_heartbeat()

    def _on_down(self, why: str) -> None:
        self.why = why

    def _clear(self) -> None:
        self.adapter.reset()
        self._tokens, self._pending, self._block = [], None, None
        self._node = self._root

    def health(self) -> dict:
        m = self.main
        up = m.link is not None and m.link.state == "up"
        out = {"status": "ok" if up else "degraded",
               "stage": {"role": "tail", "cut": self.adapter.cut,
                         "link": {"state": "up" if up else "down", "epoch": m.epoch}}}
        if not up:
            out["stage"]["link"]["reason"] = m.why
        return out

    def report(self) -> dict:
        """The counters of every slot's kept state, for `/health`."""
        cos = [self.main] + list(self.main.sides.values())
        out = {k: sum(c.stats[k] for c in cos) for k in self.stats}
        out["round_max"] = self.main.stats["round_max"]
        out.update(marks=len(self.marks), rebuilds=sum(c.rebuilds for c in cos),
                   replays=sum(c.replays for c in cos), slots=len(cos))
        return out

    def _down(self, exc: Exception) -> StageDown:
        m = self.main
        if m.link is not None and m.link.state == "up":
            try:
                _error(m.link, m._seq, "stage_down")
            except L.LinkError:
                pass
            m.link.down(getattr(exc, "code", "link"))
        m.why = m.why or getattr(exc, "code", "link")
        return StageDown(f"stage_down: {m.why}")

    def _ask(self, payload: bytes, deadline: float, into=None):
        m = self.main
        link = m.link
        if link is None or link.state != "up":
            raise StageDown(f"stage_down: {m.why}")
        m._seq += 1
        try:
            with link.lock:
                link.send(L.STEP, payload, seq=m._seq, deadline=deadline)
                fr = link.recv(deadline, into=into)
        except L.LinkError as e:
            raise self._down(e) from e
        if fr.seq != m._seq or fr.epoch != m.epoch:
            raise self._down(L.LinkProtocol("an answer to another request"))
        if fr.kind == L.ERROR:
            info = fr.json()
            err = StageError(str(info.get("code", "error")), json.dumps(info))
            err.info = info
            raise err
        return fr

    # ------------------------------------------------------------------ requests
    @property
    def length(self) -> int:
        return self.adapter.length

    @property
    def key(self) -> bytes:
        """The key of the committed state: both stages' name for it."""
        return self._node.key

    def reset(self) -> None:
        """A new sequence on both stages."""
        with self._lock:
            self._clear()
            self._ask(pack_step(OP_RESET, 0, slot=self.slot), self.step_deadline)

    def prefill(self, tokens, head: str = "all", graph: int = 0):
        """A prompt chunk: the logits its head asks for; both stages keep its rows."""
        with self._lock:
            return self._run(Block(tokens, None, slot=self.slot, graph=graph, head=head))

    def verify(self, tokens, parents, graph: int = 0):
        """A draft block's logits [rows, vocab]; the block stays pending until `commit`."""
        with self._lock:
            return self._run(Block(tokens, parents, slot=self.slot, graph=graph))

    def commit(self, path) -> None:
        """Keep the rows `path` of the last verify block. The tail keeps them now; the front is
        told with the next header."""
        with self._lock:
            path = [int(i) for i in path]
            if self._block is None:
                raise ValueError("no block to commit")
            self.adapter.commit(path)
            self._tokens += [self._block.tokens[i] for i in path]
            if path and self.exact:
                self._node = _Node(self._node, chain_key(self._node.key, self._block, path),
                                   self._node.length + len(path), self._block, path)
            self._pending, self._block = path, None

    def sync(self) -> int:
        """Deliver a pending commit now; the front's length."""
        with self._lock:
            return self._flush(OP_SYNC)["length"]

    def probe(self) -> dict:
        """The front's committed length and state digest (a pending commit lands first)."""
        with self._lock:
            return self._flush(OP_PROBE)

    def _at(self) -> tuple:
        """(length, key) a header names: the log's own in exact mode."""
        if self.exact:
            return self._node.length, self._node.key
        return self.adapter.length, None

    def _flush(self, op: int, keep: bool = False) -> dict:
        n, key = self._at()
        try:
            fr = self._ask(pack_step(op, n, commit=self._pending, key=key, keep=keep, slot=self.slot),
                           self.chunk_deadline if keep else self.step_deadline)
        except StageError as e:
            if e.code != "desync":
                raise
            if self.exact:
                self._rebuild()
            else:
                self._replay()
            n, key = self._at()
            fr = self._ask(pack_step(op, n, key=key, keep=keep, slot=self.slot),
                           self.chunk_deadline if keep else self.step_deadline)
        self._pending = None
        return fr.json()

    def _deadline(self, block: Block) -> float:
        # one row is a step whatever kind of block carries it (a decode token is a chain of one row);
        # only a chain of several rows, a prompt chunk, gets the chunk's time
        return self.chunk_deadline if block.chain and block.rows > 1 else self.step_deadline

    def _run(self, block: Block, replaying: bool = False):
        ad = self.adapter
        block.pos_class = ad.position_class(ad.length, block.rows)
        deadline = self._deadline(block)
        into = ad.staging(block.rows * ad.carry_bytes_per_row)
        n, key = self._at()
        try:
            fr = self._ask(pack_step(OP_STEP, n, block, self._pending, key=key), deadline, into)
        except StageError as e:
            if e.code != "desync" or replaying:
                raise
            if self.exact:
                self._rebuild()
            else:
                self._replay()
            n, key = self._at()
            fr = self._ask(pack_step(OP_STEP, n, block, key=key), deadline, into)
        self._pending = None
        want = (block.rows,) + tuple(ad.carry_dims)
        if fr.kind != L.CARRY or L.DTYPES[fr.dtype][0] != ad.carry_dtype or tuple(fr.dims) != want:
            raise self._down(L.LinkProtocol("a carry of another shape"))
        out = ad.rows(block, ad.decode(fr.dtype, fr.dims, fr.payload))
        self._ran(block)
        self.stats["trees" if not block.chain else "chunks" if block.rows > 1 else "steps"] += 1
        return out

    def _ran(self, block: Block) -> None:
        if block.chain:
            self._tokens += block.tokens
            self._block = None
            if self.exact:
                self._node = _Node(self._node, chain_key(self._node.key, block), self._node.length + block.rows,
                                   block)
        else:
            self._block = block

    def _replay(self) -> None:
        """The front's state is not this side's: start both over and run the committed tokens
        again as prompt chunks."""
        tokens = self._tokens
        self.replays += 1
        self._clear()
        self._ask(pack_step(OP_RESET, 0, slot=self.slot), self.step_deadline)
        for at in range(0, len(tokens), self.replay_rows):
            self._run(Block(tokens[at:at + self.replay_rows], None, slot=self.slot, head="none"), replaying=True)

    # ------------------------------------------------------------------ a joint round
    def verify_round(self, items: list) -> list:
        """[(coordinator, tokens, parents)] of this link's slots, one block each: every header in one
        request, every slot's rows in one carry; each slot's block stays pending until its commit.
        Returns each one's logits."""
        m = self.main
        with self._lock:
            cos = [co for co, _, _ in items]
            if len({co.slot for co in cos}) != len(cos) or any(co.main is not m for co in cos):
                raise ValueError("a round takes one block a slot of one link")
            blocks = [Block(tokens, parents, slot=co.slot) for co, tokens, parents in items]
            rows = sum(b.rows for b in blocks)
            for co, b in zip(cos, blocks):
                b.pos_class = co.adapter.position_class(co.adapter.length, b.rows)
            ad = m.adapter
            into = ad.staging(rows * ad.carry_bytes_per_row)
            for _ in range(len(cos) + 1):
                heads = []
                for co, b in zip(cos, blocks):
                    n, key = co._at()
                    heads.append(pack_step(OP_STEP, n, b, co._pending, key=key))
                try:
                    fr = self._ask(pack_round(heads), self.step_deadline, into)
                    break
                except StageError as e:
                    i = getattr(e, "info", {}).get("index")
                    if e.code != "desync" or i is None or not 0 <= i < len(cos) or not cos[i].exact:
                        raise
                    for co in cos[:i]:
                        co._pending = None           # the headers before it were settled
                    cos[i]._rebuild()
            else:
                raise StageError("desync", "desync: a round's slots could not be brought back")
            for co in cos:
                co._pending = None
            if (fr.kind != L.CARRY or L.DTYPES[fr.dtype][0] != ad.carry_dtype
                    or tuple(fr.dims) != (rows,) + tuple(ad.carry_dims)):
                raise self._down(L.LinkProtocol("a carry of another shape"))
            parts = ad.split(ad.decode(fr.dtype, fr.dims, fr.payload), [b.rows for b in blocks])
            outs = ad.round([(co.adapter, b, part) for co, b, part in zip(cos, blocks, parts)])
            for co, b in zip(cos, blocks):
                co._ran(b)
            m.stats["rounds"] += 1
            m.stats["round_blocks"] += len(cos)
            m.stats["round_max"] = max(m.stats["round_max"], len(cos))
            return outs

    # ------------------------------------------------------------------ state kept under a key
    def mark(self, keep: bool = False) -> bytes:
        """The key of the committed state, with the front asked to keep its half under it (`keep`:
        in its own directory too). The key is returned whatever the link says: a front that does
        not hold it is rebuilt from the log when the key is restored."""
        with self._lock:
            node = self._node
            m = self.marks.get(node.key)
            if m is None:
                m = self.marks[node.key] = [node, set(), False]
                self._trim()
            else:
                self.marks.move_to_end(node.key)
            if self.exact and (self.slot not in m[1] or (keep and not m[2])):
                try:
                    self._flush(OP_SNAPSHOT, keep=keep)
                    m[1].add(self.slot)
                    m[2] = m[2] or keep
                    self.stats["snapshots"] += 1
                except StageError:
                    pass
            return node.key

    def _trim(self) -> None:
        gone: dict = {}
        while len(self.marks) > MARKS:
            key, m = self.marks.popitem(last=False)
            for slot in m[1]:
                gone.setdefault(slot, []).append(key)
        for slot, keys in gone.items():
            co = self.main if slot == 0 else self.main.sides.get(slot)
            if co is not None:
                co.drop(keys)

    def drop(self, keys) -> None:
        """Tell this slot's front to forget `keys` (nothing is said to a link that is down)."""
        with self._lock:
            keys = [bytes(k) for k in keys]
            for k in keys:
                m = self.marks.get(k)
                if m is not None:
                    m[1].discard(self.slot)
                    if not m[1]:
                        del self.marks[k]
            try:
                for at in range(0, len(keys), 1024):
                    self._ask(pack_step(OP_DROP, 0, slot=self.slot, keys=keys[at:at + 1024]), self.step_deadline)
                    self.stats["drops"] += 1
            except StageError:
                pass

    def _front_restore(self, key: bytes, length: int) -> bool:
        fr = self._ask(pack_step(OP_RESTORE, length, key=key, slot=self.slot), self.chunk_deadline)
        hit = bool(fr.json().get("hit"))
        self.stats["restore_hits" if hit else "restore_misses"] += 1
        return hit

    def restore(self, key: bytes, length: int) -> str:
        """The tail's state is the one marked `key` again (its caller wrote it back): bring the
        front there. "hit": the front held it. "rebuilt": it did not, and ran the log's blocks
        again. StageError `snapshot`: no log of that key here and the front does not hold it; then
        nothing changed on either side."""
        with self._lock:
            key = bytes(key)
            m = self.marks.get(key)
            if m is not None and m[0].length != length:
                raise StageError("snapshot", f"snapshot: the state under this key has {m[0].length} rows, not {length}")
            hit = self._front_restore(key, length)
            if not hit and (m is None or m[0].orphan):
                raise StageError("snapshot", "snapshot: the front stage does not hold its half of this state")
            if m is None:
                m = self.marks[key] = [_Node(None, key, length, orphan=True), {self.slot}, False]
                self._trim()
            else:
                self.marks.move_to_end(key)
            self._node, self._pending, self._block = m[0], None, None
            self._tokens = []
            if hit:
                m[1].add(self.slot)
                return "hit"
            self._rebuild(missed=key)
            return "rebuilt"

    def _rebuild(self, missed: bytes | None = None) -> None:
        """The front's state is not this side's: bring it to the log's end. From the nearest key
        the front still holds (`missed`: one it was just asked for), else from an empty sequence,
        each block of the log is sent again as the kind of block it was, and no carry comes back.
        This side's rows stay."""
        target = self._node
        self.rebuilds += 1
        self._pending = None
        todo, node, tries, base = [], target, 0, None
        while node.parent is not None:
            if node.key != missed and node.key in self.marks and tries < REBUILD_TRIES:
                tries += 1
                if self._front_restore(node.key, node.length):
                    base = node
                    break
            todo.append(node)
            node = node.parent
        if base is None:
            if node.orphan:
                raise StageError("desync", "desync: the front lost a state this stage has no log of")
            self._ask(pack_step(OP_RESET, 0, slot=self.slot), self.step_deadline)
        at = base or node
        for node in reversed(todo):
            b = node.block
            b.pos_class = self.adapter.position_class(at.length, b.rows)
            got = self._ask(pack_step(OP_STEP, at.length, b, key=at.key, quiet=True), self._deadline(b)).json()
            if node.path is not None:
                # the tree's commit, checked by the length and the key it leaves
                got = self._ask(pack_step(OP_SYNC, node.length, commit=node.path, key=node.key, slot=self.slot),
                                self.step_deadline).json()
                got["key"] = node.key.hex()
            if int(got.get("length", -1)) != node.length or got.get("key") != node.key.hex():
                raise StageError("desync", "desync: the front did not come back to this stage's state")
            self.stats["quiet_steps"] += 1
            at = node
