from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .history import HistoryIndex
from .projects import ProjectIdentity, resolve_project

CLAUDE_CONTEXT_WINDOWS = {
    "claude-opus-4-8": 1_000_000,
    "claude-opus-4-7": 1_000_000,
    "claude-opus-4-6": 1_000_000,
    "claude-sonnet-5": 1_000_000,
    "claude-sonnet-4-6": 1_000_000,
    "claude-haiku-4-5": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
}


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


def summarize_transcript(path: Path, backend: str) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "context_window": None, "final_context_pct": None, "peak_context_pct": None,
        "tokens_in": 0, "tokens_out": 0, "model": None,
        "measurement_source": None,
    }
    peak = 0.0
    final: float | None = None
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return summary
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if backend == "claude" and event.get("type") == "assistant":
            message = event.get("message") or {}
            usage = message.get("usage") or {}
            model = str(message.get("model") or "")
            window = CLAUDE_CONTEXT_WINDOWS.get(model, 0)
            current_in = sum(
                int(usage.get(key, 0))
                for key in (
                    "input_tokens",
                    "cache_read_input_tokens",
                    "cache_creation_input_tokens",
                )
            )
            summary["tokens_in"] += current_in
            summary["tokens_out"] += int(usage.get("output_tokens", 0))
            if model:
                summary["model"] = model
            if window:
                summary["context_window"] = window
                final = min(1.0, current_in / window)
                peak = max(peak, final)
                summary["measurement_source"] = "claude-transcript-backfill"
        elif backend == "codex":
            payload = event.get("payload") or {}
            if event.get("type") == "session_meta" and payload.get("model"):
                summary["model"] = str(payload["model"])
            if payload.get("type") == "token_count":
                info = payload.get("info") or payload
                total = info.get("total_token_usage") or {}
                current = info.get("last_token_usage") or total
                window = int(info.get("model_context_window") or 0)
                summary["tokens_in"] = int(total.get("input_tokens") or 0)
                summary["tokens_out"] = int(total.get("output_tokens") or 0)
                summary["model"] = str(info.get("model") or "") or summary["model"]
                if window:
                    summary["context_window"] = window
                    final = min(1.0, int(current.get("input_tokens") or 0) / window)
                    peak = max(peak, final)
                    summary["measurement_source"] = "codex-transcript-backfill"
    summary["final_context_pct"] = final
    summary["peak_context_pct"] = peak if final is not None else None
    return summary


async def reconcile_external_history(history: HistoryIndex, home: Path | None = None) -> int:
    transcripts = await asyncio.to_thread(scan_external_transcripts, home)
    projects: dict[str, ProjectIdentity] = {}
    for item in transcripts:
        if item.cwd not in projects:
            projects[item.cwd] = await resolve_project(item.cwd)
        project = projects[item.cwd]
        await history.register_project_scope(project)
        summary = await asyncio.to_thread(summarize_transcript, item.path, item.backend)
        await history.upsert_external(
            row_id=item.row_id,
            native_id=item.native_id,
            backend=item.backend,
            name=Path(item.cwd).name or item.backend,
            cwd=item.cwd,
            spawned_at=item.created_at,
            transcript_path=str(item.path),
            project_id=project.id,
            project_label=project.label,
            project_root=project.root,
            project_scope_id=project.id,
            repo_group_id=project.repo_group_id,
            **summary,
        )
    return len(transcripts)
