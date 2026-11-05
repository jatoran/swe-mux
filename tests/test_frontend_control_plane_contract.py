from __future__ import annotations

from pathlib import Path

from tests.support.frontend_source import localized_source, source_contains

ROOT = Path(__file__).parents[1] / "frontend" / "src"


def test_automation_dashboard_exposes_outcomes_diagnostics_and_reviewed_batches() -> None:
    dashboard = localized_source((ROOT / "AutomationDashboard.tsx").read_text(encoding="utf-8"))

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
    assert not source_contains(dashboard, "view==='attention'")
    assert not source_contains(dashboard, "view==='notes'")
    assert not source_contains(dashboard, "view==='health'")
    assert source_contains(dashboard, "<AttentionInbox")
    assert not source_contains(dashboard, "/api/attention/inbox")
    assert not source_contains(dashboard, "/api/annotations")
    assert "Findings" in localized_source((ROOT / "FindingsPane.tsx").read_text(encoding="utf-8"))
    # The workload table left this view, following the cost column that had already left it
    # for the same reason, and is now the Runs view of Usage & activity -> Activity.
    assert not source_contains(dashboard, "Observed workload telemetry")
    assert not source_contains(dashboard, "/api/telemetry/v2/runs")
    workload = localized_source((ROOT / "WorkloadTelemetry.tsx").read_text(encoding="utf-8"))
    assert source_contains(workload, "Runs started in this period")
    assert source_contains(workload, "/api/telemetry/v2/runs")
    assert source_contains(dashboard, "Select up to 25 ended runs")
    assert source_contains(dashboard, "never modify a repository")
    assert source_contains(dashboard, "start reviewed batch")
    assert source_contains(dashboard, "preview_token:String(batchPreview?.preview_token")


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
    app = localized_source((ROOT / "App.tsx").read_text(encoding="utf-8"))
    history = localized_source((ROOT / "HistoryBrowser.tsx").read_text(encoding="utf-8"))

    for surface in (app, history):
        assert not source_contains(surface, "second-opinion")
        assert not source_contains(surface, "SecondOpinion")
        assert not source_contains(surface, "reviewState")
    assert not source_contains(history, "Review with")


def test_automation_settings_keep_key_write_only_and_show_privacy_boundary() -> None:
    settings = localized_source((ROOT / "Settings.tsx").read_text(encoding="utf-8"))
    settings += localized_source((ROOT / "ProviderSetup.tsx").read_text(encoding="utf-8"))
    policy = localized_source((ROOT / "AutomationPolicyView.tsx").read_text(encoding="utf-8"))

    assert source_contains(settings, 'type="password"')
    assert source_contains(settings, "The key is write-only")
    assert source_contains(policy, "bounded transcript slice")
    assert source_contains(policy, "do not crawl Project files")
    assert source_contains(settings, "Test + set/replace")
    assert source_contains(settings, "Test entered key")
    assert source_contains(settings, "Clear stored key")
    # Spending caps are edited through the shared `{tokens?, usd?, mode}` control rather
    # than as separate token and dollar boxes, so the contract is the control being wired
    # to each cap. `frontend/test/budgetControl.test.ts` owns the full inventory.
    assert source_contains(policy, '<BudgetControl name="automation_daily_budget"')
    assert source_contains(policy, '<BudgetControl name="automation_rule_daily_budget"')
