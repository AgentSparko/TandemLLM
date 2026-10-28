"""Who may talk to a TandemNode agent (SRV-725), before and after pairing (SEC-4).

    mode = listen_mode(host, store)       'secret' | 'mtls', or BindRefused: the bind rule
    secret = load_secret(path, create=True)   the loopback secret, 0600, made at the first start
    ctx = server_context(store)           TLS that asks every client for a certificate
    ctx = client_context(cert, key, pem)  TLS for a call to another node (linktest, the controller)
    fingerprint(der)                      sha256 of a certificate, hex
    make_identity(cert, key, cn)          a key pair and its self-signed certificate (SEC-4)

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
certificate, made at the agent's first start (EC P-256, self-signed). `peers/*.pem`: the
certificates it trusts, each a pinned self-signed certificate or a CA whose certificates it
accepts: `peers/owner.pem` is the one controller that owns the node (`owner.json` beside it says
who and since when), `peers/node-<id>.pem` another node pinned for link tests. A client that
presents no certificate, or one no PEM there vouches for, fails the TLS handshake: no route answers
it. The node's own certificate is trusted too, so its own CLI (`tandem node pair`, `takeover`)
reaches it on loopback with the key only its user can read, as the secret does before pairing.
`listen.json`: the addresses pairing made the agent listen on beyond `--host`. `audit.log`: one
JSON line per pairing, release and take-over.
"""

from __future__ import annotations

import datetime
import glob
import hashlib
import ipaddress
import json
import os
import secrets
import ssl
import stat
import time

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


def pem_fingerprint(pem: str) -> str:
    """The fingerprint of the first certificate in `pem`; TrustError when there is none."""
    der = pem_der(pem)
    return fingerprint(der)


def pem_der(pem: str) -> bytes:
    try:
        return ssl.PEM_cert_to_DER_cert(pem.strip() + "\n")
    except (ValueError, TypeError) as exc:
        raise TrustError(f"not a PEM certificate ({exc})")


def write_private(path: str, data: bytes | str, mode: int = 0o600) -> None:
    """`path` replaced whole, mode `mode`, its directory 0700: never a half-written file."""
    d = os.path.dirname(path)
    os.makedirs(d, mode=0o700, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "wb") as f:
        f.write(data.encode() if isinstance(data, str) else data)
    os.replace(tmp, path)


