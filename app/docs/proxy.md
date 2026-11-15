# The proxy: one dashboard in front of any OpenAI-compatible backend

The dashboard can sit in front of other engines and cloud APIs, not only this one. Clients send their OpenAI requests to one address; the proxy routes each request by its model id to the backend that serves it and copies the answer back byte for byte. On the way it records what a client could measure: tokens from `usage`, time to first token, decode tokens a second, cached and reasoning tokens when the provider reports them, and the cost at the model's price. One ledger and one dashboard cover every backend.

Backends in this release:

| kind | what it is | routes relayed |
|-|-|-|
| `tandemllm` | another TandemLLM server, by URL | chat, completions, embeddings, Responses |
| `generic` | vLLM, SGLang, llama.cpp, LM Studio, Ollama, any OpenAI-compatible server | chat, completions, embeddings, Responses |
| `openai` | OpenAI's API with your key | chat, completions, embeddings, Responses |
| `deepseek` | DeepSeek's API with your key | chat, completions |

The proxy only observes in this release. It never changes a request's meaning: it sets the model id the backend knows, and it asks a stream for its usage frame when the client did not (then it drops that frame before the client). The conductor's features (task class, effort, packages, the loop and tool checks) come to foreign backends in a later release. Their switches are already stored per model.

## Two ways to run it

### Inside the engine's server

`server/app.py --backends ~/.tandemllm/config.sqlite3` serves the engine's own model as before and relays every request whose model belongs to a backend. A relayed request runs on its own thread and never takes the engine's lock or a place in its queue, so a slow cloud answer cannot hold up the engine and the engine's queue cannot hold up a cloud answer. On the :8000 service, set `QSE_BACKENDS` in `ops/serve.env` and restart; empty means off, and the service is exactly what it was.

First live run, 2026-10-07 on the test board (0.4.1-test), with DeepSeek as the one backend:

    # once: the backend and its key into the config database (the key encrypted, AES-256-GCM;
    # the master key is secrets.key beside the database, mode 0600), from the Backends screen or
    # ConfigDB.create_backend({"id": "deepseek", "kind": "deepseek", "key": ...})
    # ops/serve.env:
    QSE_BACKENDS=${HOME}/.qwen38-spark-engine/backends/config.sqlite3
    TANDEM_BACKENDS=deepseek
    TANDEM_BACKEND_DEEPSEEK_KIND=deepseek
    TANDEM_BACKEND_DEEPSEEK_URL=https://api.deepseek.com/v1

Setting the kind and the URL in the environment locks them on the Backends screen. Without an admin password anyone who reaches the port can edit a backend, and a URL changed to another host would receive the key in the `Authorization` header; with the URL locked that edit answers 422. The key itself stays in the database, never in serve.env. The start log says `[proxy] on: ..., 1 backends (deepseek); TandemLLM stays the engine's`. Measured through the LAN reverse proxy: `/v1/models` listed `TandemLLM`, `deepseek-flash` and `deepseek-v4-pro`; streamed answers carried the reasoning in `reasoning_content` with `thinking` on and none with it off; time to first token 0.65 to 0.85 s; Open WebUI and opencode picked the DeepSeek models from the same connection as `TandemLLM`; a DeepSeek request sent while the engine was decoding a request of its own got its first token in 0.77 s, as when the engine was idle. Every row is in the ledger with its cost.

### On its own

Anyone without a GPU runs the same code as TandemApp, the `tandem-app` command. It needs Python 3.11 or newer, the standard library and `cryptography`, and never pulls torch. A checkout has no built page (see "The built page" below), so build it first, with Node 20.19 or newer, or fetch a released one:

    python3 tools/dashboard_bundle.py build     # npm ci and npm run build in dashboard/
    pip install .                       # or '.[host]': adds psutil and NVML for the System view
    tandem-app                          # 127.0.0.1:8080, ~/.tandemllm/config.sqlite3
    open http://127.0.0.1:8080/dashboard/

`python -m proxy` in the checkout does the same without installing anything but `cryptography`. The page opens on Setup while no backend exists. The engine-only panels (caches, speculation, the conductor, memory) say that this server has no engine.

From a DeepSeek key to a row in the Usage view takes five commands:

    pip install '.[host]'
    printf 'TANDEM_BACKENDS=deepseek\nTANDEM_BACKEND_DEEPSEEK_KIND=deepseek\nTANDEM_BACKEND_DEEPSEEK_KEY=%s\n' "$DEEPSEEK_API_KEY" > app.env && chmod 600 app.env
    tandem-app check --env-file app.env
    tandem-app serve --env-file app.env &
    curl -s localhost:8080/v1/chat/completions -H 'Content-Type: application/json' -d '{"model":"deepseek-chat","messages":[{"role":"user","content":"Say hi"}]}'

Then open `http://127.0.0.1:8080/dashboard/#/usage`.

### In docker

`packaging/Dockerfile.app` builds an image of about 70 MB on `python:3.12-slim`, with the page inside and no compiler or torch in it. A Node stage builds the page from `dashboard/` with the lockfile, so the checkout needs no Node:

    docker build -f packaging/Dockerfile.app -t tandemllm-app .
    cp packaging/app.env.example app.env          # fill in; no quotes around values
    docker run -d --name tandem-app -p 127.0.0.1:8080:8080 -v tandem-app:/data \
        --env-file app.env tandemllm-app

The config database, the usage ledger and the key file (when `TANDEM_SECRETS_KEY` is not set) live in the `/data` volume. Inside the container the server binds 0.0.0.0, so what the outside reaches is what `-p` publishes: `127.0.0.1:8080` for this machine, a LAN address for a local network. Turn the login on (`TANDEM_DASHBOARD_LOGIN=on` and `TANDEM_ADMIN_TOKEN`) before you publish it to anything but loopback. Failed logins are throttled per client: a fronting proxy's `X-Real-IP` is believed only when the TCP peer is loopback, or names itself in `TANDEM_TRUSTED_PROXY`. With `-p`, a proxy on the host does not reach the container from loopback: Docker forwards its connection from the bridge's gateway, 172.17.0.1 on the default bridge, so that gateway address goes into `TANDEM_TRUSTED_PROXY`; without it every client behind the proxy shares one bucket. The proxy must set `X-Real-IP` itself (`proxy_set_header X-Real-IP $remote_addr;` in nginx): a proxy that passes the client's own header on lets the client pick its bucket. The container runs as an unprivileged user and answers its health check on `/health`.

### The built page

The page (`dashboard/dist/`) is a build output and not in git. CI builds it on every pull request (`.github/workflows/dashboard-dist.yml`: `npm ci`, the type check, vitest, the build, the size budget, a second build that must give the same hash) and hands it to the jobs that package it. `tools/dashboard_bundle.py` is the one tool every install path uses:

    python3 tools/dashboard_bundle.py build                 # npm ci + npm run build, then dist/bundle.json
    python3 tools/dashboard_bundle.py verify                # the files against their bundle.json
    python3 tools/dashboard_bundle.py fetch --url URL --sha256 SHA256   # a released bundle, checked
    python3 tools/dashboard_bundle.py ensure                # intact, else build (Node), else fetch, else what to do
    python3 tools/dashboard_bundle.py publish --to host:/var/www/tandemapp   # ensure, then rsync

`dist/bundle.json` holds the bundle's content hash (the sha256 over each file's path and sha256), every file's sha256, the commit, the lockfile's sha256 and the Node version. The same commit built twice gives the same hash. The server names the bundle it serves: `tandem-app check` prints it on its `page` line, every answer under `/dashboard/` carries `X-Tandem-Bundle` (16 hex digits of the hash, `none` for the "not built" page), and `/health` with the admin token has it under `dashboard`. `pip install` of a checkout without the page stops and says how to get it, `pip install -e` too; with `TANDEM_DASHBOARD_LATER=1` an editable install goes on, and `/dashboard/` says the page is not built until it is. A fetch needs the archive's sha256 from the release and refuses an archive without one, with links, or with a path outside its folder. Releases do not ship the archive yet.

Up to 0.15.0 the image was `tandemllm-dash` and the volume `tandem-dash`; the volume keeps its old name and its data. Mount it as before, or copy it into the new volume once:

    docker run -d --name tandem-app -p 127.0.0.1:8080:8080 -v tandem-dash:/data \
        --env-file app.env tandemllm-app
    docker run --rm -v tandem-dash:/from -v tandem-app:/to python:3.12-slim cp -a /from/. /to/

### The `tandem-app` command

| command | what it does |
|-|-|
| `tandem-app` or `tandem-app serve` | the server; every flag of `python -m proxy` (`--env-file`, `--config-db`, `--host`, `--port`, `--usage-ledger`, `--dashboard-login`) |
| `tandem-app check` | reads the environment and a copy of the config database through the schema and prints what would run: the backends, their key hints and locked fields, the rules, the page, the listen address. It writes nothing and exits 1 on an error that names the variable |
| `tandem-app secrets init` | makes the master key file beside the config database (0600) when no master key answers; `secrets status` says which source answers |
| `tandem-app backends list` | the backends, one line each, from a copy of the database |
| `tandem-app hostcollect` | the remote host collector (below) |
| `tandem-app launch vllm\|sglang` | starts vLLM or SGLang with TandemLLM's plugin inside and registers it as a backend (below) |
| `tandem-app service install\|uninstall\|status` | the controller as a background service of the user: a launchd agent on macOS, a systemd user unit on Linux (below) |
| `tandem-app package [--version V]` | the wheel this controller installs on nodes and updates them to, built from its own copy; a local label (`0.15.0+fix`) marks a build of a release |
| `tandem-app version` | the installed version |

`tandem-dash`, the old name, still runs `tandem-app` with the same arguments and exit codes, after one line on stderr; it is removed in 0.17.0.

## Configuration: the GUI and the environment

Everything lives in one SQLite file, the config database: backends, their keys (encrypted), model lists, prices, routing rules, per-model switches, users. The Backends tab edits it: add a backend, test the connection (it shows the latency and the models before anything is saved), edit, delete. Every change is a row in its `audit` table with the time and who made it, and no key value.

The same objects can be declared as `TANDEM_*` variables, for a docker start or any headless box. The environment wins, field by field: at every start the variables are written over the database's rows (a backend is created when missing), and the GUI shows each field a variable sets as locked, with the variable's name on it. A save that changes such a field answers 422 and names the variable. When the variable goes away, the GUI can edit the field again, starting from its last value. A key from the environment stays in memory and is never written to the file.

    TANDEM_BACKENDS=deepseek,vllm1
    TANDEM_BACKEND_DEEPSEEK_KIND=deepseek
    TANDEM_BACKEND_DEEPSEEK_KEY=sk-...
    TANDEM_BACKEND_VLLM1_KIND=generic
    TANDEM_BACKEND_VLLM1_URL=http://gpu-box:8000/v1
    TANDEM_BACKEND_VLLM1_FALLBACK=deepseek
    TANDEM_ROUTE_1="deepseek-*=deepseek"
    TANDEM_PRICE_1="deepseek/deepseek-flash=0.30,0.006,1.2"
    TANDEM_PRICE_1_OFFPEAK="0.15,0.003,0.6"
    TANDEM_API_KEYS="laptop:tdm-...,ci:tdm-..."

`python -m proxy --env-file proxy.env` reads such a file; a variable already in the process environment keeps its value. The full list, with the fields each backend takes (`_KIND`, `_URL`, `_KEY`, `_LABEL`, `_MODELS`, `_MODELS_MODE`, `_FALLBACK`, `_MAX_INFLIGHT`, `_ENABLED`, `_INJECT_USAGE`, `_HARNESS`, `_HARNESS_PROFILE`, `_TIMEOUT_CONNECT`, `_TIMEOUT_FIRST_BYTE`, `_TIMEOUT_IDLE`, `_HOST`, `_HOST_URL`, `_HOST_DCGM_URL`), is at the top of `proxy/config.py`, and `packaging/app.env.example` is a file to start from. One schema (`proxy/config.schema.json`) checks both ways in: a wrong variable stops the start with a message that names it.

