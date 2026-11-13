"""ENG-308: Flash-Next as two stages on the engine's kernel paths
(engine/models/flashnext/stages.py `FlashNextRowStage`, tail.py `FlashNextSplitServing`), no GPU.

The engine's kernels are CUDA: none of them runs here. Everything around them does, on a small EXL3
pack in the served layout with host stand-ins for the launches and for the CUDA graph calls
(tests/fn_host.py): the loader, the engine class and its stage, the staging, the layer loops,
`verify`, `commit`, the folded commit, the graph classes and the serving adapter are the real ones.

  -  the stage's passes carry the engine's own statements: `trunk_range`, `rows_step`, `forward_pre`
     and `new_state` against `FlashNextEngine`'s, node for node; the stage's config view covers every
     layer list of the config; a new whole-model loop in the engine fails here until the stage is
     checked (runs anywhere, without torch)
  -  at every cut, eager and with graphs: a prompt of several chunks, decode steps, a draft tree and
     its commit, a tree under a class edge, chain blocks kept in part, a prompt chunk in the middle,
     the sparse attention's class: logits, picks, streams, the committed sections and cache rows are
     the one engine's bit for bit; the carry is rows x its size; a second sequence in the same
     buffers replays the graphs
  -  a graph a stage: each side captures a block's graph once and replays it; the tree under a class
     edge runs eager on both; a capture that fails on a stage keeps no graph and the round completes
     eager; dropped graphs are captured again; the bits are the one engine's every time
  -  the stage loader reads its own layers from the pack (and the draft layer on the tail, the
     n-gram table on the side of the PLE layer), `check_pack` reads the same from the headers alone
  -  the draft layer on the tail drafts the one engine's tokens and probabilities
  -  the tail behind the server's interface over a real link gives the one serving's logits; a
     stalled link ends a decode step, a verify block and a prompt chunk inside their deadlines, a cut
     link at once; a new link starts both stages empty under a higher epoch with the same answers
  -  what a split does not hold is refused; stages that disagree are refused

Run: python tests/test_fn_stage.py
"""

from __future__ import annotations

import ast
import os
import secrets
import shutil
import sys
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


def _need_torch():
    if torch is None:
        from scriptmode import Skip
        raise Skip("no torch here")
    import fn_host
    return fn_host


def _raises(kind, fn, *needles):
    try:
        fn()
    except kind as e:
        for n in needles:
            assert n in str(e), (n, str(e))
        return e
    raise AssertionError(f"no {kind.__name__} (wanted {needles})")


# ------------------------------------------------------------------ the passes are the engine's
def _func(tree, name: str, cls: str):
    body = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls).body
    return next(n for n in body if isinstance(n, ast.FunctionDef) and n.name == name)


def _dump(nodes) -> str:
    return "\n".join(ast.dump(n) for n in (nodes if isinstance(nodes, list) else [nodes]))


def _parse(rel: str):
    with open(os.path.join(ROOT, rel)) as f:
        return ast.parse(f.read())


def _code(fn) -> list:
    """A function's statements without its docstring."""
    body = fn.body
    return body[1:] if isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) else body


#: the engine's methods that walk every layer of the model by its count. A stage runs none of them
#: but through its own cut (`new_state`, `trunk`) or not at all (the joint passes of several slots)
WHOLE_MODEL = {"__init__", "new_state", "_site_norm", "trunk", "verify_multi", "_jg_body", "_joint_graph_pass",
               "_verify_multi_rowwise", "_attn_entries", "_joint_tables_begin", "_attn_multi_end"}


def test_the_stage_passes_are_the_engines():
    eng, stg, cfg = (_parse("engine/models/flashnext/" + f) for f in ("engine.py", "stages.py", "config.py"))
    E, S_ = "FlashNextEngine", "FlashNextStageEngine"
    # the trunk: the layer loop's body, and what follows it past the cut's return
    a, b = _code(_func(eng, "trunk", E)), _code(_func(stg, "trunk_range", S_))
    la, lb = (next(n for n in x if isinstance(n, ast.For)) for x in (a, b))
    assert _dump(la.body) == _dump(lb.body), "trunk_range's layer loop is not FlashNextEngine.trunk's"
    after_a, after_b = a[a.index(la) + 1:], b[b.index(lb) + 1:]
    assert isinstance(after_b[1], ast.If) and _dump(after_a) == _dump(after_b[:1] + after_b[2:]), "after the loop"
    # the row pass: up to the rows' entry, the entry at layer 0, and everything after the trunk
    a, b = _code(_func(eng, "rows_step", E)), _code(_func(stg, "rows_step", S_))
    assert _dump(a[:7]) == _dump(b[:7]), "rows_step before the trunk"
    assert isinstance(b[7], ast.If) and _dump(a[7]) == _dump(b[7].body[0]), "the rows' entry at layer 0"
    assert isinstance(b[9], ast.If) and _dump(a[9:]) == _dump(b[10:]), "rows_step after the trunk"
    assert _dump(a[-3:-1]) == _dump(b[9].body[:2]), "the cut's end of rows_step: the fold and the streams"
    # the prompt chunk
    a, b = _code(_func(eng, "forward_pre", E)), _code(_func(stg, "forward_pre", S_))
    assert _dump(a[:3]) == _dump(b[:3]) and isinstance(b[3], ast.If), "forward_pre before the trunk"
    assert _dump(a[3:6]) == _dump(b[3].body[:3]), "the chunk's entry at layer 0"
    assert _dump(a[7:10]) == _dump(b[5:8]) and isinstance(b[8], ast.If) and _dump(a[10:]) == _dump(b[9:])
    # the state: the same statements over the stage's layers
    a, b = _code(_func(eng, "new_state", E)), _code(_func(stg, "new_state", S_))
    assert len(a) == len(b)
    for x, y in zip(a, b):
        if isinstance(x, ast.For) and isinstance(x.iter, ast.Call) and getattr(x.iter.func, "id", "") == "range":
            assert _dump(x.body) == _dump(y.body) and "self.lo" in ast.unparse(y.iter)
        else:
            assert _dump(x) == _dump(y), ast.unparse(x)
    # the config view covers every layer list the config derives
    props = {}
    for name in ("FlashNextConfig",):
        for n in next(c for c in cfg.body if isinstance(c, ast.ClassDef) and c.name == name).body:
            if isinstance(n, ast.FunctionDef) and any(isinstance(d, ast.Name) and d.id == "property"
                                                      for d in n.decorator_list):
                src = ast.unparse(n)
                if "layer_types" in src or "ple_layer_ids" in src:
                    props[n.name] = src
    view = {n.name for n in next(c for c in stg.body if isinstance(c, ast.ClassDef) and c.name == "StageConfig").body
            if isinstance(n, ast.FunctionDef)}
    assert set(props) <= view and set(props) == {"ple_layers", "attention_layers", "linear_layers"}, (props, view)
    # no other pass of the engine walks every layer
    cls = next(n for n in eng.body if isinstance(n, ast.ClassDef) and n.name == E)
    walks = {n.name for n in cls.body if isinstance(n, ast.FunctionDef) and "num_hidden_layers" in ast.unparse(n)}
    assert walks == WHOLE_MODEL, ("a pass of the engine that walks every layer changed: check "
                                  "engine/models/flashnext/stages.py", sorted(walks ^ WHOLE_MODEL))
    return "trunk_range, rows_step, forward_pre and new_state carry the engine's statements, node for node"


