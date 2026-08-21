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
with no UI handle, so the count must be visible rather than only a log line.
It also reports `session_recovery: bool` and `cold_sessions`: sessions rebuilt from durable
recovery data because their processes died with a daemon that never recorded how they ended
(`features/session-recovery.md`). A non-zero count is the signal that something took the whole
app down, which no other field says. Shutdown exists only when the daemon
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
POST /api/daemon/redeploy           {force?: bool}
GET  /api/daemon/redeploy
POST /api/daemon/redeploy/announce
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
The endpoint passes `--restore-visibility`; the script samples whether a desktop shell window is visible immediately before the stop and uses that presentation for success, swap-failure relaunch, and rollback relaunch.
It also passes `--lock-held`, because the lock above is already claimed and
already names the child; without it the script would refuse itself.

GET returns `{running, pid, phase, log_tail, last_result, interrupted, available}`.
`interrupted` is served whether or not a redeploy is in flight - the confirm
dialog reads it before you commit - and carries `previews[]` (each with its
`proxy_path`, stable across the restart), `kills_processes: false`, and a note
saying so. The 202 from POST carries the same object, so an agent that triggered
the redeploy can say what it is about to interrupt rather than discovering it as
a dead proxy minutes later. Nothing in it can refuse a redeploy.
`log_tail` is served only when it belongs to the run being reported: the lock is
created at run start, so while one is in flight a `redeploy.log` older than the
lock is a previous run's and is withheld. Only a redeploy this daemon spawned
writes that file at all - one launched from a terminal prints to its own stdout
- so without the check the progress chip would render an earlier redeploy's
build output for the whole of this one, which reads as real progress and is not.
The same rule applies to the tail embedded in `redeploy-result.json`.
`phase` is `"building"` whenever a lock is live and `"idle"` otherwise -
answering at all means the daemon is up, so a live lock is always the build
stage; the stop/swap/relaunch stage has no daemon left to ask, which is why the
UI infers that one from health probes rather than from here. `last_result` is
the previous run's outcome, read from `<data_dir>/redeploy-result.json`, and is
what lets a reconnecting client report a rollback: the app comes back looking
normal, so otherwise nothing would say the change never shipped.

POST `/api/daemon/redeploy/announce` (loopback-only) broadcasts
`daemon_redeploy_started` for a redeploy this daemon did not spawn - one run
straight from a terminal, which was previously invisible to every client until
the daemon vanished underneath them. It is refused with
`409 no_redeploy_in_flight` unless `redeploy.lock` names a live process: it
exists to describe a redeploy that is really happening, not to let anything put
the fleet's UI into a maintenance mode. The daemon separately emits
`daemon_redeploy_stopping` from `POST /api/desktop/shutdown` while a redeploy is
in flight, and lingers briefly afterwards so that frame reaches the `/events`
sockets the shutdown is about to close.

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

`DELETE /project-groups/{group_id}` ungroups the Group's Projects in the same transaction that removes the row, so nothing survives pointing at a Group that no longer exists.
It and `PATCH /project-groups/{group_id}` answer an unknown id with `400 unknown group` rather than a server fault, because either request can come from a menu drawn before another device deleted the Group.

Project payloads add `created_at` (registration), daemon-persisted `last_used_at` (explicit user use), derived `last_activity` (latest session activity from history), `history_count`, and `root_available`.
Time fields use epoch seconds with `0` when unknown.
`POST /projects/{project_id}/used` advances `last_used_at` monotonically and emits `project_used {project_id, last_used_at, reason}` so connected clients converge without sharing browser storage.
Both order endpoints demand a
complete permutation plus the `expected_order` the client last saw, answering `409` with
`{"code": "order_conflict"}` when another device already moved something.

Project creation rejects duplicate active canonical roots and an empty root, and initializes
`.swe-mux/`. `create_missing` makes exactly one folder: the parent must already exist, an
already-present folder is accepted, and the duplicate/group checks run first so a rejected
request leaves no stray directory.
The new folder's leaf is validated server-side under the same Windows-safe rules as
`POST /resources` (invalid/reserved names and the `.git`/`.swe-mux` control directories
refused), because the dialog's folder field is free text and the assistant's create_project
tool has no dialog at all; adopting a folder that already exists skips the leaf check.
When the canonical root belongs to a removed Project, creation restores the original Project ID and responds with HTTP 200 plus `restored=true`; a new identity responds with HTTP 201 and `restored=false`.
Project removal tombstones the registration, preserves History and disk contents, and returns the number of preserved history rows.
Only live sessions block removal; the HTTP 409 response carries `code=project_has_live_sessions` and a bounded identity list.
Project and note read paths never recreate a missing canonical root.
Deleting a Group ungroups its Projects.

```text
POST /projects/{project_id}/init-scripts/run   {script_ids}
```

Runs the user's own setup commands (`project_init_scripts` in the daemon config) as ordinary
one-shot shell terminals at the Project root, started in configured order. Unknown ids are
rejected as a whole; a launch failure is reported per script and returns `207`. These are
machine-local and user-authored, so no trust fingerprint is involved.

```text
GET  /projects/{project_id}/actions
GET  /projects/{project_id}/actions/diff
GET  /projects/{project_id}/actions/source
PUT  /projects/{project_id}/actions/source  {text, revision}
POST /projects/{project_id}/actions/trust   {fingerprint}            # every present file
POST /projects/{project_id}/actions/trust   {source, fingerprint}    # one file
POST /projects/{project_id}/actions/run     {action_id, inputs}
```

The source routes read and write `.swe-mux/actions.toml` for the Run menu's editor. `GET`
returns `{path, exists, text, revision, starter}`, answering a missing file with a starter
template rather than a 404. `PUT` validates the text before writing, refuses a stale
`revision` and an unparseable file, returns import `diagnostics` for a file that parses with
problems, and returns the fresh catalog. A save always changes the file's bytes and therefore
un-approves it.

