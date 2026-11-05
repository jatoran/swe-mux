"""Run a heavy daemon-owned batch command so it cannot starve the host it runs on.

On 2026-10-31 the scheduled `ccusage` refresh froze the operator's whole PC for
seconds at a time: it re-read 46 GB of transcripts on one thread per logical CPU,
briefly held ~10 GB of RAM, and ran at *normal* priority - the daemon runs above
normal, but Windows hands a child the parent's class only when that class is idle or
below normal, so every child of the daemon starts at normal, level with the browser
the operator was using. The work was legitimate; nothing told the OS it could wait.

A contained run tells it, through mechanisms the OS enforces rather than ones the
program has to cooperate with:

- **Windows.** The child is created suspended and placed in a Job object before its
  first instruction, so it and everything it starts are members:
  - the job pins every member's scheduling class to *idle*;
  - a hard CPU-rate cap bounds the whole tree's share of the machine, which is the
    only bound on a program that sizes its thread pool from the logical CPU count
    (Rust's `available_parallelism` on Windows reads `GetSystemInfo`, which ignores
    affinity, so an affinity mask would not have reduced ccusage's 32 workers);
  - the root's I/O priority is lowered to *very low* and its memory priority to
    *very low* while it is still suspended, so its reads queue behind interactive
    I/O and the pages it streams through are the first the memory manager
    repurposes. A child inherits both at creation (measured 2026-10-31: a grandchild
    started 0.17 s into the run already read very-low I/O). Neither is a job limit,
    so a member could raise its own; the job is re-read on every poll and any member
    not yet seen is lowered again;
  - kill-on-close makes the job the tree reap: closing it ends any member that
    outlived the run, including one that escaped the parent chain;
  - the job's own accounting (CPU time, bytes read, peak commit) is the progress and
    resource reading, cumulative across members that have already exited.
- **macOS.** `taskpolicy -b` runs the command at background QoS (throttled CPU and
  I/O; efficiency cores on Apple silicon).
- **Linux.** `ionice -c 3` (idle I/O class) and `nice -n 19`. They wrap the command
  rather than being applied after the spawn because on Linux both are per *thread*: a
  priority set on the pid after the program has started its workers reaches only the
  main thread.

What is deliberately not here: a memory ceiling. A job memory limit turns "slow" into
"failed" for the one run whose results are worth the most (a full rebuild), and the
caller bounds memory far more effectively by asking for less work.

Every mechanism is best-effort and reported. A containment that could not be applied
is a `degraded` reason on the run, logged once, never a refusal to run - the command
the operator configured still does its job, just without the guarantee.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .ui_messages import ui_message

try:
    import psutil
except ImportError:  # pragma: no cover - psutil is a base dependency
    psutil = None

log = logging.getLogger(__name__)

#: Hard ceiling on the contained tree's share of all logical CPUs (Windows job).
DEFAULT_CPU_PERCENT = 25

MECHANISM_JOB = "job"
MECHANISM_PRIORITY_CLASS = "priority_class"
MECHANISM_TASKPOLICY = "taskpolicy"
MECHANISM_NICE = "nice"
MECHANISM_NONE = "none"

#: How long a POSIX wrapper probe may take before it is treated as unusable.
_PROBE_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class ContainmentPolicy:
    """How a contained run is held back. Scheduling classes are fixed by design:
    a run that is contained at all is background work, and only the CPU share is a
    property of the particular command."""

    cpu_percent: int = DEFAULT_CPU_PERCENT

    def __post_init__(self) -> None:
        if not 1 <= self.cpu_percent <= 100:
            raise ValueError(ui_message("background_containment.58cc2009f1"))


BACKGROUND_BATCH = ContainmentPolicy()


@dataclass(frozen=True, slots=True)
class PreparedSpawn:
    """What to hand `create_subprocess_exec`: possibly wrapped argv, extra flags."""

    argv: list[str]
    creationflags: int
    mechanism: str
    degraded: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResourceSample:
    """One reading of the contained tree. Counters are cumulative for the run.

    ``None`` means "not measurable here", never zero. ``progress`` counts the polls
    on which any counter moved, so a caller compares two values for "did anything
    happen since" without knowing which counters this host has.
    """

    cpu_seconds: float | None
    read_bytes: int | None
    processes: int | None
    progress: int

    @property
    def measured(self) -> bool:
        return self.cpu_seconds is not None or self.read_bytes is not None


@dataclass(slots=True)
class ContainedRun:
    """The live handle on one contained run: poll it while it runs, close it once."""

    pid: int
    mechanism: str
    cpu_percent: int | None
    degraded: list[str] = field(default_factory=list)
    _backend: Any = None
    _progress: int = 0
    _last: tuple[Any, ...] | None = None
    _started: float = field(default_factory=time.monotonic)
    _closed: dict[str, Any] | None = None

    def poll(self) -> ResourceSample:
        """Read the tree's counters and hold back any member that appeared since."""
        cpu: float | None = None
        read: int | None = None
        processes: int | None = None
        try:
            if self._backend is not None:
                cpu, read, processes = self._backend.poll()
        except Exception as exc:  # noqa: BLE001 - a reading must never break the run
            log.debug("containment_poll_failed pid=%d error=%s", self.pid, exc)
        marker = (cpu, read, processes)
        if _advanced(self._last or (None, None, None), marker):
            self._progress += 1
        self._last = marker
        return ResourceSample(cpu, read, processes, self._progress)

    def close(self) -> dict[str, Any]:
        """Final accounting, then release the job (which ends any surviving member)."""
        if self._closed is not None:
            return self._closed
        sample = self.poll()
        peak: int | None = None
        try:
            if self._backend is not None:
                peak = self._backend.peak_memory()
        except Exception as exc:  # noqa: BLE001 - accounting is diagnostic only
            log.debug("containment_peak_failed pid=%d error=%s", self.pid, exc)
        try:
            if self._backend is not None:
                self._backend.close()
        except Exception as exc:  # noqa: BLE001 - releasing must not fail a finished run
            log.debug("containment_close_failed pid=%d error=%s", self.pid, exc)
        self._closed = {
            "mechanism": self.mechanism,
            "cpu_percent_cap": self.cpu_percent,
            "degraded": list(self.degraded),
            "cpu_seconds": None if sample.cpu_seconds is None else round(sample.cpu_seconds, 2),
            "read_bytes": sample.read_bytes,
            "peak_memory_bytes": peak,
            "processes": sample.processes,
            "wall_seconds": round(time.monotonic() - self._started, 2),
        }
        return self._closed


