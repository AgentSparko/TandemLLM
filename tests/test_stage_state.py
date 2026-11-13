"""engine/methods/stages.py: the state two stages keep beyond the running sequence (ENG-424).

A toy family over a real link on loopback (no torch): the front is a follower thread, the test
thread the coordinator.

  -  both stages name a state by the same key, computed on each side from the blocks it ran
  -  snapshot, restore and drop cross the link: a restored state continues with the answers of
     the sequence that was never left; a dropped key is a miss
  -  a miss is rebuilt on the front alone, each block as the kind it was (a prompt chunk, a decode
     step, a tree and its path), and the tail's rows are not run again
  -  a front whose rows are not the tail's is found by its key, also at the same length
  -  a new epoch empties both running sequences; a state restored after it is the front's own kept
     one (same process) or rebuilt (a new front): never another state's section
  -  a tail that keeps its sequence over a new link (its caches hold it) is left as it is, and
     the front is brought back to it at the next request
  -  the front keeps a state only under the key it computed itself
  -  a state this coordinator has no log of and the front does not hold is refused, nothing changes
  -  several slots: one sequence each; a joint round is one request and one carry and gives what
     each slot gives alone; a slot that lost its rows inside a round is rebuilt
  -  the header: keys, the quiet and keep flags, a drop's keys and a round parse back; a header of
     the first version reads as before

Run: python3 tests/test_stage_state.py
"""

from __future__ import annotations

import os
import secrets
import sys
import threading
import time
from array import array

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from engine.methods import link as L  # noqa: E402
from engine.methods import stages as S  # noqa: E402

MOD = 2 ** 31 - 1
STEP = 1
PROMPT = [5, 9, 3, 17, 22, 3, 40, 41, 12]


def raises(kind, fn):
    try:
        fn()
    except kind as e:
        return e
    raise AssertionError(f"no {kind.__name__}")


class Toy(S.StageAdapter):
    """Four layers of integer arithmetic, one number of state a layer and one cache row a token.
    A row's value depends on the kind of block that wrote it, as a model's rows do (a prompt chunk
    and a decode step run on other kernels), so a block run again as another kind is seen."""

    family = "toy"
    layers = 4
    cuts = (1, 2, 3)
    carry_dtype = "int32"
    carry_dims = (2,)
    exact = True

    def __init__(self, lo: int, hi: int, slots: int = 1, slot_id: int = 0):
        self.lo, self.hi, self.slots, self.slot_id = lo, hi, slots, slot_id
        self.kept: dict = {}
        self.ran: list = []
        self.sides: dict = {}
        self.reset()

    @property
    def length(self) -> int:
        return self.n

    def reset(self) -> None:
        self.acc = {li: 0 for li in range(self.lo, self.hi)}
        self.cache: list = []
        self.n = 0
        self.pend = None

    def rows(self, block, carry):
        if block.slot != self.slot_id:
            raise ValueError("another slot's block")
        parents = list(range(-1, block.rows - 1)) if block.chain else block.parents
        kind = 3 if not block.chain else (2 if block.graph == STEP else 1)
        depth = []
        for p in parents:
            depth.append(0 if p < 0 else depth[p] + 1)
        out = []
        for i, t in enumerate(block.tokens):
            v = [t, 0] if self.lo == 0 else list(carry[i])
            for li in range(self.lo, self.hi):
                v = [(v[0] * 31 + self.acc[li] + li + depth[i] + self.n + (kind if block.chain else 0)) % MOD,
                     (v[1] + v[0] + sum(self.cache[-2:])) % MOD]
            out.append(v)
        self.pend = (block.tokens, parents, [t * 4 + kind for t in block.tokens], kind)
        self.ran.append((kind, list(block.tokens)))
        if block.chain:
            self.commit(list(range(block.rows)))
        if self.hi < self.layers:
            return out
        return None if block.head == "none" else [a ^ b for a, b in out]

    def commit(self, path) -> None:
        if self.pend is None:
            if path:
                raise ValueError("no block to commit")
            return
        tokens, parents, vals, kind = self.pend
        if path and (path[0] != 0 or any(not 0 <= b < len(parents) or parents[b] != a
                                         for a, b in zip(path, path[1:]))):
            raise ValueError("not a root path")
        for i in path:
            for li in self.acc:
                self.acc[li] = (self.acc[li] * 7 + tokens[i] + 1) % MOD
            self.cache.append(vals[i])
        self.n += len(path)
        self.pend = None

    def state(self) -> dict:
        return {**{f"acc.{li}": [v] for li, v in self.acc.items()}, "cache": list(self.cache)}

    # --- kept state
    def snapshot(self, key: bytes, keep: bool = False) -> None:
        self.kept[bytes(key)] = (dict(self.acc), list(self.cache), self.n, keep)

    def restore(self, key: bytes, length: int) -> bool:
        got = self.kept.get(bytes(key))
        if got is None or got[2] != length:
            return False
        self.acc, self.cache, self.n, self.pend = dict(got[0]), list(got[1]), got[2], None
        return True

    def drop(self, keys) -> None:
        for k in keys:
            self.kept.pop(bytes(k), None)

    def slot(self, i: int):
        if not 0 < i < self.slots:
            raise ValueError(f"this stage serves {self.slots} slots")
        if i not in self.sides:
            self.sides[i] = Toy(self.lo, self.hi, self.slots, i)
        return self.sides[i]

    def encode(self, t):
        rows = len(t)
        if rows and not isinstance(t[0], list):
            return L.DTYPE_CODES["int32"], (rows,), array("i", [x % MOD for x in t]).tobytes()
        flat = [x for r in t for x in r]
        return L.DTYPE_CODES["int32"], (rows, 2), array("i", flat).tobytes()

    def decode(self, dtype, dims, buf):
        a = array("i")
        a.frombytes(bytes(buf))
        flat = a.tolist()
        return [flat[i:i + 2] for i in range(0, len(flat), 2)]


