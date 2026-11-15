"""`tandem remote` (engine-ctl-v1): the engine's control verbs over an SSH forced command.

server/remote/cli.py is the entry point (`python -m server.remote`), grammar.py the command line,
journal.py the call journal, overlay.py the managed settings overlay, lifecycle.py the start, stop
and restart jobs, keys.py `unpair`, paths.py where the files are. docs/remote-control.md says how
to use it; contracts/engine-ctl-v1 is the contract. Standard library only.
"""
