# Provider accounts

## What it is

- App-owned Claude Code and Codex credential snapshots reconciled against one live system
  login per provider.
- Continuous durable quota tracking for every saved account. Selection never creates isolated
  config, skill, project, or transcript directories.

## Key concepts

- Saved account: identity metadata plus one private provider auth-file snapshot.
- Live system auth: credentials currently present in the provider's normal system auth file;
  always authoritative.
- Selected account: saved account matching live system auth by exact digest or stable provider
  identity.
- External login: valid live system auth that does not match a saved account.
- Quota sample: append-only retained observation of session/weekly utilization, reset times,
  source, freshness, raw precision, error, active-account state, and auth state.

## Data model

- `<data_dir>/provider-accounts.json`: versioned account metadata, selected IDs, recent
  compatibility quota state, and private auth digests; atomic replacement.
- `<data_dir>/mux.db`: durable quota samples/rollups, reset evidence, and probabilistic
  mux-activity attribution. The latest account quota API derives from these samples.
- `<data_dir>/provider-accounts/claude/<id>/.credentials.json`: Claude auth snapshot.
- `<data_dir>/provider-accounts/codex/<id>/auth.json`: Codex auth snapshot.
- System selection targets: `~/.claude/.credentials.json`, `~/.codex/auth.json`.
- Public snapshots omit auth digests, credential contents, and filesystem paths.
- Public `current` state per provider: `saved | external | signed_out | unreadable`, matched
  account ID when saved, and non-secret identity metadata when available.

## Operations

- Sign in + save runs the provider's ordinary host login command, then captures the
  resulting system auth file. Save current login captures without launching login.
- Selection atomically replaces only the normal system auth file. Provider config,
  skills, sessions, projects, and histories remain shared. Live provider processes are
  neither stopped nor gated; provider-native system credential propagation applies.
- Capture deduplicates stable provider account ID, then email, then exact auth digest.
- Daemon startup, provider-status reads, and quota refreshes read live system auth before other
  account work. Exact snapshot/digest matches select that saved account. If valid Claude auth
  omits identity and its rotated digest matches no snapshot, reconciliation awaits
  `claude auth status --json`, caches the returned non-secret identity against that exact digest,
  and matches a unique saved email/account ID. Credentials changing while the command runs cause
  one retry; stale identity is never applied to a new digest. Failed status probes are briefly
  rate-limited on ordinary status reads, while an explicit full refresh retries immediately. A
  stable match updates the private snapshot with live credentials, so an externally refreshed
  login relinks without being captured again. Unmatched or ambiguous valid credentials become an
  explicit external login; missing or malformed credentials clear saved selection. Reconciliation
  never copies an older saved snapshot into system auth.
- Every configured polling interval (15 minutes by default) quota polling reconciles again,
  then refreshes selected accounts first and inactive accounts sequentially. Optional refresh
  after an eligible root turn is disabled by default and globally rate-limited; subagent
  completion never triggers it.
- Claude uses the OAuth usage endpoint. An authorization failure may rotate the public
  Claude Code refresh token; rotated credentials update the saved snapshot and, when
  still matched to live system auth, the system auth file. A login changed during the network
  request is never overwritten.
- Codex uses the CLI backend usage endpoint. Authorization failure falls back to a bounded
  `codex app-server` JSON-RPC request in a temporary auth-only `CODEX_HOME`; refreshed auth
  is copied back without retaining the temporary home.
- A failed refresh preserves the last success as `stale` for 30 minutes. Older data clears
  to `error`. Terminal/session operations never wait for background polling.
- A downward movement is scheduled when its advertised reset boundary falls between the two
  fresh samples, so a delayed poll does not mislabel it. Unexpected candidates require a reset
  timer at least one hour away, a drop of at least 20 percentage points, and a landing no higher
  than 10%; confirmation then requires a same-account/auth stable-low sample 5–45 minutes later
  while still before that boundary. Missing/ambiguous timers, stale or out-of-order data,
  rebounds, auth transitions, and account changes suppress confirmation.
- Confirmed unexpected resets expose a purple UI indicator and optional deduplicated
  per-device sound. Attribution shows estimate ranges, ambiguity, provider lag, and an
  explicit external/unassigned remainder; it never claims shared-account identity.
- The account popover can review a reset as manual Codex usage or discard it as a detection
  error. Review is server-persisted, removes the row from active notifications, retains it in
  the evidence log, and rejects manual-usage classification for Claude rows.
- Desktop status uses one bottom-sidebar row per provider: terminal-style icon, current
  identity, 5-hour/weekly quota percentages with compact reset countdowns, and live
  quota/auth state. The full switcher is a viewport-level overlay anchored to this status
  block, so sidebar width and overflow never clip it; mobile retains the compact account
  control.
- Removing the selected saved account removes mux ownership metadata and its private
  snapshot; live system auth remains untouched and becomes external.

## API surface

```text
GET    /api/provider-accounts
POST   /api/provider-accounts/refresh
POST   /api/provider-accounts/{provider}/capture
POST   /api/provider-accounts/{provider}/login
PATCH  /api/provider-accounts/{provider}/{account-id}
POST   /api/provider-accounts/{provider}/{account-id}/select
DELETE /api/provider-accounts/{provider}/{account-id}
PATCH  /api/telemetry/quota-resets/{reset-id}
```

```ts
type CurrentProviderAuth = {
  state: "saved" | "external" | "signed_out" | "unreadable"
  account_id: string | null
  email: string | null
  provider_account_id: string | null
  organization: string | null
}
```

## Failure modes

- Missing/malformed system credentials ⇒ capture fails without registry mutation.
- Missing/unreadable credentials during reconciliation ⇒ typed current state; remembered
  selection clears; system auth is never written.
- Login cancel, timeout, or CLI failure ⇒ visible Settings error; existing accounts remain.
- Claude status failure or repeated credential change during reconciliation ⇒ valid login remains
  external; daemon startup and terminal use continue, and a later status/refresh retries safely.
- Provider/network/auth failure ⇒ stale-retention policy; no account switch.
- Private snapshot sync failure ⇒ live login remains external; no claim that stale saved auth
  is active.
- Missing Codex app-server ⇒ direct quota failure remains visible; terminal use unaffected.

## Key files

- Service: `src/swe_mux/provider_accounts.py`
- Durable evidence: `src/swe_mux/operational_telemetry.py`
- Composition/routes: `src/swe_mux/server.py`
- UI: `frontend/src/ProviderAccounts.tsx`, `frontend/src/providerAccountDisplay.ts`,
  `frontend/src/style.css`
- Tests: `tests/test_provider_accounts.py`

## Relates to

- `usage.md`: historical ccusage analytics remain a separate subsystem.
- `backends.md`: provider execution continues to use ordinary shared homes.
- `ui.md`: bottom-sidebar provider rows and the mobile account control expose selection.