## Keys at rest

A backend's key is stored as AES-256-GCM ciphertext. The master key comes from the first of these that answers: `TANDEM_SECRETS_KEY` (base64 of 32 bytes), the OS keychain (`security` on macOS, `secret-tool` on Linux, read only), or `secrets.key` beside the config database, made once at mode 0600. The Backends tab names the source. No API answer, log line, ledger row or audit row holds a key: the GUI gets `key_set`, the last 4 characters and 6 hex digits of the key's sha256, and the key field is write-only.

Encryption at rest protects a copied or backed-up database. It does not protect against a process running as the same user, which can read the key file as easily as the proxy can, and a backup of the whole directory carries the key file too. Keep the master key in the environment or the keychain when backups leave the machine. A lost master key means the keys are typed in again, nothing else.

To keep it in the macOS keychain instead of the file:

    security add-generic-password -s tandemllm -a secrets-key -w "$(cat ~/.tandemllm/secrets.key)"
    rm ~/.tandemllm/secrets.key

## The machine behind a backend

The System view shows the machine each backend runs on, down to the process behind the backend's port. For a vLLM on the same machine that is its API server and its engine core, with their RSS, CPU, threads, uptime and GPU memory. A backend's host setting says where to read it:

| setting | reads | per-process figures |
|-|-|-|
| `auto` (the default) | this machine, when the backend's URL is loopback or one of this machine's addresses; nothing for a cloud API | yes |
| `same` | this machine, whatever the URL says | yes |
| `collector` | `tandem-app hostcollect` running on the backend's machine, at `host.url` | yes |
| `exporter` | a node_exporter page at `host.url` and a DCGM exporter page at `host.dcgm_url` | no |
| `off` | nothing | |

The server's own process is always listed first, as `engine` in the engine's server and `proxy` in the standalone one. The Backends form sets the host per backend; `TANDEM_BACKEND_<ID>_HOST`, `_HOST_URL` and `_HOST_DCGM_URL` set it from the environment and lock it.

The collector reads psutil and NVML when they are installed (`pip install '.[host]'`), else `/proc` and `nvidia-smi`, and macOS without psutil gives the machine's total memory and load only. The process behind a port is found by its listening socket: psutil, then `/proc/net/tcp` and the processes' file descriptors, then `lsof` or `ss`. A process that belongs to another user stays unreadable without root, and the view says so. Command lines show the home as `~`, and the collector masks the value after any flag whose name holds `key`, `token`, `secret`, `passw` or `auth`.

On a GB10 (the DGX Spark) the CPU and the GPU share one memory pool. The driver reports no memory for the GPU itself, so the view shows "unified" there and `MemAvailable` is the figure to watch. The GPU memory of a process is still reported, and it comes out of the same pool as its RSS: the two overlap.

When the System view asks, the collector reads memory, CPU, GPUs, disks and network at most every 5 s and scans the processes at most every 15 s; in `tandem-app` the fleet's sampler (below) reads the same collector every 2 s. One sample with a scan took 4.4 ms of CPU on a Mac with psutil, under 0.1 % of one core at that cadence, and each host's footer shows what its own last sample cost.

On another machine, run the collector next to the backend and give both sides the same token:

    TANDEM_COLLECTOR_TOKEN=$(openssl rand -hex 24) tandem-app hostcollect --host 0.0.0.0 --port 9108

It answers `GET /host` as JSON and `GET /metrics` as Prometheus text (`tandem_host_*`), both behind the bearer token; it refuses to bind beyond loopback without one, because its answer names processes and their command lines. `--watch vllm:8000` reports a port on every answer; the proxy asks for its backends' ports itself. A machine that already runs node_exporter and NVIDIA's DCGM exporter needs nothing new: set the backend's host to `exporter` with both page URLs. That path has no per-process figures, and the panel says so. A remote read runs on its own thread, so a slow or dead host never holds the page; its panel shows the last answer, its age, or the error.

In docker the collector sees the container, not the machine. Run the container with `--pid=host` (and `--gpus all` for NVML) for the machine's view, or run `tandem-app hostcollect` on the machine itself.

## The fleet: `/v1/fleet/*`

TandemApp keeps the registry of the Sparks it manages and answers the fleet API (contract: [`contracts/fleet-v1`](../contracts/fleet-v1/README.md), code: `proxy/fleet.py`, `proxy/nodesource.py`, `proxy/fleetstore.py`, `proxy/fleetwatch.py`). The dashboard reads it for the shell's fleet readout, the scope bar, the charts and the windows; the browser talks to `tandem-app` only.

On one box the fleet has one node: the machine `tandem-app` runs on, when it has an NVIDIA GPU or an engine of its own. The first start registers it with its hostname as the alias, the first identity colour and an id from the machine (the DMI serial, else `/etc/machine-id`), never its address. A new address keeps the node and is noted in the log. A machine in front of cloud APIs only is the controller's host and no node. When TandemNode runs on the same machine (below), the controller reads the machine through it, the same path as any other node's; while it does not answer, in-process as before. A node on another machine is read through its TandemNode agent once pairing has given the controller its trust, and is listed `off` until then.

| route | on a fleet of one |
|-|-|
| `GET nodes`, `GET nodes/{id}` | the node with status, version and inventory |
| `PATCH nodes/{id}` | `{alias?, colour?, icon?, tags?}`; an alias or colour another node has is refused |
| `GET`/`POST deployments`, `GET`/`PATCH`/`DELETE deployments/{id}`, `GET`/`POST deployments/{id}/actions/{action}` | the deployments (below): the records, and a Single derived from each model an engine serves that no record covers; start, stop and move |
| `POST placement` | `{model, pack, ctx, slots?, nodes?}`: each node's fit from its own Setup Guide estimate, the types allowed or why not (below) |
| `GET weights`, `POST weights/copy`, `GET weights/copy/{id}`, `GET`/`DELETE weights/{node}/{profile}` | the packs on every node, a copy between nodes verified by content id, a removal with its confirm (below) |
| `GET`/`POST nodes/{id}/setup/<route>`, `GET nodes/{id}/setup-confirm/<route>` | the node's Setup Guide through its agent, streams as they come; a write (apply, remove, reset, a retry, waking Wren) with the confirm of its plan (below) |
| `GET links`, `GET topology` | the LAN of the default route; the internet when a cloud backend exists |
| `GET metrics?source=&metric=&range=` | decode tok/s, requests and TTFT p50 from each engine (a TandemLLM engine's live snapshot, a vLLM's or SGLang's `/metrics`), the hardware from the host collector, from the fleet store (below); the hardware sampled every 2 s, a TandemLLM engine's own one-second samples read every 2 s while a dashboard reads the fleet and every 30 s otherwise (the first read brings its last five minutes); null where no sample exists |
| `GET memory` | the engine's split (weights, KV, caches), the rest of the machine's use, the page cache, GiB |
| `GET logs?node=&level=&q=&limit=&access=` | `tandem-app`'s own log (`gateway`) and its TandemLLM engines' (`engine`), merged by time; `info` and up unless `level` says otherwise; HTTP access lines are tagged `access` and left out unless `access=1` |
| `GET`/`PUT layouts/{page}?device=` | a page's window layout per device class in the config database; `saved_at` is the controller's clock, the last write wins |
| `GET events?since=&before=&node=&type=&limit=` | the event log, newest first (below) |
| `GET alerts`, `POST alerts/{id}/ack` | the alerts firing now; an acknowledgement is kept |
| `GET rules`, `PATCH rules/{id}` | the alert rules; `{enabled?, notify?, threshold?, for_s?, scope?}` |
| `POST pair`, `pair/ssh`, `pair/ssh/hostkey`, `DELETE nodes/{id}`, `GET trust` | node trust, below |
| `GET`/`POST nodes/{id}/actions/{action}` | the node actions, below: a plan of what it would do, then the action with the plan's `confirm` |
| `POST links/{id}/test` | a LAN link's round trip and bandwidth from the controller to the node's agent, kept on the link |
| `GET`/`POST`/`DELETE session` | the controller's own sign-in for its writes |

Reads (an action's plan too) and a page's window layout follow the dashboard rule, the other writes the admin rule, as `/v1/config` does; pairing, release, forget and a lease's release need the admin password or a caller on the controller's machine, and with no password set only the latter. The engine's own server has no fleet API; a dashboard it serves shows the box alone.

**Node actions** (code: `proxy/fleetops.py`). `drain`, `undrain`, `restart-engine` (`?engine=`, else the first engine TandemNode manages), `reboot`, `update`, `forget`, `release-lease`. `GET .../actions/{action}` answers what it stops (the deployments on the node and where their new requests go, the requests in flight), what it removes and what it keeps, and `blocked` in words when it may not run. All but drain and undrain are destructive: their plan carries a `confirm`, single use for five minutes, that the `POST` must send (428 without it, 409 with a spent or another action's).

- **Drain.** A drained node takes no new request through this gateway: the relay skips a route's backend on it for the route's fallbacks, and with none left answers 503 `node_draining` with `Retry-After: 30`. What is in flight finishes; in flight is the larger of the gateway's own requests on the node's engines and the engines' own count (a client that calls an engine's port directly is counted, not turned away). The node is `draining` (◐) until nothing is in flight, then `drain.state` `drained`; the drain is kept in the config database, so it holds across a restart of the controller from its first request, until `undrain`. "Elsewhere" is another member of a Replicas deployment (the gateway places no new conversation on a drained member; its own conversations go to another member at their next turn), else a route's fallback.
- **Restart, reboot, update** go to the node's agent: an engine's restart (an engine TandemNode did not start is refused: "not managed by TandemNode"), the machine's reboot and update (below). A node read without its agent has none of them.
- **Forget** is the release above: both pins, the record and its history here go; TandemNode, its engines and the weights stay on the node. **Release lease** is for a rented node: weights and caches wiped, keys removed, TandemNode uninstalled; it comes with rental support (409 for an owned node, 501 until then).

### Backends as fleet members

Any OpenAI-compatible server is a fleet member without TandemNode and without the TandemLLM engine (code: `proxy/members.py`). A backend of the config database that sits on no node (a cloud API, a server on a machine without TandemNode) is listed in `GET nodes` under `backends`, in a node's shape with `class` `backend`, the id `backend.<backend id>` and the backend's label as its alias. A backend whose URL is on a node's address is that node's engine, as before, and the node's hardware figures are its own.

**Connect one.** Get started (or API, Backends) takes the server's URL and, when the server asks for one, a key. `POST /v1/config/test {url, key?}` asks the server for its model list and answers `ok`, the models, and a `reason` when it fails: `key_rejected` (401 or 403: the server answers and refuses the key), `unreachable` (no answer), `not_openai` (an answer that is no model list). `POST /v1/config/backends {url, key?, label?, kind?, verify: true}` tests first and saves nothing when the test fails (409 with the test) until the same body comes with `confirm: true`. Without an `id` the backend gets a free one from its label or the URL's host; `kind` defaults to `generic`.

**What a member gives.** Its models are deployments (a derived Single each), are in `/v1/models` and answer through the gateway; the ledger row and `X-Tandem-Node` name the member, so Usage splits by member. The scope bar, Overview, Performance and Models list it beside the Sparks. Its figures are what the OpenAI surface and the gateway give: the requests in flight and the time to first token the gateway measured, and the decode rate only when the app reads the server's own metrics page. Whatever needs a machine is not available for a member, and each answer says so: `nodes/{id}/sample` is empty with a note, a hardware series is all null, Hardware shows "No host figures" with the offer to install TandemNode (Add Spark), and a node action, the Setup Guide relay, a patch or a release answers 409 `not_a_node`.

**Its state.** `up` when the model list answers; `alert` with "key rejected" when the server answers and refuses the key (it is reachable, so it is not `off`, and no other member is touched); `off` when it does not answer, is disabled or was not checked yet. A local server's model list is its owner's: a model it lists from now on is served at once (a cloud API's new id still waits to be turned on), one it drops leaves the deployments, and a client that asks for the dropped id reads the ids served now in its 404.

**Replicas** over two members exist only as a record that names them (`members: [{"backend": "a"}, {"backend": "b"}]`): the same model id on two backends is two Singles. Such members show "identity not available" and no output canary runs for the deployment; neither check can mark them a mismatch. A TandemLLM engine reached by URL has its engine API version read from its `/health` with the backend's key: another major is `down` with the reason and takes no request, and an answer without the field reads as 1.0.

**The key** is stored as every backend key is (below, Keys at rest): encrypted in the config database, sent upstream only. It is in no log line, ledger row, `/health`, config or fleet answer, and the configuration export leaves the backends and their keys out.

### Deployments, placement and weights

Code: `proxy/deployments.py`, `proxy/placement.py`, `proxy/fleetweights.py` (the node's half `proxy/nodeweights.py`), `proxy/affinity.py`. A deployment is a record in the config database: a type, a model, a pack, members and options.

| type | members | what the gateway does |
|-|-|-|
| `single` | one node's engine | routes the deployment's API id to it |
| `replicas` | two or more nodes' engines at one release | keeps each conversation on one member (the engine's own conversation key: `conversation_id`, `session_id`, `user`, their `metadata` forms, `X-Conversation-Id`, `x-session-id`, Open WebUI's chat id; a worker's `-task-N` key with its chat), places a new one by rendezvous hash over the members that serve, one without a key on the member with the fewest in flight; the other members are its fallbacks before any byte |
| `role` | one node's engine, a helper of another deployment (`role_of`) | as `single` |
| `split` | two nodes: the front stage's, then the tail's | routes the deployment's API id to the tail alone; the front serves no request and is never listed. 503 `stage_down` with `Retry-After` while the two are not linked |

`POST deployments {model, type, members, pack?, api_id?, ...}` makes one; a member is a node id, or `{node, engine}` on a node with several engines, and the controller makes the backend that reaches that engine when there is none (`http://<node address>:<port>/v1`). `/v1/models` lists each deployment once under its API id; every answer from a node's engine carries `X-Tandem-Node` (the node's alias) and `X-Tandem-Deployment`, and its ledger row the node's id and the deployment (Usage filters `node=` and `deployment=`). A model an engine on a node serves that no record covers is a Single derived from it (the box's own engine on :8000, which TandemNode does not manage, shows that way until a record covers it). A Replicas member on another release, or with other weights, than the first serving one takes no request (`mismatch`); so does one whose identity or canary differs (below). A constrained request (structured output, a grammar, a forced tool choice) goes to another member only when the first never took it. `X-Tandem-Pin: <node id or alias>` sends a request for a record to that member alone: no other member is tried, a Replicas conversation keeps its home, 409 `not_a_member` for another node and 503 `member_not_serving` for a member that takes no request (the Playground's A/B of two members).

**Replicas checks** (code: `proxy/canary.py`, SRV-741). Each member's `check` in the deployment says what the controller found:

- **Identity.** An engine that says engine API 1.1 or later is read at `GET /v1/replica/identity` through its TandemNode agent when it turns serving, when it restarted and every 60 s, never on a request's path. The block names the code, the model, the weights, drafters, rows and tokenizer by content, and the decode settings. Every field but `max_len` is compared with the first serving member's; a member that differs is `mismatch` and the text names the field (`decode.temperature`, `weights`). The first read after a start may answer 503 `identity_pending`: it is asked again every second for at most 120 s. An engine on 1.0 is never asked: its `check.identity` is `not_available`, and the release and weights name check is all there is. The controller compares blocks; it knows nothing of a model family.
- **Canary.** Three fixed prompts, greedy, thinking off, 32 tokens, with logprobs, to each serving member after any member state change and every 10 minutes; the first serving member is the reference. The requests go to each engine directly with `User-Agent: tandem-controller/canary`, so this ledger has no row of them and an engine's ledger files them as internal. Other tokens: `mismatch` until a later round passes. A near tie (each answer ranks the other's token within 0.1 nats of its own) runs the prompt twice more on the reference: if the reference writes the member's token there itself, the result is the warning `near_tie` (the member keeps serving, the deployment's `warn` says it); if not, `mismatch`. A round runs on a member only while it has nothing in flight; a busy member puts the round off by 30 s, so a user's request waits at most for one canary prompt.
- **Windows.** Members may differ in `max_len` (`check.window`). When the serving members say more than one window, the gateway counts a request's prompt with the first member's engine (`POST /v1/tokenize`) and adds `max_tokens`: a request past that member's window goes to a member that fits, else 400 `context_length_exceeded` that names the largest window. With one window nothing is counted, and the engine's own 400 applies.
- **Cache routes.** `POST /v1/cache/peek`, `/prewarm` and `/release` with a deployment's `model` go to the member that holds the conversation (its `session`, else the conversation key above), with `X-Tandem-Node` and `X-Tandem-Deployment`; they never move the conversation's home. Without `model`: 400 `model_required`. Behind a reverse proxy these three are sent to the controller only where a client uses them on a Replicas deployment.

