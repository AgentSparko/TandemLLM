"""ENG-308: LFM2 MoE's engine as two stages (engine/models/lfm2_moe/stages.py, tail.py), on the CPU.

The row kernels run under Triton's interpreter here (TRITON_INTERPRET=1, set below when there is no
GPU), on the tiny BF16 model of tests/test_lfm2_moe_engine.py. The gate is bit equality with the
engine in one process (torch.equal, same dtype):

  -  the stage's layer loops are the engine's: the loop bodies of `rows_range`, `chunk_range` and
     `load_stage` equal the bodies in engine.py and load.py as syntax trees (no torch needed), so a
     change to one that is not made to the other fails here
  -  every cut k in 1..layers-1, front and tail in one process with a host round trip of the carry:
     logits after a prompt chunk, decode steps, a tree verify and the commit of a moved path, a
     chain kept in part, a rollback, a second chunk and a one-row chunk, and the committed conv
     states and KV rows, equal the one engine's
  -  a stage engine holds its own layers only, and a stage read from a checkpoint
     (`open_stage`) holds only their weights and gives the same rows
  -  one flipped bit in a carry changes the logits (the check can fail)
  -  tools/stage_cut_cost.py runs the uncut and the cut arm over one copy of the weights, compares
     every output and records every setting
  -  the tail behind the server's engine interface (`Lfm2MoeSplitServing`) over a real link gives
     the logits and the tokens `Lfm2MoeServing` gives, through the server's own decode loop too;
     its self-check across the cut passes
  -  what a split does not hold is refused: a length set by hand, a snapshot, a side slot, images
  -  stages that disagree are refused: a block past the smaller window, other weights (the
     handshake), a range that is neither front nor tail; a replay after a desync ends the request
     and drops the sequence

Run: python tests/test_lfm2_moe_stage.py
"""

from __future__ import annotations

import ast
import json
import os
import secrets
import sys
import tempfile
import threading
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

try:
    import torch
except ImportError:
    torch = None
if torch is not None and not torch.cuda.is_available():
    os.environ.setdefault("TRITON_INTERPRET", "1")

from engine.methods import link as L  # noqa: E402
from engine.methods import stages as S  # noqa: E402


def _skip(why: str):
    from scriptmode import Skip
    return Skip(why)


def _need_kernels():
    if torch is None:
        raise _skip("no torch here")
    import test_lfm2_moe_engine as E
    if E.SKIP:
        raise _skip("no Triton here")
    return E


def _raises(kind, fn, *needles):
    try:
        fn()
    except kind as e:
        for n in needles:
            assert n in str(e), (n, str(e))
        return e
    raise AssertionError(f"no {kind.__name__} (wanted {needles})")


def chain_of(n: int) -> list:
    return list(range(-1, n - 1))


# ------------------------------------------------------------------ the loops are the engine's
def _func(tree, name: str, cls: str | None = None):
    body = tree.body
    if cls is not None:
        body = next(n for n in body if isinstance(n, ast.ClassDef) and n.name == cls).body
    return next(n for n in body if isinstance(n, ast.FunctionDef) and n.name == name)


def _loops(fn) -> list:
    return [n for n in fn.body if isinstance(n, ast.For)]


def _dump(nodes) -> str:
    return "\n".join(ast.dump(n) for n in nodes)


def _parse(rel: str):
    with open(os.path.join(ROOT, rel)) as f:
        return ast.parse(f.read())


def test_the_stage_loops_are_the_engines():
    eng, stg, load = (_parse("engine/models/lfm2_moe/" + f) for f in ("engine.py", "stages.py", "load.py"))
    # the row pass: the layer loop's body, and what follows it (the head, the greedy picks)
    rows, mine = _func(eng, "_rows", "Lfm2MoeEngine"), _func(stg, "rows_range")
    a, b = _loops(rows)[1], _loops(mine)[1]              # after the loop over the parts' row counts
    assert _dump(a.body) == _dump(b.body), "rows_range's layer loop is not Lfm2MoeEngine._rows'"
    after_a = rows.body[rows.body.index(a) + 1:]
    after_b = mine.body[mine.body.index(b) + 2:-1]          # past the cut's return, before `return None`
    assert _dump(after_a) == _dump(after_b), "rows_range's head is not Lfm2MoeEngine._rows'"
    # the prompt chunk
    chunk, mine = _func(eng, "forward_chunk", "Lfm2MoeEngine"), _func(stg, "chunk_range")
    a, b = _loops(chunk)[-1], _loops(mine)[-1]
    assert _dump(a.body) == _dump(b.body), "chunk_range's layer loop is not forward_chunk's"
    assert _dump(chunk.body[chunk.body.index(a) + 2:]) == _dump(mine.body[mine.body.index(b) + 3:])
    # the loader: the fused launches and the experts (the engine's loop also logs its progress)
    whole, mine = _func(load, "load"), _func(stg, "load_stage")
    fused_a, experts_a = _loops(whole)[-2:]
    fused_b, experts_b = _loops(mine)[-2:]
    assert _dump(fused_a.body) == _dump(fused_b.body), "load_stage's fused launches are not load's"
    assert _dump(experts_a.body[:-1]) == _dump(experts_b.body), "load_stage's experts are not load's"
    return "rows_range, chunk_range and load_stage carry the engine's loop bodies, node for node"


