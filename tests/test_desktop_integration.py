"""The desktop-integration surface: present on Windows, absent elsewhere."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from swe_mux import app_keys as keys
from swe_mux.config import Config
from swe_mux.routes import desktop_integration


def _config(tmp_path: Path) -> Config:
    config = Config(data_dir=tmp_path)
    config.config_path = tmp_path / "config.toml"
    return config


def test_status_is_absent_not_failing_off_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(desktop_integration, "IS_WINDOWS", False)
    assert desktop_integration._status(_config(tmp_path)) == {"supported": False}


def test_status_reports_both_halves_on_windows(tmp_path: Path) -> None:
    if sys.platform != "win32":
        pytest.skip("the surface exists only on Windows")
    payload = desktop_integration._status(_config(tmp_path))
    assert payload["supported"] is True
    shortcuts = payload["shortcuts"]["slots"]  # type: ignore[index]
    assert set(shortcuts) == {"start-menu", "desktop", "startup"}
    shell = payload["shell"]  # type: ignore[index]
    # The install-time remedy is stated exactly, never guessed at.
    assert "desktop" in shell["extra_command"]
    # The press route answers with the shared four-state vocabulary.
    assert shell["closure"]["status"] in {"not_downloaded", "downloading", "ready", "error"}


async def test_posts_refuse_off_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(desktop_integration, "IS_WINDOWS", False)

    class _Request:
        app = {keys.CONFIG: _config(tmp_path)}
        can_read_body = False

        async def json(self) -> dict[str, object]:
            return {}

    request: object = _Request()
    assert (await desktop_integration.post_shortcuts(request)).status == 400  # type: ignore[arg-type]
    assert (await desktop_integration.post_shell_download(request)).status == 400  # type: ignore[arg-type]
