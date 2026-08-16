from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_storage_modal_is_reachable_and_uses_diagnostics_endpoint() -> None:
    app = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    modal = (ROOT / "frontend" / "src" / "StorageUsageModal.tsx").read_text(
        encoding="utf-8"
    )

    assert "storageUsage.open" in app
    assert "Open storage usage" in app
    assert "<StorageUsageModal" in app
    assert "'GET',`/api/diagnostics/storage" in modal
