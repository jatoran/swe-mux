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
DELETE /history/{id}
GET    /events[?after_seq=&session=]
GET    /notifications
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

`/preview/{registration-id}/{path}` is the bounded preview bridge. Ordinary HTTP methods
proxy bodies/queries to the immutable registered literal-loopback origin; WebSocket
upgrades bridge text/binary/ping/pong frames and negotiated HMR subprotocols. Root-relative
HTML/CSS/module and runtime browser URLs are scoped to the registration. External redirects,
per-request upstream URLs, ended source sessions, oversized bodies/responses/messages, and
excess bridge concurrency fail closed.

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

type SessionInput = { data: string }
type PatchSession = { name?: string; space?: string; pin?: boolean }
type BroadcastSet = { include: boolean }
type LayoutLeaf = { type: "leaf"; kind: "terminal" | "note" | "preview"; id: string }
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
  children: Array<LayoutLeaf & { kind: "terminal" }>
}
type LayoutNode = LayoutLeaf | LayoutSplit | LayoutStack
type SpaceLayout = { version: 3; root: LayoutNode | null }
type PatchSpace = {
  name?: string
  default_profile_id?: string | null
  default_cwd?: string | null
  layout?: SpaceLayout
  layout_revision?: number // required with layout; stale values reject the whole write
}
```

Layout validation limits trees to 64 leaves and depth 24, rejects duplicate resource
identities, and migrates persisted version-1 `{version:1,panes:string[]}` plus version-2
split layouts to v3. Stack children are terminal leaves only and active selection uses a
stable child ID.

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

Project list results are activity ordered and bounded to 500 items per request. Detail
returns config diagnostics, inert-rules presence, live sessions, reference blockers,
revisioned artifacts, and bounded linked/detached/unlinked/conflicting inventory. Transfer
accepts only `keep | move | copy` plus a known target scope and source revision; it accepts
no arbitrary path. Forget returns typed reference counts and never mutates repository files.

## Terminal WebSocket

`GET /pty/{id-or-name}`

- Server order on attach: JSON state → `replay_start` → binary replay → `replay_end` → binary live output.
- Server JSON bootstrap: `{type:"state", snapshot:Session, revision}` then
  `{type:"replay_start", reason:"attach"}` / optional binary replay /
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
  source: "hook" | "transcript" | "pty" | "daemon"
  type: string
  payload: Record<string, unknown>
}
```

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
