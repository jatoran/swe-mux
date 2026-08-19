from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_bandwidth_view_is_reachable_and_uses_diagnostics_endpoint() -> None:
    """Bandwidth is a segment of the Resources dialog, not a modal of its own.

    The named entry point survives the consolidation: `networkUsage.open` still exists and
    still lands on bandwidth rather than on whatever the dialog opens by default. That is
    the whole test - four modals became one dialog, and no way in was allowed to become a
    way to somewhere else.
    """
    app = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    resources = (ROOT / "frontend" / "src" / "ResourcesModal.tsx").read_text(encoding="utf-8")
    view = (ROOT / "frontend" / "src" / "NetworkUsageModal.tsx").read_text(encoding="utf-8")

    assert "networkUsage.open" in app
    assert "openResources('network')" in app
    assert "<ResourcesModal" in app
    assert "<NetworkUsageView />" in resources
    assert "'GET','/api/diagnostics/network'" in view
    assert "'DELETE','/api/diagnostics/network'" in view
