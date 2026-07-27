// The right-edge utility drawer: tab registry, persistence, and pure helpers.
//
// One host, two renderings. On mobile it is an overlay drawer entering from the
// right edge (the sidebar's mirror image, and mutually exclusive with it). On
// desktop it is an in-flow column of the workspace grid, next to an always-visible
// icon rail, because an overlay covering a tiling pane manager hides the terminal
// you opened it to work with.
//
// Tab order groups by what a tab acts on. First the surfaces that *inject into the
// focused session* (clipboard, session commands, prompt templates). Then the
// *navigators* (files, notes): project-scoped indexes that open a document into a
// pane rather than injecting text — narrow-column surfaces that used to cost a
// permanent workspace tab each. Notifications is neither, and stays last.

export type DrawerTabId = 'clipboard' | 'commands' | 'prompts' | 'files' | 'notes' | 'notifications'

/** What a tab acts on: the focused terminal, the active Project, or the app itself. */
export type DrawerTabScope = 'session' | 'project' | 'app'

export type DrawerTab = {
  id: DrawerTabId
  /** Short accessible name. Both surfaces render an icon, so this is never drawn. */
  label: string
  title: string
  scope: DrawerTabScope
}

// The tab's mark lives in `railIcons.tsx` (`DRAWER_TAB_ICONS`), not here, so this module stays
// JSX-free and unit-testable under plain `node --experimental-strip-types`.
// Every title leads with its tab's label. Nothing on either surface is drawn with a word any
// more, so the tooltip is the only place the tab is named and has to say the name first.
export const DRAWER_TABS: DrawerTab[] = [
  { id: 'clipboard', label: 'Clipboard', title: 'Clipboard history — insert a recent copy', scope: 'session' },
  { id: 'commands', label: 'Commands', title: 'Commands — keys, skills, and slash commands not on the rail', scope: 'session' },
  { id: 'prompts', label: 'Prompts', title: 'Prompts — insert a saved template into the focused terminal', scope: 'session' },
  { id: 'files', label: 'Files', title: 'Files — browse or search this Project, then open into a pane', scope: 'project' },
  { id: 'notes', label: 'Notes', title: 'Notes — Project and session notes, opened into a pane', scope: 'project' },
  { id: 'notifications', label: 'Alerts', title: 'Alerts — notifications and attention records', scope: 'app' },
]

/** Tabs that open a document into the workspace instead of typing into it. */
export const isNavigatorTab = (id: DrawerTabId): boolean => id === 'files' || id === 'notes'

export const DRAWER_TAB_KEY = 'mux.drawer.tab.v1'
export const DRAWER_WIDTH_KEY = 'mux.drawer.width.v1'
export const DRAWER_MIN_WIDTH = 300
export const DRAWER_MAX_WIDTH = 620
export const DRAWER_DEFAULT_WIDTH = 380

export function clampDrawerWidth(value: number): number {
  if (!Number.isFinite(value)) return DRAWER_DEFAULT_WIDTH
  return Math.max(DRAWER_MIN_WIDTH, Math.min(DRAWER_MAX_WIDTH, value))
}

export function storedDrawerWidth(raw: string | null): number {
  const parsed = Number(raw)
  return parsed ? clampDrawerWidth(parsed) : DRAWER_DEFAULT_WIDTH
}

/** Last-used tab, so the drawer reopens where you left it. */
export function parseDrawerTab(raw: string | null): DrawerTabId {
  return DRAWER_TABS.some(tab => tab.id === raw) ? (raw as DrawerTabId) : 'clipboard'
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
