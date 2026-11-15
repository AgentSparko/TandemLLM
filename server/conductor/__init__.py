"""The conductor's parts that run models in the engine's process (SRV-747).

The conductor itself is TandemApp's (TandemApp's tandemapp/conductor/). What stays with the engine is what
loads a model or reads the engine's memory: the shadow reader (`shadow.py`), the residency planner
(`residency.py`), and `inproc.py`, which starts the narrator's slot and answers the engine-only
screens of `/v1/conductor/*` (deciders, the residency plan, the disk tier). The engine runs the
conductor in its own process until ENG-413; then these go with the decision models' runtime.
"""
