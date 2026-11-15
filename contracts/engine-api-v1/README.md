# Engine API contract v1

These files are the truth for what TandemApp (the dashboard, the controller, the conductor, chatd and TandemNode) may use of a TandemLLM engine (`engine/`, `server/`): the HTTP routes, the extensions to the OpenAI shapes, the shapes of the engine's own reads, its metrics, its tool-call parsing, and how TandemNode controls an install without importing it. The engine owns them: they live at the engine's root (`contracts/engine-api-v1/`, since 1.3), and TandemApp keeps a copy pinned to an engine commit (`PIN` in its copy, `tests/test_contract_pin.py`). The engine's tests hold its code to them (`tests/test_engine_api_contract.py`, `tests/test_engine_api_live.py`, `tests/test_contract_toolcalls.py`); TandemApp's tests hold its callers and its copies to them (`tests/test_engine_api_contract.py`, `tests/test_contract_toolcalls.py`). Its version is `1.8`, independent of either release. The in-process extension interface is its neighbour, `../engine-ext-v1` (its own version).

| file | what |
|-|-|
| `routes.json` | every route by group with its TandemApp callers (`used_by`, or `consumer` when no TandemApp file calls it), its shape, and `live`: a GET an engine without a model (`--fake-engine`) answers 200; the extension's routes in the engine's process; the answers where nothing serves a path; the controller's own families |
| `health.schema.json`, `health.example.json` | `GET /health`: the open, full, setup-mode and status responder answers |
| `health.stage-front.example.json`, `health.stage-tail.example.json` | `GET /health` of the two stage processes of a split model, with the `stage` block (since 1.2) |
| `setup/estimate-cases.json` | `models/estimate-cases.json` byte for byte: the Setup Guide's estimate formula pinned by cases |
| `setup/limits.json` | `engine_max_slots`, the registry's `ENGINE_MAX_SLOTS` |
| `setup/split.schema.json`, `setup/split.example.json`, `setup/split-request.example.json` | `POST /v1/setup/estimate`'s optional `split` ask (`$defs.ask`) and block (since 1.2); the example is a block the engine computed from a real pack's headers |
| `node/serve.env.example` | the `PORT=` and `QSE_STATUS_PORT=` lines install.sh writes to `run/ops/serve.env` from `ops/serve.env` |
| `node/hub_manifest.example.json` | the `_hub_manifest.json` `ops/fetch_weights.py` writes beside a pack (here each file is `size` zero bytes) |
| `dashboard/` | the engine's own reads (since 1.3, moved from TandemApp's `dashboard-v1`, byte for byte): `live`, `system`, `logs-json`, `logs-line`, `parallel`, `summary`, `usage`, `requests` schemas and examples, and `logs-export.example.txt`. They keep their `$id` and their `contract_version` field |
| `decision/systemone.schema.json` | `POST /v1/systemone`'s shape (since 1.3, a copy of TandemApp's `conductor-v1` file) |
| `metrics-families.json`, `metrics.md` | every `/metrics` family by name, type, labels and owner (`engine`, or `ext`: registered by the engine extension and absent on a headless engine), and what each counts (since 1.3) |
| `toolcalls/` | the tool-call fixtures, one file per form (`xml`, `argkey`, `pythonic`) and `repair.json`, and `check.py`, the runner both parser copies pass (since 1.3) |
| `auth.json` | the write guard (`same_origin`, `same_origin_json`) as a table of header cases and answers, which both copies of the rule answer (since 1.3) |
| `cache.json` | `POST /v1/cache/peek`'s tier names, `tier_of` (since 1.3) |
| `compat/1.2/` | the 1.2 routes and examples as they were, which 1.3 still has to accept (since 1.3) |
| `compat/1.3/` | the 1.3 routes and `/health` examples as they were, which 1.4 still has to accept (since 1.4) |
| `compat/1.4/` | the 1.4 routes and `/health` examples as they were, which 1.5 still has to accept (since 1.5) |
| `compat/1.5/` | the 1.5 routes and `/health` examples as they were, which 1.6 still has to accept (since 1.6) |
| `compat/1.6/` | the 1.6 routes and `/health` examples as they were, which 1.7 still has to accept (since 1.7) |
| `compat/1.7/` | the 1.7 routes and `/health` examples as they were, which 1.8 still has to accept (since 1.8) |
| `engine/` | group 9, the engine-only screens (since 1.5): `residency-plan`, `disk`, `disk-clear`, `deciders-status`, `decider-action`, `narrator-status`, `narrator-action`, `shadow-status`, `shadow-ask` and `error` schemas, with examples |
| `node/control.json` | bin/tandem's verbs and the install.sh flags TandemNode and the controller use; `stage`: the `run/local.env` keys of a split model's stage, the start and stop order and `bin/tandem status`'s stage line (since 1.2); `chat`: who runs chatd on an engine box, the engine's `QSE_CHATD` on, off or node, and what each launcher does with node (since 1.6); since 1.7 the engine runs no chatd: node or off, `on` read as node and unset as off, and the `install.sh --with-chat --app-wheel` install |
| `PIN` | only in TandemApp's copy: the engine commit it was taken from, the versions, and the tree id of this folder and of `../engine-ext-v1` (since 1.3) |

