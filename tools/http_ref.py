"""The HTTP reference of the docs (docs/reference/http.md): every route, header and error, checked
against the code.

The routes, headers and errors are written here by hand, because a handler's purpose and the
shape of its body are not in the code in a form a script can read. What keeps them true is the
check: `scan()` reads the code and `problems()` names

  - a path the request handlers test that no route row covers (a route was added),
  - a route row whose path the handlers never test (a route was removed or renamed),
  - a header name the code reads or writes that no header row names, and a row the code never uses,
  - an error type or code the code puts in an error body that no error row names, and a row
    whose types the code never uses.

`gen_reference.py` writes the tables and fails (`--check` too) while any problem is open, so a
change to the API cannot ship without its reference.

The engine's own API only (ENG-413): the code it reads is server/. TandemApp's routes, headers and
errors (the controller, chatd's forward, the routes its extension serves inside the engine) are in
TandemApp's docs, checked against TandemApp's code by its copy of this file.
"""

from __future__ import annotations

import ast
import os
import re

# --------------------------------------------------------------------------- who may call a route
# The labels of the `who` column; docs/reference/http.md explains them.
WHO = {
    "open": "anyone who reaches the port",
    "admin": "the admin rule: anyone while no admin password is set; with one, the admin token, a dashboard session, or a tool on the machine itself",
    "admin-bearer": "as admin, but the token only as a bearer header, never the session cookie",
    "dash": "the dashboard rule: anyone unless a password is set and the dashboard login is on",
    "metrics": "the metrics token when one is set, else as admin",
    "session": "only under a session address, /s/<token>/...",
    "key": "anyone until the first user key exists; then a user key, except from the machine itself",
    "collect": "the collect token, from every address",
}

