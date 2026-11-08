"""What `notify` does when the sender leaves `delivery` out (2026-11-08).

The install's `agent_message_default_delivery` decides, and it is `now`: a peer's
message is written into the target's running turn as soon as that is safe, so the
target hears it before it finishes rather than after. The choice used to sit with
every sender, and senders chose to wait almost every time.

Two rules make that default safe to apply to every message, and both are pinned
here. A defaulted `now` that a mid-turn gate refuses is staged `when_idle` and the
result names the gate - the default must never be the one spelling of `notify` that
a receiver's opt-out breaks - while an *explicit* `now` still refuses. And a
defaulted mid-turn message says it makes no claim of urgency, because one that
said "urgent" of every routine note would teach receivers to drop their work for it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from swe_mux.config import AGENT_MESSAGE_DELIVERY_MODES, Config, _validate
from swe_mux.prompt_queue import DELIVERY_MODES, QueueError
from tests.test_agent_messaging import Harness, live_session


@pytest.fixture
def harness(tmp_path: Path) -> Any:
    built = Harness(tmp_path, live_session("s1"), live_session("s2"))
    yield built
    built.close()


async def _granted(built: Harness) -> None:
    """The target Project permits mid-turn delivery and s2 has not opted out."""
    built.interject_grants[str(built.root)] = "granted"
    await built.auto.tick()


# -- the setting --------------------------------------------------------------


def test_the_install_default_is_mid_turn() -> None:
    assert Config().agent_message_default_delivery == "now"


def test_the_setting_accepts_exactly_the_queue_item_modes() -> None:
    """Spelled out in `config.py` so loading a config does not import the queue,
    and held equal to the queue's own set here rather than trusted."""
    assert set(AGENT_MESSAGE_DELIVERY_MODES) == set(DELIVERY_MODES)
    for mode in DELIVERY_MODES:
        _validate(Config(agent_message_default_delivery=mode))
    with pytest.raises(ValueError) as caught:
        _validate(Config(agent_message_default_delivery="immediately"))
    assert "agent_message_default_delivery" in str(caught.value)


# -- the default, applied ------------------------------------------------------


@pytest.mark.asyncio
async def test_an_omitted_delivery_lands_mid_turn_where_that_is_permitted(
    harness: Harness,
) -> None:
    await _granted(harness)
    result = await harness.messaging.notify(
        harness.manager.sessions["s1"], target="s2", body="the schema moved to v2"
    )
    message = await harness.store.message(result["message_id"])
    assert message is not None
    # A property of the item, so the manual and automatic paths both honour it.
    assert message["constraints"]["delivery"] == "now"
    assert result["delivery"] == "now"
    assert result["delivery_source"] == "default"
    assert "delivery_fallback" not in result
    # The envelope says it may arrive mid-turn and that this is routine, not urgent.
    assert "may land mid-turn, as messages do by default here" in message["body"]
    assert "no claim of urgency or authority" in message["body"]
    assert "the sender asked" not in message["body"]


@pytest.mark.asyncio
async def test_the_full_envelope_names_a_defaulted_mid_turn_arrival_too(
    harness: Harness,
) -> None:
    await _granted(harness)
    result = await harness.messaging.notify(
        harness.manager.sessions["s1"], target="s2", body="heads up", envelope="full"
    )
    message = await harness.store.message(result["message_id"])
    assert message is not None
    assert "delivery: may be written into a turn that is already running" in message["body"]
    assert "how messages are delivered here by default" in message["body"]


@pytest.mark.parametrize(
    ("close_gate", "code"),
    [
        ("project", "interject_not_granted"),
        ("receiver", "interject_refused_by_target"),
        ("install", "interject_disabled"),
    ],
)
@pytest.mark.asyncio
async def test_a_closed_gate_turns_the_default_into_when_idle_and_says_which(
    harness: Harness, close_gate: str, code: str
) -> None:
    await _granted(harness)
    if close_gate == "project":
        harness.interject_grants[str(harness.root)] = "off"
    elif close_gate == "receiver":
        await harness.auto.set_accept_agent_interjections("s2", False)
    else:
        harness.config.agent_interject_enabled = False

    result = await harness.messaging.notify(
        harness.manager.sessions["s1"], target="s2", body="when you can"
    )
    message = await harness.store.message(result["message_id"])
    assert message is not None
    assert result["state"] == "armed"
    assert "delivery" not in message["constraints"]
    assert "mid-turn" not in message["body"]
    assert result["delivery"] == "when_idle"
    assert result["delivery_source"] == "default"
    fallback = result["delivery_fallback"]
    assert fallback["from"] == "now"
    assert fallback["code"] == code
    # The cause alone: telling a sender to resend as when_idle, beside a result
    # that already did exactly that, would read as an instruction to duplicate it.
    assert "when_idle" not in fallback["cause"]
    assert code in result["note"]


