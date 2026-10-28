"""The controller's side of node trust (SEC-4): its own certificate, pairing by code and by SSH
once, release, and the audit of each.

    t = Trust(fleet, dir)                   dir: `controller/` beside the config database
    t.view()                                {controller: {id, name, fingerprint, since}, audit}
    t.pair_code({address, code, port?}, who)              -> the node's row
    t.host_key({host, port?})                             -> {type, fingerprint, known, stored}
    t.pair_ssh({host, user, port?, password?, key?, key_file?, jumps?, tunnel?, host_key?,
                takeover?}, who)                          -> the node's row
    t.release(node id, force, who)                        -> {id, node}

THE CONTROLLER'S CERTIFICATE. `controller.crt` and `controller.key` (EC P-256, self-signed, 0600
in a 0700 directory) made at the first start, beside the config database as the master key file
is. Every node this controller pairs pins it; the node records carry its paths and the node's
pinned certificate (`trust`: kind mtls, cert, key, pin), and nodesource.agent_for builds every
call from them. A new certificate (the files removed) means pairing every node again.

PAIRING BY CODE (proxy/pairing.py). The node's window at address:port (the agent's port + 1); the
answer names the node's id and its mTLS port. A node whose window does not answer but whose mTLS
port refuses this certificate is owned by another TandemApp: 409 `owned`.

SSH ONCE (proxy/sshonce.py). The controller's certificate goes to `tandem-node adopt` on the node
(found from its user unit, PATH, or an install.sh install); the node's certificate comes back on
the same authenticated channel and is pinned. The probe refuses before the adopt when the box's
machine id is this controller's own (/etc/machine-id): the controller's machine is read through its
loopback agent, and an SSH login to it must not pin anything in that agent's trust store. A node another TandemApp owns is taken over only
with `takeover: true` (SSH access proves the node is the user's). The same login first prints the
node's DMI (vendor, product name, version, family, all world-readable): discovery
(proxy/discovery.py) learns from it what the box is, a GB10 with nothing installed included. The
route is the one `/discovery/target` read from an ssh line (5.2): `jumps` (the jump hosts in
order) and `key_file` (a private key file on this machine, the line's `-i` or ~/.ssh/config's
IdentityFile; ssh reads it, nothing copies it). `tunnel: true` is a rented or remote node: its
agent listens on its own loopback only (in mTLS once pinned) and the controller reaches it through
an SSH tunnel it keeps open (proxy/sshtunnel.py), never on a WAN address; the record's `transport`
is `tunnel` and its trust carries the tunnel's route, which needs a key or the user's agent.

RELEASE. `DELETE /v1/fleet/nodes/{id}` asks the node to forget this controller (its pin, its
listen addresses), then forgets the node here. A node that does not answer is forgotten only with
`?force=1`; `tandem node reset` on the node does its half.

THE AUDIT. Each pairing, take-over, release and forced forget is a row of the config database's
audit table (object `node`, the details in `detail`): never a password, a key or a code.
"""

from __future__ import annotations

import collections
import hashlib
import json
import os
import shlex
import socket
import ssl
import threading
import time
import urllib.parse

from proxy import nodeclient, nodesource, nodetrust, pairing, sshonce

PAIR_PORT_OFFSET = 1
TRIES_PER_MIN = 10
IDENTITY_WAIT_S = 8.0
ACTIONS = ("pair", "takeover", "release", "forget")
REMOTE = r"""set -u
dmi=${TANDEM_DMI_DIR:-/sys/devices/virtual/dmi/id}
r() { head -n 1 "$dmi/$1" 2>/dev/null | tr -d '|\r'; }
echo "TANDEMNODE-DMI $(r sys_vendor)|$(r product_name)|$(r product_version)|$(r product_family)" >&2
unit="$HOME/.config/systemd/user/tandemnode.service"
addr=$(printf '%s' "${SSH_CONNECTION:-}" | awk '{print $3}')
node=""
args=""
if [ -f "$unit" ]; then
  line=$(sed -n 's/^ExecStart=//p' "$unit" | head -n 1 | sed -e "s|%h|$HOME|g")
  node=${line%% serve*}
  case "$line" in *" serve"*) args=${line#* serve} ;; esac
elif command -v tandem-node >/dev/null 2>&1; then node=tandem-node
elif [ -x "$HOME/TandemLLM/bin/tandem" ]; then node="$HOME/TandemLLM/bin/tandem node"
fi
if [ -z "$node" ]; then
  echo "TANDEMNODE-ERROR not_installed TandemNode is not installed" >&2
  exit 64
fi
mid=$(cat "${TANDEM_MACHINE_ID_FILE:-/etc/machine-id}" 2>/dev/null)
if [ -n "${own:-}" ] && [ "$mid" = "$own" ]; then
  echo "TANDEMNODE-ERROR own_machine this is the controller's own machine" >&2
  exit 68
fi
exec $node adopt $args --address "$addr" """