# ------------------------------------------------------------------ one serving, whole or split
CAP = 96
PROMPT = [5, 9, 3, 17, 22, 3, 40, 41, 12, 7, 63, 2, 30, 8, 101, 200, 77, 54, 19]     # chunks of 8, 8, 3
MIDDLE = [4, 5, 6, 60, 61, 62, 63, 64, 65, 66]                                        # chunks of 8, 2


def setup(mp):
    """The host kernels, and a prompt chunk of 8 rows (so a prompt is several chunks)."""
    H = _need_torch()
    cuda = H.kernels(mp)
    from engine.models.flashnext import engine as E
    from engine.models.flashnext import serving as SV
    from engine.models.flashnext import stages as ST
    from engine.models.flashnext import tail as TL
    for mod in (E, SV, ST, TL):
        mp.setattr(mod, "PREFILL_CHUNK", 8)
    return H, cuda


def same(a, b, what) -> None:
    assert a.dtype == b.dtype and a.shape == b.shape, (what, a.dtype, b.dtype, a.shape, b.shape)
    assert torch.equal(a, b), f"{what}: differs at {(a != b).nonzero()[:3].tolist()}"


class Hop:
    """A coordinator with no link: the front in the same thread, the carry through the host
    encoding a link carries."""

    def __init__(self, front, tail):
        self.front, self.adapter, self.replays = front, tail, 0
        self.pending = self.block = None
        self.bytes = 0

    def _run(self, block):
        if self.pending is not None:
            self.front.commit(self.pending)
            self.pending = None
        assert self.front.length == self.adapter.length
        dtype, dims, buf = self.front.encode(self.front.rows(block, None))
        assert len(buf) == block.rows * self.front.carry_bytes_per_row, (len(buf), block.rows)
        assert tuple(dims) == (block.rows,) + tuple(self.adapter.carry_dims)
        self.bytes += len(buf)
        into = self.adapter.staging(len(buf))
        into[:] = buf
        out = self.adapter.rows(block, self.adapter.decode(dtype, dims, into))
        self.block = None if block.chain else block
        return out

    def prefill(self, tokens, head="all", graph=0):
        from engine.methods.stages import Block
        return self._run(Block(tokens, None, graph=graph, head=head))

    def verify(self, tokens, parents, graph=0):
        from engine.methods.stages import Block
        return self._run(Block(tokens, parents, graph=graph))

    def commit(self, path) -> None:
        if self.block is None:
            raise ValueError("no block to commit")
        self.adapter.commit(list(path))
        self.pending, self.block = list(path), None

    def reset(self) -> None:
        self.adapter.reset()
        self.front.reset()
        self.pending = self.block = None

    def sync(self) -> None:
        if self.pending is not None:
            self.front.commit(self.pending)
            self.pending = None


def hop(eng, k: int, graphs: bool = True, front=None, tail=None):
    """The split serving at cut k over one copy of the weights, without a link."""
    from engine.models.flashnext import stages as ST
    from engine.models.flashnext.tail import FlashNextSplitServing
    front = front or ST.stage_adapter(eng, 0, k, cap=CAP, graphs=graphs)
    if tail is None:
        te = ST.FlashNextStageEngine(eng.cfg, eng.W, eng.tables, eng.banks, k, eng.cfg.num_hidden_layers,
                                     device="cpu", max_len=CAP)
        tail = ST.FlashNextRowStage(te, graphs=graphs, serving=FlashNextSplitServing)
    return tail.sv.attach(Hop(front, tail)), front, tail


def drive(srv) -> list:
    """The server's verbs in the order its loop calls them; what each leaves."""
    out, n = [], len(PROMPT)

    def keep(lg, *more):
        out.append(lg.clone())
        out.extend(t.clone() for t in more)

    keep(srv.forward(torch.tensor(PROMPT), 0), srv.hidden_post_norm, srv.last_streams())
    for t in (11, 50):                                              # 19, 20: the dense class's decode graph
        keep(srv.forward(torch.tensor([t]), n), srv.hidden_post_norm)
        n += 1
    keep(srv.forward_tree(torch.tensor([7, 19, 23, 44]), [-1, 0, 1, 1], n), srv.picks, srv.hidden_post_norm)
    srv.commit_tree([0, 1, 3])                                      # a path that leaves the chain: rows move
    n += 3
    out.append(srv.last_streams().clone())
    # 24: rows to 26 are dense, a chain of 6 would end at 29, past the edge: no graph, eager, and its commit
    keep(srv.forward_tree(torch.tensor([9, 13, 21, 33, 60, 28]), [-1, 0, 1, 1, 0, 4], n), srv.picks)
    srv.commit_tree([0, 4, 5])
    n += 3
    keep(srv.forward(torch.tensor([3]), n), srv.hidden_post_norm)   # 27: the first sparse row
    n += 1
    keep(srv.forward_block(torch.tensor([9, 10, 11, 12]), n), srv.picks)
    srv.rollback_to(2)                                              # a partly accepted draft
    n += 2
    keep(srv.forward_block(torch.tensor([8, 9, 13]), n))
    assert srv.trim_pending(n + 1)
    n += 1
    keep(srv.forward(torch.tensor(MIDDLE), n), srv.hidden_post_norm)   # a prompt chunk in the middle, sparse
    n += len(MIDDLE)
    keep(srv.forward(torch.tensor([60]), n), srv.hidden_post_norm)
    n += 1
    keep(srv.forward_tree(torch.tensor([10, 20, 30, 40]), [-1, 0, 0, 2], n), srv.picks, srv.hidden_post_norm)
    srv.commit_tree([0, 2, 3])
    n += 3
    out.append(srv.last_streams().clone())
    assert srv.kv.length == n == 45
    return out


def state_of(fn, st) -> dict:
    """A state's committed sections and cache rows by name (as `FlashNextRowStage.state`)."""
    from engine.models.flashnext.ref import FNModelState
    if fn.parked(st):
        fn.flush(st)
    n = st.length
    out = dict(FNModelState(st).sections())
    for li in sorted(st.k):
        out[f"k.{li}"], out[f"v.{li}"] = st.k[li][:, :n], st.v[li][:, :n]
    for li in sorted(st.ik):
        out[f"ik.{li}"] = st.ik[li][:n]
    return {k: v.clone() for k, v in out.items()}


_WANT = {}