# --------------------------------------------------------------------------- the engine's routes
# (method, path, who, what, body, answer). `path` placeholders are {name}; a query is ?a=&b=.
ENGINE_ROUTES = [
    ("POST", "/v1/chat/completions", "open",
     "A chat completion, streamed as server-sent events or not.",
     "OpenAI's chat body: model, messages, tools, stream, max_tokens and the other fields of Section sec:api-fields",
     "chat.completion, or chat.completion.chunk events and [DONE]; usage carries prompt_tokens_details.hidden_tokens for the conductor's notes"),
    ("POST", "/v1/completions", "open",
     "A plain text completion.",
     "prompt, max_tokens and the sampling fields",
     "text_completion, or its chunks and [DONE]"),
    ("GET", "/v1/models", "open",
     "The model list: TandemLLM alone with the conductor on, else the served name.",
     "", "{object: list, data: [...]}, and decision_models beside data when decision models are served"),
    ("GET", "/v1/models/{id}", "open",
     "One model card: the engine's or a decision model's.",
     "", "the card, or 404 model_not_found"),
    ("GET", "/v1/replica/identity", "open",
     "What two engines compare before they serve one model as replicas: the code, the family, the weights, drafters, rows and tokenizer by content, and the decode settings (SRV-741). Follows the caller rule of /v1/models.",
     "", "{identity: 1, served_model, base_model, version, code, family, layers, profile, weights, drafters, rows, tokenizer, decode, max_len, stage}; 503 identity_pending until the first call's computation ends"),
    ("GET", "/health", "open",
     "Whether the server takes work. The full body follows the admin rule; anyone else gets the status alone. Also /healthz and /v1/health.",
     "", "{status: ok|draining}; the full body adds model, uptime, inflight, the queue, limits, sampling, cache, memory and parallel; 503 while draining"),
    ("GET", "/metrics", "metrics",
     "The Prometheus families of Chapter ch:ref-metrics.", "", "Prometheus text format"),
    ("GET", "/metrics/up", "open",
     "One gauge, qse_up, so a scraper can tell an engine that is down from a wrong token.", "", "qse_up 1, or 0 while draining"),
    ("GET", "/v1/cache/stats", "admin",
     "Every cache's counters.", "", "the cache report /health carries"),
    ("GET", "/v1/cache/memory?pools=1", "admin",
     "The engine's memory by owner against what it held once loaded: each slot's anchors, end anchor, stash, pin and park, the store, the KV pool's pages and what no cache holds (SPD-257). pools=1 adds the allocator's segments by pool; it walks every block.",
     "", "{allocated, reserved, load, caches, by_owner, unattributed, limits, kv_pool, allocator_pools?}"),
    ("POST", "/v1/cache/peek", "admin",
     "Where a chat's rows are before it is sent: the body is rendered as a request would be, the session's router notes are replayed, and the cache tiers are asked without running the model (SRV-349).",
     "a chat body (messages, tools, the effort fields) and session", "{tier: resident|ram_park|ram_snapshot|nvme_spill|disk_tier|none, cached_tokens, reread_tokens, reread_eta_ms, tokens, restore_ms, session}; 409 while a request holds the engine"),
    ("POST", "/v1/cache/prewarm", "admin",
     "Reads a conversation the peek finds on the NVMe back into the engine while nothing runs or waits, so its next request resumes at the end anchor.",
     "as /v1/cache/peek", "{prewarmed, why?, restore?} and the peek's fields; 409 while busy"),
    ("POST", "/v1/tokenize", "open",
     "Exact token counts with the model's own chat template, split by part; nothing runs on the GPU (SRV-347). With ids: true also the ids, the chat prompt's or the text's as /v1/completions reads it (SPD-249). 240 a minute from one address.",
     "{messages, tools?, ids?} or {text, ids?}", "{tokens, parts: {system, tools, messages}, ids?}"),
    ("POST", "/v1/cache/clear?tier=ram|disk|all", "admin-bearer",
     "Empties the caches in memory (ram, the default), the disk tier and the session spill (disk), or both (all). Nothing else is reset.",
     "", "the cache report after the clear; 400 for another tier"),
    ("POST", "/v1/cache/release", "admin-bearer",
     "Gives back memory the engine holds idle, cheapest first: free allocator blocks and caches while slot 0 is idle, the small models, idle side conversations, slot 0's idle conversation (to the NVMe first). chatd asks it before a sandbox waits (SRV-483).",
     "{bytes}", "what each step gave back and the total"),
    ("GET", "/dashboard/", "open",
     "No page: every engine answers 404 no_ui under /dashboard (engine API 1.8); TandemApp's controller serves the page.", "", "404 no_ui"),
    ("GET", "/v1/dashboard/summary?from=&to=&tz=&model=&client=&backend=&user=&internal=", "dash",
     "The Usage view's totals for a range.", "", "summary.schema.json in contracts/engine-api-v1/dashboard"),
    ("GET", "/v1/dashboard/usage?bucket=hour|day&from=&to=&tz=&model=&client=&backend=&user=&internal=", "dash",
     "Tokens, requests and cost per hour or day.", "", "usage.schema.json"),
    ("GET", "/v1/dashboard/requests?limit=&before=&finish=&model=&client=&backend=&user=&internal=", "dash",
     "Recent request rows of the usage ledger, newest first.", "", "requests.schema.json"),
    ("GET", "/v1/dashboard/system", "dash",
     "The machine, the engine, the memory plan and the slots; the proxy's mode is always off.", "", "system.schema.json"),
    ("GET", "/v1/dashboard/live", "dash",
     "What each request is doing now, as server-sent events.", "", "live.schema.json events"),
    ("GET", "/v1/dashboard/logs?level=&grep=&backlog=&since=&follow=", "dash",
     "The log: a backlog of up to 5,000 lines, then live lines as server-sent events; Last-Event-ID resumes; follow=0 answers JSON once.",
     "", "logs-line.schema.json events, or logs-json.schema.json; 429 past four streams at once"),
    ("GET", "/v1/dashboard/logs/export", "dash",
     "The whole log ring as one text file: a header with the version, the commit, the uptime, the time of export and the line count, then every line the ring holds, at all levels, oldest first. It takes no stream place.",
     "", "text/plain as an attachment named tandemllm-<version>-<time>.log"),
    ("GET", "/v1/dashboard/metrics", "dash",
     "The /metrics page under the dashboard's rule, for the Performance view.", "", "Prometheus text format"),
    ("GET", "/v1/dashboard/parallel?since=", "dash",
     "Every request in flight on the engine.", "", "parallel.schema.json"),
    ("GET", "/v1/dashboard/session", "open",
     "Whether a sign-in exists and is needed.", "", "{authenticated, login, password_set, expires_at}"),
    ("POST", "/v1/dashboard/session", "open",
     "The sign-in, with a password set and the login on: sets the qse_dash cookie. Five failures a minute from one address and the sixth gets a 429.",
     "{token}", "the session; 401 for a wrong token"),
    ("DELETE", "/v1/dashboard/session", "open",
     "Signs this session out.", "", "the session, signed out"),
    ("*", "/v1/conductor/*", "admin",
     "The conductor's documents, journal, sessions and memory, served by the extension in chat mode (TandemApp's docs lists them). Reads follow the dashboard rule, writes and every memory route the admin rule. 404 not_on_engine on a headless engine.",
     "", ""),
    ("*", "/v1/setup/*", "admin",
     "The Setup Guide's API (docs/setup.md): the model registry, the box, the memory estimate, the review plan, the apply and reset jobs and Wren. Every POST must also be same-origin JSON: 415 for another content type, 403 for another origin. In setup mode (--setup) these, the session and /health are the only routes that answer.",
     "", ""),
    ("*", "/v1/config/*", "open",
     "404 not_on_engine on every engine (engine API 1.8): other backends sit behind TandemApp's controller.",
     "", "404 not_on_engine"),
    ("POST", "/v1/collect/{backend}", "open",
     "404 not_on_engine on every engine (engine API 1.8): the host collector is TandemApp's controller's.",
     "", "404 not_on_engine"),
    ("*", "/v1/chat-app", "open",
     "404 not_on_engine on every engine (engine API 1.8), and every path under it: TandemApp's controller serves the Chat tab's service. The body is not read.",
     "", "404 not_on_engine"),
    ("POST", "/v1/systemone", "admin",
     "Typed decisions from the decision models, in the engine's idle window. /v1/predict is the same route. Only with QWEN38_DECIDER_MODELS.",
     "{state, model?, questions: {id: {type, instructions, criteria?}}, task?, lang?}",
     "{model, answers, usage, routing, latency_ms, path}"),
    ("POST", "/v1/predict", "admin", "The same as /v1/systemone.", "as /v1/systemone", "as /v1/systemone"),
    ("GET", "/readyz", "open",
     "Whether a decision model is loaded and warm.", "", "200, or 503 with the reason"),
    ("GET", "/v1/laya", "open",
     "What the Laya route takes and answers, its limits, and whether it is on. The Laya, library and session memory routes below are the extension's in chat mode (TandemApp's docs has their shapes); a headless engine answers them 404 not_on_engine.", "", "a description of the route"),
    ("POST", "/v1/laya/ask", "open",
     "A typed answer from Laya on one text or many, for the model's own scripts; rate-limited.",
     "{question, choices | scale | neither, text | texts, threshold?, model?, deadline_ms?}",
     "{label, p, sure, probs, model, ms, ...}, or {answers: [...]} for texts"),
    ("POST", "/v1/laya/drive/end", "open",
     "Ends a playtest driver session and writes its journal row.", "the session's summary", "the row"),
    ("GET", "/v1/laya/kit/{file}", "open",
     "A file of the playtest kit the model copies into a project.", "", "the file"),
    ("OPTIONS", "/v1/laya/*", "open",
     "A browser's CORS preflight for the Laya routes, which the engine asks the extension for. Every other path answers OPTIONS with 501.", "", "204 with the extension's CORS headers"),
    ("GET", "/v1/library", "open",
     "The package library: every enabled skill's name, description and classes.", "", "the list and the two routes below"),
    ("GET", "/v1/library/search?q=&k=&class=", "open",
     "BM25 search over the skills, best first (k from 1 to 20, default 5).", "", "[{name, description, score}]"),
    ("GET", "/v1/library/{name}", "open",
     "One skill's text and its reference.", "", "{name, description, classes, body, reference}"),
    ("GET", "/s/{token}/v1/memory", "session",
     "The memory index this session sees. The /s/{token}/ prefix also serves the library and Laya routes, filed to the session.",
     "", "the notes' index lines"),
    ("GET", "/s/{token}/v1/memory/search?q=&k=", "session",
     "Memory notes that match, best first (k from 1 to 10).", "", "[{id, kind, scope, status, title, score}]"),
    ("GET", "/s/{token}/v1/memory/{id}", "session",
     "One note.", "", "{id, title, kind, scope, status, body, ...}"),
    ("POST", "/s/{token}/v1/memory", "session",
     "Writes, changes or archives a note, with the fields of the hidden memory block.",
     "{op, scope, kind, title, tags, body, id, reason}", "{op, id, title, ...}, or 422 with the reason as the type"),
]

