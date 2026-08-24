from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import app_keys as keys
from swe_mux.clipboard_store import ClipboardStore, clipboard_preview, looks_like_secret
from swe_mux.config import (
    MOBILE_GESTURE_SLOTS,
    SCHEMA_VERSION,
    Config,
    default_mobile_gestures,
    load_config,
)
from swe_mux.keybindings import COMMAND_IDS, normalize_binding
from swe_mux.routes.clipboard import (
    capture_clipboard_entry,
    clear_clipboard_entries,
    delete_clipboard_entry,
    get_clipboard_entry,
    list_clipboard_entries,
    patch_clipboard_entry,
)
from swe_mux.server import error_middleware


def _rows(path: Path) -> list[sqlite3.Row]:
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        return db.execute("SELECT * FROM clipboard_entries").fetchall()
    finally:
        db.close()


async def test_capture_dedupes_by_content_and_keeps_newest_first(tmp_path: Path) -> None:
    clock = SimpleNamespace(now=1000.0)
    store = ClipboardStore(tmp_path / "mux.db", clock=lambda: clock.now)
    try:
        first, first_reason = await store.capture("alpha", source="terminal")
        clock.now += 5
        await store.capture("beta", source="note")
        clock.now += 5
        promoted, promoted_reason = await store.capture("alpha", source="terminal")

        assert first_reason == "stored"
        assert promoted_reason == "promoted"
        assert first is not None and promoted is not None and promoted.id == first.id
        # One entry per distinct text, and the re-copy is back at the front.
        assert [entry.text for entry in store.entries()] == ["alpha", "beta"]
        assert promoted.created_at == 1000.0
        assert promoted.updated_at == 1010.0
    finally:
        store.close()


async def test_secret_shaped_and_oversized_copies_are_never_stored(tmp_path: Path) -> None:
    store = ClipboardStore(tmp_path / "mux.db", entry_max_chars=64)
    try:
        entry, reason = await store.capture("export OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwx")
        assert (entry, reason) == (None, "secret")
        # The size ceiling is checked first because it is the cheap test; an
        # oversized copy is skipped either way.
        entry, reason = await store.capture("x" * 65)
        assert (entry, reason) == (None, "too_large")
        entry, reason = await store.capture("   ")
        assert (entry, reason) == (None, "empty")
        assert store.entries() == []

        # The heuristic is a setting, not a law: turning it off records the copy.
        store.redact_secrets = False
        entry, reason = await store.capture("ghp_abcdefghijklmnopqrstuvwxyz012345")
        assert reason == "stored" and entry is not None
    finally:
        store.close()


def test_secret_heuristic_spares_ordinary_copies() -> None:
    assert looks_like_secret("-----BEGIN OPENSSH PRIVATE KEY-----\nabc")
    assert looks_like_secret('  "api_key": "abcdefgh12345678"')
    assert looks_like_secret("Authorization: Bearer abcdefghijklmnop")
    assert looks_like_secret(
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1g"
    )
    # Things a developer copies constantly and must keep getting back.
    assert not looks_like_secret("13d2c3a8f1e4b7c0d9a2e5f8b1c4d7e0a3f6b9c2")  # git SHA
    assert not looks_like_secret("D:\\PROJECTS\\swe-mux\\src\\swe_mux\\clipboard_store.py")
    assert not looks_like_secret("uv run pytest tests -q -m 'not live_agent'")
    assert not looks_like_secret("The quick brown fox jumped over the lazy dog repeatedly.")


async def test_pinned_entries_survive_eviction_retention_and_clear(tmp_path: Path) -> None:
    clock = SimpleNamespace(now=1000.0)
    store = ClipboardStore(
        tmp_path / "mux.db", limit=2, retention_hours=1, clock=lambda: clock.now
    )
    try:
        keeper, _ = await store.capture("keep me")
        assert keeper is not None
        await store.set_pinned(keeper.id, True)
        for index in range(4):
            clock.now += 1
            await store.capture(f"entry-{index}")

        texts = [entry.text for entry in store.entries()]
        # Two unpinned entries at most, plus the pin — which is exempt from the cap.
        assert texts[0] == "keep me"
        assert texts[1:] == ["entry-3", "entry-2"]

        clock.now += 7200  # two hours: everything unpinned is now expired
        assert await store.prune() == 2
        assert [entry.text for entry in store.entries()] == ["keep me"]

        await store.capture("fresh")
        assert await store.clear() == 1
        assert [entry.text for entry in store.entries()] == ["keep me"]
        assert await store.clear(include_pinned=True) == 1
        assert store.entries() == []
    finally:
        store.close()


