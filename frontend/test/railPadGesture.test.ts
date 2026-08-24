import assert from 'node:assert/strict'
import test from 'node:test'

import {
  createRailPadGesture,
  RAIL_PAD_DEAD_RADIUS_PX,
  RAIL_PAD_DIAL_DELAY_MS,
  RAIL_PAD_EXIT_RATIO,
  RAIL_PAD_FAN_SPAN_DEG,
  RAIL_PAD_FAN_START_DEG,
  RAIL_PAD_LIFT_PX,
  RAIL_PAD_MIN_SCALE,
  RAIL_PAD_OUTER_PX,
  RAIL_PAD_REPEAT_DELAY_MS,
  RAIL_PAD_REPEAT_INTERVAL_MS,
  RAIL_PAD_RING_PX,
  RAIL_PAD_SINGLE_OUTER_PX,
  RAIL_PAD_SKIRT_DEG,
  RAIL_PAD_SWITCH_MARGIN_PX,
  RAIL_PAD_TAP_SLOP_PX,
  railPadAngle,
  railPadAxes,
  railPadBands,
  railPadResolve,
  railPadScaleFor,
  railPadWedgeBounds,
  railPadWedgeCentre,
  railPadWedgeIndex,
  type RailPadPressOptions,
  type RailPadShape,
} from '../src/railPadGesture.ts'
import { PAD_CENTER, RAIL_PAD_MAX_WEDGES, padSlotKey } from '../src/commandRail.ts'
import { markPointerDragClaims, pointerDragOwnsPointer } from '../src/pointerDragClaim.ts'
import { RAIL_PAN_SLOP_PX } from '../src/railOverflow.ts'

/** Three wedges, one ring: up in the middle, right at `0:0`, left at `0:2`. */
const W = (wedge: number, ring = 0) => padSlotKey(ring, wedge)
const RIGHT = W(0)
const UP = W(1)
const LEFT = W(2)

const THREE: RailPadPressOptions['slots'] = {
  [RIGHT]: { mode: 'enter' },
  [UP]: { mode: 'enter-repeat' },
  [LEFT]: { mode: 'release' },
}

// A hand-driven clock, so the cadence is asserted rather than waited for.
function harness(options?: Partial<RailPadPressOptions>) {
  const fired: string[] = []
  const latches: { slot: string | null; armed: boolean }[] = []
  const ends: number[] = []
  const bands: boolean[] = []
  let now = 0
  const timers: { at: number; run: () => void; id: number }[] = []
  let nextId = 1
  const gesture = createRailPadGesture<number>(
    {
      fire: slot => fired.push(slot),
      latch: (slot, detail) => latches.push({ slot, armed: detail.armed }),
      band: beyond => { bands.push(beyond) },
      end: () => { ends.push(1) },
    },
    (callback, delayMs) => {
      const id = nextId++
      timers.push({ at: now + delayMs, run: callback, id })
      return id
    },
    id => {
      const index = timers.findIndex(timer => timer.id === id)
      if (index >= 0) timers.splice(index, 1)
    },
  )
  const advance = (ms: number) => {
    const until = now + ms
    for (;;) {
      const due = timers.filter(timer => timer.at <= until).sort((a, b) => a.at - b.at)[0]
      if (!due) break
      timers.splice(timers.indexOf(due), 1)
      now = due.at
      due.run()
    }
    now = until
  }
  let pressX = 0
  let pressY = 0
  const press = (x = 0, y = 0) => {
    pressX = x
    pressY = y
    return gesture.press(1, x, y, {
      wedges: 3,
      rings: 1,
      slots: THREE,
      ...options,
    })
  }
  /**
   * Absolute pointer coordinates for a point *on the dial*: `radius` from the fan's origin,
   * at `degrees`.
   *
   * Every geometric move goes through this rather than naming a raw pointer offset, because
   * the origin is not the press: it sits `RAIL_PAD_LIFT_PX` above it, so "60px up from the
   * finger" and "60px up the dial" are different points and only the second one is what any
   * of these tests mean.
   */
  const point = (radius: number, degrees: number) => {
    const polar = at(radius, degrees)
    return { x: pressX + polar.dx, y: pressY - RAIL_PAD_LIFT_PX + polar.dy }
  }
  /** Put the finger on that dial point. */
  const aim = (radius: number, degrees: number) => {
    const target = point(radius, degrees)
    return gesture.move(1, target.x, target.y)
  }
  return { gesture, fired, latches, ends, bands, advance, press, point, aim }
}

/** A point at a distance and an angle, in the gesture's own screen-axis convention. */
const at = (radius: number, degrees: number) => ({
  dx: radius * Math.cos(degrees * Math.PI / 180),
  dy: -radius * Math.sin(degrees * Math.PI / 180),
})

