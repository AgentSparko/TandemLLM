"""`tandem-node` (`tandem node`): TandemNode, the agent on every Spark that TandemApp's controller
talks to (SRV-725). Port 7700.

    tandem-node [serve] [--host 127.0.0.1] [--port 7700] [--state-dir ~/.tandemllm/node]
                [--tandem-dir DIR] [--virtual NAME] [--mdns on|off] [--weights-dir DIR]
                                                      the agent (systemd user unit: `tandem-node unit`)
    tandem-node check [...]                           how it would listen, and why; starts nothing
    tandem-node unit [...]                            a systemd user unit for these options, on stdout
    tandem-node pair [--address IP] [--takeover]      a pairing code for TandemApp (SEC-4), until used
    tandem-node takeover [--address IP]               the same, moving the node to another TandemApp
    tandem-node status                                the owner, this node's fingerprint, the window
    tandem-node reset [--keys]                        forget the owner and every pin (and the key)
    tandem-node adopt --address IP [--takeover]       a controller's certificate from stdin (SSH once)

ROUTES (every one behind the caller check below; JSON unless an event stream):

    GET    /node/identity              node id, hostname, DMI product and serial, OS, kernel, driver,
                                       CUDA, TandemLLM version, code sha, how the agent listens
    GET    /node/inventory             the fleet's inventory line, and disks, NICs (ConnectX ports with
                                       their firmware), memory, CPU cores, GPUs
    GET    /node/metrics               events `sample` once a second (proxy/nodemetrics.py); ?once=1
                                       the newest as JSON
    GET    /node/engines               the engines (proxy/nodeengines.py): state, health, job
    GET    /node/engines/{id}
    POST   /node/engines/{id}/start | stop | restart     202 and the job; 409 not managed or busy
    GET    /node/engines/{id}/<route>  the engine's /health, /v1/models, /v1/dashboard/live (its live
                                       stream), /v1/dashboard/system, /v1/dashboard/logs, /metrics
    GET    /node/maintenance           the machine's actions (proxy/nodemaint.py): whether reboot and
                                       update are set up here, simulated on a virtual node, the job
    POST   /node/maintenance/reboot | update      202 and the job; 409 not set up or busy
    GET    /node/weights               the engine's Setup Guide weights and the weights store's
                                       packs (proxy/nodeweights.py), each with a content id; the
                                       store's folder and free space
    GET    /node/weights/{profile}/manifest   every file of a pack: size, sha256 when known
    GET    /node/weights/{profile}/files/{path}   one file's bytes (a copy the controller relays)
    DELETE /node/weights/{profile}?confirm=1 | ?dry_run=1   a store pack removed, else the Setup
                                       Guide's removal of one profile
    POST   /node/weights/receive       {copy, profile, files}: a copy arriving (SRV-731): its token
    GET    /node/weights/receive/{copy}               its progress
    PUT    /node/weights/receive/{copy}/{path}        one file (a peer node: with the token only)
    POST   /node/weights/receive/{copy}/commit        every file there, hashed: the pack in place
    DELETE /node/weights/receive/{copy}               aborted, nothing left
    POST   /node/weights/send {copy, profile, to, token}   202: this node pushes a pack to the
                                       peer agent at `to` (pinned mTLS)
    GET    /node/weights/send/{copy}, DELETE ...      its progress; stop it
    GET    /node/logs                  ?level=&q=&source=all|agent|<engine>&backlog=; events `log`;
                                       ?follow=0 the lines as JSON
    GET    /node/linktest/info         this node's interfaces (proxy/linktest.py)
    GET    /node/linktest/echo         a round trip
    POST   /node/linktest/sink         a body read and dropped: the bandwidth test's far end
    POST   /node/linktest              {peer, bytes?, streams?, rtt_n?}: this node to the peer's agent
    *      /node/setup/<route>         the engine's /v1/setup/<route> (the Setup Guide) as it answers;
                                       its status responder (OPS-175) while it restarts; 503
                                       no_engine when no engine here serves the Setup Guide,
                                       engine_down when it does not answer. POST estimate too:
                                       this agent computes no estimate itself
    GET    /node/pair                  the owner, this node's fingerprint, the window (its code only
                                       to this node's own caller), the audit log's last lines
    POST   /node/pair                  {address?, port?, takeover?}: open a pairing window (this
                                       node's own caller only: the secret, or its own certificate)
    DELETE /node/pair/window           close it
    POST   /node/pair/reload           read the trust store again (after `adopt` or `reset`)
    DELETE /node/pair                  release: the owner forgotten (the owner, or this node)
    PUT    /node/peers/{id}            {certificate}: another node pinned for link tests (the owner)
    DELETE /node/peers/{id}
    GET    /node/preflight             the Add Spark wizard's checks of this box (proxy/preflight.py
                                       SCRIPT, run here): {facts}
    PUT    /node/fleet                 {fingerprint, name}: the controller on this machine says this
                                       node is its fleet's own (loopback secret only); mDNS then
                                       says owned
    GET    /node/update                whether this agent updates itself here, its version, the job
                                       (proxy/nodeupdate.py, OPS-234)
    PUT    /node/update/package?name=&sha256=     the wheel the controller updates it from
    POST   /node/update {name, sha256, version, ref?, install_sh?}   202 and the job: the wheel
                                       installed, the engine's runtime, the agent restarted on it
    PUT    /node/owner {certificate, name, address?}   the controller moves (OPS-234): the owner
                                       hands this node to the new controller's certificate; the
                                       controller on this machine (the secret, no owner yet)
                                       hands it on with the LAN address to listen on
    GET    /node/controller            a controller this agent started on this machine, or none
    POST   /node/controller {bundle, port, admin_token, name?, state_dir?}   the owner's: a
                                       TandemApp controller started here as a systemd user unit
                                       (proxy/appservice.py) with the fleet's export, which it
                                       imports at its first start; its certificate in the answer

CALLERS. An address is listened on as proxy/nodetrust.py's bind rule says: loopback in plain HTTP
behind the loopback secret (`<state dir>/secret`, 0600, made at the first start) until the trust
store (`<state dir>/trust`) is filled, then mTLS; any other address only in mTLS; never 0.0.0.0 or a
WAN address. A call without the secret, or a TLS client without a certificate the store vouches
for, gets no answer from any route. The owner (the controller pinned as peers/owner.pem) and this
node's own certificate reach every route; another pinned node only its identity, the link test and a weights receive whose token it holds.
Every change (an engine or machine action, a removal, a setup POST, a link test, a pairing) is a
log line with the caller (`secret`, `self`, `owner:` or `peer:` and the certificate's
fingerprint).

PAIRING (SEC-4, proxy/pairing.py). `tandem node pair` asks this agent, on loopback, for a window:
a 6-digit code (5 minutes, 5 tries, single use) and a TLS listener at the address the controller
reaches (default: the default route's source address) on port + 1, which answers the pairing
protocol and nothing else. Its success pins the controller as the owner, writes the address to
listen.json and listens there in mTLS on the agent's port; loopback switches to mTLS too (this
node's CLI then uses its own certificate). A node with an owner opens a window only for a
take-over (`--takeover`, or `tandem node takeover`): the old owner's pin goes, its open
connections are closed. `adopt` is the same pinning for SSH once (proxy/sshonce.py): the
controller's certificate on stdin, from a session the user's SSH login opened. Every pairing,
release, take-over and reset is a line in the trust store's audit.log and the agent's log.

MDNS (SRV-727, proxy/mdns.py). The agent advertises `_tandemnode._tcp` so that TandemApp's Add
Spark wizard finds it, on the addresses its bind rule allows (LAN, link-local, fabric; never
loopback, never a WAN address) and answers only askers on those subnets. The TXT carries no
secret: `id` (the node id), `class` (spark for a GB10 by its DMI or GPU, else other), `ver`
(TandemLLM), `hw` (the box by its DMI), `paired` (0 or 1) and `owner` (the first 16 hex of the
owning controller's certificate fingerprint: public, as any TLS client sees it), `listen`
(loopback or lan), `window` (the pairing window's port while one is open), `hk` (the SSH host
key's fingerprint, which ssh-keyscan reads anyway; not on a virtual node, which shares its
machine's) and `virtual` (a virtual node's name). The node of the controller's own machine has no
owner (the controller reads it over loopback with the secret), so the controller says so once
(PUT /node/fleet, kept in `<state dir>/fleet.json`): it advertises `paired=1` and that
controller's fingerprint as `owner`, and another TandemApp lists it as owned, never as a Spark to
pair. Docker and VM bridges, VPN tunnels and Apple's peer links (discovery.VIRTUAL) are no LAN
and get no answer. An unpaired node advertises too, although its
agent listens on loopback only: the advertisement opens no port, and it is what lets the wizard say
"Spark with TandemLLM x.y, not paired" and, while `tandem node pair` shows a code, "pairing code
showing on the node" (the TXT says `listen=loopback`, the SRV the agent's port it will listen on
once paired). Every pairing, release and window is announced at once. `--mdns off` (or
TANDEM_NODE_MDNS=off) advertises nothing.

ENGINES. The engine's own routes are called on loopback without proxy headers, where it trusts its
caller; the caller's secret is never passed on. The Setup Guide routes run in the engine, so the
node's Setup Guide is the engine's, unchanged; the first TandemLLM engine in the list serves them.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import http.client
import queue
import re
import select
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if __package__ in (None, ""):                                    # pragma: no cover
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proxy import hostcollect, linktest, mdns, nodeclient, nodeengines, nodemaint  # noqa: E402
from proxy import nodemetrics, nodeupdate, nodeweights  # noqa: E402
from proxy import nodesource, nodetrust, pairing  # noqa: E402

DEFAULT_PORT = 7700
DEFAULT_DIR = "~/.tandemllm/node"
MAX_JSON = 1 << 20
PING_S = 5.0
LEVELS = ("debug", "info", "warn", "error")
TO_BUF = {"warn": "warning"}
FROM_BUF = {"warning": "warn"}
INVENTORY_EVERY_S = 600.0
# the engines' memory split in the stream: the engine takes 0.1 to 0.6 s to answer it
SYSTEM_EVERY_S = 60.0
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# who reaches which routes once the caller is known (CALLERS above)
LOCAL_ROLES = ("secret", "self")
PEER_ROUTES = (("GET", "identity"), ("GET", "linktest"), ("POST", "linktest"),
               ("PUT", "weights"), ("POST", "weights"))
UNIT = "~/.config/systemd/user/tandemnode.service"


def virtual_id(machine_id: str, virtual: str | None) -> str:
    """The machine's node id; a virtual node's (a second agent on one machine, OPS-197) own."""
    if not virtual:
        return machine_id
    import hashlib
    return "n-" + hashlib.sha256(f"{machine_id}:{virtual}".encode()).hexdigest()[:12]


def _code_sha() -> str | None:
    if not os.path.isdir(os.path.join(ROOT, ".git")):
        return None
    try:
        r = subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"], capture_output=True,
                           text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return (r.stdout.strip() or None) if r.returncode == 0 else None


def content_id(path: str) -> tuple[str | None, str | None]:
    """(sha256:<hex>, basis) of a weights folder from the file hashes it already records, never
    from reading the weights: `_hub_manifest.json` (ops/fetch_weights.py), else the Hugging Face
    download metadata (`.cache/huggingface/download/*.metadata`, a `--local-dir` download), else a
    Hugging Face cache snapshot's links into its blobs (named by their hash). (None, None) when
    none of these is there."""
    import hashlib
    rows = None
    basis = None
    try:
        with open(os.path.join(path, "_hub_manifest.json")) as f:
            files = json.load(f).get("files") or {}
        rows = [f"{n}\t{v.get('size')}\t{v.get('sha256') or ''}" for n, v in files.items()
                if isinstance(v, dict)]
        basis = "hub_manifest"
    except (OSError, ValueError, AttributeError):
        pass
    if rows is None:
        meta = os.path.join(path, ".cache", "huggingface", "download")
        got = []
        for d, _dirs, fs in os.walk(meta):
            for f in fs:
                if not f.endswith(".metadata"):
                    continue
                try:
                    with open(os.path.join(d, f)) as fh:
                        lines = fh.read().splitlines()
                except OSError:
                    continue
                if len(lines) >= 2:
                    got.append(f"{os.path.relpath(os.path.join(d, f[:-9]), meta)}\t{lines[1]}")
        if got:
            rows, basis = got, "hf_metadata"
    if rows is None and "/snapshots/" in os.path.realpath(path) + "/":
        got = []
        for d, _dirs, fs in os.walk(path):
            for f in fs:
                p = os.path.join(d, f)
                if os.path.islink(p):
                    got.append(f"{os.path.relpath(p, path)}\t{os.path.basename(os.readlink(p))}")
        if got:
            rows, basis = got, "hf_blobs"
    if not rows:
        return None, None
    return "sha256:" + hashlib.sha256("\n".join(sorted(rows)).encode()).hexdigest(), basis


class Agent:
    """Everything the routes read: this machine (LocalNode over its own host collector), the 1 s
    sampler, the engines, the trust store and the secret."""

    def __init__(self, state_dir: str, *, tandem_dir: str | None = None, root: str = "/",
                 collector=None, local=None, engines=None, clock=time.time, sample_wait=None,
                 addrs=None, ping=None, logbuf=None, log=None, virtual: str | None = None,
                 maintenance=None, weights_dir: str | None = None):
        self.state_dir = os.path.expanduser(state_dir)
        os.makedirs(self.state_dir, mode=0o700, exist_ok=True)
        self.log = log or (lambda m: print(m, flush=True))
        self.clock = clock
        self.root = root
        self.virtual = virtual
        self.store = nodetrust.Store(os.path.join(self.state_dir, "trust"))
        if self.store.ensure_identity(socket.gethostname().split(".")[0] + (
                f"-{virtual}" if virtual else "")):
            self.log(f"[node] this node's certificate made: sha256 "
                     f"{self.store.own_fingerprint()[:16]}")
        self.secret_path = os.path.join(self.state_dir, "secret")
        self.secret = nodetrust.load_secret(self.secret_path, create=True)
        self.engines = engines or nodeengines.Engines.load(self.state_dir, tandem_dir,
                                                           virtual=virtual, log=self.log,
                                                           clock=clock)
        self.maintenance = maintenance or nodemaint.Maintenance.load(
            self.state_dir, virtual=virtual, log=self.log, clock=clock)
        self.weights = nodeweights.Store(weights_dir or os.path.join(self.state_dir, "weights"),
                                         log=self.log, clock=clock)
        self.collector = collector or hostcollect.HostCollector(fast_period=0.5,
                                                                paths=[self.state_dir])
        from proxy import _version
        self.local = local or nodesource.LocalNode(
            hosts=lambda: self.collector.sample([]), version=_version.release, root=root,
            clock=clock, system_every=SYSTEM_EVERY_S)
        self.local.sync([{"id": e.id, "kind": e.kind, "url": e.url("/v1")}
                         for e in self.engines.list])
        self.reader = nodemetrics.Reader(self.local, self.collector, root=root, clock=clock)
        self._read_lock = threading.Lock()
        self.sampler = nodemetrics.Sampler(self._sample, clock=clock, wait=sample_wait,
                                           log=self.log)
        self.addrs = addrs or linktest.ip_addrs
        self.ping = ping
        self.logbuf = logbuf
        self.started = clock()
        self.listening: dict = {}                # address -> mode
        self.servers: list = []
        self.hosts: list[str] = []               # --host: always listened on
        self.port = DEFAULT_PORT
        self.window: pairing.Window | None = None
        self.pair_server = None
        self._trust = threading.RLock()
        self.code_sha = _code_sha()
        self._inv: dict | None = None
        self._inv_at = -INVENTORY_EVERY_S
        self.mdns = None                         # the mDNS responder (main), or None
        self.updater = nodeupdate.Updater(self.state_dir, virtual=virtual, tandem_dir=tandem_dir,
                                          log=self.log, clock=clock,
                                          release=lambda: self.code_release())
        self.started_controller: dict | None = None
        from proxy import appservice, _version
        running = _version.release()
        # the TandemLLM this agent's code is: read once, so an update installed beside it shows
        # only once the agent runs it (nodeupdate's restart)
        self.code_release = lambda: running
        self.service = appservice.Service         # the controller's service (start_controller)

    def _sample(self, t: float) -> dict:
        with self._read_lock:
            return self.reader.sample(t)

    # ------------------------------------------------------------------ who and what
    def node_id(self) -> str:
        return virtual_id(self.local.identity()["id"], self.virtual)

    def identity(self) -> dict:
        ident = self.local.identity()
        inv = self.local.inventory()
        owner = self.store.owner()
        return nodemetrics.prune({
            "id": self.node_id(),
            "hostname": ident["alias"] + (f"-{self.virtual}" if self.virtual else ""),
            "serial": ident["serial"],
            "hw": ident["hw"], "dmi": ident["dmi"], "address": ident["address"],
            "os": inv.get("os"), "kernel": inv.get("kernel"),
            "driver": inv.get("driver"), "cuda": inv.get("cuda"),
            "tandemllm": self.code_release() or None, "code_sha": self.code_sha,
            "virtual": self.virtual,
            "agent": {"listen": [{"address": a, "port": m["port"], "mode": m["mode"]}
                                 for a, m in self.listening.items()],
                      "started_at": self.started, "pid": os.getpid()},
            "trust": {"fingerprint": self.store.own_fingerprint(),
                      "owner": None if owner is None else
                      {k: owner.get(k) for k in ("name", "fingerprint", "via", "paired_at")}}})

    def inventory(self) -> dict:
        now = self.clock()
        if self._inv is not None and now - self._inv_at < INVENTORY_EVERY_S:
            return self._inv
        r = self.reader
        disks = []
        for d in r._ls("sys", "block"):
            if d.startswith(("loop", "ram", "zram", "dm-", "sr")):
                continue
            sectors = r._int("sys", "block", d, "size")
            disks.append({"name": d, "nvme": d.startswith("nvme"),
                          "model": r._read("sys", "block", d, "device", "model"),
                          "size_gb": None if sectors is None else round(sectors * 512 / 1e9, 1),
                          "rotational": r._int("sys", "block", d, "queue", "rotational") == 1})
        nics = []
        for i in linktest.interfaces(r, self.addrs()):
            drv = os.path.realpath(r._p("sys", "class", "net", i["iface"], "device", "driver"))
            row = dict(i, driver=os.path.basename(drv) if os.path.isdir(drv) else None,
                       mac=r._read("sys", "class", "net", i["iface"], "address"))
            rp = r.rdma_port(i["iface"])
            if rp is not None:
                row["firmware"] = r._read("sys", "class", "infiniband", rp[0], "fw_ver")
                row["board_id"] = r._read("sys", "class", "infiniband", rp[0], "board_id")
            nics.append(row)
        caps = r._capacities()
        h = self.local._last_host or self.local.host() or {}
        mem = h.get("memory") or {}
        g = lambda b: None if b is None else round(b / nodesource.GIB, 2)   # noqa: E731
        inv = nodemetrics.prune({
            "fleet": self.local.inventory(), "lan": self.local.lan(), "disks": disks,
            "nics": nics,
            "memory": {"total_gib": g(mem.get("total_bytes")),
                       "swap_total_gib": g(mem.get("swap_total_bytes")),
                       "unified": mem.get("unified")},
            "cpu": {"count": os.cpu_count(),
                    "performance": sum(1 for v in caps.values() if v == "performance") or None,
                    "efficiency": sum(1 for v in caps.values() if v == "efficiency") or None},
            "gpus": [{"index": x.get("index"), "name": x.get("name"), "uuid": x.get("uuid"),
                      "memory_unified": x.get("memory_unified")} for x in h.get("gpus") or []]})
        self._inv, self._inv_at = inv, now
        return inv

    def mdns_info(self) -> dict:
        """The `_tandemnode._tcp` service this node advertises (MDNS above)."""
        from proxy import discovery
        r = self.reader
        dmi = {k: r._read("sys", "class", "dmi", "id", f) for k, f in (
            ("vendor", "sys_vendor"), ("product", "product_name"),
            ("version", "product_version"), ("family", "product_family"))}
        got = discovery.classify_dmi(**dmi)
        h = self.local._last_host or {}
        gb10 = any("GB10" in (g.get("name") or "") for g in h.get("gpus") or [])
        owner = self.store.owner() or self.fleet_claim()
        nid = self.node_id()
        txt = {"v": "1", "id": nid, "class": "spark" if (got and got[1]) or gb10 else "other",
               "ver": self.code_release() or None, "hw": got[0] if got else None,
               "paired": "1" if owner else "0"}
        if owner is not None:
            txt["owner"] = owner["fingerprint"][:16]
        lan = any(not nodetrust.parse_ip(a).is_loopback for a in self.listening)
        txt["listen"] = "lan" if lan else "loopback"
        w, srv = self.window, self.pair_server
        if w is not None and srv is not None and w.view()["state"] == "open":
            txt["window"] = str(srv.server_address[1])
        if self.virtual:
            txt["virtual"] = self.virtual
        else:
            txt["hk"] = self.ssh_host_key()
        return {"instance": socket.gethostname().split(".")[0] + (
                    f"-{self.virtual}" if self.virtual else ""),
                "port": self.port, "target": "tandemnode-" + nid.removeprefix("n-")[:12],
                "txt": {k: v for k, v in txt.items() if v is not None}}

    def fleet_claim(self) -> dict | None:
        """{fingerprint, name} of the controller on this machine that reads this node as its
        fleet's own (PUT /node/fleet), or None."""
        try:
            with open(os.path.join(self.state_dir, "fleet.json")) as f:
                doc = json.load(f)
            return doc if isinstance(doc, dict) and doc.get("fingerprint") else None
        except (OSError, ValueError):
            return None

    def claim(self, body) -> dict:
        if not isinstance(body, dict) or set(body) - {"fingerprint", "name"} or \
                not isinstance(body.get("fingerprint"), str) or \
                not re.fullmatch(r"[0-9a-f]{64}", body["fingerprint"]):
            raise ValueError("the body is {fingerprint: sha256 hex, name?}")
        doc = {"fingerprint": body["fingerprint"], "name": str(body.get("name") or "")[:80],
               "at": round(self.clock(), 3)}
        was = self.fleet_claim()
        path = os.path.join(self.state_dir, "fleet.json")
        fd = os.open(path + ".part", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(doc, f)
        os.replace(path + ".part", path)
        if (was or {}).get("fingerprint") != doc["fingerprint"]:
            self.log(f"[node] the controller on this machine ({doc['name'] or 'TandemApp'}, "
                     f"sha256 {doc['fingerprint'][:16]}) reads this node as its fleet's own")
            self.advertise()
        return doc

    def preflight(self) -> dict:
        """The wizard's checks of this box: proxy/preflight.py's script, run here."""
        from proxy import _version, preflight
        env = dict(os.environ, TANDEM_PF_SELF=_version.release() or "")
        r = subprocess.run(["sh", "-c", preflight.SCRIPT], capture_output=True, text=True,
                           env=env, timeout=30)
        return {"facts": preflight.parse(r.stdout), "at": round(self.clock(), 3)}

    def ssh_host_key(self) -> str | None:
        """This machine's SSH host key fingerprint (the ed25519 one first), from its public file."""
        from proxy import sshonce
        for kind in ("ed25519", "ecdsa", "rsa"):
            try:
                with open(os.path.join(self.root, "etc", "ssh", f"ssh_host_{kind}_key.pub")) as f:
                    return sshonce.fingerprint(f.read().split()[1])
            except (OSError, IndexError, sshonce.SshError):
                continue
        return None

    def advertise(self) -> None:
        """The advertisement again, at once: the pairing state or the window changed."""
        if self.mdns is not None:
            self.mdns.update()

    def setup_engine(self) -> nodeengines.Engine | None:
        return next((e for e in self.engines.list if e.kind == "tandemllm"), None)

    # ------------------------------------------------------------------ trust (SEC-4)
    def open_window(self, body, who: str) -> dict:
        """A pairing window: a code and a TLS listener for the protocol at `address`:`port`
        (default: the default route's source address, the agent's port + 1)."""
        if not isinstance(body, dict):
            raise ValueError("the body is {address?, port?, takeover?}")
        takeover = bool(body.get("takeover"))
        owner = self.store.owner()
        if owner is not None and not takeover:
            name = owner.get("name") or "a TandemApp"
            raise nodeengines.EngineError(409, "owned", f"owned by {name} (sha256 "
                                          f"{owner['fingerprint'][:16]}); `tandem node takeover` "
                                          f"moves it to another TandemApp, or release it there")
        address = str(body.get("address") or self.local.address() or "")
        ip = nodetrust.parse_ip(address)
        if ip.is_unspecified or ip.is_global:
            raise nodetrust.BindRefused(f"{address}: a pairing window listens on a LAN, fabric "
                                        f"or loopback address, never 0.0.0.0 or a WAN address")
        port = body.get("port")
        port = self.port + 1 if port is None else int(port)
        with self._trust:
            self.close_window()
            w = pairing.Window(self.store.own_pem(), takeover=takeover, address=str(ip),
                               clock=self.clock)
            srv = PairServer((str(ip), port), self, w)
            self.window, self.pair_server = w, srv
            threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.25},
                             daemon=True).start()
            threading.Thread(target=self._watch_window, args=(w, srv), daemon=True).start()
        self.log(f"[node] {who} opened a pairing window on {ip}:{srv.server_address[1]}"
                 f"{' (take-over)' if takeover else ''}, 5 minutes, 5 tries")
        self.advertise()
        return dict(w.view(), port=srv.server_address[1])

    def _watch_window(self, w: pairing.Window, srv) -> None:
        while w.view()["state"] == "open" and self.pair_server is srv:
            time.sleep(0.25)
        st = w.view()["state"]
        if st in ("expired", "spent"):
            self.log(f"[node] the pairing window closed: {st} ({w.failed} failed tries)")
            self.store.audit("window_" + st, failed=w.failed, address=w.address)
        with self._trust:
            if self.pair_server is srv:
                self.pair_server = None
        _stop(srv)
        self.advertise()

    def close_window(self) -> None:
        with self._trust:
            w, srv = self.window, self.pair_server
            self.pair_server = None
        if w is not None:
            w.close()
        if srv is not None:
            _stop(srv)
            self.advertise()

    def window_view(self, with_code: bool) -> dict | None:
        w = self.window
        if w is None:
            return None
        v = w.view()
        if not with_code:
            v.pop("code")
        srv = self.pair_server
        v["port"] = srv.server_address[1] if srv is not None else None
        return v

    def paired(self, w: pairing.Window, pem: str, name: str) -> dict:
        """The window's success: the controller pinned as the owner, the address listened on in
        mTLS. The pairing answer {node_id, port, took_over_from}."""
        with self._trust:
            old = self.store.owner()
            fp = self.store.set_owner(pem, {"name": name, "via": "code",
                                            "paired_at": round(self.clock(), 3),
                                            "address": w.address})
            took = None
            if old is not None and old["fingerprint"] != fp:
                took = {"name": old.get("name"), "fingerprint": old["fingerprint"]}
            self._remember_address(w.address)
            row = self.store.audit("takeover" if took else "pair", via="code",
                                   controller=fp, name=name, address=w.address,
                                   **({"from": took["fingerprint"]} if took else {}))
        self.log(f"[node] {row['action']}: paired with {name or 'a TandemApp'} (sha256 "
                 f"{fp[:16]}) by code on {w.address}"
                 + (f", the pin of {took['name'] or took['fingerprint'][:16]} removed" if took
                    else ""))
        threading.Timer(0.3, self.apply_trust).start()
        return {"node_id": self.node_id(), "port": self.port, "took_over_from": took,
                "fingerprint": self.store.own_fingerprint()}

    def _remember_address(self, address: str) -> None:
        if address and not nodetrust.parse_ip(address).is_loopback:
            self.store.set_listen(self.store.listen_addrs() + [address])

    def release(self, who: str) -> dict | None:
        """The owner forgotten (its pin, its addresses): a release from the owner or this node."""
        with self._trust:
            was = self.store.clear_owner()
            if was is not None:
                self.store.audit("release", by=who, controller=was["fingerprint"],
                                 name=was.get("name"))
        if was is not None:
            self.log(f"[node] {who} released this node from {was.get('name') or 'its owner'} "
                     f"(sha256 {was['fingerprint'][:16]})")
        threading.Timer(0.3, self.apply_trust).start()
        return was

    def handover(self, body, role: str, who: str) -> dict:
        """The controller moves (OPS-234): its successor's certificate pinned as the owner in
        its place, the old owner's connections closed. The owner hands over; the controller on
        this machine (the loopback secret) hands on a node no one owns, with the LAN `address`
        the new controller reaches it at."""
        if not isinstance(body, dict) or set(body) - {"certificate", "name", "address"}:
            raise ValueError("the body is {certificate, name, address?}")
        pem = str(body.get("certificate") or "")
        name = str(body.get("name") or "")[:80]
        with self._trust:
            old = self.store.owner()
            if role != "owner" and old is not None:
                raise nodeengines.EngineError(403, "forbidden", "an owned node is handed over by "
                                                                "its owner")
            address = body.get("address")
            if role != "owner":
                ip = nodetrust.parse_ip(str(address or ""))
                if ip.is_unspecified or ip.is_global or ip.is_loopback:
                    raise nodetrust.BindRefused(f"{address}: the address the new controller "
                                                f"reaches this node at, on the LAN or fabric")
                address = str(ip)
            fp = self.store.set_owner(pem, {"name": name, "via": "handover",
                                            "paired_at": round(self.clock(), 3),
                                            "address": address or (old or {}).get("address")})
            if address:
                self._remember_address(address)
            self.store.audit("handover", by=who, controller=fp, name=name,
                             **({"from": old["fingerprint"]} if old else {}))
        self.log(f"[node] {who} handed this node to {name or 'a TandemApp'} (sha256 {fp[:16]})"
                 + (f"; {old.get('name') or old['fingerprint'][:16]} is no longer pinned" if old
                    else f"; listening on {address} in mTLS"))
        threading.Timer(0.3, self.apply_trust).start()
        return {"fingerprint": self.store.own_fingerprint(), "certificate": self.store.own_pem(),
                "owner": fp, "port": self.port}

    def start_controller(self, body, who: str) -> dict:
        """A TandemApp controller on this machine (the controller moves here, OPS-234): its
        state dir with the fleet's export and an env file, a systemd user unit, the controller
        answering /health. Its certificate (made at its first start) comes back, for the owner
        to hand the nodes to. A state dir that holds a controller's database already is
        refused: an import never lands on another fleet."""
        from proxy import appservice
        if not isinstance(body, dict) or set(body) - {"bundle", "port", "admin_token", "name",
                                                      "state_dir"}:
            raise ValueError("the body is {bundle, port, admin_token, name?, state_dir?}")
        port = body.get("port")
        if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port < 65536 or \
                port in (self.port, self.port + 1):
            raise ValueError("port is a free port from 1024 to 65535, not the agent's")
        from tandemapp.auth import MIN_TOKEN
        token = body.get("admin_token")
        if not isinstance(token, str) or len(token) < MIN_TOKEN or any(c.isspace() for c in token):
            raise ValueError(f"admin_token is the controller's admin password ({MIN_TOKEN} or "
                             f"more characters, no spaces): it listens on the LAN")
        bundle = body.get("bundle")
        if not isinstance(bundle, dict) or bundle.get("kind") != "tandemapp-export":
            raise ValueError("bundle is the fleet's export (proxy/ctlmove.py)")
        name = str(body.get("name") or appservice.UNIT)
        d = os.path.expanduser(str(body.get("state_dir") or "~/.tandemllm/app"))
        if os.path.exists(os.path.join(d, "config.sqlite3")):
            raise nodeengines.EngineError(409, "not_empty", f"{d} holds a controller's database "
                                                            f"already: pick another state dir")
        made = not os.path.exists(d)
        os.makedirs(d, mode=0o700, exist_ok=True)
        env = {"TANDEM_ADMIN_TOKEN": token}
        if self.virtual:
            # a virtual node's controller: the machine under it is not this fleet's node
            env.update(TANDEM_NODE_AGENT="off", TANDEM_LOCAL_NODE="off")
        else:
            env.update(TANDEM_NODE_AGENT=f"http://127.0.0.1:{self.port}",
                       TANDEM_NODE_SECRET=self.secret_path)
        _write_private(os.path.join(d, "app.env"),
                       "".join(f"{k}={v}\n" for k, v in env.items()))
        _write_private(os.path.join(d, "import.json"), json.dumps(bundle))
        svc = self.service(name)
        try:
            st = svc.install(appservice.Spec(host="0.0.0.0", port=port, state_dir=d,
                                             env_file=os.path.join(d, "app.env")))
        except appservice.ServiceError as exc:
            # nothing of a controller that did not come up stays: the move can run again
            if exc.kind != "port_in_use":
                tail = _journal(name)
                svc.uninstall()
                _clear(d, made)
                self.log(f"[node] the controller {name} did not come up; removed")
                raise nodeengines.EngineError(500, exc.kind, f"{exc.message}{tail}")
            _clear(d, made)
            raise nodeengines.EngineError(409, exc.kind, exc.message)
        cert = os.path.join(d, "controller", "controller.crt")
        end = time.monotonic() + 10
        while not os.path.isfile(cert) and time.monotonic() < end:
            time.sleep(0.2)
        try:
            pem = open(cert).read()
        except OSError:
            raise nodeengines.EngineError(500, "not_answering", f"{name} answers but has no "
                                                                f"certificate at {cert} yet")
        self.started_controller = {"name": name, "port": port, "state_dir": d,
                                   "pid": st.get("pid"), "at": round(self.clock(), 3)}
        self.log(f"[node] {who} started a TandemApp controller here: {name}.service on "
                 f"0.0.0.0:{port}, state {d}")
        return dict(self.started_controller, certificate=pem,
                    fingerprint=nodetrust.pem_fingerprint(pem))

    def apply_trust(self) -> None:
        """Listen as the trust store now says: loopback in mTLS once it is filled (plain with the
        secret otherwise), the paired addresses in mTLS while it is, every TLS context renewed and
        every open connection whose certificate is no longer pinned closed."""
        with self._trust:
            ready = self.store.ready()
            ctx = nodetrust.server_context(self.store) if ready else None
            want = [h for h in self.hosts + self.store.listen_addrs()]
            want = list(dict.fromkeys(str(nodetrust.parse_ip(h)) for h in want))
            keep = []
            for srv in self.servers:
                ip = srv.server_address[0]
                if nodetrust.parse_ip(ip).is_loopback:
                    srv.mode, srv.ctx = ("mtls", ctx) if ready else ("secret", None)
                elif ready and ip in want:
                    srv.ctx = ctx
                else:
                    self.log(f"[node] no longer listening on {ip}: the trust store has "
                             f"{self.store.missing()}")
                    self.listening.pop(ip, None)
                    _stop(srv)
                    continue
                self.listening[ip] = {"mode": srv.mode, "port": srv.server_address[1]}
                keep.append(srv)
                srv.drop_unpinned()
            self.servers = keep
            have = {srv.server_address[0] for srv in keep}
            for ip in want:
                if ip in have or nodetrust.parse_ip(ip).is_loopback or not ready:
                    continue
                try:
                    srv = Server((ip, self.port), self, "mtls", ctx)
                except OSError as exc:
                    self.log(f"[node] cannot listen on {ip}:{self.port}: {exc}")
                    continue
                self.servers.append(srv)
                self.listening[ip] = {"mode": "mtls", "port": srv.server_address[1]}
                threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.25},
                                 daemon=True).start()
                self.log(f"[node] listening on {ip}:{self.port} (mtls)")
        self.advertise()

    # ------------------------------------------------------------------ logs
    def log_lines(self, level: str, q: str | None, source: str, limit: int) -> list[dict]:
        out = []
        if source in ("all", "agent") and self.logbuf is not None:
            for e in self.logbuf.lines(level=TO_BUF.get(level, level), grep=q, limit=limit):
                out.append(_line(e, "agent"))
        for p in self.local.engines():
            if source not in ("all", p.id):
                continue
            for e in p.logs(level, q, limit):
                out.append(dict(e, source=e["source"] if e["source"] == nodesource.ACCESS
                                else p.id))
        out = [x for x in out if x.get("t") is not None]
        out.sort(key=lambda x: x["t"])
        return out[-limit:]


