"""App settings (OPS-234, VIS-E19 6.3b; concept §8.13, A8, A9, A14): what the controller can say of
its machine's permissions, how it runs as a service, and the fleet's configuration as a file.

    a = AppSettings(fleet)                              fleet.app()
    GET  /v1/fleet/app                                  permissions, service (proxy/app.py adds the
                                                        admin password's state, contracts/
                                                        fleet-v1 app.schema.json)
    POST /v1/fleet/app/permissions/local-network        one short mDNS browse: the state after it
    GET  /v1/fleet/app/export                           the fleet's configuration, secrets left out
    POST /v1/fleet/app/import {config, mode?}           the plan: what the file adds, changes,
                                                        removes, and what it cannot; its confirm
    POST /v1/fleet/app/import/apply {config, mode?, confirm}
                                                        applied: 428 without the confirm, 409 with
                                                        a spent, old or another plan's one

PERMISSIONS (A14). Each is what this process can read, or `unknown` with why; nothing is a toggle
the controller cannot set. Local Network (macOS only): discovery's state (proxy/discovery.py), a
check runs one browse; `lan_denied` is what 5.3 found for a controller launchd starts (EHOSTUNREACH
on every LAN address, unicast too), so its fallback is the controller on a Spark, or started from a
Terminal, not "add by address". Keys: the controller's private key, the kept SSH keys, the API
keys' master key, the password file: each a file only its user may read (0600), the master key
from the Keychain where one is stored there; the Keychain is not used for the others (not built),
and on Linux it does not apply. Notifications: the page's (the browser's permission); the
controller sends none. Sudo on a node: asked per action (fabric link setup), never stored.

THE EXPORT (`kind: tandemapp-fleet-config`, version 1) is JSON for a person to keep: the nodes
(id, alias, colour, icon, tags, class, hardware, address, transport, lease and cost, whether it is
the controller's own machine, whether it is paired and the pinned certificate's SHA-256), the
deployment records, the alert rules as they are now, the saved layouts and the `app.*` settings.
SECRETS ARE LEFT OUT, never sealed into it: the controller's certificate key, each node's trust
(the pinned certificate stays only as its fingerprint), SSH routes, key files and tunnels, the
admin password and token, the backends and their API keys, the metric history and the events.
Moving the controller with its keys is Move / Take the fleet (proxy/ctlmove.py: sealed ECDH
P-256, HKDF-SHA256, AES-256-GCM to the taker's certificate); the file says what it left out.

THE IMPORT merges a file into the running controller (`mode` merge, the default) or makes the
registry the file's (`replace`: also removes what the file lacks, where removing is safe). It
never touches the controller's identity (its certificate and key, its own node's address and
trust, `moved_to`, the password) and never writes trust: a node the file has and this controller
does not is added UNPAIRED at its address (Add Spark pairs it; its alias, colour and tags stay),
a node paired here keeps its pin whatever the file says. Deployment records arrive stopped (Models
starts them); one that serves here is never replaced or removed. A paired node is never removed
(release it on its page). The plan lists each item (`add`, `change`, `remove`, `keep`, `conflict`
with why); conflicts are skipped. The confirm is single use, good for CONFIRM_S, and bound to the
file, the mode and the registry as it was planned: a registry that changed since is 409.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import threading
import time

from proxy import _version

CONTRACT = "1.0"
KIND = "tandemapp-fleet-config"
CONFIRM_S = 300.0
SERVICE_CACHE_S = 30.0
AGENT_PORT = 7700
MODES = ("merge", "replace")
LIMITS = {"nodes": 64, "deployments": 256, "rules": 64, "layouts": 256, "settings": 128}
SETTINGS_PREFIX = "app."
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")
NODE_FIELDS = ("alias", "colour", "icon", "tags")
RULE_FIELDS = ("enabled", "notify", "threshold", "for_s", "scope")
DEP_FIELDS = ("name", "model", "pack", "type", "members", "stages", "options")
EXCLUDED = [
    "this controller's certificate key and every node's trust (only each pin's SHA-256 is kept)",
    "SSH routes, key files and tunnels",
    "the admin password and the admin token",
    "the backends and their API keys",
    "the metric history and the event log",
]


class AppError(Exception):
    def __init__(self, status: int, kind: str, message: str):
        super().__init__(message)
        self.status, self.kind, self.message = status, kind, message


def _tilde(p: str) -> str:
    home = os.path.expanduser("~")
    return "~" + p[len(home):] if home not in ("", "/") and p.startswith(home) else p


def _digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


# ---------------------------------------------------------------------- permissions

def file_state(path: str, what: str) -> dict:
    """A secret file's mode: `ok` (only its user reads it), `too_open`, or `missing`."""
    try:
        m = stat.S_IMODE(os.stat(path).st_mode)
    except FileNotFoundError:
        return {"what": what, "path": _tilde(path), "mode": None, "state": "missing"}
    except OSError:
        return {"what": what, "path": _tilde(path), "mode": None, "state": "unknown"}
    return {"what": what, "path": _tilde(path), "mode": f"{m:04o}",
            "state": "too_open" if m & 0o077 else "ok"}


