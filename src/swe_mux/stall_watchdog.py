"""Bounded Python stack sampling without asynchronous native frame traversal.

The sampler holds owned frame references from sys._current_frames().
It can inspect a stalled event loop while other Python threads still run.
A native call holding the GIL prevents sampling too; canary lateness records that
coverage gap, and desktop-side recovery can still observe the unresponsive daemon.
The event loop only publishes a timestamp. Sampling, trace IO, and rotation happen
on other threads. Legacy faulthandler trace files remain readable.

A stack sample says where every thread is, not who ran. The 2026-10-16 incident
showed why that is not enough: 68 of 73 main-thread samples sat at different
GIL-releasing syscalls, every executor thread looked busy, and nothing could say
whether the loop was doing too much work or waiting for the GIL while somebody else
did. So beside the stacks the canary keeps:

- **per-thread CPU time** sampled once a second (`thread_cpu`), so the explanation can
  difference it across exactly the stall's window and name the thread that burned CPU;
- **process and host CPU** over the same window, so a host that did not schedule this
  process is told apart from a process that was busy;
- **the loop's ready-queue depth** while the loop is overdue, which separates a loop
  drowning in callbacks from one stuck in a single call;
- **what each thread says it is waiting for** (`parked_threads`), which replaces leaf
  guessing for the waits that mark themselves;
- optionally **one external `py-spy dump`** at the stall's onset, whose `active+gil`
  marker names the GIL holder outright - the one thing no in-process sample can see.

From those, `diagnose` states a verdict for every stall (`VERDICTS`).
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import IO, Any

from .executor_accounting import LEDGER, WorkLedger
from .host_metrics import (
    ThreadCpuReader,
    host_cpu_times,
    host_memory_percent,
    host_process_count,
)
from .parked_threads import DB_LOCK_WAIT, parked_threads
from .subprocess_flags import background_creation_flags

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

#: How often the canary records every thread's CPU time. A stall is at least the
#: threshold long, so a one-second cadence always leaves a sample before it began.
CPU_SAMPLE_INTERVAL_SECONDS = 1.0
#: About a minute of CPU samples: enough to find the one preceding any stall the
#: loop can still report, which it does the moment it resumes.
CPU_HISTORY = 64
#: The loop-lag probe beats every half second; overdue by this much, the loop is
#: behind and its ready queue is worth reading.
READY_SAMPLE_AFTER_SECONDS = 1.0
READY_HISTORY = 256

#: The loop thread ran at least this share of the window: it was the one working.
LOOP_BUSY_SHARE = 0.6
#: Other threads of this process burned at least this many cores of CPU across the
#: window while the loop did not: the loop was waiting for the GIL behind them.
OTHERS_BUSY_CORES = 0.5
#: A smaller burn still counts when it exceeds the loop's own share.
OTHERS_CONTENDING_CORES = 0.25
#: Host-wide utilisation at which nothing in this process running means the OS did
#: not schedule it.
HOST_SATURATED_SHARE = 0.9
#: A ready queue at least this deep while the loop was busy means many callbacks
#: rather than one long one. The depth is a racy hint read from another thread.
READY_DEEP = 64
#: A thread that used less than this share of the window was not busy, whatever its
#: stack looked like: it was blocked in a call that released the GIL.
BUSY_THREAD_SHARE = 0.05
TOP_CPU_THREADS = 6
TOP_WORK = 6
MAX_BLOCKED_NAMES = 8

#: `MUX_STALL_PROFILER`: unset uses `py-spy` from `PATH` when present, `off`
#: disables the external dump, anything else names the executable to run.
STALL_PROFILER_ENV = "MUX_STALL_PROFILER"
#: `MUX_STALL_THRESHOLD_SECONDS`: a diagnostic override of the capture threshold,
#: for a load harness whose stalls are real but shorter than three seconds. Bounded
#: below because a capture every half second would itself be load.
STALL_THRESHOLD_ENV = "MUX_STALL_THRESHOLD_SECONDS"
STALL_THRESHOLD_BOUNDS = (0.5, 60.0)


def threshold_from_environment(environ: Mapping[str, str] | None = None) -> float:
    """The capture threshold, honouring a valid `MUX_STALL_THRESHOLD_SECONDS`."""
    raw = (os.environ if environ is None else environ).get(STALL_THRESHOLD_ENV, "").strip()
    if not raw:
        return DEFAULT_THRESHOLD_SECONDS
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    low, high = STALL_THRESHOLD_BOUNDS
    if not low <= value <= high:
        log.warning(
            "ignoring %s=%r: expected seconds in [%.1f, %.1f]", STALL_THRESHOLD_ENV, raw, low, high
        )
        return DEFAULT_THRESHOLD_SECONDS
    return value


EXTERNAL_DUMP_MIN_INTERVAL_SECONDS = 120.0
EXTERNAL_DUMP_TIMEOUT_SECONDS = 15.0
#: How long an explanation waits for a dump still running from its stall's onset.
EXTERNAL_DUMP_WAIT_SECONDS = 5.0
MAX_EXTERNAL_THREADS = 8
MAX_EXTERNAL_FRAMES = 8

#: Every verdict `diagnose` can state, and what it means.
VERDICTS: dict[str, str] = {
    "loop_blocking_call": "the loop thread ran CPU for most of the stall with a shallow "
    "ready queue: one long synchronous call",
    "loop_saturated": "the loop thread ran CPU for most of the stall with a deep ready "
    "queue: more callbacks than it can run",
    "gil_convoy": "the loop thread barely ran while other threads of this process burned "
    "CPU: it was waiting for the GIL",
    "host_starved": "nothing in this process ran much while the host CPU was saturated: "
    "the daemon was not scheduled, or waited on I/O the saturated host served slowly",
    "blocked_wait": "nothing in this process ran much and the host had headroom: the "
    "loop was blocked in a wait (I/O, a lock, paging, or a native call holding the GIL)",
    "unsampled": "no CPU samples covered the stall, so it cannot be classified",
}

_THREAD_HEADER = re.compile(r"^(?:Current thread|Thread) 0x([0-9A-Fa-f]+)(?: \[parked:([^\]]*)\])?")
_FRAME_LINE = re.compile(r'^  File "(?P<file>.*)", line (?P<line>\d+) in (?P<func>.*)$')
#: faulthandler prints the fraction only when the timeout has one: ``Timeout
#: (0:00:03)!`` in production and ``Timeout (0:00:00.300000)!`` under a test.
_TIMEOUT_HEADER = re.compile(r"^Timeout \(\d+:\d\d:\d\d(?:\.\d+)?\)!")
_READY_LINE = re.compile(r"^# ready_queue=(\d+)")
_CAPTURED_LINE = re.compile(r"^# captured_at=(\d+(?:\.\d+)?)")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_PYSPY_THREAD = re.compile(r'^Thread (0x[0-9A-Fa-f]+|\d+) \(([^)]*)\)(?:: "(.*)")?\s*$')

#: Leaf frames that mean "parked, waiting for work". A thread sitting in one of these
#: was not doing anything during the stall and is left out of the report so the busy
#: ones are readable among the daemon's sixty-odd executor threads. Waits that can
#: mark themselves do so through `parked_threads` instead, which is not a guess.
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

Frame = tuple[str, str, str]


@dataclass(slots=True)
class ThreadTrace:
    ident: int
    name: str
    #: Leaf first, as faulthandler prints them: ``func (file:line)``.
    frames: list[str]
    idle: bool
    #: CPU seconds this thread used across the stall window, when measured.
    cpu_seconds: float | None = None


@dataclass(slots=True)
class StallDump:
    """One capture: every thread's frames, the parked marks, and the ready queue."""

    threads: dict[int, list[Frame]] = field(default_factory=dict)
    parked: dict[int, str] = field(default_factory=dict)
    ready_queue: int | None = None
    #: When the capture was taken, on the watchdog's clock; absent in legacy files.
    captured_at: float | None = None


