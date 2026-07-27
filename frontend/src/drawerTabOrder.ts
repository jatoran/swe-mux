// User-arranged order for the utility drawer's tabs.
//
// The order is one list shared by the drawer's own tab strip and the desktop icon rail, because
// they are two renderings of one control: dragging a tab in either has to move it in both, and a
// per-surface order would let them disagree about what "third from the top" means.
//
// It is server-persisted rather than device-local (see `deviceSettings.ts`), for the same reason
// the command rail and the file tree are: a tab order expresses which surfaces *you* reach for,
// not anything about screen size, so a phone should inherit what a desktop arranged. Drawer width
// and last-used tab stay in localStorage, which is correct for them — those genuinely differ per
// device.

// Explicit extension: this module is reachable from the node test runner, whose
// type-stripping ESM loader does not resolve extensionless specifiers.
import { DRAWER_TABS, type DrawerTab, type DrawerTabId } from './drawerTabs.ts'

export const DEFAULT_DRAWER_TAB_ORDER: DrawerTabId[] = DRAWER_TABS.map(tab => tab.id)

/**
 * Coerce anything stored (or sent by another client) into a usable order.
 *
 * Unknown and duplicate ids are dropped, and — the part that matters over time — a tab the
 * stored order predates is merged in beside its default predecessor rather than appended. A
 * saved six-tab order must never hide a seventh tab, and shipping it after Alerts would put a
 * new surface in the one position that says "afterthought". This mirrors the rule the mobile
 * tab rail already uses for the same problem.
 */
export function normalizeDrawerTabOrder(raw: unknown): DrawerTabId[] {
  const known = new Set<string>(DEFAULT_DRAWER_TAB_ORDER)
  const result: DrawerTabId[] = []
  if (Array.isArray(raw)) {
    for (const value of raw) {
      if (typeof value !== 'string' || !known.has(value)) continue
      const id = value as DrawerTabId
      if (!result.includes(id)) result.push(id)
    }
  }
  DEFAULT_DRAWER_TAB_ORDER.forEach((id, index) => {
    if (result.includes(id)) return
    const predecessor = DEFAULT_DRAWER_TAB_ORDER.slice(0, index).reverse().find(item => result.includes(item))
    result.splice(predecessor ? result.indexOf(predecessor) + 1 : 0, 0, id)
  })
  return result
}

/** The tab records in the user's order. Always every tab: arranging never hides one. */
export function orderedDrawerTabs(order: DrawerTabId[]): DrawerTab[] {
  const byId = new Map(DRAWER_TABS.map(tab => [tab.id, tab]))
  return normalizeDrawerTabOrder(order).map(id => byId.get(id)!)
}

export function isDefaultDrawerTabOrder(order: DrawerTabId[]): boolean {
  return normalizeDrawerTabOrder(order).join('\0') === DEFAULT_DRAWER_TAB_ORDER.join('\0')
}

export function sameDrawerTabOrder(first: DrawerTabId[], second: DrawerTabId[]): boolean {
  return first.join('\0') === second.join('\0')
}
