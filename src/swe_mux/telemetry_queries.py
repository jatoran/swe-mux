"""Exact analytics over the canonical telemetry ledger.

Every aggregate here covers the whole requested window: a closed, clean UTC day is
answered from its rollup row, a closed clean hour of a partial day from its hourly
row, and everything else from the canonical entities, summed into one answer.
Detail pages are finite; the counts beside them are not. No query reads a
displayed slice to compute a total, and every figure names its denominator.
"""

from __future__ import annotations

import base64
import json
import sqlite3
import time
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .telemetry_otlp import provider_capabilities
from .telemetry_schema import canonical_json, digest, period_of

#: Columns a caller may filter aggregates by. Every one exists on the canonical
#: entity tables and on both rollup tables, so a filter costs nothing in exactness.
FILTER_COLUMNS = ("project_id", "backend", "model")
TOOL_FILTER_COLUMNS = (
    *FILTER_COLUMNS,
    "invocation_layer",
    "family",
    "status",
    "evidence_quality",
)

WORKLOAD_FIELDS = (
    "runs",
    "ended_runs",
    "wall_duration_count",
    "wall_duration_s",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "final_context_count",
    "final_context_sum",
    "peak_context_count",
    "peak_context_sum",
    "turns",
    "completed_turns",
    "turn_duration_count",
    "turn_duration_ms",
    "model_tool_calls",
    "runtime_tool_calls",
    "completed_tool_calls",
    "failed_tool_calls",
    "tool_duration_count",
    "tool_duration_ms",
    "approval_wait_count",
    "approval_wait_ms",
    "model_requests",
    "model_request_failures",
    "model_wait_count",
    "model_wait_ms",
    "request_input_tokens",
    "request_output_tokens",
    "request_cache_read_tokens",
    "request_cache_write_tokens",
    "approval_events",
    "stall_events",
    "subagent_events",
    "verifications",
    "successful_verifications",
)
_WORKLOAD_REAL_FIELDS = frozenset(
    {
        "wall_duration_s",
        "final_context_sum",
        "peak_context_sum",
        "turn_duration_ms",
        "tool_duration_ms",
        "approval_wait_ms",
        "model_wait_ms",
    }
)

EXPORT_KINDS: dict[str, tuple[str, str, str]] = {
    # kind -> (table, time column, primary key)
    "tool_calls": ("telemetry_tool_calls", "started_at", "tool_call_id"),
    "runs": ("telemetry_runs", "started_at", "run_id"),
    "turns": ("telemetry_turns", "started_at", "turn_id"),
    "model_requests": ("telemetry_model_requests", "finished_at", "model_request_id"),
    "skills": ("telemetry_skill_invocations", "activated_at", "skill_invocation_id"),
    "verifications": ("telemetry_verifications", "finished_at", "verification_id"),
    "compactions": ("telemetry_compactions", "observed_at", "compaction_id"),
    "provider_metrics": ("telemetry_provider_metrics", "observed_at", "metric_id"),
    "evidence": ("telemetry_evidence", "observed_at", "evidence_id"),
}
#: Extra exact-match columns a detail page of each kind accepts, beyond FILTER_COLUMNS.
PAGE_EXTRA_FILTERS: dict[str, tuple[str, ...]] = {
    "tool_calls": ("invocation_layer", "family", "status", "evidence_quality", "raw_name",
                   "run_id", "turn_id", "session_id"),
    "runs": ("session_id", "origin"),
    "turns": ("run_id", "status", "session_id"),
    "model_requests": ("run_id", "turn_id", "query_source"),
    "skills": ("run_id", "skill_name", "invocation_trigger"),
    "verifications": ("run_id", "framework", "successful"),
    "compactions": ("run_id", "trigger"),
    "provider_metrics": ("run_id", "metric_name"),
    "evidence": ("run_id", "event_type", "source_kind"),
}
FINDING_VERDICTS = ("useful", "noise", "already_known")

Filters = dict[str, str]
Span = tuple[str, float, float, str | None]


def _where(filters: Filters | None, *, prefix: str = " AND ") -> tuple[str, tuple[Any, ...]]:
    if not filters:
        return "", ()
    clauses = [f"{column}=?" for column in filters]
    return prefix + " AND ".join(clauses), tuple(filters.values())


def clean_filters(values: dict[str, str | None], allowed: Iterable[str]) -> Filters:
    """Keep only recognised, non-empty filter columns; a stray key is a 400 upstream."""

    return {
        column: str(value)
        for column, value in values.items()
        if column in allowed and value is not None and value != ""
    }


def finding_key(kind: str, identity: dict[str, Any]) -> str:
    """A stable identity for a finding, so a review outlives the window it was made in."""

    return digest(canonical_json([kind, identity]))


class AdaptiveChangeRefused(ValueError):
    """An adaptive change was proposed without the evidence the policy requires."""


def propose_adaptive_change(
    finding: dict[str, Any],
    *,
    review: dict[str, Any] | None,
    comparison_window_days: int | None,
    rollback_rule: str | None,
) -> dict[str, Any]:
    """The only gate through which a configuration change may be offered.

    Nothing in the daemon calls this yet - no finding produces a proposal - and
    that is the point of keeping the gate in code rather than in a document: a
    future caller inherits the three conditions (an operator marked the finding
    useful, a comparison window is named, a rollback rule is named) as a type-checked
    contract instead of a paragraph it may not have read.
    """

    if review is None or review.get("verdict") != "useful":
        raise AdaptiveChangeRefused("the operator has not marked this finding useful")
    if not comparison_window_days or comparison_window_days < 1:
        raise AdaptiveChangeRefused("a comparison window of at least one day is required")
    if not rollback_rule or not rollback_rule.strip():
        raise AdaptiveChangeRefused("a rollback rule is required")
    return {
        "finding_key": finding.get("finding_key"),
        "kind": finding.get("kind"),
        "comparison_window_days": int(comparison_window_days),
        "rollback_rule": rollback_rule.strip(),
        "status": "proposed_not_applied",
    }


