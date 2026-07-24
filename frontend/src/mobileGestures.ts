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
  | 'two_finger_tap'

export const GESTURE_SLOTS: GestureSlot[] = [
  'swipe_left',
  'swipe_right',
  'two_finger_swipe_left',
  'two_finger_swipe_right',
  'two_finger_tap',
]

export const GESTURE_LABELS: Record<GestureSlot, string> = {
  swipe_left: 'Swipe left',
  swipe_right: 'Swipe right',
  two_finger_swipe_left: 'Two-finger swipe left',
  two_finger_swipe_right: 'Two-finger swipe right',
  two_finger_tap: 'Two-finger tap',
}

// Command id per slot, or '' to disable the gesture.
export type MobileGestureSettings = Record<GestureSlot, string>

export const defaultMobileGestureSettings: MobileGestureSettings = {
  swipe_left: 'mobileTab.next',
  swipe_right: 'mobileTab.previous',
  two_finger_swipe_left: 'sidebar.toggle',
  two_finger_swipe_right: 'sidebar.toggle',
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

export function classifyGesture(sample: GestureSample): GestureSlot | null {
  const { pointerCount, dx, dy, durationMs } = sample
  const absX = Math.abs(dx)
  const absY = Math.abs(dy)
  const horizontalSwipe =
    absX >= GESTURE_THRESHOLDS.swipeMinDistance && absX >= absY * GESTURE_THRESHOLDS.swipeAxisRatio

  if (pointerCount >= 2) {
    if (
      absX <= GESTURE_THRESHOLDS.tapMaxMovement &&
      absY <= GESTURE_THRESHOLDS.tapMaxMovement &&
      durationMs <= GESTURE_THRESHOLDS.tapMaxDurationMs
    ) {
      return 'two_finger_tap'
    }
    if (horizontalSwipe) return dx < 0 ? 'two_finger_swipe_left' : 'two_finger_swipe_right'
    return null
  }

  if (pointerCount === 1 && horizontalSwipe && durationMs <= GESTURE_THRESHOLDS.singleFingerSwipeMaxDurationMs) {
    return dx < 0 ? 'swipe_left' : 'swipe_right'
  }
  return null
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