class Whole:
    """The model in one process, with the coordinator's verbs."""

    def __init__(self):
        self.ad = Toy(0, 4)

    def prefill(self, tokens, head="all", graph=0):
        return self.ad.rows(S.Block(tokens, None, graph=graph, head=head), None)

    def verify(self, tokens, parents):
        return self.ad.rows(S.Block(tokens, parents), None)

    def commit(self, path):
        self.ad.commit(list(path))


class Pair:
    """A front (a follower thread) and a tail's coordinator over loopback."""

    def __init__(self, k: int = 2, slots: int = 1):
        self.k, self.slots = k, slots
        self.key = secrets.token_bytes(32)
        self.block = {"identity": 1, "code": "c0de", "weights": "w8", "family": "toy", "layers": 4}
        self.tail = Toy(k, 4, slots)
        self.co = S.Coordinator(self.tail, step_deadline=10.0, chunk_deadline=10.0)
        self.threads: list = []
        self.new_front()
        self.connect()

    def new_front(self) -> None:
        """A front process's start: empty, nothing kept."""
        self.front = Toy(0, self.k, self.slots)
        self.follower = S.Follower(self.front, idle_deadline=3.0)
        self.listener = L.Listener("127.0.0.1:0")
        fol, lis = self.follower, self.listener

        def connect(last):
            return S.open_link(self.key, S.stage_identity(self.block, fol.adapter), listener=lis,
                               last_epoch=last, accept_deadline=0.3)

        def serve():
            try:
                fol.run(connect)
            except Exception:                              # noqa: BLE001  (the listener closed)
                pass
        t = threading.Thread(target=serve, daemon=True)
        t.start()
        self.threads.append((t, lis))

    def connect(self):
        end = time.monotonic() + 10
        while True:
            try:
                link = S.open_link(self.key, S.stage_identity(self.block, self.tail),
                                   peer=f"127.0.0.1:{self.listener.port}", last_epoch=self.co.epoch)
                break
            except L.LinkError:
                if time.monotonic() > end:
                    raise
                time.sleep(0.05)
        self.co.attach(link)
        return link

    def cut(self) -> None:
        self.co.link.close()

    def kill_front(self) -> None:
        self.co.link.close()
        self.listener.close()

    def close(self) -> None:
        if self.co.link is not None:
            self.co.link.close()
        for _, lis in self.threads:
            lis.close()