class LedgerQueryMixin:
    """Query surface, mixed into `CanonicalTelemetryLedger`."""

    if TYPE_CHECKING:
        _catalog: sqlite3.Connection

        def _segment(self, period: str) -> sqlite3.Connection: ...

        def _periods(
            self, from_ts: float | None = None, to_ts: float | None = None
        ) -> list[str]: ...

    def _query_all(
        self,
        sql: str,
        args: Iterable[Any] = (),
        *,
        from_ts: float | None = None,
        to_ts: float | None = None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        params = tuple(args)
        for period in self._periods(from_ts, to_ts):
            rows.extend(dict(row) for row in self._segment(period).execute(sql, params).fetchall())
        return rows

    def _spans(self, from_ts: float, to_ts: float, *, hours: bool = True) -> list[Span]:
        """Split a window into rolled-up days, rolled-up hours, and raw spans.

        Raw spans never cross a segment, and consecutive raw stretches inside one
        month collapse into a single span, so a window of dirty days costs one query
        per month rather than one per day. Hour rollups exist only for the tool and
        workload tables; a caller without them asks for day spans only.
        """

        available = set(self._periods(from_ts, to_ts))
        rolled = {
            str(row["day"])
            for row in self._catalog.execute("SELECT day FROM rollup_days").fetchall()
        }
        dirty = {
            str(row["day"])
            for row in self._catalog.execute("SELECT day FROM rollup_dirty_days").fetchall()
        }
        rolled_hours: set[str] = set()
        dirty_hours: set[str] = set()
        if hours:
            rolled_hours = {
                str(row["hour"])
                for row in self._catalog.execute("SELECT hour FROM rollup_hours").fetchall()
            }
            dirty_hours = {
                str(row["hour"])
                for row in self._catalog.execute(
                    "SELECT hour FROM rollup_dirty_hours"
                ).fetchall()
            }
        spans: list[Span] = []

        def raw(start: float, end: float) -> None:
            previous = spans[-1] if spans else None
            if (
                previous is not None
                and previous[0] == "raw"
                and previous[2] == start
                and period_of(previous[1]) == period_of(start)
            ):
                spans[-1] = ("raw", previous[1], end, None)
            else:
                spans.append(("raw", start, end, None))

        day = datetime.fromtimestamp(from_ts, tz=UTC).date()
        last_day = datetime.fromtimestamp(max(from_ts, to_ts - 0.000001), tz=UTC).date()
        while day <= last_day:
            day_text = day.isoformat()
            day_start = datetime.combine(day, datetime.min.time(), tzinfo=UTC).timestamp()
            day_end = day_start + 86400
            query_start = max(from_ts, day_start)
            query_end = min(to_ts, day_end)
            full_day = query_start == day_start and query_end == day_end
            if day_text[:7] not in available:
                day += timedelta(days=1)
                continue
            if full_day and day_text in rolled and day_text not in dirty:
                spans.append(("day", day_start, day_end, day_text))
            elif not hours:
                raw(query_start, query_end)
            else:
                cursor = query_start
                while cursor < query_end:
                    hour_start = (cursor // 3600) * 3600
                    hour_end = hour_start + 3600
                    slice_end = min(query_end, hour_end)
                    hour_text = datetime.fromtimestamp(hour_start, tz=UTC).strftime(
                        "%Y-%m-%dT%H"
                    )
                    full_hour = cursor == hour_start and slice_end == hour_end
                    if full_hour and hour_text in rolled_hours and hour_text not in dirty_hours:
                        spans.append(("hour", hour_start, hour_end, hour_text))
                    else:
                        raw(cursor, slice_end)
                    cursor = slice_end
            day += timedelta(days=1)
        return spans

    @staticmethod
    def _coverage(spans: list[Span]) -> dict[str, Any]:
        """How the window was answered, so a caption can say it."""

        return {
            "rolled_days": sum(1 for span in spans if span[0] == "day"),
            "rolled_hours": sum(1 for span in spans if span[0] == "hour"),
            "raw_spans": sum(1 for span in spans if span[0] == "raw"),
            "raw_seconds": sum(span[2] - span[1] for span in spans if span[0] == "raw"),
        }

    # -- cursors ----------------------------------------------------------------

    @staticmethod
    def _encode_cursor(*parts: Any) -> str:
        payload = canonical_json(list(parts))
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str | None, length: int) -> list[Any] | None:
        if not cursor:
            return None
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            value = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
            if not isinstance(value, list) or len(value) != length:
                raise ValueError
            return value
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid telemetry cursor") from exc

    # -- detail pages -----------------------------------------------------------

    def tool_calls(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        rows = self._query_all("SELECT * FROM telemetry_tool_calls")
        rows.sort(key=lambda row: float(row["started_at"] or row["finished_at"] or 0), reverse=True)
        return rows if limit is None else rows[: max(1, limit)]

    def entity_page(
        self,
        *,
        kind: str,
        from_ts: float,
        to_ts: float,
        limit: int = 100,
        cursor: str | None = None,
        origin: str | None = "mux_owned",
        filters: Filters | None = None,
    ) -> dict[str, Any]:
        """Newest-first page of one entity kind with an exact matching count."""

        if kind not in EXPORT_KINDS:
            raise ValueError(f"unknown entity kind {kind!r}")
        table, time_column, key_column = EXPORT_KINDS[kind]
        bounded_limit = max(1, min(int(limit), 500))
        allowed = {*FILTER_COLUMNS, *PAGE_EXTRA_FILTERS.get(kind, ())}
        if kind == "runs":
            allowed.discard("model")
        usable = {column: value for column, value in (filters or {}).items() if column in allowed}
        clauses = [f"{time_column}>=?", f"{time_column}<?"]
        args: list[Any] = [from_ts, to_ts]
        if origin is not None and "origin" not in usable:
            clauses.append("origin=?")
            args.append(origin)
        for column, value in usable.items():
            clauses.append(f"{column}=?")
            args.append(value)
        count_where = " AND ".join(clauses)
        count_args = tuple(args)
        decoded = self._decode_cursor(cursor, 2)
        if decoded is not None:
            clauses.append(f"({time_column}<? OR ({time_column}=? AND {key_column}<?))")
            marker = float(decoded[0])
            args.extend((marker, marker, str(decoded[1])))
        where = " AND ".join(clauses)
        candidates: list[dict[str, Any]] = []
        matching = 0
        for period in self._periods(from_ts, to_ts):
            connection = self._segment(period)
            matching += int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {count_where}", count_args
                ).fetchone()[0]
            )
            candidates.extend(
                dict(row)
                for row in connection.execute(
                    f"SELECT * FROM {table} WHERE {where} "
                    f"ORDER BY {time_column} DESC,{key_column} DESC LIMIT ?",
                    (*args, bounded_limit + 1),
                ).fetchall()
            )
        candidates.sort(
            key=lambda row: (float(row[time_column] or 0), str(row[key_column])), reverse=True
        )
        page = candidates[:bounded_limit]
        next_cursor = None
        if len(candidates) > bounded_limit and page:
            last = page[-1]
            next_cursor = self._encode_cursor(float(last[time_column] or 0), str(last[key_column]))
        return {
            "kind": kind,
            "from": from_ts,
            "to": to_ts,
            "origin": origin or "all",
            "filters": dict(usable),
            "matching": matching,
            "items": page,
            "next_cursor": next_cursor,
        }

    def tool_page(
        self,
        *,
        from_ts: float,
        to_ts: float,
        limit: int = 100,
        cursor: str | None = None,
        project_id: str | None = None,
        backend: str | None = None,
        model: str | None = None,
        origin: str | None = "mux_owned",
        invocation_layer: str | None = None,
        family: str | None = None,
        status: str | None = None,
        evidence_quality: str | None = None,
        raw_name: str | None = None,
        run_id: str | None = None,
        turn_id: str | None = None,
    ) -> dict[str, Any]:
        filters = {
            column: value
            for column, value in {
                "project_id": project_id,
                "backend": backend,
                "model": model,
                "invocation_layer": invocation_layer,
                "family": family,
                "status": status,
                "evidence_quality": evidence_quality,
                "raw_name": raw_name,
                "run_id": run_id,
                "turn_id": turn_id,
            }.items()
            if value is not None
        }
        page = self.entity_page(
            kind="tool_calls",
            from_ts=from_ts,
            to_ts=to_ts,
            limit=limit,
            cursor=cursor,
            origin=origin,
            filters=filters,
        )
        page["matching_calls"] = page["matching"]
        return page

    # -- audits -----------------------------------------------------------------

    def _evidence_for(self, entity_kind: str, entity_id: str, period: str) -> list[dict[str, Any]]:
        connection = self._segment(period)
        links = connection.execute(
            "SELECT evidence_id,contribution,precedence_rank,conflict "
            "FROM telemetry_entity_evidence WHERE entity_kind=? AND entity_id=? "
            "ORDER BY precedence_rank DESC,evidence_id",
            (entity_kind, entity_id),
        ).fetchall()
        wanted = {str(link["evidence_id"]) for link in links}
        evidence: dict[str, dict[str, Any]] = {}
        if wanted:
            placeholders = ",".join("?" for _ in wanted)
            for other in self._periods():
                for row in self._segment(other).execute(
                    f"SELECT * FROM telemetry_evidence WHERE evidence_id IN ({placeholders})",
                    tuple(wanted),
                ).fetchall():
                    evidence[str(row["evidence_id"])] = dict(row)
        return [
            {**dict(link), "observation": evidence.get(str(link["evidence_id"]))}
            for link in links
        ]

    def _locate(self, entity_kind: str, entity_id: str) -> str | None:
        row = self._catalog.execute(
            "SELECT period FROM entity_locations WHERE entity_kind=? AND entity_id=?",
            (entity_kind, entity_id),
        ).fetchone()
        return str(row["period"]) if row is not None else None

    def tool_audit(self, tool_call_id: str) -> dict[str, Any] | None:
        period = self._locate("tool_call", tool_call_id)
        if period is None:
            return None
        call = self._segment(period).execute(
            "SELECT * FROM telemetry_tool_calls WHERE tool_call_id=?", (tool_call_id,)
        ).fetchone()
        if call is None:
            return None
        return {
            "call": dict(call),
            "evidence": self._evidence_for("tool_call", tool_call_id, period),
        }

    def run_audit(self, run_id: str) -> dict[str, Any] | None:
        """One run: its row, its turns, per-status call counts, and its lifecycle evidence."""

        period = self._locate("run", run_id)
        if period is None:
            return None
        run = self._segment(period).execute(
            "SELECT * FROM telemetry_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if run is None:
            return None
        turns = self._query_all(
            "SELECT * FROM telemetry_turns WHERE run_id=? ORDER BY started_at", (run_id,)
        )
        calls: dict[str, Any] = {
            "total": 0,
            "by_status": {},
            "by_layer": {},
            "by_quality": {},
        }
        for row in self._query_all(
            "SELECT status,invocation_layer,evidence_quality,COUNT(*) count "
            "FROM telemetry_tool_calls WHERE run_id=? GROUP BY 1,2,3",
            (run_id,),
        ):
            count = int(row["count"])
            calls["total"] += count
            for key, column in (
                ("by_status", "status"),
                ("by_layer", "invocation_layer"),
                ("by_quality", "evidence_quality"),
            ):
                bucket = calls[key]
                bucket[row[column]] = bucket.get(row[column], 0) + count
        requests = self._query_all(
            "SELECT COUNT(*) count,SUM(success=0) failures,SUM(COALESCE(input_tokens,0)) "
            "input_tokens,SUM(COALESCE(output_tokens,0)) output_tokens "
            "FROM telemetry_model_requests WHERE run_id=?",
            (run_id,),
        )
        metrics = self._query_all(
            "SELECT metric_name,COUNT(*) points,SUM(COALESCE(count,0)) count,"
            "SUM(COALESCE(sum,0)) total FROM telemetry_provider_metrics WHERE run_id=? "
            "GROUP BY metric_name ORDER BY metric_name",
            (run_id,),
        )
        return {
            "run": dict(run),
            "turns": turns,
            "tool_calls": calls,
            "model_requests": {
                "count": sum(int(row["count"] or 0) for row in requests),
                "failures": sum(int(row["failures"] or 0) for row in requests),
                "input_tokens": sum(int(row["input_tokens"] or 0) for row in requests),
                "output_tokens": sum(int(row["output_tokens"] or 0) for row in requests),
            },
            "provider_metrics": metrics,
            "reconciliation": self.native_reconciliation_for(run_id),
            "evidence": self._evidence_for("run", run_id, period),
        }

    def turn_audit(self, turn_id: str) -> dict[str, Any] | None:
        period = self._locate("turn", turn_id)
        if period is None:
            return None
        turn = self._segment(period).execute(
            "SELECT * FROM telemetry_turns WHERE turn_id=?", (turn_id,)
        ).fetchone()
        if turn is None:
            return None
        return {
            "turn": dict(turn),
            "tool_calls": self._query_all(
                "SELECT * FROM telemetry_tool_calls WHERE turn_id=? ORDER BY started_at",
                (turn_id,),
            ),
            "model_requests": self._query_all(
                "SELECT * FROM telemetry_model_requests WHERE turn_id=? ORDER BY finished_at",
                (turn_id,),
            ),
            "evidence": self._evidence_for("turn", turn_id, period),
        }

    def turns(self) -> list[dict[str, Any]]:
        return self._query_all("SELECT * FROM telemetry_turns ORDER BY started_at")

    def skills(self) -> list[dict[str, Any]]:
        return self._query_all("SELECT * FROM telemetry_skill_invocations ORDER BY activated_at")

    def verifications(self) -> list[dict[str, Any]]:
        return self._query_all("SELECT * FROM telemetry_verifications ORDER BY finished_at")

    def model_requests(self) -> list[dict[str, Any]]:
        return self._query_all("SELECT * FROM telemetry_model_requests ORDER BY finished_at")

    def provider_metrics(self) -> list[dict[str, Any]]:
        return self._query_all("SELECT * FROM telemetry_provider_metrics ORDER BY observed_at")

    # -- summaries --------------------------------------------------------------

    def _rollup_or_raw(
        self,
        *,
        from_ts: float,
        to_ts: float,
        origin: str | None,
        filters: Filters | None,
        rollup_table: str,
        rollup_columns: str,
        raw_sql: str,
        hours: bool,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Rows from the right source for every span of the window, plus coverage."""

        origin_clause = "" if origin is None else " AND origin=?"
        origin_args: tuple[Any, ...] = () if origin is None else (origin,)
        filter_clause, filter_args = _where(filters)
        spans = self._spans(from_ts, to_ts, hours=hours)
        rows: list[dict[str, Any]] = []
        for kind, start, end, bucket in spans:
            if kind == "day" or kind == "hour":
                table = rollup_table if kind == "day" else rollup_table.replace("daily", "hourly")
                column = "day" if kind == "day" else "hour"
                rows.extend(
                    dict(row)
                    for row in self._catalog.execute(
                        f"SELECT {rollup_columns} FROM {table} WHERE {column}=?"
                        f"{origin_clause}{filter_clause}",
                        (bucket, *origin_args, *filter_args),
                    ).fetchall()
                )
            else:
                rows.extend(
                    dict(row)
                    for row in self._segment(period_of(start)).execute(
                        raw_sql.format(origin=origin_clause, filters=filter_clause),
                        (start, end, *origin_args, *filter_args),
                    ).fetchall()
                )
        return rows, self._coverage(spans)

    def compaction_summary(
        self,
        *,
        from_ts: float,
        to_ts: float,
        origin: str | None = "mux_owned",
        filters: Filters | None = None,
    ) -> dict[str, Any]:
        rows, coverage = self._rollup_or_raw(
            from_ts=from_ts,
            to_ts=to_ts,
            origin=origin,
            filters=filters,
            rollup_table="compaction_daily",
            rollup_columns=(
                "backend,model,project_id,origin,trigger,count,failures,duration_count,"
                "duration_ms,token_count,tokens_reclaimed"
            ),
            raw_sql=(
                "SELECT backend,COALESCE(model,'unknown') model,COALESCE(project_id,'') "
                "project_id,origin,COALESCE(trigger,'unknown') trigger,COUNT(*) count,"
                "SUM(success=0) failures,SUM(duration_ms IS NOT NULL) duration_count,"
                "SUM(COALESCE(duration_ms,0)) duration_ms,"
                "SUM(tokens_before IS NOT NULL AND tokens_after IS NOT NULL) token_count,"
                "SUM(CASE WHEN tokens_before IS NOT NULL AND tokens_after IS NOT NULL "
                "THEN tokens_before-tokens_after ELSE 0 END) tokens_reclaimed "
                "FROM telemetry_compactions WHERE observed_at>=? AND observed_at<?"
                "{origin}{filters} GROUP BY backend,model,project_id,origin,trigger"
            ),
            hours=False,
        )
        groups: dict[tuple[str, ...], dict[str, Any]] = {}
        total = 0
        for row in rows:
            count = int(row["count"])
            total += count
            key = tuple(
                str(row[field])
                for field in ("backend", "model", "project_id", "origin", "trigger")
            )
            group = groups.setdefault(
                key,
                {
                    "backend": key[0],
                    "model": key[1],
                    "project_id": key[2],
                    "origin": key[3],
                    "trigger": key[4],
                    "count": 0,
                    "failures": 0,
                    "duration_count": 0,
                    "duration_ms": 0.0,
                    "token_count": 0,
                    "tokens_reclaimed": 0,
                },
            )
            for field in ("count", "failures", "duration_count", "token_count", "tokens_reclaimed"):
                group[field] += int(row[field] or 0)
            group["duration_ms"] += float(row["duration_ms"] or 0)
        for group in groups.values():
            group["average_duration_ms"] = (
                group["duration_ms"] / group["duration_count"] if group["duration_count"] else None
            )
            group["average_tokens_reclaimed"] = (
                group["tokens_reclaimed"] / group["token_count"] if group["token_count"] else None
            )
        return {
            "from": from_ts,
            "to": to_ts,
            "origin": origin or "all",
            "filters": dict(filters or {}),
            "coverage": coverage,
            "total": total,
            "groups": sorted(groups.values(), key=lambda item: (-item["count"], item["backend"])),
        }

    def skill_summary(
        self,
        *,
        from_ts: float,
        to_ts: float,
        origin: str | None = "mux_owned",
        filters: Filters | None = None,
    ) -> dict[str, Any]:
        rows, coverage = self._rollup_or_raw(
            from_ts=from_ts,
            to_ts=to_ts,
            origin=origin,
            filters=filters,
            rollup_table="skill_daily",
            rollup_columns=(
                "backend,model,project_id,origin,skill_name,invocation_trigger,skill_source,"
                "skill_scope,invocations"
            ),
            raw_sql=(
                "SELECT backend,COALESCE(model,'unknown') model,COALESCE(project_id,'') "
                "project_id,origin,skill_name,COALESCE(invocation_trigger,'unknown') "
                "invocation_trigger,COALESCE(skill_source,'unknown') skill_source,"
                "COALESCE(skill_scope,'unknown') skill_scope,SUM(occurrences) invocations "
                "FROM telemetry_skill_invocations WHERE activated_at>=? AND activated_at<?"
                "{origin}{filters} GROUP BY backend,model,project_id,origin,skill_name,"
                "invocation_trigger,skill_source,skill_scope"
            ),
            hours=False,
        )
        groups: dict[tuple[str, ...], dict[str, Any]] = {}
        matching = 0
        for row in rows:
            count = int(row["invocations"])
            matching += count
            key = tuple(
                str(row[field])
                for field in (
                    "backend",
                    "model",
                    "project_id",
                    "origin",
                    "skill_name",
                    "invocation_trigger",
                    "skill_source",
                    "skill_scope",
                )
            )
            group = groups.setdefault(
                key,
                {
                    "backend": key[0],
                    "model": key[1],
                    "project_id": key[2],
                    "origin": key[3],
                    "skill_name": key[4],
                    "invocation_trigger": key[5],
                    "skill_source": key[6],
                    "skill_scope": key[7],
                    "invocations": 0,
                },
            )
            group["invocations"] += count
        return {
            "from": from_ts,
            "to": to_ts,
            "origin": origin or "all",
            "filters": dict(filters or {}),
            "coverage": coverage,
            "matching_invocations": matching,
            "groups": sorted(
                groups.values(), key=lambda item: (-item["invocations"], item["skill_name"])
            ),
        }

    def verification_summary(
        self,
        *,
        from_ts: float,
        to_ts: float,
        origin: str | None = "mux_owned",
        filters: Filters | None = None,
    ) -> dict[str, Any]:
        rows, coverage = self._rollup_or_raw(
            from_ts=from_ts,
            to_ts=to_ts,
            origin=origin,
            filters=filters,
            rollup_table="verification_daily",
            rollup_columns=(
                "backend,model,project_id,origin,framework,verifications,successful,passed,"
                "failed,errors,skipped"
            ),
            raw_sql=(
                "SELECT backend,COALESCE(model,'unknown') model,COALESCE(project_id,'') "
                "project_id,origin,framework,COUNT(*) verifications,SUM(successful=1) successful,"
                "SUM(COALESCE(passed,0)) passed,SUM(COALESCE(failed,0)) failed,"
                "SUM(COALESCE(errors,0)) errors,SUM(COALESCE(skipped,0)) skipped "
                "FROM telemetry_verifications WHERE finished_at>=? AND finished_at<?"
                "{origin}{filters} GROUP BY backend,model,project_id,origin,framework"
            ),
            hours=False,
        )
        groups: dict[tuple[str, ...], dict[str, Any]] = {}
        totals = {
            "verifications": 0,
            "successful": 0,
            "passed": 0,
            "failed": 0,
            "errors": 0,
            "skipped": 0,
        }
        for row in rows:
            key = tuple(
                str(row[field])
                for field in ("backend", "model", "project_id", "origin", "framework")
            )
            group = groups.setdefault(
                key,
                {
                    "backend": key[0],
                    "model": key[1],
                    "project_id": key[2],
                    "origin": key[3],
                    "framework": key[4],
                    **dict.fromkeys(totals, 0),
                },
            )
            for field in totals:
                value = int(row[field] or 0)
                group[field] += value
                totals[field] += value
        for group in groups.values():
            group["success_rate"] = (
                group["successful"] / group["verifications"] if group["verifications"] else None
            )
        return {
            "from": from_ts,
            "to": to_ts,
            "origin": origin or "all",
            "filters": dict(filters or {}),
            "coverage": coverage,
            "totals": totals,
            "groups": sorted(
                groups.values(), key=lambda item: (-item["verifications"], item["framework"])
            ),
        }

    _TOOL_RAW_SQL = (
        "SELECT backend,COALESCE(model,'unknown') model,COALESCE(project_id,'') project_id,"
        "origin,invocation_layer,family,operation,transport,raw_name,status,evidence_quality,"
        "COUNT(*) count,SUM(duration_ms IS NOT NULL) duration_count,"
        "SUM(COALESCE(duration_ms,0)) duration_ms,SUM(COALESCE(input_bytes,0)) input_bytes,"
        "SUM(COALESCE(output_bytes,0)) output_bytes,"
        "SUM(approval_wait_ms IS NOT NULL) approval_wait_count,"
        "SUM(COALESCE(approval_wait_ms,0)) approval_wait_ms "
        "FROM telemetry_tool_calls WHERE started_at>=? AND started_at<?{origin}{filters} "
        "GROUP BY backend,model,project_id,origin,invocation_layer,family,operation,"
        "transport,raw_name,status,evidence_quality"
    )
    _TOOL_ROLLUP_COLUMNS = (
        "backend,model,project_id,origin,invocation_layer,family,operation,transport,raw_name,"
        "status,evidence_quality,calls count,duration_count,duration_ms,input_bytes,output_bytes,"
        "approval_wait_count,approval_wait_ms"
    )

    def tool_summary(
        self,
        *,
        from_ts: float,
        to_ts: float,
        origin: str | None = "mux_owned",
        filters: Filters | None = None,
    ) -> dict[str, Any]:
        rows, coverage = self._rollup_or_raw(
            from_ts=from_ts,
            to_ts=to_ts,
            origin=origin,
            filters=filters,
            rollup_table="tool_daily",
            rollup_columns=self._TOOL_ROLLUP_COLUMNS,
            raw_sql=self._TOOL_RAW_SQL,
            hours=True,
        )
        totals = {
            "model_calls": 0,
            "runtime_calls": 0,
            "completed": 0,
            "succeeded": 0,
            "failed": 0,
            "denied": 0,
            "interrupted": 0,
            "abandoned": 0,
        }
        qualities: dict[str, int] = {}
        groups: dict[tuple[str, ...], dict[str, Any]] = {}
        matching = 0
        duration_count = 0
        duration_ms = 0.0
        input_bytes = 0
        output_bytes = 0
        approval_wait_count = 0
        approval_wait_ms = 0.0
        for row in rows:
            count = int(row["count"])
            matching += count
            duration_count += int(row["duration_count"] or 0)
            duration_ms += float(row["duration_ms"] or 0)
            input_bytes += int(row["input_bytes"] or 0)
            output_bytes += int(row["output_bytes"] or 0)
            approval_wait_count += int(row["approval_wait_count"] or 0)
            approval_wait_ms += float(row["approval_wait_ms"] or 0)
            layer_key = "runtime_calls" if row["invocation_layer"] == "runtime" else "model_calls"
            totals[layer_key] += count
            status = str(row["status"])
            quality = str(row["evidence_quality"])
            qualities[quality] = qualities.get(quality, 0) + count
            if status in {"succeeded", "failed", "denied", "interrupted", "abandoned"}:
                totals["completed"] += count
            if status in totals:
                totals[status] += count
            key = (
                str(row["backend"]),
                str(row["model"]),
                str(row["project_id"]),
                str(row["origin"]),
                str(row["invocation_layer"]),
                str(row["family"]),
                str(row["operation"]),
                str(row["transport"]),
                str(row["raw_name"]),
            )
            group = groups.setdefault(
                key,
                {
                    "backend": key[0],
                    "model": key[1],
                    "project_id": key[2],
                    "origin": key[3],
                    "invocation_layer": key[4],
                    "family": key[5],
                    "operation": key[6],
                    "transport": key[7],
                    "raw_name": key[8],
                    "calls": 0,
                    "statuses": {},
                    "qualities": {},
                    "duration_count": 0,
                    "duration_ms": 0.0,
                    "input_bytes": 0,
                    "output_bytes": 0,
                    "approval_wait_count": 0,
                    "approval_wait_ms": 0.0,
                },
            )
            group["calls"] += count
            group["statuses"][status] = group["statuses"].get(status, 0) + count
            group["qualities"][quality] = group["qualities"].get(quality, 0) + count
            group["duration_count"] += int(row["duration_count"] or 0)
            group["duration_ms"] += float(row["duration_ms"] or 0)
            group["input_bytes"] += int(row["input_bytes"] or 0)
            group["output_bytes"] += int(row["output_bytes"] or 0)
            group["approval_wait_count"] += int(row["approval_wait_count"] or 0)
            group["approval_wait_ms"] += float(row["approval_wait_ms"] or 0)
        for group in groups.values():
            group["average_duration_ms"] = (
                group["duration_ms"] / group["duration_count"] if group["duration_count"] else None
            )
            group["average_approval_wait_ms"] = (
                group["approval_wait_ms"] / group["approval_wait_count"]
                if group["approval_wait_count"]
                else None
            )
        skill_filters = {
            column: value for column, value in (filters or {}).items() if column in FILTER_COLUMNS
        }
        return {
            "from": from_ts,
            "to": to_ts,
            "origin": origin or "all",
            "filters": dict(filters or {}),
            "coverage": coverage,
            "matching_calls": matching,
            "totals": totals,
            "qualities": qualities,
            "duration_coverage": {
                "measured": duration_count,
                "completed": totals["completed"],
                "average_ms": duration_ms / duration_count if duration_count else None,
            },
            "approval_wait": {
                "measured": approval_wait_count,
                "average_ms": (
                    approval_wait_ms / approval_wait_count if approval_wait_count else None
                ),
            },
            "input_bytes": input_bytes,
            "output_bytes": output_bytes,
            "groups": sorted(groups.values(), key=lambda item: (-item["calls"], item["raw_name"])),
            "skills": self.skill_summary(
                from_ts=from_ts, to_ts=to_ts, origin=origin, filters=skill_filters
            ),
        }

    # -- workload ---------------------------------------------------------------

    @staticmethod
    def _workload_raw_rows(
        connection: sqlite3.Connection,
        start: float,
        end: float,
        origin: str | None,
        filters: Filters | None,
    ) -> Iterator[dict[str, Any]]:
        """Partial workload rows from one segment; each carries the group key.

        Six independent axes, each read from the entity that owns it, so a
        missing duration on one axis never leaks into another.
        """

        origin_clause = "" if origin is None else " AND origin=?"
        origin_args: tuple[Any, ...] = () if origin is None else (origin,)
        filter_clause, filter_args = _where(filters)
        run_filter_clause, run_filter_args = _where(
            {column: value for column, value in (filters or {}).items() if column != "model"}
        )
        run_model_clause = ""
        run_model_args: tuple[Any, ...] = ()
        if filters and "model" in filters:
            run_model_clause = " AND model=?"
            run_model_args = (filters["model"],)
        args = (start, end, *origin_args, *filter_args)
        for row in connection.execute(
            "SELECT * FROM (SELECT backend,CASE WHEN initial_model IS NOT NULL "
            "AND final_model IS NOT NULL AND initial_model!=final_model THEN 'mixed' "
            "ELSE COALESCE(final_model,initial_model,'unknown') END model,"
            "COALESCE(project_id,'') project_id,origin,"
            "COUNT(*) runs,SUM(ended_at IS NOT NULL) ended_runs,"
            "SUM(CASE WHEN ended_at IS NOT NULL THEN 1 ELSE 0 END) wall_duration_count,"
            "SUM(CASE WHEN ended_at IS NOT NULL THEN ended_at-started_at ELSE 0 END) "
            "wall_duration_s,"
            "SUM(input_tokens) input_tokens,SUM(output_tokens) output_tokens,"
            "SUM(cache_read_tokens) cache_read_tokens,"
            "SUM(cache_write_tokens) cache_write_tokens,"
            "SUM(final_context_pct IS NOT NULL) final_context_count,"
            "SUM(COALESCE(final_context_pct,0)) final_context_sum,"
            "SUM(peak_context_pct IS NOT NULL) peak_context_count,"
            "SUM(COALESCE(peak_context_pct,0)) peak_context_sum "
            "FROM telemetry_runs WHERE started_at>=? AND started_at<?"
            f"{origin_clause}{run_filter_clause} GROUP BY backend,model,project_id,origin)"
            f"WHERE 1=1{run_model_clause}",
            (start, end, *origin_args, *run_filter_args, *run_model_args),
        ).fetchall():
            yield dict(row)
        for row in connection.execute(
            "SELECT backend,COALESCE(model,'unknown') model,COALESCE(project_id,'') project_id,"
            "origin,COUNT(*) turns,"
            "SUM(status='completed') completed_turns,SUM(duration_ms IS NOT NULL) "
            "turn_duration_count,SUM(COALESCE(duration_ms,0)) turn_duration_ms "
            "FROM telemetry_turns WHERE started_at>=? AND started_at<?"
            f"{origin_clause}{filter_clause} GROUP BY backend,model,project_id,origin",
            args,
        ).fetchall():
            yield dict(row)
        for row in connection.execute(
            "SELECT backend,COALESCE(model,'unknown') model,COALESCE(project_id,'') project_id,"
            "origin,SUM(invocation_layer='model') model_tool_calls,"
            "SUM(invocation_layer='runtime') runtime_tool_calls,"
            "SUM(status IN ('succeeded','failed','denied','interrupted','abandoned')) "
            "completed_tool_calls,SUM(status='failed') failed_tool_calls,"
            "SUM(duration_ms IS NOT NULL) tool_duration_count,"
            "SUM(COALESCE(duration_ms,0)) tool_duration_ms,"
            "SUM(approval_wait_ms IS NOT NULL) approval_wait_count,"
            "SUM(COALESCE(approval_wait_ms,0)) approval_wait_ms FROM telemetry_tool_calls "
            "WHERE started_at>=? AND started_at<?"
            f"{origin_clause}{filter_clause} GROUP BY backend,model,project_id,origin",
            args,
        ).fetchall():
            yield dict(row)
        for row in connection.execute(
            "SELECT backend,COALESCE(model,'unknown') model,COALESCE(project_id,'') project_id,"
            "origin,COUNT(*) model_requests,SUM(success=0) model_request_failures,"
            "SUM(duration_ms IS NOT NULL) model_wait_count,"
            "SUM(COALESCE(duration_ms,0)) model_wait_ms,"
            "SUM(COALESCE(input_tokens,0)) request_input_tokens,"
            "SUM(COALESCE(output_tokens,0)) request_output_tokens,"
            "SUM(COALESCE(cache_read_tokens,0)) request_cache_read_tokens,"
            "SUM(COALESCE(cache_write_tokens,0)) request_cache_write_tokens "
            "FROM telemetry_model_requests WHERE finished_at>=? AND finished_at<?"
            f"{origin_clause}{filter_clause} GROUP BY backend,model,project_id,origin",
            args,
        ).fetchall():
            yield dict(row)
        for row in connection.execute(
            "SELECT backend,COALESCE(model,'unknown') model,COALESCE(project_id,'') project_id,"
            "origin,SUM(event_type='approval_needed') approval_events,"
            "SUM(event_type='stalled') stall_events,"
            "SUM(event_type='subagent_activity') subagent_events "
            "FROM telemetry_evidence WHERE observed_at>=? AND observed_at<? "
            "AND event_type IN ('approval_needed','stalled','subagent_activity')"
            f"{origin_clause}{filter_clause} GROUP BY backend,model,project_id,origin",
            args,
        ).fetchall():
            yield dict(row)
        for row in connection.execute(
            "SELECT backend,COALESCE(model,'unknown') model,COALESCE(project_id,'') project_id,"
            "origin,COUNT(*) verifications,SUM(successful=1) successful_verifications "
            "FROM telemetry_verifications WHERE finished_at>=? AND finished_at<?"
            f"{origin_clause}{filter_clause} GROUP BY backend,model,project_id,origin",
            args,
        ).fetchall():
            yield dict(row)

    @staticmethod
    def _workload_group(
        groups: dict[tuple[str, str, str, str], dict[str, Any]], row: dict[str, Any]
    ) -> dict[str, Any]:
        key = (
            str(row.get("backend") or "unknown"),
            str(row.get("model") or "unknown"),
            str(row.get("project_id") or ""),
            str(row.get("origin") or "unknown"),
        )
        group = groups.get(key)
        if group is None:
            group = {"backend": key[0], "model": key[1], "project_id": key[2], "origin": key[3]}
            for field in WORKLOAD_FIELDS:
                group[field] = 0.0 if field in _WORKLOAD_REAL_FIELDS else 0
            groups[key] = group
        for field in WORKLOAD_FIELDS:
            value = row.get(field)
            if value is None:
                continue
            if field in _WORKLOAD_REAL_FIELDS:
                group[field] += float(value)
            else:
                group[field] += int(value)
        return group

    def workload_rows_between(self, period: str, start: float, end: float) -> list[dict[str, Any]]:
        """Exact per-group workload for one bucket, for the rollup tables."""

        groups: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for row in self._workload_raw_rows(self._segment(period), start, end, None, None):
            self._workload_group(groups, row)
        return list(groups.values())

    def workload_summary(
        self,
        *,
        from_ts: float,
        to_ts: float,
        origin: str | None = "mux_owned",
        filters: Filters | None = None,
    ) -> dict[str, Any]:
        """Exact workload cohorts with each duration axis kept independent."""

        groups: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        origin_clause = "" if origin is None else " AND origin=?"
        origin_args: tuple[Any, ...] = () if origin is None else (origin,)
        filter_clause, filter_args = _where(filters)
        spans = self._spans(from_ts, to_ts, hours=True)
        for kind, start, end, bucket in spans:
            if kind in {"day", "hour"}:
                table = "workload_daily" if kind == "day" else "workload_hourly"
                column = "day" if kind == "day" else "hour"
                for rolled in self._catalog.execute(
                    f"SELECT * FROM {table} WHERE {column}=?{origin_clause}{filter_clause}",
                    (bucket, *origin_args, *filter_args),
                ).fetchall():
                    self._workload_group(groups, dict(rolled))
                continue
            for row in self._workload_raw_rows(
                self._segment(period_of(start)), start, end, origin, filters
            ):
                self._workload_group(groups, row)
        for group in groups.values():
            for name, total, count in (
                ("average_wall_duration_s", "wall_duration_s", "wall_duration_count"),
                ("average_turn_duration_ms", "turn_duration_ms", "turn_duration_count"),
                ("average_tool_duration_ms", "tool_duration_ms", "tool_duration_count"),
                ("average_model_wait_ms", "model_wait_ms", "model_wait_count"),
                ("average_approval_wait_ms", "approval_wait_ms", "approval_wait_count"),
                ("average_final_context_pct", "final_context_sum", "final_context_count"),
                ("average_peak_context_pct", "peak_context_sum", "peak_context_count"),
            ):
                group[name] = group[total] / group[count] if group[count] else None
        return {
            "from": from_ts,
            "to": to_ts,
            "origin": origin or "all",
            "filters": dict(filters or {}),
            "coverage": self._coverage(spans),
            "interpretation": "observational_correlation_only",
            "dimensions": sorted(
                groups.values(), key=lambda item: (-item["runs"], item["backend"], item["model"])
            ),
        }

    # -- provider metrics -------------------------------------------------------

    def metric_summary(
        self,
        *,
        from_ts: float,
        to_ts: float,
        origin: str | None = "mux_owned",
        filters: Filters | None = None,
    ) -> dict[str, Any]:
        """Aggregated provider self-reports beside the ledger's own counts.

        Provider metrics carry no call identity, so the comparison is per run and
        per tool name: `codex.tool.call` summed for a run against the canonical
        calls the ledger holds for it. Agreement is evidence the ledger is
        complete; disagreement names the run to look at.
        """

        origin_clause = "" if origin is None else " AND origin=?"
        origin_args: tuple[Any, ...] = () if origin is None else (origin,)
        filter_clause, filter_args = _where(filters)
        metrics: dict[tuple[str, str, str], dict[str, Any]] = {}
        for row in self._query_all(
            "SELECT backend,COALESCE(harness_version,'unknown') harness_version,metric_name,"
            "kind,COUNT(*) points,SUM(COALESCE(count,0)) count,SUM(COALESCE(sum,0)) total,"
            "MIN(min) low,MAX(max) high FROM telemetry_provider_metrics "
            "WHERE observed_at>=? AND observed_at<?"
            f"{origin_clause}{filter_clause} GROUP BY backend,harness_version,metric_name,kind",
            (from_ts, to_ts, *origin_args, *filter_args),
            from_ts=from_ts,
            to_ts=to_ts,
        ):
            key = (str(row["backend"]), str(row["harness_version"]), str(row["metric_name"]))
            entry = metrics.setdefault(
                key,
                {
                    "backend": key[0],
                    "harness_version": key[1],
                    "metric": key[2],
                    "kind": row["kind"],
                    "points": 0,
                    "count": 0,
                    "total": 0.0,
                    "min": None,
                    "max": None,
                },
            )
            entry["points"] += int(row["points"] or 0)
            entry["count"] += int(row["count"] or 0)
            entry["total"] += float(row["total"] or 0)
            for name, value, pick in (("min", row["low"], min), ("max", row["high"], max)):
                if value is not None:
                    entry[name] = value if entry[name] is None else pick(entry[name], value)
        # Provider-reported tool calls per run against canonical calls per run.
        reported: dict[str, float] = {}
        for row in self._query_all(
            "SELECT run_id,SUM(COALESCE(sum,0)) reported FROM telemetry_provider_metrics "
            "WHERE metric_name='codex.tool.call' AND kind='sum' "
            "AND observed_at>=? AND observed_at<?"
            f"{origin_clause}{filter_clause} GROUP BY run_id",
            (from_ts, to_ts, *origin_args, *filter_args),
            from_ts=from_ts,
            to_ts=to_ts,
        ):
            reported[str(row["run_id"])] = reported.get(str(row["run_id"]), 0.0) + float(
                row["reported"] or 0
            )
        agreement: dict[str, Any] = {
            "runs": 0,
            "agree": 0,
            "ledger_more": 0,
            "provider_more": 0,
            "examples": [],
        }
        if reported:
            placeholders = ",".join("?" for _ in reported)
            canonical: dict[str, int] = {}
            for row in self._query_all(
                f"SELECT run_id,COUNT(*) calls FROM telemetry_tool_calls "
                f"WHERE run_id IN ({placeholders}) GROUP BY run_id",
                tuple(reported),
            ):
                canonical[str(row["run_id"])] = canonical.get(str(row["run_id"]), 0) + int(
                    row["calls"]
                )
            for run_id, provider_count in sorted(reported.items()):
                ledger_count = canonical.get(run_id, 0)
                agreement["runs"] += 1
                if ledger_count == int(provider_count):
                    agreement["agree"] += 1
                    continue
                verdict = "ledger_more" if ledger_count > provider_count else "provider_more"
                agreement[verdict] += 1
                if len(agreement["examples"]) < 20:
                    agreement["examples"].append(
                        {
                            "run_id": run_id,
                            "provider_reported": int(provider_count),
                            "ledger": ledger_count,
                            "verdict": verdict,
                        }
                    )
        return {
            "from": from_ts,
            "to": to_ts,
            "origin": origin or "all",
            "filters": dict(filters or {}),
            "metrics": sorted(metrics.values(), key=lambda item: (item["backend"], item["metric"])),
            "tool_call_agreement": agreement,
            "interpretation": "provider_self_report_beside_canonical_entities",
        }

    # -- findings, reviews, cohorts ---------------------------------------------

    def finding_reviews(self) -> dict[str, dict[str, Any]]:
        rows = self._catalog.execute(
            "SELECT finding_key,kind,verdict,note,reviewed_at FROM finding_reviews"
        ).fetchall()
        return {str(row["finding_key"]): dict(row) for row in rows}

    def review_finding(
        self,
        *,
        finding_key: str,
        kind: str,
        verdict: str,
        note: str | None,
        now: float | None = None,
    ) -> dict[str, Any]:
        if verdict not in FINDING_VERDICTS:
            raise ValueError(f"verdict must be one of {', '.join(FINDING_VERDICTS)}")
        if not finding_key or len(finding_key) != 64:
            raise ValueError("finding_key must be the finding's own key")
        reviewed_at = time.time() if now is None else now
        with self._catalog:
            self._catalog.execute(
                "INSERT INTO finding_reviews(finding_key,kind,verdict,note,reviewed_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(finding_key) DO UPDATE SET kind=excluded.kind,"
                "verdict=excluded.verdict,note=excluded.note,reviewed_at=excluded.reviewed_at",
                (finding_key, kind, verdict, (note or "")[:2000] or None, reviewed_at),
            )
        return {
            "finding_key": finding_key,
            "kind": kind,
            "verdict": verdict,
            "note": note,
            "reviewed_at": reviewed_at,
        }

    def _repeated_call_findings(
        self, from_ts: float, to_ts: float, origin: str | None, filters: Filters | None
    ) -> list[dict[str, Any]]:
        """Identical calls (same tool, same executed or requested input) repeated in one run.

        Read from the entities rather than a rollup because the key is a content
        hash; bounded by the window, and the window is bounded by the caller.
        """

        origin_clause = "" if origin is None else " AND origin=?"
        origin_args: tuple[Any, ...] = () if origin is None else (origin,)
        usable = {
            column: value
            for column, value in (filters or {}).items()
            if column in TOOL_FILTER_COLUMNS
        }
        filter_clause, filter_args = _where(usable)
        by_tool: dict[tuple[str, str, str], dict[str, Any]] = {}
        for row in self._query_all(
            "SELECT backend,COALESCE(model,'unknown') model,raw_name,run_id,"
            "COALESCE(executed_input_sha256,input_sha256) input_hash,COUNT(*) repeats "
            "FROM telemetry_tool_calls WHERE started_at>=? AND started_at<? "
            "AND COALESCE(executed_input_sha256,input_sha256) IS NOT NULL"
            f"{origin_clause}{filter_clause} GROUP BY backend,model,raw_name,run_id,input_hash "
            "HAVING COUNT(*)>=3",
            (from_ts, to_ts, *origin_args, *filter_args),
            from_ts=from_ts,
            to_ts=to_ts,
        ):
            key = (str(row["backend"]), str(row["model"]), str(row["raw_name"]))
            entry = by_tool.setdefault(
                key,
                {"runs": set(), "sequences": 0, "max_repeats": 0, "repeated_calls": 0},
            )
            entry["runs"].add(str(row["run_id"]))
            entry["sequences"] += 1
            entry["max_repeats"] = max(entry["max_repeats"], int(row["repeats"]))
            entry["repeated_calls"] += int(row["repeats"])
        findings: list[dict[str, Any]] = []
        for (backend, model, raw_name), entry in sorted(by_tool.items()):
            identity = {"backend": backend, "model": model, "raw_name": raw_name}
            findings.append(
                {
                    "kind": "repeated_identical_calls",
                    "finding_key": finding_key("repeated_identical_calls", identity),
                    "tool": identity,
                    "evidence": {
                        "runs": len(entry["runs"]),
                        "repeated_sequences": entry["sequences"],
                        "max_repeats": entry["max_repeats"],
                        "repeated_calls": entry["repeated_calls"],
                    },
                    "coverage": 1.0,
                    "confidence": "descriptive",
                    "suggestion": (
                        "The same tool ran the same input three or more times in one run; "
                        "look for a loop that is not reading its own result."
                    ),
                }
            )
        return findings

    def inefficiency_findings(
        self,
        *,
        from_ts: float,
        to_ts: float,
        origin: str | None = "mux_owned",
        filters: Filters | None = None,
        include_reviewed: bool = True,
    ) -> dict[str, Any]:
        """Deterministic candidates for operator review, never adaptive actions."""

        summary = self.tool_summary(from_ts=from_ts, to_ts=to_ts, origin=origin, filters=filters)
        findings: list[dict[str, Any]] = []
        for group in summary["groups"]:
            calls = int(group["calls"])
            statuses = group["statuses"]
            failed = int(statuses.get("failed", 0))
            denied = int(statuses.get("denied", 0))
            abandoned = int(statuses.get("abandoned", 0))
            interrupted = int(statuses.get("interrupted", 0))
            completed = sum(
                int(statuses.get(status, 0))
                for status in ("succeeded", "failed", "denied", "interrupted", "abandoned")
            )
            coverage = completed / calls if calls else 0.0
            identity = {
                key: group[key]
                for key in (
                    "backend",
                    "model",
                    "project_id",
                    "invocation_layer",
                    "raw_name",
                    "family",
                    "operation",
                    "transport",
                )
            }

            def add(kind: str, evidence: dict[str, Any], confidence: str, suggestion: str,
                    *, cov: float = coverage, ident: dict[str, Any] = identity) -> None:
                findings.append(
                    {
                        "kind": kind,
                        "finding_key": finding_key(kind, ident),
                        "tool": ident,
                        "evidence": evidence,
                        "coverage": cov,
                        "confidence": confidence,
                        "suggestion": suggestion,
                    }
                )

            rate_findings = (
                ("high_failure_rate", failed, "failed",
                 "Inspect recurring errors and tool arguments before changing configuration."),
                ("high_denial_rate", denied, "denied",
                 "Review permission rules or repeated unsupported requests."),
                ("high_abandonment_rate", abandoned, "abandoned",
                 "Calls left open when their run ended; look for hangs or killed sessions."),
                ("high_interruption_rate", interrupted, "interrupted",
                 "Operators are cutting this tool off; check whether it runs too long."),
            )
            for kind, count, label, suggestion in rate_findings:
                if completed >= 5 and count / completed >= 0.2:
                    add(
                        kind,
                        {label: count, "completed": completed},
                        "high" if coverage >= 0.9 else "limited",
                        suggestion,
                    )
            if group["operation"] == "wait" and calls >= 10:
                add(
                    "frequent_polling",
                    {"calls": calls},
                    "descriptive",
                    "Prefer a bounded blocking wait or event-driven completion when available.",
                )
            duration_count = int(group["duration_count"])
            average_duration = group["average_duration_ms"]
            if duration_count >= 5 and average_duration is not None and average_duration >= 30_000:
                add(
                    "slow_tool",
                    {"average_duration_ms": average_duration, "measured_calls": duration_count},
                    "high" if duration_count / calls >= 0.9 else "limited",
                    "Inspect this tool's slow calls and separate expected waits from execution.",
                    cov=duration_count / calls if calls else 0.0,
                )
            if calls >= 5 and int(group["output_bytes"]) / calls >= 100_000:
                add(
                    "large_results",
                    {"average_output_bytes": int(group["output_bytes"]) / calls, "calls": calls},
                    "descriptive",
                    "Request narrower output or pagination where the tool supports it.",
                )
            wait_count = int(group["approval_wait_count"])
            average_wait = group["average_approval_wait_ms"]
            if wait_count >= 3 and average_wait is not None and average_wait >= 60_000:
                add(
                    "excessive_approval_wait",
                    {"average_approval_wait_ms": average_wait, "measured_waits": wait_count},
                    "high" if wait_count / calls >= 0.5 else "limited",
                    "Calls sat at an approval for over a minute on average; consider an "
                    "approval rule for this tool or a faster route to the operator.",
                    cov=wait_count / calls if calls else 0.0,
                )
        findings.extend(self._repeated_call_findings(from_ts, to_ts, origin, filters))
        reviews = self.finding_reviews()
        for finding in findings:
            finding["review"] = reviews.get(finding["finding_key"])
        visible = (
            findings
            if include_reviewed
            else [item for item in findings if not item["review"]]
        )
        return {
            "from": from_ts,
            "to": to_ts,
            "origin": origin or "all",
            "filters": dict(filters or {}),
            "coverage": summary["coverage"],
            "interpretation": "deterministic_candidates_not_causal_claims",
            "findings": visible,
            "reviewed": sum(1 for item in findings if item["review"]),
            "adaptive_changes": {
                "offered": 0,
                "policy": (
                    "A configuration change may be offered only for a finding the operator "
                    "marked useful, with a comparison window and a rollback rule; none is "
                    "generated automatically."
                ),
            },
            "collection": {
                "matching_calls": summary["matching_calls"],
                "duration": summary["duration_coverage"],
                "approval_wait": summary["approval_wait"],
            },
        }

    def compare_cohorts(
        self,
        *,
        from_ts: float,
        to_ts: float,
        split: str,
        origin: str | None = "mux_owned",
        filters: Filters | None = None,
    ) -> dict[str, Any]:
        """Skill activation and verification outcomes across explicit, comparable cohorts.

        The cohorts are the values of one dimension (`split`) with every other
        dimension either fixed by a filter or reported as mixed. Two cohorts are
        called comparable only when the dimensions they are *not* split by are the
        same, which is what stops a model comparison from quietly being a project
        comparison. Every figure names its denominator; none is a ranking.
        """

        if split not in FILTER_COLUMNS:
            raise ValueError(f"split must be one of {', '.join(FILTER_COLUMNS)}")
        fixed = {
            column: value for column, value in (filters or {}).items() if column != split
        }
        workload = self.workload_summary(
            from_ts=from_ts, to_ts=to_ts, origin=origin, filters=fixed
        )
        skills = self.skill_summary(from_ts=from_ts, to_ts=to_ts, origin=origin, filters=fixed)
        verifications = self.verification_summary(
            from_ts=from_ts, to_ts=to_ts, origin=origin, filters=fixed
        )
        cohorts: dict[str, dict[str, Any]] = {}
        others = [column for column in FILTER_COLUMNS if column != split]

        def cohort(value: str) -> dict[str, Any]:
            return cohorts.setdefault(
                value,
                {
                    "cohort": value,
                    "runs": 0,
                    "completed_turns": 0,
                    "tool_calls": 0,
                    "completed_tool_calls": 0,
                    "failed_tool_calls": 0,
                    "verifications": 0,
                    "successful_verifications": 0,
                    "skill_activations": 0,
                    "other_dimensions": {column: set() for column in others},
                },
            )

        for row in workload["dimensions"]:
            entry = cohort(str(row[split]))
            entry["runs"] += int(row["runs"])
            entry["completed_turns"] += int(row["completed_turns"])
            entry["tool_calls"] += int(row["model_tool_calls"]) + int(row["runtime_tool_calls"])
            entry["completed_tool_calls"] += int(row["completed_tool_calls"])
            entry["failed_tool_calls"] += int(row["failed_tool_calls"])
            entry["verifications"] += int(row["verifications"])
            entry["successful_verifications"] += int(row["successful_verifications"])
            for column in others:
                entry["other_dimensions"][column].add(str(row[column]))
        for row in skills["groups"]:
            cohort(str(row[split]))["skill_activations"] += int(row["invocations"])
        del verifications  # the per-cohort figures above already carry the denominators
        result = []
        for entry in cohorts.values():
            dimensions = {
                column: sorted(values) for column, values in entry["other_dimensions"].items()
            }
            result.append(
                {
                    **{key: value for key, value in entry.items() if key != "other_dimensions"},
                    "other_dimensions": dimensions,
                    "tool_failure_rate": (
                        entry["failed_tool_calls"] / entry["completed_tool_calls"]
                        if entry["completed_tool_calls"]
                        else None
                    ),
                    "verification_success_rate": (
                        entry["successful_verifications"] / entry["verifications"]
                        if entry["verifications"]
                        else None
                    ),
                    "skill_activations_per_run": (
                        entry["skill_activations"] / entry["runs"] if entry["runs"] else None
                    ),
                }
            )
        # Comparable only when every non-split dimension is a single shared value.
        shared = all(
            len({tuple(item["other_dimensions"][column]) for item in result}) <= 1
            and all(len(item["other_dimensions"][column]) <= 1 for item in result)
            for column in others
        )
        return {
            "from": from_ts,
            "to": to_ts,
            "origin": origin or "all",
            "split": split,
            "fixed": fixed,
            "comparable": bool(result) and shared,
            "why_not_comparable": (
                None
                if shared
                else "cohorts differ on a dimension the split does not name; fix it with a filter"
            ),
            "interpretation": "observational_within_cohort_not_a_ranking",
            "cohorts": sorted(result, key=lambda item: (-item["runs"], item["cohort"])),
        }

    # -- quality ----------------------------------------------------------------

    def quality_summary(
        self,
        *,
        from_ts: float,
        to_ts: float,
        origin: str | None = "mux_owned",
        filters: Filters | None = None,
    ) -> dict[str, Any]:
        """Field completeness and lifecycle balance, never collapsed into one score."""

        fields = (
            "calls",
            "with_request",
            "with_result",
            "with_provider_result",
            "with_duration",
            "with_input_hash",
            "with_executed_input_hash",
            "with_output_hash",
            "with_output_size",
            "with_harness_version",
            "with_approval_wait",
            "truncated_outputs",
            "runtime_parent_unavailable",
            "other_family",
        )
        totals = {field: 0 for field in fields}
        backends: dict[str, dict[str, int]] = {}
        versions: dict[tuple[str, str], dict[str, int]] = {}
        filter_clause, filter_args = _where(filters)
        origin_clause = "" if origin is None else " AND origin=?"
        origin_args: tuple[Any, ...] = () if origin is None else (origin,)
        sql = (
            "SELECT backend,COALESCE(harness_version,'unknown') harness_version,"
            "COUNT(*) calls,SUM(request_source IS NOT NULL) with_request,"
            "SUM(result_source IS NOT NULL) with_result,"
            "SUM(result_source IN ('otel','provider_otel')) with_provider_result,"
            "SUM(duration_ms IS NOT NULL) with_duration,"
            "SUM(COALESCE(executed_input_sha256,input_sha256) IS NOT NULL) with_input_hash,"
            "SUM(executed_input_sha256 IS NOT NULL) with_executed_input_hash,"
            "SUM(output_sha256 IS NOT NULL) with_output_hash,"
            "SUM(output_bytes IS NOT NULL) with_output_size,"
            "SUM(harness_version IS NOT NULL) with_harness_version,"
            "SUM(approval_wait_ms IS NOT NULL) with_approval_wait,"
            "SUM(COALESCE(output_truncated,0)=1) truncated_outputs,"
            "SUM(invocation_layer='runtime' AND parent_status='provider_unavailable') "
            "runtime_parent_unavailable,SUM(family='other') other_family "
            "FROM telemetry_tool_calls WHERE started_at>=? AND started_at<?"
            f"{origin_clause}{filter_clause} GROUP BY backend,harness_version"
        )
        for row in self._query_all(
            sql, (from_ts, to_ts, *origin_args, *filter_args), from_ts=from_ts, to_ts=to_ts
        ):
            backend = str(row["backend"])
            target = backends.setdefault(backend, {field: 0 for field in fields})
            version = versions.setdefault(
                (backend, str(row["harness_version"])), {field: 0 for field in fields}
            )
            for field in fields:
                value = int(row[field] or 0)
                totals[field] += value
                target[field] += value
                version[field] += value
        runs = {"runs": 0, "declared_start": 0, "first_evidence_start": 0, "ended": 0}
        for row in self._query_all(
            "SELECT COUNT(*) runs,SUM(started_at_source='declared') declared,"
            "SUM(COALESCE(started_at_source,'unknown')!='declared') estimated,"
            "SUM(ended_at IS NOT NULL) ended FROM telemetry_runs "
            f"WHERE started_at>=? AND started_at<?{origin_clause}{filter_clause}",
            (from_ts, to_ts, *origin_args, *filter_args),
            from_ts=from_ts,
            to_ts=to_ts,
        ):
            runs["runs"] += int(row["runs"] or 0)
            runs["declared_start"] += int(row["declared"] or 0)
            runs["first_evidence_start"] += int(row["estimated"] or 0)
            runs["ended"] += int(row["ended"] or 0)
        return {
            "from": from_ts,
            "to": to_ts,
            "origin": origin or "all",
            "filters": dict(filters or {}),
            "totals": totals,
            "backends": [
                {"backend": backend, **values} for backend, values in sorted(backends.items())
            ],
            "versions": [
                {"backend": backend, "harness_version": version, **values}
                for (backend, version), values in sorted(versions.items())
            ],
            "runs": runs,
            "capabilities": provider_capabilities(),
            "parsers": self.parser_signatures(),
            "reconciliation": self.native_reconciliation_status(),
        }

    def parser_signatures(self) -> list[dict[str, Any]]:
        """Every provider event name seen per harness version, and whether it is understood."""

        rows = self._catalog.execute(
            "SELECT backend,harness_version,parser_version,event_name,recognized,occurrences,"
            "first_seen_at,last_seen_at FROM parser_signatures "
            "ORDER BY backend,harness_version,recognized,event_name"
        ).fetchall()
        return [dict(row) for row in rows]

    if TYPE_CHECKING:

        def native_reconciliation_status(self) -> dict[str, Any]: ...

        def native_reconciliation_for(self, run_id: str) -> dict[str, Any] | None: ...

    # -- shadow comparison ------------------------------------------------------

    def shadow_comparison(
        self, database: Path | str, *, from_ts: float, to_ts: float, limit: int = 50
    ) -> dict[str, Any]:
        """Legacy `tool_events` against canonical calls, per run and tool, classified.

        The legacy metric counted `tool_use` rows per run and raw tool name; the
        canonical ledger counts entities. Where they differ the difference has a
        reason, and the reason is what this reports: the ledger merged a request and
        result the legacy table held as two rows, the ledger holds a native-only call
        the legacy parser never saw, or the legacy table has rows the ledger has not
        imported yet. Exact totals, a bounded list of examples.
        """

        source = sqlite3.connect(f"file:{Path(database).as_posix()}?mode=ro", uri=True)
        source.row_factory = sqlite3.Row
        try:
            legacy_rows = source.execute(
                "SELECT t.agent_run_id run_id,t.raw_tool,"
                "SUM(t.kind='tool_use') uses,SUM(t.kind='tool_result') results "
                "FROM tool_events t JOIN history h ON h.id=t.agent_run_id "
                "WHERE h.spawned_at>=? AND h.spawned_at<? "
                "GROUP BY t.agent_run_id,t.raw_tool",
                (from_ts, to_ts),
            ).fetchall()
        finally:
            source.close()
        legacy: dict[tuple[str, str], dict[str, int]] = {
            (str(row["run_id"]), str(row["raw_tool"])): {
                "uses": int(row["uses"] or 0),
                "results": int(row["results"] or 0),
            }
            for row in legacy_rows
        }
        canonical: dict[tuple[str, str], dict[str, int]] = {}
        run_ids = {key[0] for key in legacy}
        for row in self._query_all(
            "SELECT run_id,raw_name,COUNT(*) calls,"
            "SUM(request_source IS NOT NULL) with_request,"
            "SUM(result_source IN ('otel','provider_otel')) native "
            "FROM telemetry_tool_calls WHERE run_id IN (SELECT run_id FROM telemetry_runs "
            "WHERE started_at>=? AND started_at<?) GROUP BY run_id,raw_name",
            (from_ts, to_ts),
            from_ts=from_ts,
            to_ts=to_ts,
        ):
            key = (str(row["run_id"]), str(row["raw_name"]))
            entry = canonical.setdefault(key, {"calls": 0, "with_request": 0, "native": 0})
            entry["calls"] += int(row["calls"])
            entry["with_request"] += int(row["with_request"] or 0)
            entry["native"] += int(row["native"] or 0)
            run_ids.add(key[0])
        classes = {
            "agree": 0,
            "canonical_native_only": 0,
            "canonical_more": 0,
            "legacy_more_not_yet_imported": 0,
            "legacy_only": 0,
            "canonical_only": 0,
        }
        examples: list[dict[str, Any]] = []
        for key in sorted(set(legacy) | set(canonical)):
            left = legacy.get(key)
            right = canonical.get(key)
            if left is None:
                verdict = "canonical_native_only" if right and right["native"] else "canonical_only"
            elif right is None:
                verdict = "legacy_only"
            elif left["uses"] == right["with_request"]:
                verdict = "agree"
            elif right["with_request"] > left["uses"]:
                verdict = "canonical_native_only" if right["native"] else "canonical_more"
            else:
                verdict = "legacy_more_not_yet_imported"
            classes[verdict] += 1
            if verdict != "agree" and len(examples) < max(1, min(limit, 500)):
                examples.append(
                    {
                        "run_id": key[0],
                        "raw_tool": key[1],
                        "legacy": left,
                        "canonical": right,
                        "verdict": verdict,
                    }
                )
        return {
            "from": from_ts,
            "to": to_ts,
            "runs": len(run_ids),
            "pairs": sum(classes.values()),
            "classes": classes,
            "examples": examples,
            "interpretation": (
                "agree: legacy tool_use rows equal canonical requests; canonical_native_only: "
                "the ledger holds a call only native telemetry reported; canonical_more: the "
                "ledger holds a call the legacy parser missed; legacy_more_not_yet_imported: "
                "legacy rows the ledger has not imported or merged yet; legacy_only / "
                "canonical_only: the pair exists on one side"
            ),
        }

    # -- export -----------------------------------------------------------------

    def export_page(
        self,
        *,
        kind: str,
        from_ts: float,
        to_ts: float,
        origin: str | None = "mux_owned",
        filters: Filters | None = None,
        cursor: str | None = None,
        limit: int = 1000,
    ) -> dict[str, Any]:
        """One ascending page of canonical rows with an opaque continuation cursor.

        Rows are ordered by (time, primary key) within a segment and segments are
        walked oldest first, so a consumer pulling every page sees each row once.
        Every row keeps its evidence identifiers and source locator, which is the
        provenance an export is required to carry.
        """

        if kind not in EXPORT_KINDS:
            raise ValueError(f"unknown export kind {kind!r}")
        table, time_column, key_column = EXPORT_KINDS[kind]
        bounded = max(1, min(int(limit), 5000))
        origin_clause = "" if origin is None else " AND origin=?"
        origin_args: tuple[Any, ...] = () if origin is None else (origin,)
        usable = {
            column: value
            for column, value in (filters or {}).items()
            if column in FILTER_COLUMNS and not (kind == "runs" and column == "model")
        }
        filter_clause, filter_args = _where(usable)
        decoded = self._decode_cursor(cursor, 3)
        periods = self._periods(from_ts, to_ts)
        start_index = 0
        after: tuple[float, str] | None = None
        if decoded is not None:
            period = str(decoded[0])
            if period in periods:
                start_index = periods.index(period)
            after = (float(decoded[1]), str(decoded[2]))
        items: list[dict[str, Any]] = []
        # The continuation names the segment the last *emitted* row came from,
        # which is not necessarily the segment that proved there was more.
        last_period: str | None = None
        next_cursor: str | None = None
        for index in range(start_index, len(periods)):
            period = periods[index]
            clauses = [f"{time_column}>=?", f"{time_column}<?"]
            args: list[Any] = [from_ts, to_ts, *origin_args, *filter_args]
            if after is not None and index == start_index:
                clauses.append(f"({time_column}>? OR ({time_column}=? AND {key_column}>?))")
                args.extend((after[0], after[0], after[1]))
            where = " AND ".join(clauses) + origin_clause + filter_clause
            remaining = bounded - len(items) + 1
            rows = [
                dict(row)
                for row in self._segment(period).execute(
                    f"SELECT * FROM {table} WHERE {where} "
                    f"ORDER BY {time_column},{key_column} LIMIT ?",
                    (*args, remaining),
                ).fetchall()
            ]
            if len(rows) >= remaining:
                taken = rows[: remaining - 1]
                if taken:
                    items.extend(taken)
                    last_period = period
                last = items[-1]
                next_cursor = self._encode_cursor(
                    last_period, float(last[time_column]), str(last[key_column])
                )
                break
            if rows:
                items.extend(rows)
                last_period = period
        return {
            "kind": kind,
            "from": from_ts,
            "to": to_ts,
            "origin": origin or "all",
            "filters": dict(usable),
            "items": items,
            "next_cursor": next_cursor,
        }
