"""The worker process: the daemon's bulk work, in an interpreter of its own.

`development/DAEMON_LOAD_ISOLATION.md` item 19. The daemon serves every terminal
keystroke from one event loop, and every thread it runs competes with that loop
for one GIL. Work that is heavy and not interactive - process inspection, the
canonical telemetry ledgers - therefore runs here instead, in a process the
daemon spawns, owns and restarts (`worker_host.py`), and talks to over
`framed_ipc`.

**The worker holds no state the daemon cannot give it back.** One worker process
hosts every service, so a crash restarts all of them at once, and each service
is rebuilt on reconnect from what the daemon pushes to it. Nothing here writes
`mux.db`: the daemon stays its only writing process.

**It dies with the daemon, three ways over.** The daemon holds it in a
kill-on-close Job on Windows (a process group with a guardian on POSIX), the
daemon's own exit closes the control connection and the worker exits on that
EOF, and if neither has happened before the daemon pid itself is gone, the
liveness check below notices.

**How it is started matters.** Frozen, it is `swe-mux.exe --worker-child`, never
`swe-mux.exe -m swe_mux.worker`: on the app's own image that shape is a session
helper to the redeploy (which spares it) and to the process inspector (which
keeps it session-owned), and a worker is neither. From source it is
`python -m swe_mux.worker`, as the source supervisor is `-m swe_mux.supervisor`:
both of those checks apply only to the frozen image, and `-m swe_mux` would load
the whole daemon - aiohttp, the server, every store - into a process that needs
none of it.

The token arrives on stdin rather than in argv or the environment, which any
process of the same user can read; the port is not a secret and is an argument.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from . import __version__
from .framed_ipc import FramedPeer, HandshakeRefused, RequestRefused, connect

log = logging.getLogger(__name__)

#: Bumped only when a message the two ends must agree on changes shape; the
#: daemon and its worker are always the same build, so this guards a mismatched
#: pair (a source daemon and a stale frozen worker) rather than an upgrade path.
WORKER_PROTOCOL = 1
WORKER_ROLE = "worker"
#: The longest token line the worker will read before giving up on its parent.
TOKEN_MAX_BYTES = 4096
CONNECT_TIMEOUT_SECONDS = 15.0
#: How often the worker checks that its daemon still exists, as a backstop for an
#: OS that did not close the connection when the daemon died.
DAEMON_CHECK_SECONDS = 5.0

Reply = tuple[Mapping[str, Any], bytes] | Mapping[str, Any] | None


class WorkerService(Protocol):
    """One service the worker hosts, addressed as `<name>.<method>`."""

    name: str

    async def start(self, worker: Worker) -> None: ...

    async def handle(self, method: str, header: dict[str, Any], payload: bytes) -> Reply: ...

    async def stop(self) -> None: ...


class NoopService:
    """The service item 19 proves the host with, and a probe for everything after.

    `echo` answers what it was sent; `configure` and `describe` hold state the
    daemon pushes, which is how a test shows a restarted worker is rebuilt from the
    daemon rather than remembering anything; `announce` sends a notice back.
    """

    name = "noop"

    def __init__(self) -> None:
        self.state: dict[str, Any] = {}
        self.configured = 0
        self._worker: Worker | None = None

    async def start(self, worker: Worker) -> None:
        self._worker = worker

    async def handle(self, method: str, header: dict[str, Any], payload: bytes) -> Reply:
        if method == "echo":
            return {"echo": header.get("value")}, payload
        if method == "configure":
            state = header.get("state")
            if not isinstance(state, dict):
                raise RequestRefused("configure needs a `state` object")
            self.state = dict(state)
            self.configured += 1
            return {"configured": self.configured}
        if method == "describe":
            return {"state": self.state, "configured": self.configured}
        if method == "announce":
            if self._worker is not None:
                self._worker.notify(self.name, "announced", {"value": header.get("value")})
            return None
        raise RequestRefused(f"unknown method {self.name}.{method}")

    async def stop(self) -> None:
        self._worker = None


# The factories import their modules when called rather than at the top: the
# daemon imports this module for its constants (`worker_host.py`), and must not
# load the worker's services with it.


def _process_service() -> WorkerService:
    from .process_service import ProcessService

    return ProcessService()


def _ghost_window_service() -> WorkerService:
    from .ghost_windows import GhostWindowService

    return GhostWindowService()


def _telemetry_service() -> WorkerService:
    from .telemetry_worker import TelemetryWorkerService

    return TelemetryWorkerService()


#: Every service the worker can host, by name. Item 22 adds its own here.
SERVICES: dict[str, Callable[[], WorkerService]] = {
    "noop": NoopService,
    "processes": _process_service,
    "ghost_windows": _ghost_window_service,
    "telemetry": _telemetry_service,
}


class Worker:
    """The worker's side of the control connection, and the services behind it."""

    def __init__(self, services: Mapping[str, Callable[[], WorkerService]] | None = None) -> None:
        factories = SERVICES if services is None else services
        self.services: dict[str, WorkerService] = {name: make() for name, make in factories.items()}
        #: Held by every service while it walks the host (the process pass, the
        #: ghost-window sweep). Two such threads take this interpreter's one GIL in
        #: turn, and the sweep needs it once per top-level window: run together under
        #: load, a 4 ms sweep took 290 ms and a pass 0.6 s (`DAEMON_LOAD_ISOLATION.md`
        #: item 20). One at a time, neither waits on the other's GIL.
        self.host_scan_lock = asyncio.Lock()
        self.started_at = time.monotonic()
        self.peer: FramedPeer | None = None
        self._stop = asyncio.Event()

    def notify(
        self, service: str, method: str, fields: Mapping[str, Any], payload: bytes = b""
    ) -> None:
        """Send the daemon a notice from a service; dropped once disconnected."""
        peer = self.peer
        if peer is None or peer.closed:
            return
        peer.notify({**fields, "t": f"{service}.{method}"}, payload)

    def request_stop(self) -> None:
        self._stop.set()

    async def run(self, host: str, port: int, token: str, *, daemon_pid: int | None) -> int:
        """Serve until the daemon goes away or asks the worker to stop; the exit code."""
        for service in self.services.values():
            await service.start(self)
        try:
            self.peer = await connect(
                host,
                port,
                token=token,
                protocol=WORKER_PROTOCOL,
                hello={
                    "role": WORKER_ROLE,
                    "pid": os.getpid(),
                    "build": __version__,
                    "services": sorted(self.services),
                },
                name="worker",
                timeout_seconds=CONNECT_TIMEOUT_SECONDS,
                on_request=self._on_request,
                on_close=lambda _peer, _reason: self._stop.set(),
            )
        except HandshakeRefused as refusal:
            log.error("worker could not reach its daemon on %s:%s: %s", host, port, refusal)
            await self._stop_services()
            return 2
        log.info(
            "worker pid %s serving daemon pid %s (services %s)",
            os.getpid(),
            daemon_pid,
            ", ".join(sorted(self.services)) or "none",
            extra={"worker_pid": os.getpid(), "daemon_pid": daemon_pid},
        )
        watch = asyncio.create_task(self._watch_daemon(daemon_pid), name="worker-daemon-watch")
        try:
            await self._stop.wait()
        finally:
            watch.cancel()
            await asyncio.gather(watch, return_exceptions=True)
            if self.peer is not None:
                reason = self.peer.close_reason or "worker stopping"
                await self.peer.close("worker stopping")
                log.info("worker pid %s leaving: %s", os.getpid(), reason)
            await self._stop_services()
        return 0

    async def _stop_services(self) -> None:
        for service in self.services.values():
            try:
                await service.stop()
            except Exception:
                log.exception("worker service %s failed to stop", service.name)

    async def _watch_daemon(self, daemon_pid: int | None) -> None:
        if daemon_pid is None:
            return
        while not self._stop.is_set():
            await asyncio.sleep(DAEMON_CHECK_SECONDS)
            if not await asyncio.to_thread(_pid_alive, daemon_pid):
                log.warning("worker's daemon pid %s is gone; exiting", daemon_pid)
                self._stop.set()

    async def _on_request(self, peer: FramedPeer, header: dict[str, Any], payload: bytes) -> Reply:
        kind = str(header.get("t") or "")
        service_name, _, method = kind.partition(".")
        if service_name == WORKER_ROLE:
            return self._builtin(method)
        service = self.services.get(service_name)
        if service is None:
            raise RequestRefused(f"unknown service {service_name!r}")
        return await service.handle(method, header, payload)

    def _builtin(self, method: str) -> Reply:
        if method == "stats":
            return {
                "pid": os.getpid(),
                "uptime_seconds": round(time.monotonic() - self.started_at, 3),
                "services": sorted(self.services),
                "build": __version__,
                "switch_interval_ms": round(sys.getswitchinterval() * 1000, 3),
            }
        if method == "shutdown":
            # After this reply is written: the loop runs the stop on its next turn.
            asyncio.get_running_loop().call_soon(self._stop.set)
            return {"stopping": True}
        raise RequestRefused(f"unknown method worker.{method}")


