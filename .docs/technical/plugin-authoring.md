# Plugin authoring and repository model

This page is the implementation-facing guide for humans and coding agents creating or maintaining swe-mux plugins.
Read `../design/features/plugins.md` first for the product and trust boundaries.
Print the release-matched manifest and callback reference with `swemux plugin schema`.

## Repository boundary

One plugin is one ordinary repository with `swe-mux-plugin.toml` at its root.
The plugin repository owns its manifest, source, tests, README, license, dependency metadata, and release artifacts.
A plugin must not require a swe-mux fork, a patch under `src/` or `frontend/`, or files copied into the application bundle.
A plugin must not import swe-mux Python or frontend modules.
Its supported integration surface is the manifest, runtime environment, loopback callback endpoint, and ordinary terminal behavior.

The primary checkout may contain an ignored `.private/plugin-lab/` directory for machine-local development.
Every direct child of `.private/plugin-lab/` is an independent Git repository rather than a subdirectory of the swe-mux repository.
The parent swe-mux repository never stages, commits, publishes, packages, or releases those plugin files.
The local lab is evidence and a smoke-test surface, not the public example distribution.
A fresh clone is correct without it.

Linked source remains author-owned.
`swemux plugin link` registers the directory in place and `swemux plugin uninstall` never removes it.
Managed source is copied beneath the swe-mux data directory and may be removed by uninstall.
Config and state have separate lifetimes and survive ordinary uninstall unless the operator explicitly requests purge.

## Agent rules

- Make a plugin change in that plugin's repository, not on the swe-mux branch.
- Do not couple one plugin repository to another local plugin repository.
- Do not use `.private/plugin-lab` paths in a manifest, source file, or published README.
- Do not write plugin config or state into the plugin source tree or a user's Project.
- Read `SWEMUX_PLUGIN_CONFIG_DIR` and `SWEMUX_PLUGIN_STATE_DIR` at runtime.
- Do not commit runtime caches, generated state, command output, bearer tokens, local paths, or test screenshots.
- Keep commands cross-platform when the manifest advertises more than one platform.
- Use argv arrays and direct executables.
  Do not hide shell parsing inside a command string.
- Declare every runtime, executable, host capability, callback permission, context, platform, and architecture the plugin needs.
- Treat all context and callback results as untrusted input and bound rendered or persisted content.
- Never print or persist `SWEMUX_PLUGIN_TOKEN` or inherited credentials.
- Use the primary checkout only for live daemon and browser validation because the daemon, port, and data directory are singletons.
  Plugin source may live anywhere, but a worktree must not start or redeploy a second app.
- Test disable, re-enable, uninstall, and relink behavior before calling a plugin complete.

## Repository anatomy

```text
publisher-plugin/
├── swe-mux-plugin.toml
├── README.md
├── LICENSE
├── plugin.py
├── test_plugin.py
└── .gitignore
```

Only `swe-mux-plugin.toml` has a reserved name.
The command may target Python, JavaScript, PowerShell, a compiled executable, or another runtime available on the declared host.
Managed installation does not run a package manager or build step, so published plugins must be directly runnable after acquisition.

## Minimal manifest

```toml
manifest_version = 1
id = "publisher.plugin"
name = "Plugin"
version = "0.1.0"
min_swe_mux_version = "0.1.5"
description = "One precise sentence."
author = "Publisher"
license = "MIT"
platforms = ["windows", "linux", "macos"]
requires = ["plugin.actions.v1"]
permissions = ["projects.read"]
runtime_requirements = ["python>=3.10"]

[[actions]]
id = "inspect"
title = "Inspect Project"
description = "Print a bounded Project report."
contexts = ["project"]
command = ["python", "plugin.py", "--inspect"]
timeout_seconds = 30
```

Plugin IDs are globally namespaced and contribution IDs are unique within their family.
Changing any source byte or security-relevant manifest field invalidates approval and disables execution until the current content is approved again.

