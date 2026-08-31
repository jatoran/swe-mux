// The right-edge utility drawer: tab registry, persistence, and pure helpers.
//
// One singleton host per tab, with two responsive projections. On mobile it is an
// overlay drawer entering from the right edge. On desktop it is an in-flow recursive
// split tree next to an always-visible launcher rail, so it never covers the Project
// workspace.
//
// Tab order: the *navigators* lead (Notes, then Files), then the session-scoped block,
// then the remaining Project-scoped surfaces, with Notifications last.
//
// Notes and Files are project-scoped indexes over documents — narrow-column surfaces
// that used to cost a permanent workspace tab each. Both host what you pick and open
// into a pane only on request, because reading a note or a file without leaving the
// session on screen is what the tabs are for on a phone. Files was the last surface
// that did not: it inserted a leaf into the shared layout, so opening a file on a
// phone rearranged the desktop's panes (`drawerFiles.ts`). They lead because they are
// the two surfaces that are useful before a
// single session exists — a fresh workspace has notes to write and files to open while
// every session tab still has nothing to act on — and because they are the pair reached
// most often mid-work without reference to the focused terminal.
//
// The session block keeps its internal order: first the surfaces that *inject into the
// focused session* (Actions and the prompt queue), then the passive session surfaces —
// Transcript reads the focused conversation, Activity reads what the run did, and Agent
// inventories the CLI environment it did it with. Git reads the repository behind the
// Project rather than opening anything, so it is not a navigator; it follows the session
// block with Processes and Schedule beside it. Notifications is the one application-wide
// fleet view that earns a permanent tab, and stays last.
//
// Queue closes the injection block, which is where it belongs and why it is here rather
// than in a workspace tab or a modal: deciding whether to send is a judgement about the
// agent's live state, and that state is only legible in the terminal. A pane leaf replaces
// the terminal, a modal covers it; the docked column is the one placement that keeps the
// target and the control on screen together.
//
// Three former tabs are now segments or sections of their neighbours
// (`drawerSegments.ts`), on the rule that a low-frequency *inspection* surface can afford
// one more click while an *injection* surface cannot:
//
//  * Clipboard is a section of Actions. Both put text into the focused agent — the same
//    verb, the same `onInsert`/`onDone` — and a section is co-visible rather than a mode,
//    so the tightest merge available also costs no extra click on the one surface that
//    could least afford one.
//  * Change Map is Activity's third segment. Insight reported what a session said it was
//    doing and Change Map reported which files it actually wrote; those are two readings
//    of one run and were never two questions. The graph still wants more width than this
//    column has — that is what its pop-out is for, and it survived the merge.
//  * Agent Context is Agent's Instructions segment. Tools, policies, and instruction files
//    are the halves of "what is this agent running with", and nobody asks one without the
//    other. Instructions has no `available` gate, so a shell session focused on this tab
//    still reaches it; that is what the separate Project-scoped tab used to buy.
//
// That argument is exactly why the *fleet* queue is not a tab. It has no send button —
// nothing there needs a terminal beside it — so it is a modal opened from the Queue tab
// and the app menu, the same watch-here/act-there split Processes has with the process
// fleet. Two queue-shaped tabs in one rail also read as a duplicate of each other.
//
// Schedule closes the Project-scoped block, immediately after Processes, because the two
// answer the same question at different times: Processes is what this Project's sessions are
// running *now*, Schedule is what it will start *later*. It carries its own fleet scope the
// way Processes does rather than a companion modal — "what fires tonight" spans Projects even
// though every schedule belongs to exactly one — and it is a tab rather than a modal because
// deciding whether a nightly run should keep running, be paused, or be run right now is a
// judgement about live sessions, which are legible in the workspace behind it.
//
// Processes ships hidden by default (`DEFAULT_HIDDEN_DRAWER_TABS` in `drawerVisibility.ts`).
// It is *not* made redundant by the Resources modal: a modal covers the terminal, and this
// tab exists to answer "what is *this* session running" beside it, pinned to the focused
// session. But that is asked rarely enough that it should not spend a rail slot for a new
// user who has not asked for it.

export type DrawerTabId = 'actions' | 'queue' | 'transcript' | 'activity' | 'agent' | 'files' | 'notes' | 'git' | 'processes' | 'schedule' | 'notifications'

/** What a tab acts on: the focused terminal, the active Project, or the app itself. */
export type DrawerTabScope = 'session' | 'project' | 'app'

export type DrawerTab = {
  id: DrawerTabId
  /** Short accessible name, also drawn when the Appearance setting uses titles. */
  label: string
  /** Visible identity inside the content surface. Kept separate from the compact rail label. */
  heading: string
  title: string
  scope: DrawerTabScope
}

