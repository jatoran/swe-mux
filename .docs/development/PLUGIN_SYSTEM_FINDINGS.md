# Plugin system reference: Herdr evidence and swe-mux decisions

## Status and scope

This document is the Herdr research and architectural decision record for `ROADMAP.md` Phase 25.
Phase 25 owns the remaining public-release and hardening contract.
Current swe-mux behavior lives in `../design/features/plugins.md` and repository workflow lives in `../technical/plugin-authoring.md`.

Herdr evidence was reviewed 2026-08-30 against the plugin and marketplace documentation and the local reference checkout at commit `f7d791eba53d1e72b2318d2d5d038fc0dc8c15dc`.
The shipped swe-mux baseline was reviewed 2026-08-30 against its implementation, tests, live frozen app, and six independent machine-local plugin repositories.

## Herdr's model

Herdr has no embedded plugin interpreter, WASM runtime, dynamic-library interface, or in-process SDK.
A plugin is a directory containing `herdr-plugin.toml` and commands Herdr launches as subprocesses.
Commands use Herdr's existing CLI or socket API rather than a second plugin-only control API.

The manifest exposes six command-backed contribution types:

| Contribution | Herdr behavior |
|---|---|
| `build` | Runs commands during managed installation. |
| `startup` | Runs one-shot commands after restore and API readiness. |
| `actions` | Registers user-invokable commands in global, workspace, tab, pane, or selection context. |
| `events` | Maps a normalized Herdr event name to a command. |
| `panes` | Starts a plugin-owned PTY pane as an overlay, popup, split, tab, or zoomed pane. |
| `link_handlers` | Maps a terminal URL pattern to a declared plugin action. |

Required metadata includes plugin identity, version, minimum Herdr version, and supported platforms.
Commands are argv arrays and do not pass through a shell unless the plugin explicitly launches one.
Runtime commands receive the plugin root, separate config and state directories, invocation context, Herdr binary path, and socket path through environment variables.
Herdr caps captured output, concurrent commands, and retained command logs.

Managed installation clones a GitHub repository, resolves a commit, validates and previews the manifest, asks for confirmation, runs declared build commands, rechecks that the manifest did not change during the build, and atomically installs the checkout with rollback.
Local development uses `plugin link`, which registers a working directory without running build commands.
Enablement, configuration directories, state directories, command logs, uninstall, and reinstall-based updates are global to the current user.

Herdr plugins are full-trust user processes.
Herdr does not sandbox, review, or restrict their filesystem, credential, process, or network access.
Its install preview communicates authority but does not make the code safe.

## Marketplace and community evidence

Herdr's marketplace is an automatic index of public GitHub repositories carrying the `herdr-plugin` topic and at least one parseable `herdr-plugin.toml` on the default branch.
It stores repository metadata and exact manifest metadata, refreshes automatically, and explicitly does not review listings.
Installation still resolves an immutable commit through the ordinary plugin install path.

The official index generated 2026-08-29 reports 872 valid plugin manifests across 856 repositories from 888 public repositories carrying the topic.
It records 23 repositories with no manifest, one invalid manifest, and seven duplicate manifests rather than presenting every self-tagged repository as installable.

Repository stars are a discovery signal, not plugin adoption.
The most-starred cards include large standalone products whose Herdr adapter is one secondary integration: `oh-my-opencode-slim` at 8,503 stars, `terminal-browser` at 2,396, `terminal-code` at 1,748, and `crabbox` at 1,339.
The stronger Herdr-first cohort in the same snapshot is `herdr-reviewr` at 562 stars, `collie` at 561, `herdr-file-viewer` at 495, `herdr-plus` at 271, `herdr-sidebar` at 226, `herdr-annotate` at 212, and `herdr-mirror` at 197.

The recurring high-signal categories are interactive diff review and annotation, file and source-control workbenches, mobile and remote control, declarative workspace launchers, transcript search and memory, disposable remote test environments, worktree bootstrap, CI and pull-request status, and adapters that bring an existing developer tool into a pane.
The strongest plugins are not cosmetic.
They close a repeated loop between an agent session and a concrete human task such as reviewing, resuming, testing, dispatching, or responding.

The community lesson is not merely that extensions exist.
The low-friction combination is an ordinary repository, a small manifest, an existing CLI as the API, a local link workflow, and automatic discovery without central package hosting.

## Herdr limitations swe-mux should not copy

- Install-time build commands execute newly acquired repository code and require the user's machine to carry the author's toolchain.
- Compiled plugins have no first-class prebuilt artifact contract in plugin v1, so a user without Rust, Go, or another required toolchain cannot install them.
- `plugin update` is reinstall rather than a distinct inspected and rollback-capable lifecycle transition.
- Minimum-host-version checks do not replace capability negotiation or a deprecation policy.
- Runtime API access is powerful, and API scoping is still necessary even though an unsandboxed process remains full-trust host code.
- Native non-terminal UI is absent.
  This keeps the host stable, but it means plugin UI is a TUI unless a separately isolated web surface is designed.

