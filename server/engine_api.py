"""The engine API's version (SRV-747): `engine_api` in the full /health answer, the setup-mode /health
and the status responder's /health (ops/engine_status.py keeps the same literal: it imports nothing
of the engine).

The contract is contracts/engine-api-v1 (README.md, routes.json), which the engine owns; TandemApp
keeps a copy pinned to an engine commit. Its version is independent of the release. A MINOR step
adds a route or an optional field; a MAJOR step removes or renames a route or a field, or changes a
meaning. An engine whose /health has no `engine_api` (every engine up to 0.15.x) speaks 1.0. 1.1
adds GET /v1/replica/identity (SRV-741). 1.2 adds the optional `split` ask and block of POST
/v1/setup/estimate and the optional `stage` block of /health (SRV-817). 1.3 moves the contract to
the engine and writes down the engine's reads, metrics, tool-call parsing and copied values; no
answer changes (ENG-427). 1.4 adds the optional `extensions` field of the full /health answer and
the 404 answers `no_ui` and `not_on_engine` of a path nothing in the engine serves (ENG-413 WP3a,
the extension host). 1.5 adds group 9, the engine-only screens as the engine's own /v1/engine/*
routes (ENG-413 WP5, ENG-433). 1.6 adds node/control.json's `chat` block: QSE_CHATD=node leaves
chatd to TandemNode, so the engine's launchers neither start nor stop it (ENG-413 WP6, SRV-823); no
route or answer changes. 1.7: the engine runs no chatd at all, QSE_CHATD is node or off (on is
read as node, unset as off), and install.sh --with-chat --app-wheel installs TandemApp's wheel and
TandemNode with chatd (ENG-413 WP7, OPS-272); no route or answer changes. 1.8: the engine serves
no page, no Chat tab forward and no other backends: /v1/chat-app*, /v1/config* and /v1/collect/*
answer 404 not_on_engine and /dashboard* 404 no_ui on every engine, with or without an extension
(ENG-413 WP14). Standard library only, nothing
imported.
"""

VERSION = "1.8"
