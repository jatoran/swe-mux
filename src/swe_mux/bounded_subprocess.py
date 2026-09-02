"""One bounded runner for every daemon-owned one-shot subprocess.

The daemon shells out in a dozen places - `ccusage`, provider login and status,
read-only Git queries, repository-declared worktree commands - and each of those
call sites needs the same four things: a timeout, a cap on how much output it is
willing to hold in memory, a *tree* reap so a Windows `cmd.exe` wrapper cannot
leave its real workers behind, and enough of a log line to correlate a slow or
truncated run with the operation that asked for it.

The pattern here is `worktree_exec.py`'s, which had all four; the other call sites
had one to three each. What they were missing, exactly:

  * **Buffer-then-check is not a cap.** `usage.py` read `ccusage` to completion
    through `communicate()` and only then compared the result against its 10 MiB
    limit, so the limit described the error message rather than the memory. Reading
    in chunks and keeping only the head and tail is what makes the number true.
  * **No cap at all** on `provider_accounts.py` and `git_monitor.py`, both of which
    run programs whose output size is not the daemon's to decide.
  * **No reap on `CancelledError`.** All three reaped on timeout - the audit's
    claim that they leaked there was wrong - but every one of them runs inside a
    supervised background loop, and a loop cancelled at shutdown left its child
    alive with the pipes open.

Two deliberate non-features. Nothing here interprets an exit status: a caller that
wants to treat a nonzero code as a failure says so, because "the command did not
run" and "the command said no" are the two answers that must never be conflated.
And nothing here decodes: `git cat-file blob` needs the bytes git stores, and
`errors="replace"` would silently digest a repaired string instead.
"""

from __future__ import annotations

import asyncio
import atexit
import contextvars
import logging
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .subprocess_flags import background_creation_flags, reap_process_tree

log = logging.getLogger(__name__)

#: The thread the spawn loop runs on. Named so a stall dump reads as what it is.
SPAWN_THREAD_NAME = "subprocess-spawn"
#: Lanes: one loop for pollers, one for commands a person is waiting on. A spawn
#: that the kernel holds for twenty seconds blocks every other spawn queued on the
#: same loop, and a Land or a commit must not sit behind a git monitor sweep.
LANE_BACKGROUND = "background"
LANE_INTERACTIVE = "interactive"
LANES: tuple[str, ...] = (LANE_BACKGROUND, LANE_INTERACTIVE)
#: How long shutdown waits for the spawn loop to stop before abandoning it.
SPAWN_LOOP_STOP_SECONDS = 5.0

#: How long a drain may take *after* the tree has been reaped. The pipes close as
#: the tree dies, so this is milliseconds in every ordinary case; it exists because
#: a descendant that escaped the reap and still holds the write end would otherwise
#: hang the caller forever, and a daemon that hangs is worse than one that reports
#: a partial capture.
DRAIN_GRACE_SECONDS = 5.0

#: One line per (label, kind) per window, with the suppressed count carried into
#: the next one. A 4s Git query on a 5s poll would otherwise write 720 identical
#: warnings an hour, which is the shape of log spam this repository has already
#: had to clean up once (`worktree_graveyard_purge_failed`, 1,165 lines).
LOG_WINDOW_SECONDS = 60.0

_READ_CHUNK = 64 * 1024

_log_windows: dict[tuple[str, str], tuple[float, int]] = {}


def _rate_limited(kind: str, label: str, message: str, *args: object) -> None:
    """Warn at most once per window per (label, kind), counting what was dropped."""
    key = (label, kind)
    now = time.monotonic()
    opens_at, suppressed = _log_windows.get(key, (0.0, 0))
    if now < opens_at:
        _log_windows[key] = (opens_at, suppressed + 1)
        return
    _log_windows[key] = (now + LOG_WINDOW_SECONDS, 0)
    if suppressed:
        log.warning(message + " suppressed_since_last=%d", *args, suppressed)
    else:
        log.warning(message, *args)


