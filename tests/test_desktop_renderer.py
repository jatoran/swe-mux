"""The desktop window recovers from a crashed or hung page, with previews paused.

On 2026-09-24 a preview document looped forever in the renderer that also drew the
app; the window froze, and reloading restored the same tab and froze it again. These
drive `RendererGuard` with a fake clock and a fake WebView2, because the real one is
a COM object that only exists inside the running shell.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from swe_mux.desktop_renderer import (
    BROWSER_ARGUMENTS_ENV,
    OPERATOR_REASON,
    RecoveryBudget,
    RendererGuard,
    configure_webview_isolation,
    recovery_url,
    webview_browser_arguments,
)

ORIGIN = "http://127.0.0.1:8765"


class FakeEvent:
    def __init__(self) -> None:
        self.handlers: list[Any] = []

    def __iadd__(self, handler: Any) -> FakeEvent:
        self.handlers.append(handler)
        return self


class FakeTask:
    def __init__(self) -> None:
        self.IsCompleted = False


class FakeCore:
    def __init__(self) -> None:
        self.ProcessFailed = FakeEvent()
        self.BrowserProcessId = 4242
        self.navigations: list[str] = []
        self.tasks: list[FakeTask] = []

    def ExecuteScriptAsync(self, _script: str) -> FakeTask:  # noqa: N802 - .NET name
        task = FakeTask()
        self.tasks.append(task)
        return task

    def Navigate(self, url: str) -> None:  # noqa: N802 - .NET name
        self.navigations.append(url)


class FakeWindow:
    def __init__(self, core: FakeCore) -> None:
        control = type("Control", (), {"CoreWebView2": core})()
        browser = type("Browser", (), {"webview": control})()
        self.native = type("Form", (), {"InvokeRequired": False, "browser": browser})()


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def settle(predicate: Any, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition never held")


def guard(**overrides: Any) -> tuple[RendererGuard, FakeCore, list[int], list[str], Clock]:
    core = FakeCore()
    window = FakeWindow(core)
    clock = Clock()
    terminated: list[int] = []
    notes: list[str] = []

    def terminate(pid: int) -> list[int]:
        terminated.append(pid)
        return [pid + 1]

    options: dict[str, Any] = {
        "note": notes.append,
        "clock": clock,
        "terminate": terminate,
        "exit_event_wait_seconds": 30.0,
        **overrides,
    }
    built = RendererGuard(ORIGIN, **options)
    # What `attach` establishes once the control exists, without its threads.
    built._window = window
    built._control = window.native.browser.webview
    built._browser_pid = core.BrowserProcessId
    return built, core, terminated, notes, clock


def test_isolation_is_added_without_removing_anything_the_operator_set() -> None:
    assert webview_browser_arguments(None) == "--enable-features=IsolateSandboxedIframes"
    assert webview_browser_arguments("--foo") == "--foo --enable-features=IsolateSandboxedIframes"
    already = "--enable-features=Other,IsolateSandboxedIframes"
    assert webview_browser_arguments(already) == already


def test_configuring_isolation_returns_the_value_a_child_must_get_back(monkeypatch: Any) -> None:
    monkeypatch.setenv(BROWSER_ARGUMENTS_ENV, "--operator-flag")
    notes: list[str] = []

    previous, applied = configure_webview_isolation(notes.append)

    assert previous == "--operator-flag"
    assert "IsolateSandboxedIframes" in applied
    assert "--operator-flag" in applied
    assert any("IsolateSandboxedIframes" in note for note in notes)


def test_the_budget_refuses_past_its_limit_inside_its_window() -> None:
    clock = Clock()
    budget = RecoveryBudget(limit=2, window=60.0, clock=clock)

    assert budget.spend() and budget.spend()
    assert not budget.spend()
    clock.now += 61
    assert budget.spend()


def test_a_crashed_page_is_reloaded_with_previews_paused() -> None:
    built, core, terminated, _notes, _clock = guard()

    built.handle_process_failed("RenderProcessExited", "reason=Crashed")

    assert core.navigations == [recovery_url(ORIGIN, "renderer_exited")]
    assert core.navigations[0].endswith("/?mux_recovered=renderer_exited")
    assert terminated == []


def test_a_subframe_crash_leaves_the_page_alone() -> None:
    built, core, terminated, notes, _clock = guard()

    built.handle_process_failed("FrameRenderProcessExited")

    assert core.navigations == [] and terminated == []
    assert any("FrameRenderProcessExited" in note for note in notes)


def test_an_unresponsive_page_is_terminated_after_the_grace_and_then_reloaded() -> None:
    built, core, terminated, _notes, clock = guard()

    built.handle_process_failed("RenderProcessUnresponsive")
    clock.now += 5
    built.handle_process_failed("RenderProcessUnresponsive")
    assert terminated == []

    clock.now += 11
    built.handle_process_failed("RenderProcessUnresponsive")
    assert terminated == [4242]
    # Termination produces the exit event, which recovers under the real reason.
    built.handle_process_failed("RenderProcessExited")
    assert core.navigations == [recovery_url(ORIGIN, "renderer_unresponsive")]


def test_a_page_that_stops_running_the_heartbeat_is_treated_as_hung() -> None:
    built, core, terminated, _notes, clock = guard()

    built.heartbeat_tick(clock.now, 5.0)
    assert len(core.tasks) == 1
    clock.now += 20
    built.heartbeat_tick(clock.now, 5.0)
    assert terminated == []
    clock.now += 11
    built.heartbeat_tick(clock.now, 5.0)

    assert terminated == [4242]
    built.handle_process_failed("RenderProcessExited")
    assert core.navigations == [recovery_url(ORIGIN, "renderer_hung")]


def test_a_responsive_page_is_never_touched() -> None:
    built, core, terminated, _notes, clock = guard()
    for _ in range(20):
        built.heartbeat_tick(clock.now, 5.0)
        core.tasks[-1].IsCompleted = True
        clock.now += 5

    assert terminated == [] and core.navigations == []
    assert len(core.tasks) == 20


def test_a_suspended_host_restarts_the_measurement_instead_of_firing() -> None:
    built, _core, terminated, _notes, clock = guard()
    built.heartbeat_tick(clock.now, 5.0)

    clock.now += 8 * 3600  # the machine slept overnight with a probe outstanding
    built.heartbeat_tick(clock.now, 8 * 3600)

    assert terminated == []


def test_a_stuck_shell_ui_thread_is_reported_not_acted_on() -> None:
    built, _core, terminated, notes, clock = guard()
    built._dispatch = lambda probe: None  # type: ignore[method-assign]
    built.heartbeat_tick(clock.now, 5.0)
    for _ in range(3):
        clock.now += 31
        built.heartbeat_tick(clock.now, 5.0)

    assert terminated == []
    assert sum("UI thread" in note for note in notes) == 1


def test_recovery_stops_and_says_so_once_when_even_safe_mode_keeps_failing() -> None:
    warnings: list[str] = []
    built, core, _terminated, _notes, _clock = guard(
        warn=lambda title, _message: warnings.append(title)
    )

    for _ in range(5):
        built.handle_process_failed("RenderProcessExited")

    assert len(core.navigations) == 3
    assert warnings == ["swe-mux window keeps failing"]


def test_the_operator_reload_is_never_refused_by_the_budget() -> None:
    built, core, terminated, _notes, _clock = guard()
    for _ in range(3):
        built.handle_process_failed("RenderProcessExited")

    built.safe_reload()
    built.handle_process_failed("RenderProcessExited")

    assert terminated == [4242]
    assert core.navigations[-1] == recovery_url(ORIGIN, OPERATOR_REASON)


def test_recovery_happens_even_if_no_exit_event_ever_arrives() -> None:
    built, core, _terminated, _notes, _clock = guard(exit_event_wait_seconds=0.05)

    built.safe_reload()

    settle(lambda: core.navigations == [recovery_url(ORIGIN, OPERATOR_REASON)])


def test_attach_subscribes_to_process_failures_and_learns_the_browser_pid() -> None:
    core = FakeCore()
    stop = threading.Event()
    built = RendererGuard(
        ORIGIN, note=lambda _m: None, stop=stop, wait_seconds=1.0, poll_seconds=0.01,
        heartbeat_seconds=0.02,
    )
    try:
        built.attach(FakeWindow(core))
        settle(lambda: len(core.ProcessFailed.handlers) == 1 and len(core.tasks) >= 1)
        assert built._browser_pid == 4242
    finally:
        stop.set()