Action discovery is inert. The catalog returns `fingerprint`, `trusted`, contributing `sources`,
per-file approval state in `files`, normalized actions/steps with `description`, `source_path`,
`trusted`, and declared `inputs`, and import diagnostics. `trusted` on the catalog is true only
when every present file is approved; `trusted` on an action reflects its own source file, which is
what governs whether it can run. Trust succeeds only for the current exact fingerprint, of the
whole catalog or of the one named `source`. The diff route reports, per source, its status and a
unified diff against the retained approved bytes, bounded at 64 KiB. Run substitutes `inputs` into
the approved template, returns the spawned ordinary session snapshots plus per-step errors, and
returns `409 project_actions_trust_required` when the action's file is untrusted or changed.

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
  default_profile_id?: string                       // shell launch profile
  default_agent_profiles?: Record<string, string>   // backend -> launch profile id
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
GET     /grants
POST    /grants   {install?, project_id?, automations?, values?, revision?}
GET     /schedules?project_id=                every schedule, or one Project's
POST    /schedules/preview                    {definition}          next fire times, unsaved
GET     /projects/{project_id}/schedules
POST    /projects/{project_id}/schedules      {definition}
PATCH   /schedules/{schedule_id}              {definition, revision?} | {enabled}
DELETE  /schedules/{schedule_id}
POST    /schedules/{schedule_id}/run          fire now, guards intact
GET     /schedules/{schedule_id}/runs?limit=
GET     /projects/{project_id}/project-context
PUT     /projects/{project_id}/project-context {markdown, revision}
GET     /sessions/{session_id}/scan-timeline
PUT     /sessions/{session_id}/scan-timeline  {enabled}
POST    /sessions/{session_id}/scan-timeline/scan
POST    /sessions/{session_id}/scan-timeline/backfill
DELETE  /sessions/{session_id}/scan-timeline/backfill
GET     /sessions/{session_id}/scan-timeline/{record_id}?rehydrate=0|1
GET     /sessions/{session_id}/catch-me-up
GET     /attention/blockers
GET     /history/scan-search?q=&run_id=|project_id=
GET     /sessions/{session_id}/change-map?scope=<session|branch|project>&hops=<1..4>
```

`catch-me-up`, `attention/blockers`, and `history/scan-search` are the Phase 7.7 scan-timeline
pull consumers (`catch_me_up`, `live_blockers`, `semantic_history_search`). Each returns
`enabled: false` rather than a fabricated empty when its Project opt-in is off, and every result
names the `agent_run_id` it came from (`features/scan-timeline.md`).

`change-map` is the Phase 7.9 per-session code change map: a bounded server-side subgraph of
edited files, their blast radius, and one hop of forward context (`features/code-graph.md`).
`available: false` with a typed `disabled_reason` (`unsupported`, `no_project`,
`automation_disabled`) is the answer when the graph cannot be built, never a fake empty graph.

`scope` selects which question is answered — `session` (this run's write facts plus everything
the session has landed, from the git provenance ledger), `branch` (everything this checkout has
changed against its comparison base, committed or not), or `project` (every session's edits, one
hue each; `unify=true` is still accepted as an alias). Omitting it lets the daemon choose, which
is what makes a worktree session default to its branch without the client knowing it is in one.
The response carries what the client cannot infer:

- `scope` is what was **served**, `scopes` is what may be asked for, and `scope_fallback` names
  why a `branch` request was not honoured. `branch` is absent from `scopes` when the checkout has
  no comparison base; an empty branch map would read as "this branch changed nothing", which is a
  claim rather than an absence.
- `checkout: {root, worktree, branch, ref, base, truncated}` names the checkout the session is
  actually working in — not the Project root, which is merely where the Project was registered.
- `excluded: {outside_root, unindexable}` counts the distinct edited files the map refused to
  draw, because the graph only ever indexes files inside the checkout and outside generated,
  vendored, and hidden directories. A map left with no nodes reports
  `empty_reason: "excluded"` rather than `"no_edits"`.
- Each node's `path` is a **casefolded graph identity and is not a filesystem path**; its
  `display_path` is the true-cased, checkout-relative one, and is absent when the file no longer
  exists. Opening a file uses `display_path` or nothing.
- A node with `indexed: false` is a seed the code index has never parsed — a file that exists
  only on this branch. Its empty neighbourhood means "not indexed here", not "nothing depends on
  it".
- `worktree` names the checkout `display_path` is relative to when that is not the Project root,
  so a worktree session's files open from the worktree rather than the primary checkout.

The old observation endpoints and `.swe-mux/observations.json` remain compatibility storage.
The current frontend has no Observation Inbox command or mounted view.
Typed `spawn_request` rows are projected into `GET /queue/mailbox` and decided once by a human through the decision route.
The decision route also acts on `kind: "control_request"` (a drafted Phase 7.6 interrupt/end): approving one runs the same shared daemon operation the granted path uses, still subject to the daemon-owner, non-agent, and readiness guards, so the human approval is what carries the authority (`features/mux-mcp.md`, `features/observations.md`).
Malformed storage reports `observations_unreadable` and is never rewritten as empty.
See `features/observations.md`.

The automations routes are the per-project control-plane opt-in surface. `GET` returns the
registry (id, kind, label, `requires`, `implemented`), the project's `requested` table, and
the resolution (`enabled`, plus `blocked` → the dependencies each still needs) so a toggle
can show a dependency tree rather than a flat checkbox list. `PUT` replaces the table
through the ordinary project-config write: `409 revision_conflict` on a stale revision,
`409 automation_not_implemented` for a reserved id with no code behind it. The same table is
also carried by the typed portable Project options (`features/automation-enablement.md`).

The schedule routes are the scheduled-run surface (`features/scheduled-runs.md`).
A definition belongs to exactly one Project, because a spawn does; the unscoped `GET` exists because "what fires tonight" spans Projects and is what the Schedule tab's fleet toggle reads.
Writing one is always allowed and firing it is what permission gates, so every row carries `blocked` - a live answer (`project_missing`, `automation_disabled`, `install_disabled`), recomputed per request rather than stored, plus the recent `runs` behind it.
`PATCH` with only `enabled` is the pause switch and keeps the definition; any other body is a full replacement validated exactly like a create, with `409 revision_conflict` on a stale revision.
A rejected definition answers `400` with a machine `code` (`invalid_cron`, `invalid_interval`, `invalid_timezone`, `invalid_run_at`, `invalid_backend`, `invalid_follow_ups`, `invalid_action`, `invalid_target`, `invalid_overlap`) and a `fields` map, so the editor can mark the field rather than print a sentence.
`POST /schedules/preview` answers the next five fire times for an unsaved definition: cron plus a timezone plus daylight saving has one implementation, in the daemon, and the editor must not grow a second one that disagrees with it twice a year.
`POST .../run` is subject to every fire-time guard except lateness, so it cannot walk around the Project opt-in, the overlap policy, or the concurrency ceiling.

A definition whose `action` is `resume` carries `target_run_id` (a **history run id**, never a session id), `target_kind` (`run` | `latest_of_session` | `fork_point`), `target_cut_message_id` + `target_cut_mode` for a fork, and `context_ceiling_pct` for a rolling continuation.
It may not carry `backend`, `profile_id`, `cwd`, or `overlap: allow`: the conversation's row and its adapter already fix the harness, the argv and the directory, and a conversation opens once.
Each row's `target` is **resolved per request** for the same reason `blocked` is - a stored label would go on naming a conversation that has since been deleted - and reports `missing`, the conversation's display name and backend, and, for a rolling target, the run it has actually reached.
A `spawn` answers `target: null`.

`GET /history/{id}/branch-points` is the session listing (`GET /sessions/{id}/branch-points`) read from a History row, because the fixed point a nightly fork cuts at is chosen from a conversation whose pane usually ended long ago.
Its payload is identical except that `session_id` becomes `history_id`, and every "nothing to offer" case answers `200` with a `reason` rather than an error.

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
`DELETE .../backfill` stops a running job; every record it already wrote stays readable.
The ordinary timeline snapshot carries chunk progress and an honest `completed`, `completed_with_gaps`, `partial`, or `failed` result, and that result is stored rather than held in daemon memory, so it survives a restart.
The snapshot also carries `gates`, every quantitative cap that can stop a scan with its current usage, and `skip_reason`, the scanner's own words for why it is currently producing nothing.
`skip_reason` is null while a run is merely idle; it names a cap or a closed gate only when scanning is actually stopped.
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

`DELETE /projects/{project_id}/notes/{note_id} {revision}` retires one note rather than
erasing it: the file moves to `.swe-mux/notes/trash/` and the response carries
`{deleted, project_id, note_id, trashed_path}`.
A stale revision is `409 revision_conflict`, as with a write.
A Project's last note is refused with `409 note_protected` and is not moved; a Project always
keeps at least one note, which is a rule about the collection and not about the seeded
`project.md`.

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
GET    /prompts[?project_id=][&all_projects=1]
POST   /prompts
PUT    /prompts/{scope}/{template_id}
DELETE /prompts/{scope}/{template_id}
POST   /prompts/{scope}/{template_id}/use
PATCH  /prompts/{scope}/{template_id}/favorite
```

Scopes are `global | project`. Writes are revision checked; same-ID global/Project conflicts
are returned explicitly. Each item names its owning Project (`project_id`, `project_name`;
null for global) and the response lists the Projects it read a library for. `all_projects=1`
widens the read to every registered Project that admits Project templates - a management view
only, because the unwidened listing is what an Action layout pins from and must stay confined
to the focused Project. Template bodies are bounded inert UTF-8 text and terminal control
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
                                                     accept_agent_messages?,
                                                     accept_agent_interjections?}
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
Its view also carries a `spawn_requests` list and, since Phase 7.6, a `control_requests` list of drafted interrupt/end approvals awaiting a human, each sorted newest-first and bounded.
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
GET    /sessions/{id}/approvals
PUT    /sessions/{id}/approvals                 {mode: wait|allowlisted|allow_all, set_by?}
POST   /sessions/{id}/approvals/approve-once    {fingerprint?}
DELETE /sessions/{id}
POST   /sessions/{id}/input
GET    /sessions/{id}/branch-points[?limit=]
POST   /sessions/{id}/branch          {name?, target_session_id?, direction?, from_message_id?, mode?}
POST   /sessions/{id}/broadcast-set
POST   /broadcast/input
POST   /sessions/{id}/attachments   multipart `file`; X-Mux-User-Gesture: terminal-attachment
POST   /sessions/{id}/media         legacy image-only compatibility route
GET    /sessions/{id}/last-reply
GET    /sessions/{id}/transcript[?limit=]
GET    /sessions/{id}/skills[?refresh=1]
GET    /sessions/{id}/agent-environment[?refresh=1]
```

`GET /sessions/{id}/branch-points` lists where this conversation can be forked. Every "nothing to
offer" case answers `200` with a `reason` rather than an error status, because opening the picker
on a pane that has not spoken yet is an ordinary thing to do:

```
{session_id, backend, conversation_id, strategy, from_message, truncated,
 reason: null|not_agent|no_transcript|unreadable|dialect_unsupported|strategy_has_no_points,
 points: [{message_id, ordinal, role, ts, text, default_mode,
           modes: {before: {eligible, reason}, after: {eligible, reason}}}]}
