"""chatd on a TandemNode (SRV-823, ENG-413 WP6): the Chat tab's service, owned by this agent on an
engine box.

    chat = Chat.load(state_dir, tandem_dir, engines, own=False)
    chat.view()                  what runs and who owns it (GET /node/chat)
    chat.act(action, who)        start | stop | restart: a job (POST /node/chat/{action})
    chat.tick()                  the watcher, once a minute; chat.kick() after an engine start here

WHO OWNS CHATD. One owner at a time. Up to engine API 1.6 the engine's launchers own it while the
engine's QSE_CHATD is on, their default: ops/start.sh starts it once the engine answers,
ops/watchdog.sh starts it again, bin/tandem start and stop. This agent owns it with `tandem-node
serve --chat on` (TANDEM_NODE_CHAT=on) or `"own": true` in chat.json; the engine gives it up with
QSE_CHATD=node (engine API 1.6 or later within one major, contracts/engine-api-v1/node/control.json
`chat`) or QSE_CHATD=off (any engine). A 1.7 engine has no chatd launcher: its serve.env reads on
as node and unset as off, so this agent reads node or off from it. While the engine still says on,
or says node without knowing it (an engine API before 1.6, or of another major), the watcher
starts nothing and says why once an hour: two owners would race. A hand verb still acts.
Without ownership every verb is refused (409 `not_owned`) and the watcher does nothing: an engine
box without TandemNode's chat keeps the engine's launchers, and an engine-only box has no chatd.

THE LAUNCHER is TandemApp's ops/chatd.sh (chatd/ops/chatd.sh in a wheel install, OPS-250), the
script the engine's launchers ran before engine API 1.7: its pid file
(<data>/chatd.pid, so either side sees the other's chatd), its interpreter order, the forward key
from the engine's secrets.env. It runs in the engine's environment as ops/start.sh exports it
(`serve_env`, which reads its local.env, then the secrets.env it names; chatd.sh holds the admin and
metrics tokens back) and with the engine tree's run/chatd.env, so chatd's data folder, port, auth,
interpreter and the engine it calls are what they were. Its log is <state dir>/logs/chatd.log
(QSE_CHAT_LOG). Under systemd a start runs in a scope of its own (systemd-run --user --scope), so a
restart of this agent, its update included, never ends a chat run.

THE WATCHER is ops/watchdog.sh's chatd rule (OPS-155), once a minute, with its log lines:
  - nothing while chatd is not this node's, while the engine does not answer /health with 200,
    while a hold's pause file (<repo>/.watchdog.off, younger than 2,400 s) is there, in setup mode
    (<repo>/run/setup-pending, or /health mode setup), or after a hand stop until a hand start
  - a running chatd clears the count
  - a dead one is started, at most 3 starts in a clock hour (<state dir>/chat.watch: the hour and
    the count), then one line "giving up until the next hour"
  - engine mode (/health mode engine) starts none, and an engine seen coming up in engine mode
    stops a running chatd once, as ops/start.sh did on the switch to engine
After an engine start or restart this agent ran, the watcher runs at once and starts a chatd that
is not running, outside the count, as ops/start.sh did after /health.

CHAT.JSON (<state dir>/chat.json), every key optional:

    {"own": true, "engine": "engine", "serve_env": "~/eng/ops/serve.env", "repo": "~/eng",
     "chat_env": "~/eng/run/chatd.env", "launcher": PATH, "log": PATH, "url": URL,
     "data": PATH, "python": PATH, "scope": "auto" | "off"}

    engine      the engine id (engines.json) chatd waits for; default the first TandemLLM engine
    serve_env   the engine's env file; default <tandem dir>/run/ops/serve.env
    repo        the engine's tree (pause file, setup marker); default <tandem dir>/src, else the
                folder above serve_env's ops/
    chat_env    chatd's own env file (QSE_CHATD_ENV); default <tandem dir>/run/chatd.env, else
                <repo>/run/chatd.env, as chatd.sh finds it beside the engine
    url, data   QSE_CHAT_URL and QSE_CHAT_DATA over the engine's; default: the engine's
    python      CHATD_PY; default chatd.sh's order, or this interpreter on a box with no engine env
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERBS = ("start", "stop", "restart", "status", "logs")
ACTIONS = ("start", "stop", "restart")
#: the engine's QSE_CHATD value that hands chatd to TandemNode, and the engine API that knows it
NODE_VALUE = "node"
NEEDS_API = (1, 6)
MAX_STARTS = 3                         # an hour, as ops/watchdog.sh
PAUSE_MAX_S = 2400                     # WATCHDOG_PAUSE_MAX: a pause file older than this is stale
EVERY_S = 60.0                         # the watchdog's cron minute
LAUNCH_TIMEOUT_S = 90.0                # chatd.sh waits 10 s for /health, its stop 30 s and a KILL
STATUS_TIMEOUT_S = 15.0
KEYS = ("own", "engine", "serve_env", "repo", "chat_env", "launcher", "log", "url", "data",
        "python", "scope")

# the engine's environment as ops/start.sh exports it (serve.env, then load_secrets), the overrides
# of chat.json, then the launcher; nothing here is ever printed
_WRAP = r"""
if [ -n "${TN_SERVE_ENV:-}" ]; then
    set -a
    . "$TN_SERVE_ENV"
    TN_S="${QSE_SECRETS:-${QSE_STATE_DIR:+$QSE_STATE_DIR/secrets.env}}"
    TN_S="${TN_S:-$HOME/.qwen38-spark-engine/secrets.env}"
    if [ -f "$TN_S" ]; then . "$TN_S"; fi
    set +a
    unset TN_S
