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

The step header, little-endian, then the arrays:

    op u8 | flags u8 | slot u16 | position class u16 | graph u16 | length u32 | rows u32 | commit u16
    tokens i32[rows] | parents i16[rows] (absent for a chain) | commit path i16[commit]
"""

from __future__ import annotations

import hashlib
import importlib
import json
import struct
import sys
import threading
from array import array

from engine.methods import link as L

STEP_S = 5.0            # a verify block's carry
CHUNK_S = 60.0          # a prompt chunk's carry
IDLE_S = 5.0            # a follower without a frame for this long takes the link for dead
REPLAY_ROWS = 1024      # a replay's chunk

OP_STEP, OP_SYNC, OP_RESET, OP_PROBE = 1, 2, 3, 4
F_CHAIN, F_COMMIT = 1, 2
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


def pack_step(op: int, length: int, block: Block | None = None, commit: list | None = None) -> bytes:
    flags = (F_COMMIT if commit is not None else 0) | (F_CHAIN if block is not None and block.chain else 0)
    n = block.rows if block is not None else 0
    path = commit or []
    out = _HEAD.pack(op, flags, block.slot if block else 0, block.pos_class if block else 0,
                     block.graph if block else 0, length, n, len(path))
    if block is not None:
        out += _ints("i", block.tokens)
        if not block.chain:
            out += _ints("h", block.parents)
    return out + _ints("h", path)


def unpack_step(raw: bytes) -> tuple:
    """(op, length, block or None, commit path or None) of a step header, or ValueError."""
    raw = bytes(raw)
    if len(raw) < _HEAD.size:
        raise ValueError("a step header too short")
    op, flags, slot, pos_class, graph, length, n, nc = _HEAD.unpack_from(raw)
    chain = bool(flags & F_CHAIN)
    want = _HEAD.size + 4 * n + (0 if chain or not n else 2 * n) + 2 * nc
    if op not in (OP_STEP, OP_SYNC, OP_RESET, OP_PROBE) or len(raw) != want or (op == OP_STEP) != bool(n):
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
    if not flags & F_COMMIT:
        if nc:
            raise ValueError("a commit path without its flag")
        path = None
    return op, length, block, path


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
    """

    family = ""
    layers = 0
    lo = 0
    hi = 0
    cuts: tuple = ()
    carry_dtype = "bfloat16"
    carry_dims: tuple = ()

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


class Follower:
    """The front stage's loop: block on the link, act on a header, answer with the carry."""

    def __init__(self, adapter: StageAdapter, idle_deadline: float = IDLE_S):
        if adapter.role != "front":
            raise ValueError("the follower is the front stage")
        self.adapter = adapter
        self.idle_deadline = idle_deadline
        self.steps = 0

    def serve(self, link) -> str:
        """Serve one link (its handshake done) until it ends; returns why. The state starts empty
        and is dropped at the end: a link's rows never outlive its epoch."""
        ad = self.adapter
        ad.reset()
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
                    self._step(link, fr)
        except L.LinkError as e:
            return e.code
        finally:
            ad.reset()

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
        ad = self.adapter
        try:
            op, length, block, path = unpack_step(fr.payload)
        except ValueError:
            return _error(link, fr.seq, "header")
        if op == OP_RESET:
            ad.reset()
            return link.send(L.REPLY, b'{"length": 0}', seq=fr.seq)
        try:
            if path is not None:
                ad.commit(path)
        except ValueError:
            return _error(link, fr.seq, "desync", length=ad.length)
        if ad.length != length:
            return _error(link, fr.seq, "desync", length=ad.length)
        if op == OP_SYNC:
            return link.send(L.REPLY, json.dumps({"length": ad.length}).encode(), seq=fr.seq)
        if op == OP_PROBE:
            return link.send(L.REPLY, json.dumps({"length": ad.length, "digest": ad.digest()}).encode(),
                             seq=fr.seq)
        try:
            carry = ad.rows(block, None)
        except ValueError as e:
            return _error(link, fr.seq, "block", message=str(e)[:200])
        dtype, dims, buf = ad.encode(carry)
        self.steps += 1
        link.send(L.CARRY, buf, dtype=dtype, dims=dims, seq=fr.seq, deadline=CHUNK_S)


