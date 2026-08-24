// Pointer behaviour for a command-rail pad: one chip holding several actions plus a centre,
// each reached by dragging a direction off it.
//
// **The pad is a fan that opens upward, and it has no downward wedge at all.** The rail sits
// at the bottom of its pane, and on a phone that is the bottom of the screen - so a
// downward wedge is drawn off the glass and competes with the system's own bottom-edge
// gesture. Rather than squeeze it, there is no south: the 180° above the finger is divided
// instead, and the whole lower half becomes the abort zone. Pulling down cancels, which is
// the one gesture a bottom-edge rail can always complete. An action with no direction of its
// own - Down, on the arrows pad - goes in the centre, where a tap lands.
//
// The fan can be divided **two ways**, and they cost different things:
//
//  * by **angle**, into 1..5 wedges. Costs angular tolerance and nothing else. Five is the
//    ceiling because five is ±22°, the same slop that made an eight-way circle a poor
//    control; arc length is still comfortable well past it, so tolerance is what runs out.
//  * by **distance**, into 1 or 2 rings. Costs *fire-on-entry*: reaching the far ring means
//    crossing the near one, so a ringed pad's slots default to `release`
//    (`defaultPadTriggerMode`). A ring buys a slot per wedge without spending any angle.
//
// Wedges are therefore the cheaper axis to grow on, and the shipped pads use one ring.
//
// **The fan is centred above the finger, not on it** (`RAIL_PAD_LIFT_PX`), and the hub it
// opens from is large (`RAIL_PAD_DEAD_RADIUS_PX`). One decision, two numbers, and the reason
// is the same for both: with the origin under the finger and a 14px hub, the press landed at
// the common vertex of every wedge with 14px of tolerance in every direction, so a thumb
// settling onto the glass or a mouse drifting a few pixels committed an action nobody chose.
// The lift moves the press *below* the whole fan, into the abort zone's own half-plane, so a
// press is neutral by construction and sideways drift has to clear the skirt (about 93px)
// before it can reach any wedge at all; the hub widens the base of each wedge from 18px to
// 51px, which is the tolerance between two *neighbouring* actions once the finger is up
// there. The deliberate cost is the one direction the lift works against: committing
// straight up is `lift + dead` of travel rather than `dead`.
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
  PAD_CENTER,
  padSlotKey,
  padWedgeUnit,
  parsePadSlotKey,
  railPadBanded,
  type RailPadSlotKey,
  type RailPadTriggerMode,
} from './commandRail.ts'
import { claimPointerDrag } from './pointerDragClaim.ts'
import { RAIL_KEY_REPEAT_DELAY_MS, RAIL_KEY_REPEAT_INTERVAL_MS } from './railKeyRepeat.ts'
import { RAIL_PAN_SLOP_PX } from './railOverflow.ts'

/**
 * The hub: the neutral disc at the fan's origin, inside which nothing is selected.
 *
 * Large rather than small, and that is the deliberate reversal. A 14px hub put the wedge
 * boundaries 14px from the origin, where a 220° fan divided three ways gives each wedge an
 * 18px base - so the finger that opened the press was already within a twitch of two
 * different actions, and mouse jitter or a thumb settling onto the glass committed one. The
 * base of a wedge is `dead × wedgeWidth`, so this number *is* the tolerance between
 * neighbouring actions, and it is the cheapest one to buy.
 */
export const RAIL_PAD_DEAD_RADIUS_PX = 40
/**
 * How far above the press the fan's origin sits.
 *
 * The second half of the same decision. Centring the fan on the finger meant the finger
 * covered the hub *and* sat at the common vertex of every wedge, so the shortest distance to
 * an unintended action was the dead radius in any direction. Lifting the origin puts the
 * press below the fan entirely, in the abort zone's own half-plane: a press starts neutral by
 * construction, and reaching *any* wedge by drifting sideways now requires clearing the
 * skirt, which is `lift / tan(RAIL_PAD_SKIRT_DEG)` - about 93px at full size, against 14px
 * before.
 *
 * Strictly less than the hub at every scale, so the press itself always lands *inside* the
 * hub rather than in the abort zone below it: "nothing is selected" and "you are in the
 * middle" have to be the same state, or the dial would open already describing an escape.
 */
export const RAIL_PAD_LIFT_PX = 34
/** Finger travel that makes a press no longer a tap. Its own number rather than the dead
 *  radius, which used to serve as one: the hub no longer sits under the finger, so hub
 *  membership stopped answering "did this press move at all". Kept at the old dead radius,
 *  so what counts as a tap is exactly what it always was. */
export const RAIL_PAD_TAP_SLOP_PX = 14
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

