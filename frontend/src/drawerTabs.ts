// The right-edge utility drawer: tab registry, persistence, and pure helpers.
//
// One host, two renderings. On mobile it is an overlay drawer entering from the
// right edge (the sidebar's mirror image, and mutually exclusive with it). On
// desktop it is an in-flow column of the workspace grid, next to an always-visible
// icon rail, because an overlay covering a tiling pane manager hides the terminal
// you opened it to work with.
//
// Tab order groups by what a tab acts on. First the surfaces that *inject into the
// focused session* (clipboard, session commands, prompt templates, the prompt queue),
// then Transcript, which is session-scoped like them but writes nothing: it reads the
// focused session's conversation back. It closes that block rather than opening it
// because the block is ordered by what you do to a session, and reading is what you do
// between the doing.
// Then the *navigators* (files, notes): project-scoped indexes over documents rather than
// surfaces that inject text — narrow-column surfaces that used to cost a permanent
// workspace tab each. Files opens what you pick into a pane; Notes hosts the editor itself
// and opens into a pane only on request, because reading or adding to a note without
// leaving the session on screen is what the tab is for on a phone. Context follows them as
// a read-only view of the files agents themselves consume. Git closes the Project-scoped block: it reads the
// repository behind the Project rather than opening anything, so it is not a navigator,
// but it acts on the same thing they do. Notifications is neither, and stays last.
//
// Queue closes the injection block, which is where it belongs and why it is here rather
// than in a workspace tab or a modal: deciding whether to send is a judgement about the
// agent's live state, and that state is only legible in the terminal. A pane leaf replaces
// the terminal, a modal covers it; the docked column is the one placement that keeps the
// target and the control on screen together.

export type DrawerTabId = 'clipboard' | 'commands' | 'prompts' | 'queue' | 'transcript' | 'files' | 'notes' | 'context' | 'git' | 'processes' | 'notifications'

/** What a tab acts on: the focused terminal, the active Project, or the app itself. */
export type DrawerTabScope = 'session' | 'project' | 'app'

