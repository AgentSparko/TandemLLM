"""ENG-424: Flash-Next as two stages with the server's caches on and several slots
(engine/models/flashnext/tail.py `FlashNextSplitCached`, stages.py, engine/methods/stage_store.py),
no GPU.

The engine's kernels are CUDA: none runs here. The real classes run on a small pack in the served
layout with host stand-ins for the launches (tests/fn_host.py), the caches are engine/cache.py and
engine/disk_cache.py as they are, and the two stages talk over a real link on loopback.

Every case runs the same turns three ways: the one engine with the same caches, the split with
them, and (where the cache's own contract is bit equality with a cold prefill) the split cold. The
logits of the prompt's last row and of every decoded row are compared with `torch.equal`.

  -  the resident prefix (an anchor on the grid), the end of the last generation (the session), a
     guest over a resident conversation (the stash), the state store (the prefix cache) and the
     disk tier each resume the split where they resume the one engine, with its logits
  -  the front keeps its half under the tail's key: its rows stay in place until a pass writes
     over them, are copied out then and copied back by the restore
  -  the disk tier a side: after both stages start again the tail reads its files and the front
     its own, under the same key; without the front's file the tier's entry is a miss and is given
     up, and the prompt runs cold with the same logits
  -  a front that starts again in the middle of a conversation is rebuilt from the tail's log,
     each block as the kind it was: the next turn's logits and the front's sections and rows are
     the ones of a front that never stopped; the same front after a cut link serves its own kept
     half
  -  a front under its budget: a state gives up its sections and stays a link of its chain while
     its rows are in place; rows it had no room to copy out are a miss, and the front is rebuilt
  -  several slots: a sequence each on both stages over one copy of the weights; a joint round is
     one request and one carry, and each slot's logits, picks, streams and state are the one
     engine's for that slot. The one engine's own joint pass (`verify_multi`) needs the CUDA build
     and does not run on this host, so its slots verify one after the other here; the engine
     gives a row of a joint pass its lone bits, and the GPU proof compares the batches
  -  both stages as the server starts them with the caches on: the seed in both stages' keys, a
     cached turn, a side slot with the pack's own drafter, a joint round
  -  the options and the store's settings from the server's flags
  -  the cut-cost tool times its uncut arm a second time each run and says whether its baseline
     is clean

Run: python tests/test_fn_split_state.py
"""

from __future__ import annotations

import os
import secrets
import sys
import tempfile
import threading
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

try:
    import torch
except ImportError:
    torch = None

import test_fn_stage as T  # noqa: E402

CH = 8
BIG = 1 << 30
CAP = T.CAP
A1 = T.PROMPT + [31, 32, 33, 34, 35, 36, 37, 38, 39]                # 28 tokens: lines at 8, 16, 24
A2 = [61, 62, 63, 64, 65]                                           # the user's next message
B1 = [90, 91, 92, 93, 94]                                           # another client's short prompt


def _raises(kind, fn, *needles):
    return T._raises(kind, fn, *needles)


class Split:
    """The tail's serving with the caches on and the front's follower over loopback. A front can
    stop and start again (a new adapter and a new store: a new process), a link can be cut."""

    def __init__(self, eng, k: int, store_bytes: int = BIG, root: str | None = None, slots: int = 1,
                 tail_cls=None):
        from engine.methods import link as L
        from engine.methods import stages as S
        from engine.models.flashnext import stages as ST
        from engine.models.flashnext.tail import FlashNextSplitCached
        self.L, self.S, self.ST = L, S, ST
        self.eng, self.k, self.slots = eng, k, slots
        self.store = {"bytes": store_bytes, "dir": root, "disk_bytes": 0, "name": "t"}
        self.cls = tail_cls or FlashNextSplitCached
        self.key = secrets.token_bytes(32)
        self.block = {"identity": 1, "code": "c0de", "weights": "w8", "family": "qwen4_exp",
                      "layers": eng.cfg.num_hidden_layers}
        self.listeners, self.threads = [], []
        self.new_tail()
        self.new_front()
        self.connect()

    def new_tail(self) -> None:
        layers = self.eng.cfg.num_hidden_layers
        self.tail = self.ST.stage_adapter(self.eng, self.k, layers, cap=CAP, graphs=True, serving=self.cls,
                                          slots=self.slots)
        self.co = self.S.Coordinator(self.tail, step_deadline=60.0, chunk_deadline=60.0)
        self.srv = self.tail.sv.attach(self.co)

    def new_front(self) -> None:
        S, L = self.S, self.L
        self.front = self.ST.stage_adapter(self.eng, 0, self.k, cap=CAP, graphs=True, slots=self.slots,
                                           store=dict(self.store))
        self.follower = S.Follower(self.front, idle_deadline=3.0)
        self.listener = L.Listener("127.0.0.1:0")
        self.listeners.append(self.listener)
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
        self.threads.append(t)

    def connect(self):
        end = time.monotonic() + 20
        while True:
            try:
                link = self.S.open_link(self.key, self.S.stage_identity(self.block, self.tail),
                                        peer=f"127.0.0.1:{self.listener.port}", last_epoch=self.co.epoch)
                break
            except self.L.LinkError:
                if time.monotonic() > end:
                    raise
                time.sleep(0.05)
        self.co.attach(link)
        return link

    def restart_front(self) -> None:
        self.co.link.close()
        self.listener.close()
        self.new_front()
        self.connect()

    def cut(self) -> None:
        self.co.link.close()
        time.sleep(0.2)
        self.connect()

    def close(self) -> None:
        """Every follower thread ended before the next case (and before the process ends: a thread
        still in a kernel stand-in then takes the interpreter down)."""
        if self.co.link is not None:
            self.co.link.close()
        for lis in self.listeners:
            lis.close()
        for t in self.threads:
            t.join(15)
        assert not any(t.is_alive() for t in self.threads), "a follower thread did not end"


