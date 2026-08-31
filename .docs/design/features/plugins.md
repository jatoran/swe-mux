# Plugins

## What it is

Plugins are machine-local, versioned packages that contribute external-process actions, terminal panes, startup hooks, EventBus hooks, and terminal link handlers without modifying the swe-mux source tree or application bundle.
The daemon validates and registers manifests, owns lifecycle and logs, and launches commands through existing subprocess and session primitives.
Third-party code is never imported into the daemon or browser.

## Manifest

The canonical filename is `swe-mux-plugin.toml` and `manifest_version = 1`.
Required metadata is `id`, `name`, `version`, `min_swe_mux_version`, `platforms`, and versioned `requires` capabilities.
Optional metadata includes description, author, license, homepage, architectures, runtime requirements, and scoped callback permissions.

Version-1 host capabilities are:

- `plugin.actions.v1`
- `plugin.panes.v1`
- `plugin.events.v1`
- `plugin.startup.v1`
- `plugin.links.v1`

Every command is an argv array with an optional contained cwd, bounded environment additions, timeout, and platform override.
No command passes through an implicit shell.
Relative executable and cwd paths may not escape the plugin root.

## Repository model

One plugin is one standalone repository with `swe-mux-plugin.toml` at its root.
Plugin source is never part of the swe-mux repository, application bundle, installer, wheel, or release closure.
The primary checkout's ignored `.private/plugin-lab/` is a machine-local smoke lab whose four direct children are the maintained public plugin repositories.
Retired experiments remain recoverable under ignored `.trash/plugin-lab-retired/` and are not part of the active lab or marketplace.
A fresh clone contains no lab plugins and remains complete.
The authoritative agent and author workflow is `../../technical/plugin-authoring.md`.

## Lifecycle

Lifecycle states distinguish inert acquisition from executable authority:

```text
inspect -> approve -> enable -> disable
   |          |          |
 changed <- update    uninstall -> optional purge
                  \-> rollback -> inspect
```

`swemux plugin link` registers a mutable developer directory and never removes it.
`swemux plugin install` copies a local source or resolves a GitHub repository plus optional release channel, branch, or tag to an immutable checkout under `<data_dir>/plugins/sources`.
The registry retains the requested ref, the selected release tag or branch, and the resolved commit separately.
The special `latest` channel resolves the repository's newest GitHub release on each explicit update; a literal tag remains pinned.
Managed versions use digest-named directories, so a running Windows pane may keep its old cwd while a new version is inspected beside it.
Update installs new content inert unless it is explicitly approved and enabled.
Rollback swaps registry identity back to the previous immutable directory and requires approval again.
Uninstall refuses while a live plugin pane still owns a session, removes managed source, and retains config and state unless purge is separately confirmed.

## Contributions

### Actions

Actions run bounded one-shot commands from the Plugins UI, `swemux plugin action`, or dynamically registered command-palette entries.
The caller supplies bounded global, Project, session, pane, selection, or worktree context and the daemon checks that the manifest declares the context kind.
Target identity is not yet derived through the shared Phase 23 operation-boundary authorization service.
Captured output is capped and recorded in the plugin command ledger.

### Panes

Panes launch through `SessionManager.spawn` as ordinary shell sessions and the existing supervisor `spawn` message.
`SessionRecord.plugin_id`, `plugin_version`, `plugin_entrypoint_id`, and `plugin_placement` retain ownership across layout moves and daemon adoption.
The supervisor can keep a live plugin pane process and terminal session alive across a daemon restart.
Callback bearer grants are currently daemon-generation scoped, so a callback-dependent pane must be reopened after reload before it can call swe-mux again.
Source updates do not rewrite a pane that is already running.
Tab placement enters the focused terminal stack and split placement opens to its right.
The fleet reconciler routes those sessions from their retained `plugin_placement`, so an event-driven refresh cannot race the pane-open response.
Popup placement renders the same session in a modal and is excluded from durable layout reconciliation while it remains a popup.
The popup is a responsive terminal host on desktop and mobile and offers `Keep as Project tab` as an explicit promotion into the owning Project's durable layout.
Promotion changes the live session's retained placement to `tab`, closes the modal without stopping the process, and makes later launches focus that tab rather than recreate the manifest's initial popup placement.
Closing an undocked popup stops its session.
Opening any pane contribution closes Settings and focuses the resulting workspace pane or popup.
Each plugin entrypoint is single-instance per Project while its pane is live.
Launching it again focuses the existing pane instead of creating a duplicate.
Plugin utility panes use ordinary terminal input and context menus but omit the agent-oriented command rail.

### Startup hooks

Startup hooks are one-shot bounded commands scheduled after daemon runtime construction.
They restore plugin-owned state and must exit.
They are not supervised daemons and failure never blocks readiness.

### Event hooks

Event hooks subscribe to exact normalized EventBus event names with optional bounded glob matches.
They run only on live events after enablement.
The plugin adapter has its own concurrency, timeout, rate, idempotency, and loop guards and does not widen Universal hook authority or register into legacy meta-hooks.

### Link handlers

