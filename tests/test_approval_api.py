"""The approval endpoints: what they report, and what they refuse.

Every refusal here is named rather than silently downgrading to `wait`. An
operator who picks `allow_all`, gets `wait`, and is told nothing will conclude
the control does not work — and then stop trusting the one that does.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux.config import Config
from swe_mux.event_bus import EventBus
from swe_mux.models import ApprovalPolicy, SessionRecord
from swe_mux.server import (
    approve_pending_request,
    error_middleware,
    get_session_approvals,
    put_session_approvals,
)

pytestmark = pytest.mark.anyio


class SessionStub:
    def __init__(self, session_record: SessionRecord) -> None:
        self.record = session_record
        self.state_transitions: list[dict[str, Any]] = []
        self.published = 0

    def publish_update(self) -> None:
        self.published += 1


class ManagerStub:
    def __init__(self, session: SessionStub) -> None:
        self.session = session

    def resolve(self, _sid: str) -> SessionStub:
        return self.session


class ProjectStub:
    def __init__(self, root: Path) -> None:
        self.id = "proj-1"
        self.root = str(root)


class ProjectsStub:
    def __init__(self, project: ProjectStub | None) -> None:
        self.projects = {project.id: project} if project else {}


def record(**overrides: Any) -> SessionRecord:
    base: dict[str, Any] = {
        "id": "sess-1",
        "name": "one",
        "project_id": "proj-1",
        "backend": "claude",
        "native_session_id": "native-1",
        "cwd": "C:/nowhere",
        "exe": "claude.exe",
        "args": [],
        "agent_run_id": "run-1",
    }
    return SessionRecord(**{**base, **overrides})


def build(
    session_record: SessionRecord, root: Path, *, config: Config | None = None
) -> tuple[web.Application, SessionStub]:
    session = SessionStub(session_record)
    app = web.Application(middlewares=[error_middleware])
    app["sessions"] = ManagerStub(session)
    app["projects"] = ProjectsStub(ProjectStub(root))
    app["events"] = EventBus()
    app["config"] = config or Config(data_dir=root, approval_auto_enabled=True)
    app.router.add_get("/api/sessions/{sid}/approvals", get_session_approvals)
    app.router.add_put("/api/sessions/{sid}/approvals", put_session_approvals)
    app.router.add_post("/api/sessions/{sid}/approvals/approve-once", approve_pending_request)
    return app, session


def project_config(root: Path, body: str) -> None:
    (root / ".swe-mux").mkdir(parents=True, exist_ok=True)
    (root / ".swe-mux" / "config.toml").write_text(f"version = 1\n{body}", encoding="utf-8")


async def client_for(app: web.Application) -> TestClient:
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


# -- reporting ---------------------------------------------------------------


async def test_a_fresh_session_reports_wait_with_the_shipped_rules(tmp_path: Path) -> None:
    app, _ = build(record(), tmp_path)
    client = await client_for(app)
    try:
        body = await (await client.get("/api/sessions/sess-1/approvals")).json()
    finally:
        await client.close()
    assert body["policy"]["mode"] == "wait"
    assert body["effective_mode"] == "wait"
    assert body["supported"] is True
    assert body["rules_source"] == "default"
    assert "Read" in body["rules"]
    assert body["unavailable"] is None


async def test_the_install_switch_being_off_is_stated_rather_than_hidden(tmp_path: Path) -> None:
    app, _ = build(
        record(), tmp_path, config=Config(data_dir=tmp_path, approval_auto_enabled=False)
    )
    client = await client_for(app)
    try:
        body = await (await client.get("/api/sessions/sess-1/approvals")).json()
    finally:
        await client.close()
    assert body["enabled"] is False
    assert body["unavailable"] == "off for this install"


async def test_a_harness_that_cannot_answer_says_so_by_name(tmp_path: Path) -> None:
    app, _ = build(record(backend="codex", exe="codex.exe"), tmp_path)
    client = await client_for(app)
    try:
        body = await (await client.get("/api/sessions/sess-1/approvals")).json()
    finally:
        await client.close()
    assert body["supported"] is False
    assert "cannot answer approvals" in body["unavailable"]


async def test_project_rules_replace_the_defaults(tmp_path: Path) -> None:
    project_config(tmp_path, 'approval_allow = ["Read", "Bash(npm run *)"]\n')
    app, _ = build(record(), tmp_path)
    client = await client_for(app)
    try:
        body = await (await client.get("/api/sessions/sess-1/approvals")).json()
    finally:
        await client.close()
    assert body["rules"] == ["Read", "Bash(npm run *)"]
    assert body["rules_source"] == "project"


async def test_an_expired_grant_reports_its_stored_mode_and_an_effective_wait(
    tmp_path: Path,
) -> None:
    """Both facts are needed: "it lapsed" reads differently from "it was refused"."""
    session_record = record()
    session_record.approval_policy = ApprovalPolicy(
        mode="allow_all", run_id="run-1", expires_at=time.time() - 1
    )
    app, _ = build(session_record, tmp_path)
    client = await client_for(app)
    try:
        body = await (await client.get("/api/sessions/sess-1/approvals")).json()
    finally:
        await client.close()
    assert body["policy"]["mode"] == "allow_all"
    assert body["effective_mode"] == "wait"


# -- setting a mode ----------------------------------------------------------


async def test_setting_a_mode_binds_it_to_the_conversation_and_bounds_it(
    tmp_path: Path,
) -> None:
    app, session = build(record(), tmp_path)
    client = await client_for(app)
    try:
        body = await (
            await client.put("/api/sessions/sess-1/approvals", json={"mode": "allow_all"})
        ).json()
    finally:
        await client.close()
    assert body["policy"]["mode"] == "allow_all"
    assert body["policy"]["run_id"] == "run-1"
    assert body["policy"]["expires_at"] is not None
    assert body["policy"]["max_auto"] > 0
    assert session.published == 1


async def test_an_unknown_mode_is_refused(tmp_path: Path) -> None:
    app, _ = build(record(), tmp_path)
    client = await client_for(app)
    try:
        response = await client.put("/api/sessions/sess-1/approvals", json={"mode": "yolo"})
        body = await response.json()
    finally:
        await client.close()
    assert response.status == 400
    assert body["code"] == "invalid_mode"


async def test_a_project_ceiling_refuses_a_stronger_mode_and_says_which(
    tmp_path: Path,
) -> None:
    project_config(tmp_path, 'approval_ceiling = "allowlisted"\n')
    app, _ = build(record(), tmp_path)
    client = await client_for(app)
    try:
        response = await client.put("/api/sessions/sess-1/approvals", json={"mode": "allow_all"})
        body = await response.json()
        allowed = await client.put(
            "/api/sessions/sess-1/approvals", json={"mode": "allowlisted"}
        )
    finally:
        await client.close()
    assert response.status == 409
    assert body["code"] == "above_ceiling"
    assert "allowlisted" in body["error"]
    assert allowed.status == 200


async def test_a_project_may_forbid_auto_approval_entirely(tmp_path: Path) -> None:
    project_config(tmp_path, 'approval_ceiling = "wait"\n')
    app, _ = build(record(), tmp_path)
    client = await client_for(app)
    try:
        response = await client.put(
            "/api/sessions/sess-1/approvals", json={"mode": "allowlisted"}
        )
        body = await response.json()
    finally:
        await client.close()
    assert response.status == 409
    assert body["code"] == "approvals_unavailable"


async def test_an_empty_project_allowlist_refuses_allowlisted_rather_than_granting_nothing(
    tmp_path: Path,
) -> None:
    """Otherwise the mode is on, answers nothing, and looks broken."""
    project_config(tmp_path, "approval_allow = []\n")
    app, _ = build(record(), tmp_path)
    client = await client_for(app)
    try:
        response = await client.put(
            "/api/sessions/sess-1/approvals", json={"mode": "allowlisted"}
        )
        body = await response.json()
    finally:
        await client.close()
    assert response.status == 409
    assert body["code"] == "empty_allowlist"


async def test_allow_all_can_be_disabled_install_wide(tmp_path: Path) -> None:
    app, _ = build(
        record(),
        tmp_path,
        config=Config(
            data_dir=tmp_path, approval_auto_enabled=True, approval_allow_all_permitted=False
        ),
    )
    client = await client_for(app)
    try:
        response = await client.put("/api/sessions/sess-1/approvals", json={"mode": "allow_all"})
        allowed = await client.put(
            "/api/sessions/sess-1/approvals", json={"mode": "allowlisted"}
        )
    finally:
        await client.close()
    assert response.status == 409
    assert allowed.status == 200


async def test_returning_to_wait_is_never_refused(tmp_path: Path) -> None:
    """Taking authority back must not depend on the install switch, the Project
    ceiling, or the conversation still being the one it was granted against."""
    session_record = record(agent_run_id="")
    session_record.approval_policy = ApprovalPolicy(mode="allow_all", run_id="gone")
    project_config(tmp_path, 'approval_ceiling = "wait"\n')
    app, _ = build(
        session_record,
        tmp_path,
        config=Config(data_dir=tmp_path, approval_auto_enabled=False),
    )
    client = await client_for(app)
    try:
        response = await client.put("/api/sessions/sess-1/approvals", json={"mode": "wait"})
        body = await response.json()
    finally:
        await client.close()
    assert response.status == 200
    assert body["policy"]["mode"] == "wait"


async def test_a_session_with_no_conversation_cannot_hold_a_grant(tmp_path: Path) -> None:
    app, _ = build(record(agent_run_id=""), tmp_path)
    client = await client_for(app)
    try:
        response = await client.put("/api/sessions/sess-1/approvals", json={"mode": "allow_all"})
        body = await response.json()
    finally:
        await client.close()
    assert response.status == 409
    assert body["code"] == "approvals_unavailable"


async def test_a_malformed_project_config_fails_closed(tmp_path: Path) -> None:
    """An unreadable ceiling is not evidence of permission."""
    (tmp_path / ".swe-mux").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".swe-mux" / "config.toml").write_text("not = [valid", encoding="utf-8")
    app, _ = build(record(), tmp_path)
    client = await client_for(app)
    try:
        body = await (await client.get("/api/sessions/sess-1/approvals")).json()
    finally:
        await client.close()
    assert body["ceiling"] == "wait"
    assert body["unavailable"]


# -- the one-shot ------------------------------------------------------------


async def test_approve_once_refuses_a_session_that_is_not_showing_an_approval(
    tmp_path: Path,
) -> None:
    app, _ = build(record(state="idle"), tmp_path)
    client = await client_for(app)
    try:
        response = await client.post("/api/sessions/sess-1/approvals/approve-once")
        body = await response.json()
    finally:
        await client.close()
    assert response.status == 409
    assert body["code"] == "no_approval"


def test_the_config_bounds_reject_an_unbounded_grant() -> None:
    from swe_mux.config import _validate

    with pytest.raises(ValueError) as caught:
        _validate(Config(approval_grant_ttl_minutes=0, approval_max_auto_per_grant=0))
    errors = caught.value.args[0]
    assert "approval_grant_ttl_minutes" in errors
    assert "approval_max_auto_per_grant" in errors


def test_the_config_defaults_are_off_and_bounded() -> None:
    config = Config()
    assert config.approval_auto_enabled is False
    assert 0 < config.approval_grant_ttl_minutes <= 480
    assert 0 < config.approval_hook_timeout_seconds <= 60


def test_json_shape_of_a_policy_is_stable() -> None:
    """The browser reads these keys by name; renaming one is a silent break."""
    snapshot = ApprovalPolicy().snapshot()
    assert set(snapshot) == {
        "mode",
        "run_id",
        "expires_at",
        "granted_at",
        "set_by",
        "rules",
        "auto_approved",
        "max_auto",
        "last_decision_at",
        "last_request",
        "floor_deferred",
    }
    json.dumps(snapshot)
