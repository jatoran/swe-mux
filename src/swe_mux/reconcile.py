from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .history import HistoryIndex


@dataclass(slots=True)
class ExternalTranscript:
    backend: str
    native_id: str
    cwd: str
    created_at: float
    path: Path

    @property
    def row_id(self) -> str:
        digest = hashlib.sha256(f"{self.backend}:{self.native_id}".encode()).hexdigest()[:24]
        return f"external:{digest}"


def _timestamp(value: Any, fallback: float) -> float:
    if isinstance(value, (int, float)):
        return float(value) / 1000 if value > 10_000_000_000 else float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return fallback


def _first_events(path: Path, limit: int = 20) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for _, line in zip(range(limit), handle, strict=False):
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    events.append(event)
    except OSError:
        pass
    return events


def inspect_claude(path: Path) -> ExternalTranscript | None:
    events = _first_events(path)
    if not events:
        return None
    cwd = next((str(event["cwd"]) for event in events if event.get("cwd")), "")
    native_id = next(
        (str(event["sessionId"]) for event in events if event.get("sessionId")), path.stem
    )
    if not cwd:
        return None
    created = _timestamp(events[0].get("timestamp"), path.stat().st_mtime)
    return ExternalTranscript("claude", native_id, cwd, created, path)


def inspect_codex(path: Path) -> ExternalTranscript | None:
    events = _first_events(path)
    for event in events:
        if event.get("type") != "session_meta":
            continue
        payload = event.get("payload") or {}
        native_id, cwd = payload.get("id"), payload.get("cwd")
        if native_id and cwd:
            created = _timestamp(event.get("timestamp"), path.stat().st_mtime)
            return ExternalTranscript("codex", str(native_id), str(cwd), created, path)
    return None


def scan_external_transcripts(home: Path | None = None) -> list[ExternalTranscript]:
    home = home or Path.home()
    specs = (
        (home / ".claude" / "projects", "*.jsonl", inspect_claude),
        (home / ".codex" / "sessions", "rollout-*.jsonl", inspect_codex),
    )
    found: list[ExternalTranscript] = []
    for root, pattern, inspect in specs:
        if not root.exists():
            continue
        paths = sorted(
            root.glob(f"**/{pattern}"), key=lambda item: item.stat().st_mtime, reverse=True
        )
        for path in paths[:2000]:
            if transcript := inspect(path):
                found.append(transcript)
    return found


async def reconcile_external_history(history: HistoryIndex, home: Path | None = None) -> int:
    transcripts = await asyncio.to_thread(scan_external_transcripts, home)
    for item in transcripts:
        await history.upsert_external(
            row_id=item.row_id,
            native_id=item.native_id,
            backend=item.backend,
            name=Path(item.cwd).name or item.backend,
            cwd=item.cwd,
            spawned_at=item.created_at,
            transcript_path=str(item.path),
        )
    return len(transcripts)
