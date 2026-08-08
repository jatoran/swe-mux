from pathlib import Path

ROOT = Path(__file__).parents[1]
FRONTEND = ROOT / "frontend" / "src"


def source(name: str) -> str:
    return (FRONTEND / name).read_text(encoding="utf-8")


def test_process_fleet_exposes_durable_identity_state_and_rechecked_actions() -> None:
    panel = source("ProcessPanel.tsx")
    assert "identity_id" in panel
    assert "suspected_orphan" in panel
    assert "evidence_reason" in panel
    assert "confidence" in panel
    assert "first seen" in panel
    assert "last seen" in panel
    assert "Re-checks the durable process fingerprint" in panel
    assert "identity_id:process.identity_id" in panel
    assert "auto" + "kill" not in panel.lower()


def test_usage_dashboard_exposes_phase2_evidence_without_identity_overclaim() -> None:
    dashboard = source("UsageDashboard.tsx")
    assert "/api/telemetry/operational" in dashboard
    assert "quota + resets" in dashboard
    assert "external/unassigned" in dashboard
    assert "probabilistic" in dashboard
    assert "does not prove personal identity" in dashboard
    assert "tools + skills" in dashboard
    assert "unknown/unmapped" in dashboard
    assert "Project/session" in dashboard
    assert "parser_versions" in dashboard
    assert "Token drops alone remain unknown" in dashboard


def test_reset_indicator_is_purple_deduplicated_and_sound_is_per_device_profile() -> None:
    accounts = source("ProviderAccounts.tsx")
    sounds = source("sessionSounds.ts")
    alert_settings = source("NotificationPushSettings.tsx")
    device_settings = source("deviceSettings.ts")
    styles = source("style.css")
    assert "quota-reset-indicator" in accounts
    assert "swe-mux:last-seen-reset" in accounts
    # Sound preferences moved from per-browser localStorage to server-persisted
    # desktop/mobile device-class profiles; the old local blob is imported once.
    assert "soundPreferencesFor" in sounds and "rawDomain" in sounds
    assert "'swe-mux:session-sounds-v1'" in device_settings
    assert "migrateLegacySounds" in device_settings
    assert "unexpected_quota_reset" in sounds
    assert "/api/telemetry/quota-resets/" in accounts
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
