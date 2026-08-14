# Interfaces

All JSON APIs are rooted at `/api`. PTY and event streams use `/pty/{session_id}` and
`/events` WebSockets.

## Desktop lifecycle control

```text
GET  /health
POST /desktop/shutdown   Authorization: Bearer DESKTOP_CONTROL_TOKEN
```

`GET /health` remains ordinary local/tailnet diagnostics; it also reports `supervisor: bool`
(whether the daemon is attached to the PTY supervisor), `supervisor_state`
(`connected | lost | absent`), and `supervisor_unadopted`. `lost` is deliberately distinct
from `absent`: the supervisor process is alive and still holds live sessions, this daemon
just cannot reach them — reporting that as "no supervisor" hides sessions that are running
and unkillable from here. `supervisor_unadopted` counts supervised sessions this daemon
could not rebuild (snapshot drift, a crash inside the spawn-meta window); they keep running
with no UI handle, so the count must be visible rather than only a log line. Shutdown exists only when the daemon
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
unless `force=true`, same authority as restart), a pid single-flight lock
(`409 redeploy_in_progress`), and that no foreign process anchors the bundle
(`409 bundle_in_use`, skipped with `force=true`): the swap's one
non-retryable step is renaming `dist/swe-mux`, and a process the redeploy
cannot stop — typically a dev server behind a Preview tab, or a terminal
whose cwd landed inside the bundle; sessions descend from the supervisor and
survive the app stop — dooms it after minutes of build. The refusal's
`message` names each holder (`holders[]`: `{pid, name, via: exe|cwd, path}`)
so the user can stop that process or close its tab, and the same gate runs in
`packaging/redeploy_desktop.py` itself (pre-build and again pre-stop;
`--force` downgrades it to a warning). Then it spawns `packaging/redeploy_desktop.py`
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
POST   /projects                    {name, root, group_id?, create_missing?}
PATCH  /projects/{project_id}       mutable Project fields/layout revision
PUT    /projects/order              {project_ids, expected_order}
POST   /projects/{project_id}/used  {reason: prompt_submitted | session_started}
DELETE /projects/{project_id}

GET    /project-groups
POST   /project-groups              {name}
PUT    /project-groups/order        {group_ids, expected_order}
PATCH  /project-groups/{group_id}   {name?, position?}
DELETE /project-groups/{group_id}
```

Project payloads add `created_at` (registration), daemon-persisted `last_used_at` (explicit user use), and derived `last_activity`
(latest session activity from history), all as epoch seconds with `0` when unknown.
`POST /projects/{project_id}/used` advances `last_used_at` monotonically and emits `project_used {project_id, last_used_at, reason}` so connected clients converge without sharing browser storage.
Both order endpoints demand a
complete permutation plus the `expected_order` the client last saw, answering `409` with
`{"code": "order_conflict"}` when another device already moved something.

Project creation rejects duplicate canonical roots and an empty root, and initializes
`.swe-mux/`. `create_missing` makes exactly one folder: the parent must already exist, an
already-present folder is accepted, and the duplicate/group checks run first so a rejected
request leaves no stray directory. Project deletion rejects any live or historical session
reference. Deleting a Group ungroups its Projects.

```text
POST /projects/{project_id}/init-scripts/run   {script_ids}
```

Runs the user's own setup commands (`project_init_scripts` in the daemon config) as ordinary
one-shot shell terminals at the Project root, started in configured order. Unknown ids are
rejected as a whole; a launch failure is reported per script and returns `207`. These are
machine-local and user-authored, so no trust fingerprint is involved.

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
  git_compare_ref: string | null
  default_backend?: 'shell' | 'claude' | 'codex'
  default_profile_id?: string
  portable_options: ProjectPortableOptions
  effective_options: ProjectEffectiveOptions
  option_sources: Record<string, 'global' | 'project_record' | 'project_file'>
}

interface ProjectGroup { id: string; name: string; position: number }

type PaneLeaf = {type: 'leaf'; kind: 'terminal'|'note'|'preview'|'history'|'queue'; id: string}
type PaneStack = {type: 'stack'; id: string; children: PaneLeaf[]; active_child_id: string}
type PaneSplit = {type: 'split'; id: string; direction: 'horizontal'|'vertical'; ratio: number; first: PaneNode; second: PaneNode}
type PaneNode = PaneStack | PaneSplit
type PaneLayout = {version: 7; root: PaneNode | null}
```

