# Provider accounts

## What it is

- App-owned credential snapshots for harnesses that explicitly declare provider-account management, reconciled against one live system login per provider.
- The provider inventory comes from the independent `provider_account_management` descriptor capability, not the derived `managed` level.
- Claude Code and Codex are the current providers with account-manager implementations.
- OMP is report-only.
  Its session and history surfaces expose the current message provider, model, exact native cost, and each `credential_pin` provider-to-pseudonymous-hash mapping.
  The hashes are linkable SHA-256 account/scope identifiers, not anonymous values, and raw account identities are never copied into mux.
  Mux does not start OMP's auth broker, generate account-pool files, select OMP accounts, or poll broker usage.
  This avoids the broker's default collision with mux on `127.0.0.1:8765` and preserves OMP's own account-routing policy.
- Provider CLI discovery uses the registry-backed `harness_exe` configuration map.
- Continuous durable quota tracking for every saved account. Selection never creates isolated
  config, skill, project, or transcript directories.

## Scope and terms

The feature is **one operator switching between accounts they personally own**, replacing
the manual logout/login cycle the provider CLIs otherwise require.
Read cold - by a reviewer, an investor's diligence scan, or a provider - it could instead be
read as account pooling or rate-limit circumvention, so the boundaries that make it the
former are design constraints rather than incidental behaviour, and each is enforced by the
model rather than by convention.

- **One live system login per provider, always.** There is no concurrent multi-account
  execution: sessions run against whatever single credential is in the provider's normal auth
  file. Selection replaces that file; it does not multiplex over it.
- **Switching is an explicit user action.** No code path selects an account in response to a
  quota reading, an exhausted limit, or a failed request. Quota polling is observation that
  informs the operator, never an input to selection.
- **No pooling and no sharing.** Saved snapshots live in the operator's own data directory on
  their own machine. Nothing distributes credentials between people or machines, and the
  tailnet surface exposes no credential material.
- **Credentials are sent only to the provider that issued them**, for identity verification
  and quota reads (`CLAUDE_USAGE_URL`, `CLAUDE_PROFILE_URL`, `CODEX_USAGE_URL` in
  `provider_accounts.py`). swe-mux proxies no provider traffic and resells no access.
- **Each account remains subject to the operator's own agreement with that provider**, which
  swe-mux neither modifies nor mediates. The same holds for the optional OpenRouter and
  Hugging Face integrations: the user's own key, the user's own quota, the user's own
  agreement.

Quota polling reaches the endpoints the provider CLIs themselves use rather than a documented
public API, so those endpoints can change or be withdrawn without notice.
The **Failure modes** rule "provider/network/auth failure ⇒ stale-retention policy; no account
switch" is what keeps that dependency from being load-bearing: an unreadable quota degrades to
the last good reading and changes no state.

## Key concepts

- Saved account: identity metadata plus one private provider auth-file snapshot.
- Live system auth: credentials currently present in the provider's normal system auth file;
  always authoritative.
- Verified identity: the owning provider account resolved by asking the provider *with that
  credential* (Claude OAuth profile endpoint; Codex token claims). Recorded as
  `identity_source: token` and keyed on the provider's account UUID, never an organization
  UUID, because several logins can share one organization.
- Weak identity: an email or organization read from the provider CLI's cached profile or from
  legacy credential-file fields. It describes machine-global state that any provider process can
  rewrite, so it may label and hint but never authorize a credential write.
- Selected account: saved account matching live system auth by exact digest or verified identity.
- External login: valid live system auth that does not match a saved account by either strong
  rule, including one that a weak identity merely resembles.
- Duplicate account: two saved slots whose verified identities are the same provider account.
- Quota sample: append-only retained observation of session/weekly utilization, reset times,
  source, freshness, raw precision, error, active-account state, and auth state.

## Data model