# ------------------------------------------------------------------ one engine, and its two stages
class Alone:
    """The engine in one process, driven as its serving adapter drives it."""

    def __init__(self, eng, cap: int = 128):
        self.eng, self.st = eng, eng.new_state(cap)

    def chunk(self, tokens):
        return self.eng.forward_chunk(torch.tensor(tokens), self.st, head="last").clone()

    def step(self, token):
        self.eng.stage(token, self.st)
        out = self.eng.step(self.st).clone()
        self.st.length += 1
        return out

    def verify(self, tokens, parents):
        self.eng.stage_rows(tokens, parents, self.st)
        return self.eng.rows_step(self.st, len(tokens)).clone()

    def commit(self, path):
        self.eng.commit(self.st, path)

    def state(self) -> dict:
        st, n = self.st, self.st.length
        out = {f"conv.{li}": st.conv[li].clone() for li in st.conv}
        for li in st.k:
            out[f"k.{li}"], out[f"v.{li}"] = st.k[li][:, :n].clone(), st.v[li][:, :n].clone()
        return out


class Cut:
    """Front and tail in one process: every carry goes through `encode` and `decode`, the host
    round trip a link makes. `flip`: a function applied to the carry's bytes."""

    def __init__(self, front, tail, flip=None):
        from engine.models.lfm2_moe import stages
        self.front, self.tail, self.flip, self.step_tag = front, tail, flip, stages.STEP
        self.bytes = []

    def _run(self, block):
        dtype, dims, buf = self.front.encode(self.front.rows(block, None))
        raw = bytearray(bytes(buf))
        self.bytes.append((block.rows, len(raw)))
        if self.flip is not None:
            self.flip(raw)
        out = self.tail.rows(block, self.tail.decode(dtype, dims, raw))
        return None if out is None else out.clone()

    def chunk(self, tokens):
        return self._run(S.Block(tokens, None, head="last"))

    def step(self, token):
        return self._run(S.Block([token], None, graph=self.step_tag))

    def verify(self, tokens, parents):
        return self._run(S.Block(tokens, parents))

    def commit(self, path):
        self.front.commit(path)
        self.tail.commit(path)

    def state(self) -> dict:
        assert self.front.length == self.tail.length
        a, b = self.front.state(), self.tail.state()
        assert not set(a) & set(b), "the two stages' sections overlap"
        return {k: v.clone() for k, v in {**a, **b}.items()}


def play(m, prompt) -> list:
    """Every kind of block the server sends, in an order that crosses them."""
    out = [m.chunk(prompt)]
    out += [m.step(t) for t in (11, 50, 3)]
    out.append(m.verify([7, 19, 23, 44], [-1, 0, 1, 1]))
    m.commit([0, 1, 3])                                    # a moved path
    out.append(m.step(5))
    out.append(m.verify([9, 10, 11, 12], chain_of(4)))
    m.commit([0, 1])                                       # a chain kept in part
    out.append(m.verify([8, 9], chain_of(2)))
    m.commit([])                                           # a rollback: none kept
    out.append(m.step(60))
    out.append(m.chunk([4, 5, 6]))                         # a second chunk, past the first
    out.append(m.chunk([21]))                              # a chunk of one row is not a decode step
    out.append(m.step(7))
    return out


def same(a, b, what) -> None:
    assert a.dtype == b.dtype and a.shape == b.shape and torch.equal(a, b), what


def same_states(got: dict, want: dict, what) -> None:
    assert set(got) == set(want), (what, sorted(set(got) ^ set(want)))
    for k in want:
        same(got[k], want[k], (what, k))


_WANT = []