@dataclass(slots=True)
class ThreadClassification:
    main: list[str]
    #: Not parked and, where CPU was measured, using it.
    busy: list[ThreadTrace]
    #: Not parked, not busy: blocked in a call that released the GIL.
    blocked: list[str]
    parked: int
    db_lock_waiters: int


@dataclass(slots=True)
class CpuSample:
    at: float
    #: CPU seconds by thread ident.
    threads: dict[int, float]
    names: dict[int, str]
    process: float
    host: tuple[float, float] | None


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
    diagnosis: dict[str, Any] = field(default_factory=dict)

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
            "diagnosis": dict(self.diagnosis),
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


def parse_stall_dumps(
    text: str,
    *,
    full: int | None = None,
    window: tuple[float, float] | None = None,
) -> list[StallDump]:
    """Split trace text into dumps, each with its threads, parked marks and ready queue.

    faulthandler prints one ``Timeout (h:mm:ss)!`` line per firing, then one
    ``Thread 0x... (most recent call first):`` block per thread with its frames as
    ``  File "...", line N in func``. Frames are returned leaf first, exactly as
    printed. Text before the first timeout header (a banner, a previous
    explanation's marker) belongs to no dump and is ignored. This sampler's own
    captures add ``[parked:reason]`` to a thread header and a ``# ready_queue=N``
    line after the timeout header; legacy files have neither and parse the same.

    ``full`` parses threads only for the first that many dumps; later ones carry
    their ready queue alone. An explanation reads only its first dump's stacks,
    and a long stall writes one dump per threshold of 300-odd lines each.

    ``window`` drops every stamped dump captured outside ``(start, end)``, so an
    explanation never reads a capture that belonged to some other slow stretch.
    Unstamped (legacy) dumps are kept.
    """
    dumps: list[StallDump] = []
    current: StallDump | None = None
    thread: list[Frame] | None = None
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if _TIMEOUT_HEADER.match(line):
            current = StallDump()
            dumps.append(current)
            thread = None
            continue
        if current is None:
            continue
        if line.startswith("# captured_at="):
            captured = _CAPTURED_LINE.match(line)
            if captured:
                current.captured_at = float(captured.group(1))
                if window is not None and not window[0] <= current.captured_at <= window[1]:
                    dumps.pop()
                    current = None
            continue
        if full is not None and len(dumps) > full:
            if line.startswith("# ready_queue="):
                ready = _READY_LINE.match(line)
                if ready:
                    current.ready_queue = int(ready.group(1))
            continue
        header = _THREAD_HEADER.match(line)
        if header:
            ident = int(header.group(1), 16)
            thread = []
            current.threads[ident] = thread
            if header.group(2):
                current.parked[ident] = header.group(2)
            continue
        ready = _READY_LINE.match(line)
        if ready:
            current.ready_queue = int(ready.group(1))
            continue
        frame = _FRAME_LINE.match(line)
        if frame and thread is not None:
            thread.append((frame.group("file"), frame.group("line"), frame.group("func")))
    return dumps


