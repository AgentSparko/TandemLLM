"""One model as two stage processes, the server's part (ENG-307).

    --stage LO:HI          this process serves the layers [LO, HI) of `--model`: 0:k is the front
                           stage, k:layers the tail
    --link-listen IP:PORT  wait for the other stage on this address, or
    --link-peer IP:PORT    connect to the other stage there (one of the two, on each side)
    --link-key-file PATH   the key both stages share: a file of mode 0600 with at least 32 bytes
    --stage-graphs on|off  a family whose stage captures graphs for its passes: its own graphs
                           (on, the default) or every pass eager
    --stage-caches on|off  keep state on both stages under one key (ENG-423): the prefix, session
                           and resident caches, the disk tier (`--disk-cache`) and side slots
                           (`--parallel`); off, the default: one sequence, nothing kept
    --stage-snapshots-gb G the front: memory for the states it keeps, a slot
    --stage-cache-dir PATH the front: where it writes its half of what the tail's disk tier keeps
    --stage-cache-gb G     the front: that directory's size

The tail is the engine a client talks to: the server as it is, with the family's tail stage as its
engine (the family's `serve` reads `STATE["stage"]` and asks it for the link, `Stage.attach`). The
front serves no model route: it loads its layers, answers `/health`, and acts on the headers the
tail sends (engine/methods/stages.py `Follower`). The protocol, the link and its handshake are
engine/methods; a family's stage code is its own (`engine/models/<family>/stages.py`). This module
names no family.

What a stage does not serve is refused at the start, before any weight is read: more than one
slot and the disk tier without `--stage-caches on` (or for a family that keeps no state split),
the KV pool, the conductor, setup mode, a family without a stage loader, a range that is neither
the front nor the tail. The session spill is turned off. A request with images gets 400. While the
link is down the tail's `/health` says `degraded` with `stage.link.state: down`, a request in
flight ends with `stage_down`, and a new one gets 503 with Retry-After; a new link starts both
stages' running sequences empty under a new epoch. A state the caches put back after that is the
front's own kept half under the same key, or the front is rebuilt from the tail's log of blocks.

The key is read once, from the file, and appears in no message, log line or route.
"""

from __future__ import annotations

import json
import os
import stat
import threading
import time
from array import array
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from engine.methods import link as L
from engine.methods import stages as S
from server import identity as identity_mod

#: how long a tail waits at its start for the front's link
ATTACH_S = 120.0
RETRY_S = 1.0
RETRY_AFTER_S = 5
#: the fake engine's stand-in model (`--fake-engine --stage`): no weights, the protocol alone
FAKE_LAYERS = 4
IMAGE_PARTS = ("image_url", "input_image", "image")


def add_flags(ap) -> None:
    ap.add_argument("--stage", default="", metavar="LO:HI",
                    help="serve the layers [LO, HI) of --model as one of two stage processes: 0:k is "
                         "the front stage, k:layers the tail, the one a client talks to (ENG-307)")
    ap.add_argument("--link-listen", default="", metavar="IP:PORT",
                    help="with --stage: wait for the other stage on this address (an IP literal)")
    ap.add_argument("--link-peer", default="", metavar="IP:PORT",
                    help="with --stage: connect to the other stage at this address (an IP literal)")
    ap.add_argument("--stage-graphs", default="on", choices=("on", "off"),
                    help="with --stage: a stage whose family captures graphs for its passes captures "
                         "its own (on), or runs every pass eager (off)")
    ap.add_argument("--stage-caches", default="off", choices=("on", "off"),
                    help="with --stage: keep state on both stages under one key: the prefix, session and "
                         "resident caches, the disk tier and side slots (ENG-423); off: one sequence, "
                         "nothing kept")
    ap.add_argument("--stage-snapshots-gb", type=float, default=2.0, metavar="G",
                    help="with --stage-caches on, the front stage: memory for the states it keeps, a slot")
    ap.add_argument("--stage-cache-dir", default="", metavar="PATH",
                    help="with --stage-caches on, the front stage: the directory for its half of what the "
                         "tail's disk tier keeps (none: a state read from the tail's disk is rebuilt or missed)")
    ap.add_argument("--stage-cache-gb", type=float, default=32.0, metavar="G",
                    help="with --stage-cache-dir: the directory's size; the files unused longest go first")
    ap.add_argument("--link-key-file", default="", metavar="PATH",
                    help="with --stage: the key both stages share, a file of mode 0600 with at "
                         "least 32 bytes")


