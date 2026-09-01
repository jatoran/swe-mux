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
 *   as a diagram. Both the axis and the side are chosen from where the targets actually
 *   are, so the same beat shape works for the fleet column on the left, the side panel on
 *   the right, and the command rail along the bottom.
 * - **A label may move, a target may not.** When two labels want the same band of
 *   pixels the second one is pushed along the gutter, never the target's ring, because
 *   the ring is the only part whose position carries meaning.
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

/**
 * Which edge of the targets the label gutter stands on.
 *
 * Four rather than two because a set of targets can be arranged either way, and a row of
 * them has no gutter to either side: the space between two rail chips is exactly where a
 * label would have to go, and the free space at the ends of the strip is nowhere near the
 * thing being named. See `gutterSide`.
 */
export type Side = 'left' | 'right' | 'top' | 'bottom'

/** Whether a side puts the gutter beside the targets or above/below them. */
export const isVertical = (side: Side): boolean => side === 'left' || side === 'right'

export type Placed = {
  callout: Callout
  target: Box
  /** The chip's own box, in viewport coordinates. The view positions from these and
   *  nothing else, which is what lets one style rule serve all four sides. */
  left: number
  top: number
  width: number
  height: number
  /** Where the leader line meets the chip: the midpoint of whichever edge faces the
   *  targets. Kept separate from the box because the wire and the chip are drawn by
   *  different elements and only this module knows which edge is the near one. */
  x: number
  y: number
  side: Side
}

/** The gap between the targets' edge and the leader lines' shared run. */
const ELBOW_GAP = 22
/** And between that run and the chips themselves. */
const COLUMN_GAP = 30
/** Breathing room between two chips that would otherwise touch, along the gutter. */
const STACK_GAP = 7
/** Keep the whole column inside the frame. */
const EDGE = 8

export type Viewport = { width: number; height: number }

/**
 * How far wider than tall a set of targets has to be before it counts as a row.
 *
 * A margin rather than a bare comparison, because an ambiguous arrangement - two boxes on
 * a rough diagonal - should keep the side gutter that every beat used before there was a
 * choice. The two arrangements this actually has to separate are not close to the line: a
 * command rail is about 800x30, a session row's fields are about 200x40 stacked nine
 * deep.
 */
const ROW_RATIO = 1.6

/**
 * Which edge of the targets the gutter goes on.
 *
 * Measured from the targets rather than configured, because one beat shape labels the
 * fleet column (left of the screen, so the gutter is on the right), the side panel (right
 * of the screen, so it is on the left) and the command rail (a strip along the bottom, so
 * the gutter is above it).
 *
 * Two readings, in order:
 *
 * - **The axis follows the targets' arrangement.** Several targets laid out in a row have
 *   no gutter to either side of them - the space beside any one of them is the next one,
 *   and the free space at the ends of the strip is off where nothing is being named. So a
 *   row is labelled from above or below, and everything else keeps the side gutter. One
 *   target has no arrangement and always takes a side.
 * - **The side is free space, not the targets' centre.** A centre reading is right for a
 *   narrow column and wrong for a wide card: a beat labelling rows inside a
 *   thousand-pixel dialog has its centre near the middle of the screen, reads as
 *   left-hand chrome, and puts every label in the sixty pixels left over on the right -
 *   clamped, on top of each other, at the end of leader lines the width of the card.
 *   Asking which side has room gives the same answer as the centre rule wherever the
 *   centre rule was right, and a usable one where it was not.
 */
export function gutterSide(boxes: Box[], viewport: Viewport): Side {
  const span = unionBox(boxes)
  if (!span) return 'right'
  const gap = COLUMN_GAP + ELBOW_GAP
  if (boxes.length > 1 && span.width > span.height * ROW_RATIO) {
    return span.top - gap >= viewport.height - span.bottom - gap ? 'top' : 'bottom'
  }
  return viewport.width - span.right - gap >= span.left - gap ? 'right' : 'left'
}

/**
 * Lay a set of measured callouts out in one gutter.
 *
 * `width` and `height` are the chips' own measured sizes, which only the view knows;
 * passing them in is what keeps this function pure. The order of the result follows the
 * targets along the gutter rather than the caller's order, because the deconfliction pass
 * is only correct on a sorted list and a caller ordering its notes by importance is a
 * reasonable thing to do.
 *
 * `side` is an override, and it exists for the walk: that mode places one label at a time,
 * so measuring the side from the single active target would let the gutter jump from one
 * stop to the next. The caller decides the side once from the whole set and passes it in.
 */
export function placeCallouts(
  entries: Array<{ callout: Callout; target: Box; width: number; height: number }>,
  viewport: Viewport,
  side: Side = gutterSide(entries.map(entry => entry.target), viewport),
): Placed[] {
  if (!entries.length) return []
  const span = unionBox(entries.map(entry => entry.target))!
  return isVertical(side)
    ? besideTargets(entries, viewport, span, side)
    : aboveTargets(entries, viewport, span, side)
}