def permissions(*, platform: str, lan: str | None, service: dict, key_source: str | None,
                files: list[dict], system_rules: int) -> list[dict]:
    """The rows of A14 for this machine (see PERMISSIONS above)."""
    mac = platform == "darwin"
    rows = []
    # ---- Local Network
    why = ("Find lists the Sparks on your network, and the controller reaches them there.")
    if not mac:
        rows.append({"id": "local_network", "title": "Local Network", "applies": False,
                     "state": "not_applicable", "why": why,
                     "detail": "Linux asks no app for the local network: nothing to allow.",
                     "fallback": None, "action": None})
    else:
        launchd = service.get("manager") == "launchd"
        state = {"granted": "granted", "denied": "lan_denied", "not_asked": "not_asked"}.get(
            lan or "", "unknown")
        detail = {
            "granted": "Allowed: this controller reaches the local network.",
            "lan_denied": "macOS keeps this controller off the local network: every LAN "
                          "address fails (EHOSTUNREACH), not only discovery." +
                          (" A controller started by launchd (the login service) gets no "
                           "prompt for it." if launchd else ""),
            "not_asked": "Not checked yet: Check asks macOS (its prompt shows the first time).",
            "unknown": "This machine has no network to check it on.",
        }[state]
        rows.append({"id": "local_network", "title": "Local Network", "applies": True,
                     "state": state, "why": why, "detail": detail,
                     "fallback": None if state in ("granted", "not_asked") else
                     "Run the controller on a Spark (Move, above), or start it from a Terminal "
                     "window. Adding a Spark by its address needs this permission too.",
                     "settings": "System Settings → Privacy & Security → Local Network",
                     "action": {"kind": "check", "label": "Check now"}})
    # ---- keys
    loose = [f for f in files if f["state"] == "too_open"]
    held = [f for f in files if f["state"] == "ok"]
    master = {"keychain": "in the Keychain", "file": "in a file beside the config",
              "env": "from the environment (TANDEM_SECRETS_KEY)"}.get(key_source or "",
                                                                      "not made yet")
    words = (f"{len(held)} key file{'' if len(held) == 1 else 's'} only your user can read "
             f"(0600). The API keys' master key is {master}.")
    rows.append({"id": "keys", "title": "Keychain and key files", "applies": mac,
                 "state": "too_open" if loose else ("file" if mac else "not_applicable"),
                 "why": "The controller's certificate key pins it to every Spark; the SSH key "
                        "reaches a rented one.",
                 "detail": (("Readable by other users: " + ", ".join(
                     f"{f['what']} ({f['path']}, {f['mode']})" for f in loose) + ". ")
                     if loose else "") +
                 ("The Keychain is not used for the certificate or SSH keys (not built): " if mac
                  else "Linux has no Keychain here: ") + words,
                 "fallback": "chmod 600 " + " ".join(f["path"] for f in loose) if loose
                 else None,
                 "files": files, "action": None})
    # ---- notifications
    rows.append({"id": "notifications", "title": "Notifications", "applies": True,
                 "state": "in_browser",
                 "why": "An alert shows as a notification while the page is closed or hidden.",
                 "detail": f"The browser asks, not the controller (it sends none itself). "
                           f"{system_rules} alert rule{'' if system_rules == 1 else 's'} "
                           f"notif{'ies' if system_rules == 1 else 'y'} this way.",
                 "fallback": "Alerts stay in Events & Alerts and on the fleet readout.",
                 "action": {"kind": "browser", "label": "Allow notifications"}})
    # ---- sudo on a node
    rows.append({"id": "node_sudo", "title": "Administrator rights on a Spark", "applies": True,
                 "state": "per_action",
                 "why": "Setting up a fabric link changes the Spark's network (link addresses, "
                        "MTU, modules).",
                 "detail": "Asked for each such action on that Spark, used once, never stored. "
                           "Nothing else on a node needs them.",
                 "fallback": None, "action": None})
    return rows


def _proc_unit(read=None) -> str | None:
    """The systemd unit this process runs in, from /proc/self/cgroup."""
    try:
        text = read() if read else open("/proc/self/cgroup").read()
    except OSError:
        return None
    for line in text.splitlines():
        for part in reversed(line.rsplit(":", 1)[-1].split("/")):
            if part.endswith(".service") and not part.startswith("user@"):
                return part
    return None


