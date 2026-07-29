"""Rules for deciding which device the human is at.

Every case here is one the notification path gets wrong if presence is treated as a
focus bit: a window left focused while its owner walks away, a tab frozen by the OS,
a client that reports focus but has never been touched.
"""

from __future__ import annotations

from swe_mux.device_presence import DevicePresenceStore, parse_device_report

DESKTOP = {"type": "presence", "profile": "desktop", "visible": True, "focused": True}


def _store(now: list[float]) -> DevicePresenceStore:
    return DevicePresenceStore(clock=lambda: now[0], activity_window=120.0, ttl=90.0)


def test_a_focused_recently_used_device_is_active() -> None:
    now = [1000.0]
    store = _store(now)
    store.report("c1", parse_device_report({**DESKTOP, "interaction_age": 5}))
    assert store.active_profiles() == {"desktop"}
    assert store.other_profile_active("mobile") is True
    assert store.other_profile_active("desktop") is False


def test_focus_without_recent_interaction_is_not_presence() -> None:
    """The dangerous case: a desktop left focused while its owner walks away looks
    identical to one being typed into, and silences the device they took with them."""
    now = [1000.0]
    store = _store(now)
    store.report("c1", parse_device_report({**DESKTOP, "interaction_age": 5}))
    now[0] += 200  # still focused, untouched for over three minutes
    store.report("c1", parse_device_report({**DESKTOP, "interaction_age": 205}))
    assert store.active_profiles() == set()


def test_a_hidden_or_unfocused_window_is_not_active() -> None:
    now = [1000.0]
    store = _store(now)
    store.report("c1", parse_device_report({**DESKTOP, "visible": False, "interaction_age": 1}))
    assert store.active_profiles() == set()
    store.report("c1", parse_device_report({**DESKTOP, "focused": False, "interaction_age": 1}))
    assert store.active_profiles() == set()


def test_a_device_that_has_never_been_touched_is_not_active() -> None:
    now = [1000.0]
    store = _store(now)
    store.report("c1", parse_device_report({**DESKTOP, "interaction_age": None}))
    assert store.active_profiles() == set()


def test_a_stale_heartbeat_expires_rather_than_lingering() -> None:
    """A frozen tab stops reporting; it must not stay 'present' forever and mute
    the device the user actually has."""
    now = [1000.0]
    store = _store(now)
    store.report("c1", parse_device_report({**DESKTOP, "interaction_age": 1}))
    now[0] += 91
    assert store.active_profiles() == set()


def test_a_closed_socket_drops_its_device() -> None:
    now = [1000.0]
    store = _store(now)
    store.report("c1", parse_device_report({**DESKTOP, "interaction_age": 1}))
    store.drop("c1")
    assert store.active_profiles() == set()
    assert store.snapshot()["devices"] == []


def test_one_active_tab_is_enough_for_its_device_class() -> None:
    now = [1000.0]
    store = _store(now)
    store.report("c1", parse_device_report({**DESKTOP, "focused": False, "interaction_age": 1}))
    store.report("c2", parse_device_report({**DESKTOP, "interaction_age": 1}))
    assert store.active_profiles() == {"desktop"}


def test_interaction_since_answers_who_was_present_during_a_deferral() -> None:
    now = [1000.0]
    store = _store(now)
    store.report("c1", parse_device_report({**DESKTOP, "interaction_age": 0}))
    raised = 1000.0
    now[0] += 30
    # Nothing new: the user has not touched the desktop since the alert.
    assert store.interaction_since(raised, exclude="mobile") is False
    store.report("c1", parse_device_report({**DESKTOP, "interaction_age": 2}))
    assert store.interaction_since(raised, exclude="mobile") is True
    # The notified device's own interaction never counts as "handled elsewhere".
    assert store.interaction_since(raised, exclude="desktop") is False


def test_the_most_recently_touched_device_leads_when_both_are_active() -> None:
    """Both classes are routinely active at once, because a desktop left open and
    focused keeps counting for two minutes after the last keystroke — which is exactly
    the window in which someone picks up their phone. Where the hands went last is the
    only honest tiebreak; without it the incumbent kept input while the user worked
    somewhere else, and every session opened on the phone had to be claimed by hand."""
    now = [1000.0]
    store = _store(now)
    store.report("desk", parse_device_report({**DESKTOP, "interaction_age": 90}))
    store.report(
        "phone",
        parse_device_report(
            {"profile": "mobile", "visible": True, "focused": True, "interaction_age": 2}
        ),
    )
    assert store.active_profiles() == {"desktop", "mobile"}
    assert store.leading_profile() == "mobile"

    # Touching the desktop again hands it straight back.
    store.report("desk", parse_device_report({**DESKTOP, "interaction_age": 0}))
    assert store.leading_profile() == "desktop"


def test_only_an_active_device_can_lead() -> None:
    now = [1000.0]
    store = _store(now)
    # Recently touched, but the window is hidden: the user put the phone away.
    store.report(
        "phone",
        parse_device_report(
            {"profile": "mobile", "visible": False, "focused": False, "interaction_age": 1}
        ),
    )
    store.report("desk", parse_device_report({**DESKTOP, "interaction_age": 30}))
    assert store.leading_profile() == "desktop"
    store.drop("desk")
    assert store.leading_profile() is None


def test_reports_are_validated_and_ages_are_bounded() -> None:
    assert parse_device_report({"profile": "tablet", "visible": True}) is None
    assert parse_device_report({"visible": True}) is None
    report = parse_device_report({**DESKTOP, "interaction_age": -5})
    assert report is not None and report.interaction_age == 0.0
    huge = parse_device_report({**DESKTOP, "interaction_age": 10**9})
    assert huge is not None and huge.interaction_age == 3600.0
    # A client that reports nonsense loses its interaction claim, not its report.
    text = parse_device_report({**DESKTOP, "interaction_age": "soon"})
    assert text is not None and text.interaction_age is None


def test_snapshot_explains_why_a_device_is_or_is_not_active() -> None:
    now = [1000.0]
    store = _store(now)
    store.report("c1", parse_device_report({**DESKTOP, "interaction_age": 3}))
    snapshot = store.snapshot()
    assert snapshot["active_profiles"] == ["desktop"]
    device = snapshot["devices"][0]
    assert device["profile"] == "desktop"
    assert device["active"] is True
    assert device["interaction_age"] == 3.0
    assert device["heartbeat_age"] == 0.0
