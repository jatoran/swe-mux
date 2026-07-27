# Interfaces

All JSON APIs are rooted at `/api`. PTY and event streams use `/pty/{session_id}` and
`/events` WebSockets.

## Desktop lifecycle control

```text
GET  /health
POST /desktop/shutdown   Authorization: Bearer DESKTOP_CONTROL_TOKEN
```

`GET /health` remains ordinary local/tailnet diagnostics; it also reports `supervisor: bool`
(whether the daemon is attached to the PTY supervisor). Shutdown exists only when the daemon
was launched with desktop control, accepts IP-loopback peers only, compares the generated token
in constant time, and returns `202 {status: "shutting_down", mode}`. An optional JSON body
`{mode: "quit"|"restart"}` (default `quit`) carries shutdown intent: `quit` reaps every session
(including PTY-supervisor sessions); `restart` detaches so supervised sessions survive for the
next daemon to reattach. The token lives under the daemon data directory and never enters
browser state or the tailnet API. Standalone `muxd` returns 404.

## Runtime log level

```text
GET  /api/debug/log-level
POST /api/debug/log-level  {level: "DEBUG"|"INFO"|"WARNING"|"ERROR"}
```

Runtime verbosity toggle for the daemon's root logger (console + rotating
`<data_dir>/daemon.log`), applied live without a restart; returns
`{level}` (POST normalizes case, `400` on an unknown level). The `log_level`
config field is the startup default and is also applied live when the config
file changes; this endpoint deliberately does not persist. The aiohttp request
log is isolated in `<data_dir>/access.log` and unaffected by the level.

## Daemon self-restart

```text
POST /api/daemon/restart   {force?: bool}
```

The session-preserving "reload daemon" trigger for the UI (menu/palette), `mux
reload-daemon`, and agents. The daemon spawns a successor process (which waits
for the port with `--relaunch-wait`), signals its own shutdown with detach
intent, and exits; the successor reattaches to the PTY supervisor's live
sessions. Returns `202 {status: "restarting", sessions_preserved}`. Without an
attached supervisor a restart would kill every session, so it returns
`409 supervisor_not_attached` unless `force=true` (the same authority level as
killing sessions); daemons started without a relaunchable entry point return
`409 restart_unavailable`. This carries browser authority like the session
APIs — it is not gated on the desktop control token because a preserved
restart is no more destructive than the existing kill-session surface.

## Frozen-app redeploy

```text
POST /api/daemon/redeploy  {force?: bool}
GET  /api/daemon/redeploy
```

The staged frozen-app rebuild trigger for the UI ("Rebuild + redeploy app
(keep sessions)", `app.redeploy`; works from desktop and mobile). POST
validates a source checkout + `uv` on PATH (`409 no_source_checkout` /
`409 uv_not_found`), an attached supervisor (`409 supervisor_not_attached`
unless `force=true`, same authority as restart), and a pid single-flight lock
(`409 redeploy_in_progress`), then spawns `packaging/redeploy_desktop.py`
detached from the daemon's lifetime (log: `<data_dir>/redeploy.log`, lock:
`<data_dir>/redeploy.lock`) and returns `202 {status: "redeploying", pid,
log}`. The script builds into `dist/.staging` while this daemon keeps serving,
stops it only after a successful build, swaps the bundle (previous kept at
`dist/swe-mux.prev`), and rolls back if the new build never reports healthy.
GET returns `{running, pid, log_tail, available}` so the UI can detect an
early build failure while the old daemon is still alive.

## PTY supervisor IPC (local only)

With `pty_supervisor_enabled`, the daemon talks to the standalone PTY supervisor over a
loopback TCP socket discovered through `<data_dir>/supervisor.json` (pid, port, random token,
protocol version). Frames are length-prefixed JSON headers with optional binary payloads;
`hello` performs a constant-time token check plus a protocol-version handshake and announces
existing sessions. Messages: `spawn / write / resize / set_graceful_exit / subscribe /
unsubscribe / set_meta / stop / release / remove / list / ping / reap_all_and_exit`. This
surface is process-local plumbing, not a public API: it is bound to 127.0.0.1, authenticated
by a token readable only from the local data directory, and versioned so a mismatched daemon
refuses to attach (falling back to in-process PTYs). `muxd --shutdown` is the explicit
kill-server command: it reaps all supervised sessions and stops the supervisor.