# The status responder beside the engine (TandemApp's host collector is in its docs).
SIDE_ROUTES = [
    ("ops/engine_status.py", "GET", "/v1/models", "200 with the engine's last model list and its state, while the engine is away"),
    ("ops/engine_status.py", "GET", "/dashboard/", "503 with a page that says why the engine is away and opens the dashboard when it is back"),
    ("ops/engine_status.py", "*", "any other path", "503 provider_unavailable with Retry-After and the state as engine_<state>"),
]

# --------------------------------------------------------------------------- headers
# (name, direction, where, meaning). direction: in (the server reads it), out (it writes it),
# both. where: engine, or both (TandemApp's controller reads or writes it too).
HEADERS = [
    ("X-Tandem", "both", "both",
     "raw, shadow or on: lowers the conductor's mode for one request; the response says which mode ran"),
    ("X-Tandem-Profile", "both", "both", "picks a conductor profile; the response names the profile that ran; an unknown name is a 400"),
    ("X-Tandem-Memory", "in", "engine", "off: no memory note is read or written; read: reads only, a block the model writes is hidden and dropped"),
    ("X-Tandem-Probe", "in", "engine", "off takes the request out of the probe snapshots"),
    ("X-Tandem-User", "in", "engine", "a proxy's user name for the memory notes, honoured from loopback or QWEN38_MEMORY_PROXIES"),
    ("X-Tandem-Relay-Id", "in", "engine", "a proxy's id for a relayed request, for the live counts; a relayed request without X-Tandem-User touches no memory"),
    ("X-Tandem-Parent", "in", "engine", "the request a call of TandemLLM to itself serves; the row becomes client kind internal"),
    ("X-Tandem-Worker", "in", "engine", "1 on an agent's worker request: its session comes back, so it is never a guest, and its rows go to the NVMe rather than away while it waits"),
    ("X-Tandem-Parent-Session", "in", "engine", "a worker's parent session (the chat's): for 600 s after the worker's last request the parent's rows go to the NVMe rather than away, and its return tries slot 0 first"),
    ("X-Tandem-Regenerate", "in", "engine", "1 serves the request as a regenerate (sampled, a fresh seed); 0 turns the detection off for it"),
    ("X-Tandem-Images-Left-Out", "out", "engine", "how many earlier images were left out of the prompt"),
    ("X-Tandem-Wait", "out", "engine", "memory, on a response that waited for memory before its prefill; its stream says so every 15 s"),
    ("X-Tandem-Wait-For-Memory", "in", "engine", "1: a request not streamed waits for memory up to QSE_MEM_WAIT_S, as wait_for_memory: true in the body does, instead of a 503 after the queue timeout"),
    ("X-Engine-Stop", "out", "engine", "pattern-stop(<label>) on a response without a stream that a stop pattern ended"),
    ("x-session-id", "in", "both", "opencode's session id: the router's session and the caches' session key"),
    ("X-Conversation-Id", "in", "both", "a conversation id for clients that send no session field; read after the body's own ids"),
    ("X-OpenWebUI-Chat-Id", "in", "both", "Open WebUI's chat id (a connection header with {{CHAT_ID}}): the router's session for that chat"),
    ("X-QSE-Client", "in", "engine", "dashboard marks the dashboard's own requests (client kind dashboard)"),
    ("X-Real-IP", "in", "both", "set by a reverse proxy; a request that carries it is never trusted as local; the sign-in's rate limit counts per address"),
    ("X-Forwarded-For", "in", "both", "as X-Real-IP: a request that carries it is never trusted as local"),
    ("X-Forwarded-Proto", "in", "both", "https makes the session cookie Secure, and the notes' {tandem_url} https"),
    ("X-Forwarded-Host", "in", "engine", "the host the notes' {tandem_url} names, unless QWEN38_TANDEM_URL sets it"),
    ("X-Decider-Deadline-Ms", "in", "engine", "how long a decision request waits for the idle window (default 30,000, at most 120,000)"),
    ("X-Decider-Window", "in", "engine", "in_decode is refused: decisions run in the idle window only"),
    ("X-Accel-Buffering", "out", "both", "no, on every stream, so nginx sends each event at once"),
    ("X-Content-Type-Options", "out", "both", "nosniff on the dashboard's static files"),
    ("Authorization", "in", "both", "Bearer: the admin token, the metrics token, the collect token or a proxy user key, by route; the engine's OpenAI routes ignore it and file the request under a hash of it"),
    ("User-Agent", "in", "both", "the client kind in the ledger: open-webui, opencode, openai-sdk, curl, other (a Hermes-shaped body: hermes)"),
    ("Last-Event-ID", "in", "both", "resumes the log stream after that line"),
    ("Retry-After", "out", "both", "with a 503 (queue full, memory gate, draining, a full backend), a 429, or a decision model that is loading"),
    ("WWW-Authenticate", "out", "both", "Bearer, with a 401"),
    ("Set-Cookie", "out", "both", "qse_dash, the dashboard session: HttpOnly, SameSite=Strict, Secure behind https"),
    ("X-Tandem-Pair-Key", "in", "engine", "the pairing window key (run/remote/pair-local.key): tandem pair opens and closes a window with it, from the machine itself"),
]

