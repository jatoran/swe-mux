"""The spawn stage path: text parked in a fresh agent's composer, unsent.

`seed_text` was documented as staging while actually submitting (the seed rides
argv, so the CLI runs it) — three sessions opened with their prompts already
sent while the operator asked for them left unsent (2026-08-20). `stage_text`
is the real stage-without-send: spawn, wait for readiness, bracketed paste with
NO carriage return, all daemon-side. These tests pin the contract the live
probe proved: nothing submits, the paste carries no trailing Enter, and the
readiness wait is bounded rather than trusted.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from swe_mux import server
from swe_mux.prompt_queue import BRACKETED_PASTE_END, paste_payload


class _Record:
    def __init__(self) -> None:
        self.id = "s1"
        self.state = "starting"


class _Session:
    def __init__(self) -> None:
        self.record = _Record()


class _Events:
    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict[str, Any]]] = []

    async def emit(self, name: str, **payload: Any) -> None:
        self.emitted.append((name, payload))


def _capture_writes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    writes: list[tuple[str, str]] = []

    def record(
        events: Any, session: Any, data: str, *, source: str, input_owner: bool = True
    ) -> None:
        writes.append((data, source))

    monkeypatch.setattr(server, "_record_operator_input", record)
    monkeypatch.setattr(server, "STAGE_READY_POLL_SECONDS", 0.001)
    return writes


async def test_stage_waits_for_readiness_then_pastes_without_enter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session()
    events = _Events()
    writes = _capture_writes(monkeypatch)

    async def become_ready() -> None:
        await asyncio.sleep(0.01)
        session.record.state = "idle"

    settle = asyncio.create_task(become_ready())
    await server._stage_spawn_text({"events": events}, session, "line one\nline two")
    await settle
    assert writes == [(paste_payload("line one\nline two"), "spawn_stage")]
    # The whole point: the write ends on the paste terminator, never on Enter.
    assert writes[0][0].endswith(BRACKETED_PASTE_END)
    assert "\r" not in writes[0][0].split(BRACKETED_PASTE_END)[-1]
    assert events.emitted == [
        (
            "spawn_text_staged",
            {
                "session_id": "s1",
                "source": "spawn_stage",
                "characters": len("line one\nline two"),
                "ready": True,
            },
        )
    ]


async def test_stage_timeout_still_pastes_and_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    """A session that never reads ready still gets the paste — the PTY buffers
    input written before the CLI listens (proven live 2026-08-20) — but the
    event records ready=False instead of pretending the wait succeeded."""
    session = _Session()
    events = _Events()
    writes = _capture_writes(monkeypatch)
    monkeypatch.setattr(server, "STAGE_READY_TIMEOUT_SECONDS", 0.02)
    await server._stage_spawn_text({"events": events}, session, "parked prompt")
    assert writes == [(paste_payload("parked prompt"), "spawn_stage")]
    assert events.emitted[0][1]["ready"] is False


async def test_stage_refuses_a_session_that_died_first(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _Session()
    session.record.state = "exited"
    events = _Events()
    writes = _capture_writes(monkeypatch)
    with pytest.raises(ValueError, match="stage_text"):
        await server._stage_spawn_text({"events": events}, session, "too late")
    assert writes == []
    assert events.emitted == []
