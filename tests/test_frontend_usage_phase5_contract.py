from __future__ import annotations

from pathlib import Path

from tests.support.frontend_source import localized_source, source_contains


def test_usage_dashboard_and_palette_autofocus_are_wired() -> None:
    root = Path(__file__).parents[1] / "frontend" / "src"
    app = localized_source((root / "App.tsx").read_text(encoding="utf-8"))
    usage = localized_source((root / "UsageDashboardView.tsx").read_text(encoding="utf-8"))
    modal = localized_source((root / "UsageModal.tsx").read_text(encoding="utf-8"))
    assert source_contains(app, "paletteInput.current?.focus()")
    # Spend is its own dialog, not a segment of Resources. `usage.open` has been named
    # "Open usage analytics" the whole time; it now opens the dialog it is named for.
    assert source_contains(app, "openUsage('overview')")
    assert source_contains(modal, "<UsageAgentsView")
    assert source_contains(usage, "<UsageTrend")
    assert source_contains(usage, "<UsageBreakdown")


def test_usage_dialog_keeps_measurement_sources_separate() -> None:
    root = Path(__file__).parents[1] / "frontend" / "src"
    modal = localized_source((root / "UsageModal.tsx").read_text(encoding="utf-8"))
    resources = localized_source((root / "ResourcesModal.tsx").read_text(encoding="utf-8"))
    overview = localized_source((root / "UsageOverview.tsx").read_text(encoding="utf-8"))
    assert source_contains(modal, "<FleetActivityView")
    assert not source_contains(resources, "FleetActivityView")
    assert source_contains(overview, "transcript estimate")
    assert source_contains(overview, "metered · billed")
    assert source_contains(overview, "% of window")
    assert source_contains(modal, "{active.footer}")


def test_historical_collection_controls_remain_source_scoped() -> None:
    root = Path(__file__).parents[1] / "frontend" / "src"
    usage = localized_source((root / "UsageDashboardView.tsx").read_text(encoding="utf-8"))
    quota = localized_source((root / "QuotaAnalytics.tsx").read_text(encoding="utf-8"))
    assert source_contains(usage, "/api/usage/refresh")
    assert source_contains(usage, "/api/usage/cache")
    assert not source_contains(quota, "/api/usage/refresh")
    for elsewhere in ("WorkloadTelemetry", "ToolsView", "ContextView", "QuotaAnalytics"):
        assert elsewhere not in usage


def test_browser_access_has_no_mux_bearer_path_and_previews_use_proxy() -> None:
    root = Path(__file__).parents[1] / "frontend" / "src"
    api = localized_source((root / "api.ts").read_text(encoding="utf-8"))
    preview = localized_source((root / "PreviewPane.tsx").read_text(encoding="utf-8"))
    location = localized_source((root / "previewLocation.ts").read_text(encoding="utf-8"))
    assert not source_contains(api, "Authorization")
    assert not source_contains(api, "mux.token")
    assert not source_contains(api, "mux.auth")
    # The pane mounts under the proxy route; the route string lives with the path
    # validation that keeps every page it mounts inside it.
    assert source_contains(location, "/preview/${encodeURIComponent(previewId)}/")
    assert source_contains(preview, "const route = previewRoute(preview.id)")
    assert source_contains(preview, "const proxyUrl = route + srcPath")
    assert not source_contains(preview, "allow-same-origin")


def test_mobile_preview_grid_cannot_expand_past_its_tab() -> None:
    css = localized_source(
        (Path(__file__).parents[1] / "frontend" / "src" / "style.css").read_text(encoding="utf-8")
    )

    assert source_contains(css, ".preview-pane{width:100%;min-width:0;min-height:0;display:grid;")
    assert source_contains(css, "grid-template-columns:minmax(0,1fr)")
    assert source_contains(css, ".preview-frame{width:100%;min-width:0;max-width:100%")
    assert source_contains(css, ".preview-frame iframe{display:block;max-width:100%;width:100%")


def test_settings_exposes_direct_tailnet_listener_and_optional_serve() -> None:
    settings = localized_source(
        (Path(__file__).parents[1] / "frontend" / "src" / "Settings.tsx").read_text(
            encoding="utf-8"
        )
    )
    assert source_contains(settings, "tailnet_enabled")
    assert source_contains(settings, "Listen on Tailscale IPv4")
    assert source_contains(settings, "Optional HTTPS with Tailscale Serve")
    assert source_contains(settings, "never every LAN interface")


def test_settings_use_tabs_compact_profile_selection_and_latest_ccusage() -> None:
    root = Path(__file__).parents[1] / "frontend" / "src"
    settings = localized_source((root / "Settings.tsx").read_text(encoding="utf-8"))
    style = localized_source((root / "style.css").read_text(encoding="utf-8"))
    # The section list is one docked column wide and one slide-in drawer narrow, so its
    # class is composed rather than a literal attribute; both halves have to be present,
    # and the drawer needs the stylesheet rule that actually moves it off screen.
    assert source_contains(settings, "settings-tabs")
    assert source_contains(settings, "settings-tabs-drawer")
    assert source_contains(style, ".settings-tabs-drawer{")
    assert source_contains(settings, "activeTab==='terminals'")
    assert source_contains(settings, "selectedProfileId")
    assert source_contains(settings, "Select a profile to inspect or edit it")
    assert source_contains(settings, "npm install -g ccusage@latest")
    assert source_contains(style, ".settings-content label:not(.check)")


def test_input_has_a_command_first_validated_shortcut_editor() -> None:
    root = Path(__file__).parents[1] / "frontend" / "src"
    settings = localized_source((root / "Settings.tsx").read_text(encoding="utf-8"))
    tabs = localized_source((root / "settingsTabs.ts").read_text(encoding="utf-8"))
    style = localized_source((root / "style.css").read_text(encoding="utf-8"))

    assert source_contains(tabs, "{id:'input',label:'Input',group:'Interface'}")
    assert source_contains(settings, "activeTab==='input'")
    assert source_contains(settings, "<h3>Keyboard shortcuts</h3>")
    assert source_contains(settings, "captureBinding")
    # The policy disclosure stopped being one "reserved" list when the reserved sets
    # were split into "the host keeps this" and "you can have it, and here is what it
    # costs" - conflating those is why Ctrl+F was refused while this very panel was
    # intercepting it in the same browser.
    assert source_contains(settings, "What each host and platform takes for itself")
    assert source_contains(settings, "A BROWSER TAB KEEPS")
    assert source_contains(settings, "SHARED WITH THE BROWSER")
    # The preset picker lives on the same tab, and applies outside the draft/Save
    # cycle after a confirmation that names what the preset takes.
    assert source_contains(settings, "<h3>Keyboard preset</h3>")
    assert source_contains(settings, "applyPreset")
    # `browser_reserved` split into two fields when the sets did.
    assert source_contains(settings, "browser_unreachable")
    assert source_contains(settings, "browser_contested")
    assert source_contains(settings, "terminal_reserved")
    assert source_contains(settings, "wm_reserved")
    assert not source_contains(settings, "Keybindings JSON")
    assert source_contains(style, ".keybinding-list")
