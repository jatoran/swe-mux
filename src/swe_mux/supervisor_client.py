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
from collections.abc import Mapping
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
        self.pid = -1
        self.reaper_assignment = "supervisor_pending"
        self._graceful_exit = graceful_exit
        self._alive = False
        self._exit_code: int | None = None
        self._first_output_at: float | None = None
        self._queue: asyncio.Queue[bytes] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._released = False

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

    def isalive(self) -> bool:
        return self._alive and self.client.connected

    def exit_status(self) -> int | None:
        if self._alive:
            return None
        return self._exit_code

    def release(self) -> None:
        """Drop the ended supervisor entry; daemon-side scrollback is retained."""
        if self._alive and self.client.connected:
            raise RuntimeError("cannot release a live pseudoconsole")
        if self._released:
            return
        self._released = True
        self.client.unregister_host(self)
        self.client.notify({"t": "remove", "sid": self.sid})

    def stop(self, *, graceful: bool = True, timeout: float = 2.0) -> None:
        """Blocking stop RPC; only ever called via ``asyncio.to_thread``."""
        if self._loop is None or not self.client.connected:
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
            # The b"" sentinel is the same end-of-output contract the local
            # read thread uses; Session._fanout converts it into _mark_ended.
            try:
                self._queue.put_nowait(b"")
            except asyncio.QueueFull:
                pass

    def _receive_output(self, _chunk: bytes) -> None:
        if self._first_output_at is None:
            self._first_output_at = time.perf_counter()


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
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[tuple[dict[str, Any], bytes]]] = {}
        self._meta_pending: dict[str, dict[str, Any]] = {}
        self._meta_sent: dict[str, str] = {}
        self._meta_task: asyncio.Task[None] | None = None
        self._read_task = asyncio.create_task(self._read_loop(), name="supervisor-read")

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
                kind = header.get("t")
                request_id = header.get("id")
                if request_id is not None:
                    future = self._pending.get(int(request_id))
                    if future is not None and not future.done():
                        future.set_result((header, payload))
                    continue
                if kind == "output":
                    host = self.hosts.get(str(header.get("sid")))
                    if host is not None and host._queue is not None:
                        host._receive_output(payload)
                        # Awaiting the put preserves backpressure: a slow daemon
                        # consumer throttles this read loop, which throttles the
                        # supervisor via TCP flow control, which throttles the child.
                        await host._queue.put(payload)
                elif kind == "pty_exit":
                    host = self.hosts.get(str(header.get("sid")))
                    if host is not None:
                        exit_code = header.get("exit_code")
                        host._mark_dead(int(exit_code) if exit_code is not None else None)
        except asyncio.CancelledError:
            raise
        except (asyncio.IncompleteReadError, ConnectionError, OSError, ValueError):
            self._on_connection_lost("connection lost")

    def _on_connection_lost(self, reason: str) -> None:
        if not self.connected:
            return
        self.connected = False
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
                "running unreachable — restart the daemon to reattach",
                reason,
                self.supervisor_pid,
                len(self.hosts),
            )
            return
        log.warning("supervisor %s; marking %d remote session(s) dead", reason, len(self.hosts))
        for host in tuple(self.hosts.values()):
            host._mark_dead(None)

    # -- host lifecycle -----------------------------------------------------------

    def register_host(self, host: RemotePtyHost) -> None:
        self.hosts[host.sid] = host

    def unregister_host(self, host: RemotePtyHost) -> None:
        current = self.hosts.get(host.sid)
        if current is host:
            del self.hosts[host.sid]
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
            }
        )
        host.pid = int(response.get("pid", -1))
        host.reaper_assignment = str(response.get("reaper_assignment", "supervisor_unknown"))
        host._alive = True

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


def _discovery_pid(data_dir: Path) -> int:
    try:
        info = json.loads(discovery_path(data_dir).read_text(encoding="utf-8"))
        return int(info.get("pid", -1))
    except (OSError, ValueError, TypeError):
        return -1


def _terminate_supervisor(pid: int) -> bool:
    """Last-resort reap: closing the supervisor's Job kills every session tree.

    Used only when the protocol handshake cannot be completed (a supervisor from
    a previous build after a PROTOCOL_VERSION bump). Without it the documented
    "force quit everything" action fails and the only remaining recovery is the
    manual taskkill the design says must never be required.
    """
    try:
        import psutil

        process = psutil.Process(pid)
        if "swe" not in (process.name() or "").casefold():
            return False
        process.terminate()
        process.wait(timeout=10)
        return True
    except Exception:
        log.exception("could not terminate supervisor pid %d", pid)
        return False


async def kill_server(config: Config) -> bool:
    """Explicit kill-server: reap every supervised session and stop the supervisor."""
    pid = _discovery_pid(config.data_dir)
    try:
        client = await SupervisorClient.connect(config.data_dir)
    except SupervisorUnavailable as exc:
        if pid <= 0 or not _pid_running(pid):
            return False
        # Reachable-but-unusable is not "absent": a protocol-version mismatch
        # leaves live sessions running that this daemon cannot address.
        log.warning("supervisor pid %d is alive but unusable (%s); terminating it", pid, exc)
        if not await asyncio.to_thread(_terminate_supervisor, pid):
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
    "RemotePtyHost",
    "SupervisorClient",
    "SupervisorUnavailable",
    "host_for_adoption",
    "kill_server",
    "supervisor_command",
]
