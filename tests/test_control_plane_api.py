from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux.automation_store import AutomationStore
from swe_mux.config import Config
from swe_mux.models import SessionRecord
from swe_mux.server import (
    automation_notifications,
    create_observer_batch,
    delete_session,
    error_middleware,
    export_handoff,
    get_automation_status,
    list_annotations,
    list_lineage,
    patch_automation_notification,
    patch_automation_notifications,
    relaunch_session,
    second_opinion,
)


class EventsStub:
    """Records durable telemetry without a SQLite sink or a running task."""

    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict[str, Any]]] = []

    def emit_background(self, event_type: str, **payload: Any) -> None:
        self.emitted.append((event_type, payload))

    def payload(self, event_type: str) -> dict[str, Any]:
        matches = [payload for kind, payload in self.emitted if kind == event_type]
        assert len(matches) == 1, f"expected exactly one {event_type}, got {len(matches)}"
        return matches[0]


class HistoryStub:
    def __init__(self, row: dict[str, Any]) -> None:
        self.row = row

    async def history_entry(self, identity: str) -> dict[str, Any] | None:
        return self.row if identity == self.row["id"] else None


class ProjectsStub:
    def __init__(self) -> None:
        self.projects = {
            "default": SimpleNamespace(
                id="default",
                name="Main",
                root=".",
                layout={"version": 3, "root": None},
                layout_revision=0,
            )
        }

    async def update(self, identity: str, **changes: Any) -> Any:
        project = self.projects[identity]
        project.layout = changes["layout"]
        project.layout_revision += 1
        return project


async def test_delete_crashed_session_dismisses_it_without_stopping_pty(
    tmp_path: Path,
) -> None:
    record = SimpleNamespace(id="crashed-session", state="crashed", exit_code=1)
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
    events = EventsStub()
    request = SimpleNamespace(
        app={
            "sessions": manager,
            "config": SimpleNamespace(data_dir=tmp_path),
            "events": events,
        },
        match_info={"sid": record.id},
    )

    response = await delete_session(cast(Any, request))

    assert response.status == 200
    assert record.id not in manager.sessions
    removed = events.payload("session_removed")
    assert removed["was_live"] is False
    assert removed["exit_code"] == 1


async def test_delete_live_session_stops_it_and_records_how_long_that_took(
    tmp_path: Path,
) -> None:
    """The UI removes a killed session on sight, so this event is the only place the
    real cost of a close is recorded. Losing it would make a close that starts taking
    ten seconds invisible to every observer."""
    record = SimpleNamespace(id="live-session", state="running", exit_code=None)
    session = SimpleNamespace(record=record)
    stopped: list[str] = []

    class SessionsStub:
        def __init__(self) -> None:
            self.sessions = {record.id: session}

        def resolve(self, identity: str) -> Any:
            return self.sessions[identity]

        async def stop(self, identity: str) -> None:
            stopped.append(identity)
            record.state = "exited"
            record.exit_code = 0

    manager = SessionsStub()
    events = EventsStub()
    request = SimpleNamespace(
        app={
            "sessions": manager,
            "config": SimpleNamespace(data_dir=tmp_path),
            "events": events,
        },
        match_info={"sid": record.id},
    )

    response = await delete_session(cast(Any, request))

    assert response.status == 200
    assert stopped == [record.id]
    assert record.id not in manager.sessions
    removed = events.payload("session_removed")
    assert removed["was_live"] is True
    assert removed["exit_code"] == 0
    assert removed["stop_ms"] >= 0
    assert removed["total_ms"] >= removed["stop_ms"]


