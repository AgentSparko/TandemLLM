# Reference: HTTP

This page lists every route, every header and every error of the engine's server. TandemApp's proxy speaks the same API for clients and has its own routes, headers and errors, and so does its extension inside the engine: [TandemApp's docs](https://github.com/TandemLLM/TandemApp/blob/main/docs/README.md) lists them. The tables are written in `tools/http_ref.py`, and `tools/gen_reference.py --check` compares them with the code in the tests: a route a handler tests, a header name or an error type the code uses that is missing here, or a row the code no longer has, fails the check.

## Who may call what

The admin password is the admin token, `QSE_ADMIN_TOKEN` in `secrets.env`. It is not set by default. Then every route below answers anyone who reaches the port, and only `/metrics` keeps the metrics token when one is set. With a password set, an admin route needs it as a bearer header or a dashboard session (a cookie the token buys). A request from the machine itself (loopback, no proxy headers) needs neither, because the watchdog and the measurement tools rely on that (`--trust-loopback`).

The OpenAI routes are open on the engine, with or without a password. The table below names the labels the route tables use.

<!-- generated: http-who by tools/gen_reference.py; do not edit by hand -->

### Who may call a route: the labels of the routes' who column

| label | who may call |
|-|-|
| `open` | anyone who reaches the port |
| `admin` | the admin rule: anyone while no admin password is set; with one, the admin token, a dashboard session, or a tool on the machine itself |
| `admin-bearer` | as admin, but the token only as a bearer header, never the session cookie |
| `dash` | the dashboard rule: anyone unless a password is set and the dashboard login is on |
| `metrics` | the metrics token when one is set, else as admin |
| `session` | only under a session address, `/s/<token>/...` |
| `key` | anyone until the first user key exists; then a user key, except from the machine itself |
| `collect` | the collect token, from every address |

<!-- /generated -->

## The engine's routes

the table below lists the engine server's routes. A route the server does not know answers 404. A route TandemApp serves answers 404 `not_on_engine` on a headless engine, and the page 404 `no_ui`; in chat mode the extension serves them. The proxy's routes without `--backends` answer 404 with a message that names the flag, and the decision routes without decision models as an unknown route. The table below adds the status responder, which answers while the engine is stopped.

<!-- generated: http-engine-routes by tools/gen_reference.py; do not edit by hand -->

### The engine server's routes

| route | who | what |
|-|-|-|
| `POST` `/v1/chat/completions` | open | A chat completion, streamed as server-sent events or not. *Body:* OpenAI's chat body: model, messages, tools, stream, `max_tokens` and the other fields of [the API fields](../server.md). *Answer:* chat.completion, or chat.completion.chunk events and `[DONE]`; usage carries `prompt_tokens_details.hidden_tokens` for the conductor's notes. |
| `POST` `/v1/completions` | open | A plain text completion. *Body:* prompt, `max_tokens` and the sampling fields. *Answer:* `text_completion`, or its chunks and `[DONE]`. |
| `GET` `/v1/models` | open | The model list: TandemLLM alone with the conductor on, else the served name. *Answer:* `{object: list, data: [...]}`, and `decision_models` beside data when decision models are served. |
| `GET` `/v1/models/{id}` | open | One model card: the engine's or a decision model's. *Answer:* the card, or 404 `model_not_found`. |
| `GET` `/v1/replica/identity` | open | What two engines compare before they serve one model as replicas: the code, the family, the weights, drafters, rows and tokenizer by content, and the decode settings (SRV-741). Follows the caller rule of `/v1/models.` *Answer:* `{identity: 1, served_model, base_model, version, code, family, layers, profile, weights, drafters, rows, tokenizer, decode, max_len, stage}`; 503 `identity_pending` until the first call's computation ends. |
| `GET` `/health` | open | Whether the server takes work. The full body follows the admin rule; anyone else gets the status alone. Also `/healthz` and `/v1/health.` *Answer:* `{status: ok\|draining}`; the full body adds model, uptime, inflight, the queue, limits, sampling, cache, memory and parallel; 503 while draining. |
| `GET` `/metrics` | metrics | The Prometheus families of [the metrics reference](metrics.md). *Answer:* Prometheus text format. |
| `GET` `/metrics/up` | open | One gauge, `qse_up`, so a scraper can tell an engine that is down from a wrong token. *Answer:* `qse_up` 1, or 0 while draining. |
| `GET` `/v1/cache/stats` | admin | Every cache's counters. *Answer:* the cache report `/health` carries. |
| `GET` `/v1/cache/memory?pools=1` | admin | The engine's memory by owner against what it held once loaded: each slot's anchors, end anchor, stash, pin and park, the store, the KV pool's pages and what no cache holds (SPD-257). pools=1 adds the allocator's segments by pool; it walks every block. *Answer:* `{allocated, reserved, load, caches, by_owner, unattributed, limits, kv_pool, allocator_pools?}`. |
| `POST` `/v1/cache/peek` | admin | Where a chat's rows are before it is sent: the body is rendered as a request would be, the session's router notes are replayed, and the cache tiers are asked without running the model (SRV-349). *Body:* a chat body (messages, tools, the effort fields) and session. *Answer:* `{tier: resident\|ram_park\|ram_snapshot\|nvme_spill\|disk_tier\|none, cached_tokens, reread_tokens, reread_eta_ms, tokens, restore_ms, session}`; 409 while a request holds the engine. |
| `POST` `/v1/cache/prewarm` | admin | Reads a conversation the peek finds on the NVMe back into the engine while nothing runs or waits, so its next request resumes at the end anchor. *Body:* as `/v1/cache/peek`. *Answer:* `{prewarmed, why?, restore?}` and the peek's fields; 409 while busy. |
| `POST` `/v1/tokenize` | open | Exact token counts with the model's own chat template, split by part; nothing runs on the GPU (SRV-347). With ids: true also the ids, the chat prompt's or the text's as `/v1/completions` reads it (SPD-249). 240 a minute from one address. *Body:* `{messages, tools?, ids?}` or `{text, ids?}`. *Answer:* `{tokens, parts: {system, tools, messages}, ids?}`. |
| `POST` `/v1/cache/clear?tier=ram\|disk\|all` | admin-bearer | Empties the caches in memory (ram, the default), the disk tier and the session spill (disk), or both (all). Nothing else is reset. *Answer:* the cache report after the clear; 400 for another tier. |
| `POST` `/v1/cache/release` | admin-bearer | Gives back memory the engine holds idle, cheapest first: free allocator blocks and caches while slot 0 is idle, the small models, idle side conversations, slot 0's idle conversation (to the NVMe first). chatd asks it before a sandbox waits (SRV-483). *Body:* `{bytes}`. *Answer:* what each step gave back and the total. |
| `GET` `/dashboard/` | open | No page: every engine answers 404 `no_ui` under `/dashboard` (engine API 1.8); TandemApp's controller serves the page. *Answer:* 404 `no_ui`. |
| `GET` `/v1/dashboard/summary?from=&to=&tz=&model=&client=&backend=&user=&internal=` | dash | The Usage view's totals for a range. *Answer:* `summary.schema.json` in contracts/engine-api-v1/dashboard. |
| `GET` `/v1/dashboard/usage?bucket=hour\|day&from=&to=&tz=&model=&client=&backend=&user=&internal=` | dash | Tokens, requests and cost per hour or day. *Answer:* `usage.schema.json`. |
| `GET` `/v1/dashboard/requests?limit=&before=&finish=&model=&client=&backend=&user=&internal=` | dash | Recent request rows of the usage ledger, newest first. *Answer:* `requests.schema.json`. |
| `GET` `/v1/dashboard/system` | dash | The machine, the engine, the memory plan and the slots; the proxy's mode is always off. *Answer:* `system.schema.json`. |
| `GET` `/v1/dashboard/live` | dash | What each request is doing now, as server-sent events. *Answer:* `live.schema.json` events. |
| `GET` `/v1/dashboard/logs?level=&grep=&backlog=&since=&follow=` | dash | The log: a backlog of up to 5,000 lines, then live lines as server-sent events; Last-Event-ID resumes; follow=0 answers JSON once. *Answer:* `logs-line.schema.json` events, or `logs-json.schema.json`; 429 past four streams at once. |
| `GET` `/v1/dashboard/logs/export` | dash | The whole log ring as one text file: a header with the version, the commit, the uptime, the time of export and the line count, then every line the ring holds, at all levels, oldest first. It takes no stream place. *Answer:* text/plain as an attachment named tandemllm-&lt;version&gt;-&lt;time&gt;.log. |
| `GET` `/v1/dashboard/metrics` | dash | The `/metrics` page under the dashboard's rule, for the Performance view. *Answer:* Prometheus text format. |
| `GET` `/v1/dashboard/parallel?since=` | dash | Every request in flight on the engine. *Answer:* `parallel.schema.json`. |
| `GET` `/v1/dashboard/session` | open | Whether a sign-in exists and is needed. *Answer:* `{authenticated, login, password_set, expires_at}`. |
| `POST` `/v1/dashboard/session` | open | The sign-in, with a password set and the login on: sets the `qse_dash` cookie. Five failures a minute from one address and the sixth gets a 429. *Body:* `{token}`. *Answer:* the session; 401 for a wrong token. |
| `DELETE` `/v1/dashboard/session` | open | Signs this session out. *Answer:* the session, signed out. |
| `*` `/v1/conductor/*` | admin | The conductor's documents, journal, sessions and memory, served by the extension in chat mode (TandemApp's docs lists them). Reads follow the dashboard rule, writes and every memory route the admin rule. 404 `not_on_engine` on a headless engine. |
| `*` `/v1/setup/*` | admin | The Setup Guide's API (docs/setup.md): the model registry, the box, the memory estimate, the review plan, the apply and reset jobs and Wren. Every POST must also be same-origin JSON: 415 for another content type, 403 for another origin. In setup mode (`--setup`) these, the session and `/health` are the only routes that answer. |
| `*` `/v1/config/*` | open | 404 `not_on_engine` on every engine (engine API 1.8): other backends sit behind TandemApp's controller. *Answer:* 404 `not_on_engine`. |
| `POST` `/v1/collect/{backend}` | open | 404 `not_on_engine` on every engine (engine API 1.8): the host collector is TandemApp's controller's. *Answer:* 404 `not_on_engine`. |
| `*` `/v1/chat-app` | open | 404 `not_on_engine` on every engine (engine API 1.8), and every path under it: TandemApp's controller serves the Chat tab's service. The body is not read. *Answer:* 404 `not_on_engine`. |
| `POST` `/v1/systemone` | admin | Typed decisions from the decision models, in the engine's idle window. `/v1/predict` is the same route. Only with QWEN38\_DECIDER\_MODELS. *Body:* {state, model?, questions: `{id: {type, instructions, criteria?}}`, task?, lang?}. *Answer:* `{model, answers, usage, routing, latency_ms, path}`. |
| `POST` `/v1/predict` | admin | The same as `/v1/systemone.` *Body:* as `/v1/systemone`. *Answer:* as `/v1/systemone`. |
| `GET` `/readyz` | open | Whether a decision model is loaded and warm. *Answer:* 200, or 503 with the reason. |
| `GET` `/v1/laya` | open | What the Laya route takes and answers, its limits, and whether it is on. The Laya, library and session memory routes below are the extension's in chat mode (TandemApp's docs has their shapes); a headless engine answers them 404 `not_on_engine`. *Answer:* a description of the route. |
| `POST` `/v1/laya/ask` | open | A typed answer from Laya on one text or many, for the model's own scripts; rate-limited. *Body:* `{question, choices \| scale \| neither, text \| texts, threshold?, model?, deadline_ms?}`. *Answer:* `{label, p, sure, probs, model, ms, ...}`, or `{answers: [...]}` for texts. |
| `POST` `/v1/laya/drive/end` | open | Ends a playtest driver session and writes its journal row. *Body:* the session's summary. *Answer:* the row. |
| `GET` `/v1/laya/kit/{file}` | open | A file of the playtest kit the model copies into a project. *Answer:* the file. |
| `OPTIONS` `/v1/laya/*` | open | A browser's CORS preflight for the Laya routes, which the engine asks the extension for. Every other path answers OPTIONS with 501. *Answer:* 204 with the extension's CORS headers. |
| `GET` `/v1/library` | open | The package library: every enabled skill's name, description and classes. *Answer:* the list and the two routes below. |
| `GET` `/v1/library/search?q=&k=&class=` | open | BM25 search over the skills, best first (k from 1 to 20, default 5). *Answer:* [`{name, description, score}`]. |
| `GET` `/v1/library/{name}` | open | One skill's text and its reference. *Answer:* `{name, description, classes, body, reference}`. |
| `GET` `/s/{token}/v1/memory` | session | The memory index this session sees. The `/s/{token}/` prefix also serves the library and Laya routes, filed to the session. *Answer:* the notes' index lines. |
| `GET` `/s/{token}/v1/memory/search?q=&k=` | session | Memory notes that match, best first (k from 1 to 10). *Answer:* [`{id, kind, scope, status, title, score}`]. |
| `GET` `/s/{token}/v1/memory/{id}` | session | One note. *Answer:* `{id, title, kind, scope, status, body, ...}`. |
| `POST` `/s/{token}/v1/memory` | session | Writes, changes or archives a note, with the fields of the hidden memory block. *Body:* `{op, scope, kind, title, tags, body, id, reason}`. *Answer:* `{op, id, title, ...}`, or 422 with the reason as the type. |

