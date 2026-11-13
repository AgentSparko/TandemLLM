"""LFM2 MoE as a stage of a split model (ENG-307).

Two adapters, one interface (engine/methods/stages.py `StageAdapter`):

  Lfm2MoeStage      the reference path: the layers [lo, hi) of `Lfm2MoeRef` with the reference's own
                    layer code and commit. The carry is the residual stream as it leaves layer
                    k - 1, [rows, hidden] in the compute dtype.
  Lfm2MoeRowStage   the engine's path: the layers [lo, hi) of `Lfm2MoeEngine` on its row kernels and
                    its prompt-chunk kernels, eager. The engine a stage runs is the engine itself,
                    built over a config whose layer lists hold the stage's own layers
                    (`stage_config`), so it loads, keeps and commits only those. `rows_range` and
                    `chunk_range` are `Lfm2MoeEngine._rows` and `forward_chunk` with the layer loop
                    over [lo, hi): the loop bodies are the engine's, statement for statement
                    (tests/test_lfm2_moe_stage.py compares the syntax trees). The carry is what the
                    engine holds at a layer boundary, the stream H and the next layer's norm N of
                    it, [rows, 2, hidden] bf16.

A block is run the way the engine in one process runs it: a prompt chunk on the chunk kernels, one
decode token (`STEP`) on the row kernels with the conv states shifted in place, a draft tree on the
row kernels with its commit left to `commit`. So a split writes the tokens the one process writes.

`cuts`: every layer boundary. Each layer reads the stream alone.
"""

from __future__ import annotations

import copy
import dataclasses
import re

import torch
import torch.nn.functional as F

from engine.methods import link as L
from engine.methods.stages import Block, StageAdapter
from engine.models.lfm2_moe.arch import ARCH
from engine.models.lfm2_moe.config import Lfm2MoeConfig
from engine.models.lfm2_moe.ref import Lfm2MoeRef, LfmState, chain, tree_paths

#: a chain block's tag for one decode token: the row kernels, the conv states shifted in place
STEP = 1
_LAYER = re.compile(r"layers\.(\d+)\.")


