"""The engine's own OpenAI wire format (ENG-413 WP2a).

The modules the request path reads a model's answer and a client's request with, owned by the
engine since the headless split:

  toolcall      the model's tool calls read out of its text, whole and as a stream
  toolformats   the forms a model writes its calls in (xml, argkey, pythonic)
  callrepair    argument names matched to the offered schema, tool mappings
  refusal       a request the engine will not serve as asked: the 400 that names the field
  thinking      a client's thinking budget in any request shape, and the answer reserve
  argloop       the tool-argument loop guard and the history cut of a stopped call

And what the engine reports of itself (ENG-413 WP2b):

  metrics       the /metrics registry and the qse_* families (contracts/engine-api-v1/metrics.md)
  usage         the per-request record, the usage frame and the cache tiers
  ledger        the usage ledger's request tables (the conductor's journal shares the file)
  live          the live view of the requests in flight
  parallel      the lanes of every request in flight (the parallel view)
  activity      a request's state, its line and the prefill's progress
  logbuf        the log ring, its streams and its export
  dashboard_api the /v1/dashboard/* reads, the system figures, GpuSampler
  http_body     a request body's framing: Content-Length, chunked, the cap
  auth          the admin token, the metrics token, trusted loopback, the session check, same_origin
  compaction    a compaction request told by its shape, served as a continuation, prepared ahead

And the small helpers the setup, the probe and the disk cache read (ENG-413 WP2c), each a copy of
the names it took out of one TandemApp module:

  validate      the JSON-schema subset the setup registry and the community index are checked with
  seeds         the built-in residency and roles documents, for a server without a conductor
  redact        the secret-pattern refusal the setup jobs' logs pass through
  textindex     the BM25 index Wren's knowledge searches
  repository    whether a directory lies inside a git work tree (the disk cache, the probe)
  chat_url      chatd's host and port from QSE_CHAT_URL (/health's loaded.chatd)

They were TandemApp's modules (tandemapp/*) run in the engine's process; these are copies from
that fork, changed only where they imported each other, the conductor's seed levels
(thinking.LEVELS), the conductor's side shapes (ledger.side_shape) or its compaction check
(compaction.is_compaction, which runs with the conductor off: tests/test_compaction_trace.py), or
read the allocator off torch's nested dict where a GPU gauge has no engine hook (metrics._allocator).
TandemApp keeps its own copies for its extension and its controller. Both copies answer
contracts/engine-api-v1/toolcalls/ alike (tests/test_contract_toolcalls.py here, TandemApp's own
test there); the ledger's two copies write one file's tables as before
(tests/test_ledger_owners.py). Nothing here imports TandemApp (tests/test_api_import_guard.py).
"""
