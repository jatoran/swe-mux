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

/**
 * Touch-scroll acceleration.
 *
 * A drag tracks the finger 1:1 at reading speed (content staying under the thumb is the whole
 * point of direct manipulation, and anything faster makes a small correction overshoot) and
 * gains up to `maxGain` as the flick gets faster, which is how a native scroller crosses a long
 * document without a dozen swipes. The user's own `scrollSensitivity` multiplies on top and is
 * deliberately left out of the velocity measurement: the ramp reads the finger, not the setting,
 * so raising sensitivity scales the whole curve rather than shifting where acceleration starts.
 *
 * The ramp is on velocity rather than on per-event distance because velocity is what the user
 * controls. A 120 Hz phone reports half the movement per event at twice the rate; measuring
 * distance would read that as a slow drag, while velocity reads both as the same gesture.
 */
export const TOUCH_SCROLL_ACCELERATION = {
  /** px/ms at or below which the drag is 1:1. A deliberate reading drag sits under this. */
  slowVelocity: 0.4,
  /** px/ms at which the gain saturates. A flick clears it easily; a drag never reaches it. */
  fastVelocity: 2.4,
  maxGain: 3,
  /**
   * Weight of the newest sample in the running velocity. Enough smoothing that the gain does
   * not flicker between two events of one gesture, little enough that a flick is at full gain
   * within a few of them: an acceleration that arrives late reads as the scroll ignoring you.
   */
  smoothing: 0.4,
}

/** The running finger speed in px/ms, smoothed against per-event jitter. */
export function smoothTouchVelocity(previous: number, deltaPixels: number, elapsedMs: number): number {
  // Two events at the same timestamp (coalesced, or a clock with no resolution left) carry no
  // velocity information at all. Keeping the previous value is the honest reading; dividing by
  // zero would report an infinitely fast flick for a finger that had barely moved.
  if (!(elapsedMs > 0)) return previous
  const sample = Math.abs(deltaPixels) / elapsedMs
  const { smoothing } = TOUCH_SCROLL_ACCELERATION
  return previous * (1 - smoothing) + sample * smoothing
}

/** The multiplier a drag at `velocity` px/ms earns: 1 at reading speed, `maxGain` at a flick. */
export function touchScrollGain(velocity: number): number {
  const { slowVelocity, fastVelocity, maxGain } = TOUCH_SCROLL_ACCELERATION
  const ramp = (velocity - slowVelocity) / (fastVelocity - slowVelocity)
  return 1 + (maxGain - 1) * Math.max(0, Math.min(1, ramp))
}

export type TerminalScrollSteps = {
  /** Whole rows to scroll now. */
  steps: number
  /** Sub-row travel to carry into the next move event. */
  remainder: number
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