class Lfm2MoeStage(StageAdapter):
    family = ARCH.name

    def __init__(self, ref: Lfm2MoeRef, lo: int, hi: int, cap: int = 4096):
        c = ref.cfg
        if not 0 <= lo < hi <= c.num_hidden_layers:
            raise ValueError(f"no layers [{lo}, {hi}) in a model of {c.num_hidden_layers}")
        # a copy of its own: the reference keeps the last block's rows for the commit on itself
        self.ref = copy.copy(ref)
        self.lo, self.hi, self.layers, self.cap = lo, hi, c.num_hidden_layers, cap
        self.cuts = tuple(range(1, self.layers))
        self.carry_dtype = str(ref.dtype).replace("torch.", "")
        self.carry_dims = (c.hidden_size,)
        self.reset()

    @property
    def length(self) -> int:
        return self.st.length

    def reset(self) -> None:
        ref, c = self.ref, self.ref.cfg
        st = LfmState(cap=self.cap)
        for li in range(self.lo, self.hi):
            if c.is_attention(li):
                st.k[li] = torch.zeros(c.num_key_value_heads, self.cap, c.head_dim, dtype=ref.dtype,
                                       device=ref.device)
                st.v[li] = torch.zeros_like(st.k[li])
            else:
                st.conv[li] = torch.zeros(c.conv_state_len, c.hidden_size, dtype=ref.dtype, device=ref.device)
        self.st = st
        ref.rec, ref._block = {}, None

    @torch.no_grad()
    def rows(self, block: Block, carry):
        """`Lfm2MoeRef.rows` over this stage's layers."""
        if (carry is None) != (self.lo == 0):
            raise ValueError("the front stage takes tokens, a later stage the carry")
        if block.slot:
            raise ValueError("the reference stage has one slot")
        if block.head == "last":
            raise ValueError("the reference has no last-row head")
        ref, c, st = self.ref, self.ref.cfg, self.st
        tokens = torch.tensor(block.tokens, dtype=torch.long, device=ref.device)
        M = tokens.numel()
        if st.length + M > st.cap:
            raise ValueError(f"{st.length + M} rows exceed the state's capacity {st.cap}")
        parents = chain(M) if block.chain else block.parents
        paths = tree_paths(parents)
        positions = st.length + torch.tensor([len(pa) - 1 for pa in paths], device=ref.device)
        ref.rec = {}
        ref._block = (list(parents), paths)
        h = ref.W["embed_tokens.weight"][tokens].to(ref.dtype) if self.lo == 0 else carry
        for li in range(self.lo, self.hi):
            p = ref.pre(li)
            n = ref.rms(h, ref.w(p + "operator_norm.weight"))
            if c.is_attention(li):
                h = h + ref.attention(n, li, st, paths, positions)
            else:
                h = h + ref.conv(n, li, st, paths)
            n = ref.rms(h, ref.w(p + "ffn_norm.weight"))
            h = h + (ref.moe(n, li) if c.is_moe(li) else ref.mlp(n, li))
        if block.chain:
            ref.commit(st, list(range(M)))
        if self.hi < self.layers:
            return h
        if block.head == "none":
            return None
        h = ref.rms(h, ref.w("embedding_norm.weight"))
        return ref.lin(h, ref.head_weight()).float()

    def commit(self, path: list) -> None:
        if self.ref._block is None:
            if path:
                raise ValueError("no block to commit")
            return
        if any(not 0 <= i < len(self.ref._block[0]) for i in path):
            raise ValueError(f"{path} is not a root path of the last block")
        self.ref.commit(self.st, path)

    def state(self) -> dict:
        st, n = self.st, self.st.length
        out = {f"conv.{li}": st.conv[li] for li in sorted(st.conv)}
        for li in sorted(st.k):
            out[f"k.{li}"] = st.k[li][:, :n]
            out[f"v.{li}"] = st.v[li][:, :n]
        return out


# ---------------------------------------------------------------------- the engine's path
class StageConfig(Lfm2MoeConfig):
    """The model's config as a stage's engine reads it: the layer lists hold the layers [lo, hi), so
    the engine builds its conv taps, routers and buffers for those and commits those; every other
    field, the layer count and each layer's kind are the model's."""

    lo = 0
    hi = 0

    def _own(self, layers: list) -> list:
        return [i for i in layers if self.lo <= i < self.hi]

    @property
    def attention_layers(self) -> list:
        return self._own(Lfm2MoeConfig.attention_layers.fget(self))

    @property
    def conv_layers(self) -> list:
        return self._own(Lfm2MoeConfig.conv_layers.fget(self))

    @property
    def moe_layers(self) -> list:
        return self._own(Lfm2MoeConfig.moe_layers.fget(self))


def stage_config(cfg: Lfm2MoeConfig, lo: int, hi: int) -> StageConfig:
    if not 0 <= lo < hi <= cfg.num_hidden_layers:
        raise ValueError(f"no layers [{lo}, {hi}) in a model of {cfg.num_hidden_layers}")
    out = StageConfig(**{f.name: getattr(cfg, f.name) for f in dataclasses.fields(Lfm2MoeConfig)})
    out.lo, out.hi = lo, hi
    return out