const shapeOf = (wedges: number, rings: number, scale = 1): RailPadShape =>
  ({ wedges, rings, bands: railPadBands(rings > 1, scale) })

const resolveAt = (shape: RailPadShape, radius: number, degrees: number, current: string | null = null) => {
  const point = at(radius, degrees)
  return railPadResolve(point.dx, point.dy, shape, current)
}

/** Comfortably outside the hub at full size, and inside every band boundary. */
const NEAR = RAIL_PAD_DEAD_RADIUS_PX + 20

// ---------------------------------------------------------------------------
// The fan
// ---------------------------------------------------------------------------

test('the fan opens upward, and everything below it is the abort zone', () => {
  assert.equal(RAIL_PAD_FAN_START_DEG, -RAIL_PAD_SKIRT_DEG)
  assert.equal(RAIL_PAD_FAN_SPAN_DEG, 180 + RAIL_PAD_SKIRT_DEG * 2)
  for (let wedges = 1; wedges <= RAIL_PAD_MAX_WEDGES; wedges += 1) {
    assert.notEqual(railPadWedgeIndex(90, wedges), null, 'straight up is always a wedge')
    // Clear of the fan's own edges rather than on them: the ends are exactly `-20` and
    // `200`, and a point built from a cosine lands a fraction inside or outside by luck.
    for (const degrees of [-90, -60, 230, 250]) {
      const point = at(60, degrees)
      assert.equal(railPadWedgeIndex(railPadAngle(point.dx, point.dy), wedges), null, `${degrees}° is not`)
    }
  }
})

test('the skirt catches a sideways flick that dips below the horizontal', () => {
  const dipped = at(60, -RAIL_PAD_SKIRT_DEG + 2)
  assert.equal(railPadWedgeIndex(railPadAngle(dipped.dx, dipped.dy), 3), 0)
  const past = at(60, -RAIL_PAD_SKIRT_DEG - 2)
  assert.equal(railPadWedgeIndex(railPadAngle(past.dx, past.dy), 3), null)
})

test('any wedge count divides the whole fan into contiguous wedges', () => {
  for (let wedges = 1; wedges <= RAIL_PAD_MAX_WEDGES; wedges += 1) {
    assert.equal(railPadWedgeBounds(0, wedges).from, RAIL_PAD_FAN_START_DEG)
    assert.equal(railPadWedgeBounds(wedges - 1, wedges).to, RAIL_PAD_FAN_START_DEG + RAIL_PAD_FAN_SPAN_DEG)
    for (let wedge = 1; wedge < wedges; wedge += 1) {
      assert.equal(railPadWedgeBounds(wedge, wedges).from, railPadWedgeBounds(wedge - 1, wedges).to)
    }
    // And every wedge's own centre resolves back to it, at every count.
    for (let wedge = 0; wedge < wedges; wedge += 1) {
      assert.equal(railPadWedgeIndex(railPadWedgeCentre(wedge, wedges), wedges), wedge)
    }
  }
})

test('wedges stay thumb-sized at every count the model allows', () => {
  // The complaint the dial replaced was that the targets were too small. Asserted as an arc
  // length rather than an angle, because that is what a finger meets - and it is *not* what
  // caps the wedge count, which is why the ceiling is documented as angular tolerance.
  const bands = railPadBands(false)
  const radius = bands.dead + (bands.outer - bands.dead) * 0.55
  for (let wedges = 1; wedges <= RAIL_PAD_MAX_WEDGES; wedges += 1) {
    const arc = radius * (RAIL_PAD_FAN_SPAN_DEG / wedges) * Math.PI / 180
    assert.ok(arc >= 44, `${wedges} wedges gives ${Math.round(arc)}px of arc`)
  }
})

test('the hub is what separates two neighbouring actions, so it is sized for that', () => {
  // The base of a wedge is the hub's arc across the wedge's own angle, and it is the *only*
  // thing between one action and the one beside it at the moment a drag commits. At the
  // widest count the model allows it still has to be a target rather than a line.
  const bands = railPadBands(false)
  for (let wedges = 1; wedges <= RAIL_PAD_MAX_WEDGES; wedges += 1) {
    const base = bands.dead * (RAIL_PAD_FAN_SPAN_DEG / wedges) * Math.PI / 180
    assert.ok(base >= 30, `${wedges} wedges gives a ${Math.round(base)}px base`)
  }
})

