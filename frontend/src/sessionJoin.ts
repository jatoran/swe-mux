import {
  activateContainingStack,
  leaves,
  openTab,
  spawnAnchorId,
  stackForView,
  terminalLeaf,
  type PaneLayout,
} from './layout.ts'

/**
 * Joining daemon-started sessions to the tab layout.
 *
 * A device launch writes its own leaf: `spawnTerminal` places an optimistic pending tab in the
 * focused pane and swaps the real id into it. Nothing does that for a session the *daemon*
 * started on its own - an approved `request_spawn`, the assistant's daemon spawn path, a
 * scheduled run - so those arrived in the fleet with no leaf anywhere in their Project's layout.
 * They rendered as a sidebar row detached from every pane group and joined the tabs only when
 * the operator tapped them, which is a placement decision the operator never asked to make.
 *
 * This module is the rule for making that join happen on its own, and it is deliberately pure so
 * the placement can be tested without a browser: the App feeds it a layout and the ids the fleet
 * holds, and it hands back the layout those sessions belong in.
 */

/** How many refused writes retire a session from automatic joining.
 *
 *  A join is a write nobody asked for, so it must never become a retry loop: a Project already at
 *  the server's 64-leaf cap refuses every PATCH, and each refusal refreshes, which would compute
 *  the same join again forever. After this many refusals the session simply stays floating - the
 *  behaviour before any of this existed - and tapping it still works.
 */
export const MAX_JOIN_ATTEMPTS = 3

export type JoinAttempts = Record<string, number>

/** The pane a joining session lands in, as a leaf id to anchor against.
 *
 *  This is `spawnTerminal`'s own anchor rule, which is the point: a daemon-started session should
 *  land where the same session launched from this device would have landed.
 *  - The caller's preferred view when this layout still holds it - the focused pane of the Project
 *    the operator is looking at.
 *  - Otherwise the pane that already holds terminal tabs (`spawnAnchorId`), because the Project
 *    being joined is usually not the one on screen and its agents belong beside its other agents.
 *  - `null` only for a layout with no pane at all, which is the one case that creates one.
 */
export function joinAnchorId(layout: PaneLayout, preferredViewId: string | null): string | null {
  if (preferredViewId && stackForView(layout, preferredViewId)) return preferredViewId
  return spawnAnchorId(layout)
}

/** The subset of `ids` this layout has no leaf for.
 *
 *  Tested against every leaf rather than only terminal leaves, because `openTab` dedupes on leaf
 *  id: an id the layout already holds under another kind would never be added and would be
 *  proposed again on every refresh.
 */
export function unjoinedSessionIds(layout: PaneLayout, ids: string[]): string[] {
  const held = new Set(leaves(layout).map(leaf => leaf.id))
  return ids.filter(id => !held.has(id))
}

/** Add a tab for every session this layout is missing, without moving what is on screen.
 *
 *  Every joined session lands in one pane - the anchor's - appended in fleet order, and the pane's
 *  active tab is put back afterwards. That last part is the whole "no focus stealing" contract at
 *  the layout level: `openTab` activates what it adds, which is right for a launch the operator
 *  just made and wrong for a session that appeared underneath them. The app-level focus (the
 *  active terminal, the focused view) is not this function's business and no caller should move it.
 *
 *  The one exception is a session the operator is *already* looking at. A view the layout has no
 *  leaf for is rendered as a synthetic pane covering the whole workspace, so a worktree session
 *  that finished setup is focused and unpanned at once; joining it while restoring some other tab
 *  would swap the workspace back to the pane tree and hide the very thing on screen. Following
 *  focus there is not stealing it - it is the same session, now with a tab.
 *
 *  Returns the layout unchanged - identity, so a caller can compare - when nothing was missing.
 */
export function joinSessions(
  layout: PaneLayout,
  ids: string[],
  preferredViewId: string | null,
): PaneLayout {
  const missing = unjoinedSessionIds(layout, ids)
  if (!missing.length) return layout
  const anchor = joinAnchorId(layout, preferredViewId)
  // Read before the first insertion: `openTab` overwrites it.
  const restore = anchor ? stackForView(layout, anchor)?.active_child_id ?? null : null
  let next = layout
  for (const id of missing) next = openTab(next, anchor, terminalLeaf(id))
  if (preferredViewId && missing.includes(preferredViewId)) {
    return activateContainingStack(next, preferredViewId)
  }
  // A layout that had no pane has nothing to restore, and the session it just created a pane for
  // is the only thing that pane can show.
  return restore ? activateContainingStack(next, restore) : next
}

/** Ids still eligible to join, dropping any whose join the server has refused too often. */
export function joinableSessionIds(ids: string[], attempts: JoinAttempts): string[] {
  return ids.filter(id => (attempts[id] ?? 0) < MAX_JOIN_ATTEMPTS)
}

/** Count one refused write against every session it carried. */
export function recordJoinFailure(attempts: JoinAttempts, ids: string[]): JoinAttempts {
  const next = { ...attempts }
  for (const id of ids) next[id] = (next[id] ?? 0) + 1
  return next
}

/** Drop the record for sessions that have left the fleet, so it cannot grow without bound. */
export function forgetJoinAttempts(attempts: JoinAttempts, liveIds: Set<string>): JoinAttempts {
  const next: JoinAttempts = {}
  for (const [id, count] of Object.entries(attempts)) if (liveIds.has(id)) next[id] = count
  return next
}
