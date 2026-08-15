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
}

export type ApplicationTouchScrollResult = TerminalScrollSteps & {
  /** Pixel distance the application is expected to move for the emitted reports. */
  distance: number
}

export type ApplicationTouchScrollProfile = {
  rowsPerReport: number
}

/**
 * Convert finger travel into wheel reports for a viewport the application owns.
 *
 * The unit is the application's, not the terminal's. A report is worth `rowsPerReport`
 * rows to the CLI that receives it — Claude Code moves three — so tracking the finger
 * means sending a third as many reports as the scrollback path would for the same
 * travel. Getting that unit wrong is what made a drag scroll at three times the finger.
 *
 * Nothing is rate limited or discarded here, and the sub-report remainder carries the
 * same way `terminalScrollSteps` carries a sub-row one. Shedding a fast flick's excess
 * belongs to `terminalWheelPacing`, which bounds it against the CLI's real repaint rate
 * and a capped queue; a second limiter on this side can only throw away travel a drag
 * asked for, which reads as a viewport that ignores the finger.
 */
export function applicationTouchScroll(
  state: ApplicationTouchScrollState,
  deltaPixels: number,
  rowHeight: number,
  profile: ApplicationTouchScrollProfile,
): ApplicationTouchScrollResult {
  if (rowHeight <= 0 || !deltaPixels) {
    return { steps: 0, remainder: rowHeight > 0 ? state.pixels : 0, distance: 0 }
  }

  // Reversing direction abandons a pending report from the old direction. A wheel tick
  // after the finger reverses must never continue scrolling the previous way.
  const carried = state.pixels && Math.sign(state.pixels) !== Math.sign(deltaPixels)
    ? deltaPixels
    : state.pixels + deltaPixels

  const reportDistance = rowHeight * Math.max(1, Math.trunc(profile.rowsPerReport))
  const budget = terminalScrollSteps(carried, reportDistance)
  return { ...budget, distance: budget.steps * reportDistance }
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
  // `|| 0` only to fold the negative zero `Math.trunc` returns for sub-row upward
  // travel, which is a scroll of nothing that still carries a direction downstream.
  const steps = Math.trunc(pixels / rowHeight) || 0
  return { steps, remainder: pixels - steps * rowHeight }
}

/**
 * Where a vertical drag goes: the application's own viewport, or xterm's buffer.
 *
 * `smart` follows the live mouse mode, and it has to stay that way. It is tempting to add
 * a fallback here — a Claude pane declares `owns_scroll_viewport`, so why not route to the
 * application whenever the harness says it owns one, whatever the mode says? Because
 * forwarding is a `WheelEvent` at xterm, and xterm only turns that into a mouse report
 * while a mouse mode is active. With none active it falls back to its alternate-buffer
 * behaviour and sends **cursor keys** instead, which in an agent composer walks the prompt
 * history and can replace what the reader was typing. A dead gesture is better than that.
 *
 * When this genuinely reads `terminal` on an alternate screen with no scrollback, the
 * gesture does nothing, and the fault is upstream: the child's mouse modes went missing.
 * That was a real bug — a bounded attach replay dropped them on any deep session — and it
 * is fixed where it belongs, in the daemon's replay preamble (`STICKY_PRIVATE_MODES`),
 * not by guessing here.
 */
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
