"""A real `Session` over a supervisor-owned PTY, with the supervisor replaced by a
recorder: what the daemon would have written to the child is a list to assert on.

The terminal gateway may hold only a session like this one (`terminal_placement`),
which is why the handoff tests need it rather than any in-process stand-in.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from swe_mux.models import SessionRecord
from swe_mux.session import Session
from swe_mux.supervisor_client import RemotePtyHost

#: The supervisor's name for a session's byte stream, which the daemon's ring and the
#: gateway's both count positions in.
SUPERVISOR_STREAM = "sup-stream-1"


class RecordingSupervisorClient:
    """What the daemon's `RemotePtyHost` would send the supervisor."""

    def __init__(self) -> None:
        self.notices: list[tuple[dict[str, Any], bytes]] = []

    def notify(self, header: dict[str, Any], payload: bytes = b"") -> None:
        self.notices.append((dict(header), payload))

    def written(self) -> list[bytes]:
        return [payload for header, payload in self.notices if header.get("t") == "write"]


def supervised_session(
    sid: str = "s1", *, state: str = "idle"
) -> tuple[Session, RecordingSupervisorClient]:
    client = RecordingSupervisorClient()
    pty = RemotePtyHost(cast(Any, client), sid)
    pty.stream = SUPERVISOR_STREAM
    record = SessionRecord(
        sid, f"claude-{sid}", "default", "claude", sid, ".", "claude", [], state=state
    )
    session = Session(record, cast(Any, pty), cast(Any, SimpleNamespace()), 64 * 1024, "secret")
    return session, client
