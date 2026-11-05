from pathlib import Path

from tests.support.frontend_source import localized_source, source_contains

ROOT = Path(__file__).resolve().parents[1]


def test_storage_view_is_reachable_and_uses_diagnostics_endpoint() -> None:
    """Storage is a segment of the Resources dialog; its named entry point still lands on it."""
    app = localized_source((ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8"))
    resources = localized_source(
        (ROOT / "frontend" / "src" / "ResourcesModal.tsx").read_text(encoding="utf-8")
    )
    view = localized_source(
        (ROOT / "frontend" / "src" / "StorageUsageModal.tsx").read_text(encoding="utf-8")
    )

    assert source_contains(app, "storageUsage.open")
    assert source_contains(app, "Open storage usage")
    assert source_contains(app, "openResources('storage')")
    assert source_contains(resources, "<StorageUsageView />")
    assert source_contains(view, "'GET',`/api/diagnostics/storage")
