"""SPAKE2 for pairing by code (engine-pair-v1): the group, the transcript and the confirmations of
TandemApp's proxy/pairing.py, held equal to it by shared vectors
(contracts/engine-pair-v1/fixtures/spake2.json; tests/test_pair_spake2.py), and the seal of the
engine's answer. Standard library only.

THE PROTOCOL. SPAKE2 (Abdalla and Pointcheval 2005; the flow, transcript and key confirmation of
RFC 9382) in the prime-order subgroup of the 2048-bit MODP group of RFC 3526 (group 14: a safe prime
p = 2q + 1, generator 2 of the order-q subgroup). M and N are hashed into the subgroup from fixed
labels (squaring a SHA-512 expansion), so nobody knows their discrete logarithms. The password
scalar w is HKDF-SHA256 of the code, reduced mod q.

    TandemApp (A)                                       engine (B)
    x random, pA = g^x * M^w      ── pA, its key ──▶
                                                         y random, pB = g^y * N^w
                                                         K = (pA / M^w)^y
                                  ◀── pB, cB, host key ─  cB = HMAC(KcB, TT)
    K = (pB / N^w)^x, check cB
    cA = HMAC(KcA, TT)            ── cA ──▶              check cA, install the key
                                  ◀── seal(Ke, answer) ─

TT is RFC 9382's transcript (8-byte little-endian lengths) of the identities A and B, pA, pB, K and
w; Ke||Ka = SHA-256(TT); KcA||KcB = HKDF(Ka, info "ConfirmationKeys" + AAD). The identities bind
what each side sends in the clear: A = "tandemapp-key <fingerprint> <key id>" of TandemApp's
ed25519 key, B = "tandemengine <fingerprint>" of the engine's SSH host key. A key or a host key
swapped on the way changes A or B on one side only, so the confirmations differ and nothing is
installed or trusted.

THE SEAL. The engine's answer (the HTTP token among it) is sealed with Ke: HKDF(Ke, info
"TandemLLM engine pairing seal" + AAD) gives a 32-byte stream key and a 32-byte MAC key; the
stream is HMAC-SHA256(stream key, nonce || 4-byte big-endian counter) blocks, XORed with the
plaintext; the tag is HMAC-SHA256(MAC key, nonce || ciphertext), checked before anything is read.
"""

from __future__ import annotations

import hashlib
import hmac
import struct

AAD = b"TandemLLM pairing v1"
SEAL_INFO = b"TandemLLM engine pairing seal"
NONCE_BYTES = 16

# RFC 3526, 2048-bit MODP group (group 14)
P = int(
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA63B139B22514A08798E3404"
    "DDEF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F4"
    "06B7EDEE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF0598DA48361C55D39A69163FA"
    "8FD24CF5F83655D23DCA3AD961C62F356208552BB9ED529077096966D670C354E4ABC9804F1746C08CA18217C3290"
    "5E462E36CE3BE39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF6955817183995497CEA956AE5"
    "15D2261898FA051015728E5A8AACAA68FFFFFFFFFFFFFFFF", 16)
Q = (P - 1) // 2
G = 2
ELEM_BYTES = 256


class BadElement(ValueError):
    """A received value that is not an element of the order-q subgroup."""


def _hash_to_group(label: bytes) -> int:
    out, i = b"", 0
    while len(out) < ELEM_BYTES + 32:
        out += hashlib.sha512(label + i.to_bytes(4, "big")).digest()
        i += 1
    return pow(int.from_bytes(out, "big") % P, 2, P)


M = _hash_to_group(b"TandemLLM SPAKE2 M")
N = _hash_to_group(b"TandemLLM SPAKE2 N")


def hkdf(ikm: bytes, info: bytes, n: int, salt: bytes = b"") -> bytes:
    prk = hmac.new(salt or b"\0" * 32, ikm, hashlib.sha256).digest()
    out, t, i = b"", b"", 1
    while len(out) < n:
        t = hmac.new(prk, t + info + bytes([i]), hashlib.sha256).digest()
        out += t
        i += 1
    return out[:n]


def w_of(code: str) -> int:
    return int.from_bytes(hkdf(code.encode(), b"TandemLLM pairing w", 64), "big") % Q


def enc(x: int) -> bytes:
    return x.to_bytes(ELEM_BYTES, "big")


