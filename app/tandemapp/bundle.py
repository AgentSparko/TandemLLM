"""The dashboard's built page as a bundle: its content hash, its manifest, and the ways to get one.

The page (`dashboard/dist/`) is a build output, not a file in git (OPS-246). Every install path gets
it in one of three ways, and each ends with a checked bundle or a clear message:

  build   `npm ci` with the lockfile, then `npm run build` (type check and Vite), then the manifest
  fetch   a release archive by URL or path, checked against the sha256 the caller pins, unpacked
          safely, its manifest checked against the files
  ensure  a bundle already here and intact, else build when npm is on the PATH, else fetch when a
          URL and its sha256 are given, else the message that says what to do

The bundle's hash is the sha256 of one line per file, `<path>\\0<sha256 of the file>\\n`, the paths
relative and sorted; the manifest `bundle.json` itself is not part of it and symlinks are skipped.
The same commit built twice gives the same hash (Vite names each asset by its content).

`bundle.json` holds the hash, every file's sha256, the source (build, fetch or record), the commit
and the lockfile's sha256 when known, and the Node version that built it. The server reads it to
say which bundle it serves (tandemapp/static.py). Standard library only: the build backend, the
tools and the server all import it.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time

MANIFEST = "bundle.json"
SCHEMA = 1
#: an archive larger than this, or with more files, is refused before anything is written
MAX_BYTES = 64 << 20
MAX_FILES = 4000
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

NO_BUNDLE = """\
The dashboard's page is not built in this checkout ({dist}).
It is a build output, not a file in git. Get it in one of these ways, then run this step again:
  1. Build it (Node 20.19 or newer with npm):
       python3 {tool} build
  2. Fetch a released bundle and check it against its published sha256:
       python3 {tool} fetch --url <bundle .tar.gz URL or path> --sha256 <sha256>
     (or set TANDEM_DASHBOARD_URL and TANDEM_DASHBOARD_SHA256 for an install)
Without it the server runs and /dashboard/ shows a page that says it is not built."""


class BundleError(Exception):
    """A bundle that cannot be built, fetched or trusted; the text says why and what to do."""


class NoBundle(BundleError):
    """No way to get the page here: no npm, no URL with its sha256. The text says what to do."""


def app_root() -> str:
    """TandemApp's root: the folder that holds pyproject.toml, dashboard/ and tools/."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def dist_dir(root: str | None = None) -> str:
    return os.path.join(root or app_root(), "dashboard", "dist")


def message(dist: str | None = None) -> str:
    """The actionable text for a checkout with no page."""
    return NO_BUNDLE.format(dist=dist or dist_dir(),
                            tool=os.path.join(app_root(), "tools", "dashboard_bundle.py"))


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def files(root: str) -> list[tuple[str, str]]:
    """[(relative path with /, absolute path)] of every regular file under `root`, sorted; no
    symlinks, not the manifest."""
    out = []
    for d, dirs, names in os.walk(root):
        dirs[:] = sorted(x for x in dirs if not os.path.islink(os.path.join(d, x)))
        for n in names:
            p = os.path.join(d, n)
            if os.path.islink(p) or not os.path.isfile(p):
                continue
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            if rel != MANIFEST:
                out.append((rel, p))
    return sorted(out)


def content(root: str) -> tuple[str, dict[str, str], int]:
    """(bundle hash, {path: sha256}, total bytes) of the files under `root`."""
    h = hashlib.sha256()
    each, total = {}, 0
    for rel, p in files(root):
        s = _sha256_file(p)
        each[rel] = s
        total += os.path.getsize(p)
        h.update(rel.encode() + b"\0" + s.encode() + b"\n")
    return h.hexdigest(), each, total