def reset_log_windows() -> None:
    """Drop the rate-limiter state. For tests, and for a deliberate re-report."""
    _log_windows.clear()


@dataclass(frozen=True, slots=True)
class ProcessOutcome:
    """What one bounded run produced, with no interpretation of its meaning.

    `exit_code` is `None` only when no exit status exists to report, which today
    means the run timed out. A caller must never read that as a zero.

    Truncation is reported per stream rather than folded into the bytes, because a
    caller that parses its output (`git diff --numstat`) has to be able to refuse a
    capture that lost its middle, while one that only shows the tail to a human
    does not.
    """

    exit_code: int | None
    stdout: bytes
    stderr: bytes
    stdout_truncated: bool
    stderr_truncated: bool
    duration_ms: float
    timed_out: bool = False

    @property
    def truncated(self) -> bool:
        return self.stdout_truncated or self.stderr_truncated


async def bounded_read(
    stream: asyncio.StreamReader,
    limit: int,
    *,
    label: str,
    on_chunk: Callable[[bytes], None] | None = None,
) -> tuple[bytes, bool]:
    """Read a stream to EOF, retaining its head and tail within `limit` bytes.

    Both ends are kept because they answer different questions: the head carries a
    command's banner and its first error, the tail carries how it ended. A cap that
    kept only one of them would have to be argued for per call site.

    `on_chunk` observes the bytes as they arrive and changes nothing about them:
    this is the only place a caller can watch a long command make progress, because
    everything downstream sees the capture once, after the process has already
    exited. An observer that raises is a bug in the observer, so it is contained
    here rather than allowed to abandon the pipe mid-read - a half-read pipe would
    block the process it is draining.
    """
    half = max(limit // 2, 0)
    prefix = bytearray()
    tail = bytearray()
    total = 0
    while chunk := await stream.read(_READ_CHUNK):
        if on_chunk is not None:
            try:
                on_chunk(chunk)
            except Exception:  # noqa: BLE001 - watching output must not stop reading it
                log.debug("bounded_command_observer_failed label=%s", label)
        total += len(chunk)
        if len(prefix) < half:
            take = min(half - len(prefix), len(chunk))
            prefix.extend(chunk[:take])
            chunk = chunk[take:]
        if chunk:
            tail.extend(chunk)
            if len(tail) > half:
                del tail[: len(tail) - half]
    truncated = total > limit
    if not truncated:
        return bytes(prefix + tail), False
    omitted = f"\n[swe-mux] ... {label} output omitted ...\n".encode()
    return bytes(prefix) + omitted + bytes(tail), True


async def _drain(
    task: asyncio.Task[tuple[bytes, bool]] | None, *, label: str
) -> tuple[bytes, bool]:
    """Collect a reader task's capture, bounded so an escaped child cannot hang us."""
    if task is None:
        return b"", False
    try:
        return await asyncio.wait_for(task, DRAIN_GRACE_SECONDS)
    except TimeoutError:
        _rate_limited(
            "drain",
            label,
            "bounded_command_drain_abandoned label=%s grace_s=%g",
            label,
            DRAIN_GRACE_SECONDS,
        )
        return b"", False


def release_subprocess_transport(process: asyncio.subprocess.Process) -> None:
    """Close the transport behind `process` while its event loop is still alive.

    asyncio closes a subprocess transport by itself, but only once *every* pipe has
    disconnected and the exit has been delivered, and only on a loop still running
    to deliver them. Every path here that gives up on a pipe breaks that: a
    descendant that escaped the reap and still holds the write end, a reader
    cancelled at shutdown, a drain abandoned after the grace. The transport is then
    left open, and `BaseSubprocessTransport.__del__` calls `close()` on it whenever
    the collector eventually gets there - by which time the loop is usually gone, so
    that call raises `RuntimeError: Event loop is closed` out of a *finalizer*. It
    arrives as an unraisable exception attributed to whatever happens to be running
    at that moment, which under the suite's `filterwarnings = ["error"]` fails an
    unrelated test; that is one of the three failures of the first public CI run
    (2026-08-27), and the reason it is load-sensitive is that on POSIX the exit
    notification crosses a per-child watcher thread that a loaded host may not
    schedule before the loop closes.

    On every path that ran to completion this is a no-op, because the transport is
    already closed. On the paths that did not, it is the release this runner owes:
    our ends of the pipes are dropped now rather than held until a collection.

    `_transport` is private because `asyncio.subprocess.Process` publishes no
    accessor for it and no public way to say "I am finished with this child", so it
    is read defensively; a tidy-up must never become the reason a command reports
    failure, so a close that raises is logged and swallowed.
    """
    transport = getattr(process, "_transport", None)
    if transport is None:
        return
    try:
        transport.close()
    except Exception:  # noqa: BLE001 - releasing a handle must not fail a finished run
        log.debug("bounded_command_transport_release_failed", exc_info=True)


async def _cancel(*tasks: asyncio.Task[tuple[bytes, bool]] | None) -> None:
    live = [task for task in tasks if task is not None]
    for task in live:
        task.cancel()
    if live:
        await asyncio.gather(*live, return_exceptions=True)


class _SpawnLoop:
    """One event loop on its own thread, where every bounded subprocess is spawned.

    asyncio spawns a child *synchronously on the loop that asks*: on Windows the
    proactor transport's constructor calls `CreateProcess` before it returns, and on
    POSIX the fork-and-exec is the same shape. On 2026-09-02 the stall watchdog
    caught the daemon's loop inside that call for 23.5 s while `cargo test` saturated
    the disk the git helper's image lives on - and every terminal, request and
    keystroke waited behind one `git status`. `CreateProcess` releases the GIL, so
    the same call on another thread costs the daemon nothing.

    Moving the *spawn* alone is not possible with asyncio's public API (the
    transport owns the `Popen`), so the whole bounded run - spawn, pipe reads,
    timeout, reap - executes on this loop unchanged, and the caller's loop only ever
    awaits a future. Output callbacks are marshalled back to the caller's loop, the
    caller's context (request correlation) is carried across, and cancelling the
    caller cancels the run over here, which is what reaps the tree.
    """

    def __init__(self, thread_name: str = SPAWN_THREAD_NAME) -> None:
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._thread_name = thread_name

    def get(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is not None and self._loop.is_running():
                return self._loop
            loop = asyncio.new_event_loop()
            ready = threading.Event()

            def run() -> None:
                asyncio.set_event_loop(loop)
                loop.call_soon(ready.set)
                try:
                    loop.run_forever()
                finally:
                    loop.close()

            thread = threading.Thread(target=run, name=self._thread_name, daemon=True)
            thread.start()
            ready.wait(SPAWN_LOOP_STOP_SECONDS)
            self._loop, self._thread = loop, thread
            log.info("subprocess spawn loop started thread=%s", thread.name)
            return loop

    def owns(self, loop: asyncio.AbstractEventLoop) -> bool:
        with self._lock:
            return loop is self._loop

    def stop(self) -> None:
        """Stop the loop and join its thread; a no-op when nothing was started."""
        with self._lock:
            loop, thread = self._loop, self._thread
            self._loop, self._thread = None, None
        if loop is None or thread is None:
            return
        if loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        thread.join(SPAWN_LOOP_STOP_SECONDS)
        if thread.is_alive():
            log.warning("subprocess spawn loop did not stop within %.0fs", SPAWN_LOOP_STOP_SECONDS)


_spawn_lanes: dict[str, _SpawnLoop] = {
    LANE_BACKGROUND: _SpawnLoop(SPAWN_THREAD_NAME),
    LANE_INTERACTIVE: _SpawnLoop(f"{SPAWN_THREAD_NAME}-interactive"),
}


def stop_spawn_loop() -> None:
    """Shut every spawn loop down; the daemon calls this at teardown."""
    for lane in _spawn_lanes.values():
        lane.stop()


atexit.register(stop_spawn_loop)


def _spawn_lane(lane: str) -> _SpawnLoop:
    try:
        return _spawn_lanes[lane]
    except KeyError:
        raise ValueError(f"unknown spawn lane {lane!r}; expected one of {LANES}") from None


def _resolve_on(loop: asyncio.AbstractEventLoop, future: asyncio.Future[Any], task: Any) -> None:
    """Copy a finished spawn-loop task's outcome onto the caller's future."""

    def deliver() -> None:
        if future.done():
            return
        if task.cancelled():
            future.cancel()
        elif (exc := task.exception()) is not None:
            future.set_exception(exc)
        else:
            future.set_result(task.result())

    try:
        loop.call_soon_threadsafe(deliver)
    except RuntimeError:
        # The caller's loop closed before the run finished; nothing is waiting.
        log.debug("bounded_command_outcome_dropped label=%s", getattr(task, "get_name", str)())


async def run_bounded(
    argv: Sequence[str],
    *,
    label: str,
    timeout_seconds: float,
    output_limit: int,
    stderr_limit: int | None = None,
    merge_stderr: bool = False,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    on_chunk: Callable[[bytes], None] | None = None,
    operation_id: str | None = None,
    lane: str = LANE_BACKGROUND,
) -> ProcessOutcome:
    """Run one command to completion under a timeout and an output cap.

    `argv` reaches the OS as given. `env` of `None` inherits this process's
    environment, which is what every caller but the worktree commands wants;
    stdin is always `DEVNULL`, so a program that decides to prompt fails fast
    instead of blocking on a stdin the daemon holds and will never write to.

    Raises `OSError` when the program cannot be started at all - that is the
    caller's diagnostic to phrase ("install ccusage", "could not start codex"),
    and swallowing it into an outcome would make every caller re-derive it.
    Anything raised after the spawn reaps the tree on its way out, `CancelledError`
    included.

    The run itself happens on a spawn loop (`_SpawnLoop`), never on the caller's:
    the spawn is a synchronous kernel call that a saturated disk can hold for tens
    of seconds, and the caller's loop is the one every terminal shares. `lane`
    picks which: pollers share `LANE_BACKGROUND`, and a command a person is
    waiting on (a commit, a land, a diff they opened) takes `LANE_INTERACTIVE`, so
    a spawn the kernel is holding for a sweep does not hold theirs too.
    """
    caller = asyncio.get_running_loop()
    spawn = _spawn_lane(lane).get()
    if spawn is caller:
        # Already on the spawn loop (a nested bounded run); nothing to marshal.
        return await _run_bounded_here(
            argv,
            label=label,
            timeout_seconds=timeout_seconds,
            output_limit=output_limit,
            stderr_limit=stderr_limit,
            merge_stderr=merge_stderr,
            cwd=cwd,
            env=env,
            on_chunk=on_chunk,
            operation_id=operation_id,
        )

    marshalled: Callable[[bytes], None] | None = None
    if on_chunk is not None:
        observer = on_chunk

        def marshalled(chunk: bytes) -> None:
            # Observers touch the caller's own state (an `asyncio.Event`, a queue),
            # so they run where they were written, in the order the bytes arrived.
            try:
                caller.call_soon_threadsafe(observer, chunk)
            except RuntimeError:
                pass

    context = contextvars.copy_context()
    outcome: asyncio.Future[ProcessOutcome] = caller.create_future()
    handle: dict[str, Any] = {}
    cancel_lock = threading.Lock()

    def start() -> None:
        task = spawn.create_task(
            _run_bounded_here(
                argv,
                label=label,
                timeout_seconds=timeout_seconds,
                output_limit=output_limit,
                stderr_limit=stderr_limit,
                merge_stderr=merge_stderr,
                cwd=cwd,
                env=env,
                on_chunk=marshalled,
                operation_id=operation_id,
            ),
            name=f"bounded:{label}",
            context=context,
        )
        task.add_done_callback(lambda done: _resolve_on(caller, outcome, done))
        with cancel_lock:
            handle["task"] = task
            if handle.get("cancelled"):
                task.cancel()

    spawn.call_soon_threadsafe(start)
    try:
        return await outcome
    except asyncio.CancelledError:
        # The caller gave up: cancel the run where it lives, which reaps the tree.
        # Recorded under the lock so a cancel that lands before `start` ran is not
        # lost between the two threads.
        with cancel_lock:
            handle["cancelled"] = True
            task = handle.get("task")
        if task is not None:
            spawn.call_soon_threadsafe(task.cancel)
        raise


async def _run_bounded_here(
    argv: Sequence[str],
    *,
    label: str,
    timeout_seconds: float,
    output_limit: int,
    stderr_limit: int | None,
    merge_stderr: bool,
    cwd: str | Path | None,
    env: Mapping[str, str] | None,
    on_chunk: Callable[[bytes], None] | None,
    operation_id: str | None,
) -> ProcessOutcome:
    """The run, on whichever loop is current. `run_bounded` puts it on the spawn loop."""
    loop = asyncio.get_running_loop()
    started = loop.time()
    stdout_task: asyncio.Task[tuple[bytes, bool]] | None = None
    stderr_task: asyncio.Task[tuple[bytes, bool]] | None = None

    def elapsed() -> float:
        return (loop.time() - started) * 1000

    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=None if cwd is None else str(cwd),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT if merge_stderr else asyncio.subprocess.PIPE,
        env=None if env is None else dict(env),
        creationflags=background_creation_flags(),
    )
    try:
        try:
            assert process.stdout is not None
            stdout_task = asyncio.create_task(
                bounded_read(process.stdout, output_limit, label=label, on_chunk=on_chunk)
            )
            if process.stderr is not None:
                stderr_task = asyncio.create_task(
                    bounded_read(
                        process.stderr,
                        output_limit if stderr_limit is None else stderr_limit,
                        label=f"{label} stderr",
                    )
                )
            try:
                exit_code: int | None = await asyncio.wait_for(process.wait(), timeout_seconds)
                timed_out = False
            except TimeoutError:
                await reap_process_tree(process)
                exit_code, timed_out = None, True
                _rate_limited(
                    "timeout",
                    label,
                    "bounded_command_timed_out label=%s executable=%s timeout_s=%g "
                    "operation_id=%s",
                    label,
                    argv[0] if argv else "",
                    timeout_seconds,
                    operation_id,
                )
            out, out_capped = await _drain(stdout_task, label=label)
            err, err_capped = await _drain(stderr_task, label=label)
        except BaseException:
            # `BaseException` rather than `Exception` for `CancelledError`, which is the
            # whole point: every migrated caller runs inside a supervised background
            # loop, and a loop cancelled at shutdown used to leave its child alive
            # holding the daemon's pipes.
            if process.returncode is None:
                await reap_process_tree(process)
            await _cancel(stdout_task, stderr_task)
            raise
        if out_capped or err_capped:
            _rate_limited(
                "capped",
                label,
                "bounded_command_output_capped label=%s executable=%s limit_bytes=%d "
                "stream=%s operation_id=%s",
                label,
                argv[0] if argv else "",
                output_limit,
                "stdout" if out_capped else "stderr",
                operation_id,
            )
        return ProcessOutcome(
            exit_code=exit_code,
            stdout=out,
            stderr=err,
            stdout_truncated=out_capped,
            stderr_truncated=err_capped,
            duration_ms=elapsed(),
            timed_out=timed_out,
        )
    finally:
        # Unconditional, and after the drains rather than beside the reap: a pipe
        # still being read must not have its bytes cut short, and a run that gave up
        # on one must not leave the transport for a finalizer.
        release_subprocess_transport(process)
