"""Liveness is reported without waiting on the shared default executor.

On 2026-10-18 thirty-two worktree-graveyard purges held all 32 workers of the
daemon's default executor for minutes. The event loop kept turning, but the
heartbeat file and the health probe's build-id read were both submitted to that
pool, so both went silent and the tray's recovery killed a busy daemon as a hung
one. Each test here saturates a one-worker default executor and asserts the
signal is still produced while it stays saturated.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest

from swe_mux import lifecycle, server, ui_build
from swe_mux.executor_accounting import AccountedThreadPoolExecutor

from .support.settle import until

pytestmark = pytest.mark.asyncio

BUILD_ID = "a" * 64


async def _saturate_default_executor() -> threading.Event:
    """Make the running loop's default executor one worker, and occupy it."""
    loop = asyncio.get_running_loop()
    loop.set_default_executor(
        AccountedThreadPoolExecutor(max_workers=1, thread_name_prefix="saturated")
    )
    release = threading.Event()
    started = threading.Event()

    def hold() -> None:
        started.set()
        release.wait(60)

    loop.run_in_executor(None, hold)
    await until(started.is_set, seconds=10, what="the only default worker is held")
    return release


def _write_index(directory: Path, build_id: str) -> None:
    (directory / "index.html").write_text(
        f'<html><head><meta name="ui-build" content="{build_id}"></head></html>',
        encoding="utf-8",
    )


async def test_the_heartbeat_is_written_while_the_default_executor_is_saturated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "HEARTBEAT_INTERVAL_SECONDS", 0.05)
    release = await _saturate_default_executor()
    task = asyncio.create_task(server._lifecycle_heartbeat_loop(tmp_path))
    try:
        await until(
            lambda: lifecycle.read_heartbeat(tmp_path) is not None,
            seconds=10,
            what="a heartbeat written behind a saturated pool",
        )
        assert not release.is_set(), "the pool was saturated for the whole wait"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        release.set()


async def test_the_health_build_id_is_read_while_the_default_executor_is_saturated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A generous bound, so the question is only *where* the read runs: a read queued
    # behind the held worker would never finish and this would time out.
    monkeypatch.setattr(ui_build, "UI_BUILD_WAIT_SECONDS", 30.0)
    _write_index(tmp_path, BUILD_ID)
    ui_build.forget_ui_build_id(tmp_path)
    release = await _saturate_default_executor()
    try:
        assert await asyncio.wait_for(ui_build.ui_build_id_cached(tmp_path), 20) == BUILD_ID
    finally:
        release.set()


async def test_a_slow_refresh_answers_with_the_last_reading_and_is_shared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A disk that holds the stat costs the probe staleness, never lateness."""
    monkeypatch.setattr(ui_build, "UI_BUILD_WAIT_SECONDS", 30.0)
    _write_index(tmp_path, BUILD_ID)
    ui_build.forget_ui_build_id(tmp_path)
    assert await ui_build.ui_build_id_cached(tmp_path) == BUILD_ID

    gate = threading.Event()
    reads: list[float] = []
    real_read = ui_build.read_ui_build_id

    def held(frontend_dir: Path) -> str | None:
        reads.append(time.monotonic())
        gate.wait(30)
        return real_read(frontend_dir)

    monkeypatch.setattr(ui_build, "read_ui_build_id", held)
    monkeypatch.setattr(ui_build, "UI_BUILD_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(ui_build, "UI_BUILD_FRESH_SECONDS", 0.0)
    try:
        started = time.monotonic()
        assert await ui_build.ui_build_id_cached(tmp_path) == BUILD_ID
        assert await ui_build.ui_build_id_cached(tmp_path) == BUILD_ID
        assert time.monotonic() - started < 10, "both asks answered without the read"
        await until(lambda: bool(reads), seconds=10, what="the refresh started")
        assert len(reads) == 1, "the second ask joined the refresh already running"
    finally:
        gate.set()
        # Let the refresh land while this loop is open to receive it.
        pending = ui_build._refreshing.get(tmp_path / "index.html")
        if pending is not None:
            await until(pending.done, seconds=10, what="the held refresh finished")
            await asyncio.sleep(0)
