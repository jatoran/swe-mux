from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import app_keys as keys
from swe_mux.config import Config
from swe_mux.event_bus import EventBus
from swe_mux.models import ProjectRecord, SessionRecord
from swe_mux.plugin_manifest import PluginManifestError, parse_plugin_manifest
from swe_mux.plugin_store import PluginStore
from swe_mux.plugins import PluginError, PluginManager
from swe_mux.routes import plugins as plugin_routes
from swe_mux.server import create_app


def write_plugin(root: Path, *, plugin_id: str = "tests.utility") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    script = root / "plugin.py"
    script.write_text(
        """import json, os, pathlib, sys
context=json.loads(os.environ.get('SWEMUX_PLUGIN_CONTEXT_JSON','{}'))
state=pathlib.Path(os.environ['SWEMUX_PLUGIN_STATE_DIR'])
state.mkdir(parents=True,exist_ok=True)
if sys.argv[1]=='record':
    (state/sys.argv[2]).write_text(json.dumps(context),encoding='utf-8')
print(json.dumps({'context':context,'plugin':os.environ['SWEMUX_PLUGIN_ID']}))
""",
        encoding="utf-8",
    )
    manifest = root / "swe-mux-plugin.toml"
    manifest.write_text(
        f'''manifest_version = 1
id = "{plugin_id}"
name = "Utility test"
version = "1.2.3"
min_swe_mux_version = "0.1.0"
description = "Exercises every executable contribution."
platforms = ["windows", "linux", "macos"]
requires = [
  "plugin.actions.v1",
  "plugin.panes.v1",
  "plugin.events.v1",
  "plugin.startup.v1",
  "plugin.links.v1",
]
permissions = ["projects.read", "plugins.self"]

[[actions]]
id = "inspect"
title = "Inspect context"
contexts = ["global", "project", "session"]
command = ["{sys.executable.replace(chr(92), chr(92) * 2)}", "plugin.py", "print"]

[[panes]]
id = "dashboard"
title = "Utility dashboard"
contexts = ["project"]
placement = "split"
command = ["{sys.executable.replace(chr(92), chr(92) * 2)}", "plugin.py", "print"]

[[events]]
id = "project-watch"
on = "project_created"
match = {{ source = "test*" }}
rate_limit_seconds = 0
command = ["{sys.executable.replace(chr(92), chr(92) * 2)}", "plugin.py", "record", "event.json"]

[[startup]]
id = "restore"
command = ["{sys.executable.replace(chr(92), chr(92) * 2)}", "plugin.py", "record", "startup.json"]

[[link_handlers]]
id = "github"
title = "Inspect GitHub link"
pattern = "^https://github\\\\.com/"
action = "inspect"
''',
        encoding="utf-8",
    )
    return manifest


def test_manifest_parses_every_contribution(tmp_path: Path) -> None:
    manifest = parse_plugin_manifest(write_plugin(tmp_path / "plugin"))
    assert manifest.id == "tests.utility"
    assert manifest.security_digest != manifest.digest
    assert [item.id for item in manifest.actions] == ["inspect"]
    assert [item.placement for item in manifest.panes] == ["split"]
    assert [item.on for item in manifest.events] == ["project_created"]
    assert [item.id for item in manifest.startup] == ["restore"]
    assert [item.action for item in manifest.link_handlers] == ["inspect"]


def test_manifest_rejects_undeclared_capability_and_host_env(tmp_path: Path) -> None:
    path = write_plugin(tmp_path / "plugin")
    text = path.read_text(encoding="utf-8").replace(
        'requires = [\n  "plugin.actions.v1",\n  "plugin.panes.v1",\n'
        '  "plugin.events.v1",\n  "plugin.startup.v1",\n'
        '  "plugin.links.v1",\n]',
        'requires = ["plugin.actions.v1"]',
    )
    path.write_text(text, encoding="utf-8")
    with pytest.raises(PluginManifestError, match="requires is missing"):
        parse_plugin_manifest(path)
    path = write_plugin(tmp_path / "plugin")
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            'contexts = ["global", "project", "session"]',
            'contexts = ["global"]\nenv = { MUX_MCP_TOKEN = "spoof" }',
        ),
        encoding="utf-8",
    )
    with pytest.raises(PluginManifestError, match="cannot override host identity"):
        parse_plugin_manifest(path)


