from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from swe_mux import ghost_windows
from swe_mux.config import load_config, update_config
from swe_mux.ghost_windows import GhostWindow, GhostWindowSweeper

# ---------------------------------------------------------------------------
# supported()
# ---------------------------------------------------------------------------


def test_supported_is_true_only_for_win32() -> None:
    """Ghost windows are a Win32/DWM artifact; every other platform is exempt."""
    assert ghost_windows.supported("win32") is True
    assert ghost_windows.supported("linux") is False
    assert ghost_windows.supported("darwin") is False
    assert ghost_windows.supported("cygwin") is False


# ---------------------------------------------------------------------------
# _intersects: the invariant that makes the sweep idempotent
# ---------------------------------------------------------------------------

_VIRTUAL_SCREEN = (0, 0, 1920, 1080)


def test_intersects_true_for_an_onscreen_rect() -> None:
    assert ghost_windows._intersects((10, 10, 790, 590), _VIRTUAL_SCREEN) is True


def test_intersects_false_once_parked_offscreen() -> None:
    """A window relocated to PARK_POSITION must stop matching the sweep."""
    left, top = ghost_windows.PARK_POSITION
    parked_rect = (left, top, left + 800, top + 600)
    assert ghost_windows._intersects(parked_rect, _VIRTUAL_SCREEN) is False


@pytest.mark.parametrize(
    ("rect", "reason"),
    [
        ((-780, 10, 0, 590), "right edge exactly touches bounds.left"),
        ((1920, 10, 2700, 590), "left edge exactly touches bounds.right"),
        ((10, -580, 790, 0), "bottom edge exactly touches bounds.top"),
        ((10, 1080, 790, 1660), "top edge exactly touches bounds.bottom"),
    ],
)
def test_intersects_is_false_when_a_rect_only_touches_the_boundary(
    rect: tuple[int, int, int, int], reason: str
) -> None:
    """Edge-adjacency is not overlap: a `<=`/`>=` typo would make the sweep

    perpetually re-touch a window it just parked (or one merely adjacent to
    the desktop), breaking idempotency.
    """
    assert ghost_windows._intersects(rect, _VIRTUAL_SCREEN) is False, reason


def test_intersects_true_when_overlap_is_a_single_pixel() -> None:
    """One pixel past the boundary case above must flip back to an overlap."""
    assert ghost_windows._intersects((-780, 10, 1, 590), _VIRTUAL_SCREEN) is True


# ---------------------------------------------------------------------------
# GhostWindowSweeper: unavailable short-circuits
# ---------------------------------------------------------------------------


