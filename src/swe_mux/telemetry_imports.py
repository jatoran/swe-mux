"""Resumable, non-destructive import of legacy telemetry into the canonical ledger.

Every importer reads one legacy stream in `mux.db` past a durable cursor, reduces
the rows through the same reducer live evidence goes through, and advances the
cursor. Nothing here deletes or rewrites a legacy row: the legacy store stays the
system of record for its own tables, and keeps reconciling transcripts after the
ledger has caught up, so each importer is called again periodically and picks up
whatever was appended since - `completed` means "caught up on this pass", not
"never again".
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .models import MuxEvent
from .telemetry_schema import digest

if TYPE_CHECKING:
    pass

_MAX_BATCH = 5000


class LegacyImportMixin:
    """Legacy stream importers, mixed into `CanonicalTelemetryLedger`."""

    if TYPE_CHECKING:
        _catalog: sqlite3.Connection

        def record_events(self, items: Iterable[tuple[MuxEvent, Mapping[str, Any]]]) -> int: ...

    @staticmethod
    def _legacy_call_id(source_identity: str, session_id: str, kind: str) -> str | None:
        prefix = f"native:{session_id}:{kind}:"
        return source_identity[len(prefix) :] if source_identity.startswith(prefix) else None

    def _legacy_import_state(
        self, database: Path, stream: str
    ) -> tuple[str, str, sqlite3.Row | None]:
        source_path = str(database.resolve())
        source_id = digest(f"{source_path.casefold()}\0{stream}")
        state = self._catalog.execute(
            "SELECT cursor_rowid,imported_rows,completed FROM legacy_imports WHERE source_id=?",
            (source_id,),
        ).fetchone()
        return source_id, source_path, state

    def _save_legacy_import(
        self,
        *,
        source_id: str,
        source_path: str,
        cursor: int,
        previous_imported: int,
        processed: int,
        completed: bool,
    ) -> None:
        self._catalog.execute(
            "INSERT INTO legacy_imports"
            "(source_id,source_path,cursor_rowid,imported_rows,completed,updated_at) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET "
            "cursor_rowid=excluded.cursor_rowid,imported_rows=excluded.imported_rows,"
            "completed=excluded.completed,updated_at=excluded.updated_at",
            (
                source_id,
                source_path,
                cursor,
                previous_imported + processed,
                int(completed),
                time.time(),
            ),
        )
        self._catalog.commit()

    @staticmethod
    def _read_legacy_rows(database: Path, sql: str, args: tuple[Any, ...]) -> list[sqlite3.Row]:
        source = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
        source.row_factory = sqlite3.Row
        try:
            return source.execute(sql, args).fetchall()
        finally:
            source.close()

    def _finish_import(
        self,
        *,
        source_id: str,
        source_path: str,
        state: sqlite3.Row | None,
        cursor: int,
        rows: list[sqlite3.Row],
        bounded: int,
        batch: list[tuple[MuxEvent, Mapping[str, Any]]],
    ) -> dict[str, Any]:
        inserted = self.record_events(batch)
        next_cursor = int(rows[-1]["legacy_rowid"]) if rows else cursor
        completed = len(rows) < bounded
        previous_imported = int(state["imported_rows"]) if state is not None else 0
        self._save_legacy_import(
            source_id=source_id,
            source_path=source_path,
            cursor=next_cursor,
            previous_imported=previous_imported,
            processed=len(rows),
            completed=completed,
        )
        return {"imported": inserted, "cursor": next_cursor, "completed": completed}

    def import_legacy_batch(self, database: Path, *, batch_size: int = 500) -> dict[str, Any]:
        """Incrementally preserve old tool rows without deleting or rewriting them."""

        source_id, source_path, state = self._legacy_import_state(database, "tool_events")
        cursor = int(state["cursor_rowid"]) if state is not None else 0
        bounded = max(1, min(batch_size, _MAX_BATCH))
        rows = self._read_legacy_rows(
            database,
            "SELECT t.rowid legacy_rowid,t.*,COALESCE(h.external,0) external,"
            "h.native_id,h.transcript_path,h.spawned_at FROM tool_events t "
            "LEFT JOIN history h ON h.id=t.agent_run_id WHERE t.rowid>? "
            "ORDER BY t.rowid LIMIT ?",
            (cursor, bounded),
        )
        batch: list[tuple[MuxEvent, Mapping[str, Any]]] = []
        for row in rows:
            kind = str(row["kind"])
            call_id = self._legacy_call_id(
                str(row["source_identity"]), str(row["session_id"]), kind
            )
            payload: dict[str, Any] = {
                "tool": str(row["raw_tool"]),
                "call_id": call_id,
                "success": row["success"],
                "exit_code": row["exit_code"],
                "duration_ms": row["duration_ms"],
                "parser_version": row["parser_version"],
                "legacy_source_identity": row["source_identity"],
            }
            if kind == "skill_invoked":
                payload["skill"] = row["explicit_skill"]
            batch.append(
                (
                    MuxEvent(
                        float(row["observed_at"]),
                        str(row["session_id"]),
                        str(row["source"]),
                        kind,
                        payload,
                        seq=int(row["event_seq"] or row["legacy_rowid"]),
                    ),
                    {
                        "session_id": str(row["session_id"]),
                        "run_id": str(row["agent_run_id"] or row["session_id"]),
                        "native_conversation_id": row["native_id"],
                        "turn_id": None,
                        "agent_id": "root",
                        "project_id": row["project_id"],
                        "backend": str(row["backend"]),
                        "model": row["model"],
                        "origin": "imported" if row["external"] else "mux_owned",
                        "source_locator": row["transcript_path"],
                        "run_started_at": row["spawned_at"] or row["observed_at"],
                    },
                )
            )
        return self._finish_import(
            source_id=source_id,
            source_path=source_path,
            state=state,
            cursor=cursor,
            rows=rows,
            bounded=bounded,
            batch=batch,
        )

    def import_legacy_runs_batch(
        self, database: Path, *, batch_size: int = 500
    ) -> dict[str, Any]:
        """Copy history identities and lifetimes into canonical run entities."""

        source_id, source_path, state = self._legacy_import_state(database, "history")
        cursor = int(state["cursor_rowid"]) if state is not None else 0
        bounded = max(1, min(batch_size, _MAX_BATCH))
        rows = self._read_legacy_rows(
            database,
            "SELECT rowid legacy_rowid,id,note_id,native_id,backend,project_id,model,"
            "external,transcript_path,spawned_at,exited_at,exit_reason,tokens_in,tokens_out,"
            "tokens_cache_read,tokens_cache_write,cost_usd,final_context_pct,"
            "peak_context_pct,measurement_source FROM history "
            "WHERE rowid>? AND agent_visible=1 ORDER BY rowid LIMIT ?",
            (cursor, bounded),
        )
        batch: list[tuple[MuxEvent, Mapping[str, Any]]] = []
        for row in rows:
            dimensions = {
                "session_id": str(row["note_id"] or row["id"]),
                "run_id": str(row["id"]),
                "native_conversation_id": row["native_id"],
                "turn_id": None,
                "agent_id": "root",
                "project_id": row["project_id"],
                "backend": str(row["backend"]),
                "model": row["model"],
                "model_is_final_only": True,
                "origin": "imported" if row["external"] else "mux_owned",
                "source_locator": row["transcript_path"],
                "run_started_at": row["spawned_at"],
                "input_tokens": row["tokens_in"],
                "output_tokens": row["tokens_out"],
                "cache_read_tokens": row["tokens_cache_read"],
                "cache_write_tokens": row["tokens_cache_write"],
                "cost_usd": row["cost_usd"],
                "final_context_pct": row["final_context_pct"],
                "peak_context_pct": row["peak_context_pct"],
                "measurement_source": row["measurement_source"],
            }
            batch.append(
                (
                    MuxEvent(
                        float(row["spawned_at"]),
                        str(row["note_id"] or row["id"]),
                        "legacy",
                        "agent_run_started",
                        {"legacy_history_id": row["id"]},
                        seq=int(row["legacy_rowid"]),
                    ),
                    dimensions,
                )
            )
            if row["exited_at"] is not None:
                batch.append(
                    (
                        MuxEvent(
                            float(row["exited_at"]),
                            str(row["note_id"] or row["id"]),
                            "legacy",
                            "agent_run_ended",
                            {
                                "legacy_history_id": row["id"],
                                "reason": row["exit_reason"],
                            },
                            seq=int(row["legacy_rowid"]),
                        ),
                        dimensions,
                    )
                )
        return self._finish_import(
            source_id=source_id,
            source_path=source_path,
            state=state,
            cursor=cursor,
            rows=rows,
            bounded=bounded,
            batch=batch,
        )

    def import_legacy_verifications_batch(
        self, database: Path, *, batch_size: int = 500
    ) -> dict[str, Any]:
        """Preserve parsed test outcomes from Tier 0 as canonical verification rows."""

        source_id, source_path, state = self._legacy_import_state(database, "tier0_tests")
        cursor = int(state["cursor_rowid"]) if state is not None else 0
        bounded = max(1, min(batch_size, _MAX_BATCH))
        rows = self._read_legacy_rows(
            database,
            "SELECT f.rowid legacy_rowid,f.*,h.backend,h.model,h.external,"
            "h.native_id,h.transcript_path,h.spawned_at FROM tier0_facts f "
            "LEFT JOIN history h ON h.id=f.agent_run_id "
            "WHERE f.rowid>? AND f.kind='test_result' ORDER BY f.rowid LIMIT ?",
            (cursor, bounded),
        )
        batch: list[tuple[MuxEvent, Mapping[str, Any]]] = []
        for row in rows:
            try:
                detail = json.loads(str(row["detail_json"] or "{}"))
            except json.JSONDecodeError:
                detail = {}
            outcome = detail.get("test_outcome") if isinstance(detail, dict) else None
            if not isinstance(outcome, dict):
                continue
            call_id = str(row["call_id"] or f"tier0:{row['id']}")
            batch.append(
                (
                    MuxEvent(
                        float(row["created_at"]),
                        str(row["session_id"]),
                        "tier0",
                        "tool_result",
                        {
                            "tool": detail.get("tool") or "test",
                            "call_id": call_id,
                            "success": detail.get("success"),
                            "exit_code": detail.get("exit_code"),
                            "test_outcome": outcome,
                            "parser_version": "tier0-v2",
                        },
                        seq=int(row["source_seq"] or row["legacy_rowid"]),
                    ),
                    {
                        "session_id": str(row["session_id"]),
                        "run_id": str(row["agent_run_id"] or row["session_id"]),
                        "native_conversation_id": row["native_id"],
                        "turn_id": None,
                        "agent_id": "root",
                        "project_id": row["project_id"],
                        "backend": str(row["backend"] or "unknown"),
                        "model": row["model"],
                        "origin": "imported" if row["external"] else "mux_owned",
                        "source_locator": row["transcript_path"],
                        "run_started_at": row["spawned_at"] or row["created_at"],
                    },
                )
            )
        return self._finish_import(
            source_id=source_id,
            source_path=source_path,
            state=state,
            cursor=cursor,
            rows=rows,
            bounded=bounded,
            batch=batch,
        )

    def import_legacy_turns_batch(
        self, database: Path, *, batch_size: int = 500
    ) -> dict[str, Any]:
        """Reconstruct exact completed turns from the durable transition timeline."""

        source_id, source_path, state = self._legacy_import_state(database, "status_turns")
        cursor = int(state["cursor_rowid"]) if state is not None else 0
        bounded = max(1, min(batch_size, _MAX_BATCH))
        rows = self._read_legacy_rows(
            database,
            "SELECT s.rowid legacy_rowid,s.session_id,s.agent_run_id,s.ts,s.entry_json,"
            "h.backend,h.model,h.project_id,h.external,h.native_id,h.transcript_path,"
            "h.spawned_at,(SELECT MAX(opened.ts) FROM status_timeline opened "
            "WHERE opened.agent_run_id=s.agent_run_id AND opened.ts<=s.ts "
            "AND opened.kind='transition' "
            "AND json_extract(opened.entry_json,'$.previous')='idle' "
            "AND json_extract(opened.entry_json,'$.state')='working') turn_started_at "
            "FROM status_timeline s LEFT JOIN history h ON h.id=s.agent_run_id "
            "WHERE s.rowid>? AND s.kind='transition' "
            "AND json_extract(s.entry_json,'$.turn_seq') IS NOT NULL "
            "AND json_extract(s.entry_json,'$.state') IN ('idle','exited','crashed') "
            "ORDER BY s.rowid LIMIT ?",
            (cursor, bounded),
        )
        batch: list[tuple[MuxEvent, Mapping[str, Any]]] = []
        for row in rows:
            if row["turn_started_at"] is None or row["backend"] is None:
                continue
            entry = json.loads(str(row["entry_json"]))
            turn_ordinal = int(entry["turn_seq"])
            dimensions = {
                "session_id": str(row["session_id"]),
                "run_id": str(row["agent_run_id"]),
                "native_conversation_id": row["native_id"],
                "turn_id": None,
                "turn_ordinal": turn_ordinal,
                "agent_id": "root",
                "project_id": row["project_id"],
                "backend": str(row["backend"]),
                "model": None,
                "origin": "imported" if row["external"] else "mux_owned",
                "source_locator": row["transcript_path"],
                "run_started_at": row["spawned_at"] or row["turn_started_at"],
            }
            common = {
                "turn_epoch": turn_ordinal,
                "legacy_status_rowid": row["legacy_rowid"],
            }
            batch.append(
                (
                    MuxEvent(
                        float(row["turn_started_at"]),
                        str(row["session_id"]),
                        "legacy_timeline",
                        "turn_started",
                        common,
                        seq=int(row["legacy_rowid"]),
                    ),
                    dimensions,
                )
            )
            batch.append(
                (
                    MuxEvent(
                        float(row["ts"]),
                        str(row["session_id"]),
                        "legacy_timeline",
                        "turn_ended",
                        {
                            **common,
                            "duration_ms": (
                                float(row["ts"]) - float(row["turn_started_at"])
                            )
                            * 1000,
                            "outcome": entry.get("state"),
                        },
                        seq=int(row["legacy_rowid"]),
                    ),
                    dimensions,
                )
            )
        return self._finish_import(
            source_id=source_id,
            source_path=source_path,
            state=state,
            cursor=cursor,
            rows=rows,
            bounded=bounded,
            batch=batch,
        )

    def import_legacy_compactions_batch(
        self, database: Path, *, batch_size: int = 500
    ) -> dict[str, Any]:
        source_id, source_path, state = self._legacy_import_state(database, "compactions")
        cursor = int(state["cursor_rowid"]) if state is not None else 0
        bounded = max(1, min(batch_size, _MAX_BATCH))
        rows = self._read_legacy_rows(
            database,
            "SELECT c.rowid legacy_rowid,c.*,COALESCE(h.external,0) external,"
            "h.native_id,h.transcript_path,h.spawned_at FROM context_compactions c "
            "LEFT JOIN history h ON h.id=c.agent_run_id WHERE c.rowid>? "
            "ORDER BY c.rowid LIMIT ?",
            (cursor, bounded),
        )
        batch: list[tuple[MuxEvent, Mapping[str, Any]]] = []
        for row in rows:
            batch.append(
                (
                    MuxEvent(
                        float(row["observed_at"]),
                        str(row["session_id"]),
                        str(row["source"]),
                        "context_compacted",
                        {
                            "compaction_id": row["id"],
                            "capability": row["capability"],
                            "confidence": row["confidence"],
                            "parser_version": row["parser_version"],
                        },
                        seq=int(row["event_seq"] or row["legacy_rowid"]),
                    ),
                    {
                        "session_id": str(row["session_id"]),
                        "run_id": str(row["agent_run_id"] or row["session_id"]),
                        "native_conversation_id": row["native_id"],
                        "turn_id": None,
                        "agent_id": "root",
                        "project_id": row["project_id"],
                        "backend": str(row["backend"]),
                        "model": row["model"],
                        "origin": "imported" if row["external"] else "mux_owned",
                        "source_locator": row["transcript_path"],
                        "run_started_at": row["spawned_at"] or row["observed_at"],
                    },
                )
            )
        return self._finish_import(
            source_id=source_id,
            source_path=source_path,
            state=state,
            cursor=cursor,
            rows=rows,
            bounded=bounded,
            batch=batch,
        )

    def legacy_import_status(self) -> list[dict[str, Any]]:
        rows = self._catalog.execute(
            "SELECT source_path,cursor_rowid,imported_rows,completed,updated_at "
            "FROM legacy_imports ORDER BY source_path"
        ).fetchall()
        return [dict(row) for row in rows]
