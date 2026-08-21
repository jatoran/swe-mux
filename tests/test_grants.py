"""The grant endpoint: what a gate may switch on, and everything it may not.

`POST /api/grants` is a write reachable from any drawer pane, so the interesting tests
are the refusals. Three properties carry the whole safety argument and each is asserted
directly here: a grant is additive, it is allowlisted, and it either lands whole or does
not land at all.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux.automation_registry import (
    RECOMMENDED_PROJECT_AUTOMATIONS,
    REGISTRY,
    enabling_closure,
    spends_money,
)
from swe_mux.config import Config
from swe_mux.event_bus import EventBus
from swe_mux.grants import (
    GRANTABLE_INSTALL_KEYS,
    GRANTABLE_PROJECT_VALUES,
    GrantRefusal,
    plan_grant,
    project_values_after,
)
from swe_mux.project_files import read_project_config, write_project_config
from swe_mux.server import (
    apply_grants,
    describe_grants,
    error_middleware,
    get_project_automations,
)

pytestmark = pytest.mark.anyio


class ProjectStub:
    def __init__(self, root: Path) -> None:
        self.id = "proj-1"
        self.name = "repo"
        self.root = str(root)


class ProjectsStub:
    def __init__(self, project: ProjectStub) -> None:
        self.projects = {project.id: project}

    def ordered_projects(self) -> list[ProjectStub]:
        return list(self.projects.values())


class GateCacheStub:
    def __init__(self) -> None:
        self.cleared = 0

    def clear(self) -> None:
        self.cleared += 1


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def build(tmp_path: Path) -> tuple[web.Application, Config, GateCacheStub, list[Any]]:
    root = tmp_path / "repo"
    root.mkdir()
    config = Config(data_dir=tmp_path / "data")
    events: list[Any] = []
    bus = EventBus()

    async def record(name: str, **payload: Any) -> None:
        events.append((name, payload))

    bus.emit = record  # type: ignore[method-assign]
    cache = GateCacheStub()
    app = web.Application(middlewares=[error_middleware])
    app["projects"] = ProjectsStub(ProjectStub(root))
    app["events"] = bus
    app["config"] = config
    app["automation_gate_cache"] = cache
    app["project_contexts"] = None
    app.router.add_get("/api/grants", describe_grants)
    app.router.add_post("/api/grants", apply_grants)
    app.router.add_get("/api/projects/{project_id}/automations", get_project_automations)
    return app, config, cache, events


async def client_for(app: web.Application) -> TestClient:
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


def project_root(app: web.Application) -> str:
    return app["projects"].projects["proj-1"].root


# -- the plan ----------------------------------------------------------------


def test_a_grant_is_additive_and_refuses_to_withdraw() -> None:
    with pytest.raises(GrantRefusal) as refusal:
        plan_grant(
            install={"tts_enabled": False},
            automations=None,
            values=None,
            current_install={"tts_enabled": True},
            current_automations={},
            current_values={},
        )
    assert refusal.value.code == "grant_is_additive"


def test_an_authority_field_may_be_raised_and_not_lowered() -> None:
    plan = plan_grant(
        install=None,
        automations=None,
        values={"land_grant": "granted"},
        current_install={},
        current_automations={},
        current_values={"land_grant": "draft"},
    )
    assert plan.values == {"land_grant": "granted"}
    with pytest.raises(GrantRefusal) as refusal:
        plan_grant(
            install=None,
            automations=None,
            values={"land_grant": "draft"},
            current_install={},
            current_automations={},
            current_values={"land_grant": "granted"},
        )
    assert refusal.value.code == "grant_is_additive"


def test_a_switch_outside_the_allowlist_is_refused() -> None:
    for install, values, code in [
        ({"claude_max_columns": True}, None, "not_grantable"),
        (None, {"approval_ceiling": "allow_all"}, "not_grantable"),
    ]:
        with pytest.raises(GrantRefusal) as refusal:
            plan_grant(
                install=install,
                automations=None,
                values=values,
                current_install={},
                current_automations={},
                current_values={},
            )
        assert refusal.value.code == code


def test_a_grant_writes_the_whole_dependency_closure() -> None:
    plan = plan_grant(
        install=None,
        automations=["code_graph"],
        values=None,
        current_install={},
        current_automations={},
        current_values={},
    )
    assert plan.automations == frozenset({"code_graph", "tier0", "raw_store"})
    assert plan.spends is False


def test_a_grant_reports_spending_from_the_closure_not_the_named_id() -> None:
    # Catch-me-up costs nothing and cannot be switched on without the timeline, which
    # does. A gate that asked only about the named id would call this one free.
    assert REGISTRY["catch_me_up"].spends is False
    plan = plan_grant(
        install=None,
        automations=["catch_me_up"],
        values=None,
        current_install={},
        current_automations={},
        current_values={},
    )
    assert plan.spends is True


def test_a_grant_for_something_already_on_plans_nothing() -> None:
    # A second click on a gate that has not re-rendered yet must not bump a revision and
    # race an editor someone has open.
    plan = plan_grant(
        install={"tts_enabled": True},
        automations=["raw_store"],
        values=None,
        current_install={"tts_enabled": True},
        current_automations={"raw_store": True},
        current_values={},
    )
    assert plan.empty


def test_an_unimplemented_automation_is_refused() -> None:
    with pytest.raises(GrantRefusal) as refusal:
        plan_grant(
            install=None,
            automations=["cross_session_interlocks"],
            values=None,
            current_install={},
            current_automations={},
            current_values={},
        )
    assert refusal.value.code == "automation_not_implemented"


def test_an_unknown_automation_is_refused() -> None:
    with pytest.raises(GrantRefusal) as refusal:
        plan_grant(
            install=None,
            automations=["not_a_thing"],
            values=None,
            current_install={},
            current_automations={},
            current_values={},
        )
    assert refusal.value.code == "unknown_automation"


def test_the_merged_table_keeps_what_was_already_opted_in() -> None:
    plan = plan_grant(
        install=None,
        automations=["code_graph"],
        values={"land_grant": "granted"},
        current_install={},
        current_automations={"doc_debt": True, "tier0": True, "raw_store": True},
        current_values={"automations": {"doc_debt": True, "tier0": True, "raw_store": True}},
    )
    merged = project_values_after(
        {"automations": {"doc_debt": True, "tier0": True, "raw_store": True}},
        plan,
        {"doc_debt": True, "tier0": True, "raw_store": True},
    )
    assert merged["automations"] == {
        "doc_debt": True, "tier0": True, "raw_store": True, "code_graph": True
    }
    assert merged["land_grant"] == "granted"


# -- the endpoint ------------------------------------------------------------


async def test_a_mixed_scope_grant_lands_in_one_request(tmp_path: Path) -> None:
    app, config, cache, events = build(tmp_path)
    client = await client_for(app)
    try:
        assert config.scan_timeline_enabled is False
        response = await client.post("/api/grants", json={
            "project_id": "proj-1",
            "install": {"scan_timeline_enabled": True},
            "automations": ["scan_timeline"],
        })
        assert response.status == 200
        body = await response.json()
    finally:
        await client.close()
    assert body["applied"]["install"] == ["scan_timeline_enabled"]
    assert set(body["applied"]["automations"]) == {"scan_timeline", "tier0", "raw_store"}
    assert body["spends"] is True
    assert config.scan_timeline_enabled is True
    written = await read_project_config(project_root(app))
    assert written["values"]["automations"]["scan_timeline"] is True
    assert cache.cleared == 1
    # One audit record for the whole act, the way an approved verification command leaves
    # exactly one `land_verify_approved`.
    names = [name for name, _ in events]
    assert names.count("grant_applied") == 1
    payload = next(payload for name, payload in events if name == "grant_applied")
    assert payload["keys"] == [
        "automation:raw_store", "automation:scan_timeline", "automation:tier0",
        "install:scan_timeline_enabled",
    ]
    assert payload["source"] == "user"


async def test_a_refused_grant_writes_nothing(tmp_path: Path) -> None:
    app, config, _cache, events = build(tmp_path)
    client = await client_for(app)
    try:
        response = await client.post("/api/grants", json={
            "project_id": "proj-1",
            # The install half is legal; the Project half is not. Nothing may land.
            "install": {"tts_enabled": True},
            "values": {"approval_ceiling": "allow_all"},
        })
        assert response.status == 409
        assert (await response.json())["code"] == "not_grantable"
    finally:
        await client.close()
    assert config.tts_enabled is False
    assert not [name for name, _ in events if name == "grant_applied"]


async def test_a_stale_revision_is_a_conflict_and_not_an_overwrite(tmp_path: Path) -> None:
    app, _config, _cache, _events = build(tmp_path)
    root = project_root(app)
    current = await read_project_config(root)
    await write_project_config(root, {"automations": {"doc_debt": True}}, current["revision"])
    client = await client_for(app)
    try:
        response = await client.post("/api/grants", json={
            "project_id": "proj-1",
            "automations": ["code_graph"],
            "revision": current["revision"],
        })
        assert response.status == 409
        assert (await response.json())["code"] == "revision_conflict"
    finally:
        await client.close()
    after = await read_project_config(root)
    assert after["values"]["automations"] == {"doc_debt": True}


async def test_a_project_grant_without_a_project_is_refused(tmp_path: Path) -> None:
    app, _config, _cache, _events = build(tmp_path)
    client = await client_for(app)
    try:
        response = await client.post("/api/grants", json={"automations": ["code_graph"]})
        assert response.status == 400
    finally:
        await client.close()


async def test_an_install_only_grant_needs_no_project(tmp_path: Path) -> None:
    app, config, _cache, _events = build(tmp_path)
    client = await client_for(app)
    try:
        response = await client.post(
            "/api/grants", json={"install": {"clipboard_history_enabled": True}}
        )
        assert response.status == 200
        assert "project" not in await response.json()
    finally:
        await client.close()
    assert config.clipboard_history_enabled is True


async def test_the_catalogue_describes_exactly_what_will_be_accepted(tmp_path: Path) -> None:
    app, _config, _cache, _events = build(tmp_path)
    client = await client_for(app)
    try:
        body = await (await client.get("/api/grants")).json()
    finally:
        await client.close()
    assert set(body["install"]) == GRANTABLE_INSTALL_KEYS
    assert set(body["values"]) == set(GRANTABLE_PROJECT_VALUES)
    assert body["recommended_project_automations"] == list(RECOMMENDED_PROJECT_AUTOMATIONS)
    # `spends` rides the registry payload, because the toggle surface and every gate that
    # offers the same switch have to say the same thing about it.
    assert {item["id"] for item in body["automations"]} == set(REGISTRY)
    assert all("spends" in item for item in body["automations"])


# -- the starting set --------------------------------------------------------


def test_the_recommended_starting_set_is_free_and_real() -> None:
    # Defaulted on at Project creation, so "free" has to be enforced rather than
    # believed. `automation_registry._validate_recommended` asserts this at import too;
    # this is the same claim where someone editing the tuple will see it fail.
    assert not spends_money(RECOMMENDED_PROJECT_AUTOMATIONS)
    for automation_id in RECOMMENDED_PROJECT_AUTOMATIONS:
        assert REGISTRY[automation_id].implemented
    # It is a set someone opts into explicitly, never an inherited default: nothing may
    # run on a Project whose own file did not say so.
    closure = enabling_closure(RECOMMENDED_PROJECT_AUTOMATIONS)
    assert "scan_timeline" not in closure


async def test_the_starting_set_applies_through_the_ordinary_grant_path(tmp_path: Path) -> None:
    app, _config, _cache, events = build(tmp_path)
    client = await client_for(app)
    try:
        response = await client.post("/api/grants", json={
            "project_id": "proj-1",
            "automations": list(RECOMMENDED_PROJECT_AUTOMATIONS),
        })
        assert response.status == 200
        body = await response.json()
    finally:
        await client.close()
    assert body["spends"] is False
    assert set(body["project"]["enabled"]) == set(
        enabling_closure(RECOMMENDED_PROJECT_AUTOMATIONS)
    )
    assert [name for name, _ in events].count("grant_applied") == 1
