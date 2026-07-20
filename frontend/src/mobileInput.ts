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

export function terminalScrollLines(deltaPixels: number, rowHeight: number): number {
  if (!deltaPixels || rowHeight <= 0) return 0
  return Math.trunc(deltaPixels / rowHeight) || (deltaPixels > 0 ? 1 : -1)
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
