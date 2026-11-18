# Documentation

TandemEngine is an inference engine, and we measure its speed on raw `/v1/completions` ([measurement.md](measurement.md), the rows in [CHANGELOG.md](../CHANGELOG.md)). These pages are the whole documentation; each covers one part of the engine and names the files that implement it. Start with [overview.md](overview.md), then [install.md](install.md).

## Start here

| page | what it covers |
|-|-|
| [overview.md](overview.md) | what the engine is, the test board, what runs today, the two rules, the headline numbers, the paper, and one request followed end to end |
| [install.md](install.md) | the setups that work, what the engine needs, a new box in setup mode, the installer and its steps, the first request, access, the small models, profiles, upgrading and uninstalling |
| [faq.md](faq.md) | short answers to the questions people ask first |

## Installing and running

| page | what it covers |
|-|-|
| [setup.md](setup.md) | a new box from the command line, setup mode and the Setup API behind the Setup Guide, the model registry, and Wren |
| [operations.md](operations.md) | running a profile, memory rules, holds, deploys, troubleshooting |
| [community-weights.md](community-weights.md) | packs other people published: running one, adding yours with one JSON file, how a maintainer verifies it |
| [remote-control.md](remote-control.md) | `tandem remote`: the control verbs over an SSH key that runs nothing else, the settings overlay and its versions, the journal |
| [pairing.md](pairing.md) | pairing TandemApp by a 6-digit code (`tandem pair`, the end of every install), and the engine host on the LAN (`_tandem._tcp`) |
| [design-simple-setup.md](design-simple-setup.md) | the design of the Setup Guide's simple mode: three pages over the same plan, estimate and install job |

## How the engine works

| page | what it covers |
|-|-|
| [architecture.md](architecture.md) | the 27B and Flash-Next, the byte budget, the decode loop, the layers every model plugs into, a map of the code |
| [quantisation.md](quantisation.md) | the checkpoint's FP8, NVFP4, the clip search, GPTQ from the BF16 release, the quality gate, the three profiles, Flash-Next's EXL3 packs |
| [kernels.md](kernels.md) | row invariance, the 4-bit and 8-bit matrix products, the recurrent-layer kernels, graphs |
| [speculative-decoding.md](speculative-decoding.md) | the block drafter, the lookup drafter, StairCut, the older length router, tree verify through recurrent layers, Flash-Next's MTP drafter |
| [exactness.md](exactness.md) | the promise about the text, the near-tie exception, and the checks behind it |
| [caches.md](caches.md) | the resident prefix, the state store, the persistent suffix store, the swap of long chats to the SSD (session spill) |
| [server.md](server.md) | the OpenAI API, tool calls, structured outputs, streaming, admission, chat mode and the extension it loads |
| [parallel-requests.md](parallel-requests.md) | serving several requests at once (`--parallel N`): switching it on, waiting for memory and chat priority, speed, the text, Flash-Next |
| [measurement.md](measurement.md) | the benchmark row, the teacher-forced bench, noise, the release gate |
| [benchmark-report.md](benchmark-report.md) | the benchmark report of a running server, on your GPU: how to run it, how long it takes, what each section measures |
| [quality-gate.md](quality-gate.md) | the report a pull request carries: the command, what a diff needs, the suites, the verdicts, the check |
| [adding-a-model.md](adding-a-model.md) | the adapter path a new model takes, the seams, and the tests it must pass |
| [shadow-reader.md](shadow-reader.md) | the small model that follows each session and answers questions about it |

## The models

| page | what it covers |
|-|-|
| [flash-next.md](flash-next.md) | Qwen3.8-Flash-Next: the model, its EXL3 packs and the window each fits, loading, kernels, speculation, exactness, caches, the e4m3 KV cache, memory, serving, what is not measured |
| [lfm2.5-24b-a2b.md](lfm2.5-24b-a2b.md) | LiquidAI's LFM2.5-24B-A2B: the model, its profiles, its tool-call format, how the engine runs it |
| [nemotron-h.md](nemotron-h.md) | NVIDIA's NemotronH (Nemotron 3.5 Bolt, Super VL, Lightning): the models, the EXL3, GGUF and NVFP4 profiles, LiLiCorr, how the engine runs them |
| [ornith.md](ornith.md) | Ornith-1.5-122B: the model, its pack, drafting, serving, the e4m3 KV cache, what is not measured |
| [flint.md](flint.md) | Recursive's Flint: the model, its EXL3 and BF16 profiles, reasoning and tools, the lookup at long context, how the engine runs it |

## Reference

| page | what it covers |
|-|-|
| [reference/commands.md](reference/commands.md) | every flag of the engine's server and of `install.sh`, the service scripts, the measurement tools |
| [reference/variables.md](reference/variables.md) | `ops/serve.env`, every `QWEN38_*` switch with its default and served value, the `QSE_*` and `TANDEM_*` variables, the conductor's documents |
| [reference/http.md](reference/http.md) | every route of the engine's server, who may call it, every header and every error |
| [reference/metrics.md](reference/metrics.md) | every Prometheus family, and the usage ledger's columns |
| [troubleshooting.md](troubleshooting.md) | symptoms, causes and what to do: installing, the engine, memory, the caches |
| [glossary.md](glossary.md) | the engine's terms: anchor, round, StairCut and the rest |
| [benchmarks.md](benchmarks.md) | tool-eval-bench against TandemLLM: the raw and conductor paths, the settings a score must name, results on a DGX Spark |
| [tool-eval-bench.md](tool-eval-bench.md) | tool-eval-bench results of 2026-10-19: the raw path at five thinking settings, four conductor profiles, hard mode |
| [roadmap.md](roadmap.md) | what Flash-Next still lacks, parallel requests, other GPUs |
| [threat-model.md](threat-model.md) | where untrusted input enters, the parts that matter most for security |
| [labels.md](labels.md) | the GitHub labels of issues and pull requests, by group |
| [gallery/README.md](gallery/README.md) | the dashboard's pictures this README and the site use |
| [CHANGELOG.md](../CHANGELOG.md) | every release |

Three more references live next to the code. [contracts/engine-api-v1](../contracts/engine-api-v1/README.md) is the engine's API: every route, the shapes of its reads and [every Prometheus metric](../contracts/engine-api-v1/metrics.md). [contracts/engine-ext-v1](../contracts/engine-ext-v1/README.md) is the interface of the extension the engine loads in chat mode, and [contracts/engine-ctl-v1](../contracts/engine-ctl-v1/README.md) the control verbs of `tandem remote`.

The dashboard, the Chat tab, the conductor, its packages and memory notes are TandemApp's, a separate project with its own docs: [TandemApp's documentation](https://github.com/TandemLLM/TandemApp/blob/main/docs/README.md).

`tools/gen_reference.py` writes the reference pages' tables, and the verify price tables of [speculative-decoding.md](speculative-decoding.md), from the code between marked blocks. `python3 tools/gen_reference.py --check`, which `tests/test_docs_reference.py` runs, fails while a table is stale or the HTTP reference lacks a route or header the code has.

Cite the paper for the decoding method and its numbers ([overview.md](overview.md#the-paper)), and the repository for the software; `CITATION.cff` has both.

Every number on these pages was measured on one DGX Spark, unless the page calls it arithmetic or an estimate. [measurement.md](measurement.md) says how.

This documentation is licensed under CC BY 4.0 ([LICENSE](LICENSE)). The code is licensed separately (see the [README](../README.md)).
