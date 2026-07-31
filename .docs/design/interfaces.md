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
POST   /projects                    {name, root, group_id?, create_missing?}
PATCH  /projects/{project_id}       mutable Project fields/layout revision
PUT    /projects/order              {project_ids, expected_order}
DELETE /projects/{project_id}

GET    /project-groups
POST   /project-groups              {name}
PUT    /project-groups/order        {group_ids, expected_order}
PATCH  /project-groups/{group_id}   {name?, position?}
DELETE /project-groups/{group_id}
```

Project payloads add `created_at` (registration, epoch seconds) and derived `last_activity`
(latest session activity from history); both are `0` when unknown. Both order endpoints demand a
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
Project note, session note, and individual file resources; `history` is the searchable
session archive; `queue` is the per-target prompt-queue tab, its id `queue:<session_id>`
(prefixed so it can never collide with the target's own terminal leaf). Versions 1–6 are
migrated when read. Visible legacy resource docks become
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
GET     /projects/{project_id}/automations
PUT     /projects/{project_id}/automations    {automations, revision?}
```

The observation inbox is a project-owned capture list (`.swe-mux/observations.json`, no AI).
Append is conflict-free; replace (toggle done, delete, reorder) is revision checked and
returns `409 revision_conflict`. Bounded to 500 items of 2,000 characters. An inbox file
that exists but cannot be parsed reports `status: "malformed"` and refuses both writes with
`409 observations_unreadable` — reading it as empty meant the next captured note rewrote the
file with one item and destroyed the rest. See `features/observations.md`.

The automations routes are the per-project control-plane opt-in surface. `GET` returns the
registry (id, kind, label, `requires`, `implemented`), the project's `requested` table, and
the resolution (`enabled`, plus `blocked` → the dependencies each still needs) so a toggle
can show a dependency tree rather than a flat checkbox list. `PUT` replaces the table
through the ordinary project-config write: `409 revision_conflict` on a stale revision,
`409 automation_not_implemented` for a reserved id with no code behind it. The same table is
also carried by the typed portable Project options (`features/automation-enablement.md`).

`GET|PUT /project/config` accept an optional `project_id`. Supplying it makes that
registered Project's root authoritative for paths; without it the daemon re-resolves the
supplied `cwd` through Git, which retargets a Project registered inside a larger worktree to
the enclosing one.

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

## Prompt queue (Phase 4) and its Phase 5 callers