## Contribution selection

| Contribution | Use it for | Do not use it for |
|---|---|---|
| Action | Bounded explicit work that exits and returns capped output. | Interactive or indefinite work. |
| Pane | Interactive terminal or TUI work in a tab, right-hand split, or popup. | React, CSS, DOM, or backend injection. |
| Event hook | Bounded automatic reaction to an exact normalized event. | A persistent daemon or a second rules engine. |
| Startup hook | One bounded restoration pass after plugin enablement or daemon startup. | A hidden service. |
| Link handler | Route a matched terminal URL to an action in the same plugin. | Arbitrary browser scripting or navigation authority. |

Pane placement is `tab`, `split`, or `popup`.
Tab and split panes are ordinary supervised sessions and enter Project layout state.
Popup panes are overlay-owned sessions and stop when the popup closes.
Opening a pane from Settings closes Settings and focuses the new surface.
Opening the same entrypoint again in the same Project focuses its existing live pane.
Utility panes do not receive the agent command rail; the TUI owns its own compact controls.

## Runtime contract

Every command receives:

- `SWEMUX_PLUGIN_ID`
- `SWEMUX_PLUGIN_VERSION`
- `SWEMUX_PLUGIN_ROOT`
- `SWEMUX_PLUGIN_CONFIG_DIR`
- `SWEMUX_PLUGIN_STATE_DIR`
- `SWEMUX_PLUGIN_CONTRIBUTION_KIND`
- `SWEMUX_PLUGIN_CONTRIBUTION_ID`
- `SWEMUX_PLUGIN_CONTEXT_JSON`
- `SWEMUX_PLUGIN_TOKEN`
- `SWEMUX_API_URL`
- `SWEMUX_BIN_PATH`

The callback request is `POST $SWEMUX_API_URL` with `Authorization: Bearer $SWEMUX_PLUGIN_TOKEN` and a JSON body containing `operation`.

| Operation | Permission | Result or effect |
|---|---|---|
| `projects.list` | `projects.read` | Registered Project snapshots. |
| `sessions.list` | `sessions.read` | Session snapshots with retained spawn environment removed. |
| `terminal.write` | `terminal.write` | Exact bytes written to one available session. |
| `session.stop` | `sessions.control` | Explicitly stops one session. |
| `notify` | `notifications.write` | Emits a plugin-attributed notification event. |
| `self.describe` | `plugins.self` | Current plugin identity, permissions, contribution, and pane session ID. |

Callback permissions limit cooperative swe-mux API access.
They are not an operating-system sandbox and do not restrict the plugin's filesystem, process, credential, or network access.

Runtime tokens for actions, events, and startup hooks expire with the command.
Pane tokens are scoped to the pane session but are currently held only by the daemon generation that opened the pane.
The supervised pane process can survive a daemon reload, but its old callback token is not restored.
A callback-dependent pane must tolerate temporary connection refusal and should tell the operator to close and reopen it after a daemon reload.
Do not claim transparent callback continuity until token grants have durable, secret-safe recovery coverage.

## Local development workflow

Run these commands from the plugin repository or pass its absolute path:

```text
swemux plugin validate .
swemux plugin link .
swemux plugin approve publisher.plugin
swemux plugin list
```

Validation and linking execute no plugin command.
Validation runs locally through the release-matched canonical manifest parser and does not require a daemon.
Approval enables current content by default.
Use `swemux plugin approve publisher.plugin --no-enable` when approval and enablement must remain separate.

Invoke contributions with stable IDs:

```text
swemux plugin action publisher.plugin inspect --project PROJECT_ID
swemux plugin pane publisher.plugin dashboard --project PROJECT_ID
swemux plugin logs --plugin-id publisher.plugin
```

Users normally open Project-scoped pane tools from that Project's Run menu or the command palette.
Settings is the management and diagnostic surface, not the primary launcher.

After editing linked source:

```text
swemux plugin validate .
swemux plugin list
swemux plugin approve publisher.plugin
```

The catalogue detects the changed content digest, marks the plugin changed, and revokes prior approval before new bytes run.
Relinking the same directory is allowed but is not required for ordinary source edits.

Exercise lifecycle isolation:

```text
swemux plugin disable publisher.plugin
swemux plugin enable publisher.plugin
swemux plugin uninstall publisher.plugin
swemux plugin link .
swemux plugin approve publisher.plugin
```

Use `--purge` only when the user explicitly wants the plugin's config and state removed.
Never use purge as test cleanup against user-owned state.

## Managed installation and publishing

`swemux plugin install` accepts a local directory, GitHub `owner/repository`, or Git URL plus optional `--ref`.
Managed acquisition copies immutable content and leaves it inert unless approval and enablement are explicit.
A literal tag pins one release, a branch is an explicit moving channel, and `--ref latest` resolves the newest GitHub release each time an install or update is requested.
The registry retains requested channel, selected tag or branch, and resolved commit as separate provenance.
`swemux plugin update` reuses that requested channel and stages the result as inert content; `--ref` replaces the stored channel deliberately.
`swemux plugin rollback` restores the retained prior source as inert content.

Before publishing:

- Start from a clean standalone repository.
- Add a license and a README that states authority, runtimes, platforms, permissions, state paths, and destructive operations.
- Validate from a path containing spaces.
- Run unit tests without a daemon for parsing, filtering, formatting, and command safety.
- Link to a disposable data directory or live development daemon and exercise every contribution.
- Confirm changed source revokes approval.
- Confirm disablement removes contributed surfaces.
- Confirm uninstall does not remove linked source and retains state.
- Confirm no secret or absolute machine path appears in source, logs, fixtures, or output.
- Run repository CI on Windows, Linux, and macOS with the release-matched host validator.
- Publish a GitHub release whose tag matches the manifest version.
- Add the `swe-mux-plugin` GitHub topic only after the default branch and release tag both contain a valid manifest.

The swemux.dev catalog validates root manifests at exact commits and excludes forks, archived repositories, duplicate IDs, malformed manifests, and unsupported schema versions without executing source.
Official status is a closed repository-to-plugin-ID allowlist maintained by swe-mux.
Every other valid listing is unreviewed community software.
Validation, listing, stars, signatures, and checksums are not a security endorsement.

## Machine-local plugin lab

The primary checkout retains six independent repositories under ignored `.private/plugin-lab/`.
Four are maintained public official plugins:

- `fleet-dashboard`: fleet snapshot action and live split dashboard.
- `worktree-auditor`: action, startup restoration, and popup report.
- `session-switchboard`: Project-scoped session list and explicit control in a split.
- `project-link-hub`: clickable repository links in a popup.

`attention-notifier` and `project-scratchpad` remain unpublished local experiments and are not marketplace examples.
No lab repository is tracked, bundled, or released by the swe-mux repository.
The public repositories are `jatoran/swe-mux-plugin-fleet-dashboard`, `jatoran/swe-mux-plugin-project-links`, `jatoran/swe-mux-plugin-session-switchboard`, and `jatoran/swe-mux-plugin-worktree-auditor`.

## Key files

| File | Responsibility |
|---|---|
| `../../src/swe_mux/assets/plugin-schema.md` | Release-bundled manifest and callback reference. |
| `../../src/swe_mux/plugin_manifest.py` | Canonical manifest parser and content digest. |
| `../../src/swe_mux/plugins.py` | Lifecycle, commands, panes, events, tokens, and marketplace. |
| `../../src/swe_mux/routes/plugins.py` | Management and callback HTTP operations. |
| `../../frontend/src/PluginsSettings.tsx` | Browser lifecycle and contribution controls. |
| `../../site/tools/plugins.py` | Public exact-revision catalog validation and generation. |
| `../../tests/test_plugins.py` | Host contract coverage. |
