"""The queue reads the once-a-second loops make are answered from memory between writes.

The readiness watcher read the queue summary (a GROUP BY over every message) every
second, and auto-delivery read the policy table, the pause row and every enabled
session's whole visible queue every second, each a trip to the store's worker
(`DAEMON_LOAD_ISOLATION.md` item 6). None of them changes unless something writes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from swe_mux.prompt_queue import AUTO_POLICY_GLOBAL, PromptQueueStore


@pytest.fixture
def store(tmp_path: Path) -> Any:
    built = PromptQueueStore(tmp_path / "queue.db")
    yield built
    built.close()


async def _message(store: PromptQueueStore, target: str, body: str, *, armed: bool) -> str:
    created = await store.create_message(
        target_session_id=target,
        target_agent_run_id=f"run-{target}",
        target_backend="claude",
        target_label=f"claude-{target}",
        project_id="p1",
        body=body,
        armed=armed,
        sender_kind="user",
        sender_id=None,
    )
    return str(created["id"])


def _trips(store: PromptQueueStore) -> list[str]:
    """Every operation submitted to the store's worker, by the callable it ran."""
    trips: list[str] = []
    real = store._run

    async def counting(fn: Any) -> Any:
        trips.append(getattr(fn, "__qualname__", repr(fn)))
        return await real(fn)

    store._run = counting  # type: ignore[method-assign]
    return trips


async def _the_tick_reads(store: PromptQueueStore) -> tuple[Any, ...]:
    return (
        await store.summary(),
        await store.auto_policies(["s1", "s2"]),
        await store.auto_policy(AUTO_POLICY_GLOBAL),
        await store.pending_head("s1"),
        await store.pending_head("s2"),
        await store.counters(),
    )


async def test_the_per_tick_reads_cost_no_worker_trip_until_something_writes(
    store: PromptQueueStore,
) -> None:
    await _message(store, "s1", "first", armed=True)
    await store.set_auto_policy("s1", enabled=1, agent_run_id="run-s1")
    first = await _the_tick_reads(store)
    trips = _trips(store)

    for _ in range(20):
        assert await _the_tick_reads(store) == first
    assert trips == [], "nothing was written, so nothing needed reading again"

    await _message(store, "s2", "second", armed=False)
    after = await _the_tick_reads(store)
    assert after != first
    summary = {row["target_session_id"]: row for row in after[0]}
    assert summary["s2"]["pending"] == 1, "the very next read sees the write"
    assert after[4] is not None and after[4]["body"] == "second"


async def test_a_write_that_changes_nothing_keeps_the_reads(store: PromptQueueStore) -> None:
    message_id = await _message(store, "s1", "only", armed=True)
    await _the_tick_reads(store)
    trips = _trips(store)
    # A conditional transition that loses its race writes no row.
    assert await store.fail_interrupted(message_id, "not delivering") is None
    trips.clear()
    await _the_tick_reads(store)
    assert trips == []


async def test_every_read_is_a_private_copy(store: PromptQueueStore) -> None:
    await _message(store, "s1", "body", armed=True)
    await store.set_auto_policy("s1", enabled=1)
    summary, policies, _pause, head, _none, counters = await _the_tick_reads(store)
    summary[0]["pending"] = 99
    policies[0]["enabled"] = 0
    assert head is not None
    head["state"] = "poisoned"
    head["origin"] = {"poisoned": True}
    counters["poisoned"] = 1.0
    again = await _the_tick_reads(store)
    assert again[0][0]["pending"] == 1
    assert again[1][0]["enabled"] == 1
    assert again[3]["state"] == "armed" and again[3].get("origin") != {"poisoned": True}
    assert "poisoned" not in again[5]


async def test_the_head_is_the_first_pending_item_in_queue_order(
    store: PromptQueueStore,
) -> None:
    """What auto-delivery derived from the whole visible queue, read as one row."""

    async def derived() -> Any:
        view = await store.messages_for_target("s1")
        return next(
            (item for item in view["messages"] if item["state"] in ("draft", "armed", "blocked")),
            None,
        )

    assert await store.pending_head("s1") is None
    first = await _message(store, "s1", "one", armed=True)
    second = await _message(store, "s1", "two", armed=False)
    assert await store.pending_head("s1") == await derived()
    await store.cancel(first, kind="cancelled")
    head = await store.pending_head("s1")
    assert head is not None and head["id"] == second
    assert head == await derived()
    await store.move_message(second, after=None)
    assert await store.pending_head("s1") == await derived()
    await store.delete_message(second)
    assert await store.pending_head("s1") is None and await derived() is None


async def test_a_write_made_outside_the_store_is_noticed_by_its_next_operation(
    store: PromptQueueStore,
) -> None:
    await store.set_auto_policy("s1", enabled=1)
    assert (await store.auto_policies(["s1"]))[0]["enabled"] == 1

    def out_of_band() -> None:
        store._db.execute("UPDATE queue_auto_policy SET enabled=0 WHERE session_id='s1'")
        store._db.commit()

    store._executor.submit(out_of_band).result()
    await store.message("absent")  # any operation at all
    assert (await store.auto_policies(["s1"]))[0]["enabled"] == 0


async def test_the_cache_reports_its_rate_and_stays_bounded(
    store: PromptQueueStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from swe_mux import prompt_queue

    monkeypatch.setattr(prompt_queue, "READ_CACHE_LIMIT", 4)
    for index in range(10):
        await store.pending_head(f"target-{index}")
    snapshot = store.read_cache_snapshot()
    assert snapshot["entries"] <= 4
    await store.pending_head("target-9")
    assert store.read_cache_snapshot()["hits"] == snapshot["hits"] + 1
