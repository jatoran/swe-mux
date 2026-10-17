"""The files one Windows process holds open, without touching the system handle table.

`psutil.Process.open_files()` answers this by asking the kernel for *every handle
in the system* (`SystemExtendedHandleInformation`) and filtering by pid. On the
operator's host that is ~450,000 handles per call; measured 2026-10-12 at
250 ms for one call alone and ~1 s with four concurrent, all of it holding the
GIL, which is how ten idle Codex panes probing their own rollout every 10 s
starved the daemon's event loop for 622 s in one day.

This module asks the one question that is needed: `NtQueryInformationProcess`
with `ProcessHandleInformation` (Windows 8+) returns the target process's own
handle table, a few hundred entries. Each entry carries its object type and
granted access, so only File objects that could have been opened for writing
are named at all.

Naming a handle is the one call that can hang: a query on a synchronous file
object (a pipe with a read pending, typically) waits for that I/O to finish.
Names are therefore resolved on a dedicated worker thread with a per-handle
timeout. A query that overruns is *abandoned*, never killed - the worker keeps
the duplicated handle, closes it when the kernel finally answers, and exits -
because terminating a thread inside a kernel call can strand whatever lock it
held. While too many abandoned workers are still stuck, the query refuses
(`None`) rather than piling up more threads, and an abandoned or refused query
always reports itself incomplete so a caller that needs certainty fails closed.

ctypes releases the GIL around every foreign call, so none of this starves the
event loop even when it is slow.
"""

from __future__ import annotations

import ctypes
import logging
import os
import queue
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass, field

from .parked_threads import parked

log = logging.getLogger(__name__)

_PROCESS_HANDLE_INFORMATION = 51
_STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
_PROCESS_QUERY_INFORMATION = 0x0400
_PROCESS_DUP_HANDLE = 0x0040
_DUPLICATE_SAME_ACCESS = 0x00000002
_FILE_WRITE_DATA = 0x0002
_FILE_APPEND_DATA = 0x0004
_VOLUME_NAME_DOS = 0x0

#: Granted-access masks that, on a File object, are characteristically a pipe or
#: console whose name query blocks behind a pending synchronous read. The first
#: four are the long-standing set other handle tools skip; the last two were
#: measured 2026-10-12 across 150 live node/python/claude/codex processes, where
#: they were the only masks whose name query hung (node's `0x16019F` stdio pipes,
#: and read-only `0x120089` stdin pipes), leaving workers blocked in the kernel for
#: as long as the target lived. They are never named. The cost is that an ordinary
#: disk file opened with the same generic read/write mask (SQLite's databases,
#: for one) is invisible here, which is why callers must treat "not listed" as
#: "not proven" and never as "not open". A Codex rollout is opened for append
#: (`0x120116` measured), which none of these match.
HANG_PRONE_ACCESS = frozenset(
    {0x0012019F, 0x001A019F, 0x00120189, 0x00100000, 0x0016019F, 0x00120089}
)

#: How long one name query may take before it is abandoned.
NAME_QUERY_TIMEOUT_SECONDS = 0.1
#: Wall-clock budget for one whole process; overrun answers "incomplete".
QUERY_BUDGET_SECONDS = 1.5
#: Abandoned workers still blocked in the kernel before every query is refused.
#: A hang quarantines only its own target process (`_quarantined`), and a query
#: stops at its first hang, so this bounds threads, not coverage: it is reached
#: only if this many distinct processes are each holding a worker at once.
MAX_STUCK_WORKERS = 16
#: Initial and maximum buffer for one process's handle snapshot.
_SNAPSHOT_INITIAL_BYTES = 64 * 1024
_SNAPSHOT_MAX_BYTES = 64 * 1024 * 1024


class _HandleEntry(ctypes.Structure):
    _fields_ = [
        ("HandleValue", ctypes.c_void_p),
        ("HandleCount", ctypes.c_size_t),
        ("PointerCount", ctypes.c_size_t),
        ("GrantedAccess", wintypes.DWORD),
        ("ObjectTypeIndex", wintypes.ULONG),
        ("HandleAttributes", wintypes.ULONG),
        ("Reserved", wintypes.ULONG),
    ]


_HEADER_BYTES = 2 * ctypes.sizeof(ctypes.c_size_t)

_ntdll = ctypes.WinDLL("ntdll")
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_NtQueryInformationProcess = _ntdll.NtQueryInformationProcess
_NtQueryInformationProcess.argtypes = [
    wintypes.HANDLE,
    wintypes.ULONG,
    ctypes.c_void_p,
    wintypes.ULONG,
    ctypes.POINTER(wintypes.ULONG),
]
_NtQueryInformationProcess.restype = ctypes.c_long

_OpenProcess = _kernel32.OpenProcess
_OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_OpenProcess.restype = wintypes.HANDLE

_CloseHandle = _kernel32.CloseHandle
_CloseHandle.argtypes = [wintypes.HANDLE]
_CloseHandle.restype = wintypes.BOOL

