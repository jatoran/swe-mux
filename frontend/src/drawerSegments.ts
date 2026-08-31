// The utility drawer's second axis: what a tab is showing, once a tab shows more than
// one thing.
//
// Three surfaces grew segmented controls independently — Insight's Timeline/Findings,
// Git's Map/Log/Provenance, and the Actions tab's named views - each with its
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
  { tab: 'actions', id: 'skills', kind: 'section', label: 'Skills', title: 'Skills - what the focused session’s CLI can actually see' },
  { tab: 'actions', id: 'prompts', kind: 'section', label: 'Prompts', title: 'Prompt templates - saved reusable messages' },
  { tab: 'actions', id: 'clipboard', kind: 'section', label: 'Clipboard', title: 'Clipboard history - insert a recent copy' },

  // Activity. What this session said it was doing, what the detectors concluded, and
  // what it actually wrote. Three readings of one run, which is why they are one tab.
  { tab: 'activity', id: 'timeline', kind: 'segment', label: 'Timeline', title: 'Timeline - the scan narration of this session’s run', available: hasTranscript },
  { tab: 'activity', id: 'findings', kind: 'segment', label: 'Findings', title: 'Findings - durable run notes from the detectors and observers' },
  { tab: 'activity', id: 'changes', kind: 'segment', label: 'Changes', title: 'Changes - what this session edited, and what those edits reach', keepMounted: true },

  // Agent. The three halves of "what is this agent actually running with".
  //
  // Config and Tools read a live harness inventory and are unavailable on a shell;
  // Instructions reads files on disk and is not, so a shell session focused here falls
  // back to it rather than to an empty tab. That fallback is what the old Context tab
  // did by being a separate, Project-scoped tab, and it is preserved by `available`
  // rather than by keeping two tabs.
  { tab: 'agent', id: 'config', kind: 'segment', label: 'Config', title: 'Config - runtime, policies, feature flags, and the configuration sources behind them', available: isAgent },
  { tab: 'agent', id: 'tools', kind: 'segment', label: 'Tools', title: 'Tools - built-in tools, skills, MCP servers, plugins, hooks, and custom agents', available: isAgent },
  // "Instructions" rather than "Context" or "Memory": bounded by Config and Tools it
  // reads as "the prose this agent was given", and learned memory files are auto-loaded
  // instructions in effect. The heading carries the longer, exact name.
  { tab: 'agent', id: 'instructions', kind: 'segment', label: 'Instructions', title: 'Instructions - instruction files and learned project memory this agent reads' },

  // Files. The tree and the Recent list are two readings of the same Project, and Recent
  // was a pressed icon inside the search row until now - a mode with no name anywhere in
  // the chrome, which is exactly the shape this registry exists to replace. As a segment it
  // is addressable ("open Recent"), it persists per Project like every other view choice,
  // and the reader can see which of the two they are in without inspecting a toggle's
  // pressed state. The labels are the headings rather than shorter chips: "File Explorer"
  // is what this surface is called everywhere else, and abbreviating it here to fit a chip
  // would rename it for no reason.
  { tab: 'files', id: 'explorer', kind: 'segment', label: 'File Explorer', title: 'File Explorer - browse and search this Project’s tree' },
  { tab: 'files', id: 'recent', kind: 'segment', label: 'Recent', title: 'Recent - what Git says was touched here, uncommitted work first' },

  // Git. Registered rather than left on local state, so the drawer has one mechanism for
  // this idea instead of two. Lifting it also buys "open Git Log" as a voice phrase.
  { tab: 'git', id: 'map', kind: 'segment', label: 'Map', title: 'Map - one row per worktree, with its files, changes, and live sessions' },
  { tab: 'git', id: 'log', kind: 'segment', label: 'Log', title: 'Log - the repository’s commit graph' },
  { tab: 'git', id: 'provenance', kind: 'segment', label: 'Provenance', title: 'Provenance - which session and run produced each commit' },
]

/**
 * A segment that no longer exists, and where asking for it lands now.
 *
 * The registry's whole reason for existing is that folding a surface into another one
 * must not delete a palette entry or a voice phrase (`App.tsx` generates both per
 * registered segment). Retiring one has the same hazard in reverse: "open Land" is a
 * navigation path someone learned, and dropping the entry would silently stop answering
 * a phrase that used to work.
 *
 * So a retirement is a **row here rather than a deletion**, and the rows stay forever -
 * exactly like `migratedTabTarget` in `drawerLayout.ts` and `_COMMAND_MIGRATIONS` in
 * `keybindings.py`, which are the same idea for a retired tab id and a retired keybinding.
 * `landsOn` must name a live segment of the same tab, so the phrase reaches the surface
 * that absorbed it rather than the tab's first segment.
 */
export type RetiredDrawerSegment = {
  tab: DrawerTabId
  /** The retired id. Still the last part of the command id, permanently. */
  id: string
  /** What it was called. Still the command's label and the voice phrase's noun. */
  label: string
  title: string
  /** The live segment id that answers for it now. */
  landsOn: string
}

export const RETIRED_DRAWER_SEGMENTS: RetiredDrawerSegment[] = [
  // Phase 14 shipped Land as a fourth Git reading, on the watch-here/act-there split the
  // prompt Queue has with the Fleet Queue. That split did not survive contact: the act
  // of landing belongs on the row showing the diff behind it, and once it moved there
  // the segment held one Project-wide block - the verification command, the grants, the
  // queue - which is now a compact strip at the head of Map. Landing is one surface.
  {
    tab: 'git',
    id: 'land',
    label: 'Land',
    title: 'Land - the landing strip at the head of the worktree map',
    landsOn: 'map',
  },
]

/**
 * The live segment id a stored or spoken one means now.
 *
 * Applied to a persisted selection as well as to a command, because the two are the same
 * question asked at different times. Falling through `resolveDrawerSegment`'s
 * first-available fallback would land on the right surface here by luck rather than by
 * record, and would not survive Map ceasing to be Git's first segment.
 */
export function migratedDrawerSegment(tab: DrawerTabId, id: string): string {
  const retired = RETIRED_DRAWER_SEGMENTS.find(item => item.tab === tab && item.id === id)
  return retired ? retired.landsOn : id
}

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
 * Tabs whose segments are three readings of one component, not three components.
 *
 * The default shape mounts one body *per segment*, so switching segments unmounts the
 * one you left. That is right when the segments are unrelated panes. It is wrong for
 * Git: Map, Log, and Provenance are one component reading one repository, they share a
 * refresh listener, a comparison ref, a land queue, and a commit cache, and mounting
 * them separately meant switching to Log and back threw away the map, the reader's
 * expanded worktree, and the filter they had typed — then paid for all of it again.
 *
 * `keepMounted` is not the fix here and would be the wrong one: it would leave all
 * three instances alive, each with its own `git_changed` listener, and turn one refresh
 * into three. One mount taking the active segment as a prop is what the component
 * already expects — `GitTab` has always switched on a `view` prop internally.
 */
const SHARED_SEGMENT_BODY_TABS: DrawerTabId[] = ['git']

export function hasSharedSegmentBody(tab: DrawerTabId): boolean {
  return SHARED_SEGMENT_BODY_TABS.includes(tab)
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