def parse_faulthandler_dumps(text: str) -> list[dict[int, list[Frame]]]:
    """Each dump's thread map alone (see `parse_stall_dumps`)."""
    return [dump.threads for dump in parse_stall_dumps(text)]


def _is_idle(frames: list[Frame]) -> bool:
    if not frames:
        return True
    file, _line, func = frames[0]
    return (_basename(file), func) in IDLE_LEAVES


def classify_threads(
    dump: StallDump | None,
    *,
    main_ident: int,
    names: Mapping[int, str],
    cpu: Mapping[int, float] | None = None,
    window: float | None = None,
) -> ThreadClassification:
    """Sort a dump's threads into the loop's frames, busy, blocked, and parked.

    A thread is parked when it marked itself so, or when its leaf is a known idle
    wait. Of the rest, a thread whose CPU was measured and stayed under
    `BUSY_THREAD_SHARE` of the window was blocked rather than busy - which is what
    keeps an abandoned name worker stuck in the kernel, or a reader waiting on a
    pipe, out of the busy list on every stall. Without CPU data every unparked
    thread is reported busy, as before. Busy threads are ordered by CPU, highest
    first.
    """
    if dump is None:
        return ThreadClassification(main=[], busy=[], blocked=[], parked=0, db_lock_waiters=0)
    main = [_frame_text(*frame) for frame in dump.threads.get(main_ident, [])[:MAX_FRAMES]]
    busy: list[ThreadTrace] = []
    blocked: list[str] = []
    parked = 0
    db_lock_waiters = 0
    floor = BUSY_THREAD_SHARE * window if cpu is not None and window else None
    for ident, frames in dump.threads.items():
        if ident == main_ident:
            continue
        reason = dump.parked.get(ident)
        if reason == DB_LOCK_WAIT:
            db_lock_waiters += 1
            continue
        if reason is not None or _is_idle(frames):
            parked += 1
            continue
        used = cpu.get(ident) if cpu is not None else None
        if floor is not None and (used is None or used < floor):
            blocked.append(names.get(ident, "?"))
            continue
        busy.append(
            ThreadTrace(
                ident=ident,
                name=names.get(ident, "?"),
                frames=[_frame_text(*frame) for frame in frames[:MAX_FRAMES]],
                idle=False,
                cpu_seconds=round(used, 3) if used is not None else None,
            )
        )
    busy.sort(key=lambda item: -(item.cpu_seconds or 0.0))
    return ThreadClassification(
        main=main,
        busy=busy[:MAX_BUSY_THREADS],
        blocked=blocked,
        parked=parked,
        db_lock_waiters=db_lock_waiters,
    )


def describe_dump(
    dump: dict[int, list[Frame]] | StallDump,
    *,
    main_ident: int,
    names: dict[int, str],
) -> tuple[list[str], list[ThreadTrace]]:
    """The main thread's frames and every other thread that was not parked."""
    stall_dump = dump if isinstance(dump, StallDump) else StallDump(threads=dump)
    result = classify_threads(stall_dump, main_ident=main_ident, names=names)
    return result.main, result.busy


@dataclass(slots=True)
class StallDiagnosis:
    verdict: str
    reason: str
    window_seconds: float | None = None
    loop_cpu_share: float | None = None
    others_cpu_cores: float | None = None
    process_cpu_cores: float | None = None
    host_cpu_share: float | None = None
    ready_queue_max: int | None = None
    top_threads: list[dict[str, Any]] = field(default_factory=list)
    gil_holder: dict[str, Any] | None = None
    blocked_threads: list[str] = field(default_factory=list)
    blocked_count: int = 0
    parked_threads: int = 0
    db_lock_waiters: int = 0
    external_dump: dict[str, Any] | None = None
    top_work: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "reason": self.reason,
            "window_seconds": self.window_seconds,
            "loop_cpu_share": self.loop_cpu_share,
            "others_cpu_cores": self.others_cpu_cores,
            "process_cpu_cores": self.process_cpu_cores,
            "host_cpu_share": self.host_cpu_share,
            "ready_queue_max": self.ready_queue_max,
            "top_threads": [dict(item) for item in self.top_threads],
            "gil_holder": dict(self.gil_holder) if self.gil_holder else None,
            "blocked_threads": list(self.blocked_threads),
            "blocked_count": self.blocked_count,
            "parked_threads": self.parked_threads,
            "db_lock_waiters": self.db_lock_waiters,
            "external_dump": dict(self.external_dump) if self.external_dump else None,
            "top_work": [dict(item) for item in self.top_work],
        }


