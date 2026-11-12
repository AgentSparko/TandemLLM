"""LFM2 MoE as a stage of a split model (ENG-307): the reference path.

A stage runs the layers [lo, hi) of `Lfm2MoeRef` with the reference's own layer code and commit, on
an `LfmState` that holds those layers' conv inputs and KV rows only. The carry is the residual
stream as it leaves layer k - 1, [rows, hidden] in the compute dtype. A block's rows run at once, as
in the reference (`rows`); a prompt chunk is a chain that is kept whole.

`cuts`: every layer boundary. Each layer reads the stream alone.
"""

from __future__ import annotations

import copy

import torch

from engine.methods.stages import Block, StageAdapter
from engine.models.lfm2_moe.arch import ARCH
from engine.models.lfm2_moe.ref import Lfm2MoeRef, LfmState, chain, tree_paths


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


def stage_adapter(model: Lfm2MoeRef, lo: int, hi: int, cap: int = 4096) -> Lfm2MoeStage:
    return Lfm2MoeStage(model, lo, hi, cap)
