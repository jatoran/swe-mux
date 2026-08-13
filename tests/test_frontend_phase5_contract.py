"""Phase 5 frontend contract: the affordances the safety story depends on.

Auto-delivery and agent messaging are only as bounded as the surfaces that let
a human see and stop them. These pin that the controls exist and are wired to
the daemon's typed operations rather than to browser-side state.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[1] / "frontend" / "src"


def test_the_queue_tab_exposes_the_bounded_conversation_policy_and_its_state() -> None:
    pane = (ROOT / "QueuePane.tsx").read_text(encoding="utf-8")

    assert "auto-deliver armed messages" in pane
    assert "accept agent messages armed" in pane
    # The strip must show the bounds, not just an on/off: sends left, time
    # left, and why it is off when it is.
    assert "send${row.sends_remaining === 1 ? '' : 's'} left" in pane
    assert "${minutes} min left" in pane
    assert "quiet hours — paused" in pane
    assert "paused (emergency stop)" in pane
    # The conversation policy cannot be changed while the install master is off.
    assert "disabled={busyId === 'auto' || !auto?.master_enabled}" in pane


def test_settings_explain_that_agent_conversations_default_on() -> None:
    settings = (ROOT / "Settings.tsx").read_text(encoding="utf-8")

    assert (
        "every new observed agent conversation starts with bounded auto-delivery enabled"
        in settings
    )
    assert "Allow auto-delivery for agent conversations" in settings


def test_scheduling_is_a_property_of_the_queued_item() -> None:
    pane = (ROOT / "QueuePane.tsx").read_text(encoding="utf-8")
    api = (ROOT / "queueApi.ts").read_text(encoding="utf-8")

    assert "scheduleQueueMessage" in pane
    assert "Clear schedule" in pane
    # Stamped on the daemon's clock, because the daemon is what waits on it: a
    # browser out of step would release the message early or hold it late.
    assert "not_before: serverNow() + preset.seconds" in pane
    assert "Date.now()" not in pane
    # No browser timer anywhere in the send path.
    assert "setTimeout" not in pane
    assert "a browser timer dies with the tab" in api


def test_the_emergency_controls_need_nothing_opened_to_reach() -> None:
    """A brake reachable only by opening an overlay is a brake you cannot reach in the
    moment you want it. The install-wide stop lives on the one queue surface that
    delivers, and on a command that needs nothing open at all."""
    pane = (ROOT / "QueuePane.tsx").read_text(encoding="utf-8")
    app = (ROOT / "App.tsx").read_text(encoding="utf-8")

    assert "pause all auto-delivery" in pane
    assert "report unsafe delivery" in pane
    assert "Stops every automatic delivery immediately, on every session" in pane
    assert "autodelivery.pause" in app
    assert "Pause all auto-delivery (install-wide)" in app
    assert "Resume auto-delivery (install-wide)" in app
    # The status line and the stop behind it survive an empty target: the install-wide
    # state is true with no session focused, and it is the state that makes every
    # per-session reading a lie when it is off.
    assert "if (!sessionId) return 'armed for this install'" in pane


def test_the_fleet_queue_reports_the_delivery_state_and_never_owns_it() -> None:
    """It is a review surface. Nothing in it delivers, and nothing in it is a brake."""
    fleet = (ROOT / "FleetQueue.tsx").read_text(encoding="utf-8")
    pane = (ROOT / "QueuePane.tsx").read_text(encoding="utf-8")

    assert "sendQueueMessage" not in fleet
    assert "Send now" not in fleet
    assert "setAutoPaused" not in fleet
    assert "reportUnsafeDelivery" not in fleet
    assert "paused (emergency stop)" in fleet
    assert "Revoke" in fleet
    # Proving-period numbers stay visible in both places an operator reviews deliveries.
    assert "{promotion.proving_days}/{promotion.required_days} days" in fleet
    assert "{auto.promotion.proving_days}/{auto.promotion.required_days} days" in pane


def test_the_session_queue_and_the_fleet_queue_have_non_overlapping_scopes() -> None:
    queue = (ROOT / "QueuePane.tsx").read_text(encoding="utf-8")
    fleet = (ROOT / "FleetQueue.tsx").read_text(encoding="utf-8")
    api = (ROOT / "queueApi.ts").read_text(encoding="utf-8")
    tabs = (ROOT / "drawerTabs.ts").read_text(encoding="utf-8")

    assert "fetchFleetQueue" not in queue
    assert "inbox" not in queue
    assert "outbox" not in queue
    assert "agents + automation" in fleet
    assert "human" in fleet
    assert "Project" in fleet
    assert "Session" in fleet
    assert "project_id" in api
    assert "target_session_id" in api
    # Queue keeps the docked column, because deciding to send needs the terminal beside
    # it. The fleet view has no send button, so it is a modal and not a tab at all.
    assert "id: 'queue'" in tabs and "scope: 'session'" in tabs
    assert "id: 'mailbox'" not in tabs
    assert 'role="dialog"' in fleet
    assert "useModalFocus" in fleet


def test_a_drafted_spawn_request_is_approved_by_a_human_in_the_fleet_queue() -> None:
    fleet = (ROOT / "FleetQueue.tsx").read_text(encoding="utf-8")
    api = (ROOT / "queueApi.ts").read_text(encoding="utf-8")
    app = (ROOT / "App.tsx").read_text(encoding="utf-8")

    assert "spawn request" in fleet
    assert "Approve and start session" in fleet
    assert "Dismiss" in fleet
    assert "decideSpawnRequest" in fleet
    assert "/decide" in api
    assert "Nothing was started" in fleet
    assert "observations.open" not in app
    assert "<Observations" not in app


def test_the_fleet_queue_is_reachable_from_any_device() -> None:
    """Two ways in, for the two questions that lead there: the app menu answers "is
    anything waiting anywhere", and the Queue tab's control answers "what else is staged
    while I look at this one" — the same watch-here/act-there pair Processes has with the
    process fleet."""
    app = (ROOT / "App.tsx").read_text(encoding="utf-8")
    drawer = (ROOT / "UtilityDrawer.tsx").read_text(encoding="utf-8")
    pane = (ROOT / "QueuePane.tsx").read_text(encoding="utf-8")

    assert "queue.fleet" in app
    assert "Fleet queue" in app
    assert "<FleetQueue" in app
    assert "onOpenFleetQueue" in drawer
    assert "onOpenFleetQueue" in pane


def test_the_queue_is_a_drawer_tab_and_the_pane_leaf_is_only_a_pop_out() -> None:
    """The placement itself, pinned: a queue that replaces or covers its terminal cannot
    show the state the decision to send is made from."""
    tabs = (ROOT / "drawerTabs.ts").read_text(encoding="utf-8")
    drawer = (ROOT / "UtilityDrawer.tsx").read_text(encoding="utf-8")
    app = (ROOT / "App.tsx").read_text(encoding="utf-8")

    assert "id: 'queue'" in tabs
    assert "scope: 'session'" in tabs
    assert "<QueuePane" in drawer
    # The chip focuses its session before opening the panel: the tab follows focus, so a
    # chip clicked on an unfocused pane would otherwise show another agent's queue.
    assert "if (session) await selectSession(session)" in app
    assert "openDrawerTab('queue',session?.project_id||projectId)" in app
    # Exactly one place still builds the leaf, and it is the explicit pop-out.
    assert app.count("resourceLeaf('queue'") == 1
    assert "onQueueOpenAsTab" in app
