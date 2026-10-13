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

A transcript file is read incrementally: the record keeps a cursor (byte offset,
absolute line, and fingerprints of the file's head and of the bytes just before the
offset; `telemetry_cursor.py`), the next pass parses only the complete lines appended
since, and its counts add to the ones already recorded. A file that shrank or whose
fingerprints no longer match was replaced and is read again from the start. Never
mtime: Windows does not move it while the provider holds the file open. A
store-backed conversation (opencode) keeps the watermark model, because its store
answers "changed since" itself.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .harness import conversation_store_path, require_backend
from .models import MuxEvent
from .opencode_store import conversation_records
from .opencode_store import conversation_watermark as store_watermark
from .operational_telemetry import (
    TOOL_PARSER_VERSIONS,
    scan_native_telemetry,
    scan_native_telemetry_since,
)
from .telemetry_cursor import ScanCursor, resumed_cursor
from .telemetry_schema import digest

log = logging.getLogger(__name__)

RECONCILE_VERSION_SUFFIX = "+canonical-v1"


def native_parser_version(backend: str) -> str:
    """The legacy dialect parser revision plus this reducer's own revision."""

    base = TOOL_PARSER_VERSIONS.get(backend, f"{backend}-unversioned")
    return f"{base}{RECONCILE_VERSION_SUFFIX}"


def _call_id(source_identity: str, session_id: str, kind: str) -> str | None:
    prefix = f"native:{session_id}:{kind}:"
    return source_identity[len(prefix) :] if source_identity.startswith(prefix) else None


#: Items one source line can yield before its sequence numbers would collide with the
#: next line's. A Claude record carries at most a few dozen tool blocks.
_ITEMS_PER_LINE = 1024
#: Evidence written per transaction. A first read of a long transcript yields
#: thousands of events; one transaction for all of them would hold the ledger's
#: writer for the whole batch while live ingestion waited behind it.
RECONCILE_EVENT_CHUNK = 256


class _Sequencer:
    """Sequence numbers that name a record's position in the transcript.

    The sequence is part of an evidence row's identity. Numbering items 1..N within
    one read made the same record a different observation whenever a read started
    somewhere else - every pass over a file longer than the tail window, whose start
    moved as the file grew - so each pass re-inserted all of it as new evidence.
    A number derived from the record's absolute line is the same on every read.
    """

    def __init__(self) -> None:
        self._used: dict[int, int] = {}

    def next(self, line: Any) -> int:
        position = int(line) if isinstance(line, int) and not isinstance(line, bool) else 0
        ordinal = self._used.get(position, 0)
        self._used[position] = ordinal + 1
        return position * _ITEMS_PER_LINE + ordinal


