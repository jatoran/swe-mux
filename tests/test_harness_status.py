"""Harness-reported session status: the three channels and the tee that feeds one.

The facts a CLI's own status line draws and no transcript carries - effort, the
permission mode, the provider limits - reach `SessionRecord.harness_status` over
three channels, and each is exercised here against the payload shape its
harness actually emits (Claude's documented status-line JSON, the
`permission_mode` every Claude and Codex hook carries, and the records read
out of a live Codex rollout on 2026-09-07).
"""

from __future__ import annotations

import json
import os
import shlex
import sys
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import app_keys as keys
from swe_mux import hook_client
from swe_mux.adapters.base import SpawnOptions
from swe_mux.adapters.claude import ClaudeAdapter
from swe_mux.claude_status_line import (
    StatusLineDelegate,
    resolve_status_line_delegate,
    status_line_from_settings,
)
from swe_mux.event_bus import EventBus
from swe_mux.harness_status import (
    CODEX_BASELINE_TOKENS,
    STATUS_EVENT,
    apply_claude_status_snapshot,
    apply_codex_rate_limits,
    apply_codex_settings,
    apply_hook_permission_mode,
    codex_context_fraction,
)
from swe_mux.models import (
    RATE_LIMIT_FIVE_HOUR,
    RATE_LIMIT_SEVEN_DAY,
    RATE_LIMIT_SPEND,
    HarnessStatus,
    RateLimitWindow,
    SessionRecord,
)
from swe_mux.observation import _claude, _codex, apply_hook_observation
from swe_mux.routes.agent_ingress import hook_ingress
from swe_mux.server import error_middleware, security_middleware
from swe_mux.session import SessionManager

NOW = 1_800_000_000.0


def record(backend: str = "claude") -> SessionRecord:
    return SessionRecord(
        "mux-id", "builder-one", "default", backend, "native-id", ".", f"{backend}.exe", []
    )


def fake_session(backend: str = "claude") -> Any:
    session = SimpleNamespace(record=record(backend), state_source_priority=-1, published=0)

    def publish() -> None:
        session.published += 1

    session.publish_update = publish
    return cast(Any, session)


#: The documented status-line snapshot (code.claude.com/docs/en/statusline, read
#: 2026-09-07), trimmed to the fields swe-mux reads plus a few it must ignore.
CLAUDE_SNAPSHOT: dict[str, Any] = {
    "cwd": "D:/PROJECTS/swe-mux",
    "session_id": "native-id",
    "transcript_path": "D:/transcripts/native-id.jsonl",
    "model": {"id": "claude-opus-5", "display_name": "Opus"},
    "workspace": {"current_dir": "D:/PROJECTS/swe-mux", "project_dir": "D:/PROJECTS/swe-mux"},
    "version": "2.1.260",
    "output_style": {"name": "Concise"},
    "cost": {
        "total_cost_usd": 1.2345,
        "total_duration_ms": 45_000,
        "total_api_duration_ms": 2_300,
        "total_lines_added": 156,
        "total_lines_removed": 23,
    },
    "context_window": {
        "total_input_tokens": 15_500,
        "total_output_tokens": 1_200,
        "context_window_size": 1_000_000,
        "used_percentage": 8.5,
        "remaining_percentage": 91.5,
        "current_usage": {
            "input_tokens": 8_500,
            "output_tokens": 1_200,
            "cache_creation_input_tokens": 5_000,
            "cache_read_input_tokens": 2_000,
        },
    },
    "exceeds_200k_tokens": False,
    "fast_mode": False,
    "effort": {"level": "xhigh"},
    "thinking": {"enabled": True},
    "rate_limits": {
        "five_hour": {"used_percentage": 23.5, "resets_at": 1_800_004_000},
        "seven_day": {"used_percentage": 41.2, "resets_at": 1_800_400_000},
    },
    "vim": {"mode": "NORMAL"},
}


# --- the pure parsers -------------------------------------------------------