## Substrate used by the shipped swe-mux implementation

swe-mux already owns most runtime primitives a plugin host needs:

- `src/swe_mux/event_bus.py` persists normalized events and serves the shared `/api/events` stream.
- `src/swe_mux/automation.py` evaluates canonical Universal hooks, but those rules may only annotate, notify, or invoke a bounded read-only LLM observer.
- `src/swe_mux/meta_hooks.py` retains the older `run`, `http`, `write_pty`, and `notify` actions as an isolated legacy compatibility engine.
  It is not the foundation for new plugin authority.
- `src/swe_mux/project_actions.py` already validates and launches explicit shell and process steps through the ordinary session model.
- The PTY supervisor already accepts executable, argv, cwd, and environment through its existing `spawn` message and preserves the resulting session across daemon restarts.
- The browser already renders ordinary terminal sessions in tabs, splits, and the mobile projection.
- REST, MCP, and the `swemux` CLI already converge on typed daemon operations.
  Phase 23 is completing server-side caller identity so authorization is enforced at the operation boundary rather than in one transport.
- The data directory survives source, frozen-desktop, installer, and PyPI updates.
  Plugin source, config, state, logs, and trust records can therefore remain outside every application bundle.

The shipped plugin host adds packaging, identity, lifecycle, contribution registration, scoped callback authority, and community discovery around those existing primitives.
It adds no new in-process execution runtime.

## swe-mux architectural decisions

### Process and UI boundary

- Third-party code always runs out of process.
- The daemon never imports plugin Python, JavaScript, native libraries, routes, middleware, or database migrations.
- A v1 plugin UI is a terminal/TUI process in an ordinary supervised session.
- Plugins cannot inject React components, CSS, scripts, or arbitrary DOM into the swe-mux application.
- A future hosted web plugin requires a separate isolated-origin or Preview design with explicit navigation and data boundaries.
  It is not implied by plugin v1.

### Host contributions

Plugin v1 exposes five contribution types:

| Contribution | swe-mux contract |
|---|---|
| Action | A manifest-declared command shown in approved command surfaces and invoked with explicit global, Project, session, pane, selection, or worktree context. |
| Pane | A manifest-declared executable opened through the existing session and supervisor spawn path, with plugin ownership retained as metadata. |
| Event hook | An exact normalized EventBus subscription that launches one bounded command under the plugin's separately approved authority. |
| Startup hook | A bounded one-shot restoration command scheduled after daemon runtime construction. |
| Link handler | A validated terminal URL pattern routed to an action declared by the same plugin. |

Event hooks do not become Universal hooks.
Universal hooks intentionally cannot execute commands, and widening them would let repository or model-authored rules cross an existing authority boundary.
The plugin event adapter reuses normalized events, bounded queues, rate limits, loop rejection, process cleanup, and command diagnostics without creating a second condition language or general rules engine.

Install-time build commands, background daemons, native frontend contributions, and arbitrary backend routes are excluded from v1.

### Manifest and compatibility

The canonical filename is `swe-mux-plugin.toml`.
The version-1 shape begins with this contract:

```toml
manifest_version = 1
id = "publisher.plugin"
name = "Plugin name"
version = "0.1.0"
min_swe_mux_version = "0.2.0"
platforms = ["windows", "linux", "macos"]
requires = ["plugin.actions.v1"]
permissions = ["projects.read"]
```

Contribution IDs are local to a globally namespaced plugin ID.
Every executable declaration is an argv array with optional contained cwd and bounded environment additions.
Platform and architecture declarations fail closed before acquisition or invocation.

`min_swe_mux_version` provides a useful message but is not the compatibility authority.
Versioned host capabilities such as `plugin.actions.v1`, `plugin.panes.v1`, `plugin.events.v1`, `plugin.startup.v1`, and `plugin.links.v1` determine whether the installed host can load each contribution.
A missing capability disables the incompatible contribution or plugin with a durable diagnostic and never blocks daemon startup or a core update.

### Callback contract

Runtime commands receive stable `SWEMUX_PLUGIN_*` environment variables for plugin identity, root, config directory, state directory, contribution ID, invocation source, and bounded context JSON.
They receive `SWEMUX_BIN_PATH` and a loopback API address rather than a private module path.

API access uses a revocable runtime token scoped to the manifest permissions approved for that plugin.
The token is bound to local plugin execution, is not stored in plugin state or registry output, and cannot be presented over a tailnet client connection as plugin identity.
The CLI and HTTP transports call the same operation services and enforce the same capability checks.

API permission scopes protect the swe-mux control plane from unnecessary cross-session authority.
They are not represented as an operating-system sandbox and cannot stop a trusted plugin process from reading user files or using the network.