Every split branch terminates in a stack, including a one-tab pane. `note` leaf IDs encode
Project-owned notes and individual file resources; `history` is the searchable
session archive; `queue` is the per-target prompt-queue tab, its id `queue:<session_id>`
(prefixed so it can never collide with the target's own terminal leaf). Versions 1–6 are
migrated when read. Visible legacy resource docks become
ordinary adjacent panes, while hidden docks remain closed. A v6 `files:` leaf is pruned rather
than migrated — the Files browser is a utility-drawer tab, not a pane — collapsing a pane it
emptied and the split above it.

## Project resources

```text
GET     /notes[?project_id=]
POST    /projects/{project_id}/notes                      {title}
GET     /projects/{project_id}/notes/{note_id}
PUT     /projects/{project_id}/notes/{note_id}            {markdown, revision}
PATCH   /projects/{project_id}/notes/{note_id}            {title, revision}
DELETE  /projects/{project_id}/notes/{note_id}            {revision}
GET     /projects/{project_id}/files?path=RELATIVE
POST    /projects/{project_id}/resources   {parent, name, kind: file|directory}
GET     /projects/{project_id}/search?q=&mode=names|contents|both
GET     /projects/{project_id}/file?path=RELATIVE[&worktree=ABSOLUTE]
GET     /projects/{project_id}/file/content?path=RELATIVE&revision=REVISION[&worktree=ABSOLUTE]
PUT     /projects/{project_id}/file   {path, text, revision, worktree?}
POST    /projects/{project_id}/reveal {path: RELATIVE, worktree?}
POST    /projects/{project_id}/ignore {path: RELATIVE, scope: global|project}
PUT     /projects/{project_id}/watch  {watch_id?, paths: RELATIVE_DIRECTORY[], worktree?}
DELETE  /projects/{project_id}/watch/{watch_id}
GET|PUT /project/config               typed portable Project options
GET     /projects/{project_id}/observations
POST    /projects/{project_id}/observations   {body}                append one
PUT     /projects/{project_id}/observations   {observations, revision}   replace
POST    /projects/{project_id}/observations/{observation_id}/decide {decision: approve|dismiss}
GET     /projects/{project_id}/automations
PUT     /projects/{project_id}/automations    {automations, revision?}
GET     /projects/{project_id}/project-context
PUT     /projects/{project_id}/project-context {markdown, revision}
GET     /sessions/{session_id}/scan-timeline
PUT     /sessions/{session_id}/scan-timeline  {enabled}
PUT     /sessions/{session_id}/scan-timeline/project {enabled}
POST    /sessions/{session_id}/scan-timeline/scan
POST    /sessions/{session_id}/scan-timeline/backfill
GET     /sessions/{session_id}/scan-timeline/{record_id}?rehydrate=0|1
```

The old observation endpoints and `.swe-mux/observations.json` remain compatibility storage.
The current frontend has no Observation Inbox command or mounted view.
Typed `spawn_request` rows are projected into `GET /queue/mailbox` and decided once by a human through the decision route.
Malformed storage reports `observations_unreadable` and is never rewritten as empty.
See `features/observations.md`.

The automations routes are the per-project control-plane opt-in surface. `GET` returns the
registry (id, kind, label, `requires`, `implemented`), the project's `requested` table, and
the resolution (`enabled`, plus `blocked` → the dependencies each still needs) so a toggle
can show a dependency tree rather than a flat checkbox list. `PUT` replaces the table
through the ordinary project-config write: `409 revision_conflict` on a stale revision,
`409 automation_not_implemented` for a reserved id with no code behind it. The same table is
also carried by the typed portable Project options (`features/automation-enablement.md`).

The Project-context routes read and revision-check one fixed user-owned Markdown file.
`GET` returns blank content and revision `missing` when it does not exist.
`PUT` accepts no path and returns `409 revision_conflict` rather than overwriting a concurrent external edit.
The response includes the bounded setup prompt copied by the Timeline drawer (`features/project-card.md`).

The scan-timeline routes expose the readable records and boundaries for one persistent session.
`PUT` changes only the current `agent_run_id` grant and refuses when either outer gate is off.
`PUT .../project` changes the Project permission and its required dependencies without authorizing a run or starting historical work.
`POST .../scan` requests one bounded scan and returns no record when there is no new input or a
budget gate is closed.
`POST .../backfill` starts an oldest-first background scan of uncovered messages in the current run and returns its initial state.
The ordinary timeline snapshot carries progress and an honest completed, partial, or failed result.
The record route returns compressed metadata by default; `rehydrate=1` reparses the authoritative
current or historical run transcript for the record's source interval and increments the measured
rehydration rate (`features/scan-timeline.md`).

`GET|PUT /project/config` accept an optional `project_id`. Supplying it makes that
registered Project's root authoritative for paths; without it the daemon re-resolves the
supplied `cwd` through Git, which retargets a Project registered inside a larger worktree to
the enclosing one.

`GET /notes` lists the flat Project-owned note collection newest first, optionally scoped to one
Project; an unknown `project_id` is rejected.
Each row carries `note_id`, Project identity, title, creation/update times, bytes, revision, a
bounded excerpt, and optional `origin_session_id` migration provenance.
The listing is derived from the filesystem and includes explicitly created empty notes.
Per-Project scans are capped at 500 notes.
The historical initial note remains at `.swe-mux/notes/project.md`; additional notes live under
`.swe-mux/notes/items/`.
Non-empty legacy session notes migrate into this collection and their source files move to the
recoverable `.swe-mux/notes/legacy/` tree.

`GET /global-notes/scratchpad` returns the fixed global Scratchpad.
Before the first save it returns an editable `missing` payload with empty Markdown and revision `missing` without creating a file.
`PUT /global-notes/scratchpad {markdown, revision}` writes `<data_dir>/notes/items/scratchpad.md` with the same 1 MiB body limit and optimistic revision contract as Project notes.
Other global note IDs are rejected; Scratchpad has no create, rename, or delete route.

`GET /search` recursively finds files by name and/or UTF-8 content beneath the canonical root,
reusing the same ignore rules as the browser and running off the event loop. `mode` selects
`names`, `contents`, or `both` (invalid values fall back to `names`); content matching skips
binary and oversized files. It returns `{items: [{path, name, match: name|content, line, snippet}],
truncated}`, name matches sorted before content matches, bounded on files visited, bytes read,
per-file size, and result count.

Paths are relative to the canonical root and may not escape it. Note writes, renames, and deletes
are revision checked. `POST /resources` creates exactly one empty file or directory in an
existing contained parent. `name` is a leaf, not a path; Windows-invalid/reserved names and the
Project control directories `.git`/`.swe-mux` are refused. Creation is exclusive and returns
`409 resource_exists` rather than overwriting any existing filesystem entry. The response is
`{name, path, parent, kind, size, hidden}`, where `hidden` reports the effective ignore rules.
`GET /file` classifies the representation as text, delimited text, image,
or unsupported. Text and delimited-text reads are capped at 2 MiB. Allowlisted PNG, JPEG, GIF,
and WebP reads are capped at 16 MiB and carry verified dimensions, frame count, MIME type, and
revision. `GET /file/content` requires that revision and serves only an image that passes the
format, extension, dimension, pixel, and frame checks; stale revisions return
`409 revision_conflict`, and refused content returns `415 image_unavailable`. Reveal opens the host file manager; Windows selects files and
raises the resulting Explorer window. Global ignore actions persist the resource basename;
Project ignore actions persist the Project-relative path. Watch leases last 45 seconds, accept
at most 64 directories, and are non-recursive; open resource tabs renew them every 30 seconds.

The file, file-content, write, reveal, and watch routes accept an optional exact absolute `worktree` root.
The daemon verifies that root against `git worktree list` for the Project repository on every request, then applies the existing relative-path containment checks beneath that root.
The option does not change Project ownership and is not accepted by browsing, search, create, or ignore routes.
Unknown, removed, nested, or cross-repository roots return a typed `worktree_not_found` or `invalid_worktree` error instead of falling back to the canonical Project root.
File change events and watch replies include the exact `worktree` root so identical relative paths in sibling worktrees remain isolated.

Successful Project note creates, writes, renames, and deletes emit
`note_changed {scope: "project", project_id, note_id, revision}`.
Successful Scratchpad writes emit `note_changed {scope: "global", note_id: "scratchpad", revision}`.
Clean open editors refetch on a different
revision and after event-stream reconnect; editors with local pending/in-flight/error/conflict
state retain their text and continue through optimistic conflict detection. The event contract
provides live follow, not concurrent-edit merging.

## Agent Context

```text
GET  /projects/{project_id}/agent-context
GET  /projects/{project_id}/agent-context/sources/{source_id}
POST /projects/{project_id}/agent-context/sources/{source_id}/reveal
POST /projects/{project_id}/agent-context/sync/preview   {direction}
POST /projects/{project_id}/agent-context/sync           {direction, source_revision, target_revision}
POST /projects/{project_id}/agent-context/restore        {backup_id, target_revision}
```

`direction` is a descriptor-declared synchronization pair such as `claude_to_agents | agents_to_claude`.
Inventory returns every harness-declared Project-root instruction item, their normalized `in_sync | different | missing` comparisons,
descriptor-declared global instruction sources, provider rows with complete `item_count`, and the newest valid
restore-point manifests. Source/provider status is typed:
`available | missing | disabled | unsupported | unreadable | too_large`. Claude learned memory
items and root instructions carry opaque source ids; `revealable` marks an existing regular
non-symlink file. No route accepts a path.

Source reads return `{source, text}` and are UTF-8, regular-file, non-symlink, and 512 KiB
bounded. Instruction sources carry `scope: project | global`; resolved global host paths never
cross the API. Inventory caps Claude memory rows at 128 direct Markdown children while
`item_count` reports the complete count. Codex returns an explicit provider status and no files
until its CLI publishes a stable project-memory file inventory; the daemon does not expose
private database rows.

Reveal re-resolves the opaque source ID, refuses missing, symlink, and non-file targets, and
passes the resolved file to the same OS launcher as the Project file browser. Windows Explorer
selects the file; other platforms retain the shared launcher's native behavior.

Preview remains Project-root-only. It returns a bounded unified diff plus SHA-256
`source.revision` and `target.revision`
(`missing` when absent). Commit is a complete destination overwrite and succeeds only while both
revisions still match; otherwise `409 {code:"revision_conflict"}`. It preserves an existing
destination's CRLF/LF convention and mode, uses same-directory atomic replace, and creates a
data-dir restore point first. Restore is guarded by the destination revision too and backs up the
state it replaces. A restore point recording an originally missing destination removes the file
created by sync. Successful writes emit `agent_context_changed`; see
`features/agent-context.md`.

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

## Prompt queue (Phase 4) and its Phase 5 callers

```text
GET    /queue                                       per-target aggregates
GET    /queue/messages?target_session_id=           one target's ordered queue
POST   /queue/messages                              {target_session_id, body, armed?, insert_after?,
                                                     constraints?, correlation_id?}
PATCH  /queue/messages/{message_id}                 {body, revision} | {armed} | {after} |
                                                     {retarget_session_id} | {constraints}
DELETE /queue/messages/{message_id}                 erase and hide; 409 while delivering
POST   /queue/messages/{message_id}/cancel          {kind: cancelled|skipped|revoked}
GET    /queue/messages/{message_id}/deliveries      audit rows (no prompt text)
POST   /queue/send-next                             {message_id, revision, idempotency_key?, confirm?}
GET    /queue/export?target_session_id=[&redact_secrets=0]

GET    /queue/auto                                  master switch, pause, default-on conversation
                                                     grants/overrides,
                                                     counters, promotion criteria
POST   /queue/auto/pause                            {paused}   emergency disable
PUT    /queue/auto/sessions/{sid}                   {enabled?, ttl_minutes?, max_sends?,
                                                     accept_agent_messages?}
POST   /queue/auto/report-unsafe                    {note}     operator review input
GET    /queue/mailbox?author=all|non_human|human    application-wide authorship view
                         [&project_id=...]
                         [&target_session_id=...]    server-side target filters
```

The typed daemon operations of the persistent manual prompt queue — the daemon owns
ordering, revision checks, readiness, target identity, and audit; the browser is one caller
(`features/prompt-queue.md`). Targets are live Claude/Codex sessions only. Messages carry
states `draft | armed | blocked | delivering | sent | failed | cancelled | stranded`; edits
increment `revision` and are refused for sent/delivering items. `send-next` atomically
re-checks pending state, the exact revision the caller last saw, and strict head-of-line
order, then target liveness/agent-run identity and delivery readiness immediately before the
PTY write. Typed refusals carry a machine `code`: `head_of_line_blocked`,
`revision_conflict`, `delivery_not_safe` (retryable with `confirm: true`),
`delivery_protected` (approval/Q&A/identity — never overridable), `target_ended` /
`target_run_replaced` (the message is stranded, not retargeted — an in-CLI `/clear` or `/new`
reaches this path too, via `agent_conversation_rolled`). A repeated
`idempotency_key` replays the recorded outcome instead of delivering twice. Delivery audit
rows and `queue_updated`/`queue_delivery` events never carry the prompt body; export redacts
credential-shaped bodies unless the caller opts out.

Phase 5 adds two more refusal codes — `delivery_not_due` (a scheduled item; the item keeps
its state and "Send now" overrides) and `delivery_expired` (cancelled, never delivered
late) — plus `confirm_requires_user`, which refuses a confirmation from a non-human
initiator. `sender_kind` is derived from the transport (`user` on loopback, `remote_user`
from an authenticated remote device) and never read from the body; `initiator` (`user` |
`auto`) is recorded on every delivery attempt. The `/queue/auto*` routes carry runtime
policy, not config: live agent runs receive bounded default-on rows, while explicit opt-outs
and the pause survive a restart and depend on no provider
(`features/auto-delivery.md`).
`/queue/mailbox` is an application-wide view over the same message rows, partitioned by authorship rather than inbox/outbox direction and optionally filtered by Project or target session before its result limit (`features/agent-messaging.md`).
It backs the **fleet queue** surface; the route keeps its original name because renaming a daemon path for a UI rename would be a breaking change bought with nothing.

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
POST   /sessions/{id}/read
POST   /sessions/{id}/title/regenerate
POST   /sessions/{id}/standing-activity/clear
DELETE /sessions/{id}
POST   /sessions/{id}/input
POST   /sessions/{id}/broadcast-set
POST   /broadcast/input
POST   /sessions/{id}/attachments   multipart `file`; X-Mux-User-Gesture: terminal-attachment
POST   /sessions/{id}/media         legacy image-only compatibility route
GET    /sessions/{id}/last-reply
GET    /sessions/{id}/transcript[?limit=]
GET    /sessions/{id}/skills[?refresh=1]
GET    /sessions/{id}/agent-environment[?refresh=1]
```

`POST /sessions/{id}/title/regenerate` accepts no body and returns `202 {ok:true}` after emitting
an asynchronous `title_regenerate_requested` event. It is limited to live auto-named Claude/Codex
runs; ended, shell, and manually named sessions are rejected. Provider and budget failures remain
visible through automation diagnostics and never block the agent lifecycle.

`POST /sessions/{id}/read` takes an optional `{turn_seq?: number}` (the session's current
`turn_seq` when omitted) and returns `{id, turn_seq, read_turn_seq, read_at}`.
It acknowledges completed turns for the **user**, not for one browser: the mark lives on the
session record, so it follows every device and survives a reload.
The write is monotone and clamped to the counter the daemon has actually reached - a device that
is behind cannot un-read what another cleared, and no client can acknowledge a turn that has not
happened and silently swallow the next real one.
A no-op acknowledgement emits nothing; a real one publishes a session update and a `session_read`
event so other devices converge.
Separate from `PATCH /sessions/{id}` because it is written on a dwell timer whenever a human is
looking at a pane and must not carry that route's history metadata write.

`POST /sessions/{id}/standing-activity/clear` takes an optional
`{kind?: 'loop'|'cron'|'background_tasks'|'subagents'}` (the whole set when omitted or when the
body is absent) and returns `{ok, cleared, standing_activity}`. It **retracts only**: annotations
are not states, so this cannot move `state`, `awaiting_reason`, or `delivery_state`, and it cannot
assert activity. An unknown `kind` is rejected. Every clear is ledgered with evidence `manual` and
drops the run-scoped launch bookkeeping, so a later duplicate completion cannot decrement a fresh
annotation (`design/features/status-detection.md`).

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
  seed_text?: string                // agent backends only; ≤ 500k chars
}
```

`project_id` is required. `worktree` and unknown fields are rejected. Session PATCH rejects
Project ownership changes.

`cwd` defaults to the owning Project root and may name a subdirectory of it (a task that runs
in `./frontend`). Containment is enforced in the spawn handler, which is the only layer that
knows which Project owns the request: the value is resolved (relative against the root,
symlinks collapsed) and rejected if it lands outside. One allow-listed exception, and only
for spawns: a path that `git worktree list --porcelain` reports as a worktree of the
Project's own repository is admitted even though it sits outside the root, because a
parallel agent checkout is the same codebase on another branch. Git is the authority, so no
arbitrary absolute path qualifies; only worktree roots do; and the query runs only after
plain containment has already failed. Project Actions do not get this exception
(`features/git.md`). `env` merges over the shell profile's
environment and under mux's own identity variables, so a spawned shell can never present
another session's hook credentials. Both exist because a Project Action step declares its own
directory and environment; encoding them into `argv` instead is what previously forced a
swe-mux executable into every task's process tree.

`argv` is appended after the adapter's own flags, so for an agent backend it becomes the CLI's
trailing positional prompt: that is how a session is seeded with a first message without
writing into a TUI that is not ready for input. `seed_text` is the preferred seeding field
and removes the former client-side 20,000-character ceiling: the daemon inlines a body at or
under that bound into argv itself (with the leading-dash guard) and stages a longer one into
the workspace at `.swe-mux/seeds/` (gitignored, aged out after 14 days), seeding a short
prompt that reads the staged file — which also removes the quoting-inflation risk a long
Windows command line carries.

`POST /sessions/{id}/input` is the only raw-input path that reaches a session whose pane is
not mounted in the caller's browser; the browser uses it for composer fill (insert without
submit). A multi-line body must arrive wrapped in bracketed paste (`ESC[200~` … `ESC[201~`,
newlines as CR) or the agent composer submits at every line. Actual message *delivery*
(paste + submit) to a live agent goes through the prompt queue's `POST /queue/send-next`,
which performs both writes daemon-side with the same evidence accounting.

