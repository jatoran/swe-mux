// Touch-gesture recognition for the mobile workspace. Edge- and top-anchored swipes
// are intentionally unsupported: on Android those belong to the OS (back / home /
// notification shade), so the reliable channels are mid-screen single-finger
// horizontal swipes and two-finger gestures. Vertical single-finger drags stay with
// the terminal (scrollback / application wheel) and are never claimed here.
//
// This module is pure so the classification thresholds can be unit-tested without a DOM.

export type GestureSlot =
  | 'swipe_left'
  | 'swipe_right'
  | 'two_finger_swipe_left'
  | 'two_finger_swipe_right'
  | 'two_finger_swipe_up'
  | 'two_finger_swipe_down'
  | 'two_finger_tap'

export const GESTURE_SLOTS: GestureSlot[] = [
  'swipe_left',
  'swipe_right',
  'two_finger_swipe_left',
  'two_finger_swipe_right',
  'two_finger_swipe_up',
  'two_finger_swipe_down',
  'two_finger_tap',
]

export const GESTURE_LABELS: Record<GestureSlot, string> = {
  swipe_left: 'Swipe left',
  swipe_right: 'Swipe right',
  two_finger_swipe_left: 'Two-finger swipe left',
  two_finger_swipe_right: 'Two-finger swipe right',
  two_finger_swipe_up: 'Two-finger swipe up',
  two_finger_swipe_down: 'Two-finger swipe down',
  two_finger_tap: 'Two-finger tap',
}

// Command id per slot, or '' to disable the gesture.
export type MobileGestureSettings = Record<GestureSlot, string>

export const defaultMobileGestureSettings: MobileGestureSettings = {
  swipe_left: 'mobileTab.next',
  swipe_right: 'mobileTab.previous',
  // Directional, matching where each panel lives: swiping right drags the
  // left-edge sidebar in, swiping left (starting from the right edge) drags the
  // right-edge utility drawer in. Both were sidebar.toggle before that drawer
  // existed; config.py migrates the redundant default (schema 17).
  two_finger_swipe_left: 'drawer.toggle',
  two_finger_swipe_right: 'sidebar.toggle',
  // Vertical two-finger swipes were dead input before these slots existed. Down
  // = push the on-screen keyboard away (read/select mode), which is the control
  // touch users reach for most; up = the current Project's notes. Both are
  // rebindable to any command from Settings like every other slot.
  two_finger_swipe_up: 'notes.open',
  two_finger_swipe_down: 'terminal.keyboardToggle',
  two_finger_tap: 'palette.open',
}

// A swipe must travel this far along its dominant axis, and beat the cross-axis by
// this ratio, before it counts. A two-finger tap must stay under the movement and
// duration ceilings so a slow two-finger swipe never reads as a tap.
export const GESTURE_THRESHOLDS = {
  swipeMinDistance: 48,
  swipeAxisRatio: 1.4,
  tapMaxMovement: 16,
  tapMaxDurationMs: 400,
  // A single-finger tab fling completes quickly. The terminal's long-press text
  // selection starts with a ~1.5s hold, so capping swipe duration keeps a slow
  // selection drag from being mistaken for a tab change.
  singleFingerSwipeMaxDurationMs: 600,
}

export type GestureSample = {
  pointerCount: number
  dx: number
  dy: number
  durationMs: number
}

type HorizontalScrollElement = Pick<Element, 'matches' | 'scrollWidth' | 'clientWidth'>

const KNOWN_HORIZONTAL_SCROLLERS = '.terminal-action-rail, .stack-tabs, .drawer-tabs, .overflow-rail-touch-drag, .voice-strip'

/**
 * Return whether a touch's composed event path crosses a horizontal scroller.
 * The composed path is required here: a window listener sees a shadow-DOM touch
 * target retargeted to its host, so walking `parentElement` cannot see an
 * embedded component's internal scrolling strip.
 */
export function pathOwnsHorizontalScroll<T extends HorizontalScrollElement>(
  path: readonly T[],
  overflowX: (element: T) => string,
): boolean {
  for (const element of path) {
    if (element.matches(KNOWN_HORIZONTAL_SCROLLERS)) return true
    const overflow = overflowX(element)
    if ((overflow === 'auto' || overflow === 'scroll') && element.scrollWidth > element.clientWidth + 1) return true
  }
  return false
}

