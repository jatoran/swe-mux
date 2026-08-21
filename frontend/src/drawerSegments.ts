// The utility drawer's second axis: what a tab is showing, once a tab shows more than
// one thing.
//
// Three surfaces grew segmented controls independently — Insight's Timeline/Findings,
// Git's Map/Log/Provenance, and the Actions tab's collapsible sections — each with its
// own local `useState` and none of them addressable. That cost more than duplication:
//
//  * A segment reached only by clicking has no palette entry and no voice phrase, so
//    folding a tab into a segment of another tab deleted a command and a phrase that
//    the standalone tab had for free (`App.tsx` generates two of each per registered
//    tab). Consolidation without this module is a strict regression for anyone who
//    navigates by name.
//  * Local state is not persisted, so a segment choice died on every remount and could
//    not vary per Project the way the tab selection already does.
//
// So segments are registered here, exactly like tabs are registered in `drawerTabs.ts`,
// and everything else reads the registry: the host draws the control, `App` generates
// the commands, and `drawerLayout.ts` persists the selection per Project.
//
// Two kinds, deliberately kept apart rather than collapsed into one:
//
//  * **segment** — a mutually exclusive view of the tab. Selecting one hides the others.
//    This is the Insight/Git shape.
//  * **section** — a co-visible region of one scroller, reached by scrolling to it and
//    flashing it. This is the Actions shape, and it is what Clipboard became when it
//    stopped being a tab: a section costs no extra click, which is the whole reason a
//    high-frequency insert surface could be folded in at all. `settingReveal.ts` already
//    implements the arrival; this module only names the targets.
//
// Availability is a predicate over a small context of booleans rather than over `Session`,
// for the same reason `drawerVisibility.ts` takes `hasTranscript` instead of a session:
// this module has to stay JSX-free and importable by the node test runner.

// Explicit extension: this module is reachable from the node test runner, whose
// type-stripping ESM loader does not resolve extensionless specifiers.
import { type DrawerTabId } from './drawerTabs.ts'

export type DrawerSegmentKind = 'segment' | 'section'

/** What a segment's availability may depend on. Booleans only, so this module stays pure. */
export type DrawerSegmentContext = {
  /** False on a harness that writes no transcript, and on a shell. */
  hasTranscript: boolean
  /** False when the focused terminal is a shell rather than an agent harness. */
  isAgentSession: boolean
}

export type DrawerSegment = {
  tab: DrawerTabId
  /** Unique within its tab. Persisted, and part of the command id, so treat it as stable. */
  id: string
  kind: DrawerSegmentKind
  /** Drawn in the segmented control. Short enough for three across a 300px column. */
  label: string
  /** Drawn as the pane heading while this segment is selected. Segments only. */
  heading?: string
  /** Tooltip, and the text of the generated palette command. */
  title: string
  /**
   * Keep this segment's body mounted while another segment is selected.
   *
   * Off by default, and it should stay off for almost everything: a hidden body that
   * polls or refetches costs network for a surface nobody is looking at. It is on for
   * Change Map because that pane owns a layout worker and a force simulation whose
   * settled positions are the expensive part — remounting it re-runs the layout on
   * every return, which is exactly the cost the standalone tab did not have.
   */
  keepMounted?: boolean
  available?: (context: DrawerSegmentContext) => boolean
}

const isAgent = (context: DrawerSegmentContext) => context.isAgentSession
const hasTranscript = (context: DrawerSegmentContext) => context.hasTranscript