def rows_range(self, parts: list, lo: int, hi: int, carry=None, write: bool = False):
    """`Lfm2MoeEngine._rows` over the layers [lo, hi) (`self` is the engine). lo > 0: the rows enter
    as `carry` (H, N). hi below the layer count: returns (H, N) as they leave layer hi - 1, and the
    head does not run."""
    from engine.models.lfm2_moe import kernels as K
    import tools.exl3_linear as _x3
    from tools import gpu as _gpu
    from tools import kv_fp8 as _kv8
    c = self.cfg
    eps = c.rms_norm_eps
    cuts = [0]
    for _, _, M, _ in parts:
        cuts.append(cuts[-1] + M)
    R = cuts[-1]
    sl = [slice(cuts[j], cuts[j + 1]) for j in range(len(parts))]
    one = len(parts) == 1
    if lo == 0:
        tok = parts[0][1]["d"]["tok"] if one else torch.cat([rb["d"]["tok"] for _, rb, _, _ in parts])
        h = self.W["embed_tokens.weight"].index_select(0, tok)
        h, n = K.add_rms(h, None, self.W["layers.0.operator_norm.weight"], eps)
    else:
        h, n = carry
    for li in range(lo, hi):
        p = self.pre(li)
        if c.is_attention(li):
            a = p + "self_attn."
            qkv = self._fused(n, a, "qkv", ("q_proj", "k_proj", "v_proj"))
            o = torch.empty(R, c.num_attention_heads, c.head_dim, dtype=self.dtype, device=self.device)
            for (st, rb, M, ns), r in zip(parts, sl):
                d = rb["d"]
                if li in st.ks:
                    # LFM2_KV_FP8: K and V in bf16 to the staging rows, then coded into the cache at the slots
                    q = K.qk_prep(qkv[r], d["pos"], self._kix[:M], self.W[a + "q_layernorm.weight"],
                                  self.W[a + "k_layernorm.weight"], self.rope_cos, self.rope_sin, self._kst,
                                  self._vst, c.num_attention_heads, c.num_key_value_heads, eps)
                    _kv8.kv_quant2(self._kst, self._vst, self._kix[:M], st.k[li], st.ks[li], st.v[li], st.vs[li],
                                   d["slot"])
                    K.attn_rows(q, st.k[li], st.v[li], d["ctl"], d["anc"], ns, bufs=rb["attn"].setdefault(ns, {}),
                                out=o[r], ks=st.ks[li], vs=st.vs[li])
                    continue
                q = K.qk_prep(qkv[r], d["pos"], d["slot"], self.W[a + "q_layernorm.weight"],
                              self.W[a + "k_layernorm.weight"], self.rope_cos, self.rope_sin, st.k[li], st.v[li],
                              c.num_attention_heads, c.num_key_value_heads, eps)
                # the split buffers of this class: a graph of another class holds its own, so a
                # capture never frees what an earlier graph replays into
                K.attn_rows(q, st.k[li], st.v[li], d["ctl"], d["anc"], ns, bufs=rb["attn"].setdefault(ns, {}),
                            out=o[r])
            out = self.lin(o.view(R, -1), self.W[a + "out_proj.weight"])
        else:
            bcx = self.lin(n, self.W[p + "conv.in_proj.weight"])
            y = torch.empty(R, c.hidden_size, dtype=self.dtype, device=self.device)
            for (st, rb, M, ns), r in zip(parts, sl):
                K.conv_rows(bcx[r], st.conv[li], self._wc[li], rb["d"]["csrc"], rb["bx"][li], y=y[r], write=write)
            out = self.lin(y, self.W[p + "conv.out_proj.weight"])
        h, n = K.add_rms(h, out, self.W[p + "ffn_norm.weight"], eps)
        if c.is_moe(li):
            m = self.moe_rows(n, li)
        else:
            f = p + "feed_forward."
            gu = self._fused(n, f, "w13", ("w1", "w3"))
            I = c.intermediate_size
            w2 = self.W[f + "w2.weight"]
            # the fused silu+rotate+down kernels only where the inline extensions do not
            # cover the arch: on GB10 the two torch ops and the linear keep their bits
            m = (_x3.exl3_down_silu(gu, w2) if type(w2) is _x3.EXL3Block
                 and not _gpu.profile(gu.device).inline("exl3_linear")
                 else self.lin(F.silu(gu[:, :I]) * gu[:, I:], w2))
        nxt = self.W[self.pre(li + 1) + "operator_norm.weight"] if li + 1 < c.num_hidden_layers \
            else self.W["embedding_norm.weight"]
        h, n = K.add_rms(h, m, nxt, eps)
    if hi < c.num_hidden_layers:
        return h, n
    if one:
        rb = parts[0][1]
        rb["hidden"].copy_(n)
        self.head(n, out=rb["logits"])
    else:
        logits = self.head(n)
        for (_, rb, _, _), r in zip(parts, sl):
            rb["hidden"].copy_(n[r])
            rb["logits"].copy_(logits[r])
    for _, rb, _, _ in parts:
        torch.argmax(rb["logits"], dim=-1, out=rb["next"])
        rb["next_h"].copy_(rb["next"], non_blocking=True)
    return None