def read_manifest(root: str) -> dict | None:
    try:
        with open(os.path.join(root, MANIFEST), encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def write_manifest(root: str, source: str, commit: str | None = None,
                   lockfile: str | None = None, node: str | None = None) -> dict:
    """Hash the files under `root` and write `bundle.json` beside them; the manifest."""
    if not os.path.isfile(os.path.join(root, "index.html")):
        raise BundleError(f"no index.html in {root}: not a built page")
    digest, each, total = content(root)
    doc = {"schema": SCHEMA, "sha256": digest, "source": source, "commit": commit,
           "lockfile_sha256": _sha256_file(lockfile) if lockfile and os.path.isfile(lockfile)
           else None,
           "node": node, "files": len(each), "bytes": total, "file_sha256": each}
    tmp = os.path.join(root, MANIFEST + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, os.path.join(root, MANIFEST))
    return doc


def verify(root: str, expect: str | None = None) -> dict:
    """{"ok", "sha256" (the files), "recorded" (the manifest's), "problems": [...]} for `root`."""
    problems = []
    if not os.path.isfile(os.path.join(root, "index.html")):
        return {"ok": False, "sha256": None, "recorded": None,
                "problems": [f"no index.html in {root}"]}
    digest, each, _ = content(root)
    doc = read_manifest(root)
    recorded = doc.get("sha256") if doc else None
    if doc is None:
        problems.append(f"no {MANIFEST}")
    elif recorded != digest:
        listed = doc.get("file_sha256") or {}
        changed = sorted(p for p in set(listed) | set(each) if listed.get(p) != each.get(p))
        problems.append(f"the files differ from {MANIFEST}: " + ", ".join(changed[:8])
                        + (f" and {len(changed) - 8} more" if len(changed) > 8 else ""))
    if expect and expect.lower() != digest:
        problems.append(f"bundle {digest} is not the expected {expect.lower()}")
    return {"ok": not problems, "sha256": digest, "recorded": recorded, "problems": problems}


# -------------------------------------------------------------------------- the server's view
_INFO: dict = {}


def _stamp(root: str) -> tuple:
    """Each file's path, size and mtime (the manifest's too): cheap, and any change shows."""
    out = []
    for rel, p in files(root) + [(MANIFEST, os.path.join(root, MANIFEST))]:
        try:
            st = os.stat(p)
            out.append((rel, st.st_size, st.st_mtime_ns))
        except OSError:
            out.append((rel, None, None))
    return tuple(out)


def info(root: str) -> dict:
    """What the server says about the bundle it serves, hashed again only when a file changed:
    {"present", "sha256", "verified", "source", "commit"}."""
    real = os.path.realpath(root)
    present = os.path.isfile(os.path.join(root, "index.html"))
    key = (present, _stamp(root) if present else ())
    hit = _INFO.get(real)
    if hit and hit[0] == key:
        return hit[1]
    if not present:
        out = {"present": False, "sha256": None, "verified": False, "source": None, "commit": None}
    else:
        v = verify(root)
        doc = read_manifest(root) or {}
        out = {"present": True, "sha256": v["sha256"], "verified": v["ok"],
               "source": doc.get("source"), "commit": doc.get("commit")}
    _INFO[real] = (key, out)
    return out


# -------------------------------------------------------------------------- archives
def pack(root: str, out: str) -> str:
    """A reproducible .tar.gz of the intact bundle, its manifest included: the files at the
    archive's top level, sorted, mtime 0, uid and gid 0, mode 0644. The archive's sha256."""
    v = verify(root)
    if not v["ok"]:
        raise BundleError("the bundle does not verify: " + "; ".join(v["problems"]))
    names = [rel for rel, _ in files(root)] + [MANIFEST]
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for rel in sorted(names):
            with open(os.path.join(root, *rel.split("/")), "rb") as f:
                data = f.read()
            ti = tarfile.TarInfo(rel)
            ti.size, ti.mtime, ti.mode = len(data), 0, 0o644
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = ""
            tar.addfile(ti, io.BytesIO(data))
    tmp = out + ".tmp"
    with open(tmp, "wb") as f:
        with gzip.GzipFile(filename="", mode="wb", fileobj=f, mtime=0, compresslevel=9) as gz:
            gz.write(raw.getvalue())
    os.replace(tmp, out)
    digest = _sha256_file(out)
    with open(out + ".sha256", "w") as f:
        f.write(f"{digest}  {os.path.basename(out)}\n")
    return digest


def _safe_name(name: str) -> str | None:
    if not name or "\0" in name or "\\" in name or name.startswith("/"):
        return None
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts) or re.match(r"^[A-Za-z]:", parts[0]):
        return None
    return "/".join(parts)