def reference(E):
    """(the one engine's outputs, its committed state) for `play`."""
    if not _WANT:
        cfg, eng, _ = E.tiny()
        m = Alone(eng)
        _WANT.append((play(m, E.PROMPT.tolist()), m.state()))
    return _WANT[0]


def stages_of(E, k: int, **kw):
    from engine.models.lfm2_moe import stages
    cfg, eng, _ = E.tiny()
    return stages.stage_adapter(eng, 0, k), stages.stage_adapter(eng, k, cfg.num_hidden_layers), eng


def test_every_cut_equals_the_one_engine():
    E = _need_kernels()
    want, state = reference(E)
    cfg, eng, _ = E.tiny()
    Lyr, H = cfg.num_hidden_layers, cfg.hidden_size
    for k in range(1, Lyr):
        front, tail, _ = stages_of(E, k)
        assert front.role == "front" and tail.role == "tail" and front.cut == tail.cut == k
        assert front.carry_bytes_per_row == 2 * H * 2 and front.carry_dims == (2, H)
        m = Cut(front, tail)
        got = play(m, E.PROMPT.tolist())
        for i, (a, b) in enumerate(zip(got, want, strict=True)):
            same(a, b, f"cut {k}, output {i}")
        same_states(m.state(), state, f"cut {k}")
        assert all(n == rows * front.carry_bytes_per_row for rows, n in m.bytes), m.bytes
        assert front.digest() != tail.digest()
    return f"cuts 1..{Lyr - 1}: {len(want)} outputs and the committed state, bit for bit; " \
           f"{2 * H * 2} carry bytes a row"


def test_a_stage_holds_its_own_layers_only():
    E = _need_kernels()
    from engine.models.lfm2_moe import stages
    cfg, eng, _ = E.tiny()
    Lyr = cfg.num_hidden_layers
    front, tail, _ = stages_of(E, 3)
    for ad, own in ((front, range(0, 3)), (tail, range(3, Lyr))):
        c = ad.eng.cfg
        assert c.num_hidden_layers == Lyr and c.layer_types == cfg.layer_types
        assert c.conv_layers == [i for i in cfg.conv_layers if i in own]
        assert c.attention_layers == [i for i in cfg.attention_layers if i in own]
        assert c.moe_layers == [i for i in cfg.moe_layers if i in own]
        assert set(ad.st.conv) == set(c.conv_layers) and set(ad.st.k) == set(c.attention_layers)
        assert set(ad.eng._wc) == set(c.conv_layers) and set(ad.eng._gate) == set(c.moe_layers)
        assert c.kv_bytes_per_token(2) == len(c.attention_layers) * 2 * cfg.kv_dim * 2
    _raises(ValueError, lambda: stages.stage_config(cfg, 3, 3), "no layers")
    _raises(ValueError, lambda: stages.stage_config(cfg, 0, Lyr + 1), "no layers")
    _raises(ValueError, lambda: stages.Lfm2MoeRowStage(front.eng, 0, 4), "built for layers")
    _raises(ValueError, lambda: front.rows(S.Block([1, 2]), torch.zeros(2, 2, cfg.hidden_size)), "front stage takes tokens")
    _raises(ValueError, lambda: tail.rows(S.Block([1, 2]), None), "front stage takes tokens")
    _raises(ValueError, lambda: front.rows(S.Block([1], slot=1), None), "one slot")
    _raises(ValueError, lambda: front.rows(S.Block([1, 2], None, graph=stages.STEP), None), "one row")
    _raises(ValueError, lambda: front.commit([0]), "no block")
    return "layer lists, states, taps and routers cut to the stage; a wrong range or block is refused"


def test_one_flipped_bit_in_the_carry_changes_the_logits():
    E = _need_kernels()
    want, _ = reference(E)

    def flip(raw):
        raw[5] ^= 0x10
    front, tail, _ = stages_of(E, 2)
    got = Cut(front, tail, flip).chunk(E.PROMPT.tolist())
    assert not torch.equal(got, want[0])
    return "a carry with one bit flipped gives other logits"


def _checkpoint(E) -> str:
    """The tiny model as a BF16 checkpoint on disk (the weights `tiny()` runs)."""
    from safetensors.torch import save_file
    cfg, eng, _ = E.tiny()
    d = tempfile.mkdtemp(prefix="lfm-stage-")
    W0 = E._random_weights(cfg)
    out = {"model." + k: (v.float() if k.endswith("expert_bias") else v.to(torch.bfloat16)).contiguous()
           for k, v in W0.items()}
    save_file(out, os.path.join(d, "model.safetensors"))
    with open(os.path.join(d, "config.json"), "w") as f:
        json.dump(E.TINY_B, f)
    return d


