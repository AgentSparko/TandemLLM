# Engine extension interface v1

How code that is not the engine's runs inside a TandemLLM engine process: TandemApp's conductor in chat mode (the router's plan, the session monitor in the decode loop, the state and narrated lines, the journal). The engine owns this interface; its version is `1.1`, independent of the engine API (`../engine-api-v1`) and of either release. It is a Python interface, not HTTP: the conductor acts on tokens inside the decode loop, which a second HTTP request cannot reproduce token for token.

| file | what |
|-|-|
| `ext.json` | the interface: discovery, the host's attributes, the extension's attributes and hooks with their arguments, the supervisor's methods, events, safe points and acts, the call order, the failure rule |
| `knobs.json` | the `QWEN38_*` knobs the extension reads, with their defaults |
| `fake_host.py` | the reference host: discovery, start and one request through the hooks, with the failure rule as code |
| `fake_ext.py` | the smallest extension: every hook, steered by the host's `args` into each failure case |

Both Python files use the standard library only and import neither tree. Each side loads them from this folder by path in its tests.

## Discovery

- `QSE_EXT=<module>` names the extension; empty or unset is a headless engine, which serves its own routes only.
- The module has `EXT_API` (`MAJOR.MINOR`, the version it was written against), `NAME`, `VERSION` and `start(host)`, which returns the extension object.
- The host takes the same MAJOR and a MINOR at or below its own: an extension written against 1.2 does not start on a 1.0 host, one written against 1.0 starts on a 1.2 host.
- The engine imports no TandemApp module; the extension imports no engine module. The extension gets engine objects handed in through `host`.

## The host

`ext_api`, `engine_api`, `mode` (engine, chat, setup), `state`, `lock`, `tokenizer`, `metrics` (a registry handle: `add(family)` for the families `../engine-api-v1/metrics-families.json` lists with owner `ext`), `ledger_path` (the extension owns its own tables in the file: `conductor_events` and its version row), `args` (the parsed command line, and `QSE_EXT_ARGS`, a JSON object, over it: its keys win), `engine` (the engine-only screens in process: `residency_plan`, `disk`, `disk_clear`, `deciders`, `narrator`, `shadow`, the handlers behind `/v1/engine/*`), `log(line)` and `Refusal`.

## The extension

Attributes, checked once after `start`:

- `routes`: `[{method, prefix, handler}]`, the paths it serves (`method` `*` for any; a prefix ending in `/` matches the paths below it, any other the path itself and below it; `handler(request)` returns `(status, headers, body)`, `request` has `method`, `path`, `query`, `headers`, `body`, `peer`, `auth`) (`/v1/conductor/*`, `/v1/library*`, `/v1/laya*`, `/v1/memory*`, `/s/*` today; `../engine-api-v1/routes.json` `app_routes_via_engine`). A prefix the engine serves itself is refused at start.
- `models`: what `/v1/models` lists in place of the engine's own model (the `TandemLLM` alias). The host reads it at every `/v1/models` answer, so it may follow the extension's own documents; `/v1/models/{id}` answers the entry of that id, or the first entry for the engine's own model name (a hidden alias).
- `knobs`: the `QWEN38_*` names it reads (`knobs.json`).

Hooks:

| hook | when | returns |
|-|-|-|
| `health()` | each full `/health` answer | a dict merged under `loaded` |
| `plan(request)` | before the request queues, without the engine lock; `request` has `body`, `headers`, `client_kind`, `request_id`, `chat`, `user`, `count_tokens` | a plan, or None to serve the request as a headless engine would |
| `prompt(plan, ids)` | the prompt's token ids before prefill | the ids to prefill (the router's notes replayed) |
| `supervisor(plan, event)` | once the prompt is built | the object the decode loop asks, or None |
| `extras(plan, point, info)` | `start`, each `block`, `finish` | `[{kind, data}]` the engine puts into the stream (state lines, narrated lines) |
| `follow_ups(plan, outcome)` | the turn's end | own-work requests `{kind, what, body}` the engine schedules after the client's |
| `finish(plan, row)` | the request's ledger row is written | None; the extension writes its journal rows |
| `stop()` | the engine stops | None |

A plan's fields the engine reads: `active` (false hands every step its input back) and `body` (the body served in place of the client's, or None). The request path's refactor (ENG-413 WP3b) adds the fields it needs in minor steps.

The supervisor, at the safe points, with the event types and the acts the engine defines and carries out:

| method | safe point | acts it may return |
|-|-|-|
| `at_request(event)` | SP4, the request | a list of `PROMPT_TAIL_NOTE`, `SET_THINK_BUDGET` |
| `at_block(event, room)` | SP1, after each block | `CLOSE_REASONING`, `GIVE_UP` |
| `at_tool_call(event)` | SP3, a tool call | `REPAIR_CALL`, `REGENERATE_CALL`, `GIVE_UP` |
| `at_finish(event, room)` | SP2, the end of the generation | `CONTINUE_HIDDEN`, `GIVE_UP` |
| `done(outcome, detail)` | the request is over | None |

