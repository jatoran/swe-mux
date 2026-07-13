# Agent Terminal Multiplexer — Product Spec

This document specifies the final product: a browser-based terminal multiplexer for
interactive agent CLI sessions (Claude Code, Codex CLI) and plain shells. It replaces
the orchestrator/lead/workload model of the current codebase. This is the successor
project's contract; the current repo is the donor codebase.

Not in this product: leads, roles, sub-orchestrators, auto-prime, heartbeat wake
loops, workloads, projects, DONE protocol, depth/subtree caps, bus inspector UI.

---

## 1. Product summary

A long-lived local **daemon** owns PTY-hosted CLI sessions, grouped into **spaces**.
A **browser client** attaches and detaches freely — closing the tab never affects
sessions; killing the daemon kills all sessions (deliberate: no zombie/hanging state,
no auto-restore of live sessions). Sessions are real interactive TUIs in real ConPTYs
rendered through xterm.js — never headless. The daemon monitors agent sessions
through CLI hooks + transcript tailing, exposes a uniform event stream, and layers a
user-definable meta-hook system on top. A session **history index** records every
session ever run so past transcripts can be browsed and manually resumed as new
sessions. An HTTP API + `mux` CLI make everything scriptable. Agent-to-agent
communication (linking two sessions so the system relays messages) is architecturally
reserved but not built initially.

## 2. Vocabulary

- **daemon** — the persistent Python process. Owns PTYs, spaces, session registry,
  event bus, history index. One per machine (configurable port).
- **session** — one PTY-hosted process: an agent CLI (Claude Code, Codex) or a plain
  shell. Has a stable id, a display name, a cwd, a backend adapter, a state.
- **space** — a named group of sessions with its own pane layout. Sidebar shows one
  space at a time; sessions can move between spaces.
- **backend adapter** — per-CLI plugin implementing spawn/resume args, transcript
  location + parsing, and hook wiring. Adapters: `claude`, `codex`, `shell` (null).
- **pane** — a client-side viewport bound to one session's byte stream. A space's
  main area is a tiling tree of panes (splits).
- **event** — a normalized record emitted by the daemon about a session
  (state change, turn end, approval needed, process exit, ...).
- **meta-hook** — a user-defined rule: event pattern → action, evaluated in the daemon.
- **history entry** — an indexed record of a session (live or ended) pointing at its
  native transcript, enabling view-without-resume and manual resume.

## 3. Architecture

```
┌───────────────────────────────────────────────────────────────┐
│ Browser client (Vite + TypeScript SPA)                        │
│   xterm.js panes (webgl, search, web-links, unicode11,        │
│   clipboard/OSC52 addons) · sidebar · palette · context menus │
└──────────────┬────────────────────────────────────────────────┘
               │ WS: PTY bytes + JSON control frames
               │ HTTP: REST API (same token auth)
               ▼
┌───────────────────────────────────────────────────────────────┐
│ Daemon (Python, aiohttp)   bind: configurable host:port       │
│                                                               │
│  SessionManager ── Session ──┬ PtyHost (ConPTY, pywinpty)     │
│                              ├ BackendAdapter                 │
│                              │   ├ transcript tailer + parser │
│                              │   └ hook receiver wiring       │
│                              ├ scrollback buffer              │
│                              └ StateMachine                   │
│  SpaceManager (spaces, layouts)                               │
│  EventBus (in-process pub/sub; all sources normalize here)    │
│  MetaHookEngine (rules: event pattern → action)               │
│  HistoryIndex (SQLite: every session ever run)                │
│  GitMonitor (per-cwd status polling)                          │
│  ReaperJob (Win32 job object: daemon death kills every child) │
│  HookIngress (HTTP endpoint CLI hooks POST to)                │
└──────────────┬────────────────────────────────────────────────┘
               ▼
        claude.exe / codex.exe / powershell.exe ... (one ConPTY each)
```

Single daemon process; no bus sidecar. The old bus's one surviving concern —
stable session identity + an event/message substrate — moves in-process
(EventBus + SQLite). If cross-session relay ships later, it rides on these.

### Lifecycle invariants
- Browser attach/detach has zero effect on sessions.
- Daemon exit (clean or crash) kills every child via the job object. No orphans,
  no auto-restore on next start. The HistoryIndex is the only thing that persists.
- Killing a session from the UI/API: graceful backend-specific exit, then
  `taskkill /F /T` fallback (current `PtyHost.stop` behavior).

## 4. Backend adapters

Interface (each CLI implements):