def parse_range(text: str) -> tuple:
    """(lo, hi) of "LO:HI", or ValueError."""
    lo, sep, hi = str(text).partition(":")
    if not sep or not lo.isdigit() or not hi.isdigit() or int(lo) >= int(hi):
        raise ValueError(f"--stage {text!r}: a layer range LO:HI with LO < HI")
    return int(lo), int(hi)


def read_key_file(path: str) -> bytes:
    """The link's key from `path`: a regular file of this user, mode 0600, at least 32 bytes. An
    error names the file and the rule, never a byte of the key."""
    if not path:
        raise ValueError("--link-key-file is required with --stage: the key both stages share")
    p = os.path.expanduser(path)
    try:
        fd = os.open(p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as e:
        raise ValueError(f"--link-key-file {path}: cannot be read ({type(e).__name__})") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ValueError(f"--link-key-file {path}: not a regular file")
        if st.st_uid != os.geteuid():
            raise ValueError(f"--link-key-file {path}: owned by another user")
        if stat.S_IMODE(st.st_mode) != 0o600:
            raise ValueError(f"--link-key-file {path}: mode {stat.S_IMODE(st.st_mode):04o}, it must be 0600")
        raw = os.read(fd, 1 << 16)
    finally:
        os.close(fd)
    key = raw.strip()
    if len(key) < L.KEY_MIN:
        raise ValueError(f"--link-key-file {path}: shorter than {L.KEY_MIN} bytes")
    return key


class FakeStage(S.StageAdapter):
    """A stage of no model, for `--fake-engine`: the front's carry is each row's token plus the
    length it was sent at, and the tail checks it, so a link that lost, repeated or reordered a
    round is seen."""

    family = "fake"
    layers = FAKE_LAYERS
    cuts = tuple(range(1, FAKE_LAYERS))
    carry_dtype = "int32"
    carry_dims = (1,)

    def __init__(self, lo: int, hi: int):
        self.lo, self.hi = lo, hi
        self.n, self.pend = 0, None

    @property
    def length(self) -> int:
        return self.n

    def reset(self) -> None:
        self.n, self.pend = 0, None

    def rows(self, block, carry):
        want = [(t + self.n) & 0x7FFFFFFF for t in block.tokens]
        if self.lo and list(carry) != want:
            raise S.StageError("carry", "carry: the front's rows are not this block's")
        self.pend = block.rows
        if block.chain:
            self.commit(list(range(block.rows)))
        return want if self.lo == 0 else None

    def commit(self, path) -> None:
        if self.pend is None:
            if path:
                raise ValueError("no block to commit")
            return
        if any(not 0 <= i < self.pend for i in path):
            raise ValueError("not a path of the last block")
        self.n, self.pend = self.n + len(path), None

    def state(self) -> dict:
        return {"n": [self.n]}

    def encode(self, t) -> tuple:
        return L.DTYPE_CODES["int32"], (len(t), 1), array("i", t).tobytes()

    def decode(self, dtype, dims, buf):
        a = array("i")
        a.frombytes(bytes(buf))
        return a.tolist()


class Stage:
    """This process's stage: its range, its link settings and, once attached, its link."""

    def __init__(self, lo: int, hi: int, layers: int, key: bytes, listen: str, peer: str, base: dict,
                 options: dict, family=None, log=print):
        self.lo, self.hi, self.layers = lo, hi, layers
        self.role = "front" if lo == 0 else "tail"
        self.ident = {"role": self.role, "lo": lo, "hi": hi}
        self.listen, self.peer = listen, peer
        self.options, self.family, self.log = options, family, log
        self._key, self._base = key, base
        self._listener = None
        self._said = (None, 0.0)
        self.coordinator = None          # the tail's
        self.follower = None             # the front's
        self.link = None

    def __repr__(self) -> str:
        return f"Stage({self.role} [{self.lo}, {self.hi}) of {self.layers})"

    # ------------------------------------------------------------------ the link
    def seed(self) -> bytes:
        """What both stages put in every state's key: the code, the weights, the family and the cut."""
        import hashlib
        return hashlib.sha256(json.dumps({**self._base, "cut": self.hi if self.lo == 0 else self.lo},
                                         sort_keys=True, default=str).encode()).digest()

    def identity(self, adapter) -> dict:
        """The handshake's payload: what both stages must agree on, with this stage's range."""
        if (adapter.lo, adapter.hi) != (self.lo, self.hi):
            raise ValueError(f"the stage holds layers [{adapter.lo}, {adapter.hi}), not --stage {self.lo}:{self.hi}")
        return S.stage_identity(dict(self._base), adapter)

    def _say(self, line: str) -> None:
        """A log line, not repeated inside a minute."""
        now = time.monotonic()
        if line != self._said[0] or now - self._said[1] > 60.0:
            self._said = (line, now)
            self.log(f"[stage] {line}")

    def _open(self, ident: dict, last_epoch: int, wait: float | None):
        if self.listen:
            if self._listener is None:
                self._listener = L.Listener(self.listen)
                self.log(f"[stage] link: listening on {self._listener.host}:{self._listener.port}, "
                         f"interface {L.interface_of(self._listener.host)}")
            return S.open_link(self._key, ident, listener=self._listener, last_epoch=last_epoch,
                               accept_deadline=wait)
        return S.open_link(self._key, ident, peer=self.peer, last_epoch=last_epoch)

    def attach(self, adapter):
        """The tail's coordinator with its first link up, or SystemExit: a refused link names the
        reason, a front that is not there the address. A thread then keeps the link: when it goes
        down it opens the next one, which starts both stages empty under a new epoch."""
        ident = self.identity(adapter)
        adapter.seed = self.seed()
        co = self.coordinator = S.Coordinator(adapter)
        end = time.monotonic() + ATTACH_S
        where = f"listening on {self.listen}" if self.listen else f"at {self.peer}"
        while True:
            try:
                link = self._open(ident, 0, max(0.1, min(RETRY_S, end - time.monotonic())))
                break
            except L.LinkRefused as e:
                raise SystemExit(f"[stage] REFUSING to start: the other stage refused the link ({e.reason})")
            except L.LinkError as e:
                if time.monotonic() >= end:
                    raise SystemExit(f"[stage] REFUSING to start: no front stage {where} inside "
                                     f"{ATTACH_S:g} s ({e.code})") from None
                if not self.listen:
                    time.sleep(RETRY_S)
        co.attach(link)
        self.link = link
        self.log(f"[stage] {link.describe()}")
        threading.Thread(target=self._keep, args=(co, ident), name="stage-link", daemon=True).start()
        return co

    def _keep(self, co, ident: dict) -> None:
        while True:
            time.sleep(RETRY_S)
            cur = co.link
            if cur is not None and cur.state == "up":
                continue
            try:
                link = self._open(ident, co.epoch, RETRY_S)
            except L.LinkRefused as e:
                self._say(f"link: refused ({e.reason})")
                continue
            except L.LinkError as e:
                self._say(f"link: down ({co.why}); no peer yet ({e.code})")
                continue
            co.attach(link)
            self.link = link
            self.log(f"[stage] {link.describe()}")

    def fake_tail(self):
        """`--fake-engine` as a tail: `tick(block)` asks the front for the block's rows and keeps
        them, so every block the fake writes crosses the link: its first token as a decode step
        (a chain of one row), the rest as a verify block and its commit."""
        co = self.attach(FakeStage(self.lo, self.hi))

        def tick(block) -> None:
            toks = [int(t) for t in block]
            if toks:
                co.prefill(toks[:1], graph=1)
            if len(toks) > 1:
                co.verify(toks[1:], list(range(-1, len(toks) - 2)))
                co.commit(list(range(len(toks) - 1)))
        return tick

    # ------------------------------------------------------------------ what the routes read
    def health(self) -> dict:
        if self.coordinator is not None:
            out = self.coordinator.health()
        else:
            up = self.link is not None and self.link.state == "up"
            out = {"status": "ok", "stage": {"role": "front", "cut": self.hi, "link": {
                "state": "up" if up else "down", "epoch": self.link.epoch if self.link is not None else 0}}}
            if self.follower is not None:
                out["stage"]["steps"] = self.follower.steps
        if self.options.get("caches") == "on":
            # ENG-423: the kept state's counters, each side its own
            if self.coordinator is not None:
                out["stage"]["state"] = self.coordinator.report()
            elif self.follower is not None:
                fol = self.follower
                out["stage"]["state"] = {**fol.stats, "slots": {
                    str(i): seat.adapter.store.report() for i, seat in sorted(fol.seats.items())
                    if getattr(seat.adapter, "store", None) is not None}}
        link = self.link
        if link is not None:
            out["stage"]["link"].update(transport=link.transport, bytes={
                "tx": link.stats.tx_bytes, "rx": link.stats.rx_bytes})
        out["stage"].update(lo=self.lo, hi=self.hi, layers=self.layers, options=self.options)
        return out

    def down(self) -> str | None:
        """Why the tail cannot serve now, or None."""
        co = self.coordinator
        if co is None or (co.link is not None and co.link.state == "up"):
            return None
        return str(co.why or "down")


def prepare(a, state: dict, log=print) -> None:
    """Read the stage flags into `state["stage"]`, or SystemExit with what is refused. Without
    `--stage` nothing is set and the server is the one it was."""
    given = [f for f in ("link_listen", "link_peer", "link_key_file") if getattr(a, f, "")]
    if not getattr(a, "stage", ""):
        if given:
            raise SystemExit(f"[stage] REFUSING: --{given[0].replace('_', '-')} needs --stage")
        return
    try:
        state["stage"] = _prepare(a, log)
    except ValueError as e:
        raise SystemExit(f"[stage] REFUSING: {e}") from None
    st = state["stage"]
    if getattr(a, "session_spill", "off") == "on":
        # its restore puts states back without the engine's verb: the front's half could not follow
        a.session_spill = "off"
        log("[stage] the session spill is not served split: --session-spill off")
    log(f"[stage] {st.role} stage, layers [{st.lo}, {st.hi}) of {st.layers}; "
        + (f"link listens on {st.listen}" if st.listen else f"link dials {st.peer}")
        + f"; options {json.dumps(st.options, sort_keys=True)}")


def _prepare(a, log) -> Stage:
    lo, hi = parse_range(a.stage)
    if bool(a.link_listen) == bool(a.link_peer):
        raise ValueError("--stage needs one of --link-listen and --link-peer")
    L.parse_addr(a.link_listen or a.link_peer)
    caches = getattr(a, "stage_caches", "off") == "on"
    slots = int(getattr(a, "parallel", 1) or 1)
    for flag, bad, why in (
            ("setup", bool(getattr(a, "setup", False)), "setup mode serves no model"),
            ("parallel", slots > 1 and not caches,
             "a stage serves one slot: --parallel 1 (side slots: --stage-caches on)"),
            ("kv-pool", getattr(a, "kv_pool", "off") == "on", "a stage keeps its rows in place: --kv-pool off"),
            ("disk-cache", getattr(a, "disk_cache", "off") not in ("", "off", None) and not caches,
             "a stage keeps no caches: --disk-cache off (the disk tier: --stage-caches on)"),
            ("conductor", getattr(a, "conductor", "raw") not in ("raw", "off"),
             "a stage serves the raw engine: --conductor raw"),
            ("backends", getattr(a, "backends", "off") not in ("", "off", None),
             "a stage routes to no backend: --backends off")):
        if bad:
            raise ValueError(f"--stage with --{flag}: {why}")
    key = read_key_file(a.link_key_file)
    args = vars(a)
    if getattr(a, "fake_engine", False):
        family, name, layers, weights = None, FakeStage.family, FAKE_LAYERS, "fake"
        options = {"slots": slots, "caches": "on"} if caches else {"slots": 1}
        if not (0 <= lo < hi <= layers) or (lo, hi) == (0, layers) or (lo != 0 and hi != layers):
            raise ValueError(f"--stage {lo}:{hi}: a stage is the front 0:k or the tail k:{layers} of the "
                             f"fake engine's {layers} layers")
    else:
        from engine.models import REGISTRY
        from server import diskcache
        name = identity_mod._family(args)
        family = S.family_stages(REGISTRY[name])
        if not hasattr(family, "open_stage"):
            raise ValueError(f"the {name} family is not served as a stage yet")
        options = family.stage_options(a, lo, hi)
        if caches and options.get("caches") != "on":
            raise ValueError(f"--stage-caches on: the {name} family keeps no state split yet")
        if slots > int(options.get("slots", 1)):
            raise ValueError(f"--parallel {slots}: this family's stage serves {options.get('slots', 1)} slot(s)")
        layers = identity_mod._layers({}, args)
        weights = [diskcache.content_id(x) for x in str(a.model).split(",")]
    base = {"identity": identity_mod.VERSION, "code": identity_mod.code_digest(),
            "weights": identity_mod.digest(weights), "family": name, "layers": layers}
    return Stage(lo, hi, layers, key, a.link_listen, a.link_peer, base, options, family, log)


def front(state: dict) -> bool:
    st = state.get("stage")
    return st is not None and st.role == "front"


class _FrontHandler(BaseHTTPRequestHandler):
    """The front's routes: `/health`, and 409 `stage_front` for everything else."""

    server_version = "stage"

    def log_message(self, fmt, *args):                     # noqa: D102
        return

    def _json(self, code: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _any(self) -> None:
        if self.command == "GET" and self.path.split("?")[0].rstrip("/") in ("/health", "/healthz", "/v1/health"):
            return self._json(200, self.server.health())
        self._json(409, {"error": {"type": "stage_front", "code": "stage_front",
                                   "message": "this process is the front stage of a split model; "
                                              "requests go to the tail stage"}})

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _any


def serve_front(a, state: dict, log=print) -> None:
    """The front stage: load the layers, open the port (its `/health` answers once the weights are
    in, as the server's), then serve link after link until the process is stopped."""
    st = state["stage"]
    if st.family is None:
        adapter = FakeStage(st.lo, st.hi)
    else:
        more = {}
        if hasattr(st.family, "store_options"):
            more["store"] = st.family.store_options(st.options, a, st.seed())      # ENG-423: its kept states
        t0 = time.monotonic()
        adapter = st.family.open_stage(a.model, st.lo, st.hi, int(a.max_len), log=log, options=st.options, **more)
        t1 = time.monotonic()
        if hasattr(adapter, "warm"):
            adapter.warm()                 # the kernels' first launches, before a deadline counts
        log(f"[stage] front stage: weights and engine {t1 - t0:.1f} s, first launches {time.monotonic() - t1:.1f} s")
    adapter.seed = st.seed()
    ident = st.identity(adapter)
    st.follower = S.Follower(adapter)
    started = time.time()

    def connect(last_epoch: int):
        try:
            st.link = st._open(ident, last_epoch, None)
        except L.LinkError:
            time.sleep(RETRY_S)
            raise
        return st.link

    def health() -> dict:
        return {**st.health(), "model": getattr(a, "served_model", ""),
                "uptime_s": round(time.time() - started, 1)}

    httpd = ThreadingHTTPServer((a.host, int(a.port)), _FrontHandler)
    httpd.daemon_threads = True
    httpd.health = health
    threading.Thread(target=st.follower.run, args=(connect, lambda line: st._say(line)),
                     name="stage-follower", daemon=True).start()
    log(f"[stage] front stage ready on http://{a.host}:{a.port}/health; it serves no model route")
    httpd.serve_forever()


def health(state: dict, status: str) -> dict:
    """What `/health` adds for a stage: the `stage` block, and `degraded` over `ok` while the link
    is down."""
    st = state.get("stage")
    if st is None:
        return {}
    out = st.health()
    mine = out.pop("status")
    if status == "ok" and mine != "ok":
        out["status"] = mine
    return out


class StageDisk:
    """The tail's disk tier (engine/disk_cache.py) with the front told what it keeps (ENG-423).
    Every call is the tier's own. After the tier took a state's sections for an anchor file, the
    engine asks the front to write its half under the same key (`disk_keep`). A restore the front
    cannot follow (it holds no half of that state and no log rebuilds it) is a miss: the anchor is
    given up and the prefill goes on from the caches in memory or cold."""

    def __init__(self, tier):
        self.__dict__["_tier"] = tier

    def __getattr__(self, name):
        return getattr(self._tier, name)

    def __setattr__(self, name, value):
        setattr(self._tier, name, value)

    def capture(self, job, eng, L: int) -> None:
        self._tier.capture(job, eng, L)
        if job is not None and L in getattr(job, "lines", ()):
            keep = getattr(eng, "disk_keep", None)
            if keep is not None:
                keep()

    def restore(self, eng, drafter, hit) -> bool:
        try:
            return self._tier.restore(eng, drafter, hit)
        except S.StageError as e:
            if e.code != "snapshot":
                raise
            self._tier._fail("stage", hit, ("anchor", hit.anchor["key"]))
            return False


def disk(state: dict, tier):
    """The disk tier as a tail stage with caches uses it; any other server's as it is."""
    st = state.get("stage")
    if tier is None or st is None or st.role != "tail" or st.options.get("caches") != "on":
        return tier
    return StageDisk(tier)


def _has_image(body: dict) -> bool:
    for m in body.get("messages") or []:
        parts = m.get("content") if isinstance(m, dict) else None
        if isinstance(parts, list) and any(isinstance(p, dict) and p.get("type") in IMAGE_PARTS for p in parts):
            return True
    return False


def refusal(state: dict, body: dict):
    """Why a completion request is not taken by a stage, as (status, body bytes, headers), or None:
    images (400), the link down (503 with Retry-After)."""
    st = state.get("stage")
    if st is None:
        return None
    if _has_image(body):
        return 400, json.dumps({"error": {"type": "invalid_request_error", "code": "stage_text_only",
                                          "message": "a split engine serves text only"}}).encode(), ()
    why = st.down()
    if why is not None:
        return 503, json.dumps({"error": {"type": "stage_down", "code": "stage_down",
                                          "message": f"the other stage is not reachable ({why})"}}).encode(), \
            (("Retry-After", str(RETRY_AFTER_S)),)
    return None
