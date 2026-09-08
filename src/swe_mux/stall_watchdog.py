"""Bounded Python stack sampling without asynchronous native frame traversal.

The sampler holds owned frame references from sys._current_frames().
It can inspect a stalled event loop while other Python threads still run.
A native call holding the GIL prevents sampling too; canary lateness records that
coverage gap, and desktop-side recovery can still observe the unresponsive daemon.
The event loop only publishes a timestamp. Sampling, trace IO, and rotation happen
on other threads. Legacy faulthandler trace files remain readable.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import IO, Any

log = logging.getLogger(__name__)

STALL_TRACE_FILENAME = "loop-stalls.log"
#: Lag beyond which the loop is stalled rather than busy. Three seconds is far past
#: any legitimate synchronous call and short enough that a 30 s freeze yields several
#: dumps showing whether the stuck frame moved.
DEFAULT_THRESHOLD_SECONDS = 3.0
#: The trace file is rotated once over this size, keeping one predecessor.
TRACE_ROTATE_BYTES = 4 * 1024 * 1024
#: How far back `explain()` reads when the file has grown past its last marker.
TRACE_TAIL_BYTES = 512 * 1024
CANARY_INTERVAL_SECONDS = 0.25
#: A canary wake this late is recorded; the interval itself is timer-tick noise.
CANARY_RECORD_SECONDS = 0.05
CANARY_HISTORY = 128
MAX_FRAMES = 40
MAX_THREADS = 100
MAX_BUSY_THREADS = 12
RECENT_STALLS = 16
#: Written after each explanation so the next one starts reading after it.
END_MARKER = "# stall explained"

_THREAD_HEADER = re.compile(r"^(?:Current thread|Thread) 0x([0-9A-Fa-f]+)")
_FRAME_LINE = re.compile(r'^  File "(?P<file>.*)", line (?P<line>\d+) in (?P<func>.*)$')
#: faulthandler prints the fraction only when the timeout has one: ``Timeout
#: (0:00:03)!`` in production and ``Timeout (0:00:00.300000)!`` under a test.
_TIMEOUT_HEADER = re.compile(r"^Timeout \(\d+:\d\d:\d\d(?:\.\d+)?\)!")

#: Leaf frames that mean "parked, waiting for work". A thread sitting in one of these
#: was not doing anything during the stall and is left out of the report so the busy
#: ones are readable among the daemon's sixty-odd executor threads.
IDLE_LEAVES: frozenset[tuple[str, str]] = frozenset(
    {
        ("thread.py", "_worker"),
        ("threading.py", "wait"),
        ("threading.py", "_wait_for_tstate_lock"),
        ("queue.py", "get"),
        ("windows_events.py", "_poll"),
        ("selectors.py", "select"),
        ("selectors.py", "_select"),
        ("socket.py", "accept"),
        ("subprocess.py", "_readerthread"),
        ("stall_watchdog.py", "_canary"),
    }
)


@dataclass(slots=True)
class ThreadTrace:
    ident: int
    name: str
    #: Leaf first, as faulthandler prints them: ``func (file:line)``.
    frames: list[str]
    idle: bool


@dataclass(slots=True)
class StallRecord:
    started_at: float
    duration_seconds: float
    canary_starved: bool
    dumps: int
    main_thread: list[str]
    busy_threads: list[dict[str, Any]]
    host: dict[str, Any] = field(default_factory=dict)
    trace_path: str = ""

    @property
    def main_leaf(self) -> str | None:
        return self.main_thread[0] if self.main_thread else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "duration_seconds": round(self.duration_seconds, 3),
            "canary_starved": self.canary_starved,
            "dumps": self.dumps,
            "main_leaf": self.main_leaf,
            "main_thread": list(self.main_thread),
            "busy_threads": [dict(item) for item in self.busy_threads],
            "host": dict(self.host),
            "trace_path": self.trace_path,
        }


def _basename(file: str) -> str:
    """The file's last component under either separator.

    faulthandler prints the host's own paths, but the trace file is read by the
    diagnostics export and by tests on hosts that are not the one that wrote
    it, and ``os.path.basename`` on POSIX leaves ``C:\\py\\x.py`` whole. A
    Windows path accepts both separators, so its ``name`` is right for both.
    """
    return PureWindowsPath(file).name


def _frame_text(file: str, line: str, func: str) -> str:
    return f"{func} ({_basename(file)}:{line})"


def parse_faulthandler_dumps(text: str) -> list[dict[int, list[tuple[str, str, str]]]]:
    """Split faulthandler output into dumps, each a map of thread id to frames.

    faulthandler prints one ``Timeout (h:mm:ss)!`` line per firing, then one
    ``Thread 0x... (most recent call first):`` block per thread with its frames as
    ``  File "...", line N in func``. Frames are returned leaf first, exactly as
    printed. Text before the first timeout header (a banner, a previous
    explanation's marker) belongs to no dump and is ignored.
    """
    dumps: list[dict[int, list[tuple[str, str, str]]]] = []
    current: dict[int, list[tuple[str, str, str]]] | None = None
    thread: list[tuple[str, str, str]] | None = None
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if _TIMEOUT_HEADER.match(line):
            current = {}
            dumps.append(current)
            thread = None
            continue
        if current is None:
            continue
        header = _THREAD_HEADER.match(line)
        if header:
            thread = []
            current[int(header.group(1), 16)] = thread
            continue
        frame = _FRAME_LINE.match(line)
        if frame and thread is not None:
            thread.append((frame.group("file"), frame.group("line"), frame.group("func")))
    return dumps


def _is_idle(frames: list[tuple[str, str, str]]) -> bool:
    if not frames:
        return True
    file, _line, func = frames[0]
    return (_basename(file), func) in IDLE_LEAVES


def describe_dump(
    dump: dict[int, list[tuple[str, str, str]]],
    *,
    main_ident: int,
    names: dict[int, str],
) -> tuple[list[str], list[ThreadTrace]]:
    """The main thread's frames and every other thread that was not parked."""
    main = [_frame_text(*frame) for frame in dump.get(main_ident, [])[:MAX_FRAMES]]
    busy: list[ThreadTrace] = []
    for ident, frames in dump.items():
        if ident == main_ident or _is_idle(frames):
            continue
        busy.append(
            ThreadTrace(
                ident=ident,
                name=names.get(ident, "?"),
                frames=[_frame_text(*frame) for frame in frames[:MAX_FRAMES]],
                idle=False,
            )
        )
    return main, busy[:MAX_BUSY_THREADS]


def default_host_probe() -> dict[str, Any]:
    """CPU, memory and process count at explanation time; empty without psutil."""
    try:
        import psutil
    except ImportError:  # pragma: no cover - psutil is a base dependency
        return {}
    probe: dict[str, Any] = {}
    try:
        probe["cpu_percent"] = psutil.cpu_percent(interval=None)
        probe["memory_percent"] = psutil.virtual_memory().percent
        probe["process_count"] = len(psutil.pids())
    except (OSError, RuntimeError) as exc:  # pragma: no cover - host-specific
        probe["error"] = str(exc)
    return probe


class StallWatchdog:
    """Samples safely when Python can run and explains stalls after they end."""

    def __init__(
        self,
        trace_path: Path,
        *,
        threshold: float = DEFAULT_THRESHOLD_SECONDS,
        monotonic: Callable[[], float] = time.perf_counter,
        host_probe: Callable[[], dict[str, Any]] = default_host_probe,
        rotate_bytes: int = TRACE_ROTATE_BYTES,
    ) -> None:
        self.trace_path = trace_path
        self.threshold = threshold
        self._monotonic = monotonic
        self._host_probe = host_probe
        self._rotate_bytes = rotate_bytes
        self._file: IO[bytes] | None = None
        self._explained_offset = 0
        self._last_beat = 0.0
        self._last_capture = 0.0
        self._trace_lock = threading.RLock()
        self._canary_late: deque[tuple[float, float]] = deque(maxlen=CANARY_HISTORY)
        self._canary_worst = 0.0
        #: When the canary's current sleep began, or None between sleeps. A wake
        #: that is overdue *now* is a starvation the history cannot show yet.
        self._canary_sleeping_since: float | None = None
        self._canary_stop = threading.Event()
        self._canary_thread: threading.Thread | None = None
        self._recent: deque[StallRecord] = deque(maxlen=RECENT_STALLS)
        self._lock = threading.Lock()
        self._armed = False
        self._stall_count = 0

    # -- lifecycle ---------------------------------------------------------------

    def start(self) -> None:
        if self._armed:
            return
        self.trace_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.trace_path, "ab", buffering=0)  # noqa: SIM115 - owned until close
        self._explained_offset = self._file.seek(0, os.SEEK_END)
        self._note(f"# watchdog armed threshold={self.threshold:.1f}s capture=python_gil_required")
        self._canary_stop.clear()
        self._armed = True
        self.beat()
        self._canary_thread = threading.Thread(
            target=self._canary, name="loop-stall-canary", daemon=True
        )
        self._canary_thread.start()
        log.info(
            "loop stall watchdog armed threshold_s=%.1f capture=python_gil_required trace=%s",
            self.threshold,
            self.trace_path,
        )

    async def stop(self) -> None:
        """File IO and sampler shutdown never wait on the event loop."""
        await asyncio.to_thread(self.close)

    def close(self) -> None:
        self._canary_stop.set()
        self._armed = False
        thread = self._canary_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
            if thread.is_alive():
                log.warning("loop stall sampler still draining trace=%s", self.trace_path)
                return  # The sampler owns final close, including a slow filesystem write.
        self._close_trace()

    def _close_trace(self) -> None:
        with self._trace_lock:
            if self._file is not None:
                self._note("# watchdog stopped")
                self._file.close()
                self._file = None

    # -- the loop's side ---------------------------------------------------------

    def beat(self) -> None:
        """Publish progress only: no IO, thread creation, joins, or diagnostic locks."""
        self._last_beat = self._monotonic()

    def _capture_if_stalled(self, now: float) -> None:
        if now - self._last_beat < self.threshold or now - self._last_capture < self.threshold:
            return
        self._last_capture = now
        # sys._current_frames() returns owned Python frame references while holding
        # the GIL. Never use dump_traceback_later here: its native thread traverses
        # changing interpreter frames without the GIL and crashed the daemon on
        # 2026-09-07. A GIL-held stall may have no sample; report that limitation.
        lines = [f"# capture=python_gil_required pid={os.getpid()}", "Timeout (0:00:00)!"]
        frames = sys._current_frames()
        try:
            for ident, frame in list(frames.items())[:MAX_THREADS]:
                if ident == threading.get_ident():
                    continue
                lines.append(f"Thread 0x{ident:08x} (most recent call first):")
                for _ in range(MAX_FRAMES):
                    code = frame.f_code
                    filename = code.co_filename.replace("\n", "\\n").replace("\r", "\\r")[:500]
                    lines.append(
                        f'  File "{filename}", line {frame.f_lineno} in {code.co_name[:500]}'
                    )
                    if frame.f_back is None:
                        break
                    frame = frame.f_back
        finally:
            frames.clear()
        with self._trace_lock:
            if self._file is None and self._armed:
                self._file = open(self.trace_path, "ab", buffering=0)  # noqa: SIM115
            self._rotate_if_large()
            if self._file is not None:
                self._file.write(("\n".join(lines) + "\n").encode("utf-8", "backslashreplace"))

    # -- the canary thread -------------------------------------------------------

    def _canary(self) -> None:
        try:
            while not self._canary_stop.is_set():
                before = self._monotonic()
                with self._lock:
                    self._canary_sleeping_since = before
                if self._canary_stop.wait(CANARY_INTERVAL_SECONDS):
                    break
                after = self._monotonic()
                late = after - before - CANARY_INTERVAL_SECONDS
                with self._lock:
                    self._canary_sleeping_since = None
                    if late >= CANARY_RECORD_SECONDS:
                        self._canary_late.append((after, late))
                        self._canary_worst = max(self._canary_worst, late)
                try:
                    self._capture_if_stalled(after)
                except Exception:
                    log.exception("loop stall capture failed trace=%s", self.trace_path)
        finally:
            self._close_trace()

    def canary_starved_since(self, started: float, minimum: float) -> bool:
        """Whether the canary thread itself failed to run across a window.

        ``started`` is a monotonic time; a late wake that ended after it and was
        late by at least ``minimum`` means this thread was as stuck as the loop.

        A wake that has not happened yet counts too. ``explain()`` runs the
        moment the loop resumes, and a canary that was starved by the stall is
        still waiting for the GIL then - the loop thread keeps winning it back
        after each syscall until a drop request forces a switch, which on macOS
        was reliably later than this call. Its lateness is known without it: the
        sleep began at a known time and was due an interval later.
        """
        with self._lock:
            if any(ended >= started and late >= minimum for ended, late in self._canary_late):
                return True
            sleeping_since = self._canary_sleeping_since
        if sleeping_since is None:
            return False
        overdue = self._monotonic() - sleeping_since - CANARY_INTERVAL_SECONDS
        return overdue >= minimum

    # -- after the stall ---------------------------------------------------------

    def explain(self, lag_seconds: float) -> StallRecord:
        """Build the record for a stall the loop just measured. Runs off the loop."""
        ended = self._monotonic()
        started = ended - lag_seconds
        # Half the stall, floored at two canary intervals: a canary that slept
        # through the first quarter second of a stall still wakes late by nearly
        # all of it, and anything less than half is the canary running normally.
        canary_starved = self.canary_starved_since(
            started - CANARY_INTERVAL_SECONDS,
            max(2 * CANARY_INTERVAL_SECONDS, lag_seconds * 0.5),
        )
        with self._trace_lock:
            dumps = self._read_new_dumps()
        main_ident = threading.main_thread().ident or 0
        names = {t.ident: t.name for t in threading.enumerate() if t.ident is not None}
        main_frames: list[str] = []
        busy: list[ThreadTrace] = []
        if dumps:
            main_frames, busy = describe_dump(dumps[0], main_ident=main_ident, names=names)
        record = StallRecord(
            started_at=time.time() - lag_seconds,
            duration_seconds=lag_seconds,
            canary_starved=canary_starved,
            dumps=len(dumps),
            main_thread=main_frames,
            busy_threads=[
                {"name": item.name, "ident": item.ident, "frames": item.frames} for item in busy
            ],
            host=self._host_probe(),
            trace_path=str(self.trace_path),
        )
        with self._lock:
            self._recent.appendleft(record)
            self._stall_count += 1
        with self._trace_lock:
            self._note(
                f"{END_MARKER} duration_s={lag_seconds:.2f} canary_starved={canary_starved} "
                f"dumps={len(dumps)} main={record.main_leaf or '-'}"
            )
            self._rotate_if_large()
        return record

    def _read_new_dumps(self) -> list[dict[int, list[tuple[str, str, str]]]]:
        if self._file is None:
            return []
        try:
            size = os.path.getsize(self.trace_path)
            start = max(self._explained_offset, size - TRACE_TAIL_BYTES)
            with open(self.trace_path, "rb") as handle:
                handle.seek(start)
                text = handle.read().decode("utf-8", "replace")
            self._explained_offset = size
        except OSError as exc:
            log.warning("loop stall trace unreadable path=%s error=%s", self.trace_path, exc)
            return []
        return parse_faulthandler_dumps(text)

    def _note(self, line: str) -> None:
        if self._file is None:
            return
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            self._file.write(f"{line} at={stamp}\n".encode())
        except OSError:  # pragma: no cover - a full disk must not take the loop down
            pass

    def _rotate_if_large(self) -> None:
        if self._file is None:
            return
        try:
            if os.path.getsize(self.trace_path) < self._rotate_bytes:
                return
            self._file.close()
            self._file = None
            previous = self.trace_path.with_name(self.trace_path.name + ".1")
            try:
                os.replace(self.trace_path, previous)
                self._explained_offset = 0
            finally:
                self._file = open(self.trace_path, "ab", buffering=0)  # noqa: SIM115
            self._note("# rotated")
            log.info("loop stall trace rotated path=%s", self.trace_path)
        except OSError as exc:
            log.warning("loop stall trace rotation failed path=%s error=%s", self.trace_path, exc)

    # -- reporting ---------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            recent = [record.as_dict() for record in self._recent]
            canary_worst = self._canary_worst
            count = self._stall_count
        return {
            "threshold_seconds": self.threshold,
            "armed": self._armed,
            "capture_mode": "python_gil_required",
            "gil_held_stacks_available": False,
            "trace_path": str(self.trace_path),
            "stalls_explained": count,
            "canary_worst_late_seconds": round(canary_worst, 4),
            "recent": recent,
        }