@pytest.mark.asyncio
async def test_an_explicit_now_still_refuses_on_the_same_gate(harness: Harness) -> None:
    await harness.auto.tick()
    with pytest.raises(QueueError) as caught:
        await harness.messaging.notify(
            harness.manager.sessions["s1"], target="s2", body="urgent", delivery="now"
        )
    assert caught.value.code == "interject_not_granted"


@pytest.mark.asyncio
async def test_a_spent_mid_turn_budget_falls_back_without_spending_more(
    harness: Harness,
) -> None:
    clock = {"now": 1000.0}
    harness.messaging._clock = lambda: clock["now"]
    harness.config.agent_message_limits_enabled = True
    harness.config.agent_interject_hourly_budget = 1
    harness.config.agent_interject_min_interval_seconds = 0.0
    await _granted(harness)

    first = await harness.messaging.notify(
        harness.manager.sessions["s1"], target="s2", body="one"
    )
    assert first["delivery"] == "now"
    clock["now"] += 1.0
    second = await harness.messaging.notify(
        harness.manager.sessions["s1"], target="s2", body="two"
    )
    assert second["delivery"] == "when_idle"
    assert second["delivery_fallback"]["code"] == "interject_budget_exhausted"
    # Only the delivered-mid-turn message was charged.
    assert len(harness.messaging._interjects_by_origin["s1"]) == 1


@pytest.mark.asyncio
async def test_an_install_that_chose_when_idle_never_asks_the_mid_turn_gates(
    harness: Harness,
) -> None:
    """With the default set to wait, a closed gate is not a fallback: nothing
    asked for mid-turn, so nothing is reported as having been refused it."""
    harness.config.agent_message_default_delivery = "when_idle"
    harness.config.agent_interject_enabled = False
    harness.config.auto_delivery_enabled = True
    await harness.auto.tick()
    result = await harness.messaging.notify(
        harness.manager.sessions["s1"], target="s2", body="later is fine"
    )
    assert result["delivery"] == "when_idle"
    assert result["delivery_source"] == "default"
    assert "delivery_fallback" not in result
    assert "never interrupts an active turn" in result["note"]


@pytest.mark.asyncio
async def test_a_sender_may_still_ask_to_wait(harness: Harness) -> None:
    await _granted(harness)
    result = await harness.messaging.notify(
        harness.manager.sessions["s1"],
        target="s2",
        body="for after your current task",
        delivery="when_idle",
    )
    assert result["delivery"] == "when_idle"
    assert result["delivery_source"] == "requested"
    assert "delivery_fallback" not in result


@pytest.mark.asyncio
async def test_a_dry_run_reports_the_mode_and_any_fallback_without_spending(
    harness: Harness,
) -> None:
    await harness.auto.tick()
    preview = await harness.messaging.notify(
        harness.manager.sessions["s1"], target="s2", body="check", dry_run=True
    )
    assert preview["dry_run"] is True
    assert preview["delivery"] == "when_idle"
    assert preview["delivery_fallback"]["code"] == "interject_not_granted"

    harness.config.auto_delivery_enabled = True
    await _granted(harness)
    preview = await harness.messaging.notify(
        harness.manager.sessions["s1"], target="s2", body="check", dry_run=True
    )
    assert preview["delivery"] == "now"
    assert "written into that turn" in preview["note"]
    assert harness.messaging._interjects_by_origin.get("s1", []) == []


@pytest.mark.asyncio
async def test_a_correlated_retry_reports_what_the_original_holds(
    harness: Harness,
) -> None:
    """The first call fell back; the retry finds the gate open again. It returns
    the original message, so it must report the original's mode - and spend no
    mid-turn slot for a message it did not stage."""
    await harness.auto.tick()
    first = await harness.messaging.notify(
        harness.manager.sessions["s1"], target="s2", body="once", correlation_id="c-1"
    )
    assert first["delivery"] == "when_idle"

    await _granted(harness)
    retry = await harness.messaging.notify(
        harness.manager.sessions["s1"], target="s2", body="once", correlation_id="c-1"
    )
    assert retry["deduplicated"] is True
    assert retry["message_id"] == first["message_id"]
    assert retry["delivery"] == "when_idle"
    assert "delivery_fallback" not in retry
    assert harness.messaging._interjects_by_origin.get("s1", []) == []
