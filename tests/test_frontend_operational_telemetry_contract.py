from pathlib import Path

ROOT = Path(__file__).parents[1]
FRONTEND = ROOT / "frontend" / "src"


def source(name: str) -> str:
    return (FRONTEND / name).read_text(encoding="utf-8")


def test_process_fleet_exposes_durable_identity_state_and_rechecked_actions() -> None:
    # One surface, drawn by both the modal inspector and the drawer's Processes tab, so this
    # reads the surface rather than either shell.
    view = source("ProcessFleetView.tsx")
    # Each process draws as one line, so the identity evidence moved behind that line's
    # expander and is assembled in the pure row model rather than inline in the view. It is
    # still every field, and the actions still sit beside it.
    rows = source("processRows.ts")
    fleet = source("processFleet.ts")
    assert "identity_id" in view
    assert "suspected_orphan" in fleet
    assert "evidence_reason" in rows
    assert "confidence" in rows
    assert "attribution" in rows
    assert "first_seen" in rows
    assert "last_seen" in rows
    assert "'seen'" in rows
    assert "Re-checks the durable process fingerprint" in view
    assert "identity_id: process.identity_id" in view
    assert "auto" + "kill" not in view.lower()


def test_usage_dashboard_exposes_phase2_evidence_without_identity_overclaim() -> None:
    dashboard = source("UsageDashboardView.tsx")
    quota = source("QuotaAnalytics.tsx")
    assert "/api/telemetry/operational" in dashboard
    assert "quota + resets" in dashboard
    assert "/api/telemetry/quota-series" in source("usageAnalytics.ts")
    assert "external/unassigned" in quota
    assert "Correlation remains observational" in quota
    assert "Legacy rows without a provider ID are marked explicitly" in quota
    assert "not account-specific" in dashboard
    assert "tools + skills" in dashboard
    assert "unknown or unmapped" in dashboard
    assert "project/session" in dashboard
    assert "parser_versions" in dashboard
    assert "Token drops alone remain unknown" in dashboard


def test_reset_indicator_is_purple_deduplicated_and_sound_is_per_device_profile() -> None:
    accounts = source("ProviderAccounts.tsx")
    sounds = source("sessionSounds.ts")
    alert_settings = source("NotificationPushSettings.tsx")
    device_settings = source("deviceSettings.ts")
    styles = source("style.css")
    assert "quota-reset-indicator" in accounts
    # Dismissal is server-side review, not a per-browser marker: the old localStorage
    # key meant "mark seen" at the desk left the same alert waiting on the phone.
    assert "swe-mux:last-seen-reset" not in accounts
    # Sound preferences moved from per-browser localStorage to server-persisted
    # desktop/mobile device-class profiles; the old local blob is imported once.
    assert "soundPreferencesFor" in sounds and "rawDomain" in sounds
    assert "'swe-mux:session-sounds-v1'" in device_settings
    assert "migrateLegacySounds" in device_settings
    assert "unexpected_quota_reset" in sounds
    assert "/api/telemetry/quota-resets/review" in accounts
    assert "'seen'" in accounts
    assert "manual Codex usage" in accounts
    assert "discard as error" in accounts
    assert "Sound in the open app" in alert_settings
    assert "Enable alerts for" in alert_settings
    assert "settings-profile-switch" in alert_settings
    assert "#a855f7" in styles


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
