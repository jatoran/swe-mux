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
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import ctypes
import hashlib
import json
import logging
import os
import secrets
import struct
import sys
import time
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .pty_host import PtyHost
from .scrollback import ScrollbackBuffer
from .win_jobobj import ReaperJob

log = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
DISCOVERY_FILENAME = "supervisor.json"
MAX_HEADER_BYTES = 4 * 1024 * 1024
# Backpressure threshold per client connection before the fanout awaits drain.
WRITE_BUFFER_HIGH_WATER = 2 * 1024 * 1024
# With no attached daemon and no live sessions there is nothing to preserve;
# self-exit keeps a forgotten supervisor from lingering forever (tmux-style).
LINGER_POLL_SECONDS = 30.0
LINGER_IDLE_EXIT_SECONDS = 15 * 60.0
ERROR_ALREADY_EXISTS = 183


def encode_frame(header: dict[str, Any], payload: bytes = b"") -> bytes:
    if payload:
        header = {**header, "plen": len(payload)}
    raw = json.dumps(header, separators=(",", ":")).encode("utf-8")
    return struct.pack(">I", len(raw)) + raw + payload


async def read_frame(reader: asyncio.StreamReader) -> tuple[dict[str, Any], bytes]:
    (size,) = struct.unpack(">I", await reader.readexactly(4))
    if size > MAX_HEADER_BYTES:
        raise ValueError(f"oversized frame header: {size} bytes")
    header = json.loads(await reader.readexactly(size))
    if not isinstance(header, dict):
        raise ValueError("frame header must be a JSON object")
    plen = int(header.get("plen", 0))
    payload = await reader.readexactly(plen) if plen else b""
    return header, payload


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
    ownership_job: ReaperJob | None = None
    subscribers: set[Connection] = field(default_factory=set)
    alive: bool = True
    exit_code: int | None = None
    fanout_task: asyncio.Task[None] | None = None


@dataclass(eq=False)
class Connection:
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    authenticated: bool = False

    def send(self, header: dict[str, Any], payload: bytes = b"") -> None:
        self.writer.write(encode_frame(header, payload))