def chunk_range(self, tokens: torch.Tensor, st: LfmState, lo: int, hi: int, carry=None, head: str = "last"):
    """`Lfm2MoeEngine.forward_chunk` over the layers [lo, hi) (`self` is the engine): the state
    advances. lo > 0: the rows enter as `carry` (H, N). hi below the layer count: returns (H, N);
    else the logits `head` asks for, and the final normed rows are in `self.prompt_hidden`."""
    from engine.models.lfm2_moe import kernels as K
    import tools.exl3_linear as _x3
    from tools import fn_kernels as FK
    from tools import gpu as _gpu
    c = self.cfg
    T, L, eps = tokens.numel(), st.length, c.rms_norm_eps
    if L + T > st.cap:
        raise ValueError(f"{L + T} rows exceed the state's capacity {st.cap}")
    dev = self.device
    pos = torch.arange(L, L + T, device=dev)
    S = c.conv_state_len
    i = torch.arange(T, device=dev)[:, None]
    dist = torch.arange(S, 0, -1, device=dev)[None, :]
    csrc = torch.where(i - dist >= 0, i - dist, -1 - (S - (dist - i))).to(torch.int32).contiguous()
    if lo == 0:
        h = self.W["embed_tokens.weight"].index_select(0, tokens)
        h, n = K.add_rms(h, None, self.W["layers.0.operator_norm.weight"], eps)
    else:
        h, n = carry
    bx = torch.empty(T, c.hidden_size, dtype=self.dtype, device=dev)
    for li in range(lo, hi):
        p = self.pre(li)
        if c.is_attention(li):
            out = self._attn_chunk(n, li, st, pos)
        else:
            bcx = self.lin(n, self.W[p + "conv.in_proj.weight"])
            y = K.conv_rows(bcx, st.conv[li], self._wc[li], csrc, bx)
            st.conv[li].copy_(torch.cat([st.conv[li], bx], 0)[-S:])
            out = self.lin(y, self.W[p + "conv.out_proj.weight"])
        h, n = K.add_rms(h, out, self.W[p + "ffn_norm.weight"], eps)
        if c.is_moe(li):
            logits = F.linear(n, self._gate[li]).float()
            ids, wts = K.router(logits, self._bias.get(li), c.num_experts_per_tok, c.routed_scaling_factor)
            if li in self.banks:
                from tools import fn_prefill_kernels as PK
                yd = PK.moe_grouped(n, ids, self.banks[li], rows=32)   # 64 was checked on Flash-Next only
            else:
                yd = self._moe_bf16_chunk(n, ids, li)
            m = FK.moe_combine(yd, ids, wts) if dev.type == "cuda" else K.combine_reference(yd, ids, wts)
        else:
            f = p + "feed_forward."
            gu = self._fused(n, f, "w13", ("w1", "w3"))
            I = c.intermediate_size
            w2 = self.W[f + "w2.weight"]
            # the fused silu+rotate+down kernels only where the inline extensions do not
            # cover the arch: on GB10 the two torch ops and the linear keep their bits
            m = (_x3.exl3_down_silu(gu, w2) if type(w2) is _x3.EXL3Block
                 and not _gpu.profile(gu.device).inline("exl3_linear")
                 else self.lin(F.silu(gu[:, :I]) * gu[:, I:], w2))
        nxt = self.W[self.pre(li + 1) + "operator_norm.weight"] if li + 1 < c.num_hidden_layers \
            else self.W["embedding_norm.weight"]
        h, n = K.add_rms(h, m, nxt, eps)
    st.length = L + T
    if hi < c.num_hidden_layers:
        return h, n
    self.prompt_hidden = n
    if head == "none":
        return None
    return self.head(n[-1:] if head == "last" else n)


