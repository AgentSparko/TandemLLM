# Reference: commands

`tools/gen_reference.py` builds the reference tables from the code: it parses the argument parsers, the settings registry, the metric registrations and the ledger's schema with Python's `ast` module and never imports the project. `tests/test_docs_reference.py` runs it with `--check`, so a table that no longer matches the code fails the tests. A description is the first sentence of the flag's own help text; where the code gives none, the generator carries a short one. A default is the code's; where the served profile passes another value, the cell says “served:” and that value, as `ops/start.sh` builds it from `ops/serve.env`.

## The engine's server

`python server/app.py [flags]`, or `ops/start.sh`, which builds the flags from `serve.env`. `python server/app.py --help` prints the full help texts.

<!-- generated: server-flags by tools/gen_reference.py; do not edit by hand -->

### Every flag of the engine's server, server/app.py, with the value the served profile passes where it differs

| Flag | Default | What it does |
|-|-|-|
| `--model` | `none` | the checkpoint directory of the target model. |
| `--served-model` | `qwen3.8-27b-spark-engine; served: qwen38-spark-engine` | the raw engine's model name; with the conductor on it is a hidden alias of TandemLLM. |
| `--host` | `127.0.0.1; served: 0.0.0.0` | the address to bind. |
| `--port` | `8000` | the port to listen on. |
| `--max-len` | `8192; served: 262144` | context length. The KV buffer is allocated for all of it up front, so this is a memory decision as much as a capability one: 64 KiB a token of engine KV plus 20 KiB a token per drafter arm, 27.9 GB of KV and 53.4 GB of whole ... |
| `--rep-penalty` | `1` | deterministic repetition penalty on the target's logits, before the argmax (1.0 = off, today's output). |
| `--presence-penalty` | `0` | flat subtraction from tokens already in the history (0 = off). |
| `--frequency-penalty` | `0` | subtraction per occurrence, on tokens already in the history (0 = off). |
| `--temperature` | `0` | server default sampling temperature (0 = greedy, the exact path). |
| `--top-p` | `none` | server default for top\_p. |
| `--top-k` | `none` | server default for top\_k (0 is off). |
| `--no-repeat-ngram` | `0` | forbid the token that would complete an n-gram already seen (HF's no\_repeat\_ngram\_size; 0 = off, must be &gt;= 2). |
| `--pattern-stop` | `none` | end a generation that repeats one short pattern forever, as MAX:MIN:COUNT (e.g. 64:1:8, a block of 1..64 tokens repeated 8 times ends the generation with finish\_reason stop). |
| `--argloop` | `on` | the tool-argument loop guard (server/api/argloop.py): `on` stops a held tool call whose arguments loop and tells the model on its next turn. One of: on, log, off. |
| `--default-max-tokens` | `8192; served: 32768` | what a request that does not send max\_tokens gets. |
| `--reasoning-format` | `tags` | how the reasoning block reaches the client: inside &lt;think&gt;&lt;/think&gt; in content (default, and what Open WebUI folds on), as OpenAI-style reasoning\_content deltas, or both. One of: tags, reasoning\_content, both. |
| `--drafter` | `mtp` | the drafter set. lenrouter, the served one, holds both DFlash2 widths beside the lookup drafter; none decodes one token a step. One of: mtp, router, merged, dflash2, lenrouter, none. |
| `--tree` | `off (a switch)` | verify a draft TREE per step instead of a chain, where the drafter builds one; see docs/speculative-decoding.md. |
| `--regenerate` | `sample (QWEN38_REGENERATE)` | : a regenerate (the same conversation again after it was answered, under a TandemLLM plan; or X-Tandem-Regenerate: 1) samples with a fresh seed; every other request keeps its own sampling (greedy by default). One of: sample, off. |
| `--regenerate-temperature` | `0.7 (QWEN38_REGEN_TEMPERATURE)` | a regenerate's temperature (QWEN38\_REGEN\_TEMPERATURE). |
| `--regenerate-top-p` | `0.95 (QWEN38_REGEN_TOP_P)` | a regenerate's top-p (QWEN38\_REGEN\_TOP\_P). |
| `--regenerate-top-k` | `20 (QWEN38_REGEN_TOP_K)` | a regenerate's top-k (QWEN38\_REGEN\_TOP\_K). |
| `--sampled-tree` | `none; served: det` | let a SAMPLED request keep the tree verify. One of: det, mixed. |
| `--budget` | `16` | nodes per tree, anchor included. |
| `--df2-temp` | `1` | the block drafter's temperature for its own proposals. |
| `--corpus` | `none (QWEN38_CORPUS)` | the lookup drafter's corpus: a directory of token ids with a suffix array (tools/build\_corpus.py). |
| `--depth` | `3` | the drafter's depth for the older chain drafters. |
| `--k` | `0` | verify block size; 0 = the drafter's depth. |
| `--draft-head` | `none` | an optional separate head file for the drafter. |
| `--nvfp4` | `none` | the NVFP4 weight files to load, one per target (mlp, gdn, attn); empty means the checkpoint's FP8. |
| `--price-table` | `none (QSE_PRICE_TABLE)` | FILE[:NAME], the routers' verify/draft/rollback prices for this weight set (engine/prices.py); empty = the NVFP4 constants in the code. |
| `--fp8-head` | `none` | e4m3 lm\_head from tools/quant\_head.py; halves the head's 2.54 GB in both the verify pass and the block drafter's top-k read. |
| `--dflash2-blocks` | `1` | chained 8-wide draft blocks; 1 proposes 7 tokens, 2 proposes 14. |
| `--dflash2-path` | `greedy` | how the block drafter walks its lattice: the greedy path or Viterbi. One of: greedy, viterbi. |
| `--dflash2-ckpt` | `none` | the narrow (block 8) DFlash2 drafter checkpoint. |
| `--dflash2-ckpt16` | `none` | the sixteen-wide drafter, for --drafter lenrouter. |
| `--len-fixed` | `0` | pin --drafter lenrouter to one block width (8 or 16). 0 lets the policy choose, which means --len-latch: one decision a request. 8 and 16 measure the fixed baselines through the same code. |
| `--len-latch` | `on (--no-len-latch turns it off)` | decide the block width ONCE a request, four wide blocks, up to four narrow probes, then no more switching. |
| `--learn-cost` | `on (--no-learn-cost turns it off)` | the length router learns its verify and draft costs from the blocks it times, across requests (default). --no-learn-cost keeps the served price table; timings inside a request still size its blocks, so two runs can part at a near tie. |
| `--len-explore` | `32` | blocks between forced wide probes when nothing suggests one. |
| `--deep` | `0 (QWEN38_DEEP)` | with --drafter lenrouter --tree: after --deep-after wide blocks in a row commit their whole width, propose the lookup drafter's long exact continuation of this request's text as one chain of up to this many rows. 0 = off. |
| `--deep-after` | `2 (QWEN38_DEEP_AFTER)` | full wide blocks in a row before a deep chain (2 keeps it off new text). |
| `--narrow-arm` | `on` | with --drafter lenrouter: build the block-8 drafter arm (on, the served default) or run the block-16 arm alone. One of: on, off. |
| `--drop-idle` | `off (--drop-idle turns it on); served: on` | release the arm that LOSES the latch for the rest of the request: no tap, no sync, and nothing of it in the state snapshot. |
| `--relax-tau` | `1` | Breaks the agreement rule: accept a drafted token whose probability is at least this fraction of the argmax's. 1.0, the default, keeps the rule. |
| `--relax-rank` | `1` | Breaks the agreement rule: accept a drafted token among the target's top r. 1, the default, keeps the rule. |
| `--think-stall` | `on` | with --conductor shadow or on, run the reasoning-loop arms of the policy's reasoning\_loop task (a pattern repeated over 128+ tokens, the history-wide 16-gram repeat, novelty collapse); the monitor decides whether a hit closes the ... |
| `--no-think-stall` | `sets think_stall off` | never run the reasoning-loop arms (the budget still applies). |
| `--conductor` | `raw` | the second-level harness (TandemApp's tandemapp/conductor/monitor/). raw, the default (off is the same): nothing of it runs and the output is the raw engine's. shadow: every rule runs and journals, nothing is done. on: each task ... One of: raw, off, shadow, on. |
| `--conductor-policy` | `none` | the monitor's policy file (default TandemApp's ops/supervisor.yaml), validated at start against TandemApp's ops/supervisor.schema.json. |
| `--conductor-watch-s` | `30` | with --conductor shadow or on, how often the stall watchdog's watch looks for sessions whose client has gone. |
| `--think-budget` | `0` | default cap on reasoning tokens per request, 0 = uncapped. |
| `--answer-reserve` | `auto (QSE_ANSWER_RESERVE)` | tokens of the output limit kept for the answer when a client asks for a thinking budget in any shape (server/api/thinking.py): its reasoning closes early enough to leave them. auto = min(2048, a quarter of the limit), 0 = none ... |
| `--reasoning-effort` | `none; served: medium` | default `reasoning_effort` for the chat template. One of: none, off, low, medium, xhigh. |
| `--cache-budget-gb` | `2` | RAM budget for the state cache, in GiB (CACHE\_GB). |
| `--store-entry-gb` | `1` | GiB one state-store snapshot may take (STORE\_ENTRY\_GB), whatever budget is free: 1 GiB is about 7k tokens. |
| `--no-session-cache` | `off (a switch)` | do not keep a conversation's state after its turn. |
| `--no-prefix-cache` | `off (a switch)` | do not checkpoint a prefill at chunk boundaries. |
| `--resident-gb` | `2.5` | GiB for the resident prefix's anchors (RESIDENT\_GB), the recurrent state (146.8 MiB) at chunk boundaries of the prompt the KV buffer holds, so the next turn of a growing conversation resumes in place, KV not copied, at any length ... |
| `--resident-stash-gb` | `1` | GiB for the rows a short unrelated request between two turns may overwrite, copied aside and back (RESIDENT\_STASH\_GB; ~104 kB a row: 1 GiB holds a --resident-guest-max guest and its 512-row slack). |
| `--resident-guest-max` | `8192` | the longest request that is a guest (RESIDENT\_GUEST\_MAX): opencode's title call is 1-3k tokens; a longer one replaces the resident prompt with its head pinned. |
| `--resident-tail` | `4` | the newest anchors never evicted (the next turn resumes near the end). |
| `--resident-pin-gb` | `4` | GiB for the head of the resident prompt a long unrelated request would otherwise cut (RESIDENT\_PIN\_GB): its KV rows and the anchors in it are copied aside, and a later request that shares more with that head than with the rows in ... |
| `--resident-park-gb` | `4` | GiB for one other session's rows (RESIDENT\_PARK\_GB): when a request of another session (x-session-id) replaces the resident prompt, the rows past their shared head are copied aside with its newest anchors, and that session's next ... |
| `--end-anchor` | `on` | the resident prefix's end anchor (END\_ANCHOR): at the end of every generation the recurrent state at its last position (147 MiB, one per resident prompt), so the next request that starts with the prompt and every token the engine ... One of: on, off. |
| `--compaction` | `continue (QSE_COMPACTION)` | a client's compaction request (QSE\_COMPACTION, server/api/compaction.py): continue = served as the continuation of the conversation the engine holds for its session (the prefill reads only the instructions); client = as the ... One of: continue, client. |
| `--compaction-prepare` | `on (QSE_COMPACTION_PREPARE)` | write an idle opencode session's summary ahead, so its /compact is answered without the engine (QSE\_COMPACTION\_PREPARE); a request that waits stops it at the next chunk or block. One of: on, off. |
| `--compaction-idle-s` | `30 (QSE_COMPACTION_IDLE_S)` | seconds without a request before a summary is prepared. |
| `--compaction-min-tokens` | `32768 (QSE_COMPACTION_MIN_TOKENS)` | the shortest conversation a summary is prepared for. |
| `--end-align` | `on` | the end anchor for a copy whose whitespace is not the rows' (END\_ALIGN, server/endalign.py): a follow-up whose copy of an earlier generation differs from what the engine wrote in whitespace alone (a tool call's trimmed parameter ... One of: on, off. |
| `--mem-ceiling-gib` | `70` | GiB the engine may hold between requests, allocated plus the allocator's reserve (MEM\_CEILING\_GIB): a cache grows only while the engine stays under it less --reserve-slack-gib, and over it the caches are given back, cheapest ... |
| `--mem-peak-gib` | `77` | GiB the engine may hold during a prefill (MEM\_PEAK\_GIB): the ceiling plus the workspace of a 262,144-token prompt. |
| `--reserve-slack-gib` | `2` | GiB of free blocks the allocator may keep between requests (RESERVE\_SLACK\_GIB); over it they go back to the driver once a request has finished, never inside one. |
| `--prompt-token-cache` | `4` | millions of tokens of tokenised chat messages kept, so a growing conversation's prompt is tokenised only where it is new (server/tokcache.py). 0 = tokenise every prompt whole. |
| `--session-trim` | `off` | cut the engine back to the committed tokens before the session snapshot when the last verified block ran past them (end token, stop, budget) and its commit is still pending, so the put is not declined. One of: on, off. |
| `--store-boundaries` | `1` | chunk boundaries of one prefill the state store keeps: its longest ones under the per-entry cap. 0 = every boundary, where one cold prefill's nested snapshots fill the budget and evict everything else. |
| `--prefill-heartbeat-s` | `5` | a streamed request whose prefill has run this long gets an SSE comment (': prefill done/total') after every chunk, at most once a second, so a client and every proxy between see the stream is alive. |
| `--live-activity` | `on` | the live activity on /v1/dashboard/live (states, prefill progress, timeline, the last 20 stops) at QSE\_LIVE\_HZ events a second while busy. off = contract 1.1 with those fields null at one event a second: the kill switch, and the ... One of: on, off. |
| `--max-prefill-rows` | `8192` | with the prefix cache off, forward a long prompt in chunks of this many rows instead of one call; a 131k-token single forward exhausted the board. 0 = one call. |
| `--prefix-chunk` | `1024` | tokens between prefill checkpoints, and the forward size of EVERY prefill while the prefix cache is on, the two have to agree or a warm prefill is not the same arithmetic as a cold one. |
| `--response-cache` | `off (a switch)` | OPT-IN. Answer an identical (prompt, params) request from memory. |
| `--response-cache-mb` | `256` | the response cache's budget in MiB. |
| `--response-cache-ttl` | `3600` | seconds a response cache entry is kept. |
| `--suffix-store` | `~/.qwen38-spark-engine/suffix (QWEN38_SUFFIX_STORE)` | directory for the persistent suffix store of what this engine has read and written, which the lookup drafter reads as a second corpus. |
| `--suffix-store-readonly` | `off (a switch)` | read the suffix store and never append to it: a fixed benchmark measured against a store of real traffic must not write itself into it. |
| `--suffix-store-mb` | `192` | cap on the store, in MiB of int32 token ids (192 MiB = 48 M tokens). |
| `--disk-cache` | `off; served: (a path)` | the disk tier: a directory on the NVMe for prompt-prefix state, prefixes prefilled more than once, a session's first long request, under the caches in RAM, or off (the default). |
| `--disk-cache-min-tokens` | `4096` | the shortest prefix the disk tier writes an anchor for. |
| `--conductor-defaults` | `builtin; served: shipped` | shipped, the live documents are stored as version 1 for every document the conductor's directory has no version of; builtin, the minimal built-ins stay version 0. One of: builtin, shipped. |
| `--session-spill` | `on` | a live conversation on the NVMe, under the disk tier's directory: written whole at a planned stop and read back at the next start, written before another client's request evicts it, read back at its next turn. One of: on, off. |
| `--session-spill-gb` | `64` | the session spill's own budget on the NVMe (a 256k conversation is about 27 GB as it is in the engine, about 16 GB compressed); the disk tier's free-space floor holds for both; settings.memory.session\_spill overrides it live. |
| `--session-spill-codec` | `zstd` | new spill blocks as byte planes compressed with zstd (exact under the agreement rule, 1.71x on a real entry; blocks on the disk are read either way). One of: zstd, off. |
| `--disk-write-budget-gb` | `200` | the engine's NVMe writes a local day past which optional writes stop (the tier's, and the spill's but a drain and evictions of 32k+ tokens; past 1.5x only the drain); 0 = no budget; settings.memory.disk\_writes.budget\_gb overrides ... |
| `--session-spill-idle` | `on` | write every slot's conversation of 4,096+ tokens to the NVMe once it is idle (its rows stay in RAM), so a later reclaim under memory pressure only trims it; optional under the day's write budget. One of: on, off. |
| `--session-spill-idle-s` | `20` | how long a conversation is idle before the idle backfill writes it. |
| `--session-spill-idle-every-s` | `60` | the least time between two idle writes of one conversation. |
| `--session-spill-resave-s` | `300` | : an eviction, switch or idle save of a conversation whose entry was written this recently is skipped while it grew by fewer than --session-spill-resave-min-tokens (its entry stays; a restore prefills the rest); 0 = always write. |
| `--session-spill-resave-min-tokens` | `8192` | : the growth past the entry from which such a save runs anyway. |
| `--session-spill-min-tokens` | `16384` | the shortest resident conversation an eviction writes to the NVMe (shorter ones are the RAM park's). |
| `--disk-cache-head-anchors` | `4` | anchors a session's first long request gets, at the grid lines below where its last user message starts (where the next session parts). |
| `--suffix-store-scope` | `all` | `all` remembers prompts and answers, `outputs` only what the engine wrote. One of: all, outputs. |
| `--request-timeout` | `900; served: 3600` | wall-clock cap on one generation, checked between blocks; 0 disables. |
| `--max-queue` | `8` | requests allowed to wait for the engine before new ones get a 503 with Retry-After. |
| `--queue-timeout` | `120; served: 240` | how long a request waits for the engine before a 429. |
| `--side-lane` | `on` | a short request, a client's side request or a raw one, thinking off, within the two caps below, is served at the next safe point of the generation that holds the engine (on: between decode blocks and between prefill chunks ... One of: on, decode, off. |
| `--side-lane-prompt-max` | `2048` | the longest prompt the side lane serves, in tokens. |
| `--side-lane-new-max` | `64` | the most output tokens the side lane serves. |
| `--parallel` | `1 (QSE_PARALLEL)` | sequence slots: 1 (the default) is one sequence, as always. |
| `--side-max-len` | `0 (QSE_SIDE_MAX_LEN)` | a side slot's window in tokens; 0: --max-len, every slot the same window. |
| `--parallel-floor-gb` | `15 (QSE_PARALLEL_FLOOR_GB)` | admission: MemAvailable less the caches' growth, the requests in flight and this request's worst case must stay at or above this; a side slot is built only if it does with the slot's bytes added. |
| `--parallel-batch` | `on (QSE_PARALLEL_BATCH)` | on: decoding slots verify their trees in one joint pass (lockstep rounds); off: they take turns, one verify each. One of: on, off. |
| `--parallel-draft` | `on (QSE_PARALLEL_DRAFT)` | with the joint verify on: the decoding slots' draft calls in one packed pass over the drafter (on), or one after the other (off). One of: on, off. |
| `--parallel-switch-ms` | `0 (QSE_PARALLEL_SWITCH_MS)` | with side slots: the interpreter's thread switch interval in ms (0 keeps Python's 5 ms); a woken slot waits up to this long for the GIL. |
| `--parallel-cut` | `none (QSE_PARALLEL_CUT)` | how a joint pass's rows are shared: share (each slot cuts its tree to a fixed share before it submits: 16 at two slots, 10 at three, 8 at four), joint (the round cuts every slot's whole tree together, best nodes first), or auto ... One of: share, joint, auto. |
| `--parallel-max-rows` | `0 (QSE_PARALLEL_MAX_ROWS)` | rows of a joint pass at most; 0 (the default): the model family's (`joint_policy`, else 32, the row-invariant kernels' range).: the 27B on EXL3 weights takes 64, each engine's joint rows bit for bit its own verify. |
| `--parallel-draft-rows` | `128 (QSE_PARALLEL_DRAFT_ROWS)` | rows of a packed draft pass at most: drafting decides speed only, so the drafter's pass is not held to the verify's row-invariant range; at 128, eight slots' 16-row blocks draft in one pass (32 packed two). |
| `--batch-timeout-ms` | `2` | a joint round runs without a missing slot once the GPU turn has been idle this long. |
| `--turn-quantum-ms` | `50` | the GPU turn goes to a waiting slot this far behind in GPU time. |
| `--parallel-settle` | `on` | synchronize the device before the turn changes hands (the routers' timings then hold only their own slot's work). One of: on, off. |
| `--side-precapture` | `off` | capture a side slot's verify graphs at start (on, about a minute a slot) or at their first use (off: the build takes seconds; each width's first lone round on a side slot pays its capture, a fraction of a second). One of: on, off. |
| `--side-graphs` | `on (QSE_SIDE_GRAPHS)` | a side slot's own verify graphs: on (captured as --side-precapture says; most of a slot's constant bytes) or off (the slot verifies eager when it decodes alone and holds no graph pool; a joint pass is eager either way). One of: on, off. |
| `--side-slot-const-gb` | `0 (QSE_SIDE_SLOT_CONST_GB)` | a side slot's bytes beyond its window's, in the build's estimate; 0 (the default) follows --side-graphs: 7.5 GiB with it on (6.9 to 7.5 GiB allocated measured for 8,192-token slots, most of it the verify graphs' pools: 121 ... |
| `--kv-pool` | `off (QSE_KV_POOL)` | on: every slot's KV window (engine attention KV and the drafters' context KV) is reserved as virtual addresses and its pages are committed as a conversation's rows are written and given back when it ends, from one pool for every ... One of: on, off. |
| `--kv-hard-floor-gb` | `10 (QSE_KV_HARD_FLOOR_GB)` | with --kv-pool on: a commit never takes MemAvailable under this (GiB); the pool gives back caches and idle conversations first, else the request is not run (admission keeps requests out at --parallel-floor-gb). |
| `--kv-margin-rows` | `256 (QSE_KV_MARGIN_ROWS)` | with --kv-pool on: rows committed past every write (a masked tile's over-read stays on mapped pages). |
| `--vision` | `on; served: off` | load the checkpoint's vision tower (0.92 GB bf16) and accept image\_url parts in chat requests; off refuses them with a 400. One of: on, off. |
| `--image-max-mb` | `20` | largest image a request may send or link, after base64 decoding. |
| `--image-max-decode-pixels` | `64000000` | largest canvas an image may declare, checked before its pixels are decoded; the processor then resizes to its own limits. |
| `--image-max-pixels` | `0` | the processor's resize target cap in pixels (0: the checkpoint's preprocessor\_config.json, e.g. 16,777,216 = 16,384 rows an image). |
| `--image-backend` | `torchvision (QSE_IMAGE_BACKEND)` | the image processor's backend, pinned so the patches do not depend on what is installed (the two differ by up to two 8-bit levels after a resize). torchvision (the default, what every served measurement ran) needs torchvision ... One of: torchvision, pil. |
| `--max-images` | `16` | images one request may send. |
| `--max-image-rows` | `65536` | prompt rows all of a request's images may take together (one row per 2x2 block of 16-pixel patches; a 1024x1024 image is 1024 rows). |
| `--image-https` | `on` | fetch https:// image URLs (off: data: URLs only). One of: on, off. |
| `--image-https-private` | `off` | let https image URLs reach loopback, private and link-local addresses (off: public hosts only). One of: on, off. |
| `--image-fetch-timeout` | `15` | seconds for a whole https image download, redirects included. |
| `--image-cache-mb` | `512` | encoded images kept on the device by content, so a conversation that sends an image again does not encode it again. |
| `--image-act-gb` | `6` | admission: an image whose encode would need more than this (the tower's byte math, engine/vision.py) is refused with a 400. |
| `--usage-default` | `on (QSE_USAGE_DEFAULT)` | a streamed request that sends no stream\_options gets usage, timings and metrics on its finish chunk (on, the default: Open WebUI asks for include\_usage only when a model's Usage capability is ticked), or nothing, as before (off). ... One of: on, off. |
| `--usage-context` | `client (QSE_USAGE_CONTEXT)` | usage.prompt\_tokens counts the client's prompt (client, the default) or the full context occupied after replay (engine). One of: client, engine. |
| `--usage-ledger` | `off; served: (a path)` | the SQLite usage ledger's path, or off (the default). |
| `--conductor-config` | `off` | the conductor's config directory (versioned profiles, router, roles, packages and its journal, TandemApp's tandemapp/conductor/), or off (the default). |
| `--backends` | `off` | off, the only value this engine takes: any other stops the start with one line. |
| `--usage-retention-days` | `400` | days the usage ledger keeps its rows (at least 400). |
| `--trust-loopback` | `on (--no-trust-loopback turns it off)` | a request from loopback with no X-Forwarded-For / X-Real-IP header (the box's own watchdog, row3, gate, soak) needs no token for /metrics, the full /health and the cache routes once an admin password or metrics token is set. |
| `--dashboard-login` | `off (QSE_DASHBOARD_LOGIN)` | on, with an admin password (QSE\_ADMIN\_TOKEN) set: /v1/dashboard/\* needs the admin token or a session too (the page has no sign-in screen yet). off, the default: the dashboard's read API answers without a token. One of: on, off. |
| `--setup` | `off (a switch)` | setup mode (docs/setup.md, `ops/start.sh --setup`): no served model is loaded; the Setup Guide's API /v1/setup/\* and Wren, its assistant, alone (`tandem setup` in a terminal, or TandemApp's Setup page pointed at this engine). |
| `--fake-engine` | `off (a switch)` | the e2e: no weights, no GPU, a deterministic CPU token source (server/fake\_engine.py) behind the real handler, auth, ledger, metrics and logs. |
| `--fake-tps` | `200` | with --fake-engine: tokens a second it writes. |
| `--fake-prefill-tps` | `0` | with --fake-engine: the prefill rate of a FAKE\_SLOW\_PREFILL prompt (0 = 2,000 tokens a second). |
| `--fake-load-s` | `0` | with --fake-engine: a load of this many seconds before the port opens, writing the load's phases as the real engine does. |
| `--structured-outputs` | `on (--no-structured-outputs turns it off)` | serve response\_format json\_object / json\_schema and structured\_outputs (regex, choice, json) as masks over the target's rows, exact under speculation. |
| `--log-content` | `off (a switch)` | let exception messages that quote a request into the log. |
| `--log-request-keys` | `off (a switch)` | one [body] line per request with the parameter names and the scalar values of the non-content ones (model, stream, max\_tokens, temperature,...); never messages, prompt, tools or stop strings. |
| `--verbose` | `off (a switch); served: on` | more log lines. |
| `--stage` | `none` | serve the layers [LO, HI) of --model as one of two stage processes: 0:k is the front stage, k:layers the tail, the one a client talks to. |
| `--link-listen` | `none` | with --stage: wait for the other stage on this address (an IP literal). |
| `--link-peer` | `none` | with --stage: connect to the other stage at this address (an IP literal). |
| `--stage-graphs` | `on` | with --stage: a stage whose family captures graphs for its passes captures its own (on), or runs every pass eager (off). One of: on, off. |
| `--stage-caches` | `off` | with --stage: keep state on both stages under one key: the prefix, session and resident caches, the disk tier and side slots; off: one sequence, nothing kept. One of: on, off. |
| `--stage-snapshots-gb` | `2` | with --stage-caches on, the front stage: memory for the states it keeps, a slot. |
| `--stage-cache-dir` | `none` | with --stage-caches on, the front stage: the directory for its half of what the tail's disk tier keeps (none: a state read from the tail's disk is rebuilt or missed). |
| `--stage-cache-gb` | `32` | with --stage-cache-dir: the directory's size; the files unused longest go first. |
| `--link-key-file` | `none` | with --stage: the key both stages share, a file of mode 0600 with at least 32 bytes. |