def scan_to_events(
    scan: Mapping[str, Any], *, session_id: str, run_id: str
) -> list[MuxEvent]:
    """Turn one parser scan into the reducer's event vocabulary, in record order."""

    events: list[MuxEvent] = []
    sequencer = _Sequencer()
    for item in scan.get("tools", []):
        sequence = sequencer.next(item.get("line"))
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
            sequence = sequencer.next(item.get("line"))
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
        sequence = sequencer.next(item.get("line"))
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
        _write_lock: threading.RLock

        def record_events(self, items: Iterable[tuple[MuxEvent, Mapping[str, Any]]]) -> int: ...

        def _segment(self, period: str) -> sqlite3.Connection: ...

    def _recorded_tool_name(self, run_id: str, call_id: str) -> str | None:
        """The name the ledger holds for a reconciled call, by its native id."""

        # The identity `_tool_call_identity` gives a reconciled call: root agent,
        # model layer, the native call id.
        tool_call_id = digest(f"{run_id}\0root\0model\0{call_id}")
        location = self._catalog.execute(
            "SELECT period FROM entity_locations WHERE entity_kind='tool_call' AND entity_id=?",
            (tool_call_id,),
        ).fetchone()
        if location is None:
            return None
        row = self._segment(str(location["period"])).execute(
            "SELECT raw_name FROM telemetry_tool_calls WHERE tool_call_id=?", (tool_call_id,)
        ).fetchone()
        return str(row["raw_name"]) if row is not None and row["raw_name"] else None

    def _name_results_from_ledger(self, scan: Mapping[str, Any], run_id: str) -> None:
        """Name results whose call an earlier pass read, from the call the ledger holds.

        An incremental read starts after the call's own record, so the parser could
        not name its result. A whole read would have, and the name is part of the
        evidence payload and so of its identity: naming it here keeps a result the
        same observation however the file was split across reads.
        """

        for item in scan.get("tools", []):
            if item.get("kind") != "tool_result" or item.get("named") is not False:
                continue
            call_id = _call_id(str(item.get("source_identity") or ""), run_id, "tool_result")
            name = self._recorded_tool_name(run_id, call_id) if call_id else None
            if name is not None:
                item["raw_tool"] = name

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

    def _reconciliation_state(self, run_id: str) -> sqlite3.Row | None:
        row: sqlite3.Row | None = self._catalog.execute(
            "SELECT * FROM native_reconciliations WHERE run_id=?", (run_id,)
        ).fetchone()
        return row

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
        cursor: ScanCursor | None = None,
        prior: sqlite3.Row | None = None,
    ) -> None:
        """Record one reconciliation; with `prior`, the scan's counts add to its counts."""

        tools = list(scan.get("tools", [])) if scan else []
        counts: tuple[int, ...] = (
            int(scan.get("recognized", 0)) if scan else 0,
            int(scan.get("unknown", 0)) if scan else 0,
            len(tools),
            sum(1 for item in tools if item.get("explicit_skill")),
            len(scan.get("compactions", [])) if scan else 0,
        )
        if prior is not None:
            counts = tuple(
                count + int(prior[column] or 0)
                for count, column in zip(
                    counts,
                    ("recognized", "unknown", "tool_events", "skill_events", "compaction_events"),
                    strict=True,
                )
            )
            inserted += int(prior["inserted"] or 0)
        with self._write_lock:
            self._catalog.execute(
                "INSERT INTO native_reconciliations(run_id,backend,source_locator,"
                "watermark_first,watermark_second,parser_version,status,recognized,unknown,"
                "tool_events,skill_events,compaction_events,inserted,diagnostic,reconciled_at,"
                "cursor_offset,cursor_line,cursor_head_sha256,cursor_tail_sha256,"
                "cursor_truncated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(run_id) DO UPDATE SET "
                "source_locator=excluded.source_locator,watermark_first=excluded.watermark_first,"
                "watermark_second=excluded.watermark_second,parser_version=excluded.parser_version,"
                "status=excluded.status,recognized=excluded.recognized,unknown=excluded.unknown,"
                "tool_events=excluded.tool_events,skill_events=excluded.skill_events,"
                "compaction_events=excluded.compaction_events,inserted=excluded.inserted,"
                "diagnostic=excluded.diagnostic,reconciled_at=excluded.reconciled_at,"
                "cursor_offset=excluded.cursor_offset,cursor_line=excluded.cursor_line,"
                "cursor_head_sha256=excluded.cursor_head_sha256,"
                "cursor_tail_sha256=excluded.cursor_tail_sha256,"
                "cursor_truncated=excluded.cursor_truncated",
                (
                    str(row["id"]),
                    str(row["backend"]),
                    row.get("transcript_path"),
                    first,
                    second,
                    parser_version,
                    status,
                    *counts,
                    inserted,
                    diagnostic or (scan.get("diagnostic") if scan else None),
                    time.time(),
                    cursor.offset if cursor else None,
                    cursor.line if cursor else None,
                    cursor.head_sha256 if cursor else None,
                    cursor.tail_sha256 if cursor else None,
                    int(cursor.truncated) if cursor else 0,
                ),
            )
            self._catalog.commit()

    def _record_unavailable(
        self, row: Mapping[str, Any], parser_version: str, diagnostic: str
    ) -> None:
        """Say a run's native record could not be read, once rather than every pass."""

        state = self._reconciliation_state(str(row["id"]))
        if (
            state is not None
            and state["status"] == "unavailable"
            and state["diagnostic"] == diagnostic
            and state["parser_version"] == parser_version
        ):
            return
        self._save_reconciliation(
            row,
            parser_version=parser_version,
            status="unavailable",
            first=None,
            second=None,
            scan=None,
            inserted=0,
            diagnostic=diagnostic,
        )

    def _record_scan(
        self, row: Mapping[str, Any], scan: Mapping[str, Any], backend_name: str
    ) -> int:
        """Feed one scan to the reducer in bounded transactions; the rows inserted."""

        native_id = str(row.get("native_id") or "") or None
        run_id = str(row["id"])
        raw_path = row.get("transcript_path")
        store = conversation_store_path(backend_name)
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
            "source_locator": str(raw_path) if raw_path and store is None else f"store:{store}",
            "run_started_at": row.get("spawned_at"),
        }
        self._name_results_from_ledger(scan, run_id)
        events = scan_to_events(scan, session_id=session_id, run_id=run_id)
        inserted = 0
        for start in range(0, len(events), RECONCILE_EVENT_CHUNK):
            chunk = events[start : start + RECONCILE_EVENT_CHUNK]
            inserted += self.record_events((event, dimensions) for event in chunk)
        return inserted

    def _reconcile_store_row(
        self, row: Mapping[str, Any], backend_name: str, parser_version: str, store: Path
    ) -> tuple[str, int]:
        run_id = str(row["id"])
        native_id = str(row.get("native_id") or "") or None
        try:
            pair = store_watermark(store, native_id or "")
            if pair is None:
                raise OSError("conversation is not in the store")
            first, second = pair
            if self._reconciliation_current(run_id, first, second, parser_version):
                return "skipped", 0
            records = conversation_records(store, native_id or "")
            scan = dict(
                scan_native_telemetry(
                    records, require_backend(backend_name), run_id, row.get("project_id"), None
                )
            )
        except OSError as exc:
            self._record_unavailable(row, parser_version, str(exc)[:500])
            return "errors", 0
        except ValueError as exc:
            self._save_reconciliation(
                row,
                parser_version=parser_version,
                status="error",
                first=None,
                second=None,
                scan=None,
                inserted=0,
                diagnostic=str(exc)[:500],
            )
            return "errors", 0
        scan["parser_version"] = parser_version
        inserted = self._record_scan(row, scan, backend_name)
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
        return "scanned", inserted

    def _continuable(
        self, state: sqlite3.Row | None, parser_version: str, path: Path
    ) -> tuple[ScanCursor | None, bool]:
        """The cursor a pass may continue, and whether it was read from the catalog.

        A reconciliation recorded before cursors existed holds only the size its
        whole-file read saw; everything up to that size was read then.
        """

        if state is None or state["status"] != "ready" or state["parser_version"] != parser_version:
            return None, False
        if state["cursor_offset"] is not None:
            return (
                ScanCursor(
                    offset=int(state["cursor_offset"]),
                    line=int(state["cursor_line"] or 0),
                    head_sha256=state["cursor_head_sha256"],
                    tail_sha256=state["cursor_tail_sha256"],
                    truncated=bool(state["cursor_truncated"]),
                ),
                True,
            )
        if state["watermark_second"] is not None:
            return resumed_cursor(path, int(state["watermark_second"])), False
        return None, False

    def _reconcile_file_row(
        self, row: Mapping[str, Any], backend_name: str, parser_version: str
    ) -> tuple[str, int]:
        run_id = str(row["id"])
        raw_path = row.get("transcript_path")
        if not raw_path:
            self._record_unavailable(row, parser_version, "no transcript path")
            return "errors", 0
        path = Path(str(raw_path))
        state = self._reconciliation_state(run_id)
        try:
            cursor, stored = self._continuable(state, parser_version, path)
            scan, read = scan_native_telemetry_since(
                path, require_backend(backend_name), run_id, cursor
            )
            mtime_ns = path.stat().st_mtime_ns
        except OSError as exc:
            self._record_unavailable(row, parser_version, str(exc)[:500])
            return "errors", 0
        except ValueError as exc:
            self._save_reconciliation(
                row,
                parser_version=parser_version,
                status="error",
                first=None,
                second=None,
                scan=None,
                inserted=0,
                diagnostic=str(exc)[:500],
            )
            return "errors", 0
        if read.reason == "unchanged" and stored:
            return "skipped", 0
        scan["parser_version"] = parser_version
        inserted = self._record_scan(row, scan, backend_name) if read.records else 0
        self._save_reconciliation(
            row,
            parser_version=parser_version,
            status="ready",
            first=mtime_ns,
            second=read.cursor.offset,
            scan=scan,
            inserted=inserted,
            diagnostic=None,
            cursor=read.cursor,
            prior=None if read.rescanned else state,
        )
        if read.rescanned and cursor is not None:
            log.info(
                "canonical telemetry re-read %s from the start (%s)",
                run_id,
                read.reason,
                extra={"run_id": run_id, "reason": read.reason, "bytes": read.bytes_read},
            )
        return ("skipped" if read.reason == "unchanged" else "scanned"), inserted

    def reconcile_native_rows(self, rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
        """Reconcile every changed conversation in `rows` into the reducer.

        Rows are the history inventory (`HistoryIndex.telemetry_history_rows`). A
        transcript is read past the cursor its last reconciliation kept
        (`telemetry_cursor.py`), so a pass costs what was appended since the last
        one; a store-backed conversation whose watermark and parser revision match is
        skipped. What is read is recorded as `reconciled_transcript` evidence, which
        ranks below live transcript and native evidence and above legacy imports, so
        it fills what the observer missed and overrides nothing it saw.
        """

        summary = {"scanned": 0, "skipped": 0, "errors": 0, "inserted": 0}
        for row in rows:
            backend_name = str(row["backend"])
            parser_version = native_parser_version(backend_name)
            store = conversation_store_path(backend_name)
            if store is not None:
                outcome, inserted = self._reconcile_store_row(
                    row, backend_name, parser_version, store
                )
            else:
                outcome, inserted = self._reconcile_file_row(row, backend_name, parser_version)
            summary[outcome] += 1
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
