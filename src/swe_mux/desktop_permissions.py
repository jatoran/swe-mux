"""Microphone permission for the desktop shell's WebView2 host.

**pywebview's WebView2 backend answers no permission request at all.** In
pywebview 6.2.1 the only platform file that mentions permissions is
``webview/platforms/qt.py``; ``webview/platforms/edgechromium.py`` - the backend
the Windows desktop app uses - never subscribes to
``CoreWebView2.PermissionRequested``, although the interop assembly it ships
beside itself (``webview/lib/Microsoft.Web.WebView2.Core.dll``) exposes the
event. Every permission this app is ever asked about is therefore decided by
whatever the installed WebView2 runtime happens to do with an unhandled request.

**This is hardening, not the repair for a dead microphone**, and the difference
is worth stating because the reverse was believed for a while. Measured
2026-08-29 on WebView2 152.0.4191.53: with no handler at all, ``getUserMedia``
from a loopback origin **succeeds**, silently, without prompting anyone - and
the shell's profile at ``~/.mux/webview`` had already recorded an explicit
microphone allow for the daemon's origin. The desktop shell's ``talk:error`` was
a client-side voice-status latch in ``App.tsx`` and had nothing to do with
permissions. What is left is still worth fixing, and is what this module is for:

- **The behaviour is undocumented and not ours.** Microsoft documents the
  unhandled case as the runtime's own default, and a runtime update or an
  enterprise policy can move it. An app whose microphone works by accident of
  the host's default has no contract with anything.
- **There is no scope at all.** Whatever the runtime allows, it allows to
  whatever origin the embedded browser is pointed at, and it persists that into
  the profile. A shell that can be navigated should not hand the microphone to
  wherever it lands.

The gap is genuinely upstream - a WebView2 backend that cannot express any
permission decision is a pywebview defect, not a swe-mux one - but pywebview is
a dependency here, not something to patch in place, so the answer lives on our
side of the seam.

Two mechanisms could close it and this module deliberately uses the first:

- ``CoreWebView2.PermissionRequested``, used here. It has existed since
  WebView2's first GA runtime, so no runtime in the wild is too old for it; it
  hands us the requesting document's URI, which is what makes the origin scope
  below an *enforced* check rather than a claim made once at startup; and with
  ``SavesInProfile = false`` it grants nothing durable - the decision is
  recomputed per request and no permission state is written into the user data
  folder.
- ``CoreWebView2Profile.SetPermissionStateAsync``, not used. It needs runtime
  1.0.2210.55 or newer, and it persists a grant for an origin into the profile
  on disk, where it outlives the code that decided it and has to be revoked by a
  second call. A persisted blanket grant is a larger and stickier thing than
  this feature needs.

**The grant is scoped to the app's own origin.** This is an embedded browser
that can be navigated, and the microphone is the whole of what voice needs, so a
request is allowed only when it is for ``PermissionKind.Microphone`` *and* the
requesting document's origin is the daemon's - and a microphone request from any
other origin is **denied**, not merely left alone, because on the runtime
measured above "left alone" means "granted". Camera is deliberately not granted:
swe-mux has no camera feature, so granting it would widen the app's device
access for nothing. Every kind other than the microphone is left untouched at
``State.Default``, which is exactly today's behaviour for them.

**What happened is published into the page**, as a frozen
``window.__swemuxDesktopMedia`` object, because the client is the only place
where the answer is true: a state file in the data dir would be read by browser
tabs that are not the shell and would go stale the moment the shell stopped. The
five states it can carry are the diagnostic ladder that this defect needed and
did not have - see ``MediaPermissionReport``.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

#: The page-global the shell publishes its permission state into. The frontend
#: reads it only when capture fails, so a browser tab - which never has it - is
#: not asked to care that it is absent.
MEDIA_MARKER = "__swemuxDesktopMedia"

MECHANISM = "CoreWebView2.PermissionRequested"

#: How long to wait for pywebview to build its WebView2 control before giving
#: up. The control is created inside ``webview.start()``, which is called after
#: the grant is armed, and a cold WebView2 runtime on a loaded machine has been
#: seen to take several seconds. Expiring here is a reported state, not a crash.
CONTROL_WAIT_SECONDS = 60.0
CONTROL_POLL_SECONDS = 0.1

GrantState = Literal["pending", "armed", "granted", "refused", "unsupported"]

#: Verbatim ``CoreWebView2PermissionKind`` name for the one kind we grant.
MICROPHONE_KIND = "Microphone"


@dataclass(frozen=True)
class MediaPermissionReport:
    """What the shell did about microphone permission, in the page's own terms.

    ``state`` is a ladder, and each rung sends a reader somewhere different:

    - ``pending`` - the shell is still building its WebView2 control. Only
      visible if the page loads before the control finishes initializing.
    - ``armed`` - the handler is installed and WebView2 has not asked yet. If
      capture fails from here, the denial did not come from this module, so the
      next thing to check is Windows' own microphone privacy setting for the
      app.
    - ``granted`` - a microphone request for our origin arrived and was allowed.
      A failure after this is downstream of permission entirely: no device, or
      the OS-level block.
    - ``refused`` - a microphone request arrived for an origin that is not ours
      and was denied. ``detail`` names the origin.
    - ``unsupported`` - the handler could not be installed. ``detail`` says why,
      and this is the only state that means the fix itself did not happen.
    """

    state: GrantState
    origin: str
    detail: str
    mechanism: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class PermissionDecision:
    """One request's answer, plus whether it moves the report's state."""

    allow: bool
    detail: str
    #: Set only for a microphone request from an origin that is not ours. Every
    #: other refusal is an absence of opinion, left at WebView2's default.
    deny: bool = False
    #: ``None`` for kinds the report does not speak about, so a camera request
    #: cannot overwrite a microphone grant that already succeeded.
    state: GrantState | None = None


def normalized_origin(url: str) -> str | None:
    """Scheme + host + port, with the scheme's default port folded away.

    Returns ``None`` for anything without both a scheme and a host, which
    includes the opaque origins (``about:blank``, ``data:``) a permission
    request can legitimately carry. Those can never match, which is the answer
    we want for them.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if not scheme or not host:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    default = {"http": 80, "https": 443}.get(scheme)
    if port is None or port == default:
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def same_origin(candidate: str, origin: str) -> bool:
    """Whether ``candidate`` is the same origin as ``origin``.

    Both sides are normalized, so ``http://127.0.0.1:8765/`` and
    ``http://127.0.0.1:8765`` match while ``http://localhost:8765`` does not -
    a different host is a different origin even when it resolves to the same
    address, which is the rule the web platform uses and the one an operator
    reading this will already expect.
    """
    left = normalized_origin(candidate)
    right = normalized_origin(origin)
    return left is not None and right is not None and left == right


