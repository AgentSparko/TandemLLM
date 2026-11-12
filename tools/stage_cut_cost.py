"""What cutting a model into two stages costs by itself, in one process and with no link (ENG-307).

    python3 tools/stage_cut_cost.py --model PACK --cut K [--runs 20] [--prompt-rows 512]
            [--steps 64] [--blocks 16] [--block-rows 8] [--max-len 8192] [--out FILE.json]

Arm A runs every block through the whole model, the layers [0, layers) in one pass. Arm B runs the
same block through the front [0, K), copies the carry to the host and back (the round trip a link
makes, without the link), and runs the tail [K, layers). Both arms are the family's stage adapters
over one copy of the weights (engine/models/<family>/stages.py), eager, and take the same tokens:
a prompt chunk, then decode steps, then verify blocks with their commits. The two arms run in
turn, the first round is a warm-up, and every output of B is compared with A's bit for bit.

It prints one JSON object: per kind of block the median, p10 and p90 of each arm in ms, the
difference and its share of A, the carry bytes, whether every output was equal, and every setting.
This is the cost of the host sync and the copy at the cut on this device. It is not a link cost
and not a serving speed: both stages share one GPU here.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

KINDS = ("chunk", "step", "verify", "commit")


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
            block_rows: int = 8, max_len: int = 8192, device: str = "cuda", log=print) -> dict:
    import torch

    from engine.methods import stages as S
    from engine.models import architecture
    with open(os.path.join(model, "config.json")) as f:
        raw = json.load(f)
    arch = architecture(raw)
    fam = S.family_stages(arch)
    layers = int((raw.get("text_config") or raw)["num_hidden_layers"])
    vocab = min(1000, int((raw.get("text_config") or raw)["vocab_size"]))
    whole = fam.open_stage(model, 0, layers, max_len, device=device, log=log)
    if not 0 < cut < whole.layers or cut not in whole.cuts:
        raise SystemExit(f"--cut {cut}: this model's cuts are {list(whole.cuts)}")
    front = fam.stage_adapter(whole.model, 0, cut)
    tail = fam.stage_adapter(whole.model, cut, whole.layers)
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

    with torch.no_grad():
        ta, tb = {k: [] for k in KINDS}, {k: [] for k in KINDS}
        equal, first_diff = True, None
        for r in range(runs + 1):
            ka, kb = [], []
            one(run_a, r, None if r == 0 else ta, ka)
            one(run_b, r, None if r == 0 else tb, kb)
            for i, (a, b) in enumerate(zip(ka, kb, strict=True)):
                if not torch.equal(a, b):
                    equal = False
                    first_diff = first_diff or {"run": r, "output": i}
    rows = {"chunk": prompt_rows, "step": 1, "verify": block_rows, "commit": block_rows}
    kinds = {}
    for k in KINDS:
        a, b = _pct(ta[k], 0.5), _pct(tb[k], 0.5)
        kinds[k] = {"rows": rows[k], "n": len(ta[k]),
                    "uncut_ms": {"p50": round(a, 4), "p10": round(_pct(ta[k], 0.1), 4), "p90": round(_pct(ta[k], 0.9), 4)},
                    "cut_ms": {"p50": round(b, 4), "p10": round(_pct(tb[k], 0.1), 4), "p90": round(_pct(tb[k], 0.9), 4)},
                    "cut_minus_uncut_ms": round(b - a, 4), "share_of_uncut": round((b - a) / a, 4) if a else None,
                    "carry_bytes": 0 if k == "commit" else rows[k] * front.carry_bytes_per_row}
    return {"what": "the cost of the cut in one process: eager, host round trip of the carry, no link",
            "label": "one-box emulation, not a link cost and not a serving speed",
            "equal": equal, "first_diff": first_diff, "kinds": kinds,
            "args": {"model": model, "family": arch.name, "layers": whole.layers, "cut": cut, "runs": runs,
                     "warmup_runs": 1, "prompt_rows": prompt_rows, "steps": steps, "blocks": blocks,
                     "block_rows": block_rows, "max_len": max_len, "device": device, "graphs": "eager", "slots": 1,
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
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    res = measure(a.model, a.cut, a.runs, a.prompt_rows, a.steps, a.blocks, a.block_rows, a.max_len, a.device,
                  log=lambda line: print(line, file=sys.stderr, flush=True))
    text = json.dumps(res, indent=1)
    if a.out:
        with open(a.out, "w") as f:
            f.write(text + "\n")
    print(text)
    return 0 if res["equal"] else 1


if __name__ == "__main__":
    sys.exit(main())
