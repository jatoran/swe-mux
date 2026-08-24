# Backend: the route package

Index: `../packages.md`.
Design: `../../../design/interfaces.md`.

`src/swe_mux/routes/` holds the daemon's HTTP and WebSocket handlers, one module per domain.
`server.py` remains the composition root and registers the table; it holds no handlers.
Each entry lists what the module serves, then **Not:** what it deliberately does not.

## Rules

### A route module never imports `server`

The composition root may depend on every domain.
A domain that depended back would let a handler reach the runtime handles without being given them, and the boundary would be a fiction.
Enforced by `tests/test_route_modules.py`.

### Shared code lives in a shared module

`routes/support.py` holds request-to-domain resolution.
`http_support.py` holds the transport primitives that modules below the route layer also need.
Neither is "whichever route module defined it first".

### Cross-module references go through the module

`from . import sessions`, then `sessions._spawn_from_body(...)`.
Importing the name binds a copy, so a test that replaces the implementation replaces something nothing calls.

### `routes/__init__.ORDER` is load-bearing

aiohttp resolves in registration order, so a static path registered after a dynamic one that matches it becomes unreachable.
`tests/test_route_modules.py` resolves every static path in the table and asserts it reaches its own handler.
Each module ends with a `ROUTES` tuple, and `routes.all_routes()` concatenates them in `ORDER`.

## Shared modules

### `routes/support.py`

Resolving a request to the thing it names: the Project behind `{project_id}`, the registered Project identity behind a record, the Project root behind a run, the epoch behind a query parameter, the file root behind a Project checkout, the human behind a queue write, and the optional JSON body.

**Not:** anything one domain owns, or anything that touches an HTTP response directly (`../daemon-runtime.md`, `http_support.py`).

## Daemon and configuration

### `routes/system.py`

The app shell (`/`, the manifest, the service worker), `/api/health`, the harness registry, the runtime log level, desktop shutdown, daemon restart, the frozen-app redeploy, and remote, firewall, and WSL-bridge status.
Also holds `PACKAGE_DIR`, the package-anchored checkout root the redeploy resolves from.

**Not:** the restart's successor-spawn policy beyond spawning it, or the redeploy build itself (`packaging/redeploy_desktop.py`).

### `routes/settings.py`

`/api/config` and its reset, the settings bundle, keybindings, meta-hooks, and `/api/settings`.
Also assembles the keybindings payload.

**Not:** validation or the write (`config.py`, `settings_store.py`), or applying a hot change to live handles (`runtime_config.py`).

### `routes/configurator.py`

`/api/configurator/options` and `/api/configurator/launch`.
Also builds `ConfiguratorService`, the service the gated MCP configurator tools are given.

**Not:** the guides or the generated inventory (`src/swe_mux/assets/`, `configurator.py`).

## Automation and attention

### `routes/automation.py`

Rules, dry runs, the dashboard, firings, annotations, the LLM provider and its key, notification policy, and per-Project enablement.
Also holds `FEATURE_SPENDERS` and spend-row labelling, the repo-rule entry cache, and the LLM-readiness and provider-status reads other modules call.

**Not:** running an observer (`automation.py`), or the enablement gate closure itself (built in `server.py`).

### `routes/attention.py`

Lineage, the attention inbox, item feedback, rule decisions, and the injection-safety read.

**Not:** ranking (`attention_ranking.py`) or narration (`attention_narration.py`).

### `routes/insights.py`

Second opinion, handoff export, workload telemetry, experiences, and observer batches.
Also runs an observer batch.

**Not:** the scan timeline's own reads (`routes/scan_timeline.py`).

## Projects

### `routes/projects.py`

Projects and their order, Groups, Project config, Project context, Git-Project scope, artifacts, directory pins, the filesystem browser, and launch profiles.
Also builds the Project snapshot every listing is drawn from.

`PUT /api/project/config` takes two shapes: `changes` with `base` writes named fields through `merge_project_config`, and `values` with `revision` replaces the document through `write_project_config`.
`base` is required whenever `changes` is sent - defaulting it to "no base" would turn the guard off for whoever omitted it.
`ProjectConfigConflict` is deliberately not caught here: `error_middleware` answers it with `409 revision_conflict` plus `conflicts` and `current`, so every route that merges reports a collision identically.
`PUT /api/projects/{id}/automations` (`routes/automation.py`) takes the same two shapes over its own two fields.

**Not:** Project files (`routes/project_files.py`) or Project Actions (`routes/project_actions.py`).

### `routes/project_files.py`

Files, trees, recent files, search, resources, reveal, ignore, and Project file watches.

**Not:** notes (`routes/notes.py`) or Agent Context (`routes/agent_context.py`).

### `routes/project_actions.py`

Action listing, source read and write, the trust gate, diffs, running an action, and init scripts.
Also holds `_start_project_action`, the one authority path that the MCP `run_action` tool is given a closure over, and action-timeout arming.

**Not:** parsing or substituting an action (`project_actions.py`), or spawning (`routes/sessions.py`).

### `routes/agent_context.py`

Agent Context reads, source reveal, sync preview, sync, and restore.

**Not:** discovery or the sync itself (`agent_context.py`).

### `routes/notes.py`

Global notes and Project notes.
Also holds the save-loop diagnostic and `_storage_note_id`, which the composition root uses too.

**Not:** note storage (`project_files.py`).

### `routes/observations.py`

The Project observation stream, and the human decision on a drafted request.
Also holds approval of a control request and of a land request.

