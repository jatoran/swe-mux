from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[1] / "frontend" / "src"


def test_automation_dashboard_exposes_outcomes_diagnostics_and_reviewed_batches() -> None:
    dashboard = (ROOT / "AutomationDashboard.tsx").read_text(encoding="utf-8")

    for surface in (
        "System observers",
        "Custom rules",
        # The explainer of the deterministic checks stayed, in the help panel, where every
        # other explanation of this pipeline already lived.
        "What all-session health watches",
        "Learned fixes",
        "Recent knowledge batches",
        "Recent rule execution",
        "Action results",
        "Observer calls",
    ):
        assert surface in dashboard
    # The pipeline produces exactly two things - an attention item or a run note - and each
    # has exactly one home. This dashboard used to draw a second copy of both, with two
    # different filters, so a record visible in one could be missing from the other. It
    # links to them now.
    # The *view* is gone; the words survive as the label on the link that replaced it.
    assert "view==='attention'" not in dashboard
    assert "view==='notes'" not in dashboard
    assert "view==='health'" not in dashboard
    assert "onOpenAlerts" in dashboard
    assert "onOpenFindings" in dashboard
    assert "Run notes" in dashboard  # the link's label
    assert "Findings" in (ROOT / "FindingsPane.tsx").read_text(encoding="utf-8")
    # The workload table went to Resources, following the cost column that had already left
    # the same view for the same reason.
    assert "Observed workload telemetry" not in dashboard
    assert "Observed workload" in (ROOT / "WorkloadTelemetry.tsx").read_text(encoding="utf-8")
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
    # Spending caps are edited through the shared `{tokens?, usd?, mode}` control rather
    # than as separate token and dollar boxes, so the contract is the control being wired
    # to each cap. `frontend/test/budgetControl.test.ts` owns the full inventory.
    assert '<BudgetControl name="automation_daily_budget"' in settings
    assert '<BudgetControl name="automation_rule_daily_budget"' in settings
