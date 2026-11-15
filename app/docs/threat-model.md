# Threat model

This page is for security researchers and for reviewers of a change. It says where untrusted input enters TandemApp and which parts matter most. The engine has its own page, [the engine's threat model](https://github.com/TandemLLM/TandemEngine/blob/main/docs/threat-model.md), which also says how to report.

## What runs and where input enters

TandemApp runs on one machine on a home or office LAN, in front of one or more engines. It has five parts:

| part | what it does | code |
|-|-|-|
| controller (`tandem-app`), port 8080 | the dashboard's page and API, the OpenAI API in front of other OpenAI-compatible backends and cloud APIs, the fleet, the usage ledger | `proxy/`, `tandemapp/` |
| chatd, 127.0.0.1:8016 | the Chat tab (beta): conversations, agent runs, tools, MCP, uploads; the engine (in chat mode) and the controller forward `/v1/chat-app/*` to it | `chatd/`, `tandemapp/chat_forward.py` |
| chat sandbox | one Docker or Podman container per chat, `--network none`; its only way out is chatd's egress proxy | `chatd/sandbox.py`, `chatd/egress.py` |
| dashboard | the web UI, served by the controller, and by an engine in chat mode while the engine still carries TandemApp | `dashboard/` |
| TandemNode, 7700 | the agent the controller reads a node through; mTLS with pinned certificates after pairing, a pairing window on 7701 while a code is shown | `proxy/nodeagent.py`, `proxy/pairing.py` |

The controller's admin password (`TANDEM_ADMIN_TOKEN`) is optional, as the engine's is. With no password, every route answers anyone who reaches the port. With one, the admin routes and every chat route need the bearer token or the `qse_dash` session cookie. The OpenAI routes behind the controller are open until the first user key exists; from then on every request from another machine needs a key. chatd trusts only requests that carry the forward key (`X-Tandem-Forward`).

Treat each of these as attacker-controlled:

- every HTTP request that reaches the controller, chatd's front on 4180, or TandemNode: method, path, headers, body, chunked framing, WebSocket upgrades;
- chat messages and their attachments, text files and images among them, and the PDFs and HTML pages the dashboard shows in a pane;
- answers from backends, MCP servers, `web_search` results and pages that `web_fetch` reads;
- the model's output. It picks the tool calls, and those run bash, write files and start servers inside the sandbox. A prompt injection in a fetched page can steer it;
- whatever runs inside a sandbox: it owns `/workspace` and its home, and can swap a file for a symlink or a FIFO while chatd reads it;
- config files the dashboard writes: backends, conductor config versions, the fleet's rules.

## Parts that matter most

In this order:

1. Auth in `tandemapp/auth.py` (the engine keeps its own copy), `tandemapp/chat_forward.py` and `chatd/http.py`: any way past the admin rule with a password set, a forged trusted-local, a forged forward key or `X-Tandem-*` header, a session cookie that outlives its token.
2. The sandbox boundary: a container setting that lets a chat reach the host, and egress bypasses in `chatd/egress.py` such as DNS rebinding, IPv4-mapped IPv6, redirects or CONNECT tricks that reach the LAN, the box or an engine's port. `ops/sandbox/relay.py`, which runs inside the container, is part of this.
3. Path handling on the host for a chat's files: `chatd/workspace.py` (`SandboxDir`, O_NOFOLLOW walks), uploads (`chatd/attach.py`), snapshots, the files pane, the Open WebUI tool runner (`chatd/owui.py`). Traversal, symlink races and writes outside `<data>/workspaces/<id>/` are in scope.
4. Script from a sandbox page that runs on the dashboard's origin. Previews must stay in an opaque-origin iframe; the CSPs live in `chatd/panes.py` and `chatd/preview.py`, and the port proxy in chatd.
5. HTTP handling we wrote: the chunked reader and keep-alive pool in `proxy/http1.py`, the byte forward and WebSocket splice in `tandemapp/chat_forward.py`, the egress proxy's request parsing. Request smuggling, header injection and unbounded reads count.
6. Node trust ([proxy.md](proxy.md), "Node trust"): a way to get a certificate pinned on a node or a controller without the code or SSH access, to beat the 5 tries of a pairing code, to reach a node's routes with an unpinned certificate or plain HTTP beyond loopback, or a password or key from SSH once that reaches a log, a file or an answer, or a password or pasted key from the Add Spark wizard ([proxy.md](proxy.md), "Add Spark") that outlives its 30 minutes in the controller's memory, reaches the config database or the session's JSON; or an SSH login to the controller's own machine that pins anything in its agent's trust store.

These matter less and stay in scope: the dashboard's own XSS from server data, the conductor and memory routes, the metrics route.

Out of scope: `bench/`, the scripts under `tools/` (measurement and evaluation; nothing serves them), `docs/`, `manual/` and `tests/`.

## Where to test

Every CPU test is a standalone script: `python tests/test_<name>.py`, exit 0 is a pass. They need no GPU, no torch and no Docker; `tests/test_sandbox_box.py` needs Docker with the sandbox image.

| area | run it | tests |
|-|-|-|
| chat forward and chatd | `python -m chatd --port 8016 --data /tmp/chat`; the key is in `/tmp/chat/forward.key` | `tests/test_chat_forward.py`, `tests/test_chatd.py`, `tests/test_chatd_agent.py` |
| sandbox and egress without Docker | `FakeBoxes` in `tests/chatd_util.py`, `RelayBoxes` in `tests/test_chatd_agent.py` | `tests/test_chatd_sandbox_scope.py`, `tests/test_chatd_sandbox_lifecycle.py`, `tests/test_sandbox_relay_fd.py`, `tests/test_chatd_websearch.py` |
| workspace paths and uploads | chatd as above | `tests/test_chatd_symlinks.py`, `tests/test_chatd_attach.py`, `tests/test_chatd_preview.py` |
| controller and relay | `python -m proxy --help`; `tests/fake_backend.py` is a backend | `tests/test_proxy_relay.py`, `tests/test_proxy_http1_tls_fd.py`, `tests/test_proxy_model_card_auth.py` |
| dashboard | `cd dashboard && npm test` | `dashboard/tests/*.test.ts` |

## Not in scope

- The quality or safety of model output.
- Rate limits and resource use of the chat API for a client the deployment lets in.
- A backend's own security: TandemApp passes requests on and records them.
