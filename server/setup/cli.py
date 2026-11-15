"""`bin/tandem setup`: the Setup Guide in the terminal (ENG-435), for an install with no page.

It asks the engine's own Setup API (/v1/setup/*, server/setup_api.py) over HTTP on this box, the
routes TandemApp's Setup page calls, so every answer (the models, the box, the estimate, the plan,
each refusal, the job) is the engine's and is the same one the page gets. It rebuilds none of the
rules: a selection it sends is checked by the engine (server/setup/estimate.py check). What it does
itself is what the page does in the browser before it asks: start from what the box runs now
(GET .../current), and the page's own switches (dashboard/src/setup/state.ts and deps.ts): a tool
switched off takes the tools that need it along, a tool switched on brings its `with_parent` tools,
a profile with a shorter largest window takes the window down, a window snaps to the model's step.

Why HTTP and not in-process: the routes need the server they belong to. One setup job at a time is
a lock in that server (setup_api._JOB_LOCK); the requests in flight (409 busy), Wren, the admin rule
and setup mode are its state; the job's runner is spawned by it. A second process calling the same
functions would pass that lock and read none of that state. So the terminal is one more client.

    bin/tandem setup                    the guide, step by step (a terminal only)
    bin/tandem setup models             the models (GET /v1/setup/models)
    bin/tandem setup show MODEL         a model: weights, community packs, tools, ranges, settings
    bin/tandem setup device             the box (GET /v1/setup/device)
    bin/tandem setup status             the engine and the running or newest job
    bin/tandem setup current MODEL      what the box runs now, as a selection
    bin/tandem setup community [--sync] the community packs and the sync state
    bin/tandem setup weights            the weights on the box
    bin/tandem setup estimate MODEL [choices]     the memory estimate
    bin/tandem setup plan MODEL [choices]         the review: errors, warnings, changes, downloads
    bin/tandem setup apply MODEL [choices] [--dry-run] [--remove-after P] [--restart] [--yes]
    bin/tandem setup job [ID] [--no-follow]       a job's steps and log, to its end
    bin/tandem setup retry ID [--restart] [--yes] a failed or rolled back job again
    bin/tandem setup ask QUESTION [--model M [choices]]   a question to Wren (words only)

    choices: --profile ID, --on TOOL, --off TOOL (in order, repeatable), --slots N, --window N,
             --kv-pool on|off, --floor-gb X, --set FIELD=VALUE (a method or advanced setting),
             --device ID, --from current|recommended, --selection FILE (JSON, - for stdin)
    global:  --url URL, --json, --yes, --start-setup, --keep-setup, --timeout S

Every prompt has a flag; --yes answers yes; --json prints one JSON object on stdout:
{command, ok, url, mode, selection, notices, result, error}: `result` is the engine's answer as it
came (for a job, the final job), `error` {status, code, message} or null.

A running engine is never stopped unasked: apply and retry on an engine that serves a model need
--restart and a yes (the prompt, or --yes). With nothing on the port, --start-setup (or a yes at the
prompt) starts setup mode (run/ops/start.sh --setup, 127.0.0.1), and stops it again on the way out
when no job was started. This tool writes no file; the engine's job writes what the page's does.

Exit: 0 done; 1 the engine refused, the plan has errors, the estimate does not fit, or the job
failed or rolled back; 2 a wrong command or flag; 3 no engine answers; 4 not confirmed (a no, or no
terminal and no --yes, or --restart missing). Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

OK, REFUSED, USAGE, NO_ENGINE, NOT_CONFIRMED = 0, 1, 2, 3, 4
COMMANDS = ("guide", "models", "show", "device", "status", "current", "community", "weights",
            "estimate", "plan", "apply", "job", "retry", "ask")
WITH_MODEL = ("show", "current", "estimate", "plan", "apply")
CHOICES = ("estimate", "plan", "apply", "ask")
JOB_FINAL = ("done", "failed", "rolled_back")
#: the Tools step's last line, where the "Other backends" tool was until ENG-413 WP14b
BACKENDS_LINE = ("Other backends (vLLM, SGLang, cloud APIs) are TandemApp's: add them on its "
                 "Backends page.")


class Unreachable(Exception):
    """Nothing answers at the engine's address."""


class Refused(Exception):
    """The engine answered with an error: status, and its body."""

    def __init__(self, status: int, body):
        err = (body or {}).get("error") if isinstance(body, dict) else None
        err = err if isinstance(err, dict) else {}
        self.status, self.body = status, body
        self.code = err.get("code") or err.get("type") or f"http_{status}"
        super().__init__(err.get("message") or f"HTTP {status}")


class Usage(Exception):
    pass


class NotConfirmed(Exception):
    pass


