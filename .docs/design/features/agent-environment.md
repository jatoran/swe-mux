# Agent Environment

## Purpose

Agent Environment is a session-selected, read-only inventory of the Claude Code or Codex CLI behind the focused terminal.
It answers which runtime options, tools, skills, MCP servers, plugins, hooks, agents, policies, and feature overrides can be discovered without changing the CLI.
It is not an execution surface and is not proof that a configured extension connected successfully.

The utility drawer owns a separate **Agent** tab titled **Agent Environment** immediately after Transcript.
The Project-scoped Context tab remains the instruction and memory surface and is titled **Instructions & Memory** in its body.
Commands remains the action surface for inserting a skill or command into the focused terminal.

## Inventory model

Every item keeps three independent axes:

- `scope`: `built_in | managed | user | project | local | session | unknown`;
- `origin`: the CLI, plugin, MCP source, or named configuration layer that supplied it;
- `state`: `documented`, `configured`, `enabled`, `disabled`, `available`, `restricted`, `shadowed`, or `restart_required` as applicable.

Section `completeness` states how the inventory was obtained.
The UI must show that qualifier rather than presenting a passive scan as an exhaustive runtime registry.
In particular, configured MCP servers are not reported as connected, documented built-ins are not reported as loaded, and current skill files newer than the CLI generation are reported as requiring restart.

The response includes the retained launch executable, model and selected CLI options, current trusted working directory, current source metadata, and the CLI process-generation load time.
Configuration source paths are represented by opaque IDs and stable human labels.
`changed_after_start` compares a source mtime with the time the current CLI generation loaded, including a shell-to-agent promotion but not a conversation rollover inside the same process.

## Discovery and safety

`agent_environment.py` performs a bounded passive scan only when the tab or API is requested.
Results are cached for ten seconds per backend, working directory, provider home, launch arguments, model, and process generation.
The CLI version probe is a two-second `--version` invocation cached for one hour.
All blocking work runs outside the aiohttp event loop.

The scan reads only known JSON, TOML, Markdown, skill, command, and plugin-manifest locations.
Each configuration file is capped at 1 MiB, symlinks are refused, Markdown agent discovery is bounded, and each response section is capped at 256 items with an explicit truncation flag.
Malformed and unreadable sources become diagnostics instead of failing the complete inventory.

Opening or refreshing the tab never:

- starts or health-checks an MCP server;
- authenticates a connector;
- imports or executes plugin code;
- executes a hook command or exposes its command, prompt, URL, arguments, environment, or credentials;
- writes provider configuration;
- claims that current files were loaded by the already-running CLI.

MCP endpoints omit credentials and query strings.
Stdio entries expose only the executable basename.
Policy values are limited to known non-secret keys and structured collections are summarized by count.

## Surface

The header shows the selected session, backend and version, plus an explicit Rescan control.
Runtime identity is followed by compact counts and a warning when configuration changed after load.
Sections are collapsed disclosures with item count and completeness label.
Policies open by default; a local substring filter opens matching sections and searches item identity, origin, scope, state, description, and safe metadata.

Configuration sources and diagnostics are separate disclosures below the capability sections.
The tab has no insert, send, enable, disable, connect, edit, or install action.
Shell sessions render a typed ordinary empty state and the endpoint returns `409`.

## Boundaries

Agent Context owns Project/global instruction bodies, learned memory, and deliberate root-instruction synchronization.
Agent skills owns the invocable skill inventory reused by Commands.
Agent Environment composes that skill discovery with passive runtime and extension metadata but does not return skill filesystem paths in its own normalized sections.
The provider CLI remains authoritative for actual runtime tool availability and connection health.
