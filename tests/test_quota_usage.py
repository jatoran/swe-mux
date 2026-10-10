"""Quota usage attribution: which part of an account's quota movement swe-mux spent.

The pure rules are tested directly; the store is driven end to end through its real
background loop with a fake ledger, because "computed once when the sample lands" is the
property that keeps the chart cheap and is only visible through that path.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import AsyncIterator, Iterable
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from swe_mux.history import HistoryIndex
from swe_mux.models import MuxEvent
from swe_mux.operational_telemetry import OperationalTelemetryStore
from swe_mux.quota_usage import (
    EXCESS_FLOOR_POINTS,
    Evidence,
    Usage,
    billed_spans,
    calibrated_rate,
    catch_up_chunks,
    split_rise,
    window_rise,
    window_shares,
)
from swe_mux.telemetry_queries import QUOTA_TURN_LOOKBACK
from tests.support.settle import until

# -- pure rules ------------------------------------------------------------------------


def test_a_reset_counts_as_a_climb_from_zero() -> None:
    assert window_rise(10.0, 14.0) == (4.0, False)
    assert window_rise(40.0, 3.0) == (3.0, True)
    assert window_rise(None, 3.0) == (None, False)
    # Claude resets early to zero and keeps the old reset time.
    assert window_rise(87.0, 0.0, resets_at=5000.0, at=100.0) == (0.0, True)
    # The reset time passed between the readings: a new window, even reading the same.
    assert window_rise(5.0, 5.0, resets_at=150.0, at=200.0) == (5.0, True)


def test_a_small_fall_inside_one_window_is_a_correction_not_a_reset() -> None:
    # Measured on Codex: 57%, then 56% with the same reset time, then back to 57%.
    # Read as a reset, the 56% would count as 56 points of fresh usage.
    assert window_rise(57.0, 56.0, resets_at=5000.0, at=100.0) == (0.0, False)
    assert window_rise(100.0, 98.0) == (0.0, False)
    assert window_rise(8.0, 4.0, resets_at=5000.0, at=100.0) == (4.0, True)


def test_window_shares_add_up_to_the_reading() -> None:
    parts = {"swe_mux": 4.0, "outside": 2.0, "elsewhere": 0.0, "uncertain": 0.0}
    # Attribution began after the window did: the rest is untracked.
    assert window_shares(9.0, parts) == {**parts, "untracked": 3.0}
    # A dip's climb back counted twice: scaled down to the reading.
    assert window_shares(3.0, parts) == {
        "swe_mux": 2.0,
        "outside": 1.0,
        "elsewhere": 0.0,
        "uncertain": 0.0,
        "untracked": 0.0,
    }
    assert window_shares(0.0, dict.fromkeys(parts, 0.0))["untracked"] == 0.0


def test_billing_follows_recorded_switches_and_refuses_an_unrecorded_one() -> None:
    def spans(start_active: bool, end_active: bool, changes: list[tuple[float, str]]) -> Any:
        return billed_spans(
            0.0,
            900.0,
            active_at_start=start_active,
            active_at_end=end_active,
            selections=changes,
            account_id="a",
        )

    assert spans(True, True, []) == ([(0.0, 900.0)], "selected")
    assert spans(False, False, []) == ([], "not_selected")
    assert spans(True, False, [(300.0, "b")]) == ([(0.0, 300.0)], "switched")
    assert spans(False, True, [(300.0, "a")]) == ([(300.0, 900.0)], "switched")
    # Away and back inside one poll is two spans, not "selected".
    assert spans(True, True, [(200.0, "b"), (600.0, "a")]) == (
        [(0.0, 200.0), (600.0, 900.0)],
        "switched",
    )
    # A reconcile to the account already selected changes nothing.
    assert spans(True, True, [(200.0, "a")]) == ([(0.0, 900.0)], "selected")
    # The samples say the selection changed and nothing recorded when: no span is trusted.
    assert spans(True, False, []) == ([], "uncertain")


@pytest.mark.parametrize(
    ("rise", "usage", "rate", "billing", "follows", "expected"),
    [
        (3.0, Usage(), None, "not_selected", True, {"elsewhere": 3.0}),
        (3.0, Usage(), None, "not_selected", False, {"outside": 3.0}),
        (3.0, Usage(), None, "selected", True, {"outside": 3.0}),
        (3.0, Usage(requests=4, units=1.0), None, "selected", True, {"swe_mux": 3.0}),
        # Presence without quantity takes the whole rise.
        (3.0, Usage(active_seconds=60.0), 2.0, "selected", True, {"swe_mux": 3.0}),
        # Predicted 2 of 10: the 8-point excess is not swe-mux's.
        (
            10.0,
            Usage(requests=4, units=1.0),
            2.0,
            "selected",
            True,
            {"swe_mux": 2.0, "outside": 8.0},
        ),
        # A point over the prediction is whole-percent rounding, not a second user.
        (3.0, Usage(requests=4, units=1.0), 2.0, "selected", True, {"swe_mux": 3.0}),
        (3.0, Usage(requests=4, units=1.0), None, "uncertain", True, {"uncertain": 3.0}),
        (0.0, Usage(requests=4, units=1.0), 2.0, "selected", True, {}),
    ],
)
def test_a_rise_is_split_between_the_four_sources(
    rise: float,
    usage: Usage,
    rate: float | None,
    billing: str,
    follows: bool,
    expected: dict[str, float],
) -> None:
    parts = split_rise(rise, usage=usage, rate=rate, billing=billing, follows_selection=follows)
    assert {key: value for key, value in parts.items() if value} == expected
    assert EXCESS_FLOOR_POINTS > 1, "one rounding step must never read as another user"


def test_a_rate_needs_enough_intervals_and_rise_to_outweigh_rounding() -> None:
    assert calibrated_rate("session", 30.0, 15.0, 10) == 2.0
    assert calibrated_rate("session", 30.0, 15.0, 3) is None
    assert calibrated_rate("session", 5.0, 15.0, 10) is None
    assert calibrated_rate("weekly", 9.0, 3.0, 10) == 3.0


def test_evidence_counts_requests_inside_spans_and_turns_as_presence() -> None:
    evidence = Evidence(
        requests=[
            (100.0, "run-a", "s", 0.5, 9.9),
            (200.0, "run-b", "s", None, 0.25),
            (700.0, "run-a", "s", 1.0, 9.9),
        ],
        turns=[("run-c", "s", 650.0, 800.0), ("run-d", "s", 0.0, None)],
    )
    usage = evidence.usage([(0.0, 300.0)])
    # Cost where reported, price-weighted tokens where not; turn d is still open.
    assert (usage.requests, usage.cost_usd, usage.units) == (2, 0.5, 0.75)
    assert usage.runs == 3
    assert usage.active_seconds == 300.0
    assert evidence.usage([(600.0, 900.0)], runs={"run-a"}).requests == 1
    assert evidence.usage([(600.0, 900.0)], runs={"run-c"}).active_seconds == 150.0
    # An open turn never counts past the lookback.
    assert evidence.usage([(QUOTA_TURN_LOOKBACK + 1, QUOTA_TURN_LOOKBACK + 60)]).present is False


def test_catch_up_chunks_bound_both_pair_count_and_evidence_span() -> None:
    pairs = [(index, index + 1, index * 900.0, (index + 1) * 900.0) for index in range(500)]
    by_count = catch_up_chunks(pairs, max_pairs=100, max_span=1e9)
    assert [len(chunk) for chunk in by_count] == [100] * 5
    by_span = catch_up_chunks(pairs, max_pairs=10_000, max_span=86_400.0)
    assert all(chunk[-1][3] - chunk[0][2] <= 86_400.0 for chunk in by_span)
    assert sum(len(chunk) for chunk in by_span) == 500


# -- the store, end to end -------------------------------------------------------------


class FakeLedger:
    """The ledger's two reads, over an in-memory list the test can extend."""

    def __init__(self, coverage: float | None = 0.0) -> None:
        self.requests: dict[str, list[tuple[Any, ...]]] = {}
        self.turns: dict[str, list[tuple[Any, ...]]] = {}
        self.coverage = coverage
        self.evidence_reads = 0

    def request(self, backend: str, at: float, run_id: str, cost: float | None = 1.0) -> None:
        self.requests.setdefault(backend, []).append((at, run_id, run_id, cost, 0.1))

    async def quota_usage_evidence(
        self, backend: str, from_ts: float, to_ts: float
    ) -> dict[str, list[tuple[Any, ...]]]:
        self.evidence_reads += 1
        requests = self.requests.get(backend, [])
        return {
            "requests": [row for row in requests if from_ts < row[0] <= to_ts],
            "turns": list(self.turns.get(backend, [])),
        }

    async def quota_usage_coverage(self, backend: str | None = None) -> float | None:
        return self.coverage


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "mux.db"


