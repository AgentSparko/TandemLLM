# Engine extension interface v1

How code that is not the engine's runs inside a TandemLLM engine process: TandemApp's conductor in chat mode (the router's plan, the session monitor in the decode loop, the state and narrated lines, the journal). The engine owns this interface; its version is `2.1`, independent of the engine API (`../engine-api-v1`) and of either release. It is a Python interface, not HTTP: the conductor acts on tokens inside the decode loop, which a second HTTP request cannot reproduce token for token.

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
- The host takes the same MAJOR and a MINOR at or below its own: an extension written against 2.1 does not start on a 2.0 host, one written against 2.0 starts on a 2.1 host. A 1.x extension does not start on a 2.x host: one line `[ext] REFUSING: the extension speaks engine-ext-v1 1.x; this engine speaks 2.1`.
- Since 2.1 the module may also have `read_document(ext_dir, name)` (one of its documents before it starts: the engine reads the residency document's memory limits before the weights load, the residency planner's command line its documents) and `before_load(args)` (asked once the module is found, before the weights load: the session monitor's policy is checked there; a raise refuses the start with one line, its own `SystemExit` passes as it is; its answer is a line for the log).
- The engine imports no TandemApp module; the extension imports no engine module. The extension gets engine objects handed in through `host`.

## The host

`ext_api`, `engine_api`, `mode` (engine, chat, setup), `state`, `lock`, `tokenizer`, `metrics` (a registry handle: `add(family)` for the families `../engine-api-v1/metrics-families.json` lists with owner `ext`), `ledger_path` (the extension owns its own tables in the file: `conductor_events` and its version row), `args` (the parsed command line, and `QSE_EXT_ARGS`, a JSON object, over it: its keys win), `engine` (the engine-only screens in process, the handlers behind `/v1/engine/*`; since 2.1 `residency_plan()`, `disk()`, `disk_clear()`, `deciders(method, rest, body[, base])`, `narrator(method, what, body[, base])`, `shadow(method, what, body[, base])`, each answering `(status, body)` with the body `/v1/engine/*` answers, or raising the host's `Refusal(status, type, message)`; `base` is the path an error message names), `log(line)` and `Refusal`. Since 2.0 also `ext_dir` (the directory the engine was given for the extension's documents, `--conductor-config`, or None), `identity_name()` (the served model's identity name), `own_eta(texts, max_new)` (the engine's estimate for a question of the extension; the attribute is None when the engine keeps none), `own_idle()` (seconds since a client request last decoded), `NoRoom` (what the engine's callbacks raise when a question asked in place has no room) and `metrics.event(row)` (one journal row of the extension counted in its `ext` families). Since 2.1 also `shipped_guard`, the path of the speed-guard report the engine's release tree ships.

## The extension

Attributes, checked once after `start`:

- `routes`: `[{method, prefix, handler}]`, the paths it serves (`method` `*` for any; a prefix ending in `/` matches the paths below it, any other the path itself and below it; `handler(request)` returns `(status, headers, body)`, `request` has `method`, `path`, `query`, `headers`, `body`, `peer`, `auth`, and since 2.0 `allowed(route)` (None, or the engine's refusal `(status, headers, body)` for that access rule), `session_token` and `session` (the session address `/s/<token>` the path came under, and the session key it names)) (`/v1/conductor/*`, `/v1/library*`, `/v1/laya*`, `/v1/memory*`, `/s/*` today; `../engine-api-v1/routes.json` `app_routes_via_engine`). A prefix the engine serves itself is refused at start.
- `models`: what `/v1/models` lists in place of the engine's own model (the `TandemLLM` alias). The host reads it at every `/v1/models` answer, so it may follow the extension's own documents; `/v1/models/{id}` answers the entry of that id, or the first entry for the engine's own model name (a hidden alias).
- `knobs`: the `QWEN38_*` names it reads (`knobs.json`).

Hooks:

| hook | when | returns |
|-|-|-|
| `health()` | each full `/health` answer | a dict merged under `loaded` |
| `plan(request)` | before the request queues, without the engine lock; `request` has `body`, `headers`, `client_kind`, `request_id`, `chat`, `user`, `count_tokens` | a plan, or None to serve the request as a headless engine would |
| `prompt(plan, ids, info)` | the prompt's token ids before prefill; since 1.2 `info` is `{in_think, conv}` (the prompt ends inside `<think>`; the conversation's id) (since 2.0 every extension is asked with `info`) | the ids to prefill (the router's notes replayed); since 1.2 also `{ids, in_think, client_ids}`: whether the prompt still ends inside `<think>`, and the prompt as the client sent it (None when nothing was put in) |
| `supervisor(plan, event)` | once the prompt is built | the object the decode loop asks, or None |
| `extras(plan, point, info)` | `start` (it may come before `prompt`: the lines a request shows while it waits), each `block`, `finish` | `[{kind, data}]` the engine puts into the stream (state lines, narrated lines) |
| `follow_ups(plan, outcome)` | the turn's end | own-work requests `{kind, what, body}` the engine schedules after the client's |
| `finish(plan, row)` | the request's ledger row is written | None; the extension writes its journal rows |
| `stop()` | the engine stops | None |

A plan's fields the engine reads: `active` (false hands every step its input back), `body` (the body served in place of the client's, or None) and since 2.0 `routing`: the extension's routing of the request (None for a plan that only supervises). `ext.json` `request.routing_fields` names what the engine reads and writes of it (`row` and its `extra`, `side`, `session.key`, `res.mode/profile/doc`, `headers()`, `think_budget(b)`, ...).

Since 2.0 the request path also asks the extension's optional **calls** (`ext.json` `extension.calls`, 19): each a method of the extension object with its arguments, its answer and the value a headless engine uses (`server_mode`, `served_name`, `side_first_max`, `strip_states`, `loop_escape`, `code_class`, `served`, `in_context`, `settle_context`, `ask_in_place`, `route_lines`, `memory_filter`, `memory_spans`, `answer_mask`, `replayed`, `turn_ended`, `writer_ban_ids`, `writer_foreign_call`, `probe_stage`). A missing call answers its headless value; a raising one too, logged and counted under its name.

Since 2.1 the engine reads nothing of the extension's objects outside these: the calls add what it read outside a request (`document(name)`, one of `ext.json` `extension.documents`: `residency`, `roles`, `deciders`, `settings`, `router`, `packages`; `library_text(name)`; `shadow_wanted()`; `monitor_mode()`), the small models it builds around its own (`narrator(slot)`, the narrator over the engine's slot with the methods `ext.json` `narrator` names; `role_errors(fields, name, tokenizer_sha)`, the role contract; `deciders(registry)`, the extension's client of the engine's decision models; `deciders_api(doc)`, the Deciders screen's api part) and the memory writer (`memory_writer()`: the writer and its settings; the engine decodes the writer's hidden turn and asks the writer `state`, `due`, `mark`, `gate(conv)`, `instructions`, `apply`, `skipped` and its `stats`, `ext.json` `memory_writer`). `ext.json` `state_keys` says what the engine reads of the keys the extension writes into `host.state` (`conductor` and `monitor`: only whether they are set; `journal`: `close()` at the drain). `extras start` may answer an item of kind `lines`: the request's lines object, whose fields `ext.json` `extras.lines_fields` names.

The supervisor, at the safe points, with the event types and the acts the engine defines and carries out:

| method | safe point | acts it may return |
|-|-|-|
| `at_request(event)` | SP4, the request | a list of `PROMPT_TAIL_NOTE`, `SET_THINK_BUDGET` |
| `at_block(event, room)` | SP1, after each block | `CLOSE_REASONING`, `GIVE_UP` |
| `at_tool_call(event)` | SP3, a tool call | `REPAIR_CALL`, `REGENERATE_CALL`, `GIVE_UP` |
| `check_answer(items, final)` (1.2) | SP3, an answer's held calls at once (`final`: it ended without its end token); a supervisor without it is not asked | `REGENERATE_CALL`, `GIVE_UP` |
| `at_finish(event, room)` | SP2, the end of the generation | `CONTINUE_HIDDEN`, `GIVE_UP`; since 1.2 `REGENERATE_CALL` (an answer with calls ending on its end token is checked there, where the calls can still be written again) |
| `done(outcome, detail)` | the request is over | None |

Since 2.0 the supervisor may also have the optional methods `ext.json` `supervisor.optional_methods` names (`apply_request`, `route_budget`, `settle_claim`, `without_notes`, `as_sent`, `release_call`, `release_block`, `note_repairs`, `argloop_stopped`), each asked open with its headless value, and the fields `supervisor.fields` names (`mode`, `policy.version`, `req`, `holds_calls`, `gave_up`, `cuts`, ...), which the decode loop reads (and some it writes); a supervisor that keeps one answers the field's headless value.

An act is `{kind, task, text, ids, visible, budget, detail}`, or since 1.2 an object with those fields as attributes: `ids` are forwarded through the engine, `visible` (a suffix of `ids`) is what the client receives. The events' fields are `ext.json` `supervisor.events`; events are mappings whose fields read as attributes too. Since 1.2 the request event adds `prompt_ids`, `conv`, `chat`, `tool_names`, `thinking_off` and `loop_escape`.

## The order

Start: import, check `EXT_API`, `start(host)`, read `routes`, `models`, `knobs`. Each request: `plan`, `prompt`, `supervisor`, `at_request`, `extras start`, then per block `at_block` and `extras block`, `at_tool_call` per call, `at_finish`, `extras finish`, `follow_ups`, `finish`, `done`. `CONTINUE_HIDDEN` loops back to the blocks and asks `at_finish` again; `REGENERATE_CALL` decodes the call again and asks `at_tool_call` again; `GIVE_UP` ends the turn. A plan of None asks no further hook. Shutdown: `stop`.

## The failure rule (decided 2026-11-13, ENG-429)

**Closed at start, open per request.**

- Start: `QSE_EXT` names a module that does not import, has no `EXT_API` or one the host does not accept, lacks a hook, raises in `start`, or claims an engine path; or chat mode is asked with `QSE_EXT` empty. The engine does not start: it prints one line `[ext] REFUSING: <why>`. The module's own checks (it imports, its attributes, `EXT_API`, chat mode without `QSE_EXT`, a `QSE_EXT_ARGS` that is not a JSON object) refuse before the weights load; `start(host)` runs once the served model is loaded, since the host hands it the tokenizer and the engine, so a start that raises, a missing hook or a claimed path refuses after the load and before the engine takes its first request. It never falls back to headless when an extension was asked for. Since 2.1 two more refusals: the session monitor asked (`--conductor shadow|on`) of a module without `before_load` (nothing would check and hand over its policy), before the load; and an extension below 2.1 that routes requests (it sets `state['conductor']`), after its start: a 2.1 host reads such an extension's documents and memory writer through the 2.1 calls only, so it would lose them without an error. A 2.0 extension that does neither starts as before.
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

- v2.1 (ENG-429, ENG-413 WP5): additive. The engine-only screens are the engine's `/v1/engine/*` (engine API 1.5) and `host.engine` hands their handlers over with the signatures above; `host.shipped_guard`; the module's optional `read_document` and `before_load`; the calls `document`, `library_text`, `shadow_wanted`, `monitor_mode`, `narrator`, `role_errors`, `deciders`, `deciders_api` and `memory_writer`, with `extension.documents`, `memory_writer`, `narrator` and `state_keys`. The 2.0 interim `writer()` call is gone (`memory_writer` answers the writer itself). `kept_interim` names one thing, until WP14: the conductor and the monitor's policy handed to the `--backends` proxy, which is TandemApp's own code in the engine's process and leaves it then. A 2.0 extension that neither routes requests nor runs the session monitor starts on a 2.1 host and answers each new call's headless value; one that does is refused at start (the failure rule), as 2.0 refused a 1.x extension that would lose its routing.

### What changed from 1.x (2.0, ENG-413 WP4)

A 1.x extension that answered WP3b's interim plan field `cond` would load on a host that reads `routing` and lose its routing without an error: other tokens, no journal. The closed start exists to prevent that, so the interface is a new major and the host refuses every 1.x extension at start.

- `cond` → `routing`: the plan's routing is the field `routing` (`request.routing_fields`); `cond` is not read.
- `host.interim` removed: `ext_dir`, `identity_name`, `own_eta`, `own_idle`, `NoRoom` and `metrics.event` take its place.
- Optional methods with fallbacks: the extension's calls (`extension.calls`), the supervisor's optional methods and its fields each answer a headless value when the extension has none; a missing optional supervisor method no longer detaches the supervisor.
- `prompt(plan, ids, info)` for every extension (1.0 and 1.1's two-argument form is gone).
- A route handler answers `(status, headers, body)` only.

- v2.0 (ENG-429, ENG-413 WP4, the extension moves into TandemApp; above): what an extension still read or answered outside the interface until 1.2 is the interface: the host's `ext_dir`, `identity_name`, `own_eta`, `own_idle`, `NoRoom` and `metrics.event`; the plan's `routing` and its fields; the extension's optional calls; the lines object of `extras start`; the supervisor's optional methods and fields; a route request's `allowed`, `session_token` and `session`. Every addition is optional or has a headless value; the major step is for `routing` and the closed start (above). `ext.json` `kept_interim` names what stays outside until ENG-413 WP5, with the reason: the memory writer's object (`writer`) and the extension's objects the engine reads in `host.state` outside a request.
- v1.2 (ENG-429, ENG-413 WP3b, the request path through the hooks): `prompt` gets `info` (`{in_think, conv}`) and may answer `{ids, in_think, client_ids}`; the supervisor's `check_answer(items, final)` for an answer's held calls at once (SP3); `at_finish` may answer `REGENERATE_CALL`; an act may be an object with the act's fields as attributes; the request event's `prompt_ids`, `conv`, `chat`, `tool_names`, `thinking_off`, `loop_escape`; `extras start` may come before `prompt`. A 1.0 or 1.1 extension starts as before: its `prompt` is asked with two arguments, a supervisor without `check_answer` is not asked it. Outside the interface, `ext.json` `since_1_2.interim` names what the engine's request path still asks the interim adapter until ENG-413 WP4.
- v1.1 (ENG-429, ENG-413 WP3a, the engine's host): says what the first host does where 1.0 was silent. `QSE_EXT_ARGS` is a JSON object over the command line; `models` is read at every `/v1/models` answer and its first entry answers for the engine's own model name; a route's prefix match and its `request`; the refusals of `start(host)` come after the weights load (the host hands over the tokenizer), the module's own before it; `qse_ext_errors_total`'s `hook` values. No hook, attribute or meaning a 1.0 extension relies on changes: a 1.0 extension starts on a 1.1 host.
- v1.0, first version (ENG-429): discovery, host, hooks, supervisor, order, the failure rule, the knobs.