def _round(value: float | None, digits: int = 3) -> float | None:
    return None if value is None else round(value, digits)


def diagnose(
    *,
    window: float | None,
    loop_cpu: float | None,
    others_cpu: float | None,
    process_cpu: float | None = None,
    host_share: float | None = None,
    ready_queue_max: int | None = None,
    top_threads: list[dict[str, Any]] | None = None,
    loop_name: str = "MainThread",
    loop_ident: int = 0,
    classification: ThreadClassification | None = None,
    external: dict[str, Any] | None = None,
    top_work: list[dict[str, Any]] | None = None,
) -> StallDiagnosis:
    """State what kind of stall this was from CPU over its window.

    ``loop_cpu`` and ``others_cpu`` are CPU seconds across ``window`` seconds; the
    verdicts and their thresholds are documented at `VERDICTS` and the constants
    above. The GIL holder comes from an external dump when one caught the stall,
    and otherwise is inferred: the top CPU thread for a convoy, the loop itself for
    a busy loop, and nobody when nothing ran.
    """
    top = list(top_threads or [])
    classified = classification or ThreadClassification([], [], [], 0, 0)
    loop_share = others_cores = None
    if window is None or window <= 0 or loop_cpu is None or others_cpu is None:
        verdict = "unsampled"
    else:
        loop_share = loop_cpu / window
        others_cores = others_cpu / window
        if loop_share >= LOOP_BUSY_SHARE:
            deep = ready_queue_max is not None and ready_queue_max >= READY_DEEP
            verdict = "loop_saturated" if deep else "loop_blocking_call"
        elif others_cores >= OTHERS_BUSY_CORES or (
            others_cores >= OTHERS_CONTENDING_CORES and others_cores > loop_share
        ):
            verdict = "gil_convoy"
        elif host_share is not None and host_share >= HOST_SATURATED_SHARE:
            verdict = "host_starved"
        else:
            verdict = "blocked_wait"
    holder: dict[str, Any] | None = None
    for thread in (external or {}).get("threads") or []:
        if "gil" in str(thread.get("state", "")):
            holder = {
                "source": "py-spy",
                "name": thread.get("name"),
                "native_id": thread.get("native_id"),
                "frames": list(thread.get("frames") or [])[:MAX_EXTERNAL_FRAMES],
            }
            break
    if holder is None and verdict == "gil_convoy":
        candidate = next((item for item in top if item.get("ident") != loop_ident), None)
        if candidate is not None:
            holder = {"source": "cpu", **candidate}
    if holder is None and verdict in {"loop_blocking_call", "loop_saturated"}:
        holder = {
            "source": "cpu",
            "name": loop_name,
            "ident": loop_ident,
            "share": _round(loop_share),
        }
    if verdict == "unsampled":
        reason = VERDICTS[verdict]
    else:
        reason = (
            f"loop ran {loop_share or 0:.0%} of the {window:.1f}s window, other threads "
            f"{others_cores or 0:.2f} cores, host "
            f"{'unknown' if host_share is None else f'{host_share:.0%}'} busy: "
            f"{VERDICTS[verdict]}"
        )
    return StallDiagnosis(
        verdict=verdict,
        reason=reason,
        window_seconds=_round(window),
        loop_cpu_share=_round(loop_share),
        others_cpu_cores=_round(others_cores),
        process_cpu_cores=_round(
            process_cpu / window if process_cpu is not None and window else None
        ),
        host_cpu_share=_round(host_share),
        ready_queue_max=ready_queue_max,
        top_threads=top,
        gil_holder=holder,
        blocked_threads=classified.blocked[:MAX_BLOCKED_NAMES],
        blocked_count=len(classified.blocked),
        parked_threads=classified.parked,
        db_lock_waiters=classified.db_lock_waiters,
        external_dump=external,
        top_work=list(top_work or []),
    )


def parse_py_spy_dump(text: str) -> list[dict[str, Any]]:
    """Threads from `py-spy dump` output: native id, state, name and frames.

    The state is ``idle``, ``active`` or ``active+gil``. py-spy colours its output
    even into a pipe, so escape sequences are stripped first.
    """
    threads: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw in text.splitlines():
        line = _ANSI.sub("", raw).rstrip()
        header = _PYSPY_THREAD.match(line)
        if header:
            token = header.group(1)
            current = {
                "native_id": int(token, 16) if token.lower().startswith("0x") else int(token),
                "state": header.group(2),
                "name": header.group(3) or "?",
                "frames": [],
            }
            threads.append(current)
            continue
        if current is not None and line.startswith("    ") and line.strip():
            if len(current["frames"]) < MAX_FRAMES:
                current["frames"].append(line.strip())
    return threads


