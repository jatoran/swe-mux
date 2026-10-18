"""A real `Session` over a PTY that only records what it is sent.

Every write into a terminal goes through the session's terminal core
(`terminal_core.TerminalCore.write`), which keeps the input accounting delivery
readiness reads. A stub session with a bare `pty.write` skips that path entirely,
so a test of what gets written - and of the evidence it leaves - builds one of
these instead and reads the evidence where the production code keeps it.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from swe_mux.models import SessionRecord
from swe_mux.session import Session


def recording_session(
    record: SessionRecord, writes: list[Any], *, alive: bool = True
) -> Session:
    """A live session whose PTY appends every write to ``writes``."""
    pty = SimpleNamespace(
        write=writes.append,
        resize=lambda cols, rows: None,
        isalive=lambda: alive,
    )
    return Session(record, cast(Any, pty), cast(Any, SimpleNamespace()), 64 * 1024, "secret")