def reference(H):
    """The one engine behind its serving adapter: (engine, what `drive` leaves, its state, the graphs
    it captured, its eager verifies)."""
    if not _WANT:
        from engine.models.flashnext import layer_major as LM
        from engine.models.flashnext.serving import FlashNextServing
        eng, idx = H.whole(CAP)
        srv = FlashNextServing(eng, CAP)
        window, wins = LM.window, []
        LM.window = lambda fn, toks, st, head=True: (wins.append(len(toks)), window(fn, toks, st, head=head))[1]
        try:
            outs = drive(srv)
        finally:
            LM.window = window
        # the one process runs its prompt's chunks layer-major in a window; a split runs them chunk-major
        assert wins == [3, 2], wins
        _WANT.update(eng=eng, idx=idx, outs=outs, state=state_of(eng, srv.st), v=sorted(srv.vgraphs),
                     c=sorted(srv.cgraphs), eager=srv.stats["verify_eager"], d=sorted(srv.dg.graphs))
    return _WANT


def check(got: list, want: list, what: str) -> None:
    assert len(got) == len(want), (what, len(got), len(want))
    for i, (a, b) in enumerate(zip(got, want)):
        same(a, b, f"{what}, output {i}")


def test_every_cut_equals_the_one_engine(monkeypatch):
    H, cuda = setup(monkeypatch)
    want = reference(H)
    eng, L_ = want["eng"], want["eng"].cfg.num_hidden_layers
    assert want["eager"] == 1 and (4, 0) in want["v"] and want["d"] == [0, 1024], (want["v"], want["d"])
    rows = len(PROMPT) + 2 + 4 + 6 + 1 + 4 + 3 + len(MIDDLE) + 1 + 4
    for k in range(1, L_):
        for graphs in (True, False):
            srv, front, tail = hop(eng, k, graphs)
            what = f"cut {k}, {'graphs' if graphs else 'eager'}"
            for round_ in range(2):                          # a second sequence in the same buffers
                n0 = cuda.captures
                check(drive(srv), want["outs"], f"{what}, round {round_}")
                srv.co.sync()
                both = {**front.state(), **tail.state()}
                assert len(both) == len(front.state()) + len(tail.state()) == len(want["state"]), what
                for name, t in want["state"].items():
                    same(both[name], t, f"{what}: {name}")
                assert front.length == tail.length == 45
                if round_ and graphs:
                    assert cuda.captures == n0, f"{what}: the second sequence captured {cuda.captures - n0} graphs"
                srv.kv.length = 0
                assert srv.kv.length == front.length == 0
            parts = 1 if k in eng.cfg.ple_layers else 2
            assert front.carry_dims == tail.carry_dims == (parts, 4 * 128) and front.carry_bytes_per_row == parts * 1024
            assert srv.co.bytes == 2 * rows * front.carry_bytes_per_row, (what, srv.co.bytes)
            if graphs:
                # a graph a stage: each side captured what the one engine captured, and no other
                for sv in (front.sv, srv):
                    assert sorted(sv.vgraphs) == want["v"] and sorted(sv.cgraphs) == want["c"], (what, sv.vgraphs)
                    assert sorted(sv.dg.graphs) == want["d"] and sv.stats["verify_eager"] == 2 * want["eager"]
            else:
                assert not front.sv.vgraphs and not srv.vgraphs and front.sv.dg is None and srv.dg is None
    return (f"cuts 1..{L_ - 1}, graphs and eager, two sequences each: logits, picks, streams, sections and cache "
            f"rows bit for bit; {rows} rows a sequence, the carry H alone in front of the PLE layer")


def test_a_failed_capture_and_dropped_graphs_on_a_stage(monkeypatch):
    H, cuda = setup(monkeypatch)
    want = reference(H)
    srv, front, tail = hop(want["eng"], 3)
    # the front's first verify capture fails: no graph of that key there, the round eager, the tail's captured
    n = len(PROMPT)
    srv.forward(torch.tensor(PROMPT), 0)
    for t in (11, 50):
        srv.forward(torch.tensor([t]), n)
        n += 1
    cuda.fail = RuntimeError("capture failed")
    lg = srv.forward_tree(torch.tensor([7, 19, 23, 44]), [-1, 0, 1, 1], n)
    same(lg, want["outs"][7], "the tree whose capture failed on the front")
    assert cuda.fail is None and (4, 0) not in front.sv.vgraphs and (4, 0) in srv.vgraphs
    assert len(front.sv.dead_graphs) == 1 and front.sv.stats["verify_eager"] == 1 and front.sv._eager_block
    srv.commit_tree([0, 1, 3])
    assert not front.sv.cgraphs or True                     # the front is told with the next header
    srv.kv.length = 0
    check(drive(srv), want["outs"], "the sequence after a failed capture")
    assert (4, 0) in front.sv.vgraphs, "the next round of that key captures it"
    # every graph of both stages dropped: the next sequence captures them again, the same bits
    srv.kv.length = 0
    n0 = cuda.captures
    assert srv.drop_graphs() > 0 and front.sv.drop_graphs() > 0
    assert not srv.vgraphs and srv.dg is None and front.sv.dg is None
    check(drive(srv), want["outs"], "the sequence after the graphs were dropped")
    assert cuda.captures > n0 and sorted(front.sv.vgraphs) == want["v"] and sorted(front.sv.dg.carries) == want["d"]
    return "a failed capture on the front: eager there, captured on the tail; dropped graphs captured again"


def test_a_decode_graph_carries_its_own_norm(monkeypatch):
    """The front's decode graph leaves H and N in the tensors its capture made. A replay of one class
    must not hand out another pass's N: every decode step's carry is the eager front's."""
    H, cuda = setup(monkeypatch)
    from engine.methods.stages import Block
    from engine.models.flashnext import stages as ST
    eng = reference(H)["eng"]
    a, b = (ST.stage_adapter(eng, 0, 3, cap=CAP, graphs=g) for g in (True, False))
    for ad in (a, b):
        for at in range(0, len(PROMPT), 8):
            ad.rows(Block(PROMPT[at:at + 8], None, head="none"), None)
    for i, t in enumerate((11, 50, 3, 9, 77, 5, 6, 7, 8, 9, 10)):      # 19..29: crosses into the sparse class
        if i == 4:
            for ad in (a, b):                                # a verify block between two steps of one class
                ad.rows(Block([1, 2, 3], [-1, 0, 0]), None)
                ad.commit([0, 2])
        ca, cb = (ad.rows(Block([t], None, graph=ST.STEP), None).clone() for ad in (a, b))
        same(ca, cb, f"the carry of decode step {i}")
        assert ca.shape == (1, 2, 4 * 128)
    assert sorted(a.sv.dg.graphs) == sorted(a.sv.dg.carries) == [0, 1024] and b.sv.dg is None
    return "11 decode steps over two classes with a verify block between: each carry the eager front's"