# --------------------------------------------------------------------------- errors
# (status, types, when, retry_after). `types` are the error.type (and error.code) values.
ERRORS = [
    ("400", ["invalid_request_error"],
     "a request the engine will not serve as asked: error.param names the field (messages, a role, max_tokens, tools, an image part, a prompt too long, a body that is not JSON or not an object)", False),
    ("400", ["bad_request"], "a dashboard or conductor query that does not parse (a level, a range, a number)", False),
    ("400", ["stage_text_only"], "a request with images on the tail stage of a split model (--stage), as error.code beside invalid_request_error", False),
    ("400", ["confirm_required"], "a setup reset without the confirmation its level asks for (true, or the typed word)", False),
    ("401", ["unauthorized"], "a guarded route without the token or the session it needs, once an admin password is set; a wrong collect token", False),
    ("403", ["local_only", "window_key"], "opening or closing a pairing window (POST /v1/engine/pair/open, /close) from another machine, or without the window key", False),
    ("404", ["not_found"], "no such route, or a route whose part is off on this server (the conductor, the proxy, the decision models)", False),
    ("404", ["not_on_engine"], "a route TandemApp serves (the conductor, its library, memory and Laya, the backends) on an engine where no extension serves it", False),
    ("404", ["no_pairing"], "a pairing route on a process that serves no pairing", False),
    ("404", ["no_ui"], "the page, on every engine: TandemApp's controller serves it", False),
    ("404", ["model_not_found"], "a model id no backend serves, or one two backends serve (the message names the prefixed forms) (code model_not_found)", False),
    ("409", ["busy", "not_loaded", "role_off"],
     "an action that needs a role or a decision model in another state, or a role the roles document switches off (role_off)", False),
    ("409", ["stage_front"], "any route but /health on the front stage of a split model (--stage): requests go to the tail stage", False),
    ("422", ["invalid_request_error"],
     "a value the schema refuses: a config field (error.field), a field an environment variable locks (error.variable), a decision request's fields", False),
    ("429", ["server_busy"], "a request without a stream that waited --queue-timeout for the engine", True),
    ("429", ["too_many"], "a fifth log or live stream at once; a sixth failed sign-in in a minute", True),
    ("429", ["rate_limited"], "/v1/tokenize past its 240 calls a minute from one address; a pairing route past 20 a minute from one address", True),
    ("500", ["internal_error"], "a bug: the message is the exception's; the request is in the log", False),
    ("500", ["internal"], "a PATCH /v1/engine/settings that raised: the message is the exception's; the call is journaled", False),
    ("500", ["extension_error"], "a route of the extension that raised: the extension's bug, in the engine's log", False),
    ("501", [], "OPTIONS on any path but the Laya routes", False),
    ("503", ["server_busy"], "the queue is full, the server is draining, or the memory gate or the admission by bytes cannot make room", True),
    ("503", ["memory_wait_timeout"], "a request that waited for memory to its bound: --queue-timeout without a stream, QSE_MEM_WAIT_S streamed or with wait_for_memory; a stream that opened while it waited ends with it as an error event (type server_busy, Retry-After: 30)", True),
    ("503", ["setup_mode"], "any route but the dashboard, /health, /v1/models and /v1/setup/* on a box in setup mode (Retry-After: 60)", True),
    ("503", ["warming", "loading", "refused", "failed", "not_loaded", "busy_decoding"], "a decision request while the model loads, was refused memory, failed, or the engine kept decoding past the deadline", True),
    ("503", ["identity_pending"], "the replica identity while the engine computes it, once after the start (Retry-After: 1)", True),
    ("503", ["stage_down"], "a completion on the tail stage of a split model while its link to the front stage is down (Retry-After: 5); a request in flight when the link fails ends with 500 and the same word in its message", True),
    ("503", ["engine_failed"], "a completion after the engine's CUDA context was lost: the sentence says so, the watchdog restarts the engine (Retry-After: 60)", True),
    ("503", ["provider_unavailable"], "the status responder while the engine is stopped, starting or held (code engine_<state>)", True),
    ("529", ["overloaded_error"], "a decision request the runtime could not take", False),
]

