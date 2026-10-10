"""How much of each account's quota movement swe-mux's own sessions spent.

Every quota poll closes an interval for one account: two consecutive ready samples. This
module turns each interval into one durable row (`quota_usage_intervals`) that says how
far each window rose and splits that rise by source:

- `swe_mux` - swe-mux sessions billed to this account were working;
- `outside` - the account was in use here, but the rise is not explained by swe-mux
  sessions (another app on this computer, or use elsewhere at the same time);
- `elsewhere` - this computer was not using the account at all, so the rise came from
  another device, the provider's own apps, or another login;
- `uncertain` - the account was switched mid-interval in a way the daemon did not record.

It is a running total, never a scan:

- a row is computed when its closing sample lands, and once more at the next sample so
  evidence that reached the ledger late still counts; reading the chart reads rows;
- the evidence is the canonical ledger's mux-owned model requests and turns, read by two
  index-served queries bounded by the interval (`telemetry_queries.quota_usage_evidence`);
- a catch-up pass at startup fills intervals recorded before this existed or while the
  daemon was down, in bounded chunks, oldest first.

Which account a request billed is the provider's rule (`provider_accounts.
switch_reaches_live`). A Claude session follows the selected account on its next
request, so a request bills whichever account was selected when it was made - measured
2026-10-10 over three days and two accounts: per-interval request cost predicted the
selected account's 5-hour rise at r=0.955 with no lag, while the other account stayed
flat. A Codex session keeps the login it started with, so its requests bill the account
selected when the session was spawned.

How many points a dollar of requests moves a window depends on the plan, so it is
calibrated per account and window from that account's own clean intervals. Only with a
calibrated rate is a rise ever split between swe-mux and something else; without one, any
swe-mux activity takes the whole rise.
"""

from __future__ import annotations

import asyncio
import bisect
import json
import logging
import sqlite3
import time
from collections.abc import Callable, Container, Iterable, Sequence
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

from .background_tasks import background
from .provider_accounts import switch_reaches_live
from .telemetry_queries import QUOTA_TURN_LOOKBACK

log = logging.getLogger(__name__)

T = TypeVar("T")

QUOTA_USAGE_LOOP = "quota-usage-attribution"
#: Rows older than this are recomputed by the next catch-up. 2: a small fall inside one
#: window stopped counting as a reset (`window_rise`).
QUOTA_USAGE_VERSION = 2
QUOTA_WINDOWS = ("session", "weekly")
#: How long each window runs, which bounds how early the current one can have begun.
WINDOW_SECONDS = {"session": 5 * 3600.0, "weekly": 7 * 86400.0}
#: A fall this small inside one window is the provider correcting itself, not a reset.
#: Measured 2026-10-10 over two months of readings: every fall of one or two points kept
#: its reset time (Codex reads 57%, then 56%, then climbs back), while every real reset
#: either passed its reset time, fell to zero, or fell by four points or more.
DIP_MAX_POINTS = 3.0
USAGE_SOURCES = ("swe_mux", "outside", "elsewhere", "uncertain")
#: Selection changes the daemon records (`provider_accounts`): an explicit switch, and a
#: live login it found changed underneath it.
SELECTION_EVENTS = ("provider_account_selected", "provider_account_reconciled")
#: An interval longer than this is a polling gap, and its rise does not calibrate a rate.
MAX_CLEAN_GAP_SECONDS = 40 * 60
#: Clean intervals one rate is drawn from, newest first.
RATE_INTERVALS = 300
#: The least evidence a rate needs: enough intervals, and enough total rise that the
#: provider's rounding to whole percents is a small part of it.
RATE_MIN_INTERVALS = 8
RATE_MIN_RISE = {"session": 20.0, "weekly": 8.0}
#: A rise beyond what swe-mux's requests predict is called "not swe-mux" only when the
#: excess is at least this many points and this share of the rise: a whole-percent
#: reading can be a point off in either direction.
EXCESS_FLOOR_POINTS = 2.0
EXCESS_SHARE = 0.4
#: Catch-up bounds: pairs per chunk, the span one evidence read may cover, and the pause
#: between chunks that keeps the ledger's worker free for live ingestion.
CATCH_UP_PAIRS = 192
CATCH_UP_SPAN_SECONDS = 2 * 86400.0
CATCH_UP_PAUSE_SECONDS = 0.2
_SPAWN_CACHE_LIMIT = 5000