## Canonical Projects and Groups

```text
GET    /projects
POST   /projects                    {name, root, group_id?}
PATCH  /projects/{project_id}       mutable Project fields/layout revision
PUT    /projects/order              {project_ids, expected_order}
DELETE /projects/{project_id}

GET    /project-groups
POST   /project-groups              {name}
PATCH  /project-groups/{group_id}   {name?, position?}
DELETE /project-groups/{group_id}
```

Project creation requires an existing folder, rejects duplicate canonical roots, and
initializes `.swe-mux/`. Project deletion rejects any live or historical session reference.
Deleting a Group ungroups its Projects.

```text
GET  /projects/{project_id}/actions
POST /projects/{project_id}/actions/trust   {fingerprint}
POST /projects/{project_id}/actions/run     {action_id}
```

Action discovery is inert. The catalog returns `fingerprint`, `trusted`, contributing `sources`,
normalized actions/steps, and import diagnostics. Trust succeeds only for the current exact
fingerprint. Run returns the spawned ordinary session snapshots plus per-step errors and returns
`409 project_actions_trust_required` when files are untrusted or changed.

```ts
interface Project {
  id: string
  name: string
  root: string
  position: number
  group_id: string | null
  layout: PaneLayout
  layout_revision: number
  default_backend?: 'shell' | 'claude' | 'codex'
  default_profile_id?: string
  portable_options: ProjectPortableOptions
  effective_options: ProjectEffectiveOptions
  option_sources: Record<string, 'global' | 'project_record' | 'project_file'>
}

interface ProjectGroup { id: string; name: string; position: number }

type PaneLeaf = {type: 'leaf'; kind: 'terminal'|'note'|'preview'|'history'; id: string}
type PaneStack = {type: 'stack'; id: string; children: PaneLeaf[]; active_child_id: string}
type PaneSplit = {type: 'split'; id: string; direction: 'horizontal'|'vertical'; ratio: number; first: PaneNode; second: PaneNode}
type PaneNode = PaneStack | PaneSplit
type PaneLayout = {version: 7; root: PaneNode | null}
```

Every split branch terminates in a stack, including a one-tab pane. `note` leaf IDs encode
Project note, session note, and individual file resources; `history` is the searchable
session archive. Versions 1–6 are migrated when read. Visible legacy resource docks become
ordinary adjacent panes, while hidden docks remain closed. A v6 `files:` leaf is pruned rather
than migrated — the Files browser is a utility-drawer tab, not a pane — collapsing a pane it
emptied and the split above it.

## Project resources

```text
GET     /session-notes[?project_id=]
GET|PUT /projects/{project_id}/note
GET      /projects/{project_id}/session-notes/{note_id}
POST     /projects/{project_id}/session-notes/{note_id}   initialize if absent
PUT      /projects/{project_id}/session-notes/{note_id}
GET     /projects/{project_id}/files?path=RELATIVE
GET     /projects/{project_id}/search?q=&mode=names|contents|both
GET     /projects/{project_id}/file?path=RELATIVE
PUT     /projects/{project_id}/file   {path, text, revision}
POST    /projects/{project_id}/reveal {path: RELATIVE}
POST    /projects/{project_id}/ignore {path: RELATIVE, scope: global|project}
PUT     /projects/{project_id}/watch  {watch_id?, paths: RELATIVE_DIRECTORY[]}
DELETE  /projects/{project_id}/watch/{watch_id}
GET|PUT /project/config               typed portable Project options
GET     /projects/{project_id}/observations
POST    /projects/{project_id}/observations   {body}                append one
PUT     /projects/{project_id}/observations   {observations, revision}   replace
```

The observation inbox is a project-owned capture list (`.swe-mux/observations.json`, no AI).
Append is conflict-free; replace (toggle done, delete, reorder) is revision checked and
returns `409 revision_conflict`. Bounded to 500 items of 2,000 characters. See
`features/observations.md`. The typed portable Project options include an `automations`
opt-in table gating control-plane substrate/consumers (`features/automation-enablement.md`).

