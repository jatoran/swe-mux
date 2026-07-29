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
    assert "auto-delivery is paused (emergency stop)" in pane
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
    mailbox = (ROOT / "Mailbox.tsx").read_text(encoding="utf-8")

    assert "pause all auto-delivery" in mailbox
    assert "report unsafe delivery" in mailbox
    assert "Stops every automatic delivery immediately, on every session" in mailbox
    # Proving-period numbers are visible where the operator reviews deliveries.
    assert "proving ${promotion.proving_days}/${promotion.required_days} days" in mailbox
    assert "revoke" in mailbox
    assert "Deliberately not a second transcript" in mailbox


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
