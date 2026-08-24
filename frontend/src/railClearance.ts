/**
 * How far a viewport-anchored floating message has to sit above the bottom edge to clear
 * the command rail.
 *
 * The Action rail is the bottom-most thing in a terminal pane, so on a maximised window it
 * ends exactly at the bottom of the viewport - and every app-level floating message is
 * anchored to that same corner (`.interaction-hud`, `.notification-toast`, `.toast-stack`).
 * They therefore landed *on the rail*: the HUD merely obscured chips for its 1.4s, while
 * the notification toast takes pointer events, so a tap meant for a chip opened the
 * notifications panel instead.
 *
 * The lift cannot be written as a constant. Rail height is the configured row count times
 * `--rail-row-h`, and that variable is itself one of three density steps with a separate
 * set of mobile values - so the only honest number is a measured one. The measurement is
 * published once, on the root element, and each message adds it to its own `bottom`.
 */

/** The rail's box, reduced to what the calculation actually reads. */
export interface RailBox {
  top: number
  bottom: number
}

/**
 * A rail counts only if it is *at* the bottom of the viewport.
 *
 * Panes split top/bottom both have a rail, and the upper one is nowhere near the corner a
 * toast is pinned to; lifting a toast by its height would strand the toast in mid-air. Two
 * pixels of slack absorbs subpixel layout, nothing more.
 */
export const RAIL_BOTTOM_TOLERANCE_PX = 2

/**
 * The clearance, in CSS pixels, that puts a bottom-anchored message above every rail that
 * reaches the bottom of the viewport.
 *
 * Measured from the viewport's bottom edge to the rail's *top*, so the returned number
 * already includes any gap between the rail and the edge. With several qualifying rails -
 * panes split left/right each end at the bottom - the tallest wins, which over-lifts a
 * message sitting over the shorter one rather than leaving it covered.
 */
export function railClearancePx(rails: readonly RailBox[], viewportHeight: number): number {
  let clearance = 0
  for (const rail of rails) {
    if (viewportHeight - rail.bottom > RAIL_BOTTOM_TOLERANCE_PX) continue
    clearance = Math.max(clearance, viewportHeight - rail.top)
  }
  // A rail taller than the viewport, or a stale rect from a pane mid-teardown, must not
  // push a message off the top of the screen.
  return Math.max(0, Math.min(clearance, viewportHeight))
}

export const RAIL_CLEARANCE_PROPERTY = '--rail-clearance'

const rails = new Set<HTMLElement>()
let observer: ResizeObserver | null = null
let frame: number | null = null
let published = -1

function measure(): void {
  frame = null
  if (typeof document === 'undefined') return
  const boxes: RailBox[] = []
  for (const rail of rails) {
    // A pane that unmounted without its cleanup running (or one hidden behind a stacked
    // tab) contributes nothing rather than a zeroed rect at the top of the screen.
    if (!rail.isConnected) continue
    const rect = rail.getBoundingClientRect()
    if (rect.height <= 0) continue
    boxes.push({ top: rect.top, bottom: rect.bottom })
  }
  const clearance = Math.round(railClearancePx(boxes, window.innerHeight))
  if (clearance === published) return
  published = clearance
  document.documentElement.style.setProperty(RAIL_CLEARANCE_PROPERTY, `${clearance}px`)
}

/** Coalesced, because one pane resize fires the observer for the rail and for the body. */
function schedule(): void {
  if (frame !== null || typeof window === 'undefined') return
  frame = window.requestAnimationFrame(measure)
}

/**
 * Track one pane's rail. Returns the unregister function.
 *
 * The rail's own size is not enough to watch: splitting a pane moves a sibling's rail
 * without changing its height, and that is exactly the case where the clearance changes.
 * `document.body` covers window resizes and every layout change that reaches the document
 * box; `visualViewport` covers the soft keyboard, which resizes neither.
 */
export function registerRailClearance(rail: HTMLElement): () => void {
  if (typeof window === 'undefined' || typeof ResizeObserver === 'undefined') return () => {}
  if (!observer) {
    observer = new ResizeObserver(schedule)
    observer.observe(document.body)
    window.addEventListener('resize', schedule)
    window.visualViewport?.addEventListener('resize', schedule)
  }
  rails.add(rail)
  observer.observe(rail)
  schedule()
  return () => {
    rails.delete(rail)
    observer?.unobserve(rail)
    schedule()
  }
}
