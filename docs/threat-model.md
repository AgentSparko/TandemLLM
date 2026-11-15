# Threat model

This page is for security researchers and for reviewers of a change. It says where untrusted input enters TandemLLM and which parts matter most. [SECURITY.md](../SECURITY.md) says how to report.

## What runs and where input enters

The engine runs on one machine on a home or office LAN. This page covers what the engine's repository serves:

| part | what it does | code |
|-|-|-|
| engine server, port 8000 | OpenAI-compatible API on the standard library's `ThreadingHTTPServer`, plus the engine's dashboard reads, the engine-only screens and the setup API; in chat mode also the routes of its extension | `server/`, `engine/` |
| status responder, `QSE_STATUS_PORT` | what the reverse proxy shows while the engine is stopped, starting or held | `ops/engine_status.py` |

TandemApp (the dashboard, the controller, chatd and its chat sandbox, TandemNode) is a separate project with its own threat model: [TandemApp's threat-model.md](https://github.com/TandemLLM/TandemApp/blob/main/docs/threat-model.md).

The supported deployment, from [server.md](server.md#the-admin-rule) and `server/api/auth.py` (AU1): the server listens on the LAN, sometimes behind nginx or a tunnel. The admin password (`QSE_ADMIN_TOKEN`, at least 32 characters) is optional and off by default. With no password, every route answers anyone who reaches the port. With one, the admin routes need the bearer token or the `qse_dash` session cookie. One exemption, trusted-local, lets a request in without a token: its TCP peer is loopback and it carries neither `X-Forwarded-For` nor `X-Real-IP`. The chat API (`/v1/chat/completions`, `/v1/completions`, `/v1/models`) is open in both modes.

In chat mode the engine imports the module `QSE_EXT` names into its own process ([server.md](server.md#chat-mode-the-conductor-as-an-extension)). That module runs with the engine's rights, so whoever can write `serve.env`, the install's `local.env` or the venv runs code in the engine; treat those files as the engine's code.

One more listener exists only when one model is split across two engine processes ([architecture.md](architecture.md#stages-one-model-in-two-processes)): the stage link, one TCP connection between the two stage processes of one engine (`engine/methods/link.py`). Its handshake is a challenge keyed with a shared secret of at least 32 characters, which the server reads from a file of mode 0600 (`--link-key-file`, `server/stage.py`; `serve.env` names only the file's path, `TANDEM_LINK_KEY`) and never writes it to a log or an answer, and the identity frames that follow carry a MAC under the same key. Data frames are neither signed nor encrypted. A frame is capped at 64 KiB until the peer has proved the key and at 256 MiB after it, and its length is checked before any payload is allocated. The listener binds one explicit IP literal and refuses a wildcard address. Run the link only on a direct cable or a network segment you trust, and treat the key as a secret: anyone who can change bytes on that path can change the model's answers.

Treat each of these as attacker-controlled:

- every HTTP request that reaches port 8000 or the status responder: method, path, headers, body, chunked framing;
- chat messages and their images (decoded with Pillow in `server/images.py`), and image URLs the server fetches for a client;
- the model's output: the tool calls it writes are parsed and typed by the engine (`server/api/toolcall.py`), and a client may run them;
- every byte that reaches the stage link's port before the handshake has passed;
- weight files and their headers downloaded from the Hugging Face Hub: safetensors, EXL3 packs, GGUF; and the community index of weight packs (`server/setup/community.py`);
- config files the setup API writes: the profile it switches to, and the overlay `setup.env` that `ops/serve.env` reads last.

## Parts that matter most

In this order:

1. Auth in `server/api/auth.py` (TandemApp keeps its own copy): any way past the admin rule with a password set, a forged trusted-local, a forged `X-Tandem-*` header, a session cookie that outlives its token, a CSRF on `/v1/setup/*` (the `same_origin_json` check in `server/setup_api.py`).
2. The fetch of an image URL for a client (`server/images.py`): a way to reach loopback, the LAN or the box through it.
3. Parsers for untrusted files: GGUF headers (`engine/models/nemotron_h/gguf.py`), safetensors and EXL3 headers (`engine/loader.py`, `engine/models/*/load.py`), images (`server/images.py`). Integer overflow in sizes and offsets, allocations of many GB and out-of-range reads count.
4. The setup API: it downloads, applies and removes weights and runs install steps. A crafted request or community pack that deletes files outside the weight folders, or runs a command, is in scope.

These matter less and stay in scope: the dashboard reads, the engine-only screens, the metrics route.

Out of scope: `bench/`, the scripts under `tools/` (measurement, quantising, training; nothing serves them), `docs/`, `manual/`, the GPU kernels in `engine/`, and `tests/`.

## Where to test

Every CPU test is a standalone script: `python tests/test_<name>.py`, exit 0 is a pass. They need the Python packages of install.sh's lock, with a CPU build of torch, and no GPU or Docker. `tests/gpu/` needs a DGX Spark.

| area | run it | tests |
|-|-|-|
| server, auth, admin rule | `python server/app.py --fake-engine --port 8000 --served-model qwen38-spark-engine --usage-ledger /tmp/l.sqlite3`; set `QSE_ADMIN_TOKEN` to turn the rule on | `test_auth.py`, `test_fake_engine.py`, `test_server_model_card_auth.py` |
| setup API | the fake engine above | `test_setup_api.py`, `test_setup_remove.py`, `test_setup_apply.py` |
| stage link | no server needed; `tools/linkrelay.py` cuts and stalls a link | `test_link.py`, `test_stages.py`, `test_stage_server.py` |
| loaders and images | no server needed | `test_nemotron_h_gguf.py`, `test_exl3_standalone.py`, `test_images.py` |

TandemApp's tests (chatd, the sandbox, the controller, the dashboard) are in its own repository, and its threat model names them.

## Not in scope

- Numerical differences in the GPU kernels and quantised weights, and the quality or safety of model output.
- Third-party weights and their contents, beyond how our loaders parse them.
- Rate limits and resource use of the chat API for a client the deployment lets in.
- The installer's need for network and `sudo` on a fresh machine.
