"""Keep a Windows listener accepting through one failed incoming connection.

`BaseProactorEventLoop._start_serving` closes the *listening* socket when a single
accept completion fails with `OSError` and never accepts on it again. The errors that
do that here belong to the connection, not to the listener: a client that gives up
before its `AcceptEx` completion is processed - which is what every client does while
the event loop is blocked past its timeout - leaves `WinError 64`
(`ERROR_NETNAME_DELETED`) on the queued completion, and a peer that resets between
completion and `getpeername` leaves a reset. `listener_guard.py` re-binds the socket
afterwards, but every such event still cost each local client up to the guard's
two-second tick of refused connections: 36 listener deaths in one morning of
2026-09-24, each one a moment the desktop app and the phone could not reach the daemon.

`ResilientIocpProactor.accept` absorbs those per-connection failures and arms the
next accept on the same, still-open listener, so `_start_serving` only ever sees a
connection or a genuine listener failure. It wraps the public proactor method rather
than copying CPython's private accept loop, so a Python upgrade that changes that
loop does not silently change this. The listener guard stays as the backstop for
anything this does not recognise.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

#: Per-connection accept failures. `ERROR_NETNAME_DELETED` (64) is the one measured
#: here; the rest are the other ways a peer can vanish between `AcceptEx` and
#: `getpeername`: `ERROR_SEM_TIMEOUT` (121), `ERROR_CONNECTION_ABORTED` (1236),
#: `WSAECONNABORTED` (10053), `WSAECONNRESET` (10054), `WSAENOTCONN` (10057).
TRANSIENT_ACCEPT_WINERRORS = frozenset({64, 121, 1236, 10053, 10054, 10057})

#: Consecutive absorbed failures on one accept before it is treated as a broken
#: listener after all and handed to asyncio (whose close the listener guard repairs).
MAX_CONSECUTIVE_ABSORBED = 64

#: At most one WARNING per interval; every absorbed failure is still counted.
WARNING_INTERVAL_SECONDS = 60.0


def is_transient_accept_error(exc: BaseException) -> bool:
    """Whether an accept failure describes the connection rather than the listener."""
    if isinstance(exc, ConnectionResetError | ConnectionAbortedError):
        return True
    return isinstance(exc, OSError) and getattr(exc, "winerror", None) in TRANSIENT_ACCEPT_WINERRORS


@dataclass
class AcceptStats:
    """Process-wide counters, read by `/api/diagnostics/background` (`listener_guard`)."""

    absorbed: int = 0
    last_error: str | None = None
    last_at: float | None = None
    _last_warned: float | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def note(self, exc: BaseException, listener: Any) -> None:
        now = time.monotonic()
        with self._lock:
            self.absorbed += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.last_at = time.time()
            warn = self._last_warned is None or now - self._last_warned >= WARNING_INTERVAL_SECONDS
            if warn:
                self._last_warned = now
            absorbed = self.absorbed
        level = logging.WARNING if warn else logging.DEBUG
        log.log(
            level,
            "accept_failure_absorbed listener=%s error=%s absorbed_total=%d: the "
            "connection failed, the listener stays open",
            _describe(listener),
            self.last_error,
            absorbed,
        )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "absorbed": self.absorbed,
                "last_error": self.last_error,
                "last_at": self.last_at,
            }


STATS = AcceptStats()


def _describe(listener: Any) -> str:
    try:
        host, port = listener.getsockname()[:2]
    except (OSError, ValueError, TypeError):
        return "?"
    return f"{host}:{port}"


if sys.platform == "win32":
    from asyncio import windows_events

    class ResilientIocpProactor(windows_events.IocpProactor):
        """An `IocpProactor` whose accept future fails only for a broken listener."""

        def accept(self, listener: Any) -> asyncio.Future[Any]:
            # Set by `ProactorEventLoop.__init__` through `set_loop`.
            loop: asyncio.AbstractEventLoop | None = getattr(self, "_loop", None)
            if loop is None:
                raise RuntimeError("the proactor is not attached to an event loop")
            outer: asyncio.Future[Any] = loop.create_future()
            current: list[asyncio.Future[Any]] = []
            absorbed = 0

            def attempt() -> None:
                inner = windows_events.IocpProactor.accept(self, listener)
                current[:] = [inner]
                inner.add_done_callback(settle)

            def settle(inner: asyncio.Future[Any]) -> None:
                nonlocal absorbed
                if outer.done():
                    return
                if inner.cancelled():
                    outer.cancel()
                    return
                exc = inner.exception()
                if exc is None:
                    outer.set_result(inner.result())
                    return
                if (
                    is_transient_accept_error(exc)
                    and listener.fileno() != -1
                    and absorbed < MAX_CONSECUTIVE_ABSORBED
                ):
                    absorbed += 1
                    STATS.note(exc, listener)
                    try:
                        attempt()
                    except OSError as again:
                        outer.set_exception(again)
                    return
                outer.set_exception(exc)

            def cancel_inner(future: asyncio.Future[Any]) -> None:
                if future.cancelled() and current and not current[0].done():
                    current[0].cancel()

            outer.add_done_callback(cancel_inner)
            attempt()
            return outer

    def resilient_event_loop() -> asyncio.AbstractEventLoop:
        """The daemon's event loop: a proactor loop that keeps its listeners open."""
        return asyncio.ProactorEventLoop(proactor=ResilientIocpProactor())

else:

    def resilient_event_loop() -> asyncio.AbstractEventLoop:
        """Selector loops already survive a failed accept; nothing to change there."""
        return asyncio.new_event_loop()