def one(eng):
    from engine.models.flashnext.serving import FlashNextServing
    return FlashNextServing(eng, CAP)


def turn(srv, ids, n_new: int = 6, **caches):
    """One request as the server runs it: the prompt through engine/cache.py's prefill, then
    `n_new` rounds: decode steps and, every third round, a draft tree whose verified rows are kept.
    Returns (every logits row it read, the context after it, the prefill's info, rows reused)."""
    from engine import cache
    info: dict = {}
    lg, reused, _ = cache.prefill(srv, None, list(ids), "cpu", chunk=CH, info=info, **caches)
    out = [lg.view(-1).clone()]
    toks, n = list(ids), len(ids)
    t = int(lg.view(-1).argmax())
    for i in range(n_new):
        if i % 3 == 2:
            block = [t, (t * 7 + 1) % 250 + 1, (t * 3 + 2) % 250 + 1, (t * 5 + 3) % 250 + 1]
            lgs = srv.forward_tree(torch.tensor(block), [-1, 0, 0, 2], n)
            out += [x.clone() for x in lgs]
            pick = int(lgs[0].argmax())
            path = [0] + ([1] if block[1] == pick else [2, 3] if i % 2 == 0 else [2])
            srv.commit_tree(path)
            toks += [block[j] for j in path]
            n += len(path)
            t = int(lgs[path[-1]].argmax())
        else:
            lg = srv.forward(torch.tensor([t]), n)
            out.append(lg.view(-1).clone())
            toks.append(t)
            n += 1
            t = int(lg.view(-1).argmax())
    assert srv.kv.length == n == len(toks)
    return out, toks, info, reused


def same_rows(got, want, what) -> None:
    assert len(got) == len(want), (what, len(got), len(want))
    for i, (a, b) in enumerate(zip(got, want)):
        T.same(a, b, f"{what}, row {i}")


def both_states(x) -> dict:
    x.co.sync()
    return {**x.front.state(), **x.tail.state()}


def whole_state(srv) -> dict:
    return T.state_of(srv.fn, srv.st)


def same_state(x, srv, what) -> None:
    got, want = both_states(x), whole_state(srv)
    assert set(got) == set(want), what
    for name, t in want.items():
        T.same(got[name], t, f"{what}: {name}")


def resident(end: bool = False):
    from engine import cache
    return cache.ResidentPrefix(BIG, CH, stash_bytes=BIG, end=end, guest_max=64, guest_frac=0.5)


# ------------------------------------------------------------------ the caches in memory
def test_the_resident_prefix_resumes_both_stages(monkeypatch):
    H, cuda = T.setup(monkeypatch)
    eng = T.reference(H)["eng"]
    turn2 = A1[:20] + A2 + [70, 71, 72]                       # parts from turn 1 after its line at 16
    s1, r1 = one(eng), resident()
    w1 = turn(s1, A1, resident=r1)
    w2 = turn(s1, turn2, resident=r1)
    assert (w2[2]["kind"], w2[3]) == ("resident", 16)
    cold = turn(one(eng), turn2)
    same_rows(w2[0], cold[0], "the one engine, resident against cold")
    for k in (1, 3, 5):
        x = Split(eng, k)
        try:
            res = resident()
            g1 = turn(x.srv, A1, resident=res)
            same_rows(g1[0], w1[0], f"cut {k}, turn 1")
            keys = dict(x.front.store.entries)
            assert len(keys) >= 3 and x.co.stats["snapshots"] >= 3            # the lines at 8, 16, 24
            g2 = turn(x.srv, turn2, resident=res)
            assert (g2[2]["kind"], g2[3]) == ("resident", 16) and g2[1] == w2[1]
            same_rows(g2[0], w2[0], f"cut {k}, turn 2 from the anchor at 16")
            assert x.srv.restores == {"hit": 1, "rebuilt": 0, "refused": 0} and x.co.rebuilds == 0
            assert x.front.store.stats["copied_back"] == 0, "the rows below the anchor were in place"
            same_state(x, s1, f"cut {k} after turn 2")
            y = Split(eng, k)
            try:
                same_rows(turn(y.srv, turn2)[0], g2[0], f"cut {k}, the split cold")
            finally:
                y.close()
        finally:
            x.close()
    return "cuts 1, 3, 5: turn 2 resumed at the anchor at 16 on both stages; logits and state the one engine's and a cold split's"


def test_the_end_of_a_generation_resumes_both_stages(monkeypatch):
    H, cuda = T.setup(monkeypatch)
    eng = T.reference(H)["eng"]
    s1, r1 = one(eng), resident(end=True)
    w1 = turn(s1, A1, resident=r1)
    assert r1.keep_end(s1, None, w1[1], len(A1)) == "kept"
    w2 = turn(s1, w1[1] + A2, resident=r1)
    assert (w2[2]["kind"], w2[3]) == ("end", len(w1[1]))
    for k in (2, 4):
        x = Split(eng, k)
        try:
            res = resident(end=True)
            g1 = turn(x.srv, A1, resident=res)
            assert res.keep_end(x.srv, None, g1[1], len(A1)) == "kept" and g1[1] == w1[1]
            end_key = x.co.key
            assert end_key in x.front.store.entries and x.front.store.entries[end_key].length == len(g1[1])
            # another request writes over the rows above the conversation's end before it comes back
            g2 = turn(x.srv, g1[1] + A2, resident=res)
            assert (g2[2]["kind"], g2[3]) == ("end", len(g1[1])) and g2[1] == w2[1]
            same_rows(g2[0], w2[0], f"cut {k}, turn 2 from the end of turn 1")
            assert x.srv.restores["hit"] == 1 and x.co.rebuilds == 0
            same_state(x, s1, f"cut {k} after turn 2")
        finally:
            x.close()
    return "cuts 2, 4: turn 2 resumed at the end of turn 1's generation (decode steps and trees) with the one engine's logits"


