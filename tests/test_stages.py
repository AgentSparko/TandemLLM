"""engine/methods/stages.py: one model as two stage processes (ENG-308).

The front stage is a real second process (this file run with --follower); the test process is the
tail, the coordinator. Without torch a toy family stands in for the model; with torch both
reference families run at every cut.

  -  toy family, two processes: prompt chunks, verify trees, commits and rollbacks give what the
     whole model gives in one process; the front's state digest is the single process's
  -  bytes per hop: each round's carry frame is rows x the adapter's carry size, read from the
     link's own counters; a verify header is under 1 KB
  -  the follower acts only on headers: any other frame changes nothing
  -  each family, every cut k in 1..layers-1, in one process and in two: logits, state sections
     and KV rows after prefill + tree verify + commit + rollback are bit-equal to the reference
     (torch.equal, same dtype)
  -  the check can fail: one flipped bit in a carry changes the logits
  -  a stalled front (relay blackhole) ends the request with stage_down inside its deadline, and
     the heartbeat takes an idle link down; a decode step (a chain of one row) and every request
     but a prompt chunk of several rows get the step's deadline
  -  a killed front: stage_down; a new front gets a new epoch and both sides start empty
  -  a cut link, then a reconnect to the same front process: a new epoch, the old rows are gone
  -  a front whose length is not the tail's answers desync; the coordinator replays the tokens
  -  an idle follower uses under 1 % of a core

Run: python3 tests/test_stages.py
"""

from __future__ import annotations

import argparse
import os
import secrets
import select
import signal
import subprocess
import sys
import tempfile
import time
from array import array

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from engine.methods import link as L  # noqa: E402
from engine.methods import stages as S  # noqa: E402

try:
    import torch
except ImportError:
    torch = None

MOD = 2 ** 31 - 1


def chain_of(n: int) -> list:
    return list(range(-1, n - 1))


# ------------------------------------------------------------------ a toy family (no torch)
class ToyStage(S.StageAdapter):
    """Four "layers" of integer arithmetic with one number of state a layer: enough to tell a
    wrong commit, a stale state or a swapped row."""

    family = "toy"
    layers = 4
    cuts = (1, 2, 3)
    carry_dtype = "int32"
    carry_dims = (2,)

    def __init__(self, lo: int, hi: int):
        self.lo, self.hi = lo, hi
        self.reset()

    @property
    def length(self) -> int:
        return self.n

    def reset(self) -> None:
        self.acc = {li: 0 for li in range(self.lo, self.hi)}
        self.n = 0
        self.pend = None

    def rows(self, block, carry):
        parents = chain_of(block.rows) if block.chain else block.parents
        depth = []
        for p in parents:
            depth.append(0 if p < 0 else depth[p] + 1)
        out = []
        for i, t in enumerate(block.tokens):
            v = [t, 0] if self.lo == 0 else list(carry[i])
            for li in range(self.lo, self.hi):
                v = [(v[0] * 31 + self.acc[li] + li + depth[i] + self.n) % MOD, (v[1] + v[0]) % MOD]
            out.append(v)
        self.pend = (block.tokens, parents)
        if block.chain:
            self.commit(list(range(block.rows)))
        if self.hi < self.layers:
            return out
        return None if block.head == "none" else [a ^ b for a, b in out]

    def commit(self, path) -> None:
        if self.pend is None:
            if path:
                raise ValueError("no block to commit")
            return
        tokens, parents = self.pend
        if path and (path[0] != 0 or any(not 0 <= b < len(parents) or parents[b] != a
                                         for a, b in zip(path, path[1:]))):
            raise ValueError("not a root path")
        for i in path:
            for li in self.acc:
                self.acc[li] = (self.acc[li] * 7 + tokens[i] + 1) % MOD
        self.n += len(path)
        self.pend = None

    def state(self) -> dict:
        return {f"acc.{li}": [v] for li, v in self.acc.items()}

    def encode(self, t):
        rows = len(t)
        flat = [x for r in t for x in r] if rows and isinstance(t[0], list) else list(t)
        dims = (rows, 2) if rows and isinstance(t[0], list) else (rows,)
        return L.DTYPE_CODES["int32"], dims, array("i", flat).tobytes()

    def decode(self, dtype, dims, buf):
        a = array("i")
        a.frombytes(bytes(buf))
        flat = a.tolist()
        return [flat[i:i + 2] for i in range(0, len(flat), 2)]