class Lfm2MoeRowStage(StageAdapter):
    """The layers [lo, hi) of an `Lfm2MoeEngine` built over `stage_config`: one sequence, eager.

    After a tail's pass `hidden` holds the block's final normed rows and, for a tree or a decode
    token, `picks` the greedy tokens (device tensors the next pass overwrites)."""

    family = ARCH.name
    carry_dtype = "bfloat16"

    def __init__(self, eng, lo: int, hi: int, cap: int | None = None):
        c = eng.cfg
        if (getattr(c, "lo", None), getattr(c, "hi", None)) != (lo, hi):
            raise ValueError(f"the engine was built for layers [{getattr(c, 'lo', 0)}, "
                             f"{getattr(c, 'hi', c.num_hidden_layers)}), not [{lo}, {hi})")
        if eng.kv_fp8:
            raise ValueError("a stage keeps the bf16 cache: LFM2_KV_FP8 is not served split")
        self.eng, self.model = eng, eng
        self.lo, self.hi, self.layers = lo, hi, c.num_hidden_layers
        self.cap = cap or eng.max_len
        self.cuts = tuple(range(1, self.layers))
        self.carry_dims = (2, c.hidden_size)
        self.hidden = self.picks = None
        self._host = None
        st = LfmState(cap=self.cap)
        for li in c.attention_layers:
            st.k[li] = torch.zeros(c.num_key_value_heads, self.cap, c.head_dim, dtype=eng.dtype, device=eng.device)
            st.v[li] = torch.zeros_like(st.k[li])
        for li in c.conv_layers:
            st.conv[li] = torch.zeros(c.conv_state_len, c.hidden_size, dtype=eng.dtype, device=eng.device)
        self.st = st

    @property
    def length(self) -> int:
        return self.st.length

    def position_class(self, length: int, rows: int) -> int:
        from engine.models.lfm2_moe import kernels as K
        return K.class_of(length + rows, self.cap)

    def reset(self) -> None:
        self.st.reset_inplace()
        self.st.block = None

    @torch.no_grad()
    def rows(self, block: Block, carry):
        if (carry is None) != (self.lo == 0):
            raise ValueError("the front stage takes tokens, a later stage the carry")
        if block.slot:
            raise ValueError("a stage serves one slot")
        eng, st, M = self.eng, self.st, block.rows
        if st.length + M > st.cap:
            raise ValueError(f"{st.length + M} rows exceed the state's capacity {st.cap}")
        ns = self.position_class(st.length, M)
        if carry is not None:
            carry = carry.to(eng.device)
            carry = (carry[:, 0].contiguous(), carry[:, 1].contiguous())
        if block.chain and block.graph != STEP:
            st.block = None
            tokens = torch.tensor(block.tokens, dtype=torch.long, device=eng.device)
            out = chunk_range(eng, tokens, st, self.lo, self.hi, carry, head=block.head)
            if self.hi < self.layers:
                return torch.stack(out, 1)
            self.hidden, self.picks = eng.prompt_hidden, None
            return out
        if block.chain:
            if M != 1:
                raise ValueError("a decode step is one row")
            eng.stage(block.tokens[0], st)
            st.block = None
        else:
            eng.stage_rows(block.tokens, block.parents, st)
        rb = eng._rows_buffers(M)
        out = rows_range(eng, [(st, rb, M, ns)], self.lo, self.hi, carry, write=block.chain)
        if block.chain:
            st.length += 1
        if self.hi < self.layers:
            return torch.stack(out, 1)
        self.hidden, self.picks = rb["hidden"], rb["next"]
        return None if block.head == "none" else rb["logits"]

    @torch.no_grad()
    def commit(self, path: list) -> None:
        st = self.st
        if st.block is None:
            if path:
                raise ValueError("no block to commit")
            return
        if any(not 0 <= i < len(st.block[0]) for i in path):
            raise ValueError(f"{path} is not a root path of the last block")
        self.eng.commit(st, [int(i) for i in path])
        st.block = None

    def state(self) -> dict:
        st, n = self.st, self.st.length
        out = {f"conv.{li}": st.conv[li] for li in sorted(st.conv)}
        for li in sorted(st.k):
            out[f"k.{li}"] = st.k[li][:, :n]
            out[f"v.{li}"] = st.v[li][:, :n]
        return out

    @torch.no_grad()
    def warm(self) -> None:
        """The kernels' first launches on this stage's own layers, before a link's deadline counts
        them: a prompt chunk, a decode step, a verify block and its commit, on rows of zeros."""
        H = self.eng.cfg.hidden_size

        def carry(rows: int):
            return None if self.lo == 0 else torch.zeros(rows, 2, H, dtype=self.eng.dtype, device=self.eng.device)
        self.reset()
        self.rows(Block([0] * 8, None, head="last"), carry(8))
        self.rows(Block([0], None, graph=STEP), carry(1))
        self.rows(Block([0, 0, 0, 0], [-1, 0, 1, 1]), carry(4))
        self.commit([0, 1, 3])
        if self.eng.device.type == "cuda":
            torch.cuda.synchronize()
        self.reset()

    # the host staging: one buffer, pinned beside a CUDA device, a carry is copied through
    def _stage(self, nbytes: int) -> torch.Tensor:
        if self._host is None or self._host.numel() < nbytes:
            self._host = torch.empty(max(nbytes, 1 << 16), dtype=torch.uint8,
                                     pin_memory=self.eng.device.type == "cuda")
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
        return host.to(self.eng.device) if self.eng.device.type == "cuda" else host.clone()


