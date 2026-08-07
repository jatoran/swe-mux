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

    assert "providerGlyph" in accounts
    assert "provider==='claude'?'✳':provider==='codex'?openaiMark" in accounts
    assert "harnessDisplayName(provider).slice(0,1).toUpperCase()" in accounts
    assert 'class="provider-mark"' in accounts
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
    assert "daemon + infrastructure" in resources
    assert ".account-popover{position:fixed" in css
    assert ".resource-usage-popover>section{padding:0}" in css
