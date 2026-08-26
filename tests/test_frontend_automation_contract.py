from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[1]


def source(name: str) -> str:
    return (ROOT / "frontend" / "src" / name).read_text(encoding="utf-8")


def test_automation_dashboard_exposes_complete_user_facing_model() -> None:
    dashboard = source("AutomationDashboard.tsx")
    # Run notes render in the transcript reviewer, which is HistoryBrowser. App.tsx
    # held a second, unreachable copy of that reviewer until it was removed.
    history = source("HistoryBrowser.tsx")
    app = source("App.tsx")

    assert "System observers" in dashboard
    assert "Global switches" in dashboard
    assert "automation_enabled" in dashboard
    assert "scan_timeline_enabled" in dashboard
    assert "Custom rules" in dashboard
    assert "built_in_rules" in dashboard
    assert "updateBuiltin" in dashboard
    assert "run notes" in dashboard
    assert "all-session health" in dashboard
    assert "learned fixes" in dashboard
    assert "automation-knowledge-browser" in dashboard
    assert "Demonstrated resolution" in dashboard
    assert "Built-in observers are controlled in Settings." not in dashboard
    # A collapsible band rather than a bare heading: the detail view is transcript-first
    # and everything above the conversation collapses (`design/features/history.md`).
    assert "section('notes','Run notes'" in history
    assert "Derived annotations" not in history
    assert "Derived annotations" not in app


def test_every_switch_has_one_owner_automation_policy_global_dashboard_per_rule() -> None:
    """Automation policy owns global switches; Rules owns per-rule state.

    Settings is only a portal into the unified workspace. Global switches live
    in Global policy; per-rule and observer-group switches stay beside the
    firings they explain.
    """
    dashboard = source("AutomationDashboard.tsx")
    settings = source("Settings.tsx")
    policy = source("AutomationPolicyView.tsx")

    # Global switches: policy owns the controls and Settings owns none.
    assert "change('automation_enabled'" in policy
    assert "change('scan_timeline_enabled'" in policy
    assert "change('automation_enabled'" not in settings
    assert "change('scan_timeline_enabled'" not in settings
    assert "updateControl" not in dashboard
    assert "Open global policy" in dashboard
    # Per-rule switches: dashboard-only.
    assert "updateBuiltin" in dashboard
    assert "change('observer_titler_enabled'" not in settings
    assert "change('observer_summarizer_enabled'" not in settings
    assert "change('attention_observers_enabled'" not in settings


def test_the_rules_editor_lives_on_the_dashboard_not_in_the_settings_save() -> None:
    """A rule's text, live/shadow state, and firings are one object.

    The rules.toml textarea used to save inside the Settings transaction, so a
    stale copy held open there could overwrite a rule toggled or edited on the
    dashboard. One owner now: the dashboard loads and saves the file itself,
    validate-first, and Settings neither fetches nor writes it.
    """
    dashboard = source("AutomationDashboard.tsx")
    settings = source("Settings.tsx")

    assert "'/api/automation/rules?validate=1'" in dashboard
    assert "'/api/automation/rules'" in dashboard
    assert "rules.toml" in dashboard
    assert "/api/automation/rules" not in settings
    assert "rules.toml" not in settings


def test_the_workspace_answers_and_edits_what_runs_where() -> None:
    """Nothing runs on a Project that did not opt in, and the dashboard says so.

    The projects view is the fleet aggregation of per-Project enablement — an
    opted-out Project is a row reading "nothing", never a missing row — and it
    keeps the revision-checked Project editor below the fleet matrix.
    """
    dashboard = source("AutomationDashboard.tsx")

    assert "'/api/automation/projects'" in dashboard
    assert "Where automations run" in dashboard
    assert "<AutomationOptIns" in dashboard
    assert "Select a Project to inspect and edit" in dashboard
    assert "PUT', `/api/projects/${project.id}/automations`" in source("ProjectsManager.tsx")


def test_automation_diagnostics_are_separated_from_primary_workflows() -> None:
    dashboard = source("AutomationDashboard.tsx")

    assert "Historical event dry-run" in dashboard
    assert "Research-only delivery-readiness diagnostics" in dashboard
    assert "delivery_state" in dashboard
    assert "Parser coverage" in dashboard
    assert "diagnostics" in dashboard
    # The away report is a *reading of the attention inbox*, not a fact about the pipeline
    # that fills it, so it lives with the inbox in the Alerts drawer tab.
    assert "What happened while I was away?" not in dashboard
    assert "What happened while I was away?" in source("Notifications.tsx")