## The version

- `engine_api` (MAJOR.MINOR, `server/engine_api.py` `VERSION`) is in the full `/health` answer, the setup-mode `/health` and the status responder's `/health`. The open answer, which a caller through a proxy gets, stays `{status}`.
- MINOR is additive: a new route or a new optional field. MAJOR is a removed or renamed route or field, or a changed meaning.
- An engine without the field speaks 1.0: every engine up to 0.15.x.
- A TandemApp on 1.3 takes any 1.x engine. A route or field added in a minor exists only on an engine that says that minor or later: a caller checks `engine_api` first and treats an older engine as one without it (`GET /v1/replica/identity` needs 1.1; the estimate's `split` and `/health`'s `stage` need 1.2: an older engine is "not splittable: engine too old"; it answers a `split` beside a `selection` as if the ask were not there, and a `split` inside a bare selection with 422 `unknown_field`, so a caller sends the ask beside the `selection` and only to 1.2 or later).
- 1.2 is additive: a 1.0 or 1.1 engine's answers still validate against these files (no field became required, no enum value went away), and a body without the `split` ask is answered byte for byte as before on 1.2 too.
- 1.3 is additive and changes no answer: it moves the folder to the engine and writes down what the engine already serves (the reads' shapes, the metrics, the tool-call parsing, the copied values), marks what leaves the engine, and lists what is planned. Every 1.2 route is still here with its method and aliases, and the 1.2 examples (`compat/1.2/`) still validate.
- 1.4 is additive (ENG-413 WP3a, the extension host `server/ext.py`): the full `/health` answer has `extensions`, the engine-ext-v1 extension the engine runs (`[{name, version, ext_api}]`, `[]` on a headless engine), and a path TandemApp serves answers 404 `no_ui` or `not_on_engine` where nothing in the engine serves it (`routes.json` `answers`, below). Every 1.3 route is still here, the 1.3 examples (`compat/1.3/`) still validate, and a 1.3 caller that reads no `extensions` sees the answers it saw.
- 1.5 is additive (ENG-413 WP5): group 9, the engine-only screens as the engine's own `/v1/engine/*` routes (below). Every 1.4 route is still here, the 1.4 examples (`compat/1.4/`) still validate, and the conductor's old paths answer as before while they last.
- 1.6 is additive (ENG-413 WP6, SRV-823): `node/control.json` `chat`. With `QSE_CHATD=node` TandemNode owns chatd: `ops/start.sh`, `ops/watchdog.sh` and `bin/tandem` neither start nor stop it, and `bin/tandem chat` runs `tandem-node chat`. No route or answer changes; the 1.5 examples (`compat/1.5/`) still validate. An engine before 1.6 reads node as on, so TandemNode hands chatd over only to a 1.6 engine, or to one that says off.
- 1.7 (ENG-413 WP7, OPS-272): the engine has no chatd launcher. `ops/start.sh`, `ops/watchdog.sh` and `bin/tandem` never start or stop chatd; `QSE_CHATD` is node (TandemNode runs it) or off (nobody does), serve.env's mode block reads `on` as node (start.sh says so in one line) and unset as off, and `bin/tandem chat` and `bin/tandem node` run TandemApp's `tandem-node`. `install.sh --with-chat --app-wheel FILE` installs TandemApp's wheel into the engine's venv and TandemNode's unit with `--chat on`, and writes `QSE_CHATD=node`. No route or answer changes; the 1.6 examples (`compat/1.6/`) still validate. An install from before 1.7 keeps its own launchers until it is installed again (`node/control.json` `chat.update`).
- 1.8 (ENG-413 WP14): the engine serves no page, no Chat tab forward and no other backends. `/dashboard*` answers 404 `no_ui` and `/v1/chat-app*`, `/v1/config*` and `/v1/collect/*` 404 `not_on_engine` on every engine, with or without an extension and in setup mode; `--backends` takes only `off`; `/v1/dashboard/system` has no `hosts` and `proxy.mode` is `off`. These paths were TandemApp's routes in the engine's process, never routes of this API, so no route of it goes. `dashboard/` gains `session`, `error` and `gap`, the shapes of what the engine serves under `/v1/dashboard` (byte copies of TandemApp's `dashboard-v1` files). Two routes of group 1 say `relay_only`: `POST /v1/embeddings` and `POST /v1/responses`. The engine never answered them itself. The `--backends` proxy relayed them to a backend, and an engine without it answered 404 `not_found`, as every engine does since 1.8. The 1.7 examples (`compat/1.7/`) still validate.
- A field or route of `planned` is not part of the minor that plans it: an engine has it only from the minor that lists it under `routes`, `answers` or in a schema.
- TandemApp's `proxy/engineapi.py` (`SPEAKS` 1.2) reads it for an engine of kind `tandemllm`. Another major is not ok: "this engine speaks engine API 2.0; TandemApp 0.15.0 needs 1.x". TandemNode's engine view then says `unhealthy` with that text in `health.api_why`; a deployment member on that engine is `down` with it and takes no request, not as a fallback and not for a model no record names (a derived Single's or a routing rule's: 503 `no_member`); a deployment's start fails at once with it (`start_failed`).
- Not read here yet: chatd's own engine (SRV-735) and backends the controller reaches directly (SRV-749).

## Routes

### 1. OpenAI data plane

| route | TandemApp's use | shape |
|-|-|-|
| `POST /v1/chat/completions` | `proxy/service.py`, `proxy/canary.py`, `tandemapp/conductor/classify.py`, `chatd/engine.py`, `chatd/agent.py`, `chatd/service.py`, `dashboard/src/api/client.ts`, `dashboard/src/views/playground.ts` | OpenAI chat completions; the extensions below |
| `POST /v1/completions` | `proxy/service.py` | OpenAI completions |
| `POST /v1/embeddings` | `proxy/service.py`, `proxy/relay.py` | OpenAI embeddings |
| `POST /v1/responses` | `proxy/service.py` | OpenAI responses |
| `GET /v1/models` | `proxy/nodeengines.py`, `proxy/nodesource.py`, `tandemapp/conductor/classify.py`, `chatd/engine.py`, `dashboard/src/api/fleet.ts` | OpenAI model list; each model's `tandem` card, and `decision_models` |
| `GET /v1/models/{id}` | `proxy/service.py`, `tandemapp/conductor/classify.py` | one model's card |

### 2. Health and metrics

The full answer goes to a loopback caller without proxy headers (TandemNode on its box) or a caller with the token: `status_port`, `version`, `model`, `base_model`, `weights.name`, `uptime_s`, `max_len`, `default_max_tokens`, `memory`, `parallel`, `engine_api`. `status` is `ok`, `busy`, `draining` (503) or `failed` (500, a GPU context that no longer answers); a tail stage says `degraded` (200) while its link is down (since 1.2). The setup-mode answer: `mode` setup, `status_port`, `assistant`, `engine_api`. `/metrics` is the `qse_*` families of `metrics.md` here.

**The `stage` block (since 1.2, optional).** Only a stage process of a split model has it (`health.schema.json` `stage`, `docs/server.md` "The stage block"); any other engine has no `stage` key. Shape 1, every field always present: `shape`, `role` (`front` or `tail`), `lo`, `hi`, `range` (`LO:HI`), `layers`, `cut`, `identity` (16 hex digits, null on the tail before its first link), `options` (the stage's limits: `slots`, `caches`, ...), `link` (`state` up or down, `epoch`, `reason` null while up, `transport`, `bytes`, `peer` with the other stage's `role`, `lo`, `hi`, `identity`); the front adds `steps`, and with caches on both add `state` (live counters). A later field is optional. Nothing in it is measured. The tail is a full engine (its `/health` has `engine_api`); the front runs a small server of its own that answers `/health` with `status`, `stage`, `model` and `uptime_s` only, and 409 `stage_front` on every other route. The front has no `engine_api`: a caller reads its block by its presence, and the link's handshake refuses a front of other code (`refused (code)`), so a front linked to a tail on 1.2 runs the same code. A deployment's checks: both `link.state` up, the same `link.epoch`, each side's `link.peer.identity` the other's `identity`, `front.hi == tail.lo`, `tail.hi == layers`, equal `options.slots` and `options.caches`.

| route | TandemApp's use | shape |
|-|-|-|
| `GET /health`, `/healthz`, `/v1/health` | `proxy/nodeengines.py`, `chatd/engine.py`, `dashboard/src/app.ts`, `dashboard/src/setup/api.ts`, `dashboard/src/setup/install.ts` | `health.schema.json` |
| `GET /metrics` | `proxy/nodeengines.py`, `proxy/nodesource.py`, `dashboard/src/views/performance.ts` | Prometheus text, the qse_* families of `metrics-families.json` (`metrics.md`) |
| `GET /metrics/up` | a Prometheus scrape (the engine's ops/monitoring); no TandemApp file | one number for a scraper: the engine answers |
| `GET /v1/replica/identity` | `proxy/canary.py`, `proxy/nodeengines.py` (TandemNode's relay) | the identity block (`server/identity.py`, `docs/server.md`), under the caller rule of `/v1/models`; 503 `identity_pending` until it is computed, once after the start. Since 1.1 |

### 3. Engine dashboard reads

| route | TandemApp's use | shape |
|-|-|-|
| `GET /v1/dashboard/live` | `proxy/nodeengines.py`, `proxy/nodesource.py`, `chatd/engine.py`, `dashboard/src/api/client.ts`, `dashboard/src/views/live-panel.ts` | `dashboard/live.schema.json` |
| `GET /v1/dashboard/system` | `proxy/nodeengines.py`, `proxy/nodesource.py`, `chatd/engine.py`, `dashboard/src/api/client.ts` | `dashboard/system.schema.json` |
| `GET /v1/dashboard/logs` | `proxy/nodeengines.py`, `proxy/nodesource.py`, `proxy/nodeagent.py`, `dashboard/src/api/client.ts` | `dashboard/logs-json.schema.json` |
| `GET /v1/dashboard/parallel` | `proxy/relay.py`, `tandemapp/parallel.py`, `dashboard/src/api/client.ts`, `dashboard/src/views/parallel.ts` | `dashboard/parallel.schema.json` |
| `GET /v1/dashboard/metrics` | `dashboard/src/api/client.ts` | the /metrics text under the dashboard's access rule; leaves at 2.0 (below) |

### 4. Ledger reads

| route | TandemApp's use | shape |
|-|-|-|
| `GET /v1/dashboard/summary` | `dashboard/src/api/client.ts` | `dashboard/summary.schema.json` |
| `GET /v1/dashboard/usage` | `dashboard/src/api/client.ts` | `dashboard/usage.schema.json` |
| `GET /v1/dashboard/requests` | `dashboard/src/api/client.ts`, `tools/tandem_eval.py` | `dashboard/requests.schema.json` |

### 5. Tokenize and cache

| route | TandemApp's use | shape |
|-|-|-|
| `POST /v1/tokenize` | `chatd/agent.py`, `proxy/deployments.py` | the exact prompt tokens of a request (the model's own template) |
| `POST /v1/cache/peek` | `chatd/service.py`, `proxy/deployments.py` | where a prompt's prefix is cached (the tier names of `cache.json`) |
| `POST /v1/cache/prewarm` | `chatd/service.py`, `proxy/deployments.py` | load a prompt's prefix ahead of its request |
| `POST /v1/cache/release` | `chatd/engine.py`, `proxy/deployments.py` | the engine gives back memory it holds |
| `POST /v1/cache/clear` | `tools/tandem_eval.py` | the prefix store emptied (the admin bearer) |

### 6. Setup Guide 1.0

The dashboard builds these from its base `/v1/setup` (or a node's `/v1/fleet/nodes/{id}/setup`, which TandemNode relays); `proxy/fleetsetup.py` and `proxy/nodeagent.py` relay `/v1/setup/<sub>` as it is (`relay:/v1/setup/`). The field shapes are the engine's `docs/setup.md`.

**The estimate's `split` (since 1.2, optional).** `POST /v1/setup/estimate` takes a `split` ask beside the `selection` (or inside a bare selection; `true` is every default): `cuts`, `caches`, `side_window`, `table`, `snapshots_gb`, `front_cache_gb`, `tail_disk_gb`, each optional (`setup/split.schema.json` `$defs.ask`). The answer then carries a `split` block: `splittable` false with a `reason` (`family`, `pack`, `cuts`, `caches`, `slots`) and a `message`, never an HTTP error; or `splittable` true with each cut's `front` and `tail` memory by part (each part's `basis`: `pack_headers`, `config`, `ask`, `registry`, `setting`, `registry_whole_model`), the carry a row, where the n-gram table sits, a side slot's cost, each side's disk, `fits`, `headroom_gb` and `best_cut`. Every number is computed (`computed: true`) except the `measured` list, which is what a split of the family was measured to cost on a GPU, with dates (informational). `fits` and `best_cut` assume two boxes of the answering box's preset; a planner with two different boxes compares `front.gb` and `tail.gb` with each node's own `budget_gb`, asked of that node's own engine. A node without the pack answers `reason: pack`. An invalid ask is 422 `invalid_split` (`error.errors[].field` `split.<field>`). A body without `split` (or `"split": null` beside a selection) is answered exactly as before.

| route | TandemApp's use | shape |
|-|-|-|
| `GET /v1/setup/models` | `relay:/v1/setup/`, `dashboard/src/setup/api.ts` | docs/setup.md |
| `GET /v1/setup/models/{id}` | `relay:/v1/setup/`, `dashboard/src/setup/api.ts` | docs/setup.md |
| `GET /v1/setup/models/{id}/steps` | `relay:/v1/setup/`, `dashboard/src/setup/api.ts` | docs/setup.md |
| `GET /v1/setup/models/{id}/current` | `relay:/v1/setup/`, `dashboard/src/setup/api.ts` | docs/setup.md |
| `GET /v1/setup/device` | `relay:/v1/setup/`, `dashboard/src/setup/api.ts` | docs/setup.md |
| `GET /v1/setup/weights` | `relay:/v1/setup/`, `dashboard/src/setup/api.ts`, `proxy/nodeagent.py` | docs/setup.md |
| `POST /v1/setup/remove` | `relay:/v1/setup/`, `dashboard/src/setup/api.ts`, `proxy/nodeagent.py` | docs/setup.md |
| `GET /v1/setup/status` | `relay:/v1/setup/`, `dashboard/src/setup/api.ts`, `dashboard/src/lib/restart.ts` | docs/setup.md |
| `GET /v1/setup/community` | `relay:/v1/setup/`, `dashboard/src/setup/api.ts` | docs/setup.md |
| `POST /v1/setup/community/sync` | `relay:/v1/setup/`, `dashboard/src/setup/api.ts` | docs/setup.md |
| `POST /v1/setup/estimate` | `relay:/v1/setup/`, `dashboard/src/setup/api.ts`, `proxy/placement.py` | docs/setup.md |
| `POST /v1/setup/plan` | `relay:/v1/setup/`, `dashboard/src/setup/api.ts` | docs/setup.md |
| `POST /v1/setup/apply` | `relay:/v1/setup/`, `dashboard/src/setup/api.ts` | docs/setup.md |
| `GET /v1/setup/jobs/{id}` | `relay:/v1/setup/`, `dashboard/src/setup/api.ts` | docs/setup.md |
| `POST /v1/setup/jobs/{id}/retry` | `relay:/v1/setup/`, `dashboard/src/setup/api.ts` | docs/setup.md |
| `GET /v1/setup/jobs/{id}/diagnostics` | `relay:/v1/setup/`, `dashboard/src/setup/api.ts` | docs/setup.md |
| `GET /v1/setup/reset` | `relay:/v1/setup/`, `dashboard/src/setup/api.ts` | docs/setup.md |
| `POST /v1/setup/reset` | `relay:/v1/setup/`, `dashboard/src/setup/api.ts` | docs/setup.md |
| `GET /v1/setup/assistant/status` | `relay:/v1/setup/`, `dashboard/src/setup/api.ts` | docs/setup.md |
| `POST /v1/setup/assistant/load` | `relay:/v1/setup/`, `dashboard/src/setup/api.ts` | docs/setup.md |
| `POST /v1/setup/assistant/unload` | `relay:/v1/setup/`, `dashboard/src/setup/api.ts` | docs/setup.md |
| `POST /v1/setup/assistant/chat`, `/v1/setup/chat` | `relay:/v1/setup/`, `dashboard/src/setup/api.ts`, `dashboard/src/setup/wren.ts` | docs/setup.md |

### 7. Status responder (`QSE_STATUS_PORT`, ops/engine_status.py)

The same paths on the status responder's port, answered while the engine is away: `/health` 503 with the state, `/v1/models` 200 with the last list, the setup status and a job's events. TandemNode relays the setup paths to it when the engine does not answer.

| route | TandemApp's use | shape |
|-|-|-|
| `GET /health` | `dashboard/src/app.ts`, `dashboard/src/setup/install.ts` | `health.schema.json`; ops/engine_status.py: 503, `status` the engine's state, the facts under `engine` |
| `GET /v1/models`, `/models`, `/v1/models/{id}` | an OpenAI client's model list while the engine restarts (OPS-67); no TandemApp file | ops/engine_status.py: 200, the engine's last model list |
| `GET /v1/setup/status` | `relay:/v1/setup/`, `dashboard/src/lib/restart.ts` | docs/setup.md |
| `GET /v1/setup/jobs/{id}` | `relay:/v1/setup/`, `dashboard/src/setup/api.ts` | docs/setup.md |
| `GET /_tandem/status` | ops/engine_status.py ensure (the engine's own scripts); no TandemApp file | ops/engine_status.py: the responder's tree, status file, version and pid |

### 8. Decision models

| route | TandemApp's use | shape |
|-|-|-|
| `POST /v1/systemone`, `/v1/predict` | `tandemapp/conductor/classify.py` | `decision/systemone.schema.json` |
| `GET /readyz` | `tandemapp/conductor/classify.py` | 200 once the engine's decision models are ready |

### 9. Engine-only screens (since 1.5, ENG-413 WP5)

What the conductor's screens read or load of the engine's own models and memory, served by the engine itself (`server/engine_routes.py`), on a headless engine too. They replace the conductor's paths of the same screens (`replaces`), which left this API with 1.8; the extension still gets the same handlers in process (`host.engine`, `../engine-ext-v1` 3.0). Every answer carries `Cache-Control: no-store`; an error is `{"error": {"type", "message"}}` (`engine/error.schema.json`).

| route | access | answer | shape |
|-|-|-|-|
| `GET /v1/engine/residency/plan` | the dashboard's rule | 200: `version` (the residency document's, 0 for the built-in one), `doc`, `plan`, `engine` (the engine's split), `limits`, `last_refusal`, `journal` (the last 20 lines; `[]` without the extension's directory) | `engine/residency-plan.schema.json` |
| `GET /v1/engine/disk` | `conductor_write` | 200: the disk tier: `running`, its settings and their version, the flag, the floor; the numbers when it runs | `engine/disk.schema.json` |
| `POST /v1/engine/disk/clear` | `conductor_write` | 200: what was on disk and the tier after; 409 `not_running` | `engine/disk-clear.schema.json` |
| `GET /v1/engine/deciders/status` | the dashboard's rule | 200: `enabled` false with `why` when the engine serves no decision model; else the models, the planner's answer, the last refusal, the questions; `api` is the extension's (`deciders_api`), null without it | `engine/deciders-status.schema.json` |
| `POST /v1/engine/deciders/test` | the dashboard's rule | `/v1/systemone`'s answer for the body; its errors as `error` | `decision/systemone.schema.json` |
| `POST /v1/engine/deciders/autoload` | `conductor_write` | 202 `{queued}`; `service_start`, `hold_restart` | - |
| `POST /v1/engine/deciders/models/{name}/load`, `.../unload` | `conductor_write` | 202 or 200 `{name, state, decision}`; 409 with the planner's `decision` and `error`, or `not_loaded`; 404 for a model the engine does not serve | `engine/decider-action.schema.json` |
| `GET /v1/engine/narrator/status` | the dashboard's rule | 200: `enabled` false with `why` (no `QWEN38_NARRATOR_MODEL`, or no extension builds the narrator over its slot), else the narrator's status | `engine/narrator-status.schema.json` |
| `POST /v1/engine/narrator/try`, `/load`, `/unload` | `conductor_write` | `try`: `{lines, slot}` (`events` 1 to 64, `budget_ms` 1 to 10,000, `max_tokens` 1 to 64); `load`: 202 `{state, decision}` or `{state, queued}`, 409 `role_off` or the planner's refusal (the keeper keeps it loaded from then on); `unload`: 200 `{state}`, 409 `not_loaded` | `engine/narrator-action.schema.json` |
| `GET /v1/engine/shadow/status` | the dashboard's rule | 200: `enabled` false with `why`, else the reader's status | `engine/shadow-status.schema.json` |
| `POST /v1/engine/shadow/ask` | `conductor_write` | 200 the decision `{label, confidence, probs, ms, read, tail, behind, session_tokens}`; 409 with the reason; 400 for a body out of bounds | `engine/shadow-ask.schema.json` |

The access rules are `server/api/auth.py`'s, as the conductor's paths had them: with no admin password set every caller passes; with one, a bearer token, the dashboard session or a trusted loopback caller.

`nodeengines.RELAY` (TandemNode's engine relay, GET only) is `/health`, `/v1/models`, `/v1/dashboard/live`, `/v1/dashboard/system`, `/v1/dashboard/logs` and `/metrics`, all in the table above.

## Extensions

- Request headers: `X-Tandem` (`raw` or `shadow`; nothing on a headless engine), `X-Tandem-Profile`, `X-Tandem-Relay-Id`, `X-Tandem-User`, `X-QSE-Client`, `x-session-id`, `X-Tandem-Cost`, `X-Tandem-Parent-Session`, `X-Tandem-Worker`, `X-Tandem-Memory`.
- Response fields: `usage.prompt_tokens_details.cached_tokens`, `usage.completion_tokens_details.reasoning_tokens`, `timings`, `tandem`.
- `Retry-After` on 429 and 503.
- Error types beside OpenAI's: `engine_failed` (the GPU context failed) and `setup_mode` (a setup-mode server serves no model). Since 1.2: `invalid_split` (422, the estimate's `split` ask), and on a split model `stage_down` (503 with `Retry-After: 5`, the tail while its link is down), `stage_text_only` (400, images to a split model) and `stage_front` (409, any route but `/health` on the front).
- `GET /v1/dashboard/parallel`: `lanes[].relay_id` echoes the request's `X-Tandem-Relay-Id`.

## TandemNode's control (not HTTP)

- A loopback caller without proxy headers is trusted and gets the full `/health`.
- `run/ops/serve.env` carries `PORT=` (`node/serve.env.example`); `nodeengines.default_engine` reads it.
- `bin/tandem start|stop|restart`; exit 0 is done (`node/control.json`). On a stage (since 1.2) `bin/tandem status` adds `[tandem] stage: the <role>, layers <LO:HI>, link <state> (epoch <n>)`, and a stage's settings are the `run/local.env` keys of `node/control.json` `stage`: start the front, wait for its `/health` (600 s), then the tail (600 s); stop the tail, then the front.
- `install.sh --dir --ref --runtime-only --yes`. The controller holds the script and its ref: `TANDEM_ENGINE_INSTALL_SH` and `Package.engine_ref()` (`proxy/nodeinstall.py`).
- A pack's `<weights dir>/_hub_manifest.json` (`node/hub_manifest.example.json`); `proxy/nodeweights.py` reads its sizes and sha256 for a content id.
- The agent never imports engine code and never runs `python -m server.*`. TandemApp's `tests/test_import_boundary.py` enforces this.

## TandemApp's routes in the engine's process

Since 1.8 (ENG-413 WP14) the engine runs no TandemApp code of its own. What TandemApp still serves inside an engine process is the extension's: an engine in chat mode loads TandemApp's extension by name (`QSE_EXT`, `../engine-ext-v1`), and the extension registers these routes. They are TandemApp's, under its own contract (`../conductor-v1/` in TandemApp's tree), not part of this API. A headless engine answers them as `answers` says. The last column is `routes.json` `after_eng413`:

| path | what | contract | after ENG-413 |
|-|-|-|-|
| `/v1/conductor/*` | TandemApp's conductor, run in the engine's process | `../conductor-v1/` | extension |
| `/v1/library*` | the package library (library.schema.json) | `../conductor-v1/` | extension |
| `/v1/laya*` | Laya (laya-ask.schema.json) | `../conductor-v1/` | extension |
| `/v1/memory` | the model's own memory, under its session's address | - | extension |
| `/v1/memory/*` | the model's own memory, under its session's address | - | extension |
| `/s/{token}/*` | a session address: /v1/library, /v1/laya and /v1/memory under the session's token (tandemapp/session_tokens.py) | - | extension |
| `/v1/dashboard/session` | the page's cookie sign-in | `dashboard/session.schema.json` | leaves, in a later minor with the cookie rule of `server/api/auth.py` (`planned`) |

Six paths left the engine with 1.8, as decided on 2026-11-13: the page (`/dashboard/*`), the Chat tab's way to chatd through the engine port (`/v1/chat-app`, `/v1/chat-app/*`) and `--backends` (`/v1/config`, `/v1/config/*`, `/v1/collect/*`). TandemApp's controller serves all of them, and TandemApp runs with or without an engine: since 1.8 they are among the controller's own families below. `compat/1.7/routes.json` keeps them as they were listed, and `--backends` takes only `off`. The page's cookie sign-in (`/v1/dashboard/session`) is still served: it leaves together with the cookie check in `server/api/auth.py`, which the engine's admin rule still reads.

One route of this API leaves too, at the next major: `GET /v1/dashboard/metrics` (`routes.json` `leaves`). Only the page on the engine's origin read `/metrics` that way; callers read `GET /metrics`. It is served through every 1.x. The other `/v1/dashboard/*` reads stay: they are the engine's own data, read by TandemNode, chatd and the controller with the bearer token or from loopback.

The controller's own families, never the engine's:

| path | what | contract |
|-|-|-|
| `/v1/fleet/*` | the controller's fleet API; never the engine | `../fleet-v1` |
| `/v1/config`, `/v1/config/*` | the proxy's backends and rules; the controller's since 1.8 | - |
| `/v1/collect/*` | the host collector; the controller's since 1.8 | - |
| `/v1/chat-app`, `/v1/chat-app/*` | chatd, the Chat tab's service; the controller's since 1.8 | - |
| `/dashboard/*` | the page; the controller's since 1.8 | - |

`shared_prefixes` names a base the dashboard joins with sub-paths that fall in more than one list: `/v1/dashboard` (the ledger reads and the sign-in).

The engine-only screens' old paths under `/v1/conductor/` (`residency/plan`, `memory/disk`, `memory/disk/clear`, `deciders/*`, `narrator/*`, `shadow/*`) left this API with 1.8 too. Since 1.5 the engine serves the screens itself as group 9 (`/v1/engine/*`; each route's `replaces` names the old path). What TandemApp's extension does with the old paths is TandemApp's.

## Answers where nothing serves the path (since 1.4)

`routes.json` `answers`. ENG-413 WP3a (the extension host) serves the first two; WP14 adds the third, out of `planned`.

| answer | paths | when |
|-|-|-|
| 404 `no_ui` (`error.type` and `error.code`, the message names TandemApp) | `/dashboard`, `/dashboard/*` | every engine since 1.8, in setup mode too: the engine has no page. From 1.4 to 1.7 a headless engine did, and an extension that served the page answered it |
| 404 `not_on_engine` (`error.type` and `error.code`, the message names TandemApp) | `/v1/conductor/*`, `/v1/library*`, `/v1/memory*`, `/v1/laya*`, a session address of those (`/s/{token}/...`), `/v1/config*`, `/v1/collect/*` | nothing in the engine's process serves the path: no extension route for it; `/v1/laya*` without the engine's decision models; `/v1/config*` and `/v1/collect/*` on every engine since 1.8 (before it, with `--backends off`). Each answered 404 before, with `not_found` |
| 404 `not_on_engine`, since 1.8 | `/v1/chat-app`, `/v1/chat-app/*` | every engine. A body the request carries is not read |

`planned` holds one answer in 1.8: 404 `not_on_engine` on `/v1/dashboard/session`, in the minor that removes the cookie rule. The plan's `/health` `ui` flag was never planned: with no page on any engine it would always be false and tell a caller nothing.

## ENG-413: the engine without TandemApp

Since 1.8 the engine's tree has no `app/`, no finder and no import of `tandemapp`, `proxy` or `chatd` (`tests/test_app_hook.py`, the no-app guard). The only way TandemApp's code runs in an engine process is the extension seam: `server/ext.py` imports the module `QSE_EXT` names, by name, and hands it the engine's objects. The engine's own copies of what it used to import are in `server/api/` (the wire format since WP2a, what it reports of itself since WP2b, the small helpers since WP2c), each held to the contract's fixtures.

Two kinds of files still need TandemApp beside the engine, and neither is part of the engine's own suite:

- The pair tools (`tests/test_api_import_guard.py` `PAIR_TOOLS` says why each one): they find TandemApp through `tools/pair_app.py` (`TANDEMAPP_SRC`, else an installed `tandemapp`) and stop with one line without it.
- The pair tests (`tests/test-ledger.json` `pair_suite`): they run the engine and TandemApp's extension, packages or test helpers together. `tools/pair_suite.sh` runs them with a TandemApp checkout whose `PIN` names this contract; TandemApp's `tools/pair_suite.sh` runs them from its side against an engine checkout at its `PIN`.

The values TandemApp keeps equal to the engine's are held from TandemApp's side now: `same_origin` and `same_origin_json` answer `auth.json`, `TIER_OF` is `cache.json`'s, and its `dashboard-v1` copies equal `dashboard/` byte for byte (TandemApp's `tests/test_engine_api_contract.py`). The engine holds its own to the same files. `reverse-deps.json`, the inventory of the engine's reach into the vendored `app/`, ended with 1.8.

## Tests

- TandemApp's `tests/test_contract_pin.py`: its copy of this folder and of `../engine-ext-v1` hashes to the tree ids `PIN` names (`PIN` itself left out), the versions in `PIN` are the files' own and `proxy.engineapi.SPEAKS`, and the hasher sees every kind of edit.
- TandemApp's `tests/test_engine_api_contract.py` (standard library): routes.json is well formed and its version is `proxy.engineapi.SPEAKS`; every engine path literal in the Python callers and in `dashboard/src` falls in exactly one list (a `${...}` is `{param}`, the query is dropped, a base matches as a prefix, all of it when text follows a `{param}`); every route is used by a file or a relay; `nodeengines.RELAY` is in the routes; the version cases, `nodeengines.health` against fake engines, the health example against its schema, and the node fixtures read by `nodeengines.default_engine` and `nodeweights`; since 1.3 the copied values (`auth.json` against `tandemapp/auth.py`, `cache.json` against `tandemapp/usage.py`; until 1.4 `off_why` against `tandemapp/conductor/routes.py`, since 1.5 no copy of it), the metrics families against `tandemapp/metrics.py`'s registry, its `dashboard-v1` copies of `dashboard/` byte for byte, and the 1.2 examples.
- TandemApp's `tests/test_contract_toolcalls.py`: its parser passes `toolcalls/`.
- TandemApp's `tests/test_node_agent.py` and `tests/test_deployments.py`: an engine on engine API 2.0 is `unhealthy` and names 1.x, its member is `down` and takes no request, a start fails within seconds; 1.4 and a missing field work.
- `tests/test_engine_api_contract.py` (the engine, no torch): every route is dispatched and no planned route is yet; the version is one; the engine's values answer `auth.json` and `cache.json`; the fixtures match the engine's files; the schemas and examples here validate, every reference stays in this folder but the named TandemApp ones; the metrics families are the registry's and `metrics.md`'s; 1.3 is additive over `compat/1.2/`, and each minor after it over the one before (`compat/1.3/` to `compat/1.7/`). `tests/test_engine_routes.py`: group 9 on a fake engine (headless and with an extension), each body and refusal, the access rules, and the same handlers in process. `tests/test_engine_ext_host.py`: the engine's extension host against `../engine-ext-v1` (its loader's refusals, the per-request failure rule, `/health` `extensions` and the answers of a headless engine, on a fake engine).
- `tests/test_contract_toolcalls.py` (the engine, CPU): the engine's parser passes `toolcalls/`, every form has a file, and the runner catches a changed answer.
- `tests/test_stage_contract.py` and `tests/test_split_estimate.py` (the engine, CPU): both stages' real `/health` answers validate against `health.schema.json`, and the split blocks the engine computes (splittable and each not-splittable reason) against `setup/split.schema.json`.
- `tests/test_engine_api_live.py` (CPU torch): `server/app.py --fake-engine` without a built dashboard answers `/health` with `engine_api` and every live GET route with 200, each read with a schema here validating; every `/metrics` family it names is in `metrics-families.json` with its type; and nothing under `/dashboard` is served.

## Changes

- v1.8 (ENG-427, ENG-413 WP14): the page, the Chat tab forward and `--backends` left the engine (`answers`: `no_ui` and `not_on_engine` on every engine; `/v1/chat-app*` out of `planned`, the sign-in still planned); the engine-only screens' old paths and the leaving entries of `app_routes_via_engine` but the sign-in out of `routes.json`; `relay_only` on `POST /v1/embeddings` and `/v1/responses`; `dashboard/` session, error and gap; `reverse-deps.json` retired; `compat/1.7/`. 1.0 to 1.7 engines stay valid.
- v1.5 (ENG-433, ENG-413 WP5): group 9, the engine-only screens as `/v1/engine/*` (14 routes, `engine/` schemas and examples), out of `planned`; the screens' old paths marked to leave with WP14 and answered over the same handlers; the `off_why` copies gone from TandemApp; `compat/1.4/`. Additive: 1.0 to 1.4 engines stay valid.
- v1.4 (ENG-429, ENG-413 WP3a): `/health` `extensions`; the answers 404 `no_ui` and `not_on_engine` (`answers`, out of `planned`); `qse_ext_errors_total` in `metrics-families.json`; `compat/1.3/`. Additive: 1.0 to 1.3 engines stay valid.
- v1.3 (ENG-427): the folder moves to the engine's root and TandemApp pins a copy (`PIN`); `dashboard/` (the engine's reads, from TandemApp's `dashboard-v1`), `decision/systemone.schema.json`, `metrics-families.json` and `metrics.md`, `toolcalls/`, `auth.json`, `cache.json`, `off_why` on the deciders and shadow screens; `after_eng413` on the app routes and the screens, `leaves` on `GET /v1/dashboard/metrics`; `planned`; `compat/1.2/`. No answer changes: 1.0, 1.1 and 1.2 engines stay valid.
- v1.2: the optional `split` ask and block of `POST /v1/setup/estimate`, the optional `stage` block of `/health` and the tail's `degraded` status, the error types `invalid_split`, `stage_down`, `stage_text_only`, `stage_front`, and `node/control.json` `stage` (SRV-817). Additive: 1.0 and 1.1 engines stay valid.
- v1.1: `GET /v1/replica/identity` (SRV-741). TandemApp reads it through TandemNode's relay for a Replicas member whose engine says 1.1 or later, and sends the cache routes and `/v1/tokenize` to a Replicas member (SRV-741, no change to the engine API).
- v1.0, first version (SRV-747).
