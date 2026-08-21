// Which session the operator was on *before* this one, per Project.
//
// Closing the focused pane has to hand focus somewhere, and layout order is the wrong
// answer: it lands on whatever happens to be leftmost, which is almost never the thing
// the operator was working in a moment ago. A back-and-forth between two panes -
// paste in one, read the other, close the first - used to dump focus on a third
// session that had been sitting untouched since the morning.
//
// So this keeps a small most-recently-focused stack. Deliberately:
//
//  * **Per Project.** Focus never crosses a Project boundary on a close
//    (`nextActiveAfterKill` filters by `project_id`), so a fleet-wide stack would
//    only ever offer candidates that are then discarded.
//  * **Not keyed by pane.** A session moves between panes - dragged into a split,
//    stacked as a tab, dissolved back out - so a pane-keyed stack would strand its
//    entries the moment the layout changed. The pane preference is applied at *read*
//    time instead, against the layout as it stands, which is the only version of the
//    pane that is still true.
//  * **Bounded and in memory.** It answers "where was I just now", which is a
//    question about this sitting at this device. Persisting it would have it survive
//    a reload as a claim about a session the operator has not looked at since.
//
// Kept free of runtime imports so it can be unit tested under the node runner.

/** Ids per Project, most recently focused first. */
export type SessionFocusHistory = Readonly<Record<string, readonly string[]>>

/**
 * How many sessions back one Project remembers.
 *
 * Small on purpose. Everything past the first couple of entries is only reached when
 * the ones in front of it have all been killed or ended, by which point "most
 * recently focused" has stopped meaning anything the operator would recognise, and
 * the layout-order fallbacks are the more honest answer.
 */
export const SESSION_FOCUS_HISTORY_LIMIT = 8

/** Push a session to the front of its Project's stack, moving it if already present. */
export function recordFocusedSession(
  history: SessionFocusHistory, projectId: string, sessionId: string,
): SessionFocusHistory {
  if (!projectId || !sessionId) return history
  const current = history[projectId] || []
  if (current[0] === sessionId) return history
  const next = [sessionId, ...current.filter(id => id !== sessionId)].slice(0, SESSION_FOCUS_HISTORY_LIMIT)
  return { ...history, [projectId]: next }
}

/** Drop a session from every Project's stack. Called when it is killed, so a dead id
 *  cannot sit at the head of the stack shadowing the live session behind it. */
export function forgetFocusedSession(
  history: SessionFocusHistory, sessionId: string,
): SessionFocusHistory {
  const next: Record<string, readonly string[]> = {}
  let changed = false
  for (const [projectId, ids] of Object.entries(history)) {
    const kept = ids.filter(id => id !== sessionId)
    if (kept.length !== ids.length) changed = true
    if (kept.length) next[projectId] = kept
  }
  return changed ? next : history
}

/** One Project's stack, most recent first. Empty when nothing has been focused there. */
export function recentFocusedSessions(
  history: SessionFocusHistory, projectId: string,
): readonly string[] {
  return history[projectId] || []
}
