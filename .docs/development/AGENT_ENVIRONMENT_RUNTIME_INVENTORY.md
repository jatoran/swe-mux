# Agent Environment Runtime Inventory

## Status

- Active design note and implementation plan.
- Research reviewed 2026-08-08 against current Codex, Claude Code, and oh-my-pi documentation and source.
- **Phases 1-3 shipped 2026-08-21** as the per-server tool catalog in the Agents tab (`src/swe_mux/mcp_tools.py`, `.docs/design/features/agent-environment.md` § MCP tool catalogs). Phase 4 (provider API improvements) remains open.
- Two things the implementation settled differently from the plan below, both because measurement contradicted it, are recorded in § Corrections from implementation.

## Problem

Agent Environment currently performs a bounded passive configuration scan.
That contract is safe and fast, but it cannot enumerate provider-created MCP surfaces or prove which configured tools are available in the running agent.

The visible mismatch is clearest in Codex.
The passive scan finds `node_repl`, `openaiDeveloperDocs`, and the session-injected `mux` configuration, while Codex `/mcp` also reports `codex_apps` and the tools supplied by every MCP server.

Claude Code and oh-my-pi have the same configuration-versus-runtime distinction.
Passive discovery can miss account connectors, plugin or extension contributions, dynamically changed MCP tool lists, runtime gating, authentication state, and connection failures.

## Current contract

- `src/swe_mux/agent_environment.py` reads known provider configuration without starting providers, plugins, or MCP servers.
- `src/swe_mux/harness.py` supplies documented tool catalogs rather than a live provider tool registry.
- `src/swe_mux/agent_launcher.py` injects the session-owned mux MCP configuration and the OMP extension.
- `frontend/src/AgentEnvironmentTab.tsx` presents section completeness and the current Rescan action.
- `.docs/design/features/agent-environment.md` defines the scan as passive and treats the provider CLI as authoritative for runtime availability and health.

That contract should remain the default behavior.
Opening the drawer must not silently start MCP servers, perform network authentication, or create a second provider runtime.

## Provider research

| Provider | Runtime interface | Existing session | Relevant limitation |
|---|---|---:|---|
| Codex | App Server `mcpServerStatus/list` and app APIs | No, when invoked as a sidecar | The snapshot belongs to the probe process rather than the existing TUI process. |
| Claude Code | Agent SDK initialization data and `get_mcp_status()` | No, when invoked as a sidecar | The SDK client cannot attach to an independently launched TUI process. |
| oh-my-pi | Injected extension plus runtime tool registry and `MCPManager` | Yes | Public status collapses several failure and authentication conditions into `disconnected`. |

### Codex

Codex App Server exposes `mcpServerStatus/list` with server, tool, resource, and authentication information.
The request supports `detail: "toolsAndAuthOnly"`.
The app APIs expose installed apps and connector metadata, including surfaces represented by `codex_apps`.
`config/read` exposes effective layered configuration.

`codex mcp list --json` is not sufficient because it reports configured MCP entries rather than the complete runtime inventory shown by `/mcp`.

A short-lived App Server can reproduce a session capability profile when launched with the same executable, provider home, working directory, environment, and CLI overrides.
Its server health is not the health of the already-running Codex TUI.
Hosting the real Codex session through App Server could close that gap, but it would materially change swe-mux's PTY architecture and currently depends on an experimental remote transport.

### Claude Code

`claude mcp list` lists configured servers.
Claude Code `/mcp` additionally reflects connection state, tool counts, plugin MCP servers, Claude.ai connectors, and dynamic `notifications/tools/list_changed` updates.

The Claude Agent SDK exposes the required runtime data.
The initialization system message contains active tool names and MCP server states.
`ClaudeSDKClient.get_mcp_status()` returns server states including `connected`, `failed`, `needs-auth`, `pending`, and `disabled`, with tool and error information.

A sidecar SDK client can match the session capability profile but cannot attach to an independently launched Claude TUI.
Replacing the TUI launch with an SDK-hosted session would be a larger architectural decision and is not justified solely by this drawer feature.

