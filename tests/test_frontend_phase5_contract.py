"""Phase 5 frontend contract: the affordances the safety story depends on.

Auto-delivery and agent messaging are only as bounded as the surfaces that let
a human see and stop them. These pin that the controls exist and are wired to
the daemon's typed operations rather than to browser-side state.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[1] / "frontend" / "src"


def test_the_queue_tab_exposes_the_bounded_opt_in_and_its_state() -> None:
    pane = (ROOT / "QueuePane.tsx").read_text(encoding="utf-8")

    assert "auto-deliver armed messages" in pane
    assert "accept agent messages armed" in pane
    # The strip must show the bounds, not just an on/off: sends left, time
    # left, and why it is off when it is.
    assert "send${row.sends_remaining === 1 ? '' : 's'} left" in pane
    assert "${minutes} min left" in pane
    assert "quiet hours — paused" in pane
    assert "paused (emergency stop)" in pane
    # The opt-in cannot be offered when the install's master switch is off.
    assert "disabled={busyId === 'auto' || !auto?.master_enabled}" in pane


def test_scheduling_is_a_property_of_the_queued_item() -> None:
    pane = (ROOT / "QueuePane.tsx").read_text(encoding="utf-8")
    api = (ROOT / "queueApi.ts").read_text(encoding="utf-8")

    assert "scheduleQueueMessage" in pane
    assert "Clear schedule" in pane
    assert "not_before: Date.now() / 1000 + preset.seconds" in pane
    # No browser timer anywhere in the send path.
    assert "setTimeout" not in pane
    assert "a browser timer dies with the tab" in api


def test_the_mailbox_carries_the_emergency_controls() -> None:
    # The mailbox is the Queue panel's inbox/outbox scopes, not a modal of its own; what
    # matters for the safety story is that these controls exist and stay one gesture away,
    # not which surface hosts them.
    pane = (ROOT / "QueuePane.tsx").read_text(encoding="utf-8")

    assert "'session' | 'inbox' | 'outbox'" in pane
    assert "pause all auto-delivery" in pane
    assert "report unsafe delivery" in pane
    assert "Stops every automatic delivery immediately, on every session" in pane
    # Proving-period numbers are visible where the operator reviews deliveries.
    assert "{promotion.proving_days}/{promotion.required_days} days" in pane
    assert "Revoke" in pane
    assert "Deliberately not a second transcript" in pane


def test_a_drafted_spawn_request_is_approved_by_a_human_in_the_inbox() -> None:
    observations = (ROOT / "Observations.tsx").read_text(encoding="utf-8")

    assert "spawn request" in observations
    assert "approve &amp; start session" in observations
    assert "dismiss" in observations
    assert "/decide" in observations
    assert "Nothing was started." in observations
    # A typed request is a decision, not a note: it never joins the batch insert.
    assert "Typed requests are decisions, not notes" in observations


def test_the_app_menu_reaches_the_mailbox_from_any_device() -> None:
    app = (ROOT / "App.tsx").read_text(encoding="utf-8")

    assert "mailbox.open" in app
    assert "Mailbox…" in app
    # It lands on the inbox scope of the Queue panel. The drawer is the one surface on
    # both desktop and mobile, which is what keeps the emergency controls one gesture away
    # from a phone — the modal it replaced was reached the same way and is gone.
    assert "scope:'inbox'" in app


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
    assert "openDrawerTab('queue')" in app
    # Exactly one place still builds the leaf, and it is the explicit pop-out.
    assert app.count("resourceLeaf('queue'") == 1
    assert "onQueueOpenAsTab" in app
