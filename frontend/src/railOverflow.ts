export type RailScrollDirection = -1 | 1

export interface RailScrollMetrics {
  scrollLeft: number
  scrollWidth: number
  clientWidth: number
}

export interface RailOverflowState {
  left: boolean
  right: boolean
}

export const RAIL_EDGE_WIDTH_PX = 28
export const RAIL_PAGE_OVERLAP_PX = 44
const RAIL_EDGE_TOLERANCE_PX = 1

/**
 * How far a touch travels before it is the rail's horizontal pan rather than a press on
 * whatever it landed on.
 *
 * Lives here, with the rail's other pure arithmetic, because two modules have to agree on
 * it: `RailScroller` starts panning at this distance, and `railKeyRepeat` stops treating
 * the same press as a candidate for hold-to-repeat at it. If they disagreed, the window
 * between the two thresholds would be a swipe that also spammed an arrow key.
 */
export const RAIL_PAN_SLOP_PX = 6

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value))
}

// ---------------------------------------------------------------------------
// Pinned rail + overflow popover
// ---------------------------------------------------------------------------

/**
 * Subpixel slack when deciding whether one more chip fits.
 *
 * Widths come from `getBoundingClientRect`, so a row whose chips sum to exactly its width
 * routinely measures a few hundredths over it at fractional device pixel ratios. Without
 * the slack the last chip on a perfectly-fitting row drops into the popover, and the `+N`
 * chip appears on a rail that visibly has room — which is the one thing this split is
 * supposed to never do.
 */
export const RAIL_FIT_TOLERANCE_PX = 0.5

export interface RailFitMetrics {
  /** Rendered width of every chip on the row, in configured order. */
  widths: readonly number[]
  /** The flex gap between two chips. */
  gap: number
  /** Room the row has for chips, with padding and pinned furniture already taken out. */
  available: number
  /** Width of the fixed-width `+N` chip. */
  overflowWidth: number
}

/**
 * How many leading chips stay on the row; everything after goes to that row's popover.
 *
 * A row that fits keeps every chip and is told so by getting the full count back, which is
 * what lets the caller draw no `+N` at all — a fully-fitting row must look exactly as it did
 * before this split existed, down to not reserving the chip's width "just in case".
 *
 * Once something *has* to be hidden the `+N` chip is certain, so its width is spent before
 * the first chip is placed rather than discovered at the end. That is also why the count can
 * legitimately be zero: on a rail narrower than its first chip, the popover holds everything
 * and the row holds one fixed-width control that always fits.
 */
export function railFitCount({ widths, gap, available, overflowWidth }: RailFitMetrics): number {
  if (!widths.length) return 0
  let total = 0
  for (let index = 0; index < widths.length; index += 1) total += widths[index] + (index ? gap : 0)
  if (total <= available + RAIL_FIT_TOLERANCE_PX) return widths.length

  const room = available - overflowWidth - gap
  let used = 0
  let count = 0
  for (const width of widths) {
    const next = used + width + (count ? gap : 0)
    if (next > room + RAIL_FIT_TOLERANCE_PX) break
    used = next
    count += 1
  }
  return count
}

/** Widest the overflow popover's wrap grid may grow on a desktop, before the view clamp. */
export const RAIL_POPOVER_MAX_WIDTH_PX = 520
/** Widest a drop-up's *list* may grow on a desktop. Narrower than the grid on purpose:
 *  a list of one-line rows past this reads as a stretched menu rather than a picker. */
export const RAIL_DROPUP_MAX_WIDTH_PX = 340
/** Most of the visible height a rail overlay may take, so it never blankets the composer. */
export const RAIL_POPOVER_MAX_HEIGHT_RATIO = 0.5
/** Below this the layout is the phone's, and a rail overlay takes half the screen at most.
 *  The same breakpoint the workspace projection and the device-class settings use. */
export const RAIL_OVERLAY_MOBILE_MAX_PX = 760
/** How much of a phone's screen a rail overlay may cover. Operator-chosen: the terminal it
 *  is opened over has to stay readable beside it, which a near-full-width panel prevents. */
