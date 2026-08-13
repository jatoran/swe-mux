# Architecture

## Vocabulary

- Daemon: `muxd`; owns persistence, HTTP, WebSockets, and background workers. Owns PTYs
  in-process by default; with the PTY supervisor enabled it is a supervisor client instead.
- PTY supervisor: optional standalone `swe_mux.supervisor` process (flag:
  `pty_supervisor_enabled`). Owns ConPTYs, their read loops, authoritative scrollback, and the
  kill-on-close reaper Job so live sessions survive a daemon restart. Deliberately small and
  near-frozen; volatile code stays in the daemon.
- Desktop supervisor: optional Windows `swe-mux` process; owns WebView2 window, tray, login
  startup, and a separately running managed daemon.
- Project: explicit canonical folder and the only session/layout/resource container.
- Group: optional sidebar-only organization of Projects.
- Session: one ConPTY-hosted process with immutable Project ownership.
- Pane: one leaf region in the desktop split tree. Each pane owns an ordered tab stack and one
  active tab.
- Tab/view: one terminal, preview, History, or Project-resource viewport inside a pane. View
  placement is independent of process/file lifetime. Mobile flattens all pane tabs into one
  temporary rail without rewriting the desktop tree.
- Git scope/repository group: derived status/history metadata, not a canonical Project.

## Process model

```text
WebView2/browser SPA ── HTTP + WS ──> aiohttp daemon ──> ConPTY ──> shell/agent CLI
       ▲                                  ▲
       └── Windows tray supervisor ───────┘ secret loopback shutdown only
                               │       │             └── descendants/listeners
                               │       ├── Projects + Groups + history/evidence in SQLite
                               │       ├── project `.swe-mux/` note/config/files
                               │       ├── Git/events/usage/account workers
                               │       └── bounded automation ──> OpenRouter
                               └── global + per-session Win32 jobs
```

With `pty_supervisor_enabled` the PTY column moves one process out (session-preserving
reload): the daemon becomes a client of a token-authenticated loopback IPC socket, and the
supervisor owns the ConPTYs, their read loops, authoritative scrollback, and the reaper Job.
Restarting the daemon then leaves agents running; the next daemon discovers the supervisor
via `<data_dir>/supervisor.json`, reattaches, and rebuilds each live session from mirrored
metadata plus a scrollback snapshot. Shutdown intent is signaled from outside the daemon:
"quit" reaps everything through the supervisor (identical end state to the in-process mode),
while "restart"/detach leaves supervised sessions alive. In-process spawning remains the
automatic fallback whenever the supervisor is unreachable.

## Package boundaries

- `server.py`: transport composition and Project-bound session operations.
- `desktop.py`: Windows single-instance shell, tray/window lifecycle, login startup, daemon
  supervision, and desktop control token.
- `desktop_window_state.py`: versioned desktop geometry persistence and monitor-safe restore.
- `__main__.py`: reusable aiohttp runner and standalone/desktop-child daemon entry.
- `projects.py`: explicit Project and Group lifecycle.
- `git_projects.py`: derived Git worktree/repository identity.
- `project_files.py`: safe project config, note, directory, and file access.
- `agent_context.py`: descriptor-driven bounded provider-memory/instruction discovery plus revision-guarded,
  reversible synchronization for harness-declared Project instruction files.
- `project_watcher.py`: leased, non-recursive watches for directories visible in open
  resource tabs.
- `project_actions.py`: trusted discovery and normalization of VS Code, package, and native
  Project Actions into direct spawn requests (shell command line, contained cwd, env), so a
  step runs as an ordinary supervisor-owned shell with no swe-mux binary in its process tree.
- `session.py`: live registry, spawn/stop, scrollback, PTY fanout, supervisor-session adoption.
- `supervisor.py`: standalone PTY supervisor process — ConPTY ownership, IPC server,
  authoritative scrollback, reaper Job, discovery file, single-instance mutex. Near-frozen.
- `supervisor_client.py`: daemon-side supervisor connection, `RemotePtyHost` (PtyHost-shaped
  facade), discover-or-spawn, session-metadata mirroring, `kill_server`.
- `scrollback.py`: byte-exact scrollback ring shared by sessions and the supervisor.
- `history.py`: SQLite schema, agent history/search index, Project layout persistence, and
  serialized durable access.
- `history_backfill.py`: cancellable daemon-local Project scans over complete provider-native
  history; scan jobs are progress state, not durable records.
- `transcript_view.py`: bounded Claude/Codex conversational-message parsing and timestamp
  summaries.
- `layouts.py`: server-side layout-v6 validation and migrations from versions 1–5.
- `sqlite_store.py`: process-wide per-database operation coordinator and failed-transaction
  rollback guard for History, Automation, Operational Telemetry, and Voice connections sharing
  one WAL database.
- `harness.py`: declared agent-harness identity, capability axes, delivery etiquette, tool catalogs, and hook events.
- `adapters/`: backend spawn/resume syntax, transcript discovery, and exit behavior.
- `processes.py`: descendant ownership, process actions, loopback previews.
- `operational_telemetry.py`: durable bounded process, quota/reset, compaction, and tool
  evidence plus transcript reconciliation and retention.