def test_codex_context_fraction_matches_the_footer_arithmetic() -> None:
    """`percent_of_context_window_remaining`, inverted: both sides lose the baseline."""
    window = 258_400
    last = {
        "input_tokens": 60_000,
        "cached_input_tokens": 50_000,
        "output_tokens": 2_000,
        "reasoning_output_tokens": 500,
        "total_tokens": 62_000,
    }
    expected = (62_000 - CODEX_BASELINE_TOKENS) / (window - CODEX_BASELINE_TOKENS)
    assert codex_context_fraction(last, window) == pytest.approx(expected)
    # `total_tokens` is what the CLI reads; only its absence falls back to a sum.
    assert codex_context_fraction({"input_tokens": 20_000, "output_tokens": 1_000}, window) == (
        pytest.approx((21_000 - CODEX_BASELINE_TOKENS) / (window - CODEX_BASELINE_TOKENS))
    )
    # Under the baseline reads as empty, not negative; over the window clamps.
    assert codex_context_fraction({"total_tokens": 5_000}, window) == 0.0
    assert codex_context_fraction({"total_tokens": 10 * window}, window) == 1.0
    # A window the baseline swallows is what the CLI shows as 0% left; here it
    # is "nothing measured" rather than "full", so it reads 0 used too.
    assert codex_context_fraction({"total_tokens": 5_000}, CODEX_BASELINE_TOKENS) == 0.0


def test_hook_permission_mode_is_taken_verbatim_and_garbage_is_refused() -> None:
    rec = record()
    assert apply_hook_permission_mode(rec, {"permission_mode": "acceptEdits"}, now=NOW) == [
        "permission_mode"
    ]
    assert rec.harness_status.permission_mode == "acceptEdits"
    assert rec.harness_status.sources["permission_mode"] == "hook"
    assert rec.harness_status.updated_at == NOW
    # Unchanged is not a change, so the caller does not republish for it.
    assert apply_hook_permission_mode(rec, {"permission_mode": "acceptEdits"}, now=NOW) == []
    for garbage in ("", "  ", "bypass permissions", 3, None, "x" * 41, "-plan"):
        assert apply_hook_permission_mode(rec, {"permission_mode": garbage}, now=NOW) == []
    assert rec.harness_status.permission_mode == "acceptEdits"


def test_codex_settings_read_effort_from_either_record_and_the_tier() -> None:
    rec = record("codex")
    # `turn_context` spells it `effort`; `thread_settings` spells it `reasoning_effort`.
    assert apply_codex_settings(rec, {"effort": "xhigh", "model": "gpt-6-astra"}, now=NOW) == [
        "effort"
    ]
    assert rec.harness_status.effort == "xhigh"
    changed = apply_codex_settings(
        rec, {"reasoning_effort": "medium", "service_tier": "fast"}, now=NOW
    )
    assert changed == ["effort", "fast_mode"]
    assert rec.harness_status.effort == "medium"
    assert rec.harness_status.fast_mode is True
    assert apply_codex_settings(rec, {"service_tier": "default"}, now=NOW) == ["fast_mode"]
    assert rec.harness_status.fast_mode is False
    assert rec.harness_status.sources == {"effort": "codex-rollout", "fast_mode": "codex-rollout"}
    # A record naming neither leaves everything, including the stamp, alone.
    rec.harness_status.updated_at = None
    assert apply_codex_settings(rec, {"cwd": "D:/x"}, now=NOW) == []
    assert rec.harness_status.updated_at is None


def test_codex_rate_limits_are_keyed_by_window_length_not_slot() -> None:
    """The shape read out of a live rollout's `token_count.rate_limits`."""
    rec = record("codex")
    limits = {
        "limit_id": "codex",
        "limit_name": None,
        "primary": {"used_percent": 0.0, "window_minutes": 300, "resets_at": 1_788_306_136},
        "secondary": {"used_percent": 2.0, "window_minutes": 10_080, "resets_at": 1_788_810_833},
        "credits": {"has_credits": False, "unlimited": False, "balance": "0"},
        "plan_type": "pro",
    }
    changed = apply_codex_rate_limits(rec, limits, now=NOW)
    assert changed == [f"rate_limits.{RATE_LIMIT_FIVE_HOUR}", f"rate_limits.{RATE_LIMIT_SEVEN_DAY}"]
    assert rec.harness_status.rate_limits == {
        RATE_LIMIT_FIVE_HOUR: RateLimitWindow(0.0, 1_788_306_136.0, 300),
        RATE_LIMIT_SEVEN_DAY: RateLimitWindow(2.0, 1_788_810_833.0, 10_080),
    }
    assert rec.harness_status.sources["rate_limits"] == "codex-rollout"
    # Same numbers again: nothing changed, nothing to publish.
    assert apply_codex_rate_limits(rec, limits, now=NOW) == []
    # The slots swap on some plans; the key follows the length, not the slot.
    swapped = {"primary": limits["secondary"], "secondary": limits["primary"]}
    assert apply_codex_rate_limits(rec, swapped, now=NOW) == []
    # A length the table does not know keeps its slot name rather than vanishing.
    odd = {"primary": {"used_percent": 7.0, "window_minutes": 777, "resets_at": None}}
    assert apply_codex_rate_limits(rec, odd, now=NOW) == ["rate_limits.primary"]
    assert rec.harness_status.rate_limits["primary"].window_minutes == 777
    # A window without a percentage is not a window.
    assert apply_codex_rate_limits(rec, {"primary": {"window_minutes": 300}}, now=NOW) == []


