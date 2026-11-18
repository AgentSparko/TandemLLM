# Reference: environment and configuration

TandemEngine reads four families of settings. Engine switches are `QWEN38_*` variables, read through one registry. Server settings are command-line flags ([Command-line reference](commands.md)); `ops/start.sh` builds them from `ops/serve.env`, and a few `QSE_*` variables set flags' defaults or serve the scripts. The installer and the service scripts read a few `TANDEM_*` variables. And the conductor's behaviour lives in its versioned documents, which the dashboard edits. The `QWEN38_*` knobs the extension reads, and the variables of TandemApp's proxy, collector and plugins, are in [TandemApp's docs](https://github.com/TandemLLM/TandemApp/blob/main/docs/README.md).

## The served profile

`ops/serve.env` is the served configuration. On an installed engine it is `~/TandemLLM/run/ops/serve.env`, rewritten by every run of the installer, and `local.env` beside it is read last for your own values.

<!-- generated: serve-env by tools/gen_reference.py; do not edit by hand -->

### The keys ops/serve.env sets, in file order (paths not shown)

| Key | Value | Kind |
|-|-|-|
| `HOME_DIR` | `(a path)` | read by ops/start.sh |
| `REPO` | `(a path)` | read by ops/start.sh |
| `PY` | `(a path)` | read by ops/start.sh |
| `PYTHONPATH` | `(a path)` | read by ops/start.sh |
| `HOST` | `0.0.0.0` | read by ops/start.sh |
| `PORT` | `8000` | read by ops/start.sh |
| `SERVED_MODEL` | `qwen38-spark-engine` | read by ops/start.sh |
| `NV` | `(a path)` | read by ops/start.sh |
| `HEAD` | `(a path)` | read by ops/start.sh |
| `CKPT8` | `(a path)` | read by ops/start.sh |
| `CKPT16` | `(a path)` | read by ops/start.sh |
| `CORPUS` | `(a path)` | read by ops/start.sh |
| `MAX_LEN` | `262144` | read by ops/start.sh |
| `DEFAULT_MAX_TOKENS` | `32768` | read by ops/start.sh |
| `PATTERN_STOP` | `(empty)` | read by ops/start.sh |
| `QSE_MODE` | `(empty)` | server setting |
| `CONDUCTOR` | `on` | read by ops/start.sh |
| `REASONING_FORMAT` | `tags` | read by ops/start.sh |
| `REASONING_EFFORT` | `medium` | read by ops/start.sh |
| `LEN_FIXED` | `0` | read by ops/start.sh |
| `LEN_LATCH` | `1` | read by ops/start.sh |
| `BUDGET` | `16` | read by ops/start.sh |
| `MEM_CEILING_GIB` | `70` | read by ops/start.sh |
| `MEM_PEAK_GIB` | `77` | read by ops/start.sh |
| `RESERVE_SLACK_GIB` | `2` | read by ops/start.sh |
| `RESIDENT_GB` | `2.5` | read by ops/start.sh |
| `RESIDENT_STASH_GB` | `1.0` | read by ops/start.sh |
| `RESIDENT_GUEST_MAX` | `8192` | read by ops/start.sh |
| `RESIDENT_PIN_GB` | `4.0` | read by ops/start.sh |
| `RESIDENT_PARK_GB` | `4` | read by ops/start.sh |
| `CACHE_GB` | `2` | read by ops/start.sh |
| `STORE_ENTRY_GB` | `1` | read by ops/start.sh |
| `NARROW_ARM` | `1` | read by ops/start.sh |
| `END_ANCHOR` | `on` | read by ops/start.sh |
| `END_ALIGN` | `on` | read by ops/start.sh |
| `DROP_PAGE_CACHE` | `1` | read by ops/start.sh |
| `REQUEST_TIMEOUT` | `3600` | read by ops/start.sh |
| `MAX_QUEUE` | `8` | read by ops/start.sh |
| `QUEUE_TIMEOUT` | `240` | read by ops/start.sh |
| `FUSE_PROJ` | `0` | read by ops/start.sh |
| `QWEN38_DF2_TREE_MODE` | `nodes` | engine switch |
| `QWEN38_TREE_ALIAS_STATE` | `0` | engine switch |
| `DROP_IDLE` | `1` | read by ops/start.sh |
| `QWEN38_DRAFT_TEMP` | `0.3` | engine switch |
| `SAMPLED_TREE` | `det` | read by ops/start.sh |
| `QWEN38_FUSED_GDNPREFILL` | `1` | engine switch |
| `QWEN38_DECODE_ATTN` | `1` | engine switch |
| `QWEN38_DRAFT_NVFP4` | `1` | engine switch |
| `QWEN38_NVFP4_PREFILL_V2` | `1` | engine switch |
| `QWEN38_NVFP4_PREFILL_V2_UNTIL` | `1025` | engine switch |
| `QWEN38_NVFP4_SKINNY` | `1` | engine switch |
| `QWEN38_SKINNY_TILES` | `(a path)` | engine switch |
| `QWEN38_FUSED_COMMIT` | `1` | engine switch |
| `QWEN38_FUSED_GDNVERIFY` | `1` | engine switch |
| `QWEN38_DRAFT_FC_NVFP4` | `1` | engine switch |
| `QWEN38_FUSED_ADDNORM` | `1` | engine switch |
| `QWEN38_TREE_HOST_DEPTH` | `1` | engine switch |
| `QWEN38_HOST_WALK` | `1` | engine switch |
| `QWEN38_VERIFY_GRAPH` | `1` | engine switch |
| `QWEN38_COMMIT_IN_VERIFY` | `1` | engine switch |
| `QWEN38_DRAFT_GRAPH` | `1` | engine switch |
| `QWEN38_GDNV_ONE_WARP` | `1` | engine switch |
| `QWEN38_TREE_NODES_NARROW` | `16` | engine switch |
| `QWEN38_TREE_MS` | `8:77.9,16:78.9,24:87.0,32:97.0` | engine switch |
| `QWEN38_VERIFY_ROWS` | `32` | engine switch |
| `QWEN38_SKINNY_TILES_WIDE` | `(a path)` | engine switch |
| `QWEN38_TREE_NODES` | `24` | engine switch |
| `QWEN38_TREE_WIDE_AFTER` | `32` | engine switch |
| `QWEN38_DEEP` | `32` | engine switch |
| `QWEN38_DEEP_AFTER` | `2` | engine switch |
| `QWEN38_LEN_SWITCH` | `1` | engine switch |
| `QWEN38_LEN_MODE` | `wide` | engine switch |
| `QWEN38_STAIR_TABLES` | `(a path)` | engine switch |
| `QWEN38_STAIR_RHO` | `(a path)` | engine switch |
| `QWEN38_STAIR_SKIP` | `1` | engine switch |
| `QWEN38_SKINNY_SRUN` | `1` | engine switch |
| `QSE_USAGE_LEDGER` | `(a path)` | server setting |
| `QSE_CONDUCTOR_CONFIG` | `(a path)` | server setting |
| `QSE_BACKENDS` | `(empty)` | server setting |
| `QSE_PARALLEL` | `8` | server setting |
| `QSE_SIDE_MAX_LEN` | `0` | server setting |
| `QSE_PARALLEL_FLOOR_GB` | `15` | server setting |
| `QSE_SIDE_GRAPHS` | `off` | server setting |
| `QSE_SIDE_SLOT_CONST_GB` | `1.5` | server setting |
| `QSE_KV_POOL` | `on` | server setting |
| `QSE_KV_HARD_FLOOR_GB` | `10` | server setting |
| `QSE_KV_RECOMPUTE` | `on` | server setting |
| `QSE_KV_ADMIT_ROWS` | `8192` | server setting |
| `QSE_BARRIER_IDLE_OWNER` | `on` | server setting |
| `QWEN38_GDNV_CONV_BLOCK` | `32` | engine switch |
| `QWEN38_GDNV_CONV_WARPS` | `1` | engine switch |
| `QWEN38_GDNV_WY` | `1` | engine switch |
| `QWEN38_GDNV_WY_FUSED` | `1` | engine switch |
| `QWEN38_GDNV_WY_MAXT` | `16` | engine switch |
| `QWEN38_GDNV_WY_CHAIN_MAXT` | `16` | engine switch |
| `QWEN38_GDNV_WY_FUSED_MAXT` | `16` | engine switch |
| `LIVE_ACTIVITY` | `on` | read by ops/start.sh |
| `QSE_DASHBOARD_LOGIN` | `off` | server setting |
| `ROLES_AUTOLOAD` | `(empty)` | read by ops/start.sh |
| `QWEN38_DECIDER_MODELS` | `(empty)` | engine switch |
| `QWEN38_DECIDER_DIR` | `(a path)` | engine switch |
| `QWEN38_NARRATOR_MODEL` | `(empty)` | engine switch |
| `QWEN38_NARRATOR_DIR` | `(empty)` | engine switch |
| `QWEN38_SHADOW_MODEL` | `(empty)` | engine switch |
| `QWEN38_SHADOW_DIR` | `(empty)` | engine switch |
| `QWEN38_SHADOW_GB` | `4` | engine switch |
| `QWEN38_SHADOW_GRID` | `512` | engine switch |
| `QWEN38_SHADOW_DISK` | `auto` | engine switch |
| `QWEN38_SHADOW_DISK_GB` | `20` | engine switch |
| `QWEN38_PROBE_SNAPSHOTS` | `off` | engine switch |
| `QWEN38_PROBE_DIR` | `(a path)` | engine switch |
| `QSE_DISK_CACHE` | `(a path)` | server setting |
| `SESSION_SPILL` | `on` | read by ops/start.sh |
| `SESSION_SPILL_GB` | `64` | read by ops/start.sh |
| `SESSION_SPILL_CODEC` | `zstd` | read by ops/start.sh |
| `DISK_WRITE_BUDGET_GB` | `200` | read by ops/start.sh |
| `SESSION_SPILL_IDLE` | `on` | read by ops/start.sh |
| `VISION` | `on` | read by ops/start.sh |
| `RESPONSE_CACHE` | `off` | read by ops/start.sh |
| `QSE_STATUS_PORT` | `(empty)` | server setting |
| `QSE_PROFILE_NAME` | `(a path)` | server setting |
| `QSE_PACK_VISION` | `(empty)` | server setting |
| `QSE_SETUP_ENV_FILE` | `(a path)` | server setting |
| `QSE_VISION_ASKED` | `(a path)` | server setting |
| `VISION` | `off` | read by ops/start.sh |
| `QSE_VISION_ASKED` | `(a path)` | server setting |
| `VISION` | `off` | read by ops/start.sh |
| `QSE_MODE` | `engine` | server setting |
| `QSE_MODE` | `chat` | server setting |
| `QSE_CONDUCTOR_CONFIG` | `(a path)` | server setting |
| `QSE_EXT` | `(a path)` | server setting |
| `QSE_MODE` | `engine` | server setting |
| `CONDUCTOR` | `raw` | read by ops/start.sh |
| `QSE_CONDUCTOR_CONFIG` | `off` | server setting |
| `QWEN38_DECIDER_MODELS` | `(empty)` | engine switch |
| `QWEN38_NARRATOR_MODEL` | `(empty)` | engine switch |
| `QWEN38_SHADOW_MODEL` | `(empty)` | engine switch |
| `ROLES_AUTOLOAD` | `(empty)` | read by ops/start.sh |
| `QSE_EXT` | `(empty)` | server setting |
| `QSE_CHATD_ASKED` | `(a path)` | server setting |
| `QSE_CHATD` | `off ;;` | server setting |

<!-- /generated -->

## Engine switches

`engine/settings.py` lists every engine switch with its default and the module that reads it; `tests/test_settings.py` fails when a module reads one another way. The table shows each switch, its code default, and the value the served profile sets when it sets one.

<!-- generated: engine-knobs by tools/gen_reference.py; do not edit by hand -->

### Every QWEN38_* switch the engine reads, with the value ops/serve.env sets (the extension's, in contracts/engine-ext-v1/knobs.json, are in TandemApp's docs)

| Variable | Code default | Served value · read in |
|-|-|-|
| `QWEN38_ATTN_INV` | `0` | `tools/attn_kernels.py` |
| `QWEN38_ATTN_INV_NT` | `4` | `tools/attn_kernels.py` |
| `QWEN38_ATTN_INV_SPLIT` | `1024` | `tools/attn_kernels.py` |
| `QWEN38_ATTN_STAGES3` | `1` | `tools/attn_kernels.py` |
| `QWEN38_BLOCKING_SYNC` | `0` | `server/app.py` |
| `QWEN38_CLIENT_REASONING` | `*=reasoning_content,hermes=tags` | `server/compat.py` |
| `QWEN38_COMMIT_IN_VERIFY` | `0` | `1` · `engine/model.py` |
| `QWEN38_CORPUS` | `none` | `engine/drafters/ngram.py` |
| `QWEN38_DECIDER_COLLATE` | `call` | `server/decider_api.py` |
| `QWEN38_DECIDER_DEVICE` | `auto` | `server/decider_api.py` |
| `QWEN38_DECIDER_DIR` | `~/.qwen38-spark-engine/deciders/laya` | `(a path)` · `server/decider_api.py` |
| `QWEN38_DECIDER_MAX_BATCH` | `64` | `server/decider_api.py` |
| `QWEN38_DECIDER_MAX_QUEUE` | `256` | `server/decider_api.py` |
| `QWEN38_DECIDER_MODELS` | `none` | `server/decider_api.py` |
| `QWEN38_DECIDER_WAIT_MS` | `2` | `server/decider_api.py` |
| `QWEN38_DECODE_ATTN` | `0` | `1` · `engine/model.py` |
| `QWEN38_DEEP` | `0` | `32` · `server/app.py` |
| `QWEN38_DEEP_AFTER` | `2` | `2` · `server/app.py` |
| `QWEN38_DF2_DEFER_SYNC` | `1` | `engine/drafters/dflash2.py` |
| `QWEN38_DF2_KV_WINDOW` | `0` | `engine/drafters/dflash2.py` |
| `QWEN38_DF2_PACK_BATCH` | `1` | `engine/drafters/dflash2.py` |
| `QWEN38_DF2_PACK_FOLD` | `0` | `engine/drafters/dflash2.py` |
| `QWEN38_DF2_PACK_GQA` | `0` | `engine/drafters/dflash2.py` |
| `QWEN38_DF2_PACK_HOST1` | `1` | `engine/drafters/draft_graph.py` |
| `QWEN38_DF2_PACK_KP` | `1` | `engine/drafters/dflash2.py` |
| `QWEN38_DF2_PACK_TAIL` | `0` | `engine/drafters/dflash2.py` |
| `QWEN38_DF2_PACK_TOPK` | `0` | `engine/drafters/dflash2.py` |
| `QWEN38_DF2_SPAN_STEP` | `256` | `engine/drafters/draft_graph.py` |
| `QWEN38_DF2_TEMP` | `1.0` | `engine/drafters/dflash2.py` |
| `QWEN38_DF2_TOPK_FAST` | `1` | `engine/drafters/topk_small.py` |
| `QWEN38_DF2_TREE_MODE` | `paths` | `nodes` · `engine/drafters/dflash2.py` |
| `QWEN38_DFLASH2` | `none` | `engine/drafters/dflash2.py` |
| `QWEN38_DRAFT_EXL3` | `none` | `engine/drafters/dflash2.py` |
| `QWEN38_DRAFT_FC_NVFP4` | `0` | `1` · `engine/drafters/dflash2.py` |
| `QWEN38_DRAFT_GRAPH` | `0` | `1` · `engine/drafters/dflash2.py` |
| `QWEN38_DRAFT_HEAD` | `none` | `engine/drafters/dflash2.py` |
| `QWEN38_DRAFT_HEAD_EXL3` | `none` | `engine/drafters/dflash2.py` |
| `QWEN38_DRAFT_HEAD_NVFP4` | `0` | `engine/drafters/dflash2.py` |
| `QWEN38_DRAFT_NVFP4` | `0` | `1` · `engine/drafters/dflash2.py` |
| `QWEN38_DRAFT_TEMP` | `1.0` | `0.3` · `engine/drafters/dflash2.py` |
| `QWEN38_DSPARK` | `none` | `engine/drafters/dspark.py` |
| `QWEN38_DSPARK_NO_YARN` | `none` | `engine/drafters/dspark.py` |
| `QWEN38_END_ALIGN_CALLS` | `on` | `server/endalign.py` |
| `QWEN38_END_ALIGN_MOVED` | `on` | `server/endalign.py` |
| `QWEN38_END_ALIGN_REASONING` | `on` | `server/endalign.py` |
| `QWEN38_END_RESERVE` | `on` | `server/app.py` |
| `QWEN38_EXL3` | `none` | `engine/loader.py` |
| `QWEN38_EXL3_CHUNK_UNTIL` | `128` | `tools/exl3_linear.py` |
| `QWEN38_EXL3_FRAG` | `1` | `engine/models/qwen3_5/frag.py` |
| `QWEN38_EXL3_FUSE` | `1` | `engine/loader.py` |
| `QWEN38_EXL3_MLP_BRIDGE` | `1` | `tools/exl3_linear.py` |
| `QWEN38_EXL3_PDL` | `0` | `tools/exl3_linear.py` |
| `QWEN38_EXL3_ROWGRID` | `1` | `tools/exl3_linear.py` |
| `QWEN38_EXL3_SILU_FUSED` | `1` | `tools/exl3_linear.py` |
| `QWEN38_EXL3_STAGES` | `3` | `tools/exl3_linear.py` |
| `QWEN38_EXL3_TALL` | `none` | `engine/models/qwen3_5/tall.py` |
| `QWEN38_EXL3_TILES` | `none` | `tools/exl3_linear.py` |
| `QWEN38_FLINT_ANCHOR_BACKOFF` | `0` | `engine/models/flint/serving.py` |
| `QWEN38_FLINT_KV_FP8` | `0` | `engine/models/flint/engine.py` |
| `QWEN38_FLINT_LOOKUP` | `1` | `engine/models/flint/serving.py` |
| `QWEN38_FLINT_LOOK_BACKOFF` | `0` | `engine/models/flint/serving.py` |
| `QWEN38_FLINT_PDL` | `1` | `engine/models/flint/engine.py` |
| `QWEN38_FLINT_SIDE_STREAM` | `1` | `engine/models/flint/engine.py` |
| `QWEN38_FN_ATTN_MULTI` | `0` | `engine/models/flashnext/engine.py` |
| `QWEN38_FN_DENSE` | `0` | `engine/models/flashnext/engine.py` |
| `QWEN38_FN_DRAFT_FREEZE` | `0` | `engine/models/flashnext/serving.py` |
| `QWEN38_FN_DRAFT_HOT` | `32768` | `engine/models/flashnext/serving.py` |
| `QWEN38_FN_DROP_SHARD_CACHE` | `1` | `engine/models/flashnext/load.py` |
| `QWEN38_FN_FOLD_COMMIT` | `1` | `engine/models/flashnext/engine.py` |
| `QWEN38_FN_GDN_DEFER` | `0` | `engine/models/flashnext/engine.py` |
| `QWEN38_FN_GDN_MULTI` | `1` | `engine/models/flashnext/engine.py` |
| `QWEN38_FN_GDN_PRE_MULTI` | `0` | `engine/models/flashnext/engine.py` |
| `QWEN38_FN_GEMV_MC` | `0` | `tools/fn_kernels.py` |
| `QWEN38_FN_GEMV_NB` | `0` | `tools/fn_kernels.py` |
| `QWEN38_FN_GEMV_SPLIT` | `1` | `tools/fn_kernels.py` |
| `QWEN38_FN_GROUP_ROWS` | `64` | `tools/fn_prefill_kernels.py` |
| `QWEN38_FN_JG_PLE_LATE` | `0` | `engine/models/flashnext/engine.py` |
| `QWEN38_FN_JOINT_GRAPH` | `0` | `engine/models/flashnext/engine.py` |
| `QWEN38_FN_JOINT_GRAPH_AFTER` | `2` | `engine/models/flashnext/engine.py` |
| `QWEN38_FN_JOINT_GRAPH_MAX` | `12` | `engine/models/flashnext/engine.py` |
| `QWEN38_FN_JOINT_MOE_GROUP` | `1` | `engine/models/flashnext/engine.py` |
| `QWEN38_FN_JOINT_PREFILL` | `0` | `engine/models/flashnext/joint_prefill.py` |
| `QWEN38_FN_JOINT_ROWS8` | `24` | `engine/models/flashnext/serving.py` |
| `QWEN38_FN_JOINT_ROWWISE` | `1` | `engine/models/flashnext/engine.py` |
| `QWEN38_FN_JOINT_TIMING` | `0` | `engine/models/flashnext/engine.py` |
| `QWEN38_FN_KV_FP8` | `0` | `engine/models/flashnext/engine.py` |
| `QWEN38_FN_LOOKUP` | `0` | `engine/models/flashnext/serving.py` |
| `QWEN38_FN_LOOK_BACKOFF` | `0` | `engine/models/flashnext/serving.py` |
| `QWEN38_FN_MIX` | `int8` | `engine/models/flashnext/pack.py` |
| `QWEN38_FN_MIXER_MMA` | `1` | `tools/fn_kernels.py` |
| `QWEN38_FN_MIX_FUSED` | `0` | `engine/models/flashnext/engine.py` |
| `QWEN38_FN_MOE_AS` | `0` | `tools/fn_kernels.py` |
| `QWEN38_FN_MOE_GPAR` | `0` | `tools/fn_kernels.py` |
| `QWEN38_FN_MOE_GR` | `8` | `tools/fn_kernels.py` |
| `QWEN38_FN_MOE_GY` | `0` | `tools/fn_kernels.py` |
| `QWEN38_FN_MOE_ST` | `3` | `tools/gpu/gb10.py` |
| `QWEN38_FN_MOE_STAGE` | `0` | `tools/fn_kernels.py` |
| `QWEN38_FN_MOE_WARPS` | `4` | `tools/gpu/gb10.py` |
| `QWEN38_FN_MTP_CORRECTOR` | `0` | `engine/models/flashnext/corrector.py` |
| `QWEN38_FN_MTP_CORRECTOR_FILE` | `auto` | `engine/models/flashnext/corrector.py` |
| `QWEN38_FN_MTP_COUPLED` | `0` | `engine/models/flashnext/coupled.py` |
| `QWEN38_FN_MTP_COUPLED_CAL` | `0` | `engine/models/flashnext/coupled.py` |
| `QWEN38_FN_MTP_COUPLED_FILTER` | `1` | `engine/models/flashnext/coupled.py` |
| `QWEN38_FN_MTP_COUPLED_HOT` | `0` | `engine/models/flashnext/coupled.py` |
| `QWEN38_FN_MTP_COUPLED_HOT_LONE` | `0` | `engine/models/flashnext/coupled.py` |
| `QWEN38_FN_MTP_COUPLED_SIB` | `0` | `engine/models/flashnext/coupled.py` |
| `QWEN38_FN_MTP_COUPLED_SIB_MARGIN` | `1.3` | `engine/models/flashnext/coupled.py` |
| `QWEN38_FN_MTP_COUPLED_TAU` | `0.85` | `engine/models/flashnext/coupled.py` |
| `QWEN38_FN_PACK_GRAPH` | `1` | `engine/models/flashnext/engine.py` |
| `QWEN38_FN_PACK_GRAPH_AFTER` | `2` | `engine/models/flashnext/engine.py` |
| `QWEN38_FN_PDL` | `1` | `tools/fn_kernels.py` |
| `QWEN38_FN_PF_ATTN_STAGES` | `1` | `engine/models/flashnext/engine.py` |
| `QWEN38_FN_PF_HC_FUSED` | `1` | `engine/models/flashnext/engine.py` |
| `QWEN38_FN_PF_INJ_FUSED` | `1` | `engine/models/flashnext/engine.py` |
| `QWEN38_FN_PF_NOCOPY` | `1` | `engine/models/flashnext/engine.py` |
| `QWEN38_FN_PF_ROT_ROWS` | `0` | `tools/fn_prefill_kernels.py` |
| `QWEN38_FN_PF_WINDOW` | `8192` | `engine/models/flashnext/layer_major.py` |
| `QWEN38_FN_PLE_BATCH` | `1` | `engine/models/flashnext/engine.py` |
| `QWEN38_FN_PLE_LATE` | `1` | `engine/models/flashnext/engine.py` |
| `QWEN38_FN_PLE_MULTI` | `0` | `engine/models/flashnext/engine.py` |
| `QWEN38_FN_PLE_PREFETCH` | `0` | `engine/models/flashnext/engine.py` |
| `QWEN38_FN_PLE_THREADS` | `16` | `engine/models/flashnext/ple.py` |
| `QWEN38_FN_POOL_MULTI` | `0` | `engine/models/flashnext/engine.py` |
| `QWEN38_FN_PREFILL` | `kernels` | `engine/models/flashnext/engine.py` |
| `QWEN38_FN_PREFILL_CHUNK` | `4096` | `engine/models/flashnext/engine.py` |
| `QWEN38_FN_PREFILL_MM` | `fp32` | `tools/fn_prefill_kernels.py` |
| `QWEN38_FN_QSA_EPI_TORCH` | `0` | `tools/fn_prefill_kernels.py` |
| `QWEN38_FN_QSA_LIVE` | `1` | `engine/models/flashnext/engine.py` |
| `QWEN38_FN_QSA_TORCH_BIG` | `0` | `tools/fn_seq_kernels.py` |
| `QWEN38_FN_ROUTE_FUSED` | `1` | `engine/models/flashnext/engine.py` |
| `QWEN38_FN_TABLE` | `warm` | `engine/models/flashnext/load.py` |
| `QWEN38_FN_TABLE_CACHE_GB` | `2` | `engine/models/flashnext/load.py` |
| `QWEN38_FN_TREE_PLAN` | `0` | `engine/models/flashnext/engine.py` |
| `QWEN38_FN_UNION_EVERY` | `0` | `engine/models/flashnext/engine.py` |
| `QWEN38_FN_WARM_TABLE` | `1` | `engine/models/flashnext/load.py` |
| `QWEN38_FP8_HEAD` | `none` | `engine/loader.py` |
| `QWEN38_FP8_TILES` | `none` | `tools/fp8_linear.py` |
| `QWEN38_FRESH_SEED` | `auto` | `server/app.py` |
| `QWEN38_FUSED_ADDNORM` | `0` | `1` · `engine/model.py` |
| `QWEN38_FUSED_ATTN` | `0` | `engine/model.py` |
| `QWEN38_FUSED_ATTN_PREP` | `1` | `engine/model.py` |
| `QWEN38_FUSED_COMMIT` | `0` | `1` · `engine/model.py` |
| `QWEN38_FUSED_GDN` | `1` | `engine/model.py` |
| `QWEN38_FUSED_GDNBLOCK` | `1` | `engine/model.py` |
| `QWEN38_FUSED_GDNPRE` | `1` | `engine/model.py` |
| `QWEN38_FUSED_GDNPREFILL` | `0` | `1` · `engine/model.py` |
| `QWEN38_FUSED_GDNTREE` | `1` | `engine/model.py` |
| `QWEN38_FUSED_GDNVERIFY` | `0` | `1` · `engine/model.py` |
| `QWEN38_FUSED_HEAD` | `1` | `engine/model.py` |
| `QWEN38_FUSED_NORM` | `1` | `engine/model.py` |
| `QWEN38_FUSE_PROJ` | `0` | `tools/nvfp4_linear_v2.py` |
| `QWEN38_GDNV_BV` | `16` | `tools/gdn_verify_kernels.py` |
| `QWEN38_GDNV_CONV_BLOCK` | `256` | `32` · `tools/gdn_verify_kernels.py` |
| `QWEN38_GDNV_CONV_WARPS` | `4` | `1` · `tools/gdn_verify_kernels.py` |
| `QWEN38_GDNV_ONE_WARP` | `0` | `1` · `tools/gdn_verify_kernels.py` |
| `QWEN38_GDNV_TREE_PF` | `0` | `tools/gdn_verify_kernels.py` |
| `QWEN38_GDNV_WARPS` | `4` | `tools/gdn_verify_kernels.py` |
| `QWEN38_GDNV_WY` | `0` | `1` · `tools/gdn_verify_kernels.py` |
| `QWEN38_GDNV_WY_CHAIN_MAXT` | `32` | `16` · `tools/gdn_verify_kernels.py` |
| `QWEN38_GDNV_WY_FUSED` | `0` | `1` · `tools/gdn_verify_kernels.py` |
| `QWEN38_GDNV_WY_FUSED_MAXT` | `32` | `16` · `tools/gdn_verify_kernels.py` |
| `QWEN38_GDNV_WY_KC` | `32` | `tools/gdn_verify_kernels.py` |
| `QWEN38_GDNV_WY_MAXT` | `32` | `16` · `tools/gdn_verify_kernels.py` |
| `QWEN38_GDN_AB` | `1` | `engine/model.py` |
| `QWEN38_GDN_CHUNK` | `64` | `engine/model.py` |
| `QWEN38_GDN_CHUNKED` | `1` | `engine/model.py` |
| `QWEN38_GDN_CHUNKED_BV` | `16` | `tools/gdn_chunked_kernels.py` |
| `QWEN38_GDN_CHUNKED_C` | `64` | `tools/gdn_chunked_kernels.py` |
| `QWEN38_GDN_CHUNKED_WARPS` | `4` | `tools/gdn_chunked_kernels.py` |
| `QWEN38_GDN_CHUNKED_WARPS_INTRA` | `8` | `tools/gdn_chunked_kernels.py` |
| `QWEN38_GDN_MM` | `fp32` | `engine/gdn.py` |
| `QWEN38_GDN_MM_FROM` | `64` | `engine/gdn.py` |
| `QWEN38_GDN_PREFILL_BV` | `32` | `tools/gdn_prefill_kernels.py` |
| `QWEN38_GDN_PREFILL_NOREP` | `0` | `engine/model.py` |
| `QWEN38_GDN_PREFILL_PREC` | `bf16x3` | `tools/gdn_prefill_kernels.py` |
| `QWEN38_GDN_PREFILL_PREC_A` | `tf32` | `tools/gdn_prefill_kernels.py` |
| `QWEN38_GDN_PREFILL_PREC_SCAN` | `none` | `tools/gdn_prefill_kernels.py` |
| `QWEN38_GDN_PREFILL_SERIES` | `0` | `tools/gdn_prefill_kernels.py` |
| `QWEN38_GDN_PREFILL_STAGES` | `1` | `tools/gdn_prefill_kernels.py` |
| `QWEN38_GDN_PREFILL_WARPS_INTRA` | `8` | `tools/gdn_prefill_kernels.py` |
| `QWEN38_GDN_PREFILL_WARPS_SCAN` | `8` | `tools/gdn_prefill_kernels.py` |
| `QWEN38_GDN_PREP` | `1` | `engine/model.py` |
| `QWEN38_GDN_PREP_BV` | `16` | `engine/model.py` |
| `QWEN38_GDN_PREP_WARPS` | `4` | `engine/model.py` |
| `QWEN38_GDN_SEQ` | `0` | `tools/gdn_seq_kernels.py` |
| `QWEN38_GDN_SEQ_BV` | `16` | `tools/gdn_seq_kernels.py` |
| `QWEN38_GDN_SEQ_BYNODE` | `0` | `tools/gdn_seq_kernels.py` |
| `QWEN38_GDN_SEQ_WARPS` | `1` | `tools/gdn_seq_kernels.py` |
| `QWEN38_GQA_FROM` | `64` | `engine/model.py` |
| `QWEN38_GRAPH_MAX_CTX` | `32768` | `engine/verify_graph.py` |
| `QWEN38_GRAPH_SHARE_FROM` | `2` | `engine/verify_graph.py` |
| `QWEN38_GRAPH_SHARE_OUT` | `0` | `engine/verify_graph.py` |
| `QWEN38_HEAD_GEMM` | `none` | `tools/head_gemv.py` |
| `QWEN38_HOST_ASYNC` | `0` | `engine/model.py` |
| `QWEN38_HOST_WALK` | `0` | `1` · `engine/drafters/dflash2.py` |
| `QWEN38_JOINT_ATTN_GROUP` | `1` | `engine/models/qwen3_5/attn_group.py` |
| `QWEN38_JOINT_CHAINS` | `1` | `engine/models/qwen3_5/adapter.py` |
| `QWEN38_JOINT_GDN` | `1` | `engine/model.py` |
| `QWEN38_JOINT_GRAPHS` | `1` | `engine/models/qwen3_5/joint_graphs.py` |
| `QWEN38_JOINT_GRAPHS_AFTER` | `4` | `engine/models/qwen3_5/joint_graphs.py` |
| `QWEN38_JOINT_GRAPHS_MAX` | `24` | `engine/models/qwen3_5/joint_graphs.py` |
| `QWEN38_JOINT_MAX_ROWS` | `none` | `engine/models/qwen3_5/adapter.py` |
| `QWEN38_JOINT_ROWS_AT_LANES` | `4:8` | `engine/models/qwen3_5/joint_graphs.py` |
| `QWEN38_JOINT_ROWS_A_SLOT` | `0` | `engine/models/qwen3_5/joint_graphs.py` |
| `QWEN38_JOINT_ROWWISE` | `1` | `engine/model.py` |
| `QWEN38_JOIN_VIEWS` | `1` | `engine/model.py` |
| `QWEN38_KV_FP8` | `0` | `engine/model.py` |
| `QWEN38_LATCH_PRICE` | `0` | `engine/lenrouter.py` |
| `QWEN38_LAUNCH_FIRST` | `0` | `server/app.py` |
| `QWEN38_LEN_MODE` | `none` | `wide` · `engine/lenrouter.py` |
| `QWEN38_LEN_SWITCH` | `0` | `1` · `engine/lenrouter.py` |
| `QWEN38_LFM2_KV_FP8` | `0` | `engine/models/lfm2_moe/engine.py` |
| `QWEN38_LOWER_RIGHT_FROM` | `64` | `engine/model.py` |
| `QWEN38_MEMORY_PROXIES` | `none` | `server/app.py` |
| `QWEN38_MEMORY_WRITER_TEXT` | `none` | `server/app.py` |
| `QWEN38_MM_END` | `on` | `server/app.py` |
| `QWEN38_MODEL` | `none` | `engine/config.py` |
| `QWEN38_MTP_BACKLOG_CAP` | `2048` | `engine/methods/mtp_drafter.py` |
| `QWEN38_NARRATOR_DEVICE` | `auto` | `server/conductor/inproc.py` |
| `QWEN38_NARRATOR_DIR` | `none` | `server/conductor/inproc.py` |
| `QWEN38_NH_KV_FP8` | `0` | `engine/models/nemotron_h/engine.py` |
| `QWEN38_NVFP4` | `none` | `engine/loader.py` |
| `QWEN38_NVFP4_BLOCK_TILES` | `0` | `tools/nvfp4_linear.py` |
| `QWEN38_NVFP4_DEQUANT_FROM` | `512` | `tools/nvfp4_linear.py` |
| `QWEN38_NVFP4_PREFILL_V2` | `0` | `1` · `tools/nvfp4_linear.py` |
| `QWEN38_NVFP4_PREFILL_V2_UNTIL` | `1024` | `1025` · `tools/nvfp4_linear.py` |
| `QWEN38_NVFP4_SKINNY` | `0` | `1` · `tools/nvfp4_skinny.py` |
| `QWEN38_NVFP4_SPLITK` | `0` | `tools/nvfp4_linear.py` |
| `QWEN38_NVFP4_V2` | `1` | `tools/nvfp4_linear_v2.py` |
| `QWEN38_NVFP4_V2_BN` | `0` | `tools/nvfp4_linear_v2.py` |
| `QWEN38_NVFP4_V2_DOTS` | `1` | `tools/nvfp4_linear_v2.py` |
| `QWEN38_NVFP4_V2_MAX` | `32` | `tools/nvfp4_linear_v2.py` |
| `QWEN38_NVFP4_V2_MIN` | `1` | `tools/nvfp4_linear_v2.py` |
| `QWEN38_NVFP4_V2_PREFETCH` | `0` | `tools/nvfp4_linear_v2.py` |
| `QWEN38_NVFP4_V2_W` | `0` | `tools/nvfp4_linear_v2.py` |
| `QWEN38_OWN_BURST_RATE` | `peek` | `server/app.py` |
| `QWEN38_OWN_ETA` | `on` | `server/app.py` |
| `QWEN38_OWN_RATE` | `solo` | `server/app.py` |
| `QWEN38_OWN_SIDE` | `on` | `server/app.py` |
| `QWEN38_OWN_UNITS` | `prefill` | `server/app.py` |
| `QWEN38_POOL_SNAPSHOT_TTL_S` | `5.0` | `engine/decision/device.py` |
| `QWEN38_PREFILL_CAUSAL` | `1` | `engine/model.py` |
| `QWEN38_PREFILL_WINDOW` | `8192` | `engine/cache.py` |
| `QWEN38_PREFILL_WINDOW_FREE` | `32768` | `engine/cache.py` |
| `QWEN38_PROBE_DAY_MB` | `100` | `server/probe.py` |
| `QWEN38_PROBE_DIR` | `~/.qwen38-spark-engine/probe` | `(a path)` · `server/probe.py` |
| `QWEN38_PROBE_EVERY` | `1024` | `server/probe.py` |
| `QWEN38_PROBE_SNAPSHOTS` | `off` | `off` · `server/probe.py` |
| `QWEN38_PROMPT_END` | `1` | `engine/cache.py` |
| `QWEN38_Q35M_ANCHOR_BACKOFF` | `0` | `engine/models/qwen3_5_moe/serving.py` |
| `QWEN38_Q35M_DFLASH2_HOT` | `0` | `engine/models/qwen3_5_moe/dflash2.py` |
| `QWEN38_Q35M_DFLASH2_NVFP4` | `0` | `engine/models/qwen3_5_moe/dflash2.py` |
| `QWEN38_Q35M_KV_FP8` | `0` | `engine/models/qwen3_5_moe/engine.py` |
| `QWEN38_Q35M_LOOKUP` | `1` | `engine/models/qwen3_5_moe/serving.py` |
| `QWEN38_Q35M_SIBLINGS` | `0` | `engine/models/qwen3_5_moe/serving.py` |
| `QWEN38_RANKK` | `1` | `engine/model.py` |
| `QWEN38_REGENERATE` | `sample` | `server/app.py` |
| `QWEN38_REGEN_TEMPERATURE` | `0.7` | `server/app.py` |
| `QWEN38_REGEN_TOP_K` | `20` | `server/app.py` |
| `QWEN38_REGEN_TOP_P` | `0.95` | `server/app.py` |
| `QWEN38_REREAD_SAY_TOKENS` | `8192` | `server/app.py` |
| `QWEN38_ROUND_LOG` | `none` | `server/roundlog.py` |
| `QWEN38_ROUND_LOG_HIDDEN` | `0` | `server/roundlog.py` |
| `QWEN38_ROUND_LOG_LATTICE` | `0` | `server/roundlog.py` |
| `QWEN38_ROUND_PLAN` | `off` | `engine/models/qwen3_5/roundplan.py` |
| `QWEN38_ROUND_PLAN_BUCKETS` | `4,8,16` | `engine/models/qwen3_5/roundplan.py` |
| `QWEN38_ROUND_PLAN_PRICES` | `none` | `engine/models/qwen3_5/roundplan.py` |
| `QWEN38_ROUND_PLAN_SAVE_EVERY` | `512` | `engine/models/qwen3_5/roundplan.py` |
| `QWEN38_ROUND_PLAN_STATE` | `none` | `engine/models/qwen3_5/roundplan.py` |
| `QWEN38_SAMPLED_CUT` | `0` | `engine/router.py` |
| `QWEN38_SAMPLE_BATCH` | `1` | `engine/sample.py` |
| `QWEN38_SAMPLE_KEYED_KERNEL` | `0` | `engine/sample.py` |
| `QWEN38_SAMPLE_NUCLEUS` | `1` | `engine/sample.py` |
| `QWEN38_SAMPLE_SPARSE_KEYED` | `1` | `engine/sample.py` |
| `QWEN38_SAMPLE_TOPP_K` | `1024` | `engine/sample.py` |
| `QWEN38_SCALE_ON` | `weight` | `tools/fp8_linear.py` |
| `QWEN38_SHADOW_DIR` | `none` | `server/conductor/shadow.py` |
| `QWEN38_SHADOW_DISK` | `none` | `auto` · `server/conductor/shadow.py` |
| `QWEN38_SHADOW_DISK_GB` | `20` | `20` · `server/conductor/shadow.py` |
| `QWEN38_SHADOW_GB` | `4` | `4` · `server/conductor/shadow.py` |
| `QWEN38_SHADOW_GRID` | `512` | `512` · `server/conductor/shadow.py` |
| `QWEN38_SHADOW_MODEL` | `none` | `server/conductor/shadow.py` |
| `QWEN38_SHADOW_POOL_SLACK_GIB` | `2` | `server/conductor/shadow.py` |
| `QWEN38_SIDE_CARRY` | `on` | `server/app.py` |
| `QWEN38_SIDE_CONTINUE` | `view` | `server/app.py` |
| `QWEN38_SIDE_CONV` | `on` | `server/app.py` |
| `QWEN38_SIDE_DISK` | `read` | `server/app.py` |
| `QWEN38_SIDE_GUESTS` | `on` | `server/app.py` |
| `QWEN38_SIDE_GUEST_END` | `promote` | `server/app.py` |
| `QWEN38_SIDE_GUEST_IDLE_S` | `300` | `server/app.py` |
| `QWEN38_SIDE_HOME_WAIT_S` | `10` | `server/app.py` |
| `QWEN38_SIDE_IMAGES` | `on` | `server/app.py` |
| `QWEN38_SIDE_LRU` | `on` | `server/app.py` |
| `QWEN38_SIDE_MIN_ROOM` | `16384` | `server/app.py` |
| `QWEN38_SIDE_NEW_FIRST` | `on` | `server/app.py` |
| `QWEN38_SIDE_NEW_WAIT_S` | `20` | `server/app.py` |
| `QWEN38_SIDE_RESIDENT_GB` | `0.5` | `server/app.py` |
| `QWEN38_SIDE_SLOT0_WAIT_S` | `3` | `server/app.py` |
| `QWEN38_SIDE_STASH_GB` | `1.0` | `server/app.py` |
| `QWEN38_SKINNY_LDW` | `0` | `tools/nvfp4_skinny.py` |
| `QWEN38_SKINNY_PDL` | `0` | `tools/nvfp4_skinny.py` |
| `QWEN38_SKINNY_SRUN` | `0` | `1` · `tools/nvfp4_skinny.py` |
| `QWEN38_SKINNY_SSTUB` | `0` | `tools/nvfp4_skinny.py` |
| `QWEN38_SKINNY_TILES` | `none` | `(a path)` · `tools/nvfp4_skinny.py` |
| `QWEN38_SKINNY_TILES_B` | `none` | `tools/nvfp4_skinny.py` |
| `QWEN38_SKINNY_TILES_C` | `none` | `tools/nvfp4_skinny.py` |
| `QWEN38_SKINNY_TILES_WIDE` | `none` | `(a path)` · `tools/nvfp4_skinny.py` |
| `QWEN38_SKINNY_TILES_WIDE_B` | `none` | `tools/nvfp4_skinny.py` |
| `QWEN38_SKINNY_XSTUB` | `0` | `tools/nvfp4_skinny.py` |
| `QWEN38_SLAB_TRITON` | `1` | `engine/model.py` |
| `QWEN38_SLOT0_KEEP_S` | `1800` | `server/app.py` |
| `QWEN38_SPILL_PARK` | `on` | `engine/cache.py` |
| `QWEN38_STAIR_MAX_NODES` | `none` | `engine/lenrouter.py` |
| `QWEN38_STAIR_RHO` | `none` | `(a path)` · `engine/lenrouter.py` |
| `QWEN38_STAIR_SKIP` | `0` | `1` · `engine/lenrouter.py` |
| `QWEN38_STAIR_TABLES` | `none` | `(a path)` · `engine/lenrouter.py` |
| `QWEN38_STAIR_TEMP_SAMPLED` | `none` | `engine/lenrouter.py` |
| `QWEN38_SUFFIX_STORE` | `~/.qwen38-spark-engine/suffix` | `server/app.py` |
| `QWEN38_TREE_ALIAS_STATE` | `0` | `0` · `engine/model.py` |
| `QWEN38_TREE_CHAIN_DELEGATE` | `1` | `engine/model.py` |
| `QWEN38_TREE_HOST_DEPTH` | `0` | `1` · `engine/model.py` |
| `QWEN38_TREE_MS` | `none` | `8:77.9,16:78.9,24:87.0,32:97.0` · `engine/router.py` |
| `QWEN38_TREE_NATIVE` | `1` | `engine/native_tree.py` |
| `QWEN38_TREE_NODES` | `0` | `24` · `engine/router.py` |
| `QWEN38_TREE_NODES_NARROW` | `0` | `16` · `engine/router.py` |
| `QWEN38_TREE_WIDE_AFTER` | `0` | `32` · `engine/lenrouter.py` |
| `QWEN38_TWO_STREAM` | `0` | `engine/model.py` |
| `QWEN38_UT_INVERSE` | `1` | `engine/gdn.py` |
| `QWEN38_VERIFY_GRAPH` | `0` | `1` · `engine/model.py` |
| `QWEN38_VERIFY_ROWS` | `16` | `32` · `engine/model.py` |
| `QWEN38_VERIFY_TILES` | `1` | `tools/nvfp4_verify_tiles.py` |
| `QWEN38_WARM_PREFILL_TOKENS` | `2600` | `server/app.py` |
| `QWEN38_WINDOW_MLP` | `0` | `engine/model.py` |
| `QWEN38_WINDOW_NORM_ROT` | `1` | `engine/model.py` |
| `QWEN38_WINDOW_ROWWISE` | `1` | `engine/model.py` |
| `QWEN38_WY_BLOCK` | `8` | `tools/gdn_wy_kernels.py` |
| `QWEN38_WY_BV` | `16` | `tools/gdn_wy_kernels.py` |
| `QWEN38_WY_DBG` | `0` | `tools/gdn_wy_kernels.py` |
| `QWEN38_WY_PREC` | `ieee` | `tools/gdn_wy_kernels.py` |
| `QWEN38_WY_WARPS` | `4` | `tools/gdn_wy_kernels.py` |
| `QWEN38_WY_WARPS_PREP` | `4` | `tools/gdn_wy_kernels.py` |

<!-- /generated -->

## Server variables

<!-- generated: qse-vars by tools/gen_reference.py; do not edit by hand -->

### QSE_* variables: the server's settings from the environment or serve.env

| Variable | Read in |
|-|-|
| `QSE_ADMIN_TOKEN` | `ops/make-secrets.sh, ops/serve.env, ops/start.sh` and 5 more |
| `QSE_ADVERTISE` | `ops/serve.env, server/pairing/mdns.py, server/remote/overlay.py` |
| `QSE_ALLOW_FROM` | `ops/serve.env, server/api/access.py, server/app.py` and 4 more |
| `QSE_ANSWER_RESERVE` | `server/app.py` |
| `QSE_ASSETS` | `engine/models/qwen3_5/community.py, ops/profiles/_flashnext.env, ops/profiles/_flint.env` and 16 more |
| `QSE_BACKENDS` | `install.sh, ops/engines.sh, ops/serve.env` and 5 more |
| `QSE_BARRIER_IDLE_OWNER` | `ops/serve.env, server/slots.py` |
| `QSE_CAPTURE_GUARD_DEBUG` | `engine/capture_lock.py` |
| `QSE_CHATD` | `install.sh, ops/engines.sh, ops/serve.env` and 5 more |
| `QSE_CHATD_ASKED` | `install.sh, ops/serve.env, ops/start.sh` and 1 more |
| `QSE_CHATD_ENV` | `install.sh` |
| `QSE_CHAT_DATA` | `install.sh, server/setup/doctor.py` |
| `QSE_CHAT_FORWARD_KEY` | `ops/make-secrets.sh` |
| `QSE_CHAT_URL` | `server/api/__init__.py, server/api/chat_url.py, server/mode.py` |
| `QSE_COMMUNITY_DIR` | `server/setup/community.py` |
| `QSE_COMMUNITY_ENV_DIR` | `ops/serve.env, ops/use-profile.sh` |
| `QSE_COMMUNITY_URL` | `ops/serve.env, server/setup/community.py` |
| `QSE_COMPACTION` | `server/app.py` |
| `QSE_COMPACTION_IDLE_S` | `server/app.py` |
| `QSE_COMPACTION_MIN_TOKENS` | `server/app.py` |
| `QSE_COMPACTION_PREPARE` | `server/app.py` |
| `QSE_CONDUCTOR_CONFIG` | `ops/engines.sh, ops/serve.env, ops/start.sh` and 4 more |
| `QSE_CONDUCTOR_ROOT` | `ops/roles.sh, server/conductor/residency.py` |
| `QSE_CORPUS_BUILD` | `ops/use-profile.sh` |
| `QSE_CORRECTOR_HF` | `engine/models/flashnext/corrector.py, ops/fetch_weights.py, ops/profiles/_flashnext.env` and 2 more |
| `QSE_CORRECTOR_PATH` | `engine/models/flashnext/corrector.py, ops/fetch_weights.py, ops/profiles/flashnext-3.05.env` and 1 more |
| `QSE_CORRECTOR_REVISION` | `engine/models/flashnext/corrector.py, ops/fetch_weights.py, ops/profiles/flashnext-3.05.env` and 1 more |
| `QSE_CORRECTOR_SHA256` | `engine/models/flashnext/corrector.py, ops/fetch_weights.py, ops/profiles/flashnext-3.05.env` and 1 more |
| `QSE_DASHBOARD_LOGIN` | `ops/serve.env, ops/start.sh, server/api/auth.py` and 1 more |
| `QSE_DISK_ADOPT_FROM` | `ops/serve.env, server/diskcache.py` |
| `QSE_DISK_CACHE` | `engine/models/nemotron_h/community.py, engine/models/qwen3_5/community.py, ops/engines.sh` and 11 more |
| `QSE_ENGINE_ALLOC_CONF` | `engine/cache.py, ops/profiles/_flashnext.env, ops/start.sh` |
| `QSE_ENGINE_FLAGS` | `engine/models/nemotron_h/community.py, engine/models/qwen3_5/community.py, ops/engines.sh` and 10 more |
| `QSE_ENGINE_PORT` | `ops/engine_status.py` |
| `QSE_EXL3_BPW` | `ops/profiles/_exl3.env, ops/profiles/exl3-2.5.env, ops/profiles/exl3-3.0.env` and 4 more |
| `QSE_EXT` | `ops/serve.env, server/__init__.py, server/app.py` and 6 more |
| `QSE_EXT_ARGS` | `server/app.py, server/ext.py` |
| `QSE_FAKE_CAPTURE_HOLD_S` | `server/fake_engine.py` |
| `QSE_FAKE_CAPTURE_MARK` | `server/fake_engine.py` |
| `QSE_FAKE_CARD_DIR` | `server/fake_engine.py` |
| `QSE_FAKE_PROMPTS` | `server/fake_engine.py` |
| `QSE_FETCH_ASSETS` | `ops/use-profile.sh, server/setup/apply.py` |
| `QSE_FLINT_GROUPED` | `engine/models/flint/engine.py` |
| `QSE_FLINT_VARIANT` | `ops/profiles/_flint.env, ops/profiles/flint-bf16.env, ops/profiles/flint-exl3-4.0.env` and 1 more |
| `QSE_FN_BPW` | `ops/profiles/_flashnext.env, ops/profiles/flashnext-2.05.env, ops/profiles/flashnext-3.05.env` and 3 more |
| `QSE_FN_TABLE` | `ops/profiles/_flashnext.env, ops/profiles/flashnext-2.05.env, ops/profiles/flashnext-3.05.env` and 3 more |
| `QSE_FN_VISION_FILE` | `ops/profiles/_flashnext.env, ops/profiles/flashnext-4.05.env` |
| `QSE_GRAPH_POOL_GIB` | `server/app.py` |
| `QSE_GRAPH_SET_MIN_IDLE_S` | `server/app.py` |
| `QSE_GUARD_MARGIN_GB` | `server/app.py` |
| `QSE_HANDLER_TIMEOUT_S` | `server/app.py` |
| `QSE_HEALTH_TTL_MS` | `ops/profiles/_flashnext.env, server/app.py, server/health_cache.py` |
| `QSE_IMAGE_BACKEND` | `server/app.py` |
| `QSE_KV_ADMIT_ROWS` | `ops/serve.env, server/app.py` |
| `QSE_KV_HARD_FLOOR_GB` | `ops/profiles/_flashnext.env, ops/serve.env, server/app.py` |
| `QSE_KV_MARGIN_ROWS` | `server/app.py` |
| `QSE_KV_POOL` | `engine/kvpool.py, ops/engines.sh, ops/profiles/_flashnext.env` and 7 more |
| `QSE_KV_POOL_MIN_ROWS` | `engine/kvpool.py` |
| `QSE_KV_RECOMPUTE` | `ops/serve.env, server/app.py` |
| `QSE_KV_TEST_REFUSE_AFTER` | `server/round_runner.py` |
| `QSE_KV_TEST_REFUSE_SLOTS` | `server/round_runner.py` |
| `QSE_LFM_VARIANT` | `ops/profiles/_lfm25.env, ops/profiles/lfm25-bf16.env, ops/profiles/lfm25-exl3-2.5.env` and 5 more |
| `QSE_LIVE_DECODE_WARMUP_TOKENS` | `ops/serve.env, server/api/live.py` |
| `QSE_LIVE_HZ` | `ops/serve.env, ops/start.sh, server/api/live.py` and 1 more |
| `QSE_LIVE_MOSTLY_CACHED_PCT` | `ops/serve.env, server/api/live.py` |
| `QSE_LIVE_MOSTLY_CACHED_TOKENS` | `ops/serve.env, server/api/live.py` |
| `QSE_LIVE_NOW_WINDOW_S` | `ops/serve.env, server/api/live.py` |
| `QSE_LIVE_PREFILL_WARMUP_CHUNKS` | `ops/serve.env, server/api/live.py` |
| `QSE_MANAGED_ENV` | `ops/serve.env, server/remote/overlay.py` |
| `QSE_MAX_BODY_MB` | `server/api/http_body.py, server/app.py` |
| `QSE_MEMINFO` | `ops/engines.sh` |
| `QSE_MEM_NEED_GIB` | `ops/profiles/flashnext-2.05.env, ops/profiles/flashnext-3.05.env, ops/profiles/flashnext-4.05.env` and 27 more |
| `QSE_MEM_WAIT_S` | `server/app.py` |
| `QSE_METRICS_TOKEN` | `ops/make-secrets.sh, ops/start.sh, server/api/auth.py` |
| `QSE_MODE` | `install.sh, ops/engines.sh, ops/serve.env` and 5 more |
| `QSE_NH_DENSE_ROWS` | `engine/models/nemotron_h/engine.py` |
| `QSE_NH_DIR` | `engine/models/nemotron_h/community.py, ops/profiles/_nemotron.env, ops/profiles/nemotron-super-vl-exl3-2.0.env` and 2 more |
| `QSE_NH_GGUF` | `engine/models/nemotron_h/load.py, ops/profiles/nemotron-bolt-iq2.env, server/diskcache.py` |
| `QSE_NH_HEAD` | `engine/models/nemotron_h/community.py, ops/profiles/_nemotron.env, ops/profiles/nemotron-bolt-exl3-2.0.env` and 10 more |
| `QSE_NH_JOINT_ROWS8` | `engine/models/nemotron_h/serving.py` |
| `QSE_NH_LILICORR` | `engine/models/nemotron_h/serving.py, ops/profiles/nemotron-bolt-exl3-2.0.env, ops/profiles/nemotron-bolt-exl3-2.5.env` and 8 more |
| `QSE_NH_LILICORR_PACK` | `engine/drafters/lilicorr.py` |
| `QSE_NH_LILICORR_PACK_GRAPHS` | `engine/drafters/lilicorr.py` |
| `QSE_NH_LOOKUP` | `engine/models/nemotron_h/serving.py` |
| `QSE_NH_MODEL` | `engine/models/nemotron_h/serving.py, ops/profiles/_nemotron.env, ops/profiles/nemotron-bolt-exl3-2.0.env` and 11 more |
| `QSE_NH_VARIANT` | `engine/models/nemotron_h/community.py, ops/profiles/_nemotron.env, ops/profiles/nemotron-bolt-exl3-2.0.env` and 11 more |
| `QSE_NO_ROLLBACK` | `ops/use-profile.sh, server/setup/apply.py` |
| `QSE_OPS_DIR` | `ops/start.sh, server/setup/apply.py, server/setup/device.py` |
| `QSE_ORN_DFLASH2` | `engine/models/qwen3_5_moe/serving.py, ops/profiles/_ornith.env, ops/profiles/ornith-1.5-122b-exl3-4.0.env` |
| `QSE_ORN_DRAFTER` | `ops/profiles/_ornith.env` |
| `QSE_ORN_HEAD` | `ops/profiles/_ornith.env, ops/profiles/ornith-1.5-122b-exl3-4.0.env` |
| `QSE_ORN_VARIANT` | `ops/profiles/_ornith.env, ops/profiles/ornith-1.5-122b-exl3-4.0.env` |
| `QSE_PACK_VISION` | `engine/models/nemotron_h/community.py, engine/models/qwen3_5/community.py, ops/serve.env` and 2 more |
| `QSE_PAGE_CACHE_DROP` | `engine/models/nemotron_h/community.py, ops/profiles/_flashnext.env, ops/profiles/_flint.env` and 6 more |
| `QSE_PAGE_CACHE_KEEP` | `ops/profiles/_flashnext.env, ops/serve.env, server/remote/overlay.py` |
| `QSE_PARALLEL` | `ops/engines.sh, ops/profiles/_flashnext.env, ops/profiles/_flint.env` and 6 more |
| `QSE_PARALLEL_AUTO_N` | `server/app.py, server/slots.py` |
| `QSE_PARALLEL_BATCH` | `server/app.py` |
| `QSE_PARALLEL_BUSY_WAIT_MS` | `server/slots.py` |
| `QSE_PARALLEL_CUT` | `server/app.py` |
| `QSE_PARALLEL_DRAFT` | `server/app.py` |
| `QSE_PARALLEL_DRAFT_ROWS` | `server/app.py` |
| `QSE_PARALLEL_FLOOR_GB` | `engine/kvpool.py, ops/profiles/_flashnext.env, ops/serve.env` and 1 more |
| `QSE_PARALLEL_LAYER_YIELD` | `server/app.py` |
| `QSE_PARALLEL_MAX_ROWS` | `server/app.py` |
| `QSE_PARALLEL_ROW_CAP` | `server/app.py` |
| `QSE_PARALLEL_SWITCH_MS` | `server/app.py` |
| `QSE_PARALLEL_TIMING` | `server/app.py` |
| `QSE_PARALLEL_WINDOW_ROWS` | `server/app.py` |
| `QSE_PEAK_STRICT` | `engine/cache.py, server/app.py` |
| `QSE_PEER` | `ops/engines.sh, ops/start.sh, ops/stop.sh` |
| `QSE_PEER_DISK_CACHE` | `ops/engines.sh` |
| `QSE_PEER_ENGINE_FLAGS` | `ops/engines.sh` |
| `QSE_PEER_FLOOR_GB` | `ops/engines.sh` |
| `QSE_PEER_HOST` | `ops/engines.sh` |
| `QSE_PEER_NEED_GB` | `ops/engines.sh, ops/start.sh` |
| `QSE_PEER_PORT` | `ops/engines.sh, ops/start.sh` |
| `QSE_PREFILL_BURST` | `server/app.py, server/prefill_burst.py` |
| `QSE_PREFILL_BURST_ASKING` | `server/app.py` |
| `QSE_PREFILL_BURST_WAIT_S` | `server/app.py` |
| `QSE_PRICE_TABLE` | `server/app.py` |
| `QSE_PROFILE_NAME` | `ops/serve.env, ops/start.sh, server/remote/cli.py` and 1 more |
| `QSE_PROFILE_OVERRIDE` | `ops/serve.env, ops/use-profile.sh, server/setup/apply.py` and 1 more |
| `QSE_RECLAIM_GRAPHS` | `server/app.py` |
| `QSE_REQ_PEAK` | `server/app.py` |
| `QSE_ROLES_DYNAMIC` | `ops/serve.env, server/rolemem.py` |
| `QSE_ROLE_MAX_HOLD_S` | `ops/serve.env, server/rolemem.py` |
| `QSE_ROLE_RELOAD_MARGIN_GB` | `ops/serve.env, server/rolemem.py` |
| `QSE_ROLE_RELOAD_S` | `ops/serve.env, server/rolemem.py` |
| `QSE_ROLE_THRASH_S` | `ops/serve.env, server/rolemem.py` |
| `QSE_ROLE_TICK_FAKE` | `server/app.py` |
| `QSE_ROLE_TICK_S` | `server/rolemem.py` |
| `QSE_ROLE_UNLOAD_LAYA` | `ops/serve.env, server/app.py, server/rolemem.py` |
| `QSE_ROUND_RUNNER` | `server/round_runner.py` |
| `QSE_ROUND_WALK` | `server/app.py` |
| `QSE_RUNNER_BUSY_WORK` | `ops/profiles/_flashnext.env, server/api/live.py, server/health_cache.py` and 1 more |
| `QSE_RUNNER_COMMIT_MULTI` | `server/round_runner.py` |
| `QSE_RUNNER_COUNTERS` | `engine/models/flashnext/serving.py, server/round_runner.py` |
| `QSE_RUNNER_GC` | `engine/cache.py, ops/profiles/_flashnext.env, server/app.py` and 1 more |
| `QSE_RUNNER_GC_FULL_ROUNDS` | `ops/profiles/_flashnext.env, server/round_runner.py` |
| `QSE_RUNNER_HOST_BLOCKS` | `engine/models/flashnext/serving.py, server/round_runner.py` |
| `QSE_RUNNER_JOINT1` | `engine/model.py, engine/models/qwen3_5/joint_graphs.py, server/round_runner.py` |
| `QSE_RUNNER_JOINT_PRICE` | `server/round_runner.py` |
| `QSE_RUNNER_LANE_MARGIN` | `server/round_runner.py` |
| `QSE_RUNNER_LANE_PLAIN` | `engine/models/nemotron_h/serving.py, server/round_runner.py` |
| `QSE_RUNNER_LANE_PROBE` | `server/round_runner.py` |
| `QSE_RUNNER_MTP_PACK` | `server/round_runner.py` |
| `QSE_RUNNER_PIPE` | `engine/models/flashnext/engine.py, server/round_runner.py` |
| `QSE_RUNNER_PRICED_CUT` | `server/round_runner.py` |
| `QSE_RUNNER_PRICED_EXPLORE` | `server/round_runner.py` |
| `QSE_RUNNER_ROWCAP` | `server/app.py` |
| `QSE_RUNNER_ROWCOPY` | `engine/rowcopy.py, server/round_runner.py` |
| `QSE_RUNNER_SUPERVISED` | `server/round_runner.py` |
| `QSE_RUNNER_VERDICT_WAIT_MS` | `server/round_runner.py` |
| `QSE_SECRETS` | `install.sh, ops/make-secrets.sh, ops/start.sh` and 3 more |
| `QSE_SESSION_S` | `server/api/auth.py` |
| `QSE_SETUP_ENV` | `ops/serve.env, server/setup/apply.py` |
| `QSE_SETUP_ENV_FILE` | `ops/serve.env, server/remote/overlay.py, server/setup/doctor.py` |
| `QSE_SETUP_MODE` | `ops/start.sh` |
| `QSE_SIDE_GRAPHS` | `ops/serve.env, server/app.py` |
| `QSE_SIDE_MAX_LEN` | `ops/profiles/_flashnext.env, ops/serve.env, server/app.py` and 2 more |
| `QSE_SIDE_RESIDENT_GB` | `server/app.py` |
| `QSE_SIDE_SLOT_CONST_GB` | `ops/serve.env, server/app.py` |
| `QSE_SSH_PORT` | `ops/serve.env, server/pairing/hostkey.py, server/remote/overlay.py` |
| `QSE_STACK_SAMPLE` | `server/app.py, server/stack_sampler.py` |
| `QSE_STATE_DIR` | `ops/make-secrets.sh, ops/start.sh, server/setup/apply.py` and 2 more |
| `QSE_STATUS_FILE` | `ops/engine_status.py` |
| `QSE_STATUS_HOST` | `ops/engine_status.py, ops/hold.sh, ops/serve.env` and 1 more |
| `QSE_STATUS_KEEP` | `ops/hold.sh, ops/stop.sh` |
| `QSE_STATUS_PORT` | `install.sh, ops/engine_status.py, ops/engines.sh` and 4 more |
| `QSE_STATUS_TREE` | `install.sh, ops/engine_status.py, ops/hold.sh` |
| `QSE_SUFFIX_RAW` | `server/app.py` |
| `QSE_TEST` | `server/api/ledger.py, server/app.py` |
| `QSE_USAGE_CONTEXT` | `server/app.py` |
| `QSE_USAGE_DEFAULT` | `server/app.py` |
| `QSE_USAGE_LEDGER` | `install.sh, ops/engines.sh, ops/serve.env` and 4 more |
| `QSE_VISION_ASKED` | `ops/serve.env, ops/start.sh, server/remote/overlay.py` |
| `QSE_WEIGHTS` | `engine/models/flashnext/corrector.py, engine/models/flashnext/coupled.py, engine/models/flashnext/serving.py` and 20 more |
| `QSE_WEIGHTS_ALSO` | `ops/profiles/_flashnext.env, ops/use-profile.sh` |
| `QSE_WEIGHTS_DIR` | `engine/models/nemotron_h/community.py, engine/models/qwen3_5/community.py, ops/profiles/_exl3.env` and 12 more |
| `QSE_WEIGHTS_FILES` | `engine/models/nemotron_h/community.py, engine/models/qwen3_5/community.py, ops/profiles/_exl3.env` and 9 more |
| `QSE_WEIGHTS_GB` | `engine/models/nemotron_h/community.py, engine/models/qwen3_5/community.py, ops/profiles/exl3-2.5.env` and 35 more |
| `QSE_WEIGHTS_HF` | `engine/models/nemotron_h/community.py, engine/models/qwen3_5/community.py, ops/profiles/_exl3.env` and 27 more |
| `QSE_WEIGHTS_REVISION` | `engine/models/nemotron_h/community.py, engine/models/qwen3_5/community.py, ops/profiles/_flashnext.env` and 30 more |
| `QSE_WREN_MODEL` | `server/setup/wren.py` |

<!-- /generated -->

The most used: `QSE_CONDUCTOR_CONFIG`, `QSE_USAGE_LEDGER`, `QSE_DISK_CACHE` and `QSE_BACKENDS` name the conductor's directory, the ledger, the disk tier's directory and the proxy's config database; `ops/start.sh` passes them to the service only, so a benchmark server started with `serve.env` exported never writes them. `QSE_DASHBOARD_LOGIN` turns the dashboard login on. `QSE_ADMIN_TOKEN` and `QSE_METRICS_TOKEN` live in `secrets.env`, which `ops/make-secrets.sh` writes.

The wait for memory ([Waiting for memory](../server.md)) reads these, with their code defaults: `QSE_MEM_WAIT_S` (10,800) is the longest wait in seconds of a streamed request, or of one that sends `wait_for_memory: true`; `QSE_PARALLEL_FLOOR_GB` (15) is the floor the admission keeps MemAvailable at, the same as `--parallel-floor-gb`; `QSE_KV_HARD_FLOOR_GB` (10) is the floor the KV pool's commits keep, the same as `--kv-hard-floor-gb`; `QSE_GUARD_MARGIN_GB` (5) is the margin each floor holds under the largest step the engine takes between two checks; `QSE_KV_ADMIT_ROWS` (8,192) is the part of `max_tokens` the admission reserves KV pages for, 0 for all of it; `QSE_KV_RECOMPUTE` (`on`) lets a lane the pool refuses give way and go on later; `QSE_PEAK_STRICT` (0) set to 1 makes a lone request that the memory gate refuses wait too.

## TANDEM variables

<!-- generated: tandem-vars by tools/gen_reference.py; do not edit by hand -->

### TANDEM_* variables the engine's code reads: the installer, the service scripts and the server

| Variable | Read in |
|-|-|
| `TANDEM_ADMIN_TOKEN` | read in `server/api/auth.py, server/setup/cli.py` |
| `TANDEM_DIR` | read in `install.sh, server/setup/cli.py` |
| `TANDEM_HEALTH_TIMEOUT` | read in `install.sh` |
| `TANDEM_HOST` | read in `install.sh` |
| `TANDEM_LINK_KEY` | read in `ops/engines.sh, ops/serve.env` |
| `TANDEM_LINK_LISTEN` | read in `ops/engines.sh, ops/serve.env` |
| `TANDEM_LINK_PEER` | read in `ops/engines.sh, ops/serve.env` |
| `TANDEM_MIN_FREE_GIB` | read in `install.sh` |
| `TANDEM_MODEL_NAME` | read in `install.sh` |
| `TANDEM_NODE_BIN` | read in `install.sh` |
| `TANDEM_NODE_PORT` | read in `install.sh, ops/start.sh` |
| `TANDEM_PORT` | read in `install.sh` |
| `TANDEM_REF` | read in `install.sh` |
| `TANDEM_REPO` | read in `install.sh` |
| `TANDEM_SKIP_MEMCHECK` | read in `install.sh, server/remote/lifecycle.py` |
| `TANDEM_STAGE` | read in `install.sh, ops/engines.sh` |
| `TANDEM_STAGE_CACHES` | read in `ops/engines.sh, ops/serve.env` |
| `TANDEM_STAGE_CACHE_DIR` | read in `ops/engines.sh, ops/serve.env` |
| `TANDEM_STAGE_CACHE_GB` | read in `ops/engines.sh, ops/serve.env` |
| `TANDEM_STAGE_SNAPSHOTS_GB` | read in `ops/engines.sh, ops/serve.env` |
| `TANDEM_TRUSTED_PROXY` | read in `server/api/auth.py, server/app.py` |
| `TANDEM_URL` | read in `server/setup/cli.py` |

<!-- /generated -->

## The conductor's documents

| document | what it holds | edited on |
|-|-|-|
| `profiles` | one document per profile: mode, decisions, task modes and caps, thinking, `ui`, outcomes, library, memory, shadow | Profiles |
| `router` | the classes and their policy, the classifier stages and thresholds, the structural rules, side shapes, outcomes, library | Routing |
| `packages` | every package: source, switch, limits, flags | Packages |
| `roles` | every role's contract | Profiles, Roles |
| `settings` | the default profile, transcripts, the disk tier, memory notes and the writer | Memory, Profiles |
| `deciders` | the decision models, their questions, Laya for scripts | Deciders |
| `residency` | the floor, the margin, the settle time, the load order, the claims | Profiles, Residency |
| `monitor` | the session monitor's policy (version 0 is `ops/supervisor.yaml`) | Monitor |
| `harness` | the harness's settings per backend, behind the proxy | Backends |

*The conductor's versioned documents.*

A document is JSON, checked against the conductor's schema (`GET /v1/conductor/meta` serves it, and TandemApp's `contracts/conductor-v1/` holds a copy). A version stored by an older release loads with defaults for every field it lacks, and the journal says so once.