# ---------------------------------------------------------------------- the engine's API
class Api:
    """The Setup API at `url`. A tool on the box itself is trusted-local (server/api/auth.py): no
    token. Another address takes TANDEM_ADMIN_TOKEN as a bearer token (never printed)."""

    def __init__(self, url: str, token: str | None = None, timeout: float = 60.0):
        self.url, self.token, self.timeout = url.rstrip("/"), token, timeout

    def _req(self, method: str, path: str, body=None, headers=None) -> urllib.request.Request:
        h = {"Accept": "application/json"}
        data = None
        if method == "POST":
            data = json.dumps(body if body is not None else {}).encode()
            h["Content-Type"] = "application/json"
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        h.update(headers or {})
        return urllib.request.Request(self.url + path, data=data, headers=h, method=method)

    def call(self, method: str, path: str, body=None) -> tuple[int, object]:
        try:
            with urllib.request.urlopen(self._req(method, path, body), timeout=self.timeout) as r:
                raw = r.read()
                status = r.status
        except urllib.error.HTTPError as exc:
            raw, status = exc.read(), exc.code
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
            raise Unreachable(f"nothing answers at {self.url} ({_why(exc)})") from None
        try:
            return status, json.loads(raw) if raw else None
        except ValueError:
            return status, raw.decode(errors="replace")

    def get(self, path: str):
        status, body = self.call("GET", path)
        if status >= 400:
            raise Refused(status, body)
        return body

    def post(self, path: str, body=None):
        status, out = self.call("POST", path, body)
        if status >= 400:
            raise Refused(status, out)
        return out

    def stream(self, method: str, path: str, body=None, after: int = 0):
        """An event stream: (id or None, the decoded data) per frame; comments are skipped."""
        headers = {"Accept": "text/event-stream"}
        if after:
            headers["Last-Event-ID"] = str(after)
        try:
            r = urllib.request.urlopen(self._req(method, path, body, headers), timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                raise Refused(exc.code, json.loads(raw)) from None
            except ValueError:
                raise Refused(exc.code, None) from None
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
            raise Unreachable(f"nothing answers at {self.url} ({_why(exc)})") from None
        with r:
            eid, data = None, []
            try:
                for raw in r:
                    line = raw.decode(errors="replace").rstrip("\r\n")
                    if not line:
                        if data:
                            try:
                                yield eid, json.loads("\n".join(data))
                            except ValueError:
                                pass
                        eid, data = None, []
                    elif line.startswith(":"):
                        continue
                    elif line.startswith("id:"):
                        eid = line[3:].strip()
                    elif line.startswith("data:"):
                        data.append(line[5:].lstrip(" "))
            except (ConnectionError, TimeoutError, OSError) as exc:
                raise Unreachable(f"the stream from {self.url} broke ({_why(exc)})") from None


def _why(exc: BaseException) -> str:
    r = getattr(exc, "reason", None)
    return str(r if r is not None else exc) or type(exc).__name__


def quote(x: str) -> str:
    return urllib.parse.quote(x, safe="")


# ---------------------------------------------------------------------- the page's own switches
# dashboard/src/setup/deps.ts and state.ts, for what the page does before it asks the engine. The
# engine decides what is valid; these only make the same selection the page would send.

def join_and(xs: list[str]) -> str:
    if len(xs) <= 1:
        return xs[0] if xs else ""
    return f"{', '.join(xs[:-1])} and {xs[-1]}"


def lower_label(label: str) -> str:
    """"Chat (beta)" -> "the chat (beta)"; a name stays as it is (deps.ts lowerLabel: Laya, and a
    word with a capital or a digit after its first letter, such as an acronym)."""
    first = label.split(" ", 1)[0]
    if first == "Laya" or any(ch.isupper() or ch.isdigit() for ch in first[1:]):
        return label
    return f"the {label[:1].lower()}{label[1:]}"


def _comp(m: dict, cid: str) -> dict | None:
    return next((c for c in m["components"] if c["id"] == cid), None)


def is_on(c: dict, sel: dict) -> bool:
    v = sel.get(c["id"])
    return bool(c["required"] or (v if isinstance(v, bool) else c["default"]))


def _conflicts_of(m: dict, c: dict) -> list[dict]:
    return [o for o in m["components"] if o["id"] != c["id"] and
            (o["id"] in c["conflicts"] or c["id"] in o["conflicts"])]


def availability(m: dict, sel: dict) -> dict:
    """deps.ts availability: per tool {disabled, reason, required}."""
    out = {}
    for c in m["components"]:
        if c["required"]:
            out[c["id"]] = {"disabled": True, "reason": "Always on", "required": True}
            continue
        missing = [d for d in c["depends_on"] if _comp(m, d) is None or not is_on(_comp(m, d), sel)]
        if missing:
            out[c["id"]] = {"disabled": True, "required": False, "reason": "Needs " + join_and(
                [lower_label(_comp(m, d)["label"]) if _comp(m, d) else d for d in missing])}
            continue
        clash = [o for o in _conflicts_of(m, c) if is_on(o, sel)]
        if clash and not is_on(c, sel):
            out[c["id"]] = {"disabled": True, "required": False, "reason": "Cannot run with " +
                            join_and([lower_label(o["label"]) for o in clash])}
            continue
        out[c["id"]] = {"disabled": False, "reason": None, "required": False}
    return out


def dependents_of(m: dict, cid: str) -> list[dict]:
    out, seen, queue = [], {cid}, [cid]
    while queue:
        cur = queue.pop(0)
        for c in m["components"]:
            if c["id"] not in seen and cur in c["depends_on"]:
                seen.add(c["id"])
                out.append(c)
                queue.append(c["id"])
    return out


def toggle(m: dict, sel: dict, cid: str, on: bool) -> tuple[dict, str | None]:
    """deps.ts toggleComponent: (the new components, a note); Usage when the page refuses."""
    c = _comp(m, cid)
    if c is None:
        from server.setup.registry import RETIRED_TOOLS
        gone = RETIRED_TOOLS.get(cid)
        tools = ", ".join(x["id"] for x in m["components"])
        raise Usage(f"{m['id']} has no tool called {cid}"
                    + (f". {gone}. Its tools: {tools}" if gone else f"; its tools: {tools}"))
    if c["required"]:
        if on:
            return dict(sel, **{cid: True}), None
        raise Usage(f"{c['label']} is always on; nobody can turn it off.")
    av = availability(m, sel)[cid]
    if on and av["disabled"]:
        raise Usage(f"{c['label']}: {av['reason']}")
    nxt = dict(sel, **{cid: on})
    if on:
        for d in m["components"]:
            if d.get("with_parent") and cid in d["depends_on"]:
                nxt[d["id"]] = True
        return nxt, None
    cascaded = [d for d in dependents_of(m, cid) if is_on(d, sel) and not d["required"]]
    for d in cascaded:
        nxt[d["id"]] = False
    if not cascaded:
        return nxt, None
    labels = [d["label"] for d in cascaded]
    one = len(labels) == 1
    return nxt, (f"{join_and(labels)} {'needs' if one else 'need'} {lower_label(c['label'])}, so "
                 f"{'it is' if one else 'they are'} off too.")


def normalise_components(m: dict, sel: dict) -> dict:
    out = {c["id"]: True if c["required"] else sel[c["id"]] if isinstance(sel.get(c["id"]), bool)
           else c["default"] for c in m["components"]}
    for _ in m["components"]:
        changed = False
        for c in m["components"]:
            if out[c["id"]] and not c["required"] and any(not out.get(d) for d in c["depends_on"]):
                out[c["id"]] = False
                changed = True
        if not changed:
            break
    return out


def slots_max(m: dict) -> int:
    return m["parallelism"]["slots"].get("engine_max") or 64


def defaults(m: dict) -> dict:
    """state.ts defaults: the recommended setup ("Use the recommended values")."""
    rec = next((p for p in m["profiles"] if p.get("recommended")), m["profiles"][0])
    par = m["parallelism"]
    sel = {"model": m["id"], "profile": rec["id"], "components": normalise_components(m, {}),
           "slots": par["slots"]["default"], "window": par["window"]["default"],
           "kv_pool": par["kv_pool"]["default"], "floor_gb": par["floor_gb"]["default"],
           "methods": {f["id"]: f["recommended"] for f in m["methods"]},
           "advanced": {f["id"]: f["recommended"] for f in m["advanced"]}}
    if m.get("devices"):
        sel["device"] = m["devices"][0]["id"]
    return sel


def profile_of_entry(m: dict, e: dict) -> dict:
    """community.ts profileOfEntry: the pack as a profile, over the own profile its memory figures
    came from (so it keeps that profile's largest window)."""
    own = m["profiles"]
    like = (next((p for p in own if p["id"] == (e.get("memory") or {}).get("like")), None)
            or next((p for p in own if p.get("format") == e.get("format")), None) or own[0])
    return dict(like, id=e["profile"], label=e.get("name") or e["id"], disk_gb=e.get("size_gb"),
                weights_gib=(e.get("memory") or {}).get("weights_gib"), recommended=False,
                community=e)


def profiles(m: dict) -> list[dict]:
    """The weights the page offers: the model's own, then its community packs (community/<id>)
    (community.ts withCommunityProfiles)."""
    own = [dict(p, community=None) for p in m["profiles"]]
    return own + [profile_of_entry(m, c) for c in m.get("community") or []
                  if str(c.get("profile", "")).startswith("community/")]


def window_max(m: dict, profile: str) -> int:
    p = next((x for x in profiles(m) if x["id"] == profile), None)
    mw = (p or {}).get("max_window")
    top = m["parallelism"]["window"]["max"]
    return min(top, mw) if isinstance(mw, (int, float)) and not isinstance(mw, bool) else top


def clamp_window(m: dict, n: float, top: int) -> tuple[int, bool]:
    r = m["parallelism"]["window"]
    step = r["step"] or 1
    v = int((n / step) + 0.5) * step if n >= 0 else -int((-n / step) + 0.5) * step
    v = min(r["max"], top, max(r["min"], v))
    return v, v != n


def pack_vision_off(m: dict, sel: dict) -> bool:
    p = next((x for x in profiles(m) if x["id"] == sel.get("profile")), None)
    c = (p or {}).get("community")
    return bool(c and not (c.get("modalities") or {}).get("image") and _comp(m, "vision")
                and sel.get("components", {}).get("vision") is not False)


def tokens(n: int) -> str:
    return f"{n:,}"


def set_profile(m: dict, sel: dict, pid: str) -> tuple[dict, list[str]]:
    if not any(p["id"] == pid for p in profiles(m)):
        raise Usage(f"{m['id']} has no weights {pid!r}; its weights: "
                    f"{', '.join(p['id'] for p in profiles(m))}")
    notes = []
    nxt = dict(sel, profile=pid)
    top = window_max(m, pid)
    if sel["window"] > top:
        nxt["window"] = top
        notes.append(f"Set to {tokens(top)} tokens, the nearest length this model supports.")
    if pack_vision_off(m, nxt):
        try:
            comps, _ = toggle(m, nxt["components"], "vision", False)
        except Usage:
            comps = dict(nxt["components"], vision=False)
        nxt["components"] = comps
        notes.append("This pack runs text only, so the Vision tool is off.")
    return nxt, notes


def set_parallel(m: dict, sel: dict, key: str, value) -> tuple[dict, list[str]]:
    p = m["parallelism"]
    if key == "kv_pool":
        return dict(sel, kv_pool=bool(value)), []
    if key == "window":
        v, clamped = clamp_window(m, value, window_max(m, sel["profile"]))
        return dict(sel, window=v), ([f"Set to {tokens(v)} tokens, the nearest length this "
                                      f"model supports."] if clamped else [])
    r = p[key]
    top = slots_max(m) if key == "slots" else r["max"]
    v = min(top, max(r["min"], int(value + 0.5) if key == "slots" else value))
    return dict(sel, **{key: v}), []


def field_of(m: dict, fid: str) -> tuple[str, dict] | None:
    for group in ("methods", "advanced"):
        for f in m[group]:
            if f["id"] == fid:
                return group, f
    return None


def parse_value(f: dict, raw: str):
    t = f["type"]
    if t == "bool":
        low = raw.strip().lower()
        if low in ("on", "true", "yes", "1"):
            return True
        if low in ("off", "false", "no", "0"):
            return False
        raise Usage(f"{f['id']}: on or off, not {raw!r}")
    if t in ("int", "number"):
        try:
            v = float(raw)
        except ValueError:
            raise Usage(f"{f['id']}: a number, not {raw!r}") from None
        return int(v) if t == "int" and v.is_integer() else v
    return raw


def set_field(m: dict, sel: dict, fid: str, raw: str) -> dict:
    hit = field_of(m, fid)
    if hit is None:
        raise Usage(f"{m['id']} has no setting {fid!r}; its settings: "
                    f"{', '.join(f['id'] for f in m['methods'] + m['advanced'])}")
    group, f = hit
    return dict(sel, **{group: dict(sel.get(group) or {}, **{fid: parse_value(f, raw)})})


def onoff(raw: str) -> bool:
    low = str(raw).strip().lower()
    if low in ("on", "true", "yes", "1"):
        return True
    if low in ("off", "false", "no", "0"):
        return False
    raise Usage(f"on or off, not {raw!r}")


def apply_choices(m: dict, sel: dict, a) -> tuple[dict, list[str]]:
    """The flags on top of the seed, in the page's order: weights, tools, lanes, settings."""
    notes: list[str] = []
    sel = json.loads(json.dumps(sel))
    if getattr(a, "profile", None):
        sel, n = set_profile(m, sel, a.profile)
        notes += n
    for what, cid in getattr(a, "tools", None) or []:
        comps, n = toggle(m, sel["components"], cid, what == "on")
        sel["components"] = comps
        notes += [n] if n else []
    for key, flag in (("slots", "slots"), ("window", "window"), ("floor_gb", "floor_gb")):
        v = getattr(a, flag, None)
        if v is not None:
            sel, n = set_parallel(m, sel, key, v)
            notes += n
    if getattr(a, "kv_pool", None) is not None:
        sel, _ = set_parallel(m, sel, "kv_pool", onoff(a.kv_pool))
    for item in getattr(a, "set", None) or []:
        if "=" not in item:
            raise Usage(f"--set takes FIELD=VALUE, not {item!r}")
        fid, raw = item.split("=", 1)
        sel = set_field(m, sel, fid.strip(), raw.strip())
    if getattr(a, "device", None):
        sel["device"] = a.device
    return sel, notes


# ---------------------------------------------------------------------- terminal and output
class Term:
    """Prompts on stderr, answers from stdin; output on stdout. No colours: plain over SSH."""

    def __init__(self, yes: bool, json_out: bool, stdin=None, stdout=None, stderr=None):
        self.yes, self.json = yes, json_out
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self.stderr = stderr or sys.stderr
        try:
            self.tty = bool(self.stdin.isatty())
        except (AttributeError, ValueError):
            self.tty = False

    def say(self, text: str = "") -> None:
        if not self.json:
            print(text, file=self.stdout, flush=True)

    def note(self, text: str) -> None:
        """Progress and notes: stderr, so stdout stays the answer (and --json's object)."""
        print(text, file=self.stderr, flush=True)

    def ask(self, prompt: str, default: str = "") -> str:
        if not self.tty or self.json:
            raise NotConfirmed(f"{prompt.strip()} needs an answer: run it in a terminal, or give "
                               f"the flag for it")
        print(prompt, end="", file=self.stderr, flush=True)
        line = self.stdin.readline()
        if not line:
            raise NotConfirmed("no answer (end of input)")
        return line.strip() or default

    def confirm(self, question: str) -> bool:
        """--yes, else a yes typed at a terminal; NotConfirmed when neither can be had."""
        if self.yes:
            return True
        if not self.tty or self.json:
            raise NotConfirmed(f"{question} Add --yes to go on without a terminal.")
        return self.ask(f"{question} [y/N] ").lower() in ("y", "yes")


def gib(x) -> str:
    return "-" if x is None else f"{x:.2f} GiB"


def show_models(t: Term, body: dict) -> None:
    for tile in body.get("models") or []:
        best = tile.get("best_speed") or {}
        speed = f", {best['tok_s']:g} tok/s {best['task']}" if best.get("tok_s") else ""
        mark = " (recommended)" if tile.get("recommended") else ""
        packs = tile.get("community_count") or 0
        t.say(f"  {tile['id']}{mark}: {tile['name']}, {tile['params']}{speed}"
              + (f", {packs} community pack{'s' if packs != 1 else ''}" if packs else ""))
        if tile.get("pitch"):
            t.say(f"      {tile['pitch']}")
    for e in body.get("errors") or []:
        t.say(f"  not loaded: {e.get('path')}: {e.get('message')}")


def show_device(t: Term, d: dict) -> None:
    eng = d.get("engine") or {}
    t.say(f"Mode: {d.get('mode')}" + (" (setup mode: no model loaded)" if d.get("mode") == "setup"
                                       else f", {eng.get('profile') or 'no profile'}"
                                            f", {eng.get('running', 0)} running, "
                                            f"{eng.get('waiting', 0)} waiting"))
    t.say(f"Memory: {gib(d.get('mem_total_gb'))} total, {gib(d.get('mem_available_gb'))} available"
          + (f" (preset {d['preset']})" if d.get("preset") else ""))
    g = d.get("gpu") or {}
    if g.get("name"):
        t.say(f"GPU: {g['name']}")
    disk = d.get("disk") or {}
    if disk:
        t.say(f"Disk: {disk.get('free_gb', 0):.0f} GB free of {disk.get('total_gb', 0):.0f} GB "
              f"({disk.get('path')})")
    t.say(f"Running weights: {d.get('active_profile') or 'none'}")
    on = [k for k, v in (d.get("profiles_on_disk") or {}).items() if v]
    t.say(f"Weights on disk: {', '.join(on) if on else 'none'}")
    t.say(f"Admin password: {'set' if d.get('password_set') else 'not set'}; Hugging Face token: "
          f"{'set' if d.get('hf_token_present') else 'not set'}")
    w = d.get("assistant") or {}
    t.say(f"Wren: {w.get('state', 'off')}" + (f" ({w['reason']})" if w.get("reason") else ""))


def show_model(t: Term, m: dict, sel: dict | None = None, on_disk: dict | None = None) -> None:
    t.say(f"{m['name']} ({m['id']}), {m.get('params')}: {m.get('pitch', '')}")
    t.say("Weights:")
    for p in profiles(m):
        bits = []
        if p.get("recommended"):
            bits.append("recommended")
        if p.get("community"):
            c = p["community"]
            bits.append(f"community, by {(c.get('author') or {}).get('github')}, "
                        f"{c.get('status', '')}".rstrip(", "))
        if p.get("disk_gb") is not None:
            bits.append(f"{p['disk_gb']:g} GB download")
        if p.get("weights_gib") is not None:
            bits.append(f"{p['weights_gib']:g} GiB in memory")
        if on_disk is not None and on_disk.get(p["id"]):
            bits.append("on this box")
        if p.get("max_window"):
            bits.append(f"window at most {tokens(p['max_window'])}")
        here = " <" if sel and sel.get("profile") == p["id"] else ""
        t.say(f"  {p['id']}: {p.get('label', '')}" + (f" ({'; '.join(bits)})" if bits else "") + here)
    t.say("Tools:")
    comps = (sel or {}).get("components") or {}
    av = availability(m, comps) if sel else {}
    for c in m["components"]:
        state = "on" if is_on(c, comps) else "off"
        why = (av.get(c["id"]) or {}).get("reason")
        t.say(f"  {c['id']}: {c['label']}, {state}" + (f" ({why})" if why else ""))
    par = m["parallelism"]
    t.say("Lanes and window:")
    t.say(f"  slots {par['slots']['min']}..{slots_max(m)}"
          + (f", now {sel['slots']}" if sel else f", default {par['slots']['default']}"))
    w = par["window"]
    t.say(f"  window {tokens(w['min'])}..{tokens(w['max'])} in steps of {tokens(w['step'])}"
          + (f", now {tokens(sel['window'])}" if sel else f", default {tokens(w['default'])}"))
    t.say(f"  kv_pool " + ("on" if (sel or {}).get("kv_pool", par["kv_pool"]["default"]) else "off"))
    f = par["floor_gb"]
    t.say(f"  floor_gb {f['min']:g}..{f['max']:g}"
          + (f", now {sel['floor_gb']:g}" if sel else f", default {f['default']:g}"))
    t.say("Settings (--set FIELD=VALUE):")
    for group in ("methods", "advanced"):
        for fl in m[group]:
            now = (sel or {}).get(group, {}).get(fl["id"], fl["recommended"])
            opts = ""
            if fl["type"] == "enum":
                opts = " [" + "|".join(str(o["value"]) for o in fl.get("options") or []) + "]"
            t.say(f"  {fl['id']} ({fl['type']}{opts}): {fl['label']}, {_val(now)}")


def _val(v) -> str:
    if isinstance(v, bool):
        return "on" if v else "off"
    return str(v)


def show_estimate(t: Term, e: dict) -> None:
    t.say(f"Memory: {gib(e.get('total_gb'))} of {gib(e.get('budget_gb'))} "
          f"({'fits' if e.get('fits') else 'does not fit'}; the box keeps {gib(e.get('floor_gb'))} free)")
    for p in e.get("parts") or []:
        worst = f", up to {gib(p['worst_gb'])}" if p.get("worst_gb") is not None else ""
        t.say(f"  {p['label']}: {gib(p['gb'])}{worst}")
    if e.get("lanes_full") is not None:
        t.say(f"Lanes that can all hold the full window at once: {e['lanes_full']}; "
              f"context that fits: {tokens(int(e.get('context_tokens_fit') or 0))} tokens")
    for n in e.get("notes") or []:
        t.say(f"  note: {n}")


def show_plan(t: Term, p: dict) -> None:
    pr = p.get("profile") or {}
    t.say(f"Weights: {pr.get('current') or 'none'} -> {pr.get('next')}"
          + (" (on this box)" if pr.get("on_disk") else f" (download about {pr.get('download_gb', 0):g} GB)"))
    for d in p.get("downloads") or []:
        t.say(f"  download {d['label']}: {d['gb']:g} GB, for {d['for']}")
    ports = p.get("ports") or {}
    if ports and ports.get("current") != ports.get("next"):
        t.say(f"Port: {ports.get('current')} -> {ports.get('next')}")
    if p.get("changes"):
        t.say("Settings that change:")
        for c in p["changes"]:
            t.say(f"  {c['key']}: {c.get('current') or 'unset'} -> {c.get('next') or 'unset'}"
                  + (f" ({c['from']})" if c.get("from") else ""))
    else:
        t.say("No setting changes.")
    est = p.get("estimate") or {}
    if est:
        t.say(f"Memory: {gib(est.get('total_gb'))} of {gib(est.get('budget_gb'))}, "
              f"{'fits' if est.get('fits') else 'does not fit'}")
    t.say("What the install runs:")
    for c in p.get("commands") or []:
        t.say(f"  {c}")
    for w in p.get("warnings") or []:
        t.say(f"Warning: {w}")
    for e, code in zip(p.get("errors") or [], p.get("error_codes") or []):
        t.say(f"Error ({code}): {e}")
    t.say("The plan is ready to install." if p.get("ok") else "The plan cannot be installed as it is.")


def show_job(t: Term, j: dict) -> None:
    t.say(f"Job {j['id']} ({j.get('kind', 'apply')}): {j['state']}, weights {j.get('profile')}")
    for i, s in enumerate(j.get("steps") or [], 1):
        t.say(f"  {i}. {s['label']}: {s['state']}" + (f" ({s['detail']})" if s.get("detail") else ""))
    if j.get("message"):
        t.say(f"  {j['message']}")
    for w in j.get("warnings") or []:
        t.say(f"  warning: {w}")
    for n in j.get("notes") or []:
        t.say(f"  note: {n}")


# ---------------------------------------------------------------------- the session
class Session:
    def __init__(self, a, term: Term):
        self.a, self.t = a, term
        url = a.url or os.environ.get("TANDEM_URL") or \
            f"http://127.0.0.1:{os.environ.get('PORT') or '8000'}"
        local = urllib.parse.urlsplit(url).hostname in ("127.0.0.1", "localhost", "::1")
        self.local = local
        token = os.environ.get("TANDEM_ADMIN_TOKEN") if not local else None
        self.api = Api(url, token, timeout=a.http_timeout)
        self.dir = a.tandem_dir or os.environ.get("TANDEM_DIR")
        self.started_setup = False
        self.job_started = False
        self.device: dict | None = None
        self.out = {"command": a.command, "ok": False, "url": self.api.url, "mode": None,
                    "selection": None, "notices": [], "result": None, "error": None}

    # -- the engine
    def connect(self) -> dict:
        try:
            self.device = self.api.get("/v1/setup/device")
        except Unreachable:
            self._start_setup_mode()
            self.device = self.api.get("/v1/setup/device")
        self.out["mode"] = self.device.get("mode")
        return self.device

    def _engine_pid(self) -> int | None:
        if not self.dir:
            return None
        for f in (os.path.join(self.dir, "src", "logs", "engine.pid"),):
            try:
                pid = int(open(f).read().strip())
                os.kill(pid, 0)
                return pid
            except (OSError, ValueError):
                continue
        return None

    def _start_setup_mode(self) -> None:
        start = os.path.join(self.dir or "", "run", "ops", "start.sh")
        if not self.local or not self.dir or not os.path.isfile(start):
            raise Unreachable(f"nothing answers at {self.api.url}. Start the engine "
                              f"(bin/tandem start) or setup mode (bin/tandem setup --start-setup)")
        pid = self._engine_pid()
        if pid is not None:
            raise Unreachable(f"nothing answers at {self.api.url}, but the engine runs (pid {pid}): "
                              f"it is starting or loading. Try again when bin/tandem status says "
                              f"healthy")
        if not self.a.start_setup:
            if not self.t.tty or self.t.json or self.t.yes:
                raise Unreachable(f"nothing answers at {self.api.url}. Add --start-setup to start "
                                  f"setup mode (the Setup API alone, no model, on 127.0.0.1)")
            if not self.t.confirm(f"Nothing answers at {self.api.url}. Start setup mode (the Setup "
                                  f"API alone, no model, on 127.0.0.1)?"):
                raise NotConfirmed("setup mode was not started")
        self.t.note("[setup] starting setup mode: bash run/ops/start.sh --setup")
        r = subprocess.run(["bash", start, "--setup"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, timeout=300)
        for line in (r.stdout or "").splitlines():
            self.t.note(f"    | {line}")
        if r.returncode != 0:
            raise Unreachable(f"setup mode did not start (exit {r.returncode}); see "
                              f"{os.path.join(self.dir, 'src', 'logs', 'engine.log')}")
        self.started_setup = True

    def close(self) -> None:
        """Setup mode this tool started goes again, unless a job took over or --keep-setup."""
        if not self.started_setup or self.job_started or self.a.keep_setup:
            return
        stop = os.path.join(self.dir, "run", "ops", "stop.sh")
        self.t.note("[setup] stopping the setup mode this command started (--keep-setup keeps it)")
        r = subprocess.run(["bash", stop, "60"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, timeout=180)
        for line in (r.stdout or "").splitlines():
            self.t.note(f"    | {line}")

    def serving(self) -> bool:
        return (self.device or {}).get("mode") != "setup"

    # -- the selection
    def model(self, mid: str) -> dict:
        return self.api.get(f"/v1/setup/models/{quote(mid)}")

    def seed(self, m: dict) -> tuple[dict, list[str]]:
        a = self.a
        if getattr(a, "selection", None):
            try:
                if a.selection == "-":
                    body = json.load(self.t.stdin)
                else:
                    with open(a.selection, encoding="utf-8") as fh:
                        body = json.load(fh)
            except OSError as exc:
                raise Usage(f"--selection: cannot read {a.selection} ({exc.strerror})") from None
            except ValueError as exc:
                raise Usage(f"--selection: not JSON ({exc})") from None
            sel = body.get("selection", body) if isinstance(body, dict) else body
            if not isinstance(sel, dict):
                raise Usage("--selection: a JSON object")
            if sel.get("model") not in (None, m["id"]):
                raise Usage(f"--selection names {sel.get('model')!r}, not {m['id']}")
            return dict(sel, model=m["id"]), []
        if getattr(a, "base", "current") == "recommended":
            return defaults(m), []
        cur = self.api.get(f"/v1/setup/models/{quote(m['id'])}/current")
        sel = cur["selection"]
        notes = [] if json.dumps(sel, sort_keys=True) == json.dumps(defaults(m), sort_keys=True) \
            else ["The guide starts from what this box runs now (--from recommended starts from ours)."]
        for u in cur.get("unmatched") or []:
            notes.append(f"{u['field']}: this box's value is not one the guide offers; it starts "
                         f"from the recommended one")
        return sel, notes

    def selection(self, mid: str) -> tuple[dict, dict, list[str]]:
        m = self.model(mid)
        sel, notes = self.seed(m)
        if not getattr(self.a, "selection", None) or self._has_choices():
            if "components" in sel and isinstance(sel["components"], dict):
                sel = dict(sel, components=dict(sel["components"]))
            sel, more = apply_choices(m, _complete(m, sel), self.a)
            notes += more
        self.out["selection"], self.out["notices"] = sel, notes
        for n in notes:
            self.t.note(f"[setup] {n}")
        return m, sel, notes

    def _has_choices(self) -> bool:
        a = self.a
        return any(getattr(a, k, None) not in (None, [], False) for k in
                   ("profile", "tools", "slots", "window", "kv_pool", "floor_gb", "set", "device"))

    # -- the job
    def follow(self, jid: str, ports: dict | None = None) -> dict:
        """The job's events to its end; the engine restarts in the middle, so a dropped stream
        is opened again with Last-Event-ID (also on the plan's new port) until --timeout."""
        t, after = self.t, 0
        deadline = time.monotonic() + self.a.timeout
        bases = [self.api.url]
        if ports and ports.get("next") and ports.get("next") != ports.get("current"):
            u = urllib.parse.urlsplit(self.api.url)
            bases.append(f"{u.scheme}://{u.hostname}:{ports['next']}")
        steps: dict[str, str] = {}
        away = False
        turn = 0
        while True:
            api = Api(bases[turn % len(bases)], self.api.token, self.api.timeout)
            try:
                for eid, ev in api.stream("GET", f"/v1/setup/jobs/{quote(jid)}", after=after):
                    if eid and eid.isdigit():
                        after = int(eid)
                    if away:
                        t.note("[setup] the engine answers again")
                        away = False
                    kind = ev.get("type")
                    if kind == "job":
                        for s in ev["job"].get("steps") or []:
                            self._step(steps, s)
                    elif kind == "step":
                        self._step(steps, ev["step"])
                    elif kind == "log":
                        if not t.json and not self.a.quiet:
                            t.say(f"    | {ev.get('line', '')}")
                    elif kind == "done":
                        self.api = api
                        return self._final(jid, deadline)
            except Unreachable:
                if not away:
                    t.note("[setup] the engine does not answer (it restarts during the install); "
                           "waiting for it")
                    away = True
            except Refused as exc:
                if exc.status == 404:
                    raise
                if not away:
                    t.note(f"[setup] the job stream answered {exc.status}; trying again")
                    away = True
            if time.monotonic() > deadline:
                raise Unreachable(f"the job {jid} did not end within {self.a.timeout:g} s here; it "
                                  f"goes on in the engine. Follow it again: bin/tandem setup job {jid}")
            turn += 1
            time.sleep(self.a.poll)

    def _step(self, seen: dict, s: dict) -> None:
        key = (s.get("state"), s.get("detail"))
        if seen.get(s["id"]) == key:
            return
        seen[s["id"]] = key
        if s.get("state") == "pending":
            return
        self.t.say(f"  {s['label']}: {s['state']}" + (f" ({s['detail']})" if s.get("detail") else ""))

    def _final(self, jid: str, deadline: float) -> dict:
        while True:
            try:
                return self.api.get(f"/v1/setup/jobs/{quote(jid)}?format=json")
            except Unreachable:
                if time.monotonic() > deadline:
                    raise
                time.sleep(self.a.poll)


def _complete(m: dict, sel: dict) -> dict:
    """A selection with the keys the page's switches read (a scripted file may leave some out;
    the engine fills them the same way: estimate.check)."""
    d = defaults(m)
    out = dict(d, **{k: v for k, v in sel.items() if v is not None})
    out["components"] = dict(d["components"], **(sel.get("components") or {}))
    for g in ("methods", "advanced"):
        out[g] = dict(d[g], **(sel.get(g) or {}))
    return out


# ---------------------------------------------------------------------- the commands
def cmd_models(s: Session):
    body = s.api.get("/v1/setup/models")
    show_models(s.t, body)
    return body, OK


def cmd_show(s: Session):
    m = s.model(s.a.model)
    show_model(s.t, m, on_disk=(s.device or {}).get("profiles_on_disk"))
    return m, OK


def cmd_device(s: Session):
    show_device(s.t, s.device)
    return s.device, OK


def cmd_status(s: Session):
    body = s.api.get("/v1/setup/status")
    eng = body.get("engine") or {}
    s.t.say(f"Engine: {eng.get('words') or eng.get('state')}" +
            (f", {eng.get('mode')}" if eng.get("mode") else ""))
    job = body.get("job")
    if job:
        show_job(s.t, job)
    else:
        s.t.say("No setup job.")
    return body, OK


def cmd_current(s: Session):
    m = s.model(s.a.model)
    body = s.api.get(f"/v1/setup/models/{quote(m['id'])}/current")
    show_model(s.t, m, body["selection"], (s.device or {}).get("profiles_on_disk"))
    for u in body.get("unmatched") or []:
        s.t.say(f"Not matched: {u['field']} = {u['value']}")
    return body, OK


def cmd_community(s: Session):
    body = s.api.post("/v1/setup/community/sync", {}) if s.a.sync else s.api.get("/v1/setup/community")
    sync = body.get("sync") or {}
    s.t.say(f"Index: {sync.get('source') or sync.get('state') or 'bundled'}"
            + (f" (fetched {sync['fetched_at']})" if sync.get("fetched_at") else "")
            + (f"; {sync['error']}" if sync.get("error") else ""))
    for mid, packs in (body.get("models") or {}).items():
        for c in packs:
            mods = "+".join(k for k, v in (c.get("modalities") or {}).items() if v)
            s.t.say(f"  {mid}: {c['profile']}: {c.get('name')}, by "
                    f"{(c.get('author') or {}).get('github')}, {c.get('size_gb', 0):g} GB, "
                    f"{c.get('status')}" + (f", {mods}" if mods else ""))
    return body, OK


def cmd_weights(s: Session):
    body = s.api.get("/v1/setup/weights")
    for w in body.get("weights") or []:
        s.t.say(f"  {w['profile']} ({w['model']}): {w['gb']:g} GB"
                + (", running" if w.get("active") else "") + f", {w['dir']}")
    for a in body.get("assets") or []:
        s.t.say(f"  asset {a['id']}: {a['gb']:g} GB, {a['path']}")
    if body.get("free_gb") is not None:
        s.t.say(f"Disk free: {body['free_gb']:g} GB")
    return body, OK


def cmd_estimate(s: Session):
    m, sel, _ = s.selection(s.a.model)
    body = s.api.post("/v1/setup/estimate", sel)
    show_estimate(s.t, body)
    return body, OK if body.get("fits") else REFUSED


def cmd_plan(s: Session):
    m, sel, _ = s.selection(s.a.model)
    body = s.api.post("/v1/setup/plan", {"selection": sel})
    show_plan(s.t, body)
    return body, OK if body.get("ok") else REFUSED


def restart_gate(s: Session, what: str) -> None:
    """A running engine stops only with --restart and a yes."""
    if s.serving():
        if not s.a.restart:
            raise NotConfirmed(f"{what} restarts the engine that serves now "
                               f"({(s.device.get('engine') or {}).get('profile') or 'a model'}). "
                               f"Add --restart to allow it.")
        if not s.t.confirm(f"{what} stops the engine that serves now and starts it with the new "
                           f"setup. Go on?"):
            raise NotConfirmed("not confirmed: nothing changed")
    elif not s.t.confirm(f"{what} leaves setup mode and starts the engine with this setup. Go on?"):
        raise NotConfirmed("not confirmed: nothing changed")


def cmd_apply(s: Session):
    m, sel, _ = s.selection(s.a.model)
    remove_after = list(s.a.remove_after or [])
    if s.a.dry_run:
        body = s.api.post("/v1/setup/apply", {"selection": sel, "dry_run": True,
                                              **({"remove_after": remove_after} if remove_after else {})})
        show_plan(s.t, body)
        return body, OK if body.get("ok") else REFUSED
    plan = s.api.post("/v1/setup/plan", {"selection": sel})
    show_plan(s.t, plan)
    if not plan.get("ok"):
        return plan, REFUSED
    restart_gate(s, "Installing")
    return launch(s, "/v1/setup/apply", dict({"selection": sel}, **(
        {"remove_after": remove_after} if remove_after else {})), plan.get("ports"))


def launch(s: Session, path: str, body: dict, ports: dict | None):
    status, out = s.api.call("POST", path, body)
    if status >= 400:
        if isinstance(out, dict) and isinstance(out.get("plan"), dict):
            show_plan(s.t, out["plan"])
        raise Refused(status, out)
    job = out["job"]
    s.job_started = True
    s.t.say(f"Job {job['id']} started.")
    if s.a.no_follow:
        s.t.say(f"Follow it with: bin/tandem setup job {job['id']}")
        return job, OK
    final = s.follow(job["id"], ports)
    show_job(s.t, final)
    return final, OK if final.get("state") == "done" else REFUSED


def cmd_job(s: Session):
    jid = s.a.job_id
    if not jid:
        st = s.api.get("/v1/setup/status")
        jid = ((st.get("job") or {}).get("id"))
        if not jid:
            s.t.say("No setup job on this box.")
            return st, OK
    job = s.api.get(f"/v1/setup/jobs/{quote(jid)}?format=json")
    if not s.a.no_follow and job.get("state") not in JOB_FINAL:
        job = s.follow(jid)
    show_job(s.t, job)
    return job, OK if job.get("state") in ("done", "queued", "running") else REFUSED


def cmd_retry(s: Session):
    old = s.api.get(f"/v1/setup/jobs/{quote(s.a.job_id)}?format=json")
    show_job(s.t, old)
    restart_gate(s, "Trying the job again")
    return launch(s, f"/v1/setup/jobs/{quote(s.a.job_id)}/retry", {}, None)


def wren_ready(s: Session) -> dict:
    st = s.api.get("/v1/setup/assistant/status")
    if st.get("state") in ("loaded",):
        return st
    if st.get("state") not in ("loading",):
        if not st.get("can_load"):
            raise Refused(409, {"error": {"code": f"wren_{st.get('state')}", "message":
                                          f"Wren cannot answer: {st.get('reason') or st.get('state')}"}})
        s.t.note("[setup] loading Wren, the setup guide's assistant")
        st = s.api.post("/v1/setup/assistant/load", {})
    deadline = time.monotonic() + min(self_timeout(s), 600)
    while st.get("state") == "loading" and time.monotonic() < deadline:
        time.sleep(s.a.poll)
        st = s.api.get("/v1/setup/assistant/status")
    if st.get("state") != "loaded":
        raise Refused(409, {"error": {"code": f"wren_{st.get('state')}", "message":
                                      f"Wren is {st.get('state')}: {st.get('reason') or 'not loaded'}"}})
    return st


def self_timeout(s: Session) -> float:
    return float(s.a.timeout)


def ask_wren(s: Session, question: str, page: dict, history: list[dict]) -> tuple[str, dict]:
    wren_ready(s)
    msgs = history + [{"role": "user", "content": question}]
    text, done = [], {}
    for _eid, ev in s.api.stream("POST", "/v1/setup/assistant/chat",
                                 {"messages": msgs, "page": page, "tools": False}):
        kind = ev.get("type")
        if kind == "text":
            text.append(ev.get("delta") or "")
            if not s.t.json:
                print(ev.get("delta") or "", end="", file=s.t.stdout, flush=True)
        elif kind == "error":
            raise Refused(409, {"error": {"code": "wren_error", "message": ev.get("message")}})
        elif kind == "done":
            done = ev
    if not s.t.json:
        print(file=s.t.stdout)
        for src in done.get("sources") or []:
            s.t.say(f"  source: {src.get('title')} ({src.get('path')})")
    answer = "".join(text)
    history.extend([{"role": "user", "content": question}, {"role": "assistant", "content": answer}])
    return answer, done


def page_state(m: dict | None, sel: dict | None, step: str = "review") -> dict:
    ids = ["models", "overview", "weights", "components", "parallelism", "methods", "review", "install"]
    if m is None:
        return {"model": None, "step": "models", "step_index": 0, "selection": {}}
    return {"model": m["id"], "step": step, "step_index": ids.index(step), "selection": sel or {}}


def cmd_ask(s: Session):
    q = " ".join(s.a.question).strip()
    if not q:
        raise Usage("ask needs a question")
    m = sel = None
    if s.a.model:
        m, sel, _ = s.selection(s.a.model)
    answer, done = ask_wren(s, q, page_state(m, sel), [])
    return {"answer": answer, "finish": done.get("finish"), "sources": done.get("sources") or []}, OK


# ---------------------------------------------------------------------- the guide
def cmd_guide(s: Session):
    t = s.t
    if not t.tty or t.json:
        raise Usage("the guide asks in a terminal; for a script use the commands "
                    "(models, plan, apply ...; bin/tandem setup --help)")
    show_device(t, s.device)
    t.say("")
    t.say("At any question, type ? and a question for Wren, the setup guide's assistant.")
    history: list[dict] = []
    tiles = s.api.get("/v1/setup/models")
    weights = s.api.get("/v1/setup/weights")
    active_model = next((w["model"] for w in weights.get("weights") or [] if w.get("active")), None)
    rec = next((x["id"] for x in tiles["models"] if x.get("recommended")), tiles["models"][0]["id"])
    t.say("")
    t.say("1. The model")
    for i, tile in enumerate(tiles["models"], 1):
        t.say(f"  {i}. {tile['id']}: {tile['name']}" + (" (recommended)" if tile.get("recommended") else "")
              + (" (running)" if tile["id"] == active_model else ""))
    ids = [x["id"] for x in tiles["models"]]

    def ask(prompt, default, m=None, sel=None):
        while True:
            ans = t.ask(prompt, default)
            if ans.startswith("?"):
                try:
                    ask_wren(s, ans[1:].strip() or "What should I pick here?", page_state(m, sel), history)
                except Refused as exc:
                    t.say(f"Wren: {exc}")
                continue
            return ans

    pick = ask(f"Model [{active_model or rec}]: ", active_model or rec)
    mid = ids[int(pick) - 1] if pick.isdigit() and 0 < int(pick) <= len(ids) else pick
    if mid not in ids:
        raise Usage(f"no model {mid!r}; the models: {', '.join(ids)}")
    s.a.model = mid
    m, sel, _ = s.selection(mid)
    seed = json.loads(json.dumps(sel))
    on_disk = (s.device or {}).get("profiles_on_disk") or {}

    t.say("")
    t.say("2. The weights")
    for p in profiles(m):
        t.say(f"  {p['id']}: {p.get('label', '')}" + (" (recommended)" if p.get("recommended") else "")
              + (" (community)" if p.get("community") else "")
              + (" (on this box)" if on_disk.get(p["id"]) else
                 f" ({p['disk_gb']:g} GB download)" if p.get("disk_gb") is not None else ""))
    while True:
        pid = ask(f"Weights [{sel['profile']}]: ", sel["profile"], m, sel)
        try:
            sel, notes = set_profile(m, sel, pid)
            for n in notes:
                t.say(n)
            break
        except Usage as exc:
            t.say(str(exc))

    t.say("")
    t.say("3. The tools (type a tool's id to switch it, Enter to go on)")
    while True:
        av = availability(m, sel["components"])
        for c in m["components"]:
            why = av[c["id"]]["reason"]
            t.say(f"  {c['id']}: {c['label']}, {'on' if is_on(c, sel['components']) else 'off'}"
                  + (f" ({why})" if why else ""))
        t.say(f"  {BACKENDS_LINE}")
        cid = ask("Switch: ", "", m, sel)
        if not cid:
            break
        c = _comp(m, cid)
        try:
            comps, n = toggle(m, sel["components"], cid, not is_on(c, sel["components"]) if c else True)
            sel["components"] = comps
            if n:
                t.say(n)
        except Usage as exc:
            t.say(str(exc))

    t.say("")
    t.say("4. Lanes and window")
    par = m["parallelism"]
    for key, label, lo, hi in (("slots", "Parallel lanes", par["slots"]["min"], slots_max(m)),
                               ("window", "Context window, every lane", par["window"]["min"],
                                window_max(m, sel["profile"])),
                               ("floor_gb", "Memory kept free (GiB)", par["floor_gb"]["min"],
                                par["floor_gb"]["max"])):
        while True:
            raw = ask(f"{label} ({lo:g}..{hi:g}) [{sel[key]:g}]: ", "", m, sel)
            if not raw:
                break
            try:
                sel, notes = set_parallel(m, sel, key, float(raw.replace(",", "")))
                for n in notes:
                    t.say(n)
                break
            except ValueError:
                t.say("A number, please.")
    raw = ask(f"Shared memory pool (on/off) [{'on' if sel['kv_pool'] else 'off'}]: ", "", m, sel)
    if raw:
        try:
            sel["kv_pool"] = onoff(raw)
        except Usage as exc:
            t.say(str(exc))

    t.say("")
    t.say("5. Methods and advanced settings (type a setting's id to change it, Enter to go on)")
    while True:
        for g in ("methods", "advanced"):
            for f in m[g]:
                t.say(f"  {f['id']}: {f['label']}, {_val(sel[g].get(f['id'], f['recommended']))}")
        fid = ask("Change: ", "", m, sel)
        if not fid:
            break
        hit = field_of(m, fid)
        if hit is None:
            t.say(f"No setting {fid!r}.")
            continue
        f = hit[1]
        opts = (" [" + "|".join(str(o["value"]) for o in f.get("options") or []) + "]"
                if f["type"] == "enum" else "")
        raw = ask(f"{f['label']}{opts}: ", "", m, sel)
        if raw:
            try:
                sel = set_field(m, sel, fid, raw)
            except Usage as exc:
                t.say(str(exc))

    s.out["selection"] = sel
    t.say("")
    t.say("6. Review")
    plan = s.api.post("/v1/setup/plan", {"selection": sel})
    show_plan(t, plan)
    line = command_line(m, seed, sel)
    t.say("")
    t.say(f"The same from a script: {line}")
    if not plan.get("ok"):
        return plan, REFUSED
    if s.serving() and not s.a.restart:
        t.say("Installing restarts the engine that serves now; this guide does that only with "
              "--restart. Nothing changed. To install: " + line + " --restart")
        return plan, OK
    restart_gate(s, "Installing")
    return launch(s, "/v1/setup/apply", {"selection": sel}, plan.get("ports"))


def command_line(m: dict, seed: dict, sel: dict) -> str:
    """The flags that turn the seed into `sel` (a reproduction of the guide's answers)."""
    out = ["bin/tandem setup apply", m["id"]]
    if sel["profile"] != seed["profile"]:
        out += ["--profile", sel["profile"]]
    for c in m["components"]:
        a, b = is_on(c, seed["components"]), is_on(c, sel["components"])
        if a != b:
            out += ["--on" if b else "--off", c["id"]]
    for key, flag in (("slots", "--slots"), ("window", "--window"), ("floor_gb", "--floor-gb")):
        if sel[key] != seed[key]:
            out += [flag, f"{sel[key]:g}"]
    if sel["kv_pool"] != seed["kv_pool"]:
        out += ["--kv-pool", "on" if sel["kv_pool"] else "off"]
    for g in ("methods", "advanced"):
        for fid, v in sel[g].items():
            if seed.get(g, {}).get(fid) != v:
                out += ["--set", f"{fid}={_val(v)}"]
    return " ".join(shlex.quote(x) if i else x for i, x in enumerate(out))


# ---------------------------------------------------------------------- arguments
class _Tool(argparse.Action):
    def __call__(self, parser, ns, value, option=None):
        items = list(getattr(ns, "tools", None) or [])
        items.append(("on" if option == "--on" else "off", value))
        ns.tools = items


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bin/tandem setup", formatter_class=argparse.RawDescriptionHelpFormatter,
        description="The Setup Guide in the terminal: pick, check and load a model with the "
                    "engine's own Setup API (/v1/setup/*).",
        epilog="Exit: 0 done, 1 refused or failed, 2 wrong command, 3 no engine answers, "
               "4 not confirmed.")
    p.add_argument("command", nargs="?", default="guide", choices=COMMANDS, metavar="COMMAND",
                   help="guide (default), " + ", ".join(COMMANDS[1:]))
    p.add_argument("args", nargs="*", help="MODEL, a job id, or a question")
    g = p.add_argument_group("the engine")
    g.add_argument("--url", help="the engine's address (default http://127.0.0.1:$PORT)")
    g.add_argument("--tandem-dir", help="the install (bin/tandem passes it)")
    g.add_argument("--start-setup", action="store_true",
                   help="nothing answers: start setup mode (and stop it again when no job started)")
    g.add_argument("--keep-setup", action="store_true", help="leave setup mode this command started")
    g.add_argument("--http-timeout", type=float, default=60.0, help=argparse.SUPPRESS)
    g.add_argument("--timeout", type=float, default=3 * 3600.0,
                   help="seconds to follow a job (default 3 h; the job goes on without it)")
    g.add_argument("--poll", type=float, default=2.0, help=argparse.SUPPRESS)
    o = p.add_argument_group("answers and output")
    o.add_argument("--json", action="store_true", help="one JSON object on stdout")
    o.add_argument("--yes", "-y", action="store_true", help="yes to every confirmation")
    o.add_argument("--restart", action="store_true",
                   help="allow apply and retry to restart an engine that serves now")
    o.add_argument("--quiet", "-q", action="store_true", help="no job log lines")
    c = p.add_argument_group("choices (estimate, plan, apply, ask)")
    c.add_argument("--model", dest="model_flag", help="the model (or the first argument)")
    c.add_argument("--from", dest="base", choices=("current", "recommended"), default="current",
                   help="start from what the box runs now (default) or the recommended setup")
    c.add_argument("--selection", help="a selection as JSON (a file, or - for stdin)")
    c.add_argument("--profile", help="the weights: a profile id or community/<id>")
    c.add_argument("--on", action=_Tool, metavar="TOOL", help="switch a tool on (repeatable)")
    c.add_argument("--off", action=_Tool, metavar="TOOL", help="switch a tool off (repeatable)")
    c.add_argument("--slots", type=float, help="parallel lanes")
    c.add_argument("--window", type=float, help="every lane's context window, tokens")
    c.add_argument("--kv-pool", choices=("on", "off"), help="the shared memory pool")
    c.add_argument("--floor-gb", type=float, help="memory kept free, GiB")
    c.add_argument("--set", action="append", metavar="FIELD=VALUE",
                   help="a method or advanced setting (repeatable)")
    c.add_argument("--device", help="a device preset id")
    j = p.add_argument_group("apply, job, retry, community")
    j.add_argument("--dry-run", action="store_true", help="apply: the plan only, nothing changes")
    j.add_argument("--remove-after", action="append", metavar="PROFILE",
                   help="apply: weights to remove once the new setup answers (repeatable)")
    j.add_argument("--no-follow", action="store_true", help="start the job and do not wait")
    j.add_argument("--sync", action="store_true", help="community: fetch the index now")
    return p


def parse(argv: list[str]) -> argparse.Namespace:
    p = parser()
    a = p.parse_intermixed_args(argv)
    a.model = a.model_flag
    a.job_id = None
    a.question = []
    rest = list(a.args)
    if a.command in WITH_MODEL:
        if not a.model:
            if not rest:
                raise Usage(f"{a.command} needs a MODEL (bin/tandem setup models lists them)")
            a.model = rest.pop(0)
        if rest:
            raise Usage(f"{a.command}: unexpected {' '.join(rest)}")
    elif a.command in ("job", "retry"):
        if rest:
            a.job_id = rest.pop(0)
        if a.command == "retry" and not a.job_id:
            raise Usage("retry needs a job id")
        if rest:
            raise Usage(f"{a.command}: unexpected {' '.join(rest)}")
    elif a.command == "ask":
        a.question = rest
    elif rest:
        raise Usage(f"{a.command}: unexpected {' '.join(rest)}")
    if a.command not in CHOICES and a.command != "guide":
        if getattr(a, "tools", None) or any(getattr(a, k) is not None for k in
                                            ("profile", "slots", "window", "kv_pool", "floor_gb",
                                             "set", "device", "selection")):
            raise Usage(f"{a.command} takes no choices (--profile, --on, ...)")
    if not hasattr(a, "tools"):
        a.tools = []
    return a


RUN = {"guide": cmd_guide, "models": cmd_models, "show": cmd_show, "device": cmd_device,
       "status": cmd_status, "current": cmd_current, "community": cmd_community,
       "weights": cmd_weights, "estimate": cmd_estimate, "plan": cmd_plan, "apply": cmd_apply,
       "job": cmd_job, "retry": cmd_retry, "ask": cmd_ask}


def main(argv: list[str] | None = None, *, stdin=None, stdout=None, stderr=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    out_json = "--json" in argv
    err = stderr or sys.stderr
    try:
        a = parse(argv)
    except Usage as exc:
        return _end_usage(out_json, str(exc), stdout or sys.stdout, err)
    except SystemExit as exc:
        return int(exc.code or 0) if exc.code in (0, None) else USAGE
    t = Term(a.yes, a.json, stdin, stdout, stderr)
    s = Session(a, t)
    rc = REFUSED
    try:
        s.connect()
        result, rc = RUN[a.command](s)
        s.out.update(result=result, ok=rc == OK)
        if rc != OK:
            s.out["error"] = outcome_error(result)
    except Usage as exc:
        rc = USAGE
        s.out["error"] = {"status": None, "code": "usage", "message": str(exc)}
    except NotConfirmed as exc:
        rc = NOT_CONFIRMED
        s.out["error"] = {"status": None, "code": "not_confirmed", "message": str(exc)}
    except Unreachable as exc:
        rc = NO_ENGINE
        s.out["error"] = {"status": None, "code": "unreachable", "message": str(exc)}
    except Refused as exc:
        rc = REFUSED
        s.out.update(result=exc.body, error={"status": exc.status, "code": exc.code,
                                             "message": str(exc)})
    except KeyboardInterrupt:
        rc = NOT_CONFIRMED
        s.out["error"] = {"status": None, "code": "interrupted",
                          "message": "stopped here; a job that started goes on in the engine"}
    finally:
        try:
            s.close()
        except Exception as exc:                                   # noqa: BLE001
            t.note(f"[setup] could not stop setup mode: {exc}")
    if a.json:
        print(json.dumps(s.out, ensure_ascii=False), file=t.stdout, flush=True)
    elif s.out["error"]:
        e = s.out["error"]
        t.note(f"[setup] {e['message']}" + (f" ({e['code']}, HTTP {e['status']})" if e["status"] else ""))
    return rc


def outcome_error(result) -> dict:
    """The error object of an answer that is not a refusal but says no: a plan with errors, an
    estimate that does not fit, a job that failed or rolled back."""
    r = result if isinstance(result, dict) else {}
    if r.get("error_codes"):
        return {"status": None, "code": r["error_codes"][0], "message": " ".join(r.get("errors") or [])}
    if "fits" in r and not r["fits"]:
        return {"status": None, "code": "over_budget", "message": " ".join(r.get("notes") or [])}
    if r.get("state") in ("failed", "rolled_back"):
        return {"status": None, "code": f"job_{r['state']}", "message": r.get("message") or r["state"]}
    return {"status": None, "code": "refused", "message": "see result"}


def _end_usage(out_json: bool, msg: str, stdout, stderr) -> int:
    if out_json:
        print(json.dumps({"command": None, "ok": False, "url": None, "mode": None, "selection": None,
                          "notices": [], "result": None,
                          "error": {"status": None, "code": "usage", "message": msg}}),
              file=stdout, flush=True)
    else:
        print(f"[setup] {msg}", file=stderr, flush=True)
    return USAGE


if __name__ == "__main__":
    sys.exit(main())
