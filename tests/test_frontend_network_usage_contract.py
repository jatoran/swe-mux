from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_bandwidth_modal_is_reachable_and_uses_diagnostics_endpoint() -> None:
    app = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    modal = (ROOT / "frontend" / "src" / "NetworkUsageModal.tsx").read_text(
        encoding="utf-8"
    )

    assert "networkUsage.open" in app
    assert "Bandwidth usage…" in app
    assert "<NetworkUsageModal" in app
    assert "'GET','/api/diagnostics/network'" in modal
    assert "'DELETE','/api/diagnostics/network'" in modal