_GetCurrentProcess = _kernel32.GetCurrentProcess
_GetCurrentProcess.argtypes = []
_GetCurrentProcess.restype = wintypes.HANDLE

_DuplicateHandle = _kernel32.DuplicateHandle
_DuplicateHandle.argtypes = [
    wintypes.HANDLE,
    wintypes.HANDLE,
    wintypes.HANDLE,
    ctypes.POINTER(wintypes.HANDLE),
    wintypes.DWORD,
    wintypes.BOOL,
    wintypes.DWORD,
]
_DuplicateHandle.restype = wintypes.BOOL

_GetFinalPathNameByHandleW = _kernel32.GetFinalPathNameByHandleW
_GetFinalPathNameByHandleW.argtypes = [
    wintypes.HANDLE,
    wintypes.LPWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
]
_GetFinalPathNameByHandleW.restype = wintypes.DWORD


def _snapshot(process: int | None) -> list[tuple[int, int, int]]:
    """``(handle value, granted access, object type index)`` for every handle."""
    size = _SNAPSHOT_INITIAL_BYTES
    while size <= _SNAPSHOT_MAX_BYTES:
        buffer = ctypes.create_string_buffer(size)
        needed = wintypes.ULONG(0)
        status = _NtQueryInformationProcess(
            process, _PROCESS_HANDLE_INFORMATION, buffer, size, ctypes.byref(needed)
        )
        if (status & 0xFFFFFFFF) == _STATUS_INFO_LENGTH_MISMATCH:
            size = max(size * 2, int(needed.value) + 4096)
            continue
        if status < 0:
            raise OSError(f"NtQueryInformationProcess failed: 0x{status & 0xFFFFFFFF:08X}")
        count = ctypes.c_size_t.from_buffer(buffer, 0).value
        if _HEADER_BYTES + count * ctypes.sizeof(_HandleEntry) > size:
            raise OSError("process handle snapshot is truncated")
        entries = (_HandleEntry * count).from_buffer(buffer, _HEADER_BYTES)
        return [
            (int(entry.HandleValue or 0), int(entry.GrantedAccess), int(entry.ObjectTypeIndex))
            for entry in entries
        ]
    raise OSError("process handle snapshot exceeds its bound")


_file_type_lock = threading.Lock()
_file_type_index: int | None = None


def file_object_type_index() -> int:
    """The kernel's object type index for File objects, learned once per process.

    Type indices are assigned at boot, so the honest way to learn the one for
    File is to open a file and look the handle up in this process's own table.
    The null device is a File object and touches no disk.
    """
    global _file_type_index
    with _file_type_lock:
        if _file_type_index is not None:
            return _file_type_index
        import msvcrt

        descriptor = os.open(os.devnull, os.O_RDONLY)
        try:
            handle = msvcrt.get_osfhandle(descriptor)
            for value, _access, type_index in _snapshot(_GetCurrentProcess()):
                if value == handle:
                    _file_type_index = type_index
                    return type_index
        finally:
            os.close(descriptor)
        raise OSError("could not learn the File object type index")


def _final_path(handle: int) -> str | None:
    length = 1024
    # unsupervised-loop-ok: grows a buffer until the name fits; not a daemon loop.
    while True:
        buffer = ctypes.create_unicode_buffer(length)
        written = _GetFinalPathNameByHandleW(handle, buffer, length, _VOLUME_NAME_DOS)
        if written == 0:
            return None
        if written < length:
            return _strip_device_prefix(buffer.value)
        length = written + 1


def _strip_device_prefix(path: str) -> str:
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


@dataclass
class _Query:
    handle: int
    done: threading.Event = field(default_factory=threading.Event)
    path: str | None = None