export const RAIL_OVERLAY_MOBILE_WIDTH_RATIO = 0.5
const RAIL_POPOVER_MARGIN_PX = 8
const RAIL_POPOVER_MIN_HEIGHT_PX = 120
const RAIL_OVERLAY_MIN_WIDTH_PX = 160

/** The rail's trailing cluster: `right` is the edge to align to, `top` the edge to open from. */
export interface RailPopoverRect { left: number; right: number; top: number }

/**
 * The part of the page the operator can actually see, in layout-viewport coordinates.
 *
 * This is `visualViewport`, not `window.innerWidth/innerHeight`, and the difference is the
 * whole of the soft keyboard. The app declares `interactive-widget=resizes-visual`, so an
 * open keyboard leaves the layout viewport at full height and shrinks only this one - which
 * means every bound drawn against `innerHeight` describes a rectangle extending well behind
 * the keyboard. `top`/`left` carry the visual viewport's offset for the case where the page
 * is also scrolled within it.
 */
export interface RailOverlayView { left: number; top: number; width: number; height: number }

/** Where a rail overlay goes, in layout-viewport coordinates, before any containing block
 *  is taken into account (`railOverlayPlacement.ts` does that half). `bottom` is where the
 *  panel's bottom *edge* sits, not a CSS inset - the two differ the moment a transformed
 *  ancestor is in the way, and naming the edge is what makes that conversion checkable. */
export interface RailOverlayBox { left: number; bottom: number; width: number; maxHeight: number }

/**
 * Place a command-rail overlay above its anchor, growing upward, inside what is visible.
 *
 * One function for the overflow popover and for every drop-up, because they are one control
 * as far as placement goes: both hang off the rail at the bottom of a pane, both must open
 * upward, both must stay right-aligned to the rail's trailing edge, and both were getting the
 * soft keyboard wrong in the same way. Only the desktop width cap differs, because one lays
 * out a wrap grid and the other a list.
 *
 * The anchor for the overflow popover is the rail's trailing *cluster* rather than the `+N`
 * chip inside it: the panel's right edge then lands on the rail's trailing edge on every row,
 * whatever that row is carrying. Aligning to the chip left the panel a gear-width short of
 * the edge on the one row that carries a gear, which is two placements for one control.
 *
 * Not `anchoredPopoverStyle`: that one is the account/resource popovers' rule and knows
 * nothing about the rail, the keyboard, or a phone's width budget.
 */
export function railOverlayBox(
  anchor: RailPopoverRect,
  view: RailOverlayView,
  maxWidth: number,
): RailOverlayBox {
  const viewRight = view.left + view.width
  const viewBottom = view.top + view.height
  const room = view.width - RAIL_POPOVER_MARGIN_PX * 2
  const cap = view.width <= RAIL_OVERLAY_MOBILE_MAX_PX
    ? Math.floor(view.width * RAIL_OVERLAY_MOBILE_WIDTH_RATIO)
    : maxWidth
  const width = Math.max(Math.min(cap, room), Math.min(RAIL_OVERLAY_MIN_WIDTH_PX, room))
  // A phone aligns every rail overlay to the screen's trailing edge rather than to its own
  // trigger. On a wide pane, hanging off the trigger is the useful thing - it says which
  // control opened this. On a phone at half width it only means a picker opened from a chip
  // in the middle of the rail lands in the middle of the screen, so two overlays opened a
  // second apart appear in two places. The desktop keeps the trigger; the phone gets one
  // place, which is the same edge the rail's own trailing cluster sits on.
  const trailing = viewRight - width - RAIL_POPOVER_MARGIN_PX
  const left = view.width <= RAIL_OVERLAY_MOBILE_MAX_PX
    ? Math.max(view.left + RAIL_POPOVER_MARGIN_PX, trailing)
    : clamp(anchor.right - width, view.left + RAIL_POPOVER_MARGIN_PX, Math.max(view.left + RAIL_POPOVER_MARGIN_PX, trailing))
  const above = anchor.top - (view.top + RAIL_POPOVER_MARGIN_PX) - 4
  const maxHeight = Math.max(
    RAIL_POPOVER_MIN_HEIGHT_PX,
    Math.min(above, Math.round(view.height * RAIL_POPOVER_MAX_HEIGHT_RATIO)),
  )
  // Hugs the anchor while there is room above it, and stops hugging rather than growing off
  // the top of what is visible: the minimum height is a floor, so a rail high in a short
  // view would otherwise be handed a panel whose first row is off screen.
  const bottom = clamp(
    anchor.top - 4,
    Math.min(viewBottom, view.top + maxHeight + RAIL_POPOVER_MARGIN_PX),
    viewBottom - RAIL_POPOVER_MARGIN_PX,
  )
  return {
    left: Math.round(left),
    bottom: Math.round(bottom),
    width: Math.round(width),
    maxHeight: Math.round(maxHeight),
  }
}

