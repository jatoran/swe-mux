from __future__ import annotations

import sys
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from swe_mux import app_keys as keys
from swe_mux import session_resume
from swe_mux.adapters import CodexAdapter
from swe_mux.config import Config, LaunchProfile, load_config, update_config
from swe_mux.models import ProjectRecord
from swe_mux.profiles import (
    derive_capabilities,
    detected_profiles,
    profile_payload,
    resolve_profile,
)
from swe_mux.routes.history import resume_history
from swe_mux.routes.sessions import spawn_session


def profile(profile_id: str = "pwsh") -> LaunchProfile:
    return LaunchProfile(
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
    # A stored executable shaped for another host is re-derived on load, so a
    # round-trip fixture has to name one this host could start - otherwise this
    # would be asserting the exact survival `tests/test_foreign_host_config.py`
    # exists to prevent. Every other field is unchanged, which is what is under
    # test here.
    configured.executable = "pwsh.exe" if sys.platform == "win32" else "pwsh"
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


@pytest.mark.skipif(
    sys.platform != "win32", reason="Windows shell detection (PowerShell, CMD, WSL)"
)
def test_detected_presets_include_native_shells_and_wsl_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("swe_mux.profiles._available", lambda command: rf"C:\bin\{command}")
    monkeypatch.setattr("swe_mux.profiles._wsl_distros", lambda: ["Ubuntu"])

    profiles = {item.id: item for item in detected_profiles()}

    assert {"powershell", "pwsh", "cmd", "wsl-ubuntu"} <= set(profiles)
    assert profiles["cmd"].args == ["/Q"]
    # Capabilities are derived from the profile now, not written beside it. The
    # hand-written copy could be edited to claim `agent-aware` about the one shell
    # where the agent shims provably cannot reach.
    assert "agent-aware" in derive_capabilities(profiles["pwsh"], breakpoints=True)
    assert "agent-bridge-unavailable" in derive_capabilities(
        profiles["wsl-ubuntu"], breakpoints=True
    )


def test_capabilities_describe_what_the_pane_will_actually_support() -> None:
    """One function answers this, and `resolve_profile` calls the same one.

    The two entries worth having were previously computed at every spawn and then
    discarded, so the only capabilities a user ever saw were the ones typed by hand.
    """
    powershell = LaunchProfile("p", "PowerShell 7", "pwsh.exe", ["-NoLogo"])
    assert derive_capabilities(powershell, breakpoints=True) == (
        "interactive",
        "agent-aware",
        "breakpoint-osc133",
    )

    powershell.cwd_integration = True
    assert "cwd-osc7" in derive_capabilities(powershell, breakpoints=True)
    assert "breakpoint-osc133" not in derive_capabilities(powershell, breakpoints=False)

    # A profile handed a script has no prompt to instrument and no room for the
    # bootstrap, so it earns neither marker.
    scripted = LaunchProfile("s", "Scripted", "pwsh.exe", ["-Command", "Get-Date"])
    assert derive_capabilities(scripted, breakpoints=True) == ("interactive", "agent-aware")

    # cmd runs the shims fine but cannot carry the PowerShell bootstrap.
    assert derive_capabilities(
        LaunchProfile("c", "Cmd", "cmd.exe", ["/Q"]), breakpoints=True
    ) == ("interactive", "agent-aware")

    # An agent profile contributes arguments to a CLI; it has no shell to instrument.
    agent = LaunchProfile("a", "Claude (plan)", "", ["--permission-mode", "plan"])
    agent.backend = "claude"
    assert derive_capabilities(agent, breakpoints=True) == ()


def test_the_profiles_payload_publishes_derived_capabilities_not_stored_ones() -> None:
    """What a user reads must be what a spawn produces.

    The stored list stays in the record so existing `config.toml` files load, but it
    is no longer echoed back: a profile could otherwise keep advertising a
    capability the daemon would never grant it.
    """
    lying = LaunchProfile(
        "wsl-ubuntu",
        "WSL: Ubuntu",
        "wsl.exe",
        ["--distribution", "Ubuntu"],
        cwd_strategy="wsl",
        capabilities=["interactive", "agent-aware", "invented"],
    )

    payload = profile_payload(Config(shell_profiles=[lying], default_shell_profile="wsl-ubuntu"))

    published = cast(list[dict[str, Any]], payload["profiles"])[0]["capabilities"]
    assert published == ["interactive", "wsl", "agent-bridge-unavailable"]
    assert "invented" not in published


def test_wsl_profile_translates_windows_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wsl = tmp_path / "wsl.exe"
    wsl.write_bytes(b"fixture")
    configured = LaunchProfile(
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
        keys.CONFIG: config,
        keys.SESSIONS: SimpleNamespace(spawn=spawn),
        keys.PROJECTS: SimpleNamespace(
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
    # Patched on `session_resume`, which owns the resume for both the route and the
    # scheduled-resume path.
    monkeypatch.setattr(session_resume, "RESUME_SETTLE_SECONDS", 0.3)
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
            keys.HISTORY: history,
            keys.SESSIONS: manager,
            keys.PROJECTS: projects,
            keys.AUTOMATION_STORE: SimpleNamespace(add_lineage=add_lineage),
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