test('the press always lands inside the hub, at every squeeze', () => {
  // The invariant the whole lift rests on: "nothing is selected" and "you are in the neutral
  // middle" have to be the same state. A lift that reached past the hub would open every
  // dial already describing the abort zone, which reads as a pad that starts broken.
  for (const scale of [1, 0.9, 0.75, RAIL_PAD_MIN_SCALE, 0.1]) {
    for (const banded of [false, true]) {
      const bands = railPadBands(banded, scale)
      assert.ok(bands.lift < bands.dead, `scale ${scale}, banded ${banded}`)
      assert.ok(bands.dead < bands.outer, `scale ${scale}: the wedges keep an annulus`)
    }
  }
})

test('a three-wedge pad reads left, up and right, and never a fourth', () => {
  const shape = shapeOf(3, 1)
  assert.equal(resolveAt(shape, NEAR, 0), RIGHT)
  assert.equal(resolveAt(shape, NEAR, 90), UP)
  assert.equal(resolveAt(shape, NEAR, 180), LEFT)
  // Everything downward is the centre, which is what makes "pull down" a cancel that always
  // has room - the one gesture a rail on the screen's bottom edge can always complete.
  assert.equal(resolveAt(shape, NEAR, -90), null)
  assert.equal(resolveAt(shape, 200, -90), null)
})

test('a four-wedge pad splits the same fan four ways, all of them upward', () => {
  const shape = shapeOf(4, 1)
  const seen = [10, 60, 120, 170].map(degrees => resolveAt(shape, NEAR, degrees))
  assert.deepEqual(seen, [W(0), W(1), W(2), W(3)])
  assert.equal(resolveAt(shape, NEAR, -90), null)
})

test('a one-ring pad has no ring boundary, however far the drag goes', () => {
  const shape = shapeOf(3, 1)
  assert.equal(shape.bands.ring, Infinity)
  assert.equal(shape.bands.outer, RAIL_PAD_SINGLE_OUTER_PX)
  assert.equal(resolveAt(shape, 400, 90), UP)
})

test('a two-ring pad reads near and far in the same wedge', () => {
  const shape = shapeOf(2, 2)
  assert.equal(shape.bands.ring, RAIL_PAD_RING_PX)
  assert.equal(shape.bands.outer, RAIL_PAD_OUTER_PX)
  assert.equal(resolveAt(shape, RAIL_PAD_RING_PX - 2, 145), W(1, 0))
  assert.equal(resolveAt(shape, RAIL_PAD_RING_PX + 2, 145), W(1, 1))
  // Past the drawn edge is still the far ring: a long drag is not an abort.
  assert.equal(resolveAt(shape, 400, 145), W(1, 1))
})

test('a short pane squeezes the dial, and the gesture squeezes with it', () => {
  // The lift is inside the reach being fitted, so it is part of what a full-size dial needs
  // above the press - a lift kept at full size while the bands shrank would spend the scarce
  // pixels pushing the origin off the top of the window.
  const bandedReach = RAIL_PAD_LIFT_PX + RAIL_PAD_OUTER_PX
  const singleReach = RAIL_PAD_LIFT_PX + RAIL_PAD_SINGLE_OUTER_PX
  assert.equal(railPadScaleFor(true, Infinity), 1)
  assert.equal(railPadScaleFor(true, bandedReach * 2), 1)
  assert.equal(railPadScaleFor(true, bandedReach / 2), 0.5)
  assert.equal(railPadScaleFor(true, 0), RAIL_PAD_MIN_SCALE)
  assert.equal(railPadScaleFor(false, singleReach / 2), 0.5)
  const squeezed = railPadBands(true, 0.5)
  assert.equal(squeezed.ring, RAIL_PAD_RING_PX / 2)
  assert.equal(squeezed.outer, RAIL_PAD_OUTER_PX / 2)
  assert.equal(squeezed.lift, RAIL_PAD_LIFT_PX / 2)
  assert.equal(squeezed.dead, RAIL_PAD_DEAD_RADIUS_PX / 2)
})

test('the smallest hub a squeeze can produce is still bigger than the old full-size one', () => {
  // What `RAIL_PAD_MIN_SCALE` buys once the hub scales, and the reason the hub needs no floor
  // of its own: the worst case is not merely survivable, it is roomier than what every pad
  // used to have at full size. Anchored on the number rather than on the old constant,
  // because the old constant is gone and this is the fact worth keeping.
  const tiny = railPadBands(false, 0.01)
  assert.equal(tiny.dead, RAIL_PAD_DEAD_RADIUS_PX * RAIL_PAD_MIN_SCALE)
  assert.ok(tiny.dead > 14, `a maximally squeezed hub is ${tiny.dead}px`)
  assert.ok(tiny.lift < tiny.dead)
})

