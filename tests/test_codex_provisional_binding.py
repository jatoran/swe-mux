"""Provisional Codex transcript binding: it may drive state, and nothing else.

Codex mints its own thread id and names it only on `agent-turn-complete`, so a
fresh pane cannot be exact-matched to its rollout until the first turn *ends*.
Refusing to look at the file until then is what left every new pane reporting
"ready · turn complete" for its whole first turn (measured live at 200 s, with the
rollout's own `task_started` written 4 s after spawn).

Following the sole unclaimed candidate fixes that, but the file is a well-reasoned
guess rather than a proven fact, so these tests pin the split: turn state moves,
and every durable claim that some work was *this* session's waits for the hook.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from swe_mux.event_bus import EventBus
from swe_mux.models import SessionRecord
from swe_mux.session import SessionManager


def codex_record(cwd: str = ".") -> SessionRecord:
    return SessionRecord(
        "mux-id", "codex-one", "default", "codex", "mux-id", cwd, "codex.exe", []
    )


def provisional_session(rollout: Path | None = None) -> Any:
    return cast(
        Any,
        SimpleNamespace(
            record=codex_record(),
            adapter=SimpleNamespace(
                name="codex",
                assigns_conversation_id=False,
                # The rollout's stem carries its thread id, as Codex's own header does.
                transcript_native_id=lambda path: Path(path).stem.split("-")[-1],
            ),
            transcript_path=rollout,
            transcript_provisional=True,
            agent_lifecycle_id=None,
            state_source_priority=-1,
            publish_update=lambda: None,
            tasks=set(),
        ),
    )


def resolving_manager() -> tuple[Any, list[tuple[str, str]], list[str], list[Any]]:
    manager = cast(Any, SessionManager.__new__(SessionManager))
    promoted: list[tuple[str, str]] = []
    emitted: list[str] = []
    restarted: list[Any] = []

    async def session_promoted(record: Any, path: str) -> None:
        promoted.append((record.native_session_id, path))

    async def emit(event_type: str, **_payload: Any) -> None:
        emitted.append(event_type)

    manager.history = SimpleNamespace(session_promoted=session_promoted)
    manager.events = SimpleNamespace(emit=emit)
    manager._start_observer = lambda _session, path: restarted.append(path)
    return manager, promoted, emitted, restarted


async def test_a_confirmed_guess_is_promoted_and_only_then_written_to_history(
    tmp_path: Path,
) -> None:
    """The hook names the conversation the guess was already following, so
    everything the provisional standing withheld becomes safe at once."""
    rollout = tmp_path / "rollout-abc123.jsonl"
    rollout.write_text("{}\n", encoding="utf-8")
    session = provisional_session(rollout)
    session.record.native_session_id = "abc123"
    manager, promoted, emitted, restarted = resolving_manager()

    await SessionManager._resolve_provisional_transcript(manager, session)

    assert session.transcript_provisional is False
    assert promoted == [("abc123", str(rollout))]
    assert "transcript_binding_confirmed" in emitted
    assert restarted == [], "a confirmed guess keeps the observer it already has"


async def test_a_refuted_guess_is_dropped_with_nothing_left_behind(
    tmp_path: Path,
) -> None:
    """The pane was following a stranger's conversation. Nothing durable was ever
    written under it, which is the entire point of the standing, so discarding the
    guess is complete — and the exact-match route exists from this moment on."""
    rollout = tmp_path / "rollout-stranger.jsonl"
    rollout.write_text("{}\n", encoding="utf-8")
    session = provisional_session(rollout)
    session.record.native_session_id = "ours"
    manager, promoted, emitted, restarted = resolving_manager()

    await SessionManager._resolve_provisional_transcript(manager, session)

    assert promoted == [], "a refuted guess must never reach the history row"
    assert "transcript_binding_discarded" in emitted
    assert restarted == [None], "re-derive from scratch, now by exact match"


async def test_resolution_is_inert_for_a_binding_that_was_never_provisional(
    tmp_path: Path,
) -> None:
    session = provisional_session(tmp_path / "rollout-abc.jsonl")
    session.transcript_provisional = False
    manager, promoted, emitted, restarted = resolving_manager()

    await SessionManager._resolve_provisional_transcript(manager, session)

    assert (promoted, emitted, restarted) == ([], [], [])


async def test_a_provisional_transcript_never_rekeys_the_conversation() -> None:
    """`session_meta` carries the file's own id. Reading it would make the guess
    confirm itself and bypass the hook, which is the only real evidence there is."""
    from swe_mux.observation import _codex

    session = provisional_session()
    session.record.model = ""
    await _codex(
        session,
        {
            "type": "session_meta",
            "payload": {"id": "someone-elses-thread", "cwd": ".", "model": "gpt-5"},
        },
        EventBus(),
    )

    assert session.record.native_session_id == "mux-id"
    assert session.record.model == ""


async def test_a_provisional_transcript_publishes_no_tokens_or_context() -> None:
    """Tokens are shown on the pane and copied into the history row at turn end, so
    they are attribution, not state. Nothing is lost by waiting: Codex reports
    cumulative totals, so the first count after the binding carries the whole turn."""
    from swe_mux.observation import _codex

    session = provisional_session()
    events = EventBus()
    payload = {
        "type": "token_count",
        "info": {
            "total_token_usage": {"input_tokens": 900, "output_tokens": 80},
            "last_token_usage": {"input_tokens": 900},
            "model_context_window": 200_000,
            "model": "gpt-5",
        },
    }
    await _codex(session, {"type": "event_msg", "payload": payload}, events)
    assert (session.record.tokens_in, session.record.tokens_out) == (0, 0)
    assert session.record.context_window == 0

    session.transcript_provisional = False
    await _codex(session, {"type": "event_msg", "payload": payload}, events)
    assert (session.record.tokens_in, session.record.tokens_out) == (900, 80)
    assert session.record.context_window == 200_000


async def test_a_provisional_transcript_records_no_compaction_evidence() -> None:
    """Durable per-session operational telemetry: attributing a stranger's
    compaction is a claim about this pane that nothing later removes."""
    from swe_mux.observation import _codex

    session = provisional_session()
    events = EventBus()
    queue = events.subscribe()
    await _codex(
        session, {"type": "event_msg", "payload": {"type": "context_compacted"}}, events
    )
    assert [] == [
        item for item in _drain(queue) if getattr(item, "type", "") == "context_compacted"
    ]


async def test_a_provisional_transcript_still_drives_turn_state() -> None:
    """The whole reason to follow the file: the first turn becomes visible."""
    from swe_mux.observation import _codex

    session = provisional_session()
    events = EventBus()
    await _codex(session, {"type": "event_msg", "payload": {"type": "task_started"}}, events)
    assert session.record.state == "working"
    await _codex(session, {"type": "event_msg", "payload": {"type": "task_complete"}}, events)
    assert session.record.state == "idle"


def test_a_provisional_path_is_not_mirrored_into_supervisor_metadata(
    tmp_path: Path,
) -> None:
    """The successor daemon reads that metadata as an *established* binding and
    starts its observer on it directly, so mirroring would promote a guess to a fact
    across exactly the restart that erased the reasoning behind it."""
    rollout = tmp_path / "rollout-abc.jsonl"
    session = cast(
        Any,
        SimpleNamespace(
            record=SimpleNamespace(snapshot=lambda: {"id": "mux-id"}),
            hook_secret="hs",
            mcp_token="tok",
            transcript_path=rollout,
            transcript_provisional=True,
            agent_lifecycle_id=None,
        ),
    )
    assert SessionManager._session_meta(session)["transcript_path"] is None
    session.transcript_provisional = False
    assert SessionManager._session_meta(session)["transcript_path"] == str(rollout)


def _drain(queue: Any) -> list[Any]:
    items = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items