# ------------------------------------------------------------------ the families (torch)
def build(family: str, dtype: str = "float32"):
    """(the family's stage module, its reference model) for the tiny config."""
    from engine.models import REGISTRY
    kind = getattr(torch, dtype)
    if family == "lfm2_moe":
        import test_lfm2_moe_ref as T
        from engine.models.lfm2_moe.ref import Lfm2MoeRef
        cfg, W, _ = T.tiny()
        model = Lfm2MoeRef(cfg, {k: v.to(kind) for k, v in W.items()}, dtype=kind)
    else:
        from fn_tiny import tiny
        from engine.models.flashnext.ref import FlashNextRef
        cfg, W, tables = tiny(dtype=kind)
        model = FlashNextRef(cfg, W, tables, dtype=kind)
    return S.family_stages(REGISTRY[family]), model


def adapter_for(family: str, lo: int, hi: int, dtype: str = "float32"):
    if family == "toy":
        return ToyStage(lo, hi)
    mod, model = build(family, dtype)
    return mod.stage_adapter(model, lo, hi)


def identity(ad, **over) -> dict:
    block = {"identity": 1, "code": "c0de", "weights": "w8", "family": ad.family, "layers": ad.layers}
    return S.stage_identity({**block, **over}, ad)


# ------------------------------------------------------------------ the follower process
def follower_main(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--follower", required=True)
    ap.add_argument("--cut", type=int, required=True)
    ap.add_argument("--dtype", default="float32")
    ap.add_argument("--dump", default=None)
    a = ap.parse_args(argv)
    ad = adapter_for(a.follower, 0, a.cut, a.dtype)
    if a.dump:
        digest = ad.digest

        def dumping():
            torch.save({k: v.clone() for k, v in ad.state().items()}, a.dump + ".tmp")
            os.replace(a.dump + ".tmp", a.dump)
            return digest()
        ad.digest = dumping
    key = L.read_key(os.environ)
    lst = L.Listener("127.0.0.1:0")
    print(f"listening 127.0.0.1:{lst.port}", flush=True)
    fol = S.Follower(ad)
    epoch = 0
    while True:
        try:
            link = S.open_link(key, identity(ad), listener=lst, last_epoch=epoch, accept_deadline=120)
        except L.LinkTimeout:
            return 0                         # nobody came: the test is gone
        except L.LinkError:
            continue
        epoch = link.epoch
        print(f"epoch {epoch} ended: {fol.serve(link)}", flush=True)


class Front:
    """A follower process of `family` cut at `k`, and how to reach it."""

    def __init__(self, family: str, k: int, dtype: str = "float32", dump: bool = False):
        self.dir = tempfile.mkdtemp(prefix="stages-")
        self.dump = os.path.join(self.dir, "state.pt") if dump else None
        self.key = secrets.token_hex(24)
        cmd = [sys.executable, os.path.abspath(__file__), "--follower", family, "--cut", str(k),
               "--dtype", dtype] + (["--dump", self.dump] if dump else [])
        self.p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                  env={**os.environ, L.KEY_ENV: self.key})
        ready, _, _ = select.select([self.p.stdout], [], [], 120)
        line = self.p.stdout.readline() if ready else ""
        if not line.startswith("listening "):
            self.kill()
            raise AssertionError(f"the follower did not start: {line!r} {self.p.stderr.read()[-2000:]}")
        self.addr = line.split()[1]
        self.port = int(self.addr.rsplit(":", 1)[1])

    def connect(self, co: S.Coordinator, to: str | None = None, **over) -> L.TcpLink:
        link = S.open_link(self.key.encode(), identity(co.adapter, **over), peer=to or self.addr,
                           last_epoch=co.epoch)
        co.attach(link)
        return link

    def cpu_seconds(self) -> float:
        try:
            with open(f"/proc/{self.p.pid}/stat") as f:
                parts = f.read().rsplit(")", 1)[1].split()
            return (int(parts[11]) + int(parts[12])) / os.sysconf("SC_CLK_TCK")
        except OSError:
            out = subprocess.run(["ps", "-o", "cputime=", "-p", str(self.p.pid)], capture_output=True,
                                 text=True).stdout.strip()
            secs = 0.0
            for part in out.replace("-", ":").split(":"):
                secs = secs * 60 + float(part)
            return secs

    def kill(self) -> None:
        if self.p.poll() is None:
            self.p.send_signal(signal.SIGKILL)
        self.p.wait(10)
        for f in (self.p.stdout, self.p.stderr):
            f.close()


