"""ConPTY-specific backend behaviour, moved out of `test_core` with the code.

These assertions were always about the Windows pseudoconsole rather than about the
shared host, and keeping them in `test_core` is what made the whole module
unimportable on any other target.
"""

from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="ConPTY is Windows-only")

if sys.platform == "win32":
    import swe_mux.pty_backend_windows as backend
    from swe_mux.pty_backend_windows import WindowsPtyProcess, create_pty


def test_conpty_creation_retries_only_the_private_pyo3_panic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class PanicException(BaseException):
        pass

    PanicException.__module__ = "pyo3_runtime"
    sentinel = object()
    attempts = 0

    def create(**_kwargs: int) -> object:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PanicException("transient ConPTY initialization failure")
        return sentinel

    monkeypatch.setattr("swe_mux.pty_backend_windows.winpty.PTY", create)
    monkeypatch.setattr("swe_mux.pty_backend_windows.time.sleep", lambda _seconds: None)

    assert create_pty(120, 30) is sentinel
    assert attempts == 2


def test_backend_reaps_a_delayed_replacement_console_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = WindowsPtyProcess(120, 30)
    process._root_started_at = 100.0
    process._console_host_pid = 10
    process._console_host_started_at = 100.0
    killed: list[tuple[int, float]] = []

    def kill_console_host(pid: int, started_at: float) -> bool:
        killed.append((pid, started_at))
        return pid == 20

    monkeypatch.setattr(process, "_kill_console_host", kill_console_host)
    monkeypatch.setattr(backend, "_console_host_children", lambda: {20: 100.1})
    try:
        process._reap_console_host()
    finally:
        backend._CLAIMED_CONSOLE_HOSTS.clear()

    assert killed == [(10, 100.0), (20, 100.1)]
