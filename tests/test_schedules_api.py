"""HTTP surface for scheduled runs."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux.config import Config
from swe_mux.schedule_store import ScheduleStore
from swe_mux.scheduler import ScheduleService
from swe_mux.server import (
    create_project_schedule,
    delete_schedule,
    error_middleware,
    list_project_schedules,
    list_schedule_runs,
    list_schedules,
    patch_schedule,
    preview_schedule,
    run_schedule_now,
)


class EventsStub:
    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict[str, Any]]] = []

    async def emit(self, event_type: str, **payload: Any) -> None:
        self.emitted.append((event_type, payload))


def build_app(tmp_path: Path, *, enabled: frozenset[str]) -> tuple[web.Application, list[Any]]:
    store = ScheduleStore(tmp_path / "mux.db")
    spawned: list[dict[str, Any]] = []
    sessions = SimpleNamespace(sessions={})

    async def spawn(body: dict[str, Any]) -> Any:
        spawned.append(body)
        record = SimpleNamespace(id=f"pty-{len(spawned)}", state="running")
        session = SimpleNamespace(record=record)
        sessions.sessions[record.id] = session
        return session

    async def enqueue(**_kwargs: Any) -> dict[str, Any]:
        return {"id": "m1"}

    async def gate(_root: str) -> frozenset[str]:
        return enabled

    projects = SimpleNamespace(
        projects={"p1": SimpleNamespace(id="p1", name="Main", root=str(tmp_path))}
    )
    config = Config(data_dir=tmp_path)
    app = web.Application(middlewares=[error_middleware])
    app["schedule_store"] = store
    app["projects"] = projects
    app["config"] = config
    app["events"] = EventsStub()
    app["automation_gate"] = gate
    app["schedules"] = ScheduleService(
        store=store,
        projects=projects,
        sessions=sessions,
        config=config,
        events=app["events"],
        automation_gate=gate,
        spawn_op=spawn,
        enqueue=enqueue,
    )
    app.router.add_get("/api/schedules", list_schedules)
    app.router.add_post("/api/schedules/preview", preview_schedule)
    app.router.add_get("/api/projects/{project_id}/schedules", list_project_schedules)
    app.router.add_post("/api/projects/{project_id}/schedules", create_project_schedule)
    app.router.add_patch("/api/schedules/{schedule_id}", patch_schedule)
    app.router.add_delete("/api/schedules/{schedule_id}", delete_schedule)
    app.router.add_post("/api/schedules/{schedule_id}/run", run_schedule_now)
    app.router.add_get("/api/schedules/{schedule_id}/runs", list_schedule_runs)
    return app, spawned


def definition(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "label": "Nightly health check",
        "prompt": "Check the deployment and report.",
        "trigger_kind": "cron",
        "cron": "0 3 * * *",
        "backend": "claude",
    }
    body.update(overrides)
    return body


@pytest.fixture
def app_and_spawns(tmp_path: Path):  # type: ignore[no-untyped-def]
    app, spawned = build_app(tmp_path, enabled=frozenset({"scheduled_runs"}))
    yield app, spawned
    app["schedule_store"].close()


async def test_create_list_and_delete(app_and_spawns) -> None:  # type: ignore[no-untyped-def]
    app, _ = app_and_spawns
    async with TestClient(TestServer(app)) as client:
        created = await client.post("/api/projects/p1/schedules", json=definition())
        assert created.status == 201
        row = await created.json()
        assert row["label"] == "Nightly health check"
        assert row["blocked"] == ""
        assert row["next_fire_at"] > time.time()

        listed = await (await client.get("/api/projects/p1/schedules")).json()
        assert [item["id"] for item in listed["schedules"]] == [row["id"]]
        # The unscoped listing is what the tab's fleet toggle reads.
        fleet = await (await client.get("/api/schedules")).json()
        assert fleet["schedules"][0]["project_name"] == "Main"
        assert fleet["status"]["armed"] == 1

        removed = await client.delete(f"/api/schedules/{row['id']}")
        assert removed.status == 200
        assert not (await (await client.get("/api/schedules")).json())["schedules"]


async def test_validation_errors_name_the_field(app_and_spawns) -> None:  # type: ignore[no-untyped-def]
    app, _ = app_and_spawns
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/api/projects/p1/schedules", json=definition(cron="0 3 * *")
        )
        assert response.status == 400
        payload = await response.json()
        assert payload["code"] == "invalid_cron"
        assert payload["fields"] == {"cron": "five fields required"}


async def test_patch_replaces_and_guards_the_revision(app_and_spawns) -> None:  # type: ignore[no-untyped-def]
    app, _ = app_and_spawns
    async with TestClient(TestServer(app)) as client:
        row = await (await client.post("/api/projects/p1/schedules", json=definition())).json()
        stale = await client.patch(
            f"/api/schedules/{row['id']}",
            json={**definition(label="Renamed"), "revision": row["revision"] + 3},
        )
        assert stale.status == 409
        assert (await stale.json())["code"] == "revision_conflict"

        updated = await client.patch(
            f"/api/schedules/{row['id']}",
            json={**definition(label="Renamed"), "revision": row["revision"]},
        )
        assert (await updated.json())["label"] == "Renamed"


async def test_patch_enabled_alone_is_the_pause_switch(app_and_spawns) -> None:  # type: ignore[no-untyped-def]
    app, _ = app_and_spawns
    async with TestClient(TestServer(app)) as client:
        row = await (await client.post("/api/projects/p1/schedules", json=definition())).json()
        paused = await (
            await client.patch(f"/api/schedules/{row['id']}", json={"enabled": False})
        ).json()
        assert paused["enabled"] is False
        assert paused["next_fire_at"] is None
        assert paused["label"] == row["label"]
        armed = await (
            await client.patch(f"/api/schedules/{row['id']}", json={"enabled": True})
        ).json()
        assert armed["enabled"] is True
        assert armed["next_fire_at"] > time.time()


async def test_run_now_spawns_and_records_the_run(app_and_spawns) -> None:  # type: ignore[no-untyped-def]
    app, spawned = app_and_spawns
    async with TestClient(TestServer(app)) as client:
        row = await (await client.post("/api/projects/p1/schedules", json=definition())).json()
        result = await (await client.post(f"/api/schedules/{row['id']}/run")).json()
        assert result["run"]["outcome"] == "spawned"
        assert result["run"]["origin"] == "manual"
        assert spawned[0]["seed_text"] == "Check the deployment and report."
        runs = await (await client.get(f"/api/schedules/{row['id']}/runs")).json()
        assert [item["outcome"] for item in runs["runs"]] == ["spawned"]


async def test_a_project_without_the_opt_in_reads_blocked(tmp_path: Path) -> None:
    """Writing a schedule is allowed; firing it is what permission gates."""
    app, spawned = build_app(tmp_path, enabled=frozenset())
    try:
        async with TestClient(TestServer(app)) as client:
            row = await (
                await client.post("/api/projects/p1/schedules", json=definition())
            ).json()
            assert row["blocked"] == "automation_disabled"
            result = await (await client.post(f"/api/schedules/{row['id']}/run")).json()
            assert result["run"]["outcome"] == "skipped"
            assert not spawned
    finally:
        app["schedule_store"].close()


async def test_preview_answers_the_next_fire_times(app_and_spawns) -> None:  # type: ignore[no-untyped-def]
    app, _ = app_and_spawns
    async with TestClient(TestServer(app)) as client:
        payload = await (
            await client.post(
                "/api/schedules/preview",
                json=definition(cron="0 3 * * *", timezone="UTC"),
            )
        ).json()
        assert len(payload["next_fires"]) == 5
        # Daily, so consecutive fires are a day apart.
        assert payload["next_fires"][1] - payload["next_fires"][0] == 86_400
        assert payload["next_fires"][0] > payload["now"]


async def test_unknown_schedule_is_a_404(app_and_spawns) -> None:  # type: ignore[no-untyped-def]
    app, _ = app_and_spawns
    async with TestClient(TestServer(app)) as client:
        assert (await client.post("/api/schedules/sch_nope/run")).status == 404
        assert (await client.delete("/api/schedules/sch_nope")).status == 404
        assert (await client.get("/api/schedules/sch_nope/runs")).status == 404
