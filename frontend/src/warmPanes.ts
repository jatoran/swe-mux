/**
 * Which terminals stay mounted after you switch away from them.
 *
 * Switching tabs used to unmount the pane, which disposes xterm, drops the PTY
 * socket, and makes the next visit a cold attach: the daemon replays the retained
 * buffer and xterm parses it in time-sliced chunks, so returning to a long session
 * is *watched* redrawing. Worst for a CLI whose transcript lives in scrollback (how
 * mux launches Codex, `tui.alternate_screen="never"`), whose retained bytes are real
 * lines that each allocate and scroll rather than repaints of one alternate screen.
 *
 * Keeping a pane mounted removes the work instead of hiding it — there is no replay
 * at all — but a live pane costs a socket, an xterm instance, and its scrollback, so
 * the set is bounded and ordered by recency rather than kept for every tab.
 *
 * Pure and separate from App so the eviction order is testable without a DOM.
 */

/**
 * How many *hidden* terminals stay mounted. Panes that are visible are always
 * mounted and never counted here, so this is the extra cost, not the total.
 *
 * Three covers the flow the bound exists for — switching between a couple of
 * sessions while a third runs — without holding a large workspace's every tab
 * open. Above this the memory is real and the benefit is a tab you last touched
 * long enough ago that a cold attach is not surprising.
 */
export const WARM_TERMINAL_PANES = 3

/** Mobile avoids hidden PTY sockets: their output consumes metered bandwidth even
 * while another pane is visible. Desktop keeps the tab-switch latency benefit. */
export function warmPaneBudget(profile: 'desktop' | 'mobile'): number {
  return profile === 'mobile' ? 0 : WARM_TERMINAL_PANES
}

/** Cap on remembered recency, so a long session cannot grow this without bound. */
const HISTORY_LIMIT = 64

/**
 * Move every id in `visited` to the front of the recency list, most recent last.
 *
 * Called with the set of panes currently active in their stacks, so a layout that
 * activates several at once (restoring a workspace, closing a split) records them
 * all rather than silently keeping only one.
 */
export function recordPaneVisits(
  history: readonly string[],
  visited: readonly string[],
  limit: number = HISTORY_LIMIT,
): string[] {
  const next = [...visited].reverse()
  for (const id of history) if (!next.includes(id)) next.push(id)
  return next.slice(0, limit)
}

/**
 * The hidden terminals to keep mounted, most recently used first.
 *
 * `active` panes are excluded because they are mounted anyway; counting them would
 * spend the budget on panes that are already on screen and evict the ones this
 * exists to keep. `present` restricts the result to panes the layout still has, so
 * a closed tab cannot hold a slot that a live one needs.
 */
export function warmPaneIds(
  history: readonly string[],
  active: readonly string[],
  present: readonly string[],
  limit: number = WARM_TERMINAL_PANES,
): string[] {
  if (limit <= 0) return []
  const live = new Set(present)
  const onScreen = new Set(active)
  const warm: string[] = []
  for (const id of history) {
    if (warm.length >= limit) break
    if (onScreen.has(id) || !live.has(id) || warm.includes(id)) continue
    warm.push(id)
  }
  return warm
}
