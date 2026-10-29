"""The engine API's version (SRV-747): `engine_api` in the full /health answer, the setup-mode /health
and the status responder's /health (ops/engine_status.py keeps the same literal: it imports nothing
of the engine).

The contract is app/contracts/engine-api-v1 (README.md, routes.json). Its version is independent of
the release. A MINOR step adds a route or an optional field; a MAJOR step removes or renames a route
or a field, or changes a meaning. An engine whose /health has no `engine_api` (every engine up to
0.15.x) speaks 1.0. Standard library only, nothing imported.
"""

VERSION = "1.0"
