# swe-mux Project Context

## Purpose

- swe-mux is a Windows-native browser terminal multiplexer for long-lived PowerShell, Claude Code, Codex CLI, and other declared agent-harness sessions.
- The daemon owns each ConPTY, so closing or reloading a browser does not stop a session.
- Explicit Projects bind sessions, layouts, notes, files, history scans, automation, and other resources to canonical folders.

## Architecture

- Python 3.12 backend: `src/swe_mux/`, with `aiohttp` HTTP and WebSocket transport.
- Browser client: `frontend/src/`, built with TypeScript, Preact, Vite, and xterm.js into `src/swe_mux/static/`.
- CLI entry points: `mux` from `swe_mux.cli`, `muxd` from `swe_mux.__main__`, and the optional desktop app `swe-mux` from `swe_mux.desktop`.
- Desktop shell: optional WebView2 window and tray process that supervises a separately running daemon.
- PTY supervisor: optional standalone `swe_mux.supervisor` process that owns ConPTYs, read loops, scrollback, and the reaper Job so sessions survive daemon restarts and app rebuilds.
- Persistence: SQLite stores Projects, Groups, history, layouts, events, automation, queue state, and operational evidence; Project-owned portable resources live under each Project's `.swe-mux/` directory.
- Integrations include Win32 Job Objects, Git, provider-native Claude and Codex transcripts, Tailscale, OpenRouter observers, provider account tooling, speech services, and loopback development previews.

## Canonical terminology

- **Project**: an explicit canonical folder and the only owner of sessions, layouts, and Project resources.
- **Group**: optional sidebar organization for Projects with no behavioral ownership.
- **Session**: one ConPTY-hosted process with immutable Project ownership.
- **Agent run**: one provider conversation on one PTY, identified by `agent_run_id`; `/clear`, `/new`, or another conversation replacement creates a successor run.
- **Harness**: a declared agent integration with capability axes, adapter behavior, tool catalogs, delivery etiquette, and hook events.
- **Pane**: one leaf region in the desktop split tree containing an ordered tab stack.
- **View or tab**: a terminal, preview, History, Queue, note, or file viewport whose placement is independent of process and file lifetime.
- **History**: durable lifecycle and searchable indexes derived from authoritative provider-native transcripts.
- **Tier 0 facts**: deterministic, model-free evidence such as file writes, commands, tests, Git events, and tool events.
- **Scan timeline**: a read-only, run-scoped Tier 1 semantic index over bounded transcript deltas and Tier 0 facts.
- **Fleet Queue**: the application-wide view over persistent prompt-queue messages and inert spawn requests.
- **Delivery readiness**: a fail-closed `safe | blocked | unknown` assessment used before prompt delivery; `unknown` never authorizes PTY input.

## Major subsystems and paths

- Runtime composition and APIs: `src/swe_mux/server.py`, `src/swe_mux/__main__.py`.
- Live sessions, PTYs, replay, and adoption: `src/swe_mux/session.py`, `src/swe_mux/pty_host.py`, `src/swe_mux/supervisor.py`, `src/swe_mux/supervisor_client.py`, `src/swe_mux/scrollback.py`.
- Harness definitions and provider adapters: `src/swe_mux/harness.py`, `src/swe_mux/adapters/`, `src/swe_mux/agent_launcher.py`.
- Projects, Groups, Project files, and layouts: `src/swe_mux/projects.py`, `src/swe_mux/project_files.py`, `src/swe_mux/layouts.py`.
- History and transcript views: `src/swe_mux/history.py`, `src/swe_mux/history_backfill.py`, `src/swe_mux/transcript_view.py`.
- Git, worktrees, processes, and previews: `src/swe_mux/git_monitor.py`, `src/swe_mux/git_review.py`, `src/swe_mux/worktree_setup.py`, `src/swe_mux/processes.py`, `src/swe_mux/preview_capture.py`.
- Automation and evidence: `src/swe_mux/automation.py`, `src/swe_mux/automation_registry.py`, `src/swe_mux/automation_store.py`, `src/swe_mux/tier0_store.py`, `src/swe_mux/deterministic_consumers.py`.
- Timeline context and scanning: `src/swe_mux/project_context.py`, `src/swe_mux/scan_timeline.py`, `frontend/src/ScanTimelineTab.tsx`.
- Queueing, messaging, and guarded delivery: `src/swe_mux/prompt_queue.py`, `src/swe_mux/agent_messaging.py`, `src/swe_mux/auto_delivery.py`, `src/swe_mux/delivery_readiness.py`, `src/swe_mux/mcp.py`.
- Browser composition and workspace state: `frontend/src/App.tsx`, `frontend/src/UtilityDrawer.tsx`, `frontend/src/layout.ts`, `frontend/src/mobileWorkspace.ts`.
- History, resources, Git, and automation UI: `frontend/src/HistoryBrowser.tsx`, `frontend/src/ProjectResource.tsx`, `frontend/src/GitTab.tsx`, `frontend/src/AutomationDashboard.tsx`.
- Packaging and frozen-app deployment: `packaging/build_desktop.py`, `packaging/redeploy_desktop.py`, `packaging/swe_mux.spec`.
- Maintained design documentation: `.docs/design/00_OVERVIEW.md`, with task routing in `.docs/CLAUDE.md` and active plans in `.docs/development/`.
- Tests: `tests/` for backend and contract coverage, `frontend/test/` for browser-client logic.