```

`default_mode` follows the role, because the two cuts are opposite acts on opposite kinds of
message: a prompt is a thing to redo (`before`), an agent reply a thing to continue from
(`after`). Eligibility is per point, not per harness — the same conversation offers a legal cut
after one reply and an `unanswered_tool_calls` refusal after the next. `truncated` bounds only
which cuts can be *named*: a fork always carries the conversation from its first byte.

`POST /sessions/{id}/branch` forks the conversation and returns
`201 {session, source, strategy, fork, seed_text}` with the new pane already attached to the
Project layout. `from_message_id`/`mode` are accepted only by a `transcript_fork` harness and
default to the newest point; `name` overrides the derived `B<n>-<source subject>`
(`sessions.md`); `fork` reports `{conversation_id, from_message_id, mode, cut_offset,
records_written, records_dropped, attachments_copied, bytes_written}`; `seed_text` is the prompt a
`before` cut excluded, for the client to place in the new pane's composer. It emits
`session_branched` carrying `original`, `branch_id`, `sibling_id`, `strategy`, `from_message_id`,
`mode`, `records_written`, `attempts`, and `duration_ms`, and records a `branch` lineage edge
carrying the cut and a bounded excerpt of the message it was made at. The
refusals are all distinguishable, and none of them leaves a half-made pane behind:

| Code | Status | Meaning |
| --- | --- | --- |
| `not_agent` | 422 | The backend has no observable transcript |
| `branch_unsupported` | 422 | The harness declares no `branch_strategy` |
| `branch_point_unsupported` | 422 | A point was named for a harness that can only fork from now |
| `bad_mode` | 422 | `mode` was neither `before` nor `after` |
| `source_busy` | 409 | `resume_child_thread` only: the pane is mid-turn or holding an approval dialog |
| `source_not_live` | 409 | `resume_child_thread` only: the pane has ended |
| `source_composer_dirty` | 409 | `resume_child_thread` only: unsent composer text would swallow the command |
| `native_id_missing` | 409 | No conversation id to fork from yet |
| `no_transcript` / `unreadable` / `dialect_unsupported` / `no_messages` | 409 | Nothing forkable to read |
| `branch_point_unknown` | 409 | The named message is not in the conversation's current window |
| `unanswered_tool_calls` | 409 | A cut there would leave a tool call unanswered, and the provider rejects such a conversation |
| `outside_window` | 409 | Nothing precedes that message to cut after |
| `empty_prefix` / `source_too_large` / `fork_id_taken` / `source_unreadable` | 409 | The writer refused the source (`transcript_fork.ForkRefused`) |
| `fork_write_failed` | 500 | The fork could not be written |
| `branch_sibling_failed` | 503 | The branch exists but its pane would not stay up; carries `conversation_id` so it can be reopened from History |

`GET /lineage[?run_id=]` returns the edges touching a run, each decorated with its two
endpoints: `{parent, child}` where an endpoint is `{name, live, known, session_id?}`. Naming is
the daemon's because an endpoint is a *run* in one of three states a client cannot see - a live
session, an ended History row, or a deleted one. `known: false` is reported rather than the edge
being dropped, because the edge still records that the fork happened.

`POST /sessions/{id}/title/regenerate` accepts no body and returns `202 {ok:true}` after emitting
an asynchronous `title_regenerate_requested` event. It is limited to live auto-named Claude/Codex
runs; ended, shell, and manually named sessions are rejected. Provider and budget failures remain
visible through automation diagnostics and never block the agent lifecycle.

`POST /sessions/{id}/read` returns `{id, turn_seq, read_turn_seq, read_at, unread_pin}` and takes
one of three bodies, because its two writers - the dwell timer and the user - must not be able to
impersonate each other:
- `{turn_seq?: number}` (the session's current `turn_seq` when omitted, and an empty body is the
  same thing) is the **implicit** acknowledgement written on a dwell timer whenever a human is
  looking at a pane. Monotone and clamped to the counter the daemon has actually reached - a
  device that is behind cannot un-read what another cleared, and no client can acknowledge a turn
  that has not happened and silently swallow the next real one. Refused outright while
  `unread_pin` is set.
- `{read: true}` is an **explicit** read: it clears `unread_pin` and acknowledges every counted
  turn. Written by the menu item, and also by a client whose user has returned to a pane they
  marked unread - see below.
- `{read: false}` is an **explicit** unread: it sets `unread_pin` and rolls `read_turn_seq` back
  to just before the latest counted turn. This is the only write in the system that moves the mark
  backwards, and the pin is what keeps the dwell timer from undoing it on a pane that is still on
  screen. The daemon retires the pin when the session next completes a turn.

The pin's other end is **the client's** call, because only a client knows which panes are on
screen. It holds the pin for the visit it was set in and releases it once that session goes off
screen; the next dwell on a released pin is written as `{read: true}` rather than as a cursor
(`sessionAttention.ts`, `trackPinVisits`). The daemon's refusal of the implicit shape is therefore
narrower than it looks: it stops the marking visit's own timer, not every timer thereafter.

The mark is acknowledged for the **user**, not for one browser: it lives on the session record, so
it follows every device and survives a reload.
A no-op call emits nothing; a real one publishes a session update and a `session_read` event
(carrying `turn_seq` and `unread`) so other devices converge.
Separate from `PATCH /sessions/{id}` because the dwell-timer path must not carry that route's
history metadata write.

`POST /sessions/{id}/standing-activity/clear` takes an optional
`{kind?: 'loop'|'cron'|'background_tasks'|'subagents'}` (the whole set when omitted or when the
body is absent) and returns `{ok, cleared, standing_activity}`. It **retracts only**: annotations
are not states, so this cannot move `state`, `awaiting_reason`, or `delivery_state`, and it cannot
assert activity. An unknown `kind` is rejected. Every clear is ledgered with evidence `manual` and
drops the run-scoped launch bookkeeping, so a later duplicate completion cannot decrement a fresh
annotation (`design/features/status-detection.md`).

`GET`/`PUT /sessions/{id}/approvals` read and set what mux answers on this conversation's behalf
(`design/features/approvals.md`). Both return the same body:

```ts
interface ApprovalStatus {
  supported: boolean            // this harness can answer a permission request through a hook
  enabled: boolean              // the install master switch
  ceiling: ApprovalMode         // the strongest mode this Project permits
  rules: string[]               // what `allowlisted` would resolve against right now
  rules_source: 'project' | 'default'
  unavailable: string | null    // why no mode above `wait` can be selected, phrased for display
  ttl_seconds: number
  max_auto: number
  policy: ApprovalPolicy        // the stored grant
  effective_mode: ApprovalMode  // what is actually in force
  modes: ApprovalMode[]
}
```

`policy.mode` and `effective_mode` are both present and answer different questions: an expired
grant, or one made against a conversation since replaced, reports its stored mode *and* an
effective `wait`, because "it lapsed" reads differently from "it was refused".

`PUT` refuses with a named code rather than silently downgrading: `invalid_mode` (400),
`approvals_unavailable` (409, the install switch, an unsupported harness, no live conversation,
or a Project ceiling of `wait`), `above_ceiling` (409), and `empty_allowlist` (409). Setting
`wait` is never refused — taking authority back must not depend on the install switch, the
Project ceiling, or the conversation still being the one the grant was made against.

`POST /sessions/{id}/approvals/approve-once` answers the approval the session is showing, once.
It is not a mode. It re-checks the agent run, this session's own screen still classifying as an
approval, and the supplied `fingerprint` when present, then writes one `\r`; it refuses with
`no_approval` (409) or `fingerprint_changed` (409). The browser routes through the daemon rather
than writing Enter itself because only the server can make those checks.

```ts
interface SpawnRequest {
  project_id: string
  backend?: 'shell' | 'claude' | 'codex'
  name?: string
  profile_id?: string               // a launch profile whose own backend must match
  executable?: string
  argv?: string[]
  resume_native_id?: string
  cwd?: string                      // must resolve inside the owning Project root
  env?: Record<string, string>      // ≤ 64 entries; scalar values stringified
  completion_mode?: 'interactive' | 'one_shot'
  seed_text?: string                // agent backends only; ≤ 500k chars; the agent RUNS it
  stage_text?: string               // agent backends only; ≤ 500k chars; parked unsent
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
(`features/git.md`). `env` merges over the launch profile's
environment and under mux's own identity variables, so a spawned shell can never present
another session's hook credentials.

`profile_id` is accepted for every backend, not only `shell`. The named profile must declare
the backend the request asked for, and it may not set argv the adapter builds for itself; both
refusals live in `resolve_agent_profile` rather than in this contract, because parsing cannot
see the profile (`features/launch-profiles.md`). With no `profile_id`, an agent spawn applies
the Project's default for that harness when it has a usable one. Both exist because a Project Action step declares its own
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
Because the seed rides argv, the agent runs it: `seed_text` submits by construction and can
never leave text waiting for review.

`stage_text` is the stage-without-send counterpart, mutually exclusive with `seed_text`.
The daemon spawns the session, waits up to 15 s for it to read `idle` (a fresh Claude
composer arrives in ~1 s), then writes a bracketed paste with no carriage return through the
operator-input path, so composer shadowing and delivery-readiness accounting see the parked
text as partial input.
Nothing is submitted; the operator reviews and presses Enter.
A session that never reads ready still gets the paste — the PTY buffers input written before
the CLI listens — and the `spawn_text_staged` event records `ready: false` for that case.
The spawn response returns after staging completes, and a session that dies before staging
fails the request rather than reporting a spawn that silently lost its text.

`POST /sessions/{id}/input` is the only raw-input path that reaches a session whose pane is
not mounted in the caller's browser; the browser uses it for composer fill (insert without
submit). A multi-line body must arrive wrapped in bracketed paste (`ESC[200~` … `ESC[201~`,
newlines as CR) or the agent composer submits at every line. Actual message *delivery*
(paste + submit) to a live agent goes through the prompt queue's `POST /queue/send-next`,
which performs both writes daemon-side with the same evidence accounting.