```python
class BackendAdapter(Protocol):
    name: str                                   # "claude" | "codex" | "shell"
    def spawn_cmdline(self, sid: str, opts: SpawnOpts) -> tuple[str, str]   # (appname, cmdline)
    def resume_cmdline(self, native_id: str, opts: SpawnOpts) -> tuple[str, str]
    def transcript_path(self, native_id: str, cwd: Path) -> Path | None
    def make_parser(self) -> TranscriptParser | None    # events -> normalized updates
    def hook_config(self, sid: str, ingress_url: str) -> HookSetup | None
    def graceful_exit_keys(self) -> str                 # e.g. "/exit\r"
```

- **claude** — `--session-id <uuid>` spawn / `--resume <uuid>` resume; JSONL at
  `~/.claude/projects/<encoded-cwd>/<sid>.jsonl` (port `transcript.py` encoding +
  parser + StateMachine verbatim). Hooks: spawn with `--settings <generated.json>`
  registering `Stop`, `Notification`, `PreToolUse`, `SessionStart`, `SessionEnd`
  hooks whose command POSTs `{sid, event, payload}` to the daemon's HookIngress.
  `Notification` (permission prompt) replaces the 15s awaiting-inference heuristic
  as the primary `awaiting` signal; transcript inference stays as fallback.
- **codex** — rollout JSONL under `~/.codex/sessions/`; `codex resume <id>`.
  `notify` config program → HookIngress for turn-complete / approval-needed.
  Parser targets the rollout schema (reverse-engineer; keep parser isolated so
  schema drift is a one-file fix).
- **shell** — any exe (default PowerShell). No tailer, no state machine, no hooks.
  State is only `running | exited`.

Adapter selection is per-session at spawn time. Nothing outside the adapter may
reference CLI-specific paths, flags, or event schemas.

## 5. Session model & state

Session record (daemon memory, mirrored to HistoryIndex):

```
id (uuid) · name · space_id · backend · native_session_id · cwd · exe+args ·
pid · created_at · state · state_detail · tokens_in/out · context_pct ·
last_activity_ts · git {branch, dirty, ahead, behind} · pinned_attention
```

States: `running` (shell), and for agents: `starting → working → idle`,
`awaiting` (approval/input needed), `exited`, `crashed`. Signal priority:
hook events (exact) > transcript inference > PTY liveness (1Hz ticker).

### Scrollback
Per-session in-memory scrollback buffer, default 5 MB (configurable), replayed on
attach so a fresh pane reconstitutes screen + history; xterm.js scrollback set to
match. No disk persistence — sessions don't outlive the daemon, so neither does
scrollback. Conversation history beyond the buffer lives in the native transcript
(viewable via the history browser).

## 6. Event system & meta-hooks

All observation normalizes into one per-daemon event stream:

```
{ts, session_id, source: hook|transcript|pty|daemon, type, payload}
```

Types (initial set): `state_changed`, `turn_started`, `turn_ended`,
`approval_needed`, `tool_use`, `session_spawned`, `session_exited`,
`session_crashed`, `git_changed`, `message` (reserved for relay).

Events fan out to: the StateMachine/UI (WS control frames), the MetaHookEngine,
and an append-only `events` table in SQLite (queryable via API, powers history
detail views).

**MetaHookEngine**: user rules in `~/.mux/hooks.toml` (hot-reloaded):

```toml
[[hook]]
match = { type = "approval_needed", backend = "claude" }   # field/glob match
action = { kind = "notify", channel = "ui" }               # v1: ui badge only

[[hook]]
match = { type = "turn_ended", session_name = "builder-*" }
action = { kind = "run", command = "powershell -File notify.ps1 {session_name}" }
```

Action kinds: `notify` (UI now; telegram/ntfy channels later — channel is a string,
so new transports are additive), `run` (subprocess with template vars), `write_pty`
(inject text into a session — the sanctioned home of type-at-the-TUI; used by
future relay), `http` (POST payload to a URL). Actions are rate-limited per rule.

## 7. Communication substrate (reserved, not built)

Shipped now: stable session ids, the EventBus, `write_pty` action, and a `links`
table (`session_a, session_b, mode, created_at`) with no consumers. Later, a relay
becomes: UI links two sessions → meta-hook rule template "on message from A,
when B idle, write_pty to B". No daemon rearchitecture required. No roles, no
depth, no orchestration protocol — relay policy is just meta-hook rules.

## 8. HTTP API + CLI

REST (all under `/api`, token-authed):