def _pid_alive(pid: int) -> bool:
    """Whether `pid` still names a running process (best effort, never raises)."""
    try:
        import psutil

        return bool(psutil.pid_exists(pid))
    except Exception:  # noqa: BLE001 - a liveness backstop must not end the worker
        return True


def read_token(fd: int = 0, *, limit: int = TOKEN_MAX_BYTES) -> str:
    """Read the token line the daemon writes to stdin; '' when there is none.

    Read from the descriptor rather than `sys.stdin`: a windowed frozen build may
    have no `sys.stdin` object at all, and Windows text stdio would decode it.
    """
    data = b""
    while b"\n" not in data and len(data) <= limit:
        try:
            chunk = os.read(fd, limit + 1 - len(data))
        except OSError:
            break
        if not chunk:
            break
        data += chunk
    line = data.split(b"\n", 1)[0]
    if len(line) > limit:
        return ""
    return line.decode("ascii", "replace").strip()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="swe-mux --worker-child")
    result.add_argument("--port", type=int, required=True)
    result.add_argument("--host", default="127.0.0.1")
    result.add_argument("--data-dir", type=Path, required=True)
    result.add_argument("--log-level", default="INFO")
    result.add_argument("--switch-interval-ms", type=float, default=5.0)
    result.add_argument("--daemon-pid", type=int, default=None)
    return result