def test_a_guest_over_a_resident_conversation(monkeypatch):
    H, cuda = T.setup(monkeypatch)
    eng = T.reference(H)["eng"]
    s1, r1 = one(eng), resident(end=True)
    w1 = turn(s1, A1, resident=r1)
    r1.keep_end(s1, None, w1[1], len(A1))
    wb = turn(s1, B1, n_new=3, resident=r1)
    assert r1._stash is not None, "the short prompt was no guest"
    w2 = turn(s1, w1[1] + A2, resident=r1)
    assert w2[2]["kind"] == "end"
    x = Split(eng, 3)
    try:
        res = resident(end=True)
        g1 = turn(x.srv, A1, resident=res)
        res.keep_end(x.srv, None, g1[1], len(A1))
        st = x.front.store
        gb = turn(x.srv, B1, n_new=3, resident=res)
        same_rows(gb[0], wb[0], "the guest")
        assert res._stash is not None and st.stats["copied_out"] >= 1, st.stats
        g2 = turn(x.srv, g1[1] + A2, resident=res)
        assert (g2[2]["kind"], g2[3]) == ("end", len(g1[1]))
        same_rows(g2[0], w2[0], "the conversation after the guest")
        assert st.stats["copied_back"] >= 1 and x.co.rebuilds == 0, (st.stats, x.co.rebuilds)
        same_state(x, s1, "after the guest")
    finally:
        x.close()
    return "cut 3: a guest wrote over the conversation's first rows; the front copied its segment out and back"


def test_the_state_store_restores_both_stages(monkeypatch):
    H, cuda = T.setup(monkeypatch)
    from engine import cache
    eng = T.reference(H)["eng"]
    turn2 = A1[:17] + A2 + [70, 71]
    cold = turn(one(eng), turn2)
    s1, st1 = one(eng), cache.StateStore(BIG, chunk=CH)
    turn(s1, A1, store=st1, checkpoint=True)
    turn(s1, B1 + B1, n_new=2, store=st1, checkpoint=True)
    w2 = turn(s1, turn2, store=st1, checkpoint=True)
    assert (w2[2]["kind"], w2[3]) == ("prefix", 16)
    same_rows(w2[0], cold[0], "the one engine, the store against cold")
    for k in (1, 4):
        x = Split(eng, k)
        try:
            store = cache.StateStore(BIG, chunk=CH)
            turn(x.srv, A1, store=store, checkpoint=True)
            turn(x.srv, B1 + B1, n_new=2, store=store, checkpoint=True)      # another prompt from row 0
            g2 = turn(x.srv, turn2, store=store, checkpoint=True)
            assert (g2[2]["kind"], g2[3]) == ("prefix", 16)
            same_rows(g2[0], cold[0], f"cut {k}, the store's snapshot at 16")
            fs = x.front.store.stats
            assert x.srv.restores["hit"] == 1 and x.co.rebuilds == 0 and fs["copied_back"] >= 1, fs
            same_state(x, s1, f"cut {k} after the store's restore")
        finally:
            x.close()
    return "cuts 1, 4: a snapshot at 16 restored after another prompt: the tail's rows by the store, the front's from its own copy"


# ------------------------------------------------------------------ the disk tier, a side
def _tier(d, wrap: bool):
    from disk_tier_util import waiting
    from engine import disk_cache
    from server import stage
    t = waiting(disk_cache.DiskTier(d, chunk=CH, fingerprint="fn-stage", settings={"admit_after": 1, "budget_gb": 1},
                                    free_bytes=lambda p: 10 ** 15, head_anchors=2, min_tokens=CH).open())
    return stage.StageDisk(t) if wrap else t


