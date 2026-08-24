from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import app_keys as keys
from swe_mux.config import load_config, update_config
from swe_mux.history import HistoryIndex
from swe_mux.project_actions import action_spawn_body
from swe_mux.project_init import init_script_step, select_init_scripts
from swe_mux.projects import ProjectManager, canonical_project_root
from swe_mux.server import error_middleware, run_project_init_scripts

SCRIPTS = [
    {"id": "git", "label": "Initialize git", "command": "git init", "default_enabled": True},
    {"id": "workspace", "label": "VS Code workspace", "command": "code -n ."},
]


def _config(tmp_path: Path, scripts: list[dict[str, Any]] | None = None):  # type: ignore[no-untyped-def]
    config = load_config(tmp_path / "config.toml")
    if scripts is not None:
        update_config(config, {"project_init_scripts": scripts})
    return config


def test_init_scripts_are_hot_reloadable_and_shape_validated(tmp_path: Path) -> None:
    config = _config(tmp_path)
    hot, restart = update_config(config, {"project_init_scripts": SCRIPTS})
    assert hot == {"project_init_scripts"}
    assert restart == set()
    assert load_config(tmp_path / "config.toml").project_init_scripts == SCRIPTS

    with pytest.raises(ValueError, match="duplicate init script id git"):
        update_config(config, {"project_init_scripts": [SCRIPTS[0], dict(SCRIPTS[0])]})
    with pytest.raises(ValueError, match="non-empty command"):
        update_config(config, {"project_init_scripts": [{"id": "a", "label": "A", "command": " "}]})
    with pytest.raises(ValueError, match="letters, digits, hyphen"):
        update_config(
            config, {"project_init_scripts": [{"id": "bad id", "label": "A", "command": "x"}]}
        )
    with pytest.raises(ValueError, match="at most 32"):
        update_config(
            config,
            {
                "project_init_scripts": [
                    {"id": f"s{index}", "label": "A", "command": "x"} for index in range(33)
                ]
            },
        )


def test_selection_follows_configured_order_and_reports_unknown_ids(tmp_path: Path) -> None:
    config = _config(tmp_path, SCRIPTS)
    chosen, unknown = select_init_scripts(config, ["workspace", "git", "workspace"])
    # Configured order wins: a checkbox list has no order of its own.
    assert [item["id"] for item in chosen] == ["git", "workspace"]
    assert unknown == []
    assert select_init_scripts(config, ["nope"])[1] == ["nope"]


def test_init_script_spawns_one_shot_shell_at_the_project_root(tmp_path: Path) -> None:
    config = _config(tmp_path, SCRIPTS)
    root = tmp_path / "repo"
    root.mkdir()
    step = init_script_step(SCRIPTS[0], root=str(root))
    body = action_spawn_body(
        step, project_id="project-a", config=config, profile_id=config.default_shell_profile
    )
    assert body["project_id"] == "project-a"
    assert body["cwd"] == str(root)
    assert body["completion_mode"] == "one_shot"
    assert body["backend"] == "shell"
    # The command string is handed to the shell untouched, so the user's own `&&`
    # or `;` sequencing works exactly as typed.
    assert any("git init" in str(item) for item in body["argv"])


def test_canonical_root_create_mode_validates_the_new_leaf(tmp_path: Path) -> None:
    # Server-side, because the dialog's Folder field is free text and the
    # assistant path has no dialog at all. Nothing is created on refusal.
    with pytest.raises(ValueError, match="reserved by Windows"):
        canonical_project_root(tmp_path / "COM1", create=True)
    with pytest.raises(ValueError, match="Windows-invalid character"):
        canonical_project_root(tmp_path / "bad<name", create=True)
    with pytest.raises(ValueError, match="control directory names are reserved"):
        canonical_project_root(tmp_path / ".swe-mux", create=True)
    assert not any(tmp_path.iterdir())
    # Adopting a folder that already exists skips leaf validation: it is on disk
    # under whatever name it has. (.git is creatable on every platform; COM1 is
    # not even creatable on Windows, which is the point of the refusal above.)
    existing = tmp_path / ".git"
    existing.mkdir()
    assert canonical_project_root(existing, create=True) == existing.resolve()


def test_canonical_root_creates_one_folder_and_refuses_a_missing_parent(tmp_path: Path) -> None:
    created = canonical_project_root(tmp_path / "fresh", create=True)
    assert created.is_dir()
    # Creating again is not an error: a retried create must not fail on its own work.
    assert canonical_project_root(tmp_path / "fresh", create=True) == created
    with pytest.raises(ValueError, match="parent folder does not exist"):
        canonical_project_root(tmp_path / "missing" / "deep", create=True)
    assert not (tmp_path / "missing").exists()
    with pytest.raises(ValueError, match="does not exist"):
        canonical_project_root(tmp_path / "never", create=False)


async def test_create_makes_the_folder_and_rejects_duplicates_before_creating(
    tmp_path: Path,
) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    projects = ProjectManager(history)
    await projects.start()

    root = tmp_path / "workspace" / "horizon"
    (tmp_path / "workspace").mkdir()
    project = await projects.create("Horizon", str(root), create_missing=True)
    assert Path(project.root) == root.resolve()
    assert (root / ".swe-mux" / "notes" / "project.md").exists()

    with pytest.raises(ValueError, match="already registered"):
        await projects.create("Horizon again", str(root), create_missing=True)
    with pytest.raises(ValueError, match="project folder is required"):
        await projects.create("Nameless", "  ", create_missing=True)
    # A rejected registration leaves no stray directory behind.
    assert not (tmp_path / "workspace" / "other").exists()
    history.close()


@pytest.mark.asyncio
async def test_init_script_endpoint_starts_selected_scripts_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    project = SimpleNamespace(
        id="project-one", root=str(root), name="Project One", default_profile_id=None
    )
    started: list[str] = []

    async def fake_spawn(app: web.Application, body: dict[str, Any]) -> Any:
        started.append(body["name"])
        if body["name"] == "VS Code workspace":
            raise RuntimeError("executable not found")
        record = SimpleNamespace(
            relaunchable=False, snapshot=lambda: {"id": f"session-{body['name']}"}
        )
        return SimpleNamespace(record=record, publish_update=lambda: None)

    events: list[str] = []

    class Events:
        async def emit(self, name: str, **_: Any) -> None:
            events.append(name)

    monkeypatch.setattr("swe_mux.server._spawn_from_body", fake_spawn)
    app = web.Application(middlewares=[error_middleware])
    app[keys.CONFIG] = _config(tmp_path, SCRIPTS)
    app[keys.PROJECTS] = SimpleNamespace(projects={project.id: project})
    app[keys.EVENTS] = Events()
    app.router.add_post("/projects/{project_id}/init-scripts/run", run_project_init_scripts)

    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/projects/project-one/init-scripts/run",
            json={"script_ids": ["workspace", "git"]},
        )
        payload = await response.json()
        unknown = await client.post(
            "/projects/project-one/init-scripts/run", json={"script_ids": ["nope"]}
        )
        malformed = await client.post(
            "/projects/project-one/init-scripts/run", json={"script_ids": "git"}
        )

    assert started == ["Initialize git", "VS Code workspace"]
    # One failed launch is reported without stranding the scripts around it.
    assert response.status == 207
    assert [item["id"] for item in payload["sessions"]] == ["session-Initialize git"]
    assert payload["errors"] == [{"script": "workspace", "error": "executable not found"}]
    assert events == ["project_init_scripts_started"]
    assert unknown.status == 400
    assert malformed.status == 400
