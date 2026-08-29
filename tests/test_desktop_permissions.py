from __future__ import annotations

import json
import threading
import time
from typing import Any

import pytest

from swe_mux.desktop_permissions import (
    MECHANISM,
    MEDIA_MARKER,
    MediaPermissionReport,
    WebviewMicrophoneGrant,
    decide_media_permission,
    marker_script,
    normalized_origin,
    same_origin,
)

ORIGIN = "http://127.0.0.1:8765"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://127.0.0.1:8765/", "http://127.0.0.1:8765"),
        ("http://127.0.0.1:8765/index.html?x=1#y", "http://127.0.0.1:8765"),
        ("HTTP://127.0.0.1:8765", "http://127.0.0.1:8765"),
        ("http://example.test:80/", "http://example.test"),
        ("https://example.test:443/", "https://example.test"),
        ("https://example.test:8443/", "https://example.test:8443"),
        ("http://[::1]:8765/", "http://::1:8765"),
    ],
)
def test_normalized_origin_folds_default_ports_and_drops_the_path(url: str, expected: str) -> None:
    assert normalized_origin(url) == expected


@pytest.mark.parametrize("url", ["about:blank", "data:text/html,<p>", "", "not a url", "/relative"])
def test_normalized_origin_rejects_anything_without_a_scheme_and_host(url: str) -> None:
    """Opaque origins can never match, which is the right answer for them."""
    assert normalized_origin(url) is None


def test_same_origin_is_exact_rather_than_resolved() -> None:
    assert same_origin("http://127.0.0.1:8765/app", ORIGIN)
    # localhost resolves to 127.0.0.1 and is still a different origin, which is
    # the rule the web platform uses.
    assert not same_origin("http://localhost:8765/", ORIGIN)
    assert not same_origin("http://127.0.0.1:8766/", ORIGIN)
    assert not same_origin("https://127.0.0.1:8765/", ORIGIN)
    assert not same_origin("about:blank", ORIGIN)


def test_microphone_from_our_own_origin_is_granted() -> None:
    decision = decide_media_permission("Microphone", f"{ORIGIN}/", ORIGIN)
    assert decision.allow is True
    assert decision.deny is False
    assert decision.state == "granted"
    assert ORIGIN in decision.detail


def test_microphone_from_a_foreign_origin_is_denied_not_merely_unhandled() -> None:
    """Falling through to WebView2's default is not a scope.

    Measured on WebView2 152.0.4191.53: with no handler at all the runtime
    grants the microphone to a loopback origin without asking anyone, so a
    decision that only declines to allow would still let a navigated-away shell
    take the microphone.
    """
    decision = decide_media_permission("Microphone", "https://example.test/", ORIGIN)
    assert decision.allow is False
    assert decision.deny is True
    assert decision.state == "refused"
    assert "example.test" in decision.detail


@pytest.mark.parametrize("kind", ["Camera", "Geolocation", "ClipboardRead", "Notifications"])
def test_every_other_permission_kind_is_left_at_the_webview_default(kind: str) -> None:
    """Camera included, deliberately: swe-mux has no camera feature."""
    decision = decide_media_permission(kind, f"{ORIGIN}/", ORIGIN)
    assert decision.allow is False
    assert decision.deny is False
    # No state, so a camera request cannot overwrite a microphone grant.
    assert decision.state is None


def test_marker_script_publishes_a_frozen_report() -> None:
    report = MediaPermissionReport(
        state="armed", origin=ORIGIN, detail="ready", mechanism=MECHANISM
    )
    script = marker_script(report)
    assert script.startswith(f"window.{MEDIA_MARKER}=Object.freeze(")
    payload = json.loads(script[script.index("(") + 1 : script.rindex(")")])
    assert payload == {
        "state": "armed",
        "origin": ORIGIN,
        "detail": "ready",
        "mechanism": MECHANISM,
    }


class FakeEvent:
    def __init__(self) -> None:
        self.handlers: list[Any] = []

    def __iadd__(self, handler: Any) -> FakeEvent:
        self.handlers.append(handler)
        return self


class FakeCoreWebView2:
    def __init__(self) -> None:
        self.PermissionRequested = FakeEvent()
        self.scripts: list[str] = []

    def ExecuteScriptAsync(self, script: str) -> None:  # noqa: N802 - .NET name
        self.scripts.append(script)


class FakeControl:
    def __init__(self, core: FakeCoreWebView2 | None) -> None:
        self.CoreWebView2 = core


class FakeForm:
    InvokeRequired = False

    def __init__(self, control: FakeControl | None) -> None:
        self.browser = None if control is None else type("B", (), {"webview": control})()


class FakeWindow:
    def __init__(self, native: FakeForm | None) -> None:
        self.native = native
        self.events = type("E", (), {"loaded": FakeEvent()})()

    def evaluate_js(self, script: str) -> None:
        # Present so a regression back to it is a failure rather than a 20-second
        # startup stall nobody notices: publishing must never take this path.
        raise AssertionError("the marker must not be published through evaluate_js")


