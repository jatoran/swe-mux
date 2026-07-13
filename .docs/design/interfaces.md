# Interfaces

## Scope

- In: implemented HTTP and WebSocket contracts.
- Out: UI interaction design and backend transcript schemas.

## HTTP

All JSON routes use `/api`. Non-loopback bindings require `Authorization: Bearer <token>`.

```text
GET    /health
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

`/hooks/{id}` and `/sessions/{id}/promote|demote` are loopback-only integration surfaces
authenticated with the per-session `X-Mux-Hook-Secret`; they do not accept the
ordinary bearer token as a substitute.

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

Config reads return no bearer or hook secrets and include a revision/ETag. PATCH accepts
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
  direction: "horizontal" | "vertical"
  ratio: number // inclusive 0.1..0.9
  first: LayoutNode
  second: LayoutNode
}
type LayoutNode = LayoutLeaf | LayoutSplit
type SpaceLayout = { version: 2; root: LayoutNode | null }
type PatchSpace = {
  name?: string
  default_profile_id?: string | null
  default_cwd?: string | null
  layout?: SpaceLayout
  layout_revision?: number // required with layout; stale values reject the whole write
}
```

Layout validation limits trees to 64 leaves and depth 24, rejects duplicate resource
identities, and migrates persisted version-1 `{version:1,panes:string[]}` layouts to v2.

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
The browser uses a WebSocket subprotocol for the bearer. Query-token compatibility still
exists in this phase and is removed by the Phase 5 login/token hardening.

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
```

Configuration: `MUX_URL`; optional `MUX_TOKEN`.
