// Which utility-drawer tabs are drawn, and which the user has put away.
//
// Two independent reasons a tab is not on screen, deliberately kept apart:
//
//  * **Structural** — the tab has nothing to act on and nothing the user does inside
//    swe-mux changes that. Transcript on a shell session is the whole list today.
//    Not a preference, never persisted, and no control offers to "restore" it.
//  * **Hidden** — the user put it away from the tab's own context menu. One global,
//    device-local set, the same scope the arrangement in `drawerLayout.ts` has, because
//    visibility *is* arrangement: which tabs exist is not a property that varies by
//    Project while their position, stack, and split ratio do not.
//
// Hiding is a render filter and never a layout mutation. `normalizeDrawerLayout` keeps
// every registered tab in the tree exactly once and re-inserts a missing one at its
// *canonical* position, so removing a tab from the layout would silently discard where
// the user had dragged it the moment they showed it again.
//
// Explicit navigation is not filtered. A palette entry, a voice command, or a menu row
// that names a surface has already said "show me this", so the host peeks a hidden tab
// (`peek`) for as long as it stays selected rather than quietly unhiding it.

// Explicit extension: this module is reachable from the node test runner, whose
// type-stripping ESM loader does not resolve extensionless specifiers.
import { DRAWER_TABS, type DrawerTabId } from './drawerTabs.ts'

export const DRAWER_HIDDEN_KEY = 'mux.drawer.hidden.v1'

export type DrawerVisibility = {
  /** Tabs the user has put away, in no meaningful order. */
  hidden: readonly DrawerTabId[]
  /** False on a session whose harness writes no transcript. */
  hasTranscript: boolean
  /** A hidden tab reached by name, shown until the selection moves off it. */
  peek?: DrawerTabId | null
}

const known = (value: unknown): value is DrawerTabId =>
  typeof value === 'string' && DRAWER_TABS.some(tab => tab.id === value)

/**
 * What a browser that has never chosen hides.
 *
 * Distinct from the empty set on purpose: `parseHiddenDrawerTabs(null)` means "no stored
 * choice", and that is the only moment this applies. Once the user touches the visibility
 * menu at all, their set is stored — including the empty set — and this is never consulted
 * again, so showing Processes and then never seeing it hidden again is a decision the user
 * gets to keep.
 *
 * Processes is the one entry. It is not redundant with the Resources modal (a modal covers
 * the terminal; this tab pins the focused session beside it), but it answers a question
 * asked rarely enough not to spend a permanent rail slot on by default. Alerts is
 * deliberately *not* here: it is the only tab that draws an unread badge, and hiding the
 * one surface that says something needs you is the opposite of streamlining.
 */
export const DEFAULT_HIDDEN_DRAWER_TABS: readonly DrawerTabId[] = ['processes']

/** Coerce anything stored (or written by an older build) into a usable hidden set. */
export function parseHiddenDrawerTabs(raw: string | null): DrawerTabId[] {
  if (raw === null) return [...DEFAULT_HIDDEN_DRAWER_TABS]
  if (!raw) return []
  let parsed: unknown
  try { parsed = JSON.parse(raw) } catch { return [] }
  if (!Array.isArray(parsed)) return []
  const result: DrawerTabId[] = []
  for (const value of parsed) if (known(value) && !result.includes(value)) result.push(value)
  // A stored set that hides everything would leave no tab strip to reach the restore
  // control from, so it is dropped rather than honoured.
  return result.length < DRAWER_TABS.length ? result : []
}

/** Canonical order on write, so the stored value does not churn as tabs are toggled. */
export function serializeHiddenDrawerTabs(hidden: readonly DrawerTabId[]): string {
  return JSON.stringify(DRAWER_TABS.map(tab => tab.id).filter(id => hidden.includes(id)))
}

/**
 * Hiding the last remaining tab is refused rather than allowed and recovered from.
 *
 * The restore control lives on the tab strip, so an empty strip would leave Settings as
 * the only way back — the exact "hidden functionality with no way to find it" this
 * feature exists to avoid. The bound counts the hidden set alone and ignores structural
 * availability so the answer does not change with the focused session, which is also
 * what lets Settings render the identical checklist without one.
 */
export function canHideDrawerTab(hidden: readonly DrawerTabId[], id: DrawerTabId): boolean {
  return hidden.includes(id) || hidden.length + 1 < DRAWER_TABS.length
}

export function withDrawerTabHidden(
  hidden: readonly DrawerTabId[],
  id: DrawerTabId,
  value: boolean,
): DrawerTabId[] {
  if (value === hidden.includes(id)) return [...hidden]
  if (value && !canHideDrawerTab(hidden, id)) return [...hidden]
  return value ? [...hidden, id] : hidden.filter(item => item !== id)
}

/** True where the tab has something to act on, regardless of what the user hid. */
export function drawerTabStructurallyAvailable(id: DrawerTabId, hasTranscript: boolean): boolean {
  // Activity and Agent are deliberately not here. Their per-segment `available` predicates
  // in `drawerSegments.ts` gate the segments that need a transcript or an agent harness,
  // and `resolveDrawerSegment` falls back to one that does not — so a shell session still
  // reaches Activity's findings and Agent's instructions. Gating the whole tab would hide
  // surfaces that work.
  return id !== 'transcript' || hasTranscript
}

export function drawerTabVisible(id: DrawerTabId, visibility: DrawerVisibility): boolean {
  if (!drawerTabStructurallyAvailable(id, visibility.hasTranscript)) return false
  return !visibility.hidden.includes(id) || visibility.peek === id
}

export function visibleDrawerTabs(
  ids: readonly DrawerTabId[],
  visibility: DrawerVisibility,
): DrawerTabId[] {
  return ids.filter(id => drawerTabVisible(id, visibility))
}