def _advanced(before: tuple[Any, ...], after: tuple[Any, ...]) -> bool:
    """Whether any counter grew. A count that fell (a member exited) is not progress."""
    for old, new in zip(before, after, strict=True):
        if new is not None and (old is None or new > old):
            return True
    return False


# --------------------------------------------------------------------------- #
# Spawn preparation                                                           #
# --------------------------------------------------------------------------- #

_posix_wrapper_cache: dict[str, list[str] | None] = {}


def _probe_wrapper(name: str, prefix: list[str]) -> list[str] | None:
    """``prefix`` if running ``prefix + [true]`` succeeds here, else ``None``; cached.

    A wrapper that cannot apply its priority (a container refusing `ioprio_set`, a
    BusyBox `ionice` without `-t`) would otherwise fail the very command it wraps.
    """
    if name in _posix_wrapper_cache:
        return _posix_wrapper_cache[name]
    true_path = shutil.which("true")
    usable: list[str] | None = None
    if true_path is not None:
        try:
            completed = subprocess.run(
                [*prefix, true_path],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=_PROBE_TIMEOUT_SECONDS,
                check=False,
            )
            usable = prefix if completed.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            usable = None
    _posix_wrapper_cache[name] = usable
    log.info("containment_wrapper_probe name=%s usable=%s", name, usable is not None)
    return usable


