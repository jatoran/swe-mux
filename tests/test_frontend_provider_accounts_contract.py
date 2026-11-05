from __future__ import annotations

from pathlib import Path

from tests.support.frontend_source import localized_source, source_contains

ROOT = Path(__file__).parents[1]


def test_provider_account_ui_distinguishes_live_external_and_saved_auth() -> None:
    source = localized_source(
        (ROOT / "frontend" / "src" / "ProviderAccounts.tsx").read_text(encoding="utf-8")
    )

    assert source_contains(source, "state:'saved'|'external'|'signed_out'|'unreadable'")
    assert source_contains(source, "current:Record<ProviderName,CurrentProviderAccount>")
    assert source_contains(source, "external / unsaved")
    assert source_contains(source, "LIVE SYSTEM AUTH")
    assert source_contains(source, "startup never restores an older saved account")


def test_provider_account_ui_surfaces_identity_verification_and_duplicates() -> None:
    source = localized_source(
        (ROOT / "frontend" / "src" / "ProviderAccounts.tsx").read_text(encoding="utf-8")
    )
    css = localized_source((ROOT / "frontend" / "src" / "style.css").read_text(encoding="utf-8"))

    assert source_contains(source, "identity_source?:IdentitySource|null")
    assert source_contains(source, "conflict?:AccountConflict|null")
    assert source_contains(source, "match_hint?:MatchHint|null")
    # Two identity states, so exactly two words, with the sentence in the tooltip.
    # This used to be three strings ("verified with the provider", "unverified
    # identity", "identity unverified") for those same two states, which reads on a
    # crowded row as three distinct conditions.
    assert source_contains(source, "account.identity_source==='token'?'verified':'unverified'")
    assert source_contains(
        source, "Identity confirmed by asking the provider with these credentials."
    )
    assert source_contains(source, "Identity has not been confirmed against the provider yet.")
    assert source_contains(source, "Quota polling is suspended")
    assert source_contains(source, "relink only if this really is that account")
    assert source_contains(source, "/adopt")
    assert source_contains(source, "'/api/provider-accounts/verify'")
    # Switching is unconditional: no force flag, no "switch anyway" confirmation.
    assert not source_contains(source, "force")
    # And it says what a switch does to processes already running per CLI rather than
    # for both at once: Claude Code re-reads its credential file on an mtime change
    # and follows; Codex keeps the token it read at startup. The disclosure has been
    # wrong in both directions before, so both halves are pinned.
    assert source_contains(
        source,
        "It is never blocked and never confirmed. "
        "Whether it reaches sessions already running is up to the CLI",
    )
    assert source_contains(source, "Claude Code re-reads its credential file when the file changes")
    assert source_contains(source, "Codex reads its login once at startup")
    assert not source_contains(source, "not retroactive")
    assert not source_contains(source, "Sessions already running follow the switch")
    assert source_contains(css, ".account-conflict{")
    assert source_contains(css, ".account-identity.verified{")


def test_account_switcher_can_start_a_sign_in_without_opening_settings() -> None:
    """The popover is a way in, not only a way to switch between what exists.

    With nothing saved it used to print "No saved accounts" beside a `manage…`
    button - which is the one screen a new install always lands on, and the one
    with no path forward on it.
    """
    source = localized_source(
        (ROOT / "frontend" / "src" / "ProviderAccounts.tsx").read_text(encoding="utf-8")
    )
    css = localized_source((ROOT / "frontend" / "src" / "style.css").read_text(encoding="utf-8"))

    assert source_contains(source, "useProviderLogin")
    assert source_contains(source, "/login/dismiss")
    # Per-provider control on the heading line, and the empty state itself is the
    # call to action rather than a sentence about being empty.
    assert source_contains(source, 'class="account-section-head"')
    assert source_contains(source, 'class="account-signin"')
    assert source_contains(source, 'class="account-empty-cta"')
    assert source_contains(source, "sign in to {provider}")
    assert not source_contains(source, "<p>No saved accounts</p>")
    assert source_contains(css, ".account-section-head{")
    assert source_contains(css, ".account-popover button.account-empty-cta{")