- `provider_accounts.py`: private auth snapshots, provider selection, and safe quota polling.
- `frontend/src/App.tsx`: Project/Group sidebar and layout coordination.
- `frontend/src/layout.ts`: typed layout-v6 transformations, resource identities, and browser
  migrations.
- `frontend/src/mobileWorkspace.ts`: pure depth-first mobile tab projection and close fallback.
- `frontend/src/HistoryBrowser.tsx`: searchable archive, transcript review, and Project backfill.
- `frontend/src/ProjectResource.tsx`: Project-owned notes, folder browser, and file tabs.
- `frontend/src/ProjectNoteEditor.tsx`: the shared Continuity Markdown editor for every
  editable `.md` surface (notes and Markdown files) plus the note-specific autosave wrapper;
  it owns only the in-memory revision and commits through a caller-supplied callback.

## Lifecycle invariants

1. A Project must be created against an existing folder before any session can spawn.
2. Project creation initializes `.swe-mux/config.toml` and the first ordinary note at
   `.swe-mux/notes/project.md`, seeding it only when that file is absent.
   Additional notes are created explicitly from the Project's Notes collection.
3. `POST /api/sessions` requires `project_id` and defaults to the canonical root.
   An explicit cwd may be a contained subdirectory or an exact Git-listed worktree root of the same repository.
4. Session PATCH cannot change Project ownership. `cd` affects only validated runtime cwd.
5. Resume and cross-vendor review require a valid target Project and also start at its root.
6. Layout v6 stores one recursive split tree of mixed-view pane stacks, caps it at 64 leaves
   and depth 24, and uses optimistic revisions. Leaf IDs are globally unique within a layout.
   Closing resources never deletes files; terminal close uses an explicit kill path.
7. Group updates only change sidebar organization.
8. Worktrees are never Projects, tabs, or sidebar rows.
   The Git tab manages them, and the Project Run menu may create a worktree and start one session in it atomically.
9. Preview proxying accepts only bounded literal-loopback destinations attributable to a live
   session in the requesting Project or explicitly approved by the user. Endpoint identity is
   Project-wide; ownership follows the actual listener, and sandboxed cross-service requests
   stay inside registered `/preview/{id}/…` routes.
10. Provider system auth is authoritative. Startup derives saved selection from it and never
    restores registry memory into it; explicit switching atomically replaces only that auth file.
11. Durable process actions use PID plus creation-time fingerprint and revalidate immediately
    before acting; suspected orphans are never terminated automatically.
12. Quota attribution remains probabilistic with an explicit external remainder; only a
    twice-observed fresh unexpected reset may alert.
13. Repository tasks execute only after explicit user selection and local trust in the exact
    current bytes of all supported task files. A changed fingerprint returns to untrusted.
14. Desktop window close/minimize hides the viewport; it never stops the daemon or PTYs. Tray
    Quit confirms live sessions and requests graceful daemon shutdown through a secret-gated
    loopback route. A desktop crash leaves the separate daemon process running.
15. A session's root provider identity is immutable. Nested-agent promotion is a shell-only
    state transition, live transcript paths/native IDs have one owner, and supervisor metadata
    is revalidated against those facts before daemon reattachment publishes the session. The
    one-owner rule is also enforced continuously: the state watchdog's identity sweep reports
    any two live sessions claiming one conversation and heals a Claude session back to its own
    anchor, and backends whose CLI reports conversation changes itself (Claude) never adopt a
    conversation by filesystem heuristics.

## Failure modes

- Daemon crash closes the global job and terminates owned child processes — in-process PTY
  mode only. With the PTY supervisor, a daemon crash or restart leaves agents running; the
  next daemon reattaches and revalidates mirrored provider/transcript identity. Proven legacy
  contamination is repaired before observation resumes and its false history owner is hidden.
  Only a supervisor crash/kill (its Job closes) or an explicit quit / `muxd --shutdown` takes
  agents down. The supervisor itself cannot be hot-updated without killing sessions; it
  self-exits after a bounded idle window with no clients and no live sessions.
- Browser disconnect removes subscribers while PTYs and bounded scrollback remain live.
- Desktop/tray crash removes only the local desktop viewport. A new supervisor reconnects to a
  healthy daemon; an unmanaged daemon cannot be terminated through desktop control.
- Invalid/missing Project roots block creation or spawn before a PTY is allocated.
- Stale layout/file/note revisions return conflicts rather than overwriting newer data.
- Project history scans are daemon-local and cancellable. A daemon restart loses job progress,
  not indexed rows or provider transcripts; the next scan safely fingerprints and skips
  unchanged sources.
- Missing optional integrations report typed unavailable states without affecting terminals.
- History, automation, telemetry, and voice operations sharing one database are coordinated by
  a process-wide per-database lock. Failed operations roll back, and returning with an open
  transaction is rejected, so one feature worker cannot strand or contend for the WAL writer
  slot and break unrelated terminal spawns or PTY attachment events.
- Account/quota failures preserve live processes and retain recent successful quota data.
- Reused/inaccessible process fingerprints remain stale/unverifiable instead of attaching to
  a live PID. Parser drift remains visible as unknown/unmapped coverage.
- Passive note-editor prop synchronization can replay the pre-edit value after the first
  browser input; layout-synchronous creation/reconciliation prevents the resulting input loop.
