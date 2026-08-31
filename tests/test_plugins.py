from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import app_keys as keys
from swe_mux import plugins as plugin_module
from swe_mux.cli import _plugin_command, build_parser
from swe_mux.config import Config
from swe_mux.errors import NotFound
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


def test_cli_validation_is_local_and_uses_the_canonical_parser(tmp_path: Path) -> None:
    write_plugin(tmp_path / "plugin")
    result, renderer = _plugin_command(
        SimpleNamespace(plugin_action="validate", path=str(tmp_path / "plugin")),
        "http://127.0.0.1:1",
    )
    assert result["manifest"]["id"] == "tests.utility"
    assert renderer is None


def test_cli_exposes_plugin_development_and_review_operations() -> None:
    parser = build_parser()
    assert parser.parse_args(["plugin", "refresh"]).plugin_action == "refresh"
    assert parser.parse_args(["plugin", "discover"]).plugin_action == "discover"
    root = parser.parse_args(["plugin", "development-root", "C:/plugins", "--create"])
    assert root.path == "C:/plugins" and root.create is True
    assert parser.parse_args(["plugin", "check-updates"]).plugin_action == "check-updates"
    assert (
        parser.parse_args(["plugin", "approve-update", "publisher.plugin"]).plugin_id
        == "publisher.plugin"
    )
    assert (
        parser.parse_args(["plugin", "restart-panes", "publisher.plugin"]).plugin_id
        == "publisher.plugin"
    )


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
            "requested_ref": "latest",
            "selected_ref": "v1.0.0",
            "resolved_ref": "abc123",
            "root": str(tmp_path),
            "manifest_path": str(tmp_path / "swe-mux-plugin.toml"),
            "manifest_digest": "a",
            "security_digest": "b",
        }
    )
    assert record["enabled"] is False
    assert record["requested_ref"] == "latest"
    assert record["selected_ref"] == "v1.0.0"
    assert record["resolved_ref"] == "abc123"
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
    assert await store.get_setting("development_root") is None
    await store.set_setting("development_root", str(tmp_path / "plugins"))
    assert await store.get_setting("development_root") == str(tmp_path / "plugins")
    staged = await store.put_update_stage("tests.utility", {"version": "2.0.0"})
    assert staged["version"] == "2.0.0"
    assert (await store.get_update_stage("tests.utility"))["version"] == "2.0.0"  # type: ignore[index]
    assert (await store.list_update_stages())["tests.utility"]["version"] == "2.0.0"
    assert (await store.remove_update_stage("tests.utility"))["version"] == "2.0.0"  # type: ignore[index]
    await store.close()


@pytest.mark.asyncio
async def test_plugin_store_migrates_release_provenance_columns(tmp_path: Path) -> None:
    path = tmp_path / "mux.db"
    db = sqlite3.connect(path)
    db.execute(
        """CREATE TABLE plugins(
        id TEXT PRIMARY KEY,name TEXT NOT NULL,version TEXT NOT NULL,
        enabled INTEGER NOT NULL,lifecycle TEXT NOT NULL,source_kind TEXT NOT NULL,
        source_ref TEXT NOT NULL,resolved_ref TEXT NOT NULL,root TEXT NOT NULL,
        manifest_path TEXT NOT NULL,manifest_digest TEXT NOT NULL,content_digest TEXT NOT NULL,
        security_digest TEXT NOT NULL,approved_digest TEXT NOT NULL,previous_root TEXT NOT NULL,
        diagnostic TEXT NOT NULL,installed_at REAL NOT NULL,updated_at REAL NOT NULL)"""
    )
    db.execute(
        "INSERT INTO plugins VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "tests.old",
            "Old",
            "1.0.0",
            0,
            "inspected",
            "managed",
            "publisher/old",
            "abc123",
            str(tmp_path),
            str(tmp_path / "swe-mux-plugin.toml"),
            "manifest",
            "content",
            "security",
            "",
            "",
            "",
            1.0,
            1.0,
        ),
    )
    db.commit()
    db.close()
    store = PluginStore(path)
    record = await store.get("tests.old")
    assert record and record["requested_ref"] == ""
    assert record["selected_ref"] == ""
    assert record["resolved_ref"] == "abc123"
    await store.close()


