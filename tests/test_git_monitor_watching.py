"""The Git monitor reads a checkout when it changes, not on a timer.

These drive `GitMonitor._poll` with an explicit clock and a stand-in for the OS
watches, so every rate limit and sweep is asserted as a property of time rather
than measured against a wall clock. The watches themselves are covered against a
real repository in `test_git_watch.py`.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, cast

import pytest

from swe_mux import git_monitor
from swe_mux.event_bus import EventBus
from swe_mux.git_monitor import GitEvidence, GitMonitor, GitReading
from swe_mux.git_watch import CheckoutLayout, normalize_key
from swe_mux.models import GitState

ROOT = "C:/repo"
LAYOUT = CheckoutLayout(ROOT, f"{ROOT}/.git", f"{ROOT}/.git")


class _Session:
    def __init__(self, cwd: str, attached: bool, state: str = "idle") -> None:
        self.subscribers = [object()] if attached else []
        self.record = SimpleNamespace(
            id=f"{cwd}:{attached}",
            git=GitState(),
            git_cwd=cwd,
            state=state,
            project_id="project-1",
        )
        self.revision = 0

    def publish_update(self) -> None:
        self.revision += 1


class _Watches:
    """Stands in for `CheckoutWatches`: records what it is asked to follow."""

    def __init__(self, *, live: bool = True) -> None:
        self.live = live
        self.synced: list[dict[str, CheckoutLayout]] = []
        self.fresh: set[str] = set()

    def sync(self, layouts: dict[str, CheckoutLayout]) -> set[str]:
        self.synced.append(dict(layouts))
        fresh, self.fresh = self.fresh, set()
        return fresh

    def watched(self, root_key: str) -> bool:
        return self.live and bool(self.synced) and root_key in self.synced[-1]

    def status(self) -> dict[str, object]:
        return {"live": self.live}

    async def stop(self) -> None:
        return None


def _monitor(
    monkeypatch: pytest.MonkeyPatch,
    sessions: list[_Session],
    *,
    layout: CheckoutLayout | None = LAYOUT,
    watches: _Watches | None = None,
) -> tuple[GitMonitor, list[list[Any]], _Watches]:
    reads: list[list[Any]] = []
    branch = {"value": "main"}

    async def fake_read(targets):  # type: ignore[no-untyped-def]
        listed = list(targets)
        reads.append(listed)
        state = GitState(branch=branch["value"], root=ROOT)
        return {target: GitReading(state, GitEvidence(), layout) for target in listed}

    monkeypatch.setattr(git_monitor, "read_unique_git_readings", fake_read)
    manager = cast(Any, SimpleNamespace(sessions={str(i): s for i, s in enumerate(sessions)}))
    monitor = GitMonitor(manager, EventBus(), cadence=5.0, watch=False)
    fake = watches or _Watches()
    monitor._watches = cast(Any, fake)
    monitor._test_branch = branch  # type: ignore[attr-defined]
    return monitor, reads, fake


def _changed(monitor: GitMonitor) -> None:
    monitor._on_checkout_changed(normalize_key(ROOT), False)


@pytest.mark.asyncio
async def test_an_unchanged_watched_checkout_is_not_reread_until_its_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The defect this replaced: ~7 git processes per checkout every five seconds."""
    monitor, reads, _ = _monitor(monkeypatch, [_Session(ROOT, attached=True)])
    await monitor._poll(now=0.0)
    assert len(reads) == 1, "the first tick reads everything"
    for second in range(1, int(GitMonitor.ATTACHED_SWEEP_SECONDS)):
        await monitor._poll(now=float(second))
    assert len(reads) == 1, "nothing changed, so nothing was read"
    await monitor._poll(now=GitMonitor.ATTACHED_SWEEP_SECONDS)
    assert len(reads) == 2, "the safety sweep still comes around"
    assert monitor.status()["reads"] == {"initial": 1, "sweep": 1}


@pytest.mark.asyncio
async def test_a_change_is_read_promptly_but_at_most_once_per_cadence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monitor, reads, _ = _monitor(monkeypatch, [_Session(ROOT, attached=True)])
    await monitor._poll(now=0.0)
    _changed(monitor)
    await monitor._poll(now=1.0)
    assert len(reads) == 1, "a change inside the cadence waits for it"
    await monitor._poll(now=5.0)
    assert len(reads) == 2, "and is read as soon as the cadence allows"
    # A storm of changes still costs one read per cadence, never one per change.
    for second in range(6, 10):
        _changed(monitor)
        await monitor._poll(now=float(second))
    assert len(reads) == 2
    await monitor._poll(now=10.0)
    assert len(reads) == 3


@pytest.mark.asyncio
async def test_a_detached_checkout_changes_slowly_and_sweeps_rarely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monitor, reads, _ = _monitor(monkeypatch, [_Session(ROOT, attached=False)])
    await monitor._poll(now=0.0)
    _changed(monitor)
    await monitor._poll(now=GitMonitor.DETACHED_CHANGE_SECONDS - 1)
    assert len(reads) == 1
    await monitor._poll(now=GitMonitor.DETACHED_CHANGE_SECONDS)
    assert len(reads) == 2
    start = GitMonitor.DETACHED_CHANGE_SECONDS
    await monitor._poll(now=start + GitMonitor.DETACHED_SWEEP_SECONDS - 1)
    assert len(reads) == 2
    await monitor._poll(now=start + GitMonitor.DETACHED_SWEEP_SECONDS)
    assert len(reads) == 3