def _journal(unit: str) -> str:
    """The last line a failed controller unit printed, for the error."""
    try:
        r = subprocess.run(["journalctl", "--user", "-u", f"{unit}.service", "-n", "8",
                            "--no-pager", "-o", "cat"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    lines = [x for x in r.stdout.splitlines() if x.strip()]
    return f"; it said: {lines[-1][:200]}" if lines else ""


def _clear(d: str, made: bool) -> None:
    """A state dir a controller start made (all of it), or the files it put in one given."""
    import shutil
    if made:
        shutil.rmtree(d, ignore_errors=True)
        return
    for name in ("import.json", "app.env", "config.sqlite3", "config.sqlite3-wal",
                 "config.sqlite3-shm", "ledger.sqlite3", "ledger.sqlite3-wal", "ledger.sqlite3-shm",
                 "fleet.sqlite3", "fleet.sqlite3-wal", "fleet.sqlite3-shm", "secrets.key"):
        try:
            os.remove(os.path.join(d, name))
        except FileNotFoundError:
            pass
    for sub in ("controller", "conductor", "logs", "packages"):
        shutil.rmtree(os.path.join(d, sub), ignore_errors=True)


def _write_private(path: str, text: str) -> None:
    fd = os.open(path + ".part", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(path + ".part", path)


def _line(e: dict, source: str) -> dict:
    return {"t": nodesource._epoch(e.get("ts")), "level": FROM_BUF.get(e.get("level"),
                                                                      e.get("level")),
            "source": nodesource._source(e, source), "msg": str(e.get("msg") or "")}


# ====================================================================== the HTTP side

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "TandemNode"
    server: "Server"

    def log_message(self, fmt, *args):
        pass

    # ------------------------------------------------------------------ plumbing
    @property
    def agent(self) -> Agent:
        return self.server.agent

    def _send(self, code: int, body: bytes, ctype: str = "application/json",
              extra: tuple = ()) -> None:
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in extra:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
            self.close_connection = True

    def _json(self, code: int, doc, extra: tuple = ()) -> None:
        self._send(code, json.dumps(doc).encode(), extra=extra)

    def _err(self, code: int, kind: str, message: str) -> None:
        self._json(code, {"error": {"type": kind, "message": message}})

    def _cert(self) -> bytes | None:
        try:
            return self.connection.getpeercert(binary_form=True)
        except (AttributeError, ValueError):
            return None

    def _role(self) -> str | None:
        """secret, self, owner, peer (proxy/nodetrust.py Store.role), or None."""
        if not isinstance(self.connection, ssl.SSLSocket):
            return "secret" if self.server.mode == "secret" else None
        return self.agent.store.role(self._cert())

    def _who(self) -> str:
        role = self._role()
        if role in ("owner", "peer"):
            der = self._cert()
            return f"{role}:" + (nodetrust.fingerprint(der)[:16] if der else "?")
        return role or "cert:?"

    def _allowed(self) -> bool:
        if isinstance(self.connection, ssl.SSLSocket):
            if self._role() is not None:     # the handshake checked it against the pins
                return True
            self.agent.log(f"[node] refused {self.command} {self.path.split('?')[0]} from "
                           f"{self.client_address[0]}: its certificate is no longer pinned")
            self._err(401, "unauthorized", "this certificate is no longer pinned here")
            self.close_connection = True
            return False
        if self.server.mode != "secret":
            self.close_connection = True     # plain HTTP on a port that has turned to mTLS
            return False
        got = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
        if got and hmac.compare_digest(got.encode(), self.agent.secret.encode()):
            return True
        self.agent.log(f"[node] refused {self.command} {self.path.split('?')[0]} from "
                       f"{self.client_address[0]}: {'a wrong' if got else 'no'} secret")
        self._err(401, "unauthorized", "this agent answers its loopback secret or a paired "
                                       "certificate only")
        self.close_connection = True
        return False

    def _may(self, method: str, head: str) -> bool:
        """Another pinned node (a link test's peer) reaches its identity and the link test only."""
        if self._role() != "peer" or (method, head) in PEER_ROUTES:
            return True
        self._err(403, "forbidden", "a pinned peer node reaches /node/identity, /node/linktest "
                                    "and a weights receive only")
        return False

    def _body(self, cap: int = MAX_JSON):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > cap:
            raise ValueError(f"a body of at most {cap} bytes")
        raw = self.rfile.read(n) if n else b""
        return raw

    def _audit(self, what: str, status: int) -> None:
        self.agent.log(f"[node] {self._who()} {what} -> {status}")

    def _gone(self) -> bool:
        if isinstance(self.connection, ssl.SSLSocket):
            return False                     # a closed TLS reader shows at the next write
        try:
            ready, _, _ = select.select([self.connection], [], [], 0)
            return bool(ready) and not self.connection.recv(1, socket.MSG_PEEK)
        except (OSError, ValueError):
            return True

    def _stream_head(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    # ------------------------------------------------------------------ verbs
    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_DELETE(self):
        self._route("DELETE")

    def do_PUT(self):
        self._route("PUT")

    def _route(self, method: str) -> None:
        if not self._allowed():
            return
        u = urllib.parse.urlsplit(self.path)
        path = u.path.rstrip("/")
        q = {k: v[-1] for k, v in urllib.parse.parse_qs(u.query).items()}
        parts = [urllib.parse.unquote(p) for p in path.split("/") if p]
        try:
            if parts[:1] != ["node"] or len(parts) < 2:
                return self._err(404, "not_found", f"no route {method} {path}")
            head = parts[1]
            if not self._may(method, head):
                return
            if head == "pair":
                return self._pair(method, parts[2:])
            if head == "peers":
                return self._peers(method, parts[2:])
            if method == "GET" and parts == ["node", "identity"]:
                return self._json(200, self.agent.identity())
            if method == "GET" and parts == ["node", "inventory"]:
                return self._json(200, self.agent.inventory())
            if method == "GET" and parts == ["node", "metrics"]:
                return self._metrics(q)
            if head == "engines":
                return self._engines(method, parts[2:], u.query)
            if head == "maintenance":
                return self._maintenance(method, parts[2:])
            if head == "weights":
                return self._weights(method, parts[2:], q)
            if method == "GET" and parts == ["node", "logs"]:
                return self._logs(q)
            if method == "GET" and parts == ["node", "preflight"]:
                return self._json(200, self.agent.preflight())
            if method == "PUT" and parts == ["node", "fleet"]:
                if self._role() != "secret":
                    return self._err(403, "forbidden", "only the controller on this machine "
                                                       "(the loopback secret) claims this node")
                doc = self.agent.claim(json.loads(self._body() or b"null"))
                self._audit("fleet claim", 200)
                return self._json(200, doc)
            if head == "update":
                return self._update(method, parts[2:], q)
            if method == "PUT" and parts == ["node", "owner"]:
                role = self._role()
                if role not in ("owner",) + LOCAL_ROLES:
                    return self._err(403, "forbidden", "the owner hands this node over")
                doc = self.agent.handover(json.loads(self._body() or b"null"), role,
                                          self._who())
                self._audit("handover", 200)
                return self._json(200, doc)
            if head == "controller":
                if self._role() != "owner":
                    return self._err(403, "forbidden", "the owner starts a controller here")
                if method == "GET" and len(parts) == 2:
                    return self._json(200, {"controller": self.agent.started_controller})
                if method == "POST" and len(parts) == 2:
                    doc = self.agent.start_controller(json.loads(self._body() or b"null"),
                                                      self._who())
                    self._audit(f"start controller {doc['name']}", 200)
                    return self._json(200, doc)
            if head == "linktest":
                return self._linktest(method, parts[2:], q)
            if head == "setup":
                return self._setup(method, "/".join(parts[2:]), u.query)
            return self._err(404, "not_found", f"no route {method} {path}")
        except ValueError as exc:
            return self._err(400, "bad_request", str(exc))
        except nodeengines.EngineError as exc:
            return self._json(exc.status, exc.body())
        except nodeupdate.UpdateError as exc:
            return self._json(exc.status, exc.body())
        except nodeweights.WeightsError as exc:
            return self._json(exc.status, exc.body())
        except (nodetrust.TrustError, nodetrust.BindRefused) as exc:
            return self._err(400, "bad_request", str(exc))

    # ------------------------------------------------------------------ trust (SEC-4)
    def _pair(self, method: str, rest: list[str]) -> None:
        a, role = self.agent, self._role()
        local = role in LOCAL_ROLES
        if method == "GET" and not rest and (local or role == "owner"):
            return self._json(200, {"fingerprint": a.store.own_fingerprint(),
                                    "certificate": a.store.own_pem(),
                                    "owner": a.store.owner(),
                                    "window": a.window_view(with_code=local),
                                    "listen": a.store.listen_addrs(),
                                    "audit": a.store.audit_rows(20) if local else []})
        if method == "DELETE" and not rest and (local or role == "owner"):
            who = self._who()
            was = a.release(who)
            a.log(f"[node] {who} release -> 200")
            return self._json(200, {"released": was is not None})
        if not local:
            return self._err(403, "forbidden", "pairing is opened on the node itself (tandem "
                                               "node pair)")
        if method == "POST" and not rest:
            doc = json.loads(self._body() or b"{}")
            try:
                return self._json(200, a.open_window(doc, self._who()))
            except OSError as exc:
                return self._err(409, "address_in_use", f"the pairing window cannot listen "
                                                        f"there: {exc.strerror or exc}")
        if method == "DELETE" and rest == ["window"]:
            a.close_window()
            return self._json(200, {"closed": True})
        if method == "POST" and rest == ["reload"]:
            a.apply_trust()
            return self._json(200, {"listen": [{"address": ip, **m}
                                               for ip, m in a.listening.items()]})
        return self._err(404, "not_found", f"no route {method} /node/pair/{'/'.join(rest)}")

    def _peers(self, method: str, rest: list[str]) -> None:
        a = self.agent
        if self._role() not in LOCAL_ROLES + ("owner",):
            return self._err(403, "forbidden", "the owner pins peers")
        if len(rest) != 1 or not rest[0].replace("-", "").isalnum() or len(rest[0]) > 40:
            raise ValueError("a peer is /node/peers/<node id>")
        name = "node-" + rest[0]
        if method == "PUT":
            doc = json.loads(self._body() or b"{}")
            fp = a.store.pin(name, str((doc or {}).get("certificate") or ""))
            a.store.audit("pin_peer", peer=rest[0], fingerprint=fp, by=self._who())
        elif method == "DELETE":
            a.store.unpin(name)
            a.store.audit("unpin_peer", peer=rest[0], by=self._who())
        else:
            return self._err(405, "method_not_allowed", "PUT or DELETE /node/peers/<id>")
        self._audit(f"{method} peer {rest[0]}", 200)
        threading.Timer(0.1, a.apply_trust).start()
        return self._json(200, {"peer": rest[0]})

    # ------------------------------------------------------------------ metrics
    def _metrics(self, q: dict) -> None:
        s = self.agent.sampler
        if q.get("once") in ("1", "true"):
            return self._json(200, s.latest or self.agent._sample(self.agent.clock()))
        sub = s.subscribe()
        try:
            self._stream_head()
            last = time.monotonic()
            while True:
                items = sub.take(PING_S)
                if items:
                    for it in items:
                        self.wfile.write(b"event: sample\ndata: " + json.dumps(it).encode()
                                         + b"\n\n")
                    self.wfile.flush()
                    last = time.monotonic()
                elif time.monotonic() - last >= PING_S:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    last = time.monotonic()
                if self._gone() or sub.closed:
                    break
        except (BrokenPipeError, ConnectionResetError, ssl.SSLError, OSError):
            pass
        finally:
            s.unsubscribe(sub)

    # ------------------------------------------------------------------ engines
    def _engines(self, method: str, rest: list[str], query: str) -> None:
        eng = self.agent.engines
        if method == "GET" and not rest:
            return self._json(200, {"engines": eng.view()})
        if not rest:
            return self._err(405, "method_not_allowed", "GET /node/engines")
        e = eng.get(rest[0])
        if method == "GET" and len(rest) == 1:
            return self._json(200, eng.view_one(e))
        if method == "POST" and len(rest) == 2:
            job = eng.act(e.id, rest[1], self._who())
            self._audit(f"{rest[1]} engine {e.id}", 202)
            return self._json(202, job)
        if method == "GET":
            return self._relay_get(e, "/" + "/".join(rest[1:]), query)
        return self._err(404, "not_found", f"no route {method} /node/engines/{'/'.join(rest)}")

    def _maintenance(self, method: str, rest: list[str]) -> None:
        m = self.agent.maintenance
        if method == "GET" and not rest:
            return self._json(200, m.view())
        if method == "POST" and len(rest) == 1:
            job = m.act(rest[0], self._who())
            self._audit(f"{rest[0]} the node{' (simulated)' if job['simulated'] else ''}", 202)
            return self._json(202, job)
        return self._err(404, "not_found", f"no route {method} /node/maintenance/"
                                           f"{'/'.join(rest)}")

    def _update(self, method: str, rest: list[str], q: dict) -> None:
        up = self.agent.updater
        if self._role() not in ("owner",) + LOCAL_ROLES:
            return self._err(403, "forbidden", "the owner updates this node")
        if method == "GET" and not rest:
            return self._json(200, up.view())
        if method == "PUT" and rest == ["package"]:
            doc = up.receive(q.get("name") or "", q.get("sha256") or "",
                             self._body(nodeupdate.PKG_MAX))
            self._audit(f"update package {doc['name']}", 200)
            return self._json(200, doc)
        if method == "POST" and not rest:
            job = up.start(json.loads(self._body() or b"null"), self._who())
            self._audit(f"update to {job['version']}", 202)
            return self._json(202, job)
        return self._err(404, "not_found", f"no route {method} /node/update/{'/'.join(rest)}")

    def _relay_get(self, e, path: str, query: str) -> None:
        try:
            st, ctype, conn, r = self.agent.engines.relay(e.id, path, query)
        except OSError as exc:
            return self._err(503, "engine_down", f"{e.id} does not answer on :{e.port} "
                                                 f"({type(exc).__name__})")
        self._pass(st, ctype, conn, r)

    def _pass(self, st: int, ctype: str, conn, r, extra: tuple = ()) -> None:
        """An upstream answer passed on: whole with its length, or as it arrives (a stream)."""
        try:
            if "text/event-stream" not in ctype:
                return self._send(st, r.read(), ctype, extra)
            self.send_response(st)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            for k, v in extra:
                self.send_header(k, v)
            self.end_headers()
            self.close_connection = True

            def write(piece: bytes) -> None:
                self.wfile.write(piece)
                self.wfile.flush()
            nodeclient.pipe(r, conn, write, self._gone)
        except (BrokenPipeError, ConnectionResetError, ssl.SSLError, OSError):
            self.close_connection = True
        finally:
            conn.close()

    # ------------------------------------------------------------------ setup and weights
    def _setup_call(self, method: str, target: str, body: bytes | None, quiet: bool = False):
        """The setup engine's answer to `target` (its status responder's while it restarts):
        (status, content type, connection, response), or an error already sent (None; `quiet`:
        none sent)."""
        e = self.agent.setup_engine()
        if e is None:
            if not quiet:
                self._err(503, "no_engine", "no TandemLLM engine on this node serves the Setup "
                                            "Guide")
            return None
        h = {"Accept": self.headers.get("Accept") or "application/json"}
        for k in ("Content-Type", "Last-Event-ID"):
            if self.headers.get(k):
                h[k] = self.headers[k]
        for port in (e.port, e.status_port):
            if port is None:
                continue
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
            try:
                conn.request(method, target, body=body, headers=h)
                sock = conn.sock
                r = conn.getresponse()
                r.sock = sock
                return r.status, r.getheader("Content-Type") or "application/json", conn, r
            except OSError:
                conn.close()
                continue
        if not quiet:
            self._err(503, "engine_down", f"the engine on :{e.port} does not answer")
        return None

    def _setup(self, method: str, sub: str, query: str) -> None:
        body = self._body() if method == "POST" else None
        got = self._setup_call(method, f"/v1/setup/{sub}" + (f"?{query}" if query else ""), body)
        if got is None:
            return
        st, ctype, conn, r = got
        if method != "GET":
            self._audit(f"{method} setup/{sub}", st)
        extra = tuple((k, r.getheader(k)) for k in ("Retry-After",) if r.getheader(k))
        self._pass(st, ctype, conn, r, extra)

    def _setup_weights(self) -> dict:
        """The engine's Setup Guide weights with content ids; {} when no engine serves them."""
        got = self._setup_call("GET", "/v1/setup/weights", None, quiet=True)
        if got is None:
            return {"setup": "no TandemLLM engine on this node serves the Setup Guide"}
        st, _, conn, r = got
        try:
            doc = json.loads(r.read() or b"null")
        except ValueError:
            doc = None
        finally:
            conn.close()
        if st != 200 or not isinstance(doc, dict):
            return {"setup": f"the engine's Setup Guide answered HTTP {st}"}
        for w in doc.get("weights") or []:
            cid, basis = content_id(w.get("dir") or "")
            w["content_id"], w["content_basis"] = cid, basis
            w.setdefault("source", "setup")
        return doc

    def _pack_dir(self, name: str) -> str:
        """A pack's folder: the store's, else the Setup Guide's of that profile."""
        d = self.agent.weights.pack_dir(name)
        if d is not None:
            return d
        for w in self._setup_weights().get("weights") or []:
            if w.get("profile") == name and w.get("dir") and os.path.isdir(w["dir"]):
                return w["dir"]
        raise nodeweights.WeightsError(404, "not_found", f"no pack {name} on this node")

    def _weights(self, method: str, rest: list[str], q: dict) -> None:
        store = self.agent.weights
        role = self._role()
        if role == "peer":
            # a peer node (the source of a copy) reaches an open receive with its token only
            if not (rest[:1] == ["receive"] and len(rest) >= 3 and method in ("PUT", "POST")):
                return self._err(403, "forbidden", "a peer node reaches a weights receive only")
            store.receive(rest[1], self.headers.get("X-Tandem-Copy-Token") or "")
        if rest[:1] == ["receive"]:
            return self._receive(method, rest[1:])
        if rest[:1] == ["send"]:
            return self._send_weights(method, rest[1:])
        if method == "GET" and not rest:
            doc = self._setup_weights()
            setup = [dict(w, source=w.get("source") or "setup") for w in doc.get("weights") or []]
            out = {k: v for k, v in doc.items() if k != "weights"}
            out.update(weights=setup + store.packs(),
                       store={"dir": store.root, "free_gib": round(store.free() / nodeweights.GIB,
                                                                   2)})
            return self._json(200, out)
        if method == "GET" and len(rest) == 2 and rest[1] == "manifest":
            d = self._pack_dir(nodeweights.check_name(rest[0]))
            m = nodeweights.manifest(d)
            cid, basis = content_id(d)
            return self._json(200, {"profile": rest[0], "dir": d, "content_id": cid,
                                    "content_basis": basis, **m})
        if method == "GET" and len(rest) >= 3 and rest[1] == "files":
            d = self._pack_dir(nodeweights.check_name(rest[0]))
            rel = nodeweights.check_rel("/".join(rest[2:]))
            path = os.path.realpath(os.path.join(d, *rel.split("/")))
            if not os.path.isfile(path) or rel not in nodeweights.files_of(d):
                return self._err(404, "not_found", f"no file {rel} in {rest[0]}")
            size = os.path.getsize(path)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with open(path, "rb") as f:
                try:
                    shutil.copyfileobj(f, self.wfile, nodeweights.CHUNK)
                except (BrokenPipeError, ConnectionResetError, ssl.SSLError, OSError):
                    self.close_connection = True
            return
        if method == "DELETE" and len(rest) == 1 and store.pack_dir(
                nodeweights.check_name(rest[0])) is not None:
            dry = q.get("dry_run") in ("1", "true")
            doc = store.remove(rest[0], dry, q.get("confirm") in ("1", "true"))
            self._audit(f"remove weights {rest[0]} from the store{' (dry run)' if dry else ''}",
                        200)
            return self._json(200, doc)
        if method == "DELETE" and len(rest) == 1:
            dry = q.get("dry_run") in ("1", "true")
            body = {"profiles": [rest[0]], "dry_run": dry}
            if not dry:
                body["confirm"] = q.get("confirm") in ("1", "true")
            got = self._setup_call("POST", "/v1/setup/remove", json.dumps(body).encode())
            if got is None:
                return
            st, ctype, conn, r = got
            self._audit(f"remove weights {rest[0]}{' (dry run)' if dry else ''}", st)
            return self._pass(st, ctype, conn, r)
        return self._err(404, "not_found", f"no route {method} /node/weights/{'/'.join(rest)}")

    def _receive(self, method: str, rest: list[str]) -> None:
        store = self.agent.weights
        if method == "POST" and not rest:
            if self._role() not in ("owner",) + LOCAL_ROLES:
                return self._err(403, "forbidden", "the owner opens a receive")
            doc = json.loads(self._body() or b"null")
            rcv = store.open_receive(doc, (doc or {}).get("from"))
            self._audit(f"receive {rcv.profile} as copy {rcv.copy}", 200)
            return self._json(200, dict(rcv.view(), token=rcv.token, dir=rcv.dir,
                                        free_gib=round(store.free() / nodeweights.GIB, 2)))
        if not rest:
            return self._err(404, "not_found", "a receive is /node/weights/receive/<copy>")
        rcv = store.receive(rest[0])
        if method == "GET" and len(rest) == 1:
            return self._json(200, rcv.view())
        if method == "DELETE" and len(rest) == 1:
            rcv.abort()
            self._audit(f"abort copy {rcv.copy}", 200)
            return self._json(200, rcv.view())
        if method == "PUT" and len(rest) >= 2:
            try:
                n = int(self.headers.get("Content-Length") or -1)
            except ValueError:
                n = -1
            if n < 0:
                raise ValueError("a file is sent with its Content-Length")
            try:
                return self._json(200, rcv.put("/".join(rest[1:]), self.rfile, n))
            except nodeweights.WeightsError:
                self.close_connection = True             # what is left of the body is unread
                raise
        if method == "POST" and rest[1:] == ["commit"]:
            try:
                doc = rcv.commit()
            except nodeweights.WeightsError as exc:
                if exc.kind != "incomplete":
                    rcv.abort()
                raise
            self._audit(f"copy {rcv.copy}: {rcv.profile} in place", 200)
            return self._json(200, doc)
        return self._err(404, "not_found", f"no route {method} /node/weights/receive/"
                                           f"{'/'.join(rest)}")

    def _send_weights(self, method: str, rest: list[str]) -> None:
        store = self.agent.weights
        if self._role() not in ("owner",) + LOCAL_ROLES:
            return self._err(403, "forbidden", "the owner sends a pack")
        if method == "POST" and not rest:
            doc = json.loads(self._body() or b"null")
            if not isinstance(doc, dict) or not all(isinstance(doc.get(k), str) for k in
                                                    ("copy", "profile", "to", "token")):
                raise ValueError("the body is {copy, profile, to, token}")
            d = self._pack_dir(nodeweights.check_name(doc["profile"]))
            files = nodeweights.manifest(d)["files"]
            job = store.start_send(doc["copy"], d, files, self._peer_client(doc["to"]),
                                   doc["token"], self._who())
            self._audit(f"send {doc['profile']} to {doc['to']} as copy {doc['copy']}", 202)
            return self._json(202, job)
        if len(rest) == 1 and method == "GET":
            job = store.sends.get(rest[0])
            if job is None:
                return self._err(404, "not_found", f"no send {rest[0]}")
            return self._json(200, job)
        if len(rest) == 1 and method == "DELETE":
            return self._json(200, store.stop_send(rest[0]))
        return self._err(404, "not_found", f"no route {method} /node/weights/send/"
                                           f"{'/'.join(rest)}")

    # ------------------------------------------------------------------ logs
    def _logs(self, q: dict) -> None:
        level = q.get("level") or "info"
        if level not in LEVELS:
            raise ValueError(f"level is {', '.join(LEVELS)}")
        grep = q.get("q") or None
        if grep is not None and len(grep) > 128:
            raise ValueError("q is at most 128 characters")
        source = q.get("source") or "all"
        names = {p.id for p in self.agent.local.engines()}
        if source not in ("all", "agent") and source not in names:
            raise ValueError(f"source is all, agent or an engine ({', '.join(sorted(names))})")
        try:
            limit = int(q.get("backlog") or q.get("limit") or 200)
        except ValueError:
            limit = -1
        if not 0 <= limit <= 5000:
            raise ValueError("backlog is 0 to 5000")
        lines = self.agent.log_lines(level, grep, source, limit) if limit else []
        if q.get("follow", "1") in ("0", "false", "no"):
            return self._json(200, {"lines": lines})
        out: queue.Queue = queue.Queue(maxsize=2000)
        stop = threading.Event()
        conns: list = []
        sub = None
        buf = self.agent.logbuf
        if source in ("all", "agent") and buf is not None:
            sub = buf.subscribe(TO_BUF.get(level, level), grep)
            if sub is None:
                return self._err(429, "too_many", "too many log streams at once")
        for e in self.agent.engines.list:
            if e.kind == "tandemllm" and source in ("all", e.id):
                threading.Thread(target=self._follow_engine, daemon=True,
                                 args=(e, level, grep, out, stop, conns)).start()
        try:
            self._stream_head()
            for x in lines:
                self.wfile.write(b"event: log\ndata: " + json.dumps(x).encode() + b"\n\n")
            self.wfile.flush()
            last = time.monotonic()
            while not self._gone():
                got = []
                if sub is not None:
                    items, _ = sub.take(0.25)
                    got += [_line(e, "agent") for e in items]
                try:
                    while True:
                        got.append(out.get(timeout=0 if got or sub is not None else 0.25))
                except queue.Empty:
                    pass
                for x in got:
                    self.wfile.write(b"event: log\ndata: " + json.dumps(x).encode() + b"\n\n")
                if got:
                    self.wfile.flush()
                    last = time.monotonic()
                elif time.monotonic() - last >= PING_S:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    last = time.monotonic()
        except (BrokenPipeError, ConnectionResetError, ssl.SSLError, OSError):
            pass
        finally:
            stop.set()
            for c in conns:
                try:
                    c.close()
                except OSError:
                    pass
            if sub is not None:
                buf.unsubscribe(sub)

    @staticmethod
    def _follow_engine(e, level, grep, out, stop, conns) -> None:
        qs = {"follow": "1", "backlog": "0", "level": TO_BUF.get(level, level)}
        if grep:
            qs["grep"] = grep
        try:
            st, _, conn, r = nodeengines.open_get(e.port, "/v1/dashboard/logs?"
                                                  + urllib.parse.urlencode(qs), 3600)
        except OSError:
            return
        conns.append(conn)
        try:
            data = []
            while not stop.is_set():
                line = r.readline()
                if not line:
                    return
                line = line.decode("utf-8", "replace").rstrip("\r\n")
                if line.startswith("data:"):
                    data.append(line[5:].lstrip())
                elif not line and data:
                    try:
                        ent = json.loads("\n".join(data))
                    except ValueError:
                        ent = None
                    data = []
                    if isinstance(ent, dict) and "seq" in ent:
                        try:
                            out.put_nowait(_line(ent, e.id))
                        except queue.Full:
                            pass
        except (OSError, ValueError):
            return
        finally:
            conn.close()

    # ------------------------------------------------------------------ linktest
    def _linktest(self, method: str, rest: list[str], q: dict) -> None:
        a = self.agent
        if method == "GET" and rest == ["echo"]:
            return self._json(200, {"t": a.clock()})
        if method == "GET" and rest == ["info"]:
            return self._json(200, {"id": a.node_id(),
                                    "interfaces": linktest.interfaces(a.reader, a.addrs())})
        if method == "POST" and rest == ["sink"]:
            n = int(self.headers.get("Content-Length") or 0)
            if not 0 <= n <= linktest.MAX_BYTES:
                raise ValueError(f"a sink body is at most {linktest.MAX_BYTES} bytes")
            t0, left = time.perf_counter(), n
            while left:
                got = self.rfile.read(min(left, linktest.CHUNK))
                if not got:
                    break
                left -= len(got)
            return self._json(200, {"bytes": n - left, "s": round(time.perf_counter() - t0, 4)})
        if method == "POST" and not rest:
            doc = json.loads(self._body() or b"{}")
            if not isinstance(doc, dict) or not isinstance(doc.get("peer"), str):
                raise ValueError("the body is {peer: the peer agent's URL, bytes?, streams?, "
                                 "rtt_n?}")
            nbytes = int(doc.get("bytes") or linktest.DEFAULT_BYTES)
            streams = int(doc.get("streams") or linktest.DEFAULT_STREAMS)
            rtt_n = int(doc.get("rtt_n") or linktest.DEFAULT_RTT_N)
            if not (linktest.CHUNK <= nbytes <= linktest.MAX_BYTES and 1 <= streams <= 16
                    and 1 <= rtt_n <= 1000):
                raise ValueError("bytes 1 MiB to 8 GiB, streams 1 to 16, rtt_n 1 to 1000")
            client = self._peer_client(doc["peer"])
            res = linktest.run(client, linktest.interfaces(a.reader, a.addrs()), nbytes=nbytes,
                               streams=streams, rtt_n=rtt_n, ping=a.ping)
            self._audit(f"linktest to {doc['peer']}", 200)
            return self._json(200, res)
        return self._err(404, "not_found", f"no route {method} /node/linktest/{'/'.join(rest)}")

    def _peer_client(self, url: str) -> nodeclient.Client:
        u = urllib.parse.urlsplit(url)
        if u.scheme == "https":
            try:
                ctx = nodetrust.store_client_context(self.agent.store)
            except nodetrust.TrustError as exc:
                raise ValueError(f"an https peer needs this node's paired certificate: {exc}")
            return nodeclient.Client(url, ssl_context=ctx, timeout=30)
        try:
            loop = nodetrust.parse_ip(u.hostname or "").is_loopback
        except nodetrust.BindRefused:
            loop = False
        if u.scheme != "http" or not loop:
            raise ValueError("a peer is https://<address>:<port> (mTLS), or http:// on loopback "
                             "(an agent on this box, the same secret)")
        return nodeclient.Client(url, secret=self.agent.secret, timeout=30)


class Server(ThreadingHTTPServer):
    """One listening address. With TLS the handshake runs on the request's own thread, so a slow or
    refused client never holds the accept loop."""

    daemon_threads = True
    request_queue_size = 64
    allow_reuse_address = True

    handler = None

    def __init__(self, addr, agent: Agent, mode: str, ctx: ssl.SSLContext | None):
        self.agent, self.mode, self.ctx = agent, mode, ctx
        self.live: set = set()
        self._live = threading.Lock()
        if ":" in addr[0]:
            self.address_family = socket.AF_INET6
        super().__init__(addr, self.handler or Handler)

    def finish_request(self, request, client_address):
        try:                         # a head and its body are two writes: no 40 ms Nagle stall
            request.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        ctx = self.ctx
        if ctx is None:
            return super().finish_request(request, client_address)
        try:
            request.settimeout(10.0)
            tls = ctx.wrap_socket(request, server_side=True)
            tls.settimeout(None)
        except (ssl.SSLError, OSError) as exc:
            reason = getattr(exc, "reason", None) or type(exc).__name__
            self.agent.log(f"[node] refused TLS from {client_address[0]}: {reason}")
            return
        with self._live:
            self.live.add(tls)
        try:
            self.RequestHandlerClass(tls, client_address, self)
        finally:
            with self._live:
                self.live.discard(tls)
            try:
                tls.close()
            except OSError:
                pass

    def drop_unpinned(self) -> None:
        """Close every open TLS connection whose certificate the store no longer pins (a
        released or replaced owner's metrics stream ends here, not at its next request)."""
        with self._live:
            live = list(self.live)
        for tls in live:
            try:
                der = tls.getpeercert(binary_form=True)
            except (OSError, ValueError):
                der = None
            if der is not None and self.agent.store.role(der) is None:
                try:
                    tls.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass


class PairHandler(Handler):
    """The pairing window's routes, and nothing else: POST /node/pair/1, /2, /abandon."""

    server_version = "TandemNode-pair"

    def _route(self, method: str) -> None:
        srv: PairServer = self.server        # type: ignore[assignment]
        path = urllib.parse.urlsplit(self.path).path.rstrip("/")
        step = {"/node/pair/1": "1", "/node/pair/2": "2", "/node/pair/abandon": "abandon"}
        if method != "POST" or path not in step:
            return self._err(404, "not_found", "a pairing window answers the pairing "
                                               "protocol only")
        w = srv.window
        try:
            doc = json.loads(self._body(64 * 1024) or b"{}")
            if step[path] == "1":
                return self._json(200, w.step1(doc))
            if step[path] == "abandon":
                w.abandon(doc)
                return self._json(200, {"abandoned": True})
            pem, name = w.step2(doc)
            return self._json(200, self.agent.paired(w, pem, name))
        except pairing.PairError as exc:
            self.agent.log(f"[node] pairing from {self.client_address[0]}: {exc.kind} "
                           f"({w.tries_left} tries left)")
            return self._json(exc.status, exc.body())
        except (ValueError, nodetrust.TrustError) as exc:
            return self._err(400, "bad_request", str(exc))


class PairServer(Server):
    """The pairing window's listener: TLS with this node's certificate, no client certificate
    asked (the protocol binds both certificates to the code)."""

    handler = PairHandler

    def __init__(self, addr, agent: Agent, window: pairing.Window):
        self.window = window
        super().__init__(addr, agent, "pair", nodetrust.pairing_context(agent.store))


def _stop(srv) -> None:
    """A listener stopped from any thread but its own (shutdown waits for its loop)."""
    def go():
        try:
            srv.shutdown()
        finally:
            srv.server_close()
    threading.Thread(target=go, daemon=True).start()


def listen(agent: Agent, hosts: list[str], port: int) -> list[Server]:
    """A server per address, each in the mode the bind rule gives it (BindRefused otherwise,
    before anything listens): `hosts`, then the addresses pairing listened on while the trust
    store is filled. The first one's port is the agent's port (0 picks one)."""
    extra = agent.store.listen_addrs() if agent.store.ready() else []
    want = list(dict.fromkeys(str(nodetrust.parse_ip(h)) for h in list(hosts) + extra))
    modes = [(h, nodetrust.listen_mode(h, agent.store)) for h in want]
    servers = []
    for h, mode in modes:
        ctx = nodetrust.server_context(agent.store) if mode == "mtls" else None
        srv = Server((h, port), agent, mode, ctx)
        port = port or srv.server_address[1]
        agent.listening[h] = {"mode": mode, "port": srv.server_address[1]}
        servers.append(srv)
    agent.servers, agent.port = servers, port
    agent.hosts = [str(nodetrust.parse_ip(h)) for h in hosts]
    return servers


# ====================================================================== the command

COMMANDS = ("serve", "check", "unit", "pair", "takeover", "status", "reset", "adopt")


def parser() -> argparse.ArgumentParser:
    env = os.environ
    ap = argparse.ArgumentParser(prog="tandem-node", description=__doc__.split("\n")[0])
    ap.add_argument("--host", action="append", default=None,
                    help="an address to listen on (repeatable): loopback until pairing, any other "
                         "only with mTLS; never 0.0.0.0 or a WAN address (TANDEM_NODE_HOST, "
                         "default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=int(env.get("TANDEM_NODE_PORT", DEFAULT_PORT)))
    ap.add_argument("--state-dir", default=env.get("TANDEM_NODE_DIR", DEFAULT_DIR),
                    help="the secret, the trust store, engines.json, the engines' logs")
    ap.add_argument("--tandem-dir", default=env.get("TANDEM_DIR") or None,
                    help="an install.sh install: its engine is the default engine")
    ap.add_argument("--virtual", default=env.get("TANDEM_NODE_VIRTUAL") or None,
                    help="a second agent on this machine acting as another node (OPS-197): its "
                         "own node id and hostname suffix")
    ap.add_argument("--weights-dir", default=env.get("TANDEM_NODE_WEIGHTS") or None,
                    help="the weights store a copy from another node lands in "
                         "(TANDEM_NODE_WEIGHTS, default <state dir>/weights)")
    ap.add_argument("--mdns", choices=("on", "off"),
                    default=env.get("TANDEM_NODE_MDNS", "on").lower() or "on",
                    help="advertise _tandemnode._tcp on the LAN addresses the bind rule allows "
                         "(TANDEM_NODE_MDNS, default on)")
    ap.add_argument("--address", default=None,
                    help="pair, takeover, adopt: the address TandemApp reaches this node at "
                         "(default: the default route's source address)")
    ap.add_argument("--pair-port", type=int, default=None,
                    help="pair, takeover: the pairing window's port (default: --port + 1)")
    ap.add_argument("--takeover", action="store_true",
                    help="pair, adopt: move a node another TandemApp owns to this one")
    ap.add_argument("--name", default="", help="adopt: the controller's name")
    ap.add_argument("--keys", action="store_true", help="reset: a new key and certificate too")
    ap.add_argument("--json", action="store_true",
                    help="pair, takeover, status: the answer as JSON; pair does not wait")
    return ap


def lan_addrs(addrs: dict) -> list[str]:
    """The interface addresses mDNS may answer on: none of a container or VM bridge, a VPN
    tunnel or a peer link (the interfaces discovery never scans either)."""
    from proxy import discovery
    return [x for name, v in addrs.items() if not name.startswith(discovery.VIRTUAL) for x in v]


def _hosts(a) -> list[str]:
    return a.host or [x.strip() for x in os.environ.get("TANDEM_NODE_HOST",
                                                        "127.0.0.1").split(",") if x.strip()]


def unit_text(a) -> str:
    exe = os.path.join(os.path.dirname(sys.executable), "tandem-node")
    cmd = exe if os.path.exists(exe) else f"{sys.executable} -m proxy.nodeagent"
    args = "".join(f" --host {h}" for h in _hosts(a)) + f" --port {a.port} --state-dir " \
        f"{a.state_dir}" + (f" --tandem-dir {a.tandem_dir}" if a.tandem_dir else "") + \
        (f" --virtual {a.virtual}" if a.virtual else "") + \
        (" --mdns off" if a.mdns == "off" else "") + \
        (f" --weights-dir {a.weights_dir}" if a.weights_dir else "")
    return f"""[Unit]
Description=TandemNode agent (tandem-node) on {', '.join(_hosts(a))}:{a.port}
After=network-online.target

[Service]
ExecStart={cmd} serve{args}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""


def local_client(a, timeout: float = 10.0) -> nodeclient.Client | None:
    """This node's own agent on loopback: with its own certificate once the trust store is
    filled, else with the loopback secret; None when it does not answer."""
    store = nodetrust.Store(os.path.join(a.state_dir, "trust"))
    host = next((h for h in _hosts(a) if nodetrust.parse_ip(h).is_loopback), "127.0.0.1")
    host = str(nodetrust.parse_ip(host))
    host = f"[{host}]" if ":" in host else host
    tries = []
    if os.path.isfile(store.cert) and os.path.isfile(store.key):
        try:
            ctx = nodetrust.client_context(store.cert, store.key, store.own_pem())
            tries.append(nodeclient.Client(f"https://{host}:{a.port}", ssl_context=ctx,
                                           timeout=timeout))
        except (OSError, ssl.SSLError, nodetrust.TrustError):
            pass
    try:
        secret = nodetrust.load_secret(os.path.join(a.state_dir, "secret"))
        tries.insert(0 if not store.ready() else len(tries),
                     nodeclient.Client(f"http://{host}:{a.port}", secret=secret, timeout=timeout))
    except (OSError, nodetrust.TrustError):
        pass
    for c in tries:
        try:
            st, _ = c.json("GET", "/node/pair")
        except (OSError, ssl.SSLError, http.client.HTTPException):
            continue
        if st == 200:
            return c
    return None


def _when(t) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else "?"


def cmd_pair(a, takeover: bool) -> int:
    c = local_client(a)
    if c is None:
        print(f"[node] the agent does not answer on loopback port {a.port}: start it first "
              f"(tandem node, or systemctl --user start tandemnode)", file=sys.stderr)
        return 3
    body = {"takeover": takeover}
    if a.address:
        body["address"] = a.address
    if a.pair_port is not None:
        body["port"] = a.pair_port
    st, doc = c.json("POST", "/node/pair", body)
    if st != 200:
        msg = ((doc or {}).get("error") or {}).get("message") or f"HTTP {st}"
        print(f"[node] no pairing window: {msg}", file=sys.stderr)
        return 4 if st == 409 else 1
    if a.json:
        print(json.dumps(doc))
        return 0
    code = doc["code"]
    print(f"TandemNode pairing code:  {code[:3]} {code[3:]}\n"
          f"In TandemApp, Add Spark:  address {doc['address']}, port {doc['port']}, this code\n"
          f"Valid until {_when(doc['expires_at'])}, {doc['tries_left']} tries, once"
          + (" (take-over: the current owner loses this node)" if takeover else "") + "\n"
          f"This node's certificate:  sha256 {doc['fingerprint']}", flush=True)
    try:
        while True:
            time.sleep(1.0)
            st, got = c.json("GET", "/node/pair")
            w = (got or {}).get("window") or {}
            if st != 200:
                print("[node] the agent stopped answering", file=sys.stderr)
                return 1
            if w.get("state") != "open":
                break
    except KeyboardInterrupt:
        c.json("DELETE", "/node/pair/window")
        print("\n[node] pairing window closed")
        return 130
    if w.get("state") == "paired":
        o = (got or {}).get("owner") or {}
        print(f"Paired with {o.get('name') or 'TandemApp'} (sha256 {o.get('fingerprint', '')})")
        return 0
    print(f"[node] the window closed: {w.get('state')}", file=sys.stderr)
    return 5


def cmd_status(a) -> int:
    store = nodetrust.Store(os.path.join(a.state_dir, "trust"))
    c = local_client(a)
    doc = None
    if c is not None:
        st, doc = c.json("GET", "/node/pair")
    if doc is None:
        doc = {"fingerprint": store.own_fingerprint(), "owner": store.owner(),
               "window": None, "listen": store.listen_addrs(), "audit": store.audit_rows(20),
               "agent": "not answering"}
    if a.json:
        print(json.dumps(doc))
        return 0
    o = doc.get("owner")
    print(f"this node:  sha256 {doc.get('fingerprint')}")
    print(f"owner:      {o.get('name') or 'TandemApp'} (sha256 {o['fingerprint']}), "
          f"{o.get('via')} since {_when(o.get('paired_at'))}" if o else "owner:      none "
          "(tandem node pair)")
    print(f"listens on: {', '.join(doc.get('listen') or []) or 'loopback only'}")
    w = doc.get("window")
    if w:
        print(f"window:     {w['state']}, {w.get('tries_left')} tries left")
    return 0


def cmd_reset(a) -> int:
    """Every pin and the owner forgotten (files, so it works with the agent stopped too);
    --keys a new key and certificate as well. The running agent reloads first, while it still
    knows this node's old certificate."""
    store = nodetrust.Store(os.path.join(a.state_dir, "trust"))
    c = local_client(a)
    was = store.clear_owner()
    for p in store.peers():
        os.remove(p)
    store.audit("reset", keys=bool(a.keys),
                **({"controller": was["fingerprint"]} if was else {}))
    if c is not None:
        c.json("POST", "/node/pair/reload")
    if a.keys:
        for p in (store.cert, store.key):
            try:
                os.remove(p)
            except FileNotFoundError:
                pass
        store.ensure_identity(socket.gethostname().split(".")[0])
    print(f"[node] reset: {'owner ' + (was.get('name') or was['fingerprint'][:16]) + ' forgotten, ' if was else ''}"
          f"no pins{', a new certificate sha256 ' + store.own_fingerprint() if a.keys else ''}"
          f"{'' if c is not None else ' (the agent is not running)'}")
    return 0


def cmd_adopt(a) -> int:
    """SSH once (proxy/sshonce.py), on the node: the controller's certificate from stdin pinned
    as the owner, the address listened on in mTLS, the agent reloaded or its user unit started;
    one line `TANDEMNODE-ADOPTED {json}` with this node's certificate on stdout. Exit 65: owned by
    another controller (no --takeover); 66: the agent is not running and has no user unit; 67: an
    address it never listens on."""
    store = nodetrust.Store(os.path.join(a.state_dir, "trust"))
    pem = sys.stdin.read(64 * 1024)
    try:
        fp = nodetrust.pem_fingerprint(pem)
        address = str(nodetrust.parse_ip(a.address or "127.0.0.1"))
        ip = nodetrust.parse_ip(address)
        if ip.is_unspecified or ip.is_global:
            raise nodetrust.BindRefused(f"{address}: never a WAN or the unspecified address")
    except nodetrust.TrustError as exc:
        print(f"TANDEMNODE-ERROR bad_request {exc}", file=sys.stderr)
        return 2
    except nodetrust.BindRefused as exc:
        print(f"TANDEMNODE-ERROR bad_address {exc}", file=sys.stderr)
        return 67
    store.ensure_identity(socket.gethostname().split(".")[0] + (
        f"-{a.virtual}" if a.virtual else ""))
    owner = store.owner()
    if owner is not None and owner["fingerprint"] != fp and not a.takeover:
        print(f"TANDEMNODE-ERROR owned {owner.get('name') or 'TandemApp'} "
              f"{owner['fingerprint'][:16]}", file=sys.stderr)
        return 65
    c = local_client(a)
    unit = os.path.expanduser(UNIT)
    if c is None and not os.path.isfile(unit):
        print(f"TANDEMNODE-ERROR not_running the agent does not answer on port {a.port} and "
              f"there is no {UNIT}", file=sys.stderr)
        return 66
    took = owner if owner is not None and owner["fingerprint"] != fp else None
    store.set_owner(pem, {"name": a.name[:80], "via": "ssh", "paired_at": round(time.time(), 3),
                          "address": address})
    if not ip.is_loopback:
        store.set_listen(store.listen_addrs() + [address])
    store.audit("takeover" if took else "pair", via="ssh", controller=fp, name=a.name[:80],
                address=address, **({"from": took["fingerprint"]} if took else {}))
    if c is not None:
        c.json("POST", "/node/pair/reload")
    else:
        subprocess.run(["systemctl", "--user", "enable", "--now", "tandemnode"],
                       capture_output=True, timeout=30)
    node_id = virtual_id(nodesource.LocalNode(hosts=lambda: None).identity()["id"], a.virtual)
    print("TANDEMNODE-ADOPTED " + json.dumps({
        "certificate": store.own_pem(), "address": address, "port": a.port, "node_id": node_id,
        "took_over_from": None if took is None else {"name": took.get("name"),
                                                     "fingerprint": took["fingerprint"]}}))
    return 0


def main(argv=None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv.pop(0) if argv and argv[0] in COMMANDS else "serve"
    a = parser().parse_args(argv)
    a.state_dir = os.path.expanduser(a.state_dir)
    if cmd == "unit":
        sys.stdout.write(unit_text(a))
        return
    if cmd in ("pair", "takeover"):
        raise SystemExit(cmd_pair(a, takeover=a.takeover or cmd == "takeover"))
    if cmd == "status":
        raise SystemExit(cmd_status(a))
    if cmd == "reset":
        raise SystemExit(cmd_reset(a))
    if cmd == "adopt":
        raise SystemExit(cmd_adopt(a))
    store = nodetrust.Store(os.path.join(a.state_dir, "trust"))
    extra = store.listen_addrs() if store.ready() else []
    try:
        modes = [(h, nodetrust.listen_mode(h, store)) for h in _hosts(a) + extra]
    except nodetrust.BindRefused as exc:
        raise SystemExit(f"[node] not listening: {exc}")
    if cmd == "check":
        for h, m in modes:
            print(f"{h}:{a.port} {m}" + (" (plain HTTP, the loopback secret)" if m == "secret"
                                         else " (mTLS, the trust store)"))
        lans = [str(i) for i in mdns.allowed(lan_addrs(linktest.ip_addrs()))]
        print("mDNS: " + ("off" if a.mdns == "off" else
                          f"{mdns.SERVICE} on {', '.join(lans)}" if lans else
                          "no LAN address to advertise on"))
        return
    from tandemapp import logbuf
    buf = logbuf.install()
    try:
        agent = Agent(a.state_dir, tandem_dir=a.tandem_dir, logbuf=buf, virtual=a.virtual,
                      weights_dir=a.weights_dir)
        listen(agent, _hosts(a), a.port)
    except (nodetrust.TrustError, nodetrust.BindRefused, ValueError, OSError) as exc:
        raise SystemExit(f"[node] not starting: {exc}")
    ident = agent.identity()
    print(f"[node] TandemNode {ident.get('tandemllm', '')} node {ident['id']} on "
          + ", ".join(f"{ip}:{m['port']} ({m['mode']})" for ip, m in agent.listening.items())
          + "; engines: " + ", ".join(f"{e.id} :{e.port}{' managed' if e.managed else ''}"
                                       for e in agent.engines.list)
          + f"; psutil {'yes' if agent.collector.psutil else 'no'}, nvml "
          f"{'yes' if agent.collector._nvml_handle() else 'no'}"
          + f"; owner {(agent.store.owner() or {}).get('name') or 'none'}", flush=True)
    for s in agent.servers:
        threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.5},
                         daemon=True).start()
    if a.mdns == "on":
        try:
            agent.mdns = mdns.Responder(agent.mdns_info, lambda: lan_addrs(agent.addrs()),
                                        log=agent.log).start()
        except OSError as exc:
            agent.log(f"[node] mDNS: not advertising ({exc}); add this node by address")
    import signal
    done = threading.Event()
    signal.signal(signal.SIGTERM, lambda signum, frame: done.set())
    try:
        done.wait()
    except KeyboardInterrupt:
        pass
    finally:
        if agent.mdns is not None:
            agent.mdns.stop()
            agent.mdns = None
        agent.sampler.close()
        agent.close_window()
        for s in list(agent.servers):
            s.shutdown()
            s.server_close()
        print("[node] stopped", flush=True)


if __name__ == "__main__":
    main()