def decide_media_permission(kind: str, uri: str, origin: str) -> PermissionDecision:
    """Answer one ``PermissionRequested``, scoped to microphone and our origin."""
    if kind != MICROPHONE_KIND:
        # Left at State.Default, which is what WebView2 already did with every
        # request before this module existed. Camera is in here on purpose:
        # swe-mux has no camera feature, so granting it would widen device
        # access for nothing.
        return PermissionDecision(
            allow=False, detail=f"{kind} request from {uri} left at the WebView2 default"
        )
    if not same_origin(uri, origin):
        # Denied rather than left at Default, and that difference is the whole
        # scope. Measured on WebView2 152.0.4191.53: with no handler at all the
        # runtime *allows* the microphone to a loopback origin without asking
        # anyone, so leaving a foreign origin at Default would grant it too.
        # Declaring a scope and then falling through to the default is not a
        # scope.
        return PermissionDecision(
            allow=False,
            deny=True,
            state="refused",
            detail=(
                f"a microphone request from {normalized_origin(uri) or uri} was denied: "
                f"the desktop shell grants the microphone only to {origin}"
            ),
        )
    return PermissionDecision(
        allow=True,
        state="granted",
        detail=f"the desktop shell granted the microphone to {origin}",
    )


def marker_script(report: MediaPermissionReport) -> str:
    """JavaScript that publishes ``report`` into the page, idempotently."""
    return f"window.{MEDIA_MARKER}=Object.freeze({json.dumps(report.as_dict())});"


