# Interfaces

## Scope

- In: implemented HTTP and WebSocket contracts.
- Out: UI interaction design and backend transcript schemas.

## HTTP

All JSON routes use `/api`. Non-loopback bindings require `Authorization: Bearer <token>`.

```text
GET    /health
GET    /config
GET    /sessions[?space=&state=&backend=]
POST   /sessions
GET    /sessions/{id-or-name}
PATCH  /sessions/{id-or-name}
DELETE /sessions/{id-or-name}
POST   /sessions/{id-or-name}/input
POST   /sessions/{id-or-name}/broadcast-set
POST   /sessions/{id-or-name}/promote
GET    /spaces
POST   /spaces
PATCH  /spaces/{id}
DELETE /spaces/{id}
GET    /history[?q=&backend=]
GET    /history/{id}/transcript
POST   /history/{id}/resume
GET    /events[?since=&session=]
GET    /notifications
POST   /hooks/{id}
GET    /git/worktrees[?cwd=]
POST   /git/worktrees
DELETE /git/worktrees
POST   /reveal
```

`/hooks/{id}` and `/sessions/{id}/promote` are loopback-only integration surfaces
authenticated with the per-session `X-Mux-Hook-Secret`; they do not accept the
ordinary bearer token as a substitute.

```ts
type SpawnSession = {
  backend?: "shell" | "claude" | "codex"
  name?: string
  cwd?: string
  space?: string
  exe?: string
  exe_args?: string[]
}

type SessionInput = { data: string }
type PatchSession = { name?: string; space?: string; pin?: boolean }
type BroadcastSet = { include: boolean }
```

## Terminal WebSocket

`GET /pty/{id-or-name}`

- Server order on attach: JSON state → `replay_start` → binary replay → `replay_end` → binary live output.
- Server JSON: `{type:"state", snapshot:Session}` or `{type:"exit"}`.
- Client JSON: `{type:"input", data:string, broadcast?:boolean}`.
- Client JSON: `{type:"claim_input"}`; most recently focused pane becomes sole input owner.
- Client JSON: `{type:"resize", cols:number, rows:number}`.
- Client binary: direct PTY input bytes.

Only the input owner may write. Other attachments remain read-only until focus/claim;
this prevents duplicate xterm device-query responses from becoming visible prompt input.
The client suppresses xterm-generated input until replay rendering completes, so historical
device queries cannot generate fresh responses at the shell prompt.

## Event WebSocket

`GET /events` emits normalized JSON records:

```ts
type MuxEvent = {
  ts: number
  session_id: string | null
  source: "hook" | "transcript" | "pty" | "daemon"
  type: string
  payload: Record<string, unknown>
}
```

## CLI

```text
mux ls
mux spawn --backend shell|claude|codex --cwd PATH --name NAME --space ID
mux send ID_OR_NAME TEXT
mux send --all-broadcast TEXT
mux kill ID_OR_NAME
mux history
mux resume HISTORY_ID
mux spaces
```

Configuration: `MUX_URL`; optional `MUX_TOKEN`.
