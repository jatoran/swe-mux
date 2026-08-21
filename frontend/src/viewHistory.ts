// The navigation rung of "back": where you were looking, once nothing is layered on top.
//
// The dismiss stack answers back while an overlay is open. With nothing open there was
// no rung below it, so on a phone the platform back gesture backgrounded the whole PWA:
// a user reading a session had no way back to the tab they came from. Android's own
// convention is overlays first, then navigation, then leave, and this is the middle one.
//
// Deliberately an in-memory ring rather than one history entry per view. Chrome's
// history-manipulation intervention marks entries pushed without a user gesture as
// skippable, and focus here moves programmatically all the time - a spawn, a resume, a
// branch, a closing pane handing focus to its neighbour. Those entries would quietly
// stop being poppable and back would leave the app at random moments. Keeping the ring
// in memory leaves `systemBack.ts` with the single sentinel it already had, so this
// feature adds no new history entries at all.
//
// Four rules decide whether this is usable or maddening, and all four are why the module
// is pure and DOM-free:
//
//  1. **A traversal consumes.** Going back is not a navigation and never re-records.
//     Without that the ring is a cycle, back can never leave the app, and the user is
//     worse off than with the bug this fixes.
//  2. **Recording is MRU-distinct, and the ring holds only places you are not.** A view
//     already in it moves rather than repeating, and arriving somewhere drops that
//     destination out, because back can never mean "stay where you are". Together those
//     bound the depth by how many distinct views were visited rather than by how often
//     two of them were flipped between: ten presses is a limit, not a typical walk.
//  3. **Dead entries are skipped when popped, not pruned when pushed.** A pane closes, a
//     session ends, a Project is removed; what is reachable is only knowable at the
//     moment back is pressed, and the owner answers that question (`ViewNavigator`).
//  4. **The traversal echo is recognized by identity, not suppressed by a flag.**
//     Restoring a view makes the recorder observe a move *to* that view, which would
//     re-record the one just left and ping-pong forever. A "skip the next record" flag
//     would be silently eaten by a restore that changed nothing, taking a real
//     navigation with it, so the entry just handed out is remembered and compared.

import type { BackTarget } from './dismissStack.ts'

/** A view that can be returned to: a leaf id, plus the Project whose layout holds it. */
export type ViewEntry = { projectId: string; viewId: string }

/** Where focus is now, which may be nowhere in particular. */
export type ViewPosition = { projectId: string; viewId: string | null }

/**
 * How far back the gesture may walk before it hands the press to the platform.
 *
 * A bound rather than a preference: whatever the ring holds, back leaves the app within
 * ten presses from any state, which is what keeps "back always eventually exits" true.
 */
export const VIEW_HISTORY_LIMIT = 10

function sameViewPosition(left: ViewPosition | null, right: ViewPosition | null): boolean {
  if (!left || !right) return left === right
  return left.projectId === right.projectId && left.viewId === right.viewId
}

/** Whether an entry still names something the workspace can show. See rule 3. */
export type ViewAlive = (entry: ViewEntry) => boolean

export type ViewHistory = ReturnType<typeof createViewHistory>