async def test_memory_only_default_writes_nothing_to_disk(tmp_path: Path) -> None:
    database = tmp_path / "mux.db"
    store = ClipboardStore(database)
    try:
        await store.capture("in memory only")
        assert store.entries() and _rows(database) == []
    finally:
        store.close()


async def test_persistence_round_trips_and_purges_when_switched_off(tmp_path: Path) -> None:
    database = tmp_path / "mux.db"
    store = ClipboardStore(database, persist=True)
    try:
        entry, _ = await store.capture("durable copy", source="terminal")
        assert entry is not None
        rows = _rows(database)
        assert [row["text"] for row in rows] == ["durable copy"]
    finally:
        store.close()

    reopened = ClipboardStore(database, persist=True)
    try:
        assert await reopened.load() == 1
        assert [item.text for item in reopened.entries()] == ["durable copy"]

        # Turning saving off must delete what was already written, not just stop writing.
        reopened.apply_config(
            Config(
                data_dir=database.parent,
                clipboard_history_persist=False,
                clipboard_history_enabled=True,
            )
        )
        await reopened._resync_persistence(clear_rows=True)
        assert _rows(database) == []
        assert [item.text for item in reopened.entries()] == ["durable copy"]

        # Disabling history drops the ring itself, and any rows still on disk.
        reopened.persist = True
        await reopened._flush_rows()
        assert _rows(database)
        reopened.apply_config(
            Config(
                data_dir=database.parent,
                clipboard_history_enabled=False,
                clipboard_history_persist=True,
            )
        )
        assert reopened.entries() == []
        await reopened._resync_persistence(clear_rows=True)
        assert _rows(database) == []
    finally:
        reopened.close()


async def test_load_deletes_rows_outside_the_adopted_window(tmp_path: Path) -> None:
    # Rows beyond the adopted window are unreachable by every later path: not in
    # the picker, not expired by retention, not removed by "clear history". They
    # would keep verbatim copied text on disk forever, against the count/time
    # bound this store promises. Lowering the limit is the ordinary way in.
    database = tmp_path / "mux.db"
    store = ClipboardStore(database, persist=True, limit=8)
    try:
        for index in range(6):
            await store.capture(f"copy {index}", source="terminal")
        assert len(_rows(database)) == 6
    finally:
        store.close()

    narrowed = ClipboardStore(database, persist=True, limit=1)
    try:
        assert await narrowed.load() == 1
        # Two rows are adopted (limit * 2) and pruned to one; the four the new
        # limit can never reach again are deleted rather than left behind.
        assert [row["text"] for row in _rows(database)] == [
            item.text for item in narrowed.entries()
        ]
    finally:
        narrowed.close()


async def test_clipboard_api_capture_read_pin_delete_and_clear(tmp_path: Path) -> None:
    events: list[tuple[str, dict[str, Any]]] = []

    class EventsStub:
        async def emit(self, event_type: str, **payload: Any) -> None:
            events.append((event_type, payload))

    store = ClipboardStore(tmp_path / "mux.db")
    app = web.Application(middlewares=[error_middleware])
    app[keys.CLIPBOARD] = store
    app[keys.EVENTS] = EventsStub()
    app.router.add_get("/api/clipboard", list_clipboard_entries)
    app.router.add_post("/api/clipboard", capture_clipboard_entry)
    app.router.add_delete("/api/clipboard", clear_clipboard_entries)
    app.router.add_get("/api/clipboard/{entry_id}", get_clipboard_entry)
    app.router.add_patch("/api/clipboard/{entry_id}", patch_clipboard_entry)
    app.router.add_delete("/api/clipboard/{entry_id}", delete_clipboard_entry)

    try:
        async with TestClient(TestServer(app)) as client:
            created = await client.post(
                "/api/clipboard",
                json={
                    "text": "line one\nline two",
                    "source": "terminal",
                    "session_id": "sess-1",
                    "device": "mobile",
                },
            )
            assert created.status == 201
            body = await created.json()
            entry_id = body["entry"]["id"]
            assert body["stored"] is True and body["reason"] == "stored"
            # The wire form never carries the copied text on capture or list.
            assert "text" not in body["entry"]

            listing = await (await client.get("/api/clipboard")).json()
            assert listing["count"] == 1 and listing["persist"] is False
            assert listing["entries"][0]["preview"] == "line one line two"
            assert listing["entries"][0]["line_count"] == 2
            assert "text" not in listing["entries"][0]

            full = await (await client.get(f"/api/clipboard/{entry_id}")).json()
            assert full["text"] == "line one\nline two"
            assert (await client.get("/api/clipboard/missing")).status == 404

            pinned = await (
                await client.patch(f"/api/clipboard/{entry_id}", json={"pinned": True})
            ).json()
            assert pinned["pinned"] is True
            assert (await client.patch(f"/api/clipboard/{entry_id}", json={})).status == 400

            cleared = await (await client.delete("/api/clipboard")).json()
            assert cleared["removed"] == 0  # pinned entries are not cleared
            assert (await client.delete(f"/api/clipboard/{entry_id}")).status == 200
            assert (await client.delete(f"/api/clipboard/{entry_id}")).status == 404

        # Every mutation announces itself, and no event payload carries the text.
        assert {name for name, _ in events} == {"clipboard_changed"}
        assert all("text" not in payload for _, payload in events)
    finally:
        store.close()


