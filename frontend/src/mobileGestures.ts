// Touch-gesture recognition for the mobile workspace. Edge- and top-anchored swipes
// are intentionally unsupported: on Android those belong to the OS (back / home /
// notification shade), so the reliable channels are mid-screen single-finger
// horizontal swipes and two-finger gestures. Vertical single-finger drags stay with
// the terminal (scrollback / application wheel) and are never claimed here.
//
// One region is the exception, and it is a region rather than a whole-screen channel:
// the command rail has no vertical scroll of its own to protect, so the single-finger
// upward swipe that would be reserved anywhere else is a real slot *there*
// (`classifyRailGesture`). See `RAIL_GESTURE_SELECTOR`. An overlay opened *from* that
// rail does have one, and is excluded by name — `GESTURE_SHADOWING_SELECTORS`.
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

// ---------------------------------------------------------------------------
// Surface gestures
// ---------------------------------------------------------------------------
//
// A **region** is a piece of chrome that answers to a swipe of its own, because the
// channel the rest of the app reserves is dead there. The command rail was the first
// (single-finger vertical means nothing to a strip that only pans sideways); four
// more follow the same test, and each one is a *place with an obvious local meaning*
// rather than another global slot: swiping the voice dock works the voice dock,
// swiping a tab works that tab.
//
// Three rules hold for every region and are what keep this from eating the app:
//
//  * **One finger.** Two fingers are the global slots' channel everywhere, region or
//    not, and no region has ever resolved a two-finger gesture.
//  * **A region decides its own directions.** A matched region that does not claim
//    the direction resolves to nothing — it never falls through to the workspace
//    binding, or a swipe over chrome would change tabs behind it.
//  * **Horizontal only where nothing else pans.** Vertical is free in all five
//    regions; horizontal is claimed by two, and the caller still drops it when the
//    composed path owns a horizontal scroller, so the dock's action strip and the
//    top bar's account switcher keep their drags when they overflow.
//
// Only the command rail's gesture is a rebindable slot, because it is the only one
// whose action is not about the surface it starts on — it opens the app menu, and any
// command would make sense there. The rest are part of their surface's design and are
// turned off together (`surfaceGesturesEnabled`) rather than remapped one by one.

export type GestureDirection = 'up' | 'down' | 'left' | 'right'

export type GestureRegion =
  | 'noteRail'
  | 'tabRail'
  | 'voiceDock'
  | 'projectName'
  | 'quotaChip'
  | 'micToggle'
  | 'runTrigger'
  | 'navToggle'
  | 'drawerToggle'
  | 'commandRail'

/** What a region gesture asks for. Resolved to a command, or to an act needing the
 *  element the touch started on, by the caller — this module owns only the mapping. */
export type SurfaceGesture =
  | 'voiceDock.expand'
  | 'voiceDock.collapse'
  | 'voiceDock.modeNext'
  | 'voiceDock.modePrevious'
  | 'projectName.next'
  | 'projectName.previous'
  | 'projectName.menu'
  | 'tabRail.menu'
  | 'noteRail.outline'
  | 'quotaChip.accounts'
  | 'micToggle.reveal'
  | 'micToggle.hide'
  | 'runTrigger.menu'
  | 'navToggle.open'
  | 'drawerToggle.open'

/**
 * Region selectors, matched against the composed path, **most specific first**.
 *
 * `noteRail` covers the Notes document rail, a Project note's header, and Continuity's
 * exported command-rail part. The shadow part is the stable editor contract;
 * `.command-rail-buttons` is an internal that a version bump may rename under us.
 */
export const GESTURE_REGION_SELECTORS: readonly (readonly [GestureRegion, string])[] = [
  ['noteRail', '.notes-subtabs-row'],
  ['noteRail', '.project-resource[data-resource-kind="note"]>header'],
  ['noteRail', '[part~="command-rail"]'],
  ['tabRail', '.mobile-unified-tabs'],
  ['voiceDock', '.voice-dock-head'],
  ['projectName', '.mobile-project-name'],
  // The rest of the mobile top bar. Every control on that row now answers to a drag as
  // well as a tap, and mostly to the *same* thing the tap does — which is the point
  // rather than a redundancy: it is a 44px row of small targets under a thumb, and a
  // drag that starts anywhere on a control and ends anywhere at all is far more
  // forgiving than a tap that has to land and stay put.
  ['quotaChip', '.rail-quota'],
  ['micToggle', '.conversation-talk-toggle'],
  ['runTrigger', '.mobile-run-trigger'],
  ['navToggle', '.mobile-nav-toggle'],
  ['drawerToggle', '.mobile-drawer-toggle'],
  ['commandRail', RAIL_GESTURE_SELECTOR],
]

