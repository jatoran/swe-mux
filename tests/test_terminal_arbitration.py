"""Rules for who may type into a shared session, and how big it is.

These encode the failure that motivated the module: a desktop pane that kept DOM
focus while minimized re-claimed input every time the phone claimed it, so the phone
typed against a stale belief and the daemon dropped the keystrokes.
"""

from __future__ import annotations

from swe_mux.terminal_arbitration import (
    ClaimRequest,
    OwnerState,
    effective_geometry,
    evaluate_claim,
    release_owner,
)


def desktop(now: float, reason: str = "passive", focused: bool = True) -> ClaimRequest:
    return ClaimRequest("desktop-conn", now, reason, "desktop", focused)  # type: ignore[arg-type]


def phone(now: float, reason: str = "gesture", focused: bool = True) -> ClaimRequest:
    return ClaimRequest("phone-conn", now, reason, "mobile", focused)  # type: ignore[arg-type]


def test_an_unowned_session_goes_to_whoever_asks() -> None:
    decision = evaluate_claim(OwnerState(), desktop(now=100.0))
    assert decision.granted and decision.changed
    assert decision.state.connection_id == "desktop-conn"
    assert decision.state.epoch == 1
    assert decision.state.device == "desktop"
    # A passive grant records no gesture, so the next passive claim can still take it.
    assert decision.state.last_gesture_at is None


def test_a_gesture_always_wins_even_against_an_active_owner() -> None:
    owned = OwnerState("desktop-conn", 4, "desktop", last_gesture_at=99.0)
    decision = evaluate_claim(owned, phone(now=100.0))
    assert decision.granted and decision.changed
    assert decision.state.connection_id == "phone-conn"
    assert decision.state.epoch == 5
    assert decision.state.last_gesture_at == 100.0


def test_a_passive_claim_cannot_displace_a_device_being_typed_into() -> None:
    """The exact multi-device bug: the phone is in use, the desktop pane is not."""
    owned = OwnerState("phone-conn", 5, "mobile", last_gesture_at=99.0)
    decision = evaluate_claim(owned, desktop(now=100.0))
    assert not decision.granted
    assert decision.reason == "denied_active_owner"
    assert decision.state is owned


def test_a_passive_claim_from_a_hidden_window_is_refused_outright() -> None:
    """A minimized pane keeps DOM focus and used to re-claim on that alone."""
    owned = OwnerState("phone-conn", 5, "mobile", last_gesture_at=None)
    decision = evaluate_claim(owned, desktop(now=100.0, focused=False))
    assert not decision.granted
    assert decision.reason == "denied_unfocused"


def test_a_passive_claim_takes_an_abandoned_session() -> None:
    owned = OwnerState("phone-conn", 5, "mobile", last_gesture_at=50.0)
    decision = evaluate_claim(owned, desktop(now=100.0))
    assert decision.granted and decision.changed
    assert decision.reason == "granted_idle_owner"
    assert decision.state.epoch == 6


def test_the_owner_renewing_its_own_claim_does_not_bump_the_epoch() -> None:
    """Clients read an epoch change as "ownership moved". A pane re-claiming on its
    own focus events must not look like that to everyone else."""
    owned = OwnerState("phone-conn", 5, "mobile", last_gesture_at=90.0)
    decision = evaluate_claim(owned, phone(now=100.0))
    assert decision.granted and not decision.changed
    assert decision.state.epoch == 5
    assert decision.state.last_gesture_at == 100.0


def test_a_passive_renewal_keeps_the_owners_gesture_protection() -> None:
    owned = OwnerState("phone-conn", 5, "mobile", last_gesture_at=90.0)
    decision = evaluate_claim(owned, phone(now=100.0, reason="passive"))
    assert decision.granted and decision.state.last_gesture_at == 90.0


def test_the_hold_window_is_configurable_and_bounds_the_denial() -> None:
    owned = OwnerState("phone-conn", 5, "mobile", last_gesture_at=95.0)
    assert not evaluate_claim(owned, desktop(now=100.0), hold_seconds=10.0).granted
    assert evaluate_claim(owned, desktop(now=100.0), hold_seconds=1.0).granted


def test_release_clears_only_the_holders_claim_and_keeps_the_epoch() -> None:
    owned = OwnerState("phone-conn", 5, "mobile", last_gesture_at=90.0)
    assert release_owner(owned, "desktop-conn") is owned
    released = release_owner(owned, "phone-conn")
    assert released.connection_id is None
    assert released.device is None
    # The epoch survives so a late frame from the released connection is still
    # recognizable as stale by whoever claims next.
    assert released.epoch == 5


def test_geometry_follows_the_input_owner() -> None:
    viewports = {"desktop-conn": (200, 50), "phone-conn": (40, 20)}
    assert effective_geometry(viewports, "phone-conn") == (40, 20)
    assert effective_geometry(viewports, "desktop-conn") == (200, 50)


def test_geometry_falls_back_to_the_smallest_attached_viewport() -> None:
    viewports = {"a": (200, 50), "b": (120, 60)}
    # No owner, or an owner whose window is hidden and therefore deregistered.
    assert effective_geometry(viewports, None) == (120, 50)
    assert effective_geometry(viewports, "hidden-conn") == (120, 50)


def test_geometry_is_undecided_with_nothing_attached() -> None:
    assert effective_geometry({}, None) is None
    assert effective_geometry({}, "phone-conn") is None