def test_claude_snapshot_fills_the_status_and_the_three_cli_measurements() -> None:
    rec = record()
    changed = apply_claude_status_snapshot(rec, CLAUDE_SNAPSHOT, now=NOW)
    status = rec.harness_status
    assert status.effort == "xhigh"
    assert status.output_style == "Concise"
    assert status.fast_mode is False
    assert status.thinking is True
    assert status.rate_limits == {
        RATE_LIMIT_FIVE_HOUR: RateLimitWindow(23.5, 1_800_004_000.0),
        RATE_LIMIT_SEVEN_DAY: RateLimitWindow(41.2, 1_800_400_000.0),
    }
    assert status.context_window_size == 1_000_000
    assert rec.context_window == 1_000_000
    assert rec.context_pct == pytest.approx(0.085)
    assert rec.context_peak_pct == pytest.approx(0.085)
    assert rec.cost_usd == 1.2345
    assert status.updated_at == NOW
    assert set(status.sources) == {
        "effort",
        "output_style",
        "fast_mode",
        "thinking",
        "rate_limits",
        "context_window_size",
    }
    assert set(status.sources.values()) == {"claude-statusline"}
    assert set(changed) == {
        "effort",
        "output_style",
        "fast_mode",
        "thinking",
        f"rate_limits.{RATE_LIMIT_FIVE_HOUR}",
        f"rate_limits.{RATE_LIMIT_SEVEN_DAY}",
        "context_window_size",
        "context_window",
        "context_pct",
        "cost_usd",
    }
    # The same snapshot again changes nothing.
    assert apply_claude_status_snapshot(rec, CLAUDE_SNAPSHOT, now=NOW + 1) == []
    # The model is deliberately not read here: the transcript owns it, and two
    # writers of one field is how model-divergence noise starts.
    assert rec.model is None


def test_claude_snapshot_leaves_what_it_does_not_carry() -> None:
    """`current_usage` is null before the first call and after `/compact`."""
    rec = record()
    apply_claude_status_snapshot(rec, CLAUDE_SNAPSHOT, now=NOW)
    after_compact = {
        **CLAUDE_SNAPSHOT,
        "context_window": {
            "total_input_tokens": 15_500,
            "total_output_tokens": 1_200,
            "context_window_size": 1_000_000,
            "used_percentage": None,
            "remaining_percentage": None,
            "current_usage": None,
        },
        "effort": None,
        "rate_limits": None,
        "cost": {"total_cost_usd": "not a number"},
    }
    assert apply_claude_status_snapshot(rec, after_compact, now=NOW + 5) == []
    assert rec.context_pct == pytest.approx(0.085)
    assert rec.harness_status.effort == "xhigh"
    assert rec.cost_usd == 1.2345
    assert rec.harness_status.updated_at == NOW + 5
    # A spend limit behind a gateway lands under its own key.
    with_spend = {
        **CLAUDE_SNAPSHOT,
        "rate_limits": {"spend_limit": {"used_percentage": 120.0, "resets_at": 1_800_900_000}},
    }
    assert apply_claude_status_snapshot(rec, with_spend, now=NOW + 6) == [
        f"rate_limits.{RATE_LIMIT_SPEND}"
    ]
    assert rec.harness_status.rate_limits[RATE_LIMIT_SPEND].used_pct == 120.0


def test_harness_status_rides_the_record_snapshot_both_ways() -> None:
    rec = record()
    apply_claude_status_snapshot(rec, CLAUDE_SNAPSHOT, now=NOW)
    apply_hook_permission_mode(rec, {"permission_mode": "plan"}, now=NOW)
    restored = SessionRecord.from_snapshot(json.loads(json.dumps(rec.snapshot())))
    assert restored.harness_status == rec.harness_status
    # Drift in either direction degrades to defaults, never to a crash.
    assert (
        SessionRecord.from_snapshot({**rec.snapshot(), "harness_status": "bogus"}).harness_status
        == HarnessStatus()
    )
    partial = HarnessStatus.from_snapshot(
        {
            "effort": "",
            "fast_mode": "yes",
            "rate_limits": {"five_hour": {"used_percent": 3}},
            "x": 1,
        }
    )
    assert partial.effort is None
    assert partial.fast_mode is None
    assert partial.rate_limits == {}
    assert partial.is_empty()


# --- the observer -----------------------------------------------------------