test('a latched wedge costs the switch margin to leave, at every count', () => {
  for (const wedges of [3, 4, 5]) {
    const shape = shapeOf(wedges, 1)
    const boundary = railPadWedgeBounds(0, wedges).to
    assert.equal(resolveAt(shape, NEAR, boundary + 1, W(0)), W(0), `${wedges} wedges: held`)
    assert.equal(resolveAt(shape, NEAR, boundary + 25, W(0)), W(1), `${wedges} wedges: switched`)
    // With nothing latched the same point reads as its raw wedge.
    assert.equal(resolveAt(shape, NEAR, boundary + 1, null), W(1))
  }
})

test('the ring boundary costs the margin in both directions', () => {
  const shape = shapeOf(2, 2)
  const near = W(1, 0)
  const far = W(1, 1)
  assert.equal(resolveAt(shape, RAIL_PAD_RING_PX + 2, 145, near), near, 'crossing outward costs the margin')
  assert.equal(resolveAt(shape, RAIL_PAD_RING_PX + RAIL_PAD_SWITCH_MARGIN_PX + 4, 145, near), far)
  assert.equal(resolveAt(shape, RAIL_PAD_RING_PX - 2, 145, far), far, 'and so does crossing back')
  assert.equal(resolveAt(shape, RAIL_PAD_RING_PX - RAIL_PAD_SWITCH_MARGIN_PX - 4, 145, far), near)
})

test('railPadAxes reports only the axes the bound wedges span', () => {
  // The middle wedge of three points straight up and spans no horizontal at all, which is
  // what lets such a pad hand a sideways flick to the rail's pan.
  assert.deepEqual(railPadAxes([UP], 3), { horizontal: false, vertical: true })
  assert.deepEqual(railPadAxes([LEFT, RIGHT], 3), { horizontal: true, vertical: true })
  assert.deepEqual(railPadAxes([W(0, 1)], 2), { horizontal: true, vertical: true })
  assert.deepEqual(railPadAxes([PAD_CENTER], 3), { horizontal: false, vertical: false })
  assert.deepEqual(railPadAxes([], 3), { horizontal: false, vertical: false })
})

// ---------------------------------------------------------------------------
// The lift
// ---------------------------------------------------------------------------

test('a press that has not moved selects nothing, however long it is held', () => {
  const { gesture, fired, latches, advance, press } = harness()
  press(100, 100)
  // The same coordinates the press arrived at. Under a fan centred on the finger this was
  // the vertex of every wedge; under a lifted one it is inside the hub.
  gesture.move(1, 100, 100)
  assert.deepEqual(latches, [])
  advance(RAIL_PAD_REPEAT_DELAY_MS + RAIL_PAD_REPEAT_INTERVAL_MS * 10)
  assert.deepEqual(fired, [])
  gesture.cancel()
})

test('sideways drift has to clear the skirt before it reaches any wedge', () => {
  // The drift the lift exists to refuse. With the origin on the finger, this much sideways
  // travel was several times the dead radius and committed the outermost wedge; with it
  // above, the same travel is still below the fan because the angle back to the origin has
  // not cleared the skirt.
  const { gesture, fired, latches, press } = harness()
  press(0, 0)
  const safe = RAIL_PAD_LIFT_PX / Math.tan(RAIL_PAD_SKIRT_DEG * Math.PI / 180)
  assert.ok(safe > 90, `the skirt is only cleared past ${Math.round(safe)}px of drift`)
  for (const drift of [20, 40, 60, 80]) {
    gesture.move(1, drift, 0)
    gesture.move(1, -drift, 0)
  }
  assert.deepEqual(fired, [])
  assert.deepEqual(latches, [])
  // Deliberately going that far does still work, because the wedge is angular and the skirt
  // is a real part of it.
  gesture.move(1, safe + 30, 0)
  assert.deepEqual(fired, [RIGHT])
  gesture.cancel()
})

test('committing straight up costs the lift as well as the hub', () => {
  // The one direction the lift works against, asserted so it stays a decision: the finger has
  // to travel through the origin and out the far side of the hub.
  const { gesture, fired, press } = harness()
  press(0, 0)
  gesture.move(1, 0, -(RAIL_PAD_LIFT_PX + RAIL_PAD_DEAD_RADIUS_PX - 1))
  assert.deepEqual(fired, [])
  gesture.move(1, 0, -(RAIL_PAD_LIFT_PX + RAIL_PAD_DEAD_RADIUS_PX))
  assert.deepEqual(fired, [UP])
  gesture.cancel()
})

// ---------------------------------------------------------------------------
// The gesture
// ---------------------------------------------------------------------------

