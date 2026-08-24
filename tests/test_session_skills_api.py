"""The session skills endpoint: scoping, refusal, and the not-loaded-yet flag."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import app_keys as keys
from swe_mux import mcp_tools
from swe_mux.agent_environment import capture_config_baseline
from swe_mux.agent_environment import clear_cache as clear_environment_cache
from swe_mux.agent_skills import clear_cache
from swe_mux.models import SessionRecord
from swe_mux.server import (
    error_middleware,
    runtime_inventory_ingress,
    session_agent_environment,
    session_mcp_tools,
    session_skills,
)


@pytest.fixture(autouse=True)
def _isolated_cache():
    clear_cache()
    clear_environment_cache()
    mcp_tools.clear_cache()
    yield
    clear_cache()
    clear_environment_cache()
    mcp_tools.clear_cache()


class SessionStub:
    def __init__(self, record: SessionRecord) -> None:
        self.record = record
        self.agent_promoted_at: float | None = None
        self.hook_secret = "hook-secret"


class ManagerStub:
    def __init__(self, session: SessionStub) -> None:
        self.session = session
        self.ingress_url = "http://127.0.0.1:8765"
        self.sessions = {session.record.id: session}

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
    app[keys.SESSIONS] = ManagerStub(SessionStub(session_record))
    app[keys.RUNTIME_INVENTORIES] = mcp_tools.LiveSnapshotStore()
    app[keys.MCP_TOOLS_WINDOWS] = {}
    app.router.add_get("/api/sessions/{sid}/skills", session_skills)
    app.router.add_get("/api/sessions/{sid}/agent-environment", session_agent_environment)
    app.router.add_post("/api/sessions/{sid}/agent-environment/mcp-tools", session_mcp_tools)
    app.router.add_post("/api/sessions/{sid}/runtime-inventory", runtime_inventory_ingress)
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


async def test_remote_boundary_refuses_local_agent_integrations(tmp_path: Path) -> None:
    app = build(
        record(
            cwd=str(tmp_path),
            runtime_boundary="remote",
            remote_authority="example.test",
        )
    )
    async with TestClient(TestServer(app)) as client:
        for endpoint in ("skills", "agent-environment"):
            response = await client.get(f"/api/sessions/sess-1/{endpoint}")
            assert response.status == 409
            payload = await response.json()
            assert payload["code"] == "agent_bridge_unavailable"
            assert payload["capability"] == "agent-bridge-unavailable"
            assert payload["reason"] == "remote_terminal_boundary"
            assert payload["authority"] == "example.test"


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


async def test_agent_environment_serves_the_session_drift_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The endpoint's whole contribution to drift: hand over the record's snapshot.

    Without one the tab must say the question is untracked rather than answer
    it, which is what a session adopted from before baselines existed gets.
    """
    home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(home))
    home.mkdir()
    cwd = tmp_path / "repo"
    cwd.mkdir()
    config = home / "config.toml"
    config.write_text('model = "gpt"\n', encoding="utf-8")
    baseline = capture_config_baseline(backend="codex", cwd=cwd, args=[])

    untracked = build(record(cwd=str(cwd)))
    async with TestClient(TestServer(untracked)) as client:
        blind = await (await client.get("/api/sessions/sess-1/agent-environment")).json()

    config.write_text('model = "gpt-5"\n', encoding="utf-8")
    clear_environment_cache()
    tracked = build(record(cwd=str(cwd), agent_env_baseline=baseline))
    async with TestClient(TestServer(tracked)) as client:
        watched = await (await client.get("/api/sessions/sess-1/agent-environment")).json()

    assert blind["config_baseline"] == "unavailable"
    assert not any(source["changed_after_start"] for source in blind["sources"])
    assert watched["config_baseline"] == "captured"
    assert [
        source["label"] for source in watched["sources"] if source["changed_after_start"]
    ] == ["~/.codex/config.toml"]


# ---------------------------------------------------------------------------
# The explicit per-server tool fetch
# ---------------------------------------------------------------------------


def _codex_home_with_mcp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "codex-home"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    (home / "config.toml").write_text(
        "[mcp_servers.node_repl]\ncommand = \"node\"\nargs = [\"repl.js\"]\n",
        encoding="utf-8",
    )
    cwd = tmp_path / "repo"
    cwd.mkdir()
    return cwd