Two shared typed daemon operations added in Phase 7.6 stop or end a running agent, and the browser, CLI, and MCP `interrupt`/`end_session` tools all call them so no path skips the accounting.
The interrupt operation writes the harness interrupt byte through the same operator-input helper (correct `input_revision`/`terminal_input` bookkeeping), leaving the session alive.
The graceful-end operation interrupts the turn, sends the harness's own exit sequence (the adapter `graceful_exit_keys()`, carried on the PTY as `graceful_exit`), waits `session_control_graceful_timeout_s` for the CLI to tear itself down, and falls back to the existing hard stop only on timeout.
It stamps `SessionRecord.requested_end_reason` first, so an agent-initiated end records `agent_ended` even when the CLI exits on its own (`features/sessions.md`, `features/mux-mcp.md`).

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
Rows carry `turn_started_at`, monotonic `turn_epoch`, and nullable `active_turn_id` for the open root-turn generation.
Rows also carry nullable `running_work_since`, the start of the current stretch of running work.
It is the only timestamp that dates a request whose harness has handed off to background agents:
that hand-off is a real turn end, so `turn_started_at` is absent and `last_turn_ms` describes only
the turn that dispatched the work. A client rendering "how long has this been going" must prefer
it over both while `standing_activity` holds a `subagents` or `background_tasks` entry.
Rows also carry nullable `interrupt_pending_at` and `interrupt_pending_source`; these expose exact operator interrupt intent without claiming completion or changing delivery readiness.
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
On initial attach, the daemon waits up to 250 ms for
`{type:"attach_ready", cols, rows, renderer, hidden, output_flow_control, since?}`, then sends `state` followed by the replay.
The handshake precedes the replay snapshot because the frame decides what the replay is: `since` is the ring position the client has already parsed up to, and when the ring provably still holds everything after it the attach is answered as a **delta** (`replay_start` with `reason:"delta"`, exactly the missed bytes, into a terminal the client does not reset) instead of a reset plus the bounded window.
That is what keeps a pane's parsed scrollback across a reconnect — a tab switch, a minimize, a phone freeze, or a session-preserving daemon restart (ring positions survive supervisor adoption).
Every doubt — no `since`, a gap the ring no longer covers, a gap larger than `attach_replay_bytes`, a position outside the stream — falls back to the full windowed replay, because a wrong delta corrupts a terminal silently while a wasted replay only costs a parse.
A **cold session never serves a delta at all**: its ring was rebuilt from disk, so its positions describe a different stream from the one any client parsed before the crash, and a `since` from that stream can land inside range by coincidence (`features/session-recovery.md`).
`replay_end` carries `position`, the ring position at the snapshot: that anchor plus every live binary byte the client receives afterwards is what it may offer as `since` next time.
A `gap` frame (dropped chunks) invalidates the client's count until the resync's `replay_end` re-anchors it.
A delta never allows terminal responses and never triggers the truncated-replay repaint pulse — the client's screen is exact after the append.
It applies the frame's dimensions before
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

Input to a session that has **ended** — including a cold one — is refused separately and before
ownership is considered: `{type:"input_refused", reason:"session_ended"}` on the socket, and a 400
on the HTTP paths. An ended pane stays open until it is dismissed, so it is a pane a person can
click into; without this, `PtyHost.write` raises for its released or never-spawned pseudoterminal
and the operator gets a 500 or a dropped socket instead of an explanation
(`features/session-recovery.md`).

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
It reports `running_work_since` beside `turn_started_at` and `last_turn_ms` for the same
reason the transcript timestamps travel together: the trio is the diagnosis. An anchor far
older than `last_turn_ms` on a session with no open turn is a harness that handed off to
background agents, which is exactly when the turn alone stops answering "how long has this
been going" — and it is the shape a row reading `idle` while `cli_state` reads `busy` and
`pty_tail` reads `working` will have.
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
Transcript tab: `{messages:[{ordinal,role,ts,text,preceding_tool_calls,preceding_tools:[{id,name,input}],abandoned?}], trailing_tool_calls:[{id,name,input}], hidden, abandoned_messages, truncated, observation_stale_since, reason}`.
Tool calls stay outside conversational prose but carry their native names and input arguments for the default-off disclosure.
Tool results and operational telemetry are never included.
An agent's turn is merged into one message per *segment*, a segment being a run of records with no tool call
between them (`transcript_view.conversation_view`, see `ui.md`); `hidden` counts what was withheld
and `preceding_tool_calls` counts the tool calls between a message and the one before it, so
neither the filtering nor the gap is ever invisible.
`abandoned` marks a message on a branch the conversation left and `abandoned_messages` counts them
in the returned window (`features/transcript-branches.md`).
This is the one route that returns them at all: they are marked rather than dropped because a
reader is entitled to see that their conversation branched, and folded rather than inlined
because they are not what it says.
A branch boundary never merges into an adjacent segment, and an abandoned turn's tool calls are
never counted as `preceding_tool_calls` for the live message that follows it.
`trailing_tool_calls` preserves calls made after the newest prose message without inventing another message.
`ordinal` numbers the returned window rather
than the conversation, making it a display key and not an identity. Deliberately **not**
`/history/{id}/transcript`, which reindexes the run's searchable messages and loads its
annotations on every call: right for opening an entry once, wrong for a surface that refreshes on
every turn. This route only reads. Bounded twice — newest `limit` messages (default 200, max
1000) and a 64 MB tail — with `truncated` set by either; the cap exists because Codex rollouts
reach hundreds of MB. Nothing to show is a `200` with a `reason`
(`not_agent`/`no_transcript`/`unreadable`), because a shell pane and an agent that has not spoken
yet are ordinary states of a passive view, not failures. No redaction: unlike the MCP surface,
the reader is the machine's owner and needs the literal text to copy.

`GET /history/{id}/transcript` returns the same tool-use block subset inside native message content: `{type:"tool_use",id,name,input}`.
Pure tool-call records remain reviewable even when they contain no prose.
Tool results and other output-bearing blocks are removed before the response and are not persisted in the History message index.
Its `messages` are the indexing projection, so turns on an abandoned branch are absent rather than
marked; `abandoned_messages` reports how many, so a retried run does not read as one with pieces
cut out (`features/transcript-branches.md`).

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
GET  /api/remote/firewall
POST /api/remote/firewall/repair   X-Mux-User-Gesture: firewall-repair
GET  /api/wsl/bridge                                   ?probe=1
POST /api/wsl/bridge/install              X-Mux-User-Gesture: wsl-bridge-install
POST /api/wsl/bridge/firewall/repair      X-Mux-User-Gesture: wsl-firewall-repair
```

`wsl/bridge` is the setup surface for the WSL agent bridge, and it answers **without**
`wsl_bridge_enabled` being on. That is the point rather than a convenience: a user cannot be
asked to turn something on before anything will tell them whether it would work, and the
first version of this diagnostic stayed silent until after the decision it existed to inform.
It reports `supported`, `enabled`, the WSL adapter address and subnet, the firewall rule name,
`restart_required` (the flag changes which sockets the daemon binds, and that only happens at
startup), and one entry per distribution with whether it is running.

`?probe=1` additionally inspects each distribution and attaches its `bridge` status. Off by
default because inspecting a distribution *starts* it, which takes seconds - so it is a button
in Settings rather than something opening the page does.

Both mutating calls require their own gesture header, for the same reason the firewall repair
does: one writes into the user's distribution and the other raises a UAC prompt, and neither
may be reachable by a background poll. The WSL firewall rule is separate from the tailnet one
by design - the scopes differ (the WSL virtual subnet versus `100.64.0.0/10`), and enabling
the bridge is not consent to phone access, or the reverse. It refuses with `no_wsl_adapter`
rather than guessing a scope when the adapter cannot be read, because an invented scope would
silently widen the rule beyond what the user agreed to.

The mobile-voice request is accepted only while the Tailscale listener is enabled and only from
the explicit Talk/Settings action. It returns a secure URL only when the daemon has a verified
secure endpoint; otherwise it returns `error` without changing the working direct HTTP listener.

`remote/status` adds the Tailscale connection state (`connection_state`, `device_name`,
`connection_command`, `connection_detail`) read from `tailscale status --json`, distinct from
`available` (CLI on PATH).
`remote/firewall` reports whether Windows Defender Firewall admits phone connections on the
Private profile (`supported`, `needs_repair`, `blocking_rule_detected`, `rule_allowed`,
`private_firewall_enabled`, `network_category`, `detail`); it reports `supported: false` off a
frozen Windows build.
`remote/firewall/repair` requires the explicit-action header, runs an elevated PowerShell repair,
and returns `{ok, reason}` where `reason` is `cancelled` (UAC declined), `unsupported`, or
`failed`.
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
POST   /voice/speak                      {text, stream_id?: UUID, continue_stream?, final?}
GET    /voice/stt-latency
POST   /voice/stt-latency                one browser-measured stage sample
DELETE /voice/stt-latency
POST   /voice/barge-in-diagnostic        bounded confirmed/rejected playback probe
POST   /voice/capture-diagnostic         bounded stalled/recovered capture watchdog report
POST   /voice/deferral-diagnostic        one resolved unfinished-utterance deferral
GET    /voice/clips[?session=&run=&limit=]
GET    /voice/clips/{clip_id}/audio
DELETE /voice/clips/{clip_id}
GET    /voice/models/kokoro               pinned-download state
POST   /voice/models/kokoro/download      202; progress rides voice_model_progress events
```

