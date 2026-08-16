// The right-edge utility drawer: tab registry, persistence, and pure helpers.
//
// One singleton host per tab, with two responsive projections. On mobile it is an
// overlay drawer entering from the right edge. On desktop it is an in-flow recursive
// split tree next to an always-visible launcher rail, so it never covers the Project
// workspace.
//
// Tab order groups by what a tab acts on. First the surfaces that *inject into the
// focused session* (clipboard, Actions, and the prompt queue),
// then the passive session surfaces: Transcript reads the focused conversation and Agent
// inventories the selected CLI environment. Agent closes the session-scoped block.
// Then the *navigators* (files, notes): project-scoped indexes over documents rather than
// surfaces that inject text — narrow-column surfaces that used to cost a permanent
// workspace tab each. Files opens what you pick into a pane; Notes hosts the editor itself
// and opens into a pane only on request, because reading or adding to a note without
// leaving the session on screen is what the tab is for on a phone. Context follows them as
// a read-only view of the files agents themselves consume. Git closes the Project-scoped block: it reads the
// repository behind the Project rather than opening anything, so it is not a navigator,
// but it acts on the same thing they do. Notifications is the one application-wide fleet
// view that earns a permanent tab, and stays last.
//
// Queue closes the injection block, which is where it belongs and why it is here rather
// than in a workspace tab or a modal: deciding whether to send is a judgement about the
// agent's live state, and that state is only legible in the terminal. A pane leaf replaces
// the terminal, a modal covers it; the docked column is the one placement that keeps the
// target and the control on screen together.
//
// That argument is exactly why the *fleet* queue is not a tab. It has no send button —
// nothing there needs a terminal beside it — so it is a modal opened from the Queue tab
// and the app menu, the same watch-here/act-there split Processes has with the process
// fleet. Two queue-shaped tabs in one rail also read as a duplicate of each other.

export type DrawerTabId = 'clipboard' | 'actions' | 'queue' | 'transcript' | 'insight' | 'agent' | 'files' | 'notes' | 'context' | 'git' | 'processes' | 'notifications'

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
  { id: 'clipboard', label: 'Clipboard', heading: 'Clipboard History', title: 'Clipboard history - insert a recent copy', scope: 'session' },
  { id: 'actions', label: 'Actions', heading: 'Actions', title: 'Actions - quick shortcuts, skills, and prompt templates', scope: 'session' },
  { id: 'queue', label: 'Queue', heading: 'Prompt Queue', title: 'Queue - messages staged for the focused agent', scope: 'session' },
  { id: 'transcript', label: 'Transcript', heading: 'Transcript', title: 'Transcript - read and copy this session’s conversation', scope: 'session' },
  { id: 'insight', label: 'Insight', heading: 'Insight', title: 'Insight - scan timeline and deterministic findings for this session', scope: 'session' },
  { id: 'agent', label: 'Agent', heading: 'Agent Environment', title: 'Agent - inspect tools, extensions, policies, and configuration for this session', scope: 'session' },
  { id: 'files', label: 'Files', heading: 'File Explorer', title: 'Files - browse or search this Project, then open into a pane', scope: 'project' },
  { id: 'notes', label: 'Notes', heading: 'Notes', title: 'Notes - create and edit Project-owned notes here, or open one in a pane', scope: 'project' },
  { id: 'context', label: 'Context', heading: 'Instructions & Memory', title: 'Context - view agent instructions and learned project memory', scope: 'project' },
  { id: 'git', label: 'Git', heading: 'Git', title: 'Git - worktree map and commit graph for this Project', scope: 'project' },
  { id: 'processes', label: 'Processes', heading: 'Processes', title: 'Processes - what this Project’s sessions are running, and what they are serving', scope: 'project' },
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