def play(m) -> list:
    """A prompt chunk, decode steps, a tree and its path, a tree kept to its root, a chunk."""
    out = [m.prefill(PROMPT)]
    out.append(m.prefill([11], graph=STEP))
    out.append(m.prefill([12], graph=STEP))
    out.append(m.verify([7, 19, 23, 5], [-1, 0, 0, 2]))
    m.commit([0, 2, 3])
    out.append(m.verify([1, 2, 3], [-1, 0, 1]))
    m.commit([0])
    out.append(m.verify([4, 5], [-1, 0]))
    m.commit([])
    out.append(m.prefill([30, 31, 32], head="last"))
    return out


def more(m) -> list:
    out = [m.prefill([50], graph=STEP), m.verify([8, 9, 10], [-1, 0, 1])]
    m.commit([0, 1, 2])
    out.append(m.prefill([60, 61]))
    return out


def test_both_stages_name_a_state_by_the_same_key():
    x = Pair()
    try:
        co, seat = x.co, x.follower.seats
        assert co.key == S.root_key() == seat[0].key
        keys = [co.key]
        co.prefill(PROMPT)
        keys.append(co.key)
        assert seat[0].key == co.key
        co.prefill([11], graph=STEP)
        keys.append(co.key)
        co.verify([7, 19, 23], [-1, 0, 0])
        assert co.key == keys[-1] == seat[0].key              # a tree is no state until its commit
        co.commit([0, 2])
        keys.append(co.key)
        assert co.sync() == len(PROMPT) + 3 and seat[0].key == co.key
        co.verify([4], [-1])
        co.commit([])
        assert co.sync() == len(PROMPT) + 3 and seat[0].key == co.key == keys[-1]
        assert len(set(keys)) == 4 and all(len(k) == S.KEY_BYTES for k in keys)
        # the key names the kind of block, its shape and its path, not only the tokens
        b = S.Block([1, 2, 3], [-1, 0, 1])
        ks = {S.chain_key(keys[0], S.Block([1, 2, 3])), S.chain_key(keys[0], S.Block([1, 2, 3], graph=STEP)),
              S.chain_key(keys[0], b, [0, 1, 2]), S.chain_key(keys[0], b, [0, 1]),
              S.chain_key(keys[0], S.Block([1, 2, 3], [-1, 0, 0]), [0, 1]), S.chain_key(keys[1], b, [0, 1])}
        assert len(ks) == 6
        assert S.root_key(b"a") != S.root_key(b"b")
        assert co.rebuilds == co.replays == 0
    finally:
        x.close()
    return "4 states, 4 keys, equal on both stages; kind, shape, path and history each change the key"


