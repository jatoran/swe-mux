from __future__ import annotations

from pathlib import Path

from tests.support.frontend_source import localized_source, source_contains

ROOT = Path(__file__).parents[1]


def source(name: str) -> str:
    return localized_source((ROOT / "frontend" / "src" / name).read_text(encoding="utf-8"))


def test_automation_dashboard_exposes_complete_user_facing_model() -> None:
    dashboard = source("AutomationDashboard.tsx")
    # Run notes render in the transcript reviewer, which is HistoryBrowser. App.tsx
    # held a second, unreachable copy of that reviewer until it was removed.
    history = source("HistoryBrowser.tsx")
    app = source("App.tsx")

    # Three tabs, and the tab is the question: what may run (and where), what
    # it costs, what it did.
    assert source_contains(dashboard, "export type AutomationView='policy'|'usage'|'activity'")
    # The built-in observers (session titler, attention observers) are rows in the
    # policy matrix since schema 36 - per-Project automations switched globally and
    # per Project like every other consumer - so the dashboard draws no separate
    # observer section and owns no observer switch of its own.
    assert not source_contains(dashboard, "System observers")
    assert not source_contains(dashboard, "updateBuiltin")
    assert source_contains(dashboard, "Custom rules")
    assert source_contains(dashboard, "built_in_rules")
    assert source_contains(dashboard, "all-session health")
    assert source_contains(dashboard, "Learned fixes")
    assert source_contains(dashboard, "automation-knowledge-browser")
    assert source_contains(dashboard, "Demonstrated resolution")
    assert not source_contains(dashboard, "Built-in observers are controlled in Settings.")
    # A collapsible band rather than a bare heading: the detail view is transcript-first
    # and everything above the conversation collapses (`design/features/history.md`).
    assert source_contains(history, "section('notes','Run notes'")
    assert not source_contains(history, "Derived annotations")
    assert not source_contains(app, "Derived annotations")


def test_every_switch_has_one_owner_matrix_global_dashboard_per_rule() -> None:
    """The policy matrix owns install switches; the rules drawer owns per-rule state.

    Settings is only a portal into the unified workspace. The master switch and
    every per-automation install ceiling live on the matrix's Global column, the
    limits live in the Limits & budgets disclosure, and per-rule and
    observer-group switches stay beside the firings they explain.
    """
    dashboard = source("AutomationDashboard.tsx")
    settings = source("Settings.tsx")
    policy = source("AutomationPolicyView.tsx")
    matrix = source("AutomationMatrix.tsx")

    # Install switches: the matrix owns the controls and Settings owns none.
    assert source_contains(matrix, 'data-setting="automation_enabled"')
    assert source_contains(matrix, "automation_global_allow")
    assert not source_contains(settings, "change('automation_enabled'")
    assert not source_contains(settings, "change('scan_timeline_enabled'")
    # ...and the limits view no longer carries a second copy of either switch.
    # The names may appear in prose explaining where the controls went; a
    # *control* form of either is the second owner this refuses.
    assert not source_contains(policy, "change('automation_enabled'")
    assert not source_contains(policy, "change('scan_timeline_enabled'")
    assert not source_contains(policy, 'data-setting="automation_enabled"')
    assert not source_contains(policy, 'data-setting="scan_timeline_enabled"')
    assert not source_contains(dashboard, "updateControl")
    # Per-rule switches for custom rules stay dashboard-only; the built-in
    # observers are matrix rows, so no surface writes an observer switch by name.
    assert source_contains(dashboard, "updateRule")
    assert not source_contains(settings, "observer_titler_enabled")
    assert not source_contains(settings, "observer_summarizer_enabled")
    assert not source_contains(settings, "attention_observers_enabled")
    assert not source_contains(dashboard, "observer_titler_enabled")
    assert not source_contains(dashboard, "attention_observers_enabled")
    # ...and the matrix draws rows by the registry's `family` - the titler in a
    # Titling block directly above the re-titler, the attention observers under
    # Attention - never by dependency shape, which the depth indentation already draws.
    assert source_contains(matrix, "'titling'")
    assert source_contains(matrix, "'attention'")
    assert source_contains(matrix, "item.family")
    # Every row expands to what the switch does, and the copy comes from the
    # daemon's registry rather than a second one written in the browser.
    assert source_contains(matrix, "item.description")
    assert source_contains(matrix, "aria-expanded")


