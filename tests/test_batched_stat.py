"""Every observed transcript is stat-ed in one worker pass per tick, not one hop each.

At 36 sessions the tailers took about 150 stat hops a second through the default
executor, each a GIL round trip for a few microseconds of syscall
(`DAEMON_LOAD_ISOLATION.md` item 6).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from swe_mux import batched_stat as module
from swe_mux.batched_stat import StatTicker, batched_stat, ticker
from swe_mux.observation import JsonlTailer
from tests.support.settle import until


async def test_one_tick_answers_every_request_in_one_pass(tmp_path: Path) -> None:
    present = [tmp_path / f"t{index}.jsonl" for index in range(12)]
    for path in present:
        path.write_bytes(b"{}\n")
    missing = tmp_path / "absent.jsonl"
    before = ticker().snapshot()

    answers = await asyncio.gather(
        *(batched_stat(path) for path in present), batched_stat(missing), batched_stat(present[0])
    )

    *stats, absent, repeated = answers
    assert [stat.st_size for stat in stats if stat is not None] == [3] * 12
    assert absent is None, "a missing file is an answer, not an error"
    assert repeated is not None and repeated.st_size == 3
    after = ticker().snapshot()
    assert after["passes"] - before["passes"] == 1
    assert after["stats"] - before["stats"] == 13, "a path asked for twice is stat-ed once"


async def test_any_other_failure_raises_as_path_stat_would(tmp_path: Path) -> None:
    good = tmp_path / "good.jsonl"
    good.write_bytes(b"{}\n")
    bad = Path(str(tmp_path / "bad") + "\x00.jsonl")

    results = await asyncio.gather(batched_stat(good), batched_stat(bad), return_exceptions=True)

    assert not isinstance(results[0], BaseException) and results[0] is not None
    assert isinstance(results[1], ValueError)


async def test_a_cancelled_request_leaves_the_rest_of_its_pass_answered(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_bytes(b"{}\n")
    abandoned = asyncio.ensure_future(batched_stat(path))
    kept = asyncio.ensure_future(batched_stat(path))
    await asyncio.sleep(0)
    abandoned.cancel()
    stat = await kept
    assert stat is not None and stat.st_size == 3
    assert abandoned.cancelled()


async def test_a_closing_loops_shut_executor_fails_the_waiters_instead_of_hanging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = asyncio.get_running_loop()
    local = StatTicker(loop, tick=0.01)

    def shut(*_args: Any) -> Any:
        raise RuntimeError("cannot schedule new futures after shutdown")

    monkeypatch.setattr(loop, "run_in_executor", shut)
    with pytest.raises(RuntimeError, match="after shutdown"):
        await local.request(tmp_path / "t.jsonl")


async def test_a_fleet_of_tailers_shares_one_pass_per_tick(tmp_path: Path) -> None:
    """Twenty tailers, started at scattered moments, converge on the same passes."""
    paths = [tmp_path / f"s{index}.jsonl" for index in range(20)]
    for path in paths[::2]:  # half exist; the other half are being waited for
        path.write_bytes(b'{"n":1}\n')
    stop = asyncio.Event()
    caught_up: set[Path] = set()

    async def tail(path: Path, delay: float) -> None:
        await asyncio.sleep(delay)
        async for item, historical in JsonlTailer(path).events(stop):
            if item is None and not historical:
                caught_up.add(path)

    tasks = [
        asyncio.create_task(tail(path, (index % 7) * 0.031)) for index, path in enumerate(paths)
    ]
    try:
        await until(lambda: caught_up >= set(paths[::2]), what="every existing file replayed")
        before = ticker().snapshot()
        await asyncio.sleep(1.0)
        after = ticker().snapshot()
    finally:
        stop.set()
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)

    passes = after["passes"] - before["passes"]
    stats = after["stats"] - before["stats"]
    # Four ticks a second; a loaded machine runs fewer, never more. One hop per
    # tailer per tick would be about eighty.
    assert 1 <= passes <= 1.0 / module.TICK_SECONDS + 2
    assert stats <= passes * len(paths)
    assert stats > passes, "each pass carried many tailers, not one"