def test_snapshot_restore_and_drop_cross_the_link():
    want = play(Whole())
    w = Whole()
    play(w)
    want_more = more(w)
    x = Pair()
    try:
        co, tail, front = x.co, x.tail, x.front
        assert play(co) == want
        key = co.mark()
        assert key == co.key and key in front.kept and co.stats["snapshots"] == 1
        assert co.mark() == key and co.stats["snapshots"] == 1          # sent once
        held = (dict(tail.acc), list(tail.cache), tail.n)               # the tail's caches keep its half
        assert more(co) == want_more
        other = co.mark()
        # back to the kept state: the tail's half by its caches, the front's by the key
        tail.acc, tail.cache, tail.n, tail.pend = dict(held[0]), list(held[1]), held[2], None
        assert co.restore(key, held[2]) == "hit" and co.key == key and co.rebuilds == 0
        assert co.probe()["length"] == held[2] == front.n
        assert more(co) == want_more and co.key == other                # the same sequence, the same key
        # a dropped key is a miss; with the log it is rebuilt
        co.drop([key])
        assert key not in front.kept and key not in co.marks
        tail.acc, tail.cache, tail.n, tail.pend = dict(held[0]), list(held[1]), held[2], None
        e = raises(S.StageError, lambda: co.restore(key, held[2]))
        assert e.code == "snapshot" and co.key == other                 # nothing changed
        tail.acc = {li: v for li, v in w.ad.acc.items() if li >= 2}
        tail.cache, tail.n, tail.pend = list(w.ad.cache), w.ad.n, None
        assert co.restore(other, w.ad.n) == "hit" and co.key == other
        # a wrong length is refused before anything is asked
        e = raises(S.StageError, lambda: co.restore(other, 3))
        assert e.code == "snapshot"
    finally:
        x.close()
    return "a kept state continues with the whole model's answers; a dropped key is a miss; a wrong length is refused"


def test_a_miss_is_rebuilt_on_the_front_alone_block_by_block():
    want = play(Whole())
    w = Whole()
    play(w)
    want_more = more(w)
    x = Pair()
    try:
        co, tail, front = x.co, x.tail, x.front
        co.prefill(PROMPT[:4])
        early = co.mark()                                               # a key the front keeps
        co.reset()
        assert play(co) == want
        key = co.mark()
        held = (dict(tail.acc), list(tail.cache), tail.n)
        first = list(front.ran)
        assert more(co) == want_more
        front.kept.pop(key)                                             # the front lost it (its budget)
        tail.acc, tail.cache, tail.n, tail.pend = dict(held[0]), list(held[1]), held[2], None
        ran_tail, n0 = len(tail.ran), len(front.ran)
        assert co.restore(key, held[2]) == "rebuilt" and co.rebuilds == 1 and co.replays == 0
        assert len(tail.ran) == ran_tail, "the tail ran rows again"
        # from an empty sequence (the kept key is another sequence's): every block as the kind it was
        again = front.ran[n0:]
        kinds = [k for k, _ in again]
        assert kinds == [1, 2, 2, 3, 3, 1], kinds
        assert again == [r for r in first[-8:] if r[1] != [4, 5]][-6:]   # the tree kept to nothing is not sent
        assert front.n == held[2] and x.follower.seats[0].key == key
        assert more(co) == want_more
        assert co.stats["quiet_steps"] == 6
        # the nearest kept key is used: only the blocks after it are sent again
        co.reset()
        co.prefill(PROMPT[:4])
        assert co.key == early
        co.prefill([70, 71])
        late = co.mark()
        h2 = (dict(tail.acc), list(tail.cache), tail.n)
        co.prefill([72])
        front.kept.pop(late)
        tail.acc, tail.cache, tail.n, tail.pend = dict(h2[0]), list(h2[1]), h2[2], None
        n0 = len(front.ran)
        assert co.restore(late, 6) == "rebuilt" and front.ran[n0:] == [(1, [70, 71])]
        assert early in front.kept
    finally:
        x.close()
    return "6 blocks sent again as chunk, step, step, tree, tree, chunk, no carry; the tail ran none; then 1 block from a kept key"


def test_a_front_with_other_rows_is_found_by_its_key():
    want = play(Whole())
    w = Whole()
    play(w)
    want_more = more(w)
    x = Pair()
    try:
        co, front = x.co, x.front
        assert play(co) == want
        co.sync()
        # the same length, another state: a length check alone would run the block on it
        front.acc[0] += 1
        x.follower.seats[0].key = bytes(16)
        assert more(co) == want_more and co.rebuilds == 1 and co.replays == 0
        assert x.follower.seats[0].key == co.key
        ref = Whole()
        play(ref)
        more(ref)
        assert {k: v for k, v in ref.ad.state().items() if k in ("acc.0", "acc.1")} == \
            {k: v for k, v in front.state().items() if k != "cache"}
    finally:
        x.close()
    return "a front at the same length with another key: desync, rebuilt from the log, the whole model's answers"


