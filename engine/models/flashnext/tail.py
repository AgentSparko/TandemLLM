"""Flash-Next's tail stage behind the server's generation loop (ENG-307): `FlashNextServing` with
every pass sent through the stage coordinator (engine/methods/stages.py), and the builder the
family's `serve` hands a `--stage` start to. The front stage has no serving adapter of its own: the
server runs it from `stages.open_stage` (server/stage.py).

The tail holds the layers [k, layers), the final mixer, the head, the draft layer and the sampler.
The draft layer reads the streams as they leave the last layer, which only the tail has, so the
drafter, its cache and its graphs are the one process's, unchanged.

`FlashNextSplitServing` is the first build: one sequence, nothing kept. `FlashNextSplitCached`
(ENG-423, `--stage-caches on`) holds the server's caches: the tail's half of a state is what the
caches keep, with the key both stages name the state by as one more section (`split.key`). Reading
the sections asks the front to keep its half under that key; a state the caches put back is
followed by `resume_at`, which sends the key in those sections to the front. So the two halves of a
restored state are always the same state's, whichever cache held it and however it moved the bytes.
"""

from __future__ import annotations

import torch

from engine.models.adapter import Capabilities
from engine.models.flashnext.engine import PREFILL_CHUNK
from engine.methods.manifest import Manifest
from engine.models.flashnext.serving import FlashNextCacheState, FlashNextServing, _KV, draft_setup, manifest


#: where a tail stage's weights and state live
DEVICE = "cuda"


class _SplitKV(_KV):
    """A split engine's `kv`: the length is both stages'. Zero starts a new sequence on both; no
    other length can be set, a split keeps no rows by length."""

    pages = None

    @property
    def length(self) -> int:
        return _KV.length.fget(self)

    @length.setter
    def length(self, v: int) -> None:
        v = int(v)
        if v == 0:
            self._s.restart()
        elif v != self.length:
            raise ValueError(f"a split engine keeps no rows by length (asked for {v}, it holds {self.length})")


class _NoSnapshots(FlashNextCacheState):
    """A split engine's cache state: there is none, and nothing reads half of one. Its buffers are
    still counted (`cost`, `memory_parts`): the tail's own sections."""

    def _refuse(self, *a, **k):
        raise ValueError("a split engine keeps no snapshots: the caches are off (ENG-307)")

    sections = views = clone = restore = signature = nbytes = _refuse