# --------------------------------------------------------------------------- the scan
HANDLERS = {
    "server/app.py": ("do_GET", "do_POST", "do_PUT", "do_PATCH", "do_DELETE", "do_OPTIONS",
                      "_dashboard_get", "_conductor_write", "_strip_session", "_chat"),
}
# the engine's server/ only; TandemApp's code is checked by TandemApp's copy of this file (ENG-413)
HEADER_DIRS = ("server",)
_HEADER = re.compile(r"^(?:[Xx]-[A-Za-z0-9-]+|Retry-After|WWW-Authenticate|Set-Cookie|Last-Event-ID"
                     r"|OpenAI-[A-Za-z-]+|Access-Control-[A-Za-z-]+)$")
# code that is not part of the served API: test doubles, the kit the model copies into a project, and
# bin/tandem setup's terminal client (server/setup/cli.py), whose error codes it prints itself
_SKIP = ("fake", "laya_kit")
_SKIP_FILES = ("server/setup/cli.py",)


def _parse(root: str, rel: str):
    try:
        with open(os.path.join(root, rel), encoding="utf-8") as fh:
            return ast.parse(fh.read())
    except (OSError, SyntaxError):
        return None


def handler_paths(root: str) -> dict:
    """{rel: set of the path literals the request handlers test}."""
    out = {}
    for rel, names in HANDLERS.items():
        tree = _parse(root, rel)
        found = set()
        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name in names:
                    # what tests a path: a comparison (== or in), a startswith(), a dispatch dict's key
                    tested = []
                    for c in ast.walk(node):
                        if isinstance(c, ast.Compare):
                            tested += [c.left, *c.comparators]
                        elif isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "startswith":
                            tested += c.args
                        elif isinstance(c, ast.Dict):
                            tested += [k for k in c.keys if k is not None]
                    for t in tested:
                        for c in ast.walk(t):
                            if (isinstance(c, ast.Constant) and isinstance(c.value, str)
                                    and c.value.startswith("/") and " " not in c.value):
                                found.add(c.value.rstrip("/") or "/")
        out[rel] = found
    return out


