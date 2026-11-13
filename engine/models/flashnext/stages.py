"""Flash-Next as a stage of a split model (ENG-307).

Two adapters, one interface (engine/methods/stages.py `StageAdapter`):

  FlashNextStage      the reference path: the layers [lo, hi) of `FlashNextRef` with the reference's
                      own layer code and an `FNState` of those layers' entries. The carry is the 4
                      residual streams as they leave layer k - 1, [rows, hc * hidden] in the compute
                      dtype. A prompt chunk runs as the reference runs it (`forward`); a verify block
                      runs row by row, each row on a copy of its parent's state.
  FlashNextRowStage   the engine's path: the layers [lo, hi) of `FlashNextEngine` on its row kernels
                      and its prefill kernels. The stage's engine is `FlashNextStageEngine`: the
                      engine class built over a config whose layer lists hold the stage's own layers
                      (`stage_config`), so it loads, keeps and commits only those, with the three
                      passes that walk the layers cut to [lo, hi): `trunk_range`, `rows_step` and
                      `forward_pre` carry the engine's own statements (tests/test_fn_stage.py compares
                      the syntax trees). Everything else is the engine's code as it is: the staging,
                      `verify`, `commit`, the folded commit, the decode, verify and commit graphs.

The carry of the engine's path is what the engine holds at a layer boundary: the streams H and, fused
in the same launch, the next site's norm N of them, [rows, 2, hc * hidden] bf16. In front of a PLE
layer the engine computes the norm after that layer changed the streams, so the carry there is H
alone, [rows, 1, hc * hidden]. A PLE layer reads the block's tokens (the header carries them) and
the n-gram table on whichever side holds it.

A block is run the way the engine in one process runs it: a prompt chunk on the prefill kernels
(chunk-major, one chunk a round), one decode token (`STEP`) on the row kernels with the state
advanced in place, a draft tree on the row kernels with its commit left to `commit`. With graphs
(the default) a stage captures its own decode, verify and commit graphs through the engine's graph
classes: a graph's body ends at the cut, the carry enters it through static buffers and leaves it as
the graph's own tensors, and the copy to and from the host runs outside the graph.

`cuts`: every layer boundary.

State beyond the running sequence (ENG-423): a stage built with a store (`open_stage`, a front's)
keeps its half of a state under the coordinator's key (engine/methods/stage_store.py): its
sections copied, its cache rows where they are until a pass would write over them. A block run
again gives the rows it gave (`exact`), so a front that lost its state is rebuilt from the tail's
log of blocks. A stage built with slots gives each slot its own engine over the same weights
(`slot`): its own state, row buffers, carry buffers and graphs, so the blocks of a joint round
run one slot after the other on each stage and each stays pending until its own commit.
"""

from __future__ import annotations

import dataclasses

import torch

from engine.methods import link as L
from engine.methods.stages import Block, StageAdapter
from engine.models.flashnext.arch import ARCH
from engine.models.flashnext.config import FlashNextConfig
from engine.models.flashnext.engine import DENSE_MAX, PREFILL_CHUNK, FlashNextEngine
from engine.models.flashnext.ref import FlashNextRef, FNModelState, FNState

#: a chain block's tag for one decode token: the row kernels, the state advanced in place
STEP = 1


class FlashNextStage(StageAdapter):
    family = ARCH.name

    def __init__(self, ref: FlashNextRef, lo: int, hi: int):
        c = ref.cfg
        if not 0 <= lo < hi <= c.num_hidden_layers:
            raise ValueError(f"no layers [{lo}, {hi}) in a model of {c.num_hidden_layers}")
        self.ref, self.lo, self.hi, self.layers = ref, lo, hi, c.num_hidden_layers
        self.cuts = tuple(range(1, self.layers))
        self.carry_dtype = str(ref.dtype).replace("torch.", "")
        self.carry_dims = (c.hc_count * c.hidden_size,)
        self.reset()

    @property
    def length(self) -> int:
        return self.st.length

    def reset(self) -> None:
        self.st = FNState()
        self.pending = None
        self.stack = None        # the last block's streams before the final mixer (the draft layer's input)

    @torch.no_grad()
    def _run(self, tokens: torch.Tensor, h, st: FNState, head: str):
        """`FlashNextRef.forward` over this stage's layers: from the tokens (front) or from the
        carry, to the carry or to the head."""
        ref = self.ref
        T = tokens.shape[0]
        positions = torch.arange(st.length, st.length + T, device=ref.device)
        if self.lo == 0:
            h = ref.embed(tokens).repeat(1, ref.cfg.hc_count)
        for li in range(self.lo, self.hi):
            h = ref.layer(h, li, tokens, st, positions)
        st.length += T
        if self.hi < self.layers or head == "none":
            return h, None
        x = ref.mixer(h[-1:] if head == "last" else h, "hyper_connection_mixer", combine=False)
        return h, ref.lin(x, ref.w("lm_head.weight")).float()

    def rows(self, block: Block, carry):
        if (carry is None) != (self.lo == 0):
            raise ValueError("the front stage takes tokens, a later stage the carry")
        if block.slot:
            raise ValueError("the reference stage has one slot")
        tokens = torch.tensor(block.tokens, dtype=torch.long)
        if block.chain:
            h, logits = self._run(tokens, carry, self.st, block.head)
            self.pending = None
        else:
            hs, lgs, states = [], [], []
            for i, p in enumerate(block.parents):
                st = (states[p] if p >= 0 else self.st).clone()
                h1, lg = self._run(tokens[i:i + 1], None if carry is None else carry[i:i + 1], st, "all")
                hs.append(h1)
                lgs.append(lg)
                states.append(st)
            h = torch.cat(hs)
            logits = torch.cat(lgs) if lgs[0] is not None else None
            self.pending = (block.parents, states)
        if self.hi < self.layers:
            return h
        self.stack = h
        return logits

    def commit(self, path: list) -> None:
        if self.pending is None:
            if path:
                raise ValueError("no block to commit")
            return
        parents, states = self.pending
        if path and (path[0] != 0 or any(not 0 <= b < len(parents) or parents[b] != a
                                         for a, b in zip(path, path[1:]))):
            raise ValueError(f"{path} is not a root path of the last block")
        if path:
            self.st = states[path[-1]]
        self.pending = None

    def state(self) -> dict:
        st = self.st
        out = dict(FNModelState(st).sections())
        for name, rows in (("k", st.k), ("v", st.v), ("ik", st.ik)):
            for li in sorted(rows):
                out[f"{name}.{li}"] = rows[li]
        return out


