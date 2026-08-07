from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any

import pytest

from swe_mux.background_tasks import background
from swe_mux.config import Config
from swe_mux.event_bus import EventBus
from swe_mux.provider_accounts import (
    SELECTION_GUARD_LOOP,
    ProviderAccountError,
    ProviderAccountManager,
)
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


async def no_status(self: ProviderAccountManager, provider: str) -> dict[str, Any]:
    return {"email": None, "provider_account_id": None, "organization": None, "source": None}


async def fake_refresh(
    self: ProviderAccountManager, account_id: str | None = None, **kwargs: Any
) -> dict[str, Any]:
    return self.snapshot()


def token_identity(email: str, account_uuid: str) -> dict[str, Any]:
    return {
        "email": email,
        "provider_account_id": account_uuid,
        "organization": "Test Org",
        "source": "token",
    }


def oauth_account_block(uuid_value: str, email: str) -> dict[str, Any]:
    """A ~/.claude.json `oauthAccount` block as the CLI writes it."""
    return {
        "accountUuid": uuid_value,
        "emailAddress": email,
        "organizationUuid": f"org-{uuid_value}",
        "billingType": "stripe_subscription",
        "profileFetchedAt": 1_700_000_000_000,
    }


def offline(
    manager: ProviderAccountManager,
    identity: dict[str, Any] | Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
) -> ProviderAccountManager:
    """Keep identity verification deterministic and off the network."""

    async def verify(
        self: ProviderAccountManager, provider: str, auth: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        if provider == "codex":
            resolved = self._identity("codex", auth)
            return (resolved if resolved.get("provider_account_id") else None), None
        resolved = identity(auth) if callable(identity) else identity
        return (resolved if isinstance(resolved, dict) else None), None

    manager._verify_token_identity = MethodType(verify, manager)  # type: ignore[method-assign]
    return manager


@pytest.mark.asyncio
async def test_capture_and_switch_copy_auth_only_and_keep_shared_config(tmp_path: Path) -> None:
    home = tmp_path / "home"
    system_auth = home / ".claude" / ".credentials.json"
    config = home / ".claude" / "settings.json"
    system_auth.parent.mkdir(parents=True)
    config.write_text('{"theme":"shared"}', encoding="utf-8")
    manager = offline(ProviderAccountManager(tmp_path / "mux", EventBus(), home=home))
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
    manager = offline(ProviderAccountManager(tmp_path / "mux", EventBus(), home=home))
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
    manager = offline(ProviderAccountManager(data_dir, EventBus(), home=home))
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
    manager = offline(ProviderAccountManager(data_dir, EventBus(), home=home))
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
        "identity_source": "file",
        "match_hint": None,
    }


