"""What the terminal core needs from the process hosting it, and nothing more.

The core owns everything about a session's terminal that a browser connection
touches. What it does not own - the session's record, the event log, which device
the human is at, and what typing means to status detection and delivery - it asks
for through these interfaces, so the same core can run in the daemon or in a
process of its own.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol

from aiohttp import WSMessage


@dataclass(frozen=True, slots=True)
class TerminalDescriptor:
    """What the core needs to know about the session it serves, read when needed.

    The four traits are the harness's: whether a geometry change leaves its screen
    for the core to repair (`needs_resize_repaint`), whether it answers a width
    pulse by restating a transcript held in scrollback (`repaints_scrollback`),
    whether a windowed replay of its output is a slice of a differential frame
    stream (`replay_needs_repaint`), and whether its startup palette probe stops
    accepting late OSC 10/11 replies (`suppresses_late_color_response`).
    """

    session_id: str
    backend: str
    state: str
    created_at: float
    stopping: bool
    needs_resize_repaint: bool
    repaints_scrollback: bool
    replay_needs_repaint: bool
    suppresses_late_color_response: bool

    @property
    def ended(self) -> bool:
        """An ended pane stays visible until it is dismissed, with no child behind it."""
        return self.state in {"exited", "crashed"}


class PtySink(Protocol):
    """Where the core's writes and resizes go."""

    def write(self, data: str | bytes) -> Any: ...

    def resize(self, cols: int, rows: int) -> Any: ...


class TerminalSocket(Protocol):
    """One browser connection, as the core uses it."""

    async def send_json(self, data: Any) -> None: ...

    async def send_bytes_classified(self, data: bytes, kind: str) -> None: ...

    async def receive(self) -> WSMessage: ...

    def __aiter__(self) -> AsyncIterator[WSMessage]: ...


class TerminalHost(Protocol):
    """The hosting process's side of one connection."""

    #: Stamped on every snapshot frame, so a browser can order PTY snapshots
    #: against the REST fleet snapshot (`_snapshot_generation`).
    generation: str

    def snapshot(self) -> tuple[dict[str, Any], int]:
        """The session's current record snapshot and its revision."""
        ...

    def presence_leader(self) -> str | None:
        """The device class whose human touched it most recently, or None."""
        ...

    def emit(self, kind: str, *, source: str, **payload: Any) -> None:
        """Record an event about this session, without waiting for it."""
        ...

    def note_typed_text(self, data: str) -> None:
        """The side effects typed text owes the rest of the system.

        Called after the write and after the core advanced `input_revision`.
        """
        ...

    def note_typed_bytes(self, data: bytes) -> None:
        """The same, for binary input, which carries no approval or interrupt intent."""
        ...

    def note_owner_submit(self, data: str) -> None:
        """Typed text from the input owner, after its presence was renewed."""
        ...

    async def broadcast(self, data: str) -> None:
        """Deliver typed text to every other session in the broadcast set."""
        ...
