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


def test_operational_evidence_survives_the_split_without_identity_overclaim() -> None:
    """The evidence surfaces moved to two dialogs; every claim they make moved with them.

    Quota is one of the three pots of spend and lives in the Usage dialog. Tool, skill, and
    compaction evidence measures behavior rather than money and lives in Resources → Fleet
    activity. One `/api/telemetry/operational` payload feeds both halves, so the shapes are
    shared rather than copied into each reader.
    """
    telemetry = source("operationalTelemetry.ts")
    fleet = source("FleetActivityView.tsx")
    modal = source("UsageModal.tsx")
    segments = source("usageSegments.ts")
    quota = source("QuotaAnalytics.tsx")

    # One declared path and one set of shapes, read by both dialogs.
    assert "/api/telemetry/operational" in telemetry
    assert "OPERATIONAL_TELEMETRY_PATH" in fleet
    assert "OPERATIONAL_TELEMETRY_PATH" in modal
    assert "/api/telemetry/quota-series" in source("usageAnalytics.ts")

    # Quota keeps its account semantics and its refusal to overclaim them.
    assert "<QuotaAnalytics" in modal
    assert "external/unassigned" in quota
    assert "Correlation remains observational" in quota
    assert "Legacy rows without a provider ID are marked explicitly" in quota

    # ...and the historical pot beside it keeps the opposite promise, which is why the
    # caveat is per segment rather than one line for the dialog: ccusage reads transcript
    # roots that carry no trustworthy saved-account identity, so a historical row must never
    # be presented as belonging to an account slot.
    assert "not account-specific" in segments
    assert "quota utilization, not tokens" in segments
    assert "{active.footer}" in modal

    # Tool, skill, and compaction evidence, with every caveat it carried before the move.
    assert "tools + skills" in fleet
    assert "context + compaction" in fleet
    assert "unknown or unmapped" in fleet
    assert "project/session" in fleet
    assert "parser_versions" in fleet
    assert "Token drops alone remain unknown" in fleet
    assert "Prompt similarity and file reads never imply skill usage" in fleet
    # Parser coverage is a collapsed diagnostic, not a third peer metric: it says whether
    # the figures above were collectable, which is asked only once they look wrong.
    assert "telemetry-collection-health" in fleet
    assert "<details class=\"telemetry-collection-health\">" in fleet

    # Money is deliberately absent here. The Usage dialog is the whole cost picture, and a
    # second table of one number under a second name is the drift this split removed.
    for money in ("cost_usd", "AutomationSpendView", "formatMoney"):
        assert money not in fleet


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