### The status responder beside the engine

| server | route | answer |
|-|-|-|
| `ops/engine_status.py` | `GET` `/v1/models` | 200 with the engine's last model list and its state, while the engine is away |
| `ops/engine_status.py` | `GET` `/dashboard/` | 503 with a page that says why the engine is away and opens the dashboard when it is back |
| `ops/engine_status.py` | `*` `any other path` | 503 `provider_unavailable` with Retry-After and the state as engine\_&lt;state&gt; |

<!-- /generated -->

## Request fields of the engine's own

Two fields of a chat or completion request concern the wait for memory ([waiting for memory](../server.md)). The engine reads them; [What the API supports](../server.md#what-the-api-supports) lists the OpenAI fields.

- `tandem.priority`: `"high"`, `"normal"` or `"low"`, the chat's place in the line for memory and in the order the KV pool's reclaim takes idle conversations. Any other value, or none, is normal (0.15.3).
- `wait_for_memory: true`, in the body or in its `tandem` object: a request without a stream waits for memory up to `QSE_MEM_WAIT_S` instead of `--queue-timeout`, as the header `X-Tandem-Wait-For-Memory: 1` asks (0.15.3).

## Headers

the table below lists every header the engine reads or writes beyond the ones HTTP itself needs. *in* is a header the server reads, *out* one it writes.

<!-- generated: http-headers by tools/gen_reference.py; do not edit by hand -->

### Every header the engine reads or writes (both: TandemApp's controller too)

| header | direction, where | meaning |
|-|-|-|
| `X-Tandem` | both, both | raw, shadow or on: lowers the conductor's mode for one request; the response says which mode ran |
| `X-Tandem-Profile` | both, both | picks a conductor profile; the response names the profile that ran; an unknown name is a 400 |
| `X-Tandem-Memory` | in, engine | off: no memory note is read or written; read: reads only, a block the model writes is hidden and dropped |
| `X-Tandem-Probe` | in, engine | off takes the request out of the probe snapshots |
| `X-Tandem-User` | in, engine | a proxy's user name for the memory notes, honoured from loopback or QWEN38\_MEMORY\_PROXIES |
| `X-Tandem-Relay-Id` | in, engine | a proxy's id for a relayed request, for the live counts; a relayed request without X-Tandem-User touches no memory |
| `X-Tandem-Parent` | in, engine | the request a call of TandemLLM to itself serves; the row becomes client kind internal |
| `X-Tandem-Worker` | in, engine | 1 on an agent's worker request: its session comes back, so it is never a guest, and its rows go to the NVMe rather than away while it waits |
| `X-Tandem-Parent-Session` | in, engine | a worker's parent session (the chat's): for 600 s after the worker's last request the parent's rows go to the NVMe rather than away, and its return tries slot 0 first |
| `X-Tandem-Regenerate` | in, engine | 1 serves the request as a regenerate (sampled, a fresh seed); 0 turns the detection off for it |
| `X-Tandem-Images-Left-Out` | out, engine | how many earlier images were left out of the prompt |
| `X-Tandem-Wait` | out, engine | memory, on a response that waited for memory before its prefill; its stream says so every 15 s |
| `X-Tandem-Wait-For-Memory` | in, engine | 1: a request not streamed waits for memory up to `QSE_MEM_WAIT_S`, as `wait_for_memory`: true in the body does, instead of a 503 after the queue timeout |
| `X-Engine-Stop` | out, engine | pattern-stop(&lt;label&gt;) on a response without a stream that a stop pattern ended |
| `x-session-id` | in, both | opencode's session id: the router's session and the caches' session key |
| `X-Conversation-Id` | in, both | a conversation id for clients that send no session field; read after the body's own ids |
| `X-OpenWebUI-Chat-Id` | in, both | Open WebUI's chat id (a connection header with `{{CHAT_ID}}`): the router's session for that chat |
| `X-QSE-Client` | in, engine | dashboard marks the dashboard's own requests (client kind dashboard) |
| `X-Real-IP` | in, both | set by a reverse proxy; a request that carries it is never trusted as local; the sign-in's rate limit counts per address |
| `X-Forwarded-For` | in, both | as X-Real-IP: a request that carries it is never trusted as local |
| `X-Forwarded-Proto` | in, both | https makes the session cookie Secure, and the notes' `{tandem_url}` https |
| `X-Forwarded-Host` | in, engine | the host the notes' `{tandem_url}` names, unless QWEN38\_TANDEM\_URL sets it |
| `X-Decider-Deadline-Ms` | in, engine | how long a decision request waits for the idle window (default 30,000, at most 120,000) |
| `X-Decider-Window` | in, engine | `in_decode` is refused: decisions run in the idle window only |
| `X-Accel-Buffering` | out, both | no, on every stream, so nginx sends each event at once |
| `X-Content-Type-Options` | out, both | nosniff on the dashboard's static files |
| `Authorization` | in, both | Bearer: the admin token, the metrics token, the collect token or a proxy user key, by route; the engine's OpenAI routes ignore it and file the request under a hash of it |
| `User-Agent` | in, both | the client kind in the ledger: open-webui, opencode, openai-sdk, curl, other (a Hermes-shaped body: hermes) |
| `Last-Event-ID` | in, both | resumes the log stream after that line |
| `Retry-After` | out, both | with a 503 (queue full, memory gate, draining, a full backend), a 429, or a decision model that is loading |
| `WWW-Authenticate` | out, both | Bearer, with a 401 |
| `Set-Cookie` | out, both | `qse_dash`, the dashboard session: HttpOnly, SameSite=Strict, Secure behind https |
| `X-Tandem-Pair-Key` | in, engine | the pairing window key (run/remote/pair-local.key): tandem pair opens and closes a window with it, from the machine itself |