### oh-my-pi

swe-mux already injects an extension into the actual OMP process.
The extension API exposes `getActiveTools()` and `getAllTools()`.
OMP's session API exposes active and complete tool-name inventories and an `MCPManager`.

The current MCP manager can enumerate known servers, configurations, sources, connection state, and tools grouped by MCP server.
This permits exact active-tool reporting from the existing process without another OMP process.

The public manager state distinguishes `connected`, `connecting`, and `disconnected` but does not preserve a complete public snapshot of authentication and failure reasons.
The current OMP RPC command set has no tool-inventory or MCP-status command.
A small upstream `getStatusSnapshot()` API would be the clean long-term way to expose detailed server state without depending on internal manager details.

## Decision

Do not start a Codex App Server or Claude Agent SDK client automatically for every session.
The runtime cost, duplicate MCP processes, network side effects, and misleading process identity are disproportionate to a sidebar inventory feature.

Use a tiered evidence model:

1. Show passive configuration immediately.
2. Add runtime data without another process when the running provider already exposes it to swe-mux.
3. Offer an explicit, cached runtime probe for Codex and Claude when the user needs a richer snapshot.
4. Preserve evidence provenance in the API and UI.

## Evidence model

```ts
type InventoryEvidence =
  | "live_process"
  | "parallel_probe"
  | "passive_config"
  | "swe_mux_owned"

type RuntimeCapabilitySnapshot = {
  provider: "codex" | "claude" | "omp"
  evidence: InventoryEvidence
  generated_at: string
  capability_profile: string
  tools: RuntimeTool[]
  mcp_servers: RuntimeMcpServer[]
  completeness: "complete" | "partial" | "configured_only"
}
```

`live_process` means the running session reported the data.
`parallel_probe` means a separate provider runtime reproduced the same capability profile.
`passive_config` means swe-mux discovered configuration without executing provider code.
`swe_mux_owned` means swe-mux can state the inventory directly because it owns the integration.

The API must not merge these evidence levels into an unqualified `connected` or `available` state.

## Collection plan

### Passive default

- Keep the current bounded passive scan and ten-second request cache.
- Continue to report configured MCP entries as configured rather than connected.
- Preserve the current no-execution safety boundary.
- Describe documented built-ins as documented rather than loaded.

### swe-mux-owned inventory

- Publish the session `mux` MCP server and its tool names from the same definitions used to implement the server.
- Mark the result `swe_mux_owned` rather than pretending it came from provider interrogation.
- Avoid a second source-maintained static list that can drift from the implemented tools.

### OMP live inventory

- Extend the existing injected OMP extension to publish active and complete tool inventories from the running process.
- Include MCP server names, source, connection state, and loaded tools when available through `MCPManager`.
- Publish at session startup and when the runtime tool inventory changes.
- Mark the snapshot `live_process`.
- Treat detailed authentication or failure cause as unknown until OMP exposes a stable status snapshot.

### Codex and Claude runtime probes

- Add one explicit `Refresh runtime` action rather than probing when the drawer opens or when a session starts.
- Launch the provider probe with the matching executable, provider home, account profile, working directory, environment, project configuration, CLI overrides, and enabled plugin set.
- For Codex, call App Server `mcpServerStatus/list` with `toolsAndAuthOnly` and the relevant app inventory methods.
- For Claude, initialize an Agent SDK client and call `get_mcp_status()` without sending a model prompt.
- Terminate the probe reliably after collecting the snapshot.
- Mark every result `parallel_probe` and never describe its connection health as the existing TUI's live health.

## Capability-profile cache

Runtime probes should be cached per effective capability profile rather than per session.
Multiple sessions with the same profile should reuse one snapshot.

The profile fingerprint should include:

- provider;
- CLI executable identity and version;
- account or authentication profile identity without credentials;
- trusted working directory or project-configuration fingerprint;
- global and project configuration fingerprints;
- relevant CLI overrides;
- enabled plugin or extension arguments.

