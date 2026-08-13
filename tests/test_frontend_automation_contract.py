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
    assert "Global controls" in dashboard
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
    assert "<h4>Run notes</h4>" in history
    assert "Derived annotations" not in history
    assert "Derived annotations" not in app


def test_automation_enablement_is_centralized_in_the_dashboard() -> None:
    dashboard = source("AutomationDashboard.tsx")
    settings = source("Settings.tsx")

    assert "updateControl" in dashboard
    assert "updateBuiltin" in dashboard
    assert 'change(\'automation_enabled\'' not in settings
    assert 'change(\'scan_timeline_enabled\'' not in settings
    assert 'change(\'observer_titler_enabled\'' not in settings
    assert 'change(\'observer_summarizer_enabled\'' not in settings
    assert 'change(\'phase7_observers_enabled\'' not in settings


def test_automation_diagnostics_are_separated_from_primary_workflows() -> None:
    dashboard = source("AutomationDashboard.tsx")

    assert "Historical event dry-run" in dashboard
    assert "Research-only delivery-readiness diagnostics" in dashboard
    assert "delivery_state" in dashboard
    assert "Parser coverage" in dashboard
    assert "diagnostics" in dashboard
    assert "What happened while I was away?" in dashboard
