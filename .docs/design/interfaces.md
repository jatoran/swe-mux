# Interfaces

## Scope

- In: implemented HTTP and WebSocket contracts.
- Out: UI interaction design and backend transcript schemas.

## HTTP

All JSON routes use `/api`. muxd listens on localhost plus the detected Tailscale IPv4 by
default. Both use the same credential-free API; Tailscale policy controls remote access.
Tailscale Serve is an optional HTTPS proxy, not a requirement.

```text
GET    /health
GET    /remote/status
GET    /config
PATCH  /config
POST   /config/reset
GET    /keybindings
PUT    /keybindings[?validate=1]
GET    /hooks
GET    /hooks/status
PUT    /hooks[?validate=1]
GET    /automation
GET    /automation/rules
PUT    /automation/rules[?validate=1]
PATCH  /automation/rules/{rule-id}
POST   /automation/dry-run
GET    /automation/dashboard
GET    /automation/firings
GET    /annotations[?agent_run_id=&session_id=&tag=&limit=]
GET    /automation/provider
POST   /automation/provider/key
POST   /automation/provider/models/refresh
GET    /automation/notifications
PATCH  /automation/notifications/{notification-id}
GET    /automation/batches
POST   /automation/batches
GET    /automation/injection-safety
GET    /profiles
GET    /directories/pins
POST   /directories/pins
DELETE /directories/pins
GET    /fs/roots
GET    /fs/list?path=
GET    /project/config?cwd=
PUT    /project/config
GET    /project/notes?cwd=&kind=&id=
PUT    /project/notes
GET    /project/notes/search?cwd=&q=
GET    /space-notes
GET    /note-shelf
GET    /projects[?include_hidden=1&offset=&limit=]
POST   /projects/resolve
GET    /projects/{project_scope_id}
PATCH  /projects/{project_scope_id}
DELETE /projects/{project_scope_id}
GET    /artifacts[?project_scope_id=]
POST   /artifacts/{artifact_id}/transfer
GET    /sessions[?space=&state=&backend=]
POST   /sessions
GET    /sessions/{id-or-name}
PATCH  /sessions/{id-or-name}
DELETE /sessions/{id-or-name}
POST   /sessions/{id-or-name}/input
POST   /sessions/{id-or-name}/broadcast-set
POST   /broadcast/input
POST   /sessions/{id-or-name}/media
POST   /sessions/{id-or-name}/promote
POST   /sessions/{id-or-name}/demote
GET    /spaces
POST   /spaces
PATCH  /spaces/{id}
DELETE /spaces/{id}
GET    /history[?q=&backend=&project=&state=&space=&external=&from=&to=&cursor=]
GET    /history/projects
GET    /history/{id}/transcript
POST   /history/{id}/resume
POST   /history/{id}/second-opinion
GET    /history/{id}/handoff
DELETE /history/{id}
GET    /lineage[?run_id=]
POST   /lineage
GET    /attention/absence[?since=]
GET    /telemetry/workloads[?since=]
GET    /experiences[?q=&project_scope_id=&limit=]
GET    /events[?after_seq=&session=]
GET    /notifications
GET    /voice
POST   /sessions/{id-or-name}/voice/generate
GET    /voice/clips[?session=&run=&limit=]
GET    /voice/clips/{clip-id}/audio
DELETE /voice/clips/{clip-id}
GET    /usage
POST   /usage/refresh
DELETE /usage/cache
GET    /processes[?session=]
POST   /processes/action
GET    /previews[?session=&space=]
POST   /previews
DELETE /previews/{id}
POST   /hooks/{id}
GET    /git/worktrees[?cwd=]
POST   /git/worktrees
DELETE /git/worktrees
POST   /reveal
```

`POST /sessions/{id-or-name}/media` accepts a multipart `file` only for an open Claude
or Codex session. The browser marks the request with
`X-Mux-User-Gesture: terminal-image` (the legacy `clipboard-image` value remains accepted
for cached clients). PNG, JPEG, WebP, and GIF payloads are accepted up to 10 MiB, with at
most 32 staged files per session. A successful response returns `reference`, `path`,
`media_type`, and `bytes`; the terminal pastes `reference` as one isolated bracketed-paste
payload so the agent CLI can convert it into its native image attachment.

