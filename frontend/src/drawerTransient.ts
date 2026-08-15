import type { DrawerTabId } from './drawerTabs.ts'
import { activateDrawerTab, type DrawerLayout, type DrawerProjectPresentation } from './drawerLayout.ts'

/** A momentary tab selection owned by one Project. It is intentionally never serialized. */
export type TransientDrawerTab = {
  projectId: string
  tab: DrawerTabId
}

export function transientDrawerTabForProject(
  transient: TransientDrawerTab | null,
  projectId: string,
): DrawerTabId | null {
  return transient?.projectId === projectId ? transient.tab : null
}

/** Render a temporary tab without modifying the Project's durable presentation. */
export function presentationWithTransientDrawerTab(
  presentation: DrawerProjectPresentation,
  layout: DrawerLayout,
  tab: DrawerTabId | null,
): DrawerProjectPresentation {
  return tab ? activateDrawerTab(presentation, layout, tab) : presentation
}