def elem(hexstr) -> int:
    """A received group element, checked: in range and in the order-q subgroup."""
    try:
        x = int(hexstr, 16) if isinstance(hexstr, str) and 0 < len(hexstr) <= 2 * ELEM_BYTES else -1
    except ValueError:
        x = -1
    if not 1 < x < P - 1 or pow(x, Q, P) != 1:
        raise BadElement("not a group element")
    return x


def _lp(b: bytes) -> bytes:
    return struct.pack("<Q", len(b)) + b


def transcript(ida: str, idb: str, pa: int, pb: int, k: int, w: int) -> bytes:
    return b"".join(_lp(x) for x in (ida.encode(), idb.encode(), enc(pa), enc(pb), enc(k),
                                     w.to_bytes(ELEM_BYTES, "big")))


def keys(ida: str, idb: str, pa: int, pb: int, k: int, w: int) -> tuple[bytes, bytes, bytes]:
    """(KcA, KcB, TT) of RFC 9382 for this run (proxy/pairing.py `_keys`)."""
    tt = transcript(ida, idb, pa, pb, k, w)
    ka = hashlib.sha256(tt).digest()[16:]
    kc = hkdf(ka, b"ConfirmationKeys" + AAD, 32)
    return kc[:16], kc[16:], tt


def ke_of(tt: bytes) -> bytes:
    """Ke, the shared secret of the run (the first half of SHA-256(TT))."""
    return hashlib.sha256(tt).digest()[:16]


def mac(key: bytes, tt: bytes) -> str:
    return hmac.new(key, tt, hashlib.sha256).hexdigest()


def message_a(x: int, w: int) -> int:
    return pow(G, x, P) * pow(M, w, P) % P


def message_b(y: int, w: int) -> int:
    return pow(G, y, P) * pow(N, w, P) % P


def shared_b(pa: int, y: int, w: int) -> int:
    """K on the engine's side: (pA / M^w)^y."""
    return pow(pa * pow(M, Q - w, P) % P, y, P)


def shared_a(pb: int, x: int, w: int) -> int:
    """K on TandemApp's side: (pB / N^w)^x."""
    return pow(pb * pow(N, Q - w, P) % P, x, P)


# ---------------------------------------------------------------------------------------- identities
# the fingerprints are OpenSSH's SHA256 form (server/pairing/hostkey.py `fingerprint`)
def identity_a(key_fp: str, key_id: str) -> str:
    return f"tandemapp-key {key_fp} {key_id}"


def identity_b(host_fp: str) -> str:
    return f"tandemengine {host_fp}"


# ---------------------------------------------------------------------------------------- the seal
def _seal_keys(ke: bytes) -> tuple[bytes, bytes]:
    k = hkdf(ke, SEAL_INFO + AAD, 64)
    return k[:32], k[32:]


def _stream(key: bytes, nonce: bytes, n: int) -> bytes:
    out, i = b"", 0
    while len(out) < n:
        out += hmac.new(key, nonce + i.to_bytes(4, "big"), hashlib.sha256).digest()
        i += 1
    return out[:n]


def seal(ke: bytes, plaintext: bytes, nonce: bytes) -> dict:
    """{nonce, box, tag}, hex: `plaintext` encrypted and authenticated under Ke."""
    if len(nonce) != NONCE_BYTES:
        raise ValueError(f"the nonce is {NONCE_BYTES} bytes")
    ek, mk = _seal_keys(ke)
    box = bytes(a ^ b for a, b in zip(plaintext, _stream(ek, nonce, len(plaintext))))
    tag = hmac.new(mk, nonce + box, hashlib.sha256).hexdigest()
    return {"nonce": nonce.hex(), "box": box.hex(), "tag": tag}


def open_seal(ke: bytes, sealed: dict) -> bytes:
    """The plaintext of `sealed`; ValueError when its tag does not check."""
    try:
        nonce, box = bytes.fromhex(sealed["nonce"]), bytes.fromhex(sealed["box"])
        tag = str(sealed["tag"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("not a sealed answer") from None
    ek, mk = _seal_keys(ke)
    if len(nonce) != NONCE_BYTES or not hmac.compare_digest(
            tag, hmac.new(mk, nonce + box, hashlib.sha256).hexdigest()):
        raise ValueError("the seal does not check")
    return bytes(a ^ b for a, b in zip(box, _stream(ek, nonce, len(box))))
