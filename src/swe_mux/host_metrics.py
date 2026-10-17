"""CPU time per thread of this process, and the host's CPU, memory and process count.

The stall watchdog needs to say who ran during a stall, which a stack sample cannot:
a sample shows where every thread *is*, and in a GIL convoy every thread is in a
different innocent-looking place in every sample. CPU time is the measurement that
separates them - the thread that burned it held the GIL, and a loop thread that burned
none was waiting.

Windows reads each thread's times with `GetThreadTimes` on a cached handle, through a
ctypes library that keeps the GIL across the call. That is deliberate: the reads are
non-blocking kernel queries of a few microseconds, and releasing the GIL around each
of ~90 of them would cost a GIL round trip apiece - during a convoy, the very
condition being measured, that is up to the switch interval each. psutil's
`Process.threads()` is not used there because on Windows it snapshots every thread on
the host (`CreateToolhelp32Snapshot`), which is tens of thousands under a busy fleet.

Elsewhere `psutil.Process().threads()` reads the process's own task list, which is
cheap. `time.pthread_getcpuclockid` is not used: its documentation warns that an
expired thread id is undefined behaviour up to a segfault, and a thread can exit
between being listed and being read.

Host memory and process count are read the opposite way on Windows, through ctypes
calls that *release* the GIL, because psutil's equivalents do not: measured on the
operator's host with ~1,200 processes, `psutil.virtual_memory()` held the GIL for
29 ms and `psutil.pids()` for 27 ms per call, which made every stall explanation
add 55 ms of GIL hold to the convoy it was explaining.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Mapping
from typing import Any, Protocol

log = logging.getLogger(__name__)


class _TimesBackend(Protocol):
    def read(self, threads: Mapping[int, int]) -> dict[int, float]: ...

    def close(self) -> None: ...


class ThreadCpuReader:
    """Reads per-thread CPU seconds by native thread id; never raises.

    Not thread-safe: the Windows backend reuses its call buffers, so one reader is
    driven by one thread at a time (the watchdog serialises its samples).
    """

    def __init__(self) -> None:
        self._windows: _TimesBackend | None = None
        self._psutil_process: Any = None
        self.supported = False
        if sys.platform == "win32":
            try:
                self._windows = _WindowsThreadTimes()
                self.supported = True
            except (OSError, AttributeError) as exc:  # pragma: no cover - host-specific
                log.warning("thread_cpu unavailable error=%s", exc)
        else:
            try:
                import psutil

                self._psutil_process = psutil.Process()
                self._psutil_process.threads()
                self.supported = True
            except Exception as exc:  # noqa: BLE001 - optional diagnostics only
                log.warning("thread_cpu unavailable error=%s", exc)

    def read(self, threads: Mapping[int, int]) -> dict[int, float]:
        """CPU seconds (user plus kernel) for each ``native_id -> generation`` given.

        The generation is any value that changes when a native id is reused by a
        new thread (the caller passes the `Thread` object's `id`), so a handle cached
        for a thread that exited is never read on behalf of its successor. Threads
        that cannot be read are absent from the result rather than zero.
        """
        if not self.supported:
            return {}
        try:
            if self._windows is not None:
                return self._windows.read(threads)
            wanted = set(threads)
            return {
                int(item.id): float(item.user_time) + float(item.system_time)
                for item in self._psutil_process.threads()
                if int(item.id) in wanted
            }
        except Exception as exc:  # noqa: BLE001 - optional diagnostics only
            log.debug("thread_cpu read failed error=%s", exc)
            return {}

    def close(self) -> None:
        if self._windows is not None:
            self._windows.close()


def host_cpu_times() -> tuple[float, float] | None:
    """Host-wide ``(busy, total)`` CPU seconds summed over every logical CPU.

    Deltas of these over a window give the host's utilisation across exactly that
    window, which `psutil.cpu_percent(interval=None)` cannot: its baseline is the
    previous call *from the same thread*, so a probe that runs only after a stall
    measures whatever interval happened to precede it.
    """
    try:
        import psutil

        times = psutil.cpu_times()
    except Exception:  # noqa: BLE001 - optional diagnostics only
        return None
    values = times._asdict()
    total = float(sum(values.values()))
    # `guest` time is already counted inside `user` on Linux.
    total -= float(values.get("guest", 0.0)) + float(values.get("guest_nice", 0.0))
    idle = float(values.get("idle", 0.0)) + float(values.get("iowait", 0.0))
    return total - idle, total


def host_memory_percent() -> float | None:
    """Physical memory in use, as a percentage; ``None`` when it cannot be read."""
    if sys.platform == "win32":
        return _windows_memory_percent()
    try:
        import psutil

        return float(psutil.virtual_memory().percent)
    except Exception:  # noqa: BLE001 - optional diagnostics only
        return None


def host_process_count() -> int | None:
    """Processes on the host; ``None`` when they cannot be listed."""
    if sys.platform == "win32":
        return _windows_process_count()
    try:
        import psutil

        return len(psutil.pids())
    except Exception:  # noqa: BLE001 - optional diagnostics only
        return None


def process_start_stamp(pid: int) -> int | None:
    """An opaque stamp of when ``pid`` started; ``None`` when it cannot be read.

    Equal stamps for one pid mean the same process, which is what a cache keyed on
    a pid needs to refuse a recycled one. On Windows this is one `OpenProcess` and
    one `GetProcessTimes` with the GIL released; `psutil.Process` costs the same
    syscalls holding it, plus a host-wide pid enumeration when the pid is gone.
    """
    if pid <= 0:  # the idle process or the scheduler: never anything a caller tracks
        return None
    if sys.platform == "win32":
        return _windows_process_start_stamp(pid)
    try:
        import psutil

        return int(psutil.Process(pid).create_time() * 1_000_000)
    except Exception:  # noqa: BLE001 - a process that is gone or hidden has no stamp
        return None


def process_exited(pid: int) -> bool | None:
    """Whether ``pid`` names no running process: ``True``, ``False``, or ``None`` for unknown.

    For a caller about to build a `psutil.Process`. On Windows psutil learns that a
    pid is gone by enumerating every pid on the host with the GIL held - measured
    27 ms against 0.045 ms for a live pid on a 630-process host - and a process
    whose parent still holds a handle to it is still openable, so psutil enumerates
    for those too. Here it is one `OpenProcess` and one zero-timeout wait, with the
    GIL released. A process this one may not open is ``None``, as is every other
    host, where psutil's own answer for a missing pid is already cheap.
    """
    if pid <= 0:
        return None
    if sys.platform == "win32":
        return _windows_process_exited(pid)
    return None


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("length", wintypes.DWORD),
            ("memory_load", wintypes.DWORD),
            ("total_physical", ctypes.c_ulonglong),
            ("available_physical", ctypes.c_ulonglong),
            ("total_page_file", ctypes.c_ulonglong),
            ("available_page_file", ctypes.c_ulonglong),
            ("total_virtual", ctypes.c_ulonglong),
            ("available_virtual", ctypes.c_ulonglong),
            ("available_extended_virtual", ctypes.c_ulonglong),
        ]

    # WinDLL releases the GIL around every call (module docstring).
    _released = ctypes.WinDLL("kernel32", use_last_error=True)
    _GlobalMemoryStatusEx = _released.GlobalMemoryStatusEx
    _GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(_MemoryStatus)]
    _GlobalMemoryStatusEx.restype = wintypes.BOOL
    _EnumProcesses = _released.K32EnumProcesses
    _EnumProcesses.argtypes = [
        ctypes.POINTER(wintypes.DWORD),
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _EnumProcesses.restype = wintypes.BOOL

    def _windows_memory_percent() -> float | None:
        status = _MemoryStatus()
        status.length = ctypes.sizeof(_MemoryStatus)
        if not _GlobalMemoryStatusEx(ctypes.byref(status)) or not status.total_physical:
            return None
        total = int(status.total_physical)
        used = total - int(status.available_physical)
        return round(100.0 * used / total, 1)

    def _windows_process_count() -> int | None:
        capacity = 4096
        while capacity <= 1 << 20:
            pids = (wintypes.DWORD * capacity)()
            returned = wintypes.DWORD()
            size = ctypes.sizeof(pids)
            if not _EnumProcesses(pids, size, ctypes.byref(returned)):
                return None
            if returned.value < size:
                return returned.value // ctypes.sizeof(wintypes.DWORD)
            capacity *= 4  # a full buffer may have been truncated
        return None

    _THREAD_QUERY_LIMITED_INFORMATION = 0x0800

    class _FileTime(ctypes.Structure):
        _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

        def seconds(self) -> float:
            return ((int(self.high) << 32) | int(self.low)) / 10_000_000

        def ticks(self) -> int:
            return (int(self.high) << 32) | int(self.low)

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _OpenProcess = _released.OpenProcess
    _OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _OpenProcess.restype = wintypes.HANDLE
    _GetProcessTimes = _released.GetProcessTimes
    _GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(_FileTime)] * 4
    _GetProcessTimes.restype = wintypes.BOOL
    _CloseProcessHandle = _released.CloseHandle
    _CloseProcessHandle.argtypes = [wintypes.HANDLE]
    _CloseProcessHandle.restype = wintypes.BOOL

    def _windows_process_start_stamp(pid: int) -> int | None:
        handle = _OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            creation, exited, kernel, user = _FileTime(), _FileTime(), _FileTime(), _FileTime()
            if not _GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exited),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                return None
            return creation.ticks() or None
        finally:
            _CloseProcessHandle(handle)

    _SYNCHRONIZE = 0x00100000
    _ERROR_INVALID_PARAMETER = 87
    _WAIT_OBJECT_0 = 0
    _WAIT_TIMEOUT = 0x102
    _WaitForSingleObject = _released.WaitForSingleObject
    _WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _WaitForSingleObject.restype = wintypes.DWORD

    def _windows_process_exited(pid: int) -> bool | None:
        handle = _OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION | _SYNCHRONIZE, False, pid)
        if not handle:
            # No process object has this pid. Anything else (access denied, chiefly)
            # is a process that exists and is not ours to ask about.
            return True if ctypes.get_last_error() == _ERROR_INVALID_PARAMETER else None
        try:
            # A process object is signalled when it exits, and outlives that while
            # anyone holds a handle to it - so this, not openability, is the answer.
            # Not the exit code either: STILL_ACTIVE is also a legal exit code.
            state = _WaitForSingleObject(handle, 0)
        finally:
            _CloseProcessHandle(handle)
        if state == _WAIT_OBJECT_0:
            return True
        if state == _WAIT_TIMEOUT:
            return False
        return None

    class _WindowsThreadTimes:
        def __init__(self) -> None:
            # PyDLL keeps the GIL across each call (module docstring). On x64 there is
            # one calling convention, so the cdecl flag PyDLL carries is harmless; a
            # 32-bit interpreter needs stdcall and falls back to WinDLL.
            library: ctypes.CDLL
            if sys.maxsize > 2**32:
                library = ctypes.PyDLL("kernel32", use_last_error=True)
            else:  # pragma: no cover - swe-mux ships 64-bit only
                library = ctypes.WinDLL("kernel32", use_last_error=True)
            self._open = library.OpenThread
            self._open.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            self._open.restype = wintypes.HANDLE
            self._times = library.GetThreadTimes
            self._times.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(_FileTime)] * 4
            self._times.restype = wintypes.BOOL
            self._process_of = library.GetProcessIdOfThread
            self._process_of.argtypes = [wintypes.HANDLE]
            self._process_of.restype = wintypes.DWORD
            self._close = library.CloseHandle
            self._close.argtypes = [wintypes.HANDLE]
            self._close.restype = wintypes.BOOL
            self._pid = os.getpid()
            #: native id -> (handle, generation)
            self._handles: dict[int, tuple[int, int]] = {}
            self._created = _FileTime()
            self._exited = _FileTime()
            self._kernel = _FileTime()
            self._user = _FileTime()

        def _handle(self, native_id: int, generation: int) -> int | None:
            cached = self._handles.get(native_id)
            if cached is not None:
                if cached[1] == generation:
                    return cached[0]
                self._close(cached[0])
                del self._handles[native_id]
            handle = self._open(_THREAD_QUERY_LIMITED_INFORMATION, False, native_id)
            if not handle:
                return None
            if self._process_of(handle) != self._pid:
                # The id now belongs to a thread of another process.
                self._close(handle)
                return None
            self._handles[native_id] = (int(handle), generation)
            return int(handle)

        def read(self, threads: Mapping[int, int]) -> dict[int, float]:
            result: dict[int, float] = {}
            for native_id, generation in threads.items():
                handle = self._handle(native_id, generation)
                if handle is None:
                    continue
                if not self._times(
                    handle,
                    ctypes.byref(self._created),
                    ctypes.byref(self._exited),
                    ctypes.byref(self._kernel),
                    ctypes.byref(self._user),
                ):
                    self._close(handle)
                    del self._handles[native_id]
                    continue
                result[native_id] = self._kernel.seconds() + self._user.seconds()
            for native_id in [known for known in self._handles if known not in threads]:
                self._close(self._handles.pop(native_id)[0])
            return result

        def close(self) -> None:
            for handle, _generation in self._handles.values():
                self._close(handle)
            self._handles.clear()