class TrustError(Exception):
    def __init__(self, kind: str, message: str, status: int):
        super().__init__(message)
        self.kind, self.message, self.status = kind, message, status


def _ip(text, what: str) -> str:
    try:
        return str(nodetrust.parse_ip(str(text or "")))
    except nodetrust.BindRefused:
        raise TrustError("bad_request", f"{what} is an IP address (the node's LAN or fabric "
                                        f"address), not \"{text}\"", 400)


def _port(v, default: int) -> int:
    if v is None:
        return default
    if not isinstance(v, int) or isinstance(v, bool) or not 0 < v < 65536:
        raise TrustError("bad_request", "port is a whole number from 1 to 65535", 400)
    return v


def _dmi(err: str) -> dict | None:
    """The DMI line the SSH login printed: {vendor, product, version, family}, None without."""
    line = next((x for x in err.splitlines() if x.startswith("TANDEMNODE-DMI ")), None)
    if line is None:
        return None
    parts = (line[len("TANDEMNODE-DMI "):].split("|") + ["", "", "", ""])[:4]
    got = dict(zip(("vendor", "product", "version", "family"), (p.strip() or None
                                                                 for p in parts)))
    return got if any(got.values()) else None


def _machine_id(path: str = "/etc/machine-id") -> str:
    """This machine's systemd machine id ('' where there is none, a Mac): the SSH-once probe
    refuses to adopt on the controller's own machine before it pins anything there."""
    try:
        with open(os.environ.get("TANDEM_MACHINE_ID_FILE") or path) as f:
            return f.read().strip()
    except OSError:
        return ""


def _login(user) -> bool:
    return isinstance(user, str) and 0 < len(user) <= 64 and not user.startswith("-") and \
        all(c.isalnum() or c in "._-" for c in user)


def _url(address: str, port: int) -> str:
    return f"https://{'[' + address + ']' if ':' in address else address}:{port}"


