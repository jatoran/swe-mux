"""The terminal core: one session's terminal, independent of the process hosting it.

It owns everything a browser connection touches - the attach handshake, replay,
deltas and resyncs over the retained ring, the subscriber queues and their output
credit, input ownership and arbitration, the shared geometry, the repaint pulses,
client frames and their rate limits, and the input accounting delivery readiness
reads (`input_revision`, the last input's time, the composer estimate).

What it does not own it asks its host for (`host.py`): the session's record and
revision, the event log, which device the human is at, and what typing means to
the rest of the daemon. The daemon hosts it in process (`routes/pty.py`), and a
`Session` keeps its old attributes as views onto it, so no reader changed when it
moved here. Keeping the host interface this narrow is what lets the same core run
in a terminal gateway process (`development/DAEMON_LOAD_ISOLATION.md`).

The gateway half is here too, process-agnostic: `protocol.py` is what the daemon
and the gateway say, `gateway.py` hosts cores for the daemon's sessions, and
`remote.py` is the daemon's mirror of a terminal the gateway holds, which stands
on `Session.terminal` where a `TerminalCore` stands in process.

Nothing here may import the daemon's session, observation or route modules.
"""

from __future__ import annotations

from .connection import PTY_ATTACH_READY_TIMEOUT_SECONDS, pty_sender, serve_connection
from .core import (
    CLAIM_LOG_LIMIT,
    CLIENT_REPAINT_MIN_INTERVAL_SECONDS,
    RESIZE_REPAINT_SETTLE_SECONDS,
    TerminalCore,
    WriteCondition,
    WriteRefused,
)
from .flow import PTY_OUTPUT_BATCH_BYTES, PTY_OUTPUT_HIGH_WATER_BYTES, PtyOutputFlow
from .gateway import GatewayHub, GatewaySession
from .host import PtySink, TerminalDescriptor, TerminalHost, TerminalSocket
from .input import is_default_color_response, pointer_report_kind
from .protocol import GATEWAY_PROTOCOL_VERSION, DescriptorState
from .remote import RemoteHub, RemoteTerminal, TerminalUnavailable
from .stream import PtySubscriber, TerminalStream

__all__ = [
    "CLAIM_LOG_LIMIT",
    "CLIENT_REPAINT_MIN_INTERVAL_SECONDS",
    "GATEWAY_PROTOCOL_VERSION",
    "PTY_ATTACH_READY_TIMEOUT_SECONDS",
    "PTY_OUTPUT_BATCH_BYTES",
    "PTY_OUTPUT_HIGH_WATER_BYTES",
    "RESIZE_REPAINT_SETTLE_SECONDS",
    "DescriptorState",
    "GatewayHub",
    "GatewaySession",
    "PtyOutputFlow",
    "PtySink",
    "PtySubscriber",
    "RemoteHub",
    "RemoteTerminal",
    "TerminalCore",
    "TerminalDescriptor",
    "TerminalHost",
    "TerminalSocket",
    "TerminalStream",
    "TerminalUnavailable",
    "WriteCondition",
    "WriteRefused",
    "is_default_color_response",
    "pointer_report_kind",
    "pty_sender",
    "serve_connection",
]
