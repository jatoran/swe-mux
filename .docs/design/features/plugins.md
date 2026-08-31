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

## Lifecycle

Lifecycle states distinguish inert acquisition from executable authority:

```text
inspect -> approve -> enable -> disable
   |          |          |
 changed <- update    uninstall -> optional purge
                  \-> rollback -> inspect
```

`swemux plugin link` registers a mutable developer directory and never removes it.
`swemux plugin install` copies a local source or resolves a GitHub repository to an immutable checkout under `<data_dir>/plugins/sources`.
Managed versions use digest-named directories, so a running Windows pane may keep its old cwd while a new version is inspected beside it.
Update installs new content inert unless it is explicitly approved and enabled.
Rollback swaps registry identity back to the previous immutable directory and requires approval again.
Uninstall refuses while a live plugin pane still owns a session, removes managed source, and retains config and state unless purge is separately confirmed.

## Contributions

### Actions

Actions run bounded one-shot commands from the Plugins UI, `swemux plugin action`, or dynamically registered command-palette entries.
The daemon supplies global, Project, session, pane, selection, or worktree context only when the manifest declares that context.
Captured output is capped and recorded in the plugin command ledger.

### Panes

Panes launch through `SessionManager.spawn` as ordinary shell sessions and the existing supervisor `spawn` message.
`SessionRecord.plugin_id`, `plugin_version`, `plugin_entrypoint_id`, and `plugin_placement` retain ownership across layout moves and daemon adoption.
Live plugin panes survive a daemon restart.
Source updates do not rewrite a pane that is already running.
Tab placement enters the focused terminal stack and split placement opens to its right.
The fleet reconciler routes those sessions from their retained `plugin_placement`, so an event-driven refresh cannot race the pane-open response.
Popup placement renders the same session in a modal, is excluded from durable layout reconciliation, and stops the session when the modal closes.

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
The callback endpoint accepts only the token's declared Project, session, terminal, notification, control, or plugin-self permissions.
Tokens are withheld from registry and logs, removed after one-shot commands, and revoked on disablement.
The permission layer prevents accidental control-plane widening and provides audit attribution; it is not represented as an operating-system security boundary.

## Bounds and diagnostics

- One-shot output is capped at 64 KiB per stream.
- Global and per-plugin semaphores bound concurrent commands.
- Each command has a manifest timeout and process-tree cancellation.
- The durable command ledger is capped at 1,000 rows and carries correlation, context, outcome, truncation, and diagnostic fields.
- EventBus backpressure never waits on a plugin command.
- Missing, changed, invalid, or incompatible plugins remain inspectable and cannot block daemon readiness.
- `swemux doctor` reports installed, enabled, degraded, in-flight, token, and EventBus-subscription state.

## Management surfaces

Settings contains a Plugins tab for the global kill switch, link and install, inspection and approval, enablement, update, rollback, uninstall, purge, contributions, command logs, source/config/state paths, and the unreviewed marketplace.
The command palette receives one entry per enabled action and pane plus a stable Manage plugins command.
The `swemux plugin` CLI exposes the same lifecycle and contribution operations for recovery and scripts.

## Marketplace

The marketplace reads the public GitHub `swe-mux-plugin` topic and marks every result unreviewed.
It hosts no executable content and grants no authority.
Selecting a repository only fills the managed-install source; installation still passes through inspection, approval, and enablement.

## Key files

- Manifest model and validation: `src/swe_mux/plugin_manifest.py`
- Registry and command ledger: `src/swe_mux/plugin_store.py`
- Lifecycle, runtime, events, tokens, and marketplace: `src/swe_mux/plugins.py`
- HTTP and callback routes: `src/swe_mux/routes/plugins.py`
- CLI: `src/swe_mux/cli.py`
- Session ownership: `src/swe_mux/models.py`
- Settings UI: `frontend/src/PluginsSettings.tsx`
- Command-palette integration: `frontend/src/App.tsx`
- Terminal link routing: `frontend/src/pluginLinks.ts`, `frontend/src/TerminalPane.tsx`
- Backend tests: `tests/test_plugins.py`
- Frontend link tests: `frontend/test/pluginLinks.test.ts`

## Relates to

- `sessions.md`: plugin panes use ordinary session and supervisor ownership.
- `automation.md`: plugin EventBus commands remain outside canonical Universal hooks.
- `meta-hooks.md`: legacy executable hooks are not the plugin engine.
- `project-actions.md`: plugin actions reuse safe argv ideas but have machine-local plugin trust and identity.
- `mux-mcp.md`: bearer identity and typed operation boundaries inform plugin callback tokens.
- `desktop-shell.md`: plugin data lives outside frozen and installer bundles and survives application updates.
