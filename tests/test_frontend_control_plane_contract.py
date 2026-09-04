from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[1] / "frontend" / "src"


def test_automation_dashboard_exposes_outcomes_diagnostics_and_reviewed_batches() -> None:
    dashboard = (ROOT / "AutomationDashboard.tsx").read_text(encoding="utf-8")

    # The built-in observers are policy-matrix rows since schema 36 (an "Observers"
    # group in AutomationMatrix.tsx), so the dashboard draws no observer section.
    for surface in (
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
    # The pipeline produces exactly two things - an attention item or a run note.
    # Run notes keep exactly one home (Activity -> Findings); attention items have
    # two surfaces now, and the second is the SAME component over the SAME
    # endpoints (`AttentionInbox` on the Activity tab), so the two can never
    # disagree about read state. A hand-rolled second inbox or a second
    # annotations table is the drift this pins away.
    assert "view==='attention'" not in dashboard
    assert "view==='notes'" not in dashboard
    assert "view==='health'" not in dashboard
    assert "<AttentionInbox" in dashboard
    assert "/api/attention/inbox" not in dashboard
    assert "/api/annotations" not in dashboard
    assert "Findings" in (ROOT / "FindingsPane.tsx").read_text(encoding="utf-8")
    # The workload table went to Resources, following the cost column that had already left
    # the same view for the same reason.
    assert "Observed workload telemetry" not in dashboard
    assert "Observed workload" in (ROOT / "WorkloadTelemetry.tsx").read_text(encoding="utf-8")
    assert "Select up to 25 ended runs" in dashboard
    assert "never modify a repository" in dashboard
    assert "start reviewed batch" in dashboard
    assert "preview_token:String(batchPreview?.preview_token" in dashboard


def test_cross_vendor_review_has_no_frontend_surface() -> None:
    """The review dialog and its only entry point are gone, together.

    The button lived in the History detail view and the preview dialog lived in
    `App.tsx`; the button was the dialog's sole opener, so removing one and keeping the
    other would leave a review flow nothing can reach and nothing can audit. What is
    pinned here is that neither half came back on its own.

    `POST /history/{id}/second-opinion` is unaffected and keeps its own confirmation
    contract - a preview that spawns nothing, a stale confirm refused, and a confirm
    bound to the preview's token. That contract lives with the route, in
    `test_control_plane_api.py`'s
    `test_review_requires_preview_then_explicit_confirm_and_records_lineage`, so any
    future surface has to satisfy the route rather than a copy of its rules held here.
    """
    app = (ROOT / "App.tsx").read_text(encoding="utf-8")
    history = (ROOT / "HistoryBrowser.tsx").read_text(encoding="utf-8")

    for surface in (app, history):
        assert "second-opinion" not in surface
        assert "SecondOpinion" not in surface
        assert "reviewState" not in surface
    assert "Review with" not in history


def test_automation_settings_keep_key_write_only_and_show_privacy_boundary() -> None:
    settings = (ROOT / "Settings.tsx").read_text(encoding="utf-8")
    policy = (ROOT / "AutomationPolicyView.tsx").read_text(encoding="utf-8")

    assert 'type="password"' in settings
    assert "The key is write-only" in settings
    assert "bounded transcript slice" in policy
    assert "do not crawl Project files" in policy
    assert "Test + set/replace" in settings
    assert "Test entered key" in settings
    assert "Clear stored key" in settings
    # Spending caps are edited through the shared `{tokens?, usd?, mode}` control rather
    # than as separate token and dollar boxes, so the contract is the control being wired
    # to each cap. `frontend/test/budgetControl.test.ts` owns the full inventory.
    assert '<BudgetControl name="automation_daily_budget"' in policy
    assert '<BudgetControl name="automation_rule_daily_budget"' in policy