def test_a_stage_from_a_checkpoint_reads_its_own_weights():
    E = _need_kernels()
    from engine.models.lfm2_moe import stages
    want, state = reference(E)
    cfg, eng, _ = E.tiny()
    Lyr, k = cfg.num_hidden_layers, 3
    d = _checkpoint(E)
    front = stages.open_stage(d, 0, k, 128, device="cpu", log=None)
    tail = stages.open_stage(d, k, Lyr, 128, device="cpu", log=None)
    for ad, own in ((front, range(0, k)), (tail, range(k, Lyr))):
        layers = {int(n.split(".")[1]) for n in ad.eng.W if n.startswith("layers.")}
        assert layers <= set(own) | {ad.hi}, sorted(layers)
        assert set(ad.eng.experts) == {i for i in cfg.moe_layers if i in own}
        for name, w in ad.eng.W.items():
            if torch.is_tensor(w) and name in eng.W:
                same(w, eng.W[name], name)
    assert "embed_tokens.weight" in front.eng.W and "embedding_norm.weight" not in front.eng.W
    assert "lm_head.weight" in tail.eng.W and f"layers.{k}.operator_norm.weight" in front.eng.W
    m = Cut(front, tail)
    got = play(m, E.PROMPT.tolist())
    for i, (a, b) in enumerate(zip(got, want, strict=True)):
        same(a, b, f"output {i}")
    same_states(m.state(), state, "from the checkpoint")
    front.warm()
    tail.warm()
    assert front.length == tail.length == 0
    args = types.SimpleNamespace(model=d, max_len=128)
    opts = stages.stage_options(args, 0, k)
    assert opts["slots"] == 1 and opts["graphs"] == "eager" and opts["caches"] == "off"
    assert opts["prefill_chunk"] == 4096 and opts["verify_rows_max"] == 33 and opts["max_len"] == 128
    for lo, hi in ((0, Lyr), (1, 3), (0, Lyr + 1), (2, Lyr + 2)):
        _raises(ValueError, lambda lo=lo, hi=hi: stages.stage_options(args, lo, hi), "front 0:k or the tail")
    return "two stages read from one checkpoint: own layers' weights only, the one engine's rows"


def test_the_cut_cost_tool_compares_both_arms():
    E = _need_kernels()
    from tools import stage_cut_cost as T
    res = T.measure(_checkpoint(E), 3, runs=1, prompt_rows=12, steps=3, blocks=2, block_rows=4, max_len=128,
                    device="cpu", log=None)
    assert res["equal"] is True and res["first_diff"] is None, res
    assert set(res["kinds"]) == {"chunk", "step", "verify", "commit"}
    k = res["kinds"]
    cfg = E.tiny()[0]
    assert (k["chunk"]["n"], k["step"]["n"], k["verify"]["n"], k["commit"]["n"]) == (1, 3, 2, 2)
    assert k["verify"]["carry_bytes"] == 4 * 2 * cfg.hidden_size * 2 and k["step"]["uncut_ms"]["p50"] > 0
    a = res["args"]
    assert (a["cut"], a["layers"], a["graphs"], a["slots"], a["warmup_runs"]) == (3, 6, "eager", 1, 1)
    assert "not a link cost" in res["label"]
    _raises(SystemExit, lambda: T.measure(_checkpoint(E), 6, runs=1, device="cpu", log=None), "cuts are")
    return "uncut and cut arms over one copy of the weights: every output equal, every setting recorded"


# ------------------------------------------------------------------ the tail behind the server's interface
def linked(E, k: int, front_eng=None, **base):
    """(the tail's serving, the front's adapter, the link, the follower's thread) over loopback."""
    from engine.models.lfm2_moe import stages
    from engine.models.lfm2_moe.tail import Lfm2MoeSplitServing
    cfg, eng, _ = E.tiny()
    front = stages.stage_adapter(front_eng or eng, 0, k)
    tail = stages.stage_adapter(eng, k, cfg.num_hidden_layers)
    block = {"identity": 1, "code": "c0de", "weights": "w8", "family": tail.family, "layers": tail.layers}
    key = secrets.token_bytes(32)
    listener = L.Listener("127.0.0.1:0")
    follower = S.Follower(front)
    tries = []

    def connect(last):
        if tries:
            raise SystemExit                                # one link a test
        tries.append(1)
        return S.open_link(key, S.stage_identity({**block, **base}, front), listener=listener,
                           last_epoch=last, accept_deadline=30.0)
    t = threading.Thread(target=follower.run, args=(connect,), kwargs={"links": 1}, daemon=True)
    t.start()
    try:
        link = S.open_link(key, S.stage_identity(block, tail), peer=f"127.0.0.1:{listener.port}")
    finally:
        listener.close()
    co = S.Coordinator(tail, step_deadline=60.0)
    co.attach(link)
    return Lfm2MoeSplitServing(co, 128), front, link, t


