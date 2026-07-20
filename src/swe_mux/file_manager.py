from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def file_manager_command(
    path: Path, *, is_directory: bool, platform: str | None = None
) -> list[str]:
    target = str(path)
    current_platform = platform or sys.platform
    if current_platform == "win32":
        if is_directory:
            return ["explorer.exe", "/n,", target]
        return ["explorer.exe", f"/select,{target}"]
    if current_platform == "darwin":
        return ["open", target] if is_directory else ["open", "-R", target]
    return ["xdg-open", target if is_directory else str(path.parent)]


def _windows_explorer_handles() -> list[int]:
    from ctypes import wintypes

    user32: Any = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    handles: list[int] = []

    def collect(hwnd: int, _parameter: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        class_name = ctypes.create_unicode_buffer(64)
        if user32.GetClassNameW(hwnd, class_name, len(class_name)):
            if class_name.value in {"CabinetWClass", "ExploreWClass"}:
                handles.append(hwnd)
        return True

    callback = callback_type(collect)
    user32.EnumWindows(callback, 0)
    return handles


def _bring_windows_window_forward(hwnd: int) -> None:
    from ctypes import wintypes

    user32: Any = ctypes.WinDLL("user32", use_last_error=True)
    window = wintypes.HWND(hwnd)
    no_move_or_size = 0x0001 | 0x0002 | 0x0040
    user32.ShowWindow(window, 9)  # SW_RESTORE
    # A short topmost pulse is reliable even when Windows declines a direct
    # SetForegroundWindow request from the daemon process.
    user32.SetWindowPos(window, wintypes.HWND(-1), 0, 0, 0, 0, no_move_or_size)
    user32.SetForegroundWindow(window)
    user32.SetWindowPos(window, wintypes.HWND(-2), 0, 0, 0, 0, no_move_or_size)


def _focus_launched_windows_explorer(previous_handles: set[int]) -> None:
    deadline = time.monotonic() + 2.0
    handles: list[int] = []
    while time.monotonic() < deadline:
        handles = _windows_explorer_handles()
        created = [handle for handle in handles if handle not in previous_handles]
        if created:
            _bring_windows_window_forward(created[0])
            return
        time.sleep(0.05)
    # Explorer can reuse an existing window despite its command-line switches.
    # EnumWindows is ordered from top to bottom, so raise its highest window.
    if handles:
        _bring_windows_window_forward(handles[0])


def open_in_file_manager(path: str | Path) -> None:
    target = Path(path).resolve()
    if not target.exists():
        raise ValueError("path does not exist")
    previous = set(_windows_explorer_handles()) if sys.platform == "win32" else set()
    subprocess.Popen(file_manager_command(target, is_directory=target.is_dir()))
    if sys.platform == "win32":
        _focus_launched_windows_explorer(previous)
