from __future__ import annotations

from pathlib import Path


def test_usage_dashboard_and_palette_autofocus_are_wired() -> None:
    root = Path(__file__).parents[1] / "frontend" / "src"
    app = (root / "App.tsx").read_text(encoding="utf-8")
    usage = (root / "UsageDashboardView.tsx").read_text(encoding="utf-8")
    modal = (root / "UsageModal.tsx").read_text(encoding="utf-8")
    assert "paletteInput.current?.focus()" in app
    # Spend is its own dialog, not a segment of Resources. `usage.open` has been named
    # "Open usage analytics" the whole time; it now opens the dialog it is named for.
    assert "openUsage('overview')" in app
    assert "<UsageAgentsView" in modal
    assert "Refreshing historical sources" in usage
    assert "time series" in usage
    assert "model breakdown" in usage
    assert "UsageSeries" in usage


def test_usage_dialog_separates_the_three_pots_and_never_sums_them() -> None:
    root = Path(__file__).parents[1] / "frontend" / "src"
    segments = (root / "usageSegments.ts").read_text(encoding="utf-8")
    overview = (root / "UsageOverview.tsx").read_text(encoding="utf-8")
    modal = (root / "UsageModal.tsx").read_text(encoding="utf-8")

    # Four segments, and the first is the headline the old Tokens segment never had.
    assert "'overview' | 'agents' | 'automation' | 'quota'" in segments
    for label in ("Overview", "Agents", "Automation", "Quota"):
        assert f"label: '{label}'" in segments

    # Every pot carries the basis that makes its figure mean something. A dollar figure
    # read back out of transcripts and a dollar figure billed by the call are not the same
    # claim, and the whole reason these are three tiles rather than one row is that a
    # reader has to be able to tell them apart without opening anything.
    assert "subscription · estimated" in overview
    assert "metered · billed" in overview
    assert "% of window" in overview
    assert "never totaled" in overview
    # Every segment's footer restates it, because a reader who deep-linked to one pot never
    # saw the Overview that explains why there is no total.
    assert segments.count("The three pots are never summed") >= 1
    assert "NEVER_SUMMED" in segments
    assert "{active.footer}" in modal


def test_the_historical_controls_belong_to_the_only_segment_they_apply_to() -> None:
    """The source picker, refresh, and cache controls were shared by six domains that had
    no use for five of them, and the status line printed an apology saying so."""
    usage = (
        Path(__file__).parents[1] / "frontend" / "src" / "UsageDashboardView.tsx"
    ).read_text(encoding="utf-8")

    assert "usage-source-picker" in usage
    assert "clear cache" in usage
    # The apology, and the domain rail that made it necessary, are both gone.
    assert "Filters below apply only to this telemetry category" not in usage
    assert "usage-domain-tabs" not in usage
    # ...and so is everything that was never a token or a dollar.
    for elsewhere in ("WorkloadTelemetry", "ToolsView", "ContextView", "QuotaAnalytics"):
        assert elsewhere not in usage


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
    # The section list is one docked column wide and one slide-in drawer narrow, so its
    # class is composed rather than a literal attribute; both halves have to be present,
    # and the drawer needs the stylesheet rule that actually moves it off screen.
    assert "settings-tabs" in settings
    assert "settings-tabs-drawer" in settings
    assert ".settings-tabs-drawer{" in style
    assert "activeTab==='terminals'" in settings
    assert "selectedProfileId" in settings
    assert "Select a profile to inspect or edit it" in settings
    assert "npm install -g ccusage@latest" in settings
    assert ".settings-content label:not(.check)" in style


def test_input_has_a_command_first_validated_shortcut_editor() -> None:
    root = Path(__file__).parents[1] / "frontend" / "src"
    settings = (root / "Settings.tsx").read_text(encoding="utf-8")
    tabs = (root / "settingsTabs.ts").read_text(encoding="utf-8")
    style = (root / "style.css").read_text(encoding="utf-8")

    assert "{id:'input',label:'Input',group:'Interface'}" in tabs
    assert "activeTab==='input'" in settings
    assert "<h3>Keyboard shortcuts</h3>" in settings
    assert "captureBinding" in settings
    # The policy disclosure stopped being one "reserved" list when the reserved sets
    # were split into "the host keeps this" and "you can have it, and here is what it
    # costs" - conflating those is why Ctrl+F was refused while this very panel was
    # intercepting it in the same browser.
    assert "What each host and platform takes for itself" in settings
    assert "A BROWSER TAB KEEPS" in settings
    assert "SHARED WITH THE BROWSER" in settings
    # The preset picker lives on the same tab, and applies outside the draft/Save
    # cycle after a confirmation that names what the preset takes.
    assert "<h3>Keyboard preset</h3>" in settings
    assert "applyPreset" in settings
    # `browser_reserved` split into two fields when the sets did.
    assert "browser_unreachable" in settings
    assert "browser_contested" in settings
    assert "terminal_reserved" in settings
    assert "wm_reserved" in settings
    assert "Keybindings JSON" not in settings
    assert ".keybinding-list" in style
