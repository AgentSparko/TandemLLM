# Pairing

TandemApp controls an engine host over SSH, with a key that runs only `tandem remote` ([remote-control.md](remote-control.md)). Pairing puts that key on the engine host without a password: you type a 6-digit code the engine host shows, and TandemApp gets back the SSH host key, the SSH user and port, and an HTTP token of its own. The code also proves the host key, so TandemApp does not have to trust it on first sight. The contract is [contracts/engine-pair-v1](../contracts/engine-pair-v1/README.md); the code is `server/pairing/`.

## The install ends in pairing

    curl -fsSL https://raw.githubusercontent.com/0xBakeer/TandemLLM/main/install.sh | bash

No sudo is needed. The install starts the engine, or setup mode with `--setup`, then opens a pairing window. Its last lines are:

      Code:          482913
      Fingerprint:   SHA256:jvC1...   (ED25519 SSH host key)
      Address:       192.0.2.10:8000

In TandemApp, choose Add engine, pick this host (or type the address), and type the code.

- A new install without `--host` binds `0.0.0.0`, so TandemApp on another machine can reach it.
- Before anything is paired, it writes the HTTP token's lock line to `src/run/http-token`, the hash of a token nobody holds. Callers on other machines get 401 until pairing gives TandemApp its own token. Callers on the engine host need no token.
- A re-run keeps the address the install has.
- `--host` chooses the address yourself. On `127.0.0.1` only this machine can pair, and `tandem pair` says so.
- `--no-pair` skips all of it: no window, no address chosen, no lock.
- `--no-start` opens no window either; run `tandem pair` after the first start.

## tandem pair

    bin/tandem pair            a new window: 5 minutes, 5 tries, single use
    bin/tandem pair --status   the window's state
    bin/tandem pair --close    close it

Run it on the engine host itself; it needs no SSH. The window lives in the engine process, in setup mode or while serving, so start the engine first (`bin/tandem start`). A new window closes an open one. `tandem pair` reaches the engine over loopback with the window key, `src/run/remote/pair-local.key`, which only the installing user can read and which changes at every start. A paired key cannot open a window: `tandem remote` has no `pair` verb.

## What the exchange does

TandemApp makes a new ed25519 key and runs SPAKE2 with the code against `POST /v1/engine/pair/1` and `/2`. These routes take a caller without the token only while a window is open, at most 20 requests a minute from one address. The allow-from list (`QSE_ALLOW_FROM`) still applies.

Once TandemApp's confirmation checks, the engine host:
1. appends one line to `~/.ssh/authorized_keys` of the user that installed the engine:

       command="/bin/bash DIR/bin/tandem remote --key-id ID",no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding ssh-ed25519 AAAA... ID

2. issues an HTTP token for that key id; `src/run/http-token` keeps only its sha256, on the key id's own line;
3. answers, sealed with the run's key: the SSH user and port, the host keys, the HTTP port and the token;
4. closes the window.

Limits and safety:
- A wrong code installs nothing. Each run spends one of 5 tries; after 5 the code is spent. After 5 minutes it expires.
- A host key or a TandemApp key swapped on the way makes the confirmation fail, so nothing is installed and nothing is trusted.
- `~/.ssh` is created 0700 and the file 0600 if missing. No other line is touched.
- A `~/.ssh` or `authorized_keys` that is a symbolic link, belongs to another user, or is writable by group or others is refused. `tandem pair` says so before it opens a window.
- A key that already has a line of its own is refused.

`tandem remote unpair` removes the key's line and its token line. `tandem remote token rotate` over a key's channel replaces only that key's token, so several TandemApps can each be paired with their own. Removing the last paired key's token leaves the lock, never an open network.

Every step is a line of `src/run/remote/journal.jsonl` (`pair open`, `pair try`, `pair abandoned`, `pair confirm`, `pair paired`, `pair expired`, `pair closed`). Each line has the address, the key id and the key's fingerprint, never the code, the token or a key.

## Finding the engine on the LAN

While it listens beyond loopback, in setup mode or serving, the engine process answers mDNS for `_tandem._tcp`. The answer carries the host name, the release, the HTTP port and `fp`, the SSH host key's fingerprint that `tandem pair` prints. No secret is ever in it.

- `QSE_ADVERTISE=off`, in `run/local.env` or through `tandem remote settings`, switches the advertisement off. TandemApp then takes the address by hand, a tailnet one included.
- The responder shares UDP 5353 with avahi or systemd-resolved (`SO_REUSEADDR`, `SO_REUSEPORT`) and uses its own target name, `<host>-tandem.local`.
- If another program holds the port without sharing it, the engine registers through `avahi-publish-service` when that is installed. Otherwise it advertises nothing and says so in one line at the start.
- IPv4 only.

`QSE_SSH_PORT` names the SSH port pairing reports; unset, it is sshd_config's `Port`, else 22.