async def test_status_event_updates_the_record_without_moving_state() -> None:
    session = fake_session()
    session.record.state = "working"
    events = EventBus()
    queue = events.subscribe()

    decision = await apply_hook_observation(session, STATUS_EVENT, dict(CLAUDE_SNAPSHOT), events)

    assert decision is None
    assert session.record.state == "working"
    assert session.record.harness_status.effort == "xhigh"
    assert session.record.cost_usd == 1.2345
    assert session.published == 1
    emitted = [item for item in _drain(queue) if item.type == "harness_status"]
    assert len(emitted) == 1
    assert emitted[0].payload["effort"] == "xhigh"
    assert emitted[0].payload["rate_limits"] == {
        RATE_LIMIT_FIVE_HOUR: 23.5,
        RATE_LIMIT_SEVEN_DAY: 41.2,
    }
    assert "delegate" not in emitted[0].payload
    # An identical snapshot publishes nothing and emits nothing.
    await apply_hook_observation(session, STATUS_EVENT, dict(CLAUDE_SNAPSHOT), events)
    assert session.published == 1
    assert [item for item in _drain(queue) if item.type == "harness_status"] == []


async def test_status_event_reports_a_failed_delegate_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = fake_session()
    session.status_health_counters = {}
    events = EventBus()
    failed = {
        **CLAUDE_SNAPSHOT,
        "delegate": {"ok": False, "exit_code": 127, "error": "exit 127: bash: bun: not found"},
    }
    with caplog.at_level("INFO", logger="swe_mux.observation"):
        await apply_hook_observation(session, STATUS_EVENT, dict(failed), events)
        await apply_hook_observation(session, STATUS_EVENT, dict(failed), events)
        recovered = {**CLAUDE_SNAPSHOT, "delegate": {"ok": True, "exit_code": 0, "error": None}}
        await apply_hook_observation(session, STATUS_EVENT, dict(recovered), events)
    warnings = [line for line in caplog.messages if "delegate failed" in line]
    assert len(warnings) == 1, "one warning per distinct error, not one per message"
    assert "bun: not found" in warnings[0]
    assert any("delegate recovered" in line for line in caplog.messages)
    assert session.status_health_counters == {"status_delegate_failed": 1}


async def test_permission_mode_comes_from_root_hooks_only() -> None:
    session = fake_session()
    events = EventBus()
    await apply_hook_observation(
        session, "PreToolUse", {"permission_mode": "bypassPermissions", "tool_name": "Bash"}, events
    )
    assert session.record.harness_status.permission_mode == "bypassPermissions"
    # A subagent runs under its own mode; the pane's row must keep the pane's.
    await apply_hook_observation(
        session,
        "PostToolUse",
        {"permission_mode": "default", "agent_id": "child-1", "tool_name": "Read"},
        events,
    )
    assert session.record.harness_status.permission_mode == "bypassPermissions"
    await apply_hook_observation(session, "Stop", {"permission_mode": "plan"}, events)
    assert session.record.harness_status.permission_mode == "plan"


async def test_the_claude_transcript_reads_the_window_the_cli_reported() -> None:
    """The model table is the fallback; the CLI's own size wins once known."""
    session = fake_session()
    events = EventBus()
    usage = {
        "type": "assistant",
        "message": {
            "content": [{"type": "text", "text": "OK"}],
            "stop_reason": "end_turn",
            "model": "claude-something-new",
            "usage": {
                "input_tokens": 1_000,
                "cache_creation_input_tokens": 2_000,
                "cache_read_input_tokens": 3_000,
                "output_tokens": 10,
            },
        },
    }
    await _claude(session, usage, events)
    # Unknown model, no table row: the failure this exists for reads as 0%.
    assert session.record.context_window == 0
    assert session.record.context_pct == 0

    await apply_hook_observation(session, STATUS_EVENT, dict(CLAUDE_SNAPSHOT), events)
    await _claude(session, usage, events)
    assert session.record.context_window == 1_000_000
    assert session.record.context_pct == pytest.approx(6_000 / 1_000_000)