class FakeSessions:
    def __init__(self) -> None:
        self.sessions: dict[str, Any] = {}
        self.spawn_args: dict[str, Any] = {}
        self.spawn_count = 0
        self.published: list[dict[str, Any]] = []

    async def spawn(self, **kwargs: Any) -> Any:
        self.spawn_count += 1
        self.spawn_args = kwargs
        session_id = f"pane-{self.spawn_count}"
        record = SessionRecord(
            session_id,
            kwargs["name"],
            kwargs["project_id"],
            "shell",
            session_id,
            kwargs["cwd"],
            kwargs["exe"],
            kwargs["args"],
        )
        session = SimpleNamespace(
            record=record,
            approval_input_sink=None,
            publish_update=lambda: self.published.append(record.snapshot()),
        )
        self.sessions[record.id] = session
        return session

    def resolve(self, identity: str) -> Any:
        try:
            return self.sessions[identity]
        except KeyError as exc:
            raise NotFound(identity, kind="session") from exc

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
async def test_development_root_discovers_direct_children_without_linking(tmp_path: Path) -> None:
    manager = PluginManager(
        data_dir=tmp_path / "data",
        database_path=tmp_path / "mux.db",
        events=EventBus(),
        sessions=FakeSessions(),
        projects=SimpleNamespace(projects={}),
        port=8765,
    )
    development = tmp_path / "development"
    await manager.set_development_root(str(development), create=True)
    write_plugin(development / "valid")
    invalid = development / "invalid"
    invalid.mkdir()
    (invalid / "swe-mux-plugin.toml").write_text("not = [valid", encoding="utf-8")
    write_plugin(development / "nested" / "ignored")

    scan = await manager.scan_development_root()
    assert [item["name"] for item in scan["candidates"]] == ["invalid", "Utility test"]
    valid = next(item for item in scan["candidates"] if item["id"] == "tests.utility")
    assert valid["linked"] is False
    await manager.link(valid["path"])
    rescanned = await manager.scan_development_root()
    assert next(item for item in rescanned["candidates"] if item["id"] == "tests.utility")[
        "linked"
    ] is True
    await manager.stop()


@pytest.mark.asyncio
async def test_linked_plugin_panes_restart_on_current_approved_source(tmp_path: Path) -> None:
    root = tmp_path / "plugin"
    write_plugin(root)
    sessions = FakeSessions()
    project = ProjectRecord("p1", "Project", str(tmp_path), 0)
    manager = PluginManager(
        data_dir=tmp_path / "data",
        database_path=tmp_path / "mux.db",
        events=EventBus(),
        sessions=sessions,
        projects=SimpleNamespace(projects={project.id: project}),
        port=8765,
    )
    await manager.start()
    await manager.link(root, approve=True, enable=True)
    opened = await manager.open_pane(
        "tests.utility", "dashboard", {"context": "project", "project_id": project.id}
    )
    manager.dock_pane(opened["session"]["id"])

    result = await manager.restart_panes("tests.utility")
    assert result["restarted"][0]["old_session_id"] == "pane-1"
    assert result["restarted"][0]["session"]["id"] == "pane-2"
    assert result["restarted"][0]["placement"] == "tab"
    assert list(sessions.sessions) == ["pane-2"]
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
async def test_uninstall_remains_available_after_linked_manifest_identity_changes(
    tmp_path: Path,
) -> None:
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
        path.read_text(encoding="utf-8").replace("tests.utility", "tests.renamed"),
        encoding="utf-8",
    )
    assert (await manager.list())["plugins"][0]["lifecycle"] == "changed"
    removed = await manager.uninstall("tests.utility")
    assert removed["id"] == "tests.utility"
    assert root.exists()
    assert (await manager.list())["plugins"] == []
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
        development = await client.put(
            "/api/plugins/development",
            json={"path": str(tmp_path / "plugin-development"), "create": True},
        )
        assert development.status == 200
        assert (await development.json())["exists"] is True
        refreshed = await client.post("/api/plugins/refresh")
        assert refreshed.status == 200
        assert (await refreshed.json())["summary"]["checked"] == 1
        opened = await client.post(
            "/api/plugins/tests.utility/panes/dashboard",
            json={"context": "project", "project_id": "p1"},
        )
        assert opened.status == 201
        docked = await client.post("/api/plugins/panes/pane-1/dock")
        assert docked.status == 200
        assert (await docked.json())["plugin_placement"] == "tab"
        assert sessions.published[-1]["plugin_placement"] == "tab"
        reopened = await client.post(
            "/api/plugins/tests.utility/panes/dashboard",
            json={"context": "project", "project_id": "p1"},
        )
        assert reopened.status == 201
        assert (await reopened.json())["placement"] == "tab"
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
        restarted = await client.post("/api/plugins/tests.utility/panes/restart")
        assert restarted.status == 200
        assert (await restarted.json())["restarted"][0]["session"]["id"] == "pane-2"
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
    assert updated["staged"] is True
    current = await manager.store.get("tests.utility")
    assert current and current["version"] == "1.2.3"
    assert current["enabled"] is True
    catalogue = await manager.list(refresh=False)
    assert catalogue["plugins"][0]["staged_update"]["version"] == "2.0.0"
    approved = await manager.approve_update("tests.utility")
    assert approved["version"] == "2.0.0"
    assert approved["enabled"] is True
    rolled_back = await manager.rollback_plugin("tests.utility")
    assert rolled_back["version"] == "1.2.3"
    assert rolled_back["enabled"] is False
    assert rolled_back["lifecycle"] == "inspected"
    await manager.stop()


