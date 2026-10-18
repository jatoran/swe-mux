"""Out-of-process PTY supervisor: the stable half of the daemon split.

Owns the ConPTYs, their read loops, authoritative scrollback, and the
kill-on-close reaper Job so live agent sessions survive a daemon restart
(SESSION_PRESERVING_RELOAD.md §5). The daemon is a client of this process
over a token-authenticated loopback socket. Everything here is deliberately
small and near-frozen: hot-updating the supervisor itself kills sessions
(§8), so volatile code must stay daemon-side.

Wire format: 4-byte big-endian header length, JSON header, then an optional
binary payload whose size is the header's ``plen``. Requests carry ``id`` and
are answered with ``{id, ok, ...}``; ``output``/``pty_exit`` frames are
unsolicited and tagged with ``sid``.

The two directions are bounded differently on purpose. Daemon-inbound frames are
capped and an unauthenticated connection is capped harder still and given a
deadline; the reverse direction is left unbounded, because a legitimate
``subscribe`` reply carries a whole scrollback buffer whose size is the daemon's
``scrollback_bytes`` setting.

A client that advertises ``output_gap`` in ``hello`` is never waited on
(DAEMON_LOAD_ISOLATION.md item 12). Its frames go through an outbox drained by
its own writer task; once that outbox holds ``OUTPUT_GAP_QUEUE_BYTES`` of output,
each session's further output is dropped for that client and one ``gap {sid,
position}`` frame says where its copy stops, and the client resubscribes to
recover. Every other client is written to directly and waited on when it falls
behind, which is the behaviour an older daemon was built against.

Item 13 adds, all additively: a ``stream`` id per session incarnation on every
session info and ``subscribe`` reply; ``subscribe`` with ``since`` and that
``stream``, answered with only the newer bytes while they are retained; a
``terminal`` scope that never receives a session's mirrored metadata and may only
drive terminals; and unsolicited ``resized`` and ``removed`` notices.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import ctypes
import faulthandler
import hashlib
import json
import logging
import logging.handlers
import os
import secrets
import struct
import sys
import time
import tomllib
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from .nested_job import create_nested_session_job
from .process_reaper import ProcessReaper, create_reaper, process_in_job
from .pty_host import PtyHost
from .scrollback import ScrollbackBuffer
from .timer_resolution import raise_timer_resolution

log = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
DISCOVERY_FILENAME = "supervisor.json"
MAX_HEADER_BYTES = 4 * 1024 * 1024
# Daemon-inbound bounds (F13). A header carries the child's whole environment
# block and the session's mirrored metadata, so it is not small; a payload
# carries at most one paste or one respawn's seeded scrollback. Both are far
# above anything legitimate and finite, which is the point: before this, a
# header could name any payload size at all and the supervisor read it.
#
# The interaction to know about: `scrollback_bytes` is settable up to 1 GiB, and
# a respawn seeds the new session with the old one's buffer. A configuration
# above `REQUEST_MAX_PAYLOAD_BYTES` would have that seed refused (the spawn then
# fails and the daemon falls back), which is why this is 12x the 5 MiB default
# rather than snug against it.
REQUEST_MAX_HEADER_BYTES = MAX_HEADER_BYTES
REQUEST_MAX_PAYLOAD_BYTES = 64 * 1024 * 1024
# Before `hello`, a connection has proven nothing. It gets a header cap sized for
# the only frame it is allowed to send and no payload allowance at all, so an
# unauthenticated peer cannot make the supervisor allocate.
HELLO_MAX_HEADER_BYTES = 64 * 1024
# ...and a deadline, so it cannot hold a connection and a task open by simply
# never speaking.
AUTH_DEADLINE_SECONDS = 15.0
# Backpressure threshold per client connection before the fanout awaits drain.
# Applies only to a connection that did not negotiate `output_gap`.
WRITE_BUFFER_HIGH_WATER = 2 * 1024 * 1024
# What a client advertises in `hello` to be dropped from rather than waited on.
CAPABILITY_OUTPUT_GAP = "output_gap"
# `subscribe` honours `since` together with the `stream` it was taken against.
CAPABILITY_SUBSCRIBE_SINCE = "subscribe_since"
# `hello` accepts `scope: "terminal"`.
CAPABILITY_TERMINAL_SCOPE = "terminal_scope"
# Unsolicited `resized` and `removed` frames.
CAPABILITY_NOTICES = "notices"
# Every capability this supervisor understands, announced in the `hello` reply so
# a client can tell an older supervisor (no list) from one that declined it.
SUPERVISOR_CAPABILITIES = (
    CAPABILITY_OUTPUT_GAP,
    CAPABILITY_SUBSCRIBE_SINCE,
    CAPABILITY_TERMINAL_SCOPE,
    CAPABILITY_NOTICES,
)
# A connection's scope, chosen in `hello`. The daemon's is the default; a terminal
# gateway asks for `terminal`, which never receives a session's mirrored metadata
# (it carries `hook_secret` and `mcp_token`) and may only drive terminals.
SCOPE_DAEMON = "daemon"
SCOPE_TERMINAL = "terminal"
TERMINAL_SCOPE_MESSAGES = frozenset({"subscribe", "unsubscribe", "write", "resize", "list", "ping"})
# Output an `output_gap` connection may have queued and unwritten before each
# session's further output is dropped for it with a `gap`. Sized to ride out a
# daemon stall of several seconds at fleet output rates (36 sessions at 20 KB/s
# fill it in about eleven) while bounding what one stopped reader costs this
# process; replies are not counted, so a large `subscribe` reply never causes one.
OUTPUT_GAP_QUEUE_BYTES = 8 * 1024 * 1024
# Everything queued for one `output_gap` connection, replies included, past which
# the supervisor stops reading that connection's requests until its outbox is
# back under the output cap. Only a client that keeps asking and never reads its
# answers can get here, and the pause is its own: nothing else waits on it.
REQUEST_PAUSE_QUEUE_BYTES = 64 * 1024 * 1024
# With no attached daemon and no live sessions there is nothing to preserve;
# self-exit keeps a forgotten supervisor from lingering forever (tmux-style).
LINGER_POLL_SECONDS = 30.0
LINGER_IDLE_EXIT_SECONDS = 15 * 60.0
# Teardown waits this long for in-flight background work - a ConPTY spawn already
# past its refusal check - before reaping anyway (F8).
SHUTDOWN_DRAIN_SECONDS = 20.0
# How long a duplicate `spawn` waits for the original one to resolve before
# answering honestly that it is still in flight. Kept under the client's RPC
# timeout so the answer arrives rather than the caller timing out again.
SPAWN_DEDUPE_WAIT_SECONDS = 55.0
ERROR_ALREADY_EXISTS = 183

# `spawn_status` states. "unknown" is the load-bearing one: it is the only answer
# that lets a daemon whose spawn reply was lost fall back without risking a
# second agent process in the same workspace.
SPAWN_STATE_UNKNOWN = "unknown"
SPAWN_STATE_RESERVED = "reserved"
SPAWN_STATE_LIVE = "live"
SPAWN_STATE_EXITED = "exited"


def encode_frame(header: dict[str, Any], payload: bytes = b"") -> bytes:
    if payload:
        header = {**header, "plen": len(payload)}
    raw = json.dumps(header, separators=(",", ":")).encode("utf-8")
    return struct.pack(">I", len(raw)) + raw + payload


async def read_frame(
    reader: asyncio.StreamReader,
    *,
    max_header_bytes: int = MAX_HEADER_BYTES,
    max_payload_bytes: int | None = None,
) -> tuple[dict[str, Any], bytes]:
    """Read one framed message, refusing a payload length before honouring it.

    ``max_payload_bytes`` of None means unbounded and is the default *on
    purpose*: the same function reads the client-inbound direction, where a
    single legitimate frame carries a whole scrollback buffer. The supervisor's
    own server loop passes explicit caps; a caller that does not is the generous
    direction. A malformed or negative length is refused either way, because
    ``readexactly`` would otherwise be handed a value it cannot mean.
    """
    (size,) = struct.unpack(">I", await reader.readexactly(4))
    if size > max_header_bytes:
        raise ValueError(f"oversized frame header: {size} bytes")
    header = json.loads(await reader.readexactly(size))
    if not isinstance(header, dict):
        raise ValueError("frame header must be a JSON object")
    raw_plen = header.get("plen", 0)
    try:
        plen = int(raw_plen)
    except (TypeError, ValueError):
        raise ValueError(f"frame payload length is not a number: {raw_plen!r}") from None
    if plen < 0:
        raise ValueError(f"negative frame payload length: {plen}")
    if max_payload_bytes is not None and plen > max_payload_bytes:
        raise ValueError(f"oversized frame payload: {plen} bytes (cap {max_payload_bytes})")
    payload = await reader.readexactly(plen) if plen else b""
    return header, payload


def new_stream_id(sid: str) -> str:
    """One incarnation of a session id, so a byte cursor cannot cross into another.

    A respawn reuses the id and restarts the byte positions at its seed, so a cursor
    taken against the previous incarnation would be answered with the wrong bytes.
    Minted at reservation rather than from `started_at`, which does not exist until
    the child does, and suffixed so two reservations in one millisecond still differ.
    """
    return f"{sid}@{int(time.time() * 1000)}.{secrets.token_hex(4)}"


class ScopeRefused(Exception):
    """A message this connection's scope does not allow; refused, not disconnected."""