def test_a_state_restored_after_a_new_epoch_is_never_anothers():
    want = play(Whole())
    w = Whole()
    play(w)
    want_more = more(w)
    x = Pair()
    try:
        co, tail = x.co, x.tail
        assert play(co) == want
        key = co.mark()
        held = (dict(tail.acc), list(tail.cache), tail.n)

        def back():
            tail.acc, tail.cache, tail.n, tail.pend = dict(held[0]), list(held[1]), held[2], None
        # the link is cut and comes back: the same front process, a new epoch, both sequences empty
        x.cut()
        raises(S.StageDown, lambda: co.prefill([1], graph=STEP))
        assert co.mark() == co.key                                      # on a dead link: the key, nothing sent
        x.connect()
        assert co.epoch == 2 and co.length == 0 and x.front.n == 0 and co.key == S.root_key()
        assert key in x.front.kept                                      # what it kept under a key stays
        back()
        assert co.restore(key, held[2]) == "hit" and more(co) == want_more
        # the front process is gone and a new one starts: it holds nothing; the state is rebuilt
        x.kill_front()
        raises(S.StageDown, lambda: co.sync())
        x.new_front()
        x.connect()
        assert co.epoch == 3 and x.front.kept == {} and x.front.n == 0
        back()
        assert co.restore(key, held[2]) == "rebuilt" and more(co) == want_more
        assert x.follower.seats[0].key == co.key
        # a front that holds another state's section under the key (another length): not used
        co.reset()
        assert play(co) == want and co.key == key
        x.front.kept[key] = ({0: 1, 1: 2}, [1, 2, 3], 3, False)
        co.prefill([99])
        back()
        assert co.restore(key, held[2]) == "rebuilt" and more(co) == want_more
        assert co.replays == 0
    finally:
        x.close()
    return "epoch 2, same front: its own kept state; epoch 3, a new front: rebuilt; a section of another length: not used"


def test_a_tail_that_keeps_its_sequence_brings_the_front_back():
    want = play(Whole())
    w = Whole()
    play(w)
    want_more = more(w)
    for marked, fresh in ((True, False), (False, False), (False, True)):
        x = Pair()
        try:
            co = x.co
            co.keep = True
            assert play(co) == want
            if marked:
                co.mark()
            co.verify([4, 4], [-1, 0])                                   # a tree waits for its commit
            n, key = co.length, co.key
            if fresh:
                x.kill_front()
                x.new_front()
            else:
                x.cut()
            x.connect()
            assert co.epoch == 2 and co.length == n and co.key == key and x.front.n == 0
            ran = len(x.tail.ran)
            co.commit([])                                                # this stage's own block, still its own
            assert more(co) == want_more and co.rebuilds == 1 and co.replays == 0
            assert len(x.tail.ran) == ran + 3, "the tail ran its sequence again"
            assert co.stats["quiet_steps"] == (0 if marked else 6)       # its own kept state, or the log
            assert x.follower.seats[0].key == co.key
        finally:
            x.close()
    return "a new link under a kept sequence: the front comes back from its kept state, or block by block from the log"


def test_the_front_keeps_a_state_only_under_its_own_key():
    x = Pair()
    try:
        co, front = x.co, x.front
        co.prefill(PROMPT)
        # a snapshot header that names another key than the front computed: desync, nothing kept
        e = raises(S.StageError, lambda: co._ask(
            S.pack_step(S.OP_SNAPSHOT, len(PROMPT), key=bytes(16)), 5))
        assert e.code == "desync" and front.kept == {}
        raises(ValueError, lambda: S.pack_step(S.OP_SNAPSHOT, 1, key=b"short"))
        with co.link.lock:
            co.link.send(L.STEP, S.pack_step(S.OP_SNAPSHOT, len(PROMPT))[:-1] + b"\0", seq=77)
            assert co.link.recv(5).json()["code"] == "header"            # no key
        assert co.mark() == co.key and list(front.kept) == [co.key]
    finally:
        x.close()
    return "a snapshot under a key the front did not compute: desync, nothing kept"


