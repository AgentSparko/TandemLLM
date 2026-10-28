"""A TandemNode's weights store and the copy of a pack between nodes (SRV-731, concept C9: download
once, copy to the other nodes, never N times from Hugging Face).

    store = Store(root)                          `<state dir>/weights` (`--weights-dir`)
    store.packs()                                the packs it holds, each with its content id
    store.manifest(dir)                          every file of a pack: size, sha256 when known
    store.open_receive(body) -> Receive          a copy arriving: its token, its staging folder
    rcv.put(rel, stream, length)                 one file, hashed as it is written
    rcv.commit() -> {profile, dir, content_id}   every file there: the folder moved into place
    push(files, opener, client, copy, token, job, stop)   the sender: each file PUT to the
                                                 receiving agent, hashed on both ends

A PACK is a folder of weight files under one name (a Setup Guide profile: `exl3-4.0`). The node's
Setup Guide packs (the engine's `GET /v1/setup/weights`) and the store's are listed together; a
copy lands in the store. Its CONTENT ID is proxy/nodeagent.py's `content_id`: sha256 over every
file's name, size and sha256 as `_hub_manifest.json` records them (ops/fetch_weights.py writes it
with the hub's own hashes).

A COPY (the controller's job, proxy/fleetweights.py):

    1  the source's manifest of the pack (GET /node/weights/{profile}/manifest): every file with
       its size and, when its hub manifest has one, its sha256
    2  the receiver opens a receive (POST /node/weights/receive): the pack must not be there, its
       bytes and 1 GiB must be free; it answers a single-use token and stages into
       `<store>/.incoming-<copy>/`
    3  each file PUT to /node/weights/receive/{copy}/{path} with its length: the receiver hashes
       what it writes and answers the sha256; the sender hashed what it read; they must agree, and
       agree with the manifest's sha256 where it has one, else the copy fails
    4  commit (POST .../commit): every file there at its size; a pack that carried no
       `_hub_manifest.json` gets one from the hashes both ends agreed on; the staging folder is
       moved into place and the content id computed there

The sender is the source node's agent (POST /node/weights/send: it pushes to the receiver's agent
over their pinned mTLS, the LAN or the fabric), or the controller itself, which streams from the
source's agent (GET /node/weights/{profile}/files/{path}) to the receiver's when the two cannot
reach each other (the controller's own machine on loopback, a tunnelled node). A peer node reaches
only a receive's routes, and only with that receive's token. A receive that stops half way
(abort, a failed file, the agent's restart) leaves nothing: its staging folder is removed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import threading
import time
import urllib.parse

MANIFEST = "_hub_manifest.json"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
CHUNK = 1 << 20
SPARE = 1 << 30                       # free space a receive leaves beside the pack
GIB = float(1 << 30)


class WeightsError(Exception):
    def __init__(self, status: int, kind: str, message: str):
        super().__init__(message)
        self.status, self.kind, self.message = status, kind, message

    def body(self) -> dict:
        return {"error": {"type": self.kind, "message": self.message}}


def check_name(name: str) -> str:
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise WeightsError(400, "bad_request", "a pack's name is 1 to 128 of A-Z, a-z, 0-9, "
                                               "'.', '_', '-'")
    return name


def check_rel(rel: str) -> str:
    """A file's path inside a pack: relative, no `..`, no empty part."""
    parts = rel.split("/") if isinstance(rel, str) else []
    if not parts or rel.startswith("/") or any(p in ("", ".", "..") for p in parts) or \
            "\0" in rel or len(rel) > 1024:
        raise WeightsError(400, "bad_request", f"not a path inside a pack: {rel!r}")
    return rel


def files_of(d: str) -> dict:
    """{relative path: size} of every file under a pack's folder (a link followed to its file)."""
    out = {}
    for base, dirs, fs in os.walk(d, followlinks=True):
        dirs[:] = sorted(x for x in dirs if not x.startswith(".incoming-"))
        for f in fs:
            p = os.path.join(base, f)
            try:
                out[os.path.relpath(p, d).replace(os.sep, "/")] = os.stat(p).st_size
            except OSError:
                continue
    return out


def content_id_of(rows: dict) -> str:
    """The content id of {path: (size, sha256 or None)}: nodeagent.content_id's formula."""
    lines = [f"{n}\t{s}\t{h or ''}" for n, (s, h) in rows.items()]
    return "sha256:" + hashlib.sha256("\n".join(sorted(lines)).encode()).hexdigest()


