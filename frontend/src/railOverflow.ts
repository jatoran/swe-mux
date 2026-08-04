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

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value))
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
