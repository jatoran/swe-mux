"""What `delivery="now"` changes in the one place that writes to a PTY.

The load-bearing claim is that it is *not* an override. `send_next` still runs
every protection first, still refuses a confirm from a non-human initiator, and
still refuses anything the readiness tracker has not positively cleared - the
only difference is which predicate clears it. These pin that, and pin that an
ordinary item is unaffected by any of it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from swe_mux.prompt_queue import QueueError, delivery_mode, normalize_constraints
from tests.test_prompt_queue import Harness, ReadinessStub, live_session


def _harness(tmp_path: Path, **readiness: Any) -> Harness:
    built = Harness(tmp_path, live_session("s1"), live_session("s2"))
    built.readiness.__dict__.update(ReadinessStub(**readiness).__dict__)
    return built


async def _now_item(built: Harness, body: str = "urgent") -> dict[str, Any]:
    return await built.service.enqueue(
        target_session_id="s1",
        body=body,
        armed=True,
        constraints={"delivery": "now"},
    )


def test_the_mode_is_validated_and_the_default_is_not_persisted() -> None:
    assert normalize_constraints({"delivery": "when_idle"}) is None
    assert normalize_constraints({"delivery": "now"}) == {"delivery": "now"}
    with pytest.raises(QueueError) as caught:
        normalize_constraints({"delivery": "immediately"})
    assert caught.value.code == "invalid_constraints"
    # An item with no constraints at all means what every item meant before.
    assert delivery_mode({}) == "when_idle"
    assert delivery_mode({"constraints": {"delivery": "now"}}) == "now"


@pytest.mark.asyncio
async def test_a_now_item_delivers_into_a_running_turn(tmp_path: Path) -> None:
    harness = _harness(
        tmp_path, state="blocked", reasons=["root_agent_working"], interject_state="safe"
    )
    try:
        message = await _now_item(harness)
        result = await harness.service.send_next(
            message["id"], revision=message["revision"], initiator="auto"
        )
        assert result["status"] == "sent"
        # Not an override: nothing confirmed, and the audit says which it was.
        assert result["confirmed"] is False
        assert result["interjected"] is True
        rows = await harness.store.deliveries(message["id"])
        assert rows[0]["interjected"] == 1
        assert rows[0]["confirmed"] == 0
        assert rows[0]["delivery_state"] == "blocked"
    finally:
        harness.store.close()


@pytest.mark.asyncio
async def test_an_ordinary_item_still_waits_on_the_same_running_turn(
    tmp_path: Path,
) -> None:
    """The mode is a property of the item, so one message asking for it never
    widens the contract for the next."""
    harness = _harness(
        tmp_path, state="blocked", reasons=["root_agent_working"], interject_state="safe"
    )
    try:
        message = await harness.service.enqueue(
            target_session_id="s1", body="whenever", armed=True
        )
        with pytest.raises(QueueError) as caught:
            await harness.service.send_next(
                message["id"], revision=message["revision"], initiator="auto"
            )
        assert caught.value.code == "delivery_not_safe"
        assert caught.value.payload["reasons"] == ["root_agent_working"]
    finally:
        harness.store.close()


@pytest.mark.asyncio
async def test_a_now_item_is_refused_when_the_interject_predicate_says_no(
    tmp_path: Path,
) -> None:
    harness = _harness(
        tmp_path,
        state="blocked",
        reasons=["root_agent_working"],
        interject_state="blocked",
        interject_reasons=["screen_does_not_show_a_running_turn"],
    )
    try:
        message = await _now_item(harness)
        with pytest.raises(QueueError) as caught:
            await harness.service.send_next(
                message["id"], revision=message["revision"], initiator="auto"
            )
        assert caught.value.code == "delivery_not_safe"
        # Reported with the reasons that stopped *it*. The ordinary reasons would
        # say `root_agent_working` - the one thing it was allowed to step over -
        # and leave the real blocker unnamed.
        assert caught.value.payload["reasons"] == ["screen_does_not_show_a_running_turn"]
        assert not harness.writes
    finally:
        harness.store.close()


@pytest.mark.asyncio
async def test_a_protection_is_not_reachable_through_the_mode(tmp_path: Path) -> None:
    """The protections run before either predicate, so a mid-turn item cannot
    reach an approval prompt even if something upstream mistakenly cleared it."""
    harness = _harness(
        tmp_path,
        state="blocked",
        reasons=["root_agent_working", "approval_required"],
        interject_state="safe",
    )
    try:
        message = await _now_item(harness)
        with pytest.raises(QueueError) as caught:
            await harness.service.send_next(
                message["id"], revision=message["revision"], initiator="auto"
            )
        assert caught.value.code == "delivery_protected"
        assert caught.value.payload["protected"] is True
        assert not harness.writes
    finally:
        harness.store.close()


@pytest.mark.asyncio
async def test_the_mode_never_becomes_a_confirm(tmp_path: Path) -> None:
    """A controller still cannot override, with or without the mode."""
    harness = _harness(
        tmp_path,
        state="blocked",
        reasons=["root_agent_working"],
        interject_state="blocked",
    )
    try:
        message = await _now_item(harness)
        with pytest.raises(QueueError) as caught:
            await harness.service.send_next(
                message["id"],
                revision=message["revision"],
                initiator="auto",
                confirm=True,
            )
        assert caught.value.code == "confirm_requires_user"
    finally:
        harness.store.close()


@pytest.mark.asyncio
async def test_a_human_can_still_confirm_a_now_item_the_predicate_refused(
    tmp_path: Path,
) -> None:
    """The mode adds a path; it does not remove the one that already existed."""
    harness = _harness(
        tmp_path,
        state="blocked",
        reasons=["root_agent_working"],
        interject_state="blocked",
    )
    try:
        message = await _now_item(harness)
        result = await harness.service.send_next(
            message["id"], revision=message["revision"], confirm=True
        )
        assert result["status"] == "sent"
        assert result["confirmed"] is True
        assert result["interjected"] is False
    finally:
        harness.store.close()