def split(family: str, k: int, dtype: str = "float32", dump: bool = False, **kw):
    """(the follower process, the coordinator attached to it, the link)."""
    front = Front(family, k, dtype, dump)
    try:
        if family == "toy":
            ad = ToyStage(k, ToyStage.layers)
        else:
            mod, model = build(family, dtype)
            ad = mod.stage_adapter(model, k, model.cfg.num_hidden_layers)
        co = S.Coordinator(ad, **kw)
        return front, co, front.connect(co)
    except BaseException:
        front.kill()
        raise


def note(text: str) -> str:
    print(f"    {text}", flush=True)
    return text


def raises(kind, fn):
    try:
        fn()
    except kind as e:
        return e
    raise AssertionError(f"no {kind.__name__}")


# ------------------------------------------------------------------ one scenario for every model
PROMPT = [5, 9, 3, 17, 22, 3, 40, 41, 12]
SCRIPT = (
    ("prefill", PROMPT),
    ("prefill", [7, 63, 2, 30, 8]),
    ("verify", [7, 19, 23, 44, 31, 60], [-1, 0, 1, 1, 0, 4]),
    ("commit", [0, 1, 3]),                    # a path that leaves the chain: its KV rows move
    ("check",),
    ("verify", [11, 50, 3, 28, 6], chain_of(5)),
    ("commit", [0, 1]),                       # a partly accepted draft: the rest is rolled back
    ("verify", [9, 13, 21], [-1, 0, 0]),
    ("commit", []),                           # nothing kept: the whole block is rolled back
    ("check",),
    ("verify", [33], [-1]),
    ("commit", [0]),
    ("prefill", [4, 4, 58]),
    ("verify", [10, 20, 30, 40], [-1, 0, 1, 0]),
    ("commit", [0, 3]),
    ("check",),
)


def play(model, check=None) -> list:
    """Run SCRIPT on anything with prefill / verify / commit; the outputs in order."""
    out = []
    for step in SCRIPT:
        if step[0] == "prefill":
            out.append(model.prefill(step[1]))
        elif step[0] == "verify":
            out.append(model.verify(step[1], step[2]))
        elif step[0] == "commit":
            model.commit(step[1])
        elif check is not None:
            out.append(check())
    return out


class Whole:
    """A stage adapter that holds every layer: the model in one process."""

    def __init__(self, ad):
        self.ad = ad

    def prefill(self, tokens):
        return self.ad.rows(S.Block(tokens), None)

    def verify(self, tokens, parents):
        return self.ad.rows(S.Block(tokens, parents), None)

    def commit(self, path):
        self.ad.commit(path)


class Pair:
    """Front and tail adapters in one process, the carry handed over directly."""

    def __init__(self, front, tail):
        self.front, self.tail = front, tail

    def prefill(self, tokens):
        return self.tail.rows(S.Block(tokens), self.front.rows(S.Block(tokens), None))

    def verify(self, tokens, parents):
        return self.tail.rows(S.Block(tokens, parents), self.front.rows(S.Block(tokens, parents), None))

    def commit(self, path):
        self.front.commit(path)
        self.tail.commit(path)


class Counted:
    """A coordinator whose every round is held to bytes per hop = rows x carry size."""

    def __init__(self, co, link):
        self.co, self.link, self.rounds, self.carried = co, link, 0, 0

    def _round(self, rows, fn):
        st = self.link.stats
        with self.link.lock:                  # no heartbeat inside the measured round
            rx, frames = st.rx_bytes, st.rx_frames
            carry, sent = st.rx_payload.get(L.CARRY, 0), st.tx_payload.get(L.STEP, 0)
            out = fn()
            want = rows * self.co.adapter.carry_bytes_per_row
            assert st.rx_payload[L.CARRY] - carry == want, (st.rx_payload[L.CARRY] - carry, want)
            assert st.rx_bytes - rx == want + L.HEADER_BYTES and st.rx_frames - frames == 1
            assert st.tx_payload[L.STEP] - sent < 1024
        self.rounds += 1
        self.carried += want
        return out

    def prefill(self, tokens):
        return self._round(len(tokens), lambda: self.co.prefill(tokens))

    def verify(self, tokens, parents):
        return self._round(len(tokens), lambda: self.co.verify(tokens, parents))

    def commit(self, path):
        self.co.commit(path)


