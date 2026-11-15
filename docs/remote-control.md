# Remote control

`tandem remote` is the engine's control CLI for a controller on another machine. The controller reaches the engine box over SSH with a key whose line in `authorized_keys` runs only `tandem remote`: the key can start, stop and restart the engine, switch its mode, change its settings, install extensions, upgrade the engine, set the HTTP token, read its logs and run its checks, and nothing else. No shell, no terminal, no port forward. No sudo is needed. The contract is [contracts/engine-ctl-v1](../contracts/engine-ctl-v1/README.md); the code is `server/remote/`.

## The key's line

One line in `~/.ssh/authorized_keys` of the user that installed the engine:

    command="/bin/bash /home/u/TandemLLM/bin/tandem remote --key-id ctl-1",no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding ssh-ed25519 AAAA... ctl-1

- `command=` is the forced command: sshd runs it whatever the client asks, and hands the client's command over in `SSH_ORIGINAL_COMMAND`. It goes through `bash`, as every call of the engine's scripts does.
- `--key-id` names the key. `unpair` removes the line that carries it. Without it, `unpair` needs sshd's `ExposeAuthInfo yes`.
- `from="192.0.2.0/24"` before `command=` narrows the addresses the key works from.

A call:

    ssh -i ctl-1 u@engine-box status
    ssh -i ctl-1 u@engine-box 'settings plan MAX_QUEUE=4'

By hand on the box, the same verbs: `bin/tandem remote status`.

## The verbs