export type DrawerTab = {
  id: DrawerTabId
  /** Short accessible name. Both surfaces render an icon, so this is never drawn. */
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
  { id: 'commands', label: 'Commands', heading: 'Commands', title: 'Commands - keys, skills, and slash commands not on the rail', scope: 'session' },
  { id: 'prompts', label: 'Prompts', heading: 'Prompt Library', title: 'Prompts - insert a saved template into the focused terminal', scope: 'session' },
  { id: 'queue', label: 'Queue', heading: 'Prompt Queue', title: 'Queue - messages staged for this agent, and the mailbox', scope: 'session' },
  { id: 'transcript', label: 'Transcript', heading: 'Transcript', title: 'Transcript - read and copy this session’s conversation', scope: 'session' },
  { id: 'files', label: 'Files', heading: 'File Explorer', title: 'Files - browse or search this Project, then open into a pane', scope: 'project' },
  { id: 'notes', label: 'Notes', heading: 'Notes', title: 'Notes - read and write Project and session notes here, or open one in a pane', scope: 'project' },
  { id: 'context', label: 'Context', heading: 'Agent Context', title: 'Context - view agent instructions and learned project memory', scope: 'project' },
  { id: 'git', label: 'Git', heading: 'Git', title: 'Git - worktree map and commit graph for this Project', scope: 'project' },
  { id: 'processes', label: 'Processes', heading: 'Processes', title: 'Processes - what this Project’s sessions are running, and what they are serving', scope: 'project' },
  { id: 'notifications', label: 'Alerts', heading: 'Alerts', title: 'Alerts - notifications and attention records', scope: 'app' },
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

export type DrawerProjectState = Readonly<{
  tab: DrawerTabId
  desktopExpanded: boolean
}>

/** Project id → the device-local presentation of that Project's drawer. */
export type DrawerProjectStateMap = Readonly<Record<string, DrawerProjectState>>

export const DEFAULT_DRAWER_PROJECT_STATE: DrawerProjectState = {
  tab: 'clipboard',
  desktopExpanded: false,
}

export const EMPTY_DRAWER_PROJECT_STATES: DrawerProjectStateMap = {}

export function clampDrawerWidth(value: number, maximum = Number.POSITIVE_INFINITY): number {
  if (!Number.isFinite(value)) return DRAWER_DEFAULT_WIDTH
  const ceiling = Number.isFinite(maximum) ? Math.max(DRAWER_MIN_WIDTH, maximum) : Number.POSITIVE_INFINITY
  return Math.max(DRAWER_MIN_WIDTH, Math.min(ceiling, value))
}

/** Largest dock width that preserves the main workspace's usable desktop strip. */
export function drawerMaximumWidth(viewportWidth: number, leftChromeWidth: number): number {
  if (!Number.isFinite(viewportWidth) || !Number.isFinite(leftChromeWidth)) return DRAWER_DEFAULT_WIDTH
  const available = viewportWidth - leftChromeWidth - DRAWER_RESIZER_WIDTH - DRAWER_RAIL_WIDTH - MAIN_WORKSPACE_MIN_WIDTH
  return Math.max(DRAWER_MIN_WIDTH, Math.floor(available))
}

export function storedDrawerWidth(raw: string | null): number {
  const parsed = Number(raw)
  return parsed ? clampDrawerWidth(parsed) : DRAWER_DEFAULT_WIDTH
}

/** Validate a stored tab id; each Project's last-used tab is restored through the map below. */
export function parseDrawerTab(raw: string | null): DrawerTabId {
  return DRAWER_TABS.some(tab => tab.id === raw) ? (raw as DrawerTabId) : 'clipboard'
}

/** Read the per-Project drawer map without letting stale or hand-edited storage break boot. */
export function parseDrawerProjectStates(raw: string | null): DrawerProjectStateMap {
  if (!raw) return EMPTY_DRAWER_PROJECT_STATES
  let value: unknown
  try {
    value = JSON.parse(raw)
  } catch {
    return EMPTY_DRAWER_PROJECT_STATES
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) return EMPTY_DRAWER_PROJECT_STATES
  const map: Record<string, DrawerProjectState> = {}
  for (const [projectId, stored] of Object.entries(value as Record<string, unknown>)) {
    if (!projectId || !stored || typeof stored !== 'object' || Array.isArray(stored)) continue
    const candidate = stored as { tab?: unknown; desktopExpanded?: unknown }
    map[projectId] = {
      tab: typeof candidate.tab === 'string' ? parseDrawerTab(candidate.tab) : DEFAULT_DRAWER_PROJECT_STATE.tab,
      desktopExpanded: candidate.desktopExpanded === true,
    }
  }
  return map
}

export function serializeDrawerProjectStates(map: DrawerProjectStateMap): string {
  return JSON.stringify(map)
}

export function drawerProjectStateFor(map: DrawerProjectStateMap, projectId: string): DrawerProjectState {
  return (projectId && map[projectId]) || DEFAULT_DRAWER_PROJECT_STATE
}

/** Apply one presentation change without disturbing any other Project's drawer. */
export function updateDrawerProjectState(
  map: DrawerProjectStateMap,
  projectId: string,
  patch: Partial<DrawerProjectState>,
): DrawerProjectStateMap {
  if (!projectId) return map
  const current = drawerProjectStateFor(map, projectId)
  const next: DrawerProjectState = {
    tab: patch.tab ?? current.tab,
    desktopExpanded: patch.desktopExpanded ?? current.desktopExpanded,
  }
  if (map[projectId] && next.tab === current.tab && next.desktopExpanded === current.desktopExpanded) return map
  if (!map[projectId] && next.tab === DEFAULT_DRAWER_PROJECT_STATE.tab && next.desktopExpanded === DEFAULT_DRAWER_PROJECT_STATE.desktopExpanded) return map
  return { ...map, [projectId]: next }
}

/** Seed only the initially active Project from the former one-tab-for-the-whole-app key. */
export function migrateLegacyDrawerTab(
  map: DrawerProjectStateMap,
  projectId: string,
  legacyTab: string | null,
): DrawerProjectStateMap {
  if (!projectId || legacyTab === null || map[projectId]) return map
  return updateDrawerProjectState(map, projectId, { tab: parseDrawerTab(legacyTab) })
}

/** Remove state belonging to Projects that no longer exist. */
export function pruneDrawerProjectStates(
  map: DrawerProjectStateMap,
  knownProjectIds: readonly string[],
): DrawerProjectStateMap {
  const known = new Set(knownProjectIds)
  const stale = Object.keys(map).filter(projectId => !known.has(projectId))
  if (!stale.length) return map
  const next = { ...map }
  for (const projectId of stale) delete next[projectId]
  return next
}

export function drawerTab(id: DrawerTabId): DrawerTab {
  return DRAWER_TABS.find(tab => tab.id === id) || DRAWER_TABS[0]
}

/** Cycle tabs (wrapping) for keyboard navigation inside the drawer.
 *
 * `order` is the user's arrangement when they have one, because Tab has to walk the strip in
 * the order it is drawn; without it, keyboard cycling would jump around a rearranged strip.
 */
export function nextDrawerTab(current: DrawerTabId, offset: number, order?: DrawerTabId[]): DrawerTabId {
  const ids = order?.length ? order : DRAWER_TABS.map(tab => tab.id)
  const index = ids.indexOf(current)
  const at = (index < 0 ? 0 : index) + offset
  return ids[((at % ids.length) + ids.length) % ids.length]
}
