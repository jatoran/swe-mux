from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field

from .host_platform import IS_WINDOWS
from .process_reaper import ProcessReaper
from .pty_backend import PtyError, PtyProcess, open_pty

log = logging.getLogger(__name__)

# Upper bound on a single coalesced read handoff. Caps per-handoff memory and keeps a
# firehose from starving the drain loop while still collapsing typical bursts into one put.
_MAX_COALESCE_BYTES = 256 * 1024
# How often a blocked cross-thread handoff re-checks whether the child is still
# alive. Not a deadline: a live child keeps its reader waiting indefinitely.
_QUEUE_PUT_POLL_SECONDS = 5.0
# Reader poll cadence, graduated by how recently this PTY did any I/O.
#
# The read is deliberately nonblocking: a blocking read parks the thread
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

    Pure so the ladder is testable without a pseudoterminal: the whole point is the
    boundaries, and those are the thing a future edit would get subtly wrong.
    """
    if idle_seconds < _READ_ACTIVE_WINDOW_SECONDS:
        return _READ_POLL_ACTIVE_SECONDS
    if idle_seconds < _READ_DEEP_IDLE_AFTER_SECONDS:
        return _READ_POLL_RECENT_SECONDS
    return _READ_POLL_DEEP_IDLE_SECONDS


def merge_environment(base: Mapping[str, str], extra: Mapping[str, str]) -> dict[str, str]:
    """Merge a child environment, collapsing duplicate keys the way the host would.

    Windows treats ``Path`` and ``PATH`` as the same variable, but a raw ConPTY
    environment block can contain both. Which value a child observes is then
    inconsistent, so overrides must replace the original spelling as well.

    POSIX environment keys are case-*sensitive*: ``Path`` and ``PATH`` are two
    different variables there, and folding them would silently drop one. So the
    collapse is applied only on the host whose rule it is. The sort is kept on both
    for a stable, diffable block.
    """
    if not IS_WINDOWS:
        merged_posix = {**base, **extra}
        return dict(sorted(merged_posix.items()))
    overridden = {key.casefold() for key in extra}
    merged = {key: value for key, value in base.items() if key.casefold() not in overridden}
    merged.update(extra)
    return dict(sorted(merged.items(), key=lambda item: item[0].casefold()))


@dataclass
class PtyHost:
    """One pseudoterminal session: platform-neutral buffering over a `PtyProcess`.

    Everything below the reader thread is delegated to the platform backend
    (`pty_backend.open_pty`); everything at and above it - coalescing, the
    backpressure handoff, the poll ladder, the resize and exit-status contracts -
    is shared and exists once.
    """

    appname: str
    argv: tuple[str, ...] = ()
    cwd: str | None = None
    cols: int = 120
    rows: int = 30
    reaper: ProcessReaper | None = None
    env_extra: Mapping[str, str] | None = None
    # When provided, the child environment is built from this mapping instead of
    # this process's os.environ. The out-of-process supervisor passes {} plus a
    # fully merged env_extra so children see the daemon's environment, not the
    # (potentially stale) supervisor's.
    env_base: Mapping[str, str] | None = None
    graceful_exit: str = "exit\r"
    _pty: PtyProcess | None = field(default=None, init=False)
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
        pty = open_pty(self.cols, self.rows)
        env: dict[str, str] | None = None
        if self.env_extra:
            base = os.environ if self.env_base is None else self.env_base
            env = merge_environment(base, self.env_extra)
        pty.spawn(self.appname, tuple(self.argv), self.cwd, env)
        self._pty = pty
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
        # teardown once this reader drops its last reference.
        pty = self._pty
        try:
            while not self._stop.is_set():
                try:
                    chunk = pty.read()
                except PtyError:
                    chunk = None
                if chunk:
                    buffer = bytearray(chunk)
                    # Coalesce any output already waiting in the pseudoterminal into a
                    # single cross-thread handoff. A burst (build log, large `ls`) is
                    # thousands of tiny reads; without this each pays a full event-loop
                    # wakeup plus a blocking round-trip, capping throughput at
                    # loop-wakeup latency.
                    while len(buffer) < _MAX_COALESCE_BYTES:
                        try:
                            more = pty.read()
                        except PtyError:
                            break
                        if not more:
                            break
                        buffer += more
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
                    # out an interval chosen for an idle pseudoterminal. Cleared after the
                    # wait rather than before, so a set that lands mid-wait is consumed
                    # by this iteration instead of spinning the next one.
                    self._io_wake.wait(read_poll_interval(time.monotonic() - self._last_io_at))
                    self._io_wake.clear()
        finally:
            try:
                asyncio.run_coroutine_threadsafe(self._queue.put(b""), self._loop).result(timeout=2)
            except Exception:
                pass

    def _put_with_backpressure(self, payload: bytes, pty: PtyProcess) -> bool:
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
        """Return the root exit code after it has stopped, when available."""
        if not self._pty:
            return None
        return self._pty.exit_status()

    def release(self) -> None:
        """Release an ended pseudoterminal without discarding retained scrollback."""
        pty = self._pty
        if pty is not None and pty.isalive():
            raise RuntimeError("cannot release a live pseudoterminal")
        self._stop.set()
        if pty is not None:
            pty.interrupt_read()
            pty.close()
        self._pty = None

    def stop(self, *, graceful: bool = True, timeout: float = 2.0) -> None:
        if not self._pty:
            return
        pty = self._pty
        if graceful and pty.isalive():
            try:
                pty.write(self.graceful_exit)
            except Exception:
                pass
            deadline = time.monotonic() + timeout
            while pty.isalive() and time.monotonic() < deadline:
                time.sleep(0.05)
        if pty.isalive():
            pty.force_kill()
        self._stop.set()
        pty.interrupt_read()
        # Explicit stop owns forced teardown; dropping the final host reference
        # closes the pseudoterminal even if kill status has not propagated yet.
        self._pty = None
        pty.close()