class Coordinator:
    """The tail stage's side: every request of the split model goes through here."""

    def __init__(self, adapter: StageAdapter, step_deadline: float = STEP_S,
                 chunk_deadline: float = CHUNK_S, replay_rows: int = REPLAY_ROWS):
        if adapter.role != "tail":
            raise ValueError("the coordinator is the tail stage")
        self.adapter = adapter
        self.step_deadline, self.chunk_deadline, self.replay_rows = step_deadline, chunk_deadline, replay_rows
        self.link = None
        self.epoch = 0
        self.why = "never attached"
        self.replays = 0
        self._seq = 0
        self._tokens: list = []          # the committed sequence, for a replay
        self._pending = None             # the commit path the front has not seen yet
        self._block = None               # the tree block a commit may keep rows of
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ the link
    def attach(self, link) -> None:
        """Take a link whose handshake is done. Its epoch is new, so the state starts empty."""
        with self._lock:
            if self.link is not None and self.link.state == "up":
                self.link.close()
            self._clear()
            self.link, self.epoch, self.why = link, link.epoch, None
            link.on_down = self._on_down
            link.start_heartbeat()

    def _on_down(self, why: str) -> None:
        self.why = why

    def _clear(self) -> None:
        self.adapter.reset()
        self._tokens, self._pending, self._block = [], None, None

    def health(self) -> dict:
        up = self.link is not None and self.link.state == "up"
        out = {"status": "ok" if up else "degraded",
               "stage": {"role": "tail", "cut": self.adapter.cut,
                         "link": {"state": "up" if up else "down", "epoch": self.epoch}}}
        if not up:
            out["stage"]["link"]["reason"] = self.why
        return out

    def _down(self, exc: Exception) -> StageDown:
        if self.link is not None and self.link.state == "up":
            try:
                _error(self.link, self._seq, "stage_down")
            except L.LinkError:
                pass
            self.link.down(getattr(exc, "code", "link"))
        self.why = self.why or getattr(exc, "code", "link")
        return StageDown(f"stage_down: {self.why}")

    def _ask(self, payload: bytes, deadline: float, into=None):
        link = self.link
        if link is None or link.state != "up":
            raise StageDown(f"stage_down: {self.why}")
        self._seq += 1
        try:
            with link.lock:
                link.send(L.STEP, payload, seq=self._seq, deadline=deadline)
                fr = link.recv(deadline, into=into)
        except L.LinkError as e:
            raise self._down(e) from e
        if fr.seq != self._seq or fr.epoch != self.epoch:
            raise self._down(L.LinkProtocol("an answer to another request"))
        if fr.kind == L.ERROR:
            info = fr.json()
            raise StageError(str(info.get("code", "error")), json.dumps(info))
        return fr

    # ------------------------------------------------------------------ requests
    @property
    def length(self) -> int:
        return self.adapter.length

    def reset(self) -> None:
        """A new sequence on both stages."""
        with self._lock:
            self._clear()
            self._ask(pack_step(OP_RESET, 0), self.step_deadline)

    def prefill(self, tokens, head: str = "all", graph: int = 0):
        """A prompt chunk: the logits its head asks for; both stages keep its rows."""
        with self._lock:
            return self._run(Block(tokens, None, graph=graph, head=head))

    def verify(self, tokens, parents, graph: int = 0):
        """A draft block's logits [rows, vocab]; the block stays pending until `commit`."""
        with self._lock:
            return self._run(Block(tokens, parents, graph=graph))

    def commit(self, path) -> None:
        """Keep the rows `path` of the last verify block. The tail keeps them now; the front is
        told with the next header."""
        with self._lock:
            path = [int(i) for i in path]
            if self._block is None:
                raise ValueError("no block to commit")
            self.adapter.commit(path)
            self._tokens += [self._block.tokens[i] for i in path]
            self._pending, self._block = path, None

    def sync(self) -> int:
        """Deliver a pending commit now; the front's length."""
        with self._lock:
            return self._flush(OP_SYNC)["length"]

    def probe(self) -> dict:
        """The front's committed length and state digest (a pending commit lands first)."""
        with self._lock:
            return self._flush(OP_PROBE)

    def _flush(self, op: int) -> dict:
        try:
            fr = self._ask(pack_step(op, self.adapter.length, commit=self._pending), self.step_deadline)
        except StageError as e:
            if e.code != "desync":
                raise
            self._replay()
            fr = self._ask(pack_step(op, self.adapter.length), self.step_deadline)
        self._pending = None
        return fr.json()

    def _run(self, block: Block, replaying: bool = False):
        ad = self.adapter
        block.pos_class = ad.position_class(ad.length, block.rows)
        # one row is a step whatever kind of block carries it (a decode token is a chain of one row);
        # only a chain of several rows, a prompt chunk, gets the chunk's time
        deadline = self.chunk_deadline if block.chain and block.rows > 1 else self.step_deadline
        into = ad.staging(block.rows * ad.carry_bytes_per_row)
        try:
            fr = self._ask(pack_step(OP_STEP, ad.length, block, self._pending), deadline, into)
        except StageError as e:
            if e.code != "desync" or replaying:
                raise
            self._replay()
            fr = self._ask(pack_step(OP_STEP, ad.length, block), deadline, into)
        self._pending = None
        want = (block.rows,) + tuple(ad.carry_dims)
        if fr.kind != L.CARRY or L.DTYPES[fr.dtype][0] != ad.carry_dtype or tuple(fr.dims) != want:
            raise self._down(L.LinkProtocol("a carry of another shape"))
        out = ad.rows(block, ad.decode(fr.dtype, fr.dims, fr.payload))
        if block.chain:
            self._tokens += block.tokens
            self._block = None
        else:
            self._block = block
        return out

    def _replay(self) -> None:
        """The front's state is not this side's: start both over and run the committed tokens
        again as prompt chunks."""
        tokens = self._tokens
        self.replays += 1
        self._clear()
        self._ask(pack_step(OP_RESET, 0), self.step_deadline)
        for at in range(0, len(tokens), self.replay_rows):
            self._run(Block(tokens[at:at + self.replay_rows], None, head="none"), replaying=True)
