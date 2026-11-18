# Troubleshooting

Each entry is a symptom, what causes it, and what to do. `~/TandemLLM/bin/tandem logs` follows the server log on an installed engine, and `GET /v1/dashboard/logs` streams it. [TandemApp's docs](https://github.com/TandemLLM/TandemApp/blob/main/docs/README.md) has the dashboard's, the Chat tab's and the proxy's entries.

## Installing

**The installer stops: no tag, branch or commit 'v0.16.0'.** The repository it clones has no such tag. The installer clones the repository `--repo` names, the public one by default, at the tag `--ref` names, `v0.16.0` by default. To install from another repository, such as a fork, pass its address with `--repo`. The same missing tag makes `curl` answer 404 for the installer's own URL.

**`install.sh -{}-setup` refuses: the directory already has a configured engine.** A box that switched to a profile counts as configured, and `--setup` would stop that engine. To update it, add `--force-setup`, then run `use-profile.sh` with the profile again; its files are already on disk.

**`use-profile.sh` refuses the download: not enough disk.** The fetcher keeps 60 GB free after every download, per filesystem, and counts the profile's weights and the files beside them first. `--dry-run` prints each file with its size. Free the space, or pick a smaller profile: `exl3-2.5` needs 12.0 GB of weights beside the 27B's shared 40 GB.

**A download stopped halfway.** Run the same `use-profile.sh` command again. It keeps every file that finished and checked, pins the same commit, and continues; a folder appears only when all its files are there.

**The setup guide's community section says “no published list yet”.** GitHub answered 404 for `models/community-index.json` on the public repository's `main`: no release there carries the list yet. That is a note, not an error. The page shows the list from the box's last fetch, or the one that came with its release, and Refresh asks again ([Where the list comes from](community-weights.md)). A real failure shows one short line instead, such as “GitHub could not be reached”, and the server's log has the URL.

## The engine

**The start refuses: an engine is already alive.** One engine per board. Two engines loading side by side run a DGX Spark out of memory, and a board in that state stops answering until a power cycle. A loading engine counts although it answers nothing yet, because it binds its port only after the weights are in. `. ops/engines.sh; engine_pids` lists every engine process; stop the other one before starting. The same rule holds for a vLLM or SGLang on the same board: check the free memory first and keep at least 15 GiB of `MemAvailable` at the worst case.

**A request gets a 503 with Retry-After, and the log names the peak.** The memory gate ([The memory gate](server.md)). Before every prefill the engine gives memory back cheapest first until the prompt's workspace fits under the declared peak (77 GiB); when it still does not fit, it refuses rather than wedge the board. The System view's memory plan shows what holds memory: a small model beside the engine holds its claim, and a 262,144-token context holds about 13 GiB more than a 131,072-token one. If the refusals go on after a role loaded, unload it in the Conductor view or lower `QWEN38_SHADOW_GB`.

**A role does not load.** The residency planner refused it: `refused_memory`, `refused_hold` or `refused_load_in_progress`. It loads one model at a time, never during a hold, and only when the board keeps its floor and margin (15 + 6 GiB) after the engine's growth to its peak and the role's claim. The Deciders screen and the System view say why, and `residency.jsonl` in the conductor's directory journals each decision. The narrator's keeper tries again in the next idle window.

**The engine does not start: the image processor resolved to the pil backend.** torchvision is missing or too old. Install the pair that matches torch (`pillow==11.3.0`, `torchvision==0.28.0+cu130` from the CUDA 13.0 index), or start with `--image-backend pil`, or `--vision off` for text only. The installer's venv has both.

**A benchmark is slower than the paper, or its text differs.** With the conductor on, every request runs through the router: a class, an effort cap and a hidden note. Send `X-Tandem: raw`, or the profile `bench`, for the raw path. Then check the lookup corpus: the installer builds a public one, and the paper's corpus is not published. And check the store view: a store that has seen the benchmark's prompts inflates it ([Measurement](measurement.md)).

**A client asks for a model name the engine does not list.** With the conductor on, `/v1/models` lists `TandemLLM` alone, and any model name is served as `TandemLLM`. Nothing to fix.

**A turn waits a long time before its first token.** With one sequence (`QSE_PARALLEL=1`; the shipped profile runs 8 lanes) the engine serves one turn at a time. A streamed request behind a running turn gets an SSE comment every 10 s with its position and an estimate, and with the progress lines on a waiting line in the client; it waits as long as a request may run. A request without a stream gets a 429 after `--queue-timeout`. Short requests go through the side lane. A full queue (8) is a 503 at once.

**A turn waits for memory.** The chat or the client says it waits for memory, the response has `X-Tandem-Wait: memory`, or the Lanes view shows `waiting_memory`. The turn is paused and starts by itself once memory frees ([Waiting for memory](parallel-requests.md#waiting-for-memory)). Its line in the dashboard says what it needs, what holds the rest and when the wait ends; the engine log repeats it every 15 s. A streamed turn gives up only after `QSE_MEM_WAIT_S` (3 hours) with a 503 `memory_wait_timeout`; one without a stream after `--queue-timeout`, unless it sends `wait_for_memory: true`. A shorter conversation or fewer chats at once needs less memory, and a high priority lets the chat that matters go first. Since 0.15.4 a short turn on a long chat is charged the rows it forwards, so a wait of minutes for such a turn is worth a report with the exported log (`GET /v1/dashboard/logs/export`).

**The next turn re-reads the whole conversation.** Look at the request's cache source in the Performance view or the Dev view's detail: `end`, `resident`, `prefix`, `session`, `disk`, `spill`, `continuation`, or `none` for a cold prefill. A client that rewrites its history (another system text, a changed earlier message) parts from the cached rows where it changed. A short conversation started while a longer one is resident is a guest of it and re-reads its first answer at its second turn. [Caches](caches.md) lists each case. With `--parallel N`, each slot keeps the conversation it served last, and a turn goes back to the slot that holds its conversation (the request row's `slot`, the `[parallel]` log line). When memory ran short, the KV pool may have given that conversation's rows back: with the session spill on it wrote them to the NVMe drive (the SSD) first, and the turn reads them back with the cache source `spill` ([Live conversations on the NVMe](caches.md#live-conversations-on-the-nvme)). With the spill off it reads its prompt again. A new user message in Open WebUI after the agent changed directory used to part at the tool block (its working directory is in `run_command`'s description); under a conductor plan it no longer does (`QWEN38_CLIENT_CWD`). If the turn's source is `none` right after short requests of TandemLLM's own (`json` rows of the router), check `refused_stash` in `/health`'s resident report and `cache.memory` against the ceiling.

**An agent turn ends with no answer and no tool call.** The model wrote its tool call inside its thinking and stopped there. With the session monitor acting and E1 on in the request's profile (a profile stored before this release may have it off: set it to act on the Monitor screen), the call goes out as the answer when it parses and names an offered tool, under the line *tool call written in the thinking, sending it*; otherwise the turn is continued, and after two tries it ends with one sentence. On the raw path the engine returns what the model wrote.

**A long conversation shows X-Tandem-Images-Left-Out.** A request carries at most 16 images and 65,536 image rows. When the excess is in earlier messages, the oldest images are left out in steps of 8 and the request is served; only the newest message's own images over a cap are a 400.

**The dashboard says the engine is stopped for maintenance or starting.** The engine is away and the reverse proxy shows the status responder's page ([While the engine is away](operations.md#while-the-engine-is-away-status-page)): the reason, since when, about how long. It opens the dashboard by itself when `/health` answers.

**A request answers 400 and `param` names a field.** The request's shape is wrong: no user message, an unknown role, a late system message, `max_tokens` below 1, an empty prompt ([What the API supports](server.md#what-the-api-supports)).

**Open WebUI shows a literal `</think>` while an answer streams.** Open WebUI 0.11.3 shows the stream piece that carries `</think>` as message text until the round ends. Under a conductor plan the engine sends Open WebUI its reasoning in `reasoning_content`, which has no tag; a request with `reasoning_format: tags`, the raw path, or `QWEN38_CLIENT_REASONING` set empty bring the tags back. The saved chat is clean either way.

**The model writes “Laya unavailable”.** Its scripts could not reach the engine from where they run. Set `QWEN38_TANDEM_URL` to an address that machine reaches.

## Memory notes

**The model never writes a note.** Every shipped profile has `memory: off`; set it to `shadow` or `on`. The background writer has its own switch, `settings.memory.notes.writer.mode`. The log says why a due session was not written: `hold`, `gpu`, `nothing`, `moved` or `no session`.

**Behind the proxy the writer never runs on a cloud model.**

`auto` is off on a paid cloud model. Switch it on for that backend, give the model a price, and check the day's caps on the backend's page.

**A note was refused.** Caps refuse, never cut; a body that is mostly a secret is refused; a rule about the user needs the user's own message in the turn. The Notes tab lists the refusals with their reasons.