class FlashNextSplitServing(FlashNextServing):
    """The tail stage of a split model in the server's engine interface (ENG-307). The object is
    also the serving behind the tail's stage adapter (stages.py `FlashNextRowStage.sv`): the verbs
    below ask the coordinator, which asks the front stage for the block's rows at the cut and then
    has the adapter run `FlashNextServing`'s own verb on this object, on the tail's layers.

      forward, one token      a decode step on both stages (stages.py `STEP`)
      forward, more tokens    prompt chunks of FN_PREFILL_CHUNK, chunk-major, a round a chunk
      forward_tree            a verify block on both stages; `commit_tree` keeps its path

    so a split runs each block on the kernels the engine in one process runs it on. One slot, text
    only, no caches, no rows kept by length, no layer-major window and no joint pass: `caps` says so,
    and what is asked beyond it is refused."""

    caps = Capabilities(caches=False, side_slots=False, anchor_grid=None, shadow=False, joint_verify=False,
                        side_lane=False, joint_plain=False)

    def __init__(self, fn, max_len: int):
        super().__init__(fn, max_len)
        self.co = self.ad = None
        self._replays = 0
        self.state = _NoSnapshots(self)
        self.kv = _SplitKV(self)

    def attach(self, co) -> "FlashNextSplitServing":
        """Take the coordinator (its link is up): every pass goes through it from here on."""
        self.co, self.ad, self._replays = co, co.adapter, co.replays
        return self

    def _ask(self, fn, *args, **kw):
        """One request of the coordinator. A replay ran the sequence again on other kernels than
        it was written on (prompt chunks for decode steps), so its rows are not the one process's:
        the sequence is dropped and the request ends."""
        from engine.methods.stages import StageError
        if self.mm is not None or self.pos_delta:
            raise ValueError("a split engine serves text only")
        out = fn(*args, **kw)
        if self.co.replays != self._replays:
            self._replays = self.co.replays
            self.restart()
            raise StageError("desync", "desync: the stages' rows were replayed; the sequence is dropped")
        return out

    def restart(self) -> None:
        """A new sequence on both stages. With the link down nothing is sent: a new link starts
        both stages empty."""
        from engine.methods.stages import StageError
        self._chain = None
        if self.co is None:
            return FlashNextServing.restart(self)
        try:
            self.co.reset()                  # this stage through the adapter, then the front
        except StageError:
            pass

    @torch.no_grad()
    def forward(self, ids: torch.Tensor, start: int, last_only: bool = True) -> torch.Tensor:
        from engine.models.flashnext.stages import STEP
        self._settle_chain()
        toks = [int(t) for t in ids.view(-1).tolist()]
        self._at(start)
        T = len(toks)
        if T == 1 and start > 0:
            lg = self._ask(self.co.prefill, toks, "all", graph=STEP)
            return lg.view(1, 1, -1)
        rows = []
        for at in range(0, T, PREFILL_CHUNK):
            last = at + PREFILL_CHUNK >= T
            lg = self._ask(self.co.prefill, toks[at:at + PREFILL_CHUNK], "last" if last else "none")
            rows.append(self.ad.hidden)
        self._hidden = (rows[0] if len(rows) == 1 else torch.cat(rows)).unsqueeze(0)
        self._h_last.copy_(self._hidden[0, T - 1:T])
        self.stats["prefill_rows"] += T
        return lg.view(1, 1, -1)

    @torch.no_grad()
    def forward_tree(self, block: torch.Tensor, parents: list[int], start: int) -> torch.Tensor:
        self._settle_chain()
        self._at(start)
        toks = [int(t) for t in block.view(-1).tolist()]
        lg = self._ask(self.co.verify, toks, [int(p) for p in parents])
        return lg[:len(toks)]

    @torch.no_grad()
    def commit_tree(self, path: list[int]) -> None:
        self.co.commit([int(p) for p in path])

    # what one slot of a split does not run
    def window_ok(self, i: int) -> bool:
        return False

    def prefill_joint_ok(self, T: int, start: int) -> bool:
        return False

    def tree_batchable(self, parents) -> bool:
        return False

    @staticmethod
    def forward_tree_multi(items: list) -> list:
        raise ValueError("a split engine has one slot: no joint pass")

    @staticmethod
    def commit_tree_multi(items: list) -> None:
        raise ValueError("a split engine has one slot: no joint pass")

    def side_slot(self, max_len: int, drafter0=None) -> tuple:
        raise ValueError("a split engine has one slot")

    def resume_at(self, n: int) -> None:
        raise ValueError("a split engine keeps no snapshots: the caches are off (ENG-307)")

    @torch.no_grad()
    def self_check(self, prompt: list[int], steps: int = 16, log=print) -> dict:
        """The load-time check across the cut: decode `steps` tokens one at a time after a prefill,
        then verify the same tokens as one chain block after the same prefill, and compare every
        row's logits and streams bit for bit. Any difference leaves speculation off."""
        self.restart()
        lg = self.forward(torch.tensor(prompt), 0)
        toks, rows, streams = [int(lg.view(-1).argmax())], [], []
        for i in range(min(steps, 32)):
            out = self.forward(torch.tensor([toks[-1]]), len(prompt) + i)
            rows.append(out.view(-1).clone())
            streams.append(self.hidden_post_norm.view(-1).clone())
            toks.append(int(out.view(-1).argmax()))
        self.restart()
        self.forward(torch.tensor(prompt), 0)
        M = len(rows)
        got = self.forward_tree(torch.tensor(toks[:M]), list(range(-1, M - 1)), len(prompt))
        hid = self.hidden_post_norm[0]
        bad = next((i for i in range(M) if not (torch.equal(got[i], rows[i]) and torch.equal(hid[i], streams[i]))),
                   None)
        self.restart()                       # the block is dropped with the sequence
        self.fn.spec_ok = bad is None
        res = {"ok": bad is None, "rows": M, "first_diff": bad}
        if log:
            log(f"[fn-selfcheck] {'PASS' if bad is None else 'FAIL: speculation stays off'} across the cut: {res}")
        return res


class _SplitState(FlashNextCacheState):
    """The tail's half of a state as the caches keep it: the tail's sections and, as one more
    section, the key both stages name the state by. The caches copy that section with the others,
    to a snapshot, a spare buffer or a file, so a kept half always carries its key."""

    def buffers(self) -> dict:
        out = super().buffers()
        out["split.key"] = self.s._key
        return out

    def sections(self) -> dict:
        out = super().sections()
        self.s._mark()                   # the live state's key, and the front asked to keep its half
        return out