class WebviewMicrophoneGrant:
    """Arms the WebView2 microphone grant and keeps the page told about it.

    Everything here is best effort by construction. The desktop shell's job is
    to put a window on screen; a microphone that cannot be granted must degrade
    to a legible state in that window, never to a startup crash. So every
    platform call below is wrapped, and every failure becomes an
    ``unsupported`` report rather than an exception out of ``attach``.
    """

    def __init__(
        self,
        origin: str,
        *,
        note: Callable[[str], None],
        wait_seconds: float = CONTROL_WAIT_SECONDS,
        poll_seconds: float = CONTROL_POLL_SECONDS,
    ) -> None:
        self._origin = origin
        self._note = note
        self._wait_seconds = wait_seconds
        self._poll_seconds = poll_seconds
        self._lock = threading.Lock()
        self._window: Any = None
        self._control: Any = None
        self._report = MediaPermissionReport(
            state="pending",
            origin=origin,
            detail="the desktop shell is still building its WebView2 host",
        )

    @property
    def report(self) -> MediaPermissionReport:
        with self._lock:
            return self._report

    def attach(self, window: Any) -> None:
        """Arm the grant for ``window`` and return; installation runs off-thread.

        The WebView2 control does not exist yet: pywebview builds it inside
        ``webview.start()``, which the caller has not reached. Polling for it on
        a daemon thread is what lets this be called at window-creation time,
        where the rest of the window's wiring already is.
        """
        self._window = window
        try:
            window.events.loaded += self._publish
        except Exception as exc:  # noqa: BLE001 - a shell must still open
            self._note(f"could not subscribe to the window's loaded event: {exc}")
        threading.Thread(
            target=self._install, name="swe-mux-webview-permission", daemon=True
        ).start()

    def _record(self, state: GrantState, detail: str, *, mechanism: str | None = None) -> None:
        with self._lock:
            self._report = MediaPermissionReport(
                state=state,
                origin=self._origin,
                detail=detail,
                mechanism=mechanism if mechanism is not None else self._report.mechanism,
            )
        self._note(f"webview microphone permission: {state} - {detail}")
        self._publish()

    def _publish(self, *_: object) -> None:
        """Push the current report into the page, without waiting for anything.

        Deliberately not ``window.evaluate_js``. That is the obvious call and it
        is the wrong one twice over: it is gated on pywebview's ready event and
        blocks the caller for up to 20 seconds when the page is not up yet - and
        this runs during startup, when it is not - and it round-trips a result
        through the backend's scripting path, which the app's own code is also
        using. ``ExecuteScriptAsync`` marshalled with ``BeginInvoke`` posts the
        assignment to the UI thread and returns immediately, which is all a
        one-way marker needs.

        A publish that lands nowhere is survivable: the frontend reads a missing
        marker as "not the desktop shell", which is a weaker diagnostic but never
        a wrong one, and the `loaded` subscription re-pushes on every navigation.
        """
        control = self._control
        window = self._window
        if control is None or window is None:
            return
        script = marker_script(self.report)

        def push() -> None:
            core = control.CoreWebView2
            if core is not None:
                core.ExecuteScriptAsync(script)

        try:
            self._on_ui_thread(window.native, push, wait=False)
        except Exception:  # noqa: BLE001
            pass

    def _install(self) -> None:
        try:
            control = self._await_control()
        except Exception as exc:  # noqa: BLE001
            self._record("unsupported", f"could not reach the WebView2 control: {exc}")
            return
        if control is None:
            self._record(
                "unsupported",
                "pywebview never produced a WebView2 control, so the microphone "
                f"grant could not be installed within {self._wait_seconds:g}s",
            )
            return
        # Set before subscribing, so even a runtime that rejects the handler can
        # still be told about itself in the page.
        self._control = control
        try:
            self._subscribe(control)
        except TimeoutError as exc:
            self._record("unsupported", f"{exc}, so the microphone grant is not installed")
            return
        except Exception as exc:  # noqa: BLE001
            # Careful with the advice here. A rejected handler does not mean the
            # microphone is dead: it means the decision falls back to whatever
            # this runtime does with an unhandled request, which on the runtime
            # this was measured against is to allow it. Saying "voice needs a
            # newer runtime" would send a reader to fix something that is very
            # likely working.
            self._record(
                "unsupported",
                "this WebView2 runtime would not accept the microphone permission "
                f"handler ({type(exc).__name__}: {exc}), so the microphone is left to "
                "the runtime's own default for this origin rather than being granted "
                "deliberately; if capture then fails, open swe-mux in a browser",
            )
            return
        self._record(
            "armed",
            f"the desktop shell will grant the microphone to {self._origin} when "
            "WebView2 asks",
            mechanism=MECHANISM,
        )

    def _await_control(self) -> Any:
        """Poll for pywebview's WebView2 control, or ``None`` if it never comes.

        **Reads nothing but Python attributes.** ``native`` and ``browser`` are
        plain attributes on pywebview's own objects and ``webview`` is a
        reference held by one of them, so following that chain costs nothing and
        crosses no apartment. Reaching one step further to ``CoreWebView2`` -
        which is what an obvious version of this loop does, to find out whether
        the control is ready - is a cross-apartment COM call from a background
        thread, and it does not merely fail: measured 2026-08-29, it wedged the
        whole process, and because pythonnet holds the GIL across that call it
        also froze every other Python thread, including the watchdog that was
        supposed to notice. The window became a "not responding" ghost on the
        operator's desktop. Readiness is therefore decided on the UI thread, in
        ``_subscribe``, and never here.
        """
        deadline = time.monotonic() + self._wait_seconds
        while time.monotonic() < deadline:
            browser = getattr(getattr(self._window, "native", None), "browser", None)
            control = getattr(browser, "webview", None)
            if control is not None:
                return control
            time.sleep(self._poll_seconds)
        return None

    def _on_ui_thread(self, form: Any, work: Callable[[], None], *, wait: bool) -> None:
        """Run ``work`` on the WinForms message loop, or raise if there is no loop yet.

        Every WebView2 call in this module goes through here. ``InvokeRequired``
        is one of the few members WinForms documents as safe to read from any
        thread, which is what makes the check itself legal - but it answers
        **false** when the control has no window handle yet, because then there
        is no owning thread to differ from. Taking that at face value would run
        the work inline on the caller, which is the exact off-thread COM call
        this indirection exists to prevent, and it would happen only in the
        narrow startup window where it is hardest to reproduce. So a form
        without a handle is "not ready", never "safe to call directly".
        """
        if not getattr(form, "IsHandleCreated", True):
            raise RuntimeError("the WebView2 host window has no handle yet")
        if not getattr(form, "InvokeRequired", False):
            work()
            return
        # Imported here rather than at module scope: `System` exists only once
        # pythonnet has loaded the CLR, which the WinForms backend has done and
        # nothing else has. Marshalling is the only reason we need it.
        from System import Action

        if wait:
            form.Invoke(Action(work))
        else:
            form.BeginInvoke(Action(work))

    def _subscribe(self, control: Any) -> None:
        """Attach the handler on the UI thread, which is where WebView2 lives.

        ``CoreWebView2`` may still be null when we get here - pywebview kicks off
        ``EnsureCoreWebView2Async`` and does not wait - so this both decides
        readiness and subscribes, in one trip, and retries until the deadline.
        """
        form = self._window.native
        deadline = time.monotonic() + self._wait_seconds
        # unsupervised-loop-ok: runs once per shell launch, in the desktop
        # process rather than the daemon (which has no registry to join from
        # here), and every path out of it is bounded by the deadline above.
        while True:
            if self._try_bind(form, control):
                return
            if time.monotonic() >= deadline:
                raise TimeoutError("the WebView2 control never finished initializing")
            time.sleep(self._poll_seconds)

    def _try_bind(self, form: Any, control: Any) -> bool:
        """One attempt: ``True`` if subscribed, ``False`` if not ready yet.

        A method rather than a closure inside the retry loop, so nothing here
        captures a loop variable - the outcome is the return value and the
        failure is re-raised, both of which cross the thread boundary as data.
        """
        outcome: list[BaseException | bool] = []

        def bind() -> None:
            try:
                core = control.CoreWebView2
                if core is None:
                    outcome.append(False)
                    return
                # Recorded only after the subscription actually took, so a
                # runtime that rejects it cannot be read as "ready".
                core.PermissionRequested += self._on_permission_requested
                outcome.append(True)
            except BaseException as exc:  # noqa: BLE001 - re-raised on our thread
                outcome.append(exc)

        self._on_ui_thread(form, bind, wait=True)
        if not outcome:
            return False
        result = outcome[0]
        if isinstance(result, BaseException):
            raise result
        return result

    def _on_permission_requested(self, _sender: Any, args: Any) -> None:
        try:
            kind = str(args.PermissionKind)
            uri = str(args.Uri)
        except Exception as exc:  # noqa: BLE001
            self._record("unsupported", f"a WebView2 permission request was unreadable: {exc}")
            return
        decision = decide_media_permission(kind, uri, self._origin)
        if decision.allow or decision.deny:
            try:
                # Only the two branches that actually set a state need the CLR;
                # leaving a request at WebView2's default touches nothing.
                from Microsoft.Web.WebView2.Core import (
                    CoreWebView2PermissionState,
                )

                args.State = (
                    CoreWebView2PermissionState.Allow
                    if decision.allow
                    else CoreWebView2PermissionState.Deny
                )
                # Decide per request rather than writing a grant into the user
                # data folder that would outlive this code.
                args.SavesInProfile = False
                # Suppress any prompt the runtime would otherwise draw. Older
                # runtimes have no `Handled`, and on those an explicit state is
                # already prompt-free, so its absence is not a failure.
                try:
                    args.Handled = True
                except Exception:  # noqa: BLE001
                    pass
            except Exception as exc:  # noqa: BLE001
                self._record(
                    "unsupported",
                    "this WebView2 runtime would not accept a microphone decision "
                    f"({type(exc).__name__}: {exc})",
                )
                return
        if decision.state is not None:
            self._record(decision.state, decision.detail)
        else:
            self._note(f"webview permission: {decision.detail}")
