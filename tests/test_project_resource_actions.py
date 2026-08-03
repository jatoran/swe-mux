from __future__ import annotations

import io
from types import SimpleNamespace

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

from swe_mux.config import load_config
from swe_mux.event_bus import EventBus
from swe_mux.project_files import read_project_config
from swe_mux.server import (
    error_middleware,
    get_project_file,
    get_project_file_content,
    ignore_project_resource,
    post_project_resource,
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


async def test_project_image_content_route_is_pinned_and_inert(tmp_path) -> None:  # type: ignore[no-untyped-def]
    root = tmp_path / "project"
    root.mkdir()
    buffer = io.BytesIO()
    Image.new("RGB", (4, 3), "navy").save(buffer, format="PNG")
    image_bytes = buffer.getvalue()
    (root / "diagram.png").write_bytes(image_bytes)
    project = SimpleNamespace(id="project-one", root=str(root), name="Project One")
    app = web.Application(middlewares=[error_middleware])
    app["projects"] = SimpleNamespace(projects={project.id: project})
    app.router.add_get("/projects/{project_id}/file", get_project_file)
    app.router.add_get("/projects/{project_id}/file/content", get_project_file_content)

    async with TestClient(TestServer(app)) as client:
        metadata = await client.get(
            "/projects/project-one/file", params={"path": "diagram.png"}
        )
        payload = await metadata.json()
        content = await client.get(
            "/projects/project-one/file/content",
            params={"path": "diagram.png", "revision": payload["revision"]},
        )
        stale = await client.get(
            "/projects/project-one/file/content",
            params={"path": "diagram.png", "revision": "old"},
        )

        assert metadata.status == 200
        assert content.status == 200
        assert await content.read() == image_bytes
        assert content.headers["Content-Type"] == "image/png"
        assert content.headers["X-Content-Type-Options"] == "nosniff"
        assert content.headers["Content-Security-Policy"] == "sandbox; default-src 'none'"
        assert content.headers["Cache-Control"] == "private, no-store"
        assert stale.status == 409
        assert (await stale.json())["code"] == "revision_conflict"


async def test_project_resource_create_route_is_exclusive_and_reports_ignored_items(
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    config = load_config(tmp_path / "config.toml")
    config.project_ignore_patterns = ["*.secret"]
    project = SimpleNamespace(id="project-one", root=str(root), name="Project One")
    app = web.Application(middlewares=[error_middleware])
    app["config"] = config
    app["projects"] = SimpleNamespace(projects={project.id: project})
    app.router.add_post("/projects/{project_id}/resources", post_project_resource)

    async with TestClient(TestServer(app)) as client:
        created = await client.post(
            "/projects/project-one/resources",
            json={"parent": "src", "name": "draft.secret", "kind": "file"},
        )
        collision = await client.post(
            "/projects/project-one/resources",
            json={"parent": "src", "name": "draft.secret", "kind": "directory"},
        )
        invalid = await client.post(
            "/projects/project-one/resources",
            json={"parent": "src", "name": "../escape", "kind": "file"},
        )

        created_payload = await created.json()
        collision_payload = await collision.json()

    assert created.status == 201
    assert created_payload["path"] == "src/draft.secret"
    assert created_payload["hidden"] is True
    assert (root / "src" / "draft.secret").read_bytes() == b""
    assert collision.status == 409
    assert collision_payload["code"] == "resource_exists"
    assert invalid.status == 400
    assert not (root / "escape").exists()