def drive(srv, prompt) -> list:
    """The server's verbs in the order its loop calls them; the logits of each."""
    n = len(prompt)
    out = [srv.forward(torch.tensor(prompt), 0).clone()]
    for i, t in enumerate((11, 50, 3)):
        out.append(srv.forward(torch.tensor([t]), n + i).clone())
    n += 3
    out.append(srv.forward_tree(torch.tensor([7, 19, 23, 44]), [-1, 0, 1, 1], n).clone())
    out.append(srv.picks.clone())
    out.append(srv.hidden_post_norm.clone())
    srv.commit_tree([0, 1, 3])
    n += 3
    out.append(srv.forward_block(torch.tensor([9, 10, 11, 12]), n).clone())
    assert srv.kv.length == n + 4
    srv.rollback_to(2)
    n += 2
    out.append(srv.forward_block(torch.tensor([8, 9, 13]), n).clone())
    assert srv.trim_pending(n + 1)
    n += 1
    out.append(srv.forward(torch.tensor([60]), n).clone())
    out.append(srv.hidden_post_norm.clone())
    out.append(srv.forward(torch.tensor([4, 5, 6]), n + 1).clone())
    out.append(srv.last_streams().clone())
    assert srv.kv.length == n + 4
    return out


def test_the_split_serving_gives_the_one_servings_logits():
    E = _need_kernels()
    from engine.models.lfm2_moe.serving import Lfm2MoeServing
    cfg, eng, _ = E.tiny()
    prompt = E.PROMPT.tolist()
    want = drive(Lfm2MoeServing(eng, 128), prompt)
    for k in (1, 4):
        srv, front, link, t = linked(E, k)
        try:
            for round_ in range(2):                        # a second sequence in the same buffers
                got = drive(srv, prompt)
                for i, (a, b) in enumerate(zip(got, want, strict=True)):
                    same(a, b, f"cut {k}, round {round_}, output {i}")
                assert srv.co.probe()["length"] == srv.kv.length == front.length
                srv.kv.length = 0
                assert srv.kv.length == 0 and srv.co.probe()["length"] == 0
            res = srv.self_check(prompt, steps=6, log=None)
            assert res == {"ok": True, "rows": 6, "first_diff": None}, res
            carried = link.stats.rx_payload[L.CARRY]
            assert carried % front.carry_bytes_per_row == 0 and srv.co.replays == 0
        finally:
            link.close()
            t.join(10)
    return "cuts 1 and 4, two sequences each: logits, picks and final rows bit for bit; the self-check passes"


class _Tok:
    unk_token_id = -1
    eos_token_id = None

    def convert_tokens_to_ids(self, text):
        return -1

    def __call__(self, text, add_special_tokens=False, return_tensors=None):
        ids = [1 + (ord(c) % 50) for c in text]
        return type("R", (), {"input_ids": torch.tensor([ids]) if return_tensors == "pt" else ids})()

    def decode(self, seq, skip_special_tokens=True):
        return "".join(chr(ord("a") + int(t) % 26) for t in seq)


def _loop(eng, prompt, n: int) -> list:
    """`n` greedy tokens from the server's own decode loop over `eng`."""
    from engine.penalty import PenaltySpec
    from engine.spec import Relax
    from server import app
    app.STATE.clear()
    app.STATE.update(engine=eng, drafter=None, k=4, tree=False, sampled_tree=False, relax=Relax(1.0, 1),
                     verbose=False, state_store=None, prefix_cache=False, prefix_chunk=0, session_cache=False,
                     tok=_Tok(), device="cpu", max_len=128, default_max_tokens=64, pen_spec=PenaltySpec(),
                     model="t", cfg_eos=None, think_budget=0, think_stall=False, reasoning_format="tags",
                     request_timeout=0.0, max_queue=8, queue_timeout=5.0, pattern_stop=None,
                     response_cache=None, suffix_store=None)
    eng.kv.length = 0
    return [int(t) for t in app.generate_stream(torch.tensor(prompt), n, set())]


