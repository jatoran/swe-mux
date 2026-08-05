"""The session skills endpoint: scoping, refusal, and the not-loaded-yet flag."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux.agent_skills import clear_cache
from swe_mux.models import SessionRecord
from swe_mux.server import error_middleware, session_agent_environment, session_skills


@pytest.fixture(autouse=True)
def _isolated_cache():
    clear_cache()
    yield
    clear_cache()


class SessionStub:
    def __init__(self, record: SessionRecord) -> None:
        self.record = record
        self.agent_promoted_at: float | None = None


class ManagerStub:
    def __init__(self, session: SessionStub) -> None:
        self.session = session

    def resolve(self, _sid: str) -> SessionStub:
        return self.session


def record(**overrides: Any) -> SessionRecord:
    base = {
        "id": "sess-1",
        "name": "one",
        "project_id": "proj-1",
        "backend": "codex",
        "native_session_id": "native-1",
        "cwd": "C:/nowhere",
        "exe": "codex.exe",
        "args": [],
    }
    return SessionRecord(**{**base, **overrides})


def build(session_record: SessionRecord) -> web.Application:
    app = web.Application(middlewares=[error_middleware])
    app["sessions"] = ManagerStub(SessionStub(session_record))
    app.router.add_get("/api/sessions/{sid}/skills", session_skills)
    app.router.add_get("/api/sessions/{sid}/agent-environment", session_agent_environment)
    return app


def write_skill(root: Path, name: str, mtime: float | None = None) -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / "SKILL.md"
    manifest.write_text(f"---\nname: {name}\ndescription: does {name}\n---\n", encoding="utf-8")
    if mtime is not None:
        os.utime(manifest, (mtime, mtime))
    return manifest


async def test_shell_sessions_are_refused_rather_than_answered_emptily(tmp_path: Path) -> None:
    app = build(record(backend="shell", cwd=str(tmp_path)))
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/sessions/sess-1/skills")
        assert response.status == 409
        assert "agent sessions" in (await response.json())["error"]


async def test_repo_skills_follow_the_live_cwd_not_the_spawn_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    spawned = tmp_path / "primary"
    wandered = tmp_path / "worktree"
    write_skill(spawned / ".codex" / "skills", "primary-only")
    write_skill(wandered / ".codex" / "skills", "worktree-only")
    app = build(
        record(
            cwd=str(spawned),
            spawn_cwd=str(spawned),
            runtime_cwd=str(wandered),
            runtime_cwd_live=True,
        )
    )

    async with TestClient(TestServer(app)) as client:
        payload = await (await client.get("/api/sessions/sess-1/skills")).json()

    # A session that wandered into a worktree sees that worktree's skills, because
    # that is the directory its CLI resolves `.codex/skills` from.
    assert [skill["name"] for skill in payload["skills"]] == ["worktree-only"]
    assert payload["cwd"] == str(wandered)


async def test_untrusted_runtime_cwd_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    spawned = tmp_path / "primary"
    stale = tmp_path / "stale"
    write_skill(spawned / ".codex" / "skills", "primary-only")
    write_skill(stale / ".codex" / "skills", "stale-only")
    app = build(
        record(
            cwd=str(spawned),
            spawn_cwd=str(spawned),
            runtime_cwd=str(stale),
            runtime_cwd_live=False,
        )
    )

    async with TestClient(TestServer(app)) as client:
        payload = await (await client.get("/api/sessions/sess-1/skills")).json()

    assert [skill["name"] for skill in payload["skills"]] == ["primary-only"]


async def test_a_skill_newer_than_the_run_is_flagged_as_not_loaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(home))
    cwd = tmp_path / "repo"
    cwd.mkdir()
    write_skill(home / "skills", "was-there", mtime=1_000.0)
    write_skill(home / "skills", "added-since", mtime=3_000.0)
    app = build(record(cwd=str(cwd), agent_run_started_at=2_000.0))

    async with TestClient(TestServer(app)) as client:
        payload = await (await client.get("/api/sessions/sess-1/skills")).json()

    flags = {skill["name"]: skill["added_after_start"] for skill in payload["skills"]}
    # The CLI read its skills at startup, so this one exists but is not loaded —
    # the difference between a button that works and one that types a dead command.
    assert flags == {"added-since": True, "was-there": False}
    assert payload["agent_run_started_at"] == 2_000.0
    assert payload["agent_loaded_at"] == 2_000.0


async def test_refresh_bypasses_the_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(home))
    cwd = tmp_path / "repo"
    cwd.mkdir()
    write_skill(home / "skills", "first")
    app = build(record(cwd=str(cwd)))

    async with TestClient(TestServer(app)) as client:
        first = await (await client.get("/api/sessions/sess-1/skills")).json()
        write_skill(home / "skills", "second")
        cached = await (await client.get("/api/sessions/sess-1/skills")).json()
        fresh = await (await client.get("/api/sessions/sess-1/skills?refresh=1")).json()

    assert [skill["name"] for skill in first["skills"]] == ["first"]
    assert [skill["name"] for skill in cached["skills"]] == ["first"]
    assert [skill["name"] for skill in fresh["skills"]] == ["first", "second"]


async def test_agent_environment_is_session_scoped_and_shells_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(home))
    home.mkdir()
    cwd = tmp_path / "repo"
    cwd.mkdir()
    (home / "config.toml").write_text('model = "gpt-test"\n', encoding="utf-8")
    app = build(record(cwd=str(cwd), agent_run_started_at=1_500.0))
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/sessions/sess-1/agent-environment")
        payload = await response.json()
    assert response.status == 200
    assert payload["backend"] == "codex"
    assert payload["runtime"]["loaded_at"] == 1_500.0
    assert any(section["id"] == "policies" for section in payload["sections"])

    shell_app = build(record(backend="shell", cwd=str(cwd)))
    async with TestClient(TestServer(shell_app)) as client:
        response = await client.get("/api/sessions/sess-1/agent-environment")
        assert response.status == 409


async def test_agent_environment_keeps_the_cli_generation_across_conversation_rollover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(home))
    home.mkdir()
    cwd = tmp_path / "repo"
    cwd.mkdir()
    app = build(
        record(
            cwd=str(cwd),
            spawn_backend="codex",
            created_at=1_000.0,
            agent_loaded_at=1_000.0,
            agent_run_started_at=2_000.0,
            agent_run_seq=1,
        )
    )

    async with TestClient(TestServer(app)) as client:
        payload = await (await client.get("/api/sessions/sess-1/agent-environment")).json()

    assert payload["runtime"]["loaded_at"] == 1_000.0
    assert payload["runtime"]["run_started_at"] == 2_000.0