def test_the_disk_tier_a_side(monkeypatch):
    H, cuda = T.setup(monkeypatch)
    eng = T.reference(H)["eng"]
    # 34 rows: chunks of 8, 8, 8, 8, 2. A last chunk of one row is no case of this test: the one
    # engine's layer-major window runs it on the prefill kernels, a chunk-major forward (the
    # split's, and the one engine's own without a window) as a decode step
    prompt = A1 + A2 + [70]
    cold = turn(one(eng), prompt)
    with tempfile.TemporaryDirectory() as d1:
        t = _tier(d1, False)
        turn(one(eng), A1, disk=t)
        assert t.flush()
        t.close()
        t = _tier(d1, False)
        w = turn(one(eng), prompt, disk=t)
        t.close()
        assert w[2]["kind"] == "disk" and w[3] == 24, (w[2], w[3])
        same_rows(w[0], cold[0], "the one engine, the disk against cold")
    for k in (2, 5):
        with tempfile.TemporaryDirectory() as dt, tempfile.TemporaryDirectory() as df:
            x = Split(eng, k, root=df)
            try:
                t = _tier(dt, True)
                turn(x.srv, A1, disk=t)
                assert t.flush()
                t.close()
                files = sorted(os.listdir(os.path.join(df, "t")))
                assert files and all(f.endswith(".seg") for f in files), files
                assert x.front.store.stats["files_written"] == len(files)
                kept = x.co.report()
            finally:
                x.close()
            # both stages start again: nothing in memory on either side
            x = Split(eng, k, root=df)
            try:
                t = _tier(dt, True)
                g = turn(x.srv, prompt, disk=t)
                assert (g[2]["kind"], g[3]) == ("disk", 24), (g[2], g[3])
                same_rows(g[0], cold[0], f"cut {k}, both halves from disk")
                assert x.srv.restores == {"hit": 1, "rebuilt": 0, "refused": 0}
                assert x.front.store.stats["files_read"] == len(files) and x.co.rebuilds == 0
                assert kept["snapshots"] >= 1
                srv = one(eng)
                turn(srv, prompt)
                same_state(x, srv, f"cut {k} after the disk's restore")
                t.close()
            finally:
                x.close()
            # the front's files are gone: the tier's entry is a miss, given up, and the prompt runs cold
            for f in os.listdir(os.path.join(df, "t")):
                os.unlink(os.path.join(df, "t", f))
            x = Split(eng, k, root=df)
            try:
                t = _tier(dt, True)
                g = turn(x.srv, prompt, disk=t)
                assert g[2]["kind"] is None and g[3] == 0, (g[2], g[3])
                same_rows(g[0], cold[0], f"cut {k}, the front's half missing")
                assert x.srv.restores["refused"] == 1 and x.co.rebuilds == 0
                assert t.stats["failed_restores"] == 1
                t.close()
            finally:
                x.close()
    return "cuts 2, 5: both halves read back under one key after a restart; without the front's files a miss, then cold"


def test_a_file_that_is_damaged_or_of_another_layout_is_a_miss(monkeypatch):
    H, cuda = T.setup(monkeypatch)
    eng = T.reference(H)["eng"]
    with tempfile.TemporaryDirectory() as df:
        x = Split(eng, 3, root=df)
        try:
            x.srv.forward(torch.tensor(A1[:8]), 0)
            x.srv.forward(torch.tensor(A1[8:16]), 8)
            x.srv.disk_keep()
            key = x.co.key
            st = x.front.store
            path = st._path(key)
            assert os.path.exists(path) and st.entries[key].on_disk and st.stats["files_written"] == 1
            assert not [f for f in os.listdir(st.dir) if f.endswith(".tmp")]
            x.srv.forward(torch.tensor([5]), 16)
            st.drop([key])
            assert not x.front.restore(key, 15), "another length"
            assert x.front.restore(key, 16) and st.stats["files_read"] == 1 and x.front.length == 16
            # one flipped byte: the checksum refuses it and the file goes
            st.drop([key])
            x.front.reset()
            raw = bytearray(open(path, "rb").read())
            raw[-3] ^= 1
            open(path, "wb").write(raw)
            assert not x.front.restore(key, 16) and st.stats["files_bad"] == 1 and not os.path.exists(path)
            assert x.front.length == 0
        finally:
            x.close()
        # a stage of another cut reads none of them
        y = Split(eng, 4, root=df)
        try:
            y.srv.forward(torch.tensor(A1[:8]), 0)
            y.srv.disk_keep()
            other = y.front.store._path(y.co.key)
        finally:
            y.close()
        z = Split(eng, 2, root=df)
        try:
            assert os.path.exists(other) and not z.front.restore(y.co.key, 8)
            assert os.path.exists(other) and z.front.store.stats["files_bad"] == 0
        finally:
            z.close()
    return "a file read back at its length only; one flipped byte: refused and removed; another cut's file: left alone"


# ------------------------------------------------------------------ a front that lost its state
def test_a_front_that_starts_again_is_rebuilt_from_the_log(monkeypatch):
    H, cuda = T.setup(monkeypatch)
    eng = T.reference(H)["eng"]
    s1, r1 = one(eng), resident(end=True)
    w1 = turn(s1, A1, resident=r1)
    r1.keep_end(s1, None, w1[1], len(A1))
    w2 = turn(s1, w1[1] + A2, resident=r1)
    r1.keep_end(s1, None, w2[1], len(w1[1]) + len(A2))
    w3 = turn(s1, w2[1] + [80, 81], n_new=3, resident=r1)
    for k in (1, 3, 5):
        x = Split(eng, k)
        try:
            res = resident(end=True)
            g1 = turn(x.srv, A1, resident=res)
            res.keep_end(x.srv, None, g1[1], len(A1))
            # the front process stops and a new one starts: it holds nothing; the tail keeps its rows
            x.restart_front()
            assert x.co.epoch == 2 and x.front.length == 0 and not x.front.store.entries
            assert x.tail.length == len(g1[1]) == x.srv.kv.length
            g2 = turn(x.srv, g1[1] + A2, resident=res)
            assert (g2[2]["kind"], g2[3]) == ("end", len(g1[1]))
            same_rows(g2[0], w2[0], f"cut {k}, turn 2 after the front started again")
            # the first thing that names the state finds the front elsewhere and rebuilds it from the log
            quiet = x.co.stats["quiet_steps"]
            assert x.co.rebuilds == 1 and x.co.replays == 0 and quiet >= 6 and x.srv.restores["refused"] == 0
            same_state(x, _replayed(eng, [(A1, 6), (g1[1] + A2, 6)]), f"cut {k}: the rebuilt front")
            res.keep_end(x.srv, None, g2[1], len(g1[1]) + len(A2))
            # the link is cut and comes back to the same front: it serves its own kept half
            x.cut()
            assert x.co.epoch == 3 and x.front.length == 0 and x.tail.length == len(g2[1])
            g3 = turn(x.srv, g2[1] + [80, 81], n_new=3, resident=res)
            assert g3[2]["kind"] == "end"
            same_rows(g3[0], w3[0], f"cut {k}, turn 3 after a cut link")
            assert x.co.stats["quiet_steps"] == quiet, "blocks were sent again to a front that kept the state"
            assert x.front.store.stats["hits"] >= 1 and x.srv.restores["refused"] == 0 and x.co.replays == 0
        finally:
            x.close()
    return ("cuts 1, 3, 5: a new front under epoch 2 rebuilt from the log (chunks, steps, trees), turn 2's "
            "logits and both stages' state the one engine's; the same front under epoch 3: its own kept half, "
            "no block sent again")