@pytest.mark.asyncio
async def test_plugin_store_round_trip_and_bounded_log(tmp_path: Path) -> None:
    store = PluginStore(tmp_path / "mux.db")
    record = await store.put(
        {
            "id": "tests.utility",
            "name": "Utility",
            "version": "1.0.0",
            "enabled": False,
            "lifecycle": "inspected",
            "source_kind": "link",
            "root": str(tmp_path),
            "manifest_path": str(tmp_path / "swe-mux-plugin.toml"),
            "manifest_digest": "a",
            "security_digest": "b",
        }
    )
    assert record["enabled"] is False
    enabled = await store.set_state("tests.utility", enabled=True, lifecycle="enabled")
    assert enabled and enabled["enabled"] is True
    await store.log_started(
        {
            "id": "log-1",
            "plugin_id": "tests.utility",
            "contribution_kind": "action",
            "contribution_id": "inspect",
            "invocation_source": "test",
            "correlation_id": "correlation",
            "context": {"project_id": "p1"},
            "started_at": 1.0,
        }
    )
    await store.log_finished("log-1", outcome="succeeded", exit_code=0, stdout="ok")
    assert (await store.logs("tests.utility"))[0]["context"] == {"project_id": "p1"}
    assert await store.execution_enabled() is True
    await store.set_execution_enabled(False)
    assert await store.execution_enabled() is False
    await store.close()


class FakeSessions:
    def __init__(self) -> None:
        self.sessions: dict[str, Any] = {}
        self.spawn_args: dict[str, Any] = {}
        self.spawn_count = 0

    async def spawn(self, **kwargs: Any) -> Any:
        self.spawn_count += 1
        self.spawn_args = kwargs
        record = SessionRecord(
            "pane-1",
            kwargs["name"],
            kwargs["project_id"],
            "shell",
            "pane-1",
            kwargs["cwd"],
            kwargs["exe"],
            kwargs["args"],
        )
        session = SimpleNamespace(record=record, approval_input_sink=None)
        self.sessions[record.id] = session
        return session

    async def stop(self, session_id: str, *, reason: str = "killed") -> None:
        self.sessions.pop(session_id, None)


@pytest.mark.asyncio
async def test_manager_link_approval_action_pane_event_and_uninstall(tmp_path: Path) -> None:
    root = tmp_path / "plugin"
    write_plugin(root)
    events = EventBus()
    sessions = FakeSessions()
    project = ProjectRecord("p1", "Project", str(tmp_path), 0)
    projects = SimpleNamespace(projects={project.id: project})
    manager = PluginManager(
        data_dir=tmp_path / "data",
        database_path=tmp_path / "mux.db",
        events=events,
        sessions=sessions,
        projects=projects,
        port=8765,
    )
    await manager.start()
    linked = await manager.link(root, approve=True, enable=True)
    assert linked["enabled"] is True
    result = await manager.invoke_action(
        "tests.utility", "inspect", {"context": "project", "project_id": "p1"}
    )
    assert result["outcome"] == "succeeded"
    assert json.loads(result["stdout"])["plugin"] == "tests.utility"
    pane = await manager.open_pane(
        "tests.utility", "dashboard", {"context": "project", "project_id": "p1"}
    )
    assert pane["placement"] == "split"
    assert pane["session"]["plugin_id"] == "tests.utility"
    assert pane["session"]["spawn_env"] == {}
    assert sessions.spawn_args["retain_extra_env"] is False
    assert sessions.spawn_args["exe"] == sys.executable
    same_pane = await manager.open_pane(
        "tests.utility", "dashboard", {"context": "project", "project_id": "p1"}
    )
    assert same_pane["reused"] is True
    assert same_pane["session"]["id"] == "pane-1"
    assert sessions.spawn_count == 1
    await events.emit("project_created", source="tests", project_id="p1")
    event_file = manager.states / "tests.utility" / "event.json"
    for _ in range(100):
        if event_file.exists():
            break
        await asyncio.sleep(0.02)
    assert json.loads(event_file.read_text(encoding="utf-8"))["event"]["type"] == "project_created"
    link = await manager.activate_link(
        "tests.utility",
        "github",
        "https://github.com/jatoran/swe-mux",
        {"context": "global"},
    )
    assert link["outcome"] == "succeeded"
    await manager.enable("tests.utility", False)
    with pytest.raises(PluginError, match="not enabled"):
        await manager.invoke_action("tests.utility", "inspect", {"context": "global"})
    with pytest.raises(PluginError, match="stop the plugin panes"):
        await manager.uninstall("tests.utility", purge=True)
    await sessions.stop("pane-1")
    assert (await manager.status())["runtime_tokens"] == 0
    removed = await manager.uninstall("tests.utility", purge=True)
    assert removed["id"] == "tests.utility"
    assert root.exists(), "unlinking must never remove a developer working tree"
    await manager.stop()


@pytest.mark.asyncio
async def test_manifest_change_revokes_enablement(tmp_path: Path) -> None:
    root = tmp_path / "plugin"
    path = write_plugin(root)
    manager = PluginManager(
        data_dir=tmp_path / "data",
        database_path=tmp_path / "mux.db",
        events=EventBus(),
        sessions=FakeSessions(),
        projects=SimpleNamespace(projects={}),
        port=8765,
    )
    await manager.start()
    await manager.link(root, approve=True, enable=True)
    path.write_text(
        path.read_text(encoding="utf-8").replace("Utility test", "Changed utility"),
        encoding="utf-8",
    )
    listing = await manager.list()
    changed = listing["plugins"][0]
    assert changed["name"] == "Changed utility"
    assert changed["enabled"] is False
    assert changed["lifecycle"] == "changed"
    assert changed["approval_current"] is False
    approved = await manager.approve("tests.utility")
    assert approved["name"] == "Changed utility"
    await manager.stop()


