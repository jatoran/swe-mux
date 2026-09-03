"""Direct reconciliation of native provider stores into the canonical reducer.

The legacy operational store parses every harness's transcript or conversation
store into `tool_events`, and the ledger used to receive that parse only by
importing those rows. This mixin runs the same parser (`scan_native_telemetry`,
which is the one place each dialect's record shapes are known) and feeds the
result straight into the canonical reducer as `reconciled_transcript` evidence,
so a conversation the observer never saw live - an imported one, or one whose
hooks were off - still reaches the ledger without the legacy hop.

Every reconciliation is recorded per run with the watermark it read, the parser
revision that read it, and what it found, which is what makes "unchanged since last
time" answerable and what the audit tool uses to compare the ledger with the
provider's own record call by call.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .harness import conversation_store_path, require_backend
from .models import MuxEvent
from .opencode_store import conversation_records
from .opencode_store import conversation_watermark as store_watermark
from .operational_telemetry import TOOL_PARSER_VERSIONS, scan_native_telemetry

RECONCILE_VERSION_SUFFIX = "+canonical-v1"


def native_parser_version(backend: str) -> str:
    """The legacy dialect parser revision plus this reducer's own revision."""

    base = TOOL_PARSER_VERSIONS.get(backend, f"{backend}-unversioned")
    return f"{base}{RECONCILE_VERSION_SUFFIX}"


def _call_id(source_identity: str, session_id: str, kind: str) -> str | None:
    prefix = f"native:{session_id}:{kind}:"
    return source_identity[len(prefix) :] if source_identity.startswith(prefix) else None


def scan_to_events(
    scan: Mapping[str, Any], *, session_id: str, run_id: str
) -> list[MuxEvent]:
    """Turn one parser scan into the reducer's event vocabulary, in record order."""

    events: list[MuxEvent] = []
    sequence = 0
    for item in scan.get("tools", []):
        sequence += 1
        kind = str(item.get("kind"))
        call_id = _call_id(str(item.get("source_identity") or ""), run_id, kind)
        payload: dict[str, Any] = {
            "tool": str(item.get("raw_tool") or "tool"),
            "call_id": call_id,
            "parser_version": scan.get("parser_version"),
        }
        if kind == "tool_result":
            payload["success"] = item.get("success")
            payload["exit_code"] = item.get("exit_code")
            payload["duration_ms"] = item.get("duration_ms")
        events.append(
            MuxEvent(
                float(item["observed_at"]),
                session_id,
                "reconciled_transcript",
                kind,
                payload,
                seq=sequence,
            )
        )
        skill = item.get("explicit_skill")
        if kind == "tool_use" and isinstance(skill, str) and skill:
            sequence += 1
            events.append(
                MuxEvent(
                    float(item["observed_at"]),
                    session_id,
                    "reconciled_transcript",
                    "skill_invoked",
                    {
                        "skill": skill,
                        "call_id": call_id,
                        "invocation_trigger": "explicit",
                        "parser_version": scan.get("parser_version"),
                    },
                    seq=sequence,
                )
            )
    for item in scan.get("compactions", []):
        sequence += 1
        identity = str(item.get("compaction_id") or item.get("source_identity") or "")
        events.append(
            MuxEvent(
                float(item["observed_at"]),
                session_id,
                "reconciled_transcript",
                "context_compacted",
                {
                    "compaction_id": identity,
                    "capability": "native_record",
                    "confidence": "explicit",
                    "parser_version": scan.get("parser_version"),
                },
                seq=sequence,
            )
        )
    return events