@pytest.mark.asyncio
async def test_local_managed_update_check_is_read_only(tmp_path: Path) -> None:
    source = tmp_path / "source"
    write_plugin(source)
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
    current = await manager.check_updates()
    assert current["updates"]["tests.utility"]["status"] == "current"
    assert (await manager.store.get("tests.utility"))["enabled"] is True  # type: ignore[index]

    script = source / "plugin.py"
    script.write_text(script.read_text(encoding="utf-8") + "\n# update\n", encoding="utf-8")
    available = await manager.check_updates()
    assert available["available"] == ["tests.utility"]
    assert available["updates"]["tests.utility"]["status"] == "available"
    unchanged = await manager.store.get("tests.utility")
    assert unchanged and unchanged["root"] == installed["root"] and unchanged["enabled"] is True
    await manager.stop()


@pytest.mark.asyncio
async def test_failed_update_acquisition_leaves_active_plugin_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    write_plugin(source)
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

    async def fail_acquisition(source: str, ref: str) -> dict[str, Any]:
        raise PluginError("acquisition_failed", "network unavailable")

    monkeypatch.setattr(manager, "_acquire_managed", fail_acquisition)
    with pytest.raises(PluginError, match="network unavailable"):
        await manager.update("tests.utility")
    current = await manager.store.get("tests.utility")
    assert current and current["root"] == installed["root"]
    assert current["enabled"] is True and current["lifecycle"] == "enabled"
    assert await manager.store.get_update_stage("tests.utility") is None
    await manager.stop()


@pytest.mark.asyncio
async def test_git_update_check_distinguishes_moving_branches_from_pinned_tags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = write_plugin(tmp_path / "installed")
    manager = PluginManager(
        data_dir=tmp_path / "data",
        database_path=tmp_path / "mux.db",
        events=EventBus(),
        sessions=FakeSessions(),
        projects=SimpleNamespace(projects={}),
        port=8765,
    )
    record = await manager.store.put(
        {
            "id": "tests.utility",
            "name": "Utility",
            "version": "1.0.0",
            "enabled": True,
            "lifecycle": "enabled",
            "source_kind": "managed",
            "source_ref": "publisher/utility",
            "requested_ref": "main",
            "selected_ref": "main",
            "resolved_ref": "abc123",
            "root": str(manifest_path.parent),
            "manifest_path": str(manifest_path),
            "manifest_digest": "a",
            "content_digest": "b",
            "security_digest": "c",
        }
    )

    async def branch_remote(*args: Any, **kwargs: Any) -> Any:
        return SimpleNamespace(exit_code=0, stdout=b"def456\trefs/heads/main\n", stderr=b"")

    monkeypatch.setattr(plugin_module, "run_bounded", branch_remote)
    branch = await manager._check_update(record)
    assert branch["status"] == "available" and branch["channel"] == "branch"

    record["requested_ref"] = "v1.0.0"

    async def tag_remote(*args: Any, **kwargs: Any) -> Any:
        return SimpleNamespace(exit_code=0, stdout=b"abc123\trefs/tags/v1.0.0\n", stderr=b"")

    monkeypatch.setattr(plugin_module, "run_bounded", tag_remote)
    tag = await manager._check_update(record)
    assert tag["status"] == "pinned" and tag["channel"] == "tag"
    await manager.store.close()


