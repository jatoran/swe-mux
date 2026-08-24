import { terminalIds, visibleTerminalIds, type PaneLayout } from './layout.ts'
import type { Session } from './types'

/**
 * Optimistic session removal.
 *
 * Killing a live session is slow on purpose: the daemon types the backend's graceful
 * exit keys and waits for the process to die (up to a couple of seconds when an agent
 * is mid-turn and never processes them), force-kills the tree, persists the run, and
 * clears the session's media directory. Awaiting all of that before touching the UI
 * left the tab, its pane, and the focus sitting on a session the operator had already
 * decided was gone.
 *
 * So the client removes it immediately and lets the DELETE finish underneath, the
 * mirror image of what `spawnTerminal` already does with `pendingSpawns`. The
 * bookkeeping that makes that safe lives here:
 *
 * - A tombstone per in-flight kill, so the periodic `refresh()` cannot resurrect the
 *   row. `reconcileSessionSnapshots` rebuilds the fleet from the daemon's list, and
 *   the daemon still reports the session as live for the whole teardown window.
 * - The same tombstone excludes the id from the layout reconcile's live set. That is
 *   the only removal the layout needs: `reconcileTerminals` prunes leaves whose
 *   session is not live and never adds one back, so the leaf stays gone whether or not
 *   the layout PATCH has landed yet - which is why the caller can defer that write
 *   until the DELETE settles and have nothing to roll back if it fails.
 * - A TTL, because the dangerous direction is a tombstone that outlives its request:
 *   that hides a session which is still running. On expiry the id is released and the
 *   next refresh decides the truth.
 */

/** How long a kill may hide a session before the client stops believing it. Chosen
 *  above the daemon's own ceiling (graceful wait, force kill, run persistence) so an
 *  ordinary slow kill never trips it, and low enough that a lost request surfaces
 *  while the operator still connects it to what they did. */
export const KILL_TOMBSTONE_TTL_MS = 15000

export type KillTombstone = {
  sessionId: string
  projectId: string
  /** `Date.now()` when the DELETE was issued. */
  startedAt: number
}

export type KillTombstones = Record<string, KillTombstone>

/** Session ids currently hidden by an in-flight kill. */
export function killedSessionIds(tombstones: KillTombstones): Set<string> {
  return new Set(Object.keys(tombstones))
}

/** Drop the rows an optimistic kill has already taken off screen. */
export function applyKillTombstones<T extends { id: string }>(
  sessions: T[], tombstones: KillTombstones,
): T[] {
  const killed = killedSessionIds(tombstones)
  return killed.size === 0 ? sessions : sessions.filter(session => !killed.has(session.id))
}

/** Ids whose DELETE never settled, and which must stop being hidden. */
export function expiredKillIds(
  tombstones: KillTombstones, now: number, ttlMs: number = KILL_TOMBSTONE_TTL_MS,
): string[] {
  return Object.values(tombstones)
    .filter(tombstone => now - tombstone.startedAt >= ttlMs)
    .map(tombstone => tombstone.sessionId)
}

/**
 * Whether a failed DELETE still means the session is gone.
 *
 * The daemon answers 404 for an id it no longer holds, which is precisely the
 * outcome a kill wants: a double-tap, a second client that killed it first, or a
 * session that exited on its own between the click and the request. Treating that as
 * an error would put a row back that nothing is behind.
 */
export function killRemovedTheSession(status: number | undefined): boolean {
  return status === 404
}

/**
 * The ended sessions one "clear ended" sweep takes off a Project.
 *
 * A sweep is the same removal as the single-row one, applied to every row in the
 * Project that has nothing behind it any more, so the filter is exactly the row's own
 * ended test plus the tombstone set. Ids already hidden by an in-flight kill are
 * excluded rather than re-issued: their DELETE is still in the air, and taking a
 * second tombstone for one would let the first `finally` release the id while the
 * second request is still running, briefly showing a row the operator has twice said
 * they are done with.
 *
 * Order follows the caller's list, so the sweep removes rows in the order the sidebar
 * drew them and the layout write below stays deterministic.
 */
export function clearableEndedSessions(
  sessions: readonly Session[], projectId: string, tombstones: KillTombstones,
): Session[] {
  return sessions.filter(session =>
    session.project_id === projectId
    && (session.state === 'exited' || session.state === 'crashed')
    && !tombstones[session.id])
}

export type NextActiveInput = {
  /** Layout with the killed leaf already removed. */
  layout: PaneLayout
  /** Fleet as it stood before the kill. */
  sessions: Session[]
  killedId: string
  projectId: string
  activeId: string | null
  /**
   * This Project's most-recently-focused session ids, newest first
   * (`sessionFocusHistory.ts`). Optional: an empty list is not an error, it is a
   * fresh tab that has nothing behind it yet, and the layout fallbacks answer.
   */
  recent?: readonly string[]
  /**
   * Terminal ids that shared the killed session's pane, read from the layout as it
   * stood *before* the leaf was removed - the post-kill layout can no longer say
   * which pane the session was in, and a stack that emptied is gone from it entirely.
   */
  paneIds?: readonly string[]
}

/**
 * Which session takes focus once a killed one leaves.
 *
 * Recency first, position second. The operator's own back-and-forth is the best
 * available statement of where they want to be next, so the most recently focused
 * surviving session wins - preferring one in the pane just vacated, because closing
 * the last tab of a split should settle inside that split rather than jump across the
 * screen. Only when recency knows nothing does this fall back to reading the layout:
 * visible-in-the-layout, then anywhere in the layout, then any surviving session in
 * the project - a tab that is merely stacked behind another is still a better landing
 * spot than nothing.
 *
 * A recent id that has left the layout ranks *below* those two fallbacks rather than
 * above: something on screen beats something that is not, whatever the operator last
 * touched.
 *
 * Focus only moves when the killed session held it; killing an unfocused tab must not
 * yank the operator elsewhere.
 */
export function nextActiveAfterKill(
  { layout, sessions, killedId, projectId, activeId, recent = [], paneIds = [] }: NextActiveInput,
): string | null {
  if (activeId !== killedId) return activeId
  const surviving = sessions.filter(session =>
    session.id !== killedId
    && session.project_id === projectId
    && session.state !== 'exited' && session.state !== 'crashed')
  const survivingIds = new Set(surviving.map(session => session.id))
  // Dead and killed ids are skipped rather than trusted: the stack records what was
  // focused, and says nothing about what is still alive.
  const recentLive = recent.filter(id => id !== killedId && survivingIds.has(id))
  const pane = new Set(paneIds)
  const placed = new Set(terminalIds(layout))
  return recentLive.find(id => pane.has(id) && placed.has(id))
    ?? recentLive.find(id => placed.has(id))
    ?? visibleTerminalIds(layout).find(id => survivingIds.has(id))
    ?? terminalIds(layout).find(id => survivingIds.has(id))
    ?? recentLive[0]
    ?? surviving[0]?.id
    ?? null
}
