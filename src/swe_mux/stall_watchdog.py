"""Name the frame the event loop was stuck in, from outside the loop.

`loop_lag` measures how late the loop ran its own timer and can say *how long* it
was gone; it cannot say *where* the thread was, because the thing that would record
that is the thread that is stuck. On 2026-09-01 the daemon froze for 46 s and 53 s
twice in three minutes under a fleet-wide cargo build wave, every HTTP request and
websocket frame hung with it, and nothing in the process could say what it had been
doing. A profiler attached afterwards saw a healthy daemon.

Two mechanisms, chosen because they fail differently:

- **`faulthandler.dump_traceback_later` is a C watchdog that does not need the GIL.**
  The loop re-arms it on every lag probe; when the loop stops re-arming it, the dump
  fires and writes every thread's Python stack to `loop-stalls.log`. This is the only
  thing that can see a stall whose cause is a native call holding the GIL in a
  *worker* thread (psutil on Windows, `re` over a large string, a hung
  `ReadProcessMemory`) - a Python watchdog thread is starved by exactly the thing it
  is meant to observe.
- **A Python canary thread that only measures its own lateness.** It sleeps a quarter
  second in a loop and records every wake that came late. When the loop reports a
  stall, whether the canary was starved across the same window is the one bit that
  discriminates the two shapes: canary on time means synchronous Python work sat on
  the loop thread and the dump names it; canary starved too means the GIL was held
  natively or the whole process was descheduled, and the dump's *worker* frames are
  the ones to read.

The loop is the authority on duration (its own `sample()` measured it); this module
supplies the explanation. `explain()` runs after the stall, off the loop, reads the
dumps written during it, and returns one bounded record that `server.py` logs and
`OperationalTelemetryStore` keeps, so the question "what was it doing" is answerable
from `daemon.log` and `mux.db` after the fact rather than by being present.
"""

from __future__ import annotations

import faulthandler
import logging
import os
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
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


def _frame_text(file: str, line: str, func: str) -> str:
    return f"{func} ({os.path.basename(file)}:{line})"


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
    return (os.path.basename(file), func) in IDLE_LEAVES


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
    """Arms a GIL-free stack dump from the loop and explains a stall after it ends."""

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
        self._last_arm = 0.0
        self._last_beat = 0.0
        #: Half the threshold, so the dump fires between one and one and a half
        #: thresholds after the loop's last probe. Each arm starts a fresh C thread
        #: (that is how faulthandler resets its timer), which is cheap at this rate.
        self._rearm_interval = threshold / 2
        self._canary_late: deque[tuple[float, float]] = deque(maxlen=CANARY_HISTORY)
        self._canary_worst = 0.0
        self._canary_stop = threading.Event()
        self._canary_thread: threading.Thread | None = None
        self._recent: deque[StallRecord] = deque(maxlen=RECENT_STALLS)
        self._lock = threading.Lock()
        self._armed = False
        self._stall_count = 0

    # -- lifecycle ---------------------------------------------------------------

    def start(self) -> None:
        self.trace_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.trace_path, "ab", buffering=0)  # noqa: SIM115 - kept open for faulthandler
        self._explained_offset = self._file.seek(0, os.SEEK_END)
        self._note(f"# watchdog armed threshold={self.threshold:.1f}s")
        self._canary_stop.clear()
        self._canary_thread = threading.Thread(
            target=self._canary, name="loop-stall-canary", daemon=True
        )
        self._canary_thread.start()
        self.beat()
        log.info(
            "loop stall watchdog armed threshold_s=%.1f trace=%s", self.threshold, self.trace_path
        )

    async def stop(self) -> None:
        """The shape every daemon handle is stopped through; the work is synchronous."""
        self.close()

    def close(self) -> None:
        self._canary_stop.set()
        if self._armed:
            faulthandler.cancel_dump_traceback_later()
            self._armed = False
        if self._file is not None:
            self._note("# watchdog stopped")
            self._file.close()
            self._file = None

    # -- the loop's side ---------------------------------------------------------

    def beat(self) -> None:
        """Called from the event loop on every lag probe; re-arms the C watchdog."""
        now = self._monotonic()
        self._last_beat = now
        if self._file is None:
            return
        if not self._armed or now - self._last_arm >= self._rearm_interval:
            self._arm()

    def _arm(self) -> None:
        if self._file is None:
            return
        faulthandler.dump_traceback_later(self.threshold, repeat=True, file=self._file, exit=False)
        self._armed = True
        self._last_arm = self._monotonic()

    # -- the canary thread -------------------------------------------------------

    def _canary(self) -> None:
        while not self._canary_stop.is_set():
            before = self._monotonic()
            time.sleep(CANARY_INTERVAL_SECONDS)
            after = self._monotonic()
            late = after - before - CANARY_INTERVAL_SECONDS
            if late >= CANARY_RECORD_SECONDS:
                with self._lock:
                    self._canary_late.append((after, late))
                    self._canary_worst = max(self._canary_worst, late)

    def canary_starved_since(self, started: float, minimum: float) -> bool:
        """Whether the canary thread itself failed to run across a window.

        ``started`` is a monotonic time; a late wake that ended after it and was
        late by at least ``minimum`` means this thread was as stuck as the loop.
        """
        with self._lock:
            return any(
                ended >= started and late >= minimum for ended, late in self._canary_late
            )

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
            if self._armed:
                faulthandler.cancel_dump_traceback_later()
                self._armed = False
            self._file.close()
            previous = self.trace_path.with_name(self.trace_path.name + ".1")
            os.replace(self.trace_path, previous)
            self._file = open(self.trace_path, "ab", buffering=0)  # noqa: SIM115
            self._explained_offset = 0
            self._note("# rotated")
            self._arm()
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
            "trace_path": str(self.trace_path),
            "stalls_explained": count,
            "canary_worst_late_seconds": round(canary_worst, 4),
            "recent": recent,
        }
