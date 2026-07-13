from __future__ import annotations

from pathlib import Path


def test_usage_dashboard_and_palette_autofocus_are_wired() -> None:
    root = Path(__file__).parents[1] / "frontend" / "src"
    app = (root / "App.tsx").read_text(encoding="utf-8")
    usage = (root / "UsageDashboard.tsx").read_text(encoding="utf-8")
    assert "paletteInput.current?.focus()" in app
    assert "UsageDashboard" in app
    assert "Refreshing ${provider} usage" in usage
    assert "Recent daily usage" in usage
    assert "Models" in usage


def test_browser_access_has_no_mux_bearer_path_and_previews_use_proxy() -> None:
    root = Path(__file__).parents[1] / "frontend" / "src"
    api = (root / "api.ts").read_text(encoding="utf-8")
    preview = (root / "PreviewPane.tsx").read_text(encoding="utf-8")
    assert "Authorization" not in api
    assert "mux.token" not in api
    assert "mux.auth" not in api
    assert "/preview/${encodeURIComponent(preview.id)}/" in preview
    assert "allow-same-origin" not in preview


def test_settings_exposes_direct_tailnet_listener_and_optional_serve() -> None:
    settings = (
        Path(__file__).parents[1] / "frontend" / "src" / "Settings.tsx"
    ).read_text(encoding="utf-8")
    assert "tailnet_enabled" in settings
    assert "Listen directly on the detected Tailscale IPv4 address" in settings
    assert "Optional HTTPS with Tailscale Serve" in settings
    assert "never every LAN interface" in settings