A page whose `/v1/*` reaches an engine and only `/v1/fleet/*` reaches the controller (a reverse proxy in front of both) reads the controller's ledger at `GET /v1/fleet/ledger/summary`, `/usage` and `/requests` (the answers of `/v1/dashboard/*`, with `node=` and `deployment=`) and sends a completion through the gateway at `POST /v1/fleet/gateway/chat/completions` (the page's own JSON POST only, then the API keys as on `/v1/chat/completions`). The two ledgers are never added up: a request the gateway relays to a node's engine is a row in both, the controller's with its node and deployment. Usage shows one ledger at a time and says which.

The actions run as jobs through the node agents. **Start**: each member's engine started (an engine TandemNode does not manage only when it answers already), its health, then one token from the model; a member serves only after that warm-up. **Stop** (with the plan's confirm): the record stops first (503 `deployment_stopped` for new requests), what is in flight finishes, the engines stop. **Move** `{from, to}` (with the plan's confirm): the engine on `to` started and warmed, then it takes the new requests and conversations while `from` finishes what it runs, then `from`'s engine stops; the API id stays.

**A Split** (code: `proxy/splitplan.py`, `proxy/splitrun.py`, SRV-817) is one model cut between two layers: the front stage on one node, the tail on another, joined by one TCP link. It needs engine API 1.2 on both nodes and a TandemNode agent that manages each engine.

- **The plan.** `POST placement` with `split` (`true`, or the engine's ask: `caches`, `side_window`, `table`, `snapshots_gb`, `front_cache_gb`, `tail_disk_gb`, `cuts`; the planner's own `cut` and `link`) asks each node's engine for its estimate with the `split` block. For each legal cut the front's need is the front node's own answer and the tail's the tail node's, plus a margin for side slots: the estimate leaves out a side slot's graphs and row buffers, so each side slot is counted once more (measured once: 0.87 GiB against 0.44 computed). A side fits within its node's budget (the device less the floor; on a node that keeps serving something, what is free now). The plan takes the cut whose smaller headroom is largest and lists every other cut with why it lost. Every figure is computed from the estimates; the engine's `measured` list is passed on as it is. A model that is not splittable, an engine before 1.2, or no cut that fits is the type's `reason`.
- **The link.** The nodes need an interface pair in one subnet: a fabric pair first, else a LAN pair with a warning, because the data link is neither signed nor encrypted after its handshake. The addresses are IP literals, never a name and never every interface; `split.link {listen, peer?}` names them by hand. The front listens (port 7740 unless named), the tail dials.
- **The start.** The controller makes a link key (48 random bytes) and hands it to both node agents (`PUT /node/engines/{id}/stage`). Each agent writes it to a 0600 file and gives its engine the file's path with the stage's keys (an install's `run/local.env`, else the start's environment). The key is in no database, log, job, event or answer. Then the front starts, and once it answers the tail; both `/health` stage blocks are checked (the planned ranges, one epoch, each side's peer identity, equal slots and caches), the tail answers one token, and the plan's `built` turns true. A start that fails stops what it started and removes the keys; if a stage cannot be stopped the error is `partial_split`.
- **States.** `split.phase` is `stopped`, `starting-front`, `starting-tail`, `linked`, `degraded` (both run, not linked: 503 `stage_down` with `Retry-After: 5`; the engines link again by themselves and it serves again), `partial` (one stage runs: Start brings the other back, Stop ends it), `stopping` or `down`. A stage's member state `waiting` means it runs without a link.
- **Stop** takes the tail first, then the front, and removes both key files. A drain of either node stops new requests for the Split; a fleet update takes both nodes in one step and starts the stages again in order. A Split does not move: stop it, plan it on the new nodes, start it.
- Not done by the controller: it does not switch a node's weight profile. Each node's engine must be set to the pack first (its Setup Guide); two stages of different weights refuse their link.

Timeouts: a stage's start 600 s each and 150 s for the link after both answer. An engine's start 1800 s (its `start_timeout_s` in the agent's `engines.json` applies on the node), the health and the warm-up 300 s, the requests in flight on a stopping or leaving member 600 s. The fleet update drains a node for at most 600 s (`DRAIN_WAIT_S`), waits 1800 s for its update, and rejoins it only once its engines answer and each deployment it serves gives one token, for at most 300 s (`REJOIN_WAIT_S`); a Replicas member waits at most 300 s for another member to serve before it drains. Past any of them the step fails, the job stops and the node stays drained.

**Placement** (`POST placement`) plans each node from its own estimate: the Setup Guide's memory formula with that node's device and MemAvailable, through its agent (`POST /node/setup/estimate`, which the agent relays to its engine's Setup Guide; the controller's own machine read without its agent asks its engine's `POST /v1/setup/estimate`). Neither TandemNode nor the controller computes an estimate itself: a node whose engine is absent or does not answer has no estimate and fits nothing. A node that serves nothing fits when the estimate fits its budget; one that serves something must fit the new engine and the floor in what is free now (`needs 98 GiB, Gold has 61 free`). Replicas need two nodes that fit at one release (and warn when one is remote). A Split is planned when the body asks for one (`split`) or when no node holds the model alone (below).

**Weights.** Each node's packs are its agent's: the Setup Guide's weights and the agent's weights store (`--weights-dir`, default `~/.tandemllm/node/weights`), each with its content id. `POST weights/copy {from, to, profile}` copies a pack once over the network instead of a second download: the receiver checks its free space, every file is hashed by the sender and the receiver (and against the hub manifest's sha256), and the copy is verified when its content id is the source's. It goes from the source's agent straight to the receiver's when both are paired over mTLS on the LAN, else through the controller (the controller's own machine on loopback, a tunnelled node). A removal needs its plan's confirm and is refused while a deployment serves the pack there.

**Each node's Setup Guide** (code: `proxy/fleetsetup.py`; the relay in `proxy/app.py`). `nodes/{id}/setup/<route>` is the node's engine's `/v1/setup/<route>` through its agent, so TandemApp sets up any Spark without the browser ever calling it: the answers and the streams (a job's log, Wren's answer) pass unchanged, and a node whose engine is restarting answers through its status responder. The rule is the engine's own `/v1/setup` rule; a POST must be a same-origin JSON request. The writes (`apply`, `remove`, `reset`, `jobs/<id>/retry`, `assistant/load`) need the confirm of `GET nodes/{id}/setup-confirm/<route>`, which names what the write does on that node (its engine restarts, which deployments stop answering there and where their requests go, what is removed) and is bound to the body it was read for (`?model=&profile=`, `?profiles=`, `?level=`); a write the node took is an `engine` event. A copied pack sits in the agent's weights store, which the Setup Guide does not read yet: the guide finds a profile's weights in the folder its serve.env profile names (`QSE_WEIGHTS_DIR`), so the weights matrix lists a copy while the guide's weights step still counts that profile as a download.

SRV-467's replica sets (a routing rule with members, from its branch) become Replicas records the first time a config database written by that branch opens (`self`, the engine the proxy ran in, is this machine's engine); the rule is turned off and kept. Its environment form, `TANDEM_ROUTE_<n>="model=a+b"`, is refused with the words to make a Replicas deployment instead.

### History, events and alert rules

The fleet store is one SQLite file beside the config database, `fleet.sqlite3` (WAL, mode 0600; `--fleet-store PATH|off`, `TANDEM_FLEET_STORE`). Each metric of each node and engine is kept in three tiers of slots, each slot the sum, count and largest of its samples:

| tier | kept | answers |
|-|-|-|
| 2 s | 15 minutes, in memory; written at a clean stop and read at the start | `5m` |
| 30 s | 26 hours | `1h`, `6h`, `24h`, the newest day of `7d` |
| 10 min | 30 days (`--fleet-retention-days`, at least 7; `TANDEM_FLEET_RETENTION_DAYS`) | `7d` beyond 26 hours: the six slots that end in each hour |

A slot that changed is written at the next flush, every 5 minutes in one transaction, so a crash loses at most those minutes and a clean stop nothing; the slots and events past their reach are deleted once an hour. TTFT keeps every value per slot (up to 64 distinct ones, then buckets 2^(1/16) apart, within 2.2 %), and a sample's TTFT is the median of the requests in its window, merged over the node's engines. History survives a restart; the time the controller was stopped stays null.

The event log records a node going off, draining or up again (held 10 s), a node paired, taken over or released, this machine's new address, an engine backend down, up, started, restarted (back within 2 minutes, or its token counter reset while up) or stopped by a job on its node, a deployment's status, each alert firing, resolving and acknowledged, and the controller's start and stop. Each event names a node id, `all` or `host`.

The alert rules run on every sample. Each has a debounce (the condition holds for `for_s` before it fires) and hysteresis (it resolves once the value is back past the threshold by its margin for its own time). The defaults suit a GB10, with unified memory and no fan or power cap exposed:

| rule | fires | severity | resolves |
|-|-|-|-|
| `gpu-temp` | GPU ≥ 85 °C for 60 s | err | below 80 °C for 60 s |
| `throttle` | a thermal or power-brake clock event (NVML: HW slowdown, SW or HW thermal, power brake) for 30 s | warn | none for 60 s |
| `mem-avail` | MemAvailable < 10 GiB for 30 s | warn | above 12 GiB for 30 s |
| `disk-free` | a mount under 10 % free for 5 min | warn | above 12 % for 5 min |
| `node-down` | the node does not answer for 30 s | err | up for 10 s |
| `engine-down` | an engine backend's health check fails for 60 s | err | one check that passes |
| `host-sleep` | the controller runs on a machine that may sleep (a Mac) | warn | |

An alert carries the page to open (`#/hardware?scope=node:<id>`, `#/sparks/<id>`, `#/models`, `#/app`) and stays listed, acknowledged or not, until it resolves; one that fires again is a new alert. A rule turned off resolves its alerts; a figure a node does not give (an off node's temperature) holds them. `notify`: `app` is the alerts list, `system` is for the page to show as a system notification; the controller makes no outbound call, and another target plugs in as a notifier in `proxy/fleetwatch.py`. Rule edits and acknowledgements follow the admin rule.

Write cost on a fleet of one, a DGX Spark with one engine: 11 series (the hardware figures the GB10 gives and the engine's three), about 3,000 rows a day each in the two written tiers. Measured with the engine idle: the controller wrote 196,608 bytes in 21 minutes, about 13 MB a day for all its files (the config database and the usage ledger included), of which the fleet store's WAL took about 7 MB. At its full reach the file holds about 80,000 slots, a few MB (projected, not yet measured).

## The data plane: clients through the controller

On a box where clients call the engine at `/v1`, TandemApp's controller can take the OpenAI routes in its place (SRV-733): `POST /v1/chat/completions`, `/v1/completions`, `/v1/embeddings`, `/v1/responses` and `GET /v1/models[/...]`. Then every client request is a row of the controller's ledger with its Spark and deployment, and a deployment on another Spark answers at the same address. Everything else under `/v1` stays the engine's: the conductor, the Setup Guide, the dashboard's reads, the caches, the memory, the library, Laya and the decision models, the tokenizer, the Chat tab's chatd routes, the configuration.

A client sees what the engine gave it before, with no change on its side:

| what | through the controller |
|-|-|
| status codes and bodies | the engine's answer, byte for byte for a stream; the controller's own answers (a 401, a 404 model, a body that is not JSON) are the ones the engine writes, from the same code |
| `/v1/models` | the engine's cards as the engine lists them (profile, description, `tandem` with its capabilities, context and output limits) and its `decision_models`; `/v1/models/{id}` of an id no list names (the raw model's alias, a decision model) is the engine's own answer; the answer carries `X-Tandem-Gateway: tandem-app` |
| a model id no backend lists | `TANDEM_DEFAULT_BACKEND=engine`: to the engine as asked; the engine answers any id with its own model, as it did (unset: OpenAI's 404) |
| the client's headers | to the engine as sent: its agent and referer (the client kind), the conductor's, memory's and the session headers, `X-Real-IP` and `X-Forwarded-For` as the reverse proxy set them, its `Authorization` when the backend has no key of its own; never a cookie, `X-Tandem-User`, `X-Tandem-Relay-Id` or `X-Tandem-Pin` a client sent |
| whose memory | the key's label, else `owner`, as for a request sent to the engine (`X-Tandem-User`, which the engine takes from a loopback peer only) |
| the answer's headers | the engine's, but `X-Tandem-Backend`, `X-Tandem-Node` and `X-Tandem-Deployment` are the controller's |
| the cost comment (`X-Tandem-Cost: 1`) | the engine's own, which prices what it relays; the controller adds none for a TandemLLM backend |
| keys | `TANDEM_API_USERS_FROM=<the engine's config database>`: the keys the engine's users hold open the controller too, live (a user made or turned off on the engine counts at the next request). The controller reads the label, the key's sha256 and `enabled` from that file, read only, at each check; no key and no hash is copied, logged or stored here. While the engine has no user both are open; a wrong key is the same 401 on both. A file that cannot be read keeps the last answer, and before any answer the routes need a key |
| long thinking | no idle cut while the engine still works on the request: when the first-byte (600 s) or idle (120 s) wait runs out, the relay asks the engine's board (`/v1/dashboard/parallel`) whether it still holds the request under the relay's lane id, and waits another span while it does. The engine's own request timeout ends a request; a client that leaves still closes the upstream within a quarter second. An engine whose board does not answer the controller (a remote engine with the dashboard login on) is cut at the timeouts as before |
| counted once | a request is a row in the controller's ledger and one in the engine's, never added up. The controller's row holds the engine's answer id (`upstream_request`, ledger schema 12), which is the `request_id` of the engine's row: the two join on it. Usage reads one ledger at a time and says which; a page whose `/v1/models` answer carries `X-Tandem-Gateway` reads the controller's by default, one Spark too, with the engine's a click away (the Chat tab's requests go to the engine straight and are in its ledger) |

The engine's relays stay its own: a DeepSeek model the engine relays is listed and answered through the controller as the engine lists and answers it, and both ledgers have the row. The relay may try the engine again on a 429 or 503 before any byte (twice at most, after its `Retry-After`), as it does for every backend.

The controller's settings for a box (`app.env`):

    TANDEM_BACKENDS=engine
    TANDEM_BACKEND_ENGINE_KIND=tandemllm
    TANDEM_BACKEND_ENGINE_URL=http://127.0.0.1:8000/v1
    TANDEM_DEFAULT_BACKEND=engine
    TANDEM_API_USERS_FROM=~/.qwen38-spark-engine/backends/config.sqlite3

First run, 2026-10-28, on the test board (0.15.0: the engine with its conductor and a DeepSeek backend of its own, the controller beside it on the same Spark, the reverse proxy on another machine of the LAN, no users on the engine). Before the switch, on the controller's port: `/v1/models` gave the engine's three ids with the same card and `tandem` keys and its four decision models, an unknown card and a body that is not JSON the engine's 404 and 400, and three requests of 8 tokens (whole, streamed, the raw model's name) 200 in 3.1 to 4.0 s with `X-Tandem-Node` and `X-Tandem-Deployment`; each controller row's `upstream_request` found the engine's row of the request, client kind as the client sent it. After the switch, the same through the public address, and every engine-only route kept the status it had (health, dashboard, cache, conductor, Setup Guide, library, Laya, memory, chat-app, fleet). A fake engine quiet for 630 s (no byte at all for a whole answer; the head and then nothing for a stream) behind a controller with the default timeouts answered 200 after 630 s both ways. The rollback below took one reload: the next `/v1/models` came from the engine.

### Through a reverse proxy

nginx in front of both sends the five route families to the controller and leaves every other path where it was. Exact (`=`) and `^~` locations win over a regular expression location, so an existing `location ~ ^/(v1|health|events|metrics)(/|$)` for the engine stays as it is:

    location = /v1/chat/completions { include snippets/tandemapp-data-plane.conf; }
    location = /v1/completions      { include snippets/tandemapp-data-plane.conf; }
    location = /v1/embeddings       { include snippets/tandemapp-data-plane.conf; }
    location = /v1/responses        { include snippets/tandemapp-data-plane.conf; }
    location = /v1/models           { include snippets/tandemapp-data-plane.conf; }
    location ^~ /v1/models/         { include snippets/tandemapp-data-plane.conf; }

with `snippets/tandemapp-data-plane.conf` (`<controller>` is the Spark's address):

    proxy_pass http://<controller>:8091;
    proxy_http_version 1.1;
    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header Connection        "";
    proxy_buffering off;               # a stream's frames go out as they come
    proxy_request_buffering off;
    proxy_cache off;
    gzip off;
    client_max_body_size 200M;
    proxy_connect_timeout 10s;
    proxy_send_timeout 3600s;
    proxy_read_timeout 3600s;          # the engine's --request-timeout: a quiet hour is the most

`proxy_read_timeout` is the time between two reads, so a stream that sends a byte now and then (the engine's prefill comments, reasoning) is never cut by it; a whole answer of an hour's thinking is. `/v1/chat/completions/` with a slash and other spellings stay on the engine. Before the switch, check the controller directly on its port with a small request of each kind (both paths answer in the meantime), then `nginx -t` and `systemctl reload nginx`, and check the same through the public address.

### Rollback

Restore the site file from the copy made before the change (or delete the six `location` lines), `nginx -t`, `systemctl reload nginx`: clients reach the engine directly again with that reload, without a change on the controller, the engine or a client. The controller keeps its ledger; requests after the rollback are in the engine's only.

## TandemNode: `tandem node`

TandemNode is the agent on each Spark that TandemApp's controller talks to (code: `proxy/nodeagent.py`). It ships in the same package: `tandem-node`, or `tandem node` on an `install.sh` install, where the install's engine is its engine. It listens on port 7700.

| route | what it answers |
|-|-|
| `GET /node/identity` | node id, hostname, DMI product and serial, OS, kernel, driver, CUDA, TandemLLM version, code sha |
| `GET /node/inventory` | disks, network interfaces (ConnectX ports with firmware, RoCE state, physical port), memory, CPU cores by kind, GPUs |
| `GET /node/metrics` | server-sent `sample` events once a second, on the whole second: GPU (use, clocks, power, temperature, throttle reasons, encoder and decoder), unified memory split, per-core CPU, disks with NVMe temperature, LAN and fabric per interface with RDMA counters, thermal zones, fans, uptime. A field the board does not expose is absent, never 0. `?once=1`: the newest as JSON |
| `GET /node/engines`, `/node/engines/{id}` | each engine: port, managed or not, state, its `/health` in short, the running job |
| `POST /node/engines/{id}/start\|stop\|restart` | a job (202); 409 when the engine is not managed here or a job runs |
| `GET /node/engines/{id}/<route>` | the engine's `/health`, `/v1/models`, `/v1/dashboard/live` (its live stream), `/v1/dashboard/system`, `/v1/dashboard/logs`, `/metrics`, relayed |
| `GET /node/weights`, `DELETE /node/weights/{profile}?confirm=1` | the Setup Guide's weights with a content id per pack (from the hashes the download recorded), and its removal |
| `GET /node/logs?level=&q=&source=` | the agent's and its engines' lines as events; `?follow=0` as JSON |
| `POST /node/linktest {peer}` | to another node's agent: RTT, bandwidth per interface, MTU, RoCE state; a port whose cable reaches one of its two logical interfaces is flagged `half_bandwidth` |
| `GET /node/maintenance`, `POST /node/maintenance/reboot\|update` | whether the machine's reboot and update are set up here (simulated on a virtual node), the last job; a job (202), 409 when not set up or a job runs |
| `* /node/setup/<route>` | the engine's `/v1/setup/<route>`, unchanged (its status responder while it restarts) |
| `GET /node/chat`, `POST /node/chat/start\|stop\|restart` | chatd, the Chat tab's service, when this node owns it: running or stopped, the engine's `QSE_CHATD`, the watcher's count; a job (202), 409 when chatd is the engine's or a job runs |

Engines come from `~/.tandemllm/node/engines.json` (`{"engines": [{"id", "port", "kind", "start", "stop", "restart"}]}`, commands as argument lists). An engine without a `start` command is listed and relayed but never started or stopped by the agent. Without the file: the install's engine, else a TandemLLM engine on :8000, not managed; a virtual node (below) has none.

**chatd on an engine box** (SRV-823, code: `proxy/nodechat.py`). With `tandem-node serve --chat on` (or `{"own": true}` in `~/.tandemllm/node/chat.json`) TandemNode owns chatd: `tandem-node chat start|stop|restart|status|logs`, and a watcher with the engine watchdog's rule (it starts a dead chatd once the engine answers `/health`, at most 3 times an hour, never in setup mode, during a hold or in engine mode, and leaves one stopped by hand). It runs TandemApp's `ops/chatd.sh` in the engine's environment, so chatd's data folder, port, forward key and interpreter stay what they were; its log is `~/.tandemllm/node/logs/chatd.log`. The engine hands chatd over with `QSE_CHATD=node` in its `run/local.env` (engine API 1.6: its `ops/start.sh`, watchdog and `bin/tandem` then leave chatd alone, and `tandem chat` runs `tandem-node chat`), or `QSE_CHATD=off` on an older engine; until then the watcher starts nothing and says why. Without `--chat on` nothing changes: the engine's launchers run chatd.

The machine's reboot and update come from `~/.tandemllm/node/node.json` (`{"reboot": [...], "update": [...]}`, argument lists; code: `proxy/nodemaint.py`). Neither is set by default: a reboot needs a right to reboot (a sudoers or polkit rule for the agent's user) that the agent never takes itself, and an update is the install's own. Without a command the action is refused with the words to set it up.

**Who may call it.** Every route needs the caller check. Until pairing has filled the trust store (`~/.tandemllm/node/trust`: `node.crt`, `node.key`, `peers/*.pem`), the agent listens on loopback only, in plain HTTP, and every request carries the loopback secret from `~/.tandemllm/node/secret` (made at the first start, mode 0600; a file others can read is refused) as `Authorization: Bearer`. Any other address needs mTLS: a client without a certificate the store vouches for fails the handshake. After pairing, loopback is mTLS too, and the node's own CLI uses the node's certificate. The owner and the node itself reach every route; another node pinned for link tests only `/node/identity` and `/node/linktest`. It never listens on 0.0.0.0 or a WAN address; a remote node is reached through an SSH tunnel to its loopback. `tandem-node check` says how it would listen, and why not. Every change is a log line with its caller.

**As a service.** `tandem-node unit > ~/.config/systemd/user/tandemnode.service`, then `systemctl --user daemon-reload && systemctl --user enable --now tandemnode`. A `tandem-app` on the same machine finds it at `http://127.0.0.1:7700` with that secret (`TANDEM_NODE_AGENT`, `TANDEM_NODE_SECRET`; `TANDEM_NODE_AGENT=off` for none). `--virtual NAME` runs a second agent on the same machine as another node, with its own id (its own `--state-dir` and `--port`). A virtual node never reboots or updates the machine (both are simulated, whatever `node.json` says), has no engine unless its own `engines.json` names one, and serves only the engine backends whose port its agent lists.

## Node trust: pairing, ownership, reset

A node and a controller trust each other through pinned certificates; no password is stored anywhere (code: `proxy/pairing.py`, `proxy/sshonce.py`, `proxy/fleettrust.py`, `proxy/nodetrust.py`).

**What is pinned where.**

| side | file | what |
|-|-|-|
| node | `~/.tandemllm/node/trust/node.crt`, `node.key` | its own EC P-256 certificate, self-signed, made at the first start |
| node | `trust/peers/owner.pem`, `owner.json` | the one controller that owns it, and its name, how and when it paired |
| node | `trust/peers/node-<id>.pem` | another node pinned for link tests (the owner pins it) |
| node | `trust/listen.json`, `audit.log` | the addresses pairing listens on in mTLS; one line per pairing, release, take-over, reset |
| controller | `controller/controller.crt`, `controller.key` beside the config database | its own certificate, made at the first start |
| controller | the node's record (`trust`) | the node's pinned certificate and its fingerprint |
| controller | `controller/known_hosts` | SSH host keys, trusted on first use |
| controller | the config database's `audit` table | each pairing, take-over, release and forced forget |

**Pairing by code.** `tandem node pair` on the node opens a window and prints a 6-digit code, the address and port, and the node's certificate fingerprint. The window is a TLS listener on the node's LAN address (default: its default route's source), port 7701 (the agent's + 1), and answers the pairing protocol only. In TandemApp, Add Spark (or `POST /v1/fleet/pair {address, code}`) runs SPAKE2 with the code: the RFC 9382 message flow, transcript and key confirmation, in the prime-order subgroup of RFC 3526's 2048-bit MODP group. Both certificates' SHA-256 fingerprints are SPAKE2's identities: a certificate swapped on the way, or a wrong code, makes the confirmations differ and nothing is pinned. A run gives an attacker one guess and nothing to test codes against offline. A code lives 5 minutes, allows 5 runs and is used once; the controller makes at most 10 attempts a minute. On success the node pins the controller as its owner and listens on that address in mTLS on 7700; the controller pins the node's certificate and records `https://<address>:7700`.

**SSH once.** `POST /v1/fleet/pair/ssh {host, user, password | key}` logs in with the system's `ssh`, once. The password reaches ssh through `SSH_ASKPASS` (a script in a private temporary directory that prints a variable set for that child only); a pasted key is a 0600 file in the same directory; both are gone when the call returns and are scrubbed from every message. The host key is trusted on first use and remembered; `POST /v1/fleet/pair/ssh/hostkey` shows it before, `host_key` in the body insists on it, and a changed key is refused. Over the session, `tandem-node adopt` (found from the `tandemnode` user unit, the PATH, or an `install.sh` install) pins the controller, reloads the agent or starts its unit, and sends back the node's certificate, which the controller pins. A node without TandemNode is refused: installing is Add Spark's Install step. The probe refuses before the adopt when the box's `/etc/machine-id` is the controller's own (the controller's machine is read through its loopback agent and needs no pairing). The route of an ssh line comes along: `jumps` (`-J`, each jump logged in to with the user's own keys or agent, never the password) and `key_file` (a private key file on the controller's machine, read by ssh where it is). `tunnel: true` pairs a rented or remote node whose agent stays on its own loopback: `adopt` takes `127.0.0.1`, and the controller keeps an `ssh -N -L 127.0.0.1:<port>:127.0.0.1:7700` open to it (`proxy/sshtunnel.py`: BatchMode, the remembered host key, restarted after 1, 2, 4 ... up to 60 s when it ends). A tunnel logs in for as long as the node is in the fleet, so it takes a key file or the agent, never a password; a key pasted for it is kept as a 0600 file in `controller/keys/` and removed when the node is released.

**Ownership and take-over.** A node has one owner. Another TandemApp gets 409 `owned` and nothing else (its certificate fails the handshake). Two ways to move a node: the owner releases it (`DELETE /v1/fleet/nodes/{id}`: the node forgets the controller, then the controller forgets the node; a node that does not answer only with `?force=1`), or the take-over is confirmed on the node: `tandem node takeover` (a code window marked as a take-over), or SSH once with `takeover: true` (SSH access proves the node is the user's). The old owner's pin goes and its open connections close; its records show the node off, "refuses this controller's certificate".

**Who may pair.** Pairing, release and take-over need the admin password (`TANDEM_ADMIN_TOKEN`: the bearer token, or a session from `POST /v1/fleet/session {token}`), or a caller on the controller's machine. With no password set, only the latter: pairing never opens to the network by default.

**Reset.** On a node: `tandem node reset` forgets the owner and every pin, then the agent listens on loopback with the secret again; `--keys` makes a new certificate too (every controller must pair again). `tandem node status` shows the owner, the fingerprint and the window. On the controller: removing `controller/` makes a new certificate at the next start, and every node must pair again; a node's line in `controller/known_hosts` is removed by hand after a reinstall.

## Add Spark: the wizard

Settings → Add Spark walks Find, Connect, Check, Install, Link, Name and Done (code: `proxy/addspark.py`, `dashboard/src/views/fleet/wizard.ts`; the routes in `contracts/fleet-v1`). Its session lives in the config database (`add_sessions`), one active at a time, so a reload, a restart of the controller, or another device opens it at its step. A step with nothing to do is skipped and says why: Install when every device runs the fleet's release already, Link with fewer than two new Sparks on the LAN.

- **Find** is discovery (above): the live list, the LAN scan after its question, Add by address, and a rented or remote node from its ssh line, with an optional key file, jump host, lease end and cost per hour.
- **Connect** pairs by the node's code, or over SSH once with the host key shown and confirmed first. A box with nothing installed is `bare` and goes on to Install.
- **Check** runs one script (`proxy/preflight.py`) through the node's agent (`GET /node/preflight`) when it is paired, else in an SSH login: OS, kernel, driver, CUDA, disk, memory, TandemNode's release (the install prefix's venv, else `tandem-node` on the PATH; an engine checkout's `VERSION` does not count), ConnectX-7 and its links, sudo, the clock, and for an install also Python and whether user services outlive a logout (linger). Every row that is not ok names its fix. A TandemNode release other than the fleet's is red and Install refuses it; a local build label (`0.15.0+build`) is the same release.
- **Install** (`proxy/nodeinstall.py`) installs TandemNode at the fleet's release and the engine's runtime at the engine ref (**The engine**, below) on a bare box, one SSH login per step with progress and the last lines of each in the session: `check`, `upload` (the wheel, its sha256 checked there), `agent` (a venv in `~/.local/share/tandemllm`, `pip install` of the wheel; cryptography from the node's own package index), `engine` (the engine's `install.sh --runtime-only`: the engine's source and its Python environment with the CUDA 13.0 torch, never weights, which come with Serve a model), `unit` (`tandemnode.service`, started, answering within 30 s) and `pair` (SSH once's adopt). **The package**: nothing is published, so the node gets what this controller runs: one wheel built here from the controller's own copy with the standard library's zipfile (its checkout as `pyproject.toml` lists it, or the installed distribution's files), cached in `packages/` beside the config database; `TANDEM_NODE_WHEEL` names a wheel to send instead. **The engine** is not TandemApp's to carry: `TANDEM_ENGINE_INSTALL_SH` names the engine's `install.sh` on the controller's machine (the login gets it as its input), and the release it installs is `TANDEM_ENGINE_REF` (a tag such as `v0.16.0`), else `v` and the `VERSION` file beside that installer, else `v` and the controller's own release; the ref is checked as a tag name and quoted on the remote command line. Without `TANDEM_ENGINE_INSTALL_SH` the engine step fails and names the variable (an install with `engine: false` skips it). The controller refuses to install when its own release is not the fleet's. A controller that stops during an install marks it interrupted at its next start; each step is safe to run again.
- **Link** with two or more new LAN Sparks tests each one's link from the controller and shows the result. Setting link addresses and MTU on the fabric needs sudo on each node, asked per action; it comes with the rented pair and answers 501 until then.
- **Name** sets each new node's alias, colour, icon and tags; Finish applies them as `PATCH /v1/fleet/nodes/{id}` would (an alias or colour another Spark has is refused). **Done** shows the topology with the new nodes and Serve a model.

**Secrets.** The wizard stores no password and no pasted key: not in the session, the database, a log line or an answer. SSH once uses the password for its login; a box with nothing installed needs it again for Check and Install, so this controller's memory holds it for that target for 30 minutes at most, until the install takes it, the session ends or the controller stops, whichever is first; then the page asks for it again and says why. A key file is a path on the controller's machine. A rented node's key is kept as a 0600 file while the node is in the fleet, for its tunnel (above).

**Permissions** are asked where they are needed, each with one line of why and what works without it: macOS's Local Network before the first search (denied: add by address, or a rented node by its ssh line), notifications in Done for a rented node's lease reminder (else Events & Alerts). The Keychain is not a browser app's to ask for: TandemApp running in a browser keeps no secret of the user's; the controller's own certificate and keys are 0600 files beside its config database. The native Mac shell (parked) is where a Keychain belongs.

**Rented.** A target with a lease end is paired through its tunnel; its record gets `lease_end`, `cost_per_hour` and the tag `rented`. The rule `lease` reminds 24 h before the end by default (`lease_left`, warn, app and system notifications; editable like any rule), once per lease; it resolves when the lease is extended or the node is released.

## TandemApp alone: install, the controller's host, the fleet update

**Install.** TandemApp is the controller and its page, without an engine, torch or a GPU (OPS-234). On a Mac, `brew install ./packaging/homebrew/tandemapp.rb` (the formula is in the repository; no tap carries it yet) or, on macOS and Linux alike, `pip install '.[app]'` in a virtual environment (Python 3.11 or newer; the `app` extra adds psutil only). Then:

    tandem-app service install --open     # launchd (macOS) or a systemd user unit (Linux); opens the page
    tandem-app service status             # installed, running, its pid, whether it answers /health
    tandem-app service uninstall          # stopped, unloaded, the plist or unit removed; the data stays

`service install` writes `~/Library/LaunchAgents/org.tandemllm.app.plist` (started at login, kept alive, its log in `<state dir>/logs/tandem-app.log`) or `~/.config/systemd/user/tandemapp.service` (enabled, restarted on failure), runs `<its Python> -m proxy serve` with `--host` (default 127.0.0.1), `--port` (8080), the state dir's files (`--state-dir`, default `~/.tandemllm`) and `--env-file` when given, and waits until `/health` answers. `--name` gives the label or unit another name, so a second controller can run beside the first. A Linux user unit stops at logout unless the user lingers (`loginctl enable-linger`); status says so. A controller that listens beyond loopback needs `TANDEM_ADMIN_TOKEN` in its env file. `uninstall` leaves nothing loaded and no file of the service; the registry, the ledger and the keys in the state dir stay.

The first open of the page without a route, on a controller with no member yet (no Spark, no backend), lands on Get started (Settings): connect a server by its URL, add a Spark, or install the TandemLLM engine on a machine (Add Spark's install step). A route in the link is kept.

**The controller's host** (App settings, `GET /v1/fleet/controller`, code: `proxy/ctlmove.py`). The controller must stay on for the API to stay up, so a Spark is the better host; a Mac can run it, and the host-sleep rule and the page say what its sleep costs. Moving it to a Spark reads a plan first (`GET /v1/fleet/controller/move?to=<node>&port=`: the new API URL, each node and what happens to it, what stays) and runs with the plan's confirm:

1. the fleet's export: the config database without its secrets, the wizard's sessions and the jobs (backends, routes, the proxy's keys as hashes, rules, nodes with their pins, layouts), and each stored API key, to be stored again under the new controller's own master key;
2. the Spark's TandemNode starts a controller there (`POST /node/controller`: a systemd user unit, its own state dir with the export and an env file with the admin password, both 0600), which imports the export at its first start and makes its own certificate; a state dir that holds a controller's database already is refused;
3. each paired node is handed to the new certificate by its current owner (`PUT /node/owner`): from then on the old controller is refused and the new one accepted. The Spark itself is released and read by the new controller over its loopback; a node reached through an SSH tunnel stays (its key is a file on this machine);
4. the new controller is asked for the nodes until each answers it, the old controller's refusal is checked, and the old one says `moved_to`: its page follows to the new URL and the API URL is printed.

The metric history stays where it was. The way back starts on the machine that takes the fleet: its App settings take it from the controller's URL with that controller's admin password (`POST /v1/fleet/controller/take`); the giver seals its export to the taker's certificate (ECDH P-256, HKDF-SHA256, AES-256-GCM), the taker imports it, the giver hands its nodes over and says `moved_to` the taker. Every move, handover and take needs pairing's rights.

**The fleet update** (Sparks → Update fleet, `/v1/fleet/update`, code: `proxy/fleetupdate.py`, the node's half `proxy/nodeupdate.py`). One release on every node: the one in the package this controller installs (`tandem-app package`, or `TANDEM_NODE_WHEEL`). The plan orders the nodes: those that serve nothing first, then each Replicas member alone (drained, updated, rejoined while another member serves its model), then nodes with a deployment only they serve (the plan names the downtime), then each Split's members together. A Split never updates partly: a member that cannot be updated refuses the Split, and a selection with one member and not another is refused 409 `partial_split`. An engine installer the controller cannot read (`TANDEM_ENGINE_INSTALL_SH`), or an engine ref that names no release (`TANDEM_ENGINE_REF`, the `VERSION` beside the installer), blocks the plan too, and its POST is refused 409 `conflict` before any node drains. On a node the agent takes the wheel (sha256 checked), installs it into its own virtual environment with pip, runs the engine's runtime install (the controller's `TANDEM_ENGINE_INSTALL_SH` with `--runtime-only`, at the same engine ref as Add Spark; skipped with "the controller sent no engine installer (TANDEM_ENGINE_INSTALL_SH)" when the controller has none; simulated on a virtual node) and replaces itself with the new code (same process id). The job keeps each node's phase in the config database; a controller restarted during it resumes where it was. A node that runs outside a virtual environment of its own (a checkout) is skipped with the words to update it by hand.

**Mixed versions.** The rule `version-mismatch` fires one alert for the fleet once the nodes that answer run more than one release for 60 s (a local build label counts as its release); it is held while an update runs. The fleet pill and the Overview say it, and its action opens Update fleet.

## vLLM and SGLang: their own metrics

The proxy relays a backend of kind `vllm` or `sglang` like any OpenAI-compatible server, and it also reads the engine's own `/metrics` page every 15 s. That page holds what a relay cannot see. It says how many requests run and wait inside the engine and how full the KV cache is. It gives the prefix-cache hit rate and the engine's own time split of each request. Speculation and preemptions are on it too. The Performance view shows one card per such backend, the System view shows the same cards, and a backend's page under Backends shows its engine.

The names come from the source of vLLM v0.30.0 and SGLang v0.5.21, the releases current on 2026-10-04. Each engine has a name table in `proxy/engines.py`. A family the page does not have maps to nothing, and its tile says "not reported by this backend". The proxy skips a family the table does not know. A new release can cost a tile, never a failed read.

| figure | vLLM | SGLang |
|-|-|-|
| running, waiting | `vllm:num_requests_running`, `vllm:num_requests_waiting` | `sglang:num_running_reqs`, `sglang:num_queue_reqs` |
| KV in use | `vllm:kv_cache_usage_perc` (a fraction, 1 is full) | `sglang:token_usage`, and tokens from `sglang:num_used_tokens` of `sglang:max_total_num_tokens` |
| decode rate | `vllm:generation_tokens_total` over the window | `sglang:generation_tokens_total` over the window, and the engine's own `sglang:gen_throughput` |
| prefix-cache hits | `vllm:prefix_cache_hits_total` over `vllm:prefix_cache_queries_total` | `sglang:cached_tokens_total` over `sglang:prompt_tokens_total`, and the engine's own `sglang:cache_hit_rate` |
| TTFT | `vllm:time_to_first_token_seconds` | `sglang:time_to_first_token_seconds` |
| queue, prefill, decode | `vllm:request_queue_time_seconds`, `..._prefill_time_seconds`, `..._decode_time_seconds` | `sglang:queue_time_seconds`; no prefill or decode histogram |
| preemptions | `vllm:num_preemptions_total` | `sglang:num_retracted_requests_total` |
| speculation | `vllm:spec_decode_num_drafts_total`, `..._num_draft_tokens_total`, `..._num_accepted_tokens_total`, `..._num_accepted_tokens_per_pos_total{position}`: acceptance and tokens per step over the window, and acceptance per draft position | `sglang:spec_accept_rate` and `sglang:spec_accept_length`, gauges over SGLang's own last window |

Window figures come from the first and the newest reading of the last 5 minutes. With one reading, or no traffic in the window, they are null. vLLM's families carry an `engine` label per data-parallel engine; the proxy adds them up and takes the mean of a fraction. SGLang reports its scheduler families from the first tensor-parallel rank only. With `--enable-metrics-for-all-schedulers` every rank reports the same numbers, so the proxy keeps rank 0 and drops the rest instead of counting them twice.

The proxy's own `/metrics` carries the mapped families as `qse_backend_*` with a `backend` label, `qse_backend_info{backend,kind,version}` and `qse_backend_scrape_age_seconds`. They never enter the engine's own families. The Performance view reads them from that page; the System view reads the same figures from the backend's `engine` block in the system body.

The flags each engine needs:

| | vLLM | SGLang |
|-|-|-|
| the metrics page | always on | `--enable-metrics`; without it `/metrics` answers 404 and the card says so |
| cached tokens per request | `--enable-prompt-tokens-details` | `--enable-cache-report` |
| reasoning tokens per request | `--reasoning-parser` (the stream's field is `delta.reasoning`) | `--reasoning-parser`; without it SGLang still sends `reasoning_tokens: 0`, which the proxy reads as null when the stream carried reasoning |
| the version | `GET /version` | `GET /server_info` (`/get_server_info` before it), which also gives the speculative settings, the cache report and the reasoning parser |

The proxy does not call either engine's `/health`. On SGLang it runs a one-token generation when the server is idle, and the health poll's `GET /v1/models` already says whether the server answers. After a failed read the proxy waits 30 s, then 60, then 120 s between tries; after a 404 it asks every 120 s.

Two more facts from the v0.30.0 source matter for later layers. vLLM removed `guided_choice` and the other `guided_*` fields: it still answers 200 but ignores them, so a constrained question must use `structured_outputs: {"choice": [...]}`. And vLLM takes a client's `X-Request-Id` as the request id (`chatcmpl-<id>`; the engine's internal id adds 8 random hex characters), while SGLang takes `rid` in the body instead.

## Inside vLLM and SGLang: the plugins and the launcher

The `/metrics` page gives totals. A single request's queue time, its prefill time and whether the scheduler preempted it are not on it. Two small packages in `plugins/` run inside the engine and send those numbers per request to the proxy (layer L2). Install one into the engine's own Python environment; neither has a dependency:

    pip install plugins/vllm        # tandemllm-vllm-plugin: vLLM loads it from vllm.stat_logger_plugins
    pip install plugins/sglang      # tandemllm-sglang-plugin: SGLang loads it from sglang.srt.plugins

The engine needs two variables, `TANDEM_COLLECT_URL` (the proxy's `/v1/collect/<backend id>`) and `TANDEM_COLLECT_TOKEN`. The plugin keeps the last 10,000 finished requests and 300 one-second scheduler samples in memory and posts what is new once a second, or an empty batch every 10 s when the engine is idle. `plugins/README.md` lists what each one reads, the versions it was checked against, and what happens when a release does not fit.

One command does all of it, from nothing to a backend with L2 data:

    tandem-app launch vllm --model Qwen/Qwen3.5-4B --port 8100 -- --max-model-len 32768
    tandem-app launch sglang --model Qwen/Qwen3.5-4B --port 30000

`launch` creates the backend in the config database (kind, `http://127.0.0.1:<port>/v1`, host `same`), hands the engine the collect URL and the token, adds the flags the dashboard reads (`--enable-prompt-tokens-details` on vLLM, `--enable-metrics` and `--enable-cache-report` on SGLang), checks that the plugin is installed in the engine's Python, and then replaces itself with the engine, so the pid it records is the engine's. Without the plugin installed it says how to install it and starts the engine at L1. `--dry-run` prints the command, `--collect-url` names another proxy, and everything after `--` goes to the engine unchanged. `launch` is optional: an engine started by hand with the plugin and the two variables gives the same data.

The proxy joins each item to the request's ledger row by an id it sends upstream. vLLM gets `X-Request-Id: px-<hex>` and reports `chatcmpl-px-<hex>`, the row's own id. SGLang gets `rid` in the body and reports it unchanged. A client that sets its own `X-Request-Id` or `rid` keeps it, and the join uses it. The row then holds the engine's own numbers:

| column | vLLM | SGLang |
|-|-|-|
| `queue_ms` | `queued_time` | the scheduler's queue duration |
| `prompt_ms` (prefill) | `prefill_time` | forward entry to the end of prefill |
| `decode_ms`, `decode_tps` | `decode_time`; (tokens - 1) over it | first token to the end; the same rate |
| `prefill_tps` | uncached prompt tokens over the prefill | the same |
| `cached_tokens` | `num_cached_tokens` | `cached_tokens` (device, host, storage) |
| `preemptions` | `num_preemptions` | retractions |
| `engine_finish`, `corrupted` | the engine's finish reason, the NaN check | the finish reason |
| `blocks`, `draft_tokens`, `draft_accepted` | null (vLLM counts speculation per step, not per request) | verify passes, proposed and accepted drafts |
| `l2_join` | `exact`, or `approximate` | the same |

TTFT stays the proxy's own: it is what the client saw. An item usually arrives a second after its row; the ledger then sets the columns on the row in place (`Ledger.amend`, schema version 4). An item that comes first waits for its row. Either waits 10 minutes for the other and is then dropped and counted. A request the proxy cannot tag (the Responses API, SGLang with `n` above 1) is matched by time and token counts: the same backend, the same prompt and completion counts, ends within 3 s of each other, and only when exactly one row fits. Such a row says `approximate`, and the log has one line for it.

The backend's card shows "L2 plugin" while batches arrive and "L1 only" after 60 s without one, with the plugin and engine versions it last saw. Under the tiles it says how many rows got the engine's split, and over the last 5 minutes the preemptions, the KV evictions and, on vLLM, the acceptance per draft position. The proxy's `/metrics` adds `qse_backend_plugin_age_seconds` and `qse_backend_plugin_joins{join}`. A vLLM that loads the second entry point, `tandem_stats_endpoint` (vLLM loads an endpoint plugin only when `VLLM_PLUGINS` names it), also serves `GET /tandem/stats`. When nothing was pushed for 25 s the proxy reads that route every 2 s instead. Items carry sequence numbers, so a batch read twice counts once.

The collect token is `TANDEM_COLLECT_TOKEN` in the proxy's environment, or a random one the proxy stores in its config database at the first need. Every batch needs it, from loopback too. It lets its holder post numbers for a backend that exists, nothing else. The plugins send counts and times only, never a prompt, an answer or a token id.

Not tested yet on real engines: the night pass (`tools/proxy_engines_check.sh --plugins`) installs both plugins into the release images, checks that every row joins and that the plugin's sums over 12 requests equal the engine's own `/metrics` within 1 %, and measures the plugin's cost on vLLM: decode tok/s with and without it, three starts (without, with, without), 20 requests each, gate 1 %.

## Models, names and routing

`GET /v1/models` lists each backend's real models under their own ids (`deepseek-flash`, `Qwen/Qwen3.5-4B`), with `owned_by` set to the backend's id and a `tandem` block (backend, kind, label, upstream id, the provider's name of the model and its largest `max_tokens`, each null when the adapter has none, whether the backend is a cloud API, context length, switches). In the engine's server, `TandemLLM` stays the engine's own. When two backends serve one id, both are listed as `<backend>/<model>` and the bare id is not; the prefixed form is always accepted. A model can get a public id of its own in the GUI, and an output limit: the operator's comes first in `max_output`, before the provider's list and the adapter's table, and a discovery never overwrites it (a relayed vLLM model has neither of the other two).

What a provider's own model list says goes into the card. DeepSeek's `GET /models` (read 2026-10-17) gives each model a `name` (`DeepSeek-V4.1-Flash`, shown as "DeepSeek V4.1 Flash"), `context_window` (1,048,576), `max_output_tokens` (393,216), `input_modalities` (`text`, `image` on deepseek-flash; `text` on deepseek-v4-pro) and `effort` (`supported_levels` low, high, max; `default_level` high). The discovery every 10 minutes stores them per model; the card then carries `capabilities`: `vision` when the input kinds name `image`, `effort_levels`, `effort_default` and `effort_off` (the word that turns thinking off: DeepSeek's `reasoning_effort: none`). The Chat tab offers the provider's levels in its effort chip, sends `reasoning_effort` with one of them, and sends images (uploads and `render_page` screenshots) only to a model that reads them.

A model id a later discovery finds for the first time is stored disabled: it is not listed until it is turned on under Backends. The log says `[proxy] deepseek: new model <id> in /models, disabled until it is turned on under Backends`, and one push goes to the ntfy topic of the monitor's stall watchdog (its `url`, `token_file` and `ca_file`) when one is set. An id is new once; when it leaves the list and comes back, nothing is sent again. On 2026-10-17 `deepseek-v4.1-flash-1017` was in DeepSeek's list for a while at about 01:59 UTC and then gone.

Every relayed answer's `system_fingerprint` (OpenAI's and DeepSeek's name for the backend configuration that wrote it) goes into its ledger row (schema 10) and the request detail on Performance. The model id stays `deepseek-flash` while the snapshot behind it changes; a new fingerprint in the rows shows the change.

A request goes to, in this order: an explicit `<backend>/` prefix, the first enabled routing rule whose glob matches the id, the one backend that serves it, else OpenAI's 404 `model_not_found` (which names the prefixed forms when two backends serve the id).

A backend or a single model may name fallbacks. On a connect error, a 429, 502 or 503, before any byte reached the client, the proxy tries the next backend at once, or the same one again after 1 s, then 2 s (or its `Retry-After`, up to 30 s). At most 2 tries again in all. After the first byte a failure is an `error` frame and `[DONE]`. The ledger row records the backend that answered, `fallback_from`, `retries` and the upstream status.

The proxy adds no limit of its own to how many requests a backend takes at once: a cloud API, vLLM or SGLang gets as many as clients send, and answers its own 429 or 503 when it is full (that answer goes to a fallback as any 429 or 503 does). A backend's `max_inflight` (a number from 1 to 4,096 on its form, or `TANDEM_BACKEND_<ID>_MAX_INFLIGHT`) is a cap you set yourself: past it a request goes to a fallback with room, else it gets 503 with `Retry-After: 1`. Empty, `null` or `none` is no limit, the default. Until 0.3.44-test every backend got 64 without asking; a stored 64 became none when the config file moved to schema 2. How many requests TandemLLM's own engine decodes at once is the engine's setting, not a backend's: `QSE_PARALLEL` in its serve.env ([docs/parallel-requests.md](https://github.com/TandemLLM/TandemEngine/blob/main/docs/parallel-requests.md)), shown on the Backends screen under the engine's name. Timeouts per backend (local: connect 5 s, first byte 600 s, idle gap 120 s; cloud: 10, 120, 60 s) end a request with `finish_reason: timeout` and the kind in `error_type`. A client that leaves closes the upstream connection within a quarter second, so the backend stops generating. The standalone proxy (`python -m proxy`) closes a client's connection that sends nothing for 60 s while it reads a request, or sits idle that long between requests (`--idle-timeout`, `TANDEM_IDLE_TIMEOUT_S`; 0 for none). The timeout ends when the answer starts: a stream, the Chat tab's terminal included, is never cut by it.

## What the dashboard shows for a relayed request

Measured or null, never estimated:

| figure | from |
|-|-|
| prompt, completion tokens | the backend's `usage`; null when it sends none |
| cached prompt tokens | `prompt_tokens_details.cached_tokens` (OpenAI; vLLM with `--enable-prompt-tokens-details`; SGLang with `--enable-cache-report`), `prompt_cache_hit_tokens` (DeepSeek), `input_tokens_details.cached_tokens` (Responses) |
| reasoning tokens | `completion_tokens_details.reasoning_tokens` (vLLM with `--reasoning-parser`), SGLang's `reasoning_tokens`; a reported 0 beside streamed reasoning is null |
| TTFT | arrival at the proxy to the first frame with a token; the network and the backend's queue are inside it |
| decode tok/s | (completion tokens - 1) / (last token frame - first token frame); null without `usage`, because a frame is not a token |
| cost | the model's price when the row is written |
| proxy overhead | arrival to the upstream send, plus the first frame's delay through the proxy |

Queue, prefill time, blocks, draft acceptance and the cache source come off an engine's loop and are null for a relayed row; the views say "not reported by this backend". The Usage view filters by backend (`engine` is the engine's own rows) and by user, and shows the cost of the priced models. With a relayed backend in the ledger, Usage has a table of the engine and each relayed backend side by side (requests, tokens, decode and TTFT p50, cost), and Performance draws them apart: relayed requests are red dots of their own on a log axis in Recent requests, and a line of their own beside the engine's in the decode and TTFT history. The cache sources table and the engine's history count the engine's own requests only; the Live panel names a relayed request's backend and says its tokens are counted at its end.

## Every request at once: Performance, Parallel

With several backends, tens of streams run side by side. Performance has two pages: Engine (the engine's own requests, one at a time) and Parallel (`#/performance?tab=parallel`), which shows every request in flight on every backend, our engine included. It reads `GET /v1/dashboard/parallel` once a second (`tandemapp/parallel.py`, contract `parallel.schema.json`); the server builds the document at most every 0.4 s for all viewers, and a poll with `since` carries only the new series samples (about 25 KB a poll with 30 streams, under 2 ms to build).

The page has the aggregate decode rate now and its last five minutes stacked by backend, the requests by state (waiting, prefilling, thinking, tool call, writing), TTFT p50 and p90, the cache hit and the cost per minute over the requests finished in the last five minutes; one card per backend; and one lane per request: backend and model, client and session (`x-session-id`), state, tokens (thinking apart), decode tok/s now and on average, TTFT, prefill rate, cache, cost so far, elapsed. A lane keeps its row until a few seconds after it ends; a new request takes the first free row.

Live token counts, and where each comes from:

| backend | live count |
|-|-|
| the engine's own requests | the engine's count, prefill progress and rate, the state |
| a TandemLLM backend behind the proxy | the same: the relay sends `X-Tandem-Relay-Id`, the engine files the request under it, and the proxy reads the engine's `/v1/dashboard/parallel` once a second while it has requests there (it answers a trusted local caller or the admin token). The engine streams blocks of several tokens, so its frames are not a count |
| any other backend | its stream frames, only after they were checked: each finished streamed answer's frames are compared with its `usage.completion_tokens` (within 2 tokens and 1 %), answers with text only and answers with tool calls apart. Two passing answers of a kind and no failing one among its last 8 make those frames a count. DeepSeek's reasoning and content frames are one token each; its tool-call arguments arrive several tokens a frame, so a DeepSeek lane has no live count while it writes a call |

Without a count a lane shows its frames and "not reported"; its tokens and rate come with the usage at the end. A relayed request's prompt and cached tokens arrive with the usage too ("at the end"). Cost so far is the completion tokens so far at the output price, marked `+` until the usage adds the prompt's part.

`tools/parallel_load.py` drives the page: N agent-like sessions (a system prompt, two tools, up to three streamed turns each, tool results fed back) over the models you name, for example `--mix deepseek-flash=10,fake-a=18,TandemLLM=2 --duration 90 --budget-usd 0.5 --board`. It reports TTFT, decode rate, cache hit, throughput and cost per model, stops starting turns at the budget, and with `--board` puts the page's per-second series beside the frames its own clients received.

## Prices and cost

A streamed request with `X-Tandem-Cost: 1` (the Chat tab's chatd sends it) gets the request's cost as one SSE comment just before `data: [DONE]`: `: tandem-cost {"cost_micros": 6140, "backend": "deepseek"}`, every request the harness sent for it summed, the figure its ledger row gets; `cost_micros` is null without a price. SSE readers skip comments; without the header the stream is the backend's, byte for byte.

A price row per model holds USD per million tokens for input, cached input, cache write and output. The cost of a request is `(prompt - cached) x input + cached x cached input + cache write x its price + completion x output`, written into the row when the request ends, so a later price change never rewrites history. A model without a price has a null cost. DeepSeek charges half outside its peak hours (01:00-04:00 and 06:00-10:00 UTC on weekdays); a price row can carry an `offpeak` block, and the DeepSeek adapter picks by the request's UTC hour. Chinese public holidays count as peak here, so their cost reads high.

The proxy never fetches a price list. `proxy/prices.seed.json` holds the list prices of OpenAI and DeepSeek as read from their pages on 2026-10-04; the model rows offer them and apply them only on a click. Check the providers' pages before you bill anyone from these numbers.

## Users and their keys

Under Backends, Users, the admin makes a key per person or tool. The key is shown once and stored as its sha256. From the first key on, the OpenAI routes need one (`Authorization: Bearer tdm-...`) for every model, the engine's own included, from any machine other than this one: a request from loopback without proxy headers passes, so the engine's own helpers keep working. The key's label goes into the ledger's `user` column. `TANDEM_API_KEYS` adds keys from the environment.

## Who may change the configuration

Reading and writing `/v1/config` follow the admin rule ([docs/server.md](https://github.com/TandemLLM/TandemEngine/blob/main/docs/server.md#the-admin-rule)). With no admin password (`TANDEM_ADMIN_TOKEN`) set, the default, anyone who reaches the port can change the configuration from the Backends and Setup screens, which ask for no password. With one set, a write needs it as a bearer header or a dashboard session, or comes from the machine itself. The default bind is 127.0.0.1; the page is meant for a trusted network. Never put it on the internet.

## Terms, in short

Using your own key through your own proxy for your own clients is ordinary API use. Putting one key in front of several people makes you the provider toward them: the provider's terms hold the key holder responsible for what those people send, and most forbid reselling access without an agreement. Per-user keys keep each person's usage visible in the ledger. This is not legal advice.

## The harness in front of a backend

The conductor runs in front of other backends too: the router's task class, the effort, the packages and skills as notes, the side requests' caps, the session's memory and its `/s/<token>` address, the library and Laya, the loop guards, E1 and the tool checks, the compaction section and the state lines. In the engine's server it is the engine's own conductor (`--backends` with `--conductor-config`); the standalone proxy keeps one of its own under `--conductor-config` (default `~/.tandemllm/conductor`, `off` for none), with its journal in the usage ledger's file as on the engine. A backend's `harness.mode` turns it on: `off` (the default) is the relay above, byte for byte; `shadow` journals what it would do and changes nothing; `on` acts. `harness.profile` picks the conductor profile for that backend's requests (empty: by client, as on the engine). Both are on the backend's form and in `TANDEM_BACKEND_<ID>_HARNESS` and `_HARNESS_PROFILE`.

Per model, each feature group can be `inherit`, `off`, `shadow` or `on`, and the model can override the mode. A group is never above the mode. What `inherit` means:

| group | what it does over HTTP | local engines | cloud APIs |
|-|-|-|-|
| class | the router's question asked of the backend itself, with its own constraint field (vLLM `structured_outputs.choice`, SGLang `regex`, OpenAI a JSON-schema enum, plain text otherwise) | on | on |
| effort | the backend's own switch: `chat_template_kwargs.enable_thinking`, `reasoning_effort` (OpenAI's o-series and gpt-5 only), `thinking.type` (DeepSeek); a budget is a word, never a cap | on | on |
| packages | the router's note at the end of the newest user message, replayed on later turns at the same message index | on | off |
| side | a client's title or summary call capped (64 or 256 tokens) with thinking off | on | on |
| loops | D1 and D2 on the streamed words; the tool-argument guard on the streamed arguments | on | on |
| e1 | an empty answer to a request with tools gets a second request with a hidden user turn | on | off |
| tools | C1 to C4 on the answer's calls; a regenerate is a second request | on | shadow |
| compaction | the TandemLLM section appended to a compaction request | on | on |
| states | the state lines in the stream | on | on |
| memory | the memory card and notes at the end of the user message, the model's blocks taken out of the stream and written; the background writer as one more request in the backend's idle time (`TANDEM_BACKEND_<ID>_WRITER`: auto is off on a paid model; a daily spend and run cap; docs/memory.md) | on | shadow |

The Backends screen shows, next to each switch, what it resolves to, one sentence on what it costs or loses on that backend, and what its extra requests cost over the last 24 hours (the class questions are ledger rows of the client `harness`; a second request's tokens and cost are in its journal row). The Compare view lists shadow precision per backend.

### What is different from the engine

The note is text at the end of the user's message, inside the `<runtime-note>` frame the router uses, not ids before the end of the turn, so a model can quote it. The replay keeps the bytes of the conversation's head the same from turn to turn, so a backend with a prefix cache (vLLM, SGLang, OpenAI's and DeepSeek's prompt caches) keeps its match: the hash covers each message's role, content, name and tool calls up to the noted one. A client that rewrites its history breaks the hash; the note is gone and the router adds its block again, as on the engine.

The class question is a request of its own, a few hundred prompt tokens each. A new user message can ask up to three (the class, the package among several candidates, a library skill); a tool turn and a repeated message ask none. On the engine the same questions cost nothing beside the turn. With the harness on, `proxy_ms` includes the time the plan took, the questions included.

E1, the regenerate and the reasoning close are second requests. The client gets one stream: the second answer goes out as the continuation of the first, with the first chunk's id, no second role delta and one `[DONE]`. To make that possible the proxy holds the finish frame, the usage frame and `[DONE]` until the turn is settled, and holds the tool-call frames while a check can act. When the client asked for usage, the usage frame it gets is the sum of every request, and so is the ledger row. A reasoning close needs `continue_final_message` (the `vllm` and `sglang` kinds); on any other backend D1 and D2 are journaled in shadow with `no_continue_final_message`. A request that is not streamed gets the plan (the note, the effort, the side cap, the compaction section) but none of the monitor's acts, which read the stream.

### The word-level loop detector

D1's three arms (a repeated pattern, the repeat share of the history, the novelty of the last window) run over words instead of tokens (`tandemapp/conductor/monitor/tier0_words.py`), with numbers measured for words, never the token ones copied. `tools/loop_words_fp.py` streams the opencode database's reasoning through it. On 2026-10-04: 2,674 finished turns whose reasoning compresses under 5 times, and 20 loops (turns the token limit cut inside the reasoning, or reasoning that compresses 8 times or more). The strictest of 32 settings fires on 6 of the finished turns and catches 11 of the 20 loops. The engine's own D1 acted on that traffic, so some of those 6 may be loops it closed; the text alone does not say which. The shipped `words` block in `ops/supervisor.yaml` therefore says `calibrated: false`, and every D1 verdict over the proxy stays in shadow until the Compare view's labels show its precision.

### Laya, the library and the session address

The standalone proxy serves `GET /v1/library[/...]`, and `POST /v1/laya/ask` when the conductor's `decider` role names a Jev endpoint over HTTP. Both also answer under `/s/<token>/`, the address the notes give the model, and are journaled to that session. The `classifier` role may name a backend (`backend:<id>/<model>`) to answer the class question instead of the request's own backend, and the `narrator` role the same for the state lines, inside its latency budget; without one the lines are the templates.

## License

The proxy, the collector and the package are part of TandemLLM and carry its licenses: AGPL-3.0-only, or the commercial license in `COMMERCIAL-LICENSE.md`. Under the AGPL, anyone who runs a changed copy of the dashboard as a service for others must offer those users the changed source. The one runtime dependency, `cryptography`, is Apache-2.0 or BSD at the user's choice; the `host` extra adds psutil (BSD-3-Clause) and nvidia-ml-py (BSD-3-Clause). `NOTICE` lists them. None of their code is copied into this repository. The two plugins in `plugins/` are part of TandemLLM too, under the same license, and have no dependencies; vLLM and SGLang (Apache-2.0) are the engines they run in, not part of them.

## Not in this release

An Anthropic adapter, and a release on PyPI (the wheel builds; publishing it is a separate step). The cloud adapters and the harness's second requests are tested against the providers' documented shapes and a fake backend, not against a paid call or a real vLLM or SGLang; `tools/proxy_harness_check.py` runs the harness in front of a live TandemLLM engine. The per-process GPU memory on a discrete GPU is tested against a recorded NVML answer, not yet on such a machine. The vLLM and SGLang tables are tested on pages built from each release's own metric definitions, and the plugins on stand-ins built from each release's own classes; `tools/proxy_engines_check.sh --plugins` checks both against running servers.