@pytest.mark.asyncio
async def test_managed_update_retains_the_requested_release_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = PluginManager(
        data_dir=tmp_path / "data",
        database_path=tmp_path / "mux.db",
        events=EventBus(),
        sessions=FakeSessions(),
        projects=SimpleNamespace(projects={}),
        port=8765,
    )
    manifest_path = write_plugin(tmp_path)
    await manager.store.put(
        {
            "id": "tests.utility",
            "name": "Utility",
            "version": "1.0.0",
            "enabled": False,
            "lifecycle": "inspected",
            "source_kind": "managed",
            "source_ref": "publisher/utility",
            "requested_ref": "latest",
            "selected_ref": "v1.0.0",
            "resolved_ref": "abc123",
            "root": str(tmp_path),
            "manifest_path": str(manifest_path),
            "manifest_digest": "a",
            "security_digest": "b",
        }
    )
    captured: dict[str, Any] = {}

    async def fake_acquire(source: str, ref: str) -> dict[str, Any]:
        captured.update(source=source, ref=ref)
        manifest = parse_plugin_manifest(manifest_path)
        return {
            "id": "tests.utility",
            "name": "Utility",
            "version": "2.0.0",
            "source_kind": "managed",
            "source_ref": source,
            "requested_ref": ref,
            "selected_ref": "v2.0.0",
            "resolved_ref": "def456",
            "root": str(tmp_path),
            "manifest_path": str(manifest_path),
            "manifest_digest": manifest.digest,
            "content_digest": "new-content",
            "security_digest": manifest.security_digest,
            "diagnostic": "",
            "manifest": {**manifest.snapshot(), "version": "2.0.0"},
        }

    monkeypatch.setattr(manager, "_acquire_managed", fake_acquire)
    await manager.update("tests.utility")
    assert captured == {"source": "publisher/utility", "ref": "latest"}
    await manager.store.close()


@pytest.mark.asyncio
async def test_latest_release_channel_resolves_a_github_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeResponse:
        status = 200

        async def __aenter__(self) -> FakeResponse:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def json(self) -> dict[str, str]:
            return {"tag_name": "v2.3.4"}

    class FakeClientSession:
        async def __aenter__(self) -> FakeClientSession:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        def get(self, url: str, *, allow_redirects: bool) -> FakeResponse:
            assert url.endswith("/repos/publisher/utility/releases/latest")
            assert allow_redirects is False
            return FakeResponse()

    monkeypatch.setattr(
        plugin_module.aiohttp,
        "ClientSession",
        lambda **kwargs: FakeClientSession(),
    )
    manager = PluginManager(
        data_dir=tmp_path / "data",
        database_path=tmp_path / "mux.db",
        events=EventBus(),
        sessions=FakeSessions(),
        projects=SimpleNamespace(projects={}),
        port=8765,
    )
    assert await manager._latest_release_ref("publisher/utility") == "v2.3.4"
    await manager.store.close()


def test_validated_catalog_maps_manifest_and_release_metadata() -> None:
    repositories = PluginManager._catalog_repositories(
        {
            "schema": 1,
            "plugins": [
                {
                    "official": True,
                    "indexed_ref": "abc123",
                    "install_ref": "v1.0.0",
                    "release_url": "https://github.com/publisher/utility/releases/tag/v1.0.0",
                    "repository": {
                        "name": "utility",
                        "full_name": "publisher/utility",
                        "owner": "publisher",
                        "description": "Repository description",
                        "stars": 2,
                        "language": "Python",
                        "updated_at": "2026-08-31T00:00:00Z",
                        "url": "https://github.com/publisher/utility",
                        "license": "MIT",
                    },
                    "manifest": {
                        "id": "publisher.utility",
                        "name": "Utility",
                        "version": "1.0.0",
                        "description": "Manifest description",
                        "license": "MIT",
                        "permissions": ["projects.read"],
                        "requires": ["plugin.actions.v1"],
                        "platforms": ["windows"],
                        "runtime_requirements": ["python>=3.10"],
                    },
                }
            ],
        }
    )
    assert repositories[0]["plugin_id"] == "publisher.utility"
    assert repositories[0]["description"] == "Manifest description"
    assert repositories[0]["install_ref"] == "v1.0.0"
    assert repositories[0]["official"] is True


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
