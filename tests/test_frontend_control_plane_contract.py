from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[1] / "frontend" / "src"


def test_automation_dashboard_exposes_outcomes_diagnostics_and_reviewed_batches() -> None:
    dashboard = (ROOT / "AutomationDashboard.tsx").read_text(encoding="utf-8")

    for surface in (
        "System observers",
        "Custom rules",
        "Run notes",
        "Attention inbox",
        "What all-session health watches",
        "Observed workload telemetry",
        "What happened while I was away?",
        "Learned fixes",
        "Recent knowledge batches",
        "Recent rule execution",
        "Action results",
        "Observer calls",
    ):
        assert surface in dashboard
    assert "Select up to 25 ended runs" in dashboard
    assert "never modify a repository" in dashboard
    assert "start reviewed batch" in dashboard
    assert "preview_token:String(batchPreview?.preview_token" in dashboard


def test_cross_vendor_review_binds_explicit_confirmation_to_visible_prompt() -> None:
    app = (ROOT / "App.tsx").read_text(encoding="utf-8")

    assert "Reviewed prompt" in app
    assert "readOnly value={reviewState.preview.prompt}" in app
    assert "preview_token:reviewState.preview.preview_token" in app
    assert "prompt reviewed" in app
    assert "Spawn {reviewState.preview.backend} review" in app
    assert "no rule or observer can start this session" in app


def test_automation_settings_keep_key_write_only_and_show_privacy_boundary() -> None:
    settings = (ROOT / "Settings.tsx").read_text(encoding="utf-8")

    assert 'type="password"' in settings
    assert "The key is write-only" in settings
    assert "bounded transcript slice" in settings
    assert "Test + set/replace" in settings
    assert "Test entered key" in settings
    assert "Clear stored key" in settings
    assert "Daily token budget" in settings
    assert "Per-rule daily dollars" in settings