def unpack(archive: str, dest: str) -> dict:
    """Unpack a bundle archive into `dest` (replaced as a whole, only after the new files verify).
    Refuses links, devices, absolute or `..` paths and oversized archives. The manifest."""
    parent = os.path.dirname(os.path.abspath(dest))
    os.makedirs(parent, exist_ok=True)
    stage = tempfile.mkdtemp(prefix=".bundle-new-", dir=parent)
    try:
        try:
            tar = tarfile.open(archive, mode="r:gz")
        except (tarfile.TarError, OSError, EOFError) as exc:
            raise BundleError(f"not a bundle archive (.tar.gz): {exc}") from None
        with tar:
            total = count = 0
            for m in tar:
                count += 1
                if count > MAX_FILES:
                    raise BundleError(f"more than {MAX_FILES} entries in the archive")
                name = _safe_name(m.name)
                if name is None:
                    raise BundleError(f"unsafe path in the archive: {m.name!r}")
                if m.isdir():
                    continue
                if not m.isfile():
                    raise BundleError(f"not a regular file in the archive: {m.name!r}")
                total += m.size
                if total > MAX_BYTES:
                    raise BundleError(f"the archive unpacks to more than {MAX_BYTES >> 20} MiB")
                path = os.path.join(stage, *name.split("/"))
                os.makedirs(os.path.dirname(path), exist_ok=True)
                src = tar.extractfile(m)
                with open(path, "wb") as f:
                    shutil.copyfileobj(src, f)
                os.chmod(path, 0o644)
        v = verify(stage)
        if not v["ok"]:
            raise BundleError("the archive's files do not match its manifest: "
                              + "; ".join(v["problems"]))
        _swap(stage, dest)
        stage = None
        return read_manifest(dest) or {}
    finally:
        if stage and os.path.isdir(stage):
            shutil.rmtree(stage, ignore_errors=True)


def _swap(new: str, dest: str) -> None:
    """Put the folder `new` at `dest`; an old `dest` is moved aside first and removed after."""
    old = None
    if os.path.lexists(dest):
        old = tempfile.mkdtemp(prefix=".bundle-old-", dir=os.path.dirname(os.path.abspath(dest)))
        os.rmdir(old)
        os.replace(dest, old)
    os.replace(new, dest)
    os.chmod(dest, 0o755)
    if old:
        shutil.rmtree(old, ignore_errors=True)


def fetch(url: str, sha256: str, dest: str, timeout: float = 60.0) -> dict:
    """Download (http, https, file URL or a local path) a bundle archive, check its sha256 against
    the pinned one before reading it, unpack it into `dest`. The manifest."""
    want = (sha256 or "").strip().lower()
    if not _HEX64.match(want):
        raise NoBundle("a fetch needs the archive's sha256 (64 hex digits) from the release; "
                          "it never trusts an archive without one")
    parent = os.path.dirname(os.path.abspath(dest))
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".bundle-dl-", suffix=".tar.gz", dir=parent)
    try:
        h, size = hashlib.sha256(), 0
        with os.fdopen(fd, "wb") as out:
            for block in _read(url, timeout):
                size += len(block)
                if size > MAX_BYTES:
                    raise BundleError(f"{url}: larger than {MAX_BYTES >> 20} MiB")
                h.update(block)
                out.write(block)
        got = h.hexdigest()
        if got != want:
            raise BundleError(f"{url}: sha256 {got} is not the pinned {want}; nothing was "
                              "unpacked")
        doc = unpack(tmp, dest)
        return _resource(dest, doc, url, got)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _resource(dest: str, doc: dict, url: str, archive_sha: str) -> dict:
    """Mark a fetched bundle as such in its manifest (the files and their hash stay as built)."""
    doc = dict(doc, source="fetch", built_as=doc.get("source"), fetched_from=url,
               archive_sha256=archive_sha)
    tmp = os.path.join(dest, MANIFEST + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, os.path.join(dest, MANIFEST))
    return doc


def _read(url: str, timeout: float):
    if "://" not in url:
        path = os.path.expanduser(url)
    elif url.startswith("file://"):
        from urllib.parse import unquote, urlparse
        path = unquote(urlparse(url).path)
    else:
        if not url.startswith(("http://", "https://")):
            raise BundleError(f"{url}: only http, https, file URLs or a path")
        import urllib.error
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "tandemapp-bundle"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                for block in iter(lambda: r.read(1 << 16), b""):
                    yield block
        except urllib.error.HTTPError as exc:
            raise BundleError(f"{url}: HTTP {exc.code}") from None
        except (urllib.error.URLError, OSError) as exc:
            raise BundleError(f"{url}: {getattr(exc, 'reason', exc)}") from None
        return
    try:
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1 << 16), b""):
                yield block
    except OSError as exc:
        raise BundleError(f"{url}: {exc.strerror or exc}") from None


