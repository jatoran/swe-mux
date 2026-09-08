"""Desktop-side recovery, independent of the daemon's event loop and GIL.

The durable record names one process generation, not a reusable PID. A shared
OS lock fences termination against intentional shutdown and in-process PTY
creation. Once a generation attempts a local PTY, forced recovery is revoked
for its lifetime. The PTY supervisor is only queried, never changed or stopped.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import socket
import struct
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psutil

from .bundle_locks import REDEPLOY_LOCK_NAME, live_redeploy_lock_pid
from .lifecycle import ledger

RECORD_NAME = "daemon-recovery.json"
LOCK_NAME = "daemon-recovery.lock"
POLL_SECONDS = 2.0
DEAD_GRACE_SECONDS = 4.0
HANG_SECONDS = 45.0
HANDOFF_SECONDS = 300.0
RETRY_WINDOW_SECONDS = 600.0
MAX_RESTARTS = 3


@contextmanager
def recovery_lock(data_dir: Path, *, timeout: float = 5.0) -> Iterator[None]:
    """A kernel-owned lock: a crash releases it; the file is never deleted."""
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / LOCK_NAME).open("a+b") as handle:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + timeout
        # unsupervised-loop-ok: bounded OS-lock acquisition, never a daemon service loop.
        while True:
            handle.seek(0)
            try:
                if sys.platform == "win32":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise TimeoutError("daemon recovery lock busy") from exc
                time.sleep(0.025)
        try:
            yield
        finally:
            handle.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def read_record(data_dir: Path) -> dict[str, Any]:
    try:
        with (data_dir / RECORD_NAME).open("rb") as handle:
            raw = json.loads(handle.read(16 * 1024))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_record(data_dir: Path, record: dict[str, Any]) -> None:
    temporary = data_dir / (RECORD_NAME + ".tmp")
    temporary.write_text(json.dumps(record), encoding="utf-8")
    os.replace(temporary, data_dir / RECORD_NAME)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def register_daemon(data_dir: Path, token: str | None) -> None:
    with recovery_lock(data_dir):
        _write_record(
            data_dir,
            {
                "pid": os.getpid(),
                "created_at": psutil.Process().create_time(),
                "owner": token_digest(token) if token else None,
                "ready": False,
                "local_pty": False,
                "supervisor": None,
                "intent": None,
                "intent_at": None,
            },
        )


def update_daemon(data_dir: Path, **fields: Any) -> None:
    with recovery_lock(data_dir):
        record = read_record(data_dir)
        if record.get("pid") == os.getpid():
            record.update(fields)
            _write_record(data_dir, record)


def revoke_for_local_pty(data_dir: Path) -> None:
    # Must succeed BEFORE starting the local PTY. Failing closed costs one
    # degraded spawn, whereas ignoring a failed revocation could kill sessions.
    update_daemon(data_dir, local_pty=True)


def process_state(record: dict[str, Any]) -> str:
    """alive | gone | unknown; permission failures never authorize a kill."""
    pid, created = record.get("pid"), record.get("created_at")
    if (
        type(pid) is not int
        or pid <= 0
        or not isinstance(created, (int, float))
        or not math.isfinite(created)
        or created <= 0
    ):
        return "unknown"
    try:
        process = psutil.Process(pid)
        return "alive" if abs(process.create_time() - created) < 0.001 else "gone"
    except psutil.NoSuchProcess:
        return "gone"
    except (psutil.Error, OSError):
        return "unknown"


def supervisor_identity(data_dir: Path) -> dict[str, Any] | None:
    """Bounded, read-only supervisor handshake. Never spawn or attach a PTY."""
    try:
        info = json.loads((data_dir / "supervisor.json").read_text(encoding="utf-8"))
        identity = {
            "pid": int(info["pid"]),
            "created_at": psutil.Process(int(info["pid"])).create_time(),
        }
        header = json.dumps(
            {"t": "hello", "id": 0, "token": info["token"], "protocol": info["protocol"]}
        ).encode()
        with socket.create_connection(("127.0.0.1", int(info["port"])), timeout=1.0) as sock:
            deadline = time.monotonic() + 1.0

            def read_exact(size: int) -> bytes:
                result = bytearray()
                while len(result) < size:
                    sock.settimeout(max(0.001, deadline - time.monotonic()))
                    chunk = sock.recv(size - len(result))
                    if not chunk or time.monotonic() > deadline:
                        raise OSError("supervisor handshake incomplete")
                    result.extend(chunk)
                return bytes(result)

            sock.sendall(struct.pack(">I", len(header)) + header)
            size = struct.unpack(">I", read_exact(4))[0]
            if size > 4 * 1024 * 1024:  # hello includes the bounded session inventory
                return None
            response = json.loads(read_exact(size))
            if (
                isinstance(response, dict)
                and response.get("ok") is True
                and response.get("id") == 0
                and response.get("pid") == identity["pid"]
            ):
                return identity if process_state(identity) == "alive" else None
    except (OSError, ValueError, KeyError, TypeError, psutil.Error):
        pass
    return None


def mark_ready(data_dir: Path, supervisor_pid: int | None) -> None:
    # Capture outside the lock; update_daemon preserves a local-spawn revocation.
    identity = (
        supervisor_identity(data_dir)
        if supervisor_pid and read_record(data_dir).get("owner")
        else None
    )
    if identity is not None and identity["pid"] != supervisor_pid:
        identity = None
    update_daemon(data_dir, ready=True, supervisor=identity)


def terminate_generation(record: dict[str, Any]) -> bool:
    """Terminate just the fenced daemon; psutil rechecks PID reuse on terminate."""
    if process_state(record) != "alive":
        return process_state(record) == "gone"
    process = psutil.Process(record["pid"])
    if abs(process.create_time() - record["created_at"]) >= 0.001:
        return False
    process.terminate()
    try:
        process.wait(timeout=3.0)
        return True
    except psutil.TimeoutExpired:
        return False


class DaemonRecovery:
    """One desktop-owned monitor, following successors via the durable record."""

    def __init__(
        self,
        data_dir: Path,
        token: str,
        *,
        health: Callable[[], bool],
        spawn: Callable[[], dict[str, Any]],
        stop: threading.Event,
        pause: threading.Event | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.data_dir = data_dir
        self.owner = token_digest(token)
        self.health = health
        self.spawn = spawn
        self.stop = stop
        self.pause = pause or threading.Event()
        self.clock = monotonic
        self._generation: tuple[Any, Any] | None = None
        self._missed_since: float | None = None
        self._attempts: deque[float] = deque()
        self._last_state: tuple[str, Any] | None = None
        self._pending: dict[str, Any] | None = None

    def _state(self, state: str, pid: Any = None) -> None:
        if self._last_state != (state, pid):
            ledger(self.data_dir, f"daemon_recovery state={state} pid={pid}")
            self._last_state = state, pid

    def _redeploying(self) -> bool:
        path = self.data_dir / REDEPLOY_LOCK_NAME
        if live_redeploy_lock_pid(path) is not None:
            return True
        # The launcher first creates an empty claim, then writes its child PID.
        try:
            return path.stat().st_size == 0 and time.time() - path.stat().st_mtime < 30
        except OSError:
            return False

    def step(self) -> None:
        if self.stop.is_set() or self.pause.is_set():
            return
        now = self.clock()
        if self.health():
            self._missed_since = None
            self._pending = None
            self._state("healthy")
            return
        with recovery_lock(self.data_dir, timeout=0):
            record = read_record(self.data_dir)
            if not record or record.get("owner") != self.owner:
                self._state("unmanaged")
                return
            generation = record.get("pid"), record.get("created_at")
            if self._pending is not None:
                pending_generation = self._pending.get("pid"), self._pending.get("created_at")
                if generation == pending_generation or process_state(self._pending) == "gone":
                    self._pending = None
                else:
                    self._state("replacement_starting", self._pending.get("pid"))
                    return
            if generation != self._generation:
                self._generation = generation
                self._missed_since = None
            if self._missed_since is None:
                self._missed_since = now
            elapsed = now - self._missed_since
            pid = record.get("pid")
            intent = record.get("intent")
            if self._redeploying() or intent == "quit" or self.stop.is_set():
                self._state("intentional_stop", pid)
                self._missed_since = None
                return
            if intent in ("detach", "restart") and elapsed < HANDOFF_SECONDS:
                self._state("handoff", pid)
                return
            state = process_state(record)
            if state == "unknown":
                self._state("identity_unavailable", pid)
                return
            if state == "alive" and record.get("ready") is not True:
                self._state("starting", pid)
                return  # Slow startup/maintenance is never force-terminated.
            threshold = HANG_SECONDS if state == "alive" else DEAD_GRACE_SECONDS
            if elapsed < threshold:
                self._state("unresponsive" if state == "alive" else "exited", pid)
                return
            while self._attempts and now - self._attempts[0] >= RETRY_WINDOW_SECONDS:
                self._attempts.popleft()
            if len(self._attempts) >= MAX_RESTARTS:
                self._state("retry_limit", pid)
                return
            if state == "alive" and (
                record.get("local_pty") is not False
                or not record.get("supervisor")
                or record["supervisor"].get("pid") == pid
                or supervisor_identity(self.data_dir) != record["supervisor"]
            ):
                self._state("sessions_not_protected", pid)
                return
            # Recheck after the supervisor RPC and before committing a recovery.
            # The shared lock excludes local PTY creation and planned handoffs.
            if self.stop.is_set() or self.pause.is_set() or self._redeploying() or self.health():
                return
            self._attempts.append(now)
            self._state("restarting_hung" if state == "alive" else "restarting_dead", pid)
            if state == "alive" and not terminate_generation(record):
                self._state("termination_pending", pid)
                return
            if self.stop.is_set() or self.pause.is_set() or self._redeploying():
                return
            self._pending = self.spawn()
            self._missed_since = now
            self._state("replacement_spawned", pid)

    def run(self) -> None:
        self._state("monitoring")
        while not self.stop.wait(POLL_SECONDS):
            try:
                self.step()
            except TimeoutError:
                continue  # Another lifecycle operation owns the fence.
            except Exception as exc:
                ledger(
                    self.data_dir,
                    f"daemon_recovery state=error error={type(exc).__name__}: {exc}",
                )
        self._state("stopped")
