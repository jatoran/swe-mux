from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux.automation_store import AutomationStore
from swe_mux.config import Config
from swe_mux.models import SessionRecord
from swe_mux.server import (
    create_observer_batch,
    delete_session,
    error_middleware,
    export_handoff,
    get_automation_status,
    list_lineage,
    second_opinion,
)


class HistoryStub:
    def __init__(self, row: dict[str, Any]) -> None:
        self.row = row

    async def history_entry(self, identity: str) -> dict[str, Any] | None:
        return self.row if identity == self.row["id"] else None


class SpacesStub:
    def __init__(self) -> None:
        self.spaces = {
            "default": SimpleNamespace(
                id="default", layout={"version": 3, "root": None}, layout_revision=0
            )
        }

    async def update(self, identity: str, **changes: Any) -> Any:
        space = self.spaces[identity]
        space.layout = changes["layout"]
        space.layout_revision += 1
        return space


async def test_delete_crashed_session_dismisses_it_without_stopping_pty(
    tmp_path: Path,
) -> None:
    record = SimpleNamespace(id="crashed-session", state="crashed")
    session = SimpleNamespace(record=record)

    class SessionsStub:
        def __init__(self) -> None:
            self.sessions = {record.id: session}

        def resolve(self, identity: str) -> Any:
            assert identity == record.id
            return session

        async def stop(self, _identity: str) -> None:
            raise AssertionError("an ended session must not stop its PTY again")

    manager = SessionsStub()
    request = SimpleNamespace(
        app={"sessions": manager, "config": SimpleNamespace(data_dir=tmp_path)},
        match_info={"sid": record.id},
    )

    response = await delete_session(cast(Any, request))

    assert response.status == 200
    assert record.id not in manager.sessions


async def test_review_requires_preview_then_explicit_confirm_and_records_lineage(
    tmp_path: Path, monkeypatch: Any
) -> None:
    source = {
        "id": "run-source",
        "backend": "claude",
        "name": "builder",
        "cwd": str(tmp_path),
        "space_id": "default",
    }
    store = AutomationStore(tmp_path / "mux.db")
    await store.create_annotation(
        agent_run_id="run-source",
        session_id="pty-source",
        tag="turn-summary",
        content="Implemented the parser.",
        source_event_seq=1,
        rule_id="builtin.turn-summarizer",
        rule_revision="r1",
        provenance="openrouter_observer",
    )
    spawn_bodies: list[dict[str, Any]] = []

    async def spawn_stub(_app: web.Application, body: dict[str, Any]) -> Any:
        spawn_bodies.append(body)
        record = SessionRecord(
            "pty-review",
            "codex review",
            "default",
            "codex",
            "native-review",
            str(tmp_path),
            "codex",
            [],
        )
        record.agent_run_id = "run-review"
        return SimpleNamespace(record=record)

    async def git_stub(_cwd: str, *args: str) -> tuple[int, str]:
        if args[:2] == ("status", "--short"):
            return 0, "## main\n M src/parser.py"
        return 0, "src/parser.py | 4 +++-"

    monkeypatch.setattr("swe_mux.server._spawn_from_body", spawn_stub)
    monkeypatch.setattr("swe_mux.server._git", git_stub)
    app = web.Application(middlewares=[error_middleware])
    app["history"] = HistoryStub(source)
    app["automation_store"] = store
    app["sessions"] = SimpleNamespace(sessions={}, stop=lambda _identity: None)
    app["spaces"] = SpacesStub()
    app.router.add_post("/review/{sid}", second_opinion)
    app.router.add_get("/lineage", list_lineage)

    async with TestClient(TestServer(app)) as client:
        preview_response = await client.post(
            "/review/run-source", json={"confirm": False, "instructions": "Focus on tests."}
        )
        preview = await preview_response.json()
        assert preview_response.status == 200
        assert preview["spawned"] is False
        assert preview["preview"]["backend"] == "codex"
        assert "Implemented the parser" in preview["preview"]["prompt"]
        assert "Focus on tests" in preview["preview"]["prompt"]
        assert "Current bounded worktree context" in preview["preview"]["prompt"]
        assert "src/parser.py | 4 +++-" in preview["preview"]["worktree_context"]
        assert spawn_bodies == []

        stale_response = await client.post(
            "/review/run-source",
            json={
                "confirm": True,
                "backend": "codex",
                "space": "default",
                "instructions": "Focus on tests.",
            },
        )
        assert stale_response.status == 400
        assert spawn_bodies == []

        confirmed_response = await client.post(
            "/review/run-source",
            json={
                "confirm": True,
                "backend": "codex",
                "space": "default",
                "instructions": "Focus on tests.",
                "preview_token": preview["preview"]["preview_token"],
            },
        )
        confirmed = await confirmed_response.json()
        lineage_response = await client.get("/lineage?run_id=run-source")
        lineage = await lineage_response.json()

    assert confirmed_response.status == 201
    assert confirmed["spawned"] is True
    assert confirmed["session"]["agent_run_id"] == "run-review"
    assert len(spawn_bodies) == 1
    assert spawn_bodies[0]["backend"] == "codex"
    assert lineage["items"][0]["relation"] == "review"
    assert lineage["items"][0]["metadata"]["prompt_reviewed"] is True
    assert lineage["items"][0]["metadata"]["preview_token"] == preview["preview"]["preview_token"]
    store.close()