class SupervisorServer:
    def __init__(self, config_path: Path, data_dir: Path) -> None:
        self.config_path = config_path
        self.data_dir = data_dir
        self.token = secrets.token_urlsafe(32)
        self.reaper = ReaperJob()
        self.sessions: dict[str, SupervisedSession] = {}
        self.connections: set[Connection] = set()
        self.exit_event = asyncio.Event()
        self._server: asyncio.Server | None = None
        self._last_busy = time.monotonic()
        self._background_tasks: set[asyncio.Task[None]] = set()

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

    async def _teardown(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        for entry in self.sessions.values():
            if entry.fanout_task and not entry.fanout_task.done():
                entry.fanout_task.cancel()
            if entry.ownership_job:
                entry.ownership_job.close()
        for connection in tuple(self.connections):
            with contextlib.suppress(Exception):
                connection.writer.close()
        self._remove_discovery()
        # Closing the Job handle is the reap: kill-on-close terminates every
        # process tree that was assigned to it.
        self.reaper.close()

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
        connection = Connection(reader, writer)
        self.connections.add(connection)
        self._last_busy = time.monotonic()
        try:
            while not self.exit_event.is_set():
                try:
                    header, payload = await read_frame(reader)
                except (asyncio.IncompleteReadError, ConnectionError):
                    break
                try:
                    await self._dispatch(connection, header, payload)
                except PermissionError as exc:
                    if header.get("id") is not None:
                        connection.send({"id": header["id"], "ok": False, "error": str(exc)})
                    with contextlib.suppress(ConnectionError):
                        await writer.drain()
                    break
                except Exception as exc:
                    request_id = header.get("id")
                    log.exception("failed handling %s", header.get("t"))
                    if request_id is not None:
                        connection.send({"id": request_id, "ok": False, "error": str(exc)})
                with contextlib.suppress(ConnectionError):
                    await writer.drain()
        finally:
            self.connections.discard(connection)
            for entry in self.sessions.values():
                entry.subscribers.discard(connection)
            with contextlib.suppress(Exception):
                writer.close()

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
        if kind == "spawn":
            # Reserve and validate synchronously; run the blocking ConPTY spawn
            # off the read loop so one slow spawn cannot stall other sessions'
            # input frames on this connection.
            entry = self._reserve_spawn(connection, header)
            self._spawn_background(
                self._finish_spawn(connection, entry, request_id), connection, request_id
            )
        elif kind == "write":
            self._require(header).host.write(payload)
        elif kind == "resize":
            self._require(header).host.resize(int(header["cols"]), int(header["rows"]))
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
            # Snapshot and subscription registration are synchronous, so the
            # fanout task cannot interleave a chunk between them: the client
            # neither misses nor duplicates the boundary chunk (the same
            # contract as Session.replay_and_subscribe, one level down).
            entry.subscribers.add(connection)
            connection.send(
                {
                    "id": request_id,
                    "ok": True,
                    "alive": entry.alive,
                    "exit_code": entry.exit_code,
                    "position": entry.scrollback.position,
                    "meta": entry.meta,
                },
                entry.scrollback.bytes(),
            )
        elif kind == "unsubscribe":
            existing = self.sessions.get(str(header.get("sid")))
            if existing is not None:
                existing.subscribers.discard(connection)
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
            connection.send({"id": request_id, "ok": True, "sessions": self._session_infos()})
        elif kind == "ping":
            if request_id is not None:
                connection.send({"id": request_id, "ok": True})
        elif kind == "reap_all_and_exit":
            log.info("reap_all_and_exit requested; terminating %d session(s)", len(self.sessions))
            if request_id is not None:
                connection.send({"id": request_id, "ok": True})
                with contextlib.suppress(ConnectionError):
                    await connection.writer.drain()
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
        connection.authenticated = True
        connection.send(
            {
                "id": request_id,
                "ok": True,
                "protocol": PROTOCOL_VERSION,
                "pid": os.getpid(),
                "sessions": self._session_infos(),
            }
        )

    def _session_infos(self) -> list[dict[str, Any]]:
        return [
            {
                "sid": entry.sid,
                "alive": entry.alive,
                "exit_code": entry.exit_code,
                "pid": entry.host.pid,
                "cols": entry.host.cols,
                "rows": entry.host.rows,
                "reaper_assignment": entry.host.reaper_assignment,
                "meta": entry.meta,
            }
            for entry in self.sessions.values()
        ]

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

    def _reserve_spawn(self, connection: Connection, header: dict[str, Any]) -> SupervisedSession:
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
        except BaseException:
            self.sessions.pop(entry.sid, None)
            raise
        ownership_job: ReaperJob | None = None
        try:
            ownership_job = self.reaper.create_child()
            ownership_job.assign(host.pid)
            host.reaper_assignment += ";nested_session_job_assigned"
        except OSError as exc:
            host.reaper_assignment += f";nested_session_job_failed:{exc}"
            if ownership_job:
                ownership_job.close()
            ownership_job = None
        entry.ownership_job = ownership_job
        entry.fanout_task = asyncio.create_task(
            self._fanout(entry), name=f"sup-fanout-{entry.sid}"
        )
        connection.send(
            {
                "id": request_id,
                "ok": True,
                "pid": host.pid,
                "reaper_assignment": host.reaper_assignment,
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
            if entry.fanout_task and not entry.fanout_task.done():
                entry.fanout_task.cancel()
                await asyncio.gather(entry.fanout_task, return_exceptions=True)
            if entry.alive:
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
                entry.alive = False
                entry.exit_code = entry.host.exit_status()
                frame = encode_frame(
                    {"t": "pty_exit", "sid": entry.sid, "exit_code": entry.exit_code}
                )
                for connection in tuple(entry.subscribers):
                    with contextlib.suppress(Exception):
                        connection.writer.write(frame)
                return
            entry.scrollback.append(chunk)
            frame = encode_frame({"t": "output", "sid": entry.sid}, chunk)
            for connection in tuple(entry.subscribers):
                try:
                    connection.writer.write(frame)
                    transport = connection.writer.transport
                    if (
                        transport is not None
                        and transport.get_write_buffer_size() > WRITE_BUFFER_HIGH_WATER
                    ):
                        await connection.writer.drain()
                except (ConnectionError, RuntimeError):
                    entry.subscribers.discard(connection)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="swe-mux-supervisor", description="swe-mux PTY supervisor"
    )
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config_path = args.config
    data_dir = resolve_data_dir(config_path)
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