# ------------------------------------------------------------------ the protocol, toy family
def test_toy_split_equals_the_whole_in_two_processes():
    want = play(Whole(ToyStage(0, 4)))
    for k in ToyStage.cuts:
        front, co, link = split("toy", k)
        try:
            local = ToyStage(0, k)
            play(Pair(local, ToyStage(k, 4)))
            counted = Counted(co, link)
            got = play(counted, check=lambda: co.probe())
            probes = [x for x in got if isinstance(x, dict)]
            assert [x for x in got if not isinstance(x, dict)] == want, k
            assert len(probes) == 3 and probes[-1] == {"length": co.length, "digest": local.digest()}, probes
            assert co.length == 14 + 3 + 2 + 0 + 1 + 3 + 2 and co.replays == 0
            assert counted.rounds == 8 and counted.carried == 36 * 8, (counted.rounds, counted.carried)
            assert co.health()["status"] == "ok" and co.health()["stage"]["link"] == {"state": "up", "epoch": 1}
        finally:
            link.close()
            front.kill()
    return note(f"cuts {list(ToyStage.cuts)}: 8 rounds, {36 * 8} carry bytes each (36 rows x 8)")


def test_the_follower_acts_only_on_headers():
    front, co, link = split("toy", 2)
    try:
        co.prefill(PROMPT)
        before = co.probe()
        for kind, payload in ((L.CARRY, b"\0" * 8), (L.REPLY, b"{}"), (L.OK, b""), (L.IDENT, b"x")):
            with link.lock:
                link.send(kind, payload, seq=99)
                fr = link.recv(5)
            assert fr.kind == L.ERROR and fr.json()["code"] == "frame" and fr.seq == 99, fr
        with link.lock:
            link.send(L.STEP, b"not a header", seq=5)
            assert link.recv(5).json()["code"] == "header"
            link.epoch += 1                                  # a header of another epoch
            link.send(L.STEP, S.pack_step(S.OP_RESET, 0), seq=6)
            link.epoch -= 1
            assert link.recv(5).json()["code"] == "epoch"
        assert co.probe() == before and before["length"] == len(PROMPT)
        # a commit path for a block the front does not hold: desync, never a guess
        e = raises(S.StageError, lambda: co._ask(S.pack_step(S.OP_SYNC, len(PROMPT), commit=[0, 5]), 2))
        assert e.code == "desync", e.code
    finally:
        link.close()
        front.kill()


# ------------------------------------------------------------------ the families, every cut
def _skip(why: str):
    from scriptmode import Skip
    return Skip(why)


class FlashNextAlone:
    """The reference in one process, from `FlashNextRef.forward` alone: a prompt chunk as it is, a
    verify row as the decode step of its path (a copy of its parent's state)."""

    def __init__(self, ref):
        from engine.models.flashnext.ref import FNState
        self.ref, self.st, self.states = ref, FNState(), None

    def prefill(self, tokens):
        return self.ref.forward(torch.tensor(tokens), self.st)

    def verify(self, tokens, parents):
        self.states, out = [], []
        for t, p in zip(tokens, parents):
            st = (self.states[p] if p >= 0 else self.st).clone()
            out.append(self.ref.forward(torch.tensor([t]), st))
            self.states.append(st)
        return torch.cat(out)

    def commit(self, path):
        if path:
            self.st = self.states[path[-1]]

    def state(self) -> dict:
        from engine.models.flashnext.ref import FNModelState
        st = self.st
        out = dict(FNModelState(st).sections())
        for name, rows in (("k", st.k), ("v", st.v), ("ik", st.ik)):
            out.update({f"{name}.{li}": t for li, t in rows.items()})
        return out