<!-- /generated -->

## The installer

`install.sh [options]`; [install.md](../install.md) explains the install.

<!-- generated: install-flags by tools/gen_reference.py; do not edit by hand -->

### install.sh options

| Option | What it does |
|-|-|
| `--dir DIR` | install directory (default ~/TandemLLM) |
| `--port PORT` | the API port (default 8000) |
| `--host ADDR` | the address to bind (default 127.0.0.1; 0.0.0.0 serves the LAN, with no auth on the OpenAI routes) |
| `--ref REF` | the engine release: tag, branch or commit (default v0.16.0) |
| `--repo URL` | the git repository to clone (default the public repository) |
| `--hf-token TOKEN` | a Hugging Face token (or set HF\_TOKEN; a stored 'hf auth login' works too) |
| `--no-corpus` | skip the lookup corpus (the lookup drafter then copies from the conversation only) |
| `--no-start` | install and configure, but do not start the engine |
| `--with-chat` | the chat (beta) too: the conductor (router, user memory, packages), its small models, and TandemApp's TandemNode with chatd, the Chat tab's service; needs --app-wheel (a re-run keeps the TandemApp it installed). Without it a new install serves the engine alone (no page, no chat) and a re-run keeps the mode |
| `--app-wheel FILE` | with --with-chat: TandemApp's wheel, a local file (tandemapp-\*.whl), into this install's venv (the engine's pins held) |
| `--app-wheel-sha256 HEX` | the wheel's sha256, checked before it is installed |
| `--engine-only` | the engine alone, a new install's default; on a re-run it turns the chat off |
| `--setup` | set up instead of a model now: install the code and Wren's model only, then start setup mode (the Setup API, /v1/setup/\*, for TandemApp's Setup page; docs/setup.md); 127.0.0.1 unless --host says otherwise |
| `--force-setup` | with --setup, on a directory that already has a configured engine |
| `--systemd` | also install a systemd user unit, tandemllm.service (off by default) |
| `--runtime-only` | the engine's source and Python environment only: no weights, no corpus, no config, no start (what TandemApp's Add Spark wizard installs on a new node) |
| `--update` | an install with its config: the source at --ref and the venv, then the config rendered again from the new release with this install's keys as they are; no weights, no corpus, no start (tandem remote upgrade runs it) |
| `--no-fetch` | take --ref from what DIR/src already has; no git fetch |
| `--no-pair` | no pairing window at the end; a new install then binds 127.0.0.1 and writes no HTTP token lock (bin/tandem pair opens a window later: docs/pairing.md) |
| `--retire-backends` | on a re-run whose config sets QSE\_BACKENDS (the relay the engine had before engine API 1.8): once its backends are on TandemApp's controller, comment the line out (dated) and go on; the backends' database stays |
| `--yes, -y` | non-interactive: answer yes to every question |
| `--dry-run` | print every step and command, change nothing |
| `--uninstall` | stop the engine, remove the unit and DIR (the weights stay in the HF cache) |
| `-h, --help` | this text |

