"""What the plugin event dispatcher costs per event, which is paid for every event.

Measured 2026-09-24 on a live daemon: ~30 events a second, no plugin enabled, and the
dispatcher holding about half of the event loop's GIL time. Its dedupe dict was pruned
by age only once it passed 2,000 entries, so past 2,000 events an hour it rebuilt
itself on every event - a per-event cost that grew with uptime.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from swe_mux.event_bus import EventBus
from swe_mux.plugins import EVENT_DEDUPE_WINDOW, PluginManager


class FakeEvent:
    def __init__(self, seq: int) -> None:
        self.seq = seq
        self.type = "turn_ended"
        self.ts = 1000.0 + seq
        self.payload: dict[str, Any] = {}
        self.snapshots = 0

    def snapshot(self) -> dict[str, Any]:
        self.snapshots += 1
        return {"type": self.type, "source": "tests", "payload": {}}


class FakeStore:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records
        self.lists = 0

    async def list(self) -> list[dict[str, Any]]:
        self.lists += 1
        return [dict(record) for record in self.records]


def manager(tmp_path: Path, records: list[dict[str, Any]]) -> PluginManager:
    host = PluginManager(
        data_dir=tmp_path / "data",
        database_path=tmp_path / "mux.db",
        events=EventBus(),
        sessions=SimpleNamespace(sessions={}),
        projects=SimpleNamespace(projects={}),
        port=8765,
    )
    host.store = FakeStore(records)  # type: ignore[assignment]
    return host


@pytest.mark.asyncio
async def test_no_enabled_plugin_means_no_per_event_work(tmp_path: Path) -> None:
    host = manager(tmp_path, [{"id": "a", "enabled": False}])
    events = [FakeEvent(seq) for seq in range(50)]

    for event in events:
        await host._dispatch_event(event)

    assert sum(event.snapshots for event in events) == 0
    assert host._event_seen == set()


@pytest.mark.asyncio
async def test_the_dedupe_window_is_bounded_by_count_and_refuses_a_redelivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = manager(tmp_path, [{"id": "a", "enabled": True}])

    async def no_hooks(_record: dict[str, Any], *, require_enabled: bool = False) -> Any:
        return SimpleNamespace(id="a", events=())

    monkeypatch.setattr(host, "_load", no_hooks)
    total = EVENT_DEDUPE_WINDOW + 500
    for seq in range(total):
        await host._dispatch_event(FakeEvent(seq))

    assert len(host._event_seen) == len(host._event_order) == EVENT_DEDUPE_WINDOW
    recent = FakeEvent(total - 1)
    await host._dispatch_event(recent)
    assert recent.snapshots == 0, "a redelivered event must not be dispatched again"
    # The oldest keys were forgotten, in order.
    oldest = total - EVENT_DEDUPE_WINDOW
    assert host._event_order[0] == f"{oldest}:turn_ended:{1000.0 + oldest}"
