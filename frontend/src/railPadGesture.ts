// Pointer behaviour for a command-rail pad: one chip holding four actions plus a
// centre, each reached by dragging a direction off it.
//
// The single rule the whole thing is built on: **the only threshold is distance,
// never time.** A press is live from the first pixel, a direction commits the instant
// travel crosses the entry radius, and nothing anywhere waits for a clock before
// deciding what the finger meant. That is what makes "press, flick up, release" send
// one Up as fast as the hand can do it - the key has already fired mid-flick, before
// the finger stopped. The only timer in the module repeats an already-committed
// direction, and the only other one in the feature is cosmetic (`RAIL_PAD_PETAL_DELAY_MS`,
// how long before the labels are drawn, which the gesture does not consult).
//
// The second rule is arbitration, and it needs no new machinery. Two other readers of
// the same finger exist - the rail's own horizontal pan (`RailScroller`) and the mobile
// gesture recognizer's `rail_swipe_up`, which opens the app menu - and both already
// stand down for `pointerDragClaim`. The pan checks it live; the recognizer checks a
// generation mark, so a claim taken at pointer-down and released at pointer-up is still
// visible at the later `touchend` where it would have classified the swipe. So a pad
// claims, and the conflict is over with no delay, no selector exception, and no edit to
// either of them.
//
// The pad claims **only the axes it uses**, which is what keeps the rest of the trade
// honest. A pad with slots on both axes has nothing to yield and claims at pointer-down.
// A pad using only one axis lets travel on the other reach the pan, so a horizontal
// flick across a vertical-only pad still scrolls the strip. Both decisions are taken at
// `RAIL_PAN_SLOP_PX`, the same distance the pan starts at, so exactly one of them ever
// takes the pointer.
//
// DOM-free: the geometry, the hysteresis, the trigger modes and the repeat cadence are
// all decided here so they can be tested without a browser. `RailPad.tsx` owns the
// element, the listeners, the petals and the haptics.

import {
  padDirectionDescends,
  padDirectionUnit,
  type RailPadDirection,
  type RailPadOrientation,
  type RailPadSlotKey,
  type RailPadTriggerMode,
} from './commandRail.ts'
import { claimPointerDrag } from './pointerDragClaim.ts'
import { RAIL_KEY_REPEAT_DELAY_MS, RAIL_KEY_REPEAT_INTERVAL_MS } from './railKeyRepeat.ts'
import { RAIL_PAN_SLOP_PX } from './railOverflow.ts'

/** Travel that commits a direction. Small enough that the key fires mid-flick. */
export const RAIL_PAD_ENTER_RADIUS_PX = 10
/** Floor for a radius the screen edge has squeezed. Below the pan's own slop the pad
 *  would fire on a press that was never a drag, so this is where compression stops. */
export const RAIL_PAD_MIN_RADIUS_PX = RAIL_PAN_SLOP_PX
/** How much of the room below the finger a descending direction may ask for. Well under
 *  half, because the finger has to be able to travel *and* the pad has to draw there. */
export const RAIL_PAD_CLEARANCE_RATIO = 0.4
/** Coming back inside this fraction of the entry radius returns the pad to neutral.
 *  Asymmetric on purpose: equal thresholds chatter on the boundary. */
export const RAIL_PAD_EXIT_RATIO = 0.6
/** Extra travel required to leave a latched direction for its neighbour. */
export const RAIL_PAD_SWITCH_MARGIN_PX = 6
/** How long a press waits before the labels are *drawn*. Cosmetic only - the gesture is
 *  live from the first pixel, so a fast operator never meets this and a hesitant one
 *  always gets the map. */
export const RAIL_PAD_PETAL_DELAY_MS = 150

export { RAIL_KEY_REPEAT_DELAY_MS as RAIL_PAD_REPEAT_DELAY_MS, RAIL_KEY_REPEAT_INTERVAL_MS as RAIL_PAD_REPEAT_INTERVAL_MS }

/** Which axes a pad's populated directions actually span. */
export interface RailPadAxes { horizontal: boolean; vertical: boolean }