def service_view(*, platform: str, env, run, cgroup=None) -> dict:
    """How this process runs: under systemd (its unit, enabled, restarted on failure, the user's
    linger), launchd (its label, kept alive) or from a terminal."""
    out = {"manager": "unknown", "unit": None, "starts_at_boot": None, "restarts": None,
           "linger": None, "text": ""}
    if platform == "darwin":
        label = env.get("XPC_SERVICE_NAME") or ""
        if not label or label == "0" or label.startswith("application."):
            out.update(manager="none", text="Started from a terminal or an app: it stops with "
                                            "it. `tandem-app service install` keeps it running.")
            return out
        out.update(manager="launchd", unit=label)
        rc, text, _ = run(["launchctl", "print", f"gui/{os.getuid()}/{label}"])
        if rc == 0:
            keep = re.search(r"^\s*keepalive\s*=\s*(\S+)", text, re.M | re.I)
            out["restarts"] = None if keep is None else keep.group(1).lower() not in ("0", "false")
            out["starts_at_boot"] = True
        out["text"] = (f"A launchd agent ({label}): it starts at login and while the Mac is "
                       f"awake.")
        return out
    unit = _proc_unit(cgroup)
    if unit is None:
        out.update(manager="none", text="Started from a shell: it stops with it. `tandem-app "
                                        "service install` keeps it running.")
        return out
    out.update(manager="systemd", unit=unit)
    rc, text, _ = run(["systemctl", "--user", "show", unit, "-p", "UnitFileState", "-p",
                       "Restart"])
    if rc == 0:
        got = dict(x.split("=", 1) for x in text.splitlines() if "=" in x)
        if got.get("UnitFileState"):
            out["starts_at_boot"] = got["UnitFileState"] in ("enabled", "linked", "static")
        if got.get("Restart"):
            out["restarts"] = got["Restart"] != "no"
    user = env.get("USER") or ""
    if user:
        rc, text, _ = run(["loginctl", "show-user", user, "-p", "Linger"])
        if rc == 0 and "=" in text:
            out["linger"] = text.strip().split("=", 1)[1] == "yes"
    words = [f"A systemd user unit ({unit})"]
    if out["starts_at_boot"] is not None:
        words.append("enabled" if out["starts_at_boot"] else "not enabled at boot")
    if out["restarts"] is not None:
        words.append("restarted when it fails" if out["restarts"] else "not restarted")
    if out["linger"] is False:
        words.append("it stops at logout (loginctl enable-linger keeps it)")
    out["text"] = ", ".join(words) + "."
    return out


