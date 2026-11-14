"""The closed think block of the chat template (ENG-413 WP5b): what the shadow reader's question
turn ends with (server/api/incontext.py `TURN`). A copy of TandemApp's tandemapp/conductor/inject.py
`THINK_OFF`, held to it by tests/test_api_import_guard.py.
"""

from __future__ import annotations


THINK_OFF = "<think>\n\n</think>\n\n"
