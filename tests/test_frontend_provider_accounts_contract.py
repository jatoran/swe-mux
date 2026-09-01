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
    # Two identity states, so exactly two words, with the sentence in the tooltip.
    # This used to be three strings ("verified with the provider", "unverified
    # identity", "identity unverified") for those same two states, which reads on a
    # crowded row as three distinct conditions.
    assert "account.identity_source==='token'?'verified':'unverified'" in source
    assert "Identity confirmed by asking the provider with these credentials." in source
    assert "Identity has not been confirmed against the provider yet." in source
    assert "Quota polling is suspended" in source
    assert "relink only if this really is that account" in source
    assert "/adopt" in source
    assert "'/api/provider-accounts/verify'" in source
    # Switching is unconditional: no force flag, no "switch anyway" confirmation.
    assert "force" not in source
    # And it says what a switch does to processes already running per CLI rather than
    # for both at once: Claude Code re-reads its credential file on an mtime change
    # and follows; Codex keeps the token it read at startup. The disclosure has been
    # wrong in both directions before, so both halves are pinned.
    assert (
        "It is never blocked and never confirmed. "
        "Whether it reaches sessions already running is up to the CLI"
    ) in source
    assert "Claude Code re-reads its credential file when the file changes" in source
    assert "Codex reads its login once at startup" in source
    assert "not retroactive" not in source
    assert "Sessions already running follow the switch" not in source
    assert ".account-conflict{" in css
    assert ".account-identity.verified{" in css


def test_account_switcher_can_start_a_sign_in_without_opening_settings() -> None:
    """The popover is a way in, not only a way to switch between what exists.

    With nothing saved it used to print "No saved accounts" beside a `manage…`
    button - which is the one screen a new install always lands on, and the one
    with no path forward on it.
    """
    source = (ROOT / "frontend" / "src" / "ProviderAccounts.tsx").read_text(
        encoding="utf-8"
    )
    css = (ROOT / "frontend" / "src" / "style.css").read_text(encoding="utf-8")

    assert "useProviderLogin" in source
    assert "/login/dismiss" in source
    # Per-provider control on the heading line, and the empty state itself is the
    # call to action rather than a sentence about being empty.
    assert 'class="account-section-head"' in source
    assert 'class="account-signin"' in source
    assert 'class="account-empty-cta"' in source
    assert "sign in to {provider}" in source
    assert "<p>No saved accounts</p>" not in source
    assert ".account-section-head{" in css
    assert ".account-popover button.account-empty-cta{" in css


def test_running_sign_in_is_daemon_state_that_every_client_sees() -> None:
    """A login outlives the request that started it, so its progress is polled.

    The provider CLI can hold the daemon for `LOGIN_TIMEOUT_SECONDS` while a human
    finishes an OAuth flow. While that was one blocked HTTP request, whoever asked
    owned the only copy of the outcome: closing the panel, reloading, or asking
    from a second device lost it.
    """
    source = (ROOT / "frontend" / "src" / "ProviderAccounts.tsx").read_text(
        encoding="utf-8"
    )
    display = (ROOT / "frontend" / "src" / "providerAccountDisplay.ts").read_text(
        encoding="utf-8"
    )
    css = (ROOT / "frontend" / "src" / "style.css").read_text(encoding="utf-8")

    assert "login?:Record<ProviderName,LoginState|null>" in source
    assert "state:'running'|'succeeded'|'failed'" in display
    assert "function LoginProgress" in source
    # Both surfaces draw the same one.
    assert source.count("<LoginProgress ") == 2
    # A running sign-in resolves on human time, so it gets its own poll cadence.
    assert "const LOGIN_POLL_MS" in source
    assert "awaitingLogin?LOGIN_POLL_MS:idle?idleMs:intervalMs" in source
    for state in ("running", "succeeded", "failed"):
        assert f".account-login.{state}{{" in css
    # The command the tooltip names is the daemon's, built from the configured
    # executable. A copy compiled into the browser would still have named the shipped
    # default on an install that repointed `harness_exe` - which is the drift the
    # harness-name rule exists to stop.
    assert "login_commands?:Record<ProviderName,string>" in source
    assert "signInTitle(status?.login_commands,provider)" in source
    assert "claudeai" not in source


def test_account_settings_states_policy_once_and_only_where_it_acts() -> None:
    """The panel's prose was ~160 static words before any control, with two providers.

    What is left is the part that changes: the live-auth block now renders only in
    the states that need explaining and carry the relink action, the reference text
    folds away, and the two add buttons carry their own explanation.
    """
    source = (ROOT / "frontend" / "src" / "ProviderAccounts.tsx").read_text(
        encoding="utf-8"
    )
    css = (ROOT / "frontend" / "src" / "style.css").read_text(encoding="utf-8")

    # Reference, not instruction: folded, but still the same sentences.
    assert 'class="account-explainer"' in source
    assert "<summary>How switching works</summary>" in source
    assert "keeps spending the outgoing account until it is restarted" in source
    assert "startup never restores an older saved account" in source
    # Only drawn when it is not simply restating the row marked ◆ active below it.
    assert "current?.state!=='saved'&&<div class={`account-current" in source
    # The standing paragraph under the two add buttons became their tooltips, and
    # the optional label input is gone - the daemon names a slot from the identity
    # it just verified, and the list row renames in place.
    assert 'class="account-help"' not in source
    assert "optional label" not in source
    assert "sign in + save" in source
    assert "already signed in? save current login" in source
    # `/verify` is one install-wide endpoint, so it is one button, not one per
    # provider heading sharing a busy key with its twin.
    assert source.count("'/api/provider-accounts/verify'") == 1
    assert 'class="account-settings-head"' in source
    assert ".account-explainer>summary{" in css


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