@pytest.fixture
async def store(db_path: Path) -> AsyncIterator[OperationalTelemetryStore]:
    # History first, so `events` and `history` exist exactly as they do in the daemon.
    HistoryIndex(db_path).close()
    opened = OperationalTelemetryStore(db_path)
    try:
        yield opened
    finally:
        await opened.stop_quota_usage()
        opened.close()


async def sample(
    store: OperationalTelemetryStore,
    *,
    provider: str = "claude",
    account: str,
    at: float,
    session: float | None,
    weekly: float | None = 0.0,
    weekly_resets: float | None = None,
    active: bool,
) -> int:
    result = await store.record_quota_sample(
        provider=provider,
        account_id=account,
        quota={
            "status": "ready",
            "session": None if session is None else {"used_percent": session},
            "weekly": (
                None if weekly is None else {"used_percent": weekly, "resets_at": weekly_resets}
            ),
        },
        sampled_at=at,
        account_active=active,
        auth_state="saved",
    )
    return int(result["sample_id"])


def stored(path: Path, *sample_ids: int) -> dict[int, dict[str, Any]]:
    with closing(sqlite3.connect(path, timeout=5)) as db:
        db.row_factory = sqlite3.Row
        placeholders = ",".join("?" for _ in sample_ids)
        rows = db.execute(
            f"SELECT * FROM quota_usage_intervals WHERE sample_id IN ({placeholders})",  # noqa: S608
            sample_ids,
        ).fetchall()
    return {int(row["sample_id"]): dict(row) for row in rows}