def _replayed(eng, turns):
    """The one engine after the same turns, each resumed at the end of the one before."""
    srv, res = one(eng), resident(end=True)
    for ids, n_new in turns:
        out = turn(srv, ids, n_new=n_new, resident=res)
        res.keep_end(srv, None, out[1], len(ids))
    return srv


def test_a_link_that_drops_inside_a_generation(monkeypatch):
    H, cuda = T.setup(monkeypatch)
    from engine import cache
    eng = T.reference(H)["eng"]
    # the one engine runs the same calls: a request that stops after one decoded token, then again
    s1, r1 = one(eng), resident(end=True)
    w1 = turn(s1, A1, resident=r1)
    r1.keep_end(s1, None, w1[1], len(A1))
    n2 = len(w1[1]) + len(A2)
    cache.prefill(s1, None, w1[1] + A2, "cpu", chunk=CH, resident=r1)
    s1.forward(torch.tensor([7]), n2)
    w2 = turn(s1, w1[1] + A2, resident=r1)
    for new_front in (True, False):
        x = Split(eng, 3)
        try:
            S = x.S
            res = resident(end=True)
            g1 = turn(x.srv, A1, resident=res)
            res.keep_end(x.srv, None, g1[1], len(A1))
            cache.prefill(x.srv, None, g1[1] + A2, "cpu", chunk=CH, resident=res)
            x.srv.forward(torch.tensor([7]), n2)
            # the link goes down between two rounds: the request ends, this stage's rows stay
            x.co.link.close()
            _raises(S.StageDown, lambda: x.srv.forward(torch.tensor([9]), n2 + 1))
            _raises(S.StageDown, lambda: x.srv.forward_tree(torch.tensor([9, 8]), [-1, 0], n2 + 1))
            if new_front:
                x.restart_front()
            else:
                x.cut()
            assert x.co.epoch == 2 and x.srv.kv.length == n2 + 1 and x.front.length == 0
            # the client sends it again: the caches hold the conversation, the front is brought back
            g2 = turn(x.srv, g1[1] + A2, resident=res)
            assert (g2[2]["kind"], g2[3], g2[1]) == (w2[2]["kind"], w2[3], w2[1]), (g2[2], w2[2])
            same_rows(g2[0], w2[0], "the request again after the link came back")
            assert x.co.replays == 0 and x.srv.restores["refused"] == 0
            assert (x.co.rebuilds >= 1) == new_front or x.srv.restores["hit"] >= 1
            same_state(x, s1, "after the request ran again")
        finally:
            x.close()
    return ("a link down inside a generation: the request ends with stage_down and this stage's rows stay; the same "
            "request again gives the one engine's logits, with a new front (rebuilt) and with the same one")


def test_a_front_under_its_budget(monkeypatch):
    H, cuda = T.setup(monkeypatch)
    eng = T.reference(H)["eng"]
    s1, r1 = one(eng), resident(end=True)
    w1 = turn(s1, A1, resident=r1)
    r1.keep_end(s1, None, w1[1], len(A1))
    w2 = turn(s1, w1[1] + A2, resident=r1)
    r1.keep_end(s1, None, w2[1], len(w1[1]) + len(A2))
    turn(s1, B1, n_new=3, resident=r1)
    w3 = turn(s1, w2[1] + [80, 81], n_new=3, resident=r1)
    probe = Split(eng, 3)
    try:
        one_state = sum(t.numel() * t.element_size() for t in probe.front.sv.state.sections().values())
    finally:
        probe.close()
    x = Split(eng, 3, store_bytes=2 * one_state)             # two states' sections, no room for a row
    try:
        res = resident(end=True)
        g1 = turn(x.srv, A1, resident=res)
        res.keep_end(x.srv, None, g1[1], len(A1))
        st = x.front.store
        rep = st.report()
        # the older states gave up their sections and stay links of the chain: their rows are in place
        assert st.bytes <= st.budget and st.stats["evicted"] >= 2 and rep["links"] >= 2, rep
        assert sum(1 for e in st.entries.values() if e.sections is not None) <= 2
        g2 = turn(x.srv, g1[1] + A2, resident=res)
        assert (g2[2]["kind"], g2[3]) == ("end", len(g1[1]))
        same_rows(g2[0], w2[0], "the conversation's end, through links")
        assert x.srv.restores == {"hit": 1, "rebuilt": 0, "refused": 0} and x.co.rebuilds == 0
        res.keep_end(x.srv, None, g2[1], len(g1[1]) + len(A2))
        # a guest writes over the first rows: no room to copy them out, so the chain is given up
        turn(x.srv, B1, n_new=3, resident=res)
        g3 = turn(x.srv, g2[1] + [80, 81], n_new=3, resident=res)
        assert g3[2]["kind"] == "end"
        same_rows(g3[0], w3[0], "after the store gave the conversation's rows up")
        assert x.srv.restores["rebuilt"] == 1 and x.srv.restores["refused"] == 0 and st.bytes <= st.budget
        same_state(x, s1, "after the rebuild")
    finally:
        x.close()
    return ("a front store of two states' bytes: older states stay links while their rows are in place and the "
            "conversation's end restores through them; rows a guest wrote over are given up, a miss, rebuilt")