def test_a_state_nobody_has_the_log_of_is_refused():
    x = Pair()
    try:
        co, front, tail = x.co, x.front, x.tail
        co.prefill(PROMPT)
        key = co.mark()
        held = (dict(tail.acc), list(tail.cache), tail.n)
        before = co.key
        # a key from a file of an earlier process: the front holds it
        stranger = S.Coordinator(Toy(2, 4))
        assert stranger.key == S.root_key()
        co.marks.clear()                                                 # as after a restart of the tail
        co.prefill([1], graph=STEP)
        tail.acc, tail.cache, tail.n, tail.pend = dict(held[0]), list(held[1]), held[2], None
        assert co.restore(key, len(PROMPT)) == "hit" and co.key == key
        ref = Whole()
        ref.prefill(PROMPT)
        assert co.prefill([11], graph=STEP) == ref.prefill([11], graph=STEP)
        # the front does not hold it and there is no log: refused, and nothing moved
        co.marks.clear()
        front.kept.clear()
        at = (co.key, co.length, front.n)
        e = raises(S.StageError, lambda: co.restore(key, len(PROMPT)))
        assert e.code == "snapshot" and (co.key, co.length, front.n) == at
        assert before == key
    finally:
        x.close()
    return "a key without a log: used when the front holds it, refused when it does not"


def test_a_joint_round_is_one_request_and_gives_each_slots_answers():
    # each slot alone, in one process
    alone = []
    for s in range(3):
        w = Whole()
        w.prefill([10 + s] + PROMPT[s:])
        a = w.verify([7 + s, 19, 23], [-1, 0, 0])
        w.commit([0, 2] if s != 1 else [0])
        b = w.verify([3, 4 + s], [-1, 0])
        w.commit([0, 1])
        alone.append((a, b, w.prefill([90 + s], graph=STEP)))
    x = Pair(slots=3)
    try:
        co = x.co
        cos = [co] + [co.side(x.tail.slot(i), i) for i in (1, 2)]
        raises(ValueError, lambda: co.side(x.tail, 0))
        for s, c in enumerate(cos):
            c.prefill([10 + s] + PROMPT[s:])
        carries = co.link.stats.rx_payload[L.CARRY]
        n0 = x.follower.stats["rounds"]
        outs = co.verify_round([(c, [7 + s, 19, 23], [-1, 0, 0]) for s, c in enumerate(cos)])
        assert outs == [a for a, _, _ in alone]
        assert co.link.stats.rx_payload[L.CARRY] - carries == 9 * x.front.carry_bytes_per_row    # one carry, 9 rows
        for s, c in enumerate(cos):
            c.commit([0, 2] if s != 1 else [0])
        # the commits ride in the next round's headers; one slot lost its rows meanwhile
        x.front.slot(1).acc[0] += 5
        x.follower.seats[1].key = bytes(16)
        outs = co.verify_round([(c, [3, 4 + s], [-1, 0]) for s, c in enumerate(cos)])
        assert outs == [b for _, b, _ in alone]
        assert cos[1].rebuilds == 1 and cos[0].rebuilds == cos[2].rebuilds == 0
        for c in cos:
            c.commit([0, 1])
        assert [c.prefill([90 + s], graph=STEP) for s, c in enumerate(cos)] == [c_ for _, _, c_ in alone]
        assert x.follower.stats["rounds"] - n0 == 2 and co.stats["rounds"] == 2
        assert (co.stats["round_blocks"], co.stats["round_max"], co.stats["trees"]) == (6, 3, 0)
        assert sum(c.stats["chunks"] for c in cos) == 3
        assert [x.follower.seats[i].key for i in range(3)] == [c.key for c in cos]
        assert len({c.key for c in cos}) == 3
        # a round takes one block a slot; a chain block is no round's
        raises(ValueError, lambda: co.verify_round([(co, [1], [-1]), (co, [2], [-1])]))
        # a slot the front does not have
        e = raises(S.StageError, lambda: co._ask(S.pack_step(S.OP_SYNC, 0, slot=7), 5))
        assert e.code == "block"
        # each slot keeps and restores its own state
        k1 = cos[1].mark()
        assert k1 in x.front.slot(1).kept and k1 not in x.front.kept
        assert co.report()["rounds"] == 2 and co.report()["marks"] == 1
    finally:
        x.close()
    return "3 slots: 2 rounds, one request and one carry each, each slot's answers its own alone; a slot rebuilt inside a round"