# ---------------------------------------------------------------------- the engine's path
class StageConfig(FlashNextConfig):
    """The model's config as a stage's engine reads it: the layer lists hold the layers [lo, hi), so
    the engine builds its conv taps, hashes and buffers for those and commits those; every other
    field, the layer count and each layer's kind are the model's."""

    lo = 0
    hi = 0

    def _own(self, layers: list) -> list:
        return [i for i in layers if self.lo <= i < self.hi]

    @property
    def attention_layers(self) -> list:
        return self._own(FlashNextConfig.attention_layers.fget(self))

    @property
    def linear_layers(self) -> list:
        return self._own(FlashNextConfig.linear_layers.fget(self))

    @property
    def ple_layers(self) -> dict:
        return {li: pi for li, pi in FlashNextConfig.ple_layers.fget(self).items() if self.lo <= li < self.hi}


def stage_config(cfg: FlashNextConfig, lo: int, hi: int) -> StageConfig:
    if not 0 <= lo < hi <= cfg.num_hidden_layers:
        raise ValueError(f"no layers [{lo}, {hi}) in a model of {cfg.num_hidden_layers}")
    out = StageConfig(**{f.name: getattr(cfg, f.name) for f in dataclasses.fields(FlashNextConfig)})
    out.lo, out.hi = lo, hi
    return out


def carry_parts(cfg: FlashNextConfig, k: int) -> int:
    """The parts of a row's carry at the boundary in front of layer k: the streams H and the site's
    norm N of them; H alone in front of a PLE layer, which changes the streams before the norm."""
    return 1 if k in FlashNextConfig.ple_layers.fget(cfg) else 2


