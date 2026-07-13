from __future__ import annotations

import json
import sys

import pytest

from swe_mux import cli


def test_cli_profiles_lists_profile_capabilities(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["mux", "profiles"])
    monkeypatch.setattr(
        cli,
        "request",
        lambda method, path, body=None: {"default_profile_id": "pwsh"},
    )

    cli.main()

    assert json.loads(capsys.readouterr().out) == {"default_profile_id": "pwsh"}


def test_cli_spawn_sends_profile_and_structured_raw_arguments(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    captured: list[tuple[str, str, dict[str, object] | None]] = []
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mux", "spawn", "--profile", "pwsh", "--cwd", "D:/work",
            "--arg=-NoExit",
        ],
    )
    monkeypatch.setattr(
        cli,
        "request",
        lambda method, path, body=None: captured.append((method, path, body)) or {"ok": True},
    )

    cli.main()

    assert captured[0][0:2] == ("POST", "/api/sessions")
    assert captured[0][2] is not None
    assert captured[0][2]["profile_id"] == "pwsh"
    assert captured[0][2]["argv"] == ["-NoExit"]
    assert json.loads(capsys.readouterr().out) == {"ok": True}