export function createViewHistory(limit: number = VIEW_HISTORY_LIMIT) {
  // Oldest first; the end is where back reads from.
  let entries: ViewEntry[] = []
  const listeners = new Set<() => void>()
  // The entry the last `take` handed out, cleared by the record it explains. Rule 4.
  let restored: ViewEntry | null = null

  const notify = () => { for (const listener of [...listeners]) listener() }

  /**
   * Note that focus moved, so that the view being left becomes reachable again.
   *
   * Called from the *committed* focus pair rather than from the places that set it:
   * only the settled value is what the user is actually looking at.
   */
  const record = (previous: ViewPosition, next: ViewPosition): void => {
    if (sameViewPosition(next, restored)) { restored = null; return }
    restored = null
    // A position naming no view is not somewhere to return to. It is a real state (a
    // Project with nothing open, a moment mid-reconciliation) but not a destination.
    if (!previous.viewId) return
    if (sameViewPosition(previous, next)) return
    const entry: ViewEntry = { projectId: previous.projectId, viewId: previous.viewId }
    // Rule 2, both halves: the view being left moves to the top rather than repeating,
    // and the one being arrived at leaves the ring - it is where the user now is, so it
    // is not somewhere back could take them.
    const kept = entries.filter(existing => !sameViewPosition(existing, entry) && !sameViewPosition(existing, next))
    kept.push(entry)
    entries = kept.slice(Math.max(0, kept.length - limit))
    notify()
  }

  /** How many entries back could actually reach right now. Never mutates: `sync` calls it. */
  const liveDepth = (alive: ViewAlive): number => entries.filter(alive).length

  /**
   * Remove and return the most recent reachable entry, dropping dead ones on the way.
   *
   * Dropping them here rather than leaving them is what stops a run of closed panes from
   * costing one swallowed back press each.
   */
  const take = (alive: ViewAlive): ViewEntry | null => {
    let taken: ViewEntry | null = null
    let changed = false
    while (entries.length) {
      const entry = entries.pop()!
      changed = true
      if (alive(entry)) { taken = entry; break }
    }
    restored = taken
    if (changed) notify()
    return taken
  }

  /**
   * Re-announce without changing anything.
   *
   * Liveness is a question about state this store cannot see, so the owner says when
   * that state moved - a pane closed, a Project went away, the layout mode flipped.
   * Otherwise the history sentinel stays armed against entries that name nothing.
   */
  const touch = (): void => { notify() }

  return {
    record,
    liveDepth,
    take,
    touch,
    subscribe: (listener: () => void): (() => void) => {
      listeners.add(listener)
      return () => { listeners.delete(listener) }
    },
    /** Oldest first. Diagnostics only. */
    entries: (): ViewEntry[] => entries.map(entry => ({ ...entry })),
  }
}

/**
 * The workspace side of a traversal, supplied by whoever owns the layouts.
 *
 * `enabled` is how the mobile-only rule is expressed. On the desktop the tabs are on
 * screen and clickable, and a permanently armed sentinel would stop the browser's own
 * Back button from ever leaving the site - the same reason the docked sidebar and drawer
 * are not dismiss levels there. Recording still happens on every layout, so a phone
 * rotating across the 760 px boundary and back keeps a coherent ring instead of a wiped
 * one.
 */
export type ViewNavigator = {
  enabled: () => boolean
  alive: ViewAlive
  go: (entry: ViewEntry) => void
}

/**
 * Put the view ring underneath a dismiss stack, as one target `systemBack` can hold.
 *
 * Overlays always win. A layered surface is painted over the workspace, so a back press
 * over it can only mean something about it; stepping the workspace underneath would
 * change a view the user cannot even see. This is the same precedence `resolveGestureCommand`
 * applies to swipes, and the reason the composite is *not* what feeds `gestureOverlayDepth`:
 * that function asks "is an overlay open", and answering it with a depth that includes
 * navigation history would resolve every gesture slot to nothing forever.
 */
export function composeBackTarget(
  dismiss: BackTarget,
  history: ViewHistory,
  navigator: ViewNavigator,
): BackTarget {
  const viewDepth = () => (navigator.enabled() ? history.liveDepth(navigator.alive) : 0)
  return {
    depth: () => dismiss.depth() + viewDepth(),
    pop: () => {
      if (dismiss.depth() > 0) return dismiss.pop()
      if (!navigator.enabled()) return 'empty'
      const entry = history.take(navigator.alive)
      if (!entry) return 'empty'
      navigator.go(entry)
      return 'popped'
    },
    subscribe: listener => {
      const offDismiss = dismiss.subscribe(listener)
      const offHistory = history.subscribe(listener)
      return () => { offDismiss(); offHistory() }
    },
  }
}

/** Hot-reloadable switch (Settings → Input → touch gestures). Absent means on. */
export function viewBackEnabled(config: Record<string, unknown>): boolean {
  return config.mobile_back_view_history !== false
}

/** The app-wide ring. Tests build their own with `createViewHistory`. */
export const viewHistory = createViewHistory()