The Mux assistant (`design/features/assistant.md`) adds its own surface:

```text
GET    /assistant                          enabled, model, budget, spend, trust, diagnostic
GET    /assistant/dialogs[?limit=]
POST   /assistant/dialogs                  {title?}
GET    /assistant/dialogs/{id}             messages, actions, turn_running
POST   /assistant/dialogs/{id}/turns       {text, client_context?} -> 202 {turn_id, queued}
POST   /assistant/dialogs/{id}/interrupt
POST   /assistant/actions/{id}/confirm
POST   /assistant/actions/{id}/cancel
POST   /assistant/actions/{id}/ui-result   {ok, detail?, candidates?} from the executing device
POST   /assistant/actions/{id}/announced   a device began speaking this card aloud
```

Turn progress arrives over `/events` as `assistant_turn_queued` (only when a turn is already
running), `assistant_turn_started`, `assistant_sentence`
(dual-form `display`/`speech`), `assistant_tool_status`, `assistant_action` (the typed
pending/scheduled/executed confirmation state), `assistant_turn_done`, and
`assistant_turn_failed`, each carrying `dialog_id` and `turn_id`.

`assistant_notice` is the one assistant event belonging to **no turn**:
`{dialog_id, message_id, display, speech}`, published when the daemon has something to say
after the turn that started it ended. Today that is a Project Action's outcome, which arrives
when its step sessions finish - minutes later, with nobody's turn open to carry it.
It is stored as an ordinary assistant message, so it survives a reload and rides the next
turn's context, and a client renders it complete rather than streaming.
Spoken, it takes the announcement path (join the live stream, never take the floor), because an
outcome landing mid-sentence must not cut that sentence off.

Text posted while a turn is running is **queued, never refused**: the response carries
`queued: true`, `assistant_turn_queued` announces it under the id it will run as, and
consecutive arrivals merge into that one waiting turn (`merged: true`) rather than becoming
several - a sentence finished in two breaths is one request.
The queued and started events share one message id per turn, so a client renders the operator's
words once and updates them in place.
Refusing instead left the client holding text with nowhere to put it, which is how speaking over
the assistant lost what was said and how one sentence ended up split across two dialogs.

`assistant_turn_done` carries `exhausted`, true when the turn stopped on
`MAX_MODEL_CALLS_PER_TURN` with work still outstanding. The reply then ends with a plain notice
saying so, and that notice is spoken even when `speech_suppressed` would otherwise silence the
turn: a half-finished turn is the one thing the operator must hear about.

Sentences are the speech contract, not a preview of it.
Each `assistant_sentence` is released as the model writes it, so a device speaks the reply while the model is still generating; `assistant_turn_done` carries the same text for the record but is not a second thing to say.
Both carry `speech_suppressed`, set for what the model emits after a turn opened a confirmation card *and did nothing else*: the card's own `announcement` is then the spoken statement, and the model's paraphrase of it would be the same sentence twice.
A turn that also executed something, or opened more than one card, keeps its voice - "I opened two of the three and one needs your confirmation" is information no card carries.
`assistant_turn_done` also carries `sentence_count`, and its `speech` is only what still needs saying - empty when the card covers it.

`assistant_action` carries `announcement`, the daemon-built spoken line for a `pending` or `scheduled` card.
It omits the text preview the written `restatement` keeps, because synthesis time tracks characters and the operator can read the preview on the card.
Its wording is the trust policy talking: a `scheduled` card can only be stopped, a `pending` one only runs if the operator agrees, and a client must not reconstruct that distinction itself.

`/assistant/actions/{id}/announced` restarts a `scheduled` card's cancel window from the moment a device begins reading it aloud, so the window measures reaction time rather than synthesis time.
**It moves the deadline once per action and never again**, because extending re-emits the card and a device announces a card when it sees one - a second extension would close that cycle into a loop (it did, on 2026-08-20).
A repeat call is a logged no-op returning `extended: false`; so is a call for anything not currently scheduled.
The deadline only ever moves forward, is clamped to `CANCEL_WINDOW_MAX_SECONDS` from the action's creation, and a client that never calls this keeps the original window.

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

`/voice/capture-diagnostic` records the client frame watchdog's stalled/recovered reports:
event, detector, silent milliseconds, `AudioContext` state, track state and muted flag, and
the recovery-attempt count, each clamped on arrival.
A stall logs at WARNING because it is the durable evidence a dead phone microphone leaves at
the moment it dies — the failure this exists for was reconstructed from the access log hours
later while the UI said "listening" throughout.

`/voice/deferral-diagnostic` records one resolved unfinished-utterance deferral:
`{outcome, kind, trigger, words, heldMs}`, where `outcome` is `merged`, `submitted`, `held`, or
`discarded` and `kind` is `conjunction`, `preposition`, or `article`.
The trigger token is required and is narrowed to alphanumerics, spaces, apostrophes, and hyphens
before it reaches a log line; the counts are clamped.
It is posted on resolution rather than at the deferral, because the outcome is what judges the
heuristic: `merged` caught a real trail-off while `submitted` cost the operator one patience
extension for nothing, so the ratio of the two is the false-positive rate the word lists are
tuned against (`design/features/voice.md`).

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

It has three shapes, because the assistant produces its reply over several seconds and the operator should not wait for the last sentence to hear the first:

- Default (`continue_stream` absent): opens a stream and synthesizes its opening clip inline, returning it so a lost `voice_clip_ready` still has the HTTP response as a playback fallback.
- `continue_stream: true`: appends to an already-open stream. Synthesis runs on that stream's single worker and the response is an acknowledgement (`{stream_id, queued, segment_index, stream_open}`), not a clip - the browser is already playing the stream and picks the continuation up from its events. Appending to an unknown or completed stream is a `409`.
- Empty `text` with `final: true` and a `stream_id`: closes the stream with nothing more to say, which is what a turn that ended on a tool result needs.

`final: false` leaves the stream open.
Its segments carry `segment_count: 0`, meaning unknown, until the closing one carries the real total; a client must treat a non-positive count as "still open" rather than as the last segment.
Ordering is the invariant: one worker drains one FIFO per stream, so clip indices are monotonic however the appends arrive.
`voice_stream_closed` (`stream_id`, `segment_count`, `failed`) marks the end, including the case where a stream ends without a final clip - a failed segment ends its stream rather than speaking the rest out of order.

`/sessions/{id}/voice/generate` reads the latest assistant reply using the session/global effective content mode unless `content_mode` is supplied.
The request override is validated, applies to one clip only, and never mutates the session's persistent read-aloud preference.
The optional stream ID has the same claim-before-request role as `/voice/speak`.
Both it and `/voice/speak` refuse with `409 read aloud is off` while the `tts_enabled` master is off: the master gates generation everywhere, not only on the automatic `turn_ended` path.

Automatic, manual, and application-text synthesis returns the first coherent clip with `stream_id` and `segment_count`, then emits ordered `voice_clip_ready` events sharing `stream_id`, `segment_index`, and `segment_count`.
Agent replies of at most 420 characters stay in that one clip; longer replies prefer a complete opening sentence before continuing.
Application speech opens at a much tighter bound instead, because that opening clip is time-to-first-sound: synthesis is roughly linear in characters, and a 420-character opener measures 11-14 s of silence on Kokoro against about a second for a short one.
The wide bound stays for agent replies, where the coherence of somebody else's prose matters more than the first second.
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
GET /api/diagnostics/export
GET /api/diagnostics/prerequisites
GET /api/diagnostics/doctor
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
Each classified row contains `frames` and `bytes`; PTY kinds are `attach_replay`, `delta_replay`, `resync_replay`, and `live_output`.
These rows are a non-additive breakdown of already-counted sent WebSocket binary frames.
HTTP byte counts are encoded response and request bodies, excluding headers and transport
overhead.
WebSocket byte counts are application text/binary frame payloads before per-message compression.
The DELETE form records the prior totals in the rotating daemon log and resets the in-memory
window without disrupting live sessions.