async def settled(
    path: Path, sample_ids: Iterable[int], **expected: Any
) -> dict[int, dict[str, Any]]:
    """Wait until every sample has an interval row matching `expected`."""
    ids = tuple(sample_ids)

    def ready() -> bool:
        rows = stored(path, *ids)
        return len(rows) == len(ids) and all(
            all(row.get(key) == value for key, value in expected.items()) for row in rows.values()
        )

    await until(ready, what=f"interval rows for {ids} matching {expected}")
    return stored(path, *ids)


def parts(row: dict[str, Any], window: str = "session") -> dict[str, float]:
    sources = ("swe_mux", "outside", "elsewhere", "uncertain")
    return {source: row[f"{window}_{source}"] for source in sources if row[f"{window}_{source}"]}


async def test_claude_quota_rising_on_the_unselected_account_was_used_elsewhere(
    store: OperationalTelemetryStore, db_path: Path
) -> None:
    ledger = FakeLedger()
    store.start_quota_usage(ledger)
    base = 1_800_000_000.0
    for account, active in (("work", True), ("spare", False)):
        await sample(store, account=account, at=base, session=10.0, active=active)
    ledger.request("claude", base + 300, "run-1", cost=2.0)
    work = await sample(store, account="work", at=base + 900, session=13.0, active=True)
    spare = await sample(store, account="spare", at=base + 901, session=16.0, active=False)

    rows = await settled(db_path, [work, spare])
    assert rows[work]["billing"] == "selected"
    assert (rows[work]["mux_requests"], rows[work]["mux_cost_usd"]) == (1, 2.0)
    assert parts(rows[work]) == {"swe_mux": 3.0}
    # Claude follows the selection, so nothing on this computer could have billed "spare".
    assert rows[spare]["billing"] == "not_selected"
    assert rows[spare]["mux_requests"] == 0
    assert parts(rows[spare]) == {"elsewhere": 6.0}

    series = await store.quota_series(
        provider="claude", since=base - 1, until=base + 1000, resolution="raw"
    )
    usage = {item["account_id"]: item["usage"] for item in series["series"]}
    assert usage["work"][0]["session"]["swe_mux"] == 3.0
    assert usage["spare"][0]["session"]["elsewhere"] == 6.0
    snapshot = await store.snapshot(provider="claude")
    listed = {item["account_id"] for item in snapshot["quota"]["usage_intervals"]}
    assert listed == {"work", "spare"}


async def test_a_recorded_switch_bills_each_side_of_it_to_its_own_account(
    store: OperationalTelemetryStore, db_path: Path
) -> None:
    ledger = FakeLedger()
    store.start_quota_usage(ledger)
    base = 1_800_000_000.0
    switched_at = base + 400
    history = HistoryIndex(db_path)
    await history.append_event(
        MuxEvent(
            switched_at,
            None,
            "test",
            "provider_account_selected",
            {"provider": "claude", "account_id": "b"},
        )
    )
    history.close()
    await sample(store, account="a", at=base, session=0.0, active=True)
    await sample(store, account="b", at=base + 1, session=0.0, active=False)
    ledger.request("claude", base + 100, "run-1", cost=1.0)
    ledger.request("claude", base + 600, "run-1", cost=3.0)
    a = await sample(store, account="a", at=base + 900, session=2.0, active=False)
    b = await sample(store, account="b", at=base + 901, session=5.0, active=True)

    rows = await settled(db_path, [a, b], billing="switched")
    assert rows[a]["mux_cost_usd"] == 1.0
    assert rows[b]["mux_cost_usd"] == 3.0
    assert parts(rows[a]) == {"swe_mux": 2.0}
    assert parts(rows[b]) == {"swe_mux": 5.0}