async def test_codex_rollout_records_feed_effort_tier_and_limits() -> None:
    session = fake_session("codex")
    events = EventBus()
    await _codex(
        session,
        {
            "type": "turn_context",
            "payload": {
                "turn_id": "t1",
                "cwd": "D:/PROJECTS/x",
                "approval_policy": "on-request",
                "model": "gpt-6-astra",
                "effort": "xhigh",
            },
        },
        events,
    )
    assert session.record.model == "gpt-6-astra"
    assert session.record.harness_status.effort == "xhigh"
    await _codex(
        session,
        {
            "type": "event_msg",
            "payload": {
                "type": "thread_settings_applied",
                "thread_settings": {
                    "model": "gpt-6-astra",
                    "service_tier": "fast",
                    "approval_policy": "on-request",
                    "reasoning_effort": "high",
                },
            },
        },
        events,
    )
    assert session.record.harness_status.effort == "high"
    assert session.record.harness_status.fast_mode is True
    await _codex(
        session,
        {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": 19_830,
                        "output_tokens": 197,
                        "total_tokens": 20_027,
                    },
                    "last_token_usage": {
                        "input_tokens": 19_830,
                        "cached_input_tokens": 11_008,
                        "output_tokens": 197,
                        "total_tokens": 20_027,
                    },
                    "model_context_window": 828_400,
                },
                "rate_limits": {
                    "limit_id": "codex",
                    "primary": {
                        "used_percent": 4.0,
                        "window_minutes": 300,
                        "resets_at": 1_788_462_337,
                    },
                    "secondary": {
                        "used_percent": 9.0,
                        "window_minutes": 10_080,
                        "resets_at": 1_788_810_833,
                    },
                },
            },
        },
        events,
    )
    status = session.record.harness_status
    assert status.rate_limits[RATE_LIMIT_FIVE_HOUR].used_pct == 4.0
    assert status.rate_limits[RATE_LIMIT_SEVEN_DAY].used_pct == 9.0
    assert session.record.context_pct == pytest.approx(
        (20_027 - CODEX_BASELINE_TOKENS) / (828_400 - CODEX_BASELINE_TOKENS)
    )


async def test_a_provisional_codex_follow_publishes_no_settings() -> None:
    """Attribution, like tokens: a guessed file must not put its effort on this pane."""
    session = fake_session("codex")
    session.transcript_provisional = True
    events = EventBus()
    await _codex(
        session, {"type": "turn_context", "payload": {"effort": "xhigh", "model": "m"}}, events
    )
    assert session.record.harness_status.effort is None
    assert session.record.model is None


def test_every_reset_site_clears_the_harness_status() -> None:
    rec = record()
    apply_claude_status_snapshot(rec, CLAUDE_SNAPSHOT, now=NOW)
    SessionManager._reset_provider_observation(rec)
    assert rec.harness_status == HarnessStatus()


def _drain(queue: Any) -> list[Any]:
    items = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items


# --- the ingress ------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_ingress_accepts_status_and_keeps_it_off_the_event_bus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    applied: list[tuple[str, dict[str, object]]] = []
    emitted: list[str] = []

    class Events:
        async def emit(self, event: str, **_: object) -> None:
            emitted.append(event)

    async def apply(
        _session: object, event_type: str, payload: dict[str, object], _events: object
    ) -> None:
        applied.append((event_type, payload))

    async def heal(_session: object, _payload: object) -> None:
        return None

    monkeypatch.setattr("swe_mux.routes.agent_ingress.apply_hook_observation", apply)
    monkeypatch.setattr(
        "swe_mux.routes.agent_ingress.foreign_conversation_hook_id", lambda _s, _p: None
    )
    rec = SimpleNamespace(
        id="00000000-0000-4000-8000-000000000002",
        native_session_id="00000000-0000-4000-8000-000000000002",
        backend="claude",
        state="running",
    )
    cwds: list[str] = []
    session = SimpleNamespace(
        record=rec,
        adapter=SimpleNamespace(assigns_conversation_id=True),
        hook_secret="secret",
        observation_state={},
        state_transitions=deque(),
        last_hook_ts=0.0,
        last_turn_hook_ts=0.0,
    )
    sessions = SimpleNamespace(
        sessions={rec.id: session},
        resolve=lambda _sid: session,
        maybe_heal_from_own_conversation_hook=heal,
        note_hook_cwd=lambda _session, payload: cwds.append(str(payload.get("cwd"))),
        note_hook_transcript_path=lambda _session, _payload: None,
    )
    app = web.Application(middlewares=[error_middleware, security_middleware])
    app[keys.SESSIONS] = sessions
    app[keys.EVENTS] = Events()
    app[keys.AUTOMATION] = SimpleNamespace(note_native_hook=lambda _sid: None)
    app[keys.HOOK_INGRESS_WINDOWS] = {}
    app.router.add_post("/api/hooks/{sid}", hook_ingress)
    headers = {"X-Mux-Hook-Secret": "secret"}
    snapshot = {**CLAUDE_SNAPSHOT, "session_id": rec.id, "delegate": {"ok": True}}
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            f"/api/hooks/{rec.id}",
            json={"event": STATUS_EVENT, "payload": snapshot},
            headers=headers,
        )
        stop = await client.post(
            f"/api/hooks/{rec.id}",
            json={"event": "Stop", "payload": {"session_id": rec.id}},
            headers=headers,
        )

    assert response.status == 200
    assert stop.status == 200
    assert [event for event, _ in applied] == [STATUS_EVENT, "Stop"]
    # The snapshot never lands on the bus whole; a lifecycle hook still does.
    assert emitted == ["Stop"]
    # It still tells the daemon where the CLI is standing, like every hook.
    assert cwds == ["D:/PROJECTS/swe-mux", "None"]
    # And it is not turn evidence: the staleness stamp stays where it was.
    assert session.last_turn_hook_ts == 0.0 or session.last_turn_hook_ts == session.last_hook_ts