Enabled handlers publish validated regular expressions and a same-plugin action ID.
Control-clicking a matching xterm literal or OSC 8 URL invokes the action with session context and the clicked URL.
An unmatched modified click retains the ordinary browser behavior.

## Trust and authorization

Plugins are full-trust same-user programs and are not sandboxed.
Installation review, immutable revisions, and digests communicate and preserve the approved content but do not prove it benign.

Runtime commands receive a revocable `SWEMUX_PLUGIN_TOKEN` plus stable `SWEMUX_PLUGIN_*` paths and context.
The callback endpoint accepts only operations allowed by the token's declared Project-read, session-read, terminal-write, session-control, notification, or plugin-self permissions.
Read permissions currently return the corresponding registered collection rather than a token-bound Project or session subset.
Tokens are withheld from registry and logs, removed after one-shot commands, and revoked on disablement.
The permission layer prevents accidental control-plane widening and provides audit attribution; it is not represented as an operating-system security boundary.

## Bounds and diagnostics

- One-shot output is capped at 64 KiB per stream.
- Global and per-plugin semaphores bound concurrent commands.
- Each command has a manifest timeout and process-tree cancellation.
- The durable command ledger is capped at 1,000 rows and carries correlation, context, outcome, truncation, and diagnostic fields.
- EventBus backpressure never waits on a plugin command.
- Missing, changed, invalid, or incompatible plugins remain inspectable and cannot block daemon readiness.
- Background diagnostics report installed, enabled, degraded, in-flight, token, and EventBus-subscription state.

## Management surfaces

Settings contains a compact Plugins list with inline enablement and uninstall controls.
The app menu's Plugins row and the `plugins.open` command both open that Settings section.
Each row expands for trust details, source/config/state paths, contribution testing, update, rollback, purge, and command logs.
The shared test Project defaults to the Project focused when Settings opened, while its choices are alphabetical.
Project-scoped pane contributions appear in that Project's Run menu, and every enabled contribution remains discoverable in the command palette.
The command palette receives one entry per enabled action and pane plus a stable Manage plugins command.
Popup pane headers expose `Keep as Project tab`; docking preserves the running utility process and focuses its new Project tab.
The `swemux plugin` CLI exposes the same lifecycle and contribution operations for recovery and scripts.
The shipped local-development loop is validate, link, inspect, approve, enable, invoke, inspect logs, disable, uninstall, and relink.
Linked source is author-owned and uninstall never removes its repository.
Managed source, plugin config, and plugin state have separate lifetimes.

## Marketplace

The primary marketplace reads `https://swemux.dev/plugins/catalog.json`.
The catalog builder discovers public GitHub repositories carrying the `swe-mux-plugin` topic, reads no source beyond the root manifest, validates the manifest at an exact commit, and excludes forks, archived repositories, malformed manifests, duplicate plugin IDs, and unsupported manifest versions.
Official listings are an explicit repository-to-plugin-ID allowlist maintained by swe-mux.
Every other valid listing remains unreviewed community software.
Catalog generation executes no plugin code and publishes metadata rather than executable content.

The daemon falls back to the live unreviewed GitHub topic query when the catalog is unavailable.
Selecting a catalog entry fills the repository and immutable release tag; installation still passes through the ordinary acquisition, inspection, approval, and enablement path.
The public `/plugins/` page and the in-app marketplace consume the same generated catalog.

## Key files

- Manifest model and validation: `src/swe_mux/plugin_manifest.py`
- Registry and command ledger: `src/swe_mux/plugin_store.py`
- Lifecycle, runtime, events, tokens, and marketplace: `src/swe_mux/plugins.py`
- HTTP and callback routes: `src/swe_mux/routes/plugins.py`
- CLI: `src/swe_mux/cli.py`
- Session ownership: `src/swe_mux/models.py`
- Settings UI: `frontend/src/PluginsSettings.tsx`
- Popup host and docking control: `frontend/src/PluginPopup.tsx`, `frontend/src/App.tsx`
- Public catalog builder: `site/tools/plugins.py`
- Public catalog page: `site/content/plugins.html`, `site/content/plugins.js`
- Project launch surface: `frontend/src/ProjectRunMenu.tsx`
- Command-palette integration: `frontend/src/App.tsx`
- Terminal link routing: `frontend/src/pluginLinks.ts`, `frontend/src/TerminalPane.tsx`
- Backend tests: `tests/test_plugins.py`
- Frontend link tests: `frontend/test/pluginLinks.test.ts`
- Agent-neutral repository and authoring guide: `.docs/technical/plugin-authoring.md`

## Relates to

- `sessions.md`: plugin panes use ordinary session and supervisor ownership.
- `automation.md`: plugin EventBus commands remain outside canonical Universal hooks.
- `meta-hooks.md`: legacy executable hooks are not the plugin engine.
- `project-actions.md`: plugin actions reuse safe argv ideas but have machine-local plugin trust and identity.
- `mux-mcp.md`: bearer identity and typed operation boundaries inform plugin callback tokens.
- `desktop-shell.md`: plugin data lives outside frozen and installer bundles and survives application updates.