class NativeReconcileMixin:
    """Direct native-store reconciliation, mixed into `CanonicalTelemetryLedger`."""

    if TYPE_CHECKING:
        _catalog: sqlite3.Connection

        def record_events(self, items: Iterable[tuple[MuxEvent, Mapping[str, Any]]]) -> int: ...

    def _reconciliation_current(
        self, run_id: str, first: int, second: int, parser_version: str
    ) -> bool:
        row = self._catalog.execute(
            "SELECT watermark_first,watermark_second,parser_version,status "
            "FROM native_reconciliations WHERE run_id=?",
            (run_id,),
        ).fetchone()
        return bool(
            row
            and row["watermark_first"] == first
            and row["watermark_second"] == second
            and row["parser_version"] == parser_version
            and row["status"] == "ready"
        )

    def _save_reconciliation(
        self,
        row: Mapping[str, Any],
        *,
        parser_version: str,
        status: str,
        first: int | None,
        second: int | None,
        scan: Mapping[str, Any] | None,
        inserted: int,
        diagnostic: str | None,
    ) -> None:
        tools = list(scan.get("tools", [])) if scan else []
        self._catalog.execute(
            "INSERT INTO native_reconciliations(run_id,backend,source_locator,watermark_first,"
            "watermark_second,parser_version,status,recognized,unknown,tool_events,skill_events,"
            "compaction_events,inserted,diagnostic,reconciled_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET "
            "source_locator=excluded.source_locator,watermark_first=excluded.watermark_first,"
            "watermark_second=excluded.watermark_second,parser_version=excluded.parser_version,"
            "status=excluded.status,recognized=excluded.recognized,unknown=excluded.unknown,"
            "tool_events=excluded.tool_events,skill_events=excluded.skill_events,"
            "compaction_events=excluded.compaction_events,inserted=excluded.inserted,"
            "diagnostic=excluded.diagnostic,reconciled_at=excluded.reconciled_at",
            (
                str(row["id"]),
                str(row["backend"]),
                row.get("transcript_path"),
                first,
                second,
                parser_version,
                status,
                int(scan.get("recognized", 0)) if scan else 0,
                int(scan.get("unknown", 0)) if scan else 0,
                len(tools),
                sum(1 for item in tools if item.get("explicit_skill")),
                len(scan.get("compactions", [])) if scan else 0,
                inserted,
                diagnostic or (scan.get("diagnostic") if scan else None),
                time.time(),
            ),
        )
        self._catalog.commit()

    def reconcile_native_rows(self, rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
        """Reconcile every changed conversation in `rows` into the reducer.

        Rows are the history inventory (`HistoryIndex.telemetry_history_rows`). A
        conversation whose watermark and parser revision match its last
        reconciliation is skipped; everything else is parsed and recorded as
        `reconciled_transcript` evidence, which ranks below live transcript and
        native evidence and above legacy imports, so it fills what the observer
        missed and overrides nothing it saw.
        """

        summary = {"scanned": 0, "skipped": 0, "errors": 0, "inserted": 0}
        for row in rows:
            backend_name = str(row["backend"])
            run_id = str(row["id"])
            parser_version = native_parser_version(backend_name)
            raw_path = row.get("transcript_path")
            path = Path(str(raw_path)) if raw_path else None
            native_id = str(row.get("native_id") or "") or None
            store = conversation_store_path(backend_name)
            try:
                if store is not None:
                    pair = store_watermark(store, native_id or "")
                    if pair is None:
                        raise OSError("conversation is not in the store")
                    first, second = pair
                    source: Path | list[dict[str, Any]] = conversation_records(
                        store, native_id or ""
                    )
                else:
                    if path is None:
                        raise OSError("no transcript path")
                    stat = path.stat()
                    first, second = stat.st_mtime_ns, stat.st_size
                    source = path
            except OSError as exc:
                self._save_reconciliation(
                    row,
                    parser_version=parser_version,
                    status="unavailable",
                    first=None,
                    second=None,
                    scan=None,
                    inserted=0,
                    diagnostic=str(exc)[:500],
                )
                summary["errors"] += 1
                continue
            if self._reconciliation_current(run_id, first, second, parser_version):
                summary["skipped"] += 1
                continue
            try:
                backend = require_backend(backend_name)
                scan = dict(
                    scan_native_telemetry(
                        source, backend, run_id, row.get("project_id"), row.get("model")
                    )
                )
            except (OSError, ValueError) as exc:
                self._save_reconciliation(
                    row,
                    parser_version=parser_version,
                    status="error",
                    first=first,
                    second=second,
                    scan=None,
                    inserted=0,
                    diagnostic=str(exc)[:500],
                )
                summary["errors"] += 1
                continue
            scan["parser_version"] = parser_version
            session_id = str(row.get("note_id") or run_id)
            dimensions = {
                "session_id": session_id,
                "run_id": run_id,
                "native_conversation_id": native_id,
                "turn_id": None,
                "agent_id": "root",
                "project_id": row.get("project_id"),
                "backend": backend_name,
                "model": row.get("model"),
                "origin": "imported" if row.get("external") else "mux_owned",
                "source_locator": str(path) if path is not None else f"store:{store}",
                "run_started_at": row.get("spawned_at"),
            }
            events = scan_to_events(scan, session_id=session_id, run_id=run_id)
            inserted = self.record_events((event, dimensions) for event in events)
            self._save_reconciliation(
                row,
                parser_version=parser_version,
                status="ready",
                first=first,
                second=second,
                scan=scan,
                inserted=inserted,
                diagnostic=None,
            )
            summary["scanned"] += 1
            summary["inserted"] += inserted
        return summary

    def native_reconciliation_status(self) -> dict[str, Any]:
        rows = self._catalog.execute(
            "SELECT backend,parser_version,status,COUNT(*) runs,SUM(recognized) recognized,"
            "SUM(unknown) unknown,SUM(tool_events) tool_events,SUM(inserted) inserted,"
            "MAX(reconciled_at) last_at FROM native_reconciliations "
            "GROUP BY backend,parser_version,status ORDER BY backend,parser_version,status"
        ).fetchall()
        return {
            "by_backend": [dict(row) for row in rows],
            "runs": int(
                self._catalog.execute("SELECT COUNT(*) FROM native_reconciliations").fetchone()[0]
                or 0
            ),
        }

    def native_reconciliation_for(self, run_id: str) -> dict[str, Any] | None:
        row = self._catalog.execute(
            "SELECT * FROM native_reconciliations WHERE run_id=?", (run_id,)
        ).fetchone()
        return dict(row) if row is not None else None