def load_stage(path: str, lo: int, hi: int, device="cuda", dtype=torch.bfloat16, log=print):
    """(idx, W, experts, timing) of the layers [lo, hi) of the checkpoint or pack at `path`, in the
    forms `load.load` gives the whole model (its fused launches and expert banks, statement for
    statement). The front also reads the embedding and layer hi's operator norm (the N of its
    carry), the tail the final norm, the head and the embedding too: the head is the embedding
    when the pack ties them, and the engine names the embedding whenever it asks for the head."""
    import time

    from tools.exl3_linear import EXL3Block
    from engine.models.lfm2_moe.load import ExpertsBF16, ExpertsEXL3, _plain, _proj, apply_splits
    from engine.models.lfm2_moe.pack import PackIndex, plain_names, projections
    t0 = time.time()
    idx = PackIndex.open(path)
    idx.audit()
    if idx.is_exl3:
        idx.check_mul1()
    c = idx.cfg
    last = hi == c.num_hidden_layers
    timing = {"index_s": round(time.time() - t0, 2), "notes": list(idx.notes), "layers": [lo, hi]}
    if idx.is_exl3 and str(device).startswith("cuda"):
        timing["dense_splits"] = apply_splits()

    def own(name: str) -> bool:
        m = _LAYER.match(name)
        return m is not None and lo <= int(m.group(1)) < hi

    W: dict = {}
    for base, _, _ in projections(c, experts=False):
        if own(base):
            W[base + ".weight"] = _proj(idx, base, device, dtype)
    tied = idx.formats.get("lm_head") == "tied"
    keep = {f"layers.{hi}.operator_norm.weight"}
    if lo == 0 or last:
        keep.add("embed_tokens.weight")
    if last:
        keep.add("embedding_norm.weight")
    for name, _ in plain_names(c):
        if own(name) or name in keep:
            W[name] = _plain(idx, name, device, torch.float32 if name.endswith("expert_bias") else dtype)
    if last:
        W["lm_head.weight"] = W["embed_tokens.weight"] if tied else _proj(idx, "lm_head", device, dtype, out_fp32=True)
    # fused launches
    for li in range(lo, hi):
        p = f"layers.{li}."
        if c.is_attention(li):
            a = p + "self_attn."
            ws = [W[a + f"{x}_proj.weight"] for x in ("q", "k", "v")]
            if all(isinstance(w, EXL3Block) for w in ws) and len({w.kb for w in ws}) == 1:
                g = EXL3Block.group(ws, ["q", "k", "v"])
                W[a + "qkv"] = g
                for i, x in enumerate(("q", "k", "v")):
                    W[a + f"{x}_proj.weight"] = g.member(i)
            elif all(torch.is_tensor(w) for w in ws):
                W[a + "qkv"] = torch.cat(ws).contiguous()
        if not c.is_moe(li):
            f = p + "feed_forward."
            w1, w3 = W[f + "w1.weight"], W[f + "w3.weight"]
            if isinstance(w1, EXL3Block) and isinstance(w3, EXL3Block) and w1.kb == w3.kb:
                g = EXL3Block.group([w1, w3], ["w1", "w3"])
                W[f + "w13"] = g
                W[f + "w1.weight"], W[f + "w3.weight"] = g.member(0), g.member(1)
            elif torch.is_tensor(w1) and torch.is_tensor(w3):
                W[f + "w13"] = torch.cat([w1, w3]).contiguous()
    experts: dict = {}
    for li in [x for x in c.moe_layers if lo <= x < hi]:
        f = f"layers.{li}.feed_forward.experts."
        if idx.formats.get(f + "0.w1") == "exl3":
            experts[li] = ExpertsEXL3([tuple(idx.exl3(f + f"{e}.{w}", device) for w in ("w1", "w3", "w2"))
                                       for e in range(c.num_experts)])
        else:
            I, H = c.moe_intermediate_size, c.hidden_size
            w13 = torch.empty(c.num_experts, 2 * I, H, dtype=dtype, device=device)
            w2 = torch.empty(c.num_experts, H, I, dtype=dtype, device=device)
            for e in range(c.num_experts):
                w13[e, :I].copy_(idx.read(f + f"{e}.w1.weight"))
                w13[e, I:].copy_(idx.read(f + f"{e}.w3.weight"))
                w2[e].copy_(idx.read(f + f"{e}.w2.weight"))
            experts[li] = ExpertsBF16(w13, w2)
    timing["total_s"] = round(time.time() - t0, 2)
    if log:
        log(f"[lfm-load] {path}: layers [{lo}, {hi}): {timing}")
    return idx, W, experts, timing


