# Qwen3.8-Flash-Next

A plain decode token of Qwen3.8-Flash-Next on its 3.05 pack reads about 4.23 GB (arithmetic, from the pack's headers), and the engine decodes it at 45.8 tok/s at 1k tokens of context with no drafter (measured, 2026-10-11). Flash-Next is the second model TandemLLM serves. It runs on the same board as the 27B, one model at a time, through the same server, the same caches and the same speculation methods. This page collects what is different, from the model and its packs to the parts that are not measured yet. [Adding a model](adding-a-model.md) describes the adapter path it came in through.

## The model

Flash-Next keeps the 27B's hybrid of Gated DeltaNet layers with an attention layer every fourth, and changes most of what sits around it (the table below). `engine/models/flashnext/config.py` reads its configuration and refuses any field it does not know, naming it, so a later release that adds an arithmetic switch is not served as if it were this one.

| part | Flash-Next | the 27B |
|-|-|-|
| layers | 48: 36 Gated DeltaNet, 12 attention | 64: 48 Gated DeltaNet, 16 attention |
| MLP | 512 experts a layer; 10 routed and 1 shared run a token | dense, width 17,408 |
| residual stream | 4 streams of 2,560, joined by hyper-connection mixers | one stream of 5,120 |
| attention | sparse past 2,051 tokens: an indexer scores blocks of keys and each row reads the 512 best | full |
| extra input | a hashed n-gram embedding: 16 table rows a token, in front of layer 1 | none |
| draft layer | one multi-token-prediction (MTP) layer in the checkpoint | none; DFlash2 drafters beside it |
| state of a sequence | 110.3 MiB of sections plus 29,952 bytes a token | about 147 MiB plus 64 KB a token |
| weights served | turboderp's EXL3 packs, 2.05 to 6.05 bits a weight | NVFP4 with an e4m3 head, or EXL3 |

*Flash-Next beside the 27B.*

The tokenizer is the 27B's (248,320 ids), so the lookup drafter's corpus and the persistent suffix store serve both models unchanged; both carry the tokenizer's fingerprint and are checked against it at load.

## The packs and the window each one fits

turboderp publishes Flash-Next in five sizes, one branch each of `turboderp/Qwen3.8-Flash-Next-exl3`, and each size is a weight profile (`ops/profiles/flashnext-<bpw>.env`). The engine reads the packs as they are; [Quantisation](quantisation.md) says what is inside one. The largest window of each pack is in its registry entry (`models/qwen3.8-flash-next/model.yaml`), and a start with a longer `--max-len` stops before the load and names both numbers.

| profile | disk | weights | n-gram table | largest window |
|-|-|-|-|-|
| `flashnext-2.05` | 61.5 GB | 32.85 GiB | 24.44 GiB, warm | 262,144 |
| `flashnext-3.05` | 85.1 GB | 47.87 GiB | 30.4 GiB, cold | 262,144 (recommended profile) |
| `flashnext-4.05` | 106.1 GB | 62.47 GiB | 36.36 GiB, cold | 208,896 |
| `flashnext-5.05` | 121.9 GB | 77.16 GiB | 36.36 GiB, cold | load/generation unverified |
| `flashnext-6.05` | 137.7 GB | 91.87 GiB | 36.36 GiB, cold | none: it does not fit |

*The Flash-Next packs on one DGX Spark. Weights: the GiB on the device, the draft layer and the int8 mixers included; (measured) for 3.05, estimated from the file sizes for the others.*

One request of 260,800 tokens through the server, on each of the three packs that load, decided those windows (the figure below). The Setup Guide's estimate is calibrated on these three runs and predicts their lowest MemAvailable within 0.7 GiB. On the box the other programs took 4.6 GiB; the estimate's frame keeps 10 GiB for them, which is why 4.05 comes out at 208,896 tokens with the vision tower on (212,992 without) although its run at 260,800 kept MemAvailable above the floor. For 5.05 the estimate is 53,248 with the tower and 65,536 without; full-pack load and generation remain unverified.

| pack | lowest counted MemAvailable |
|-|-|
| 2.05, table warm | 25.5 GiB |
| 3.05, table cold | 31.9 GiB |
| 4.05, table cold | 17.8 GiB |

*The lowest MemAvailable, less a warm table's resident bytes, during one 260,800-token request at a 262,144-token window, sampled at 10 Hz. (measured), 2026-10-12, one run a pack, conductor on, with 4.6 GiB of other programs on the box.*

The run also measured what a long prefill takes: 571 tok/s for the 260,800-token request on 3.05, 261,549 rows with the conductor's notes (585 on 2.05 and 581 on 4.05), and a prefill workspace of 10.47 GiB above the engine, which stops growing at about 140k tokens. Decoding at that length, with the prompt from the caches, 3.05 made 22.5 tok/s with the drafter at 2.0 tokens a block; 2.05 made 27.0 and 4.05 20.5 (measured, one run each). A server without a drafter decoded 36.6 tok/s at that length (one request), and [Never slower than the plain step](#never-slower-than-the-plain-step) says what changed.

Full-pack load and generation of 5.05 remain unverified. Its earlier blocker was 7-bit dense projections, not routed experts: the MoE kernels already supported 7-bit experts, and dense dispatch now covers 2 through 8 bits. Its window is an estimate, not a measured run. 6.05 needs about 92 GiB for its weights, which leaves too little under the 15 GiB floor at any window. 3.05 reads its n-gram table cold. Warm, with the whole table resident, MemAvailable less the table fell to 8.4 GiB during a 146,400-token request at a 147,456-token window, under the floor.

## Loading

`engine/models/flashnext/pack.py` audits a pack before anything loads: every tensor the configuration expects, every shard's size against its header, and the hub's sha256 of every file. A missing tensor or a wrong size stops the load with its name. The loader reads each layer's experts as one file range into a 16-byte-aligned image and copies it to the device in one go, then drops the shards' page cache (52.3 GB on 3.05) and keeps the n-gram table's. On 3.05 a cold load took 56.2 s and a warm one 24.0 s (measured); through the server, 53.8 s.

The self-check runs at every load. It decodes 32 tokens one at a time, replays the recorded steps as chains of 2, 4, 8, 12, 16 and 32 rows and as an 8-row tree, and compares every row bit for bit: 3,936 rows on 3.05. A single differing bit turns speculation off for the process, and the log names the layer and the row. An injected one-bit difference failed it at row 5 of an 8-row chain.

## Kernels

Every Flash-Next kernel is row-invariant: a row's instruction sequence does not depend on how many rows the call has or where its neighbours sit ([Kernels](kernels.md)). The MoE expert kernel (`tools/fn_kernels.py`) gives each (row, expert) pair its own work, rotates the pair's input with that expert's scales, and sums each row's slices in a fixed order; unselected experts are never read. At one row it moves 186 GB/s, 21.7 MB a layer in 0.117 ms (measured). One decode token is one replay of a CUDA graph, about 21.8 ms at 1k of context (measured).

A prompt runs through other kernels (`tools/fn_prefill_kernels.py`): a chunk of 4,096 rows sorts its (row, expert) pairs by expert and runs one matrix product over each expert's rows. On 3.05 the prefill made 1,185, 1,243 and 1,161 tok/s at 2k, 8k and 32k tokens (measured). The prefill and the decode path differ by BF16 rounding. Teacher-forced on prose and code at 3k and 8k tokens, their top tokens agreed at 96.2 to 98.1 % of positions (measured). The gate the prefill passed asks that it agree with the decode path at least as well as the decode path agrees with the reference forward, less one point. Knobs that give the same bits on and off (SPD-249, `docs/kernels.md`): `QWEN38_FN_PF_WINDOW` runs a prompt's chunks layer-major in windows of up to that many rows (8,192 by default), each layer's experts once over the window and the rest a chunk at a time; a window asks the memory policy for its bytes first and runs its chunks one at a time when they are not there, and 0 restores the previous path; `QWEN38_FN_PF_HC_FUSED`, `QWEN38_FN_PF_INJ_FUSED` and `QWEN38_FN_PF_NOCOPY` (on by default) and `QWEN38_FN_PF_ROT_ROWS` (off) take fewer passes and copies; `QWEN38_FN_PF_ATTN_STAGES` sets the attention launch's pipeline stages (1; 2 is the decode path's).

## Speculation

The MTP layer is the drafter. Each round it drafts a chain of 4 tokens with its top two tokens a depth, from one captured graph, through a draft head cut to the 32,768 most frequent ids (`QWEN38_FN_DRAFT_HOT`). The shared StairCut (`engine/methods/staircut.py`) keeps the part of that draft with the most expected tokens a millisecond, and with `QWEN38_FN_LOOKUP=1` (on in every Flash-Next profile) the lookup's best continuation joins the same tree. One verify a round checks it. A verified row is the decode step's row bit for bit, so greedy speculation writes the plain greedy text token for token, and a seeded sampled request draws what plain sampled decoding draws ([Exactness](#exactness)).

| class | MTP, tok/s | with lookup | tokens a block | with lookup |
|-|-|-|-|-|
| edit | 106.2 | 131.7 | 4.56 | 10.43 |
| copy | 102.3 | 132.9 | 4.37 | 11.88 |
| prose | 49.4 | 51.1 | 2.16 | 2.15 |
| chat | 62.1 | 64.0 | 2.47 | 2.49 |
| code | 84.9 | 82.7 | 3.43 | 3.48 |

*One greedy request of up to 512 tokens a class (prose stopped at 421) through the server on 3.05, MTP alone and with the lookup. Every text equalled plain decoding, which made 41.5 to 45.7 tok/s. (measured), 2026-10-12, one request each.*

Copies gain less in speed than in tokens a block because a verify costs more with every row on this model. Each row routes to its own experts, and a block reads the union: about 19 experts a layer at 2 rows, 26 to 33 at 4 and 72 to 82 at 16, across prose, code and chat at 1k and 8k (measured). The learned prices were 27.8 ms for 2 rows, 38.6 for 5, 52.2 for 10 and 68.8 for 16 at about 1k of context.

Sampled requests take the same tree (`--sampled-tree det`): at each node the target draws its own token with the 27B's sampler, keyed by seed and position. At temperature 0.7, `top_p` 0.9, `top_k` 20 and `min_p` 0.05 on 3.05, every seeded answer was the same with and without speculation, each seed repeated, and two unseeded draws differed (measured, one run each). Speculation made 43.1 and 40.2 tok/s on prose after 1k and 8k tokens of held-out text, 76.2 and 73.6 on code and 59.0 and 60.8 on chat, against 38.4 to 41.7 plain.

## Never slower than the plain step

A drafted round at long context can cost more a token than the plain step: on the first runs at 260,800 tokens the drafter decoded 22.5 tok/s and a server without one 36.6. StairCut on Flash-Next therefore prices every round by its wall time per context class. The class is 0 while the sparse attention is still dense, and above that the next of 4,096 · 2^k and 6,144 · 2^k at or above the context, up to 262,144. Per class it keeps the plain step's cost and a drafted round's cost a token, both starting from the price file (`ops/prices/qwen3.8-flash-next/flashnext-3.05.dgx-spark.json`, provisional).

- A class whose drafted rounds cost more a token than its plain steps takes the plain step, and drafts one probe round at an interval that doubles while drafting keeps losing (16 rounds, up to 256).
- A turn drafts only when what speculation saves over the expected answer pays for the draft head's backlog, the prompt rows it has not read (0.28 ms a row at 260,800 tokens).
- What a class learned of its drafted rounds ages back to the prior while its turns take the plain step.
- One second after the last request, the server captures the graphs the current class lacks, 25 to 30 in about 4.3 s. Before that, the first drafted request in a new class paid 3.4 to 5.1 s of captures inside its answer.

| context | speculation, tok/s | plain, tok/s |
|-|-|-|
| 1k | 56.6 and 57.3 | 41.0 and 45.4 |
| 32k | 34.9 and 55.6 | 33.0 and 42.5 |
| 140k | 42.1 and 52.4 | 33.9 and 41.4 |
| 260,800 | 41.0 and 41.0, plain turns | 39.6 and 40.5 |

*One greedy request a length and the same request again (its prompt from the caches), the second round on one fresh 3.05 server, and the same pair on a server with no drafter. (measured), 2026-10-12. All 16 texts equalled plain decoding.*

With the idle captures in place, the first request at a new length took the plain step and the next one drafted: 48.2 against 42.5 tok/s at 32k and 46.0 against 41.4 at 140k, every answer equal to plain decoding (measured, one fresh server a length).

## Exactness

On the 27B a batched verify can round a near tie the other way ([Exactness](exactness.md)). On Flash-Next it cannot: its verify kernels give every row the decode step's bits, and the load-time self-check proves it on the device before speculation turns on. `tools/fn_server_check.py --compare` sends 20 requests to a `--drafter none` server and a `--drafter mtp` server (greedy, thinking, streaming, every `tool_choice` mode, JSON, penalties with `logit_bias`, logprobs, seeds, a stop string): 19 gave the same answers and tool calls, and the largest logprob difference was 0.0. The 20th is a think budget at low effort, which closes at the first block boundary past the cap, so the two runs close the reasoning block a few tokens apart (1,049 and 1,052 reasoning tokens) and differ after it.

Two places hold the 27B's rule instead. The prefill and the decode path differ by BF16 rounding, so a conversation resumed from an end anchor, a state the decode kernels wrote, is held to the greedy gate and not to bit equality ([Caches](#caches)). And the weights themselves differ from the BF16 release by design; how much is not measured.

## Caches

Every cache tier keeps Flash-Next's state through its adapter (`eng.cache_state()`). Its sections are the 36 GDN states and convolution tails, the n-gram convolution and its token context, and `mtp.h_last`, the last committed row's streams that the MTP layer reads next: 115,662,864 bytes. Its views are K, V and the indexer keys of the 12 attention layers, and the MTP layer's own: 29,952 bytes a token. The pooled indexer keys are not stored; a restore rebuilds them. The grid is 4,096 tokens, the prefill's chunk, so a resumed prefill runs the same chunks as a cold one: a 4,096-token chunk file is 122.7 MB and an anchor file 115.7 MB.

`tools/fn_cache_check.py` sent a request sequence to a server with its caches on and the same requests to a cold server. Restores from grid anchors, the state store, the session spill after a restart and the disk tier after a restart answered bit for bit (logprobs equal). Two restores from end anchors answered the same first token: one the same text, logprobs within 0.044, and one a longer list from its 13th token at a near tie. A reset keeps the KV rows in place; before that fix, a short request run between the turns of a long conversation zeroed rows the conversation resumed over.

## The e4m3 KV cache

`FN_KV_FP8=1` keeps the 12 attention layers' keys and values as e4m3 codes with one fp32 scale a (head, token): 18,944 bytes a token of window instead of 31,040, with the indexer keys and the MTP layer's cache in bf16 (SPD-150). The output is no longer the exact model's, so the knob is off by default and the exactness checks hold only with it off. A decode token reads at most 2,051 keys a layer, so the gain is memory rather than speed. Its speed and its cost against the exact cache are not measured. To turn it on, switch on *Long-context cache (FP8)* under Features in the Setup Guide's Tools step, or add `QWEN38_FN_KV_FP8=1` at the end of `~/TandemLLM/run/ops/serve.env` and run `~/TandemLLM/bin/tandem restart`; `/health` then shows `"kv_cache": "e4m3"` under `weights`. `docs/caches.md` covers every model's switch.

## Memory

MemAvailable counts page cache as free. Under the warm policy the n-gram table is page cache the engine keeps on purpose, so every floor check (the residency planner, the roles' governor, the side slots' admission, the KV pool) takes the table's resident bytes off first. After a load of 3.05, MemAvailable read 68.0 GiB with 46.9 GiB allocated (measured).

Each profile sets its engine's ceiling and peak with the arithmetic in the file: the weights, 4.15 GiB of runtime measured on 3.05, slot 0's KV at 262,144 tokens (7.58 GiB) and 6 GiB of caches, and on top the prefill workspace (10.75 GiB, where it stops growing). 3.05 sets 66 and 77 GiB, 4.05 81 and 92. The adapter states the workspace: a floor of 2.75 GiB, 76,309 bytes a context token, and that cap.

## Serving and operations

`ops/use-profile.sh flashnext-3.05` switches the box to Flash-Next as it switches between the 27B's profiles: it fetches the pack's branch when it is missing (pinned to its commit, checked against the hub's sha256, the shared n-gram table hard-linked), stops the engine under the box lock, starts the new profile and rolls back to the previous one, 27B or Flash-Next, when the smoke check fails. `ops/start.sh` passes the profile's `QSE_ENGINE_FLAGS` (`--model <pack> --drafter mtp`) and the server picks the engine from the checkpoint's own configuration. The watchdog and the reboot line start the active profile, whichever model it is. [Operations](operations.md) has the rest.

Behind the server everything is the 27B's code: every route, reasoning formats and budgets, tool calls and grammar masks, penalties, logprobs, seeds, stops, the live view and the usage rows. The conductor and its roles run in front of it; the shadow reader does not, because its outputs were validated on the 27B's reasoning only, and the status says so. The dashboard's system view names the pack and its branch, shows `max_len` against the pack's largest window, and reports the table under its policy. Flash-Next reads images through the pack's own vision tower on the EXL3 kernels, with the image rows' three-axis rotary positions (VIS-97, `VISION=on` in its profiles). On 3.05 the image chats answered the same with the MTP drafter and without (5 of 5: two single-image chats, a two-image chat of 2,089 prompt tokens and a second turn from the caches), and a text-only answer is the same with the tower loaded and without. Against transformers' `Qwen4ExpVisionModel` on the dequantised weights the tower's outputs sit within the official model's own BF16 noise (0.048 to 0.090 from the fp32 model, against 0.046 to 0.104). In the language model the text rows after an image keep the text's own agreement (KL 0.019 after 140 image rows and 0.0025 after 2,184 with the sparse attention on, top-1 97.9 %; the text floor 0.022), and every verify pick equals the decode (`tools/fn_vision_check.py`, VIS-97).

Parallel requests work on Flash-Next without changing any answer, and its profiles turn them on: three slots over one engine with the KV pool (`QSE_PARALLEL=3`, `QSE_KV_POOL=on`). On fresh servers three requests arriving together took 27.18 s against 27.03 s one after the other, the same total throughput, and the first tokens came at 2.2 to 6.1 s instead of up to about 20 s for the last of three. One request alone decodes at 45.78 tok/s with three slots, 45.79 with one (measured). At a 262,144-token window with two 65,536-token side slots, a 260,800-token prompt kept MemAvailable at 31.6 GiB or more. The table below lists the limits.

Requests that arrive together on an idle engine prefill in one forward (`QWEN38_FN_JOINT_PREFILL`, on in the profiles, off in the family's defaults, SPD-249): each layer's experts run once over every prompt's rows, the rest of the layer a prompt at a time, and every prompt ends bit for bit as its own prefill leaves it. The burst gets more tokens per second in total, and its long prompts get their first token later, since every first token waits for the one forward.

| setting | value |
|-|-|
| sequence slots (`QSE_PARALLEL`) | 3 |
| side window (`QSE_SIDE_MAX_LEN`) | 0: `MAX_LEN`, every lane the same window (65,536 before SRV-707) |
| admission floor (`QSE_PARALLEL_FLOOR_GB`) | 15 GiB |
| joint verify | up to 32 rows |
| turn quantum, batch timeout | 50 ms, 2 ms |
| KV pool | hard floor 10 GiB, margin 256 rows, page 2 MiB |
| memory ceiling, peak, reserve slack | 66, 77, 2 GiB |

*Flash-Next's parallel limits in its profiles (ENG-241). A side slot took 0.20 to 0.22 GiB allocated at load.*

## What is not measured

- Quality against the BF16 model, per pack. The BF16 release is 360 GB and does not fit the board; the registry's quality fields are empty.
- Any task benchmark, and the benchmark row of [Measurement](measurement.md). The rates in this page are single requests, mostly one run each.
- A sweep of StairCut's prices. The price file is provisional: class 0 holds online-learned prices, and the other classes single requests.
- Full-pack load and generation of the 5.05 pack after the 7-bit dense-dispatch fix, and the 6.05 pack, which needs more than one board.
- Whether parallel requests pay on Flash-Next: one run of three, and no timing of the joint pass on its own.
- The e4m3 KV cache ([The e4m3 KV cache](#the-e4m3-kv-cache)): its speed and its cost against the exact cache.

