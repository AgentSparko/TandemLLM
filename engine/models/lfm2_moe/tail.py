"""LFM2 MoE's tail stage behind the server's generation loop (ENG-307): `Lfm2MoeServing` with every
pass sent through the stage coordinator (engine/methods/stages.py), and the builder the family's
`serve` hands a `--stage` start to. The front stage has no serving adapter: the server runs it from
`stages.open_stage` (server/stage.py).
"""

from __future__ import annotations

import time

import torch

from engine.models.adapter import Capabilities
from engine.models.lfm2_moe.engine import PREFILL_CHUNK
from engine.models.lfm2_moe.serving import CONDUCTOR_FIT, Lfm2MoeServing, _KV, lookup_drafter


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


class _NoSnapshots:
    """A split engine's cache state: there is none, and nothing reads half of one."""

    def _refuse(self, *a, **k):
        raise ValueError("a split engine keeps no snapshots: the caches are off (ENG-307)")

    sections = views = clone = restore = signature = nbytes = _refuse


class Lfm2MoeSplitServing(Lfm2MoeServing):
    """The tail stage of a split model in the server's engine interface (ENG-307). Every pass goes
    through the stage coordinator (engine/methods/stages.py), which asks the front stage for the
    block's rows at the cut and runs the tail's layers and the head on them here.

      forward, one token      a decode step on both stages (stages.py `STEP`)
      forward, more tokens    prompt chunks of PREFILL_CHUNK, as `Lfm2MoeEngine.prefill` cuts them
      forward_tree            a verify block on both stages; `commit_tree` keeps its path

    so a split runs each block on the kernels the engine in one process runs it on. One slot, eager,
    no caches, no rows kept by length: `caps` says so, and what is asked beyond it is refused."""

    caps = Capabilities(caches=False, side_slots=False, anchor_grid=None, shadow=False, joint_verify=False,
                        side_lane=False, joint_plain=False, conductor=CONDUCTOR_FIT)

    def __init__(self, co, max_len: int):
        from engine.models.lfm2_moe import stages
        ad = co.adapter
        self._step = stages.STEP
        self.co, self.ad = co, ad
        self.fn, self.cfg, self.max_len = ad.eng, ad.eng.cfg, max_len
        self.slot = None
        self.st = ad.st
        self.state = _NoSnapshots()
        self.kv = _SplitKV(self)
        self.dg = None
        self.vgraphs, self.cgraphs = {}, {}
        self._graphs = False                 # eager: a stage captures no graph
        self.picks = None
        self.tap = None
        self.mm = None
        self.pos_delta = 0
        self._hidden = None
        self._chain = None
        self._width = 0
        self.commit_ms = 0.0
        self.captured = False
        self.plain_captured = False
        self.heads: dict = {}
        self.sides: list = []
        self._plain_pending = False
        self._replays = co.replays
        self.stats = {"prefill_rows": 0, "decode_steps": 0, "verify_blocks": 0, "verify_rows": 0}

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
        self._plain_pending = False
        self.picks = None
        try:
            self.co.reset()
        except StageError:
            pass

    @torch.no_grad()
    def forward(self, ids: torch.Tensor, start: int, last_only: bool = True) -> torch.Tensor:
        self._settle_chain()
        toks = [int(t) for t in ids.view(-1).tolist()]
        self._at(start)
        T = len(toks)
        if T == 1 and start > 0:
            lg = self._ask(self.co.prefill, toks, "all", graph=self._step)
            self._hidden = self.ad.hidden.view(1, 1, -1)
            self.stats["decode_steps"] += 1
            return lg.view(1, 1, -1)
        rows = []
        for at in range(0, T, PREFILL_CHUNK):
            last = at + PREFILL_CHUNK >= T
            lg = self._ask(self.co.prefill, toks[at:at + PREFILL_CHUNK], "last" if last else "none")
            rows.append(self.ad.hidden)
        self._hidden = (rows[0] if len(rows) == 1 else torch.cat(rows)).unsqueeze(0)
        self.stats["prefill_rows"] += T
        return lg.view(1, 1, -1)

    @torch.no_grad()
    def forward_tree(self, block: torch.Tensor, parents: list[int], start: int) -> torch.Tensor:
        self._settle_chain()
        self._at(start)
        toks = [int(t) for t in block.view(-1).tolist()]
        M = len(toks)
        lg = self._ask(self.co.verify, toks, [int(p) for p in parents])
        self.captured, self._width = False, M
        self.picks = self.ad.picks[:M]
        self._hidden = self.ad.hidden[:M].unsqueeze(0)
        self.stats["verify_blocks"] += 1
        self.stats["verify_rows"] += M
        return lg[:M]

    @torch.no_grad()
    def commit_tree(self, path: list[int]) -> None:
        t0 = time.perf_counter()
        self.co.commit([int(p) for p in path])
        self.commit_ms = (time.perf_counter() - t0) * 1e3

    def tree_batchable(self, parents) -> bool:
        return False

    @staticmethod
    def forward_tree_multi(items: list) -> list:
        raise ValueError("a split engine has one slot: no joint pass")

    def side_slot(self, max_len: int, drafter0=None) -> tuple:
        raise ValueError("a split engine has one slot")

    @torch.no_grad()
    def self_check(self, prompt: list[int], steps: int = 24, log=print) -> dict:
        """`Lfm2MoeEngine.self_check` across the cut: decode `steps` tokens one at a time after a
        prefill, then verify the same tokens as one chain block after the same prefill, and compare
        every row's logits bit for bit. Any difference leaves speculation off."""
        self.restart()
        lg = self.forward(torch.tensor(prompt), 0)
        toks, rows = [int(lg.view(-1).argmax())], []
        for i in range(min(steps, 32)):
            out = self.forward(torch.tensor([toks[-1]]), len(prompt) + i)
            rows.append(out.view(-1).clone())
            toks.append(int(out.view(-1).argmax()))
        self.restart()
        self.forward(torch.tensor(prompt), 0)
        M = len(rows)
        got = self.forward_tree(torch.tensor(toks[:M]), list(range(-1, M - 1)), len(prompt))
        bad = next((i for i in range(M) if not torch.equal(got[i], rows[i])), None)
        self.commit_tree([])
        self.restart()
        res = {"ok": bad is None, "rows": M, "first_diff": bad}
        if log:
            log(f"[lfm-selfcheck] {'PASS' if bad is None else 'FAIL: speculation stays off'} across the cut: {res}")
        return res