export function railPadAxes(slots: Iterable<RailPadSlotKey>, wedges: number): RailPadAxes {
  let horizontal = false
  let vertical = false
  for (const key of slots) {
    const at = parsePadSlotKey(key)
    if (!at) continue
    const unit = padWedgeUnit(at.wedge, wedges)
    if (Math.abs(unit.x) > 0.01) horizontal = true
    if (Math.abs(unit.y) > 0.01) vertical = true
  }
  return { horizontal, vertical }
}

/** The radii a pad's rings occupy, at a given squeeze. Shared by the gesture and the
 *  drawing, which is what stops the dial describing a boundary it does not have. */
export interface RailPadBands {
  /** Inner hole: below this the press is at the centre. */
  dead: number
  /** How far above the press the origin these radii are measured from actually sits.
   *  Carried here rather than read from the constant, so the dial is drawn around the same
   *  point the gesture resolves against at every squeeze. */
  lift: number
  /** Near/far boundary, or `Infinity` on a one-ring pad. */
  ring: number
  /** Outer drawn edge. */
  outer: number
}

/**
 * Keyed on whether the pad has a boundary at all, not on its ring *count*.
 *
 * Two different features want the same radii: a second ring of slots, and a slot that
 * repeats beyond a band. What the boundary *means* is the slot's business
 * (`railPadSlotMode`); where it sits is only ever this.
 */
export function railPadBands(banded: boolean, scale = 1): RailPadBands {
  const clamped = Math.min(1, Math.max(RAIL_PAD_MIN_SCALE, scale))
  // The hub scales now. It did not, back when it was 14px and the stated reason was that it
  // was already small enough that shrinking it would fire on a press that never really moved.
  // At 40px that reasoning inverts: an unscaled hub inside a squeezed dial eats the wedge
  // annulus it is supposed to open into. `RAIL_PAD_MIN_SCALE` is what the old reason buys
  // instead - the smallest hub any squeeze can produce is still larger than the whole
  // full-size hub used to be.
  const dead = RAIL_PAD_DEAD_RADIUS_PX * clamped
  const lift = RAIL_PAD_LIFT_PX * clamped
  if (banded) {
    return { dead, lift, ring: RAIL_PAD_RING_PX * clamped, outer: RAIL_PAD_OUTER_PX * clamped }
  }
  return { dead, lift, ring: Infinity, outer: RAIL_PAD_SINGLE_OUTER_PX * clamped }
}

/**
 * How much the dial has to shrink to fit the room above the press.
 *
 * The one direction that can run out, now that the fan opens upward: a pad in a short pane
 * near the top of the window has less than the outer band's reach above it, and a boundary
 * you cannot travel to is a slot - or a repeat - that does not exist.
 *
 * The lift is part of that reach and is inside the same scale, which is why it is added to
 * the denominator rather than subtracted from the room. A lift held at full size in a short
 * pane would spend the scarce pixels pushing the origin off the top of the window, and a fan
 * whose hub is off screen is one no finger can travel into at all.
 */
export function railPadScaleFor(banded: boolean, roomAbovePx: number): number {
  if (!Number.isFinite(roomAbovePx)) return 1
  const wanted = RAIL_PAD_LIFT_PX + (banded ? RAIL_PAD_OUTER_PX : RAIL_PAD_SINGLE_OUTER_PX)
  return Math.min(1, Math.max(RAIL_PAD_MIN_SCALE, Math.max(0, roomAbovePx) / wanted))
}

/** Whether a radius counts as beyond the band, given where it was last. The same margin the
 *  ring slots use, and for the same reason: a finger resting on the boundary must not flip
 *  a repeat on and off. */
export function railPadBeyond(radius: number, bands: RailPadBands, was: boolean): boolean {
  if (!Number.isFinite(bands.ring)) return false
  return radius >= bands.ring + (was ? -RAIL_PAD_SWITCH_MARGIN_PX : RAIL_PAD_SWITCH_MARGIN_PX)
}

/** Angle of a displacement, in degrees, measured counter-clockwise from due east with up
 *  positive - so the fan is simply `0..180` and the abort zone is everything past it. */
export function railPadAngle(dx: number, dy: number): number {
  const raw = Math.atan2(-dy, dx) * 180 / Math.PI
  return raw < RAIL_PAD_FAN_START_DEG ? raw + 360 : raw
}

/** Half-open angular bounds of one wedge, in `railPadAngle` degrees. */
export function railPadWedgeBounds(wedge: number, wedges: number): { from: number; to: number } {
  const width = RAIL_PAD_FAN_SPAN_DEG / Math.max(1, wedges)
  const from = RAIL_PAD_FAN_START_DEG + wedge * width
  return { from, to: from + width }
}