# ------------------------------------------------------------------ slots and joint rounds
def test_slots_and_joint_rounds_equal_the_one_engines(monkeypatch):
    H, cuda = T.setup(monkeypatch)
    from engine.models.flashnext.serving import FlashNextServing
    eng = T.reference(H)["eng"]
    prompts = [A1[:19], A1[3:25], B1 + A2 + [70, 71, 72, 73]]
    blocks = [([7, 19, 23, 44], [-1, 0, 1, 1], [0, 1, 3]), ([9, 13, 21], [-1, 0, 0], [0, 2]),
              ([5, 6, 8, 10, 12], [-1, 0, 1, 2, 2], [0, 1, 2, 4])]
    blocks2 = [([3, 4], [-1, 0], [0, 1]), ([8], [-1], [0]), ([11, 12, 13], [-1, 0, 0], [0])]

    def run(servs, multi, commit_multi):
        out = []
        for s, p in zip(servs, prompts):
            out.append(s.forward(torch.tensor(p), 0).view(-1).clone())
        for rnd in (blocks, blocks2):
            lgs = multi([(s, torch.tensor(b[0]), b[1], s.kv.length) for s, b in zip(servs, rnd)])
            for s, lg in zip(servs, lgs):
                out += [lg.clone(), s.picks.clone(), s.hidden_post_norm.clone()]
            commit_multi([(s, b[2]) for s, b in zip(servs, rnd)])
            out += [s.last_streams().clone() for s in servs]
        for s in servs:
            out.append(s.forward(torch.tensor([33]), s.kv.length).view(-1).clone())
        return out

    # the one engine: three slots over its weights; its own joint pass where the host can run it (it needs
    # the CUDA build of the joint kernels), else each slot alone, which the engine holds to the same bits
    s0 = FlashNextServing(eng, CAP)
    ones = [s0] + [s0.side_slot(CAP)[0] for _ in range(2)]
    try:
        want = run(ones, FlashNextServing.forward_tree_multi, FlashNextServing.commit_tree_multi)
        joint = "the one engine's joint pass"
    except Exception as e:                                   # noqa: BLE001  (no host stand-in for the joint kernels)
        joint = f"the one engine's slots each alone (its joint pass does not run on this host: {type(e).__name__})"
        s0 = FlashNextServing(eng, CAP)
        ones = [s0] + [s0.side_slot(CAP)[0] for _ in range(2)]

        outs = []
        for s, p in zip(ones, prompts):
            outs.append(s.forward(torch.tensor(p), 0).view(-1).clone())
        for rnd in (blocks, blocks2):
            for s, b in zip(ones, rnd):
                lg = s.forward_tree(torch.tensor(b[0]), b[1], s.kv.length)
                outs += [lg.clone(), s.picks.clone(), s.hidden_post_norm.clone()]
                s.commit_tree(b[2])
            outs += [s.last_streams().clone() for s in ones]
        for s in ones:
            outs.append(s.forward(torch.tensor([33]), s.kv.length).view(-1).clone())
        want = outs                                          # the order `run` gives them in
    for k in (2, 4):
        x = Split(eng, k, slots=3)
        try:
            servs = [x.srv] + [x.srv.side_slot(CAP)[0] for _ in range(2)]
            assert [s.co.slot for s in servs] == [0, 1, 2] and len({id(s.fn) for s in servs}) == 3
            assert all(s.fn.W is x.srv.fn.W for s in servs), "a slot loaded weights of its own"
            n0 = x.follower.stats["rounds"]
            c0 = x.co.link.stats.rx_payload.get(x.L.CARRY, 0)
            got = run(servs, type(x.srv).forward_tree_multi, type(x.srv).commit_tree_multi)
            same_rows(got, want, f"cut {k}, three slots")
            assert x.follower.stats["rounds"] - n0 == 2 and x.co.stats["rounds"] == 2
            rows = sum(len(p) for p in prompts) + sum(len(b[0]) for b in blocks + blocks2) + 3
            assert x.co.link.stats.rx_payload[x.L.CARRY] - c0 == rows * x.front.carry_bytes_per_row
            for i, (s, o) in enumerate(zip(servs, ones)):
                s.co.sync()
                front = x.front if i == 0 else x.front.slot(i)
                got_state = {**front.state(), **s.ad.state()}
                for name, t in whole_state(o).items():
                    T.same(got_state[name], t, f"cut {k}, slot {i}: {name}")
            # a slot keeps and restores its own state; a fourth slot is refused
            _raises(ValueError, lambda: x.srv.side_slot(CAP) and x.srv.side_slot(CAP), "3 slots")
            k1 = servs[1]._mark()
            assert k1 in x.front.slot(1).store.entries and k1 not in x.front.store.entries
        finally:
            x.close()
    return f"cuts 2, 4, three slots: two joint rounds, one request and one carry each; logits, picks, streams and state equal {joint}"