SCHEMA = """
CREATE TABLE IF NOT EXISTS quota_usage_intervals (
  sample_id INTEGER PRIMARY KEY,
  previous_sample_id INTEGER NOT NULL,
  provider TEXT NOT NULL,
  account_id TEXT NOT NULL,
  provider_account_uuid TEXT NOT NULL DEFAULT '',
  interval_start REAL NOT NULL,
  interval_end REAL NOT NULL,
  billing TEXT NOT NULL,
  mux_requests INTEGER NOT NULL,
  mux_runs INTEGER NOT NULL,
  mux_cost_usd REAL NOT NULL,
  mux_units REAL NOT NULL,
  mux_active_seconds REAL NOT NULL,
  session_rise REAL,
  session_reset INTEGER NOT NULL,
  session_swe_mux REAL NOT NULL,
  session_outside REAL NOT NULL,
  session_elsewhere REAL NOT NULL,
  session_uncertain REAL NOT NULL,
  session_clean INTEGER NOT NULL,
  weekly_rise REAL,
  weekly_reset INTEGER NOT NULL,
  weekly_swe_mux REAL NOT NULL,
  weekly_outside REAL NOT NULL,
  weekly_elsewhere REAL NOT NULL,
  weekly_uncertain REAL NOT NULL,
  weekly_clean INTEGER NOT NULL,
  version INTEGER NOT NULL,
  computed_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quota_usage_account
  ON quota_usage_intervals(provider,account_id,interval_end);
CREATE INDEX IF NOT EXISTS idx_quota_usage_recent
  ON quota_usage_intervals(interval_end);
"""

_COLUMNS = (
    "sample_id,previous_sample_id,provider,account_id,provider_account_uuid,interval_start,"
    "interval_end,billing,mux_requests,mux_runs,mux_cost_usd,mux_units,mux_active_seconds,"
    "session_rise,session_reset,session_swe_mux,session_outside,session_elsewhere,"
    "session_uncertain,session_clean,weekly_rise,weekly_reset,weekly_swe_mux,weekly_outside,"
    "weekly_elsewhere,weekly_uncertain,weekly_clean,version,computed_at"
)


class QuotaUsageSource(Protocol):
    """The canonical ledger's side (`CanonicalTelemetryService`)."""

    async def quota_usage_evidence(
        self, backend: str, from_ts: float, to_ts: float
    ) -> dict[str, list[tuple[Any, ...]]]: ...

    async def quota_usage_coverage(self, backend: str | None = None) -> float | None: ...


# -- pure rules ------------------------------------------------------------------------


def window_rise(
    before: float | None,
    after: float | None,
    *,
    resets_at: float | None = None,
    at: float | None = None,
) -> tuple[float | None, bool]:
    """How far a window climbed between two readings, and whether it reset in between.

    `resets_at` is when the window the earlier reading belonged to was due to reset, and
    `at` is when the later reading was taken. The window reset if that time passed, or
    if it fell to zero or by more than `DIP_MAX_POINTS`; a smaller fall is a correction
    inside one window and rose nothing. A reset counts as a climb from zero: a window
    that rolled over and reads 3% rose 3 points in that interval, not fell 40.
    """
    if before is None or after is None:
        return None, False
    rolled = resets_at is not None and at is not None and at >= resets_at
    if rolled or (after < before and (after <= 0 or before - after > DIP_MAX_POINTS)):
        return after, True
    return max(0.0, after - before), False


def billed_spans(
    start: float,
    end: float,
    *,
    active_at_start: bool,
    active_at_end: bool,
    selections: Iterable[tuple[float, str]],
    account_id: str,
) -> tuple[list[tuple[float, float]], str]:
    """When `account_id` was the selected account inside `(start, end]`.

    `selections` are the provider's recorded selection changes inside the interval,
    oldest first, as `(ts, selected account)`. The label is `selected`, `not_selected`,
    `switched`, or `uncertain` when the recorded changes do not arrive at the selection
    the closing sample saw - a change the daemon never recorded - in which case no span
    can be trusted.
    """
    spans: list[tuple[float, float]] = []
    active = active_at_start
    since = start
    for ts, selected in selections:
        now_active = selected == account_id
        if now_active == active:
            continue
        if active:
            spans.append((since, ts))
        since, active = ts, now_active
    if active:
        spans.append((since, end))
    if active != active_at_end:
        return [], "uncertain"
    if not spans:
        return [], "not_selected"
    if spans == [(start, end)]:
        return spans, "selected"
    return spans, "switched"


