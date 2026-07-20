from __future__ import annotations

import json
from pathlib import Path
from types import MethodType
from typing import Any

import pytest

from swe_mux.config import Config
from swe_mux.event_bus import EventBus
from swe_mux.provider_accounts import ProviderAccountError, ProviderAccountManager
from swe_mux.server import create_app


def claude_auth(token: str, email: str) -> dict[str, Any]:
    return {
        "claudeAiOauth": {
            "accessToken": token,
            "refreshToken": f"refresh-{token}",
            "email": email,
        }
    }


def identityless_claude_auth(token: str) -> dict[str, Any]:
    return {
        "claudeAiOauth": {
            "accessToken": token,
            "refreshToken": f"refresh-{token}",
            "expiresAt": 1_900_000_000_000,
            "subscriptionType": "max",
        }
    }


async def no_status(self: ProviderAccountManager, provider: str) -> dict[str, str | None]:
    return {"email": None, "provider_account_id": None, "organization": None}


async def fake_refresh(
    self: ProviderAccountManager, account_id: str | None = None
) -> dict[str, Any]:
    return self.snapshot()


@pytest.mark.asyncio
async def test_capture_and_switch_copy_auth_only_and_keep_shared_config(tmp_path: Path) -> None:
    home = tmp_path / "home"
    system_auth = home / ".claude" / ".credentials.json"
    config = home / ".claude" / "settings.json"
    system_auth.parent.mkdir(parents=True)
    config.write_text('{"theme":"shared"}', encoding="utf-8")
    manager = ProviderAccountManager(tmp_path / "mux", EventBus(), home=home)
    manager._status_identity = MethodType(no_status, manager)  # type: ignore[method-assign]
    manager.refresh = MethodType(fake_refresh, manager)  # type: ignore[method-assign]

    first = claude_auth("one", "one@example.com")
    system_auth.write_text(json.dumps(first), encoding="utf-8")
    snapshot = await manager.capture_current("claude", label="personal")
    first_id = snapshot["selected"]["claude"]

    second = claude_auth("two", "two@example.com")
    system_auth.write_text(json.dumps(second), encoding="utf-8")
    snapshot = await manager.capture_current("claude", label="work")
    assert len(snapshot["accounts"]) == 2

    await manager.select("claude", first_id)

    assert json.loads(system_auth.read_text(encoding="utf-8")) == first
    assert config.read_text(encoding="utf-8") == '{"theme":"shared"}'
    assert "auth_digest" not in manager.snapshot()["accounts"][0]
    assert "path" not in json.dumps(manager.snapshot())


@pytest.mark.asyncio
async def test_capture_deduplicates_provider_identity(tmp_path: Path) -> None:
    home = tmp_path / "home"
    auth_path = home / ".codex" / "auth.json"
    auth_path.parent.mkdir(parents=True)
    manager = ProviderAccountManager(tmp_path / "mux", EventBus(), home=home)
    manager.refresh = MethodType(fake_refresh, manager)  # type: ignore[method-assign]
    first = {"tokens": {"access_token": "first", "account_id": "account-1"}}
    second = {"tokens": {"access_token": "rotated", "account_id": "account-1"}}

    auth_path.write_text(json.dumps(first), encoding="utf-8")
    await manager.capture_current("codex", label="before")
    auth_path.write_text(json.dumps(second), encoding="utf-8")
    snapshot = await manager.capture_current("codex", label="after")

    assert len(snapshot["accounts"]) == 1
    assert snapshot["accounts"][0]["label"] == "after"