def manifest(d: str) -> dict:
    """Every file of a pack: {files: {path: {size, sha256 or None}}, bytes}. The sha256 is the hub
    manifest's when it has one for that file at that size."""
    hub = {}
    try:
        with open(os.path.join(d, MANIFEST)) as f:
            hub = json.load(f).get("files") or {}
    except (OSError, ValueError, AttributeError):
        hub = {}
    out = {}
    for rel, size in sorted(files_of(d).items()):
        h = hub.get(rel) if isinstance(hub.get(rel), dict) else {}
        sha = h.get("sha256") if h.get("size") in (None, size) else None
        out[rel] = {"size": size, "sha256": sha}
    return {"files": out, "bytes": sum(x["size"] for x in out.values())}


class Receive:
    def __init__(self, store: "Store", copy: str, profile: str, files: dict, source: str | None,
                 clock=time.time):
        self.store, self.copy, self.profile, self.files = store, copy, profile, files
        self.source = source
        self.token = secrets.token_urlsafe(24)
        self.dir = os.path.join(store.root, f".incoming-{copy}")
        self.got: dict[str, tuple[int, str]] = {}
        self.state = "receiving"
        self.started = clock()
        self.clock = clock
        self.bytes_done = 0
        self.lock = threading.Lock()
        os.makedirs(self.dir, mode=0o700)

    def view(self) -> dict:
        return {"copy": self.copy, "profile": self.profile, "state": self.state,
                "files_total": len(self.files), "files_done": len(self.got),
                "bytes_total": sum(x["size"] for x in self.files.values()),
                "bytes_done": self.bytes_done, "source": self.source}

    def put(self, rel: str, stream, length: int) -> dict:
        rel = check_rel(rel)
        want = self.files.get(rel)
        if self.state != "receiving":
            raise WeightsError(409, "conflict", f"this receive is {self.state}")
        if want is None:
            raise WeightsError(400, "bad_request", f"{rel} is not in the copy's manifest")
        if length != want["size"]:
            raise WeightsError(400, "bad_request", f"{rel} is {want['size']} bytes, not {length}")
        path = os.path.join(self.dir, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        h = hashlib.sha256()
        left = length
        with open(path, "wb") as f:
            while left:
                got = stream.read(min(CHUNK, left))
                if not got:
                    break
                h.update(got)
                f.write(got)
                left -= len(got)
                with self.lock:
                    self.bytes_done += len(got)
        if left:
            os.remove(path)
            raise WeightsError(400, "short_body", f"{rel}: {length - left} of {length} bytes "
                                                  f"arrived")
        sha = h.hexdigest()
        if want.get("sha256") and want["sha256"] != sha:
            os.remove(path)
            raise WeightsError(422, "hash_mismatch", f"{rel}: sha256 {sha[:16]}… is not the "
                                                     f"manifest's {want['sha256'][:16]}…")
        self.got[rel] = (length, sha)
        return {"path": rel, "bytes": length, "sha256": sha}

    def commit(self) -> dict:
        missing = [r for r in self.files if r not in self.got]
        if missing:
            raise WeightsError(409, "incomplete", f"{len(missing)} files have not arrived "
                                                  f"(the first: {missing[0]})")
        if MANIFEST not in self.files:
            # the hashes both ends agreed on, so the new pack has a content id of its own
            doc = {"files": {r: {"size": s, "sha256": h} for r, (s, h) in sorted(self.got.items())},
                   "copied_from": self.source, "copied_at": round(self.clock(), 3)}
            with open(os.path.join(self.dir, MANIFEST), "w") as f:
                json.dump(doc, f, indent=1)
        dest = os.path.join(self.store.root, self.profile)
        if os.path.exists(dest):
            raise WeightsError(409, "exists", f"{self.profile} arrived here meanwhile")
        os.rename(self.dir, dest)
        self.state = "done"
        from proxy.nodeagent import content_id
        cid, basis = content_id(dest)
        return {"copy": self.copy, "profile": self.profile, "dir": dest, "content_id": cid,
                "content_basis": basis, "bytes": sum(s for s, _ in self.got.values()),
                "files": len(self.got)}

    def abort(self) -> None:
        if self.state == "receiving":
            self.state = "aborted"
        shutil.rmtree(self.dir, ignore_errors=True)


class Store:
    def __init__(self, root: str, *, log=None, clock=time.time):
        self.root = os.path.expanduser(root)
        os.makedirs(self.root, mode=0o700, exist_ok=True)
        self.log = log or (lambda m: print(m, flush=True))
        self.clock = clock
        self.receives: dict[str, Receive] = {}
        self.sends: dict[str, dict] = {}
        self._stops: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        for d in os.listdir(self.root):                # a receive the last run left half done
            if d.startswith(".incoming-"):
                shutil.rmtree(os.path.join(self.root, d), ignore_errors=True)

    def free(self) -> int:
        return shutil.disk_usage(self.root).free

    def packs(self) -> list[dict]:
        from proxy.nodeagent import content_id
        out = []
        for name in sorted(os.listdir(self.root)):
            d = os.path.join(self.root, name)
            if name.startswith(".") or not os.path.isdir(d) or not NAME_RE.match(name):
                continue
            cid, basis = content_id(d)
            sizes = files_of(d)
            out.append({"profile": name, "dir": d, "source": "store",
                        "bytes": sum(sizes.values()), "files": len(sizes), "content_id": cid,
                        "content_basis": basis})
        return out

    def pack_dir(self, name: str) -> str | None:
        d = os.path.join(self.root, check_name(name))
        return d if os.path.isdir(d) else None

    def open_receive(self, body, source: str | None = None) -> Receive:
        if not isinstance(body, dict):
            raise WeightsError(400, "bad_request", "the body is {copy, profile, files}")
        copy = body.get("copy")
        if not isinstance(copy, str) or not NAME_RE.match(copy):
            raise WeightsError(400, "bad_request", "copy is an id of A-Z, a-z, 0-9, '.', '_', '-'")
        profile = check_name(body.get("profile"))
        files = body.get("files")
        if not isinstance(files, dict) or not files:
            raise WeightsError(400, "bad_request", "files is {path: {size, sha256?}}")
        clean = {}
        for rel, f in files.items():
            check_rel(rel)
            if not isinstance(f, dict) or not isinstance(f.get("size"), int) or f["size"] < 0:
                raise WeightsError(400, "bad_request", f"{rel}: {{size, sha256?}}")
            sha = f.get("sha256")
            if sha is not None and not (isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{64}",
                                                                               sha)):
                raise WeightsError(400, "bad_request", f"{rel}: sha256 is 64 hex digits")
            clean[rel] = {"size": f["size"], "sha256": sha}
        if os.path.exists(os.path.join(self.root, profile)):
            raise WeightsError(409, "exists", f"{profile} is here already: delete it first")
        need = sum(f["size"] for f in clean.values())
        free = self.free()
        if need + SPARE > free:
            raise WeightsError(507, "no_space", f"needs {need / GIB:.1f} GiB and 1 GiB to spare, "
                                                f"{free / GIB:.1f} GiB free")
        with self._lock:
            if copy in self.receives and self.receives[copy].state == "receiving":
                raise WeightsError(409, "busy", f"copy {copy} is arriving already")
            rcv = self.receives[copy] = Receive(self, copy, profile, clean, source,
                                                clock=self.clock)
        self.log(f"[node] receiving {profile} ({need / GIB:.2f} GiB, {len(clean)} files) "
                 f"as copy {copy}")
        return rcv

    def receive(self, copy: str, token: str | None = None) -> Receive:
        rcv = self.receives.get(copy)
        if rcv is None:
            raise WeightsError(404, "not_found", f"no receive {copy}")
        if token is not None and not secrets.compare_digest(token, rcv.token):
            raise WeightsError(403, "forbidden", "this copy's token does not match")
        return rcv

    def remove(self, name: str, dry_run: bool, confirm: bool) -> dict:
        d = self.pack_dir(name)
        if d is None:
            raise WeightsError(404, "not_found", f"no pack {name} in the store")
        size = sum(files_of(d).values())
        out = {"profile": name, "dir": d, "bytes": size, "dry_run": dry_run, "removed": False}
        if dry_run:
            return out
        if not confirm:
            raise WeightsError(428, "confirm_required", "a removal needs confirm=1")
        shutil.rmtree(d)
        self.log(f"[node] removed {name} ({size / GIB:.2f} GiB) from the weights store")
        return dict(out, removed=True)

    # ------------------------------------------------------------------ sending (this node)
    def start_send(self, copy: str, profile_dir: str, files: dict, client, token: str,
                   who: str) -> dict:
        job = {"copy": copy, "state": "running", "by": who, "started_at": self.clock(),
               "ended_at": None, "files_total": len(files), "files_done": 0,
               "bytes_total": sum(f["size"] for f in files.values()), "bytes_done": 0,
               "hashes": {}, "error": None}
        stop = threading.Event()
        with self._lock:
            old = self.sends.get(copy)
            if old is not None and old["state"] == "running":
                raise WeightsError(409, "busy", f"copy {copy} is being sent already")
            self.sends[copy] = job
            self._stops[copy] = stop

        def opener(rel):
            path = os.path.join(profile_dir, *rel.split("/"))
            return open(path, "rb")

        def run():
            try:
                push(files, opener, client, copy, token, job, stop)
                job["state"] = "done"
            except WeightsError as exc:
                job.update(state="failed", error={"type": exc.kind, "message": exc.message})
            except Exception as exc:                                 # noqa: BLE001
                job.update(state="failed", error={"type": "error",
                                                  "message": f"{type(exc).__name__}: {exc}"})
            job["ended_at"] = self.clock()
            self.log(f"[node] send {copy}: {job['state']}"
                     + (f" · {job['error']['message']}" if job["error"] else ""))
        threading.Thread(target=run, name=f"weights-send-{copy}", daemon=True).start()
        return dict(job)

    def stop_send(self, copy: str) -> dict:
        with self._lock:
            ev, job = self._stops.get(copy), self.sends.get(copy)
        if job is None:
            raise WeightsError(404, "not_found", f"no send {copy}")
        if ev is not None:
            ev.set()
        return dict(job)


