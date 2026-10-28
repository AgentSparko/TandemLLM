"""The controller's side of node trust (SEC-4): its own certificate, pairing by code and by SSH
once, release, and the audit of each.

    t = Trust(fleet, dir)                   dir: `controller/` beside the config database
    t.view()                                {controller: {id, name, fingerprint, since}, audit}
    t.pair_code({address, code, port?}, who)              -> the node's row
    t.host_key({host, port?})                             -> {type, fingerprint, known, stored}
    t.pair_ssh({host, user, port?, password?, key?, host_key?, takeover?}, who) -> the node's row
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
the same authenticated channel and is pinned. A node another TandemApp owns is taken over only
with `takeover: true` (SSH access proves the node is the user's).

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
                via: str, who: str, took=None, host_key: str | None = None) -> dict:
        """The node's row after a pairing: found by its id (kept: alias, colour, tags), else an
        unpaired row at its address (a one-peer migration's), else a new one."""
        fp = nodetrust.pem_fingerprint(node_pem)
        endpoint = _url(address, port)
        trust = {"kind": "mtls", "cert": self.cert, "key": self.key, "pin": node_pem.strip()
                 + "\n", "fingerprint": fp, "via": via, "paired_at": round(self.clock(), 3)}
        if host_key:
            trust["host_key"] = host_key
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
                  "transport": "lan"}
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
                    raise TrustError("owned", f"{address} is paired with another TandemApp: "
                                              f"take it over on the node (tandem node takeover) "
                                              f"or release it there", 409)
                if why == "ours":
                    raise TrustError("conflict", f"{address} is paired with this TandemApp "
                                                 f"already", 409)
            self.fleet.log(f"[fleet] pairing with {address}:{port} failed: {exc.kind}")
            raise TrustError(exc.kind, exc.message, exc.status)
        return self._record(node_pem=node_pem, address=address,
                            port=_port(ans.get("port"), nodesource_port()),
                            node_id=ans.get("node_id"), via="code", who=who,
                            took=ans.get("took_over_from"))

    # ------------------------------------------------------------------ SSH once
    def _host(self, body) -> tuple[str, int]:
        host = body.get("host")
        if not isinstance(host, str) or not host or len(host) > 253 or host.startswith("-") \
                or any(c.isspace() or c in "@/\\'\"`$;|&<>" for c in host):
            raise TrustError("bad_request", "host is an address or a host name", 400)
        return host, _port(body.get("port"), 22)

    def host_key(self, body) -> dict:
        if not isinstance(body, dict) or set(body) - {"host", "port"}:
            raise TrustError("bad_request", "the body is {host, port?}", 400)
        host, port = self._host(body)
        try:
            hk, _ = self.ssh.host_key(host, port)
        except sshonce.SshError as exc:
            raise TrustError(exc.kind, exc.message, exc.status)
        return {"contract_version": "1.0", "host": host, "port": port, **hk}

    def pair_ssh(self, body, who: str) -> dict:
        keys = {"host", "port", "user", "password", "key", "host_key", "takeover"}
        if not isinstance(body, dict) or set(body) - keys:
            raise TrustError("bad_request", "the body is {host, user, port?, password? | key?, "
                                            "host_key?, takeover?}", 400)
        host, port = self._host(body)
        user = body.get("user")
        if not isinstance(user, str) or not user or len(user) > 64 or user.startswith("-") \
                or not all(c.isalnum() or c in "._-" for c in user):
            raise TrustError("bad_request", "user is a login name", 400)
        password, key = body.get("password"), body.get("key")
        if password is not None and key is not None:
            raise TrustError("bad_request", "a password or a key, not both", 400)
        for v, what in ((password, "password"), (key, "key")):
            if v is not None and (not isinstance(v, str) or not v or len(v) > 16384):
                raise TrustError("bad_request", f"{what} is a non-empty text", 400)
        self._rate()
        try:
            hk, b64 = self.ssh.host_key(host, port)
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
        command = REMOTE + "--name " + shlex.quote(self.name) + (
            " --takeover" if body.get("takeover") else "")
        try:
            rc, out, err = self.ssh.run(host, port, user, command, self.pem.encode(),
                                        kind=hk["type"], b64=b64, password=password, key=key)
        except sshonce.SshError as exc:
            raise TrustError(exc.kind, exc.message, exc.status)
        finally:
            password = key = None
            body.pop("password", None)
            body.pop("key", None)
        line = next((x for x in out.splitlines() if x.startswith("TANDEMNODE-ADOPTED ")), None)
        if rc != 0 or line is None:
            raise self._ssh_refusal(rc, err, host)
        try:
            ans = json.loads(line[len("TANDEMNODE-ADOPTED "):])
            node_pem = ans["certificate"]
            nodetrust.pem_fingerprint(node_pem)
        except (ValueError, KeyError, TypeError, nodetrust.TrustError):
            raise TrustError("ssh_failed", f"tandem-node adopt on {host} answered something "
                                           f"else", 502)
        self.ssh.remember(host, port, hk["type"], b64)
        return self._record(node_pem=node_pem, address=_ip(ans.get("address"), "the address"),
                            port=_port(ans.get("port"), nodesource_port()),
                            node_id=ans.get("node_id"), via="ssh", who=who,
                            took=ans.get("took_over_from"), host_key=hk["fingerprint"])

    def _ssh_refusal(self, rc: int, err: str, host: str) -> TrustError:
        mark = next((x for x in err.splitlines() if x.startswith("TANDEMNODE-ERROR ")), "")
        kind = mark.split(" ", 2)[1] if mark else ""
        if rc == 64 or kind == "not_installed":
            return TrustError("not_installed", f"TandemNode is not installed on {host}: the "
                                               f"Add Spark wizard's Install step installs it", 409)
        if rc == 66 or kind == "not_running":
            return TrustError("not_installed", f"TandemNode on {host} is not running and has no "
                                               f"user unit (tandemnode.service): set it up "
                                               f"first (tandem node unit)", 409)
        if rc == 65 or kind == "owned":
            return TrustError("owned", f"{host} is paired with another TandemApp: send "
                                       f"takeover: true to move it here (SSH access proves it "
                                       f"is yours)", 409)
        last = (err.strip().splitlines() or [f"exit status {rc}"])[-1][:300]
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