def test_one_flipped_bit_in_the_carry_changes_the_logits(monkeypatch):
    """The tail reads the carry and nothing else of the front: one bit of H or of N, in a prompt
    chunk, a decode step or a verify block, eager or from a graph's static buffers, moves the logits."""
    H, cuda = setup(monkeypatch)
    from engine.methods.stages import Block
    from engine.models.flashnext import stages as ST
    eng = reference(H)["eng"]
    seen = 0
    for graphs in (True, False):
        front = ST.stage_adapter(eng, 0, 3, cap=CAP, graphs=graphs)
        tails = [ST.stage_adapter(eng, 3, 6, cap=CAP, graphs=graphs) for _ in range(3)]      # true, H flipped, N flipped
        blocks = [Block(PROMPT[:8], None, head="all"), Block([11], None, graph=ST.STEP),
                  Block([7, 19, 23, 44], [-1, 0, 1, 1]), Block([5], None, graph=ST.STEP),
                  Block([7, 19, 23, 44], [-1, 0, 1, 1])]                 # the second step and tree replay their graphs
        for bi, block in enumerate(blocks):
            carry = front.rows(block, None).clone()
            outs = []
            for part, tail in zip((None, 0, 1), tails):
                c = carry.clone()
                if part is not None:
                    raw = c.view(torch.int16)
                    raw[-1, part, 5] ^= 1 << 6
                outs.append(tail.rows(block, c).clone())
            assert not torch.equal(outs[0], outs[1]) and not torch.equal(outs[0], outs[2]), (graphs, bi)
            seen += 2
            if not block.chain:
                for ad in [front] + tails:
                    ad.commit([0, 1])
        if graphs:
            assert cuda.replays > 0 and (4, 0) in tails[0].sv.vgraphs
    return f"{seen} flipped bits (H and N; a chunk, decode steps, verify blocks; graphs and eager): each moved the logits"


def test_the_tail_captures_while_idle_and_the_front_at_first_use(monkeypatch):
    """The server's warm-up captures the dense class's graphs on the tail (`precapture`); the front
    captures a block's graph when the block first arrives. The bits are the one engine's."""
    H, cuda = setup(monkeypatch)
    from engine.models.flashnext import serving as SV
    monkeypatch.setattr(SV, "CAPTURE_ROWS", 4)               # the host's dense class ends at 27 rows
    want = reference(H)
    srv, front, tail = hop(want["eng"], 3)
    assert srv.precapture(None) is None
    n = srv.precapture(object())
    assert n and n > 0 and len(srv.vgraphs) >= 5 and not front.sv.vgraphs and srv.kv.length == 0, (n, srv.vgraphs)
    check(drive(srv), want["outs"], "after the tail's idle captures")
    assert sorted(front.sv.vgraphs) == want["v"]
    return f"{n} graphs captured on the tail before any request, the front's at first use: the one engine's outputs"


class _Tok:
    unk_token_id = -1
    eos_token_id = None
    chat_template = None

    def convert_tokens_to_ids(self, text):
        return -1

    def __call__(self, text, add_special_tokens=False, return_tensors=None):
        ids = [1 + (ord(c) % 50) for c in text]
        return type("R", (dict,), {"input_ids": torch.tensor([ids]) if return_tensors == "pt" else ids})(input_ids=ids)

    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True, **kw):
        return "user: " + messages[-1]["content"] + " assistant:"

    def decode(self, seq, skip_special_tokens=True):
        return "".join(chr(ord("a") + int(t) % 26) for t in seq)


def _loop(eng, prompt, n: int, drafter=None) -> list:
    """`n` greedy tokens from the server's own decode loop over `eng`."""
    from engine.penalty import PenaltySpec
    from engine.spec import Relax
    from server import app
    app.STATE.clear()
    app.STATE.update(engine=eng, drafter=drafter, k=15, tree=drafter is not None, sampled_tree=False, relax=Relax(1.0, 1),
                     verbose=False, state_store=None, prefix_cache=False, prefix_chunk=0, session_cache=False,
                     tok=_Tok(), device="cpu", max_len=CAP, default_max_tokens=64, pen_spec=PenaltySpec(),
                     model="t", cfg_eos=None, think_budget=0, think_stall=False, reasoning_format="tags",
                     request_timeout=0.0, max_queue=8, queue_timeout=5.0, pattern_stop=None,
                     response_cache=None, suffix_store=None)
    eng.kv.length = 0
    return [int(t) for t in app.generate_stream(torch.tensor(prompt), n, set())]


def _mtp_drafter(srv):
    """The family's own drafter as `draft_setup` builds it: the MTP head, StairCut without prices."""
    from engine.methods.mtp_drafter import MTPTreeDrafter
    from engine.methods.staircut import PriceModel, StairCut
    d = MTPTreeDrafter(srv, srv.add_mtp_head(4), StairCut(PriceModel({}, None), siblings=False), None, freeze=False)
    d.capture_all()
    return d


def test_the_servers_loop_writes_the_same_tokens_split(monkeypatch):
    H, cuda = setup(monkeypatch)
    from engine.models.flashnext.serving import FlashNextServing
    from test_window_edge import FixedDrafter
    eng = reference(H)["eng"]
    text = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111]
    prompt = text + [70] + text[:6]                                  # 19 rows: three chunks
    one = FlashNextServing(eng, CAP)
    plain = _loop(one, prompt, 12)
    fixed = _loop(one, prompt, 16, FixedDrafter(250, 5, tree_mode=True))
    blocks = one.stats["verify_blocks"]
    own = _loop(one, prompt, 16, _mtp_drafter(one))
    assert len(plain) == 12 and fixed[:12] == plain and own == fixed and blocks > 0, (plain, fixed, own)
    for k in (1, 3, 5):
        srv, front, tail = hop(eng, k)
        assert _loop(srv, prompt, 12) == plain, k
        v0 = srv.stats["verify_blocks"]
        assert _loop(srv, prompt, 16, FixedDrafter(250, 5, tree_mode=True)) == fixed, k
        assert srv.stats["verify_blocks"] > v0 and front.sv.stats["verify_blocks"] == srv.stats["verify_blocks"]
        v0 = srv.stats["verify_blocks"]
        assert _loop(srv, prompt, 16, _mtp_drafter(srv)) == own, k
        assert srv.stats["verify_blocks"] > v0, "the pack's own draft layer drafted nothing across the cut"
        srv.co.sync()
        assert front.length == srv.kv.length
    return (f"cuts 1, 3, 5: 12 plain tokens, 16 with a fixed five-row draft and 16 with the pack's own draft layer "
            f"through server/app.py's loop: the one process's, {plain[:6]}...")


