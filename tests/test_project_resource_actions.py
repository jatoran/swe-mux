from __future__ import annotations

from types import SimpleNamespace

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux.config import load_config
from swe_mux.event_bus import EventBus
from swe_mux.project_files import read_project_config
from swe_mux.server import (
    error_middleware,
    ignore_project_resource,
    reveal_project_resource,
)


async def test_project_resource_context_actions_are_scoped_and_persisted(
    tmp_path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    root = tmp_path / "project"
    (root / "vendor").mkdir(parents=True)
    (root / "src").mkdir()
    target_file = root / "src" / "cache.bin"
    target_file.write_bytes(b"cache")
    revealed: list[str] = []
    monkeypatch.setattr(
        "swe_mux.server.open_in_file_manager", lambda path: revealed.append(str(path))
    )

    config = load_config(tmp_path / "config.toml")
    project = SimpleNamespace(id="project-one", root=str(root), name="Project One")
    app = web.Application(middlewares=[error_middleware])
    app["config"] = config
    app["projects"] = SimpleNamespace(projects={project.id: project})
    app["events"] = EventBus()
    app.router.add_post("/projects/{project_id}/reveal", reveal_project_resource)
    app.router.add_post("/projects/{project_id}/ignore", ignore_project_resource)

    async with TestClient(TestServer(app)) as client:
        reveal = await client.post("/projects/project-one/reveal", json={"path": "src/cache.bin"})
        global_ignore = await client.post(
            "/projects/project-one/ignore", json={"path": "vendor", "scope": "global"}
        )
        project_ignore = await client.post(
            "/projects/project-one/ignore",
            json={"path": "src/cache.bin", "scope": "project"},
        )
        traversal = await client.post("/projects/project-one/reveal", json={"path": "../outside"})
        global_payload = await global_ignore.json()
        project_payload = await project_ignore.json()

    assert reveal.status == 200
    assert revealed == [str(target_file.resolve())]
    assert global_payload["pattern"] == "vendor"
    assert "vendor" in config.project_ignore_patterns
    assert project_payload["pattern"] == "src/cache.bin"
    project_config = await read_project_config(root)
    assert project_config["values"]["ignore_patterns"] == ["src/cache.bin"]
    assert traversal.status == 400
