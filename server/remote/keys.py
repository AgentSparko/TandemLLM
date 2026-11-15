"""`tandem remote unpair`: the authorized_keys line of the key this call came with goes, nothing else.

The call's key is known two ways, and one is needed:

  --key-id ID   the forced command of the key's line names it (`command="/bin/bash DIR/bin/tandem
                remote --key-id ID",...`): the line that carries exactly that command goes
  SSH_USER_AUTH sshd's file of the methods this session authenticated with (ExposeAuthInfo yes): the
                line that carries that public key and a forced command of this install goes

A line without this install's forced command (an operator's own login key) is never removed, and
neither is anything when more than one line matches. The file keeps its mode and every other line
byte for byte. Standard library only.
"""

from __future__ import annotations

import os
import re
import tempfile

from server.remote.grammar import EXIT_REFUSED, Refusal

KEY_TYPES = re.compile(r"^(ssh-(ed25519|rsa|dss)|ecdsa-sha2-nistp(256|384|521)|"
                       r"sk-(ssh-ed25519|ecdsa-sha2-nistp256)@openssh\.com)$")


def split_line(line: str) -> tuple[str, str, str, str] | None:
    """(options, key type, key blob, comment) of one authorized_keys line, None for a comment or
    blank. Options are comma-separated before the key type; a quoted value may hold commas and
    spaces (sshd's rules: `\\"` inside quotes)."""
    s = line.strip()
    if not s or s.startswith("#"):
        return None
    i, quoted, opts_end = 0, False, None
    first = s.split(None, 1)[0]
    if KEY_TYPES.match(first):
        opts_end = 0
    else:
        while i < len(s):
            c = s[i]
            if c == "\\" and quoted and i + 1 < len(s):
                i += 2
                continue
            if c == '"':
                quoted = not quoted
            elif c in " \t" and not quoted:
                opts_end = i
                break
            i += 1
        if opts_end is None:
            return None
    opts = s[:opts_end]
    rest = s[opts_end:].split(None, 2)
    if len(rest) < 2 or not KEY_TYPES.match(rest[0]):
        return None
    return opts, rest[0], rest[1], rest[2] if len(rest) > 2 else ""


def forced_command(opts: str) -> str | None:
    m = re.search(r'(?:^|,)command="((?:[^"\\]|\\.)*)"', opts)
    return m.group(1).replace('\\"', '"') if m else None


def auth_key(path: str | None) -> tuple[str, str] | None:
    """(type, blob) of the public key in sshd's SSH_USER_AUTH file, or None."""
    if not path:
        return None
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                parts = ln.split()
                if len(parts) >= 3 and parts[0] == "publickey" and KEY_TYPES.match(parts[1]):
                    return parts[1], parts[2]
    except OSError:
        return None
    return None


def _ours(cmd: str | None, control: str) -> bool:
    """The forced command runs this install's `tandem remote` (with or without bash in front)."""
    if not cmd:
        return False
    words = cmd.split()
    if words and os.path.basename(words[0]) == "bash":
        words = words[1:]
    return len(words) >= 2 and os.path.realpath(words[0]) == os.path.realpath(control) and words[1] == "remote"


def unpair(control: str, key_id: str | None, user_auth: str | None, path: str | None = None) -> dict:
    """Remove the one line of the call's key; a Refusal when the key is not known or not one line."""
    path = path or os.path.expanduser("~/.ssh/authorized_keys")
    blob = auth_key(user_auth)
    if not key_id and not blob:
        raise Refusal("key_unknown", "this call's key is not known: its forced command names no "
                                     "--key-id and sshd gives no SSH_USER_AUTH (ExposeAuthInfo); "
                                     "nothing was removed", EXIT_REFUSED)
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except FileNotFoundError:
        raise Refusal("key_unknown", f"{path} does not exist: nothing was removed", EXIT_REFUSED) from None
    lines = raw.decode("utf-8", "surrogateescape").split("\n")
    hits = []
    for i, ln in enumerate(lines):
        p = split_line(ln)
        if not p:
            continue
        cmd = forced_command(p[0])
        if not _ours(cmd, control):
            continue
        if key_id and re.search(r"(?:^|\s)--key-id\s+" + re.escape(key_id) + r"(?:\s|$)", cmd or ""):
            if blob and (p[1], p[2]) != blob:
                continue
            hits.append(i)
        elif not key_id and blob and (p[1], p[2]) == blob:
            hits.append(i)
    if not hits:
        raise Refusal("key_unknown", "no line of authorized_keys carries this call's key with this "
                                     "install's forced command: nothing was removed", EXIT_REFUSED)
    if len(hits) > 1:
        raise Refusal("key_ambiguous", f"{len(hits)} lines carry this call's key: nothing was removed",
                      EXIT_REFUSED)
    gone = split_line(lines[hits[0]])
    del lines[hits[0]]
    mode = os.stat(path).st_mode & 0o777
    fd, tmp = tempfile.mkstemp(prefix=".authorized_keys-", dir=os.path.dirname(path))
    with os.fdopen(fd, "wb") as fh:
        fh.write("\n".join(lines).encode("utf-8", "surrogateescape"))
    os.chmod(tmp, mode)
    os.replace(tmp, path)
    return {"removed": {"type": gone[1], "comment": gone[3][:80], "key_id": key_id},
            "left": sum(1 for ln in lines if split_line(ln))}