- `<data_dir>/provider-accounts.json`: versioned (v2) account metadata including identity
  provenance, selected IDs, recent compatibility quota state, private auth digests, and a
  bounded digest→identity map; atomic replacement. v1 manifests migrate in place: Codex IDs are
  kept, Claude organization-scoped IDs are dropped as unverified.
- `<data_dir>/mux.db`: durable quota samples/rollups, reset evidence, and probabilistic
  mux-activity attribution. The latest account quota API derives from these samples. Each sample
  records the verified provider account it describes alongside the local slot it was filed
  under, so a slot that changes hands cannot silently re-attribute its own history; reset
  detection only compares samples of the same verified account. Removing an account purges its
  rows, and a bounded purge can discard only the period after credentials changed hands.
- `<data_dir>/provider-accounts/claude/<id>/.credentials.json`: Claude auth snapshot.
- `<data_dir>/provider-accounts/claude/<id>/oauth-account.json`: snapshot of the CLI's cached
  profile block (`oauthAccount` from `~/.claude.json`), saved only when token-verified as this
  slot's owner; restored into the CLI config on selection.
- `<data_dir>/provider-accounts/codex/<id>/auth.json`: Codex auth snapshot.
- `<snapshot>.prev`: the credential a slot held before its most recent replacement.
- `<data_dir>/provider-accounts/credential-events.jsonl`: append-only, rotated audit of every
  credential-affecting decision (action, matched-by, truncated digests, non-secret identity).
- System selection targets: `~/.claude/.credentials.json`, `~/.codex/auth.json`.
- Public snapshots omit auth digests, credential contents, and filesystem paths.
- Public `current` state per provider: `saved | external | signed_out | unreadable`, matched
  account ID when saved, and non-secret identity metadata when available.

## Operations

- Sign in + save runs the provider's ordinary host login command, then captures the
  resulting system auth file. Save current login captures without launching login.
- **Every failure on that path is durable, and none of it is the payload.** A provider CLI
  that could not be started, timed out, or exited nonzero used to exist only in the HTTP
  response body of whoever happened to ask; `daemon.log` held nothing about any of it, which
  is why four failures on WSL Ubuntu took about an hour of manual archaeology on 2026-08-28.
  Unstartable is an ERROR carrying the configured value, what resolution found, and why it
  was rejected (`shim_paths.resolve_executable`); a timeout and a nonzero exit are WARNINGs
  carrying the fixed argv, the exit code, and a bounded **stderr** tail. A login start and a
  successful capture are INFO. Provider **stdout** is never logged - it is where a token or a
  credential blob would be, even when a failure printed nothing else - and the bound is the
  same `DIAGNOSTIC_TAIL_CHARS` the operator-facing error already used, so the log can never
  quietly carry more of a third-party CLI's output than the error does.
- Executable resolution refuses rather than falls through. A configured value that resolves
  only to swe-mux's own agent shim, or to a Windows binary reached through WSL interop, fails
  the operation with the resolver's own sentence instead of being exec'd anyway - running the
  binary that was just refused is how the shim recursed into itself, and on WSL it is how a
  Windows CLI would be driven from a Linux daemon. A stale `.exe` suffix is stripped and
  retried first, on every host (`backends.md` § Executable resolution).
- Selection atomically replaces only the normal system auth file. Provider config,
  skills, sessions, projects, and histories remain shared. It is never refused and never asks
  for confirmation, including while live sessions of that provider are running: those processes
  re-read the shared credential file when its mtime changes, so they follow the switch without
  being restarted.
