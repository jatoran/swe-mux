"""A History listing ships what the list draws, and never waits long on a transcript.

Measured 2026-10-12 on the development host: a 50-row page was 199 KB, of which
`argv_json` was 66% and the full title annotation 16%; and every listing re-read the
head and tail of each changed conversation before it answered - 168 ms per live row,
plus every Codex row on every listing, because the stored pair was a file stat and the
pair it was compared with was the Codex watermark `(0, total)`, which no stat equals
(122 rows were stuck re-reading that way).
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import app_keys as keys
from swe_mux import history as history_module
from swe_mux.history import HistoryIndex
from swe_mux.models import SessionRecord
from swe_mux.routes.history import list_history
from swe_mux.transcript_view import conversation_watermark

PROJECT = "project-id"


def codex_rollout(path: Path, replies: int = 2) -> Path:
    records: list[dict[str, Any]] = [
        {
            "type": "session_meta",
            "timestamp": "2026-02-01T10:00:00Z",
            "payload": {"id": "native-codex", "cwd": str(path.parent)},
        },
        {
            "type": "response_item",
            "timestamp": "2026-02-01T10:00:01Z",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Investigate"}],
            },
        },
    ]
    for reply in range(replies):
        records.append(
            {
                "type": "response_item",
                "timestamp": f"2026-02-01T10:00:{reply + 3:02d}Z",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": f"Resolved {reply}"}],
                },
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    return path


async def seeded(tmp_path: Path) -> tuple[HistoryIndex, Path]:
    rollout = codex_rollout(tmp_path / "sessions" / "rollout-native-codex.jsonl")
    history = HistoryIndex(tmp_path / "mux.db")
    record = SessionRecord(
        "codex-run",
        "codex",
        PROJECT,
        "codex",
        "native-codex",
        str(tmp_path),
        "codex.exe",
        ["codex", "--model", "gpt"],
    )
    record.agent_run_id = "codex-run"
    await history.session_started(record, str(rollout))
    return history, rollout


def count_reads(
    monkeypatch: pytest.MonkeyPatch, *, gate: threading.Event | None = None
) -> list[Path]:
    reads: list[Path] = []
    real = history_module.transcript_time_summary

    def counted(path: Path, backend: str, **kwargs: Any) -> dict[str, Any]:
        reads.append(path)
        if gate is not None:
            assert gate.wait(10), "the test never released the read"
        return real(path, backend, **kwargs)

    monkeypatch.setattr(history_module, "transcript_time_summary", counted)
    return reads


async def test_a_codex_row_is_summarised_once_and_then_left_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history, rollout = await seeded(tmp_path)
    reads = count_reads(monkeypatch)
    try:
        first = (await history.history_page(project_id=PROJECT))["items"]
        assert await history.refresh_time_summaries(first, wait_s=10) == 1
        assert first[0]["last_message_role"] == "assistant"
        assert first[0]["last_message_at"] is not None
        _identity, mark, total = conversation_watermark(rollout, "codex", "native-codex")
        assert mark == 0, "the Codex watermark is not a file stat"
        stored = await history.history_entry("codex-run") or {}
        assert (stored["time_summary_mtime_ns"], stored["time_summary_size"]) == (mark, total)

        second = (await history.history_page(project_id=PROJECT))["items"]
        assert await history.refresh_time_summaries(second, wait_s=10) == 0
        assert len(reads) == 1, "an unchanged Codex row was re-read"

        codex_rollout(rollout, replies=3)
        third = (await history.history_page(project_id=PROJECT))["items"]
        assert await history.refresh_time_summaries(third, wait_s=10) == 1
        assert len(reads) == 2
    finally:
        history.close()


async def test_a_slow_read_does_not_hold_the_listing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history, _rollout = await seeded(tmp_path)
    release = threading.Event()
    reads = count_reads(monkeypatch, gate=release)
    try:
        items = (await history.history_page(project_id=PROJECT))["items"]
        started = time.monotonic()
        refreshed = await history.refresh_time_summaries(items, wait_s=0.05)
        assert time.monotonic() - started < 5
        assert refreshed == 0
        assert items[0]["last_message_at"] is None, "answered with what was stored"

        # Single flight: a listing that arrives while the read runs joins it.
        again = (await history.history_page(project_id=PROJECT))["items"]
        assert await history.refresh_time_summaries(again, wait_s=0.05) == 0
        assert len(reads) == 1

        # The read finishes on its own and lands for the next listing.
        release.set()
        history.wait_for_background(timeout=10)
        stored = await history.history_entry("codex-run") or {}
        assert stored["last_message_role"] == "assistant"
        later = (await history.history_page(project_id=PROJECT))["items"]
        assert later[0]["last_message_at"] is not None
        assert await history.refresh_time_summaries(later, wait_s=10) == 0
        assert len(reads) == 1
    finally:
        release.set()
        history.close()


async def test_a_listing_page_ships_no_argv(tmp_path: Path) -> None:
    history, _rollout = await seeded(tmp_path)
    try:
        page = await history.history_page(project_id=PROJECT)
        assert page["items"] and "argv_json" not in page["items"][0]
        # The single-row read still carries it: Resume reads it from there.
        assert (await history.history_entry("codex-run") or {}).get("argv_json")
    finally:
        history.close()


async def test_the_listing_route_trims_the_title_annotation(tmp_path: Path) -> None:
    history, _rollout = await seeded(tmp_path)

    async def annotations(**_kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "id": "title-1",
                "agent_run_id": "codex-run",
                "content": "Investigation",
                "evidence_json": json.dumps(
                    [{"kind": "title_lifecycle", "stability": "provisional"}]
                ),
                "provenance": "model",
                "resolved_model": "some-model",
                "cost_usd": 0.02,
            }
        ]

    app = web.Application()
    app[keys.HISTORY] = history
    app[keys.CONFIG] = cast(Any, SimpleNamespace(history_limit=50))
    app[keys.AUTOMATION_STORE] = cast(Any, SimpleNamespace(annotations=annotations))
    app[keys.SESSIONS] = cast(Any, SimpleNamespace(conversation_holders=dict))
    app[keys.HISTORY_SEARCH_GATE] = asyncio.Lock()
    app.router.add_get("/api/history", list_history)
    try:
        async with TestClient(TestServer(app)) as client:
            response = await client.get(f"/api/history?project={PROJECT}")
            assert response.status == 200
            items = (await response.json())["items"]
        assert items[0]["generated_title"] == "Investigation"
        assert items[0]["generated_title_annotation"] == {"stability": "provisional"}
        assert "argv_json" not in items[0]
    finally:
        history.close()
