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
    assert "switch anyway" in source
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
    assert "provider==='claude'?'✳':openaiMark" in accounts
    assert 'class="provider-mark"' in accounts
    assert "{state!=='ready'&&<em>{state}</em>}" in accounts
    assert app.rfind("<AccountSwitcher onManage") > app.rfind('class="project-tree"')
    assert ".account-summary{grid-template-columns:1fr}" in css
    assert 'class="sidebar-status"' in app
    assert ".sidebar-status{min-width:0;flex:none;margin-top:auto" in css


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
    assert 'class="account-popover resource-usage-popover"' in resources
    assert "daemon + infrastructure" in resources
    assert ".account-popover{position:fixed" in css
    assert ".resource-usage-popover>section{padding:0}" in css
