from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from swe_mux import server
from swe_mux.adapters import CodexAdapter
from swe_mux.config import Config, ShellProfile, load_config, update_config
from swe_mux.models import ProjectRecord
from swe_mux.profiles import detected_profiles, resolve_profile
from swe_mux.server import resume_history, spawn_session


def profile(profile_id: str = "pwsh") -> ShellProfile:
    return ShellProfile(
        profile_id,
        "PowerShell 7",
        "pwsh.exe",
        ["-NoLogo"],
        {"PROFILE_TEST": "yes"},
        marker="ps7",
    )


def test_profile_config_round_trips_all_runtime_fields(tmp_path: Path) -> None:
    config = load_config(tmp_path / "config.toml")
    configured = profile()
    update_config(
        config,
        {
            "shell_profiles": [asdict(configured)],
            "default_shell_profile": "pwsh",
            "pinned_directories": [str(tmp_path)],
        },
    )

    loaded = load_config(tmp_path / "config.toml")
    assert loaded.default_shell_profile == "pwsh"
    assert loaded.shell_profiles == [configured]
    assert loaded.pinned_directories == [str(tmp_path)]


def test_resolve_profile_preserves_profile_owned_argv_and_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "pwsh.exe"
    executable.write_bytes(b"fixture")
    configured = profile()
    configured.executable = str(executable)
    config = Config(shell_profiles=[configured], default_shell_profile="pwsh")

    resolved = resolve_profile(config, "pwsh", tmp_path)

    assert resolved.executable == str(executable)
    # The profile's own argv leads; the shim-path bootstrap is appended after it.
    assert resolved.argv[:2] == ("-NoLogo", "-NoExit")
    assert resolved.argv[2] == "-Command"
    assert "MUX_SHIM_DIR" in resolved.argv[3]
    assert resolved.env == {"PROFILE_TEST": "yes"}


def test_detected_presets_include_native_shells_and_wsl_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("swe_mux.profiles._available", lambda command: rf"C:\bin\{command}")
    monkeypatch.setattr("swe_mux.profiles._wsl_distros", lambda: ["Ubuntu"])

    profiles = {item.id: item for item in detected_profiles()}

    assert {"powershell", "pwsh", "cmd", "wsl-ubuntu"} <= set(profiles)
    assert profiles["cmd"].args == ["/Q"]
    assert "agent-aware" in profiles["pwsh"].capabilities
    assert "agent-bridge-unavailable" in profiles["wsl-ubuntu"].capabilities


def test_wsl_profile_translates_windows_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wsl = tmp_path / "wsl.exe"
    wsl.write_bytes(b"fixture")
    configured = ShellProfile(
        "wsl-ubuntu",
        "WSL: Ubuntu",
        str(wsl),
        ["--distribution", "Ubuntu"],
        cwd_strategy="wsl",
        capabilities=["interactive", "wsl"],
    )
    config = Config(shell_profiles=[configured], default_shell_profile=configured.id)
    monkeypatch.setattr(
        "swe_mux.profiles.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="/mnt/d/project\n"),
    )

    resolved = resolve_profile(config, configured.id, tmp_path)

    assert resolved.argv[-2:] == ("--cd", "/mnt/d/project")


