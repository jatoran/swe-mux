# Architecture

## Vocabulary

- Daemon: `muxd`; owns PTYs, persistence, HTTP, WebSockets, and background workers.
- Project: explicit canonical folder and the only session/layout/resource container.
- Group: optional sidebar-only organization of Projects.
- Session: one ConPTY-hosted process with immutable Project ownership.
- Pane/tab: one browser viewport region containing mixed terminal, preview, or Project-resource
  tabs; presentation is independent of process/file lifetime.
- Git scope/repository group: derived status/history metadata, not a canonical Project.

## Process model

```text
Browser SPA ── HTTP + WS ──> aiohttp daemon ──> ConPTY ──> shell/agent CLI
                               │       │             └── descendants/listeners
                               │       ├── Projects + Groups + history/evidence in SQLite
                               │       ├── project `.swe-mux/` note/config/files
                               │       ├── Git/events/usage/account workers
                               │       └── bounded automation ──> OpenRouter
                               └── global + per-session Win32 jobs
```

## Package boundaries

- `server.py`: transport composition and Project-bound session operations.
- `projects.py`: explicit Project and Group lifecycle.
- `git_projects.py`: derived Git worktree/repository identity.
- `project_files.py`: safe project config, note, directory, and file access.
- `project_watcher.py`: leased, non-recursive watches for directories visible in open
  resource tabs.
- `session.py`: live registry, spawn/stop, scrollback, PTY fanout.
- `history.py`: SQLite schema and serialized durable access.
- `sqlite_store.py`: shared failed-transaction rollback guard for the History, Automation,
  Operational Telemetry, and Voice connections that use the same WAL database.
- `adapters/`: backend executable flags, resume syntax, transcripts, and exit behavior.
- `processes.py`: descendant ownership, process actions, loopback previews.
- `operational_telemetry.py`: durable bounded process, quota/reset, compaction, and tool
  evidence plus transcript reconciliation and retention.
- `provider_accounts.py`: private auth snapshots, provider selection, and safe quota polling.
- `frontend/src/App.tsx`: Project/Group sidebar and layout coordination.
- `frontend/src/ProjectResource.tsx`: project note, folder browser, and file tabs.
- `frontend/src/ProjectNoteEditor.tsx`: controlled CodeMirror lifecycle; value creation and
  reconciliation run in layout effects so stale props cannot overwrite browser input.

## Lifecycle invariants

1. A Project must be created against an existing folder before any session can spawn.
2. Project creation initializes `.swe-mux/config.toml` and `.swe-mux/notes/project.md`.
3. `POST /api/sessions` requires `project_id`; the daemon ignores no alternate cwd and
   always spawns at the canonical root.
4. Session PATCH cannot change Project ownership. `cd` affects only validated runtime cwd.
5. Resume and cross-vendor review require a valid target Project and also start at its root.
6. Layout v6 stores one recursive split tree of mixed-view tab panes and uses optimistic
   revisions. Closing panes/resources never kills processes or deletes files.
7. Group updates only change sidebar organization.
8. Worktree endpoints remain for Git tooling, but the primary UI does not create or display
   worktrees as Projects, tabs, or sidebar rows.
9. Preview proxying accepts only bounded loopback destinations attributable to a session or
   explicitly approved by the user.
10. Provider system auth is authoritative. Startup derives saved selection from it and never
    restores registry memory into it; explicit switching atomically replaces only that auth file.
11. Durable process actions use PID plus creation-time fingerprint and revalidate immediately
    before acting; suspected orphans are never terminated automatically.
12. Quota attribution remains probabilistic with an explicit external remainder; only a
    twice-observed fresh unexpected reset may alert.

## Failure modes

- Daemon crash closes the global job and terminates owned child processes.
- Browser disconnect removes subscribers while PTYs and bounded scrollback remain live.
- Invalid/missing Project roots block creation or spawn before a PTY is allocated.
- Stale layout/file/note revisions return conflicts rather than overwriting newer data.
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
