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


def test_an_unowned_session_still_refuses_a_pane_that_is_not_on_screen() -> None:
    """The letterbox bug: a warm pane (mounted, `display:none`, behind another tab)
    attaches, passively claims the session nobody owns yet, and — because the owner
    dictates geometry — resizes ConPTY to the size it reports. Its host measures zero,
    so that size is xterm's unfitted 80x24 default, and every on-screen client is then
    told to letterbox to a grid the user never chose."""
    decision = evaluate_claim(OwnerState(), desktop(now=100.0, focused=False))
    assert not decision.granted
    assert decision.reason == "denied_unfocused"
    assert decision.state.connection_id is None

    # A real gesture is still a human, so it takes an unowned session either way.
    clicked = evaluate_claim(
        OwnerState(), desktop(now=100.0, reason="gesture", focused=False)
    )
    assert clicked.granted and clicked.state.connection_id == "desktop-conn"


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


def test_a_passive_claim_never_crosses_to_the_device_in_use() -> None:
    """The report this rule exists for: every session opened on the phone demanded a
    manual "take over", and pausing on one let the desktop take it straight back.

    Ownership is per session and the gesture window is seconds long, so neither could
    express "the human is on their phone right now" — a fact about the whole app."""
    # The desktop holds this session and was typed into moments ago, but the human is
    # on their phone now. Opening the session there must not require a manual claim.
    owned = OwnerState("desktop-conn", 5, "desktop", last_gesture_at=99.0)
    request = ClaimRequest(
        "phone-conn", 100.0, "passive", "mobile", True, this_device_in_use=True
    )
    decision = evaluate_claim(owned, request)
    assert decision.granted and decision.changed
    assert decision.reason == "granted_device_in_use"

    # And the reverse: a desktop pane reattaching cannot take it back, even though the
    # phone's own gesture protection has long since lapsed.
    idle_phone_owner = OwnerState("phone-conn", 6, "mobile", last_gesture_at=None)
    intruder = ClaimRequest(
        "desktop-conn", 100.0, "passive", "desktop", True, other_device_in_use=True
    )
    refused = evaluate_claim(idle_phone_owner, intruder)
    assert not refused.granted
    assert refused.reason == "denied_device_in_use"


def test_the_device_in_use_rule_still_yields_to_a_real_gesture() -> None:
    """Someone sitting down at the desktop and clicking into a terminal is not a
    background pane; the whole point of the gesture tier is that it always works."""
    owned = OwnerState("phone-conn", 5, "mobile", last_gesture_at=99.0)
    request = ClaimRequest(
        "desktop-conn", 100.0, "gesture", "desktop", True, other_device_in_use=True
    )
    assert evaluate_claim(owned, request).granted


def test_being_the_device_in_use_does_not_settle_contention_within_it() -> None:
    """Two panes on the same device are the case the gesture window was written for,
    and it still decides them."""
    owned = OwnerState("desk-a", 5, "desktop", last_gesture_at=99.0)
    sibling = ClaimRequest(
        "desk-b", 100.0, "passive", "desktop", True, this_device_in_use=True
    )
    assert evaluate_claim(owned, sibling).reason == "denied_active_owner"


def test_the_owner_renewing_its_claim_is_unaffected_by_another_device() -> None:
    owned = OwnerState("phone-conn", 5, "mobile", last_gesture_at=90.0)
    request = ClaimRequest(
        "phone-conn", 100.0, "passive", "mobile", True, other_device_in_use=True
    )
    decision = evaluate_claim(owned, request)
    assert decision.granted and not decision.changed


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
