"""ConPTY: pseudoconsole allocation, the OpenConsole helper, and forced teardown.

Everything here was previously inline in `pty_host.py`, where it sat between the
reader loop and the backpressure handoff and made both look platform-specific
when only this part is. Nothing about the behaviour changed in the move; the
console-host binding rules in particular are load-bearing and are reproduced
exactly, including why they exist.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time

import psutil
import winpty

from .pty_backend import PtyError
from .subprocess_flags import background_creation_flags

log = logging.getLogger(__name__)

_CONPTY_CREATE_ATTEMPTS = 3
_CONSOLE_HOST_NAMES = {"conhost", "conhost.exe", "openconsole", "openconsole.exe"}
_PTY_SPAWN_LOCK = threading.Lock()
_CLAIMED_CONSOLE_HOSTS: set[tuple[int, float]] = set()


def _console_host_children() -> dict[int, float]:
    try:
        children = psutil.Process(os.getpid()).children()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return {}
    result: dict[int, float] = {}
    for process in children:
        try:
            if process.name().casefold() in _CONSOLE_HOST_NAMES:
                result[int(process.pid)] = float(process.create_time())
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    return result


def create_pty(cols: int, rows: int) -> winpty.PTY:
    """Create ConPTY with a bounded retry for pywinpty's transient PyO3 panic.

    In a frozen windowed process, the first Windows pseudoconsole allocation can
    fail with ``ERROR_SEM_NOT_FOUND``. pywinpty 3 surfaces that Rust panic as an
    unexported ``pyo3_runtime.PanicException`` derived directly from
    ``BaseException``. Match only that private exception identity; control-flow
    exceptions must never be swallowed.
    """
    last_error: BaseException | None = None
    for attempt in range(_CONPTY_CREATE_ATTEMPTS):
        try:
            return winpty.PTY(cols=cols, rows=rows)
        except BaseException as exc:
            is_pyo3_panic = (
                exc.__class__.__module__ == "pyo3_runtime"
                and exc.__class__.__name__ == "PanicException"
            )
            if not is_pyo3_panic:
                raise
            last_error = exc
            if attempt + 1 < _CONPTY_CREATE_ATTEMPTS:
                time.sleep(0.05 * (attempt + 1))
    raise RuntimeError("Windows could not initialize a pseudoconsole") from last_error


class WindowsPtyProcess:
    """A ConPTY plus the OpenConsole helper pywinpty leaves behind."""

    def __init__(self, cols: int, rows: int) -> None:
        self._cols = cols
        self._rows = rows
        self._pty: winpty.PTY | None = None
        self._console_host_pid: int | None = None
        self._console_host_started_at: float | None = None
        self._console_hosts_before: set[int] = set()
        self._root_started_at: float | None = None

    @property
    def pid(self) -> int:
        return self._pty.pid if self._pty else -1

    def spawn(
        self,
        appname: str,
        argv: tuple[str, ...],
        cwd: str | None,
        env: dict[str, str] | None,
    ) -> None:
        # pywinpty launches OpenConsole as a daemon sibling rather than a child of
        # the terminal root. Serialize only allocation so the new helper can be
        # bound unambiguously to this host for deterministic teardown.
        with _PTY_SPAWN_LOCK:
            existing_hosts = _console_host_children()
            self._console_hosts_before = set(existing_hosts)
            self._pty = create_pty(self._cols, self._rows)
            env_block = None
            if env is not None:
                env_block = "\0".join(f"{k}={v}" for k, v in env.items()) + "\0\0"
            # ConPTY is the Windows process boundary and therefore owns Windows argv
            # quoting. Backend adapters keep arguments structured and platform-neutral.
            cmdline = subprocess.list2cmdline(argv) if argv else None
            self._pty.spawn(appname, cmdline=cmdline, cwd=cwd, env=env_block)
            try:
                self._root_started_at = float(psutil.Process(self.pid).create_time())
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                self._root_started_at = time.time()
            for _ in range(20):
                if self._bind_console_host(_console_host_children()):
                    break
                time.sleep(0.01)

    def read(self) -> bytes | None:
        pty = self._pty
        if pty is None:
            raise PtyError("pseudoconsole is not open")
        try:
            chunk = pty.read(blocking=False)
        except winpty.WinptyError as exc:
            raise PtyError(str(exc)) from exc
        if not chunk:
            return None
        return chunk.encode("utf-8") if isinstance(chunk, str) else bytes(chunk)

    def write(self, data: str) -> None:
        if self._pty is None:
            raise PtyError("pseudoconsole is not open")
        self._pty.write(data)

    def set_size(self, cols: int, rows: int) -> None:
        if self._pty is None:
            return
        self._pty.set_size(cols, rows)
        self._cols, self._rows = cols, rows

    def isalive(self) -> bool:
        return bool(self._pty and self._pty.isalive())

    def exit_status(self) -> int | None:
        if not self._pty or self._pty.isalive():
            return None
        try:
            status = self._pty.get_exitstatus()
        except (winpty.WinptyError, OSError, TypeError, ValueError):
            return None
        return int(status) if status is not None else None

    def interrupt_read(self) -> None:
        # Frozen pywinpty can keep a nonblocking reader parked after the root has
        # exited. Wake it so its thread-local PTY reference is released
        # deterministically instead of retaining OpenConsole.
        cancel_io = getattr(self._pty, "cancel_io", None)
        if callable(cancel_io):
            try:
                cancel_io()
            except (winpty.WinptyError, OSError):
                pass

    def force_kill(self) -> None:
        pid = self.pid
        if pid <= 0:
            return
        subprocess.run(
            ["taskkill", "/F", "/PID", str(pid), "/T"],
            capture_output=True,
            check=False,
            creationflags=background_creation_flags(),
        )

    def close(self) -> None:
        # Dropping the final host reference is what closes ConPTY; the console
        # host is reaped separately because pywinpty does not own it.
        self._pty = None
        self._reap_console_host()

    def _reap_console_host(self) -> None:
        pid, started_at = self._console_host_pid, self._console_host_started_at
        self._console_host_pid = None
        self._console_host_started_at = None
        if pid is not None and started_at is not None:
            if self._kill_console_host(pid, started_at):
                return
        # Frozen builds can replace the initially observed OpenConsole process
        # with conhost after PTY.spawn returns. Resolve that delayed helper by
        # creation time at teardown, when it is guaranteed to be present.
        with _PTY_SPAWN_LOCK:
            self._bind_console_host(_console_host_children())
        pid, started_at = self._console_host_pid, self._console_host_started_at
        self._console_host_pid = None
        self._console_host_started_at = None
        if pid is None or started_at is None:
            return
        self._kill_console_host(pid, started_at)

    @staticmethod
    def _kill_console_host(pid: int, started_at: float) -> bool:
        try:
            process = psutil.Process(pid)
            if (
                abs(float(process.create_time()) - started_at) > 0.01
                or process.name().casefold() not in _CONSOLE_HOST_NAMES
                or int(process.ppid()) != os.getpid()
            ):
                return False
            process.kill()
            return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            return False
        finally:
            _CLAIMED_CONSOLE_HOSTS.discard((pid, started_at))

    def _bind_console_host(self, hosts: dict[int, float]) -> bool:
        root_started_at = self._root_started_at
        if root_started_at is None:
            return False
        candidates = {
            pid: started_at
            for pid, started_at in hosts.items()
            if pid not in self._console_hosts_before
            and (pid, started_at) not in _CLAIMED_CONSOLE_HOSTS
        }
        if not candidates:
            return False
        pid, started_at = min(
            candidates.items(), key=lambda item: abs(item[1] - root_started_at)
        )
        if abs(started_at - root_started_at) > 5:
            return False
        self._console_host_pid = pid
        self._console_host_started_at = started_at
        _CLAIMED_CONSOLE_HOSTS.add((pid, started_at))
        return True


__all__ = ["WindowsPtyProcess", "create_pty"]