Environment variables the installer reads: `TANDEM_REF`, `TANDEM_REPO`, `TANDEM_DIR`, `TANDEM_PORT`, `TANDEM_HOST`, `HF_TOKEN`, `HF_HOME`.

<!-- /generated -->

## TandemApp

`tandem-app`, `tandem-app launch` and `tandem-app hostcollect` are TandemApp's commands: [TandemApp's docs](https://github.com/TandemLLM/TandemApp/blob/main/docs/README.md) lists their flags.

## The service scripts

| command | what it does |
|-|-|
| `bin/tandem start\|stop\|restart\|status\|smoke\|logs` | the installed engine's control script ([install.md](../install.md)) |
| `ops/start.sh` | starts the engine from `serve.env`, waits for `/health`, drops the weights' page cache, loads the roles |
| `ops/stop.sh [seconds]` | SIGTERM, the drain, then a kill only after the wait |
| `ops/watchdog.sh` | one health check, for cron every minute |
| `ops/hold.sh <minutes> <command>` | a command with the board to itself, then the service back |
| `ops/gate.sh <label> [--profile F]` | the release gate |
| `ops/make-secrets.sh [--admin] [--rotate]` | the metrics token; `--admin` adds the admin password |
| `ops/roles.sh load\|up\|down\|settle` | roles beside the engine, through the residency planner |
| `. ops/engines.sh; engine_pids` | every engine process on the board |

*The scripts in `ops/` and the installed control script.*

## Measurement and maintenance tools

The `tools/` directory holds about 180 scripts: kernels, quantisers, probes, checks and benches. The ones these docs refer to are listed here; each prints its own `--help`.

| tool | what it does |
|-|-|
| `tools/row3.py` | the benchmark row, N runs through one server, with `--compare` |
| `tools/block_ab.py` | the in-process A/B for changes that keep the bits |
| `tools/forced_bench.py` | the teacher-forced bench |
| `tools/verify_spec.py` | the agreement gate with its near-tie rule |
| `tools/flagoff_identity.py` | the identity gate with the new flags off |
| `tools/api_text_check.py` | 13 plain requests against the current release |
| `tools/client_smoke.py` | the request shapes real clients send |
| `tools/verify_curve.py` | the verify prices StairCut cuts on |
| `tools/quant_nvfp4.py`, `quant_head.py` | the quantisers |
| `tools/quality_gate.py` | the quality gate |
| `tools/calib_corpus.py`, `build_corpus.py` | the calibration text and the lookup corpus |
| `tools/mem_audit.py` | the memory a configuration holds |
| `tools/disk_cache_live.py`, `disk_restore_check.py` | the disk tier's live and bit-exact checks |
| `tools/session_spill_check.py` | the session spill's bit-exact check |
| `tools/lane_check.py` | the side lane on the real engine |
| `tools/tandem_eval.py` | the conductor's evaluation suites |
| `tools/conductor_policy.py` | the monitor's policy schema and its check |
| `tools/shadow_ab.py`, `shadow_measure.py` | the shadow reader's cost and read rate |
| `tools/voice_replay.py` | a recorded session's lines through the voice |
| `tools/parallel_load.py` | agent-like sessions over several backends |
| `tools/package_check.sh` | the wheel builds and installs without torch |
| `tools/gen_reference.py` | the reference tables |

*The tools these docs refer to.*