/** Centre angle of a wedge. Where its label sits, and the way its unit vector points. */
export const railPadWedgeCentre = (wedge: number, wedges: number): number => {
  const { from, to } = railPadWedgeBounds(wedge, wedges)
  return (from + to) / 2
}

/** Which wedge an angle falls in, or `null` for the abort zone below the fan. */
export function railPadWedgeIndex(angle: number, wedges: number): number | null {
  const end = RAIL_PAD_FAN_START_DEG + RAIL_PAD_FAN_SPAN_DEG
  if (angle < RAIL_PAD_FAN_START_DEG || angle >= end) return null
  const count = Math.max(1, wedges)
  const width = RAIL_PAD_FAN_SPAN_DEG / count
  return Math.min(count - 1, Math.floor((angle - RAIL_PAD_FAN_START_DEG) / width))
}

/** What the pad decides a press is currently pointing at. `null` is the centre or the abort
 *  zone, which behave identically: neither fires anything on the way through. */
export type RailPadLatch = RailPadSlotKey | null

/** The shape a press is resolved against: how the fan is divided, and where its bands sit. */
export interface RailPadShape {
  wedges: number
  rings: number
  bands: RailPadBands
}

/**
 * The wedge and ring a displacement resolves to, biased towards one already latched.
 *
 * The angular bias is the hysteresis for the wedge boundaries, applied in the plane along
 * the latched wedge's own centre line so one expression covers every boundary whatever the
 * wedge count.
 */
