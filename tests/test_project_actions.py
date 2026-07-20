from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux.action_runner import command_argv
from swe_mux.config import Config, ShellProfile
from swe_mux.project_actions import (
    ProjectActionService,
    action_runner_invocation,
    loads_jsonc,
    runner_spawn_body,
)
from swe_mux.server import error_middleware, list_project_actions, run_project_action


def test_jsonc_parser_preserves_comment_text_inside_strings() -> None:
    parsed = loads_jsonc(
        '{// heading\n"url":"http://localhost:5173//path","items":[1,2,],/*tail*/}'
    )
    assert parsed == {"url": "http://localhost:5173//path", "items": [1, 2]}


def test_catalog_imports_package_vscode_compound_and_native_actions(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / ".vscode").mkdir(parents=True)
    (root / ".swe-mux").mkdir()
    (root / "package.json").write_text(
        json.dumps({"scripts": {"dev": "vite", "test": "vitest"}}), encoding="utf-8"
    )
    (root / ".vscode" / "tasks.json").write_text(
        """{
          // JSONC and workspace variables are normal VS Code task syntax.
          "version": "2.0.0",
          "tasks": [
            {"label":"frontend","type":"process","command":"${workspaceFolder}\\\\web.cmd",},
            {"label":"backend","type":"shell","command":"python -m api"},
            {"label":"all","dependsOn":["frontend","backend"]}
          ]
        }""",
        encoding="utf-8",
    )
    (root / ".swe-mux" / "actions.toml").write_text(
        """version = 1
[[actions]]
id = "checks"
label = "Checks"
[[actions.steps]]
name = "lint"
type = "process"
command = "ruff"
args = ["check", "."]
[[actions.steps]]
name = "types"
command = "mypy ."
""",
        encoding="utf-8",
    )

    service = ProjectActionService(tmp_path / "data")
    catalog = service.catalog(str(root))
    assert catalog.trusted is False
    assert set(catalog.sources) == {
        ".vscode/tasks.json",
        "package.json",
        ".swe-mux/actions.toml",
    }
    assert {item.id for item in catalog.actions} >= {
        "package:dev",
        "vscode:all",
        "native:checks",
    }
    compound = next(item for item in catalog.actions if item.id == "vscode:all")
    assert [step.name for step in compound.steps] == ["frontend", "backend"]
    native = next(item for item in catalog.actions if item.id == "native:checks")
    assert len(native.steps) == 2


def test_trust_is_exact_and_invalidates_when_task_files_change(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    package = root / "package.json"
    package.write_text('{"scripts":{"dev":"vite"}}', encoding="utf-8")
    service = ProjectActionService(tmp_path / "data")
    first = service.catalog(str(root))

    trusted = service.trust(str(root), first.fingerprint)
    assert trusted.trusted is True
    assert service.action(str(root), "package:dev")[1].label == "dev"

    package.write_text('{"scripts":{"dev":"vite --host"}}', encoding="utf-8")
    changed = service.catalog(str(root))
    assert changed.fingerprint != first.fingerprint
    assert changed.trusted is False
    with pytest.raises(PermissionError, match="not trusted"):
        service.action(str(root), "package:dev")
    with pytest.raises(ValueError, match="changed"):
        service.trust(str(root), first.fingerprint)


def test_runner_payload_keeps_action_cwd_and_uses_noninteractive_profile(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    package = root / "package.json"
    package.write_text('{"scripts":{"dev":"vite"}}', encoding="utf-8")
    service = ProjectActionService(tmp_path / "data")
    catalog = service.trust(str(root), service.catalog(str(root)).fingerprint)
    step = next(item for item in catalog.actions if item.id == "package:dev").steps[0]
    config = Config(
        data_dir=tmp_path / "data",
        default_shell_profile="test",
        shell_profiles=[ShellProfile("test", "Test", "pwsh.exe", ["-NoLogo"])],
    )
    body = runner_spawn_body(step, project_id="project-a", config=config, profile_id="test")
    assert body["project_id"] == "project-a"
    assert body["backend"] == "shell"
    assert body["name"] == "dev"
    assert body["argv"][:2] == ["-m", "swe_mux.action_runner"]
    assert body["completion_mode"] == "one_shot"


def test_packaged_actions_use_the_sibling_console_runner() -> None:
    assert action_runner_invocation(
        executable=r"C:\Program Files\swe-mux\swe-mux.exe", frozen=True
    ) == (r"C:\Program Files\swe-mux\swe-mux-action.exe", ())
    assert action_runner_invocation(executable=r"C:\Python\python.exe", frozen=False) == (
        r"C:\Python\python.exe",
        ("-m", "swe_mux.action_runner"),
    )


def test_action_cwd_cannot_escape_project(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / ".swe-mux").mkdir(parents=True)
    (root / ".swe-mux" / "actions.toml").write_text(
        'version=1\n[[actions]]\nid="bad"\ncommand="echo bad"\ncwd=".."\n',
        encoding="utf-8",
    )
    catalog = ProjectActionService(tmp_path / "data").catalog(str(root))
    assert catalog.actions == ()
    assert any("must stay inside" in item for item in catalog.diagnostics)


def test_action_runner_resolves_path_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("swe_mux.action_runner.shutil.which", lambda command: f"/tools/{command}")
    assert command_argv({"command": "npm", "args": ["run", "dev"]}) == [
        "/tools/npm",
        "run",
        "dev",
    ]


@pytest.mark.asyncio
async def test_action_api_discovers_but_refuses_untrusted_execution(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "package.json").write_text('{"scripts":{"dev":"vite"}}', encoding="utf-8")
    project = SimpleNamespace(id="project-one", root=str(root), name="Project One")
    app = web.Application(middlewares=[error_middleware])
    app["projects"] = SimpleNamespace(projects={project.id: project})
    app["project_actions"] = ProjectActionService(tmp_path / "data")
    app.router.add_get("/projects/{project_id}/actions", list_project_actions)
    app.router.add_post("/projects/{project_id}/actions/run", run_project_action)

    async with TestClient(TestServer(app)) as client:
        catalog_response = await client.get("/projects/project-one/actions")
        run_response = await client.post(
            "/projects/project-one/actions/run", json={"action_id": "package:dev"}
        )
        catalog = await catalog_response.json()
        refused = await run_response.json()

    assert catalog_response.status == 200
    assert catalog["trusted"] is False
    assert [item["id"] for item in catalog["actions"]] == ["package:dev"]
    assert run_response.status == 409
    assert refused["code"] == "project_actions_trust_required"