class FlashNextStageEngine(FlashNextEngine):
    """`FlashNextEngine` over the layers [lo, hi) of a model: built over `stage_config`, with the
    passes that walk the layers cut to the range. lo == 0: the rows enter as tokens; else as the
    carry (H, N), which a row pass reads from static buffers (`carry_in`) so a graph can hold it.
    hi below the layer count: a pass ends at the cut with the carry (H, N) and the head does not
    run; `rec["stack"]` is then H and `rec["carry"]` N (None in front of a PLE layer)."""

    def __init__(self, cfg: FlashNextConfig, W: dict, tables: dict, banks: dict, lo: int, hi: int, device="cuda",
                 max_len: int = 8192):
        view = stage_config(cfg, lo, hi)
        #: the model's config (the stage's own, `cfg`, lists the stage's layers only)
        self.model_cfg = cfg
        self.lo, self.hi, self.layers = lo, hi, cfg.num_hidden_layers
        super().__init__(view, W, {li: t for li, t in tables.items() if li in view.ple_layers}, banks, device=device,
                         max_len=max_len)

    # ------------------------------------------------------------------------- state
    def new_state(self, cap: int | None = None, paged: bool = False) -> FNState:
        """`FlashNextEngine.new_state` for the layers [lo, hi)."""
        from engine import kvpool as _kvp
        c = self.cfg
        cap = cap or self.max_len
        st = FNState(cap=cap)
        if (paged and _kvp.POOL is not None and cap >= _kvp.PAGED_MIN_ROWS
                and str(self.device).startswith("cuda")):
            st.pages = _kvp.PagedSet(_kvp.OWNER)
        dev = self.device
        for li in range(self.lo, self.hi):
            if c.is_linear(li):
                st.S[li] = torch.zeros(1, c.linear_num_value_heads, c.linear_key_head_dim,
                                       c.linear_value_head_dim, dtype=torch.float32, device=dev)
                st.conv[li] = torch.zeros(1, c.conv_dim, c.linear_conv_kernel_dim - 1, dtype=self.dtype, device=dev)
            else:
                # SPD-150: e4m3 codes (uint8) and their scales [Hk, cap] fp32 with QWEN38_FN_KV_FP8
                kdt = torch.uint8 if self.kv_fp8 else None
                st.k[li] = self._rows_tensor(st, (c.num_key_value_heads, cap, c.head_dim), 1, f"k.{li}", kdt)
                st.v[li] = self._rows_tensor(st, (c.num_key_value_heads, cap, c.head_dim), 1, f"v.{li}", kdt)
                if self.kv_fp8:
                    st.ks[li] = self._rows_tensor(st, (c.num_key_value_heads, cap), 1, f"ks.{li}", torch.float32)
                    st.vs[li] = self._rows_tensor(st, (c.num_key_value_heads, cap), 1, f"vs.{li}", torch.float32)
                if c.has_indexer:
                    st.ik[li] = self._rows_tensor(st, (cap, c.indexer_head_dim), 0, f"ik.{li}")
                    st.pool[li] = self._pool_tensor(cap)
        for li in c.ple_layers:
            st.ple_conv[li] = torch.zeros(1, c.hc_hidden, (c.ple_conv_kernel_size - 1) * c.ngram_size,
                                          dtype=self.dtype, device=dev)
        return st

    def stage_commit(self, st: FNState, path: list[int]) -> tuple:
        """`FlashNextEngine.stage_commit`. It sizes the pool rows a commit names from the first
        attention layer's pool; a stage without an attention layer has none, and no layer of it
        reads those rows: it is lent one of that size for the call."""
        if not self.cfg.has_indexer or st.pool:
            return super().stage_commit(st, path)
        if getattr(self, "_no_pool", None) is None or self._no_pool[0] != st.cap:
            self._no_pool = (st.cap, self._pool_tensor(st.cap)[:, :1])
        st.pool[None] = self._no_pool[1]
        try:
            return super().stage_commit(st, path)
        finally:
            del st.pool[None]

    # ------------------------------------------------------------------------- the carry
    def carry_in(self, rb: dict) -> tuple:
        """The static buffers a row pass of `rb`'s rows reads its carry from: (H, N), or (H,) in
        front of a PLE layer; each [rows, hc H]. Made on first use, kept with the rows buffers."""
        cin = rb.get("cin")
        if cin is None:
            c = self.cfg
            cin = rb["cin"] = tuple(torch.zeros(rb["M"], c.hc_count * c.hidden_size, dtype=self.dtype,
                                                device=self.device)
                                    for _ in range(carry_parts(self.model_cfg, self.lo)))
        return cin

    # ------------------------------------------------------------------------- the trunk
    def trunk_range(self, h, n, tokens, st, positions, lo: int, hi: int, step: bool = False, pre: bool = False):
        """`FlashNextEngine.trunk` over the layers [lo, hi): the loop's body is the engine's,
        statement for statement, and reads the model's config (a layer is a PLE layer or follows
        one by the model's list, whichever stage holds it). `n`: the norm the rows enter with
        (None at layer 0 and in front of a PLE layer). hi below the layer count: returns (H, N) as
        they leave layer hi - 1; else the final mixer's normed streams."""
        from tools import fn_kernels as K
        c = self.model_cfg
        H, hc, eps = c.hidden_size, c.hc_count, c.rms_norm_eps
        mix = self._mix_pre if pre else self._mix
        for li in range(lo, hi):
            p = self.pre(li)
            if li in c.ple_layers:
                if step:
                    h = h + self._ple_step(h, li, st)
                else:
                    h = h + (self._ple_pre(h, tokens, li, st) if pre else self.ple(h, tokens, li, st))
                n = None
            if n is None:
                n = K.hc_norm(h, self._site_norm(li), H, eps)
            x, di = mix(n, p + "attn_hyper_connection")
            if c.is_linear(li):
                out = self._gdn1(x, li, st) if step else (self._gdn_pre(x, li, st) if pre else self.gdn(x, li, st))
            else:
                out = self._attn1(x, li, st) if step else (self._attn_pre(x, li, st) if pre else
                                                           self.attention(x, li, st, positions))
            h, n = (self._inject_pre if pre else K.inject_norm)(h, out, di, self.W[p + "attn_hyper_connection.rank"],
                                                                self.W[p + "mlp_hyper_connection.hc_norm.weight"], hc, eps)
            x, di = mix(n, p + "mlp_hyper_connection")
            nxt = None if li + 1 in c.ple_layers else self._site_norm(li + 1)
            R = self.W[p + "mlp_hyper_connection.rank"]
            if pre:
                h, n = self._inject_pre(h, self._moe_pre(x, li), di, R, nxt, hc, eps)
            elif x.shape[0] <= self.MOE_ROWS:
                yd, ids, wts = self.moe_parts(x, li)
                if step and not self._write:
                    self.rec[("ids", li)] = ids            # the experts a verify block reads (ENG-155 prices)
                h, n = K.combine_inject_norm(h, yd, ids, wts, di, R, nxt, hc, eps)
            else:
                h, n = K.inject_norm(h, self.moe(x, li), di, R, nxt, hc, eps)
        self.stack = h
        if hi < c.num_hidden_layers:
            return h, n
        if n is None:
            n = K.hc_norm(h, self._site_norm(c.num_hidden_layers), H, eps)
        return n

    def trunk(self, h, tokens, st, positions, step: bool = False, pre: bool = False):
        raise NotImplementedError("a stage walks its own layers: trunk_range")

    def rows_step(self, st: FNState, M: int, write: bool = False, cls: int = 0) -> torch.Tensor:
        """`FlashNextEngine.rows_step` over the stage's layers. A later stage reads the rows' carry
        from `carry_in`'s buffers. A stage that ends at a cut stops there: `rec["stack"]` is H,
        `rec["carry"]` N, and the logits buffer is not written."""
        from tools import fn_kernels as K
        c = self.cfg
        rb = self._rows_buffers(M)
        self.class_check(rb.get("last", st.length + M - 1), cls, "rows_step")
        self._cur, self._write, self._cls = rb, write, cls
        self.rec = {}
        d = rb["d"]
        if self.lo == 0:
            h = self.W["embed_tokens.weight"].index_select(0, d["tok"]).repeat(1, c.hc_count)
            n = None
        else:
            cin = self.carry_in(rb)
            h, n = cin[0], (cin[1] if len(cin) > 1 else None)
        n = self.trunk_range(h, n, None, st, None, self.lo, self.hi, step=True)
        if self.hi < self.layers:
            if self.fold:
                self._pend_of(st)[1].zero_()           # every GDN layer applied its parked rows
            self.rec["stack"] = self.stack
            self.rec["carry"] = n[1]
            return rb["logits"]
        dd = K.gemv(n, self.W["hyper_connection_mixer.down_inject"])
        x = K.mix_up(dd, n, self.W["hyper_connection_mixer.input_mix_weight_up.weight"], c.hc_count)
        if DENSE_MAX:
            K.dense(x, self.W["lm_head.weight"], out=rb["logits"])
        else:
            from tools.exl3_linear import exl3_matmul
            exl3_matmul(x, self.W["lm_head.weight"], out=rb["logits"])
        # the greedy tokens, and their copy into pinned host memory: the host reads them after a sync
        torch.argmax(rb["logits"], dim=-1, out=rb["next"])
        rb["next_h"].copy_(rb["next"], non_blocking=True)
        if self.fold:
            self._pend_of(st)[1].zero_()           # every GDN layer applied its parked rows
        self.rec["stack"] = self.stack
        return rb["logits"]

    @torch.no_grad()
    def forward_pre(self, tokens, st, head="all", carry=None):
        """`FlashNextEngine.forward_pre` over the stage's layers: a prompt chunk through the prefill
        kernels, the state advanced. A later stage takes the chunk's rows as `carry` (H, N). A stage
        that ends at a cut returns (H, N); else the logits `head` asks for."""
        tokens = tokens.view(-1).to(self.device)
        T = tokens.shape[0]
        self._pre = self._pre_ctl(st.length, T, st)
        if self.lo == 0:
            e = self.embed(tokens)
            if st.mm is not None:
                e = self._image_rows(e, st, st.length)
            h = e.repeat(1, self.cfg.hc_count)
            n = None
        else:
            h, n = carry
        n = self.trunk_range(h, n, tokens, st, None, self.lo, self.hi, pre=True)
        self._n32_clear()
        st.length += T
        self._pre = None
        if self.hi < self.layers:
            return n
        if head == "none":
            return None
        return self.final(n[-1:] if head == "last" else n)


