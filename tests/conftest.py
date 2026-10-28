"""pytest's setup for tests/, loaded before any test module is collected.

chatd/sandbox.py reads its limit (QSE_SANDBOX_MEM_GB) and its socket (QSE_SANDBOX_SOCKET) once,
when it is imported. tests/chatd_util.py pins both for the tests, but a test module that imports
chatd before it (tests/test_sandbox_box.py did; pytest collects in the order it is given) fixed
the defaults for the whole session: the 4 GiB limit failed six tests in
tests/test_chatd_agent.py, and on a box with Docker a Chat could find the real sandbox. The same
pins here hold whatever is collected first; chatd_util keeps them for a run without pytest.

No test reaches this machine's own TandemNode (tests/realnode.py, SRV-745): the guard is on from
here, and each test is checked after it ran (scriptmode.run in script mode, the fixture below).
"""

import os
import tempfile

import realnode

if os.environ.get("QSE_SANDBOX_BOX") != "1":
    os.environ.setdefault("QSE_SANDBOX_SOCKET",
                          os.path.join(tempfile.gettempdir(), "qse-tests-no-sandbox.sock"))
os.environ.setdefault("QSE_SANDBOX_MEM_GB", "1")
realnode.guard()

try:
    import pytest
except ImportError:                                        # script mode on a box without pytest
    pytest = None

if pytest is not None:
    @pytest.fixture(autouse=True)
    def _real_node():
        yield
        realnode.check()
