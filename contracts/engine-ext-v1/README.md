# Engine extension interface v1

How code that is not the engine's runs inside a TandemLLM engine process: TandemApp's conductor in chat mode (the router's plan, the session monitor in the decode loop, the state and narrated lines, the journal). The engine owns this interface; its version is `3.2`, independent of the engine API (`../engine-api-v1`) and of either release. It is a Python interface, not HTTP: the conductor acts on tokens inside the decode loop, which a second HTTP request cannot reproduce token for token.

| file | what |
|-|-|
| `ext.json` | the interface: discovery, the host's attributes, the extension's attributes and hooks with their arguments, the supervisor's methods, events, safe points and acts, the call order, the failure rule |
| `knobs.json` | the `QWEN38_*` knobs the extension reads, with their defaults |
| `fake_host.py` | the reference host: discovery, start and one request through the hooks, with the failure rule as code |
| `fake_ext.py` | the smallest extension: every hook, steered by the host's `args` into each failure case and, since 3.2, into SET_CONSTRAINT |
| `compat/3.1/` | `ext.json` of 3.1 as it was, which 3.2 still has to hold (since 3.2) |

Both Python files use the standard library only and import neither tree. Each side loads them from this folder by path in its tests.

## Discovery

- `QSE_EXT=<module>` names the extension; empty or unset is a headless engine, which serves its own routes only.
- The module has `EXT_API` (`MAJOR.MINOR`, the version it was written against), `NAME`, `VERSION` and `start(host)`, which returns the extension object.
- The host takes the same MAJOR and a MINOR at or below its own: an extension written against 3.1 does not start on a 3.0 host, one written against 3.0 starts on a 3.1 host. A 1.x or 2.x extension does not start on a 3.x host: one line `[ext] REFUSING: the extension speaks engine-ext-v1 2.0; this engine speaks 3.2`, before the weights load.
- Since 3.0 the module may also have `read_document(ext_dir, name)` (one of its documents before it starts: the engine reads the residency document's memory limits before the weights load, the residency planner's command line its documents) and `before_load(args)` (asked once the module is found, before the weights load: the session monitor's policy is checked there; a raise refuses the start with one line, its own `SystemExit` passes as it is; its answer is a line for the log).
- The engine imports no TandemApp module; the extension imports no engine module. The extension gets engine objects handed in through `host`.

## The host

`ext_api`, `engine_api`, `mode` (engine, chat, setup), `state`, `lock`, `tokenizer`, `metrics` (a registry handle: `add(family)` for the families `../engine-api-v1/metrics-families.json` lists with owner `ext`), `ledger_path` (the extension owns its own tables in the file: `conductor_events` and its version row), `args` (the parsed command line, and `QSE_EXT_ARGS`, a JSON object, over it: its keys win), `engine` (the engine-only screens in process, the handlers behind `/v1/engine/*`; since 3.0 `residency_plan()`, `disk()`, `disk_clear()`, `deciders(method, rest, body[, base])`, `narrator(method, what, body[, base])`, `shadow(method, what, body[, base])`, each answering `(status, body)` with the body `/v1/engine/*` answers, or raising the host's `Refusal(status, type, message)`; `base` is the path an error message names), `log(line)` and `Refusal`. Since 2.0 also `ext_dir` (the directory the engine was given for the extension's documents, `--conductor-config`, or None), `identity_name()` (the served model's identity name), `own_eta(texts, max_new)` (the engine's estimate for a question of the extension; the attribute is None when the engine keeps none), `own_idle()` (seconds since a client request last decoded), `NoRoom` (what the engine's callbacks raise when a question asked in place has no room) and `metrics.event(row)` (one journal row of the extension counted in its `ext` families). Since 3.0 also `shipped_guard`, the path of the speed-guard report the engine's release tree ships.

## The extension

Attributes, checked once after `start`:

- `routes`: `[{method, prefix, handler}]`, the paths it serves (`method` `*` for any; a prefix ending in `/` matches the paths below it, any other the path itself and below it; `handler(request)` returns `(status, headers, body)`, `request` has `method`, `path`, `query`, `headers`, `body`, `peer`, `auth`, and since 2.0 `allowed(route)` (None, or the engine's refusal `(status, headers, body)` for that access rule), `session_token` and `session` (the session address `/s/<token>` the path came under, and the session key it names)) (`/v1/conductor/*`, `/v1/library*`, `/v1/laya*`, `/v1/memory*` today, each also under a session address `/s/<token>`, which the engine strips; `../engine-api-v1/routes.json` `app_routes_via_engine`). A prefix the engine serves itself is refused at start. Since 3.1 a route may name `OPTIONS`: the engine asks it for the preflight of a path it dispatches to the extension (Laya's), never a `*` route, and an answer with status 204 goes out without a `Content-Type`.
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

Since 3.0 the engine reads nothing of the extension's objects outside these: the calls add what it read outside a request (`document(name)`, one of `ext.json` `extension.documents`: `residency`, `roles`, `deciders`, `settings`, `router`, `packages`; `library_text(name)`; `shadow_wanted()`; `monitor_mode()`), the small models it builds around its own (`narrator(slot)`, the narrator over the engine's slot with the methods `ext.json` `narrator` names; `role_errors(fields, name, tokenizer_sha)`, the role contract; `deciders(registry)`, the extension's client of the engine's decision models; `deciders_api(doc)`, the Deciders screen's api part) and the memory writer (`memory_writer()`: the writer and its settings; the engine decodes the writer's hidden turn and asks the writer `state`, `due`, `mark`, `gate(conv)`, `instructions`, `apply`, `skipped` and its `stats`, `ext.json` `memory_writer`). `ext.json` `state_keys` says what the engine reads of the keys the extension writes into `host.state` (`conductor` and `monitor`: only whether they are set; `journal`: `close()` at the drain), and since 3.1 the two keys that hold the engine's own objects the extension uses: `escapes` (the loop escape's counts, `step` and `report`) and `session_tokens` (the session addresses' registry: `token_for`, `key_for`, `note`, `calls`, `report`). Since 3.1 also `shadow_question(routing, prompt_ids)`: the router's class question for the shadow reader at this turn (`{at, question, choices, router, request_id, row_id}`, `{skip: why}` or None), which the reader asks over its own state. `extras start` may answer an item of kind `lines`: the request's lines object, whose fields `ext.json` `extras.lines_fields` names.

The supervisor, at the safe points, with the event types and the acts the engine defines and carries out:

| method | safe point | acts it may return |
|-|-|-|
| `at_request(event)` | SP4, the request | a list of `PROMPT_TAIL_NOTE`, `SET_THINK_BUDGET`; since 3.2 `SET_CONSTRAINT` |
| `at_block(event, room)` | SP1, after each block | `CLOSE_REASONING`, `GIVE_UP` |
| `at_tool_call(event)` | SP3, a tool call | `REPAIR_CALL`, `REGENERATE_CALL`, `GIVE_UP` |
| `check_answer(items, final)` (1.2) | SP3, an answer's held calls at once (`final`: it ended without its end token); a supervisor without it is not asked | `REGENERATE_CALL`, `GIVE_UP` |
| `at_finish(event, room)` | SP2, the end of the generation | `CONTINUE_HIDDEN`, `GIVE_UP`; since 1.2 `REGENERATE_CALL` (an answer with calls ending on its end token is checked there, where the calls can still be written again) |
| `done(outcome, detail)` | the request is over | None |

Since 2.0 the supervisor may also have the optional methods `ext.json` `supervisor.optional_methods` names (`apply_request`, `route_budget`, `settle_claim`, `without_notes`, `as_sent`, `release_call`, `release_block`, `note_repairs`, `argloop_stopped`), each asked open with its headless value, and the fields `supervisor.fields` names (`mode`, `policy.version`, `req`, `holds_calls`, `gave_up`, `cuts`, ...), which the decode loop reads (and some it writes); a supervisor that keeps one answers the field's headless value.

An act is `{kind, task, text, ids, visible, budget, detail}`, or since 1.2 an object with those fields as attributes: `ids` are forwarded through the engine, `visible` (a suffix of `ids`) is what the client receives. Since 3.2 an act may also have `constraint` (SET_CONSTRAINT's, below). The events' fields are `ext.json` `supervisor.events`; events are mappings whose fields read as attributes too. Since 1.2 the request event adds `prompt_ids`, `conv`, `chat`, `tool_names`, `thinking_off` and `loop_escape`.

### SET_CONSTRAINT (since 3.2)

A hook steers the form of the answer without a per-token callback: such a callback would sit in the speculative decode path and cost speed and bit-equality. Instead `at_request` may answer `SET_CONSTRAINT`, and the engine carries it out with its own grammar, the code that serves the request fields (`server/compat.py` `act_constraint`). The act's `constraint` is `{kind, value, scope, strict}` (`ext.json` `supervisor.act.constraint`):

| kind | value | the same as the request field |
|-|-|-|
| `regex` | a string in the engine's regex dialect (`engine/grammar.py`), the whole answer matched | `structured_outputs: {"regex": value}` |
| `choice` | a non-empty list of strings | `structured_outputs: {"choice": value}` |
| `json_schema` | a JSON schema of the subset the grammar compiles | `structured_outputs: {"json": value}` |
| `logit_bias` | `{token id: number in [-100, 100]}`, ids in the served vocabulary | `logit_bias: value` |

- `scope`: `answer` (the default) constrains the answer after the reasoning, as structured outputs do; with tools offered under `tool_choice` auto that is the calls or the document, as for the field. `tool_args` (kind `choice` only) makes the answer calls to the functions `value` names, their arguments typed by the offered schemas: the forced calls of `tool_choice` (one name: that function; every offered one: `required`), without the sentence `tool_choice` puts in the system turn, since SP4 comes after the prompt is built. `logit_bias` takes no scope: like the field, it acts on every token.
- `strict`: false (the default) leaves the request unconstrained when the engine does not carry the constraint out; true answers the client 400 `invalid_request_error`, param `SET_CONSTRAINT`, with the reason.
- The same automaton, masks, speculative rows and response-cache key as the request field: a draft row the constraint forbids is masked exactly as for the field, so speculation writes what plain constrained decoding writes, and the act's answer is the field's answer token for token. A request with no SET_CONSTRAINT is served as before.
- Precedence: the request's own constraint wins. A `response_format` `json_object` or `json_schema`, `structured_outputs`, or `tool_choice` `required` or a named function keeps the grammar kinds out; the request's own `logit_bias` keeps the act's out; `tool_choice` `none` keeps `tool_args` out. The act gives way and is refused, uncounted. One grammar and one logit bias a request: a second act of a family is refused. An engine started with `--no-structured-outputs` refuses the grammar kinds.
- Limits: a regex of 8,192 characters, 256 choices of 1,024 characters each, a schema of 65,536 bytes as JSON, 300 bias ids, and the automaton's own 20,000 states. SP4 is asked with the engine's turn taken, so the act's automaton is compiled there (a client's is compiled before the queue); the grammar cache keeps it for the next request that sends the same one.
- `apply_request` gets the other acts only; a list of SET_CONSTRAINT alone splices nothing.
- What became of each act, `{kind, scope, strict, applied, why}`, goes to the supervisor's optional `constraint_outcomes(outcomes)`, to the routing row's `extra.set_constraint` (a plan with routing, which the extension's `finish` journals) and to one log line each.

## The order

Start: import, check `EXT_API`, `start(host)`, read `routes`, `models`, `knobs`. Each request: `plan`, `prompt`, `supervisor`, `at_request`, `extras start`, then per block `at_block` and `extras block`, `at_tool_call` per call, `at_finish`, `extras finish`, `follow_ups`, `finish`, `done`. `CONTINUE_HIDDEN` loops back to the blocks and asks `at_finish` again; `REGENERATE_CALL` decodes the call again and asks `at_tool_call` again; `GIVE_UP` ends the turn. A plan of None asks no further hook. Shutdown: `stop`.

## The failure rule (decided 2026-11-13, ENG-429)

**Closed at start, open per request.**

- Start: `QSE_EXT` names a module that does not import, has no `EXT_API` or one the host does not accept, lacks a hook, raises in `start`, or claims an engine path; or chat mode is asked with `QSE_EXT` empty. The engine does not start: it prints one line `[ext] REFUSING: <why>`. The module's own checks (it imports, its attributes, `EXT_API`, chat mode without `QSE_EXT`, a `QSE_EXT_ARGS` that is not a JSON object) refuse before the weights load; `start(host)` runs once the served model is loaded, since the host hands it the tokenizer and the engine, so a start that raises, a missing hook or a claimed path refuses after the load and before the engine takes its first request. It never falls back to headless when an extension was asked for. Since 3.0 one more refusal: the session monitor asked (`--conductor shadow|on`) of a module without `before_load` (nothing would check and hand over its policy), before the load.
- A request: a hook that raises or returns a value of the wrong shape is logged and counted (`qse_ext_errors_total{hook}`: the hook's or the supervisor method's name, and `models`, `route`, `stop`), and the rest of that request is served as a headless engine would serve it: a failed `plan` serves the request raw, a failed `prompt` keeps the ids as built, a failed supervisor method detaches the supervisor for the rest of the request, failed `extras` add nothing, failed `follow_ups` and `finish` are skipped. Acts already carried out stay. The next request asks the extension again.
- `plan` may raise the host's `Refusal(status, type, message)`: the client gets that error. That is an answer, not a failure.
- An act the safe point does not allow is dropped and counted as an error of that method.
- Since 3.2: a SET_CONSTRAINT whose `constraint` has the wrong shape (missing, a kind or scope `ext.json` does not list, a `strict` that is not a bool) is a wrong shape: the supervisor is detached, as above. One of the right shape that the engine does not carry out (a regex that does not compile, a schema outside the subset or over a limit, a token id outside the vocabulary, a bias out of bounds, a scope its kind does not take) is refused and counted as an error of `at_request`; one that gives way to the request's own constraint is refused uncounted. Either way the request runs without it, the other acts of the list are carried out, the supervisor stays attached, and the outcome says why. A strict act's refusal is the client's 400 instead. A constraint that leaves no legal token while decoding ends the request as the field's would (a 400, or the stream's error), naming `SET_CONSTRAINT`.
- A handler of the extension's own route that raises answers 500 `extension_error`, counted as `route`: there is no headless answer for a path only the extension serves.

Why: an engine asked for chat mode that came up without its extension would serve different tokens and write no journal without anyone being told, so a refused start with one line is the better failure. Once running, the engine's own answer is always a valid answer and the engine already serves a request raw when the conductor raises (`server/app.py`, the plan's call: "a conductor bug serves the request as raw"), so an extension bug costs that request its conductor, never the engine's availability.

## Versioning

MAJOR.MINOR. A minor step adds a hook, an argument with a default, a host attribute, an event field, a plan field or an act kind. A major step removes or renames one, or changes a meaning or the failure rule.

Until ENG-413's release (decided 2026-11-14) every contract change is a minor step, even one a mismatched pair would feel: the extension is tested with the engine its `PIN` names (TandemApp's `PIN`; the engine vendored TandemApp until WP14), so an engine and an extension from different steps never meet. The release step then sets one clean major and freezes it; from there the rule above holds for every change.

## Tests

- SET_CONSTRAINT (`tests/test_set_constraint.py`): 3.2 holds every part of `compat/3.1/ext.json`; the reference host and the engine agree on each act's outcome; the act is the request field it is the same as (the answer's automaton, and on the CPU model the same tokens with no drafter, a chain and a tree); no act, no change; an invalid constraint runs the request unconstrained with its outcome; precedence; a strict refusal; `tool_args`.
- The engine (`tests/test_engine_ext_contract.py`): `ext.json` is well formed; `fake_ext.py` has every hook with its arguments; `fake_host.py` drives the order of `ext.json` (a plain request, each act loop); every start failure is refused with one line, every request failure is open; the Python files here import only the standard library; `knobs.json` matches `engine/settings.py` `KNOBS`. The engine's host (`server/ext.py`, ENG-413 WP3a) is held to the same cases by `tests/test_engine_ext_host.py`: `fake_ext.py` through the real loader, each refusal line on a fake engine, the per-request rule and `qse_ext_errors_total`.
- TandemApp (`tests/test_engine_ext_contract.py`): `knobs.json` is `tandemapp/knobs.py` `DEFAULTS`; the fake host and the fake extension run in its tree. Its extension (WP4) runs against `fake_host.py`.

## Changes

- v3.2 (ENG-458): the act `SET_CONSTRAINT` at SP4 (regex, choice, JSON schema, logit bias; scope `answer` or `tool_args`; `strict`), carried out by the engine's grammar as the request field it is the same as; the act field `constraint`; the supervisor's optional `constraint_outcomes`; the failure rule's `constraint` line; `compat/3.1/ext.json`. Additive: a 3.1 extension starts on a 3.2 host and is served as before, since it answers no SET_CONSTRAINT and its `apply_request` gets the acts it got.
- v3.1 (ENG-429, ENG-413 WP5b): the call `shadow_question` (the router's question for the shadow reader; the engine keeps the question's form, `server/api/incontext.py`); Laya (`/v1/laya*`) is the extension's route over the engine's decision models (`deciders(registry)`), and a route may name `OPTIONS` for its preflight (a 204 answer has no `Content-Type`); `state_keys` names `session_tokens` and the methods of `escapes`, the engine's own objects (`server/api/session_tokens.py`, `server/api/loopescape.py`). Additive: a 3.0 extension starts on a 3.1 host. What a 3.0 extension does not have there: Laya (the engine serves no Laya of its own any more: 404 `not_on_engine`) and the shadow reader's router question (not asked).

### What changed from 2.x (3.0, ENG-413 WP5)

A 2.x extension that routes requests relied on the engine reading its objects (its documents, its memory writer) and writing the monitor's policy into the state. A 3.0 host does neither: such an extension would run with the built-in documents, no memory writer and no monitor, other tokens, without an error. So the interface is a new major and the host refuses every 2.x (and 1.x) extension at start, one line, before the weights load (`ext.json` `changed_from_2`).

- The interim `writer()` is gone: `memory_writer()` answers the writer (`gate(conv)`).
- The engine reads no attribute of `state['conductor']` or `state['monitor']` outside the hooks: `document`, `library_text`, `shadow_wanted`, `monitor_mode`, `memory_writer` answer; `state_keys` says what is left.
- The module checks the monitor's policy (`before_load`); the engine no longer writes `state['conductor_policy']`.
- `host.engine`'s handlers take no conductor (the `/v1/engine/*` handlers, engine API 1.5).
- The engine builds the narrator's slot and asks `narrator(slot)`; the role contract is `role_errors`, the decision models' client `deciders(registry)`.

- v3.0 (ENG-429, ENG-413 WP5): the engine-only screens are the engine's `/v1/engine/*` (engine API 1.5) and `host.engine` hands their handlers over with the signatures above; `host.shipped_guard`; the module's optional `read_document` and `before_load`; the calls `document`, `library_text`, `shadow_wanted`, `monitor_mode`, `narrator`, `role_errors`, `deciders`, `deciders_api` and `memory_writer`, with `extension.documents`, `memory_writer`, `narrator` and `state_keys`. `kept_interim` names one thing, until WP14: the conductor and the monitor's policy handed to the `--backends` proxy. The major step: above.

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
