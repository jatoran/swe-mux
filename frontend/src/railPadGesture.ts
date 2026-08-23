// Pointer behaviour for a command-rail pad: one chip holding three or four actions plus a
// centre, each reached by dragging a direction off it.
//
// **The pad is a fan that opens upward, and it has no downward slot at all.** The rail sits
// at the bottom of its pane, and on a phone that is the bottom of the screen - so a
// downward wedge is drawn off the glass, dragged into a place the finger cannot reach, and
// competes with the system's own bottom-edge gesture. Rather than squeeze it, there is no
// south: the 180° above the finger is divided instead, and the whole lower half becomes the
// abort zone. Pulling down cancels, which is a gesture that always has room.
//
// Four slots still fit, because the fan is divided **twice**: by angle into wedges, and by
// distance into rings. A `cardinal` pad is three wedges of one ring (left, up, right). A
// `diagonal` pad is two wedges of two rings (up-left and up-right, near and far), which is
// what a 2x2 of two independent binary choices wants - one choice per division.
//
// The rule the whole thing is built on: **the only threshold is distance, never time.** A
// press is live from the first pixel, a direction commits the instant travel crosses the
// dead radius, and nothing waits on a clock to decide what the finger meant. That is what
// makes "press, flick up, release" send one Up as fast as the hand can do it. The only timer
// here repeats an already-committed direction; the only other one in the feature is cosmetic
// (`RAIL_PAD_DIAL_DELAY_MS`, when the dial is *drawn*, which the gesture never consults).
//
// The second rule is arbitration, and it needs no new machinery. Two other readers of the
// same finger exist - the rail's horizontal pan (`RailScroller`) and the mobile recognizer's
// `rail_swipe_up`, which opens the app menu - and both already stand down for
// `pointerDragClaim`. The pan checks it live; the recognizer checks a generation mark, so a
// claim taken at pointer-down and released at pointer-up is still visible at the later
// `touchend` where it would have classified the swipe. So a pad claims, and the conflict is
// over with no delay and no edit to either of them.
//
// The pad claims **only the axes it uses**. A pad with wedges on both axes has nothing to
// yield and claims at pointer-down; one using a single axis lets travel on the other reach
// the pan. Both decisions are taken at `RAIL_PAN_SLOP_PX`, the same distance the pan starts
// at, so exactly one of them ever takes the pointer.
//
// DOM-free: the geometry, the hysteresis, the trigger modes and the repeat cadence are all
// decided here so they can be tested without a browser, and so the dial can be *drawn* from
// the same numbers the gesture is tested against. `RailPad.tsx` owns the element, the
// listeners, the dial and the haptics.

import {
  padDirectionUnit,
  padDirections,
  padRingOf,
  padSectorCount,
  type RailPadDirection,
  type RailPadOrientation,
  type RailPadRing,
  type RailPadSlotKey,
  type RailPadTriggerMode,
} from './commandRail.ts'
import { claimPointerDrag } from './pointerDragClaim.ts'
import { RAIL_KEY_REPEAT_DELAY_MS, RAIL_KEY_REPEAT_INTERVAL_MS } from './railKeyRepeat.ts'
import { RAIL_PAN_SLOP_PX } from './railOverflow.ts'

/** Travel that leaves the centre and commits a direction. Small on purpose: the wedges are
 *  thumb-sized but the *commitment* still happens mid-flick, which is what keeps the pad
 *  fast. Size and speed are separate decisions here and only one of them is this number. */
export const RAIL_PAD_DEAD_RADIUS_PX = 14
/** Where the near ring ends and the far one begins, on a two-ring pad. */
export const RAIL_PAD_RING_PX = 104
/** Outer drawn edge of a two-ring pad. Travel past it is still the far ring. */
export const RAIL_PAD_OUTER_PX = 188
/** Outer drawn edge of a one-ring pad, which has no boundary to leave room for. */
export const RAIL_PAD_SINGLE_OUTER_PX = 150
/** How far the fan reaches below the horizontal at each end, so a thumb flicking sideways
 *  that dips a little still lands in the wedge it aimed at. */
export const RAIL_PAD_SKIRT_DEG = 20
/** Coming back inside this fraction of the dead radius returns the pad to neutral.
 *  Asymmetric on purpose: equal thresholds chatter on the boundary. */
export const RAIL_PAD_EXIT_RATIO = 0.6
/** Extra travel required to leave a latched wedge for its neighbour, or to cross a ring. */
export const RAIL_PAD_SWITCH_MARGIN_PX = 8
/** How long a press waits before the dial is *drawn*. Cosmetic only - the gesture is live
 *  from the first pixel, so a fast operator never meets this and a hesitant one always gets
 *  the map. */
