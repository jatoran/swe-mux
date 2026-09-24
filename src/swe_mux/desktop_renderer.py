"""Keep the desktop window usable when its WebView2 page crashes or stops responding.

**What went wrong without this (2026-09-24).** A preview document looped forever in
the renderer that also drew the app. The window froze; WebView2 later killed the
renderer for its memory and left its built-in error page; and because the Project's
tab layout is shared across devices, reloading put the same preview back on screen
and froze it again. A daemon restart could not help - the daemon was fine - and the
tray had no way to reach the page at all.

**What this does.** `RendererGuard` watches the window's main-frame renderer two ways
and answers both the same way:

- `CoreWebView2.ProcessFailed`: `RenderProcessExited` (the page's renderer died), or
  `RenderProcessUnresponsive` repeated for longer than a grace period. WebView2 raises
  the latter only while it has noticed, which in practice means while input is
  pending, so it is the fast path when someone is actively trying to use the window.
- A heartbeat: a trivial `ExecuteScriptAsync` every few seconds, on the UI thread. A
  main frame that has not run it within `HANG_SECONDS` is hung whether or not anyone
  is clicking - which is what a window left open overnight needs.

A hung renderer is terminated (a same-site navigation would be queued behind the very
script that never yields), and the window is navigated to `/?mux_recovered=<reason>`.
The frontend (`rendererRecovery.ts`) reads that flag and mounts no Preview document
until the operator asks for one, so a layout that restores the tab that caused the
hang cannot reproduce it. Recovery is budgeted: past `MAX_RECOVERIES` inside
`RECOVERY_WINDOW_SECONDS` it stops, says so once, and leaves WebView2's own error page
and its Refresh button in place, because a page that fails even in safe mode is not
something another reload will fix.

Preview documents no longer share the app's renderer at all
(`webview_browser_arguments`); this guard is what still holds when a hang is the
app's own, or when isolation is unavailable.

Every WebView2 call goes through `desktop_webview.on_ui_thread`.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .desktop_webview import (
    CONTROL_POLL_SECONDS,
    CONTROL_WAIT_SECONDS,
    await_webview_control,
    on_ui_thread,
)

#: The query parameter the frontend reads to start with Preview documents paused.
RECOVERY_QUERY_PARAM = "mux_recovered"
#: The reason an operator-requested reload carries; exempt from the recovery budget.
OPERATOR_REASON = "operator_reload"

HEARTBEAT_SECONDS = 5.0
#: A main frame that has not run a trivial script for this long is hung. Far above
#: anything legitimate: the UI's own heaviest work is a terminal replay, measured in
#: hundreds of milliseconds.
HANG_SECONDS = 30.0
#: How long WebView2 must keep reporting the renderer unresponsive before acting.
UNRESPONSIVE_GRACE_SECONDS = 15.0
#: A heartbeat tick later than this means the host was suspended (sleep, hibernate),
#: not that the page hung: the measurement restarts instead of firing on resume.
SUSPEND_GAP_SECONDS = 20.0
#: After terminating a hung renderer, navigate even if no exit event arrived by then.
EXIT_EVENT_WAIT_SECONDS = 5.0
MAX_RECOVERIES = 3
RECOVERY_WINDOW_SECONDS = 600.0

#: Chromium's switch for giving sandboxed iframes their own renderer process. Edge's
#: field-trial state can leave it off: measured 2026-09-24 against the live shell's
#: own variations seed, a looping preview froze the app's page with it off and did
#: not with it forced on, on the same WebView2 runtime (153.0.4234.32).
ISOLATE_SANDBOXED_IFRAMES = "IsolateSandboxedIframes"
BROWSER_ARGUMENTS_ENV = "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"


def webview_browser_arguments(existing: str | None) -> str:
    """`existing` WebView2 browser arguments plus sandboxed-iframe isolation.

    WebView2 appends this variable to the arguments pywebview passes and merges every
    `--enable-features` list, so adding a switch here removes nothing the operator or
    pywebview set, and a value that already enables the feature is returned as is.
    """
    current = (existing or "").strip()
    for token in current.split():
        if token.startswith("--enable-features=") and ISOLATE_SANDBOXED_IFRAMES in token.split(
            "=", 1
        )[1].split(","):
            return current
    addition = f"--enable-features={ISOLATE_SANDBOXED_IFRAMES}"
    return f"{current} {addition}".strip()


def recovery_url(origin: str, reason: str) -> str:
    return f"{origin.rstrip('/')}/?{RECOVERY_QUERY_PARAM}={reason}"


@dataclass
class RecoveryBudget:
    """At most `limit` recoveries inside any `window` seconds."""

    limit: int = MAX_RECOVERIES
    window: float = RECOVERY_WINDOW_SECONDS
    clock: Callable[[], float] = time.monotonic
    _spent: deque[float] = field(default_factory=deque)

    def spend(self) -> bool:
        now = self.clock()
        while self._spent and now - self._spent[0] >= self.window:
            self._spent.popleft()
        if len(self._spent) >= self.limit:
            return False
        self._spent.append(now)
        return True


@dataclass
class _Probe:
    requested_at: float
    dispatched_at: float | None = None
    task: Any = None
    failed: bool = False


def _task_completed(task: Any) -> bool:
    try:
        return bool(task.IsCompleted)
    except Exception:  # noqa: BLE001 - an unreadable task is not evidence of a hang
        return True


def terminate_renderers(browser_pid: int) -> list[int]:
    """Terminate every renderer of one WebView2 browser process; return their pids."""
    import psutil

    killed: list[int] = []
    try:
        children = psutil.Process(browser_pid).children()
    except (psutil.Error, OSError):
        return killed
    for child in children:
        try:
            if "--type=renderer" not in " ".join(child.cmdline()):
                continue
            child.kill()
            killed.append(child.pid)
        except (psutil.Error, OSError):
            continue
    return killed


class RendererGuard:
    """Detects a crashed or hung main-frame renderer and reloads the page safely."""

    def __init__(
        self,
        origin: str,
        *,
        note: Callable[[str], None],
        warn: Callable[[str, str], None] | None = None,
        stop: threading.Event | None = None,
        clock: Callable[[], float] = time.monotonic,
        terminate: Callable[[int], list[int]] = terminate_renderers,
        budget: RecoveryBudget | None = None,
        heartbeat_seconds: float = HEARTBEAT_SECONDS,
        hang_seconds: float = HANG_SECONDS,
        unresponsive_grace_seconds: float = UNRESPONSIVE_GRACE_SECONDS,
        suspend_gap_seconds: float = SUSPEND_GAP_SECONDS,
        exit_event_wait_seconds: float = EXIT_EVENT_WAIT_SECONDS,
        wait_seconds: float = CONTROL_WAIT_SECONDS,
        poll_seconds: float = CONTROL_POLL_SECONDS,
    ) -> None:
        self._origin = origin
        self._note = note
        self._warn = warn
        self._stop = stop or threading.Event()
        self._clock = clock
        self._terminate = terminate
        self._budget = budget or RecoveryBudget(clock=clock)
        self._heartbeat_seconds = heartbeat_seconds
        self._hang_seconds = hang_seconds
        self._unresponsive_grace = unresponsive_grace_seconds
        self._suspend_gap = suspend_gap_seconds
        self._exit_wait = exit_event_wait_seconds
        self._wait_seconds = wait_seconds
        self._poll_seconds = poll_seconds
        self._lock = threading.Lock()
        self._window: Any = None
        self._control: Any = None
        self._browser_pid: int | None = None
        self._probe: _Probe | None = None
        self._unresponsive_since: float | None = None
        #: Set while a terminated renderer's exit is awaited, so the exit event
        #: recovers under the reason that caused it rather than as a plain crash.
        self._pending_reason: str | None = None
        self._ui_stall_noted = False
        self._exhausted_warned = False
        self.recoveries: list[str] = []

    # -- wiring -------------------------------------------------------------------

    def attach(self, window: Any) -> None:
        """Start watching `window`; everything after this runs off the caller."""
        self._window = window
        threading.Thread(target=self._run, name="swe-mux-renderer-guard", daemon=True).start()

    def _run(self) -> None:
        try:
            control = await_webview_control(
                self._window, wait_seconds=self._wait_seconds, poll_seconds=self._poll_seconds
            )
        except Exception as exc:  # noqa: BLE001 - a guard must never take the shell down
            self._note(f"renderer guard could not reach the WebView2 control: {exc}")
            return
        if control is None:
            self._note("renderer guard inactive: pywebview never produced a WebView2 control")
            return
        self._control = control
        if not self._bind():
            return
        self._note(
            f"renderer guard armed heartbeat={self._heartbeat_seconds:g}s "
            f"hang={self._hang_seconds:g}s browser_pid={self._browser_pid}"
        )
        self.heartbeat_loop()

    def _bind(self) -> bool:
        form = self._window.native
        deadline = self._clock() + self._wait_seconds
        # unsupervised-loop-ok: desktop process, bounded by the deadline.
        while not self._stop.is_set():
            result = self._try_bind(form)
            if isinstance(result, BaseException):
                self._note(f"renderer guard could not subscribe to ProcessFailed: {result}")
                return False
            if result:
                return True
            if self._clock() >= deadline:
                self._note("renderer guard inactive: the WebView2 control never initialized")
                return False
            time.sleep(self._poll_seconds)
        return False

    def _try_bind(self, form: Any) -> BaseException | bool:
        """One attempt on the UI thread: True if subscribed, False if not ready yet."""
        outcome: list[BaseException | bool] = []

        def bind() -> None:
            try:
                core = self._control.CoreWebView2
                if core is None:
                    outcome.append(False)
                    return
                core.ProcessFailed += self._on_process_failed
                try:
                    self._browser_pid = int(core.BrowserProcessId)
                except Exception:  # noqa: BLE001 - only termination needs it
                    self._browser_pid = None
                outcome.append(True)
            except BaseException as exc:  # noqa: BLE001 - re-raised on our thread
                outcome.append(exc)

        try:
            on_ui_thread(form, bind, wait=True)
        except Exception as exc:  # noqa: BLE001
            return exc
        return outcome[0] if outcome else False

    # -- WebView2's own report (UI thread) --------------------------------------

    def _on_process_failed(self, _sender: Any, args: Any) -> None:
        try:
            kind = str(args.ProcessFailedKind)
        except Exception as exc:  # noqa: BLE001
            self._note(f"webview process failure was unreadable: {exc}")
            return
        self.handle_process_failed(kind, _describe_failure(args))

    def handle_process_failed(self, kind: str, detail: str = "") -> None:
        """React to one `ProcessFailed` report. Separated from the COM event for tests."""
        suffix = f" {detail}" if detail else ""
        if kind == "RenderProcessUnresponsive":
            now = self._clock()
            with self._lock:
                if self._unresponsive_since is None:
                    self._unresponsive_since = now
                since = self._unresponsive_since
            if now - since < self._unresponsive_grace:
                if now == since:
                    self._note(f"webview page renderer reported unresponsive{suffix}")
                return
            self._terminate_and_recover(
                "renderer_unresponsive", f"unresponsive for {now - since:.0f}s{suffix}"
            )
            return
        self._note(f"webview process failed kind={kind}{suffix}")
        if kind == "RenderProcessExited":
            with self._lock:
                reason = self._pending_reason or "renderer_exited"
                self._pending_reason = None
                self._unresponsive_since = None
                self._probe = None
            self._recover(reason, budgeted=reason != OPERATOR_REASON)
        elif kind == "BrowserProcessExited" and self._warn is not None:
            self._warn(
                "swe-mux window stopped",
                "The embedded browser behind the swe-mux window exited. Your sessions are "
                "unaffected. Quit and reopen swe-mux from the tray, or use 'Open in browser'.",
            )

    # -- heartbeat (guard thread) ------------------------------------------------

    def heartbeat_loop(self) -> None:
        last_tick = self._clock()
        while not self._stop.wait(self._heartbeat_seconds):
            now = self._clock()
            gap = now - last_tick
            last_tick = now
            self.heartbeat_tick(now, gap)

    def heartbeat_tick(self, now: float, gap: float) -> None:
        """One heartbeat step. Separated from the loop so a fake clock can drive it."""
        with self._lock:
            if gap > self._suspend_gap:
                # The host slept: an outstanding probe measured the suspension.
                self._probe = None
            probe = self._probe
            if probe is not None and probe.task is not None and _task_completed(probe.task):
                self._probe = None
                self._unresponsive_since = None
                self._ui_stall_noted = False
                probe = None
            if probe is not None and probe.failed:
                self._probe = None
                probe = None
            if probe is None:
                probe = _Probe(requested_at=now)
                self._probe = probe
                dispatch = True
            else:
                dispatch = False
        if dispatch:
            self._dispatch(probe)
            return
        if probe.dispatched_at is None:
            # The shell's own UI thread has not run a queued call. Terminating the
            # renderer would not help that, so it is reported rather than acted on.
            if now - probe.requested_at >= self._hang_seconds and not self._ui_stall_noted:
                self._ui_stall_noted = True
                self._note(
                    f"desktop shell UI thread has not run a queued call for "
                    f"{now - probe.requested_at:.0f}s"
                )
            return
        if now - probe.dispatched_at >= self._hang_seconds:
            with self._lock:
                self._probe = None
            self._terminate_and_recover(
                "renderer_hung",
                f"the page did not run a heartbeat script for {now - probe.dispatched_at:.0f}s",
            )

    def _dispatch(self, probe: _Probe) -> None:
        control = self._control
        window = self._window
        if control is None or window is None:
            return

        def send() -> None:
            probe.dispatched_at = self._clock()
            try:
                core = control.CoreWebView2
                if core is None:
                    probe.failed = True
                    return
                probe.task = core.ExecuteScriptAsync("0")
            except Exception:  # noqa: BLE001 - a closed WebView reports elsewhere
                probe.failed = True

        try:
            on_ui_thread(window.native, send, wait=False)
        except Exception:  # noqa: BLE001
            probe.failed = True

    # -- recovery ----------------------------------------------------------------

    def safe_reload(self) -> None:
        """The tray's "Reload window (previews paused)": the operator's own escape hatch.

        Works on a hung page, because it terminates the renderer rather than asking it
        to navigate, and is never refused by the automatic-recovery budget.
        """
        self._terminate_and_recover(OPERATOR_REASON, "requested from the tray")

    def _terminate_and_recover(self, reason: str, detail: str) -> None:
        with self._lock:
            if self._pending_reason is not None:
                return
            self._pending_reason = reason
            self._unresponsive_since = None
        browser_pid = self._browser_pid
        killed = self._terminate(browser_pid) if browser_pid is not None else []
        self._note(
            f"webview page renderer {reason}: {detail}; terminated renderer pids {killed or '-'}"
        )
        # The exit event normally arrives within a second and recovers from there.
        # If it does not - no pid to kill, or WebView2 said nothing - recover anyway.
        timer = threading.Timer(self._exit_wait, self._recover_if_still_pending, args=(reason,))
        timer.daemon = True
        timer.start()

    def _recover_if_still_pending(self, reason: str) -> None:
        with self._lock:
            if self._pending_reason != reason:
                return
            self._pending_reason = None
        self._recover(reason, budgeted=reason != OPERATOR_REASON)

    def _recover(self, reason: str, *, budgeted: bool = True) -> None:
        if budgeted and not self._budget.spend():
            if not self._exhausted_warned:
                self._exhausted_warned = True
                self._note(
                    f"renderer recovery paused: {MAX_RECOVERIES} recoveries inside "
                    f"{RECOVERY_WINDOW_SECONDS:.0f}s ({reason}); leaving the page as it is"
                )
                if self._warn is not None:
                    self._warn(
                        "swe-mux window keeps failing",
                        "The swe-mux page failed repeatedly even after reloading with "
                        "previews paused, so automatic recovery has stopped. Your sessions "
                        "are unaffected. Use 'Open in browser' from the tray, or quit and "
                        "reopen swe-mux.",
                    )
            return
        url = recovery_url(self._origin, reason)
        self.recoveries.append(reason)
        self._note(f"webview page recovering reason={reason} url={url}")
        control = self._control
        window = self._window
        if control is None or window is None:
            return

        def navigate() -> None:
            core = control.CoreWebView2
            if core is not None:
                core.Navigate(url)

        try:
            on_ui_thread(window.native, navigate, wait=False)
        except Exception as exc:  # noqa: BLE001
            self._note(f"webview recovery navigation failed: {exc}")


def _describe_failure(args: Any) -> str:
    parts: list[str] = []
    for name in ("Reason", "ExitCode", "ProcessDescription"):
        try:
            value = getattr(args, name)
        except Exception:  # noqa: BLE001 - older runtimes lack the later fields
            continue
        if value not in (None, ""):
            parts.append(f"{name.lower()}={value}")
    return " ".join(parts)


def configure_webview_isolation(note: Callable[[str], None]) -> tuple[str | None, str]:
    """Set the WebView2 browser arguments for this process; return (previous, applied).

    Must run before pywebview creates its WebView2 environment, i.e. before
    `webview.start()`. The previous value is returned so a child process the shell
    spawns afterwards (the daemon) can be given the operator's own value back.
    """
    previous = os.environ.get(BROWSER_ARGUMENTS_ENV)
    applied = webview_browser_arguments(previous)
    os.environ[BROWSER_ARGUMENTS_ENV] = applied
    note(f"webview browser arguments: {applied}")
    return previous, applied