export function railPadAxes(directions: readonly RailPadDirection[]): RailPadAxes {
  let horizontal = false
  let vertical = false
  for (const direction of directions) {
    const unit = padDirectionUnit(direction)
    if (unit.x !== 0) horizontal = true
    if (unit.y !== 0) vertical = true
  }
  return { horizontal, vertical }
}

/**
 * The radius this direction has to be crossed at.
 *
 * Uniform except downward, where it shrinks to fit whatever room is left below the
 * finger. A rail sits at the bottom of its pane, so on a phone the space under a chip
 * can be less than the radius the pad would like - and a threshold you cannot reach is
 * a slot that does not exist. The drawn wedge is placed from this same number, so the
 * pad is asymmetric and *looks* asymmetric rather than lying about where its boundary
 * is. Firing on entry rather than on release is what makes the squeezed case safe as
 * well as reachable: committing within a few pixels beats Android's bottom-edge home
 * gesture, which needs considerably more travel before it recognises.
 *
 * `clearanceBelowPx` of `Infinity` (the default, and every desktop case) leaves the
 * radius uniform.
 */
export function railPadRadius(direction: RailPadDirection, clearanceBelowPx = Infinity): number {
  if (!padDirectionDescends(direction) || !Number.isFinite(clearanceBelowPx)) return RAIL_PAD_ENTER_RADIUS_PX
  const room = Math.max(0, clearanceBelowPx) * RAIL_PAD_CLEARANCE_RATIO
  return Math.min(RAIL_PAD_ENTER_RADIUS_PX, Math.max(RAIL_PAD_MIN_RADIUS_PX, room))
}

/** The direction a displacement points at, ignoring how far it went. */
function rawSector(dx: number, dy: number, orientation: RailPadOrientation): RailPadDirection {
  if (orientation === 'diagonal') {
    if (dy < 0) return dx < 0 ? 'upLeft' : 'upRight'
    return dx < 0 ? 'downLeft' : 'downRight'
  }
  if (Math.abs(dx) >= Math.abs(dy)) return dx < 0 ? 'left' : 'right'
  return dy < 0 ? 'up' : 'down'
}

/**
 * The direction a displacement points at, biased towards one already latched.
 *
 * The bias is the whole hysteresis: the point is pulled `RAIL_PAD_SWITCH_MARGIN_PX`
 * back along the current direction before its sector is read, so leaving that
 * direction costs exactly that much travel past the boundary. One expression covers
 * both orientations even though their boundaries sit in different places - cardinal's
 * on the diagonals, diagonal's on the axes - because the bias is applied in the plane
 * rather than to whichever coordinate happens to be the boundary.
 */
export function railPadSector(
  dx: number,
  dy: number,
  orientation: RailPadOrientation,
  current: RailPadDirection | null = null,
): RailPadDirection {
  if (!current) return rawSector(dx, dy, orientation)
  const unit = padDirectionUnit(current)
  return rawSector(
    dx + unit.x * RAIL_PAD_SWITCH_MARGIN_PX,
    dy + unit.y * RAIL_PAD_SWITCH_MARGIN_PX,
    orientation,
  )
}

/** What the pad decides a press is currently pointing at. `null` is the centre. */
export type RailPadLatch = RailPadDirection | null

/** Everything the engine needs to know about one slot. Supplied per press, because a
 *  slot's availability follows session state that changes under a mounted pad. */
export interface RailPadSlotSpec {
  mode: RailPadTriggerMode
  /** A direction whose action this backend or state does not admit. It still latches -
   *  directions are positional and must never reflow - and fires nothing, which also
   *  makes it a safe place to abort a gesture into. */
  disabled?: boolean
}

export interface RailPadPressOptions {
  orientation: RailPadOrientation
  /** Only the keys present here are live; everything else is a dead direction. */
  slots: Partial<Record<RailPadSlotKey, RailPadSlotSpec>>
  /** Room below the press, in CSS pixels. Squeezes the descending radii. */
  clearanceBelowPx?: number
}

