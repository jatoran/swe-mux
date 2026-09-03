"""Exact analytics over the canonical telemetry ledger.

Every aggregate here covers the whole requested window: a closed, clean UTC day is
answered from its rollup row and everything else from the canonical entities, and the
two are summed into one answer. Detail pages are finite; the counts beside them are
not. No query reads a displayed slice to compute a total.
"""

from __future__ import annotations

import base64
import json
import sqlite3
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from .telemetry_schema import canonical_json, period_of

#: Columns a caller may filter aggregates by. Every one exists on the canonical
#: entity tables and on both rollup tables, so a filter costs nothing in exactness.
FILTER_COLUMNS = ("project_id", "backend", "model")
TOOL_FILTER_COLUMNS = (*FILTER_COLUMNS, "invocation_layer", "family", "status")

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
    "evidence": ("telemetry_evidence", "observed_at", "evidence_id"),
}

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

    def _spans(self, from_ts: float, to_ts: float) -> list[Span]:
        """Split a window into rolled-up days and raw spans, never crossing a segment.

        Consecutive raw days inside one month collapse into a single span, so a
        window of dirty days costs one query per month rather than one per day.
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
        spans: list[Span] = []
        day = datetime.fromtimestamp(from_ts, tz=UTC).date()
        last_day = datetime.fromtimestamp(max(from_ts, to_ts - 0.000001), tz=UTC).date()
        while day <= last_day:
            day_text = day.isoformat()
            day_start = datetime.combine(day, datetime.min.time(), tzinfo=UTC).timestamp()
            day_end = day_start + 86400
            query_start = max(from_ts, day_start)
            query_end = min(to_ts, day_end)
            full_day = query_start == day_start and query_end == day_end
            if full_day and day_text in rolled and day_text not in dirty:
                spans.append(("rollup", day_start, day_end, day_text))
            elif day_text[:7] in available:
                previous = spans[-1] if spans else None
                if (
                    previous is not None
                    and previous[0] == "raw"
                    and previous[2] == query_start
                    and period_of(previous[1]) == day_text[:7]
                ):
                    spans[-1] = ("raw", previous[1], query_end, None)
                else:
                    spans.append(("raw", query_start, query_end, None))
            day += timedelta(days=1)
        return spans

    # -- tool calls -------------------------------------------------------------

    def tool_calls(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        rows = self._query_all("SELECT * FROM telemetry_tool_calls")
        rows.sort(key=lambda row: float(row["started_at"] or row["finished_at"] or 0), reverse=True)
        return rows if limit is None else rows[: max(1, limit)]

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
    ) -> dict[str, Any]:
        bounded_limit = max(1, min(int(limit), 500))
        clauses = ["started_at>=?", "started_at<?"]
        args: list[Any] = [from_ts, to_ts]
        filters = {
            "project_id": project_id,
            "backend": backend,
            "model": model,
            "origin": origin,
            "invocation_layer": invocation_layer,
            "family": family,
            "status": status,
        }
        for column, value in filters.items():
            if value is not None:
                clauses.append(f"{column}=?")
                args.append(value)
        count_where = " AND ".join(clauses)
        count_args = tuple(args)
        decoded = self._decode_cursor(cursor, 2)
        if decoded is not None:
            clauses.append("(started_at<? OR (started_at=? AND tool_call_id<?))")
            started = float(decoded[0])
            args.extend((started, started, str(decoded[1])))
        where = " AND ".join(clauses)
        candidates: list[dict[str, Any]] = []
        matching = 0
        for period in self._periods(from_ts, to_ts):
            connection = self._segment(period)
            matching += int(
                connection.execute(
                    f"SELECT COUNT(*) FROM telemetry_tool_calls WHERE {count_where}",
                    count_args,
                ).fetchone()[0]
            )
            candidates.extend(
                dict(row)
                for row in connection.execute(
                    f"SELECT * FROM telemetry_tool_calls WHERE {where} "
                    "ORDER BY started_at DESC,tool_call_id DESC LIMIT ?",
                    (*args, bounded_limit + 1),
                ).fetchall()
            )
        candidates.sort(
            key=lambda row: (float(row["started_at"]), str(row["tool_call_id"])),
            reverse=True,
        )
        page = candidates[:bounded_limit]
        has_more = len(candidates) > bounded_limit
        next_cursor = None
        if has_more and page:
            last = page[-1]
            next_cursor = self._encode_cursor(float(last["started_at"]), str(last["tool_call_id"]))
        return {
            "from": from_ts,
            "to": to_ts,
            "matching_calls": matching,
            "items": page,
            "next_cursor": next_cursor,
        }

    def tool_audit(self, tool_call_id: str) -> dict[str, Any] | None:
        location = self._catalog.execute(
            "SELECT period FROM entity_locations WHERE entity_kind='tool_call' AND entity_id=?",
            (tool_call_id,),
        ).fetchone()
        if location is None:
            return None
        connection = self._segment(str(location["period"]))
        call = connection.execute(
            "SELECT * FROM telemetry_tool_calls WHERE tool_call_id=?", (tool_call_id,)
        ).fetchone()
        if call is None:
            return None
        links = connection.execute(
            "SELECT evidence_id,contribution,precedence_rank,conflict "
            "FROM telemetry_entity_evidence WHERE entity_kind='tool_call' AND entity_id=? "
            "ORDER BY precedence_rank DESC,evidence_id",
            (tool_call_id,),
        ).fetchall()
        wanted = {str(link["evidence_id"]) for link in links}
        evidence: dict[str, dict[str, Any]] = {}
        if wanted:
            placeholders = ",".join("?" for _ in wanted)
            for period in self._periods():
                for row in self._segment(period).execute(
                    f"SELECT * FROM telemetry_evidence WHERE evidence_id IN ({placeholders})",
                    tuple(wanted),
                ).fetchall():
                    evidence[str(row["evidence_id"])] = dict(row)
        return {
            "call": dict(call),
            "evidence": [
                {
                    **dict(link),
                    "observation": evidence.get(str(link["evidence_id"])),
                }
                for link in links
            ],
        }

    def turns(self) -> list[dict[str, Any]]:
        return self._query_all("SELECT * FROM telemetry_turns ORDER BY started_at")

    def skills(self) -> list[dict[str, Any]]:
        return self._query_all("SELECT * FROM telemetry_skill_invocations ORDER BY activated_at")

    def verifications(self) -> list[dict[str, Any]]:
        return self._query_all("SELECT * FROM telemetry_verifications ORDER BY finished_at")

    def model_requests(self) -> list[dict[str, Any]]:
        return self._query_all("SELECT * FROM telemetry_model_requests ORDER BY finished_at")

    # -- summaries --------------------------------------------------------------

    def compaction_summary(
        self,
        *,
        from_ts: float,
        to_ts: float,
        origin: str | None = "mux_owned",
        filters: Filters | None = None,
    ) -> dict[str, Any]:
        groups: dict[tuple[str, ...], dict[str, Any]] = {}
        total = 0
        origin_clause = "" if origin is None else " AND origin=?"
        origin_args: tuple[Any, ...] = () if origin is None else (origin,)
        filter_clause, filter_args = _where(filters)
        sql = (
            "SELECT backend,COALESCE(model,'unknown') model,COALESCE(project_id,'') project_id,"
            "origin,COALESCE(trigger,'unknown') trigger,COUNT(*) count,SUM(success=0) failures,"
            "SUM(duration_ms IS NOT NULL) duration_count,SUM(COALESCE(duration_ms,0)) duration_ms,"
            "SUM(tokens_before IS NOT NULL AND tokens_after IS NOT NULL) token_count,"
            "SUM(CASE WHEN tokens_before IS NOT NULL AND tokens_after IS NOT NULL "
            "THEN tokens_before-tokens_after ELSE 0 END) tokens_reclaimed "
            "FROM telemetry_compactions WHERE observed_at>=? AND observed_at<?"
            f"{origin_clause}{filter_clause} GROUP BY backend,model,project_id,origin,trigger"
        )
        for row in self._query_all(
            sql,
            (from_ts, to_ts, *origin_args, *filter_args),
            from_ts=from_ts,
            to_ts=to_ts,
        ):
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
            for field in (
                "count",
                "failures",
                "duration_count",
                "token_count",
                "tokens_reclaimed",
            ):
                group[field] += int(row[field] or 0)
            group["duration_ms"] += float(row["duration_ms"] or 0)
        for group in groups.values():
            group["average_duration_ms"] = (
                group["duration_ms"] / group["duration_count"]
                if group["duration_count"]
                else None
            )
            group["average_tokens_reclaimed"] = (
                group["tokens_reclaimed"] / group["token_count"]
                if group["token_count"]
                else None
            )
        return {
            "from": from_ts,
            "to": to_ts,
            "origin": origin or "all",
            "filters": dict(filters or {}),
            "total": total,
            "groups": sorted(
                groups.values(), key=lambda item: (-item["count"], item["backend"])
            ),
        }

    def skill_summary(
        self,
        *,
        from_ts: float,
        to_ts: float,
        origin: str | None = "mux_owned",
        filters: Filters | None = None,
    ) -> dict[str, Any]:
        groups: dict[tuple[str, ...], dict[str, Any]] = {}
        matching = 0
        origin_clause = "" if origin is None else " AND origin=?"
        origin_args: tuple[Any, ...] = () if origin is None else (origin,)
        filter_clause, filter_args = _where(filters)
        sql = (
            "SELECT backend,COALESCE(model,'unknown') model,COALESCE(project_id,'') project_id,"
            "origin,skill_name,COALESCE(invocation_trigger,'unknown') invocation_trigger,"
            "COALESCE(skill_source,'unknown') skill_source,COALESCE(skill_scope,'unknown') "
            "skill_scope,SUM(occurrences) invocations FROM telemetry_skill_invocations "
            "WHERE activated_at>=? AND activated_at<?"
            f"{origin_clause}{filter_clause} GROUP BY backend,model,project_id,origin,skill_name,"
            "invocation_trigger,skill_source,skill_scope"
        )
        for row in self._query_all(
            sql,
            (from_ts, to_ts, *origin_args, *filter_args),
            from_ts=from_ts,
            to_ts=to_ts,
        ):
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
            "matching_invocations": matching,
            "groups": sorted(
                groups.values(),
                key=lambda item: (-item["invocations"], item["skill_name"]),
            ),
        }

    def _tool_summary_rows(
        self,
        from_ts: float,
        to_ts: float,
        origin: str | None,
        filters: Filters | None = None,
    ) -> Iterator[dict[str, Any]]:
        origin_clause = "" if origin is None else " AND origin=?"
        origin_args: tuple[Any, ...] = () if origin is None else (origin,)
        filter_clause, filter_args = _where(filters)
        raw_sql = (
            "SELECT backend,COALESCE(model,'unknown') model,COALESCE(project_id,'') project_id,"
            "origin,invocation_layer,family,operation,transport,raw_name,status,COUNT(*) count,"
            "SUM(duration_ms IS NOT NULL) duration_count,SUM(COALESCE(duration_ms,0)) duration_ms,"
            "SUM(COALESCE(input_bytes,0)) input_bytes,SUM(COALESCE(output_bytes,0)) output_bytes "
            "FROM telemetry_tool_calls WHERE started_at>=? AND started_at<?"
            f"{origin_clause}{filter_clause} "
            "GROUP BY backend,model,project_id,origin,invocation_layer,family,operation,"
            "transport,raw_name,status"
        )
        rollup_sql = (
            "SELECT backend,model,project_id,origin,invocation_layer,family,operation,"
            "transport,raw_name,status,calls count,duration_count,duration_ms,"
            f"input_bytes,output_bytes FROM tool_daily WHERE day=?{origin_clause}{filter_clause}"
        )
        for kind, start, end, day in self._spans(from_ts, to_ts):
            if kind == "rollup":
                rows = self._catalog.execute(
                    rollup_sql, (day, *origin_args, *filter_args)
                ).fetchall()
            else:
                rows = self._segment(period_of(start)).execute(
                    raw_sql, (start, end, *origin_args, *filter_args)
                ).fetchall()
            for row in rows:
                yield dict(row)

    def tool_summary(
        self,
        *,
        from_ts: float,
        to_ts: float,
        origin: str | None = "mux_owned",
        filters: Filters | None = None,
    ) -> dict[str, Any]:
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
        groups: dict[tuple[str, ...], dict[str, Any]] = {}
        matching = 0
        duration_count = 0
        duration_ms = 0.0
        input_bytes = 0
        output_bytes = 0
        for row in self._tool_summary_rows(from_ts, to_ts, origin, filters):
            count = int(row["count"])
            matching += count
            duration_count += int(row["duration_count"] or 0)
            duration_ms += float(row["duration_ms"] or 0)
            input_bytes += int(row["input_bytes"] or 0)
            output_bytes += int(row["output_bytes"] or 0)
            layer_key = "runtime_calls" if row["invocation_layer"] == "runtime" else "model_calls"
            totals[layer_key] += count
            status = str(row["status"])
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
                    "duration_count": 0,
                    "duration_ms": 0.0,
                    "input_bytes": 0,
                    "output_bytes": 0,
                },
            )
            group["calls"] += count
            group["statuses"][status] = group["statuses"].get(status, 0) + count
            group["duration_count"] += int(row["duration_count"] or 0)
            group["duration_ms"] += float(row["duration_ms"] or 0)
            group["input_bytes"] += int(row["input_bytes"] or 0)
            group["output_bytes"] += int(row["output_bytes"] or 0)
        for group in groups.values():
            group["average_duration_ms"] = (
                group["duration_ms"] / group["duration_count"]
                if group["duration_count"]
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
            "matching_calls": matching,
            "totals": totals,
            "duration_coverage": {
                "measured": duration_count,
                "completed": totals["completed"],
                "average_ms": duration_ms / duration_count if duration_count else None,
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
            {
                column: value
                for column, value in (filters or {}).items()
                if column != "model"
            }
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

    def workload_day_rows(self, day: str) -> list[dict[str, Any]]:
        """Exact per-group workload for one closed UTC day, for the rollup table."""

        start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC).timestamp()
        period = day[:7]
        known = self._catalog.execute(
            "SELECT 1 FROM ledger_segments WHERE period=?", (period,)
        ).fetchone()
        if known is None:
            return []
        groups: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for row in self._workload_raw_rows(
            self._segment(period), start, start + 86400, None, None
        ):
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
        for kind, start, end, day in self._spans(from_ts, to_ts):
            if kind == "rollup":
                for rolled in self._catalog.execute(
                    f"SELECT * FROM workload_daily WHERE day=?{origin_clause}{filter_clause}",
                    (day, *origin_args, *filter_args),
                ).fetchall():
                    self._workload_group(groups, dict(rolled))
                continue
            for row in self._workload_raw_rows(
                self._segment(period_of(start)), start, end, origin, filters
            ):
                self._workload_group(groups, row)
        for group in groups.values():
            group["average_wall_duration_s"] = (
                group["wall_duration_s"] / group["wall_duration_count"]
                if group["wall_duration_count"]
                else None
            )
            group["average_turn_duration_ms"] = (
                group["turn_duration_ms"] / group["turn_duration_count"]
                if group["turn_duration_count"]
                else None
            )
            group["average_tool_duration_ms"] = (
                group["tool_duration_ms"] / group["tool_duration_count"]
                if group["tool_duration_count"]
                else None
            )
            group["average_model_wait_ms"] = (
                group["model_wait_ms"] / group["model_wait_count"]
                if group["model_wait_count"]
                else None
            )
            group["average_final_context_pct"] = (
                group["final_context_sum"] / group["final_context_count"]
                if group["final_context_count"]
                else None
            )
            group["average_peak_context_pct"] = (
                group["peak_context_sum"] / group["peak_context_count"]
                if group["peak_context_count"]
                else None
            )
        return {
            "from": from_ts,
            "to": to_ts,
            "origin": origin or "all",
            "filters": dict(filters or {}),
            "interpretation": "observational_correlation_only",
            "dimensions": sorted(
                groups.values(), key=lambda item: (-item["runs"], item["backend"], item["model"])
            ),
        }

    # -- findings and quality ---------------------------------------------------

    def inefficiency_findings(
        self,
        *,
        from_ts: float,
        to_ts: float,
        origin: str | None = "mux_owned",
        filters: Filters | None = None,
    ) -> dict[str, Any]:
        """Deterministic candidates for operator review, never adaptive actions."""

        summary = self.tool_summary(
            from_ts=from_ts, to_ts=to_ts, origin=origin, filters=filters
        )
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
                    findings.append(
                        {
                            "kind": kind,
                            "tool": identity,
                            "evidence": {label: count, "completed": completed},
                            "coverage": coverage,
                            "confidence": "high" if coverage >= 0.9 else "limited",
                            "suggestion": suggestion,
                        }
                    )
            if group["operation"] == "wait" and calls >= 10:
                findings.append(
                    {
                        "kind": "frequent_polling",
                        "tool": identity,
                        "evidence": {"calls": calls},
                        "coverage": coverage,
                        "confidence": "descriptive",
                        "suggestion": (
                            "Prefer a bounded blocking wait or event-driven completion "
                            "when available."
                        ),
                    }
                )
            duration_count = int(group["duration_count"])
            average_duration = group["average_duration_ms"]
            if duration_count >= 5 and average_duration is not None and average_duration >= 30_000:
                findings.append(
                    {
                        "kind": "slow_tool",
                        "tool": identity,
                        "evidence": {
                            "average_duration_ms": average_duration,
                            "measured_calls": duration_count,
                        },
                        "coverage": duration_count / calls if calls else 0.0,
                        "confidence": "high" if duration_count / calls >= 0.9 else "limited",
                        "suggestion": (
                            "Inspect this tool's slow calls and separate expected waits "
                            "from execution."
                        ),
                    }
                )
            if calls >= 5 and int(group["output_bytes"]) / calls >= 100_000:
                findings.append(
                    {
                        "kind": "large_results",
                        "tool": identity,
                        "evidence": {
                            "average_output_bytes": int(group["output_bytes"]) / calls,
                            "calls": calls,
                        },
                        "coverage": coverage,
                        "confidence": "descriptive",
                        "suggestion": (
                            "Request narrower output or pagination where the tool supports it."
                        ),
                    }
                )
        return {
            "from": from_ts,
            "to": to_ts,
            "origin": origin or "all",
            "filters": dict(filters or {}),
            "interpretation": "deterministic_candidates_not_causal_claims",
            "findings": findings,
            "collection": {
                "matching_calls": summary["matching_calls"],
                "duration": summary["duration_coverage"],
            },
        }

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
            "truncated_outputs",
            "runtime_parent_unavailable",
            "other_family",
        )
        totals = {field: 0 for field in fields}
        backends: dict[str, dict[str, int]] = {}
        filter_clause, filter_args = _where(filters)
        sql = (
            "SELECT backend,COUNT(*) calls,SUM(request_source IS NOT NULL) with_request,"
            "SUM(result_source IS NOT NULL) with_result,"
            "SUM(result_source IN ('otel','provider_otel')) with_provider_result,"
            "SUM(duration_ms IS NOT NULL) with_duration,"
            "SUM(COALESCE(executed_input_sha256,input_sha256) IS NOT NULL) with_input_hash,"
            "SUM(executed_input_sha256 IS NOT NULL) with_executed_input_hash,"
            "SUM(output_sha256 IS NOT NULL) with_output_hash,"
            "SUM(output_bytes IS NOT NULL) with_output_size,"
            "SUM(harness_version IS NOT NULL) with_harness_version,"
            "SUM(COALESCE(output_truncated,0)=1) truncated_outputs,"
            "SUM(invocation_layer='runtime' AND parent_status='provider_unavailable') "
            "runtime_parent_unavailable,SUM(family='other') other_family "
            "FROM telemetry_tool_calls WHERE started_at>=? AND started_at<?"
            + ("" if origin is None else " AND origin=?")
            + filter_clause
            + " GROUP BY backend"
        )
        args = (from_ts, to_ts, *(() if origin is None else (origin,)), *filter_args)
        for row in self._query_all(
            sql, args, from_ts=from_ts, to_ts=to_ts
        ):
            backend = str(row["backend"])
            target = backends.setdefault(backend, {field: 0 for field in fields})
            for field in fields:
                value = int(row[field] or 0)
                totals[field] += value
                target[field] += value
        return {
            "from": from_ts,
            "to": to_ts,
            "origin": origin or "all",
            "filters": dict(filters or {}),
            "totals": totals,
            "backends": [
                {"backend": backend, **values}
                for backend, values in sorted(backends.items())
            ],
            "parsers": self.parser_signatures(),
        }

    def parser_signatures(self) -> list[dict[str, Any]]:
        """Every provider event name seen per harness version, and whether it is understood."""

        rows = self._catalog.execute(
            "SELECT backend,harness_version,parser_version,event_name,recognized,occurrences,"
            "first_seen_at,last_seen_at FROM parser_signatures "
            "ORDER BY backend,harness_version,recognized,event_name"
        ).fetchall()
        return [dict(row) for row in rows]

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