// The tab's mark lives in `railIcons.tsx` (`DRAWER_TAB_ICONS`), not here, so this module stays
// JSX-free and unit-testable under plain `node --experimental-strip-types`.
// Every title leads with its tab's compact label so the icon controls announce and tooltip the
// same identity. The longer heading is drawn once inside the active content surface.
export const DRAWER_TABS: DrawerTab[] = [
  { id: 'notes', label: 'Notes', heading: 'Notes', title: 'Notes - create and edit Project-owned notes here, or open one in a pane', scope: 'project' },
  { id: 'files', label: 'Files', heading: 'File Explorer', title: 'Files - browse or search this Project, then open here or in a pane', scope: 'project' },
  { id: 'actions', label: 'Actions', heading: 'Actions', title: 'Actions - quick shortcuts, skills, prompt templates, and clipboard history', scope: 'session' },
  { id: 'queue', label: 'Queue', heading: 'Prompt Queue', title: 'Queue - messages staged for the focused agent', scope: 'session' },
  { id: 'transcript', label: 'Transcript', heading: 'Transcript', title: 'Transcript - read and copy this session’s conversation', scope: 'session' },
  { id: 'activity', label: 'Activity', heading: 'Activity', title: 'Activity - what this session narrated, what the detectors found, and what it changed', scope: 'session' },
  { id: 'agent', label: 'Agent', heading: 'Agent', title: 'Agent - configuration, tools, and instructions this session is running with', scope: 'session' },
  { id: 'git', label: 'Git', heading: 'Git', title: 'Git - worktree map and commit graph for this Project', scope: 'project' },
  { id: 'processes', label: 'Processes', heading: 'Processes', title: 'Processes - what this Project’s sessions are running, and what they are serving', scope: 'project' },
  { id: 'schedule', label: 'Schedule', heading: 'Scheduled Runs', title: 'Schedule - sessions this Project starts on its own, and what they did last time', scope: 'project' },
  { id: 'notifications', label: 'Alerts', heading: 'Alerts', title: 'Alerts - what needs you now, and every attention record behind it', scope: 'app' },
]

/** Tabs that open a document into the workspace instead of typing into it.
 *
 * Git is Project-scoped like the navigators and sits beside them, but it is not one: it
 * reports on the repository rather than opening anything into a pane. */
export const isNavigatorTab = (id: DrawerTabId): boolean => id === 'files' || id === 'notes'

export const DRAWER_TAB_KEY = 'mux.drawer.tab.v1'
export const DRAWER_PROJECT_STATE_KEY = 'mux.drawer.projects.v1'
export const DRAWER_WIDTH_KEY = 'mux.drawer.width.v1'
export const DRAWER_MIN_WIDTH = 300
export const DRAWER_DEFAULT_WIDTH = 380
export const DRAWER_COLLAPSE_WIDTH = 260
export const DRAWER_REOPEN_WIDTH = 280
export const DRAWER_RESIZER_WIDTH = 4
export const DRAWER_RAIL_WIDTH = 40
export const MAIN_WORKSPACE_MIN_WIDTH = 150

export function clampDrawerWidth(value: number, maximum = Number.POSITIVE_INFINITY): number {
  if (!Number.isFinite(value)) return DRAWER_DEFAULT_WIDTH
  const ceiling = Number.isFinite(maximum) ? Math.max(DRAWER_MIN_WIDTH, maximum) : Number.POSITIVE_INFINITY
  return Math.max(DRAWER_MIN_WIDTH, Math.min(ceiling, value))
}

/** Largest dock width that preserves the main workspace's usable desktop strip. */
export function drawerMaximumWidth(
  viewportWidth: number,
  leftChromeWidth: number,
  launcherWidth = DRAWER_RAIL_WIDTH,
): number {
  if (!Number.isFinite(viewportWidth) || !Number.isFinite(leftChromeWidth) || !Number.isFinite(launcherWidth)) return DRAWER_DEFAULT_WIDTH
  const available = viewportWidth - leftChromeWidth - DRAWER_RESIZER_WIDTH - launcherWidth - MAIN_WORKSPACE_MIN_WIDTH
  return Math.max(DRAWER_MIN_WIDTH, Math.floor(available))
}

export function storedDrawerWidth(raw: string | null): number {
  const parsed = Number(raw)
  return parsed ? clampDrawerWidth(parsed) : DRAWER_DEFAULT_WIDTH
}

export function drawerTab(id: DrawerTabId): DrawerTab {
  return DRAWER_TABS.find(tab => tab.id === id) || DRAWER_TABS[0]
}