`GET /session-notes` lists session notes that hold text, newest first, optionally scoped to one
Project; an unknown `project_id` is rejected. Each row carries `note_id`, Project identity,
`updated_at`, `bytes`, a bounded excerpt, and best-effort `owner_label`/`owner_backend`/
`owner_live`/`owner_known` resolved from live sessions then history. The listing is derived from
the filesystem, so it outlives sessions, history rows, and daemon restarts; per-Project scans are
capped and empty notes are omitted.

`GET /search` recursively finds files by name and/or UTF-8 content beneath the canonical root,
reusing the same ignore rules as the browser and running off the event loop. `mode` selects
`names`, `contents`, or `both` (invalid values fall back to `names`); content matching skips
binary and oversized files. It returns `{items: [{path, name, match: name|content, line, snippet}],
truncated}`, name matches sorted before content matches, bounded on files visited, bytes read,
per-file size, and result count.

Paths are relative to the canonical root and may not escape it. Project and session-note writes
are revision checked. A session note can be initialized only for a live terminal, a History row
owned by the Project, or a note file already owned by that Project. Reveal opens the host file
manager; Windows selects files and raises the resulting Explorer window. Global ignore actions
persist the resource basename; Project ignore
actions persist the Project-relative path. The file editor limit is 2 MiB. Watch leases last
45 seconds, accept at most 64 directories, and are non-recursive; open resource tabs renew them
every 30 seconds.

Successful note writes emit `project_note_changed {project_id, revision}` or
`session_note_changed {project_id, note_id, revision}`. Clean open editors refetch on a different
revision and after event-stream reconnect; editors with local pending/in-flight/error/conflict
state retain their text and continue through optimistic conflict detection. The event contract
provides live follow, not concurrent-edit merging.

## Prompt templates

```text
GET    /prompts[?project_id=]
POST   /prompts
PUT    /prompts/{scope}/{template_id}
DELETE /prompts/{scope}/{template_id}
POST   /prompts/{scope}/{template_id}/use
PATCH  /prompts/{scope}/{template_id}/favorite
```

Scopes are `global | project`. Writes are revision checked; same-ID global/Project conflicts
are returned explicitly. Template bodies are bounded inert UTF-8 text and terminal control
characters are rejected. The browser's Insert action uses terminal paste semantics and never
adds Enter or submits.

## Clipboard history

```text
GET    /clipboard
POST   /clipboard                        {text, source?, session_id?, project_id?, device?}
DELETE /clipboard[?include_pinned=1]
GET    /clipboard/{entry_id}
PATCH  /clipboard/{entry_id}             {pinned}
DELETE /clipboard/{entry_id}
```

`GET /clipboard` returns the ring's settings (`enabled`, `persist`, `limit`, `entry_max_chars`,
`retention_hours`, `redact_secrets`, `count`) plus entries carrying **previews only** — a
single-line, whitespace-collapsed 180-character label with character/line counts, provenance
(`source`, `session_id`, `project_id`, `device`) and `pinned`. The copied text is returned only
by the per-entry `GET`, so a long history never ships in the list payload.

`POST /clipboard` is the capture path used by the browser's boot-installed hooks. It always
answers `{stored, reason, entry}` and never errors on a refusal: `stored:false` with reason
`disabled | empty | too_large | secret`, `promoted` when identical text already existed (the
existing entry moves to the front instead of duplicating), `stored` otherwise. Mutations emit a
`clipboard_changed` event whose payload deliberately carries no text — those events are persisted
in the history event log, which would defeat the memory-only default.

Pinned entries are exempt from eviction, retention, and an ordinary `DELETE /clipboard`.

## Sessions

```text
GET    /sessions[?project=&state=&backend=]
POST   /sessions
GET    /sessions/{id}
PATCH  /sessions/{id}
DELETE /sessions/{id}
POST   /sessions/{id}/input
POST   /sessions/{id}/broadcast-set
POST   /broadcast/input
GET    /sessions/{id}/last-reply
```