class _NameWorker:
    """One thread naming handles in order; retired after it overruns once."""

    def __init__(self) -> None:
        self.inbox: queue.SimpleQueue[_Query] = queue.SimpleQueue()
        self.retired = False
        #: The process whose handle this worker was abandoned on, if any.
        self.stuck_on: int | None = None
        self.thread = threading.Thread(target=self._run, name="open-file-names", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        # unsupervised-loop-ok: an OS thread serving one query at a time, which
        # cannot raise out of the loop and exits once retired; not asyncio.
        while True:
            with parked("open_file_names_inbox"):
                item = self.inbox.get()
            try:
                item.path = _final_path(item.handle)
            except OSError:
                item.path = None
            except Exception:
                # Unexpected (a ctypes failure). The query answers unnamed, which
                # the caller treats as incomplete, and the worker stays alive.
                log.warning("open_file_name_failed", exc_info=True)
                item.path = None
            finally:
                _CloseHandle(item.handle)
                item.done.set()
            if self.retired:
                return


_workers_lock = threading.Lock()
_active_worker: _NameWorker | None = None
_stuck_workers: list[_NameWorker] = []
_stats: dict[str, float] = {
    "queries": 0,
    "complete": 0,
    "abandoned_names": 0,
    "refused": 0,
    "skipped_handles": 0,
    "worst_seconds": 0.0,
}


def _prune_stuck() -> None:
    _stuck_workers[:] = [w for w in _stuck_workers if w.thread.is_alive()]


def _quarantined(pid: int) -> bool:
    """A query is refused for a process that has a worker still stuck on it.

    A hang is a property of one target's handle (a pipe with a read pending for
    as long as that process lives), so it quarantines that process rather than
    every query: one CLI holding a blocked pipe must not blind the daemon to the
    rest of the fleet. The global bound on stuck threads is the backstop.
    """
    with _workers_lock:
        _prune_stuck()
        return len(_stuck_workers) >= MAX_STUCK_WORKERS or any(
            worker.stuck_on == pid for worker in _stuck_workers
        )


def _worker() -> _NameWorker:
    global _active_worker
    with _workers_lock:
        if _active_worker is None:
            _active_worker = _NameWorker()
        return _active_worker


def _abandon(worker: _NameWorker, pid: int) -> None:
    global _active_worker
    with _workers_lock:
        worker.retired = True
        worker.stuck_on = pid
        if _active_worker is worker:
            _active_worker = None
        _stuck_workers.append(worker)
        _stats["abandoned_names"] += 1


@dataclass(frozen=True)
class OpenFiles:
    """What one query saw. ``complete`` is False if any candidate went unnamed."""

    paths: tuple[str, ...]
    complete: bool


def open_file_paths(pid: int, *, writable_only: bool = True) -> OpenFiles | None:
    """The disk files ``pid`` holds open, by final path.

    ``writable_only`` restricts to handles granted write or append access, which
    is every file a writer is producing and excludes the read-only handles most
    processes hold by the hundred. ``None`` means the process could not be
    queried at all (gone, inaccessible, or queries are being refused).
    """
    # One query at a time: the name worker answers in order, so two interleaved
    # queries would charge each other's latency to their own per-handle timeout.
    with _query_lock:
        return _open_file_paths(pid, writable_only=writable_only)


_query_lock = threading.Lock()


def _open_file_paths(pid: int, *, writable_only: bool) -> OpenFiles | None:
    started = time.perf_counter()
    if _quarantined(pid):
        with _workers_lock:
            _stats["refused"] += 1
        return None
    file_type = file_object_type_index()
    process = _OpenProcess(_PROCESS_QUERY_INFORMATION | _PROCESS_DUP_HANDLE, False, pid)
    if not process:
        return None
    try:
        try:
            entries = _snapshot(process)
        except OSError:
            return None
        candidates = [
            value
            for value, access, type_index in entries
            if type_index == file_type
            and access not in HANG_PRONE_ACCESS
            and (not writable_only or access & (_FILE_WRITE_DATA | _FILE_APPEND_DATA))
        ]
        skipped = sum(
            1
            for _value, access, type_index in entries
            if type_index == file_type and access in HANG_PRONE_ACCESS
        )
        paths: list[str] = []
        complete = True
        deadline = time.monotonic() + QUERY_BUDGET_SECONDS
        current = _GetCurrentProcess()
        for value in candidates:
            if time.monotonic() >= deadline:
                complete = False
                break
            duplicate = wintypes.HANDLE()
            if not _DuplicateHandle(
                process, value, current, ctypes.byref(duplicate), 0, False, _DUPLICATE_SAME_ACCESS
            ):
                # Closed between the snapshot and now: not open, so not missing.
                continue
            worker = _worker()
            item = _Query(int(duplicate.value or 0))
            worker.inbox.put(item)
            if not item.done.wait(NAME_QUERY_TIMEOUT_SECONDS):
                # The worker owns the duplicate now and closes it when the kernel
                # answers. Stop here: the next handle could hang the same way.
                _abandon(worker, pid)
                complete = False
                break
            if item.path:
                paths.append(item.path)
    finally:
        _CloseHandle(process)
    elapsed = time.perf_counter() - started
    with _workers_lock:
        _stats["queries"] += 1
        _stats["complete"] += 1 if complete else 0
        _stats["skipped_handles"] += skipped
        _stats["worst_seconds"] = max(_stats["worst_seconds"], elapsed)
    if not complete:
        log.warning(
            "open_files_incomplete pid=%d named=%d candidates=%d elapsed_s=%.3f",
            pid,
            len(paths),
            len(candidates),
            elapsed,
            extra={"open_files_pid": pid, "open_files_elapsed_s": round(elapsed, 3)},
        )
    return OpenFiles(tuple(paths), complete)


def query_stats() -> dict[str, float | int]:
    """Counters for the diagnostics endpoint."""
    with _workers_lock:
        return {
            **{
                key: (round(value, 4) if key == "worst_seconds" else int(value))
                for key, value in _stats.items()
            },
            "stuck_workers": sum(1 for w in _stuck_workers if w.thread.is_alive()),
        }