export function railPadResolve(
  dx: number,
  dy: number,
  shape: RailPadShape,
  current: RailPadLatch = null,
): RailPadLatch {
  const at = current ? parsePadSlotKey(current) : null
  let x = dx
  let y = dy
  if (at) {
    const unit = padWedgeUnit(at.wedge, shape.wedges)
    x += unit.x * RAIL_PAD_SWITCH_MARGIN_PX
    y += unit.y * RAIL_PAD_SWITCH_MARGIN_PX
  }
  if (Math.hypot(x, y) < shape.bands.dead) return null
  const wedge = railPadWedgeIndex(railPadAngle(x, y), shape.wedges)
  if (wedge === null) return null
  if (shape.rings <= 1) return padSlotKey(0, wedge)
  // The radial boundary gets the same margin the angular ones do, but measured on the raw
  // radius rather than through the biased point: the plane bias points along the wedge's
  // *centre*, which is a direction, and a ring is a distance - so it would move the ring
  // boundary by an amount that depended on which wedge you were in.
  //
  // It has to move both ways. A near latch pushes the boundary out and a far latch pulls it
  // in, so a finger resting on the ring cannot flip between two actions; a one-sided version
  // makes crossing outward free, which is the direction it happens by accident.
  const margin = at ? (at.ring > 0 ? -RAIL_PAD_SWITCH_MARGIN_PX : RAIL_PAD_SWITCH_MARGIN_PX) : 0
  const ring = Math.hypot(dx, dy) >= shape.bands.ring + margin ? 1 : 0
  return padSlotKey(ring, wedge)
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
  /** Angular divisions of the fan. */
  wedges: number
  /** Radial bands, 1 or 2. */
  rings: number
  /** Only the keys present here are live; everything else is a dead wedge. */
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
  /** The press crossed the outer band, in or out. Fires nothing by itself - it only arms or
   *  disarms an `enter-repeat-far` slot - so it is a separate signal from `latch`, which
   *  always means "a different thing is now selected". */
  band(beyond: boolean): void
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
  /** Where the finger went down. Arbitration and "was this a tap" are about the finger. */
  let startX = 0
  let startY = 0
  /** Where the fan is centred: the press, lifted. Every wedge and ring is measured from here,
   *  and it is the only origin `railPadResolve` ever sees. */
  let originX = 0
  let originY = 0
  let options: RailPadPressOptions | null = null
  let shape: RailPadShape = { wedges: 3, rings: 1, bands: railPadBands(false) }
  let latched: RailPadLatch = null
  /** Whether the press is currently past the outer band. Tracked separately from the latch
   *  because crossing it selects nothing - it only arms or disarms a repeat. */
  let beyond = false
  let timer: T | null = null
  let releaseClaim: (() => void) | null = null
  let handledClick = false
  /** Whether the finger ever really moved. Not "ever latched a wedge": pulling straight down
   *  into the abort zone latches nothing, and firing the centre for that would turn the one
   *  escape a bottom-edge rail can always complete into a different action. Measured from the
   *  press rather than from the fan's origin, which the lift moved out from under it. */
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
    beyond = false
    leftCentre = false
    axes = { horizontal: false, vertical: false }
    callbacks.end()
  }

  const specFor = (slot: RailPadLatch): RailPadSlotSpec | undefined => options?.slots[slot ?? PAD_CENTER]

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
    // Landing *directly* in the outer band - a fast flick that never paused inside - still
    // arms the stream, because the finger is where the stream lives.
    else if (spec.mode === 'enter-repeat-far' && beyond) armRepeat(next, RAIL_KEY_REPEAT_DELAY_MS)
  }

  /**
   * Crossing the outer band, which fires nothing either way.
   *
   * Going out arms an `enter-repeat-far` slot's stream; coming back in cancels it without
   * re-firing, and going out again restarts the delay rather than resuming mid-stream - so a
   * wiggle across the boundary cannot machine-gun. Every other mode ignores the band
   * entirely: `enter-repeat` is already running and `enter` has nothing to run.
   */
  const setBeyond = (next: boolean) => {
    if (next === beyond) return
    beyond = next
    callbacks.band(next)
    const spec = specFor(latched)
    if (!latched || !spec || spec.disabled || spec.mode !== 'enter-repeat-far') return
    clearTimer()
    if (next) armRepeat(latched, RAIL_KEY_REPEAT_DELAY_MS)
  }

  return {
    press(pointerId, x, y, next) {
      // A gesture whose click never arrived must not swallow the next press's tap.
      handledClick = false
      if (pointer !== null) return false
      pointer = pointerId
      options = next
      // The band exists if a second ring of slots needs it *or* a slot repeats beyond it.
      // Same radii either way; what the boundary means is the slot's business.
      const banded = railPadBanded(
        next.rings,
        Object.values(next.slots).flatMap(spec => spec ? [spec.mode] : []),
      )
      shape = {
        wedges: next.wedges,
        rings: next.rings,
        bands: railPadBands(banded, railPadScaleFor(banded, next.roomAbovePx ?? Infinity)),
      }
      startX = x
      startY = y
      // The fan is centred above the finger, never on it. Applied once here so every later
      // reading - the wedge, the ring, the band, the dial - is measured from one origin.
      originX = x
      originY = y - shape.bands.lift
      latched = null
      beyond = false
      axes = railPadAxes(Object.keys(next.slots), next.wedges)
      // Nothing to yield to: every axis this pad uses is its own, so the pan and the menu
      // swipe are wrong about this finger from the very first pixel. A pad that leaves an
      // axis free waits instead, and decides at the pan's own slop.
      if (axes.horizontal && axes.vertical) claim()
      return true
    },
    move(pointerId, x, y) {
      if (pointer !== pointerId || !options) return false
      // Travel, which the arbitration and the tap test are about...
      const travelX = x - startX
      const travelY = y - startY
      if (!releaseClaim) {
        // The one-axis case. Whichever way this drag went, it settles here, at the same
        // distance the pan would start at, so the two can never both take it.
        if (Math.max(Math.abs(travelX), Math.abs(travelY)) < RAIL_PAN_SLOP_PX) return false
        const wantsThis = Math.abs(travelX) >= Math.abs(travelY) ? axes.horizontal : axes.vertical
        if (!wantsThis) {
          // Travel on an axis this pad does not use. Stand down for the whole press:
          // re-entering later would take a pan the strip has already begun.
          stop()
          return false
        }
        claim()
      }
      // Recorded on travel alone, before any wedge is resolved: a drag straight down reaches
      // no wedge at all, and it still has to count as movement or the lift would run the
      // centre.
      if (Math.hypot(travelX, travelY) >= RAIL_PAD_TAP_SLOP_PX) leftCentre = true
      // ...and displacement from the fan's own origin, which every geometric reading is
      // against. The two differ by exactly the lift, and conflating them is the bug this
      // split exists to prevent: a press that has not moved is `lift` pixels from the origin.
      const dx = x - originX
      const dy = y - originY
      const radius = Math.hypot(dx, dy)
      // Leaving for the centre uses the exit ratio; everything else is `railPadResolve`,
      // which is also what the dial is drawn from.
      if (latched !== null && radius < shape.bands.dead * RAIL_PAD_EXIT_RATIO) {
        setBeyond(false)
        setLatch(null)
        return true
      }
      // The band before the wedge, so a flick that lands directly in the outer band has
      // `beyond` already true when `setLatch` decides whether to arm the stream.
      setBeyond(railPadBeyond(radius, shape.bands, beyond))
      setLatch(railPadResolve(dx, dy, shape, latched))
      return true
    },
    release(pointerId) {
      if (pointer !== pointerId) return false
      const slot: RailPadSlotKey = latched ?? PAD_CENTER
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
        beyond,
        bands: shape.bands,
      }
    },
    consumeHandledClick() {
      const handled = handledClick
      handledClick = false
      return handled
    },
  }
}