def instance_key(config_path: Path) -> str:
    normalized = str(config_path.resolve()).casefold().encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()[:20]


def discovery_path(data_dir: Path) -> Path:
    return data_dir / DISCOVERY_FILENAME


def resolve_data_dir(config_path: Path) -> Path:
    """Minimal config read; the supervisor must not depend on config schema churn."""
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
        data_dir = raw.get("data_dir")
        if isinstance(data_dir, str) and data_dir:
            return Path(data_dir)
    except (OSError, tomllib.TOMLDecodeError):
        pass
    return config_path.parent


class SingleInstanceMutex:
    """Windows named mutex keyed on the config path: one supervisor per config."""

    def __init__(self, key: str) -> None:
        self.already_running = False
        self._handle = None
        self._kernel32 = None
        if sys.platform != "win32":
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_bool
        self._kernel32 = kernel32
        self._handle = kernel32.CreateMutexW(None, False, f"Local\\swe-mux-supervisor-{key}")
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.already_running = ctypes.get_last_error() == ERROR_ALREADY_EXISTS

    def close(self) -> None:
        if self._handle and self._kernel32:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


@dataclass(eq=False)
class SupervisedSession:
    sid: str
    host: PtyHost
    scrollback: ScrollbackBuffer
    meta: dict[str, Any] = field(default_factory=dict)
    ownership_job: ProcessReaper | None = None
    subscribers: set[Connection] = field(default_factory=set)
    alive: bool = True
    exit_code: int | None = None
    fanout_task: asyncio.Task[None] | None = None
    # The reader stopped while the root process was still running. The session
    # keeps its process tree (and its kill-on-close job) but produces no further
    # output; an implicit remove must not reap it as if it had exited.
    output_ended_while_alive: bool = False
    # Spawn idempotency and the `spawn_status` query (F2). The reservation exists
    # from the instant the request is accepted, so a duplicate spawn - or a
    # client asking what became of a reply it never received - is answered from
    # the first attempt's outcome rather than by starting a second agent process
    # in the same workspace. `spawn_done` is set exactly once, on success and on
    # failure alike, so nothing can wait on it forever after the attempt ended.
    spawn_done: asyncio.Event = field(default_factory=asyncio.Event)
    spawn_error: str | None = None
    started_at: float | None = None
    stream: str = ""

    def __post_init__(self) -> None:
        if not self.stream:
            self.stream = new_stream_id(self.sid)

    def spawn_state(self) -> str:
        if not self.spawn_done.is_set():
            return SPAWN_STATE_RESERVED
        return SPAWN_STATE_LIVE if self.alive else SPAWN_STATE_EXITED