`POST /sessions/{id}/attachments` accepts exactly one multipart file for a live Claude/Codex
session and returns `{id, name, path, relative_path, reference, kind, media_type, bytes}`. Storage
is under the owning Project's `.swe-mux/attachments/`, except that an agent spawned at an
out-of-Project Git worktree uses that validated worktree root. General files are limited to
25 MiB; content-verified PNG/JPEG/WebP/GIF inputs use `kind: "image"` and a 10 MiB limit. Per
session limits are 32 files and 100 MiB. The explicit gesture header is required. The older
`/media` route keeps its image-only gesture headers and response compatibility, but now delegates
to the same persistent workspace storage. Neither endpoint writes to the PTY; the browser inserts
the returned reference as unicast draft input without submitting it.

`GET /sessions` adds a compact, read-only `delivery_readiness` object with
`state: safe|blocked|unknown`, a reason, and `authorized: false`. It is not accepted on writes.
Rows carry `unsent_input` — `{since}` in epoch seconds, or absent — the daemon's estimate that
text is sitting unsent in that session's composer, derived from the bytes every operator path
writes to the PTY (`features/terminal-input.md`).
It is present only while something is there, so presence is the whole signal, and the character
count behind it is deliberately not published: it is inferred from keystrokes and a number on
screen would be read as a measurement.
It is process-scoped — a daemon restart forgets it, because the byte history it comes from does
not survive one — and cross-device, because it describes the PTY rather than a client.
`composer_input_changed` (`session_id`, `source`, `pending`) announces the empty/non-empty
crossing and nothing between.
Every `GET /sessions` row and every PTY `state`/`update`/`exit` snapshot carries
`_snapshot_generation`, `_snapshot_revision`, and `_snapshot_enriched`.
The generation identifies one daemon process, the revision orders one session inside that generation, and the enriched flag identifies REST rows that authoritatively carry generated-title and delivery-readiness presentation fields.
Clients reject lower revisions from the same generation, accept a new generation even when its revision resets, and preserve enriched fields when applying a raw PTY snapshot for the same agent run.
Rows also carry `idle_reason`, the idle-axis sibling of `awaiting_reason`:
`waiting_on_background` means the turn genuinely ended (the composer accepts input,
`delivery_state` is unchanged) while the agent has background work that will wake it back
up. Completion sounds and push alerts skip that turn end; the next one is the moment worth
the user's attention.
Rows carry nullable `agent_loaded_at`, the start of the current Claude or Codex process generation.
Unlike `agent_run_started_at`, it survives an in-process conversation rollover and daemon adoption.
Rows carry `standing_activity`, the standing-engagement annotation axis: a list of
`{kind: loop|cron|background_tasks|subagents, source, evidence, since, expires_at,
count, detail}` objects describing engagements that outlive the turn (an armed `/loop`
wakeup, a cron schedule, running background tasks, live subagents). Annotations are not
states — `state`, `awaiting_reason`, and `delivery_readiness` are unaffected — and each
either self-expires (`expires_at`) or is positively cleared. The same list appears on
`GET /sessions/{sid}/state-log` alongside the transition ledger, whose non-transition
entries (`kind: "standing_activity"`, action `added|updated|removed|expired`) record every
mutation (`features/status-detection.md`).
Session rows do not expose or own notes. `spawn_backend` and `spawn_native_session_id` identify the immutable root
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
`{type:"attach_ready", cols, rows, renderer, hidden, output_flow_control}`. It applies those dimensions before
`replay_start`; an old-style `resize` frame also releases replay, and timeout preserves
compatibility with clients that send neither. Other frames arriving during this window are
buffered and handled after `replay_end`. Later `attach_ready` frames are equivalent to `resize`.
When `output_flow_control` is true, the daemon counts binary output sent to that connection and pauses at 128 KiB unacknowledged.
The client sends `{type:"output_ack", bytes}` from xterm write callbacks, where `bytes` is the newly parsed byte count, and the daemon resumes as credit returns.
Attach and resync replay participate in the same accounting.
The capability is opt-in so older browser bundles that never send acknowledgements retain unlimited delivery rather than stalling.
Replay is bounded by `attach_replay_bytes` (default 512 KiB) rather than by full retention, and
the same bound applies to a resync, which resets the client's terminal and so receives a complete
replay into an empty buffer rather than a patch. A pane the client is keeping mounted but not
showing reports `hidden:true` exactly as a backgrounded browser tab does, which deregisters its
viewport from geometry arbitration.
Any client may send `{type:"repaint"}` after judging its parsed replay scrollback-free
(`features/sessions.md`); the daemon honors it only for `repaints_scrollback` harnesses,
rate-limited per session, by pulsing the PTY one column and back so the child restates its
transcript. An alternate-screen harness needs no such frame: whenever its replay was a bounded
window rather than everything retained, the daemon runs the same pulse itself once the attach
completes, because a slice of a differential frame stream carries no evidence of what it is
missing for either end to judge (`replay_needs_repaint`, `features/sessions.md`). `{type:"client_diagnostic", phase, detail}` persists a client-side terminal repair
to the durable event log as `terminal_client_repair`; phases outside the server's allowlist are
dropped, `detail` is clamped, and emission is rate-limited per session.
Physical keyboard, IME, and paste input may add `{input_seq, client_sent_at_ms, client_event_delay_ms, client_queue_delay_ms, input_source, ws_buffered_bytes}` to the ordinary `input` frame.
The daemon writes the input before replying with `{type:"input_ack", input_seq, server_received_at_ms}` and copies the bounded timing fields into sampled `terminal_input` events.
Latency phases `input_event_delay`, `input_main_thread_stall`, `input_socket_backlog`, `input_ack_latency`, and `input_echo_latency` use the same `client_diagnostic` frame and persist as `terminal_input_diagnostic`.
Each phase has its own one-second rate window, so a renderer repair cannot suppress input evidence.
Diagnostic payloads contain timing, counts, connection state, visibility, ownership, device, and renderer context but never the input text.