```text
GET    /queue                                       per-target aggregates
GET    /queue/messages?target_session_id=           one target's ordered queue
POST   /queue/messages                              {target_session_id, body, armed?, insert_after?,
                                                     constraints?, correlation_id?}
PATCH  /queue/messages/{message_id}                 {body, revision} | {armed} | {after} |
                                                     {retarget_session_id} | {constraints}
POST   /queue/messages/{message_id}/cancel          {kind: cancelled|skipped|revoked}
GET    /queue/messages/{message_id}/deliveries      audit rows (no prompt text)
POST   /queue/send-next                             {message_id, revision, idempotency_key?, confirm?}
GET    /queue/export?target_session_id=[&redact_secrets=0]

GET    /queue/auto                                  master switch, pause, per-session opt-ins,
                                                     counters, promotion criteria
POST   /queue/auto/pause                            {paused}   emergency disable
PUT    /queue/auto/sessions/{sid}                   {enabled?, ttl_minutes?, max_sends?,
                                                     accept_agent_messages?}
POST   /queue/auto/report-unsafe                    {note}     operator review input
GET    /queue/mailbox?role=all|inbox|outbox         cross-target messages with provenance
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
policy, not config: the pause survives a restart and depends on no provider
(`features/auto-delivery.md`). `/queue/mailbox` is a view over the same message rows, with
sender/target labels and delivery state (`features/agent-messaging.md`).

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

`GET /sessions` adds a compact, read-only `delivery_readiness` object with
`state: safe|blocked|unknown`, a reason, and `authorized: false`. It is not accepted on writes.
Rows also carry `idle_reason`, the idle-axis sibling of `awaiting_reason`:
`waiting_on_background` means the turn genuinely ended (the composer accepts input,
`delivery_state` is unchanged) while the agent has background work that will wake it back
up. Completion sounds and push alerts skip that turn end; the next one is the moment worth
the user's attention.
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
`{type:"attach_ready", cols, rows, renderer, hidden}`. It applies those dimensions before
`replay_start`; an old-style `resize` frame also releases replay, and timeout preserves
compatibility with clients that send neither. Other frames arriving during this window are
buffered and handled after `replay_end`. Later `attach_ready` frames are equivalent to `resize`.
Replay is bounded by `attach_replay_bytes` (default 512 KiB) rather than by full retention, and
the same bound applies to a resync, which resets the client's terminal and so receives a complete
replay into an empty buffer rather than a patch. A pane the client is keeping mounted but not
showing reports `hidden:true` exactly as a backgrounded browser tab does, which deregisters its
viewport from geometry arbitration.

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
`observation_stale_since` beside the transcript path and its mtime. This is the endpoint for
"which conversation am I actually looking at", and staleness is the one fault that otherwise
presents as a perfectly healthy session (`features/backends.md`).

`{type:"resize", cols, rows, hidden}` registers a client's fitted size, or deregisters it
when `hidden` is true — a minimized window still reports layout and must not reshape the
PTY for the device in use. The daemon resizes to the input owner's viewport, or, with no
owner, to the smallest attached one, and announces the result to every client as
`{type:"geometry", cols, rows, owner_device}` (also sent after `replay_end` and after a
resync). A client whose own fit differs renders that geometry at a reduced font size
rather than fitting, which is what keeps two devices from resizing each other in a loop.
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

```text
GET /api/diagnostics/status-health
GET /api/diagnostics/background
```

`status-health` reports the fleet's transition ledger: proven/inferred counts, bounds, alarm.
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
otherwise indistinguishable from a quiet fleet) plus `project_cards` (cached/builds/skipped
and the last reason a project got no card — "no card" is a legitimate outcome, so the reason
has to be readable somewhere). Both are in-memory per daemon
boot and do not survive a restart. This is the surface that makes a poller which died — the
audited failure mode where a feature silently stops for the rest of the process lifetime —
visible instead of merely absent.

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

Resume/review confirmation must target an existing Project and starts at its root. Resume
returns `409 conversation_live` (with the owning `session_id`) when a live session currently
claims the row's native conversation — Branch, not resume, is the flow for forking a live
conversation. The resumed pane keeps the conversation's own name (no suffix), and for a Claude
row resumed at its recorded root it keeps the conversation's `agent_run_id` too: that resume
continues one transcript, so it continues one history entry rather than opening a second over
the same file. A Codex resume, or a Claude resume into a different root, is a new conversation
and gets its own entry plus a `resume` lineage edge. Backfill
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
GET    /git/graph
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

`GET /git/worktrees[?cwd=][&trunk=]` returns the porcelain worktree list. Each row that has
a branch also carries `unlanded`: commits on that branch which `trunk` (default
`integration`) does not have. The field is **absent** rather than `0` when it could not be
measured — no such trunk, a Git failure, or a timeout — because zero would read as "nothing
waiting to be landed". Each non-bare row also carries measured `working_tree` and, when the
trunk/branch comparison succeeds, `branch_delta`:

```ts
type GitChangeFile = {status: string; path: string; old_path?: string}
type GitChangeSummary = {
  total: number
  files: GitChangeFile[]        // first 200
  truncated: boolean
}
```

`working_tree` is uncommitted porcelain-v2 status for that exact worktree, including
individual untracked files. `branch_delta` is name-status from the trunk merge base to the
checked-out branch. Absence means unmeasured; measured zero means clean/no branch files.
`trunk` must match `[A-Za-z0-9._/-]{1,200}`.

`GET /git/graph?cwd=&limit=` returns `{lines, limit, has_more}`. `limit` is 1–200 (default
80). Lines are either `{kind:"connector", graph}` or typed commit rows carrying `graph`,
`oid`, `parents`, `refs`, `author`, `committed_at`, and `subject`. `graph` is Git's own
`git log --graph` prefix; connector rows preserve merge topology between commits. The route
is read-only and queries all local refs.

`POST /git/worktrees` takes `{cwd, path, branch?, start_point?, spawn?}`. With `spawn`
present (an ordinary spawn body; `project_id` required) it creates the worktree and then
starts a session whose cwd is forced to the new tree. The reply always carries
`spawn: {status}` where status is `not_requested | spawned | error`, plus `session_id` on
success or `error` on failure — the worktree is the durable artefact, so a failed spawn is
reported rather than raised and never unwinds it.

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

## Agent MCP surface

`POST /mcp` — streamable-HTTP MCP endpoint for spawned agent sessions (JSON-RPC 2.0,
protocol 2025-06-18; loopback-only; 256 KiB body cap; 120 calls/min per session). Auth is
`Authorization: Bearer <MUX_MCP_TOKEN>`; the token is per-session, minted at spawn,
injected into the session environment beside `MUX_MCP_URL`, and survives daemon restarts
via supervisor meta. Tools are read-only and Project-scoped: `list_sessions`,
`get_session`, `read_transcript`, `search_history`. Full contract:
`features/mux-mcp.md`. Unknown token → 401 (session ended or predates the surface);
non-loopback → 403; rate overflow → 429 with `Retry-After`.

## Other API groups

Configuration/keybindings, automation/annotations/lineage, events/notifications, voice,
remote status, filesystem discovery, and preview proxy routes retain their feature-specific
contracts described in the corresponding `features/` documents.

`GET /events[?after_seq=N][&session=<id>]` is the live event stream. `after_seq` is a resume
cursor: the client tracks the highest `seq` it has applied and sends it on reconnect, and the
server replays exactly the events above it (oldest first, each marked `replay: true`). With
no cursor the server replays the **newest** retained page, not the oldest — an established
install otherwise re-sent days-old history and delivered none of the gap. When more was
missed than the page carries, a leading `{"type": "events_gap", "reason":
"catchup_truncated"}` frame tells the client to full-refresh rather than assume it caught up.
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
