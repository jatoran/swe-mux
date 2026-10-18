"""The terminal gateway's control protocol: what the daemon and the gateway say.

`development/archive/DAEMON_LOAD_ISOLATION.md` item 27. The gateway hosts terminal cores
for the daemon's sessions; the daemon keeps the session's record, its event log
and every decision about what typing means. They talk over `framed_ipc` - a
loopback connection authenticated by a shared token, with the protocol version
checked at `hello` - and this module is the vocabulary.

**Daemon to gateway**, all notices so they apply in the order they were sent:
`upsert` and `remove` a session, `sessions` (the complete set the daemon serves
through this gateway), `update` and `exit` (a snapshot frame for attached
browsers, with the descriptor it implies), `presence` (the leading device class),
`pending_submit` (whether a delivery sits unsubmitted in the composer), `write`
and `repaint` (each carrying a request id the result answers), `state_ack`, and
`quit` (the daemon is quitting, so the gateway exits too).
`diagnostics` and `ping` are the requests: they want an answer and order against
nothing, and `ping`'s answer is empty - it exists to be answered.

**Gateway to daemon**: `state`, one per session and coalesced - the authoritative
`input_revision`, the last input's age, the keystroke writes whose side effects
the daemon owes (in order, because interrupt detection and the composer estimate
read each write on its own), and the terminal's mode, owner, geometry and attach
count, and the composer's screen reading while it is current; `event` for the
event log; `write_result` and `repaint_result`;
`broadcast`, typed text the daemon delivers to the rest of the broadcast set; and
`metering`, the browser traffic the gateway carried, for the daemon's counters.

**Coalescing is acknowledged, not timed.** A session has at most one `state` in
flight; later changes fold into the next one, which goes out when the daemon's
`state_ack` arrives. A stalled daemon therefore costs the gateway one pending
summary per session - bounded writes, not a queue - and a reconnect sends every
session's whole state again.

**Times cross as ages**, never as `time.monotonic()` readings, which mean nothing
in another process.

**No notice may carry `id` or `ok`.** To `framed_ipc` a frame with `ok` is a reply
and one with `id` a request, so a result says `written`, and a request id is `rid`.
"""

from __future__ import annotations

import base64
from dataclasses import asdict, dataclass, fields
from typing import Any

from .core import WriteCondition

GATEWAY_PROTOCOL_VERSION = 1

#: Optional features, negotiated at `hello`. A peer that did not agree one is
#: never sent its messages.
CAP_DIAGNOSTICS = "diagnostics"
#: `ping`, a request the gateway answers at once and with nothing: how the daemon
#: tells a gateway that is busy from one whose loop has stopped (item 32).
CAP_PING = "ping"
GATEWAY_CAPABILITIES: tuple[str, ...] = (CAP_DIAGNOSTICS, CAP_PING)

# Daemon -> gateway.
UPSERT = "upsert"
REMOVE = "remove"
SESSIONS = "sessions"
UPDATE = "update"
EXIT = "exit"
PRESENCE = "presence"
PENDING_SUBMIT = "pending_submit"
WRITE = "write"
REPAINT = "repaint"
STATE_ACK = "state_ack"
QUIT = "quit"
DIAGNOSTICS = "diagnostics"
PING = "ping"

# Gateway -> daemon.
STATE = "state"
EVENT = "event"
WRITE_RESULT = "write_result"
REPAINT_RESULT = "repaint_result"
BROADCAST = "broadcast"
METERING = "metering"

#: WebSocket close codes a browser acts on (`DAEMON_LOAD_ISOLATION.md` items 30 and
#: 32): reconnect to the daemon for this session, wait for the gateway to return,
#: or - from the daemon - reconnect to the gateway, which holds this terminal.
CLOSE_USE_DAEMON = 4010
CLOSE_GATEWAY_RESTARTING = 4011
CLOSE_USE_GATEWAY = 4012

#: How much keystroke history one session's pending summary holds while the daemon
#: is not acknowledging. Past either bound the oldest writes are dropped and counted:
#: their side effects are display evidence and approval cancellation, and the
#: daemon treats a gap as "the operator typed" (`STATE`'s `dropped`).
MAX_PENDING_WRITES = 1024
MAX_PENDING_WRITE_BYTES = 256 * 1024
#: Events held while no daemon is attached, oldest dropped first.
MAX_PENDING_EVENTS = 512

#: One keystroke write in a `state` summary: typed text (`TEXT`, with whether it was
#: also the owner's submit), an owner submit on its own (`SUBMIT`), or binary input
#: (`BYTES`, base64 on the wire).
WRITE_TEXT = "t"
WRITE_SUBMIT = "o"
WRITE_BYTES = "b"