- Cached-profile restore (Claude): the CLI shows identity (`/status`, browser bridge) from the
  `oauthAccount` block in `~/.claude.json`, not from the credential file, and refetches it at
  most daily — a credential swap alone leaves every surface naming the outgoing account for up
  to a day. Each saved Claude account therefore keeps a snapshot of that block
  (`oauth-account.json`, captured only when its `accountUuid` equals the slot's token-verified
  owner; refreshed on capture, adopt, and each quota poll of the selected account). Selection
  and the guard's re-assert restore it: only the `oauthAccount` key is rewritten, a block
  already naming the right account is left alone, and an absent or unparseable config is never
  written. With no usable snapshot, a minimal verified-identity block without
  `profileFetchedAt` is written instead — it fails the CLI's 24h freshness gate, forcing a
  refetch that self-corrects on the next session start. Restores are audited as
  `oauth_profile_restored`. Panes already running hold their config in memory and keep the old
  display until restarted; their requests still authenticate as the new account.
- Selection guard: a switch made while live sessions exist is defended for
  `SELECTION_GUARD_SECONDS` (60s). The one case that survives the mtime re-read is a token
  refresh already in flight when the swap lands — it completes with the outgoing account's
  refresh token and writes that back, silently reverting the switch. The guard polls
  reconciliation and re-applies the selection, audited as `selection_reasserted`, but only when
  the live login positively resolves to a *different saved account*; an `external` login is left
  alone, because it may hold a newer token than any saved snapshot. A newer deliberate
  selection, capture, or adoption retires the guard, so the two never fight over the file.
- Capture resolves the owner from the credential first, then falls back to weaker readings. It
  deduplicates on verified account ID, then on weak identity only when no verified ID exists,
  then on exact auth digest, so one provider account occupies exactly one slot. Capturing into
  an explicit `replace_id` fails when the credentials belong to another saved account.
- Daemon startup, provider-status reads, and quota refreshes read live system auth before other
  account work. Only two matches move credentials into a saved snapshot: an exact digest match,
  or a verified identity that uniquely names one saved account. Everything else is an external
  login. A weak identity that resembles exactly one saved account is reported as a relink hint
  and applied only by explicit adoption, which re-checks ownership first. This is deliberate:
  acting on a CLI-cached email is how a rotation belonging to one account overwrote another
  account's saved snapshot and produced two slots reporting one account's usage.
- An unrecognized live login is identified by asking the provider with that credential. The
  credential digest is re-read afterwards and a reading is cached only against the digest it was
  derived from; a credential that changed mid-probe causes one retry. Failed probes are briefly
  rate-limited on ordinary status reads, while an explicit full refresh retries immediately.
  Missing or malformed credentials clear saved selection. Reconciliation never copies an older
  saved snapshot into system auth, and never writes credentials from a snapshot read.
- Every credential write to a saved snapshot retains the previous file and appends an audit
  entry naming the action, the match that authorized it, and truncated digests.
- Every configured polling interval (15 minutes by default) quota polling reconciles again,
  then refreshes selected accounts first and inactive accounts sequentially. A refresh
  re-derives a slot's owner whenever that slot's credential changed, so a swapped-in token is
  detected rather than reported as a second account's usage. Optional refresh after an eligible
  root turn is disabled by default and globally rate-limited; subagent completion never triggers
  it.
- Saved accounts resolving to the same provider account are marked as duplicates. The primary is
  the selected one, otherwise the oldest; the rest stop being polled, report quota status
  `conflict`, and are excluded from durable-sample display rather than repeating the primary's
  numbers.
- Claude uses the OAuth usage endpoint. An authorization failure may rotate the public
  Claude Code refresh token — but never for the selected account while live Claude sessions
  run: those CLIs rotate the same refresh token themselves, and a managed rotation racing that
  writes a dead credential over a live one. The gated refresh fails with a typed error and
  quota goes stale until the CLI rotates and reconciliation syncs the slot. Otherwise rotated
  credentials update the saved snapshot and, when still matched to live system auth, the
  system auth file. A login changed during the network
  request is never overwritten: the slot's digest is re-checked against the one read at the
  start of the refresh, and a mismatch skips the write with an audited `rotation_skipped`.
  The rotation write itself keeps the previous credential as `.prev` and appends an audit
  entry like every other credential write — a background rotation is exactly the silent
  rewrite the audit trail exists to explain afterwards.
