"""First-use contracts across restarts, clients, retained data and prerequisites."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from swe_mux import app_keys as keys
from swe_mux import model_setup, onboarding
from swe_mux.__main__ import load_daemon_config
from swe_mux.automation_registry import LLM_PROJECT_AUTOMATIONS, RECOMMENDED_PROJECT_AUTOMATIONS
from swe_mux.config import Config, update_config
from swe_mux.experience_tiers import tier_changes
from swe_mux.llm_endpoint import LlmReadiness, resolve_endpoint
from swe_mux.reconcile import ExternalTranscript
from swe_mux.routes import onboarding as routes
from swe_mux.routes import settings


def config_at(path: Path) -> Config:
    return Config(data_dir=path, config_path=path / "config.toml")


class Request:
    def __init__(self, config: Config, body: dict[str, Any]) -> None:
        self.app: dict[Any, Any] = {keys.CONFIG: config, keys.EVENTS: AsyncMock()}
        self.body = body

    async def json(self) -> dict[str, Any]:
        return dict(self.body)


def test_fresh_setup_is_immediate_and_a_new_client_resumes(tmp_path: Path) -> None:
    config = config_at(tmp_path)
    first = onboarding.read_state(config)
    assert (first["step"], first["status"]) == ("experience", "active")
    saved = onboarding.change_state(config, {"step": "projects", "status": "deferred"}, 0)
    assert onboarding.read_state(config_at(tmp_path)) == saved
    with pytest.raises(ValueError, match="another client"):
        onboarding.change_state(config, {"hidden": True}, 0)
    assert onboarding.read_state(config)["hidden"] is False


def test_retained_preferences_offer_reuse_once_and_updates_do_not_reset(tmp_path: Path) -> None:
    config = config_at(tmp_path)
    config.harness_setup_complete = True
    first = onboarding.read_state(config)
    assert first["step"] == "existing"
    saved = onboarding.change_state(config, {"status": "complete", "step": "complete"}, 0)
    assert onboarding.read_state(config) == saved
    moved = onboarding.read_state(config, installation="another-install")
    assert moved["step"] == "existing"


def test_tour_dismissal_and_quest_completion_are_separate(tmp_path: Path) -> None:
    config = config_at(tmp_path)
    onboarding.read_state(config)
    saved = onboarding.change_state(
        config,
        {
            "tour_status": "deferred",
            "tour_step": "splits",
            "dismissed": ["voice"],
            "completed": ["project"],
            "hidden": True,
        },
        0,
    )
    assert onboarding.read_state(config_at(tmp_path)) == saved
    restored = onboarding.change_state(config, {"hidden": False, "dismissed": []}, 1)
    assert restored["completed"] == ["project"]
    assert restored["tour_step"] == "splits"
    assert restored["status"] == "active"


@pytest.mark.parametrize(
    "patch",
    [
        {"api_key": "secret"},
        {"draft": {"api_key": "secret"}},
        {"step": "lost"},
        {"hidden": 1},
        {"completed": ["unknown"]},
    ],
)
def test_invalid_or_credential_fields_never_reach_the_progress_record(
    tmp_path: Path, patch: dict[str, Any]
) -> None:
    config = config_at(tmp_path)
    onboarding.read_state(config)
    before = (tmp_path / "onboarding.json").read_bytes()
    with pytest.raises(ValueError):
        onboarding.change_state(config, patch, 0)
    assert (tmp_path / "onboarding.json").read_bytes() == before


def test_corrupt_progress_is_preserved_and_recoverable(tmp_path: Path) -> None:
    original = b'{"step":broken'
    (tmp_path / "onboarding.json").write_bytes(original)
    state = onboarding.read_state(config_at(tmp_path))
    assert state["step"] == "existing"
    assert next(tmp_path.glob("onboarding.invalid-*.json")).read_bytes() == original


async def test_fresh_preferences_back_up_and_keep_projects_accounts_and_connection(
    tmp_path: Path,
) -> None:
    config = config_at(tmp_path)
    update_config(config, {"theme": "tokyo-night", "automation_enabled": True})
    config.port = 9321
    (tmp_path / "provider-accounts.json").write_text("account sentinel")
    (tmp_path / "mux.db").write_text("history sentinel")
    before = config.config_path.read_bytes() if config.config_path else b""
    onboarding.read_state(config)
    request = Request(config, {"revision": 0, "action": "fresh"})
    response = await routes.patch_onboarding(cast(Any, request))
    assert response.status == 200
    payload = json.loads(response.text or "")
    assert (Path(payload["backup"]) / "config.toml").read_bytes() == before
    assert (tmp_path / "provider-accounts.json").read_text() == "account sentinel"
    assert (tmp_path / "mux.db").read_text() == "history sentinel"
    assert config.port == 9321
    assert config.automation_enabled is False
    assert payload["step"] == "experience"
    assert (tmp_path / "keybindings.json").is_file()


async def test_stale_fresh_request_cannot_reset_preferences(tmp_path: Path) -> None:
    config = config_at(tmp_path)
    onboarding.read_state(config)
    onboarding.change_state(config, {"step": "projects"}, 0)
    request = Request(config, {"revision": 0, "action": "fresh"})
    response = await routes.patch_onboarding(cast(Any, request))
    assert response.status == 409
    assert not (tmp_path / "setup-backups").exists()


def test_tiers_preserve_unmanaged_defaults_and_include_deterministic_layer() -> None:
    changes = tier_changes(
        "automations", project_defaults={"land_queue": True}, global_allow={"land_queue": False}
    )
    assert changes["automation_project_defaults"]["land_queue"] is True
    assert all(
        changes["automation_project_defaults"][item] for item in RECOMMENDED_PROJECT_AUTOMATIONS
    )
    deterministic = tier_changes("deterministic")
    assert all(
        deterministic["automation_project_defaults"][item]
        for item in RECOMMENDED_PROJECT_AUTOMATIONS
    )
    assert not any(
        deterministic["automation_project_defaults"][item] for item in LLM_PROJECT_AUTOMATIONS
    )


async def test_automation_tier_refuses_missing_provider_before_any_write(tmp_path: Path) -> None:
    config = config_at(tmp_path)
    request = Request(config, {"tier": "automations"})
    request.app[keys.LLM_READY] = AsyncMock(
        return_value=LlmReadiness(False, "openrouter", "no_key", "No key configured.")
    )
    response = await settings.apply_experience_tier(cast(Any, request))
    assert response.status == 409
    assert not config.automation_enabled
    assert not config.automation_project_defaults


async def test_project_discovery_orders_activity_deduplicates_and_keeps_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from swe_mux.git_projects import ProjectIdentity

    root = tmp_path / "repo"
    root.mkdir()
    nested = root / "src"
    nested.mkdir()
    monkeypatch.setattr(
        routes,
        "resolve_project",
        AsyncMock(return_value=ProjectIdentity("repo", "repo", str(root), "cwd")),
    )
    monkeypatch.setattr(routes, "_git", AsyncMock(return_value=None))
    items = [
        ExternalTranscript("claude", "one", str(root), 10, None),
        ExternalTranscript("codex", "two", str(nested), 20, None),
        ExternalTranscript("claude", "three", str(tmp_path / "missing"), 30, None),
    ]
    found = await routes.project_candidates(items)
    assert len(found) == 2
    assert found[0]["available"] is False
    assert found[1]["sessions"] == 2
    assert found[1]["harnesses"] == ["claude", "codex"]


def test_new_user_profile_is_local_and_separate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    config, _ = load_daemon_config(["--new-user-profile", "test-one", "--port", "19321"])
    assert config.data_dir == tmp_path / ".mux-test-profiles" / "test-one"
    assert config.port == 19321
    assert not config.tailnet_enabled
    assert not config.wsl_bridge_enabled


@pytest.mark.parametrize("name", ["../real", "a/b", "", "a" * 65])
def test_profile_names_cannot_escape_the_test_root(name: str) -> None:
    with pytest.raises(SystemExit):
        load_daemon_config(["--new-user-profile", name])


async def test_model_approval_proves_roles_and_is_revoked_by_a_model_change(tmp_path: Path) -> None:
    config = config_at(tmp_path)
    config.openrouter_cheap_model = "cheap"
    config.openrouter_standard_model = "standard"
    request = Request(config, {})
    request.app[keys.LLM_READY] = AsyncMock(
        return_value=LlmReadiness(True, "openrouter", "ready", "Ready")
    )
    request.app[keys.SECRET_STORE] = SimpleNamespace(get=lambda _: "test-secret")
    provider = SimpleNamespace(
        complete_json=AsyncMock(return_value=SimpleNamespace(value={"ok": True}, cost_usd=0.001)),
        complete_tools=AsyncMock(
            return_value=SimpleNamespace(
                tool_calls=[{"function": {"name": "setup_check", "arguments": '{"ok":true}'}}],
                cost_usd=0.001,
            )
        ),
    )
    request.app[keys.OPENROUTER] = provider
    response = await routes.verify_models(cast(Any, request))
    assert response.status == 200
    assert provider.complete_json.await_count == 3
    assert provider.complete_tools.await_count == 1
    assert model_setup.verified(config, resolve_endpoint(config), "test-secret")
    record = (tmp_path / "model-setup-verification.json").read_text()
    assert "test-secret" not in record
    config.openrouter_standard_model = "changed"
    assert not model_setup.verified(config, resolve_endpoint(config), "test-secret")
    assert not config.automation_enabled


async def test_bad_tool_arguments_cannot_approve_automation_models(tmp_path: Path) -> None:
    config = config_at(tmp_path)
    config.openrouter_cheap_model = config.openrouter_standard_model = "model"
    request = Request(config, {})
    request.app[keys.LLM_READY] = AsyncMock(
        return_value=LlmReadiness(True, "openrouter", "ready", "Ready")
    )
    request.app[keys.SECRET_STORE] = SimpleNamespace(get=lambda _: "test-secret")
    request.app[keys.OPENROUTER] = SimpleNamespace(
        complete_json=AsyncMock(return_value=SimpleNamespace(value={"ok": True}, cost_usd=0)),
        complete_tools=AsyncMock(
            return_value=SimpleNamespace(
                tool_calls=[{"function": {"name": "setup_check", "arguments": "not-json"}}],
                cost_usd=0,
            )
        ),
    )
    response = await routes.verify_models(cast(Any, request))
    assert response.status == 422
    assert not (tmp_path / "model-setup-verification.json").exists()
