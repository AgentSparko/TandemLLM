# Engine control contract v1

`tandem remote` is the engine's control CLI: `bin/tandem remote VERB ...`, run by hand or as the forced command of a controller's SSH key. These files are its contract; the engine owns them. Its version is `1.1`, independent of the release and of the engine API (`../engine-api-v1`).

| file | what |
|-|-|
| `verbs.json` | every verb with its options and arguments (`server/remote/grammar.py` `VERBS`), the exit statuses, the error codes, the journal's fields, the files the CLI keeps and the `authorized_keys` line |
| `status.schema.json` | `status` |
| `job.schema.json` | `start`, `stop`, `restart`, `mode set`, `settings apply`, `settings restore`: the job they started |
| `mode-get.schema.json` | `mode get` |
| `settings-get.schema.json`, `settings-plan.schema.json`, `settings-versions.schema.json` | `settings get`, `plan`, `versions` |
| `logs.schema.json`, `logs-line.schema.json` | `logs`, and each line of `logs --follow` |
| `doctor.schema.json` | `doctor`: one code and one fix a finding |
| `unpair.schema.json` | `unpair` |
| `ext-list.schema.json`, `ext-install.schema.json`, `ext-remove.schema.json` | `ext list`, `install`, `remove` (1.1) |
| `upgrade-plan.schema.json` | `upgrade plan`; `upgrade apply` answers `job.schema.json` with the plan under `upgrade` (1.1) |
| `token-rotate.schema.json` | `token rotate` (1.1) |
| `error.schema.json` | every refusal and error: `{ok: false, error: {code, message}}` |

`tests/test_remote_contract.py` holds the CLI's answers to these schemas and `verbs.json` to the grammar.

## The rules

- One command line in: `SSH_ORIGINAL_COMMAND` under a forced command, else the arguments. It is split with `shlex` and never passed to a shell. A token is printable ASCII without a space.
- One JSON document out on stdout, `{ctl, version, verb, ok, ...}`, and one exit status (`verbs.json` `exit`). `logs --follow` writes one document a line until the client goes.
- Every call is journaled, refused ones too: `run/remote/journal.jsonl`.
- A session with a terminal, a forwarded agent or X11 display, or no command (a shell), is refused before a verb runs.
- A path value lies under the install or the engine's state directory (`~/.qwen38-spark-engine`), never under `~/.ssh`.
- `settings apply` and `settings restore` take the plan's `confirm` token; a change of a key that feeds the disk cache's fingerprint takes `confirm_fingerprint` as well. A token is bound to the overlay version the plan read, so a plan of another version does not apply.
- One lifecycle job acts at a time. A new one cancels a job that still waits; while one acts, a call answers `busy` (exit 4).
- Since 1.1, two verbs read stdin: `ext install` (a wheel) and `upgrade plan --sha256` (an archive of the tree). Each checks the bytes' sha256 against `--sha256` before it writes anything, and the journal line keeps the sha256 it got (`checked`), a refused call's too.
- `upgrade apply` takes the plan's `confirm` token, bound to the tree's commit and the target, and its job rolls back to the previous tree when the install or the start fails.
- `token rotate` answers the engine's new HTTP token on its channel only; the box keeps its sha256 (`run/http-token`), never the token.

## The version

MINOR adds a verb, an option or an optional field; MAJOR removes or renames one, or changes a meaning. `verbs.json` `planned` names the verbs of later minors (`models`, `pair`); until then they are refused as `unknown_verb`. 1.1 (ENG-454, ENG-456) adds `ext`, `upgrade` and `token`, the exit status 1 for an install that failed, `status`'s optional `http`, `job`'s kind `upgrade` with `upgrade` and `rolled_back`, and the type `cidrs` of `settings get`; every 1.0 answer is still valid.
