/**
 * Labelling live chrome, as geometry rather than as a component.
 *
 * The director's original spotlight pointed at exactly one thing, which is right for a
 * beat that says "press this" and useless for a beat that says "here is what a row is
 * made of". A session row carries seven facts in about forty pixels of height; naming
 * them one at a time would be seven beats nobody would sit through, and naming them in
 * prose asks the visitor to map words onto glyphs themselves.
 *
 * So a beat may carry several **callouts**, each a selector plus a label, and this module
 * turns them into positions. It is deliberately separate from the view and from the
 * runner: everything here is a pure function of boxes and a viewport, which is what lets
 * the placement rules be unit-tested without a browser. The view measures, calls
 * `placeCallouts`, and draws. Nothing here touches the DOM.
 *
 * Two rules shape all of it:
 *
 * - **The labels go in one gutter, on the side away from the chrome.** Nine labels
 *   scattered around their targets is a ransom note; one column with leader lines reads
 *   as a diagram. The side is chosen from where the targets actually are, so the same
 *   beat shape works for the fleet column on the left and the side panel on the right.
 * - **A label may move, a target may not.** When two labels want the same band of
 *   pixels the lower one is pushed down, never the target's ring, because the ring is
 *   the only part whose position carries meaning.
 */

/** A measured target, in viewport coordinates. */
export type Box = {
  left: number
  top: number
  width: number
  height: number
  right: number
  bottom: number
  cx: number
  cy: number
}

/** One label, and the chrome it names. First visible selector wins, as everywhere else. */
export type Callout = {
  at: string[]
  label: string
  /** A shorter second clause, drawn dimmer on the same chip. */
  sub?: string
}

/**
 * How a beat's callouts arrive.
 *
 * `glitch` is the default and the cheapest: each chip clips itself in over three steps
 * with a one-frame colour split. `sweep` runs a radar band down the column first and
 * wakes each chip as the band passes it, which is the one that reads as "look at this
 * whole list". `walk` dims everything and visits one target at a time, for a beat that
 * wants the eye held rather than a diagram. `blueprint` draws brackets and dimension
 * ticks with no motion at all, and is what survives a screenshot.
 */
export type RevealMode = 'glitch' | 'sweep' | 'walk' | 'blueprint'

/** Everything a beat can draw over the app besides its one spotlight ring. */
export type Show = {
  notes?: Callout[]
  reveal?: RevealMode
  /** The chrome the radar band crosses. Defaults to the notes' own bounding column. */
  sweep?: string[]
  /** A chord to show on the keycap HUD, one cap per entry, in order. */
  keys?: string[]
  /** Chrome whose value changed under the visitor, flashed where it sits. Each entry is
   *  a list of alternates, as everywhere else here: first visible match wins. */
  shimmer?: string[][]
  /** Chrome that has just appeared, marked with a stepped arrival flash. */
  arrive?: string[][]
  /** Scanlines and bloom, for the length of this beat only. */
  crt?: boolean
}

export type Placed = {
  callout: Callout
  target: Box
  /** The chip's left edge (`side: 'right'`) or right edge (`side: 'left'`). */
  x: number
  /** The chip's vertical centre, after deconfliction. */
  y: number
  top: number
  side: 'left' | 'right'
}

/** The gap between the targets' edge and the leader line's vertical run. */
const ELBOW_GAP = 22
/** And between that run and the chips themselves. */
const COLUMN_GAP = 30
/** Vertical breathing room between two chips that would otherwise touch. */
const STACK_GAP = 7
/** Keep the whole column inside the frame. */
const EDGE = 8

export type Viewport = { width: number; height: number }

/**
 * Which side of the targets the gutter goes on.
 *
 * Measured from the targets rather than configured, because the same beat shape labels
 * the fleet column (left of the screen, so the gutter is on the right) and the side
 * panel (right of the screen, so it is on the left). A beat that spans both is treated
 * as left-hand chrome, which is the common case and never draws off screen.
 */
export function gutterSide(boxes: Box[], viewport: Viewport): 'left' | 'right' {
  if (!boxes.length) return 'right'
  const centre = boxes.reduce((total, box) => total + box.cx, 0) / boxes.length
  return centre > viewport.width * 0.58 ? 'left' : 'right'
}

