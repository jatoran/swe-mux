from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import psutil
import winpty

from .subprocess_flags import background_creation_flags
from .win_jobobj import ReaperJob

log = logging.getLogger(__name__)

# Upper bound on a single coalesced read handoff. Caps per-handoff memory and keeps a
# firehose from starving the drain loop while still collapsing typical bursts into one put.
_MAX_COALESCE_BYTES = 256 * 1024
# How often a blocked cross-thread handoff re-checks whether the child is still
# alive. Not a deadline: a live child keeps its reader waiting indefinitely.
_QUEUE_PUT_POLL_SECONDS = 5.0
# Reader poll cadence, graduated by how recently this PTY did any I/O.
#
# The read is deliberately nonblocking: `pty.read(blocking=True)` parks the thread
# somewhere neither `_stop` nor a dead child can reach it, so the reader polls and the
# only real question is how often. A single fixed interval answers that question badly,
# because the two cases want opposite things. While output is flowing, the interval is
# pure added latency on every chunk. While a session sits at its prompt for minutes, it
# is pure waste, once per session, forever.
#
# So the interval follows the session instead of being guessed once. `_ACTIVE` covers a
# live burst and, more importantly, the keystroke-to-echo round trip, because `write()`
# arms the window too: typing never waits on an idle-tuned timer. `_RECENT` is the old
# fixed 10 ms, kept exactly so nothing that was fast enough before gets slower. `_DEEP`
# only applies after whole seconds of silence, where the sole cost is that unprompted
# output (a build finishing, a first token) can appear up to 40 ms late — invisible,
# and paid once per wake rather than per chunk.
_READ_POLL_ACTIVE_SECONDS = 0.0005
_READ_POLL_RECENT_SECONDS = 0.01
_READ_POLL_DEEP_IDLE_SECONDS = 0.04
_READ_ACTIVE_WINDOW_SECONDS = 0.25
_READ_DEEP_IDLE_AFTER_SECONDS = 5.0


def read_poll_interval(idle_seconds: float) -> float:
    """Seconds to sleep before the next nonblocking read attempt.

    Pure so the ladder is testable without a pseudoconsole: the whole point is the
    boundaries, and those are the thing a future edit would get subtly wrong.
    """
    if idle_seconds < _READ_ACTIVE_WINDOW_SECONDS:
        return _READ_POLL_ACTIVE_SECONDS
    if idle_seconds < _READ_DEEP_IDLE_AFTER_SECONDS:
        return _READ_POLL_RECENT_SECONDS
    return _READ_POLL_DEEP_IDLE_SECONDS
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


