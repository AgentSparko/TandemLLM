"""Flash-Next's tail stage behind the server's generation loop (ENG-307): `FlashNextServing` with
every pass sent through the stage coordinator (engine/methods/stages.py), and the builder the
family's `serve` hands a `--stage` start to. The front stage has no serving adapter of its own: the
server runs it from `stages.open_stage` (server/stage.py).

The tail holds the layers [k, layers), the final mixer, the head, the draft layer and the sampler.
The draft layer reads the streams as they leave the last layer, which only the tail has, so the
drafter, its cache and its graphs are the one process's, unchanged.
"""

from __future__ import annotations

import torch

from engine.models.adapter import Capabilities
from engine.models.flashnext.engine import PREFILL_CHUNK
from engine.models.flashnext.serving import FlashNextCacheState, FlashNextServing, _KV, draft_setup


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
    ad = stages.open_stage(a.model, stage.lo, stage.hi, a.max_len, device=DEVICE, log=ctx.log,
                           options=stage.options, serving=FlashNextSplitServing, mtp=spec)
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
    describe = (f"Flash-Next {a.model}, tail stage layers [{stage.lo}, {stage.hi}): load "
                f"{info['load'].get('total_s')} s, drafter "
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