@pytest.mark.asyncio
async def test_startup_follows_system_auth_instead_of_remembered_selection(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    system_auth = home / ".claude" / ".credentials.json"
    system_auth.parent.mkdir(parents=True)
    data_dir = tmp_path / "mux"
    manager = ProviderAccountManager(data_dir, EventBus(), home=home)
    manager._status_identity = MethodType(no_status, manager)  # type: ignore[method-assign]
    manager.refresh = MethodType(fake_refresh, manager)  # type: ignore[method-assign]

    first = claude_auth("one", "one@example.com")
    system_auth.write_text(json.dumps(first), encoding="utf-8")
    first_id = (await manager.capture_current("claude", label="personal"))["selected"][
        "claude"
    ]
    system_auth.write_text(
        json.dumps(claude_auth("two", "two@example.com")), encoding="utf-8"
    )
    second_id = (await manager.capture_current("claude", label="work"))["selected"][
        "claude"
    ]
    assert second_id != first_id

    system_auth.write_text(json.dumps(first), encoding="utf-8")
    restarted = ProviderAccountManager(data_dir, EventBus(), home=home)
    snapshot = restarted.snapshot()

    assert snapshot["selected"]["claude"] == first_id
    assert snapshot["current"]["claude"]["state"] == "saved"
    assert snapshot["current"]["claude"]["account_id"] == first_id


@pytest.mark.asyncio
async def test_startup_reports_unmatched_system_auth_as_external(tmp_path: Path) -> None:
    home = tmp_path / "home"
    system_auth = home / ".claude" / ".credentials.json"
    system_auth.parent.mkdir(parents=True)
    data_dir = tmp_path / "mux"
    manager = ProviderAccountManager(data_dir, EventBus(), home=home)
    manager._status_identity = MethodType(no_status, manager)  # type: ignore[method-assign]
    manager.refresh = MethodType(fake_refresh, manager)  # type: ignore[method-assign]
    system_auth.write_text(
        json.dumps(claude_auth("saved", "saved@example.com")), encoding="utf-8"
    )
    await manager.capture_current("claude", label="saved")

    external = json.dumps(claude_auth("external", "external@example.com")).encode()
    system_auth.write_bytes(external)
    restarted = ProviderAccountManager(data_dir, EventBus(), home=home)
    snapshot = restarted.snapshot()

    assert system_auth.read_bytes() == external
    assert snapshot["selected"]["claude"] is None
    assert snapshot["current"]["claude"] == {
        "state": "external",
        "account_id": None,
        "email": "external@example.com",
        "provider_account_id": None,
        "organization": None,
    }


@pytest.mark.asyncio
async def test_startup_clears_selection_for_missing_or_unreadable_auth(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    system_auth = home / ".claude" / ".credentials.json"
    system_auth.parent.mkdir(parents=True)
    data_dir = tmp_path / "mux"
    manager = ProviderAccountManager(data_dir, EventBus(), home=home)
    manager._status_identity = MethodType(no_status, manager)  # type: ignore[method-assign]
    manager.refresh = MethodType(fake_refresh, manager)  # type: ignore[method-assign]
    system_auth.write_text(
        json.dumps(claude_auth("saved", "saved@example.com")), encoding="utf-8"
    )
    await manager.capture_current("claude")

    system_auth.unlink()
    signed_out = ProviderAccountManager(data_dir, EventBus(), home=home).snapshot()
    assert signed_out["selected"]["claude"] is None
    assert signed_out["current"]["claude"]["state"] == "signed_out"

    system_auth.write_text("not-json", encoding="utf-8")
    unreadable = ProviderAccountManager(data_dir, EventBus(), home=home).snapshot()
    assert unreadable["selected"]["claude"] is None
    assert unreadable["current"]["claude"]["state"] == "unreadable"
    assert system_auth.read_text(encoding="utf-8") == "not-json"


@pytest.mark.asyncio
async def test_startup_syncs_rotated_credentials_for_matching_identity(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    system_auth = home / ".codex" / "auth.json"
    system_auth.parent.mkdir(parents=True)
    data_dir = tmp_path / "mux"
    manager = ProviderAccountManager(data_dir, EventBus(), home=home)
    manager.refresh = MethodType(fake_refresh, manager)  # type: ignore[method-assign]
    system_auth.write_text(
        json.dumps({"tokens": {"access_token": "first", "account_id": "account-1"}}),
        encoding="utf-8",
    )
    account_id = (await manager.capture_current("codex"))["selected"]["codex"]

    rotated = json.dumps(
        {"tokens": {"access_token": "rotated", "account_id": "account-1"}}
    ).encode()
    system_auth.write_bytes(rotated)
    restarted = ProviderAccountManager(data_dir, EventBus(), home=home)

    assert restarted.snapshot()["selected"]["codex"] == account_id
    assert system_auth.read_bytes() == rotated
    assert restarted._managed_auth_path("codex", account_id).read_bytes() == rotated


@pytest.mark.asyncio
async def test_startup_uses_claude_status_to_match_identityless_rotated_auth(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    system_auth = home / ".claude" / ".credentials.json"
    system_auth.parent.mkdir(parents=True)
    data_dir = tmp_path / "mux"
    manager = ProviderAccountManager(data_dir, EventBus(), home=home)
    manager._status_identity = MethodType(no_status, manager)  # type: ignore[method-assign]
    manager.refresh = MethodType(fake_refresh, manager)  # type: ignore[method-assign]
    system_auth.write_text(
        json.dumps(claude_auth("saved", "adamtbandel@gmail.com")), encoding="utf-8"
    )
    account_id = (await manager.capture_current("claude"))["selected"]["claude"]

    rotated = json.dumps(identityless_claude_auth("rotated")).encode()
    system_auth.write_bytes(rotated)
    restarted = ProviderAccountManager(data_dir, EventBus(), home=home)
    assert restarted.snapshot()["current"]["claude"]["state"] == "external"
    assert restarted.snapshot()["current"]["claude"]["email"] is None

    async def adam_status(
        self: ProviderAccountManager, provider: str
    ) -> dict[str, str | None]:
        assert provider == "claude"
        return {
            "email": "adamtbandel@gmail.com",
            "provider_account_id": None,
            "organization": None,
        }

    restarted._status_identity = MethodType(adam_status, restarted)  # type: ignore[method-assign]
    snapshot = await restarted.reconcile_startup()

    assert system_auth.read_bytes() == rotated
    assert restarted._managed_auth_path("claude", account_id).read_bytes() == rotated
    assert snapshot["selected"]["claude"] == account_id
    assert snapshot["current"]["claude"]["state"] == "saved"
    assert snapshot["current"]["claude"]["email"] == "adamtbandel@gmail.com"


@pytest.mark.asyncio
async def test_refresh_reconciles_identityless_external_login_to_saved_account(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    system_auth = home / ".claude" / ".credentials.json"
    system_auth.parent.mkdir(parents=True)
    manager = ProviderAccountManager(tmp_path / "mux", EventBus(), home=home)
    manager._status_identity = MethodType(no_status, manager)  # type: ignore[method-assign]
    system_auth.write_text(
        json.dumps(claude_auth("saved", "saved@example.com")), encoding="utf-8"
    )
    account_id = (await manager.capture_current("claude"))["selected"]["claude"]

    rotated = json.dumps(identityless_claude_auth("rotated")).encode()
    system_auth.write_bytes(rotated)

    async def saved_status(
        self: ProviderAccountManager, provider: str
    ) -> dict[str, str | None]:
        assert provider == "claude"
        return {
            "email": "saved@example.com",
            "provider_account_id": None,
            "organization": None,
        }

    async def skip_quota(self: ProviderAccountManager, account: dict[str, Any]) -> None:
        return None

    manager._status_identity = MethodType(saved_status, manager)  # type: ignore[method-assign]
    manager._refresh_one = MethodType(skip_quota, manager)  # type: ignore[method-assign]
    snapshot = await manager.refresh()

    assert snapshot["selected"]["claude"] == account_id
    assert snapshot["current"]["claude"]["state"] == "saved"
    assert manager._managed_auth_path("claude", account_id).read_bytes() == rotated


@pytest.mark.asyncio
async def test_startup_status_identity_is_retried_if_live_auth_changes(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    system_auth = home / ".claude" / ".credentials.json"
    system_auth.parent.mkdir(parents=True)
    data_dir = tmp_path / "mux"
    manager = ProviderAccountManager(data_dir, EventBus(), home=home)
    manager._status_identity = MethodType(no_status, manager)  # type: ignore[method-assign]
    manager.refresh = MethodType(fake_refresh, manager)  # type: ignore[method-assign]
    system_auth.write_text(
        json.dumps(claude_auth("saved", "adamtbandel@gmail.com")), encoding="utf-8"
    )
    await manager.capture_current("claude")

    system_auth.write_text(json.dumps(identityless_claude_auth("first")), encoding="utf-8")
    restarted = ProviderAccountManager(data_dir, EventBus(), home=home)
    calls = 0

    async def changing_status(
        self: ProviderAccountManager, provider: str
    ) -> dict[str, str | None]:
        nonlocal calls
        calls += 1
        if calls == 1:
            system_auth.write_text(
                json.dumps(identityless_claude_auth("second")), encoding="utf-8"
            )
            email = "adamtbandel@gmail.com"
        else:
            email = "someone-else@example.com"
        return {"email": email, "provider_account_id": None, "organization": None}

    restarted._status_identity = MethodType(  # type: ignore[method-assign]
        changing_status, restarted
    )
    snapshot = await restarted.reconcile_startup()

    assert calls == 2
    assert snapshot["selected"]["claude"] is None
    assert snapshot["current"]["claude"]["state"] == "external"
    assert snapshot["current"]["claude"]["email"] == "someone-else@example.com"


@pytest.mark.asyncio
async def test_refresh_rotation_does_not_overwrite_changed_system_login(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    system_auth = home / ".claude" / ".credentials.json"
    system_auth.parent.mkdir(parents=True)
    manager = ProviderAccountManager(tmp_path / "mux", EventBus(), home=home)
    manager._status_identity = MethodType(no_status, manager)  # type: ignore[method-assign]
    manager.refresh = MethodType(fake_refresh, manager)  # type: ignore[method-assign]
    system_auth.write_text(
        json.dumps(claude_auth("saved", "saved@example.com")), encoding="utf-8"
    )
    account_id = (await manager.capture_current("claude"))["selected"]["claude"]
    account = manager._account(account_id)

    concurrently_rotated = json.dumps(
        claude_auth("newer-system-token", "saved@example.com")
    ).encode()
    system_auth.write_bytes(concurrently_rotated)

    async def rotated_refresh(
        self: ProviderAccountManager, auth: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        return {"session": None, "weekly": None}, claude_auth(
            "rotated", "saved@example.com"
        )

    manager._fetch_claude = MethodType(rotated_refresh, manager)  # type: ignore[method-assign]
    await manager._refresh_one(account)

    assert system_auth.read_bytes() == concurrently_rotated
    assert manager.snapshot()["selected"]["claude"] == account_id
    assert manager.snapshot()["current"]["claude"]["state"] == "saved"


@pytest.mark.asyncio
async def test_quota_failure_retains_last_success_as_stale(tmp_path: Path) -> None:
    home = tmp_path / "home"
    auth_path = home / ".claude" / ".credentials.json"
    auth_path.parent.mkdir(parents=True)
    auth_path.write_text(json.dumps(claude_auth("one", "one@example.com")), encoding="utf-8")
    manager = ProviderAccountManager(tmp_path / "mux", EventBus(), home=home)
    manager._status_identity = MethodType(no_status, manager)  # type: ignore[method-assign]
    manager.refresh = MethodType(fake_refresh, manager)  # type: ignore[method-assign]
    snapshot = await manager.capture_current("claude")
    account = manager._account(snapshot["selected"]["claude"])

    async def success(
        self: ProviderAccountManager, auth: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        return {"session": {"used_percent": 25}, "weekly": {"used_percent": 50}}, None

    manager._fetch_claude = MethodType(success, manager)  # type: ignore[method-assign]
    await manager._refresh_one(account)

    async def failure(
        self: ProviderAccountManager, auth: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        raise ProviderAccountError("temporary provider failure")

    manager._fetch_claude = MethodType(failure, manager)  # type: ignore[method-assign]
    await manager._refresh_one(account)
    quota = manager.snapshot()["accounts"][0]["quota"]

    assert quota["status"] == "stale"
    assert quota["session"]["used_percent"] == 25
    assert quota["error"] == "temporary provider failure"


@pytest.mark.asyncio
async def test_codex_backend_quota_mapping(tmp_path: Path) -> None:
    manager = ProviderAccountManager(tmp_path, EventBus(), home=tmp_path / "home")

    async def request(
        self: ProviderAccountManager,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        assert headers and headers["ChatGPT-Account-Id"] == "account-1"
        return 200, {
            "plan_type": "plus",
            "rate_limit": {
                "primary_window": {
                    "used_percent": 12,
                    "limit_window_seconds": 18_000,
                    "reset_at": 1_800_000_000,
                },
                "secondary_window": {"used_percent": 34},
            },
        }

    manager._json_request = MethodType(request, manager)  # type: ignore[method-assign]
    quota, updated = await manager._fetch_codex(
        {"tokens": {"access_token": "secret", "account_id": "account-1"}}, "id"
    )

    assert quota["session"]["used_percent"] == 12
    assert quota["session"]["window_minutes"] == 300
    assert quota["weekly"]["used_percent"] == 34
    assert quota["plan"] == "plus"
    assert updated is None


async def test_claude_quota_maps_fable_scoped_weekly(tmp_path: Path) -> None:
    manager = ProviderAccountManager(tmp_path, EventBus(), home=tmp_path / "home")

    async def request(
        self: ProviderAccountManager,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        return 200, {
            "five_hour": {"utilization": 29, "resets_at": "2026-07-20T17:49:59+00:00"},
            "seven_day": {"utilization": 90, "resets_at": "2026-07-23T06:59:59+00:00"},
            "seven_day_opus": None,
            "limits": [
                {"kind": "session", "group": "session", "percent": 29},
                {"kind": "weekly_all", "group": "weekly", "percent": 90, "scope": None},
                {
                    "kind": "weekly_scoped",
                    "group": "weekly",
                    "percent": 80,
                    "resets_at": "2026-07-23T06:59:59+00:00",
                    "scope": {"model": {"id": None, "display_name": "Fable"}},
                },
            ],
        }

    manager._json_request = MethodType(request, manager)  # type: ignore[method-assign]
    quota, updated = await manager._fetch_claude({"claudeAiOauth": {"accessToken": "secret"}})

    assert quota["session"]["used_percent"] == 29
    assert quota["weekly"]["used_percent"] == 90
    assert quota["fable"]["used_percent"] == 80
    assert quota["fable"]["window_minutes"] == 10080
    assert quota["fable"]["resets_at"] is not None
    assert updated is None


async def test_claude_quota_fable_absent_is_none(tmp_path: Path) -> None:
    manager = ProviderAccountManager(tmp_path, EventBus(), home=tmp_path / "home")

    async def request(
        self: ProviderAccountManager,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        return 200, {
            "five_hour": {"utilization": 10, "resets_at": "2026-07-20T17:49:59+00:00"},
            "seven_day": {"utilization": 20, "resets_at": "2026-07-23T06:59:59+00:00"},
            "limits": [{"kind": "weekly_all", "group": "weekly", "percent": 20, "scope": None}],
        }

    manager._json_request = MethodType(request, manager)  # type: ignore[method-assign]
    quota, _ = await manager._fetch_claude({"claudeAiOauth": {"accessToken": "secret"}})

    assert quota["fable"] is None


def test_provider_account_routes_are_registered(tmp_path: Path) -> None:
    app = create_app(Config(data_dir=tmp_path))
    routes = {(route.method, route.resource.canonical) for route in app.router.routes()}

    assert ("GET", "/api/provider-accounts") in routes
    assert ("POST", "/api/provider-accounts/{provider}/capture") in routes
    assert ("POST", "/api/provider-accounts/{provider}/{account_id}/select") in routes
    assert ("DELETE", "/api/provider-accounts/{provider}/{account_id}") in routes


def test_account_commands_resolve_npm_batch_shims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = ProviderAccountManager(tmp_path, EventBus(), codex_exe="codex.exe")
    monkeypatch.setattr(
        "swe_mux.provider_accounts.shutil.which",
        lambda command: r"C:\npm\codex.cmd" if command == "codex" else None,
    )
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")

    assert manager._spawn_command("codex", ["login"]) == [
        r"C:\Windows\System32\cmd.exe",
        "/d",
        "/c",
        r"C:\npm\codex.cmd",
        "login",
    ]
