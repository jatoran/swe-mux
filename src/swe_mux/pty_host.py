from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field

import winpty

from .win_jobobj import ReaperJob

log = logging.getLogger(__name__)


def merge_environment(
    base: Mapping[str, str], extra: Mapping[str, str]
) -> dict[str, str]:
    """Merge a Windows environment without duplicate case-insensitive keys.

    Windows treats ``Path`` and ``PATH`` as the same variable, but a raw ConPTY
    environment block can contain both. Which value a child observes is then
    inconsistent, so overrides must replace the original spelling as well.
    """
    overridden = {key.casefold() for key in extra}
    merged = {key: value for key, value in base.items() if key.casefold() not in overridden}
    merged.update(extra)
    return dict(sorted(merged.items(), key=lambda item: item[0].casefold()))


@dataclass
class PtyHost:
    appname: str
    argv: tuple[str, ...] = ()
    cwd: str | None = None
    cols: int = 120
    rows: int = 30
    reaper: ReaperJob | None = None
    env_extra: Mapping[str, str] | None = None
    graceful_exit: str = "exit\r"
    _pty: winpty.PTY | None = field(default=None, init=False)
    _queue: asyncio.Queue[bytes] | None = field(default=None, init=False)
    _loop: asyncio.AbstractEventLoop | None = field(default=None, init=False)
    _stop: threading.Event = field(default_factory=threading.Event, init=False)

    @property
    def pid(self) -> int:
        return self._pty.pid if self._pty else -1

    @property
    def output_queue(self) -> asyncio.Queue[bytes]:
        if self._queue is None:
            raise RuntimeError("PTY has not been spawned")
        return self._queue

    def spawn(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue(maxsize=1024)
        self._pty = winpty.PTY(cols=self.cols, rows=self.rows)
        env = None
        if self.env_extra:
            merged = merge_environment(os.environ, self.env_extra)
            env = "\0".join(f"{k}={v}" for k, v in merged.items()) + "\0\0"
        # ConPTY is the Windows process boundary and therefore owns Windows argv
        # quoting. Backend adapters keep arguments structured and platform-neutral.
        cmdline = subprocess.list2cmdline(self.argv) if self.argv else None
        self._pty.spawn(self.appname, cmdline=cmdline, cwd=self.cwd, env=env)
        if self.reaper:
            try:
                self.reaper.assign(self.pid)
            except OSError as exc:
                log.warning("could not assign pid %s to reaper: %s", self.pid, exc)
        threading.Thread(target=self._read, name=f"mux-pty-{self.pid}", daemon=True).start()

    def _read(self) -> None:
        assert self._pty and self._queue and self._loop
        try:
            while not self._stop.is_set():
                try:
                    chunk = self._pty.read(blocking=False)
                except winpty.WinptyError:
                    chunk = None
                if chunk:
                    data = chunk.encode("utf-8") if isinstance(chunk, str) else chunk
                    future = asyncio.run_coroutine_threadsafe(self._queue.put(data), self._loop)
                    future.result(timeout=5)
                elif not self._pty.isalive():
                    break
                else:
                    time.sleep(0.01)
        finally:
            try:
                asyncio.run_coroutine_threadsafe(self._queue.put(b""), self._loop).result(timeout=2)
            except Exception:
                pass

    def write(self, data: str | bytes) -> None:
        if not self._pty:
            raise RuntimeError("PTY has not been spawned")
        self._pty.write(data.decode("utf-8", "replace") if isinstance(data, bytes) else data)

    def resize(self, cols: int, rows: int) -> None:
        if self._pty:
            cols, rows = max(2, cols), max(1, rows)
            self._pty.set_size(cols, rows)
            self.cols, self.rows = cols, rows

    def isalive(self) -> bool:
        return bool(self._pty and self._pty.isalive())

    def stop(self, *, graceful: bool = True, timeout: float = 2.0) -> None:
        if not self._pty:
            return
        pty, pid = self._pty, self.pid
        if graceful and pty.isalive():
            try:
                pty.write(self.graceful_exit)
            except Exception:
                pass
            deadline = time.monotonic() + timeout
            while pty.isalive() and time.monotonic() < deadline:
                time.sleep(0.05)
        if pty.isalive():
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid), "/T"], capture_output=True, check=False
            )
        self._stop.set()
        self._pty = None