def test_status_block_draws_only_providers_this_machine_has_a_credential_for() -> None:
    """Two rows reporting "signed out" is a feature advertising itself.

    `providers` is the inventory of what mux *can* manage and is two entries from
    the first launch, so a machine that has never signed in to either drew two
    sidebar rows, two `—` chips on the collapsed rail, and two more on the phone's
    toolbar. Visibility is derived from whether a credential exists on the daemon
    host rather than remembered, so signing in to one provider brings back that
    provider's row and not the other's.
    """
    display = (ROOT / "frontend" / "src" / "providerAccountDisplay.ts").read_text(
        encoding="utf-8"
    )
    source = (ROOT / "frontend" / "src" / "ProviderAccounts.tsx").read_text(
        encoding="utf-8"
    )
    css = (ROOT / "frontend" / "src" / "style.css").read_text(encoding="utf-8")

    assert "export function visibleProviders" in display
    # `unreadable` is a credential that exists and cannot be parsed: a problem to
    # report, not an absence to hide. `signed_out` is the only absence.
    assert "const PRESENT_AUTH_STATES=new Set(['saved','external','unreadable'])" in display
    # All three surfaces read the same list, or "hidden" would mean one of them:
    # the collapsed rail, the phone's toolbar, and the expanded sidebar's rows.
    assert source.count("visible.map(provider=>") == 3
    assert "{visible.map(provider=>{const account=selected(provider)" in source
    # The full inventory still reaches the popover and Settings, which have to offer
    # a sign-in for a provider that has no credential yet.
    assert "const providers=status?.providers||[]" in source
    # The condensed surfaces render nothing rather than a call to action neither has
    # room for; the expanded sidebar carries the invitation for all three. Nothing to
    # draw and nothing to invite with is nothing at all - an empty grid still occupies
    # a row of the status block.
    assert "const invite=!compact&&!!status&&!visible.length" in source
    assert "if(!visible.length&&!invite&&!open)return null" in source
    assert 'class="account-prompt"' in source
    assert ".account-prompt{" in css
    assert ".sidebar-status .account-prompt-actions button{min-height:44px}" in css


def test_the_account_invitation_is_dismissed_machine_side_and_answers_itself() -> None:
    """Hiding puts away the invitation, not the feature.

    The status block is derived from whether a credential exists, so signing in
    later brings the quota rows back whatever the flag says - which is why there is
    deliberately no control to un-hide it. Machine-side for the reason the quest
    dismissals are: the credentials it invites you to add are the daemon host's, so
    putting it away at the desk must put it away on the phone too.
    """
    app = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    source = (ROOT / "frontend" / "src" / "ProviderAccounts.tsx").read_text(
        encoding="utf-8"
    )
    config = (ROOT / "src" / "swe_mux" / "config.py").read_text(encoding="utf-8")

    assert "provider_accounts_prompt_dismissed: bool = False" in config
    assert "{ provider_accounts_prompt_dismissed: true }" in app
    assert "config.provider_accounts_prompt_dismissed === true" in app
    # Held while a first-run surface is up: the tour has an account step of its own,
    # and two invitations to the same thing at once is the overwhelm first-run exists
    # to remove.
    assert "promptSuppressed={firstRun!=='none'}" in app
    assert "!promptDismissed&&!promptSuppressed" in source


def test_the_popover_counts_sessions_per_account_without_claiming_identity() -> None:
    """The count says where each session started; the daemon says what a switch did to it.

    Deliberately "spawned under", never "using": mux stamps what it had selected
    when the process started, and cannot see a `/login` typed inside a pane. Whether
    the process then follows a switch is the CLI's behaviour, so the sentence under
    the count reads `switch_reaches_live` from the payload rather than assuming one
    answer for both providers - the assumption that put a false sentence under every
    Claude count.
    """
    display = (ROOT / "frontend" / "src" / "providerAccountDisplay.ts").read_text(
        encoding="utf-8"
    )
    source = (ROOT / "frontend" / "src" / "ProviderAccounts.tsx").read_text(
        encoding="utf-8"
    )
    css = (ROOT / "frontend" / "src" / "style.css").read_text(encoding="utf-8")

    assert "export function strandedSessions" in display
    assert "spawnedSessionCount" in display
    # Counted by the daemon and carried on the accounts payload, so the phone, the
    # popover and Settings cannot disagree about the number.
    assert "sessions?:AccountSessionCounts" in source
    assert "spawnedSessionCount(status?.sessions,account.id)" in source
    assert "strandedSessions(status,provider).map" in source
    assert "not proof of what it authenticates as now" in source
    # Three sentences, chosen by the daemon's per-provider fact and never by a
    # provider name in the browser: follows, does not, and unknown.
    assert "switch_reaches_live?:Record<ProviderName,boolean>" in source
    assert "reachesLive=status?.switch_reaches_live?.[provider]" in source
    assert "cli:harnessDisplayName(provider)" in source
    assert "if(reach.reachesLive===true)" in display
    assert "if(reach.reachesLive===false)" in display
    assert "spending the selected account now" in display
    assert "until restarted." in display
    assert "restart to be sure." in display
    assert "not retroactive" not in display
    assert ".account-popover .account-session-count{" in css
    assert ".account-popover .account-session-notice{" in css
    assert ".account-popover .account-session-notice.follows{" in css
