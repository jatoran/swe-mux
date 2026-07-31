"""The Settings panel opens with one bundled GET instead of nine round trips."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from swe_mux.config import Config
from swe_mux.server import settings_bundle


class AutomationStub:
    rules: list[Any] = []
    diagnostic = None

    def status(self) -> dict[str, Any]:
        return {"enabled": True, "rules": [], "queue": {"size": 0, "capacity": 8, "dropped": 0}}


class HistoryStub:
    async def project_scopes(self, include_hidden: bool = False) -> list[dict[str, Any]]:
        return []

    async def project_last_activity(self) -> dict[str, float]:
        return {}


class SecretStoreStub:
    def status(self, name: str) -> dict[str, Any]:
        return {"configured": False, "source": "none", "persistent": False}


class AutomationStoreStub:
    async def model_cache(self) -> dict[str, Any]:
        return {"models": [], "stale": True}


class UsageStub:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def snapshot(self) -> dict[str, Any]:
        if self.fail:
            raise RuntimeError("ccusage cache unreadable")
        return {"enabled": False, "refreshing": False, "states": {}}


def _request(tmp_path: Path, *, usage_fails: bool = False, cwd: str | None = None) -> Any:
    app = {
        "config": Config(data_dir=tmp_path),
        "automation": AutomationStub(),
        "history": HistoryStub(),
        "hooks": SimpleNamespace(rules=[], diagnostic=None),
        "projects": SimpleNamespace(ordered_projects=lambda: []),
        "secret_store": SecretStoreStub(),
        "automation_store": AutomationStoreStub(),
        "usage": UsageStub(fail=usage_fails),
    }
    query = {"cwd": cwd} if cwd else {}
    return SimpleNamespace(app=app, query=query)


async def test_bundle_returns_every_settings_part_in_one_response(tmp_path: Path) -> None:
    response = await settings_bundle(cast(Any, _request(tmp_path)))

    assert response.status == 200
    payload = json.loads(response.text or "")
    # The exact part set the frontend destructures on open.
    assert set(payload) == {
        "config",
        "automation_rules",
        "keybindings",
        "profiles",
        "projects",
        "automation",
        "provider",
        "usage",
        "project_config",
        "errors",
    }
    assert payload["errors"] == {}
    assert payload["config"]["scrollback_bytes"] == 5 * 1024 * 1024
    assert payload["automation_rules"]["text"] == "version = 1\n"
    assert payload["keybindings"]["bindings"]
    assert isinstance(payload["profiles"]["detected"], list)
    assert payload["projects"] == []
    assert payload["automation"]["repository_rules"] == []
    assert payload["provider"]["secret"]["configured"] is False
    assert payload["usage"]["enabled"] is False
    # No cwd supplied, so the per-project part is intentionally absent.
    assert payload["project_config"] is None


async def test_bundle_degrades_a_failed_part_to_null_instead_of_failing(tmp_path: Path) -> None:
    response = await settings_bundle(cast(Any, _request(tmp_path, usage_fails=True)))

    assert response.status == 200
    payload = json.loads(response.text or "")
    assert payload["usage"] is None
    assert "ccusage cache unreadable" in payload["errors"]["usage"]
    # Everything else still arrives, including the parts the panel cannot open without.
    assert payload["config"]["scrollback_bytes"]
    assert payload["keybindings"]["bindings"]
    assert payload["automation_rules"]["text"]


async def test_bundle_reads_project_config_for_the_requested_cwd(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    response = await settings_bundle(cast(Any, _request(tmp_path, cwd=str(project_root))))

    assert response.status == 200
    payload = json.loads(response.text or "")
    assert payload["project_config"] is not None
    assert "values" in payload["project_config"]