```ts
interface SpawnRequest {
  project_id: string
  backend?: 'shell' | 'claude' | 'codex'
  name?: string
  profile_id?: string
  executable?: string
  argv?: string[]
  resume_native_id?: string
  cwd?: string                      // must resolve inside the owning Project root
  env?: Record<string, string>      // ≤ 64 entries; scalar values stringified
  completion_mode?: 'interactive' | 'one_shot'
}
```

`project_id` is required. `worktree` and unknown fields are rejected. Session PATCH rejects
Project ownership changes.

`cwd` defaults to the owning Project root and may name a subdirectory of it (a task that runs
in `./frontend`). Containment is enforced in the spawn handler, which is the only layer that
knows which Project owns the request: the value is resolved (relative against the root,
symlinks collapsed) and rejected if it lands outside. `env` merges over the shell profile's
environment and under mux's own identity variables, so a spawned shell can never present
another session's hook credentials. Both exist because a Project Action step declares its own
directory and environment; encoding them into `argv` instead is what previously forced a
swe-mux executable into every task's process tree.

`argv` is appended after the adapter's own flags, so for an agent backend it becomes the CLI's
trailing positional prompt: that is how a session is seeded with a first message (the
cross-vendor review spawn and note "send to agent" both use it) without writing into a TUI that
is not ready for input. It is a command line, so the caller owns its length — the browser caps a
seeded prompt at 20,000 characters and routes anything longer to a live session instead.

`POST /sessions/{id}/input` is the only delivery path that reaches a session whose pane is not
mounted in the caller's browser. A multi-line body must arrive wrapped in bracketed paste
(`ESC[200~` … `ESC[201~`, newlines as CR) or the agent composer submits at every line, and a
submitting Enter is a separate later write, not the same one.

`GET /sessions` adds a compact, read-only `delivery_readiness` object with
`state: safe|blocked|unknown`, a reason, and `authorized: false`. It is not accepted on writes.
Each row also exposes its stable terminal `note_id` and whether its lazily created note file
currently exists. `spawn_backend` and `spawn_native_session_id` identify the immutable root
process; `backend` and `native_session_id` may instead describe the active promoted run only
when that root is a shell.
PTY WebSocket owners may send `{type:"terminal_state", mode:"normal|alternate"}`. Input
frames label xterm device replies with `kind:"terminal_response"`; every other input frame,
including bracketed paste, advances the human-input boundary. OSC 10/11 default-color replies
are recognized as device replies but are not delivered to Codex: its bounded native-Windows
startup probe can expire during the browser/WebSocket round trip and would then treat the late
reply as composer text. Codex falls back to the console palette. The daemon also rejects exact
Codex OSC 10/11 reply payloads from older cached browser builds that labeled them as user input.
On initial attach, the daemon sends `state` and waits up to 250 ms for
`{type:"attach_ready", cols, rows, renderer}`. It applies those dimensions before
`replay_start`; an old-style `resize` frame also releases replay, and timeout preserves
compatibility with clients that send neither. Other frames arriving during this window are
buffered and handled after `replay_end`. Later `attach_ready` frames are equivalent to `resize`.
`GET /sessions/{id}/last-reply` returns the newest meaningful Claude/Codex assistant turn for
gesture-safe clipboard prefetch. Provider control acknowledgements are skipped; the route does
not type `/copy` into the PTY.

## Voice and Conversation mode

```text
GET  /remote/status
POST /remote/mobile-voice/enable   X-Mux-User-Gesture: mobile-voice-setup
```

The mobile-voice request is accepted only while the Tailscale listener is enabled and only from
the explicit Talk/Settings action. It returns a secure URL only when the daemon has a verified
secure endpoint; otherwise it returns `error` without changing the working direct HTTP listener.
It does not change tailnet policy or make swe-mux public.

```text
GET    /voice
POST   /sessions/{id}/voice/generate
POST   /sessions/{id}/voice/transcribe   Content-Type: audio/wav; bounded mono PCM
POST   /sessions/{id}/voice/submit       {utterance_id, text}
POST   /sessions/{id}/voice/interrupt
GET    /voice/clips[?session=&run=&limit=]
GET    /voice/clips/{clip_id}/audio
DELETE /voice/clips/{clip_id}
```

