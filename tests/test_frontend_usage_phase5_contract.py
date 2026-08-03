from __future__ import annotations

from pathlib import Path


def test_usage_dashboard_and_palette_autofocus_are_wired() -> None:
    root = Path(__file__).parents[1] / "frontend" / "src"
    app = (root / "App.tsx").read_text(encoding="utf-8")
    usage = (root / "UsageDashboard.tsx").read_text(encoding="utf-8")
    assert "paletteInput.current?.focus()" in app
    assert "UsageDashboard" in app
    assert "Refreshing ${provider} usage" in usage
    assert "time series" in usage
    assert "model breakdown" in usage
    assert "UsageSeries" in usage


def test_browser_access_has_no_mux_bearer_path_and_previews_use_proxy() -> None:
    root = Path(__file__).parents[1] / "frontend" / "src"
    api = (root / "api.ts").read_text(encoding="utf-8")
    preview = (root / "PreviewPane.tsx").read_text(encoding="utf-8")
    assert "Authorization" not in api
    assert "mux.token" not in api
    assert "mux.auth" not in api
    assert "/preview/${encodeURIComponent(preview.id)}/" in preview
    assert "allow-same-origin" not in preview


def test_mobile_preview_grid_cannot_expand_past_its_tab() -> None:
    css = (Path(__file__).parents[1] / "frontend" / "src" / "style.css").read_text(
        encoding="utf-8"
    )

    assert ".preview-pane{width:100%;min-width:0;min-height:0;display:grid;" in css
    assert "grid-template-columns:minmax(0,1fr)" in css
    assert ".preview-frame{width:100%;min-width:0;max-width:100%" in css
    assert ".preview-frame iframe{display:block;max-width:100%;width:100%" in css


def test_settings_exposes_direct_tailnet_listener_and_optional_serve() -> None:
    settings = (Path(__file__).parents[1] / "frontend" / "src" / "Settings.tsx").read_text(
        encoding="utf-8"
    )
    assert "tailnet_enabled" in settings
    assert "Listen on Tailscale IPv4" in settings
    assert "Optional HTTPS with Tailscale Serve" in settings
    assert "never every LAN interface" in settings


def test_settings_use_tabs_compact_profile_selection_and_latest_ccusage() -> None:
    root = Path(__file__).parents[1] / "frontend" / "src"
    settings = (root / "Settings.tsx").read_text(encoding="utf-8")
    style = (root / "style.css").read_text(encoding="utf-8")
    assert 'class="settings-tabs"' in settings
    assert "activeTab==='terminals'" in settings
    assert "selectedProfileId" in settings
    assert "Select a profile to inspect or edit it" in settings
    assert "npm install -g ccusage@latest" in settings
    assert ".settings-content label:not(.check)" in style


def test_input_has_a_command_first_validated_shortcut_editor() -> None:
    root = Path(__file__).parents[1] / "frontend" / "src"
    settings = (root / "Settings.tsx").read_text(encoding="utf-8")
    style = (root / "style.css").read_text(encoding="utf-8")

    assert "{id:'input',label:'Input'}" in settings
    assert "activeTab==='input'" in settings
    assert "KEYBOARD::SHORTCUTS" in settings
    assert "captureBinding" in settings
    assert "Reserved shortcut policy" in settings
    assert "browser_reserved" in settings
    assert "terminal_reserved" in settings
    assert "Keybindings JSON" not in settings
    assert ".keybinding-list" in style
