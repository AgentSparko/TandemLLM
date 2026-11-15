# Engine pairing contract v1

How TandemApp pairs with an engine host by a one-time code, and how it finds the host on the LAN. The engine owns this contract; its version is `1.0`, independent of the release, of the engine API (`../engine-api-v1`, whose group 9 lists the routes since 1.9) and of the control CLI (`../engine-ctl-v1`, whose `key_line` the exchange installs). The code is `server/pairing/`; the user's side is [docs/pairing.md](../../docs/pairing.md).

| file | what |
|-|-|
| `status.schema.json` | `GET /v1/engine/pair` |
| `step1-request.schema.json`, `step1.schema.json` | `POST /v1/engine/pair/1` |
| `step2-request.schema.json`, `step2.schema.json` | `POST /v1/engine/pair/2` |
| `answer.schema.json` | the plaintext of step 2's sealed answer |
| `abandon-request.schema.json`, `abandon.schema.json` | `POST /v1/engine/pair/abandon` |
| `open.schema.json`, `close.schema.json` | `POST /v1/engine/pair/open` and `/close`: `tandem pair`, on the engine host only |
| `error.schema.json` | every refusal: `{error: {type, message}}` |
| `fixtures/spake2.json` | the group and SPAKE2 runs, every intermediate value; the last set uses the node identities of TandemApp's `proxy/pairing.py`, which replays it unchanged |
| `fixtures/exchange.json` | one whole exchange on the wire from a fixed code, x, y, session, nonce and token, with the plaintext and a swapped host key |
| `fixtures/mdns.json` | a browse query for `_tandem._tcp` and the engine's answer, as bytes |

`tools/pair_fixtures.py --check` rebuilds the fixtures from the code; `tests/test_pair_spake2.py` holds the engine's window to them.

## The window

`tandem pair` on the engine host opens a window in the engine process (setup mode or serving) and prints a 6-digit code, the SSH host key's SHA256 fingerprint and the address. A window lasts 5 minutes and allows 5 runs; each step 1 spends a run. The first run that confirms closes it. A new `tandem pair` closes an open window. There is one window at a time.

| route | who | what |
|-|-|-|
| `GET /v1/engine/pair` | anyone the access rule lets in | the window's state, no code |
| `POST /v1/engine/pair/1` | the network while a window is open | step 1 |
| `POST /v1/engine/pair/2` | the same | step 2 |
| `POST /v1/engine/pair/abandon` | the same | a run TandemApp gave up |
| `POST /v1/engine/pair/open`, `/close` | trusted-local with the window key | `tandem pair` |

Access:
- While a window is open, the first four take a caller from the network without the HTTP token. Otherwise they follow the engine's token rule like any route.
- The allow-from list (`QSE_ALLOW_FROM`) applies to all of them.
- A caller that is not trusted-local gets at most 20 requests a minute from its address, and 120 from all addresses together; then 429 `rate_limited` with `Retry-After`.
- `open` and `close` need a trusted-local caller (loopback, no forwarding headers) and the header `X-Tandem-Pair-Key` with `run/remote/pair-local.key` (mode 600, new at every engine start). Otherwise 403 `local_only` or `window_key`.

Every answer carries `Cache-Control: no-store`.

## The exchange

