# Frontend: Agent tab and Activity tab

Index: `../packages.md`.
Design: `../../../design/features/agent-context.md`, `../../../design/features/agent-environment.md`, `../../../design/features/scan-timeline.md`, `../../../design/features/deterministic-consumers.md`.

## Agent tab, Instructions segment

`AgentContextTab.tsx`, `agentContext.ts`

Descriptor-driven Project and global read-only instruction and memory inventory in consistent disclosures, desktop right-click reveal over opaque source IDs, and focus-trapped copy/link management for declared Project instruction-file pairs.
The modal keeps one-time copy as the default, lets the user choose either descriptor file as canonical, previews link replacement, shows active relationships, unlinks by retaining content, explains platform caveats, and exposes typed revision-guarded restore points.
It never accepts paths, edits bodies, writes global instructions or provider memory, selects a canonical file automatically, or synchronizes in the background.

The inventory is held in a bounded module-scoped `INVENTORY_CACHE` keyed by Project, the same shape the sibling Config/Tools segments already use, because this tab is not `keepMounted` and every remount was otherwise a full rescan of every instruction file in front of an empty pane.
A remount draws the last reading and the fetch replaces it; the daemon memoizes its half on a stat signature over the same files.
**`rescan` sends `refresh=1` and bypasses both**, which is what keeps a stat signature honest - it cannot see a same-size rewrite landing in the same nanosecond, and that is exactly when someone presses rescan.

## Agent tab, Config and Tools segments

`AgentEnvironmentTab.tsx`, `agentEnvironment.ts`

Session-scoped passive CLI inspection from `/api/sessions/{id}/agent-environment`: runtime identity, tools, skills, MCP, plugins, hooks, custom agents, policies, features, source drift, and diagnostics.
The surface has no execute, connect, edit, install, or insert action.

`McpServerTools` is the one exception and stays deliberately narrow: a per-row Fetch tools button posting to `/agent-environment/mcp-tools`, rendering the returned catalog under that row with its evidence chip.
It is local state per row rather than part of the shared fetch, because the whole point is that opening the tab probes nothing - folding it into the inventory would start MCP servers for anyone who opened the drawer.
`mcpEvidenceLabel` and `mcpStatusLabel` are pure and tested for distinctness: collapsing two tiers, or rendering "auth required" the same as "no tools", is the failure the labels exist to prevent.

The split is by question rather than by size.
**Config** answers "how is it set up" - runtime block, policies, feature flags, configuration sources, diagnostics.
**Tools** answers "what can it do" - built-in tools, skills, MCP, plugins, hooks, custom agents, with the filter.
The one column they shared made a reader scroll past a hooks inventory to check a model name.

The two are readings of a **single fetch**: a module-scoped cache keyed on session id, both cwds, and the run sequence, so toggling between them is not a round trip for a document the other segment just read, and neither draws a Rescan over the other's stale copy.

Pure helpers own scope, state, completeness, and owner labels, local filtering, and `groupAgentEnvironmentItems`, which builds consecutive runs rather than a keyed map so a section renders in the order the server chose (Hooks: lifecycle order) instead of an alphabetical one of its own.

## Activity tab: Timeline, Findings, Change Map

`FindingsPane.tsx`, `ScanTimelineTab.tsx`, `LazyChangeMap.tsx`, `ChangeMapPane.tsx`, `ProjectContextEditor.tsx`

The three segments are mounted by `UtilityDrawer.tsx` from the segment registry, with no per-tab wrapper component.
Timeline gates on a harness transcript; Findings and Changes do not, so a shell session still reaches its Project findings.

Change Map is the one segment marked `keepMounted`: its layout worker's settled positions are the expensive part, and remounting would re-run the force simulation on every return.
It keeps its pop-out into a workspace tab, which is what makes a graph tolerable in a 380 px column.

Both mount points - the drawer segment and the popped-out pane in `App.tsx` - go through `LazyChangeMap.tsx`, the dynamic boundary for Sigma and Graphology.
Nothing outside this pane uses either, and the pane is only ever mounted by a deliberate act, so importing it statically put a WebGL graph renderer in the entry chunk for every page load - including the phone, where the pane renders lists and draws no canvas at all.
A static import from either host puts it back; `bundleSplit.test.ts` asserts neither has one.
The stand-in shown while the chunk is in flight carries `.change-map-pane` so the drawer does not reflow when the real pane arrives, and holds its caption back a third of a second so a chunk that lands in a frame shows no message.

Everything **Project-wide** - permission, auto-arm, and the user-owned context Markdown via `ProjectContextEditor.tsx` - lives in `ProjectsManager.tsx`; the tab only links there.
The application topbar carries no scan action, and the surface never sends terminal input.

### `FindingsPane.tsx`

The **read-only** human read of the deterministic consumers' annotations over `GET /api/annotations`.

- A session/Project scope toggle defaulting to session.
- Tag-count chips, with the high-volume provenance tag hidden until its chip is opened.
- An always-present notice of what the current scope excludes (the "off versus quiet" rule).
- Rows labelling each finding `deterministic` versus model, with its run id.
- A **source filter** (all / deterministic / observer), drawn only when both kinds are present.
- A footer link to the full Automation dashboard.
It issues no mutating request, which keeps the surface out of the actuation gate.
The source filter exists because this pane is the *only* home for run notes: a second, differently-filtered copy of the same table elsewhere means a note visible in one can be missing from the other.

### `ScanTimelineTab.tsx`

The **session-scoped** half.

- Current-run permission.
- Current and full-session scans, startable and stoppable, with chunk arithmetic on every terminal state.
- Standalone rollover boundaries.
- A one-row budget summary naming the closest-to-binding cap, which expands to all of them.
- The scanner's own reason when scanning is stopped, and a footer indicator for an in-flight scan.
- Records opened at the newest and pinned there.

A record is a **compact row** until it is opened: time, work phase, lifecycle state, one clamped line of summary, and the four flags a collapse must not swallow - `blocked`/`dead end`, which say the run stalled here, and `behind`/`repaired`, which say the record is partial.
Its detail is *mounted only while open*, so expansion is for detail rather than for identifying the row: the asked/intent/claim/blocked fields, the collapsed evidence-target and output-repair disclosures, the lag note when a record was written behind the transcript, novelty/behaviour/confidence, and explicit source expansion.

Three things stay outside that collapse:

- Rollover boundaries, which are landmarks rather than entries.
- The enablement and liveness block in the panel chrome: a budget-stopped scanner and a quiet one both return an empty tail, and only that block separates them.
- The open/closed set itself, which is component state - never server state, and never a device store keyed by unbounded per-run record ids.

`frontend/test/renderer/scan-timeline-rows.spec.ts` pins the row geometry and the phone tap target, because row height is invisible to every unit test.
