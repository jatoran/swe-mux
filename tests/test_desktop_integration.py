"""The desktop-integration surface: present on Windows, absent elsewhere."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from swe_mux import app_keys as keys
from swe_mux.config import Config
from swe_mux.routes import desktop_integration


def _config(tmp_path: Path) -> Config:
    config = Config(data_dir=tmp_path)
    config.config_path = tmp_path / "config.toml"
    return config


class _Request:
    """The two attributes these handlers touch, and a settable JSON body."""

    def __init__(self, config: Config, body: dict[str, object] | None = None) -> None:
        self.app = {keys.CONFIG: config}
        self._body = body
        self.can_read_body = body is not None

    async def json(self) -> dict[str, object]:
        return self._body or {}


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
    # A yes/no about this environment, not an offer to acquire something.
    assert isinstance(shell["importable"], bool)
    assert shell["importable"] is not bool(shell["missing"])
    # A remedy only when there is something to remedy, so a healthy install
    # cannot render a reinstall command at anybody.
    if shell["importable"]:
        assert shell["reinstall_command"] == ""


def test_status_carries_a_runnable_remedy_when_the_shell_cannot_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The message a broken install reads has to name a command it can run.

    Injected rather than taken from this interpreter: the modules are base
    dependencies now, so a machine that can run these tests necessarily has
    them, and asserting against the environment would make this a test of the
    host (`.docs/CLAUDE.md`).
    """
    monkeypatch.setattr(desktop_integration, "IS_WINDOWS", True)
    monkeypatch.setattr(
        desktop_integration, "missing_shell_modules", lambda: ("pystray", "webview")
    )
    payload = desktop_integration._status(_config(tmp_path))
    shell: Any = payload["shell"]
    assert shell["importable"] is False
    assert shell["missing"] == ["pystray", "webview"]
    # A frozen bundle has no installer to re-run and correctly offers no command;
    # everything else must offer one rather than leaving the reader stuck.
    assert bool(shell["reinstall_command"]) is not shell["frozen"]


async def test_posts_refuse_off_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(desktop_integration, "IS_WINDOWS", False)
    request: object = _Request(_config(tmp_path))
    assert (await desktop_integration.post_shortcuts(request)).status == 400  # type: ignore[arg-type]


def test_requested_slots_defaults_to_none_for_a_client_that_sends_none() -> None:
    """An older client means "the usual two", not "write nothing"."""
    assert desktop_integration._requested_slots({}) is None
    assert desktop_integration._requested_slots({"remove": True}) is None


def test_requested_slots_accepts_the_login_slot_and_dedupes() -> None:
    """`startup` is the slot this field exists for: creatable from the UI at last."""
    assert desktop_integration._requested_slots(
        {"slots": ["start-menu", "startup", "start-menu"]}
    ) == ("start-menu", "startup")


def test_requested_slots_refuses_anything_it_does_not_know() -> None:
    """Validated here rather than at the PowerShell boundary.

    `plan_shortcuts` would also refuse an unknown slot, but only after the
    request has been accepted; refusing at the edge is what makes the 400 a 400
    and keeps an arbitrary string out of a rendered script.
    """
    from swe_mux.shortcuts import ShortcutError

    with pytest.raises(ShortcutError, match="unknown shortcut slot"):
        desktop_integration._requested_slots({"slots": ["taskbar"]})
    with pytest.raises(ShortcutError, match="must be a list"):
        desktop_integration._requested_slots({"slots": "startup"})


async def test_a_bad_slot_is_a_400_rather_than_a_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(desktop_integration, "IS_WINDOWS", True)
    request: object = _Request(_config(tmp_path), {"slots": ["taskbar"]})
    response = await desktop_integration.post_shortcuts(request)  # type: ignore[arg-type]
    assert response.status == 400


async def test_removal_addresses_every_slot_whatever_the_request_asked_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Narrowing a cleanup by the caller's selection would strand a login entry.

    `apply_shortcuts` already widens removal to all three slots, and the reason
    it can is that this handler never passes a narrower `slots` through on a
    removal - so a client that asks to "remove start-menu" still takes back the
    run-at-login link it may have forgotten about.
    """
    monkeypatch.setattr(desktop_integration, "IS_WINDOWS", True)
    seen: dict[str, object] = {}

    def _fake(**kwargs: object) -> object:
        seen.update(kwargs)

        class _Report:
            def as_dict(self) -> dict[str, object]:
                return {"action": "remove"}

        return _Report()

    monkeypatch.setattr(desktop_integration, "apply_shortcuts", _fake)
    request: object = _Request(
        _config(tmp_path), {"remove": True, "slots": ["start-menu"]}
    )
    await desktop_integration.post_shortcuts(request)  # type: ignore[arg-type]
    assert seen["remove"] is True
    assert "slots" not in seen
