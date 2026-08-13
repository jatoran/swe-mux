export type MobileVerticalDrag = 'smart' | 'terminal' | 'application' | 'disabled'
export type MobileScrollDirection = 'natural' | 'wheel'
export type MobileLongPress = 'context_menu' | 'disabled'
export type MobileDragTarget = 'terminal' | 'application' | 'disabled'

export type TerminalCell = { column: number; row: number }
export type TerminalWordRange = { start: number; length: number }
export type TerminalSelectionSpan = { column: number; row: number; length: number }

export type MobileInputSettings = {
  verticalDrag: MobileVerticalDrag
  scrollDirection: MobileScrollDirection
  scrollSensitivity: number
  longPress: MobileLongPress
  autoCopySelection: boolean
}

export const defaultMobileInputSettings: MobileInputSettings = {
  verticalDrag: 'smart',
  scrollDirection: 'natural',
  scrollSensitivity: 1,
  longPress: 'context_menu',
  autoCopySelection: true,
}

export function mobileInputSettings(config: Record<string, unknown>): MobileInputSettings {
  return {
    verticalDrag: ['smart', 'terminal', 'application', 'disabled'].includes(String(config.mobile_vertical_drag))
      ? config.mobile_vertical_drag as MobileVerticalDrag
      : 'smart',
    scrollDirection: config.mobile_scroll_direction === 'wheel' ? 'wheel' : 'natural',
    scrollSensitivity: Math.max(.25, Math.min(4, Number(config.mobile_scroll_sensitivity) || 1)),
    longPress: config.mobile_long_press === 'disabled' ? 'disabled' : 'context_menu',
    autoCopySelection: config.terminal_auto_copy_selection !== false,
  }
}

export function touchWheelDelta(previousY: number, currentY: number, settings: MobileInputSettings): number {
  const fingerDelta = currentY - previousY
  const direction = settings.scrollDirection === 'natural' ? -1 : 1
  return fingerDelta * direction * settings.scrollSensitivity
}

export type TerminalScrollSteps = {
  /** Whole rows to scroll now. */
  steps: number
  /** Sub-row travel to carry into the next move event. */
  remainder: number
}

export type ApplicationTouchScrollState = {
  /** Finger travel not yet converted into an application wheel report. */
  pixels: number
  /** Pointer-event timestamp of the last report sent during this gesture. */
  lastReportAt: number
}

export type ApplicationTouchScrollResult = TerminalScrollSteps & {
  lastReportAt: number
  /** Pixel distance the application is expected to move for the emitted reports. */
  distance: number
  /** Excess high-velocity travel intentionally not banked for delayed scrolling. */
  droppedPixels: number
}

export type ApplicationTouchScrollProfile = {
  rowsPerReport: number
  reportIntervalMs: number
}

/**
 * Some application-owned viewports consume several rows per wheel report or accelerate
 * reports that arrive too close together. The harness registry publishes that measured
 * profile. Excess fast-flick travel is shed instead of queued, because replaying it later
 * is the runaway-scroll failure.
 */