async def test_relaunch_replays_task_shell_and_retires_the_old_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_record = SimpleNamespace(
        id="old-task",
        project_id="default",
        name="frontend",
        exe="pwsh.exe",
        args=["-NoLogo", "-Command", "npm 'run' 'dev'"],
        completion_mode="one_shot",
        state="running",
        relaunchable=True,
        cold=False,
        backend="shell",
        spawn_cwd=r"D:\project\frontend",
        spawn_env={"PORT": "45603"},
    )
    old = SimpleNamespace(record=old_record)
    new_record = SimpleNamespace(
        id="new-task", relaunchable=False, snapshot=lambda: {"id": "new-task"}
    )
    published: list[bool] = []
    new = SimpleNamespace(record=new_record, publish_update=lambda: published.append(True))
    stopped: list[str] = []

    class SessionsStub:
        def __init__(self) -> None:
            self.sessions = {old_record.id: old}

        def resolve(self, identity: str) -> Any:
            return self.sessions[identity]

        async def stop(self, identity: str) -> None:
            stopped.append(identity)

    manager = SessionsStub()
    captured: dict[str, Any] = {}

    async def fake_spawn(_app: Any, body: dict[str, Any]) -> Any:
        captured["body"] = body
        manager.sessions[new_record.id] = new
        return new

    monkeypatch.setattr("swe_mux.server._spawn_from_body", fake_spawn)
    request = SimpleNamespace(
        app={"sessions": manager, "config": SimpleNamespace(data_dir=tmp_path)},
        match_info={"sid": old_record.id},
    )

    response = await relaunch_session(cast(Any, request))

    assert response.status == 201
    # Exact replay from the record: no task file re-read, no trust re-approval. cwd
    # and env are replayed too, or a relaunched task would silently run in the
    # project root without the environment its step declared.
    assert captured["body"]["executable"] == "pwsh.exe"
    assert captured["body"]["argv"] == ["-NoLogo", "-Command", "npm 'run' 'dev'"]
    assert captured["body"]["completion_mode"] == "one_shot"
    assert captured["body"]["cwd"] == r"D:\project\frontend"
    assert captured["body"]["env"] == {"PORT": "45603"}
    # The running original is stopped and removed; the replacement carries the flag onward.
    assert stopped == [old_record.id]
    assert old_record.id not in manager.sessions
    assert new_record.relaunchable is True
    assert published
    assert json.loads(response.text or "")["replaced"] == old_record.id


async def test_relaunch_refuses_a_non_relaunchable_session(tmp_path: Path) -> None:
    record = SimpleNamespace(id="agent", relaunchable=False, cold=False, backend="claude")
    session = SimpleNamespace(record=record)

    class SessionsStub:
        def __init__(self) -> None:
            self.sessions = {record.id: session}

        def resolve(self, identity: str) -> Any:
            return self.sessions[identity]

        async def stop(self, _identity: str) -> None:
            raise AssertionError("a non-relaunchable session must never be stopped")

    request = SimpleNamespace(
        app={"sessions": SessionsStub(), "config": SimpleNamespace(data_dir=tmp_path)},
        match_info={"sid": record.id},
    )

    with pytest.raises(ValueError):
        await relaunch_session(cast(Any, request))