export interface RailPadCallbacks {
  /** Run the slot. Called once per commitment and once per repetition. */
  fire(slot: RailPadSlotKey): void
  /** The latch changed. `armed` marks a `release` slot now waiting for the lift, which
   *  is the state the petals and the haptics render differently. */
  latch(slot: RailPadLatch, detail: { armed: boolean; disabled: boolean }): void
  /** The press is over, however it ended.
   *
   *  The petals are torn down from here rather than from the chip's own `pointerup`,
   *  because by then the finger is 40-odd pixels away and that event belongs to whatever
   *  is under it. A chip that only cleaned up after events it received itself left its
   *  labels on screen after every gesture that actually went somewhere. */
  end(): void
}

export interface RailPadGesture {
  /** Open a press. Returns false if one is already open. Fires nothing. */
  press(pointerId: number, x: number, y: number, options: RailPadPressOptions): boolean
  /** Report the pointer. Returns true once this press owns the pointer, so the caller
   *  knows the claim has been taken. */
  move(pointerId: number, x: number, y: number): boolean
  /** End the press, firing a `release` slot still latched, or the centre from neutral. */
  release(pointerId: number): boolean
  /** Abandon without firing: cancel, blur, a hidden tab, a session swap, unmount. */
  cancel(): void
  /** Whether a press is open, and where it points. For the petals. */
  peek(): { open: boolean; latch: RailPadLatch; armed: boolean; radius: (direction: RailPadDirection) => number }
  /** Whether the trailing click was already answered by the gesture and must be
   *  swallowed rather than run as the chip's own tap. One-shot: reading clears it. */
  consumeHandledClick(): boolean
}

/**
 * One pad press at a time, per rail.
 *
 * Per rail rather than per chip for the reason `useRailKeyRepeat` is: two pads must
 * never repeat at once, whatever takes the window away has to be able to stop
 * whichever one is, and `RailStrip` renders every chip a second time inside its
 * overflow popover - so two live instances of the same pad genuinely coexist.
 */