test('a press fires nothing, and crossing the dead radius fires once immediately', () => {
  const { gesture, fired, advance, press, aim } = harness()
  assert.equal(press(100, 100), true)
  assert.deepEqual(fired, [])
  aim(RAIL_PAD_DEAD_RADIUS_PX - 1, 90)
  assert.deepEqual(fired, [])
  // Crossing it fires on entry, with no clock involved at all - the wedges are thumb-sized
  // but the *commitment* is still this close in, which is what keeps the pad fast.
  aim(RAIL_PAD_DEAD_RADIUS_PX, 90)
  assert.deepEqual(fired, [UP])
  advance(0)
  assert.deepEqual(fired, [UP])
  gesture.cancel()
})

test('the dial delay is not a gesture delay', () => {
  assert.ok(RAIL_PAD_DIAL_DELAY_MS > 0)
  const { gesture, fired, press, aim } = harness()
  press(0, 0)
  aim(NEAR, 90)
  assert.deepEqual(fired, [UP])
  gesture.cancel()
})

test('a hold repeats at the shared cadence, and only an enter-repeat slot does', () => {
  const { gesture, fired, advance, press, aim } = harness()
  press(0, 0)
  aim(NEAR, 90)
  assert.deepEqual(fired, [UP])
  advance(RAIL_PAD_REPEAT_DELAY_MS - 1)
  assert.deepEqual(fired, [UP])
  advance(1)
  assert.deepEqual(fired, [UP, UP])
  advance(RAIL_PAD_REPEAT_INTERVAL_MS * 3)
  assert.equal(fired.length, 5)
  // Switching to a plain `enter` slot fires once and stops repeating.
  aim(NEAR, 0)
  const afterSwitch = fired.length
  assert.equal(fired[afterSwitch - 1], RIGHT)
  advance(RAIL_PAD_REPEAT_DELAY_MS + RAIL_PAD_REPEAT_INTERVAL_MS * 5)
  assert.equal(fired.length, afterSwitch)
  gesture.cancel()
})

// ---------------------------------------------------------------------------
// Repeat on push-out
// ---------------------------------------------------------------------------

const FAR_SLOTS: RailPadPressOptions['slots'] = { [UP]: { mode: 'enter-repeat-far' } }
/** Comfortably inside the band, and comfortably past it. */
const INSIDE = RAIL_PAD_RING_PX - 30
const OUTSIDE = RAIL_PAD_RING_PX + 40

test('a repeat-far slot sends exactly one however long it is held inside', () => {
  // The whole point of the mode: dwell is a poor statement of "I meant lots of these", and
  // `enter-repeat` starts spamming after 350ms from a thumb that merely hesitated.
  const { gesture, fired, advance, press, aim } = harness({ slots: FAR_SLOTS })
  press(0, 0)
  aim(INSIDE, 90)
  assert.deepEqual(fired, [UP])
  advance(RAIL_PAD_REPEAT_DELAY_MS + RAIL_PAD_REPEAT_INTERVAL_MS * 20)
  assert.deepEqual(fired, [UP], 'still one, twenty intervals later')
  gesture.release(1)
  assert.deepEqual(fired, [UP])
})

test('pushing past the band starts the stream, and fires nothing on the crossing itself', () => {
  const { gesture, fired, advance, press, bands, aim } = harness({ slots: FAR_SLOTS })
  press(0, 0)
  aim(INSIDE, 90)
  assert.deepEqual(fired, [UP])
  aim(OUTSIDE, 90)
  // Crossing selects nothing - it only arms - so the count is unchanged until the delay.
  assert.deepEqual(fired, [UP])
  assert.deepEqual(bands, [true])
  advance(RAIL_PAD_REPEAT_DELAY_MS)
  assert.deepEqual(fired, [UP, UP])
  advance(RAIL_PAD_REPEAT_INTERVAL_MS * 3)
  assert.equal(fired.length, 5)
  gesture.cancel()
})

test('coming back inside stops the stream without re-firing, and going out restarts the delay', () => {
  const { gesture, fired, advance, press, bands, aim } = harness({ slots: FAR_SLOTS })
  press(0, 0)
  aim(OUTSIDE, 90)
  advance(RAIL_PAD_REPEAT_DELAY_MS + RAIL_PAD_REPEAT_INTERVAL_MS)
  const streamed = fired.length
  assert.ok(streamed >= 3)

  aim(INSIDE, 90)
  assert.equal(fired.length, streamed, 'coming back in fires nothing')
  advance(RAIL_PAD_REPEAT_INTERVAL_MS * 10)
  assert.equal(fired.length, streamed, 'and stops the stream')

  // Out again restarts the *delay* rather than resuming mid-stream, so a wiggle across the
  // boundary cannot machine-gun.
  aim(OUTSIDE, 90)
  assert.equal(fired.length, streamed)
  advance(RAIL_PAD_REPEAT_DELAY_MS - 1)
  assert.equal(fired.length, streamed)
  advance(1)
  assert.equal(fired.length, streamed + 1)
  assert.deepEqual(bands, [true, false, true])
  gesture.cancel()
})