class FlashNextRowStage(StageAdapter):
    """The layers [lo, hi) of a `FlashNextStageEngine`: one sequence.

    The stage's passes run behind `sv`, a `FlashNextServing` over the stage's engine (the tail's is
    the object the server talks to, engine/models/flashnext/tail.py): the adapter calls the
    serving class's own verbs on it, so a stage captures, replays, drops and falls back from its
    graphs exactly as the engine in one process does. `graphs` False: every pass eager.

    After a tail's pass `hidden` holds the block's streams before the final mixer (the draft
    layer's input) and, for a tree or a decode token, `picks` the greedy tokens (device tensors the
    next pass overwrites)."""

    family = ARCH.name
    carry_dtype = "bfloat16"
    exact = True

    def __init__(self, eng: FlashNextStageEngine, cap: int | None = None, graphs: bool = True, serving=None,
                 slots: int = 1, slot_id: int = 0, store: dict | None = None):
        from engine.models.flashnext.serving import FlashNextServing
        c = eng.model_cfg
        if eng.kv_fp8:
            raise ValueError("a stage keeps the bf16 cache: FN_KV_FP8 is not served split")
        self.eng, self.model = eng, eng
        self.lo, self.hi, self.layers = eng.lo, eng.hi, eng.layers
        self.cap = cap or eng.max_len
        self.cuts = tuple(range(1, self.layers))
        self.graphs = bool(graphs)
        self.carry_dtype = str(eng.dtype).replace("torch.", "")
        self.parts = carry_parts(c, self.cut) if self.role != "whole" else 2
        self.carry_dims = (self.parts, c.hc_count * c.hidden_size)
        self._base = FlashNextServing
        self.sv = (serving or FlashNextServing)(eng, self.cap)
        self.st = self.sv.st
        self.hidden = self.picks = None
        self._tree = None                    # the rows of the verify block a commit may keep rows of
        self._host = None
        self.slots, self.slot_id, self._sides, self._serving = int(slots), int(slot_id), {}, serving
        #: the kept states of this stage's half (a front's): `store` is {"bytes", "dir", "disk_bytes"}
        self._store_opts = store
        self.store = None
        if store is not None and self.role == "front":
            from engine.methods.stage_store import SideStore
            self.store = SideStore(self.sv.state, int(store.get("bytes", 0)), store.get("dir") or None,
                                   int(store.get("disk_bytes", 0)), name=store.get("name", ""))

    @property
    def length(self) -> int:
        return self.st.length

    def position_class(self, length: int, rows: int) -> int:
        """0 while every row of the block is in the dense regime, else the bit length of the
        context class (`qsa_class`) of its last possible position."""
        return int(self.eng.qsa_class(length + rows - 1)).bit_length()

    def reset(self) -> None:
        self._base.restart(self.sv)
        self._tree = None
        if self.store is not None:
            self.store.reset()

    # ------------------------------------------------------------------ state kept under a key
    @torch.no_grad()
    def snapshot(self, key: bytes, keep: bool = False) -> None:
        """Keep the committed state under `key`: what is pending lands first (`settle`)."""
        if self.store is None:
            raise ValueError("this stage keeps no snapshots")
        self._base.settle(self.sv)               # a tree still pending stays: a verify parks nothing
        self.store.snapshot(key, self.st.length, keep=keep)

    @torch.no_grad()
    def restore(self, key: bytes, length: int) -> bool:
        if self.store is None or length > self.st.cap or not self.store.restore(key, length):
            return False
        self._tree = None
        if length:
            self._base.resume_at(self.sv, length)        # the PLE context, the length, the pooled keys
        else:
            self._base.restart(self.sv)
        return True

    def drop(self, keys) -> None:
        if self.store is not None:
            self.store.drop(keys)

    def slot(self, i: int, cap: int | None = None) -> "FlashNextRowStage":
        """Slot i's adapter: its own stage engine over this one's weights. `cap`: its window when
        it is made (the tail's side slots may be shorter than slot 0; a front's are slot 0's)."""
        if self.slot_id or not 0 < i < self.slots:
            raise ValueError(f"this stage serves {self.slots if not self.slot_id else 1} slot"
                             f"{'s' if self.slots > 1 and not self.slot_id else ''}")
        if i not in self._sides:
            e = self.eng
            cap = min(int(cap or self.cap), self.cap)
            eng = FlashNextStageEngine(e.model_cfg, e.W, e.tables, e.banks, e.lo, e.hi, device=e.device, max_len=cap)
            store = None if self._store_opts is None else dict(self._store_opts)
            self._sides[i] = type(self)(eng, cap, self.graphs, self._serving, slots=self.slots, slot_id=i,
                                        store=store)
        return self._sides[i]

    # ------------------------------------------------------------------ the carry at the cut
    def _take(self, rows: int, carry) -> None:
        """The carry of a row pass into the engine's static buffers, on the device."""
        cin = self.eng.carry_in(self.eng._rows_buffers(rows))
        for i, t in enumerate(cin):
            t.copy_(carry[:, i])

    @staticmethod
    def _join(h, n):
        return h.unsqueeze(1) if n is None else torch.stack((h, n), 1)

    # ------------------------------------------------------------------ a round
    @torch.no_grad()
    def rows(self, block: Block, carry):
        if (carry is None) != (self.lo == 0):
            raise ValueError("the front stage takes tokens, a later stage the carry")
        if block.slot != self.slot_id:
            raise ValueError("a stage serves one slot" if self.slots == 1 else "another slot's block")
        eng, st, M = self.eng, self.st, block.rows
        if st.length + M > st.cap:
            raise ValueError(f"{st.length + M} rows exceed the state's capacity {st.cap}")
        if self.store is not None:
            self.store.write(st.length, st.length + M)
        if carry is not None:
            carry = carry.to(eng.device)
            if tuple(carry.shape) != (M,) + tuple(self.carry_dims):
                raise ValueError(f"a carry of {tuple(carry.shape)} for {M} rows of {self.carry_dims}")
        if block.chain and block.graph != STEP:
            return self._chunk(block, carry)
        if block.chain:
            if M != 1:
                raise ValueError("a decode step is one row")
            if st.length == 0:
                raise ValueError("a decode step follows a prompt chunk")
            return self._step(block, carry)
        return self._verify(block, carry)

    def _chunk(self, block: Block, carry):
        """A prompt chunk as `FlashNextEngine.prefill` runs one: the parked rows landed, the chunk
        through the prefill kernels."""
        eng, st = self.eng, self.st
        if block.rows > PREFILL_CHUNK:
            raise ValueError(f"a prompt chunk of {block.rows} rows (the prefill kernels' chunk is {PREFILL_CHUNK})")
        self._tree = None
        eng.flush(st)
        tokens = torch.tensor(block.tokens, dtype=torch.long, device=eng.device)
        if carry is not None:
            carry = tuple(carry[:, i].contiguous() for i in range(self.parts))
            carry = (carry[0], carry[1] if self.parts > 1 else None)
        out = eng.forward_pre(tokens, st, head=block.head, carry=carry)
        if st.length > 4096 and str(eng.device).startswith("cuda"):
            torch.cuda.empty_cache()                 # as `FlashNextEngine.prefill`
        if self.hi < self.layers:
            return self._join(*out)
        self.hidden, self.picks = eng.stack, None
        return out

    def _step(self, block: Block, carry):
        """One decode token: the engine's decode graph of the state's class, or its eager step."""
        eng, st, sv = self.eng, self.st, self.sv
        self._tree = None
        if carry is not None:
            self._take(1, carry)
        cls = eng.qsa_class(st.length)
        if self.graphs:
            new = sv.dg is None or cls not in sv.dg.graphs
            lg = self._base.forward(sv, torch.tensor(block.tokens), st.length).view(1, -1)
            if new:
                # the graph's own N: the tensor its capture wrote, which every replay writes again
                sv.dg.__dict__.setdefault("carries", {})[cls] = eng.rec.get("carry")
            h, n = sv.dg.stacks[cls], sv.dg.carries[cls]
        else:
            lg = eng.decode_eager(block.tokens[0], st)
            h, n = eng.stack, eng.rec.get("carry")
            sv._hidden = h.view(1, 1, -1)
            sv._h_last.copy_(h[-1:])
            sv.stats["decode_steps"] += 1
        if self.hi < self.layers:
            return self._join(h, n)
        self.hidden, self.picks = h, eng.g["next"]
        return None if block.head == "none" else lg

    def _verify(self, block: Block, carry):
        """A draft tree: the engine's verify graph of its rows and class, or its eager verify.
        Nothing but the block's own cache rows is written until `commit`."""
        eng, st, sv, M = self.eng, self.st, self.sv, block.rows
        if carry is not None:
            self._take(M, carry)
        if self.graphs:
            lg = self._base.forward_tree(sv, torch.tensor(block.tokens), block.parents, st.length)
        else:
            eng.verify(block.tokens, block.parents, st, None)
            rb = eng._rb[M]
            sv._eager_block, sv._width, sv.captured = True, M, False
            sv.picks = rb["next"][:M]
            sv._hidden = eng.rec["stack"][:M].unsqueeze(0)
            sv.stats["verify_blocks"] += 1
            sv.stats["verify_rows"] += M
            lg = rb["logits"][:M]
        self._tree = M
        if self.hi < self.layers:
            return self._join(eng.rec["stack"][:M], eng.rec.get("carry"))
        self.hidden, self.picks = sv._hidden[0], sv.picks
        return None if block.head == "none" else lg

    @torch.no_grad()
    def commit(self, path: list) -> None:
        if self._tree is None:
            if path:
                raise ValueError("no block to commit")
            return
        path = [int(i) for i in path]
        paths = self.eng._block[2]
        if path and (not 0 <= path[-1] < self._tree or list(paths[path[-1]]) != path):
            raise ValueError(f"{path} is not a root path of the last block")
        self._base.commit_tree(self.sv, path)
        self._tree = None

    def state(self) -> dict:
        eng, st = self.eng, self.st
        if eng.parked(st):
            eng.flush(st)                            # the folded commit's rows, into the sections
        n = st.length
        out = dict(FNModelState(st).sections())
        for li in sorted(st.k):
            out[f"k.{li}"] = st.k[li][:, :n]
            out[f"v.{li}"] = st.v[li][:, :n]
        for li in sorted(st.ik):
            out[f"ik.{li}"] = st.ik[li][:n]
        return out

    @torch.no_grad()
    def warm(self) -> None:
        """The kernels' first launches on this stage's own layers, before a link's deadline counts
        them: a prompt chunk, a decode step, a verify block and its commit, on rows of zeros (with
        graphs, the dense class's are captured here); then a decode step and a verify block past
        the dense regime, eager, so the sparse attention's kernels have run once."""
        from tools import fn_seq_kernels as SK
        eng, st = self.eng, self.st
        W = eng.cfg.hc_count * eng.cfg.hidden_size

        def carry(rows: int):
            return None if self.lo == 0 else torch.zeros(rows, self.parts, W, dtype=eng.dtype, device=eng.device)
        self.reset()
        self.rows(Block([0] * 8, None, head="last"), carry(8))
        self.rows(Block([0], None, graph=STEP), carry(1))
        self.rows(Block([0, 0, 0, 0], [-1, 0, 1, 1]), carry(4))
        self.commit([0, 1, 3])
        # the first rows of the first sparse class: a block past the dense budget
        far = eng.cfg.indexer_compress_ratio * (SK.TOPK + 2) if eng.cfg.has_indexer else 0
        if far and far + 8 <= st.cap and eng.qsa_class(far):
            graphs, self.graphs = self.graphs, False
            try:
                eng.flush(st)
                st.length = far                      # rows of zeros behind it: nobody's sequence
                self.rows(Block([0], None, graph=STEP), carry(1))
                self.rows(Block([0, 0, 0, 0], [-1, 0, 1, 1]), carry(4))
                self.commit([0, 1, 3])
            finally:
                self.graphs = graphs
        if str(eng.device).startswith("cuda"):
            torch.cuda.synchronize()
        self.reset()

    # the host staging: one buffer, pinned beside a CUDA device, a carry is copied through
    def _stage(self, nbytes: int) -> torch.Tensor:
        if self._host is None or self._host.numel() < nbytes:
            self._host = torch.empty(max(nbytes, 1 << 16), dtype=torch.uint8,
                                     pin_memory=str(self.eng.device).startswith("cuda"))
        return self._host[:nbytes]

    def staging(self, nbytes: int):
        return self._stage(nbytes).numpy()

    def encode(self, t) -> tuple:
        t = t.detach()
        host = self._stage(t.numel() * t.element_size())
        host.view(t.dtype).view(t.shape).copy_(t)
        return L.DTYPE_CODES[str(t.dtype).replace("torch.", "")], tuple(t.shape), host.numpy()

    def decode(self, dtype: int, dims: tuple, buf):
        kind = getattr(torch, L.DTYPES[dtype][0])
        host = torch.frombuffer(buf, dtype=torch.uint8).view(kind).view(dims)
        return host.to(self.eng.device) if str(self.eng.device).startswith("cuda") else host.clone()