async def test_a_recovered_shell_relaunches_without_becoming_a_task_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one deliberate widening of the relaunchable gate.

    That gate exists to keep this away from a live lifecycle, and a cold session
    has none. But re-running a recovered shell's command must not promote it into
    a task terminal: `relaunchable` drives an affordance that only makes sense for
    a step whose argv the daemon vouches for.
    """
    old_record = SimpleNamespace(
        id="cold-shell",
        project_id="default",
        name="build",
        exe="pwsh.exe",
        args=["-NoLogo"],
        completion_mode="interactive",
        state="crashed",
        relaunchable=False,
        cold=True,
        backend="shell",
        spawn_cwd=r"D:\project",
        spawn_env={},
    )
    old = SimpleNamespace(record=old_record)
    new_record = SimpleNamespace(
        id="restarted", relaunchable=True, snapshot=lambda: {"id": "restarted"}
    )
    new = SimpleNamespace(record=new_record, publish_update=lambda: None)

    class SessionsStub:
        def __init__(self) -> None:
            self.sessions = {old_record.id: old}

        def resolve(self, identity: str) -> Any:
            return self.sessions[identity]

        async def stop(self, _identity: str) -> None:
            raise AssertionError("a session with no process must never be stopped")

    manager = SessionsStub()

    async def fake_spawn(_app: Any, _body: dict[str, Any]) -> Any:
        manager.sessions[new_record.id] = new
        return new

    monkeypatch.setattr("swe_mux.server._spawn_from_body", fake_spawn)
    request = SimpleNamespace(
        app={"sessions": manager, "config": SimpleNamespace(data_dir=tmp_path)},
        match_info={"sid": old_record.id},
    )

    response = await relaunch_session(cast(Any, request))

    assert response.status == 201
    assert new_record.relaunchable is False
    assert old_record.id not in manager.sessions


async def test_a_recovered_agent_is_resumed_rather_than_relaunched(tmp_path: Path) -> None:
    """Replaying an agent's argv would start a fresh conversation while
    re-injecting the old one's `--session-id`, where the operator asked to return
    to the conversation."""
    record = SimpleNamespace(
        id="cold-agent", relaunchable=False, cold=True, backend="claude", exe="claude"
    )
    session = SimpleNamespace(record=record)

    class SessionsStub:
        def __init__(self) -> None:
            self.sessions = {record.id: session}

        def resolve(self, identity: str) -> Any:
            return self.sessions[identity]

    request = SimpleNamespace(
        app={"sessions": SessionsStub(), "config": SimpleNamespace(data_dir=tmp_path)},
        match_info={"sid": record.id},
    )

    with pytest.raises(ValueError, match="resumed, not relaunched"):
        await relaunch_session(cast(Any, request))


async def test_review_requires_preview_then_explicit_confirm_and_records_lineage(
    tmp_path: Path, monkeypatch: Any
) -> None:
    source = {
        "id": "run-source",
        "backend": "claude",
        "name": "builder",
        "cwd": str(tmp_path),
        "project_id": "default",
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
    app["projects"] = ProjectsStub()
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
                "project_id": "default",
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
                "project_id": "default",
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
    transcript_path = tmp_path / ".codex" / "sessions" / "run-source.jsonl"
    transcript_path.parent.mkdir(parents=True)
    transcript_path.write_text('{"type":"message"}\n', encoding="utf-8")
    source = {
        "id": "run-source",
        "native_id": "provider-run-source",
        "backend": "codex",
        "name": "verifier",
        "cwd": str(tmp_path),
        "transcript_path": str(transcript_path),
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
    assert "swe-mux history ID: run-source" in payload["markdown"]
    assert "Provider session ID: provider-run-source" in payload["markdown"]
    assert f"`{transcript_path}`" in payload["markdown"]
    assert "Read this provider-native file directly" in payload["markdown"]
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


async def test_attention_records_dismiss_individually_and_in_bulk(tmp_path: Path) -> None:
    """The inbox is append-only until retention ages a row out; dismissal is the only
    way a human can clear a detector that fired on something they judged normal."""
    store = AutomationStore(tmp_path / "mux.db")
    created = [
        await store.notify(
            agent_run_id=None,
            session_id=None,
            rule_id=None,
            kind="environment_interlock",
            title=f"Record {index}",
            message="noise",
            severity="warning",
        )
        for index in range(3)
    ]
    app = web.Application(middlewares=[error_middleware])
    app["automation_store"] = store
    app.router.add_get("/notifications", automation_notifications)
    app.router.add_patch("/notifications", patch_automation_notifications)
    app.router.add_patch("/notifications/{notification_id}", patch_automation_notification)

    async with TestClient(TestServer(app)) as client:
        one = await client.patch(f"/notifications/{created[0]['id']}", json={"read": True})
        after_one = await (await client.get("/notifications?unread=1")).json()
        bulk = await (await client.patch("/notifications", json={"read": True})).json()
        after_bulk = await (await client.get("/notifications?unread=1")).json()
        restored = await client.patch(f"/notifications/{created[0]['id']}", json={"read": False})
        after_restore = await (await client.get("/notifications?unread=1")).json()

    assert one.status == 200
    assert len(after_one["items"]) == 2
    # Only the still-open records are touched, so the count is what the button cleared.
    assert bulk["changed"] == 2
    assert after_bulk["items"] == []
    # Dismissing is not deleting: every record is still readable afterwards.
    assert restored.status == 200
    assert [item["title"] for item in after_restore["items"]] == ["Record 0"]
    assert len(await store.notifications()) == 3
    store.close()


async def _seed_findings(store: AutomationStore) -> None:
    """Two run-anchored findings, one project-anchored, and one other Project."""
    await store.create_annotation(
        agent_run_id="run-a",
        project_id="proj",
        tag="loop-detected",
        content="looping",
        provenance="deterministic_consumer",
    )
    await store.create_annotation(
        agent_run_id="run-b",
        project_id="proj",
        tag="declared-vs-verified",
        content="claimed done",
        provenance="deterministic_consumer",
    )
    await store.create_annotation(
        project_id="proj",
        tag="doc-debt",
        content="docs owe an update",
        provenance="deterministic_consumer",
    )
    await store.create_annotation(
        agent_run_id="run-x",
        project_id="other",
        tag="provenance",
        content="cross-session edge",
        provenance="deterministic_consumer",
    )


async def test_annotation_store_filters_resolve_runs_since_and_counts(
    tmp_path: Path,
) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    await _seed_findings(store)

    # Project scope: everything anchored to this Project, nothing from another.
    proj = await store.annotations(project_id="proj")
    assert {row["tag"] for row in proj} == {
        "loop-detected",
        "declared-vs-verified",
        "doc-debt",
    }

    # Session scope is the resolved run-id set. The project-anchored doc-debt row
    # has no run, so it is structurally absent - the "off vs quiet" case.
    sess = await store.annotations(agent_run_ids=["run-a", "run-b"], project_id="proj")
    assert {row["tag"] for row in sess} == {"loop-detected", "declared-vs-verified"}

    # A session resolved to zero runs matches nothing, not everything.
    assert await store.annotations(agent_run_ids=[]) == []

    # `since` filters on created_at: the oldest stamp keeps all, a future stamp none.
    oldest = min(row["created_at"] for row in proj)
    assert len(await store.annotations(since=oldest, project_id="proj")) == 3
    assert await store.annotations(since=oldest + 1e9, project_id="proj") == []

    # tag_counts honour scope but ignore the tag chip.
    assert await store.annotation_tag_counts(project_id="proj") == {
        "loop-detected": 1,
        "declared-vs-verified": 1,
        "doc-debt": 1,
    }
    assert await store.annotation_tag_counts(
        agent_run_ids=["run-a", "run-b"]
    ) == {"loop-detected": 1, "declared-vs-verified": 1}
    assert await store.annotation_tag_counts(agent_run_ids=[]) == {}
    store.close()


async def test_list_annotations_endpoint_scopes_and_reports_tag_counts(
    tmp_path: Path,
) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    await _seed_findings(store)

    live = SimpleNamespace(record=SimpleNamespace(agent_run_id="run-a"))

    class SessionRunHistory:
        async def agent_runs_for_session(self, session_id: str) -> list[dict[str, Any]]:
            assert session_id == "sess"
            # A superseded run of the same session (a /clear mints a fresh one).
            return [{"agent_run_id": "run-b"}]

    app = web.Application(middlewares=[error_middleware])
    app["automation_store"] = store
    app["sessions"] = SimpleNamespace(sessions={"sess": live})
    app["history"] = SessionRunHistory()
    app.router.add_get("/api/annotations", list_annotations)

    async with TestClient(TestServer(app)) as client:
        project = await (await client.get("/api/annotations?project_id=proj")).json()
        assert {item["tag"] for item in project["items"]} == {
            "loop-detected",
            "declared-vs-verified",
            "doc-debt",
        }
        assert project["tag_counts"] == {
            "loop-detected": 1,
            "declared-vs-verified": 1,
            "doc-debt": 1,
        }

        # The session's live run plus its superseded run; doc-debt (no run) absent.
        session = await (await client.get("/api/annotations?session_id=sess")).json()
        assert {item["tag"] for item in session["items"]} == {
            "loop-detected",
            "declared-vs-verified",
        }
        assert "doc-debt" not in session["tag_counts"]

        # The tag chip narrows the rows but never the counts.
        one = await (
            await client.get("/api/annotations?session_id=sess&tag=loop-detected")
        ).json()
        assert [item["tag"] for item in one["items"]] == ["loop-detected"]
        assert one["tag_counts"] == {
            "loop-detected": 1,
            "declared-vs-verified": 1,
        }
    store.close()
