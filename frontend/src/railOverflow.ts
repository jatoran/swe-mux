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

/** Widest the overflow popover is allowed to grow, before the viewport clamp. */
export const RAIL_POPOVER_MAX_WIDTH_PX = 520
/** Most of the viewport height the popover may take, so it never blankets the composer. */
export const RAIL_POPOVER_MAX_HEIGHT_RATIO = 0.5
const RAIL_POPOVER_MARGIN_PX = 8
const RAIL_POPOVER_MIN_HEIGHT_PX = 120

export interface RailPopoverRect { left: number; right: number; top: number }

/**
 * Place the overflow popover above its `+N` chip, growing upward in rows.
 *
 * Right-aligned to the chip rather than left-aligned to it, because the chip is at the row's
 * trailing end: aligning the panel's right edge with it puts the panel over the rail it came
 * from on a wide pane and flush to the rail's edge on a phone, which is the same rule in both
 * places rather than two.
 *
 * Not `anchoredPopoverStyle`: that one caps at 340px because it lays out a *list*, and a wrap
 * grid of rail chips at 340px is a column of one chip per row.
 */
export function railPopoverStyle(
  anchor: RailPopoverRect,
  viewport: { width: number; height: number },
): Record<string, string> {
  const width = Math.min(RAIL_POPOVER_MAX_WIDTH_PX, Math.max(160, viewport.width - RAIL_POPOVER_MARGIN_PX * 2))
  const left = clamp(anchor.right - width, RAIL_POPOVER_MARGIN_PX, Math.max(RAIL_POPOVER_MARGIN_PX, viewport.width - width - RAIL_POPOVER_MARGIN_PX))
  const above = anchor.top - RAIL_POPOVER_MARGIN_PX - 4
  const maxHeight = Math.max(
    RAIL_POPOVER_MIN_HEIGHT_PX,
    Math.min(above, Math.round(viewport.height * RAIL_POPOVER_MAX_HEIGHT_RATIO)),
  )
  // Hugs the chip while there is room above it, and stops hugging rather than growing off
  // the top of the screen: the minimum height is a floor, so a rail high in a short viewport
  // would otherwise be handed a panel whose first row is above the window.
  const bottom = clamp(
    viewport.height - anchor.top + 4,
    RAIL_POPOVER_MARGIN_PX,
    Math.max(RAIL_POPOVER_MARGIN_PX, viewport.height - maxHeight - RAIL_POPOVER_MARGIN_PX),
  )
  return {
    left: `${Math.round(left)}px`,
    bottom: `${Math.round(bottom)}px`,
    width: `${Math.round(width)}px`,
    maxHeight: `${Math.round(maxHeight)}px`,
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