/**
 * What each region does per direction. `commandRail` is absent because it resolves
 * through the ordinary slot system instead (`rail_swipe_up`).
 *
 * The dock is **top-anchored and grows downward** (`.voice-dock{position:absolute;top:6px}`),
 * which is why down expands and up collapses — the same sense as its own `▾`/`▴` buttons,
 * and the opposite of what a bottom-anchored sheet would want.
 *
 * Leftward means "next" for the dock's modes because that is already what leftward means
 * on this device: `swipe_left` is `mobileTab.next`. Same for Projects.
 *
 * Both vertical directions on a tab open its menu rather than one opening and one closing:
 * the menu is dismissed by the next touch anywhere, so a "close" direction would be a
 * gesture for something that has already happened.
 */
const REGION_GESTURES: Partial<Record<GestureRegion, Partial<Record<GestureDirection, SurfaceGesture>>>> = {
  voiceDock: {
    up: 'voiceDock.collapse',
    down: 'voiceDock.expand',
    left: 'voiceDock.modeNext',
    right: 'voiceDock.modePrevious',
  },
  projectName: {
    left: 'projectName.next',
    right: 'projectName.previous',
    up: 'projectName.menu',
    down: 'projectName.menu',
  },
  tabRail: { up: 'tabRail.menu', down: 'tabRail.menu' },
  noteRail: { down: 'noteRail.outline' },
  // Downward for the three that *open* something, because the thing they open is drawn
  // below the bar and the drag is the pull that brings it down. Only the mic has an
  // upward half, because its panel is the one that stays on screen afterwards and so is
  // the one you can be holding open by mistake.
  quotaChip: { down: 'quotaChip.accounts' },
  micToggle: { down: 'micToggle.reveal', up: 'micToggle.hide' },
  runTrigger: { down: 'runTrigger.menu' },
  // Any direction at all: these two are the edge toggles, and the whole reason to drag
  // one instead of tapping it is that a thumb reaching the corner of a phone is not
  // placing a precise tap. Refusing three of the four directions would put the precision
  // back. They only ever open — the panel they open is closed by its own scrim, its own
  // swipe-away, and a second tap.
  navToggle: { up: 'navToggle.open', down: 'navToggle.open', left: 'navToggle.open', right: 'navToggle.open' },
  drawerToggle: { up: 'drawerToggle.open', down: 'drawerToggle.open', left: 'drawerToggle.open', right: 'drawerToggle.open' },
}

/**
 * What breaks a region's premise, so the region stops answering to a swipe.
 *
 * A region answers to a swipe because the channel it claims is dead there — the command
 * rail claims single-finger vertical because a strip that only pans sideways has no
 * vertical scroll of its own to protect. Two things break that premise, and neither is
 * the region ceasing to exist:
 *
 *   * **A surface painted over it.** An overlay opened from the rail lives inside it:
 *     `RailStrip` renders the overflow popover as a DOM child of `.rail-row`, and its
 *     `.rail-overflow-grid` is a real vertical scroller, so scrolling the folded row to
 *     reach a chip *was* an upward swipe on the rail and opened the app menu.
 *   * **The region in a mode that uses the channel itself.** An arranging rail
 *     (`railArrange.ts`) is dragged in every direction, including up into its bin and
 *     new-row targets, so single-finger vertical is the most load-bearing thing on it
 *     rather than the deadest. A *committed* drag already stands this recognizer down
 *     through the pointer-drag claim, but a flick that never holds long enough to lift a
 *     chip takes no claim — and that is precisely the gesture that opened the app menu
 *     out from under someone rearranging their rail.
 *
 * The veto is on identity and mode, never on scroll state. A short popover that does not
 * overflow is no more a place to open the app menu than a long one, and `overflow-y` is
 * the wrong question to ask: what settles it is that a gesture there can only mean
 * something about the thing under it. That is the same rule `resolveGestureCommand`
 * applies to the dismiss stack, one level down.
 *
 * Checked per path element, nearest-first, so a shadowing surface *inside* a region wins
 * over the region and a region nested inside chrome is unaffected. An arranging rail
 * matches both its region selector and its shadow selector on the same element, and the
 * shadow is checked first, which is what makes the mode entry work at all.
 *
 * Drop-ups (`.rail-dropup`) are deliberately absent: they mount under `.terminal-surface`,
 * outside `.terminal-action-rail`, so no region ever matches them in the first place.
 */
