"""`tandem pair`: open a pairing window on this engine host and print the code, the SSH host key's
fingerprint and the address TandemApp reaches the engine at (engine-pair-v1, docs/pairing.md).

    bin/tandem pair            a new window (an open one is closed): 5 minutes, 5 tries, single use
    bin/tandem pair --close    close the open window
    bin/tandem pair --status   the window's state (no code)
    bin/tandem pair --json     the answer as one JSON document (the code included)

The window lives in the engine process (setup mode or serving), which answers the exchange on its
port; this command asks it over loopback with the window key (run/remote/pair-local.key, readable by
the installing user only). It runs on the engine host, without SSH: pairing is how TandemApp's SSH
key gets here. Exit 0 ok, 1 not done (the reason in one line). Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

from server.pairing.http import KEY_HEADER, PREFIX
from server.remote.paths import Tree


def _call(port: int, method: str, rest: str, key: str | None, body: dict | None = None,
          timeout: float = 10.0) -> tuple[int, dict]:
    url = f"http://127.0.0.1:{port}{PREFIX}{'/' + rest if rest else ''}"
    data = json.dumps(body or {}).encode() if method == "POST" else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", "Accept": "application/json",
                                          **({KEY_HEADER: key} if key else {})})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except ValueError:
            return exc.code, {}


def _key(tree: Tree) -> str | None:
    try:
        with open(os.path.join(tree.remote, "pair-local.key")) as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def render(doc: dict) -> list[str]:
    """The window's lines; the code, the fingerprint and the address last."""
    mins = max(1, round(doc.get("expires_in", 300) / 60))
    lines = [f"[pair] a pairing window is open for {mins} minutes ({doc.get('tries', 5)} tries, single use).",
             "       In TandemApp: Add engine, pick this host (or type its address), then type the code."]
    addrs = [f"{a}:{doc['port']}" for a in doc.get("addresses") or []]
    if doc.get("lan"):
        where = addrs[0] + (f"   (also {', '.join(addrs[1:])})" if len(addrs) > 1 else "")
    else:
        lines.append(f"       The engine listens on {doc.get('host')} only: TandemApp on another machine cannot "
                     "reach it. Set HOST=0.0.0.0 in run/local.env and restart to pair over the network.")
        where = f"127.0.0.1:{doc['port']}   (this machine only)"
    lines += [f"  Code:          {doc['code']}",
              f"  Fingerprint:   {doc['fingerprint']}   ({doc.get('host_key_type', 'SSH')} SSH host key)",
              f"  Address:       {where}"]
    return lines


def main(argv: list[str] | None = None, tree: Tree | None = None, out=None) -> int:
    out = out or sys.stdout
    ap = argparse.ArgumentParser(prog="tandem pair", description="open a pairing window for TandemApp")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT") or 8000))
    ap.add_argument("--close", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    tree = tree or Tree()

    def say(line: str) -> None:
        print(line, file=out, flush=True)

    try:
        if a.status:
            code, doc = _call(a.port, "GET", "", None)
        else:
            key = _key(tree)
            if key is None:
                say(f"[pair] no window key ({os.path.join(tree.remote, 'pair-local.key')}): the engine has not "
                    "started here yet. Start it (tandem start, or setup mode), then tandem pair")
                return 1
            code, doc = _call(a.port, "POST", "close" if a.close else "open", key)
    except (OSError, ValueError) as exc:
        say(f"[pair] the engine does not answer on 127.0.0.1:{a.port} ({getattr(exc, 'reason', exc)}): start it "
            "(tandem start, or setup mode), then tandem pair")
        return 1
    if code != 200:
        e = doc.get("error") if isinstance(doc.get("error"), dict) else {}
        if code == 404 and not e:
            e = {"message": "this engine has no pairing routes (an older release): upgrade it"}
        say(f"[pair] not done: {e.get('message') or f'HTTP {code}'}")
        return 1
    if a.json:
        say(json.dumps(doc, sort_keys=True))
        return 0
    if a.status:
        say(f"[pair] window: {doc.get('state')}" + (f", {doc.get('expires_in')} s and {doc.get('tries_left')} "
                                                    f"tries left" if doc.get("open") else ""))
        return 0
    if a.close:
        say("[pair] the window is closed" if doc.get("closed") else "[pair] no window was open")
        return 0
    for ln in render(doc):
        say(ln)
    return 0