# ------------------------------------------------------------------ the flags
def test_the_options_say_what_a_cached_split_serves(monkeypatch, tmp_path):
    H, cuda = T.setup(monkeypatch)
    from engine.models.flashnext import stages as ST
    from engine.models.flashnext.tail import FlashNextSplitCached, FlashNextSplitServing
    d = H.host_pack()
    args = types.SimpleNamespace(model=d, max_len=4096, stage_graphs="on")
    assert ST.stage_options(args, 0, 3)["caches"] == "off" and ST.store_options(ST.stage_options(args, 0, 3)) is None
    args = types.SimpleNamespace(model=d, max_len=4096, stage_graphs="on", stage_caches="on", parallel=3,
                                 stage_snapshots_gb=0.5, stage_cache_dir=str(tmp_path), stage_cache_gb=4.0)
    o = ST.stage_options(args, 0, 3)
    assert (o["slots"], o["caches"], o["snapshots_gb"], o["cache_dir"], o["cache_dir_gb"], o["images"]) == \
        (3, "on", 0.5, "set", 4.0, "refused"), o
    so = ST.store_options(o, args, bytes(range(32)))
    assert so == {"bytes": 1 << 29, "dir": str(tmp_path), "disk_bytes": 4 << 30, "name": bytes(range(16)).hex()}
    args.parallel = 9
    _raises(ValueError, lambda: ST.stage_options(args, 0, 3), "at most 8 slots")
    c = FlashNextSplitCached.caps
    assert c.caches and c.side_slots and c.joint_verify and c.anchor_grid == 4096 and not (c.side_lane or c.shadow)
    assert not FlashNextSplitServing.caps.caches
    # the stage's own files are another arch's than the one process's
    eng = T.reference(H)["eng"]
    x = Split(eng, 3)
    try:
        from engine.models.flashnext.serving import manifest
        m = x.srv.manifest()
        assert m.arch == manifest().arch + "-stage-3-6" and "engine/models/flashnext/stages.py" in m.modules
        from engine.methods import manifest as mf
        fp, whole = mf.fingerprint(ROOT, m, {}), mf.fingerprint(ROOT, manifest(), {})
        assert len(fp) == len(whole) and fp != whole, "a stage's disk tier would read the one process's files"
        # what a cached split still refuses
        _raises(ValueError, lambda: setattr(x.srv.kv, "length", 7), "keeps no rows by length")
        x.srv.forward(torch.tensor(A1[:8]), 0)
        x.srv.mm = object()
        _raises(ValueError, lambda: x.srv.forward(torch.tensor([1]), 8), "text only")
        x.srv.mm = None
        assert not x.srv.window_ok(0) and not x.srv.prefill_joint_ok(8, 0)
        # a state with no key on either side
        x.srv._key.fill_(7)
        e = _raises(x.S.StageError, lambda: x.srv.resume_at(4))
        assert e.code == "snapshot" and x.srv.kv.length == 8 and x.srv.restores["refused"] == 1
    finally:
        x.close()
    return "caches and slots only with --stage-caches on; the store's bytes, folder and name; images, rows by length and a keyless state refused"