`/preview/{registration-id}/{path}` is the bounded preview bridge. Ordinary HTTP methods
proxy bodies/queries to the immutable registered literal-loopback origin; WebSocket
upgrades bridge text/binary/ping/pong frames and negotiated HMR subprotocols. Root-relative
HTML/CSS/module and runtime browser URLs are scoped to the registration. External redirects,
per-request upstream URLs, ended source sessions, oversized bodies/responses/messages, and
excess bridge concurrency fail closed.

`GET /voice` reports read-aloud status: enabled/engine availability, content mode, summary
model, today's summary spend versus the voice budget, and cache totals. Manual generation
returns the finished clip or a typed 409 diagnostic. Clip listings are newest-first and
never include daemon file paths; `/voice/clips/{id}/audio` streams the cached mp3/wav with
range support. `voice_clip_ready`/`voice_clip_failed` events carry the clip and trigger.

`/hooks/{id}` and `/sessions/{id}/promote|demote` are loopback-only integration surfaces
authenticated with the per-session `X-Mux-Hook-Secret`. Hook ingress additionally
requires a loopback peer, an allowlisted event type, and a bounded request body.

```ts
type SpawnSession = {
  backend?: "shell" | "claude" | "codex"
  name?: string
  cwd?: string
  space?: string
  profile_id?: string // shell-only; mutually exclusive with executable
  executable?: string // `exe` remains a compatibility alias
  argv?: string[] // `exe_args` remains a compatibility alias
  worktree?: Record<string, unknown>
}

Shell spawn precedence: raw executable → request profile → space default profile → global
default profile. Request argv appends to profile argv. Resolved session snapshots include
`shell_profile_id`, effective `exe`, and effective `args`.

Config reads return no hook secrets and include `tailnet_enabled` plus
`access_mode: "local+tailnet" | "loopback"` and a revision/ETag. Legacy token fields are
removed during migration. PATCH accepts
`If-Match` or `_revision`, returns field errors without mutation, and reports
`hot_applied` versus `restart_required`. Canonical TOML rewrites are atomic; migration
creates `config.toml.bak`. Invalid external edits retain the last-known-good runtime
configuration and emit `configuration_error`.

Mobile input fields are hot-applied: `mobile_vertical_drag`
(`smart|terminal|application|disabled`), `mobile_scroll_direction`
(`natural|wheel`), `mobile_scroll_sensitivity` (`0.25..4`), and `mobile_long_press`
(`context_menu|disabled`).

type SessionInput = { data: string }
type PatchSession = {
  name?: string; space?: string; pin?: boolean
  voice_mode?: "off" | "on_demand" | "auto" | null // null inherits the global TTS default
  voice_content?: "summary" | "verbatim" | null // null inherits the global content setting
}
type BroadcastSet = { include: boolean }
type LayoutLeaf = { type: "leaf"; kind: "terminal" | "preview"; id: string }
type LayoutSplit = {
  type: "split"
  id: string
  direction: "horizontal" | "vertical"
  ratio: number // inclusive 0.1..0.9
  first: LayoutNode
  second: LayoutNode
}
type LayoutStack = {
  type: "stack"
  id: string
  active_child_id: string
  children: Array<LayoutLeaf & { kind: "terminal" | "preview" }>
}
type LayoutNode = LayoutLeaf | LayoutSplit | LayoutStack
type NoteDock = {
  open_ids: string[] // ordered, unique note resource IDs; maximum 32
  active_id: string | null
  size: number // desktop width fraction, inclusive 0.2..0.7
}
type SpaceLayout = { version: 4; root: LayoutNode | null; note_dock: NoteDock }
type PatchSpace = {
  name?: string
  default_profile_id?: string | null
  default_cwd?: string | null
  layout?: SpaceLayout
  layout_revision?: number // required with layout; stale values reject the whole write
}
```