/** Commands that are a departure from the rail, spelled out rather than prefix-guessed. */
const RAIL_POPOVER_CLOSING_COMMANDS: readonly string[] = ['clipboard.open', 'processes.open', 'resources.open']

/**
 * Whether firing this command should collapse the overflow popover.
 *
 * The popover deliberately survives a selection — two-click confirms and repeat-tap keys have
 * to work in place — so the exceptions are the selections that *move you somewhere else*: a
 * drawer tab, a drawer section, or the prompt library. Leaving the panel open over a surface
 * the same tap just opened would cover the thing it opened.
 */
export function railPopoverClosingCommand(command: string): boolean {
  return command.startsWith('drawer.')
    || command.startsWith('prompts.')
    || RAIL_POPOVER_CLOSING_COMMANDS.includes(command)
}

/** Which edge controls are useful at the strip's current scroll position. */
export function railOverflowState(metrics: RailScrollMetrics): RailOverflowState {
  const maximum = Math.max(0, metrics.scrollWidth - metrics.clientWidth)
  const position = clamp(metrics.scrollLeft, 0, maximum)
  return {
    left: position > RAIL_EDGE_TOLERANCE_PX,
    right: position < maximum - RAIL_EDGE_TOLERANCE_PX,
  }
}

/**
 * Page the rail while preserving one item-width of context, then settle on an
 * item boundary. Item offsets are normalized to the strip's leading padding.
 */
export function railPageTarget(
  metrics: RailScrollMetrics,
  itemOffsets: readonly number[],
  direction: RailScrollDirection,
): number {
  const maximum = Math.max(0, metrics.scrollWidth - metrics.clientWidth)
  if (!maximum) return 0

  const distance = Math.max(24, metrics.clientWidth - RAIL_PAGE_OVERLAP_PX)
  const rawTarget = clamp(metrics.scrollLeft + direction * distance, 0, maximum)
  if (rawTarget === 0 || rawTarget === maximum) return rawTarget

  if (direction > 0) {
    const boundary = itemOffsets.find(offset => offset >= rawTarget)
    return clamp(boundary ?? maximum, 0, maximum)
  }

  for (let index = itemOffsets.length - 1; index >= 0; index -= 1) {
    if (itemOffsets[index] <= rawTarget) return clamp(itemOffsets[index], 0, maximum)
  }
  return 0
}

/** Keep a focused item clear of the overlay controls. */
export function railFocusTarget(
  metrics: RailScrollMetrics,
  itemStart: number,
  itemEnd: number,
): number {
  const maximum = Math.max(0, metrics.scrollWidth - metrics.clientWidth)
  const visibleStart = metrics.scrollLeft + RAIL_EDGE_WIDTH_PX
  const visibleEnd = metrics.scrollLeft + metrics.clientWidth - RAIL_EDGE_WIDTH_PX
  if (itemStart < visibleStart) return clamp(itemStart - RAIL_EDGE_WIDTH_PX, 0, maximum)
  if (itemEnd > visibleEnd) return clamp(itemEnd - metrics.clientWidth + RAIL_EDGE_WIDTH_PX, 0, maximum)
  return clamp(metrics.scrollLeft, 0, maximum)
}