def test_scan_and_sweep_are_noops_when_unsupported_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ghost_windows, "supported", lambda platform=None: False)

    def _fail(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("no Win32 API may be touched when the sweeper is unavailable")

    monkeypatch.setattr(ghost_windows.ctypes, "WinDLL", _fail, raising=False)

    sweeper = GhostWindowSweeper()
    assert sweeper.scan() == []
    assert sweeper.sweep() == []
    assert sweeper.swept_total == 0
    assert sweeper.recent == []


def test_scan_and_sweep_are_noops_when_psutil_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even on a real win32 host, no psutil means no dependable process check."""
    monkeypatch.setattr(ghost_windows, "supported", lambda platform=None: True)
    monkeypatch.setattr(ghost_windows, "psutil", None)

    def _fail(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("no Win32 API may be touched when the sweeper is unavailable")

    monkeypatch.setattr(ghost_windows.ctypes, "WinDLL", _fail, raising=False)

    sweeper = GhostWindowSweeper()
    assert sweeper.available is False
    assert sweeper.scan() == []
    assert sweeper.sweep() == []


# ---------------------------------------------------------------------------
# start(): must never register a background task unless active and enabled
# ---------------------------------------------------------------------------


def test_start_registers_no_task_when_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ghost_windows, "supported", lambda platform=None: False)
    sweeper = GhostWindowSweeper(enabled=True)

    sweeper.start()

    assert sweeper._task is None


def test_start_registers_no_task_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Availability alone is not enough; the operator's `enabled=False` wins."""
    monkeypatch.setattr(ghost_windows, "supported", lambda platform=None: True)
    monkeypatch.setattr(ghost_windows, "psutil", SimpleNamespace())
    sweeper = GhostWindowSweeper(enabled=False)

    sweeper.start()

    assert sweeper._task is None


# ---------------------------------------------------------------------------
# _is_automation_browser: the Electron-vs-headless-Chrome discriminator
# ---------------------------------------------------------------------------


def _fake_psutil(records: dict[int, dict[str, Any]]) -> Any:
    class _Process:
        def __init__(self, pid: int) -> None:
            self._record = records[pid]

        def create_time(self) -> float:
            return self._record["create_time"]

        def cmdline(self) -> list[str]:
            self._record["calls"] += 1
            return self._record["cmdline"]

    return SimpleNamespace(Process=_Process)


def test_is_automation_browser_detects_the_headless_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    records = {
        100: {
            "create_time": 1000.0,
            "cmdline": ["chrome.exe", "--headless=new", "--remote-debugging-port=0"],
            "calls": 0,
        },
        200: {"create_time": 1000.0, "cmdline": ["Electron.exe", "--type=renderer"], "calls": 0},
    }
    monkeypatch.setattr(ghost_windows, "psutil", _fake_psutil(records))
    sweeper = GhostWindowSweeper()

    assert sweeper._is_automation_browser(100) is True
    assert sweeper._is_automation_browser(200) is False


def test_is_automation_browser_memoizes_per_pid_and_create_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = {
        100: {
            "create_time": 1000.0,
            "cmdline": ["chrome.exe", "--headless=new"],
            "calls": 0,
        },
        200: {"create_time": 1000.0, "cmdline": ["Electron.exe", "--type=renderer"], "calls": 0},
    }
    monkeypatch.setattr(ghost_windows, "psutil", _fake_psutil(records))
    sweeper = GhostWindowSweeper()

    assert sweeper._is_automation_browser(100) is True
    assert records[100]["calls"] == 1
    assert sweeper._is_automation_browser(100) is True
    assert records[100]["calls"] == 1, "a cached True verdict must not re-read the command line"

    # A `False` verdict is not falsy-in-cache: `cached is not None` must treat
    # it the same as a cached `True`, or every legitimate Electron window gets
    # its command line re-read on every single sweep forever.
    assert sweeper._is_automation_browser(200) is False
    assert records[200]["calls"] == 1
    assert sweeper._is_automation_browser(200) is False
    assert records[200]["calls"] == 1, "a cached False verdict must not re-read the command line"


def test_is_automation_browser_reevaluates_a_recycled_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows reuses PIDs; only (pid, create_time) together identify a process."""
    records = {
        100: {
            "create_time": 1000.0,
            "cmdline": ["chrome.exe", "--headless=new"],
            "calls": 0,
        }
    }
    monkeypatch.setattr(ghost_windows, "psutil", _fake_psutil(records))
    sweeper = GhostWindowSweeper()

    assert sweeper._is_automation_browser(100) is True
    assert records[100]["calls"] == 1

    # Same OS pid, a different (later) process: an ordinary Electron app that
    # happens to have been assigned pid 100 after the browser exited.
    records[100]["create_time"] = 2000.0
    records[100]["cmdline"] = ["Electron.exe", "--type=renderer"]

    assert sweeper._is_automation_browser(100) is False
    assert records[100]["calls"] == 2, (
        "a recycled pid must re-read the command line, not reuse pid 100's old verdict"
    )


# ---------------------------------------------------------------------------
# sweep(): bookkeeping
# ---------------------------------------------------------------------------


def _fake_user32(fail_hwnds: frozenset[int] = frozenset()) -> Any:
    class _User32:
        def __init__(self) -> None:
            self.set_window_pos_calls: list[tuple[int, int, int]] = []
            self.redraw_calls = 0

        def SetWindowPos(
            self,
            hwnd: int,
            _insert_after: int,
            x: int,
            y: int,
            _cx: int,
            _cy: int,
            _flags: int,
        ) -> int:
            self.set_window_pos_calls.append((hwnd, x, y))
            return 0 if hwnd in fail_hwnds else 1

        def RedrawWindow(self, *_args: Any, **_kwargs: Any) -> int:
            self.redraw_calls += 1
            return 1

    return _User32()


def _ghost(n: int) -> GhostWindow:
    return GhostWindow(
        hwnd=n,
        pid=1000 + n,
        title=f"ghost {n} - Google Chrome for Testing",
        rect=(10, 10, 810, 610),
    )


def test_sweep_never_touches_win32_when_scan_finds_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    sweeper = GhostWindowSweeper()
    monkeypatch.setattr(sweeper, "scan", lambda: [])

    def _fail(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("SetWindowPos/RedrawWindow must not run with nothing to sweep")

    monkeypatch.setattr(ghost_windows.ctypes, "WinDLL", _fail, raising=False)

    assert sweeper.sweep() == []
    assert sweeper.swept_total == 0
    assert sweeper.recent == []


def test_sweep_parks_windows_and_records_bookkeeping(monkeypatch: pytest.MonkeyPatch) -> None:
    ghosts = [_ghost(1), _ghost(2)]
    sweeper = GhostWindowSweeper()
    monkeypatch.setattr(sweeper, "scan", lambda: list(ghosts))
    fake_user32 = _fake_user32()
    monkeypatch.setattr(
        ghost_windows.ctypes, "WinDLL", lambda *_a, **_k: fake_user32, raising=False
    )

    parked = sweeper.sweep()

    assert parked == ghosts
    assert sweeper.swept_total == 2
    assert fake_user32.redraw_calls == 1
    assert fake_user32.set_window_pos_calls == [
        (1, ghost_windows.PARK_POSITION[0], ghost_windows.PARK_POSITION[1]),
        (2, ghost_windows.PARK_POSITION[0], ghost_windows.PARK_POSITION[1]),
    ]
    assert len(sweeper.recent) == 2
    assert sweeper.recent[0]["hwnd"] == 1
    assert sweeper.recent[0]["rect"] == [10, 10, 810, 610]
    assert "ts" in sweeper.recent[0]


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 window enumeration")
def test_sweep_skips_windows_that_fail_to_move(monkeypatch: pytest.MonkeyPatch) -> None:
    ghosts = [_ghost(1), _ghost(2)]
    sweeper = GhostWindowSweeper()
    monkeypatch.setattr(sweeper, "scan", lambda: list(ghosts))
    fake_user32 = _fake_user32(fail_hwnds=frozenset({2}))
    monkeypatch.setattr(
        ghost_windows.ctypes, "WinDLL", lambda *_a, **_k: fake_user32, raising=False
    )

    parked = sweeper.sweep()

    assert parked == [ghosts[0]]
    assert sweeper.swept_total == 1
    assert len(sweeper.recent) == 1
    assert sweeper.recent[0]["hwnd"] == 1
    # A repaint still happens: at least one window did move.
    assert fake_user32.redraw_calls == 1


def test_sweep_recent_is_capped_at_fifty_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    ghosts = [_ghost(n) for n in range(60)]
    sweeper = GhostWindowSweeper()
    monkeypatch.setattr(sweeper, "scan", lambda: list(ghosts))
    fake_user32 = _fake_user32()
    monkeypatch.setattr(
        ghost_windows.ctypes, "WinDLL", lambda *_a, **_k: fake_user32, raising=False
    )

    sweeper.sweep()

    assert sweeper.swept_total == 60
    assert len(sweeper.recent) == ghost_windows._MAX_RECENT
    # The oldest 10 of 60 were evicted; the newest 50 (hwnd 10..59) remain, in order.
    assert sweeper.recent[0]["hwnd"] == 10
    assert sweeper.recent[-1]["hwnd"] == 59


# ---------------------------------------------------------------------------
# GhostWindow.as_dict() / GhostWindowSweeper.diagnostics()
# ---------------------------------------------------------------------------


def test_ghost_window_as_dict_shape() -> None:
    window = GhostWindow(
        hwnd=42, pid=99, title="Example - Google Chrome for Testing", rect=(10, 10, 800, 600)
    )

    assert window.as_dict() == {
        "hwnd": 42,
        "pid": 99,
        "title": "Example - Google Chrome for Testing",
        "rect": [10, 10, 800, 600],
    }
    assert isinstance(window.as_dict()["rect"], list)


def test_diagnostics_shape_and_recent_is_not_the_live_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ghost_windows, "supported", lambda platform=None: True)
    monkeypatch.setattr(ghost_windows, "psutil", SimpleNamespace())
    sweeper = GhostWindowSweeper(cadence=2.5, enabled=True)

    diagnostics = sweeper.diagnostics()

    assert diagnostics == {
        "available": True,
        "enabled": True,
        "cadence": 2.5,
        "swept_total": 0,
        "recent": [],
    }
    diagnostics["recent"].append({"hwnd": 999})
    assert sweeper.recent == [], "diagnostics() must return a copy, not the sweeper's live list"


# ---------------------------------------------------------------------------
# Config: defaults and range validation
# ---------------------------------------------------------------------------


def test_ghost_window_sweep_config_defaults(tmp_path: Path) -> None:
    config = load_config(tmp_path / "config.toml")

    assert config.ghost_window_sweep_enabled is True
    assert config.ghost_window_poll_seconds == 5.0


@pytest.mark.parametrize("value", [0.0, 0.4, -1.0, 60.1, 120.0])
def test_ghost_window_poll_seconds_rejects_out_of_range_values(
    tmp_path: Path, value: float
) -> None:
    config = load_config(tmp_path / "config.toml")

    with pytest.raises(ValueError) as excinfo:
        update_config(config, {"ghost_window_poll_seconds": value})

    assert "ghost_window_poll_seconds" in excinfo.value.args[0]
    assert config.ghost_window_poll_seconds == 5.0


@pytest.mark.parametrize("value", [0.5, 10.0, 60.0])
def test_ghost_window_poll_seconds_accepts_in_range_values(tmp_path: Path, value: float) -> None:
    config = load_config(tmp_path / "config.toml")

    hot, restart = update_config(config, {"ghost_window_poll_seconds": value})

    assert config.ghost_window_poll_seconds == value
    assert "ghost_window_poll_seconds" in hot
    assert "ghost_window_poll_seconds" not in restart