async def test_spawn_api_keeps_agent_and_profile_paths_distinct(tmp_path: Path) -> None:
    configured = profile()
    configured.executable = str(tmp_path / "pwsh.exe")
    Path(configured.executable).write_bytes(b"fixture")
    config = Config(shell_profiles=[configured], default_shell_profile="pwsh")
    captured: list[dict[str, Any]] = []

    async def spawn(**kwargs: Any) -> Any:
        captured.append(kwargs)
        record = SimpleNamespace(snapshot=lambda: kwargs)
        return SimpleNamespace(record=record)

    app = {
        "config": config,
        "sessions": SimpleNamespace(spawn=spawn),
        "projects": SimpleNamespace(
            projects={"default": ProjectRecord("default", "Main", str(tmp_path), 0)}
        ),
    }

    class Request:
        def __init__(self, body: dict[str, Any]) -> None:
            self.app = app
            self._body = body

        async def json(self) -> dict[str, Any]:
            return self._body

    await spawn_session(
        cast(
            Any,
            Request({"backend": "shell", "profile_id": "pwsh", "project_id": "default"}),
        )
    )
    await spawn_session(cast(Any, Request({"backend": "claude", "project_id": "default"})))
    raw = tmp_path / "custom-shell.exe"
    raw.write_bytes(b"fixture")
    await spawn_session(
        cast(
            Any,
            Request(
                {
                    "backend": "shell",
                    "executable": str(raw),
                    "argv": ["--exact"],
                    "project_id": "default",
                }
            ),
        )
    )

    assert captured[0]["shell_profile_id"] == "pwsh"
    assert captured[0]["args"][:1] == ["-NoLogo"]
    assert captured[1]["shell_profile_id"] is None
    assert captured[1]["backend"] == "claude"
    assert captured[2]["shell_profile_id"] is None
    assert captured[2]["exe"] == str(raw)
    assert captured[2]["args"] == ["--exact"]


async def test_native_agent_history_resume_bypasses_shell_profiles(
    tmp_path: Path, monkeypatch: Any
) -> None:
    # Codex publishes no per-process state, so nothing can prove the resumed pane
    # took the conversation early and it waits the settle window out. Shortened
    # here rather than skipped: the wait is part of what this path now does.
    monkeypatch.setattr(server, "RESUME_SETTLE_SECONDS", 0.3)
    captured: list[dict[str, Any]] = []
    lineage: list[tuple[str, str, str, dict[str, Any]]] = []
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")

    async def spawn(**kwargs: Any) -> Any:
        captured.append(kwargs)
        # Mirrors SessionManager.spawn: an inherited run id is the record's.
        return SimpleNamespace(
            record=SimpleNamespace(
                id="resumed",
                pid=4242,
                state="idle",
                agent_run_id=kwargs.get("adopt_run_id") or "resumed-run",
                snapshot=lambda: kwargs,
            )
        )

    async def add_lineage(parent: str, child: str, relation: str, metadata: dict[str, Any]) -> None:
        lineage.append((parent, child, relation, metadata))

    async def update_project(*args: Any, **kwargs: Any) -> Any:
        return (args, kwargs)

    history = SimpleNamespace(
        history_entry=lambda identity: _async_value(
            {
                "id": identity,
                "backend": "codex",
                "name": "agent",
                "cwd": str(tmp_path),
                "native_id": "native-codex",
                "agent_visible": 1,
                "transcript_path": str(transcript),
                "project_id": "default",
            }
        )
    )
    manager = SimpleNamespace(
        spawn=spawn,
        # The daemon's answer for a harness that publishes no side state.
        conversation_holder=lambda backend, native_id: None,
        adapters={"codex": CodexAdapter()},
        sessions={},
    )
    projects = SimpleNamespace(
        projects={
            "default": SimpleNamespace(
                name="Main",
                root=str(tmp_path),
                layout={"version": 2, "root": None},
                layout_revision=0,
            )
        },
        update=update_project,
    )
    request = SimpleNamespace(
        app={
            "history": history,
            "sessions": manager,
            "projects": projects,
            "automation_store": SimpleNamespace(add_lineage=add_lineage),
        },
        match_info={"sid": "history-id"},
        can_read_body=False,
    )

    await resume_history(cast(Any, request))

    assert captured[0]["backend"] == "codex"
    assert captured[0]["resume_native_id"] == "native-codex"
    assert "shell_profile_id" not in captured[0]
    # `codex resume` reopens the same rollout, so the pane continues that
    # conversation's run: the same run, and no fork to record.
    assert captured[0]["adopt_run_id"] == "history-id"
    assert lineage == []


async def _async_value(value: Any) -> Any:
    return value