## Project-owned files

- `.swe-mux/config.toml`: typed, portable Project options and per-Project automation opt-ins.
- `.swe-mux/project-context.md`: user-authored Markdown reference supplied to timeline scans; swe-mux never derives it from repository docs or source files.
- `.swe-mux/notes/`: Project-owned Markdown notes, excluded from Git by its generated `.gitignore`.
- `.swe-mux/attachments/`: persistent user-selected session attachments, excluded from Git by a generated `.gitignore`.
- `.swe-mux/prompts/`: Project prompt templates stored as inert text.
- `.swe-mux/actions.toml`, `.vscode/tasks.json`, and root `package.json`: optional task sources that remain inert until explicitly selected and trusted against their exact current contents.

## Core workflows

- Create a Project against an existing folder before spawning a session; new sessions default to the Project root.
- A contained subdirectory may be used as a spawn cwd; an exact Git-listed worktree of the same repository is the only allowed out-of-root spawn exception.
- Starting `claude` or `codex` inside a shell can promote the existing terminal to an observed agent session while retaining immutable root-process identity.
- Native provider transcripts remain authoritative and in vendor-owned locations; swe-mux indexes them without moving or deleting them.
- Project Actions discover repository tasks without executing them, require explicit user selection, and require renewed trust after any supported task-file change.
- Worktrees are Git artifacts rather than Projects or sidebar rows; Project Run may create a worktree, run setup, and start one session in its exact root.
- Scan timeline requires three gates: the global master switch, the Project automation dependency closure, and explicit authorization for the current agent run.
- Timeline requests contain bounded transcript deltas, recent same-run records, Tier 0 fact identifiers, and the current user-authored Project context.
- **Scan full session** scans uncovered current-run messages oldest first to a fixed watermark while retaining normal provider, budget, validation, and authorization gates.
- Agent-facing MCP is loopback-only, token-authenticated, Project-scoped, bounded, and separates read tools from permission-gated write tools.
- Remote browser access uses localhost plus the detected Tailscale address; Tailscale policy is the access boundary because swe-mux has no separate remote login.

## Constraints and invariants

- A session belongs to exactly one Project for its entire lifetime; runtime cwd and Git scope never retarget ownership.
- One session may have several attached devices, but exactly one connection owns PTY input and one daemon-arbitrated geometry applies.
- Closing a resource view never deletes its file or process; killing a terminal is an explicit operation.
- Automation is opt-in per Project through a dependency graph; observers cannot type, approve, spawn, execute scripts, or mutate Projects.
- Scan timeline is read-only, run-scoped, budgeted, and uses `deepseek/deepseek-v4-flash` through OpenRouter with strict locally validated JSON output.
- Invalid provider output or provider failure produces no guessed scan record.
- Project context is UTF-8 Markdown at one fixed contained path, limited to 16 KiB, and saved atomically with revision checks.
- Project files and note writes use containment and optimistic revision checks; stale revisions return conflicts instead of overwriting newer data.
- Process identity requires PID plus creation-time and ownership evidence; suspected or inaccessible processes are never terminated automatically.
- Provider authentication and OpenRouter credentials do not belong in Project files or SQLite.
- Clipboard history is memory-only by default; secret-shaped and oversized copies are refused before storage.
- A daemon restart preserves sessions only when the PTY supervisor is attached; killing the supervisor or using `muxd --shutdown` reaps live sessions.
- Worktrees isolate edits, not the singleton runtime on port 8765 and `~/.mux`; never start the app or redeploy from a worktree.

## Development and deployment

- Install backend dependencies with `uv sync --group dev` and frontend dependencies with `npm install` or `.worktree-setup`.
- Build the frontend from `frontend/` with `npm run build`; generated hashed assets under `src/swe_mux/static/` are gitignored.
- For a source daemon, reload backend code with `POST /api/daemon/restart` or `mux reload-daemon` while preserving supervised sessions.
- The frozen desktop app serves its bundled backend and static assets, so source changes require `uv run python packaging/redeploy_desktop.py` from the primary checkout.
- Supervisor changes cannot use the ordinary session-preserving redeploy because replacing the supervisor requires intentionally stopping all live sessions.
- Before claiming a frontend change is live, compare the asset hash served at `http://127.0.0.1:8765/` with `src/swe_mux/static/index.html`.

## Validation conventions

- Canonical completed-worktree gate: run `.worktree-verify` directly in the finished worktree.
- Backend tests: `uv run pytest tests -q -m "not live_agent and not live_subagent and not live_telemetry and not live_quota"`.
- Backend lint: `uv run ruff check src/swe_mux tests packaging`.
- Backend types: `uv run mypy` with strict Python 3.12 checking.
- Frontend types: run `npx tsc --noEmit` from `frontend/`.
- Frontend tests: run `npm test` from `frontend/`.
- Live-agent, live-subagent, live-telemetry, and live-quota tests are explicit pytest markers and are excluded from the ordinary automated gate.
- Finished worktree branches reconcile with `master`, pass `.worktree-verify`, and land into the primary checkout by fast-forward only.