`diagnostics/export` returns one aggregated bundle for a connection report: `config`
(sanitized `public_dict`, no secrets and no token), `remote_status`, `firewall`, `network_usage`,
`status_health`, `status_timeline_sink`, and `logs` with bounded tails of `daemon.log` and
`redeploy.log`.
It never includes terminal bytes or message content; the two logs are command-free by design.
`mux doctor --export` prints the same bundle from the CLI.

`diagnostics/prerequisites` reports the presence of Git, Node, npm, and Tailscale, each with `id`, `label`, `purpose`, `present`, `path`, `download_url`, and `install_command`.
It backs the onboarding checklist so a feature that needs an absent tool reads as unconfigured rather than broken.
Both forms are excluded from the counters so observing a window does not change it.

`diagnostics/doctor` is the consolidated read-only report behind `mux doctor` (no `--export`).
It is aggregation, not new capability: the handler gathers the payloads the diagnostics above already produce (health, remote status, firewall, prerequisites, status-health, background loops, the harness registry) plus the one class of fault nothing else exposes, the **observation-freshness** check, and the assembly is a pure function in `doctor.py`.
The response carries `version`, `generated_at`, `ok` (false when any check failed), a `summary` count of `ok`/`warn`/`fail`/`unavailable`, a machine-readable `capabilities` block (versions, platform, per-harness detection, remote and firewall availability), a flat `checks[]` list, and `observation_freshness[]`.
Each check is `{id, category, title, status, severity, detail, remedy}`; `status` is `ok`/`warn`/`fail`/`unavailable` and `severity` separates a `critical` fault (a lost supervisor, a dead background loop, a stale observation that blocks delivery, a needs-repair firewall rule) from an `optional` unavailable feature (a harness not installed, Tailscale logged out) and pure `info`.
Every failed check carries a concrete `remedy`.
Each `observation_freshness[]` row is one agent session whose observation the daemon can no longer trust - `{id, name, backend, reason, since, seconds_stale, diagnostic, delivery_blocking}` - drawn from the same `observation_stale_since`/`observation_stale_reason` fields the per-session state-log exposes (`features/status-detection.md`).
Like `export`, the report is built from already-sanitized sources and content-free rows, so it never includes a secret, terminal bytes, prompt or message content, or a credential.

## History and reviews

```text
GET    /history[?q=&scope=all|user|assistant|metadata&backend=&project=&state=&external=&time_basis=started|last_message&date_from=&date_to=&cursor=]
GET    /history/projects
GET    /history/{id}/transcript[?q=&scope=all|user|assistant|metadata]
GET    /history/backfills[?project_id=]
POST   /history/backfills              {project_id}
GET    /history/backfills/{job_id}
DELETE /history/backfills/{job_id}
GET    /history/scan
POST   /history/scan
DELETE /history/scan
GET    /history/duplicates
POST   /history/duplicates/repair     {dry_run?}
POST   /history/{id}/resume           {project_id, ...}
GET    /history/{id}/branch-points    cut points of an ended conversation, for a scheduled fork
DELETE /history/{id}
POST   /history/{id}/second-opinion   preview/confirm with project_id
GET    /history/{id}/handoff
```