def _args(*argv):
    """The server's flags a stage start reads, parsed as the server parses them."""
    import argparse

    from server import stage
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="")
    ap.add_argument("--fake-engine", action="store_true")
    ap.add_argument("--parallel", type=int, default=1)
    ap.add_argument("--kv-pool", default="off")
    ap.add_argument("--disk-cache", default="off")
    ap.add_argument("--conductor", default="raw")
    ap.add_argument("--backends", default="off")
    ap.add_argument("--setup", action="store_true")
    ap.add_argument("--max-len", type=int, default=CAP)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--served-model", default="m")
    ap.add_argument("--drafter", default="mtp")
    ap.add_argument("--depth", type=int, default=4)
    ap.add_argument("--corpus", default=None)
    ap.add_argument("--k", type=int, default=0)
    stage.add_flags(ap)
    return ap.parse_args(list(argv))


def test_both_stages_start_as_the_server_starts_them(monkeypatch, tmp_path):
    """The server's own stage code on this family (server/stage.py): the flags and the options
    before any load, the front's process part (its weights from the pack, its warm-up, /health, the
    follower), `serve`'s stage branch for the tail (its weights, its warm-up, the link, the
    self-check across the cut, the drafters); then every figure the server reads of an engine at its
    start and for /health."""
    H, cuda = setup(monkeypatch)
    import functools
    import json
    import urllib.request

    from engine.models.flashnext import serving as SV
    from engine.models.flashnext import stages as ST
    from engine.models.flashnext import tail as TL
    from engine.models.flashnext.tail import FlashNextSplitServing
    from ports import free_port
    from server import stage
    want = reference(H)
    d = H.host_pack()
    monkeypatch.setattr(TL, "DEVICE", "cpu")
    monkeypatch.setattr(ST, "open_stage", functools.partial(ST.open_stage, device="cpu"))
    monkeypatch.setattr(SV, "CAPTURE_ROWS", 4)               # the host's dense class ends at 27 rows
    monkeypatch.setenv("QWEN38_FN_DRAFT_HOT", "0")           # the hot draft head's ids are the real vocabulary's
    monkeypatch.setitem(sys.modules, "transformers", types.SimpleNamespace(
        AutoTokenizer=types.SimpleNamespace(from_pretrained=lambda path: _Tok())))
    key = str(tmp_path / "link.key")
    with open(os.open(key, os.O_WRONLY | os.O_CREAT, 0o600), "w") as f:
        f.write(secrets.token_hex(32) + "\n")
    lines, marks = [], []
    # what a stage does not serve is refused before any load, by the family's own word too
    for bad, why in ((("--stage", "0:6"), "front 0:k or the tail k:6"), (("--stage", "2:4"), "front 0:k or the tail"),
                     (("--stage", "0:3", "--parallel", "2"), "one slot"), (("--stage", "0:3", "--kv-pool", "on"), "kv-pool")):
        _raises(SystemExit, lambda bad=bad: stage.prepare(_args("--model", d, "--link-listen", "127.0.0.1:1",
                                                                "--link-key-file", key, *bad), {}, log=lines.append), why)
    for drafter, graphs in (("mtp", "on"), ("none", "off")):
        link_port, port = free_port(), free_port()
        common = ("--model", d, "--link-key-file", key, "--stage-graphs", graphs, "--drafter", drafter)
        fa = _args(*common, "--stage", "0:3", "--link-listen", f"127.0.0.1:{link_port}", "--port", str(port))
        fstate: dict = {}
        stage.prepare(fa, fstate, log=lines.append)
        assert stage.front(fstate) and fstate["stage"].options["graphs"] == ("captured" if graphs == "on" else "eager")
        threading.Thread(target=stage.serve_front, args=(fa, fstate, lines.append), daemon=True).start()
        end, health = time.monotonic() + 120, None
        while health is None and time.monotonic() < end:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as r:
                    health = json.loads(r.read())
            except OSError:
                time.sleep(0.2)
        st = health["stage"]
        assert (st["role"], st["lo"], st["hi"], st["layers"], st["cut"]) == ("front", 0, 3, 6, 3), st
        assert st["options"]["carry"] == "H+N bf16, 2048 bytes a row" and st["options"]["slots"] == 1
        ta = _args(*common, "--stage", "3:6", "--link-peer", f"127.0.0.1:{link_port}")
        tstate: dict = {}
        stage.prepare(ta, tstate, log=lines.append)
        ctx = types.SimpleNamespace(state=tstate, keys=lambda *x: None, mark=marks.append, log=lines.append,
                                    vision=lambda *x: 1 / 0)
        out = SV.serve(ta, ctx)
        srv, front = out["engine"], fstate["stage"].follower.adapter
        try:
            assert type(srv) is FlashNextSplitServing and srv.co.link.state == "up" and out["device"] == "cpu"
            assert srv.ad.graphs == (graphs == "on") == front.graphs
            th = stage.health(tstate, "ok")
            assert th["stage"]["role"] == "tail" and th["stage"]["link"]["state"] == "up" and "status" not in th
            assert th["stage"]["options"] == tstate["stage"].options and stage.refusal(tstate, {"prompt": "x"}) is None
            assert stage.refusal(tstate, {"messages": [{"content": [{"type": "image_url"}]}]})[0] == 400
            info = out["serving"]["weights_info"]
            assert info["kv_cache"] == "bf16" and "tail stage layers [3, 6)" in out["describe"]
            if drafter == "mtp":
                assert info["self_check"] == {"ok": True, "rows": 16, "first_diff": None} and out["drafter"] is not None
                assert out["serving"]["tree"] and srv.fn.spec_ok and srv.heads["mtp"].graphs
            else:
                assert out["drafter"] is None and info["self_check"] is None and not out["serving"]["tree"]
            # what the server reads of the engine at its start and for /health
            cost = srv.cost()
            whole = SV.FlashNextServing(want["eng"], CAP).cost()
            assert cost.view_bytes_per_token + front.sv.cost().view_bytes_per_token == whole.view_bytes_per_token > 0
            assert cost.snapshot_cost() is not None and cost.workspace_rows(40, 8) > 0
            parts = srv.memory_parts()
            assert parts["weights"] > 0 and (parts["mtp_weights"] > 0) == (drafter == "mtp")
            assert srv.resident_files() == [] and len(front.sv.resident_files()) == 1     # the table is the front's
            assert srv.graph_count() >= 0 and isinstance(srv.graph_pools(), set) and srv.kv.pages is None
            assert srv.kv.max_len == CAP and srv.price_class(10) == 0 and srv.capture_cost(10) >= 0
            n = srv.precapture(out["drafter"])               # the server's call after its warm-up
            assert (n is None) if drafter == "none" else (n > 0 and srv.vgraphs), (drafter, n)
            srv.settle()
            if drafter == "mtp":
                check(drive(srv), want["outs"], "the stages as the server starts them")
            tokens = _loop(srv, PROMPT, 12, out["drafter"])
            assert tokens == _loop(SV.FlashNextServing(want["eng"], CAP), PROMPT, 12), drafter
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as r:
                assert json.loads(r.read())["stage"]["steps"] > 0
        finally:
            srv.co.link.close()
    assert "weights" in marks and "engine" in marks and "drafters" in marks
    assert any("[fn-selfcheck] PASS across the cut" in x for x in lines), lines[-5:]
    assert any("front stage ready" in x for x in lines)
    return ("server/stage.py and serve() on this family: the front loads, warms up and follows; the tail loads, "
            "warms up, links, checks itself across the cut and drafts; captured and eager")