def _py_files(root: str, dirs: tuple):
    for d in dirs:
        for dp, _, fs in os.walk(os.path.join(root, d)):
            if any(s in os.path.relpath(dp, root) for s in _SKIP):
                continue
            for f in sorted(fs):
                rel = os.path.relpath(os.path.join(dp, f), root)
                if f.endswith(".py") and not f.startswith(_SKIP) and rel not in _SKIP_FILES:
                    yield rel


def header_names(root: str) -> set:
    """Lower-cased header names the server reads or writes."""
    out = set()
    for rel in _py_files(root, HEADER_DIRS):
        tree = _parse(root, rel)
        if tree is None:
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and _HEADER.match(n.value):
                out.add(n.value.lower())
    return out


def _text(node) -> str:
    """A message argument as text: a string, or an f-string with {...} for its values."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{...}" for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _text(node.left) + _text(node.right)
    return ""


_TYPE = re.compile(r"[a-z][a-z_]+")
# the calls that raise or answer an error: an ...Error class, a Refused or Refusal, a refuse helper,
# _err and _json (a 200 call with a status-like number, such as a token budget, is no error)
_ERRCALL = re.compile(r"(?:Error|Refused|Refusal|_refuse|^_err|^_json)$")


def error_sites(root: str) -> list:
    """(status or None, type, message, rel) for every error.type and error.code the code puts in
    an error body or raises with a status."""
    out = []
    for rel in _py_files(root, HEADER_DIRS):
        tree = _parse(root, rel)
        if tree is None:
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Dict):
                for k, v in zip(n.keys, n.values):
                    if isinstance(k, ast.Constant) and k.value == "error" and isinstance(v, ast.Dict):
                        msg = next((_text(v2) for k2, v2 in zip(v.keys, v.values)
                                    if isinstance(k2, ast.Constant) and k2.value == "message"), "")
                        for k2, v2 in zip(v.keys, v.values):
                            if (isinstance(k2, ast.Constant) and k2.value in ("type", "code")
                                    and isinstance(v2, ast.Constant) and isinstance(v2.value, str)
                                    and _TYPE.fullmatch(v2.value)):
                                out.append((None, v2.value, msg, rel))
            elif isinstance(n, ast.Call):
                fn = n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
                args = n.args
                if fn == "_error_body":
                    msg = _text(args[0]) if args else ""
                    out += [(None, a.value, msg, rel) for a in args[1:3]
                            if isinstance(a, ast.Constant) and isinstance(a.value, str) and _TYPE.fullmatch(a.value)]
                elif (fn == "Refused" and len(args) >= 2 and isinstance(args[0], ast.Constant)
                      and isinstance(args[0].value, str) and _TYPE.fullmatch(args[0].value)):
                    out.append((422, args[0].value, _text(args[1]), rel))   # a memory note's or a proposal's reason
                elif fn == "_busy":
                    out.append((503, "server_busy", "", rel))
                elif _ERRCALL.search(fn):
                    # any error a call raises or answers as (..., status, "type", message?): Refused,
                    # ApiError, AddError, UpdateError, PluginError, _err(handler, 403, "type", ...)
                    for x, (a, b) in enumerate(zip(args, args[1:])):
                        if (isinstance(a, ast.Constant) and isinstance(a.value, int) and not isinstance(a.value, bool)
                                and 400 <= a.value <= 599 and isinstance(b, ast.Constant)
                                and isinstance(b.value, str) and _TYPE.fullmatch(b.value)):
                            msg = _text(args[x + 2]) if len(args) > x + 2 else ""
                            out.append((a.value, b.value, msg, rel))
    return out


def error_types(root: str) -> set:
    """The error.type and error.code values the code puts in an error body."""
    return {t for _, t, _, _ in error_sites(root)}


def _static(path: str) -> str:
    """A documented path up to its first placeholder or query: what the code must test."""
    p = re.split(r"[{<*?]", path, maxsplit=1)[0]
    return p.rstrip("/") or "/"


def _unsession(path: str) -> str:
    """/s/{token}/v1/memory... is the handler's /v1/memory... after the session prefix is cut."""
    return re.sub(r"^/s/\{token\}", "", path)


