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
from swe_mux.plugin_store import PluginStore
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


def _plugin_row(plugin_id: str, root: Path) -> dict[str, Any]:
    return {
        "id": plugin_id,
        "name": plugin_id,
        "version": "1.0.0",
        "enabled": False,
        "lifecycle": "inspected",
        "source_kind": "link",
        "root": str(root),
        "manifest_path": str(root / "swe-mux-plugin.toml"),
        "manifest_digest": "a",
        "security_digest": "b",
    }


@pytest.mark.asyncio
async def test_the_registry_is_read_from_sqlite_once_until_it_is_written(
    tmp_path: Path,
) -> None:
    """The dispatcher reads the registry for every event (12.6k reads in one
    two-minute harness run, `DAEMON_LOAD_ISOLATION.md` item 6) and it changes only
    when an operator installs, enables or removes a plugin."""
    store = PluginStore(tmp_path / "mux.db")
    host = manager(tmp_path, [])
    host.store = store
    try:
        await store.put(_plugin_row("tests.a", tmp_path))
        selects: list[str] = []

        def trace(sql: str) -> None:
            if sql.startswith("SELECT * FROM plugins ORDER"):
                selects.append(sql)

        store._db.set_trace_callback(trace)
        for seq in range(50):
            await host._dispatch_event(FakeEvent(seq))
        assert len(selects) == 1

        # A caller mutating what it was handed must not reach the next reader.
        (first,) = await store.list()
        first["enabled"] = True
        assert (await store.list())[0]["enabled"] is False
        assert len(selects) == 1

        for write in (
            lambda: store.set_state("tests.a", enabled=True, lifecycle="enabled"),
            lambda: store.put(_plugin_row("tests.b", tmp_path)),
            lambda: store.remove("tests.a"),
        ):
            before = await store.list()
            await write()
            after = await store.list()
            assert after != before, "every write is visible to the very next read"
        assert [record["id"] for record in await store.list()] == ["tests.b"]
        assert len(selects) == 4
    finally:
        await store.close()
