"""Flash-Next as a stage of a split model (ENG-307): the reference path.

A stage runs the layers [lo, hi) of `FlashNextRef` with the reference's own layer code and keeps an
`FNState` that holds those layers' entries only (the state is keyed by layer, so it partitions at
the cut with no new format). The carry is the 4 residual streams as they leave layer k - 1,
[rows, hc * hidden] in the compute dtype. A PLE layer reads the block's tokens, which the header
carries, on whichever side holds it.

A prompt chunk runs as the reference runs it (`forward`): the chunk's rows at once, kept. A verify
block runs row by row, each row on a copy of its parent's state, so a row is the decode step of its
path, bit for bit; `commit(path)` keeps the state behind the path's last row.

`cuts`: every layer boundary. The reference computes each site's norm from the streams, so the
streams alone are the carry at every cut.
"""

from __future__ import annotations

import torch

from engine.methods.stages import Block, StageAdapter
from engine.models.flashnext.arch import ARCH
from engine.models.flashnext.ref import FlashNextRef, FNModelState, FNState


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


def stage_adapter(model: FlashNextRef, lo: int, hi: int) -> FlashNextStage:
    return FlashNextStage(model, lo, hi)