test('the band boundary costs the switch margin in both directions', () => {
  // Same hysteresis the ring slots use, and for the same reason: a finger resting on the
  // boundary must not flip a stream on and off.
  const { gesture, press, bands, aim } = harness({ slots: FAR_SLOTS })
  press(0, 0)
  aim(RAIL_PAD_RING_PX + 2, 90)
  assert.deepEqual(bands, [], 'just past is not past enough')
  aim(RAIL_PAD_RING_PX + RAIL_PAD_SWITCH_MARGIN_PX + 2, 90)
  assert.deepEqual(bands, [true])
  aim(RAIL_PAD_RING_PX - 2, 90)
  assert.deepEqual(bands, [true], 'and neither is just inside')
  aim(RAIL_PAD_RING_PX - RAIL_PAD_SWITCH_MARGIN_PX - 2, 90)
  assert.deepEqual(bands, [true, false])
  gesture.cancel()
})

test('a flick that lands straight in the band still arms the stream', () => {
  // A fast operator never pauses inside; the stream has to be where the finger is.
  const { gesture, fired, advance, press, aim } = harness({ slots: FAR_SLOTS })
  press(0, 0)
  aim(OUTSIDE, 90)
  assert.deepEqual(fired, [UP], 'one on entry, as always')
  advance(RAIL_PAD_REPEAT_DELAY_MS)
  assert.deepEqual(fired, [UP, UP])
  gesture.cancel()
})

test('a repeat-far pad is banded, so the geometry has a boundary to cross', () => {
  const { gesture, press } = harness({ slots: FAR_SLOTS })
  press(0, 0)
  const { bands } = gesture.peek()
  assert.equal(bands.ring, RAIL_PAD_RING_PX)
  assert.equal(bands.outer, RAIL_PAD_OUTER_PX)
  gesture.cancel()
  // Without such a slot, a one-ring pad has no boundary at all.
  const plain = harness()
  plain.press(0, 0)
  assert.equal(plain.gesture.peek().bands.ring, Infinity)
  plain.gesture.cancel()
})

test('switching wedges mid-stream stops it, and the band does not restart it for another mode', () => {
  const { gesture, fired, advance, press, aim } = harness({
    slots: { [UP]: { mode: 'enter-repeat-far' }, [RIGHT]: { mode: 'enter' } },
  })
  press(0, 0)
  aim(OUTSIDE, 90)
  advance(RAIL_PAD_REPEAT_DELAY_MS + RAIL_PAD_REPEAT_INTERVAL_MS * 2)
  const streamed = fired.length
  // Sideways to the plain `enter` wedge, still out past the band.
  aim(OUTSIDE, 0)
  assert.equal(fired[fired.length - 1], RIGHT)
  advance(RAIL_PAD_REPEAT_DELAY_MS + RAIL_PAD_REPEAT_INTERVAL_MS * 10)
  assert.equal(fired.length, streamed + 1, 'an enter slot does not stream just because it is far out')
  gesture.cancel()
})

test('leaving and re-entering a wedge fires it again', () => {
  const { gesture, fired, press, aim } = harness()
  press(0, 0)
  aim(NEAR, 90)
  aim(RAIL_PAD_DEAD_RADIUS_PX * RAIL_PAD_EXIT_RATIO - 0.5, 90)
  aim(NEAR, 90)
  aim(0, 90)
  aim(NEAR, 90)
  assert.deepEqual(fired, [UP, UP, UP])
  gesture.cancel()
})

test('pulling down into the abort zone releases the latch and fires nothing', () => {
  const { gesture, fired, latches, press, aim } = harness()
  press(0, 0)
  aim(NEAR, 90)
  assert.deepEqual(fired, [UP])
  aim(120, -90)
  assert.equal(latches.at(-1)?.slot, null)
  gesture.release(1)
  assert.deepEqual(fired, [UP], 'and the centre does not sneak in on the way out')
})

test('hysteresis is asymmetric: leaving costs less travel than entering', () => {
  const { gesture, fired, press, aim } = harness()
  press(0, 0)
  aim(RAIL_PAD_DEAD_RADIUS_PX, 90)
  assert.deepEqual(fired, [UP])
  aim(RAIL_PAD_DEAD_RADIUS_PX - 1, 90)
  aim(RAIL_PAD_DEAD_RADIUS_PX, 90)
  assert.deepEqual(fired, [UP])
  gesture.cancel()
})

