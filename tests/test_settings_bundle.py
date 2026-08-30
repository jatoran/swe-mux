"""The Settings panel opens with one bundled GET instead of nine round trips."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from swe_mux import app_keys as keys
from swe_mux.config import Config
from swe_mux.llm_endpoint import CapabilityStore
from swe_mux.routes.settings import settings_bundle


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

    async def project_history_counts(self) -> dict[str, int]:
        return {}


class SecretStoreStub:
    def status(self, name: str) -> dict[str, Any]:
        return {"configured": False, "source": "none", "persistent": False}

    def get(self, name: str) -> str | None:
        return None


class AutomationStoreStub:
    async def model_cache(self) -> dict[str, Any]:
        return {"models": [], "stale": True}

    async def provider_verification(self, provider: str) -> dict[str, Any] | None:
        return None


class UsageStub:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def snapshot(self) -> dict[str, Any]:
        if self.fail:
            raise RuntimeError("ccusage cache unreadable")
        return {
            "enabled": False,
            "refreshing": False,
            "collector": {"id": "ccusage", "status": "disabled"},
        }


def _request(
    tmp_path: Path,
    *,
    usage_fails: bool = False,
    cwd: str | None = None,
    parts: str | None = None,
) -> Any:
    app = {
        keys.CONFIG: Config(data_dir=tmp_path),
        keys.AUTOMATION: AutomationStub(),
        keys.HISTORY: HistoryStub(),
        keys.HOOKS: SimpleNamespace(rules=[], diagnostic=None),
        keys.PROJECTS: SimpleNamespace(ordered_projects=lambda: []),
        keys.SECRET_STORE: SecretStoreStub(),
        keys.AUTOMATION_STORE: AutomationStoreStub(),
        keys.LLM_CAPABILITIES: CapabilityStore(),
        keys.USAGE: UsageStub(fail=usage_fails),
    }
    query: dict[str, str] = {}
    if cwd:
        query["cwd"] = cwd
    if parts is not None:
        query["parts"] = parts
    return SimpleNamespace(app=app, query=query)


async def test_the_default_bundle_is_what_first_paint_needs_and_nothing_else(
    tmp_path: Path,
) -> None:
    """The split that matters, asserted as a set rather than as sizes.

    The panel blocks its first paint on this response. Measured 2026-08-30 before
    the split, it was 518 KiB, of which `usage` was 319.8 KiB and `provider`
    177.3 KiB - two tabs the operator usually has not opened - against 12.7 KiB
    of config the panel actually renders from. Both are now fetched by the tab
    that reads them.
    """
    response = await settings_bundle(cast(Any, _request(tmp_path)))

    assert response.status == 200
    payload = json.loads(response.text or "")
    # The exact part set the frontend destructures on open. The rules.toml text is
    # deliberately absent: the Automation dashboard owns the rules editor and loads
    # `GET /api/automation/rules` itself, so Settings never holds a stale copy its
    # Save could write back over a dashboard edit.
    assert set(payload) == {
        "config",
        "keybindings",
        "profiles",
        "projects",
        "automation",
        "project_config",
        "errors",
    }
    assert "usage" not in payload
    assert "provider" not in payload
    assert payload["errors"] == {}
    assert payload["config"]["scrollback_bytes"] == 5 * 1024 * 1024
    assert payload["keybindings"]["bindings"]
    assert isinstance(payload["profiles"]["detected"], list)
    assert payload["projects"] == []
    assert payload["automation"]["repository_rules"] == []
    # No cwd supplied, so the per-project part is intentionally absent.
    assert payload["project_config"] is None


async def test_a_tab_asks_for_its_own_part_and_gets_only_that(tmp_path: Path) -> None:
    """`config` always rides along, because nothing renders without it."""
    response = await settings_bundle(cast(Any, _request(tmp_path, parts="usage")))
    payload = json.loads(response.text or "")
    assert set(payload) == {"config", "usage", "errors"}
    assert payload["usage"]["enabled"] is False

    response = await settings_bundle(cast(Any, _request(tmp_path, parts="provider")))
    payload = json.loads(response.text or "")
    assert set(payload) == {"config", "provider", "errors"}
    assert payload["provider"]["secret"]["configured"] is False


async def test_an_unknown_part_is_refused_rather_than_dropped(tmp_path: Path) -> None:
    """Silently dropping it would hand the client a payload missing a key it
    asked for, which reads exactly like a part that failed - and those need
    different handling."""
    response = await settings_bundle(cast(Any, _request(tmp_path, parts="usage,telemetry")))

    assert response.status == 400
    payload = json.loads(response.text or "")
    assert payload["code"] == "unknown_bundle_part"
    assert "telemetry" in payload["error"]
    assert "usage" in payload["known"]


async def test_bundle_degrades_a_failed_part_to_null_instead_of_failing(tmp_path: Path) -> None:
    request = _request(tmp_path, usage_fails=True, parts="usage,keybindings")
    response = await settings_bundle(cast(Any, request))

    assert response.status == 200
    payload = json.loads(response.text or "")
    assert payload["usage"] is None
    assert "ccusage cache unreadable" in payload["errors"]["usage"]
    # Everything else still arrives, including the parts the panel cannot open without.
    assert payload["config"]["scrollback_bytes"]
    assert payload["keybindings"]["bindings"]


async def test_bundle_reads_project_config_for_the_requested_cwd(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    response = await settings_bundle(cast(Any, _request(tmp_path, cwd=str(project_root))))

    assert response.status == 200
    payload = json.loads(response.text or "")
    assert payload["project_config"] is not None
    assert "values" in payload["project_config"]
