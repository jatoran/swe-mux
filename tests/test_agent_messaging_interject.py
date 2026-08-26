"""Mid-turn delivery: who may ask, and what the sender is told when nobody will.

Two things live here, and they are only in one file because they are the two
halves of the same 2026-08-19 failure - three sessions in a working exchange
went quiet, and nothing anywhere could say why.

- `delivery="now"` lets a sender ask for a message to land in a turn that is
  already running. Asking is gated three ways; whether the write is *safe* is
  the readiness tracker's separate predicate, tested next door.
- A reply refreshes the replying session's own auto-delivery budget, and a
  notify result says out loud when nothing will deliver what it just staged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from swe_mux.prompt_queue import SEND_CAP_REASON, QueueError
from tests.test_agent_messaging import Harness, live_session


@pytest.fixture
def harness(tmp_path: Path) -> Any:
    built = Harness(tmp_path, live_session("s1"), live_session("s2"))
    yield built
    built.close()


async def _delivered_peer_message(built: Harness) -> None:
    """s2 writes to s1 and it lands, so s1's next notify continues that thread.

    Delivery matters: a session's inbound relay context follows the peer's most
    recent *delivered* message, so answering something still sitting armed opens
    a fresh thread rather than continuing one.
    """
    staged = await built.messaging.notify(
        built.manager.sessions["s2"], target="s1", body="how far along are you?"
    )
    await built.service.send_next(staged["message_id"], revision=1)


async def _granted(built: Harness) -> None:
    """The target Project permits mid-turn delivery and s2 has not opted out."""
    built.interject_grants[str(built.root)] = "granted"
    await built.auto.tick()


@pytest.mark.asyncio
async def test_a_granted_interject_is_carried_on_the_item_and_named_in_the_envelope(
    harness: Harness,
) -> None:
    await _granted(harness)
    result = await harness.messaging.notify(
        harness.manager.sessions["s1"],
        target="s2",
        body="stop using the v1 endpoint, I just deleted it",
        delivery="now",
    )
    message = await harness.store.message(result["message_id"])
    assert message is not None
    # A property of the item, checked in send_next - never a second delivery path.
    assert message["constraints"]["delivery"] == "now"
    # The receiver cannot tell a mid-turn arrival from an ordinary one out of the
    # text: the CLI buffers the paste and hands it over at the turn boundary.
    assert "delivery: written into a turn that was already running" in message["body"]
    assert "urgency, not about authority" in message["body"]


@pytest.mark.asyncio
async def test_an_ordinary_message_carries_no_delivery_constraint(
    harness: Harness,
) -> None:
    """The default is not persisted: an item without the key means what every
    item meant before the mode existed."""
    await harness.auto.tick()
    result = await harness.messaging.notify(
        harness.manager.sessions["s1"], target="s2", body="when you get a moment"
    )
    message = await harness.store.message(result["message_id"])
    assert message is not None
    assert "delivery" not in message["constraints"]
    assert "written into a turn" not in message["body"]


@pytest.mark.asyncio
async def test_a_project_that_did_not_grant_it_refuses(harness: Harness) -> None:
    await harness.auto.tick()
    with pytest.raises(QueueError) as caught:
        await harness.messaging.notify(
            harness.manager.sessions["s1"], target="s2", body="urgent", delivery="now"
        )
    assert caught.value.code == "interject_not_granted"
    # The refusal has to name the way through, or the sender abandons the message
    # rather than sending it the ordinary way.
    assert "without `delivery`" in str(caught.value)


@pytest.mark.asyncio
async def test_the_install_master_switch_refuses_it_everywhere(harness: Harness) -> None:
    await _granted(harness)
    harness.config.agent_interject_enabled = False
    with pytest.raises(QueueError) as caught:
        await harness.messaging.notify(
            harness.manager.sessions["s1"], target="s2", body="urgent", delivery="now"
        )
    assert caught.value.code == "interject_disabled"


@pytest.mark.asyncio
async def test_the_receiver_keeps_a_veto(harness: Harness) -> None:
    """Being written to mid-turn costs the receiver attention immediately, so the
    receiver's own switch is independent of the Project's standing permission."""
    await _granted(harness)
    await harness.auto.set_accept_agent_interjections("s2", False)
    with pytest.raises(QueueError) as caught:
        await harness.messaging.notify(
            harness.manager.sessions["s1"], target="s2", body="urgent", delivery="now"
        )
    assert caught.value.code == "interject_refused_by_target"
    # And the ordinary path is untouched by that opt-out.
    ordinary = await harness.messaging.notify(
        harness.manager.sessions["s1"], target="s2", body="urgent"
    )
    assert ordinary["state"] == "armed"


@pytest.mark.asyncio
async def test_the_per_origin_budget_and_the_per_target_floor_both_bound_it(
    harness: Harness,
) -> None:
    clock = {"now": 1000.0}
    harness.messaging._clock = lambda: clock["now"]
    # The configured interject bounds bind only while the limits toggle is on
    # (2026-08-25); off, the fixed backstops apply instead.
    harness.config.agent_message_limits_enabled = True
    harness.config.agent_interject_hourly_budget = 2
    harness.config.agent_interject_min_interval_seconds = 60.0
    await _granted(harness)

    await harness.messaging.notify(
        harness.manager.sessions["s1"], target="s2", body="one", delivery="now"
    )
    # The floor stops a peer machine-gunning a session that is trying to work.
    clock["now"] += 5.0
    with pytest.raises(QueueError) as too_soon:
        await harness.messaging.notify(
            harness.manager.sessions["s1"], target="s2", body="two", delivery="now"
        )
    assert too_soon.value.code == "interject_too_soon"

    clock["now"] += 120.0
    await harness.messaging.notify(
        harness.manager.sessions["s1"], target="s2", body="two", delivery="now"
    )
    clock["now"] += 120.0
    with pytest.raises(QueueError) as spent:
        await harness.messaging.notify(
            harness.manager.sessions["s1"], target="s2", body="three", delivery="now"
        )
    assert spent.value.code == "interject_budget_exhausted"

    # The hour rolls and the budget comes back.
    clock["now"] += 3601.0
    await harness.messaging.notify(
        harness.manager.sessions["s1"], target="s2", body="later", delivery="now"
    )


@pytest.mark.asyncio
async def test_a_correlated_retry_does_not_spend_a_second_slot(
    harness: Harness,
) -> None:
    """The bounds are charged once the item exists, not once it is asked for.

    A retry with the same correlation id returns the original message rather than
    staging a second one. Charging at check time would make that retry spend a
    slot and then trip the interval floor, so an idempotent call would answer
    with a refusal instead of the message it already staged.
    """
    clock = {"now": 1000.0}
    harness.messaging._clock = lambda: clock["now"]
    await _granted(harness)
    first = await harness.messaging.notify(
        harness.manager.sessions["s1"],
        target="s2",
        body="urgent",
        delivery="now",
        correlation_id="corr-1",
    )
    clock["now"] += 1.0
    retry = await harness.messaging.notify(
        harness.manager.sessions["s1"],
        target="s2",
        body="urgent",
        delivery="now",
        correlation_id="corr-1",
    )
    assert retry["message_id"] == first["message_id"]
    assert retry["deduplicated"] is True


@pytest.mark.asyncio
async def test_an_unknown_delivery_mode_is_refused(harness: Harness) -> None:
    await _granted(harness)
    with pytest.raises(QueueError) as caught:
        await harness.messaging.notify(
            harness.manager.sessions["s1"],
            target="s2",
            body="hi",
            delivery="immediately",
        )
    assert caught.value.code == "invalid_delivery"


# -- the stall, and what clears it -------------------------------------------


@pytest.mark.asyncio
async def test_a_reply_restores_the_replying_sessions_capped_grant(
    harness: Harness,
) -> None:
    """The 2026-08-19 stall, from the messaging side.

    s1's inbound grant hit the consecutive cap. Answering in a thread s2 started
    is direct evidence that s1 consumed what was delivered and is still working
    the exchange, which is the opposite of the unattended run the cap exists to
    stop - so it clears, and the next peer message can reach s1 again.
    """
    await harness.auto.tick()
    await _delivered_peer_message(harness)
    await harness.store.set_auto_policy(
        "s1", enabled=0, disabled_reason=SEND_CAP_REASON, sends_used=3
    )
    await harness.messaging.notify(
        harness.manager.sessions["s1"], target="s2", body="on it"
    )
    restored = await harness.store.auto_policy("s1")
    assert restored is not None
    assert restored["enabled"]
    assert restored["disabled_reason"] is None
    assert restored["sends_used"] == 0
    # The *target* is untouched: only the session that produced the evidence.
    target = await harness.store.auto_policy("s2")
    assert target is not None and target["enabled"]


@pytest.mark.asyncio
async def test_opening_a_fresh_thread_is_not_evidence(harness: Harness) -> None:
    """Writing to a peer nobody has written to proves the caller is alive, and
    nothing about whether it read what was delivered to it. The cap counts
    deliveries *into* a session, so only answering in a thread somebody else
    started contradicts it."""
    await harness.auto.tick()
    await harness.store.set_auto_policy(
        "s1", enabled=0, disabled_reason=SEND_CAP_REASON, sends_used=3
    )
    await harness.messaging.notify(
        harness.manager.sessions["s1"], target="s2", body="starting something new"
    )
    unchanged = await harness.store.auto_policy("s1")
    assert unchanged is not None
    assert not unchanged["enabled"]
    assert unchanged["disabled_reason"] == SEND_CAP_REASON


@pytest.mark.asyncio
async def test_a_reply_does_not_clear_a_decision(harness: Harness) -> None:
    await harness.auto.tick()
    await _delivered_peer_message(harness)
    await harness.store.set_auto_policy(
        "s1", enabled=0, disabled_reason="disabled by user"
    )
    await harness.messaging.notify(
        harness.manager.sessions["s1"], target="s2", body="on it"
    )
    held = await harness.store.auto_policy("s1")
    assert held is not None
    assert not held["enabled"]
    assert held["disabled_reason"] == "disabled by user"


@pytest.mark.asyncio
async def test_the_sender_is_told_when_nothing_will_deliver_the_message(
    harness: Harness,
) -> None:
    """`armed` alone is unactionable: it is the same word for a peer that is
    merely busy and for one nothing can reach without a human. A sender that
    cannot tell them apart waits, and the exchange stops with nobody able to
    say why.
    """
    await harness.auto.tick()
    harness.config.auto_delivery_enabled = False
    result = await harness.messaging.notify(
        harness.manager.sessions["s1"], target="s2", body="anything?"
    )
    assert result["state"] == "armed"
    assert result["target_delivery"]["auto_delivery"] is False
    assert "switched off for this install" in result["target_delivery"]["blocked_by"]
    assert "nothing will send it automatically" in result["note"]
    assert "Say so rather than waiting silently" in result["note"]

    status = await harness.messaging.message_status(
        harness.manager.sessions["s1"], result["message_id"]
    )
    assert status["status"] == "armed"
    assert status["target_delivery"]["auto_delivery"] is False


@pytest.mark.asyncio
async def test_a_capped_target_is_named_as_the_reason_nothing_will_send(
    harness: Harness,
) -> None:
    await harness.auto.tick()
    harness.config.auto_delivery_enabled = True
    await harness.store.set_auto_policy("s2", enabled=0, disabled_reason=SEND_CAP_REASON)
    result = await harness.messaging.notify(
        harness.manager.sessions["s1"], target="s2", body="anything?"
    )
    assert result["target_delivery"]["blocked_by"] == SEND_CAP_REASON


@pytest.mark.asyncio
async def test_a_healthy_target_reports_that_delivery_will_happen(
    harness: Harness,
) -> None:
    await harness.auto.tick()
    harness.config.auto_delivery_enabled = True
    result = await harness.messaging.notify(
        harness.manager.sessions["s1"], target="s2", body="anything?"
    )
    assert result["target_delivery"] == {
        "auto_delivery": True,
        "blocked_by": None,
        "sends_remaining": harness.config.auto_delivery_max_consecutive,
    }
    assert "never interrupts an active turn" in result["note"]
