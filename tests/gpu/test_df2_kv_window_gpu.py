"""On the board: SPD-177's window buffer over real driver pages (engine/drafters/dflash2.py `_window_buffer`).
Rows past the floor keep what was written; rows below it read and write one shared page without a fault;
a lowered floor maps fresh zeroed pages and keeps the rows above the old floor; the pool's account follows;
a sync whose rows' pages went back writes nothing (#174).
Run on the box under the lock: python tests/gpu/test_df2_kv_window_gpu.py
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
import torch  # noqa: E402

from engine import kvpool  # noqa: E402
from engine.drafters import dflash2 as D  # noqa: E402

SHAPE = (5, 8, 65536, 128)


def test_window_over_driver_pages():
    assert kvpool.Driver.get() is not None, "needs CUDA virtual memory management"
    pool = kvpool.KVPool(hard_floor=0, margin_rows=0, meminfo=lambda: 1 << 50)
    W = D._window_buffer(kvpool)
    b = W(SHAPE, 2, torch.bfloat16, "cuda", owner="w", pool=pool, window=2048)
    t = b.tensor
    b.ensure(20000)
    t[:, :, :20000] = torch.arange(20000, device="cuda", dtype=torch.float32)[:, None].to(torch.bfloat16)
    b.ensure(60000)                                   # the floor moves to 49152
    assert b.floor == 49152 and b.alias
    t[:, :, 50000:60000] = 7.0
    torch.cuda.synchronize()
    t[:, :, :40000].float().sum().item()              # below the floor: the shared page, no fault
    t[:, :, 1000:3000] = 3.0                          # a write below the floor lands in the shared page
    torch.cuda.synchronize()
    assert bool((t[:, :, 50000:60000] == 7.0).all()), "rows above the floor keep what was written"
    assert pool.owner_bytes("w") == b.committed_bytes() == 2 * 40 * b.page
    b.set_floor(b._floor_for(51000))                  # a resume at 51K: its window mapped again
    assert b.floor == 32768
    b.ensure(51000)
    torch.cuda.synchronize()
    assert b.floor == 32768
    assert bool((t[:, :, 32768:49152] == 0).all()), "the uncovered pages are fresh and zeroed"
    assert bool((t[:, :, 50000:51000] == 7.0).all()), "rows above the old floor are kept"
    b.trim(0)
    assert b.floor == 0 and not b.alias and b.committed_bytes() == 0
    b.close()
    return "floor up, rows kept, alias reads/writes, floor down, zeroed, trimmed"


def test_a_resume_below_the_window_over_driver_pages():
    """SPD-211: `check_window` on the device: the rows from the lowered floor are mapped and take a write."""
    assert kvpool.Driver.get() is not None, "needs CUDA virtual memory management"
    out = D.check_window("cuda")
    assert out[-1] == "written and read back on the device", out
    return "; ".join(out)


def test_a_trim_below_the_floor_then_the_spill_reads_the_rows():
    """SRV-885 on the device: the 27B soak's shape. A 40,188-row chat holds the floor at 24,576; a new chat
    ends at 21,184 rows; the end-of-request trim brings the floor down to 8,192; the spill save then reads
    every row it keeps. Before the fix that read was an illegal memory access (run this in its own process:
    a fault poisons the context)."""
    assert kvpool.Driver.get() is not None, "needs CUDA virtual memory management"
    pool = kvpool.KVPool(hard_floor=0, margin_rows=256, meminfo=lambda: 1 << 50)
    ps = kvpool.PagedSet("s", pool=pool)
    W = D._window_buffer(kvpool)
    w = ps.add(W(SHAPE, 2, torch.bfloat16, "cuda", pool=pool, window=2048))
    t = w.tensor
    ps.ensure(40188)
    t[:, :, 40188 - 2048:40188] = 1.0
    assert w.floor == 24576
    ps.ensure(21184)
    t[:, :, :21184] = 2.0                             # the new chat: below the old floor, the shared page
    ps.trim(21184)
    assert w.floor == 8192
    total = float(t[:, :, :21184 + 256].float().sum().item())    # the spill save's read of the rows kept
    torch.cuda.synchronize()
    assert total == total, "read"
    ps.ensure(21184)
    t[:, :, 21184 - 2048:21184] = 3.0                 # the next turn writes its window: real pages again
    torch.cuda.synchronize()
    assert bool((t[:, :, 21184 - 2048:21184] == 3.0).all())
    ps.trim(0)
    assert not w.alias and w.committed_bytes() == 0
    w.close()
    return "floor 24,576 -> 8,192 at the trim; rows 0..21,440 read without a fault; the window maps again"


def test_a_pending_sync_after_the_pages_went_back():
    """#174 on the device: a side slot's warm-up leaves a sync pending, its pages go back (`trim(0)`), and
    the slot's first request reads the drafter's views. Before the fix the pending sync wrote rows
    2,600..2,608 onto unmapped pages: an illegal memory access (run this in its own process: a fault
    poisons the context). Rows on pages still held are written as before."""
    import types
    assert kvpool.Driver.get() is not None, "needs CUDA virtual memory management"
    pool = kvpool.KVPool(hard_floor=0, margin_rows=256, meminfo=lambda: 1 << 50)
    ps = kvpool.PagedSet("s", pool=pool)
    W = D._window_buffer(kvpool)
    d = D.DFlash2Drafter.__new__(D.DFlash2Drafter)
    d._psync, d._partial, d.ctx_len, d._tap_rows, d._want = [], False, 0, [], [0]
    d._ckb = ps.add(W(SHAPE, 2, torch.bfloat16, "cuda", pool=pool, name="draft.k", window=2048))
    d._cvb = ps.add(W(SHAPE, 2, torch.bfloat16, "cuda", pool=pool, name="draft.v", window=2048))
    d._ck, d._cv = d._ckb.tensor, d._cvb.tensor
    d._cs = torch.zeros(SHAPE[2], dtype=torch.uint8, device="cuda")
    d.eng = types.SimpleNamespace(device="cuda")
    one = lambda n: torch.ones(SHAPE[1], n, SHAPE[3], dtype=torch.bfloat16, device="cuda")  # noqa: E731
    m = types.SimpleNamespace(dtype=torch.bfloat16, project_context=lambda x: x,
                              context_kv=lambda h, pos: [(one(len(pos)), one(len(pos)))] * SHAPE[0])
    d.module, d._build = types.SimpleNamespace(w=object()), lambda: m
    fused = torch.ones(8, 4, dtype=torch.bfloat16, device="cuda")
    ps.ensure(2700)                                   # the warm-up
    d._psync.append((fused, 2600, 8))
    d.ctx_len = 2608
    d.kv_views()                                      # held: written
    torch.cuda.synchronize()
    assert bool((d._ck[:, :, 2600:2608] == 1).all()) and bool((d._cs[2600:2608] == 1).all())
    d._psync.append((fused, 2608, 8))                 # the last block's sync, still pending
    d.ctx_len = 2616
    ps.trim(0)                                        # the slot's build gives its pages back
    assert ps.committed_bytes() == 0
    d.kv_views()                                      # the first request's carry reads the views
    torch.cuda.synchronize()
    assert not d._psync and d._partial and d.ctx_len == 2608 and ps.committed_bytes() == 0
    d._tap_rows = [fused]
    D.DFlash2Drafter.sync(d, list(range(8)), None, 2608)      # the router's catch-up, written at once
    torch.cuda.synchronize()
    assert d.ctx_len == 2608 and ps.committed_bytes() == 0
    ps.ensure(3000)                                   # the request's own rows: fresh pages, zeroed
    assert not bool(d._ck[:, :, 2600:2616].any())
    D.DFlash2Drafter.sync(d, list(range(8)), None, 2608)
    torch.cuda.synchronize()
    assert bool((d._cv[:, :, 2608:2616] == 1).all()) and d.ctx_len == 2616
    ps.trim(0)
    for b in (d._ckb, d._cvb):
        b.close()
    return "a pending sync after trim(0) writes nothing and faults nothing; held rows are written"


if __name__ == "__main__":
    print(test_window_over_driver_pages())
    print(test_a_resume_below_the_window_over_driver_pages())
    print(test_a_trim_below_the_floor_then_the_spill_reads_the_rows())
    print(test_a_pending_sync_after_the_pages_went_back())
    print("4 passed")
