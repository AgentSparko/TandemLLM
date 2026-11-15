"""`tandem remote unpair`: the authorized_keys line of the key this call came with goes, nothing else.

The call's key is known two ways, and one is needed:

  --key-id ID   the forced command of the key's line names it (`command="/bin/bash DIR/bin/tandem
                remote --key-id ID",...`): the line that carries exactly that command goes
  SSH_USER_AUTH sshd's file of the methods this session authenticated with (ExposeAuthInfo yes): the
                line that carries that public key and a forced command of this install goes

A line without this install's forced command (an operator's own login key) is never removed, and
neither is anything when more than one line matches. The file keeps its mode and every other line
byte for byte.

`install` is pairing's half (server/pairing, engine-pair-v1): it appends one key's forced-command
line (`key_line`) and touches no other line. ~/.ssh is created 0700 and the file 0600 when they are
missing; a ~/.ssh or a file that is a symbolic link, belongs to another user or is writable by group
or others (sshd's StrictModes would ignore it) is refused, and so is a key that already has a line
of its own (an operator's login line for the same key would run first and give it a shell). Standard
library only.
"""

from __future__ import annotations

import fcntl
import os
import pwd
import re
import stat
import tempfile

from server.remote.grammar import EXIT_REFUSED, Refusal

KEY_TYPES = re.compile(r"^(ssh-(ed25519|rsa|dss)|ecdsa-sha2-nistp(256|384|521)|"
                       r"sk-(ssh-ed25519|ecdsa-sha2-nistp256)@openssh\.com)$")


#: the options after the forced command: no terminal, no forwarding of any kind
FORCED_OPTS = "no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding"


def key_line(control: str, key_id: str, key_type: str, key_b64: str) -> str:
    """The authorized_keys line of a controller's key: `tandem remote` forced, its id named."""
    return (f'command="/bin/bash {control} remote --key-id {key_id}",{FORCED_OPTS} '
            f"{key_type} {key_b64} {key_id}")


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


# ------------------------------------------------------------------------------------- install (pairing)
class Unsafe(Exception):
    """authorized_keys cannot take the line: `code` and one sentence."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def default_path() -> str:
    try:
        home = pwd.getpwuid(os.getuid()).pw_dir
    except KeyError:                      # no passwd entry (a bare container): the environment's
        home = os.path.expanduser("~")
    return os.path.join(home, ".ssh", "authorized_keys")


def _check(st: os.stat_result, what: str, uid: int, kind: str) -> None:
    if stat.S_ISLNK(st.st_mode):
        raise Unsafe(f"{kind}_link", f"{what} is a symbolic link: pairing writes only a real file")
    if not (stat.S_ISDIR(st.st_mode) if kind == "ssh_dir" else stat.S_ISREG(st.st_mode)):
        raise Unsafe(f"{kind}_type", f"{what} is not a {'directory' if kind == 'ssh_dir' else 'regular file'}")
    if st.st_uid != uid:
        raise Unsafe(f"{kind}_owner", f"{what} belongs to another user (uid {st.st_uid})")
    if st.st_mode & 0o022:
        raise Unsafe(f"{kind}_mode", f"{what} is writable by group or others (mode "
                                     f"{stat.S_IMODE(st.st_mode):o}): sshd ignores it; chmod go-w it")


def check_place(path: str, uid: int | None = None) -> None:
    """An Unsafe unless `path` and its directory may take a line (missing ones may be created)."""
    uid = os.getuid() if uid is None else uid
    for p, kind in ((os.path.dirname(path), "ssh_dir"), (path, "authorized_keys")):
        try:
            st = os.lstat(p)
        except FileNotFoundError:
            return
        _check(st, p, uid, kind)


def _matches_id(cmd: str | None, key_id: str) -> bool:
    return bool(cmd) and re.search(r"(?:^|\s)--key-id\s+" + re.escape(key_id) + r"(?:\s|$)", cmd) is not None


def install(control: str, key_id: str, key_type: str, key_b64: str, path: str | None = None,
            uid: int | None = None) -> dict:
    """Append the key's forced-command line; {added, created, line}. `added` is False when the very
    same line (this key, this id, this install's forced command) is there already. Unsafe otherwise."""
    path = path or default_path()
    uid = os.getuid() if uid is None else uid
    check_place(path, uid)
    d = os.path.dirname(path)
    if not os.path.lexists(d):
        try:
            os.mkdir(d, 0o700)
            os.chmod(d, 0o700)
        except OSError as exc:
            raise Unsafe("ssh_dir_create", f"{d} cannot be created ({exc.strerror})") from None
    flags = os.O_RDWR | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    created = not os.path.lexists(path)
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise Unsafe("authorized_keys_open", f"{path} cannot be opened ({exc.strerror})") from None
    try:
        st = os.fstat(fd)
        _check(st, path, uid, "authorized_keys")            # the file opened, not a name checked earlier
        if created:
            os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        raw, chunk = b"", os.read(fd, 1 << 16)
        while chunk:
            raw += chunk
            chunk = os.read(fd, 1 << 16)
        line = key_line(control, key_id, key_type, key_b64)
        for ln in raw.decode("utf-8", "surrogateescape").split("\n"):
            p = split_line(ln)
            if not p:
                continue
            cmd = forced_command(p[0])
            ours = _ours(cmd, control)
            if (p[1], p[2]) == (key_type, key_b64):
                if ours and _matches_id(cmd, key_id):
                    return {"added": False, "created": False, "line": line}
                raise Unsafe("key_present", "this key already has a line of its own in authorized_keys: "
                                            "nothing was added")
            if ours and _matches_id(cmd, key_id):
                raise Unsafe("key_id_taken", f"another key holds the id {key_id}: nothing was added")
        data = (b"\n" if raw and not raw.endswith(b"\n") else b"") + line.encode() + b"\n"
        os.write(fd, data)
        os.fsync(fd)
        return {"added": True, "created": created, "line": line}
    finally:
        os.close(fd)