class Lfm2Alone:
    """The reference in one process: `Lfm2MoeRef.rows`, `commit` and `prefill` as they are."""

    def __init__(self, ref):
        self.ref, self.st = ref, ref.new_state(4096)

    def prefill(self, tokens):
        return self.ref.prefill(torch.tensor(tokens), self.st)

    def verify(self, tokens, parents):
        return self.ref.rows(torch.tensor(tokens), parents, self.st)

    def commit(self, path):
        self.ref.commit(self.st, path)

    def state(self) -> dict:
        st, n = self.st, self.st.length
        out = {f"conv.{li}": t for li, t in st.conv.items()}
        for li in st.k:
            out[f"k.{li}"], out[f"v.{li}"] = st.k[li][:, :n], st.v[li][:, :n]
        return out


def alone(family: str, dtype: str):
    mod, model = build(family, dtype)
    return (Lfm2Alone if family == "lfm2_moe" else FlashNextAlone)(model)


def same(a, b, what) -> None:
    assert a.dtype == b.dtype and a.shape == b.shape and torch.equal(a, b), what


def same_states(got: dict, want: dict, what) -> None:
    assert sorted(got) == sorted(want), (what, sorted(got), sorted(want))
    for name in want:
        same(got[name], want[name], (what, name))


def reference(family: str, dtype: str) -> tuple:
    """(the scenario's logits, the state at each check) of the single-process reference."""
    ref = alone(family, dtype)
    snaps = []
    logits = play(ref, check=lambda: snaps.append({k: v.clone() for k, v in ref.state().items()}))
    return [x for x in logits if x is not None], snaps


def _in_process(family: str, dtypes: tuple) -> str:
    if torch is None:
        raise _skip("no torch here")
    cuts = []
    for dtype in dtypes:
        want, snaps = reference(family, dtype)
        mod, model = build(family, dtype)
        layers = model.cfg.num_hidden_layers
        whole = mod.stage_adapter(model, 0, layers)
        assert whole.cuts == tuple(range(1, layers)), whole.cuts
        states = []
        got = play(Whole(whole), check=lambda: states.append({k: v.clone() for k, v in whole.state().items()}))
        for a, b in zip([x for x in got if x is not None], want, strict=True):
            same(a, b, (family, dtype, "whole"))
        for a, b in zip(states, snaps, strict=True):
            same_states(a, b, (family, dtype, "whole"))
        for k in whole.cuts:
            front, tail = mod.stage_adapter(model, 0, k), mod.stage_adapter(model, k, layers)
            assert (front.role, tail.role, front.cut, tail.cut) == ("front", "tail", k, k)
            states = []

            def check():
                both = {**front.state(), **tail.state()}
                assert len(both) == len(front.state()) + len(tail.state())      # the names are disjoint
                assert all(int(n.rsplit(".", 1)[1]) < k for n in front.state())
                assert all(int(n.rsplit(".", 1)[1]) >= k for n in tail.state())
                states.append({n: t.clone() for n, t in both.items()})
            got = play(Pair(front, tail), check=check)
            for i, (a, b) in enumerate(zip([x for x in got if x is not None], want, strict=True)):
                same(a, b, (family, dtype, k, "logits", i))
            for a, b in zip(states, snaps, strict=True):
                same_states(a, b, (family, dtype, k))
            cuts.append(k)
    return f"{layers} layers, cuts {sorted(set(cuts))} in {'/'.join(dtypes)}: logits, sections and KV rows equal"


def test_flashnext_every_cut_in_one_process():
    return note(_in_process("qwen4_exp", ("float32", "bfloat16")))


def test_lfm2_moe_every_cut_in_one_process():
    return note(_in_process("lfm2_moe", ("float32",)))


