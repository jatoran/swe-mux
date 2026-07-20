from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from swe_mux.event_bus import EventBus
from swe_mux.meta_hooks import HookConfigError, MetaHookEngine, parse_hook_rules
from swe_mux.models import MuxEvent


def manager() -> Any:
    record = SimpleNamespace(name="builder", backend="claude", cwd=str(Path.cwd()))
    session = SimpleNamespace(record=record)
    return SimpleNamespace(sessions={"mux-1": session})


def test_hook_schema_rejects_unknown_templates_and_unbounded_retry() -> None:
    with pytest.raises(HookConfigError, match="unsupported template"):
        parse_hook_rules('[[hook]]\n[hook.action]\nkind="write_pty"\ntext="{secret}"\n')
    with pytest.raises(HookConfigError, match="retries is out of range"):
        parse_hook_rules(
            '[[hook]]\n[hook.action]\nkind="http"\nurl="https://example.test"\nretries=99\n'
        )


@pytest.mark.asyncio
async def test_invalid_reload_retains_last_known_good_and_emits_diagnostic(
    tmp_path: Path,
) -> None:
    path = tmp_path / "hooks.toml"
    path.write_text(
        '[[hook]]\n[hook.match]\ntype="approval_*"\n[hook.action]\nkind="notify"\nchannel="ui"\n',
        encoding="utf-8",
    )
    events = EventBus()
    queue = events.subscribe()
    engine = MetaHookEngine(path, events, cast(Any, manager()))
    await engine._reload()
    assert len(engine.rules) == 1
    while not queue.empty():
        queue.get_nowait()

    path.write_text("[[hook]\n", encoding="utf-8")
    await engine._reload()
    assert len(engine.rules) == 1
    assert engine.diagnostic["status"] == "invalid"
    assert engine.diagnostic["retained_last_known_good"] is True
    assert (await queue.get()).type == "hook_reload_failed"


@pytest.mark.asyncio
async def test_ui_notification_creates_provider_neutral_delivery_record(
    tmp_path: Path,
) -> None:
    events = EventBus()
    engine = MetaHookEngine(tmp_path / "hooks.toml", events, cast(Any, manager()))
    event = MuxEvent(10.0, "mux-1", "hook", "approval_needed", {}, seq=42)
    await engine._act(
        {
            "kind": "notify",
            "provider": "ui",
            "channel": "attention",
            "sender": "daemon",
            "reply_target": "mux-1",
        },
        event,
    )
    delivery = engine.deliveries[0]
    assert delivery.correlation_id == "42"
    assert delivery.status == "delivered"
    assert delivery.attempts == 1
    assert delivery.reply_target == "mux-1"
    assert engine.notifications[0]["delivery_id"] == delivery.id
