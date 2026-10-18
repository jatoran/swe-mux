"""What the terminal core needs from the process hosting it, and nothing more.

The core owns everything about a session's terminal that a browser connection
touches. What it does not own - the session's record, the event log, which device
the human is at, and what typing means to status detection and delivery - it asks
for through these interfaces, so the same core can run in the daemon or in a
process of its own.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol

from aiohttp import WSMessage


class TerminalDescriptor(Protocol):
    """What the core needs to know about the session it serves.

    Read live, attribute by attribute, at the moment the core needs one: a keystroke
    reads two of these, and the session's state and harness move underneath the
    core (a shell promoted to an agent changes every trait at once).

    The four traits are the harness's: whether a geometry change leaves its screen
    for the core to repair (`needs_resize_repaint`), whether it answers a width
    pulse by restating a transcript held in scrollback (`repaints_scrollback`),
    whether a windowed replay of its output is a slice of a differential frame
    stream (`replay_needs_repaint`), and whether its startup palette probe stops
    accepting late OSC 10/11 replies (`suppresses_late_color_response`).
    """

    @property
    def session_id(self) -> str: ...

    @property
    def backend(self) -> str: ...

    @property
    def state(self) -> str: ...

    @property
    def created_at(self) -> float: ...

    @property
    def stopping(self) -> bool: ...

    @property
    def ended(self) -> bool:
        """An ended pane stays visible until it is dismissed, with no child behind it."""
        ...

    @property
    def needs_resize_repaint(self) -> bool: ...

    @property
    def repaints_scrollback(self) -> bool: ...

    @property
    def replay_needs_repaint(self) -> bool: ...

    @property
    def suppresses_late_color_response(self) -> bool: ...


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

    @property
    def generation(self) -> str:
        """Stamped on every snapshot frame, so a browser can order PTY snapshots
        against the REST fleet snapshot (`_snapshot_generation`)."""
        ...

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