export const GESTURE_SHADOWING_SELECTORS: readonly string[] = [
  '.rail-overflow-popover',
  '.terminal-action-rail.rail-arranging',
]

/**
 * Whether a touch began somewhere that shadows everything under it.
 *
 * The recognizer drops the sequence outright on this, rather than merely declining to
 * call it a region gesture. Declining would hand the touch to the *workspace* slots,
 * where a horizontal swipe is `mobileTab.next` — so scrolling the folded rail sideways,
 * or panning an arranging rail to reach a chip, would change tabs behind it: one wrong
 * gesture traded for a worse one.
 */
export function pathShadowsGesture(path: readonly { matches: (selector: string) => boolean }[]): boolean {
  return path.some(element => GESTURE_SHADOWING_SELECTORS.some(selector => element.matches(selector)))
}

/**
 * The region a touch began in, or null.
 *
 * Composed path for the same reason `pathOwnsHorizontalScroll` uses one: a window
 * listener sees a retargeted target, so an ancestor walk cannot see a shadow root's
 * internals — which for `noteRail` is the whole question.
 */
export function regionForPath(path: readonly { matches: (selector: string) => boolean }[]): GestureRegion | null {
  for (const element of path) {
    for (const selector of GESTURE_SHADOWING_SELECTORS) {
      if (element.matches(selector)) return null
    }
    for (const [region, selector] of GESTURE_REGION_SELECTORS) {
      if (element.matches(selector)) return region
    }
  }
  return null
}

/** Kept for the command rail's own name, and because the docs and tests speak of it. */
export function pathOwnsRailGesture(path: readonly { matches: (selector: string) => boolean }[]): boolean {
  return regionForPath(path) === 'commandRail'
}

/**
 * Direction of a region swipe: **one finger**, past the shared distance, axis-ratio and
 * duration bars. A hesitant drag is no more a region swipe than it is a tab flick, and a
 * long press that becomes a hold-to-repeat or a drag is settled before this by the
 * pointer-drag claim.
 */
export function classifyRegionGesture(sample: GestureSample): GestureDirection | null {
  const { pointerCount, dx, dy, durationMs } = sample
  if (pointerCount !== 1) return null
  if (durationMs > GESTURE_THRESHOLDS.singleFingerSwipeMaxDurationMs) return null
  const absX = Math.abs(dx)
  const absY = Math.abs(dy)
  if (absY >= GESTURE_THRESHOLDS.swipeMinDistance && absY >= absX * GESTURE_THRESHOLDS.swipeAxisRatio) {
    return dy < 0 ? 'up' : 'down'
  }
  if (absX >= GESTURE_THRESHOLDS.swipeMinDistance && absX >= absY * GESTURE_THRESHOLDS.swipeAxisRatio) {
    return dx < 0 ? 'left' : 'right'
  }
  return null
}

/** True for the two directions a horizontal scroller in the path would be panning. */
export const isHorizontalDirection = (direction: GestureDirection): boolean =>
  direction === 'left' || direction === 'right'

/** What this region does in this direction, if anything. */
export function surfaceGestureFor(region: GestureRegion, direction: GestureDirection): SurfaceGesture | null {
  return REGION_GESTURES[region]?.[direction] ?? null
}

export function surfaceGesturesEnabled(config: Record<string, unknown>): boolean {
  return config.mobile_surface_gestures !== false
}

/**
 * Classification for a sequence that began on the command rail.
 *
 * Deliberately narrow: **one finger, upward, and nothing else.** The rail keeps every
 * horizontal touch for its own pan, and a second finger over it has never resolved to
 * anything, so widening this would take input away from a control rather than find
 * unused input.
 */
export function classifyRailGesture(sample: GestureSample): GestureSlot | null {
  return classifyRegionGesture(sample) === 'up' ? 'rail_swipe_up' : null
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
