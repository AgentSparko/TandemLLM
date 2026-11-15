"""The engine's HTTP server (server/app.py) and the modules it runs.

The engine imports no module of TandemApp. Chat mode runs an extension that `QSE_EXT` names, loaded
by name through engine-ext-v1 (server/ext.py, contracts/engine-ext-v1); without one the engine is
headless (ENG-413).
"""