def test_running_sign_in_is_daemon_state_that_every_client_sees() -> None:
    """A login outlives the request that started it, so its progress is polled.

    The provider CLI can hold the daemon for `LOGIN_TIMEOUT_SECONDS` while a human
    finishes an OAuth flow. While that was one blocked HTTP request, whoever asked
    owned the only copy of the outcome: closing the panel, reloading, or asking
    from a second device lost it.
    """
    source = localized_source(
        (ROOT / "frontend" / "src" / "ProviderAccounts.tsx").read_text(encoding="utf-8")
    )
    display = localized_source(
        (ROOT / "frontend" / "src" / "providerAccountDisplay.ts").read_text(encoding="utf-8")
    )
    css = localized_source((ROOT / "frontend" / "src" / "style.css").read_text(encoding="utf-8"))

    assert source_contains(source, "login?:Record<ProviderName,LoginState|null>")
    assert source_contains(display, "state:'running'|'succeeded'|'failed'")
    assert source_contains(source, "function LoginProgress")
    # Both surfaces draw the same one.
    assert source.count("<LoginProgress ") == 2
    # A running sign-in resolves on human time, so it gets its own poll cadence.
    assert source_contains(source, "const LOGIN_POLL_MS")
    assert source_contains(source, "awaitingLogin?LOGIN_POLL_MS:idle?idleMs:intervalMs")
    for state in ("running", "succeeded", "failed"):
        assert f".account-login.{state}{{" in css
    # The command the tooltip names is the daemon's, built from the configured
    # executable. A copy compiled into the browser would still have named the shipped
    # default on an install that repointed `harness_exe` - which is the drift the
    # harness-name rule exists to stop.
    assert source_contains(source, "login_commands?:Record<ProviderName,string>")
    assert source_contains(source, "signInTitle(status?.login_commands,provider)")
    assert not source_contains(source, "claudeai")


def test_account_settings_states_policy_once_and_only_where_it_acts() -> None:
    """The panel's prose was ~160 static words before any control, with two providers.

    What is left is the part that changes: the live-auth block now renders only in
    the states that need explaining and carry the relink action, the reference text
    folds away, and the two add buttons carry their own explanation.
    """
    source = localized_source(
        (ROOT / "frontend" / "src" / "ProviderAccounts.tsx").read_text(encoding="utf-8")
    )
    css = localized_source((ROOT / "frontend" / "src" / "style.css").read_text(encoding="utf-8"))

    # Reference, not instruction: folded, but still the same sentences.
    assert source_contains(source, 'class="account-explainer"')
    assert source_contains(source, "<summary>How switching works</summary>")
    assert source_contains(source, "keeps spending the outgoing account until it is restarted")
    assert source_contains(source, "startup never restores an older saved account")
    # Only drawn when it is not simply restating the row marked ◆ active below it.
    assert source_contains(source, "current?.state!=='saved'&&<div class={`account-current")
    # The standing paragraph under the two add buttons became their tooltips, and
    # the optional label input is gone - the daemon names a slot from the identity
    # it just verified, and the list row renames in place.
    assert not source_contains(source, 'class="account-help"')
    assert not source_contains(source, "optional label")
    assert source_contains(source, "sign in + save")
    assert source_contains(source, "already signed in? save current login")
    # `/verify` is one install-wide endpoint, so it is one button, not one per
    # provider heading sharing a busy key with its twin.
    assert source.count("'/api/provider-accounts/verify'") == 1
    assert source_contains(source, 'class="account-settings-head"')
    assert source_contains(css, ".account-explainer>summary{")


def test_provider_account_ui_marks_external_and_unreadable_states() -> None:
    css = localized_source((ROOT / "frontend" / "src" / "style.css").read_text(encoding="utf-8"))

    assert source_contains(css, ".account-summary>button.external")
    assert source_contains(css, ".account-current.external")
    assert source_contains(css, ".account-current.unreadable")