@dataclass(eq=False)
class Connection:
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    authenticated: bool = False
    peer: str = ""
    scope: str = SCOPE_DAEMON
    scope_refusals: int = 0
    # Negotiated in `hello`. A lossy connection's frames are queued in `_outbox`
    # and written by `_writer_task`, so nothing that sends to it ever waits on it.
    output_gap: bool = False
    # Sessions whose output this connection has stopped receiving since a `gap`;
    # `subscribe` and `unsubscribe` take a session out again.
    gapped: set[str] = field(default_factory=set)
    gaps: int = 0
    dropped_bytes: int = 0
    closed: bool = False
    _outbox: deque[tuple[bytes, bool]] = field(default_factory=deque)
    _queued_bytes: int = 0
    _queued_output_bytes: int = 0
    _wake: asyncio.Event = field(default_factory=asyncio.Event)
    _room: asyncio.Event = field(default_factory=asyncio.Event)
    _idle: asyncio.Event = field(default_factory=asyncio.Event)
    _writer_task: asyncio.Task[None] | None = None

    def send(self, header: dict[str, Any], payload: bytes = b"") -> None:
        self.send_frame(encode_frame(header, payload))

    def send_frame(self, frame: bytes) -> None:
        """Queue a control frame or a reply. Never dropped for being over the cap."""
        if self.output_gap:
            self._enqueue(frame, output=False)
        else:
            self.writer.write(frame)

    @property
    def queued_bytes(self) -> int:
        """Everything in the outbox, replies included; zero for a direct connection."""
        return self._queued_bytes

    def start_lossy(self) -> None:
        """Switch to the queued, never-waited-on mode `output_gap` asks for.

        Called while `hello` is handled, before its reply is sent, so the reply is
        the outbox's first frame and nothing written directly can follow it.
        """
        self.output_gap = True
        self._room.set()
        self._idle.set()
        self._writer_task = asyncio.create_task(
            self._write_loop(), name=f"sup-writer-{self.peer or id(self)}"
        )

    def offer_output(self, sid: str, frame: bytes, start: int, size: int) -> None:
        """Queue one session's output frame, or drop it. Never waits.

        ``start`` is the session's stream position at the first byte of the frame,
        which is exactly where this connection's copy stops when it is the first
        frame dropped, so that is what the `gap` names.
        """
        if self.closed:
            return
        if sid in self.gapped:
            self.dropped_bytes += size
            return
        if self._queued_output_bytes >= OUTPUT_GAP_QUEUE_BYTES:
            if not self.gapped:
                log.warning(
                    "supervisor: connection %s is not reading (%d byte(s) of output "
                    "queued); dropping its output per session with gap frames until "
                    "it resubscribes",
                    self.peer,
                    self._queued_output_bytes,
                )
            self.gapped.add(sid)
            self.gaps += 1
            self.dropped_bytes += size
            self._enqueue(encode_frame({"t": "gap", "sid": sid, "position": start}), output=False)
            return
        self._enqueue(frame, output=True)

    def resume(self, sid: str) -> None:
        """Deliver ``sid``'s output to this connection again (it resubscribed or left)."""
        if sid not in self.gapped:
            return
        self.gapped.discard(sid)
        if not self.gapped:
            log.info(
                "supervisor: connection %s caught up; %d gap(s) so far, %d byte(s) "
                "of output dropped in total",
                self.peer,
                self.gaps,
                self.dropped_bytes,
            )

    def _enqueue(self, frame: bytes, *, output: bool) -> None:
        if self.closed:
            return
        self._outbox.append((frame, output))
        self._queued_bytes += len(frame)
        if output:
            self._queued_output_bytes += len(frame)
        self._idle.clear()
        self._wake.set()

    async def _write_loop(self) -> None:
        """Write the outbox in order, waiting only on this connection's transport."""
        try:
            while True:
                await self._wake.wait()
                self._wake.clear()
                while self._outbox:
                    frame, output = self._outbox.popleft()
                    self._queued_bytes -= len(frame)
                    if output:
                        self._queued_output_bytes -= len(frame)
                    self.writer.write(frame)
                    # Returns at once unless the transport has paused this writer;
                    # the outbox, not the transport, is where a backlog accumulates.
                    await self.writer.drain()
                    if self._queued_bytes <= OUTPUT_GAP_QUEUE_BYTES:
                        self._room.set()
                self._idle.set()
        except (ConnectionError, RuntimeError, OSError) as exc:
            log.info("supervisor: connection %s write failed: %s", self.peer, exc)
            self._abandon()
            with contextlib.suppress(Exception):
                self.writer.close()

    async def wait_for_room(self) -> None:
        """Wait until the outbox is back under the output cap, or the connection is gone."""
        while self._queued_bytes > OUTPUT_GAP_QUEUE_BYTES and not self.closed:
            self._room.clear()
            await self._room.wait()

    async def flush(self) -> None:
        """Wait until everything sent so far has reached the transport and drained."""
        if self.output_gap:
            while self._outbox and not self.closed:
                await self._idle.wait()
        if not self.closed:
            await self.writer.drain()

    def _abandon(self) -> None:
        self.closed = True
        self._outbox.clear()
        self._queued_bytes = 0
        self._queued_output_bytes = 0
        self._room.set()
        self._idle.set()

    def close(self) -> asyncio.Task[None] | None:
        """Stop writing and close the transport; returns the writer task to await."""
        self._abandon()
        task, self._writer_task = self._writer_task, None
        if task is not None and not task.done():
            task.cancel()
        with contextlib.suppress(Exception):
            self.writer.close()
        return task