Transcription accepts at most 2 MiB and 35 seconds of mono 16-bit PCM at 8–48 kHz. Raw audio is
temporary and deleted after recognition. Submit is agent-only, rejects control characters, caps
text at 20,000 characters, deduplicates bounded recent `utterance_id` values, writes text plus one
Enter, and advances the ordinary human-input revision. Interrupt sends one Ctrl-C and records the
same boundary. Neither route approves provider prompts or derives authorization from delivery
readiness.

Automatic completed-reply synthesis emits ordered `voice_clip_ready` events sharing
`stream_id`, `segment_index`, and `segment_count`; each ready segment is independently playable.
Summary/verbatim selection remains the existing session/global contract.

## Delivery diagnostics

```text
GET /automation/injection-safety
```

Version 2 returns research-only per-session delivery checks/evidence, parser coverage, and
aggregate shadow metrics. `authorizes_actuation` and every session's `authorized` are always
false. Prompt bodies and terminal bytes are never included.

## History and reviews

```text
GET    /history[?q=&scope=all|user|assistant|metadata&backend=&project=&state=&external=&time_basis=started|last_message&date_from=&date_to=&cursor=]
GET    /history/projects
GET    /history/{id}/transcript[?q=&scope=all|user|assistant|metadata]
GET    /history/backfills[?project_id=]
POST   /history/backfills              {project_id}
GET    /history/backfills/{job_id}
DELETE /history/backfills/{job_id}
POST   /history/{id}/resume           {project_id, ...}
DELETE /history/{id}
POST   /history/{id}/second-opinion   preview/confirm with project_id
GET    /history/{id}/handoff
```

Resume/review confirmation must target an existing Project and starts at its root. Backfill
jobs are daemon-local, cancellable, idempotent scans of complete shared native CLI history.
Handoff Markdown exposes the swe-mux history ID, provider-native session ID, and recorded native
transcript path; transcript bytes remain in the provider-owned file and are never copied into the
export. A missing or stale transcript pointer is reported explicitly.
History rows expose lifecycle `spawned_at`, nullable conversational `native_started_at`, nullable
`last_message_at`, and nullable `last_message_role: user|assistant`. Start/last are chronological
bounds of valid provider-native user/assistant timestamps, even when native records are written
out of order. Transcript messages expose nullable provider-native `ts`; missing timestamps are
not synthesized from exit time or file metadata.

## Git and processes

```text
GET    /git/projects
POST   /git/projects/resolve
GET    /git/projects/{scope_id}
GET    /git/worktrees
POST   /git/worktrees
DELETE /git/worktrees
GET    /processes
POST   /processes/action             {session_id, pid, identity_id, action}
GET    /previews[?session=]
POST   /previews                     {session_id, url, approved?, attach?, target_session_id?, direction?}
POST   /previews/{id}/capture         {viewport?, width?, height?, clip?}
DELETE /previews/{id}
```

`POST /previews/{id}/capture` headlessly screenshots the live loopback server and saves a PNG
under the owning Project's `.swe-mux/preview-shots/` (data-dir fallback), returning
`{available, path, url, width, height, region}`. Optional `clip {x,y,width,height}` (page
pixels, from the top of the page) captures a region. Missing the optional Playwright backend
returns `{available: false, reason, install}`; a render error returns `{available: true,
error}` (502). It never writes a PTY. See `features/processes-and-previews.md`.

Git scopes/worktrees are derived tooling APIs, not canonical Project/session ownership and
not first-class frontend navigation.

Process snapshots expose bounded observational states `active | exited | escaped |
suspected_orphan | stale | inaccessible`. Actions revalidate PID, creation-time identity,
and ownership immediately before signaling; no state triggers automatic termination.
`GET /processes` returns running processes only; `include_ended=1` adds records that ended
during the current daemon run. Ended records never contribute to resource totals.
`memory_bytes` is RSS (the working set), which counts shared pages once per mapping process
and therefore overstates a summed tree. `unique_memory=1` additionally samples unique set size
into `memory_unique_bytes` per process, per daemon member, and in totals; it is opt-in because
it walks every working set at roughly 200x the cost of the RSS read, so only user-opened views
request it. Totals report it only when every contributor supplied one.

