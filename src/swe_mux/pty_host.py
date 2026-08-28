from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections.abc import Mapping
from concurrent.futures import Future, InvalidStateError
from contextlib import suppress
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
# A read failure the reader swallows and keeps going from is invisible by
# construction: the session stays alive and simply stops producing output (F14).
# Log the first one immediately, then at most one line per interval carrying the
# counts, so a storm is diagnosable without becoming the storm.
_READ_ERROR_LOG_INTERVAL_SECONDS = 30.0
_READ_POLL_ACTIVE_SECONDS = 0.0005
_READ_POLL_RECENT_SECONDS = 0.01
_READ_POLL_DEEP_IDLE_SECONDS = 0.04
_READ_ACTIVE_WINDOW_SECONDS = 0.25
_READ_DEEP_IDLE_AFTER_SECONDS = 5.0


def submit_queue_put(
    queue: asyncio.Queue[bytes],
    loop: asyncio.AbstractEventLoop,
    payload: bytes,
) -> Future[None] | None:
    """Hand ``payload`` to ``queue`` from a non-loop thread, creating no orphan coroutine.

    ``asyncio.run_coroutine_threadsafe`` is the obvious call here and is the wrong
    one, for one structural reason: it takes an *already constructed* coroutine, so
    ``queue.put(payload)`` is built on the calling thread and only then handed over.
    The reader thread that calls this is a daemon thread which outlives the loop it
    feeds, and two teardown orderings leave that coroutine with nobody to await it:

      * the scheduling call raises ``RuntimeError`` because the loop is already
        closed, and the coroutine built for it is dropped by the guard; or
      * the callback is accepted because the loop is not closed *yet*, and the loop
        stops before running it, so the coroutine dies inside an abandoned closure
        without ever being wrapped in a task.

    Python reports either as ``RuntimeWarning: coroutine 'Queue.put' was never
    awaited`` when the collector reaches it, from a finalizer, so it arrives as an
    *unraisable* exception attributed to whatever happens to be running at that
    moment rather than to the session that tore down. Under the suite's
    ``filterwarnings = ["error"]`` that fails an unrelated test. Both orderings need
    a loaded host to lose the race, which is why this first surfaced on 2026-08-27
    on shared CI runners and never on the development host.

    Building the coroutine *inside* the scheduled callback removes the orphan by
    construction: the coroutine exists only on the loop thread, at a moment the loop
    is provably running it, and a callback the loop never reaches drops a closure,
    which needs no finalizer. Returns ``None`` when the loop is already closed -
    there is nothing to deliver and no future worth handing back.

    The returned future keeps ``run_coroutine_threadsafe``'s contract, because both
    call sites depend on all of it: ``result(timeout=...)`` raises ``TimeoutError``
    while the put is still queued, ``cancel()`` from this thread cancels the queued
    put on the loop, and a failed put surfaces as that future's exception.
    """
    handoff: Future[None] = Future()

    def _settle(task: asyncio.Task[None]) -> None:
        # On the loop thread. `cancel()` from the reader thread can land between
        # any two statements here, so every transition is attempted rather than
        # guarded by a check that would immediately be stale.
        with suppress(InvalidStateError):
            if task.cancelled():
                handoff.cancel()
            elif (error := task.exception()) is not None:
                handoff.set_exception(error)
            else:
                handoff.set_result(None)

    def _start() -> None:
        if handoff.cancelled():
            return
        try:
            task = loop.create_task(queue.put(payload))
        except RuntimeError as exc:
            # The loop was closed between accepting this callback and running it.
            with suppress(InvalidStateError):
                handoff.set_exception(exc)
            return

        def _forward_cancel(_: Future[None]) -> None:
            # Fires on whichever thread completed the handoff, so the cancel has
            # to cross back to the loop the way any foreign-thread call does.
            if handoff.cancelled():
                with suppress(RuntimeError):
                    loop.call_soon_threadsafe(task.cancel)

        task.add_done_callback(_settle)
        handoff.add_done_callback(_forward_cancel)

    try:
        loop.call_soon_threadsafe(_start)
    except RuntimeError:
        return None
    return handoff


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
    # Swallowed read-failure accounting (F14). Read by the supervisor's session
    # inventory, so a silent-but-alive session can be told apart from a session
    # whose agent is merely thinking - the daemon cannot see this reader thread.
    # Written only by the reader thread; ints and object references are assigned
    # atomically under the GIL and a torn read could only mis-report one sample.
    read_errors: int = field(default=0, init=False)
    last_read_error: str | None = field(default=None, init=False)
    last_read_error_at: float | None = field(default=None, init=False)
    _read_errors_since_log: int = field(default=0, init=False)
    _read_error_logged_at: float = field(default=0.0, init=False)

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
        pid = pty.pid
        try:
            while not self._stop.is_set():
                try:
                    chunk = pty.read()
                except PtyError as exc:
                    self._note_read_error(exc, pid)
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
                        except PtyError as exc:
                            self._note_read_error(exc, pid)
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
            log.info(
                "pty reader stopped pid=%s stop_requested=%s read_errors=%d",
                pid,
                self._stop.is_set(),
                self.read_errors,
            )
            self._put_end_of_output(pid)

    def _note_read_error(self, exc: BaseException, pid: int) -> None:
        """Record a read failure the reader is about to continue past.

        The reader deliberately keeps going: a transient read error on a live
        pseudoterminal is not a reason to end a session. What it must not do is
        keep going *silently* - that turns a broken reader into a session that is
        alive and permanently quiet, with nothing anywhere saying why.
        """
        self.read_errors += 1
        self._read_errors_since_log += 1
        self.last_read_error = f"{type(exc).__name__}: {exc}"
        self.last_read_error_at = time.time()
        now = time.monotonic()
        if (
            self.read_errors == 1
            or now - self._read_error_logged_at >= _READ_ERROR_LOG_INTERVAL_SECONDS
        ):
            log.warning(
                "pty read failed pid=%s error=%s since_last_report=%d total=%d",
                pid,
                self.last_read_error,
                self._read_errors_since_log,
                self.read_errors,
            )
            self._read_error_logged_at = now
            self._read_errors_since_log = 0

    def _put_end_of_output(self, pid: int) -> None:
        """Deliver the b"" end-of-output sentinel, waiting the way a chunk does.

        This used to give up after two seconds and swallow the failure (F14).
        The sentinel is the *only* signal that a session's output ended: losing
        it under a momentarily full queue leaves a phantom-alive session that
        never emits `pty_exit`, a supervisor that lingers forever because it
        still counts a live session, and a pane that never resolves. A data chunk
        already waits indefinitely for exactly this reason
        (`_put_with_backpressure`); the exit signal must not be the one thing
        that gives up first.

        The single bounded case is a deliberate teardown. `stop()` and
        `release()` set `_stop`, and removing a supervised session cancels its
        fanout - the only consumer - so there provably will never be a reader for
        this put, and waiting on it would park this thread for the life of the
        process. One poll interval is the whole wait there, and it is logged.
        """
        queue, loop = self._queue, self._loop
        if queue is None or loop is None:
            return
        future = submit_queue_put(queue, loop, b"")
        if future is None:
            # The event loop is already closed; there is nobody left to tell.
            return
        waited = 0.0
        while True:
            try:
                future.result(timeout=_QUEUE_PUT_POLL_SECONDS)
                return
            except TimeoutError:
                waited += _QUEUE_PUT_POLL_SECONDS
                if self._stop.is_set():
                    log.warning(
                        "pty %s: end-of-output sentinel undeliverable %.0fs after a "
                        "requested stop (queue full, consumer gone); dropping it",
                        pid,
                        waited,
                    )
                    future.cancel()
                    return
                log.warning(
                    "pty %s: end-of-output sentinel still queued after %.0fs; the "
                    "consumer is not draining, still waiting rather than losing the exit",
                    pid,
                    waited,
                )
            except (RuntimeError, asyncio.CancelledError):
                # The event loop is closing (daemon teardown) - nothing to deliver.
                return

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
        future = submit_queue_put(self._queue, self._loop, payload)
        if future is None:
            # The event loop is already closed (daemon teardown, or a test whose
            # loop went away while this reader was still draining) - nothing can
            # be delivered, so stop reading rather than spin.
            return False
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