export const RAIL_PAD_DIAL_DELAY_MS = 150
/** Floor for the scale a cramped pane may squeeze the dial to. Below this the far ring is
 *  merely hard to reach, which beats a ring that commits on a twitch. */
export const RAIL_PAD_MIN_SCALE = 0.45

export { RAIL_KEY_REPEAT_DELAY_MS as RAIL_PAD_REPEAT_DELAY_MS, RAIL_KEY_REPEAT_INTERVAL_MS as RAIL_PAD_REPEAT_INTERVAL_MS }

/** The angular span the wedges divide: 180° above the finger, plus a skirt at each end. */
export const RAIL_PAD_FAN_START_DEG = -RAIL_PAD_SKIRT_DEG
export const RAIL_PAD_FAN_SPAN_DEG = 180 + RAIL_PAD_SKIRT_DEG * 2

/** Which axes a pad's populated wedges span. */
export interface RailPadAxes { horizontal: boolean; vertical: boolean }

export function railPadAxes(directions: readonly RailPadDirection[]): RailPadAxes {
  let horizontal = false
  let vertical = false
  for (const direction of directions) {
    const unit = padDirectionUnit(direction)
    if (Math.abs(unit.x) > 0.01) horizontal = true
    if (Math.abs(unit.y) > 0.01) vertical = true
  }
  return { horizontal, vertical }
}

/** The radii one orientation's rings occupy, at a given squeeze. Shared by the gesture and
 *  the drawing, which is what stops the dial describing a boundary it does not have. */
export interface RailPadBands {
  /** Inner hole: below this the press is at the centre. */
  dead: number
  /** Near/far boundary, or `Infinity` where there is only one ring. */
  ring: number
  /** Outer drawn edge. */
  outer: number
}

export function railPadBands(orientation: RailPadOrientation, scale = 1): RailPadBands {
  const clamped = Math.min(1, Math.max(RAIL_PAD_MIN_SCALE, scale))
  if (orientation === 'diagonal') {
    return { dead: RAIL_PAD_DEAD_RADIUS_PX, ring: RAIL_PAD_RING_PX * clamped, outer: RAIL_PAD_OUTER_PX * clamped }
  }
  return { dead: RAIL_PAD_DEAD_RADIUS_PX, ring: Infinity, outer: RAIL_PAD_SINGLE_OUTER_PX * clamped }
}

/**
 * How much the dial has to shrink to fit the room above the press.
 *
 * The one direction that can run out now, and the mirror of the downward squeeze this
 * replaced: a pad in a short pane near the top of the window has less than the far ring's
 * reach above it, and a boundary you cannot travel to is a slot that does not exist.
 */
export function railPadScaleFor(orientation: RailPadOrientation, roomAbovePx: number): number {
  if (!Number.isFinite(roomAbovePx)) return 1
  const wanted = orientation === 'diagonal' ? RAIL_PAD_OUTER_PX : RAIL_PAD_SINGLE_OUTER_PX
  return Math.min(1, Math.max(RAIL_PAD_MIN_SCALE, Math.max(0, roomAbovePx) / wanted))
}

/** Angle of a displacement, in degrees, measured counter-clockwise from due east with up
 *  positive - so the fan is simply `0..180` and the abort zone is everything past it. */
export function railPadAngle(dx: number, dy: number): number {
  const raw = Math.atan2(-dy, dx) * 180 / Math.PI
  return raw < RAIL_PAD_FAN_START_DEG ? raw + 360 : raw
}

/** Half-open angular bounds of one wedge, in `railPadAngle` degrees. */
export function railPadWedgeBounds(orientation: RailPadOrientation, index: number): { from: number; to: number } {
  const width = RAIL_PAD_FAN_SPAN_DEG / padSectorCount(orientation)
  const from = RAIL_PAD_FAN_START_DEG + index * width
  return { from, to: from + width }
}

/** Centre angle of a wedge. Where its label sits, and the direction its unit vector points. */
export const railPadWedgeCentre = (orientation: RailPadOrientation, index: number): number => {
  const { from, to } = railPadWedgeBounds(orientation, index)
  return (from + to) / 2
}

/** Which wedge an angle falls in, or `null` for the abort zone below the fan. */
export function railPadWedgeIndex(orientation: RailPadOrientation, angle: number): number | null {
  const end = RAIL_PAD_FAN_START_DEG + RAIL_PAD_FAN_SPAN_DEG
  if (angle < RAIL_PAD_FAN_START_DEG || angle >= end) return null
  const width = RAIL_PAD_FAN_SPAN_DEG / padSectorCount(orientation)
  return Math.min(padSectorCount(orientation) - 1, Math.floor((angle - RAIL_PAD_FAN_START_DEG) / width))
}