Live `Session` snapshots include `startup_timing_ms`, a non-persistent map of millisecond
measurements. `project_resolution`, `project_config`, `profile_resolution`, `pty_spawn`, and
`registration` are phase durations. `server_ready`, `first_output`, and optional
`first_prompt` are cumulative from daemon request handling start. Browser-only launch
milestones are reported once to `POST /sessions/{id}/startup-metrics` as an allowlisted,
bounded `timing_ms` map and exposed live as `client_startup_timing_ms`. The daemon persists
separate server- and browser-sourced startup events, while session snapshots remain volatile.

Layout validation limits trees to 64 leaves and depth 24, rejects duplicate resource
identities, and bounds the Notes Dock to 32 unique resources. It migrates persisted
version-1 `{version:1,panes:string[]}` and version-2/3 trees to v4. Legacy embedded note
leaves are extracted into `note_dock`; their former split branches collapse without changing
the remaining terminal/preview tree. Stack children are terminal or preview leaves, so a
session and the previews it spawned share one tab region; notes are never stack children
because they live in the space note workspace. Active selection uses a stable child ID.

Registering a preview with `attach` groups it as a tab in the region that already holds its
owning session rather than splitting a new region; a preview whose session has no terminal
in that layout falls back to the requested split `direction`.

Live session snapshots expose immutable `spawn_cwd`/`spawn_project_scope_id`, untrusted
display-only `runtime_cwd`/optional known `runtime_project_scope_id`, and active immutable
`agent_run_id`/`run_cwd`/`run_project_scope_id`. Compatibility `project_scope_id` means the
active run scope for an agent and spawn scope for a shell. History rows are agent-run owners,
not PTY owners. Space-note requests use stable space identity and route only to daemon app
data. Project and agent-run notes use their project-scope owner; caller cwd cannot override
an existing run-note owner. Plain shells have no durable session-note endpoint.

Note kind `projects` maps a known project scope to `.swe-mux/notes/project.md`; `sessions`
maps an agent-run/history ID to project-local storage; `spaces` maps a space ID to
`<data_dir>/notes/spaces/`. Runtime cwd is resolved only after an explicit current-project
action and never independently authorizes a write.

`GET /note-shelf` is a read-only, activity-ordered discovery index over saved app-owned
space notes, bound project/agent-run note artifacts, and safely scanned unlinked project
Markdown. It returns bounded excerpts and friendly ownership metadata, creates no files or
bindings, rejects symlink/filesystem escapes, and marks unowned recovery entries non-openable.

`DELETE /sessions/{id-or-name}` stops a live session before removing it from the volatile
session registry. For an already exited or crashed session it skips process shutdown and
removes the registry entry directly; durable history is not deleted.

Project list results are activity ordered and bounded to 500 items per request. Detail
returns config diagnostics, inert-rules presence, live sessions, reference blockers,
revisioned artifacts, and bounded linked/detached/unlinked/conflicting inventory. Transfer
accepts only `keep | move | copy` plus a known target scope and source revision; it accepts
no arbitrary path. Forget returns typed reference counts and never mutates repository files.

`GET /processes?session=<id>` returns the bounded process tree for one session. Omitting
`session` returns one coherently sampled fleet snapshot: `sessions[]` grouped by stable
session/space identity plus aggregate live process, CPU, resident-memory, listener, and
established-connection totals. Per-process records expose CPU/memory and connection/listener
counts, not network byte throughput. Sampling is serialized so the background inspector and
browser refreshes cannot race or corrupt CPU deltas. Process actions still require the exact
owning session ID and PID identity.

### Automation and fleet contracts

Canonical `/automation/rules` accepts a complete version-1 `rules.toml`; validation occurs
before atomic replacement. Ordinary per-rule PATCH accepts only boolean `enabled` and
`shadow`. Dry-run selects one persisted event sequence and writes no firing, action,
checkpoint, provider-call, annotation, notification, or spend record. Repository rule files
appear only in `/automation` diagnostics with `execution: "inert"`.

