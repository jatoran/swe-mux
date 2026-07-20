# Interfaces

All JSON APIs are rooted at `/api`. PTY and event streams use `/pty/{session_id}` and
`/events` WebSockets.

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
type PaneLayout = {version: 6; root: PaneNode | null}
```

Every split branch terminates in a stack, including a one-tab pane. `note` leaf IDs encode
Project note, session note, Files, and individual file resources; `history` is the searchable
session archive. Versions 1–5 are migrated when read. Visible legacy resource docks become
ordinary adjacent panes, while hidden docks remain closed.

## Project resources

```text
GET|PUT /projects/{project_id}/note
GET      /projects/{project_id}/session-notes/{note_id}
POST     /projects/{project_id}/session-notes/{note_id}   initialize if absent
PUT      /projects/{project_id}/session-notes/{note_id}
GET     /projects/{project_id}/files?path=RELATIVE
GET     /projects/{project_id}/file?path=RELATIVE
PUT     /projects/{project_id}/file   {path, text, revision}
POST    /projects/{project_id}/reveal {path: RELATIVE}
POST    /projects/{project_id}/ignore {path: RELATIVE, scope: global|project}
PUT     /projects/{project_id}/watch  {watch_id?, paths: RELATIVE_DIRECTORY[]}
DELETE  /projects/{project_id}/watch/{watch_id}
GET|PUT /project/config               typed portable Project options
```

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
}
```

`project_id` is required. `cwd`, `worktree`, and unknown fields are rejected. The daemon
always passes the owning Project root to the session manager. Session PATCH rejects Project
ownership changes.

`GET /sessions` adds a compact, read-only `delivery_readiness` object with
`state: safe|blocked|unknown`, a reason, and `authorized: false`. It is not accepted on writes.
Each row also exposes its stable terminal `note_id` and whether its lazily created note file
currently exists.
PTY WebSocket owners may send `{type:"terminal_state", mode:"normal|alternate"}`. Input
frames label xterm device replies with `kind:"terminal_response"`; every other input frame,
including bracketed paste, advances the human-input boundary.
`GET /sessions/{id}/last-reply` returns the newest meaningful Claude/Codex assistant turn for
gesture-safe clipboard prefetch. Provider control acknowledgements are skipped; the route does
not type `/copy` into the PTY.

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
GET|POST /previews
DELETE /previews/{id}
```

Git scopes/worktrees are derived tooling APIs, not canonical Project/session ownership and
not first-class frontend navigation.

Process snapshots expose bounded observational states `active | exited | escaped |
suspected_orphan | stale | inaccessible`. Actions revalidate PID, creation-time identity,
and ownership immediately before signaling; no state triggers automatic termination.

## Provider accounts and usage

```text
GET    /provider-accounts
POST   /provider-accounts/refresh
POST   /provider-accounts/{provider}/capture
POST   /provider-accounts/{provider}/login
PATCH  /provider-accounts/{provider}/{account_id}
POST   /provider-accounts/{provider}/{account_id}/select
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
Quota fields are derived from the newest durable sample. The operational endpoint caps
`limit` to 1–1,000 per collection and returns quota samples/rollups/reset evidence,
probabilistic attributions, tool/skill aggregates, parser coverage, and explicit compactions.
Its interpretation is always `observational_correlation_only`.
Reset review is durable and audit-preserving: both resolutions remove the row from the active
alert summary without deleting evidence; `manual_usage` is valid only for Codex rows.

## Other API groups

Configuration/keybindings, automation/annotations/lineage, events/notifications, voice,
remote status, filesystem discovery, and preview proxy routes retain their feature-specific
contracts described in the corresponding `features/` documents.

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
