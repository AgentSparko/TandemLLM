"""Flash-Next's engine on the host, for the CPU tests of its stages (ENG-308).

The engine's kernels are CUDA; no kernel of it runs without a GPU. What does run without one is
everything around them: the loader, the engine class, the staging on the host, the layer loops,
`verify`, `commit`, the folded commit, the graph classes and the serving adapter. This module gives
those their kernels as host stand-ins, so the real classes run on a pack with the real layout:

  host_pack()   a small EXL3 pack in the served layout (tests/test_fn_pack.py's writer at sizes the
                EXL3 blocks and the kernels' fixed widths take: every head 128 wide), six layers: GDN,
                GDN with the PLE layer, attention, GDN, GDN, attention; an MTP layer, the n-gram table
  kernels(mp)   every launch the row path, the prefill path, the commit and the MTP chain make, as a
                torch function of the same inputs, state and control buffers (the weights dequantised
                on the host), and the CUDA calls a capture and a replay make

A stand-in is not the kernel's arithmetic to the bit; it is a deterministic function of what the
kernel reads, writes what the kernel writes, and is row-wise where the kernel is row-invariant. The
prompt-chunk GEMMs depend on the call's row count, as the library GEMMs pick their algorithm by it.
So two runs agree bit for bit exactly when the engine handed every launch the same inputs in the
same order: what a stage must do.

A captured graph replays by running its recorded pass again and writing the results into the
tensors the capture made, which is what a replay does to a graph's own memory: a caller that reads
anything but those tensors and the static buffers reads stale values here, as on the device.
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile

import torch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from fn_tiny import TINY  # noqa: E402

#: the served layout at the smallest sizes the EXL3 blocks take (K and N multiples of 128)
SMALL = dict(
    TINY, vocab_size=256, hidden_size=128, num_hidden_layers=6,
    num_attention_heads=2, num_key_value_heads=1, head_dim=128,
    layer_types=["linear_attention", "linear_attention", "full_attention", "linear_attention",
                 "linear_attention", "full_attention"],
    linear_key_head_dim=128, linear_value_head_dim=128, linear_num_key_heads=1, linear_num_value_heads=2,
    moe_intermediate_size=128, shared_expert_intermediate_size=128, intermediate_size=128,
    ple_layer_ids=[2], ple_embed_dim=16 * 160,
    indexer_n_heads=1, indexer_kv_heads=1, indexer_head_dim=128, indexer_budget=32, indexer_compress_ratio=4,
)
#: complete blocks a row sees densely (the kernels': 512, 2,051 tokens); small here so a short
#: sequence leaves the dense regime and crosses a class edge
TOPK = 6

_PACK = []


def host_pack() -> str:
    """The small pack's folder (written once a process)."""
    if not _PACK:
        import test_fn_pack as TP
        from engine.models.flashnext.config import config_from_dict
        old = TP.MINI, TP.CFG
        TP.MINI, TP.CFG = dict(SMALL), config_from_dict(dict(SMALL))
        try:
            d = tempfile.mkdtemp(prefix="fnstage-")
            TP.build(d)
        finally:
            TP.MINI, TP.CFG = old
        _PACK.append(d)
    return _PACK[0]


# ------------------------------------------------------------------ weights on the host
_DENSE: dict = {}


def dense(w) -> torch.Tensor:
    """A weight as fp32 [N, K]: a tensor, an EXL3 block or group (dequantised), int8 rows."""
    got = _DENSE.get(id(w))
    if got is None or got[0] is not w:
        if torch.is_tensor(w):
            d = w.float()
        elif hasattr(w, "bf16_cached"):
            d = w.dequant().float()
        else:
            d = w.dequant()
        got = _DENSE[id(w)] = (w, d)
    return got[1]


def _experts(bank) -> list:
    """Each expert's (gate, up, down) as fp32 weights, from the bank's kept tensors."""
    got = _DENSE.get(id(bank))
    if got is None or got[0] is not bank:
        from tools.exl3_linear import EXL3Block
        k = bank.keep
        ws = [EXL3Block(k[i], k[i + 1], k[i + 2]).dequant().float() for i in range(0, len(k), 3)]
        got = _DENSE[id(bank)] = (bank, [tuple(ws[i:i + 3]) for i in range(0, len(ws), 3)])
    return got[1]


def _bf(x: torch.Tensor) -> torch.Tensor:
    return x.to(torch.bfloat16)


def _out(y: torch.Tensor, out):
    return y if out is None else out.copy_(y)


