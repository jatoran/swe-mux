from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_storage_view_is_reachable_and_uses_diagnostics_endpoint() -> None:
    """Storage is a segment of the Resources dialog; its named entry point still lands on it."""
    app = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    resources = (ROOT / "frontend" / "src" / "ResourcesModal.tsx").read_text(encoding="utf-8")
    view = (ROOT / "frontend" / "src" / "StorageUsageModal.tsx").read_text(encoding="utf-8")

    assert "storageUsage.open" in app
    assert "Open storage usage" in app
    assert "openResources('storage')" in app
    assert "<StorageUsageView />" in resources
    assert "'GET',`/api/diagnostics/storage" in view
