// Device-local mobile tab order.
//
// The mobile rail is a projection of the desktop pane tree (see mobileWorkspace),
// so its order is normally derived: depth-first over the tree, each stack's
// children in order. That makes the phone's tab order a consequence of desktop
// geometry, which is not always the order you want to swipe through.
//
// This module adds a *permutation only* overlay on top of that projection. It is
// deliberately unable to add or remove a tab: the layout stays authoritative for
// membership, so the overlay can never desync from what actually exists. It is
// also never sent to the daemon — reordering here writes no layout revision, so
// a phone rearranging its rail cannot touch the desktop layout or any other
// device. Storage is the same device-local browser state as focus memory.
//
// Pure so the merge rules can be unit-tested without a DOM.

/** projectId → the ordered leaf ids last displayed on this device. */
export type MobileTabOrder = Record<string, string[]>

export const MOBILE_TAB_ORDER_KEY = 'mux.mobileTabOrder.v1'

export function parseMobileTabOrder(raw: string | null): MobileTabOrder {
  if (!raw) return {}
  try {
    const parsed: unknown = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {}
    const result: MobileTabOrder = {}
    for (const [projectId, ids] of Object.entries(parsed as Record<string, unknown>)) {
      if (!Array.isArray(ids)) continue
      const clean = ids.filter((id): id is string => typeof id === 'string')
      if (clean.length) result[projectId] = clean
    }
    return result
  } catch {
    return {}
  }
}

/** Drop projects that no longer exist so the stored blob cannot grow forever. */
export function pruneMobileTabOrder(order: MobileTabOrder, knownProjectIds: Iterable<string>): MobileTabOrder {
  const known = new Set(knownProjectIds)
  const result: MobileTabOrder = {}
  for (const [projectId, ids] of Object.entries(order)) {
    if (known.has(projectId) && ids.length) result[projectId] = ids
  }
  return result
}

export function serializeMobileTabOrder(order: MobileTabOrder): string {
  return JSON.stringify(order)
}

/** Apply a saved permutation to tabs given in layout order.
 *
 *  Saved ids that no longer exist are ignored, and tabs the save predates are
 *  inserted at their *layout-relative* position rather than appended: a session
 *  launched from a given tab should appear next to it, the way it does on
 *  desktop, not jump to the end of the rail. A run of new tabs anchors on the
 *  previously inserted one, so they stay together in layout order. */
export function orderMobileTabs<T extends { id: string }>(tabs: T[], saved: readonly string[] | undefined): T[] {
  if (!saved || !saved.length) return tabs
  const byId = new Map(tabs.map(tab => [tab.id, tab]))
  const placed = new Set<string>()
  const result: T[] = []
  for (const id of saved) {
    const tab = byId.get(id)
    if (!tab || placed.has(id)) continue
    placed.add(id)
    result.push(tab)
  }
  if (result.length === tabs.length) return result
  for (let index = 0; index < tabs.length; index += 1) {
    const tab = tabs[index]
    if (placed.has(tab.id)) continue
    let at = 0
    for (let previous = index - 1; previous >= 0; previous -= 1) {
      const anchor = result.findIndex(item => item.id === tabs[previous].id)
      if (anchor >= 0) { at = anchor + 1; break }
    }
    result.splice(at, 0, tab)
    placed.add(tab.id)
  }
  return result
}

/** The id list after moving `id` one slot, or null when it cannot move (absent,
 *  or already at that end). Callers use null to disable the control. */
export function moveMobileTab(ids: readonly string[], id: string, direction: 'left' | 'right'): string[] | null {
  const index = ids.indexOf(id)
  if (index < 0) return null
  const target = direction === 'left' ? index - 1 : index + 1
  if (target < 0 || target >= ids.length) return null
  const next = [...ids]
  next[index] = next[target]
  next[target] = id
  return next
}