### Multi-device arbitration

One session can be attached from several devices, and both of the things they share — who
may type, and how big the PTY is — are decided by the daemon, not by whoever spoke last.

`{type:"claim_input", reason:"gesture"|"passive", device, focused}` asks for input.
A `gesture` claim (tap, click, keystroke) always wins. A `passive` claim (attach,
reconnect, restored DOM focus) is refused outright when `focused` is false, refused
while a *different* device class is leading, granted over an owner on another device
class when *this* one is leading, and otherwise refused while the current owner has
been typed into within 10 s. The leading device class is the active one whose human
touched it most recently — both classes are active at once whenever a desktop is left
open and focused (it counts as active for two minutes past its last keystroke), and
that is exactly the moment someone picks up their phone, so recency is the tiebreak. A missing `reason` is read as `gesture`, so
pre-existing clients keep their old semantics.

Which device leads comes from the daemon's presence tracking (see the `/events`
`presence` frame below), not from the claim; `device` on the claim is the same device
class the presence frame reports, since the daemon compares the two. It has to: ownership is per session and
the 10 s window covers only the session being typed into, so neither can express "the
human is on their phone right now" — and without that, every session opened on the
phone had to be claimed by hand and any desktop reconnect took it straight back.
`focused` is likewise per device class: a phone reports it from visibility alone,
because `document.hasFocus()` answers a window-manager question a phone has no answer
for and mobile engines report inconsistently, which had the phone refused as a
background window on every passive claim it made. The reply is
`{type:"input_owner", active, epoch, reason, owner_device}`; the displaced owner gets the
same frame with `reason:"claimed_elsewhere"`, and every remaining client gets
`{type:"input_owner_released", epoch}` when the owner detaches. `epoch` increments on each
transfer (not on an owner renewing its own claim) so a client can discard an ownership
frame that lost a race with a newer one. A refusal is *not* a displacement: clients must
not re-claim on one, and the daemon leaves a connection's repeated passive claims
unanswered for a second after refusing one, because answering each is what turns a
refusal into a claim loop — one live session logged 7566 refused claims that way.

Input from a connection that is not the owner is refused, not dropped:
`{type:"input_rejected", epoch, owner_device, data, broadcast, retry}` echoes the payload
back so the client can claim and resend it once (`retry:true`, which is never echoed into
a second retry). Refused xterm device replies are discarded instead — a late one is worse
than none. Refusals are counted per session and surface in `GET /api/sessions/{id}/state-log`
as `input_arbitration`, alongside the refused-claim count, the current geometry, the
leading device, and `claims`: the last two dozen decisions with the asking device, what it
reported about itself, what the daemon believed, and the verdict. A counter says a claim was
refused; only that log says which device asked and why it lost.