test('a release slot waits for the lift, and dragging back out cancels it', () => {
  const held = harness()
  held.press(0, 0)
  held.aim(NEAR, 180)
  assert.deepEqual(held.fired, [], 'a release slot fires nothing on the way in')
  assert.equal(held.latches.at(-1)?.armed, true)
  held.gesture.release(1)
  assert.deepEqual(held.fired, [LEFT])

  const escaped = harness()
  escaped.press(0, 0)
  escaped.aim(NEAR, 180)
  escaped.aim(0, 90)
  escaped.gesture.release(1)
  assert.deepEqual(escaped.fired, [])

  const swapped = harness()
  swapped.press(0, 0)
  swapped.aim(NEAR, 180)
  swapped.aim(NEAR, 0)
  swapped.gesture.release(1)
  assert.deepEqual(swapped.fired, [RIGHT])
})

test('the press announces its end however it finished, so the dial can be torn down', () => {
  const lifted = harness()
  lifted.press(0, 0)
  lifted.aim(NEAR, 90)
  assert.deepEqual(lifted.ends, [])
  lifted.gesture.release(1)
  assert.equal(lifted.ends.length, 1)

  const cancelled = harness()
  cancelled.press(0, 0)
  cancelled.aim(NEAR, 90)
  cancelled.gesture.cancel()
  assert.equal(cancelled.ends.length, 1)
  cancelled.gesture.cancel()
  assert.equal(cancelled.ends.length, 1)
})

test('returning to the centre to abort runs nothing, even with a centre bound', () => {
  const { gesture, fired, press, aim } = harness({ slots: { ...THREE, [PAD_CENTER]: { mode: 'enter' } } })
  press(0, 0)
  aim(NEAR, 180)
  aim(0, 90)
  gesture.release(1)
  assert.deepEqual(fired, [])
})

test('a press that never travels fires the centre, and one that did does not', () => {
  const tapped = harness({ slots: { ...THREE, [PAD_CENTER]: { mode: 'enter' } } })
  tapped.press(0, 0)
  // Raw pointer jitter rather than a dial point: what makes this a tap is that the *finger*
  // barely moved, which is the one reading the lift does not apply to.
  tapped.gesture.move(1, 2, 2)
  tapped.gesture.release(1)
  assert.deepEqual(tapped.fired, [PAD_CENTER])
  assert.equal(tapped.gesture.consumeHandledClick(), true, 'the centre it fired is the tap, so the click is spent')

  const dragged = harness({ slots: { ...THREE, [PAD_CENTER]: { mode: 'enter' } } })
  dragged.press(0, 0)
  dragged.aim(NEAR, 0)
  dragged.gesture.release(1)
  assert.deepEqual(dragged.fired, [RIGHT], 'a wedge was chosen, so the centre is not also run')
})

test('the tap slop is about the finger, not about the hub the finger is inside', () => {
  // A press that drifts within the hub still reaches no wedge, so nothing latches - but it is
  // no longer a tap either, and running the centre for it would be the pad answering a
  // gesture the operator abandoned. The hub is far wider than this slop, so the two readings
  // genuinely disagree over exactly this range and only one of them is the right one.
  const { gesture, fired, press } = harness({ slots: { ...THREE, [PAD_CENTER]: { mode: 'enter' } } })
  press(0, 0)
  gesture.move(1, 0, RAIL_PAD_TAP_SLOP_PX)
  gesture.release(1)
  assert.deepEqual(fired, [])
})

test('an unbound wedge latches, fires nothing, and blocks the centre', () => {
  const { gesture, fired, press, latches, aim } = harness({
    slots: { [UP]: { mode: 'enter' }, [LEFT]: { mode: 'enter' }, [PAD_CENTER]: { mode: 'enter' } },
  })
  press(0, 0)
  aim(NEAR, 0)
  assert.deepEqual(fired, [])
  assert.equal(latches.at(-1)?.slot, RIGHT)
  gesture.release(1)
  assert.deepEqual(fired, [], 'releasing into a dead wedge is a deliberate abort, not a centre tap')
})

test('a disabled slot is a dead wedge, not a hidden one', () => {
  const { gesture, fired, press, latches, aim } = harness({
    slots: { [UP]: { mode: 'enter', disabled: true }, [RIGHT]: { mode: 'enter' } },
  })
  press(0, 0)
  aim(NEAR, 90)
  assert.equal(latches.at(-1)?.slot, UP, 'the wedge is still where it was')
  assert.deepEqual(fired, [])
  aim(NEAR, 0)
  assert.deepEqual(fired, [RIGHT])
  gesture.cancel()
})

