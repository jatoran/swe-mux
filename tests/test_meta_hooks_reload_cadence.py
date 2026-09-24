"""The hook rules file is re-read on a cadence, not before every event.

Before 2026-09-24 the engine stat'ed and read `hooks.toml` on the event loop ahead of
handling each event, so a busy fleet paid a filesystem round trip per event - and a
slow disk turned that into a loop stall.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from swe_mux.event_bus import EventBus
from swe_mux.meta_hooks import MetaHookEngine
from tests.support.settle import until


async def test_a_burst_of_events_reads_the_rules_file_once(tmp_path: Path) -> None:
    events = EventBus()
    engine = MetaHookEngine(
        tmp_path / "hooks.toml", events, cast(Any, SimpleNamespace(sessions={}))
    )
    reloads = 0
    handled = 0

    async def counting_reload() -> None:
        nonlocal reloads
        reloads += 1

    async def counting_handle(_event: Any) -> None:
        nonlocal handled
        handled += 1

    engine._reload = counting_reload  # type: ignore[method-assign]
    engine._handle = counting_handle  # type: ignore[method-assign]
    engine.start()
    try:
        # The first reload happens once the engine has subscribed, so events emitted
        # after it are guaranteed to reach it.
        await until(lambda: reloads == 1)
        for index in range(50):
            await events.emit("command_failed", session_id=f"s{index}", source="test")
        await until(lambda: handled == 50)
        # One reload per cadence interval, not one per event: a loaded worker that
        # takes longer than the interval to deliver fifty events may see a second.
        assert reloads <= 2
    finally:
        await engine.stop()


async def test_a_missing_rules_file_reads_as_missing_off_the_loop(tmp_path: Path) -> None:
    engine = MetaHookEngine(
        tmp_path / "absent.toml", EventBus(), cast(Any, SimpleNamespace(sessions={}))
    )

    await engine._reload()

    assert engine.diagnostic == {"status": "missing", "error": None, "rules": 0}
