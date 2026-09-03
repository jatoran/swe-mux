"""Audit one window of the canonical ledger against the providers' own records.

For every run that started in the window, the native transcript or conversation
store is parsed by the same dialect scanner the daemon reconciles with, and each
tool call it names is looked up in the ledger by its native call id. The report says,
call by call, what matched, what the ledger holds that the native record does not
(live-only evidence: hooks and OTel), and what the native record holds that the
ledger does not (the gap reconciliation is there to close). Nothing is estimated
and nothing is written.

    uv run python tools/telemetry_audit_window.py --hours 24
    uv run python tools/telemetry_audit_window.py --hours 24 --data-dir C:/Users/me/.mux --json audit.json

It reads the data directory read-only and can run beside a live daemon.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

from swe_mux.config import Config
from swe_mux.harness import conversation_store_path, require_backend
from swe_mux.opencode_store import conversation_records
from swe_mux.operational_telemetry import scan_native_telemetry
from swe_mux.telemetry_reconcile import _call_id


def _history_rows(database: Path, start: float, end: float) -> list[dict[str, Any]]:
    source = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    try:
        rows = source.execute(
            "SELECT id,note_id,backend,project_id,model,native_id,external,transcript_path,"
            "spawned_at,exited_at FROM history WHERE agent_visible=1 AND spawned_at>=? "
            "AND spawned_at<? ORDER BY spawned_at",
            (start, end),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        source.close()


def _ledger_calls(telemetry_root: Path, run_ids: set[str]) -> dict[str, dict[str, Any]]:
    """Every canonical call for the runs, keyed by (run, native call id)."""

    calls: dict[str, dict[str, Any]] = {}
    if not run_ids:
        return calls
    placeholders = ",".join("?" for _ in run_ids)
    for segment in sorted((telemetry_root / "segments").glob("*.sqlite3")):
        connection = sqlite3.connect(f"file:{segment.as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            for row in connection.execute(
                "SELECT run_id,native_call_id,raw_name,status,invocation_layer,"
                "request_source,result_source,evidence_quality FROM telemetry_tool_calls "
                f"WHERE run_id IN ({placeholders})",
                tuple(run_ids),
            ).fetchall():
                key = f"{row['run_id']}\0{row['native_call_id']}\0{row['invocation_layer']}"
                calls[key] = dict(row)
        finally:
            connection.close()
    return calls


def audit(data_dir: Path, *, start: float, end: float, examples: int) -> dict[str, Any]:
    rows = _history_rows(data_dir / "mux.db", start, end)
    ledger = _ledger_calls(data_dir / "telemetry", {str(row["id"]) for row in rows})
    report: dict[str, Any] = {
        "from": start,
        "to": end,
        "runs": len(rows),
        "runs_with_native_record": 0,
        "runs_without_native_record": 0,
        "native_calls": 0,
        "matched": 0,
        "native_only": 0,
        "ledger_only": 0,
        "by_backend": {},
        "examples": {"native_only": [], "ledger_only": []},
    }
    seen_keys: set[str] = set()
    for row in rows:
        backend = str(row["backend"])
        run_id = str(row["id"])
        per = report["by_backend"].setdefault(
            backend, {"runs": 0, "native_calls": 0, "matched": 0, "native_only": 0}
        )
        per["runs"] += 1
        store = conversation_store_path(backend)
        try:
            if store is not None:
                source: Path | list[dict[str, Any]] = conversation_records(
                    store, str(row.get("native_id") or "")
                )
            else:
                path = Path(str(row.get("transcript_path") or ""))
                if not row.get("transcript_path") or not path.is_file():
                    raise OSError("no transcript")
                source = path
            scan = scan_native_telemetry(
                source, require_backend(backend), run_id, row.get("project_id"), row.get("model")
            )
        except (OSError, ValueError):
            report["runs_without_native_record"] += 1
            continue
        report["runs_with_native_record"] += 1
        for item in scan["tools"]:
            if item.get("kind") != "tool_use":
                continue
            call_id = _call_id(str(item.get("source_identity") or ""), run_id, "tool_use")
            report["native_calls"] += 1
            per["native_calls"] += 1
            found = next(
                (
                    key
                    for key in (f"{run_id}\0{call_id}\0model", f"{run_id}\0{call_id}\0runtime")
                    if key in ledger
                ),
                None,
            )
            if found is None:
                report["native_only"] += 1
                per["native_only"] += 1
                if len(report["examples"]["native_only"]) < examples:
                    report["examples"]["native_only"].append(
                        {"run_id": run_id, "backend": backend, "call_id": call_id,
                         "tool": item.get("raw_tool")}
                    )
                continue
            seen_keys.add(found)
            report["matched"] += 1
            per["matched"] += 1
    for key, call in ledger.items():
        if key in seen_keys:
            continue
        report["ledger_only"] += 1
        if len(report["examples"]["ledger_only"]) < examples:
            report["examples"]["ledger_only"].append(
                {
                    "run_id": call["run_id"],
                    "call_id": call["native_call_id"],
                    "tool": call["raw_name"],
                    "layer": call["invocation_layer"],
                    "sources": [call["request_source"], call["result_source"]],
                    "evidence_quality": call["evidence_quality"],
                }
            )
    report["ledger_calls"] = len(ledger)
    report["interpretation"] = (
        "matched: a native call the ledger holds under the same id; native_only: a call in "
        "the provider's record the ledger lacks (reconciliation closes this); ledger_only: a "
        "call the ledger holds that the provider's record does not name - typically live-only "
        "evidence such as a nested runtime execution or a hook-reported call"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--examples", type=int, default=20)
    parser.add_argument("--json", type=Path, default=None)
    arguments = parser.parse_args()
    data_dir = arguments.data_dir or Config().data_dir
    end = time.time()
    report = audit(data_dir, start=end - arguments.hours * 3600, end=end, examples=arguments.examples)
    if arguments.json is not None:
        arguments.json.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(
        f"runs {report['runs']} (native record for {report['runs_with_native_record']}, "
        f"none for {report['runs_without_native_record']})"
    )
    print(
        f"native calls {report['native_calls']}: matched {report['matched']}, "
        f"native_only {report['native_only']}; ledger calls {report['ledger_calls']}: "
        f"ledger_only {report['ledger_only']}"
    )
    for backend, per in sorted(report["by_backend"].items()):
        print(f"  {backend:<9} runs {per['runs']:>4} native {per['native_calls']:>6} "
              f"matched {per['matched']:>6} native_only {per['native_only']:>5}")
    for kind in ("native_only", "ledger_only"):
        for example in report["examples"][kind][:5]:
            print(f"  {kind}: {example}")
    return 0 if report["native_only"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