<!-- /generated -->

## Errors

Errors are OpenAI-shaped:

```jsonc
{"error": {"message": "max_tokens must be at least 1", "type": "invalid_request_error",
           "param": "max_tokens", "code": null}}
```

A 400 names the field or the message part it refuses in `param`. A refusal the client can retry (a full queue, the memory gate, a server that drains) has the type `server_busy`, the status in `code`, and a `Retry-After` header. A refusal after a stream opened (a queue timeout, a prompt too long, the memory gate) ends the stream with a chunk whose `finish_reason` is `"error"` and which carries the `error` object, then `[DONE]`. The decision routes keep their own body, `{"type": "error", "error": {"type", "message"}}`. The tables below list every status and type: the first explains the common ones, the second lists every other type the code answers, with the code's own message.

<!-- generated: http-errors by tools/gen_reference.py; do not edit by hand -->

### The errors explained

| status | error.type or code | when |
|-|-|-|
| `400` | `invalid_request_error` | a request the engine will not serve as asked: error.param names the field (messages, a role, `max_tokens`, tools, an image part, a prompt too long, a body that is not JSON or not an object) |
| `400` | `bad_request` | a dashboard or conductor query that does not parse (a level, a range, a number) |
| `400` | `stage_text_only` | a request with images on the tail stage of a split model (`--stage`), as error.code beside `invalid_request_error` |
| `400` | `confirm_required` | a setup reset without the confirmation its level asks for (true, or the typed word) |
| `401` | `unauthorized` | a guarded route without the token or the session it needs, once an admin password is set; a wrong collect token |
| `403` | `local_only`, `window_key` | opening or closing a pairing window (POST `/v1/engine/pair/open`, `/close`) from another machine, or without the window key |
| `404` | `not_found` | no such route, or a route whose part is off on this server (the conductor, the proxy, the decision models) |
| `404` | `not_on_engine` | a route TandemApp serves (the conductor, its library, memory and Laya, the backends) on an engine where no extension serves it |
| `404` | `no_pairing` | a pairing route on a process that serves no pairing |
| `404` | `no_ui` | the page, on every engine: TandemApp's controller serves it |
| `404` | `model_not_found` | a model id no backend serves, or one two backends serve (the message names the prefixed forms) (code `model_not_found`) |
| `409` | `busy`, `not_loaded`, `role_off` | an action that needs a role or a decision model in another state, or a role the roles document switches off (`role_off`) |
| `409` | `stage_front` | any route but `/health` on the front stage of a split model (`--stage`): requests go to the tail stage |
| `422` | `invalid_request_error` | a value the schema refuses: a config field (error.field), a field an environment variable locks (error.variable), a decision request's fields |
| `429` with `Retry-After` | `server_busy` | a request without a stream that waited `--queue-timeout` for the engine |
| `429` with `Retry-After` | `too_many` | a fifth log or live stream at once; a sixth failed sign-in in a minute |
| `429` with `Retry-After` | `rate_limited` | `/v1/tokenize` past its 240 calls a minute from one address; a pairing route past 20 a minute from one address |
| `500` | `internal_error` | a bug: the message is the exception's; the request is in the log |
| `500` | `internal` | a PATCH `/v1/engine/settings` that raised: the message is the exception's; the call is journaled |
| `500` | `extension_error` | a route of the extension that raised: the extension's bug, in the engine's log |
| `501` | none | OPTIONS on any path but the Laya routes |
| `503` with `Retry-After` | `server_busy` | the queue is full, the server is draining, or the memory gate or the admission by bytes cannot make room |
| `503` with `Retry-After` | `memory_wait_timeout` | a request that waited for memory to its bound: `--queue-timeout` without a stream, `QSE_MEM_WAIT_S` streamed or with `wait_for_memory`; a stream that opened while it waited ends with it as an error event (type `server_busy`, Retry-After: 30) |
| `503` with `Retry-After` | `setup_mode` | any route but the dashboard, `/health`, `/v1/models` and `/v1/setup/*` on a box in setup mode (Retry-After: 60) |
| `503` with `Retry-After` | `warming`, `loading`, `refused`, `failed`, `not_loaded`, `busy_decoding` | a decision request while the model loads, was refused memory, failed, or the engine kept decoding past the deadline |
| `503` with `Retry-After` | `identity_pending` | the replica identity while the engine computes it, once after the start (Retry-After: 1) |
| `503` with `Retry-After` | `stage_down` | a completion on the tail stage of a split model while its link to the front stage is down (Retry-After: 5); a request in flight when the link fails ends with 500 and the same word in its message |
| `503` with `Retry-After` | `engine_failed` | a completion after the engine's CUDA context was lost: the sentence says so, the watchdog restarts the engine (Retry-After: 60) |
| `503` with `Retry-After` | `provider_unavailable` | the status responder while the engine is stopped, starting or held (code engine\_&lt;state&gt;) |
| `529` | `overloaded_error` | a decision request the runtime could not take |

