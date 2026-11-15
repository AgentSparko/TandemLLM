"""Pairing TandemApp with this engine host by a one-time code, and the host's advertisement on the
LAN (engine-pair-v1: contracts/engine-pair-v1, docs/pairing.md).

    spake2.py   the exchange's arithmetic and the seal, held equal to TandemApp's proxy/pairing.py
    window.py   the window (code, limits) and the exchange that installs TandemApp's key
    http.py     the routes /v1/engine/pair* on the engine's port, and the start in the engine process
    hostkey.py  the SSH host key, port and user, and the host's addresses
    mdns.py     the `_tandem._tcp` responder
    cli.py      `tandem pair`
"""