@pytest.mark.asyncio
async def test_plugin_source_change_revokes_enablement(tmp_path: Path) -> None:
    root = tmp_path / "plugin"
    write_plugin(root)
    manager = PluginManager(
        data_dir=tmp_path / "data",
        database_path=tmp_path / "mux.db",
        events=EventBus(),
        sessions=FakeSessions(),
        projects=SimpleNamespace(projects={}),
        port=8765,
    )
    await manager.start()
    await manager.link(root, approve=True, enable=True)
    script = root / "plugin.py"
    script.write_text(script.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    changed = (await manager.list())["plugins"][0]
    assert changed["enabled"] is False
    assert changed["approval_current"] is False
    assert "approve" in changed["diagnostic"]
    await manager.stop()


@pytest.mark.asyncio
async def test_plugin_http_lifecycle_and_scoped_callback(tmp_path: Path) -> None:
    root = tmp_path / "plugin"
    write_plugin(root)
    events = EventBus()
    sessions = FakeSessions()
    project = ProjectRecord("p1", "Project", str(tmp_path), 0)
    projects = SimpleNamespace(projects={project.id: project})
    manager = PluginManager(
        data_dir=tmp_path / "data",
        database_path=tmp_path / "mux.db",
        events=events,
        sessions=sessions,
        projects=projects,
        port=8765,
    )
    await manager.start()
    app = web.Application()
    app[keys.PLUGINS] = manager
    app[keys.EVENTS] = events
    app[keys.PROJECTS] = projects
    app[keys.SESSIONS] = sessions
    app.add_routes(plugin_routes.ROUTES)
    async with TestClient(TestServer(app)) as client:
        linked = await client.post(
            "/api/plugins/link", json={"path": str(root), "approve": True, "enable": True}
        )
        assert linked.status == 201
        catalogue = await (await client.get("/api/plugins")).json()
        assert catalogue["plugins"][0]["manifest"]["actions"][0]["id"] == "inspect"
        opened = await client.post(
            "/api/plugins/tests.utility/panes/dashboard",
            json={"context": "project", "project_id": "p1"},
        )
        assert opened.status == 201
        token = next(
            token for token, grant in manager._tokens.items() if grant.session_id == "pane-1"
        )
        callback = await client.post(
            "/api/plugins/callback",
            json={"operation": "projects.list"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert callback.status == 200
        assert (await callback.json())[0]["id"] == "p1"
        forbidden = await client.post(
            "/api/plugins/callback",
            json={"operation": "session.stop", "session_id": "pane-1"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert forbidden.status == 403
        disabled = await client.post("/api/plugins/tests.utility/enable", json={"enabled": False})
        assert disabled.status == 200
        assert (await disabled.json())["enabled"] is False
    await manager.stop()


@pytest.mark.asyncio
async def test_managed_update_is_inert_and_rollback_restores_previous_version(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    manifest_path = write_plugin(source)
    manager = PluginManager(
        data_dir=tmp_path / "data",
        database_path=tmp_path / "mux.db",
        events=EventBus(),
        sessions=FakeSessions(),
        projects=SimpleNamespace(projects={}),
        port=8765,
    )
    await manager.start()
    installed = await manager.install(str(source), approve=True, enable=True)
    assert installed["version"] == "1.2.3"
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8").replace('version = "1.2.3"', 'version = "2.0.0"'),
        encoding="utf-8",
    )
    updated = await manager.update("tests.utility")
    assert updated["version"] == "2.0.0"
    assert updated["enabled"] is False
    assert updated["lifecycle"] == "inspected"
    rolled_back = await manager.rollback_plugin("tests.utility")
    assert rolled_back["version"] == "1.2.3"
    assert rolled_back["enabled"] is False
    assert rolled_back["lifecycle"] == "inspected"
    await manager.stop()


@pytest.mark.asyncio
async def test_real_app_serves_plugin_registry_without_installed_content(
    tmp_path: Path,
) -> None:
    app = create_app(Config(data_dir=tmp_path / "data", update_check_enabled=False))
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/plugins")
        for _ in range(200):
            if response.status != 503:
                break
            await asyncio.sleep(0.02)
            response = await client.get("/api/plugins")
        assert response.status == 200
        payload = await response.json()
        assert payload["plugins"] == []
        assert "plugin.actions.v1" in payload["host_capabilities"]
        schema = await client.get("/api/plugins/schema")
        assert schema.status == 200
        assert "swe-mux plugin manifest v1" in await schema.text()