/** The direction at one wedge and ring. `padDirections` order is wedge-major within a ring. */
export function railPadDirectionAt(
  orientation: RailPadOrientation,
  index: number,
  ring: RailPadRing,
): RailPadDirection | null {
  const sectors = padSectorCount(orientation)
  const offset = ring === 'far' ? sectors : 0
  return padDirections(orientation)[index + offset] ?? null
}

/** What the pad decides a press is currently pointing at. `null` is the centre or the abort
 *  zone, which behave identically: neither fires anything on the way through. */
export type RailPadLatch = RailPadDirection | null

/**
 * The wedge and ring a displacement resolves to, biased towards one already latched.
 *
 * The bias is the whole hysteresis, and it is applied in the plane rather than to whichever
 * coordinate happens to be a boundary - so one expression covers the angular boundaries
 * between wedges and the radial one between rings, even though they are different kinds of
 * edge. Leaving a latched direction costs `RAIL_PAD_SWITCH_MARGIN_PX` of travel past it.
 */
export function railPadResolve(
  dx: number,
  dy: number,
  orientation: RailPadOrientation,
  bands: RailPadBands,
  current: RailPadLatch = null,
): RailPadLatch {
  let x = dx
  let y = dy
  if (current) {
    const unit = padDirectionUnit(current)
    x += unit.x * RAIL_PAD_SWITCH_MARGIN_PX
    y += unit.y * RAIL_PAD_SWITCH_MARGIN_PX
  }
  const distance = Math.hypot(x, y)
  if (distance < bands.dead) return null
  const index = railPadWedgeIndex(orientation, railPadAngle(x, y))
  if (index === null) return null
  // The radial boundary gets the same margin the angular ones do, but measured on the raw
  // radius rather than through the biased point: the plane bias points along the wedge's
  // *centre*, which is a direction, and a ring is a distance - so it would move the ring
  // boundary by an amount that depended on which wedge you were in.
  //
  // It has to move both ways. A near latch pushes the boundary out and a far latch pulls it
  // in, so a finger resting on the ring cannot flip between two actions; a one-sided version
  // makes crossing outward free, which is the direction it happens by accident.
  const currentRing = current ? padRingOf(current) : null
  const boundary = bands.ring
    + (currentRing === 'far' ? -RAIL_PAD_SWITCH_MARGIN_PX : currentRing === 'near' ? RAIL_PAD_SWITCH_MARGIN_PX : 0)
  return railPadDirectionAt(orientation, index, Math.hypot(dx, dy) >= boundary ? 'far' : 'near')
}

/** Everything the engine needs to know about one slot. Supplied per press, because a slot's
 *  availability follows session state that changes under a mounted pad. */
export interface RailPadSlotSpec {
  mode: RailPadTriggerMode
  /** A direction whose action this backend or state does not admit. It still latches -
   *  directions are positional and must never reflow - and fires nothing, which also makes
   *  it a safe place to abort a gesture into. */
  disabled?: boolean
}

export interface RailPadPressOptions {
  orientation: RailPadOrientation
  /** Only the keys present here are live; everything else is a dead direction. */
  slots: Partial<Record<RailPadSlotKey, RailPadSlotSpec>>
  /** Room above the press, in CSS pixels. Squeezes the dial when a pane is short. */
  roomAbovePx?: number
}

export interface RailPadCallbacks {
  /** Run the slot. Called once per commitment and once per repetition. */
  fire(slot: RailPadSlotKey): void
  /** The latch changed. `armed` marks a `release` slot now waiting for the lift, which is
   *  the state the dial and the haptics render differently. */
  latch(slot: RailPadLatch, detail: { armed: boolean; disabled: boolean }): void
  /** The press is over, however it ended.
   *
   *  The dial is torn down from here rather than from the chip's own `pointerup`, because by
   *  then the finger is a hundred-odd pixels away and that event belongs to whatever is
   *  under it. A chip that only cleaned up after events it received itself left its dial on
   *  screen after every gesture that actually went somewhere. */
  end(): void
}

