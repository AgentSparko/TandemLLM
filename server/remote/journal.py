"""The journal of `tandem remote`: one JSON line a call, refused calls included.

    run/remote/journal.jsonl     {at, verb, args, exit, ms, via, key?, from?, error?, job?}

`at` is UTC, `ms` the call's duration, `args` the command's tokens after the verb with the value of
every secret-looking key cut out (`KEY=VALUE` and `--option VALUE` alike), each cut to 200
characters. Past CAP_BYTES the file moves to journal.jsonl.1 (and .1 to .2, up to KEEP files);
writers take an exclusive lock, so calls at the same time write whole lines. A journal that cannot
be written never stops a call: the line goes to stderr instead. Standard library only.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import sys
import time

CAP_BYTES = 4 << 20
KEEP = 3
MAX_ARG = 200
#: a key or option name that may carry a secret: its value is never written
SECRET_NAME = re.compile(r"(?i)(?:^|[_-])(?:token|key|pin|otp|auth|cookie|signature|credentials?)$|"
                         r"secret|passw|passphrase|private[_-]?key|api[_-]?key|bearer")
REDACTED = "[redacted]"


def redact_args(tokens: list[str]) -> list[str]:
    """`tokens` with the value of each secret-looking `KEY=VALUE` or `--name VALUE` replaced."""
    out, hide_next = [], False
    for t in tokens:
        if hide_next:
            out.append(REDACTED)
            hide_next = False
            continue
        name, eq, _ = t.partition("=")
        if eq and SECRET_NAME.search(name.lstrip("-")):
            out.append(f"{name}={REDACTED}")
            continue
        if t.startswith("--") and not eq and SECRET_NAME.search(t[2:]):
            hide_next = True
        out.append(t if len(t) <= MAX_ARG else t[:MAX_ARG] + "...")
    return out


def utc(t: float | None = None) -> str:
    t = time.time() if t is None else t
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int((t % 1) * 1000):03d}Z"


def _rotate(path: str) -> None:
    for i in range(KEEP - 1, 0, -1):
        if os.path.exists(f"{path}.{i}"):
            os.replace(f"{path}.{i}", f"{path}.{i + 1}")
    os.replace(path, f"{path}.1")


def write(path: str, row: dict) -> None:
    """Append `row` as one line (rotating past CAP_BYTES); on an OSError the line goes to stderr."""
    line = json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        with open(path + ".lock", "a") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            try:
                if os.path.exists(path) and os.path.getsize(path) + len(line) > CAP_BYTES:
                    _rotate(path)
                fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                with os.fdopen(fd, "a") as fh:
                    fh.write(line)
            finally:
                fcntl.flock(lk, fcntl.LOCK_UN)
    except OSError as exc:
        print(f"[remote] the journal {path} was not written ({exc.strerror}): {line}", end="", file=sys.stderr)


def read(path: str, n: int = 50) -> list[dict]:
    """The last `n` rows (this file only)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()[-n:]
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out