def test_the_header_parses_back():
    key, k2 = bytes(range(16)), bytes(range(16, 32))
    b = S.Block([1, 2, 3], [-1, 0, 0], slot=3, graph=2)
    st = S.unpack(S.pack_step(S.OP_STEP, 9, b, [0, 2], key=key, quiet=True))
    assert (st.op, st.length, st.slot, st.commit, st.key, st.quiet, st.keep) == (S.OP_STEP, 9, 3, [0, 2], key, True, False)
    assert st.block.tokens == [1, 2, 3] and st.block.parents == [-1, 0, 0] and st.block.graph == 2
    st = S.unpack(S.pack_step(S.OP_SNAPSHOT, 4, commit=[0], key=key, keep=True, slot=2))
    assert (st.op, st.slot, st.commit, st.key, st.keep, st.block) == (S.OP_SNAPSHOT, 2, [0], key, True, None)
    st = S.unpack(S.pack_step(S.OP_DROP, 0, keys=[key, k2], slot=1))
    assert st.keys == [key, k2] and st.slot == 1
    st = S.unpack(S.pack_step(S.OP_RESTORE, 12, key=k2))
    assert (st.op, st.length, st.key) == (S.OP_RESTORE, 12, k2)
    # the first version's header: the same bytes, the same reading
    old = S.pack_step(S.OP_STEP, 5, S.Block([4, 5]), [0])
    op, length, block, path = S.unpack_step(old)
    assert len(old) == 18 + 8 + 2 and (op, length, block.tokens, block.parents, path) == (S.OP_STEP, 5, [4, 5], None, [0])
    assert S.unpack(old).key is None and not S.unpack(old).quiet
    for bad in (old + b"\0", S.pack_step(S.OP_RESTORE, 1), S.pack_step(S.OP_SYNC, 1) + key,
                S.pack_step(S.OP_DROP, 0) + b"123"):
        raises(ValueError, lambda bad=bad: S.unpack(bad))
    heads = [S.pack_step(S.OP_STEP, 1, S.Block([1], [-1], slot=i), key=key) for i in range(3)]
    assert S.unpack_round(S.pack_round(heads)) == heads
    raises(ValueError, lambda: S.unpack_round(S.pack_round(heads)[:-1]))
    raises(ValueError, lambda: S.unpack_round(heads[0]))
    return "keys, quiet, keep, a drop's keys and a round parse back; a first-version header is unchanged"


def test_the_coordinator_keeps_a_bounded_log_of_keys():
    x = Pair()
    cap, S.MARKS = S.MARKS, 4
    try:
        co, front = x.co, x.front
        keys = []
        for t in range(7):
            co.prefill([t + 1], graph=STEP) if t else co.prefill([1, 2])
            keys.append(co.mark())
        assert list(co.marks) == keys[-4:] and sorted(front.kept) == sorted(keys[-4:])
        assert co.stats["drops"] == 3
    finally:
        S.MARKS = cap
        x.close()
    return "7 keys under a cap of 4: the 3 oldest dropped on both stages"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
