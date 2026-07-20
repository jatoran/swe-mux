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


def test_reset_indicator_is_purple_deduplicated_and_sound_is_device_local() -> None:
    accounts = source("ProviderAccounts.tsx")
    sounds = source("sessionSounds.ts")
    sound_settings = source("NotificationSoundSettings.tsx")
    styles = source("style.css")
    assert "quota-reset-indicator" in accounts
    assert "swe-mux:last-seen-reset" in accounts
    assert "swe-mux:session-sounds-v1" in sounds
    assert "unexpected_quota_reset" in sounds
    assert "Enable sounds on this device" in sound_settings
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