export interface RailPadGesture {
  /** Open a press. Returns false if one is already open. Fires nothing. */
  press(pointerId: number, x: number, y: number, options: RailPadPressOptions): boolean
  /** Report the pointer. Returns true once this press owns the pointer. */
  move(pointerId: number, x: number, y: number): boolean
  /** End the press, firing a `release` slot still latched, or the centre from a clean tap. */
  release(pointerId: number): boolean
  /** Abandon without firing: cancel, blur, a hidden tab, a session swap, unmount. */
  cancel(): void
  /** Whether a press is open, where it points, and the bands it is using. For the dial. */
  peek(): { open: boolean; latch: RailPadLatch; armed: boolean; bands: RailPadBands }
  /** Whether the trailing click was already answered by the gesture and must be swallowed
   *  rather than run as the chip's own tap. One-shot: reading it clears it. */
  consumeHandledClick(): boolean
}

/**
 * One pad press at a time, per rail.
 *
 * Per rail rather than per chip for the reason `useRailKeyRepeat` is: two pads must never
 * repeat at once, whatever takes the window away has to be able to stop whichever one is,
 * and `RailStrip` renders every chip a second time inside its overflow popover - so two live
 * instances of the same pad genuinely coexist and must share one press.
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
  let bands = railPadBands('cardinal')
  let latched: RailPadLatch = null
  let timer: T | null = null
  let releaseClaim: (() => void) | null = null
  let handledClick = false
  /** Whether the press ever left the hub. Not "ever latched a wedge": pulling straight down
   *  into the abort zone latches nothing, and firing the centre for that would turn the one
   *  escape a bottom-edge rail can always complete into a different action. */
  let leftCentre = false
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
    leftCentre = false
    axes = { horizontal: false, vertical: false }
    callbacks.end()
  }

  const specFor = (slot: RailPadLatch): RailPadSlotSpec | undefined => options?.slots[slot ?? 'center']

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
    // Reaching *any* wedge answers the chip's click, a dead one included: dragging into an
    // empty direction is a deliberate abort, and it would be a poor one if the trailing
    // click then ran the chip's tap action anyway. Claiming the pointer is not enough on its
    // own - a chip that both taps and pads has to keep its tap, and that press has already
    // claimed by the time it turns out to have gone nowhere.
    if (next) handledClick = true
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
      bands = railPadBands(next.orientation, railPadScaleFor(next.orientation, next.roomAbovePx ?? Infinity))
      startX = x
      startY = y
      latched = null
      const live = (Object.keys(next.slots) as RailPadSlotKey[])
        .filter((key): key is RailPadDirection => key !== 'center')
      axes = railPadAxes(live)
      // Nothing to yield to: every axis this pad uses is its own, so the pan and the menu
      // swipe are wrong about this finger from the very first pixel. A pad that leaves an
      // axis free waits instead, and decides at the pan's own slop.
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
      // Recorded on distance alone, before any wedge is resolved: a drag straight down
      // reaches no wedge at all, and it still has to count as having left the hub or the
      // lift would run the centre.
      if (Math.hypot(dx, dy) >= bands.dead) leftCentre = true
      // Leaving for the centre uses the exit ratio; everything else is `railPadResolve`,
      // which is also what the dial is drawn from.
      if (latched !== null && Math.hypot(dx, dy) < bands.dead * RAIL_PAD_EXIT_RATIO) {
        setLatch(null)
        return true
      }
      setLatch(railPadResolve(dx, dy, options.orientation, bands, latched))
      return true
    },
    release(pointerId) {
      if (pointer !== pointerId) return false
      const slot: RailPadSlotKey = latched ?? 'center'
      const spec = specFor(latched)
      // A latched direction fires only if it was waiting for exactly this. Every other mode
      // already fired on the way in, and a `release` slot the finger has since left is no
      // longer latched, which is the whole escape hatch.
      //
      // Neutral fires the centre only when the press *never* reached a direction. Coming
      // back to the middle to abort, or pulling down into the abort zone, must not resolve
      // to a different action instead - an escape hatch that ran the centre would be a
      // redirect, not an escape - so the centre stays what it is: what a tap does.
      const fires = !!spec && !spec.disabled && (latched === null
        ? !leftCentre && spec.mode !== 'release'
        : spec.mode === 'release')
      clearTimer()
      if (fires) {
        // A centre tap that fires *is* the chip's action for this press, so the click behind
        // it is a duplicate. A press that resolved to nothing leaves the click alone, and an
        // untravelled press on a padded chip is therefore still a tap.
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
      const spec = specFor(latched)
      return {
        open: pointer !== null,
        latch: latched,
        armed: !!latched && !!spec && !spec.disabled && spec.mode === 'release',
        bands,
      }
    },
    consumeHandledClick() {
      const handled = handledClick
      handledClick = false
      return handled
    },
  }
}
