"""Two prefills hashing at once cannot tear the prefix hash memo.

engine/cache.py keeps one module-global `_PrefixHashMemo` and every slot's prefill calls it
(`_prefill`, for the prompt's `prefix_hashes`). Two handler threads interleave inside
`__call__`: one reads the memo's keys, the other swaps them, the first slices a hash list
that is no longer its prefix. The result is a too-short hash list (an IndexError mid-
prefill) or another prompt's hashes stored under these keys (silent cache misses).

  * a second prefill that lands while the first is between reading the memo's keys and
    reading its hashes cannot tear the first's hash list — each gets exactly
    `prefix_hashes` of its own keys;
  * under a burst of concurrent prefills every caller gets its own hashes, every time.

Run: python tests/test_hash_memo_race.py
"""

from __future__ import annotations

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import cache  # noqa: E402


def test_a_second_prefill_cannot_tear_the_firsts_hash_list():
    memo = cache._PrefixHashMemo()
    keys_a = [1] * 1000 + [2] * 50
    keys_b = [7]
    memo(list(keys_a))                    # the memo knows the head already

    inside = threading.Event()            # the first is between the keys read and the hashes
    resume = threading.Event()
    b_returned = threading.Event()        # the second got through (no lock: it will)
    got = {}

    def patched(a, b, step=4096):
        c = orig(a, b, step)
        if threading.current_thread() is ta:
            inside.set()
            resume.wait(5)
        return c

    orig = cache._common_prefix
    ta = threading.Thread(target=lambda: got.setdefault("a", memo(keys_a)))
    tb = threading.Thread(target=lambda: (got.setdefault("b", memo(keys_b)),
                                          b_returned.set()))
    cache._common_prefix = patched
    try:
        ta.start()
        assert inside.wait(5), "the first prefill never reached the memo"
        tb.start()
        b_returned.wait(1.0)              # unsynchronized: the second swaps the memo here
        resume.set()
        ta.join(5)
        tb.join(5)
    finally:
        cache._common_prefix = orig
    assert not ta.is_alive() and not tb.is_alive(), "a prefill never left the memo"
    assert "a" in got and "b" in got, f"a prefill never returned: {sorted(got)}"
    assert got["a"] == cache.prefix_hashes(keys_a), \
        f"the first prefill got a torn hash list: {len(got['a'])} entries for {len(keys_a)} tokens"
    assert got["b"] == cache.prefix_hashes(keys_b), "the second prefill got the wrong hashes"


def test_a_burst_of_concurrent_prefills_each_get_their_own_hashes():
    memo = cache._PrefixHashMemo()
    prompts = [[(i * 7 + 3 * j) % 977 + 1 for j in range(400)] for i in range(8)]
    wrong = []

    def hammer(keys):
        for _ in range(50):
            if memo(keys) != cache.prefix_hashes(keys):
                wrong.append(keys[:3])

    threads = [threading.Thread(target=hammer, args=(k,)) for k in prompts]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not any(t.is_alive() for t in threads), "a prefill never left the memo"
    assert not wrong, f"{len(wrong)} prefills got hashes that are not their prompts' own"


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  {name:56s} ok")
            passed += 1
    print(f"{passed} passed")