export function applicationTouchScroll(
  state: ApplicationTouchScrollState,
  deltaPixels: number,
  rowHeight: number,
  profile: ApplicationTouchScrollProfile,
  now: number,
): ApplicationTouchScrollResult {
  if (rowHeight <= 0 || !deltaPixels) {
    return { steps: 0, remainder: rowHeight > 0 ? state.pixels : 0, lastReportAt: state.lastReportAt, distance: 0, droppedPixels: 0 }
  }

  // Reversing direction abandons a pending report from the old direction. A wheel tick
  // after the finger reverses must never continue scrolling the previous way.
  const carried = state.pixels && Math.sign(state.pixels) !== Math.sign(deltaPixels)
    ? deltaPixels
    : state.pixels + deltaPixels

  const rowsPerReport = Math.max(1, Math.trunc(profile.rowsPerReport))
  const reportIntervalMs = Math.max(0, Math.trunc(profile.reportIntervalMs))
  if (rowsPerReport === 1 && reportIntervalMs === 0) {
    const budget = terminalScrollSteps(carried, rowHeight)
    return {
      ...budget,
      lastReportAt: budget.steps ? now : state.lastReportAt,
      distance: budget.steps * rowHeight,
      droppedPixels: 0,
    }
  }

  const reportDistance = rowHeight * rowsPerReport
  const bounded = Math.max(-reportDistance, Math.min(reportDistance, carried))
  const droppedPixels = Math.max(0, Math.abs(carried) - Math.abs(bounded))
  const intervalElapsed = now < state.lastReportAt || now - state.lastReportAt >= reportIntervalMs
  if (Math.abs(bounded) < reportDistance || !intervalElapsed) {
    return { steps: 0, remainder: bounded, lastReportAt: state.lastReportAt, distance: 0, droppedPixels }
  }

  const steps = Math.sign(bounded)
  return {
    steps,
    remainder: bounded - steps * reportDistance,
    lastReportAt: now,
    distance: steps * reportDistance,
    droppedPixels,
  }
}

/**
 * Split a pixel budget into whole rows and the remainder to carry.
 *
 * Carrying it is what makes a drag track the finger. Truncating each event on its own throws
 * away up to a row of travel *per event*, which on a 120 Hz phone reporting 10px at a time is
 * most of the gesture; the old fallback of "any movement is at least one row" corrected for
 * that by over-scrolling every slow drag instead. A running remainder needs neither.
 */
export function terminalScrollSteps(pixels: number, rowHeight: number): TerminalScrollSteps {
  if (rowHeight <= 0) return { steps: 0, remainder: 0 }
  const steps = Math.trunc(pixels / rowHeight)
  return { steps, remainder: pixels - steps * rowHeight }
}

export function mobileDragTarget(mode: MobileVerticalDrag, mouseTracking: boolean): MobileDragTarget {
  if (mode === 'disabled') return 'disabled'
  if (mode === 'smart') return mouseTracking ? 'application' : 'terminal'
  return mode
}

export function terminalCellAtPoint(
  clientX: number,
  clientY: number,
  bounds: Pick<DOMRect, 'left' | 'top' | 'width' | 'height'>,
  columns: number,
  rows: number,
  viewportRow: number,
): TerminalCell | null {
  if (columns < 1 || rows < 1 || bounds.width <= 0 || bounds.height <= 0) return null
  const column = Math.max(0, Math.min(columns - 1, Math.floor((clientX - bounds.left) / bounds.width * columns)))
  const visibleRow = Math.max(0, Math.min(rows - 1, Math.floor((clientY - bounds.top) / bounds.height * rows)))
  return { column, row: viewportRow + visibleRow }
}

export function terminalWordRange(text: string, column: number): TerminalWordRange {
  if (!text) return { start: 0, length: 0 }
  let cursor = Math.max(0, Math.min(text.length - 1, column))
  if (/\s/.test(text[cursor])) {
    const right = text.slice(cursor).search(/\S/)
    if (right >= 0) cursor += right
    else {
      const left = text.slice(0, cursor).search(/\S+\s*$/)
      if (left < 0) return { start: cursor, length: 1 }
      cursor = left
    }
  }
  let start = cursor
  let end = cursor + 1
  while (start > 0 && !/\s/.test(text[start - 1])) start -= 1
  while (end < text.length && !/\s/.test(text[end])) end += 1
  return { start, length: end - start }
}

export function terminalSelectionSpan(
  anchorStart: TerminalCell,
  anchorLength: number,
  current: TerminalCell,
  columns: number,
): TerminalSelectionSpan {
  const anchorOffset = anchorStart.row * columns + anchorStart.column
  const anchorEnd = anchorOffset + Math.max(1, anchorLength)
  const currentOffset = current.row * columns + current.column
  const start = Math.min(anchorOffset, currentOffset)
  const end = Math.max(anchorEnd, currentOffset + 1)
  return { column: start % columns, row: Math.floor(start / columns), length: end - start }
}