def _covers(doc_paths: list, lit: str) -> bool:
    for d in map(_unsession, doc_paths):
        base, st = d.split("?")[0], _static(d)
        if base == lit or base.startswith(lit + "/") or st == lit:
            return True
        if re.search(r"[{*]", base) and lit.startswith(st + "/"):
            return True                   # a placeholder or a wildcard row covers what is under it
    return False


ALIASES = {"/healthz": "/health", "/v1/health": "/health", "/dashboard": "/dashboard/"}


def problems(root: str) -> list:
    out = []
    lits = handler_paths(root)
    for rel, rows in (("server/app.py", ENGINE_ROUTES),):
        docs = [r[1] for r in rows]
        found = lits.get(rel, set())
        for lit in sorted(found):
            if lit.startswith("/s/"):
                continue
            target = ALIASES.get(lit, lit)
            if not _covers(docs, target) and not _covers(docs, lit):
                out.append(f"undocumented route in {rel}: {lit}")
        for d in docs:
            s = _static(_unsession(d))
            if s == "/":
                ok = "/" in found
            else:
                ok = any(l == s or l.startswith(s + "/") or s.startswith(l + "/") or s.startswith(l)
                         for l in found if l != "/")
            if not ok:
                out.append(f"documented route not in {rel}: {d}")
    code_h = header_names(root)
    doc_h = {h[0].lower() for h in HEADERS}
    for h in sorted(code_h):
        if h not in doc_h and not any(d.endswith("*") and h.startswith(d[:-1]) for d in doc_h):
            out.append(f"undocumented header: {h}")
    for d in sorted(doc_h):
        if d in ("authorization", "user-agent"):
            continue                      # read through helpers, not as bare literals everywhere
        if d.endswith("*"):
            if not any(h.startswith(d[:-1]) for h in code_h):
                out.append(f"documented header not in the code: {d}")
        elif d not in code_h:
            out.append(f"documented header not in the code: {d}")
    code_e = error_types(root)
    doc_e = {t for row in ERRORS for t in row[1]}
    # every type the code has and no row explains is listed by gen_errors' second table
    for t in sorted(doc_e - code_e):
        if t in ("warming", "loading", "refused", "failed", "not_loaded", "busy_decoding",
                 "provider_unavailable", "overloaded_error"):
            continue                      # built from a variable (the runtime's state, an f-string)
        out.append(f"documented error type not in the code: {t}")
    return out


