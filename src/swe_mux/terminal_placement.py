"""Which process holds each session's terminal: this daemon, or the terminal gateway.

`development/archive/DAEMON_LOAD_ISOLATION.md` item 32. A session's terminal core - input
ownership, geometry, the keystroke accounting - lives in exactly one process at a
time, because a terminal written and resized from two processes has two owners and
two sets of books. This module is the one place that moves it.

**Which sessions the gateway holds.** A live session whose pseudoconsole the PTY
supervisor owns: the gateway reaches its bytes through the supervisor, and nothing
else. A session this daemon spawned in process has no supervisor to reach it through,
a cold one is a ring rebuilt from disk with no child behind it, and an ended one has
no child left - all three stay here (`eligible`).

**When a terminal moves.**

- *To the gateway* when the daemon connects to one (every eligible session at once,
  inside `RemoteHub.connect`, so its first `sessions` notice names the complete set) and
  when an eligible session starts while it is connected. The daemon's own core is
  retired in the same instant: it writes and resizes nothing more, and its browsers are
  closed with 4012, which sends each one to the gateway with its ring position for a
  delta.
- *Back to the daemon* when the session ends - after its `exit` frame has reached the
  gateway's browsers - and, for every session, when the gateway holding them is gone.
  The new in-process core starts from the mirror's last reading with the input
  revision moved on once, so a delivery judged against the gateway's count fails safe
  rather than landing on input the gateway accepted and never reported.

A gateway that is still alive keeps its sessions through a lost control connection:
it goes on serving their browsers, and taking them back while it does would put two
writers on each PTY. Deciding that a live gateway is hung, and ending it so its
terminals can come back, is `gateway_host.GatewayHost`'s.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

from .supervisor_client import RemotePtyHost
from .terminal_core import RemoteHub, RemoteTerminal, TerminalCore
from .terminal_core.host import TerminalHost
from .terminal_core.protocol import CLOSE_USE_GATEWAY

log = logging.getLogger(__name__)

ENDED_STATES = frozenset({"exited", "crashed"})

SessionsProvider = Callable[[], Mapping[str, Any]]
HostFactory = Callable[[Any], TerminalHost]


class TerminalPlacement:
    """Moves sessions' terminals between this daemon and the gateway behind ``hub``."""

    def __init__(
        self, hub: RemoteHub, *, sessions: SessionsProvider, host_for: HostFactory
    ) -> None:
        self.hub = hub
        self._sessions = sessions
        self._host_for = host_for
        self.handovers = 0
        self.takebacks = 0
        self.last_takeback_reason = ""

    # -- the rule ------------------------------------------------------------------------

    @staticmethod
    def eligible(session: Any) -> bool:
        """Whether the gateway may hold this session's terminal."""
        pty = session.pty
        record = session.record
        return (
            isinstance(pty, RemotePtyHost)
            and pty.sid == record.id
            and not getattr(record, "cold", False)
            and not getattr(record, "inactive", False)
            and str(record.state) not in ENDED_STATES
            and not getattr(session, "stopping", False)
        )

    @staticmethod
    def must_come_back(session: Any) -> bool:
        """Whether a terminal the gateway holds belongs here now.

        An ended session once its `exit` frame has gone out, so the browsers on the
        gateway see the end before they are moved; and a session that stopped being
        the gateway's to carry at all. A session merely stopping stays where it is
        until it ends: moving it would bounce its browsers for nothing.
        """
        record = session.record
        if str(record.state) in ENDED_STATES:
            return bool(getattr(session.terminal, "exit_sent", True))
        pty = session.pty
        return (
            not isinstance(pty, RemotePtyHost)
            or pty.sid != record.id
            or bool(getattr(record, "cold", False))
            or bool(getattr(record, "inactive", False))
        )

    @staticmethod
    def held_by_gateway(session: Any) -> bool:
        return isinstance(session.terminal, RemoteTerminal)

    @property
    def remote_count(self) -> int:
        """Sessions whose terminal the gateway holds now."""
        return len(self.hub.terminals)

    # -- moving one terminal ---------------------------------------------------------------

    def _to_gateway(self, session: Any) -> None:
        core = session.terminal
        if not isinstance(core, TerminalCore):
            return
        session.terminal = self.hub.adopt(
            stream=core.stream,
            descriptor=core.descriptor,
            host=self._host_for(session),
            seed=core,
        )
        core.retire(CLOSE_USE_GATEWAY, b"moved")
        self.handovers += 1

    def _to_daemon(self, session: Any) -> None:
        remote = session.terminal
        if not isinstance(remote, RemoteTerminal):
            return
        core = session.new_terminal_core()
        remote.hand_back(core)
        session.terminal = core
        self.takebacks += 1

    # -- when they move ----------------------------------------------------------------------

    def adopt_all(self) -> int:
        """Hand every eligible session to the gateway. Inside `RemoteHub.connect`,
        before anything is sent, so this never awaits."""
        moved = 0
        registry = self._sessions()
        self._release_stale(registry)
        for session in list(registry.values()):
            if self.eligible(session) and not self.held_by_gateway(session):
                self._to_gateway(session)
                moved += 1
        if moved:
            log.info("terminal gateway now holds %d session terminal(s)", self.remote_count)
        return moved

    def place(self, session: Any) -> None:
        """Put one session's terminal where it belongs now: a session that just started
        goes to a connected gateway, one that ended comes back."""
        if self.held_by_gateway(session):
            if self.must_come_back(session):
                terminal = session.terminal
                self._to_daemon(session)
                self.hub.release(terminal.session_id)
            return
        if self.hub.connected and self.eligible(session):
            self._to_gateway(session)

    def reconcile(self) -> None:
        """Catch what no hook reported: a session removed from the registry or replaced
        under its id, and anything started or ended between hooks."""
        registry = self._sessions()
        self._release_stale(registry)
        for session in list(registry.values()):
            self.place(session)

    def take_back_all(self, reason: str) -> int:
        """Every terminal the gateway held comes back: the gateway is gone."""
        registry = self._sessions()
        taken = 0
        for sid, terminal in list(self.hub.terminals.items()):
            session = registry.get(sid)
            if session is not None and session.terminal is terminal:
                self._to_daemon(session)
                taken += 1
            # Released without a word to the gateway when it is gone, which it is.
            self.hub.release(sid)
        if taken:
            self.last_takeback_reason = reason
            log.warning(
                "took %d session terminal(s) back from the terminal gateway: %s", taken, reason
            )
        return taken

    def _release_stale(self, registry: Mapping[str, Any]) -> None:
        for sid, terminal in list(self.hub.terminals.items()):
            session = registry.get(sid)
            if session is None or session.terminal is not terminal:
                self.hub.release(sid)

    def stats(self) -> dict[str, Any]:
        return {
            "remote": self.remote_count,
            "handovers": self.handovers,
            "takebacks": self.takebacks,
            "last_takeback_reason": self.last_takeback_reason or None,
        }


__all__ = ["ENDED_STATES", "TerminalPlacement"]