class Trust:
    def __init__(self, fleet, root: str, *, ssh: sshonce.Ssh | None = None, clock=time.time):
        self.fleet = fleet
        self.root = root
        self.cert = os.path.join(root, "controller.crt")
        self.key = os.path.join(root, "controller.key")
        self.name = f"TandemApp on {socket.gethostname().split('.')[0]}"
        self.clock = clock
        self.ssh = ssh or sshonce.Ssh(os.path.join(root, "known_hosts"))
        self.machine_id = _machine_id()
        self._tries: collections.deque = collections.deque()
        self._lock = threading.Lock()
        if not (os.path.isfile(self.cert) and os.path.isfile(self.key)):
            nodetrust.make_identity(self.cert, self.key, self.name)
            fleet.log(f"[fleet] this controller's certificate made: sha256 "
                      f"{self.fingerprint()[:16]} ({self.cert})")
        self.pem = open(self.cert).read()

    @property
    def cfg(self):
        return self.fleet.cfg

    def fingerprint(self) -> str:
        return nodetrust.pem_fingerprint(open(self.cert).read())

    def view(self) -> dict:
        fp = self.fingerprint()
        return {"contract_version": "1.0",
                "controller": {"id": "c-" + fp[:12], "name": self.name, "fingerprint": fp,
                               "since": round(os.stat(self.cert).st_mtime, 3)},
                "audit": self.audit()}

    def audit(self, limit: int = 50) -> list[dict]:
        out = []
        for r in self.cfg.audit(500):
            if r["object"] != "node" or r["action"] not in ACTIONS:
                continue
            d = r.get("detail") or {}
            out.append({"t": r["ts_ms"] / 1000, "action": r["action"], "node": r["object_id"],
                        "who": r["who"] or "", "via": d.get("via"),
                        "fingerprint": d.get("fingerprint"), "detail": d.get("text") or ""})
            if len(out) >= limit:
                break
        return out

    def _rate(self) -> None:
        now = self.clock()
        with self._lock:
            while self._tries and now - self._tries[0] > 60:
                self._tries.popleft()
            if len(self._tries) >= TRIES_PER_MIN:
                raise TrustError("too_many", f"{TRIES_PER_MIN} pairing attempts a minute at "
                                             f"most; wait a minute", 429)
            self._tries.append(now)

    # ------------------------------------------------------------------ the record
    def _identity(self, endpoint: str, trust: dict) -> dict | None:
        """The node's identity over the new mTLS, once its agent listens (it switches 0.3 s
        after the pairing)."""
        src = nodesource.agent_for({"endpoint": endpoint, "trust": trust}, clock=time.time)
        if not isinstance(src, nodesource.AgentNode):
            return None
        end = time.monotonic() + IDENTITY_WAIT_S
        while time.monotonic() < end:
            try:
                st, doc = src.client.json("GET", "/node/identity", timeout=3.0)
                if st == 200 and isinstance(doc, dict) and doc.get("id"):
                    return doc
            except (OSError, ssl.SSLError, ValueError):
                pass
            time.sleep(0.25)
        return None

    def _record(self, *, node_pem: str, address: str, port: int, node_id: str | None,
                via: str, who: str, took=None, host_key: str | None = None,
                tunnel: dict | None = None) -> dict:
        """The node's row after a pairing: found by its id (kept: alias, colour, tags), else an
        unpaired row at its address (a one-peer migration's), else a new one. With `tunnel` (its
        route, the agent's port on the node's loopback) the agent is reached through it."""
        fp = nodetrust.pem_fingerprint(node_pem)
        trust = {"kind": "mtls", "cert": self.cert, "key": self.key, "pin": node_pem.strip()
                 + "\n", "fingerprint": fp, "via": via, "paired_at": round(self.clock(), 3)}
        if host_key:
            trust["host_key"] = host_key
        if tunnel is not None:
            trust["tunnel"] = dict(tunnel, remote_port=port)
            endpoint = _url("127.0.0.1", self.fleet.tunnels.ensure(trust["tunnel"]))
        else:
            endpoint = _url(address, port)
        ident = self._identity(endpoint, trust) or {}
        nid = ident.get("id") or node_id
        if not nid:
            raise TrustError("unreachable", "the node paired but did not say its id", 502)
        self.fleet.ensure_local()
        if nid == self.fleet._local_id:
            self._unpin_remote(endpoint, trust)
            raise TrustError("conflict", "this is the controller's own machine: it is read "
                                         "through its loopback agent and needs no pairing", 409)
        fields = {"endpoint": endpoint, "address": address, "trust": trust,
                  "transport": "lan" if tunnel is None else "tunnel"}
        for k, v in (("hw", ident.get("hw")), ("dmi", ident.get("dmi")),
                     ("serial", ident.get("serial"))):
            if v:
                fields[k] = v
        row = self.cfg.node(nid)
        if row is None:
            old = next((n for n in self.cfg.nodes() if n["address"] == address
                        and n["endpoint"] != "local" and not n["trust"]), None)
            taken = {n["alias"].lower() for n in self.cfg.nodes() if old is None
                     or n["id"] != old["id"]}
            alias = (old or {}).get("alias") or ident.get("hostname") or nid
            base, i = alias, 2
            while alias.lower() in taken:
                alias, i = f"{base} {i}", i + 1
            doc = dict(fields, id=nid, alias=alias)
            if old is not None:
                self.cfg.delete_node(old["id"], who="controller")
                doc.update(colour=old["colour"], icon=old["icon"], tags=old["tags"])
            row = self.cfg.add_node(doc, who=who)
        else:
            row = self.cfg.update_node(nid, fields, who=who)
        action = "takeover" if took else "pair"
        text = (f"paired by {via} at {address}" + (f", taken over from "
                f"{took.get('name') or (took.get('fingerprint') or '')[:16]}" if took else ""))
        self.cfg.log_audit(who, action, "node", nid, detail={
            "via": via, "fingerprint": fp, "address": address, "text": text,
            **({"host_key": host_key} if host_key else {}),
            **({"from": took.get("fingerprint")} if took else {})})
        self.fleet.log(f"[fleet] {row['alias']} ({nid}) {text}; its certificate sha256 "
                       f"{fp[:16]} pinned")
        what = "taken over" if took else "paired"
        self.fleet.store.add_event("node", nid, f"{row['alias']} {what} by {via}",
                                   f"{text}; certificate sha256 {fp[:16]} pinned")
        return row

    def _unpin_remote(self, endpoint: str, trust: dict) -> str:
        """Ask the node to forget this controller: unpinned, refused (it no longer pins us),
        or unreachable."""
        try:
            ctx = nodetrust.client_context(trust.get("cert"), trust.get("key"), trust["pin"])
            st, _ = nodeclient.Client(endpoint, ssl_context=ctx, timeout=5.0).json(
                "DELETE", "/node/pair")
        except ssl.SSLError:
            return "refused"
        except (OSError, ValueError, nodetrust.TrustError):
            return "unreachable"
        return "unpinned" if st == 200 else "refused" if st in (401, 403) else "unreachable"

    # ------------------------------------------------------------------ by code
    def pair_code(self, body, who: str) -> dict:
        if not isinstance(body, dict) or set(body) - {"address", "code", "port"}:
            raise TrustError("bad_request", "the body is {address, code, port?}", 400)
        address = _ip(body.get("address"), "address")
        port = _port(body.get("port"), nodesource_port() + PAIR_PORT_OFFSET)
        self._rate()
        try:
            node_pem, ans = pairing.pair(address, port, str(body.get("code") or ""), self.pem,
                                         name=self.name)
        except pairing.PairError as exc:
            if exc.kind == "unreachable":
                why = pairing.owned_elsewhere(address, port - PAIR_PORT_OFFSET, self.cert,
                                              self.key)
                if why == "owned":
                    self._learn(address, owned=True)
                    raise TrustError("owned", f"{address} is paired with another TandemApp: "
                                              f"take it over on the node (tandem node takeover) "
                                              f"or release it there", 409)
                if why == "ours":
                    raise TrustError("conflict", f"{address} is paired with this TandemApp "
                                                 f"already", 409)
            self.fleet.log(f"[fleet] pairing with {address}:{port} failed: {exc.kind}")
            raise TrustError(exc.kind, exc.message, exc.status)
        row = self._record(node_pem=node_pem, address=address,
                           port=_port(ans.get("port"), nodesource_port()),
                           node_id=ans.get("node_id"), via="code", who=who,
                           took=ans.get("took_over_from"))
        self._learn(address, node=row["id"])
        return row

    # ------------------------------------------------------------------ SSH once
    def _host(self, body) -> tuple[str, int]:
        host = body.get("host")
        if not isinstance(host, str) or not host or len(host) > 253 or host.startswith("-") \
                or any(c.isspace() or c in "@/\\'\"`$;|&<>" for c in host):
            raise TrustError("bad_request", "host is an address or a host name", 400)
        return host, _port(body.get("port"), 22)

    def _route(self, body) -> tuple[list[dict], str | None]:
        """The jump hosts and the key file a pairing names (the ssh line's route)."""
        jumps = body.get("jumps") or []
        if not isinstance(jumps, list) or len(jumps) > 4:
            raise TrustError("bad_request", "jumps is a list of up to 4 jump hosts", 400)
        out = []
        for j in jumps:
            if not isinstance(j, dict) or set(j) - {"user", "host", "port"}:
                raise TrustError("bad_request", "a jump host is {user?, host, port?}", 400)
            h, p = self._host(j)
            u = j.get("user")
            if u is not None and not _login(u):
                raise TrustError("bad_request", "a jump host's user is a login name", 400)
            out.append({"user": u, "host": h, "port": p})
        kf = body.get("key_file")
        if kf is not None:
            if not isinstance(kf, str) or not kf or len(kf) > 1024 or "\n" in kf or \
                    kf.startswith("-"):
                raise TrustError("bad_request", "key_file is the path of a private key file on "
                                                "the controller's machine", 400)
            kf = os.path.expanduser(kf)
            if not os.path.isfile(kf):
                raise TrustError("bad_request", f"key_file {kf}: no such file on the "
                                                f"controller's machine", 400)
        return out, kf

    def host_key(self, body) -> dict:
        if not isinstance(body, dict) or set(body) - {"host", "port", "jumps"}:
            raise TrustError("bad_request", "the body is {host, port?, jumps?}", 400)
        host, port = self._host(body)
        jumps, _ = self._route({"jumps": body.get("jumps")})
        try:
            hk, _ = self.ssh.host_key(host, port, jumps)
        except sshonce.SshError as exc:
            raise TrustError(exc.kind, exc.message, exc.status)
        return {"contract_version": "1.0", "host": host, "port": port, **hk}

    def pair_ssh(self, body, who: str, *, command: str | None = None) -> dict:
        """SSH once. `command` (the Add Spark wizard's install, proxy/nodeinstall.py) replaces
        the REMOTE probe with the adopt of a TandemNode it just installed."""
        keys = {"host", "port", "user", "password", "key", "key_file", "jumps", "tunnel",
                "host_key", "takeover"}
        if not isinstance(body, dict) or set(body) - keys:
            raise TrustError("bad_request", "the body is {host, user, port?, password? | key? | "
                                            "key_file?, jumps?, tunnel?, host_key?, takeover?}",
                             400)
        host, port = self._host(body)
        user = body.get("user")
        if not _login(user):
            raise TrustError("bad_request", "user is a login name", 400)
        password, key = body.get("password"), body.get("key")
        jumps, key_file = self._route(body)
        if sum(x is not None for x in (password, key, key_file)) > 1:
            raise TrustError("bad_request", "a password, a key or a key file, not two", 400)
        for v, what in ((password, "password"), (key, "key")):
            if v is not None and (not isinstance(v, str) or not v or len(v) > 16384):
                raise TrustError("bad_request", f"{what} is a non-empty text", 400)
        tunnel = body.get("tunnel") is True
        if tunnel and password is not None:
            raise TrustError("bad_request", "a node behind a tunnel is reached with a key or "
                                            "the agent for as long as it is in the fleet: give "
                                            "its key file, not a password", 400)
        self._rate()
        try:
            hk, b64 = self.ssh.host_key(host, port, jumps)
        except sshonce.SshError as exc:
            raise TrustError(exc.kind, exc.message, exc.status)
        want = body.get("host_key")
        if hk["known"] == "changed":
            raise TrustError("host_key_changed", f"the host key of {host} changed: it is "
                                                 f"{hk['fingerprint']} now, {hk['stored']} "
                                                 f"before; refused (if the node was "
                                                 f"reinstalled, remove its line from "
                                                 f"{self.ssh.known_hosts})", 409)
        if want is not None and want != hk["fingerprint"]:
            raise TrustError("host_key_changed", f"the host key of {host} is "
                                                 f"{hk['fingerprint']}, not the confirmed "
                                                 f"{want}; refused", 409)
        command = (command or (f"own={shlex.quote(self.machine_id)}\n" + REMOTE)) + \
            "--name " + shlex.quote(self.name) + (" --takeover" if body.get("takeover") else "")
        # a pasted key a tunnel logs in with for as long as the node is in the fleet
        tunnel_key = key if tunnel and key_file is None else None
        if tunnel:
            # the agent listens on the node's loopback; SSH_CONNECTION's address is a WAN one
            command = command.replace('--address "$addr"', "--address 127.0.0.1")
        try:
            rc, out, err = self.ssh.run(host, port, user, command, self.pem.encode(),
                                        kind=hk["type"], b64=b64, password=password, key=key,
                                        key_file=key_file, jumps=jumps)
        except sshonce.SshError as exc:
            raise TrustError(exc.kind, exc.message, exc.status)
        finally:
            password = key = None
            body.pop("password", None)
            body.pop("key", None)
        line = next((x for x in out.splitlines() if x.startswith("TANDEMNODE-ADOPTED ")), None)
        dmi = _dmi(err)
        if rc != 0 or line is None:
            exc = self._ssh_refusal(rc, err, host, dmi)
            # 64: not installed; 66: installed but neither running nor set up as a unit
            installed = {64: False, 66: True}.get(rc, True if exc.kind == "owned" else None)
            self._learn(host, port, dmi=dmi, host_key=hk["fingerprint"], installed=installed,
                        owned=exc.kind == "owned")
            raise exc
        try:
            ans = json.loads(line[len("TANDEMNODE-ADOPTED "):])
            node_pem = ans["certificate"]
            nodetrust.pem_fingerprint(node_pem)
        except (ValueError, KeyError, TypeError, nodetrust.TrustError):
            raise TrustError("ssh_failed", f"tandem-node adopt on {host} answered something "
                                           f"else", 502)
        self.ssh.remember(host, port, hk["type"], b64)
        route = None
        if tunnel:
            route = {"host": host, "port": port, "user": user, "jumps": jumps,
                     "identity_file": key_file or self._kept_key(tunnel_key,
                                                                 ans.get("node_id"))}
            tunnel_key = None
        row = self._record(node_pem=node_pem, address=host if tunnel else
                           _ip(ans.get("address"), "the address"),
                           port=_port(ans.get("port"), nodesource_port()),
                           node_id=ans.get("node_id"), via="ssh", who=who,
                           took=ans.get("took_over_from"), host_key=hk["fingerprint"],
                           tunnel=route)
        self._learn(host, port, dmi=dmi, installed=True, host_key=hk["fingerprint"],
                    node=row["id"])
        return row

    def _kept_key(self, key: str | None, node_id: str | None) -> str | None:
        """A pasted key a tunnel needs for as long as the node is in the fleet: a 0600 file in
        `keys/` beside the controller's certificate, removed with the node (release)."""
        if key is None:
            return None
        d = os.path.join(self.root, "keys")
        os.makedirs(d, mode=0o700, exist_ok=True)
        path = os.path.join(d, (node_id or hashlib.sha256(key.encode()).hexdigest()[:12])
                            + ".key")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(key.strip() + "\n")
        return path

    def _learn(self, host: str, port: int = 22, **facts) -> None:
        """What this pairing found out, for discovery's list; never in its way."""
        try:
            self.fleet.discover().learn(host, port, **facts)
        except Exception as exc:                                     # noqa: BLE001
            self.fleet.log(f"[fleet] discovery did not take what pairing {host} found: "
                           f"{type(exc).__name__}: {exc}")

    def _ssh_refusal(self, rc: int, err: str, host: str, dmi: dict | None = None) -> TrustError:
        mark = next((x for x in err.splitlines() if x.startswith("TANDEMNODE-ERROR ")), "")
        kind = mark.split(" ", 2)[1] if mark else ""
        if rc == 64 or kind == "not_installed":
            from proxy import discovery
            got = discovery.classify_dmi(**dmi) if dmi else None
            what = f" ({got[0]}{', a GB10' if got[1] else ', not a Spark'})" if got else ""
            return TrustError("not_installed", f"TandemNode is not installed on {host}{what}: "
                                               f"the Add Spark wizard's Install step installs "
                                               f"it", 409)
        if rc == 66 or kind == "not_running":
            return TrustError("not_installed", f"TandemNode on {host} is not running and has no "
                                               f"user unit (tandemnode.service): set it up "
                                               f"first (tandem node unit)", 409)
        if rc == 68 or kind == "own_machine":
            return TrustError("conflict", f"{host} is the controller's own machine: it is read "
                                          f"through its loopback agent and needs no pairing", 409)
        if rc == 65 or kind == "owned":
            return TrustError("owned", f"{host} is paired with another TandemApp: send "
                                       f"takeover: true to move it here (SSH access proves it "
                                       f"is yours)", 409)
        lines = [x for x in err.strip().splitlines() if not x.startswith("TANDEMNODE-DMI ")]
        last = (lines or [f"exit status {rc}"])[-1][:300]
        if "Host key verification failed" in err:
            return TrustError("host_key_changed", f"{host} did not present the confirmed host "
                                                  f"key; refused", 409)
        return TrustError("ssh_failed", f"ssh to {host} failed: {last}", 502)

    # ------------------------------------------------------------------ release
    def release(self, nid: str, force: bool, who: str) -> dict:
        row = self.cfg.node(nid)
        if row is None:
            raise TrustError("not_found", f"no node {nid}", 404)
        if row["endpoint"] == "local":
            raise TrustError("conflict", "this machine is the controller's own node; there is "
                                         "nothing to release", 409)
        t = row["trust"] or {}
        if t.get("kind") == "mtls" and t.get("pin"):
            side = self._unpin_remote(row["endpoint"], t)
            if side == "unreachable" and not force:
                raise TrustError("unreachable", f"{row['alias']} does not answer; ?force=1 "
                                                f"forgets it here only (then `tandem node "
                                                f"reset` on the node)", 502)
        else:
            side = "not_paired"
        tun = t.get("tunnel")
        if tun:
            self.fleet.tunnels.close(tun)
            kept = tun.get("identity_file") or ""
            if os.path.dirname(kept) == os.path.join(self.root, "keys"):
                try:
                    os.remove(kept)
                except FileNotFoundError:
                    pass
        self.fleet.forget(nid)
        action = "release" if side in ("unpinned", "not_paired") else "forget"
        self.cfg.log_audit(who, action, "node", nid, detail={
            "via": t.get("via"), "fingerprint": t.get("fingerprint"),
            "text": f"{row['alias']} released (node side: {side})"})
        self.fleet.log(f"[fleet] {row['alias']} ({nid}) released; node side: {side}")
        self.fleet.store.add_event("node", nid, f"{row['alias']} released" if action == "release"
                                   else f"{row['alias']} forgotten here",
                                   f"node side: {side}")
        return {"contract_version": "1.0", "id": nid, "node": side}