`GET /api/sessions/{id}/state-log` also reports the conversation identity behind the state:
`agent_run_id`, `agent_run_seq`, `native_session_id`, `agent_lifecycle_id`, and
`observation_stale_since`, `observation_stale_reason`, the transcript path, its `transcript_mtime`, `transcript_growth_ts` (when the daemon's tailer last saw that file grow), and `transcript_record_ts` (the newest valid provider timestamp carried by a record in that file).
This is the endpoint for "which conversation am I actually looking at", and staleness is the one fault that otherwise presents as a perfectly healthy session (`features/backends.md`).
The three timestamps are reported together because the comparison is the diagnosis: a frozen `transcript_mtime` beside recent growth or a recent record timestamp is a filesystem that stopped dating a live file, routine for Codex rollouts on Windows, rather than a replaced conversation.
It also carries
`cli_state` - the CLI's own published per-process state for this conversation
(`~/.claude/sessions/<pid>.json`; Claude `waiting` vetoes raw PTY evidence that would hide an approval, and `busy` vetoes only a transient raw idle repaint, but neither initiates a transition) - beside
the `standing_activity` list and `layer_readings`, the last observed reading per
detection-ladder layer.
Its `pty_explain` object exposes the effective `outcome`, raw `screen_outcome`, `outcome_source`, `cli_state_status`, and all evaluated screen rules.
The timeline and `layer_readings` expose the raw value as `pty_tail_screen`, the effective value as `pty_tail`, and its source as `pty_tail_arbitration`.

With `?from=&to=` (epoch seconds) the state-log adds `timeline`: the requested slice of the
**durable** detection timeline (`status_timeline` table), flushed from the live ring first
so the slice is complete to the moment of the request. For a session that no longer exists
the same route answers in post-mortem mode (`live: false`): the durable timeline, the
history row, and the run ids the timeline spans — the id may be the mux session id or any
of its agent-run ids (a history row's key). `timeline_sink` reports the write-behind's
volume/loss counters on every response.

```text
GET /api/sessions/{sid}/diagnostic-bundle?from=&to=
```

One-fetch investigation artifact for a status incident (window defaults to the last hour):
the durable `timeline` slice, the live `state_log` fields (null for ended sessions), the
history row, the `fleet_status_health` aggregate, and `transcripts` — the records whose
native timestamps fall inside the window, per agent run the window touches, bounded per
run. The investigation procedure that consumes it: `development/STATUS_INCIDENT_RUNBOOK.md`.

`{type:"resize", cols, rows, hidden}` registers a client's fitted size, or deregisters it
when `hidden` is true — a minimized window still reports layout and must not reshape the
PTY for the device in use. The daemon resizes to the input owner's viewport, or, with no
owner, to the smallest attached one, and announces the result to every client as
`{type:"geometry", cols, rows, owner_device}` (also sent after `replay_end` and after a
resync). A client whose own fit differs renders that geometry at a reduced font size
rather than fitting, which is what keeps two devices from resizing each other in a loop.
`GET /sessions/{id}/last-reply` returns the agent's newest assistant *segment* for gesture-safe
clipboard prefetch: the same last message `/sessions/{id}/transcript` shows, read from the same
reduction (`transcript_view.final_reply_text`), so the two cannot disagree about where a reply
starts. A reply that resumed after tool use begins at that tool boundary rather than at the
narration the agent wrote before it. Provider control acknowledgements are skipped; the route
does not type `/copy` into the PTY.

`GET /sessions/{id}/transcript` returns the live session's readable conversation for the drawer's
Transcript tab: `{messages:[{ordinal,role,ts,text,preceding_tool_calls}], hidden, truncated,
observation_stale_since, reason}`. Tool calls and CLI machinery are classified out, and an agent's
turn is merged into one message per *segment*, a segment being a run of records with no tool call
between them (`transcript_view.conversation_view`, see `ui.md`); `hidden` counts what was withheld
and `preceding_tool_calls` counts the tool calls between a message and the one before it, so
neither the filtering nor the gap is ever invisible. `ordinal` numbers the returned window rather
than the conversation, making it a display key and not an identity. Deliberately **not**
`/history/{id}/transcript`, which reindexes the run's searchable messages and loads its
annotations on every call: right for opening an entry once, wrong for a surface that refreshes on
every turn. This route only reads. Bounded twice — newest `limit` messages (default 200, max
1000) and a 64 MB tail — with `truncated` set by either; the cap exists because Codex rollouts
reach hundreds of MB. Nothing to show is a `200` with a `reason`
(`not_agent`/`no_transcript`/`unreadable`), because a shell pane and an agent that has not spoken
yet are ordinary states of a passive view, not failures. No redaction: unlike the MCP surface,
the reader is the machine's owner and needs the literal text to copy.

`GET /sessions/{id}/skills` lists the skills that session's CLI can see, read off disk from the
directories the CLI itself reads (`agent_skills.py`). Agent backends only; `409` on a shell
session. Session-scoped because both inputs are: the backend picks the roots and the invocation
prefix, and the session's *live* cwd picks the repo roots, so a worktree session legitimately
returns a different set than one in the primary checkout of the same Project. Scans are cached
per (backend, cwd, home) for ten seconds; `?refresh=1` bypasses that.

```ts
interface SkillInventory {
  backend: 'claude' | 'codex'
  cwd: string
  generated_at: number
  agent_loaded_at: number
  agent_run_started_at: number
  roots: Array<{ path: string; scope: SkillScope; kind: 'skill' | 'command'
                 origin: string; exists: boolean; count: number }>
  skills: Array<{
    name: string; description: string; path: string
    scope: SkillScope                 // 'project' | 'user' | 'plugin' | 'system'
    origin: string                    // 'user skills', 'plugin: dev-browser', …
    kind: 'skill' | 'command'         // Claude surfaces commands/*.md in the same list
    invocation: string                // '/name' on Claude, '$name' on Codex
    mtime: number
    implicit: boolean                 // false = Codex allow_implicit_invocation: false
    display_name: string | null       // Codex agents/openai.yaml interface
    short_description: string | null
    shadowed_by: string | null        // the higher-precedence root that wins the name
    added_after_start: boolean        // newer than this CLI generation, so not loaded
  }>
  errors: Array<{ path: string; message: string }>
  truncated: boolean
  skipped_plugins: string[]           // installed/cached but switched off, so not scanned
  builtin_skills_hidden: boolean      // Claude: its built-ins live in the binary
}
```

Every root is reported whether or not it exists, because a root that quietly stopped being
scanned (a CLI update moving one) is otherwise indistinguishable from an empty one. The
inventory is deliberately not exhaustive in one direction and says so: Claude's built-in skills
are compiled into the CLI and cannot be enumerated from disk, which `builtin_skills_hidden`
declares rather than implying they do not exist.

`GET /sessions/{id}/agent-environment` returns the passive Agent Environment inventory described in `features/agent-environment.md`.
It is session-scoped, uses the live trusted cwd, returns `409` for a shell, and accepts only the optional `refresh=1` cache bypass.
The inventory and reused skill discovery each have a ten-second cache; the cached CLI `--version` probe has a one-hour lifetime.
The handler runs the bounded filesystem work off the event loop.

```ts
interface AgentEnvironmentInventory {
  schema_version: 1
  backend: 'claude' | 'codex'
  cwd: string
  generated_at: number
  runtime: {
    executable: string
    version: string | null
    model: string | null
    loaded_at: number
    run_started_at: number | null
    options: Array<{label: string; value: string}>
    modes: string[]
  }
  sources: Array<{
    id: string
    label: string
    scope: AgentEnvironmentScope
    format: string
    status: string
    mtime: number | null
    changed_after_start: boolean
  }>
  sections: Array<{
    id: string
    label: string
    completeness: string
    items: Array<{
      id: string
      kind: string
      name: string
      description: string
      scope: AgentEnvironmentScope
      origin: string
      state: string
      group: string   // in-section heading for this item's run; '' when ungrouped
      owner: string   // who installed it: 'swe_mux', or '' when not knowable
      source_id: string | null
      source_label: string | null
      changed_after_start: boolean
      meta: Array<{label: string; value: string}>
    }>
    total: number
    truncated: boolean
    note: string
  }>
  diagnostics: Array<{kind: string; source_id: string | null; message: string}>
}
```

`AgentEnvironmentScope` is `built_in | managed | user | project | local | session | unknown`.
Hook items set `group` to the lifecycle event and name the handler target: the program and the one script or module its command runs, resolved structurally as described in `features/agent-environment.md`.
The response never includes hook command lines, their arguments or inline shell bodies, environment values, credentials, or unredacted MCP URLs.
Configured MCP entries intentionally have no connection-health claim because the route never starts a server.

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
POST   /sessions/{id}/voice/generate    {content_mode?: summary|verbatim, stream_id?: UUID}
POST   /sessions/{id}/voice/transcribe   Content-Type: audio/wav; bounded mono PCM
POST   /voice/transcribe                 same body and headers, no session
POST   /sessions/{id}/voice/prepare-submit  {text}
POST   /sessions/{id}/voice/submit       {utterance_id, text}
POST   /sessions/{id}/voice/approval     {action: prepare|confirm|cancel, confirmation_id?}
POST   /sessions/{id}/voice/interrupt
POST   /voice/speak                      {text, stream_id?: UUID}
GET    /voice/stt-latency
POST   /voice/stt-latency                one browser-measured stage sample
DELETE /voice/stt-latency
POST   /voice/barge-in-diagnostic        bounded confirmed/rejected playback probe
GET    /voice/clips[?session=&run=&limit=]
GET    /voice/clips/{clip_id}/audio
DELETE /voice/clips/{clip_id}
```

Transcription accepts at most 2 MiB and 35 seconds of mono 16-bit PCM at 16 kHz.
Whisper decodes the validated PCM from memory and never writes it to disk.
The optional legacy Windows SAPI engine writes bounded temporary WAV/text files, removes them after the request, and sweeps stale files left by an abandoned recognizer.
Two request headers steer transcription, both optional:
`X-Mux-Utterance-Id` correlates the daemon's decode log line with the browser's latency sample,
and `X-Mux-Decode-Profile` selects `command` (small routing model, greedy) or `dictation`
(default: the accurate model, beam search above three seconds). The profiles hold separate locks,
so a speculative routing decode cannot queue a real utterance behind it. The response carries
`{text, timings}`, where `timings` reports the daemon's own `queue_ms`, `decode_ms`, `server_ms`,
`audio_ms`, model, and beam size.

The session-free transcribe form exists for the wake-word tester: choosing a trigger word is a
recognition question, not a dictation one, and requiring a live agent to ask it would have forced
a parallel implementation of the decoder and the matcher.

`/voice/stt-latency` is the end-of-speech-to-action stage breakdown. Only the browser can measure
the whole path, so it posts the merged sample; every field is clamped on arrival rather than
trusted, because a diagnostic that can be poisoned into showing impossible stages is still
believed. `GET` returns per-stage p50/p95/max plus a separate command-only total, `DELETE` starts
a fresh measurement run, and every sample is also written to `daemon.log`, which is what survives
a restart.

`/voice/barge-in-diagnostic` records the result of the browser's playback sidechain probe.
It accepts a confirmed/rejected outcome, Silero/energy detector, optional agent/system origin, peak probability, and peak RMS.
The daemon validates and clamps the browser-supplied values before writing the record to `daemon.log`.

The Talk client first calls `/voice/prepare-submit` to recheck the live Agent target, bounded text, and non-overridable delivery protections without writing input.
It then sends Agent drafts through the mounted terminal's ordinary xterm/WebSocket input path, not through `/voice/submit`.
That is the only path that can append to text already held by the interactive application composer and then use the exact carriage return used by the visible Send control.
For paste-and-submit, the mounted pane waits 180 ms between bracketed paste and carriage return so Codex and other interactive TUIs can commit the composer text first.
Its browser acknowledgement is emitted only after that carriage return, and a missing or replaced pane rejects the request so Talk keeps its draft.
The endpoint remains a compatibility API: it is agent-only, rejects control characters, caps text at 20,000 characters, deduplicates
bounded recent `utterance_id` values, writes text plus one Enter, and advances the ordinary
human-input revision.
It refuses the prompt queue's non-overridable readiness reasons before claiming an utterance id.
Interrupt sends one Ctrl-C and records the same boundary.

Approval `prepare` requires a focused session whose stabilized state and current PTY screen both say approval.
It returns a one-use 20-second confirmation id plus the bounded operation text actually visible on that screen.
`confirm` rechecks the session, agent run, approval classifier, expiry, and screen fingerprint before writing Enter.
`cancel` invalidates only the challenge.
There is no bulk form.

`/voice/speak` validates and synthesizes bounded application-authored text through the configured TTS engine without transcript reading, summarization, or a model call.
The optional client-generated stream ID lets the requesting tab claim live segment events before the first synthesis finishes.

`/sessions/{id}/voice/generate` reads the latest assistant reply using the session/global effective content mode unless `content_mode` is supplied.
The request override is validated, applies to one clip only, and never mutates the session's persistent read-aloud preference.
The optional stream ID has the same claim-before-request role as `/voice/speak`.

Automatic, manual, and application-text synthesis returns the first coherent clip with `stream_id` and `segment_count`, then emits ordered `voice_clip_ready` events sharing `stream_id`, `segment_index`, and `segment_count`.
Replies of at most 420 characters stay in that one clip; longer replies prefer a complete opening sentence before continuing.
Each ready segment is independently playable, and later segments continue in tracked background tasks after the HTTP response.
Summary/verbatim selection remains the existing session/global contract.

## Delivery diagnostics

```text
GET /automation/injection-safety
```

Version 2 returns research-only per-session delivery checks/evidence, parser coverage, and
aggregate shadow metrics. `authorizes_actuation` and every session's `authorized` are always
false. Prompt bodies and terminal bytes are never included.

```text
GET /api/diagnostics/status-health
GET /api/diagnostics/background
GET /api/diagnostics/notifications[?days=7]
GET /api/diagnostics/network
DELETE /api/diagnostics/network
```

`status-health` reports the fleet's transition ledger: proven/inferred counts, bounds, alarm.
It aggregates the detection-hierarchy counters (`consolidation_counters`:
`screen_classifier_blind`, `cli_state_disagrees`, `nested_children_observed`,
`standing_activity_expired`) and lists `classifier_blind_sessions[]`; two or more blind
sessions raise the alarm with reason `screen_classifier_blind`
(`features/status-detection.md`).
It also reports `identity_collisions[]` — live agent sessions sharing one
`(backend, native_session_id)` or one transcript path (`{kind, backend, value, sessions}`)
— and any entry raises the alarm with reason `identity_collision`: two sessions on one
conversation is the cross-attribution that renders one session's status and tokens under
another's identity.

`background` reports whether the daemon's long-lived loops are actually running:
`loops[]` (name, running/stopped, restarts, faults, last fault + timestamp, iterations,
seconds since progress), `degraded[]`, and per-subscriber `event_bus` drop counts and queue
depths plus `tier0_capture` (captured/dropped, last error) and `deterministic_consumers`
(findings, last error, loop liveness — a detector that stopped producing findings is
otherwise indistinguishable from a quiet fleet) plus `project_contexts` (fixed path, size bound,
reads, writes, blank-file creates, and last read error). Both are in-memory per daemon
boot and do not survive a restart.

It also reports **what each loop costs**, not only whether it lives. Every `loops[]` entry
carries `busy_seconds` (wall time inside `iteration()` bodies, **awaits included**),
`busy_share` (that time over the loop's uptime), `mean_seconds`, `p50_seconds`, `p95_seconds`
and `slowest_seconds`, and `costliest[]` ranks the top loops by `busy_share`. Because awaits
count, where a loop puts its waits is part of its reported cost: a loop that awaits its batching
window inside the guard reports that as its own cost while the event loop was free throughout.
Keep waits outside the guard and wrap only the work. `busy_seconds` is wall time, not CPU, and
is not evidence of blocking — `loop_lag` is the only measurement that separates an await from a
stall. **Iteration counts
are not a cost signal and reading them as one hides the expensive work by construction**: a loop
that ticks rarely and costs a great deal per tick ranks last by frequency. Measured 2026-08-05,
`process-inspector` ran 0.15 iterations/sec — second-least frequent of 27 loops — while
consuming 45.2% of the daemon's CPU samples.

`loop_lag` reports scheduling delay for the event loop itself: `p50/p95/p99_seconds`,
`max_seconds` over a bounded window, `worst_seconds` retained for the whole process lifetime,
and `stalls` (samples at or beyond `stall_threshold_seconds`). Everything on that loop shares
one thread, so a single synchronous call delays every terminal write, websocket frame and HTTP
response behind it, and no per-subsystem metric reports that. Investigation procedure:
`development/PERFORMANCE_RUNBOOK.md`.

This is the surface that makes a poller which died — the
audited failure mode where a feature silently stops for the rest of the process lifetime —
visible instead of merely absent.

`diagnostics/notifications` reports append-only notification planner and delivery outcomes over a recent retained window.
`days` defaults to 7 and must be positive and no greater than the configured operational-telemetry retention.
The response carries `since`, `until`, `hours`, record and candidate totals, grouped rows under `by_category`, and a `waiting` aggregate with candidate, hold, suppression, settle, delivered push, delivered candidate, failure, and delivered-push-per-10-hour counts.
Rows are content-free and include no notification body, terminal content, endpoint, preference payload, or credential.

`diagnostics/network` reports a daemon-local measurement window with totals, per-peer HTTP and WebSocket counters, normalized HTTP route templates, named WebSocket channels, and `websocket_sent_payloads[]` rows keyed by `peer`, `channel`, and `kind`.
Each classified row contains `frames` and `bytes`; PTY kinds are `attach_replay`, `resync_replay`, and `live_output`.
These rows are a non-additive breakdown of already-counted sent WebSocket binary frames.
HTTP byte counts are encoded response and request bodies, excluding headers and transport
overhead.
WebSocket byte counts are application text/binary frame payloads before per-message compression.
The DELETE form records the prior totals in the rotating daemon log and resets the in-memory
window without disrupting live sessions.
Both forms are excluded from the counters so observing a window does not change it.

## History and reviews

```text
GET    /history[?q=&scope=all|user|assistant|metadata&backend=&project=&state=&external=&time_basis=started|last_message&date_from=&date_to=&cursor=]
GET    /history/projects
GET    /history/{id}/transcript[?q=&scope=all|user|assistant|metadata]
GET    /history/backfills[?project_id=]
POST   /history/backfills              {project_id}
GET    /history/backfills/{job_id}
DELETE /history/backfills/{job_id}
GET    /history/duplicates
POST   /history/duplicates/repair     {dry_run?}
POST   /history/{id}/resume           {project_id, ...}
DELETE /history/{id}
POST   /history/{id}/second-opinion   preview/confirm with project_id
GET    /history/{id}/handoff
```

Resume/review confirmation must target an existing Project and starts at its root.
Resume returns `409 conversation_live` (with the owning `session_id`) when a live session currently claims the row's native conversation; Branch, not resume, is the flow for forking a live conversation.
The resumed pane keeps the conversation's effective visible name (manual name, or generated title while auto-named) with no suffix, and it keeps the conversation's `agent_run_id` too: the resume continues one transcript, so it continues one history entry rather than opening a second over the same file.
A resume inherits that effective name before any new run is minted, so a generated title does not disappear with the old annotation key. A row the user renamed resumes under that name, never under its generated title.
Whether a resume continues the conversation is the adapter's rule: `codex resume` always does, `claude --resume` only at the conversation's recorded root. A Claude resume into a different root is a new conversation and gets its own entry plus a `resume` lineage edge.
`GET /history/duplicates` reports conversations still split across several rows. `POST /history/duplicates/repair` folds each back into its earliest row and defaults to `dry_run: true`, reporting the keeper, the rows it would remove, the values it would carry over, and any group skipped because a live pane is still writing to a duplicate. It never edits native transcripts and never touches a quarantined row.
Backfill jobs are daemon-local, cancellable, idempotent scans of complete shared native CLI history.
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
GET    /git/graph
GET    /git/commits/{oid}/changes
GET    /git/diff
POST   /git/worktrees
POST   /git/worktrees/session
DELETE /git/worktrees
GET    /processes[?session=&include_ended=1&unique_memory=1&summary=1]
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

Git review routes are derived, read-only tooling APIs except for the existing worktree create and remove mutations.
Every review read is Project-scoped and rejects unlisted parameters instead of accepting caller-supplied repositories or arbitrary refs.

The session snapshot's `git` object and the `git_changed.git` event field carry the same shape, which describes the **checkout** a session is in and never the session:

```text
git {
  head,                             # full current commit OID, or null for no readable HEAD
  branch, dirty, ahead, behind,     # branch name, dirty file count, upstream divergence
  worktree,                         # leaf name when a linked worktree, else null
  root,                             # absolute working-tree root: the identity of the checkout
  added, removed,                   # tracked lines vs HEAD; null means unmeasured, not zero
  compare_ref,                      # base the branch-scoped counts are measured from
  compare_added, compare_removed, compare_files
}
```

On a same-checkout HEAD transition, `git_changed.previous_head` carries the prior full commit OID and the existing top-level `head` evidence field carries the new one.
The first poll and a checkout switch set `previous_head` to `null`, so they establish a baseline without inventing provenance.

Sessions sharing a working tree report identical values by construction.
`compare_*` measure the working tree against its merge base with `compare_ref`, so they include
committed work that the HEAD-scoped `added`/`removed` have already lost; the ref is the same one
`GET /git/worktrees` reports. Any of these being `null` means the measurement could not be made,
which is deliberately distinct from a measured `0`.

`GET /git/worktrees?project_id=ID` returns repository identity, comparison metadata, and a bounded overview for every listed worktree.
Comparison inference tries the Project override, `origin/HEAD`, the single non-origin remote default, local `main`, then local `master`.
It performs no fetch and returns no comparison ref when none resolves.
The comparison object names the exact effective ref, source, candidates, and any unavailable reason.
Each non-bare worktree separates `conflicted`, `unstaged`, `staged`, and merge-base `branch_delta` summaries, with comparison ahead and behind counts omitted when unavailable.
Each summary reports totals, additions, deletions, binary and submodule counts, the first 200 files, and a truncation flag.

`GET /git/graph?project_id=ID&limit=N` returns `{lines, limit, has_more}` for all local refs.
`limit` is 1 to 200 with a default of 80.
Lines are either `{kind:"connector", graph}` or typed commit rows carrying `graph`, `oid`, `parents`, `refs`, `author`, `committed_at`, and `subject`.
Git supplies the graph prefixes and the browser renders them without reconstructing topology.

`GET /git/provenance?project_id=ID[&session_id=ID][&agent_run_id=ID][&commit=FULL_OID][&limit=N]` returns `{items}` from the durable session-to-commit evidence ledger.
`project_id` is required and must name a registered Project, `limit` is 1 to 500 with a default of 200, and repeated `commit` parameters select multiple full 40-to-64-character object IDs.
Every item carries its durable id, session id and captured label, nullable agent run id, Project, exact worktree root, full commit OID, parent OIDs, copied subject and commit time, previous HEAD, relationship, confidence, ambiguity flag, source, nullable source event sequence and tool-call id, and first/latest observation times.
Rows are newest-first by their first observation time.
The route rejects unknown parameters and never accepts a repository path from the caller.

`GET /git/commits/{full_oid}/changes?project_id=ID[&parent=FULL_OID]` validates the commit and selected direct parent, then returns the complete parent list, the commit `message`, and a bounded file summary.
`message` is the whole message, subject and body, capped at 16,384 characters; it is independent of the selected parent.
Root commits use Git's initial-commit comparison, and merge commits default to their first parent while allowing another direct parent.

`GET /git/diff` requires `project_id`, `scope`, and `path` plus the locator fields for `unstaged`, `staged`, `conflicted`, `branch`, or `commit` scope.
Local scopes require an exact listed `worktree`; commit scope requires a full commit OID and optional validated parent.
Responses contain one bounded patch snapshot with identities, effective comparison fields, HEAD for local scopes, SHA-256, truncation metadata, and explicit binary or too-large state.
`expected_head` and `patch_hash` implement stale checks without returning a newly changed body.
Patch output is capped at 1 MiB and 10,000 lines and subprocess timeouts are four seconds.

Git review failures use typed JSON `{error, code}` with status 400, 404, 409, 413, or 504 as appropriate.
Success and error logs contain metadata only and never patch bodies or file contents.

`POST /git/worktrees` takes `{cwd, path, branch?, start_point?, spawn?}`.
With `spawn` present as an ordinary spawn body with required `project_id`, it creates the worktree and then starts a session whose cwd is forced to the new tree.
Before spawn it runs `[worktree].setup_command` from the Project config, or an executable `.worktree-setup` convention when no override exists.
`POST /git/worktrees/session` takes `{path, spawn}` and applies the same setup and forced-cwd spawn contract to an existing exact Git-listed Project worktree.
The split endpoint lets an interactive client dismiss creation UI after the durable `POST /git/worktrees` result while setup continues.
The reply always carries `spawn: {status}` where status is `not_requested | spawned | error`, plus `session_id` and the complete `session` snapshot on success or `error` on failure.
Requested spawns also carry `spawn.setup` with `status: not_configured | succeeded | failed | timed_out | error`, source, command, exit code, duration, truncation state, and an error summary without captured command output.
Captured setup output is instead seeded into the spawned session's bounded scrollback before harness output.
Setup failure does not change `spawn.status`: session creation is still attempted and the setup result marks the tree unbootstrapped.
The worktree is the durable artifact, so a failed spawn is reported rather than raised and never unwinds it.
If the target parent is missing, the daemon creates it only when it is below the configured `worktree_root`; otherwise the existing-parent requirement remains.
The public configuration returns `worktree_root` as an absolute path, resolving an empty stored value to `<data_dir>/worktrees`.

Process snapshots expose bounded observational states `active | exited | escaped |
suspected_orphan | stale | inaccessible`. Actions revalidate PID, creation-time identity,
and ownership immediately before signaling; no state triggers automatic termination.
Each process carries stable `attribution_version`, `attribution_source`, `last_attributed_at`, nullable `last_job_confirmed_at`, and derived `server_eligible`.
Fleet and session responses carry a bounded command-free `ownership_diagnostics` list for rejected causal edges, infrastructure claims, ownership conflicts, and legacy repair.
`GET /processes` returns running processes only; `include_ended=1` adds records that ended
during the current daemon run. Ended records never contribute to resource totals.
`summary=1` returns the fleet projection used by the always-mounted browser watch.
It retains session and Project ids, process ids, command labels, exit state, CPU, RSS, listeners,
server eligibility, aggregate totals, system CPU, and daemon totals while omitting process
identity evidence, ownership diagnostics, parent/connection detail, and daemon members.
It cannot be combined with `session`, `include_ended`, or `unique_memory`.
Fleet responses also carry nullable `system_cpu_pct`, normalized to 0–100% whole-machine
utilization from consecutive cumulative OS CPU-counter samples.
It is null until two samples establish an interval.
Per-process and owned-bucket `cpu_pct` values remain additive per logical processor and can
exceed 100%; resource summaries present those attributable values as equivalent core load.
`memory_bytes` is RSS (the working set), which counts shared pages once per mapping process
and therefore overstates a summed tree. `unique_memory=1` additionally samples unique set size
into `memory_unique_bytes` per process, per daemon member, and in totals; it is opt-in because
it walks every working set at roughly 200x the cost of the RSS read, so only user-opened views
request it. Totals report it only when every contributor supplied one.

Preview URLs are HTTP(S), literal loopback, and credential/query/fragment-free.
Registration deduplicates by canonical Project endpoint and records the session that owns the live listener, even when another terminal printed the clicked URL.
Automatic Project listener discovery creates `listed=false` route identities for cross-service rewriting.
A bounded, cached HTTP probe promotes browser-facing HTML endpoints to `listed=true`; explicit registration promotes any accepted endpoint.
`GET /previews` returns listed items only, plus session-scoped raw listener candidates when requested with `?session=`.
`attach=true` opens or activates the stable Preview leaf beside the owner, and closing the leaf leaves the listed registration intact.
Sandboxed Preview fetch/XHR/WebSocket traffic to another registered Project service is rewritten through that service's `/preview/{id}/…` route.

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
GET    /telemetry/quota-series[?provider=&account=&since=&until=&resolution=raw|daily&limit=]
POST   /telemetry/quota-resets/review {ids: [reset_id], resolution: seen|manual_usage|discarded}
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
`/telemetry/quota-series` returns server-filtered account timelines with `interpretation: quota_utilization_not_token_usage`.
Daily responses merge retained rollups with unpruned samples and keep different verified provider identities in a reused local account slot separate.

## Agent MCP surface

`POST /mcp` is the streamable-HTTP MCP endpoint for spawned agent sessions (JSON-RPC 2.0, protocol 2025-06-18; loopback-only; 256 KiB body cap; 120 calls/min per session).
Authentication is `Authorization: Bearer <MUX_MCP_TOKEN>`; the token is per-session, minted at spawn, injected into the session environment beside `MUX_MCP_URL`, and survives daemon restarts via supervisor meta.
The Project-scoped tools are `list_sessions`, `get_session`, `read_transcript`, `search_history`, `memory_sources`, `read_memory`, `project_notes`, `read_project_note`, `message_status`, `spawn_requests`, `notify`, and `request_spawn`.
Session results expose the stable id, backend-generated `name`, and UI-equivalent `display_name`; an exact unique display name is accepted wherever a tool targets a session.
`list_sessions` filters by query and pages a combined live/ended result capped at 25 compact rows and 32 KiB per call.
`search_history` performs server-side message ranking over the Project history index and returns compact hits by default.
It supports literal hybrid/all-term/any-term/phrase/substring matching plus role, raw/generated title, backend, persisted state, exact run, session-start, and matching-message time filters.
Its lower date boundaries are inclusive, upper date boundaries are exclusive, default limit is eight hits, default hit-payload budget is 16 KiB, and cursors are bound to the normalized query.
Its response includes `search_index_ready`; `false` means a post-upgrade repair is using bounded literal filtering until both rebuildable FTS indexes reach their durable watermark.
`read_transcript(hit_id=...)` reads a bounded indexed neighborhood around one search hit, defaulting to one message before and two after.
The opaque hit is bound to the caller's Project scope, run, message ordinal, and transcript-index watermark; a changed transcript reports a stale hit instead of returning shifted text.
Without a hit, `read_transcript` pages from either end through an opaque cursor bound to one `agent_run_id`, labels every message with run id/sequence, and includes system/meta records only by explicit opt-in.
Ordinary reads default to 12 messages and 32 KiB of message text while preserving explicit expansion to 200 messages and 512 KiB.
An omitted session id or `self` addresses the caller; an explicit `agent_run_id` can select the current run or one of only that caller's superseded runs.
`get_session` includes the run's pinned title and opening request, exposes the caller's own superseded run ids, and also defaults to `self`.
All reads remain own-Project only; v0.5 defines no cross-Project grant.
Claude's generated settings allow the ten declared read tools without a prompt, while both write tools remain permission-gated.
Tool annotations declare the same read/write split.
Successful MCP calls record content-free per-tool call, serialized-response-byte, and truncation counters in background diagnostics.
`notify` only stages a queue message with a visible sender/message/correlation envelope and `request_spawn` only creates an inert Fleet Queue approval row.
The full contract is `features/mux-mcp.md`.
An unknown token returns 401, non-loopback access returns 403, and rate overflow returns 429 with `Retry-After`.

## Other API groups

Configuration/keybindings, automation/annotations/lineage, events/notifications, voice,
remote status, filesystem discovery, and preview proxy routes retain their feature-specific
contracts described in the corresponding `features/` documents.
The keybinding policy separates `browser_reserved`, `desktop_only`, `application_reserved`,
and `terminal_reserved`; application-reserved UI scale chords are rejected as configurable bindings.

`GET /events[?after_seq=N][&session=<id>]` is the live event stream. `after_seq` is a resume
cursor: the client tracks the highest `seq` it has applied and sends it on reconnect, and the
server replays up to 64 events above it, oldest first, with each replayed event marked
`replay: true`.
With no cursor the server sends `{"type":"events_ready","sequence":N}` and no history;
the client's initial REST snapshot supplies state while the watermark closes the subscribe race.
When more than 64 events were missed, `{"type":"events_gap","reason":"catchup_truncated",
"sequence":N}` advances to the current watermark and tells the client to perform one full refresh.
If catch-up contains only trailing browser-irrelevant audit events, an `events_cursor` control
frame advances the sequence without transferring their payloads.
`PreToolUse`, `PostToolUse`, `tool_use`, and `tool_result` remain durable but are omitted from
browser delivery because user-visible state changes use separate event types.
A malformed `after_seq` is rejected with 400.

The stream is otherwise server-to-client, with one exception: clients may send
`{"type":"presence", profile, visible, focused, interaction_age}` to report which device
the user is at. `interaction_age` is seconds since the last pointer/key event on that
client — an age, not a timestamp, so a phone's clock skew cannot make it look
permanently present. This socket carries it (rather than the push-presence endpoint)
because every client holds one whether or not it can receive Web Push, and because the
socket closing is itself the signal that the device is gone. Unparseable frames and
unknown profiles are ignored without dropping the connection. `GET /api/push/presence`
returns the daemon's current view — per device: visible, focused, interaction age,
heartbeat age, and whether it counts as active — because the suppression it feeds is
invisible by construction: getting it wrong shows up as a notification that never came.

`GET /api/settings/bundle?cwd=<path>` aggregates the Settings panel's open payload
(config, automation rules, keybindings, profiles, projects, automation status, provider
status, usage, and — when `cwd` is supplied — project config) into one response. `config`
failing fails the request; any other part degrades to `null` with its reason keyed under
`errors`. The individual endpoints remain authoritative and unchanged.
The provider section supplies the cached structured-output model catalog used by the Automation
tab's live-filtered cheap and standard model pickers.

`GET /api/automation/dashboard` includes recent observer-call diagnostics without response
content: requested and resolved model, generation, provider, finish reason, HTTP status,
retryability, token and cost usage, latency, and response content type and length.

## Attention ranking

Behaviour and invariants: `features/attention-ranking.md`.

`GET /api/attention/inbox[?limit=N]` returns open ranked incidents grouped by channel
(`interrupt_now`, `next_breakpoint`, `inbox`, `digest`), plus `budget` (daily bound, used,
remaining, hourly burst limiter), `fanout` (`ok` with a `sustainable_agents` estimate, or
`insufficient_samples` with no number), `resumption_lag`, `suppressed` counts by reason,
mined `rules`, and `delivery`. `delivery` is always `{"push": false, "surface": "in_app"}`:
ranked items reach no device, and the response states that rather than implying it.

`POST /api/attention/items/{item_id}/feedback` with `{"action": "acted"|"dismissed"}` resolves
one item and records the only input rule mining reads. An unknown item is 404; any other
action is rejected.

`POST /api/attention/rules` with `{incident_class, channel, accept}` accepts or rejects a
mined demotion rule and returns the current rule set. An accepted rule expires after 14 days
and returns as proposed, which is the periodic forced re-judgment.

`GET /api/attention/absence[?since=<epoch>]` is the away report. Its original keys
(`sessions`, `annotations`, `notifications`, `since`) are unchanged; it additionally carries
`items`, `boundaries` (each rollover rendered as an explicit boundary rather than smoothed
over), `suppressed`, `fanout`, and `resumption_lag`.

Ranking emits `attention_item_ranked` and `attention_breakpoint`; a shell pane whose command
finished emits `shell_command_finished` with its exit status. Loop health and narration
counters are under `attention_ranking` and `attention_narration` in
`GET /api/diagnostics/background`.

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