export function createRailPadGesture<T>(
  callbacks: RailPadCallbacks,
  schedule: (callback: () => void, delayMs: number) => T,
  cancelTimer: (timer: T) => void,
): RailPadGesture {
  let pointer: number | null = null
  let startX = 0
  let startY = 0
  let options: RailPadPressOptions | null = null
  let latched: RailPadLatch = null
  let timer: T | null = null
  let releaseClaim: (() => void) | null = null
  let handledClick = false
  let everLatched = false
  let axes: RailPadAxes = { horizontal: false, vertical: false }

  const clearTimer = () => {
    if (timer === null) return
    cancelTimer(timer)
    timer = null
  }

  const claim = () => {
    if (releaseClaim) return
    releaseClaim = claimPointerDrag()
  }

  const stop = () => {
    clearTimer()
    releaseClaim?.()
    releaseClaim = null
    pointer = null
    options = null
    latched = null
    everLatched = false
    axes = { horizontal: false, vertical: false }
    callbacks.end()
  }

  const specFor = (slot: RailPadLatch): RailPadSlotSpec | undefined =>
    options?.slots[slot ?? 'center']

  const armRepeat = (slot: RailPadSlotKey, delayMs: number) => {
    timer = schedule(() => {
      timer = null
      if (pointer === null) return
      callbacks.fire(slot)
      armRepeat(slot, RAIL_KEY_REPEAT_INTERVAL_MS)
    }, delayMs)
  }

  const setLatch = (next: RailPadLatch) => {
    if (next === latched) return
    clearTimer()
    latched = next
    // Reaching *any* direction answers the chip's click, a dead one included: dragging
    // into an empty direction is a deliberate abort, and it would be a poor one if the
    // trailing click then ran the chip's tap action anyway. Claiming the pointer is not
    // enough on its own - a chip that both taps and pads has to keep its tap, and that
    // press has already claimed by the time it turns out to have gone nowhere.
    if (next) { handledClick = true; everLatched = true }
    const spec = specFor(next)
    const armed = !!next && !!spec && !spec.disabled && spec.mode === 'release'
    callbacks.latch(next, { armed, disabled: !!next && !spec })
    if (!next || !spec || spec.disabled) return
    if (spec.mode === 'release') return
    callbacks.fire(next)
    if (spec.mode === 'enter-repeat') armRepeat(next, RAIL_KEY_REPEAT_DELAY_MS)
  }

  return {
    press(pointerId, x, y, next) {
      // A gesture whose click never arrived must not swallow the next press's tap.
      handledClick = false
      if (pointer !== null) return false
      pointer = pointerId
      options = next
      startX = x
      startY = y
      latched = null
      const live = (Object.keys(next.slots) as RailPadSlotKey[])
        .filter((key): key is RailPadDirection => key !== 'center')
      axes = railPadAxes(live)
      // Nothing to yield to: every axis this pad uses is its own, so the pan and the
      // menu swipe are wrong about this finger from the very first pixel. A pad that
      // leaves an axis free waits instead, and decides at the pan's own slop.
      if (axes.horizontal && axes.vertical) claim()
      return true
    },
    move(pointerId, x, y) {
      if (pointer !== pointerId || !options) return false
      const dx = x - startX
      const dy = y - startY
      if (!releaseClaim) {
        // The one-axis case. Whichever way this drag went, it settles here, at the same
        // distance the pan would start at, so the two can never both take it.
        if (Math.max(Math.abs(dx), Math.abs(dy)) < RAIL_PAN_SLOP_PX) return false
        const wantsThis = Math.abs(dx) >= Math.abs(dy) ? axes.horizontal : axes.vertical
        if (!wantsThis) {
          // Travel on an axis this pad does not use. Stand down for the whole press:
          // re-entering later would take a pan the strip has already begun.
          stop()
          return false
        }
        claim()
      }
      const distance = Math.hypot(dx, dy)
      const candidate = railPadSector(dx, dy, options.orientation, latched)
      const radius = railPadRadius(candidate, options.clearanceBelowPx)
      if (latched === null) {
        if (distance >= radius) setLatch(candidate)
        return true
      }
      // Leaving for the centre uses the *latched* direction's radius, so a squeezed
      // downward slot is also released closer in and the pair stays symmetric.
      if (distance < railPadRadius(latched, options.clearanceBelowPx) * RAIL_PAD_EXIT_RATIO) {
        setLatch(null)
        return true
      }
      if (candidate !== latched && distance >= radius) setLatch(candidate)
      return true
    },
    release(pointerId) {
      if (pointer !== pointerId) return false
      const slot: RailPadSlotKey = latched ?? 'center'
      const spec = specFor(latched)
      // A latched direction fires only if it was waiting for exactly this. Every other
      // mode already fired on the way in, and a `release` slot the finger has since left
      // is no longer latched, which is the whole escape hatch.
      //
      // Neutral fires the centre only when the press *never* reached a direction. Coming
      // back to the middle to abort must not resolve to a different action instead - an
      // escape hatch that ran the centre would be a redirect, not an escape - so the
      // centre stays what it is: what a tap does.
      const fires = !!spec && !spec.disabled && (latched === null
        ? !everLatched && spec.mode !== 'release'
        : spec.mode === 'release')
      clearTimer()
      if (fires) {
        // A centre tap that fires *is* the chip's action for this press, so the click
        // behind it is a duplicate. A press that resolved to nothing leaves the click
        // alone, and an untravelled press on a padded chip is therefore still a tap.
        if (latched === null) handledClick = true
        callbacks.fire(slot)
      }
      if (latched !== null) callbacks.latch(null, { armed: false, disabled: false })
      stop()
      return true
    },
    cancel() {
      if (pointer === null) return
      callbacks.latch(null, { armed: false, disabled: false })
      stop()
    },
    peek() {
      const clearance = options?.clearanceBelowPx
      const spec = specFor(latched)
      return {
        open: pointer !== null,
        latch: latched,
        armed: !!latched && !!spec && !spec.disabled && spec.mode === 'release',
        radius: (direction: RailPadDirection) => railPadRadius(direction, clearance),
      }
    },
    consumeHandledClick() {
      const handled = handledClick
      handledClick = false
      return handled
    },
  }
}
