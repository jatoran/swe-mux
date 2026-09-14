"""Re-bind a listening socket that asyncio closed on an accept error.

On Windows, `BaseProactorEventLoop._start_serving` closes the *listening* socket
when one accept completion fails with `OSError` (`proactor_events.py`, the
`Accept failed on a socket` handler) and never listens again. The failure that
produces it here is ordinary: while the event loop is blocked for longer than a
client's connect timeout - a 62s store-construction phase on 2026-09-14 - the
client's half-open connection is reset, and when the loop resumes the queued
`AcceptEx` completion fails with `WinError 64`. From then on the daemon is
alive, healthy, answering on every other interface, and unreachable on the one
every local client uses. The tray then reads it as hung and, once its retry
budget allows, kills it.

`ListenerGuard` polls each site's server sockets: a closed socket reports
`fileno() == -1`, which is the only trace asyncio leaves. A dead site is stopped
and started again on the same host and port, and every finding is an ERROR in
`daemon.log`, a line in `lifecycle.log`, and a counter on
`/api/diagnostics/background`. The poll is a handful of attribute reads, so its
cadence is short; the rebind is the same `TCPSite.start` the startup path uses.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

log = logging.getLogger(__name__)

#: How often each listener is checked. A dead listener costs every local client
#: a connect timeout per attempt, so the check is frequent; it reads a few
#: attributes and touches no socket.
LISTENER_GUARD_INTERVAL_SECONDS = 2.0

#: A rebind that fails (the port taken by something else in the gap) is retried
#: on the next tick; this bounds how often the failure is logged at ERROR.
REBIND_FAILURE_LOG_INTERVAL_SECONDS = 30.0


class ListeningSite(Protocol):
    """The slice of `aiohttp.web.TCPSite` the guard needs."""

    async def start(self) -> None: ...
    async def stop(self) -> None: ...


@dataclass
class GuardedListener:
    """One host:port the daemon serves and the site currently bound to it."""

    host: str
    port: int
    site: Any
    rebinds: int = 0
    last_failure_logged_at: float = 0.0
    failing_since: float | None = None


@dataclass
class ListenerGuard:
    """Watches bound sites and re-creates one whose listening socket closed."""

    make_site: Callable[[str, int], Any]
    ledger: Callable[[str], None] | None = None
    interval_seconds: float = LISTENER_GUARD_INTERVAL_SECONDS
    listeners: list[GuardedListener] = field(default_factory=list)
    checks: int = 0
    dead_found: int = 0
    rebind_failures: int = 0

    def watch(self, host: str, port: int, site: Any) -> None:
        self.listeners.append(GuardedListener(host, port, site))

    @staticmethod
    def sockets_of(site: Any) -> list[Any]:
        """The site's listening sockets, or an empty list when it has none.

        `TCPSite` keeps its `asyncio.Server` on `_server`; both are read
        defensively because a site that failed to start has neither.
        """
        server = getattr(site, "_server", None)
        sockets = getattr(server, "sockets", None)
        return list(sockets) if sockets else []

    @classmethod
    def is_dead(cls, site: Any) -> bool:
        """Whether a started site has lost its listening socket.

        asyncio closes the socket and leaves it in the server's socket list, so a
        closed socket is one whose `fileno()` reads -1. A site with no sockets at
        all is not "dead": it never started, and that is the startup path's
        failure to report, not this guard's.
        """
        sockets = cls.sockets_of(site)
        if not sockets:
            return False
        return any(cls._fileno(sock) == -1 for sock in sockets)

    @staticmethod
    def _fileno(sock: Any) -> int:
        try:
            return int(sock.fileno())
        except (OSError, ValueError, TypeError):
            return -1

    async def check_once(self) -> int:
        """Inspect every listener; rebind the dead ones. Returns how many were dead."""
        self.checks += 1
        dead = 0
        for listener in self.listeners:
            if not self.is_dead(listener.site):
                continue
            dead += 1
            if listener.failing_since is None:
                self.dead_found += 1
                listener.failing_since = time.monotonic()
                log.error(
                    "listener_dead host=%s port=%d: the listening socket was closed "
                    "(asyncio closes it after one failed accept); rebinding",
                    listener.host,
                    listener.port,
                    extra={"listener_host": listener.host, "listener_port": listener.port},
                )
                self._ledger(
                    f"listener on {listener.host}:{listener.port} died "
                    "(accept failure closed the socket); rebinding"
                )
            await self._rebind(listener)
        return dead

    async def _rebind(self, listener: GuardedListener) -> None:
        try:
            await listener.site.stop()
        except Exception as exc:  # noqa: BLE001 - a dead site's teardown must not stop the rebind
            log.debug(
                "stopping dead listener on %s:%d raised: %s", listener.host, listener.port, exc
            )
        site = self.make_site(listener.host, listener.port)
        try:
            await site.start()
        except OSError as exc:
            self.rebind_failures += 1
            now = time.monotonic()
            if now - listener.last_failure_logged_at >= REBIND_FAILURE_LOG_INTERVAL_SECONDS:
                listener.last_failure_logged_at = now
                log.error(
                    "listener_rebind_failed host=%s port=%d error=%s; retrying every %.0fs",
                    listener.host,
                    listener.port,
                    exc,
                    self.interval_seconds,
                    extra={"listener_host": listener.host, "listener_port": listener.port},
                )
            # Keep the dead site so the next tick sees it as dead and tries again.
            return
        listener.site = site
        listener.rebinds += 1
        down_for = time.monotonic() - (listener.failing_since or time.monotonic())
        listener.failing_since = None
        log.warning(
            "listener_rebound host=%s port=%d down_for_s=%.1f rebinds=%d",
            listener.host,
            listener.port,
            down_for,
            listener.rebinds,
            extra={
                "listener_host": listener.host,
                "listener_port": listener.port,
                "listener_down_s": round(down_for, 3),
            },
        )
        self._ledger(f"listener on {listener.host}:{listener.port} rebound after {down_for:.1f}s")

    async def run(self, stop: asyncio.Event) -> None:
        """Check on a cadence until `stop` is set; never raises out of the loop."""
        log.info(
            "listener guard armed interval_s=%.1f listeners=%s",
            self.interval_seconds,
            ",".join(f"{item.host}:{item.port}" for item in self.listeners),
        )
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.interval_seconds)
                return
            except TimeoutError:
                pass
            try:
                await self.check_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the guard outlives any one bad tick
                log.exception("listener guard tick failed")

    def _ledger(self, line: str) -> None:
        if self.ledger is None:
            return
        try:
            self.ledger(line)
        except Exception:  # noqa: BLE001 - a ledger write must never take the guard down
            log.debug("listener guard ledger write failed", exc_info=True)

    def snapshot(self) -> dict[str, Any]:
        """Counters for `/api/diagnostics/background`."""
        return {
            "interval_seconds": self.interval_seconds,
            "checks": self.checks,
            "dead_found": self.dead_found,
            "rebind_failures": self.rebind_failures,
            "listeners": [
                {
                    "host": item.host,
                    "port": item.port,
                    "rebinds": item.rebinds,
                    "alive": not self.is_dead(item.site),
                    "failing_since": item.failing_since,
                }
                for item in self.listeners
            ],
        }


def ledger_writer(data_dir: Path) -> Callable[[str], None]:
    """The lifecycle ledger as a one-argument writer, for the guard."""
    from .lifecycle import ledger

    return lambda line: ledger(data_dir, line)
