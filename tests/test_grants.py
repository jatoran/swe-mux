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

from swe_mux import app_keys as keys
from swe_mux.automation_registry import (
    AUTONOMY_PROJECT_AUTOMATIONS,
    LLM_PROJECT_AUTOMATIONS,
    RECOMMENDED_PROJECT_AUTOMATIONS,
    REGISTRY,
    enabling_closure,
    install_defaults,
    spends_money,
)
from swe_mux.config import Config
from swe_mux.event_bus import EventBus
from swe_mux.grants import (
    AUTONOMY_PROJECT_VALUES,
    GRANTABLE_INSTALL_KEYS,
    GRANTABLE_PROJECT_VALUES,
    LLM_PROJECT_VALUES,
    GrantRefusal,
    plan_grant,
    project_values_after,
)
from swe_mux.project_files import read_project_config, write_project_config
from swe_mux.routes.automation import get_project_automations
from swe_mux.routes.grants import apply_grants, describe_grants
from swe_mux.server import error_middleware

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
    app[keys.PROJECTS] = ProjectsStub(ProjectStub(root))
    app[keys.EVENTS] = bus
    app[keys.CONFIG] = config
    app[keys.AUTOMATION_GATE_CACHE] = cache
    app[keys.PROJECT_CONTEXTS] = None
    app.router.add_get("/api/grants", describe_grants)
    app.router.add_post("/api/grants", apply_grants)
    app.router.add_get("/api/projects/{project_id}/automations", get_project_automations)
    return app, config, cache, events


async def client_for(app: web.Application) -> TestClient:
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


def project_root(app: web.Application) -> str:
    return app[keys.PROJECTS].projects["proj-1"].root


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


def test_a_grant_for_something_the_install_already_defaults_on_writes_nothing() -> None:
    """A gate must not pin a Project to a default the operator can still change.

    Not only about saving a revision. Writing the id down turns an inherited
    "on" into this repository's own decision, so an operator who later withdrew
    the install default would find every Project a gate happened to touch still
    running it - the default silently stops meaning anything, one press at a
    time. `plan_grant` therefore treats inherited-on as already-on.
    """
    defaults = install_defaults({"doc_debt": True})
    plan = plan_grant(
        install=None,
        automations=["doc_debt"],
        values=None,
        current_install={},
        current_automations={},
        current_values={},
        project_defaults=defaults,
    )
    assert plan.empty
    # Without the install layer the same call is a real write, which is what an
    # install that has expressed no policy still gets.
    assert not plan_grant(
        install=None,
        automations=["doc_debt"],
        values=None,
        current_install={},
        current_automations={},
        current_values={},
    ).empty
    # An explicit opt-out is still the one state a grant overrides.
    explicit = plan_grant(
        install=None,
        automations=["doc_debt"],
        values=None,
        current_install={},
        current_automations={"doc_debt": False},
        current_values={"automations": {"doc_debt": False}},
        project_defaults=defaults,
    )
    assert explicit.automations == frozenset({"doc_debt"})


def test_an_explicit_opt_out_survives_a_grant_that_never_mentioned_it() -> None:
    """Every explicit false is kept now, not only a default-on one.

    Absence used to mean off for an ordinary opt-in, so dropping its `false` was
    lossless. Absence means *inherit* now, so a dropped false is a Project that
    comes on by itself the moment somebody defaults that id on.
    """
    current_automations = {"code_graph": False, "tier0": True, "raw_store": True}
    plan = plan_grant(
        install=None,
        automations=["doc_debt"],
        values=None,
        current_install={},
        current_automations=current_automations,
        current_values={"automations": dict(current_automations)},
    )
    merged = project_values_after(
        {"automations": dict(current_automations)}, plan, current_automations
    )
    assert merged["automations"]["code_graph"] is False