@dataclass(frozen=True, slots=True)
class DescriptorState:
    """What the gateway's core needs to know about a session (`TerminalDescriptor`).

    The daemon owns all of it - the record, the stopping flag and the harness
    registry - so it is sent rather than derived, and re-sent with every snapshot
    frame, since a promoted shell changes every trait at once.
    """

    session_id: str
    backend: str
    state: str
    created_at: float
    stopping: bool = False
    ended: bool = False
    needs_resize_repaint: bool = False
    repaints_scrollback: bool = False
    replay_needs_repaint: bool = False
    suppresses_late_color_response: bool = False

    def to_wire(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_wire(cls, value: Any) -> DescriptorState:
        if not isinstance(value, dict):
            raise ValueError("a descriptor must be an object")
        known = {field.name for field in fields(cls)}
        data = {key: item for key, item in value.items() if key in known}
        missing = {"session_id", "backend", "state", "created_at"} - data.keys()
        if missing:
            raise ValueError(f"descriptor is missing {sorted(missing)}")
        return cls(
            session_id=str(data["session_id"]),
            backend=str(data["backend"]),
            state=str(data["state"]),
            created_at=float(data["created_at"]),
            stopping=bool(data.get("stopping", False)),
            ended=bool(data.get("ended", False)),
            needs_resize_repaint=bool(data.get("needs_resize_repaint", False)),
            repaints_scrollback=bool(data.get("repaints_scrollback", False)),
            replay_needs_repaint=bool(data.get("replay_needs_repaint", False)),
            suppresses_late_color_response=bool(
                data.get("suppresses_late_color_response", False)
            ),
        )

    @classmethod
    def of(cls, descriptor: Any) -> DescriptorState:
        """A snapshot of any live `TerminalDescriptor`."""
        return cls(
            session_id=descriptor.session_id,
            backend=descriptor.backend,
            state=descriptor.state,
            created_at=descriptor.created_at,
            stopping=descriptor.stopping,
            ended=descriptor.ended,
            needs_resize_repaint=descriptor.needs_resize_repaint,
            repaints_scrollback=descriptor.repaints_scrollback,
            replay_needs_repaint=descriptor.replay_needs_repaint,
            suppresses_late_color_response=descriptor.suppresses_late_color_response,
        )


def condition_to_wire(condition: WriteCondition | None) -> dict[str, Any] | None:
    if condition is None:
        return None
    return {
        "input_revision": condition.input_revision,
        "operator_quiet_seconds": condition.operator_quiet_seconds,
        "no_pending_submit": condition.no_pending_submit,
        "screen_position": condition.screen_position,
    }


def condition_from_wire(value: Any) -> WriteCondition | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("a write condition must be an object")
    revision = value.get("input_revision")
    quiet = value.get("operator_quiet_seconds")
    if revision is not None and (not isinstance(revision, int) or isinstance(revision, bool)):
        raise ValueError("a condition's input_revision must be an integer")
    if quiet is not None and (not isinstance(quiet, int | float) or isinstance(quiet, bool)):
        raise ValueError("a condition's operator_quiet_seconds must be a number")
    screen = value.get("screen_position")
    if screen is not None and (not isinstance(screen, int) or isinstance(screen, bool)):
        raise ValueError("a condition's screen_position must be an integer")
    return WriteCondition(
        input_revision=revision,
        operator_quiet_seconds=float(quiet) if quiet is not None else None,
        no_pending_submit=bool(value.get("no_pending_submit", False)),
        screen_position=screen,
    )


def encode_write_entry(kind: str, data: str | bytes, *, submit: bool = False) -> list[Any]:
    """One keystroke write for a `state` summary."""
    if kind == WRITE_BYTES:
        assert isinstance(data, bytes)
        return [WRITE_BYTES, base64.b64encode(data).decode("ascii")]
    assert isinstance(data, str)
    return [kind, data, 1] if submit else [kind, data]


def decode_write_entry(entry: Any) -> tuple[str, str | bytes, bool]:
    """``(kind, data, submit)`` from one `state` summary write."""
    if not isinstance(entry, list) or len(entry) < 2 or not isinstance(entry[1], str):
        raise ValueError(f"malformed write entry: {entry!r}")
    kind = entry[0]
    if kind == WRITE_BYTES:
        return WRITE_BYTES, base64.b64decode(entry[1]), False
    if kind not in {WRITE_TEXT, WRITE_SUBMIT}:
        raise ValueError(f"unknown write kind: {kind!r}")
    return kind, entry[1], len(entry) > 2 and bool(entry[2])


def entry_size(entry: list[Any]) -> int:
    return len(entry[1])


def optional_age(value: Any) -> float | None:
    """An age from the wire: a non-negative number, or None for "never"."""
    if value is None:
        return None
    if not isinstance(value, int | float) or isinstance(value, bool) or value < 0:
        raise ValueError(f"an age must be a non-negative number: {value!r}")
    return float(value)


__all__ = [
    "BROADCAST",
    "CAP_DIAGNOSTICS",
    "CAP_PING",
    "CLOSE_GATEWAY_RESTARTING",
    "CLOSE_USE_DAEMON",
    "CLOSE_USE_GATEWAY",
    "DIAGNOSTICS",
    "EVENT",
    "EXIT",
    "GATEWAY_CAPABILITIES",
    "GATEWAY_PROTOCOL_VERSION",
    "METERING",
    "QUIT",
    "MAX_PENDING_EVENTS",
    "MAX_PENDING_WRITES",
    "MAX_PENDING_WRITE_BYTES",
    "PENDING_SUBMIT",
    "PING",
    "PRESENCE",
    "REMOVE",
    "REPAINT",
    "REPAINT_RESULT",
    "SESSIONS",
    "STATE",
    "STATE_ACK",
    "UPDATE",
    "UPSERT",
    "WRITE",
    "WRITE_BYTES",
    "WRITE_RESULT",
    "WRITE_SUBMIT",
    "WRITE_TEXT",
    "DescriptorState",
    "condition_from_wire",
    "condition_to_wire",
    "decode_write_entry",
    "encode_write_entry",
    "entry_size",
    "optional_age",
]
