"""Phase 5 frontend contract: the affordances the safety story depends on.

Auto-delivery and agent messaging are only as bounded as the surfaces that let
a human see and stop them. These pin that the controls exist and are wired to
the daemon's typed operations rather than to browser-side state.
"""

from __future__ import annotations

from pathlib import Path

from tests.support.frontend_source import localized_source, source_contains

ROOT = Path(__file__).parents[1] / "frontend" / "src"


def test_the_queue_tab_exposes_the_bounded_conversation_policy_and_its_state() -> None:
    pane = localized_source((ROOT / "QueuePane.tsx").read_text(encoding="utf-8"))

    assert source_contains(pane, "auto-deliver armed messages")
    assert source_contains(pane, "accept agent messages armed")
    # The strip must show the bounds, not just an on/off: sends left, time
    # left, and why it is off when it is.
    assert source_contains(pane, "send${row.sends_remaining === 1 ? '' : 's'} left")
    assert source_contains(pane, "${minutes} min left")
    assert source_contains(pane, "quiet hours — paused")
    assert source_contains(pane, "paused (emergency stop)")
    # The conversation policy cannot be changed while the install master is off.
    assert source_contains(pane, "disabled={busyId === 'auto' || !auto?.master_enabled}")


def test_a_lapse_is_explained_where_the_reader_is_already_asking_why() -> None:
    """ "Lapsed while the conversation was idle" is true and unactionable alone.

    The strip is where somebody looks when a message has not moved, so it
    carries the numbers behind the lapse — how long the conversation was idle,
    under what window, and how many messages were left waiting — and routes to
    the bound itself. The window is a *value* rather than a switch, so per
    `setting-links.md` that is a link and never a grant.
    """
    pane = localized_source((ROOT / "QueuePane.tsx").read_text(encoding="utf-8"))

    assert source_contains(pane, "describeLapse")
    assert source_contains(pane, "idle ${Math.round(lapse.idle_seconds / 60)} min")
    assert source_contains(pane, "${lapse.pending} waiting")
    assert source_contains(pane, 'target="queue.grantWindow"')
    # A grant held open by a live exchange says so, because otherwise it is
    # indistinguishable from one that was simply granted a moment ago.
    assert source_contains(pane, "'reply window open'")
    assert source_contains(pane, "is owed a reply")


def test_settings_explain_that_agent_conversations_default_on() -> None:
    settings = localized_source((ROOT / "Settings.tsx").read_text(encoding="utf-8"))

    assert source_contains(
        settings, "New observed agent conversations start enabled with these bounds"
    )
    assert source_contains(settings, "Allow auto-delivery for agent conversations")


def test_the_two_grant_windows_are_editable_and_marked_for_deep_links() -> None:
    """One owner per setting: both windows are edited here and nowhere else.

    The idle window is what a lapse notice routes to, so it has to carry the
    mark that reveal scrolls to. The reply window is the narrower bound beside
    it, and its prose has to say what it does *not* widen — otherwise "keeps its
    grant while awaiting a reply" reads as a second delivery path.
    """
    settings = localized_source((ROOT / "Settings.tsx").read_text(encoding="utf-8"))

    assert source_contains(settings, 'data-setting="auto_delivery_session_ttl_minutes"')
    assert source_contains(settings, 'data-setting="auto_delivery_reply_window_minutes"')
    assert source_contains(settings, "Minutes a session keeps its grant while awaiting a reply")
    assert source_contains(settings, "Extends only the current request-response exchange")
    assert source_contains(settings, "Set 0 to disable.")


def test_scheduling_is_a_property_of_the_queued_item() -> None:
    pane = localized_source((ROOT / "QueuePane.tsx").read_text(encoding="utf-8"))
    api = localized_source((ROOT / "queueApi.ts").read_text(encoding="utf-8"))

    assert source_contains(pane, "scheduleQueueMessage")
    assert source_contains(pane, "Clear schedule")
    # Stamped on the daemon's clock, because the daemon is what waits on it: a
    # browser out of step would release the message early or hold it late.
    assert source_contains(pane, "not_before: serverNow() + preset.seconds")
    assert not source_contains(pane, "Date.now()")
    # No browser timer anywhere in the send path.
    assert not source_contains(pane, "setTimeout")
    assert source_contains(api, "a browser timer dies with the tab")


def test_the_emergency_controls_need_nothing_opened_to_reach() -> None:
    """A brake reachable only by opening an overlay is a brake you cannot reach in the
    moment you want it. The install-wide stop lives on the one queue surface that
    delivers, and on a command that needs nothing open at all."""
    pane = localized_source((ROOT / "QueuePane.tsx").read_text(encoding="utf-8"))
    app = localized_source((ROOT / "App.tsx").read_text(encoding="utf-8"))

    assert source_contains(pane, "pause all auto-delivery")
    assert source_contains(pane, "report unsafe delivery")
    assert source_contains(pane, "Stops every automatic delivery immediately, on every session")
    assert source_contains(app, "autodelivery.pause")
    assert source_contains(app, "Pause all auto-delivery (install-wide)")
    assert source_contains(app, "Resume auto-delivery (install-wide)")
    # The status line and the stop behind it survive an empty target: the install-wide
    # state is true with no session focused, and it is the state that makes every
    # per-session reading a lie when it is off.
    assert source_contains(pane, "if (!sessionId) return 'armed for this install'")


