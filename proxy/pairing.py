"""Pairing by code (SEC-4): a TandemNode and a TandemApp controller pin each other's certificate
after the user types the 6-digit code the node shows.

    w = Window(code_len=6, ttl_s=300, tries=5)        the node's side: one code, its limits
    sid, msg = w.step1(body, own_pem)                  -> {session, pB, cB}
    ctl_pem = w.step2(body)                            the controller's certificate, or PairError
    node_pem, info = pair(address, port, code, ...)    the controller's side, over TLS

THE PROTOCOL. SPAKE2 (Abdalla and Pointcheval 2005; the message flow, transcript and key
confirmation of RFC 9382), instantiated in the prime-order subgroup of the 2048-bit MODP group of
RFC 3526 (group 14, a safe prime p = 2q + 1, generator 2 of the order-q subgroup). The
`cryptography` package exposes no elliptic-curve point arithmetic, so the group is the
finite-field one; M and N are hashed into the subgroup from fixed labels (squaring a SHA-512
expansion), so nobody knows their discrete logarithms. The password scalar w is HKDF-SHA256 of
the code, reduced mod q.

    controller (A)                                        node (B)
    x random, pA = g^x * M^w           ── pA, A's certificate ──▶
                                                           y random, pB = g^y * N^w
                                                           K = (pA / M^w)^y
                                       ◀── pB, cB ──       cB = HMAC(KcB, TT)
    K = (pB / N^w)^x, check cB
    cA = HMAC(KcA, TT)                 ── cA ──▶           check cA, pin A's certificate

TT is RFC 9382's transcript (8-byte little-endian lengths) of the identities A and B, pA, pB, K
and w. A is the SHA-256 fingerprint of the controller's certificate as it sent it, B the
fingerprint of the node's certificate as each side sees it: the node its own, the controller the
one its TLS connection presented. Ke||Ka = SHA-256(TT); KcA||KcB = HKDF(Ka, info
"ConfirmationKeys" + AAD).

WHAT IT GIVES. The pairing runs inside TLS to the node's self-signed certificate, which nobody
vouches for yet; SPAKE2 is what binds it. Someone between the two who swaps either certificate
changes A or B on one side only, so the confirmations differ and nothing is pinned; relaying the
messages unchanged gets the attacker certificates it holds no key for. Without the code an
attacker gets one guess per run (SPAKE2's messages give nothing to test codes against offline),
and the node allows 5 runs per code, for 5 minutes, then the code is spent: 5 in 10^6. The code is
single-use: the first success closes the window.
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import secrets
import socket
import ssl
import struct
import threading
import time

from proxy import nodetrust

CODE_LEN = 6
TTL_S = 300.0
TRIES = 5
SESSION_S = 60.0
AAD = b"TandemLLM pairing v1"

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


class PairError(Exception):
    """A pairing that did not happen: `kind` is the contract's error type, `status` its HTTP
    status."""

    def __init__(self, kind: str, message: str, status: int = 400):
        super().__init__(message)
        self.kind, self.message, self.status = kind, message, status

    def body(self) -> dict:
        return {"error": {"type": self.kind, "message": self.message}}


def _hash_to_group(label: bytes) -> int:
    out, i = b"", 0
    while len(out) < ELEM_BYTES + 32:
        out += hashlib.sha512(label + i.to_bytes(4, "big")).digest()
        i += 1
    return pow(int.from_bytes(out, "big") % P, 2, P)


M = _hash_to_group(b"TandemLLM SPAKE2 M")
N = _hash_to_group(b"TandemLLM SPAKE2 N")


def _hkdf(ikm: bytes, info: bytes, n: int, salt: bytes = b"") -> bytes:
    prk = hmac.new(salt or b"\0" * 32, ikm, hashlib.sha256).digest()
    out, t, i = b"", b"", 1
    while len(out) < n:
        t = hmac.new(prk, t + info + bytes([i]), hashlib.sha256).digest()
        out += t
        i += 1
    return out[:n]


def _w(code: str) -> int:
    return int.from_bytes(_hkdf(code.encode(), b"TandemLLM pairing w", 64), "big") % Q


def _enc(x: int) -> bytes:
    return x.to_bytes(ELEM_BYTES, "big")


def _elem(hexstr) -> int:
    """A received group element, checked: in range and in the order-q subgroup."""
    try:
        x = int(hexstr, 16) if isinstance(hexstr, str) and len(hexstr) <= 2 * ELEM_BYTES else -1
    except ValueError:
        x = -1
    if not 1 < x < P - 1 or pow(x, Q, P) != 1:
        raise PairError("bad_request", "not a group element")
    return x


def _lp(b: bytes) -> bytes:
    return struct.pack("<Q", len(b)) + b


def _keys(ida: str, idb: str, pa: int, pb: int, k: int, w: int) -> tuple[bytes, bytes, bytes]:
    """(KcA, KcB, TT) of RFC 9382 for this run."""
    tt = b"".join(_lp(x) for x in (ida.encode(), idb.encode(), _enc(pa), _enc(pb), _enc(k),
                                   w.to_bytes(ELEM_BYTES, "big")))
    ka = hashlib.sha256(tt).digest()[16:]
    kc = _hkdf(ka, b"ConfirmationKeys" + AAD, 32)
    return kc[:16], kc[16:], tt


def _mac(key: bytes, tt: bytes) -> str:
    return hmac.new(key, tt, hashlib.sha256).hexdigest()


def new_code(n: int = CODE_LEN) -> str:
    return f"{secrets.randbelow(10 ** n):0{n}d}"


def identity_a(ctl_fp: str) -> str:
    return "tandemapp:" + ctl_fp


def identity_b(node_fp: str) -> str:
    return "tandemnode:" + node_fp


# ====================================================================== the node's side

class Window:
    """One pairing code on a node: `ttl_s` to live, `tries` runs, single use. `state` is open,
    paired, expired, spent (the tries are used up) or closed."""

    def __init__(self, own_pem: str, *, code: str | None = None, ttl_s: float = TTL_S,
                 tries: int = TRIES, takeover: bool = False, address: str = "",
                 clock=time.time):
        self.code = code or new_code()
        self.own_fp = nodetrust.pem_fingerprint(own_pem)
        self.clock = clock
        self.opened = clock()
        self.expires_at = self.opened + ttl_s
        self.tries_left = tries
        self.takeover = takeover
        self.address = address
        self.state = "open"
        self.failed = 0
        self._sessions: dict[str, tuple] = {}
        self._lock = threading.Lock()

    def view(self) -> dict:
        """What the node's own CLI sees (the code included: it shows it)."""
        self._expire()
        return {"state": self.state, "code": self.code, "expires_at": round(self.expires_at, 3),
                "tries_left": self.tries_left, "takeover": self.takeover,
                "address": self.address, "fingerprint": self.own_fp}

    def _expire(self) -> None:
        if self.state == "open" and self.clock() >= self.expires_at:
            self.state = "expired"

    def _open(self) -> None:
        self._expire()
        if self.state == "expired":
            raise PairError("code_spent", "the code has expired: show a new one on the node "
                                          "(tandem node pair)", 410)
        if self.state == "spent":
            raise PairError("code_spent", f"{TRIES} wrong codes: this code is spent; show a new "
                                          "one on the node (tandem node pair)", 410)
        if self.state != "open":
            raise PairError("code_spent", "this code was used; show a new one on the node "
                                          "(tandem node pair)", 410)

    def step1(self, body) -> dict:
        """The controller's pA and certificate: one try spent; pB and the node's confirmation."""
        if not isinstance(body, dict) or not isinstance(body.get("certificate"), str):
            raise PairError("bad_request", "the body is {pA, certificate, name?}")
        ctl_fp = nodetrust.pem_fingerprint(body["certificate"])
        pa = _elem(body.get("pA"))
        with self._lock:
            self._open()
            if self.tries_left <= 0:
                self.state = "spent"
                self._open()
            self.tries_left -= 1
            now = self.clock()
            self._sessions = {k: v for k, v in self._sessions.items() if v[0] > now}
            w = _w(self.code)
            y = 1 + secrets.randbelow(Q - 1)
            pb = pow(G, y, P) * pow(N, w, P) % P
            k = pow(pa * pow(M, Q - w, P) % P, y, P)
            kca, kcb, tt = _keys(identity_a(ctl_fp), identity_b(self.own_fp), pa, pb, k, w)
            sid = secrets.token_hex(16)
            self._sessions[sid] = (now + SESSION_S, kca, tt, body["certificate"],
                                   str(body.get("name") or "")[:80])
        return {"session": sid, "pB": format(pb, "x"), "cB": _mac(kcb, tt)}

    def step2(self, body) -> tuple[str, str]:
        """The controller's confirmation: (its certificate, its name) once it matches; the window
        is then paired. A wrong one is a failed try."""
        if not isinstance(body, dict):
            raise PairError("bad_request", "the body is {session, cA}")
        with self._lock:
            got = self._sessions.pop(str(body.get("session")), None)
            if got is None or got[0] <= self.clock():
                self._open()
                raise PairError("bad_request", "no such pairing session (one try, 60 s)")
            _, kca, tt, cert, name = got
            ca = body.get("cA")
            if not isinstance(ca, str) or not hmac.compare_digest(ca, _mac(kca, tt)):
                self.failed += 1
                if self.tries_left <= 0:
                    self.state = "spent"
                self._expire()
                raise PairError("wrong_code", "the code does not match"
                                + (f": {self.tries_left} tries left" if self.state == "open"
                                   else f"; {TRIES} wrong codes spent it"), 403)
            self._open()
            self.state = "paired"
            self._sessions.clear()
        return cert, name

    def abandon(self, body) -> None:
        """A run the controller gave up (its check of cB failed): a failed try, counted at step 1
        already."""
        with self._lock:
            if self._sessions.pop(str((body or {}).get("session")), None) is not None:
                self.failed += 1
                if self.tries_left <= 0 and self.state == "open":
                    self.state = "spent"

    def close(self) -> None:
        with self._lock:
            if self.state == "open":
                self.state = "closed"