```
GET    /sessions                      list (filter: space, state, backend)
POST   /sessions                      spawn {backend, name?, cwd?, space?, exe_args?, worktree?}
GET    /sessions/{id}                 snapshot
PATCH  /sessions/{id}                 rename, move space, pin
DELETE /sessions/{id}                 kill
POST   /sessions/{id}/input           write text/keys to PTY
POST   /sessions/{id}/broadcast-set   include/exclude from broadcast group
GET    /spaces · POST /spaces · PATCH /spaces/{id} · DELETE /spaces/{id}
GET    /history                       indexed past sessions (search, filter)
GET    /history/{id}/transcript       parsed transcript view (no resume)
POST   /history/{id}/resume           spawn new session via adapter resume
GET    /events?since=&session=        query event log
GET    /config · GET /health
WS     /pty/{id}                      bytes + control frames (resize, state)
WS     /events                        live event stream (UI + external consumers)
```

`mux` CLI wraps it: `mux ls`, `mux spawn --backend claude --cwd . --name x`,
`mux send <name> "text"`, `mux kill <name>`, `mux history`, `mux resume <id>`,
`mux spaces`. Names resolve to ids server-side.

## 9. History index & resume

SQLite `history` table: every session ever spawned (id, native_id, backend, name,
cwd, spawn/exit timestamps, exit reason, final token counts, transcript path).
On daemon start, optionally reconcile: scan adapter transcript dirs for sessions
created outside the mux (flag `external=true`) so the browser sees all CLI history,
not just mux-spawned. History UI: searchable list → transcript viewer (parsed,
read-only, rendered from the native JSONL — not a PTY replay) → "Resume" button
spawns a fresh session via `resume_cmdline` into a chosen space. Deleting a history
entry never deletes the native transcript file.

## 10. Frontend

Vite + TypeScript SPA (light framework — Preact or Svelte; no build-time coupling
to the daemon beyond the API). Layout: topbar (space tabs, global status) ·
sidebar (session list for active space) · main (pane tiling tree) · no inspector strip.

### Terminal panes
- xterm.js + addons: **webgl** renderer, **fit**, **search** (find-in-scrollback UI),
  **web-links**, **unicode11**, **clipboard** (OSC 52).
- Input transparency contract (herdr postmortem): the focused terminal receives
  every key by default. The client intercepts ONLY bindings on an explicit,
  user-visible allowlist, checked in exactly one place
  (`attachCustomKeyEventHandler`). No silent drops: an intercepted key that
  matches no enabled action falls through to the PTY.
- Clipboard: native xterm paste path (bracketed paste preserved — no raw-write
  Ctrl+V override; use `term.paste()`). Ctrl+C with active selection = copy +
  clear selection; without selection = SIGINT. Ctrl+Shift+C/V always copy/paste.
  Right-click menu: selection-aware Copy / Paste / Select All / Clear / Find.
  Middle-click paste optional (setting).
- Resize: per-pane fit → WS resize frame → ConPTY resize (current plumbing).

### Panes & splits
Tiling tree per space: split horizontal/vertical, drag borders, close pane
(≠ kill session — a session can be unpaned and live in the sidebar only),
swap/move panes, zoom (temporarily maximize one pane). Layout persists per space
in the daemon. Empty pane state offers: attach existing session / spawn agent /
spawn shell.

### Sidebar & attention
Rows: state dot · name · backend chip · ctx% chip (thresholded colors) · git chip
(branch ± dirty count) · quick actions on hover. **Attention routing**: sessions in
`awaiting` sort to top with a distinct badge + subtle row pulse; `crashed`/`exited`
next; document.title carries an attention count (e.g. `(2) mux`) so it's visible
from other tabs — this is the whole of v1 notifications; the `notify` action's
channel abstraction is where telegram/ntfy attach later.

### Context menus
- Sidebar session row: Rename · Kill · Resume-as-new (if exited) · Move to space ·
  Add to broadcast · Open in pane / split · Copy session id · Copy cwd ·
  Reveal cwd in Explorer · Worktree ▸ (create worktree + spawn session in it,
  list worktrees, remove worktree — thin wrapper over `git worktree`).
- Space tab: Rename · New session here · Close space (prompts: kill or move sessions).
- Terminal pane: clipboard menu above + Split ▸ · Detach pane · Kill session.

### Hotkeys & command palette
- Every UI action is a named **command**; palette (default Ctrl+Shift+P) lists and
  runs all of them, fuzzy-searched.
- User keybindings in `~/.mux/keybindings.json`: `{ "ctrl+alt+t": "session.spawnShell",
  "ctrl+alt+1..9": "space.activate(n)", ... }`. Defaults ship for pane nav/split,
  space switching, palette, find. Validation rejects bindings that would shadow
  plain typing or unmodified keys (herdr guardrail). Browser-reserved chords
  (Ctrl+W/T/N) are documented as unbindable.