Provider key operations are `{operation:"test"|"set"|"replace"|"clear", key?:string}`.
Responses expose configured/source/persistent status, never the submitted or stored key.
Model refresh is explicit. Provider status includes the fixed origin, exact configured model
IDs, and cached model metadata/stale/error state.

`POST /history/{id}/second-opinion` with `confirm:false` returns the full generated prompt,
bounded Git context, other backend, cwd, and `preview_token` without spawning. `confirm:true`
must repeat the unchanged inputs and exact token; success creates one ordinary session and a
`review` lineage edge. No canonical rule/model action reaches this endpoint.

`POST /automation/batches` accepts a kind plus 1..25 ended agent-run IDs with native
transcripts. `confirm:false` returns maximum token/call estimates and `preview_token`.
`confirm:true` requires that exact token and enabled/budgeted automation. Results are
preview/export data; `repository_mutation` is always false.

Lineage relations are `resume | handoff | continuation | review`. The absence report is
bounded since explicit `since` or the persisted last attach/input checkpoint. Workload
telemetry labels ccusage provider/model costs as aggregate—not per-run attribution.
Injection-safety output is research-only and always has `authorizes_actuation:false`.

## Terminal WebSocket

`GET /pty/{id-or-name}`

- Server order on attach: JSON state → `replay_start` → binary replay → `replay_end` → binary live output.
- Server JSON bootstrap: `{type:"state", snapshot:Session, revision}` then
  `{type:"replay_start", reason:"attach", allow_terminal_responses:boolean}` / optional binary replay /
  `{type:"replay_end", reason:"attach"}`.
- Server JSON live update: `{type:"update", snapshot:Session, revision}`.
- Slow-client recovery: `{type:"gap", dropped_bytes, dropped_chunks}` then replay
  brackets with `reason:"resync"`, replay bytes, and a current update snapshot.
- Server exit: `{type:"exit", snapshot:Session, revision, reason}`.
- Client JSON: `{type:"input", data:string, broadcast?:boolean}`.
- Client JSON: `{type:"claim_input"}`; most recently focused pane becomes sole input owner.
- Client JSON: `{type:"resize", cols:number, rows:number}`.
- Client binary: direct PTY input bytes.

Only the input owner may write. Other attachments remain read-only until focus/claim;
this prevents duplicate xterm device-query responses from becoming visible prompt input.
The client suppresses xterm-generated input until replay rendering completes, so historical
device queries cannot generate fresh responses at the shell prompt.
Browser WebSockets use the current origin and carry no swe-mux bearer or query token.
Mutating HTTP requests and WebSocket upgrades validate `Host` and the full browser `Origin`
authority, including explicit ports;
supported hosts are localhost addresses, Tailscale address ranges, and tailnet
`*.ts.net` names.

## Event WebSocket

`GET /events` emits normalized JSON records:

```ts
type MuxEvent = {
  seq: number
  ts: number
  session_id: string | null
  source: "hook" | "transcript" | "pty" | "process" | "git" | "daemon" |
          "settings" | "user" | "automation" | "hooks" | "ccusage" | "external_file"
  type: string
  payload: Record<string, unknown>
}
```

Automation converts persisted mux events to a separate version-1 normalized envelope with
trusted run/spawn project scope, agent-run identity, backend/session/space state, source,
confidence, capability, chain ID/depth/rule path, and an allowlisted bounded payload.
Native hook/transcript fields never pass through that boundary. `annotation_created`,
`notification_created`, attention composites, environment interlocks, and capability
degradation re-enter the same event stream subject to chain/loop limits.

`GET /events?after_seq=N` and `GET /events` WebSocket bootstrap return records strictly
after the supplied cursor in sequence order. The client advances by `seq`, not timestamp.

## CLI

```text
mux ls
mux spawn --backend shell|claude|codex --cwd PATH --name NAME --space ID
mux spawn --profile ID [--arg VALUE]
mux profiles
mux send ID_OR_NAME TEXT
mux send --all-broadcast TEXT
mux kill ID_OR_NAME
mux history
mux resume HISTORY_ID
mux spaces
mux doctor
```

Configuration: `MUX_URL`.