class SupervisorServer:
    def __init__(self, config_path: Path, data_dir: Path) -> None:
        self.config_path = config_path
        self.data_dir = data_dir
        self.token = secrets.token_urlsafe(32)
        self.reaper = create_reaper()
        self.sessions: dict[str, SupervisedSession] = {}
        self.connections: set[Connection] = set()
        self.exit_event = asyncio.Event()
        self._server: asyncio.Server | None = None
        self._last_busy = time.monotonic()
        self._background_tasks: set[asyncio.Task[None]] = set()
        # Set before the listener closes, so nothing new is accepted while
        # in-flight work drains (F8).
        self._closing = False

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._serve_connection, "127.0.0.1", 0)
        port = int(self._server.sockets[0].getsockname()[1])
        self._write_discovery(port)
        log.info("supervisor listening on 127.0.0.1:%d", port)
        return port

    def _write_discovery(self, port: int) -> None:
        payload = {
            "pid": os.getpid(),
            "port": port,
            "token": self.token,
            "protocol": PROTOCOL_VERSION,
            "config_path": str(self.config_path),
            "started_at": time.time(),
        }
        self.data_dir.mkdir(parents=True, exist_ok=True)
        path = discovery_path(self.data_dir)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(temporary, path)

    def _remove_discovery(self) -> None:
        try:
            path = discovery_path(self.data_dir)
            current = json.loads(path.read_text(encoding="utf-8"))
            if int(current.get("pid", -1)) == os.getpid():
                path.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass

    async def run(self) -> None:
        linger = asyncio.create_task(self._linger_loop(), name="supervisor-linger")
        try:
            await self.exit_event.wait()
        finally:
            linger.cancel()
            await asyncio.gather(linger, return_exceptions=True)
            await self._teardown()

    @property
    def refusing_spawns(self) -> bool:
        """Whether this supervisor has begun shutting down.

        Read at two points on purpose: when a `spawn` is dispatched, and again
        after its blocking ConPTY creation returns. One check cannot cover both,
        because the whole failure this exists for (F8) is a spawn that was
        already past the first check when teardown began.
        """
        return self._closing or self.exit_event.is_set()

    async def _teardown(self) -> None:
        # The order is the entire fix (F8). Refuse new work and stop accepting
        # connections, let in-flight spawns finish and be accounted for, and only
        # then close the Jobs -- a child created after the reaper Job closed is
        # assigned to nothing and killed by nothing, which is how an orphaned
        # agent escapes a reap that reported success.
        self._closing = True
        if self._server is not None:
            self._server.close()
        await self._drain_background_tasks()
        # Connections close before the listener is awaited, because since Python
        # 3.12.1 `wait_closed` also waits for every connected client to go away:
        # one client that stayed connected - a second daemon, a stalled reader -
        # used to hold the reap below open for as long as it stayed.
        writer_tasks = [task for task in (c.close() for c in tuple(self.connections)) if task]
        if writer_tasks:
            await asyncio.gather(*writer_tasks, return_exceptions=True)
        if self._server is not None:
            try:
                await asyncio.wait_for(self._server.wait_closed(), timeout=SHUTDOWN_DRAIN_SECONDS)
            except TimeoutError:
                log.warning(
                    "supervisor teardown: connection handlers still running after %.0fs; "
                    "reaping anyway",
                    SHUTDOWN_DRAIN_SECONDS,
                )
        for entry in self.sessions.values():
            if entry.fanout_task and not entry.fanout_task.done():
                entry.fanout_task.cancel()
            if entry.ownership_job:
                entry.ownership_job.close()
        self._remove_discovery()
        # Closing the Job handle is the reap: kill-on-close terminates every
        # process tree that was assigned to it.
        log.info("supervisor teardown: reaping %d session(s)", len(self.sessions))
        self.reaper.close()

    async def _drain_background_tasks(self) -> None:
        """Let in-flight handlers finish before anything is reaped.

        Bounded, because a wedged ConPTY creation must not hold the reap open
        forever; overrunning is logged rather than silent, since what escapes
        past the bound is exactly the orphan this drain exists to prevent.
        """
        pending = {task for task in self._background_tasks if not task.done()}
        if not pending:
            return
        log.info("supervisor teardown: draining %d in-flight operation(s)", len(pending))
        _done, still_pending = await asyncio.wait(pending, timeout=SHUTDOWN_DRAIN_SECONDS)
        if still_pending:
            log.warning(
                "supervisor teardown: %d operation(s) still running after %.0fs; "
                "cancelling and reaping anyway (a child created after this point "
                "is not owned by the reaper Job)",
                len(still_pending),
                SHUTDOWN_DRAIN_SECONDS,
            )
            for task in still_pending:
                task.cancel()
            await asyncio.gather(*still_pending, return_exceptions=True)

    async def _linger_loop(self) -> None:
        while True:
            await asyncio.sleep(LINGER_POLL_SECONDS)
            busy = bool(self.connections) or any(e.alive for e in self.sessions.values())
            if busy:
                self._last_busy = time.monotonic()
            elif time.monotonic() - self._last_busy > LINGER_IDLE_EXIT_SECONDS:
                log.info("no clients and no live sessions; supervisor exiting")
                self.exit_event.set()
                return

    # -- connection handling -------------------------------------------------

    async def _serve_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        connection = Connection(reader, writer, peer=str(peer))
        self.connections.add(connection)
        self._last_busy = time.monotonic()
        try:
            while not self.exit_event.is_set():
                try:
                    header, payload = await self._read_request(connection)
                except (asyncio.IncompleteReadError, ConnectionError):
                    break
                except TimeoutError:
                    log.warning(
                        "supervisor: dropping connection from %s that never authenticated "
                        "within %.0fs",
                        peer,
                        AUTH_DEADLINE_SECONDS,
                    )
                    break
                except ValueError as exc:
                    # A refused or malformed frame leaves the stream desynced at
                    # an unknown offset, so there is nothing to resynchronise to:
                    # the connection is the unit that has to go.
                    log.warning(
                        "supervisor: refusing frame from %s (authenticated=%s): %s",
                        peer,
                        connection.authenticated,
                        exc,
                    )
                    break
                try:
                    await self._dispatch(connection, header, payload)
                except PermissionError as exc:
                    if header.get("id") is not None:
                        connection.send({"id": header["id"], "ok": False, "error": str(exc)})
                    with contextlib.suppress(ConnectionError):
                        await connection.flush()
                    break
                except ScopeRefused as exc:
                    # The connection stays: a gateway asking for something it may
                    # not have is a bug to log, not a reason to drop its terminals.
                    connection.scope_refusals += 1
                    if connection.scope_refusals == 1:
                        log.warning("supervisor: %s from %s", exc, peer)
                    if header.get("id") is not None:
                        connection.send({"id": header["id"], "ok": False, "error": str(exc)})
                except Exception as exc:
                    request_id = header.get("id")
                    log.exception("failed handling %s", header.get("t"))
                    if request_id is not None:
                        connection.send({"id": request_id, "ok": False, "error": str(exc)})
                if connection.output_gap:
                    # Replies are queued rather than waited for, so a client that has
                    # stopped reading still has its keystrokes written. Only one that
                    # also keeps asking past the pause threshold is held, alone.
                    if connection.queued_bytes > REQUEST_PAUSE_QUEUE_BYTES:
                        await connection.wait_for_room()
                    continue
                with contextlib.suppress(ConnectionError):
                    await writer.drain()
        finally:
            self.connections.discard(connection)
            for entry in self.sessions.values():
                entry.subscribers.discard(connection)
            writer_task = connection.close()
            if writer_task is not None:
                await asyncio.gather(writer_task, return_exceptions=True)

    async def _read_request(self, connection: Connection) -> tuple[dict[str, Any], bytes]:
        """Read one daemon-inbound frame under the bounds its state has earned (F13).

        An unauthenticated connection is allowed exactly one shape - a
        payload-free ``hello`` with a small header - and a deadline to send it.
        The payload cap of zero is what makes "payload-free" enforced rather than
        assumed: before this, the supervisor read an arbitrary number of bytes
        named by an unauthenticated peer's header, and only then checked the
        token.
        """
        if connection.authenticated:
            return await read_frame(
                connection.reader,
                max_header_bytes=REQUEST_MAX_HEADER_BYTES,
                max_payload_bytes=REQUEST_MAX_PAYLOAD_BYTES,
            )
        return await asyncio.wait_for(
            read_frame(
                connection.reader,
                max_header_bytes=HELLO_MAX_HEADER_BYTES,
                max_payload_bytes=0,
            ),
            timeout=AUTH_DEADLINE_SECONDS,
        )

    async def _dispatch(
        self, connection: Connection, header: dict[str, Any], payload: bytes
    ) -> None:
        kind = header.get("t")
        request_id = header.get("id")
        if kind == "hello":
            self._handle_hello(connection, header, request_id)
            return
        if not connection.authenticated:
            raise PermissionError("hello with a valid token is required first")
        if connection.scope == SCOPE_TERMINAL and kind not in TERMINAL_SCOPE_MESSAGES:
            raise ScopeRefused(f"{kind!r} is not available to a terminal-scope connection")
        if kind == "spawn":
            if self.refusing_spawns:
                raise RuntimeError("supervisor is shutting down; spawn refused")
            existing = self.sessions.get(str(header.get("sid")))
            if existing is not None:
                # Idempotency (F2). A spawn whose reply was lost - a >60s ConPTY
                # stall under Defender is enough - used to be retried into a
                # *second* agent process on the same workspace, because the id
                # was already reserved and the supervisor answered "session
                # already exists". Answer with the first attempt's outcome
                # instead; the reservation is the deduplication key.
                log.info(
                    "spawn for %s deduplicated against an existing %s reservation",
                    existing.sid,
                    existing.spawn_state(),
                )
                self._spawn_background(
                    self._reply_existing_spawn(connection, existing, request_id),
                    connection,
                    request_id,
                )
            else:
                # Reserve and validate synchronously; run the blocking ConPTY
                # spawn off the read loop so one slow spawn cannot stall other
                # sessions' input frames on this connection.
                entry = self._reserve_spawn(connection, header, payload)
                self._spawn_background(
                    self._finish_spawn(connection, entry, request_id), connection, request_id
                )
        elif kind == "spawn_status":
            connection.send(
                {"id": request_id, "ok": True, **self._spawn_status(str(header.get("sid")))}
            )
        elif kind == "write":
            self._require(header).host.write(payload)
        elif kind == "resize":
            entry = self._require(header)
            entry.host.resize(int(header["cols"]), int(header["rows"]))
            # What the host applied, to everyone but the connection that asked: it
            # knows what it asked for, and an echo would race its next resize.
            cols, rows = entry.host.cols, entry.host.rows
            self._notify_subscribers(
                entry,
                {"t": "resized", "sid": entry.sid, "cols": cols, "rows": rows},
                exclude=connection,
            )
        elif kind == "set_graceful_exit":
            self._require(header).host.graceful_exit = str(header.get("keys") or "exit\r")
        elif kind == "set_meta":
            existing = self.sessions.get(str(header.get("sid")))
            if existing is not None:
                meta = header.get("meta")
                if isinstance(meta, dict):
                    existing.meta = meta
        elif kind == "subscribe":
            entry = self._require(header)
            delta = self._honours_since(entry, header)
            # Snapshot and subscription registration are synchronous, so the
            # fanout task cannot interleave a chunk between them: the client
            # neither misses nor duplicates the boundary chunk (the same
            # contract as Session.replay_and_subscribe, one level down). The same
            # step ends a gap: the reply carries everything the gap dropped.
            entry.subscribers.add(connection)
            connection.resume(entry.sid)
            reply: dict[str, Any] = {
                "id": request_id,
                "ok": True,
                "alive": entry.alive,
                "exit_code": entry.exit_code,
                "position": entry.scrollback.position,
                "stream": entry.stream,
                "delta": delta,
            }
            if connection.scope == SCOPE_DAEMON:
                reply["meta"] = entry.meta
            connection.send(
                reply,
                entry.scrollback.bytes_since(header["since"])
                if delta
                else entry.scrollback.bytes(),
            )
        elif kind == "unsubscribe":
            existing = self.sessions.get(str(header.get("sid")))
            if existing is not None:
                existing.subscribers.discard(connection)
                connection.resume(existing.sid)
        elif kind == "stop":
            entry = self._require(header)
            self._spawn_background(
                self._stop_and_reply(connection, entry, header, request_id),
                connection,
                request_id,
            )
        elif kind == "release":
            entry = self._require(header)
            if entry.alive:
                raise RuntimeError("cannot release a live session")
            self._spawn_background(
                self._release_and_reply(connection, entry, request_id), connection, request_id
            )
        elif kind == "remove":
            self._spawn_background(
                self._remove_and_reply(connection, header, request_id), connection, request_id
            )
        elif kind == "list":
            connection.send(
                {
                    "id": request_id,
                    "ok": True,
                    "sessions": self._session_infos(meta=connection.scope == SCOPE_DAEMON),
                }
            )
        elif kind == "job_pids":
            connection.send({"id": request_id, "ok": True, "jobs": self._job_pids()})
        elif kind == "ping":
            if request_id is not None:
                connection.send({"id": request_id, "ok": True})
        elif kind == "reap_all_and_exit":
            log.info("reap_all_and_exit requested; terminating %d session(s)", len(self.sessions))
            if request_id is not None:
                connection.send({"id": request_id, "ok": True})
                with contextlib.suppress(ConnectionError):
                    await connection.flush()
            self.exit_event.set()
        else:
            raise ValueError(f"unknown message type: {kind!r}")

    def _handle_hello(
        self, connection: Connection, header: dict[str, Any], request_id: Any
    ) -> None:
        token = str(header.get("token") or "")
        if not token or not secrets.compare_digest(token, self.token):
            raise PermissionError("invalid supervisor token")
        protocol = int(header.get("protocol", 0))
        if protocol != PROTOCOL_VERSION:
            raise PermissionError(
                f"protocol mismatch: supervisor={PROTOCOL_VERSION} client={protocol}"
            )
        scope = header.get("scope", SCOPE_DAEMON)
        if scope not in (SCOPE_DAEMON, SCOPE_TERMINAL):
            # Fails closed: a scope this supervisor does not know cannot be honoured
            # by widening it to the daemon's.
            raise PermissionError(f"unknown connection scope: {scope!r}")
        if connection.authenticated and scope != connection.scope:
            raise PermissionError("a connection's scope cannot change after hello")
        connection.scope = str(scope)
        connection.authenticated = True
        # Capabilities are additive and never gate the protocol version: an older
        # supervisor ignores the list it does not understand and announces none,
        # which is how the client knows it is being waited on as before.
        requested = header.get("capabilities")
        if (
            isinstance(requested, list)
            and CAPABILITY_OUTPUT_GAP in requested
            and not connection.output_gap
        ):
            connection.start_lossy()
        connection.send(
            {
                "id": request_id,
                "ok": True,
                "protocol": PROTOCOL_VERSION,
                "pid": os.getpid(),
                "capabilities": list(SUPERVISOR_CAPABILITIES),
                "scope": connection.scope,
                "sessions": self._session_infos(meta=connection.scope == SCOPE_DAEMON),
            }
        )

    @staticmethod
    def _honours_since(entry: SupervisedSession, header: dict[str, Any]) -> bool:
        """Whether a `subscribe` can be answered with only the bytes after its cursor.

        Only for the stream the cursor was taken against, and only while every byte
        after it is still retained; anything else gets the whole buffer, which is
        always correct and only ever larger.
        """
        since = header.get("since")
        if not isinstance(since, int) or isinstance(since, bool):
            return False
        if header.get("stream") != entry.stream:
            return False
        position = entry.scrollback.position
        return position - entry.scrollback.size <= since <= position

    @staticmethod
    def _notify_subscribers(
        entry: SupervisedSession, header: dict[str, Any], *, exclude: Connection | None
    ) -> None:
        """Send an unsolicited control frame to a session's subscribers.

        An older client ignores a frame type it does not know, so a notice needs no
        capability check on the way out.
        """
        frame = encode_frame(header)
        for subscriber in tuple(entry.subscribers):
            if subscriber is exclude:
                continue
            with contextlib.suppress(Exception):
                subscriber.send_frame(frame)

    def _job_pids(self) -> dict[str, list[int]]:
        """Per-session job membership, for the daemon's process attribution.

        The nested per-session job lives here rather than in the daemon -- a
        daemon-held handle would kill the tree on daemon exit and defeat session
        survival -- so the daemon cannot query it directly. It is the only
        ownership evidence that survives an intermediate parent exiting, which
        is what a detached launch (`Start-Process`) leaves behind.

        Deliberately *not* gated on PROTOCOL_VERSION: an older supervisor
        answers "unknown message type" and the daemon falls back to the parent
        walk, so a new daemon can still drive a running older supervisor rather
        than orphaning every live session over an attribution nicety.
        """
        result: dict[str, list[int]] = {}
        for entry in self.sessions.values():
            if entry.ownership_job is None:
                continue
            try:
                result[entry.sid] = entry.ownership_job.process_ids()
            except OSError:
                continue
        return result

    def _spawn_status(self, sid: str) -> dict[str, Any]:
        """What became of the spawn for ``sid`` (F2).

        The daemon reaches this exactly when it does not know: a spawn RPC timed
        out, or its connection dropped mid-request. The lost reply was the only
        thing that said whether an agent process now exists, and both guesses are
        harmful - assume it failed and a live agent is orphaned and re-adopted at
        next boot beside its replacement, assume it succeeded and a genuinely
        failed spawn leaves a dead pane. "unknown" here is a real answer: the id
        was never reserved, so falling back cannot duplicate anything.

        Deliberately *not* gated on PROTOCOL_VERSION, for the same reason
        `job_pids` is not: an older supervisor answers "unknown message type",
        the daemon degrades to its previous behaviour, and a new daemon can still
        drive a running older supervisor rather than orphaning every live session
        over a query.
        """
        entry = self.sessions.get(sid)
        if entry is None:
            log.info("spawn_status sid=%s state=%s", sid, SPAWN_STATE_UNKNOWN)
            return {"sid": sid, "state": SPAWN_STATE_UNKNOWN}
        state = entry.spawn_state()
        status: dict[str, Any] = {"sid": sid, "state": state}
        if state != SPAWN_STATE_RESERVED:
            status["pid"] = entry.host.pid
            status["started_at"] = entry.started_at
            status["reaper_assignment"] = entry.host.reaper_assignment
        if state == SPAWN_STATE_EXITED:
            status["exit_code"] = entry.exit_code
        log.info("spawn_status sid=%s state=%s pid=%s", sid, state, status.get("pid"))
        return status

    def _session_infos(self, *, meta: bool = True) -> list[dict[str, Any]]:
        infos: list[dict[str, Any]] = []
        for entry in self.sessions.values():
            info: dict[str, Any] = {
                "sid": entry.sid,
                "alive": entry.alive,
                "exit_code": entry.exit_code,
                "pid": entry.host.pid,
                "cols": entry.host.cols,
                "rows": entry.host.rows,
                "reaper_assignment": entry.host.reaper_assignment,
                "started_at": entry.started_at,
                "stream": entry.stream,
                # Read failures the reader swallows and keeps going from (F14).
                # A session that is alive and silent is otherwise indistinguishable
                # from one whose agent is simply thinking, and the daemon cannot
                # see the supervisor's reader thread at all.
                "read_errors": entry.host.read_errors,
                "last_read_error": entry.host.last_read_error,
                "last_read_error_at": entry.host.last_read_error_at,
            }
            if meta:
                info["meta"] = entry.meta
            infos.append(info)
        return infos

    def _require(self, header: dict[str, Any]) -> SupervisedSession:
        sid = str(header.get("sid"))
        entry = self.sessions.get(sid)
        if entry is None:
            raise KeyError(f"unknown session: {sid}")
        return entry

    # -- session lifecycle -----------------------------------------------------

    def _spawn_background(
        self, coroutine: Any, connection: Connection, request_id: Any
    ) -> None:
        """Run a blocking handler off the read loop, replying with its error if it fails."""

        async def guarded() -> None:
            try:
                await coroutine
            except Exception as exc:
                log.exception("background supervisor operation failed")
                if request_id is not None:
                    with contextlib.suppress(Exception):
                        connection.send({"id": request_id, "ok": False, "error": str(exc)})

        task = asyncio.create_task(guarded())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def _reserve_spawn(
        self, connection: Connection, header: dict[str, Any], initial_output: bytes
    ) -> SupervisedSession:
        sid = str(header["sid"])
        if sid in self.sessions:
            raise ValueError(f"session already exists: {sid}")
        env = header.get("env") or {}
        if not isinstance(env, dict):
            raise ValueError("env must be a string map")
        host = PtyHost(
            str(header["appname"]),
            tuple(str(item) for item in header.get("argv") or ()),
            str(header["cwd"]) if header.get("cwd") else None,
            cols=int(header.get("cols", 120)),
            rows=int(header.get("rows", 30)),
            reaper=self.reaper,
            env_extra={str(k): str(v) for k, v in env.items()},
            env_base={},
            graceful_exit=str(header.get("graceful_exit") or "exit\r"),
        )
        meta = header.get("meta")
        entry = SupervisedSession(
            sid,
            host,
            ScrollbackBuffer(int(header.get("max_scrollback", 5 * 1024 * 1024))),
            meta=meta if isinstance(meta, dict) else {},
        )
        if initial_output:
            entry.scrollback.append(initial_output)
        entry.subscribers.add(connection)
        self.sessions[sid] = entry
        return entry

    async def _finish_spawn(
        self, connection: Connection, entry: SupervisedSession, request_id: Any
    ) -> None:
        host = entry.host
        try:
            host.prepare()
            await asyncio.to_thread(host.spawn)
        except BaseException as exc:
            # Release the reservation: the id is free again, so a retry spawns
            # rather than being deduplicated against a session that never
            # existed. Anything already waiting on this attempt gets the reason.
            self.sessions.pop(entry.sid, None)
            entry.spawn_error = f"spawn failed: {exc}"
            entry.spawn_done.set()
            log.warning("session %s: spawn failed: %s", entry.sid, exc)
            raise
        if self.refusing_spawns:
            # This spawn was already past the dispatch-time refusal when teardown
            # began (F8). Its child exists now, moments before the reaper Job
            # closes, and if it were left alone it would be owned by nothing and
            # killed by nothing - a reap that reported success while an agent
            # kept running.
            log.warning(
                "session %s: spawn completed during shutdown (pid=%s); stopping it "
                "so it cannot escape the reap",
                entry.sid,
                host.pid,
            )
            self.sessions.pop(entry.sid, None)
            entry.spawn_error = "supervisor is shutting down"
            entry.spawn_done.set()
            await asyncio.to_thread(host.stop, graceful=False)
            raise RuntimeError("supervisor is shutting down; the spawned child was stopped")
        nested = create_nested_session_job(self.reaper, host.pid, sid=entry.sid)
        host.reaper_assignment += nested.suffix
        entry.ownership_job = nested.job
        entry.started_at = time.time()
        # Set before the reply is written: a duplicate spawn waiting on this
        # event must never observe a session the client has already been told about.
        entry.spawn_done.set()
        entry.fanout_task = asyncio.create_task(
            self._fanout(entry), name=f"sup-fanout-{entry.sid}"
        )
        log.info(
            "session %s spawned pid=%s assignment=%s",
            entry.sid,
            host.pid,
            host.reaper_assignment,
        )
        connection.send(
            {
                "id": request_id,
                "ok": True,
                "pid": host.pid,
                "reaper_assignment": host.reaper_assignment,
                "state": entry.spawn_state(),
                "started_at": entry.started_at,
                # Where the first output frame will start: after the seeded bytes.
                # The fanout was created above but has not run, so nothing has
                # been appended since the seed.
                "position": entry.scrollback.position,
                "stream": entry.stream,
            }
        )

    async def _reply_existing_spawn(
        self, connection: Connection, entry: SupervisedSession, request_id: Any
    ) -> None:
        """Answer a duplicate `spawn` with the first attempt's outcome (F2).

        Deliberately does *not* add this connection as a subscriber. Registration
        and the scrollback snapshot are one indivisible step in `subscribe`, and
        the whole reason they are indivisible is that splitting them either drops
        or duplicates the chunk on the boundary. A client recovering a lost reply
        attaches with `subscribe`, which is the path that already gets this right.
        """
        try:
            await asyncio.wait_for(entry.spawn_done.wait(), timeout=SPAWN_DEDUPE_WAIT_SECONDS)
        except TimeoutError:
            # Honest beats convenient: replying "ok" with no pid would have the
            # daemon record a session whose process may not exist.
            log.warning(
                "session %s: duplicate spawn timed out waiting for the original attempt",
                entry.sid,
            )
            if request_id is not None:
                connection.send(
                    {
                        "id": request_id,
                        "ok": False,
                        "error": f"spawn for {entry.sid} is still in flight",
                        "state": SPAWN_STATE_RESERVED,
                        "deduped": True,
                    }
                )
            return
        if request_id is None:
            return
        if entry.spawn_error is not None:
            connection.send(
                {
                    "id": request_id,
                    "ok": False,
                    "error": entry.spawn_error,
                    "deduped": True,
                }
            )
            return
        connection.send(
            {
                "id": request_id,
                "ok": True,
                "deduped": True,
                "state": entry.spawn_state(),
                "pid": entry.host.pid,
                "reaper_assignment": entry.host.reaper_assignment,
                "started_at": entry.started_at,
                "alive": entry.alive,
                "exit_code": entry.exit_code,
            }
        )

    async def _stop_and_reply(
        self,
        connection: Connection,
        entry: SupervisedSession,
        header: dict[str, Any],
        request_id: Any,
    ) -> None:
        await asyncio.to_thread(
            entry.host.stop,
            graceful=bool(header.get("graceful", True)),
            timeout=float(header.get("timeout", 2.0)),
        )
        if request_id is not None:
            connection.send({"id": request_id, "ok": True})

    async def _release_and_reply(
        self, connection: Connection, entry: SupervisedSession, request_id: Any
    ) -> None:
        await asyncio.to_thread(entry.host.release)
        if request_id is not None:
            connection.send({"id": request_id, "ok": True})

    async def _remove_and_reply(
        self, connection: Connection, header: dict[str, Any], request_id: Any
    ) -> None:
        sid = str(header.get("sid"))
        entry = self.sessions.pop(sid, None)
        if entry is not None:
            # Before the fanout stops, so nothing a subscriber receives afterwards
            # can be mistaken for the session still being here.
            self._notify_subscribers(entry, {"t": "removed", "sid": sid}, exclude=connection)
            for subscriber in tuple(entry.subscribers):
                subscriber.resume(sid)
            if entry.fanout_task and not entry.fanout_task.done():
                entry.fanout_task.cancel()
                await asyncio.gather(entry.fanout_task, return_exceptions=True)
            # The release branch assumes the process is already gone: it suppresses
            # the "cannot release a live pseudoconsole" error and then closes a
            # kill-on-close job, which reaps the tree abruptly. Re-check liveness so
            # a stale `alive=False` cannot take that path on a running agent.
            still_running = entry.alive or await asyncio.to_thread(entry.host.isalive)
            if still_running:
                if not entry.alive:
                    log.warning(
                        "session %s: removal requested for a session reported dead "
                        "but still running; stopping it deterministically",
                        sid,
                    )
                await asyncio.to_thread(entry.host.stop, graceful=False)
            else:
                with contextlib.suppress(RuntimeError):
                    await asyncio.to_thread(entry.host.release)
            if entry.ownership_job:
                entry.ownership_job.close()
                entry.ownership_job = None
        if request_id is not None:
            connection.send({"id": request_id, "ok": True})

    async def _fanout(self, entry: SupervisedSession) -> None:
        while True:
            chunk = await entry.host.output_queue.get()
            if chunk == b"":
                # The sentinel means "the reader stopped", which is not by itself
                # proof the child died — a reader that aborts (loop teardown, an
                # I/O fault) injects the same byte string. Declaring an exit here
                # without checking is what let a backpressured-but-live session be
                # reported as exited, and the removal path then closes its
                # kill-on-close job. Verify liveness before believing the sentinel.
                if await asyncio.to_thread(entry.host.isalive):
                    log.warning(
                        "session %s: output ended while the process is still alive; "
                        "keeping the session and its job open",
                        entry.sid,
                    )
                    entry.output_ended_while_alive = True
                    return
                entry.alive = False
                entry.exit_code = entry.host.exit_status()
                frame = encode_frame(
                    {"t": "pty_exit", "sid": entry.sid, "exit_code": entry.exit_code}
                )
                # A control frame, so a gapped connection gets it too; the reply
                # to its resubscribe carries the bytes before it.
                for connection in tuple(entry.subscribers):
                    with contextlib.suppress(Exception):
                        connection.send_frame(frame)
                return
            entry.scrollback.append(chunk)
            frame = encode_frame({"t": "output", "sid": entry.sid}, chunk)
            start = entry.scrollback.position - len(chunk)
            behind: list[Connection] = []
            # Every subscriber gets the chunk before any is waited on, so a direct
            # connection that has fallen behind delays the next chunk, never this
            # one, and an `output_gap` connection is never waited on at all.
            for connection in tuple(entry.subscribers):
                try:
                    if connection.output_gap:
                        connection.offer_output(entry.sid, frame, start, len(chunk))
                        continue
                    connection.writer.write(frame)
                    transport = connection.writer.transport
                    if (
                        transport is not None
                        and transport.get_write_buffer_size() > WRITE_BUFFER_HIGH_WATER
                    ):
                        behind.append(connection)
                except (ConnectionError, RuntimeError):
                    entry.subscribers.discard(connection)
            for connection in behind:
                try:
                    await connection.writer.drain()
                except (ConnectionError, RuntimeError):
                    entry.subscribers.discard(connection)


