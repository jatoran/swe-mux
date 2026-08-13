"""Persistent, monitor-safe geometry for the Windows desktop shell."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Protocol

WINDOW_STATE_NAME = "desktop-window-state.json"
WINDOW_STATE_VERSION = 1
DEFAULT_WINDOW_WIDTH = 1440
DEFAULT_WINDOW_HEIGHT = 920
MIN_WINDOW_WIDTH = 760
MIN_WINDOW_HEIGHT = 480
WINDOW_STATE_DEBOUNCE_SECONDS = 0.3


@dataclass(frozen=True)
class DesktopWindowState:
    """Normal window bounds plus whether that normal rectangle is maximized."""

    x: int
    y: int
    width: int
    height: int
    maximized: bool = False
    version: int = WINDOW_STATE_VERSION


class ScreenArea(Protocol):
    x: int
    y: int
    width: int
    height: int


def _plain_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("window geometry values must be integers")
    return value


def load_window_state(path: Path) -> DesktopWindowState:
    """Read and strictly validate one desktop-window state file."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("desktop window state must be an object")
    version = _plain_int(payload.get("version"))
    if version != WINDOW_STATE_VERSION:
        raise ValueError(f"unsupported desktop window state version {version}")
    x = _plain_int(payload.get("x"))
    y = _plain_int(payload.get("y"))
    width = _plain_int(payload.get("width"))
    height = _plain_int(payload.get("height"))
    maximized = payload.get("maximized")
    if not isinstance(maximized, bool):
        raise ValueError("desktop window maximized state must be boolean")
    if not (-1_000_000 <= x <= 1_000_000 and -1_000_000 <= y <= 1_000_000):
        raise ValueError("desktop window position is outside the supported range")
    if not (1 <= width <= 100_000 and 1 <= height <= 100_000):
        raise ValueError("desktop window size is outside the supported range")
    return DesktopWindowState(x, y, width, height, maximized, version)


def save_window_state(path: Path, state: DesktopWindowState) -> None:
    """Atomically replace the desktop-window state file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(asdict(state), indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _overlap_area(state: DesktopWindowState, screen: ScreenArea) -> int:
    width = max(0, min(state.x + state.width, screen.x + screen.width) - max(state.x, screen.x))
    height = max(0, min(state.y + state.height, screen.y + screen.height) - max(state.y, screen.y))
    return width * height


def _distance_to_screen(state: DesktopWindowState, screen: ScreenArea) -> int:
    center_x = state.x + state.width // 2
    center_y = state.y + state.height // 2
    nearest_x = min(max(center_x, screen.x), screen.x + screen.width)
    nearest_y = min(max(center_y, screen.y), screen.y + screen.height)
    return (center_x - nearest_x) ** 2 + (center_y - nearest_y) ** 2


def fit_window_state(
    state: DesktopWindowState,
    screens: Sequence[ScreenArea],
    *,
    min_width: int = MIN_WINDOW_WIDTH,
    min_height: int = MIN_WINDOW_HEIGHT,
) -> DesktopWindowState:
    """Fit saved bounds into the current monitor working areas.

    The monitor with the largest overlap wins. If the saved monitor disappeared,
    the nearest remaining monitor wins. The entire normal rectangle is placed in
    that working area when possible, preventing a valid old state from restoring
    with its title bar off-screen.
    """

    usable = [screen for screen in screens if screen.width > 0 and screen.height > 0]
    if not usable:
        return replace(
            state,
            width=max(min_width, state.width),
            height=max(min_height, state.height),
        )

    overlap = [_overlap_area(state, screen) for screen in usable]
    if max(overlap) > 0:
        target = usable[overlap.index(max(overlap))]
    else:
        target = min(usable, key=lambda screen: _distance_to_screen(state, screen))

    width = max(min_width, state.width)
    height = max(min_height, state.height)
    if target.width >= min_width:
        width = min(width, target.width)
    if target.height >= min_height:
        height = min(height, target.height)
    max_x = target.x + max(0, target.width - width)
    max_y = target.y + max(0, target.height - height)
    return replace(
        state,
        x=min(max(state.x, target.x), max_x),
        y=min(max(state.y, target.y), max_y),
        width=width,
        height=height,
    )


class WindowStateRecorder:
    """Debounce pywebview geometry events into one durable normal rectangle."""

    def __init__(
        self,
        path: Path,
        initial: DesktopWindowState | None,
        *,
        debounce_seconds: float = WINDOW_STATE_DEBOUNCE_SECONDS,
        on_saved: Callable[[DesktopWindowState], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self.path = path
        self.debounce_seconds = debounce_seconds
        self.on_saved = on_saved
        self.on_error = on_error
        self._lock = threading.RLock()
        self._timer: threading.Timer | None = None
        self._closed = False
        self._x = initial.x if initial else None
        self._y = initial.y if initial else None
        self._width = initial.width if initial else DEFAULT_WINDOW_WIDTH
        self._height = initial.height if initial else DEFAULT_WINDOW_HEIGHT
        self._maximized = initial.maximized if initial else False
        self._minimized = False
        self._pending_x: int | None = None
        self._pending_y: int | None = None
        self._pending_width: int | None = None
        self._pending_height: int | None = None
        self._last_written = initial

    def _schedule_locked(self) -> None:
        if self._closed:
            return
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(self.debounce_seconds, self.flush)
        self._timer.daemon = True
        self._timer.start()

    def moved(self, x: int, y: int) -> None:
        with self._lock:
            if self._maximized or self._minimized:
                return
            self._pending_x = int(x)
            self._pending_y = int(y)
            self._schedule_locked()

    def resized(self, width: int, height: int) -> None:
        with self._lock:
            if self._maximized or self._minimized:
                return
            self._pending_width = int(width)
            self._pending_height = int(height)
            self._schedule_locked()

    def maximized(self) -> None:
        with self._lock:
            self._maximized = True
            self._minimized = False
            self._discard_pending_locked()
            self._schedule_locked()

    def minimized(self) -> None:
        with self._lock:
            self._minimized = True
            self._discard_pending_locked()
            self._schedule_locked()

    def restored(self) -> None:
        with self._lock:
            self._maximized = False
            self._minimized = False
            self._discard_pending_locked()
            self._schedule_locked()

    def _discard_pending_locked(self) -> None:
        self._pending_x = None
        self._pending_y = None
        self._pending_width = None
        self._pending_height = None

    def flush(self) -> DesktopWindowState | None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            if not self._maximized and not self._minimized:
                if self._pending_x is not None:
                    self._x = self._pending_x
                    self._y = self._pending_y
                if self._pending_width is not None and self._pending_height is not None:
                    self._width = self._pending_width
                    self._height = self._pending_height
            self._discard_pending_locked()
            if self._x is None or self._y is None:
                return None
            state = DesktopWindowState(
                self._x,
                self._y,
                self._width,
                self._height,
                self._maximized,
            )
            if state == self._last_written:
                return state
            try:
                save_window_state(self.path, state)
            except Exception as exc:
                if self.on_error is not None:
                    self.on_error(exc)
                return None
            self._last_written = state
            if self.on_saved is not None:
                self.on_saved(state)
            return state

    def close(self) -> None:
        self.flush()
        with self._lock:
            self._closed = True
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