async def test_codex_bills_the_account_a_session_was_spawned_under(
    store: OperationalTelemetryStore, db_path: Path
) -> None:
    # A Codex session keeps its startup login, so "selected" says nothing about it.
    store.sessions = SimpleNamespace(
        sessions={
            "session-1": SimpleNamespace(
                record=SimpleNamespace(
                    id="session-1",
                    agent_run_id="run-1",
                    spawn_provider="codex",
                    spawn_provider_account_id="old",
                )
            )
        }
    )
    ledger = FakeLedger()
    store.start_quota_usage(ledger)
    base = 1_800_000_000.0
    await sample(store, provider="codex", account="old", at=base, session=0.0, active=False)
    await sample(store, provider="codex", account="new", at=base + 1, session=0.0, active=True)
    ledger.request("codex", base + 300, "run-1", cost=None)
    old = await sample(
        store, provider="codex", account="old", at=base + 900, session=4.0, active=False
    )
    new = await sample(
        store, provider="codex", account="new", at=base + 901, session=2.0, active=True
    )

    rows = await settled(db_path, [old, new], billing="per_session")
    assert rows[old]["mux_requests"] == 1
    assert rows[old]["mux_units"] == pytest.approx(0.1)
    assert parts(rows[old]) == {"swe_mux": 4.0}
    # Not "elsewhere": an unselected Codex account can still be billed from this computer.
    assert parts(rows[new]) == {"outside": 2.0}


async def test_a_calibrated_rate_splits_off_rise_the_requests_cannot_explain(
    store: OperationalTelemetryStore, db_path: Path
) -> None:
    ledger = FakeLedger()
    store.start_quota_usage(ledger)
    base = 1_800_000_000.0
    used = 0.0
    last = 0
    await sample(store, account="a", at=base, session=used, active=True)
    for step in range(1, 11):
        at = base + step * 900
        ledger.request("claude", at - 100, f"run-{step}", cost=1.0)
        used += 2.0
        last = await sample(store, account="a", at=at, session=used, active=True)
    await settled(db_path, [last])
    # Ten clean intervals at 2 points per dollar; then a dollar of work and 10 points.
    at = base + 11 * 900
    ledger.request("claude", at - 100, "run-11", cost=1.0)
    spike = await sample(store, account="a", at=at, session=used + 10.0, active=True)

    rows = await settled(db_path, [spike])
    assert parts(rows[spike]) == {"swe_mux": pytest.approx(2.0), "outside": pytest.approx(8.0)}


async def test_late_evidence_is_counted_when_the_next_sample_lands(
    store: OperationalTelemetryStore, db_path: Path
) -> None:
    ledger = FakeLedger()
    store.start_quota_usage(ledger)
    base = 1_800_000_000.0
    await sample(store, account="a", at=base, session=0.0, active=True)
    first = await sample(store, account="a", at=base + 900, session=3.0, active=True)
    assert parts((await settled(db_path, [first]))[first]) == {"outside": 3.0}
    # The request for that interval reached the ledger after its sample did.
    ledger.request("claude", base + 450, "run-1")
    await sample(store, account="a", at=base + 1800, session=3.0, active=True)

    await settled(db_path, [first], session_swe_mux=3.0)


async def test_catch_up_fills_covered_history_and_leaves_the_uncovered_alone(
    db_path: Path,
) -> None:
    HistoryIndex(db_path).close()
    store = OperationalTelemetryStore(db_path)
    base = 1_800_000_000.0
    ledger = FakeLedger(coverage=base + 1000)
    ids = [
        await sample(store, account="a", at=base + step * 900, session=step * 1.0, active=True)
        for step in range(6)
    ]
    assert stored(db_path, *ids) == {}, "nothing is attributed before the loop starts"
    try:
        store.start_quota_usage(ledger)
        covered = ids[2:]
        rows = await settled(db_path, covered)
        assert all(parts(row) == {"outside": 1.0} for row in rows.values())
        # Intervals ending before the ledger's evidence begins are unknown, not "outside".
        assert stored(db_path, ids[1]) == {}
        reads = ledger.evidence_reads
        # A second pass finds nothing to do and reads no evidence.
        assert await store.catch_up_quota_usage() == 0
        assert ledger.evidence_reads == reads
    finally:
        await store.stop_quota_usage()
        store.close()