def stage_names(cfg, lo: int, hi: int) -> tuple:
    """(projection bases, plain tensor names) `load_stage` reads for the layers [lo, hi): the
    stage's own layers, with the experts of its MoE layers, and what the first and the last stage
    read beside them."""
    from engine.models.lfm2_moe.pack import plain_names, projections
    last = hi == cfg.num_hidden_layers

    def own(name: str) -> bool:
        m = _LAYER.match(name)
        return m is not None and lo <= int(m.group(1)) < hi
    keep = {f"layers.{hi}.operator_norm.weight"}
    if lo == 0 or last:
        keep.add("embed_tokens.weight")
    if last:
        keep.add("embedding_norm.weight")
    return ([b for b, _, _ in projections(cfg) if own(b)] + (["lm_head"] if last else []),
            [n for n, _ in plain_names(cfg) if own(n) or n in keep])


def check_pack(path: str, cut: int) -> dict:
    """The checkpoint or pack at `path` against what the two stages of a cut at `cut` will read,
    from the files' headers alone (no weight is loaded): the whole pack's audit (every tensor of
    the layout, its shape, the EXL3 parts and rates), then per stage every name its loader asks
    for, present in the form the loader reads. ValueError names the first thing missing. Returns
    what the pack is: its head, its formats, and each stage's tensors and bytes."""
    from engine.models.lfm2_moe.pack import PackIndex
    idx = PackIndex.open(path)
    idx.audit()
    if idx.is_exl3:
        idx.check_mul1()
    c = idx.cfg
    layers = c.num_hidden_layers
    if not 0 < cut < layers:
        raise ValueError(f"no cut at {cut} in a model of {layers} layers")
    head = idx.formats["lm_head"]
    out = {"layers": layers, "cut": cut, "head": head, "exl3": idx.is_exl3, "notes": list(idx.notes),
           "hidden": c.hidden_size, "vocab": c.vocab_size, "stages": {}}
    for role, lo, hi in (("front", 0, cut), ("tail", cut, layers)):
        bases, plain = stage_names(c, lo, hi)
        n = nbytes = 0
        for base in bases:
            fmt = idx.formats.get(base)
            if base == "lm_head" and fmt == "tied":
                continue
            if fmt is None:
                raise ValueError(f"{role}: {base} is not in the pack's audit")
            parts = [base + ".weight"] if fmt != "exl3" else [
                base + ".trellis", base + (".suh" if base + ".suh" in idx.tensors else ".su"),
                base + (".svh" if base + ".svh" in idx.tensors else ".sv")]
            for name in parts:
                if name not in idx.tensors:
                    raise ValueError(f"{role}: {name} is missing from the pack")
                n, nbytes = n + 1, nbytes + idx.tensors[name].nbytes
        for name in plain:
            if name not in idx.tensors:
                raise ValueError(f"{role}: {name} is missing from the pack")
            n, nbytes = n + 1, nbytes + idx.tensors[name].nbytes
        out["stages"][role] = {"layers": [lo, hi], "projections": len(bases), "plain": len(plain), "tensors": n,
                               "bytes": nbytes}
    return out