**Not:** drafting a request (`agent_messaging.py`, `land_queue.py`).

### `routes/grants.py`

`/api/grants` - the one additive write behind every gate notice.

**Not:** deciding what is grantable (`grants.py`, whose closed sets are checked at import).

### `routes/schedules.py`

Scheduled runs, previews, and run history.

**Not:** firing one (`scheduler.py`) or the trigger arithmetic (`schedules.py`).

## Sessions

### `routes/sessions.py`

Session listing and spawn, patch, read, title regeneration, standing-activity clear, approvals, delete, relaunch, attachments and media, and promote/demote.
Also holds `_spawn_from_body`, the spawn transport every other spawn caller goes through, and generated-title and conversation-holder decoration.

**Not:** the spawn itself (`session.py`), media validation (`session_media.py`), or terminal writes (`routes/terminal.py`).

### `routes/terminal.py`

Session input, broadcast set and broadcast input, and startup metrics.
Also holds operator-input recording, composer insertion, interrupt, and graceful end - all reached from other modules and from the composition root.

**Not:** the PTY WebSocket (`routes/pty.py`) or input arbitration (`terminal_arbitration.py`).

### `routes/branch.py`

Branch points and branching a session.
Also holds cut-point payloads, sibling spawn, pane naming, and transcript-fork execution.

**Not:** writing the fork (`transcript_fork.py`) or linearizing a transcript (`transcript_view.py`).

### `routes/queue.py`

The prompt queue, its mailbox and export, and the auto-delivery controls.

**Not:** delivery (`auto_delivery.py`) or queue storage (`prompt_queue.py`).

### `routes/prompts.py`

The prompt library.

**Not:** template storage (`prompt_library.py`).

### `routes/voice.py`

Voice status, Kokoro models, the lexicon, transcription, latency and diagnostics, prepare/submit/approval/interrupt, generation, speech, and clips.
Also holds the current-approval read `routes/sessions.py` uses.

**Not:** synthesis or transcription themselves (`voice.py`, `kokoro_tts.py`).

### `routes/assistant.py`

Assistant dialogs, turns, interrupts, and action confirm, cancel, announce, and UI result.

**Not:** the assistant loop or its trust policy (`assistant.py`).

## Reading a session

### `routes/scan_timeline.py`

The scan timeline and its backfill, the change map, catch-me-up, fleet blockers, scan search, the transcript, skills, the agent environment, the MCP tool fetch, and runtime-inventory ingress.
Also holds change-map seed admission and worktree-membership validation.

**Not:** scanning (`scan_timeline.py`) or the code graph (`code_graph.py`).

### `routes/history.py`

Run history, backfills, scans, transcripts, resume, duplicates, and the event log.
Also holds the live run-id sets and the conversation parsing the diagnostic bundle shares.

**Not:** indexing (`history.py`) or resume policy (`session_resume.py`).

### `routes/diagnostics.py`

Status health, background health, notification diagnostics, network and storage usage, the diagnostics export, the doctor report, and the per-session state log and diagnostic bundle.
Also holds the live and post-mortem state-log payloads and the WSL-bridge report.

**Not:** the doctor's own checks (`doctor.py`) or the timeline store (`status_timeline.py`).

## Devices and processes

### `routes/clipboard.py`

Clipboard history.

**Not:** reading the OS clipboard (`clipboard_store.py` does not either; the browser captures).

### `routes/push.py`

Web Push subscription, device presence, and the notification list.

**Not:** sending (`push.py`) or deciding which device receives (`device_presence.py`).

### `routes/processes.py`

Owned processes, the Preview registry, and the Preview passthrough route.
The passthrough's handler is `preview_transport.preview_proxy`; only its registration is here, beside the registry routes it belongs with.

**Not:** proxying (`preview_transport.py`) or process inspection (`processes.py`).

### `routes/usage.py`

Provider usage and its cache, operational telemetry, quota series and reset review, and provider accounts.

**Not:** collection (`usage.py`, `provider_accounts.py`).

## Git and landing

### `routes/git.py`

Worktree inventory, the commit graph, provenance, commit changes, diff, repository init, worktree create and remove, worktree session spawn, and path reveal.
Also holds worktree setup orchestration and graveyard-purge scheduling.

**Not:** the removal transaction (`worktree_mutation.py`), which this module only translates to HTTP.

### `routes/land.py`

The land queue, its events, and the verification command's read, write, and approve.

**Not:** running the pipeline (`land_queue.py`) or the approval store (`worktree_verify.py`).

## Agent-facing and streaming

### `routes/agent_ingress.py`

What an agent's own process posts back: `/mcp` and `/api/hooks/{sid}`.
Also holds hook-event payload normalisation, the transcript-backed hook-event set, and both rate-limit windows.

**Not:** the MCP tool surface (`mcp.py`) or observation (`observation.py`).

### `routes/pty.py`

`/pty/{sid}` and `/events`.
Also holds `PtyOutputFlow` credit accounting, terminal-input arbitration and its claim log, repaint scheduling, and client diagnostics.

**Not:** the PTY itself (`pty_host.py`, `supervisor_client.py`) or durable event storage (`history.py`).

## Related

- `daemon-runtime.md` - `server.py`, `app_keys.py`, `http_support.py`, `runtime_config.py`.
- `projects-and-worktrees.md` - `worktree_mutation.py`.
- `processes-and-devices.md` - `preview_transport.py`.
- `pty-and-sessions.md` - `session_media.py`.
