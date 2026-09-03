"""Canonical, provenance-preserving agent activity telemetry.

The legacy operational telemetry tables store observer events as independent rows.
This ledger reduces those observations into run, turn, tool-call, skill, model
request, compaction, verification, and provider-metric entities while retaining a
content-free evidence trail. Detailed provider output remains in the provider
transcript or conversation store.

The storage core is synchronous on purpose: the daemon adapter
(`telemetry_service.CanonicalTelemetryService`) runs every call on one dedicated
executor, and migrations, corpus backfills, and deterministic tests use this same
reducer rather than an async twin. Schema and migrations live in
`telemetry_schema.py`, the legacy importers in `telemetry_imports.py`, direct native
reconciliation in `telemetry_reconcile.py`, and the query surface in
`telemetry_queries.py`; this module owns identity, precedence, and the write path.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import sqlite3
import time
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .harness import HARNESSES
from .models import MuxEvent
from .telemetry_imports import LegacyImportMixin
from .telemetry_queries import WORKLOAD_FIELDS, LedgerQueryMixin
from .telemetry_reconcile import NativeReconcileMixin
from .telemetry_schema import (
    LEDGER_SCHEMA_VERSION,
    canonical_json,
    classify_tool,
    content_metrics,
    day_of,
    digest,
    evidence_quality_for,
    expected_signature,
    hour_of,
    migrate_catalog,
    migrate_segment,
    period_of,
    schema_signature,
    source_rank,
    turn_entity_id,
)

__all__ = ["CanonicalTelemetryLedger", "LEDGER_SCHEMA_VERSION", "classify_tool"]

log = logging.getLogger(__name__)

_CAPTURED_EVENT_TYPES = frozenset(
    {
        "agent_run_started",
        "agent_run_ended",
        "approval_needed",
        "approval_resolved",
        "canonical_skill_invoked",
        "canonical_model_request",
        "canonical_tool_result",
        "canonical_tool_use",
        "context_compacted",
        "git_changed",
        "land_handed_back",
        "land_landed",
        "land_refused",
        "provider_metric",
        "session_crashed",
        "session_exited",
        "skill_invoked",
        "stalled",
        "subagent_activity",
        "tool_result",
        "tool_use",
        "turn_aborted",
        "turn_ended",
        "turn_started",
    }
)
_RUN_LIFECYCLE_EVENTS = frozenset(
    {"agent_run_started", "agent_run_ended", "session_exited", "session_crashed"}
)
_RUN_ENDING_EVENTS = frozenset({"agent_run_ended", "session_exited", "session_crashed"})
_NATIVE_SOURCES = frozenset({"otel", "provider_otel", "transcript"})
_UNMEASURED = "unknown"


class CanonicalTelemetryLedger(LegacyImportMixin, NativeReconcileMixin, LedgerQueryMixin):
    """Synchronous core for segmented canonical telemetry."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.segments_dir = root / "segments"
        self.segments_dir.mkdir(parents=True, exist_ok=True)
        # The service constructs the ledger during startup and performs every live
        # operation on its dedicated single-worker executor.
        self._catalog = sqlite3.connect(root / "catalog.sqlite3", check_same_thread=False)
        self._catalog.row_factory = sqlite3.Row
        self._migrations: dict[str, dict[str, Any]] = {
            "catalog": migrate_catalog(self._catalog)
        }
        self._segments: dict[str, sqlite3.Connection] = {}
        self._dirty_segments: set[str] = set()
        # Timestamps of entities a batch touched *besides* the events' own: the
        # start of a run or turn that ended later, a call abandoned by a run end.
        # Their day and hour are dirtied so the rollup they live in is rebuilt.
        self._extra_dirty_stamps: set[float] = set()
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        for connection in self._segments.values():
            connection.close()
        self._segments.clear()
        self._catalog.close()
        self._closed = True

    @staticmethod
    def dimensions_from_session(session: Any) -> dict[str, Any]:
        record = session.record
        transcript = getattr(session, "transcript_path", None)
        return {
            "session_id": str(record.id),
            "run_id": str(record.agent_run_id or record.id),
            "native_conversation_id": str(record.native_session_id or "") or None,
            "turn_id": str(record.active_turn_id or "") or None,
            "turn_ordinal": int(getattr(record, "turn_epoch", 0)),
            "agent_id": "root",
            "project_id": str(record.project_id or "") or None,
            "backend": str(record.backend),
            "model": str(record.model or "") or None,
            "input_tokens": int(getattr(record, "tokens_in", 0)),
            "output_tokens": int(getattr(record, "tokens_out", 0)),
            "cache_read_tokens": int(getattr(record, "tokens_cache_read", 0)),
            "cache_write_tokens": int(getattr(record, "tokens_cache_write", 0)),
            "cost_usd": float(getattr(record, "cost_usd", 0.0)),
            "final_context_pct": getattr(record, "context_pct", None),
            "peak_context_pct": getattr(record, "context_peak_pct", None),
            "measurement_source": getattr(record, "measurement_source", None),
            "origin": "mux_owned",
            "harness_version": None,
            "source_locator": str(transcript) if transcript else None,
        }

    # -- segments and schema ----------------------------------------------------

    def _segment(self, period: str) -> sqlite3.Connection:
        existing = self._segments.get(period)
        if existing is not None:
            return existing
        path = self.segments_dir / f"{period}.sqlite3"
        connection = sqlite3.connect(path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        outcome = migrate_segment(connection)
        if outcome["applied"]:
            log.info(
                "canonical telemetry segment %s migrated from schema %s to %s (%d steps)",
                period,
                outcome["found"],
                outcome["version"],
                len(outcome["applied"]),
            )
        self._migrations[period] = outcome
        self._catalog.execute(
            "INSERT OR IGNORE INTO ledger_segments(period,relative_path) VALUES(?,?)",
            (period, str(path.relative_to(self.root))),
        )
        self._catalog.commit()
        self._segments[period] = connection
        return connection

    def _periods(self, from_ts: float | None = None, to_ts: float | None = None) -> list[str]:
        clauses: list[str] = []
        args: list[Any] = []
        if from_ts is not None:
            clauses.append("last_observed_at>=?")
            args.append(from_ts)
        if to_ts is not None:
            clauses.append("first_observed_at<=?")
            args.append(to_ts)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._catalog.execute(
            f"SELECT period FROM ledger_segments{where} ORDER BY period", args
        ).fetchall()
        return [str(row["period"]) for row in rows]

    def schema_status(self) -> dict[str, Any]:
        """Version and structural signatures of every ledger file, for diagnostics.

        A signature that differs from the expected one names the file whose shape is
        not what this daemon's schema produces - the situation a redeploy against an
        older data directory creates when a migration is missing.
        """

        expected_catalog = expected_signature("catalog")
        expected_segment = expected_signature("segment")
        segments: dict[str, str] = {}
        for period in self._periods():
            segments[period] = schema_signature(self._segment(period))
        catalog = schema_signature(self._catalog)
        drift = [
            period for period, signature in segments.items() if signature != expected_segment
        ]
        if catalog != expected_catalog:
            drift.insert(0, "catalog")
        return {
            "version": LEDGER_SCHEMA_VERSION,
            "catalog_signature": catalog,
            "expected_catalog_signature": expected_catalog,
            "expected_segment_signature": expected_segment,
            "segment_signatures": segments,
            "drift": drift,
            "migrations": {
                name: {"found": item["found"], "applied": len(item["applied"])}
                for name, item in self._migrations.items()
                if item["applied"] or item["found"] != LEDGER_SCHEMA_VERSION
            },
        }

    def _entity_connection(
        self, entity_kind: str, entity_id: str, preferred_period: str
    ) -> sqlite3.Connection:
        row = self._catalog.execute(
            "SELECT period FROM entity_locations WHERE entity_kind=? AND entity_id=?",
            (entity_kind, entity_id),
        ).fetchone()
        period = str(row["period"]) if row is not None else preferred_period
        if row is None:
            self._catalog.execute(
                "INSERT INTO entity_locations(entity_kind,entity_id,period) VALUES(?,?,?)",
                (entity_kind, entity_id, period),
            )
        self._dirty_segments.add(period)
        return self._writable_segment(period, reason="late entity evidence")

    def _writable_segment(self, period: str, *, reason: str) -> sqlite3.Connection:
        connection = self._segment(period)
        sealed = self._catalog.execute(
            "SELECT sealed_at FROM ledger_segments WHERE period=? AND sealed_at IS NOT NULL",
            (period,),
        ).fetchone()
        if sealed is not None:
            now = time.time()
            self._catalog.execute(
                "UPDATE segment_seals SET invalidated_at=?,invalidation_reason=? "
                "WHERE period=? AND sealed_at=?",
                (now, reason, period, sealed["sealed_at"]),
            )
            self._catalog.execute(
                "UPDATE ledger_segments SET sealed_at=NULL,sha256=NULL WHERE period=?",
                (period,),
            )
        return connection

    # -- write path -------------------------------------------------------------

    def record_event(self, event: MuxEvent, dimensions: Mapping[str, Any]) -> None:
        self.record_events(((event, dimensions),))

    def record_events(
        self, items: Iterable[tuple[MuxEvent, Mapping[str, Any]]]
    ) -> int:
        """Reduce a batch and commit each touched segment exactly once."""

        self._dirty_segments.clear()
        self._extra_dirty_stamps.clear()
        changed: dict[str, list[float]] = {}
        inserted = 0
        for event, dimensions in items:
            if not event.session_id:
                continue
            if self._record_event_uncommitted(event, dimensions):
                changed.setdefault(period_of(float(event.ts)), []).append(float(event.ts))
                inserted += 1
        for period in self._dirty_segments:
            self._segment(period).commit()
        now = time.time()
        stamps = set(self._extra_dirty_stamps)
        for period, timestamps in changed.items():
            first = min(timestamps)
            last = max(timestamps)
            self._catalog.execute(
                "UPDATE ledger_segments SET first_observed_at=CASE "
                "WHEN first_observed_at IS NULL OR first_observed_at>? THEN ? "
                "ELSE first_observed_at END,last_observed_at=CASE "
                "WHEN last_observed_at IS NULL OR last_observed_at<? THEN ? "
                "ELSE last_observed_at END,evidence_rows=evidence_rows+? WHERE period=?",
                (first, first, last, last, len(timestamps), period),
            )
            stamps.update(timestamps)
        self._mark_dirty(stamps, now)
        if changed or stamps:
            self._catalog.commit()
        return inserted

    def _mark_dirty(self, stamps: Iterable[float], now: float) -> None:
        days = {day_of(stamp) for stamp in stamps}
        hours = {hour_of(stamp) for stamp in stamps}
        for day in days:
            self._catalog.execute(
                "INSERT INTO rollup_dirty_days(day,dirtied_at) VALUES(?,?) "
                "ON CONFLICT(day) DO UPDATE SET dirtied_at=excluded.dirtied_at",
                (day, now),
            )
        for hour in hours:
            self._catalog.execute(
                "INSERT INTO rollup_dirty_hours(hour,dirtied_at) VALUES(?,?) "
                "ON CONFLICT(hour) DO UPDATE SET dirtied_at=excluded.dirtied_at",
                (hour, now),
            )

    def record_parser_signatures(
        self,
        *,
        backend: str,
        harness_version: str | None,
        parser_version: str,
        signatures: Mapping[tuple[str, bool], int],
        now: float | None = None,
    ) -> None:
        """Count every provider event name per harness version, understood or not."""

        if not signatures:
            return
        seen_at = time.time() if now is None else now
        version = harness_version or "unknown"
        with self._catalog:
            for (name, recognized), count in signatures.items():
                self._catalog.execute(
                    "INSERT INTO parser_signatures(backend,harness_version,parser_version,"
                    "event_name,recognized,occurrences,first_seen_at,last_seen_at) "
                    "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(backend,harness_version,"
                    "parser_version,event_name) DO UPDATE SET "
                    "occurrences=parser_signatures.occurrences+excluded.occurrences,"
                    "recognized=excluded.recognized,last_seen_at=excluded.last_seen_at",
                    (
                        backend,
                        version,
                        parser_version,
                        name,
                        int(recognized),
                        count,
                        seen_at,
                        seen_at,
                    ),
                )

    def _record_event_uncommitted(
        self, event: MuxEvent, dimensions: Mapping[str, Any]
    ) -> bool:
        if event.type not in _CAPTURED_EVENT_TYPES:
            return False
        normalized_type = {
            "canonical_tool_use": "tool_use",
            "canonical_tool_result": "tool_result",
            "canonical_skill_invoked": "skill_invoked",
        }.get(event.type)
        if normalized_type is not None:
            event = MuxEvent(
                event.ts,
                event.session_id,
                event.source,
                normalized_type,
                event.payload,
                seq=event.seq,
                transient=event.transient,
            )
        if not event.session_id:
            return False
        observed_at = float(event.ts)
        period = period_of(observed_at)
        connection = self._writable_segment(period, reason="new observation")
        self._dirty_segments.add(period)
        payload_bytes = canonical_json(event.payload)
        payload_sha = digest(payload_bytes)
        evidence_id = digest(
            "\0".join(
                (
                    event.session_id,
                    event.type,
                    event.source,
                    str(event.seq),
                    payload_sha,
                )
            )
        )
        run_id = str(dimensions.get("run_id") or event.session_id)
        native_call_id = str(event.payload.get("call_id") or "") or None
        native_id = native_call_id or str(event.payload.get("compaction_id") or "") or None
        native_turn_id = (
            str(event.payload.get("turn_id") or dimensions.get("turn_id") or "") or None
        )
        raw_ordinal = event.payload.get("turn_epoch", dimensions.get("turn_ordinal"))
        turn_ordinal = int(raw_ordinal) if isinstance(raw_ordinal, int) else None
        turn_id = turn_entity_id(run_id, native_turn_id, turn_ordinal)
        backend = str(event.payload.get("backend") or dimensions.get("backend") or "unknown")
        inserted = connection.execute(
            "INSERT OR IGNORE INTO telemetry_evidence"
            "(evidence_id,observed_at,received_at,event_type,source_kind,source_version,backend,"
            "project_id,model,origin,session_id,run_id,turn_id,native_id,source_locator,"
            "payload_sha256,payload_bytes,privacy_class) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                evidence_id,
                observed_at,
                time.time(),
                event.type,
                event.source,
                str(event.payload.get("parser_version") or "") or None,
                backend,
                dimensions.get("project_id"),
                dimensions.get("model"),
                str(dimensions.get("origin") or "mux_owned"),
                event.session_id,
                run_id,
                turn_id,
                native_id,
                dimensions.get("source_locator"),
                payload_sha,
                len(payload_bytes),
                "metadata_only",
            ),
        ).rowcount
        if not inserted:
            return False
        self._upsert_run(connection, event, dimensions, evidence_id, run_id, backend)
        if event.type in _RUN_ENDING_EVENTS:
            self._close_incomplete_entities(event, evidence_id, run_id)
        if event.type in {"turn_started", "turn_ended", "turn_aborted"}:
            self._upsert_turn(
                connection,
                event,
                dimensions,
                evidence_id,
                run_id,
                turn_id,
                native_turn_id,
                turn_ordinal,
            )
        elif event.type in {"tool_use", "tool_result"}:
            tool_call_id = self._upsert_tool_call(
                connection, event, dimensions, evidence_id, run_id, turn_id, backend
            )
            if event.type == "tool_result" and isinstance(
                event.payload.get("test_outcome"), dict
            ):
                self._record_verification(
                    connection,
                    event,
                    dimensions,
                    evidence_id,
                    run_id,
                    turn_id,
                    tool_call_id,
                )
        elif event.type in {"approval_needed", "approval_resolved"} and native_call_id:
            self._note_approval(
                connection, event, dimensions, evidence_id, run_id, turn_id, backend
            )
        elif event.type == "skill_invoked":
            self._record_skill(
                connection, event, dimensions, evidence_id, run_id, turn_id
            )
        elif event.type == "canonical_model_request":
            self._record_model_request(
                connection, event, dimensions, evidence_id, run_id, turn_id
            )
        elif event.type == "context_compacted":
            self._record_compaction(
                connection, event, dimensions, evidence_id, run_id, turn_id
            )
        elif event.type == "provider_metric":
            self._record_provider_metric(
                connection, event, dimensions, evidence_id, run_id, backend
            )
        return True

    def _close_incomplete_entities(
        self, event: MuxEvent, evidence_id: str, run_id: str
    ) -> None:
        """Abandon what the ending run left open, wherever its segment lives."""

        for period in self._periods():
            connection = self._segment(period)
            tool_rows = [
                (str(row["tool_call_id"]), float(row["started_at"]))
                for row in connection.execute(
                    "SELECT tool_call_id,started_at FROM telemetry_tool_calls "
                    "WHERE run_id=? AND status='running'",
                    (run_id,),
                ).fetchall()
            ]
            if tool_rows:
                connection.execute(
                    "UPDATE telemetry_tool_calls SET status='abandoned',finished_at=?,"
                    "result_source=?,result_rank=?,status_source=?,evidence_quality=? "
                    "WHERE run_id=? AND status='running'",
                    (
                        event.ts,
                        event.source,
                        source_rank(event.source),
                        event.source,
                        evidence_quality_for(source_rank(event.source), event.source),
                        run_id,
                    ),
                )
                for tool_id, started_at in tool_rows:
                    self._extra_dirty_stamps.add(started_at)
                    self._link_evidence(
                        connection,
                        "tool_call",
                        tool_id,
                        evidence_id,
                        "abandonment",
                        event.source,
                    )
            open_turns = [
                float(row["started_at"])
                for row in connection.execute(
                    "SELECT started_at FROM telemetry_turns WHERE run_id=? AND status='running'",
                    (run_id,),
                ).fetchall()
            ]
            if open_turns:
                connection.execute(
                    "UPDATE telemetry_turns SET status='abandoned',finished_at=?,"
                    "last_evidence_id=? WHERE run_id=? AND status='running'",
                    (event.ts, evidence_id, run_id),
                )
                self._extra_dirty_stamps.update(open_turns)
            if tool_rows or open_turns:
                self._dirty_segments.add(period)

    def _upsert_run(
        self,
        connection: sqlite3.Connection,
        event: MuxEvent,
        dimensions: Mapping[str, Any],
        evidence_id: str,
        run_id: str,
        backend: str,
    ) -> None:
        model = str(dimensions.get("model") or "") or None
        initial_model = None if dimensions.get("model_is_final_only") else model
        ended = event.ts if event.type in _RUN_ENDING_EVENTS else None
        declared = dimensions.get("run_started_at")
        # A declared start comes from the session record or the history row. Without
        # one, the earliest evidence is the only start there is, and the row says so
        # rather than presenting the estimate as a fact.
        started_at = float(declared) if declared else float(event.ts)
        started_at_source = "declared" if declared else "first_evidence"
        connection = self._entity_connection("run", run_id, period_of(started_at))
        existing = connection.execute(
            "SELECT started_at FROM telemetry_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if existing is not None:
            # The run's rollup bucket is its start; anything that changes the row
            # later (tokens, end time, model) has to dirty that bucket, not today's.
            self._extra_dirty_stamps.add(float(existing["started_at"]))
        connection.execute(
            "INSERT INTO telemetry_runs"
            "(run_id,session_id,native_conversation_id,parent_run_id,launch_tool_call_id,"
            "project_id,backend,harness_version,origin,source_locator,started_at,ended_at,"
            "end_reason,initial_model,final_model,input_tokens,output_tokens,cache_read_tokens,"
            "cache_write_tokens,cost_usd,final_context_pct,peak_context_pct,measurement_source,"
            "first_evidence_id,last_evidence_id,started_at_source) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(run_id) DO UPDATE SET "
            "project_id=COALESCE(telemetry_runs.project_id,excluded.project_id),"
            "native_conversation_id=COALESCE(telemetry_runs.native_conversation_id,"
            "excluded.native_conversation_id),"
            "harness_version=COALESCE(excluded.harness_version,telemetry_runs.harness_version),"
            "source_locator=COALESCE(excluded.source_locator,telemetry_runs.source_locator),"
            # A declared start replaces a first-evidence estimate, never the reverse,
            # and among estimates the earliest evidence wins.
            "started_at=CASE WHEN excluded.started_at_source='declared' "
            "AND COALESCE(telemetry_runs.started_at_source,'')!='declared' "
            "THEN excluded.started_at "
            "WHEN excluded.started_at_source!='declared' "
            "AND COALESCE(telemetry_runs.started_at_source,'')!='declared' "
            "THEN MIN(telemetry_runs.started_at,excluded.started_at) "
            "ELSE telemetry_runs.started_at END,"
            "started_at_source=CASE WHEN excluded.started_at_source='declared' "
            "THEN 'declared' ELSE COALESCE(telemetry_runs.started_at_source,"
            "excluded.started_at_source) END,"
            "ended_at=COALESCE(excluded.ended_at,telemetry_runs.ended_at),"
            "end_reason=COALESCE(excluded.end_reason,telemetry_runs.end_reason),"
            "final_model=COALESCE(excluded.final_model,telemetry_runs.final_model),"
            "input_tokens=MAX(telemetry_runs.input_tokens,excluded.input_tokens),"
            "output_tokens=MAX(telemetry_runs.output_tokens,excluded.output_tokens),"
            "cache_read_tokens=MAX(telemetry_runs.cache_read_tokens,excluded.cache_read_tokens),"
            "cache_write_tokens=MAX(telemetry_runs.cache_write_tokens,excluded.cache_write_tokens),"
            "cost_usd=COALESCE(MAX(telemetry_runs.cost_usd,excluded.cost_usd),"
            "excluded.cost_usd,telemetry_runs.cost_usd),"
            "final_context_pct=COALESCE(excluded.final_context_pct,telemetry_runs.final_context_pct),"
            "peak_context_pct=COALESCE("
            "MAX(telemetry_runs.peak_context_pct,excluded.peak_context_pct),"
            "excluded.peak_context_pct,telemetry_runs.peak_context_pct),"
            "measurement_source=COALESCE(excluded.measurement_source,"
            "telemetry_runs.measurement_source),"
            "last_evidence_id=excluded.last_evidence_id",
            (
                run_id,
                event.session_id,
                dimensions.get("native_conversation_id"),
                dimensions.get("parent_run_id"),
                dimensions.get("launch_tool_call_id"),
                dimensions.get("project_id"),
                backend,
                dimensions.get("harness_version"),
                str(dimensions.get("origin") or "mux_owned"),
                dimensions.get("source_locator"),
                started_at,
                ended,
                str(event.payload.get("reason") or event.type) if ended else None,
                initial_model,
                model,
                int(dimensions.get("input_tokens") or 0),
                int(dimensions.get("output_tokens") or 0),
                int(dimensions.get("cache_read_tokens") or 0),
                int(dimensions.get("cache_write_tokens") or 0),
                dimensions.get("cost_usd"),
                dimensions.get("final_context_pct"),
                dimensions.get("peak_context_pct"),
                dimensions.get("measurement_source"),
                evidence_id,
                evidence_id,
                started_at_source,
            ),
        )
        if event.type in _RUN_LIFECYCLE_EVENTS:
            self._link_evidence(connection, "run", run_id, evidence_id, event.type, event.source)

    def _upsert_turn(
        self,
        connection: sqlite3.Connection,
        event: MuxEvent,
        dimensions: Mapping[str, Any],
        evidence_id: str,
        run_id: str,
        turn_id: str | None,
        native_turn_id: str | None,
        turn_ordinal: int | None,
    ) -> None:
        if turn_id is None:
            return
        connection = self._entity_connection("turn", turn_id, period_of(event.ts))
        existing = connection.execute(
            "SELECT started_at FROM telemetry_turns WHERE turn_id=?", (turn_id,)
        ).fetchone()
        if existing is not None:
            self._extra_dirty_stamps.add(float(existing["started_at"]))
        finished = event.ts if event.type != "turn_started" else None
        status = {
            "turn_started": "running",
            "turn_ended": "completed",
            "turn_aborted": "aborted",
        }[event.type]
        connection.execute(
            "INSERT INTO telemetry_turns"
            "(turn_id,run_id,native_turn_id,agent_id,session_id,project_id,backend,origin,"
            "harness_version,ordinal,trigger,started_at,finished_at,status,duration_ms,model,"
            "first_evidence_id,last_evidence_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(turn_id) DO UPDATE SET "
            "finished_at=COALESCE(excluded.finished_at,telemetry_turns.finished_at),"
            "status=CASE WHEN excluded.finished_at IS NOT NULL THEN excluded.status "
            "ELSE telemetry_turns.status END,"
            "duration_ms=COALESCE(excluded.duration_ms,telemetry_turns.duration_ms),"
            "harness_version=COALESCE(excluded.harness_version,telemetry_turns.harness_version),"
            "model=COALESCE(excluded.model,telemetry_turns.model),"
            "last_evidence_id=excluded.last_evidence_id",
            (
                turn_id,
                run_id,
                native_turn_id,
                str(dimensions.get("agent_id") or "root"),
                str(event.session_id),
                dimensions.get("project_id"),
                str(dimensions.get("backend") or "unknown"),
                str(dimensions.get("origin") or "mux_owned"),
                dimensions.get("harness_version"),
                turn_ordinal,
                str(event.payload.get("trigger") or "unknown"),
                event.ts,
                finished,
                status,
                event.payload.get("duration_ms"),
                dimensions.get("model"),
                evidence_id,
                evidence_id,
            ),
        )
        self._link_evidence(connection, "turn", turn_id, evidence_id, event.type, event.source)

    @staticmethod
    def _invocation_layer(event: MuxEvent, backend: str) -> str:
        explicit = event.payload.get("invocation_layer")
        if explicit in {"model", "runtime", "harness", "user"}:
            return str(explicit)
        harness = HARNESSES.get(backend)
        if harness is not None and harness.hook_reports_runtime_layer and event.source == "hook":
            return "runtime"
        return "model"

    def _tool_call_identity(
        self, event: MuxEvent, dimensions: Mapping[str, Any], run_id: str, backend: str
    ) -> tuple[str, str, str, str | None]:
        native_call_id = str(event.payload.get("call_id") or "") or None
        layer = self._invocation_layer(event, backend)
        agent_id = str(event.payload.get("agent_id") or dimensions.get("agent_id") or "root")
        identity = native_call_id or f"evidence:{event.session_id}:{event.seq}"
        return digest(f"{run_id}\0{agent_id}\0{layer}\0{identity}"), layer, agent_id, native_call_id

    def _note_approval(
        self,
        connection: sqlite3.Connection,
        event: MuxEvent,
        dimensions: Mapping[str, Any],
        evidence_id: str,
        run_id: str,
        turn_id: str | None,
        backend: str,
    ) -> None:
        """Pair an approval request with the call it named, and time the wait.

        The request opens the call's row if nothing has yet; the resolution, or the
        call's own result, closes the wait. Nothing is estimated: a resolution with
        no recorded request leaves the wait unknown.
        """

        tool_call_id, layer, agent_id, native_call_id = self._tool_call_identity(
            event, dimensions, run_id, backend
        )
        connection = self._entity_connection("tool_call", tool_call_id, period_of(event.ts))
        existing = connection.execute(
            "SELECT started_at,approval_requested_at,approval_wait_ms FROM telemetry_tool_calls "
            "WHERE tool_call_id=?",
            (tool_call_id,),
        ).fetchone()
        raw_name = str(event.payload.get("tool") or event.payload.get("detail") or "tool")
        if existing is None:
            classified = classify_tool(raw_name, backend=backend, source=event.source)
            connection.execute(
                "INSERT INTO telemetry_tool_calls"
                "(tool_call_id,run_id,turn_id,agent_id,session_id,project_id,backend,model,"
                "origin,native_call_id,invocation_layer,raw_name,family,operation,transport,"
                "server_name,tool_name,proposed_at,started_at,status,input_measurement,"
                "output_measurement,executed_input_measurement,normalization_version,"
                "evidence_count,evidence_quality,approval_requested_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'running',?,?,?,3,1,'none',?)",
                (
                    tool_call_id,
                    run_id,
                    turn_id,
                    agent_id,
                    event.session_id,
                    dimensions.get("project_id"),
                    backend,
                    event.payload.get("model") or dimensions.get("model"),
                    str(dimensions.get("origin") or "mux_owned"),
                    native_call_id,
                    layer,
                    raw_name,
                    classified["family"],
                    classified["operation"],
                    classified["transport"],
                    classified["server"],
                    classified["tool"],
                    event.ts,
                    event.ts,
                    _UNMEASURED,
                    _UNMEASURED,
                    _UNMEASURED,
                    event.ts if event.type == "approval_needed" else None,
                ),
            )
        else:
            self._extra_dirty_stamps.add(float(existing["started_at"]))
            if event.type == "approval_needed" and existing["approval_requested_at"] is None:
                connection.execute(
                    "UPDATE telemetry_tool_calls SET approval_requested_at=?,"
                    "evidence_count=evidence_count+1 WHERE tool_call_id=?",
                    (event.ts, tool_call_id),
                )
            elif (
                event.type == "approval_resolved"
                and existing["approval_requested_at"] is not None
                and existing["approval_wait_ms"] is None
            ):
                wait = max(0.0, (event.ts - float(existing["approval_requested_at"])) * 1000)
                connection.execute(
                    "UPDATE telemetry_tool_calls SET approval_wait_ms=?,"
                    "evidence_count=evidence_count+1 WHERE tool_call_id=?",
                    (wait, tool_call_id),
                )
            else:
                connection.execute(
                    "UPDATE telemetry_tool_calls SET evidence_count=evidence_count+1 "
                    "WHERE tool_call_id=?",
                    (tool_call_id,),
                )
        self._link_evidence(
            connection,
            "tool_call",
            tool_call_id,
            evidence_id,
            "approval_request" if event.type == "approval_needed" else "approval_resolution",
            event.source,
        )

    def _upsert_tool_call(
        self,
        connection: sqlite3.Connection,
        event: MuxEvent,
        dimensions: Mapping[str, Any],
        evidence_id: str,
        run_id: str,
        turn_id: str | None,
        backend: str,
    ) -> str:
        payload = event.payload
        tool_call_id, layer, agent_id, native_call_id = self._tool_call_identity(
            event, dimensions, run_id, backend
        )
        connection = self._entity_connection(
            "tool_call", tool_call_id, period_of(event.ts)
        )
        raw_name = str(payload.get("tool") or payload.get("name") or "tool")
        classified = classify_tool(raw_name, backend=backend, source=event.source)
        server_name = str(payload.get("server_name") or "") or classified["server"]
        rank = source_rank(event.source)
        existing = connection.execute(
            "SELECT * FROM telemetry_tool_calls WHERE tool_call_id=?", (tool_call_id,)
        ).fetchone()
        if existing is not None:
            self._extra_dirty_stamps.add(float(existing["started_at"]))
        is_result = event.type == "tool_result"
        input_value = (
            payload.get("tool_input") or payload.get("arguments") or payload.get("target")
            if not is_result
            else None
        )
        output_value = (
            payload.get("output") or payload.get("result") or payload.get("detail")
            if is_result
            else None
        )
        input_chars, input_bytes, input_sha = content_metrics(input_value)
        output_chars, output_bytes, output_sha = content_metrics(output_value)
        input_measurement = "full" if input_value is not None else _UNMEASURED
        output_measurement = "full" if output_value is not None else _UNMEASURED
        precomputed_output = any(
            payload.get(field) is not None
            for field in ("output_chars", "output_bytes", "output_sha256")
        )
        if isinstance(payload.get("output_chars"), int):
            output_chars = int(payload["output_chars"])
        if isinstance(payload.get("output_bytes"), int):
            output_bytes = int(payload["output_bytes"])
        if isinstance(payload.get("output_sha256"), str):
            output_sha = str(payload["output_sha256"])
        if precomputed_output:
            output_measurement = str(payload.get("output_measurement") or "full")
        elif is_result and isinstance(payload.get("content_hash"), str):
            # The observation parser hashes the full provider result before it
            # bounds `detail`. The bounded preview cannot supply honest size.
            output_sha = str(payload["content_hash"])
            output_chars = None
            output_bytes = None
            output_measurement = "full_hash_size_unknown"
        if not is_result and isinstance(payload.get("content_hash"), str):
            input_sha = str(payload["content_hash"])
            input_measurement = "full_hash_size_unknown"
        executed_input_bytes = payload.get("executed_input_bytes")
        executed_input_chars = payload.get("executed_input_chars")
        executed_input_sha = payload.get("executed_input_sha256")
        executed_present = any(
            value is not None
            for value in (executed_input_bytes, executed_input_chars, executed_input_sha)
        )
        executed_input_measurement = (
            str(payload.get("executed_input_measurement") or "full")
            if executed_present
            else _UNMEASURED
        )
        target = payload.get("target")
        target_text = target if isinstance(target, str) and target else None
        target_sha = digest(target_text) if target_text else None
        success_value = payload.get("success")
        success = None if success_value is None else int(bool(success_value))
        if is_result:
            if payload.get("denied"):
                status = "denied"
            elif payload.get("interrupted"):
                status = "interrupted"
            elif success == 1:
                status = "succeeded"
            elif success == 0:
                status = "failed"
            else:
                status = "unknown"
        else:
            status = "running"
        truncated_value = payload.get("output_truncated")
        output_truncated = None if truncated_value is None else int(bool(truncated_value))
        provider_sequence = payload.get("provider_sequence")
        quality = evidence_quality_for(rank, event.source) if is_result else "none"
        conflict = False
        if existing is not None and is_result:
            conflict = (
                success is not None
                and existing["success"] is not None
                and int(existing["success"]) != success
            )
        elif existing is not None:
            conflict = str(existing["raw_name"]) != raw_name or bool(
                target_sha
                and existing["target_sha256"]
                and str(existing["target_sha256"]) != target_sha
            )
        if existing is None:
            connection.execute(
                "INSERT INTO telemetry_tool_calls"
                "(tool_call_id,run_id,turn_id,agent_id,session_id,project_id,backend,model,"
                "origin,harness_version,parent_tool_call_id,parent_status,"
                "native_call_id,invocation_layer,raw_name,family,operation,transport,server_name,"
                "tool_name,proposed_at,started_at,finished_at,status,success,error_type,exit_code,"
                "duration_ms,approval_wait_ms,input_bytes,output_bytes,input_chars,output_chars,"
                "input_sha256,output_sha256,input_measurement,output_measurement,"
                "executed_input_bytes,executed_input_chars,executed_input_sha256,"
                "executed_input_measurement,executed_input_source,"
                "target_preview,target_sha256,request_source,"
                "request_rank,result_source,result_rank,status_source,duration_source,error_source,"
                "output_source,normalization_version,evidence_count,output_truncated,"
                "provider_sequence,native_conversation_id,tool_namespace,error_sha256,"
                "evidence_quality) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
                "?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    tool_call_id,
                    run_id,
                    turn_id,
                    agent_id,
                    event.session_id,
                    dimensions.get("project_id"),
                    backend,
                    payload.get("model") or dimensions.get("model"),
                    str(dimensions.get("origin") or "mux_owned"),
                    payload.get("harness_version") or dimensions.get("harness_version"),
                    payload.get("parent_tool_call_id"),
                    payload.get("parent_status")
                    or ("provider_unavailable" if layer == "runtime" else None),
                    native_call_id,
                    layer,
                    raw_name,
                    classified["family"],
                    classified["operation"],
                    classified["transport"],
                    server_name,
                    classified["tool"],
                    None if is_result else event.ts,
                    event.ts,
                    event.ts if is_result else None,
                    status,
                    success,
                    payload.get("error_type"),
                    payload.get("exit_code"),
                    payload.get("duration_ms"),
                    payload.get("approval_wait_ms"),
                    input_bytes,
                    output_bytes,
                    input_chars,
                    output_chars,
                    input_sha,
                    output_sha,
                    input_measurement,
                    output_measurement,
                    executed_input_bytes,
                    executed_input_chars,
                    executed_input_sha,
                    executed_input_measurement,
                    event.source if executed_present else None,
                    target_text[:512] if target_text else None,
                    target_sha,
                    None if is_result else event.source,
                    0 if is_result else rank,
                    event.source if is_result else None,
                    rank if is_result else 0,
                    event.source if is_result else None,
                    (
                        event.source
                        if is_result and payload.get("duration_ms") is not None
                        else None
                    ),
                    (
                        event.source
                        if is_result and payload.get("error_type") is not None
                        else None
                    ),
                    event.source if is_result and output_sha is not None else None,
                    3,
                    1,
                    output_truncated,
                    provider_sequence,
                    payload.get("native_conversation_id")
                    or dimensions.get("native_conversation_id"),
                    payload.get("tool_namespace"),
                    payload.get("error_sha256"),
                    quality,
                ),
            )
        else:
            updates: dict[str, Any] = {"evidence_count": int(existing["evidence_count"]) + 1}
            # Provenance a later, richer source can add to either side of the call.
            for field in ("harness_version", "model", "native_conversation_id", "tool_namespace"):
                value = payload.get(field)
                if value is not None and existing[field] is None:
                    updates[field] = value
            if is_result and rank >= int(existing["result_rank"]):
                updates.update(
                    {
                        "finished_at": event.ts,
                        "status": status,
                        "success": success,
                        "result_source": event.source,
                        "result_rank": rank,
                        "status_source": event.source,
                        "evidence_quality": quality,
                    }
                )
            if is_result:
                field_values = {
                    "error_type": payload.get("error_type"),
                    "error_sha256": payload.get("error_sha256"),
                    "exit_code": payload.get("exit_code"),
                    "duration_ms": payload.get("duration_ms"),
                    "approval_wait_ms": payload.get("approval_wait_ms"),
                    "output_bytes": output_bytes,
                    "output_chars": output_chars,
                    "output_sha256": output_sha,
                    "output_truncated": output_truncated,
                    "provider_sequence": provider_sequence,
                    "executed_input_bytes": executed_input_bytes,
                    "executed_input_chars": executed_input_chars,
                    "executed_input_sha256": executed_input_sha,
                }
                for field, value in field_values.items():
                    if value is not None and existing[field] is None:
                        updates[field] = value
                if (
                    existing["approval_requested_at"] is not None
                    and existing["approval_wait_ms"] is None
                    and "approval_wait_ms" not in updates
                ):
                    # The result is the latest the wait can have ended; a resolution
                    # that arrived earlier already closed it more precisely.
                    updates["approval_wait_ms"] = max(
                        0.0, (event.ts - float(existing["approval_requested_at"])) * 1000
                    )
                if updates.keys() & {"duration_ms", "approval_wait_ms"}:
                    updates["duration_source"] = event.source
                if updates.keys() & {"error_type", "exit_code", "error_sha256"}:
                    updates["error_source"] = event.source
                if updates.keys() & {"output_bytes", "output_chars", "output_sha256"}:
                    updates["output_source"] = event.source
                    # The measurement describes the values just filled; a row whose
                    # measurement was `unknown` takes the new one, and a row that
                    # already carries a measurement keeps the one its values came with.
                    if existing["output_measurement"] == _UNMEASURED:
                        updates["output_measurement"] = output_measurement
                if updates.keys() & {
                    "executed_input_bytes",
                    "executed_input_chars",
                    "executed_input_sha256",
                }:
                    updates["executed_input_measurement"] = executed_input_measurement
                    updates["executed_input_source"] = event.source
            elif not is_result and rank >= int(existing["request_rank"]):
                updates.update(
                    {
                        "turn_id": turn_id or existing["turn_id"],
                        "raw_name": raw_name,
                        "family": classified["family"],
                        "operation": classified["operation"],
                        "transport": classified["transport"],
                        "server_name": server_name,
                        "tool_name": classified["tool"],
                        "input_bytes": input_bytes,
                        "input_chars": input_chars,
                        "input_sha256": input_sha,
                        "input_measurement": input_measurement,
                        "target_preview": target_text[:512] if target_text else None,
                        "target_sha256": target_sha,
                        "request_source": event.source,
                        "request_rank": rank,
                    }
                )
                if existing["proposed_at"] is None:
                    updates["proposed_at"] = event.ts
            assignments = ",".join(f"{key}=?" for key in updates)
            connection.execute(
                f"UPDATE telemetry_tool_calls SET {assignments} WHERE tool_call_id=?",
                (*updates.values(), tool_call_id),
            )
        self._link_evidence(
            connection,
            "tool_call",
            tool_call_id,
            evidence_id,
            "result" if is_result else "request",
            event.source,
            conflict=conflict,
        )
        return tool_call_id

    def _record_skill(
        self,
        connection: sqlite3.Connection,
        event: MuxEvent,
        dimensions: Mapping[str, Any],
        evidence_id: str,
        run_id: str,
        turn_id: str | None,
    ) -> None:
        name = str(event.payload.get("skill") or event.payload.get("skill_name") or "").strip()
        if not name:
            return
        occurrences = max(1, int(event.payload.get("count") or 1))
        if event.payload.get("metric_temporality") == "cumulative":
            series_id = str(event.payload.get("metric_series_id") or "")
            if not series_id:
                return
            start_time = str(event.payload.get("metric_start_time") or "") or None
            checkpoint = self._catalog.execute(
                "SELECT start_time_unix_nano,value FROM metric_checkpoints WHERE series_id=?",
                (series_id,),
            ).fetchone()
            # A cumulative counter only reduces to a delta against the same series
            # start; a new start time is a process restart and the whole value is new.
            if checkpoint is not None and checkpoint["start_time_unix_nano"] == start_time:
                occurrences = max(0, occurrences - int(checkpoint["value"]))
            self._catalog.execute(
                "INSERT INTO metric_checkpoints(series_id,start_time_unix_nano,value,observed_at) "
                "VALUES(?,?,?,?) ON CONFLICT(series_id) DO UPDATE SET "
                "start_time_unix_nano=excluded.start_time_unix_nano,value=excluded.value,"
                "observed_at=excluded.observed_at",
                (series_id, start_time, int(event.payload.get("count") or 0), event.ts),
            )
            if occurrences == 0:
                return
        native = str(event.payload.get("invocation_id") or event.payload.get("call_id") or "")
        identity = native or evidence_id
        skill_id = digest(
            f"{run_id}\0{dimensions.get('agent_id') or 'root'}\0{identity}\0{name}"
        )
        connection = self._entity_connection(
            "skill_invocation", skill_id, period_of(event.ts)
        )
        quality = "native" if event.source in _NATIVE_SOURCES else "hook"
        connection.execute(
            "INSERT INTO telemetry_skill_invocations"
            "(skill_invocation_id,run_id,turn_id,agent_id,session_id,project_id,backend,model,"
            "origin,native_invocation_id,skill_name,"
            "skill_revision,skill_source,skill_scope,plugin_id,plugin_version,invocation_trigger,"
            "activated_at,evidence_quality,occurrences,evidence_count) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1) "
            "ON CONFLICT(skill_invocation_id) DO UPDATE SET "
            "occurrences=MAX(telemetry_skill_invocations.occurrences,excluded.occurrences),"
            "evidence_count=telemetry_skill_invocations.evidence_count+1",
            (
                skill_id,
                run_id,
                turn_id,
                str(dimensions.get("agent_id") or "root"),
                str(event.session_id),
                dimensions.get("project_id"),
                str(dimensions.get("backend") or "unknown"),
                event.payload.get("model") or dimensions.get("model"),
                str(dimensions.get("origin") or "mux_owned"),
                native or None,
                name,
                event.payload.get("skill_revision"),
                event.payload.get("skill_source"),
                event.payload.get("skill_scope"),
                event.payload.get("plugin_id"),
                event.payload.get("plugin_version"),
                str(event.payload.get("invocation_trigger") or "unknown"),
                event.ts,
                quality,
                occurrences,
            ),
        )
        self._link_evidence(
            connection, "skill_invocation", skill_id, evidence_id, "activation", event.source
        )

    def _record_model_request(
        self,
        connection: sqlite3.Connection,
        event: MuxEvent,
        dimensions: Mapping[str, Any],
        evidence_id: str,
        run_id: str,
        turn_id: str | None,
    ) -> None:
        payload = event.payload
        native = str(payload.get("request_id") or "") or None
        agent_id = str(payload.get("agent_id") or dimensions.get("agent_id") or "root")
        identity = native or f"sequence:{event.seq or evidence_id}"
        request_id = digest(f"{run_id}\0{agent_id}\0{identity}")
        duration = payload.get("duration_ms")
        duration_ms = float(duration) if isinstance(duration, (int, float)) else None
        started_at = event.ts - duration_ms / 1000 if duration_ms is not None else None
        connection = self._entity_connection(
            "model_request", request_id, period_of(started_at or event.ts)
        )
        connection.execute(
            "INSERT INTO telemetry_model_requests"
            "(model_request_id,run_id,turn_id,agent_id,session_id,project_id,backend,model,"
            "origin,native_request_id,query_source,started_at,finished_at,duration_ms,success,"
            "attempts,input_tokens,output_tokens,cache_read_tokens,cache_write_tokens,cost_usd,"
            "error_type,error_sha256,evidence_count,first_token_ms,reasoning_tokens,"
            "client_request_id,endpoint,effort) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?) "
            "ON CONFLICT(model_request_id) DO UPDATE SET "
            "duration_ms=COALESCE(excluded.duration_ms,telemetry_model_requests.duration_ms),"
            "success=COALESCE(excluded.success,telemetry_model_requests.success),"
            "attempts=MAX(COALESCE(excluded.attempts,0),"
            "COALESCE(telemetry_model_requests.attempts,0)),"
            "input_tokens=COALESCE(excluded.input_tokens,telemetry_model_requests.input_tokens),"
            "output_tokens=COALESCE(excluded.output_tokens,telemetry_model_requests.output_tokens),"
            "cache_read_tokens=COALESCE(excluded.cache_read_tokens,"
            "telemetry_model_requests.cache_read_tokens),"
            "cache_write_tokens=COALESCE(excluded.cache_write_tokens,"
            "telemetry_model_requests.cache_write_tokens),"
            "cost_usd=COALESCE(excluded.cost_usd,telemetry_model_requests.cost_usd),"
            "error_type=COALESCE(excluded.error_type,telemetry_model_requests.error_type),"
            "error_sha256=COALESCE(excluded.error_sha256,"
            "telemetry_model_requests.error_sha256),"
            "first_token_ms=COALESCE(excluded.first_token_ms,"
            "telemetry_model_requests.first_token_ms),"
            "reasoning_tokens=COALESCE(excluded.reasoning_tokens,"
            "telemetry_model_requests.reasoning_tokens),"
            "client_request_id=COALESCE(excluded.client_request_id,"
            "telemetry_model_requests.client_request_id),"
            "endpoint=COALESCE(excluded.endpoint,telemetry_model_requests.endpoint),"
            "effort=COALESCE(excluded.effort,telemetry_model_requests.effort),"
            "evidence_count=telemetry_model_requests.evidence_count+1",
            (
                request_id,
                run_id,
                turn_id,
                agent_id,
                str(event.session_id),
                dimensions.get("project_id"),
                str(dimensions.get("backend") or payload.get("backend") or "unknown"),
                payload.get("model") or dimensions.get("model"),
                str(dimensions.get("origin") or "mux_owned"),
                native,
                payload.get("query_source"),
                started_at,
                event.ts,
                duration_ms,
                None if payload.get("success") is None else int(bool(payload.get("success"))),
                payload.get("attempts"),
                payload.get("input_tokens"),
                payload.get("output_tokens"),
                payload.get("cache_read_tokens"),
                payload.get("cache_write_tokens"),
                payload.get("cost_usd"),
                payload.get("error_type"),
                payload.get("error_sha256"),
                payload.get("first_token_ms"),
                payload.get("reasoning_tokens"),
                payload.get("client_request_id"),
                payload.get("endpoint"),
                payload.get("effort"),
            ),
        )
        self._link_evidence(
            connection, "model_request", request_id, evidence_id, "completion", event.source
        )

    def _record_compaction(
        self,
        connection: sqlite3.Connection,
        event: MuxEvent,
        dimensions: Mapping[str, Any],
        evidence_id: str,
        run_id: str,
        turn_id: str | None,
    ) -> None:
        native = str(event.payload.get("compaction_id") or "") or None
        compaction_id = digest(f"{run_id}\0{native or evidence_id}")
        connection = self._entity_connection(
            "compaction", compaction_id, period_of(event.ts)
        )
        success = event.payload.get("success")
        connection.execute(
            "INSERT INTO telemetry_compactions"
            "(compaction_id,run_id,turn_id,agent_id,session_id,project_id,backend,model,origin,"
            "native_compaction_id,observed_at,trigger,success,duration_ms,tokens_before,"
            "tokens_after,capability,confidence,evidence_count) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1) "
            "ON CONFLICT(compaction_id) DO UPDATE SET "
            "trigger=COALESCE(excluded.trigger,telemetry_compactions.trigger),"
            "success=COALESCE(excluded.success,telemetry_compactions.success),"
            "duration_ms=COALESCE(excluded.duration_ms,telemetry_compactions.duration_ms),"
            "tokens_before=COALESCE(excluded.tokens_before,telemetry_compactions.tokens_before),"
            "tokens_after=COALESCE(excluded.tokens_after,telemetry_compactions.tokens_after),"
            "capability=COALESCE(excluded.capability,telemetry_compactions.capability),"
            "confidence=COALESCE(excluded.confidence,telemetry_compactions.confidence),"
            "evidence_count=telemetry_compactions.evidence_count+1",
            (
                compaction_id,
                run_id,
                turn_id,
                str(event.payload.get("agent_id") or dimensions.get("agent_id") or "root"),
                str(event.session_id),
                dimensions.get("project_id"),
                str(event.payload.get("backend") or dimensions.get("backend") or "unknown"),
                event.payload.get("model") or dimensions.get("model"),
                str(dimensions.get("origin") or "mux_owned"),
                native,
                event.ts,
                event.payload.get("trigger"),
                None if success is None else int(bool(success)),
                event.payload.get("duration_ms"),
                event.payload.get("tokens_before"),
                event.payload.get("tokens_after"),
                event.payload.get("capability"),
                event.payload.get("confidence"),
            ),
        )
        self._link_evidence(
            connection, "compaction", compaction_id, evidence_id, "event", event.source
        )

    def _record_verification(
        self,
        connection: sqlite3.Connection,
        event: MuxEvent,
        dimensions: Mapping[str, Any],
        evidence_id: str,
        run_id: str,
        turn_id: str | None,
        tool_call_id: str,
    ) -> None:
        outcome = event.payload["test_outcome"]
        encoded = canonical_json(outcome)
        outcome_sha = digest(encoded)
        verification_id = digest(f"{tool_call_id}\0{outcome_sha}")
        connection = self._entity_connection(
            "verification", verification_id, period_of(event.ts)
        )
        failed = outcome.get("failed")
        errors = outcome.get("errors")
        successful = int(not int(failed or 0) and not int(errors or 0))
        duration = event.payload.get("duration_ms")
        started = event.ts - float(duration) / 1000 if isinstance(duration, (int, float)) else None
        connection.execute(
            "INSERT OR IGNORE INTO telemetry_verifications"
            "(verification_id,run_id,turn_id,tool_call_id,project_id,backend,model,origin,"
            "framework,passed,failed,"
            "errors,skipped,successful,started_at,finished_at,outcome_sha256,parser_version,"
            "evidence_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                verification_id,
                run_id,
                turn_id,
                tool_call_id,
                dimensions.get("project_id"),
                str(dimensions.get("backend") or "unknown"),
                dimensions.get("model"),
                str(dimensions.get("origin") or "mux_owned"),
                str(outcome.get("framework") or "unknown"),
                outcome.get("passed"),
                failed,
                errors,
                outcome.get("skipped"),
                successful,
                started,
                event.ts,
                outcome_sha,
                event.payload.get("parser_version"),
                evidence_id,
            ),
        )
        self._link_evidence(
            connection,
            "verification",
            verification_id,
            evidence_id,
            "outcome",
            event.source,
        )

    def _record_provider_metric(
        self,
        connection: sqlite3.Connection,
        event: MuxEvent,
        dimensions: Mapping[str, Any],
        evidence_id: str,
        run_id: str,
        backend: str,
    ) -> None:
        """Keep one aggregated provider point as itself; it never becomes an entity."""

        payload = event.payload
        metric = str(payload.get("metric") or "").strip()
        if not metric:
            return
        attributes = payload.get("attributes")
        attributes = attributes if isinstance(attributes, dict) else {}
        metric_id = digest(f"{run_id}\0metric\0{evidence_id}")
        connection.execute(
            "INSERT OR IGNORE INTO telemetry_provider_metrics"
            "(metric_id,run_id,session_id,agent_id,project_id,backend,model,origin,"
            "harness_version,metric_name,kind,temporality,attributes_json,count,sum,min,max,"
            "started_at,observed_at,evidence_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                metric_id,
                run_id,
                str(event.session_id),
                str(payload.get("agent_id") or dimensions.get("agent_id") or "root"),
                dimensions.get("project_id"),
                backend,
                payload.get("model") or dimensions.get("model"),
                str(dimensions.get("origin") or "mux_owned"),
                payload.get("harness_version") or dimensions.get("harness_version"),
                metric,
                str(payload.get("kind") or "sum"),
                str(payload.get("temporality") or "delta"),
                canonical_json(attributes).decode("utf-8"),
                payload.get("count"),
                payload.get("sum"),
                payload.get("min"),
                payload.get("max"),
                payload.get("started_at"),
                event.ts,
                evidence_id,
            ),
        )
        self._link_evidence(
            connection, "provider_metric", metric_id, evidence_id, "point", event.source
        )

    @staticmethod
    def _link_evidence(
        connection: sqlite3.Connection,
        entity_kind: str,
        entity_id: str,
        evidence_id: str,
        contribution: str,
        source: str,
        *,
        conflict: bool = False,
    ) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO telemetry_entity_evidence"
            "(entity_kind,entity_id,evidence_id,contribution,precedence_rank,conflict) "
            "VALUES(?,?,?,?,?,?)",
            (
                entity_kind,
                entity_id,
                evidence_id,
                contribution,
                source_rank(source),
                int(conflict),
            ),
        )

    # -- rollups, seals, storage ------------------------------------------------

    _TOOL_ROLLUP_SQL = (
        "SELECT backend,COALESCE(model,'unknown') model,COALESCE(project_id,'') project_id,"
        "origin,invocation_layer,family,operation,transport,raw_name,status,evidence_quality,"
        "COUNT(*) calls,SUM(duration_ms IS NOT NULL) duration_count,"
        "SUM(COALESCE(duration_ms,0)) duration_ms,SUM(COALESCE(input_bytes,0)) input_bytes,"
        "SUM(COALESCE(output_bytes,0)) output_bytes,"
        "SUM(approval_wait_ms IS NOT NULL) approval_wait_count,"
        "SUM(COALESCE(approval_wait_ms,0)) approval_wait_ms "
        "FROM telemetry_tool_calls WHERE started_at>=? AND started_at<? "
        "GROUP BY backend,model,project_id,origin,invocation_layer,family,operation,transport,"
        "raw_name,status,evidence_quality"
    )
    _TOOL_ROLLUP_INSERT_COLUMNS = (
        "backend,model,project_id,origin,invocation_layer,family,operation,transport,raw_name,"
        "status,evidence_quality,calls,duration_count,duration_ms,input_bytes,output_bytes,"
        "approval_wait_count,approval_wait_ms"
    )

    def _tool_rollup_rows(self, period: str, start: float, end: float) -> list[tuple[Any, ...]]:
        rows = self._segment(period).execute(self._TOOL_ROLLUP_SQL, (start, end)).fetchall()
        return [tuple(row) for row in rows]

    def _rebuild_bucket(
        self,
        *,
        bucket_column: str,
        bucket: str,
        start: float,
        end: float,
        period: str,
        tool_table: str,
        workload_table: str,
    ) -> None:
        known = self._catalog.execute(
            "SELECT 1 FROM ledger_segments WHERE period=?", (period,)
        ).fetchone()
        tool_rows = self._tool_rollup_rows(period, start, end) if known is not None else []
        workload_rows = self.workload_rows_between(period, start, end) if known is not None else []
        workload_columns = (
            bucket_column, "backend", "model", "project_id", "origin", *WORKLOAD_FIELDS
        )
        placeholders = ",".join("?" for _ in range(18))
        self._catalog.execute(f"DELETE FROM {tool_table} WHERE {bucket_column}=?", (bucket,))
        self._catalog.executemany(
            f"INSERT INTO {tool_table}({bucket_column},{self._TOOL_ROLLUP_INSERT_COLUMNS}) "
            f"VALUES(?,{placeholders})",
            [(bucket, *row) for row in tool_rows],
        )
        self._catalog.execute(f"DELETE FROM {workload_table} WHERE {bucket_column}=?", (bucket,))
        self._catalog.executemany(
            f"INSERT INTO {workload_table}({','.join(workload_columns)}) "
            f"VALUES({','.join('?' for _ in workload_columns)})",
            [
                (bucket, *(group[column] for column in workload_columns[1:]))
                for group in workload_rows
            ],
        )

    def _rebuild_daily_entities(self, day: str, start: float, end: float, period: str) -> None:
        known = self._catalog.execute(
            "SELECT 1 FROM ledger_segments WHERE period=?", (period,)
        ).fetchone()
        connection = self._segment(period) if known is not None else None
        self._catalog.execute("DELETE FROM skill_daily WHERE day=?", (day,))
        self._catalog.execute("DELETE FROM verification_daily WHERE day=?", (day,))
        self._catalog.execute("DELETE FROM compaction_daily WHERE day=?", (day,))
        if connection is None:
            return
        self._catalog.executemany(
            "INSERT INTO skill_daily(day,backend,model,project_id,origin,skill_name,"
            "invocation_trigger,skill_source,skill_scope,invocations) VALUES(?,?,?,?,?,?,?,?,?,?)",
            [
                (day, *tuple(row))
                for row in connection.execute(
                    "SELECT backend,COALESCE(model,'unknown'),COALESCE(project_id,''),origin,"
                    "skill_name,COALESCE(invocation_trigger,'unknown'),"
                    "COALESCE(skill_source,'unknown'),COALESCE(skill_scope,'unknown'),"
                    "SUM(occurrences) FROM telemetry_skill_invocations "
                    "WHERE activated_at>=? AND activated_at<? GROUP BY 1,2,3,4,5,6,7,8",
                    (start, end),
                ).fetchall()
            ],
        )
        self._catalog.executemany(
            "INSERT INTO verification_daily(day,backend,model,project_id,origin,framework,"
            "verifications,successful,passed,failed,errors,skipped) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (day, *tuple(row))
                for row in connection.execute(
                    "SELECT backend,COALESCE(model,'unknown'),COALESCE(project_id,''),origin,"
                    "framework,COUNT(*),SUM(successful=1),SUM(COALESCE(passed,0)),"
                    "SUM(COALESCE(failed,0)),SUM(COALESCE(errors,0)),SUM(COALESCE(skipped,0)) "
                    "FROM telemetry_verifications WHERE finished_at>=? AND finished_at<? "
                    "GROUP BY 1,2,3,4,5",
                    (start, end),
                ).fetchall()
            ],
        )
        self._catalog.executemany(
            "INSERT INTO compaction_daily(day,backend,model,project_id,origin,trigger,count,"
            "failures,duration_count,duration_ms,token_count,tokens_reclaimed) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (day, *tuple(row))
                for row in connection.execute(
                    "SELECT backend,COALESCE(model,'unknown'),COALESCE(project_id,''),origin,"
                    "COALESCE(trigger,'unknown'),COUNT(*),"
                    "SUM(CASE WHEN success=0 THEN 1 ELSE 0 END),"
                    "SUM(duration_ms IS NOT NULL),SUM(COALESCE(duration_ms,0)),"
                    "SUM(tokens_before IS NOT NULL AND tokens_after IS NOT NULL),"
                    "SUM(CASE WHEN tokens_before IS NOT NULL AND tokens_after IS NOT NULL "
                    "THEN tokens_before-tokens_after ELSE 0 END) "
                    "FROM telemetry_compactions WHERE observed_at>=? AND observed_at<? "
                    "GROUP BY 1,2,3,4,5",
                    (start, end),
                ).fetchall()
            ],
        )

    def rebuild_next_closed_day(self, *, now: float | None = None) -> str | None:
        """Rebuild one dirty closed UTC day's rollups from canonical entities.

        Tool, workload, skill, verification, and compaction rollups are rebuilt
        together. The day's hour rollups are left alone: a full-day window reads
        the day, a partial-day window still needs the hours, and the hours that
        changed are dirty in their own right and rebuilt by the hour worker.
        """

        current_day = day_of(time.time() if now is None else now)
        row = self._catalog.execute(
            "SELECT day FROM rollup_dirty_days WHERE day<? ORDER BY day LIMIT 1",
            (current_day,),
        ).fetchone()
        if row is None:
            return None
        day = str(row["day"])
        start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC).timestamp()
        end = start + 86400
        period = day[:7]
        with self._catalog:
            self._rebuild_bucket(
                bucket_column="day",
                bucket=day,
                start=start,
                end=end,
                period=period,
                tool_table="tool_daily",
                workload_table="workload_daily",
            )
            self._rebuild_daily_entities(day, start, end, period)
            self._catalog.execute("DELETE FROM rollup_dirty_days WHERE day=?", (day,))
            self._catalog.execute(
                "INSERT INTO rollup_days(day,rebuilt_at) VALUES(?,?) "
                "ON CONFLICT(day) DO UPDATE SET rebuilt_at=excluded.rebuilt_at",
                (day, time.time()),
            )
        return day

    def rebuild_next_closed_hour(self, *, now: float | None = None) -> str | None:
        """Rebuild one dirty closed UTC hour's tool and workload rollups.

        Hours serve the sub-day windows (the last 24 hours spans two partial days
        that no day rollup can answer), so every closed dirty hour is rebuilt even
        when its day already has a rollup of its own.
        """

        current_hour = hour_of(time.time() if now is None else now)
        row = self._catalog.execute(
            "SELECT hour FROM rollup_dirty_hours WHERE hour<? ORDER BY hour LIMIT 1",
            (current_hour,),
        ).fetchone()
        if row is None:
            return None
        hour = str(row["hour"])
        start = datetime.strptime(hour, "%Y-%m-%dT%H").replace(tzinfo=UTC).timestamp()
        with self._catalog:
            self._rebuild_bucket(
                bucket_column="hour",
                bucket=hour,
                start=start,
                end=start + 3600,
                period=hour[:7],
                tool_table="tool_hourly",
                workload_table="workload_hourly",
            )
            self._catalog.execute(
                "INSERT INTO rollup_hours(hour,rebuilt_at) VALUES(?,?) "
                "ON CONFLICT(hour) DO UPDATE SET rebuilt_at=excluded.rebuilt_at",
                (hour, time.time()),
            )
            self._catalog.execute("DELETE FROM rollup_dirty_hours WHERE hour=?", (hour,))
        return hour

    def seal_next_segment(
        self, *, now: float | None = None, grace_days: int = 7
    ) -> dict[str, Any] | None:
        """Integrity-check and hash one inactive segment without deleting it."""

        current = time.time() if now is None else now
        cutoff = current - max(1, grace_days) * 86400
        row = self._catalog.execute(
            "SELECT period,relative_path FROM ledger_segments WHERE sealed_at IS NULL "
            "AND last_observed_at<? AND NOT EXISTS "
            "(SELECT 1 FROM rollup_dirty_days d "
            "WHERE substr(d.day,1,7)=ledger_segments.period) ORDER BY period LIMIT 1",
            (cutoff,),
        ).fetchone()
        if row is None:
            return None
        period = str(row["period"])
        connection = self._segment(period)
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        verdict = connection.execute("PRAGMA quick_check").fetchone()
        if verdict is None or str(verdict[0]) != "ok":
            raise sqlite3.DatabaseError(
                f"canonical telemetry segment {period} failed quick_check"
            )
        path = self.root / str(row["relative_path"])
        file_digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                file_digest.update(block)
        sha256 = file_digest.hexdigest()
        sealed_at = time.time()
        with self._catalog:
            self._catalog.execute(
                "UPDATE ledger_segments SET sealed_at=?,sha256=? WHERE period=?",
                (sealed_at, sha256, period),
            )
            self._catalog.execute(
                "INSERT INTO segment_seals(period,sealed_at,sha256) VALUES(?,?,?)",
                (period, sealed_at, sha256),
            )
        return {"period": period, "sealed_at": sealed_at, "sha256": sha256}

    def storage_status(self) -> dict[str, Any]:
        files = [path for path in self.root.rglob("*") if path.is_file()]
        bytes_used = sum(path.stat().st_size for path in files)
        usage = shutil.disk_usage(self.root)
        warning = usage.free < max(2 * 1024**3, int(usage.total * 0.05))
        segments = self._catalog.execute(
            "SELECT COUNT(*) count,SUM(sealed_at IS NOT NULL) sealed,"
            "MIN(first_observed_at) oldest,MAX(last_observed_at) newest "
            "FROM ledger_segments"
        ).fetchone()
        counts = {
            name: int(self._catalog.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] or 0)
            for name, table in (
                ("dirty_days", "rollup_dirty_days"),
                ("rolled_days", "rollup_days"),
                ("dirty_hours", "rollup_dirty_hours"),
                ("rolled_hours", "rollup_hours"),
            )
        }
        return {
            "bytes": bytes_used,
            "disk_free_bytes": usage.free,
            "disk_total_bytes": usage.total,
            "disk_pressure": warning,
            "segments": int(segments["count"] or 0),
            "sealed_segments": int(segments["sealed"] or 0),
            "oldest_at": segments["oldest"],
            "newest_at": segments["newest"],
            **counts,
            "retention": "forever",
            "automatic_deletion": False,
        }
