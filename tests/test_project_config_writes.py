"""The two routes that edit one Project's `.swe-mux/config.toml`, and their guards.

Everything here is about one defect: the file has six independent writers that own
disjoint keys - the Projects editor's three sections, a grant gate, the land queue's
verify command, the configurator, the file browser's "ignore this" - and a whole-file
revision guard reports every one of them as a collision with every other. The
operator saw it as "project config changed externally; reload before saving" the
second time they touched anything in the Projects panel, with no way forward but
closing the panel and opening it again.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from swe_mux import app_keys as keys
from swe_mux.config import Config
from swe_mux.project_files import (
    ProjectConfigConflict,
    read_project_config,
    write_project_config,
)
from swe_mux.routes.automation import put_project_automations
from swe_mux.routes.projects import put_project_config


async def _resolved(value: dict[str, Any]) -> dict[str, Any]:
    return value


class _Events:
    def __init__(self) -> None:
        self.kinds: list[str] = []

    async def emit(self, kind: str, **_payload: object) -> None:
        self.kinds.append(kind)


class _History:
    async def register_project_scope(self, _project: object) -> None:
        return None


def _app(project: SimpleNamespace) -> dict[object, object]:
    return {
        keys.PROJECTS: SimpleNamespace(projects={project.id: project}),
        keys.EVENTS: _Events(),
        keys.HISTORY: _History(),
        # The enablement read resolves against the install-wide ceiling now.
        keys.CONFIG: Config(),
    }


def _request(app: dict[object, object], body: dict[str, Any]) -> object:
    return SimpleNamespace(
        app=app,
        query={},
        match_info={"project_id": "p1"},
        json=lambda: _resolved(body),
    )


@pytest.mark.asyncio
async def test_two_sections_of_one_panel_no_longer_collide(tmp_path: Path) -> None:
    project = SimpleNamespace(id="p1", name="Main", root=str(tmp_path))
    app = _app(project)
    await write_project_config(
        tmp_path, {"automations": {"raw_store": True}, "session_control_grant": "draft"}, "missing"
    )
    # Both sections read the file at this moment; each is about to write a field the
    # other never mentions.
    shared = await read_project_config(tmp_path)

    optins = await put_project_automations(  # type: ignore[arg-type]
        _request(
            app,
            {
                "automations": {"raw_store": True, "tier0": True},
                "base": {
                    "automations": shared["values"].get("automations"),
                    "scan_timeline_auto_enable": shared["values"].get(
                        "scan_timeline_auto_enable"
                    ),
                },
            },
        )
    )
    # `session_control` is the default-on capability gate (2026-08-25).
    assert set(json.loads(optins.body)["enabled"]) == {
        "raw_store", "tier0", "session_control"
    }

    # The authority row's write was composed against the file as it stood *before* the
    # opt-in above. Under the old whole-file guard this is where the operator was told
    # to reload; it now succeeds, because nothing it named has moved.
    authority = await put_project_config(  # type: ignore[arg-type]
        _request(
            app,
            {
                "cwd": str(tmp_path),
                "project_id": "p1",
                "changes": {"session_control_grant": "granted"},
                "base": {"session_control_grant": shared["values"].get("session_control_grant")},
            },
        )
    )
    written = json.loads(authority.body)
    assert written["values"]["session_control_grant"] == "granted"
    assert written["values"]["automations"] == {"raw_store": True, "tier0": True}


@pytest.mark.asyncio
async def test_the_matrix_writes_authority_and_clearing_one_restores_inheritance(
    tmp_path: Path,
) -> None:
    """"Follow global" removes the key; it does not write the global's value.

    The distinction is the whole reason the Project cell has three positions.
    Writing the current global as an explicit value would pin the Project to
    today's answer, so a later change to the install default would skip exactly
    the Projects whose operator thought they were inheriting.
    """
    project = SimpleNamespace(id="p1", name="Main", root=str(tmp_path))
    app = _app(project)
    app[keys.CONFIG] = Config(agent_authority_default={"land_grant": "granted"})
    await write_project_config(tmp_path, {"automations": {"raw_store": True}}, "missing")

    lowered = await put_project_automations(  # type: ignore[arg-type]
        _request(
            app,
            {
                "automations": {"raw_store": True},
                "authority": {"land_grant": "draft"},
                "revision": (await read_project_config(tmp_path))["revision"],
            },
        )
    )
    payload = json.loads(lowered.body)
    assert payload["authority"]["land_grant"] == "draft"
    assert payload["authority_effective"]["land_grant"] == "draft"

    restored = await put_project_automations(  # type: ignore[arg-type]
        _request(
            app,
            {
                "automations": {"raw_store": True},
                "authority": {"land_grant": None},
                "revision": (await read_project_config(tmp_path))["revision"],
            },
        )
    )
    payload = json.loads(restored.body)
    # Unset on disk, and therefore reached by the install default.
    assert payload["authority"]["land_grant"] is None
    assert payload["authority_effective"]["land_grant"] == "granted"
    assert "land_grant" not in (await read_project_config(tmp_path))["values"]


@pytest.mark.asyncio
async def test_the_matrix_refuses_an_unknown_authority_field_or_level(tmp_path: Path) -> None:
    project = SimpleNamespace(id="p1", name="Main", root=str(tmp_path))
    app = _app(project)
    await write_project_config(tmp_path, {"automations": {}}, "missing")
    revision = (await read_project_config(tmp_path))["revision"]
    with pytest.raises(ValueError, match="unknown authority fields"):
        await put_project_automations(  # type: ignore[arg-type]
            _request(
                app,
                {"automations": {}, "authority": {"land_grants": "draft"}, "revision": revision},
            )
        )
    with pytest.raises(ValueError, match="invalid authority levels"):
        await put_project_automations(  # type: ignore[arg-type]
            _request(
                app,
                {"automations": {}, "authority": {"land_grant": "maybe"}, "revision": revision},
            )
        )


@pytest.mark.asyncio
async def test_a_field_that_really_moved_is_still_refused_by_name(tmp_path: Path) -> None:
    project = SimpleNamespace(id="p1", name="Main", root=str(tmp_path))
    app = _app(project)
    await write_project_config(tmp_path, {"approval_ceiling": "wait"}, "missing")

    body = {
        "cwd": str(tmp_path),
        "project_id": "p1",
        "changes": {"approval_ceiling": "allowlisted"},
        "base": {"approval_ceiling": "wait"},
    }
    await put_project_config(_request(app, body))  # type: ignore[arg-type]

    # The same edit again, still believing "wait". `ProjectConfigConflict` reaches the
    # error middleware, which is where the 409 and its payload are shaped; the route
    # deliberately does not catch it.
    with pytest.raises(ProjectConfigConflict) as conflict:
        await put_project_config(_request(app, body))  # type: ignore[arg-type]
    assert conflict.value.fields == ["approval_ceiling"]
    assert conflict.value.current["values"]["approval_ceiling"] == "allowlisted"


@pytest.mark.asyncio
async def test_a_whole_document_write_still_takes_the_older_revision_guard(
    tmp_path: Path,
) -> None:
    # The `values` shape is what a client that predates field-scoped writes sends, and
    # what every caller that reads and writes in one breath still sends. Dropping its
    # guard silently would be worse than the false conflicts it causes.
    project = SimpleNamespace(id="p1", name="Main", root=str(tmp_path))
    app = _app(project)
    saved = await write_project_config(tmp_path, {"approval_ceiling": "wait"}, "missing")

    stale = _request(
        app,
        {
            "cwd": str(tmp_path),
            "project_id": "p1",
            "values": {"approval_ceiling": "allow_all"},
            "revision": "missing",
        },
    )
    refusal = await put_project_config(stale)  # type: ignore[arg-type]
    assert refusal.status == 409
    assert json.loads(refusal.body)["code"] == "revision_conflict"

    current = _request(
        app,
        {
            "cwd": str(tmp_path),
            "project_id": "p1",
            "values": {"approval_ceiling": "allow_all"},
            "revision": saved["revision"],
        },
    )
    accepted = await put_project_config(current)  # type: ignore[arg-type]
    assert json.loads(accepted.body)["values"]["approval_ceiling"] == "allow_all"


@pytest.mark.asyncio
async def test_changes_without_a_base_are_refused_rather_than_unguarded(
    tmp_path: Path,
) -> None:
    # Defaulting a missing base to "no base" would turn the guard off for whoever
    # forgot it, which is the failure mode a guard exists to prevent.
    project = SimpleNamespace(id="p1", name="Main", root=str(tmp_path))
    app = _app(project)
    with pytest.raises(ValueError, match="base must accompany changes"):
        await put_project_config(  # type: ignore[arg-type]
            _request(
                app,
                {
                    "cwd": str(tmp_path),
                    "project_id": "p1",
                    "changes": {"approval_ceiling": "wait"},
                },
            )
        )


@pytest.mark.asyncio
async def test_opting_out_of_the_scan_timeline_clears_its_arming_rule(
    tmp_path: Path,
) -> None:
    # The rule the field-scoped path has to keep: auto-enable is meaningless without
    # the permission it rides on, and leaving it set would silently re-arm every run
    # the moment the Project is opted in again.
    project = SimpleNamespace(id="p1", name="Main", root=str(tmp_path))
    app = _app(project)
    await put_project_automations(  # type: ignore[arg-type]
        _request(
            app,
            {
                "automations": {"raw_store": True, "tier0": True, "scan_timeline": True},
                "scan_timeline_auto_enable": True,
                "base": {"automations": None, "scan_timeline_auto_enable": None},
            },
        )
    )
    armed = await read_project_config(tmp_path)
    assert armed["values"]["scan_timeline_auto_enable"] is True

    await put_project_automations(  # type: ignore[arg-type]
        _request(
            app,
            {
                "automations": {"raw_store": True, "tier0": True},
                "base": {
                    "automations": armed["values"].get("automations"),
                    "scan_timeline_auto_enable": armed["values"].get("scan_timeline_auto_enable"),
                },
            },
        )
    )
    disarmed = await read_project_config(tmp_path)
    assert "scan_timeline_auto_enable" not in disarmed["values"]