@dataclass(frozen=True)
class Usage:
    """What swe-mux sessions billed to one account did in one interval."""

    requests: int = 0
    runs: int = 0
    cost_usd: float = 0.0
    #: Cost where the provider reports it, else price-weighted tokens; calibrated per
    #: account, so the two are never compared with each other.
    units: float = 0.0
    active_seconds: float = 0.0

    @property
    def present(self) -> bool:
        return self.requests > 0 or self.active_seconds > 0


class Evidence:
    """One ledger read, answering usage questions for any span inside it."""

    def __init__(
        self, requests: Sequence[Sequence[Any]], turns: Sequence[Sequence[Any]]
    ) -> None:
        self.requests = sorted(requests, key=lambda row: float(row[0]))
        self.times = [float(row[0]) for row in self.requests]
        self.turns = list(turns)

    @property
    def run_ids(self) -> set[str]:
        return {str(row[1]) for row in self.requests} | {str(row[0]) for row in self.turns}

    def usage(
        self, spans: Iterable[tuple[float, float]], runs: Container[str] | None = None
    ) -> Usage:
        requests = 0
        cost = units = active = 0.0
        seen: set[str] = set()
        for start, end in spans:
            low = bisect.bisect_right(self.times, start)
            high = bisect.bisect_right(self.times, end)
            for _finished, run_id, _session, cost_usd, weight in self.requests[low:high]:
                if runs is not None and run_id not in runs:
                    continue
                requests += 1
                seen.add(str(run_id))
                if cost_usd is not None:
                    cost += float(cost_usd)
                    units += float(cost_usd)
                else:
                    units += float(weight or 0.0)
            for run_id, _session, turn_start, turn_end in self.turns:
                if runs is not None and run_id not in runs:
                    continue
                # A turn the ledger never saw end counts for at most the lookback.
                closes = (
                    float(turn_end)
                    if turn_end is not None
                    else float(turn_start) + QUOTA_TURN_LOOKBACK
                )
                overlap = min(end, closes) - max(start, float(turn_start))
                if overlap > 0:
                    active += overlap
                    seen.add(str(run_id))
        return Usage(requests, len(seen), cost, units, active)


def calibrated_rate(window: str, rise_sum: float, unit_sum: float, count: int) -> float | None:
    """Points of `window` per usage unit, or None while the evidence is too thin."""
    if count < RATE_MIN_INTERVALS or unit_sum <= 0 or rise_sum < RATE_MIN_RISE[window]:
        return None
    return rise_sum / unit_sum


def split_rise(
    rise: float | None,
    *,
    usage: Usage,
    rate: float | None,
    billing: str,
    follows_selection: bool,
) -> dict[str, float]:
    """Divide one window's rise between the four sources."""
    parts = dict.fromkeys(USAGE_SOURCES, 0.0)
    if rise is None or rise <= 0:
        return parts
    if billing == "uncertain":
        parts["uncertain"] = rise
        return parts
    if not usage.present:
        # Nothing swe-mux runs billed this account. When running sessions follow the
        # selection and the account was not selected at all, nothing on this computer
        # could have billed it either.
        elsewhere = follows_selection and billing == "not_selected"
        parts["elsewhere" if elsewhere else "outside"] = rise
        return parts
    if rate is None or usage.units <= 0:
        parts["swe_mux"] = rise
        return parts
    excess = rise - rate * usage.units
    if excess >= max(EXCESS_FLOOR_POINTS, EXCESS_SHARE * rise):
        parts["swe_mux"] = rise - excess
        parts["outside"] = excess
    else:
        parts["swe_mux"] = rise
    return parts


