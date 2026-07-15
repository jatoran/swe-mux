export type MobileVerticalDrag = 'smart' | 'terminal' | 'application' | 'disabled'
export type MobileScrollDirection = 'natural' | 'wheel'
export type MobileLongPress = 'context_menu' | 'disabled'
export type MobileDragTarget = 'terminal' | 'application' | 'disabled'

export type MobileInputSettings = {
  verticalDrag: MobileVerticalDrag
  scrollDirection: MobileScrollDirection
  scrollSensitivity: number
  longPress: MobileLongPress
}

export const defaultMobileInputSettings: MobileInputSettings = {
  verticalDrag: 'smart',
  scrollDirection: 'natural',
  scrollSensitivity: 1,
  longPress: 'context_menu',
}

export function mobileInputSettings(config: Record<string, unknown>): MobileInputSettings {
  return {
    verticalDrag: ['smart', 'terminal', 'application', 'disabled'].includes(String(config.mobile_vertical_drag))
      ? config.mobile_vertical_drag as MobileVerticalDrag
      : 'smart',
    scrollDirection: config.mobile_scroll_direction === 'wheel' ? 'wheel' : 'natural',
    scrollSensitivity: Math.max(.25, Math.min(4, Number(config.mobile_scroll_sensitivity) || 1)),
    longPress: config.mobile_long_press === 'disabled' ? 'disabled' : 'context_menu',
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