# --- the adapter: where the tee is written ----------------------------------


def test_the_tee_is_written_only_beside_an_identity_and_only_with_a_delegate(
    tmp_path: Path,
) -> None:
    delegate = StatusLineDelegate(
        command='bash "C:/Users/me/.claude/hooks/combined-status.sh"',
        source="~/.claude/settings.json",
        padding=2,
        refresh_interval=5,
        hide_vim_mode_indicator=True,
    )
    adapter = ClaudeAdapter(data_dir=tmp_path, status_line_resolver=lambda _cwd: delegate)
    # The base settings file has no identity, so no tee: the shim would have
    # nowhere to post and the user's command would be wrapped for nothing.
    assert "statusLine" not in json.loads((tmp_path / "claude-hooks.json").read_text("utf-8"))

    spec = adapter.spawn_spec(
        "native",
        SpawnOptions(
            tmp_path,
            session_id="mux-session",
            hook_url="http://127.0.0.1:8765/api/hooks/mux-session",
            hook_secret="pane-secret",
        ),
    )
    settings_path = Path(spec.argv[spec.argv.index("--settings") + 1])
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    identity_path = tmp_path / "sessions" / "mux-session" / "hook-identity.json"
    identity = json.loads(identity_path.read_text(encoding="utf-8"))

    entry = settings["statusLine"]
    assert entry["type"] == "command"
    assert shlex.split(entry["command"])[1:] == [
        "-m",
        "swe_mux.hook_client",
        STATUS_EVENT,
        "--identity",
        str(identity_path),
    ]
    # The user's presentation keys travel with the replacement, so the only
    # thing that changed about their status line is who runs it.
    assert (entry["padding"], entry["refreshInterval"], entry["hideVimModeIndicator"]) == (
        2,
        5,
        True,
    )
    assert identity["status_line_command"] == delegate.command
    assert identity["status_line_source"] == "~/.claude/settings.json"
    # The hooks themselves are exactly what they were.
    assert set(settings["hooks"]) == set(
        json.loads((tmp_path / "claude-hooks.json").read_text("utf-8"))["hooks"]
    )


def test_no_delegate_means_no_tee_and_an_untouched_identity(tmp_path: Path) -> None:
    """Configuring a status line where the user had none would change their terminal."""
    for resolver in (None, lambda _cwd: None):
        adapter = ClaudeAdapter(data_dir=tmp_path, status_line_resolver=resolver)
        spec = adapter.spawn_spec(
            "native",
            SpawnOptions(
                tmp_path,
                session_id="mux-session",
                hook_url="http://127.0.0.1:8765/api/hooks/mux-session",
                hook_secret="pane-secret",
            ),
        )
        settings = json.loads(Path(spec.argv[spec.argv.index("--settings") + 1]).read_text("utf-8"))
        identity = json.loads(
            (tmp_path / "sessions" / "mux-session" / "hook-identity.json").read_text("utf-8")
        )
        assert "statusLine" not in settings
        assert "status_line_command" not in identity