def main(argv: Sequence[str] | None = None) -> None:
    from .gil_switch import apply_gil_switch_interval
    from .logsetup import enable_crash_tracebacks, setup_worker_logging
    from .timer_resolution import raise_timer_resolution

    args = parser().parse_args(argv)
    setup_worker_logging(args.data_dir, args.log_level)
    enable_crash_tracebacks(args.data_dir)
    code = 1
    try:
        raise_timer_resolution()
        apply_gil_switch_interval(args.switch_interval_ms)
        token = read_token()
        if token:
            log.info(
                "worker pid %s starting for daemon pid %s on port %s",
                os.getpid(),
                args.daemon_pid,
                args.port,
            )
            code = asyncio.run(
                Worker().run(args.host, args.port, token, daemon_pid=args.daemon_pid)
            )
        else:
            log.error("worker pid %s got no token on stdin; exiting", os.getpid())
            code = 2
    except Exception:
        log.exception("worker pid %s failed", os.getpid())
        code = 1
    finally:
        logging.shutdown()
    # An explicit exit code, because the daemon records it: 0 is a stop it asked
    # for or a daemon that went away, 2 is a handshake that never happened.
    sys.exit(code)


if __name__ == "__main__":
    main()


__all__ = [
    "SERVICES",
    "WORKER_PROTOCOL",
    "WORKER_ROLE",
    "NoopService",
    "Worker",
    "WorkerService",
    "main",
    "read_token",
]