def load_stage(path: str, lo: int, hi: int, device="cuda", mtp: bool = False, log=print):
    """(idx, W, banks, tables, timing) of the layers [lo, hi) of the pack at `path`: the engine's
    own loader with its layer subset (`load.load(layers=...)`), so every weight is in the form the
    whole model's is. Beside its layers a stage reads what the loader reads for any subset (the
    embedding, the head, the final mixer), the front the norm of layer hi (the N of its carry), and
    with `mtp` the draft layer. Only a stage that holds a PLE layer keeps the n-gram table."""
    from engine.models.flashnext import load as LD
    from engine.models.flashnext.config import load_flashnext_config
    c = load_flashnext_config(path)
    if not 0 <= lo < hi <= c.num_hidden_layers:
        raise ValueError(f"no layers [{lo}, {hi}) in a model of {c.num_hidden_layers}")
    own = [li for li in c.ple_layers if lo <= li < hi]
    warm = LD.WARM_TABLE
    if not own:
        LD.WARM_TABLE = False                # this stage reads no n-gram row: nothing to warm
    try:
        idx, W, banks, tables, timing = LD.load(path, device, log=log, layers=range(lo, hi), mtp=mtp)
    finally:
        LD.WARM_TABLE = warm
    if hi < c.num_hidden_layers and carry_parts(c, hi) > 1:
        name = f"layers.{hi}.attn_hyper_connection.hc_norm.weight"
        W[name] = idx.read(name, device)
    timing["layers"] = [lo, hi]
    return idx, W, banks, {li: tables[li] for li in own if li in tables}, timing


