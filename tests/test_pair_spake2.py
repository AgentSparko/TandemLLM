"""The pairing exchange's arithmetic held to the shared vectors (SEC-10; engine-pair-v1).

server/pairing/spake2.py is a standard-library copy of TandemApp's proxy/pairing.py arithmetic; the two
are held equal by contracts/engine-pair-v1/fixtures, which TandemApp's tests replay byte for byte.
No server, no box files.

The test matrix (the Gherkin of SEC-10, the parts the vectors pin):

  1  the fixture files are what tools/pair_fixtures.py makes from the code    test_the_fixtures_are_current
  2  every SPAKE2 run of spake2.json, the engine's identities and the node identities of
     proxy/pairing.py: w, pA, pB, K (both sides), the transcript, KcA, KcB, cA, cB and Ke equal
                                                                             test_the_vectors_replay
  3  the engine's window answers exchange.json's step 1 byte for byte (its code, y and session),
     takes its cA, and its seal of the plaintext with Ke and the nonce is the fixture's sealed
     answer; the reference client opens it to the plaintext            test_the_window_answers_the_fixture
  4  a received element is checked: 0, 1, p-1, p, an element outside the order-q subgroup, a value
     past 512 hex digits, a non-hex string and a non-string are refused     test_elements_are_checked
  5  the seal: a changed box, tag or nonce does not open; a wrong Ke does not open
                                                                             test_the_seal_refuses_changes
  6  the swapped host key of exchange.json: the client's check of cB fails  test_a_swapped_host_key_fails

Run: python tests/test_pair_spake2.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from pair_client import FixedRng, blob_of  # noqa: E402
from server.pairing import hostkey, spake2 as S  # noqa: E402
from server.pairing.window import Window  # noqa: E402

FIX = os.path.join(ROOT, "contracts", "engine-pair-v1", "fixtures")


def _fix(name: str) -> dict:
    with open(os.path.join(FIX, name)) as fh:
        return json.load(fh)


def test_the_fixtures_are_current():
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "pair_fixtures.py"), "--check"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout.strip().replace("\n", ", ")


def test_the_vectors_replay():
    d = _fix("spake2.json")
    g = d["group"]
    assert (int(g["p"], 16), int(g["q"], 16), g["g"], int(g["M"], 16), int(g["N"], 16), g["aad"].encode()) == \
        (S.P, S.Q, S.G, S.M, S.N, S.AAD)
    n = 0
    for r in d["engine"] + d["node_identities"]:
        w = S.w_of(r["code"])
        x, y = int(r["x"], 16), int(r["y"], 16)
        pa, pb = S.message_a(x, w), S.message_b(y, w)
        k = S.shared_b(pa, y, w)
        assert k == S.shared_a(pb, x, w)
        kca, kcb, tt = S.keys(r["ida"], r["idb"], pa, pb, k, w)
        got = {"w": format(w, "x"), "pA": format(pa, "x"), "pB": format(pb, "x"), "K": format(k, "x"),
               "TT": tt.hex(), "KcA": kca.hex(), "KcB": kcb.hex(), "cA": S.mac(kca, tt), "cB": S.mac(kcb, tt),
               "Ke": S.ke_of(tt).hex()}
        assert got == {k2: r[k2] for k2 in got}, r["code"]
        n += 1
    assert d["engine"][0]["ida"] == S.identity_a(d["keys"]["app_fingerprint"], "tandemapp-1")
    assert d["engine"][0]["idb"] == S.identity_b(d["keys"]["host_fingerprint"])
    assert d["node_identities"][0]["ida"].startswith("tandemapp:")
    return f"{n} runs equal"


def test_the_window_answers_the_fixture():
    ex = _fix("exchange.json")
    host = ex["step1"]["answer"]["host_key"]
    hk = {"line": host, "fingerprint": hostkey.fingerprint(blob_of(host)), "type": "ssh-ed25519"}
    y = int(ex["y"], 16)
    rng = FixedRng(below=[y - 1], hexes=[ex["step1"]["answer"]["session"]])
    clock = [1000.0]
    w = Window(hk, code=ex["code"], clock=lambda: clock[0], rng=rng)
    req = ex["step1"]["request"]
    key_fp = hostkey.fingerprint(blob_of(req["public_key"]))
    out = w.step1(key_fp, req["key_id"], S.elem(req["pA"]), {})
    want = {k: v for k, v in ex["step1"]["answer"].items() if k != "pair"}
    assert out == want, (out, want)
    s = w.step2(ex["step2"]["request"]["session"], ex["step2"]["request"]["cA"])
    assert S.ke_of(s["tt"]).hex() == ex["transcript"]["Ke"]
    import hashlib
    assert hashlib.sha256(s["tt"]).hexdigest() == ex["transcript"]["TT_sha256"]
    sealed = S.seal(bytes.fromhex(ex["transcript"]["Ke"]), ex["plaintext"].encode(), bytes(range(16)))
    assert sealed == ex["step2"]["answer"]["sealed"]
    # TandemApp's side of the same run
    wv = S.w_of(ex["code"])
    x = int(ex["x"], 16)
    pa = S.message_a(x, wv)
    assert format(pa, "x") == req["pA"]
    k = S.shared_a(S.elem(ex["step1"]["answer"]["pB"]), x, wv)
    kca, kcb, tt = S.keys(ex["transcript"]["ida"], ex["transcript"]["idb"], pa, S.elem(ex["step1"]["answer"]["pB"]),
                          k, wv)
    assert S.mac(kcb, tt) == ex["step1"]["answer"]["cB"] and S.mac(kca, tt) == ex["step2"]["request"]["cA"]
    plain = json.loads(S.open_seal(S.ke_of(tt), ex["step2"]["answer"]["sealed"]))
    assert plain == json.loads(ex["plaintext"]) and plain["host_key"] == host
    return "step 1 equal, cA taken, seal equal"


def test_elements_are_checked():
    nonsub = next(v for v in range(3, 50) if pow(v, S.Q, S.P) != 1)
    bad = [format(0, "x"), "1", format(S.P - 1, "x"), format(S.P, "x"), format(nonsub, "x"), "f" * 513, "xyz", 5,
           None, ""]
    for b in bad:
        try:
            S.elem(b)
            raise AssertionError(f"took {b!r:.40}")
        except S.BadElement:
            pass
    assert S.elem(format(S.message_a(5, 7), "x")) == S.message_a(5, 7)
    return f"{len(bad)} refused (a non-member: {nonsub})"


def test_the_seal_refuses_changes():
    ex = _fix("exchange.json")
    ke = bytes.fromhex(ex["transcript"]["Ke"])
    sealed = ex["step2"]["answer"]["sealed"]
    assert S.open_seal(ke, sealed) == ex["plaintext"].encode()

    def flip(h: str) -> str:
        return format(int(h[0], 16) ^ 1, "x") + h[1:]
    for field in ("box", "tag", "nonce"):
        doc = dict(sealed, **{field: flip(sealed[field])})
        try:
            S.open_seal(ke, doc)
            raise AssertionError(field)
        except ValueError:
            pass
    for doc, k in ((sealed, bytes(16)), ({"box": "00"}, ke), ({"nonce": "zz", "box": "", "tag": ""}, ke)):
        try:
            S.open_seal(k, doc)
            raise AssertionError(doc)
        except ValueError:
            pass
    return "box, tag, nonce, key and shape refused"


def test_a_swapped_host_key_fails():
    ex = _fix("exchange.json")
    wv, x = S.w_of(ex["code"]), int(ex["x"], 16)
    pa, pb = S.message_a(x, wv), S.elem(ex["step1"]["answer"]["pB"])
    k = S.shared_a(pb, x, wv)
    other = ex["swapped_host_key"]["host_key"]
    _kca, kcb, tt = S.keys(ex["transcript"]["ida"], S.identity_b(hostkey.fingerprint(blob_of(other))), pa, pb, k, wv)
    assert S.mac(kcb, tt) != ex["step1"]["answer"]["cB"]
    return "cB differs"


if __name__ == "__main__":
    from scriptmode import run
    run(globals())
