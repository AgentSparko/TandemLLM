"""The shared vectors of engine-pair-v1 (contracts/engine-pair-v1/fixtures), made from fixed inputs.

    python tools/pair_fixtures.py            print what the files should hold (JSON per file)
    python tools/pair_fixtures.py --write    write them
    python tools/pair_fixtures.py --check    exit 1 when a committed file differs

spake2.json   the group's constants and runs of the arithmetic: code -> w, x and y -> pA, pB, K, the
              transcript, KcA, KcB, cA, cB and Ke, for the engine's identities and for the node
              identities of TandemApp's proxy/pairing.py (which replays the second set as it is)
exchange.json one whole exchange on the wire: the two requests and answers of /v1/engine/pair/1 and
              /2, the sealed answer and its plaintext, from a fixed code, x, y, session, nonce and token
mdns.json     a browse query for _tandem._tcp and the engine's multicast answer, as bytes (hex)

The values are computed with server/pairing/spake2.py and server/pairing/mdns.py; tests/test_pair_spake2.py
holds the engine's window to them, and TandemApp's tests replay the same files.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server.pairing import hostkey, mdns, spake2 as S  # noqa: E402
from server.remote import keys  # noqa: E402

OUT = os.path.join(ROOT, "contracts", "engine-pair-v1", "fixtures")


def scalar(label: str) -> int:
    """A fixed scalar in [1, q - 1] from a label."""
    return 1 + int.from_bytes(hashlib.sha512(label.encode()).digest() * 4, "big") % (S.Q - 1)


def ed25519_line(label: str) -> tuple[str, bytes]:
    """A well-formed ssh-ed25519 public key line (32 bytes from a label; not checked as a point)."""
    blob = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + hashlib.sha256(label.encode()).digest()
    return "ssh-ed25519 " + base64.b64encode(blob).decode(), blob


def run(code: str, x: int, y: int, ida: str, idb: str) -> dict:
    w = S.w_of(code)
    pa, pb = S.message_a(x, w), S.message_b(y, w)
    k = S.shared_b(pa, y, w)
    assert k == S.shared_a(pb, x, w)
    kca, kcb, tt = S.keys(ida, idb, pa, pb, k, w)
    return {"code": code, "x": format(x, "x"), "y": format(y, "x"), "ida": ida, "idb": idb, "w": format(w, "x"),
            "pA": format(pa, "x"), "pB": format(pb, "x"), "K": format(k, "x"), "TT": tt.hex(),
            "KcA": kca.hex(), "KcB": kcb.hex(), "cA": S.mac(kca, tt), "cB": S.mac(kcb, tt), "Ke": S.ke_of(tt).hex()}


def spake2_doc() -> dict:
    app_line, app_blob = ed25519_line("fixture TandemApp key")
    host_line, host_blob = ed25519_line("fixture host key")
    a = S.identity_a(hostkey.fingerprint(app_blob), "tandemapp-1")
    b = S.identity_b(hostkey.fingerprint(host_blob))
    runs = [run("246810", scalar("x1"), scalar("y1"), a, b),
            run("000000", scalar("x2"), scalar("y2"), a, b),
            run("999999", scalar("x3"), scalar("y3"), S.identity_a(hostkey.fingerprint(app_blob), "ctl:b.2"), b)]
    node = [run("123456", scalar("nx1"), scalar("ny1"), "tandemapp:" + "ab" * 32, "tandemnode:" + "cd" * 32)]
    return {"pair": "engine-pair-v1", "about": "SPAKE2 runs: every value hex, big-endian, no leading zeros "
            "(as format(v, 'x')); TT and the keys as bytes in hex",
            "group": {"p": format(S.P, "x"), "q": format(S.Q, "x"), "g": S.G, "M": format(S.M, "x"),
                      "N": format(S.N, "x"), "elem_bytes": S.ELEM_BYTES, "aad": S.AAD.decode()},
            "keys": {"app": app_line, "app_fingerprint": hostkey.fingerprint(app_blob), "host": host_line,
                     "host_fingerprint": hostkey.fingerprint(host_blob)},
            "engine": runs, "node_identities": node}


def exchange_doc() -> dict:
    app_line, app_blob = ed25519_line("fixture TandemApp key")
    host_line, host_blob = ed25519_line("fixture host key")
    key_id, code = "tandemapp-1", "246810"
    r = run(code, scalar("x1"), scalar("y1"), S.identity_a(hostkey.fingerprint(app_blob), key_id),
            S.identity_b(hostkey.fingerprint(host_blob)))
    session, nonce, token = "5e5510" + "0" * 26, bytes(range(16)), "fixture-token-" + "A" * 29
    control = "/home/u/TandemLLM/bin/tandem"
    line = keys.key_line(control, key_id, "ssh-ed25519", app_line.split()[1])
    plain = {"pair": "engine-pair-v1", "key_id": key_id, "ssh_user": "u", "ssh_port": 22, "host_key": host_line,
             "host_keys": [host_line], "http_port": 8000, "http_token": token, "engine_version": "0.15.4",
             "mode": "setup", "authorized_keys": {"added": True, "line": line}}
    pt = json.dumps(plain, sort_keys=True, separators=(",", ":")).encode()
    sealed = S.seal(bytes.fromhex(r["Ke"]), pt, nonce)
    return {"pair": "engine-pair-v1",
            "about": "one exchange: the code is shown on the engine host; x is TandemApp's secret, y the engine's",
            "code": code, "x": r["x"], "y": r["y"],
            "step1": {"request": {"pA": r["pA"], "public_key": app_line, "key_id": key_id, "name": "TandemApp"},
                      "answer": {"pair": "engine-pair-v1", "session": session, "pB": r["pB"], "cB": r["cB"],
                                 "host_key": host_line, "fingerprint": hostkey.fingerprint(host_blob), "tries_left": 4,
                                 "expires_in": 300}},
            "transcript": {"ida": r["ida"], "idb": r["idb"], "TT_sha256": hashlib.sha256(bytes.fromhex(r["TT"])).hexdigest(),
                           "Ke": r["Ke"]},
            "step2": {"request": {"session": session, "cA": r["cA"]},
                      "answer": {"pair": "engine-pair-v1", "sealed": sealed}},
            "plaintext": pt.decode(),
            "swapped_host_key": {"host_key": ed25519_line("fixture other host key")[0],
                                 "note": "the same step 1 answer with this host_key: TandemApp's check of cB fails"}}


def mdns_doc() -> dict:
    adv = mdns.Advert("enginehost", 8000, "0.15.4", "SHA256:" + "A" * 43, ["192.0.2.10"])
    q = struct.pack(">6H", 0, 0, 1, 0, 0, 0) + mdns.encode_name(mdns.SERVICE) + struct.pack(">HH", mdns.PTR, 1)
    out = mdns.Responder(adv).answer(q, ("192.0.2.20", mdns.PORT))
    (pkt, dest), = out
    return {"pair": "engine-pair-v1", "about": "a browse for _tandem._tcp from port 5353 and the multicast answer",
            "advert": {"name": "enginehost", "port": 8000, "version": "0.15.4", "fp": "SHA256:" + "A" * 43,
                       "addresses": ["192.0.2.10"]},
            "query": q.hex(), "answer": pkt.hex(), "to": list(dest),
            "txt": [f"{k}={v}" for k, v in adv.txt]}


def docs() -> dict:
    return {"spake2.json": spake2_doc(), "exchange.json": exchange_doc(), "mdns.json": mdns_doc()}


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    bad = 0
    for name, doc in docs().items():
        text = json.dumps(doc, indent=1, sort_keys=True) + "\n"
        path = os.path.join(OUT, name)
        if mode == "--write":
            os.makedirs(OUT, exist_ok=True)
            with open(path, "w") as fh:
                fh.write(text)
        elif mode == "--check":
            try:
                same = open(path).read() == text
            except OSError:
                same = False
            print(f"{name}: {'same' if same else 'DIFFERS'}")
            bad += not same
        else:
            print(name, text)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
