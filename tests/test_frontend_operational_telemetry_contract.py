from pathlib import Path

from tests.support.frontend_source import localized_source, source_contains

ROOT = Path(__file__).parents[1]
FRONTEND = ROOT / "frontend" / "src"


def source(name: str) -> str:
    return localized_source((FRONTEND / name).read_text(encoding="utf-8"))


def test_process_fleet_exposes_durable_identity_state_and_rechecked_actions() -> None:
    # One surface, drawn by both the modal inspector and the drawer's Processes tab, so this
    # reads the surface rather than either shell.
    view = source("ProcessFleetView.tsx")
    # Each process draws as one line, so the identity evidence moved behind that line's
    # expander and is assembled in the pure row model rather than inline in the view. It is
    # still every field, and the actions still sit beside it.
    rows = source("processRows.ts")
    fleet = source("processFleet.ts")
    assert source_contains(view, "identity_id")
    assert source_contains(fleet, "suspected_orphan")
    assert source_contains(rows, "evidence_reason")
    assert source_contains(rows, "confidence")
    assert source_contains(rows, "attribution")
    assert source_contains(rows, "first_seen")
    assert source_contains(rows, "last_seen")
    assert source_contains(rows, "'seen'")
    assert source_contains(view, "Re-checks the durable process fingerprint")
    assert source_contains(view, "identity_id: process.identity_id")
    assert "auto" + "kill" not in view.lower()


def test_operational_evidence_keeps_identity_and_collection_boundaries() -> None:
    fleet = source("FleetActivityView.tsx")
    runs = source("WorkloadTelemetry.tsx")
    quota = source("QuotaAnalytics.tsx")
    assert source_contains(quota, "OPERATIONAL_TELEMETRY_PATH")
    assert not source_contains(fleet, "OPERATIONAL_TELEMETRY_PATH")
    for route in (
        "tools/summary",
        "quality",
        "compactions",
        "verifications",
        "metrics/summary",
        "compare",
        "inefficiencies/review",
        "export/",
    ):
        assert f"/api/telemetry/v2/{route}" in fleet
    assert source_contains(runs, "/api/telemetry/v2/runs/")
    assert source_contains(runs, "/api/telemetry/v2/turns/")
    assert source_contains(fleet, "legacy_dashboard_enabled")
    assert source_contains(fleet, "runtime_parent_unavailable")
    assert source_contains(fleet, "Missing measurements are not zero")
    assert source_contains(quota, "Correlation does not establish who used an account")
    for money in ("cost_usd", "AutomationSpendView", "formatMoney"):
        assert money not in fleet


def test_reset_indicator_is_purple_deduplicated_and_sound_is_per_device_profile() -> None:
    accounts = source("ProviderAccounts.tsx")
    sounds = source("sessionSounds.ts")
    alert_settings = source("NotificationPushSettings.tsx")
    device_settings = source("deviceSettings.ts")
    styles = source("style.css")
    assert source_contains(accounts, "quota-reset-indicator")
    # Dismissal is server-side review, not a per-browser marker: the old localStorage
    # key meant "mark seen" at the desk left the same alert waiting on the phone.
    assert not source_contains(accounts, "swe-mux:last-seen-reset")
    # Sound preferences moved from per-browser localStorage to server-persisted
    # desktop/mobile device-class profiles; the old local blob is imported once.
    assert source_contains(sounds, "soundPreferencesFor") and source_contains(sounds, "rawDomain")
    assert source_contains(device_settings, "'swe-mux:session-sounds-v1'")
    assert source_contains(device_settings, "migrateLegacySounds")
    assert source_contains(sounds, "unexpected_quota_reset")
    assert source_contains(accounts, "/api/telemetry/quota-resets/review")
    assert source_contains(accounts, "'seen'")
    assert source_contains(accounts, "manual Codex usage")
    assert source_contains(accounts, "discard as error")
    assert source_contains(alert_settings, "Sound in the open app")
    assert source_contains(alert_settings, "Enable alerts for")
    assert source_contains(alert_settings, "settings-profile-switch")
    assert source_contains(styles, "#a855f7")


def test_settings_expose_bounded_process_and_quota_controls() -> None:
    settings = source("Settings.tsx")
    for field in (
        "process_poll_seconds",
        "process_orphan_grace_seconds",
        "process_evidence_retention_days",
        "operational_telemetry_retention_days",
        "provider_quota_poll_minutes",
        "provider_quota_turn_refresh_enabled",
        "provider_quota_turn_refresh_min_minutes",
    ):
        assert field in settings