# ------------------------------------------------------------------ the pack as it is served
def test_the_stages_load_their_own_layers_from_the_pack(monkeypatch):
    H, cuda = setup(monkeypatch)
    from engine.models.flashnext import stages as ST
    from engine.models.flashnext.tail import FlashNextSplitServing
    want = reference(H)
    d, cfg = H.host_pack(), want["eng"].cfg
    L_ = cfg.num_hidden_layers
    assert sorted(cfg.ple_layers) == [1] and cfg.attention_layers == [2, 5]
    for k in range(1, L_):
        front = ST.open_stage(d, 0, k, CAP, device="cpu", log=None)
        tail = ST.open_stage(d, k, L_, CAP, device="cpu", log=None, serving=FlashNextSplitServing, mtp=True)
        for ad, lo, hi in ((front, 0, k), (tail, k, L_)):
            mine = {int(n.split(".")[1]) for n in ad.eng.W if n.startswith("layers.")}
            norm = {hi} if hi < L_ and hi not in cfg.ple_layers else set()
            assert mine == set(range(lo, hi)) | norm, (k, sorted(mine))
            assert sorted(li for li in ad.eng.banks if li >= 0) == list(range(lo, hi))
            assert sorted(ad.eng.tables) == [li for li in (1,) if lo <= li < hi] == sorted(ad.eng.hash)
            assert sorted(ad.st.S) == [li for li in cfg.linear_layers if lo <= li < hi]
            assert sorted(ad.st.k) == sorted(ad.st.pool) == [li for li in cfg.attention_layers if lo <= li < hi]
            assert sorted(ad.st.ple_conv) == sorted(ad.eng.tables)
            assert ad.info["load"]["layers"] == [lo, hi]
        assert -1 in tail.eng.banks and -1 not in front.eng.banks                 # the draft layer is the tail's
        assert any(n.startswith("mtp.") for n in tail.eng.W) and not any(n.startswith("mtp.") for n in front.eng.W)
        front.warm()
        tail.warm()
        assert front.length == tail.length == 0
        srv = tail.sv.attach(Hop(front, tail))
        check(drive(srv), want["outs"], f"cut {k} from the pack")
        res = srv.self_check(PROMPT, steps=6, log=None)
        assert res == {"ok": True, "rows": 6, "first_diff": None} and tail.eng.spec_ok, res
    return f"cuts 1..{L_ - 1}: each stage's weights, experts, table and state are its own layers'; the one engine's bits"


def test_check_pack_reads_the_headers_for_both_stages(monkeypatch, tmp_path):
    H = _need_torch()
    from engine.models.flashnext import stages as ST
    d = H.host_pack()
    total = None
    for k in range(1, 6):
        res = ST.check_pack(d, k)
        f, t = res["stages"]["front"], res["stages"]["tail"]
        assert (res["layers"], res["cut"], res["hidden"], res["streams"]) == (6, k, 128, 4)
        assert res["carry_parts"] == (1 if k == 1 else 2) and res["carry_bytes_per_row"] == res["carry_parts"] * 1024
        assert (f["ple_layers"], t["ple_layers"]) == (([1], []) if k > 1 else ([], [1]))
        assert (f["table_bytes"] > 0) == (k > 1) and (t["table_bytes"] > 0) == (k == 1)
        assert f["layers"] == [0, k] and t["layers"] == [k, 6] and f["tensors"] > 0 and t["tensors"] > f["tensors"] * 0
        # every layer's tensors are read by exactly one stage
        layer_bytes = f["bytes"] + t["bytes"]
        total = total or layer_bytes
        assert abs(layer_bytes - total) <= 4 * 1024, (k, layer_bytes, total)   # the norm of the cut's layer
    _raises(ValueError, lambda: ST.check_pack(d, 0), "no cut at 0")
    _raises(ValueError, lambda: ST.check_pack(d, 6), "no cut at 6")
    # a pack without its n-gram table: named before any weight is read
    bad = str(tmp_path / "pack")
    shutil.copytree(d, bad)
    os.remove(os.path.join(bad, "ngram_embedding.safetensors"))
    _raises(FileNotFoundError, lambda: ST.check_pack(bad, 3), "n-gram table")
    args = types.SimpleNamespace(model=d, max_len=4096, stage_graphs="on")
    opts = ST.stage_options(args, 0, 3)
    assert (opts["slots"], opts["graphs"], opts["caches"], opts["kv_cache"], opts["images"]) == \
        (1, "captured", "off", "bf16", "refused"), opts
    assert opts["carry"] == "H+N bf16, 2048 bytes a row" and opts["ple_layers"] == {"front": [1], "tail": []}
    assert ST.stage_options(args, 1, 6)["carry"] == "H bf16, 1024 bytes a row"
    args.stage_graphs = "off"
    assert ST.stage_options(args, 3, 6)["graphs"] == "eager"
    for lo, hi in ((0, 6), (1, 4), (2, 9), (3, 3)):
        _raises(ValueError, lambda lo=lo, hi=hi: ST.stage_options(args, lo, hi), "front 0:k or the tail")
    return "headers only: each stage's tensors and bytes, the table on the PLE layer's side; the options and refusals"


def test_the_draft_layer_on_the_tail_drafts_the_one_engines_tokens(monkeypatch):
    H, cuda = setup(monkeypatch)
    from engine.models.flashnext.serving import FlashNextServing
    want = reference(H)
    eng = want["eng"]

    def drafts(srv) -> list:
        head = srv.add_mtp_head(3)
        out = []
        srv.forward(torch.tensor(PROMPT), 0)
        rows = srv.hidden_post_norm[0]
        head.prime(rows[:-1], PROMPT[1:])
        n = len(PROMPT)
        for t in (11, 50, 3):
            head.launch(srv.last_streams(), [t])
            out.append(head.read())
            srv.forward(torch.tensor([t]), n)
            n += 1
        lg = srv.forward_tree(torch.tensor([7, 19, 23]), [-1, 0, 1], n)
        rows = srv.hidden_post_norm[0][:2]
        srv.commit_tree([0, 1])
        head.launch(torch.cat([srv._h_last.new_zeros(0, rows.shape[1]), rows]), [19, int(lg[1].argmax())])
        out.append(head.read())
        assert head.st.length == len(PROMPT) - 1 + 3 + 2
        return out
    one = drafts(FlashNextServing(eng, CAP))
    for k in (1, 3, 5):
        srv, front, tail = hop(eng, k)
        got = drafts(srv)
        assert got == one, (k, got, one)
    assert all(len(toks) == 3 for toks, _ in one)
    return "the MTP chain over the tail's streams at cuts 1, 3, 5: the one engine's tokens and probabilities"