- **Broadcast input**: toggleable mode; keystrokes to the focused pane mirror to
  every session in the broadcast set (visible warning banner while active).
  Also `mux send --all-broadcast`.

### Responsiveness (remote/Tailscale use)
Layout functional down to tablet/phone widths: sidebar collapses to a drawer,
single-pane view with a session switcher, touch-friendly context menus (long-press),
on-screen paste button (mobile clipboard API limits). No separate mobile app.

## 11. Remote access & security

- Daemon binds localhost plus the detected Tailscale IPv4 address by default; `--port`
  defaults to 8765. `--local-only` and `tailnet_enabled = false` suppress the tailnet
  listener. Never bind `0.0.0.0` or a LAN interface implicitly.
- No swe-mux bearer/login layer. Tailscale policy controls direct tailnet access. Browser
  mutations and WebSocket upgrades still validate Host/Origin against localhost,
  Tailscale address, or full `*.ts.net` names.
- No TLS in-daemon. Tailscale encrypts direct node traffic; `tailscale serve` is optional
  when browser-recognized HTTPS/secure-context APIs are wanted.
- HookIngress accepts only loopback + a per-session secret injected into hook
  configs, so arbitrary tailnet peers can't spoof session events.
- The tailnet UI retains localhost feature parity. Development servers stay on workstation
  loopback and are viewed through a session-owned `/preview/{registration}/…` HTTP and
  WebSocket/HMR bridge; swe-mux never exposes their raw ports or proxies arbitrary URLs.

## 12. Git awareness

GitMonitor polls each unique session cwd (only when a client is attached;
~5s cadence): branch, dirty count, ahead/behind upstream. Surfaced in sidebar
chips and `GET /sessions`. Worktree operations from the context menu run
`git worktree add/list/remove` under the daemon and spawn sessions into the
new cwd. These are the only mutating git operations the product performs, and
only ever user-initiated.

## 13. Refactors from the current codebase

**Port nearly verbatim** (proven, keep):
- `pty_host.py` — ConPTY spawn/read-thread/env-block/write discipline, all Win32 gotchas.
- `win_jobobj.py` — ReaperJob.
- `transcript.py` — tailer + Claude parser + StateMachine → becomes the `claude`
  adapter's internals (awaiting-heuristic demoted to fallback behind hook signal).
- WS byte protocol + replay-on-attach + resize flow from `server.py`/`agent.py`
  (fanout, subscriber queues; ring buffer generalized to the larger scrollback buffer).
- `respawn_agent`'s `--resume` flow → adapter `resume_cmdline` + history resume.

**Delete** (no successor):
- `heartbeat.py`, `workload.py`, bus sidecar (`bus/`), `tools/bus.py` CLI,
  auto-prime/`_build_prime_prompt`, roles/`bus_depth`/team endpoints,
  projects/lead/DONE machinery, dashboard keepalive, self-report loop,
  bus inspector / workload / project UI.

**Rewrite**:
- `server.py` → daemon: SessionManager/SpaceManager/EventBus/MetaHookEngine/
  HistoryIndex/HookIngress/GitMonitor + the API in §8.
- `static/index.html` → the SPA in §10.
- Agent bundle (`agent.py`) → backend-agnostic Session (adapter injected;
  hook wiring replaces heartbeat/self-report/auto-prime tasks).

**New**:
- Codex adapter (rollout parser, resume, notify wiring).
- Shell adapter.
- Meta-hook engine + hooks.toml.
- History index + reconcile scan + transcript viewer.
- `mux` CLI.
- Auth layer.
- Pane tiling, palette, keybindings, context menus, broadcast.

## 14. Configuration surface

`~/.mux/config.toml`: host/port, auth token + loopback-auth flag, default backend,
per-backend exe paths + default args, scrollback size, git poll cadence, notification
channels. `~/.mux/keybindings.json` and `~/.mux/hooks.toml` as above. All hot-reloadable
except bind address. Per-space overrides (default cwd, default backend) stored in
the daemon DB, edited via UI.

## 15. Explicit non-goals

- No orchestration: no roles, leads, task delegation, DONE protocols, agent caps.
- No headless agent execution — sessions are always real interactive TUIs.
- No session survival across daemon restarts (history index + manual resume instead).
- No in-daemon TLS/user-management — single-user, Tailscale-transported.
- No Electron/Tauri shell (revisit only if browser-reserved chords prove painful).
- No plugin marketplace; extensibility = meta-hooks + HTTP API + CLI.
