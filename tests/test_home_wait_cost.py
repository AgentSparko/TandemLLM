"""The home wait is cost-aware: a conversation too big to re-read waits at home, not hops.

THE INCIDENT (2026-11-21 02:58-04:28, flashnext 3.05 on the 121 GiB box,
engine-20261121-013742.log, the ledger's seven `abandoned` rows at 900 s intervals,
completion_tokens None). An agent conversation of 133,075 tokens lived on side slot 5. Its next
turn found the home slot busy for more than the fixed SIDE_HOME_WAIT_S (10 s), hopped, and
re-read 133k rows on a slot holding nothing of it: under the load of the other resident streams
that re-read never finished inside the client's 900 s. The retry hopped again (the first retry
found its old home busy too — its own predecessor's rows were still there), and every hop
charged another conversation's rows to be re-read as well. The spiral held until the session
gave up. Waiting at home costs only the first token; a hop costs the whole prompt — and rows
prefilled before a client left stay good for its retry (`_prefill_watch`), which only a request
that stayed on its own slot can resume.

THE FIX (server/app.py): `_home_wait_budget_s` scales the home budget with the estimated
re-read cost — prompt rows / HOP_READ_TOK_S, clamped to [SIDE_HOME_WAIT_S, HOME_WAIT_MAX_S].
A small chat keeps the old 10 s floor; SRV-528's 45k conversation (re-read measured 80.9 s on
2026-10-17) now waits long enough to cover its own measured re-read; a 133k conversation waits
minutes at home before a hop is even considered.

Run: python tests/test_home_wait_cost.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import app as A  # noqa: E402


def test_small_chat_keeps_the_fixed_floor():
    assert A._home_wait_budget_s(500) == A.SIDE_HOME_WAIT_S
    assert A._home_wait_budget_s(None) == A.SIDE_HOME_WAIT_S
    return f"floor {A.SIDE_HOME_WAIT_S:g} s unchanged"


def test_srv_528_size_covers_its_measured_reread():
    # 45,778 rows were re-read in 80.9 s on 2026-10-17; the budget must cover that
    b = A._home_wait_budget_s(45_778)
    assert b > 80.9, b
    return f"45k waits {b:.0f} s (measured re-read was 80.9 s)"


def test_133k_waits_minutes_not_seconds():
    b = A._home_wait_budget_s(133_075)
    assert b > 200, b
    return f"133k waits {b:.0f} s at home instead of a 900 s re-read that never finished"


def test_the_budget_is_capped():
    assert A._home_wait_budget_s(10_000_000) == A.HOME_WAIT_MAX_S
    return f"cap {A.HOME_WAIT_MAX_S:g} s"


def test_the_budget_grows_with_the_prompt():
    small, big = A._home_wait_budget_s(2_000), A._home_wait_budget_s(100_000)
    assert big > small * 10, (small, big)
    return f"2k -> {small:g} s, 100k -> {big:g} s"


if __name__ == "__main__":
    passed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(f"  {name:72s} ok   {fn() or ''}", flush=True)
            passed += 1
    print(f"{passed} passed")