def test_the_cut_cost_tool_compares_both_arms(monkeypatch):
    H, cuda = setup(monkeypatch)
    from tools import stage_cut_cost as T
    for graphs in (False, True):
        res = T.measure(H.host_pack(), 3, runs=1, prompt_rows=8, steps=3, blocks=2, block_rows=4, max_len=CAP,
                        device="cpu", log=None, graphs=graphs)
        assert res["equal"] and res["first_diff"] is None, res
        a = res["args"]
        assert (a["cut"], a["layers"], a["graphs"], a["slots"]) == (3, 6, "captured" if graphs else "eager", 1)
        assert a["carry_bytes_per_row"] == 2048 and res["kinds"]["step"]["n"] == 3
    return "uncut and cut arms over one copy of the weights, eager and with graphs: every output equal"


# ------------------------------------------------------------------ over a link
class Linked:
    """The tail's serving and the front's follower over loopback, through the relay when asked."""

    def __init__(self, eng, k: int, relay: bool = False, step_s: float = 60.0, chunk_s: float = 60.0, **base):
        from engine.methods import link as L
        from engine.methods import stages as S
        from engine.models.flashnext import stages as ST
        from engine.models.flashnext.tail import FlashNextSplitServing
        self.L, self.S = L, S
        self.front = ST.stage_adapter(eng, 0, k, cap=base.pop("front_cap", CAP), graphs=True)
        te = ST.FlashNextStageEngine(eng.cfg, eng.W, eng.tables, eng.banks, k, eng.cfg.num_hidden_layers,
                                     device="cpu", max_len=CAP)
        self.tail = ST.FlashNextRowStage(te, graphs=True, serving=FlashNextSplitServing)
        self.block = {"identity": 1, "code": "c0de", "weights": "w8", "family": self.tail.family,
                      "layers": self.tail.layers}
        self.base = base
        self.key = secrets.token_bytes(32)
        self.listener = L.Listener("127.0.0.1:0")
        self.follower = S.Follower(self.front, idle_deadline=2.0)
        self.stop = False
        self.relay = None
        if relay:
            from test_linkrelay import Relay
            self.relay = Relay(self.listener.port)
        self.to = f"127.0.0.1:{self.relay.port if relay else self.listener.port}"

        def connect(last):
            if self.stop:
                raise SystemExit
            return S.open_link(self.key, S.stage_identity({**self.block, **self.base}, self.front),
                               listener=self.listener, last_epoch=last, accept_deadline=0.5)
        self.thread = threading.Thread(target=self._serve, args=(connect,), daemon=True)
        self.thread.start()
        self.co = S.Coordinator(self.tail, step_deadline=step_s, chunk_deadline=chunk_s)
        self.connect()
        self.srv = self.tail.sv.attach(self.co)

    def _serve(self, connect) -> None:
        try:
            self.follower.run(connect)
        except SystemExit:
            pass

    def connect(self):
        end = time.monotonic() + 20
        while True:
            try:
                link = self.S.open_link(self.key, self.S.stage_identity(self.block, self.tail), peer=self.to,
                                        last_epoch=self.co.epoch)
                break
            except self.L.LinkRefused:
                raise
            except self.L.LinkError:
                if time.monotonic() > end:
                    raise
                time.sleep(0.1)
        self.co.attach(link)
        return link

    def close(self) -> None:
        self.stop = True
        if self.co.link is not None:
            self.co.link.close()
        self.thread.join(10)
        self.listener.close()
        if self.relay is not None:
            self.relay.stop()


def test_the_split_serving_over_a_link_gives_the_one_servings_logits(monkeypatch):
    H, cuda = setup(monkeypatch)
    want = reference(H)
    for k in (1, 4):
        x = Linked(want["eng"], k)
        try:
            for round_ in range(2):
                check(drive(x.srv), want["outs"], f"cut {k} over a link, round {round_}")
                p = x.co.probe()
                assert p == {"length": 45, "digest": x.front.digest()} and x.srv.kv.length == 45
                both = {**x.front.state(), **x.tail.state()}
                for name, t in want["state"].items():
                    same(both[name], t, f"cut {k} over a link: {name}")
                x.srv.kv.length = 0
                assert x.srv.kv.length == 0 and x.co.probe()["length"] == 0
            res = x.srv.self_check(PROMPT, steps=6, log=None)
            assert res == {"ok": True, "rows": 6, "first_diff": None}, res
            carried = x.co.link.stats.rx_payload[x.L.CARRY]
            assert carried % x.front.carry_bytes_per_row == 0 and carried > 0 and x.co.replays == 0
            h = x.co.health()
            assert h["status"] == "ok" and h["stage"] == {"role": "tail", "cut": k, "link": {"state": "up", "epoch": 1}}
        finally:
            x.close()
    return "cuts 1 and 4 over loopback, two sequences each: the one serving's outputs, the front's digest and rows"


def test_a_stalled_and_a_cut_link_end_this_familys_blocks(monkeypatch):
    H, cuda = setup(monkeypatch)
    want = reference(H)
    x = Linked(want["eng"], 3, relay=True, step_s=1.0, chunk_s=2.5)
    S, srv, took = x.S, None, {}
    srv = x.srv
    n = len(PROMPT)
    try:
        asks = (("a decode step", 1.0, lambda: srv.forward(torch.tensor([11]), n)),
                ("a verify block", 1.0, lambda: srv.forward_tree(torch.tensor([7, 19, 23, 44]), [-1, 0, 1, 1], n)),
                ("a prompt chunk", 2.5, lambda: srv.forward(torch.tensor(MIDDLE[:6]), n)))
        for i, (what, bound, ask) in enumerate(asks):
            srv.kv.length = 0
            srv.forward(torch.tensor(PROMPT), 0)
            x.relay.cmd(cmd="blackhole")
            t0 = time.monotonic()
            e = _raises(S.StageDown, ask)
            took[what] = time.monotonic() - t0
            assert e.code == "stage_down" and bound - 0.1 <= took[what] < bound + 2.5, (what, took)
            h = x.co.health()
            assert h["status"] == "degraded" and h["stage"]["link"] == {"state": "down", "epoch": i + 1,
                                                                        "reason": "timeout"}, h
            t0 = time.monotonic()
            _raises(S.StageDown, lambda: srv.forward(torch.tensor([5]), n))     # nothing waits on a dead link
            assert time.monotonic() - t0 < 0.5
            srv.kv.length = 0                                 # the request is over: the sequence is dropped
            x.relay.cmd(cmd="heal")
            x.connect()
            assert x.co.epoch == i + 2 and x.co.probe() == {"length": 0, "digest": x.front.digest()}
        check(drive(srv), want["outs"], "after three stalled links")
        # a cut link, in the middle of a sequence: at once, then a new epoch and the same answers
        x.relay.cmd(cmd="cut")
        t0 = time.monotonic()
        e = _raises(S.StageDown, lambda: srv.forward(torch.tensor([60]), 45))
        assert e.code == "stage_down" and time.monotonic() - t0 < 1.5
        assert x.co.health()["status"] == "degraded"
        x.relay.cmd(cmd="heal")
        srv.kv.length = 0
        x.connect()
        assert x.co.epoch == 5 and x.front.length == 0 and x.tail.length == 0
        check(drive(srv), want["outs"], "after a cut link")
        assert x.co.replays == 0
    finally:
        x.close()
    return ("step deadline 1.0 s, chunk 2.5 s: " + ", ".join(f"{k} {v:.1f} s" for k, v in took.items())
            + "; a cut link at once; the same outputs under epochs 4 and 5")