def push(files: dict, opener, client, copy: str, token: str, job: dict, stop) -> None:
    """Each file of `files` ({path: {size, sha256?}}) PUT to the receiving agent behind `client`
    (proxy/nodeclient.py), read through `opener(path)` (a file, or a stream from another agent)
    and hashed on the way; the receiver's hash must be the same, and the manifest's where it has
    one. `job` counts files_done, bytes_done and keeps `hashes` {path: [size, sha256]}."""
    for rel, f in files.items():
        if stop.is_set():
            raise WeightsError(409, "stopped", "the copy was stopped")
        size = f["size"]
        src = opener(rel)
        conn = client.connection(timeout=300.0)
        try:
            h = client.headers()
            h.update({"Content-Type": "application/octet-stream", "Content-Length": str(size),
                      "X-Tandem-Copy-Token": token})
            conn.putrequest("PUT", f"/node/weights/receive/{urllib.parse.quote(copy)}/"
                                   f"{urllib.parse.quote(rel)}", skip_accept_encoding=True)
            for k, v in h.items():
                conn.putheader(k, v)
            conn.endheaders()
            sha = hashlib.sha256()
            left = size
            while left:
                if stop.is_set():
                    raise WeightsError(409, "stopped", "the copy was stopped")
                got = src.read(min(CHUNK, left))
                if not got:
                    raise WeightsError(502, "short_read", f"{rel}: the source gave "
                                                          f"{size - left} of {size} bytes")
                sha.update(got)
                conn.send(got)
                left -= len(got)
                job["bytes_done"] += len(got)
            r = conn.getresponse()
            raw = r.read()
            try:
                doc = json.loads(raw or b"null")
            except ValueError:
                doc = None
            if r.status != 200 or not isinstance(doc, dict):
                err = (doc or {}).get("error") if isinstance(doc, dict) else None
                raise WeightsError(r.status if r.status >= 400 else 502,
                                   (err or {}).get("type") or "refused",
                                   f"{rel}: the receiver answered HTTP {r.status}"
                                   + (f": {err['message']}" if err else ""))
        finally:
            conn.close()
            close = getattr(src, "close", None)
            if close is not None:
                close()
        mine = sha.hexdigest()
        if doc.get("sha256") != mine:
            raise WeightsError(502, "hash_mismatch", f"{rel}: the receiver wrote other bytes "
                                                     f"({str(doc.get('sha256'))[:16]}…, sent "
                                                     f"{mine[:16]}…)")
        if f.get("sha256") and f["sha256"] != mine:
            raise WeightsError(502, "hash_mismatch", f"{rel}: the source's file is not its "
                                                     f"manifest's ({mine[:16]}…)")
        job["hashes"][rel] = [size, mine]
        job["files_done"] += 1
