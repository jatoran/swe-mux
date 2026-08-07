# Plugins: what herdr does and what swe-mux would need

A findings record, not a plan.
Reviewed 2026-08-06 against the herdr reference checkout and `src/swe_mux/`.
Nothing here is scheduled.

## herdr's design, in one line

**There is no plugin runtime.** No embedded interpreter, no WASM, no dynamic library, no in-process
API. Every extension point is `command: Vec<String>`, a subprocess. That is why the whole subsystem
is about 8,300 lines.

Three things are unified rather than built twice:

- **The plugin API is the agent API is the CLI.** A plugin subprocess receives `HERDR_BIN_PATH` (the
  herdr binary) and `HERDR_SOCKET_PATH`, so it drives the app through the same commands agents and
  humans use. There is no separate plugin API to design, version, or keep in sync.
- **Plugin events are the socket API's events.** `[[events]] on = "worktree.created"` subscribes to
  the same 26-member `EventKind` vocabulary (`src/api/schema/events.rs:225`). No second bus.
- **A plugin pane is a PTY running argv**, placed in the layout (`src/app/api/plugins/panes.rs`).
  A plugin UI is a TUI program. No widget API, no rendering surface.

## The manifest

`herdr-plugin.toml`, six extension points, every one a subprocess, each `platforms`-gated:

| Point | What it does |
| --- | --- |
| `build` | commands run at install time |
| `startup` | commands run when herdr starts |
| `actions` | user-invokable; `contexts` = global / workspace / tab / pane / selection |
| `events` | `on = "<event>"` maps to a command |
| `panes` | plugin-owned pane; `placement` = overlay / popup / split / tab / zoomed |
| `link_handlers` | regex `pattern` over terminal text maps to an action |

**Invocation contract** (`src/app/api/plugins/runtime.rs:39-81`) is environment variables:
`HERDR_ENV=1`, `HERDR_PLUGIN_ID`, `HERDR_PLUGIN_ROOT` / `_CONFIG_DIR` / `_STATE_DIR` (auto-created
per plugin), `HERDR_PLUGIN_CONTEXT_JSON`, plus per-invocation `HERDR_PLUGIN_ACTION_ID`,
`HERDR_PLUGIN_EVENT`, `HERDR_PLUGIN_EVENT_JSON`, `HERDR_WORKSPACE_ID` / `TAB_ID` / `PANE_ID`,
`HERDR_PLUGIN_CLICKED_URL`, `HERDR_PLUGIN_LINK_HANDLER_ID`.

**Bounds**: 64 KiB output cap, 32 concurrent commands, a 200-entry command log, cwd is the plugin
root.

**Install** (`src/cli/plugin.rs:193`): git checkout to temp, parse manifest, print preview,
interactive `y/N`, run build commands, `ensure_manifest_unchanged_after_build`, atomic rename with
a rollback backup. That post-build manifest recheck is the one real control: a build script cannot
rewrite the manifest to grant itself entrypoints the user never saw in the preview.

**There is no sandbox.** Plugins run with full user privileges; trust is the install confirmation
plus the git source.

## swe-mux already has more substrate than herdr

- An event bus with roughly 60 distinct event types and `/api/events`. herdr has 26.
- 169 REST endpoints, a richer control API than herdr's socket API.
- **`meta_hooks.py` already is herdr's `events` extension point**: TOML rules, `match` patterns
  against events, `action.kind` in `{notify, write_pty, run, http}`, per-rule rate limits, template
  validation, a delivery log, and a reload watcher.
- `automation.py` - a rules engine with batches, dry-run, dashboards, and LLM actions.
- `project_actions.py` - trusted task imports with shell/process steps, already parsing VS Code
  tasks and package scripts. Most of `actions` minus the registration surface.
- MCP with per-session bearer tokens - the callback-auth pattern is already designed and shipped.

## What is actually missing

1. **Packaging and identity.** A plugin as a versioned installable unit with an id, config dir,
   state dir, enable/disable, and an install/update lifecycle. Today's meta-hooks are one flat TOML
   file with no owner.
2. **`actions` contributed to the UI by a third party.** The command rail and utility drawer are
   the natural home; nothing can currently contribute to them.
3. **`panes`.** Nothing lets a non-agent process claim a pane by declaration.
4. **`link_handlers`.**
5. **A documented callback contract**: environment variables plus a scoped token.

## Constraints specific to swe-mux

- **Subprocess only. Never in-process Python.** A third-party module imported into the daemon means
  a plugin exception takes down observation for every live session. swe-mux needs this more than
  herdr does, because its daemon owns strictly more shared state.
- **Plugin panes must ride the existing supervisor `spawn` message.** A new supervisor message type
  forces a `PROTOCOL_VERSION` bump, which reaps every live session. A plugin pane is exe + argv +
  cwd + env, so this is achievable, but only if it is a constraint from the start.
- **The frozen desktop app cannot assume a toolchain.** herdr assumes git and a dev machine.
  "Plugin is any executable" survives that; "plugin is a Python module we import" does not.
- **Tailscale, not a local socket.** herdr's plugin trust rests on a unix socket only the local user
  can reach. swe-mux is reachable over the tailnet, so a plugin token needs the same same-host
  scoping already decided for MCP.
- **Do not build a second event-to-action path.** Plugins should register *into* meta-hooks and
  automation, not parallel them, or there will be two rules engines with two rate limiters and two
  delivery logs.

## Value ranking, if it is ever picked up

herdr's six extension points are not equally worth having here:

1. **`panes`** - highest value, lowest cost. swe-mux is a pane multiplexer with a browser UI, tabs,
   splits, and a drawer, so it has more places to put a plugin pane than herdr does, and the spawn
   path already exists.
2. **`actions`** - the command rail and drawer are the obvious home, and `project_actions.py`
   already models steps.
3. **Packaging over the existing hooks** - wrap `meta_hooks.py` in plugin identity rather than
   rebuilding it. Best value-to-effort, because it turns an unowned TOML file into distributable
   units.
4. **`link_handlers`** - useful in a terminal, moderate cost.
5. **`build` and `startup`** - skip. Running arbitrary build commands on a user's machine is the
   riskiest part of herdr's design and is precisely what forces `ensure_manifest_unchanged_after_build`.
   "Plugin is a prebuilt executable or a script" avoids the whole category.