# ------------------------------------------------------------------ fn_kernels
def gemv(x, w, out=None):
    return _out(x.float() @ dense(w).T, out)


def gemv_bf16(x, w, out=None):
    return _out(_bf(x.float() @ dense(w).T), out)


def hc_norm(x, w, group, eps, out=None):
    v = x.float().view(x.shape[0], -1, group)
    y = (v * torch.rsqrt((v * v).mean(-1, keepdim=True) + eps)).view(x.shape) * (1 + w.float())
    return _out(y.to(x.dtype), out)


def inject_norm(h0, out, d, R, w, hc, eps):
    inj = 2 * torch.sigmoid(_bf(d[:, R:R + hc]).float() / hc)
    h = _bf(h0.float() + (out.float()[:, None, :] * inj[:, :, None]).reshape(h0.shape))
    return h, (hc_norm(h, w, h.shape[1] // hc, eps) if w is not None else None)


def mix_tail(u, n, hc, out=None):
    x = _bf((torch.sigmoid(u) * n.float()).view(n.shape[0], hc, -1).mean(1))
    return _out(x, out)


def mix_up(d, n, w_up, hc, out=None):
    wu = dense(w_up)
    a = _bf(torch.nn.functional.silu(_bf(d[:, :wu.shape[1]]).float() / hc))
    return mix_tail(a.float() @ wu.T, n, hc, out)


def gated_norm(o, z, w, eps, sigmoid):
    v = o.float()
    v = v * torch.rsqrt((v * v).mean(-1, keepdim=True) + eps) * (1 + w.float())
    g = torch.sigmoid(z.float()) if sigmoid else torch.nn.functional.silu(z.float())
    return _bf(v * g)


def matmul(x, w, *, out=None, **kw):
    y = x.float() @ dense(w).T
    return _out(y if getattr(w, "out_fp32", False) else _bf(y), out)


def dense_rows(x, w, out=None):
    return matmul(x, w, out=out)


def attn_prep(qkv, iqk, pos, qw, kw, cos, sin, kc, vc, ikc, hq, hk, ioff, eps, slot=None, qln=None, iq=None):
    M, D = qkv.shape[0], kc.shape[2]
    slot = pos if slot is None else slot
    turn = 1.0 + 1e-3 * pos.float().view(M, 1, 1)                 # where a row sits (the rotary stand-in)
    x = qkv.float()
    q = x[:, :hq * D].view(M, hq, D) * (1 + qw.float()) * turn
    k = x[:, 2 * hq * D:2 * hq * D + hk * D].view(M, hk, D) * (1 + kw.float()) * turn
    v = x[:, 2 * hq * D + hk * D:2 * hq * D + 2 * hk * D].view(M, hk, D)
    kc.index_copy_(1, slot[:M], _bf(k).transpose(0, 1))
    vc.index_copy_(1, slot[:M], _bf(v).transpose(0, 1))
    if ikc is not None:
        ikc.index_copy_(0, slot[:M], _bf(iqk[:, ioff:]))
    if iq is not None:
        iq[:M].copy_(_bf(iqk.float()[:, :ioff].view(M, -1, ikc.shape[1]) * (1 + qln.float()) * turn))
    return _bf(q)


def gate_mul(o, qkv, D):
    M = o.shape[0]
    y = o.float().reshape(M, -1)
    return _bf(y * torch.sigmoid(qkv.float()[:, y.shape[1]:2 * y.shape[1]]))


def moe_experts(x, ids, bank, order=None):
    ex = _experts(bank)
    M, S = ids.shape
    out = torch.zeros(M * S, x.shape[1], dtype=torch.float32)
    for m in range(M):
        xm = x[m].float()
        for s in range(S):
            e = int(ids[m, s])
            if e < 0:
                continue
            g, u, dn = ex[e]
            out[m * S + s] = (_bf(torch.nn.functional.silu(_bf(g @ xm).float())).float() * _bf(u @ xm).float()) @ dn.T
    return out


def moe_grouped(x, ids, bank, rows=64):
    return moe_experts(x, ids, bank)


def moe_combine(yd, ids, wts, out=None):
    M, S = ids.shape
    return _out(_bf((yd.view(M, S, -1) * wts.float()[:, :, None]).sum(1)), out)


def combine_inject_norm(h0, yd, ids, wts, d, R, w, hc, eps):
    return inject_norm(h0, moe_combine(yd, ids, wts), d, R, w, hc, eps)


def router(logits, E, k, ids=None, wts=None):
    from tools import fn_kernels as K
    i, w = K.router_reference(logits, E, k)
    return i.to(torch.int32), w


# ------------------------------------------------------------------ fn_seq_kernels
def _taps(x, state, src, m: int) -> list:
    return [x[int(s)] if int(s) >= 0 else state[0][:, -1 - int(s)] for s in src[m]]


def gdn_pre(x, conv_state, conv_w, src, ab, a_log, dt_bias, shift=False, out=None):
    M, C, H = x.shape[0], conv_w.shape[0], a_log.numel()
    w, xc = conv_w.float(), x[:, :C].float()
    y = torch.empty(M, C)
    for m in range(M):
        t = _taps(xc, conv_state.float(), src, m)
        y[m] = torch.nn.functional.silu(w[:, 0] * t[0] + w[:, 1] * t[1] + w[:, 2] * t[2] + w[:, 3] * xc[m])
    a = ab.float()
    g = -torch.exp(a_log.float()) * torch.nn.functional.softplus(a[:, :H] + dt_bias.float())
    beta = torch.sigmoid(a[:, H:])
    if shift:
        conv_state.copy_(torch.cat([conv_state[..., 1:], x[:1, :C].T.unsqueeze(0).to(conv_state.dtype)], -1))
    return _out(_bf(y), out), g, beta


def _gdn_row(s, row, key_dim: int, g, beta, rep: int):
    """One row of the delta rule on the state s [H, Dk, Dv] -> (the state after it, the row's out)."""
    H, Dk, Dv = s.shape
    q = row[:key_dim].float().view(-1, Dk).repeat_interleave(rep, 0)
    k = row[key_dim:2 * key_dim].float().view(-1, Dk).repeat_interleave(rep, 0)
    v = row[2 * key_dim:2 * key_dim + H * Dv].float().view(H, Dv)
    q = q * torch.rsqrt((q * q).sum(-1, keepdim=True) + 1e-6) * Dk ** -0.5
    k = k * torch.rsqrt((k * k).sum(-1, keepdim=True) + 1e-6)
    s = s * torch.exp(g).view(H, 1, 1)
    kv = torch.einsum("hkv,hk->hv", s, k)
    s = s + k.unsqueeze(-1) * ((v - kv) * beta.view(H, 1)).unsqueeze(1)
    return s, torch.einsum("hkv,hk->hv", s, q)


def gdn_rows(qkv, key_dim, g, beta, S, scratch, ctl, rep, write, out=None, pre=None):
    M = qkv.shape[0]
    H, Dk, Dv = S.shape[-3:]
    if out is None:
        out = torch.empty(M, H, Dv, dtype=torch.bfloat16)
    if pre is not None:
        pr, pg, pb, pn = pre
        s = S.view(H, Dk, Dv).clone()
        for j in range(int(pn[0])):
            s, _ = _gdn_row(s, pr[j], key_dim, pg[j], pb[j], rep)
        S.view(H, Dk, Dv).copy_(s)
    cur = None
    for m in range(M):
        ld, sv = int(ctl[m, 0]), int(ctl[m, 1])
        s = S.view(H, Dk, Dv).clone() if ld == -2 else (cur if ld == -1 else scratch[ld].clone())
        cur, o = _gdn_row(s, qkv[m], key_dim, g[m], beta[m], rep)
        out[m].copy_(_bf(o))
        if sv >= 0:
            scratch[sv].copy_(cur)
    if write and cur is not None:
        S.view(H, Dk, Dv).copy_(cur)
    return out


def ple_gate(key, qry, val, hc):
    M = key.shape[0]
    H = key.shape[1] // hc
    g = torch.sigmoid((key.float() * qry.float()).view(M, hc, H).sum(-1) * H ** -0.5)
    return _bf((g[:, :, None] * val.float()[:, None, :]).reshape(M, hc * H))


def ple_conv(gvn, gv, state, w_bf16, src):
    w, x = w_bf16.float(), gvn.float()
    y = torch.empty_like(x)
    for m in range(x.shape[0]):
        t = _taps(x, state.float(), src, m)
        y[m] = w[:, 0] * t[0] + w[:, 1] * t[1] + w[:, 2] * t[2] + w[:, 3] * x[m]
    return _bf(gv.float() + torch.nn.functional.silu(y))


def qsa_pool(ikc, slots, posb, kln, cos, sin, eps, out=None):
    v = ikc.float()[slots.long()].mean(1)
    v = v * torch.rsqrt((v * v).mean(-1, keepdim=True) + eps) * (1 + kln.float())
    return _out(_bf(v * (1.0 + 1e-3 * posb.float().view(-1, 1))), out)


def qsa_scores(iq, pool, own, qctl, NB, out=None):
    M = iq.shape[0]
    if out is None:
        out = torch.empty(M, NB, dtype=torch.float32)
    out.fill_(float("-inf"))
    for m in range(M):
        n, own0 = int(qctl[m, 0]), int(qctl[m, 1])
        keys = torch.cat([pool[:min(n, own0)].float(), own[m].float()])[:n]
        out[m, :n] = (iq[m].float() @ keys.T).sum(0)
    return out


def qsa_scores_rows(iq, pool, nblk, NB, block=1024, lean=False):
    M = iq.shape[0]
    out = torch.full((M, NB), float("-inf"))
    for m in range(M):
        n = int(nblk[m])
        out[m, :n] = (iq[m].float() @ pool[:n].float().T).sum(0)
    return out


def attn_rows(q, kc, vc, ctl, anc, sel, bufs=None, stages=2, ks=None, vs=None):
    from tools import fn_seq_kernels as SK
    return _bf(SK.attn_rows_reference(q, kc, vc, ctl, anc, sel))


# ------------------------------------------------------------------ fn_prefill_kernels
def mm_rows(x, w):
    """A library GEMM: its bits depend on the call's rows."""
    return x.float() @ dense(w).T + 1e-4 * (x.shape[0] % 5)


def mix_up_tail(a, w, n, hc, tile=0, out=None):
    return mix_tail(a.float() @ dense(w).T, n, hc, out)


def delta_rule(self, q, k, v, g, beta, S):
    """`FlashNextEngine.delta_rule` of a prompt chunk: the rows in order, from the state."""
    T, H = q.shape[1], S.shape[-3]
    s = S.view(S.shape[-3:]).float().clone()
    out = torch.empty(T, H, v.shape[-1])
    for t in range(T):
        kk = k[0, t].float()
        kk = kk * torch.rsqrt((kk * kk).sum(-1, keepdim=True) + 1e-6)
        s = s * torch.exp(g[0, t]).view(H, 1, 1)
        s = s + kk.unsqueeze(-1) * ((v[0, t].float() - torch.einsum("hkv,hk->hv", s, kk))
                                    * beta[0, t].view(H, 1)).unsqueeze(1)
        out[t] = torch.einsum("hkv,hk->hv", s, q[0, t].float())
    return out.unsqueeze(0), s.view(S.shape)


# ------------------------------------------------------------------ the CUDA calls
class _Stream:
    def __init__(self, name: str):
        self.name = name

    def wait_stream(self, s) -> None:
        pass

    def synchronize(self) -> None:
        pass


class _Event:
    def record(self, *a) -> None:
        pass

    def synchronize(self) -> None:
        pass

    def query(self) -> bool:
        return True


class Cuda:
    """The CUDA calls of a capture and a replay, on the host. A graph keeps the passes run inside its
    capture (`note`) and what they left on the engine; `replay` runs them again and writes the new
    results into the capture's tensors. `captures` and `replays` count; `fail` makes the next
    capture section raise."""

    def __init__(self):
        self.capturing = None
        self.replaying = False
        self.captures = self.replays = 0
        self.fail = None
        self.cur = self.default = _Stream("default")
        cuda = self

        class Graph:
            def __init__(self):
                self.tape, self.done = [], False

            def replay(self):
                if not self.done:
                    raise RuntimeError("replay of a graph that was never captured")
                cuda.replays += 1
                cuda.replaying = True
                try:
                    for eng, call, kept in self.tape:
                        py = dict(eng.__dict__)
                        call()
                        if kept is not None:
                            _refresh(kept, _left(eng))
                        eng.__dict__.clear()                 # a replay moves no host attribute
                        eng.__dict__.update(py)
                finally:
                    cuda.replaying = False

            def reset(self):
                self.tape, self.done = [], False

        @contextlib.contextmanager
        def graph(g, **kw):
            if cuda.fail is not None:
                exc, cuda.fail = cuda.fail, None
                raise exc
            prev, cuda.cur, cuda.capturing = cuda.cur, kw.get("stream") or _Stream("capture"), g
            try:
                yield
            finally:
                cuda.cur, cuda.capturing = prev, None
            g.done = True
            cuda.captures += 1

        @contextlib.contextmanager
        def side(s):
            prev, cuda.cur = cuda.cur, s
            try:
                yield
            finally:
                cuda.cur = prev

        self.calls = {"Stream": lambda *a, **k: _Stream("side"), "stream": side, "Event": _Event,
                      "current_stream": lambda *a, **k: cuda.cur, "default_stream": lambda *a, **k: cuda.default,
                      "set_stream": lambda s: setattr(cuda, "cur", s), "CUDAGraph": Graph, "graph": graph,
                      "synchronize": lambda *a, **k: None, "empty_cache": lambda *a, **k: None}

    def note(self, eng, call, rows: bool) -> None:
        """A pass run inside a capture: kept, a row pass with what it left on the engine (a commit
        and a draft chain write static buffers and the state only)."""
        if self.capturing is not None:
            self.capturing.tape.append((eng, call, _left(eng) if rows else None))


def _left(eng) -> dict:
    """What a row pass leaves for its reader beside the static buffers: the records and the streams."""
    return {"rec": dict(eng.rec), "stack": getattr(eng, "stack", None)}


def _refresh(kept, new) -> None:
    if torch.is_tensor(kept):
        if kept is not new:
            kept.copy_(new)
    elif isinstance(kept, dict):
        for k, v in kept.items():
            _refresh(v, new[k])
    elif isinstance(kept, (tuple, list)):
        for a, b in zip(kept, new, strict=True):
            _refresh(a, b)


def kernels(mp) -> Cuda:
    """Put the host stand-ins in place for one test (`mp`: its monkeypatch). Returns the CUDA calls'
    stand-in, for its counters."""
    from engine import capture_lock
    from engine.models.flashnext import engine as E
    from engine.models.flashnext import stages as ST
    from tools import exl3_linear as X
    from tools import fn_kernels as K
    from tools import fn_prefill_kernels as PK
    from tools import fn_seq_kernels as SK
    from tools import gdn_prefill_kernels as GK
    me = sys.modules[__name__]
    for name in ("gemv", "gemv_bf16", "hc_norm", "inject_norm", "mix_up", "gated_norm", "attn_prep", "gate_mul",
                 "moe_experts", "moe_combine", "combine_inject_norm", "router", "dense_rows"):
        mp.setattr(K, name, getattr(me, name))
    mp.setattr(K, "dense", dense_rows)
    mp.setattr(K, "route_ok", lambda dev: False)
    for name in ("gdn_pre", "gdn_rows", "ple_gate", "ple_conv", "qsa_pool", "qsa_scores", "attn_rows"):
        mp.setattr(SK, name, getattr(me, name))
    mp.setattr(SK, "qsa_select", SK.qsa_select_torch)
    mp.setattr(SK, "TOPK", TOPK)
    for name in ("mm_rows", "mix_up_tail", "mix_tail", "moe_grouped", "qsa_scores_rows"):
        mp.setattr(PK, name, getattr(me, name))
    mp.setattr(PK, "gated_norm_ok", lambda o, z: False)
    mp.setattr(X, "exl3_matmul", matmul)
    mp.setattr(GK, "fused_prefill_refusal", lambda *a, **k: "the host")
    mp.setattr(E.FlashNextEngine, "delta_rule", delta_rule)
    cuda = Cuda()
    for k, v in cuda.calls.items():
        mp.setattr(torch.cuda, k, v, raising=False)
    mp.setattr(capture_lock, "_new_stream", lambda: _Stream("leased"))
    zeros = torch.zeros
    mp.setattr(torch, "zeros", lambda *a, pin_memory=False, **k: zeros(*a, **k))

    def taped(fn, rows: bool):
        def run(self, *a, **k):
            out = fn(self, *a, **k)
            cuda.note(self, lambda: fn(self, *a, **k), rows)
            return out
        run.__wrapped__ = fn
        return run
    for cls, name in ((E.FlashNextEngine, "rows_step"), (E.FlashNextEngine, "commit_dev"),
                      (ST.FlashNextStageEngine, "rows_step"), (E.FlashNextEngine, "mtp_chain_dev")):
        mp.setattr(cls, name, taped(cls.__dict__[name], name == "rows_step"))
    return cuda


def whole(max_len: int = 256, mtp: bool = True):
    """The engine in one process on the small pack: (`FlashNextEngine`, the pack's index)."""
    from engine.models.flashnext import engine as E
    from engine.models.flashnext.load import load
    idx, W, banks, tables, _ = load(host_pack(), "cpu", log=None, mtp=mtp)
    return E.FlashNextEngine(idx.cfg, W, tables, banks, device="cpu", max_len=max_len), idx
