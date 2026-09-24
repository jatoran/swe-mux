"""The two ways the desktop shell may touch pywebview's WebView2 control, and no others.

Both are shared by every shell feature that talks to WebView2
(`desktop_permissions.py`, `desktop_renderer.py`), because each is a trap that has
already been walked into once:

- **Finding the control reads Python attributes only.** `native.browser.webview` is a
  chain of plain attributes on pywebview's own objects. Reaching one step further to
  `CoreWebView2` from a background thread is a cross-apartment COM call, and measured
  2026-08-29 it wedged the whole process - and because pythonnet holds the GIL across
  that call, every other Python thread froze with it, including the watchdog meant to
  notice. Readiness is decided on the UI thread, never by polling `CoreWebView2`.
- **Every WebView2 call runs on the WinForms message loop.** `CoreWebView2` is thread
  affine. `InvokeRequired` is safe to read from any thread, but it answers false when
  the form has no window handle yet, so a form without a handle is "not ready", never
  "safe to call directly".
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

#: How long to wait for pywebview to build its WebView2 control. It is created inside
#: `webview.start()`, and a cold runtime on a loaded machine has taken several seconds.
CONTROL_WAIT_SECONDS = 60.0
CONTROL_POLL_SECONDS = 0.1


def await_webview_control(
    window: Any,
    *,
    wait_seconds: float = CONTROL_WAIT_SECONDS,
    poll_seconds: float = CONTROL_POLL_SECONDS,
) -> Any:
    """pywebview's WebView2 control for `window`, or None if it never appears."""
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        browser = getattr(getattr(window, "native", None), "browser", None)
        control = getattr(browser, "webview", None)
        if control is not None:
            return control
        time.sleep(poll_seconds)
    return None


def on_ui_thread(form: Any, work: Callable[[], None], *, wait: bool) -> None:
    """Run `work` on the WinForms message loop, or raise if there is no loop yet."""
    if not getattr(form, "IsHandleCreated", True):
        raise RuntimeError("the WebView2 host window has no handle yet")
    if not getattr(form, "InvokeRequired", False):
        work()
        return
    # Imported here: `System` exists only once pythonnet has loaded the CLR, which the
    # WinForms backend has done and nothing else has.
    from System import Action

    if wait:
        form.Invoke(Action(work))
    else:
        form.BeginInvoke(Action(work))