- Codex uses the CLI backend usage endpoint. Authorization failure falls back to a bounded
  `codex app-server` JSON-RPC request in a temporary auth-only `CODEX_HOME`; refreshed auth
  is copied back without retaining the temporary home.
- A failed refresh preserves the last success as `stale` for 30 minutes. Older data clears
  to `error`. Terminal/session operations never wait for background polling.
- The quota poll and turn-refresh loops run under the shared background-task supervisor, so a
  single failure costs one cycle rather than ending quota sampling, reset detection and
  managed-token rotation for the daemon's lifetime. The manifest's atomic replace additionally
  retries a transient Windows `PermissionError` (antivirus or a backup agent holding the
  destination), which was the concrete way that loop died. Loop liveness and last fault are at
  `GET /api/diagnostics/background`.
- A downward movement is scheduled when its advertised reset boundary falls between the two
  fresh samples, so a delayed poll does not mislabel it. Unexpected candidates require a reset
  timer at least one hour away, a drop of at least 20 percentage points, and a landing no higher
  than 10%; confirmation then requires a same-account/auth stable-low sample 5–45 minutes later
  while still before that boundary. Missing/ambiguous timers, stale or out-of-order data,
  rebounds, auth transitions, and account changes suppress confirmation.
- Confirmed unexpected resets expose a purple UI indicator and an optional sound. A provider
  rolls its whole plan over at once, so alerts are coalesced per provider before they are
  raised and the indicator carries the whole unreviewed group rather than its newest row.
  Attribution shows estimate ranges, ambiguity, provider lag, and an
  explicit external/unassigned remainder; it never claims shared-account identity.
- The account popover reviews the whole group in one action: `seen` acknowledges it,
  `manual_usage` classifies Codex rows, `discarded` treats it as a detection error. Review is
  server-persisted, so a dismissal at the desk also clears the phone; it removes the rows from
  active notifications, retains them in the evidence log, and rejects manual-usage
  classification for Claude rows.
- `POST .../select` takes no body; there is no force flag and no confirmation step.
- The expanded sidebar's status block uses one two-row metric grid per provider.
  The first row shows the provider icon, 5-hour reset countdown, weekly reset countdown, and optional Fable heading.
  The second row shows the selected account label's first four characters followed by the corresponding usage percentages.
  Each column centres its two rows on each other, so a percentage sits under the middle of its reset countdown and the icon over the middle of the account abbreviation rather than both being start-aligned against strings of different lengths.
  Column tracks are sized in `ch`, not pixels, because `.app-shell *` forces `--ui-font-size` onto this surface with `!important`: the 8 px monospace the block's own rule requests is never what renders, and tracks cut for it were narrower than their own content, so every countdown overflowed into the gutter and columns nearly touched.
  A track that a narrow sidebar cannot seat shrinks and ellipsizes within itself, keeping the gutter, rather than overrunning its neighbour; rows with different window counts therefore share one track width whenever they fit, which covers the shipped sidebar width at every chrome scale.
  `frontend/test/renderer/quota-grid-layout.spec.ts` measures the separation, the centring, and both regimes, because this geometry is pure CSS and invisible to unit tests.
  Both condensed surfaces — the collapsed desktop rail and the mobile toolbar — instead draw one 28 px square per provider, icon above weekly percentage: the rail's width cannot contain the grid, and the phone's toolbar cannot spare the row (see `ui.md` § quota indicators). Either square opens the same popover, which is where the window-by-window reading lives for a device with no hover.
  The full switcher is a viewport-level overlay anchored to the status block, so sidebar width and overflow never clip it.
- The Usage dashboard queries durable quota history by provider, saved local account, date range, and raw/daily resolution.
- Friendly saved-account labels are shown beside verified provider identity, while legacy samples without provider identity are marked unverified.
- Separate 5-hour and weekly timelines include reset markers and daily first/last/min/max/sample-count summaries.
- These account-specific charts describe quota utilization only; they are not joined to `ccusage` historical token or model totals.
- Removing the selected saved account removes mux ownership metadata and its private
  snapshot; live system auth remains untouched and becomes external.

