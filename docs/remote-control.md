# Remote control

`tandem remote` is the engine's control CLI for a controller on another machine. The controller reaches the engine box over SSH with a key whose line in `authorized_keys` runs only `tandem remote`: the key can start, stop and restart the engine, switch its mode, change its settings, read its logs and run its checks, and nothing else. No shell, no terminal, no port forward. No sudo is needed. The contract is [contracts/engine-ctl-v1](../contracts/engine-ctl-v1/README.md); the code is `server/remote/`.

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

Every answer is one JSON document with `ok`, and the exit status says the same: 0 ok, 1 not ok (doctor found something), 2 usage, 3 refused, 4 busy (a job acts), 5 internal. Lifecycle verbs answer at once with their job; `status` follows it. One job acts at a time; a new one replaces a job that still waits.

## The settings overlay

The settings a controller changes live in their own file, `run/managed.env` of the engine's tree, read by `ops/serve.env` after an install's `run/local.env` and before the mode is applied. `ops/serve.env` and the profiles are never edited. Each apply writes a new version, `run/managed.d/<n>.env`; `manifest.json` names the current one and the good one, the last the engine reached `/health` with. A start with a new version that does not reach `/health` puts the good version back, starts the engine again, and the job reports both.

The keys are the ones `ops/serve.env` sets or documents. The install's (paths, the address), the weight profile's (weights, drafters, corpus, the model's flags) and `QSE_BACKENDS` are not the overlay's. A value is letters, digits and `. _ , : = @ % + / -`, with `${HOME}` and `${REPO}` the only expansions; a path lies under the install or `~/.qwen38-spark-engine`.

A key that feeds the disk cache's fingerprint (the `QWEN38_*` arithmetic switches, the weight and drafter arguments, `NARROW_ARM`, `FUSE_PROJ`) changes which saved prefixes and parked chats the engine can read: after the restart they all miss. Its plan says so and gives a second token, and the apply needs both. With an empty overlay, `ops/start.sh` starts the engine with the command line and environment it had before the overlay existed.

An install whose `run/ops/serve.env` was written by an older release does not read the overlay; `settings plan` says so (`hook_missing`) until the installer runs again.

## The journal

Every call, refused ones included, is one line of `run/remote/journal.jsonl`: the UTC time, the verb, its arguments (the value of a secret-looking key cut out), the exit status, the duration, the key id and the address it came from. Past 4 MiB the file moves to `journal.jsonl.1`, up to three old files.