# ====================================================================== the controller's side

def _tls(address: str, port: int, timeout: float) -> tuple[http.client.HTTPSConnection, bytes]:
    """A TLS connection to the pairing window and the certificate it presented (not trusted:
    the protocol checks it)."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    conn = http.client.HTTPSConnection(address, port, context=ctx, timeout=timeout)
    conn.connect()
    der = conn.sock.getpeercert(binary_form=True)
    if not der:
        conn.close()
        raise PairError("unreachable", f"{address}:{port} presented no certificate", 502)
    return conn, der


def _post(conn, path: str, doc: dict) -> tuple[int, dict]:
    conn.request("POST", path, body=json.dumps(doc).encode(),
                 headers={"Content-Type": "application/json", "Accept": "application/json"})
    r = conn.getresponse()
    raw = r.read()
    try:
        got = json.loads(raw) if raw else {}
    except ValueError:
        got = {}
    return r.status, got if isinstance(got, dict) else {}


def _refusal(st: int, doc: dict) -> PairError:
    e = doc.get("error") if isinstance(doc.get("error"), dict) else {}
    kind = e.get("type") if e.get("type") in ("wrong_code", "code_spent", "bad_request",
                                                "owned") else "unreachable"
    return PairError(kind, str(e.get("message") or f"the node answered HTTP {st}"),
                     st if kind != "unreachable" else 502)


def pair(address: str, port: int, code: str, own_pem: str, *, name: str = "",
         timeout: float = 10.0) -> tuple[str, dict]:
    """Pair with the node's window at address:port: (the node's certificate PEM, the node's
    answer {node_id, port, took_over_from?}). PairError otherwise: a wrong code, a spent one, a
    certificate swapped on the way (the same error as a wrong code: the two look alike)."""
    if not (isinstance(code, str) and len(code) == CODE_LEN and code.isdigit()):
        raise PairError("bad_request", f"the code is {CODE_LEN} digits")
    ctl_fp = nodetrust.pem_fingerprint(own_pem)
    try:
        conn, der = _tls(address, port, timeout)
    except (OSError, ssl.SSLError) as exc:
        raise PairError("unreachable", f"no pairing window answers at {address}:{port} "
                                       f"({type(exc).__name__}); run `tandem node pair` on the "
                                       "node", 502)
    node_fp = nodetrust.fingerprint(der)
    node_pem = ssl.DER_cert_to_PEM_cert(der)
    try:
        w = _w(code)
        x = 1 + secrets.randbelow(Q - 1)
        pa = pow(G, x, P) * pow(M, w, P) % P
        st, doc = _post(conn, "/node/pair/1", {"pA": format(pa, "x"), "certificate": own_pem,
                                               "name": name})
        if st != 200:
            raise _refusal(st, doc)
        pb = _elem(doc.get("pB"))
        k = pow(pb * pow(N, Q - w, P) % P, x, P)
        kca, kcb, tt = _keys(identity_a(ctl_fp), identity_b(node_fp), pa, pb, k, w)
        if not hmac.compare_digest(str(doc.get("cB") or ""), _mac(kcb, tt)):
            _post(conn, "/node/pair/abandon", {"session": doc.get("session")})
            raise PairError("wrong_code", "the code does not match (or the connection to the "
                                          "node is intercepted)", 403)
        st, done = _post(conn, "/node/pair/2", {"session": doc.get("session"),
                                                "cA": _mac(kca, tt)})
        if st != 200:
            raise _refusal(st, done)
    except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
        raise PairError("unreachable", f"the pairing window at {address}:{port} stopped "
                                       f"answering ({type(exc).__name__})", 502)
    finally:
        conn.close()
    return node_pem, done


def owned_elsewhere(address: str, port: int, cert: str, key: str,
                    timeout: float = 5.0) -> str | None:
    """Why a node's mTLS port refuses this controller, or None: 'owned' (it answers with a
    certificate but refuses ours), 'ours' (it takes ours), None (nothing answers TLS there)."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.load_cert_chain(cert, key)
    try:
        conn = http.client.HTTPSConnection(address, port, context=ctx, timeout=timeout)
        conn.request("GET", "/node/identity")
        conn.getresponse().read()
        conn.close()
        return "ours"
    except ssl.SSLError as exc:
        if exc.reason in ("WRONG_VERSION_NUMBER", "RECORD_LAYER_FAILURE", "HTTP_REQUEST",
                          "PACKET_LENGTH_TOO_LONG"):
            return None                  # plain HTTP: a node's loopback before pairing
        return "owned"
    except (OSError, http.client.HTTPException) as exc:
        if isinstance(exc, (ConnectionResetError, BrokenPipeError, http.client.RemoteDisconnected)):
            return "owned"
        return None


def wait_port(address: str, port: int, timeout: float) -> bool:
    """True once address:port accepts a connection."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            socket.create_connection((address, port), timeout=1.0).close()
            return True
        except OSError:
            time.sleep(0.1)
    return False