Resume/review confirmation must target an existing Project and starts at its root.
Resume returns `409 conversation_live` (with the owning `session_id`) when a live session currently claims the row's native conversation; Branch, not resume, is the flow for forking a live conversation.
It returns `409 conversation_held` (with `holder: {kind, pid, job_id, name}`) when a CLI process mux does not own holds the conversation — a Claude background agent is the case that occurs — because that CLI answers a second opener by exiting rather than by refusing to start.
It returns `503 resume_failed` (with `attempts` and the pane's own cleaned last output in `detail`) when the resumed pane died inside its settle window; the pane is discarded rather than returned.
History rows and transcript entries carry `held_by: {kind, pid, job_id, name, detail}` while a live process holds their conversation, read fresh per request and never stored.
The resumed pane keeps the conversation's effective visible name (manual name, or generated title while auto-named) with no suffix, and it keeps the conversation's `agent_run_id` too: the resume continues one transcript, so it continues one history entry rather than opening a second over the same file.
A resume inherits that effective name before any new run is minted, so a generated title does not disappear with the old annotation key. A row the user renamed resumes under that name, never under its generated title.
Whether a resume continues the conversation is the adapter's rule: `codex resume` always does, `claude --resume` only at the conversation's recorded root. A Claude resume into a different root is a new conversation and gets its own entry plus a `resume` lineage edge.
`GET /history/duplicates` reports conversations still split across several rows. `POST /history/duplicates/repair` folds each back into its earliest row and defaults to `dry_run: true`, reporting the keeper, the rows it would remove, the values it would carry over, and any group skipped because a live pane is still writing to a duplicate. It never edits native transcripts and never touches a quarantined row.
Backfill jobs are daemon-local, cancellable, idempotent scans of complete shared native CLI history, scoped to one Project by cwd ownership.
`/history/scan` is the global, user-triggered counterpart of the startup reconcile, scoped to the enabled harnesses rather than to a Project.
`POST` starts the single scan (a second start while one runs is a no-op that returns the in-flight job), `GET` returns its `{status, phase, backends, scanned, processed, imported}` job for polling, and `DELETE` requests cancellation.
It exists because a first import can be expensive on a machine with a large history, so it runs in the background with progress and can be cancelled rather than blocking startup.
Handoff Markdown exposes the swe-mux history ID, provider-native session ID, and recorded native
transcript path; transcript bytes remain in the provider-owned file and are never copied into the
export. A missing or stale transcript pointer is reported explicitly.
When the run's Project opts into `timeline_handoff` (Phase 7.7), the Progress section is regenerated
phase-structured from the run's scan spine; otherwise it falls back to annotation summaries.
The `GET /history/{id}/transcript` payload also carries the run's `scan_records` alongside its
annotations, so the Run-notes view renders the behavioral spine (`features/scan-timeline.md`).
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
POST   /git/init                     {project_id}
POST   /git/worktrees
POST   /git/worktrees/session
DELETE /git/worktrees
GET    /land[?project_id=]
POST   /land                         {project_id, worktree_root}
DELETE /land/{request_id}
GET    /land/{request_id}/events
GET    /land/verify-command          ?project_id=&worktree_root=
POST   /land/verify-command/approve  {project_id, worktree_root, digest}
GET    /processes[?session=&include_ended=1&unique_memory=1&summary=1]
POST   /processes/action             {session_id, pid, identity_id, action}
GET    /previews[?session=]
POST   /previews                     {session_id, url, approved?, attach?, target_session_id?, direction?}
POST   /previews/{id}/capture         {viewport?, width?, height?, clip?}
DELETE /previews/{id}
```

`POST /git/init` creates a repository for a Project whose folder has none, writes a starter
`.gitignore` when the folder has no such file, stages nothing and commits nothing, and returns
`{ok, root, branch, gitignore: "created"|"existing", operation_id}` plus a `git_changed` event.
It re-resolves the folder's repository state inside the request: `404 project_not_found`,
`404 root_unavailable`, `409 already_initialized` for a folder Git already tracks, and
`400 git_error` carrying Git's own message. The reading that leads a client here is the
`404 not_git_repository` code from `GET /git/worktrees`. See `features/git.md`.

The `/land` routes are the land queue (`features/land-queue.md`). **None of them lands
anything**: `POST /land` enqueues a request and the daemon's own sweep is the only thing that
moves a trunk, so a client that gets `201` has been told the request was accepted, not that the
branch is on the trunk. `GET /land` returns the queue plus its bounds (`hourly_budget`,
`hold_timeout_seconds`, `retry_verification`); `409 already_queued` names an active request for
that branch, and `DELETE` refuses with `409 not_cancellable` once a step is in flight.

`GET /land/verify-command` reports the command a land would run, its digest, whether those exact
bytes are approved, and both the approved and current text so the prompt can show a diff.
`POST /land/verify-command/approve` requires the digest the caller was shown: `409
digest_mismatch` means the bytes moved between the prompt and the click and returns the new
digest, and `409 not_configured` means the repository declares no gate.

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
Content-derived counts for untracked files inspect only those first 200 rows and stop after 16 MiB across the summary.
Concurrent requests with the same Project root and comparison ref await one shared overview task rather than launching duplicate Git subprocess sets.

`GET /git/graph?project_id=ID&limit=N` returns `{lines, limit, has_more}` for all local refs.
`limit` is 1 to 200 with a default of 80.
Lines are either `{kind:"connector", graph}` or typed commit rows carrying `graph`, `oid`, `parents`, `refs`, `author`, `committed_at`, and `subject`.
Git supplies the graph prefixes and the browser renders them without reconstructing topology.

`GET /git/provenance?project_id=ID[&session_id=ID][&agent_run_id=ID][&commit=FULL_OID][&limit=N]` returns `{items, commits, ref_moves}` from the durable session-to-commit evidence ledger.
`project_id` is required and must name a registered Project, `limit` is 1 to 500 with a default of 200, and repeated `commit` parameters select multiple full 40-to-64-character object IDs.
Every item carries its durable id, session id and captured label, nullable agent run id, Project, exact worktree root, full commit OID, parent OIDs, copied subject and commit time, previous HEAD, relationship, confidence, ambiguity flag, role, match method, contributed paths, source, nullable source event sequence and tool-call id, and first/latest observation times.
Each item is additionally decorated on read with `display_name`, the session's current name under the rule every surface uses, resolved from the live session when the fleet holds it and from its History row otherwise, and with `history_id`, the conversation a reader can open.
The stored `session_name` is left untouched: it is evidence of what the session was called at capture time, while `display_name` is what it is called now.
`history_id` is absent when neither a live session nor a History row exists, so a caller can tell "no conversation to open" from one it could open.
Rows are newest-first by their first observation time.
`commits` rolls the same rows up per commit into `{commit_oid, subject, committed_at, worktree_root, committer, contributors[], attribution}`, so a reader gets who made a commit and whose work is in it without a second request.
`attribution` is `exact` when a committer was isolated, `correlated` when only contributions or occupancy are known, and `ambiguous` for a commit whose work mux never observed.
`items` stays one row per session per commit because that is what each piece of evidence is about; the set is assembled for the reader rather than denormalized into every row.
Retracted rows are absent from `items` and from the rollup: a withdrawn row is evidence the ledger no longer stands behind, and a reader asking who made a commit must not be handed one.
`ref_moves` carries `{id, project_id, worktree_root, commit_oid, previous_head, kind, commit_count, authored_count, subject, committed_at, observed_at}` for the checkout reference movements in scope, newest first.
It is deliberately **not** filtered by `session_id` or `agent_run_id`.
"What did this session do" and "what happened to this checkout" are different questions, and answering the first with the second is what used to put a merge nobody in the checkout had made onto every session's ledger.
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
A repository whose HEAD is unborn (freshly initialized, no commits) is refused before any Git mutation with `400 {code: repository_has_no_commits}` and a message naming the fix, unless an explicit `start_point` is given - Git resolves that ref without HEAD.
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
`DELETE /git/worktrees` takes `{cwd, path, force?}` and validates `path` against the exact current porcelain list.
Create and remove responses carry an `operation_id` that correlates daemon mutation logs.
Worktree mutations are daemon-owned, survive requesting-client cancellation, and have a 30-minute deadline distinct from four-second Git reads.
An exact prunable path with an existing directory and missing `.git` link is repaired and revalidated before removal; success and post-repair failures report `repaired`.
Revalidation checks the exact registration, `.git` link, and reported top-level even when `git worktree repair` returns nonzero, because Git may already have restored the requested path before reporting a different repair error.
After a nonzero remove, the daemon re-lists before declaring failure.
An absent exact registration is success; a surviving directory is atomically renamed below the same parent as `.swe-mux-orphans/<name>-<operation_id>` and returned as `cleanup: {status:"quarantined", path}`.
Failure to quarantine returns `409 worktree_cleanup_failed` with `removed:true` and the original remaining path.
Non-repairable prune states return `409 prunable_worktree`; repair failure returns `409 worktree_repair_failed`; mutation timeout returns `504 git_timeout`.
No removal path invokes repository-wide `git worktree prune`.

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

`GET /usage` returns one ccusage `collector` state and cache version 3 with a dynamic `sources` map.
Each source carries `source_id`, `source_label`, `collector_id`, daily, monthly, session, and model aggregates, totals, and provenance.
`POST /usage` runs the configured unified `ccusage daily --json --by-agent` collector once and atomically replaces the cache after complete validation.
The historical source list is not derived from the harness registry and may include tools swe-mux does not launch.
`DELETE /usage/cache` clears only historical ccusage data.
Quota provider and account telemetry is independent and remains under `/telemetry/*`.

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
The read tools are `list_sessions`, `get_session`, `read_transcript`, `search_history`, `memory_sources`, `read_memory`, `project_notes`, `read_project_note`, `project_actions`, `message_status`, `spawn_requests`, and the four Phase 7.5 cross-session memory reads `provenance`, `verified_status`, `prior_resolutions`, and `dead_ends`.
The write tools are `notify`, `request_spawn`, `run_action`, and the two Phase 7.6 session-control tools `interrupt` and `end_session`.
Each takes a `project` argument selecting the scope it answers within: omitted (or `self`) is the caller's own Project, `fleet` is every Project, and a Project name or id is that one.
`request_spawn` accepts a name but refuses `fleet`, because one request starts one session in one Project.
An unknown Project name is refused with the names that exist; a name matching two sessions is refused with their session ids rather than resolved.
Every result carries `project_scope`, and a default `list_sessions` also reports `live_sessions_in_other_projects` with a note naming the argument that would include them.
Session results expose the stable id, backend-generated `name`, and UI-equivalent `display_name`; an exact unique display name is accepted wherever a tool targets a session.
`list_sessions` filters by query and pages a combined live/ended result capped at 25 compact rows and 32 KiB per call.
`search_history` performs server-side message ranking over the Project history index and returns compact hits by default.
It supports literal hybrid/all-term/any-term/phrase/substring matching plus role, raw/generated title, backend, persisted state, exact run, session-start, and matching-message time filters.
Its lower date boundaries are inclusive, upper date boundaries are exclusive, default limit is eight hits, default hit-payload budget is 16 KiB, and cursors are bound to the normalized query.
Its response includes `search_index_ready`; `false` means a post-upgrade repair is using bounded literal filtering until both rebuildable FTS indexes reach their durable watermark.
`read_transcript(hit_id=...)` reads a bounded indexed neighborhood around one search hit, defaulting to one message before and two after.
The opaque hit is bound to the Project of the row that produced it, plus run, message ordinal, and transcript-index watermark; a changed transcript reports a stale hit instead of returning shifted text.
Reading a hit from another Project requires the same `project` argument the search used, so a hit id never widens the call that consumes it.
Without a hit, `read_transcript` pages from either end through an opaque cursor bound to one `agent_run_id`, labels every message with run id/sequence, and includes system/meta records only by explicit opt-in.
Ordinary reads default to 12 messages and 32 KiB of message text while preserving explicit expansion to 200 messages and 512 KiB.
An omitted session id or `self` addresses the caller; an explicit `agent_run_id` can select the current run or one of only that caller's superseded runs.
`get_session` includes the run's pinned title and opening request, exposes the caller's own superseded run ids, and also defaults to `self`.
Reads and writes default to the caller's own Project and reach another only through an explicit `project` argument; there is no mode, no config flag, and no implicit widening.
Claude's generated settings allow every declared read tool without a prompt, while every write tool remains permission-gated.
A session spawned before a read tool is added does not carry it in its allowlist, so a newly added read tool reaches only sessions spawned afterwards.
Tool annotations declare the same read/write split.
Successful MCP calls record content-free per-tool call, serialized-response-byte, and truncation counters in background diagnostics.
`notify` only stages a queue message with a visible sender/message/correlation envelope and `request_spawn` only creates an inert Fleet Queue approval row.
The four cross-session memory reads are deterministic queries over Tier 0 facts, git-provenance edges, the experience corpus, and the scan timeline; each is per-Project opt-in through the enablement DAG and returns `unsupported` (503) when the substrate is absent or `disabled` (409) when no Project in scope opted its automation in, never a fake empty.
The Phase 7.11 `scan_timeline` and `scan_search` reads expose the scan timeline to agents.
`scan_timeline` is session-scoped and gates on the **target session's** Project opting into `scan_reads`; `scan_search` gates on `semantic_history_search`, the opt-in that already gates the identical query on the human surface.
`detail:"digest"` is the bounded `catch_me_up` rollup, `detail:"records"` is the compact projection paged newest-first and cursored by an exclusive `since_t1`, and `detail:"full"` expands at most five explicitly named record ids.
The projection omits `evidence_refs`, `tier0_fact_ids`, `prompt_hash`, `prompt_version` and `observer_model` and collapses `target` to a count plus a few paths, while keeping `repaired_fields`, `messages_seen` and `window_truncated`, which are what let a reader calibrate a label; a record that withheld `approach_status` omits the key rather than rendering `unknown`.
Every result carries the enablement/liveness block (`scanning`, `last_scan_at`, `skip_reason`, `run_decided`), so a budget-stopped scanner is never readable as a quiet session, and an ended session is readable rather than refused.
Neither `POST .../scan-timeline/scan` nor the backfill route is reachable through MCP: a read costs nothing, a scan spends the human's gated budget.
The Phase 7.10 `doc_debt` read follows the same gate: it returns `{doc, changed_files}` pairs re-derived from each doc's "Key files" section over the Project's recently changed files, gated on the `doc_debt` detector's automation, and names the same blind spot the surface has — a source file no doc lists produces no debt, so empty is not proof the docs are current.
`interrupt` and `end_session` act on a running agent only under a per-Project grant (`off`/`draft`/`granted`, default `draft`); a `draft` grant writes an inert `control_request` a human decides in Fleet Queue, and `interrupt` is refused unless delivery-readiness is `safe`.
The full contract is `features/mux-mcp.md`.
An unknown token returns 401, non-loopback access returns 403, and rate overflow returns 429 with `Retry-After`.

## Other API groups

Configuration/keybindings, automation/annotations/lineage, events/notifications, voice,
remote status, filesystem discovery, and preview proxy routes retain their feature-specific
contracts described in the corresponding `features/` documents.
The keybinding policy separates `browser_reserved`, `desktop_only`, `application_reserved`,
and `terminal_reserved`; application-reserved UI scale chords are rejected as configurable bindings.

`GET /events[?after_seq=N][&session=<id>]` is the live event stream.
Every accepted connection first receives `{"type":"events_hello","ui_build_id":string|null,"daemon_generation":string}`.
`ui_build_id` is the SHA-256 identity embedded in the served production `index.html`, or `null` when the frontend has no valid identity, and `GET /api/health` exposes the same field.
Production clients compare it with the identity in their loaded document; a mismatch means a newer UI is available, while equal or absent identities do not request a reload.
`after_seq` is a resume
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
(config, keybindings, profiles, projects, automation status, provider
status, usage, and — when `cwd` is supplied — project config) into one response. `config`
failing fails the request; any other part degrades to `null` with its reason keyed under
`errors`. The individual endpoints remain authoritative and unchanged. The rules.toml text
is deliberately not a part: the Automation dashboard owns the rules editor and reads
`GET /api/automation/rules` itself, so Settings never holds a copy its Save could write back
stale.
The provider section supplies the cached structured-output model catalog used by the Automation
tab's live-filtered cheap and standard model pickers.

`GET /api/automation/dashboard` includes recent observer-call diagnostics without response
content: requested and resolved model, generation, provider, finish reason, HTTP status,
retryability, token and cost usage, latency, and response content type and length.

It also carries `spend_breakdown`: `{days, today, start_day, totals, rules[]}`, where each rule
row has `rule_id`, `calls`, `tokens`, `input_tokens`, `cached_tokens`, `cost_usd`, the same five
figures scoped to today, the requested models, and `last_at`.
`cached_tokens` is the prompt-cache hit and is a *subset* of `input_tokens`, never added to it;
`input_tokens` rides along because it is the only honest denominator for a hit rate, `tokens`
being input plus output and output never cacheable.
`GET /api/assistant` and every other reader of the ledger's `spend()` helper carry the same two
figures beside `tokens` and `cost_usd`. The daemon labels each row with `label`, `detail`, `kind`
(`observer` | `custom` | `feature` | `retired`), `enabled`, and `setting_label`, because several
features bill the observer budget without being automation rules and a raw `rule_id` names
neither them nor the setting that governs them.
`enabled` is read from the governing switch in every case - the live engine for a rule, the
named `Config` flag for a feature - because the column's whole job is to separate a live bill
from spent history.
`kind: retired` is the fallback for an id nothing on the page can turn off, so a *live* spender
missing from `FEATURE_SPENDERS` is reported as the opposite of what it is; the guard against
that is `tests/test_spend_label_matrix.py` rather than review. The rows are grouped from
`automation_budget_ledger` — the same table `spend_today` sums — so they reconcile with it
exactly, including the rows a call that failed after the provider billed for its input writes.

`GET /api/automation/projects` is the read-only fleet aggregation of per-Project automation
enablement: the registry (id, kind, label, `requires`, `implemented`) once, plus one row per
registered Project in sidebar order — `project_id`, `project_name`, config `status`, the
`requested` table, the resolved `enabled` list, `blocked` (id → missing dependencies), and
`scan_timeline_auto_enable`. Projects that opted into nothing are listed rather than omitted.
Drawn by the Automation dashboard's `projects` view; it has no write half — the
revision-checked `PUT /api/projects/{project_id}/automations` stays the only editor.

`POST /api/grants` is the one write behind every gate notice in the app: the way a surface
that cannot work turns on the thing it needs without sending the reader to an overlay
(`features/setting-links.md`). It takes `install` (a table of install switches), `project_id`
plus `automations` (ids, whose dependency closure the daemon computes) and/or `values` (typed
Project fields), and an optional `revision`. It is **additive only** — a `false`, a `draft`,
or an `off` is refused with `grant_is_additive`, so no surface but the owning editor can take
a permission away, which is what lets many surfaces grant while one owns each switch. Keys
outside `GRANTABLE_INSTALL_KEYS` / `GRANTABLE_PROJECT_VALUES` are refused with `not_grantable`;
both sets are validated against `Config` and `PROJECT_CONFIG_FIELDS` at import. The whole
request is validated before the first write and a refusal writes nothing; the Project write
goes first because it is the one that can fail. Success returns `applied` (per scope),
`spends`, the public config, and the Project's resolved automation state, and emits one
`grant_applied` audit event listing every scope-qualified key. `GET /api/grants` returns the
same allowlists plus the registry and `recommended_project_automations`, and is the contract
`frontend/test/grants.test.ts` holds the browser's catalogue against.

`GET /api/annotations` is the human Findings read over the deterministic consumers' output (Phase 7.10).
It filters by `tag`, `project_id`, `agent_run_id`, `session_id`, and `since` (epoch seconds), and caps at `limit` (default 200, max 1000).
`session_id` is resolved to the session's run-id set — its live run plus its superseded runs from history — and matched against `agent_run_id`, because the annotation's own `session_id` column is populated by one detector alone; a Project-anchored finding with a null run (doc-debt, provenance) is therefore absent from a session scope by construction.
The response carries `items` and `tag_counts`, the per-tag totals in the current scope (project/session/since honoured, the tag chip ignored) so a quiet scope reads apart from one buried under provenance edges.
The dashboard payload's `recent_annotations` key is unchanged; this endpoint is the filtered surface the Findings pane points at.

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
over), `suppressed`, `fanout`, and `resumption_lag`. Since Phase 7.7 it also carries
`scan_records` - the behavioral spine written since the absence began, attributed by run - because
the retired turn summarizer no longer feeds it.

Ranking emits `attention_item_ranked` and `attention_breakpoint`; a shell pane whose command
finished emits `shell_command_finished` with its exit status. Loop health and narration
counters are under `attention_ranking` and `attention_narration` in
`GET /api/diagnostics/background`.

## CLI

```text
mux ls [--project ID] [--state STATE] [--backend BACKEND]
mux projects
mux profiles
mux harnesses
mux spawn --project ID [--backend BACKEND] [--name NAME] [--profile ID] [--exe PATH] [--arg VALUE]
mux resume HISTORY_ID --project ID
mux send SESSION TEXT | mux send --all-broadcast TEXT
mux kill SESSION
mux history
mux history-duplicates [report|repair]
mux accounts [list|verify|audit] [--limit N]
mux reload-daemon [--force]
mux doctor [--export]
```

`mux` is the scriptable control surface; the browser and mobile clients remain the interactive
interface and MCP serves structured reads to agents, so the CLI carries only the operations with
no substitute.
Every subcommand accepts `--json` (raw daemon payload) and `--url` (daemon base URL); without
`--json` the output is a human table.

`SESSION` is a stable session id, an exact session name, or a unique id prefix.
An ambiguous name or prefix lists the candidates and exits `5`; no match exits `6`.

Backend and harness choices come from the harness registry, not a hardcoded list; `mux harnesses`
prints the registry with per-harness detection (installed, resolved path, CLI version, and whether
that version is newer than the tested bound).

URL resolution is `--url`, then `MUX_URL`, then the daemon host/port from config, then the loopback
default; the CLI never accepts or prints a provider secret.

Exit codes: `0` success, `2` usage, `3` daemon unreachable, `4` daemon HTTP error, `5` ambiguous
name, `6` not found, `1` a `doctor` report with a failing check.

`mux doctor` prints the consolidated diagnostics report from `GET /api/diagnostics/doctor` (see
Delivery diagnostics); `mux doctor --export` prints the full `GET /api/diagnostics/export` bundle
as JSON.