def test_both_stages_start_with_the_caches_as_the_server_starts_them(monkeypatch, tmp_path):
    """server/stage.py and `serve`'s stage branch with --stage-caches on and three slots: the front
    builds its slots and its store from the flags, the tail is the cached serving on the server's
    own coordinator; then a cached turn, a side slot with the pack's own drafter and a joint round
    over that wiring."""
    H, cuda = T.setup(monkeypatch)
    import functools
    import json
    import urllib.request

    from engine.methods import stages as S
    from engine.models.flashnext import serving as SV
    from engine.models.flashnext import stages as ST
    from engine.models.flashnext import tail as TL
    from ports import free_port
    from server import stage
    eng = T.reference(H)["eng"]
    d = H.host_pack()
    monkeypatch.setattr(TL, "DEVICE", "cpu")
    monkeypatch.setattr(ST, "open_stage", functools.partial(ST.open_stage, device="cpu"))
    monkeypatch.setattr(SV, "CAPTURE_ROWS", 4)
    monkeypatch.setenv("QWEN38_FN_DRAFT_HOT", "0")
    monkeypatch.setitem(sys.modules, "transformers", types.SimpleNamespace(
        AutoTokenizer=types.SimpleNamespace(from_pretrained=lambda path: T._Tok())))
    key = str(tmp_path / "link.key")
    with open(os.open(key, os.O_WRONLY | os.O_CREAT, 0o600), "w") as f:
        f.write(secrets.token_hex(32) + "\n")
    lines, marks = [], []
    link_port, port = free_port(), free_port()
    common = ("--model", d, "--link-key-file", key, "--stage-caches", "on", "--parallel", "3", "--drafter", "mtp",
              "--stage-snapshots-gb", "1", "--stage-cache-dir", str(tmp_path / "front"))
    fa = T._args(*common, "--stage", "0:3", "--link-listen", f"127.0.0.1:{link_port}", "--port", str(port))
    fstate: dict = {}
    stage.prepare(fa, fstate, log=lines.append)
    assert fstate["stage"].options["slots"] == 3 and fstate["stage"].options["caches"] == "on"
    threading.Thread(target=stage.serve_front, args=(fa, fstate, lines.append), daemon=True).start()

    def front_health():
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as r:
            return json.loads(r.read())["stage"]
    end, st = time.monotonic() + 240, None
    while st is None and time.monotonic() < end:
        try:
            st = front_health()
        except OSError:
            time.sleep(0.2)
    assert st["options"]["slots"] == 3 and st["state"]["slots"] == {} and st["state"]["snapshots"] == 0, st
    assert sum("built and warmed" in x for x in lines) == 2 and any("first launches" in x for x in lines), lines[-6:]
    ta = T._args(*common, "--stage", "3:6", "--link-peer", f"127.0.0.1:{link_port}")
    tstate: dict = {}
    stage.prepare(ta, tstate, log=lines.append)
    ctx = types.SimpleNamespace(state=tstate, keys=lambda *x: None, mark=marks.append, log=lines.append,
                                vision=lambda *x: 1 / 0)
    out = SV.serve(ta, ctx)
    srv, fol = out["engine"], fstate["stage"].follower
    try:
        assert type(srv) is TL.FlashNextSplitCached and srv.co.keep and srv.caps.caches and "caches on" in out["describe"]
        seed = tstate["stage"].seed()
        assert seed == fstate["stage"].seed() and srv.co.key == S.root_key(seed) != S.root_key()
        # a cached turn over the server's own link: the one engine's logits, the front's half under the same key
        s1, r1 = one(eng), resident(end=True)
        w1 = turn(s1, A1, resident=r1)
        r1.keep_end(s1, None, w1[1], len(A1))
        w2 = turn(s1, w1[1] + A2, resident=r1)
        res = resident(end=True)
        g1 = turn(srv, A1, resident=res)
        res.keep_end(srv, None, g1[1], len(A1))
        same_rows(g1[0], w1[0], "turn 1 as the server starts the stages")
        g2 = turn(srv, g1[1] + A2, resident=res)
        assert g2[2]["kind"] == "end"
        same_rows(g2[0], w2[0], "turn 2 from the end of turn 1")
        srv.co.sync()
        assert srv.restores["hit"] == 1 and srv.co.rebuilds == 0 and fol.seats[0].key == srv.co.key
        assert os.path.isdir(os.path.join(str(tmp_path / "front"), seed.hex()[:32]))
        # a side slot with the pack's own drafter, and one without: the server's loop on slot 1
        e1, d1 = srv.side_slot(CAP, out["drafter"])
        e2, _ = srv.side_slot(CAP, None)
        assert d1 is not None and e1.heads["mtp"].graphs and e1.co.slot == 1 and e1.co.key == S.root_key(seed)
        assert e1.fn is not srv.fn and e1.fn.W is srv.fn.W and e1.fn.draft_ids is srv.fn.draft_ids
        tokens = T._loop(e1, T.PROMPT, 12, d1)
        assert tokens == T._loop(SV.FlashNextServing(eng, CAP), T.PROMPT, 12), "slot 1 with its drafter"
        e1.co.sync()
        assert e1.stats["verify_blocks"] > 0 and fol.seats[1].key == e1.co.key
        # a joint round of the three slots over the server's link
        for s, p in ((srv, A1[:19]), (e1, A1[3:25]), (e2, B1 + A2)):
            s.kv.length = 0
            s.forward(torch.tensor(p), 0)
        lgs = type(srv).forward_tree_multi([(s, torch.tensor([7, 19, 23]), [-1, 0, 0], s.kv.length) for s in (srv, e1, e2)])
        ref = one(eng)
        ref.forward(torch.tensor(A1[3:25]), 0)
        T.same(lgs[1], ref.forward_tree(torch.tensor([7, 19, 23]), [-1, 0, 0], ref.kv.length), "slot 1's block in the round")
        type(srv).commit_tree_multi([(s, [0, 2]) for s in (srv, e1, e2)])
        th = stage.health(tstate, "ok")["stage"]
        assert th["state"]["rounds"] == 1 and th["state"]["round_max"] == 3 and th["state"]["slots"] == 3, th["state"]
        assert th["options"]["slots"] == 3 and th["options"]["caches"] == "on"
        fh = front_health()
        assert sorted(fh["state"]["slots"]) == ["0", "1", "2"] and fh["state"]["rounds"] == 1, fh["state"]
        assert isinstance(stage.disk(tstate, object()), stage.StageDisk)
    finally:
        srv.co.link.close()
        time.sleep(1.0)                          # the front's follower drops its sequences before the next case
    return ("server/stage.py and serve() with --stage-caches on and three slots: one seed in both stages' keys, a "
            "cached turn, slot 1 with the pack's drafter through the server's loop, a joint round of three")


def test_the_cut_cost_tool_checks_its_baseline(monkeypatch):
    H, cuda = T.setup(monkeypatch)
    from tools import stage_cut_cost as C
    res = C.measure(H.host_pack(), 3, runs=2, prompt_rows=8, steps=3, blocks=2, block_rows=4, max_len=CAP,
                    device="cpu", log=None, graphs=False)
    b, a = res["baseline"], res["args"]
    assert res["equal"] and (a["prime"], a["order"], b["prime"], b["order"]) == ("on", "alt", "on", "alt")
    assert set(b["position_share"]) == set(C.KINDS) and isinstance(b["clean"], bool) and "2%" in b["rule"]
    # one timed run a side: each order has the uncut arm's and the cut arm's times
    assert set(b["by_order"]) == {"uncut_first", "cut_first"}
    assert all(v["step"]["n"] == 3 for v in b["by_order"].values())
    k = res["kinds"]["step"]
    assert k["n"] == 6 and k["uncut_again_ms"]["p50"] > 0 and k["uncut_ms"]["p50"] > 0 and k["cut_ms"]["p50"] > 0
    old = C.measure(H.host_pack(), 3, runs=1, prompt_rows=8, steps=3, blocks=2, block_rows=4, max_len=CAP,
                    device="cpu", log=None, graphs=True, prime=False, order="ab")
    assert old["equal"] and old["baseline"]["prime"] == "off" and set(old["baseline"]["by_order"]) == {"uncut_first"}
    return "primed, alternating order, the uncut arm once more a run: the baseline's own check beside the cut's cost"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