export const DRAWER_SEGMENTS: DrawerSegment[] = [
  // Actions. Sections rather than segments: everything here ends in text reaching the
  // focused agent, and a mode switch between "the thing I want to send" and "the other
  // thing I want to send" is a click that buys nothing.
  { tab: 'actions', id: 'quick', kind: 'section', label: 'Quick actions', title: 'Quick actions - this device’s configured Drawer layout' },
  { tab: 'actions', id: 'skills', kind: 'section', label: 'Skills', title: 'Skills - what the focused session’s CLI can actually see' },
  { tab: 'actions', id: 'prompts', kind: 'section', label: 'Prompt templates', title: 'Prompt templates - saved reusable messages' },
  { tab: 'actions', id: 'clipboard', kind: 'section', label: 'Clipboard', title: 'Clipboard history - insert a recent copy' },

  // Activity. What this session said it was doing, what the detectors concluded, and
  // what it actually wrote. Three readings of one run, which is why they are one tab.
  { tab: 'activity', id: 'timeline', kind: 'segment', label: 'Timeline', heading: 'Scan Timeline', title: 'Timeline - the scan narration of this session’s run', available: hasTranscript },
  { tab: 'activity', id: 'findings', kind: 'segment', label: 'Findings', heading: 'Findings', title: 'Findings - durable run notes from the detectors and observers' },
  { tab: 'activity', id: 'changes', kind: 'segment', label: 'Changes', heading: 'Change Map', title: 'Changes - what this session edited, and what those edits reach', keepMounted: true },

  // Agent. The three halves of "what is this agent actually running with".
  //
  // Config and Tools read a live harness inventory and are unavailable on a shell;
  // Instructions reads files on disk and is not, so a shell session focused here falls
  // back to it rather than to an empty tab. That fallback is what the old Context tab
  // did by being a separate, Project-scoped tab, and it is preserved by `available`
  // rather than by keeping two tabs.
  { tab: 'agent', id: 'config', kind: 'segment', label: 'Config', heading: 'Agent Configuration', title: 'Config - runtime, policies, feature flags, and the configuration sources behind them', available: isAgent },
  { tab: 'agent', id: 'tools', kind: 'segment', label: 'Tools', heading: 'Tools & Extensions', title: 'Tools - built-in tools, skills, MCP servers, plugins, hooks, and custom agents', available: isAgent },
  // "Instructions" rather than "Context" or "Memory": bounded by Config and Tools it
  // reads as "the prose this agent was given", and learned memory files are auto-loaded
  // instructions in effect. The heading carries the longer, exact name.
  { tab: 'agent', id: 'instructions', kind: 'segment', label: 'Instructions', heading: 'Instructions & Memory', title: 'Instructions - instruction files and learned project memory this agent reads' },

  // Git. Registered rather than left on local state, so the drawer has one mechanism for
  // this idea instead of two. Lifting it also buys "open Git Log" as a voice phrase.
  { tab: 'git', id: 'map', kind: 'segment', label: 'Map', heading: 'Worktree Map', title: 'Map - one row per worktree, with its files, changes, and live sessions' },
  { tab: 'git', id: 'log', kind: 'segment', label: 'Log', heading: 'Commit Log', title: 'Log - the repository’s commit graph' },
  { tab: 'git', id: 'provenance', kind: 'segment', label: 'Provenance', heading: 'Commit Provenance', title: 'Provenance - which session and run produced each commit' },
  // Phase 14. A segment of its own rather than a strip inside Map, for the same
  // watch-here/act-there reason the prompt Queue is separate from the Fleet Queue: Map
  // answers "what is in this worktree", and Land answers "what is happening to it".
  // The act of landing a branch lives on that branch's Map row; this segment is the
  // queue itself, the Project's verification command, and who besides you may start one.
  { tab: 'git', id: 'land', kind: 'segment', label: 'Land', heading: 'Land Queue', title: 'Land - the landing queue, the verification command, and who may start a land' },
]

export const DRAWER_SEGMENT_TABS: readonly DrawerTabId[] =
  [...new Set(DRAWER_SEGMENTS.filter(item => item.kind === 'segment').map(item => item.tab))]

export function drawerSegmentsFor(tab: DrawerTabId, kind?: DrawerSegmentKind): DrawerSegment[] {
  return DRAWER_SEGMENTS.filter(item => item.tab === tab && (!kind || item.kind === kind))
}

export function drawerSegment(tab: DrawerTabId, id: string): DrawerSegment | null {
  return DRAWER_SEGMENTS.find(item => item.tab === tab && item.id === id) ?? null
}

export function drawerSegmentAvailable(segment: DrawerSegment, context: DrawerSegmentContext): boolean {
  return segment.available ? segment.available(context) : true
}

export function availableDrawerSegments(tab: DrawerTabId, context: DrawerSegmentContext): DrawerSegment[] {
  return drawerSegmentsFor(tab, 'segment').filter(item => drawerSegmentAvailable(item, context))
}

/**
 * The segment a tab should actually draw.
 *
 * A stored choice that is unavailable for the focused session falls back to the first
 * available one rather than rendering an empty body — the rule `InsightTab` used to carry
 * inline for Timeline, now applied to every segmented tab. Returns `null` for a tab with
 * no segments, and for the (unreachable by construction, but not by types) case where a
 * segmented tab has nothing available at all.
 */
export function resolveDrawerSegment(
  tab: DrawerTabId,
  requested: string | undefined,
  context: DrawerSegmentContext,
): string | null {
  const available = availableDrawerSegments(tab, context)
  if (!available.length) return null
  if (requested && available.some(item => item.id === requested)) return requested
  return available[0].id
}

/** Whether this tab draws a segmented control at all. */
export function hasDrawerSegments(tab: DrawerTabId): boolean {
  return DRAWER_SEGMENT_TABS.includes(tab)
}

/**
 * The `[data-setting]` value a section marks itself with.
 *
 * Sections reuse `settingReveal.ts` rather than growing a parallel mechanism: it already
 * waits for a node that has not rendered, waits for it to have a layout box, opens the
 * disclosures above it, centres it, and flashes it. The prefix keeps drawer targets from
 * colliding with the Settings namespace that module was written for.
 */
export const drawerSectionTarget = (tab: DrawerTabId, id: string): string => `drawer.${tab}.${id}`