SPAKE2 (RFC 9382's flow, transcript and key confirmation) in the order-q subgroup of RFC 3526's 2048-bit MODP group, exactly as `proxy/pairing.py`: generator 2, M and N hashed from `TandemLLM SPAKE2 M` and `TandemLLM SPAKE2 N`, w = HKDF-SHA256(code, info `TandemLLM pairing w`, 64 bytes) mod q, AAD `TandemLLM pairing v1`. Group elements travel as lower-case hex without leading zeros and are checked on receipt (1 < x < p - 1, x^q = 1).

The identities bind what travels in the clear:
- A = `tandemapp-key <fp> <key id>`: fp is the SHA256 fingerprint (as `ssh-keygen -l` prints it) of TandemApp's ed25519 key.
- B = `tandemengine <fp>`: fp is the fingerprint of the engine's SSH host key in step 1's answer.

TandemApp, with the code the user typed:

1. Make a new ed25519 key pair and a key id (1 to 64 of letters, digits and `. _ : -`, a letter or digit first).
2. x random in [1, q-1]; pA = g^x · M^w. POST `/1` with `{pA, public_key: "ssh-ed25519 AAAA...", key_id, name?}`.
3. The answer: `{session, pB, cB, host_key, fingerprint, tries_left, expires_in}`. Check that the fingerprint of `host_key` is `fingerprint`, and that it is the one the user saw: from `tandem pair`'s output or the advertisement.
4. K = (pB / N^w)^x. (KcA, KcB, TT) = RFC 9382's keys over A, B, pA, pB, K, w. Check cB = HMAC-SHA256(KcB, TT).
   - If it does not check, the code is wrong or something on the way swapped the host key. POST `/abandon` with `{session}`; nothing is trusted.
5. POST `/2` with `{session, cA: HMAC-SHA256(KcA, TT)}`.
6. The answer: `{sealed: {nonce, box, tag}}`. Ke = the first 16 bytes of SHA-256(TT). Open the seal; a tag that does not check means nothing is trusted. The plaintext is `answer.schema.json`:
   - `ssh_user`, `ssh_port`
   - `host_key`, `host_keys`: pin these for the host
   - `http_port`, `http_token`: the bearer for the engine's HTTP routes
   - `key_id`, `engine_version`, `mode`
   - `authorized_keys`: `{added, line}`, the line installed

The engine, once cA checks:
- appends `line` to the installing user's `~/.ssh/authorized_keys`;
- issues the token, keeping only its sha256 in `run/http-token` on the key id's own line;
- closes the window.

The line is `../engine-ctl-v1`'s `key_line`:

    command="/bin/bash DIR/bin/tandem remote --key-id ID",no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding ssh-ed25519 AAAA... ID

Nothing is installed before the confirmation, and nothing at all from a wrong code. A key or a host key swapped on the way changes A or B on one side only, so the confirmations differ. Without the code, an attacker gets one guess per run and 5 runs per code: 5 in 10^6.

**The seal.** HKDF-SHA256(Ke, info `TandemLLM engine pairing seal` + AAD, 64 bytes) gives a stream key (the first 32 bytes) and a MAC key (the last 32).
- The stream is HMAC-SHA256(stream key, nonce ‖ counter) blocks, the counter 4 bytes big-endian from 0.
- box = plaintext XOR stream.
- tag = HMAC-SHA256(MAC key, nonce ‖ box).
- The nonce is 16 random bytes. The plaintext is the answer as compact JSON with sorted keys.

**authorized_keys.**
- `~/.ssh` is created 0700 and the file 0600 when missing. No other line is changed.
- Refused with 409, the window still open and the run spent:
  - a `~/.ssh` or a file that is a symbolic link, is not a directory or regular file, belongs to another user, or is writable by group or others (`ssh_dir_*`, `authorized_keys_*`);
  - a key that already has a line of its own (`key_present`);
  - a key id that another key's line of this install holds (`key_id_taken`).
- The same key and id again is not added twice (`added: false`).

## Errors

| type | status | when |
|-|-|-|
| `bad_request` | 400 | a body that does not read; an unknown or expired session (one run, 60 s) |
| `wrong_code` | 403 | step 2's cA does not check: a failed run; the message says how many are left |
| `no_window` | 404 | no window was opened, or it was closed |
| `code_spent` | 410 | the window expired, its 5 runs are spent, or it was used |
| `rate_limited` | 429 | too many requests from the address, with `Retry-After` |
| `local_only`, `window_key` | 403 | `open` or `close` from the network, or without the window key |
| `no_install`, `no_host_key`, `ssh_dir_*`, `authorized_keys_*` | 409 | `open`: a checkout (no `bin/tandem` to force), no readable SSH host key, an unsafe `~/.ssh` |
| `key_present`, `key_id_taken`, `ssh_dir_*`, `authorized_keys_*` | 409 | step 2: the key could not be installed |
| `token_write` | 500 | step 2: the token file could not be written |

## The journal

Each step is a line of `run/remote/journal.jsonl`, the control CLI's journal, with verb `pair open`, `pair try`, `pair abandoned`, `pair confirm`, `pair paired`, `pair expired` or `pair closed`. It has `via` http or local, the peer's address, the key id, the key's fingerprint and the error type. It never holds the code, the token, a key or a protocol message.

## The advertisement

The engine process answers mDNS (RFC 6762, DNS-SD RFC 6763) for `_tandem._tcp.local` while it listens beyond loopback. Unless `QSE_ADVERTISE=off`, it advertises one instance, named after the host:

| record | value |
|-|-|
| PTR `_services._dns-sd._udp.local` | `_tandem._tcp.local` |
| PTR `_tandem._tcp.local` | `<name>._tandem._tcp.local` |
| SRV | priority 0, weight 0, the engine's HTTP port, target `<name>-tandem.local` |
| TXT | `name=<host name>`, `version=<release>`, `port=<HTTP port>`, `fp=SHA256:...` (the host key's fingerprint) |
| A `<name>-tandem.local` | each IPv4 address the engine listens on for other machines |

Nothing else is advertised: no code, token, key or path. TandemApp lists the instance with its name and `fp`. Pairing then checks that same fingerprint at step 3.

## The version

MINOR adds a route, a field or an error type; MAJOR changes the group, the identities, the transcript, the seal or a meaning. TandemApp keeps a copy pinned to an engine commit and replays `fixtures/` against its own code.