def catch_up_chunks(
    pairs: Sequence[tuple[int, int, float, float]],
    *,
    max_pairs: int = CATCH_UP_PAIRS,
    max_span: float = CATCH_UP_SPAN_SECONDS,
) -> list[list[tuple[int, int, float, float]]]:
    """Group `(previous id, id, previous at, at)` pairs so one evidence read stays bounded."""
    chunks: list[list[tuple[int, int, float, float]]] = []
    for pair in pairs:
        current = chunks[-1] if chunks else None
        fits = (
            current is not None
            and len(current) < max_pairs
            and pair[3] - current[0][2] <= max_span
        )
        if current is not None and fits:
            current.append(pair)
        else:
            chunks.append([pair])
    return chunks


def usage_interval_public(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    """The API shape of one stored interval."""
    return {
        "sample_id": int(row["sample_id"]),
        "provider": str(row["provider"]),
        "account_id": str(row["account_id"]),
        "provider_account_uuid": str(row["provider_account_uuid"] or "") or None,
        "start": float(row["interval_start"]),
        "end": float(row["interval_end"]),
        "billing": str(row["billing"]),
        "requests": int(row["mux_requests"]),
        "runs": int(row["mux_runs"]),
        "cost_usd": round(float(row["mux_cost_usd"]), 4),
        **{
            window: {
                "rise": row[f"{window}_rise"],
                "reset": bool(row[f"{window}_reset"]),
                **{
                    source: round(float(row[f"{window}_{source}"]), 3)
                    for source in USAGE_SOURCES
                },
            }
            for window in QUOTA_WINDOWS
        },
    }


def window_shares(used: float, parts: dict[str, float]) -> dict[str, float]:
    """Divide a reading's `used` between the sources whose rises built it.

    The parts are the rises attributed since the window began. They fall short of the
    reading when attribution started after the window did, which is `untracked`; they
    overshoot it by each dip's climb back (`window_rise`), and are scaled down to it.
    """
    total = sum(parts.values())
    if total > used:
        scale = used / total if total > 0 else 0.0
        return {**{source: parts[source] * scale for source in parts}, "untracked": 0.0}
    return {**parts, "untracked": used - total}


#: The columns `usage_interval_public` reads, as SQL aggregates over one UTC day.
DAILY_USAGE_COLUMNS = (
    "SUM(mux_requests) mux_requests,SUM(mux_cost_usd) mux_cost_usd,"
    + ",".join(
        f"SUM(CASE WHEN {window}_rise>0 THEN {window}_rise ELSE 0 END) {window}_rise,"
        + ",".join(f"SUM({window}_{source}) {window}_{source}" for source in USAGE_SOURCES)
        for window in QUOTA_WINDOWS
    )
)


def daily_usage_public(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "day": str(row["day"]),
        "requests": int(row["mux_requests"] or 0),
        "cost_usd": round(float(row["mux_cost_usd"] or 0.0), 4),
        **{
            window: {
                "rise": float(row[f"{window}_rise"] or 0.0),
                **{
                    source: round(float(row[f"{window}_{source}"] or 0.0), 3)
                    for source in USAGE_SOURCES
                },
            }
            for window in QUOTA_WINDOWS
        },
    }


# -- the store side --------------------------------------------------------------------


class QuotaUsageMixin:
    """Maintains `quota_usage_intervals`, mixed into `OperationalTelemetryStore`."""

    if TYPE_CHECKING:
        _db: sqlite3.Connection
        sessions: Any

        async def _run(self, fn: Callable[[], T]) -> T: ...

    def _init_quota_usage(self) -> None:
        self._quota_usage_source: QuotaUsageSource | None = None
        self._quota_usage_queue: asyncio.Queue[int] | None = None
        self._quota_usage_coverage: dict[str, float | None] = {}
        self._quota_spawn_accounts: dict[str, str | None] = {}

    def start_quota_usage(self, source: QuotaUsageSource) -> None:
        """Begin attributing: catch up on what is missing, then follow new samples."""
        self._quota_usage_source = source
        self._quota_usage_queue = asyncio.Queue()
        background.start(QUOTA_USAGE_LOOP, self._quota_usage_loop)

    async def stop_quota_usage(self) -> None:
        if self._quota_usage_queue is not None:
            await background.stop(QUOTA_USAGE_LOOP)
            self._quota_usage_queue = None

    def _note_quota_sample(self, sample_id: int) -> None:
        if self._quota_usage_queue is not None:
            self._quota_usage_queue.put_nowait(sample_id)

    async def _quota_usage_loop(self) -> None:
        queue = self._quota_usage_queue
        assert queue is not None
        # Samples that land during catch-up wait in the queue, so they are attributed
        # with the rates catch-up has just made available.
        await self.catch_up_quota_usage()
        while True:
            ids = [await queue.get()]
            while not queue.empty():
                ids.append(queue.get_nowait())
            with background.iteration(QUOTA_USAGE_LOOP):
                for sample_id in ids:
                    await self.attribute_quota_sample(sample_id)

    async def _usage_coverage(self, provider: str) -> float | None:
        """When the ledger's evidence for `provider` begins, cached for the daemon's life.

        A provider swe-mux never ran falls back to the ledger's own start: before that
        nothing is known, after it "no swe-mux activity" is a fact.
        """
        if provider not in self._quota_usage_coverage:
            source = self._quota_usage_source
            if source is None:
                return None
            coverage = await source.quota_usage_coverage(provider)
            if coverage is None:
                coverage = await source.quota_usage_coverage(None)
            if coverage is None:
                # An empty ledger is asked again next time rather than cached as empty.
                return None
            self._quota_usage_coverage[provider] = coverage
        return self._quota_usage_coverage[provider]

    async def attribute_quota_sample(self, sample_id: int) -> int:
        """Attribute the interval a new sample closes, and re-attribute the one before it."""
        pairs = await self._run(partial(self._trailing_usage_pairs, sample_id))
        if not pairs:
            return 0
        coverage = await self._usage_coverage(str(pairs[-1][1]["provider"]))
        if coverage is None:
            return 0
        pairs = [pair for pair in pairs if float(pair[1]["sampled_at"]) >= coverage]
        return await self._attribute_pairs(pairs) if pairs else 0

    async def catch_up_quota_usage(self) -> int:
        """Attribute every covered interval that has no current row, oldest first."""
        if self._quota_usage_source is None:
            return 0
        started = time.perf_counter()
        written = 0
        accounts = await self._run(
            lambda: [
                (str(row[0]), str(row[1]))
                for row in self._db.execute(
                    "SELECT DISTINCT provider,account_id FROM quota_samples"
                ).fetchall()
            ]
        )
        for provider, account_id in accounts:
            coverage = await self._usage_coverage(provider)
            if coverage is None:
                continue
            missing = await self._run(
                partial(self._missing_usage_pairs, provider, account_id, coverage)
            )
            for chunk in catch_up_chunks(missing):
                with background.iteration(QUOTA_USAGE_LOOP):
                    pairs = await self._run(partial(self._usage_pair_rows, chunk))
                    written += await self._attribute_pairs(pairs)
                await asyncio.sleep(CATCH_UP_PAUSE_SECONDS)
        elapsed = time.perf_counter() - started
        log.info(
            "quota_usage_catch_up written=%d accounts=%d elapsed_s=%.2f",
            written,
            len(accounts),
            elapsed,
            extra={"written": written, "accounts": len(accounts), "elapsed_s": round(elapsed, 2)},
        )
        return written

    async def _attribute_pairs(self, pairs: list[tuple[sqlite3.Row, sqlite3.Row]]) -> int:
        source = self._quota_usage_source
        if source is None or not pairs:
            return 0
        provider = str(pairs[0][1]["provider"])
        start = min(float(previous["sampled_at"]) for previous, _ in pairs)
        end = max(float(current["sampled_at"]) for _, current in pairs)
        raw = await source.quota_usage_evidence(provider, start, end)
        evidence = Evidence(raw.get("requests", []), raw.get("turns", []))
        follows = switch_reaches_live(provider)
        live_spawns = {} if follows else self._live_spawn_accounts(provider)
        written: int = await self._run(
            partial(self._write_usage_intervals, pairs, evidence, follows, live_spawns)
        )
        log.debug(
            "quota_usage_attributed provider=%s account=%s intervals=%d requests=%d",
            provider,
            pairs[0][1]["account_id"],
            written,
            len(evidence.requests),
        )
        return written

    def _live_spawn_accounts(self, provider: str) -> dict[str, str]:
        """Run id -> the account each live session of `provider` was spawned under."""
        result: dict[str, str] = {}
        for session in getattr(self.sessions, "sessions", {}).values():
            record = getattr(session, "record", None)
            if record is None or getattr(record, "spawn_provider", None) != provider:
                continue
            account = getattr(record, "spawn_provider_account_id", None)
            if not account:
                continue
            for run_id in (getattr(record, "agent_run_id", None), getattr(record, "id", None)):
                if run_id:
                    result[str(run_id)] = str(account)
        return result

    # -- database side (store executor) --------------------------------------------------

    def _usage_for_series(
        self,
        *,
        provider: str | None,
        account_id: str | None,
        since: float | None,
        until: float | None,
        daily: bool,
    ) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
        """Stored intervals that rose, keyed like the quota series they annotate.

        Raw resolution returns each rising interval; daily sums them per UTC day of the
        interval's end, the same day key the daily quota rollups use.
        """
        filters = ["(session_rise>0 OR weekly_rise>0)"]
        arguments: list[Any] = []
        for column, value in (("provider", provider), ("account_id", account_id)):
            if value:
                filters.append(f"{column}=?")
                arguments.append(value)
        if since is not None:
            filters.append("interval_end>=?")
            arguments.append(since)
        if until is not None:
            filters.append("interval_end<=?")
            arguments.append(until)
        where = " AND ".join(filters)
        grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        if daily:
            rows = self._db.execute(
                "SELECT provider,account_id,provider_account_uuid,"  # noqa: S608
                f"date(interval_end,'unixepoch') day,{DAILY_USAGE_COLUMNS} "
                f"FROM quota_usage_intervals WHERE {where} "
                "GROUP BY provider,account_id,provider_account_uuid,day ORDER BY day",
                arguments,
            ).fetchall()
            for row in rows:
                key = (str(row["provider"]), str(row["account_id"]), str(row[2] or ""))
                grouped.setdefault(key, []).append(daily_usage_public(row))
            return grouped
        for row in self._db.execute(
            f"SELECT * FROM quota_usage_intervals WHERE {where} ORDER BY interval_end",  # noqa: S608
            arguments,
        ).fetchall():
            key = (str(row["provider"]), str(row["account_id"]), str(row["provider_account_uuid"]))
            grouped.setdefault(key, []).append(usage_interval_public(row))
        return grouped

    def _window_sources(self, sample: sqlite3.Row, window: str) -> dict[str, Any] | None:
        """Where the current `window` usage of one reading came from.

        The window began at the last reset attributed inside its length, or - with none -
        a window's length before the provider's reset time (before the reading, when the
        provider gives none). A Claude reset that comes early keeps the old reset time, so
        an observed reset is the better answer whenever there is one.
        """
        used = sample[f"{window}_used"]
        if used is None or sample["status"] != "ready":
            return None
        at = float(sample["sampled_at"])
        resets_at = sample[f"{window}_reset_at"]
        began = (float(resets_at) if resets_at else at) - WINDOW_SECONDS[window]
        series = (
            sample["provider"],
            sample["account_id"],
            sample["provider_account_uuid"] or "",
        )
        scope = (
            "FROM quota_usage_intervals WHERE provider=? AND account_id=? "
            "AND provider_account_uuid=? AND interval_end>=? AND interval_end<=?"
        )
        reset_at = self._db.execute(
            f"SELECT MAX(interval_end) {scope} AND {window}_reset=1",  # noqa: S608
            (*series, began, at),
        ).fetchone()[0]
        since = float(reset_at) if reset_at is not None else began
        sums = self._db.execute(
            "SELECT "  # noqa: S608
            + ",".join(f"SUM({window}_{source})" for source in USAGE_SOURCES)
            + f" {scope}",
            (*series, since, at),
        ).fetchone()
        shares = window_shares(
            float(used),
            {source: float(sums[index] or 0.0) for index, source in enumerate(USAGE_SOURCES)},
        )
        return {
            # When the window began: the reading that saw it reset, or the reset time's
            # arithmetic when no reset was seen (`reset_seen`).
            "since": since,
            "reset_seen": reset_at is not None,
            **{source: round(value, 3) for source, value in shares.items()},
        }

    def _trailing_usage_pairs(self, sample_id: int) -> list[tuple[sqlite3.Row, sqlite3.Row]]:
        current = self._db.execute(
            "SELECT * FROM quota_samples WHERE id=?", (sample_id,)
        ).fetchone()
        if current is None or current["status"] != "ready":
            return []
        earlier = self._db.execute(
            "SELECT * FROM quota_samples WHERE provider=? AND account_id=? AND status='ready' "
            "AND COALESCE(provider_account_uuid,'')=? "
            "AND (sampled_at<? OR (sampled_at=? AND id<?)) "
            "ORDER BY sampled_at DESC,id DESC LIMIT 2",
            (
                current["provider"],
                current["account_id"],
                current["provider_account_uuid"] or "",
                current["sampled_at"],
                current["sampled_at"],
                sample_id,
            ),
        ).fetchall()
        chain = [*reversed(earlier), current]
        return list(zip(chain, chain[1:], strict=False))

    def _missing_usage_pairs(
        self, provider: str, account_id: str, coverage: float
    ) -> list[tuple[int, int, float, float]]:
        done = {
            int(row[0])
            for row in self._db.execute(
                "SELECT sample_id FROM quota_usage_intervals WHERE provider=? AND account_id=? "
                "AND version>=?",
                (provider, account_id, QUOTA_USAGE_VERSION),
            ).fetchall()
        }
        last: dict[str, tuple[int, float]] = {}
        missing: list[tuple[int, int, float, float]] = []
        for sample_id, sampled_at, owner in self._db.execute(
            "SELECT id,sampled_at,COALESCE(provider_account_uuid,'') FROM quota_samples "
            "WHERE provider=? AND account_id=? AND status='ready' ORDER BY sampled_at,id",
            (provider, account_id),
        ).fetchall():
            previous = last.get(owner)
            last[owner] = (int(sample_id), float(sampled_at))
            if previous is None or float(sampled_at) < coverage or int(sample_id) in done:
                continue
            missing.append((previous[0], int(sample_id), previous[1], float(sampled_at)))
        return missing

    def _usage_pair_rows(
        self, chunk: Sequence[tuple[int, int, float, float]]
    ) -> list[tuple[sqlite3.Row, sqlite3.Row]]:
        ids = sorted({item for pair in chunk for item in pair[:2]})
        placeholders = ",".join("?" for _ in ids)
        rows = {
            int(row["id"]): row
            for row in self._db.execute(
                f"SELECT * FROM quota_samples WHERE id IN ({placeholders})",  # noqa: S608
                ids,
            ).fetchall()
        }
        return [
            (rows[previous], rows[current])
            for previous, current, _at, _end in chunk
            if previous in rows and current in rows
        ]

    def _selection_changes(
        self, provider: str, start: float, end: float
    ) -> list[tuple[float, str]]:
        try:
            rows = self._db.execute(
                "SELECT ts,payload_json FROM events WHERE ts>? AND ts<=? AND type IN (?,?) "
                "ORDER BY ts",
                (start, end, *SELECTION_EVENTS),
            ).fetchall()
        except sqlite3.OperationalError:
            # The store is independently testable without the history tables.
            return []
        changes: list[tuple[float, str]] = []
        for ts, payload in rows:
            try:
                data = json.loads(payload)
            except (TypeError, ValueError):
                continue
            if not isinstance(data, dict) or data.get("provider") != provider:
                continue
            if data.get("account_id"):
                changes.append((float(ts), str(data["account_id"])))
        return changes

    def _spawn_account(self, provider: str, run_id: str) -> str | None:
        """The account a run that has ended was spawned under, read from history.

        The session record stamps it exactly while the session is live; afterwards the
        account selected when the run's history row was opened is the same answer at
        poll resolution.
        """
        key = f"{provider}\0{run_id}"
        if key in self._quota_spawn_accounts:
            return self._quota_spawn_accounts[key]
        if len(self._quota_spawn_accounts) >= _SPAWN_CACHE_LIMIT:
            self._quota_spawn_accounts.clear()
        account: str | None = None
        try:
            spawned = self._db.execute(
                "SELECT spawned_at FROM history WHERE id=? OR native_id=? LIMIT 1",
                (run_id, run_id),
            ).fetchone()
        except sqlite3.OperationalError:
            spawned = None
        if spawned is not None:
            selected = self._db.execute(
                "SELECT account_id FROM quota_samples WHERE provider=? AND account_active=1 "
                "AND sampled_at<=? ORDER BY sampled_at DESC LIMIT 1",
                (provider, float(spawned[0])),
            ).fetchone()
            account = str(selected[0]) if selected is not None else None
        self._quota_spawn_accounts[key] = account
        return account

    def _usage_rate(
        self, provider: str, account_id: str, owner: str, window: str, before: float
    ) -> float | None:
        row = self._db.execute(
            f"SELECT SUM(rise),SUM(units),COUNT(*) FROM (SELECT {window}_rise rise,"  # noqa: S608
            "mux_units units FROM quota_usage_intervals WHERE provider=? AND account_id=? "
            f"AND provider_account_uuid=? AND {window}_clean=1 AND interval_end<? "
            "ORDER BY interval_end DESC LIMIT ?)",
            (provider, account_id, owner, before, RATE_INTERVALS),
        ).fetchone()
        return calibrated_rate(window, float(row[0] or 0), float(row[1] or 0), int(row[2] or 0))

    def _write_usage_intervals(
        self,
        pairs: Sequence[tuple[sqlite3.Row, sqlite3.Row]],
        evidence: Evidence,
        follows_selection: bool,
        live_spawns: dict[str, str],
    ) -> int:
        provider = str(pairs[0][1]["provider"])
        account_id = str(pairs[0][1]["account_id"])
        start = min(float(previous["sampled_at"]) for previous, _ in pairs)
        end = max(float(current["sampled_at"]) for _, current in pairs)
        selections = self._selection_changes(provider, start, end) if follows_selection else []
        runs: set[str] | None = None
        if not follows_selection:
            runs = {
                run_id
                for run_id in evidence.run_ids
                if (live_spawns.get(run_id) or self._spawn_account(provider, run_id)) == account_id
            }
        computed_at = time.time()
        for previous, current in pairs:
            interval_start = float(previous["sampled_at"])
            interval_end = float(current["sampled_at"])
            if follows_selection:
                spans, billing = billed_spans(
                    interval_start,
                    interval_end,
                    active_at_start=bool(previous["account_active"]),
                    active_at_end=bool(current["account_active"]),
                    selections=[
                        change
                        for change in selections
                        if interval_start < change[0] <= interval_end
                    ],
                    account_id=account_id,
                )
                usage = evidence.usage(spans)
            else:
                billing = "per_session"
                usage = evidence.usage([(interval_start, interval_end)], runs)
            owner = str(current["provider_account_uuid"] or "")
            values: list[Any] = [
                int(current["id"]),
                int(previous["id"]),
                provider,
                account_id,
                owner,
                interval_start,
                interval_end,
                billing,
                usage.requests,
                usage.runs,
                usage.cost_usd,
                usage.units,
                usage.active_seconds,
            ]
            for window in QUOTA_WINDOWS:
                rise, reset = window_rise(
                    previous[f"{window}_used"],
                    current[f"{window}_used"],
                    resets_at=previous[f"{window}_reset_at"],
                    at=interval_end,
                )
                clean = (
                    billing in ("selected", "per_session")
                    and rise is not None
                    and not reset
                    and interval_end - interval_start <= MAX_CLEAN_GAP_SECONDS
                    and usage.units > 0
                )
                rate = (
                    self._usage_rate(provider, account_id, owner, window, interval_end)
                    if usage.units > 0
                    else None
                )
                parts = split_rise(
                    rise,
                    usage=usage,
                    rate=rate,
                    billing=billing,
                    follows_selection=follows_selection,
                )
                values.extend(
                    [rise, int(reset), *(parts[source] for source in USAGE_SOURCES), int(clean)]
                )
            values.extend([QUOTA_USAGE_VERSION, computed_at])
            self._db.execute(
                f"INSERT OR REPLACE INTO quota_usage_intervals({_COLUMNS}) "  # noqa: S608
                f"VALUES({','.join('?' for _ in values)})",
                values,
            )
        self._db.commit()
        return len(pairs)