def reset_wrapper_cache() -> None:
    """Forget the POSIX wrapper probes. For tests."""
    _posix_wrapper_cache.clear()


def prepare_spawn(argv: Sequence[str], policy: ContainmentPolicy) -> PreparedSpawn:
    """Wrap ``argv`` and choose creation flags so the child starts held back."""
    command = list(argv)
    if sys.platform == "win32":
        return PreparedSpawn(
            argv=command,
            creationflags=_CREATE_SUSPENDED | _IDLE_PRIORITY_CLASS,
            mechanism=MECHANISM_JOB,
        )
    degraded: list[str] = []
    if sys.platform == "darwin":
        taskpolicy = shutil.which("taskpolicy")
        if taskpolicy is not None:
            prefix = _probe_wrapper("taskpolicy", [taskpolicy, "-b"])
            if prefix is not None:
                return PreparedSpawn([*prefix, *command], 0, MECHANISM_TASKPOLICY)
        degraded.append("taskpolicy_unavailable")
    prefix_parts: list[str] = []
    if sys.platform.startswith("linux"):
        ionice = shutil.which("ionice")
        found = None if ionice is None else _probe_wrapper("ionice", [ionice, "-c", "3", "-t"])
        if found is None:
            degraded.append("ionice_unavailable")
        else:
            prefix_parts.extend(found)
    nice = shutil.which("nice")
    found_nice = None if nice is None else _probe_wrapper("nice", [nice, "-n", "19"])
    if found_nice is None:
        degraded.append("nice_unavailable")
    else:
        prefix_parts.extend(found_nice)
    mechanism = MECHANISM_NICE if found_nice is not None else MECHANISM_NONE
    return PreparedSpawn([*prefix_parts, *command], 0, mechanism, tuple(degraded))


def attach(pid: int, prepared: PreparedSpawn, policy: ContainmentPolicy) -> ContainedRun:
    """Take ownership of a just-spawned child and let it run.

    On Windows the child is still suspended: it joins a configured job, its
    priorities are lowered, and only then is it resumed. A failure to contain it is
    recorded and the child is resumed anyway; a failure to *resume* it raises, and
    the caller reaps the suspended child rather than leaving it parked forever.
    """
    if sys.platform == "win32":
        run = ContainedRun(pid=pid, mechanism=MECHANISM_JOB, cpu_percent=policy.cpu_percent)
        backend = _WindowsJob(pid, policy, run.degraded)
        run._backend = backend
        if backend.job is None:
            run.mechanism = MECHANISM_PRIORITY_CLASS
            run.cpu_percent = None
        try:
            backend.resume()
        except OSError:
            backend.close()  # kill-on-close takes the suspended child with the job
            raise
    else:
        run = ContainedRun(
            pid=pid,
            mechanism=prepared.mechanism,
            cpu_percent=None,
            degraded=list(prepared.degraded),
        )
        run._backend = _PsutilTree(pid)
    level = logging.WARNING if run.degraded else logging.INFO
    log.log(
        level,
        "containment_attached pid=%d mechanism=%s cpu_cap=%s degraded=%s",
        pid,
        run.mechanism,
        run.cpu_percent,
        ",".join(run.degraded) or "-",
    )
    return run


# --------------------------------------------------------------------------- #
# POSIX accounting                                                            #
# --------------------------------------------------------------------------- #