def serve_stage(a, ctx, stage) -> dict:
    """The tail stage for the server: its own layers' weights, the link to the front (`stage.attach`,
    the server's), the load-time self-check across the cut. The front is started by the server
    itself from `stages.open_stage` (server/stage.py)."""
    from transformers import AutoTokenizer

    from engine.models.lfm2_moe import stages
    from engine.spec import Relax
    ctx.keys(a.model, [a.model])
    tok = AutoTokenizer.from_pretrained(a.model)
    ad = stages.open_stage(a.model, stage.lo, stage.hi, a.max_len, log=ctx.log)
    ctx.mark("weights")
    srv = Lfm2MoeSplitServing(stage.attach(ad), a.max_len)
    ctx.mark("engine")
    text = tok.apply_chat_template([{"role": "user", "content": "Count from one to twenty in words."}],
                                   tokenize=False, add_generation_prompt=True)
    res = srv.self_check(tok(text, add_special_tokens=False)["input_ids"], log=ctx.log)
    drafter = None
    if a.drafter != "none":
        if res["ok"]:
            drafter = lookup_drafter(srv, a.model, a.corpus, ctx.log)
            a.k = max(int(a.k or 0), drafter.cut.max_rows - 1)
        else:
            ctx.log("[server] LFM2 self-check FAILED across the cut: speculation off, plain decode only")
    ctx.mark("drafters")
    info = ad.info
    fmt = "exl3" if info["exl3"] else "bf16"
    describe = (f"LFM2 MoE {a.model} ({fmt}), tail stage layers [{stage.lo}, {stage.hi}): load "
                f"{info['load'].get('total_s')} s, drafter {'lookup + StairCut' if drafter is not None else 'none'}, "
                f"KV cache bf16, max_len {a.max_len:,},")
    return {"engine": srv, "tok": tok, "drafter": drafter, "path": a.model, "device": "cuda",
            "cfg": None, "w": None, "describe": describe,
            "serving": {"tree": drafter is not None, "sampled_tree": "det", "relax": Relax(1.0, 1),
                        "weights_info": {"format": fmt, "weights": a.model,
                                         "drafters": "lookup (no draft head)" if drafter is not None else None,
                                         "load_s": info["load"].get("total_s"),
                                         "self_check": res, "kv_cache": "bf16"}}}