class FlashNextSplitCached(FlashNextSplitServing):
    """The tail stage with the server's caches on (ENG-423): the prefix, session and resident
    caches and the disk tier keep the tail's half of a state; the front keeps its own half under
    the same key (stages.py `FlashNextRowStage.snapshot`). A state put back whose front half is
    gone is rebuilt on the front from the coordinator's log, block by block; one neither stage has
    a log of is refused before this stage's length moves (`resume_at`), and the cache that offered
    it falls back.

    Side slots (`side_slot`): a slot is a stage engine of its own over this stage's weights, with
    its own state, row buffers, graphs and draft head, and a sequence of its own on the front. The
    blocks of a joint round cross the link as one request and come back as one carry
    (`forward_tree_multi`); on each stage every slot's block then runs on its own engine, the pass
    the slot runs alone, so a row's bits are the lone pass's.

    Still one process's work a block: no layer-major window, no joint prefill, text only."""

    caps = Capabilities(caches=True, side_slots=True, anchor_grid=4096, shadow=False, joint_verify=True,
                        side_lane=False, joint_plain=True)

    def __init__(self, fn, max_len: int):
        super().__init__(fn, max_len)
        self._key = torch.zeros(1, 2, dtype=torch.long, device=fn.device)
        self.state = _SplitState(self)
        self.restores = {"hit": 0, "rebuilt": 0, "refused": 0}
        self.main = self                 # slot 0's serving
        self.sides: list = []            # slot 0's: the side slots' servings

    def attach(self, co) -> "FlashNextSplitCached":
        """As the first build's, and this stage's sequence outlives a link: the caches hold rows of
        it, so a new link leaves it as it is and the front is brought back to it."""
        co.keep = True
        return super().attach(co)

    # --- side slots and joint rounds ---------------------------------------------------------------
    def side_slot(self, max_len: int, drafter0=None) -> tuple:
        """The next side slot's serving and drafter: the stage's next slot adapter (its own engine
        over the same weights, a window of `max_len` rows, at most slot 0's), its coordinator on
        slot 0's link, and the drafter the one process builds for a slot, over this slot's own
        draft head."""
        from engine.methods.mtp_drafter import MTPTreeDrafter
        from engine.methods.staircut import PriceModel, StairCut
        main = self.main
        ad = main.ad.slot(len(main.sides) + 1, cap=max_len)      # ValueError: the stage has no further slot
        e2 = ad.sv
        e2.attach(main.co.side(ad, ad.slot_id))
        e2.main = main
        fn0, fn2 = main.fn, e2.fn
        for name in ("draft_head", "draft_ids", "coupled_head", "coupled_ids", "lone_head", "lone_ids",
                     "_draft_heads", "spec_ok"):
            if name in fn0.__dict__:
                setattr(fn2, name, fn0.__dict__[name])   # the hot draft heads: one copy, as the weights
        main.sides.append(e2)
        d2 = None
        if drafter0 is not None and hasattr(drafter0, "cut"):
            ng = None
            if drafter0.ngram is not None:
                from engine.drafters.ngram import NgramDrafter
                ng = NgramDrafter(corpus_path="", min_order=3, max_depth=16, min_corpus_order=8)
                ng.corpus = drafter0.ngram.corpus
            c0 = drafter0.cut
            d2 = MTPTreeDrafter(e2, e2.add_mtp_head(drafter0.depth),
                                StairCut(PriceModel(c0.prices.table, c0.prices.step), siblings=c0.siblings), ng,
                                freeze=getattr(drafter0, "freeze", False))
            d2.mtp_look_backoff = getattr(drafter0, "mtp_look_backoff", 0)
            d2.capture_all()
        return e2, d2

    def tree_batchable(self, parents) -> bool:
        return len(parents) <= 32

    @staticmethod
    @torch.no_grad()
    def forward_tree_multi(items: list) -> list:
        """[(serving, block, parents, start)] of one link's slots: one request and one carry for
        the round, then each slot's block on its own engine; each slot is left as its
        `forward_tree` leaves it and its block is kept by its `commit_tree`."""
        main = items[0][0].main
        jobs = []
        for s, block, parents, start in items:
            if s.mm is not None or s.pos_delta:
                raise ValueError("a split engine serves text only")
            s._settle_chain()
            s._at(start)
            jobs.append((s.co, [int(t) for t in block.view(-1).tolist()], [int(p) for p in parents]))
        outs = main.co.verify_round(jobs)
        return [lg[:len(job[1])] for lg, job in zip(outs, jobs)]

    @staticmethod
    @torch.no_grad()
    def commit_tree_multi(items: list) -> None:
        for s, path in items:
            s.commit_tree(path)

    def manifest(self) -> Manifest:
        """A stage's stored rows are its own layers': another arch's files than the one process's."""
        m = manifest()
        return Manifest(arch=f"{m.arch}-stage-{self.fn.lo}-{self.fn.hi}",
                        modules=m.modules + ("engine/models/flashnext/stages.py",), data=m.data)

    def _mark(self, keep: bool = False) -> bytes:
        """The key of the state this stage holds, written into its section; the coordinator asks
        the front to keep its half (`keep`: on its disk too)."""
        mark = getattr(self.co, "mark", None)
        key = mark(keep=keep) if mark is not None else bytes(16)
        self._key.copy_(torch.frombuffer(bytearray(key), dtype=torch.int64).view(1, 2))
        return key

    def disk_keep(self) -> None:
        """The disk tier took this state's sections for a file: the front writes its half too."""
        self._mark(keep=True)

    @torch.no_grad()
    def resume_at(self, n: int) -> None:
        """The caches wrote a kept state into this stage's sections and rows: the front goes to the
        state of the key in them, then this stage continues at `n` as the one process does. A front
        that cannot (StageError `snapshot`) leaves both lengths where they were."""
        from engine.methods.stages import StageError
        n = int(n)
        key = self._key.view(-1).cpu().numpy().tobytes()
        try:
            how = self.co.restore(key, n)
        except StageError as e:
            if e.code == "snapshot":
                self.restores["refused"] += 1
            raise
        self.restores[how] += 1
        self.ad._tree = None
        self.discard_pending()
        if n == 0:
            return FlashNextServing.restart(self)
        for li, buf in self._ctx.items():
            self.st.ple_ctx[li] = buf.view(1, -1).cpu().clone()
        self.st.length = n
        self.st.clamp_pool()
        self.fn.build_pool(self.st)