Preview URLs are HTTP(S), literal loopback, and credential/query/fragment-free. Registration
deduplicates by canonical Project endpoint and records the session that owns the live listener,
even when another terminal printed the clicked URL. `attach=true` opens or activates the stable
Preview leaf beside that owner; closing the leaf leaves registration intact. Listing discovers
live Project listeners without opening tabs. Sandboxed Preview fetch/XHR/WebSocket traffic to
another registered Project service is rewritten through that service's `/preview/{id}/…` route.

## Provider accounts and usage

```text
GET    /provider-accounts
GET    /provider-accounts/audit[?limit=]
POST   /provider-accounts/refresh
POST   /provider-accounts/verify
POST   /provider-accounts/{provider}/capture
POST   /provider-accounts/{provider}/login
PATCH  /provider-accounts/{provider}/{account_id}
POST   /provider-accounts/{provider}/{account_id}/select {force?: bool}
POST   /provider-accounts/{provider}/{account_id}/adopt
POST   /provider-accounts/{provider}/{account_id}/purge-telemetry {since?: epoch}
DELETE /provider-accounts/{provider}/{account_id}
GET|POST /usage
DELETE /usage/cache
GET    /telemetry/operational[?provider=&account=&limit=]
PATCH  /telemetry/quota-resets/{reset_id} {resolution: manual_usage|discarded}
```

Auth file contents never appear in API responses. `GET /provider-accounts` reports each live
system auth state as `saved | external | signed_out | unreadable`; saved selection is derived
from the system auth file rather than restored from registry memory. Explicit selection changes
only the provider's system auth file; polling covers saved active and inactive accounts.
Quota fields are derived from the newest durable sample.

Account identity carries its provenance. `identity_source` is `token` when the owner was
resolved by asking the provider with that credential, and `cli`/`file` for weaker readings that
describe machine state rather than the token. Only a `token` identity or an exact digest match
lets reconciliation move credentials into a saved account; a weaker match is reported as
`current.match_hint` and applied only through `…/adopt`. `POST /provider-accounts/verify`
re-derives every saved account's owner. Saved accounts resolving to one provider account carry
`conflict`, and every non-primary duplicate stops being polled and reports quota status
`conflict`. Selecting a different account while live sessions of that provider are running
returns HTTP 409 with `conflict: true` until the caller passes `force`; those sessions hold the
outgoing token and rotate it back into the shared credential file. `GET
/provider-accounts/audit` returns the append-only record of credential-affecting decisions
(action, matched-by, truncated digests) and never credential contents. Quota samples carry
`provider_account_uuid`, the verified account a sample describes independent of the slot it was
filed under; `…/purge-telemetry` discards a slot's durable rows, bounded by `since` when only
the period after a credential handover is bad (daily rollups have no instant to cut on and are
left intact by a bounded purge). The operational endpoint caps
`limit` to 1–1,000 per collection and returns quota samples/rollups/reset evidence,
probabilistic attributions, tool/skill aggregates, parser coverage, and explicit compactions.
Its interpretation is always `observational_correlation_only`.
Reset review is durable and audit-preserving: both resolutions remove the row from the active
alert summary without deleting evidence; `manual_usage` is valid only for Codex rows.

## Other API groups

Configuration/keybindings, automation/annotations/lineage, events/notifications, voice,
remote status, filesystem discovery, and preview proxy routes retain their feature-specific
contracts described in the corresponding `features/` documents.

`GET /api/settings/bundle?cwd=<path>` aggregates the Settings panel's open payload
(config, automation rules, keybindings, profiles, projects, automation status, provider
status, usage, and — when `cwd` is supplied — project config) into one response. `config`
failing fails the request; any other part degrades to `null` with its reason keyed under
`errors`. The individual endpoints remain authoritative and unchanged.

## CLI

```text
mux ls
mux projects
mux profiles
mux spawn --project ID [--backend shell|claude|codex] [--profile ID] [--arg VALUE]
mux resume HISTORY_ID --project ID
mux send SESSION TEXT
mux kill SESSION
mux history
mux doctor
```
