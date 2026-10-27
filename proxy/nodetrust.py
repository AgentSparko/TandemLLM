"""Who may talk to a TandemNode agent (SRV-725), before and after pairing (SEC-4).

    mode = listen_mode(host, store)       'secret' | 'mtls', or BindRefused: the bind rule
    secret = load_secret(path, create=True)   the loopback secret, 0600, made at the first start
    ctx = server_context(store)           TLS that asks every client for a certificate
    ctx = client_context(store, pins)     TLS for a call to another node (linktest, the controller)
    fingerprint(der)                      sha256 of a certificate, hex

THE BIND RULE. An address is an IP literal (`localhost` stands for 127.0.0.1): a name could resolve
to anything, so nothing is inferred. Never the unspecified address (0.0.0.0, ::): name the
interface. Never a global (WAN) address: a node far away is reached through an SSH tunnel to its
loopback. A loopback address listens in plain HTTP behind the loopback secret, or in mTLS once the
trust store is filled; any other address (LAN, link-local, the fabric) only in mTLS. So until
pairing exists the agent can listen on loopback only, for a controller on the same box.

THE LOOPBACK SECRET. A file of one line, mode 0600, owned by the user the agent runs as, in a
directory of mode 0700; every request carries it as `Authorization: Bearer <secret>`. A file that
others can read, or that another user owns, is refused (as ssh refuses a key file).

THE TRUST STORE (the directory SEC-4 fills). `node.crt` and `node.key`: this node's own
certificate. `peers/*.pem`: the certificates it trusts, each a pinned self-signed certificate (a
controller's, another node's) or a CA whose certificates it accepts. A client that presents no
certificate, or one no PEM there vouches for, fails the TLS handshake: no route answers it.
"""

from __future__ import annotations

import glob
import hashlib
import ipaddress
import os
import secrets
import ssl
import stat

SECRET_BYTES = 32


class BindRefused(ValueError):
    """An address the agent will not listen on, with the reason."""


class TrustError(ValueError):
    """A secret file or trust store that cannot be used, with the reason."""


def parse_ip(host: str):
    if host == "localhost":
        host = "127.0.0.1"
    try:
        return ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        raise BindRefused(f"{host}: not an IP address (an address is named, never resolved)")


def listen_mode(host: str, store: "Store") -> str:
    """How the agent listens on `host`: 'secret' (loopback, plain HTTP, the loopback secret) or
    'mtls'; BindRefused for an address it never listens on."""
    ip = parse_ip(host)
    if ip.is_unspecified:
        raise BindRefused(f"{host}: the unspecified address listens everywhere; name the "
                          f"interface's address")
    if ip.is_loopback:
        return "mtls" if store.ready() else "secret"
    if ip.is_global:
        raise BindRefused(f"{host} is a WAN address: TandemNode never listens there; reach a "
                          f"remote node through an SSH tunnel to its loopback")
    if not store.ready():
        raise BindRefused(f"{host} is not loopback: listening there needs mTLS, and the trust "
                          f"store {store.root} has {store.missing()} (pairing fills it)")
    return "mtls"


def _check_private(path: str, what: str) -> os.stat_result:
    st = os.stat(path)
    if hasattr(os, "geteuid") and st.st_uid != os.geteuid():
        raise TrustError(f"{path}: {what} belongs to another user")
    if stat.S_IMODE(st.st_mode) & 0o077:
        raise TrustError(f"{path}: {what} has mode {stat.S_IMODE(st.st_mode):04o}; chmod 600 it")
    return st


def load_secret(path: str, create: bool = False) -> str:
    """The loopback secret from `path` (one line). `create`: a missing file is made, mode 0600,
    its directory 0700. TrustError for a file others can read or another user owns."""
    path = os.path.expanduser(path)
    if create and not os.path.exists(path):
        d = os.path.dirname(path)
        os.makedirs(d, mode=0o700, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass                                      # another start made it first
        else:
            with os.fdopen(fd, "w") as f:
                f.write(secrets.token_urlsafe(SECRET_BYTES) + "\n")
    _check_private(path, "the loopback secret")
    with open(path) as f:
        got = f.readline().strip()
    if len(got) < 16:
        raise TrustError(f"{path}: the loopback secret is empty or too short")
    return got


def fingerprint(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()


class Store:
    """The trust store directory: this node's certificate and the certificates it trusts."""

    def __init__(self, root: str):
        self.root = os.path.expanduser(root)
        self.cert = os.path.join(self.root, "node.crt")
        self.key = os.path.join(self.root, "node.key")

    def peers(self) -> list[str]:
        return sorted(glob.glob(os.path.join(self.root, "peers", "*.pem")))

    def missing(self) -> str:
        need = [n for n, p in (("node.crt", self.cert), ("node.key", self.key))
                if not os.path.isfile(p)]
        if not self.peers():
            need.append("no peers/*.pem")
        return ", ".join(need) or "nothing missing"

    def ready(self) -> bool:
        return os.path.isfile(self.cert) and os.path.isfile(self.key) and bool(self.peers())

    def _bundle(self) -> str:
        return "".join(open(p).read().rstrip() + "\n" for p in self.peers())


def server_context(store: Store) -> ssl.SSLContext:
    """TLS 1.2+ with this node's certificate; every client must present one that a PEM in
    peers/ vouches for (a pinned self-signed certificate is its own anchor)."""
    if not store.ready():
        raise TrustError(f"the trust store {store.root} has {store.missing()}")
    _check_private(store.key, "the node key")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(store.cert, store.key)
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    ctx.load_verify_locations(cadata=store._bundle())
    return ctx


def client_context(cert: str | None, key: str | None, trusted_pem: str) -> ssl.SSLContext:
    """TLS for a call to a node: the node's certificate must be one `trusted_pem` vouches for
    (pinned, so no host name is checked); `cert`/`key` the caller's own, when it has one."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    ctx.load_verify_locations(cadata=trusted_pem)
    if cert and key:
        ctx.load_cert_chain(cert, key)
    return ctx


def store_client_context(store: Store) -> ssl.SSLContext:
    """A node calling another node (linktest): its own certificate, the peers it trusts."""
    if not store.ready():
        raise TrustError(f"the trust store {store.root} has {store.missing()}")
    return client_context(store.cert, store.key, store._bundle())
