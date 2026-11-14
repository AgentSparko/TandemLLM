# Engine extension interface v1

How code that is not the engine's runs inside a TandemLLM engine process: TandemApp's conductor in chat mode (the router's plan, the session monitor in the decode loop, the state and narrated lines, the journal). The engine owns this interface; its version is `1.3`, independent of the engine API (`../engine-api-v1`) and of either release. It is a Python interface, not HTTP: the conductor acts on tokens inside the decode loop, which a second HTTP request cannot reproduce token for token.

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

`ext_api`, `engine_api`, `mode` (engine, chat, setup), `state`, `lock`, `tokenizer`, `metrics` (a registry handle: `add(family)` for the families `../engine-api-v1/metrics-families.json` lists with owner `ext`), `ledger_path` (the extension owns its own tables in the file: `conductor_events` and its version row), `args` (the parsed command line, and `QSE_EXT_ARGS`, a JSON object, over it: its keys win), `engine` (the engine-only screens in process: `residency_plan`, `disk`, `disk_clear`, `deciders`, `narrator`, `shadow`, the handlers behind `/v1/engine/*`), `log(line)` and `Refusal`. Since 1.3 also `ext_dir` (the directory the engine was given for the extension's documents, `--conductor-config`, or None), `identity_name()` (the served model's identity name), `own_eta(texts, max_new)` (the engine's estimate for a question of the extension; the attribute is None when the engine keeps none), `own_idle()` (seconds since a client request last decoded), `NoRoom` (what the engine's callbacks raise when a question asked in place has no room) and `metrics.event(row)` (one journal row of the extension counted in its `ext` families).

## The extension

Attributes, checked once after `start`:

- `routes`: `[{method, prefix, handler}]`, the paths it serves (`method` `*` for any; a prefix ending in `/` matches the paths below it, any other the path itself and below it; `handler(request)` returns `(status, headers, body)`, `request` has `method`, `path`, `query`, `headers`, `body`, `peer`, `auth`, and since 1.3 `allowed(route)` (None, or the engine's refusal `(status, headers, body)` for that access rule), `session_token` and `session` (the session address `/s/<token>` the path came under, and the session key it names)) (`/v1/conductor/*`, `/v1/library*`, `/v1/laya*`, `/v1/memory*`, `/s/*` today; `../engine-api-v1/routes.json` `app_routes_via_engine`). A prefix the engine serves itself is refused at start.
- `models`: what `/v1/models` lists in place of the engine's own model (the `TandemLLM` alias). The host reads it at every `/v1/models` answer, so it may follow the extension's own documents; `/v1/models/{id}` answers the entry of that id, or the first entry for the engine's own model name (a hidden alias).
- `knobs`: the `QWEN38_*` names it reads (`knobs.json`).

Hooks:

| hook | when | returns |
|-|-|-|
| `health()` | each full `/health` answer | a dict merged under `loaded` |
| `plan(request)` | before the request queues, without the engine lock; `request` has `body`, `headers`, `client_kind`, `request_id`, `chat`, `user`, `count_tokens` | a plan, or None to serve the request as a headless engine would |
| `prompt(plan, ids, info)` | the prompt's token ids before prefill; since 1.2 `info` is `{in_think, conv}` (the prompt ends inside `<think>`; the conversation's id) (a host asks an extension before 1.2 with `plan` and `ids` only) | the ids to prefill (the router's notes replayed); since 1.2 also `{ids, in_think, client_ids}`: whether the prompt still ends inside `<think>`, and the prompt as the client sent it (None when nothing was put in) |
| `supervisor(plan, event)` | once the prompt is built | the object the decode loop asks, or None |
| `extras(plan, point, info)` | `start` (it may come before `prompt`: the lines a request shows while it waits), each `block`, `finish` | `[{kind, data}]` the engine puts into the stream (state lines, narrated lines) |
| `follow_ups(plan, outcome)` | the turn's end | own-work requests `{kind, what, body}` the engine schedules after the client's |
| `finish(plan, row)` | the request's ledger row is written | None; the extension writes its journal rows |
| `stop()` | the engine stops | None |

A plan's fields the engine reads: `active` (false hands every step its input back), `body` (the body served in place of the client's, or None) and since 1.3 `routing`: the extension's routing of the request (None for a plan that only supervises). `ext.json` `request.routing_fields` names what the engine reads and writes of it (`row` and its `extra`, `side`, `session.key`, `res.mode/profile/doc`, `headers()`, `think_budget(b)`, ...).

Since 1.3 the request path also asks the extension's optional **calls** (`ext.json` `extension.calls`, 19): each a method of the extension object with its arguments, its answer and the value a headless engine uses (`server_mode`, `served_name`, `side_first_max`, `strip_states`, `loop_escape`, `code_class`, `served`, `in_context`, `settle_context`, `ask_in_place`, `route_lines`, `memory_filter`, `memory_spans`, `answer_mask`, `replayed`, `turn_ended`, `writer_ban_ids`, `writer_foreign_call`, `probe_stage`). A missing call answers its headless value; a raising one too, logged and counted under its name. `extras start` may answer an item of kind `lines`: the request's lines object, whose fields `ext.json` `extras.lines_fields` names.

The supervisor, at the safe points, with the event types and the acts the engine defines and carries out:

| method | safe point | acts it may return |
|-|-|-|
| `at_request(event)` | SP4, the request | a list of `PROMPT_TAIL_NOTE`, `SET_THINK_BUDGET` |
| `at_block(event, room)` | SP1, after each block | `CLOSE_REASONING`, `GIVE_UP` |
| `at_tool_call(event)` | SP3, a tool call | `REPAIR_CALL`, `REGENERATE_CALL`, `GIVE_UP` |
| `check_answer(items, final)` (1.2) | SP3, an answer's held calls at once (`final`: it ended without its end token); a supervisor without it is not asked | `REGENERATE_CALL`, `GIVE_UP` |
| `at_finish(event, room)` | SP2, the end of the generation | `CONTINUE_HIDDEN`, `GIVE_UP`; since 1.2 `REGENERATE_CALL` (an answer with calls ending on its end token is checked there, where the calls can still be written again) |
| `done(outcome, detail)` | the request is over | None |

Since 1.3 the supervisor may also have the optional methods `ext.json` `supervisor.optional_methods` names (`apply_request`, `route_budget`, `settle_claim`, `without_notes`, `as_sent`, `release_call`, `release_block`, `note_repairs`, `argloop_stopped`), each asked open with its headless value, and the fields `supervisor.fields` names (`mode`, `policy.version`, `req`, `holds_calls`, `gave_up`, `cuts`, ...), which the decode loop reads (and some it writes); a supervisor that keeps one answers the field's headless value.

An act is `{kind, task, text, ids, visible, budget, detail}`, or since 1.2 an object with those fields as attributes: `ids` are forwarded through the engine, `visible` (a suffix of `ids`) is what the client receives. The events' fields are `ext.json` `supervisor.events`; events are mappings whose fields read as attributes too. Since 1.2 the request event adds `prompt_ids`, `conv`, `chat`, `tool_names`, `thinking_off` and `loop_escape`.

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

- v1.3 (ENG-429, ENG-413 WP4, the extension moves into TandemApp): what an extension still read or answered outside the interface until 1.2 is the interface: the host's `ext_dir`, `identity_name`, `own_eta`, `own_idle`, `NoRoom` and `metrics.event`; the plan's `routing` and its fields; the extension's optional calls; the lines object of `extras start`; the supervisor's optional methods and fields; a route request's `allowed`, `session_token` and `session`. Every addition is optional or has a headless value, so a 1.0 to 1.2 extension starts and works as before. `ext.json` `kept_interim` names what stays outside until ENG-413 WP5, with the reason: the memory writer's object (`writer`) and the extension's objects the engine reads in `host.state` outside a request.
- v1.2 (ENG-429, ENG-413 WP3b, the request path through the hooks): `prompt` gets `info` (`{in_think, conv}`) and may answer `{ids, in_think, client_ids}`; the supervisor's `check_answer(items, final)` for an answer's held calls at once (SP3); `at_finish` may answer `REGENERATE_CALL`; an act may be an object with the act's fields as attributes; the request event's `prompt_ids`, `conv`, `chat`, `tool_names`, `thinking_off`, `loop_escape`; `extras start` may come before `prompt`. A 1.0 or 1.1 extension starts as before: its `prompt` is asked with two arguments, a supervisor without `check_answer` is not asked it. Outside the interface, `ext.json` `since_1_2.interim` names what the engine's request path still asks the interim adapter until ENG-413 WP4.
- v1.1 (ENG-429, ENG-413 WP3a, the engine's host): says what the first host does where 1.0 was silent. `QSE_EXT_ARGS` is a JSON object over the command line; `models` is read at every `/v1/models` answer and its first entry answers for the engine's own model name; a route's prefix match and its `request`; the refusals of `start(host)` come after the weights load (the host hands over the tokenizer), the module's own before it; `qse_ext_errors_total`'s `hook` values. No hook, attribute or meaning a 1.0 extension relies on changes: a 1.0 extension starts on a 1.1 host.
- v1.0, first version (ENG-429): discovery, host, hooks, supervisor, order, the failure rule, the knobs.
