// Bringing a source line to the top of a Continuity viewport without moving the selection.
//
// Continuity 0.2.25 reveals ranges against rendered projection geometry, but `revealRange`
// deliberately makes its range the primary selection. A heading jump is a reading action: it
// must leave the caret alone and keep one source row above the target for swe-mux's heading trail.
//
// The public viewport API exposes the visible source-line window and scroll offset, not a direct
// viewport-only line jump. A projected heading can render larger, wrapped rows carry a measured
// hanging indent, and the active scroll owner differs by pointer type. The host therefore uses
// `visibleLineRange()`, measured from the projection's own client rectangles, as a feedback loop:
// scroll, measure which lines landed on screen, correct, and repeat. The loop needs no pixel model
// of the document, only a step size good enough to converge, which each round re-measures from
// what the previous one bought.
//
// Pure and DOM-free: `ProjectResource` supplies the four editor calls behind `ViewportScroller`,
// which is also what lets a test drive it over a simulated document whose lines are not all the
// same height.

export type LineWindow = { startLine: number; endLine: number }

export const HEADING_JUMP = {
  /**
   * Source rows of lead-in kept above the target.
   *
   * One, not zero: the heading trail is an opaque strip over the note's first row, and a
   * target landed at the very top edge reads underneath it. A row of lead is normally the
   * blank line Markdown puts before a heading, so it costs nothing on screen.
   */
  lead: 1,
  /**
   * Corrections per jump. Each round re-measures its own step size, so ordinary notes land in
   * two or three; this is the guard against a document that keeps changing height underneath.
   */
  maxSteps: 12,
  /** A correction this small is either landed or clamped. Either way there is nothing to do. */
  minStepPx: 1,
  /** Bounds on a measured row height, so one degenerate sample cannot fling the viewport. */
  minLineHeightPx: 2,
}

/**
 * A viewport that reports which source lines it shows and can be scrolled by pixels.
 *
 * `window` returns `null` when there is nothing to measure - before first layout, or while the
 * host holds the editor in a `display:none` tab - and the loop does nothing in that case rather
 * than scrolling blind.
 */
export type ViewportScroller = {
  window: () => LineWindow | null
  top: () => number
  scrollTo: (top: number) => void
  viewportHeight: () => number
}

/** Rows in a window, at least one: a single line taller than the viewport still counts. */
function windowRows(view: LineWindow): number {
  return Math.max(1, view.endLine - view.startLine + 1)
}

/** First step size, before any correction has been measured: the visible average. */
export function seedLineHeight(view: LineWindow, viewportHeightPx: number): number {
  return Math.max(HEADING_JUMP.minLineHeightPx, viewportHeightPx / windowRows(view))
}

/**
 * Row height implied by what the last correction actually bought.
 *
 * Beats any estimate of it, because it is measured over the region the loop just crossed and so
 * already contains that region's headings, wrapped rows, and the editor's own compensation ramp
 * (which moves the projection slightly further than the scroller). A move that changed the
 * scroll offset without changing the first visible line measures nothing and keeps `fallback`.
 */
export function measuredLineHeight(scrolledPx: number, linesMoved: number, fallback: number): number {
  if (!linesMoved || !scrolledPx) return fallback
  return Math.max(HEADING_JUMP.minLineHeightPx, Math.abs(scrolledPx / linesMoved))
}

/**
 * Pixels to scroll to bring `targetLine` to the top of `view`, or 0 once it is there.
 *
 * Landed means the target is *on screen* and within `lead` rows of the top. The visibility half
 * is not redundant: when the line above the target wraps to more rows than the viewport has,
 * `lead` alone would report success on a window the target is not in, so that case aims at the
 * top edge instead and gives up its lead-in.
 */
export function headingJumpDelta(targetLine: number, view: LineWindow, lineHeightPx: number): number {
  const offset = targetLine - view.startLine
  const visible = targetLine >= view.startLine && targetLine <= view.endLine
  if (visible && offset >= 0 && offset <= HEADING_JUMP.lead) return 0
  const lead = targetLine <= view.endLine ? HEADING_JUMP.lead : 0
  return (offset - lead) * lineHeightPx
}

/**
 * Scroll until `targetLine` is at the top of the viewport, correcting against what each step
 * actually showed.
 *
 * Stops early on three conditions that all mean "nothing left to do": the target has landed, the
 * scroller is clamped (a heading near the end of a note cannot be brought to the top, and the
 * editor's own ramp guarantees it is on screen at the floor anyway), or the correction has become
 * smaller than a pixel.
 */
export function scrollLineIntoView(scroller: ViewportScroller, targetLine: number): void {
  let lineHeight = 0
  let previous: { top: number; startLine: number } | null = null
  for (let step = 0; step < HEADING_JUMP.maxSteps; step += 1) {
    const view = scroller.window()
    if (!view) return
    const top = scroller.top()
    if (previous) {
      lineHeight = measuredLineHeight(top - previous.top, view.startLine - previous.startLine, lineHeight)
    }
    if (lineHeight <= 0) lineHeight = seedLineHeight(view, scroller.viewportHeight())
    const delta = headingJumpDelta(targetLine, view, lineHeight)
    if (Math.abs(delta) < HEADING_JUMP.minStepPx) return
    const next = Math.max(0, top + delta)
    if (Math.abs(next - top) < HEADING_JUMP.minStepPx) return
    previous = { top, startLine: view.startLine }
    scroller.scrollTo(next)
    // Clamped at the scroller's own floor or ceiling: another identical request cannot help.
    if (scroller.top() === top) return
  }
}