fi
[ -z "${TN_CHAT_URL:-}" ] || export QSE_CHAT_URL="$TN_CHAT_URL"
[ -z "${TN_CHAT_DATA:-}" ] || export QSE_CHAT_DATA="$TN_CHAT_DATA"
[ -z "${TN_CHAT_PY:-}" ] || export CHATD_PY="$TN_CHAT_PY"
[ -z "${TN_CHAT_ENV:-}" ] || export QSE_CHATD_ENV="${QSE_CHATD_ENV:-$TN_CHAT_ENV}"
export QSE_CHAT_LOG="$TN_CHAT_LOG"
unset TN_SERVE_ENV TN_CHAT_URL TN_CHAT_DATA TN_CHAT_PY TN_CHAT_ENV TN_CHAT_LOG
exec bash "$@"
"""
_READ = r"""set -a; . "$1" >/dev/null 2>&1; set +a; printf '%s' "${QSE_CHATD:-on}" """


class ChatError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message

    def body(self) -> dict:
        return {"error": {"type": self.code, "message": self.message}}


def _path(v) -> str | None:
    return os.path.expanduser(str(v)) if v else None


def api_at_least(v, need=NEEDS_API) -> bool:
    m = re.fullmatch(r"(\d+)\.(\d+)", str(v or ""))
    return bool(m) and (int(m[1]), int(m[2])) >= need


def api_hands_over(v, need=NEEDS_API) -> bool:
    """An engine API that knows QSE_CHATD=node: `need` or a later minor of the same major. Another
    major is a contract this TandemApp does not read (proxy/engineapi.py calls it not ok), so it
    gets no hand-over either."""
    m = re.fullmatch(r"(\d+)\.(\d+)", str(v or ""))
    return bool(m) and int(m[1]) == need[0] and int(m[2]) >= need[1]


def default_launcher(root: str = ROOT) -> str:
    """TandemApp's ops/chatd.sh in a checkout; in a wheel install, where `root` is site-packages,
    the chatd package's copy, chatd/ops/chatd.sh (OPS-250). The checkout's path when neither is
    there, so the refusal names where it was looked for."""
    own = os.path.join(root, "ops", "chatd.sh")
    if os.path.isfile(own):
        return own
    packaged = os.path.join(root, "chatd", "ops", "chatd.sh")
    return packaged if os.path.isfile(packaged) else own


def _parse_status(lines: list[str]) -> dict:
    """chatd.sh status's lines: pid, url, data and tree of a running chatd, or url and data of
    none."""
    out: dict = {"pid": None, "url": None, "data": None, "tree": None}
    for ln in lines:
        m = re.match(r"\[chatd\] running: pid (\d+), (\S+?),", ln)
        if m:
            out["pid"], out["url"] = int(m[1]), m[2]
        m = re.match(r"\[chatd\] python .*?, data (.+?)(?:, tree (.+))?$", ln)
        if m:
            out["data"], out["tree"] = m[1], m[2]
        m = re.match(r"\[chatd\] not running \((\S+), data (.+)\)$", ln)
        if m:
            out["url"], out["data"] = m[1], m[2]
    return out


class Chat:
    def __init__(self, state_dir: str, cfg: dict, engines=None, *, log=lambda m: print(m, flush=True),
                 clock=time.time, env=None, which=shutil.which, every_s: float = EVERY_S):
        self.state_dir = os.path.expanduser(state_dir)
        self.cfg = cfg
        self.own = bool(cfg.get("own"))
        self.engines = engines
        self.log, self.clock, self.every_s = log, clock, every_s
        self.env = dict(os.environ if env is None else env)
        self.which = which
        self.launcher = _path(cfg.get("launcher")) or default_launcher()
        self.log_path = _path(cfg.get("log")) or os.path.join(self.state_dir, "logs", "chatd.log")
        self.watch_path = os.path.join(self.state_dir, "chat.watch")
        self.serve_env = _path(cfg.get("serve_env"))
        self.repo = _path(cfg.get("repo"))
        self.chat_env = _path(cfg.get("chat_env"))
        self.python = _path(cfg.get("python"))
        self.scope = cfg.get("scope") or "auto"
        if self.scope not in ("auto", "off"):
            raise ValueError("chat.json scope: auto or off")
        self.engine_id = cfg.get("engine")
        self.job: dict | None = None
        self.lock = threading.Lock()                # the job slot
        self._run = threading.Lock()                # one launcher call at a time
        self._seen = None                           # the engine at the last tick: (up, mode)
        self._kick = threading.Event()
        self._stop = threading.Event()
        self._thread = None

    @classmethod
    def load(cls, state_dir: str, tandem_dir: str | None = None, engines=None, *, own: bool = False,
             **kw) -> "Chat":
        state_dir = os.path.expanduser(state_dir)
        path = os.path.join(state_dir, "chat.json")
        cfg: dict = {}
        if os.path.exists(path):
            with open(path) as f:
                cfg = json.load(f)
            if not isinstance(cfg, dict):
                raise ValueError(f"{path}: an object")
            bad = sorted(set(cfg) - set(KEYS))
            if bad:
                raise ValueError(f"{path}: unknown keys {', '.join(bad)} ({', '.join(KEYS)})")
        cfg = dict(cfg)
        cfg["own"] = bool(cfg.get("own")) or own
        td = os.path.expanduser(tandem_dir) if tandem_dir else None
        if td:
            cfg.setdefault("serve_env", os.path.join(td, "run", "ops", "serve.env"))
            cfg.setdefault("repo", os.path.join(td, "src"))
            cfg.setdefault("chat_env", os.path.join(td, "run", "chatd.env"))
        if cfg.get("serve_env") and not cfg.get("repo"):
            se = os.path.expanduser(cfg["serve_env"])
            if os.path.basename(os.path.dirname(se)) == "ops":
                cfg["repo"] = os.path.dirname(os.path.dirname(se))
        if cfg.get("repo") and not cfg.get("chat_env"):
            cfg["chat_env"] = os.path.join(os.path.expanduser(cfg["repo"]), "run", "chatd.env")
        if not cfg.get("serve_env") and not cfg.get("python"):
            cfg["python"] = sys.executable           # no engine env: TandemApp's own interpreter
        return cls(state_dir, cfg, engines, **kw)

    # ------------------------------------------------------------------ the engine
    def engine(self):
        if self.engines is None:
            return None
        if self.engine_id:
            return self.engines.by_id.get(self.engine_id)
        return next((e for e in self.engines.list if e.kind == "tandemllm"), None)

    def engine_health(self) -> dict | None:
        e = self.engine()
        return self.engines.health(e) if e is not None else None

    def engine_says(self) -> str | None:
        """The engine's QSE_CHATD after its env file (and its mode block), or None without one."""
        if not self.serve_env or not os.path.isfile(self.serve_env):
            return None
        try:
            r = subprocess.run(["bash", "-c", _READ, "tn-read", self.serve_env], capture_output=True,
                               text=True, timeout=STATUS_TIMEOUT_S, env=self.env,
                               stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            return "?"
        return r.stdout.strip() or "on"

    def hands_over(self, h: dict | None) -> tuple[bool | None, str | None, str | None]:
        """(the engine leaves chatd to this node, why not, its QSE_CHATD); None while an engine
        that says node does not answer."""
        v = self.engine_says()
        if v is None or v == "off":
            return True, None, v
        if v == NODE_VALUE:
            if h is None:
                return None, "the engine does not answer: its engine API is not known yet", v
            api = h.get("engine_api")
            if api_hands_over(api):
                return True, None, v
            if api_at_least(api):
                return False, (f"the engine speaks engine API {api}, another major than this "
                               f"TandemNode's 1.x: it does not hand chatd over on QSE_CHATD=node "
                               f"(QSE_CHATD=off does)"), v
            return False, (f"the engine (engine API {api or 'unknown'}) does not know "
                           f"QSE_CHATD=node: its launchers would start chatd too; it needs 1.6, "
                           f"or QSE_CHATD=off"), v
        return False, (f"the engine's launchers own chatd (QSE_CHATD={v}): QSE_CHATD=node in its "
                       f"run/local.env hands it to TandemNode"), v

    # ------------------------------------------------------------------ the launcher
    def _scoped(self) -> bool:
        return self.scope == "auto" and bool(self.env.get("INVOCATION_ID")) and \
            bool(self.env.get("XDG_RUNTIME_DIR")) and self.which("systemd-run") is not None

    def argv(self, verb: str) -> list[str]:
        """The launcher call for a verb: the engine's environment, then chatd.sh; a start in a
        scope of its own under systemd."""
        cmd = ["bash", "-c", _WRAP, "tn-chat", self.launcher, verb]
        if verb in ("start", "restart") and self._scoped():
            cmd = ["systemd-run", "--user", "--scope", "--quiet", "--collect",
                   f"--unit=tandem-chatd-{int(self.clock() * 1000)}", "--"] + cmd
        return cmd

    def launch_env(self) -> dict:
        env = dict(self.env)
        for k, v in (("TN_SERVE_ENV", self.serve_env), ("TN_CHAT_URL", self.cfg.get("url")),
                     ("TN_CHAT_DATA", _path(self.cfg.get("data"))), ("TN_CHAT_PY", self.python),
                     ("TN_CHAT_ENV", self.chat_env), ("TN_CHAT_LOG", self.log_path)):
            if v:
                env[k] = str(v)
            else:
                env.pop(k, None)
        return env

    def _call(self, verb: str, timeout: float = LAUNCH_TIMEOUT_S) -> tuple[int, list[str]]:
        if not os.path.isfile(self.launcher):
            raise ChatError(409, "no_launcher", f"TandemApp's chatd launcher {self.launcher} is "
                                                f"not here (a package without ops/chatd.sh)")
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        try:
            r = subprocess.run(self.argv(verb), capture_output=True, text=True, timeout=timeout,
                               env=self.launch_env(), stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return -1, [f"[chatd] {verb}: no answer from the launcher within {timeout:g} s"]
        lines = [ln for ln in (r.stdout + r.stderr).splitlines() if ln.strip()]
        return r.returncode, lines

    def status(self) -> tuple[bool, list[str]]:
        if not os.path.isfile(self.launcher):
            return False, [f"[chatd] no launcher at {self.launcher}"]
        rc, lines = self._call("status", STATUS_TIMEOUT_S)
        return rc == 0, lines

    def running(self) -> bool:
        return self.status()[0]

    # ------------------------------------------------------------------ the watch file
    def _watch(self) -> dict:
        try:
            with open(self.watch_path) as f:
                doc = json.load(f)
            return doc if isinstance(doc, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, doc: dict) -> None:
        os.makedirs(self.state_dir, exist_ok=True)
        tmp = self.watch_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(doc, f)
        os.replace(tmp, self.watch_path)

    def _update(self, **kw) -> dict:
        doc = self._watch()
        for k, v in kw.items():
            if v is None:
                doc.pop(k, None)
            else:
                doc[k] = v
        self._save(doc)
        return doc

    def hour(self) -> str:
        return time.strftime("%Y%m%d%H", time.localtime(self.clock()))

    # ------------------------------------------------------------------ what runs
    def view(self) -> dict:
        up, lines = self.status()
        st = _parse_status(lines)
        w = self._watch()
        h = self.engine_health() if self.own else None
        ok, why, says = self.hands_over(h) if self.own else (None, None, None)
        e = self.engine()
        job = self.job
        if not self.own:
            owner = "engine"
        elif ok is False:
            owner = "both"
        else:
            owner = "node"
        return {"own": self.own, "owner": owner, "state": "running" if up else "stopped",
                "pid": st["pid"], "url": st["url"], "data": st["data"], "tree": st["tree"],
                "status": lines, "launcher": self.launcher, "log": self.log_path,
                "engine": e.id if e is not None else None,
                "engine_chatd": says, "hands_over": ok, "why": why,
                "watch": {"hour": w.get("hour"), "starts": int(w.get("n") or 0),
                          "gave_up": int(w.get("n") or 0) > MAX_STARTS,
                          "stopped_by_hand": bool(w.get("hand_stop"))},
                "job": dict(job) if job else None}

    # ------------------------------------------------------------------ hand verbs
    def act(self, action: str, who: str) -> dict:
        if action not in ACTIONS:
            raise ChatError(404, "not_found", f"no action {action}")
        if not self.own:
            raise ChatError(409, "not_owned", "chatd is not this node's: the engine's launchers "
                                              "run it (tandem-node serve --chat on, or chat.json "
                                              "own, with QSE_CHATD=node for the engine)")
        with self.lock:
            if self.job is not None and self.job["state"] == "running":
                raise ChatError(409, "busy", f"chatd: a {self.job['action']} runs")
            self.job = {"id": f"chat-{int(self.clock() * 1000)}", "action": action,
                        "state": "running", "who": who, "started_at": self.clock(),
                        "ended_at": None, "rc": None, "message": None}
            job = self.job
        self.log(f"[node] chat {action} by {who}")
        threading.Thread(target=self._job, args=(job,), name=f"chat-{action}",
                         daemon=True).start()
        return dict(job)

    def _job(self, job: dict) -> None:
        try:
            rc, msg = self.run_action(job["action"])
            job.update(state="done" if rc == 0 else "failed", rc=rc, message=msg)
        except ChatError as exc:
            job.update(state="failed", message=exc.message)
        except Exception as exc:                                     # noqa: BLE001
            job.update(state="failed", message=f"{type(exc).__name__}: {exc}")
        job["ended_at"] = self.clock()
        self.log(f"[node] chat {job['action']}: {job['state']}"
                 f"{' · ' + job['message'] if job['message'] else ''}")

    def run_action(self, action: str) -> tuple[int, str]:
        """A hand verb, now: the launcher's code and its last line. A hand stop holds the watcher
        until a hand start or restart."""
        with self._run:
            rc, lines = self._call(action)
        for ln in lines:
            self.log(f"[node] chat: {ln}")
        if rc == 0:
            self._update(hand_stop=True if action == "stop" else None)
        return rc, (lines[-1] if lines else f"exit {rc}")

    # ------------------------------------------------------------------ the watcher
    def _paused(self) -> bool:
        if not self.repo:
            return False
        p = os.path.join(self.repo, ".watchdog.off")
        try:
            age = self.clock() - os.stat(p).st_mtime
        except OSError:
            return False
        return age < float(self.env.get("WATCHDOG_PAUSE_MAX") or PAUSE_MAX_S)

    def _setup(self, mode) -> bool:
        return mode == "setup" or bool(self.repo) and os.path.isfile(
            os.path.join(self.repo, "run", "setup-pending"))

    def kick(self) -> None:
        """An engine start or restart this agent ran is done: the watcher runs now."""
        if self.own:
            self._kick.set()

    def tick(self, kicked: bool = False) -> str:
        """One look, as ops/watchdog.sh's chatd_check; returns what it found (for the tests)."""
        if not self.own:
            return "not_owned"
        if self.job is not None and self.job["state"] == "running":
            return "busy"
        if not self._run.acquire(blocking=False):
            return "busy"
        try:
            return self._tick(kicked)
        except ChatError as exc:                     # no launcher: said once an hour
            hour = self.hour()
            if self._watch().get("why_hour") != hour:
                self._update(why_hour=hour)
                self.log(f"[node] chat: not started here: {exc.message} (said once an hour)")
            return exc.code
        finally:
            self._run.release()

    def _tick(self, kicked: bool) -> str:
        h = self.engine_health()
        up = h is not None and h.get("http") == 200
        mode = h.get("mode") if up else None
        before, self._seen = self._seen, (up, mode)
        if not up:
            return "engine_down"
        if self._paused():
            return "paused"
        if self._setup(mode):
            return "setup"
        if mode == "engine":
            # the switch to engine (ops/start.sh, OPS-260): a chatd running then stops, once
            if before is not None and before != (True, "engine"):
                rc, lines = self._call("status", STATUS_TIMEOUT_S)
                if rc == 0:
                    rc, lines = self._call("stop")
                    self.log("[node] chat: the engine runs engine only (QSE_MODE=engine): chatd "
                             + ("stopped" if rc == 0 else f"did not stop ({lines[-1] if lines else rc})"))
                    return "stopped_engine_only"
            return "engine_only"
        w = self._watch()
        if w.get("hand_stop"):
            return "stopped_by_hand"
        ok, why, _ = self.hands_over(h)
        if not ok:
            hour = self.hour()
            if w.get("why_hour") != hour:
                self._update(why_hour=hour)
                self.log(f"[node] chat: not started here: {why} (said once an hour)")
            return "two_owners"
        rc, _ = self._call("status", STATUS_TIMEOUT_S)
        if rc == 0:
            if w.get("hour") or w.get("n"):
                self._update(hour=None, n=None)
            return "running"
        if kicked:
            rc, lines = self._call("start")
            self.log("[node] chat: the engine answers; chatd " + (
                "started" if rc == 0 else f"did not start ({lines[-1] if lines else rc}); log: "
                                          f"{self.log_path}"))
            return "started" if rc == 0 else "failed"
        hour = self.hour()
        n = int(w.get("n") or 0) if w.get("hour") == hour else 0
        if n >= MAX_STARTS:
            if n == MAX_STARTS:
                self.log("[node] chatd: 3 starts this hour did not keep it running; giving up "
                         "until the next hour")
                self._update(hour=hour, n=MAX_STARTS + 1)
            return "gave_up"
        self._update(hour=hour, n=n + 1)
        rc, lines = self._call("start")
        if rc == 0:
            self.log(f"[node] chatd was not running; started it ({n + 1}/3 this hour)")
            return "started"
        self.log(f"[node] chatd was not running and did not start ({n + 1}/3 this hour); log: "
                 f"{self.log_path}")
        return "failed"

    def start_watcher(self) -> None:
        if not self.own or self._thread is not None:
            return

        def loop():
            kicked = False
            while not self._stop.is_set():
                try:
                    self.tick(kicked=kicked)
                except Exception as exc:                             # noqa: BLE001
                    self.log(f"[node] chat: the watcher failed once: {type(exc).__name__}: {exc}")
                kicked = self._kick.wait(self.every_s)
                self._kick.clear()
        self._thread = threading.Thread(target=loop, name="chat-watch", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._kick.set()