async def test_secret_shaped_capture_reports_why_it_was_skipped(tmp_path: Path) -> None:
    class EventsStub:
        async def emit(self, event_type: str, **payload: Any) -> None:
            raise AssertionError("a skipped capture must not emit an event")

    store = ClipboardStore(tmp_path / "mux.db")
    app = web.Application(middlewares=[error_middleware])
    app[keys.CLIPBOARD] = store
    app[keys.EVENTS] = EventsStub()
    app.router.add_post("/api/clipboard", capture_clipboard_entry)
    try:
        async with TestClient(TestServer(app)) as client:
            response = await client.post(
                "/api/clipboard", json={"text": "AKIAIOSFODNN7EXAMPLE"}
            )
            assert response.status == 200
            assert await response.json() == {"stored": False, "reason": "secret", "entry": None}
    finally:
        store.close()


def test_preview_collapses_whitespace_and_bounds_length() -> None:
    assert clipboard_preview("  one\n\ttwo   three ") == "one two three"
    assert clipboard_preview("x" * 400).endswith("…")
    assert len(clipboard_preview("x" * 400)) == 180


def test_drawer_commands_are_bindable_and_gesture_default_is_the_panel() -> None:
    assert {
        "drawer.toggle",
        "drawer.actions",
        "drawer.actions.clipboard",
        "drawer.actions.prompts",
        "drawer.notifications",
        "clipboard.open",
        "clipboard.clear",
    } <= COMMAND_IDS
    # The ids those replaced are retired, not merely renamed: a keybindings file is
    # durable, so each one must still resolve to whatever now answers the same request.
    for retired, survivor in {
        "drawer.clipboard": "drawer.actions.clipboard",
        "drawer.commands": "drawer.actions",
        "drawer.prompts": "drawer.actions.prompts",
        "drawer.context": "drawer.agent.instructions",
        "drawer.insight": "drawer.activity",
        "drawer.changemap": "drawer.activity.changes",
    }.items():
        assert retired not in COMMAND_IDS
        assert normalize_binding("ctrl+alt+9", retired) == ("ctrl+alt+9", survivor)
    # The right-edge drawer is pulled in by the leftward two-finger swipe; the
    # sidebar keeps the rightward one.
    assert default_mobile_gestures()["two_finger_swipe_left"] == "drawer.toggle"
    assert default_mobile_gestures()["two_finger_swipe_right"] == "sidebar.toggle"


def test_redundant_sidebar_gesture_migrates_to_the_clipboard_panel(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "schema_version = 16\n"
        "[mobile_gestures]\n"
        'two_finger_swipe_left = "sidebar.toggle"\n'
        'two_finger_swipe_right = "sidebar.toggle"\n'
        'two_finger_tap = "palette.open"\n',
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.schema_version == SCHEMA_VERSION
    assert config.mobile_gestures["two_finger_swipe_left"] == "drawer.toggle"
    assert config.mobile_gestures["two_finger_swipe_right"] == "sidebar.toggle"


def test_deliberate_gesture_mapping_survives_migration(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "schema_version = 16\n"
        "[mobile_gestures]\n"
        'two_finger_swipe_left = "terminal.find"\n',
        encoding="utf-8",
    )
    assert load_config(path).mobile_gestures["two_finger_swipe_left"] == "terminal.find"


def test_a_gesture_slot_added_later_arrives_carrying_its_default(tmp_path: Path) -> None:
    # Settings renders the stored map directly, so a slot missing from an older config
    # showed a live gesture as "Disabled" - and saving any other slot made that true.
    path = tmp_path / "config.toml"
    path.write_text(
        "[mobile_gestures]\n"
        'swipe_left = "terminal.find"\n'
        'two_finger_tap = ""\n',
        encoding="utf-8",
    )
    gestures = load_config(path).mobile_gestures

    assert set(gestures) == set(MOBILE_GESTURE_SLOTS)
    assert gestures["rail_swipe_up"] == "menu.toggle"
    # What the file did say is untouched, including a slot deliberately turned off.
    assert gestures["swipe_left"] == "terminal.find"
    assert gestures["two_finger_tap"] == ""
