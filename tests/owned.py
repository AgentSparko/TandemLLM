"""Processes a test starts die with the test (SRV-732): no fake engine outlives its run.

    import owned
    owned.mark()            in a test process: its children learn whose they are (the env)
    owned.watch()           in a fake engine or backend: exit once that test process is gone

The owner is the first test process that marks (TANDEM_TEST_OWNER, inherited by every child and
grandchild, a daemonized engine included, whose parent is no longer the test). The watch polls
the owner every half second and ends the process the moment it is gone, however the test ended
(passed, failed, killed). `pgrep -f fake-engine` after a run shows nothing.
"""

from __future__ import annotations

import os
import threading
import time

ENV = "TANDEM_TEST_OWNER"


def mark() -> None:
    os.environ.setdefault(ENV, str(os.getpid()))


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def watch(every: float = 0.5) -> None:
    """A daemon thread that ends this process once its owner test process is gone."""
    try:
        owner = int(os.environ.get(ENV) or 0)
    except ValueError:
        owner = 0
    if owner <= 1 or owner == os.getpid():
        return

    def run():
        while alive(owner):
            time.sleep(every)
        os._exit(0)
    threading.Thread(target=run, name="owner-watch", daemon=True).start()
