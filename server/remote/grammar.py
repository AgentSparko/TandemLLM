"""The grammar of `tandem remote` (engine-ctl-v1): one command line in, one validated call out.

The line is split with `shlex` (POSIX rules, no comments) and never reaches a shell. Every token is
printable ASCII without a space; the first is a verb of VERBS, the second a subcommand where the verb
has them, and the rest are the options and arguments that verb takes, each checked here. Anything
else is a `Refusal` with its code and exit status (contracts/engine-ctl-v1/verbs.json). Values that
name settings keys are checked again by the overlay (server/remote/overlay.py); this file knows only
their shape. Standard library only, no state.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

CTL = "engine-ctl-v1"
CTL_VERSION = "1.0"

#: exit statuses (contracts/engine-ctl-v1/verbs.json `exit`)
EXIT_OK, EXIT_NOT_OK, EXIT_USAGE, EXIT_REFUSED, EXIT_BUSY, EXIT_INTERNAL = 0, 1, 2, 3, 4, 5

MAX_LINE = 8192
MAX_TOKENS = 64
MAX_TOKEN = 1024
TOKEN_RE = re.compile(r"^[\x21-\x7e]+$")
KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}(\.[A-Za-z_][A-Za-z0-9_]{0,63}){0,7}$")
KEY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
#: a restart's wait for idle: at least, at most, by default (the Setup Guide's 600 s)
DEADLINE_MIN_S, DEADLINE_MAX_S = 10, 86400
#: `--when at=` at most this far ahead
AT_MAX = timedelta(days=7)
LINES_MAX = 5000

WHEN_OPTS = ("--when", "--deadline")


class Refusal(Exception):
    """A call that is not run: `code` (verbs.json `errors`), the exit status, a sentence."""

    def __init__(self, code: str, message: str, exit_code: int = EXIT_USAGE, **extra):
        super().__init__(message)
        self.code, self.exit_code, self.extra = code, exit_code, extra

    def body(self) -> dict:
        return {"code": self.code, "message": str(self), **self.extra}


@dataclass
class Call:
    verb: str
    sub: str | None = None
    opts: dict = field(default_factory=dict)
    sets: list = field(default_factory=list)          # [(KEY, VALUE)] in the order given
    unsets: list = field(default_factory=list)        # [KEY]
    pos: list = field(default_factory=list)           # positional arguments of the subcommand
    tokens: list = field(default_factory=list)        # the whole command, for the journal

    @property
    def name(self) -> str:
        return f"{self.verb} {self.sub}" if self.sub else self.verb


# verb -> subcommand (None: the verb has none) -> (options: {name: kind}, positional spec)
#   kinds: flag, int:<lo>:<hi>, enum:<a>|<b>, when, key (repeatable), confirm
#   positional: "none", "keys" (KEY ...), "changes" (KEY=VALUE ...), "version" (one integer),
#               "mode" (engine|chat)
_WHEN = {"--when": "when", "--deadline": f"int:{DEADLINE_MIN_S}:{DEADLINE_MAX_S}"}
_CONFIRM = {"--confirm": "confirm", "--confirm-fingerprint": "confirm"}
VERBS: dict = {
    "status": {None: ({}, "none")},
    "start": {None: ({}, "none")},
    "stop": {None: ({}, "none")},
    "restart": {None: (dict(_WHEN), "none")},
    "mode": {"get": ({}, "none"),
             "set": ({**_WHEN, "--ext": "module"}, "mode")},
    "settings": {"get": ({}, "keys"),
                 "plan": ({"--unset": "key"}, "changes"),
                 "apply": ({"--unset": "key", **_CONFIRM, **_WHEN}, "changes"),
                 "versions": ({}, "none"),
                 "restore": ({**_CONFIRM, **_WHEN}, "version")},
    "logs": {None: ({"--follow": "flag", "--lines": f"int:1:{LINES_MAX}",
                     "--which": "enum:engine|launcher"}, "none")},
    "doctor": {None: ({}, "none")},
    "unpair": {None: ({}, "none")},
}
#: verbs of later work packages of the same contract: refused as unknown until they exist
PLANNED = ("ext", "upgrade", "models", "token", "pair")


def split(line: str) -> list[str]:
    """The command line as tokens, or a Refusal: never a shell, never a control character."""
    if len(line) > MAX_LINE:
        raise Refusal("bad_command", f"the command is longer than {MAX_LINE} characters")
    if any(ord(c) < 0x20 or ord(c) == 0x7f for c in line):
        raise Refusal("bad_command", "the command holds a control character")
    try:
        lex = shlex.shlex(line, posix=True, punctuation_chars=False)
        lex.whitespace_split = True
        lex.commenters = ""
        tokens = list(lex)
    except ValueError as exc:
        raise Refusal("bad_command", f"the command does not split: {exc}") from None
    if not tokens:
        raise Refusal("no_command", "no command: this key runs `tandem remote` verbs only, never a shell",
                      EXIT_REFUSED)
    if len(tokens) > MAX_TOKENS:
        raise Refusal("bad_command", f"more than {MAX_TOKENS} arguments")
    for t in tokens:
        if len(t) > MAX_TOKEN or not TOKEN_RE.match(t):
            raise Refusal("bad_command", "an argument holds a space, a non-ASCII or a control character, "
                                         f"or is longer than {MAX_TOKEN} characters")
    return tokens


def parse_when(value: str, now: datetime | None = None) -> dict:
    """`now`, `idle` or `at=<ISO 8601 time with a zone>` (at most AT_MAX ahead, not in the past)."""
    if value in ("now", "idle"):
        return {"mode": value}
    if value.startswith("at="):
        raw = value[3:]
        try:
            at = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith("Z") else raw)
        except ValueError:
            raise Refusal("bad_value", f"--when at={raw}: not an ISO 8601 time") from None
        if at.tzinfo is None:
            raise Refusal("bad_value", f"--when at={raw}: the time needs a zone (Z or +hh:mm)")
        now = now or datetime.now(timezone.utc)
        if at < now - timedelta(seconds=5):
            raise Refusal("bad_value", f"--when at={raw}: the time has passed")
        if at > now + AT_MAX:
            raise Refusal("bad_value", f"--when at={raw}: more than {AT_MAX.days} days ahead")
        return {"mode": "at", "at": at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    raise Refusal("bad_value", f"--when {value}: now, idle or at=<ISO time>")


def _value(kind: str, opt: str, raw: str, now=None):
    if kind.startswith("int:"):
        lo, hi = (int(x) for x in kind.split(":")[1:])
        if not re.fullmatch(r"[0-9]{1,6}", raw) or not lo <= int(raw) <= hi:
            raise Refusal("bad_value", f"{opt} takes a whole number {lo}..{hi}")
        return int(raw)
    if kind.startswith("enum:"):
        allowed = kind[5:].split("|")
        if raw not in allowed:
            raise Refusal("bad_value", f"{opt} takes {' or '.join(allowed)}")
        return raw
    if kind == "when":
        return parse_when(raw, now)
    if kind == "key":
        if not KEY_RE.match(raw):
            raise Refusal("bad_value", f"{opt} takes a settings key (A-Z, 0-9, _)")
        return raw
    if kind == "confirm":
        if not re.fullmatch(r"[0-9a-f]{16}", raw):
            raise Refusal("bad_value", f"{opt} takes the 16 hex digits a plan gave")
        return raw
    if kind == "module":
        if not MODULE_RE.match(raw):
            raise Refusal("bad_value", f"{opt} takes a dotted module name")
        return raw
    raise AssertionError(kind)


def parse(tokens: list[str], now: datetime | None = None) -> Call:
    """A validated Call for `tokens`, or a Refusal."""
    verb = tokens[0]
    if verb not in VERBS:
        later = " (not in this engine yet)" if verb in PLANNED else ""
        raise Refusal("unknown_verb", f"unknown verb {verb[:40]!r}{later}; verbs: {', '.join(VERBS)}")
    subs = VERBS[verb]
    rest = tokens[1:]
    sub = None
    if None not in subs:
        if not rest or rest[0] not in subs:
            got = f" {rest[0][:40]!r}" if rest else ""
            raise Refusal("unknown_subcommand", f"{verb} needs one of {', '.join(subs)}; got{got or ' none'}")
        sub, rest = rest[0], rest[1:]
    opts_spec, pos_spec = subs[sub]
    call = Call(verb=verb, sub=sub, tokens=list(tokens))
    i = 0
    while i < len(rest):
        t = rest[i]
        if t.startswith("--"):
            name, eq, raw = t.partition("=")
            if name not in opts_spec:
                raise Refusal("unknown_option", f"{call.name} takes no option {name[:40]}"
                              + (f" (it takes {', '.join(opts_spec)})" if opts_spec else ""))
            kind = opts_spec[name]
            if kind == "flag":
                if eq:
                    raise Refusal("bad_value", f"{name} takes no value")
                val = True
            else:
                if not eq:
                    if i + 1 >= len(rest):
                        raise Refusal("missing_argument", f"{name} needs a value")
                    i += 1
                    raw = rest[i]
                val = _value(kind, name, raw, now)
            if kind == "key":
                call.unsets.append(val)
            elif name in call.opts:
                raise Refusal("duplicate_option", f"{name} given twice")
            else:
                call.opts[name] = val
        else:
            call.pos.append(t)
        i += 1
    _positional(call, pos_spec)
    if "--deadline" in call.opts and call.opts.get("--when", {}).get("mode") == "now":
        raise Refusal("bad_value", "--deadline is the wait for idle: it goes with --when idle or at=")
    if call.unsets and len(set(call.unsets)) != len(call.unsets):
        raise Refusal("duplicate_option", "a key is given to --unset twice")
    return call


def _positional(call: Call, spec: str) -> None:
    pos = call.pos
    if spec == "none":
        if pos:
            raise Refusal("extra_argument", f"{call.name} takes no argument; got {pos[0][:40]!r}")
    elif spec == "keys":
        for k in pos:
            if not KEY_RE.match(k):
                raise Refusal("bad_value", f"{k[:40]!r} is not a settings key")
    elif spec == "changes":
        seen = set(call.unsets)
        for t in pos:
            k, eq, v = t.partition("=")
            if not eq or not KEY_RE.match(k):
                raise Refusal("bad_value", f"{t[:40]!r}: a change is KEY=VALUE")
            if k in seen:
                raise Refusal("duplicate_option", f"{k} is changed twice")
            seen.add(k)
            call.sets.append((k, v))
        if not call.sets and not call.unsets:
            raise Refusal("missing_argument", f"{call.name} needs at least one KEY=VALUE or --unset KEY")
    elif spec == "version":
        if len(pos) != 1 or not re.fullmatch(r"[0-9]{1,6}", pos[0]):
            raise Refusal("missing_argument" if not pos else "bad_value",
                          f"{call.name} takes one version number")
        call.opts["version"] = int(pos[0])
    elif spec == "mode":
        if len(pos) != 1 or pos[0] not in ("engine", "chat"):
            raise Refusal("missing_argument" if not pos else "bad_value", "mode set takes engine or chat")
        call.opts["mode"] = pos[0]
    else:
        raise AssertionError(spec)