def stage_options(a, lo: int, hi: int) -> dict:
    """What a stage [lo, hi) of the model at `a.model` serves with, from the server's flags `a`,
    before any weight is read; ValueError names what a stage does not serve. The limits it returns
    are the ones a result should record."""
    from engine.models.lfm2_moe import kernels as K
    from engine.models.lfm2_moe.config import load_lfm2_moe_config
    from engine.models.lfm2_moe.engine import PREFILL_CHUNK, ROWS_MAX
    from engine.settings import SETTINGS
    c = load_lfm2_moe_config(a.model)
    layers = c.num_hidden_layers
    if not (0 <= lo < hi <= layers) or (lo, hi) == (0, layers) or (lo != 0 and hi != layers):
        raise ValueError(f"--stage {lo}:{hi}: a stage is the front 0:k or the tail k:{layers} of this "
                         f"model's {layers} layers")
    if SETTINGS.get("LFM2_KV_FP8") == "1":
        raise ValueError("--stage keeps the bf16 cache: LFM2_KV_FP8 is not served split")
    return {"slots": 1, "graphs": "eager", "caches": "off", "kv_cache": "bf16", "prefill_chunk": PREFILL_CHUNK,
            "verify_rows_max": ROWS_MAX + 1, "attention_chunk": K.CHUNK, "max_len": int(a.max_len),
            "carry": f"H+N bf16, {2 * 2 * c.hidden_size} bytes a row"}


def open_stage(path: str, lo: int, hi: int, max_len: int, device="cuda", log=print) -> Lfm2MoeRowStage:
    """The stage [lo, hi) of the checkpoint or pack at `path`, on the engine's kernels: only its
    own layers' weights are read."""
    from engine.models.lfm2_moe.engine import Lfm2MoeEngine
    idx, W, experts, timing = load_stage(path, lo, hi, device, log=log)
    eng = Lfm2MoeEngine(stage_config(idx.cfg, lo, hi), W, experts, device=device, max_len=max_len)
    ad = Lfm2MoeRowStage(eng, lo, hi)
    ad.info = {"pack": path, "load": timing, "formats": idx.notes, "exl3": idx.is_exl3, "quant": idx.quant}
    return ad


def stage_adapter(model, lo: int, hi: int, cap: int = 4096) -> StageAdapter:
    """A stage of a model in hand: the reference's (`Lfm2MoeRef`), or for an `Lfm2MoeEngine` a stage
    engine over the same weights (nothing is loaded again)."""
    if hasattr(model, "rows_step"):
        eng = type(model)(stage_config(model.cfg, lo, hi), model.W, model.experts, device=model.device,
                          max_len=model.max_len)
        return Lfm2MoeRowStage(eng, lo, hi)
    return Lfm2MoeStage(model, lo, hi, cap)