def stage_names(cfg: FlashNextConfig, lo: int, hi: int, mtp: bool = False) -> list:
    """The pack's tensors `load_stage` reads for the layers [lo, hi), by the names of the pack's
    layout (`pack.expected_names`: a projection stored as EXL3 by its `.trellis`)."""
    from engine.models.flashnext.pack import expected_names
    out = []
    for name in expected_names(cfg, mtp):
        if name.startswith("layers."):
            if lo <= int(name.split(".")[1]) < hi:
                out.append(name)
        else:
            out.append(name)
    if hi < cfg.num_hidden_layers and carry_parts(cfg, hi) > 1:
        out.append(f"layers.{hi}.attn_hyper_connection.hc_norm.weight")
    return out


def check_pack(path: str, cut: int) -> dict:
    """The pack at `path` against what the two stages of a cut at `cut` will read, from the files'
    headers alone (no weight is loaded): the whole pack's audit (every tensor of the layout, its
    EXL3 parts and shapes), then per stage every name its loader asks for, and the n-gram table for
    the stage that holds a PLE layer. ValueError names the first thing missing. Returns what the
    pack is and each stage's tensors and bytes."""
    from engine.models.flashnext.pack import PackIndex
    idx = PackIndex.open(path)
    idx.audit(mtp=True)
    c = idx.cfg
    layers = c.num_hidden_layers
    if not 0 < cut < layers:
        raise ValueError(f"no cut at {cut} in a model of {layers} layers")
    out = {"layers": layers, "cut": cut, "hidden": c.hidden_size, "streams": c.hc_count, "vocab": c.vocab_size,
           "ple_layers": sorted(c.ple_layers), "attention_layers": list(c.attention_layers), "notes": list(idx.notes),
           "carry_parts": carry_parts(c, cut),
           "carry_bytes_per_row": carry_parts(c, cut) * c.hc_count * c.hidden_size * 2, "stages": {}}
    for role, lo, hi in (("front", 0, cut), ("tail", cut, layers)):
        n = nbytes = 0
        for name in stage_names(c, lo, hi, mtp=role == "tail"):
            parts = [name]
            if name.endswith(".trellis"):
                parts += [name[:-len("trellis")] + s for s in ("suh", "svh")]
            for part in parts:
                if part not in idx.tensors:
                    raise ValueError(f"{role}: {part} is missing from the pack")
                n, nbytes = n + 1, nbytes + idx.tensors[part].nbytes
        ple = [li for li in c.ple_layers if lo <= li < hi]
        if ple and idx.table is None:
            raise ValueError(f"{role}: it holds the PLE layer {ple[0]} and the pack has no n-gram table")
        out["stages"][role] = {"layers": [lo, hi], "tensors": n, "bytes": nbytes, "ple_layers": ple,
                               "table_bytes": int(idx.files.get("ngram_embedding.safetensors", 0)) if ple else 0,
                               "attention_layers": [li for li in c.attention_layers if lo <= li < hi]}
    return out