@pytest.mark.asyncio
async def test_a_change_during_a_read_causes_the_next_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dirty flag is cleared before the read, so a change mid-read survives it."""
    session = _Session(ROOT, attached=True)
    monitor, reads, _ = _monitor(monkeypatch, [session])
    await monitor._poll(now=0.0)
    original = git_monitor.read_unique_git_readings

    async def read_while_changing(targets):  # type: ignore[no-untyped-def]
        _changed(monitor)
        return await original(targets)

    monkeypatch.setattr(git_monitor, "read_unique_git_readings", read_while_changing)
    _changed(monitor)
    await monitor._poll(now=5.0)
    assert len(reads) == 2
    monkeypatch.setattr(git_monitor, "read_unique_git_readings", original)
    await monitor._poll(now=10.0)
    assert len(reads) == 3, "the change that landed mid-read was not swallowed by it"


@pytest.mark.asyncio
async def test_a_checkout_without_a_live_watch_keeps_timed_polling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Losing a watch costs cost, never correctness: the old cadence returns."""
    monitor, reads, _ = _monitor(
        monkeypatch, [_Session(ROOT, attached=True)], watches=_Watches(live=False)
    )
    await monitor._poll(now=0.0)
    await monitor._poll(now=5.0)
    await monitor._poll(now=10.0)
    assert len(reads) == 3
    assert monitor.status()["reads"] == {"initial": 1, "poll": 2}


@pytest.mark.asyncio
async def test_a_watch_that_starts_late_buys_one_catch_up_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A change in the gap before a watch started would otherwise wait for the sweep.

    A watch starting in the same tick as the read that resolved its checkout has no
    gap worth reading; one that starts later (after a failure's cooldown) does.
    """
    monitor, reads, watches = _monitor(monkeypatch, [_Session(ROOT, attached=True)])
    watches.fresh = {normalize_key(ROOT)}
    await monitor._poll(now=0.0)
    await monitor._poll(now=5.0)
    assert len(reads) == 1, "same-tick start: no extra read"
    watches.fresh = {normalize_key(ROOT)}
    await monitor._poll(now=6.0)
    await monitor._poll(now=11.0)
    assert len(reads) == 2, "a later start reads once to cover its gap"


@pytest.mark.asyncio
async def test_the_watches_follow_exactly_the_live_checkouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = _Session(ROOT, attached=True)
    monitor, _reads, watches = _monitor(monkeypatch, [live])
    await monitor._poll(now=0.0)
    assert watches.synced[-1] == {normalize_key(ROOT): LAYOUT}
    live.record.state = "exited"
    await monitor._poll(now=1.0)
    assert watches.synced[-1] == {}, "an ended session's checkout is no longer watched"


@pytest.mark.asyncio
async def test_a_directory_git_does_not_know_is_asked_again_while_attached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monitor, reads, watches = _monitor(monkeypatch, [_Session(ROOT, attached=True)], layout=None)

    async def not_a_repository(targets):  # type: ignore[no-untyped-def]
        listed = list(targets)
        reads.append(listed)
        return {target: GitReading(GitState(), GitEvidence()) for target in listed}

    monkeypatch.setattr(git_monitor, "read_unique_git_readings", not_a_repository)
    await monitor._poll(now=0.0)
    await monitor._poll(now=GitMonitor.NON_REPOSITORY_SECONDS - 1)
    assert len(reads) == 1
    await monitor._poll(now=GitMonitor.NON_REPOSITORY_SECONDS)
    assert len(reads) == 2
    assert watches.synced[-1] == {}, "nothing to watch until Git says there is"


@pytest.mark.asyncio
async def test_git_changed_carries_a_revision_ordered_row_patch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The browser patches the row from the event instead of re-reading the fleet.

    The patch is the snapshot's own serialization of the field, and its revision is
    the one `publish_update` stamped *before* the event - so a REST snapshot at or
    past it already contains the value, and one before it cannot roll it back.
    """
    session = _Session(ROOT, attached=True)
    monitor, _reads, _ = _monitor(monkeypatch, [session])
    queue = monitor.events.subscribe(name="test")
    await monitor._poll(now=0.0)
    event = queue.get_nowait()
    assert event.type == "git_changed"
    assert event.payload["session_revision"] == session.revision == 1
    assert event.payload["patch"] == {"git": event.payload["git"]}
    assert event.payload["patch"]["git"]["branch"] == "main"


@pytest.mark.asyncio
async def test_the_layout_is_resolved_once_and_never_on_the_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`realpath` on the loop was a recurring stall frame (2026-10-12)."""
    git_monitor.reset_diffstat_cache()
    calls: list[str] = []
    real = git_monitor._resolve_layout

    def counting(*args: str) -> Any:
        calls.append("loop" if _on_loop() else "worker")
        return real(*args)

    monkeypatch.setattr(git_monitor, "_resolve_layout", counting)
    first = await git_monitor._checkout_layout(ROOT, ROOT, f"{ROOT}/.git", ".git")
    second = await git_monitor._checkout_layout(ROOT, ROOT, f"{ROOT}/.git", ".git")
    assert first == second
    assert calls == ["worker"]
    git_monitor.reset_diffstat_cache()


@pytest.mark.asyncio
async def test_the_monitor_reports_why_it_read_for_the_background_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monitor, _reads, _ = _monitor(monkeypatch, [_Session(ROOT, attached=True)])
    await monitor._poll(now=0.0)
    _changed(monitor)
    status = monitor.status()
    json.dumps(status)  # served verbatim on /api/diagnostics/background
    assert status["reads"] == {"initial": 1}
    assert status["changes_received"] == 1
    assert status["dirty_targets"] == 1
    assert status["watch"] == {"live": True}


def _on_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True
