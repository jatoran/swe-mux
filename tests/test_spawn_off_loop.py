"""A bounded subprocess is spawned somewhere other than the loop that asked.

`CreateProcess` is synchronous inside asyncio's transport, and a saturated disk
held it for 23.5 s on the daemon's loop on 2026-09-02 (`git status`, during a
`cargo test`). The run now happens on a loop of its own on another thread; what
the caller keeps is its callbacks on its own loop, its request context, and the
right to cancel.
"""

from __future__ import annotations

import asyncio
import contextvars
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from swe_mux import bounded_subprocess, ui_build
from swe_mux.bounded_subprocess import SPAWN_THREAD_NAME, run_bounded

pytestmark = pytest.mark.asyncio


def python_argv(source: str) -> tuple[str, ...]:
    return (sys.executable, "-c", source)


async def test_the_spawn_happens_on_the_spawn_thread_not_the_callers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads: list[str] = []
    real_exec = asyncio.create_subprocess_exec

    async def recording_exec(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
        threads.append(threading.current_thread().name)
        return await real_exec(*args, **kwargs)

    monkeypatch.setattr(bounded_subprocess.asyncio, "create_subprocess_exec", recording_exec)
    outcome = await run_bounded(
        python_argv("print('hi')"), label="where", timeout_seconds=30, output_limit=1024
    )
    assert outcome.exit_code == 0 and b"hi" in outcome.stdout
    assert threads == [SPAWN_THREAD_NAME], (
        "the transport's constructor is where CreateProcess blocks; it must not be ours"
    )
    assert threading.current_thread().name != SPAWN_THREAD_NAME


async def test_output_callbacks_run_on_the_callers_loop_in_order() -> None:
    seen: list[tuple[str, bytes]] = []
    caller_thread = threading.current_thread().name
    caller_loop = asyncio.get_running_loop()

    def note(chunk: bytes) -> None:
        # Touching loop-bound state here is the point: an `asyncio.Event` or a
        # queue in a caller's observer would break on the spawn thread.
        assert asyncio.get_running_loop() is caller_loop
        seen.append((threading.current_thread().name, chunk))

    outcome = await run_bounded(
        python_argv("import sys\nfor i in range(3):\n    print(i, flush=True)\n"),
        label="observer",
        timeout_seconds=30,
        output_limit=4096,
        on_chunk=note,
    )
    assert outcome.exit_code == 0
    # Callbacks are delivered through the caller's loop, so they may still be in
    # flight when the outcome lands; let the loop drain them.
    for _ in range(20):
        if b"".join(chunk for _, chunk in seen).count(b"\n") >= 3:
            break
        await asyncio.sleep(0.02)
    assert all(thread == caller_thread for thread, _ in seen)
    assert b"".join(chunk for _, chunk in seen).split() == [b"0", b"1", b"2"]


async def test_the_callers_context_is_carried_across(monkeypatch: pytest.MonkeyPatch) -> None:
    """Request correlation lives in a contextvar; a run must log under the request."""
    marker: contextvars.ContextVar[str] = contextvars.ContextVar("marker", default="unset")
    observed: list[str] = []
    real_exec = asyncio.create_subprocess_exec

    async def recording_exec(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
        observed.append(marker.get())
        return await real_exec(*args, **kwargs)

    monkeypatch.setattr(bounded_subprocess.asyncio, "create_subprocess_exec", recording_exec)
    marker.set("request-42")
    await run_bounded(python_argv("pass"), label="ctx", timeout_seconds=30, output_limit=64)
    assert observed == ["request-42"]


async def test_a_program_that_cannot_start_still_raises_to_the_caller() -> None:
    with pytest.raises(OSError):
        await run_bounded(
            ("swe-mux-no-such-program-anywhere",), label="x", timeout_seconds=5, output_limit=64
        )


async def test_stopping_the_spawn_loop_is_safe_and_it_restarts_on_demand() -> None:
    bounded_subprocess.stop_spawn_loop()
    bounded_subprocess.stop_spawn_loop()
    outcome = await run_bounded(
        python_argv("print('again')"), label="restart", timeout_seconds=30, output_limit=64
    )
    assert b"again" in outcome.stdout


def _write_index(path: Path, build_id: str) -> None:
    path.write_text(f'<html><head><meta name="ui-build" content="{build_id}"></head></html>')


async def test_the_build_id_is_read_once_per_freshness_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The health endpoint's stat held the loop for up to 15 s; it now costs one
    thread stat per window and nothing between."""
    index = tmp_path / "index.html"
    first, second = "a" * 64, "b" * 64
    _write_index(index, first)
    stats: list[float] = []
    real_read = ui_build.read_ui_build_id

    def counting(frontend_dir: Path) -> str | None:
        stats.append(time.monotonic())
        return real_read(frontend_dir)

    monkeypatch.setattr(ui_build, "read_ui_build_id", counting)
    ui_build.forget_ui_build_id(tmp_path)

    assert await ui_build.ui_build_id_cached(tmp_path) == first
    _write_index(index, second)
    assert await ui_build.ui_build_id_cached(tmp_path) == first, (
        "inside the window the last reading answers, without a stat"
    )
    assert len(stats) == 1
    ui_build.forget_ui_build_id(tmp_path)
    assert await ui_build.ui_build_id_cached(tmp_path) == second
    assert len(stats) == 2


async def test_a_missing_index_is_remembered_for_the_window_too(tmp_path: Path) -> None:
    ui_build.forget_ui_build_id(tmp_path)
    assert await ui_build.ui_build_id_cached(tmp_path) is None
    _write_index(tmp_path / "index.html", "c" * 64)
    assert await ui_build.ui_build_id_cached(tmp_path) is None, (
        "a tree with no frontend is not stat'ed on every poll either"
    )
    ui_build.forget_ui_build_id(tmp_path)
    assert await ui_build.ui_build_id_cached(tmp_path) == "c" * 64