def test_the_matrix_is_the_one_editor_and_greys_the_ceiling_rather_than_hiding_it() -> None:
    """Global x Project on one grid, and the cascade is drawn from the daemon's answer.

    The matrix is the single surface that may turn an automation off, in either
    scope - which is what keeps every grant gate additive-only. A Project cell
    under a blocked install ceiling greys rather than unticking: the Project's
    own choice is retained on disk, and the fix is the Global cell beside it.
    """
    matrix = source("AutomationMatrix.tsx")

    assert source_contains(matrix, "globally_allowed"), (
        "the ceiling must come from the resolved payload"
    )
    assert source_contains(matrix, "globally-off")
    assert source_contains(matrix, "install_switch"), "dedicated switches write their own keys"
    # The install has two things to say about a row and they are two controls,
    # the same split the authority rows below already draw. They were one
    # checkbox until 2026-08-31, and the missing half is why a new Project
    # inherited nothing: the only thing an operator could say install-wide was
    # "no", so every "yes" had to be repeated per Project at creation time.
    assert source_contains(matrix, "automation_project_defaults"), (
        "the Default column writes the template"
    )
    assert source_contains(matrix, "install_default"), "the default is the daemon's resolved answer"
    assert source_contains(matrix, "off everywhere"), "the ceiling half must say what it does"
    # Three positions on the Project cell, not two. "Follow global" and
    # "explicitly off" are different states, and collapsing them is what makes
    # an install default unreachable once anything has touched the Project.
    assert source_contains(matrix, "Follow global")
    assert source_contains(matrix, "inherit"), "a Default checkbox must say how far it reaches"
    assert source_contains(matrix, "PUT',`/api/projects/${project.project_id}/automations`")
    assert source_contains(matrix, "'POST','/api/grants'"), (
        "presets apply through the ordinary grant"
    )
    # The starting-set presets live here: full-screen on a first run, a button after.
    assert source_contains(matrix, "Choose preset")
    assert source_contains(matrix, "mux.automationPresetsSeen")


def test_the_rules_editor_lives_on_the_dashboard_not_in_the_settings_save() -> None:
    """A rule's text, live/shadow state, and firings are one object.

    The rules.toml textarea used to save inside the Settings transaction, so a
    stale copy held open there could overwrite a rule toggled or edited on the
    dashboard. One owner now: the dashboard loads and saves the file itself,
    validate-first, and Settings neither fetches nor writes it.
    """
    dashboard = source("AutomationDashboard.tsx")
    settings = source("Settings.tsx")

    assert source_contains(dashboard, "'/api/automation/rules?validate=1'")
    assert source_contains(dashboard, "'/api/automation/rules'")
    assert source_contains(dashboard, "rules.toml")
    assert not source_contains(settings, "/api/automation/rules")
    assert not source_contains(settings, "rules.toml")


def test_the_workspace_answers_and_edits_what_runs_where() -> None:
    """Nothing runs on a Project that did not opt in, and the dashboard says so.

    The Policy tab is the fleet aggregation of per-Project enablement - every
    registered Project is selectable, and the fleet column counts where each
    automation actually runs - with the revision-checked Project editor as the
    matrix's own Project column.
    """
    dashboard = source("AutomationDashboard.tsx")
    matrix = source("AutomationMatrix.tsx")

    assert source_contains(dashboard, "'/api/automation/projects'")
    assert source_contains(dashboard, "<AutomationPolicyMatrix")
    assert source_contains(matrix, "fleetCount")
    assert source_contains(matrix, "projectDropdownOptions")


def test_automation_diagnostics_are_separated_from_primary_workflows() -> None:
    dashboard = source("AutomationDashboard.tsx")

    assert source_contains(dashboard, "Historical event dry-run")
    assert source_contains(dashboard, "Research-only delivery-readiness diagnostics")
    assert source_contains(dashboard, "delivery_state")
    assert source_contains(dashboard, "Parser coverage")
    assert source_contains(dashboard, "Diagnostics")
    # The away report is a *reading of the attention inbox*, not a fact about the pipeline
    # that fills it, so it lives with the inbox in the Alerts drawer tab.
    assert not source_contains(dashboard, "What happened while I was away?")
    assert "What happened while I was away?" in source("Notifications.tsx")