def serve_stage(a, ctx, stage) -> dict:
    """The tail stage for the server: its own layers' weights and the draft layer, the link to the
    front (`stage.attach`, the server's), the load-time self-check across the cut, the drafters as
    the one process builds them (serving.py `draft_setup`). The front is started by the server itself
    from `stages.open_stage` (server/stage.py). No vision tower is loaded: a split serves text."""
    from transformers import AutoTokenizer

    from engine.models.flashnext import stages
    from engine.spec import Relax
    ctx.keys(a.model, [a.model])
    spec = a.drafter != "none"
    if a.drafter not in ("mtp", "none"):
        ctx.log(f"[server] Flash-Next drafts with its own MTP layer: --drafter {a.drafter} taken as mtp")
    tok = AutoTokenizer.from_pretrained(a.model)
    depth = a.depth if a.depth > 3 else 4
    cached = (stage.options or {}).get("caches") == "on"
    ad = stages.open_stage(a.model, stage.lo, stage.hi, a.max_len, device=DEVICE, log=ctx.log,
                           options=stage.options, serving=FlashNextSplitCached if cached else FlashNextSplitServing,
                           mtp=spec)
    ctx.mark("weights")
    ad.warm()                                # the tail's own first launches, before the link is asked
    srv = ad.sv.attach(stage.attach(ad))
    ctx.mark("engine")
    drafter, check, hot, corrector = draft_setup(srv, a.model, tok, spec=spec, depth=depth, log=ctx.log,
                                                 corpus=a.corpus, self_check=srv.self_check)
    ctx.mark("drafters")
    if drafter is not None:
        a.depth = depth
        if drafter.ngram is not None:
            a.k = max(int(a.k or 0), drafter.cut.max_rows - 1)
    info = ad.info
    head = f"hot {hot}" if spec else None
    describe = (f"Flash-Next {a.model}, tail stage layers [{stage.lo}, {stage.hi}), caches "
                f"{'on' if cached else 'off'}: load {info['load'].get('total_s')} s, drafter "
                f"{'MTP chain + StairCut, depth ' + str(a.depth) if drafter is not None else 'none'}"
                f"{' + lookup' if getattr(drafter, 'ngram', None) is not None else ''}, "
                f"KV cache bf16, max_len {a.max_len:,},")
    return {"engine": srv, "tok": tok, "drafter": drafter, "path": a.model, "device": DEVICE,
            "cfg": None, "w": None, "describe": describe,
            "serving": {"tree": drafter is not None, "sampled_tree": "det", "relax": Relax(1.0, 1),
                        "weights_info": {"format": "exl3", "weights": a.model,
                                         "drafters": "mtp (the pack's own layer)" if drafter is not None else None,
                                         "draft_head": head, "load_s": info["load"].get("total_s"),
                                         "self_check": check, "kv_cache": "bf16",
                                         **({"corrector": corrector} if corrector is not None else {})}}}