Invalidate the cached snapshot when a fingerprint component changes, the provider version changes, the user explicitly refreshes, or the configured expiration elapses.
A short time-based expiration protects against account-side connector changes that have no local configuration fingerprint.

Concurrent refreshes for the same fingerprint should share one in-flight probe.
Probe failure should leave the passive inventory available and produce a typed diagnostic.

## UI contract

Use explicit evidence labels:

- `Live session`: data from the running process.
- `Runtime probe`: data from a matching but separate provider process.
- `Configured`: passive configuration only.
- `swe-mux owned`: inventory defined and served by swe-mux.

Keep `Rescan configuration` separate from `Refresh runtime` because the operations have different cost and side effects.
The runtime action should explain that it may briefly start configured MCP servers and make network connections.

Do not infer runtime health from a cached passive row.
Do not replace a newer live-process OMP snapshot with a lower-evidence passive snapshot.

## Safety and data handling

- Never store raw MCP headers, environment variables, bearer tokens, cookies, or complete configuration objects returned by provider APIs.
- Whitelist server name, tool name, description, state, source, scope, safe server metadata, and sanitized error category.
- Strip endpoint credentials and query strings using the existing Agent Environment rules.
- Bound tool and server counts and response sizes.
- Apply a strict startup timeout and total probe timeout.
- Ensure cancellation and daemon shutdown terminate sidecar processes.
- Log provider, capability-profile fingerprint, operation, duration, counts, result category, and correlation ID without secrets.

## Rejected approaches

### Automatic probe per session

Rejected because it duplicates provider runtimes and MCP processes for data that is usually shared by many sessions.

### Scraping `/mcp` terminal output

Rejected because it mutates the interactive session, depends on unstable rendering, and competes with user input and transcript ownership.

### Connecting swe-mux directly to configured MCP servers

Rejected because it duplicates provider authentication and policy behavior while still missing Codex apps, Claude.ai connectors, plugin contributions, and provider-specific tool gating.

### Treating sidecar health as live-session health

Rejected because separate runtimes can have different process, network, authentication, and server failure states.

### Rehosting Codex and Claude immediately

Rejected because replacing their PTY-owned TUI sessions with App Server or Agent SDK control planes is a broad architectural change with costs beyond this inventory feature.

## Implementation sequence

### Phase 1: Honest passive inventory

- Preserve current discovery behavior.
- Clarify evidence labels in the API and UI.
- Populate the swe-mux-owned mux tool list from its implementation source.
- Add tests preventing configured entries from being presented as connected.

### Phase 2: OMP live reporting

- Extend the injected OMP extension and hook endpoint with the runtime snapshot payload.
- Persist only the latest bounded snapshot per process generation.
- Merge the live snapshot with passive configuration without losing provenance.
- Test startup, tool changes, extension absence, stale generations, and malformed payloads.

### Phase 3: Optional shared probes

- Implement the common capability-profile cache and in-flight request coalescing.
- Add the Codex App Server collector.
- Add the Claude Agent SDK collector.
- Add explicit runtime refresh and probe diagnostics.
- Test timeout, cancellation, partial results, secret redaction, and cache invalidation.

## Corrections from implementation

Two decisions above did not survive contact with the running providers.

**Claude is dialled directly rather than through an Agent SDK sidecar.**
The plan called for initializing an Agent SDK client and calling `get_mcp_status()`; "Connecting swe-mux directly to configured MCP servers" is listed under Rejected approaches.
The rejection's reasoning still holds and is why the result is labelled `parallel_probe` and never presented as the TUI's state: raw dialling misses account connectors, plugin contributions, and provider-side gating.
What changed is the cost comparison.
An SDK sidecar is a second full agent runtime for a drawer feature, while the official `mcp` v2 client answers `initialize` + `tools/list` over both the legacy and the 2026 stateless protocol revisions in one dependency, and its `ListToolsResult` carries the `ttlMs`/`cacheScope` hints the cache honours.
An HTTP server whose configuration carries credentials is skipped rather than dialled, which is where most of the connector gap actually lands, and it is reported as "auth required / not probed" rather than as an empty catalog.

