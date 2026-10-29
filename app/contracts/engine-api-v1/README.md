# Engine API contract v1

These files are the truth for what TandemApp (`app/`: the dashboard, the controller, the conductor, chatd and TandemNode) may use of a TandemLLM engine (`engine/`, `server/`): the HTTP routes, the extensions to the OpenAI shapes, and how TandemNode controls an install without importing it. The engine's tests hold its code to them (`tests/test_engine_api_contract.py`, `tests/test_engine_api_live.py`); TandemApp's tests hold its callers to them (`app/tests/test_engine_api_contract.py`). Its version is `1.0`, independent of either release.

| file | what |
|-|-|
| `routes.json` | every route by group with its TandemApp callers (`used_by`, or `consumer` when no TandemApp file calls it), its shape, and `live`: a GET an engine without a model (`--fake-engine`) answers 200; the engine-only screens until ENG-413; TandemApp's routes the engine serves in its process until ENG-413; the controller's own families |
| `health.schema.json`, `health.example.json` | `GET /health`: the open, full, setup-mode and status responder answers |
| `setup/estimate-cases.json` | `models/estimate-cases.json` byte for byte: the Setup Guide's estimate formula pinned by cases |
| `setup/limits.json` | `engine_max_slots`, the registry's `ENGINE_MAX_SLOTS` |
| `node/serve.env.example` | the `PORT=` and `QSE_STATUS_PORT=` lines install.sh writes to `run/ops/serve.env` from `ops/serve.env` |
| `node/hub_manifest.example.json` | the `_hub_manifest.json` `ops/fetch_weights.py` writes beside a pack (here each file is `size` zero bytes) |
| `node/control.json` | bin/tandem's verbs and the install.sh flags TandemNode and the controller use |
| `reverse-deps.json` | what the engine's tree still takes from TandemApp's: the engine->app imports, the engine test files that reach app code, the reads across the boundary, and the copies both keep equal |

## The version

- `engine_api` (MAJOR.MINOR, `server/engine_api.py` `VERSION`) is in the full `/health` answer, the setup-mode `/health` and the status responder's `/health`. The open answer, which a caller through a proxy gets, stays `{status}`.
- MINOR is additive: a new route or a new optional field. MAJOR is a removed or renamed route or field, or a changed meaning.
- An engine without the field speaks 1.0: every engine up to 0.15.x.
- TandemApp's `proxy/engineapi.py` (`SPEAKS` 1.0) reads it for an engine of kind `tandemllm`. Another major is not ok: "this engine speaks engine API 2.0; TandemApp 0.15.0 needs 1.x". TandemNode's engine view then says `unhealthy` with that text in `health.api_why`; a deployment member on that engine is `down` with it and takes no request, not as a fallback and not for a model no record names (a derived Single's or a routing rule's: 503 `no_member`); a deployment's start fails at once with it (`start_failed`).
- Not read here yet: chatd's own engine (SRV-735) and backends the controller reaches directly (SRV-749).

## Routes

### 1. OpenAI data plane

| route | TandemApp's use | shape |
|-|-|-|
| `POST /v1/chat/completions` | `proxy/service.py`, `tandemapp/conductor/classify.py`, `chatd/engine.py`, `chatd/agent.py`, `chatd/service.py`, `dashboard/src/api/client.ts`, `dashboard/src/views/playground.ts` | OpenAI chat completions; the extensions below |
| `POST /v1/completions` | `proxy/service.py` | OpenAI completions |
| `POST /v1/embeddings` | `proxy/service.py`, `proxy/relay.py` | OpenAI embeddings |
| `POST /v1/responses` | `proxy/service.py` | OpenAI responses |
| `GET /v1/models` | `proxy/nodeengines.py`, `proxy/nodesource.py`, `tandemapp/conductor/classify.py`, `chatd/engine.py`, `dashboard/src/api/fleet.ts` | OpenAI model list; each model's `tandem` card, and `decision_models` |
| `GET /v1/models/{id}` | `proxy/service.py`, `tandemapp/conductor/classify.py` | one model's card |

### 2. Health and metrics