def make_identity(cert: str, key: str, cn: str, years: int = 20) -> str:
    """An EC P-256 key (0600) and its self-signed certificate at `cert`/`key`; the certificate's
    PEM. A pinned certificate is its own anchor, so it carries no CA flag and lasts long."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    k = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn[:64])])
    now = datetime.datetime.now(datetime.timezone.utc)
    c = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
         .public_key(k.public_key()).serial_number(x509.random_serial_number())
         .not_valid_before(now - datetime.timedelta(days=1))
         .not_valid_after(now + datetime.timedelta(days=365 * years))
         .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
         .sign(k, hashes.SHA256()))
    write_private(key, k.private_bytes(serialization.Encoding.PEM,
                                       serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    pem = c.public_bytes(serialization.Encoding.PEM).decode()
    write_private(cert, pem, 0o644)
    return pem


class Store:
    """The trust store directory: this node's certificate and the certificates it trusts."""

    OWNER = "owner"

    def __init__(self, root: str):
        self.root = os.path.expanduser(root)
        self.cert = os.path.join(self.root, "node.crt")
        self.key = os.path.join(self.root, "node.key")
        self.peer_dir = os.path.join(self.root, "peers")
        self.owner_file = os.path.join(self.root, "owner.json")
        self.listen_file = os.path.join(self.root, "listen.json")
        self.audit_file = os.path.join(self.root, "audit.log")

    def peers(self) -> list[str]:
        return sorted(glob.glob(os.path.join(self.peer_dir, "*.pem")))

    # ------------------------------------------------------------------ this node
    def ensure_identity(self, name: str) -> bool:
        """This node's certificate, made once (True when made now)."""
        if os.path.isfile(self.cert) and os.path.isfile(self.key):
            return False
        make_identity(self.cert, self.key, f"TandemNode {name}")
        return True

    def own_pem(self) -> str:
        with open(self.cert) as f:
            return f.read()

    def own_fingerprint(self) -> str | None:
        try:
            return pem_fingerprint(self.own_pem())
        except (OSError, TrustError):
            return None

    # ------------------------------------------------------------------ the owner
    def owner(self) -> dict | None:
        """{name, fingerprint, via, paired_at, address?} of the controller that owns this node."""
        if not os.path.isfile(self.peer_path(self.OWNER)):
            return None
        try:
            with open(self.owner_file) as f:
                doc = json.load(f)
        except (OSError, ValueError):
            doc = {}
        try:
            fp = pem_fingerprint(open(self.peer_path(self.OWNER)).read())
        except (OSError, TrustError):
            return None
        return dict(doc if isinstance(doc, dict) else {}, fingerprint=fp)

    def peer_path(self, name: str) -> str:
        return os.path.join(self.peer_dir, f"{name}.pem")

    def pin(self, name: str, pem: str) -> str:
        """`pem` pinned as peers/<name>.pem; its fingerprint."""
        fp = pem_fingerprint(pem)
        write_private(self.peer_path(name), pem.strip() + "\n", 0o644)
        return fp

    def unpin(self, name: str) -> bool:
        try:
            os.remove(self.peer_path(name))
            return True
        except FileNotFoundError:
            return False

    def set_owner(self, pem: str, info: dict) -> str:
        fp = self.pin(self.OWNER, pem)
        write_private(self.owner_file, json.dumps(dict(info, fingerprint=fp)))
        return fp

    def clear_owner(self) -> dict | None:
        """The owner forgotten: its pin, its record and the addresses pairing listened on."""
        was = self.owner()
        self.unpin(self.OWNER)
        for p in (self.owner_file, self.listen_file):
            try:
                os.remove(p)
            except FileNotFoundError:
                pass
        return was

    def role(self, der: bytes | None) -> str | None:
        """Who a client certificate is: 'self', 'owner', 'peer' (another pinned PEM) or None."""
        if not der:
            return None
        fp = fingerprint(der)
        if fp == self.own_fingerprint():
            return "self"
        owner = self.owner()
        if owner is not None and owner["fingerprint"] == fp:
            return "owner"
        for p in self.peers():
            try:
                if pem_fingerprint(open(p).read()) == fp:
                    return "peer"
            except (OSError, TrustError):
                continue
        return None

    # ------------------------------------------------------------------ addresses and audit
    def listen_addrs(self) -> list[str]:
        try:
            with open(self.listen_file) as f:
                got = json.load(f)
        except (OSError, ValueError):
            return []
        return [a for a in got if isinstance(a, str)] if isinstance(got, list) else []

    def set_listen(self, addrs: list[str]) -> None:
        write_private(self.listen_file, json.dumps(sorted(set(addrs))))

    def audit(self, action: str, **detail) -> dict:
        """One line in audit.log: what happened to the node's trust, never a secret."""
        row = dict(t=round(time.time(), 3), action=action, **detail)
        os.makedirs(self.root, mode=0o700, exist_ok=True)
        fd = os.open(self.audit_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")
        return row

    def audit_rows(self, limit: int = 50) -> list[dict]:
        try:
            with open(self.audit_file) as f:
                lines = f.readlines()[-limit:]
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

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
    peers/ vouches for (a pinned self-signed certificate is its own anchor), or this node's own."""
    if not store.ready():
        raise TrustError(f"the trust store {store.root} has {store.missing()}")
    _check_private(store.key, "the node key")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(store.cert, store.key)
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    ctx.load_verify_locations(cadata=store._bundle() + store.own_pem())
    return ctx


def pairing_context(store: Store) -> ssl.SSLContext:
    """The pairing window's TLS: this node's certificate and no client certificate asked; the
    pairing protocol (proxy/pairing.py) binds both certificates to the code instead."""
    _check_private(store.key, "the node key")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(store.cert, store.key)
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
