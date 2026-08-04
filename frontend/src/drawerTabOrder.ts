// Legacy flat-order normalization for the utility drawer migration.
//
// New arrangements are recursive and device-local in `drawerLayout.ts`. This module remains only
// to validate the former server-synced `drawerTabs` list when it seeds the first v1 layout.

// Explicit extension: this module is reachable from the node test runner, whose
// type-stripping ESM loader does not resolve extensionless specifiers.
import { DRAWER_TABS, type DrawerTabId } from './drawerTabs.ts'

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
