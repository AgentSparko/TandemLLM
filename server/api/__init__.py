"""The engine's own OpenAI wire format (ENG-413 WP2a).

The modules the request path reads a model's answer and a client's request with, owned by the
engine since the headless split:

  toolcall      the model's tool calls read out of its text, whole and as a stream
  toolformats   the forms a model writes its calls in (xml, argkey, pythonic)
  callrepair    argument names matched to the offered schema, tool mappings
  refusal       a request the engine will not serve as asked: the 400 that names the field
  thinking      a client's thinking budget in any request shape, and the answer reserve
  argloop       the tool-argument loop guard and the history cut of a stopped call

They were TandemApp's modules (tandemapp/*) run in the engine's process; these are copies from
that fork, changed only where they imported each other or the conductor's seed levels
(thinking.LEVELS). TandemApp keeps its own copies for its extension and its controller. Both
copies answer contracts/engine-api-v1/toolcalls/ alike (tests/test_contract_toolcalls.py here,
TandemApp's own test there). Nothing here imports TandemApp (tests/test_api_import_guard.py).
"""