class _PsutilTree:
    """Cumulative CPU and read counters over a process tree, summed per pid.

    Per pid rather than per poll, so a member that exits does not make the total
    fall and a later member's work read as "no progress".
    """

    def __init__(self, pid: int) -> None:
        self._pid = pid
        self._seen: dict[int, tuple[float, int | None]] = {}

    def poll(self) -> tuple[float | None, int | None, int | None]:
        if psutil is None:
            return None, None, None
        try:
            root = psutil.Process(self._pid)
            members = [root, *root.children(recursive=True)]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            members = []
        for member in members:
            try:
                times = member.cpu_times()
                cpu = float(times.user) + float(times.system)
                read: int | None = None
                counters = getattr(member, "io_counters", None)
                if counters is not None:
                    io = counters()
                    read = int(getattr(io, "read_chars", getattr(io, "read_bytes", 0)))
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
            self._seen[member.pid] = (cpu, read)
        if not self._seen:
            return None, None, None
        cpu_total = sum(value[0] for value in self._seen.values())
        reads = [value[1] for value in self._seen.values() if value[1] is not None]
        return cpu_total, (sum(reads) if reads else None), len(members)

    def peak_memory(self) -> int | None:
        return None

    def close(self) -> None:
        self._seen.clear()


# --------------------------------------------------------------------------- #
# Windows                                                                     #
# --------------------------------------------------------------------------- #