| verb | what |
|-|-|
| `status` | the engine's state (healthy, loading, draining, failed, down), its pid, port, version, `engine_api`, the configured and the running mode, the overlay version, the last job; answers with the engine down |
| `start`, `stop` | a job that starts the engine (an install's `bin/tandem start`, else `ops/start.sh`) or stops it (`ops/stop.sh`); a stop stays a stop: `ops/watchdog.sh` leaves the engine down until the next start |
| `restart [--when now\|idle\|at=TIME] [--deadline S]` | stop, then start. `idle` waits until no request runs or waits (the Setup Guide's rule), at most `--deadline` seconds (600); past it the job fails with nothing changed. `at=2026-11-16T03:00:00Z` waits until then, then as idle. The default is now |
| `mode get` | the mode serve.env resolves, its source, and whether it was inferred from an empty `QSE_MODE` |
| `mode set engine\|chat [--ext NAME] [--when ...]` | writes `QSE_MODE` and `QSE_EXT` into the overlay, never an empty mode, and restarts (default `idle`) |
| `settings get [KEY ...]` | every key with its value, its source (default, install, profile, setup, local, managed, mode), its type, its restart class and whether it feeds the disk cache's fingerprint |
| `settings plan KEY=VALUE ... [--unset KEY]` | checks the values, shows what the engine would see after the restart, and gives a confirm token; nothing is written |
| `settings apply KEY=VALUE ... --confirm T [--confirm-fingerprint T] [--when ...]` | the same changes as a new overlay version, made current by a restart job (default `idle`) |
| `settings versions`, `settings restore N --confirm T` | the versions; a restore is a new version with version N's keys |
| `logs [--follow] [--lines N] [--which engine\|launcher]` | the engine's log, or what the jobs' start and stop printed (`logs/launcher.log`); `--follow` streams one JSON line a log line |
| `doctor` | `tandem doctor --check`: each finding with a code and a fix |
| `unpair` | removes the `authorized_keys` line of the key this call came with, and no other |
| `ext list` | the extensions in the engine's venv, each read as the engine reads it at load: its name, version and `EXT_API`, whether this engine takes it, and whether chat mode runs it (1.1) |
| `ext install --sha256 HEX --name FILE.whl [--module M] [--when ...]` | an extension's wheel, on stdin, into the venv (below) (1.1) |
| `ext remove DIST [--and-mode engine] [--when ...]` | the distribution out of the venv; the one chat mode runs only with `--and-mode engine` (1.1) |
| `upgrade plan --ref REF` or `upgrade plan --sha256 HEX` | the engine's tree at another release, from the origin or from an archive on stdin: what changes and a confirm token (1.1) |
| `upgrade apply ... --confirm T [--when ...]` | the upgrade as a job (default `idle`), rolled back when it fails (1.1) |
| `token rotate` | a new HTTP token, answered on this channel only (1.1) |

Every answer is one JSON document with `ok`, and the exit status says the same: 0 ok, 1 not ok (doctor found something, or the venv's installer failed), 2 usage, 3 refused, 4 busy (a job acts), 5 internal. Lifecycle verbs answer at once with their job; `status` follows it. One job acts at a time; a new one replaces a job that still waits.

## The settings overlay

The settings a controller changes live in their own file, `run/managed.env` of the engine's tree, read by `ops/serve.env` after an install's `run/local.env` and before the mode is applied. `ops/serve.env` and the profiles are never edited. Each apply writes a new version, `run/managed.d/<n>.env`; `manifest.json` names the current one and the good one, the last the engine reached `/health` with. A start with a new version that does not reach `/health` puts the good version back, starts the engine again, and the job reports both.

The keys are the ones `ops/serve.env` sets or documents. The install's (paths, the address), the weight profile's (weights, drafters, corpus, the model's flags) and `QSE_BACKENDS` are not the overlay's. A value is letters, digits and `. _ , : = @ % + / -`, with `${HOME}` and `${REPO}` the only expansions; a path lies under the install or `~/.qwen38-spark-engine`.

A key that feeds the disk cache's fingerprint (the `QWEN38_*` arithmetic switches, the weight and drafter arguments, `NARROW_ARM`, `FUSE_PROJ`) changes which saved prefixes and parked chats the engine can read: after the restart they all miss. Its plan says so and gives a second token, and the apply needs both. With an empty overlay, `ops/start.sh` starts the engine with the command line and environment it had before the overlay existed.

An install whose `run/ops/serve.env` was written by an older release does not read the overlay; `settings plan` says so (`hook_missing`) until the installer runs again.

## Extensions

An extension is an engine-ext-v1 module the engine loads in chat mode (`QSE_EXT`). `ext install` takes its wheel on stdin:

    ssh -i ctl-1 u@engine-box "ext install --sha256 $(sha256sum x-1.0-py3-none-any.whl | cut -d' ' -f1) --name x-1.0-py3-none-any.whl" < x-1.0-py3-none-any.whl

- The bytes are refused, with nothing written, when their sha256 is not `--sha256`, when they are more than 256 MiB, or when they are not a wheel whose METADATA names the file's distribution and version.
- The extension module is the one `--module` names, else the one the wheel's entry point group `tandemllm.engine_ext` names, else its one `<package>/engine_ext` module.
- `ops/venv-wheel.sh` installs it into the install's venv, with the engine's pins (`run/requirements.lock`) as constraints, the same way `install.sh --with-chat --app-wheel` installs the chat's wheel. Only an install has a venv: a checkout is refused.
- The answer reads the module back as the engine's extension host does. `run/remote/extensions.json` keeps its distribution, module, wheel and sha256.
- The running engine keeps the code it loaded. The answer says a restart is needed; `--when now|idle|at=TIME` starts the restart job.

`ext remove DIST` uninstalls the distribution. If its module is the extension chat mode runs, the call is refused unless `--and-mode engine` is given; then the overlay gets `QSE_MODE=engine` and an empty `QSE_EXT`, as `mode set engine` writes them, and a restart job (default `idle`) makes them current.

## Upgrades

`upgrade` puts the install's tree at another release. Only an install has one: `DIR/src` is the git checkout `install.sh` made, and it must have no local changes.

    ssh -i ctl-1 u@engine-box 'upgrade plan --ref v0.16.0'
    git archive --format=tar.gz v0.16.0 | ssh -i ctl-1 u@engine-box "upgrade plan --sha256 $SHA"
    ssh -i ctl-1 u@engine-box "upgrade apply --sha256 $SHA --confirm $TOKEN --when idle"

- `--ref` fetches the install's origin and resolves REF as `install.sh` does (a tag, a branch, a commit). An archive comes on stdin, at most 512 MiB, the tree at its root (`git archive` without `--prefix`). Its sha256 must be `--sha256`. Absolute paths, `..`, links that leave the tree and macOS `._` files are refused. The plan keeps it as `run/remote/upgrades/<sha256>.tar` for the apply.
- The target must have `tandem remote` and an `install.sh` that takes `--update` and `--no-fetch`, so the key still reaches the engine afterwards.
- The plan names both trees (commit, version, engine API, this contract's version, the venv's pins), whether the venv is synced to new pins, what is kept and the steps, and gives a token bound to the current commit and the target.
- The job, once it acts:
  1. keeps the previous tree: its commit as `refs/tandem/previous`, and a copy of `run/ops`, `bin/tandem` and its `install.sh`;
  2. stops the engine;
  3. makes an archive a commit of `src`'s repository;
  4. runs the target's `install.sh --update --no-fetch --ref COMMIT`: the checkout, the venv synced to its pins, `run/ops` and `bin/tandem` written again with this install's keys, no weights, no start;
  5. starts the engine.
- A failed install or a start that does not reach `/health` rolls back: the previous commit checked out, the venv synced back with the previous `install.sh --update` when the pins changed, `run/ops` and `bin/tandem` put back byte for byte, the engine started again. The job fails and says `rolled_back`.
- The overlay, `run/local.env`, the weights, the logs and the engine's data are not touched.

## The HTTP token

`token rotate` makes a random token and answers it on this SSH channel, and nowhere else: it is in no log and no journal. The engine box keeps only its sha256, in the tree's `run/http-token` (mode 600). The engine reads the file again within a second, so a rotation needs no restart and the old token stops working.

While the file exists, a caller that is not on the engine's host needs `Authorization: Bearer <token>` on every route, inference included, or it gets 401. A caller on this host (loopback, without `X-Forwarded-For` or `X-Real-IP`) needs none, so the watchdog, the status checks and a controller on the same box keep working. `/health`, `/healthz`, `/v1/health`, `/readyz` and `/metrics/up` answer without it: a probe or a controller can tell a down engine from one that refuses it. Without the token, `/health` gives only its status; with it, the full body follows the admin rule (docs/server.md).

`QSE_ALLOW_FROM` (addresses and CIDRs, comma-separated; empty: every address) answers 403 to any other address on every route, the status included. Loopback always passes. It is a managed setting that changes live: `PATCH /v1/engine/settings` from this host, or with the token, or `settings apply` over SSH, which restarts. Engine API 1.9 has the details (`contracts/engine-api-v1`, "Access").

The status responder (`ops/engine_status.py`), which answers while the engine restarts, takes no token. Set `QSE_STATUS_HOST=127.0.0.1` when nothing outside the host may read it.

## The journal

Every call, refused ones included, is one line of `run/remote/journal.jsonl`: the UTC time, the verb, its arguments (the value of a secret-looking key cut out), the exit status, the duration, the key id and the address it came from. `ext install` and `upgrade` add the sha256 of the bytes they checked (`checked`), and a `PATCH /v1/engine/settings` is a line too (`via` http). Past 4 MiB the file moves to `journal.jsonl.1`, up to three old files.
