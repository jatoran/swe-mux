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
The default author convention is one standalone repository per direct child of `~/swe-mux-plugins`.
The Plugins Settings page can configure another absolute development root, create it explicitly, and discover its direct children without linking, approving, or executing them.
`swemux plugin development-root [PATH] [--create]` and `swemux plugin discover` expose the same flow.
`swemux plugin link PATH` remains the explicit escape hatch for repositories outside that root, including monorepos, worktrees, and alternate drives.
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

## Language and runtime support

The host is language-neutral because it launches argv and never imports plugin code.
Current managed distribution is not equally mature for every language because it installs repository content without building it or installing a runtime.

| Plugin implementation | Current managed-install position |
|---|---|
| Shell or PowerShell | Directly runnable when the named shell exists on the host. |
| Python | Directly runnable when an external compatible Python exists and dependencies are either standard-library-only or already available. |
| JavaScript | Directly runnable when a compatible Node or Deno executable exists and the repository needs no install step. |
| TypeScript | Publish compiled JavaScript in the repository; do not assume `tsx`, a package install, or experimental runtime flags exist. |
| Rust, Go, C++, or another compiled language | The process model can launch a binary, but public managed distribution has no artifact matrix yet; committed binaries are not the intended workaround. |

`runtime_requirements` is currently declaration-only metadata.
The host parses and preserves it but does not yet resolve executables or enforce versions before approval, enablement, or invocation.
Authors must test every declared host today, and a missing runtime currently fails at process launch rather than at inspection.
Phase 25 makes runtime enforcement the first expansion step: a versioned requirement grammar, bounded executable/version probes, per-platform applicability, Settings verdicts, and a fail-closed diagnostic without automatic runtime installation.

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
The popup header can promote the live session into a persistent Project tab; later launches focus that retained tab.
Project-scoped pane tools launch from that Project's Run menu or the command palette.
Settings owns global lifecycle and development operations and carries no Project selector or contribution launcher.
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
swemux plugin refresh
swemux plugin approve publisher.plugin
```

Refresh recalculates registered manifest and content digests immediately and rescans the configured development root.
The catalogue also performs the inert digest refresh when the app regains focus.
Changed content is marked changed and prior approval is revoked before new bytes run.
Relinking the same directory is allowed but is not required for ordinary source edits.
Actions and later event deliveries start from approved current bytes.
An existing pane remains its old process until it is closed and relaunched or replaced explicitly with `swemux plugin restart-panes publisher.plugin`.
Pane restart preserves each pane's Project and retained placement and issues a fresh callback token.

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
Installing an existing plugin ID is refused so a repeated install cannot bypass update review.
A literal tag pins one release, a branch is an explicit moving channel, and `--ref latest` resolves the newest GitHub release each time an install or update is requested.
The registry retains requested channel, selected tag or branch, and resolved commit as separate provenance.
`swemux plugin update` reuses that requested channel and stages the result as inert content; `--ref` replaces the stored channel deliberately.
`swemux plugin check-updates` performs only read-only source-channel probes and does not acquire or execute content.
`swemux plugin update ID` downloads a candidate into a separate durable review stage while the active version remains enabled and unchanged.
Settings shows version, revision, compatibility, and permission or capability deltas.
`swemux plugin approve-update ID` is the explicit promotion and approval act; `swemux plugin discard-update ID` abandons the review without changing the active version.
`swemux plugin rollback` restores the retained prior source as inert content.

## Planned compiled artifact contract

Compiled artifact distribution is planned Phase 25 work and is not valid manifest v1 syntax yet.
The intended `[[artifacts]]` matrix selects one immutable release asset by platform and architecture before download:

```toml
# Planned contract, not accepted by the current parser.
[[artifacts]]
platform = "windows"
architecture = "x86_64"
url = "https://github.com/publisher/plugin/releases/download/v1.0.0/plugin-windows-x86_64.zip"
sha256 = "<64 lowercase hexadecimal characters>"
archive = "zip"
executable = "plugin.exe"
```

The future installer must:

- Refuse unsupported or ambiguous platform/architecture matches before the network request.
- Bound download bytes, expanded bytes, file count, path length, and compression ratio.
- Reject absolute paths, traversal, unsafe links, Windows device names and alternate data streams, case-folding collisions, duplicate normalized paths, and unsupported archive entries.
- Extract outside the live source root, verify SHA-256 and the complete manifest, then promote atomically.
- Grant POSIX execute permission only to the declared executable after verification.
- Remove Windows `Zone.Identifier` only from verified staged files and report a specific blocked-execution diagnostic if required.
- Bind approval to the selected artifact digest, security-relevant manifest digest, platform, architecture, archive kind, and executable path.
- Show that a digest proves byte integrity, not publisher honesty, source correspondence, or binary safety.

The public catalog will expose artifact metadata without downloading the asset.
The author template will provide an optional native-host release matrix that builds, packages, hashes, verifies, and attaches every declared artifact to the matching version tag.
Most script plugins should keep the simpler repository path and avoid the artifact contract entirely.

## Future UI boundary

Native React components, CSS, arbitrary DOM, backend routes, middleware, and database migrations are permanently outside the plugin contract.
Hosted web plugins remain deferred until Roadmap Phase 13 ships a non-terminal browser leaf and defines isolated origin, navigation, authentication, storage, lifecycle, and mobile behavior.
That browser leaf is a prerequisite rather than implied authority: future hosted content would be isolated per plugin/version and would not gain native frontend or backend injection.
Dynamic Settings forms and persistent plugin daemons remain separate deferred decisions.

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
- Verify every declared runtime and version on each advertised host; current swe-mux does not enforce `runtime_requirements` for the author.
- Publish a GitHub release whose tag matches the manifest version.
- Add the `swe-mux-plugin` GitHub topic only after the default branch and release tag both contain a valid manifest.

The swemux.dev catalog validates root manifests at exact commits and excludes forks, archived repositories, duplicate IDs, malformed manifests, and unsupported schema versions without executing source.
Official status is a closed repository-to-plugin-ID allowlist maintained by swe-mux.
Every other valid listing is unreviewed community software.
Validation, listing, stars, signatures, and checksums are not a security endorsement.

## Machine-local plugin lab

The primary checkout retains four independent maintained repositories under ignored `.private/plugin-lab/`:

- `fleet-dashboard`: fleet snapshot action and live split dashboard.
- `worktree-auditor`: action, startup restoration, and popup report.
- `session-switchboard`: Project-scoped session list and explicit control in a split.
- `project-link-hub`: clickable repository links in a popup.

Retired `attention-notifier` and `project-scratchpad` repositories remain recoverable under ignored `.trash/plugin-lab-retired/` and are not marketplace examples.
No lab repository is tracked, bundled, or released by the swe-mux repository.
The public repositories are `jatoran/swe-mux-plugin-fleet-dashboard`, `jatoran/swe-mux-plugin-project-links`, `jatoran/swe-mux-plugin-session-switchboard`, and `jatoran/swe-mux-plugin-worktree-auditor`.

## Key files

| File | Responsibility |
|---|---|
| `../../src/swe_mux/assets/plugin-schema.md` | Release-bundled manifest and callback reference. |
| `../../src/swe_mux/plugin_manifest.py` | Canonical manifest parser and content digest. |
| `../../src/swe_mux/plugins.py` | Lifecycle, commands, panes, events, tokens, and marketplace. |
| `../../src/swe_mux/routes/plugins.py` | Management and callback HTTP operations. |
| `../../frontend/src/PluginsSettings.tsx` | Global lifecycle, development discovery, update review, and pane-restart controls. |
| `../../site/tools/plugins.py` | Public exact-revision catalog validation and generation. |
| `../../tests/test_plugins.py` | Host contract coverage. |
