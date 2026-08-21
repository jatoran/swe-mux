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
    assert "previously labelled “Experience index.”" in dashboard
    assert "Built-in observers are controlled in Settings." not in dashboard
    # A collapsible band rather than a bare heading: the detail view is transcript-first
    # and everything above the conversation collapses (`design/features/history.md`).
    assert "section('notes','Run notes'" in history
    assert "Derived annotations" not in history
    assert "Derived annotations" not in app


def test_every_switch_has_one_owner_settings_global_dashboard_per_rule() -> None:
    """Settings owns install-wide policy; the dashboard owns per-rule state.

    The earlier line — enablement on the dashboard, configuration in Settings —
    was invisible to a user and inconsistent with itself (the scheduled-runs
    emergency stop already lived in Settings). The invariant that survives the
    move is that no switch has two owners: the global switches are Settings
    draft toggles and the dashboard shows their state with a SettingLink, while
    the per-rule and observer-group switches stay on the dashboard beside the
    firings they explain and never grow a Settings copy.
    """
    dashboard = source("AutomationDashboard.tsx")
    settings = source("Settings.tsx")

    # Global switches: Settings owns the control, the dashboard owns only a link.
    assert "change('automation_enabled'" in settings
    assert "change('scan_timeline_enabled'" in settings
    assert "updateControl" not in dashboard
    assert 'target="automation.engine"' in dashboard
    assert 'target="automation.scanTimeline"' in dashboard
    # Per-rule switches: dashboard-only.
    assert "updateBuiltin" in dashboard
    assert "change('observer_titler_enabled'" not in settings
    assert "change('observer_summarizer_enabled'" not in settings
    assert "change('phase7_observers_enabled'" not in settings


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


def test_the_dashboard_answers_what_runs_where_read_only() -> None:
    """Nothing runs on a Project that did not opt in, and the dashboard says so.

    The projects view is the fleet aggregation of per-Project enablement — an
    opted-out Project is a row reading "nothing", never a missing row — and it
    is read-only: the revision-checked editor in each Project's settings stays
    the only write path, one link away.
    """
    dashboard = source("AutomationDashboard.tsx")

    assert "'/api/automation/projects'" in dashboard
    assert "Where automations run" in dashboard
    assert 'target="project.automations"' in dashboard
    # No write verbs against the per-Project route from this surface.
    assert "PUT','/api/projects" not in dashboard


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