test('a cancel fires nothing, including from an armed release slot', () => {
  const { gesture, fired, press, aim } = harness()
  press(0, 0)
  aim(NEAR, 180)
  gesture.cancel()
  assert.deepEqual(fired, [])
  assert.equal(pointerDragOwnsPointer(), false)
})

test('a two-axis pad claims the pointer at press, so the pan and the menu swipe stand down', () => {
  const mark = markPointerDragClaims()
  const { gesture, press } = harness()
  press(0, 0)
  assert.equal(pointerDragOwnsPointer(), true)
  gesture.release(1)
  assert.equal(pointerDragOwnsPointer(), false)
  // The generation mark is what the recognizer reads at `touchend`, which arrives after the
  // `pointerup` that released the claim - a live boolean there would always say "no".
  assert.equal(pointerDragOwnsPointer(mark), true)
})

test('a vertical-only pad yields the horizontal axis to the rail pan', () => {
  const { gesture, fired, press, aim } = harness({ slots: { [UP]: { mode: 'enter' } } })
  press(0, 0)
  assert.equal(pointerDragOwnsPointer(), false, 'nothing claimed yet: this pad has an axis to give away')
  // Raw travel: the arbitration is about which way the *finger* went, at the pan's own slop.
  gesture.move(1, RAIL_PAN_SLOP_PX, 0)
  assert.equal(pointerDragOwnsPointer(), false)
  // The press is over for the pad: coming back vertically must not steal a pan already begun.
  aim(NEAR, 90)
  assert.deepEqual(fired, [])
})

test('a vertical-only pad still claims once the drag goes its way', () => {
  const { gesture, fired, press, aim } = harness({ slots: { [UP]: { mode: 'enter' } } })
  press(0, 0)
  gesture.move(1, 0, -RAIL_PAN_SLOP_PX)
  assert.equal(pointerDragOwnsPointer(), true)
  aim(NEAR, 90)
  assert.deepEqual(fired, [UP])
  gesture.release(1)
  assert.equal(pointerDragOwnsPointer(), false)
})

test('only the pointer that opened a press may move or end it', () => {
  const { gesture, fired, press } = harness()
  press(0, 0)
  assert.equal(gesture.move(2, 0, -60), false)
  assert.deepEqual(fired, [])
  assert.equal(gesture.release(2), false)
  assert.equal(gesture.press(2, 0, 0, { wedges: 3, rings: 1, slots: THREE }), false)
  assert.equal(gesture.release(1), true)
})

test('a drag marks the click as spent, and a fresh press hands it back', () => {
  const { gesture, press, aim } = harness()
  press(0, 0)
  // Claiming the pointer is not by itself an answer to the click. A chip that taps *and*
  // pads has already claimed at this point and must still be tappable.
  assert.equal(gesture.consumeHandledClick(), false)
  aim(NEAR, 90)
  gesture.release(1)
  assert.equal(gesture.consumeHandledClick(), true)
  assert.equal(gesture.consumeHandledClick(), false, 'one-shot')

  press(0, 0)
  aim(NEAR, 90)
  gesture.release(1)
  press(0, 0)
  assert.equal(gesture.consumeHandledClick(), false)
  gesture.cancel()
})

test('peek reports the bands the press is actually using', () => {
  const { gesture, press } = harness({
    wedges: 2,
    rings: 2,
    slots: { [W(1, 0)]: { mode: 'release' }, [W(1, 1)]: { mode: 'release' } },
    roomAbovePx: (RAIL_PAD_LIFT_PX + RAIL_PAD_OUTER_PX) / 2,
  })
  press(0, 0)
  const { bands } = gesture.peek()
  assert.equal(bands.ring, RAIL_PAD_RING_PX / 2)
  assert.equal(bands.outer, RAIL_PAD_OUTER_PX / 2)
  assert.equal(bands.lift, RAIL_PAD_LIFT_PX / 2)
  gesture.cancel()
})

test('a squeezed press still opens inside its own smaller hub', () => {
  // The lift and the hub shrink together, so the invariant that a press starts neutral is not
  // something a short pane can take away.
  const { gesture, fired, latches, press } = harness({ roomAbovePx: 0 })
  press(0, 0)
  gesture.move(1, 0, 0)
  assert.deepEqual(latches, [])
  assert.deepEqual(fired, [])
  const { bands } = gesture.peek()
  assert.equal(bands.outer, RAIL_PAD_SINGLE_OUTER_PX * RAIL_PAD_MIN_SCALE)
  // And a drag scaled to that dial still reaches its wedge.
  gesture.move(1, 0, -(bands.lift + bands.dead + 4))
  assert.deepEqual(fired, [UP])
  gesture.cancel()
})