/**
 * Lay a set of measured callouts out in one gutter.
 *
 * `widths` and `heights` are the chips' own measured sizes, which only the view knows;
 * passing them in is what keeps this function pure. The order of the result follows the
 * targets down the screen rather than the caller's order, because the deconfliction pass
 * is only correct on a sorted list and a caller ordering its notes by importance is a
 * reasonable thing to do.
 */
export function placeCallouts(
  entries: Array<{ callout: Callout; target: Box; width: number; height: number }>,
  viewport: Viewport,
): Placed[] {
  if (!entries.length) return []
  const boxes = entries.map(entry => entry.target)
  const side = gutterSide(boxes, viewport)
  const column = side === 'right'
    ? Math.max(...boxes.map(box => box.right)) + COLUMN_GAP + ELBOW_GAP
    : Math.min(...boxes.map(box => box.left)) - COLUMN_GAP - ELBOW_GAP

  const sorted = [...entries].sort((left, right) => left.target.cy - right.target.cy)
  const placed: Placed[] = []
  let floor = EDGE
  for (const entry of sorted) {
    const top = Math.max(floor, Math.min(
      entry.target.cy - entry.height / 2,
      viewport.height - entry.height - EDGE,
    ))
    placed.push({
      callout: entry.callout,
      target: entry.target,
      x: side === 'right'
        ? Math.min(column, viewport.width - entry.width - EDGE)
        : Math.max(column, entry.width + EDGE),
      y: top + entry.height / 2,
      top,
      side,
    })
    floor = top + entry.height + STACK_GAP
  }
  return placed
}

/**
 * The leader line: out of the target's edge, along to the gutter, up or down to the
 * label's line, and in to its edge.
 *
 * An elbow rather than a straight diagonal because nine diagonals cross each other and
 * an orthogonal set does not, and because the vertical run doubles as the column's own
 * spine once more than one line shares it.
 */
export function wirePath(item: Placed): string {
  const outward = item.side === 'right' ? 1 : -1
  const start = item.side === 'right' ? item.target.right + 4 : item.target.left - 4
  const elbow = item.x - outward * (COLUMN_GAP - 8)
  const end = item.side === 'right' ? item.x - 6 : item.x + 6
  return `M ${round(start)} ${round(item.target.cy)} H ${round(elbow)} `
    + `V ${round(item.y)} H ${round(end)}`
}

const round = (value: number): number => Math.round(value * 10) / 10

/** Where the leader line leaves the target, for the little anchor dot. */
export const anchorPoint = (item: Placed): { x: number; y: number } => ({
  x: item.side === 'right' ? item.target.right + 4 : item.target.left - 4,
  y: item.target.cy,
})

/**
 * When each chip wakes, as a fraction of the radar band's travel.
 *
 * The band is one element crossing the column once; the chips are not animated with it,
 * they are scheduled against it. Deriving the delay from the target's own position is
 * what makes the two look like one effect rather than two that happen to overlap.
 */
export function sweepDelays(boxes: Box[], column: Box, travelMs: number): number[] {
  const height = Math.max(1, column.height)
  return boxes.map(box => {
    const fraction = (box.cy - column.top) / height
    return Math.max(0, Math.min(1, fraction) * travelMs - 70)
  })
}

/** The union of several boxes, for the band when a beat names no column of its own. */
export function unionBox(boxes: Box[]): Box | null {
  if (!boxes.length) return null
  const left = Math.min(...boxes.map(box => box.left))
  const top = Math.min(...boxes.map(box => box.top))
  const right = Math.max(...boxes.map(box => box.right))
  const bottom = Math.max(...boxes.map(box => box.bottom))
  return {
    left, top, right, bottom,
    width: right - left, height: bottom - top,
    cx: (left + right) / 2, cy: (top + bottom) / 2,
  }
}

/** A DOMRect, as the plain box everything above works in. */
export const boxOf = (rect: DOMRect): Box => ({
  left: rect.left, top: rect.top, width: rect.width, height: rect.height,
  right: rect.right, bottom: rect.bottom,
  cx: rect.left + rect.width / 2, cy: rect.top + rect.height / 2,
})
