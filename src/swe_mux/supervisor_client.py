"""Daemon-side client for the out-of-process PTY supervisor.

``SupervisorClient`` owns the loopback connection (discovery, handshake,
request/response correlation, output dispatch). ``RemotePtyHost`` presents the
same synchronous surface as the in-process ``PtyHost`` so ``Session`` and the
attach/fanout paths do not care where the ConPTY lives. Blocking methods
(``spawn``/``stop``) are only ever invoked via ``asyncio.to_thread`` by the
session manager and bridge back onto the loop with
``run_coroutine_threadsafe``; fire-and-forget methods (``write``/``resize``)
are called on the loop and enqueue a frame directly.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .subprocess_flags import background_creation_flags, popen_outside_job
from .supervisor import (
    PROTOCOL_VERSION,
    discovery_path,
    encode_frame,
    read_frame,
)

if TYPE_CHECKING:
    from .config import Config

log = logging.getLogger(__name__)

CONNECT_TIMEOUT_SECONDS = 3.0
SPAWN_DEADLINE_SECONDS = 20.0
RPC_TIMEOUT_SECONDS = 60.0
META_FLUSH_DELAY_SECONDS = 0.5
SPAWN_STATUS_TIMEOUT_SECONDS = 10.0
SPAWN_STATUS_POLL_SECONDS = 0.5
SPAWN_STATUS_DEADLINE_SECONDS = 30.0
# Smallest per-session staging budget for output the daemon has not consumed yet
# (see ``RemotePtyHost._offer``). A session configured with a tiny scrollback
# still gets a usable buffer; anything past the budget is output the daemon-side
# scrollback ring would have evicted before anyone could read it.
MIN_OUTPUT_OVERFLOW_BYTES = 1 * 1024 * 1024
OUTPUT_OVERFLOW_LOG_INTERVAL_SECONDS = 5.0
# Console-output redirect (crash catcher) for a supervisor spawned by this
# daemon. Distinct from supervisor.log, which the supervisor's own rotating
# handler owns: the child inherits this redirect handle for its lifetime, so
# rotation of the same file would never succeed.
SUPERVISOR_LOG_NAME = "supervisor-console.log"


class SupervisorUnavailable(RuntimeError):
    """No healthy supervisor could be reached (and, if requested, spawned)."""


SUPERVISOR_EXE_ENV = "SWE_MUX_SUPERVISOR_EXE"
SUPERVISOR_BUNDLE_DIR = "swe-mux-supervisor"
SUPERVISOR_BUNDLE_EXE = "swe-mux-supervisor.exe"


class Liveness(StrEnum):
    """What is known about one pseudoconsole's child process.

    ``UNREACHABLE`` is the state that used not to exist. A supervisor-owned
    console whose socket dropped is neither ``ALIVE`` (nothing can be written to
    it or read from it) nor ``DEAD`` (no exit happened, and persisting one
    fabricates history for an agent that is still mutating a workspace). Every
    caller that used to read a bool now has somewhere to put that third answer;
    ``isalive()`` keeps the bool contract by folding it into "not dead", which is
    what it factually is.
    """

    ALIVE = "alive"
    DEAD = "dead"
    UNREACHABLE = "unreachable"


def liveness_of(pty: Any) -> Liveness:
    """Tri-state liveness for any ``PtyHost``-shaped object.

    An in-process ``PtyHost`` dies with the daemon and is observed through a
    handle in this process, so it only ever has two states; only a
    supervisor-owned console can be running-but-unreachable.
    """
    probe = getattr(pty, "liveness", None)
    if callable(probe):
        state = probe()
        return state if isinstance(state, Liveness) else Liveness(str(state))
    return Liveness.ALIVE if pty.isalive() else Liveness.DEAD


# States the supervisor itself reports for a spawn, plus the two answers that
# describe the *query* rather than the spawn. Both of those are "no evidence",
# and they are kept apart because only one of them is a steady state: an older
# supervisor that never learned the message is expected and permanent, while a
# connection that could not carry the question may be answerable later.
SPAWN_STATE_UNKNOWN = "unknown"
SPAWN_STATE_RESERVED = "reserved"
SPAWN_STATE_LIVE = "live"
SPAWN_STATE_EXITED = "exited"
SPAWN_STATE_UNSUPPORTED = "unsupported"
SPAWN_STATE_INDETERMINATE = "indeterminate"
_SUPERVISOR_SPAWN_STATES = frozenset(
    {SPAWN_STATE_UNKNOWN, SPAWN_STATE_RESERVED, SPAWN_STATE_LIVE, SPAWN_STATE_EXITED}
)


@dataclass(frozen=True)
class SpawnStatus:
    """What the supervisor says became of a spawn whose reply the daemon lost."""

    state: str
    pid: int | None = None
    started_at: float | None = None
    detail: str = ""

    @property
    def is_evidence(self) -> bool:
        """True when the supervisor answered about the session, not about itself."""
        return self.state in _SUPERVISOR_SPAWN_STATES

    @property
    def reserved_or_live(self) -> bool:
        """True when the supervisor holds this id; an in-process fallback would duplicate it."""
        return self.state in {SPAWN_STATE_RESERVED, SPAWN_STATE_LIVE}


def dedicated_supervisor_exe(
    *,
    executable: str | None = None,
    frozen: bool | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path | None:
    """Locate the standalone supervisor bundle, when one applies.

    The dedicated bundle exists so rebuilding ``dist/swe-mux`` never collides
    with a running supervisor's file image. Resolution order:

    - ``SWE_MUX_SUPERVISOR_EXE`` env override (any mode).
    - Frozen: the ``swe-mux-supervisor`` sibling of the app's dist folder.
    - Source: none — source daemons launch the supervisor from source so the
      code being iterated on is the code that runs, never a stale frozen copy.
    """
    env = os.environ if environ is None else environ
    override = env.get(SUPERVISOR_EXE_ENV)
    if override:
        path = Path(override)
        return path if path.is_file() else None
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if not frozen:
        return None
    executable = executable or sys.executable
    candidate = (
        Path(executable).resolve().parent.parent / SUPERVISOR_BUNDLE_DIR / SUPERVISOR_BUNDLE_EXE
    )
    return candidate if candidate.is_file() else None


def supervisor_command(
    config_path: Path,
    *,
    executable: str | None = None,
    frozen: bool | None = None,
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    """Resolve how to launch the supervisor: dedicated bundle, frozen child, or source."""
    dedicated = dedicated_supervisor_exe(executable=executable, frozen=frozen, environ=environ)
    config_args = ["--config", str(config_path)]
    if dedicated is not None:
        return [str(dedicated), *config_args]
    executable = executable or sys.executable
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if frozen:
        # Fallback for a dist tree without the dedicated bundle: the supervisor
        # then shares the app image, so app rebuilds require reaping first.
        return [executable, "--supervisor-child", *config_args]
    return [executable, "-m", "swe_mux.supervisor", *config_args]


class RemotePtyHost:
    """PtyHost-shaped facade over one supervisor-owned ConPTY."""

    def __init__(
        self,
        client: SupervisorClient,
        sid: str,
        *,
        appname: str = "",
        argv: tuple[str, ...] = (),
        cwd: str | None = None,
        cols: int = 120,
        rows: int = 30,
        env: dict[str, str] | None = None,
        graceful_exit: str = "exit\r",
        max_scrollback: int = 5 * 1024 * 1024,
        meta: dict[str, Any] | None = None,
        initial_output: bytes = b"",
    ) -> None:
        self.client = client
        self.sid = sid
        self.appname = appname
        self.argv = argv
        self.cwd = cwd
        self.cols = cols
        self.rows = rows
        self.env = env or {}
        self.max_scrollback = max_scrollback
        self.meta = meta or {}
        self.initial_output = initial_output
        self.pid = -1
        self.reaper_assignment = "supervisor_pending"
        self._graceful_exit = graceful_exit
        self._alive = False
        self._exit_code: int | None = None
        self._first_output_at: float | None = None
        self._queue: asyncio.Queue[bytes] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._released = False
        # Staging for output this session's consumer has not taken yet. The read
        # loop must never wait on one session (see ``_offer``), so overflow past
        # the bounded queue lands here and a per-session pump drains it.
        self._pending: deque[bytes] = deque()
        self._pending_bytes = 0
        self._pump_task: asyncio.Task[None] | None = None
        self._draining = False
        self.output_dropped_bytes = 0
        self._dropped_logged_at = 0.0

    # -- PtyHost surface -------------------------------------------------------

    @property
    def graceful_exit(self) -> str:
        return self._graceful_exit

    @graceful_exit.setter
    def graceful_exit(self, keys: str) -> None:
        if keys == self._graceful_exit:
            return
        self._graceful_exit = keys
        self.client.notify({"t": "set_graceful_exit", "sid": self.sid, "keys": keys})

    @property
    def output_queue(self) -> asyncio.Queue[bytes]:
        if self._queue is None:
            raise RuntimeError("PTY has not been spawned")
        return self._queue

    @property
    def first_output_at(self) -> float | None:
        return self._first_output_at

    def prepare(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue(maxsize=1024)
        self._first_output_at = None
        self._pending.clear()
        self._pending_bytes = 0
        self.client.register_host(self)

    def spawn(self) -> None:
        """Blocking spawn RPC; only ever called via ``asyncio.to_thread``."""
        if self._loop is None or self._queue is None:
            raise RuntimeError("prepare() must run on the event loop before spawn()")
        future = asyncio.run_coroutine_threadsafe(self.client.spawn_session(self), self._loop)
        future.result(timeout=SPAWN_DEADLINE_SECONDS + RPC_TIMEOUT_SECONDS)

    def write(self, data: str | bytes) -> None:
        payload = data.encode("utf-8") if isinstance(data, str) else bytes(data)
        self.client.notify({"t": "write", "sid": self.sid}, payload)

    def resize(self, cols: int, rows: int) -> None:
        cols, rows = max(2, cols), max(1, rows)
        self.cols, self.rows = cols, rows
        self.client.notify({"t": "resize", "sid": self.sid, "cols": cols, "rows": rows})

    def liveness(self) -> Liveness:
        """Alive / dead / unreachable, as three separate facts (audit F1).

        ``_alive`` is cleared only by a definitive ``pty_exit`` or by a supervisor
        death this client confirmed, so a False here really is an ended process.
        A dropped socket leaves ``_alive`` True and reports ``UNREACHABLE``: the
        child is running, this daemon simply cannot see or drive it.
        """
        if not self._alive:
            return Liveness.DEAD
        if self.client.connected:
            return Liveness.ALIVE
        return Liveness.UNREACHABLE

    def isalive(self) -> bool:
        """True while the child process is believed to be running.

        Unreachable folds into True on purpose. Returning False merely because
        ``client.connected`` went False is what let a transient socket fault end
        every live session durably, resurrect them at the next daemon start, and
        leave agents running invisibly in between.
        """
        return self.liveness() is not Liveness.DEAD

    def exit_status(self) -> int | None:
        if self._alive:
            return None
        return self._exit_code

    def release(self) -> None:
        """Drop the ended supervisor entry; daemon-side scrollback is retained."""
        if self.liveness() is Liveness.ALIVE:
            raise RuntimeError("cannot release a live pseudoconsole")
        if self._released:
            return
        self._released = True
        self.client.unregister_host(self)
        self.client.notify({"t": "remove", "sid": self.sid})

    def stop(self, *, graceful: bool = True, timeout: float = 2.0) -> None:
        """Blocking stop RPC; only ever called via ``asyncio.to_thread``."""
        if self._loop is None or not self.client.connected:
            if self.liveness() is Liveness.UNREACHABLE:
                # A deliberate stop is still honoured locally - the operator asked
                # for it - but the child is not actually being killed, so say so
                # loudly rather than let the record read as a clean exit.
                log.error(
                    "session %s: stop requested while the supervisor is unreachable; "
                    "the child may still be running under supervisor pid %d "
                    "(restart the daemon to reattach and stop it for real)",
                    self.sid,
                    self.client.supervisor_pid,
                )
            self._mark_dead(None)
            return
        future = asyncio.run_coroutine_threadsafe(
            self.client.stop_session(self.sid, graceful=graceful, stop_timeout=timeout),
            self._loop,
        )
        try:
            future.result(timeout=timeout + RPC_TIMEOUT_SECONDS)
        except Exception:
            log.warning("supervisor stop for %s failed", self.sid, exc_info=True)
        self._alive = False

    # -- client-side bookkeeping ------------------------------------------------

    def _mark_dead(self, exit_code: int | None) -> None:
        was_alive = self._alive
        self._alive = False
        if exit_code is not None:
            self._exit_code = exit_code
        if was_alive and self._queue is not None:
            # The b"" sentinel is the same end-of-output contract the local read
            # thread uses; Session._fanout converts it into _mark_ended. It goes
            # through the same staging path as output so it stays ordered behind
            # the bytes the child produced before exiting, and so a full queue can
            # never drop it: `_offer` keeps the newest chunk unconditionally.
            self._offer(b"")

    def _receive_output(self, _chunk: bytes) -> None:
        if self._first_output_at is None:
            self._first_output_at = time.perf_counter()

    # -- output staging -----------------------------------------------------------

    def _offer(self, chunk: bytes) -> None:
        """Hand one chunk to this session without ever blocking the read loop.

        The bounded queue still carries backpressure; what changed is who waits
        on it. The read loop used to ``await`` the put, so a single session whose
        consumer had not started yet (or had stalled) stopped RPC replies,
        ``pty_exit`` delivery, and output for *every* other session on the
        connection - which is how a spawn RPC gets pushed past its 60s timeout
        (audit F3). Overflow lands in a per-session staging deque that a
        per-session pump drains, bounded by the byte budget below.
        """
        queue = self._queue
        if queue is None:
            return
        if not self._draining and not self._pending:
            try:
                queue.put_nowait(chunk)
                return
            except asyncio.QueueFull:
                pass
        self._pending.append(chunk)
        self._pending_bytes += len(chunk)
        self._trim_pending()
        if self._pump_task is None or self._pump_task.done():
            self._draining = True
            loop = self._loop or self.client.loop
            self._pump_task = loop.create_task(self._pump(), name=f"remote-pty-pump-{self.sid}")

    @property
    def _overflow_budget(self) -> int:
        return max(int(self.max_scrollback), MIN_OUTPUT_OVERFLOW_BYTES)

    def _trim_pending(self) -> None:
        """Bound the staging deque, dropping oldest-first and saying so.

        Unbounded staging would trade a stalled session for daemon memory growth.
        The ring drops from the old end, exactly like the scrollback that would
        have evicted these bytes anyway, and never drops the newest entry - which
        is what guarantees the exit sentinel survives.
        """
        budget = self._overflow_budget
        if self._pending_bytes <= budget:
            return
        dropped = 0
        while self._pending_bytes > budget and len(self._pending) > 1:
            oldest = self._pending.popleft()
            self._pending_bytes -= len(oldest)
            dropped += len(oldest)
        if not dropped:
            return
        self.output_dropped_bytes += dropped
        now = time.monotonic()
        if now - self._dropped_logged_at < OUTPUT_OVERFLOW_LOG_INTERVAL_SECONDS:
            return
        self._dropped_logged_at = now
        log.error(
            "session %s: dropped %d byte(s) of supervisor output (%d total) - the "
            "daemon-side consumer is not draining its queue; %d byte(s) staged, "
            "budget %d",
            self.sid,
            dropped,
            self.output_dropped_bytes,
            self._pending_bytes,
            budget,
        )

    async def _pump(self) -> None:
        """Drain staged output into the bounded queue, waiting only this session."""
        # unsupervised-loop-ok: scoped to one session's output, ends when the
        # staging deque empties, and cancelled with the host.
        queue = self._queue
        try:
            if queue is None:
                return
            while self._pending:
                # Popped *before* the await so an in-flight chunk cannot be
                # trimmed underneath the put and counted as dropped twice.
                chunk = self._pending.popleft()
                self._pending_bytes -= len(chunk)
                await queue.put(chunk)
        finally:
            self._draining = False

    def _cancel_pump(self) -> None:
        task, self._pump_task = self._pump_task, None
        self._draining = False
        if task is not None and not task.done():
            task.cancel()


class SupervisorClient:
    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        info: dict[str, Any],
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._loop = asyncio.get_running_loop()
        self.supervisor_pid = int(info.get("pid", -1))
        self.initial_sessions: list[dict[str, Any]] = list(info.get("sessions") or [])
        self.hosts: dict[str, RemotePtyHost] = {}
        self.connected = True
        # True when the connection dropped while the supervisor process is still
        # alive: sessions are running but unreachable, which is a different (and
        # recoverable) condition from "no supervisor".
        self.lost = False
        # Why and when the connection went away, for the health surface and for a
        # post-mortem that has only daemon.log to work from.
        self.lost_reason: str = ""
        self.lost_at: float | None = None
        # Frame desync is its own fault class: the bytes on the wire stopped
        # making sense, which is a protocol bug, not a network event. Counted and
        # named separately so it can never hide inside "connection lost".
        self.desync_count = 0
        self.desync_reason: str = ""
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[tuple[dict[str, Any], bytes]]] = {}
        self._meta_pending: dict[str, dict[str, Any]] = {}
        self._meta_sent: dict[str, str] = {}
        self._meta_task: asyncio.Task[None] | None = None
        self._read_task = asyncio.create_task(self._read_loop(), name="supervisor-read")

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        """The loop this client was built on; per-session pumps run there too."""
        return self._loop

    def supervisor_process_alive(self) -> bool:
        """Whether the supervisor *process* is running, independent of the socket.

        The one fact that separates "unreachable" from "gone", and the reason a
        dropped connection is not evidence of a session ending.
        """
        return _pid_running(self.supervisor_pid)

    # -- connection -------------------------------------------------------------

    @classmethod
    async def connect(cls, data_dir: Path) -> SupervisorClient:
        path = discovery_path(data_dir)
        try:
            info = json.loads(path.read_text(encoding="utf-8"))
            port = int(info["port"])
            token = str(info["token"])
        except (OSError, ValueError, KeyError) as exc:
            raise SupervisorUnavailable(f"no supervisor discovery file: {exc}") from exc
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", port), timeout=CONNECT_TIMEOUT_SECONDS
            )
        except (OSError, TimeoutError) as exc:
            raise SupervisorUnavailable(f"supervisor not reachable on port {port}") from exc
        request_id = 0
        writer.write(
            encode_frame(
                {"t": "hello", "id": request_id, "token": token, "protocol": PROTOCOL_VERSION}
            )
        )
        try:
            await writer.drain()
            header, _ = await asyncio.wait_for(read_frame(reader), timeout=CONNECT_TIMEOUT_SECONDS)
        except (OSError, TimeoutError, ValueError, asyncio.IncompleteReadError) as exc:
            writer.close()
            raise SupervisorUnavailable(f"supervisor handshake failed: {exc}") from exc
        if header.get("id") != request_id or not header.get("ok"):
            writer.close()
            raise SupervisorUnavailable(
                f"supervisor rejected handshake: {header.get('error', 'unknown error')}"
            )
        return cls(reader, writer, header)

    @classmethod
    async def connect_or_spawn(cls, config: Config) -> SupervisorClient:
        try:
            return await cls.connect(config.data_dir)
        except SupervisorUnavailable:
            pass
        assert config.config_path is not None
        command = supervisor_command(config.config_path)
        log_path = config.data_dir / SUPERVISOR_LOG_NAME
        config.data_dir.mkdir(parents=True, exist_ok=True)
        creationflags = background_creation_flags() | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
        with log_path.open("ab", buffering=0) as log_file:
            # Popen returns immediately (the supervisor is a long-lived sibling,
            # never waited on here), so it does not block the loop. cwd is the
            # data dir (the supervisor also chdirs itself): inheriting a cwd
            # inside dist/ would lock the app tree against rebuilds for as long
            # as the supervisor lives. Breakaway spawn: a daemon relaunched from
            # inside a session sits in that session's kill-on-close Job, and the
            # supervisor must never inherit it — it outlives every session.
            process = popen_outside_job(  # noqa: ASYNC220
                command,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                cwd=str(config.data_dir),
                creationflags=creationflags,
            )
        deadline = time.monotonic() + SPAWN_DEADLINE_SECONDS
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                return await cls.connect(config.data_dir)
            except SupervisorUnavailable as exc:
                last_error = exc
            if process.poll() is not None:
                # A concurrent supervisor may own the single-instance mutex, in
                # which case our child exits 0 and the discovery file is theirs.
                try:
                    return await cls.connect(config.data_dir)
                except SupervisorUnavailable as exc:
                    last_error = exc
                    if process.poll() != 0:
                        break
            await asyncio.sleep(0.25)
        raise SupervisorUnavailable(
            f"could not start the PTY supervisor (see {log_path}): {last_error}"
        )

    async def close(self) -> None:
        await self.flush_meta()
        self.connected = False
        if self._read_task and not self._read_task.done():
            self._read_task.cancel()
            await asyncio.gather(self._read_task, return_exceptions=True)
        pumps = [host._pump_task for host in tuple(self.hosts.values())]
        for host in tuple(self.hosts.values()):
            host._cancel_pump()
        pending = [task for task in pumps if task is not None and not task.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        try:
            self._writer.close()
        except Exception:
            pass

    # -- framing / RPC ------------------------------------------------------------

    def notify(self, header: dict[str, Any], payload: bytes = b"") -> None:
        if not self.connected:
            return
        frame = encode_frame(header, payload)
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop:
            self._write_frame(frame)
        else:
            self._loop.call_soon_threadsafe(self._write_frame, frame)

    def _write_frame(self, frame: bytes) -> None:
        if not self.connected:
            return
        try:
            self._writer.write(frame)
        except (ConnectionError, RuntimeError):
            self._on_connection_lost("write failed")

    async def request(
        self,
        header: dict[str, Any],
        payload: bytes = b"",
        timeout_seconds: float = RPC_TIMEOUT_SECONDS,
    ) -> tuple[dict[str, Any], bytes]:
        if not self.connected:
            raise SupervisorUnavailable("supervisor connection is closed")
        request_id = self._next_id
        self._next_id += 1
        future: asyncio.Future[tuple[dict[str, Any], bytes]] = self._loop.create_future()
        self._pending[request_id] = future
        try:
            self._write_frame(encode_frame({**header, "id": request_id}, payload))
            await self._writer.drain()
            response, response_payload = await asyncio.wait_for(future, timeout=timeout_seconds)
        finally:
            self._pending.pop(request_id, None)
        if not response.get("ok"):
            raise RuntimeError(str(response.get("error") or "supervisor request failed"))
        return response, response_payload

    async def _read_loop(self) -> None:
        try:
            while True:
                header, payload = await read_frame(self._reader)
                self._dispatch(header, payload)
        except asyncio.CancelledError:
            raise
        except ValueError as exc:
            # A frame whose bytes do not parse. Distinct from a transport fault
            # both in cause (a protocol bug or a corrupted stream) and in what it
            # tells an operator, and it used to vanish into "connection lost".
            self._on_frame_desync(exc)
        except (asyncio.IncompleteReadError, ConnectionError, OSError) as exc:
            self._on_connection_lost(f"connection lost ({type(exc).__name__}: {exc})")

    def _dispatch(self, header: dict[str, Any], payload: bytes) -> None:
        """Route one frame. Must never await: this is the whole control plane.

        RPC replies and ``pty_exit`` for every session share this loop with bulk
        output, so anything that waits here waits for all of them (audit F3).
        Output is handed to the session's own staging buffer instead.
        """
        kind = header.get("t")
        request_id = header.get("id")
        if request_id is not None:
            future = self._pending.get(int(request_id))
            if future is not None and not future.done():
                future.set_result((header, payload))
            return
        if kind == "output":
            host = self.hosts.get(str(header.get("sid")))
            if host is not None and host._queue is not None:
                host._receive_output(payload)
                host._offer(payload)
            return
        if kind == "pty_exit":
            host = self.hosts.get(str(header.get("sid")))
            if host is not None:
                exit_code = header.get("exit_code")
                host._mark_dead(int(exit_code) if exit_code is not None else None)

    def _on_frame_desync(self, error: Exception) -> None:
        """Log a malformed frame as itself, then drop the unusable connection."""
        self.desync_count += 1
        self.desync_reason = f"{type(error).__name__}: {error}"
        log.error(
            "supervisor frame desync #%d on pid %d: %s; a length-prefixed stream "
            "cannot be resynchronised, so the connection is being dropped "
            "(%d session(s) affected)",
            self.desync_count,
            self.supervisor_pid,
            self.desync_reason,
            len(self.hosts),
        )
        self._on_connection_lost(f"frame desync: {self.desync_reason}")

    def _on_connection_lost(self, reason: str) -> None:
        if not self.connected:
            return
        self.connected = False
        self.lost_reason = reason
        self.lost_at = time.time()
        for future in self._pending.values():
            if not future.done():
                future.set_exception(SupervisorUnavailable(reason))
        # "The socket broke" and "the supervisor died" are different facts. Only
        # the second one means the kill-on-close Jobs already closed and the
        # process trees are gone. Treating a transient read fault as death used to
        # fabricate an exit for every live session — the agents kept running
        # invisibly, history recorded false exits, and the next daemon re-adopted
        # sessions the user had watched end.
        self.lost = _pid_running(self.supervisor_pid)
        if self.lost:
            log.error(
                "supervisor connection %s but pid %d is still alive; %d session(s) are "
                "running unreachable (%s) — restart the daemon to reattach; no session "
                "will be recorded as ended on this evidence",
                reason,
                self.supervisor_pid,
                len(self.hosts),
                ", ".join(sorted(self.hosts)) or "none",
            )
            return
        log.warning(
            "supervisor %s and pid %d is gone; its kill-on-close job reaped every "
            "child, so %d remote session(s) are confirmed dead",
            reason,
            self.supervisor_pid,
            len(self.hosts),
        )
        for host in tuple(self.hosts.values()):
            host._mark_dead(None)

    # -- host lifecycle -----------------------------------------------------------

    def register_host(self, host: RemotePtyHost) -> None:
        self.hosts[host.sid] = host

    def unregister_host(self, host: RemotePtyHost) -> None:
        current = self.hosts.get(host.sid)
        if current is host:
            del self.hosts[host.sid]
        host._cancel_pump()
        self._meta_pending.pop(host.sid, None)
        self._meta_sent.pop(host.sid, None)

    async def spawn_session(self, host: RemotePtyHost) -> None:
        response, _ = await self.request(
            {
                "t": "spawn",
                "sid": host.sid,
                "appname": host.appname,
                "argv": list(host.argv),
                "cwd": host.cwd,
                "cols": host.cols,
                "rows": host.rows,
                "env": host.env,
                "graceful_exit": host.graceful_exit,
                "max_scrollback": host.max_scrollback,
                "meta": host.meta,
            },
            host.initial_output,
        )
        host.pid = int(response.get("pid", -1))
        host.reaper_assignment = str(response.get("reaper_assignment", "supervisor_unknown"))
        host._alive = True

    async def spawn_status(
        self, sid: str, *, timeout_seconds: float = SPAWN_STATUS_TIMEOUT_SECONDS
    ) -> SpawnStatus:
        """Ask what actually became of a spawn whose reply this daemon lost.

        The whole point is the difference between "the supervisor never reserved
        this id" (safe to spawn in-process instead) and "it has it" (an
        in-process fallback would put two agents in one workspace - audit F2).
        A supervisor predating the message answers "unknown message type", which
        `request` surfaces as RuntimeError; that is a steady state, not a fault,
        and it is reported as ``unsupported`` so the caller can keep the old
        behavior deliberately rather than by accident.
        """
        try:
            response, _ = await self.request(
                {"t": "spawn_status", "sid": sid}, timeout_seconds=timeout_seconds
            )
        except SupervisorUnavailable as exc:
            return SpawnStatus(SPAWN_STATE_INDETERMINATE, detail=str(exc))
        except TimeoutError:
            return SpawnStatus(
                SPAWN_STATE_INDETERMINATE, detail=f"no reply within {timeout_seconds:g}s"
            )
        except RuntimeError as exc:
            return SpawnStatus(SPAWN_STATE_UNSUPPORTED, detail=str(exc))
        state = str(response.get("state") or "")
        if state not in _SUPERVISOR_SPAWN_STATES:
            return SpawnStatus(
                SPAWN_STATE_INDETERMINATE, detail=f"unrecognised spawn state {state!r}"
            )
        pid = response.get("pid")
        started_at = response.get("started_at")
        return SpawnStatus(
            state,
            pid=int(pid) if isinstance(pid, int | float) else None,
            started_at=float(started_at) if isinstance(started_at, int | float) else None,
        )

    async def resolve_spawn_outcome(
        self, sid: str, *, deadline_seconds: float | None = None
    ) -> SpawnStatus:
        """Poll ``spawn_status`` while the supervisor still has the spawn in flight.

        ``reserved`` means the session id is taken but the child has not been
        created yet, so it answers neither of the caller's questions. Waiting it
        out is bounded; every other state returns immediately. The bound is read
        at call time rather than bound as a default, so a test can shorten it
        without every caller having to thread it through.
        """
        if deadline_seconds is None:
            deadline_seconds = SPAWN_STATUS_DEADLINE_SECONDS
        deadline = time.monotonic() + deadline_seconds
        status = await self.spawn_status(sid)
        while status.state == SPAWN_STATE_RESERVED and time.monotonic() < deadline:
            await asyncio.sleep(SPAWN_STATUS_POLL_SECONDS)
            status = await self.spawn_status(sid)
        return status

    def adopt_spawned(self, host: RemotePtyHost, status: SpawnStatus) -> None:
        """Take ownership of a session the supervisor spawned without us hearing.

        ``_reserve_spawn`` subscribes the requesting connection before the child
        exists, so output and ``pty_exit`` for this session are already arriving
        on this socket; only the reply was lost. Marking the host live is
        therefore adoption, not a guess.
        """
        if status.pid is not None:
            host.pid = status.pid
        host.reaper_assignment = "supervisor_adopted_after_lost_reply"
        # Only ``live`` is alive. A session still ``reserved`` after the poll
        # deadline is treated as dead deliberately: the ticker then ends it and
        # the release/remove that follows makes the supervisor stop the tree if
        # one did appear. Marking it alive instead would leave a session nothing
        # can ever end if the spawn actually failed.
        host._alive = status.state == SPAWN_STATE_LIVE
        log.warning(
            "session %s: spawn reply was lost but the supervisor reports %s (pid %s); "
            "adopting it instead of spawning a second process in-process",
            host.sid,
            status.state,
            status.pid if status.pid is not None else "unknown",
        )

    async def subscribe(self, host: RemotePtyHost) -> tuple[dict[str, Any], bytes]:
        """Attach to an existing supervised session; returns (info, scrollback)."""
        response, payload = await self.request({"t": "subscribe", "sid": host.sid})
        host._alive = bool(response.get("alive"))
        exit_code = response.get("exit_code")
        host._exit_code = int(exit_code) if exit_code is not None else None
        return response, payload

    async def stop_session(self, sid: str, *, graceful: bool, stop_timeout: float) -> None:
        await self.request(
            {"t": "stop", "sid": sid, "graceful": graceful, "timeout": stop_timeout},
            timeout_seconds=stop_timeout + RPC_TIMEOUT_SECONDS,
        )

    async def job_pids(self) -> dict[str, list[int]]:
        """Per-session Win32 job membership, or ``{}`` when it cannot be had.

        A supervisor predating this message answers "unknown message type",
        which `request` surfaces as RuntimeError. That is an expected steady
        state, not a fault: a new daemon is explicitly allowed to drive an older
        running supervisor rather than reap live sessions to update it. Every
        failure therefore degrades to "no job evidence" and the caller keeps
        using the parent walk alone.
        """
        try:
            response, _ = await self.request({"t": "job_pids"}, timeout_seconds=5.0)
        except (SupervisorUnavailable, RuntimeError, TimeoutError):
            return {}
        jobs = response.get("jobs")
        if not isinstance(jobs, dict):
            return {}
        result: dict[str, list[int]] = {}
        for sid, pids in jobs.items():
            if not isinstance(pids, list):
                continue
            result[str(sid)] = [int(pid) for pid in pids if isinstance(pid, int)]
        return result

    async def reap_all_and_exit(self) -> None:
        try:
            await self.request({"t": "reap_all_and_exit"}, timeout_seconds=10.0)
        except (SupervisorUnavailable, RuntimeError, TimeoutError):
            log.warning("reap_all_and_exit did not acknowledge", exc_info=True)

    # -- session metadata mirror ----------------------------------------------------

    def queue_meta(self, sid: str, meta: dict[str, Any]) -> None:
        """Coalesce and push session metadata so a future daemon can rebuild state."""
        if not self.connected or sid not in self.hosts:
            return
        try:
            serialized = json.dumps(meta, separators=(",", ":"), default=str)
        except (TypeError, ValueError):
            return
        if self._meta_sent.get(sid) == serialized:
            return
        self._meta_pending[sid] = json.loads(serialized)
        self._meta_sent[sid] = serialized
        if self._meta_task is None or self._meta_task.done():
            self._meta_task = self._loop.create_task(self._flush_meta_later())

    async def _flush_meta_later(self) -> None:
        await asyncio.sleep(META_FLUSH_DELAY_SECONDS)
        self._flush_meta_now()

    def _flush_meta_now(self) -> None:
        pending, self._meta_pending = self._meta_pending, {}
        for sid, meta in pending.items():
            self.notify({"t": "set_meta", "sid": sid, "meta": meta})

    async def flush_meta(self) -> None:
        if self._meta_task is not None and not self._meta_task.done():
            self._meta_task.cancel()
            await asyncio.gather(self._meta_task, return_exceptions=True)
        self._flush_meta_now()
        if self.connected:
            try:
                await asyncio.wait_for(self._writer.drain(), timeout=5.0)
            except (TimeoutError, ConnectionError, OSError):
                pass


def _discovery_info(data_dir: Path) -> dict[str, Any]:
    try:
        info = json.loads(discovery_path(data_dir).read_text(encoding="utf-8"))
        return info if isinstance(info, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _discovery_pid(data_dir: Path) -> int:
    try:
        return int(_discovery_info(data_dir).get("pid", -1))
    except (ValueError, TypeError):
        return -1


# The discovery file's ``started_at`` is stamped when the supervisor writes the
# file, a moment *after* the process itself started, so the two clocks bracket
# rather than match: the recorded time must land after process creation and
# within a launch's worth of it. A recycled pid lands outside that bracket in
# whichever direction it was reused.
SUPERVISOR_START_SKEW_SECONDS = 300.0
SUPERVISOR_START_BACKDATE_TOLERANCE_SECONDS = 5.0
# Every way the supervisor can be launched (`supervisor_command`) leaves one of
# these in the command line: the source module, the frozen app's child flag, or
# the dedicated bundle's executable name.
_SUPERVISOR_CMDLINE_MARKERS = ("swe_mux.supervisor", "--supervisor-child", "swe-mux-supervisor")


def _supervisor_identity_check(process: Any, *, started_at: float | None, config_path: str) -> str:
    """Empty string when this pid really is our supervisor, else why not.

    Fails closed. The evidence is the same PID+creation-time discipline
    `processes.py` uses everywhere else, plus the command line, because the name
    test this replaces ("swe" appears in the process name") would happily
    terminate any unrelated program whose name contains "swe" once a pid was
    recycled - and the action it gates kills every session tree the target owns.
    """
    if not started_at or started_at <= 0:
        return "the discovery file records no started_at to verify the pid against"
    try:
        created = float(process.create_time())
    except Exception as exc:  # noqa: BLE001 - psutil raises provider-specific errors
        return f"the process creation time could not be read ({exc})"
    age = started_at - created
    if age < -SUPERVISOR_START_BACKDATE_TOLERANCE_SECONDS or age > SUPERVISOR_START_SKEW_SECONDS:
        return (
            f"creation time {created:.3f} does not match the recorded start "
            f"{started_at:.3f} (delta {age:.3f}s); the pid has been reused"
        )
    try:
        cmdline = " ".join(str(part) for part in (process.cmdline() or []))
    except Exception as exc:  # noqa: BLE001 - psutil raises provider-specific errors
        return f"the command line could not be read ({exc})"
    if not cmdline:
        return "the process reports an empty command line"
    folded = cmdline.casefold()
    if not any(marker in folded for marker in _SUPERVISOR_CMDLINE_MARKERS):
        return f"the command line is not a supervisor launch ({cmdline!r})"
    if config_path and config_path.casefold() not in folded:
        return f"the command line names a different config than {config_path!r}"
    return ""


def _terminate_supervisor(pid: int, *, started_at: float | None, config_path: str = "") -> bool:
    """Last-resort reap: closing the supervisor's Job kills every session tree.

    Used only when the protocol handshake cannot be completed (a supervisor from
    a previous build after a PROTOCOL_VERSION bump). Without it the documented
    "force quit everything" action fails and the only remaining recovery is the
    manual taskkill the design says must never be required. Because the blast
    radius is every process tree the target owns, the target's identity is
    verified first and anything short of proof declines (audit F7).
    """
    try:
        import psutil

        process = psutil.Process(pid)
        rejection = _supervisor_identity_check(
            process, started_at=started_at, config_path=config_path
        )
        if rejection:
            log.error("refusing to terminate pid %d as the PTY supervisor: %s", pid, rejection)
            return False
        log.warning(
            "terminating verified supervisor pid %d (started_at %.3f, config %s)",
            pid,
            started_at or 0.0,
            config_path or "<unrecorded>",
        )
        process.terminate()
        process.wait(timeout=10)
        return True
    except Exception:
        log.exception("could not terminate supervisor pid %d", pid)
        return False


async def kill_server(config: Config) -> bool:
    """Explicit kill-server: reap every supervised session and stop the supervisor."""
    info = _discovery_info(config.data_dir)
    try:
        pid = int(info.get("pid", -1))
    except (TypeError, ValueError):
        pid = -1
    try:
        started_at: float | None = float(info["started_at"])
    except (KeyError, TypeError, ValueError):
        started_at = None
    recorded_config = str(info.get("config_path") or "")
    try:
        client = await SupervisorClient.connect(config.data_dir)
    except SupervisorUnavailable as exc:
        if pid <= 0 or not _pid_running(pid):
            return False
        # Reachable-but-unusable is not "absent": a protocol-version mismatch
        # leaves live sessions running that this daemon cannot address.
        log.warning("supervisor pid %d is alive but unusable (%s); terminating it", pid, exc)
        if not await asyncio.to_thread(
            _terminate_supervisor, pid, started_at=started_at, config_path=recorded_config
        ):
            return False
        _clear_discovery(config.data_dir, pid)
        return True
    pid = client.supervisor_pid
    await client.reap_all_and_exit()
    await client.close()
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if not _pid_running(pid):
            break
        await asyncio.sleep(0.2)
    _clear_discovery(config.data_dir, pid)
    return True


def _clear_discovery(data_dir: Path, pid: int) -> None:
    discovery = discovery_path(data_dir)
    try:
        info = json.loads(discovery.read_text(encoding="utf-8"))
        if int(info.get("pid", -1)) == pid:
            discovery.unlink(missing_ok=True)
    except (OSError, ValueError):
        pass


def _pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import psutil

        return bool(psutil.pid_exists(pid))
    except Exception:
        return False


# Kept out of RemotePtyHost.__init__ so adoption reads naturally at call sites.
def host_for_adoption(client: SupervisorClient, info: dict[str, Any]) -> RemotePtyHost:
    host = RemotePtyHost(
        client,
        str(info["sid"]),
        cols=int(info.get("cols", 120)),
        rows=int(info.get("rows", 30)),
        meta=info.get("meta") if isinstance(info.get("meta"), dict) else {},
    )
    host.pid = int(info.get("pid", -1))
    host.reaper_assignment = str(info.get("reaper_assignment", "supervisor_unknown"))
    host._alive = bool(info.get("alive"))
    exit_code = info.get("exit_code")
    host._exit_code = int(exit_code) if exit_code is not None else None
    return host


__all__ = [
    "Liveness",
    "RemotePtyHost",
    "SpawnStatus",
    "SupervisorClient",
    "SupervisorUnavailable",
    "host_for_adoption",
    "kill_server",
    "liveness_of",
    "supervisor_command",
]