def test_a_globally_disallowed_automation_is_refused_not_granted_inert() -> None:
    """The install-wide ceiling refuses the grant instead of reporting success.

    Unlike an unverified provider - disclosed and granted anyway - a ceiling
    entry is the operator's standing "not anywhere", so a gate reporting
    success against it would offer to turn on nothing. Asked of the closure:
    `provenance_graph` itself is allowed, and dies with `tier0`.
    """
    with pytest.raises(GrantRefusal) as refusal:
        plan_grant(
            install=None,
            automations=["provenance_graph"],
            values=None,
            current_install={},
            current_automations={},
            current_values={},
            global_allow={"tier0": False},
        )
    assert refusal.value.code == "automation_globally_disabled"
    assert "tier0" in refusal.value.message


def test_a_grant_that_raises_the_blocking_switch_in_the_same_act_is_allowed() -> None:
    # The one-act scan-timeline gate turns `scan_timeline_enabled` on together
    # with the Project opt-in; the act itself lifts the ceiling, so refusing it
    # would make the gate impossible to satisfy.
    plan = plan_grant(
        install={"scan_timeline_enabled": True},
        automations=["scan_timeline"],
        values=None,
        current_install={"scan_timeline_enabled": False},
        current_automations={},
        current_values={},
        global_allow={"scan_timeline": False},
    )
    assert "scan_timeline" in plan.automations
    with pytest.raises(GrantRefusal) as refusal:
        plan_grant(
            install=None,
            automations=["scan_timeline"],
            values=None,
            current_install={"scan_timeline_enabled": False},
            current_automations={},
            current_values={},
            global_allow={"scan_timeline": False},
        )
    assert refusal.value.code == "automation_globally_disabled"


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


def test_a_default_on_opt_out_survives_an_unrelated_grant() -> None:
    """`session_control = false` is load-bearing (absence means on) and must not
    be dropped by a grant that never mentioned it - while a grant that *does*
    ask for it is the one way a gate may override the opt-out."""
    current_automations = {"session_control": False, "tier0": True, "raw_store": True}
    unrelated = plan_grant(
        install=None,
        automations=["doc_debt"],
        values=None,
        current_install={},
        current_automations=current_automations,
        current_values={"automations": dict(current_automations)},
    )
    merged = project_values_after(
        {"automations": dict(current_automations)}, unrelated, current_automations
    )
    assert merged["automations"]["session_control"] is False

    # Unset-and-default-on is already on: granting it plans no write, so a
    # double click cannot bump a revision under an open editor.
    redundant = plan_grant(
        install=None,
        automations=["session_control"],
        values=None,
        current_install={},
        current_automations={},
        current_values={},
    )
    assert redundant.empty

    # An explicit opt-out is the one state a session_control grant overrides.
    explicit = plan_grant(
        install=None,
        automations=["session_control"],
        values=None,
        current_install={},
        current_automations={"session_control": False},
        current_values={"automations": {"session_control": False}},
    )
    merged = project_values_after(
        {"automations": {"session_control": False}}, explicit, {"session_control": False}
    )
    assert merged["automations"]["session_control"] is True


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
    # Cleared at least once. The Project write clears it, and the install half's
    # `apply_runtime_config` clears it again for `scan_timeline_enabled` - the
    # switch now composes into the gate's install-wide ceiling, so a second
    # clear is the correct behaviour rather than a double count to pin away.
    assert cache.cleared >= 1
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
    # The create form renders its checkboxes from this payload rather than a browser
    # copy, so the served sets are the contract.
    sets = body["project_starting_sets"]
    assert sets["recommended"] == {
        "automations": list(RECOMMENDED_PROJECT_AUTOMATIONS),
        "values": {},
    }
    assert sets["llm"] == {
        "automations": list(LLM_PROJECT_AUTOMATIONS),
        "values": dict(LLM_PROJECT_VALUES),
    }
    assert sets["autonomy"] == {
        "automations": list(AUTONOMY_PROJECT_AUTOMATIONS),
        "values": dict(AUTONOMY_PROJECT_VALUES),
    }
    # `spends` rides the registry payload, because the toggle surface and every gate that
    # offers the same switch have to say the same thing about it.
    assert {item["id"] for item in body["automations"]} == set(REGISTRY)
    assert all("spends" in item for item in body["automations"])
    # Two readings of the install's defaults, and the create form needs both. An
    # id the operator explicitly defaulted *off* and an id the install has never
    # had an opinion about both resolve to `install_default: false`, and only the
    # second may be pre-ticked on a new Project - so the stored map ships too.
    assert body["project_defaults"] == {}
    assert all("install_default" in item for item in body["automations"])
    session_control = next(
        item for item in body["automations"] if item["id"] == "session_control"
    )
    assert session_control["install_default"] is True


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
    # `session_control` rides along as the default-on capability gate
    # (2026-08-25), not as part of the granted closure.
    assert set(body["project"]["enabled"]) == set(
        enabling_closure(RECOMMENDED_PROJECT_AUTOMATIONS)
    ) | {"session_control"}
    assert [name for name, _ in events].count("grant_applied") == 1