# -------------------------------------------------------------------------- the build
def find_npm(env: dict | None = None) -> str | None:
    return shutil.which("npm", path=(env if env is not None else os.environ).get("PATH"))


def _git_commit(path: str) -> str | None:
    try:
        r = subprocess.run(["git", "-C", path, "rev-parse", "HEAD"], capture_output=True,
                           text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    out = r.stdout.strip()
    return out if r.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", out) else None


def build(dashboard: str, out_dir: str | None = None, commit: str | None = None,
          npm: str | None = None, env: dict | None = None, install: bool = True,
          log=print) -> dict:
    """`npm ci` (the lockfile, exactly) and `npm run build` in `dashboard`, into its dist/ or
    `out_dir`; then the manifest. Raises BundleError with the failing step's tail."""
    env = dict(os.environ if env is None else env)
    npm = npm or find_npm(env)
    if not npm:
        raise NoBundle("npm is not on the PATH: building the page needs Node 20.19 or newer "
                          "with npm.\n" + message(os.path.join(dashboard, "dist")))
    lock = os.path.join(dashboard, "package-lock.json")
    if not os.path.isfile(lock):
        raise BundleError(f"no package-lock.json in {dashboard}: the build installs the locked "
                          "versions only")
    target = os.path.abspath(out_dir) if out_dir else os.path.join(dashboard, "dist")
    steps = []
    if install:
        steps.append([npm, "ci", "--no-audit", "--no-fund"])
    steps.append([npm, "run", "build"] + (["--", "--outDir", target, "--emptyOutDir"]
                                          if out_dir else []))
    for cmd in steps:
        log("$ " + " ".join(os.path.basename(c) if i == 0 else c for i, c in enumerate(cmd)))
        t0 = time.monotonic()
        r = subprocess.run(cmd, cwd=dashboard, env=env, capture_output=True, text=True)
        if r.returncode != 0:
            tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-30:])
            raise BundleError(f"{' '.join(cmd[1:3])} failed (exit {r.returncode}):\n{tail}")
        log(f"  ok in {time.monotonic() - t0:.1f} s")
    node = None
    nodebin = os.path.join(os.path.dirname(npm), "node")
    try:
        r = subprocess.run([nodebin if os.path.exists(nodebin) else "node", "--version"],
                           capture_output=True, text=True, env=env, timeout=10)
        node = r.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        pass
    return write_manifest(target, "build", commit=commit or _git_commit(dashboard),
                          lockfile=lock, node=node)


def ensure(root: str | None = None, url: str | None = None, sha256: str | None = None,
           env: dict | None = None, log=print) -> dict:
    """A checked bundle in `root`/dashboard/dist: the one there when intact, else a build when npm
    is on the PATH, else a fetch when `url` and `sha256` are given. Otherwise BundleError with
    the actionable text. The manifest."""
    root = root or app_root()
    dist = dist_dir(root)
    env = dict(os.environ if env is None else env)
    url = url or env.get("TANDEM_DASHBOARD_URL") or None
    sha256 = sha256 or env.get("TANDEM_DASHBOARD_SHA256") or None
    if os.path.isfile(os.path.join(dist, "index.html")):
        v = verify(dist)
        if v["ok"]:
            log(f"page    {dist}: bundle {v['sha256'][:16]}, intact")
            return read_manifest(dist) or {}
        if read_manifest(dist) is None:
            # built by a plain `npm run build`: record what is there
            doc = write_manifest(dist, "record", commit=_git_commit(root))
            log(f"page    {dist}: bundle {doc['sha256'][:16]}, recorded")
            return doc
        log(f"page    {dist} does not verify ({'; '.join(v['problems'])}); replacing it")
    if find_npm(env):
        return build(os.path.join(root, "dashboard"), env=env, log=log)
    if url:
        if not sha256:
            raise NoBundle("TANDEM_DASHBOARD_URL is set but TANDEM_DASHBOARD_SHA256 is not: "
                              "a fetched bundle is only used with its pinned sha256")
        log(f"fetch   {url}")
        return fetch(url, sha256, dist)
    raise NoBundle(message(dist))