def nodesource_port() -> int:
    from proxy import nodeagent
    return nodeagent.DEFAULT_PORT


def migrate_peers(fleet) -> list[str]:
    """SRV-723's one-peer migration: an engine backend of kind tandemllm on another machine
    with a host collector becomes a node record at its address, unpaired (`off`, "not paired")
    until pairing finds it there and gives it the node's own id. Ids of the new rows."""
    made = []
    nodes = fleet.cfg.nodes()
    for b in fleet.cfg.backends():
        h = b.get("host") or {}
        if not b["enabled"] or b["cloud"] or b["kind"] != "tandemllm" or \
                h.get("mode") != "collector" or not h.get("url"):
            continue
        if fleet.locality.is_local(b["url"]):
            continue
        address = urllib.parse.urlsplit(b["url"]).hostname or ""
        if not address or any(n["address"] == address for n in nodes):
            continue
        nid = "n-" + hashlib.sha256(f"peer:{address}".encode()).hexdigest()[:12]
        taken = {n["alias"].lower() for n in nodes}
        alias, i = b.get("label") or address, 2
        while alias.lower() in taken:
            alias, i = f"{b.get('label') or address} {i}", i + 1
        row = fleet.cfg.add_node({"id": nid, "alias": alias, "endpoint": "", "address": address,
                                  "trust": {}}, who="controller")
        nodes.append(row)
        made.append(nid)
        fleet.log(f"[fleet] the peer backend {b['id']} at {address} is node {alias} ({nid}), "
                  f"not paired yet")
    return made
