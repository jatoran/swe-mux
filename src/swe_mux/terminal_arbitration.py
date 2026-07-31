"""Arbitration of the two things attached terminals must share: who may type, and
how big the PTY is.

Both were previously decided by whichever client spoke last, which is wrong when the
same session is open on more than one device. The concrete failure: a desktop pane
keeps `document.activeElement` inside its terminal even while the window is minimized,
so it re-claimed input every time the phone claimed it. The phone then typed while its
own `input_owner` belief was one round trip out of date, and the daemon dropped those
keystrokes silently — a high, latency-dependent fraction of everything typed on the
phone. Geometry had the same shape: the last client to fit reshaped ConPTY for
everyone, so a hidden desktop pane kept rewrapping the agent TUI to desktop width on a
phone screen.

The rules here are pure so they can be tested without a PTY, a socket, or a browser:

* An explicit human gesture (tap, click, keystroke) always wins. Intent beats state.
* A *passive* claim — attach, reconnect, or restored DOM focus — may not displace an
  owner a human has typed into within `PASSIVE_CLAIM_HOLD_SECONDS`, and may not
  displace anyone at all when the claiming client reports itself unfocused or hidden.
  An unfocused client may not take an *unowned* session either: ownership carries
  geometry with it, so a background pane winning it by default is a resize of the
  session for everyone who can actually see it.
* A passive claim may never cross device classes while another device class is the
  one in use (`device_presence.py`). Ownership is per session, so the gesture window
  above only protects the session being typed into, and only for seconds — which left
  the original complaint half-fixed: reading a session on the phone for a while let
  the next desktop reconnect take input back, and every *other* session opened on the
  phone still had to be claimed by hand because a desktop pane had attached to it
  first. Which device the human is at is not a per-session fact, so it is not decided
  per session.
* The input owner dictates PTY geometry; everyone else letterboxes locally. With no
  owner, the smallest visible viewport wins so no attached client is truncated.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Literal

ClaimReason = Literal["gesture", "passive"]

#: How long an owner is protected from passive claims after human input. Long enough to
#: cover a phone's round trip plus thinking time between keystrokes, short enough that
#: returning to an abandoned session still just works.
PASSIVE_CLAIM_HOLD_SECONDS = 10.0

GRANT_UNOWNED = "granted_unowned"
GRANT_GESTURE = "granted_gesture"
GRANT_IDLE_OWNER = "granted_idle_owner"
GRANT_DEVICE_IN_USE = "granted_device_in_use"
GRANT_RENEWED = "renewed"
DENY_ACTIVE_OWNER = "denied_active_owner"
DENY_UNFOCUSED = "denied_unfocused"
DENY_DEVICE_IN_USE = "denied_device_in_use"


@dataclass(frozen=True)
class OwnerState:
    """Who owns terminal input, and how recently a human proved it."""

    connection_id: str | None = None
    #: Bumped on every transfer. Ownership notifications carry it so a client can
    #: discard a stale `input_owner` frame that lost a race with a newer one.
    epoch: int = 0
    device: str | None = None
    #: Monotonic timestamp of the last gesture-backed claim or keystroke by the owner.
    last_gesture_at: float | None = None


@dataclass(frozen=True)
class ClaimRequest:
    connection_id: str
    now: float
    reason: ClaimReason = "gesture"
    device: str = "unknown"
    #: The client's own report of whether its document is visible and its window
    #: focused. A minimized desktop pane answers False and so cannot passively steal.
    focused: bool = True
    #: Which device classes are actually in use, from the daemon's own presence
    #: tracking (`device_presence.py`) rather than any client's word for it. Both
    #: False when nothing is known, which restores the per-session rules below.
    other_device_in_use: bool = False
    this_device_in_use: bool = False


@dataclass(frozen=True)
class ClaimDecision:
    granted: bool
    state: OwnerState
    reason: str
    #: True when ownership moved to a different connection, so the daemon knows it must
    #: notify the displaced client and recompute geometry.
    changed: bool


def evaluate_claim(
    state: OwnerState,
    request: ClaimRequest,
    hold_seconds: float = PASSIVE_CLAIM_HOLD_SECONDS,
) -> ClaimDecision:
    """Decide whether `request` may take terminal input from `state`."""
    gesture = request.reason == "gesture"
    if state.connection_id == request.connection_id:
        # A renewal never bumps the epoch: clients treat an epoch change as "ownership
        # moved", and a pane re-claiming on its own focus events must not look like that.
        renewed = replace(
            state,
            device=request.device,
            last_gesture_at=request.now if gesture else state.last_gesture_at,
        )
        return ClaimDecision(True, renewed, GRANT_RENEWED, changed=False)
    if state.connection_id is None:
        # Unowned is not a free-for-all. A pane that reports itself off screen has no
        # human behind it, and ownership is not just the keyboard — the owner dictates
        # PTY geometry outright (`effective_geometry`). Granting it here is how a warm
        # pane attaching behind another tab, at a size it had never measured, resized
        # ConPTY for the session and left every on-screen client letterboxing to it.
        # A gesture still wins: intent beats state, and a click is a human by definition.
        if not gesture and not request.focused:
            return ClaimDecision(False, state, DENY_UNFOCUSED, changed=False)
        return ClaimDecision(True, _granted(state, request), GRANT_UNOWNED, changed=True)
    if gesture:
        return ClaimDecision(True, _granted(state, request), GRANT_GESTURE, changed=True)
    if not request.focused:
        return ClaimDecision(False, state, DENY_UNFOCUSED, changed=False)
    if request.other_device_in_use:
        # Another device class is the one the human is demonstrably using. Nothing a
        # background pane does on its own — attaching, reconnecting, regaining DOM
        # focus — may take the keyboard away from it. Without this the per-session
        # gesture window was the only defence, and it lapses in seconds: reading a
        # session on the phone for half a minute was long enough for the next desktop
        # reconnect to take input back.
        return ClaimDecision(False, state, DENY_DEVICE_IN_USE, changed=False)
    if request.this_device_in_use and state.device != request.device:
        # This device is the one in use and the owner's is not. The gesture window
        # exists to stop a background pane stealing from an active one; applying it
        # here would do the opposite — it is why every session opened on the phone
        # demanded a manual "take over" after any recent desktop typing.
        return ClaimDecision(True, _granted(state, request), GRANT_DEVICE_IN_USE, changed=True)
    if _owner_is_active(state, request.now, hold_seconds):
        return ClaimDecision(False, state, DENY_ACTIVE_OWNER, changed=False)
    return ClaimDecision(True, _granted(state, request), GRANT_IDLE_OWNER, changed=True)


def _granted(state: OwnerState, request: ClaimRequest) -> OwnerState:
    return OwnerState(
        connection_id=request.connection_id,
        epoch=state.epoch + 1,
        device=request.device,
        # Only a gesture proves a human is at this device; a passive grant leaves the
        # new owner immediately displaceable by the next passive claim.
        last_gesture_at=request.now if request.reason == "gesture" else None,
    )


def _owner_is_active(state: OwnerState, now: float, hold_seconds: float) -> bool:
    if state.last_gesture_at is None:
        return False
    return now - state.last_gesture_at < hold_seconds


def release_owner(state: OwnerState, connection_id: str) -> OwnerState:
    """Clear ownership when `connection_id` holds it; otherwise leave it alone.

    The epoch survives a release so a late `input_owner` frame from the released
    connection can still be recognized as stale by whoever claims next.
    """
    if state.connection_id != connection_id:
        return state
    return OwnerState(connection_id=None, epoch=state.epoch, device=None, last_gesture_at=None)


def effective_geometry(
    viewports: Mapping[str, tuple[int, int]],
    owner: str | None,
) -> tuple[int, int] | None:
    """Pick the ConPTY size for a session from every visible client's viewport.

    The owner's own fit wins outright: the device a human is typing into is the one
    whose wrapping has to be right. Without an owner (or without a viewport for it —
    a hidden owner deregisters), fall back to the smallest attached viewport so no
    client is asked to render columns it does not have.
    """
    owned = viewports.get(owner) if owner is not None else None
    if owned is not None:
        return owned
    if not viewports:
        return None
    return (
        min(cols for cols, _ in viewports.values()),
        min(rows for _, rows in viewports.values()),
    )
