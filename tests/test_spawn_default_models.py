"""Per-Project default models: the table that makes "omit model" mean something.

`request_spawn`'s description long promised "omit it to take the Project's
default" while no such default existed - the CLI's own sticky default applied,
which is whatever it last ran. These pin the committed
`.swe-mux/config.toml` field (`default_agent_models`, harness -> model in the
CLI's own spelling), its validation, and the spawn-time resolution: an explicit
model always wins, a stale default degrades to a diagnostic rather than a
failed spawn, and a Project with no entry changes nothing.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from swe_mux import app_keys as keys
from swe_mux.config import Config
from swe_mux.event_bus import EventBus
from swe_mux.models import ProjectRecord
from swe_mux.project_files import parse_project_config, serialize_project_config
from swe_mux.routes.sessions import spawn_session

# -- the committed field ------------------------------------------------------


def test_the_field_round_trips_through_the_project_config() -> None:
    encoded = serialize_project_config(
        {"default_agent_models": {"claude": "opus", "codex": "gpt-5.1-codex"}}
    )
    parsed = parse_project_config(encoded)
    assert parsed["default_agent_models"] == {
        "claude": "opus",
        "codex": "gpt-5.1-codex",
    }


def test_the_field_refuses_unregistered_harnesses_and_bad_shapes() -> None:
    with pytest.raises(ValueError, match="unregistered harnesses"):
        parse_project_config(
            serialize_project_config({"default_agent_models": {"vim": "opus"}})
        )
    with pytest.raises(ValueError, match="must be a table"):
        parse_project_config(b'version = 1\ndefault_agent_models = "opus"\n')
    with pytest.raises(ValueError, match="must be a table"):
        parse_project_config(b'version = 1\ndefault_agent_models = { claude = "" }\n')


def test_a_model_name_is_shape_checked_only_at_parse_time() -> None:
    """Deliberate: parse runs on every read and a CLI's catalogue moves under a
    committed file, so a name gone stale must degrade at spawn, not brick the
    whole config."""
    parsed = parse_project_config(
        serialize_project_config({"default_agent_models": {"codex": "opus"}})
    )
    assert parsed["default_agent_models"] == {"codex": "opus"}


# -- spawn-time resolution ----------------------------------------------------


def _project_with_config(tmp_path: Path, table: str) -> ProjectRecord:
    (tmp_path / ".swe-mux").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".swe-mux" / "config.toml").write_text(
        f"version = 1\ndefault_agent_models = {table}\n", encoding="utf-8"
    )
    project = ProjectRecord("default", "Main", str(tmp_path), 0)
    return project


def _spawn_request(tmp_path: Path, spawn: Any, body: dict[str, Any], table: str) -> Any:
    project = _project_with_config(tmp_path, table)
    app = {
        keys.CONFIG: Config(data_dir=tmp_path / "data"),
        keys.EVENTS: EventBus(),
        keys.SESSIONS: SimpleNamespace(spawn=spawn),
        keys.PROJECTS: SimpleNamespace(projects={"default": project}),
    }

    class Request:
        def __init__(self) -> None:
            self.app = app
            self.headers: dict[str, str] = {}

        async def json(self) -> dict[str, Any]:
            return body

    return Request()


@pytest.mark.asyncio
async def test_the_project_default_applies_when_the_spawn_names_no_model(
    tmp_path: Path,
) -> None:
    captured: list[dict[str, Any]] = []

    async def spawn(**kwargs: Any) -> Any:
        captured.append(kwargs)
        return SimpleNamespace(record=SimpleNamespace(snapshot=lambda: kwargs))

    request = _spawn_request(
        tmp_path,
        spawn,
        {"backend": "claude", "project_id": "default"},
        '{ claude = "opus" }',
    )
    await spawn_session(cast(Any, request))
    assert captured[0]["args"] == ["--model", "opus"]


@pytest.mark.asyncio
async def test_an_explicit_model_beats_the_project_default(tmp_path: Path) -> None:
    captured: list[dict[str, Any]] = []

    async def spawn(**kwargs: Any) -> Any:
        captured.append(kwargs)
        return SimpleNamespace(record=SimpleNamespace(snapshot=lambda: kwargs))

    request = _spawn_request(
        tmp_path,
        spawn,
        {"backend": "claude", "project_id": "default", "model": "opus 5"},
        '{ claude = "opus" }',
    )
    await spawn_session(cast(Any, request))
    assert captured[0]["args"] == ["--model", "claude-opus-5"]


@pytest.mark.asyncio
async def test_a_stale_default_degrades_to_a_diagnostic_not_a_failed_spawn(
    tmp_path: Path,
) -> None:
    """A default is a default: one stale name in a shared repository file must
    not stop every session in the Project from starting. The explicit-model
    path keeps refusing, which the spawn contract's own tests already pin."""
    captured: list[dict[str, Any]] = []

    async def spawn(**kwargs: Any) -> Any:
        captured.append(kwargs)
        return SimpleNamespace(record=SimpleNamespace(snapshot=lambda: kwargs))

    request = _spawn_request(
        tmp_path,
        spawn,
        {"backend": "codex", "project_id": "default"},
        '{ codex = "opus" }',  # a claude name: codex's vocabulary refuses it
    )
    events = request.app[keys.EVENTS].subscribe(name="test")
    await spawn_session(cast(Any, request))
    assert captured[0]["args"] == []
    emitted = [events.get_nowait() for _ in range(events.qsize())]
    assert "project_default_model_unavailable" in [event.type for event in emitted]


@pytest.mark.asyncio
async def test_a_harness_with_no_entry_is_untouched(tmp_path: Path) -> None:
    captured: list[dict[str, Any]] = []

    async def spawn(**kwargs: Any) -> Any:
        captured.append(kwargs)
        return SimpleNamespace(record=SimpleNamespace(snapshot=lambda: kwargs))

    request = _spawn_request(
        tmp_path,
        spawn,
        {"backend": "codex", "project_id": "default"},
        '{ claude = "opus" }',
    )
    await spawn_session(cast(Any, request))
    assert captured[0]["args"] == []