def merge_environment(base: Mapping[str, str], extra: Mapping[str, str]) -> dict[str, str]:
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
    # When provided, the child environment is built from this mapping instead of
    # this process's os.environ. The out-of-process supervisor passes {} plus a
    # fully merged env_extra so children see the daemon's environment, not the
    # (potentially stale) supervisor's.
    env_base: Mapping[str, str] | None = None
    graceful_exit: str = "exit\r"
    _pty: winpty.PTY | None = field(default=None, init=False)
    _queue: asyncio.Queue[bytes] | None = field(default=None, init=False)
    _loop: asyncio.AbstractEventLoop | None = field(default=None, init=False)
    _stop: threading.Event = field(default_factory=threading.Event, init=False)
    _first_output_at: float | None = field(default=None, init=False)
    # Monotonic timestamp of the last read or write on this PTY, which is what the
    # reader's poll ladder is measured from. Written by the reader thread and by
    # `write()` on the loop thread; a float assignment is atomic under the GIL and a
    # torn value could only mis-tier one sleep, so it needs no lock.
    _last_io_at: float = field(default_factory=time.monotonic, init=False)
    # Set by `write()` to cut short a poll interval already in flight.
    #
    # Re-tiering the ladder is not enough on its own, and measuring proved it: a
    # keystroke arriving while the reader sits in `time.sleep(deep_idle)` still waits
    # out the remainder, so input into an idle session paid up to a full interval.
    # Measured end to end at 30ms p50 and 40ms max against a 40ms rung, which was
    # *worse* than the fixed 10ms this ladder replaced, in exactly the case the ladder
    # exists to improve. An interruptible wait is what makes arming the window mean
    # anything.
    _io_wake: threading.Event = field(default_factory=threading.Event, init=False)
    _console_host_pid: int | None = field(default=None, init=False)
    _console_host_started_at: float | None = field(default=None, init=False)
    _console_hosts_before: set[int] = field(default_factory=set, init=False)
    _root_started_at: float | None = field(default=None, init=False)
    reaper_assignment: str = field(default="not_attempted", init=False)

    @property
    def pid(self) -> int:
        return self._pty.pid if self._pty else -1

    @property
    def output_queue(self) -> asyncio.Queue[bytes]:
        if self._queue is None:
            raise RuntimeError("PTY has not been spawned")
        return self._queue

    @property
    def first_output_at(self) -> float | None:
        return self._first_output_at

    def prepare(self) -> None:
        """Bind async handoff state to the caller's event loop before blocking spawn work."""
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue(maxsize=1024)
        self._first_output_at = None

    def spawn(self) -> None:
        if self._loop is None or self._queue is None:
            self.prepare()
        # pywinpty launches OpenConsole as a daemon sibling rather than a child of
        # the terminal root. Serialize only allocation so the new helper can be
        # bound unambiguously to this host for deterministic teardown.
        with _PTY_SPAWN_LOCK:
            existing_hosts = _console_host_children()
            self._console_hosts_before = set(existing_hosts)
            self._pty = create_pty(self.cols, self.rows)
            env = None
            if self.env_extra:
                base = os.environ if self.env_base is None else self.env_base
                merged = merge_environment(base, self.env_extra)
                env = "\0".join(f"{k}={v}" for k, v in merged.items()) + "\0\0"
            # ConPTY is the Windows process boundary and therefore owns Windows argv
            # quoting. Backend adapters keep arguments structured and platform-neutral.
            cmdline = subprocess.list2cmdline(self.argv) if self.argv else None
            self._pty.spawn(self.appname, cmdline=cmdline, cwd=self.cwd, env=env)
            try:
                self._root_started_at = float(psutil.Process(self.pid).create_time())
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                self._root_started_at = time.time()
            for _ in range(20):
                if self._bind_console_host(_console_host_children()):
                    break
                time.sleep(0.01)
        if self.reaper:
            try:
                self.reaper.assign(self.pid)
                self.reaper_assignment = "daemon_job_assigned"
            except OSError as exc:
                self.reaper_assignment = f"daemon_job_failed:{exc}"
                log.warning("could not assign pid %s to reaper: %s", self.pid, exc)
        else:
            self.reaper_assignment = "no_daemon_job"
        threading.Thread(target=self._read, name=f"mux-pty-{self.pid}", daemon=True).start()

    def _read(self) -> None:
        assert self._pty and self._queue and self._loop
        # Keep a thread-local owner until all final output has drained. The
        # session can detach ``self._pty`` as soon as the root exits, allowing
        # ConPTY teardown once this reader drops its last reference.
        pty = self._pty
        try:
            while not self._stop.is_set():
                try:
                    chunk = pty.read(blocking=False)
                except winpty.WinptyError:
                    chunk = None
                if chunk:
                    buffer = bytearray(chunk.encode("utf-8") if isinstance(chunk, str) else chunk)
                    # Coalesce any output already waiting in the ConPTY into a single
                    # cross-thread handoff. A burst (build log, large `ls`) is thousands
                    # of tiny reads; without this each pays a full event-loop wakeup plus
                    # a blocking round-trip, capping throughput at loop-wakeup latency.
                    while len(buffer) < _MAX_COALESCE_BYTES:
                        try:
                            more = pty.read(blocking=False)
                        except winpty.WinptyError:
                            break
                        if not more:
                            break
                        buffer += more.encode("utf-8") if isinstance(more, str) else more
                    if self._first_output_at is None:
                        self._first_output_at = time.perf_counter()
                    self._last_io_at = time.monotonic()
                    # A single blocking put per drain preserves backpressure (a slow
                    # consumer throttles the child) while amortizing the round-trip cost.
                    if not self._put_with_backpressure(bytes(buffer), pty):
                        break
                elif not pty.isalive():
                    break
                else:
                    # Interruptible: `write()` sets this, so a keystroke does not wait
                    # out an interval chosen for an idle pseudoconsole. Cleared after the
                    # wait rather than before, so a set that lands mid-wait is consumed
                    # by this iteration instead of spinning the next one.
                    self._io_wake.wait(read_poll_interval(time.monotonic() - self._last_io_at))
                    self._io_wake.clear()
        finally:
            try:
                asyncio.run_coroutine_threadsafe(self._queue.put(b""), self._loop).result(timeout=2)
            except Exception:
                pass

    def _put_with_backpressure(self, payload: bytes, pty: Any) -> bool:
        """Hand a chunk to the loop, waiting as long as the child is alive.

        Timing out here used to abort the reader thread, which injected the b""
        end-of-output sentinel in the finally — fabricating an exit for a session
        that was merely backpressured. The supervisor then treated that sentinel
        as a real exit and closed the session's kill-on-close job, killing a live
        agent tree. Slow consumers must throttle the child, never end it.

        Returns False only when the reader should stop: the PTY is genuinely
        gone, a stop was requested, or the loop itself is unusable.
        """
        assert self._queue is not None and self._loop is not None
        future = asyncio.run_coroutine_threadsafe(self._queue.put(payload), self._loop)
        while True:
            try:
                future.result(timeout=_QUEUE_PUT_POLL_SECONDS)
                return True
            except TimeoutError:
                if self._stop.is_set():
                    future.cancel()
                    return False
                if not pty.isalive():
                    # The child is gone; the pending put may never be consumed,
                    # so stop waiting on it and let the sentinel be written.
                    future.cancel()
                    return False
                # Still alive and still backed up: keep waiting. This is the
                # backpressure the design intends.
                continue
            except (RuntimeError, asyncio.CancelledError):
                # The event loop is closing (daemon teardown) — nothing to deliver.
                return False

    def write(self, data: str | bytes) -> None:
        if not self._pty:
            raise RuntimeError("PTY has not been spawned")
        # Arm the reader's active window before the write, not after. Input is the one
        # case where the *response* latency is a human waiting on their own keystroke,
        # and a session that has been quiet at its prompt is exactly the one whose
        # reader would otherwise be sitting on the deep-idle interval. Both halves are
        # load-bearing: re-tiering decides the *next* interval, and the wake cuts short
        # the one already running. Without the wake this made idle echo slower than the
        # fixed interval it replaced.
        self._last_io_at = time.monotonic()
        self._io_wake.set()
        self._pty.write(data.decode("utf-8", "replace") if isinstance(data, bytes) else data)

    def resize(self, cols: int, rows: int) -> None:
        if self._pty:
            cols, rows = max(2, cols), max(1, rows)
            self._pty.set_size(cols, rows)
            self.cols, self.rows = cols, rows

    def isalive(self) -> bool:
        return bool(self._pty and self._pty.isalive())

    def exit_status(self) -> int | None:
        """Return the ConPTY root exit code after it has stopped, when available."""
        if not self._pty or self._pty.isalive():
            return None
        try:
            status = self._pty.get_exitstatus()
        except (winpty.WinptyError, OSError, TypeError, ValueError):
            return None
        return int(status) if status is not None else None

    def release(self) -> None:
        """Release an ended pseudoconsole without discarding retained scrollback."""
        pty = self._pty
        if pty is not None and pty.isalive():
            raise RuntimeError("cannot release a live pseudoconsole")
        self._stop.set()
        cancel_io = getattr(pty, "cancel_io", None)
        if callable(cancel_io):
            try:
                # Frozen pywinpty can keep a nonblocking reader parked after the
                # root has exited. Wake it so its thread-local PTY reference is
                # released deterministically instead of retaining OpenConsole.
                cancel_io()
            except (winpty.WinptyError, OSError):
                pass
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
                ["taskkill", "/F", "/PID", str(pid), "/T"],
                capture_output=True,
                check=False,
                creationflags=background_creation_flags(),
            )
        self._stop.set()
        cancel_io = getattr(pty, "cancel_io", None)
        if callable(cancel_io):
            try:
                cancel_io()
            except (winpty.WinptyError, OSError):
                pass
        # Explicit stop owns forced teardown; dropping the final host reference
        # closes ConPTY even if taskkill status has not propagated yet.
        self._pty = None
        self._reap_console_host()