class ExternalStackDumper:
    """One `py-spy dump --nonblocking` of this process at a stall's onset.

    `py-spy` reads the interpreter from outside, so it sees the thread holding the GIL
    even when that thread is inside a native call that no in-process sampler can
    interrupt. `--nonblocking` is not optional: without it py-spy suspends the target,
    which would be a stall of its own. Runs on its own thread, at most once per
    `EXTERNAL_DUMP_MIN_INTERVAL_SECONDS`, never more than one at a time.
    """

    def __init__(
        self,
        executable: str | None,
        *,
        source: str,
        min_interval: float = EXTERNAL_DUMP_MIN_INTERVAL_SECONDS,
        timeout: float = EXTERNAL_DUMP_TIMEOUT_SECONDS,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        monotonic: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.executable = executable
        self.source = source
        self._min_interval = min_interval
        self._timeout = timeout
        self._run_command = run
        self._monotonic = monotonic
        self._done = threading.Condition()
        self._in_flight_since: float | None = None
        self._last_started = -math.inf
        self._last: dict[str, Any] | None = None
        self._runs = 0
        self._failures = 0

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> ExternalStackDumper:
        env = os.environ if environ is None else environ
        configured = env.get(STALL_PROFILER_ENV, "").strip()
        if configured.lower() in {"off", "0", "false", "no", "none"}:
            return cls(None, source="off")
        if configured:
            found = shutil.which(configured) or (configured if os.path.isfile(configured) else None)
            return cls(found, source="env" if found else "env_not_found")
        found = shutil.which("py-spy")
        return cls(found, source="path" if found else "not_found")

    @property
    def available(self) -> bool:
        return self.executable is not None

    def status(self) -> dict[str, Any]:
        with self._done:
            last = self._last
            return {
                "available": self.available,
                "executable": self.executable,
                "source": self.source,
                "env": STALL_PROFILER_ENV,
                "runs": self._runs,
                "failures": self._failures,
                "last_status": last.get("status") if last else None,
                "last_error": last.get("error") if last else None,
            }

    def trigger(self, pid: int) -> bool:
        """Start a dump unless one is running or the last began too recently."""
        if self.executable is None:
            return False
        now = self._monotonic()
        with self._done:
            if self._in_flight_since is not None or now - self._last_started < self._min_interval:
                return False
            self._in_flight_since = now
            self._last_started = now
        threading.Thread(
            target=self._dump, args=(pid, now), name="stall-external-dump", daemon=True
        ).start()
        return True

    def _dump(self, pid: int, started: float) -> None:
        result: dict[str, Any] = {"at": started}
        executable = self.executable or ""
        try:
            completed = self._run_command(
                [executable, "dump", "--pid", str(pid), "--nonblocking"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._timeout,
                creationflags=background_creation_flags(),
                env={**os.environ, "NO_COLOR": "1"},
            )
            if completed.returncode == 0:
                threads = parse_py_spy_dump(completed.stdout or "")
                result["status"] = "ok"
                result["threads"] = threads
            else:
                result["status"] = "error"
                result["error"] = (completed.stderr or completed.stdout or "").strip()[:300]
        except (OSError, subprocess.SubprocessError) as exc:
            result["status"] = "error"
            result["error"] = str(exc)[:300]
        result["seconds"] = round(self._monotonic() - started, 3)
        with self._done:
            self._last = result
            self._in_flight_since = None
            self._runs += 1
            if result["status"] != "ok":
                self._failures += 1
            self._done.notify_all()
        if result["status"] != "ok":
            log.warning("loop_stall external dump failed error=%s", result.get("error"))

    def result_since(self, started: float, *, wait: float) -> dict[str, Any] | None:
        """The dump begun at or after ``started``, waiting up to ``wait`` for one in flight.

        Bounded to the threads worth reading: every thread py-spy saw active, and the
        GIL holder first, each with its leading frames.
        """
        deadline = self._monotonic() + wait
        with self._done:
            while self._in_flight_since is not None and self._in_flight_since >= started:
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    return {"status": "pending"}
                self._done.wait(remaining)
            last = self._last
        if last is None or last["at"] < started:
            return None
        summary: dict[str, Any] = {"status": last["status"], "seconds": last.get("seconds")}
        if last["status"] != "ok":
            summary["error"] = last.get("error")
            return summary
        threads = list(last.get("threads") or [])
        active = [item for item in threads if str(item.get("state", "")).startswith("active")]
        active.sort(key=lambda item: "gil" not in str(item.get("state", "")))
        summary["thread_count"] = len(threads)
        summary["threads"] = [
            {**item, "frames": list(item.get("frames") or [])[:MAX_EXTERNAL_FRAMES]}
            for item in active[:MAX_EXTERNAL_THREADS]
        ]
        return summary


def _thread_work(work: Mapping[str, Any], ident: int) -> str | None:
    """What a thread spent the window's CPU on, or what it is running now.

    ``None`` for a thread that is not an accounted executor worker (the loop
    itself, a watch thread), whose work the stacks and the loop costs describe.
    """
    finished: dict[str, float] = work["by_thread"].get(ident) or {}
    running = work["running"].get(ident)
    if finished:
        label = max(finished, key=lambda name: finished[name])
        if running is not None and running[0] != label:
            return f"{label} (now {running[0]})"
        return label
    return f"{running[0]} (running)" if running is not None else None


def default_host_probe() -> dict[str, Any]:
    """Memory and process count at explanation time; a field that cannot be read is absent.

    Host CPU is not read here: it is measured across the stall window from the
    canary's samples (`host_metrics.host_cpu_times`). Both reads release the GIL on
    Windows, where psutil's equivalents held it for 55 ms per explanation.
    """
    probe: dict[str, Any] = {}
    memory = host_memory_percent()
    if memory is not None:
        probe["memory_percent"] = memory
    processes = host_process_count()
    if processes is not None:
        probe["process_count"] = processes
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
        beat_interval: float = 0.0,
        cpu_reader: ThreadCpuReader | None = None,
        external_dumper: ExternalStackDumper | None = None,
        resolve_external_dumper: Callable[[], ExternalStackDumper] | None = None,
        work_ledger: WorkLedger | None = None,
    ) -> None:
        """``resolve_external_dumper`` runs on the canary thread when it starts.

        Resolving an executable searches ``PATH``, which is filesystem work the
        event loop that constructs the watchdog must not do.
        """
        self.trace_path = trace_path
        self.threshold = threshold
        self._monotonic = monotonic
        self._host_probe = host_probe
        self._rotate_bytes = rotate_bytes
        #: How long the loop sleeps between beats when nothing is wrong. A capture
        #: fires once the loop is `threshold` *late*, which is this much more than
        #: `threshold` after the last beat; without it a loop merely running slow
        #: was captured as stalled, and its dump later explained a different stall.
        self._beat_interval = max(0.0, beat_interval)
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
        #: Built on the canary thread when not injected (see `_canary`).
        self._cpu_reader: ThreadCpuReader | None = cpu_reader
        self._external = external_dumper
        self._resolve_external = resolve_external_dumper
        self._work = work_ledger if work_ledger is not None else LEDGER
        self._sample_lock = threading.Lock()
        self._cpu_samples: deque[CpuSample] = deque(maxlen=CPU_HISTORY)
        self._next_cpu_sample = 0.0
        self._ready_samples: deque[tuple[float, int]] = deque(maxlen=READY_HISTORY)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_ident: int | None = None
        self._verdicts: dict[str, int] = {}

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

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Name the loop being watched; call from that loop's own thread.

        Its ready queue is read (racily, length only) while it is overdue, and its
        thread is the one whose CPU share decides the verdict.
        """
        self._loop = loop
        self._loop_ident = threading.get_ident()

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
        with self._sample_lock:
            if self._cpu_reader is not None:
                self._cpu_reader.close()

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

    @property
    def _watched_ident(self) -> int:
        return self._loop_ident or threading.main_thread().ident or 0

    def _ready_depth(self) -> int | None:
        loop = self._loop
        ready = getattr(loop, "_ready", None) if loop is not None else None
        try:
            return len(ready) if ready is not None else None
        except TypeError:
            return None

    def _capture_if_stalled(self, now: float) -> None:
        overdue = now - self._last_beat - self._beat_interval
        if overdue < self.threshold or now - self._last_capture < self.threshold:
            return
        first_capture = self._last_capture < self._last_beat
        self._last_capture = now
        if first_capture and self._external is not None:
            self._external.trigger(os.getpid())
        self._sample_cpu(now)
        # sys._current_frames() returns owned Python frame references while holding
        # the GIL. Never use dump_traceback_later here: its native thread traverses
        # changing interpreter frames without the GIL and crashed the daemon on
        # 2026-09-07. A GIL-held stall may have no sample; report that limitation.
        lines = [
            f"# capture=python_gil_required pid={os.getpid()}",
            "Timeout (0:00:00)!",
            f"# captured_at={now:.6f}",
        ]
        depth = self._ready_depth()
        if depth is not None:
            lines.append(f"# ready_queue={depth}")
        marks = parked_threads()
        frames = sys._current_frames()
        try:
            # `sys._current_frames()` lists the newest thread first, so the loop's
            # thread - among the oldest - is exactly the one a bare cap drops. With
            # ~105 daemon threads it was missing from every dump of the 2026-10-22
            # hangs (`main=-`). The watched thread goes first; the rest keep order.
            watched = self._watched_ident
            own = threading.get_ident()
            order = sorted((ident for ident in frames if ident != own), key=lambda i: i != watched)
            listed = order[:MAX_THREADS]
            lines.append(f"# threads={len(order)} listed={len(listed)}")
            for ident in listed:
                frame = frames[ident]
                reason = marks.get(ident)
                tag = f" [parked:{reason}]" if reason else ""
                lines.append(f"Thread 0x{ident:08x}{tag} (most recent call first):")
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

    # -- CPU and ready-queue sampling -----------------------------------------------

    def _sample_cpu(self, now: float) -> CpuSample | None:
        """Record every live thread's CPU, the process's and the host's, at ``now``."""
        reader = self._cpu_reader
        if reader is None or not reader.supported:
            return None
        with self._sample_lock:
            live = [
                thread
                for thread in threading.enumerate()
                if thread.ident is not None and thread.native_id is not None
            ]
            readings = reader.read({int(thread.native_id or 0): id(thread) for thread in live})
            sample = CpuSample(
                at=now,
                threads={
                    int(thread.ident or 0): readings[int(thread.native_id or 0)]
                    for thread in live
                    if int(thread.native_id or 0) in readings
                },
                names={int(thread.ident or 0): thread.name for thread in live},
                process=time.process_time(),
                host=host_cpu_times(),
            )
            self._cpu_samples.append(sample)
        return sample

    def _sample_ready(self, now: float) -> None:
        if self._loop is None or now - self._last_beat < READY_SAMPLE_AFTER_SECONDS:
            return
        depth = self._ready_depth()
        if depth is not None:
            with self._lock:
                self._ready_samples.append((now, depth))

    def _sample_after(self, ended: float) -> CpuSample | None:
        """The canary's first sample taken within an interval after the stall ended."""
        with self._sample_lock:
            for sample in self._cpu_samples:
                if ended <= sample.at <= ended + CPU_SAMPLE_INTERVAL_SECONDS:
                    return sample
        return None

    def _baseline(self, started: float, end: CpuSample) -> CpuSample | None:
        """The last sample before the stall began, or failing that the earliest in it.

        The fallback covers a canary starved from before the stall started, or a
        watchdog younger than the stall: a sample inside the window still measures
        most of it, and one that covers less than half is not used at all.
        """
        with self._sample_lock:
            samples = [sample for sample in self._cpu_samples if sample is not end]
        before = [sample for sample in samples if sample.at <= started]
        if before:
            return before[-1]
        inside = [sample for sample in samples if sample.at < end.at]
        if inside and end.at - inside[0].at >= 0.5 * (end.at - started):
            return inside[0]
        return None

    # -- the canary thread -------------------------------------------------------

    def _canary(self) -> None:
        # Built here rather than by the constructor, which runs on the event loop:
        # the POSIX reader lists the process's threads from /proc.
        if self._cpu_reader is None:
            with self._sample_lock:
                self._cpu_reader = ThreadCpuReader()
            log.info("loop stall thread_cpu supported=%s", self._cpu_reader.supported)
        if self._resolve_external is not None and self._external is None:
            try:
                self._external = self._resolve_external()
                log.info(
                    "loop stall external dump source=%s executable=%s",
                    self._external.source,
                    self._external.executable or "-",
                )
            except Exception:
                log.exception("loop stall external dump resolution failed")
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
                    self._sample_ready(after)
                    if after >= self._next_cpu_sample:
                        self._next_cpu_sample = after + CPU_SAMPLE_INTERVAL_SECONDS
                        self._sample_cpu(after)
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

    def clock(self) -> float:
        """The watchdog's monotonic clock, for a caller stamping when a stall ended."""
        return self._monotonic()

    def explain(self, lag_seconds: float, *, ended: float | None = None) -> StallRecord:
        """Build the record for a stall the loop just measured. Runs off the loop.

        ``ended`` is when the loop resumed, on `clock()`. The explanation runs on an
        executor thread that a loaded daemon may not schedule promptly, and without
        the stamp that delay would shift the window off the stall it explains.
        """
        now = self._monotonic()
        ended = now if ended is None else min(ended, now)
        started = ended - lag_seconds
        # Half the stall, floored at two canary intervals: a canary that slept
        # through the first quarter second of a stall still wakes late by nearly
        # all of it, and anything less than half is the canary running normally.
        canary_starved = self.canary_starved_since(
            started - CANARY_INTERVAL_SECONDS,
            max(2 * CANARY_INTERVAL_SECONDS, lag_seconds * 0.5),
        )
        end_sample = self._sample_after(ended) or self._sample_cpu(now)
        base = self._baseline(started, end_sample) if end_sample is not None else None
        with self._trace_lock:
            # A capture fires a threshold into the stall and repeats each threshold;
            # the canary's interval of slack covers its wake-up jitter.
            dumps = self._read_new_dumps(
                (started - CANARY_INTERVAL_SECONDS, ended + CANARY_INTERVAL_SECONDS)
            )
        loop_ident = self._watched_ident
        names = {t.ident: t.name for t in threading.enumerate() if t.ident is not None}
        window: float | None = None
        deltas: dict[int, float] | None = None
        loop_cpu = others_cpu = process_cpu = host_share = None
        if base is not None and end_sample is not None and end_sample.at > base.at:
            window = end_sample.at - base.at
            deltas = {}
            for ident, total in end_sample.threads.items():
                if ident in base.threads:
                    deltas[ident] = max(0.0, total - base.threads[ident])
                elif ident not in base.names:
                    deltas[ident] = max(0.0, total)  # born inside the window
            names = {**base.names, **end_sample.names, **names}
            loop_cpu = deltas.get(loop_ident)
            process_cpu = max(0.0, end_sample.process - base.process)
            listed = sum(value for ident, value in deltas.items() if ident != loop_ident)
            others_cpu = max(listed, process_cpu - (loop_cpu or 0.0))
            if base.host is not None and end_sample.host is not None:
                busy = end_sample.host[0] - base.host[0]
                total = end_sample.host[1] - base.host[1]
                host_share = min(1.0, max(0.0, busy / total)) if total > 0 else None
        with self._lock:
            ready = [depth for at, depth in self._ready_samples if at >= started]
        ready.extend(dump.ready_queue for dump in dumps if dump.ready_queue is not None)
        classification = classify_threads(
            dumps[0] if dumps else None,
            main_ident=loop_ident,
            names=names,
            cpu=deltas,
            window=window,
        )
        top: list[dict[str, Any]] = []
        if deltas and window:
            for ident, used in sorted(deltas.items(), key=lambda item: -item[1])[:TOP_CPU_THREADS]:
                if used < 0.01:
                    break
                top.append(
                    {
                        "name": names.get(ident, "?"),
                        "ident": ident,
                        "cpu_seconds": round(used, 3),
                        "share": round(used / window, 3),
                    }
                )
        work_start = base.at if base is not None else started
        work_end = end_sample.at if end_sample is not None else now
        work = self._work.window(work_start, work_end)
        for item in top:
            item["work"] = _thread_work(work, int(item["ident"]))
        top_work = [
            {"label": label, "count": int(count), "cpu_seconds": round(cpu, 3)}
            for label, (count, cpu) in sorted(
                work["by_label"].items(), key=lambda entry: -entry[1][1]
            )[:TOP_WORK]
            if cpu >= 0.01
        ]
        external = (
            self._external.result_since(started, wait=EXTERNAL_DUMP_WAIT_SECONDS)
            if self._external is not None
            else None
        )
        diagnosis = diagnose(
            window=window,
            loop_cpu=loop_cpu,
            others_cpu=others_cpu,
            process_cpu=process_cpu,
            host_share=host_share,
            ready_queue_max=max(ready) if ready else None,
            top_threads=top,
            top_work=top_work,
            loop_name=names.get(loop_ident, "MainThread"),
            loop_ident=loop_ident,
            classification=classification,
            external=external,
        )
        host = self._host_probe()
        if host_share is not None:
            host["cpu_percent"] = round(host_share * 100, 1)
        record = StallRecord(
            started_at=time.time() - lag_seconds,
            duration_seconds=lag_seconds,
            canary_starved=canary_starved,
            dumps=len(dumps),
            main_thread=classification.main,
            busy_threads=[
                {
                    "name": item.name,
                    "ident": item.ident,
                    "frames": item.frames,
                    "cpu_seconds": item.cpu_seconds,
                }
                for item in classification.busy
            ],
            host=host,
            trace_path=str(self.trace_path),
            diagnosis=diagnosis.as_dict(),
        )
        with self._lock:
            self._recent.appendleft(record)
            self._stall_count += 1
            self._verdicts[diagnosis.verdict] = self._verdicts.get(diagnosis.verdict, 0) + 1
        with self._trace_lock:
            self._note(
                f"{END_MARKER} duration_s={lag_seconds:.2f} verdict={diagnosis.verdict} "
                f"canary_starved={canary_starved} dumps={len(dumps)} "
                f"main={record.main_leaf or '-'}"
            )
            self._rotate_if_large()
        return record

    def _read_new_dumps(self, window: tuple[float, float] | None = None) -> list[StallDump]:
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
        return parse_stall_dumps(text, full=1, window=window)

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

    def profiler_status(self) -> dict[str, Any]:
        """Whether stalls get CPU attribution and an external dump on this host."""
        external = (
            self._external.status()
            if self._external is not None
            else {"available": False, "source": "disabled", "env": STALL_PROFILER_ENV}
        )
        reader = self._cpu_reader
        return {
            "thread_cpu": reader.supported if reader is not None else None,
            "external_dump": external,
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            recent = [record.as_dict() for record in self._recent]
            canary_worst = self._canary_worst
            count = self._stall_count
            verdicts = dict(self._verdicts)
        return {
            "threshold_seconds": self.threshold,
            "armed": self._armed,
            "capture_mode": "python_gil_required",
            "gil_held_stacks_available": False,
            "trace_path": str(self.trace_path),
            "stalls_explained": count,
            "verdicts": verdicts,
            "canary_worst_late_seconds": round(canary_worst, 4),
            "profiler": self.profiler_status(),
            "recent": recent,
        }