def test_the_servers_loop_writes_the_same_tokens_split():
    E = _need_kernels()
    from engine.models.lfm2_moe.serving import Lfm2MoeServing
    cfg, eng, _ = E.tiny()
    prompt = E.PROMPT.tolist()
    want = _loop(Lfm2MoeServing(eng, 128), prompt, 12)
    assert len(want) == 12
    srv, front, link, t = linked(E, 3)
    try:
        assert _loop(srv, prompt, 12) == want
        assert _loop(srv, prompt[:9], 8) == _loop(Lfm2MoeServing(eng, 128), prompt[:9], 8)
        assert srv.stats["decode_steps"] >= 15 and srv.co.replays == 0
    finally:
        link.close()
        t.join(10)
    return f"12 and 8 greedy tokens through server/app.py's loop: the one process's, {want[:6]}..."


def test_what_a_split_does_not_hold_is_refused():
    E = _need_kernels()
    srv, front, link, t = linked(E, 2)
    try:
        prompt = E.PROMPT.tolist()
        srv.forward(torch.tensor(prompt), 0)
        _raises(ValueError, lambda: setattr(srv.kv, "length", 5), "no rows by length")
        srv.kv.length = len(prompt)                         # the length it holds: nothing to do
        _raises(ValueError, lambda: srv.resume_at(3), "no rows by length")
        _raises(ValueError, lambda: srv.forward(torch.tensor([1]), 3), "the state holds")
        _raises(ValueError, lambda: srv.side_slot(128), "one slot")
        _raises(ValueError, lambda: srv.forward_tree_multi([]), "one slot")
        _raises(ValueError, lambda: srv.cache_state().sections(), "no snapshots")
        _raises(ValueError, lambda: srv.state.clone(), "no snapshots")
        assert srv.caps.caches is False and srv.caps.side_slots is False and srv.caps.joint_verify is False
        assert srv.caps.side_lane is False and srv.kv.pages is None and srv._graphs_ok is False
        assert srv.precapture(None) is None and srv.kv.length == 0
        srv.mm = object()
        _raises(ValueError, lambda: srv.forward(torch.tensor(prompt), 0), "text only")
        srv.mm = None
        srv.pos_delta = 4
        _raises(ValueError, lambda: srv.forward(torch.tensor(prompt), 0), "text only")
        srv.pos_delta = 0
    finally:
        link.close()
        t.join(10)
    return "a length by hand, a snapshot, a side slot, a joint pass and images are refused"


def test_stages_that_disagree_are_refused():
    E = _need_kernels()
    from engine.models.lfm2_moe.engine import Lfm2MoeEngine
    cfg, eng, _ = E.tiny()
    prompt = E.PROMPT.tolist()
    # a smaller window on the front: the block that passes it is refused there, none before it
    small = Lfm2MoeEngine(cfg, eng.W, eng.experts, device=eng.device, max_len=16)
    srv, front, link, t = linked(E, 3, front_eng=small)
    try:
        srv.forward(torch.tensor(prompt), 0)
        srv.forward(torch.tensor([11]), len(prompt))
        srv.forward(torch.tensor([50]), len(prompt) + 1)
        e = _raises(S.StageError, lambda: srv.forward(torch.tensor([3]), len(prompt) + 2), "capacity")
        assert e.code == "block"
    finally:
        link.close()
        t.join(10)
    # a front whose rows are not the tail's any more: the coordinator replays, and the serving
    # ends the request and drops the sequence instead of serving rows of other kernels
    srv, front, link, t = linked(E, 3)
    try:
        srv.forward(torch.tensor(prompt), 0)
        srv.forward(torch.tensor([11]), len(prompt))
        front.reset()
        e = _raises(S.StageError, lambda: srv.forward(torch.tensor([50]), len(prompt) + 1), "replayed")
        assert e.code == "desync" and srv.kv.length == 0 and srv.co.probe()["length"] == 0
        from engine.models.lfm2_moe.serving import Lfm2MoeServing
        same(srv.forward(torch.tensor(prompt), 0), Lfm2MoeServing(eng, 128).forward(torch.tensor(prompt), 0),
             "the sequence after the drop")
    finally:
        link.close()
        t.join(10)
    # another model's weights on the front: the handshake refuses, and says which field
    e = _raises(L.LinkRefused, lambda: linked(E, 3, weights="other"))
    assert e.reason == "weights"
    return "a block past the smaller window: block; a desync: the request ends; other weights: refused"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
