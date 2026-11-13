"""What cutting a model into two stages costs by itself, in one process and with no link (ENG-307).

    python3 tools/stage_cut_cost.py --model PACK --cut K [--runs 20] [--prompt-rows 512]
            [--steps 64] [--blocks 16] [--block-rows 8] [--max-len 8192] [--graphs on|off]
            [--prime on|off] [--order alt|ab|ba] [--out FILE.json]

Arm A runs every block through the whole model, the layers [0, layers) in one pass. Arm B runs the
same block through the front [0, K), copies the carry to the host and back (the round trip a link
makes, without the link), and runs the tail [K, layers). Both arms are the family's stage adapters
over one copy of the weights (engine/models/<family>/stages.py), eager (or with `--graphs on` each
stage's own captured graphs, for a family whose stage has them), and take the same tokens:
a prompt chunk, then decode steps, then verify blocks with their commits. The two arms run in
turn, the first round is a warm-up, and every output of B is compared with A's bit for bit.

The baseline has to be clean before the difference means anything (ENG-423). The arms share what
the model shares: one copy of the weights and, for a family that reads rows of a table by token,
that table's cache. An arm that goes first with tokens nobody read yet pays for them, and the arm
after it does not. So a run is: the run's blocks once without a clock (`--prime on`, the default),
the two arms (`--order alt`: which goes first changes every run), then the uncut arm once more.
`baseline.position_share` is that last pass against the uncut arm's own first one, per kind: what
going later is worth with nothing cut. The baseline is called clean when it is within 2 % for
every kind; `by_order` gives each arm's median by who went first. `--prime off --order ab` is the
tool as it was: run both and the pair shows what the priming removed.

It prints one JSON object: per kind of block the median, p10 and p90 of each arm in ms, the
difference and its share of A, the carry bytes, whether every output was equal, the baseline's
check and every setting. This is the cost of the host sync and the copy at the cut on this device.
It is not a link cost and not a serving speed: both stages share one GPU here.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

KINDS = ("chunk", "step", "verify", "commit")
#: the uncut arm run again after both arms may differ from its first pass by this share of it
CLEAN = 0.02


def _tokens(n: int, seed: int, vocab: int) -> list:
    out, x = [], seed * 2654435761 % (1 << 32) or 1
    for _ in range(n):
        x = (1103515245 * x + 12345) % (1 << 31)
        out.append(3 + x % (vocab - 3))
    return out


def _pct(xs: list, q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def measure(model: str, cut: int, runs: int = 20, prompt_rows: int = 512, steps: int = 64, blocks: int = 16,
            block_rows: int = 8, max_len: int = 8192, device: str = "cuda", log=print, graphs: bool = False,
            prime: bool = True, order: str = "alt") -> dict:
    import inspect

    import torch

    from engine.methods import stages as S
    from engine.models import architecture
    with open(os.path.join(model, "config.json")) as f:
        raw = json.load(f)
    arch = architecture(raw)
    fam = S.family_stages(arch)
    layers = int((raw.get("text_config") or raw)["num_hidden_layers"])
    vocab = min(1000, int((raw.get("text_config") or raw)["vocab_size"]))
    # a family whose stage captures graphs takes the choice; any other runs eager
    captures = "graphs" in inspect.signature(fam.stage_adapter).parameters
    if graphs and not captures:
        raise SystemExit(f"--graphs on: the {arch.name} stages run eager")
    opts = {"options": {"graphs": "captured" if graphs else "eager"}} if captures else {}
    whole = fam.open_stage(model, 0, layers, max_len, device=device, log=log, **opts)
    if not 0 < cut < whole.layers or cut not in whole.cuts:
        raise SystemExit(f"--cut {cut}: this model's cuts are {list(whole.cuts)}")
    kw = {"graphs": graphs} if captures else {}
    front = fam.stage_adapter(whole.model, 0, cut, **kw)
    tail = fam.stage_adapter(whole.model, cut, whole.layers, **kw)
    step_tag = getattr(fam, "STEP", 0)
    cuda = str(device).startswith("cuda")

    def sync() -> None:
        if cuda:
            torch.cuda.synchronize()

    def run_a(block):
        out = whole.rows(block, None)
        sync()
        return out

    def run_b(block):
        dtype, dims, buf = front.encode(front.rows(block, None))
        out = tail.rows(block, tail.decode(dtype, dims, buf))
        sync()
        return out

    def one(arm, seed: int, times: dict | None, keep: list | None) -> None:
        ads = (whole,) if arm is run_a else (front, tail)
        for ad in ads:
            ad.reset()
        sync()
        work = [("chunk", S.Block(_tokens(prompt_rows, seed, vocab), None, head="last"), None)]
        work += [("step", S.Block([t], None, graph=step_tag), None) for t in _tokens(steps, seed + 1, vocab)]
        for i in range(blocks):
            work.append(("verify", S.Block(_tokens(block_rows, seed + 2 + i, vocab), list(range(-1, block_rows - 1))),
                         list(range(block_rows))))
        for kind, block, path in work:
            t0 = time.perf_counter()
            out = arm(block)
            dt = time.perf_counter() - t0
            if times is not None:
                times[kind].append(dt * 1e3)
            if keep is not None:
                keep.append(out.clone())
            if path is not None:
                t0 = time.perf_counter()
                for ad in ads:
                    ad.commit(path)
                sync()
                if times is not None:
                    times["commit"].append((time.perf_counter() - t0) * 1e3)

    if order not in ("alt", "ab", "ba"):
        raise SystemExit(f"--order {order}: alt, ab or ba")
    with torch.no_grad():
        ta, tb, tc = ({k: [] for k in KINDS} for _ in range(3))
        # each arm's times by who went first in the run: "ab" the uncut arm, "ba" the cut arm
        by = {o: ({k: [] for k in KINDS}, {k: [] for k in KINDS}) for o in ("ab", "ba")}
        equal, first_diff = True, None
        for r in range(runs + 1):
            ka, kb = [], []
            if prime:
                one(run_a, r, None, None)                # the run's tokens read once, off the clock
            first = order if order != "alt" else ("ab", "ba")[r % 2]
            warm = r == 0
            xa, xb = {k: [] for k in KINDS}, {k: [] for k in KINDS}
            for arm in first:
                if arm == "a":
                    one(run_a, r, None if warm else xa, ka)
                else:
                    one(run_b, r, None if warm else xb, kb)
            one(run_a, r, None if warm else tc, None)    # the uncut arm once more, after both
            for k in KINDS:
                ta[k] += xa[k]
                tb[k] += xb[k]
                by[first][0][k] += xa[k]
                by[first][1][k] += xb[k]
            for i, (a, b) in enumerate(zip(ka, kb, strict=True)):
                if not torch.equal(a, b):
                    equal = False
                    first_diff = first_diff or {"run": r, "output": i}
    rows = {"chunk": prompt_rows, "step": 1, "verify": block_rows, "commit": block_rows}
    kinds, position = {}, {}
    for k in KINDS:
        a, b, c = _pct(ta[k], 0.5), _pct(tb[k], 0.5), _pct(tc[k], 0.5)
        position[k] = round((c - a) / a, 4) if a else None
        kinds[k] = {"rows": rows[k], "n": len(ta[k]),
                    "uncut_ms": {"p50": round(a, 4), "p10": round(_pct(ta[k], 0.1), 4), "p90": round(_pct(ta[k], 0.9), 4)},
                    "cut_ms": {"p50": round(b, 4), "p10": round(_pct(tb[k], 0.1), 4), "p90": round(_pct(tb[k], 0.9), 4)},
                    "uncut_again_ms": {"p50": round(c, 4), "p10": round(_pct(tc[k], 0.1), 4),
                                       "p90": round(_pct(tc[k], 0.9), 4)},
                    "cut_minus_uncut_ms": round(b - a, 4), "share_of_uncut": round((b - a) / a, 4) if a else None,
                    "carry_bytes": 0 if k == "commit" else rows[k] * front.carry_bytes_per_row}
    by_order = {("uncut_first" if o == "ab" else "cut_first"): {
        k: {"n": len(xa[k]), "uncut_p50": round(_pct(xa[k], 0.5), 4), "cut_p50": round(_pct(xb[k], 0.5), 4)}
        for k in KINDS if xa[k]} for o, (xa, xb) in by.items() if xa["chunk"]}
    clean = all(v is not None and abs(v) <= CLEAN for k, v in position.items() if k != "commit")
    baseline = {"prime": "on" if prime else "off", "order": order, "position_share": position, "clean": clean,
                "rule": f"the uncut arm after both arms within {CLEAN:.0%} of its own first pass, for chunk, step "
                        "and verify (p50)", "by_order": by_order}
    return {"what": f"the cost of the cut in one process: {'graphs a stage' if graphs else 'eager'}, host round "
                    "trip of the carry, no link",
            "label": "one-box emulation, not a link cost and not a serving speed",
            "equal": equal, "first_diff": first_diff, "baseline": baseline, "kinds": kinds,
            "args": {"model": model, "family": arch.name, "layers": whole.layers, "cut": cut, "runs": runs,
                     "warmup_runs": 1, "prompt_rows": prompt_rows, "steps": steps, "blocks": blocks,
                     "block_rows": block_rows, "max_len": max_len, "device": device,
                     "graphs": "captured" if graphs else "eager", "slots": 1,
                     "prime": "on" if prime else "off", "order": order, "uncut_again": "every run",
                     "carry_bytes_per_row": front.carry_bytes_per_row, "carry_dtype": front.carry_dtype,
                     "carry_dims": list(front.carry_dims)}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True)
    ap.add_argument("--cut", type=int, required=True)
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--prompt-rows", type=int, default=512)
    ap.add_argument("--steps", type=int, default=64)
    ap.add_argument("--blocks", type=int, default=16)
    ap.add_argument("--block-rows", type=int, default=8)
    ap.add_argument("--max-len", type=int, default=8192)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--graphs", default="off", choices=("on", "off"),
                    help="on: each arm's stages capture their own graphs (a family whose stage has them)")
    ap.add_argument("--prime", default="on", choices=("on", "off"),
                    help="on: a run's blocks pass once without a clock before either arm, so no arm pays for "
                         "rows the other finds cached; off: the tool as it first was")
    ap.add_argument("--order", default="alt", choices=("alt", "ab", "ba"),
                    help="which arm goes first in a run: alt changes it every run, ab the uncut arm, ba the cut arm")
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    res = measure(a.model, a.cut, a.runs, a.prompt_rows, a.steps, a.blocks, a.block_rows, a.max_len, a.device,
                  log=lambda line: print(line, file=sys.stderr, flush=True), graphs=a.graphs == "on",
                  prime=a.prime == "on", order=a.order)
    text = json.dumps(res, indent=1)
    if a.out:
        with open(a.out, "w") as f:
            f.write(text + "\n")
    print(text)
    return 0 if res["equal"] else 1


if __name__ == "__main__":
    sys.exit(main())