def test_one_flipped_bit_in_the_carry_changes_the_logits():
    if torch is None:
        raise _skip("no torch here")
    for family, dtype in (("qwen4_exp", "bfloat16"), ("lfm2_moe", "float32")):
        mod, model = build(family, dtype)
        layers = model.cfg.num_hidden_layers
        want = mod.stage_adapter(model, 0, layers).rows(S.Block(PROMPT), None)
        for k in range(1, layers):
            front, tail = mod.stage_adapter(model, 0, k), mod.stage_adapter(model, k, layers)
            carry = front.rows(S.Block(PROMPT), None)
            dtype_code, dims, buf = front.encode(carry)
            raw = bytearray(bytes(buf))
            assert len(raw) == len(PROMPT) * front.carry_bytes_per_row
            same(tail.rows(S.Block(PROMPT), tail.decode(dtype_code, dims, bytearray(raw))), want, (family, k))
            raw[len(raw) // dims[0] // dims[1] - 2] ^= 0x40   # the first value's top mantissa bit
            tail.reset()
            got = tail.rows(S.Block(PROMPT), tail.decode(dtype_code, dims, raw))
            assert not torch.equal(got, want), (family, k)


def _two_processes(family: str, dtype: str) -> str:
    if torch is None:
        raise _skip("no torch here")
    want, snaps = reference(family, dtype)
    _, model = build(family, dtype)
    layers = model.cfg.num_hidden_layers
    per_row = total = 0
    for k in range(1, layers):
        front, co, link = split(family, k, dtype, dump=True)
        try:
            assert k in co.adapter.cuts
            counted = Counted(co, link)
            fronts, tails, probes, lengths = [], [], [], []

            def check():
                probes.append(co.probe())
                lengths.append(co.length)
                fronts.append(torch.load(front.dump))
                tails.append({n: t.clone() for n, t in co.adapter.state().items()})
            got = play(counted, check=check)
            for i, (a, b) in enumerate(zip([x for x in got if x is not None], want, strict=True)):
                same(a, b, (family, k, "logits", i))
            for i, (f, t, snap) in enumerate(zip(fronts, tails, snaps, strict=True)):
                assert not set(f) & set(t), (k, sorted(set(f) & set(t)))
                same_states({**f, **t}, snap, (family, k, "state", i))
                assert probes[i]["length"] == lengths[i], (k, probes[i], lengths[i])
            # the front's digest is the digest of the reference's rows of its layers
            alone_front = S.StageAdapter()
            alone_front.state = lambda: {n: t for n, t in snaps[-1].items() if n in fronts[-1]}
            assert probes[-1]["digest"] == alone_front.digest(), k
            per_row = co.adapter.carry_bytes_per_row
            total = counted.carried
            assert counted.rounds == 8 and total == 36 * per_row and co.replays == 0
            hidden = model.cfg.hidden_size * getattr(model.cfg, "hc_count", 1)
            assert per_row == hidden * torch.empty(0, dtype=getattr(torch, dtype)).element_size()
        finally:
            link.close()
            front.kill()
    return (f"{layers} layers, cuts {list(range(1, layers))} in {dtype}: bit-equal; "
            f"{per_row} bytes a row, {total} carry bytes over 36 rows a cut")


def test_flashnext_every_cut_in_two_processes():
    return note(_two_processes("qwen4_exp", "bfloat16"))


def test_lfm2_moe_every_cut_in_two_processes():
    return note(_two_processes("lfm2_moe", "float32"))


def test_the_shared_modules_name_no_family():
    import test_adapter_guards as G
    for name in ("link.py", "stages.py"):
        with open(os.path.join(ROOT, "engine", "methods", name), encoding="utf-8") as f:
            assert G.lint_hits(f.read()) == [], name
    for pkg in ("flashnext", "lfm2_moe"):
        assert os.path.isfile(os.path.join(ROOT, "engine", "models", pkg, "stages.py"))


# ------------------------------------------------------------------ failures: stall, kill, cut, desync; idle
def test_a_stalled_front_ends_the_request_inside_the_deadline():
    from test_linkrelay import Relay
    assert (S.STEP_S, S.CHUNK_S, L.HEARTBEAT_S) == (5.0, 60.0, 1.0)
    front = Front("toy", 2)
    r = Relay(front.port)
    co = S.Coordinator(ToyStage(2, 4), step_deadline=1.0, chunk_deadline=2.0)
    try:
        front.connect(co, to=f"127.0.0.1:{r.port}")
        co.prefill(PROMPT)
        r.cmd(cmd="blackhole")
        t0 = time.monotonic()
        e = raises(S.StageDown, lambda: co.verify([1, 2], [-1, 0]))
        took = time.monotonic() - t0
        assert e.code == "stage_down" and 0.9 <= took < 3.0, (e.code, took)   # room for a loaded box
        h = co.health()
        assert h["status"] == "degraded" and h["stage"]["link"]["state"] == "down", h
        assert h["stage"]["link"]["reason"] == "timeout", h
        t0 = time.monotonic()
        raises(S.StageDown, lambda: co.prefill([1]))        # no new request waits on a dead link
        assert time.monotonic() - t0 < 1.0
        r.cmd(cmd="heal")
        # a prompt chunk has its own, longer deadline
        front.connect(co, to=f"127.0.0.1:{r.port}")
        r.cmd(cmd="blackhole")
        t0 = time.monotonic()
        raises(S.StageDown, lambda: co.prefill(PROMPT))
        took = time.monotonic() - t0
        assert 1.9 <= took < 4.5, took
        r.cmd(cmd="heal")
        # an idle link: the heartbeat finds the stall without a request
        front.connect(co, to=f"127.0.0.1:{r.port}")
        r.cmd(cmd="blackhole")
        t0 = time.monotonic()
        while co.health()["status"] == "ok" and time.monotonic() - t0 < 10:
            time.sleep(0.05)
        took = time.monotonic() - t0
        assert co.health()["stage"]["link"]["state"] == "down" and took < 7.0, took
    finally:
        r.stop()
        front.kill()
    return note("step 1.0 s and chunk 2.0 s deadlines held; the idle heartbeat found the stall in %.1f s" % took)


def test_a_stalled_decode_step_ends_inside_the_step_deadline():
    """A decode token crosses as a chain of one row. It gets the step's deadline, not a prompt
    chunk's: a GPU run waited 60 s on a stalled front for one row."""
    from test_linkrelay import Relay
    front = Front("toy", 2)
    r = Relay(front.port)
    co = S.Coordinator(ToyStage(2, 4), step_deadline=1.0, chunk_deadline=20.0)
    took = {}
    try:
        for what, ask in (("a decode step", lambda: co.prefill([7], graph=1)),
                          ("a chunk of one row", lambda: co.prefill([7])),
                          ("a verify block", lambda: co.verify([1, 2], [-1, 0])),
                          ("a sync", co.sync), ("a probe", co.probe), ("a reset", co.reset)):
            front.connect(co, to=f"127.0.0.1:{r.port}")
            co.prefill(PROMPT)
            r.cmd(cmd="blackhole")
            t0 = time.monotonic()
            e = raises(S.StageDown, ask)
            took[what] = time.monotonic() - t0
            assert e.code == "stage_down" and 0.9 <= took[what] < 3.0, (what, took)
            assert co.health()["stage"]["link"]["reason"] == "timeout", what
            r.cmd(cmd="heal")
    finally:
        r.stop()
        front.kill()
    return note("step deadline 1.0 s against a chunk's 20 s: " + ", ".join(f"{k} {v:.1f} s" for k, v in took.items()))


def test_a_killed_front_is_stage_down_and_a_new_one_starts_a_new_epoch():
    want = play(Whole(ToyStage(0, 4)))
    front, co, link = split("toy", 2)
    try:
        co.prefill(PROMPT)
        co.verify([1, 2, 3], [-1, 0, 1])
        co.commit([0, 1])
        assert co.epoch == 1 and co.length == len(PROMPT) + 2
        front.kill()
        t0 = time.monotonic()
        e = raises(S.StageDown, lambda: co.verify([4], [-1]))
        assert e.code == "stage_down" and time.monotonic() - t0 < 4.0
        assert co.health()["stage"]["link"]["state"] == "down"
        raises(S.StageDown, lambda: co.probe())
    finally:
        front.kill()
    front = Front("toy", 2)
    try:
        link = front.connect(co)
        assert co.epoch == link.epoch == 2 and co.health()["stage"]["link"] == {"state": "up", "epoch": 2}
        assert co.length == 0 and co.probe() == {"length": 0, "digest": ToyStage(0, 2).digest()}
        assert play(co) == want                             # nothing of epoch 1 is in the answers
    finally:
        link.close()
        front.kill()


def test_a_cut_link_reconnects_with_a_new_epoch_and_an_empty_front():
    from test_linkrelay import Relay
    want = play(Whole(ToyStage(0, 4)))
    front = Front("toy", 3)
    r = Relay(front.port)
    co = S.Coordinator(ToyStage(3, 4))
    try:
        via = f"127.0.0.1:{r.port}"
        front.connect(co, to=via)
        co.prefill(PROMPT)
        assert co.probe()["length"] == len(PROMPT)
        r.cmd(cmd="cut")
        t0 = time.monotonic()
        e = raises(S.StageDown, lambda: co.verify([4], [-1]))
        assert e.code == "stage_down" and time.monotonic() - t0 < 4.0
        raises(L.LinkError, lambda: front.connect(co, to=via))     # still cut: no link, still down
        assert co.health()["status"] == "degraded"
        r.cmd(cmd="heal")
        link = front.connect(co, to=via)
        # the same front process, which held 9 rows of epoch 1: they are gone
        assert co.epoch == 2 and co.probe() == {"length": 0, "digest": ToyStage(0, 3).digest()}
        assert play(co) == want
        link.close()
    finally:
        r.stop()
        front.kill()


def test_a_desync_replays_the_tokens():
    front, co, link = split("toy", 1, replay_rows=4)
    try:
        co.prefill(PROMPT)
        co.verify([7, 19, 23], [-1, 0, 0])
        co.commit([0, 2])
        co._pending = None                    # the front never hears of the commit
        got = co.verify([5, 6], [-1, 0])
        assert co.replays == 1 and co.length == len(PROMPT) + 2
        # both sides ran the committed tokens again as prompt chunks, then the block
        ref = ToyStage(0, 4)
        tokens = PROMPT + [7, 23]
        for at in range(0, len(tokens), 4):
            ref.rows(S.Block(tokens[at:at + 4], head="none"), None)
        assert got == ref.rows(S.Block([5, 6], [-1, 0]), None)
        co.commit([0, 1])
        ref.commit([0, 1])
        local = ToyStage(0, 1)
        for at in range(0, len(tokens), 4):
            local.rows(S.Block(tokens[at:at + 4]), None)
        local.rows(S.Block([5, 6], [-1, 0]), None)
        local.commit([0, 1])
        assert co.probe() == {"length": len(tokens) + 2, "digest": local.digest()} and co.replays == 1
    finally:
        link.close()
        front.kill()


def _idle_share(front, co, seconds: float = 5.0) -> float:
    co.prefill(PROMPT)
    time.sleep(0.5)
    pings = co.link.stats.tx_payload.get(L.PING, 0), co.link.stats.tx_frames
    c0, t0 = front.cpu_seconds(), time.monotonic()
    time.sleep(seconds)
    used, wall = front.cpu_seconds() - c0, time.monotonic() - t0
    assert co.link.stats.tx_frames - pings[1] >= 3 and co.health()["status"] == "ok"   # the heartbeat ran
    return used / wall


def test_an_idle_follower_uses_under_one_percent_of_a_core():
    front, co, link = split("toy", 2)
    try:
        share = _idle_share(front, co)
        assert share < 0.01, share
    finally:
        link.close()
        front.kill()
    return note(f"{100 * share:.2f} % of a core over 5 s with the heartbeat running")


def test_a_family_front_restarts_into_a_new_epoch():
    if torch is None:
        raise _skip("no torch here")
    want, snaps = reference("lfm2_moe", "float32")
    front, co, link = split("lfm2_moe", 3, dump=True)
    try:
        co.prefill(PROMPT)
        co.verify([7, 19, 23], [-1, 0, 0])
        co.commit([0, 2])
        front.kill()
        e = raises(S.StageDown, lambda: co.verify([4], [-1]))
        assert e.code == "stage_down"
    finally:
        front.kill()
    front = Front("lfm2_moe", 3, dump=True)
    try:
        link = front.connect(co)
        assert co.epoch == 2 and co.length == 0 and co.probe()["length"] == 0
        got = [x for x in play(co) if x is not None]
        for a, b in zip(got, want, strict=True):
            same(a, b, "after the restart")
        co.probe()
        same_states({**torch.load(front.dump), **co.adapter.state()}, snaps[-1], "after the restart")
    finally:
        link.close()
        front.kill()


def test_an_idle_family_follower_uses_under_one_percent_of_a_core():
    if torch is None:
        raise _skip("no torch here")
    front, co, link = split("lfm2_moe", 3)
    try:
        share = _idle_share(front, co)
        assert share < 0.01, share
    finally:
        link.close()
        front.kill()
    return note(f"{100 * share:.2f} % of a core over 5 s with the heartbeat running")


if __name__ == "__main__":
    if "--follower" in sys.argv:
        sys.exit(follower_main(sys.argv[1:]))
    from scriptmode import run
    run(globals())
