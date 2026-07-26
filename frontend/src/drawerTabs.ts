// The right-edge utility drawer: tab registry, persistence, and pure helpers.
//
// One host, two renderings. On mobile it is an overlay drawer entering from the
// right edge (the sidebar's mirror image, and mutually exclusive with it). On
// desktop it is an in-flow column of the workspace grid, next to an always-visible
// icon rail, because an overlay covering a tiling pane manager hides the terminal
// you opened it to work with.
//
// Tab order is deliberate: the surfaces that *inject into the focused session*
// come first (clipboard, session commands, prompt templates), then the surfaces
// you merely attend to. Every one of them is text going into a terminal except
// notifications.

export type DrawerTabId = 'clipboard' | 'commands' | 'prompts' | 'notifications'

export type DrawerTab = {
  id: DrawerTabId
  /** Rail/tab glyph. Kept to one character so the 40px rail never wraps. */
  glyph: string
  label: string
  title: string
  /** True when the tab acts on the focused terminal rather than the app. */
  sessionScoped: boolean
}

export const DRAWER_TABS: DrawerTab[] = [
  { id: 'clipboard', glyph: '⧉', label: 'Clipboard', title: 'Clipboard history — insert a recent copy', sessionScoped: true },
  { id: 'commands', glyph: '⌘', label: 'Commands', title: 'Session commands — keys, skills, and slash commands not on the rail', sessionScoped: true },
  { id: 'prompts', glyph: '❯', label: 'Prompts', title: 'Prompt templates — insert into the focused terminal', sessionScoped: true },
  { id: 'notifications', glyph: '!', label: 'Alerts', title: 'Notifications and attention records', sessionScoped: false },
]

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

/** Cycle tabs (wrapping) for keyboard navigation inside the drawer. */
export function nextDrawerTab(current: DrawerTabId, offset: number): DrawerTabId {
  const index = DRAWER_TABS.findIndex(tab => tab.id === current)
  const at = (index < 0 ? 0 : index) + offset
  return DRAWER_TABS[((at % DRAWER_TABS.length) + DRAWER_TABS.length) % DRAWER_TABS.length].id
}