export function classifyGesture(sample: GestureSample): GestureSlot | null {
  const { pointerCount, dx, dy, durationMs } = sample
  const absX = Math.abs(dx)
  const absY = Math.abs(dy)
  const horizontalSwipe =
    absX >= GESTURE_THRESHOLDS.swipeMinDistance && absX >= absY * GESTURE_THRESHOLDS.swipeAxisRatio
  // Vertical only ever resolves for two fingers: a single-finger vertical drag
  // belongs to the terminal (scrollback / application wheel) and is never claimed.
  const verticalSwipe =
    absY >= GESTURE_THRESHOLDS.swipeMinDistance && absY >= absX * GESTURE_THRESHOLDS.swipeAxisRatio

  if (pointerCount >= 2) {
    if (
      absX <= GESTURE_THRESHOLDS.tapMaxMovement &&
      absY <= GESTURE_THRESHOLDS.tapMaxMovement &&
      durationMs <= GESTURE_THRESHOLDS.tapMaxDurationMs
    ) {
      return 'two_finger_tap'
    }
    if (horizontalSwipe) return dx < 0 ? 'two_finger_swipe_left' : 'two_finger_swipe_right'
    if (verticalSwipe) return dy < 0 ? 'two_finger_swipe_up' : 'two_finger_swipe_down'
    return null
  }

  if (pointerCount === 1 && horizontalSwipe && durationMs <= GESTURE_THRESHOLDS.singleFingerSwipeMaxDurationMs) {
    return dx < 0 ? 'swipe_left' : 'swipe_right'
  }
  return null
}

export type OverlayPanels = { sidebarOpen: boolean; drawerOpen: boolean }

// The command an in-overlay back swipe runs. Registered like any other command so it is
// also bindable to a key and reachable from the palette.
export const BACK_COMMAND = 'nav.back'

// Rightward is "back" for the same reason the button sits at the left of a heading: the
// motion pushes the current level off to the right and reveals what it covered. Both
// finger counts, matching the swipe-away-close override. Edge-anchored swipes are not an
// option here — Android owns them — so this is the mid-screen swipe.
const BACK_SLOTS = new Set<GestureSlot>(['swipe_right', 'two_finger_swipe_right'])
const HORIZONTAL_SLOTS = new Set<GestureSlot>(['swipe_left', 'swipe_right', 'two_finger_swipe_left', 'two_finger_swipe_right'])

// While the left sidebar is open, either horizontal direction closes it. The
// right-edge drawer keeps its directional rule: a rightward swipe pushes it
// away. These overrides are keyed on the open panel rather than the slot's
// binding, so they also work when that slot is unbound.
export function resolveGestureCommand(
  slot: GestureSlot,
  settings: MobileGestureSettings,
  panels: OverlayPanels,
  swipeAwayClose: boolean,
  overlay: { depth: number; enabled: boolean } = { depth: 0, enabled: false },
): string {
  // An open overlay level shadows everything below it, panels included: it is painted on
  // top, so a gesture over it can only mean something about it. The back swipe pops one
  // level and **every other slot resolves to nothing** — without that, a swipe inside a
  // modal would run its workspace binding and change tabs invisibly behind the modal,
  // which is exactly the hijacking the recognizer's target filter used to prevent by
  // refusing to look at overlays at all. With the config off, that original immunity is
  // what is restored, rather than the bindings coming back.
  if (overlay.depth > 0) return overlay.enabled && BACK_SLOTS.has(slot) ? BACK_COMMAND : ''
  if (swipeAwayClose) {
    // The right-edge drawer overlays the sidebar, so it wins when both are open.
    if (panels.drawerOpen && (slot === 'swipe_right' || slot === 'two_finger_swipe_right')) return 'drawer.toggle'
    if (panels.sidebarOpen && HORIZONTAL_SLOTS.has(slot)) return 'sidebar.close'
  }
  return settings[slot]
}

/** Dismiss-stack levels other than the two mobile slide-in panels are gesture overlays. */
export function gestureOverlayDepth(dismissDepth: number, panels: OverlayPanels): number {
  return Math.max(0, dismissDepth - Number(panels.sidebarOpen) - Number(panels.drawerOpen))
}

export function swipeAwayCloseEnabled(config: Record<string, unknown>): boolean {
  return config.mobile_gesture_swipe_away_close !== false
}

export function overlayBackEnabled(config: Record<string, unknown>): boolean {
  return config.mobile_gesture_overlay_back !== false
}

export function mobileGestureSettings(config: Record<string, unknown>): MobileGestureSettings {
  const raw = config.mobile_gestures
  const source = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {}
  const result = { ...defaultMobileGestureSettings }
  for (const slot of GESTURE_SLOTS) {
    if (typeof source[slot] === 'string') result[slot] = source[slot] as string
  }
  return result
}