def settle(predicate: Any, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition never held")


def test_a_window_that_never_produces_a_control_reports_unsupported() -> None:
    """A missing WebView2 must degrade to a legible state, never to a crash."""
    window = FakeWindow(FakeForm(None))
    grant = WebviewMicrophoneGrant(
        ORIGIN, note=lambda _message: None, wait_seconds=0.2, poll_seconds=0.01
    )
    grant.attach(window)
    settle(lambda: grant.report.state == "unsupported")
    assert "never produced a WebView2 control" in grant.report.detail


def test_a_runtime_that_rejects_the_handler_reports_unsupported_with_its_reason() -> None:
    class Rejecting(FakeEvent):
        def __iadd__(self, handler: Any) -> Rejecting:
            raise NotImplementedError("PermissionRequested is not available")

    core = FakeCoreWebView2()
    core.PermissionRequested = Rejecting()
    window = FakeWindow(FakeForm(FakeControl(core)))
    grant = WebviewMicrophoneGrant(
        ORIGIN, note=lambda _message: None, wait_seconds=1.0, poll_seconds=0.01
    )
    grant.attach(window)
    settle(lambda: grant.report.state == "unsupported")
    assert "NotImplementedError" in grant.report.detail
    # It must not claim the microphone is broken. A rejected handler only means
    # the decision fell back to the runtime's default, which on the runtime this
    # was measured against is to allow.
    assert "runtime's own default" in grant.report.detail
    assert "needs a newer" not in grant.report.detail
    # A runtime that refused the handler is still told about itself in the page,
    # because that report is the only place the refusal is visible at all.
    assert any(MEDIA_MARKER in script for script in core.scripts)


def test_a_healthy_window_arms_the_grant_and_publishes_it() -> None:
    core = FakeCoreWebView2()
    window = FakeWindow(FakeForm(FakeControl(core)))
    notes: list[str] = []
    grant = WebviewMicrophoneGrant(
        ORIGIN, note=notes.append, wait_seconds=1.0, poll_seconds=0.01
    )
    grant.attach(window)
    settle(lambda: grant.report.state == "armed")
    assert grant.report.mechanism == MECHANISM
    assert len(core.PermissionRequested.handlers) == 1
    assert any(f"window.{MEDIA_MARKER}=" in script for script in core.scripts)
    assert any("armed" in note for note in notes)
    # Every navigation re-publishes, so a reload does not lose the marker.
    before = len(core.scripts)
    for handler in window.events.loaded.handlers:
        handler()
    assert len(core.scripts) > before


def test_attach_survives_a_window_whose_loaded_event_cannot_be_subscribed() -> None:
    """The shell's job is to put a window on screen; this must never raise."""

    class Hostile:
        native = None

        @property
        def events(self) -> Any:
            raise RuntimeError("no events here")

        def evaluate_js(self, script: str) -> None:
            raise RuntimeError("no page here")

    notes: list[str] = []
    grant = WebviewMicrophoneGrant(
        ORIGIN, note=notes.append, wait_seconds=0.1, poll_seconds=0.01
    )
    grant.attach(Hostile())
    settle(lambda: grant.report.state == "unsupported")
    assert any("loaded event" in note for note in notes)


def test_a_request_left_at_the_default_touches_neither_the_args_nor_the_clr() -> None:
    """A kind we have no opinion about must be a genuine no-op."""

    class Args:
        PermissionKind = "Camera"
        Uri = f"{ORIGIN}/"

        def __setattr__(self, name: str, value: object) -> None:
            raise AssertionError(f"the handler must not set {name}")

    notes: list[str] = []
    grant = WebviewMicrophoneGrant(ORIGIN, note=notes.append)
    grant._on_permission_requested(None, Args())
    assert grant.report.state == "pending"
    assert any("Camera request" in note for note in notes)


def test_an_unreadable_request_is_reported_rather_than_raised() -> None:
    class Args:
        @property
        def PermissionKind(self) -> str:
            raise RuntimeError("interop is gone")

    grant = WebviewMicrophoneGrant(ORIGIN, note=lambda _m: None)
    grant._on_permission_requested(None, Args())
    assert grant.report.state == "unsupported"
    assert "unreadable" in grant.report.detail


def test_the_report_is_readable_while_the_installer_thread_runs() -> None:
    """`report` is read from the page thread while `_record` writes from another."""
    grant = WebviewMicrophoneGrant(ORIGIN, note=lambda _m: None)
    assert grant.report.state == "pending"
    seen: list[str] = []
    stop = threading.Event()

    def reader() -> None:
        while not stop.is_set():
            seen.append(grant.report.state)

    thread = threading.Thread(target=reader)
    thread.start()
    try:
        time.sleep(0.05)
    finally:
        stop.set()
        thread.join()
    assert seen and set(seen) == {"pending"}
