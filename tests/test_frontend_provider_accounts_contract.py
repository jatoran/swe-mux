from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_provider_account_ui_distinguishes_live_external_and_saved_auth() -> None:
    source = (ROOT / "frontend" / "src" / "ProviderAccounts.tsx").read_text(
        encoding="utf-8"
    )

    assert "state:'saved'|'external'|'signed_out'|'unreadable'" in source
    assert "current:Record<ProviderName,CurrentProviderAccount>" in source
    assert "external / unsaved" in source
    assert "LIVE SYSTEM AUTH" in source
    assert "startup never restores an older saved account" in source


def test_provider_account_ui_surfaces_identity_verification_and_duplicates() -> None:
    source = (ROOT / "frontend" / "src" / "ProviderAccounts.tsx").read_text(
        encoding="utf-8"
    )
    css = (ROOT / "frontend" / "src" / "style.css").read_text(encoding="utf-8")

    assert "identity_source?:IdentitySource|null" in source
    assert "conflict?:AccountConflict|null" in source
    assert "match_hint?:MatchHint|null" in source
    assert "verified with the provider" in source
    assert "Quota polling is suspended" in source
    assert "relink only if this really is that account" in source
    assert "/adopt" in source
    assert "'/api/provider-accounts/verify'" in source
    # Switching is unconditional: no force flag, no "switch anyway" confirmation.
    assert "force" not in source
    assert "Sessions already running follow the switch" in source
    assert ".account-conflict{" in css
    assert ".account-identity.verified{" in css


def test_provider_account_ui_marks_external_and_unreadable_states() -> None:
    css = (ROOT / "frontend" / "src" / "style.css").read_text(encoding="utf-8")

    assert ".account-summary>button.external" in css
    assert ".account-current.external" in css
    assert ".account-current.unreadable" in css


def test_sidebar_account_status_uses_separate_terminal_icon_rows_at_the_bottom() -> None:
    accounts = (ROOT / "frontend" / "src" / "ProviderAccounts.tsx").read_text(
        encoding="utf-8"
    )
    app = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    css = (ROOT / "frontend" / "src" / "style.css").read_text(encoding="utf-8")

    icons = (ROOT / "frontend" / "src" / "harnessIcons.tsx").read_text(encoding="utf-8")

    # One mark per harness, in one module. They used to live here, beside the switcher,
    # which knows only the harnesses that have provider accounts - so every other harness
    # fell back to its initial and `oh-my-pi` and `opencode` both drew as `O`.
    assert "harnessMark" in accounts
    assert "providerGlyph" not in accounts
    assert "stroke: 'currentColor'," in icons
    assert 'class="provider-mark"' in icons
    assert "provider==='claude'?'✳'" not in icons
    for harness in ("claude", "codex", "pi", "omp", "opencode"):
        assert f"  {harness}: " in icons
    # An unknown harness is a daemon newer than this build, and its own initial is the
    # most a browser can honestly say about it.
    assert "harnessDisplayName(name).slice(0, 1).toUpperCase()" in icons
    assert "accountAbbreviation(currentLabel(current,account))" in accounts
    assert 'class="quota-grid-column quota-grid-identity"' in accounts
    assert 'class="quota-grid-column quota-grid-metric"' in accounts
    assert "segment.heading" in accounts
    assert "segment.text" in accounts
    assert "{state!=='ready'&&<em>{state}</em>}" in accounts
    assert app.rfind("<AccountSwitcher onManage") > app.rfind('class="project-tree"')
    assert ".account-summary{grid-template-columns:1fr}" in css
    assert 'class="sidebar-status"' in app
    assert ".sidebar-status{min-width:0;flex:none;margin-top:auto" in css


def test_sidebar_account_status_survives_the_mobile_breakpoint() -> None:
    """The drawer carries the same status block as desktop.

    Mobile used to hide it outright, leaving the toolbar chips as the only quota
    surface: they show each provider's usage but not which account is selected,
    nor owned-process usage — and the drawer is where a phone user goes looking
    for exactly that.
    """
    css = (ROOT / "frontend" / "src" / "style.css").read_text(encoding="utf-8")

    assert ".sidebar-status{display:none}" not in css
    # Rows become taps rather than hover targets, so they take the mobile touch floor.
    assert (
        ".sidebar-status>.account-switcher .account-summary>button{min-height:46px}"
        in css
    )
    assert ".sidebar-status .resource-usage-summary{min-height:46px}" in css


def test_account_and_resource_switchers_escape_sidebar_as_viewport_popovers() -> None:
    accounts = (ROOT / "frontend" / "src" / "ProviderAccounts.tsx").read_text(
        encoding="utf-8"
    )
    app = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    resources = (ROOT / "frontend" / "src" / "ResourceUsage.tsx").read_text(
        encoding="utf-8"
    )
    css = (ROOT / "frontend" / "src" / "style.css").read_text(encoding="utf-8")

    assert "createPortal(popup,document.body)" in accounts
    assert "accountPopoverStyle" in accounts
    assert "ResourceUsageSummary" in app
    assert "createPortal(popup,document.body)" in resources
    assert "anchoredPopoverStyle" in resources
    assert "open process fleet…" in resources
    # `ui-portal` is what keeps a body-portalled popover following the UI scale; see
    # test_frontend_ui_scale_contract for the rule it opts into.
    assert 'class="account-popover resource-usage-popover ui-portal"' in resources
    assert ".account-popover{position:fixed" in css
    assert ".resource-usage-popover>section{padding:0}" in css


def test_expanded_resource_summary_is_one_icon_led_row_with_full_hover_copy() -> None:
    resources = (ROOT / "frontend" / "src" / "ResourceUsage.tsx").read_text(
        encoding="utf-8"
    )
    css = (ROOT / "frontend" / "src" / "style.css").read_text(encoding="utf-8")

    assert 'class="resource-process-count"' in resources
    assert "<ProcessesIcon/>" in resources
    assert "<CpuIcon/>" in resources
    assert "<RamIcon/>" in resources
    assert "click to see usage details" in resources
    assert '<div class="resource-usage-head">' not in resources
    assert ".resource-usage-summary{width:100%;min-width:0;min-height:34px;display:flex" in css


def test_resource_popover_is_three_figures_with_one_ram_box() -> None:
    """The popover stays small: system CPU, one combined RAM box, process count.

    The per-Project, daemon/infrastructure, and duplicated-tooling breakdowns
    and the scope-explanation note were removed 2026-08-26; the Resources
    dialog's Processes segment is the detail surface. The single RAM box
    prefers the reclaimable (unique-set) reading the open panel samples and
    falls back to the working set.
    """
    resources = (ROOT / "frontend" / "src" / "ResourceUsage.tsx").read_text(
        encoding="utf-8"
    )
    css = (ROOT / "frontend" / "src" / "style.css").read_text(encoding="utf-8")

    assert "SYSTEM CPU" in resources
    assert "<article><span>RAM</span>" in resources
    assert "reclaimableRam??combined.memory_bytes" in resources
    assert "RECLAIMABLE RAM" not in resources
    assert "WORKING SET" not in resources
    assert "CPU is whole-system load." not in resources
    assert "daemon + infrastructure" not in resources
    assert "by project" not in resources
    assert "duplicated per-session tooling" not in resources
    assert "OWNED RAM" not in resources
    assert "OWNED PROC" not in resources
    assert "grid-template-columns:repeat(auto-fit,minmax(72px,1fr))" in css