async def test_handoff_export_contains_only_reviewable_annotation_summary(tmp_path: Path) -> None:
    source = {
        "id": "run-source",
        "backend": "codex",
        "name": "verifier",
        "cwd": str(tmp_path),
    }
    store = AutomationStore(tmp_path / "mux.db")
    await store.create_annotation(
        agent_run_id="run-source",
        session_id="pty-source",
        tag="turn-summary",
        content="Tests pass after correcting the fixture.",
        source_event_seq=2,
        rule_id="builtin.turn-summarizer",
        rule_revision="r1",
        provenance="openrouter_observer",
    )
    app = web.Application()
    app["history"] = HistoryStub(source)
    app["automation_store"] = store
    app.router.add_get("/handoff/{sid}", export_handoff)

    async with TestClient(TestServer(app)) as client:
        response = await client.get("/handoff/run-source")
        payload = await response.json()

    assert response.status == 200
    assert "# Handoff: verifier" in payload["markdown"]
    assert "Tests pass after correcting the fixture" in payload["markdown"]
    assert "Review before using it as context" in payload["markdown"]
    store.close()


async def test_repository_rules_are_diagnostic_only_and_never_loaded(tmp_path: Path) -> None:
    project = tmp_path / "project"
    rules_path = project / ".swe-mux" / "rules.toml"
    rules_path.parent.mkdir(parents=True)
    rules_path.write_text(
        'version=1\n[[rule]]\nid="repo"\non="turn_ended"\n'
        'do=[{kind="notify",message="diagnostic only"}]\n',
        encoding="utf-8",
    )

    class ProjectHistory:
        async def project_scopes(self, *, include_hidden: bool = False) -> list[dict[str, Any]]:
            assert include_hidden is True
            return [{"id": "scope-project", "root": str(project)}]

    canonical_rules: list[Any] = []
    app = web.Application(middlewares=[error_middleware])
    app["config"] = Config(data_dir=tmp_path)
    app["history"] = ProjectHistory()
    app["automation"] = SimpleNamespace(
        rules=canonical_rules,
        status=lambda: {"enabled": False, "rules": canonical_rules},
    )
    app["hooks"] = SimpleNamespace(rules=[], diagnostic=None)
    app.router.add_get("/automation", get_automation_status)

    async with TestClient(TestServer(app)) as client:
        response = await client.get("/automation")
        payload = await response.json()

    assert response.status == 200
    assert canonical_rules == []
    assert payload["repository_rules"][0]["execution"] == "inert"
    assert payload["repository_rules"][0]["valid"] is True
    assert payload["repository_rules"][0]["rules"][0]["source"] == "repository-inert"


async def test_batch_observer_requires_ended_transcripts_and_explicit_confirmation(
    tmp_path: Path,
) -> None:
    row = {
        "id": "run-source",
        "backend": "claude",
        "name": "builder",
        "cwd": str(tmp_path),
        "exited_at": 12,
        "transcript_path": str(tmp_path / "claude.jsonl"),
    }
    app = web.Application(middlewares=[error_middleware])
    app["history"] = HistoryStub(row)
    app["config"] = Config(data_dir=tmp_path)
    app.router.add_post("/batches", create_observer_batch)

    async with TestClient(TestServer(app)) as client:
        preview_response = await client.post(
            "/batches",
            json={"kind": "experience", "run_ids": ["run-source"], "confirm": False},
        )
        preview = await preview_response.json()
        app["config"].automation_enabled = True
        unreviewed_response = await client.post(
            "/batches",
            json={"kind": "experience", "run_ids": ["run-source"], "confirm": True},
        )
        row["exited_at"] = None
        live_response = await client.post(
            "/batches",
            json={"kind": "experience", "run_ids": ["run-source"], "confirm": False},
        )

    assert preview_response.status == 200
    assert preview["preview"] is True
    assert len(preview["preview_token"]) == 64
    assert preview["estimate"]["repository_mutation"] is False
    assert unreviewed_response.status == 400
    assert live_response.status == 400