### Acquisition and trust

- Local authors use `swemux plugin link <path>` and build their own working tree.
- Managed installation resolves a repository or release to immutable content before review.
- Acquisition, inspection, approval, enablement, update, disablement, uninstall, and optional state purge are distinct lifecycle transitions.
- Source, config, state, logs, and trust records have separate paths and lifetimes.
- A source, manifest, executable, permission, or capability change invalidates approval before the new bytes can execute.
- Installation and update stage into a temporary directory, reject escaping paths and unsafe links, validate all declared content, then atomically swap with a rollback copy.
- A failed install or update leaves the prior enabled version and all config and state usable.
- Ordinary uninstall removes registration and managed source but retains config and state by default.
  Purge is a separate destructive confirmation.
- Plugin updates are explicit and never coupled to a swe-mux update.

Managed v1 installation does not run repository build commands or package managers.
Script plugins declare the runtimes they require and fail with actionable diagnostics when those runtimes are absent.
Compiled plugins publish platform and architecture artifacts with immutable URLs and SHA-256 digests.
A digest proves integrity, not author trust.

### Resource and failure boundary

- Action and event commands have timeouts, captured-output limits, global and per-plugin concurrency caps, cancellable process-tree ownership, and a bounded durable command log.
- Event delivery has a bounded queue, per-plugin rate limits, recursion depth, same-plugin loop rejection, and idempotency tied to event ID and plugin version.
- Pane processes use the existing supervisor and session lifecycle rather than a new supervisor protocol message.
- Plugin discovery and manifest parsing perform no execution.
- A missing, malformed, incompatible, disabled, crashing, hanging, or flooding plugin degrades only that plugin and remains inspectable.
- Daemon readiness never depends on a plugin command succeeding.

## Public-release community contract

The first marketplace is an unreviewed index of public GitHub repositories carrying the `swe-mux-plugin` topic and a parseable manifest.
Repository cards expose source, license metadata, supported hosts, plugin version, required host capabilities, and the exact indexed commit.
Installation always passes through the same immutable-content inspection and approval flow as a directly entered repository.

The repository currently ships the manifest reference and agent-neutral authoring guide.
Six ignored machine-local repositories prove the contribution model but are not public templates, release artifacts, or CI fixtures.
A public template repository, published examples, compatibility harness, and publishing checklist remain Phase 25 work.
Community plugins remain third-party software and do not inherit swe-mux support, security review, trademark, or release guarantees merely by appearing in the index.

## Expected extension categories

- Developer tools: file viewers, Git dashboards, database consoles, log viewers, test runners, container controls, and deployment panels.
- Agent workflows: team launchers, cross-agent handoff, transcript summarizers, review tools, model or harness routing, and organization-specific agents.
- Event integrations: notifications, worktree bootstrap, session cleanup, ticket creation, external activity records, and verification triggers.
- Service integrations: GitHub, GitLab, Jira, Linear, Sentry, Datadog, Kubernetes, cloud platforms, and private internal systems.
- Organization policy: compliance checks, approved launch profiles, release workflows, onboarding, and repository-specific operational controls.

## Sources

- Herdr plugin reference: <https://herdr.dev/docs/plugins/>
- Herdr marketplace reference: <https://herdr.dev/docs/marketplace/>
- Herdr official marketplace index snapshot: <https://assets.herdr.dev/plugins/index.json>
- Herdr plugin source: <https://github.com/herdrdev/herdr/tree/master/src/app/api/plugins>
- Herdr plugin topic: <https://github.com/topics/herdr-plugin>
- Herdr review workbench: <https://github.com/persiyanov/herdr-reviewr>
- Herdr mobile control: <https://github.com/AltanS/collie>
- Herdr file viewer: <https://github.com/smarzban/herdr-file-viewer>
- Herdr workspace launcher: <https://github.com/cloudmanic/herdr-plus>
- Herdr source-control sidebar: <https://github.com/alexarthurs/herdr-sidebar>
- Herdr annotation workflow: <https://github.com/plannotator/herdr-annotate>
- Herdr remote mirror: <https://github.com/nikok6/herdr-mirror>
- Cross-agent transcript search: <https://github.com/nicosuave/memex>
- Herdr prebuilt-artifact discussion: <https://github.com/herdrdev/herdr/issues/2693>
- swe-mux implementation plan and acceptance contract: `ROADMAP.md` Phase 25
- swe-mux shipped behavior: `../design/features/plugins.md`
- swe-mux repository and authoring workflow: `../technical/plugin-authoring.md`
- swe-mux related design: `../design/features/automation.md`, `../design/features/meta-hooks.md`, `../design/features/project-actions.md`, `../design/features/mux-mcp.md`, `../design/features/sessions.md`, `../design/features/desktop-shell.md`