def _setup_logging(data_dir: Path) -> IO[str] | None:
    """Console + rotating supervisor.log, and faulthandler into crash.log.

    Deliberately stdlib-only (the supervisor's source closure is hash-gated;
    pulling in daemon-side modules would churn it). The console stream still
    reaches the ``supervisor-console.log`` redirect held by whichever daemon
    spawned this process; the rotating file is the one that cannot grow
    unbounded. Returns the crash-log handle so it stays open for the process
    lifetime.
    """
    log_format = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    with contextlib.suppress(OSError):
        data_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                data_dir / "supervisor.log",
                maxBytes=5 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
                delay=True,
            )
        )
    logging.basicConfig(level=logging.INFO, format=log_format, handlers=handlers)
    crash_log: IO[str] | None = None
    with contextlib.suppress(OSError):
        crash_log = (data_dir / "crash.log").open("a", encoding="utf-8")
        faulthandler.enable(file=crash_log)
    return crash_log


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="swe-mux-supervisor", description="swe-mux PTY supervisor"
    )
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args(argv)
    config_path = args.config
    data_dir = resolve_data_dir(config_path)
    _crash_log = _setup_logging(data_dir)  # noqa: F841 - keeps faulthandler's target open
    # Before any PTY reader starts. This process exists to move terminal bytes, and its
    # reader waits on sub-tick intervals that Windows would otherwise round up to
    # ~15.6 ms — collapsing the poll ladder in `pty_host` into a single value and
    # rounding its 40 ms idle rung up to 46.6 ms. See `timer_resolution`.
    raise_timer_resolution()
    if process_in_job():
        log.warning(
            "supervisor is running inside a Windows Job object (launched from "
            "within a session?); every supervised session dies if that Job closes"
        )
    # A process's cwd locks that directory on Windows. Anchor in the data dir
    # so a supervisor spawned from (or inside) dist/ never blocks an app
    # rebuild, and so a deleted spawn directory cannot break relative resolution.
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        os.chdir(data_dir)
    except OSError:
        log.warning("could not anchor supervisor cwd in %s", data_dir, exc_info=True)
    mutex = SingleInstanceMutex(instance_key(config_path))
    if mutex.already_running:
        log.info("another supervisor already owns this config; exiting")
        mutex.close()
        return
    try:
        asyncio.run(_run(config_path, data_dir))
    finally:
        mutex.close()


async def _run(config_path: Path, data_dir: Path) -> None:
    server = SupervisorServer(config_path, data_dir)
    await server.start()
    await server.run()


if __name__ == "__main__":
    main()