**A `private` cache scope cannot be part of the cache key.**
The plan's fingerprint list is everything known *before* the question is asked, but `cacheScope` is something the answer tells you.
Keying on it and re-keying afterwards leaves the first reading unreachable under the key the next lookup computes - the reading is stored, and every subsequent request misses it.
The entry therefore records which session collected it, and the sharing check runs on the read: a `private` reading is served only back to its own session, and any other session probes again.
The one case that *is* known in advance stays in the key - an OMP snapshot is one process's live reading by construction, so it is session-scoped from the start.

Two measurements worth keeping:

- `codex app-server` answered `mcpServerStatus/list` with `toolsAndAuthOnly` in 1.7-2.2s against four servers and 89 tools, and `codex_apps` was among them, confirming the gap this feature exists to close.
- That response arrives as a **single** JSON-RPC line, and `toolsAndAuthOnly` still carries every tool's full input and output schema, so it overran asyncio's 64 KiB `StreamReader` default - which raises rather than truncating. The reader is given an explicit 8 MiB limit and treats an overrun as a bounded failure.

### Phase 4: Provider API improvements

- Propose an OMP status snapshot API that retains typed authentication and failure states.
- Reassess Codex and Claude same-process reporting only if their stable control APIs can preserve swe-mux's PTY and session-lifecycle contracts.

## Key files

| File | Responsibility |
|---|---|
| `src/swe_mux/agent_environment.py` | Passive inventory, normalization, completeness, cache, and `resolve_mcp_servers`. |
| `src/swe_mux/mcp_tools.py` | Evidence tiers, collectors, the fingerprint cache, and the live-snapshot store. |
| `src/swe_mux/mcp_contract.py` | The closed mux tool contract the `swe_mux_owned` catalog is checked against. |
| `tests/test_mcp_tools.py` | Tier, sanitization, cache-identity, and probe-failure coverage. |
| `src/swe_mux/agent_launcher.py` | Provider launch arguments and injected session integrations. |
| `src/swe_mux/adapters/omp.py` | OMP-specific integration and generated extension behavior. |
| `src/swe_mux/harness.py` | Provider descriptors and documented capability catalogs. |
| `src/swe_mux/server.py` | Agent Environment API and session-scoped runtime data. |
| `frontend/src/AgentEnvironmentTab.tsx` | Drawer presentation, evidence labels, and refresh actions. |
| `tests/test_agent_environment.py` | Passive discovery and normalization coverage. |
| `tests/test_session_skills_api.py` | Session-scoped Agent Environment API coverage. |

## External references

- [Codex App Server API overview](https://learn.chatgpt.com/docs/app-server#api-overview)
- [Claude Code MCP documentation](https://code.claude.com/docs/en/mcp)
- [Claude Agent SDK Python reference](https://code.claude.com/docs/en/agent-sdk/python)
- [Claude Agent SDK TypeScript reference](https://code.claude.com/docs/en/agent-sdk/typescript)
- [OMP MCP configuration](https://github.com/can1357/oh-my-pi/blob/main/docs/mcp-config.md)
- [OMP SDK documentation](https://github.com/can1357/oh-my-pi/blob/main/docs/sdk.md)
- [OMP RPC documentation](https://github.com/can1357/oh-my-pi/blob/main/docs/rpc.md)
- [OMP extension API types](https://github.com/can1357/oh-my-pi/blob/main/packages/coding-agent/src/extensibility/extensions/types.ts)
- [OMP MCP manager](https://github.com/can1357/oh-my-pi/blob/main/packages/coding-agent/src/mcp/manager.ts)
- [OMP RPC command types](https://github.com/can1357/oh-my-pi/blob/main/packages/coding-agent/src/modes/rpc/rpc-types.ts)