def stage_options(a, lo: int, hi: int) -> dict:
    """What a stage [lo, hi) of the model at `a.model` serves with, from the server's flags `a`,
    before any weight is read; ValueError names what a stage does not serve. The limits it returns
    are the ones a result should record."""
    from engine.models.flashnext import layer_major as LM
    from engine.models.flashnext import load as LD
    from engine.models.flashnext.config import load_flashnext_config
    from engine.models.flashnext.engine import PREFILL
    from engine.settings import SETTINGS
    c = load_flashnext_config(a.model)
    layers = c.num_hidden_layers
    if not (0 <= lo < hi <= layers) or (lo, hi) == (0, layers) or (lo != 0 and hi != layers):
        raise ValueError(f"--stage {lo}:{hi}: a stage is the front 0:k or the tail k:{layers} of this "
                         f"model's {layers} layers")
    if SETTINGS.get("FN_KV_FP8") == "1":
        raise ValueError("--stage keeps the bf16 cache: FN_KV_FP8 is not served split")
    if PREFILL != "kernels":
        raise ValueError("--stage runs a prompt on the prefill kernels: FN_PREFILL=kernels")
    cut = hi if lo == 0 else lo
    parts = carry_parts(c, cut)
    ple = sorted(c.ple_layers)
    out = {"slots": 1, "graphs": "captured" if getattr(a, "stage_graphs", "on") != "off" else "eager",
           "caches": "off"}
    if getattr(a, "stage_caches", "off") == "on":
        # ENG-423: state kept under keys on both stages: the caches, and a sequence a slot
        slots = max(1, int(getattr(a, "parallel", 1) or 1))
        if slots > SLOTS_MAX:
            raise ValueError(f"--stage serves at most {SLOTS_MAX} slots: --parallel {slots}")
        keep = getattr(a, "stage_cache_dir", "") or ""
        out.update(slots=slots, caches="on", snapshots_gb=float(getattr(a, "stage_snapshots_gb", SNAPSHOTS_GB)),
                   cache_dir="set" if keep else "none",
                   cache_dir_gb=float(getattr(a, "stage_cache_gb", 0.0) or 0.0) if keep else 0.0,
                   joint_round="one request a round, each slot's block on its own engine")
    return {**out, "kv_cache": "bf16", "images": "refused", "prefill_chunk": PREFILL_CHUNK,
            "prefill_window": f"chunk-major (the one process: {LM.ROWS})", "verify_rows_max": 32,
            "max_len": int(a.max_len), "table": LD.TABLE,
            "ple_layers": {"front": [li for li in ple if li < cut], "tail": [li for li in ple if li >= cut]},
            "carry": f"{'H+N' if parts > 1 else 'H'} bf16, {parts * c.hc_count * c.hidden_size * 2} bytes a row"}


