"""The daemon's side of the worker process: spawn it, own it, restart it, talk to it.

`development/archive/DAEMON_LOAD_ISOLATION.md` item 19; the child is `worker.py`.

**Spawned off the loop.** `CreateProcess` on a loaded Windows host - Defender
scanning a fresh image - can take seconds, so the spawn runs in a thread and the
event loop only ever awaits it. The token goes to the child's stdin from the
same thread, and the child is assigned to this host's reaper there too.

**Owned, not detached.** Unlike the PTY supervisor, which breaks away so it can
outlive the daemon, the worker must die with it: it is held in its own
kill-on-close reaper (a Job object on Windows; a process group with a guardian on
POSIX), which never contains a supervised session pid. A daemon that dies hard
takes its worker with it, and the next daemon starts a fresh one.

**Supervised as a background loop.** One task runs incarnation after
incarnation: spawn, wait for the child's `hello` (bounded), run the ready hooks
that push each service its state, then wait for the connection to close or the
child to exit, and restart with backoff from 0.5 s to 30 s that resets after a
minute of good service. Nothing a caller does can wait on a restart: `call`
raises `WorkerUnavailable` at once when no worker is connected, and the caller
falls back or reports.

**Reported**, as `worker` on `/api/diagnostics/background`: its state, pid,
spawn and restart counts, the last exit code and error, and the connection's own
counters.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import os
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

from .background_tasks import background
from .framed_ipc import (
    FramedPeer,
    FramedServer,
    HandshakeRefused,
    PeerClosed,
    RemoteError,
    new_token,
)
from .process_reaper import ProcessReaper, create_reaper
from .spawn_contract import scrub_claude_session_markers
from .subprocess_flags import background_creation_flags
from .worker import WORKER_PROTOCOL, WORKER_ROLE

log = logging.getLogger(__name__)

WORKER_HOST_LOOP = "worker-host"
WORKER_CONSOLE_LOG = "worker-console.log"
#: `worker-console.log` is truncated at a spawn once it is this large: it holds what
#: the interpreter printed before logging started, which only the latest run needs.
WORKER_CONSOLE_MAX_BYTES = 1024 * 1024
RESTART_INITIAL_SECONDS = 0.5
RESTART_MAX_SECONDS = 30.0
#: An incarnation that served this long resets the restart backoff.
STABLE_SECONDS = 60.0
#: From spawn to `hello`. Generous because a fresh frozen image on a loaded host
#: pays a Defender scan before it runs a line.
READY_TIMEOUT_SECONDS = 60.0
#: How long a worker whose connection closed gets to exit before it is killed.
EXIT_GRACE_SECONDS = 3.0
EXIT_POLL_SECONDS = 1.0
#: How long a ready hook may take before the incarnation is given up on.
READY_HOOK_TIMEOUT_SECONDS = 30.0
SHUTDOWN_REQUEST_SECONDS = 2.0
#: Environment the worker must not inherit: it needs no credential of the daemon's.
WITHHELD_ENVIRONMENT = ("SWE_MUX_DESKTOP_CONTROL_TOKEN",)

STATE_DISABLED = "disabled"
STATE_STOPPED = "stopped"
STATE_STARTING = "starting"
STATE_RUNNING = "running"
STATE_BACKOFF = "backoff"

ReadyHook = Callable[[FramedPeer], Awaitable[None] | None]
NoticeHandler = Callable[[str, dict[str, Any], bytes], None]


class WorkerUnavailable(Exception):
    """No worker is connected right now; the caller falls back or reports."""


def worker_command(
    *,
    port: int,
    data_dir: Path,
    log_level: str,
    switch_interval_ms: float,
    daemon_pid: int,
    executable: str | None = None,
    frozen: bool | None = None,
) -> list[str]:
    """How to start the worker: the frozen app's entry flag, or the module from source.

    Frozen, never `-m swe_mux.worker`: on the app's own image that is a session
    helper to the redeploy and the process inspector (`worker.py` explains).
    """
    executable = executable or sys.executable
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    entry = [executable, "--worker-child"] if frozen else [executable, "-m", "swe_mux.worker"]
    return [
        *entry,
        "--port",
        str(port),
        "--data-dir",
        str(data_dir),
        "--log-level",
        log_level,
        "--switch-interval-ms",
        f"{switch_interval_ms:g}",
        "--daemon-pid",
        str(daemon_pid),
    ]


CommandFactory = Callable[..., list[str]]


class WorkerHost:
    """One worker process at a time, for the life of the daemon."""

    def __init__(
        self,
        config: Any,
        *,
        command: CommandFactory = worker_command,
        reaper_factory: Callable[[], ProcessReaper] = create_reaper,
        ready_timeout: float = READY_TIMEOUT_SECONDS,
        restart_initial: float = RESTART_INITIAL_SECONDS,
        restart_max: float = RESTART_MAX_SECONDS,
        stable_seconds: float = STABLE_SECONDS,
        exit_grace: float = EXIT_GRACE_SECONDS,
        exit_poll: float = EXIT_POLL_SECONDS,
    ) -> None:
        self.enabled = bool(getattr(config, "worker_process_enabled", True))
        self.data_dir = Path(config.data_dir)
        self.log_level = str(getattr(config, "log_level", "INFO") or "INFO")
        self.switch_interval_ms = float(getattr(config, "gil_switch_interval_ms", 5.0) or 5.0)
        self._command = command
        self._reaper_factory = reaper_factory
        self._ready_timeout = ready_timeout
        self._restart_initial = restart_initial
        self._restart_max = restart_max
        self._stable_seconds = stable_seconds
        self._exit_grace = exit_grace
        self._exit_poll = exit_poll
        self.state = STATE_DISABLED if not self.enabled else STATE_STOPPED
        #: The worker's own pid once it has said hello; the spawned pid before.
        self.pid: int | None = None
        #: The spawned process when it is a launcher for the worker, else None.
        self.launcher_pid: int | None = None
        self.spawns = 0
        self.restarts = 0
        #: Counts connected workers. A service holding state in the worker compares
        #: it with the generation it last pushed that state to, so a worker that
        #: restarted between two calls is reloaded rather than trusted.
        self.generation = 0
        self.last_exit_code: int | None = None
        self.last_error = ""
        self.connected_at: float | None = None
        self.started = False
        self._token = ""
        self._server: FramedServer | None = None
        self._reaper: ProcessReaper | None = None
        self._process: subprocess.Popen[bytes] | None = None
        # Recorded in the spawn thread before the token is written, so a worker
        # quick enough to say hello before the loop resumes is still recognised.
        self._spawned_pid: int | None = None
        self._task: asyncio.Task[None] | None = None
        self._peer: FramedPeer | None = None
        self._connected: asyncio.Future[FramedPeer] | None = None
        self._ready = asyncio.Event()
        self._stopping = False
        self._ready_hooks: list[ReadyHook] = []
        self._notice_handlers: dict[str, NoticeHandler] = {}
        self.notices_unrouted = 0

    # -- registration ---------------------------------------------------------------

    def on_ready(self, hook: ReadyHook) -> None:
        """Run `hook(peer)` on every new worker before it is reported ready.

        This is how a service is rebuilt after a restart: the hook pushes the state
        the worker needs, and a hook that fails ends the incarnation, because a
        worker missing its state is worse than one restarting.
        """
        self._ready_hooks.append(hook)

    def on_notice(self, service: str, handler: NoticeHandler) -> None:
        """Route the worker's `<service>.<method>` notices to `handler(method, header, payload)`."""
        self._notice_handlers[service] = handler

    # -- use ---------------------------------------------------------------------------

    @property
    def ready(self) -> bool:
        peer = self._peer
        return self._ready.is_set() and peer is not None and not peer.closed

    async def wait_ready(self, timeout_seconds: float) -> bool:
        """Wait up to `timeout_seconds` for a worker; whether one is ready."""
        if not self.enabled:
            return False
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(timeout_seconds):
                await self._ready.wait()
        return self.ready

    async def call(
        self,
        service: str,
        method: str,
        fields: Mapping[str, Any] | None = None,
        payload: bytes = b"",
        *,
        timeout_seconds: float = 30.0,
    ) -> tuple[dict[str, Any], bytes]:
        """One request to a worker service. Raises `WorkerUnavailable` or `RemoteError`."""
        peer = self._peer
        if not self.ready or peer is None:
            raise WorkerUnavailable(f"no worker is connected (state {self.state})")
        try:
            return await peer.request(
                {**(fields or {}), "t": f"{service}.{method}"},
                payload,
                timeout_seconds=timeout_seconds,
            )
        except PeerClosed as exc:
            raise WorkerUnavailable(f"the worker went away ({exc.reason})") from exc

    def notify(
        self,
        service: str,
        method: str,
        fields: Mapping[str, Any] | None = None,
        payload: bytes = b"",
    ) -> bool:
        """Send a worker service a notice; whether there was a worker to send it to."""
        peer = self._peer
        if not self.ready or peer is None:
            return False
        try:
            peer.notify({**(fields or {}), "t": f"{service}.{method}"}, payload)
        except PeerClosed:
            return False
        return True

    # -- lifecycle -----------------------------------------------------------------------

    async def start(self) -> None:
        """Listen for the worker and start supervising it. Returns at once."""
        if not self.enabled:
            log.info("worker process disabled (worker_process_enabled = false)")
            return
        if self.started:
            return
        self.started = True
        self._stopping = False
        self._token = new_token()
        self._reaper = self._reaper_factory()
        self._server = FramedServer(
            token=self._token,
            protocol=WORKER_PROTOCOL,
            name="worker-host",
            on_connection=self._on_connection,
            on_notice=self._dispatch_notice,
            hello_reply=self._check_hello,
        )
        await self._server.start()
        self._task = background.start(WORKER_HOST_LOOP, self._supervise)

    async def stop(self) -> None:
        """Ask the worker to leave, end it if it does not, and stop listening."""
        if not self.started:
            return
        self._stopping = True
        peer = self._peer
        task = self._task
        if self.ready and peer is not None:
            with contextlib.suppress(PeerClosed, RemoteError, TimeoutError):
                await peer.request(
                    {"t": f"{WORKER_ROLE}.shutdown"}, timeout_seconds=SHUTDOWN_REQUEST_SECONDS
                )
            # The incarnation now ends by itself - the connection closes, the child
            # exits inside its grace - so it is waited for rather than cancelled
            # part-way through its own ending.
            if task is not None:
                with contextlib.suppress(TimeoutError):
                    async with asyncio.timeout(self._exit_grace + SHUTDOWN_REQUEST_SECONDS):
                        await asyncio.shield(task)
        # Whatever is left - a worker still starting, one in backoff, one that did
        # not leave - is cancelled, which runs the incarnation's ending: the child
        # gets its grace to exit and is killed if it stays.
        await background.stop(WORKER_HOST_LOOP)
        self._task = None
        reaper, self._reaper = self._reaper, None
        if reaper is not None:
            await asyncio.to_thread(reaper.close)
        server, self._server = self._server, None
        if server is not None:
            await server.close()
        self.started = False
        self.state = STATE_STOPPED
        log.info(
            "worker host stopped after %s spawn(s) and %s restart(s)",
            self.spawns,
            self.restarts,
            extra={"worker_spawns": self.spawns, "worker_restarts": self.restarts},
        )

    async def _supervise(self) -> None:
        delay = self._restart_initial
        while not self._stopping:
            began = time.monotonic()
            try:
                await self._run_incarnation()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the loop's whole job is to outlive this
                self.last_error = f"{type(exc).__name__}: {exc}"[:500]
                log.warning(
                    "worker incarnation failed: %s",
                    self.last_error,
                    extra={"worker_pid": self.pid, "worker_exit_code": self.last_exit_code},
                )
            if self._stopping:
                return
            if time.monotonic() - began >= self._stable_seconds:
                delay = self._restart_initial
            self.restarts += 1
            self.state = STATE_BACKOFF
            log.warning(
                "worker restarting in %.1fs (restart %s, last exit code %s)",
                delay,
                self.restarts,
                self.last_exit_code,
                extra={"worker_restarts": self.restarts, "worker_exit_code": self.last_exit_code},
            )
            await asyncio.sleep(delay)
            delay = min(self._restart_max, delay * 2)

    async def _run_incarnation(self) -> None:
        self.state = STATE_STARTING
        loop = asyncio.get_running_loop()
        connected: asyncio.Future[FramedPeer] = loop.create_future()
        self._connected = connected
        process = await asyncio.to_thread(self._spawn)
        self._process = process
        self.pid = process.pid
        self.launcher_pid = None
        self.spawns += 1
        log.info(
            "worker spawned: pid %s (spawn %s)",
            process.pid,
            self.spawns,
            extra={"worker_pid": process.pid, "worker_spawns": self.spawns},
        )
        exited = asyncio.create_task(self._watch_exit(process), name="worker-exit-watch")
        peer: FramedPeer | None = None
        try:
            first: set[asyncio.Future[Any]] = {connected, exited}
            done, _ = await asyncio.wait(
                first, timeout=self._ready_timeout, return_when=asyncio.FIRST_COMPLETED
            )
            if connected not in done:
                if exited in done:
                    raise RuntimeError(
                        f"the worker exited with code {exited.result()} before its hello"
                    )
                raise TimeoutError(f"the worker sent no hello within {self._ready_timeout:g}s")
            peer = connected.result()
            for hook in tuple(self._ready_hooks):
                result = hook(peer)
                if inspect.isawaitable(result):
                    async with asyncio.timeout(READY_HOOK_TIMEOUT_SECONDS):
                        await result
            self.connected_at = time.monotonic()
            self.state = STATE_RUNNING
            self.last_error = ""
            self._ready.set()
            log.info(
                "worker pid %s ready (spawned pid %s)",
                self.pid,
                process.pid,
                extra={"worker_pid": self.pid},
            )
            closed = asyncio.create_task(peer.wait_closed(), name="worker-connection-watch")
            try:
                await asyncio.wait({closed, exited}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                closed.cancel()
            if peer.closed and not self._stopping:
                self.last_error = f"connection closed: {peer.close_reason}"
        finally:
            self._ready.clear()
            self._connected = None
            await self._end_incarnation(process, exited, peer)

    async def _end_incarnation(
        self,
        process: subprocess.Popen[bytes],
        exited: asyncio.Task[int],
        peer: FramedPeer | None,
    ) -> None:
        """Close the connection, give the child its grace, and kill it if it stays."""
        if self._peer is peer:
            self._peer = None
        if peer is not None and not peer.closed:
            await peer.close("worker incarnation ending")
        if not exited.done():
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(self._exit_grace):
                    await asyncio.shield(exited)
        if not exited.done():
            log.warning(
                "worker pid %s did not exit within %.1fs; killing it",
                process.pid,
                self._exit_grace,
                extra={"worker_pid": process.pid},
            )
            await asyncio.to_thread(_kill, process)
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(10.0):
                    await asyncio.shield(exited)
        if not exited.done():
            exited.cancel()
            await asyncio.gather(exited, return_exceptions=True)
        self.last_exit_code = process.returncode
        self._process = None
        self._spawned_pid = None
        self.connected_at = None
        log.info(
            "worker pid %s ended with exit code %s",
            process.pid,
            process.returncode,
            extra={"worker_pid": process.pid, "worker_exit_code": process.returncode},
        )

    async def _watch_exit(self, process: subprocess.Popen[bytes]) -> int:
        # A poll rather than a thread parked in `wait()` for the worker's whole
        # life: one zero-timeout handle check a second, against an executor thread
        # held forever. asyncio offers no awaitable exit for a child it did not
        # spawn, and spawning on the loop is what this module exists to avoid.
        while (code := process.poll()) is None:  # noqa: ASYNC110 - see above
            await asyncio.sleep(self._exit_poll)
        return code

    def _spawn(self) -> subprocess.Popen[bytes]:
        """Start one worker, hand it the token, and take ownership. Runs in a thread."""
        assert self._server is not None
        command = self._command(
            port=self._server.port,
            data_dir=self.data_dir,
            log_level=self.log_level,
            switch_interval_ms=self.switch_interval_ms,
            daemon_pid=os.getpid(),
        )
        environment = scrub_claude_session_markers(os.environ)
        for name in WITHHELD_ENVIRONMENT:
            environment.pop(name, None)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        console_path = self.data_dir / WORKER_CONSOLE_LOG
        mode = "ab"
        with contextlib.suppress(OSError):
            if console_path.stat().st_size > WORKER_CONSOLE_MAX_BYTES:
                mode = "wb"
        options: dict[str, Any] = {}
        if os.name == "nt":
            options["creationflags"] = background_creation_flags() | int(
                getattr(subprocess, "NORMAL_PRIORITY_CLASS", 0)
            )
        else:
            # Its own group, so the reaper can own it without owning the daemon.
            options["start_new_session"] = True
        with console_path.open(mode) as console:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=console,
                cwd=str(self.data_dir),
                env=environment,
                **options,
            )
        assert process.stdin is not None
        self._spawned_pid = process.pid
        try:
            process.stdin.write(self._token.encode("ascii") + b"\n")
            process.stdin.close()
        except OSError as exc:
            # A child that died already; its exit is what the incarnation reports.
            log.warning("could not hand the worker its token: %s", exc)
        reaper = self._reaper
        if reaper is not None:
            try:
                reaper.assign(process.pid)
            except OSError as exc:
                # Weaker cleanup, not no worker: it still exits on its connection's
                # EOF and on its daemon's pid disappearing.
                log.warning(
                    "worker pid %s could not be put under the daemon's reaper: %s",
                    process.pid,
                    exc,
                    extra={"worker_pid": process.pid},
                )
        return process

    # -- the connection -----------------------------------------------------------------------

    def _check_hello(self, hello: dict[str, Any]) -> dict[str, Any]:
        if hello.get("role") != WORKER_ROLE:
            raise HandshakeRefused(f"not a worker: role {hello.get('role')!r}")
        claimed = hello.get("pid")
        spawned = self._spawned_pid
        if (
            spawned is None
            or not isinstance(claimed, int)
            or isinstance(claimed, bool)
            or not _spawned_by(claimed, spawned)
        ):
            raise HandshakeRefused(f"not the worker this daemon started (pid {claimed!r})")
        connected = self._connected
        if connected is None or connected.done():
            raise HandshakeRefused("a worker is already connected")
        return {"daemon_pid": os.getpid()}

    def _on_connection(self, peer: FramedPeer) -> None:
        connected = self._connected
        if connected is None or connected.done():
            # The incarnation gave up on this one between its hello and here.
            raise RuntimeError("no incarnation is waiting for a worker")
        self._peer = peer
        self.generation += 1
        # The worker's own pid, which is the spawned one's child under a Windows
        # virtual environment's launcher; the launcher is kept for the record.
        claimed = peer.remote_hello.get("pid")
        if isinstance(claimed, int) and claimed != self._spawned_pid:
            self.launcher_pid = self._spawned_pid
            self.pid = claimed
        connected.set_result(peer)

    def _dispatch_notice(self, peer: FramedPeer, header: dict[str, Any], payload: bytes) -> None:
        service, _, method = str(header.get("t") or "").partition(".")
        handler = self._notice_handlers.get(service)
        if handler is None:
            self.notices_unrouted += 1
            return
        handler(method, header, payload)

    # -- reporting ------------------------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        peer = self._peer
        hello = peer.remote_hello if peer is not None else {}
        connected_at = self.connected_at
        return {
            "enabled": self.enabled,
            "state": self.state,
            "ready": self.ready,
            "pid": self.pid,
            "launcher_pid": self.launcher_pid,
            "port": self._server.port if self._server is not None else None,
            "spawns": self.spawns,
            "restarts": self.restarts,
            "generation": self.generation,
            "last_exit_code": self.last_exit_code,
            "last_error": self.last_error,
            "uptime_seconds": (
                round(time.monotonic() - connected_at, 3) if connected_at is not None else None
            ),
            "services": list(hello.get("services") or []),
            "build": hello.get("build"),
            "notices_unrouted": self.notices_unrouted,
            "connection": peer.stats() if peer is not None else None,
        }


def _spawned_by(claimed: int, spawned: int) -> bool:
    """Whether `claimed` is the process this host spawned, or that process's child.

    The child case is a Windows virtual environment: its `python.exe` is a
    launcher that starts the real interpreter as a child and waits on it, so the
    worker saying hello is one level below the pid `Popen` returned. One level
    only, and only that exact parent, so a token alone never admits a stranger.
    """
    if claimed == spawned:
        return True
    try:
        import psutil

        return bool(psutil.Process(claimed).ppid() == spawned)
    except Exception:  # noqa: BLE001 - an unreadable process is not one we started
        return False


def _kill(process: subprocess.Popen[bytes]) -> None:
    with contextlib.suppress(OSError):
        process.kill()


__all__ = [
    "WORKER_HOST_LOOP",
    "WorkerHost",
    "WorkerUnavailable",
    "worker_command",
]