/** The original layout: one column of chips beside the targets, deconflicted downwards. */
function besideTargets(
  entries: Array<{ callout: Callout; target: Box; width: number; height: number }>,
  viewport: Viewport,
  span: Box,
  side: Side,
): Placed[] {
  const gap = COLUMN_GAP + ELBOW_GAP
  const column = side === 'right' ? span.right + gap : span.left - gap
  const sorted = [...entries].sort((left, right) => left.target.cy - right.target.cy)
  const placed: Placed[] = []
  let floor = EDGE
  for (const entry of sorted) {
    const top = Math.max(floor, Math.min(
      entry.target.cy - entry.height / 2,
      viewport.height - entry.height - EDGE,
    ))
    const near = side === 'right'
      ? Math.min(column, viewport.width - entry.width - EDGE)
      : Math.max(column, entry.width + EDGE)
    placed.push({
      callout: entry.callout,
      target: entry.target,
      left: side === 'right' ? near : near - entry.width,
      top,
      width: entry.width,
      height: entry.height,
      x: near,
      y: top + entry.height / 2,
      side,
    })
    floor = top + entry.height + STACK_GAP
  }
  return placed
}

/** The row layout: one line of chips above (or below) the targets, deconflicted sideways. */
function aboveTargets(
  entries: Array<{ callout: Callout; target: Box; width: number; height: number }>,
  viewport: Viewport,
  span: Box,
  side: Side,
): Placed[] {
  const gap = COLUMN_GAP + ELBOW_GAP
  const line = side === 'top' ? span.top - gap : span.bottom + gap
  const sorted = [...entries].sort((left, right) => left.target.cx - right.target.cx)
  const placed: Placed[] = []
  let floor = EDGE
  for (const entry of sorted) {
    const left = Math.max(floor, Math.min(
      entry.target.cx - entry.width / 2,
      viewport.width - entry.width - EDGE,
    ))
    const near = side === 'top'
      ? Math.max(line, entry.height + EDGE)
      : Math.min(line, viewport.height - entry.height - EDGE)
    placed.push({
      callout: entry.callout,
      target: entry.target,
      left,
      top: side === 'top' ? near - entry.height : near,
      width: entry.width,
      height: entry.height,
      x: left + entry.width / 2,
      y: near,
      side,
    })
    floor = left + entry.width + STACK_GAP
  }
  return placed
}

/**
 * The leader line: out of the target's near edge, along to the gutter, over to the
 * label's line, and in to its edge.
 *
 * An elbow rather than a straight diagonal because nine diagonals cross each other and
 * an orthogonal set does not, and because the long run doubles as the gutter's own spine
 * once more than one line shares it.
 */
export function wirePath(item: Placed): string {
  const outward = item.side === 'right' || item.side === 'bottom' ? 1 : -1
  const elbow = COLUMN_GAP - 8
  const anchor = anchorPoint(item)
  if (isVertical(item.side)) {
    return `M ${round(anchor.x)} ${round(anchor.y)} H ${round(item.x - outward * elbow)} `
      + `V ${round(item.y)} H ${round(item.x - outward * 6)}`
  }
  return `M ${round(anchor.x)} ${round(anchor.y)} V ${round(item.y - outward * elbow)} `
    + `H ${round(item.x)} V ${round(item.y - outward * 6)}`
}

const round = (value: number): number => Math.round(value * 10) / 10

/** Where the leader line leaves the target, for the little anchor dot. */
export function anchorPoint(item: Placed): { x: number; y: number } {
  if (item.side === 'right') return { x: item.target.right + 4, y: item.target.cy }
  if (item.side === 'left') return { x: item.target.left - 4, y: item.target.cy }
  if (item.side === 'top') return { x: item.target.cx, y: item.target.top - 4 }
  return { x: item.target.cx, y: item.target.bottom + 4 }
}

/**
 * Which way the radar band crosses a piece of chrome.
 *
 * From the chrome's own shape rather than from the gutter, because the band is about the
 * thing being scanned and the gutter is about where there is room to write: a band
 * travelling down an 800x30 command rail has crossed it before it has started, and reads
 * as a flash rather than as a scan.
 */
export const sweepAxis = (column: Box): 'down' | 'across' =>
  (column.width > column.height * ROW_RATIO ? 'across' : 'down')

/**
 * When each chip wakes, as a fraction of the radar band's travel.
 *
 * The band is one element crossing the column once; the chips are not animated with it,
 * they are scheduled against it. Deriving the delay from the target's own position is
 * what makes the two look like one effect rather than two that happen to overlap.
 */
export function sweepDelays(boxes: Box[], column: Box, travelMs: number): number[] {
  const across = sweepAxis(column) === 'across'
  const length = Math.max(1, across ? column.width : column.height)
  return boxes.map(box => {
    const fraction = ((across ? box.cx - column.left : box.cy - column.top)) / length
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