## API surface

```text
GET    /api/provider-accounts
GET    /api/provider-accounts/audit
POST   /api/provider-accounts/refresh
POST   /api/provider-accounts/verify
POST   /api/provider-accounts/{provider}/capture
POST   /api/provider-accounts/{provider}/login
PATCH  /api/provider-accounts/{provider}/{account-id}
POST   /api/provider-accounts/{provider}/{account-id}/select
POST   /api/provider-accounts/{provider}/{account-id}/adopt
POST   /api/provider-accounts/{provider}/{account-id}/purge-telemetry
DELETE /api/provider-accounts/{provider}/{account-id}
POST   /api/telemetry/quota-resets/review
GET    /api/telemetry/quota-series?provider=&account=&since=&until=&resolution=raw|daily
```

`mux accounts [list|verify|audit]` reaches the same surface from the CLI.

```ts
type CurrentProviderAuth = {
  state: "saved" | "external" | "signed_out" | "unreadable"
  account_id: string | null
  email: string | null
  provider_account_id: string | null
  organization: string | null
  identity_source: "token" | "cli" | "file" | null
  match_hint: { account_id: string; label: string | null; reason: string } | null
}

type AccountConflict = {
  kind: "duplicate_account"
  provider_account_id: string
  primary_id: string
  is_primary: boolean
  account_ids: string[]
}
```

## Failure modes

- Missing/malformed system credentials ⇒ capture fails without registry mutation.
- Missing/unreadable credentials during reconciliation ⇒ typed current state; remembered
  selection clears; system auth is never written.
- Login cancel, timeout, or CLI failure ⇒ visible Settings error; existing accounts remain.
- Identity verification failure or repeated credential change during reconciliation ⇒ valid login
  remains external; daemon startup and terminal use continue, and a later refresh retries safely.
  No credential is written on an unverified guess.
- Weak identity resembling a saved account ⇒ relink hint only; adoption re-checks ownership and
  refuses credentials belonging to another saved account.
- Switching under live sessions ⇒ always applied; a straggling rotation from the outgoing login
  is undone by the selection guard within its window, and an unidentifiable live login is left
  in place rather than overwritten from a saved snapshot.
- Two slots resolving to one provider account ⇒ both flagged; the duplicate stops polling instead
  of repeating the primary's usage.
- Provider/network/auth failure ⇒ stale-retention policy; no account switch.
- Selected Claude account's token expired while sessions are live ⇒ quota reads fail typed
  ("a live session owns this token's rotation") instead of racing the CLI's own refresh.
- `~/.claude.json` absent or unparseable during a switch ⇒ profile restore skipped; the CLI
  rebuilds its own config and `/status` self-corrects on its next profile refetch.
- Private snapshot sync failure ⇒ live login remains external; no claim that stale saved auth
  is active.
- Missing Codex app-server ⇒ direct quota failure remains visible; terminal use unaffected.

## Key files

- Service: `src/swe_mux/provider_accounts.py`
- Durable evidence: `src/swe_mux/operational_telemetry.py`
- Composition/routes: `src/swe_mux/server.py`
- CLI: `src/swe_mux/cli.py` (`mux accounts`)
- UI: `frontend/src/ProviderAccounts.tsx`, `frontend/src/providerAccountDisplay.ts`,
  `frontend/src/style.css`
- Tests: `tests/test_provider_accounts.py`,
  `tests/test_frontend_provider_accounts_contract.py`

## Relates to

- `usage.md`: historical ccusage analytics remain a separate subsystem.
- `backends.md`: provider execution continues to use ordinary shared homes.
- `ui.md`: bottom-sidebar provider rows and the mobile account control expose selection.
