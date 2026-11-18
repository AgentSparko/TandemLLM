# Questions and answers

**Does speculation change the text?** Under greedy decoding the engine writes what plain decoding on the same weights writes, up to the first near tie of at most one ulp between the two best tokens, where the batched verify can round the other way. The paper measured 17 of 30 outputs identical to the end of the answer and 13 that diverged at such a near tie. [Exactness](exactness.md) states it exactly.

**Does the conductor change the text?** It changes what the model reads (a hidden note, a thinking cap, a hidden close) and so what it writes, by design; the arithmetic is the same greedy decoding. `X-Tandem: raw` gives the release's raw path. Even there, two runs of one request can part at a near tie, because the length router sizes blocks from timings ([Two runs of the same request](exactness.md#the-one-exception-near-ties)).

**Does it serve several requests at once?** Yes. With `--parallel N` the engine decodes up to N requests in the same rounds: on 0.4.0 two at once gave 1.38× the total of one, four 1.42×, in single measured runs ([Parallel requests](parallel-requests.md)). The shipped profile runs 8 lanes since 0.15.0 (SRV-707): on 0.15.0 the 27B ThinkingCap EXL3 4.0 pack decoded 1, 2, 4 and 8 requests of 512 tokens at 42.5, 73.5, 108.9 and 155.9 tok/s in total, greedy ([0.15.0, eight lanes](../CHANGELOG.md#0150-2026-10-27)). The headline numbers in these docs are single-request numbers. With one sequence a short request still goes through the side lane, and the proxy serves other backends in parallel either way.

**Will it run on another GPU?** Best effort. The installer turns off the skinny kernel that is built for the GB10 only, and StairCut's prices were measured on the Spark, so the router starts from the wrong staircase and corrects its level online. Nothing about another GPU's speed is measured.

**Why NVFP4 weights but 16-bit arithmetic?** On this board the FP4 tensor-core path still has to move operands through shared memory, and a 4-bit activation path would need its own quantiser and gate to save at most the 8 % of traffic the activations are. NVFP4 saves bytes, and bytes are time on a memory-bound decode ([Kernels](kernels.md)).

**Why are my speeds below the paper's?** Check four things: the raw path (`X-Tandem: raw`), the lookup corpus (the paper's is not published), the store view (a store with the benchmark's text inflates it, a clean one does not), and the board's lock (another engine on the board halves the bandwidth).

**Can I use the engine without the dashboard?** Yes. A new install serves the engine alone, under its own model name, with no page and no chat ([Engine only, or the chat (beta)](install.md#engine-only-or-the-chat-beta)). The dashboard is TandemApp, which runs on any machine with Python 3.11, with or without this engine ([TandemApp's docs](https://github.com/TandemLLM/TandemApp/blob/main/docs/README.md)).

**Does the proxy add latency?** 0.33 ms to the time to first token at the median against a fake backend on one machine, and a decode rate within 0.8 % of the engine's own over the local network (measured, the 0.4.0 series).

**Are my prompts stored anywhere?** No log line, ledger row or metric holds a prompt or an answer. The suffix store holds token ids, never text. The conductor's transcripts keep a session's tool turns under the engine's home, mode 0600, secrets redacted, 30 days; memory notes are files you can read, edit and delete. Probe snapshots, off by default, hold activations, never text.

**Is the engine's port safe to expose?** No. The engine has no sign-in of its own: its OpenAI routes are always open, and everything else is open until an admin password is set. Sign-in, accounts and API tokens are TandemApp's ([TandemApp's sign-in](https://github.com/TandemLLM/TandemApp/blob/main/docs/auth.md)). If other people can reach the machine, keep the engine's port on loopback and let them in through TandemApp, a reverse proxy with its own sign-in, or a VPN ([Access and sign-in](install.md#access-and-sign-in)).