The full answer goes to a loopback caller without proxy headers (TandemNode on its box) or a caller with the token: `status_port`, `version`, `model`, `base_model`, `weights.name`, `uptime_s`, `max_len`, `default_max_tokens`, `memory`, `parallel`, `engine_api`. `status` is `ok`, `busy`, `draining` (503) or `failed` (500, a GPU context that no longer answers). The setup-mode answer: `mode` setup, `status_port`, `assistant`, `engine_api`. `/metrics` is the `qse_*` families of `tandemapp/METRICS.md`.

| route | TandemApp's use | shape |
|-|-|-|
| `GET /health`, `/healthz`, `/v1/health` | `proxy/nodeengines.py`, `chatd/engine.py`, `dashboard/src/app.ts`, `dashboard/src/setup/api.ts`, `dashboard/src/setup/install.ts` | `health.schema.json` |
| `GET /metrics` | `proxy/nodeengines.py`, `proxy/nodesource.py`, `dashboard/src/views/performance.ts` | Prometheus text, the qse_* families (tandemapp/METRICS.md) |
| `GET /metrics/up` | a Prometheus scrape (the engine's ops/monitoring); no TandemApp file | one number for a scraper: the engine answers |

### 3. Engine dashboard reads

| route | TandemApp's use | shape |
|-|-|-|
| `GET /v1/dashboard/live` | `proxy/nodeengines.py`, `proxy/nodesource.py`, `chatd/engine.py`, `dashboard/src/api/client.ts`, `dashboard/src/views/live-panel.ts` | `../dashboard-v1/live.schema.json` |
| `GET /v1/dashboard/system` | `proxy/nodeengines.py`, `proxy/nodesource.py`, `chatd/engine.py`, `dashboard/src/api/client.ts` | `../dashboard-v1/system.schema.json` |
| `GET /v1/dashboard/logs` | `proxy/nodeengines.py`, `proxy/nodesource.py`, `proxy/nodeagent.py`, `dashboard/src/api/client.ts` | `../dashboard-v1/logs-json.schema.json` |
| `GET /v1/dashboard/parallel` | `proxy/relay.py`, `tandemapp/parallel.py`, `dashboard/src/api/client.ts`, `dashboard/src/views/parallel.ts` | `../dashboard-v1/parallel.schema.json` |
| `GET /v1/dashboard/metrics` | `dashboard/src/api/client.ts` | the /metrics text under the dashboard's access rule |

### 4. Ledger reads

| route | TandemApp's use | shape |
|-|-|-|
| `GET /v1/dashboard/summary` | `dashboard/src/api/client.ts` | `../dashboard-v1/summary.schema.json` |
| `GET /v1/dashboard/usage` | `dashboard/src/api/client.ts` | `../dashboard-v1/usage.schema.json` |
| `GET /v1/dashboard/requests` | `dashboard/src/api/client.ts`, `tools/tandem_eval.py` | `../dashboard-v1/requests.schema.json` |

### 5. Tokenize and cache

| route | TandemApp's use | shape |
|-|-|-|
| `POST /v1/tokenize` | `chatd/agent.py` | the exact prompt tokens of a request (the model's own template) |
| `POST /v1/cache/peek` | `chatd/service.py` | where a prompt's prefix is cached (tier per tandemapp/usage.py TIER_OF) |
| `POST /v1/cache/prewarm` | `chatd/service.py` | load a prompt's prefix ahead of its request |
| `POST /v1/cache/release` | `chatd/engine.py` | the engine gives back memory it holds |
| `POST /v1/cache/clear` | `tools/tandem_eval.py` | the prefix store emptied (the admin bearer) |

### 6. Setup Guide 1.0

The dashboard builds these from its base `/v1/setup` (or a node's `/v1/fleet/nodes/{id}/setup`, which TandemNode relays); `proxy/fleetsetup.py` and `proxy/nodeagent.py` relay `/v1/setup/<sub>` as it is (`relay:/v1/setup/`). The field shapes are the engine's `docs/setup.md`.

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
| `POST /v1/systemone`, `/v1/predict` | `tandemapp/conductor/classify.py` | `../conductor-v1/systemone.schema.json` |
| `GET /readyz` | `tandemapp/conductor/classify.py` | 200 once the engine's decision models are ready |

`nodeengines.RELAY` (TandemNode's engine relay, GET only) is `/health`, `/v1/models`, `/v1/dashboard/live`, `/v1/dashboard/system`, `/v1/dashboard/logs` and `/metrics`, all in the table above.

## Extensions

- Request headers: `X-Tandem` (`raw` or `shadow`; nothing on a headless engine), `X-Tandem-Profile`, `X-Tandem-Relay-Id`, `X-Tandem-User`, `X-QSE-Client`, `x-session-id`, `X-Tandem-Cost`, `X-Tandem-Parent-Session`, `X-Tandem-Worker`, `X-Tandem-Memory`.
- Response fields: `usage.prompt_tokens_details.cached_tokens`, `usage.completion_tokens_details.reasoning_tokens`, `timings`, `tandem`.
- `Retry-After` on 429 and 503.
- Error types beside OpenAI's: `engine_failed` (the GPU context failed) and `setup_mode` (a setup-mode server serves no model).
- `GET /v1/dashboard/parallel`: `lanes[].relay_id` echoes the request's `X-Tandem-Relay-Id`.

## TandemNode's control (not HTTP)

- A loopback caller without proxy headers is trusted and gets the full `/health`.
- `run/ops/serve.env` carries `PORT=` (`node/serve.env.example`); `nodeengines.default_engine` reads it.
- `bin/tandem start|stop|restart`; exit 0 is done (`node/control.json`).
- `install.sh --dir --ref --runtime-only --yes`. The controller holds the script and its ref: `TANDEM_ENGINE_INSTALL_SH` and `Package.engine_ref()` (`proxy/nodeinstall.py`).
- A pack's `<weights dir>/_hub_manifest.json` (`node/hub_manifest.example.json`); `proxy/nodeweights.py` reads its sizes and sha256 for a content id.
- The agent never imports engine code and never runs `python -m server.*`. `app/tests/test_import_boundary.py` enforces this.

## TandemApp's routes the engine serves until ENG-413

The engine runs TandemApp's conductor, ledger and dashboard API in its own process until ENG-413 (the headless engine). The dashboard reaches these behind the engine's address on the LAN, and chatd answers through the engine port. They are TandemApp's, under their own contracts, not part of this API:

| path | what | contract |
|-|-|-|
| `/v1/conductor/*` except the engine-only screens | TandemApp's conductor, run in the engine's process | `../conductor-v1/` |
| `/v1/dashboard/session` | the dashboard's sign-in | `../dashboard-v1/session.schema.json` |
| `/v1/config` | the proxy's backends and rules (--backends) | - |
| `/v1/config/*` | the proxy's backends and rules (--backends) | - |
| `/v1/collect/*` | the host collector (--backends) | - |
| `/v1/chat-app` | chatd, the Chat tab's service, through the engine port (tandemapp/chat_forward.py) | - |
| `/v1/chat-app/*` | chatd, the Chat tab's service, through the engine port (tandemapp/chat_forward.py) | - |
| `/v1/library*` | the package library (library.schema.json) | `../conductor-v1/` |
| `/v1/laya*` | Laya (laya-ask.schema.json) | `../conductor-v1/` |
| `/v1/memory` | the model's own memory, under its session's address | - |
| `/v1/memory/*` | the model's own memory, under its session's address | - |
| `/s/{token}/*` | a session address: /v1/library, /v1/laya and /v1/memory under the session's token (tandemapp/session_tokens.py) | - |
| `/dashboard/*` | the dashboard's built files (the not-built page without them) | - |

The controller's own families, never the engine's:

| path | what | contract |
|-|-|-|
| `/v1/fleet/*` | the controller's fleet API; never the engine | `../fleet-v1` |

`shared_prefixes` names a base the dashboard joins with sub-paths that fall in more than one list: `/v1/dashboard` (the ledger reads and the sign-in).

## The engine-only screens (until ENG-413)

These `/v1/conductor/*` screens read or load the engine's own models and memory. The engine answers them through `server/conductor/inproc.py` `ROUTES`, which it puts in the conductor's state, the narrator it starts and its shadow reader. TandemApp's controller (tandem-app) runs no engine and answers them as the last column says (`../conductor-v1/README.md`).

| route | the engine's handler | tandem-app |
|-|-|-|
| `GET /v1/conductor/residency/plan` | inproc.ROUTES[residency_plan] | 404 not_found, routes.ENGINE_ONLY |
| `GET /v1/conductor/memory/disk` | inproc.ROUTES[disk] | 404 not_found, routes.ENGINE_ONLY |
| `POST /v1/conductor/memory/disk/clear` | inproc.ROUTES[disk] | 404 not_found, routes.ENGINE_ONLY |
| `* /v1/conductor/deciders/*` | inproc.ROUTES[deciders] | status: 200, enabled false, why routes.DECIDERS_OFF_WHY; the rest 404 not_found with that text |
| `* /v1/conductor/narrator/*` | the narrator inproc.start_narrator starts (state narrator) | status: 200, the narrator off; the rest 404 not_found (narrator.OFF_WHY) |
| `* /v1/conductor/shadow/*` | the shadow reader server/conductor/shadow.py start() runs (state shadow) | status: 200, enabled false, why routes.SHADOW_OFF_WHY; the rest 404 not_found with that text |

## ENG-413: what the engine still takes from TandemApp

Until the headless engine, a finder in `server/__init__.py` serves only `tandemapp`, `proxy` and `chatd` from `app/` to every entry point (server/app.py, `python -m server.setup.*`, the tools and the tests; `tests/test_app_hook.py` holds this), and these engine modules import TandemApp's (`reverse-deps.json` `imports` has each imported name):

| engine file | app modules |
|-|-|
| `server/app.py` | `proxy.collect_api`, `proxy.config`, `proxy.config_api`, `proxy.hostcollect`, `proxy.route`, `proxy.service`, `tandemapp.activity`, `tandemapp.argloop`, `tandemapp.auth`, `tandemapp.callrepair`, `tandemapp.chat_forward`, `tandemapp.compaction`, `tandemapp.conductor.classify`, `tandemapp.conductor.decisions`, `tandemapp.conductor.hook`, `tandemapp.conductor.incontext`, `tandemapp.conductor.loopescape`, `tandemapp.conductor.memory`, `tandemapp.conductor.memwriter`, `tandemapp.conductor.migrations`, `tandemapp.conductor.modelfit`, `tandemapp.conductor.monitor`, `tandemapp.conductor.monitor.policy`, `tandemapp.conductor.narrator`, `tandemapp.conductor.outcomes`, `tandemapp.conductor.redact`, `tandemapp.conductor.router`, `tandemapp.conductor.routes`, `tandemapp.conductor.schema`, `tandemapp.conductor.shipped`, `tandemapp.conductor.signals`, `tandemapp.conductor.states`, `tandemapp.conductor.store`, `tandemapp.conductor.voice`, `tandemapp.dashboard_api`, `tandemapp.laya_api`, `tandemapp.ledger`, `tandemapp.library_api`, `tandemapp.live`, `tandemapp.logbuf`, `tandemapp.memblock`, `tandemapp.metrics`, `tandemapp.parallel`, `tandemapp.session_tokens`, `tandemapp.static`, `tandemapp.thinking`, `tandemapp.toolcall`, `tandemapp.toolformats`, `tandemapp.usage` |
| `server/compat.py` | `tandemapp.refusal`, `tandemapp.toolformats` |
| `server/conductor/inproc.py` | `tandemapp.conductor.narrator`, `tandemapp.conductor.routes`, `tandemapp.conductor.schema` |
| `server/conductor/residency.py` | `tandemapp.conductor.store` |
| `server/conductor/shadow.py` | `tandemapp.conductor.incontext`, `tandemapp.conductor.inject`, `tandemapp.conductor.schema` |
| `server/decider_api.py` | `tandemapp.conductor.classify`, `tandemapp.conductor.schema`, `tandemapp.laya_api` |
| `server/diskcache.py` | `tandemapp.conductor.transcripts` |
| `server/endalign.py` | `tandemapp.toolcall` |
| `server/memory_api.py` | `tandemapp.conductor.notes`, `tandemapp.library_api` |
| `server/probe.py` | `tandemapp.conductor.transcripts`, `tandemapp.ledger` |
| `server/rolemem.py` | `tandemapp.conductor.schema` |
| `server/setup/apply.py` | `tandemapp.conductor.redact` |
| `server/setup/community.py` | `tandemapp.conductor.schema` |
| `server/setup/knowledge.py` | `tandemapp.conductor.libsearch` |
| `server/setup/registry.py` | `tandemapp.conductor.schema` |
| `server/setup/wren.py` | `tandemapp.conductor.schema`, `tandemapp.toolcall` |
| `server/setup_api.py` | `tandemapp.dashboard_api` |
| `tools/argloop_fp.py` | `tandemapp.argloop` |
| `tools/class_core_eval.py` | `tandemapp.conductor.classify`, `tandemapp.conductor.router`, `tandemapp.conductor.schema`, `tandemapp.conductor.signals` |
| `tools/class_fallback_eval.py` | `tandemapp.conductor.classify`, `tandemapp.conductor.router`, `tandemapp.conductor.signals` |
| `tools/conductor_contract.py` | `tandemapp.conductor.schema` |
| `tools/conductor_eval.py` | `tandemapp.conductor.classify`, `tandemapp.conductor.schema`, `tandemapp.conductor.signals`, `tandemapp.conductor.store` |
| `tools/ledger_migrate_check.py` | `tandemapp.ledger` |
| `tools/ledger_synth.py` | `tandemapp.ledger` |
| `tools/loop_words_fp.py` | `tandemapp.conductor.monitor.tier0_words` |
| `tools/memory_measure.py` | `tandemapp.compaction`, `tandemapp.conductor.notes` |
| `tools/narrator_reference.py` | `tandemapp.conductor.narrator`, `tandemapp.conductor.schema`, `tandemapp.conductor.states` |
| `tools/prefill_reuse_e2e.py` | `chatd.render` |
| `tools/shadow_ab.py` | `tandemapp.conductor.schema`, `tandemapp.conductor.store` |
| `tools/shadow_measure.py` | `tandemapp.conductor.incontext`, `tandemapp.conductor.narrator`, `tandemapp.conductor.schema` |
| `tools/shadow_router_eval.py` | `tandemapp.conductor.incontext`, `tandemapp.conductor.schema` |
| `tools/stall_replay.py` | `tandemapp.conductor.monitor.policy` |
| `tools/thought_narrator_measure.py` | `tandemapp.conductor.narrator`, `tandemapp.conductor.schema`, `tandemapp.conductor.states`, `tandemapp.conductor.store` |
| `tools/voice_replay.py` | `tandemapp.conductor.narrator`, `tandemapp.conductor.states`, `tandemapp.conductor.voice` |

`reverse-deps.json` also lists the engine's reads of app files (`reads`: the knob rows, the manual's generators, the setup knowledge index, the contracts), the engine test files that reach app code (`tests`), the reads the AST cannot see (`not_ast`: install.sh, ops/start.sh and ops/watchdog.sh starting chatd and TandemNode from `app/`, the models' docs links, the Grafana panels, the manual's tables) and the copies both trees keep equal until the split (`copies`: `same_origin`, `TIER_OF`, the `OFF_WHY` texts, the test helpers, `contract_check`, the licenses, the setup fixtures). At the split the engine takes byte copies of what it reads here, its own `/metrics` document and registry, and its own helpers; the app-only tools and tests move to `app/`.

## Tests

- `app/tests/test_engine_api_contract.py` (TandemApp, standard library): routes.json is well formed and its version is `proxy.engineapi.SPEAKS`; every engine path literal in the Python callers and in `dashboard/src` falls in exactly one list (a `${...}` is `{param}`, the query is dropped, a base matches as a prefix, all of it when text follows a `{param}`); every route is used by a file or a relay; `nodeengines.RELAY` is in the routes; the version cases, `nodeengines.health` against fake engines, the health example against its schema, and the node fixtures read by `nodeengines.default_engine` and `nodeweights`.
- `app/tests/test_node_agent.py` and `app/tests/test_deployments.py`: an engine on engine API 2.0 is `unhealthy` and names 1.x, its member is `down` and takes no request, a start fails within seconds; 1.4 and a missing field work.
- `tests/test_engine_api_contract.py` (the engine, no torch): every route is dispatched, the version is one, `reverse-deps.json` is the AST inventory, the copies hold equal, the fixtures match the engine's files.
- `tests/test_engine_api_live.py` (CPU torch): `server/app.py --fake-engine` without a built dashboard answers `/health` with `engine_api` and every live GET route with 200; once more from a copy of the tree without `app/dashboard`.

## Changes

- v1.0, first version (SRV-747).