async def test_daily_series_sums_each_utc_day_by_source(
    store: OperationalTelemetryStore, db_path: Path
) -> None:
    ledger = FakeLedger()
    store.start_quota_usage(ledger)
    day = 1_800_000_000.0 - 1_800_000_000.0 % 86400 + 3600
    await sample(store, account="a", at=day, session=0.0, weekly=0.0, active=True)
    ledger.request("claude", day + 100, "run-1")
    first = await sample(store, account="a", at=day + 900, session=2.0, weekly=1.0, active=True)
    second = await sample(store, account="a", at=day + 1800, session=5.0, weekly=1.0, active=True)
    await settled(db_path, [first, second])

    series = await store.quota_series(account_id="a", since=day - 1, until=day + 2000)
    (usage,) = series["series"][0]["usage"]
    assert usage["day"] == time.strftime("%Y-%m-%d", time.gmtime(day))
    assert usage["session"]["rise"] == 5.0
    assert (usage["session"]["swe_mux"], usage["session"]["outside"]) == (2.0, 3.0)
    assert usage["weekly"]["swe_mux"] == 1.0


async def test_an_account_reading_says_where_its_current_window_usage_came_from(
    store: OperationalTelemetryStore, db_path: Path
) -> None:
    base = 1_800_000_000.0
    # Evidence begins after the second reading, so what the window used before is untracked.
    ledger = FakeLedger(coverage=base + 1000)
    store.start_quota_usage(ledger)
    resets = base + 7 * 86400

    async def weekly(step: int, used: float) -> int:
        return await sample(
            store,
            account="a",
            at=base + step * 900,
            session=None,
            weekly=used,
            weekly_resets=resets,
            active=True,
        )

    await weekly(0, 1.0)
    await weekly(1, 3.0)
    ledger.request("claude", base + 1500, "run-1")
    ids = [await weekly(2, 7.0), await weekly(3, 9.0), await weekly(4, 8.0)]
    ledger.request("claude", base + 4000, "run-2")
    ids.append(await weekly(5, 9.0))
    rows = await settled(db_path, ids)
    assert (rows[ids[2]]["weekly_rise"], rows[ids[2]]["weekly_reset"]) == (0.0, 0)

    sources = (await store.latest_quota_by_account())["a"]["weekly"]["sources"]
    assert sources == {
        "since": resets - 7 * 86400,
        "reset_seen": False,
        "swe_mux": 5.0,
        "outside": 2.0,
        "elsewhere": 0.0,
        "uncertain": 0.0,
        # The 1% before the first reading and the 2 points before evidence began, less
        # the dip's climb back, which was counted again.
        "untracked": 2.0,
    }

    # An early reset to zero starts the window over, whatever the reset time says.
    reset = await weekly(6, 0.0)
    ledger.request("claude", base + 6000, "run-3")
    after = await weekly(7, 3.0)
    await settled(db_path, [reset, after])
    sources = (await store.latest_quota_by_account())["a"]["weekly"]["sources"]
    assert sources["since"] == base + 6 * 900
    assert sources["reset_seen"] is True
    assert (sources["swe_mux"], sources["untracked"]) == (3.0, 0.0)


async def test_retention_and_account_purge_take_the_intervals_with_them(
    store: OperationalTelemetryStore, db_path: Path
) -> None:
    ledger = FakeLedger()
    store.start_quota_usage(ledger)
    old = time.time() - (store.retention_days + 2) * 86400
    await sample(store, account="a", at=old, session=0.0, active=True)
    aged = await sample(store, account="a", at=old + 900, session=1.0, active=True)
    now = time.time()
    await sample(store, account="b", at=now - 900, session=0.0, active=False)
    kept = await sample(store, account="b", at=now, session=1.0, active=False)
    await settled(db_path, [aged, kept])

    await store.prune()
    assert set(stored(db_path, aged, kept)) == {kept}
    removed = await store.purge_account("claude", "b")
    assert removed["quota_usage_intervals"] == 1
    assert stored(db_path, kept) == {}


def test_the_superseded_attribution_table_is_dropped(db_path: Path) -> None:
    with closing(sqlite3.connect(db_path)) as db:
        db.execute("CREATE TABLE quota_attributions (sample_id INTEGER)")
        db.commit()
    store = OperationalTelemetryStore(db_path)
    tables = {
        str(row[0])
        for row in store._db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    store.close()
    assert "quota_attributions" not in tables
    assert "quota_usage_intervals" in tables
