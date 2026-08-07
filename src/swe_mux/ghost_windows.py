"""Sweeper for headless-browser ghost windows (Windows only).

Chromium's ``--headless=new`` creates a real top-level ``Chrome_WidgetWin_1``
window sized to the viewport at the default origin and never sets
``WS_VISIBLE``. DWM composites the surface anyway, so the window paints onto
the desktop while every interaction path skips it: no taskbar button, no
Alt+Tab entry, ``WindowFromPoint`` misses it, and clicks fall through to
whatever is behind. The operator sees an opaque rectangle that cannot be
moved, focused, or closed.

Any automation stack that drives full Chrome in new-headless mode reproduces
this, so the sweep keys on the window signature rather than on a harness.
Puppeteer's default ``headless: true`` uses full Chrome and is affected;
Playwright escapes it by shipping ``chromium_headless_shell``, a binary that
creates no windows at all.

Remediation parks the window off-screen instead of closing it. ``WM_CLOSE``
destroys the agent's page and killing the process destroys its whole browser,
whereas relocation leaves both intact: a headless surface is captured from the
compositor and never reads its own screen coordinates.

Ownership: daemon-side service, started and stopped alongside the other
background loops in `server.py`. The supervisor is deliberately uninvolved
because a ghost is a desktop artifact rather than session state.
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
import sys
import time
from dataclasses import dataclass
from typing import Any

from .background_tasks import background

try:
    import psutil
except ImportError:  # pragma: no cover - diagnostics cover an unsynchronized dev venv
    psutil = None

log = logging.getLogger(__name__)

GHOST_WINDOW_SWEEP_LOOP = "ghost-window-sweep"

# The headless browser window itself. `Chrome_WidgetWin_0` is the hidden
# message-only window that every Chromium and Electron process owns (VS Code,
# Signal, Docker Desktop, and ordinary Chrome each have one) and is never a
# ghost, so matching on the class suffix is load-bearing.
GHOST_WINDOW_CLASS = "Chrome_WidgetWin_1"

# Only a browser launched for automation carries `--headless`. This is the
# discriminator that keeps legitimate Electron windows -- which can also be
# hidden, titled, and of class `Chrome_WidgetWin_1` -- out of the sweep.
HEADLESS_MARKER = "--headless"

# Far outside any physical desktop, and the conventional parking spot for
# Win32 windows that must exist without being seen.
PARK_POSITION = (-32000, -32000)

_MAX_RECENT = 50
_CLASS_BUFFER = 64

_SWP_NOSIZE = 0x0001
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010
_SWP_FLAGS = _SWP_NOSIZE | _SWP_NOZORDER | _SWP_NOACTIVATE

_SM_XVIRTUALSCREEN = 76
_SM_YVIRTUALSCREEN = 77
_SM_CXVIRTUALSCREEN = 78
_SM_CYVIRTUALSCREEN = 79

_RDW_INVALIDATE = 0x0001
_RDW_ERASE = 0x0004
_RDW_ALLCHILDREN = 0x0080
_RDW_UPDATENOW = 0x0100
_RDW_FRAME = 0x0400
_RDW_FLAGS = _RDW_INVALIDATE | _RDW_ERASE | _RDW_ALLCHILDREN | _RDW_UPDATENOW | _RDW_FRAME


@dataclass(frozen=True)
class GhostWindow:
    """A composited window that Windows reports as not visible."""

    hwnd: int
    pid: int
    title: str
    rect: tuple[int, int, int, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "hwnd": self.hwnd,
            "pid": self.pid,
            "title": self.title,
            "rect": list(self.rect),
        }


def supported(platform: str | None = None) -> bool:
    """Ghost windows are a Win32/DWM artifact and cannot occur elsewhere."""
    return (platform or sys.platform) == "win32"


def _intersects(rect: tuple[int, int, int, int], bounds: tuple[int, int, int, int]) -> bool:
    left, top, right, bottom = rect
    b_left, b_top, b_right, b_bottom = bounds
    return left < b_right and right > b_left and top < b_bottom and bottom > b_top


class GhostWindowSweeper:
    """Parks composited-but-invisible headless browser windows off-screen.

    The sweep is idempotent: a parked window no longer intersects the virtual
    screen, so it stops matching and is not touched again. That property is
    what makes a fixed-cadence loop safe to run against another process's
    windows indefinitely.
    """

    def __init__(self, *, cadence: float = 5.0, enabled: bool = True) -> None:
        self.cadence = cadence
        self.enabled = enabled
        self.swept_total = 0
        self.recent: list[dict[str, Any]] = []
        self._task: asyncio.Task[None] | None = None
        # (pid, create_time) -> is an automation browser. Reading another
        # process's command line is the expensive half of a sweep and a
        # browser's command line never changes; the creation time keeps a
        # recycled PID from inheriting a stale verdict.
        self._headless_pids: dict[tuple[int, float], bool] = {}

    @property
    def available(self) -> bool:
        return supported() and psutil is not None

    def start(self) -> None:
        if not self.available:
            log.debug(
                "ghost window sweeper inactive platform=%s psutil=%s",
                sys.platform,
                psutil is not None,
            )
            return
        if not self.enabled:
            log.info("ghost window sweeper disabled by configuration")
            return
        log.info("ghost window sweeper started cadence=%.1fs", self.cadence)
        self._task = background.start(GHOST_WINDOW_SWEEP_LOOP, self._run)

    async def stop(self) -> None:
        if self._task is None:
            return
        await background.stop(GHOST_WINDOW_SWEEP_LOOP)
        self._task = None
        log.info("ghost window sweeper stopped swept_total=%d", self.swept_total)

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.cadence)
            if not self.enabled:
                continue
            with background.iteration(GHOST_WINDOW_SWEEP_LOOP):
                await asyncio.to_thread(self.sweep)

    def _is_automation_browser(self, pid: int) -> bool:
        if psutil is None:
            return False
        try:
            process = psutil.Process(pid)
            key = (pid, process.create_time())
            cached = self._headless_pids.get(key)
            if cached is not None:
                return cached
            verdict = any(HEADLESS_MARKER in argument for argument in process.cmdline())
        except Exception:
            # A browser that exits mid-sweep, or one the daemon may not
            # inspect, is simply not swept. Retrying gains nothing.
            return False
        if len(self._headless_pids) >= 512:
            self._headless_pids.clear()
        self._headless_pids[key] = verdict
        return verdict

    def scan(self) -> list[GhostWindow]:
        """Return every headless browser window painting on the desktop."""
        if not self.available:
            return []
        user32: Any = ctypes.WinDLL("user32", use_last_error=True)
        bounds = (
            user32.GetSystemMetrics(_SM_XVIRTUALSCREEN),
            user32.GetSystemMetrics(_SM_YVIRTUALSCREEN),
            user32.GetSystemMetrics(_SM_XVIRTUALSCREEN)
            + user32.GetSystemMetrics(_SM_CXVIRTUALSCREEN),
            user32.GetSystemMetrics(_SM_YVIRTUALSCREEN)
            + user32.GetSystemMetrics(_SM_CYVIRTUALSCREEN),
        )
        from ctypes import wintypes

        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        found: list[GhostWindow] = []

        def collect(hwnd: int, _parameter: int) -> bool:
            # Ordered cheapest test first: a visible window is a real one, and
            # the class check rejects everything but a Chromium browser frame
            # before any cross-process work happens.
            if user32.IsWindowVisible(hwnd):
                return True
            class_name = ctypes.create_unicode_buffer(_CLASS_BUFFER)
            if not user32.GetClassNameW(hwnd, class_name, len(class_name)):
                return True
            if class_name.value != GHOST_WINDOW_CLASS:
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title, length + 1)
            rect = wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return True
            geometry = (rect.left, rect.top, rect.right, rect.bottom)
            if not _intersects(geometry, bounds):
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if not self._is_automation_browser(pid.value):
                return True
            found.append(GhostWindow(hwnd, pid.value, title.value, geometry))
            return True

        user32.EnumWindows(callback_type(collect), 0)
        return found

    def sweep(self) -> list[GhostWindow]:
        """Park every ghost off-screen and repaint the desktop behind them."""
        ghosts = self.scan()
        if not ghosts:
            return []
        user32: Any = ctypes.WinDLL("user32", use_last_error=True)
        parked: list[GhostWindow] = []
        for ghost in ghosts:
            moved = user32.SetWindowPos(
                ghost.hwnd, 0, PARK_POSITION[0], PARK_POSITION[1], 0, 0, _SWP_FLAGS
            )
            if not moved:
                log.warning(
                    "ghost window park failed hwnd=%d pid=%d error=%d title=%r",
                    ghost.hwnd,
                    ghost.pid,
                    ctypes.get_last_error(),
                    ghost.title,
                )
                continue
            parked.append(ghost)
            log.info(
                "ghost window parked hwnd=%d pid=%d rect=%s title=%r",
                ghost.hwnd,
                ghost.pid,
                ghost.rect,
                ghost.title,
            )
        if parked:
            # The vacated region holds the ghost's last composited frame until
            # something invalidates it, so the repaint is part of the fix
            # rather than cosmetic.
            user32.RedrawWindow(None, None, None, _RDW_FLAGS)
            self.swept_total += len(parked)
            now = time.time()
            self.recent.extend({"ts": now, **ghost.as_dict()} for ghost in parked)
            self.recent = self.recent[-_MAX_RECENT:]
        return parked

    def diagnostics(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "enabled": self.enabled,
            "cadence": self.cadence,
            "swept_total": self.swept_total,
            "recent": list(self.recent),
        }