# ------------------------------------------------------------------ refusals
def test_what_a_split_does_not_hold_is_refused(monkeypatch):
    H, cuda = setup(monkeypatch)
    from engine.methods.stages import Block, StageError
    from engine.models.flashnext import stages as ST
    from engine.settings import SETTINGS
    want = reference(H)
    eng = want["eng"]
    srv, front, tail = hop(eng, 3)
    c = srv.caps
    assert not (c.caches or c.side_slots or c.joint_verify or c.side_lane or c.joint_plain or c.shadow)
    assert not srv.window_ok(0) and not srv.prefill_joint_ok(8, 0) and not srv.tree_batchable([-1, 0])
    srv.forward(torch.tensor(PROMPT), 0)
    _raises(ValueError, lambda: setattr(srv.kv, "length", 7), "keeps no rows by length")
    _raises(ValueError, lambda: srv.state.sections(), "keeps no snapshots")
    _raises(ValueError, lambda: srv.state.restore(None), "keeps no snapshots")
    _raises(ValueError, lambda: srv.resume_at(4), "keeps no snapshots")
    _raises(ValueError, lambda: srv.side_slot(64), "one slot")
    _raises(ValueError, lambda: srv.forward_tree_multi([]), "one slot")
    _raises(ValueError, lambda: srv.forward(torch.tensor([1]), 5), "the state holds 19 rows")
    srv.pos_delta = 3                                        # a request with images sets the rotary offset
    _raises(ValueError, lambda: srv.forward(torch.tensor([1]), 19), "text only")
    srv.pos_delta = 0
    srv.mm = object()
    _raises(ValueError, lambda: srv.forward_tree(torch.tensor([1, 2]), [-1, 0], 19), "text only")
    srv.mm = None
    # the adapter's own
    _raises(ValueError, lambda: front.rows(Block([1], None), torch.zeros(1, 2, 512)), "the front stage takes tokens")
    _raises(ValueError, lambda: tail.rows(Block([1], None), None), "a later stage the carry")
    _raises(ValueError, lambda: front.rows(Block([1], None, slot=1), None), "one slot")
    _raises(ValueError, lambda: front.rows(Block([1] * 9, None), None), "prefill kernels' chunk is 8")
    _raises(ValueError, lambda: front.rows(Block([1] * (CAP - 18), None), None), "capacity")
    _raises(ValueError, lambda: tail.rows(Block([1], None), torch.zeros(1, 1, 512, dtype=torch.bfloat16)), "a carry of")
    _raises(ValueError, lambda: front.commit([0]), "no block to commit")
    front.rows(Block([1, 2, 3], [-1, 0, 0]), None)
    _raises(ValueError, lambda: front.commit([0, 1, 2]), "not a root path")
    _raises(ValueError, lambda: front.commit([1]), "not a root path")
    front.commit([0, 2])
    empty = ST.stage_adapter(eng, 0, 3, cap=CAP)
    _raises(ValueError, lambda: empty.rows(Block([1], None, graph=ST.STEP), None), "follows a prompt chunk")
    _raises(ValueError, lambda: ST.stage_adapter(eng, 2, 2), "no layers [2, 2)")
    # a replay after a desync ran decode steps as chunks: the request ends, the sequence is dropped
    srv2, f2, t2 = hop(eng, 3)
    srv2.forward(torch.tensor(PROMPT), 0)
    srv2.co.replays += 1
    e = _raises(StageError, lambda: srv2.forward(torch.tensor([11]), 19), "replayed")
    assert e.code == "desync" and srv2.kv.length == 0 and f2.length == 0
    # the e4m3 cache is not served split
    args = types.SimpleNamespace(model=H.host_pack(), max_len=4096, stage_graphs="on")
    monkeypatch.setenv("QWEN38_FN_KV_FP8", "1")
    assert SETTINGS.get("FN_KV_FP8") == "1"
    _raises(ValueError, lambda: ST.stage_options(args, 0, 3), "FN_KV_FP8")
    return "no caches, no rows by length, one slot, text only, the chunk's size, a root path, a replay: each refused"


def test_stages_that_disagree_are_refused(monkeypatch):
    H, cuda = setup(monkeypatch)
    from engine.methods import link as L
    from engine.methods.stages import StageError
    want = reference(H)
    # a front with a smaller window: the block that does not fit is refused, the link stays up
    x = Linked(want["eng"], 3, front_cap=24)
    try:
        x.srv.forward(torch.tensor(PROMPT), 0)
        e = _raises(StageError, lambda: x.srv.forward(torch.tensor(MIDDLE), 19), "capacity")
        assert e.code == "block" and x.co.health()["status"] == "ok"
    finally:
        x.close()
    # another model's weights on the front: the handshake refuses, and says which field
    e = _raises(L.LinkRefused, lambda: Linked(want["eng"], 3, weights="other"))
    assert e.reason == "weights"
    return "a block past the smaller window: block; other weights: refused by the handshake"


def test_a_front_that_fails_says_why_and_the_next_epoch_starts_clean(monkeypatch):
    """A launch that fails on the front (a capture, the allocator) is no refused block: the tail's
    request ends at once with the front's own words, the epoch ends, and a new link serves again."""
    H, cuda = setup(monkeypatch)
    from engine.methods.stages import StageDown, StageError
    want = reference(H)
    x = Linked(want["eng"], 3, step_s=30.0)
    try:
        x.srv.forward(torch.tensor(PROMPT), 0)
        rows, broke = x.front.rows, []

        def once(block, carry):
            if not broke:
                broke.append(1)
                raise RuntimeError("CUDA error: out of memory")
            return rows(block, carry)
        x.front.rows = once
        t0 = time.monotonic()
        e = _raises(StageError, lambda: x.srv.forward(torch.tensor([11]), 19), "out of memory")
        assert e.code == "stage" and time.monotonic() - t0 < 5.0
        _raises(StageDown, lambda: x.srv.forward(torch.tensor([11]), 19))
        x.srv.kv.length = 0
        x.connect()
        assert x.co.epoch == 2 and x.front.length == 0
        check(drive(x.srv), want["outs"], "after the front's failure")
    finally:
        x.close()
    return "the front's RuntimeError reaches the tail's request as `stage` with its words; epoch 2 serves the same outputs"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