### Every other error type the code answers, with its status and the code's own message ({...} is a value)

| error.type or code | status | message | where |
|-|-|-|-|
| `active_profile` | 409 | {...} is the running setup: switch to another model first, or remove it after the switch | `server/setup/remove.py` |
| `active_unknown` | 409 | . | `server/setup_api.py` |
| `browser_only` | 400 | level 1 (the setup progress and Wren's chat) is the browser's own: the page clears it | `server/setup_api.py` |
| `invalid_combination` | 422 |   | `server/setup/estimate.py` |
| `invalid_level` | 422 | level is 2, 3, 4 or 5 | `server/setup_api.py` |
| `invalid_request` | 422 | remove takes profiles, confirm and dry\_run | `server/setup/remove.py`, `server/setup_api.py` |
| `invalid_selection` | 422 | the body must be a JSON object | `server/setup/estimate.py`, `server/setup_api.py` |
| `invalid_split` | 422 | split.{...}: {...} | `server/setup/split.py` |
| `invalid_value` | 422 | {...}.{...}: {...} | `server/setup/estimate.py` |
| `job_running` | 409 | job {...} runs; one setup job at a time | `server/setup_api.py` |
| `not_installed` | 404 | {...} has no weights on this box | `server/setup/remove.py` |
| `not_retryable` | 409 | job {...} is {...}: only a failed or rolled back job is tried again | `server/setup_api.py` |
| `not_running` | 409 |   | `server/engine_routes.py` |
| `off_step` | 422 | {...}: a multiple of {...}, not {...} | `server/setup/estimate.py` |
| `out_of_range` | 422 | {...}: a whole number from {...} to {...} | `server/setup/estimate.py` |
| `shared` | 409 | {...}'s folder is also {...}'s; remove them together | `server/setup/remove.py` |
| `timeout` | 408 | the request body stopped arriving | `server/app.py` |
| `too_large` | 413 | a setup request body is at most {...} KiB | `server/api/http_body.py`, `server/setup_api.py` |
| `unknown_component` | 400 | no tool {...} for {...} | `server/setup/estimate.py` |
| `unknown_device` | 400 | no device preset {...} for {...} | `server/setup/estimate.py` |
| `unknown_field` | 422 | unknown field {...}; reset takes level, confirm and dry\_run | `server/setup/estimate.py`, `server/setup_api.py` |
| `unknown_model` | 400, 404 | no model {...} in models/ | `server/setup/estimate.py`, `server/setup_api.py` |
| `unknown_profile` | 400 | no weight profile {...} for {...} | `server/setup/estimate.py` |
| `unsafe` | 409 | {...} is not removed: {...} | `server/setup/remove.py` |

<!-- /generated -->