_CREATE_SUSPENDED = 0x00000004
_IDLE_PRIORITY_CLASS = 0x00000040

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _PROCESS_TERMINATE = 0x0001
    _PROCESS_SET_QUOTA = 0x0100
    _PROCESS_SET_INFORMATION = 0x0200
    _PROCESS_SUSPEND_RESUME = 0x0800
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    _JOB_OBJECT_LIMIT_PRIORITY_CLASS = 0x00000020
    _JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JOB_OBJECT_CPU_RATE_CONTROL_ENABLE = 0x1
    _JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP = 0x4

    _JobObjectBasicProcessIdList = 3
    _JobObjectBasicAndIoAccountingInformation = 8
    _JobObjectExtendedLimitInformation = 9
    _JobObjectCpuRateControlInformation = 15

    _ProcessMemoryPriority = 0  # PROCESS_INFORMATION_CLASS for SetProcessInformation
    _MEMORY_PRIORITY_VERY_LOW = 1
    _ProcessIoPriority = 33  # PROCESSINFOCLASS for NtSetInformationProcess
    _IO_PRIORITY_VERY_LOW = 0
    _ERROR_MORE_DATA = 234

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_uint64)
            for name in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class _BasicAccounting(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", wintypes.LARGE_INTEGER),
            ("TotalKernelTime", wintypes.LARGE_INTEGER),
            ("ThisPeriodTotalUserTime", wintypes.LARGE_INTEGER),
            ("ThisPeriodTotalKernelTime", wintypes.LARGE_INTEGER),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    class _BasicAndIoAccounting(ctypes.Structure):
        _fields_ = [("BasicInfo", _BasicAccounting), ("IoInfo", _IoCounters)]

    class _CpuRateControl(ctypes.Structure):
        _fields_ = [("ControlFlags", wintypes.DWORD), ("CpuRate", wintypes.DWORD)]

    _PID_LIST_CAPACITY = 64

    class _PidList(ctypes.Structure):
        _fields_ = [
            ("NumberOfAssignedProcesses", wintypes.DWORD),
            ("NumberOfProcessIdsInList", wintypes.DWORD),
            ("ProcessIdList", ctypes.c_size_t * _PID_LIST_CAPACITY),
        ]

    # WinDLL releases the GIL around every call; these all run on a spawn-loop
    # thread, never the daemon's loop.
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _ntdll = ctypes.WinDLL("ntdll")

    _CreateJobObjectW = _k32.CreateJobObjectW
    _CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _CreateJobObjectW.restype = wintypes.HANDLE
    _SetInformationJobObject = _k32.SetInformationJobObject
    _SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    _SetInformationJobObject.restype = wintypes.BOOL
    _QueryInformationJobObject = _k32.QueryInformationJobObject
    _QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _QueryInformationJobObject.restype = wintypes.BOOL
    _AssignProcessToJobObject = _k32.AssignProcessToJobObject
    _AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _AssignProcessToJobObject.restype = wintypes.BOOL
    _OpenProcess = _k32.OpenProcess
    _OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _OpenProcess.restype = wintypes.HANDLE
    _GetExitCodeProcess = _k32.GetExitCodeProcess
    _GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _GetExitCodeProcess.restype = wintypes.BOOL
    _STILL_ACTIVE = 259
    _STATUS_PROCESS_IS_TERMINATING = 0xC000010A
    _CloseHandle = _k32.CloseHandle
    _CloseHandle.argtypes = [wintypes.HANDLE]
    _CloseHandle.restype = wintypes.BOOL
    _SetProcessInformation = _k32.SetProcessInformation
    _SetProcessInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    _SetProcessInformation.restype = wintypes.BOOL
    _NtResumeProcess = _ntdll.NtResumeProcess
    _NtResumeProcess.argtypes = [wintypes.HANDLE]
    _NtResumeProcess.restype = ctypes.c_long
    _NtSetInformationProcess = _ntdll.NtSetInformationProcess
    _NtSetInformationProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.ULONG,
    ]
    _NtSetInformationProcess.restype = ctypes.c_long

    def _last_error(operation: str) -> str:
        code = ctypes.get_last_error()
        return f"{operation}_failed:{code}"

    def lower_process_priorities(handle: int) -> list[str]:
        """Lower one process's I/O and memory priority; returns what failed."""
        failures: list[str] = []
        io_priority = wintypes.ULONG(_IO_PRIORITY_VERY_LOW)
        status = _NtSetInformationProcess(
            handle, _ProcessIoPriority, ctypes.byref(io_priority), ctypes.sizeof(io_priority)
        )
        if status & 0xFFFFFFFF == _STATUS_PROCESS_IS_TERMINATING:
            # NTSTATUS confirms teardown even before an exit code is available.
            log.debug("containment_priority_target_terminating")
            return []
        if status != 0:
            failures.append(f"io_priority_failed:{status & 0xFFFFFFFF:#x}")
        memory_priority = wintypes.ULONG(_MEMORY_PRIORITY_VERY_LOW)
        if not _SetProcessInformation(
            handle,
            _ProcessMemoryPriority,
            ctypes.byref(memory_priority),
            ctypes.sizeof(memory_priority),
        ):
            failures.append(_last_error("memory_priority"))
        if failures:
            # A job member can finish after enumeration/open but before these
            # writes. Only a confirmed exit makes those failures irrelevant;
            # denied writes on a live or unreadable process remain degraded.
            exit_code = wintypes.DWORD()
            if (
                _GetExitCodeProcess(handle, ctypes.byref(exit_code))
                and exit_code.value != _STILL_ACTIVE
            ):
                log.debug("containment_priority_target_exited exit_code=%d", exit_code.value)
                return []
        return failures

    class _WindowsJob:
        """One job holding one contained tree. Owns the job handle; never raises
        from `poll`, `peak_memory` or `close`."""

        def __init__(self, pid: int, policy: ContainmentPolicy, degraded: list[str]) -> None:
            self.pid = pid
            self.job: int | None = None
            self._degraded = degraded
            self._lowered: set[int] = set()
            access = (
                _PROCESS_TERMINATE
                | _PROCESS_SET_QUOTA
                | _PROCESS_SET_INFORMATION
                | _PROCESS_SUSPEND_RESUME
                | _PROCESS_QUERY_LIMITED_INFORMATION
            )
            handle = _OpenProcess(access, False, pid)
            if not handle:
                raise OSError(ctypes.get_last_error(), f"OpenProcess({pid}) failed")
            self._process = int(handle)
            self._create_job(policy)
            for failure in lower_process_priorities(self._process):
                self._note(failure)
            self._lowered.add(pid)

        def _note(self, reason: str) -> None:
            if reason not in self._degraded:
                self._degraded.append(reason)

        def _create_job(self, policy: ContainmentPolicy) -> None:
            job = _CreateJobObjectW(None, None)
            if not job:
                self._note(_last_error("create_job"))
                return
            limits = _ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = (
                _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                | _JOB_OBJECT_LIMIT_PRIORITY_CLASS
                | _JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
            )
            limits.BasicLimitInformation.PriorityClass = _IDLE_PRIORITY_CLASS
            if not _SetInformationJobObject(
                job,
                _JobObjectExtendedLimitInformation,
                ctypes.byref(limits),
                ctypes.sizeof(limits),
            ):
                self._note(_last_error("job_limits"))
                _CloseHandle(job)
                return
            rate = _CpuRateControl()
            rate.ControlFlags = (
                _JOB_OBJECT_CPU_RATE_CONTROL_ENABLE | _JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP
            )
            # Units are 1/100 of a percent of every logical CPU's cycles.
            rate.CpuRate = policy.cpu_percent * 100
            if not _SetInformationJobObject(
                job, _JobObjectCpuRateControlInformation, ctypes.byref(rate), ctypes.sizeof(rate)
            ):
                self._note(_last_error("cpu_rate_cap"))
            if not _AssignProcessToJobObject(job, self._process):
                # The child keeps the idle class it was created with, and its
                # descendants inherit it; only the cap and the reap are lost.
                self._note(_last_error("job_assign"))
                _CloseHandle(job)
                return
            self.job = int(job)

        def resume(self) -> None:
            status = _NtResumeProcess(self._process)
            if status != 0:
                raise OSError(f"NtResumeProcess({self.pid}) failed: {status & 0xFFFFFFFF:#x}")

        def _members(self) -> list[int]:
            if self.job is None:
                return [self.pid]
            listing = _PidList()
            returned = wintypes.DWORD(0)
            if (
                not _QueryInformationJobObject(
                    self.job,
                    _JobObjectBasicProcessIdList,
                    ctypes.byref(listing),
                    ctypes.sizeof(listing),
                    ctypes.byref(returned),
                )
                and ctypes.get_last_error() != _ERROR_MORE_DATA
            ):
                return []
            count = min(int(listing.NumberOfProcessIdsInList), _PID_LIST_CAPACITY)
            return [int(listing.ProcessIdList[index]) for index in range(count)]

        def _lower_new_members(self) -> None:
            for member in self._members():
                if member in self._lowered:
                    continue
                self._lowered.add(member)
                handle = _OpenProcess(
                    _PROCESS_SET_INFORMATION | _PROCESS_QUERY_LIMITED_INFORMATION, False, member
                )
                if not handle:
                    continue  # exited between the listing and the open
                try:
                    for failure in lower_process_priorities(handle):
                        self._note(failure)
                finally:
                    _CloseHandle(handle)

        def poll(self) -> tuple[float | None, int | None, int | None]:
            if self.job is None:
                return None, None, None
            self._lower_new_members()
            accounting = _BasicAndIoAccounting()
            if not _QueryInformationJobObject(
                self.job,
                _JobObjectBasicAndIoAccountingInformation,
                ctypes.byref(accounting),
                ctypes.sizeof(accounting),
                None,
            ):
                return None, None, None
            basic = accounting.BasicInfo
            cpu = (int(basic.TotalUserTime) + int(basic.TotalKernelTime)) / 10_000_000
            return cpu, int(accounting.IoInfo.ReadTransferCount), int(basic.TotalProcesses)

        def peak_memory(self) -> int | None:
            if self.job is None:
                return None
            limits = _ExtendedLimits()
            if not _QueryInformationJobObject(
                self.job,
                _JobObjectExtendedLimitInformation,
                ctypes.byref(limits),
                ctypes.sizeof(limits),
                None,
            ):
                return None
            return int(limits.PeakJobMemoryUsed) or None

        def close(self) -> None:
            if self.job is not None:
                _CloseHandle(self.job)  # kill-on-close ends any surviving member
                self.job = None
            if self._process:
                _CloseHandle(self._process)
                self._process = 0
