from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux.config import Config, LaunchProfile
from swe_mux.project_actions import (
    ActionStep,
    ProjectActionService,
    action_spawn_body,
    loads_jsonc,
    process_invocation,
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


def test_action_spawn_carries_cwd_and_runs_no_swe_mux_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The whole point of the direct spawn: a task terminal's process tree contains
    # the shim's command processor and the tool, never a swe-mux executable that
    # would lock dist/swe-mux against a redeploy.
    monkeypatch.setattr("swe_mux.project_actions.shutil.which", lambda command: f"/tools/{command}")
    root = tmp_path / "project"
    root.mkdir()
    (root / "package.json").write_text('{"scripts":{"dev":"vite"}}', encoding="utf-8")
    service = ProjectActionService(tmp_path / "data")
    catalog = service.trust(str(root), service.catalog(str(root)).fingerprint)
    step = next(item for item in catalog.actions if item.id == "package:dev").steps[0]
    config = Config(
        data_dir=tmp_path / "data",
        default_shell_profile="test",
        shell_profiles=[LaunchProfile("test", "Test", "pwsh.exe", ["-NoLogo"])],
    )

    body = action_spawn_body(step, project_id="project-a", config=config, profile_id="test")

    assert body["project_id"] == "project-a"
    assert body["backend"] == "shell"
    assert body["name"] == "dev"
    assert body["completion_mode"] == "one_shot"
    assert body["cwd"] == str(root)
    assert body["env"] == {}
    assert body["executable"] == "/tools/npm"
    assert body["argv"] == ["run", "dev"]
    assert "swe-mux" not in body["executable"]


def test_action_spawn_hands_windows_shims_to_the_command_processor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # npm and friends are .cmd shims: CreateProcess cannot launch them directly, and
    # there is no child of ours left to notice.
    monkeypatch.setattr("swe_mux.project_actions.os.name", "nt")
    monkeypatch.setattr(
        "swe_mux.project_actions.shutil.which", lambda command: rf"C:\bin\{command}.cmd"
    )
    monkeypatch.setenv("COMSPEC", r"C:\Windows\system32\cmd.exe")

    executable, argv = process_invocation("npm", ["run", "dev -- x"])

    assert executable == r"C:\Windows\system32\cmd.exe"
    assert argv == ["/d", "/s", "/c", r'C:\bin\npm.cmd run "dev -- x"']


def test_action_spawn_env_travels_as_a_spawn_field(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / ".vscode").mkdir(parents=True)
    (root / ".vscode" / "tasks.json").write_text(
        json.dumps(
            {
                "version": "2.0.0",
                "tasks": [
                    {
                        "label": "api",
                        "type": "shell",
                        "command": "uv",
                        "args": ["run", "api"],
                        "options": {
                            "cwd": "${workspaceFolder}/backend",
                            "env": {"PORT": 45601, "ROOT": "${workspaceFolder}"},
                            "shell": {"executable": "pwsh.exe"},
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (root / "backend").mkdir()
    service = ProjectActionService(tmp_path / "data")
    catalog = service.trust(str(root), service.catalog(str(root)).fingerprint)
    step = catalog.actions[0].steps[0]

    body = action_spawn_body(
        step, project_id="p", config=Config(data_dir=tmp_path / "data"), profile_id="test"
    )

    assert body["cwd"] == str(root / "backend")
    # Scalars are stringified and workspace variables expanded, exactly as before.
    assert body["env"] == {"PORT": "45601", "ROOT": str(root)}
    assert body["argv"] == ["-Command", "uv 'run' 'api'"]


def _shell_step(tmp_path: Path, task: dict[str, Any]) -> ActionStep:
    root = tmp_path / "project"
    (root / ".vscode").mkdir(parents=True)
    (root / ".vscode" / "tasks.json").write_text(
        json.dumps({"version": "2.0.0", "tasks": [task]}), encoding="utf-8"
    )
    service = ProjectActionService(tmp_path / "data")
    catalog = service.trust(str(root), service.catalog(str(root)).fingerprint)
    return catalog.actions[0].steps[0]


@pytest.mark.skipif(
    shutil.which("pwsh.exe") is None,
    reason="needs a resolvable pwsh.exe: resolve_profile refuses an absent executable",
)
def test_shell_task_args_reach_the_spawned_command_line(tmp_path: Path) -> None:
    # A shell step that loses its args runs a bare `uv` and exits, so the spawn has
    # to carry the whole command line, not just the command.
    step = _shell_step(
        tmp_path,
        {
            "label": "Backend: Start",
            "type": "shell",
            "command": "uv",
            "args": ["run", "uvicorn", "backend.app.main:app", "--port", "45601"],
        },
    )
    config = Config(
        data_dir=tmp_path / "data",
        default_shell_profile="test",
        shell_profiles=[LaunchProfile("test", "Test", "pwsh.exe", ["-NoLogo"])],
    )
    body = action_spawn_body(step, project_id="project-a", config=config, profile_id="test")
    assert body["cwd"] == str(tmp_path / "project")
    assert body["argv"] == [
        "-NoLogo",
        "-Command",
        "uv 'run' 'uvicorn' 'backend.app.main:app' '--port' '45601'",
    ]


@pytest.mark.parametrize(
    ("executable", "prefix", "line"),
    [
        ("pwsh.exe", ["-Command"], "npm 'run' 'dev -- x' 'it''s'"),
        ("cmd.exe", ["/d", "/s", "/c"], 'npm run "dev -- x" it\'s'),
        ("/bin/sh", ["-c"], "npm run 'dev -- x' 'it'\"'\"'s'"),
    ],
)
def test_shell_task_args_use_the_target_shell_quoting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    executable: str,
    prefix: list[str],
    line: str,
) -> None:
    # An unresolvable name is passed through as written, so these assertions hold on
    # any machine regardless of which shells are actually installed.
    monkeypatch.setattr("swe_mux.project_actions.shutil.which", lambda command: None)
    step = _shell_step(
        tmp_path,
        {
            "label": "web",
            "type": "shell",
            "command": "npm",
            "args": ["run", "dev -- x", "it's"],
            "options": {"shell": {"executable": executable}},
        },
    )
    config = Config(data_dir=tmp_path / "data")
    body = action_spawn_body(step, project_id="project-a", config=config, profile_id="test")
    assert body["executable"] == executable
    assert body["argv"] == [*prefix, line]


def test_shell_task_quotes_a_spaced_command_with_the_powershell_call_operator(
    tmp_path: Path,
) -> None:
    step = _shell_step(
        tmp_path,
        {
            "label": "tool",
            "type": "shell",
            "command": "${workspaceFolder}/my tool.exe",
            "args": ["--check"],
            "options": {"shell": {"executable": "pwsh.exe", "args": ["-NoProfile", "-Command"]}},
        },
    )
    config = Config(data_dir=tmp_path / "data")
    body = action_spawn_body(step, project_id="project-a", config=config, profile_id="test")
    assert body["argv"] == [
        "-NoProfile",
        "-Command",
        f"& '{tmp_path / 'project'}/my tool.exe' '--check'",
    ]


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


def test_process_steps_resolve_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("swe_mux.project_actions.os.name", "posix")
    monkeypatch.setattr("swe_mux.project_actions.shutil.which", lambda command: f"/tools/{command}")
    assert process_invocation("npm", ["run", "dev"]) == ("/tools/npm", ["run", "dev"])


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