def test_the_llm_starting_set_is_the_model_tier_and_says_so() -> None:
    # Offered as a never-defaulted-on checkbox at creation, labelled "the model-backed
    # automations" - so membership is held to that sentence: everything in it needs a
    # model, the whole set discloses spend, and nothing in its closure is a switch that
    # reads as on and does nothing.
    for automation_id in LLM_PROJECT_AUTOMATIONS:
        assert REGISTRY[automation_id].needs_llm
    assert spends_money(LLM_PROJECT_AUTOMATIONS)
    for automation_id in enabling_closure(LLM_PROJECT_AUTOMATIONS):
        assert REGISTRY[automation_id].implemented
    # Its values half arms the timeline per run; both halves must be values the grant
    # path accepts, or the create form offers a set the daemon refuses.
    for key, value in LLM_PROJECT_VALUES.items():
        assert value in GRANTABLE_PROJECT_VALUES[key]


def test_the_autonomy_starting_set_grants_spawn_and_land_and_nothing_livelier() -> None:
    assert not spends_money(AUTONOMY_PROJECT_AUTOMATIONS)
    # Deliberately included: whatever still arrives as a draft under this posture gets
    # its review surface instead of silence.
    assert "observation_inbox" in AUTONOMY_PROJECT_AUTOMATIONS
    for key, value in AUTONOMY_PROJECT_VALUES.items():
        assert value in GRANTABLE_PROJECT_VALUES[key]
    # Acting on a *live* session is a different risk class: interrupt/end and mid-turn
    # interjection stay at their inert defaults, raisable only as individual acts.
    assert "session_control_grant" not in AUTONOMY_PROJECT_VALUES
    assert "interject_grant" not in AUTONOMY_PROJECT_VALUES


def test_both_optional_starting_sets_plan_without_refusal() -> None:
    # The create form submits set contents it read off `GET /api/grants`; a set the
    # planner refuses would fail at the click, which is the drift this pins down.
    for automations, values in (
        (LLM_PROJECT_AUTOMATIONS, LLM_PROJECT_VALUES),
        (AUTONOMY_PROJECT_AUTOMATIONS, AUTONOMY_PROJECT_VALUES),
    ):
        plan = plan_grant(
            install=None,
            automations=list(automations),
            values=dict(values),
            current_install={},
            current_automations={},
            current_values={},
        )
        assert not plan.empty


async def test_the_autonomy_set_applies_opt_ins_and_authority_as_one_grant(
    tmp_path: Path,
) -> None:
    # The create form's checkbox is one POST carrying both halves, so a failure leaves
    # nothing applied and the audit trail holds exactly one act.
    app, _config, _cache, events = build(tmp_path)
    client = await client_for(app)
    try:
        response = await client.post("/api/grants", json={
            "project_id": "proj-1",
            "automations": list(AUTONOMY_PROJECT_AUTOMATIONS),
            "values": dict(AUTONOMY_PROJECT_VALUES),
        })
        assert response.status == 200
        body = await response.json()
    finally:
        await client.close()
    assert body["spends"] is False
    assert set(body["project"]["enabled"]) == set(
        enabling_closure(AUTONOMY_PROJECT_AUTOMATIONS)
    )
    written = await read_project_config(project_root(app))
    assert written["values"]["spawn_grant"] == "granted"
    assert written["values"]["land_grant"] == "granted"
    assert "session_control_grant" not in written["values"]
    assert [name for name, _ in events].count("grant_applied") == 1