def test_the_fleet_queue_reports_the_delivery_state_and_never_owns_it() -> None:
    """It is a review surface. Nothing in it delivers, and nothing in it is a brake."""
    fleet = localized_source((ROOT / "FleetQueue.tsx").read_text(encoding="utf-8"))
    pane = localized_source((ROOT / "QueuePane.tsx").read_text(encoding="utf-8"))

    assert not source_contains(fleet, "sendQueueMessage")
    assert not source_contains(fleet, "Send now")
    assert not source_contains(fleet, "setAutoPaused")
    assert not source_contains(fleet, "reportUnsafeDelivery")
    assert source_contains(fleet, "paused (emergency stop)")
    assert source_contains(fleet, "Revoke")
    # Proving-period numbers stay visible in both places an operator reviews deliveries.
    assert source_contains(fleet, "{promotion.proving_days}/{promotion.required_days} days")
    assert source_contains(
        pane, "{auto.promotion.proving_days}/{auto.promotion.required_days} days"
    )


def test_the_session_queue_and_the_fleet_queue_have_non_overlapping_scopes() -> None:
    queue = localized_source((ROOT / "QueuePane.tsx").read_text(encoding="utf-8"))
    fleet = localized_source((ROOT / "FleetQueue.tsx").read_text(encoding="utf-8"))
    api = localized_source((ROOT / "queueApi.ts").read_text(encoding="utf-8"))
    tabs = localized_source((ROOT / "drawerTabs.ts").read_text(encoding="utf-8"))

    assert not source_contains(queue, "fetchFleetQueue")
    assert not source_contains(queue, "inbox")
    assert not source_contains(queue, "outbox")
    assert source_contains(fleet, "agents + automation")
    assert source_contains(fleet, "human")
    assert source_contains(fleet, "Project")
    assert source_contains(fleet, "Session")
    assert source_contains(api, "project_id")
    assert source_contains(api, "target_session_id")
    # Queue keeps the docked column, because deciding to send needs the terminal beside
    # it. The fleet view has no send button, so it is a modal and not a tab at all.
    assert source_contains(tabs, "id: 'queue'") and source_contains(tabs, "scope: 'session'")
    assert not source_contains(tabs, "id: 'mailbox'")
    assert source_contains(fleet, 'role="dialog"')
    assert source_contains(fleet, "useModalFocus")


def test_a_drafted_spawn_request_is_approved_by_a_human_in_the_fleet_queue() -> None:
    fleet = localized_source((ROOT / "FleetQueue.tsx").read_text(encoding="utf-8"))
    api = localized_source((ROOT / "queueApi.ts").read_text(encoding="utf-8"))
    app = localized_source((ROOT / "App.tsx").read_text(encoding="utf-8"))

    assert source_contains(fleet, "spawn request")
    assert source_contains(fleet, "Approve and start session")
    assert source_contains(fleet, "Dismiss")
    assert source_contains(fleet, "decideSpawnRequest")
    assert source_contains(api, "/decide")
    assert source_contains(fleet, "Nothing was started")
    assert not source_contains(app, "observations.open")
    assert not source_contains(app, "<Observations")


def test_the_fleet_queue_is_reachable_from_any_device() -> None:
    """Two ways in, for the two questions that lead there: the app menu answers "is
    anything waiting anywhere", and the Queue tab's control answers "what else is staged
    while I look at this one" — the same watch-here/act-there pair Processes has with the
    process fleet."""
    app = localized_source((ROOT / "App.tsx").read_text(encoding="utf-8"))
    drawer = localized_source((ROOT / "UtilityDrawer.tsx").read_text(encoding="utf-8"))
    pane = localized_source((ROOT / "QueuePane.tsx").read_text(encoding="utf-8"))

    assert source_contains(app, "queue.fleet")
    assert source_contains(app, "Fleet queue")
    assert source_contains(app, "<FleetQueue")
    assert source_contains(drawer, "onOpenFleetQueue")
    assert source_contains(pane, "onOpenFleetQueue")


def test_the_queue_is_a_drawer_tab_and_the_pane_leaf_is_only_a_pop_out() -> None:
    """The placement itself, pinned: a queue that replaces or covers its terminal cannot
    show the state the decision to send is made from."""
    tabs = localized_source((ROOT / "drawerTabs.ts").read_text(encoding="utf-8"))
    drawer = localized_source((ROOT / "UtilityDrawer.tsx").read_text(encoding="utf-8"))
    app = localized_source((ROOT / "App.tsx").read_text(encoding="utf-8"))

    assert source_contains(tabs, "id: 'queue'")
    assert source_contains(tabs, "scope: 'session'")
    assert source_contains(drawer, "<QueuePane")
    # The chip focuses its session before opening the panel: the tab follows focus, so a
    # chip clicked on an unfocused pane would otherwise show another agent's queue.
    assert source_contains(app, "if (session) await selectSession(session)")
    assert source_contains(app, "openDrawerTab('queue',session?.project_id||projectId)")
    # Exactly one place still builds the leaf, and it is the explicit pop-out.
    assert app.count("resourceLeaf('queue'") == 1
    assert source_contains(app, "onQueueOpenAsTab")