# --------------------------------------------------------------------------- rendering
# "Section x" and "Chapter x" in a row's text become links to the page that has it
LINKS = {
    "sec:api-fields": ("the API fields", "../server.md"),
    "ch:ref-metrics": ("the metrics reference", "metrics.md"),
}
_REF = re.compile(r"\b(?:Section|Chapter|Table) ((?:sec:|ch:|tab:)[a-z][a-z0-9:-]*[a-z0-9])")
_CODEISH = re.compile(r"(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}|\[DONE\]|(?<![\w])--[a-z][a-z-]*|"
                      r"(?<![\w/])/[\w{}.<>/*?=&|-]*|\b[A-Za-z][\w.-]*\.(?:schema\.)?json\b|"
                      r"\b[a-z]+(?:_[a-z0-9]+)+(?:\.[a-z_]+)*\b|\b[A-Z]+(?:_[A-Z0-9]+)+\b|\bqse_\w+)")


def _ref(m: re.Match) -> str:
    text, href = LINKS[m.group(1)]
    return "\x00[" + text + "](" + href + ")\x00"


def fmt(text: str, code, esc) -> str:
    """Prose with identifiers, paths, flags and {json} set as code, and Section x as a link."""
    if not text:
        return ""
    parts = re.split("\x00", _REF.sub(_ref, text))
    out = []
    for i, part in enumerate(parts):
        if i % 2:
            out.append(part)              # a link, already Markdown
            continue
        for j, piece in enumerate(_CODEISH.split(part)):
            out.append(code(piece) if j % 2 else esc(piece))
    return "".join(out)


def _route_rows(rows, code, esc) -> list:
    out = []
    for method, path, who, what, body, answer in rows:
        cell = fmt(what, code, esc)
        if body:
            cell += " *Body:* " + fmt(body, code, esc) + "."
        if answer:
            cell += " *Answer:* " + fmt(answer, code, esc) + "."
        out.append([code(method) + " " + code(path), esc(who), cell])
    return out


def gen_who(table, code, esc) -> str:
    return table("Who may call a route: the labels of the routes' who column", ["label", "who may call"],
                 [[code(k), fmt(v, code, esc)] for k, v in WHO.items()])


def gen_engine_routes(table, code, esc) -> str:
    return (table("The engine server's routes", ["route", "who", "what"], _route_rows(ENGINE_ROUTES, code, esc))
            + "\n" + table("The status responder beside the engine", ["server", "route", "answer"],
                           [[code(server), code(method) + " " + code(path), fmt(what, code, esc)]
                            for server, method, path, what in SIDE_ROUTES]))


def gen_headers(table, code, esc) -> str:
    return table("Every header the engine reads or writes (both: TandemApp's controller too)",
                 ["header", "direction, where", "meaning"],
                 [[code(name), esc(direction + ", " + where), fmt(meaning, code, esc)]
                  for name, direction, where, meaning in HEADERS])


def gen_errors(table, code, esc, root: str) -> str:
    first = table("The errors explained", ["status", "error.type or code", "when"],
                  [[code(status) + (" with `Retry-After`" if retry else ""),
                    ", ".join(code(t) for t in types) if types else "none", fmt(when, code, esc)]
                   for status, types, when, retry in ERRORS])
    explained = {t for row in ERRORS for t in row[1]}
    by_type: dict = {}
    for status, t, msg, rel in error_sites(root):
        if t in explained:
            continue
        e = by_type.setdefault(t, {"status": set(), "msg": "", "files": set()})
        if status:
            e["status"].add(status)
        if msg and not e["msg"]:
            e["msg"] = msg
        e["files"].add(rel)
    rows = []
    for t in sorted(by_type):
        e = by_type[t]
        st = ", ".join(str(x) for x in sorted(e["status"])) or "in the body"
        files = sorted(e["files"])
        where = ", ".join(code(f) for f in files[:2]) + (f" and {len(files) - 2} more" if len(files) > 2 else "")
        rows.append([code(t), esc(st), esc(e["msg"]) or " ", where])
    second = table("Every other error type the code answers, with its status and the code's own message ({...} is a value)",
                   ["error.type or code", "status", "message", "where"], rows)
    return first + "\n" + second