def test_sidebar_account_status_uses_separate_terminal_icon_rows_at_the_bottom() -> None:
    accounts = localized_source(
        (ROOT / "frontend" / "src" / "ProviderAccounts.tsx").read_text(encoding="utf-8")
    )
    app = localized_source((ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8"))
    css = localized_source((ROOT / "frontend" / "src" / "style.css").read_text(encoding="utf-8"))

    icons = localized_source(
        (ROOT / "frontend" / "src" / "harnessIcons.tsx").read_text(encoding="utf-8")
    )

    # One mark per harness, in one module. They used to live here, beside the switcher,
    # which knows only the harnesses that have provider accounts - so every other harness
    # fell back to its initial and `oh-my-pi` and `opencode` both drew as `O`.
    assert source_contains(accounts, "harnessMark")
    assert not source_contains(accounts, "providerGlyph")
    assert source_contains(icons, "stroke: 'currentColor',")
    assert source_contains(icons, 'class="provider-mark"')
    assert not source_contains(icons, "provider==='claude'?'✳'")
    for harness in ("claude", "codex", "pi", "omp", "opencode"):
        assert f"  {harness}: " in icons
    # An unknown harness is a daemon newer than this build, and its own initial is the
    # most a browser can honestly say about it.
    assert source_contains(icons, "harnessDisplayName(name).slice(0, 1).toUpperCase()")
    assert source_contains(accounts, "accountAbbreviation(currentLabel(current,account))")
    assert source_contains(accounts, 'class="quota-grid-column quota-grid-identity"')
    assert source_contains(accounts, 'class="quota-grid-column quota-grid-metric"')
    assert source_contains(accounts, "segment.heading")
    assert source_contains(accounts, "segment.text")
    assert source_contains(accounts, "{state!=='ready'&&<em>{state}</em>}")
    assert app.rfind("<AccountSwitcher onManage") > app.rfind('class="project-tree"')
    assert source_contains(css, ".account-summary{grid-template-columns:1fr}")
    assert source_contains(app, 'class="sidebar-status"')
    assert source_contains(css, ".sidebar-status{min-width:0;flex:none;margin-top:auto")


def test_sidebar_account_status_survives_the_mobile_breakpoint() -> None:
    """The drawer carries the same status block as desktop.

    Mobile used to hide it outright, leaving the toolbar chips as the only quota
    surface: they show each provider's usage but not which account is selected,
    nor owned-process usage — and the drawer is where a phone user goes looking
    for exactly that.
    """
    css = localized_source((ROOT / "frontend" / "src" / "style.css").read_text(encoding="utf-8"))

    assert not source_contains(css, ".sidebar-status{display:none}")
    # Rows become taps rather than hover targets, so they take the mobile touch floor.
    assert source_contains(
        css, ".sidebar-status>.account-switcher .account-summary>button{min-height:46px}"
    )
    assert source_contains(css, ".sidebar-status .resource-usage-summary{min-height:46px}")


def test_account_and_resource_switchers_escape_sidebar_as_viewport_popovers() -> None:
    accounts = localized_source(
        (ROOT / "frontend" / "src" / "ProviderAccounts.tsx").read_text(encoding="utf-8")
    )
    app = localized_source((ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8"))
    resources = localized_source(
        (ROOT / "frontend" / "src" / "ResourceUsage.tsx").read_text(encoding="utf-8")
    )
    css = localized_source((ROOT / "frontend" / "src" / "style.css").read_text(encoding="utf-8"))

    assert source_contains(accounts, "createPortal(popup,document.body)")
    assert source_contains(accounts, "accountPopoverStyle")
    assert source_contains(app, "ResourceUsageSummary")
    assert source_contains(resources, "createPortal(popup,document.body)")
    assert source_contains(resources, "anchoredPopoverStyle")
    assert source_contains(resources, "open process fleet…")
    # `ui-portal` is what keeps a body-portalled popover following the UI scale; see
    # test_frontend_ui_scale_contract for the rule it opts into.
    assert source_contains(resources, 'class="account-popover resource-usage-popover ui-portal"')
    assert source_contains(css, ".account-popover{position:fixed")
    assert source_contains(css, ".resource-usage-popover>section{padding:0}")


def test_expanded_resource_summary_is_one_icon_led_row_with_full_hover_copy() -> None:
    resources = localized_source(
        (ROOT / "frontend" / "src" / "ResourceUsage.tsx").read_text(encoding="utf-8")
    )
    css = localized_source((ROOT / "frontend" / "src" / "style.css").read_text(encoding="utf-8"))

    assert source_contains(resources, 'class="resource-session-count"')
    assert source_contains(resources, "<SessionsIcon/>")
    # The process count lives only in the popover: the closed row is sessions, CPU, RAM.
    assert not source_contains(resources, "resource-process-count")
    assert not source_contains(resources, "ProcessesIcon")
    assert source_contains(resources, "<CpuIcon/>")
    assert source_contains(resources, "<RamIcon/>")
    assert source_contains(resources, "click to see usage details")
    assert not source_contains(resources, '<div class="resource-usage-head">')
    assert source_contains(
        css, ".resource-usage-summary{width:100%;min-width:0;min-height:34px;display:flex"
    )


def test_resource_popover_is_three_figures_with_one_ram_box() -> None:
    """The popover stays small: system CPU, one combined RAM box, process count.

    The per-Project, daemon/infrastructure, and duplicated-tooling breakdowns
    and the scope-explanation note were removed 2026-08-26; the Resources
    dialog's Processes segment is the detail surface. The single RAM box
    prefers the reclaimable (unique-set) reading the open panel samples and
    falls back to the working set.
    """
    resources = localized_source(
        (ROOT / "frontend" / "src" / "ResourceUsage.tsx").read_text(encoding="utf-8")
    )
    css = localized_source((ROOT / "frontend" / "src" / "style.css").read_text(encoding="utf-8"))

    assert source_contains(resources, "SYSTEM CPU")
    assert source_contains(resources, "<article><span>RAM</span>")
    assert source_contains(resources, "reclaimableRam??combined.memory_bytes")
    assert not source_contains(resources, "RECLAIMABLE RAM")
    assert not source_contains(resources, "WORKING SET")
    assert not source_contains(resources, "CPU is whole-system load.")
    assert not source_contains(resources, "daemon + infrastructure")
    assert not source_contains(resources, "by project")
    assert not source_contains(resources, "duplicated per-session tooling")
    assert not source_contains(resources, "OWNED RAM")
    assert not source_contains(resources, "OWNED PROC")
    assert source_contains(css, "grid-template-columns:repeat(auto-fit,minmax(72px,1fr))")


def test_status_block_draws_only_providers_this_machine_has_a_credential_for() -> None:
    """Two rows reporting "signed out" is a feature advertising itself.

    `providers` is the inventory of what mux *can* manage and is two entries from
    the first launch, so a machine that has never signed in to either drew two
    sidebar rows, two `—` chips on the collapsed rail, and two more on the phone's
    toolbar. Visibility is derived from whether a credential exists on the daemon
    host rather than remembered, so signing in to one provider brings back that
    provider's row and not the other's.
    """
    display = localized_source(
        (ROOT / "frontend" / "src" / "providerAccountDisplay.ts").read_text(encoding="utf-8")
    )
    source = localized_source(
        (ROOT / "frontend" / "src" / "ProviderAccounts.tsx").read_text(encoding="utf-8")
    )
    css = localized_source((ROOT / "frontend" / "src" / "style.css").read_text(encoding="utf-8"))

    assert source_contains(display, "export function visibleProviders")
    # `unreadable` is a credential that exists and cannot be parsed: a problem to
    # report, not an absence to hide. `signed_out` is the only absence.
    assert source_contains(
        display, "const PRESENT_AUTH_STATES=new Set(['saved','external','unreadable'])"
    )
    # All three surfaces read the same list, or "hidden" would mean one of them:
    # the collapsed rail, the phone's toolbar, and the expanded sidebar's rows.
    assert source.count("visible.map(provider=>") == 3
    assert source_contains(source, "{visible.map(provider=>{const account=selected(provider)")
    # The full inventory still reaches the popover and Settings, which have to offer
    # a sign-in for a provider that has no credential yet.
    assert source_contains(source, "const providers=status?.providers||[]")
    # The condensed surfaces render nothing rather than a call to action neither has
    # room for; the expanded sidebar carries the invitation for all three. Nothing to
    # draw and nothing to invite with is nothing at all - an empty grid still occupies
    # a row of the status block.
    assert source_contains(source, "const invite=!compact&&!!status&&!visible.length")
    assert source_contains(source, "if(!visible.length&&!invite&&!open)return null")
    assert source_contains(source, 'class="account-prompt"')
    assert source_contains(css, ".account-prompt{")
    assert source_contains(css, ".sidebar-status .account-prompt-actions button{min-height:44px}")


def test_the_account_invitation_is_dismissed_machine_side_and_answers_itself() -> None:
    """Hiding puts away the invitation, not the feature.

    The status block is derived from whether a credential exists, so signing in
    later brings the quota rows back whatever the flag says - which is why there is
    deliberately no control to un-hide it. Machine-side for the reason the quest
    dismissals are: the credentials it invites you to add are the daemon host's, so
    putting it away at the desk must put it away on the phone too.
    """
    app = localized_source((ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8"))
    source = localized_source(
        (ROOT / "frontend" / "src" / "ProviderAccounts.tsx").read_text(encoding="utf-8")
    )
    config = localized_source((ROOT / "src" / "swe_mux" / "config.py").read_text(encoding="utf-8"))

    assert source_contains(config, "provider_accounts_prompt_dismissed: bool = False")
    assert source_contains(app, "{ provider_accounts_prompt_dismissed: true }")
    assert source_contains(app, "config.provider_accounts_prompt_dismissed === true")
    # Held while a first-run surface is up: the tour has an account step of its own,
    # and two invitations to the same thing at once is the overwhelm first-run exists
    # to remove.
    assert source_contains(app, "promptSuppressed={firstRun!=='none'}")
    assert source_contains(source, "!promptDismissed&&!promptSuppressed")


def test_the_popover_counts_sessions_per_account_without_claiming_identity() -> None:
    """The count says where each session started; the daemon says what a switch did to it.

    Deliberately "spawned under", never "using": mux stamps what it had selected
    when the process started, and cannot see a `/login` typed inside a pane. Whether
    the process then follows a switch is the CLI's behaviour, so the sentence under
    the count reads `switch_reaches_live` from the payload rather than assuming one
    answer for both providers - the assumption that put a false sentence under every
    Claude count.
    """
    display = localized_source(
        (ROOT / "frontend" / "src" / "providerAccountDisplay.ts").read_text(encoding="utf-8")
    )
    source = localized_source(
        (ROOT / "frontend" / "src" / "ProviderAccounts.tsx").read_text(encoding="utf-8")
    )
    css = localized_source((ROOT / "frontend" / "src" / "style.css").read_text(encoding="utf-8"))

    assert source_contains(display, "export function strandedSessions")
    assert source_contains(display, "spawnedSessionCount")
    # Counted by the daemon and carried on the accounts payload, so the phone, the
    # popover and Settings cannot disagree about the number.
    assert source_contains(source, "sessions?:AccountSessionCounts")
    assert source_contains(source, "spawnedSessionCount(status?.sessions,account.id)")
    assert source_contains(source, "not proof of what it authenticates as now")
    # One notice per stranded login, drawn only where the daemon's per-provider fact
    # says the CLI keeps the login it started with, and never chosen by a provider
    # name in the browser. A CLI that follows the switch gets no notice at all: the
    # operator asked for the "spending the selected account now" one to go, and it
    # was noise. Since 2026-09-08 the notice is a collapsed line that expands to the
    # sentence, dismisses for now, and can be hidden for good (the `notices` domain).
    assert source_contains(source, "switch_reaches_live?:Record<ProviderName,boolean>")
    assert source_contains(source, "strandedNotices(provider).map")
    assert source_contains(source, "<StrandedNotice key=")
    assert source_contains(source, "cli={harnessDisplayName(provider)}")
    assert source_contains(source, "strandedSessionNotice(row,cli)")
    assert source_contains(source, "strandedSessionSummary(row)")
    assert source_contains(source, "never show this again")
    assert source_contains(source, "setNoticeHidden('stranded-sessions',true)")
    # The undo lives in Settings -> Accounts, and it writes the same id the popover does.
    assert source_contains(
        source, "setNoticeHidden('stranded-sessions',!event.currentTarget.checked)"
    )
    assert source_contains(display, "still on ${row.label}")
    assert source_contains(
        display, "switch_reaches_live?.[provider]===false?strandedSessions(status,provider):[]"
    )
    assert source_contains(display, "until restarted.")
    assert not source_contains(display, "spending the selected account now")
    assert not source_contains(display, "not retroactive")
    assert source_contains(css, ".account-popover .account-session-count{")
    assert source_contains(css, ".account-popover .account-session-notice{")
    assert not source_contains(css, ".account-session-notice.follows")