An act is `{kind, task, text, ids, visible, budget, detail}`: `ids` are forwarded through the engine, `visible` (a suffix of `ids`) is what the client receives. The events' fields are `ext.json` `supervisor.events`.

## The order

Start: import, check `EXT_API`, `start(host)`, read `routes`, `models`, `knobs`. Each request: `plan`, `prompt`, `supervisor`, `at_request`, `extras start`, then per block `at_block` and `extras block`, `at_tool_call` per call, `at_finish`, `extras finish`, `follow_ups`, `finish`, `done`. `CONTINUE_HIDDEN` loops back to the blocks and asks `at_finish` again; `REGENERATE_CALL` decodes the call again and asks `at_tool_call` again; `GIVE_UP` ends the turn. A plan of None asks no further hook. Shutdown: `stop`.

## The failure rule (decided 2026-11-13, ENG-429)

**Closed at start, open per request.**

- Start: `QSE_EXT` names a module that does not import, has no `EXT_API` or one the host does not accept, lacks a hook, raises in `start`, or claims an engine path; or chat mode is asked with `QSE_EXT` empty. The engine does not start: it prints one line `[ext] REFUSING: <why>`. The module's own checks (it imports, its attributes, `EXT_API`, chat mode without `QSE_EXT`, a `QSE_EXT_ARGS` that is not a JSON object) refuse before the weights load; `start(host)` runs once the served model is loaded, since the host hands it the tokenizer and the engine, so a start that raises, a missing hook or a claimed path refuses after the load and before the engine takes its first request. It never falls back to headless when an extension was asked for.
- A request: a hook that raises or returns a value of the wrong shape is logged and counted (`qse_ext_errors_total{hook}`: the hook's or the supervisor method's name, and `models`, `route`, `stop`), and the rest of that request is served as a headless engine would serve it: a failed `plan` serves the request raw, a failed `prompt` keeps the ids as built, a failed supervisor method detaches the supervisor for the rest of the request, failed `extras` add nothing, failed `follow_ups` and `finish` are skipped. Acts already carried out stay. The next request asks the extension again.
- `plan` may raise the host's `Refusal(status, type, message)`: the client gets that error. That is an answer, not a failure.
- An act the safe point does not allow is dropped and counted as an error of that method.
- A handler of the extension's own route that raises answers 500 `extension_error`, counted as `route`: there is no headless answer for a path only the extension serves.

Why: an engine asked for chat mode that came up without its extension would serve different tokens and write no journal without anyone being told, so a refused start with one line is the better failure. Once running, the engine's own answer is always a valid answer and the engine already serves a request raw when the conductor raises (`server/app.py`, the plan's call: "a conductor bug serves the request as raw"), so an extension bug costs that request its conductor, never the engine's availability.

## Versioning

MAJOR.MINOR. A minor step adds a hook, an argument with a default, a host attribute, an event field, a plan field or an act kind. A major step removes or renames one, or changes a meaning or the failure rule.

## Tests

- The engine (`tests/test_engine_ext_contract.py`): `ext.json` is well formed; `fake_ext.py` has every hook with its arguments; `fake_host.py` drives the order of `ext.json` (a plain request, each act loop); every start failure is refused with one line, every request failure is open; the Python files here import only the standard library; `knobs.json` matches `engine/settings.py` `KNOBS`. The engine's host (`server/ext.py`, ENG-413 WP3a) is held to the same cases by `tests/test_engine_ext_host.py`: `fake_ext.py` through the real loader, each refusal line on a fake engine, the per-request rule and `qse_ext_errors_total`.
- TandemApp (`tests/test_engine_ext_contract.py`): `knobs.json` is `tandemapp/knobs.py` `DEFAULTS`; the fake host and the fake extension run in its tree. Its extension (WP4) runs against `fake_host.py`.

## Changes

- v1.1 (ENG-429, ENG-413 WP3a, the engine's host): says what the first host does where 1.0 was silent. `QSE_EXT_ARGS` is a JSON object over the command line; `models` is read at every `/v1/models` answer and its first entry answers for the engine's own model name; a route's prefix match and its `request`; the refusals of `start(host)` come after the weights load (the host hands over the tokenizer), the module's own before it; `qse_ext_errors_total`'s `hook` values. No hook, attribute or meaning a 1.0 extension relies on changes: a 1.0 extension starts on a 1.1 host.
- v1.0, first version (ENG-429): discovery, host, hooks, supervisor, order, the failure rule, the knobs.
