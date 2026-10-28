"""Tests never touch this machine's own TandemNode (SRV-745).

A controller reads this machine through its agent at 127.0.0.1:7700 with the loopback secret in
~/.tandemllm/node (proxy/app.py node_agent) and tells it that it is the fleet's own (PUT
/node/fleet). A test controller did so on a Spark with a real agent: the suite rewrote the real
`fleet.json` to a throwaway controller until the real one claimed it again on its next start.
A test runs its own agent (tests/test_node_agent.py agent_on: a temp state dir, a free port) or
none (TANDEM_NODE_AGENT=off, the Proxy helper's default), and

    import realnode
    realnode.guard()        in a test process: an audit hook refuses (RealNode) any open, write,
                            rename or removal under the real state dir and any connection to
                            port 7700 on loopback, before the call, and records it
    realnode.check()        after each test (proxy_util.run, scriptmode.run, conftest's fixture):
                            an AssertionError naming every refused call and every file of the real
                            claim or trust store that changed since the last check (a child
                            process's write too)

TANDEM_TEST_REAL_NODE=1 lets a test that means it reach the real node; no test here does.
"""

from __future__ import annotations

import contextlib
import hashlib
import ipaddress
import os
import pwd
import sys
import threading

OPT_IN = "TANDEM_TEST_REAL_NODE"
PORT = 7700                                 # proxy/nodeagent.py DEFAULT_PORT
DIR = os.path.join(".tandemllm", "node")    # proxy/nodeagent.py DEFAULT_DIR, under the home
HOME = pwd.getpwuid(os.getuid()).pw_dir     # this account's home: a test may set HOME
#: the real state dir, as written and resolved
ROOTS = sorted({os.path.join(HOME, DIR), os.path.realpath(os.path.join(HOME, DIR))})
#: audit events whose path arguments are checked; `open` covers builtins.open and os.open
PATH_EVENTS = frozenset(("open", "os.mkdir", "os.rename", "os.remove", "os.rmdir", "os.chmod",
                         "os.chown", "os.link", "os.symlink", "os.truncate", "os.utime",
                         "os.mkfifo", "os.mknod", "shutil.rmtree", "shutil.copyfile",
                         "shutil.copymode", "shutil.copystat", "shutil.move", "sqlite3.connect"))


class RealNode(PermissionError):
    """A test reached the real TandemNode's state or port."""


_on = False
_own = threading.local()
_taken: list[str] = []
_seen: dict[str, str] = {}


def _inside(p) -> str | None:
    if isinstance(p, int) or p is None:
        return None
    try:
        p = os.path.abspath(os.fsdecode(p))
    except (TypeError, ValueError):
        return None
    for r in ROOTS:
        if p == r or p.startswith(r + os.sep):
            return p
    return None


def _loopback(host) -> bool:
    if not isinstance(host, str):
        return False
    if host in ("", "localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        return False
    return ip.is_loopback or ip.is_unspecified


def why(event: str, args: tuple) -> str | None:
    """What a call would touch of the real node, or None (the hook's decision, no I/O)."""
    if event == "socket.connect":
        addr = args[1] if len(args) > 1 else None
        if isinstance(addr, tuple) and len(addr) >= 2 and addr[1] == PORT and _loopback(addr[0]):
            return f"connects to the real TandemNode at {addr[0]}:{PORT}"
        return None
    if event in PATH_EVENTS:
        for a in args:
            p = _inside(a)
            if p is not None:
                return f"{event} {p}: the real TandemNode's state dir"
    return None


def _hook(event: str, args: tuple) -> None:
    if event != "socket.connect" and event not in PATH_EVENTS:
        return
    if getattr(_own, "on", False):
        return
    w = why(event, args)
    if w is None:
        return
    _taken.append(w)
    raise RealNode(f"SRV-745: a test {w}; give it its own state dir and agent "
                   f"({OPT_IN}=1 allows it)")


@contextlib.contextmanager
def allowed():
    """This thread's calls pass the hook (the guard's own reads)."""
    _own.on = True
    try:
        yield
    finally:
        _own.on = False


def state() -> dict[str, str]:
    """{path: sha256} of the real claim (fleet.json) and trust store; a missing file is absent."""
    out = {}
    with allowed():
        for r in ROOTS:
            names = [os.path.join(r, "fleet.json")]
            trust = os.path.join(r, "trust")
            try:
                names += [os.path.join(trust, n) for n in sorted(os.listdir(trust))]
            except OSError:
                pass
            for p in names:
                try:
                    with open(p, "rb") as f:
                        out[p] = hashlib.sha256(f.read()).hexdigest()
                except OSError:
                    pass
    return out


def guard() -> None:
    """Installs the hook in this process once; nothing with TANDEM_TEST_REAL_NODE=1."""
    global _on
    if _on or os.environ.get(OPT_IN) == "1":
        return
    _seen.update(state())
    sys.addaudithook(_hook)
    _on = True


def check() -> None:
    """Fails the test that just ran when it reached the real node; the next one starts clean."""
    if not _on:
        return
    now = state()
    changed = [f"{p} changed" for p in sorted(set(_seen) | set(now)) if _seen.get(p) != now.get(p)]
    taken = list(dict.fromkeys(_taken))
    del _taken[:]
    _seen.clear()
    _seen.update(now)
    if taken or changed:
        raise AssertionError("SRV-745: the test reached the real TandemNode: "
                             + "; ".join(taken + changed))
