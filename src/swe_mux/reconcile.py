from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .adapters.claude import encode_cwd, is_conversation_transcript
from .git_projects import ProjectIdentity, resolve_project
from .history import HistoryIndex
from .transcript_view import TRANSCRIPT_PARSER_VERSION

log = logging.getLogger(__name__)

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
    mtime_ns: int = 0
    size: int = 0

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
    # `<id>.orphaned-<ts>-<hash>.jsonl` is CLI housekeeping, not a conversation:
    # its recovered sessionId is the original's, so indexing it makes the fragment
    # and the real transcript alternate ownership of one history row forever.
    if not is_conversation_transcript(path):
        return None
    events = _first_events(path)
    if not events:
        return None
    if path.parent.name == "subagents" or any(event.get("isSidechain") is True for event in events):
        return None
    cwd = next((str(event["cwd"]) for event in events if event.get("cwd")), "")
    native_id = next(
        (str(event["sessionId"]) for event in events if event.get("sessionId")), path.stem
    )
    if not cwd:
        return None
    try:
        st = path.stat()
    except OSError:
        return None
    created = _timestamp(events[0].get("timestamp"), st.st_mtime)
    return ExternalTranscript("claude", native_id, cwd, created, path, st.st_mtime_ns, st.st_size)


def inspect_codex(path: Path) -> ExternalTranscript | None:
    events = _first_events(path)
    for event in events:
        if event.get("type") != "session_meta":
            continue
        payload = event.get("payload") or {}
        if payload.get("parent_thread_id"):
            return None
        native_id, cwd = payload.get("id"), payload.get("cwd")
        if native_id and cwd:
            try:
                st = path.stat()
            except OSError:
                return None
            created = _timestamp(event.get("timestamp"), st.st_mtime)
            return ExternalTranscript(
                "codex", str(native_id), str(cwd), created, path, st.st_mtime_ns, st.st_size
            )
    return None


def scan_external_transcripts(
    home: Path | None = None,
    *,
    limit: int | None = 2000,
    roots: Iterable[str | Path] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    on_progress: Callable[[int], None] | None = None,
) -> list[ExternalTranscript]:
    """Discover native Claude/Codex transcripts.

    ``roots`` restricts the Claude scan to project directories whose encoded
    name matches one of the supplied working-copy roots. Because Claude stores
    each session under ``projects/<encoded-cwd>/`` and ``encode_cwd`` is
    prefix-preserving, a project-scoped backfill reads a handful of directories
    instead of every transcript on the machine (a real user can have tens of
    thousands). Over-matched siblings are still filtered downstream by the
    actual cwd, so this only trades reads, never correctness. Codex stores
    sessions flat by date with no cwd in the path, so it is always scanned in
    full. ``should_cancel`` is polled per file so a long scan aborts promptly,
    and ``on_progress`` receives the running count of files examined.
    """
    user_home = home or Path.home()
    codex_home = (
        home / ".codex"
        if home is not None
        else Path(os.environ.get("CODEX_HOME") or user_home / ".codex").expanduser()
    )
    encoded_roots = (
        [encode_cwd(root).lower() for root in roots] if roots is not None else None
    )
    specs = (
        (user_home / ".claude" / "projects", "*.jsonl", inspect_claude, True),
        (codex_home / "sessions", "rollout-*.jsonl", inspect_codex, False),
    )
    found: list[ExternalTranscript] = []
    scanned = 0
    for root, pattern, inspect, scoped in specs:
        if not root.exists():
            continue
        if scoped and encoded_roots is not None:
            search_dirs = [
                child
                for child in root.iterdir()
                if child.is_dir()
                and any(child.name.lower().startswith(prefix) for prefix in encoded_roots)
            ]
        else:
            search_dirs = [root]
        discovered: list[tuple[float, Path]] = []
        for directory in search_dirs:
            for path in directory.glob(f"**/{pattern}"):
                # Provider transcript cleanup (and antivirus) removes and locks
                # files in these very directories while the scan walks them. One
                # raced file used to abort the whole reconcile with no log, so
                # every remaining transcript stayed unindexed until some later
                # start happened to complete.
                try:
                    discovered.append((path.stat().st_mtime, path))
                except OSError:
                    continue
        discovered.sort(key=lambda item: item[0], reverse=True)
        for _, path in discovered if limit is None else discovered[:limit]:
            if should_cancel is not None and should_cancel():
                return found
            try:
                transcript = inspect(path)
            except OSError:
                transcript = None
            if transcript:
                found.append(transcript)
            scanned += 1
            if on_progress is not None:
                on_progress(scanned)
    return found


def summarize_transcript(path: Path, backend: str) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "context_window": None,
        "final_context_pct": None,
        "peak_context_pct": None,
        "tokens_in": 0,
        "tokens_out": 0,
        "model": None,
        "measurement_source": None,
    }
    peak = 0.0
    final: float | None = None
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return summary
    # Streamed, not read_text().splitlines(): a months-long conversation is
    # hundreds of MB and the whole-file string plus its split list held several
    # multiples of that in the daemon at once.
    with handle:
        for line in handle:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
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
    # Skip transcripts whose (mtime_ns, size) are unchanged since the last
    # reconcile so unchanged native files are never re-read/re-parsed. The
    # watermark is persisted per external row, so this holds across restarts.
    watermarks = await history.external_watermarks()
    history_ids = await history.native_history_ids()
    message_watermarks = await history.message_index_watermarks()
    projects: dict[str, ProjectIdentity] = {}
    skipped = 0
    for item in transcripts:
        history_id = history_ids.get((item.backend, item.native_id))
        messages_current = bool(
            history_id
            and message_watermarks.get(history_id)
            == (item.mtime_ns, item.size, TRANSCRIPT_PARSER_VERSION)
        )
        if watermarks.get(str(item.path)) == (item.mtime_ns, item.size) and messages_current:
            continue
        try:
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
                repository_id=project.id,
                project_label=project.label,
                project_root=project.root,
                project_scope_id=project.id,
                repo_group_id=project.repo_group_id,
                mtime_ns=item.mtime_ns,
                size=item.size,
                **summary,
            )
            history_id = (await history.native_history_ids()).get((item.backend, item.native_id))
            if history_id:
                await history.index_transcript(history_id, item.path, item.backend)
        except asyncio.CancelledError:
            raise
        except Exception:
            # One vanished/locked transcript, or one row that will not index, used
            # to abort the whole scan and leave every remaining external
            # transcript unindexed until some later start happened to complete.
            skipped += 1
            log.warning("external history reconcile skipped %s", item.path, exc_info=True)
    if skipped:
        log.warning("external history reconcile skipped %d transcript(s)", skipped)
    return len(transcripts)