def test_a_resolver_that_raises_costs_the_mirror_and_nothing_else(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(_cwd: Path) -> StatusLineDelegate | None:
        raise OSError("settings unreadable")

    adapter = ClaudeAdapter(data_dir=tmp_path, status_line_resolver=broken)
    with caplog.at_level("ERROR", logger="swe_mux.adapters.claude"):
        spec = adapter.spawn_spec(
            "native",
            SpawnOptions(
                tmp_path,
                session_id="s",
                hook_url="http://127.0.0.1:8765/api/hooks/s",
                hook_secret="x",
            ),
        )
    assert "--settings" in spec.argv
    assert any("resolving the delegate" in line for line in caplog.messages)


def test_the_production_factory_wires_the_resolver_for_claude_only() -> None:
    from swe_mux.adapters import build_agent_adapter
    from swe_mux.harness import HARNESSES

    claude = build_agent_adapter(
        HARNESSES["claude"],
        executable="claude",
        args=[],
        data_dir=Path("."),
        mcp_url="",
        instrument=False,
    )
    assert isinstance(claude, ClaudeAdapter)
    assert claude._status_line_resolver is not None
    for name, harness in HARNESSES.items():
        if harness.adapter_family != "claude" or name == "claude":
            continue
        other = build_agent_adapter(
            harness, executable=name, args=[], data_dir=Path("."), mcp_url="", instrument=False
        )
        assert isinstance(other, ClaudeAdapter)
        assert other._status_line_resolver is None, name


# --- the resolver: which command Claude would run ---------------------------


def _write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_the_delegate_follows_claudes_scope_precedence(tmp_path: Path) -> None:
    home = tmp_path / "home" / ".claude"
    cwd = tmp_path / "repo"
    assert resolve_status_line_delegate(cwd, home) is None

    _write(
        home / "settings.json",
        {"statusLine": {"type": "command", "command": "user-cmd", "padding": 1}},
    )
    found = resolve_status_line_delegate(cwd, home)
    assert found == StatusLineDelegate("user-cmd", "~/.claude/settings.json", padding=1)

    _write(
        cwd / ".claude" / "settings.json",
        {"statusLine": {"type": "command", "command": "project-cmd"}},
    )
    assert resolve_status_line_delegate(cwd, home).command == "project-cmd"  # type: ignore[union-attr]

    _write(
        cwd / ".claude" / "settings.local.json",
        {"statusLine": {"type": "command", "command": " local-cmd "}},
    )
    local = resolve_status_line_delegate(cwd, home)
    assert local is not None
    assert (local.command, local.source) == ("local-cmd", ".claude/settings.local.json")

    # A higher scope that declares none, or declares it malformed, does not
    # shadow a lower one - it is absent, and absence is not a value.
    _write(cwd / ".claude" / "settings.local.json", {"statusLine": None, "model": "opus"})
    assert resolve_status_line_delegate(cwd, home).command == "project-cmd"  # type: ignore[union-attr]
    _write(cwd / ".claude" / "settings.local.json", "not an object")
    assert resolve_status_line_delegate(cwd, home).command == "project-cmd"  # type: ignore[union-attr]
    (cwd / ".claude" / "settings.local.json").write_text("{ broken", encoding="utf-8")
    assert resolve_status_line_delegate(cwd, home).command == "project-cmd"  # type: ignore[union-attr]


def test_only_a_command_status_line_counts() -> None:
    assert (
        status_line_from_settings({"statusLine": {"type": "command", "command": ""}}, "s") is None
    )
    assert status_line_from_settings({"statusLine": {"type": "other", "command": "x"}}, "s") is None
    assert status_line_from_settings({"statusLine": "x"}, "s") is None
    assert status_line_from_settings({}, "s") is None
    full = status_line_from_settings(
        {
            "statusLine": {
                "type": "command",
                "command": "x",
                "padding": -1,
                "refreshInterval": 0,
                "hideVimModeIndicator": "yes",
            }
        },
        "s",
    )
    # Presentation keys that are not what Claude accepts are dropped, not copied.
    assert full == StatusLineDelegate("x", "s")


# --- the shim: the tee itself -----------------------------------------------


@pytest.fixture(autouse=True)
def _no_ambient_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("MUX_HOOK_URL", "MUX_HOOK_SECRET", "MUX_HOOK_SPOOL"):
        monkeypatch.delenv(name, raising=False)


def _identity(tmp_path: Path, **payload: str) -> Path:
    path = tmp_path / "hook-identity.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_the_tee_prints_the_delegate_first_and_posts_the_snapshot_second(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    order: list[str] = []
    posted: list[tuple[str, str, dict[str, Any], float]] = []
    identity = _identity(
        tmp_path,
        url="http://127.0.0.1:8765/api/hooks/pane",
        secret="pane-secret",
        status_line_command="bun run status.ts",
    )
    snapshot = json.dumps(CLAUDE_SNAPSHOT).encode("utf-8")
    monkeypatch.setattr(hook_client, "_read_payload_bytes", lambda: snapshot)

    def run_delegate(command: str, stdin: bytes) -> tuple[bytes, dict[str, object]]:
        assert command == "bun run status.ts"
        assert stdin == snapshot, "the delegate sees exactly the bytes the CLI wrote"
        order.append("delegate")
        return b"\x1b[32m8.5%\x1b[0m opus\n", {
            "ok": True,
            "exit_code": 0,
            "error": None,
            "elapsed_ms": 12,
        }

    def post_once(url: str, secret: str, body: bytes, timeout: float) -> bool:
        order.append("post")
        posted.append((url, secret, json.loads(body), timeout))
        return True

    monkeypatch.setattr(hook_client, "_run_delegate", run_delegate)
    monkeypatch.setattr(hook_client, "_post_once", post_once)
    monkeypatch.setattr(hook_client, "_post", lambda *_: pytest.fail("the retry path must not run"))
    monkeypatch.setattr(
        hook_client.sys, "argv", ["hook_client", "Status", "--identity", str(identity)]
    )

    hook_client.main()

    assert capsys.readouterr().out == "\x1b[32m8.5%\x1b[0m opus\n"
    assert order == ["delegate", "post"]
    url, secret, body, timeout = posted[0]
    assert (url, secret) == ("http://127.0.0.1:8765/api/hooks/pane", "pane-secret")
    assert body["event"] == "Status"
    assert body["payload"]["effort"] == {"level": "xhigh"}
    assert body["payload"]["delegate"] == {
        "ok": True,
        "exit_code": 0,
        "error": None,
        "elapsed_ms": 12,
    }
    assert timeout == hook_client._STATUS_POST_TIMEOUT


def test_the_tee_still_draws_when_the_daemon_is_unreachable_or_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    identity = _identity(tmp_path, status_line_command="echo drawn")
    monkeypatch.setattr(hook_client, "_read_payload_bytes", lambda: b"{}")
    monkeypatch.setattr(
        hook_client, "_run_delegate", lambda _c, _s: (b"drawn\n", {"ok": True, "exit_code": 0})
    )
    monkeypatch.setattr(
        hook_client, "_post_once", lambda *_: pytest.fail("no credentials, no POST")
    )
    monkeypatch.setattr(
        hook_client.sys, "argv", ["hook_client", "Status", "--identity", str(identity)]
    )

    hook_client.main()

    assert capsys.readouterr().out == "drawn\n"


def test_a_missing_delegate_is_reported_rather_than_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    identity = _identity(tmp_path, url="http://127.0.0.1:8765/api/hooks/pane", secret="s")
    posted: list[dict[str, Any]] = []
    monkeypatch.setattr(hook_client, "_read_payload_bytes", lambda: b'{"session_id": "c"}')
    monkeypatch.setattr(hook_client, "_run_delegate", lambda *_: pytest.fail("nothing to run"))
    monkeypatch.setattr(
        hook_client, "_post_once", lambda _u, _s, body, _t: posted.append(json.loads(body)) or True
    )
    monkeypatch.setattr(
        hook_client.sys, "argv", ["hook_client", "Status", "--identity", str(identity)]
    )

    hook_client.main()

    assert capsys.readouterr().out == ""
    assert posted[0]["payload"]["delegate"]["ok"] is False
    assert "no status line command" in posted[0]["payload"]["delegate"]["error"]


def test_the_delegate_runs_under_a_real_shell_with_the_snapshot_on_stdin() -> None:
    """`echo` exists under sh, Git Bash and PowerShell alike."""
    output, report = hook_client._run_delegate("echo tee-ok", b"{}")
    assert output.strip().endswith(b"tee-ok")
    assert report["ok"] is True
    assert report["exit_code"] == 0
    assert report["error"] is None
    assert isinstance(report["elapsed_ms"], int)


def test_a_delegate_that_fails_reports_its_exit_and_stderr_tail() -> None:
    output, report = hook_client._run_delegate("exit 3", b"{}")
    assert output == b""
    assert report["ok"] is False
    assert report["exit_code"] == 3
    assert str(report["error"]).startswith("exit 3")


def test_the_status_shell_is_the_one_claude_would_use(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.name != "nt":
        assert hook_client._status_shell("x") == ["/bin/sh", "-c", "x"]
        return
    monkeypatch.setenv("CLAUDE_CODE_GIT_BASH_PATH", r"C:\Git\bin\bash.exe")
    assert hook_client._status_shell("x") == [r"C:\Git\bin\bash.exe", "-c", "x"]
    monkeypatch.delenv("CLAUDE_CODE_GIT_BASH_PATH")
    monkeypatch.setattr(hook_client.shutil, "which", lambda _name: None)
    assert hook_client._status_shell("x")[0] == "powershell"
    assert hook_client._status_shell("x")[-1] == "x"


def test_the_shim_never_imports_the_package_for_the_tee() -> None:
    """The relay stays a relay: a fresh interpreter per message, stdlib only."""
    source = Path(hook_client.__file__).read_text(encoding="utf-8")
    assert "from . import" not in source
    assert "from .." not in source
    assert "import swe_mux" not in source
    assert sys.modules[hook_client.__name__] is hook_client