#: the slots a split serves at most, and the bytes a front slot keeps of states by default (GiB)
SLOTS_MAX = 8
SNAPSHOTS_GB = 2.0


def store_options(options: dict | None, a=None, seed: bytes = b"") -> dict | None:
    """The front's store settings from a stage's options and the server's flags (None: no store)."""
    o = options or {}
    if o.get("caches") != "on":
        return None
    return {"bytes": int(float(o.get("snapshots_gb", SNAPSHOTS_GB)) * (1 << 30)),
            "dir": getattr(a, "stage_cache_dir", "") or None,
            "disk_bytes": int(float(o.get("cache_dir_gb", 0.0)) * (1 << 30)), "name": seed.hex()[:32]}


def open_stage(path: str, lo: int, hi: int, max_len: int, device="cuda", log=print, options: dict | None = None,
               serving=None, mtp: bool = False, store: dict | None = None) -> FlashNextRowStage:
    """The stage [lo, hi) of the pack at `path`, on the engine's kernels: only its own layers'
    experts and projections are read. `options`: the stage's (`stage_options`; "graphs": "eager"
    runs every pass without a graph; "slots"). `serving`: the class behind the adapter (the tail's
    own), `mtp`: with the draft layer (the tail's), `store`: a front's kept states
    (`store_options`)."""
    idx, W, banks, tables, timing = load_stage(path, lo, hi, device, mtp=mtp, log=log)
    eng = FlashNextStageEngine(idx.cfg, W, tables, banks, lo, hi, device=device, max_len=max_len)
    ad = FlashNextRowStage(eng, graphs=(options or {}).get("graphs", "captured") != "eager", serving=serving,
                           slots=int((options or {}).get("slots", 1)), store=store)
    ad.info = {"pack": path, "load": timing, "notes": list(idx.notes)}
    return ad


def stage_adapter(model, lo: int, hi: int, cap: int | None = None, graphs: bool = False, serving=None,
                  slots: int = 1, store: dict | None = None) -> StageAdapter:
    """A stage of a model in hand: the reference's (`FlashNextRef`), or for a `FlashNextEngine` a
    stage engine over the same weights (nothing is loaded again), eager unless `graphs`."""
    if isinstance(model, FlashNextEngine):
        cfg = getattr(model, "model_cfg", model.cfg)
        eng = FlashNextStageEngine(cfg, model.W, model.tables, model.banks, lo, hi, device=model.device,
                                   max_len=cap or model.max_len)
        return FlashNextRowStage(eng, graphs=graphs, serving=serving, slots=slots, store=store)
    return FlashNextStage(model, lo, hi)
