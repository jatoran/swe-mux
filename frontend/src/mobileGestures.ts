// Touch-gesture recognition for the mobile workspace. Edge- and top-anchored swipes
// are intentionally unsupported: on Android those belong to the OS (back / home /
// notification shade), so the reliable channels are mid-screen single-finger
// horizontal swipes and two-finger gestures. Vertical single-finger drags stay with
// the terminal (scrollback / application wheel) and are never claimed here.
//
// One region is the exception, and it is a region rather than a whole-screen channel:
// the command rail has no vertical scroll of its own to protect, so the single-finger
// upward swipe that would be reserved anywhere else is a real slot *there*
// (`classifyRailGesture`). See `RAIL_GESTURE_SELECTOR`.
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
  | 'rail_swipe_up'

export const GESTURE_SLOTS: GestureSlot[] = [
  'swipe_left',
  'swipe_right',
  'two_finger_swipe_left',
  'two_finger_swipe_right',
  'two_finger_swipe_up',
  'two_finger_swipe_down',
  'two_finger_tap',
  'rail_swipe_up',
]

export const GESTURE_LABELS: Record<GestureSlot, string> = {
  swipe_left: 'Swipe left',
  swipe_right: 'Swipe right',
  two_finger_swipe_left: 'Two-finger swipe left',
  two_finger_swipe_right: 'Two-finger swipe right',
  two_finger_swipe_up: 'Two-finger swipe up',
  two_finger_swipe_down: 'Two-finger swipe down',
  two_finger_tap: 'Two-finger tap',
  rail_swipe_up: 'Swipe up on the command rail',
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
  // The app menu, from the strip that sits directly under the operator's thumb.
  // Its only other door on a phone is the sidebar footer, so reaching it meant
  // pulling the sidebar in, tapping `: menu`, and then having a sidebar open over
  // the pane. Swiping up off the rail is that trip in one motion, and the menu is
  // a viewport-anchored overlay, so nothing about it needs the sidebar.
  rail_swipe_up: 'menu.toggle',
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

/**
 * The one horizontal scroller that also answers to a gesture of its own.
 *
 * It is named separately from the list below rather than removed from it: the rail
 * still owns every *horizontal* touch (that is its pan), and the exception is only
 * the single-finger vertical channel, which the rail has no use for.
 *
 * The note editor's own command rail is deliberately not this rail — a different
 * surface in a different pane — and it is not one of the named scrollers below
 * either: it lives in Continuity's shadow root as `.command-rail-buttons`, so what
 * vetoes a gesture over it is the generic computed-`overflow-x` branch, which is
 * exactly why that branch walks the composed path.
 */
export const RAIL_GESTURE_SELECTOR = '.terminal-action-rail'

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

/**
 * Whether this touch began on the command rail.
 *
 * Composed path for the same reason `pathOwnsHorizontalScroll` uses one: a window
 * listener sees a retargeted target, and the rail's chips include components that
 * bring their own shadow roots.
 */
export function pathOwnsRailGesture(path: readonly { matches: (selector: string) => boolean }[]): boolean {
  return path.some(element => element.matches(RAIL_GESTURE_SELECTOR))
}

/**
 * Classification for a sequence that began on the command rail.
 *
 * Deliberately narrow: **one finger, upward, and nothing else.** The rail keeps every
 * horizontal touch for its own pan, and a second finger over it has never resolved to
 * anything, so widening this would take input away from a control rather than find
 * unused input. The distance, axis-ratio and duration bars are the shared ones, so a
 * hesitant drag up the pane is no more a rail swipe than it is a tab flick, and a
 * long press that becomes a hold-to-repeat or a drag is settled before this by the
 * pointer-drag claim.
 */
export function classifyRailGesture(sample: GestureSample): GestureSlot | null {
  const { pointerCount, dx, dy, durationMs } = sample
  if (pointerCount !== 1 || dy >= 0) return null
  if (durationMs > GESTURE_THRESHOLDS.singleFingerSwipeMaxDurationMs) return null
  const absX = Math.abs(dx)
  const absY = Math.abs(dy)
  if (absY < GESTURE_THRESHOLDS.swipeMinDistance || absY < absX * GESTURE_THRESHOLDS.swipeAxisRatio) return null
  return 'rail_swipe_up'
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

// The workspace sidebar's own commands. A slot bound to any of them is "the gesture that
// works the left drawer" as far as the user is concerned, which is what an overlay with a
// left drawer of its own borrows — see `OverlayLeftPanel`.
const SIDEBAR_COMMANDS = new Set(['sidebar.toggle', 'sidebar.open', 'sidebar.close'])

/**
 * An overlay that carries a left slide-in navigation of its own — Settings on a phone.
 *
 * Its two command ids are supplied by the caller rather than named here, because this
 * module decides *which gesture means the left drawer*, not which surface owns one.
 */
export type OverlayLeftPanel = { open: boolean; toggle: string; close: string }

export type OverlayContext = { depth: number; enabled: boolean; panel?: OverlayLeftPanel }

// While the left sidebar is open, either horizontal direction closes it. The
// right-edge drawer keeps its directional rule: a rightward swipe pushes it
// away. These overrides are keyed on the open panel rather than the slot's
// binding, so they also work when that slot is unbound.
export function resolveGestureCommand(
  slot: GestureSlot,
  settings: MobileGestureSettings,
  panels: OverlayPanels,
  swipeAwayClose: boolean,
  overlay: OverlayContext = { depth: 0, enabled: false },
): string {
  // An open overlay level shadows everything below it, panels included: it is painted on
  // top, so a gesture over it can only mean something about it. The back swipe pops one
  // level and **every other slot resolves to nothing** — without that, a swipe inside a
  // modal would run its workspace binding and change tabs invisibly behind the modal,
  // which is exactly the hijacking the recognizer's target filter used to prevent by
  // refusing to look at overlays at all. With the config off, that original immunity is
  // what is restored, rather than the bindings coming back.
  if (overlay.depth > 0) {
    if (!overlay.enabled) return ''
    // An overlay with a left drawer answers to the same two rules the workspace sidebar
    // does, one level up: whichever slot the user bound the workspace sidebar to opens
    // this one, and while it is open either horizontal direction closes it. Deriving the
    // opening slot from the binding rather than hard-coding one is what makes this a
    // mirror — rebinding the workspace sidebar moves both drawers together.
    //
    // Every other slot still falls through to the back swipe, so backing out of the
    // overlay never stops working: with the defaults that is the single-finger rightward
    // swipe, which is not bound to the sidebar.
    const panel = overlay.panel
    if (panel) {
      if (panel.open && swipeAwayClose && HORIZONTAL_SLOTS.has(slot)) return panel.close
      if (SIDEBAR_COMMANDS.has(settings[slot])) return panel.open ? panel.close : panel.toggle
    }
    return BACK_SLOTS.has(slot) ? BACK_COMMAND : ''
  }
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
