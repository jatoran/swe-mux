"""Win32 job object ensuring PTY children cannot outlive the daemon."""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x0800
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9


class IO_COUNTERS(ctypes.Structure):
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


class BASIC_LIMITS(ctypes.Structure):
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


class EXTENDED_LIMITS(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", BASIC_LIMITS),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class ReaperJob:
    def __init__(self) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32 = kernel32
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        self._handle = kernel32.CreateJobObjectW(None, None)
        if not self._handle:
            self._raise("CreateJobObjectW")
        info = EXTENDED_LIMITS()
        # BREAKAWAY_OK does not weaken containment: children still land in the
        # job by default and only escape when a spawner explicitly requests
        # CREATE_BREAKAWAY_FROM_JOB. That escape hatch exists for exactly one
        # reason: a daemon/tray relaunch initiated from a shell *inside* a
        # session (redeploy_desktop.py, ensure_daemon) must not inherit that
        # session's kill-on-close job, or removing the session later silently
        # terminates the whole app.
        info.BasicLimitInformation.LimitFlags = (
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_BREAKAWAY_OK
        )
        if not kernel32.SetInformationJobObject(
            self._handle,
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            self.close()
            self._raise("SetInformationJobObject")

    def assign(self, pid: int) -> None:
        access = PROCESS_TERMINATE | PROCESS_SET_QUOTA | PROCESS_QUERY_LIMITED_INFORMATION
        hproc = self._kernel32.OpenProcess(access, False, pid)
        if not hproc:
            self._raise("OpenProcess")
        try:
            if not self._kernel32.AssignProcessToJobObject(self._handle, hproc):
                self._raise("AssignProcessToJobObject")
        finally:
            self._kernel32.CloseHandle(hproc)

    def create_child(self) -> ReaperJob:
        """Create a nested per-session job beneath the daemon-wide reaper.

        Windows 8+ supports compatible nested jobs. The daemon-wide job remains the
        final kill-on-close boundary; the child supplies session-level ownership.
        """
        return ReaperJob()

    def close(self) -> None:
        if getattr(self, "_handle", None):
            self._kernel32.CloseHandle(self._handle)
            self._handle = None

    @staticmethod
    def _raise(operation: str) -> None:
        code = ctypes.get_last_error()
        raise OSError(code, f"{operation} failed: {ctypes.FormatError(code)}")


def process_in_job() -> bool | None:
    """Whether this process is inside any Windows Job object (None if unknowable).

    A daemon, tray, or supervisor that was (re)launched from a shell inside a
    session inherits that session's kill-on-close job and is silently
    terminated when the session is removed. Startup paths call this to leave a
    loud warning while the process is still healthy.
    """
    if sys.platform != "win32":
        return None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.IsProcessInJob.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.BOOL),
        ]
        kernel32.IsProcessInJob.restype = wintypes.BOOL
        result = wintypes.BOOL(0)
        if not kernel32.IsProcessInJob(
            kernel32.GetCurrentProcess(), None, ctypes.byref(result)
        ):
            return None
        return bool(result.value)
    except OSError:
        return None