@pytest.mark.asyncio
async def test_startup_clears_selection_for_missing_or_unreadable_auth(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    system_auth = home / ".claude" / ".credentials.json"
    system_auth.parent.mkdir(parents=True)
    data_dir = tmp_path / "mux"
    manager = offline(ProviderAccountManager(data_dir, EventBus(), home=home))
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
async def test_claude_cli_status_email_never_relinks_rotated_credentials(
    tmp_path: Path,
) -> None:
    """The regression that mirrored one account's usage onto another.

    `claude auth status` reports machine-global cached profile state, not the
    credential, so it can name a different account than the token actually in
    `.credentials.json`. Acting on it copied the live token into the wrong saved
    slot. It may now only offer a relink hint.
    """
    home = tmp_path / "home"
    system_auth = home / ".claude" / ".credentials.json"
    system_auth.parent.mkdir(parents=True)
    data_dir = tmp_path / "mux"
    manager = offline(ProviderAccountManager(data_dir, EventBus(), home=home))
    manager._status_identity = MethodType(no_status, manager)  # type: ignore[method-assign]
    manager.refresh = MethodType(fake_refresh, manager)  # type: ignore[method-assign]
    saved = json.dumps(claude_auth("saved", "first@example.com")).encode()
    system_auth.write_bytes(saved)
    account_id = (await manager.capture_current("claude"))["selected"]["claude"]

    # A different account's login lands in the shared credential file while the
    # CLI's cached profile still names the saved one.
    rotated = json.dumps(identityless_claude_auth("other-account-token")).encode()
    system_auth.write_bytes(rotated)
    restarted = offline(ProviderAccountManager(data_dir, EventBus(), home=home))

    async def stale_status(self: ProviderAccountManager, provider: str) -> dict[str, Any]:
        return {
            "email": "first@example.com",
            "provider_account_id": None,
            "organization": None,
            "source": "cli",
        }

    restarted._status_identity = MethodType(stale_status, restarted)  # type: ignore[method-assign]
    snapshot = await restarted.reconcile_startup()

    assert snapshot["current"]["claude"]["state"] == "external"
    assert snapshot["selected"]["claude"] is None
    assert snapshot["current"]["claude"]["match_hint"] == {
        "account_id": account_id,
        "label": "first@example.com",
        "reason": "email",
    }
    assert restarted._managed_auth_path("claude", account_id).read_bytes() == saved


@pytest.mark.asyncio
async def test_token_verified_identity_relinks_an_externally_rotated_login(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    system_auth = home / ".claude" / ".credentials.json"
    system_auth.parent.mkdir(parents=True)
    data_dir = tmp_path / "mux"
    identity = token_identity("saved@example.com", "acct-1")
    manager = offline(ProviderAccountManager(data_dir, EventBus(), home=home), identity)
    manager._status_identity = MethodType(no_status, manager)  # type: ignore[method-assign]
    manager.refresh = MethodType(fake_refresh, manager)  # type: ignore[method-assign]
    system_auth.write_text(
        json.dumps(claude_auth("saved", "saved@example.com")), encoding="utf-8"
    )
    account_id = (await manager.capture_current("claude"))["selected"]["claude"]

    rotated = json.dumps(identityless_claude_auth("rotated")).encode()
    system_auth.write_bytes(rotated)
    restarted = offline(ProviderAccountManager(data_dir, EventBus(), home=home), identity)
    restarted._status_identity = MethodType(no_status, restarted)  # type: ignore[method-assign]
    restarted.refresh = MethodType(fake_refresh, restarted)  # type: ignore[method-assign]
    snapshot = await restarted.reconcile_startup()

    assert snapshot["selected"]["claude"] == account_id
    assert snapshot["current"]["claude"]["state"] == "saved"
    assert snapshot["current"]["claude"]["identity_source"] == "token"
    assert system_auth.read_bytes() == rotated
    assert restarted._managed_auth_path("claude", account_id).read_bytes() == rotated


@pytest.mark.asyncio
async def test_identity_probe_is_retried_if_live_auth_changes_during_the_probe(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    system_auth = home / ".claude" / ".credentials.json"
    system_auth.parent.mkdir(parents=True)
    data_dir = tmp_path / "mux"
    manager = offline(ProviderAccountManager(data_dir, EventBus(), home=home))
    manager._status_identity = MethodType(no_status, manager)  # type: ignore[method-assign]
    manager.refresh = MethodType(fake_refresh, manager)  # type: ignore[method-assign]
    system_auth.write_text(
        json.dumps(claude_auth("saved", "first@example.com")), encoding="utf-8"
    )
    await manager.capture_current("claude")

    system_auth.write_text(json.dumps(identityless_claude_auth("first")), encoding="utf-8")
    restarted = ProviderAccountManager(data_dir, EventBus(), home=home)
    restarted._status_identity = MethodType(no_status, restarted)  # type: ignore[method-assign]
    restarted.refresh = MethodType(fake_refresh, restarted)  # type: ignore[method-assign]
    calls = 0

    async def changing_verify(
        self: ProviderAccountManager, provider: str, auth: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        nonlocal calls
        calls += 1
        if calls == 1:
            system_auth.write_text(
                json.dumps(identityless_claude_auth("second")), encoding="utf-8"
            )
            return token_identity("first@example.com", "acct-1"), None
        return token_identity("someone-else@example.com", "acct-2"), None

    restarted._verify_token_identity = MethodType(  # type: ignore[method-assign]
        changing_verify, restarted
    )
    snapshot = await restarted.reconcile_startup()

    assert calls == 2
    # The stale reading is discarded rather than cached against the new digest.
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
    identity = token_identity("saved@example.com", "acct-1")
    manager = offline(ProviderAccountManager(tmp_path / "mux", EventBus(), home=home), identity)
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
        self: ProviderAccountManager, auth: dict[str, Any], *, allow_refresh: bool = True
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        return {"session": None, "weekly": None}, claude_auth(
            "rotated", "saved@example.com"
        )

    manager._fetch_claude = MethodType(rotated_refresh, manager)  # type: ignore[method-assign]
    await manager._refresh_one(account)

    assert system_auth.read_bytes() == concurrently_rotated
    assert manager._account(account_id)["provider_account_id"] == "acct-1"


@pytest.mark.asyncio
async def test_quota_failure_retains_last_success_as_stale(tmp_path: Path) -> None:
    home = tmp_path / "home"
    auth_path = home / ".claude" / ".credentials.json"
    auth_path.parent.mkdir(parents=True)
    auth_path.write_text(json.dumps(claude_auth("one", "one@example.com")), encoding="utf-8")
    manager = offline(ProviderAccountManager(tmp_path / "mux", EventBus(), home=home))
    manager._status_identity = MethodType(no_status, manager)  # type: ignore[method-assign]
    manager.refresh = MethodType(fake_refresh, manager)  # type: ignore[method-assign]
    snapshot = await manager.capture_current("claude")
    account = manager._account(snapshot["selected"]["claude"])

    async def success(
        self: ProviderAccountManager, auth: dict[str, Any], *, allow_refresh: bool = True
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        return {"session": {"used_percent": 25}, "weekly": {"used_percent": 50}}, None

    manager._fetch_claude = MethodType(success, manager)  # type: ignore[method-assign]
    await manager._refresh_one(account)

    async def failure(
        self: ProviderAccountManager, auth: dict[str, Any], *, allow_refresh: bool = True
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


async def test_codex_weekly_only_window_maps_to_weekly_not_session(tmp_path: Path) -> None:
    """Codex temporarily returns just a 7-day window in the primary slot; it must
    be classified as weekly (not shown in the 5h/session slot) by its duration."""
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
            "plan_type": "prolite",
            "rate_limit": {
                "primary_window": {
                    "used_percent": 14,
                    "limit_window_seconds": 604_800,
                    "reset_at": 1_785_126_884,
                },
                "secondary_window": None,
            },
        }

    manager._json_request = MethodType(request, manager)  # type: ignore[method-assign]
    quota, _ = await manager._fetch_codex(
        {"tokens": {"access_token": "secret", "account_id": "account-1"}}, "id"
    )

    assert quota["session"] is None
    assert quota["weekly"]["used_percent"] == 14
    assert quota["weekly"]["window_minutes"] == 10080
    assert quota["weekly"]["resets_at"] == 1_785_126_884


async def test_codex_reinstated_split_self_heals_by_duration(tmp_path: Path) -> None:
    """When Codex reinstates a 5h + weekly split — even with the windows swapped
    between the primary/secondary slots — each is routed by its real duration."""
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
            "plan_type": "pro",
            "rate_limit": {
                # Weekly in the primary slot, 5h in the secondary slot — position swapped.
                "primary_window": {"used_percent": 60, "limit_window_seconds": 604_800},
                "secondary_window": {"used_percent": 25, "limit_window_seconds": 18_000},
            },
        }

    manager._json_request = MethodType(request, manager)  # type: ignore[method-assign]
    quota, _ = await manager._fetch_codex(
        {"tokens": {"access_token": "secret", "account_id": "account-1"}}, "id"
    )

    assert quota["session"]["used_percent"] == 25
    assert quota["session"]["window_minutes"] == 300
    assert quota["weekly"]["used_percent"] == 60
    assert quota["weekly"]["window_minutes"] == 10080


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


@pytest.mark.asyncio
async def test_two_slots_holding_one_account_are_flagged_and_not_double_polled(
    tmp_path: Path,
) -> None:
    """The reported symptom: two saved accounts reporting identical usage.

    Once identity is token-derived the duplicate is detectable, so the second
    slot is marked instead of quietly polling the same account twice.
    """
    home = tmp_path / "home"
    system_auth = home / ".claude" / ".credentials.json"
    system_auth.parent.mkdir(parents=True)
    manager = offline(ProviderAccountManager(tmp_path / "mux", EventBus(), home=home))
    manager._status_identity = MethodType(no_status, manager)  # type: ignore[method-assign]
    manager.refresh = MethodType(fake_refresh, manager)  # type: ignore[method-assign]
    system_auth.write_text(json.dumps(claude_auth("one", "one@example.com")), encoding="utf-8")
    first = (await manager.capture_current("claude", label="first"))["selected"]["claude"]
    system_auth.write_text(json.dumps(claude_auth("two", "two@example.com")), encoding="utf-8")
    second = (await manager.capture_current("claude", label="second"))["selected"]["claude"]
    assert first != second

    # Both slots turn out to hold credentials for one and the same account.
    offline(manager, token_identity("one@example.com", "acct-1"))
    polled: list[str] = []

    async def record_poll(self: ProviderAccountManager, account: dict[str, Any]) -> None:
        polled.append(str(account["id"]))

    manager.refresh = ProviderAccountManager.refresh.__get__(manager)  # type: ignore[method-assign]
    manager._refresh_one = MethodType(record_poll, manager)  # type: ignore[method-assign]
    await manager.verify_identities()
    snapshot = await manager.refresh()

    conflicts = {
        account["id"]: account["conflict"]
        for account in snapshot["accounts"]
        if account["conflict"]
    }
    assert set(conflicts) == {first, second}
    assert {entry["primary_id"] for entry in conflicts.values()} == {second}
    assert polled == [second]
    quota = next(item for item in snapshot["accounts"] if item["id"] == first)["quota"]
    assert quota["status"] == "conflict"


@pytest.mark.asyncio
async def test_switching_proceeds_while_live_sessions_hold_the_current_login(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    system_auth = home / ".claude" / ".credentials.json"
    system_auth.parent.mkdir(parents=True)
    manager = offline(ProviderAccountManager(tmp_path / "mux", EventBus(), home=home))
    manager._status_identity = MethodType(no_status, manager)  # type: ignore[method-assign]
    manager.refresh = MethodType(fake_refresh, manager)  # type: ignore[method-assign]
    system_auth.write_text(json.dumps(claude_auth("one", "one@example.com")), encoding="utf-8")
    first = (await manager.capture_current("claude", label="first"))["selected"]["claude"]
    system_auth.write_text(json.dumps(claude_auth("two", "two@example.com")), encoding="utf-8")
    await manager.capture_current("claude", label="second")

    manager.sessions = SimpleNamespace(
        sessions={"s1": SimpleNamespace(record=SimpleNamespace(backend="claude", state="working"))}
    )

    # Live sessions re-read the shared credential file, so they follow the
    # switch; refusing it only ever cost the user a confirmation.
    snapshot = await manager.select("claude", first)
    assert json.loads(system_auth.read_text(encoding="utf-8"))["claudeAiOauth"]["accessToken"] == (
        "one"
    )
    assert snapshot["selected"]["claude"] == first
    assert manager._selection_guard["claude"][0] == first
    await background.stop(f"{SELECTION_GUARD_LOOP}-claude")


@pytest.mark.asyncio
async def test_selection_guard_restores_a_switch_a_live_session_rotated_back(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    system_auth = home / ".claude" / ".credentials.json"
    system_auth.parent.mkdir(parents=True)
    manager = offline(ProviderAccountManager(tmp_path / "mux", EventBus(), home=home))
    manager._status_identity = MethodType(no_status, manager)  # type: ignore[method-assign]
    manager.refresh = MethodType(fake_refresh, manager)  # type: ignore[method-assign]
    system_auth.write_text(json.dumps(claude_auth("one", "one@example.com")), encoding="utf-8")
    first = (await manager.capture_current("claude", label="first"))["selected"]["claude"]
    system_auth.write_text(json.dumps(claude_auth("two", "two@example.com")), encoding="utf-8")
    second = (await manager.capture_current("claude", label="second"))["selected"]["claude"]

    manager.sessions = SimpleNamespace(
        sessions={"s1": SimpleNamespace(record=SimpleNamespace(backend="claude", state="working"))}
    )
    await manager.select("claude", first)
    await background.stop(f"{SELECTION_GUARD_LOOP}-claude")

    # A refresh that was already in flight under the outgoing login lands after
    # the switch and writes that account's credentials straight back.
    system_auth.write_text(json.dumps(claude_auth("two", "two@example.com")), encoding="utf-8")
    await manager.reconcile_current()
    assert manager.snapshot()["selected"]["claude"] == second

    await manager._reassert_selection("claude")
    assert json.loads(system_auth.read_text(encoding="utf-8"))["claudeAiOauth"]["accessToken"] == (
        "one"
    )
    assert manager.snapshot()["selected"]["claude"] == first
    assert "selection_reasserted" in [entry["action"] for entry in manager.audit_entries()]


@pytest.mark.asyncio
async def test_selection_guard_leaves_an_unidentified_live_login_alone(
    tmp_path: Path,
) -> None:
    """An external rotation may hold a newer token than any saved snapshot."""
    home = tmp_path / "home"
    system_auth = home / ".claude" / ".credentials.json"
    system_auth.parent.mkdir(parents=True)
    manager = offline(ProviderAccountManager(tmp_path / "mux", EventBus(), home=home))
    manager._status_identity = MethodType(no_status, manager)  # type: ignore[method-assign]
    manager.refresh = MethodType(fake_refresh, manager)  # type: ignore[method-assign]
    system_auth.write_text(json.dumps(claude_auth("one", "one@example.com")), encoding="utf-8")
    first = (await manager.capture_current("claude", label="first"))["selected"]["claude"]

    manager.sessions = SimpleNamespace(
        sessions={"s1": SimpleNamespace(record=SimpleNamespace(backend="claude", state="working"))}
    )
    manager._selection_guard["claude"] = (first, time.monotonic() + 60)

    system_auth.write_text(json.dumps(identityless_claude_auth("rotated")), encoding="utf-8")
    await manager._reassert_selection("claude")
    assert json.loads(system_auth.read_text(encoding="utf-8"))["claudeAiOauth"]["accessToken"] == (
        "rotated"
    )


def cli_profile_manager(tmp_path: Path) -> ProviderAccountManager:
    """A manager whose verified identity is derived from the fake access token."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    identities = {
        "one": token_identity("one@example.com", "uuid-one"),
        "two": token_identity("two@example.com", "uuid-two"),
    }
    manager = offline(
        ProviderAccountManager(tmp_path / "mux", EventBus(), home=home),
        lambda auth: identities.get(str(auth["claudeAiOauth"]["accessToken"])),
    )
    manager._status_identity = MethodType(no_status, manager)  # type: ignore[method-assign]
    manager.refresh = MethodType(fake_refresh, manager)  # type: ignore[method-assign]
    return manager


@pytest.mark.asyncio
async def test_switch_restores_the_cli_cached_profile_block(tmp_path: Path) -> None:
    manager = cli_profile_manager(tmp_path)
    system_auth = tmp_path / "home" / ".claude" / ".credentials.json"
    config = tmp_path / "home" / ".claude.json"

    system_auth.write_text(json.dumps(claude_auth("one", "one@example.com")), encoding="utf-8")
    config.write_text(
        json.dumps(
            {
                "projects": {"a": 1},
                "oauthAccount": oauth_account_block("uuid-one", "one@example.com"),
            }
        ),
        encoding="utf-8",
    )
    first = (await manager.capture_current("claude", label="first"))["selected"]["claude"]
    snapshot_path = (
        tmp_path / "mux" / "provider-accounts" / "claude" / first / "oauth-account.json"
    )
    assert json.loads(snapshot_path.read_text(encoding="utf-8"))["accountUuid"] == "uuid-one"

    system_auth.write_text(json.dumps(claude_auth("two", "two@example.com")), encoding="utf-8")
    config.write_text(
        json.dumps(
            {
                "projects": {"a": 1},
                "oauthAccount": oauth_account_block("uuid-two", "two@example.com"),
            }
        ),
        encoding="utf-8",
    )
    await manager.capture_current("claude", label="second")

    await manager.select("claude", first)
    updated = json.loads(config.read_text(encoding="utf-8"))
    # /status reads this block, not the credential file: without the restore it
    # keeps naming the outgoing account for up to a day after a switch.
    assert updated["oauthAccount"] == oauth_account_block("uuid-one", "one@example.com")
    assert updated["projects"] == {"a": 1}
    assert "oauth_profile_restored" in [entry["action"] for entry in manager.audit_entries()]


@pytest.mark.asyncio
async def test_switch_without_profile_snapshot_writes_identity_and_forces_refetch(
    tmp_path: Path,
) -> None:
    manager = cli_profile_manager(tmp_path)
    system_auth = tmp_path / "home" / ".claude" / ".credentials.json"
    config = tmp_path / "home" / ".claude.json"

    # First login is captured while ~/.claude.json does not exist yet, so no
    # profile snapshot is taken for it.
    system_auth.write_text(json.dumps(claude_auth("one", "one@example.com")), encoding="utf-8")
    first = (await manager.capture_current("claude", label="first"))["selected"]["claude"]

    system_auth.write_text(json.dumps(claude_auth("two", "two@example.com")), encoding="utf-8")
    config.write_text(
        json.dumps({"oauthAccount": oauth_account_block("uuid-two", "two@example.com")}),
        encoding="utf-8",
    )
    await manager.capture_current("claude", label="second")

    await manager.select("claude", first)
    block = json.loads(config.read_text(encoding="utf-8"))["oauthAccount"]
    assert block == {
        "accountUuid": "uuid-one",
        "emailAddress": "one@example.com",
        "organizationName": "Test Org",
    }
    # No profileFetchedAt: the CLI's 24h freshness gate fails and it refetches
    # the real profile on the next session start.
    assert "profileFetchedAt" not in block


@pytest.mark.asyncio
async def test_profile_restore_never_touches_an_unparseable_cli_config(tmp_path: Path) -> None:
    manager = cli_profile_manager(tmp_path)
    system_auth = tmp_path / "home" / ".claude" / ".credentials.json"
    config = tmp_path / "home" / ".claude.json"

    system_auth.write_text(json.dumps(claude_auth("one", "one@example.com")), encoding="utf-8")
    first = (await manager.capture_current("claude", label="first"))["selected"]["claude"]
    system_auth.write_text(json.dumps(claude_auth("two", "two@example.com")), encoding="utf-8")
    await manager.capture_current("claude", label="second")

    config.write_text("{definitely not json", encoding="utf-8")
    await manager.select("claude", first)
    assert config.read_text(encoding="utf-8") == "{definitely not json"


@pytest.mark.asyncio
async def test_claude_token_rotation_defers_to_live_sessions(tmp_path: Path) -> None:
    manager = cli_profile_manager(tmp_path)
    system_auth = tmp_path / "home" / ".claude" / ".credentials.json"
    system_auth.write_text(json.dumps(claude_auth("one", "one@example.com")), encoding="utf-8")
    first = (await manager.capture_current("claude", label="first"))["selected"]["claude"]

    rotations: list[str] = []

    async def unauthorized(
        self: ProviderAccountManager,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        return 401, {}

    async def rotate(self: ProviderAccountManager, auth: dict[str, Any]) -> None:
        rotations.append(str(auth["claudeAiOauth"]["accessToken"]))
        return None

    manager._json_request = MethodType(unauthorized, manager)  # type: ignore[method-assign]
    manager._refresh_claude_auth = MethodType(rotate, manager)  # type: ignore[method-assign]

    # A live session runs under the selected account: its CLI owns the refresh
    # token's rotation, so the managed refresh must not race it.
    manager.sessions = SimpleNamespace(
        sessions={"s1": SimpleNamespace(record=SimpleNamespace(backend="claude", state="working"))}
    )
    await manager._refresh_one(manager._account(first))
    assert rotations == []
    quota = manager._manifest["quota"][first]
    assert "live session owns" in str(quota.get("error"))

    # No live sessions: the managed refresh may rotate freely.
    manager.sessions = None
    await manager._refresh_one(manager._account(first))
    assert rotations == ["one"]


@pytest.mark.asyncio
async def test_adopt_refuses_credentials_owned_by_another_saved_account(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    system_auth = home / ".claude" / ".credentials.json"
    system_auth.parent.mkdir(parents=True)
    manager = offline(
        ProviderAccountManager(tmp_path / "mux", EventBus(), home=home),
        lambda auth: token_identity(
            f"{auth['claudeAiOauth']['accessToken']}@example.com",
            f"acct-{auth['claudeAiOauth']['accessToken']}",
        ),
    )
    manager._status_identity = MethodType(no_status, manager)  # type: ignore[method-assign]
    manager.refresh = MethodType(fake_refresh, manager)  # type: ignore[method-assign]
    system_auth.write_text(json.dumps(claude_auth("one", "one@example.com")), encoding="utf-8")
    first = (await manager.capture_current("claude", label="first"))["selected"]["claude"]
    first_snapshot = manager._managed_auth_path("claude", first).read_bytes()
    system_auth.write_text(json.dumps(claude_auth("two", "two@example.com")), encoding="utf-8")
    await manager.capture_current("claude", label="second")

    with pytest.raises(ProviderAccountError, match="belongs to the saved account"):
        await manager.adopt("claude", first)
    assert manager._managed_auth_path("claude", first).read_bytes() == first_snapshot


@pytest.mark.asyncio
async def test_credential_writes_are_audited_and_the_previous_snapshot_is_kept(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    system_auth = home / ".claude" / ".credentials.json"
    system_auth.parent.mkdir(parents=True)
    identity = token_identity("one@example.com", "acct-1")
    manager = offline(ProviderAccountManager(tmp_path / "mux", EventBus(), home=home), identity)
    manager._status_identity = MethodType(no_status, manager)  # type: ignore[method-assign]
    manager.refresh = MethodType(fake_refresh, manager)  # type: ignore[method-assign]
    original = json.dumps(claude_auth("one", "one@example.com")).encode()
    system_auth.write_bytes(original)
    account_id = (await manager.capture_current("claude"))["selected"]["claude"]

    rotated = json.dumps(identityless_claude_auth("rotated")).encode()
    system_auth.write_bytes(rotated)
    await manager.reconcile_current(force_identity_probe=True)

    managed = manager._managed_auth_path("claude", account_id)
    assert managed.read_bytes() == rotated
    assert managed.with_name(managed.name + ".prev").read_bytes() == original
    actions = [entry["action"] for entry in manager.audit_entries()]
    assert "captured" in actions
    assert "managed_auth_written" in actions
    written = next(
        entry for entry in manager.audit_entries() if entry["action"] == "managed_auth_written"
    )
    assert written["matched_by"] == "verified_identity"
    assert written["account_id"] == account_id


@pytest.mark.asyncio
async def test_quota_samples_record_the_account_they_describe(tmp_path: Path) -> None:
    """A slot that changes hands must not silently re-attribute its history."""
    from swe_mux.operational_telemetry import OperationalTelemetryStore

    store = OperationalTelemetryStore(tmp_path / "mux.db")
    quota = {"session": {"used_percent": 40.0}, "weekly": {"used_percent": 10.0}, "status": "ready"}
    await store.record_quota_sample(
        provider="claude",
        account_id="slot",
        provider_account_uuid="acct-1",
        quota=quota,
        sampled_at=1000.0,
        account_active=True,
        auth_state="saved",
    )
    # The slot now holds a different account's credentials. Its lower usage must
    # not read as a quota reset against the previous owner's sample.
    result = await store.record_quota_sample(
        provider="claude",
        account_id="slot",
        provider_account_uuid="acct-2",
        quota={
            "session": {"used_percent": 2.0},
            "weekly": {"used_percent": 1.0},
            "status": "ready",
        },
        sampled_at=2000.0,
        account_active=True,
        auth_state="saved",
    )
    assert result["reset_events"] == []

    removed = await store.purge_account("claude", "slot", since=1500.0)
    assert removed["quota_samples"] == 1
    remaining = await store.snapshot(account_id="slot")
    store.close()
    assert removed["quota_sample_rollups"] == 0
    assert [row["provider_account_uuid"] for row in remaining["quota"]["samples"]] == ["acct-1"]


def test_v1_manifest_migration_drops_organization_scoped_claude_ids(tmp_path: Path) -> None:
    """v1 keyed Claude accounts on an organization UUID, which two logins can share."""
    data_dir = tmp_path / "mux"
    data_dir.mkdir()
    (data_dir / "provider-accounts.json").write_text(
        json.dumps(
            {
                "version": 1,
                "selected": {"claude": "a", "codex": "b"},
                "accounts": [
                    {
                        "id": "a",
                        "provider": "claude",
                        "label": "claude",
                        "email": "one@example.com",
                        "provider_account_id": "org-uuid",
                        "auth_digest": "deadbeef",
                    },
                    {
                        "id": "b",
                        "provider": "codex",
                        "label": "codex",
                        "provider_account_id": "chatgpt-account",
                        "auth_digest": "cafe",
                    },
                ],
                "quota": {"a": {"status": "ready"}},
            }
        ),
        encoding="utf-8",
    )
    manager = ProviderAccountManager(data_dir, EventBus(), home=tmp_path / "home")

    accounts = {account["id"]: account for account in manager.snapshot()["accounts"]}
    assert set(accounts) == {"a", "b"}
    assert accounts["a"]["provider_account_id"] is None
    assert accounts["a"]["identity_source"] == "file"
    assert accounts["b"]["provider_account_id"] == "chatgpt-account"
    assert accounts["b"]["identity_source"] == "token"


async def test_claude_identity_is_derived_from_the_token_not_the_machine(
    tmp_path: Path,
) -> None:
    manager = ProviderAccountManager(tmp_path, EventBus(), home=tmp_path / "home")
    seen: list[str] = []

    async def request(
        self: ProviderAccountManager,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        seen.append(url)
        assert headers and headers["Authorization"] == "Bearer secret"
        return 200, {
            "account": {"uuid": "account-uuid", "email": "person@example.com"},
            "organization": {"uuid": "org-uuid", "name": "Person"},
        }

    manager._json_request = MethodType(request, manager)  # type: ignore[method-assign]
    identity, rotated = await manager._verify_token_identity(
        "claude", {"claudeAiOauth": {"accessToken": "secret"}}
    )

    assert rotated is None
    assert identity == {
        "email": "person@example.com",
        # The account UUID, not the organization UUID: two logins can share an org.
        "provider_account_id": "account-uuid",
        "organization": "Person",
        "source": "token",
    }
    assert seen == ["https://api.anthropic.com/api/oauth/profile"]


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
    manager = ProviderAccountManager(
        tmp_path, EventBus(), executables={"codex": "codex.exe"}
    )
    monkeypatch.setattr(
        "swe_mux.shim_paths.shutil.which",
        lambda command, path=None: r"C:\npm\codex.cmd" if command == "codex" else None,
    )
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")

    assert manager._spawn_command("codex", ["login"]) == [
        r"C:\Windows\System32\cmd.exe",
        "/d",
        "/c",
        r"C:\npm\codex.cmd",
        "login",
    ]
