from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time

import pytest


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object contract")
def test_job_object_kills_child_after_forced_owner_exit() -> None:
    script = (
        "import os,subprocess;"
        "from swe_mux.win_jobobj import ReaperJob;"
        "job=ReaperJob();"
        "child=subprocess.Popen(['powershell.exe','-NoProfile','-Command','Start-Sleep 60']);"
        "job.assign(child.pid);"
        "print(child.pid,flush=True);"
        "os._exit(23)"
    )
    owner = subprocess.Popen(
        [sys.executable, "-c", script], stdout=subprocess.PIPE, text=True
    )
    assert owner.stdout is not None
    child_pid = int(owner.stdout.readline().strip())
    assert owner.wait(timeout=10) == 23

    process_query_limited_information = 0x1000
    synchronize = 0x00100000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.OpenProcess(
        process_query_limited_information | synchronize, False, child_pid
    )
    if handle:
        try:
            assert kernel32.WaitForSingleObject(handle, 5000) == 0
        finally:
            kernel32.CloseHandle(handle)
    else:
        # The process can disappear before OpenProcess, which is the desired result.
        time.sleep(0.05)