def _run(argv: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, "", str(exc)
    return r.returncode, r.stdout, r.stderr


# ---------------------------------------------------------------------- the file's shape

def _str(v, cap: int) -> bool:
    return isinstance(v, str) and len(v) <= cap


def check_file(doc) -> dict:
    """The file held to its shape (AppError 400 naming the first fault); the lists bounded."""
    from proxy import ctlmove
    if not isinstance(doc, dict):
        raise AppError(400, "bad_request", "the file is a JSON object")
    if doc.get("kind") == ctlmove.KIND:
        raise AppError(400, "bad_request", "this is a controller move's export (with its keys): "
                                           "take the fleet with Take the fleet instead")
    if doc.get("kind") != KIND:
        raise AppError(400, "bad_request", f"not a TandemApp fleet configuration (kind is "
                                           f"{KIND})")
    if doc.get("version") != 1:
        raise AppError(400, "bad_request", f"version {doc.get('version')!r}: this controller "
                                           f"reads version 1")
    for k, cap in LIMITS.items():
        v = doc.get(k, {} if k == "settings" else [])
        want = dict if k == "settings" else list
        if not isinstance(v, want):
            raise AppError(400, "bad_request", f"{k} is {'an object' if want is dict else 'a list'}")
        if len(v) > cap:
            raise AppError(400, "bad_request", f"{k}: at most {cap}")
    for i, n in enumerate(doc.get("nodes") or []):
        if not isinstance(n, dict) or not _str(n.get("id"), 80) or not ID_RE.match(n["id"]):
            raise AppError(400, "bad_request", f"nodes[{i}]: id is 1 to 80 of letters, digits, "
                                               f"'.', '_', ':', '-'")
        for k in ("alias", "address", "transport", "class", "hw", "icon"):
            if n.get(k) is not None and not _str(n[k], 200):
                raise AppError(400, "bad_request", f"nodes[{i}].{k} is a string")
    for i, d in enumerate(doc.get("deployments") or []):
        if not isinstance(d, dict) or not _str(d.get("id"), 64) or not ID_RE.match(d["id"]):
            raise AppError(400, "bad_request", f"deployments[{i}]: id is a deployment id")
        if not isinstance(d.get("members"), list) or not all(
                isinstance(m, dict) and _str(m.get("node"), 80) for m in d["members"]):
            raise AppError(400, "bad_request", f"deployments[{i}].members is a list of "
                                               f"{{node, engine?, backend?, model?}}")
    for i, r in enumerate(doc.get("rules") or []):
        if not isinstance(r, dict) or not _str(r.get("id"), 80):
            raise AppError(400, "bad_request", f"rules[{i}]: id is a rule id")
    for i, x in enumerate(doc.get("layouts") or []):
        if not isinstance(x, dict) or not isinstance(x.get("doc"), dict) or \
                not _str(x.get("page"), 80) or not x["page"] or not _str(x.get("device"), 16) or \
                any(ord(c) < 32 for c in x["page"]):
            raise AppError(400, "bad_request", f"layouts[{i}] is {{page, device, doc}}")
    for k in doc.get("settings") or {}:
        if not _str(k, 120) or any(ord(c) < 32 for c in k):
            raise AppError(400, "bad_request", "a setting's key is a string")
    return doc


def _was(k: str, a, b) -> str:
    """`alias Gold → Golden`, `enabled on → off`: a changed field in words."""
    def w(v):
        if isinstance(v, bool):
            return "on" if v else "off"
        if isinstance(v, list):
            return ", ".join(map(str, v)) or "none"
        return "none" if v is None else str(v)
    return f"{k} {w(a)} → {w(b)}"


def _ip(text) -> str | None:
    try:
        return str(ipaddress.ip_address(str(text or "").strip()))
    except ValueError:
        return None


# ---------------------------------------------------------------------- the settings

class AppSettings:
    def __init__(self, fleet, *, platform: str = sys.platform, env=None, run=_run, cgroup=None,
                 clock=time.time):
        self.fleet = fleet
        self.platform, self.env, self.run, self.cgroup = platform, env or os.environ, run, cgroup
        self.clock = clock
        self._service: tuple[float, dict] | None = None
        self._tokens: dict[str, tuple] = {}
        self._lock = threading.Lock()

    @property
    def cfg(self):
        return self.fleet.cfg

    # ------------------------------------------------------------------ the view
    def service(self) -> dict:
        now = time.monotonic()
        if self._service is None or now - self._service[0] > SERVICE_CACHE_S:
            self._service = (now, service_view(platform=self.platform, env=self.env,
                                               run=self.run, cgroup=self.cgroup))
        return self._service[1]

    def _files(self) -> list[dict]:
        root = os.path.dirname(self.cfg.path)
        ctl = os.path.join(root, "controller")
        out = [file_state(os.path.join(ctl, "controller.key"), "the controller's certificate key")]
        from proxy import secrets as vault_mod
        if self.cfg.key_source() == "file":
            out.append(file_state(os.path.join(root, vault_mod.KEY_FILE), "the API keys' master key"))
        pw = os.path.join(root, "admin.json")
        if os.path.exists(pw):
            out.append(file_state(pw, "the admin password's hash"))
        kd = os.path.join(ctl, "keys")
        if os.path.isdir(kd):
            for name in sorted(os.listdir(kd)):
                out.append(file_state(os.path.join(kd, name), "a kept SSH key"))
        return [f for f in out if f["state"] != "missing"]

    def _lan(self) -> str | None:
        if self.platform != "darwin":
            return "not_needed"
        d = getattr(self.fleet, "_discovery", None)
        return d.lan if d is not None else "not_asked"

    def view(self, now: float) -> dict:
        svc = self.service()
        system = sum(1 for r in self.fleet.watch.rules() if r["enabled"] and
                     "system" in r["notify"])
        return {"contract_version": CONTRACT,
                "platform": "mac" if self.platform == "darwin" else "linux",
                "release": _version.release() or None,
                "permissions": permissions(platform=self.platform, lan=self._lan(),
                                           service=svc, key_source=self.cfg.key_source(),
                                           files=self._files(), system_rules=system),
                "service": svc,
                "export": {"kind": KIND, "version": 1, "secrets": "excluded",
                           "excluded": EXCLUDED}}

    def check_lan(self, now: float) -> dict:
        self.fleet.discover().check_lan()
        return self.view(now)

    # ------------------------------------------------------------------ the export
    def _local(self) -> str | None:
        try:
            return self.fleet.ensure_local()
        except Exception:                                            # noqa: BLE001
            return getattr(self.fleet, "_local_id", None)

    def export(self, now: float) -> dict:
        lid = self._local()
        nodes = []
        for r in self.cfg.nodes():
            t = r["trust"] or {}
            nodes.append({"id": r["id"], "alias": r["alias"], "colour": r["colour"],
                          "icon": r["icon"], "tags": list(r["tags"] or []), "class": r["class"],
                          "hw": r["hw"], "address": r["address"], "transport": r["transport"],
                          "lease_end_ms": r["lease_end_ms"], "cost_per_hour": r["cost_per_hour"],
                          "controller_host": r["id"] == lid,
                          "paired": t.get("kind") == "mtls",
                          "pin_sha256": t.get("fingerprint") if t.get("kind") == "mtls" else None})
        deps = [{k: d[k] for k in ("id", "state") + DEP_FIELDS} for d in self.cfg.deployments()]
        rules = [{k: r[k] for k in ("id",) + RULE_FIELDS} for r in self.fleet.watch.rules()]
        layouts = [{"page": x["page"], "device": x["device"], "doc": x}
                   for x in self.cfg.layouts()]
        try:
            tr = self.fleet.trust()
            src = {"name": tr.name, "fingerprint": tr.fingerprint()}
        except Exception:                                            # noqa: BLE001
            src = {"name": None, "fingerprint": None}
        return {"kind": KIND, "version": 1, "release": _version.release() or None,
                "exported_at": round(now, 3), "from": dict(src, node=lid),
                "secrets": "excluded", "excluded": EXCLUDED, "nodes": nodes,
                "deployments": deps, "rules": rules, "layouts": layouts,
                "settings": self.cfg.settings_under(SETTINGS_PREFIX)}

    # ------------------------------------------------------------------ the plan
    def _state(self) -> str:
        """The registry as an import reads it: a plan's confirm holds only while this is so."""
        return _digest({"nodes": [{k: r[k] for k in ("id", "endpoint", "address") + NODE_FIELDS}
                                  | {"trust": (r["trust"] or {}).get("kind")}
                                  for r in self.cfg.nodes()],
                        "deps": self.cfg.deployments(), "rules": self.fleet.watch.rules(),
                        "layouts": self.cfg.layouts(),
                        "settings": self.cfg.settings_under(SETTINGS_PREFIX)})

    def _body(self, body) -> tuple[dict, str]:
        if not isinstance(body, dict) or "config" not in body or \
                set(body) - {"config", "mode", "confirm"}:
            raise AppError(400, "bad_request", "the body is {config, mode?, confirm?}: config is "
                                               "the exported file")
        mode = body.get("mode") or "merge"
        if mode not in MODES:
            raise AppError(400, "bad_request", "mode is merge or replace")
        return check_file(body["config"]), mode

    def plan(self, body, now: float, *, mint: bool = True) -> dict:
        doc, mode = self._body(body)
        f = self.fleet
        lid = self._local()
        items: list[dict] = []

        def item(kind, iid, label, op, detail=None, fields=None, apply=None):
            items.append({"kind": kind, "id": iid, "label": label, "op": op, "detail": detail,
                          "fields": fields or [], "_apply": apply})

        try:
            own_fp = f.trust().fingerprint()
        except Exception:                                            # noqa: BLE001
            own_fp = None
        src = doc.get("from") if isinstance(doc.get("from"), dict) else {}
        same = bool(own_fp) and src.get("fingerprint") == own_fp

        # ---- nodes
        here = {r["id"]: r for r in f.cfg.nodes()}
        file_ids = set()
        aliases = {r["alias"].lower(): r["id"] for r in here.values()}
        colours = {r["colour"]: r["id"] for r in here.values()}
        added_nodes: set[str] = set()
        for n in doc.get("nodes") or []:
            nid = n["id"]
            if nid in file_ids:
                item("node", nid, n.get("alias") or nid, "conflict", "listed twice in the file")
                continue
            file_ids.add(nid)
            want = self._node_fields(n)
            if isinstance(want, str):
                item("node", nid, str(n.get("alias") or nid), "conflict", want)
                continue
            label = want.get("alias") or nid
            r = here.get(nid)
            a = want.get("alias")
            if a and aliases.get(a.lower()) not in (None, nid):
                item("node", nid, label, "conflict",
                     f"the alias {a} is {here[aliases[a.lower()]]['alias']}'s here")
                continue
            note = []
            c = want.get("colour")
            if c is not None and colours.get(c) not in (None, nid):
                note.append(f"colour {c} is {here[colours[c]]['alias']}'s here: it keeps its own")
                want.pop("colour")
            if r is not None:
                changed = [k for k in NODE_FIELDS if k in want and want[k] != r[k]]
                own = r["id"] == lid or r["endpoint"] == "local"
                if own:
                    note.insert(0, "this controller's own machine: name and looks only")
                pin = n.get("pin_sha256")
                fp = (r["trust"] or {}).get("fingerprint")
                if pin and fp and pin != fp:
                    note.append("paired here with another certificate: the pairing here stays")
                if changed:
                    note = [_was(k, r[k], want[k]) for k in changed] + note
                    item("node", nid, r["alias"], "change", "; ".join(note), changed,
                         ("node-change", nid, {k: want[k] for k in changed}))
                    if "alias" in changed:
                        aliases.pop(r["alias"].lower(), None)
                        aliases[want["alias"].lower()] = nid
                    if "colour" in changed:
                        colours.pop(r["colour"], None)
                        colours[want["colour"]] = nid
                else:
                    item("node", nid, r["alias"], "keep", "; ".join(note) or None)
                continue
            addr = _ip(n.get("address"))
            if n.get("transport") == "tunnel":
                item("node", nid, label, "conflict", "reached through an SSH tunnel: add it "
                     "again with its key (Add Spark), the file holds no route")
                continue
            if addr is None:
                item("node", nid, label, "conflict", "no address to pair it at")
                continue
            if not a:
                item("node", nid, label, "conflict", "no alias")
                continue
            if any(x["address"] == addr and x["endpoint"] != "local" for x in here.values()):
                item("node", nid, label, "conflict", f"a node here has the address {addr} "
                                                     f"already")
                continue
            note.insert(0, ("the exporting controller's own machine; " if n.get("controller_host")
                            and not same else "") + f"added unpaired at {addr}: pair it with "
                                                    f"Add Spark, it keeps this name")
            rec = {"id": nid, "alias": a, "icon": want.get("icon") or "desk",
                   "tags": want.get("tags") or [], "class": n.get("class") or "spark",
                   "hw": n.get("hw"), "transport": "lan", "address": addr, "trust": {},
                   "endpoint": f"https://{addr if ':' not in addr else '[' + addr + ']'}:"
                               f"{AGENT_PORT}"}
            if "colour" in want:
                rec["colour"] = want["colour"]
                colours[want["colour"]] = nid
            aliases[a.lower()] = nid
            added_nodes.add(nid)
            item("node", nid, a, "add", "; ".join(note), sorted(k for k in want),
                 ("node-add", nid, rec))
        if mode == "replace":
            for r in here.values():
                if r["id"] in file_ids:
                    continue
                t = (r["trust"] or {}).get("kind")
                if r["id"] == lid or r["endpoint"] == "local" or t:
                    item("node", r["id"], r["alias"], "keep",
                         "this controller's own machine" if r["id"] == lid else
                         "paired: release it on its page to remove it")
                else:
                    item("node", r["id"], r["alias"], "remove", "not paired, not in the file",
                         None, ("node-remove", r["id"], None))
        known_nodes = (set(here) | added_nodes) - {i["id"] for i in items
                                                   if i["kind"] == "node" and i["op"] == "remove"}

        # ---- deployments
        recs = {d["id"]: d for d in f.cfg.deployments()}
        backends = {b["id"] for b in f.cfg.backends()}
        seen = set()
        for d in doc.get("deployments") or []:
            did = d["id"]
            label = d.get("name") or did
            if did in seen:
                item("deployment", did, label, "conflict", "listed twice in the file")
                continue
            seen.add(did)
            bad = self._dep_shape(d)
            if bad:
                item("deployment", did, label, "conflict", bad)
                continue
            want = {k: d.get(k) for k in DEP_FIELDS}
            want["pack"] = want["pack"] or ""
            want["stages"] = want["stages"] or []
            want["options"] = want["options"] or {}
            missing = [m["node"] for m in d["members"] if m["node"] not in known_nodes]
            if missing:
                item("deployment", did, label, "conflict", f"its member {missing[0]} is not a "
                                                           f"node here")
                continue
            gone = [m["backend"] for m in d["members"] if m.get("backend") and
                    m["backend"] not in backends]
            r = recs.get(did)
            if r is not None:
                diff = [k for k in DEP_FIELDS if want[k] != r[k]]
                if not diff:
                    item("deployment", did, r["name"], "keep")
                elif r["state"] == "serving":
                    item("deployment", did, r["name"], "conflict",
                         f"serves here and differs ({', '.join(diff)}): stop it on Models first")
                elif gone:
                    item("deployment", did, r["name"], "conflict", f"its engine {gone[0]} is not "
                         f"known here: serve it from Models once its Spark answers")
                else:
                    item("deployment", did, r["name"], "change", "stays stopped", diff,
                         ("dep-put", did, dict(want, id=did, state="stopped")))
                continue
            if gone:
                item("deployment", did, label, "conflict", f"its engine {gone[0]} is not known "
                     f"here: serve it from Models once its Spark answers")
                continue
            api = (want["options"] or {}).get("api_id") or want["model"]
            clash = next((x for x in recs.values() if api in (
                x["id"], (x.get("options") or {}).get("api_id"))), None)
            if clash is not None:
                item("deployment", did, label, "conflict", f"{clash['name']} answers to {api} "
                                                           f"here already")
                continue
            item("deployment", did, label, "add", "arrives stopped: start it on Models",
                 None, ("dep-put", did, dict(want, id=did, state="stopped")))
        if mode == "replace":
            for r in recs.values():
                if r["id"] in seen:
                    continue
                if r["state"] == "serving":
                    item("deployment", r["id"], r["name"], "conflict",
                         "serves here: stop it on Models to remove it")
                else:
                    item("deployment", r["id"], r["name"], "remove", "stopped, not in the file",
                         None, ("dep-delete", r["id"], None))

        # ---- rules
        rules = {r["id"]: r for r in f.watch.rules()}
        edited = f.watch.edited()
        seen = set()
        for x in doc.get("rules") or []:
            rid = x["id"]
            seen.add(rid)
            r = rules.get(rid)
            if r is None:
                item("rule", rid, rid, "conflict", "no such rule in this release")
                continue
            want = {k: x[k] for k in RULE_FIELDS if k in x}
            if r["threshold"] is None:
                want.pop("threshold", None)
            if r["scope"] == "host":
                want.pop("scope", None)
            sc = want.get("scope")
            if sc not in (None, "all") and not (isinstance(sc, str) and sc.startswith("node:")
                                                and sc[5:] in known_nodes):
                item("rule", rid, r["name"], "conflict", f"its scope {sc} names no node here")
                continue
            diff = [k for k in want if want[k] != r[k]]
            if diff:
                item("rule", rid, r["name"], "change",
                     "; ".join(_was(k, r[k], want[k]) for k in diff), diff,
                     ("rule-edit", rid, {k: want[k] for k in diff}))
            else:
                item("rule", rid, r["name"], "keep")
        if mode == "replace":
            for rid in edited:
                if rid not in seen:
                    item("rule", rid, rules[rid]["name"], "remove", "back to its default",
                         None, ("rule-reset", rid, None))

        # ---- layouts
        from proxy import fleet as fleet_mod
        have = {(x["page"], x["device"]): x for x in f.cfg.layouts()}
        seen = set()
        for x in doc.get("layouts") or []:
            key = (x["page"], x["device"])
            label = f"{x['page']} · {x['device']}"
            seen.add(key)
            try:
                if x["device"] not in fleet_mod.DEVICES:
                    raise fleet_mod.FleetError(400, f"device {x['device']}")
                lay = fleet_mod.check_layout(x["doc"], x["page"], x["device"])
            except fleet_mod.FleetError as exc:
                item("layout", label, label, "conflict", f"not a layout: {exc.message}")
                continue
            lay = dict(lay, saved_at=x["doc"].get("saved_at"))
            old = have.get(key)
            strip = (lambda d: {k: v for k, v in d.items() if k != "saved_at"})  # noqa: E731
            if old is not None and strip(old) == strip(lay):
                item("layout", label, label, "keep")
            else:
                item("layout", label, label, "add" if old is None else "change", None, None,
                     ("layout-put", label, lay))
        if mode == "replace":
            for key in have:
                if key not in seen:
                    label = f"{key[0]} · {key[1]}"
                    item("layout", label, label, "remove", "back to the page's default", None,
                         ("layout-delete", label, key))

        # ---- settings
        cur = f.cfg.settings_under(SETTINGS_PREFIX)
        for k, v in (doc.get("settings") or {}).items():
            if not k.startswith(SETTINGS_PREFIX):
                item("setting", k, k, "conflict", f"not an app setting ({SETTINGS_PREFIX}…)")
            elif cur.get(k) == v:
                item("setting", k, k, "keep")
            else:
                item("setting", k, k, "add" if k not in cur else "change", None, None,
                     ("setting-put", k, v))
        if mode == "replace":
            for k in cur:
                if k not in (doc.get("settings") or {}):
                    item("setting", k, k, "remove", None, None, ("setting-delete", k, None))

        counts = {op: sum(1 for i in items if i["op"] == op)
                  for op in ("add", "change", "remove", "keep", "conflict")}
        blocked = None
        if f.cfg.setting("controller.moved_to"):
            blocked = "this controller moved: import into the one that runs the fleet now"
        elif any(j and j.get("state") == "running" for j in
                 (f.cfg.job("move"), f.cfg.job("update"))):
            blocked = "a controller move or a fleet update runs: import once it ends"
        elif not counts["add"] + counts["change"] + counts["remove"]:
            blocked = "nothing to change: the registry is as the file has it"
        p = {"contract_version": CONTRACT, "mode": mode,
             "from": {"name": src.get("name"), "fingerprint": src.get("fingerprint"),
                      "exported_at": doc.get("exported_at"), "release": doc.get("release"),
                      "same_controller": same},
             "identity": ("this controller's certificate, its own node's address and trust, "
                          "and its admin password stay as they are" +
                          ("" if same else "; the file comes from another controller: the "
                                           "nodes it adds trust that one, pair them here")),
             "items": [{k: v for k, v in i.items() if k != "_apply"} for i in items],
             "counts": counts, "allowed": blocked is None, "blocked": blocked,
             "confirm": None, "expires_at": None}
        p["_apply"] = [i["_apply"] for i in items if i["_apply"] is not None]
        if blocked is None and mint:
            token = secrets.token_urlsafe(18)
            bound = (_digest(doc), mode, self._state())
            with self._lock:
                self._tokens = {k: v for k, v in self._tokens.items() if v[3] > now}
                self._tokens[token] = (*bound, now + CONFIRM_S)
            p["confirm"], p["expires_at"] = token, round(now + CONFIRM_S, 3)
        return p

    @staticmethod
    def _node_fields(n: dict) -> dict | str:
        from proxy import fleet as fleet_mod
        want = {}
        if "alias" in n:
            a = n["alias"].strip() if isinstance(n["alias"], str) else ""
            if not a or len(a) > fleet_mod.ALIAS_MAX or any(ord(c) < 32 for c in a):
                return f"its alias is not 1 to {fleet_mod.ALIAS_MAX} characters"
            want["alias"] = a
        if "colour" in n and n["colour"] is not None:
            c = n["colour"]
            if not isinstance(c, int) or isinstance(c, bool) or c not in fleet_mod.COLOURS:
                return "its colour is not 1 to 8"
            want["colour"] = c
        if "icon" in n and n["icon"] is not None:
            if n["icon"] not in fleet_mod.ICONS:
                return "its icon is not desk, cloud or rack"
            want["icon"] = n["icon"]
        if "tags" in n and n["tags"] is not None:
            t = n["tags"]
            if not isinstance(t, list) or len(t) > fleet_mod.TAGS_MAX or not all(
                    isinstance(x, str) and 0 < len(x.strip()) <= fleet_mod.TAG_MAX for x in t):
                return "its tags are not a list of short words"
            want["tags"] = list(dict.fromkeys(x.strip() for x in t))
        return want

    @staticmethod
    def _dep_shape(d: dict) -> str | None:
        from proxy import deployments as dep_mod
        if not dep_mod.ID_RE.match(d["id"]):
            return "its id is not 1 to 64 of a-z, 0-9, '.', '_', '-'"
        if d.get("type") not in dep_mod.TYPES:
            return f"its type is not one of {', '.join(dep_mod.TYPES)}"
        if not _str(d.get("model"), 200) or not d["model"]:
            return "its model is not a string"
        for k, cap in (("name", 120), ("pack", 80)):
            if d.get(k) is not None and not _str(d[k], cap):
                return f"its {k} is not a string of at most {cap} characters"
        o = d.get("options") or {}
        if not isinstance(o, dict) or set(o) - {"api_id", "ctx", "role", "role_of", "affinity"}:
            return "its options are not {api_id, ctx, role?, role_of?, affinity?}"
        api = o.get("api_id")
        if api is not None and (not _str(api, 200) or not api or any(c.isspace() for c in api)):
            return "its api_id is not 1 to 200 characters without spaces"
        if o.get("ctx") is not None and (not isinstance(o["ctx"], int) or
                                         isinstance(o["ctx"], bool) or o["ctx"] < 0):
            return "its ctx is not a token count"
        if o.get("affinity") is not None and o["affinity"] not in dep_mod.AFFINITY:
            return "its affinity is not root or own"
        for k in ("role", "role_of"):
            if o.get(k) is not None and not _str(o[k], 80):
                return f"its {k} is not a short string"
        st = d.get("stages") or []
        if not isinstance(st, list) or any(
                not isinstance(x, dict) or set(x) != {"node", "layers", "gib"} or
                not _str(x["node"], 80) or not _str(x["layers"], 80) or
                not isinstance(x["gib"], (int, float)) or isinstance(x["gib"], bool) for x in st):
            return "its stages are not {node, layers, gib}"
        if st and d.get("type") != "split":
            return "stages are a Split's"
        for m in d["members"]:
            if set(m) - {"node", "engine", "backend", "model"} or any(
                    m.get(k) is not None and not _str(m[k], 200)
                    for k in ("engine", "backend", "model")):
                return "a member is not {node, engine?, backend?, model?}"
        n = len(d["members"])
        t = d["type"]
        if (t in ("single", "role") and n != 1) or (t == "replicas" and n < 2) or \
                (t == "split" and n not in (2, 4)):
            return f"a {t.title()} with {n} member{'' if n == 1 else 's'}"
        return None

    # ------------------------------------------------------------------ apply
    def apply(self, body, who: str, now: float) -> dict:
        if not isinstance(body, dict) or not body.get("confirm"):
            raise AppError(428, "confirm_required", "an import needs its plan's confirm: POST "
                                                    "/v1/fleet/app/import first and send its "
                                                    "`confirm`")
        doc, mode = self._body(body)
        with self._lock:
            got = self._tokens.pop(body["confirm"], None) \
                if isinstance(body["confirm"], str) else None
        if got is None or got[3] <= now or got[:2] != (_digest(doc), mode):
            raise AppError(409, "confirm_invalid", "that confirm is spent, too old or another "
                                                   "file's: read the plan again")
        if got[2] != self._state():
            raise AppError(409, "confirm_invalid", "the fleet changed since the plan: read the "
                                                   "plan again")
        p = self.plan(body, now, mint=False)
        if not p["allowed"]:
            raise AppError(409, "conflict", p["blocked"])
        f = self.fleet
        done = {"add": 0, "change": 0, "remove": 0}
        order = ("node-add", "node-change", "dep-put", "rule-edit", "rule-reset", "layout-put",
                 "layout-delete", "setting-put", "setting-delete", "dep-delete", "node-remove")
        ops = sorted(p.pop("_apply"), key=lambda a: order.index(a[0]))
        nodes = None
        for op, key, val in ops:
            if op == "node-add":
                f.cfg.add_node(val, who=who)
            elif op == "node-change":
                f.cfg.update_node(key, val, who=who)
            elif op == "dep-put":
                f.cfg.put_deployment(val, who=who)
            elif op == "rule-edit":
                nodes = nodes or [n["id"] for n in f.cfg.nodes()]
                f.watch.edit(key, val, who, nodes)
            elif op == "rule-reset":
                f.watch.reset(key, who)
            elif op == "layout-put":
                f.cfg.set_layout(dict(val, saved_at=val.get("saved_at") or round(now, 3)))
            elif op == "layout-delete":
                f.cfg.delete_layout(*val)
            elif op == "setting-put":
                f.cfg.set_setting(key, val, who=who)
            elif op == "setting-delete":
                f.cfg.delete_setting(key, who=who)
            elif op == "dep-delete":
                f.cfg.delete_deployment(key, who=who)
            elif op == "node-remove":
                f.cfg.delete_node(key, who=who)
        for i in p["items"]:
            if i["op"] in done:
                done[i["op"]] += 1
        text = (f"Fleet configuration imported ({mode}): {done['add']} added, {done['change']} "
                f"changed, {done['remove']} removed, {p['counts']['conflict']} skipped")
        f.cfg.log_audit(who, "import", "fleet-config", None, detail={"text": text})
        f.store.add_event("controller", "host", text,
                          f"from {p['from']['name'] or 'a file'}")
        f.log(f"[fleet] {text} by {who}")
        return {"contract_version": CONTRACT, "mode": mode, "applied": done,
                "skipped": p["counts"]["conflict"], "items": p["items"]}

    # ------------------------------------------------------------------ routes
    def handle(self, method: str, parts: list[str], q: dict, body, who: str, now: float) -> dict:
        sub = parts[1:]
        if method == "GET" and not sub:
            return self.view(now)
        if method == "POST" and sub == ["permissions", "local-network"]:
            return self.check_lan(now)
        if method == "GET" and sub == ["export"]:
            return self.export(now)
        if method == "POST" and sub == ["import"]:
            p = self.plan(body, now)
            p.pop("_apply", None)
            return p
        if method == "POST" and sub == ["import", "apply"]:
            return self.apply(body, who, now)
        raise AppError(404, "not_found", f"no route {method} /v1/fleet/{'/'.join(parts)}")