async def test_opening_the_tab_never_fetches_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The passive invariant, asserted rather than trusted.

    A regression here would not look like a bug: the drawer would simply be
    slower and would start MCP servers for anyone who opened it.
    """
    cwd = _codex_home_with_mcp(tmp_path, monkeypatch)
    app = build(record(cwd=str(cwd)))
    async with TestClient(TestServer(app)) as client:
        payload = await (await client.get("/api/sessions/sess-1/agent-environment")).json()
    mcp = next(section for section in payload["sections"] if section["id"] == "mcp")
    assert [item["name"] for item in mcp["items"]] == ["node_repl"]
    assert mcp["completeness"] == "configured_only"
    assert all("tools" not in item for item in mcp["items"])
    assert "connection and tool health are not probed" in mcp["items"][0]["description"]


async def test_fetching_tools_for_an_unknown_server_is_a_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd = _codex_home_with_mcp(tmp_path, monkeypatch)
    app = build(record(cwd=str(cwd)))
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/api/sessions/sess-1/agent-environment/mcp-tools", json={"server": "nope"}
        )
        assert response.status == 404
        blank = await client.post(
            "/api/sessions/sess-1/agent-environment/mcp-tools", json={"server": "  "}
        )
        assert blank.status == 400


async def test_a_shell_and_a_remote_boundary_are_refused_by_the_fetch_too(
    tmp_path: Path,
) -> None:
    shell = build(record(backend="shell", cwd=str(tmp_path)))
    async with TestClient(TestServer(shell)) as client:
        response = await client.post(
            "/api/sessions/sess-1/agent-environment/mcp-tools", json={"server": "x"}
        )
        assert response.status == 409

    remote = build(
        record(cwd=str(tmp_path), runtime_boundary="remote", remote_authority="example.test")
    )
    async with TestClient(TestServer(remote)) as client:
        response = await client.post(
            "/api/sessions/sess-1/agent-environment/mcp-tools", json={"server": "x"}
        )
        assert response.status == 409
        assert (await response.json())["code"] == "agent_bridge_unavailable"


async def test_a_passive_harness_answers_with_a_reason_rather_than_probing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "opencode-home"
    home.mkdir()
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    cwd = tmp_path / "repo"
    cwd.mkdir()
    (cwd / "opencode.json").write_text(
        json.dumps({"mcp": {"local": {"type": "local", "command": ["srv"]}}}), encoding="utf-8"
    )
    app = build(record(backend="opencode", cwd=str(cwd), exe="opencode"))
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/api/sessions/sess-1/agent-environment/mcp-tools", json={"server": "local"}
        )
        payload = await response.json()
    assert response.status == 200
    assert payload["evidence"] == "not_supported"
    assert payload["status"] == "unsupported"
    assert payload["diagnostic"]


async def test_an_omp_session_serves_the_inventory_its_extension_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "omp-home"
    home.mkdir()
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(home))
    cwd = tmp_path / "repo"
    cwd.mkdir()
    (cwd / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"My-Server": {"command": "srv"}}}), encoding="utf-8"
    )
    app = build(record(backend="omp", cwd=str(cwd), exe="omp"))

    async with TestClient(TestServer(app)) as client:
        before = await (
            await client.post(
                "/api/sessions/sess-1/agent-environment/mcp-tools", json={"server": "My-Server"}
            )
        ).json()
        response = await client.post(
            "/api/sessions/sess-1/runtime-inventory",
            headers={"X-Mux-Hook-Secret": "hook-secret"},
            json={
                "reason": "session_start",
                "tools": [
                    {"name": "mcp__my_server_do_thing", "description": "Does a thing"},
                    {"name": "read", "description": "a built-in"},
                ],
            },
        )
        published_status, published = response.status, await response.json()
        after = await (
            await client.post(
                "/api/sessions/sess-1/agent-environment/mcp-tools",
                json={"server": "My-Server", "refresh": True},
            )
        ).json()

    # Before the session reports anything, the honest answer is "not reported",
    # which is not the same claim as "this server publishes no tools".
    assert before["status"] == "unavailable"
    assert published_status == 200
    # Only the MCP-fronted tool is retained; `read` is a built-in the documented
    # catalog already covers.
    assert published["tools"] == 1
    assert after["status"] == "ok"
    assert after["evidence"] == "live_process"
    assert [tool["name"] for tool in after["tools"]] == ["mcp__my_server_do_thing"]


async def test_runtime_inventory_ingress_requires_the_session_secret(tmp_path: Path) -> None:
    app = build(record(backend="omp", cwd=str(tmp_path), exe="omp"))
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/api/sessions/sess-1/runtime-inventory",
            headers={"X-Mux-Hook-Secret": "wrong"},
            json={"tools": []},
        )
        assert response.status == 403
